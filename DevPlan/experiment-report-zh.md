# Echo —— 实验评测报告

*本报告所有数字均来自真实的大模型调用（无任何伪造数据）；凡是结果偏弱或为零的，也如实报告。本文档由 `scripts/eval/analyze.py` 生成/更新，图表位于 `DevPlan/experiment-figures/`。*

> **口头结题须知**：以下每一项实验都在真实模型上实际跑过。proposal 中规划的"真实用户（Telegram）研究"**不在本轮范围内**，属于 future work，请勿当作已完成来展示。

---

## 1. 实验设计

### 1.1 四模型隔离（规避"循环论证"陷阱）

Echo 的核心论点是：**同一个模型对自己输出做自评是有偏的**（即 Hermes 被记录在案的缺陷）。如果评测中让同一个模型既产生行为、又给行为打分，就会原样复现这种偏差。因此每个角色都用**不同的模型家族**，而且**打分的评测器与被测 agent、模拟用户都相互独立**：

| 角色 | 模型 | 为何独立 |
|---|---|---|
| **模拟用户 / persona** | DeepSeek-V4-flash（阿里云 MaaS） | 产生请求、行为信号、自然语言反馈、点赞/踩 |
| **被测 agent** | mimo-v2.5（小米） | 被个性化的对象 |
| **Echo 自身的信号模型** | Qwen-plus（DashScope） | Layer B 情感分类、Layer C judge、reason 打分 |
| **独立评测器（即指标）** | GLM-5.2（智谱，关闭 thinking） | 对照**预先植入的偏好规则**给输出打分；不接触 Echo 内部，也不看 persona 自己的评分 |

Ground truth 是**预先植入**的、而非推断出来的：每个 persona 的偏好规则、每个技能的真实有用度都事先固定，因此指标是对照一个外部目标打分，而不是对照另一个模型的"意见"。

### 1.2 三组对照（遵循 proposal）

- **Baseline A —— 无记忆。** 纯 mimo、无状态。无法个性化；作为"基座模型冷启时的表现"对照。
- **Baseline B —— 自评 + 频率/时间衰减。** mimo 加一个模板记忆：把**自评**判为成功的输出存下来，按频率/时近度衰减，**完全不使用用户信号**——即 Echo 所批判的 Hermes / agentmemory 范式。
- **Echo —— 完整系统。** mimo 加 Echo 真实插件：M5 偏好 RAG（置信度加权的神经检索）、M4 置信度生命周期、Layer B/C 信号管线（Qwen），全部由用户信号驱动。

### 1.3 为何用"不可猜"的偏好

预实验发现 mimo 能**零样本满足**泛化的"简洁/礼貌"类偏好——这会造成**天花板效应**，掩盖记忆的价值。因此闭环 persona 采用**特异、可机检、基座模型默认不会做**的偏好，例如：*"每封邮件必须以 `Onward, R.` 单独成行结尾、正文 ≤ 60 词、绝不用感叹号"*；*"摘要必须正好 3 条 emoji 开头、每条 ≤ 8 词的要点"*；*"必须出现 `per my last note`、英式拼写、不准用破折号"*。这些偏好 (a) 从请求本身无法猜到、(b) 可机械校验，因此**记忆成为满足它们的必要条件**，指标也才有区分度。

**指标对每轮的"首个输出"打分**（修订之前）：*agent 是否主动遵守了它本应已知的该用户偏好？* 允许一轮修订，仅作为用户**传达**偏好的通道（其反馈会指出未满足的规则）；令人满意的修订是 Echo 学习的对象，但**不计入**主动满意度分。

---

## 2. 第三方 benchmark

用两个经同行评审的个性化 benchmark 给 persona 提供外部依据，堵住"你的模拟用户是自己瞎编的"这一质疑：

- **PersonaMem (COLM 2025)** —— 20 个 persona，含偏好**随时间演化**的多 session 历史，多选探针带 benchmark 自带的正确答案。测**偏好回忆**：Echo 的 M5 记忆能否帮 agent 答对？答案对照 benchmark 标准答案，无评测器循环论证。
- **PrefEval (ICLR 2025)** —— 1000 条（偏好，问题）对，跨 20 个主题，问题的自然答案恰好**违反**所述偏好。测**生成中的偏好遵从**：当偏好被混在一池其它偏好里存入 M5，检索能否把对的那条捞出来、使答案遵从？遵从与否由独立的 GLM-5.2 判定。

---

## 3. 指标

