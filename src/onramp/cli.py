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
# Native project management
# -----------------------------------------------------------------------------
def ensure_native_projects():
    """Ensure iOS and Android projects exist, create them if they don't."""
    ios_dir = os.path.join(BUILD_DIR, 'ios')
    android_dir = os.path.join(BUILD_DIR, 'android')
    
    if os.path.exists(ios_dir) and os.path.exists(android_dir):
        return True
    
    print("Native projects not found. Initializing...")
    
    # Create in temp directory to avoid CLI issues
    temp_dir = os.path.join(PROJECT_ROOT, 'temp_rn_init')
    try:
        # Use community CLI to avoid Node.js version issues
        subprocess.run([
            'npx', '@react-native-community/cli@latest', 'init', 'TempProject',
            '--version', '0.75.4',  # Use a more recent but stable version
            '--directory', temp_dir,
            '--skip-install'  # Skip npm install to avoid dependency issues
        ], check=True, cwd=PROJECT_ROOT)
        
        # The project is created directly in temp_dir, not in a subdirectory
        temp_project_dir = temp_dir
        
        # Check if the expected directories exist
        temp_ios_dir = os.path.join(temp_project_dir, 'ios')
        temp_android_dir = os.path.join(temp_project_dir, 'android')
        
        if not os.path.exists(temp_ios_dir) or not os.path.exists(temp_android_dir):
            print(f"Error: Expected native directories not found in {temp_project_dir}")
            print(f"iOS dir exists: {os.path.exists(temp_ios_dir)}")
            print(f"Android dir exists: {os.path.exists(temp_android_dir)}")
            if os.path.exists(temp_project_dir):
                print(f"Contents of temp project dir: {os.listdir(temp_project_dir)}")
            return False
        
        # Ensure build directory exists
        os.makedirs(BUILD_DIR, exist_ok=True)
        
        # Copy native folders to build directory
        if not os.path.exists(ios_dir):
            shutil.copytree(temp_ios_dir, ios_dir)
            print("✓ iOS project initialized")
            
        if not os.path.exists(android_dir):
            shutil.copytree(temp_android_dir, android_dir)
            print("✓ Android project initialized")
        
        # Install npm dependencies in build directory if package.json doesn't exist
        build_package_json = os.path.join(BUILD_DIR, 'package.json')
        if not os.path.exists(build_package_json):
            temp_package_json = os.path.join(temp_project_dir, 'package.json')
            if os.path.exists(temp_package_json):
                shutil.copy2(temp_package_json, build_package_json)
                print("Installing npm dependencies...")
                subprocess.run(['npm', 'install'], cwd=BUILD_DIR, check=True)
                print("✓ npm dependencies installed")
        
        # Only try to install iOS dependencies on macOS and if CocoaPods is available
        if platform.system() == "Darwin" and os.path.exists(ios_dir):
            try:
                # Check if pod command exists
                subprocess.run(['which', 'pod'], check=True, capture_output=True)
                print("Installing iOS dependencies...")
                subprocess.run(['pod', 'install'], cwd=ios_dir, check=True)
                print("✓ iOS dependencies installed")
            except subprocess.CalledProcessError:
                print("⚠️  CocoaPods not found or failed. You may need to:")
                print("   1. Install CocoaPods: sudo gem install cocoapods")
                print("   2. Run 'pod install' manually in the build/ios directory")
                print("   3. Ensure Xcode version >= 16.1 (current requirement)")
                # Don't fail here, let the user handle CocoaPods manually
        
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"Failed to initialize native projects: {e}")
        print("This might be due to:")
        print("  - Xcode version compatibility (React Native 0.75+ requires Xcode 16.1+)")
        print("  - Missing React Native development environment setup")
        print("  - Network issues downloading dependencies")
        print(f"  - Please check: https://reactnative.dev/docs/set-up-your-environment")
        return False
    except Exception as e:
        print(f"Error during native project setup: {e}")
        return False
    finally:
        # Clean up temp directory
        if os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception as e:
                print(f"Warning: Could not clean up temp directory {temp_dir}: {e}")


