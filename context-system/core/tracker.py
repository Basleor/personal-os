"""
Personal OS — Layer 01: 任务流转追踪器
External drive: /Volumes/13384923891/hermes-agent/
Seed — full implementation pending v2.2+ evolution.

Tracks task context switches within a session and persists to analytics.db.
"""
import time
from core.db import get_connection


def track_task_switch(session_id: str, task_type: str, description: str = "", project: str = ""):
    """Record a task context switch event."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO session_tasks (timestamp, session_id, task_type, description, project) VALUES (?, ?, ?, ?, ?)",
        (time.strftime("%Y-%m-%d %H:%M:%S"), session_id, task_type, description, project)
    )
    conn.commit()
    conn.close()


def get_session_task_flow(session_id: str) -> list:
    """Retrieve the task flow for a given session."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT timestamp, task_type, description FROM session_tasks WHERE session_id=? ORDER BY id ASC",
        (session_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [(r["timestamp"], r["task_type"], r["description"]) for r in rows]
