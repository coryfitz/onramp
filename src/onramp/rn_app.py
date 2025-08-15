#!/usr/bin/env python3
"""
React Native + React Strict DOM App Generator

This script creates a React Native app with React Strict DOM using Metro bundler.
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
        result = subprocess.run(command, shell=True, cwd=cwd, check=check, capture_output=True, text=True)
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
            "web": "npx react-native start --port 8081",
            "test": "jest",
            "lint": "eslint ."
        },
        "dependencies": {
            "react": "^18.2.0",
            "react-dom": "^18.2.0",
            "react-native": "^0.75.0",
            "react-strict-dom": "^0.0.44",
            "@stylexjs/stylex": "^0.8.0"
        },
        "devDependencies": {
            "@babel/core": "^7.20.0",
            "@babel/runtime": "^7.20.0",
            "@react-native/babel-preset": "^0.75.0",
            "@react-native/metro-config": "^0.75.0",
            "@stylexjs/babel-plugin": "^0.8.0",
            "metro-react-native-babel-preset": "^0.77.0",
            "prettier": "^2.4.1"
        },
        "jest": {
            "preset": "react-native"
        }
    }
    
    with open(project_dir / "package.json", "w") as f:
        json.dump(package_json, f, indent=2)

def create_babel_config(project_dir):
    """Create Babel configuration for React Native + React Strict DOM."""
    babel_config = """const reactStrictPreset = require('react-strict-dom/babel-preset');

function getPlatform(caller) {
  return caller && caller.platform;
}

function getIsDev(caller) {
  if (caller?.isDev != null) return caller.isDev;
  return (
    process.env.BABEL_ENV === 'development' ||
    process.env.NODE_ENV === 'development'
  );
}

module.exports = function (api) {
  const platform = api.caller(getPlatform);
  const dev = api.caller(getIsDev);

  return {
    presets: [
      'module:metro-react-native-babel-preset',
      [reactStrictPreset, {
        debug: dev,
        dev,
        platform,
        rootDir: __dirname
      }]
    ]
  };
};
"""
    with open(project_dir / "babel.config.js", "w") as f:
        f.write(babel_config)

def create_metro_config(project_dir):
    """Create Metro configuration for React Native with React Strict DOM support."""
    metro_config = """const {getDefaultConfig, mergeConfig} = require('@react-native/metro-config');

const defaultConfig = getDefaultConfig(__dirname);

const config = {
  resolver: {
    platforms: ['ios', 'android', 'native', 'web'],
    unstable_enablePackageExports: true,
  },
};

module.exports = mergeConfig(defaultConfig, config);
"""
    with open(project_dir / "metro.config.js", "w") as f:
        f.write(metro_config)

def create_app_component(project_dir):
    """Create the main App component using React Strict DOM."""
    app_component = '''import React, { useState } from 'react';
import { html } from 'react-strict-dom';

export default function App() {
  const [count, setCount] = useState(0);

  return (
    <html.div style={{
      minHeight: '100vh',
      width: '100%',
      display: 'flex',
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
        boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
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
          Welcome to the OnRamp App Framework
        </html.h1>

        <html.img style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center'
        }}src="static/logo.png" alt="OnRamp Logo"></html.img>

        <html.a style={{
          fontSize: 24,
          color: '#333',
          marginBottom: 16,
          fontWeight: 'bold',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center'
        }}href="https://onrampframework.com" target="_blank">Learn OnRamp</html.a>
        
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
          display: 'flex',
          flexDirection: 'column',
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
            display: 'flex',
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
          border: '1px solid #c3e6cb'
        }}>
          <html.p style={{
            margin: 0,
            fontSize: 14,
            color: '#155724',
            textAlign: 'center'
          }}>
            ✅ React Strict DOM working on React Native!
          </html.p>
        </html.div>
      </html.div>
    </html.div>
  );
}
'''
    with open(project_dir / "App.jsx", "w") as f:
        f.write(app_component)

def create_index_files(project_dir, app_name):
    """Create index files for React Native."""
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
        "displayName": app_name
    }
    with open(project_dir / "app.json", "w") as f:
        json.dump(app_json, f, indent=2)

def create_web_files(project_dir):
    """Create web-specific files for React Strict DOM."""
    public_dir = project_dir / "public"
    public_dir.mkdir(exist_ok=True)
    
    html_content = """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no" />
    <title>React Native + React Strict DOM</title>
  </head>
  <body>
    <div id="react-strict-dom-root"></div>
    <script src="/index.bundle?platform=web&dev=true"></script>
  </body>
</html>
"""
    with open(public_dir / "index.html", "w") as f:
        f.write(html_content)
    
    web_entry = """import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';

const root = createRoot(document.getElementById('react-strict-dom-root'));
root.render(<App />);
"""
    with open(project_dir / "index.web.js", "w") as f:
        f.write(web_entry)

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

```bash
npm run android    # Run on Android
npm run ios        # Run on iOS (macOS only)
npm start          # Start Metro bundler
npm run web        # Run on web
```

## Setup Requirements

### For Android:
- Android Studio + Android SDK + JDK

### For iOS (macOS only):
- Xcode + iOS Simulator + CocoaPods

Happy coding! 🎉
'''
    with open(project_dir / "README.md", "w") as f:
        f.write(readme_content)

def create_react_native_app(app_name, output_dir="."):
    """Create a React Native app with React Strict DOM using Metro."""
    print(f"Creating React Native + React Strict DOM app: {app_name}")
    
    project_dir = Path(output_dir) / app_name / "frontend"
    project_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Creating project in: {project_dir}")
    
    # Create all files
    create_package_json(app_name, project_dir)
    create_babel_config(project_dir)
    create_metro_config(project_dir)
    create_app_json(project_dir, app_name)
    create_web_files(project_dir)
    create_readme(project_dir, app_name)
    create_app_component(project_dir)
    create_index_files(project_dir, app_name)
    
    print(f"✅ Project structure created successfully!")
    
    # Install dependencies
    print("Installing dependencies...")
    try:
        run_command("npm install --legacy-peer-deps", cwd=project_dir)
        print("✅ Dependencies installed successfully!")
    except subprocess.CalledProcessError:
        print("⚠️  Installation failed. Please run manually:")
        print(f"   cd {app_name}/frontend && npm install --legacy-peer-deps")
    
    print(f"""
🎉 React Native + React Strict DOM app created successfully!

Next steps:
1. cd {app_name}/frontend
2. npm start                      # Start Metro bundler
3. npm run android               # Run on Android
4. npm run ios                   # Run on iOS

The app uses html.div, html.button elements that work on both native and web!
""")

def main():
    parser = argparse.ArgumentParser(description="Create a React Native app with React Strict DOM")
    parser.add_argument("app_name", help="Name of the React Native app to create")
    parser.add_argument("--output-dir", "-o", default=".", help="Output directory for the project")
    
    args = parser.parse_args()
    
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