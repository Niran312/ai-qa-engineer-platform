import sqlite3
import json
import os
from .config import settings

DB_FILE = settings.DATABASE_URL.replace("sqlite:///", "")

def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS scans (
        id TEXT PRIMARY KEY,
        url TEXT NOT NULL,
        description TEXT,
        status TEXT NOT NULL,
        progress_log TEXT,
        app_map TEXT,
        test_cases TEXT,
        excel_path TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    # Add perf_data column if it does not exist
    try:
        cursor.execute("ALTER TABLE scans ADD COLUMN perf_data TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

def create_scan(scan_id: str, url: str, description: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO scans (id, url, description, status, progress_log, app_map, test_cases, perf_data) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (scan_id, url, description, "pending", json.dumps([]), json.dumps({"nodes": [], "edges": []}), json.dumps([]), json.dumps({}))
    )
    conn.commit()
    conn.close()

def update_scan_status(scan_id: str, status: str, excel_path: str = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if excel_path:
        cursor.execute(
            "UPDATE scans SET status = ?, excel_path = ? WHERE id = ?",
            (status, excel_path, scan_id)
        )
    else:
        cursor.execute(
            "UPDATE scans SET status = ? WHERE id = ?",
            (status, scan_id)
        )
    conn.commit()
    conn.close()

def add_scan_log(scan_id: str, message: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT progress_log FROM scans WHERE id = ?", (scan_id,))
    row = cursor.fetchone()
    if row:
        logs = json.loads(row["progress_log"] or "[]")
        logs.append(message)
        cursor.execute(
            "UPDATE scans SET progress_log = ? WHERE id = ?",
            (json.dumps(logs), scan_id)
        )
        conn.commit()
    conn.close()

def update_scan_data(scan_id: str, app_map: dict = None, test_cases: list = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if app_map is not None and test_cases is not None:
        cursor.execute(
            "UPDATE scans SET app_map = ?, test_cases = ? WHERE id = ?",
            (json.dumps(app_map), json.dumps(test_cases), scan_id)
        )
    elif app_map is not None:
        cursor.execute(
            "UPDATE scans SET app_map = ? WHERE id = ?",
            (json.dumps(app_map), scan_id)
        )
    elif test_cases is not None:
        cursor.execute(
            "UPDATE scans SET test_cases = ? WHERE id = ?",
            (json.dumps(test_cases), scan_id)
        )
    conn.commit()
    conn.close()

def update_scan_perf_data(scan_id: str, updates: dict):
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
    # Merge updates
    perf_data.update(updates)
    cursor.execute(
        "UPDATE scans SET perf_data = ? WHERE id = ?",
        (json.dumps(perf_data), scan_id)
    )
    conn.commit()
    conn.close()

def save_completed_stage_dur(scan_id: str, stage_name: str, duration: float):
    if not scan_id:
        return
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
    
    stages = perf_data.get("stages_completed", {})
    stages[stage_name] = duration
    update_scan_perf_data(scan_id, {
        "stages_completed": stages
    })

def get_scan(scan_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM scans WHERE id = ?", (scan_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def get_all_scans():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, url, description, status, excel_path, created_at FROM scans ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


from contextlib import contextmanager
import time

@contextmanager
def track_stage(scan_id: str, stage_name: str):
    if not scan_id:
        yield
        return
        
    start_time = time.perf_counter()
    start_epoch = time.time() * 1000
    
    update_scan_perf_data(scan_id, {
        "stage": stage_name,
        "stage_started_at": start_epoch
    })
    
    try:
        yield
    finally:
        dur = time.perf_counter() - start_time
        
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
        
        stages_completed = perf_data.get("stages_completed", {})
        stages_completed[stage_name] = dur
        
        # Match key for final perf model if applicable
        if stage_name == "Static Crawler":
            perf_data["crawler"] = perf_data.get("crawler", {})
            perf_data["crawler"]["duration"] = dur
        elif stage_name == "MCP Behavioral Exploration":
            perf_data["mcp"] = perf_data.get("mcp", {})
            perf_data["mcp"]["exploration_duration"] = dur
        elif stage_name == "Coverage Analysis":
            perf_data["coverage"] = {"duration": dur}
        elif stage_name == "Gap Exploration":
            perf_data["gap_exploration"] = {"duration": dur}
        elif stage_name == "Test Case Generation":
            perf_data["generation"] = {"duration": dur}
        elif stage_name == "Quality Gate":
            perf_data["quality_gate"] = {"duration": dur}
        elif stage_name == "Excel Export":
            perf_data["excel"] = {"duration": dur}
            
        update_scan_perf_data(scan_id, {
            "stages_completed": stages_completed,
            **perf_data
        })

@contextmanager
def track_llm_call(scan_id: str, stage_name: str):
    if not scan_id:
        yield
        return
        
    start_time = time.perf_counter()
    start_epoch = time.time() * 1000
    
    update_scan_perf_data(scan_id, {
        "llm_started_at": start_epoch,
        "llm_stage": stage_name
    })
    
    try:
        yield
    finally:
        dur = time.perf_counter() - start_time
        
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
        
        llm_metrics = perf_data.get("llm", {"calls": 0, "duration": 0.0})
        llm_metrics["calls"] += 1
        llm_metrics["duration"] += dur
        
        llm_log = perf_data.get("llm_calls_log", [])
        llm_log.append({
            "stage": stage_name,
            "duration": dur
        })
        
        update_scan_perf_data(scan_id, {
            "llm_started_at": None,
            "llm_stage": None,
            "llm": llm_metrics,
            "llm_calls_log": llm_log
        })
