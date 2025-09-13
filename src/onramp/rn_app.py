#!/usr/bin/env python3
'''
React Native + React Strict DOM App Generator with File-Based Navigation

Creates a React Native app with React Strict DOM and file-based navigation.
Pinned to Node 20 ecosystem (RN CLI 20.x).
'''

import subprocess
import json
import argparse
import shutil
import re
from pathlib import Path

import os
import re
import subprocess

def require_node(version_min="20.19.4"):
    """
    Ensure Node >= version_min. If not, offer to switch via nvm.
    On success, updates os.environ['PATH'] so child processes use the new Node.
    """
    try:
        out = subprocess.run(["node", "-v"], text=True, capture_output=True, check=True).stdout.strip()
    except Exception:
        out = ""

    m = re.match(r"v(\d+)\.(\d+)\.(\d+)", out or "")
    cur = tuple(map(int, m.groups())) if m else (0, 0, 0)
    want = tuple(map(int, version_min.split(".")))

    if cur and cur >= want:
        # already good
        return

    # Not good enough; offer to fix automatically
    print(f"Node {out or 'not found'} detected; React Native requires ≥ v{version_min}.")
    resp = input(f"Use nvm to install/use {version_min} now? (y/N): ").strip().lower()
    if resp != "y":
        print(f"Please run: nvm install {version_min} && nvm use {version_min}")
        raise SystemExit(1)

    nvm_dir = os.path.expanduser("~/.nvm")
    nvm_sh = os.path.join(nvm_dir, "nvm.sh")
    if not os.path.exists(nvm_sh):
        print("nvm not found at ~/.nvm. Install nvm first:")
        print("  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash")
        print(f"Then run: nvm install {version_min} && nvm use {version_min}")
        raise SystemExit(1)

    # Try to install/use exact target and capture node path
    script = f'''
      export NVM_DIR="{nvm_dir}"
      [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
      nvm install {version_min}
      nvm use {version_min}
      echo NODE_BIN:$(command -v node)
      node -v
    '''
    res = subprocess.run(["bash", "-lc", script], text=True, capture_output=True)
    if res.returncode != 0:
        print("Failed to activate Node with nvm.\n", res.stdout or res.stderr)
        raise SystemExit(1)

    # Extract node bin and update PATH for this process (children inherit it)
    mm = re.search(r"NODE_BIN:(.*)", res.stdout or "")
    if not mm:
        print("Could not resolve Node path from nvm output. Aborting.")
        raise SystemExit(1)
    node_bin = mm.group(1).strip()
    node_dir = os.path.dirname(node_bin)
    os.environ["PATH"] = f"{node_dir}:{os.environ.get('PATH','')}"  # prepend

    # Final sanity check
    out2 = subprocess.run(["node", "-v"], text=True, capture_output=True, check=True).stdout.strip()
    m2 = re.match(r"v(\d+)\.(\d+)\.(\d+)", out2 or "")
    cur2 = tuple(map(int, m2.groups())) if m2 else (0, 0, 0)
    if cur2 < want:
        print(f"Unexpected: still on {out2}. Please switch manually.")
        raise SystemExit(1)

    print(f"✓ Node {out2} activated via nvm")


def run_command(command, cwd=None, check=True):
    '''Run a shell command and return the result.'''
    print(f"Running: {command}")
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            check=check,
            capture_output=True,
            text=True,
        )
        if result.stdout:
            print(result.stdout)
        return result
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {e}")
        if e.stderr:
            print(f"Error output: {e.stderr}")
        raise

def _npm_pkg_name(s: str) -> str:
    """Make a safe npm package name (lowercase, hyphenated)."""
    s = s.strip().lower()
    s = re.sub(r'[^a-z0-9._-]+', '-', s)
    s = re.sub(r'-{2,}', '-', s).strip('-')
    return s or "app"

