"""
Personal OS — Layer 01: 行为画像分析器
External drive: /Volumes/13384923891/hermes-agent/
v2.2 — Full behavioral profiling: daily summaries, weekly trends, task
distributions, and session timelines.

Generates behavioral profile insights from analytics.db data.
"""
import time
from typing import Optional
from core.db import get_connection, init_db


def _ensure_db():
    """Idempotent init — safe to call before any query."""
    init_db()


def record_profiler_event(session_id: str, event_type: str, payload: str = ""):
    """Record a behavioral event for profiling."""
    _ensure_db()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO profiler_events (timestamp, session_id, event_type, payload) VALUES (?, ?, ?, ?)",
        (time.strftime("%Y-%m-%d %H:%M:%S"), session_id, event_type, payload)
    )
    conn.commit()
    conn.close()


def get_session_stats() -> dict:
    """Generate basic session statistics."""
    _ensure_db()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(DISTINCT session_id) FROM profiler_events")
    session_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM profiler_events")
    event_count = cursor.fetchone()[0]
    conn.close()
    return {
        "total_sessions": session_count,
        "total_events": event_count,
    }


def get_daily_summary(date_str: str = None, project: Optional[str] = None) -> dict:
    """Return a daily task summary from session_tasks.

    Args:
        date_str: ISO date string (YYYY-MM-DD). Defaults to today.
        project: Optional project filter. None = all projects.

    Returns:
        dict with total_tasks, task_types, primary_type, sessions,
        first_task_time, last_task_time, active_hours.
        Returns empty dict when no data exists for the date.
    """
    _ensure_db()
    if date_str is None:
        date_str = time.strftime("%Y-%m-%d")

    conn = get_connection()
    cursor = conn.cursor()

    if project is None:
        cursor.execute(
            "SELECT COUNT(*) FROM session_tasks WHERE timestamp LIKE ?",
            (date_str + "%",)
        )
    else:
        cursor.execute(
            "SELECT COUNT(*) FROM session_tasks WHERE timestamp LIKE ? AND project=?",
            (date_str + "%", project)
        )
    total = cursor.fetchone()[0]

    if total == 0:
        conn.close()
        return {}

    if project is None:
        cursor.execute(
            "SELECT task_type, COUNT(*) as cnt FROM session_tasks "
            "WHERE timestamp LIKE ? GROUP BY task_type ORDER BY cnt DESC",
            (date_str + "%",)
        )
    else:
        cursor.execute(
            "SELECT task_type, COUNT(*) as cnt FROM session_tasks "
            "WHERE timestamp LIKE ? AND project=? GROUP BY task_type ORDER BY cnt DESC",
            (date_str + "%", project)
        )
    task_types = {row[0]: row[1] for row in cursor.fetchall()}

    primary_type = max(task_types, key=task_types.get) if task_types else None

    if project is None:
        cursor.execute(
            "SELECT COUNT(DISTINCT session_id) FROM session_tasks WHERE timestamp LIKE ?",
            (date_str + "%",)
        )
        sessions = cursor.fetchone()[0]
        cursor.execute(
            "SELECT MIN(timestamp), MAX(timestamp) FROM session_tasks WHERE timestamp LIKE ?",
            (date_str + "%",)
        )
    else:
        cursor.execute(
            "SELECT COUNT(DISTINCT session_id) FROM session_tasks WHERE timestamp LIKE ? AND project=?",
            (date_str + "%", project)
        )
        sessions = cursor.fetchone()[0]
        cursor.execute(
            "SELECT MIN(timestamp), MAX(timestamp) FROM session_tasks WHERE timestamp LIKE ? AND project=?",
            (date_str + "%", project)
        )
    first_ts, last_ts = cursor.fetchone()

    active_hours = None
    if first_ts and last_ts:
        from datetime import datetime
        fmt = "%Y-%m-%d %H:%M:%S"
        try:
            d1 = datetime.strptime(first_ts, fmt)
            d2 = datetime.strptime(last_ts, fmt)
            active_hours = round((d2 - d1).total_seconds() / 3600, 1)
        except ValueError:
            active_hours = None

    conn.close()
    return {
        "total_tasks": total,
        "task_types": task_types,
        "primary_type": primary_type,
        "sessions": sessions,
        "first_task_time": first_ts,
        "last_task_time": last_ts,
        "active_hours": active_hours,
    }