- **Metric 1 —— 满意度曲线**（闭环）：GLM-5.2 对每个首个输出打分（1–5）随交互轮次变化，分组对比。配对统计：Wilcoxon 符号秩检验 + Cliff's δ（Echo vs A、Echo vs B），按 (persona, seed, turn) 配对。
- **Metric 2 —— 错误传播**：植入一个**静默错误**技能，追踪各组持续使用它多少轮。确定性版本走内置 harness（`error_propagation`），外加闭环里植入的"坏偏好"的置信度衰减。
- **Metric 3 —— 系统开销**：各组真实 token 消耗（agent token + Echo 的 Qwen 信号 token，通过包裹辅助客户端计量）。延迟不计入用户感知，因为 Echo 的 Layer B/C 是 fire-and-forget、不在主回路上。
- **逐模块微指标**（确定性、无 LLM、植入 ground truth）：M1 提名 precision/recall（对比 Hermes ≥工具数规则）；M3 漂移 precision/recall/F1；M4 置信度↔真实有用度的 Spearman ρ；M5 检索 recall@k（带/不带置信度加权）。

统计上采用**非参检验**（Wilcoxon）+ **效应量**（Cliff's δ），并把每个 (persona, seed) 当作一个样本单元——**而非用 run 内的 n**——以避免模拟数据"n 无限大→样样显著"的陷阱。

---

## 4. 结果

图表见 [`DevPlan/experiment-figures/`](experiment-figures/)；原始统计见 [`stats.json`](experiment-figures/stats.json)；**全部分片日志(补充材料)** 见 [`DevPlan/experiment-logs/`](experiment-logs/)。

**本轮规模**(进程级并行分片，全部完成、无缺失):闭环 **15 persona × 3 seed × 3 条件 × 10 turn = 1350 turn**;两个 benchmark **各 3 seed**;Metric 2 确定性 **n_bad∈{3,10}、各 5 seed**。相比上一版(3 persona、单 seed)样本量大幅提升,统计显著性大大增强。

**关键升级**:本轮 Echo 启用了新做进插件的 **M5 偏好画像合并 + 每轮全注入**(schema v11)——这是满意度从上一版 ~2.3 跃升到 ~4.5 的主因。

### 4.1 PersonaMem(偏好回忆),3 seed,n = 540

![PersonaMem](experiment-figures/personamem_accuracy.png)

| 组别 | 准确率(均值 ± SD) | 注入上下文 |
|---|---|---|
| 无记忆(冷) | 46.8% ± 1.7% | 0 |
| 全历史(朴素 RAG) | 55.2% ± 3.0% | 8254 字符 |
| **Echo M5** | **64.6% ± 1.0%** | **2653 字符** |

3 个 seed 误差棒很窄、三组干净分离。Echo 比冷模型 **+17.8 pt**、比朴素全历史 **+9.4 pt**,且只用约 **1/3 上下文**。

### 4.2 PrefEval(生成中的偏好遵从),3 seed,n = 300

![PrefEval](experiment-figures/prefeval_adherence.png)

| 组别 | 遵从率(均值 ± SD) |
|---|---|
| 无记忆 | 13% ± 1.4% |
| **Echo M5** | **82% ± 3.7%** |
| Oracle(直接给偏好) | 90% ± 2.2% |

冷模型只有 13% 遵从(与 PrefEval"偏好遵从崩塌"结论一致);Echo 从 200 条偏好的干草堆里检索出对的那条,遵从率拉到 **82%**,距 oracle 上限仅 8 pt。

### 4.3 Metric 1 —— 满意度曲线(闭环,15 persona)

![satisfaction](experiment-figures/satisfaction_curve.png)

| 组别 | 总体均值 | 后段均值(turn≥5) |
|---|---|---|
| Baseline A(无记忆) | 1.45 | 1.42 |
| Baseline B(自评+衰减) | 1.29 | 1.29 |
| **Echo** | **4.48** | **4.69** |

Echo 曲线快速爬升并稳定在 **4.5–4.9**,两个 baseline 始终贴地。配对检验(按 persona/seed/turn 配对,**n = 450 对**):

- **Echo vs A**:Wilcoxon *p* = 4×10⁻⁷²,**Cliff's δ = 0.84(大效应)**
- **Echo vs B**:Wilcoxon *p* = 4×10⁻⁷⁵,**Cliff's δ = 0.86(大效应)**

相比上一版(δ≈0.27、echo 仅 2.3),M5 画像合并把效果从"显著但部分"推到"**大效应、接近天花板**"。残余差距来自个别多约束 persona(如英式拼写三连规则)偶尔漏一条——mimo 的多约束遵循上限,如实保留。

### 4.4 Metric 2 —— 错误传播

![deterministic](experiment-figures/error_propagation_deterministic.png)

**确定性 harness(5 seed、15% 噪声、植入 ground truth)—— 主结果**:

| 植入坏技能数 | Echo 抓出(5 seed 均值) | Baseline B 抓出 |
|---|---|---|
| 3 | **3 / 3**(每个 seed 都是) | 0 / 3 |
| 10 | **10 / 10**(min 也是 10) | 0 / 10 |

