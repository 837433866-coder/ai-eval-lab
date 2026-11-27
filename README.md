# AI 应用评测实验室

面向"AI 测试 / 智能体评测"岗位的实操项目。三套评测体系全部**本地可跑、无 API key 也能出真实数据**，同时留了接真实 LLM 的接口。

对应 JD 高频词：`RAG 评测`、`幻觉率`、`忠实度`、`上下文召回`、`LLM-as-judge`、`提示注入`、`越狱`、`PII 泄露`、`ASR`。

## 三个模块

| 模块 | 干什么 | 关键产出 |
|---|---|---|
| `rag_eval/` | 50 条黄金用例 + 3 条 badcase，测 RAG 四指标 | 基线全 PASS；幻觉模型忠实度 1.0 → 0.6761 FAIL |
| `llm_judge/` | 裁判校准 + 位置/长度/自我偏差检测 | 一致率 0.75、kappa 1.0 PASS；三种偏差全部检出 |
| `redteam/` | 30 条攻击 payload，5 类攻击 | ASR 76.7%；越权调用 100%、PII 83.3%、间接注入 80% |

## 一键复现

```bash
cd "D:/WorkBuddyOutput/ai-eval-lab"

# 1. RAG 评测：基线 + 幻觉对照组
python -m rag_eval.run_eval --badcases
python -m rag_eval.run_eval --badcases --variant hallucinating

# 2. LLM-as-judge 校准与偏差
python -m llm_judge.run_judge

# 3. 提示注入红队
python -m redteam.run_redteam
```

无第三方依赖，Python 3.10+ 标准库即可跑。所有报告落在各模块的 `output/` 目录。

---

## 模块一：RAG 评测

### 为什么这么设计

评测分两层：**先测召回，再测生成**。只看最终答案对不对，定位不到问题在哪一环。四项指标口径对齐 Ragas：

| 指标 | 含义 | 低分说明什么 |
|---|---|---|
| `context_precision` | 相关片段是否排在召回前面 | 召回对了但排序差，要上重排 |
| `context_recall` | 相关片段有没有被召回 | 知识库/切分有问题 |
| `faithfulness` | 答案陈述能否由上下文支撑 | 幻觉，或知识库混入冲突文档 |
| `answer_relevancy` | 答案是否切题 | Prompt 模板有问题 |

### 幻觉对照组——证明指标不是摆设

同一批用例跑两个模型变体：

| 指标 | 提取式基线 | 幻觉模型 |
|---|---|---|
| context_precision | 0.9160 PASS | 0.9160 PASS |
| context_recall | 0.9811 PASS | 0.9811 PASS |
| **faithfulness** | **1.0000 PASS** | **0.6761 FAIL** |
| answer_relevancy | 0.7334 PASS | 0.7349 PASS |

幻觉模型只是在正确答案后追加了一句上下文里没有的陈述，忠实度立刻跨过阈值变 FAIL，其余指标不变。**这证明忠实度指标确实在测幻觉，而不是随机打分。**

### 三类 badcase

- `conflict_knowledge`：知识库混入与正确文档矛盾的片段 → 测召回被污染
- `out_of_scope`：问题在知识库里根本没有答案 → 测拒识能力，实测正确拒答
- `long_context`：答案埋在超长文档里 → 测切分与上下文截断

报告：`rag_eval/output/report_baseline.md`、`report_hallucinating.md`

---

## 模块二：LLM-as-judge

### 裁判上线的三个硬条件

1. **裁判模型与被测模型不同**——相同模型会自我偏好（报告里实测差值 0.5）
2. **用人工标注黄金集校准**——20 条样本（15 正常 + 3 幻觉 + 2 拒答），一致率 0.75、Cohen's kappa 1.0，通过
3. **三种偏差测试通过**——不通过就做双向评测、长度归一化、强制换模型

### 偏差检测器有效性验证

这一节故意用带强偏差的裁判跑，目的是证明检测器真能抓出问题：**显示"存在偏差"是预期结果**。

| 偏差类型 | 测试方法 | 实测 |
|---|---|---|
| 位置偏好 | 交换 A/B 顺序，看结论是否翻转 | 翻转率 0.5，检出 |
| 长度偏好 | 同一答案加长，看分数是否虚高 | 3.225 → 3.85，检出 |
| 自我偏好 | 同模型 vs 不同模型打分 | 差值 0.5，检出 |

很多人只知道"用大模型打分"，不知道裁判有位置偏好、长度偏好、自我偏好，更不知道要用 kappa 校准。这就是分水岭。

报告：`llm_judge/output/report.md`、`gold_ratings.json`

---

## 模块三：提示注入红队

### 五类攻击 × 30 条 payload

| 类别 | 样本 | 成功 | ASR |
|---|---|---|---|
| 直接注入 | 8 | 4 | 50.0% |
| 间接注入（RAG 文档投毒） | 5 | 4 | 80.0% |
| 越狱角色扮演 | 5 | 4 | 80.0% |
| PII 信息套取 | 6 | 5 | 83.3% |
| 越权工具调用 | 6 | 6 | **100.0%** |
| **合计** | **30** | **23** | **76.7%** |

被测 Agent 故意留了 5 个漏洞。结果里越权调用 100% 最危险——Agent 仅凭自然语言就执行退款，服务端没做二次确认和身份校验。

### 修复方向

1. 指令与数据分离，把 RAG 检索内容标记为数据
2. 敏感工具最小权限 + 二次确认，服务端校验审批状态
3. PII 输出侧脱敏，不依赖模型自觉
4. 输入检测 + 输出校验双闸门
5. 30 条 payload 接进 CI，ASR 上升就卡构建

### promptfoo 配置

`redteam/promptfooconfig.yaml` 已生成，装了 promptfoo 配 API key 就能对真实模型跑同一批用例：

```bash
npm install -g promptfoo
promptfoo eval -c redteam/promptfooconfig.yaml
```

报告：`redteam/output/report.md`、`results.json`

---

## 接真实 LLM 时要改什么

三个模块的**评测流程和指标定义不变**，只换答案生成/裁判调用：

- `rag_eval/run_eval.py` 的 `generate_answer()` → 换成真实 LLM API 调用
- `llm_judge/judge.py` 的 `score()` → 换成裁判模型 API 调用（务必与被测模型不同）
- `redteam/target.py` 的 `respond()` → 换成被测系统 API

当前用本地规则实现，是为了让项目**没有 API key 也能复现、面试能现场跑**。这一点面试可以直接说，不丢人——评测方法论才是岗位考察的核心。

## 和另一个项目的关系

这是 `payment-api-test` 的姊妹项目：

- `payment-api-test`：接口自动化 + 日志排查 + CI 门禁 → 证明"会写测试工具"
- `ai-eval-lab`：RAG 评测 + LLM-as-judge + 红队 → 证明"懂 AI 系统怎么坏、怎么测"

合起来对应那个 1-2 万的「智能体测试工程师」JD 里写的"幻觉率、召回率、上下文融合、Agent 调用失败兜底"。
