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
# Node.js Environment Management
# -----------------------------------------------------------------------------
def setup_nodejs18_env():
    """Set up Node.js 18 environment and return the custom environment dict."""
    try:
        node_result = subprocess.run(['node', '--version'], capture_output=True, text=True, check=True)
        node_version = node_result.stdout.strip()
        print(f"Using Node.js {node_version}")
        
        # Parse major version
        major_node = int(node_version.lstrip('v').split('.')[0])
        if major_node <= 18:
            print("Node.js version is already 18 or lower, no changes needed")
            return None
            
        print(f"Warning: React Native 0.81.1 works best with Node.js 18 LTS.")
        print(f"Current version ({node_version}) may cause compatibility issues.")
        
        # Create .nvmrc file for project-specific Node.js version
        nvmrc_path = os.path.join(PROJECT_ROOT, '.nvmrc')
        if not os.path.exists(nvmrc_path):
            try:
                with open(nvmrc_path, 'w') as f:
                    f.write('18.18.0\n')
                print("Created .nvmrc file with Node.js 18.18.0 for this project")
            except Exception as e:
                print(f"Could not create .nvmrc file: {e}")
        
        # Check if nvm is available and properly sourced
        nvm_dir = os.path.expanduser('~/.nvm')
        nvm_sh = os.path.join(nvm_dir, 'nvm.sh')
        
        if not os.path.exists(nvm_sh):
            print("nvm not found. Installing nvm...")
            response = input("Install nvm automatically? (y/n): ").strip().lower()
            if response == 'y':
                try:
                    # Install nvm
                    install_script = subprocess.run([
                        'curl', '-o-', 
                        'https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh'
                    ], capture_output=True, text=True, check=True).stdout
                    
                    subprocess.run(['bash', '-c', install_script], check=True)
                    print("nvm installed successfully")
                    print("Please restart your terminal and run the command again for nvm to be available")
                    return None
                    
                except subprocess.CalledProcessError as e:
                    print(f"Failed to install nvm: {e}")
                    print("Continuing with current Node.js version")
                    return None
            else:
                print("Continuing with current Node.js version")
                return None
        
        # nvm exists, try to use it
        try:
            # Create a more robust bash script that properly sources nvm
            bash_script = f"""
            export NVM_DIR="{nvm_dir}"
            [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
            
            # Check if nvm is working
            if ! command -v nvm &> /dev/null; then
                echo "nvm command not found after sourcing"
                exit 1
            fi
            
            # Install Node.js 18.18.0 if not already installed
            if ! nvm list | grep -q "v18.18.0"; then
                nvm install 18.18.0
            fi
            
            # Get the path to Node.js 18.18.0
            nvm which 18.18.0
            """
            
            print("Setting up Node.js 18.18.0 with nvm...")
            result = subprocess.run(['bash', '-c', bash_script], 
                                   capture_output=True, text=True, check=True)
            
            node18_path = result.stdout.strip()
            
            if node18_path and os.path.exists(node18_path):
                print(f"Found Node.js 18 at: {node18_path}")
                node18_dir = os.path.dirname(node18_path)
                
                # Create custom environment with Node.js 18 first in PATH
                node18_env = os.environ.copy()
                node18_env['PATH'] = f"{node18_dir}:{node18_env['PATH']}"
                node18_env['NVM_DIR'] = nvm_dir
                
                # Verify the environment works
                verify_result = subprocess.run(['node', '--version'], 
                                             capture_output=True, text=True, 
                                             env=node18_env, check=True)
                verify_version = verify_result.stdout.strip()
                print(f"Verified Node.js environment: {verify_version}")
                
                if verify_version.startswith('v18.'):
                    print("✓ Node.js 18 environment configured successfully")
                    return node18_env
                else:
                    print(f"Warning: Expected Node.js 18, got {verify_version}")
                    return None
            else:
                print("Could not locate Node.js 18 installation")
                
                # Try a different approach - check if Node.js 18 is already installed
                check_script = f"""
                export NVM_DIR="{nvm_dir}"
                [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
                
                # Get all Node.js 18 versions, properly formatted
                nvm list | grep -E 'v18\.[0-9]+\.[0-9]+' | head -1 | sed 's/[^v0-9.]//g'
                """
                
                try:
                    list_result = subprocess.run(['bash', '-c', check_script], 
                                               capture_output=True, text=True, check=True)
                    available_v18 = list_result.stdout.strip()
                    
                    if available_v18 and available_v18.startswith('v18.'):
                        print(f"Found existing Node.js 18 installation: {available_v18}")
                        
                        # Get path to this version
                        path_script = f"""
                        export NVM_DIR="{nvm_dir}"
                        [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
                        nvm which {available_v18}
                        """
                        
                        path_result = subprocess.run(['bash', '-c', path_script], 
                                                   capture_output=True, text=True, check=True)
                        node18_path = path_result.stdout.strip()
                        
                        if node18_path and os.path.exists(node18_path):
                            node18_dir = os.path.dirname(node18_path)
                            node18_env = os.environ.copy()
                            node18_env['PATH'] = f"{node18_dir}:{node18_env['PATH']}"
                            node18_env['NVM_DIR'] = nvm_dir
                            
                            # Verify this works
                            verify_result = subprocess.run(['node', '--version'], 
                                                         capture_output=True, text=True, 
                                                         env=node18_env, check=True)
                            verify_version = verify_result.stdout.strip()
                            
                            if verify_version.startswith('v18.'):
                                print(f"✓ Using existing Node.js 18 installation: {verify_version}")
                                return node18_env
                            else:
                                print(f"Environment verification failed. Expected v18.x, got {verify_version}")
                    else:
                        print("No Node.js 18 installation found in nvm")
                        
                        # Try to install it
                        install_script = f"""
                        export NVM_DIR="{nvm_dir}"
                        [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
                        nvm install 18.18.0 && nvm which 18.18.0
                        """
                        
                        try:
                            install_result = subprocess.run(['bash', '-c', install_script], 
                                                          capture_output=True, text=True, check=True)
                            node18_path = install_result.stdout.strip()
                            
                            if node18_path and os.path.exists(node18_path):
                                node18_dir = os.path.dirname(node18_path)
                                node18_env = os.environ.copy()
                                node18_env['PATH'] = f"{node18_dir}:{node18_env['PATH']}"
                                node18_env['NVM_DIR'] = nvm_dir
                                
                                print("✓ Node.js 18.18.0 installed and configured")
                                return node18_env
                            else:
                                print("Failed to get Node.js 18 path after installation")
                        except subprocess.CalledProcessError as install_error:
                            print(f"Failed to install Node.js 18: {install_error}")
                                
                except subprocess.CalledProcessError:
                    print("Failed to check for existing Node.js 18 installations")
                    pass
                
                return None
                
        except subprocess.CalledProcessError as e:
            print(f"Failed to set up Node.js 18 with nvm: {e}")
            
            # Check if there's a shell-specific issue
            if e.returncode == 3:
                print("This might be a shell compatibility issue.")
                print("Try running these commands manually in your terminal:")
                print("  source ~/.nvm/nvm.sh")
                print("  nvm install 18.18.0")
                print("  nvm use 18.18.0")
                print("Then run 'onramp ios' again")
            
            print("Continuing with current Node.js version")
            return None
        
    except Exception as node_error:
        print(f"Error checking Node.js version: {node_error}")
        return None