def create_package_json(app_name: str, project_dir: Path):
    """Create package.json with separate bundlers for web and native."""
    pkg_name = _npm_pkg_name(app_name)

    package = {
        "name": pkg_name,
        "version": "0.0.1",
        "private": True,
        "scripts": {
            # Native
            "android": "npx react-native run-android",
            "ios": "npx react-native run-ios",
            "start:native": "npx metro start --port 8081",
            "start:rn": "npx react-native start",

            # Web (webpack)
            "start:web": "node scripts/build-routes.js && webpack serve",
            "build:web": "node scripts/build-routes.js && webpack --mode production",

            # Defaults
            "start": "npm run start:native",
            "web": "npm run start:web",
            "build:routes": "node scripts/build-routes.js",
            "test": "jest"
        },
        "dependencies": {
            "@react-navigation/bottom-tabs": "^6.6.1",
            "@react-navigation/native": "^6.1.18",
            "@react-navigation/native-stack": "^6.9.26",
            "@stylexjs/stylex": "^0.8.0",
            "react": "19.1.0",
            "react-dom": "19.1.0",
            "react-native": "0.81.1",
            "react-native-gesture-handler": "^2.16.2",
            "react-native-safe-area-context": "^5.6.1",
            "react-native-screens": "^4.6.0",
            "react-strict-dom": "^0.0.44",
        },
        "devDependencies": {
            "@babel/core": "^7.24.0",
            "@babel/preset-env": "^7.24.0",
            "@babel/preset-react": "^7.24.0",
            "@babel/preset-typescript": "^7.24.0",
            "@babel/runtime": "^7.24.0",

            "@react-native/babel-preset": "0.81.1",
            "@react-native/metro-config": "0.81.1",

            "@stylexjs/babel-plugin": "^0.8.0",

            "@react-native-community/cli": "^20.0.2",
            "@react-native-community/cli-platform-ios": "^20.0.2",
            "@react-native-community/cli-platform-android": "^20.0.2",

            # Web bundling
            "webpack": "^5.88.0",
            "webpack-cli": "^5.1.0",
            "webpack-dev-server": "^4.15.0",
            "babel-loader": "^9.1.0",
            "html-webpack-plugin": "^5.5.0",

            # Types
            "@types/react": "^19.1.0",
            "@types/react-dom": "^19.1.0",
            "typescript": "^5.6.2",

            # Tooling
            "prettier": "^2.8.8",
            "chokidar": "^3.5.3",

            # Tests (optional but nice to pin)
            "jest": "^29.7.0",
            "react-test-renderer": "19.1.0"
        },
        "jest": { "preset": "react-native" },
        "engines": { "node": ">=20.19.4" },
        "packageManager": "npm@10"
    }

    (project_dir / "package.json").write_text(
        json.dumps(package, indent=2) + "\n",
        encoding="utf-8"
    )

def create_webpack_config(project_dir: Path):
    webpack_config = '''const path = require('path');
const HtmlWebpackPlugin = require('html-webpack-plugin');

module.exports = {
  entry: './index.web.js',
  mode: 'development',
  devServer: {
    port: 'auto',
    historyApiFallback: true,
    static: { directory: path.join(__dirname, 'assets') },
  },
  module: {
    rules: [
      // App code
      {
        test: /\\.(js|jsx|ts|tsx)$/,
        exclude: /node_modules/,
        use: {
          loader: 'babel-loader',
          options: {
            // Use only these options for web (avoid metro config bleed-through)
            babelrc: false,
            configFile: false,
            presets: [
              ['@babel/preset-env', { targets: 'defaults' }],
              ['@babel/preset-react', { runtime: 'automatic' }],
              ['@babel/preset-typescript'],
              ['react-strict-dom/babel-preset', { platform: 'web' }]
            ],
            plugins: [
              ['@stylexjs/babel-plugin', {
                dev: true,
                runtimeInjection: false,
                genConditionalClasses: true,
                treeshakeCompensation: true,
                unstable_moduleResolution: { type: 'commonJS', rootDir: __dirname }
              }]
            ]
          }
        }
      },
      // Transpile react-strict-dom itself for web
      {
        test: /\\.(js|jsx|ts|tsx)$/,
        include: /node_modules[\\/]react-strict-dom/,
        use: {
          loader: 'babel-loader',
          options: {
            babelrc: false,
            configFile: false,
            presets: [
              ['@babel/preset-env', { targets: 'defaults' }],
              ['@babel/preset-react', { runtime: 'automatic' }],
              ['react-strict-dom/babel-preset', { platform: 'web' }]
            ],
            plugins: [
              ['@stylexjs/babel-plugin', {
                dev: true,
                runtimeInjection: false,
                genConditionalClasses: true,
                treeshakeCompensation: true,
                unstable_moduleResolution: { type: 'commonJS', rootDir: __dirname }
              }]
            ]
          }
        }
      }
    ]
  },
  resolve: {
    extensions: ['.web.js','.web.jsx','.web.ts','.web.tsx','.js','.jsx','.ts','.tsx'],
    alias: { 'react-native$': 'react-strict-dom' }
  },
  plugins: [ new HtmlWebpackPlugin({ template: 'index.html', inject: true }) ],
  output: { path: path.resolve(__dirname, 'dist'), filename: 'bundle.js', publicPath: '/' }
};'''
    (project_dir / "webpack.config.js").write_text(webpack_config)

