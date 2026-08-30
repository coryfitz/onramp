#!/usr/bin/env python3
import sys
sys.dont_write_bytecode = True

import argparse
import asyncio
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
import webbrowser
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
)
from .frontend import (
    create_frontend,
    doctor_frontend,
    repair_frontend,
    run_frontend,
    start_frontend,
)
from .project import atomic_write, package_version, write_project_manifest
from .upgrade import upgrade_to_version
from types import SimpleNamespace
import re

# Also set the env flag so children inherit it (uvicorn worker, etc.)
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

PROJECT_ROOT = os.path.abspath(os.getcwd())
APP_DIR = os.path.join(PROJECT_ROOT, 'app')
BUILD_DIR = os.path.join(PROJECT_ROOT, 'build')
SETTINGS_PATH = os.path.join(APP_DIR, 'settings.py')

MIN_NODE = "20.19.4"  # keep your RN minimum here

def _semver_tuple(s: str):
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", s.strip())
    return tuple(map(int, m.groups())) if m else (0, 0, 0)

def _current_node_version():
    try:
        out = subprocess.run(["node", "-v"], text=True, capture_output=True, check=True).stdout
        return _semver_tuple(out)
    except Exception:
        return (0, 0, 0)

def ensure_node_env(min_required: str = MIN_NODE, track_major: str = "20"):
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
    success = create_migration(name)
    if success:
        print("Migration prepared successfully")
    else:
        print("Failed to prepare migration")
        return 1
    return 0

def handle_migrate(args):
    """Handle the migrate command (with auto-prep)"""
    name = args.name if hasattr(args, 'name') and args.name else None
    success = migrate(name)
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
    return 0 if success else 1


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
        print("Starting iOS (in background) + backend dev server...")
        ios_process = start_frontend(
            "ios",
            BUILD_DIR,
            app_name=project_name,
            env=env,
            metro_port=metro_port,
            watch_diagnostics=watch_diagnostics,
            rebuild=rebuild,
            environment=selected_environment,
        )
        if not ios_process:
            return False
        spawned_processes.append(ios_process)
        return run_uvicorn_with_watch(
            port,
            companion_process=ios_process,
            open_browser=True,
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
        )


def run_android(
    port: int = 8000,
    metro_port: int | None = None,
    watch_diagnostics: bool = False,
    rebuild: bool = False,
    environment: str | None = None,
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
        print("Starting Android (in background) + backend dev server...")
        android_process = start_frontend(
            "android",
            BUILD_DIR,
            app_name=project_name,
            env=env,
            metro_port=metro_port,
            watch_diagnostics=watch_diagnostics,
            rebuild=rebuild,
            environment=selected_environment,
        )
        if not android_process:
            return False
        spawned_processes.append(android_process)
        return run_uvicorn_with_watch(
            port,
            companion_process=android_process,
            open_browser=True,
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
    )


def run_mobile(
    port: int = 8000,
    metro_port: int | None = None,
    watch_diagnostics: bool = False,
    rebuild: bool = False,
    environment: str | None = None,
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
        print("Starting iOS + Android (in background) + backend dev server...")
        mobile_process = start_frontend(
            "mobile",
            BUILD_DIR,
            app_name=project_name,
            env=env,
            metro_port=metro_port,
            watch_diagnostics=watch_diagnostics,
            rebuild=rebuild,
            environment=selected_environment,
        )
        if not mobile_process:
            return False
        spawned_processes.append(mobile_process)
        return run_uvicorn_with_watch(
            port,
            companion_process=mobile_process,
            open_browser=True,
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
):
    """Watch app/ for changes and restart uvicorn worker (no parent reloader)."""
    proc = None
    successful = True

    try:
        if is_port_in_use(port):
            print(f"Port {port} is already in use.")
            resp = input(f"Use next available port (starting from {port + 1})? (y/n): ").strip().lower()
            if resp == 'y':
                port = find_next_available_port(port + 1)
                print(f"Using port {port} instead.")
            else:
                print("User declined to use another port. Exiting.")
                return False

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
NODE_VERSION = "20.19.4"

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

    try:
        return 0 if asyncio.run(classify()) else 1
    except (ValueError, RuntimeError) as error:
        print(f"Could not classify account: {error}")
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
  {FRAMEWORK_NAME.lower()} ios [--port 8000] [--metro-port 8081] [--watch-diagnostics] [--rebuild]
  {FRAMEWORK_NAME.lower()} android [--port 8000] [--metro-port 8081] [--watch-diagnostics] [--rebuild]
  {FRAMEWORK_NAME.lower()} mobile [--port 8000] [--metro-port 8081] [--watch-diagnostics] [--rebuild]
  {FRAMEWORK_NAME.lower()} doctor [web|ios|android|mobile|all]
  {FRAMEWORK_NAME.lower()} repair:ios [--fresh]
  {FRAMEWORK_NAME.lower()} upgrade [--check] [--to VERSION]
  {FRAMEWORK_NAME.lower()} prepmigrations [name]
  {FRAMEWORK_NAME.lower()} migrate [name]
  {FRAMEWORK_NAME.lower()} db make [name]
  {FRAMEWORK_NAME.lower()} db upgrade
  {FRAMEWORK_NAME.lower()} db check
  {FRAMEWORK_NAME.lower()} account classify <email> <regular|internal|tester>
  {FRAMEWORK_NAME.lower()} account role <email> <add|remove> <role>
  {FRAMEWORK_NAME.lower()} deploy init [render|container]
  {FRAMEWORK_NAME.lower()} deploy --check
  {FRAMEWORK_NAME.lower()} deploy [render|container]
  {FRAMEWORK_NAME.lower()} test
  {FRAMEWORK_NAME.lower()} del <directory>

The --port option controls the Python backend. --metro-port controls the
React Native bundler. --watch-diagnostics prints source paths that trigger
Fast Refresh. --rebuild forces native apps to rebuild and reinstall.
Use --environment development, staging, or production to select one shared
backend, web, and native runtime profile.
repair:ios preserves Podfile.lock unless --fresh is set.
upgrade creates recoverable backups and never overwrites modified managed files.
""",
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
            "--fresh",
            action="store_true",
            help="Allow repair:ios to recreate Podfile.lock",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Run a read-only upgrade or deployment preflight",
        )
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
            return 0 if run_ios(args.port, **run_arguments) else 1

        elif args.command == "android":
            run_arguments = {
                "metro_port": args.metro_port,
                "watch_diagnostics": args.watch_diagnostics,
                "rebuild": args.rebuild,
            }
            if args.environment:
                run_arguments["environment"] = args.environment
            return 0 if run_android(args.port, **run_arguments) else 1

        elif args.command == "mobile":
            run_arguments = {
                "metro_port": args.metro_port,
                "watch_diagnostics": args.watch_diagnostics,
                "rebuild": args.rebuild,
            }
            if args.environment:
                run_arguments["environment"] = args.environment
            return 0 if run_mobile(args.port, **run_arguments) else 1

        elif args.command == "web":
            return 0 if run_web(
                with_backend=False, environment=args.environment
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

        elif args.command == "deploy":
            return handle_deploy(args)

        elif args.command == "account":
            return handle_account(args)

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