def get_weekly_summary(project: Optional[str] = None) -> dict:
    """Return a 7-day rolling summary from session_tasks.

    Args:
        project: Optional project filter. None = all projects.

    Returns:
        dict with daily_breakdown, trend, peak_day, total_tasks.
    """
    _ensure_db()
    conn = get_connection()
    cursor = conn.cursor()

    # Past 7 days
    if project is None:
        cursor.execute(
            "SELECT DATE(timestamp) as d, COUNT(*) as cnt, task_type "
            "FROM session_tasks "
            "WHERE timestamp >= DATE('now', '-7 days', 'localtime') "
            "GROUP BY d, task_type "
            "ORDER BY d ASC, cnt DESC"
        )
    else:
        cursor.execute(
            "SELECT DATE(timestamp) as d, COUNT(*) as cnt, task_type "
            "FROM session_tasks "
            "WHERE timestamp >= DATE('now', '-7 days', 'localtime') AND project=? "
            "GROUP BY d, task_type "
            "ORDER BY d ASC, cnt DESC",
            (project,)
        )
    rows = cursor.fetchall()

    # Build daily_breakdown: { "2026-05-18": {"total": 5, "primary": "💻 编程"}, ... }
    daily_breakdown = {}
    for row in rows:
        d, cnt, task_type = row
        if d not in daily_breakdown:
            daily_breakdown[d] = {"total": 0, "types": {}}
        daily_breakdown[d]["total"] += cnt
        daily_breakdown[d]["types"][task_type] = cnt

    for d in daily_breakdown:
        types = daily_breakdown[d]["types"]
        if types:
            daily_breakdown[d]["primary"] = max(types, key=types.get)
        else:
            daily_breakdown[d]["primary"] = None
        del daily_breakdown[d]["types"]

    # Total over 7 days
    total_current = sum(v["total"] for v in daily_breakdown.values())

    # Previous 7 days (for trend)
    if project is None:
        cursor.execute(
            "SELECT COUNT(*) FROM session_tasks "
            "WHERE timestamp >= DATE('now', '-14 days', 'localtime') "
            "AND timestamp < DATE('now', '-7 days', 'localtime')"
        )
    else:
        cursor.execute(
            "SELECT COUNT(*) FROM session_tasks "
            "WHERE timestamp >= DATE('now', '-14 days', 'localtime') "
            "AND timestamp < DATE('now', '-7 days', 'localtime') AND project=?",
            (project,)
        )
    total_prev = cursor.fetchone()[0]

    trend = None
    if total_prev > 0:
        trend = round((total_current - total_prev) / total_prev * 100, 1)

    # Peak day
    peak_day = None
    peak_count = 0
    for d, v in daily_breakdown.items():
        if v["total"] > peak_count:
            peak_count = v["total"]
            peak_day = d

    conn.close()
    return {
        "daily_breakdown": daily_breakdown,
        "trend": trend,
        "peak_day": peak_day,
        "peak_count": peak_count,
        "total_tasks": total_current,
        "prev_week_total": total_prev,
    }


def get_task_distribution(days: int = 30, project: Optional[str] = None) -> dict:
    """Return task-type distribution over the last N days.

    Args:
        days: Number of days to look back.
        project: Optional project filter. None = all projects.

    Returns:
        dict like {"💻 编程": {"count": 7, "percentage": 58.3}, ...}
        sorted by count descending.
    """
    _ensure_db()
    conn = get_connection()
    cursor = conn.cursor()

    if project is None:
        cursor.execute(
            "SELECT task_type, COUNT(*) as cnt FROM session_tasks "
            "WHERE timestamp >= DATE('now', ? || ' days', 'localtime') "
            "GROUP BY task_type ORDER BY cnt DESC",
            (f"-{days}",)
        )
    else:
        cursor.execute(
            "SELECT task_type, COUNT(*) as cnt FROM session_tasks "
            "WHERE timestamp >= DATE('now', ? || ' days', 'localtime') AND project=? "
            "GROUP BY task_type ORDER BY cnt DESC",
            (f"-{days}", project)
        )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return {}

    total = sum(r[1] for r in rows)
    result = {}
    for task_type, cnt in rows:
        result[task_type] = {
            "count": cnt,
            "percentage": round(cnt / total * 100, 1),
        }

    return result


# ═══════════════════════════════════════════════════════════════
#  Decision Advisor — Preference Tracking (Seed)
# ═══════════════════════════════════════════════════════════════

def record_preference(domain: str, key: str, value: str, confidence: float,
                      session_id: str, notes: str = "") -> int:
    """Record a user preference observation.

    Called by Hermes when it observes a user preference in conversation.
    High confidence (>0.7) preferences form the user's behavioral profile.
    """
    from core.db import insert_preference
    _ensure_db()
    return insert_preference(
        time.strftime("%Y-%m-%d %H:%M:%S"),
        domain, key, value, confidence, session_id, notes
    )


def get_domain_preferences(domain: str) -> list:
    """Return all known preferences for a given domain, newest first."""
    from core.db import get_preferences_by_domain
    _ensure_db()
    return get_preferences_by_domain(domain)


def get_preference_profile(min_confidence: float = 0.6) -> dict:
    """Return user preference profile summary.

    Groups high-confidence preferences by domain.
    Returns dict like {'product-design': [{'key': ..., 'value': ..., 'confidence': ...}], ...}
    """
    from core.db import get_high_confidence_preferences
    _ensure_db()
    rows = get_high_confidence_preferences(min_confidence)

    profile = {}
    for row in rows:
        domain = row[0]
        entry = {
            "key": row[1],
            "value": row[2],
            "confidence": row[3],
            "latest": row[4],
        }
        if domain not in profile:
            profile[domain] = []
        profile[domain].append(entry)

    return profile


def record_decision(session_id: str, context: str, options: str,
                    chosen: str, rationale: str = "") -> int:
    """Record a decision event — user made a choice among alternatives."""
    from core.db import insert_decision
    _ensure_db()
    return insert_decision(
        time.strftime("%Y-%m-%d %H:%M:%S"),
        session_id, context, options, chosen, rationale
    )


def get_session_timeline(limit: int = 10, project: Optional[str] = None) -> list:
    """Return the most recent tasks in reverse-chronological order.

    Args:
        limit: Maximum number of entries.
        project: Optional project filter. None = all projects.

    Returns:
        list of dicts with timestamp, task_type, description, session_id, event_note.
    """
    _ensure_db()
    conn = get_connection()
    cursor = conn.cursor()

    if project is None:
        cursor.execute(
            "SELECT timestamp, task_type, description, session_id, COALESCE(event_note,'') "
            "FROM session_tasks ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        )
    else:
        cursor.execute(
            "SELECT timestamp, task_type, description, session_id, COALESCE(event_note,'') "
            "FROM session_tasks WHERE project=? ORDER BY timestamp DESC LIMIT ?",
            (project, limit)
        )
    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "timestamp": row[0],
            "task_type": row[1],
            "description": row[2],
            "session_id": row[3],
            "event_note": row[4],
        }
        for row in rows
    ]