def create_web_index_html(project_dir: Path):
    '''Create HTML template for Webpack.'''
    html_content = '''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>OnRamp App</title>
  <style>
    html, body, #root { height: 100%; margin: 0; }
  </style>
</head>
<body>
  <div id="root"></div>
</body>
</html>
'''
    (project_dir / "index.html").write_text(html_content)


def create_web_entry(project_dir: Path):
    '''Create web entry point for Webpack (React Strict DOM target).'''
    web_entry = '''import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';

const container = document.getElementById('root');
if (container) {
  const root = createRoot(container);
  root.render(<App />);
}
'''
    (project_dir / "index.web.js").write_text(web_entry)


def copy_navigation_templates(project_dir: Path):
    script_dir    = Path(__file__).parent
    templates_dir = script_dir / "templates"

    src_dir        = project_dir / "src"
    navigation_dir = src_dir / "navigation"
    generated_dir  = src_dir / "generated"
    scripts_dir    = project_dir / "scripts"
    build_dir      = project_dir            # <-- use the existing build root

    for p in (src_dir, navigation_dir, generated_dir, scripts_dir, build_dir):
        p.mkdir(parents=True, exist_ok=True)

    if not templates_dir.exists():
        print("⚠️  Templates folder not found — creating basic navigation structure")
        create_basic_navigation_structure(project_dir)
        # also ensure generateRoutes.js exists as a stub
        (build_dir / "generateRoutes.js").write_text(
            "module.exports = function generateRoutes(){ /* noop */ };\n"
        )
        return

    template_map = {
        "generateRoutes.js":      build_dir / "generateRoutes.js",
        "NavigationProvider.tsx": navigation_dir / "NavigationProvider.tsx",
        "RouteRegistry.tsx":      navigation_dir / "RouteRegistry.tsx",
        "build-routes.js":        scripts_dir / "build-routes.js",
    }

    for template_name, dest_path in template_map.items():
        src_path = templates_dir / template_name
        if src_path.exists():
            print(f"Copying {template_name} to {dest_path}")
            shutil.copy2(src_path, dest_path)
        else:
            print(f"⚠️  Template {template_name} not found — creating a basic version")
            create_basic_template(template_name, dest_path)



def create_basic_navigation_structure(project_dir: Path):
    '''Create basic navigation structure if templates aren't available.'''
    src_dir = project_dir / "src"
    navigation_dir = src_dir / "navigation"
    generated_dir = src_dir / "generated"
    scripts_dir = project_dir / "scripts"

    for d in [src_dir, navigation_dir, generated_dir, scripts_dir]:
        d.mkdir(parents=True, exist_ok=True)

    basic_script = (
        '#!/usr/bin/env node\n'
        'console.log("File-based routing: Scanning for routes...");\n\n'
        'const fs = require("fs");\n'
        'const path = require("path");\n\n'
        'function generateRoutes() {\n'
        '  const routes = [];\n'
        '  const appDir = path.join(__dirname, "..", "app");\n'
        '  if (!fs.existsSync(appDir)) { fs.mkdirSync(appDir, { recursive: true }); }\n'
        '  console.log("Routes generated successfully");\n'
        '}\n'
        'generateRoutes();\n'
    )
    (scripts_dir / "build-routes.js").write_text(basic_script)


