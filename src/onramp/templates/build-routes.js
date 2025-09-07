// scripts/build-routes.js
const { generateRoutesConfig } = require('../build/generateRoutes');
const chokidar = require('chokidar');
const path = require('path');

// Generate routes initially
generateRoutesConfig();

// Watch for changes in development
if (process.env.NODE_ENV === 'development') {
  console.log('Watching for route changes...');
  
  const watcher = chokidar.watch(['app/**/*.tsx', 'app/**/*.ts'], {
    ignored: /node_modules/,
    persistent: true
  });
  
  watcher.on('add', (filePath) => {
    console.log(`Route added: ${filePath}`);
    generateRoutesConfig();
  });
  
  watcher.on('unlink', (filePath) => {
    console.log(`Route removed: ${filePath}`);
    generateRoutesConfig();
  });
  
  watcher.on('change', (filePath) => {
    // Only regenerate if it's a new file structure change
    if (isStructuralChange(filePath)) {
      console.log(`Route structure changed: ${filePath}`);
      generateRoutesConfig();
    }
  });
}

function isStructuralChange(filePath) {
  // Check if this is a structural change (new file, renamed, etc.)
  // vs just content changes
  const fileName = path.basename(filePath);
  return fileName === 'index.tsx' || fileName === '_layout.tsx' || fileName.includes('[');
}

// Metro bundler plugin (for React Native)
function createMetroPlugin() {
  return {
    transform({ filename, options, src }) {
      // Regenerate routes when app directory changes
      if (filename.includes('/app/') && !filename.includes('generated')) {
        generateRoutesConfig();
      }
      return { ast: null };
    }
  };
}

// Next.js plugin (for web)
function createNextPlugin() {
  return (nextConfig = {}) => {
    return {
      ...nextConfig,
      webpack(config, options) {
        if (options.isServer && options.dev) {
          // Regenerate routes during development
          generateRoutesConfig();
        }
        
        if (typeof nextConfig.webpack === 'function') {
          return nextConfig.webpack(config, options);
        }
        
        return config;
      }
    };
  };
}

module.exports = {
  createMetroPlugin,
  createNextPlugin,
  generateRoutesConfig
};