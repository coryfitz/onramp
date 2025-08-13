#!/usr/bin/env python3
"""
React Native + React Strict DOM App Generator

This script creates a React Native app with React Strict DOM using Metro bundler.
Metro should handle React Strict DOM's babel preset better than Vite.
"""

import os
import subprocess
import json
import argparse
from pathlib import Path

def run_command(command, cwd=None, check=True):
    """Run a shell command and return the result."""
    print(f"Running: {command}")
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            check=check,
            capture_output=True,
            text=True
        )
        if result.stdout:
            print(result.stdout)
        return result
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {e}")
        if e.stderr:
            print(f"Error output: {e.stderr}")
        raise

def create_package_json(app_name, project_dir):
    """Create package.json with React Native + React Strict DOM dependencies."""
    package_json = {
        "name": app_name,
        "version": "0.0.1",
        "private": True,
        "scripts": {
            "android": "npx react-native run-android",
            "ios": "npx react-native run-ios", 
            "start": "npx react-native start",
            "web": "npx react-native-web-cli start",
            "build:web": "npx react-native-web-cli build",
            "test": "jest",
            "lint": "eslint ."
        },
        "dependencies": {
            "react": "^19.1.0",
            "react-native": "^0.78.1",
            "react-strict-dom": "^0.0.28",
            "react-native-web": "^0.19.0"
        },
        "devDependencies": {
            "@babel/core": "^7.20.0",
            "@babel/preset-env": "^7.20.0", 
            "@babel/runtime": "^7.20.0",
            "@react-native/babel-preset": "^0.81.0",
            "@react-native/eslint-config": "^0.78.1",
            "@react-native/metro-config": "^0.78.1",
            "@react-native-community/cli": "latest",
            "babel-jest": "^29.2.1",
            "eslint": "^8.19.0",
            "jest": "^29.2.1",
            "react-native/babel-preset": "^0.81.0",
            "prettier": "^2.4.1",
            "react-test-renderer": "^19.1.0"
        },
        "jest": {
            "preset": "react-native"
        }
    }
    
    with open(project_dir / "package.json", "w") as f:
        json.dump(package_json, f, indent=2)

def create_babel_config(project_dir):
    """Create Babel configuration for React Native + React Strict DOM."""
    babel_config = """module.exports = {
  presets: [
    'module:react-native/babel-preset',
    ['react-strict-dom/babel-preset', {
      rootDir: __dirname
    }]
  ]
};
"""
    
    with open(project_dir / "babel.config.js", "w") as f:
        f.write(babel_config)

def create_metro_config(project_dir):
    """Create Metro configuration for React Native."""
    metro_config = """const {getDefaultConfig} = require('@react-native/metro-config');

/**
 * Metro configuration
 * https://facebook.github.io/metro/docs/configuration
 */
const config = getDefaultConfig(__dirname);

module.exports = config;
"""
    
    with open(project_dir / "metro.config.js", "w") as f:
        f.write(metro_config)

