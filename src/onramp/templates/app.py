from starlette.applications import Starlette
from starlette.routing import Route
import os
import importlib.util
import inspect

def discover_routes():
    """Automatically discover and create routes from files in the routes directory."""
    routes = []
    routes_dir = os.path.join(os.path.dirname(__file__), 'routes')
    
    if not os.path.exists(routes_dir):
        print("No routes directory found")
        return routes
    
    # Get all Python files in the routes directory
    for filename in os.listdir(routes_dir):
        if filename.endswith('.py') and not filename.startswith('__'):
            module_name = filename[:-3]  # Remove .py extension
            file_path = os.path.join(routes_dir, filename)
            
            try:
                # Dynamically import the module
                spec = importlib.util.spec_from_file_location(module_name, file_path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                
                # Determine the route path
                if module_name == 'index':
                    route_path = "/"
                else:
                    route_path = f"/{module_name}"
                
                # Look for route handlers in the module
                # Check for common handler names or functions that look like route handlers
                handler = None
                
                # First, try to find a function with the same name as the module
                if hasattr(module, module_name):
                    handler = getattr(module, module_name)
                # Then try common names like 'handler', 'main', or 'route'
                elif hasattr(module, 'handler'):
                    handler = getattr(module, 'handler')
                elif hasattr(module, 'main'):
                    handler = getattr(module, 'main')
                elif hasattr(module, 'route'):
                    handler = getattr(module, 'route')
                # For index.py, also try 'index'
                elif module_name == 'index' and hasattr(module, 'index'):
                    handler = getattr(module, 'index')
                else:
                    # Find the first async function that looks like a route handler
                    for name, obj in inspect.getmembers(module):
                        if (inspect.iscoroutinefunction(obj) and 
                            not name.startswith('_') and 
                            len(inspect.signature(obj).parameters) >= 1):
                            handler = obj
                            break
                
                if handler and callable(handler):
                    # Check if the handler supports multiple HTTP methods
                    methods = getattr(handler, 'methods', ['GET'])
                    routes.append(Route(route_path, handler, methods=methods))
                    print(f"Registered route: {route_path} -> {module_name}.{handler.__name__}")
                else:
                    print(f"Warning: No valid handler found in {filename}")
                    
            except Exception as e:
                print(f"Error loading route from {filename}: {e}")
    
    return routes

# Discover and register all routes
routes = discover_routes()

# Create the ASGI app
app = Starlette(routes=routes)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)