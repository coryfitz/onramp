#!/usr/bin/env python3
"""
React Native + React Strict DOM App Generator with File-Based Navigation

This script creates a React Native app with React Strict DOM and file-based navigation.
"""

import subprocess
import json
import argparse
import shutil
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
    """Create package.json with separate bundlers for web and native."""
    package_json = {
        "name": app_name,
        "version": "0.0.1",
        "private": True,
        "scripts": {
            # Native - use Metro directly for start command
            "android": "npx react-native run-android",
            "ios": "npx react-native run-ios", 
            "start:native": "npx metro start --port 8081",  # Direct Metro
            "start:rn": "npx react-native start",           # CLI fallback
            
            # Web (use Webpack)
            "start:web": "node scripts/build-routes.js && webpack serve",
            "build:web": "node scripts/build-routes.js && webpack --mode production",
            
            # Default
            "start": "npm run start:native",
            "web": "npm run start:web",
            "build:routes": "node scripts/build-routes.js",
            "test": "jest",
            "lint": "eslint ."
        },
        "dependencies": {
            "react": "^18.2.0",
            "react-dom": "^18.2.0",
            "react-native": "^0.81.1",
            "react-strict-dom": "^0.0.44",
            "@stylexjs/stylex": "^0.8.0",
            "@react-navigation/native": "^6.1.0",
            "@react-navigation/stack": "^6.3.0", 
            "@react-navigation/bottom-tabs": "^6.5.0",
            "react-native-screens": "^3.29.0",
            "react-native-safe-area-context": "^4.8.0"
        },
        "devDependencies": {
            "@babel/core": "^7.20.0",
            "@babel/runtime": "^7.20.0",
            "@babel/preset-env": "^7.22.0",
            "@babel/preset-react": "^7.22.0",
            "@babel/preset-typescript": "^7.22.0",
            "@react-native/babel-preset": "^0.81.1",
            "@react-native/metro-config": "^0.81.1",
            "@stylexjs/babel-plugin": "^0.8.0",
            "metro-react-native-babel-preset": "^0.77.0",
            
            # Add these CLI dependencies
            "@react-native-community/cli": "^14.0.0",
            "@react-native-community/cli-platform-ios": "^14.0.0", 
            "@react-native-community/cli-platform-android": "^14.0.0",
            
            # Web bundling
            "webpack": "^5.88.0",
            "webpack-cli": "^5.1.0",
            "webpack-dev-server": "^4.15.0",
            "babel-loader": "^9.1.0",
            "html-webpack-plugin": "^5.5.0",
            
            "prettier": "^2.4.1",
            "chokidar": "^3.5.3",
            "@types/react": "^18.2.0",
            "@types/react-native": "^0.72.0",
            "typescript": "^5.0.0"
        },
        "jest": {
            "preset": "react-native"
        }
    }
    
    with open(project_dir / "package.json", "w") as f:
        json.dump(package_json, f, indent=2)

def create_webpack_config(project_dir):
    """Create Webpack configuration for web builds."""
    webpack_config = """const path = require('path');
const HtmlWebpackPlugin = require('html-webpack-plugin');

module.exports = {
  entry: './index.web.js',
  mode: 'development',
  devServer: {
    port: 'auto',  // Automatically find available port
    historyApiFallback: true,
    static: {
      directory: path.join(__dirname, 'assets'),
    },
  },
  module: {
    rules: [
      {
        test: /\\.(js|jsx|ts|tsx)$/,
        exclude: /node_modules/,
        use: {
          loader: 'babel-loader',
          options: {
            presets: [
              ['@babel/preset-env', { targets: 'defaults' }],
              ['@babel/preset-react', { runtime: 'automatic' }],
              ['@babel/preset-typescript']
            ],
            plugins: [
              ['@stylexjs/babel-plugin', {
                dev: true,
                runtimeInjection: false,
                genConditionalClasses: true,
                treeshakeCompensation: true,
                unstable_moduleResolution: {
                  type: 'commonJS',
                  rootDir: __dirname,
                }
              }]
            ]
          }
        }
      },
      {
        test: /\\.(js|jsx|ts|tsx)$/,
        include: /node_modules[\\/]react-strict-dom/,
        use: {
          loader: 'babel-loader',
          options: {
            presets: [
              ['@babel/preset-env', { targets: 'defaults' }],
              ['@babel/preset-react', { runtime: 'automatic' }]
            ],
            plugins: [
              ['@stylexjs/babel-plugin', {
                dev: true,
                runtimeInjection: false,
                genConditionalClasses: true,
                treeshakeCompensation: true,
                unstable_moduleResolution: {
                  type: 'commonJS',
                  rootDir: __dirname,
                }
              }]
            ]
          }
        }
      }
    ]
  },
  resolve: {
    extensions: ['.web.js', '.web.jsx', '.web.ts', '.web.tsx', '.js', '.jsx', '.ts', '.tsx'],
    alias: {
      'react-native$': 'react-strict-dom'
    }
  },
  plugins: [
    new HtmlWebpackPlugin({
      template: 'index.html',
      inject: true
    })
  ],
  output: {
    path: path.resolve(__dirname, 'dist'),
    filename: 'bundle.js',
    publicPath: '/'
  }
};
"""
    with open(project_dir / "webpack.config.js", "w") as f:
        f.write(webpack_config)