def create_app_component(project_dir):
    """Create the main App component using React Strict DOM."""
    app_component = '''import React, { useState } from 'react';
import { html } from 'react-strict-dom';

function App() {
  const [count, setCount] = useState(0);

  return (
    <html.div style={{
      flex: 1,
      padding: 20,
      fontFamily: 'system-ui, -apple-system, sans-serif',
      backgroundColor: '#f5f5f5',
      justifyContent: 'center',
      alignItems: 'center'
    }}>
      <html.div style={{
        backgroundColor: 'white',
        padding: 30,
        borderRadius: 12,
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 2 },
        shadowOpacity: 0.1,
        shadowRadius: 8,
        elevation: 5,
        maxWidth: 500,
        width: '100%'
      }}>
        <html.h1 style={{
          fontSize: 24,
          color: '#333',
          marginBottom: 16,
          fontWeight: 'bold',
          textAlign: 'center'
        }}>
          🚀 React Native + React Strict DOM!
        </html.h1>
        
        <html.p style={{
          fontSize: 16,
          marginBottom: 20,
          color: '#666',
          textAlign: 'center',
          lineHeight: 1.5
        }}>
          Universal components that work on native and web platforms!
        </html.p>
        
        <html.div style={{
          marginBottom: 20,
          alignItems: 'center'
        }}>
          <html.p style={{
            fontSize: 20,
            marginBottom: 15,
            fontWeight: 'bold',
            textAlign: 'center'
          }}>
            Counter: {count}
          </html.p>
          
          <html.div style={{
            flexDirection: 'row',
            justifyContent: 'center',
            gap: 10
          }}>
            <html.button
              onClick={() => setCount(count + 1)}
              style={{
                backgroundColor: '#007AFF',
                color: 'white',
                border: 'none',
                padding: '12px 24px',
                borderRadius: 8,
                fontSize: 16,
                fontWeight: '600',
                cursor: 'pointer'
              }}
            >
              ➕ Increment
            </html.button>
            
            <html.button
              onClick={() => setCount(0)}
              style={{
                backgroundColor: '#FF3B30',
                color: 'white',
                border: 'none',
                padding: '12px 24px',
                borderRadius: 8,
                fontSize: 16,
                fontWeight: '600',
                cursor: 'pointer'
              }}
            >
              🔄 Reset
            </html.button>
          </html.div>
        </html.div>
        
        <html.div style={{
          padding: 15,
          backgroundColor: '#e8f5e8',
          borderRadius: 6,
          borderWidth: 1,
          borderColor: '#c3e6cb',
          borderStyle: 'solid'
        }}>
          <html.p style={{
            margin: 0,
            fontSize: 14,
            color: '#155724',
            textAlign: 'center'
          }}>
            ✅ React Strict DOM working on React Native!{`\\n`}
            Perfect for your Tailwind → StyleX plugin.
          </html.p>
        </html.div>
        
        <html.div style={{
          marginTop: 20,
          padding: 15,
          backgroundColor: '#f8f9fa',
          borderRadius: 6
        }}>
          <html.p style={{
            margin: 0,
            fontSize: 12,
            color: '#666',
            textAlign: 'center'
          }}>
            💡 Using React Strict DOM with Metro bundler{`\\n`}
            • html.div, html.button work on native & web{`\\n`}
            • Same styling API across platforms{`\\n`}
            • Ready for Tailwind → StyleX transformations
          </html.p>
        </html.div>
      </html.div>
    </html.div>
  );
}

export default App;
'''
    
    with open(project_dir / "App.jsx", "w") as f:
        f.write(app_component)

def create_index_files(project_dir, app_name):
    """Create index files for React Native."""
    
    # Main React Native index
    rn_index = f'''import {{ AppRegistry }} from 'react-native';
import App from './App';
import {{ name as appName }} from './app.json';

AppRegistry.registerComponent(appName, () => App);
'''
    
    with open(project_dir / "index.js", "w") as f:
        f.write(rn_index)

def create_app_json(project_dir, app_name):
    """Create app.json for React Native."""
    app_json = {
        "name": app_name,
        "displayName": app_name,
        "expo": {
            "web": {
                "bundler": "metro"
            }
        }
    }
    
    with open(project_dir / "app.json", "w") as f:
        json.dump(app_json, f, indent=2)

def create_watchmanconfig(project_dir):
    """Create .watchmanconfig file for React Native."""
    watchman_config = "{}\n"
    
    with open(project_dir / ".watchmanconfig", "w") as f:
        f.write(watchman_config)

def create_readme(project_dir, app_name):
    """Create README with instructions."""
    readme_content = f'''# {app_name}

React Native app with React Strict DOM using Metro bundler.

## Features

- 🚀 **React Strict DOM**: Universal components for native and web
- 📱 **React Native**: Cross-platform mobile development
- ⚡ **Metro**: React Native's optimized bundler
- 🎯 **Plugin Ready**: Perfect for Tailwind → StyleX conversion

## Development

### Native Development
```bash
npm run android    # Run on Android
npm run ios        # Run on iOS (macOS only)
npm start          # Start Metro bundler
```

### Web Development  
```bash
npm run web        # Run on web (experimental)
```

## Project Structure

```
{app_name}/frontend/
├── App.jsx               # Main app component (React Strict DOM)
├── index.js              # React Native entry point
├── babel.config.js       # Babel + React Strict DOM preset
├── metro.config.js       # Metro bundler configuration
├── package.json          # Dependencies and scripts
└── app.json             # React Native config
```

## Adding Your Tailwind → StyleX Plugin

This project uses React Strict DOM which internally uses StyleX. Perfect for your plugin:

```javascript
// babel.config.js
module.exports = {{
  presets: [
    'module:react-native/babel-preset',
    ['react-strict-dom/babel-preset', {{
      rootDir: __dirname
    }}],
    ['your-tailwind-to-stylex-plugin', {{
      // Your plugin options
    }}]
  ]
}};
```

## React Strict DOM Benefits

- **Universal API**: `html.div`, `html.button` work on native & web
- **Consistent Styling**: Same style props across platforms  
- **Performance**: Optimized rendering with StyleX under the hood
- **Future-Proof**: Built for the next generation of React

## Setup Requirements

### For Android:
- Android Studio
- Android SDK
- Java Development Kit (JDK)

### For iOS (macOS only):
- Xcode
- iOS Simulator
- CocoaPods: `sudo gem install cocoapods`

Happy coding! 🎉
'''
    
    with open(project_dir / "README.md", "w") as f:
        f.write(readme_content)

