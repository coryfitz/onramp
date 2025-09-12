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
from watchfiles import watch
from .db.migrations import create_migration, migrate, init_migrations
from .rn_app import create_react_native_app
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
    if cur >= _semver_tuple(min_required):
        # Already good enough; just return current env
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
# Node.js / npm environment helpers
# -----------------------------------------------------------------------------

def write_nvmrc(project_root: str, track: str = "20"):
    for p in (project_root, os.path.join(project_root, "build")):
        try:
            with open(os.path.join(p, ".nvmrc"), "w") as f:
                f.write(track + "\n")  # or "20.19.4\n" to pin exact
        except Exception:
            pass


# -----------------------------------------------------------------------------
# Native project management
# -----------------------------------------------------------------------------
def to_rn_project_name(s: str) -> str:
    # RN app names must be alnum and start with a letter; use PascalCase
    parts = re.findall(r"[A-Za-z0-9]+", s)
    if not parts:
        return "App"
    name = "".join(p.capitalize() for p in parts)
    if not name[0].isalpha():
        name = "App" + name
    return name

def sync_js_app_name(build_dir: str, native_name: str):
    """Keep app.json in sync with the native scheme name.
    We DO NOT rewrite index.js because it already references app.json via appName."""
    app_json = os.path.join(build_dir, "app.json")
    if not os.path.exists(app_json):
        return
    import json
    try:
        with open(app_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["name"] = native_name
        if not data.get("displayName"):
            data["displayName"] = native_name
        with open(app_json, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Warning: could not update app.json: {e}")


def _ensure_rn_cli_deps(build_dir: str, env: dict):
    import json
    pkg = os.path.join(build_dir, "package.json")
    try:
        with open(pkg, "r", encoding="utf-8") as f:
            data = json.load(f)
        dev = data.get("devDependencies", {})
        needed = [
            "@react-native-community/cli",
            "@react-native-community/cli-platform-ios",
            "@react-native-community/cli-platform-android",
        ]
        missing = [p for p in needed if p not in dev]
        if missing:
            print("Adding React Native CLI devDependencies:", ", ".join(missing))
            subprocess.run(
                ["npm", "i", "-D",
                 "@react-native-community/cli@^20.0.2",
                 "@react-native-community/cli-platform-ios@^20.0.2",
                 "@react-native-community/cli-platform-android@^20.0.2"],
                cwd=build_dir, check=True, env=env
            )
    except Exception as e:
        print(f"Warning: could not verify/add RN CLI devDependencies: {e}")

def ensure_ios_pods(ios_dir: str, env: dict):
    """Ensure CocoaPods are installed for the iOS project."""
    if platform.system() != "Darwin" or not os.path.isdir(ios_dir):
        return
    try:
        subprocess.run(['which', 'pod'], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        print("CocoaPods not found (skipping). Install with `brew install cocoapods`.")
        return

    print("Ensuring iOS dependencies (Pods)...")
    res = subprocess.run(['pod', 'install'], cwd=ios_dir, env=env)
    if res.returncode != 0:
        print("`pod install` failed. Common causes:")
        print(" - Missing @react-native-community/cli devDependencies")
        print(" - Ruby/CocoaPods setup issues")
        print("Try: `cd build && npm i -D @react-native-community/cli@^20 "
              "@react-native-community/cli-platform-ios@^20 "
              "@react-native-community/cli-platform-android@^20`")
        print("Then: `cd build/ios && pod install`")
    else:
        print("✓ iOS dependencies installed")


def ensure_native_projects(custom_env=None, project_name: str | None = None):
    ios_dir = os.path.join(BUILD_DIR, 'ios')
    android_dir = os.path.join(BUILD_DIR, 'android')
    if os.path.exists(ios_dir) and os.path.exists(android_dir):
        env = custom_env or ensure_node_env()
        # Always make sure CLI devDeps exist (needed for use_native_modules!)
        _ensure_rn_cli_deps(BUILD_DIR, env)
        # On macOS, make sure Pods are installed/updated
        ensure_ios_pods(ios_dir, env)
        # Keep names in sync in case user renamed the folder
        root_basename = os.path.basename(PROJECT_ROOT)
        sync_js_app_name(BUILD_DIR, to_rn_project_name(root_basename))
        return True

    print("Native projects not found. Initializing...")

    env = custom_env or ensure_node_env()

    # derive native name from the root folder (e.g. 'myapp' -> 'Myapp')
    root_basename = os.path.basename(PROJECT_ROOT)
    native_name = to_rn_project_name(project_name or root_basename)

    build_pkg = os.path.join(BUILD_DIR, 'package.json')
    if os.path.exists(build_pkg) and not os.path.exists(os.path.join(BUILD_DIR, 'node_modules')):
        print("Installing npm dependencies...")
        subprocess.run(['npm', 'install', '--legacy-peer-deps'], cwd=BUILD_DIR, check=True, env=env)

    # Make sure CLI dev deps are present for autolinking
    _ensure_rn_cli_deps(BUILD_DIR, env)

    temp_dir = os.path.join(PROJECT_ROOT, 'temp_rn_init')
    try:
        subprocess.run([
            'npx', '--yes', '@react-native-community/cli@20.0.2', 'init', native_name,
            '--version', '0.81.1',
            '--directory', temp_dir,
            '--skip-install'
        ], check=True, cwd=PROJECT_ROOT, env=env)

        temp_project_dir = temp_dir  # CLI writes directly to temp_dir
        temp_ios_dir = os.path.join(temp_project_dir, 'ios')
        temp_android_dir = os.path.join(temp_project_dir, 'android')

        os.makedirs(BUILD_DIR, exist_ok=True)
        if not os.path.exists(ios_dir):
            shutil.copytree(temp_ios_dir, ios_dir)
            print(f"✓ iOS project initialized ({native_name})")
        if not os.path.exists(android_dir):
            shutil.copytree(temp_android_dir, android_dir)
            print(f"✓ Android project initialized ({native_name})")

        # copy package.json if your generator didn't create one yet
        build_package_json = os.path.join(BUILD_DIR, 'package.json')
        if not os.path.exists(build_package_json):
            shutil.copy2(os.path.join(temp_project_dir, 'package.json'), build_package_json)

        # keep JS & native names in sync
        sync_js_app_name(BUILD_DIR, native_name)
        write_nvmrc(PROJECT_ROOT)

        # iOS pods on macOS
        if platform.system() == "Darwin" and os.path.exists(ios_dir):
            try:
                subprocess.run(['which', 'pod'], check=True, capture_output=True)
            except subprocess.CalledProcessError:
                print("CocoaPods not found (skipping). Install with `brew install cocoapods`.")
            else:
                print("Installing iOS dependencies...")
                res = subprocess.run(['pod', 'install'], cwd=ios_dir, env=env)
                if res.returncode != 0:
                    print("`pod install` failed. Common causes:")
                    print(" - Missing @react-native-community/cli devDependencies")
                    print(" - Ruby/CocoaPods setup issues")
                    print("Try: `cd build && npm i -D @react-native-community/cli@^20 @react-native-community/cli-platform-ios@^20 @react-native-community/cli-platform-android@^20`")
                    print("Then: `cd build/ios && pod install`")
                else:
                    print("✓ iOS dependencies installed")
        return True

    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


# -----------------------------------------------------------------------------
# Platform-specific runners
# -----------------------------------------------------------------------------
def run_web(with_backend=True, port=8000):
    if not os.path.exists(BUILD_DIR):
        print("Build directory not found. Run 'onramp new <name>' first.")
        return

    env = ensure_node_env()
    if with_backend:
        backend_enabled = getattr(settings, 'BACKEND', True)
        if backend_enabled:
            print("Starting web frontend and backend...")
            web_process = subprocess.Popen(["npm", "run", "start:web"], cwd=BUILD_DIR, env=env)
            spawned_processes.append(web_process)
            run_uvicorn_with_watch(port)
        else:
            print("Backend disabled. Running web only...")
            subprocess.run(["npm", "run", "start:web"], cwd=BUILD_DIR, env=env)
    else:
        print("Running web development server...")
        subprocess.run(["npm", "run", "start:web"], cwd=BUILD_DIR, env=env)


def run_ios(port: int = 8000):
    """Run iOS simulator; if BACKEND=True also start the backend dev server."""
    if not os.path.exists(BUILD_DIR):
        print("Build directory not found. Run 'onramp new <name>' first.")
        return

    if platform.system() != "Darwin":
        print("iOS development requires macOS.")
        return

    # Single Node env to reuse everywhere
    env = ensure_node_env()
    try:
        node_ver = subprocess.run(["node", "-v"], capture_output=True, text=True, check=True, env=env).stdout.strip()
        print(f"Using Node.js {node_ver} environment")
    except Exception:
        print("Using Node.js environment (version check failed)")

    native_name = to_rn_project_name(os.path.basename(PROJECT_ROOT))
    ios_dir = os.path.join(BUILD_DIR, "ios")

    # Xcode + components checks (same as before)
    try:
        result = subprocess.run(["xcodebuild", "-version"], capture_output=True, text=True, check=True)
        version_line = result.stdout.split("\n")[0]
        print(f"Found {version_line}")
        version_str = version_line.split()[1]
        parts = version_str.split(".")
        try:
            major_minor = float(parts[0] + "." + (parts[1] if len(parts) > 1 else "0"))
        except Exception:
            major_minor = 0.0
        if major_minor < 16.1:
            print("Warning: React Native 0.81.x works best with Xcode 16.1 or later.")
            print(f"   Current version: {version_str}")
            if input("Continue anyway? (y/n): ").strip().lower() != "y":
                return
    except subprocess.CalledProcessError:
        print("Could not detect Xcode version. Make sure Xcode is installed.")
        if input("Continue anyway? (y/n): ").strip().lower() != "y":
            return
    except Exception as e:
        print(f"Error checking Xcode version: {e}")

    print("Checking if Xcode components are properly installed...")
    try:
        subprocess.run(["xcodebuild", "-showsdks"], check=True, capture_output=True, text=True)
        try:
            subprocess.run(["xcodebuild", "-list"], check=True, capture_output=True, text=True,
                           cwd=ios_dir if os.path.isdir(ios_dir) else BUILD_DIR)
            print("Xcode components are working properly")
        except subprocess.CalledProcessError as list_error:
            err = list_error.stderr or str(list_error)
            if "DVTDownloads.framework" in err or "IDESimulatorFoundation" in err:
                print("Detected missing Xcode framework components (IDESimulatorFoundation).")
                print("Running Xcode first launch setup to install required components...")
                try:
                    subprocess.run(["sudo", "xcodebuild", "-runFirstLaunch"], check=True, text=True)
                    print("Xcode components installed successfully")
                except subprocess.CalledProcessError as setup_error:
                    print(f"Xcode first launch failed: {setup_error}")
                    if input("Continue anyway? (y/n): ").strip().lower() != "y":
                        return
            else:
                print("Xcode components appear OK")
    except subprocess.CalledProcessError as e:
        err = e.stderr or str(e)
        if "DVTDownloads.framework" in err or "IDESimulatorFoundation" in err:
            print("Detected missing Xcode framework components.")
            print("Running Xcode first launch setup to install required components...")
            try:
                subprocess.run(["sudo", "xcodebuild", "-runFirstLaunch"], check=True, text=True)
                print("Xcode components installed successfully")
            except subprocess.CalledProcessError as setup_error:
                print(f"Xcode first launch failed: {setup_error}")
                if input("Continue anyway? (y/n): ").strip().lower() != "y":
                    return
        else:
            print(f"Xcode check failed with different error: {err}")
            print("Continuing with iOS setup...")

    print("Preparing iOS development...")

    # Ensure native projects (and pods) are ready
    if not ensure_native_projects(custom_env=env, project_name=native_name):
        print("Failed to set up iOS project.")
        return
    sync_js_app_name(BUILD_DIR, native_name)
    ensure_ios_pods(ios_dir, env)

    # Simulators
    print("Checking for iOS simulators...")
    try:
        result = subprocess.run(["xcrun", "simctl", "list", "devices", "available"],
                                capture_output=True, text=True, check=True)
        lines = result.stdout.split("\n")
        ios_simulators, in_ios = [], False
        for line in lines:
            if line.strip().startswith("-- iOS"):
                in_ios = True
                continue
            elif line.strip().startswith("--") and "iOS" not in line:
                in_ios = False
                continue
            elif in_ios and line.strip() and "(" in line and ")" in line:
                ios_simulators.append(line.strip())
        if not ios_simulators:
            print("No iOS simulators found.")
            if input("Download an iOS Simulator runtime now? (y/n): ").strip().lower() == "y":
                try:
                    print("Downloading iOS platform (this may take a while)...")
                    subprocess.run(["xcodebuild", "-downloadPlatform", "iOS"], check=True, text=True)
                    print("iOS platform downloaded successfully")
                except subprocess.CalledProcessError as dl_err:
                    print(f"Failed to download iOS platform automatically: {dl_err}")
                    print("Open Xcode → Settings → Platforms → download an iOS runtime.")
                    if input("Continue anyway? (y/n): ").strip().lower() != "y":
                        return
        else:
            print(f"Found {len(ios_simulators)} iOS simulator(s)")
    except subprocess.CalledProcessError:
        print("Could not check for simulators. Continuing anyway...")
    except Exception as e:
        print(f"Error checking simulators: {e}. Continuing anyway...")

    print("Preparing simulators...")
    try:
        subprocess.run(["xcrun", "simctl", "shutdown", "all"], capture_output=True, check=True)
        print("Shutdown any running simulators to prevent boot conflicts")
    except subprocess.CalledProcessError:
        pass

    # Backend behavior parity with web
    backend_enabled = getattr(settings, "BACKEND", True)
    if backend_enabled:
        print("Starting iOS (in background) + backend dev server...")
        try:
            ios_proc = subprocess.Popen(["npx", "react-native", "run-ios"], cwd=BUILD_DIR, env=env)
            spawned_processes.append(ios_proc)
        except Exception as e:
            print(f"Failed to launch iOS build: {e}")
            return
        # Run backend watcher in foreground (keeps this process alive)
        run_uvicorn_with_watch(port)
    else:
        # No backend: run iOS and block like before
        try:
            print("Starting iOS simulator...")
            subprocess.run(["npx", "react-native", "run-ios"], cwd=BUILD_DIR, check=True, env=env)
        except subprocess.CalledProcessError as e:
            print("Failed to start iOS simulator.")
            error_output = e.stderr or str(e)
            if "DVTDownloads.framework" in error_output or "IDESimulatorFoundation" in error_output:
                print("\nXcode Framework Issue Detected:")
                print("  sudo xcodebuild -runFirstLaunch")
            elif "styleText is not a function" in error_output:
                print("\nNode/RN CLI cache issue:")
                print("1) ensure_node_env selected your Node")
                print("2) npm cache clean --force")
                print("3) rm -rf build/node_modules && (cd build && npm install)")
            else:
                print("\nTroubleshooting steps:")
                print("1) sudo xcodebuild -runFirstLaunch")
                print("2) Ensure iOS Simulator is installed in Xcode")
                print("3) Try: (cd build && npx react-native run-ios)")
                print("4) Or open build/ios/*.xcworkspace in Xcode and build there")
                print("5) https://reactnative.dev/docs/set-up-your-environment")


def run_android():
    if not os.path.exists(BUILD_DIR):
        print("Build directory not found. Run 'onramp new <name>' first.")
        return

    if platform.system() == "Darwin":
        pass  # ok anywhere; just a reminder this needs Android SDK

    env = ensure_node_env()  # <-- get env first

    native_name = to_rn_project_name(os.path.basename(PROJECT_ROOT))
    if not ensure_native_projects(custom_env=env, project_name=native_name):
        print("Failed to set up native project.")
        return

    print("Preparing Android development...")
    try:
        node_ver = subprocess.run(["node","-v"], capture_output=True, text=True, check=True, env=env).stdout.strip()
        print(f"Using Node.js {node_ver} environment")
    except Exception:
        pass

    try:
        subprocess.run(["npm", "run", "android"], cwd=BUILD_DIR, check=True, env=env)
    except subprocess.CalledProcessError:
        print("Failed to start Android emulator. Make sure Android Studio and SDK are installed.")


# -----------------------------------------------------------------------------
# Frontend helpers
# -----------------------------------------------------------------------------
def open_new_terminal_and_run_npm(build_dir: str, env: dict):
    global spawned_processes
    system = platform.system()
    node_path = shutil.which("node", path=env.get("PATH"))
    node_dir = os.path.dirname(node_path) if node_path else None

    try:
        if system == "Windows":
            path_cmd = f'set "PATH={node_dir};%PATH%" && ' if node_dir else ''
            inner = f'{path_cmd}cd /d "{build_dir}" && npm start'
            cmd = f'start "" cmd /k "{inner}"'
            p = subprocess.Popen(cmd, shell=True)
            spawned_processes.append(p)
        elif system == "Darwin":
            prefix = f'export PATH="{node_dir}:$PATH"; ' if node_dir else ''
            shell_cmd = f'{prefix}cd "{build_dir}" && npm start'
            applescript_safe = shell_cmd.replace('"', r'\"')
            p = subprocess.Popen(['osascript', '-e',
                                  f'tell application "Terminal" to do script "{applescript_safe}"'])
            spawned_processes.append(p)
        else:
            prefix = f'export PATH="{node_dir}:$PATH"; ' if node_dir else ''
            bash_cmd = f'{prefix}cd "{build_dir}" && npm start; exec bash'
            for cmd in (
                ['gnome-terminal', '--', 'bash', '-lc', bash_cmd],
                ['xterm', '-e', f'bash -lc \'{bash_cmd}\''],
                ['konsole', '-e', f'bash -lc \'{bash_cmd}\''],
                ['x-terminal-emulator', '-e', f'bash -lc \'{bash_cmd}\''],
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

# -----------------------------------------------------------------------------
# Orchestration
# -----------------------------------------------------------------------------
def open_new_terminal_and_run_web(build_dir: str, env: dict):
    """Open a new terminal window and run `npm run start:web` in build_dir,
    ensuring the Node from `env` is used in that terminal session."""
    global spawned_processes
    system = platform.system()

    # Derive the selected node bin dir from env (if any)
    node_path = shutil.which("node", path=env.get("PATH"))
    node_dir = os.path.dirname(node_path) if node_path else None

    try:
        if system == "Windows":
            # Build PATH prefix only if we have a node_dir
            path_cmd = f'set "PATH={node_dir};%PATH%" && ' if node_dir else ''
            inner = f'{path_cmd}cd /d "{build_dir}" && npm run start:web'
            # Use a single string with shell=True; start is a cmd builtin.
            cmd = f'start "" cmd /k "{inner}"'
            p = subprocess.Popen(cmd, shell=True)
            spawned_processes.append(p)

        elif system == "Darwin":
            # Prepend PATH if available
            prefix = f'export PATH="{node_dir}:$PATH"; ' if node_dir else ''
            shell_cmd = f'{prefix}cd "{build_dir}" && npm run start:web'
            # Escape double quotes for AppleScript string
            applescript_safe = shell_cmd.replace('"', r'\"')
            p = subprocess.Popen(
                ['osascript', '-e',
                 f'tell application "Terminal" to do script "{applescript_safe}"']
            )
            spawned_processes.append(p)

        else:
            # Linux / *nix — try a few common terminals
            prefix = f'export PATH="{node_dir}:$PATH"; ' if node_dir else ''
            bash_cmd = f'{prefix}cd "{build_dir}" && npm run start:web; exec bash'

            candidates = [
                ['gnome-terminal', '--', 'bash', '-lc', bash_cmd],
                ['xterm', '-e', f'bash -lc \'{bash_cmd}\'' ],
                ['konsole', '-e', f'bash -lc \'{bash_cmd}\'' ],
                ['x-terminal-emulator', '-e', f'bash -lc \'{bash_cmd}\'' ],
            ]

            for cmd in candidates:
                try:
                    p = subprocess.Popen(cmd)
                    spawned_processes.append(p)
                    break
                except FileNotFoundError:
                    continue
            else:
                print("Could not find a suitable terminal emulator. "
                      "Please run 'npm run start:web' manually in the build directory.")

    except Exception as e:
        print(f"Failed to launch web server in a new terminal: {e}")



def run_command_logic(port=8000):
    if not os.path.exists(BUILD_DIR):
        print("No build directory found. Running backend only")
        run_uvicorn_with_watch(port)
        return

    env = ensure_node_env()

    try:
        backend_enabled = getattr(settings, 'BACKEND', True)
        if not backend_enabled:
            print("Backend is disabled. Running web development server")
            try:
                p = subprocess.Popen(["npm", "run", "start:web"], cwd=BUILD_DIR, env=env)
                spawned_processes.append(p)
                p.wait()
            except KeyboardInterrupt:
                print("\nShutting down web server...")
                cleanup_processes()
        else:
            print("Backend is enabled. Starting web frontend and backend")
            open_new_terminal_and_run_web(BUILD_DIR, env)
            run_uvicorn_with_watch(port)
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
                print("Note: Run 'onramp migrate' to complete database setup")
        except Exception:
            print("Note: Run 'onramp migrate' to set up database migrations")
        finally:
            os.chdir(original_cwd)

    except Exception as e:
        print(f"An error occurred while creating the directory: {e}")

def repair_ios(build_dir=BUILD_DIR):
    ios_dir = os.path.join(build_dir, "ios")
    # blow away derived intermediates that often cause xcodebuild 65
    subprocess.run(['rm', '-rf', os.path.expanduser('~/Library/Developer/Xcode/DerivedData')])
    subprocess.run(['rm', '-rf', os.path.join(ios_dir, 'Pods')])
    subprocess.run(['rm', '-f', os.path.join(ios_dir, 'Podfile.lock')])
    subprocess.run(['pod', 'install'], cwd=ios_dir, check=False)

# Unclear why these folders are being created - I should find a more elegant fix later
def _clean_empty_shadow_dirs(root):
    for d in ("app2", "build2"):
        p = os.path.join(root, d)
        if os.path.isdir(p) and not os.listdir(p):
            shutil.rmtree(p, ignore_errors=True)
            print(f"Removed empty shadow dir: {d}")

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
        parser.add_argument("command", help="The command to run")
        parser.add_argument("name", nargs='?', help="The name of the app directory/migration to be created")
        parser.add_argument("--port", type=int, default=8000, help="Port for the development server")
        parser.add_argument("--api", action="store_true", help="Create API-only app without React Native frontend")
        parser.add_argument("--web-only", action="store_true", help="Run web without backend")
        args = parser.parse_args()

        _clean_empty_shadow_dirs(PROJECT_ROOT)

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
            if args.web_only:
                run_web(with_backend=False, port=args.port)
            else:
                run_command_logic(port=args.port)

        elif args.command == "ios":
            run_ios(args.port)

        elif args.command == "android":
            run_android()

        elif args.command == "web":
            run_web(with_backend=False)

        elif args.command == "prepmigrations":
            return handle_prepmigrations(args)

        elif args.command == "migrate":
            return handle_migrate(args)
        
        elif args.command == "repair:ios":
            repair_ios()

        else:
            print(f"Invalid command. Available commands:")
            print(f"  {FRAMEWORK_NAME.lower()} new <name>     - Create new app")
            print(f"  {FRAMEWORK_NAME.lower()} run            - Run web development (default)")
            print(f"  {FRAMEWORK_NAME.lower()} web            - Run web only (no backend)")
            print(f"  {FRAMEWORK_NAME.lower()} ios            - Run iOS simulator")
            print(f"  {FRAMEWORK_NAME.lower()} android        - Run Android emulator")
            print(f"  {FRAMEWORK_NAME.lower()} prepmigrations - Prepare database migrations")
            print(f"  {FRAMEWORK_NAME.lower()} migrate        - Apply database migrations")

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
