# 🌱 种子演化追踪系统 — 需求改动文档 v2.0

> 改动日期：2026-05-28
> 涉及模块：Pom seeds 种子库、Hermes 记忆系统、模型调度
> 改动范围：1 个数据库表、1 个脚本、1 个配置文件
> 路径：/Volumes/13384923891/hermes-agent/context-system/PRD_Seeds_Evolution_v2.0.md

---

## 〇、需求来源

用户 liuxing 提出了一个观察：**想法会随时间变化，但当前系统只记录"现在是什么"，看不到"从什么时候开始变的"。**

举例：一颗种子从"dormant（休眠）"变成"sprouting（催熟）"，用户想知道是哪天变的、为什么变、中间经历了什么。

这个需求背后的真实场景是：**产品灵感、深层想法是不断成长和进化的**，如果记忆系统只存一个最终快照，就无法捕捉思想的演变轨迹。

---

## 一、改动概览

这次改动建筑是三层：

```
┌────────────────────────────────────────────────┐
│ 第三层：seeds 演化追踪（核心新建）              │
│   seed_evolution 表 + xw-seed v2 脚本重写       │
├────────────────────────────────────────────────┤
│ 第二层：模型自动切换（节省消耗）                │
│   config.yaml 默认模型 pro → flash              │
├────────────────────────────────────────────────┤
│ 第一层：Memory 扩容（地基加大）                 │
│   memory_char_limit 2200 → 8000               │
└────────────────────────────────────────────────┘
```

| 层次 | 改了什么 | 目的 |
|------|---------|------|
| 第三层 | seeds 加演化日志 | 追踪想法的成长轨迹 |
| 第二层 | 默认模型切 flash | 降低日常使用 token 消耗 |
| 第一层 | Memory 扩容 3.6 倍 | 未来存储更多持久化记忆 |

---

## 二、数据库改动

### 2.1 新建表：seed_evolution

**表名：** `seed_evolution`（种子演化日志）
**位置：** `/Volumes/13384923891/hermes-agent/context-system/analytics.db`

**字段：**

| 字段 | 类型 | 说明 | 举例 |
|------|------|------|------|
| id | INTEGER | 自增主键 | 1 |
| seed_id | INTEGER | 关联的种子编号 | 5 |
| timestamp | TEXT | 变更时间 | `2026-05-28 11:59:58` |
| field_changed | TEXT | 哪个字段变了 | `status`、`content`、`domain` |
| old_value | TEXT | 旧值 | `dormant` |
| new_value | TEXT | 新值 | `sprouting` |
| session_id | TEXT | 在哪个会话中变更 | `20260524_104315_bac432` |

**与 seeds 表的关系：**

```
seeds                       seed_evolution
┌─────────────┐            ┌──────────────────────────────┐
│ id=5        │            │ seed_id=5, status: dormant   │
│ status=     │  一对多    │          → sprouting          │
│   blooming  │◄──────────│ seed_id=5, domain: 产品设计   │
│ content=... │            │          → +认知工具          │
│ domain=...  │            │ seed_id=5, status: sprouting │
│ tags=...    │            │          → blooming           │
└─────────────┘            └──────────────────────────────┘
  当前状态                    完整变更历史（每次修改留一行）
```

**为什么这样设计：**

- seeds 表只管"当前是什么"，永远不变更结构
- seed_evolution 只管"什么时候变的"，只增不删
- 两个表通过 seed_id 关联，查历史不影响当前状态查询性能
- 每条 evolution 记录都带时间戳，天然形成时间线

---

## 三、脚本改动：xw-seed v2

### 3.1 改动文件

**文件：** `/Volumes/13384923891/hermes-agent/scripts/xw-seed`
**改动量：** 102 行 → 230 行（新增 128 行）

### 3.2 新增命令

