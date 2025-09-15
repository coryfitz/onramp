// src/navigation/NavigationProvider.tsx
import React, { createContext, useContext, useMemo, useRef, useEffect, useState } from 'react';

type NavAPI = {
  currentRoute: string;
  params: Record<string, any>;
  navigate: (path: string, params?: Record<string, any>) => void;
  goBack: () => void;
  canGoBack: () => boolean;
};

const NavigationContext = createContext<NavAPI | null>(null);

function isWeb() {
  return typeof window !== 'undefined' && typeof document !== 'undefined';
}

export function NavigationProvider({
  children,
  initialRoute = '/',
}: {
  children: React.ReactNode;
  initialRoute?: string;
}) {
  // On web, hydrate from the real URL. On native, use provided initialRoute.
  const initialPath = isWeb() ? (window.location.pathname || '/') : initialRoute;

  const [currentRoute, setCurrentRoute] = useState<string>(initialPath);
  const paramsRef = useRef<Record<string, any>>({});
  const stackRef = useRef<string[]>([initialPath]); // used mainly for native

  // Keep route in sync with browser back/forward on web
  useEffect(() => {
    if (!isWeb()) return;
    const onPop = () => {
      const path = window.location.pathname || '/';
      paramsRef.current = {}; // popstate doesn’t carry params
      setCurrentRoute(path);
      // keep a simple stack in sync (best-effort)
      stackRef.current.push(path);
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const navigate = (path: string, params: Record<string, any> = {}) => {
    paramsRef.current = params;

    if (isWeb()) {
      // pushState keeps the URL bar in sync; RouteRegistry will decide if it exists or 404.
      if (window.location.pathname !== path) {
        window.history.pushState({}, '', path);
      }
      setCurrentRoute(path);
      stackRef.current.push(path);
      return;
    }

    // Native: just manage the in-memory stack/state
    stackRef.current.push(path);
    setCurrentRoute(path);
  };

  const goBack = () => {
    if (isWeb()) {
      // Let the browser handle it; popstate listener will update state.
      if (window.history.length > 1) {
        window.history.back();
      }
      return;
    }

    // Native
    if (stackRef.current.length > 1) {
      stackRef.current.pop();
      const prev = stackRef.current[stackRef.current.length - 1] || '/';
      paramsRef.current = {};
      setCurrentRoute(prev);
    }
  };

  const canGoBack = () => {
    if (isWeb()) return window.history.length > 1;
    return stackRef.current.length > 1;
  };

  const value = useMemo<NavAPI>(() => ({
    currentRoute,
    params: paramsRef.current,
    navigate,
    goBack,
    canGoBack,
  }), [currentRoute]);

  return <NavigationContext.Provider value={value}>{children}</NavigationContext.Provider>;
}

export function useNavigation() {
  const ctx = useContext(NavigationContext);
  if (!ctx) throw new Error('useNavigation must be used within NavigationProvider');
  return ctx;
}