# -----------------------------------------------------------------------------
# Native project management
# -----------------------------------------------------------------------------
def ensure_native_projects(custom_env=None):
    """Ensure iOS and Android projects exist, create them if they don't."""
    ios_dir = os.path.join(BUILD_DIR, 'ios')
    android_dir = os.path.join(BUILD_DIR, 'android')
    
    if os.path.exists(ios_dir) and os.path.exists(android_dir):
        return True
    
    print("Native projects not found. Initializing...")
    
    # Use custom environment if provided, otherwise use default
    env = custom_env if custom_env else os.environ
    
    # Create in temp directory to avoid CLI issues
    temp_dir = os.path.join(PROJECT_ROOT, 'temp_rn_init')
    try:
        # Use community CLI to avoid Node.js version issues
        subprocess.run([
            'npx', '@react-native-community/cli@latest', 'init', 'TempProject',
            '--version', '0.75.4',  # Use a more recent but stable version
            '--directory', temp_dir,
            '--skip-install'  # Skip npm install to avoid dependency issues
        ], check=True, cwd=PROJECT_ROOT, env=env)
        
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
                subprocess.run(['npm', 'install'], cwd=BUILD_DIR, check=True, env=env)
                print("✓ npm dependencies installed")
        
        # Only try to install iOS dependencies on macOS and if CocoaPods is available
        if platform.system() == "Darwin" and os.path.exists(ios_dir):
            try:
                # Check if pod command exists
                subprocess.run(['which', 'pod'], check=True, capture_output=True)
                print("Installing iOS dependencies...")
                subprocess.run(['pod', 'install'], cwd=ios_dir, check=True, env=env)
                print("✓ iOS dependencies installed")
            except subprocess.CalledProcessError:
                print("CocoaPods not found.")
                response = input("Would you like to install CocoaPods via Homebrew? (y/n): ").strip().lower()
                
                if response == 'y':
                    try:
                        # Check if Homebrew is installed first
                        subprocess.run(['which', 'brew'], check=True, capture_output=True)
                        
                        print("Installing CocoaPods via Homebrew...")
                        subprocess.run(['brew', 'install', 'cocoapods'], check=True)
                        print("✓ CocoaPods installed via Homebrew")
                        
                        # Now try to install pods
                        print("Installing iOS dependencies...")
                        subprocess.run(['pod', 'install'], cwd=ios_dir, check=True, env=env)
                        print("✓ iOS dependencies installed")
                        
                    except subprocess.CalledProcessError as brew_error:
                        # Check if it's because Homebrew isn't installed
                        try:
                            subprocess.run(['which', 'brew'], check=True, capture_output=True)
                            print(f"Homebrew install failed: {brew_error}")
                        except subprocess.CalledProcessError:
                            print("Homebrew not found. Please install Homebrew first:")
                            print("Visit https://brew.sh or run:")
                            print('/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"')
                            print("Then run 'brew install cocoapods'")
                            return True  # Don't fail the whole setup
                        
                        print("CocoaPods installation failed.")
                        print("Manual installation options:")
                        print("1. Try: brew install cocoapods")
                        print("2. If Homebrew issues persist, reinstall Homebrew")
                        print("3. Run 'pod install' manually in build/ios directory after installing")
                else:
                    print("Skipping CocoaPods installation.")
                    print("You can install it later with:")
                    print("1. brew install cocoapods (recommended)")
                    print("2. Then run 'pod install' in the build/ios directory")
        
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

