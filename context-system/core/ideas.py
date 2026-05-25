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
from collections import defaultdict
from core.db import (
    init_db, insert_idea, get_pending_ideas as db_get_pending,
    mark_ideas_flushed as db_mark_flushed, get_idea_stats,
    get_session_tasks_in_window,
)

WHITEBOARD_DIR = "/Volumes/13384923891/hermes-agent/context-system/Whiteboard"
IDEAS_DIR = os.path.join(WHITEBOARD_DIR, "ideas")


def _ensure_db():
    init_db()


def capture_idea(session_id: str, task_type: str, text: str, project: str = "", source: str = "popup", related_task: str = "") -> int:
    """Capture a fragment idea.
    
    source: 'popup' (⌃I), 'cli' (xw idea), 'reflect' (深度反思)
    related_task: 产生此灵感时正在做的事（空=灵感独立于当前任务）
    """
    _ensure_db()
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    return insert_idea(ts, session_id, task_type, text, project, source, related_task)


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
    """方案 B: 关联度比对缝合引擎。

    为每个灵感收集时间窗口内**所有**候选任务（而非只取最近一条），
    同时汇总所有活跃会话及其元目标。LLM 在缝合时自行判断：
    1. 碎片之间按内容相似度聚类
    2. 每个聚类与哪个会话上下文关联度最高
    3. 无法匹配的标为孤岛

    Returns:
        {
            "ideas": [...],              # pending 灵感列表
            "sessions": {...},           # session_id → {task_count, meta_goals, timeline}
            "idea_contexts": {...},      # idea_id → [候选任务列表]
            "prompt_text": "..."         # 含聚类指令的完整 prompt
        }
    """
    ideas = get_pending_ideas()
    if not ideas:
        return {"ideas": [], "sessions": {}, "idea_contexts": {}, "prompt_text": ""}

    WINDOW_MINUTES = 60
    idea_contexts = {}
    all_sessions = defaultdict(lambda: {"tasks": [], "meta_goals": set(), "task_types": set()})

    for idea_id, ts, sess, task, raw in ideas:
        try:
            idea_dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            idea_contexts[idea_id] = []
            continue

        window_start = (idea_dt - timedelta(minutes=WINDOW_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
        window_end = (idea_dt + timedelta(minutes=WINDOW_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")

        candidates = get_session_tasks_in_window(window_start, window_end)

        # 按 session 分组候选任务
        idea_contexts[idea_id] = []
        for row in candidates:
            sid = row["session_id"] or "unknown"
            ctx = {
                "session_id": sid,
                "timestamp": row["timestamp"],
                "task_type": row["task_type"] or "📋 通用",
                "description": row["description"] or "",
                "event_note": row["event_note"] or "",
            }
            idea_contexts[idea_id].append(ctx)

            # 累积到会话汇总
            all_sessions[sid]["tasks"].append(row["timestamp"])
            all_sessions[sid]["task_types"].add(ctx["task_type"])
            note = ctx["event_note"] or ctx["description"]
            if note:
                all_sessions[sid]["meta_goals"].add(note)

    # ── 整理会话汇总 ──
    sessions_summary = {}
    for sid, data in all_sessions.items():
        tasks_sorted = sorted(data["tasks"])
        sessions_summary[sid] = {
            "task_count": len(tasks_sorted),
            "first_task": tasks_sorted[0] if tasks_sorted else "",
            "last_task": tasks_sorted[-1] if tasks_sorted else "",
            "task_types": sorted(data["task_types"]),
            "meta_goals": sorted(data["meta_goals"]),
        }

    # ── 生成方案 B 专有的 LLM prompt ──
    prompt_lines = [
        "你是 Personal OS 的灵感缝合引擎（方案 B：关联度比对模式）。",
        "",
        "## 核心逻辑",
        "你有三类数据：灵感碎片本身、每条碎片时间窗口内的候选任务、以及各活跃会话的元目标。",
        "请按以下步骤分析：",
        "",
        "### 步骤 1：碎片聚类",
        "按内容相似度将碎片分组（不看任务类型标签，看语义）。",
        "例如「Obsidian 链接」「双向链接」「知识图谱」→ 聚类为「知识管理」。",
        "",
        "### 步骤 2：会话匹配",
        "每个聚类与下方列出的各会话上下文比对，选关联度最高的归属。",
        "判断标准：聚类的主题是否与该会话的元目标一致？",
        "一个聚类可以匹配多个会话（如果确实跨会话），但必须说明理由。",
        "",
        "### 步骤 3：孤岛判定",
        "无法匹配到任何会话上下文、或内容与其他碎片无关联的 → 标为「孤岛」。",
        "",
        "## 活跃会话",
        "",
    ]

    for sid, summary in sessions_summary.items():
        prompt_lines.append(f"### 会话: {sid}")
        prompt_lines.append(f"- 活跃时段: {summary['first_task'][:16]} → {summary['last_task'][:16]}")
        prompt_lines.append(f"- 任务类型: {', '.join(summary['task_types'])}")
        if summary["meta_goals"]:
            prompt_lines.append(f"- 元目标:")
            for mg in summary["meta_goals"]:
                prompt_lines.append(f"  - {mg}")
        prompt_lines.append("")

    prompt_lines.extend([
        "## 碎片及候选上下文",
        "",
    ])

    for idea_id, ts, sess, task, raw in ideas:
        prompt_lines.append(f"### 碎片 #{idea_id}: {raw}")
        prompt_lines.append(f"*捕获时间: {ts}*")
        prompt_lines.append("")

        candidates = idea_contexts.get(idea_id, [])
        if not candidates:
            prompt_lines.append("  ⚠ 无关联任务（孤岛候选）")
            prompt_lines.append("")
            continue

        # 按 session 分组显示
        by_session = defaultdict(list)
        for c in candidates:
            by_session[c["session_id"]].append(c)

        for sid, tasks in by_session.items():
            prompt_lines.append(f"  **会话 {sid}** 中的任务:")
            for t in tasks[:3]:  # 最多显示 3 条
                note = t["event_note"] or t["description"]
                prompt_lines.append(f"  - [{t['timestamp'][:16]}] {t['task_type']} {note}")
            if len(tasks) > 3:
                prompt_lines.append(f"  - ... 还有 {len(tasks)-3} 条任务")
        prompt_lines.append("")

    prompt_lines.extend([
        "## 输出格式",
        "请按以下结构输出你的分析：",
        "",
        "### 聚类 1: [主题名]",
        "- 归属会话: [session_id]（关联度: 高/中/低，理由: ...）",
        "- 包含碎片: #1, #3, #5",
        "- 关联元目标: ...",
        "- 分析: ...",
        "",
        "### 孤岛碎片",
        "- #7: [为什么孤立]",
        "",
        "### 行动建议",
        "1. ...",
        "2. ...",
    ])

    prompt_text = "\n".join(prompt_lines)

    return {
        "ideas": ideas,
        "sessions": sessions_summary,
        "idea_contexts": idea_contexts,
        "prompt_text": prompt_text,
    }
