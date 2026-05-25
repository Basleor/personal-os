# 个人操作系统（Personal OS）— 灵感便签 CtxIdea 项目说明

> **CLAUDE.md** — 你在此目录中工作时，系统会自动加载此文件。
> 它是整个 Personal OS 的入口上下文。请先理解这里的内容再动手。

---

## 项目定位

你现在位于 `/Volumes/13384923891/hermes-agent/` —— 这是一个**外挂硬盘**上的「个人操作系统（Personal OS）」。

## 五层架构（你此刻的位置）

```
05_数字代理层 [种子期]   — 未来
04_语言镜像层 [种子期]   — 未来
03_交互渲染层 [种子期]   — 未来
02_灵感白板层 [v2.1]    — ← 你的任务在这一层
01_系统内核层 [v1.0]    — 已有基础
```

## 你的任务

**构建 CtxIdea.app** —— 第 02 层（灵感白板层）的 macOS 原生图形界面。
完整的产品规格书在这里：`context-system/PRD_灵感便签_CtxIdea_v1.0.md`
请首先读完那份文件，再开始编码。

## 关键约束

1. **所有文件必须放在 `/Volumes/13384923891/hermes-agent/` 路径内**（外挂盘上）
2. **灵感数据的写入，必须通过 `context-system/core/whiteboard.py` 里的 `capture_idea()` 函数**，不要直接操作 SQLite 数据库
3. **不要修改 `context-system/Personal_OS_Manifest.md`**（那是系统宪法）
4. **与已有的 `scripts/ctx-idea` 命令行工具和平共存**，不要覆盖它
5. **推荐技术栈：Swift + SwiftUI**（macOS 原生应用），当然你有最终决定权

## 已有的基础设施（你不需要重建的部分）

- 数据库：`context-system/analytics.db`，里面有一张 `whiteboard_ideas` 表
- 核心模块：`context-system/core/whiteboard.py`（提供灵感捕获、读取、缝合等函数）
- 配置文件：`context-system/popup_config.json`（JSON 格式，存快捷键和透明度等设置）
- 应用输出目录：`applications/CtxIdea.app/`（打包好的 .app 放这里）
- 源码目录（可选）：`applications/CtxIdea/`

## 产品设计原则

- 零延迟捕获（灵感写入 < 50 毫秒，不触发任何网络请求）
- 全局热键唤起（默认 ⌃I），在系统任何位置按下都能弹出
- 所有配置都内建在工具的 ⌘K 设置面板里，用户永远不需要打开系统设置
- 视觉规格：标题栏金黄色 #FFD700 / 内容区暖奶油色 #FEF9EF / 提交按钮橙色 #FF6B35
- 窗口始终置顶、半透明（默认透明度 88%）

---

*最后更新：2026-05-22 | 版本：v1.0*