# -----------------------------------------------------------------------------
# Platform-specific runners
# -----------------------------------------------------------------------------
def run_web(with_backend=True, port=8000):
    """Run web development server."""
    if not os.path.exists(BUILD_DIR):
        print("Build directory not found. Run 'onramp new <name>' first.")
        return
    
    if with_backend:
        backend_enabled = getattr(settings, 'BACKEND', True)
        if backend_enabled:
            print("Starting web frontend and backend...")
            # Start web in background
            web_process = subprocess.Popen(["npm", "run", "start:web"], cwd=BUILD_DIR)
            spawned_processes.append(web_process)
            # Run backend in foreground
            run_uvicorn_with_watch(port)
        else:
            print("Backend disabled. Running web only...")
            subprocess.run(["npm", "run", "start:web"], cwd=BUILD_DIR)
    else:
        print("Running web development server...")
        subprocess.run(["npm", "run", "start:web"], cwd=BUILD_DIR)

def run_ios():
    """Run iOS simulator."""
    if not os.path.exists(BUILD_DIR):
        print("Build directory not found. Run 'onramp new <name>' first.")
        return
        
    if platform.system() != "Darwin":
        print("iOS development requires macOS.")
        return
    
    # Check Xcode version before proceeding
    try:
        result = subprocess.run(['xcodebuild', '-version'], capture_output=True, text=True, check=True)
        version_line = result.stdout.split('\n')[0]
        print(f"Found {version_line}")
        
        # Extract version number (e.g., "Xcode 15.4" -> "15.4")
        version_str = version_line.split()[1]
        major_version = float(version_str.split('.')[0] + '.' + version_str.split('.')[1])
        
        if major_version < 16.1:
            print(f"⚠️  Warning: React Native 0.81.1 requires Xcode 16.1 or later.")
            print(f"   Current version: {version_str}")
            print(f"   Please update Xcode to use React Native 0.81.1 features.")
            print(f"   You can update Xcode through the Mac App Store or Apple Developer portal.")
            
            response = input("Continue anyway? (y/n): ").strip().lower()
            if response != 'y':
                return
                
    except subprocess.CalledProcessError:
        print("⚠️  Could not detect Xcode version. Make sure Xcode is installed.")
        response = input("Continue anyway? (y/n): ").strip().lower()
        if response != 'y':
            return
    except Exception as e:
        print(f"⚠️  Error checking Xcode version: {e}")
        
    print("Preparing iOS development...")
    
    # Ensure native projects exist
    if not ensure_native_projects():
        print("Failed to set up iOS project.")
        return
        
    # Run iOS
    try:
        subprocess.run(["npm", "run", "ios"], cwd=BUILD_DIR, check=True)
    except subprocess.CalledProcessError:
        print("Failed to start iOS simulator.")
        print("\nTroubleshooting steps:")
        print("1. Make sure iOS Simulator is installed with Xcode")
        print("2. Try running: 'npx react-native run-ios' manually in the build directory")
        print("3. Or open build/ios/YourApp.xcworkspace in Xcode and run from there")
        print("4. Check React Native environment setup: https://reactnative.dev/docs/set-up-your-environment")

def run_android():
    """Run Android emulator."""
    if not os.path.exists(BUILD_DIR):
        print("Build directory not found. Run 'onramp new <name>' first.")
        return
        
    print("Preparing Android development...")
    
    # Ensure native projects exist
    if not ensure_native_projects():
        print("Failed to set up Android project.")
        return
        
    # Run Android
    try:
        subprocess.run(["npm", "run", "android"], cwd=BUILD_DIR, check=True)
    except subprocess.CalledProcessError:
        print("Failed to start Android emulator. Make sure Android Studio and SDK are installed.")


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
    
    # Files/patterns to ignore
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
        """Check if a file change should be ignored."""
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

        # start first worker
        proc = _start_uvicorn_worker(APP_DIR, port)

        # restart on changes
        for changes in watch(APP_DIR):
            # Filter out ignored changes
            filtered_changes = [
                change for change in changes 
                if not should_ignore_change(change[1])
            ]
            
            if not filtered_changes:
                continue  # Skip if all changes were ignored
                
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