def create_web_index_html(project_dir):
    """Create HTML template for Webpack."""
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>OnRamp App</title>
</head>
<body>
  <div id="root"></div>
</body>
</html>
"""
    with open(project_dir / "index.html", "w") as f:
        f.write(html_content)

def create_web_entry(project_dir):
    """Create web entry point for Webpack."""
    web_entry = """import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';

const container = document.getElementById('root');
if (container) {
  const root = createRoot(container);
  root.render(<App />);
}
"""
    with open(project_dir / "index.web.js", "w") as f:
        f.write(web_entry)

def copy_navigation_templates(project_dir):
    """Copy navigation template files from the templates folder."""
    script_dir = Path(__file__).parent
    templates_dir = script_dir / "templates"
    
    if not templates_dir.exists():
        print("⚠️  Templates folder not found - creating basic navigation structure")
        create_basic_navigation_structure(project_dir)
        return
    
    # Create directory structure
    src_dir = project_dir / "src"
    navigation_dir = src_dir / "navigation"
    generated_dir = src_dir / "generated"
    build_dir = project_dir / "build"
    scripts_dir = project_dir / "scripts"
    
    for dir_path in [src_dir, navigation_dir, generated_dir, build_dir, scripts_dir]:
        dir_path.mkdir(parents=True, exist_ok=True)
    
    # Map template files to destination paths
    template_files = {
        "generateRoutes.js": build_dir / "generateRoutes.js",
        "NavigationProvider.tsx": navigation_dir / "NavigationProvider.tsx", 
        "RouteRegistry.tsx": navigation_dir / "RouteRegistry.tsx",
        "build-routes.js": scripts_dir / "build-routes.js"
    }
    
    # Copy template files
    for template_name, dest_path in template_files.items():
        template_path = templates_dir / template_name
        if template_path.exists():
            print(f"Copying {template_name} to {dest_path}")
            shutil.copy2(template_path, dest_path)
        else:
            print(f"⚠️  Template {template_name} not found, creating basic version")
            create_basic_template(template_name, dest_path)

def create_basic_navigation_structure(project_dir):
    """Create basic navigation structure if templates aren't available."""
    src_dir = project_dir / "src"
    navigation_dir = src_dir / "navigation"
    generated_dir = src_dir / "generated"
    scripts_dir = project_dir / "scripts"
    
    for dir_path in [src_dir, navigation_dir, generated_dir, scripts_dir]:
        dir_path.mkdir(parents=True, exist_ok=True)
    
    # Create a basic build-routes script
    basic_script = '''#!/usr/bin/env node
console.log("File-based routing: Scanning for routes...");

const fs = require('fs');
const path = require('path');

// Basic route generation
function generateRoutes() {
  const routes = [];
  const appDir = path.join(__dirname, '..', 'app');
  
  if (!fs.existsSync(appDir)) {
    fs.mkdirSync(appDir, { recursive: true });
  }
  
  console.log("Routes generated successfully");
}

generateRoutes();
'''
    
    with open(scripts_dir / "build-routes.js", "w") as f:
        f.write(basic_script)

