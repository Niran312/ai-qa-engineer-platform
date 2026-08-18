import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    DATABASE_URL: str = "sqlite:///./qa_platform.db"

    # Comma-separated list of origins allowed to call this API (CORS). "*" allows all origins -
    # fine for local development, should be restricted to the real frontend domain(s) in production.
    ALLOWED_ORIGINS: str = os.getenv("ALLOWED_ORIGINS", "*")


    BASE_DIR: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # Static files directories
    STATIC_DIR: str = os.path.join(BASE_DIR, "static")
    SCREENSHOTS_DIR: str = os.path.join(STATIC_DIR, "screenshots")
    DOWNLOADS_DIR: str = os.path.join(STATIC_DIR, "downloads")
    STORAGE_STATE_DIR: str = os.path.join(STATIC_DIR, "storage_state")
    
    # Crawl settings
    MAX_CRAWL_DEPTH: int = 3
    MAX_CRAWLED_PAGES: int = 15

    # MCP & LLM Timeout Settings (in seconds)
    MCP_PROCESS_STARTUP_TIMEOUT: float = 30.0
    MCP_SESSION_TIMEOUT: float = 30.0
    MCP_TOOL_DISCOVERY_TIMEOUT: float = 15.0
    MCP_BROWSER_TIMEOUT: float = 30.0
    MCP_NAVIGATION_TIMEOUT: float = 30.0
    MCP_TOOL_CALL_TIMEOUT: float = 30.0
    MCP_LLM_TIMEOUT: float = 60.0
    MCP_FEATURE_TIMEOUT: float = 120.0

    class Config:
        env_file = ".env"

settings = Settings()

# Ensure directories exist
os.makedirs(settings.STATIC_DIR, exist_ok=True)
os.makedirs(settings.SCREENSHOTS_DIR, exist_ok=True)
os.makedirs(settings.DOWNLOADS_DIR, exist_ok=True)
os.makedirs(settings.STORAGE_STATE_DIR, exist_ok=True)