| 命令 | 格式 | 做什么 | 自动记录到 evolution |
|------|------|--------|---------------------|
| `sprout` | `xw seed sprout <id> [备注]` | 种子状态 dormant→sprouting | ✅ status 字段变更 |
| `bloom` | `xw seed bloom <id> [备注]` | 种子状态 sprouting→blooming | ✅ status 字段变更 |
| `update` | `xw seed update <id> --content X --domain X --tags X` | 修改种子的内容/领域/标签 | ✅ 每个字段单独记录 |
| `history` | `xw seed history <id>` | 查看种子的完整演化时间线 | —（只读查询） |

### 3.3 已有命令的增强

**`add` 命令：** 创建种子时，自动写入多行 evolution 记录：
- 记录内容（`content`）的初始值
- 记录领域（`domain`）的初始值
- 记录标签（`tags`）的初始值
- 记录初始状态（`status`）为 dormant

这样种子从一出生就有演化日志，不需要事后补。

**`show` 命令：** 增加显示两项信息：
- 该种子经历了多少次演化（如 "演化 6 次"）
- 最近一次演化是什么时候、改了哪个字段

**`list` 命令：** 每颗种子后面显示演化次数标记（如 `🔄6`）。

### 3.4 核心函数：_log_evolution

所有状态变更都通过同一个函数写入 evolution 表：

```python
def _log_evolution(conn, seed_id, field, old_val, new_val):
    conn.execute(
        "INSERT INTO seed_evolution (seed_id, timestamp, field_changed, old_value, new_value, session_id) VALUES (?,?,?,?,?,?)",
        (seed_id, time.strftime("%Y-%m-%d %H:%M:%S"), field, str(old_val), str(new_val), SESSION)
    )
```

**设计原则：**
- 任何字段变化都自动调用，不需要用户记住写日志
- 时间戳精确到秒，记录在哪个会话中操作
- 旧值和新值同时保存，能对比"从什么变成了什么"

---

## 四、Hermes 配套改动

### 4.1 默认模型切换

**文件：** `~/.hermes/config.yaml`

**改动：** 第 2 行

```yaml
# 之前
model:
  default: deepseek-v4-pro

# 之后
model:
  default: deepseek-v4-flash
```

**效果：** 新开的 Hermes 会话自动使用 flash 模型（更便宜、更快）。遇到编程、复杂架构分解、多工具排错等场景时，用户手动输入 `/model deepseek-v4-pro` 切换。

**判断标准（已写入 Hermes memory）：**

| 用 flash（默认） | 切 pro（手动） |
|-----------------|---------------|
| 日常讨论、规划 | 编程、代码调试 |
| 搜索、文件操作 | 复杂架构分解 |
| 简单问答 | 多工具环境排错 |
| 灵感记录、思路梳理 | Pom 系统级审计 |

### 4.2 Memory 容量扩容

**文件：** `~/.hermes/config.yaml`

**改动：** 第 285-286 行

```yaml
# 之前
memory_char_limit: 2200
user_char_limit: 1375

# 之后
memory_char_limit: 8000
user_char_limit: 4000
```

**效果：** Hermes 持久记忆容量从 2,200 字符提升到 8,000 字符（+264%），用户画像从 1,375 提升到 4,000（+191%）。扩容在新会话启动时生效。

---

## 五、功能联动流程

下面演示一个种子从诞生到成熟的完整过程，以及每一步系统内部发生了什么。

