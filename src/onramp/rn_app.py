#!/usr/bin/env python3
"""
React Native + React Strict DOM App Generator

This script creates a React Native app with React Strict DOM as the representation layer.
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
    """Create package.json with React Strict DOM dependencies."""
    package_json = {
        "name": app_name,
        "version": "0.0.1",
        "private": True,
        "scripts": {
            "android": "react-native run-android",
            "ios": "react-native run-ios",
            "lint": "eslint .",
            "start": "react-native start",
            "test": "jest",
            "web": "webpack serve --config webpack.config.js"
        },
        "dependencies": {
            "react": "^18.2.0",
            "react-native": "^0.72.0",
            "react-strict-dom": "^0.0.28",
            "react-native-web": "^0.19.0"
        },
        "devDependencies": {
            "@babel/core": "^7.20.0",
            "@babel/preset-env": "^7.20.0",
            "@babel/runtime": "^7.20.0",
            "@react-native/eslint-config": "^0.72.0",
            "@react-native/metro-config": "^0.72.0",
            "@tsconfig/react-native": "^3.0.0",
            "@types/react": "^18.0.24",
            "@types/react-test-renderer": "^18.0.0",
            "babel-jest": "^29.2.1",
            "eslint": "^8.19.0",
            "jest": "^29.2.1",
            "metro-react-native-babel-preset": "^0.76.0",
            "prettier": "^2.4.1",
            "react-test-renderer": "^18.2.0",
            "typescript": "^4.8.4",
            "webpack": "^5.0.0",
            "webpack-cli": "^5.0.0",
            "webpack-dev-server": "^4.0.0",
            "html-webpack-plugin": "^5.0.0"
        },
        "jest": {
            "preset": "react-native"
        }
    }
    
    with open(project_dir / "package.json", "w") as f:
        json.dump(package_json, f, indent=2)

def create_babel_config(project_dir):
    """Create Babel configuration for React Strict DOM."""
    babel_config = {
        "presets": ["module:metro-react-native-babel-preset"],
        "plugins": [
            ["react-strict-dom/babel-plugin"]
        ]
    }
    
    with open(project_dir / "babel.config.js", "w") as f:
        f.write(f"module.exports = {json.dumps(babel_config, indent=2)};")

def create_metro_config(project_dir):
    """Create Metro configuration for React Native."""
    metro_config = """const {getDefaultConfig, mergeConfig} = require('@react-native/metro-config');

/**
 * Metro configuration
 * https://facebook.github.io/metro/docs/configuration
 */
const config = {
  resolver: {
    alias: {
      'react-native': 'react-native-web',
    },
  },
};

module.exports = mergeConfig(getDefaultConfig(__dirname), config);
"""
    
    with open(project_dir / "metro.config.js", "w") as f:
        f.write(metro_config)

def create_webpack_config(project_dir):
    """Create Webpack configuration for web support."""
    webpack_config = """const path = require('path');
const HtmlWebpackPlugin = require('html-webpack-plugin');

