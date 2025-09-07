// Full navigation provider without Solito dependency
import React, { createContext, useContext, useState, useEffect } from 'react';
import { routes, routeComponents } from '../generated/routes';

// Platform detection that works across web and native
const Platform = {
  OS: typeof window !== 'undefined' ? 'web' : 'native'
};

interface NavigationContextType {
  currentRoute: string;
  params: Record<string, any>;
  navigate: (path: string, params?: Record<string, any>) => void;
  goBack: () => void;
  canGoBack: () => boolean;
}

const NavigationContext = createContext<NavigationContextType | null>(null);

interface NavigationProviderProps {
  children: React.ReactNode;
  initialRoute?: string;
}

export function NavigationProvider({ children, initialRoute = '/' }: NavigationProviderProps) {
  const [currentRoute, setCurrentRoute] = useState(initialRoute);
  const [params, setParams] = useState<Record<string, any>>({});
  const [history, setHistory] = useState<string[]>([initialRoute]);

  // Handle browser back/forward on web
  useEffect(() => {
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
      const handlePopState = () => {
        const path = window.location.pathname;
        const matchedRoute = matchRoute(path);
        if (matchedRoute) {
          setCurrentRoute(path);
          setParams(matchedRoute.params);
        }
      };

      window.addEventListener('popstate', handlePopState);
      
      // Set initial route from URL
      const initialPath = window.location.pathname;
      if (initialPath !== initialRoute) {
        const matchedRoute = matchRoute(initialPath);
        if (matchedRoute) {
          setCurrentRoute(initialPath);
          setParams(matchedRoute.params);
        }
      }

      return () => window.removeEventListener('popstate', handlePopState);
    }
  }, []);

  const matchRoute = (path: string) => {
    // First try exact match
    const exactRoute = routes.find(route => route.path === path);
    if (exactRoute) {
      return { route: exactRoute, params: {} };
    }

    // Try dynamic routes
    for (const route of routes) {
      if (route.isDynamic) {
        const params = matchDynamicRoute(path, route.path);
        if (params) {
          return { route, params };
        }
      }
    }

    return null;
  };

  const matchDynamicRoute = (pathname: string, routePath: string) => {
    const pathSegments = pathname.split('/').filter(Boolean);
    const routeSegments = routePath.split('/').filter(Boolean);

    if (pathSegments.length !== routeSegments.length) {
      return null;
    }

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
  };

  const navigate = (path: string, newParams: Record<string, any> = {}) => {
    const matchedRoute = matchRoute(path);
    if (!matchedRoute) {
      console.warn(`No route found for path: ${path}`);
      return;
    }

    setCurrentRoute(path);
    setParams({ ...matchedRoute.params, ...newParams });
    setHistory(prev => [...prev, path]);

    // Update browser URL on web
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
      window.history.pushState({}, '', path);
    }
  };

  const goBack = () => {
    if (history.length > 1) {
      const newHistory = [...history];
      newHistory.pop(); // Remove current route
      const previousRoute = newHistory[newHistory.length - 1];
      
      setHistory(newHistory);
      setCurrentRoute(previousRoute);
      
      const matchedRoute = matchRoute(previousRoute);
      setParams(matchedRoute?.params || {});

      if (Platform.OS === 'web' && typeof window !== 'undefined') {
        window.history.back();
      }
    }
  };

  const canGoBack = () => {
    return history.length > 1;
  };

  const contextValue: NavigationContextType = {
    currentRoute,
    params,
    navigate,
    goBack,
    canGoBack
  };

  return (
    <NavigationContext.Provider value={contextValue}>
      {children}
    </NavigationContext.Provider>
  );
}

export function useNavigation() {
  const context = useContext(NavigationContext);
  if (!context) {
    throw new Error('useNavigation must be used within NavigationProvider');
  }
  return context;
}

export function useParams<T = Record<string, string>>(): T {
  const { params } = useNavigation();
  return params as T;
}

export function useRoute() {
  const { currentRoute, params } = useNavigation();
  const routeConfig = routes.find(route => route.path === currentRoute);
  
  return {
    path: currentRoute,
    params,
    config: routeConfig
  };
}