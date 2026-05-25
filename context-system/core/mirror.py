"""
Personal OS — Layer 04: 语言镜像层 MVP (Language Mirror)
External drive: /Volumes/13384923891/hermes-agent/

无需语音转文字。用现有对话文本（session_tasks + ideas）做表达模式分析。
触发方式：xw mirror → 生成表达画像报告 → 落盘 Whiteboard/
"""
import time
import os
from collections import Counter

from core.db import get_connection, init_db

REPORT_DIR = "/Volumes/13384923891/hermes-agent/context-system/Whiteboard"


def gather_recent_texts(sessions: int = 5) -> dict:
    """Collect recent textual data for expression analysis.
    
    Returns:
        {
            "descriptions": [...],   # session_tasks 中的任务描述
            "event_notes": [...],    # 元目标
            "ideas": [...],          # 灵感碎片
            "preferences": [...],    # 用户偏好记录
            "session_count": N,
        }
    """
    init_db()
    conn = get_connection()
    cursor = conn.cursor()

    # Recent task descriptions
    cursor.execute(
        "SELECT DISTINCT description FROM session_tasks "
        "ORDER BY id DESC LIMIT 30"
    )
    descriptions = [r[0] for r in cursor.fetchall() if r[0]]

    # Recent event notes (meta-goals)
    cursor.execute(
        "SELECT DISTINCT event_note FROM session_tasks "
        "WHERE event_note != '' ORDER BY id DESC LIMIT 20"
    )
    event_notes = [r[0] for r in cursor.fetchall()]

    # Recent ideas
    cursor.execute(
        "SELECT raw_idea, source FROM ideas ORDER BY id DESC LIMIT 30"
    )
    ideas = [{"text": r[0], "source": r[1]} for r in cursor.fetchall()]

    # User preferences
    cursor.execute(
        "SELECT domain, preference_key, preference_value, confidence "
        "FROM user_preferences ORDER BY id DESC LIMIT 20"
    )
    preferences = [
        {"domain": r[0], "key": r[1], "value": r[2], "confidence": r[3]}
        for r in cursor.fetchall()
    ]

    # Count distinct sessions
    cursor.execute("SELECT COUNT(DISTINCT session_id) FROM session_tasks")
    session_count = cursor.fetchone()[0]

    conn.close()
    return {
        "descriptions": descriptions,
        "event_notes": event_notes,
        "ideas": ideas,
        "preferences": preferences,
        "session_count": session_count,
    }


def build_mirror_prompt(data: dict) -> str:
    """Build a natural-language prompt for expression pattern analysis."""
    lines = [
        "你是 Personal OS 的语言镜像分析引擎。",
        "",
        "你的任务：从用户的自然语言表达中，发现隐藏的行为模式和偏好信号。",
        "不是评判好坏，而是客观呈现「用户是如何思考、决策、表达的」。",
        "",
        "## 分析维度",
        "",
        "### 1. 表达偏好",
        "- 用户倾向于先给结论再解释，还是先铺垫再给结论？",
        "- 用户喜欢用比喻吗？用哪些领域的比喻？",
        "- 用户提问时是开放式还是封闭式？",
        "",
        "### 2. 决策模式",
        "- 面对二选一时，用户倾向于自己判断还是让你分析？",
        "- 用户对「自动化」的态度：偏向全自动还是手动控制？",
        "- 用户在什么情况下会推翻自己之前的决定？",
        "",
        "### 3. 关注领域",
        "- 哪些关键词/主题反复出现？",
        "- 用户投入时间最多的领域是什么？",
        "- 是否有未被用户自己意识到的关注焦点？",
        "",
        "### 4. 隐藏信号",
        "- 用户说「适应不了」其实是什么意思？（回顾历史中类似的表达）",
        "- 元目标 vs 实际行为的偏差（想做A但一直在做B）",
        "- 任何值得注意的模式",
        "",
        "## 数据",
        f"已记录会话数: {data['session_count']}",
        "",
    ]

    lines.append("### 近期任务描述")
    for d in data["descriptions"][:15]:
        lines.append(f"- {d}")
    lines.append("")

    lines.append("### 元目标（为什么做这些事）")
    for e in data["event_notes"][:10]:
        lines.append(f"- {e}")
    lines.append("")

    lines.append("### 灵感碎片")
    for idea in data["ideas"][:15]:
        src = {"popup": "⌃I", "cli": "命令", "reflect": "反思"}.get(idea["source"], idea["source"])
        lines.append(f"- [{src}] {idea['text']}")
    lines.append("")

    if data["preferences"]:
        lines.append("### 已有偏好记录")
        for p in data["preferences"]:
            lines.append(f"- {p['domain']}.{p['key']} = {p['value']} (置信度: {p['confidence']})")
        lines.append("")

    lines.extend([
        "## 输出格式",
        "",
        "### 表达画像",
        "- 风格: ...",
        "- 决策倾向: ...",
        "- 关注领域: ...",
        "",
        "### 隐藏发现",
        "- ...",
        "",
        "### 建议",
        "- 基于以上分析，Hermes 在未来对话中可以注意什么",
        "",
        "> 此报告由语言镜像层 MVP 自动生成。数据来源：session_tasks + ideas + user_preferences。",
    ])

    return "\n".join(lines)


def run_mirror() -> str:
    """Run language mirror analysis. Returns path to generated report."""
    data = gather_recent_texts()
    prompt = build_mirror_prompt(data)

    date_str = time.strftime("%Y%m%d")
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_DIR, f"Mirror_{date_str}.md")

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(prompt)

    return report_path
