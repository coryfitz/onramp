import sys
sys.dont_write_bytecode = True

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.routing import Route
from starlette.responses import HTMLResponse, JSONResponse
import os
import importlib.util
import inspect
import asyncio
from functools import wraps
from pathlib import Path
from typing import List

from onramp.api import APIError, api_exception_handler
from onramp.api_explorer import api_explorer_html, build_openapi_document
from onramp.db.manager import (
    database_is_ready,
    database_lifespan,
    get_db_manager,
)


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
    
    def __init__(self, app_dir=None):
        self.routes: List[Route] = []
        self.api_operations = []
        # Allow explicit app_dir to be passed, otherwise discover it
        self.app_dir = app_dir or self._find_app_directory()
        
    def _find_app_directory(self):
        """Find the app directory, checking common locations"""
        current_dir = os.getcwd()
        
        # Case 1: We're running from the project root (myapp/)
        # Look for myapp/app/
        app_dir_from_root = os.path.join(current_dir, 'app')
        if os.path.exists(app_dir_from_root) and os.path.exists(os.path.join(app_dir_from_root, 'api')):
            return app_dir_from_root
            
        # Case 2: We're running from inside the app directory (myapp/app/)
        # In this case, current directory IS the app directory
        if os.path.exists(os.path.join(current_dir, 'api')):
            return current_dir
            
        # Case 3: Try to find it by looking at where app.py is located
        frame = sys._getframe(1)
        while frame:
            frame_filename = frame.f_code.co_filename
            if frame_filename.endswith('app.py'):
                # Found the app.py file, its directory should be the app directory
                app_dir_from_frame = os.path.dirname(os.path.abspath(frame_filename))
                if os.path.exists(os.path.join(app_dir_from_frame, 'api')):
                    return app_dir_from_frame
            frame = frame.f_back
        
        # Case 4: Try parent directory (in case we're in app/ and need to go up to find app/)
        parent_dir = os.path.dirname(current_dir)
        app_dir_from_parent = os.path.join(parent_dir, 'app')
        if os.path.exists(app_dir_from_parent) and os.path.exists(os.path.join(app_dir_from_parent, 'api')):
            return app_dir_from_parent
        
        # Fallback: assume current directory is the app directory
        return current_dir
        
    def discover_file_routes(self):
        """Discover route handlers from files in the api directory"""
        api_dir = os.path.join(self.app_dir, 'api')
        
        if not os.path.exists(api_dir):
            print(f"No api directory found at {api_dir}")
            print(f"App directory: {self.app_dir}")
            return
        
        # Import application modules through the real ``app`` package. Adding
        # app/ itself would make files such as app/http.py shadow Python's
        # standard-library http package.
        project_root = os.path.dirname(os.path.abspath(self.app_dir))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        
        route_files = [
            path
            for path in Path(api_dir).rglob("*.py")
            if not any(part.startswith("__") for part in path.relative_to(api_dir).parts)
        ]
        route_files.sort(
            key=lambda path: (
                path.relative_to(api_dir).parts != ("index.py",),
                "[" in path.relative_to(api_dir).as_posix(),
                path.relative_to(api_dir).as_posix(),
            )
        )
        for route_file in route_files:
            self._load_route_file(route_file.relative_to(api_dir), api_dir)
    
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
    
    def _load_route_file(self, relative_path, api_dir):
        """Load a single route file and register its handlers"""
        relative_path = Path(relative_path)
        module_parts = list(relative_path.with_suffix("").parts)
        module_name = "/".join(module_parts)
        file_path = os.path.join(api_dir, *relative_path.parts)
        display_path = relative_path.as_posix()
        
        try:
            # Create a unique module name to avoid conflicts
            safe_module_name = "_".join(module_parts)
            unique_module_name = f"api_{safe_module_name}_{id(self)}"
            
            # Dynamically import the module
            spec = importlib.util.spec_from_file_location(unique_module_name, file_path)
            if spec is None:
                print(f"Could not create spec for {file_path}")
                return
                
            module = importlib.util.module_from_spec(spec)
            
            # Add to sys.modules to make imports work correctly
            sys.modules[unique_module_name] = module
            
            try:
                spec.loader.exec_module(module)
            except Exception as e:
                # Clean up on failure
                if unique_module_name in sys.modules:
                    del sys.modules[unique_module_name]
                raise e
            
            # A nested index maps to its directory. Bracketed file or directory
            # segments become Starlette path parameters.
            route_parts = module_parts[:-1] if module_parts[-1] == "index" else module_parts
            route_parts = [
                part.replace("[", "{").replace("]", "}")
                for part in route_parts
            ]
            route_path = "/api"
            if route_parts:
                route_path += "/" + "/".join(route_parts)
            
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
                        if (
                            route_path == "/api"
                            and method == "GET"
                            and self._wants_api_explorer(request)
                        ):
                            return self._api_explorer_response()

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

                for method in supported_methods:
                    self.api_operations.append(
                        self._describe_operation(
                            route_path,
                            method,
                            module_name,
                            getattr(module, method.lower()),
                        )
                    )
                
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
                
                print(f"Registered route: {route_path} -> {display_path} [{', '.join(handler_info)}]")
            else:
                print(f"Warning: No HTTP method handlers found in {display_path}")
                
        except Exception as e:
            print(f"Error loading route from {display_path}: {e}")
            import traceback
            traceback.print_exc()

    def _describe_operation(self, path, method, module_name, handler):
        """Create API explorer metadata for a discovered route handler."""
        docstring = inspect.getdoc(handler) or ""
        route_name = "API root" if module_name == "index" else self._humanize(module_name)
        summary = (
            docstring.splitlines()[0]
            if docstring
            else f"{method.title()} {route_name}"
        )
        description = docstring or f"{method} request to {path}."
        safe_name = "".join(
            character if character.isalnum() else "_"
            for character in module_name
        ).strip("_")
        return {
            "path": path,
            "method": method,
            "tag": (
                "default"
                if module_name == "index"
                else self._humanize(module_name.split("/", 1)[0])
            ),
            "summary": summary,
            "description": description,
            "operation_id": f"{method.lower()}_{safe_name or 'route'}",
        }

    @staticmethod
    def _humanize(value):
        return (
            value.replace("/", " ")
            .replace("_", " ")
            .replace("-", " ")
            .strip()
            .title()
        )

    @staticmethod
    def _wants_api_explorer(request):
        """Show docs for browser navigation while preserving the root API route."""
        if request.query_params.get("raw", "").lower() in {"1", "true", "yes"}:
            return False
        if request.query_params.get("docs", "").lower() in {"1", "true", "yes"}:
            return True
        return "text/html" in request.headers.get("accept", "").lower()

    def _api_explorer_response(self, _request=None):
        return HTMLResponse(api_explorer_html())

    def _openapi_response(self, _request):
        return JSONResponse(build_openapi_document(self.api_operations))

    def _brand_logo_response(self, _request):
        from starlette.responses import FileResponse

        return FileResponse(
            Path(__file__).resolve().parent / "static" / "logo.png",
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    async def _liveness_response(self, _request):
        return JSONResponse({"status": "ok"})

    async def _readiness_response(self, _request):
        ready = await database_is_ready()
        return JSONResponse(
            {"status": "ready" if ready else "unavailable"},
            status_code=200 if ready else 503,
        )

    def _middleware(self):
        manager = get_db_manager(self.app_dir)
        middleware = [
            Middleware(
                TrustedHostMiddleware,
                allowed_hosts=manager.allowed_hosts(),
            )
        ]
        origins = manager.cors_allowed_origins()
        if origins:
            middleware.append(
                Middleware(
                    CORSMiddleware,
                    allow_origins=origins,
                    allow_credentials=manager.cors_allow_credentials(),
                    allow_methods=["*"],
                    allow_headers=["*"],
                )
            )
        return middleware
    
    def create_app(self):
        """Create the Starlette application"""
        self.discover_file_routes()
        from onramp.auth.config import auth_enabled
        from onramp.auth.routes import auth_routes

        built_in_routes = auth_routes(self.app_dir) if auth_enabled(self.app_dir) else []
        has_api_get = any(
            operation["path"] == "/api" and operation["method"] == "GET"
            for operation in self.api_operations
        )
        explorer_routes = [
            Route("/health/live", self._liveness_response, methods=["GET"]),
            Route("/health/ready", self._readiness_response, methods=["GET"]),
            Route("/favicon.ico", self._brand_logo_response, methods=["GET"]),
            Route("/api/onramp-logo.png", self._brand_logo_response, methods=["GET"]),
            Route("/api/openapi.json", self._openapi_response, methods=["GET"]),
        ]
        if not has_api_get:
            explorer_routes.append(
                Route("/api", self._api_explorer_response, methods=["GET"])
            )
        return Starlette(
            routes=[*explorer_routes, *built_in_routes, *self.routes],
            lifespan=database_lifespan(self.app_dir),
            middleware=self._middleware(),
            exception_handlers={APIError: api_exception_handler},
        )


# Create your OnRamp app instance
onramp = OnRamp()

# Create the ASGI app (this will auto-discover routes from app/api/)
app = onramp.create_app()

# For backward compatibility when running locally
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
