#!/usr/bin/env python3
import sys
sys.dont_write_bytecode = True

import argparse
import asyncio
import getpass
import importlib
import json
import os
import shutil
import subprocess
import socket
import tomllib
import importlib.resources
import platform
import signal
import atexit
import tempfile
import threading
import time
import warnings
import webbrowser
from pathlib import Path
from watchfiles import watch
from .db.migrations import (
    apply_migrations,
    check_migrations,
    create_migration,
    init_migrations,
    migrate,
)
from .deployment import (
    SUPPORTED_PROVIDERS,
    check_deployment,
    deploy_project,
    initialize_deployment,
    load_deployment_config,
)
from .frontend import (
    create_frontend,
    doctor_frontend,
    repair_frontend,
    run_frontend,
    start_frontend,
    storage_frontend,
)
from .project import atomic_write, package_version, target_managed_files, write_project_manifest
from .secrets import (
    SecretStore,
    SecretStoreError,
    copy_secret_to_clipboard,
    environment_with_local_secrets,
    local_secret_environment,
    push_render_secret,
    validate_secret_name,
)
from .upgrade import upgrade_to_version
from types import SimpleNamespace
import re

# Also set the env flag so children inherit it (uvicorn worker, etc.)
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

PROJECT_ROOT = os.path.abspath(os.getcwd())
APP_DIR = os.path.join(PROJECT_ROOT, 'app')
BUILD_DIR = os.path.join(PROJECT_ROOT, 'build')
SETTINGS_PATH = os.path.join(APP_DIR, 'settings.py')

MIN_NODE = "22.15.0"  # keep your RN and webpack minimum here

def _semver_tuple(s: str):
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", s.strip())
    return tuple(map(int, m.groups())) if m else (0, 0, 0)

def _current_node_version():
    try:
        out = subprocess.run(["node", "-v"], text=True, capture_output=True, check=True).stdout
        return _semver_tuple(out)
    except Exception:
        return (0, 0, 0)

def ensure_node_env(min_required: str = MIN_NODE, track_major: str = "22"):
    """
    Guarantee Node >= min_required and prefer the latest track_major.x via nvm.
    Returns an env dict with PATH pointing to the selected node/npm so all
    subprocesses use it.
    """
    cur = _current_node_version()
    required_major = int(track_major)
    if cur[0] == required_major and cur >= _semver_tuple(min_required):
        # Already on the supported Node track.
        return os.environ.copy()

    # Need to upgrade/switch via nvm
    nvm_dir = os.path.expanduser("~/.nvm")
    nvm_sh = os.path.join(nvm_dir, "nvm.sh")
    if not os.path.exists(nvm_sh):
        print("nvm not found; please install nvm (https://github.com/nvm-sh/nvm).")
        print(f"Alternatively, install Node {track_major}.x manually (≥ {min_required}).")
        return os.environ.copy()

    # Ask nvm for latest {track_major}.x and use it (this also covers >= min_required)
    script = f'''
      export NVM_DIR="{nvm_dir}"
      [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
      nvm install {track_major}
      nvm use {track_major}
      echo NODE_BIN:$(command -v node)
      echo NPM_BIN:$(command -v npm)
      node --version
    '''
    res = subprocess.run(["bash", "-lc", script], text=True, capture_output=True)
    if res.returncode != 0:
        print("Failed to switch Node with nvm. Output:\n", res.stdout or res.stderr)
        return os.environ.copy()

    m = re.search(r"NODE_BIN:(.*)", res.stdout or "")
    if not m:
        print("Could not resolve Node path from nvm output; falling back to current PATH.")
        return os.environ.copy()
    node_bin = m.group(1).strip()
    # npm_bin = re.search(r"NPM_BIN:(.*)", res.stdout).group(1).strip()  # not strictly needed

    env = os.environ.copy()
    env["PATH"] = f"{os.path.dirname(node_bin)}:{env.get('PATH','')}"
    return env

