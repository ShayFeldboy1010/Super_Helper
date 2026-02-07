import os
import sys

# Add project root to sys.path just in case
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from app.main import app
except Exception as e:
    import traceback
    error_trace = traceback.format_exc()
    print(f"Startup Error: {error_trace}")
    
    # Fallback app to show error in browser
    from fastapi import FastAPI
    app = FastAPI()
    
    @app.get("/{path:path}")
    async def catch_all(path: str):
        return {"status": "error", "message": "Failed to start application", "trace": error_trace.split("\n")}