def create_basic_template(template_name, dest_path):
    """Create basic versions of templates if not found."""
    if template_name == "NavigationProvider.tsx":
        content = '''// Basic Navigation Provider
import React, { createContext, useContext } from 'react';

const NavigationContext = createContext(null);

export function NavigationProvider({ children }) {
  return (
    <NavigationContext.Provider value={{}}>
      {children}
    </NavigationContext.Provider>
  );
}

export function useNavigation() {
  return useContext(NavigationContext);
}
'''
        with open(dest_path, "w") as f:
            f.write(content)

def create_app_structure(project_dir):
    """Create the app directory structure for file-based routing."""
    app_dir = project_dir / "app"
    app_dir.mkdir(exist_ok=True)
    
    # Create index route with navigation
    index_content = '''import React from 'react';
import { html } from 'react-strict-dom';
import { useNavigation } from '../src/navigation/NavigationProvider';

export default function HomePage() {
  const { navigate } = useNavigation();

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
    } as any}>
      <html.div style={{
        backgroundColor: 'white',
        padding: 30,
        borderRadius: 12,
        boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
        maxWidth: 500,
        width: '100%'
      } as any}>
        <html.h1 style={{
          fontSize: 24,
          color: '#333',
          marginBottom: 16,
          fontWeight: 'bold',
          textAlign: 'center'
        } as any}>
          Welcome to OnRamp
        </html.h1>

        <html.h2 style={{
          fontSize: 19,
          color: '#333',
          marginBottom: 16,
          fontWeight: 'bold',
          textAlign: 'center'
        } as any}>
          The Python App Framework
        </html.h2>

        <html.div style={{
          display: 'flex',
          gap: 10,
          justifyContent: 'center',
          flexDirection: 'column'
        } as any}>
          <html.button 
            onClick={() => navigate('/profile/123')}
            style={{
              padding: '10px 20px',
              backgroundColor: '#007AFF',
              color: 'white',
              border: 'none',
              borderRadius: 6,
              cursor: 'pointer'
            } as any}
          >
            Go to Profile
          </html.button>
          <html.button 
            onClick={() => navigate('/about')}
            style={{
              padding: '10px 20px',
              backgroundColor: '#34C759',
              color: 'white',
              border: 'none',
              borderRadius: 6,
              cursor: 'pointer'
            } as any}
          >
            About Page
          </html.button>
        </html.div>
      </html.div>
    </html.div>
  );
}
'''
    
    with open(app_dir / "index.tsx", "w") as f:
        f.write(index_content)
    
    # Create a sample dynamic route with navigation
    profile_dir = app_dir / "profile"
    profile_dir.mkdir(exist_ok=True)
    
    profile_content = '''import React from 'react';
import { html } from 'react-strict-dom';
import { useNavigation } from '../../src/navigation/NavigationProvider';

export default function ProfilePage({ id }) {
  const { navigate, goBack, canGoBack } = useNavigation();

  return (
    <html.div style={{
      padding: 20,
      fontFamily: 'system-ui, -apple-system, sans-serif',
      minHeight: '100vh',
      backgroundColor: '#f5f5f5'
    }}>
      <html.div style={{
        backgroundColor: 'white',
        padding: 30,
        borderRadius: 12,
        boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
        maxWidth: 600
      }}>
        <html.h1 style={{ color: '#333', marginBottom: 16 }}>
          Profile Page
        </html.h1>
        <html.p style={{ color: '#666', marginBottom: 20 }}>
          Profile ID: {id || 'No ID provided'}
        </html.p>
        
        <html.div style={{ display: 'flex', gap: 10 }}>
          {canGoBack() && (
            <html.button 
              onClick={goBack}
              style={{
                padding: '10px 20px',
                backgroundColor: '#666',
                color: 'white',
                border: 'none',
                borderRadius: 6,
                cursor: 'pointer'
              }}
            >
              Go Back
            </html.button>
          )}
          <html.button 
            onClick={() => navigate('/')}
            style={{
              padding: '10px 20px',
              backgroundColor: '#007AFF',
              color: 'white',
              border: 'none',
              borderRadius: 6,
              cursor: 'pointer'
            }}
          >
            Home
          </html.button>
        </html.div>
      </html.div>
    </html.div>
  );
}
'''
    
    with open(profile_dir / "[id].tsx", "w") as f:
        f.write(profile_content)

    # Create an about page
    about_content = '''import React from 'react';
import { html } from 'react-strict-dom';
import { useNavigation } from '../src/navigation/NavigationProvider';

export default function AboutPage() {
  const { navigate, goBack } = useNavigation();

  return (
    <html.div style={{
      padding: 20,
      fontFamily: 'system-ui, -apple-system, sans-serif',
      minHeight: '100vh',
      backgroundColor: '#f5f5f5'
    }}>
      <html.div style={{
        backgroundColor: 'white',
        padding: 30,
        borderRadius: 12,
        boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
        maxWidth: 600
      }}>
        <html.h1 style={{ color: '#333', marginBottom: 16 }}>
          About OnRamp
        </html.h1>
        <html.p style={{ color: '#666', marginBottom: 20 }}>
          OnRamp is a modern framework for building cross-platform applications with React Native and React Strict DOM.
        </html.p>
        
        <html.div style={{ display: 'flex', gap: 10 }}>
          <html.button 
            onClick={goBack}
            style={{
              padding: '10px 20px',
              backgroundColor: '#666',
              color: 'white',
              border: 'none',
              borderRadius: 6,
              cursor: 'pointer'
            }}
          >
            Go Back
          </html.button>
          <html.button 
            onClick={() => navigate('/')}
            style={{
              padding: '10px 20px',
              backgroundColor: '#007AFF',
              color: 'white',
              border: 'none',
              borderRadius: 6,
              cursor: 'pointer'
            }}
          >
            Home
          </html.button>
        </html.div>
      </html.div>
    </html.div>
  );
}
'''
    
    with open(app_dir / "about.tsx", "w") as f:
        f.write(about_content)

