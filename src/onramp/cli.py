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
from types import SimpleNamespace
from watchfiles import watch

# Also set the env flag so children inherit it (uvicorn worker, etc.)
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

PROJECT_ROOT = os.path.abspath(os.getcwd())
APP_DIR = os.path.join(PROJECT_ROOT, 'app')
BUILD_DIR = os.path.join(PROJECT_ROOT, 'build')
SETTINGS_PATH = os.path.join(APP_DIR, 'settings.py')

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

from .rn_app import create_react_native_app

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
# Frontend helpers
# -----------------------------------------------------------------------------
def open_new_terminal_and_run_npm(build_dir: str):
    """Open a new terminal window and run npm start in the build directory."""
    global spawned_processes
    system = platform.system()
    try:
        if system == "Windows":
            p = subprocess.Popen(['start', 'cmd', '/k', f'cd /d "{build_dir}" && npm start'], shell=True)
            spawned_processes.append(p)
        elif system == "Darwin":
            p = subprocess.Popen(['osascript', '-e', f'tell application "Terminal" to do script "cd \\"{build_dir}\\" && npm start"'])
            spawned_processes.append(p)
        else:
            for cmd in (
                ['gnome-terminal', '--', 'bash', '-c', f'cd "{build_dir}" && npm start; exec bash'],
                ['xterm', '-e', f'cd "{build_dir}" && npm start; bash'],
                ['konsole', '-e', f'cd "{build_dir}" && npm start; bash'],
                ['x-terminal-emulator', '-e', f'cd "{build_dir}" && npm start; bash'],
            ):
                try:
                    p = subprocess.Popen(cmd)
                    spawned_processes.append(p)
                    break
                except FileNotFoundError:
                    continue
            else:
                print("Could not find a suitable terminal emulator. Please run 'npm start' manually in the build directory.")
    except Exception as e:
        print(f"Failed to launch npm in a new terminal: {e}")

# -----------------------------------------------------------------------------
# Backend (Uvicorn) helpers
# -----------------------------------------------------------------------------
def _uvicorn_cmd(port: int):
    # -B: disable .pyc writes for the worker
    # Use the library version of app.py instead of local app:app
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

        # start first worker
        proc = _start_uvicorn_worker(APP_DIR, port)

        # restart on changes
        for _changes in watch(APP_DIR):
            print("Changes detected, restarting server...")
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

# -----------------------------------------------------------------------------
# Orchestration
# -----------------------------------------------------------------------------
def run_command_logic(port=8000):
    """Handle the run command logic based on directory structure and settings."""
    if not os.path.exists(BUILD_DIR):
        print("No build directory found. Running backend only")
        run_uvicorn_with_watch(port)
        return

    try:
        backend_enabled = getattr(settings, 'BACKEND', True)

        if not backend_enabled:
            print("Backend is disabled. Running npm start in build directory")
            try:
                p = subprocess.Popen(["npm", "start"], cwd=BUILD_DIR)
                spawned_processes.append(p)
                p.wait()
            except KeyboardInterrupt:
                print("\nShutting down npm...")
                cleanup_processes()
        else:
            print("Backend is enabled. Starting both frontend and backend")
            open_new_terminal_and_run_npm(BUILD_DIR)  # frontend in a new terminal
            run_uvicorn_with_watch(port)               # backend in this terminal

    except Exception as e:
        print(f"Error checking settings: {e}. Running backend only")
        run_uvicorn_with_watch(port)

# -----------------------------------------------------------------------------
# Project scaffolding
# -----------------------------------------------------------------------------
def create_app_directory(name, api_only=False):
    """Create a new application directory using templates."""
    directory_path = os.path.join(PROJECT_ROOT, name)
    if os.path.exists(directory_path):
        print('app name already exists at this directory')
        return

    try:
        print(f"Creating {FRAMEWORK_NAME} {'API' if api_only else 'backend'}...")

        os.makedirs(directory_path, exist_ok=True)
        TEMPLATES_MODULE = importlib.import_module(f"{MODULE_NAME}.templates")

        backend_dir = os.path.join(directory_path, 'app')
        os.makedirs(backend_dir, exist_ok=True)

        shutil.copyfile(importlib.resources.files(TEMPLATES_MODULE) / 'settings.py',
                        os.path.join(backend_dir, 'settings.py'))

        models_dir = os.path.join(backend_dir, 'models')
        os.makedirs(models_dir, exist_ok=True)
        shutil.copyfile(importlib.resources.files(TEMPLATES_MODULE) / 'models.py',
                        os.path.join(models_dir, 'models.py'))

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
        shutil.copyfile(importlib.resources.files(TEMPLATES_MODULE) / 'index.py',
                        os.path.join(api_dir, 'index.py'))

        print(f"{FRAMEWORK_NAME} {'API' if api_only else 'backend'} created")
    except Exception as e:
        print(f"An error occurred while creating the directory: {e}")

# -----------------------------------------------------------------------------
# CLI entrypoint
# -----------------------------------------------------------------------------
def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    atexit.register(cleanup_processes)

    original_cwd = os.getcwd()
    try:
        parser = argparse.ArgumentParser(description=f"{FRAMEWORK_NAME} App Generator and Runner")
        parser.add_argument("command", help="The command to run (e.g., new or run)")
        parser.add_argument("name", nargs='?', help="The name of the app directory to be created (for 'new' command)")
        parser.add_argument("--port", type=int, default=8000, help="Port for the development server")
        parser.add_argument("--api", action="store_true", help="Create API-only app without React Native frontend (for 'new' command)")
        args = parser.parse_args()

        if args.command == "new":
            if args.name:
                create_app_directory(args.name, api_only=args.api)
                if not args.api:
                    try:
                        os.chdir(original_cwd)
                    except Exception:
                        pass
                    create_react_native_app(args.name)
            else:
                print(f"Please provide a name for the new app. Usage: '{FRAMEWORK_NAME.lower()} new <name>'")
        elif args.command == "run":
            run_command_logic(port=args.port)
        else:
            print(f"Invalid command. Use '{FRAMEWORK_NAME.lower()} new <name>' or '{FRAMEWORK_NAME.lower()} run'.")
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        cleanup_processes()
        os._exit(0)
    finally:
        try:
            os.chdir(original_cwd)
        except (FileNotFoundError, OSError):
            try:
                os.chdir(os.path.dirname(original_cwd))
            except (FileNotFoundError, OSError):
                os.chdir(os.path.expanduser("~"))

if __name__ == "__main__":
    main()