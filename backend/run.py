import os
import uvicorn

if __name__ == "__main__":
    # Local dev entrypoint - reload=True is intentional here and fine for local use.
    # Production (Render) does not invoke this file; see Dockerfile's CMD, which runs
    # uvicorn directly without reload and binds to Render's dynamically assigned $PORT.
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
