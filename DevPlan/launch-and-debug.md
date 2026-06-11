# Echo 启动 & 调试手册

> 本文是日常开发的速查表：怎么把 Echo 跑起来、信号没出现时去哪看、数据写没写进去怎么验证。
> 设计文档见 [proposal.tex](proposal.tex)，架构总览见 [../docs/hermes-architecture.html](../docs/hermes-architecture.html)。

---

## 1. 一句话原则

**永远用仓库根目录的 `./echo` 启动，不要直接敲 `hermes`。**

PATH 上的 `hermes` 是 `~/.local/bin/hermes` shim，它指向 `~/.hermes/hermes-agent/` 下的**另一份独立安装**——那份代码里没有 Echo 插件。用它启动的话：`/echo` 页面 404、thumbs 不出现、信号全部不收集，而且不报任何错。`./echo` 通过 `python -m hermes_cli.main` 从仓库根目录启动，保证跑的是本仓库的代码 + Echo 插件。

## 2. 启动方式总表

| 命令 | 启动什么 | 什么时候用 |
|---|---|---|
| `./echo chat` | CLI 聊天（终端内） | 最快验证 agent 能跑、Echo 在后台收信号 |
| `./echo tui` | 全屏 TUI | 想要好看的终端界面 |
| `./echo dash` | Web Dashboard（前台，浏览器自动开 `/echo`） | **看 Echo 的 6 个 widget**、信号流、confidence 排名 |
| `./echo dash --tui` | Dashboard + 浏览器内嵌聊天标签 | **要测 thumbs / scope A·B 交互时必须用这个**——`/chat` 标签需要 `--tui` 才可用 |
| `./echo tauri` | Tauri 桌面壳（自动拉起后台 dashboard） | 测剪贴板 / 窗口焦点信号（浏览器拿不到的两个信号源） |
| `./echo full` | 后台 dashboard + 前台 Tauri，退出时自动收尾 | 完整桌面体验 |
| `./echo verify` | 单测 + 端到端 smoke | 改完代码跑一下，~10 秒 |
| `./echo status` / `./echo stop` | 查看 / 停止后台 dashboard | 端口被占、起没起来不确定时 |

转发参数没问题：`./echo chat --resume <id>`、`./echo chat --continue` 都会透传。

### 环境变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `ECHO_DASH_HOST` / `ECHO_DASH_PORT` | `127.0.0.1` / `9119` | dashboard 地址，端口冲突时换：`ECHO_DASH_PORT=9200 ./echo dash` |
| `ECHO_PYTHON` | 自动探测 | 强制指定 Python 解释器（launcher 默认探测顺序：`ECHO_PYTHON` → `$VIRTUAL_ENV` → hermes venv → `python3`，会跳过缺 fastapi/uvicorn 的） |
| `HERMES_PYTHON` | launcher 自动导出 | TUI 的 Node 端拿它起 Python 网关。不用手动设 |
| `ECHO_EMBEDDING_PROVIDER=openai` | 不设 = 哈希 embedding | 启用神经 embedding（M5 检索 + M1 语义复现升级）。配套 `ECHO_EMBEDDING_API_KEY`（缺省回落 `OPENAI_API_KEY`）、`ECHO_EMBEDDING_MODEL`、`ECHO_EMBEDDING_BASE_URL` |
| `ECHO_DISABLE_CONFIDENCE=1` | 不设 | 消融开关：信号照收，confidence 引擎短路不动。评估实验用 |
| `HERMES_PLUGINS_DEBUG=1` | 不设 | 插件发现的详细日志打到 stderr——**插件没加载时第一个开它** |

### Echo 皮肤

`./echo` 启动会自动装上声纳青绿皮肤（ECHO banner、`∿` 提示符）。会话里 `/skin default` 看 Hermes 原版，`/skin echo` 切回。裸 `hermes` 不受影响。

## 3. UI 上各个交互什么时候出现（没出现 ≠ 坏了）

chat 底部 widget 每 5 秒轮询，按**严格优先级**渲染：

```
有待回答的 scope 问题  → ScopeQuestion（A·整体复用 / B·只借思路）
否则有 skill invocation → ThumbsBar（👍 👎，长按 ≥500ms 写理由）
否则                    → 什么都不渲染（故意的，不是 bug）
```

- **ThumbsBar 的前提是有 skill 被加载过**（Hermes 调用了 `bump_use`）。只调普通工具（browser_*、read_file…）不算 skill 加载，底部就是空的。
- **ScopeQuestion 的前提是本次创建了新 skill**（`skill_manage(action='create')` 成功）。
- Layer B 情感分类 / Layer C judge 是否真的调 LLM，取决于 `echo.aux_mode` 配置（`hermes setup echo` 可改）：`separate`（须配独立 provider）/ `shared`（用主模型，默认）/ `off`（关闭，省钱）。

## 4. 调试流程（从快到慢）

### 第 0 层：先确认插件加载了

```bash
./echo verify                 # 405 单测 + 34/34 smoke，~10 秒
```

smoke 里有一项专门验证 `_discover_dashboard_plugins()` 能找到 echo_signals。失败先看这个。

### 第 1 层：API 直接戳

dashboard 跑着时（浏览器里打开过，带 session cookie；curl 会 401 是正常的，路由存在就行）：