module.exports = {
  entry: './index.web.js',
  mode: 'development',
  devServer: {
    static: {
      directory: path.join(__dirname, 'public'),
    },
    compress: true,
    port: 3000,
  },
  module: {
    rules: [
      {
        test: /\\.(js|jsx|ts|tsx)$/,
        exclude: /node_modules/,
        use: {
          loader: 'babel-loader',
        },
      },
    ],
  },
  resolve: {
    extensions: ['.js', '.jsx', '.ts', '.tsx'],
    alias: {
      'react-native$': 'react-native-web',
    },
  },
  plugins: [
    new HtmlWebpackPlugin({
      template: './public/index.html',
    }),
  ],
};
"""
    
    with open(project_dir / "webpack.config.js", "w") as f:
        f.write(webpack_config)

def create_app_component(project_dir):
    """Create the main App component using React Strict DOM."""
    app_component = '''import React from 'react';
import {html} from 'react-strict-dom';

function App() {
  return (
    <html.div
      style={{
        flex: 1,
        justifyContent: 'center',
        alignItems: 'center',
        backgroundColor: '#f5f5f5',
        padding: 20,
      }}
    >
      <html.div
        style={{
          backgroundColor: 'white',
          padding: 30,
          borderRadius: 10,
          shadowColor: '#000',
          shadowOffset: {width: 0, height: 2},
          shadowOpacity: 0.1,
          shadowRadius: 8,
          elevation: 5,
          maxWidth: 400,
          width: '100%',
        }}
      >
        <html.h1
          style={{
            fontSize: 24,
            fontWeight: 'bold',
            textAlign: 'center',
            marginBottom: 20,
            color: '#333',
          }}
        >
          Welcome to React Native + React Strict DOM!
        </html.h1>
        
        <html.p
          style={{
            fontSize: 16,
            textAlign: 'center',
            color: '#666',
            lineHeight: 1.5,
            marginBottom: 20,
          }}
        >
          This app uses React Strict DOM as the representation layer,
          providing a unified API across web and native platforms.
        </html.p>
        
        <html.button
          style={{
            backgroundColor: '#007AFF',
            color: 'white',
            padding: 12,
            borderRadius: 8,
            border: 'none',
            fontSize: 16,
            fontWeight: '600',
            cursor: 'pointer',
            width: '100%',
          }}
          onClick={() => {
            console.log('Button pressed!');
            // Add your button logic here
          }}
        >
          Get Started
        </html.button>
      </html.div>
    </html.div>
  );
}

export default App;
'''
    
    with open(project_dir / "App.js", "w") as f:
        f.write(app_component)

def create_index_files(project_dir, app_name):
    """Create index files for React Native and web."""
    
    # React Native index
    rn_index = f'''import {{AppRegistry}} from 'react-native';
import App from './App';
import {{name as appName}} from './app.json';

AppRegistry.registerComponent(appName, () => App);
'''
    
    with open(project_dir / "index.js", "w") as f:
        f.write(rn_index)
    
    # Web index
    web_index = '''import React from 'react';
import {createRoot} from 'react-dom/client';
import App from './App';

const container = document.getElementById('root');
const root = createRoot(container);
root.render(<App />);
'''
    
    with open(project_dir / "index.web.js", "w") as f:
        f.write(web_index)

def create_html_template(project_dir):
    """Create HTML template for web version."""
    os.makedirs(project_dir / "public", exist_ok=True)
    
    html_template = '''<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>React Native + React Strict DOM</title>
  </head>
  <body>
    <noscript>You need to enable JavaScript to run this app.</noscript>
    <div id="root"></div>
  </body>
</html>
'''
    
    with open(project_dir / "public" / "index.html", "w") as f:
        f.write(html_template)

def create_app_json(project_dir, app_name):
    """Create app.json for React Native."""
    app_json = {
        "name": app_name,
        "displayName": app_name
    }
    
    with open(project_dir / "app.json", "w") as f:
        json.dump(app_json, f, indent=2)

def create_react_native_app(app_name, output_dir="."):
    """Create a complete React Native app with React Strict DOM."""
    
    print(f"Creating React Native app with React Strict DOM: {app_name}")
    
    # Create project directory
    project_dir = Path(output_dir) / app_name
    project_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Creating project in: {project_dir}")
    
    # Create all configuration files
    create_package_json(app_name, project_dir)
    create_babel_config(project_dir)
    create_metro_config(project_dir)
    create_webpack_config(project_dir)
    create_app_json(project_dir, app_name)
    
    # Create source files
    create_app_component(project_dir)
    create_index_files(project_dir, app_name)
    create_html_template(project_dir)
    
    print(f"✅ Project structure created successfully!")
    
    # Install dependencies
    print("Installing dependencies...")
    try:
        run_command("npm install", cwd=project_dir)
        print("✅ Dependencies installed successfully!")
    except subprocess.CalledProcessError:
        print("⚠️  Failed to install dependencies. Run 'npm install' manually in the project directory.")
    
    # Initialize React Native (for native components)
    try:
        print("Initializing React Native...")
        run_command("npx react-native init --skip-install", cwd=project_dir)
    except subprocess.CalledProcessError:
        print("⚠️  Could not initialize React Native. You may need to run this manually.")
    
    print(f"""
React Native app with React Strict DOM created successfully

Next steps:
1. cd {app_name}
2. For web development:
   npm run web
3. For Android:
   npm run android
4. For iOS:
   npm run ios

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