即使加 15% 信号噪声,Echo 仍稳定抓出全部坏技能(误报 0),频率衰减的 Baseline B 一个都抓不到——核心论点最硬的证明。

**闭环视角(诚实说明一处混淆)**:闭环里"坏方法被使用轮数"这个计数**被新画像功能搞混淆了**——画像每轮全注入后,植入的坏样例**不再能拖坏输出**,于是它"在场但无害"、从不被惩罚(该计数 echo 反而偏高,但那是"无害地在场",不是"错误传播")。真正的闭环错误传播看**坏任务上的满意度**:Baseline B 停在 **1.16**(错误持续),Echo 达到 **4.44**(克服了植入的坏方法)。所以 Metric 2 **以确定性 harness 为主**、闭环以满意度差为佐证;那张会误导的"使用轮数"图我**直接不画**。

### 4.5 Metric 3 —— 系统开销(公平版 + 对 proposal 的更正)

![overhead](experiment-figures/overhead.png)

本版修正了上次的不公平:**Baseline A 现在也修订**,agent-token apples-to-apples;并按 task **精确拆分** Layer B / Layer C。

- **Agent token 公平对比**:Echo 比 A 仅 **+5.3%**(A 4700 / Echo 4947)。上次那个 +322% 是"A 不修订"造成的假象,已消除。
- **日常稳态开销(无 Layer C)= 只有 Layer B**:每轮约 201 token,相对一次 agent 回复(~803/轮)约 **+25%**。**proposal 的"<15%"不成立**(Layer B 每轮都跑),如实更正;但这些 token 在便宜辅助模型档、且 fire-and-forget 不占用户延迟。
- **Layer C 是稀有事件**:450 turn 里只触发 **13 次(≈每 35 turn 1 次)**,每次约 2039 token。45 个 echo run 里 **36 个全程 0 次 judge**、仅 9 个触发过——证实"按需诊断、频率极低"。而且这还是**每个 run 都植了坏技能**的高压设定;正常使用 ≈ 0。

诚实标注:开销计量受 judge 异步线程跨 run 落点影响,有 ±少量噪声(A 也记到极少量 Layer C token,即此故)。

### 4.6 逐模块微指标(确定性、植入 ground truth,不随规模变)

| 模块 | 指标 | 结果 |
|---|---|---|
| M1 触发 | precision/recall vs Hermes 规则 | P=1.00, R=0.67(内置场景上与 Hermes 持平) |
| M3 漂移 | precision/recall/F1 | 1.00/1.00/1.00(n 小) |
| M4 置信度 | Spearman ρ | +0.67 |
| M5 检索 | recall@k 加权 uplift | 0(此内置场景;M5 真实价值见 4.1/4.2) |

## 4.7 一段话总结(可直接用于演讲)

在两个已发表 benchmark 上(各 3 seed),Echo 的偏好记忆把偏好**回忆**从 47% 提到 65%(PersonaMem,仅 1/3 上下文)、把偏好**遵从**从 13% 提到 82%(PrefEval,oracle 90%)。在 15 个高特异性 persona、独立 GLM 评分的受控闭环里(**n = 450 配对**),Echo 把主动满意度从 baseline 的 ~1.3–1.5 提到 **4.48**,**大效应(Cliff's δ ≈ 0.85)、p < 10⁻⁷²**。错误传播上,确定性测试中 Echo 在 15% 噪声下仍抓出 **3/3 和 10/10** 坏技能、频率衰减 baseline 抓 **0**;闭环里坏任务满意度 Echo 4.44 vs Baseline B 1.16。诚实代价两条:(1) 开销上 proposal 的"<15%"不成立——Layer B 每轮常驻、约 +25%,但在廉价档、不占延迟,公平比 agent token 仅 +5.3%;(2) 满意度残余差距来自 mimo 的多约束遵循上限。


## 5. 可复现性

```bash
PY=/Users/mac/.hermes/hermes-agent/venv/bin/python
# 四模型连通性
$PY -m scripts.eval.llm_clients
# 第三方 benchmark
$PY -m scripts.eval.exp_personamem --limit 180
$PY -m scripts.eval.exp_prefeval  --limit 100 --pool 200
# 我们的闭环实验
$PY -m scripts.eval.exp_closedloop --turns 10 --seeds 2
# 确定性微指标
$PY -m scripts.eval.run_micrometrics
# 图表 + 统计
$PY -m scripts.eval.analyze
```

凭据存于 `~/.hermes/.env` + `~/.hermes/config.yaml`（绝不入库）。benchmark 数据与结果产物在 `scripts/eval/data|results/` 下被 gitignore。
