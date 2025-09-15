// build/generateRoutes.js
const fs = require('fs');
const path = require('path');

function getPlatform() {
  // Allow explicit override (handy for CI)
  if (process.env.ONRAMP_PLATFORM) return process.env.ONRAMP_PLATFORM;

  // Heuristics:
  const nlel = (process.env.npm_lifecycle_event || '').toLowerCase();

  // Web: our dev server script is `start:web`, and webpack serve/build is only used on web.
  if (nlel.includes('start:web') || process.env.WEBPACK_SERVE || process.env.WEBPACK_DEV_SERVER) {
    return 'web';
  }

  // iOS / Android: common lifecycles started by RN CLI or wrappers
  if (nlel.includes('ios')) return 'ios';
  if (nlel.includes('android')) return 'android';

  // When Metro is started directly (e.g. `start:native`) treat as generic native
  return 'native';
}

function extOrderFor(platform) {
  switch (platform) {
    case 'web':
      return ['.web.tsx', '.web.ts', '.tsx', '.ts', '.web.jsx', '.jsx', '.web.js', '.js'];
    case 'ios':
      return ['.ios.tsx', '.ios.ts', '.native.tsx', '.native.ts', '.tsx', '.ts', '.ios.jsx', '.native.jsx', '.jsx', '.ios.js', '.native.js', '.js'];
    case 'android':
      return ['.android.tsx', '.android.ts', '.native.tsx', '.native.ts', '.tsx', '.ts', '.android.jsx', '.native.jsx', '.jsx', '.android.js', '.native.js', '.js'];
    case 'native':
    default:
      return ['.native.tsx', '.native.ts', '.tsx', '.ts', '.native.jsx', '.jsx', '.native.js', '.js'];
  }
}

function isPageFile(file) {
  // Page components only: .ts/.tsx/.js/.jsx
  return /\.(tsx|ts|jsx|js)$/.test(file);
}

// Turn a file path inside app/ into a route path.
// e.g. "app/about.tsx" -> "/about"
//      "app/profile/[id].tsx" -> "/profile/:id"
//      "app/index.tsx" -> "/index" (we will also alias "/" later)
function toRoutePath(appRelFile) {
  const noExt = appRelFile.replace(/\.(tsx|ts|jsx|js)$/, '');

  // Normalize segments and convert bracket params to :params
  const segments = noExt.split(path.sep).map(s => {
    if (s === 'index' && noExt !== 'index') return 'index'; // keep explicit "index" for aliasing
    if (/^\[.+\]$/.test(s)) return `:${s.slice(1, -1)}`;
    return s;
  });

  if (segments.length === 1 && segments[0] === 'index') {
    return '/index';
  }
  return '/' + segments.join('/').replace(/\/index$/, '/index'); // keep explicit /index
}

// Prefer the correct platform variant for a given logical page (route "stem")
function resolvePlatformFile(variants, platform) {
  const order = extOrderFor(platform);
  for (const ext of order) {
    const match = variants.find(v => v.endsWith(ext));
    if (match) return match;
  }
  return null;
}

// Scan app/ and group logical pages by stem (e.g., "about", "profile/[id]")
function scanApp(appDir) {
  const all = [];
  const walk = dir => {
    for (const name of fs.readdirSync(dir)) {
      const full = path.join(dir, name);
      const stat = fs.statSync(full);
      if (stat.isDirectory()) {
        walk(full);
      } else if (isPageFile(name)) {
        // Store path relative to app/
        all.push(path.relative(appDir, full));
      }
    }
  };
  walk(appDir);

  // Group by "stem" (remove extension)
  const byStem = new Map();
  for (const rel of all) {
    const stem = rel.replace(/\.(tsx|ts|jsx|js)$/, '');
    if (!byStem.has(stem)) byStem.set(stem, []);
    byStem.get(stem).push(rel);
  }
  return byStem;
}

function isDynamicRoute(routePath) {
  return routePath.split('/').some(seg => seg.startsWith(':'));
}

// Build the import specifier relative to src/generated/routes.ts
function buildImportPath(projectRoot, chosenRelFromApp) {
  // routes.ts is at "<root>/src/generated/routes.ts"
  // our files are at "<root>/app/<...>"
  // so relative path should be "../../app/<...>" (posix-style for consistency)
  const from = path.join(projectRoot, 'src', 'generated', 'routes.ts');
  const target = path.join(projectRoot, 'app', chosenRelFromApp);
  let rel = path.relative(path.dirname(from), target);
  // Drop extension for cleaner dynamic import and compatibility
  rel = rel.replace(/\.(tsx|ts|jsx|js)$/, '');
  return rel.split(path.sep).join('/'); // posix separators
}

function generateRoutesConfig() {
  const projectRoot = process.cwd();
  const appDir = path.join(projectRoot, 'app');
  const outDir = path.join(projectRoot, 'src', 'generated');
  const outFile = path.join(outDir, 'routes.ts');

  if (!fs.existsSync(appDir)) {
    console.error(`No "app" directory found at ${appDir}`);
    process.exit(1);
  }
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });

  const platform = getPlatform();
  const byStem = scanApp(appDir);

  const routes = [];
  const routeComponents = {};

  // Helper to push a route
  const addRoute = (routePath, importPath) => {
    const isDyn = isDynamicRoute(routePath);
    // Use the importPath (without extension) as a stable component key
    const componentKey = importPath;

    if (!routeComponents[componentKey]) {
      routeComponents[componentKey] = `() => import('${importPath}')`;
    }

    routes.push({
      path: routePath,
      componentPath: componentKey,
      isDynamic: isDyn,
    });
  };

  // Pass 1: generate entries for all pages that exist for the current platform
  for (const [stem, files] of byStem) {
    // Choose the best file for this platform
    const chosen = resolvePlatformFile(files, platform);
    if (!chosen) {
      // If there is no platform-appropriate or base file, skip and warn
      console.warn(
        `[routes] Skipping "${stem}" for platform "${platform}" (no matching file among: ${files.join(
          ', '
        )})`
      );
      continue;
    }

    const routePath = toRoutePath(chosen); // chosen is app-relative with extension
    const importPath = buildImportPath(projectRoot, chosen); // path without extension

    addRoute(routePath, importPath);

    // If this is "/index", also add "/" using the exact same importPath
    if (routePath === '/index') {
      addRoute('/', importPath);
    }
  }

  // Sort routes so "/" comes first, then static, then dynamic; helps your registry’s first-match check
  routes.sort((a, b) => {
    if (a.path === '/' && b.path !== '/') return -1;
    if (b.path === '/' && a.path !== '/') return 1;
    if (a.isDynamic && !b.isDynamic) return 1;
    if (b.isDynamic && !a.isDynamic) return -1;
    return a.path.localeCompare(b.path);
  });

  // Emit file
  const file =
    `// AUTO-GENERATED by generateRoutes.js — do not edit\n` +
    `// Platform: ${platform}\n` +
    `export const routes = ${JSON.stringify(routes, null, 2)} as const;\n\n` +
    `export const routeComponents: Record<string, () => Promise<any>> = {\n` +
    Object.entries(routeComponents)
      .map(([k, v]) => `  ${JSON.stringify(k)}: ${v},`)
      .join('\n') +
    `\n};\n`;

  fs.writeFileSync(outFile, file, 'utf8');
  console.log(`Generated routes configuration at src/generated/routes.ts`);
  console.log(`Found ${routes.length} routes`);
}

module.exports = { generateRoutesConfig };
