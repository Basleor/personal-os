#!/usr/bin/env python3
"""
双脑协作桥梁 — 加权上下文管理系统 (Bridge v1.0)

设计原则：
  - 越底层/越重要的任务 → 权重越大、衰减越慢
  - 系统级配置变更 → 永久保留
  - 普通任务 → 随时间衰减，过期自动归档
  - 双方（Hermes + 小龙虾）共享此文件了解彼此状态

使用方式：
  python3 bridge.py log "任务描述" --level high --source hermes
  python3 bridge.py log "配置变更" --level system --source openclaw
  python3 bridge.py status            # 查看当前上下文摘要
  python3 bridge.py prune             # 手动触发过期清理
"""

import sqlite3
import json
import time
import math
import os
import sys
from datetime import datetime, timezone, timedelta

BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BRIDGE_DIR, "logs", "bridge.db")
CONTEXT_MD = os.path.join(BRIDGE_DIR, "shared_context.md")
ARCHIVE_DIR = os.path.join(BRIDGE_DIR, "archive")

# ── 重要性权重基础分 ──
IMPORTANCE_WEIGHTS = {
    "system":   100,   # 系统级配置/架构变更 — 永不衰减
    "critical": 80,    # 关键任务/安全事件
    "high":     50,    # 重要任务/主要功能
    "medium":   25,    # 一般任务
    "low":      10,    # 琐碎操作
}

# 衰减半衰期（天）— 每过一个半衰期，权重减半
HALF_LIFE_DAYS = {
    "system":   None,  # 永不衰减
    "critical": 90,    # 90 天
    "high":     30,    # 30 天
    "medium":   14,    # 14 天
    "low":      7,     # 7 天
}

# 上下文文档大小上限（字符数），超出则触发裁剪
MAX_CONTEXT_CHARS = 8000
# 裁剪时保留的最小条目数
MIN_ENTRIES = 20


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            source TEXT NOT NULL,       -- 'hermes' | 'openclaw'
            level TEXT NOT NULL,        -- system|critical|high|medium|low
            message TEXT NOT NULL,
            tags TEXT DEFAULT '',       -- 逗号分隔标签
            archived INTEGER DEFAULT 0  -- 0=活跃, 1=已归档
        )
    """)
    # ── Task delegation protocol (v1 — designed for iteration) ──
    # payload JSON 字段是未来扩展的入口：工作流步骤、超时策略、回调钩子等
    # 不在 v1 锁定这些，等看到更好的方案后自然吸收
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            from_agent TEXT NOT NULL,    -- 委托方: 'hermes' | 'openclaw'
            to_agent TEXT NOT NULL,      -- 执行方
            title TEXT NOT NULL,         -- 任务简述
            description TEXT DEFAULT '', -- 详细说明
            status TEXT DEFAULT 'pending', -- pending|taken|done|failed
            result TEXT DEFAULT '',      -- 执行结果
            priority TEXT DEFAULT 'normal', -- normal|high|low
            payload TEXT DEFAULT '{}'    -- JSON: 未来扩展字段（工作流/超时/回调等）
        )
    """)
    conn.commit()
    return conn


def add_entry(conn, source, level, message, tags=""):
    if level not in IMPORTANCE_WEIGHTS:
        print(f"错误: 无效的重要性级别 '{level}'。可选: {list(IMPORTANCE_WEIGHTS.keys())}")
        return
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO entries (timestamp, source, level, message, tags) VALUES (?, ?, ?, ?, ?)",
        (ts, source, level, message, tags)
    )
    conn.commit()
    print(f"✓ [{level}] {source}: {message[:60]}{'...' if len(message) > 60 else ''}")


def compute_weight(level, days_ago):
    """计算一条目的当前权重"""
    base = IMPORTANCE_WEIGHTS.get(level, 10)
    half_life = HALF_LIFE_DAYS.get(level)
    if half_life is None:
        return base  # system 级别永不衰减
    # 指数衰减: weight = base * (1/2)^(days/half_life)
    decay = 0.5 ** (days_ago / half_life)
    return round(base * decay, 2)