```js
// 浏览器 devtools console:
fetch('/api/plugins/echo_signals/status').then(r=>r.json()).then(console.log)
// → schema 版本、当前 encoder（neural/hashing）、每张表行数。表行数全 0 = 信号没写进去

fetch('/api/plugins/echo_signals/invocations/recent?limit=5').then(r=>r.json()).then(console.log)
// → 空数组 = 没有 skill 被 bump_use 过 → thumbs 不会出现，符合预期

fetch('/api/plugins/echo_signals/scope/pending').then(r=>r.json()).then(console.log)
// → 有 pending = chat 底部应显示 A/B 问题
```

### 第 2 层：直接查 SQLite

```bash
sqlite3 ~/.hermes/sessions.db
.tables echo_%
SELECT * FROM echo_skill_invocation ORDER BY started_at DESC LIMIT 5;
SELECT signal_type, COUNT(*) FROM echo_signal_event GROUP BY signal_type;
SELECT skill_id, confidence, status FROM echo_skill_confidence;
SELECT * FROM echo_skill_scope WHERE scope_level='unknown';   -- 待回答的 scope
```

### 第 3 层：看日志

| 日志 | 位置 |
|---|---|
| Hermes agent 主日志（Echo 的 `logger.debug` 都在这） | `~/.hermes/logs/agent.log`，或 `hermes logs` |
| 后台 dashboard（`./echo tauri` / `full` 拉起的） | `$TMPDIR/echo-dashboard.log` |
| 插件发现过程 | `HERMES_PLUGINS_DEBUG=1 ./echo chat` 直接打 stderr |

Echo 所有内部异常都吞掉只打 DEBUG（设计如此——Echo 的 bug 不许影响 Hermes），所以**信号悄悄丢失时一定要看 agent.log 的 DEBUG 级**。

### 第 4 层：评估 harness 复现

不想手动聊天造数据时，用模拟器灌一遍：

```bash
python3 -c "
from scripts.eval.harness import Harness, build_default_scenarios
h = Harness(out_path='/tmp/run.jsonl')
for s in build_default_scenarios(): h.add_scenario(s)
h.run(); h.dump(); h.cleanup()
print('done -> /tmp/run.jsonl')"

python3 -m scripts.eval.metrics.m1 /tmp/run.jsonl    # M1 提名精度
python3 -m scripts.eval.metrics.m3 /tmp/run.jsonl    # M3 漂移 P/R
python3 -m scripts.eval.metrics.m4 /tmp/run.jsonl    # M4 校准 ρ
python3 -m scripts.eval.metrics.m5                   # M5 检索 uplift（自含）
python3 -m scripts.eval.sweep --knobs n_warm --out /tmp/sweep.jsonl   # 超参扫描（子集）
```

## 5. 测试命令速查

```bash
# Echo 全量（~5 秒）
python3 -m pytest tests/plugins/echo_signals/ -o addopts="" -q

# 单个文件 / 单个测试
python3 -m pytest tests/plugins/echo_signals/test_confidence.py -o addopts="" -q
python3 -m pytest tests/plugins/echo_signals/test_confidence.py::TestApplyRule -o addopts="" -q

# 评估 harness 的测试
python3 -m pytest tests/scripts/eval/ -o addopts="" -q

# Hermes 回归保险（确认没碰坏上游）
python3 -m pytest tests/providers/test_plugin_discovery.py tests/hermes_cli/test_plugins_cmd.py -o addopts="" -q

# 端到端 smoke（真实 Hermes 运行时对象）
python3 scripts/verify_echo.py
```

`-o addopts=""` 是必须的——pyproject 默认带 `-n auto --timeout=30`，当前环境没装 pytest-xdist。

## 6. 常见坑对照表

| 症状 | 原因 | 解法 |
|---|---|---|
| `/echo` 页空白、跳回 sessions | 用了 PATH 上的 `hermes` shim（老安装，无 Echo） | 用 `./echo dash` |
| `No module named 'openai'` | ui-tui 把 anaconda 的 `$VIRTUAL_ENV` 当 Python | `./echo` 已导出 `HERMES_PYTHON` 修掉；别绕过 launcher |
| chat 底部永远没 thumbs | 本轮没有 skill 被加载（只调了普通工具） | 让 agent 用一个 skill，或创建 skill 触发 scope 问题 |
| `HTTP 402: Insufficient account balance` | LLM provider 余额耗尽（与 Echo 无关） | 充值或 `hermes model` 换 provider；Layer B/C 失败会静默跳过不影响信号 |
| 每轮聊天都偷偷调一次小 LLM | `echo.aux_mode` 默认 `shared`，Layer B 每轮分类 | `hermes setup echo` 选 off / separate |
| 端口 9119 被占 | 上次 dashboard 没退干净 | `./echo stop`；不行就 `lsof -i :9119` 找 PID kill |
| 测试报 `-n auto` 相关错误 | 环境没装 pytest-xdist | 加 `-o addopts=""` |
| drift 永远不触发 | 该 skill 同名调用 < 20 次（N_WARM 冷启动），或基线方差为 0 | 正常设计；造数据时让正常调用有轻微波动 |
| Tauri 起不来 | 没有 Rust 工具链 / node_modules | `rustup` 装工具链；launcher 会自动 `npm install` |

## 7. 与上游同步后的体检

```bash
git fetch upstream
git merge upstream/<tag>
./echo verify                                  # 全绿才算同步成功
git diff upstream/main --stat | grep -v -E "plugins/echo_signals|DevPlan|tests/plugins/echo_signals|tests/scripts|docs/hermes-architecture|tauri-shell|scripts/eval|scripts/verify_echo|echo$|.gitignore|LICENSE|CLAUDE.md"
# ↑ 输出应该只剩 hermes_cli/setup.py（Echo 的 wizard 挂载点）和 test_setup_reconfigure.py
```
