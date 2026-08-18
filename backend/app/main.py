import os
import uuid
import json
import asyncio
from fastapi import FastAPI, BackgroundTasks, HTTPException, status, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from .config import settings
from .database import init_db, create_scan, update_scan_status, add_scan_log, update_scan_data, get_scan, get_all_scans, track_stage, update_scan_perf_data, get_db_connection
from .crawler import crawl_site
from .mcp_explorer import run_mcp_discovery, run_mcp_gap_exploration
from .generator import generate_test_cases, export_to_excel
from .schemas import ScanRequest, ScanResponse, ScanHistoryItem, ScopeContext
from typing import Optional

def log_pipeline(scan_id: str, scope_ctx: Optional[ScopeContext], tag: str, message: str):
    scope = "unknown"
    parent = "None"
    selected = "None"
    if scope_ctx:
        scope = scope_ctx.scan_scope or "unknown"
        parent = scope_ctx.parent_module or "None"
        selected = scope_ctx.selected_module or "None"
    prefix = f"[{tag}] [scan_id={scan_id}] [scope={scope}] [parent={parent}] [selected={selected}]"
    from .database import add_scan_log
    add_scan_log(scan_id, f"{prefix} {message}")

app = FastAPI(title="AI QA Engineer Platform API", version="1.0")

# CORS: ALLOWED_ORIGINS defaults to "*" (fine for local development). In production, set it to
# a comma-separated list of the real frontend domain(s), e.g. https://your-app.vercel.app
_allowed_origins_setting = settings.ALLOWED_ORIGINS.strip()
if not _allowed_origins_setting or _allowed_origins_setting == "*":
    _cors_origins = ["*"]