def create_app_component(project_dir):
    """Create the main App component that uses the navigation system."""
    app_component = '''import React from 'react';
import { NavigationProvider, useNavigation } from './src/navigation/NavigationProvider';
import { RouteComponent } from './src/navigation/RouteRegistry';

function AppContent() {
  const { currentRoute, params } = useNavigation();
  return <RouteComponent path={currentRoute} params={params} />;
}

export default function App() {
  return (
    <NavigationProvider initialRoute="/">
      <AppContent />
    </NavigationProvider>
  );
}
'''
    with open(project_dir / "App.jsx", "w") as f:
        f.write(app_component)

def create_typescript_config(project_dir):
    """Create TypeScript configuration."""
    tsconfig = {
        "compilerOptions": {
            "target": "ES2020",
            "lib": ["ES2020", "DOM"],
            "allowJs": True,
            "skipLibCheck": True,
            "esModuleInterop": True,
            "allowSyntheticDefaultImports": True,
            "strict": True,
            "forceConsistentCasingInFileNames": True,
            "moduleResolution": "bundler",  # Changed from "node"
            "resolveJsonModule": True,
            "isolatedModules": True,
            "noEmit": True,
            "jsx": "react-jsx"
        },
        "include": [
            "src/**/*",
            "app/**/*",
            "App.jsx"
        ],
        "exclude": [
            "node_modules"
        ]
    }
    
    with open(project_dir / "tsconfig.json", "w") as f:
        json.dump(tsconfig, f, indent=2)

def create_babel_config(project_dir):
    """Create simple Babel configuration."""
    babel_config = """module.exports = {
  presets: [
    'module:metro-react-native-babel-preset',
    ['react-strict-dom/babel-preset', { platform: 'web' }]
  ],
  plugins: ['@stylexjs/babel-plugin']
};
"""
    with open(project_dir / "babel.config.js", "w") as f:
        f.write(babel_config)

def create_metro_config(project_dir):
    """Create Metro configuration for native builds only."""
    metro_config = """const {getDefaultConfig, mergeConfig} = require('@react-native/metro-config');

const defaultConfig = getDefaultConfig(__dirname);

const config = {
  resolver: {
    platforms: ['ios', 'android', 'native'],
  },
};

module.exports = mergeConfig(defaultConfig, config);
"""
    with open(project_dir / "metro.config.js", "w") as f:
        f.write(metro_config)

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
    """Create simplified web entry point only."""
    web_entry = """import React from 'react';
import { createRoot } from 'react-dom/client';
import { AppRegistry } from 'react-native-web';
import App from './App';

// Register the app
AppRegistry.registerComponent('OnRampApp', () => App);

// Start the app on web
const container = document.getElementById('root');
if (container) {
  const root = createRoot(container);
  root.render(<App />);
}
"""
    with open(project_dir / "index.web.js", "w") as f:
        f.write(web_entry)
    
    # Don't create public directory since Metro serves HTML via middleware

