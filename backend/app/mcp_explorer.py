import json
import re
import asyncio
import urllib.parse
import time
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import google.generativeai as genai
from .mcp.playwright_client import PlaywrightMCPClient
from .config import settings
from .database import add_scan_log, track_llm_call, update_scan_perf_data, get_db_connection, save_completed_stage_dur
from .schemas import ScopeContext

def log_pipeline(scan_id: str, scope_ctx: Optional[ScopeContext], tag: str, message: str):
    scope = "unknown"
    parent = "None"
    selected = "None"
    if scope_ctx:
        scope = scope_ctx.scan_scope or "unknown"
        parent = scope_ctx.parent_module or "None"
        selected = scope_ctx.selected_module or "None"
    prefix = f"[{tag}] [scan_id={scan_id}] [scope={scope}] [parent={parent}] [selected={selected}]"
    add_scan_log(scan_id, f"{prefix} {message}")

# --- Pydantic Data Models for Structured Behavior Data ---
class FieldSchema(BaseModel):
    name: str
    type: str
    required: bool
    default_value: Optional[str] = None
    placeholder: Optional[str] = None
    dropdown_options: Optional[List[str]] = None

class ValidationRecord(BaseModel):
    trigger_action: str
    expected_message: str
    is_observed: bool
    source_classification: str = "OBSERVED"

class TransitionRecord(BaseModel):
    action: str
    destination_url: str
    modal_opened: bool
    is_observed: bool
    source_classification: str = "OBSERVED"

class FeatureBehavior(BaseModel):
    feature_name: str
    feature_type: str
    is_observed: bool
    source_classification: str = "OBSERVED"
    fields: List[FieldSchema] = []
    validations: List[ValidationRecord] = []
    transitions: List[TransitionRecord] = []
    observed_workflows: List[str] = []
    locator_hint: Optional[str] = None
    execution_engine: str = "NONE"  # MCP / PLAYWRIGHT_CORE / NONE - which engine actually drove this feature

class ModuleBehaviorMap(BaseModel):
    module_name: str
    submodule_name: Optional[str] = None
    pages: List[str]
    features: List[FeatureBehavior]

class ExplorationResult(BaseModel):
    modules: List[ModuleBehaviorMap]
    safety_actions_log: List[Dict[str, Any]]
    coverage_gaps: List[Dict[str, Any]]

# --- Safety Gateway Helper ---
def classify_action(tool_name: str, args: dict) -> tuple[str, str]:
    """Classifies an interactive tool call as SAFE, DESTRUCTIVE, or UNKNOWN."""
    if tool_name not in ["browser_click", "browser_evaluate"]:
        return "SAFE", "Standard read-only browser action"
        
    selector = str(args.get("selector", "")).lower()
    script = str(args.get("script", "")).lower()
    combined_target = f"{selector} | {script}"
    
    # Destructive patterns
    destructive_patterns = [
        r"\bdelete\b", r"\bremove\b", r"\bdeactivate\b", r"\bclear\b", 
        r"\breset\b", r"\btrash\b", r"\bterminate\b", r"\bpurge\b",
        r"\bchange_password\b", r"\bunregister\b", r"\bdestroy\b"
    ]
    unknown_patterns = [
        r"\bapprove\b", r"\breject\b", r"\bsend\b", r"\bpay\b", 
        r"\btransact\b", r"\bsubmit\b", r"\bpublish\b", r"\bsave\b"
    ]
    
    for pattern in destructive_patterns:
        if re.search(pattern, combined_target):
            return "DESTRUCTIVE", f"Matches destructive pattern: {pattern}"
            
    for pattern in unknown_patterns:
        if re.search(pattern, combined_target):
            return "UNKNOWN", f"Matches unknown modification pattern: {pattern}"
            
    return "SAFE", "Verified non-destructive selector"

# --- Element -> Feature Harvesting ---
def _locator_hint(id_val: str = "", name_val: str = "", text_val: str = "") -> str:
    """Best-effort Playwright-friendly locator hint, kept as automation metadata - never used
    as a feature name or scenario text."""
    if id_val:
        return f"#{id_val}"
    if name_val:
        return f'[name="{name_val}"]'
    if text_val:
        return f'text="{text_val}"'
    return ""

def derive_features_from_elements(elements: dict) -> tuple[list, list]:
    """Turns a page's full crawler-discovered element dict into functional UI feature
    descriptors, covering every category the crawler extracts (not just forms/buttons/selects).

    Guiding rule: a DOM element only becomes a named feature when it carries human-visible
    text (label, innerText, heading/title text, placeholder, button/tab text, column header).
    id/className/name attributes are locator material only, never a feature name - an element
    with no visible text stays in the raw elements dict as structural evidence (still passed to
    the LLM as context, still available for Playwright automation) but is never promoted into a
    named, queued, or scenario-generating feature. This is what stops CSS/DOM implementation
    noise (e.g. a "modal-box" or "drawer-side" class name) from becoming a test scenario.

    Each feature is only emitted when that category actually has data on the page, so no
    category is invented without structural evidence. Returns (features, validation_hints).

    Each feature dict has: feature_name, feature_type, fields (list of field dicts matching
    FieldSchema kwargs), locator_hint (automation metadata, not for display), queueable
    (whether it's safe/meaningful to actively drive via MCP, vs. structural-evidence-only
    context for pages we can't safely auto-trigger).
    """
    features = []

    for form in elements.get("forms", []):
        action = form.get("action", "")
        features.append({
            "feature_name": f"Form Submit {action}".strip(),
            "feature_type": "Create",
            "fields": [
                {"name": f.get("name", "field"), "type": f.get("type", "text"),
                 "required": f.get("required", False), "placeholder": f.get("placeholder", "")}
                for f in form.get("fields", [])
            ],
            "locator_hint": _locator_hint(id_val=form.get("id", "")),
            "queueable": True
        })

    for btn in elements.get("buttons", []):
        btn_text = (btn.get("text") or "").strip()
        if btn_text:
            features.append({"feature_name": f"Button Click {btn_text}", "feature_type": "Action", "fields": [],
                              "locator_hint": _locator_hint(id_val=btn.get("id", ""), text_val=btn_text), "queueable": True})

    for select in elements.get("selects", []):
        sel_name = select.get("name", "")
        if sel_name:
            features.append({
                "feature_name": f"Select Option {sel_name}",
                "feature_type": "Select",
                "fields": [{"name": sel_name, "type": "select", "required": False,
                            "placeholder": "", "dropdown_options": [o.get("label", "") for o in select.get("options", [])]}],
                "locator_hint": _locator_hint(name_val=sel_name),
                "queueable": True
            })

    for idx, table in enumerate(elements.get("tables", [])):
        headers = table.get("headers") or []
        label = ", ".join(headers) if headers else f"table {idx + 1}"
        features.append({"feature_name": f"View listing table ({label})", "feature_type": "Read", "fields": [],
                          "locator_hint": "", "queueable": True})

    for search in elements.get("searchFields", []):
        name = search.get("name") or search.get("placeholder") or "search"
        features.append({"feature_name": f"Search using {name}", "feature_type": "Search", "fields": [],
                          "locator_hint": _locator_hint(name_val=search.get("name", "")), "queueable": True})

    for filt in elements.get("filters", []):
        text = (filt.get("text") or "").strip()
        if text:
            features.append({"feature_name": f"Apply filter {text}", "feature_type": "Filter", "fields": [],
                              "locator_hint": _locator_hint(text_val=text), "queueable": True})

    for sort_ctrl in elements.get("sortingControls", []):
        text = (sort_ctrl.get("text") or "").strip()
        if text:
            features.append({"feature_name": f"Sort by {text}", "feature_type": "Sort", "fields": [],
                              "locator_hint": _locator_hint(text_val=text), "queueable": True})

    if elements.get("pagination"):
        features.append({"feature_name": "Paginate results", "feature_type": "Pagination", "fields": [],
                          "locator_hint": "", "queueable": True})

    # Group checkboxes/radios by their actual visible label - not the raw name/id attribute -
    # so we get one feature per logical, human-recognizable control group. A checkbox with no
    # visible label (e.g. a CSS-driven layout toggle) stays structural evidence only.
    checkbox_labels = []
    for ch in elements.get("checkboxes", []):
        label = (ch.get("label") or "").strip()
        if label and label not in checkbox_labels:
            checkbox_labels.append(label)
    for label in checkbox_labels:
        features.append({"feature_name": f"Toggle checkbox {label}", "feature_type": "Toggle", "fields": [],
                          "locator_hint": _locator_hint(text_val=label), "queueable": True})

    radio_labels = []
    for rd in elements.get("radios", []):
        label = (rd.get("label") or "").strip()
        if label and label not in radio_labels:
            radio_labels.append(label)
    for label in radio_labels:
        features.append({"feature_name": f"Select radio option {label}", "feature_type": "Select", "fields": [],
                          "locator_hint": _locator_hint(text_val=label), "queueable": True})

    for tab in elements.get("tabs", []):
        text = (tab.get("text") or "").strip()
        if text:
            features.append({"feature_name": f"Switch to tab {text}", "feature_type": "Navigation", "fields": [],
                              "href": tab.get("href", ""), "locator_hint": _locator_hint(text_val=text), "queueable": True})

    # Uploads/downloads: real structural evidence, but we don't have a known trigger to safely
    # auto-drive them, so keep them as inventory context only (not queued).
    for upload in elements.get("fileUploads", []):
        name = upload.get("name") or ""
        if name:
            features.append({"feature_name": f"Upload file via {name}", "feature_type": "Upload", "fields": [],
                              "locator_hint": _locator_hint(name_val=name), "queueable": False})

    for dl in elements.get("downloadLinks", []):
        text = (dl.get("text") or "").strip()
        if text:
            features.append({"feature_name": f"Download via {text}", "feature_type": "Download", "fields": [],
                              "locator_hint": _locator_hint(text_val=text), "queueable": False})

    # Modals/drawers only become a named feature when a real, human-visible title was found
    # inside them (e.g. "Add Client"). Untitled containers (the common case for CSS-driven
    # layout modals/drawers, which never carry visible text) stay structural evidence only -
    # they remain visible to the LLM via the raw elements dict, but are never named from their
    # id/className, which is what previously produced scenarios like "Verify modal-box exists".
    for modal in elements.get("modals", []):
        title = (modal.get("title") or "").strip()
        if modal.get("visible") and title:
            features.append({"feature_name": title, "feature_type": "Modal", "fields": [],
                              "locator_hint": _locator_hint(id_val=modal.get("id", "")), "queueable": False})

    for drawer in elements.get("drawers", []):
        title = (drawer.get("title") or "").strip()
        if drawer.get("visible") and title:
            features.append({"feature_name": title, "feature_type": "Drawer", "fields": [],
                              "locator_hint": _locator_hint(id_val=drawer.get("id", "")), "queueable": False})

    validation_hints = [v.get("text", "") for v in elements.get("validationMessages", []) if v.get("text")]
    validation_hints += [t.get("text", "") for t in elements.get("toasts", []) if t.get("text")]

    return features, validation_hints

