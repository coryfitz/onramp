// Route registry and component loader
import React, { Suspense, lazy, ComponentType, useState, useEffect } from 'react';
import { routes, routeComponents } from '../generated/routes';
import { html } from 'react-strict-dom';

// Cache for loaded components
const componentCache = new Map<string, ComponentType<any>>();

// Route registry class
class RouteRegistry {
  private routes = new Map(routes.map(route => [route.path, route]));
  
  // Get route configuration by path
  getRoute(path: string) {
    return this.routes.get(path);
  }
  
  // Match dynamic routes
  matchRoute(pathname: string) {
    // First try exact match
    const exactMatch = this.routes.get(pathname);
    if (exactMatch) return { route: exactMatch, params: {} };
    
    // Try dynamic routes
    for (const [routePath, routeConfig] of this.routes) {
      if (routeConfig.isDynamic) {
        const params = this.matchDynamicRoute(pathname, routePath);
        if (params) {
          return { route: routeConfig, params };
        }
      }
    }
    
    return null;
  }
  
  private matchDynamicRoute(pathname: string, routePath: string) {
    const pathSegments = pathname.split('/').filter(Boolean);
    const routeSegments = routePath.split('/').filter(Boolean);
    
    if (pathSegments.length !== routeSegments.length) return null;
    
    const params: Record<string, string> = {};
    
    for (let i = 0; i < routeSegments.length; i++) {
      const routeSegment = routeSegments[i];
      const pathSegment = pathSegments[i];
      
      if (routeSegment.startsWith(':')) {
        // Dynamic segment
        const paramName = routeSegment.slice(1);
        params[paramName] = pathSegment;
      } else if (routeSegment !== pathSegment) {
        // Static segment doesn't match
        return null;
      }
    }
    
    return params;
  }
  
  // Load component for route
  async loadComponent(componentPath: string): Promise<ComponentType<any>> {
    // Check cache first
    if (componentCache.has(componentPath)) {
      return componentCache.get(componentPath)!;
    }
    
    // Load component dynamically
    const importFn = routeComponents[componentPath];
    if (!importFn) {
      throw new Error(`Component not found: ${componentPath}`);
    }
    
    const module = await importFn();
    const Component = module.default || module;
    
    // Cache the component
    componentCache.set(componentPath, Component);
    
    return Component;
  }
  
  // Create lazy component
  createLazyComponent(componentPath: string) {
    return lazy(() => 
      this.loadComponent(componentPath).then(Component => ({ default: Component }))
    );
  }
}

// Singleton instance
export const routeRegistry = new RouteRegistry();

// Loading component
function LoadingScreen() {
  return (
    <html.div style={{
      display: 'flex',
      justifyContent: 'center',
      alignItems: 'center',
      minHeight: '100vh',
      fontFamily: 'system-ui, -apple-system, sans-serif'
    }}>
      <html.div style={{
        textAlign: 'center'
      }}>
        <html.div style={{
          fontSize: 18,
          marginBottom: 8
        }}>
          Loading...
        </html.div>
        <html.div style={{
          fontSize: 14,
          color: '#666'
        }}>
          Please wait
        </html.div>
      </html.div>
    </html.div>
  );
}

// 404 Not Found component
function NotFoundScreen({ path }: { path: string }) {
  return (
    <html.div style={{
      display: 'flex',
      justifyContent: 'center',
      alignItems: 'center',
      minHeight: '100vh',
      fontFamily: 'system-ui, -apple-system, sans-serif',
      backgroundColor: '#f5f5f5'
    }}>
      <html.div style={{
        textAlign: 'center',
        backgroundColor: 'white',
        padding: 40,
        borderRadius: 12,
        boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
        maxWidth: 500
      }}>
        <html.h1 style={{
          fontSize: 48,
          margin: 0,
          marginBottom: 16,
          color: '#333'
        }}>
          404
        </html.h1>
        <html.h2 style={{
          fontSize: 24,
          margin: 0,
          marginBottom: 16,
          color: '#666'
        }}>
          Page Not Found
        </html.h2>
        <html.p style={{
          fontSize: 16,
          color: '#888',
          marginBottom: 24
        }}>
          The page "{path}" could not be found.
        </html.p>
        <html.button 
          onClick={() => {
            if (typeof window !== 'undefined') {
              window.location.href = '/';
            }
          }}
          style={{
            padding: '12px 24px',
            backgroundColor: '#007AFF',
            color: 'white',
            border: 'none',
            borderRadius: 8,
            fontSize: 16,
            cursor: 'pointer'
          }}
        >
          Go Home
        </html.button>
      </html.div>
    </html.div>
  );
}

// Component that renders a route
interface RouteComponentProps {
  path: string;
  params?: Record<string, any>;
}

export function RouteComponent({ path, params = {} }: RouteComponentProps) {
  const [Component, setComponent] = useState<ComponentType<any> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const loadRoute = async () => {
      setLoading(true);
      setError(null);

      try {
        const match = routeRegistry.matchRoute(path);
        
        if (!match) {
          if (!cancelled) {
            setComponent(null);
            setLoading(false);
          }
          return;
        }

        const { route } = match;
        const loadedComponent = await routeRegistry.loadComponent(route.componentPath);
        
        if (!cancelled) {
          setComponent(() => loadedComponent);
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load component');
          setLoading(false);
        }
      }
    };

    loadRoute();

    return () => {
      cancelled = true;
    };
  }, [path]);

  if (loading) {
    return <LoadingScreen />;
  }

  if (error) {
    return (
      <html.div style={{
        padding: 20,
        color: 'red',
        fontFamily: 'system-ui, -apple-system, sans-serif'
      }}>
        <html.h2>Error Loading Route</html.h2>
        <html.p>{error}</html.p>
      </html.div>
    );
  }

  if (!Component) {
    return <NotFoundScreen path={path} />;
  }

  // Merge route params with passed params
  const match = routeRegistry.matchRoute(path);
  const allParams = { ...match?.params, ...params };

  return <Component {...allParams} />;
}

// Hook to get route information
export function useRouteMatch(path: string) {
  return routeRegistry.matchRoute(path);
}

// Preload route component
export function preloadRoute(path: string) {
  const route = routeRegistry.getRoute(path);
  if (route) {
    return routeRegistry.loadComponent(route.componentPath);
  }
  return Promise.resolve(null);
}