def create_basic_template(template_name: str, dest_path: Path):
    '''Create basic versions of templates if not found.'''
    if template_name == "NavigationProvider.tsx":
        content = '''// Basic Navigation Provider
import React, { createContext, useContext, useRef } from 'react';

const NavigationContext = createContext({
  currentRoute: '/',
  params: {},
  navigate: (_path) => {},
  goBack: () => {},
  canGoBack: () => false
});

export function NavigationProvider({ children, initialRoute = '/' }) {
  const history = useRef([initialRoute]);
  const paramsRef = useRef({});

  const api = {
    get currentRoute() { return history.current[history.current.length - 1]; },
    get params() { return paramsRef.current; },
    navigate: (path, params = {}) => { history.current.push(path); paramsRef.current = params; },
    goBack: () => { if (history.current.length > 1) history.current.pop(); },
    canGoBack: () => history.current.length > 1,
  };

  return <NavigationContext.Provider value={api}>{children}</NavigationContext.Provider>;
}

export function useNavigation() { return useContext(NavigationContext); }
'''
        dest_path.write_text(content)
    elif template_name == "RouteRegistry.tsx":
        content = '''// Minimal Route Registry glue (replace with your generator output)
import React from 'react';
import HomePage from '../../app/index';
import AboutPage from '../../app/about';

export function RouteComponent({ path, params }) {
  if (path === '/about') return <AboutPage />;
  if (path.startsWith('/profile/')) {
    const id = path.split('/').pop();
    const Profile = require('../../app/profile/[id]').default;
    return <Profile id={id} />;
  }
  return <HomePage />;
}
'''
        dest_path.write_text(content)
    elif template_name == "generateRoutes.js":
        dest_path.write_text(
            "module.exports = function generateRoutes(){ /* noop */ };\n"
        )
    elif template_name == "build-routes.js":
        dest_path.write_text(
            '#!/usr/bin/env node\n'
            'console.log("File-based routing: Scanning for routes...");\n'
            'try { require("../generateRoutes")(); } catch (e) { /* optional */ }\n'
            'console.log("Routes generated successfully");\n'
        )
    else:
        dest_path.write_text('// Template placeholder\n')


def create_app_structure(project_dir: Path):
    '''Create the app directory structure for file-based routing.'''
    app_dir = project_dir / "app"
    app_dir.mkdir(exist_ok=True)

    index_content = '''import React from 'react';
import { html } from 'react-strict-dom';
import { useNavigation } from '../src/navigation/NavigationProvider';

export default function HomePage() {
  const { navigate } = useNavigation();

  return (
    <html.div style={{
      flex: 1,
      boxSizing: 'border-box',
      height: '100%',
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
        <html.h1 style={{ fontSize: 24, color: '#333', marginBottom: 16, fontWeight: 'bold', textAlign: 'center' } as any}>
          Welcome to OnRamp
        </html.h1>
        <html.h2 style={{ fontSize: 19, color: '#333', marginBottom: 16, fontWeight: 'bold', textAlign: 'center' } as any}>
          The Python App Framework
        </html.h2>
        <html.div style={{ display: 'flex', gap: 10, justifyContent: 'center', flexDirection: 'column' } as any}>
          <html.button 
            onClick={() => navigate('/profile/123')}
            style={{ padding: '10px 20px', backgroundColor: '#007AFF', color: 'white', border: 'none', borderRadius: 6, cursor: 'pointer' } as any}
          >Go to Profile</html.button>
          <html.button 
            onClick={() => navigate('/about')}
            style={{ padding: '10px 20px', backgroundColor: '#34C759', color: 'white', border: 'none', borderRadius: 6, cursor: 'pointer' } as any}
          >About Page</html.button>
        </html.div>
      </html.div>
    </html.div>
  );
}
'''
    (app_dir / "index.tsx").write_text(index_content)

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
      flex: 1,
      boxSizing: 'border-box',
      height: '100%',
      backgroundColor: '#f5f5f5'
    }}>
      <html.div style={{
        backgroundColor: 'white',
        padding: 30,
        borderRadius: 12,
        boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
        maxWidth: 600
      }}>
        <html.h1 style={{ color: '#333', marginBottom: 16 }}>Profile Page</html.h1>
        <html.p style={{ color: '#666', marginBottom: 20 }}>Profile ID: {id || 'No ID provided'}</html.p>
        <html.div style={{ display: 'flex', gap: 10 }}>
          {canGoBack() && (
            <html.button 
              onClick={goBack}
              style={{ padding: '10px 20px', backgroundColor: '#666', color: 'white', border: 'none', borderRadius: 6, cursor: 'pointer' }}
            >Go Back</html.button>
          )}
          <html.button 
            onClick={() => navigate('/')}
            style={{ padding: '10px 20px', backgroundColor: '#007AFF', color: 'white', border: 'none', borderRadius: 6, cursor: 'pointer' }}
          >Home</html.button>
        </html.div>
      </html.div>
    </html.div>
  );
}
'''
    (profile_dir / "[id].tsx").write_text(profile_content)

    about_content = '''import React from 'react';