def load_settings():
    """Load app/settings.py, defaulting to BACKEND=True if not present or import fails."""
    if not os.path.exists(SETTINGS_PATH):
        return SimpleNamespace(BACKEND=True)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("app_settings", SETTINGS_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not hasattr(mod, 'BACKEND'):
            mod.BACKEND = True
        return mod
    except Exception:
        return SimpleNamespace(BACKEND=True)

settings = load_settings()

_BACKEND_SETTING = re.compile(
    r"^(?P<prefix>[ \t]*BACKEND(?:[ \t]*:[^=\r\n]+)?[ \t]*=[ \t]*)"
    r"(?P<value>True|False)(?P<suffix>[ \t]*(?:#[^\r\n]*)?)(?=\r?$)",
    re.MULTILINE,
)


def set_backend_enabled(enabled: bool):
    """Set the current project's BACKEND setting."""
    if not os.path.isfile(SETTINGS_PATH):
        print(
            "app/settings.py not found. Run this command from an OnRamp "
            "project root."
        )
        return False

    try:
        with open(SETTINGS_PATH, encoding="utf-8", newline="") as settings_file:
            content = settings_file.read()
    except (OSError, UnicodeError) as error:
        print(f"Could not read app/settings.py: {error}")
        return False

    match = _BACKEND_SETTING.search(content)
    if not match:
        print(
            "A top-level BACKEND = True or BACKEND = False setting was not "
            "found in app/settings.py."
        )
        return False

    target_value = "True" if enabled else "False"
    state = "enabled" if enabled else "disabled"
    if match.group("value") == target_value:
        print(f"Backend is already {state} (BACKEND = {target_value}).")
        return True

    updated = (
        content[:match.start("value")]
        + target_value
        + content[match.end("value"):]
    )
    try:
        atomic_write(SETTINGS_PATH, updated)
    except OSError as error:
        print(f"Could not update app/settings.py: {error}")
        return False

    print(f"Backend {state} (BACKEND = {target_value}).")
    return True


def enable_backend():
    """Set the current project's BACKEND setting to True."""
    return set_backend_enabled(True)


def disable_backend():
    """Set the current project's BACKEND setting to False."""
    return set_backend_enabled(False)


def handle_prepmigrations(args):
    """Handle the prepmigrations command"""
    name = args.name if hasattr(args, 'name') and args.name else None
    selected = _select_environment(getattr(args, "environment", None))
    try:
        with local_secret_environment(PROJECT_ROOT, selected):
            success = create_migration(name)
    except SecretStoreError as error:
        print(f"Could not load local secrets: {error}")
        return 1
    if success:
        print("Migration prepared successfully")
    else:
        print("Failed to prepare migration")
        return 1
    return 0

def handle_migrate(args):
    """Handle the migrate command (with auto-prep)"""
    name = args.name if hasattr(args, 'name') and args.name else None
    selected = _select_environment(getattr(args, "environment", None))
    try:
        with local_secret_environment(PROJECT_ROOT, selected):
            success = migrate(name)
    except SecretStoreError as error:
        print(f"Could not load local secrets: {error}")
        return 1
    if success:
        print("Migration completed successfully")
    else:
        print("Migration failed")
        return 1
    return 0


def handle_db(args):
    """Handle explicit development and production migration stages."""
    operation = args.name
    extra = getattr(args, "extra", [])
    selected = _select_environment(getattr(args, "environment", None))
    try:
        with local_secret_environment(PROJECT_ROOT, selected):
            if operation == "make":
                if len(extra) > 1:
                    print("Usage: 'onramp db make [name]'")
                    return 2
                success = create_migration(extra[0] if extra else None)
            elif operation == "upgrade":
                if extra:
                    print("Usage: 'onramp db upgrade'")
                    return 2
                success = apply_migrations()
            elif operation == "check":
                if extra:
                    print("Usage: 'onramp db check'")
                    return 2
                success = check_migrations()
            else:
                print("Usage: 'onramp db <make [name] | upgrade | check>'")
                return 2
    except SecretStoreError as error:
        print(f"Could not load local secrets: {error}")
        return 1
    return 0 if success else 1


def handle_secret(args):
    """Store local backend secrets or explicitly hand one to Render."""
    operation = args.name
    extra = list(getattr(args, "extra", []))
    if operation is None:
        print(
            "Usage: 'onramp secret <NAME> | list | check NAME | "
            "delete NAME | copy NAME | push NAME'"
        )
        return 2

    if operation == "set":
        if len(extra) != 1:
            print("Usage: 'onramp secret set <NAME>'")
            return 2
        action, name = "set", extra[0]
    elif operation in {"check", "delete", "copy", "push"}:
        if len(extra) != 1:
            print(f"Usage: 'onramp secret {operation} <NAME>'")
            return 2
        action, name = operation, extra[0]
    elif operation == "list":
        if extra:
            print("Usage: 'onramp secret list'")
            return 2
        action, name = "list", None
    else:
        action, name = "set", operation
        if extra:
            print(
                "Do not put secret values in command arguments; shell history and "
                "process listings can expose them. Rerun as 'onramp secret NAME'."
            )
            return 2

    if action in {"copy", "push"}:
        config = load_deployment_config(PROJECT_ROOT)
        deployment_environment = str(
            args.environment
            or (config or {}).get("environment")
            or "production"
        ).strip().lower()
        scope = None
    else:
        deployment_environment = None
        scope = args.environment
    scope_label = f"the {scope} override" if scope else "all environments"

    try:
        if name is not None:
            name = validate_secret_name(name)
        store = SecretStore(PROJECT_ROOT)
        if action == "set":
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    value = getpass.getpass(f"Enter {name} for {scope_label}: ")
            except (EOFError, getpass.GetPassWarning):
                print("A secure interactive prompt is required to store a secret.")
                return 1
            store.set(scope, name, value)
            print(f"Stored {name} for {scope_label} in the OS credential store.")
            return 0
        if action == "list":
            names = store.names(scope)
            if names:
                heading = (
                    f"Local secret overrides for {scope}:"
                    if scope
                    else "Shared local secrets:"
                )
                print(heading)
                for stored_name in names:
                    print(f"  {stored_name}")
            else:
                print(f"No local secrets are stored for {scope_label}.")
            return 0
        if action == "check":
            value = store.get(scope, name)
            if value is not None:
                print(f"{name} is stored for {scope_label}.")
                return 0
            if scope and store.get(None, name) is not None:
                print(f"{name} uses the shared value in {scope}.")
                return 0
            print(f"{name} is not stored for {scope_label}.")
            return 1
        if action == "delete":
            if not store.delete(scope, name):
                if scope and store.get(None, name) is not None:
                    print(
                        f"No {scope} override was stored for {name}; "
                        "the shared value remains."
                    )
                else:
                    print(f"{name} was not stored for {scope_label}.")
                return 1
            print(f"Deleted {name} for {scope_label} from the OS credential store.")
            return 0

        value = store.resolve(deployment_environment, name)
        if value is None:
            print(
                f"{name} has no shared value or {deployment_environment} override. "
                f"Store it with 'onramp secret {name}', adding --environment "
                f"{deployment_environment} only if that value should differ."
            )
            return 1
        if action == "copy":
            copy_secret_to_clipboard(value)
            print(
                f"Copied {name} for {deployment_environment} to the clipboard. "
                "Paste it promptly, then replace the clipboard contents; other "
                "apps and clipboard history may be able to read it."
            )
            return 0
        render_api_key = os.environ.get("RENDER_API_KEY")
        if not render_api_key:
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    render_api_key = getpass.getpass("Render API key (not saved): ")
            except (EOFError, getpass.GetPassWarning):
                print("A Render API key is required to push the secret.")
                return 1
        service = push_render_secret(
            PROJECT_ROOT,
            deployment_environment,
            name,
            value,
            render_api_key,
        )
        print(
            f"Updated {name} on Render backend service {service}. "
            "Deploy or restart the service when you are ready to use it."
        )
        return 0
    except SecretStoreError as error:
        print(f"Secret operation failed: {error}")
        return 1


def handle_deploy(args):
    """Prepare, validate, or run a portable production deployment."""
    action = args.name
    extra = getattr(args, "extra", [])
    if getattr(args, "check", False):
        if extra or action not in {None, "check", *SUPPORTED_PROVIDERS}:
            print("Usage: 'onramp deploy [render|container] --check'")
            return 2
        provider = action if action in SUPPORTED_PROVIDERS else None
        arguments = {}
        if args.environment:
            arguments["environment_override"] = args.environment
        return 0 if check_deployment(PROJECT_ROOT, provider, **arguments) else 1
    if action == "init":
        if len(extra) > 1:
            print("Usage: 'onramp deploy init [render|container]'")
            return 2
        provider = extra[0] if extra else "render"
        return 0 if initialize_deployment(PROJECT_ROOT, provider) else 1
    if action == "check":
        if extra:
            print("Usage: 'onramp deploy --check'")
            return 2
        arguments = {}
        if args.environment:
            arguments["environment_override"] = args.environment
        return 0 if check_deployment(PROJECT_ROOT, **arguments) else 1
    if action in SUPPORTED_PROVIDERS:
        if extra:
            print("Usage: 'onramp deploy [render|container]'")
            return 2
        arguments = {}
        if args.environment:
            arguments["environment_override"] = args.environment
        return 0 if deploy_project(PROJECT_ROOT, action, **arguments) else 1
    if action is not None or extra:
        print(
            "Usage: 'onramp deploy [init [render|container] | check | "
            "render | container] [--check]'"
        )
        return 2
    arguments = {}
    if args.environment:
        arguments["environment_override"] = args.environment
    return 0 if deploy_project(PROJECT_ROOT, **arguments) else 1


def run_project_tests(project_root=PROJECT_ROOT):
    """Run every configured backend and frontend verification suite."""
    successful = True
    tests_dir = os.path.join(project_root, "tests")
    if os.path.isdir(tests_dir):
        print("Running backend tests...")
        result = subprocess.run(
            [sys.executable, "-m", "pytest"],
            cwd=project_root,
            check=False,
        )
        successful = result.returncode == 0 and successful

    package_path = os.path.join(project_root, "build", "package.json")
    if os.path.isfile(package_path):
        try:
            with open(package_path, encoding="utf-8") as package_file:
                scripts = dict(json.load(package_file).get("scripts", {}))
        except (OSError, ValueError, TypeError):
            print("Could not read build/package.json.")
            return False
        environment = ensure_node_env()
        for script in ("typecheck", "test", "build:web"):
            if script not in scripts:
                continue
            print(f"Running frontend {script}...")
            result = subprocess.run(
                ["npm", "run", script],
                cwd=os.path.join(project_root, "build"),
                env=environment,
                check=False,
            )
            successful = result.returncode == 0 and successful

    if not os.path.isdir(tests_dir) and not os.path.isfile(package_path):
        print("No backend or frontend tests are configured.")
        return False
    return successful

# -----------------------------------------------------------------------------
# Framework config (from config.toml)
# -----------------------------------------------------------------------------
script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, "config.toml")
with open(config_path, "rb") as f:
    config = tomllib.load(f)
    FRAMEWORK_NAME = config['framework_name']
