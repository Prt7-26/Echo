# Echo SQLite Schema 设计

> 六张新表 + 一张版本表，作为 Module 4（置信度衰减）和 Module 5（偏好库）的存储后端。
> 全部驻留在 Hermes 现有的 SQLite 数据库 `get_hermes_home() / "state.db"`
> （见 [hermes_state.py:34](../hermes_state.py#L34) 的 `DEFAULT_DB_PATH`），
> Echo 开自己的 sqlite3 connection 到该文件，操作仅限 `echo_*` 命名空间。

## 设计原则

1. **skill_id 用 string 不用 int**：存的是 SKILL.md 的 `name:` 字段——和 Hermes 自己的 [tools/skill_usage.py](../tools/skill_usage.py) 的 keying 一致（`bump_use(skill_name)`、Curator 都用它）。是个稳定标识符（文件夹重命名不会改）。
2. **时间戳用 INTEGER unix epoch（秒）**：与 Hermes 现有 `started_at`、`timestamp` 列保持一致（见 [hermes_state.py:190](../hermes_state.py#L190)）。
3. **JSON 值用 TEXT 列存 JSON 字符串**：SQLite 没有原生 JSON 类型，但有 JSON1 函数。
4. **embedding 用 BLOB**：直接存 float32 二进制（向量维度由 auxiliary embedding 模型决定，统一存 1536 或 768）。读出来在 Python 算余弦相似度——M5 偏好库容量上限几千条，不需要 vector index。
5. **所有表加 `created_at` 和 `updated_at`**，用 trigger 自动更新。
6. **migration 走 Hermes 已有的 `_MIGRATIONS` dict**（见 [hermes_state.py](../hermes_state.py)），单调递增 ID，永不改老 migration。
7. **不在 Hermes 既有表上加列**：所有 Echo 数据放新表，外键引用既有 `sessions(session_id)` 等。这样 rebase upstream 不冲突。

## 表 1: `echo_skill_confidence`

每个技能的当前置信度状态——一行一个技能。

```sql
CREATE TABLE IF NOT EXISTS echo_skill_confidence (
    skill_id        TEXT    PRIMARY KEY,           -- Hermes 的 skill_identifier
    confidence      REAL    NOT NULL DEFAULT 0.5,  -- c ∈ [0, 1]
    locked          INTEGER NOT NULL DEFAULT 0,    -- 0/1，用户手动编辑过则锁定
    n_invocations   INTEGER NOT NULL DEFAULT 0,    -- 累计被调用次数
    n_signals       INTEGER NOT NULL DEFAULT 0,    -- 累计收到的信号事件数
    status          TEXT    NOT NULL DEFAULT 'active',
                                                    -- 'active' | 'pending_review' | 'retired'
    retired_at      INTEGER,                       -- NULL if still active
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_echo_skill_confidence_status
    ON echo_skill_confidence(status, confidence ASC);
```

**生命周期状态转换**：
- `active` → `pending_review`：confidence 跌破 `c_min`（默认 0.3）时
- `pending_review` → `retired`：Layer C judge 确认下降 + confidence 跌破 `c_retire`（默认 0.1）
- `pending_review` → `active`：judge 确认无问题，恢复

## 表 2: `echo_skill_baseline`

每个技能的每个行为指标的基线分布（Layer A 用）。

```sql
CREATE TABLE IF NOT EXISTS echo_skill_baseline (
    skill_id       TEXT    NOT NULL,
    metric_name    TEXT    NOT NULL,                -- 'modification_rounds' | 'reply_latency_seconds' | ...
    mean           REAL    NOT NULL,
    variance       REAL    NOT NULL,
    n              INTEGER NOT NULL,                -- 样本数
    baseline_ready INTEGER NOT NULL DEFAULT 0,     -- 0/1: n 是否达到 N_warm 阈值（默认 20）
    last_updated   INTEGER NOT NULL,
    PRIMARY KEY (skill_id, metric_name),
    FOREIGN KEY (skill_id) REFERENCES echo_skill_confidence(skill_id) ON DELETE CASCADE
);
```

**更新策略**：Welford's online algorithm 增量更新 mean / variance，避免存所有历史样本。当 `n ≥ N_warm` 时把 `baseline_ready` 翻到 1，从此开始进入分布偏移检测模式。

**冷启动期处理**：`baseline_ready = 0` 时不参与 Layer A 衰减——只依赖 Layer B 的显式反馈和 M2 的适用范围信号。这是对 proposal Challenge 1 的具体回答。

## 表 3: `echo_skill_invocation`

每次技能被调用的一条记录——是 signal_event 的聚合上下文锚点。

```sql
CREATE TABLE IF NOT EXISTS echo_skill_invocation (
    invocation_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id       TEXT    NOT NULL,
    session_id     TEXT    NOT NULL,                -- Hermes sessions.session_id
    platform       TEXT    NOT NULL,                -- 'cli' | 'telegram' | 'discord' | ...
    started_at     INTEGER NOT NULL,
    finished_at    INTEGER,                         -- NULL = 还在进行中
    task_summary   TEXT,                            -- 可选: 主 LLM 给的一句话任务摘要
    FOREIGN KEY (skill_id) REFERENCES echo_skill_confidence(skill_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_echo_invocation_skill_time
    ON echo_skill_invocation(skill_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_echo_invocation_session
    ON echo_skill_invocation(session_id);
```

**为什么需要这张表**：滑动窗口的分布偏移检测需要"按 skill 分组取最近 N 次调用"。直接对 `signal_event` 做 GROUP BY 太慢，独立的 invocation 表让查询是 O(N) 索引扫描。

## 表 4: `echo_signal_event`

所有原始信号事件流水——这是分析的真相之源。

```sql
CREATE TABLE IF NOT EXISTS echo_signal_event (
    event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    invocation_id  INTEGER NOT NULL,
    skill_id       TEXT    NOT NULL,                -- 反规范化为加速 per-skill 查询
    layer          TEXT    NOT NULL,                -- 'A' | 'B' | 'C'
    signal_type    TEXT    NOT NULL,                -- 'modification_rounds' | 'thumbs_up'
                                                     -- | 'nl_sentiment_positive' | 'judge_verdict' ...
    value_real     REAL,                            -- 数值信号
    value_int      INTEGER,                         -- 离散信号 (e.g., thumbs ±1)
    value_text     TEXT,                            -- 文本信号 (e.g., judge 给的解释)
    metadata       TEXT,                            -- JSON 附加数据
    ts             INTEGER NOT NULL,
    FOREIGN KEY (invocation_id) REFERENCES echo_skill_invocation(invocation_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_echo_signal_skill_layer_time
    ON echo_signal_event(skill_id, layer, ts DESC);
CREATE INDEX IF NOT EXISTS idx_echo_signal_invocation
    ON echo_signal_event(invocation_id);
```

**为什么三列 value (real / int / text)**：不同信号类型的值域不同。`thumbs_up` 是 ±1 (int)，`reply_latency` 是浮点秒数 (real)，`judge_verdict` 是 "skill_quality_degraded" + 解释 (text)。三列各管一种，避免每条信号都序列化 JSON。

**为什么反规范化 skill_id**：分析查询 99% 是按 skill 聚合，避免每次都 JOIN invocation。

## 表 5: `echo_skill_scope`

M2 适用范围确认的产物——一行一个技能。

```sql
CREATE TABLE IF NOT EXISTS echo_skill_scope (
    skill_id              TEXT    PRIMARY KEY,
    scope_level           TEXT    NOT NULL DEFAULT 'unknown',
                                                    -- 'broad' (用户选 A) | 'narrow' (用户选 B) | 'unknown'
    task_type_tags        TEXT,                     -- JSON array: ["marketing_copy", "instagram"]
    exclusion_conditions  TEXT,                     -- JSON array: 由 Layer C judge 追加
    methodology_layer     TEXT,                     -- M2 选 B 时拆出来的方法论
    specifics_layer       TEXT,                     -- M2 选 B 时拆出来的具体操作
    user_confirmed_at     INTEGER,                  -- M2 二元选择题完成时间
    created_at            INTEGER NOT NULL,
    updated_at            INTEGER NOT NULL,
    FOREIGN KEY (skill_id) REFERENCES echo_skill_confidence(skill_id) ON DELETE CASCADE
);
```

**重要约束（来自 AGENTS.md prompt caching 规则）**：往 `exclusion_conditions` 追加新条目**不能**触发当前 session 的系统提示重组——会破坏 prompt cache。改动应该 deferred 到下个 session，或者引入 `--now` flag 显式立即生效。

## 表 6: `echo_preference_example` (M5)

经过用户认证的优质输出库。

```sql
CREATE TABLE IF NOT EXISTS echo_preference_example (
    example_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    task_request      TEXT    NOT NULL,             -- 用户原始请求文本
    task_embedding    BLOB    NOT NULL,             -- float32 二进制，维度由模型决定
    agent_output      TEXT    NOT NULL,             -- 用户认证的优质输出
    rating            INTEGER NOT NULL,             -- 1-5，仅 ≥4 入库
    skill_id          TEXT,                         -- 关联技能（可空）
    task_type_tag     TEXT,                         -- 与 echo_skill_scope.task_type_tags 同语义
    created_at        INTEGER NOT NULL,
    last_used_at      INTEGER,
    use_count         INTEGER NOT NULL DEFAULT 0,
    composite_score   REAL                          -- rating × time_recency × use_count，淘汰用
);

CREATE INDEX IF NOT EXISTS idx_echo_preference_skill
    ON echo_preference_example(skill_id);
CREATE INDEX IF NOT EXISTS idx_echo_preference_score
    ON echo_preference_example(composite_score ASC);
```

**M4-M5 耦合点**：检索时，对每个候选示例算 `final_score = mmr_similarity × echo_skill_confidence.confidence(example.skill_id)`。低置信度技能的关联示例会被自然降权——这就是 M5 和 M4 的明确耦合点（回应 proposal 风险 5）。

## 默认超参数（建议从 config.yaml 注入）

```yaml
echo:
  # M4 置信度更新
  initial_confidence: 0.5
  alpha_explicit_positive: 0.10  # thumbs up
  alpha_nl_positive: 0.05        # NL 情感分类正面
  beta_dist_drift: 0.15          # Layer A 分布偏移
  gamma_explicit_negative: 0.30  # thumbs down
  c_min: 0.30                    # 进入 pending_review
  c_retire: 0.10                 # 真正淘汰
  # 冷启动
  n_warm: 20                     # baseline_ready 阈值
  # 分布偏移检测
  sliding_window_size: 10        # 最近 N 次调用算分布
  drift_threshold_zscore: 2.0    # |z| 超过这个算偏移
  # M5 检索
  mmr_lambda: 0.7                # MMR 相关性 vs 多样性权衡
  top_k_examples: 3              # 注入 prompt 的示例数
  preference_capacity: 2000      # 偏好库上限
```

## Migration 落地

向 [hermes_state.py](../hermes_state.py) 的 `_MIGRATIONS` dict 追加一个新 entry（ID 比现有最大 ID 大 1）。新 migration 文件做的事：
1. `CREATE TABLE IF NOT EXISTS` 上面 6 张表
2. `CREATE INDEX IF NOT EXISTS` 索引
3. 插入 `state_meta` 一行记录 schema_version

**绝不修改任何已有 migration**——遵守 AGENTS.md "永远不改老 migration" 的硬规则。

## 表关系图

```
sessions (Hermes 既有)
    ↓ FK
echo_skill_invocation ──┬─→ echo_signal_event (一对多)
    ↓ FK              │
echo_skill_confidence ─┼─→ echo_skill_baseline (一对多, by metric_name)
    ↓ FK              └─→ echo_skill_scope (一对一)
echo_preference_example (松耦合: 通过 skill_id 引用 confidence)
```

## 开发顺序建议

依赖最少 → 依赖最多：
1. `echo_skill_confidence`（独立表，主键即可）
2. `echo_skill_invocation` + `echo_signal_event`（信号采集核心 pair，必须一起做）
3. `echo_skill_baseline`（依赖足够的 invocation 数据才能填）
4. `echo_skill_scope`（M2 完成后才会有数据）
5. `echo_preference_example`（M5，可延后）

前 3 张表是 M3 + M4 的最小可工作集——做完就能跑信号采集和置信度衰减的端到端 demo。