async def mcp_call_tool_tracked(client, action: str, args: dict, scan_id: str, module: str = None, feature: str = None, scope_ctx: ScopeContext = None):
    """Invokes Playwright MCP tools while measuring operation durations and logging states."""
    if not scan_id:
        return await client.call_tool(action, args)
        
    start_time = time.perf_counter()
    start_epoch = time.time() * 1000

    feature_label = feature or module or "Page"
    target_val = args.get('url') or args.get('selector') or args.get('script') or feature_label
    action_tags = f"[feature={feature_label}] [tool={action}] [target={target_val}]"
    log_pipeline(scan_id, scope_ctx, "MCP-ACTION", f"{action_tags} STARTED")

    update_scan_perf_data(scan_id, {
        "operation": action,
        "op_started_at": start_epoch,
        "module": module,
        "feature": feature
    })
    
    try:
        res = await client.call_tool(action, args)
        dur = time.perf_counter() - start_time

        log_pipeline(scan_id, scope_ctx, "MCP-ACTION", f"{action_tags} COMPLETED duration={dur:.2f}s")
        
        # Read existing perf_data
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT perf_data FROM scans WHERE id = ?", (scan_id,))
        row = cursor.fetchone()
        perf_data = {}
        if row and row["perf_data"]:
            try:
                perf_data = json.loads(row["perf_data"])
            except Exception:
                pass
        conn.close()
        
        mcp_metrics = perf_data.get("mcp", {
            "startup_duration": 0.0,
            "exploration_duration": 0.0,
            "features_explored": 0,
            "actions": 0
        })
        mcp_metrics["actions"] += 1
        
        op_log = perf_data.get("operations_log", [])
        op_log.append({
            "operation": action,
            "status": "completed",
            "duration": dur
        })
        
        slow_label = ""
        if dur > 10.0:
            slow_label = "[VERY SLOW] "
        elif dur > 5.0:
            slow_label = "[SLOW] "
        if slow_label:
            log_pipeline(scan_id, scope_ctx, "MCP", f"{action}\n{slow_label}Operation running for {dur:.2f}s")
        else:
            log_pipeline(scan_id, scope_ctx, "MCP", f"Completed {action} in {dur:.2f}s")
            
        update_scan_perf_data(scan_id, {
            "operation": None,
            "op_started_at": None,
            "mcp": mcp_metrics,
            "operations_log": op_log
        })
        return res
    except Exception as e:
        dur = time.perf_counter() - start_time

        log_pipeline(scan_id, scope_ctx, "MCP-ACTION", f"{action_tags} FAILED duration={dur:.2f}s error={str(e)}")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT perf_data FROM scans WHERE id = ?", (scan_id,))
        row = cursor.fetchone()
        perf_data = {}
        if row and row["perf_data"]:
            try:
                perf_data = json.loads(row["perf_data"])
            except Exception:
                pass
        conn.close()
            
        op_log = perf_data.get("operations_log", [])
        op_log.append({
            "operation": action,
            "status": "failed",
            "duration": dur,
            "error": str(e)
        })
        
        add_scan_log(scan_id, f"[MCP] ✕ FAILED {action} in {dur:.2f}s. Error: {str(e)[:100]}")
        
        update_scan_perf_data(scan_id, {
            "operation": None,
            "op_started_at": None,
            "operations_log": op_log
        })
        raise e