```
步骤1: 创建种子
  用户输入:  xw seed add "AI 记忆系统需要时间维度" --domain 系统架构 --tags 记忆,演化
  数据库:    INSERT seeds (id=6, status=dormant, ...)
            INSERT seed_evolution (content 初始值)
            INSERT seed_evolution (domain 初始值)
            INSERT seed_evolution (tags 初始值)
            INSERT seed_evolution (status→dormant)

步骤2: 查看种子
  用户输入:  xw seed show 6
  查询:      seeds 表 → 当前状态
            seed_evolution 表 → 演化次数 + 最近变更
  输出:      🌱 种子 #6 [系统架构] 💤 dormant
            演化 4 次 | 最近: status→dormant

步骤3: 开始催熟
  用户输入:  xw seed sprout 6 "调研 Graphiti 时序图谱"
  数据库:    UPDATE seeds SET status='sprouting'
            INSERT seed_evolution (status: dormant→sprouting)
            INSERT seed_evolution (notes: "调研 Graphiti...")

步骤4: 修改内容
  用户输入:  xw seed update 6 --content "记忆系统必须有时间维度，否则无法追踪想法演化"
  数据库:    UPDATE seeds SET content='...'
            INSERT seed_evolution (content: 旧值→新值)

步骤5: 标记完成
  用户输入:  xw seed bloom 6 "evolution 表已上线"
  数据库:    UPDATE seeds SET status='blooming'
            INSERT seed_evolution (status: sprouting→blooming)
            INSERT seed_evolution (notes: "evolution 表已上线")

步骤6: 查看完整轨迹
  用户输入:  xw seed history 6
  输出:
    📜 种子 #6 演化时间线 🌸 blooming
      2026-05-28 12:00  🌱 status: → dormant
      2026-05-28 12:00  🌱 content: AI记忆系统...
      2026-05-28 12:05  🔀 status: dormant → sprouting
      2026-05-28 12:05  💬 notes: 调研 Graphiti...
      2026-05-28 12:10  📝 content: 旧值 → 新值
      2026-05-28 12:15  🔀 status: sprouting → blooming
      ─────────────── 6 次演化 ───────────────
```

**关键设计：用户不需要做任何额外操作。** sprout、bloom、update 三个命令内部自动调用 evolution 写入，用户只需像往常一样管理种子。

---

## 六、操作速查表

| 我想做什么 | 命令 |
|-----------|------|
| 新建一颗种子 | `xw seed add "内容" --domain 领域 --tags 标签` |
| 列出所有种子 | `xw seed list` |
| 只看催熟中的种子 | `xw seed list --status sprouting` |
| 看某个种子的详情 | `xw seed show 5` |
| 标记种子为"正在催熟" | `xw seed sprout 5 "开始动手了"` |
| 标记种子为"已整合完成" | `xw seed bloom 5 "代码已写完"` |
| 修改种子的内容 | `xw seed update 5 --content "新的想法描述"` |
| 给种子换一个领域 | `xw seed update 5 --domain "新领域名"` |
| 查看种子的完整演化轨迹 | `xw seed history 5` |
| 切换回 pro 模型 | `/model deepseek-v4-pro` |

---

## 七、与外部系统对比

改动过程中调研了 GitHub 上 30+ 个 AI 记忆系统，选出最接近需求的三个对比：

| 维度 | Pom evolution（本方案） | Graphiti（26K⭐） | memv（80⭐） |
|------|------------------------|------------------|------------|
| 演化追踪 | ✅ 每次变更自动记 | ✅ 内置 validity window | ✅ 双向时序 |
| 时间点查询 | 手写 SQL | ✅ 原生 `at_time()` | ✅ 原生 |
| 部署依赖 | 零依赖 | Neo4j + Python | PydanticAI + pgvector |
| 外挂盘可迁移 | ✅ 一个 db 文件带走 | ❌ Neo4j 绑主盘 | ❌ 服务绑主盘 |
| 当前适配度 | ★★★★★ | ★★★ | ★★ |

**选择 Pom 内置方案的理由：**

1. 当前种子库只有 5 颗，内置方案完全够用
2. 全部数据在一个 SQLite 文件中，外挂盘拔走即迁移
3. 不需要部署任何新服务
4. 未来种子量大到 SQLite 扛不住时，Graphiti 作为升级方向保留

---

## 八、改动文件清单

| 文件 | 操作 | 改动内容 |
|------|------|---------|
| `context-system/analytics.db` | 新建表 | `seed_evolution` 表 + 2 个索引 |
| `scripts/xw-seed` | 重写 | 102→230 行，新增 4 个命令 |
| `~/.hermes/config.yaml` | 改 3 行 | 默认模型 flash、memory 扩容 |
| `~/.hermes/memories/` | 更新 | 模型切换标准写入 memory |
| `context-system/PRD_Seeds_Evolution_v2.0.md` | 新建 | 本文档 |
