#!/usr/bin/env python3
"""
React Native + React Strict DOM App Generator

This script creates a React Native app with React Strict DOM as the representation layer.
Uses Vite for web development instead of webpack for better reliability.
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
    """Create package.json with React Strict DOM dependencies using Vite."""
    package_json = {
        "name": app_name,
        "version": "0.0.1",
        "type": "module",
        "private": True,
        "scripts": {
            "dev": "vite",
            "build": "vite build",
            "preview": "vite preview",
            "lint": "eslint .",
            "test": "jest"
        },
        "dependencies": {
            "react": "^18.2.0",
            "react-dom": "^18.2.0",
            "react-strict-dom": "^0.0.28",
            "@stylexjs/stylex": "^0.9.0"
        },
        "devDependencies": {
            "@types/react": "^18.2.0",
            "@types/react-dom": "^18.2.0",
            "@vitejs/plugin-react": "^4.0.0",
            "@stylexjs/babel-plugin": "^0.9.0",
            "vite": "^4.4.0",
            "eslint": "^8.19.0",
            "prettier": "^2.4.1"
        }
    }
    
    with open(project_dir / "package.json", "w") as f:
        json.dump(package_json, f, indent=2)

def create_vite_config(project_dir):
    """Create Vite configuration for React Strict DOM."""
    vite_config = """import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [
    react({
      babel: {
        presets: [
          ['react-strict-dom/babel-preset', { 
            rootDir: process.cwd() 
          }]
        ]
      }
    })
  ],
  server: {
    port: 3000,
    open: true
  }
})
"""
    
    with open(project_dir / "vite.config.js", "w") as f:
        f.write(vite_config)

def create_app_component(project_dir):
    """Create the main App component using React Strict DOM."""
    app_component = '''import React, { useState } from 'react';
import { html } from 'react-strict-dom';

function App() {
  const [count, setCount] = useState(0);

  return (
    <html.div
      style={{
        padding: '2rem',
        textAlign: 'center',
        fontFamily: 'system-ui, -apple-system, sans-serif',
        minHeight: '100vh',
        backgroundColor: '#f5f5f5',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center'
      }}
    >
      <html.div
        style={{
          backgroundColor: 'white',
          padding: '3rem',
          borderRadius: '12px',
          boxShadow: '0 4px 6px rgba(0, 0, 0, 0.1)',
          maxWidth: '500px',
          width: '100%'
        }}
      >
        <html.h1
          style={{
            fontSize: '2.5rem',
            color: '#333',
            marginBottom: '1rem',
            fontWeight: 'bold'
          }}
        >
          🚀 React Strict DOM
        </html.h1>
        
        <html.p
          style={{
            fontSize: '1.1rem',
            marginBottom: '2rem',
            color: '#666',
            lineHeight: '1.6'
          }}
        >
          Universal components that work on web and native platforms!
        </html.p>
        
        <html.div
          style={{
            backgroundColor: '#f8f9fa',
            padding: '2rem',
            borderRadius: '8px',
            marginBottom: '2rem'
          }}
        >
          <html.p
            style={{
              fontSize: '1.3rem',
              marginBottom: '1.5rem',
              fontWeight: 'bold'
            }}
          >
            Counter: <span style={{ color: '#007AFF' }}>{count}</span>
          </html.p>
          
          <html.div
            style={{
              display: 'flex',
              gap: '1rem',
              justifyContent: 'center',
              flexWrap: 'wrap'
            }}
          >
            <html.button
              onClick={() => setCount(count + 1)}
              style={{
                backgroundColor: '#007AFF',
                color: 'white',
                border: 'none',
                padding: '12px 24px',
                borderRadius: '8px',
                fontSize: '16px',
                fontWeight: '600',
                cursor: 'pointer'
              }}
            >
              ➕ Add One
            </html.button>
            
            <html.button
              onClick={() => setCount(Math.max(0, count - 1))}
              style={{
                backgroundColor: '#FF9500',
                color: 'white',
                border: 'none',
                padding: '12px 24px',
                borderRadius: '8px',
                fontSize: '16px',
                fontWeight: '600',
                cursor: 'pointer'
              }}
            >
              ➖ Subtract
            </html.button>
            
            <html.button
              onClick={() => setCount(0)}
              style={{
                backgroundColor: '#FF3B30',
                color: 'white',
                border: 'none',
                padding: '12px 24px',
                borderRadius: '8px',
                fontSize: '16px',
                fontWeight: '600',
                cursor: 'pointer'
              }}
            >
              🔄 Reset
            </html.button>
          </html.div>
        </html.div>
        
        <html.div
          style={{
            backgroundColor: count > 10 ? '#d4edda' : count > 5 ? '#fff3cd' : '#d1ecf1',
            color: count > 10 ? '#155724' : count > 5 ? '#856404' : '#0c5460',
            padding: '1rem',
            borderRadius: '6px',
            border: `2px solid ${count > 10 ? '#c3e6cb' : count > 5 ? '#ffeaa7' : '#bee5eb'}`
          }}
        >
          <html.p style={{ margin: 0, fontWeight: '500' }}>
            {count > 10 ? '🎉 Wow! You\\'re really clicking!' : 
             count > 5 ? '👏 Nice work!' : 
             count > 0 ? '✨ Keep going!' : 
             '👆 Click a button to get started!'}
          </html.p>
        </html.div>
        
        <html.div
          style={{
            marginTop: '2rem',
            padding: '1rem',
            backgroundColor: '#f8f9fa',
            borderRadius: '6px',
            fontSize: '0.9rem',
            color: '#666'
          }}
        >
          <html.p style={{ margin: 0 }}>
            💡 <strong>React Strict DOM Benefits:</strong><br/>
            • Same components work on web & mobile<br/>
            • DOM-like API (html.div, html.button, etc.)<br/>
            • Better performance than traditional React Native<br/>
            • Perfect for building Tailwind → StyleX plugins!
          </html.p>
        </html.div>
      </html.div>
    </html.div>
  );
}

export default App;
'''
    
    with open(project_dir / "src" / "App.jsx", "w") as f:
        f.write(app_component)

def create_main_entry(project_dir):
    """Create the main entry point for Vite."""
    main_jsx = '''import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
'''
    
    with open(project_dir / "src" / "main.jsx", "w") as f:
        f.write(main_jsx)

def create_html_template(project_dir):
    """Create HTML template for Vite."""
    html_template = '''<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>React Strict DOM App</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
'''
    
    with open(project_dir / "index.html", "w") as f:
        f.write(html_template)

def create_app_json(project_dir, app_name):
    """Create app.json for React Native."""
    app_json = {
        "name": app_name,
        "displayName": app_name
    }
    
    with open(project_dir / "app.json", "w") as f:
        json.dump(app_json, f, indent=2)

def create_readme(project_dir, app_name):
    """Create README with instructions."""
    readme_content = f'''# {app_name}

React Strict DOM web app with universal components.

## Features

- 🚀 **React Strict DOM**: Universal components that work on web and native
- ⚡ **Vite**: Fast development server and build tool
- 🎯 **Ready for plugins**: Perfect foundation for Tailwind → StyleX conversion
- 📱 **Future-ready**: Same code will work on native when React Native supports React 19

## Development

### Web Development
```bash
npm run dev        # Start Vite dev server
npm run build      # Build for production
npm run preview    # Preview production build
```

## Project Structure

```
{app_name}/
├── src/
│   ├── App.jsx           # Main app component
│   └── main.jsx          # Entry point
├── index.html            # HTML template
├── vite.config.js        # Vite configuration
├── package.json          # Dependencies and scripts
└── README.md            # This file
```

## Adding Your Tailwind → StyleX Plugin

This project is set up perfectly for adding your custom Babel plugin:

```javascript
// vite.config.js
export default defineConfig({{
  plugins: [
    react({{
      babel: {{
        presets: [
          ['react-strict-dom/babel-preset', {{ 
            rootDir: process.cwd() 
          }}]
        ],
        plugins: [
          ['your-tailwind-to-stylex-plugin', {{
            // Your plugin options
          }}]
        ]
      }}
    }})
  ],
}})
```

## Adding React Native Support Later

When React Native updates to support React 19, you can add native support:

```bash
# Install React Native dependencies
npm install react-native@latest
npx react-native init --skip-install
```

Then add these scripts to package.json:
```json
{{
  "scripts": {{
    "android": "react-native run-android",
    "ios": "react-native run-ios", 
    "start": "react-native start"
  }}
}}
```

## React Strict DOM Benefits

- **Universal API**: `html.div`, `html.button`, etc. work everywhere
- **Better Performance**: Optimized for both web and native
- **Familiar Syntax**: DOM-like API that's easy to learn
- **Plugin Ready**: Perfect for build-time transformations

Happy coding! 🎉
'''
    
    with open(project_dir / "README.md", "w") as f:
        f.write(readme_content)

def create_react_native_app(app_name, output_dir="."):
    """Create a React Strict DOM web app (React Native support coming when ecosystem catches up to React 19)."""
    
    print(f"Creating React Strict DOM web app: {app_name}")
    
    # Create project directory
    project_dir = Path(output_dir) / app_name
    project_dir.mkdir(parents=True, exist_ok=True)
    
    # Create src directory
    src_dir = project_dir / "src"
    src_dir.mkdir(exist_ok=True)
    
    print(f"Creating project in: {project_dir}")
    
    # Create all configuration files
    create_package_json(app_name, project_dir)
    create_vite_config(project_dir)
    create_app_json(project_dir, app_name)
    create_html_template(project_dir)
    create_readme(project_dir, app_name)
    
    # Create source files
    create_app_component(project_dir)
    create_main_entry(project_dir)
    
    print(f"✅ Project structure created successfully!")
    
    # Install dependencies
    print("Installing dependencies...")
    try:
        run_command("npm install --legacy-peer-deps", cwd=project_dir)
        print("✅ Dependencies installed successfully!")
    except subprocess.CalledProcessError:
        print("⚠️  Failed to install dependencies. Run 'npm install --legacy-peer-deps' manually in the project directory.")
    
    print(f"""
🎉 React Strict DOM web app created successfully!

Next steps:
1. cd {app_name}
2. npm run dev                    # Start web development

Features:
✅ Vite for fast development
✅ React Strict DOM for universal components  
✅ Ready for your Tailwind → StyleX plugin
✅ React 18 with React Strict DOM

Note: This creates a web-first setup using React 18. React Native support
can be added when the ecosystem stabilizes around compatible versions.

The app includes an interactive demo showing React Strict DOM in action!
""")

def main():
    parser = argparse.ArgumentParser(
        description="Create a React Strict DOM web app (React Native support coming with React 19 compatibility)"
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