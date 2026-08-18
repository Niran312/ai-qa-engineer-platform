import asyncio
import time
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from ..config import settings
from ..database import add_scan_log, update_scan_perf_data, save_completed_stage_dur

class PlaywrightMCPClient:
    """Standard client manager to interface with the Node-based @playwright/mcp server using Node 20."""
    
    def __init__(self, storage_state_path: str = None):
        # Dynamically launch using Node 20 and pass headless execution parameters to avoid hangs in server environments
        args = ["-y", "-p", "node@20", "-p", "@playwright/mcp@latest", "playwright-mcp", "--headless", "--no-sandbox", "--isolated"]
        if storage_state_path:
            # Reuse the crawler's already-authenticated session (cookies/localStorage) instead
            # of the MCP browser having to run its own from-scratch login flow.
            args.append(f"--storage-state={storage_state_path}")
        self.server_params = StdioServerParameters(
            command="npx",
            args=args
        )
        self._client_ctx = None
        self.session = None

    async def connect(self, scan_id: str = None):
        """Spawns the MCP server subprocess and connects standard stdio channels with hard timeouts."""
        try:
            p_start = time.perf_counter()
            if scan_id:
                add_scan_log(scan_id, "[MCP] [START] Starting Playwright MCP process")
                update_scan_perf_data(scan_id, {"stage": "MCP Process Startup", "stage_started_at": time.time() * 1000})
            
            self._client_ctx = stdio_client(self.server_params)
            
            s_start = time.perf_counter()
            if scan_id:
                add_scan_log(scan_id, "[MCP] [START] Connecting stdio transport")
                update_scan_perf_data(scan_id, {"stage": "MCP Session Initialization", "stage_started_at": time.time() * 1000})
                
            read, write = await asyncio.wait_for(
                self._client_ctx.__aenter__(), 
                timeout=settings.MCP_PROCESS_STARTUP_TIMEOUT
            )
            
            p_dur = time.perf_counter() - p_start
            if scan_id:
                add_scan_log(scan_id, f"[MCP] [END] Starting Playwright MCP process | duration={p_dur:.2f}s")
                add_scan_log(scan_id, f"[MCP] [END] Connecting stdio transport | duration={time.perf_counter() - s_start:.2f}s")
                save_completed_stage_dur(scan_id, "MCP Process Startup", p_dur)
                add_scan_log(scan_id, "[MCP] [START] MCP session initialization")
            
            self.session = ClientSession(read, write)
            await asyncio.wait_for(
                self.session.__aenter__(), 
                timeout=settings.MCP_SESSION_TIMEOUT
            )
            await asyncio.wait_for(
                self.session.initialize(), 
                timeout=settings.MCP_SESSION_TIMEOUT
            )
            
            if scan_id:
                session_dur = time.perf_counter() - s_start
                add_scan_log(scan_id, f"[MCP] [END] MCP session initialization | duration={session_dur:.2f}s")
                save_completed_stage_dur(scan_id, "MCP Session Initialization", session_dur)
                
        except asyncio.TimeoutError as e:
            if scan_id:
                add_scan_log(scan_id, f"[MCP] [ERROR] MCP session initialization timed out after {settings.MCP_SESSION_TIMEOUT}s")
            await self.disconnect()
            raise TimeoutError("Playwright MCP connection or initialization handshakes timed out.") from e
        except Exception as e:
            if scan_id:
                add_scan_log(scan_id, f"[MCP] [ERROR] MCP subprocess startup failed: {str(e)}")
            await self.disconnect()
            raise e

    async def call_tool(self, name: str, arguments: dict = None) -> str:
        """Call a specific tool on the Playwright MCP server with timeout constraints."""
        if not self.session:
            raise RuntimeError("Playwright MCP Client is not connected.")
        
        try:
            result = await asyncio.wait_for(
                self.session.call_tool(name, arguments or {}),
                timeout=settings.MCP_TOOL_CALL_TIMEOUT
            )
            content_items = getattr(result, 'content', [])
            texts = []
            for item in content_items:
                text = getattr(item, 'text', '')
                if text:
                    texts.append(text)
            res_text = "\n".join(texts)
            
            if getattr(result, 'isError', False):
                raise RuntimeError(res_text)
                
            return res_text
        except asyncio.TimeoutError as e:
            raise TimeoutError(f"MCP Tool call '{name}' exceeded the configured timeout threshold.") from e

    async def disconnect(self):
        """Disconnects the session and terminates the subprocess."""
        if self.session:
            try:
                await asyncio.wait_for(self.session.__aexit__(None, None, None), timeout=5.0)
            except Exception:
                pass
            self.session = None
        if self._client_ctx:
            try:
                await asyncio.wait_for(self._client_ctx.__aexit__(None, None, None), timeout=5.0)
            except Exception:
                pass
            self._client_ctx = None