def generate_context_md(conn):
    """生成 shared_context.md"""
    now = datetime.now(timezone.utc)
    rows = conn.execute(
        "SELECT id, timestamp, source, level, message, tags FROM entries WHERE archived=0 ORDER BY timestamp DESC"
    ).fetchall()

    entries_with_weight = []
    for row in rows:
        eid, ts, source, level, msg, tags = row
        dt = datetime.fromisoformat(ts)
        days_ago = (now - dt).total_seconds() / 86400
        weight = compute_weight(level, days_ago)
        entries_with_weight.append((eid, ts, source, level, msg, tags, weight, days_ago))

    # 按权重降序排列
    entries_with_weight.sort(key=lambda x: x[6], reverse=True)

    lines = []
    lines.append("# 双脑协作上下文 (Shared Context)")
    lines.append(f"> 自动生成于 {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"> 活跃条目: {len(entries_with_weight)}")
    lines.append("")
    lines.append("## 图例")
    lines.append("| 符号 | 含义 |")
    lines.append("|------|------|")
    lines.append("| 🏛️ | 系统级 — 永不衰减 |")
    lines.append("| 🔴 | 关键 — 90天半衰 |")
    lines.append("| 🟠 | 重要 — 30天半衰 |")
    lines.append("| 🟡 | 一般 — 14天半衰 |")
    lines.append("| ⚪ | 琐碎 — 7天半衰 |")
    lines.append("")

    # 按时间分组
    current_date = None
    total_chars = 0
    kept_lines = []

    for eid, ts, source, level, msg, tags, weight, days_ago in entries_with_weight:
        emoji = {"system": "🏛️", "critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}.get(level, "⚪")
        short_ts = ts[:16].replace("T", " ")
        line = f"- {emoji} `{source}` [{short_ts}] w={weight} — {msg}"
        if tags:
            line += f" `#{tags}`"

        # 检查是否超出长度限制
        if total_chars + len(line) > MAX_CONTEXT_CHARS and len(kept_lines) >= MIN_ENTRIES:
            break
        kept_lines.append(line)
        total_chars += len(line)

    lines.extend(kept_lines)

    if len(kept_lines) < len(entries_with_weight):
        omitted = len(entries_with_weight) - len(kept_lines)
        lines.append(f"\n> ⚠️ 省略了 {omitted} 条低权重条目以控制上下文大小。运行 `bridge.py prune` 可归档过期条目。")

    lines.append("")
    lines.append("---")
    lines.append("*此文件由 bridge.py 自动维护，双方 Agent 均可读取。*")

    content = "\n".join(lines)
    with open(CONTEXT_MD, "w", encoding="utf-8") as f:
        f.write(content)

    return len(kept_lines), len(entries_with_weight)


def prune_entries(conn, min_weight=2.0):
    """归档权重低于阈值的条目"""
    now = datetime.now(timezone.utc)
    rows = conn.execute("SELECT id, timestamp, level FROM entries WHERE archived=0").fetchall()

    archived_count = 0
    for eid, ts, level in rows:
        dt = datetime.fromisoformat(ts)
        days_ago = (now - dt).total_seconds() / 86400
        weight = compute_weight(level, days_ago)
        if weight < min_weight and level != "system":
            archive_entry(conn, eid)
            archived_count += 1

    conn.commit()
    return archived_count


def archive_entry(conn, eid):
    """将单条归档"""
    row = conn.execute("SELECT * FROM entries WHERE id=?", (eid,)).fetchone()
    if not row:
        return
    # 写入归档文件
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    archive_file = os.path.join(ARCHIVE_DIR, f"archive_{datetime.now().strftime('%Y%m')}.jsonl")
    entry = {
        "id": row[0], "timestamp": row[1], "source": row[2],
        "level": row[3], "message": row[4], "tags": row[5]
    }
    with open(archive_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    conn.execute("UPDATE entries SET archived=1 WHERE id=?", (eid,))


def show_status(conn):
    """显示当前状态摘要"""
    rows = conn.execute("SELECT level, COUNT(*) FROM entries WHERE archived=0 GROUP BY level").fetchall()
    print("\n═══ 双脑桥梁状态 ═══")
    print(f"数据库: {DB_PATH}")
    print(f"上下文文件: {CONTEXT_MD}")
    print()
    total = 0
    for level, count in rows:
        emoji = {"system": "🏛️", "critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}.get(level, "⚪")
        print(f"  {emoji} {level}: {count} 条")
        total += count
    print(f"  ─────────────")
    print(f"  总计: {total} 条活跃")
    print()

    # 最近 5 条
    recent = conn.execute(
        "SELECT timestamp, source, level, message FROM entries WHERE archived=0 ORDER BY timestamp DESC LIMIT 5"
    ).fetchall()
    if recent:
        print("最近活动:")
        for ts, source, level, msg in recent:
            print(f"  [{ts[:16]}] {source} [{level}] {msg[:70]}")
    print()


def main():
    init_db()
    conn = sqlite3.connect(DB_PATH)

    if len(sys.argv) < 2:
        show_status(conn)
        conn.close()
        return

    cmd = sys.argv[1]

    if cmd == "log":
        if len(sys.argv) < 4:
            print("用法: bridge.py log <消息> --level <级别> --source <来源> [--tags <标签>]")
            conn.close()
            return
        # 简单解析
        message_parts = []
        level = "medium"
        source = "hermes"
        tags = ""
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--level" and i + 1 < len(sys.argv):
                level = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--source" and i + 1 < len(sys.argv):
                source = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--tags" and i + 1 < len(sys.argv):
                tags = sys.argv[i + 1]
                i += 2
            else:
                message_parts.append(sys.argv[i])
                i += 1
        message = " ".join(message_parts)
        add_entry(conn, source, level, message, tags)
        generate_context_md(conn)

    elif cmd == "status":
        show_status(conn)

    elif cmd == "prune":
        archived = prune_entries(conn)
        print(f"✓ 归档了 {archived} 条过期条目")
        kept, total = generate_context_md(conn)
        print(f"✓ shared_context.md 已更新 ({kept}/{total} 条)")

    elif cmd == "rebuild":
        kept, total = generate_context_md(conn)
        print(f"✓ shared_context.md 已重建 ({kept}/{total} 条)")

    # ── Task delegation protocol v1 ──
    elif cmd == "task":
        if len(sys.argv) < 3:
            print("用法: bridge.py task {create|list|take|done|status} [参数]")
            conn.close()
            return

        sub = sys.argv[2]

        if sub == "create":
            # bridge.py task create "标题" --from hermes --to openclaw [--desc "..."] [--priority high]
            title = ""
            from_agent = "hermes"
            to_agent = "openclaw"
            desc = ""
            priority = "normal"
            i = 3
            while i < len(sys.argv):
                if sys.argv[i] == "--from" and i+1 < len(sys.argv):
                    from_agent = sys.argv[i+1]; i += 2
                elif sys.argv[i] == "--to" and i+1 < len(sys.argv):
                    to_agent = sys.argv[i+1]; i += 2
                elif sys.argv[i] == "--desc" and i+1 < len(sys.argv):
                    desc = sys.argv[i+1]; i += 2
                elif sys.argv[i] == "--priority" and i+1 < len(sys.argv):
                    priority = sys.argv[i+1]; i += 2
                else:
                    title = sys.argv[i]; i += 1

            if not title:
                print("错误: 需要任务标题")
            else:
                ts = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO tasks (created_at,updated_at,from_agent,to_agent,title,description,status,priority) VALUES (?,?,?,?,?,?,?,?)",
                    (ts, ts, from_agent, to_agent, title, desc, "pending", priority)
                )
                conn.commit()
                tid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                print(f"✓ 任务 #{tid} 已创建 [{priority}] {from_agent} → {to_agent}: {title}")

        elif sub == "list":
            agent = sys.argv[3] if len(sys.argv) > 3 else None
            if agent:
                rows = conn.execute(
                    "SELECT id,created_at,from_agent,title,status,priority FROM tasks WHERE to_agent=? AND status!='done' ORDER BY priority='high' DESC, created_at ASC",
                    (agent,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id,created_at,from_agent,to_agent,title,status,priority FROM tasks WHERE status!='done' ORDER BY created_at DESC LIMIT 20"
                ).fetchall()

            if not rows:
                print("📭 暂无待处理任务")
            else:
                print(f"📋 任务列表 ({len(rows)} 条):")
                emoji = {"pending": "⏳", "taken": "🔄", "failed": "❌"}
                for rid, ts, fa, title_or_to, status, pri in rows:
                    e = emoji.get(status, "⏳")
                    agent_info = f"{fa} → {title_or_to}" if agent is None else fa
                    print(f"  {e} #{rid} [{pri}] {agent_info}: {title_or_to if agent else title}")

        elif sub == "take":
            if len(sys.argv) < 4:
                print("用法: bridge.py task take <任务ID>")
            else:
                tid = int(sys.argv[3])
                ts = datetime.now(timezone.utc).isoformat()
                conn.execute("UPDATE tasks SET status='taken', updated_at=? WHERE id=? AND status='pending'", (ts, tid))
                conn.commit()
                print(f"✓ 任务 #{tid} 已接取")

        elif sub == "done":
            if len(sys.argv) < 4:
                print("用法: bridge.py task done <任务ID> [结果]")
            else:
                tid = int(sys.argv[3])
                result = " ".join(sys.argv[4:]) if len(sys.argv) > 4 else ""
                ts = datetime.now(timezone.utc).isoformat()
                conn.execute("UPDATE tasks SET status='done', result=?, updated_at=? WHERE id=?", (result, ts, tid))
                conn.commit()
                # 同时写入 entries 表，让双方都能在 shared_context 中看到
                row = conn.execute("SELECT title, from_agent, to_agent FROM tasks WHERE id=?", (tid,)).fetchone()
                if row:
                    add_entry(conn, row[1], "high", f"任务完成 #{tid}: {row[0]} → {result[:80]}")
                print(f"✓ 任务 #{tid} 已完成")

        elif sub == "status":
            if len(sys.argv) < 4:
                print("用法: bridge.py task status <任务ID>")
            else:
                tid = int(sys.argv[3])
                row = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
                if not row:
                    print(f"✗ 任务 #{tid} 不存在")
                else:
                    print(f"任务 #{row['id']}: {row['title']}")
                    print(f"  状态: {row['status']} | 优先级: {row['priority']}")
                    print(f"  委托: {row['from_agent']} → {row['to_agent']}")
                    print(f"  创建: {row['created_at'][:16]} | 更新: {row['updated_at'][:16]}")
                    if row['result']:
                        print(f"  结果: {row['result']}")
                    if row['description']:
                        print(f"  描述: {row['description']}")

        else:
            print(f"未知子命令: task {sub}")
            print("可用: task {create|list|take|done|status}")

    else:
        print(f"未知命令: {cmd}")
        print("可用: log, status, prune, rebuild, task")

    conn.close()


if __name__ == "__main__":
    main()
