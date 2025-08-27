from starlette.responses import JSONResponse

async def index(request):
    """Handler for the root path /"""
    return JSONResponse({"message": "Hello World", "status": "API is running"})

# You can add more handlers in the same file if needed
async def health(request):
    return JSONResponse({"status": "healthy"})