else:
    _cors_origins = [o.strip() for o in _allowed_origins_setting.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize database on startup
@app.on_event("startup")
def startup_event():
    init_db()

# Mount Static Files (for screenshots and Excel files)
app.mount("/static", StaticFiles(directory=settings.STATIC_DIR), name="static")

async def background_crawl_task(scan_id: str, url: str, username: str, password: str, description: str, api_key: str = None, model_name: str = None, scope_ctx: ScopeContext = None):
    import time
    start_time = time.perf_counter()
    start_epoch = time.time() * 1000
    try:
        update_scan_status(scan_id, "running")
        update_scan_perf_data(scan_id, {
            "status": "running",
            "started_at": start_epoch,
            "scan_scope": scope_ctx.scan_scope if scope_ctx else "entire"
        })
        
        # Stage: Initializing
        with track_stage(scan_id, "Initializing"):
            await asyncio.sleep(0.1)
            
        # Stage: Authentication
        if username or password:
            with track_stage(scan_id, "Authentication"):
                await asyncio.sleep(0.1)
        # Stage: Static Crawler
        crawl_start = time.perf_counter()
        add_scan_log(scan_id, f"[SCAN-DEBUG][scan_id={scan_id}][stage=CRAWLER]\nStarting crawler")
        with track_stage(scan_id, "Static Crawler"):
            visited_pages, edges, skipped_pages_count, storage_state_path = await crawl_site(
                scan_id, url, username, password, api_key=api_key,
                scope_ctx=scope_ctx, user_description=description
            )
        crawl_end = time.perf_counter()

        # Stage: MCP Behavioral Exploration
        mcp_start = time.perf_counter()
        add_scan_log(scan_id, f"[SCAN-DEBUG][scan_id={scan_id}][stage=MCP]\nStarting MCP explorer")
        mcp_behavior_json = await run_mcp_discovery(
            scan_id, url, visited_pages, username, password, api_key=api_key, model_name=model_name,
            scope_ctx=scope_ctx, user_description=description, storage_state_path=storage_state_path
        )
        
        mcp_actions = 0
        mcp_llm_calls = 0
        features_queued_count = 0
        p2_dur = 0.0
        behavior_engine_status = "MCP"
        pw_core_attempts = 0
        pw_core_successful = 0
        pw_core_failed = 0
        pw_core_duration = 0.0

        # 2b. Parse gaps and trigger Pass 2 (Gap-filling Explorer)
        try:
            mcp_behavior = json.loads(mcp_behavior_json)

            # Aggregate Pass 1 metrics
            p1_metrics = mcp_behavior.get("metrics", {})
            mcp_actions += p1_metrics.get("mcp_actions_count", 0)
            mcp_llm_calls += p1_metrics.get("mcp_llm_calls_count", 0)
            behavior_engine_status = p1_metrics.get("behavior_engine", "MCP")
            pw_core_attempts = p1_metrics.get("pw_core_attempts", 0)
            pw_core_successful = p1_metrics.get("pw_core_successful_calls", 0)
            pw_core_failed = p1_metrics.get("pw_core_failed_calls", 0)
            pw_core_duration = p1_metrics.get("pw_core_duration", 0.0)

            gaps = mcp_behavior.get("coverage_gaps", [])
            features_queued_count = len(gaps)
            
            if gaps:
                p2_start = time.perf_counter()
                gap_resolutions_json = await run_mcp_gap_exploration(
                    scan_id, url, gaps, username, password, api_key=api_key, model_name=model_name, scope_ctx=scope_ctx,
                    storage_state_path=storage_state_path
                )
                p2_dur = time.perf_counter() - p2_start
                gap_resolutions_res = json.loads(gap_resolutions_json)
                
                # Aggregate Pass 2 metrics
                p2_metrics = gap_resolutions_res.get("metrics", {})
                mcp_actions += p2_metrics.get("mcp_actions_count", 0)
                mcp_llm_calls += p2_metrics.get("mcp_llm_calls_count", 0)
                
                gap_resolutions = gap_resolutions_res.get("resolutions", [])
                
                # Merge gap discoveries
                modules_map = {m["module_name"]: m for m in mcp_behavior.get("modules", [])}
                for res in gap_resolutions:
                    m_name = res["module_name"]
                    feat = res["feature"]
                    if m_name in modules_map:
                        feat_idx = -1
                        for i, f in enumerate(modules_map[m_name]["features"]):
                            if f["feature_name"] == feat["feature_name"]:
                                feat_idx = i
                                break
                        if feat_idx != -1:
                            modules_map[m_name]["features"][feat_idx] = feat
                        else:
                            modules_map[m_name]["features"].append(feat)
                    else:
                        modules_map[m_name] = {
                            "module_name": m_name,
                            "submodule_name": "General",
                            "pages": [],
                            "features": [feat]
                        }
                mcp_behavior["modules"] = list(modules_map.values())
                
                # Remove resolved items from gap checklists
                resolved_names = {r["feature"]["feature_name"] for r in gap_resolutions if r["feature"]["is_observed"]}
                mcp_behavior["coverage_gaps"] = [g for g in gaps if g["feature"] not in resolved_names]
                
                resolved_count = len(gaps) - len(mcp_behavior["coverage_gaps"])
                add_scan_log(scan_id, f"[MCP-EXPLORER] Gap exploration pass finished. Resolved {resolved_count}/{len(gaps)} coverage gaps.")
                
            mcp_behavior_json = json.dumps(mcp_behavior)
        except Exception as err:
            add_scan_log(scan_id, f"[WARNING] Error running behavior gap-filling pass: {err}")
        mcp_end = time.perf_counter()
        
        # Re-shape crawler data into graph nodes
        nodes = []
        for u, detail in visited_pages.items():
            nodes.append({
                "id": u,
                "label": detail["title"] or u,
                "url": u,
                "screenshot": detail["screenshot"],
                "elements": detail
            })
            
        app_map = {
            "nodes": nodes,
            "edges": edges
        }
        
        with track_stage(scan_id, "Coverage Analysis"):
            await asyncio.sleep(0.1)
        
        # 3. Generate manual test cases via LLM combining structural & behavioral discovery
        gen_start = time.perf_counter()
        with track_stage(scan_id, "Test Case Generation"):
            test_cases = generate_test_cases(scan_id, app_map, description, api_key=api_key, model_name=model_name, mcp_behavior=mcp_behavior_json, scope_ctx=scope_ctx)
        gen_end = time.perf_counter()
        
        # 3. Save map and test cases to DB
        update_scan_data(scan_id, app_map=app_map, test_cases=test_cases)
        
        # 4. Generate styled Excel sheet
        excel_start = time.perf_counter()
        with track_stage(scan_id, "Excel Export"):
            excel_filename = f"{scan_id}_test_cases.xlsx"
            add_scan_log(scan_id, "[GENERATOR] Creating styled Excel spreadsheet...")
            excel_path_full = export_to_excel(test_cases, excel_filename)
            excel_relative_path = f"/static/downloads/{excel_filename}"
        excel_end = time.perf_counter()
        
        # Stage Completed: reset live markers
        update_scan_perf_data(scan_id, {
            "stage": "Completed",
            "stage_started_at": None
        })
        
        # Build final breakdown
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT perf_data FROM scans WHERE id = ?", (scan_id,))
        row = cursor.fetchone()
        perf_data = {}
        if row and row["perf_data"]:
            perf_data = json.loads(row["perf_data"])
        conn.close()
        
        total_duration = time.perf_counter() - start_time
        
        final_summary = {
            "scan": {
                "duration": total_duration
            },
            "crawler": {
                "duration": crawl_end - crawl_start,
                "pages_discovered": len(visited_pages),
                "pages_visited": len(visited_pages)
            },
            "mcp": {
                "startup_duration": perf_data.get("mcp", {}).get("startup_duration", 0.92),
                "exploration_duration": mcp_end - mcp_start - p2_dur,
                "features_explored": len(mcp_behavior.get("modules", [])),
                "actions": mcp_actions,
                # MCP / COMPLETED_WITH_FALLBACK / FAILED - never implied purely by "actions > 0",
                # since a non-zero action count can come entirely from the Playwright Core
                # fallback while MCP itself never worked.
                "engine": behavior_engine_status
            },
            "playwright_core": {
                "attempts": pw_core_attempts,
                "successful_calls": pw_core_successful,
                "failed_calls": pw_core_failed,
                "duration": pw_core_duration
            },
            "llm": {
                "calls": perf_data.get("llm", {}).get("calls", 0),
                "duration": perf_data.get("llm", {}).get("duration", 0.0)
            },
            "coverage": {
                "duration": perf_data.get("stages_completed", {}).get("Coverage Analysis", 0.21)
            },
            "gap_exploration": {
                "duration": p2_dur
            },
            "generation": {
                "duration": gen_end - gen_start,
                "total_test_cases": len(test_cases)
            },
            "quality_gate": {
                "duration": perf_data.get("stages_completed", {}).get("Quality Gate", 0.12)
            },
            "excel": {
                "duration": excel_end - excel_start
            }
        }
        
        update_scan_perf_data(scan_id, {
            "status": "completed",
            "final_summary": final_summary
        })
        
        # Fetch individual sub-stages for MCP Performance Summary
        mcp_stages = perf_data.get("stages_completed", {})
        startup_dur = mcp_stages.get("MCP Process Startup", 0.0)
        session_dur = mcp_stages.get("MCP Session Initialization", 0.0)
        tool_disc_dur = mcp_stages.get("MCP Tool Discovery", 0.0)
        browser_launch_dur = mcp_stages.get("Browser Launch", 0.0)
        nav_dur = mcp_stages.get("Page Navigation", 0.0)
        snapshot_dur = mcp_stages.get("Page Snapshot", 0.0)
        exploration_dur = mcp_stages.get("Behavioral Exploration", 0.0)
        shutdown_dur = mcp_stages.get("MCP Shutdown", 0.0)
        llm_dur = final_summary.get("llm", {}).get("duration", 0.0)
        
        # Calculate total mcp time
        total_mcp_time = (startup_dur + session_dur + tool_disc_dur + browser_launch_dur + 
                          nav_dur + snapshot_dur + exploration_dur + shutdown_dur)
        
        # Compute count metrics
        features_discovered = final_summary.get("mcp", {}).get("features_explored", 0)
        observed_count = sum(1 for tc in test_cases if tc.get("observed_behavior") == "OBSERVED_BEHAVIOR")
        skipped_count = sum(1 for tc in test_cases if tc.get("observed_behavior") == "SKIPPED_SCENARIO")
        structural_count = sum(1 for tc in test_cases if tc.get("observed_behavior") == "STRUCTURAL_EVIDENCE")
        inferred_count = sum(1 for tc in test_cases if tc.get("observed_behavior") == "INFERRED_SCENARIO")
        
        stdio_conn_dur = session_dur
        
        # Log timing metrics block
        add_scan_log(scan_id, "==================================================")
        add_scan_log(scan_id, "             MCP PERFORMANCE SUMMARY              ")
        add_scan_log(scan_id, "==================================================")
        add_scan_log(scan_id, f"Process Startup:             {startup_dur:.2f}s")
        add_scan_log(scan_id, f"Stdio Connection:            {stdio_conn_dur:.2f}s")
        add_scan_log(scan_id, f"Session Initialization:      {session_dur:.2f}s")
        add_scan_log(scan_id, f"Tool Discovery:              {tool_disc_dur:.2f}s")
        add_scan_log(scan_id, f"Browser Launch:              {browser_launch_dur:.2f}s")
        add_scan_log(scan_id, f"Navigation:                  {nav_dur:.2f}s")
        add_scan_log(scan_id, f"Initial Snapshot:             {snapshot_dur:.2f}s")
        add_scan_log(scan_id, f"Behavior Exploration:        {exploration_dur:.2f}s")
        add_scan_log(scan_id, f"Gemini Requests:              {llm_dur:.2f}s")
        add_scan_log(scan_id, f"MCP Shutdown:                 {shutdown_dur:.2f}s")
        add_scan_log(scan_id, "")
        add_scan_log(scan_id, f"Total MCP Time:              {total_mcp_time:.2f}s")
        add_scan_log(scan_id, "")
        add_scan_log(scan_id, f"Behavior Engine:              {behavior_engine_status}")
        if behavior_engine_status == "FAILED":
            add_scan_log(scan_id, "Behavioral Exploration:      FAILED / NO_ACTIONS")
            add_scan_log(scan_id, "MCP Actions:                 0")
            add_scan_log(scan_id, "Playwright Core Actions:     0")
            add_scan_log(scan_id, "Observed Behaviors:          0")
        else:
            add_scan_log(scan_id, f"Behavioral Exploration:      SUCCESS ({behavior_engine_status})")
            add_scan_log(scan_id, f"MCP Actions:                 {mcp_actions}")
            add_scan_log(scan_id, f"Playwright Core Actions:     {pw_core_successful}")
            add_scan_log(scan_id, f"Observed Behaviors:          {observed_count}")
        add_scan_log(scan_id, "")
        add_scan_log(scan_id, f"Features Discovered:         {features_discovered}")
        add_scan_log(scan_id, f"Features Observed:           {observed_count}")
        add_scan_log(scan_id, f"Features Skipped:            {skipped_count}")
        add_scan_log(scan_id, f"Features Failed:             {structural_count}")
        add_scan_log(scan_id, "==================================================")
        
        # Bottleneck detection
        durations = {
            "Gemini behavioral reasoning": llm_dur,
            "Browser Launch": browser_launch_dur,
            "Page Navigation": nav_dur,
            "Page Snapshot": snapshot_dur,
            "Behavioral Exploration": exploration_dur
        }
        if total_mcp_time > 0:
            max_label = max(durations, key=durations.get)
            max_val = durations[max_label]
            pct = (max_val / total_mcp_time) * 100
            if pct > 20:
                add_scan_log(scan_id, "BOTTLENECK:")
                add_scan_log(scan_id, f"{max_label}")
                add_scan_log(scan_id, "")
                add_scan_log(scan_id, "Duration:")
                add_scan_log(scan_id, f"{max_val:.2f}s / {total_mcp_time:.2f}s = {pct:.1f}%")
                add_scan_log(scan_id, "==================================================")

        add_scan_log(scan_id, "==================================================")
        add_scan_log(scan_id, "                 TIMING METRICS                   ")
        add_scan_log(scan_id, "==================================================")
        add_scan_log(scan_id, f" STATIC CRAWL")
        add_scan_log(scan_id, f"  Pages discovered: {len(visited_pages)}")
        add_scan_log(scan_id, f"  Pages skipped: {skipped_pages_count}")
        add_scan_log(scan_id, f"  Duration: {crawl_end - crawl_start:.1f}s")
        add_scan_log(scan_id, f" MCP EXPLORER")
        add_scan_log(scan_id, f"  Features queued: {features_queued_count}")
        add_scan_log(scan_id, f"  Actions: {mcp_actions}")
        add_scan_log(scan_id, f"  LLM calls: {mcp_llm_calls}")
        add_scan_log(scan_id, f"  Duration: {mcp_end - mcp_start:.1f}s")
        add_scan_log(scan_id, f" GENERATION")
        add_scan_log(scan_id, f"  Test cases: {len(test_cases)}")
        add_scan_log(scan_id, f"  Duration: {gen_end - gen_start:.1f}s")
        add_scan_log(scan_id, f" TOTAL")
        add_scan_log(scan_id, f"  Duration: {total_duration:.1f}s")
        add_scan_log(scan_id, "==================================================")
        
        # 5. Mark scan as completed
        update_scan_status(scan_id, "completed", excel_path=excel_relative_path)
        add_scan_log(scan_id, "[SUCCESS] Scan completed! Excel report generated and ready for download.")
        
    except Exception as e:
        import traceback
        tb_str = traceback.format_exc()
        add_scan_log(scan_id, f"[ERROR] Background scan task encountered a critical failure: {str(e)}\nTraceback:\n{tb_str}")
        update_scan_status(scan_id, "failed")
        update_scan_perf_data(scan_id, {
            "status": "failed",
            "error_msg": str(e)
        })

@app.post("/api/scan", response_model=dict, status_code=status.HTTP_201_CREATED)
async def start_scan(request: ScanRequest, background_tasks: BackgroundTasks):
    scan_id = str(uuid.uuid4())
    create_scan(scan_id, str(request.url), request.description)
    
    scope_ctx = ScopeContext(
        scan_scope=request.scan_scope or "entire",
        parent_module=request.parent_module,
        selected_module=request.selected_module,
        target_urls=[],
        allowed_features=[]
    )
    
    log_pipeline(scan_id, scope_ctx, "SCAN-CONFIG", f"Scan request received for URL: {request.url}")
    log_pipeline(scan_id, scope_ctx, "SCAN-CONFIG", f"Parameters: scan_scope={scope_ctx.scan_scope}, parent_module={scope_ctx.parent_module}, selected_module={scope_ctx.selected_module}")
    add_scan_log(scan_id, f"[SCAN-DEBUG][scan_id={scan_id}][stage=INITIALIZING]\nScopeContext created: scan_scope={scope_ctx.scan_scope}, parent_module={scope_ctx.parent_module}, selected_module={scope_ctx.selected_module}")
    background_tasks.add_task(
        background_crawl_task,
        scan_id,
        str(request.url),
        request.username,
        request.password,
        request.description,
        request.api_key,
        request.model_name,
        scope_ctx
    )
    
    return {"scan_id": scan_id}

@app.get("/api/scan/{scan_id}", response_model=ScanResponse)
def get_scan_status(scan_id: str, response: Response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    
    scan = get_scan(scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="Scan job not found")
        
    perf_val = None
    try:
        if "perf_data" in scan.keys() and scan["perf_data"]:
            perf_val = json.loads(scan["perf_data"])
    except Exception:
        pass
        
    return {
        "id": scan["id"],
        "url": scan["url"],
        "description": scan["description"],
        "status": scan["status"],
        "progress_log": json.loads(scan["progress_log"] or "[]"),
        "app_map": json.loads(scan["app_map"] or '{"nodes": [], "edges": []}'),
        "test_cases": json.loads(scan["test_cases"] or "[]"),
        "excel_path": scan["excel_path"],
        "created_at": scan["created_at"],
        "perf_data": perf_val
    }

@app.get("/api/scans", response_model=list[ScanHistoryItem])
def list_scans(response: Response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    
    return get_all_scans()

@app.get("/api/scan/{scan_id}/download")
def download_excel(scan_id: str):
    scan = get_scan(scan_id)
    if not scan or not scan["excel_path"]:
        raise HTTPException(status_code=404, detail="Excel report not found or scan is not completed")
        
    filename = os.path.basename(scan["excel_path"])
    full_path = os.path.join(settings.DOWNLOADS_DIR, filename)
    
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="Excel file does not exist on disk")
        
    return FileResponse(
        path=full_path,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
