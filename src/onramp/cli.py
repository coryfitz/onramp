import argparse
import importlib
import os
import shutil
import subprocess
import socket
import tomllib
import importlib.resources
import webbrowser
import platform
from .rn_app import create_react_native_app

script_dir = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(script_dir, "config.toml")

with open(config_path, "rb") as f:  # "rb" required for tomllib
    config = tomllib.load(f)
    FRAMEWORK_NAME = config['framework_name']

MODULE_NAME = FRAMEWORK_NAME.lower()

def is_port_in_use(port):
    """Check if a port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(('localhost', port)) == 0

def find_next_available_port(starting_port=8000):
    """Find the next available port starting from the specified port."""
    port = starting_port
    while is_port_in_use(port):
        port += 1
    return port

def open_new_terminal_and_run_npm(build_dir):
    """Open a new terminal window and run npm start in the build directory."""
    system = platform.system()
    
    if system == "Windows":
        # Windows
        subprocess.Popen(['start', 'cmd', '/k', f'cd /d "{build_dir}" && npm start'], shell=True)
    elif system == "Darwin":
        # macOS
        subprocess.Popen(['osascript', '-e', f'tell application "Terminal" to do script "cd \\"{build_dir}\\" && npm start"'])
    else:
        # Linux and other Unix-like systems
        # Try common terminal emulators
        terminal_commands = [
            ['gnome-terminal', '--', 'bash', '-c', f'cd "{build_dir}" && npm start; exec bash'],
            ['xterm', '-e', f'cd "{build_dir}" && npm start; bash'],
            ['konsole', '-e', f'cd "{build_dir}" && npm start; bash'],
            ['x-terminal-emulator', '-e', f'cd "{build_dir}" && npm start; bash']
        ]
        
        for cmd in terminal_commands:
            try:
                subprocess.Popen(cmd)
                break
            except FileNotFoundError:
                continue
        else:
            print("Could not find a suitable terminal emulator. Please run 'npm start' manually in the build directory.")

def run_uvicorn(port=8000):
    """Run Uvicorn server with a specific port and hot-reload enabled."""
    try:
        if is_port_in_use(port):
            print(f"Port {port} is already in use.")
            response = input(f"Do you want to use the next available port (starting from {port + 1})? (y/n): ").strip().lower()
            if response == 'y':
                port = find_next_available_port(port + 1)
                print(f"Using port {port} instead.")
            else:
                print("User declined to use another port. Exiting.")
                return  # Exit early, preventing further execution

        print(f"Starting Uvicorn on port {port}...")
        
        # Change to app directory before running uvicorn
        app_dir = os.path.join(os.getcwd(), 'app')
        if os.path.exists(app_dir):
            os.chdir(app_dir)
        
        subprocess.Popen(["uvicorn", "app:app", "--reload", "--port", str(port)])
        

    except subprocess.CalledProcessError as e:
        print(f"An error occurred while running Uvicorn: {e}")

def run_command_logic(port=8000):
    """Handle the run command logic based on directory structure and settings."""
    current_dir = os.getcwd()
    build_dir = os.path.join(current_dir, 'build')
    app_dir = os.path.join(current_dir, 'app')
    
    # Check if build directory exists
    if not os.path.exists(build_dir):
        # No build directory, just run uvicorn in app directory
        print("No build directory found. Running uvicorn in app directory...")
        run_uvicorn(port)
        return
    
    # Build directory exists, check settings from the app directory
    try:
        # Use importlib to dynamically import settings from the app directory
        import importlib.util
        settings_path = os.path.join(app_dir, 'settings.py')
        
        if not os.path.exists(settings_path):
            print("No settings.py found in app directory. Running uvicorn in app directory...")
            run_uvicorn(port)
            return
        
        spec = importlib.util.spec_from_file_location("app_settings", settings_path)
        settings = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(settings)
        
        # Check if BACKEND attribute exists and get its value
        backend_enabled = getattr(settings, 'BACKEND', True)  # Default to True if not found
        
        if not backend_enabled:
            # Backend is False, only run npm start in build directory
            print("Backend is disabled. Running npm start in build directory...")
            os.chdir(build_dir)
            subprocess.run(["npm", "start"])
        else:
            # Backend is True, run both npm start (in new terminal) and uvicorn
            print("Backend is enabled. Starting both frontend and backend...")
            
            # Open new terminal and run npm start
            open_new_terminal_and_run_npm(build_dir)
            
            # Run uvicorn in app directory in current terminal
            os.chdir(app_dir)
            run_uvicorn(port)
    
    except ImportError:
        print("Could not import settings from app directory. Running uvicorn in app directory...")
        run_uvicorn(port)
    except Exception as e:
        print(f"Error checking settings: {e}. Running uvicorn in app directory...")
        run_uvicorn(port)

def create_app_directory(name, api_only=False):
    """Create a new application directory using templates."""
    directory_path = os.path.join(os.getcwd(), name)

    if os.path.exists(directory_path):
        print('app name already exists at this directory')
        return  # Exit early if the directory already exists

    try:
        os.makedirs(directory_path, exist_ok=True)

        TEMPLATES_MODULE = importlib.import_module(f"{MODULE_NAME}.templates")

        backend_dir = os.path.join(directory_path, 'app')
        os.makedirs(backend_dir, exist_ok=True)

        new_settings_path = os.path.join(backend_dir, 'settings.py')
        master_settings = importlib.resources.files(TEMPLATES_MODULE) / 'settings.py'
        shutil.copyfile(master_settings, new_settings_path)

        new_app_path = os.path.join(backend_dir, 'app.py')
        master_app = importlib.resources.files(TEMPLATES_MODULE) / 'app.py'
        shutil.copyfile(master_app, new_app_path)

        models_dir = os.path.join(backend_dir, 'models')
        os.makedirs(models_dir, exist_ok=True)

        new_models_path = os.path.join(models_dir, 'models.py')
        master_models = importlib.resources.files(TEMPLATES_MODULE) / 'models.py'
        shutil.copyfile(master_models, new_models_path)

        # Create the routes directory structure inside app/
        routes_dir = os.path.join(backend_dir, 'routes')  # Inside app directory
        os.makedirs(routes_dir, exist_ok=True)

        # Create routes/api directory for API endpoints
        api_dir = os.path.join(routes_dir, 'api')
        os.makedirs(api_dir, exist_ok=True)

        # Create the initial API route file
        new_index_path = os.path.join(api_dir, 'index.py')
        master_index = importlib.resources.files(TEMPLATES_MODULE) / 'index.py'
        shutil.copyfile(master_index, new_index_path)

        if api_only:
            print(f"Created a new {FRAMEWORK_NAME} API at {directory_path}")
        else:
            print(f"Created a new {FRAMEWORK_NAME} backend at {directory_path}")

    except Exception as e:
        print(f"An error occurred while creating the directory: {e}")

def main():
    """CLI entry point."""
    # Create an argument parser
    parser = argparse.ArgumentParser(description=F"{FRAMEWORK_NAME} App Generator and Runner")
    
    # Add 'new' and 'run' commands
    parser.add_argument("command", help="The command to run (e.g., new or run)")
    parser.add_argument("name", nargs='?', help="The name of the app directory to be created (for 'new' command)")
    parser.add_argument("--port", type=int, help="The port to run the development server on (for 'run' command)", default=8000)
    parser.add_argument("--api", action="store_true", help="Create API-only app without React Native frontend (for 'new' command)")

    # Parse the arguments
    args = parser.parse_args()

    # Check which command is provided
    if args.command == "new":
        if args.name:
            create_app_directory(args.name, api_only=args.api)
            if not args.api:
                create_react_native_app(args.name)
        else:
            print(f"Please provide a name for the new app. Usage: '{FRAMEWORK_NAME.lower()} new <name>'")
    elif args.command == "run":
        run_command_logic(port=args.port)
    else:
        print(f"Invalid command. Use '{FRAMEWORK_NAME.lower()} new <name>' to create a new app or '{FRAMEWORK_NAME.lower()} run' to run the development server.")

if __name__ == "__main__":
    main()