def open_new_terminal_and_run_web(build_dir: str):
    """Open a new terminal window and run npm run start:web in the build directory."""
    global spawned_processes
    system = platform.system()
    try:
        if system == "Windows":
            p = subprocess.Popen(['start', 'cmd', '/k', f'cd /d "{build_dir}" && npm run start:web'], shell=True)
            spawned_processes.append(p)
        elif system == "Darwin":
            p = subprocess.Popen(['osascript', '-e', f'tell application "Terminal" to do script "cd \\"{build_dir}\\" && npm run start:web"'])
            spawned_processes.append(p)
        else:
            for cmd in (
                ['gnome-terminal', '--', 'bash', '-c', f'cd "{build_dir}" && npm run start:web; exec bash'],
                ['xterm', '-e', f'cd "{build_dir}" && npm run start:web; bash'],
                ['konsole', '-e', f'cd "{build_dir}" && npm run start:web; bash'],
                ['x-terminal-emulator', '-e', f'cd "{build_dir}" && npm run start:web; bash'],
            ):
                try:
                    p = subprocess.Popen(cmd)
                    spawned_processes.append(p)
                    break
                except FileNotFoundError:
                    continue
            else:
                print("Could not find a suitable terminal emulator. Please run 'npm run start:web' manually in the build directory.")
    except Exception as e:
        print(f"Failed to launch web server in a new terminal: {e}")

def run_command_logic(port=8000):
    """Handle the run command logic - web first approach."""
    if not os.path.exists(BUILD_DIR):
        print("No build directory found. Running backend only")
        run_uvicorn_with_watch(port)
        return

    try:
        backend_enabled = getattr(settings, 'BACKEND', True)

        if not backend_enabled:
            print("Backend is disabled. Running web development server")
            try:
                # Run web by default when backend is disabled
                p = subprocess.Popen(["npm", "run", "start:web"], cwd=BUILD_DIR)
                spawned_processes.append(p)
                p.wait()
            except KeyboardInterrupt:
                print("\nShutting down web server...")
                cleanup_processes()
        else:
            print("Backend is enabled. Starting web frontend and backend")
            # Start web instead of native by default
            open_new_terminal_and_run_web(BUILD_DIR)  # web in new terminal
            run_uvicorn_with_watch(port)              # backend in this terminal

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
        
        # Create __init__.py to make app a proper Python package
        with open(os.path.join(backend_dir, '__init__.py'), 'w') as f:
            f.write("# OnRamp App Package\n")

        shutil.copyfile(importlib.resources.files(TEMPLATES_MODULE) / 'settings.py',
                        os.path.join(backend_dir, 'settings.py'))

        models_dir = os.path.join(backend_dir, 'models')
        os.makedirs(models_dir, exist_ok=True)
        shutil.copyfile(importlib.resources.files(TEMPLATES_MODULE) / 'models.py',
                        os.path.join(models_dir, 'models.py'))
        # Create __init__.py to make models a proper Python package
        with open(os.path.join(models_dir, '__init__.py'), 'w') as f:
            f.write("# Models package\n")

        # Create db directory for database files and migrations
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
            # Change to the new project directory to initialize migrations
            os.chdir(directory_path)
            success = init_migrations(backend_dir)
            if success:
                print("Database migration system ready")
            else:
                print("Note: Run 'onramp migrate' to complete database setup")
        except Exception as e:
            print(f"Note: Run 'onramp migrate' to set up database migrations")
        finally:
            os.chdir(original_cwd)
            
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
        parser.add_argument("command", help="The command to run")
        parser.add_argument("name", nargs='?', help="The name of the app directory/migration to be created")
        parser.add_argument("--port", type=int, default=8000, help="Port for the development server")
        parser.add_argument("--api", action="store_true", help="Create API-only app without React Native frontend")
        parser.add_argument("--web-only", action="store_true", help="Run web without backend")
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
            # Use the sophisticated run_command_logic instead of just run_web
            if args.web_only:
                run_web(with_backend=False, port=args.port)
            else:
                run_command_logic(port=args.port)
            
        elif args.command == "ios":
            run_ios()
            
        elif args.command == "android":
            run_android()
            
        elif args.command == "web":
            run_web(with_backend=False)
            
        elif args.command == "prepmigrations":
            return handle_prepmigrations(args)
            
        elif args.command == "migrate":
            return handle_migrate(args)
            
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