def ensure_shell_nodejs18():
    """
    Ensure the shell environment is using Node.js 18 by restarting with nvm.
    This ensures Metro bundler also uses Node.js 18.
    """
    try:
        node_result = subprocess.run(['node', '--version'], capture_output=True, text=True, check=True)
        node_version = node_result.stdout.strip()
        
        # Parse major version
        major_node = int(node_version.lstrip('v').split('.')[0])
        if major_node <= 18:
            return  # Already using Node.js 18 or lower
            
        print(f"Shell is using Node.js {node_version}. Switching to Node.js 18 for Metro compatibility...")
        
        # Check if nvm is available
        nvm_dir = os.path.expanduser('~/.nvm')
        nvm_sh = os.path.join(nvm_dir, 'nvm.sh')
        
        if not os.path.exists(nvm_sh):
            print("nvm not found. Metro bundler may have Node.js compatibility issues.")
            print("Consider installing nvm and Node.js 18 manually:")
            print("  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh | bash")
            print("  nvm install 18 && nvm use 18")
            return
        
        # Create a script that sources nvm, switches to Node.js 18, and re-runs the CLI
        import sys
        
        # Get the original command arguments
        original_args = sys.argv[:]
        original_args[0] = 'onramp'  # Use the CLI command name
        
        # Create a temporary script to restart with Node.js 18
        temp_script_content = f'''#!/bin/bash
export NVM_DIR="{nvm_dir}"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

# Check if Node.js 18 is available
if ! nvm list | grep -q "v18"; then
    echo "Installing Node.js 18..."
    nvm install 18.18.0
fi

echo "Switching to Node.js 18..."
nvm use 18

# Verify the switch
node_version=$(node --version)
echo "Now using Node.js $node_version"

# Re-run the original command
cd "{PROJECT_ROOT}"
{' '.join(original_args)}
'''
        
        temp_script_path = os.path.join(PROJECT_ROOT, '.onramp_node18_restart.sh')
        try:
            with open(temp_script_path, 'w') as f:
                f.write(temp_script_content)
            os.chmod(temp_script_path, 0o755)
            
            print("Restarting CLI with Node.js 18...")
            print("(This ensures Metro bundler also uses Node.js 18)")
            
            # Execute the script and exit current process
            os.execv('/bin/bash', ['bash', temp_script_path])
            
        except Exception as e:
            print(f"Failed to restart with Node.js 18: {e}")
            print("You can manually run these commands:")
            print("  source ~/.nvm/nvm.sh")
            print("  nvm use 18")
            print("  onramp ios")
            
        finally:
            # Clean up temp script
            try:
                if os.path.exists(temp_script_path):
                    os.remove(temp_script_path)
            except:
                pass
                
    except Exception as e:
        print(f"Error checking shell Node.js version: {e}")


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
    
    # FIRST: Ensure shell is using Node.js 18 (this may restart the CLI)
    ensure_shell_nodejs18()
    
    # Check Xcode version and setup before proceeding
    try:
        result = subprocess.run(['xcodebuild', '-version'], capture_output=True, text=True, check=True)
        version_line = result.stdout.split('\n')[0]
        print(f"Found {version_line}")
        
        # Extract version number (e.g., "Xcode 15.4" -> "15.4")
        version_str = version_line.split()[1]
        major_version = float(version_str.split('.')[0] + '.' + version_str.split('.')[1])
        
        if major_version < 16.1:
            print(f"Warning: React Native 0.81.1 requires Xcode 16.1 or later.")
            print(f"   Current version: {version_str}")
            print(f"   Please update Xcode to use React Native 0.81.1 features.")
            print(f"   You can update Xcode through the Mac App Store or Apple Developer portal.")
            
            response = input("Continue anyway? (y/n): ").strip().lower()
            if response != 'y':
                return
                
    except subprocess.CalledProcessError:
        print("Could not detect Xcode version. Make sure Xcode is installed.")
        response = input("Continue anyway? (y/n): ").strip().lower()
        if response != 'y':
            return
    except Exception as e:
        print(f"Error checking Xcode version: {e}")
    
    # Set up Node.js 18 environment BEFORE any native setup
    node18_env = setup_nodejs18_env()
    
    # Check if Xcode components needed by React Native are working
    print("Checking if Xcode components are properly installed...")
    try:
        # First try the basic SDK check
        subprocess.run(['xcodebuild', '-showsdks'], check=True, capture_output=True, text=True)
        
        # Now try a more comprehensive check that's closer to what React Native does
        # We'll test if we can list build configurations, which requires the same frameworks
        try:
            subprocess.run(['xcodebuild', '-list'], check=True, capture_output=True, text=True, cwd=BUILD_DIR)
            print("Xcode components are working properly")
        except subprocess.CalledProcessError as list_error:
            # This is likely the DVTDownloads.framework issue
            error_output = list_error.stderr if list_error.stderr else str(list_error)
            if "DVTDownloads.framework" in error_output or "IDESimulatorFoundation" in error_output:
                print("Detected missing Xcode framework components (IDESimulatorFoundation).")
                print("Running Xcode first launch setup to install required components...")
                try:
                    result = subprocess.run(['sudo', 'xcodebuild', '-runFirstLaunch'], 
                                          check=True, text=True)
                    print("Xcode components installed successfully")
                except subprocess.CalledProcessError as setup_error:
                    print(f"Xcode first launch failed: {setup_error}")
                    print("This may cause issues with the iOS simulator.")
                    print("You can try running 'sudo xcodebuild -runFirstLaunch' manually.")
                    
                    response = input("Continue anyway? (y/n): ").strip().lower()
                    if response != 'y':
                        return
                except Exception as setup_error:
                    print(f"Error running Xcode first launch: {setup_error}")
                    print("You may need to run 'sudo xcodebuild -runFirstLaunch' manually.")
            else:
                print("Xcode components are working properly")
                
    except subprocess.CalledProcessError as e:
        # If even basic SDK listing fails, there's a more fundamental issue
        error_output = e.stderr if e.stderr else str(e)
        if "DVTDownloads.framework" in error_output or "IDESimulatorFoundation" in error_output:
            print("Detected missing Xcode framework components.")
            print("Running Xcode first launch setup to install required components...")
            try:
                result = subprocess.run(['sudo', 'xcodebuild', '-runFirstLaunch'], 
                                      check=True, text=True)
                print("Xcode components installed successfully")
            except subprocess.CalledProcessError as setup_error:
                print(f"Xcode first launch failed: {setup_error}")
                print("This may cause issues with the iOS simulator.")
                print("You can try running 'sudo xcodebuild -runFirstLaunch' manually.")
                
                response = input("Continue anyway? (y/n): ").strip().lower()
                if response != 'y':
                    return
            except Exception as setup_error:
                print(f"Error running Xcode first launch: {setup_error}")
                print("You may need to run 'sudo xcodebuild -runFirstLaunch' manually.")
        else:
            print(f"Xcode check failed with different error: {error_output}")
            print("Continuing with iOS setup...")
        
    print("Preparing iOS development...")
    
    # Ensure native projects exist (with Node.js 18 environment if available)
    if not ensure_native_projects(node18_env):
        print("Failed to set up iOS project.")
        return
    
    # Check if iOS simulators are available
    print("Checking for iOS simulators...")
    try:
        result = subprocess.run(['xcrun', 'simctl', 'list', 'devices', 'available'], 
                              capture_output=True, text=True, check=True)
        
        # Check if there are any iOS simulators available
        # Look for device entries under iOS sections
        lines = result.stdout.split('\n')
        ios_simulators = []
        in_ios_section = False
        
        for line in lines:
            # Check if we're entering an iOS section
            if line.strip().startswith('-- iOS'):
                in_ios_section = True
                continue
            # Check if we're leaving iOS section
            elif line.strip().startswith('--') and 'iOS' not in line:
                in_ios_section = False
                continue
            # If we're in an iOS section and find a device line
            elif in_ios_section and line.strip() and '(' in line and ')' in line:
                # This is a device line like "iPhone 16 Pro (UUID) (Shutdown)"
                ios_simulators.append(line.strip())
        
        if not ios_simulators:
            print("No iOS simulators found.")
            response = input("Would you like to install an iOS simulator automatically? (y/n): ").strip().lower()
            
            if response == 'y':
                try:
                    # First try to download iOS platform automatically
                    print("Downloading iOS simulator platform...")
                    subprocess.run(['xcodebuild', '-downloadPlatform', 'iOS'], 
                                 check=True, text=True)
                    print("iOS platform downloaded successfully")
                    
                    # Now check for available runtimes after download
                    print("Checking available iOS runtimes...")
                    runtime_result = subprocess.run(['xcrun', 'simctl', 'list', 'runtimes'], 
                                                  capture_output=True, text=True, check=True)
                    
                    # Look for an available iOS runtime
                    available_ios_runtime = None
                    for line in runtime_result.stdout.split('\n'):
                        if 'iOS' in line and 'com.apple.CoreSimulator.SimRuntime.iOS' in line:
                            # Extract the proper runtime identifier from the parentheses
                            if '(' in line and ')' in line:
                                runtime_id = line.split('(')[1].split(')')[0]
                                if runtime_id.startswith('com.apple.CoreSimulator.SimRuntime.iOS'):
                                    available_ios_runtime = runtime_id
                                    break
                    
                    if available_ios_runtime:
                        print(f"Creating iOS simulator with runtime {available_ios_runtime}...")
                        subprocess.run([
                            'xcrun', 'simctl', 'create', 
                            'iPhone 15 (OnRamp)', 'iPhone 15', available_ios_runtime
                        ], check=True, capture_output=True, text=True)
                        print("iOS simulator created successfully")
                    else:
                        print("No properly formatted iOS runtime found after platform download.")
                        print("Let's try listing available device types and runtimes...")
                        
                        # Debug: show what runtimes are actually available
                        try:
                            print("Available runtimes:")
                            print(runtime_result.stdout)
                        except:
                            pass
                        
                        print("You may need to create a simulator manually with:")
                        print("xcrun simctl list runtimes")
                        print("xcrun simctl create 'iPhone 15' 'iPhone 15' <runtime-id>")
                        
                        response = input("Continue anyway? (y/n): ").strip().lower()
                        if response != 'y':
                            return
                    
                except subprocess.CalledProcessError as download_error:
                    print(f"Failed to download iOS platform automatically: {download_error}")
                    print("Falling back to manual installation:")
                    print("1. Open Xcode > Settings > Platforms")
                    print("2. Download an iOS Simulator runtime")
                    print("Note: This requires several GB of download space")
                    
                    response = input("Continue anyway? (y/n): ").strip().lower()
                    if response != 'y':
                        return
            else:
                print("Skipping simulator installation.")
                print("You can install simulators later through:")
                print("1. Open Xcode > Settings > Platforms")
                print("2. Download an iOS Simulator runtime")
        else:
            print(f"Found {len(ios_simulators)} iOS simulator(s)")
            
    except subprocess.CalledProcessError:
        print("Could not check for simulators. Continuing anyway...")
    except Exception as e:
        print(f"Error checking simulators: {e}. Continuing anyway...")
        
    # Shutdown any running simulators to avoid boot conflicts
    print("Preparing simulators...")
    try:
        subprocess.run(['xcrun', 'simctl', 'shutdown', 'all'], 
                      capture_output=True, check=True)
        print("Shutdown any running simulators to prevent boot conflicts")
    except subprocess.CalledProcessError:
        # Failing to shutdown isn't critical
        pass
        
    # Run iOS using npx to ensure we use the locally installed CLI
    try:
        print("Starting iOS simulator...")
        
        # Use Node.js 18 environment if available
        if node18_env:
            print("Using Node.js 18 environment for React Native build")
            subprocess.run(["npx", "react-native", "run-ios"], cwd=BUILD_DIR, check=True, env=node18_env)
        else:
            print("Using system Node.js version")
            subprocess.run(["npx", "react-native", "run-ios"], cwd=BUILD_DIR, check=True)
            
    except subprocess.CalledProcessError as e:
        print("Failed to start iOS simulator.")
        
        # Check for specific error patterns and provide targeted advice
        error_output = str(e)
        if "DVTDownloads.framework" in error_output or "IDESimulatorFoundation" in error_output:
            print("\nXcode Framework Issue Detected:")
            print("Run this command to fix Xcode components:")
            print("sudo xcodebuild -runFirstLaunch")
            print("Then try 'onramp ios' again.")
        elif "styleText is not a function" in error_output:
            print("\nNode.js Compatibility Issue:")
            print("This error occurs with newer Node.js versions.")
            if node18_env:
                print("The CLI attempted to configure Node.js 18 but the error persists.")
            print("Solutions:")
            print("1. Install Node.js 18 LTS: nvm install 18 && nvm use 18")
            print("2. Update React Native CLI: npm install -g @react-native-community/cli@latest")
            print("3. Clear npm cache: npm cache clean --force")
        elif "Library not loaded" in error_output:
            print("\nXcode Installation Issue:")
            print("Try reinstalling Xcode Command Line Tools:")
            print("sudo xcode-select --install")
            print("Or reinstall Xcode completely from the App Store.")
        else:
            print("\nTroubleshooting steps:")
            print("1. Run: sudo xcodebuild -runFirstLaunch")
            print("2. Switch to Node.js 18 LTS: nvm use 18")
            print("3. Make sure iOS Simulator is installed with Xcode")
            print("4. Try running: 'npx react-native run-ios' manually in the build directory")
            print("5. Or open build/ios/YourApp.xcworkspace in Xcode and run from there")
            print("6. Check React Native environment setup: https://reactnative.dev/docs/set-up-your-environment")

def run_android():
    """Run Android emulator."""
    if not os.path.exists(BUILD_DIR):
        print("Build directory not found. Run 'onramp new <name>' first.")
        return
        
    print("Preparing Android development...")

    # FIRST: Ensure shell is using Node.js 18 (this may restart the CLI)
    ensure_shell_nodejs18()
    
    # Set up Node.js 18 environment (add this line)
    node18_env = setup_nodejs18_env()
    
    # Ensure native projects exist (pass the environment)
    if not ensure_native_projects(node18_env):
        print("Failed to set up Android project.")
        return
        
    # Run Android (use the environment)
    try:
        if node18_env:
            print("Using Node.js 18 environment for Android build")
            subprocess.run(["npm", "run", "android"], cwd=BUILD_DIR, check=True, env=node18_env)
        else:
            print("Using system Node.js version")
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