MODULE_NAME = FRAMEWORK_NAME.lower()

# -----------------------------------------------------------------------------
# Process management
# -----------------------------------------------------------------------------
spawned_processes = []

def _stop_process(process, timeout=3):
    """Terminate and reap one child process."""
    try:
        if process.poll() is not None:
            return
        print(f"Terminating process {process.pid}...")
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        pass


def cleanup_processes():
    """Clean up all spawned processes."""
    global spawned_processes
    for process in spawned_processes:
        _stop_process(process)
    spawned_processes.clear()

def signal_handler(signum, frame):
    print("\nReceived interrupt signal. Cleaning up...")
    raise KeyboardInterrupt

def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(('localhost', port)) == 0

def find_next_available_port(starting_port=8000):
    port = starting_port
    while is_port_in_use(port):
        port += 1
    return port


def _resolve_backend_port(port: int, *, force: bool = False) -> int | None:
    """Resolve any backend-port choice before native children start."""
    if not is_port_in_use(port):
        return port
    print(f"Port {port} is already in use.")
    if not force:
        response = input(
            f"Use next available port (starting from {port + 1})? (y/n): "
        ).strip().lower()
        if response != "y":
            print("User declined to use another port. Exiting.")
            return None
    selected = find_next_available_port(port + 1)
    print(f"Using port {selected} instead.")
    return selected

# -----------------------------------------------------------------------------
# Platform-specific runners
# -----------------------------------------------------------------------------
def _select_environment(environment: str | None = None) -> str:
    selected = str(
        environment or os.environ.get("ONRAMP_ENVIRONMENT", "development")
    ).strip().lower()
    if selected not in {"development", "staging", "production"}:
        raise ValueError(
            "Environment must be development, staging, or production."
        )
    os.environ["ONRAMP_ENVIRONMENT"] = selected
    return selected


def run_web(with_backend=True, port=8000, environment: str | None = None):
    if not os.path.exists(BUILD_DIR):
        print("Build directory not found. Run 'onramp new <name>' first.")
        return False

    selected_environment = _select_environment(environment)
    env = ensure_node_env()
    env["ONRAMP_ENVIRONMENT"] = selected_environment
    if with_backend:
        backend_enabled = getattr(settings, 'BACKEND', True)
        if backend_enabled:
            print("Starting web frontend and backend...")
            web_process = start_frontend(
                "web", BUILD_DIR, env=env, environment=selected_environment
            )
            if not web_process:
                return False
            spawned_processes.append(web_process)
            return run_uvicorn_with_watch(
                port,
                companion_process=web_process,
                open_browser=True,
            )
        else:
            print("Backend disabled. Running web only...")
            return run_frontend(
                "web", BUILD_DIR, env=env, environment=selected_environment
            )
    else:
        print("Running web development server...")
        return run_frontend(
            "web", BUILD_DIR, env=env, environment=selected_environment
        )


def run_ios(
    port: int = 8000,
    metro_port: int | None = None,
    watch_diagnostics: bool = False,
    rebuild: bool = False,
    environment: str | None = None,
    force_emulator_updates: bool = False,
):
    """Run iOS simulator; if BACKEND=True also start the backend dev server."""
    if not os.path.exists(BUILD_DIR):
        print("Build directory not found. Run 'onramp new <name>' first.")
        return False

    selected_environment = _select_environment(environment)
    env = ensure_node_env()
    env["ONRAMP_ENVIRONMENT"] = selected_environment
    project_name = os.path.basename(PROJECT_ROOT)
    backend_enabled = getattr(settings, "BACKEND", True)
    if backend_enabled:
        selected_port = _resolve_backend_port(
            port,
            force=force_emulator_updates,
        )
        if selected_port is None:
            return False
        print("Starting iOS (in background) + backend dev server...")
        ios_process = start_frontend(
            "ios",
            BUILD_DIR,
            app_name=project_name,
            env=env,
            backend_port=selected_port,
            metro_port=metro_port,
            watch_diagnostics=watch_diagnostics,
            rebuild=rebuild,
            environment=selected_environment,
            **({"force_emulator_updates": True} if force_emulator_updates else {}),
        )
        if not ios_process:
            return False
        spawned_processes.append(ios_process)
        return run_uvicorn_with_watch(
            selected_port,
            companion_process=ios_process,
            open_browser=True,
            port_preselected=True,
        )
    else:
        return run_frontend(
            "ios",
            BUILD_DIR,
            app_name=project_name,
            env=env,
            metro_port=metro_port,
            watch_diagnostics=watch_diagnostics,
            rebuild=rebuild,
            environment=selected_environment,
            **({"force_emulator_updates": True} if force_emulator_updates else {}),
        )


def run_android(
    port: int = 8000,
    metro_port: int | None = None,
    watch_diagnostics: bool = False,
    rebuild: bool = False,
    environment: str | None = None,
    force_emulator_updates: bool = False,
):
    if not os.path.exists(BUILD_DIR):
        print("Build directory not found. Run 'onramp new <name>' first.")
        return False

    selected_environment = _select_environment(environment)
    env = ensure_node_env()
    env["ONRAMP_ENVIRONMENT"] = selected_environment
    project_name = os.path.basename(PROJECT_ROOT)
    backend_enabled = getattr(settings, "BACKEND", True)
    if backend_enabled:
        selected_port = _resolve_backend_port(
            port,
            force=force_emulator_updates,
        )
        if selected_port is None:
            return False
        print("Starting Android (in background) + backend dev server...")
        android_process = start_frontend(
            "android",
            BUILD_DIR,
            app_name=project_name,
            env=env,
            backend_port=selected_port,
            metro_port=metro_port,
            watch_diagnostics=watch_diagnostics,
            rebuild=rebuild,
            environment=selected_environment,
            **({"force_emulator_updates": True} if force_emulator_updates else {}),
        )
        if not android_process:
            return False
        spawned_processes.append(android_process)
        return run_uvicorn_with_watch(
            selected_port,
            companion_process=android_process,
            open_browser=True,
            port_preselected=True,
        )

    return run_frontend(
        "android",
        BUILD_DIR,
        app_name=project_name,
        env=env,
        metro_port=metro_port,
        watch_diagnostics=watch_diagnostics,
        rebuild=rebuild,
        environment=selected_environment,
        **({"force_emulator_updates": True} if force_emulator_updates else {}),
    )


def run_mobile(
    port: int = 8000,
    metro_port: int | None = None,
    watch_diagnostics: bool = False,
    rebuild: bool = False,
    environment: str | None = None,
    force_emulator_updates: bool = False,
):
    """Run the iOS and Android apps with one shared backend process."""
    if not os.path.exists(BUILD_DIR):
        print("Build directory not found. Run 'onramp new <name>' first.")
        return False

    selected_environment = _select_environment(environment)
    env = ensure_node_env()
    env["ONRAMP_ENVIRONMENT"] = selected_environment
    project_name = os.path.basename(PROJECT_ROOT)
    backend_enabled = getattr(settings, "BACKEND", True)
    if backend_enabled:
        selected_port = _resolve_backend_port(
            port,
            force=force_emulator_updates,
        )
        if selected_port is None:
            return False
        print("Starting iOS + Android (in background) + backend dev server...")
        mobile_process = start_frontend(
            "mobile",
            BUILD_DIR,
            app_name=project_name,
            env=env,
            backend_port=selected_port,
            metro_port=metro_port,
            watch_diagnostics=watch_diagnostics,
            rebuild=rebuild,
            environment=selected_environment,
            **({"force_emulator_updates": True} if force_emulator_updates else {}),
        )
        if not mobile_process:
            return False
        spawned_processes.append(mobile_process)
        return run_uvicorn_with_watch(
            selected_port,
            companion_process=mobile_process,
            open_browser=True,
            port_preselected=True,
        )

    return run_frontend(
        "mobile",
        BUILD_DIR,
        app_name=project_name,
        env=env,
        metro_port=metro_port,
        watch_diagnostics=watch_diagnostics,
        rebuild=rebuild,
        environment=selected_environment,
        **({"force_emulator_updates": True} if force_emulator_updates else {}),
    )


