"""
Personal OS — Layer 02: 灵感捕获核心 (Ideas Core)
External drive: /Volumes/13384923891/hermes-agent/
v2.2 — Fragment interception, async semantic stitching via flush.
All data access through db.py — no raw SQL in this module.
"""
import time
import os

from typing import Optional
from datetime import datetime, timedelta
from core.db import (
    init_db, insert_idea, get_pending_ideas as db_get_pending,
    mark_ideas_flushed as db_mark_flushed, get_idea_stats,
    get_session_tasks_in_window,
)

WHITEBOARD_DIR = "/Volumes/13384923891/hermes-agent/context-system/Whiteboard"
IDEAS_DIR = os.path.join(WHITEBOARD_DIR, "ideas")


def _ensure_db():
    init_db()


def capture_idea(session_id: str, task_type: str, text: str, project: str = "") -> int:
    """Capture a fragment idea and persist to the external drive database.
    Returns the new idea's row id."""
    _ensure_db()
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    return insert_idea(ts, session_id, task_type, text, project)


def get_pending_ideas(limit: int = 100, project: Optional[str] = None) -> list:
    """Retrieve all pending ideas for semantic stitching.
    Optionally filter by project tag."""
    _ensure_db()
    return db_get_pending(limit, project=project)


def mark_ideas_flushed(idea_ids: list):
    """Mark a batch of ideas as flushed after processing."""
    _ensure_db()
    db_mark_flushed(idea_ids)


def generate_flush_report(session_id: str = None) -> str:
    """Generate Obsidian-friendly flush report.
    
    Creates:
      - Individual .md files per idea in Whiteboard/ideas/ (with YAML frontmatter)
      - A summary index at Whiteboard/Whiteboard_Log_YYYYMMDD.md with [[links]]
    
    Returns the path to the summary index file, or None if no pending ideas.
    """
    ideas = get_pending_ideas()
    if not ideas:
        return None

    date_str = time.strftime("%Y%m%d")
    date_full = time.strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(IDEAS_DIR, exist_ok=True)

    # ── Step 1: 给每个灵感创建独立 .md 文件 ──
    idea_files = []
    for i, (idea_id, ts, sess, task, raw) in enumerate(ideas, 1):
        slug = raw[:20].replace(" ", "-").replace("/", "-")
        filename = f"{date_str}_{idea_id}_{slug}.md"
        filepath = os.path.join(IDEAS_DIR, filename)

        content = f"""---
id: {idea_id}
date: {ts}
session: {sess}
task: {task}
tags: [灵感, {task.replace(' ', '-')}]
---

# {raw}

*捕获于 {ts} | 任务上下文: {task}*
"""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        
        idea_files.append({
            "id": idea_id,
            "filename": filename,
            "ts": ts,
            "task": task,
            "raw": raw,
        })

    # ── Step 2: 生成摘要索引（带 [[链接]]） ──
    report_path = os.path.join(WHITEBOARD_DIR, f"Whiteboard_Log_{date_str}.md")
    
    lines = [
        "---",
        f"date: {date_full}",
        f"session: {session_id or 'N/A'}",
        f"count: {len(ideas)}",
        "tags: [flush, 灵感缝合]",
        "---",
        "",
        f"# 🪐 Ideas Flush — {date_str}",
        "",
        f"**缝合时间:** {date_full}",
        f"**碎片数量:** {len(ideas)}",
        "",
        "---",
        "",
        "## 本次缝合的灵感",
        "",
    ]

    # Group by task type
    from collections import defaultdict
    groups = defaultdict(list)
    for item in idea_files:
        groups[item["task"]].append(item)

    for task, items in groups.items():
        lines.append(f"### {task}")
        lines.append("")
        for item in items:
            lines.append(f"- [[ideas/{item['filename'].replace('.md','')}|{item['raw']}]]  `{item['ts'][:16]}`")
        lines.append("")

    lines.extend([
        "---",
        "",
        "## 在 Obsidian 中打开",
        "",
        f"将此目录挂载为 Obsidian 仓库: `{WHITEBOARD_DIR}`",
        f"所有灵感碎片在 `ideas/` 子目录中，可通过 [[双向链接]] 自由关联。",
        "",
        "> 💡 在 Obsidian 图谱视图中可以看到碎片之间的隐藏联系。",
    ])

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    mark_ideas_flushed([i[0] for i in ideas])

    return report_path