def create_react_native_app(app_name, output_dir="."):
    """Create a React Native app with React Strict DOM using Metro."""
    
    print(f"Creating React Native + React Strict DOM app: {app_name}")
    
    # Create project directory with frontend subfolder
    project_dir = Path(output_dir) / app_name / "frontend"
    project_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Creating project in: {project_dir}")
    
    # Create all configuration files
    create_package_json(app_name, project_dir)
    create_babel_config(project_dir)
    create_metro_config(project_dir)
    create_app_json(project_dir, app_name)
    create_watchmanconfig(project_dir)
    create_readme(project_dir, app_name)
    
    # Create source files
    create_app_component(project_dir)
    create_index_files(project_dir, app_name)
    
    print(f"✅ Project structure created successfully!")
    
    # Install dependencies with multiple fallback strategies
    print("Installing dependencies...")
    install_success = False
    
    # Try different installation strategies for React Native + React Strict DOM
    strategies = [
        "npm install --legacy-peer-deps",
        "npm install react@19.1.0 react-native@0.78.1 react-strict-dom@latest --legacy-peer-deps && npm install --legacy-peer-deps",
        "npm install --force"
    ]
    
    for i, strategy in enumerate(strategies, 1):
        try:
            print(f"Trying installation strategy {i}/{len(strategies)}: {strategy}")
            run_command(strategy, cwd=project_dir)
            install_success = True
            print("✅ Dependencies installed successfully!")
            break
        except subprocess.CalledProcessError as e:
            print(f"Strategy {i} failed: {e}")
            if i < len(strategies):
                print(f"Trying next strategy...")
            continue
    
    if not install_success:
        print("⚠️  All installation strategies failed. Please try manually:")
        print("   cd " + app_name + "/frontend")
        print("   npm install react@19.1.0 react-native@0.78.1 --legacy-peer-deps")
        print("   npm install react-strict-dom@latest --legacy-peer-deps")
        print("   npm install --legacy-peer-deps")
    
    print(f"""
🎉 React Native + React Strict DOM app created successfully!

Next steps:
1. cd {app_name}/frontend
2. npm start                      # Start Metro bundler
3. npm run android               # Run on Android (requires Android Studio)
4. npm run ios                   # Run on iOS (requires Xcode on macOS)

Features:
✅ React Native for cross-platform mobile development
✅ React Strict DOM for universal components
✅ Metro bundler (optimized for React Native)
✅ Perfect foundation for your Tailwind → StyleX plugin

Setup Requirements:
📱 Android: Android Studio + Android SDK + JDK
🍎 iOS: Xcode + iOS Simulator + CocoaPods (macOS only)

The app uses html.div, html.button elements that work on both native and web!

Troubleshooting:
- For React 19 conflicts: npm install --legacy-peer-deps
- For native setup: Follow React Native environment setup guide
- For iOS: Run 'cd ios && pod install' after npm install
""")

def main():
    parser = argparse.ArgumentParser(
        description="Create a React Native app with React Strict DOM"
    )
    parser.add_argument(
        "app_name",
        help="Name of the React Native app to create"
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default=".",
        help="Output directory for the project (default: current directory)"
    )
    
    args = parser.parse_args()
    
    # Validate app name
    if not args.app_name.replace("-", "").replace("_", "").isalnum():
        print("Error: App name should only contain letters, numbers, hyphens, and underscores")
        return 1
    
    try:
        create_react_native_app(args.app_name, args.output_dir)
        return 0
    except Exception as e:
        print(f"Error creating app: {e}")
        return 1

if __name__ == "__main__":
    exit(main())