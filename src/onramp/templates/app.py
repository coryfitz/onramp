# app.py - OnRamp: An Async-by-Default Web Framework
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
        # Get the directory where this app.py file is located
        current_dir = os.path.dirname(os.path.abspath(__file__))
        api_dir = os.path.join(current_dir, 'routes', 'api')
        
        if not os.path.exists(api_dir):
            print(f"No routes/api directory found at {api_dir}")
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
        
        # Get the function signature to determine what parameters it expects
        sig = inspect.signature(handler_func)
        param_count = len(sig.parameters)
        
        # Check if explicitly marked as sync
        if getattr(handler_func, '_onramp_sync', False):
            # Wrap sync function to run in thread pool
            @wraps(handler_func)
            async def sync_wrapper(request, params=None):
                loop = asyncio.get_event_loop()
                
                # Call with appropriate number of arguments
                if param_count == 0:
                    result = await loop.run_in_executor(None, lambda: handler_func())
                elif param_count == 1:
                    result = await loop.run_in_executor(None, lambda: handler_func(request))
                else:
                    result = await loop.run_in_executor(None, lambda: handler_func(request, params))
                
                return self._convert_response(result)
            return sync_wrapper
        
        # Check if already async
        if inspect.iscoroutinefunction(handler_func):
            # Already async, just wrap with response conversion
            @wraps(handler_func)
            async def async_wrapper(request, params=None):
                # Call with appropriate number of arguments
                if param_count == 0:
                    result = await handler_func()
                elif param_count == 1:
                    result = await handler_func(request)
                else:
                    result = await handler_func(request, params)
                
                return self._convert_response(result)
            return async_wrapper
        
        # Regular function - make it async by default
        @wraps(handler_func)
        async def default_async_wrapper(request, params=None):
            # Run sync function in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            
            # Call with appropriate number of arguments
            if param_count == 0:
                result = await loop.run_in_executor(None, lambda: handler_func())
            elif param_count == 1:
                result = await loop.run_in_executor(None, lambda: handler_func(request))
            else:
                result = await loop.run_in_executor(None, lambda: handler_func(request, params))
            
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
            
            # Determine the route path from filename with /api prefix
            if module_name == 'index':
                route_path = "/api"
            else:
                route_path = f"/api/{module_name}"
            
            # Check for dynamic route (contains brackets)
            if '[' in module_name and ']' in module_name:
                # Convert [id] to {id} for Starlette path parameters
                route_path = module_name.replace('[', '{').replace(']', '}')
                route_path = f"/api/{route_path}"
            
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
        return Starlette(routes=self.routes)

# Create your OnRamp app instance
onramp = OnRamp()

# Create the ASGI app (this will auto-discover routes from routes/api/)
app = onramp.create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)