# -----------------------------------------------------------------------------
# Backend (Uvicorn) helpers
# -----------------------------------------------------------------------------
def _uvicorn_cmd(port: int):
    # -B: disable .pyc writes for the worker
    return [
        sys.executable,
        "-B",
        "-m", "uvicorn", "onramp.app:app",
        "--port", str(port),
    ]


def _production_uvicorn_cmd(port: int | None = None, host: str | None = None):
    """Build the stable production server command used by every host."""
    resolved_host = host or os.environ.get("ONRAMP_HOST") or "0.0.0.0"
    configured_port = os.environ.get("PORT") or os.environ.get("ONRAMP_PORT")
    try:
        resolved_port = int(configured_port) if configured_port else int(port or 8000)
    except ValueError as error:
        raise ValueError("PORT and ONRAMP_PORT must be integers") from error
    forwarded = os.environ.get("ONRAMP_FORWARDED_ALLOW_IPS", "127.0.0.1")
    command = [
        sys.executable,
        "-B",
        "-m",
        "uvicorn",
        "onramp.app:app",
        "--host",
        resolved_host,
        "--port",
        str(resolved_port),
        "--lifespan",
        "on",
        "--proxy-headers",
        "--forwarded-allow-ips",
        forwarded,
    ]
    workers = os.environ.get("ONRAMP_WORKERS", "").strip()
    if workers:
        try:
            if int(workers) < 1:
                raise ValueError
        except ValueError as error:
            raise ValueError("ONRAMP_WORKERS must be a positive integer") from error
        command.extend(["--workers", workers])
    return command


def start_production_server(port: int | None = None, host: str | None = None):
    """Replace the CLI process with Uvicorn so platform signals are graceful."""
    environment = os.environ.copy()
    environment.setdefault("ONRAMP_ENVIRONMENT", "production")
    command = _production_uvicorn_cmd(port=port, host=host)
    print(
        f"Starting production server on {command[command.index('--host') + 1]}:"
        f"{command[command.index('--port') + 1]}"
    )
    os.execvpe(command[0], command, environment)

def _start_uvicorn_worker(app_dir: str, port: int):
    """Start and track a worker owned by the OnRamp parent process."""
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env = environment_with_local_secrets(
        PROJECT_ROOT,
        env.get("ONRAMP_ENVIRONMENT", "development"),
        env,
    )
    popen_options = {}
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        # Keep terminal Ctrl+C with the wrapper. It will explicitly stop this
        # worker from its guaranteed cleanup path.
        popen_options["start_new_session"] = True
    p = subprocess.Popen(
        _uvicorn_cmd(port),
        env=env,
        cwd=app_dir,
        **popen_options,
    )
    spawned_processes.append(p)
    return p

def _backend_source_filter(_change, file_path):
    """Restart the backend only for Python source changes."""
    path = str(file_path)
    if any(part in path for part in (
        '__pycache__',
        '.pyc',
        '.pyo',
    )):
        return False
    return path.endswith('.py')


def _api_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/api"


def _open_api_url(port: int):
    """Open the default API route in the system browser."""
    url = _api_url(port)
    print(f"Opening API in browser: {url}")
    try:
        if webbrowser.open(url, new=2):
            return True
    except (OSError, webbrowser.Error) as error:
        print(f"Could not open the browser: {error}")
        return False
    print(f"Could not open the browser automatically. API: {url}")
    return False


def _open_api_when_ready(port: int, process, timeout: float = 30):
    """Wait for Uvicorn to listen, then open its API without blocking launch."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                _open_api_url(port)
                return
        except OSError:
            time.sleep(0.05)

    print(f"API did not become ready for browser launch: {_api_url(port)}")


def _schedule_api_browser(port: int, process):
    browser_thread = threading.Thread(
        target=_open_api_when_ready,
        args=(port, process),
        daemon=True,
        name="onramp-api-browser",
    )
    browser_thread.start()
    return browser_thread


def _finished_frontend_result(companion_process):
    if companion_process is None:
        return None
    status = companion_process.poll()
    if status is None:
        return None
    if status == 0:
        print("Frontend command stopped; stopping backend.")
        return True
    print(
        f"Frontend command failed with status {status}; stopping backend."
    )
    return False


def run_uvicorn_with_watch(
    port=8000,
    companion_process=None,
    open_browser=False,
    port_preselected=False,
):
    """Watch app/ for changes and restart uvicorn worker (no parent reloader)."""
    proc = None
    successful = True

    try:
        if not port_preselected:
            selected_port = _resolve_backend_port(port)
            if selected_port is None:
                return False
            port = selected_port

        frontend_result = _finished_frontend_result(companion_process)
        if frontend_result is not None:
            return frontend_result

        print(f"Dev watch active on {APP_DIR}.")
        proc = _start_uvicorn_worker(APP_DIR, port)
        if open_browser:
            _schedule_api_browser(port, proc)

        for changes in watch(
            APP_DIR,
            watch_filter=_backend_source_filter,
            rust_timeout=500 if companion_process is not None else 5000,
            yield_on_timeout=companion_process is not None,
        ):
            frontend_result = _finished_frontend_result(companion_process)
            if frontend_result is not None:
                successful = frontend_result
                break
            if not changes:
                continue
            print(f"Changes detected: {list(changes)}")
            print("Restarting server...")
            try:
                if proc:
                    _stop_process(proc)
            except Exception as e:
                print(f"Error stopping previous worker: {e}")

            proc = _start_uvicorn_worker(APP_DIR, port)

    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"Watcher error: {e}")
        successful = False
    finally:
        if proc:
            _stop_process(proc)
        cleanup_processes()
    return successful

def run_command_logic(port=8000, environment: str | None = None):
    if not os.path.exists(BUILD_DIR):
        print("No build directory found. Running backend only")
        _select_environment(environment)
        return run_uvicorn_with_watch(port)

    try:
        backend_enabled = getattr(settings, 'BACKEND', True)
        return run_web(
            with_backend=backend_enabled, port=port, environment=environment
        )
    except Exception as e:
        print(f"Error checking settings: {e}. Running backend only")
        _select_environment(environment)
        return run_uvicorn_with_watch(port)

# -----------------------------------------------------------------------------
# Project scaffolding
# -----------------------------------------------------------------------------

def write_netlify_toml(project_root: str):
    netlify_path = os.path.join(project_root, "netlify.toml")
    if os.path.exists(netlify_path):
        # don’t overwrite if user already has one
        print("netlify.toml already exists, leaving it untouched.")
        return

    content = """[build]
base = "build"
command = "npm ci && npm run build:web"
publish = "dist"

