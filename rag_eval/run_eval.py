#!/usr/bin/env python3
"""RAG 评测主流程：召回 → 生成答案 → 四指标打分 → 出报告。

用法：
    python -m rag_eval.run_eval                 # 50 条常规用例
    python -m rag_eval.run_eval --badcases      # 加上 3 条 badcase
    python -m rag_eval.run_eval --top-k 3

无外部 API key 也能跑：答案用提取式生成（从召回片段抽句），
换成真实 LLM 只需替换 generate_answer 函数，评测流程不变。
"""
import argparse
import json
from pathlib import Path

from .knowledge import DOCS, CONFLICT_DOC, LONG_DOC, retrieve, similarity
from .goldset import build_goldset, build_badcases
from .metrics import evaluate_one, aggregate, split_sentences

OUT_DIR = Path(__file__).resolve().parent / "output"
THRESHOLD = 0.70
REJECT_THRESHOLD = 0.10

# 幻觉模型对照组：在正确答案后追加上下文里根本没有的陈述。
# 用来证明忠实度指标确实能区分好坏系统，不是摆设。
HALLUCINATION = "此外，该系统默认支持跨区域多活部署，故障切换完全无需人工干预，历史数据自动保留二十年。"


def generate_answer(question, retrieved, variant="extractive"):
    """提取式答案：取 top1 片段里与问题最相关的两句。
    相似度过低则拒答，模拟生产系统的拒识能力。"""
    if not retrieved or retrieved[0][1] < REJECT_THRESHOLD:
        return "知识库中没有相关信息，无法回答。"
    doc = retrieved[0][0]
    sentences = split_sentences(doc["content"])
    ranked = sorted(sentences, key=lambda s: similarity(question, s), reverse=True)
    answer = "".join(ranked[:2])
    if variant == "hallucinating":
        answer += HALLUCINATION
    return answer


def run(cases, docs, top_k, variant="extractive"):
    results = []
    for case in cases:
        retrieved = retrieve(case["question"], docs, top_k=top_k)
        answer = generate_answer(case["question"], retrieved, variant)
        metrics = evaluate_one(case, retrieved, answer)
        results.append({
            "id": case["id"],
            "question": case["question"],
            "type": case["type"],
            "expected_contexts": case["relevant_contexts"],
            "retrieved_contexts": [d["id"] for d, _ in retrieved],
            "answer": answer,
            "metrics": metrics,
        })
    return results


def write_report(results, agg, with_badcases, variant="extractive"):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": agg,
        "threshold": THRESHOLD,
        "results": results,
    }
    (OUT_DIR / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# RAG 评测报告",
        "",
        f"被测模型变体：**{variant}**　用例数：**{agg['_count']}**　（含 badcase：{'是' if with_badcases else '否'}）",
        "",
        "## 指标汇总（口径对齐 Ragas）",
        "",
        "| 指标 | 得分 | 阈值 | 结论 | 说明 |",
        "|---|---|---|---|---|",
    ]
    desc = {
        "context_precision": "相关片段是否排在召回前面",
        "context_recall": "相关片段有没有被召回",
        "faithfulness": "答案陈述能否由上下文支撑（幻觉反向指标）",
        "answer_relevancy": "答案是否切题",
    }
    for k in ["context_precision", "context_recall", "faithfulness", "answer_relevancy"]:
        v = agg[k]
        ok = "PASS" if v >= THRESHOLD else "FAIL"
        lines.append(f"| {k} | {v:.4f} | ≥ {THRESHOLD} | {ok} | {desc[k]} |")

    lines += ["", "## 失败用例 TOP10（按四项均值升序）", ""]
    ranked = sorted(results, key=lambda r: sum(r["metrics"].values()) / 4)
    for r in ranked[:10]:
        m = r["metrics"]
        avg = sum(m.values()) / 4
        lines.append(
            f"- `{r['id']}` [{r['type']}] 均值 {avg:.3f}　"
            f"召回={r['retrieved_contexts']} 期望={r['expected_contexts']}"
        )
        lines.append(f"  - 问题：{r['question']}")
        lines.append(f"  - 答案：{r['answer'][:110]}")
        lines.append(
            f"  - 精确率 {m['context_precision']} / 召回率 {m['context_recall']} / "
            f"忠实度 {m['faithfulness']} / 相关性 {m['answer_relevancy']}"
        )

    if with_badcases:
        lines += ["", "## Badcase 归因", ""]
        for r in results:
            if r["type"] == "normal":
                continue
            lines.append(f"### {r['id']} · {r['type']}")
            lines.append(f"- 召回：{r['retrieved_contexts']}，期望：{r['expected_contexts']}")
            lines.append(f"- 答案：{r['answer'][:160]}")
            lines.append(f"- 指标：{r['metrics']}")
            lines.append("")

    lines += [
        "## 怎么归因",
        "",
        "1. **召回率 FAIL、精确率 FAIL** → 知识库或切分有问题，先查文档有没有入库、块是不是切碎了。",
        "2. **召回率 PASS、精确率 FAIL** → 召回了但排序差，上重排模型。",
        "3. **忠实度 FAIL** → 答案里有上下文不支持的内容，即幻觉；也可能是知识库混入冲突文档。",
        "4. **答案相关性 FAIL** → 召回对了但答非所问，查 Prompt 模板。",
        "",
        "> 关键原则：**先分召回和生成两层，再看最终答案**。只看最终答案对不对，定位不到问题在哪一环。",
        "",
    ]
    (OUT_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已生成：{OUT_DIR / 'report.md'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--badcases", action="store_true", help="注入冲突知识/超长文本/范围外问题")
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--variant", choices=["extractive", "hallucinating"], default="extractive",
                    help="extractive=提取式基线；hallucinating=故意加无依据内容，验证指标能测出幻觉")
    args = ap.parse_args()

    cases = build_goldset()
    docs = list(DOCS)
    if args.badcases:
        cases += build_badcases()
        docs += [CONFLICT_DOC, LONG_DOC]

    results = run(cases, docs, args.top_k, args.variant)
    agg = aggregate(results)
    write_report(results, agg, args.badcases, args.variant)

    print(f"评测完成：{agg['_count']} 条用例　变体={args.variant}")
    for k in ["context_precision", "context_recall", "faithfulness", "answer_relevancy"]:
        flag = "PASS" if agg[k] >= THRESHOLD else "FAIL"
        print(f"  {k:<20} {agg[k]:.4f}  {flag}")


if __name__ == "__main__":
    main()
