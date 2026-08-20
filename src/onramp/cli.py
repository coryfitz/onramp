#!/usr/bin/env python3
import sys
sys.dont_write_bytecode = True

import argparse
import importlib
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
from watchfiles import watch
from .db.migrations import create_migration, migrate, init_migrations
from .frontend import (
    create_frontend,
    doctor_frontend,
    repair_frontend,
    run_frontend,
    start_frontend,
)
from .project import write_project_manifest
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

def cleanup_processes():
    """Clean up all spawned processes."""
    global spawned_processes
    for process in spawned_processes:
        try:
            if process.poll() is None:
                print(f"Terminating process {process.pid}...")
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
        except Exception:
            pass
    spawned_processes.clear()

def signal_handler(signum, frame):
    print("\nReceived interrupt signal. Cleaning up...")
    cleanup_processes()
    os._exit(0)

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
def run_web(with_backend=True, port=8000):
    if not os.path.exists(BUILD_DIR):
        print("Build directory not found. Run 'onramp new <name>' first.")
        return False

    env = ensure_node_env()
    if with_backend:
        backend_enabled = getattr(settings, 'BACKEND', True)
        if backend_enabled:
            print("Starting web frontend and backend...")
            web_process = start_frontend("web", BUILD_DIR, env=env)
            if not web_process:
                return False
            spawned_processes.append(web_process)
            run_uvicorn_with_watch(port)
            return True
        else:
            print("Backend disabled. Running web only...")
            return run_frontend("web", BUILD_DIR, env=env)
    else:
        print("Running web development server...")
        return run_frontend("web", BUILD_DIR, env=env)


def run_ios(
    port: int = 8000,
    metro_port: int | None = None,
    watch_diagnostics: bool = False,
):
    """Run iOS simulator; if BACKEND=True also start the backend dev server."""
    if not os.path.exists(BUILD_DIR):
        print("Build directory not found. Run 'onramp new <name>' first.")
        return False

    env = ensure_node_env()
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
        )
        if not ios_process:
            return False
        spawned_processes.append(ios_process)
        run_uvicorn_with_watch(port)
        return True
    else:
        return run_frontend(
            "ios",
            BUILD_DIR,
            app_name=project_name,
            env=env,
            metro_port=metro_port,
            watch_diagnostics=watch_diagnostics,
        )


def run_android(
    port: int = 8000,
    metro_port: int | None = None,
    watch_diagnostics: bool = False,
):
    if not os.path.exists(BUILD_DIR):
        print("Build directory not found. Run 'onramp new <name>' first.")
        return False

    env = ensure_node_env()
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
        )
        if not android_process:
            return False
        spawned_processes.append(android_process)
        run_uvicorn_with_watch(port)
        return True

    return run_frontend(
        "android",
        BUILD_DIR,
        app_name=project_name,
        env=env,
        metro_port=metro_port,
        watch_diagnostics=watch_diagnostics,
    )