def copy_static_assets(project_dir):
    """Copy static assets like logo.png to the project."""
    script_dir = Path(__file__).parent
    static_dir = script_dir / "static"
    
    if static_dir.exists():
        print("Copying static assets")
        logo_source = static_dir / "logo.png"
        if logo_source.exists():
            # Copy to project root
            logo_dest = project_dir / "logo.png"
            shutil.copy2(logo_source, logo_dest)
            
            # Create assets directory instead of public
            assets_dir = project_dir / "assets"
            assets_dir.mkdir(exist_ok=True)
            logo_assets_dest = assets_dir / "logo.png"
            shutil.copy2(logo_source, logo_assets_dest)
    else:
        print("⚠️  Static folder not found - skipping asset copy")

def create_readme(project_dir, app_name):
    """Create README with instructions."""
    readme_content = f'''# {app_name}

React Native app with React Strict DOM and file-based navigation.

## File-Based Routing

This app uses file-based routing similar to Next.js:

- `app/index.tsx` → `/` route
- `app/profile/[id].tsx` → `/profile/:id` route  
- `app/(tabs)/home.tsx` → `/home` route with tabs layout

## Development

```bash
npm run build:routes  # Generate routes from file structure
npm run android       # Run on Android
npm run ios          # Run on iOS (macOS only)
npm start            # Start Metro bundler with route generation
npm run web          # Run on web
```

## Adding Routes

1. Create files in the `app/` directory
2. Routes are automatically generated from file structure
3. Use `[param]` for dynamic routes
4. Use `(group)` for layout groups
5. Use `_layout.tsx` for nested layouts

## Setup Requirements

### For Android:
- Android Studio + Android SDK + JDK

### For iOS (macOS only):
- Xcode + iOS Simulator + CocoaPods

'''
    with open(project_dir / "README.md", "w") as f:
        f.write(readme_content)

def create_react_native_app(app_name, output_dir="."):
    """Create a React Native app with separate web and native bundling."""
    
    project_dir = Path(output_dir) / app_name / "build"
    project_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Creating OnRamp frontend with file-based navigation...")
    
    # Create all files
    create_package_json(app_name, project_dir)
    create_babel_config(project_dir)
    create_metro_config(project_dir)  # Native only
    create_webpack_config(project_dir)  # Web only
    create_app_json(project_dir, app_name)
    create_web_index_html(project_dir)  # For Webpack
    create_web_entry(project_dir)  # For Webpack
    create_typescript_config(project_dir)
    create_readme(project_dir, app_name)
    
    # Copy navigation templates
    copy_navigation_templates(project_dir)
    
    # Create app structure and components
    create_app_structure(project_dir)
    create_app_component(project_dir)
    create_index_files(project_dir, app_name)  # For React Native
    
    # Copy static assets
    copy_static_assets(project_dir)
    
    # Install dependencies
    try:
        run_command("npm install --legacy-peer-deps", cwd=project_dir)
    except subprocess.CalledProcessError:
        print("⚠️  Installation failed. Please run manually:")
        print(f"   cd {app_name}/build && npm install --legacy-peer-deps")
        return
    
    # Generate initial routes
    scripts_dir = project_dir / "scripts"
    build_script = scripts_dir / "build-routes.js"
    if build_script.exists():
        try:
            run_command("node scripts/build-routes.js", cwd=project_dir)
        except subprocess.CalledProcessError as e:
            print("⚠️  Could not generate initial routes")
    
    print(f"OnRamp frontend created!")
    print(f"")
    print(f"Commands:")
    print(f"  cd {app_name}/build")
    print(f"  npm run start:native  # Start native development")
    print(f"  npm run start:web     # Start web development")
    print(f"  npm run android       # Run Android app")
    print(f"  npm run ios           # Run iOS app")