[build.environment]
NODE_VERSION = "22.15.0"

[[redirects]]
from = "/*"
to = "/index.html"
status = 200
"""
    with open(netlify_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("✓ netlify.toml created")


def _project_distribution_name(name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-._")
    return normalized.lower() or "onramp-app"


def _write_project_template(
    template_name: str,
    destination: str,
    replacements: dict[str, str] | None = None,
):
    templates_module = importlib.import_module(f"{MODULE_NAME}.templates")
    content = (
        importlib.resources.files(templates_module) / template_name
    ).read_text(encoding="utf-8")
    for source, replacement in (replacements or {}).items():
        content = content.replace(source, replacement)
    with open(destination, "w", encoding="utf-8") as project_file:
        project_file.write(content)


def write_project_files(project_root: str, name: str, api_only: bool = False):
    replacements = {
        "__ONRAMP_APP_NAME__": name,
        "__ONRAMP_PROJECT_NAME__": _project_distribution_name(name),
        "__ONRAMP_PROJECT_KIND__": "API-only" if api_only else "full-stack",
        "__ONRAMP_VERSION__": package_version(),
    }
    _write_project_template(
        "project_README.md",
        os.path.join(project_root, "README.md"),
        replacements,
    )
    _write_project_template(
        "project_gitignore",
        os.path.join(project_root, ".gitignore"),
    )
    _write_project_template(
        "AGENTS.md",
        os.path.join(project_root, "AGENTS.md"),
        replacements,
    )
    for relative_path, content in target_managed_files(project_root, name).items():
        atomic_write(Path(project_root) / relative_path, content)
    _write_project_template(
        "pyproject.toml",
        os.path.join(project_root, "pyproject.toml"),
        replacements,
    )


def create_app_directory(name, api_only=False, directory_path=None):
    """Create a new application directory using templates."""
    directory_path = directory_path or os.path.join(PROJECT_ROOT, name)
    if os.path.exists(directory_path):
        if not os.path.isdir(directory_path):
            print(f"Cannot create app: target is not a directory: {directory_path}")
            return False
        if os.listdir(directory_path):
            print(f"Cannot create app: target directory is not empty: {directory_path}")
            return False

    try:
        print(f"Creating {FRAMEWORK_NAME} {'API' if api_only else 'backend'}...")

        os.makedirs(directory_path, exist_ok=True)
        TEMPLATES_MODULE = importlib.import_module(f"{MODULE_NAME}.templates")
        write_project_files(directory_path, name, api_only=api_only)

        backend_dir = os.path.join(directory_path, 'app')
        os.makedirs(backend_dir, exist_ok=True)

        if not api_only:
            write_netlify_toml(directory_path)

        # Make app a proper package
        with open(os.path.join(backend_dir, '__init__.py'), 'w') as f:
            f.write("# OnRamp App Package\n")

        shutil.copyfile(importlib.resources.files(TEMPLATES_MODULE) / 'settings.py',
                        os.path.join(backend_dir, 'settings.py'))
        settings_path = os.path.join(backend_dir, 'settings.py')
        with open(settings_path, encoding='utf-8') as settings_file:
            settings_content = settings_file.read()
        with open(settings_path, 'w', encoding='utf-8') as settings_file:
            settings_file.write(
                settings_content.replace('__ONRAMP_APP_NAME__', name)
            )

        models_dir = os.path.join(backend_dir, 'models')
        os.makedirs(models_dir, exist_ok=True)
        shutil.copyfile(importlib.resources.files(TEMPLATES_MODULE) / 'models.py',
                        os.path.join(models_dir, 'models.py'))
        with open(os.path.join(models_dir, '__init__.py'), 'w') as f:
            f.write("# Models package\n")

        db_dir = os.path.join(backend_dir, 'db')
        os.makedirs(db_dir, exist_ok=True)
        with open(os.path.join(db_dir, '__init__.py'), 'w') as f:
            f.write("# Database package\n")
        shutil.copyfile(importlib.resources.files(TEMPLATES_MODULE) / 'db_config.py',
                        os.path.join(db_dir, 'db_config.py'))

        if not api_only:
            routes_dir = os.path.join(backend_dir, 'routes')
            os.makedirs(routes_dir, exist_ok=True)
            app_static_dir = os.path.join(backend_dir, 'static')
            os.makedirs(app_static_dir, exist_ok=True)
            lib_static_dir = importlib.import_module(f"{MODULE_NAME}.static")
            shutil.copyfile(importlib.resources.files(lib_static_dir) / 'logo.png',
                            os.path.join(app_static_dir, 'logo.png'))

        api_dir = os.path.join(backend_dir, 'api')
        os.makedirs(api_dir, exist_ok=True)
        with open(os.path.join(api_dir, '__init__.py'), 'w') as f:
            f.write("# API package\n")
        shutil.copyfile(importlib.resources.files(TEMPLATES_MODULE) / 'index.py',
                        os.path.join(api_dir, 'index.py'))

        tests_dir = os.path.join(directory_path, 'tests')
        os.makedirs(tests_dir, exist_ok=True)
        shutil.copyfile(
            importlib.resources.files(TEMPLATES_MODULE) / 'test_api.py',
            os.path.join(tests_dir, 'test_api.py'),
        )

        print(f"{FRAMEWORK_NAME} {'API' if api_only else 'backend'} created")

        # Initialize database migrations as part of app setup
        print("Setting up database migrations...")
        original_cwd = os.getcwd()
        try:
            os.chdir(directory_path)
            success = init_migrations(backend_dir)
            if success:
                print("Database migration system ready")
            else:
                raise RuntimeError("Database migration setup did not complete")
        except Exception as error:
            print(f"Database migration setup failed: {error}")
            return False
        finally:
            os.chdir(original_cwd)

        return True

    except Exception as e:
        print(f"An error occurred while creating the directory: {e}")
        return False


def create_new_project(
    name: str,
    api_only: bool = False,
    platform: str = "web",
) -> bool:
    """Create a complete project in staging and publish it atomically."""
    if (
        not name
        or name in {".", ".."}
        or os.sep in name
        or (os.altsep and os.altsep in name)
    ):
        print("App name must be a single directory name.")
        return False

    target = os.path.join(PROJECT_ROOT, name)
    if os.path.exists(target):
        if not os.path.isdir(target):
            print(f"Cannot create app: target is not a directory: {target}")
            return False
        target_entries = os.listdir(target)
        if target_entries and set(target_entries) != {".git"}:
            print(f"Cannot create app: target directory is not empty: {target}")
            return False

    staging = tempfile.mkdtemp(
        prefix=f".{_project_distribution_name(name)}-onramp-",
        dir=PROJECT_ROOT,
    )
    try:
        if not create_app_directory(
            name,
            api_only=api_only,
            directory_path=staging,
        ):
            raise RuntimeError("Backend scaffolding failed")

        if not api_only:
            frontend_dir = os.path.join(staging, "build")
            frontend_env = ensure_node_env()
            frontend_env["ONRAMP_PROJECT_ROOT"] = target
            if not create_frontend(
                name,
                frontend_dir,
                env=frontend_env,
                platform=platform,
            ):
                raise RuntimeError("Frontend scaffolding failed")

        write_project_manifest(staging, project_name=name)

        if os.path.isdir(target):
            target_git = os.path.join(target, ".git")
            staged_git = os.path.join(staging, ".git")
            preserves_git = os.path.lexists(target_git)
            if preserves_git:
                os.replace(target_git, staged_git)
            try:
                os.rmdir(target)
                os.replace(staging, target)
            except Exception:
                if preserves_git and os.path.lexists(staged_git):
                    os.makedirs(target, exist_ok=True)
                    os.replace(staged_git, target_git)
                raise
        else:
            os.replace(staging, target)
        print(f"✓ {FRAMEWORK_NAME} project created at {target}")
        return True
    except Exception as error:
        print(f"Project creation failed: {error}")
        return False
    finally:
        if os.path.exists(staging):
            shutil.rmtree(staging, ignore_errors=True)

def repair_ios(build_dir=BUILD_DIR, fresh=False):
    return repair_frontend(
        "ios",
        build_dir,
        app_name=os.path.basename(PROJECT_ROOT),
        env=ensure_node_env(),
        fresh=fresh,
    )

# Unclear why these folders are being created - I should find a more elegant fix later
def _clean_empty_shadow_dirs(root):
    for d in ("app2", "build2"):
        p = os.path.join(root, d)
        if os.path.isdir(p) and not os.listdir(p):
            shutil.rmtree(p, ignore_errors=True)
            print(f"Removed empty shadow dir: {d}")

def handle_del(args):
    """Delete a direct child directory of the current working directory, without prompts."""
    name = (args.name or "").strip()
    if not name:
        print("Usage: onramp del <dirname>")
        return 1

    # Safety: only simple folder names (no slashes) to avoid arbitrary paths
    if os.sep in name or (os.altsep and os.altsep in name):
        print("Refusing: provide just a folder name (no slashes).")
        return 1

    target = os.path.abspath(os.path.join(PROJECT_ROOT, name))

    # Must exist and be a directory
    if not os.path.exists(target):
        print(f"No such file or directory: {name}")
        return 1
    if not os.path.isdir(target):
        print(f"Refusing: {name} is not a directory.")
        return 1

    # Must be a direct child of the cwd (avoid deleting siblings elsewhere)
    if os.path.dirname(target) != PROJECT_ROOT:
        print("Refusing: target must be a direct child of the current directory.")
        return 1

    # Never delete if the current process is inside that directory
    cwd = os.path.abspath(os.getcwd())
    if cwd == target or cwd.startswith(target + os.sep):
        print("Refusing: current working directory is inside the target.")
        return 1

    # Extra guardrails
    protected = {"/", os.path.expanduser("~")}
    if target in protected:
        print("Refusing: protected path.")
        return 1

    try:
        if platform.system() == "Windows":
            # Windows fallback: Python rmtree (best-effort permission handling)
            import stat
            def _onerror(func, path, exc_info):
                try:
                    os.chmod(path, stat.S_IWRITE)
                except Exception:
                    pass
                func(path)
            shutil.rmtree(target, onerror=_onerror)
        else:
            # macOS/Linux: use rm -rf semantics explicitly
            res = subprocess.run(["rm", "-rf", "--", target])
            if res.returncode != 0:
                print(f"Failed to delete {name} (rm exit {res.returncode})")
                return res.returncode
        print(f"✓ Deleted {name}")
        return 0
    except Exception as error:
        print(f"Delete failed: {error}")
        return 1


def handle_account(args):
    """Manage framework account classifications without requiring an admin UI."""
    classify_operation = args.name == "classify" and len(args.extra) == 2
    role_operation = args.name == "role" and len(args.extra) == 3
    if not classify_operation and not role_operation:
        print(
            "Usage: 'onramp account classify <email> "
            "<regular|internal|tester>' or "
            "'onramp account role <email> <add|remove> <role>'"
        )
        return 2

    async def classify():
        from tortoise import Tortoise

        from onramp.auth.config import auth_enabled
        from onramp.auth.service import classify_email, update_account_role
        from onramp.db.manager import get_db_manager

        manager = get_db_manager(APP_DIR)
        if not auth_enabled(APP_DIR):
            print("OnRamp accounts are not enabled in app/settings.py.")
            return False
        await Tortoise.init(config=manager.get_tortoise_config())
        try:
            if classify_operation:
                email, audience_type = args.extra
                normalized = await classify_email(email, audience_type)
                print(f"{normalized} is classified as {audience_type}.")
            else:
                email, action, role = args.extra
                if action not in {"add", "remove"}:
                    raise ValueError("Role action must be add or remove.")
                normalized, roles = await update_account_role(
                    email, role, enabled=action == "add"
                )
                print(
                    f"{normalized} roles: "
                    + (", ".join(roles) if roles else "(none)")
                )
            return True
        finally:
            await Tortoise.close_connections()

    selected = _select_environment(getattr(args, "environment", None))
    try:
        with local_secret_environment(PROJECT_ROOT, selected):
            return 0 if asyncio.run(classify()) else 1
    except SecretStoreError as error:
        print(f"Could not load local secrets: {error}")
        return 1
    except (ValueError, RuntimeError) as error:
        print(f"Could not classify account: {error}")
        return 1


def handle_email(args):
    from onramp.db.manager import get_db_manager
    from onramp.email_commands import run_email_command

    # Match backend startup's settings/environment precedence, without opening
    # the database or booting the application.
    try:
        selected = args.environment or get_db_manager(APP_DIR).environment()
        if selected not in {"development", "test", "staging", "production"}:
            print("Choose a valid ONRAMP_ENVIRONMENT for the email check.")
            return 2
        os.environ["ONRAMP_ENVIRONMENT"] = selected
    except Exception:
        print("Could not load app/settings.py for the email check.")
        return 1
    try:
        with local_secret_environment(PROJECT_ROOT, selected):
            return run_email_command(args, APP_DIR)
    except SecretStoreError as error:
        print(f"Could not load local secrets: {error}")
        return 1


def handle_notifications(args):
    """Report, clean, or dispatch framework notification subscriptions."""
    operation = args.name
    extra = getattr(args, "extra", [])
    if operation not in {"report", "cleanup", "anonymize", "dispatch"}:
        print(
            "Usage: 'onramp notifications "
            "<report|cleanup|anonymize EMAIL|dispatch EVENT_KEY>'"
        )
        return 2

    async def operate():
        from tortoise import Tortoise

        from onramp.auth.config import auth_enabled
        from onramp.db.manager import get_db_manager
        from onramp.notifications.service import (
            anonymize_notification_contact,
            cleanup_notification_data,
            dispatch_subscriptions,
            notification_report,
        )

        manager = get_db_manager(APP_DIR)
        if not auth_enabled(APP_DIR):
            print("OnRamp notifications are not enabled in app/settings.py.")
            return False
        await Tortoise.init(config=manager.get_tortoise_config())
        try:
            filters = {
                "resource_type": args.resource_type,
                "source": args.source,
                "resource_ids": args.resource_ids,
                "canonical_resource_id": args.canonical_resource_id,
                "environment": args.subscription_environment or manager.environment(),
                "unnotified_only": args.unnotified_only,
            }
            if operation == "report":
                if extra:
                    print("Usage: 'onramp notifications report [filters]'")
                    return False
                result = await notification_report(**filters)
            elif operation == "cleanup":
                if extra:
                    print("Usage: 'onramp notifications cleanup [--unverified-days DAYS]'")
                    return False
                result = await cleanup_notification_data(
                    unverified_days=args.unverified_days,
                    app_dir=APP_DIR,
                )
            elif operation == "anonymize":
                if len(extra) != 1:
                    print("Usage: 'onramp notifications anonymize <email>'")
                    return False
                result = await anonymize_notification_contact(extra[0])
            else:
                if len(extra) != 1 or not args.subject:
                    print(
                        "Usage: 'onramp notifications dispatch EVENT_KEY --subject "
                        'SUBJECT (--text TEXT | --text-file PATH) [filters]'
                    )
                    return False
                if bool(args.text) == bool(args.text_file):
                    print("Choose exactly one of --text or --text-file.")
                    return False
                if args.send and args.dry_run:
                    print("Choose --send or --dry-run, not both.")
                    return False
                explicitly_scoped = any(
                    (
                        args.resource_type,
                        args.source,
                        args.resource_ids,
                        args.canonical_resource_id,
                    )
                )
                if not args.all_subscriptions and not explicitly_scoped:
                    print(
                        "Refusing an unscoped dispatch. Add a resource filter or "
                        "--all-subscriptions."
                    )
                    return False
                try:
                    text_body = (
                        args.text
                        if args.text is not None
                        else Path(args.text_file).read_text(encoding="utf-8")
                    )
                    html_body = (
                        Path(args.html_file).read_text(encoding="utf-8")
                        if args.html_file
                        else None
                    )
                except (OSError, UnicodeError) as error:
                    print(f"Could not read notification content: {error}")
                    return False
                report = await dispatch_subscriptions(
                    extra[0],
                    subject=args.subject,
                    text_body=text_body,
                    html_body=html_body,
                    retry_failed=args.retry_failed,
                    dry_run=not args.send,
                    app_dir=APP_DIR,
                    **filters,
                )
                result = report.as_dict()
            print(json.dumps(result, indent=2, sort_keys=True))
            return not (
                operation == "dispatch"
                and args.send
                and bool(result.get("failed") or result.get("busy"))
            )
        finally:
            await Tortoise.close_connections()

    selected = _select_environment(getattr(args, "environment", None))
    try:
        with local_secret_environment(PROJECT_ROOT, selected):
            return 0 if asyncio.run(operate()) else 1
    except SecretStoreError as error:
        print(f"Could not load local secrets: {error}")
        return 1
    except (ValueError, RuntimeError) as error:
        print(f"Notification operation failed: {error}")
        return 1


# -----------------------------------------------------------------------------
# CLI entrypoint
# -----------------------------------------------------------------------------
def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, signal_handler)
    atexit.register(cleanup_processes)

    original_cwd = os.getcwd()
    try:
        parser = argparse.ArgumentParser(
            description=f"{FRAMEWORK_NAME} App Generator and Runner",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog=f"""Commands:
  {FRAMEWORK_NAME.lower()} new <name> [--api | --mobile | --all]
  {FRAMEWORK_NAME.lower()} backend [off]
  {FRAMEWORK_NAME.lower()} run [--port 8000]
  {FRAMEWORK_NAME.lower()} start [--host 0.0.0.0] [--port 8000]
  {FRAMEWORK_NAME.lower()} web
  {FRAMEWORK_NAME.lower()} ios [--port 8000] [--metro-port 8081] [--watch-diagnostics] [--rebuild] [--force]
  {FRAMEWORK_NAME.lower()} android [--port 8000] [--metro-port 8081] [--watch-diagnostics] [--rebuild] [--force]
  {FRAMEWORK_NAME.lower()} mobile [--port 8000] [--metro-port 8081] [--watch-diagnostics] [--rebuild] [--force]
  {FRAMEWORK_NAME.lower()} doctor [web|ios|android|mobile|all]
  {FRAMEWORK_NAME.lower()} storage [--check | --clean] [--include-other-projects]
  {FRAMEWORK_NAME.lower()} repair:ios [--fresh]
  {FRAMEWORK_NAME.lower()} upgrade [--check] [--to VERSION]
  {FRAMEWORK_NAME.lower()} prepmigrations [name]
  {FRAMEWORK_NAME.lower()} migrate [name]
  {FRAMEWORK_NAME.lower()} db make [name]
  {FRAMEWORK_NAME.lower()} db upgrade
  {FRAMEWORK_NAME.lower()} db check
  {FRAMEWORK_NAME.lower()} account classify <email> <regular|internal|tester>
  {FRAMEWORK_NAME.lower()} account role <email> <add|remove> <role>
  {FRAMEWORK_NAME.lower()} email --check
  {FRAMEWORK_NAME.lower()} email test <email> [--send [--confirm-production]]
  {FRAMEWORK_NAME.lower()} notifications report
  {FRAMEWORK_NAME.lower()} notifications cleanup [--unverified-days DAYS]
  {FRAMEWORK_NAME.lower()} notifications anonymize <email>
  {FRAMEWORK_NAME.lower()} notifications dispatch <event-key> --subject SUBJECT (--text TEXT | --text-file PATH) [--send]
  {FRAMEWORK_NAME.lower()} secret <NAME>
  {FRAMEWORK_NAME.lower()} secret list
  {FRAMEWORK_NAME.lower()} secret check <NAME>
  {FRAMEWORK_NAME.lower()} secret delete <NAME>
  {FRAMEWORK_NAME.lower()} secret copy <NAME> [--environment staging|production]
  {FRAMEWORK_NAME.lower()} secret push <NAME> [--environment staging|production]
  {FRAMEWORK_NAME.lower()} deploy init [render|container]
  {FRAMEWORK_NAME.lower()} deploy --check
  {FRAMEWORK_NAME.lower()} deploy [render|container]
  {FRAMEWORK_NAME.lower()} test
  {FRAMEWORK_NAME.lower()} del <directory>