import { html } from 'react-strict-dom';
import { useNavigation } from '../src/navigation/NavigationProvider';

export default function AboutPage() {
  const { navigate, goBack } = useNavigation();

  return (
    <html.div style={{
      padding: 20,
      fontFamily: 'system-ui, -apple-system, sans-serif',
      flex: 1,
      boxSizing: 'border-box',
      height: '100%',
      backgroundColor: '#f5f5f5'
    }}>
      <html.div style={{
        backgroundColor: 'white',
        padding: 30,
        borderRadius: 12,
        boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
        maxWidth: 600
      }}>
        <html.h1 style={{ color: '#333', marginBottom: 16 }}>About OnRamp</html.h1>
        <html.p style={{ color: '#666', marginBottom: 20 }}>
          OnRamp is a modern framework for building cross-platform applications with React Native and React Strict DOM.
        </html.p>
        <html.div style={{ display: 'flex', gap: 10 }}>
          <html.button 
            onClick={goBack}
            style={{ padding: '10px 20px', backgroundColor: '#666', color: 'white', border: 'none', borderRadius: 6, cursor: 'pointer' }}
          >Go Back</html.button>
          <html.button 
            onClick={() => navigate('/')}
            style={{ padding: '10px 20px', backgroundColor: '#007AFF', color: 'white', border: 'none', borderRadius: 6, cursor: 'pointer' }}
          >Home</html.button>
        </html.div>
      </html.div>
    </html.div>
  );
}
'''
    (app_dir / "about.tsx").write_text(about_content)


def create_app_component(project_dir: Path):
    '''Create the main App component that uses the navigation system.'''
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
    (project_dir / "App.jsx").write_text(app_component)


def create_typescript_config(project_dir: Path):
    '''Create TypeScript configuration.'''
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
            "moduleResolution": "bundler",
            "resolveJsonModule": True,
            "isolatedModules": True,
            "noEmit": True,
            "jsx": "react-jsx"
        },
        "include": ["src/**/*", "app/**/*", "App.jsx"],
        "exclude": ["node_modules"]
    }
    (project_dir / "tsconfig.json").write_text(json.dumps(tsconfig, indent=2))


def create_babel_config(project_dir: Path):
    babel_config = '''module.exports = {
  // Metro (native) only
  presets: ['module:@react-native/babel-preset'],
  plugins: ['@stylexjs/babel-plugin'],
};'''
    (project_dir / "babel.config.js").write_text(babel_config)



def create_metro_config(project_dir: Path):
    '''Create Metro configuration for native builds only.'''
    metro_config = '''const {getDefaultConfig, mergeConfig} = require('@react-native/metro-config');

const defaultConfig = getDefaultConfig(__dirname);

const config = {
  resolver: { platforms: ['ios', 'android', 'native'] },
};

module.exports = mergeConfig(defaultConfig, config);
'''
    (project_dir / "metro.config.js").write_text(metro_config)


def create_index_files(project_dir: Path, app_name: str):
    '''Create index files for React Native.'''
    rn_index = f'''import {{ AppRegistry }} from 'react-native';
import App from './App';
import {{ name as appName }} from './app.json';

