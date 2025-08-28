# framework.py - Your custom framework
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse
import os
import importlib.util
import inspect
from functools import wraps
from typing import List, Callable, Dict, Any

class OnRamp:
    def __init__(self):
        self.routes: List[Route] = []
        self.middleware = []
        
    def route(self, path: str, methods: List[str] = None):
        """Decorator to register routes"""
        if methods is None:
            methods = ["GET"]
            
        def decorator(func):
            @wraps(func)
            async def wrapper(request):
                # Auto JSON serialization if function returns dict
                result = await func(request)
                if isinstance(result, dict):
                    return JSONResponse(result)
                return result
            
            self.routes.append(Route(path, wrapper, methods=methods))
            return func
        return decorator
    
    def get(self, path: str):
        """Shorthand for GET routes"""
        return self.route(path, ["GET"])
    
    def post(self, path: str):
        """Shorthand for POST routes"""
        return self.route(path, ["POST"])
    
    def put(self, path: str):
        """Shorthand for PUT routes"""
        return self.route(path, ["PUT"])
    
    def delete(self, path: str):
        """Shorthand for DELETE routes"""
        return self.route(path, ["DELETE"])
    
    def middleware(self, middleware_func):
        """Add middleware"""
        self.middleware.append(middleware_func)
        return middleware_func
    
    def discover_file_routes(self):
        """Keep your existing file-based routing"""
        routes_dir = os.path.join(os.getcwd(), 'routes')
        
        if not os.path.exists(routes_dir):
            return
        
        for filename in os.listdir(routes_dir):
            if filename.endswith('.py') and not filename.startswith('__'):
                module_name = filename[:-3]
                file_path = os.path.join(routes_dir, filename)
                
                try:
                    spec = importlib.util.spec_from_file_location(module_name, file_path)
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    
                    # Check if module uses the OnRamp instance
                    if hasattr(module, 'app') and hasattr(module.app, 'routes'):
                        self.routes.extend(module.app.routes)
                        print(f"Loaded decorated routes from {filename}")
                        continue
                    
                    # Fallback to your existing file-based system
                    if hasattr(module, 'ROUTES'):
                        for route_def in module.ROUTES:
                            path, handler_name, methods = route_def
                            if hasattr(module, handler_name):
                                handler = getattr(module, handler_name)
                                self.routes.append(Route(path, handler, methods=methods))
                    else:
                        # Single handler per file
                        if module_name == 'index':
                            route_path = "/"
                        else:
                            route_path = f"/{module_name}"
                        
                        handler = None
                        if hasattr(module, module_name):
                            handler = getattr(module, module_name)
                        elif hasattr(module, 'handler'):
                            handler = getattr(module, 'handler')
                        elif module_name == 'index' and hasattr(module, 'index'):
                            handler = getattr(module, 'index')
                        
                        if handler:
                            methods = getattr(handler, 'methods', ['GET'])
                            self.routes.append(Route(route_path, handler, methods=methods))
                            
                except Exception as e:
                    print(f"Error loading {filename}: {e}")
    
    def create_app(self):
        """Create the Starlette application"""
        self.discover_file_routes()
        return Starlette(routes=self.routes, middleware=self.middleware)

# Create a global instance for convenience
app = OnRamp()

# Convenience functions for global use
def route(path: str, methods: List[str] = None):
    return app.route(path, methods)

def get(path: str):
    return app.get(path)

def post(path: str):
    return app.post(path)

def put(path: str):
    return app.put(path)

def delete(path: str):
    return app.delete(path)

onramp = OnRamp()

# Create the ASGI app (this will auto-discover routes)
app = onramp.create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)