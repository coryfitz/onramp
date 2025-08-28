# framework.py - OnRamp: An Async-by-Default Web Framework
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse
import os
import importlib.util
import inspect
import asyncio
from functools import wraps
from typing import List

def sync(func):
    """Decorator to mark a function as intentionally synchronous"""
    func._onramp_sync = True
    return func

class OnRamp:
    """
    OnRamp is an async-by-default web framework.
    All route handlers are automatically treated as async, even if defined with 'def'.
    Use @sync decorator for intentionally synchronous handlers.
    """
    
    def __init__(self):
        self.routes: List[Route] = []
        
    def discover_file_routes(self):
        """Discover route handlers from files in the app/routes/api directory"""
        api_dir = os.path.join(os.getcwd(), 'app', 'routes', 'api')
        
        if not os.path.exists(api_dir):
            print("No app/routes/api directory found")
            return
        
        for filename in os.listdir(api_dir):
            if filename.endswith('.py') and not filename.startswith('__'):
                self._load_route_file(filename, api_dir)
    
    def _convert_response(self, result):
        """Convert Python returns to appropriate HTTP responses (Flask-style)"""
        from starlette.responses import PlainTextResponse, HTMLResponse
        
        # Return Response objects as-is
        if hasattr(result, 'status_code'):
            return result
            
        # Convert common Python types to appropriate responses
        if isinstance(result, dict):
            return JSONResponse(result)
        elif isinstance(result, str):
            # Check if it looks like HTML
            if result.strip().startswith('<') and result.strip().endswith('>'):
                return HTMLResponse(result)
            else:
                return PlainTextResponse(result)
        elif isinstance(result, (list, tuple)):
            # Convert lists/tuples to JSON
            return JSONResponse(result)
        elif isinstance(result, (int, float, bool)):
            # Convert primitives to JSON
            return JSONResponse(result)
        elif result is None:
            return PlainTextResponse("")
        else:
            # Fallback: convert to string
            return PlainTextResponse(str(result))
    
    def _make_async_handler(self, handler_func):
        """Convert a sync handler to async, or wrap async handler safely"""
        
        # Check if explicitly marked as sync
        if getattr(handler_func, '_onramp_sync', False):
            # Wrap sync function to run in thread pool
            @wraps(handler_func)
            async def sync_wrapper(request, params=None):
                loop = asyncio.get_event_loop()
                if params is not None:
                    result = await loop.run_in_executor(None, lambda: handler_func(request, params))
                else:
                    result = await loop.run_in_executor(None, lambda: handler_func(request))
                
                # Auto-convert responses like Flask
                return self._convert_response(result)
            return sync_wrapper
        
        # Check if already async
        if inspect.iscoroutinefunction(handler_func):
            # Already async, just wrap with JSON conversion
            @wraps(handler_func)
            async def async_wrapper(request, params=None):
                if params is not None:
                    result = await handler_func(request, params)
                else:
                    result = await handler_func(request)
                
                return self._convert_response(result)
            return async_wrapper
        
        # Regular function - make it async by default
        @wraps(handler_func)
        async def default_async_wrapper(request, params=None):
            # Run sync function in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            if params is not None:
                result = await loop.run_in_executor(None, lambda: handler_func(request, params))
            else:
                result = await loop.run_in_executor(None, lambda: handler_func(request))
            
            return self._convert_response(result)
        return default_async_wrapper
    
    def _load_route_file(self, filename, api_dir):
        """Load a single route file and register its handlers"""
        module_name = filename[:-3]  # Remove .py extension
        file_path = os.path.join(api_dir, filename)
        
        try:
            # Dynamically import the module
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Determine the route path from filename
            if module_name == 'index':
                route_path = "/"
            else:
                route_path = f"/{module_name}"
            
            # Check for dynamic route (contains brackets)
            if '[' in module_name and ']' in module_name:
                # Convert [id] to {id} for Starlette path parameters
                route_path = module_name.replace('[', '{').replace(']', '}')
                route_path = f"/{route_path}"
            
            # Find HTTP method handlers in the module
            supported_methods = []
            handlers = {}
            
            for method in ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS']:
                method_lower = method.lower()
                if hasattr(module, method_lower):
                    handler_func = getattr(module, method_lower)
                    if callable(handler_func):
                        supported_methods.append(method)
                        # Convert to async handler
                        handlers[method] = self._make_async_handler(handler_func)
            
            if handlers:
                # Create a unified handler that routes to appropriate method
                async def unified_handler(request):
                    method = request.method
                    if method in handlers:
                        handler = handlers[method]
                        
                        # Prepare parameters
                        params = request.path_params if request.path_params else {}
                        
                        # All handlers are now async, so we can always await
                        return await handler(request, params)
                    else:
                        return JSONResponse(
                            {"error": f"Method {method} not allowed"}, 
                            status_code=405
                        )
                
                self.routes.append(Route(route_path, unified_handler, methods=supported_methods))
                
                # Show which handlers are sync vs async for debugging
                handler_info = []
                for method in supported_methods:
                    method_lower = method.lower()
                    original_handler = getattr(module, method_lower)
                    if getattr(original_handler, '_onramp_sync', False):
                        handler_info.append(f"{method}(sync)")
                    elif inspect.iscoroutinefunction(original_handler):
                        handler_info.append(f"{method}(async)")
                    else:
                        handler_info.append(f"{method}(auto-async)")
                
                print(f"Registered route: {route_path} -> {filename} [{', '.join(handler_info)}]")
            else:
                print(f"Warning: No HTTP method handlers found in {filename}")
                
        except Exception as e:
            print(f"Error loading route from {filename}: {e}")
    
    def create_app(self):
        """Create the Starlette application"""
        self.discover_file_routes()
        return Starlette(routes=self.routes)# framework.py - Your custom framework
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse
import os
import importlib.util
import inspect
from typing import List

