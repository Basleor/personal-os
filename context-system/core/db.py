"""
Personal OS — Layer 01: 基础数据访问层 (SQLite)
External drive: /Volumes/13384923891/hermes-agent/
v2.3 — Unified data access: ideas, preferences, decisions, tasks, profiling.
"""
import sqlite3
import os
from typing import Optional

DB_PATH = "/Volumes/13384923891/hermes-agent/context-system/analytics.db"


def get_connection():
    """Return a SQLite connection to the analytics database."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize all core tables (idempotent — safe to call repeatedly).
    Handles migration from legacy whiteboard_ideas → ideas table."""
    conn = get_connection()
    cursor = conn.cursor()

    # ── Ideas table (replaces whiteboard_ideas) ──
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ideas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            session_id TEXT,
            current_task_type TEXT,
            raw_idea TEXT,
            status TEXT DEFAULT 'pending',
            project TEXT DEFAULT ''
        )
    ''')

    # ── Migrate data from legacy whiteboard_ideas if it exists ──
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='whiteboard_ideas'"
    )
    if cursor.fetchone():
        # Copy any rows not already in ideas table
        cursor.execute('''
            INSERT OR IGNORE INTO ideas (id, timestamp, session_id, current_task_type, raw_idea, status)
            SELECT id, timestamp, session_id, current_task_type, raw_idea, status
            FROM whiteboard_ideas
        ''')
        cursor.execute("DROP TABLE whiteboard_ideas")

    # ── Migrate: add project column to existing tables (safe, ignores if already exists) ──
    for table, col_def in [
        ("ideas", "project TEXT DEFAULT ''"),
        ("session_tasks", "project TEXT DEFAULT ''"),
        ("session_tasks", "event_note TEXT DEFAULT ''"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
        except sqlite3.OperationalError:
            pass  # Column already exists

    # ── Backward-compat VIEW for ctx-report (reads whiteboard_ideas) ──
    cursor.execute('''
        CREATE VIEW IF NOT EXISTS whiteboard_ideas AS
        SELECT id, timestamp, session_id, current_task_type, raw_idea, status FROM ideas
    ''')

    # ── Session tasks (tracker.py) ──
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS session_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            session_id TEXT,
            task_type TEXT,
            description TEXT,
            event_note TEXT DEFAULT '',
            project TEXT DEFAULT ''
        )
    ''')

    # ── Profiler events (profiler.py) ──
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS profiler_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            session_id TEXT,
            event_type TEXT,
            payload TEXT
        )
    ''')

    # ── User preferences (decision advisor seed) ──
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            domain TEXT,
            preference_key TEXT,
            preference_value TEXT,
            confidence REAL,
            source_session TEXT,
            notes TEXT
        )
    ''')

    # ── Decisions log (decision advisor seed) ──
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            session_id TEXT,
            context TEXT,
            options TEXT,
            chosen TEXT,
            rationale TEXT
        )
    ''')

    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════
#  Ideas CRUD (used by ideas.py and ctx-idea)
# ═══════════════════════════════════════════════════════════════

def insert_idea(timestamp: str, session_id: str, task_type: str, raw_idea: str, project: str = "") -> int:
    """Insert a new idea row. Returns the new row id."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO ideas (timestamp, session_id, current_task_type, raw_idea, project) VALUES (?, ?, ?, ?, ?)",
        (timestamp, session_id, task_type, raw_idea, project)
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


def get_pending_ideas(limit: int = 100, project: Optional[str] = None) -> list:
    """Retrieve all pending ideas (status='pending'). Optionally filter by project.
    
    When project is None, returns all pending ideas (no filter).
    When project is an empty string '', returns ideas with no project tag.
    When project is a specific value, returns only ideas with that project.
    """
    conn = get_connection()
    cursor = conn.cursor()
    if project is None:
        cursor.execute(
            "SELECT id, timestamp, session_id, current_task_type, raw_idea "
            "FROM ideas WHERE status='pending' ORDER BY id ASC LIMIT ?",
            (limit,)
        )
    else:
        cursor.execute(
            "SELECT id, timestamp, session_id, current_task_type, raw_idea "
            "FROM ideas WHERE status='pending' AND project=? ORDER BY id ASC LIMIT ?",
            (project, limit)
        )
    rows = cursor.fetchall()
    conn.close()
    return rows


def mark_ideas_flushed(idea_ids: list):
    """Mark a batch of ideas as flushed."""
    if not idea_ids:
        return
    conn = get_connection()
    cursor = conn.cursor()
    cursor.executemany(
        "UPDATE ideas SET status='flushed' WHERE id=?",
        [(i,) for i in idea_ids]
    )
    conn.commit()
    conn.close()


def get_idea_stats() -> tuple:
    """Return (pending_count, flushed_count) for ideas."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, COUNT(*) FROM ideas GROUP BY status")
    counts = dict(cursor.fetchall())
    conn.close()
    return counts.get("pending", 0), counts.get("flushed", 0)


# ═══════════════════════════════════════════════════════════════
#  User Preferences CRUD (used by profiler.py and ctx-advisor)
# ═══════════════════════════════════════════════════════════════

def insert_preference(timestamp: str, domain: str, key: str, value: str,
                      confidence: float, source_session: str, notes: str = "") -> int:
    """Record a user preference observation."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO user_preferences (timestamp, domain, preference_key, "
        "preference_value, confidence, source_session, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (timestamp, domain, key, value, confidence, source_session, notes)
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


def get_preferences_by_domain(domain: str) -> list:
    """Return all preferences for a given domain, newest first."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, timestamp, domain, preference_key, preference_value, "
        "confidence, source_session, notes FROM user_preferences "
        "WHERE domain=? ORDER BY id DESC",
        (domain,)
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_high_confidence_preferences(min_confidence: float = 0.6) -> list:
    """Return all preferences above a confidence threshold, grouped by domain."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT domain, preference_key, preference_value, confidence, "
        "MAX(timestamp) as latest FROM user_preferences "
        "WHERE confidence >= ? GROUP BY domain, preference_key "
        "ORDER BY domain, confidence DESC",
        (min_confidence,)
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


# ═══════════════════════════════════════════════════════════════
#  Decisions CRUD (used by profiler.py)
# ═══════════════════════════════════════════════════════════════

def insert_decision(timestamp: str, session_id: str, context: str,
                    options: str, chosen: str, rationale: str = "") -> int:
    """Record a decision event."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO decisions (timestamp, session_id, context, options, chosen, rationale) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (timestamp, session_id, context, options, chosen, rationale)
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


# ═══════════════════════════════════════════════════════════════
#  Project helpers — list known projects from ideas + session_tasks
# ═══════════════════════════════════════════════════════════════

def get_session_tasks_in_window(start_ts: str, end_ts: str) -> list:
    """Return session_tasks rows whose timestamp falls within [start_ts, end_ts].

    Timestamps are compared as strings (ISO-ish format: YYYY-MM-DD HH:MM:SS).
    Returns list of sqlite3.Row objects with all columns.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM session_tasks WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC",
        (start_ts, end_ts)
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_project_list() -> list:
    """Return a sorted list of all non-empty project names used across
    both ideas and session_tasks tables."""
    conn = get_connection()
    cursor = conn.cursor()
    projects = set()
    for table in ("ideas", "session_tasks"):
        try:
            cursor.execute(f"SELECT DISTINCT project FROM {table} WHERE project != ''")
            for row in cursor.fetchall():
                val = row[0] if isinstance(row, tuple) else row["project"]
                if val:
                    projects.add(val)
        except sqlite3.OperationalError:
            pass  # Column may not exist yet
    conn.close()
    return sorted(projects)


# ═══════════════════════════════════════════════════════════════
#  Vector index table (P3 — hybrid semantic search)
# ═══════════════════════════════════════════════════════════════

def init_vector_index():
    """Create the vector_index table (idempotent)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vector_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_table TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            vector_blob TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(source_table, source_id)
        )
    ''')
    conn.commit()
    conn.close()