def run_mobile(
    port: int = 8000,
    metro_port: int | None = None,
    watch_diagnostics: bool = False,
):
    """Run the iOS and Android apps with one shared backend process."""
    if not os.path.exists(BUILD_DIR):
        print("Build directory not found. Run 'onramp new <name>' first.")
        return False

    env = ensure_node_env()
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
        )
        if not mobile_process:
            return False
        spawned_processes.append(mobile_process)
        run_uvicorn_with_watch(port)
        return True

    return run_frontend(
        "mobile",
        BUILD_DIR,
        app_name=project_name,
        env=env,
        metro_port=metro_port,
        watch_diagnostics=watch_diagnostics,
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

def _start_uvicorn_worker(app_dir: str, port: int):
    """Start one uvicorn worker and track it."""
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    p = subprocess.Popen(_uvicorn_cmd(port), env=env, cwd=app_dir)
    spawned_processes.append(p)
    return p

def run_uvicorn_with_watch(port=8000):
    """Watch app/ for changes and restart uvicorn worker (no parent reloader)."""
    proc = None

    ignore_patterns = [
        '.sqlite3-shm',
        '.sqlite3-wal',
        '.sqlite3-journal',
        '.pyc',
        '.pyo',
        '__pycache__',
        '.DS_Store',
        'Thumbs.db',
        '.tmp',
        '.log'
    ]

    def should_ignore_change(file_path):
        file_path_str = str(file_path)
        return any(pattern in file_path_str for pattern in ignore_patterns)

    try:
        if is_port_in_use(port):
            print(f"Port {port} is already in use.")
            resp = input(f"Use next available port (starting from {port + 1})? (y/n): ").strip().lower()
            if resp == 'y':
                port = find_next_available_port(port + 1)
                print(f"Using port {port} instead.")
            else:
                print("User declined to use another port. Exiting.")
                return

        print(f"Dev watch active on {APP_DIR}.")
        proc = _start_uvicorn_worker(APP_DIR, port)

        for changes in watch(APP_DIR):
            filtered_changes = [change for change in changes if not should_ignore_change(change[1])]
            if not filtered_changes:
                continue
            print(f"Changes detected: {filtered_changes}")
            print("Restarting server...")
            try:
                if proc and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            except Exception as e:
                print(f"Error stopping previous worker: {e}")

            proc = _start_uvicorn_worker(APP_DIR, port)

    except KeyboardInterrupt:
        print("\nWatcher interrupted.")
    except Exception as e:
        print(f"Watcher error: {e}")
    finally:
        try:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            pass
        cleanup_processes()

def run_command_logic(port=8000):
    if not os.path.exists(BUILD_DIR):
        print("No build directory found. Running backend only")
        run_uvicorn_with_watch(port)
        return True

    try:
        backend_enabled = getattr(settings, 'BACKEND', True)
        return run_web(with_backend=backend_enabled, port=port)
    except Exception as e:
        print(f"Error checking settings: {e}. Running backend only")
        run_uvicorn_with_watch(port)
        return True

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
        if os.listdir(target):
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
            os.rmdir(target)
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
    except Exception as e:
        print(f"Delete failed: {e}")
        return 1


# -----------------------------------------------------------------------------
# CLI entrypoint
# -----------------------------------------------------------------------------
def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    atexit.register(cleanup_processes)

    original_cwd = os.getcwd()
    try:
        parser = argparse.ArgumentParser(
            description=f"{FRAMEWORK_NAME} App Generator and Runner",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog=f"""Commands:
  {FRAMEWORK_NAME.lower()} new <name> [--api | --mobile | --all]
  {FRAMEWORK_NAME.lower()} run [--port 8000]
  {FRAMEWORK_NAME.lower()} web
  {FRAMEWORK_NAME.lower()} ios [--port 8000] [--metro-port 8081] [--watch-diagnostics]
  {FRAMEWORK_NAME.lower()} android [--port 8000] [--metro-port 8081] [--watch-diagnostics]
  {FRAMEWORK_NAME.lower()} mobile [--port 8000] [--metro-port 8081] [--watch-diagnostics]
  {FRAMEWORK_NAME.lower()} doctor [web|ios|android|mobile|all]
  {FRAMEWORK_NAME.lower()} repair:ios [--fresh]
  {FRAMEWORK_NAME.lower()} upgrade [--check] [--to VERSION]
  {FRAMEWORK_NAME.lower()} prepmigrations [name]
  {FRAMEWORK_NAME.lower()} migrate [name]
  {FRAMEWORK_NAME.lower()} del <directory>

The --port option controls the Python backend. --metro-port controls the
React Native bundler. --watch-diagnostics prints source paths that trigger
Fast Refresh. repair:ios preserves Podfile.lock unless --fresh is set.
upgrade creates recoverable backups and never overwrites modified managed files.
""",
        )
        parser.add_argument("command", help="The command to run")
        parser.add_argument("name", nargs='?', help="The name of the app directory/migration to be created")
        parser.add_argument("--port", type=int, default=8000, help="Port for the development server")
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
            "--fresh",
            action="store_true",
            help="Allow repair:ios to recreate Podfile.lock",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Preview the project upgrade and report whether it can succeed",
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

        elif args.command == "run":
            if args.web_only:
                return 0 if run_web(with_backend=False, port=args.port) else 1
            else:
                return 0 if run_command_logic(port=args.port) else 1

        elif args.command == "ios":
            return 0 if run_ios(
                args.port,
                metro_port=args.metro_port,
                watch_diagnostics=args.watch_diagnostics,
            ) else 1

        elif args.command == "android":
            return 0 if run_android(
                args.port,
                metro_port=args.metro_port,
                watch_diagnostics=args.watch_diagnostics,
            ) else 1

        elif args.command == "mobile":
            return 0 if run_mobile(
                args.port,
                metro_port=args.metro_port,
                watch_diagnostics=args.watch_diagnostics,
            ) else 1

        elif args.command == "web":
            return 0 if run_web(with_backend=False) else 1

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