class OnRamp:
    def __init__(self):
        self.routes: List[Route] = []
        
    def discover_file_routes(self):
        """Discover routes from files in the routes directory"""
        routes_dir = os.path.join(os.getcwd(), 'routes')
        
        if not os.path.exists(routes_dir):
            print("No routes directory found")
            return
        
        for filename in os.listdir(routes_dir):
            if filename.endswith('.py') and not filename.startswith('__'):
                self._load_route_file(filename, routes_dir)
    
    def _load_route_file(self, filename, routes_dir):
        """Load a single route file and register its handlers"""
        module_name = filename[:-3]  # Remove .py extension
        file_path = os.path.join(routes_dir, filename)
        
        try:
            # Dynamically import the module
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Determine the route path from filename
            if module_name == 'index':
                route_path = "/"
            else:
                route_path = f"/{module_name}"
            
            # Check for dynamic route (contains brackets)
            if '[' in module_name and ']' in module_name:
                # Convert [id] to {id} for Starlette path parameters
                route_path = module_name.replace('[', '{').replace(']', '}')
                route_path = f"/{route_path}"
            
            # Find HTTP method handlers in the module
            supported_methods = []
            handlers = {}
            
            for method in ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS']:
                method_lower = method.lower()
                if hasattr(module, method_lower):
                    handler_func = getattr(module, method_lower)
                    if callable(handler_func):
                        supported_methods.append(method)
                        handlers[method] = handler_func
            
            if handlers:
                # Create a unified handler that routes to appropriate method
                async def unified_handler(request):
                    method = request.method
                    if method in handlers:
                        handler = handlers[method]
                        
                        # Prepare parameters
                        params = request.path_params if request.path_params else {}
                        
                        # Check if handler expects params argument
                        sig = inspect.signature(handler)
                        if len(sig.parameters) >= 2:
                            # Handler expects (request, params)
                            result = await handler(request, params)
                        else:
                            # Handler only expects (request)
                            result = await handler(request)
                        
                        # Auto-convert dict responses to JSON
                        if isinstance(result, dict):
                            return JSONResponse(result)
                        return result
                    else:
                        return JSONResponse(
                            {"error": f"Method {method} not allowed"}, 
                            status_code=405
                        )
                
                self.routes.append(Route(route_path, unified_handler, methods=supported_methods))
                print(f"Registered route: {route_path} -> {filename} [{', '.join(supported_methods)}]")
            else:
                print(f"Warning: No HTTP method handlers found in {filename}")
                
        except Exception as e:
            print(f"Error loading route from {filename}: {e}")
    
    def create_app(self):
        """Create the Starlette application"""
        self.discover_file_routes()
        return Starlette(routes=self.routes)