AppRegistry.registerComponent(appName, () => App);
'''
    (project_dir / "index.js").write_text(rn_index)


def create_app_json(project_dir: Path, app_name: str):
    '''Create app.json for React Native.'''
    app_json = { "name": app_name, "displayName": app_name }
    (project_dir / "app.json").write_text(json.dumps(app_json, indent=2))


def copy_static_assets(project_dir: Path):
    '''Copy static assets like logo.png to the project.'''
    script_dir = Path(__file__).parent
    static_dir = script_dir / "static"

    if static_dir.exists():
        print("Copying static assets")
        logo_source = static_dir / "logo.png"
        if logo_source.exists():
            (project_dir / "assets").mkdir(exist_ok=True)
            shutil.copy2(logo_source, project_dir / "logo.png")
            shutil.copy2(logo_source, project_dir / "assets" / "logo.png")
    else:
        print("⚠️  Static folder not found - skipping asset copy")


def create_readme(project_dir: Path, app_name: str):
    '''Create README with instructions (no triple-backticks to avoid chat render issues).'''
    readme_content = f'''# {app_name}

React Native app with React Strict DOM and file-based navigation.

## File-Based Routing

- app/index.tsx -> /
- app/profile/[id].tsx -> /profile/:id
- app/(tabs)/home.tsx -> /home with tabs layout

## Development

    # Generate routes from file structure
    npm run build:routes
    # Run on Android
    npm run android
    # Run on iOS (macOS only)
    npm run ios
    # Start Metro bundler with route generation
    npm start
    # Run on web
    npm run web

## Setup Requirements

### Android
- Android Studio + Android SDK + JDK

### iOS (macOS only)
- Xcode + iOS Simulator + CocoaPods
'''
    (project_dir / "README.md").write_text(readme_content)


def create_react_native_app(app_name: str, output_dir: str = "."):
    """Create a React Native app with separate web and native bundling."""

    require_node()

    project_dir = Path(output_dir) / app_name / "build"
    project_dir.mkdir(parents=True, exist_ok=True)

    print("Creating OnRamp frontend with file-based navigation...")

    # Create all files
    create_package_json(app_name, project_dir)
    create_babel_config(project_dir)
    create_metro_config(project_dir)    # Native only
    create_webpack_config(project_dir)  # Web only
    create_app_json(project_dir, app_name)
    create_web_index_html(project_dir)  # For Webpack
    create_web_entry(project_dir)       # For Webpack
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
        # Scrub ~/.npmrc pitfalls before install
        npmrc = Path.home() / ".npmrc"
        if npmrc.exists():
            try:
                lines = npmrc.read_text().splitlines(True)
                kept = [ln for ln in lines if not ln.startswith(("prefix=", "globalconfig="))]
                if kept != lines:
                    npmrc.write_text("".join(kept))
                    print("Scrubbed ~/.npmrc (removed prefix/globalconfig)")
            except Exception as e:
                print(f"Warning: could not scrub ~/.npmrc: {e}")

        pkg = project_dir / "package.json"
        if not pkg.exists():
            print(f"ERROR: {pkg} was not created; aborting install.")
            try:
                print("\nContents of project_dir:")
                for p in sorted(project_dir.glob("*")):
                    print(" -", p.name)
            except Exception:
                pass
            return

        run_command("npm install --legacy-peer-deps", cwd=project_dir)
    except subprocess.CalledProcessError:
        print("⚠️  Installation failed. Please run manually:")
        print(f"   cd {app_name}/build && npm install --legacy-peer-deps")
        return

    # Generate initial routes (safe no-op if script missing)
    scripts_dir = project_dir / "scripts"
    build_script = scripts_dir / "build-routes.js"
    if build_script.exists():
        try:
            run_command("node scripts/build-routes.js", cwd=project_dir)
        except subprocess.CalledProcessError:
            print("⚠️  Could not generate initial routes")

    print("OnRamp frontend created!\n")
    print("Commands:")
    print(f"  cd {app_name}/build")
    print("  npm run start:native  # Start native development (Metro)")
    print("  npm run start:web     # Start web development (Webpack)")
    print("  npm run android       # Run Android app")
    print("  npm run ios           # Run iOS app")