The --port option controls the Python backend. --metro-port controls the
React Native bundler. --watch-diagnostics prints source paths that trigger
Fast Refresh. --rebuild forces native apps to rebuild and reinstall.
For ios, android, and mobile, --force selects the next available backend port
when the requested port is occupied and accepts available emulator updates
without prompting.
For mobile only, it also deletes verified obsolete simulator runtimes. That
includes eligible devices and their saved app data and unreferenced old images.
Active/current environments are kept. First-time installations and repairs still ask.
Xcode and Rosetta setup always require separate software-license consent.
Use --environment development, staging, or production to select one shared
backend, web, and native runtime profile.
Secret values are entered through a hidden prompt. Never place a secret value
directly in the command, where shell history and process listings can expose it.
repair:ios preserves Podfile.lock unless --fresh is set.
upgrade creates recoverable backups and never overwrites modified managed files.
""",
        )
        parser.add_argument(
            "-v",
            "--version",
            action="version",
            version=f"%(prog)s {package_version()}",
            help="Show the installed OnRamp version and exit",
        )
        parser.add_argument("command", help="The command to run")
        parser.add_argument("name", nargs='?', help="The name of the app directory/migration to be created")
        parser.add_argument("extra", nargs='*', help=argparse.SUPPRESS)
        parser.add_argument("--port", type=int, default=8000, help="Port for the development server")
        parser.add_argument(
            "--host",
            default=None,
            help="Host interface for the production server",
        )
        parser.add_argument(
            "--environment",
            choices=["development", "staging", "production"],
            default=None,
            help="Select the shared app and backend runtime environment",
        )
        parser.add_argument(
            "--metro-port",
            type=int,
            default=None,
            help="Preferred Metro port for iOS, Android, or mobile",
        )
        parser.add_argument(
            "--watch-diagnostics",
            action="store_true",
            help="Log source paths that can trigger native Fast Refresh",
        )
        parser.add_argument(
            "--rebuild",
            action="store_true",
            help="Force native apps to rebuild and reinstall",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="When needed, use the next available backend port and accept native emulator updates; mobile also deletes verified obsolete simulator files and data",
        )
        parser.add_argument(
            "--fresh",
            action="store_true",
            help="Allow repair:ios to recreate Podfile.lock",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Run a read-only upgrade, deployment, email, or storage preflight",
        )
        parser.add_argument("--clean", action="store_true", help="Remove eligible disposable development output (storage only)")
        parser.add_argument("--include-other-projects", action="store_true", help="Include old Xcode output for other deleted projects (storage only)")
        parser.add_argument(
            "--to",
            dest="target_version",
            help="Upgrade to a specific OnRamp version",
        )
        parser.add_argument(
            "--internal-upgrade",
            action="store_true",
            help=argparse.SUPPRESS,
        )
        parser.add_argument("--resource-type", default=None)
        parser.add_argument("--source", default=None)
        parser.add_argument(
            "--resource-id",
            dest="resource_ids",
            action="append",
            default=None,
        )
        parser.add_argument("--canonical-resource-id", default=None)
        parser.add_argument(
            "--subscription-environment",
            choices=["development", "test", "staging", "production"],
            default=None,
        )
        parser.add_argument("--subject", default=None)
        parser.add_argument("--text", default=None)
        parser.add_argument("--text-file", default=None)
        parser.add_argument("--html-file", default=None)
        parser.add_argument("--unverified-days", type=int, default=None)
        parser.add_argument("--retry-failed", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--send", action="store_true")
        parser.add_argument("--confirm-production", action="store_true")
        parser.add_argument("--all-subscriptions", action="store_true")
        parser.add_argument("--unnotified-only", action="store_true")
        project_type = parser.add_mutually_exclusive_group()
        project_type.add_argument(
            "--api",
            action="store_true",
            help="Create API-only app without a frontend",
        )
        project_type.add_argument(
            "-m",
            "--mobile",
            action="store_true",
            help="Create the web app and include iOS and Android projects",
        )
        project_type.add_argument(
            "-a",
            "--all",
            dest="all_platforms",
            action="store_true",
            help="Create every supported frontend platform",
        )
        parser.add_argument("--web-only", action="store_true", help="Run web without backend")
        args = parser.parse_args()

        if args.force and args.command not in {"ios", "android", "mobile"}:
            parser.error("--force is only supported for ios, android, and mobile")
        if (args.clean or args.include_other_projects) and args.command != "storage":
            parser.error("--clean and --include-other-projects are only supported for storage")
        if args.command == "storage" and (args.name or args.extra or (args.check and args.clean)):
            parser.error("Use onramp storage [--check | --clean] [--include-other-projects]")

        # Read-only service/storage diagnostics must not repair project files.
        if args.command not in {"email", "secret", "storage"}:
            _clean_empty_shadow_dirs(PROJECT_ROOT)

        if args.command == "new":
            if args.name:
                platform_selection = (
                    "all" if args.all_platforms
                    else "mobile" if args.mobile
                    else "web"
                )
                return 0 if create_new_project(
                    args.name,
                    api_only=args.api,
                    platform=platform_selection,
                ) else 1
            else:
                print(f"Please provide a name for the new app. Usage: '{FRAMEWORK_NAME.lower()} new <name>'")
                return 2

        elif args.command == "backend":
            if args.name is None:
                return 0 if enable_backend() else 1
            if args.name == "off":
                return 0 if disable_backend() else 1
            print(
                f"Invalid backend option: {args.name}. Usage: "
                f"'{FRAMEWORK_NAME.lower()} backend [off]'"
            )
            return 2

        elif args.command == "run":
            if args.web_only:
                return 0 if run_web(
                    with_backend=False,
                    port=args.port,
                    environment=args.environment,
                ) else 1
            else:
                return 0 if run_command_logic(
                    port=args.port, environment=args.environment
                ) else 1

        elif args.command == "start":
            if args.name is not None or args.extra:
                print("Usage: 'onramp start [--host HOST] [--port PORT]'")
                return 2
            start_production_server(port=args.port, host=args.host)
            return 0

        elif args.command == "ios":
            run_arguments = {
                "metro_port": args.metro_port,
                "watch_diagnostics": args.watch_diagnostics,
                "rebuild": args.rebuild,
            }
            if args.environment:
                run_arguments["environment"] = args.environment
            if args.force:
                run_arguments["force_emulator_updates"] = True
            return 0 if run_ios(args.port, **run_arguments) else 1

        elif args.command == "android":
            run_arguments = {
                "metro_port": args.metro_port,
                "watch_diagnostics": args.watch_diagnostics,
                "rebuild": args.rebuild,
            }
            if args.environment:
                run_arguments["environment"] = args.environment
            if args.force:
                run_arguments["force_emulator_updates"] = True
            return 0 if run_android(args.port, **run_arguments) else 1

        elif args.command == "mobile":
            run_arguments = {
                "metro_port": args.metro_port,
                "watch_diagnostics": args.watch_diagnostics,
                "rebuild": args.rebuild,
            }
            if args.environment:
                run_arguments["environment"] = args.environment
            if args.force:
                run_arguments["force_emulator_updates"] = True
            return 0 if run_mobile(args.port, **run_arguments) else 1

        elif args.command == "web":
            return 0 if run_web(
                with_backend=False, environment=args.environment
            ) else 1

        elif args.command == "storage":
            return 0 if storage_frontend(
                clean=args.clean,
                include_other_projects=args.include_other_projects,
                cwd=original_cwd,
                env=ensure_node_env(),
            ) else 1

        elif args.command == "doctor":
            platform_name = args.name or "all"
            return 0 if doctor_frontend(
                platform_name,
                cwd=BUILD_DIR if os.path.isdir(BUILD_DIR) else PROJECT_ROOT,
                env=ensure_node_env(),
            ) else 1

        elif args.command == "prepmigrations":
            return handle_prepmigrations(args)

        elif args.command == "migrate":
            return handle_migrate(args)

        elif args.command == "db":
            return handle_db(args)

        elif args.command == "secret":
            return handle_secret(args)

        elif args.command == "deploy":
            return handle_deploy(args)

        elif args.command == "account":
            return handle_account(args)

        elif args.command == "email":
            return handle_email(args)

        elif args.command == "notifications":
            return handle_notifications(args)

        elif args.command == "test":
            if args.name is not None or args.extra:
                print("Usage: 'onramp test'")
                return 2
            return 0 if run_project_tests() else 1
        
        elif args.command == "repair:ios":
            return 0 if repair_ios(fresh=args.fresh) else 1

        elif args.command == "upgrade":
            frontend_env = ensure_node_env() if os.path.isdir(BUILD_DIR) else None
            return 0 if upgrade_to_version(
                PROJECT_ROOT,
                requested_version=args.target_version,
                check=args.check,
                internal=args.internal_upgrade,
                frontend_env=frontend_env,
            ) else 1

        elif args.command == "del":
            return handle_del(args)

        else:
            parser.print_help()
            print(f"\nInvalid command: {args.command}")
            return 2

    except KeyboardInterrupt:
        print("\nInterrupted by user")
        cleanup_processes()
        return 130
    finally:
        try:
            os.chdir(original_cwd)
        except (FileNotFoundError, OSError):
            try:
                os.chdir(os.path.dirname(original_cwd))
            except (FileNotFoundError, OSError):
                os.chdir(os.path.expanduser("~"))

if __name__ == "__main__":
    raise SystemExit(main())
