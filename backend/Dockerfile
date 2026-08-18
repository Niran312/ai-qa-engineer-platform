FROM python:3.12-slim-bookworm

# Node.js is required for `npx @playwright/mcp` (app/mcp/playwright_client.py spawns it as a
# subprocess) - Render's native Python runtime does not include Node, so a Dockerfile is used
# instead to get full control over the image.
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg ca-certificates && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Installs Chromium plus the system libraries it needs to run headless (fonts, libnss3, etc.) -
# without --with-deps, headless Chromium fails to launch on a minimal base image like this one.
RUN playwright install --with-deps chromium

COPY . .

ENV PORT=8000
EXPOSE 8000

# Production entrypoint - no --reload (that's run.py's job for local dev only), binds to
# Render's dynamically assigned $PORT.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