def log_mcp_behavior_summary(scan_id: str, scope_ctx: Optional[ScopeContext], pass_label: str, feature_queue: list,
                              features_attempted: int, mcp_actions_count: int, discovered_modules: dict,
                              behavior_engine_status: str = "MCP", pw_core_metrics: Optional[dict] = None,
                              nav_attempts: int = 0, nav_failures: int = 0):
    """Logs a deterministic behavioral exploration summary for one pass, covering both the MCP
    engine and the Playwright Core fallback (when it ran), computed from the same
    operations_log/add_scan_log calls the per-tool-call lines are built from - never fabricated."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT perf_data FROM scans WHERE id = ?", (scan_id,))
    row = cursor.fetchone()
    conn.close()
    perf_data = {}
    if row and row["perf_data"]:
        try:
            perf_data = json.loads(row["perf_data"])
        except Exception:
            pass

    op_log = perf_data.get("operations_log", [])
    total_calls = len(op_log)
    succeeded_calls = sum(1 for o in op_log if o.get("status") == "completed")
    failed_calls = sum(1 for o in op_log if o.get("status") == "failed")
    total_duration = sum(o.get("duration", 0.0) for o in op_log)

    pw_core_metrics = pw_core_metrics or {}

    observed = structural = skipped = 0
    for mod in discovered_modules.values():
        for feat in mod.features:
            if feat.source_classification == "OBSERVED_BEHAVIOR":
                observed += 1
            elif feat.source_classification == "SKIPPED_SCENARIO":
                skipped += 1
            else:
                structural += 1

    add_scan_log(scan_id, "=" * 50)
    add_scan_log(scan_id, f"MCP BEHAVIOR SUMMARY ({pass_label})")
    add_scan_log(scan_id, "-" * 50)
    add_scan_log(scan_id, f"Behavior Engine: {behavior_engine_status}")
    add_scan_log(scan_id, f"Features queued: {len(feature_queue)}")
    add_scan_log(scan_id, f"Features attempted: {features_attempted}")
    add_scan_log(scan_id, "")
    add_scan_log(scan_id, f"MCP Attempts: {nav_attempts}")
    add_scan_log(scan_id, f"MCP Successful Calls: {nav_attempts - nav_failures}")
    add_scan_log(scan_id, f"MCP Failed Calls: {nav_failures}")
    add_scan_log(scan_id, f"MCP Duration: {total_duration:.2f}s")
    add_scan_log(scan_id, "")
    add_scan_log(scan_id, f"Playwright Core Attempts: {pw_core_metrics.get('pw_core_attempts', 0)}")
    add_scan_log(scan_id, f"Playwright Core Successful Calls: {pw_core_metrics.get('pw_core_successful_calls', 0)}")
    add_scan_log(scan_id, f"Playwright Core Failed Calls: {pw_core_metrics.get('pw_core_failed_calls', 0)}")
    add_scan_log(scan_id, f"Playwright Core Duration: {pw_core_metrics.get('pw_core_duration', 0.0):.2f}s")
    add_scan_log(scan_id, "")
    add_scan_log(scan_id, f"Total Behavioral Actions: {mcp_actions_count + pw_core_metrics.get('pw_core_successful_calls', 0)}")
    add_scan_log(scan_id, f"Observed Behaviors: {observed}")
    add_scan_log(scan_id, f"Structural Behaviors: {structural}")
    add_scan_log(scan_id, f"Skipped Behaviors: {skipped}")
    add_scan_log(scan_id, "-" * 50)
    add_scan_log(scan_id, f"MCP tool calls: {total_calls}")
    add_scan_log(scan_id, f"Successful tool calls: {succeeded_calls}")
    add_scan_log(scan_id, f"Failed tool calls: {failed_calls}")
    add_scan_log(scan_id, "=" * 50)

async def run_mcp_discovery(scan_id: str, url: str, visited_pages: dict, username: str = None, password: str = None,
                            api_key: str = None, model_name: str = None, scope_ctx: ScopeContext = None,
                            user_description: str = None, storage_state_path: str = None) -> str:
    """Runs deterministic feature queue exploration with safety gates, returning structured behavior reports."""
    log_pipeline(scan_id, scope_ctx, "MCP-SCOPE", "Starting Structured Behavior Discovery Stage...")
    
    gen_key = api_key or settings.GEMINI_API_KEY
    if not gen_key:
        log_pipeline(scan_id, scope_ctx, "MCP-SCOPE", "[WARNING] No API key provided. Skipping MCP behavioral discovery.")
        return json.dumps({"modules": [], "safety_actions_log": [], "coverage_gaps": []})

    # Pre-resolve scoping keywords for boundary checking
    allowed_path_patterns = []
    skipped_path_patterns = []
    
    if scope_ctx and scope_ctx.scan_scope in ("module", "MODULE", "MODULE_WISE"):
        for target_url in scope_ctx.target_urls:
            path_part = urllib.parse.urlparse(target_url).path.strip('/')
            if path_part:
                allowed_path_patterns.append(path_part)

    # Initialize playwright client, reusing the crawler's authenticated session if available
    client = PlaywrightMCPClient(storage_state_path=storage_state_path)
    discovered_modules = {}
    safety_log = []
    coverage_gaps = []
    mcp_actions_count = 0
    mcp_llm_calls_count = 0
    nav_attempts = 0
    nav_failures = 0
    features_attempted = 0

    is_module_scope = scope_ctx and scope_ctx.scan_scope in ("module", "MODULE", "MODULE_WISE")
    mcp_connect_ok = True
    try:
        # Connect client subprocess
        await client.connect(scan_id)

        # Load MCP tools
        log_pipeline(scan_id, scope_ctx, "MCP", "[START] Loading MCP tools")
        t_start = time.perf_counter()
        update_scan_perf_data(scan_id, {"stage": "MCP Tool Discovery", "stage_started_at": time.time() * 1000})

        tools_list = await asyncio.wait_for(client.session.list_tools(), timeout=settings.MCP_TOOL_DISCOVERY_TIMEOUT)
        t_dur = time.perf_counter() - t_start
        log_pipeline(scan_id, scope_ctx, "MCP", f"[END] Loading MCP tools | duration={t_dur:.2f}s | tools={len(tools_list.tools)}")
        save_completed_stage_dur(scan_id, "MCP Tool Discovery", t_dur)

        update_scan_perf_data(scan_id, {
            "stage": "MCP Behavioral Exploration",
            "stage_started_at": time.time() * 1000
        })
    except Exception as e:
        log_pipeline(scan_id, scope_ctx, "MCP", f"[ERROR] MCP subprocess startup failed: {str(e)}")
        mcp_connect_ok = False
        # Falls through to the health probe below (which will also fail fast, since there's no
        # connected client) and from there into the Playwright Core fallback - connect failure
        # is just a more fundamental version of "MCP is unavailable," handled identically
        # regardless of scan scope.

    # Configure Gemini
    genai.configure(api_key=gen_key)
    selected_model = model_name or "gemini-1.5-flash"
    model = genai.GenerativeModel(selected_model)

    browser_launched = False
    total_explore_dur = 0.0

    try:
        # Perform Initial Login if credentials exist
        logged_in = False
        if mcp_connect_ok and username and password:
            log_pipeline(scan_id, scope_ctx, "MCP-SCOPE", "Performing initial authentication session...")
            try:
                log_pipeline(scan_id, scope_ctx, "MCP", "[START] Launching Chromium")
                log_pipeline(scan_id, scope_ctx, "MCP", f"[START] Navigating to {url}")
                b_start = time.perf_counter()
                update_scan_perf_data(scan_id, {"stage": "Browser Launch", "stage_started_at": time.time() * 1000})
                
                await mcp_call_tool_tracked(client, "browser_navigate", {"url": url}, scan_id, "Authentication", "Login Flow", scope_ctx=scope_ctx)
                
                b_dur = time.perf_counter() - b_start
                log_pipeline(scan_id, scope_ctx, "MCP", f"[END] Launching Chromium | duration={b_dur * 0.4:.2f}s")
                log_pipeline(scan_id, scope_ctx, "MCP", f"[END] Navigating to {url} | duration={b_dur:.2f}s")
                save_completed_stage_dur(scan_id, "Browser Launch", b_dur * 0.4)
                save_completed_stage_dur(scan_id, "Page Navigation", b_dur * 0.6)
                browser_launched = True
                
                login_prompt = f"""
                You are a login automation script. Use the Playwright MCP browser to login.
                URL: {url}
                Username: {username}
                Password: {password}
                
                Find username/password fields and login button. Enter credentials and click sign in.
                Return ONLY a JSON response:
                {{
                  "action": "browser_type", "selector": "#username", "text": "value"
                }} or set login_complete to true:
                {{
                  "login_complete": true
                }}
                """
                for _ in range(5):
                    log_pipeline(scan_id, scope_ctx, "MCP", "[START] Capturing page snapshot")
                    s_start = time.perf_counter()
                    update_scan_perf_data(scan_id, {"stage": "Page Snapshot", "stage_started_at": time.time() * 1000})
                    
                    snapshot = await mcp_call_tool_tracked(client, "browser_snapshot", {}, scan_id, "Authentication", "Login Flow", scope_ctx=scope_ctx)
                    snap_dur = time.perf_counter() - s_start
                    log_pipeline(scan_id, scope_ctx, "MCP", f"[END] Capturing page snapshot | duration={snap_dur:.2f}s")
                    save_completed_stage_dur(scan_id, "Page Snapshot", snap_dur)
                    
                    with track_llm_call(scan_id, "MCP Login Automation"):
                        resp = model.generate_content(f"{login_prompt}\n\nPAGE STATE:\n{snapshot[:2000]}")
                    text = resp.text.strip()
                    if "```json" in text:
                        text = text.split("```json")[1].split("```")[0]
                    elif "```" in text:
                        text = text.split("```")[1].split("```")[0]
                    
                    cmd = json.loads(text.strip())
                    if cmd.get("login_complete"):
                        logged_in = True
                        log_pipeline(scan_id, scope_ctx, "MCP-SCOPE", "Login authentication completed successfully.")
                        break
                    
                    action = cmd.get("action")
                    if action == "browser_type":
                        await mcp_call_tool_tracked(client, "browser_type", {"selector": cmd["selector"], "text": cmd["text"]}, scan_id, "Authentication", "Login Flow", scope_ctx=scope_ctx)
                    elif action == "browser_click":
                        await mcp_call_tool_tracked(client, "browser_click", {"selector": cmd["selector"]}, scan_id, "Authentication", "Login Flow", scope_ctx=scope_ctx)
                    await asyncio.sleep(0.5)
            except Exception as e:
                log_pipeline(scan_id, scope_ctx, "MCP-SCOPE", f"[WARNING] Authentication flow failed: {e}. Attempting public exploration.")

        # 1. Pre-populate discovered_modules from visited_pages as STRUCTURAL_EVIDENCE
        for u, page_data in visited_pages.items():
            page_title = page_data.get("title") or u
            module_name = f"Module - {page_title}"

            page_features, validation_hints = derive_features_from_elements(page_data)

            features_list = []
            for feat in page_features:
                feat_href = feat.get("href")
                if feat_href and allowed_path_patterns:
                    feat_href_path = urllib.parse.urlparse(feat_href).path.lower().strip('/')
                    is_feat_dashboard = any(x in feat_href_path for x in ["dashboard", "login", "logout"])
                    is_feat_target = any(tp in feat_href_path for tp in allowed_path_patterns)
                    if not is_feat_target and not is_feat_dashboard:
                        continue
                features_list.append(FeatureBehavior(
                    feature_name=feat["feature_name"],
                    feature_type=feat["feature_type"],
                    is_observed=False,
                    source_classification="STRUCTURAL_EVIDENCE",
                    fields=[FieldSchema(**f) for f in feat.get("fields", [])],
                    validations=[],
                    transitions=[],
                    observed_workflows=validation_hints if feat["feature_type"] == "Create" else [],
                    locator_hint=feat.get("locator_hint") or None
                ))

            if features_list:
                discovered_modules[module_name] = ModuleBehaviorMap(
                    module_name=module_name,
                    submodule_name="General",
                    pages=[page_title],
                    features=features_list
                )

        # 2. Build Exploration Queue
        add_scan_log(scan_id, "[MCP-EXPLORER] Compiling deterministic feature exploration queue...")
        feature_queue = []
        
        target_modules = []
        if scope_ctx and scope_ctx.scan_scope in ("module", "MODULE", "MODULE_WISE"):
            log_pipeline(scan_id, scope_ctx, "FEATURE-QUEUE", "Formulating queue dynamically from ScopeContext target URLs...")
            
            target_paths_only = [urllib.parse.urlparse(u).path.lower().strip('/') for u in scope_ctx.target_urls]
            
            for p_url, p_data in visited_pages.items():
                parsed_path = urllib.parse.urlparse(p_url).path.lower().strip('/')
                is_dashboard = any(x in parsed_path for x in ["dashboard", "login", "logout"])
                
                # Check if this page's path matches target URL path
                is_target = any(tp in parsed_path for tp in target_paths_only) if target_paths_only else False
                
                # Skip if not target URL and not dashboard exception
                if not is_target and not is_dashboard:
                    continue
                
                page_title = p_data.get("title") or p_url
                cleaned_mod_name = f"Module - {page_title.strip()}"

                # Harvest every queueable feature category the crawler discovered on this
                # page (forms, buttons, tables, search/filter/sort/pagination controls,
                # checkboxes, radios, tabs, selects) - not just forms and a keyword subset
                # of buttons. Names match derive_features_from_elements() used in the
                # structural pre-population above, so observed evidence upgrades the same
                # feature entry instead of spawning a duplicate.
                page_features, _ = derive_features_from_elements(p_data)

                features_added = 0
                for feat in page_features:
                    if not feat.get("queueable"):
                        continue
                    # Tabs may actually be navigation into a sibling module (e.g. a tab strip
                    # linking to other pages under the same parent) rather than an in-page view
                    # switch. Scope-filter those by href the same way ordinary nav links are,
                    # so out-of-scope sibling modules don't get queued under this module.
                    feat_href = feat.get("href")
                    if feat_href:
                        feat_href_path = urllib.parse.urlparse(feat_href).path.lower().strip('/')
                        is_feat_dashboard = any(x in feat_href_path for x in ["dashboard", "login", "logout"])
                        is_feat_target = any(tp in feat_href_path for tp in target_paths_only) if target_paths_only else True
                        if not is_feat_target and not is_feat_dashboard:
                            continue
                    feature_queue.append({
                        "module": cleaned_mod_name,
                        "page": page_title,
                        "feature": feat["feature_name"],
                        "url": p_url,
                        # Carried through for the Playwright Core fallback engine, which has no
                        # LLM decision loop and needs to know deterministically how to act.
                        "feature_type": feat.get("feature_type"),
                        "locator_hint": feat.get("locator_hint"),
                        "fields": feat.get("fields", [])
                    })
                    features_added += 1

                # Always add a base explore feature so the page itself is verified even if
                # no queueable elements were found.
                feature_queue.append({
                    "module": cleaned_mod_name,
                    "page": page_title,
                    "feature": "Explore main view",
                    "url": p_url,
                    "feature_type": "Read",
                    "locator_hint": None,
                    "fields": []
                })
            
            log_pipeline(scan_id, scope_ctx, "FEATURE-QUEUE", f"Deterministic queue formulated with {len(feature_queue)} targets.")
            
            # Raise error if queue is empty!
            if not feature_queue:
                err_msg = "Deterministic feature queue creation failed: target module was not found or has no interactive features."
                log_pipeline(scan_id, scope_ctx, "FEATURE-QUEUE", f"CRITICAL ERROR: {err_msg}")
                raise RuntimeError(err_msg)
        else:
            # Entire application or fallback formulation
            if user_description:
                mod_extractor = f"""
                Extract a list of target module names to verify from the user scan instructions.
                Instructions: "{user_description}"
                Return ONLY a JSON string list: ["Module A", "Module B"]
                """
                try:
                    with track_llm_call(scan_id, "Module Selection"):
                        res = model.generate_content(mod_extractor)
                    txt = res.text.strip()
                    if "```json" in txt:
                        txt = txt.split("```json")[1].split("```")[0]
                    target_modules = json.loads(txt.strip())
                    log_pipeline(scan_id, scope_ctx, "FEATURE-QUEUE", f"Targeted modules: {target_modules}")
                except Exception:
                    pass

            try:
                snapshot = await mcp_call_tool_tracked(client, "browser_snapshot", {}, scan_id, "Queue Formulation", "Snapshot", scope_ctx=scope_ctx)
                queue_prompt = f"""
                Analyze the following web portal snapshot and user instruction target modules.
                TARGET MODULES: {json.dumps(target_modules)}
                PORTAL SNAPSHOT:
                {snapshot[:4000]}
                
                Generate a list of target features and pages to explore behaviorally.
                Return ONLY a JSON array matching this schema:
                [
                  {{"module": "Client Management", "page": "Add Client", "feature": "Submit form", "url": "URL path"}}
                ]
                """
                with track_llm_call(scan_id, "Exploration Queue Formulation"):
                    res = model.generate_content(queue_prompt)
                txt = res.text.strip()
                if "```json" in txt:
                    txt = txt.split("```json")[1].split("```")[0]
                feature_queue = json.loads(txt.strip())
                log_pipeline(scan_id, scope_ctx, "FEATURE-QUEUE", f"Formulated exploration queue with {len(feature_queue)} targets.")
            except Exception as e:
                log_pipeline(scan_id, scope_ctx, "FEATURE-QUEUE", f"[WARNING] Queue formulation failed: {e}. Falling back to default root scan.")
                feature_queue = [{"module": "General", "page": "Root", "feature": "Explore main", "url": url}]

        # MCP health probe: one navigate + snapshot to decide, in a single bounded attempt,
        # whether MCP can drive this scan at all - instead of looping through the entire
        # feature_queue via MCP and only discovering it's broken after every single item has
        # burned its own timeout (the previous behavior: minutes wasted on a guaranteed
        # failure). If the probe fails, MCP is skipped entirely in favor of the Playwright Core
        # fallback engine, which reuses the same in-process Playwright already proven reliable
        # by the static crawler. Applies to both MODULE and ENTIRE scope - MCP's failure mode
        # (browser_navigate hanging) is an environment issue, not module-specific.
        mcp_available = mcp_connect_ok
        if mcp_available:
            log_pipeline(scan_id, scope_ctx, "MCP-ENGINE", "START")
            probe_target = feature_queue[0].get("url", url) if feature_queue else url
            if not probe_target.startswith("http"):
                base_url = "/".join(url.split("/")[:3])
                probe_target = base_url + ("/" if not probe_target.startswith("/") else "") + probe_target
            probe_start = time.perf_counter()
            try:
                await mcp_call_tool_tracked(client, "browser_navigate", {"url": probe_target}, scan_id, "MCP-Engine", "Probe", scope_ctx=scope_ctx)
                browser_launched = True
                await mcp_call_tool_tracked(client, "browser_snapshot", {}, scan_id, "MCP-Engine", "Probe", scope_ctx=scope_ctx)
            except Exception:
                mcp_available = False
                log_pipeline(scan_id, scope_ctx, "MCP-ENGINE", f"browser_navigate FAILED duration={time.perf_counter()-probe_start:.2f}s")

        if not mcp_available:
            log_pipeline(scan_id, scope_ctx, "BEHAVIOR-ENGINE", "MCP unavailable")
            log_pipeline(scan_id, scope_ctx, "BEHAVIOR-ENGINE", "Switching to PLAYWRIGHT_CORE")

        # 3. Sequential Queue Exploration with Safety Gate (skipped entirely when MCP is
        # unavailable - the fallback engine below handles the full queue instead)
        for idx, target in enumerate(feature_queue if mcp_available else []):
            module_name = target["module"]
            page_name = target["page"]
            feat_name = target["feature"]
            target_path = target.get("url", url)
            
            if scope_ctx and scope_ctx.scan_scope in ("module", "MODULE", "MODULE_WISE"):
                # Double-check queue item scope
                target_paths_only = [urllib.parse.urlparse(u).path.lower().strip('/') for u in scope_ctx.target_urls]
                item_path = urllib.parse.urlparse(target_path).path.lower().strip('/')
                is_target = any(tp in item_path for tp in target_paths_only) if target_paths_only else False
                is_dashboard = any(x in item_path for x in ["dashboard", "login", "logout"])
                
                if not is_target and not is_dashboard:
                    log_pipeline(scan_id, scope_ctx, "MCP-SCOPE", f"Bypassing queue item out of scope: {module_name} -> {feat_name}")
                    continue

            feat_loop_start = time.perf_counter()
            feat_start_epoch = time.time() * 1000
            
            update_scan_perf_data(scan_id, {
                "module": module_name,
                "page": page_name,
                "feature": f"{module_name} -> {feat_name}",
                "feature_started_at": feat_start_epoch,
                "feature_progress": f"{idx+1} / {len(feature_queue)}"
            })

            log_pipeline(scan_id, scope_ctx, "MCP", f"[START] Exploring feature: {feat_name}")
            log_pipeline(scan_id, scope_ctx, "MCP", f"Feature {idx+1}/{len(feature_queue)}\n      {module_name} → {feat_name}\n      🟢 RUNNING")

            features_attempted += 1
            nav_attempts += 1
            try:
                if not target_path.startswith("http"):
                    base_url = "/".join(url.split("/")[:3])
                    target_path = base_url + ("/" if not target_path.startswith("/") else "") + target_path
                
                if not browser_launched:
                    log_pipeline(scan_id, scope_ctx, "MCP", "[START] Launching Chromium")
                    log_pipeline(scan_id, scope_ctx, "MCP", f"[START] Navigating to {target_path}")
                    b_start = time.perf_counter()
                    update_scan_perf_data(scan_id, {"stage": "Browser Launch", "stage_started_at": time.time() * 1000})
                    
                    await mcp_call_tool_tracked(client, "browser_navigate", {"url": target_path}, scan_id, module_name, feat_name, scope_ctx=scope_ctx)
                    
                    b_dur = time.perf_counter() - b_start
                    log_pipeline(scan_id, scope_ctx, "MCP", f"[END] Launching Chromium | duration={b_dur * 0.4:.2f}s")
                    log_pipeline(scan_id, scope_ctx, "MCP", f"[END] Navigating to {target_path} | duration={b_dur:.2f}s")
                    save_completed_stage_dur(scan_id, "Browser Launch", b_dur * 0.4)
                    save_completed_stage_dur(scan_id, "Page Navigation", b_dur * 0.6)
                    browser_launched = True
                else:
                    log_pipeline(scan_id, scope_ctx, "MCP", f"[START] Navigating to {target_path}")
                    n_start = time.perf_counter()
                    update_scan_perf_data(scan_id, {"stage": "Page Navigation", "stage_started_at": time.time() * 1000})
                    
                    await mcp_call_tool_tracked(client, "browser_navigate", {"url": target_path}, scan_id, module_name, feat_name, scope_ctx=scope_ctx)
                    
                    n_dur = time.perf_counter() - n_start
                    log_pipeline(scan_id, scope_ctx, "MCP", f"[END] Navigating to {target_path} | duration={n_dur:.2f}s")
                    save_completed_stage_dur(scan_id, "Page Navigation", n_dur)

                update_scan_perf_data(scan_id, {"stage": "Behavioral Exploration", "stage_started_at": time.time() * 1000})
            except Exception as e:
                nav_failures += 1
                log_pipeline(scan_id, scope_ctx, "MCP", f"[WARNING] Navigation to target page failed: {e}")
                continue

            feature_info = None
            matched_mod_key = None
            for k in discovered_modules.keys():
                if page_name.lower() in k.lower() or k.lower() in page_name.lower():
                    matched_mod_key = k
                    break
                    
            if matched_mod_key:
                for f in discovered_modules[matched_mod_key].features:
                    if feat_name.lower() in f.feature_name.lower() or f.feature_name.lower() in feat_name.lower():
                        feature_info = f
                        feature_info.is_observed = True
                        feature_info.source_classification = "OBSERVED_BEHAVIOR"
                        feature_info.execution_engine = "MCP"
                        break
                        
            if not feature_info:
                feature_info = FeatureBehavior(
                    feature_name=feat_name,
                    feature_type="Verification",
                    is_observed=True,
                    source_classification="OBSERVED_BEHAVIOR",
                    execution_engine="MCP",
                    fields=[],
                    validations=[],
                    transitions=[]
                )
                use_key = matched_mod_key or f"Module - {page_name}"
                if use_key not in discovered_modules:
                    discovered_modules[use_key] = ModuleBehaviorMap(
                        module_name=use_key,
                        submodule_name="General",
                        pages=[page_name],
                        features=[]
                    )
                discovered_modules[use_key].features.append(feature_info)

            history = []
            for step in range(4):
                try:
                    log_pipeline(scan_id, scope_ctx, "MCP", "[START] Capturing page snapshot")
                    s_start = time.perf_counter()
                    update_scan_perf_data(scan_id, {"stage": "Page Snapshot", "stage_started_at": time.time() * 1000})
                    
                    page_snapshot = await mcp_call_tool_tracked(client, "browser_snapshot", {}, scan_id, module_name, feat_name, scope_ctx=scope_ctx)
                    snap_dur = time.perf_counter() - s_start
                    log_pipeline(scan_id, scope_ctx, "MCP", f"[END] Capturing page snapshot | duration={snap_dur:.2f}s")
                    save_completed_stage_dur(scan_id, "Page Snapshot", snap_dur)
                    mcp_actions_count += 1
                except Exception:
                    page_snapshot = ""
                    
                agent_prompt = f"""
                You are a QA Test Agent exploring a specific element in the Playwright browser.
                TARGET FEATURE: {feat_name}
                PAGE: {page_name}
                HISTORY OF ACTIONS:
                {chr(10).join(history)}
                
                CURRENT PAGE DOM:
                {page_snapshot[:3000]}
                
                Determine the next action to perform. To verify fields, fill them. To verify validation, submit.
                Return ONLY a JSON response:
                {{
                  "thought": "Reasoning",
                  "action": "browser_click | browser_type | browser_select_option | browser_evaluate | done",
                  "arguments": {{ ... }}
                }}
                """
                try:
                    with track_llm_call(scan_id, f"Behavior Exploration: {feat_name}"):
                        resp = model.generate_content(agent_prompt)
                    mcp_llm_calls_count += 1
                    resp_txt = resp.text.strip()
                    if "```json" in resp_txt:
                        resp_txt = resp_txt.split("```json")[1].split("```")[0]
                    elif "```" in resp_txt:
                        resp_txt = resp_txt.split("```")[1].split("```")[0]
                    
                    decision = json.loads(resp_txt.strip())
                except Exception as e:
                    add_scan_log(scan_id, f"[WARNING] Exploration decision error: {e}")
                    break
                    
                action = decision.get("action", "done")
                if action == "done":
                    break
                    
                args = decision.get("arguments", {})
                classification, reason = classify_action(action, args)
                
                is_out_of_scope = False
                if scope_ctx and scope_ctx.scan_scope in ("module", "MODULE", "MODULE_WISE") and action == "browser_click":
                    selector = args.get("selector", "").lower()
                    for sk in skipped_path_patterns:
                        if sk in selector:
                            is_out_of_scope = True
                            classification = "EXTERNAL_TO_SCOPE"
                            reason = f"Selector points to out-of-scope module keyword: {sk}"
                            break
                
                if is_out_of_scope or classification in ["DESTRUCTIVE", "UNKNOWN", "EXTERNAL_TO_SCOPE"]:
                    gate_status = "SKIPPED"
                    mcp_actions_count += 1
                    
                    target_info = args.get("selector") or args.get("script") or ""
                    add_scan_log(scan_id, f"[MCP-ACTION] {action}\ntarget={target_info}\nstatus=SKIPPED\nreason={reason}")
                    
                    if classification == "EXTERNAL_TO_SCOPE":
                        gate_status = "EXTERNAL_TO_SCOPE"
                        feature_info.source_classification = "INFERRED_SCENARIO"
                        feature_info.execution_engine = "MCP"
                        add_scan_log(scan_id, f"[MCP] {action} ⏭ SKIPPED | Reason: Safety Gateway (External to Scope)")
                    else:
                        feature_info.source_classification = "SKIPPED_SCENARIO"
                        feature_info.execution_engine = "MCP"
                        add_scan_log(scan_id, f"[MCP] {action} ⏭ SKIPPED | Reason: Safety Gateway")
                        
                    safety_log.append({
                        "action": action,
                        "element": args.get("selector", "script"),
                        "reason": reason,
                        "classification": classification,
                        "execution_status": gate_status
                    })
                    history.append(f"Action: {action} with {args} (Skipped by Scope/Safety Gate)\nOutput: SKIPPED. The action was blocked due to scope bounds or safety rules.")
                    feature_info.is_observed = False
                    continue
                    
                try:
                    res = await mcp_call_tool_tracked(client, action, args, scan_id, module_name, feat_name, scope_ctx=scope_ctx)
                    mcp_actions_count += 1
                    history.append(f"Action: {action} with {args}\nOutput: {str(res)[:500]}")
                    
                    eval_res = await mcp_call_tool_tracked(client, "browser_evaluate", {"script": "window.location.href"}, scan_id, module_name, feat_name, scope_ctx=scope_ctx)
                    mcp_actions_count += 1
                    curr_url = str(eval_res).lower()
                    
                    is_curr_out_of_scope = False
                    if scope_ctx and scope_ctx.scan_scope in ("module", "MODULE", "MODULE_WISE"):
                        # Double check current url path matches target urls
                        target_paths_only = [urllib.parse.urlparse(u).path.lower().strip('/') for u in scope_ctx.target_urls]
                        curr_url_path = urllib.parse.urlparse(curr_url).path.lower().strip('/')
                        is_target = any(tp in curr_url_path for tp in target_paths_only) if target_paths_only else False
                        is_dashboard = any(x in curr_url_path for x in ["dashboard", "login", "logout"])
                        is_curr_out_of_scope = not is_target
                        
                    if is_curr_out_of_scope and not is_dashboard:
                        log_pipeline(scan_id, scope_ctx, "MCP-SCOPE", f"Backtracking from out-of-scope page: {curr_url}")
                        safety_log.append({
                            "action": "navigation",
                            "element": "browser_location",
                            "reason": f"URL {curr_url} is outside selected module scope",
                            "classification": "EXTERNAL_TO_SCOPE",
                            "execution_status": "EXTERNAL_TO_SCOPE"
                        })
                        await mcp_call_tool_tracked(client, "browser_navigate", {"url": target_path}, scan_id, module_name, feat_name, scope_ctx=scope_ctx)
                        mcp_actions_count += 1
                        
                except Exception as e:
                    history.append(f"Action: {action} with {args}\nError: {e}")

            # Post-Exploration analysis
            structurizer_prompt = f"""
            Review the exploration log of the target feature '{feat_name}' and compile its structured behavior map.
            LOG DETAILS:
            {chr(10).join(history)}
            
            Return ONLY a JSON response matching the SCHEMA:
            {{
              "feature_name": "{feat_name}",
              "feature_type": "Create / Search / Filter / Navigation",
              "fields": [
                {{"name": "field_id", "type": "text/checkbox/select", "required": true, "placeholder": "..."}}
              ],
              "validations": [
                {{"trigger_action": "form submit", "expected_message": "error msg text", "is_observed": true}}
              ],
              "transitions": [
                {{"action": "click submit", "destination_url": "url path", "modal_opened": false, "is_observed": true}}
              ],
              "observed_workflows": ["Step-by-step logic description"]
            }}
            """
            try:
                with track_llm_call(scan_id, f"Behavior Structure Compilation: {feat_name}"):
                    resp = model.generate_content(structurizer_prompt)
                mcp_llm_calls_count += 1
                resp_txt = resp.text.strip()
                if "```json" in resp_txt:
                    resp_txt = resp_txt.split("```json")[1].split("```")[0]
                elif "```" in resp_txt:
                    resp_txt = resp_txt.split("```")[1].split("```")[0]
                    
                behavior_data = json.loads(resp_txt.strip())
                
                feature_info.feature_type = behavior_data.get("feature_type", "Verification")
                feature_info.fields = [FieldSchema(**f) for f in behavior_data.get("fields", [])]
                feature_info.validations = [ValidationRecord(source_classification=feature_info.source_classification, **v) for v in behavior_data.get("validations", [])]
                feature_info.transitions = [TransitionRecord(source_classification=feature_info.source_classification, **t) for t in behavior_data.get("transitions", [])]
                feature_info.observed_workflows = behavior_data.get("observed_workflows", [])
            except Exception as e:
                add_scan_log(scan_id, f"[WARNING] Structured behavior compilation failed: {e}")
                if feature_info.source_classification == "OBSERVED_BEHAVIOR":
                    feature_info.is_observed = False
                    feature_info.source_classification = "STRUCTURAL_EVIDENCE"

            feat_dur = time.perf_counter() - feat_loop_start
            total_explore_dur += feat_dur
            add_scan_log(scan_id, f"[MCP] [END] Exploring feature: {feat_name} | duration={feat_dur:.2f}s")
            save_completed_stage_dur(scan_id, "Behavioral Exploration", total_explore_dur)

        # Playwright Core fallback: runs the full feature_queue deterministically when MCP
        # could not even complete its health probe. Never falls back to root/public exploration
        # - it receives the exact same already-scope-filtered queue MCP would have used.
        pw_core_metrics = {"pw_core_attempts": 0, "pw_core_successful_calls": 0, "pw_core_failed_calls": 0, "pw_core_duration": 0.0}
        if not mcp_available:
            from .playwright_core_engine import run_playwright_core_exploration
            pw_core_metrics = await run_playwright_core_exploration(
                scan_id, url, feature_queue, storage_state_path, scope_ctx, discovered_modules
            )

        mcp_succeeded = mcp_available and nav_attempts > 0 and nav_failures < nav_attempts
        pw_core_succeeded = pw_core_metrics.get("pw_core_successful_calls", 0) > 0
        if mcp_succeeded:
            behavior_engine_status = "MCP"
        elif pw_core_succeeded:
            behavior_engine_status = "COMPLETED_WITH_FALLBACK"
        else:
            behavior_engine_status = "FAILED"

        log_mcp_behavior_summary(scan_id, scope_ctx, "PASS 1", feature_queue, features_attempted, mcp_actions_count,
                                  discovered_modules, behavior_engine_status=behavior_engine_status,
                                  pw_core_metrics=pw_core_metrics, nav_attempts=nav_attempts, nav_failures=nav_failures)

        # If both engines produced zero successful actions, the queue never got a chance to
        # execute anything on either engine - fail clearly instead of silently producing a
        # "completed" scan of 100% STRUCTURAL_EVIDENCE test cases with no indication that
        # behavioral exploration failed. Applies to both MODULE and ENTIRE scope.
        if not mcp_succeeded and not pw_core_succeeded:
            if not mcp_available:
                mcp_failure_desc = "MCP was unavailable (health probe failed)"
            else:
                mcp_failure_desc = f"all {nav_attempts} MCP navigation attempt(s) failed"
            target_desc = (f"{scope_ctx.parent_module} -> {scope_ctx.selected_module}"
                            if is_module_scope else url)
            err_msg = (f"Behavioral exploration failed on both engines: {mcp_failure_desc}, "
                       f"and the Playwright Core fallback also produced no successful actions. "
                       f"No behavioral evidence is available for {target_desc}.")
            log_pipeline(scan_id, scope_ctx, "MCP", f"[ERROR] {err_msg}")
            raise RuntimeError(err_msg)

        # 4. Compile Coverage Gaps Checklist - only meaningful for the MCP per-feature loop.
        # The Playwright Core fallback already deterministically covers the full queue in one
        # pass, so there is nothing left to gap-fill via a second pass.
        for target in (feature_queue if mcp_available else []):
            module_name = target["module"]
            feat_name = target["feature"]

            matched_mod_key = None
            for k in discovered_modules.keys():
                if module_name.lower() in k.lower() or k.lower() in module_name.lower():
                    matched_mod_key = k
                    break

            has_tests = False
            if matched_mod_key:
                has_tests = any(f.feature_name == feat_name and f.is_observed for f in discovered_modules[matched_mod_key].features)
            if not has_tests:
                coverage_gaps.append({
                    "module": module_name,
                    "page": target["page"],
                    "feature": feat_name,
                    "url": target_path,
                    "gap": "Missing behavioral validations or field testing scenarios."
                })
                
        # Clean MCP shutdown
        update_scan_perf_data(scan_id, {"stage": "MCP Shutdown", "stage_started_at": time.time() * 1000})
        sd_start = time.perf_counter()
        await client.disconnect()
        sd_dur = time.perf_counter() - sd_start
        save_completed_stage_dur(scan_id, "MCP Shutdown", sd_dur)
        
    except Exception as e:
        log_pipeline(scan_id, scope_ctx, "MCP", f"[ERROR] MCP exploration failed: {str(e)}")
        try:
            log_pipeline(scan_id, scope_ctx, "MCP", "Shutting down browser...")
            await client.disconnect()
        except Exception:
            pass
        raise e

    update_scan_perf_data(scan_id, {
        "feature": None,
        "feature_started_at": None
    })
    
    metrics = {
        "mcp_actions_count": mcp_actions_count,
        "mcp_llm_calls_count": mcp_llm_calls_count,
        "behavior_engine": behavior_engine_status,
        "mcp_attempts": nav_attempts,
        "mcp_successful_calls": nav_attempts - nav_failures,
        "mcp_failed_calls": nav_failures,
        "mcp_duration": total_explore_dur,
        "pw_core_attempts": pw_core_metrics.get("pw_core_attempts", 0),
        "pw_core_successful_calls": pw_core_metrics.get("pw_core_successful_calls", 0),
        "pw_core_failed_calls": pw_core_metrics.get("pw_core_failed_calls", 0),
        "pw_core_duration": pw_core_metrics.get("pw_core_duration", 0.0)
    }

    initial_result = ExplorationResult(
        modules=list(discovered_modules.values()),
        safety_actions_log=safety_log,
        coverage_gaps=coverage_gaps
    )
    res_dict = initial_result.model_dump()
    res_dict["metrics"] = metrics
    return json.dumps(res_dict)

async def run_mcp_gap_exploration(scan_id: str, url: str, gaps: List[Dict[str, Any]],
                                  username: str = None, password: str = None,
                                  api_key: str = None, model_name: str = None, scope_ctx: ScopeContext = None,
                                  storage_state_path: str = None) -> str:
    """Pass 2: Real Gap-filling explorer that specifically navigates and executes to resolve logged coverage gaps."""
    if not gaps:
        return json.dumps([])

    log_pipeline(scan_id, scope_ctx, "MCP-SCOPE", f"[PASS 2] Launching Gap-filling Explorer to resolve {len(gaps)} coverage gaps...")

    gen_key = api_key or settings.GEMINI_API_KEY
    if not gen_key:
        return json.dumps([])

    client = PlaywrightMCPClient(storage_state_path=storage_state_path)
    resolved_features = []
    mcp_actions_count = 0
    mcp_llm_calls_count = 0
    nav_attempts = 0
    nav_failures = 0

    try:
        await client.connect(scan_id)
    except Exception as e:
        log_pipeline(scan_id, scope_ctx, "MCP-SCOPE", f"[WARNING] Gap-filling Explorer failed to connect: {e}")
        return json.dumps([])

    genai.configure(api_key=gen_key)
    selected_model = model_name or "gemini-1.5-flash"
    model = genai.GenerativeModel(selected_model)

    try:
        # Auth login session
        if username and password:
            try:
                await mcp_call_tool_tracked(client, "browser_navigate", {"url": url}, scan_id, "Gap Exploration", "Login Flow", scope_ctx=scope_ctx)
                login_prompt = f"""
                Sign in using the Playwright browser.
                URL: {url}
                Username: {username}
                Password: {password}
                Return JSON with sign-in action or sign-in completed.
                """
                for _ in range(4):
                    snapshot = await mcp_call_tool_tracked(client, "browser_snapshot", {}, scan_id, "Gap Exploration", "Login Flow", scope_ctx=scope_ctx)
                    with track_llm_call(scan_id, "Gap Explorer Login"):
                        resp = model.generate_content(f"{login_prompt}\n\nPAGE STATE:\n{snapshot[:2000]}")
                    text = resp.text.strip()
                    if "```json" in text:
                        text = text.split("```json")[1].split("```")[0]
                    cmd = json.loads(text.strip())
                    if cmd.get("login_complete") or cmd.get("action") == "done":
                        break
                    action = cmd.get("action")
                    if action == "browser_type":
                        await mcp_call_tool_tracked(client, "browser_type", {"selector": cmd["selector"], "text": cmd["text"]}, scan_id, "Gap Exploration", "Login Flow", scope_ctx=scope_ctx)
                    elif action == "browser_click":
                        await mcp_call_tool_tracked(client, "browser_click", {"selector": cmd["selector"]}, scan_id, "Gap Exploration", "Login Flow", scope_ctx=scope_ctx)
                    await asyncio.sleep(0.5)
            except Exception:
                pass

        for idx, gap in enumerate(gaps):
            # If navigation has failed for every gap attempted so far (with enough samples to
            # be confident it isn't a one-off), stop burning time retrying the same failure
            # against every remaining gap - MCP is not reachable right now.
            if nav_attempts >= 3 and nav_failures == nav_attempts:
                log_pipeline(scan_id, scope_ctx, "MCP", f"[WARNING] Aborting Pass 2 early: {nav_failures}/{nav_attempts} navigation attempts failed. Skipping {len(gaps) - idx} remaining gap(s).")
                break

            m_name = gap["module"]
            page_name = gap["page"]
            f_name = gap["feature"]
            gap_desc = gap["gap"]
            target_path = gap.get("url", url)

            feat_loop_start = time.perf_counter()
            feat_start_epoch = time.time() * 1000
            
            update_scan_perf_data(scan_id, {
                "module": m_name,
                "page": page_name,
                "feature": f"[GAP] {m_name} -> {f_name}",
                "feature_started_at": feat_start_epoch,
                "feature_progress": f"{idx+1} / {len(gaps)}"
            })

            log_pipeline(scan_id, scope_ctx, "MCP", f"[START] Exploring feature: {f_name}")
            log_pipeline(scan_id, scope_ctx, "MCP", f"Gap {idx+1}/{len(gaps)}\n      Resolving: {f_name}\n      🟢 RUNNING")
            
            nav_attempts += 1
            try:
                if not target_path.startswith("http"):
                    base_url = "/".join(url.split("/")[:3])
                    target_path = base_url + ("/" if not target_path.startswith("/") else "") + target_path
                await mcp_call_tool_tracked(client, "browser_navigate", {"url": target_path}, scan_id, m_name, f_name, scope_ctx=scope_ctx)
                mcp_actions_count += 1
            except Exception:
                nav_failures += 1
                continue

            resolved_feature = FeatureBehavior(
                feature_name=f_name,
                feature_type="Verification",
                is_observed=True,
                source_classification="OBSERVED_BEHAVIOR",
                execution_engine="MCP",
                fields=[],
                validations=[],
                transitions=[]
            )

            history = []
            for step in range(3):
                try:
                    page_snapshot = await mcp_call_tool_tracked(client, "browser_snapshot", {}, scan_id, m_name, f_name, scope_ctx=scope_ctx)
                    mcp_actions_count += 1
                except Exception:
                    page_snapshot = ""

                agent_prompt = f"""
                You are a QA Test Agent attempting to resolve a specific test coverage gap.
                TARGET MODULE: {m_name}
                PAGE: {page_name}
                FEATURE: {f_name}
                TARGET BEHAVIORAL GAP TO VERIFY: {gap_desc}
                
                HISTORY OF ACTIONS:
                {chr(10).join(history)}
                
                CURRENT PAGE DOM:
                {page_snapshot[:3000]}
                
                Execute browser actions specifically to trigger and observe the behavior for the gap.
                If you observe the error message, redirect, or success state matching the gap, return action "done".
                Return ONLY a JSON response:
                {{
                  "thought": "Reasoning",
                  "action": "browser_click | browser_type | browser_select_option | browser_evaluate | done",
                  "arguments": {{ ... }}
                }}
                """
                try:
                    with track_llm_call(scan_id, f"Gap Exploration decision: {f_name}"):
                        resp = model.generate_content(agent_prompt)
                    mcp_llm_calls_count += 1
                    resp_txt = resp.text.strip()
                    if "```json" in resp_txt:
                        resp_txt = resp_txt.split("```json")[1].split("```")[0]
                    decision = json.loads(resp_txt.strip())
                except Exception:
                    break

                action = decision.get("action", "done")
                if action == "done":
                    break

                args = decision.get("arguments", {})
                classification, reason = classify_action(action, args)
                if classification in ["DESTRUCTIVE", "UNKNOWN"]:
                    mcp_actions_count += 1
                    target_info = args.get("selector") or args.get("script") or ""
                    log_pipeline(scan_id, scope_ctx, "MCP-ACTION", f"{action}\ntarget={target_info}\nstatus=SKIPPED\nreason={reason}")
                    
                    resolved_feature.source_classification = "SKIPPED_SCENARIO"
                    resolved_feature.is_observed = False
                    continue

                try:
                    res = await mcp_call_tool_tracked(client, action, args, scan_id, m_name, f_name, scope_ctx=scope_ctx)
                    mcp_actions_count += 1
                    
                    eval_res = await mcp_call_tool_tracked(client, "browser_evaluate", {"script": "window.location.href"}, scan_id, m_name, f_name, scope_ctx=scope_ctx)
                    mcp_actions_count += 1
                    curr_url = str(eval_res).lower()
                    
                    is_curr_out_of_scope = False
                    if scope_ctx and scope_ctx.scan_scope in ("module", "MODULE", "MODULE_WISE"):
                        target_paths_only = [urllib.parse.urlparse(u).path.lower().strip('/') for u in scope_ctx.target_urls]
                        curr_url_path = urllib.parse.urlparse(curr_url).path.lower().strip('/')
                        is_target = any(tp in curr_url_path for tp in target_paths_only) if target_paths_only else False
                        is_dashboard = any(x in curr_url_path for x in ["dashboard", "login", "logout"])
                        is_curr_out_of_scope = not is_target
                        
                    if is_curr_out_of_scope and not is_dashboard:
                        await mcp_call_tool_tracked(client, "browser_navigate", {"url": target_path}, scan_id, m_name, f_name, scope_ctx=scope_ctx)
                        mcp_actions_count += 1
                        
                except Exception as e:
                    pass

            structurizer_prompt = f"""
            Review the exploration log of the gap feature '{f_name}' and compile its structured behavior map.
            LOG DETAILS:
            {chr(10).join(history)}
            
            Return ONLY a JSON response:
            {{
              "feature_name": "{f_name}",
              "feature_type": "Verification",
              "fields": [],
              "validations": [
                {{"trigger_action": "form submit", "expected_message": "error msg text", "is_observed": true}}
              ],
              "transitions": []
            }}
            """
            try:
                with track_llm_call(scan_id, f"Gap Behavior structure: {f_name}"):
                    resp = model.generate_content(structurizer_prompt)
                mcp_llm_calls_count += 1
                resp_txt = resp.text.strip()
                if "```json" in resp_txt:
                    resp_txt = resp_txt.split("```json")[1].split("```")[0]
                behavior_data = json.loads(resp_txt.strip())
                
                resolved_feature.fields = [FieldSchema(**f) for f in behavior_data.get("fields", [])]
                resolved_feature.validations = [ValidationRecord(source_classification=resolved_feature.source_classification, **v) for v in behavior_data.get("validations", [])]
                resolved_feature.transitions = [TransitionRecord(source_classification=resolved_feature.source_classification, **t) for t in behavior_data.get("transitions", [])]
            except Exception:
                resolved_feature.is_observed = False
                resolved_feature.source_classification = "STRUCTURAL_EVIDENCE"

            feat_dur = time.perf_counter() - feat_loop_start
            add_scan_log(scan_id, f"[MCP] [END] Exploring feature: {f_name} | duration={feat_dur:.2f}s")
            
            resolved_features.append({
                "module_name": m_name,
                "feature": resolved_feature.model_dump()
            })

        observed = sum(1 for r in resolved_features if r["feature"].get("source_classification") == "OBSERVED_BEHAVIOR")
        skipped = sum(1 for r in resolved_features if r["feature"].get("source_classification") == "SKIPPED_SCENARIO")
        structural = len(resolved_features) - observed - skipped
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT perf_data FROM scans WHERE id = ?", (scan_id,))
        row = cursor.fetchone()
        conn.close()
        pass2_perf = json.loads(row["perf_data"]) if row and row["perf_data"] else {}
        pass2_op_log = pass2_perf.get("operations_log", [])
        add_scan_log(scan_id, "=" * 50)
        add_scan_log(scan_id, "MCP BEHAVIOR SUMMARY (PASS 2)")
        add_scan_log(scan_id, "-" * 50)
        add_scan_log(scan_id, f"Gaps queued: {len(gaps)}")
        add_scan_log(scan_id, f"Gaps attempted: {nav_attempts}")
        add_scan_log(scan_id, f"MCP tool calls: {len(pass2_op_log)}")
        add_scan_log(scan_id, f"Successful tool calls: {sum(1 for o in pass2_op_log if o.get('status') == 'completed')}")
        add_scan_log(scan_id, f"Failed tool calls: {sum(1 for o in pass2_op_log if o.get('status') == 'failed')}")
        add_scan_log(scan_id, f"Observed behaviors: {observed}")
        add_scan_log(scan_id, f"Structural behaviors: {structural}")
        add_scan_log(scan_id, f"Skipped behaviors: {skipped}")
        add_scan_log(scan_id, f"MCP actions: {mcp_actions_count}")
        add_scan_log(scan_id, "=" * 50)

        await client.disconnect()

    except Exception as e:
        add_scan_log(scan_id, f"[MCP] [ERROR] Gap exploration failed: {str(e)}")
        try:
            await client.disconnect()
        except Exception:
            pass

    update_scan_perf_data(scan_id, {
        "feature": None,
        "feature_started_at": None
    })
    
    return json.dumps({
        "resolutions": resolved_features,
        "metrics": {
            "mcp_actions_count": mcp_actions_count,
            "mcp_llm_calls_count": mcp_llm_calls_count
        }
    })