def prepare_flush_context() -> dict:
    """Prepare rich context for LLM semantic stitching.

    For each pending idea, finds the closest session_task within ±60 minutes
    to establish the "birth soil" — what the user was doing and why.

    Returns:
        {
            "ideas": [...],           # list of (id, ts, session, task, raw) tuples
            "task_context": {...},    # idea_id -> {task_type, description, event_note}
            "meta_goals": [...],      # unique event_notes (deduped)
            "prompt_text": "..."      # natural-language prompt ready for LLM
        }
    """
    ideas = get_pending_ideas()
    if not ideas:
        return {"ideas": [], "task_context": {}, "meta_goals": [], "prompt_text": ""}

    WINDOW_MINUTES = 60
    task_context = {}
    meta_goals_set = set()

    for idea_id, ts, sess, task, raw in ideas:
        # Parse idea timestamp and compute window
        try:
            idea_dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            task_context[idea_id] = {
                "task_type": task,
                "description": "",
                "event_note": "",
            }
            continue

        window_start = (idea_dt - timedelta(minutes=WINDOW_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
        window_end = (idea_dt + timedelta(minutes=WINDOW_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")

        candidates = get_session_tasks_in_window(window_start, window_end)

        if not candidates:
            task_context[idea_id] = {
                "task_type": task,
                "description": "",
                "event_note": "",
            }
            continue

        # Find the closest task by time delta
        best = None
        best_delta = None
        for row in candidates:
            try:
                row_dt = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            delta = abs((idea_dt - row_dt).total_seconds())
            if best is None or delta < best_delta:
                best = row
                best_delta = delta

        ctx = {
            "task_type": best["task_type"] or task,
            "description": best["description"] or "",
            "event_note": best["event_note"] or "",
        }
        task_context[idea_id] = ctx

        # Use description as fallback when event_note is empty
        meta_goal = ctx["event_note"] or ctx["description"]
        if meta_goal:
            meta_goals_set.add(meta_goal)

    meta_goals = sorted(meta_goals_set)

    # ── Build the natural-language prompt ──
    prompt_lines = [
        "你是 Personal OS 的灵感缝合引擎。",
        "",
        "用户在过去一段时间内积累了一些灵感碎片，每条碎片产生于特定的任务上下文中。",
        "请从两个层面分析这些碎片：",
        "",
        "## 层面 1：任务上下文",
        "每条碎片产生时，用户正在做什么？这解释了碎片为什么会出现。",
        "",
        "## 层面 2：元目标",
        "这些任务最终是为了什么？站在更高层面，这些碎片指向什么方向？",
        "",
        "## 你要做的",
        "1. 按主题将碎片分组（不是按任务类型，是按内在关联）",
        "2. 发现碎片之间的隐藏联系",
        "3. 结合元目标，提出可执行的行动建议",
        '4. 标注"孤岛碎片"（暂时找不到关联的）',
        "",
        "## 碎片数据",
        "",
    ]

    for idea_id, ts, sess, task, raw in ideas:
        ctx = task_context.get(idea_id, {})
        meta_goal = ctx.get("event_note") or ctx.get("description") or "(未关联元目标)"

        prompt_lines.append(f"### 碎片 #{idea_id}")
        prompt_lines.append(f"- **灵感内容:** {raw}")
        prompt_lines.append(f"- **捕获时间:** {ts}")
        prompt_lines.append(f"- **任务类型:** {ctx.get('task_type', task)}")
        prompt_lines.append(f"- **任务描述:** {ctx.get('description') or '(无)'}")
        prompt_lines.append(f"- **元目标:** {meta_goal}")
        prompt_lines.append("")

    if meta_goals:
        prompt_lines.append("## 元目标汇总")
        for mg in meta_goals:
            prompt_lines.append(f"- {mg}")
        prompt_lines.append("")

    prompt_lines.extend([
        "## 灵感清单",
        "---",
    ])

    prompt_text = "\n".join(prompt_lines)

    return {
        "ideas": ideas,
        "task_context": task_context,
        "meta_goals": meta_goals,
        "prompt_text": prompt_text,
    }
