"""Compatibility entrypoint for local development."""
from backend.cascade.main import app
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.cascade.main:app", host="0.0.0.0", port=8000, reload=True)

