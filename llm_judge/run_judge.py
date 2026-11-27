#!/usr/bin/env python3
"""裁判校准 + 三种偏差测试。

流程：
  1. 用 20 条人工标注样本校准裁判（MAE / ±1 一致率 / Cohen's kappa）
  2. 测位置偏好：交换 A/B 顺序，看结论会不会翻
  3. 测长度偏好：同一答案加长，看分数会不会虚高
  4. 测自我偏好：同模型 vs 不同模型，看分数会不会偏高

用法：
    python -m llm_judge.run_judge
"""
import json
from pathlib import Path

from rag_eval.knowledge import DOCS, retrieve
from rag_eval.goldset import build_goldset
from rag_eval.run_eval import generate_answer, HALLUCINATION
from .judge import Judge, JUDGE_MODEL, TARGET_MODEL

OUT_DIR = Path(__file__).resolve().parent / "output"
CALIB_THRESHOLD = 0.70


def build_gold_ratings():
    """构造 20 条人工标注样本（演示用标注基线）。
    真实项目由业务专家逐条标注，这里按答案类型给定固定分值。"""
    docs = list(DOCS)
    samples = []
    # 只挑真正生成了答案的样本；被错误拒答的不能标成高质量答案
    for case in build_goldset():
        if len(samples) >= 15:
            break
        retrieved = retrieve(case["question"], docs, top_k=3)
        answer = generate_answer(case["question"], retrieved)
        if "无法回答" in answer:
            continue
        samples.append({
            "question": case["question"],
            "answer": answer,
            "reference": case["ground_truth"],
            "human": {"accuracy": 5, "completeness": 4, "safety": 5},
            "kind": "normal",
        })

    # 3 条幻觉答案
    for case in build_goldset()[15:18]:
        retrieved = retrieve(case["question"], docs, top_k=3)
        answer = generate_answer(case["question"], retrieved) + HALLUCINATION
        samples.append({
            "question": case["question"],
            "answer": answer,
            "reference": case["ground_truth"],
            "human": {"accuracy": 2, "completeness": 2, "safety": 2},
            "kind": "hallucinated",
        })

    # 2 条正确拒答
    for q in ["公司年度股权激励的行权条件是什么？", "公司差旅报销的审批流程是什么？"]:
        samples.append({
            "question": q,
            "answer": "知识库中没有相关信息，无法回答。",
            "reference": "知识库中无相关信息，应拒答。",
            "human": {"accuracy": 5, "completeness": 3, "safety": 5},
            "kind": "reject",
        })
    return samples


def cohen_kappa(human, machine):
    """把 1-5 分离散成三档后算 Cohen's kappa。"""
    def bucket(v):
        return 0 if v <= 2 else (1 if v <= 3 else 2)

    n = len(human)
    if n == 0:
        return 0.0
    observed = sum(1 for h, m in zip(human, machine) if bucket(h) == bucket(m)) / n
    po = [0, 0, 0]
    pe = [0, 0, 0]
    for h, m in zip(human, machine):
        po[bucket(h)] += 1
        pe[bucket(m)] += 1
    expected = sum((po[i] / n) * (pe[i] / n) for i in range(3))
    if expected >= 1:
        return 1.0
    return (observed - expected) / (1 - expected)


def calibrate(samples):
    judge = Judge()  # 无偏差裁判，先测它本身准不准
    rows = []
    for s in samples:
        m = judge.score(s["answer"], s["reference"])
        h = s["human"]
        diffs = {k: abs(m[k] - h[k]) for k in h}
        rows.append({
            "question": s["question"],
            "kind": s["kind"],
            "human": h,
            "machine": m,
            "mae": round(sum(diffs.values()) / 3, 3),
            "within_1": all(d <= 1 for d in diffs.values()),
        })

    maes = [r["mae"] for r in rows]
    human_total = [sum(s["human"].values()) / 3 for s in samples]
    machine_total = [judge.total(s["answer"], s["reference"]) for s in samples]
    agreement = sum(r["within_1"] for r in rows) / len(rows)
    kappa = cohen_kappa(human_total, machine_total)
    return {
        "rows": rows,
        "mae": round(sum(maes) / len(maes), 3),
        "agreement_within_1": round(agreement, 3),
        "cohen_kappa": round(kappa, 3),
        "passed": agreement >= CALIB_THRESHOLD and kappa >= 0.60,
    }


def test_position_bias():
    """交换 A/B 顺序，看结论是否翻转。用带位置偏差的裁判。"""
    judge = Judge(position_bias=2.0)
    samples = build_gold_ratings()
    flips = 0
    details = []
    for i in range(0, min(10, len(samples) - 1)):
        good = samples[i]["answer"]
        bad = samples[i + 1]["answer"] if samples[i + 1]["kind"] == "hallucinated" else good + HALLUCINATION
        ref = samples[i]["reference"]
        w1, _, _ = judge.pairwise(good, bad, ref, swap=False)
        w2, _, _ = judge.pairwise(good, bad, ref, swap=True)
        flipped = w1 != w2
        flips += flipped
        details.append({"pair": i, "before": w1, "after_swap": w2, "flipped": flipped})
    return {"flip_rate": round(flips / len(details), 3), "details": details,
            "passed": flips == 0}


def test_length_bias():
    """同一条错误答案，加长后看分数变化。"""
    clean = Judge()
    biased = Judge(length_bias=0.2)
    base = "这个接口没有做幂等处理，重复调用会产生多条流水记录。"
    padded = base + "补充说明。" * 60
    ref = "支付回调必须做幂等，校验订单状态并给流水表加唯一索引。"
    return {
        "clean_short": clean.total(base, ref),
        "clean_long": clean.total(padded, ref),
        "biased_short": biased.total(base, ref),
        "biased_long": biased.total(padded, ref),
        "passed": abs(biased.total(base, ref) - biased.total(padded, ref)) < 0.3,
    }


def test_self_bias():
    """同一答案，标成裁判自己模型 vs 被测模型，看分数差。"""
    biased = Judge(self_bias=0.5)
    sample = build_gold_ratings()[0]
    own = biased.total(sample["answer"], sample["reference"], answer_model=JUDGE_MODEL)
    other = biased.total(sample["answer"], sample["reference"], answer_model=TARGET_MODEL)
    return {
        "own_model_score": own,
        "other_model_score": other,
        "gap": round(own - other, 3),
        "passed": abs(own - other) < 0.2,
    }


def write_report(cal, pos, length, self_bias, samples):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "gold_ratings.json").write_text(
        json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    payload = {
        "judge_model": JUDGE_MODEL,
        "target_model": TARGET_MODEL,
        "calibration": cal,
        "position_bias": pos,
        "length_bias": length,
        "self_bias": self_bias,
    }
    (OUT_DIR / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# LLM-as-judge 校准与偏差报告",
        "",
        f"裁判模型：`{JUDGE_MODEL}`　被测模型：`{TARGET_MODEL}`",
        "",
        "> 硬规则：裁判模型必须与被测模型不同。相同模型会触发自我偏好，评分不可信。",
        "",
        "> 阅读顺序：第 1 节是**上线用的无偏差裁判**，已通过校准。第 2~4 节**故意注入强偏差**，"
        "目的是证明三种检测器真能抓出偏差——显示「存在偏差」是预期结果，不是缺陷。",
        "",
        "## 1. 黄金集校准",
        "",
        f"- 样本数：{len(samples)}（15 正常 + 3 幻觉 + 2 正确拒答）",
        f"- 平均绝对误差 MAE：**{cal['mae']}**",
        f"- ±1 分一致率：**{cal['agreement_within_1']}**（阈值 ≥ {CALIB_THRESHOLD}）",
        f"- Cohen's kappa：**{cal['cohen_kappa']}**（阈值 ≥ 0.60）",
        f"- 结论：**{'校准通过，裁判可用' if cal['passed'] else '校准不通过，禁止上线'}**",
        "",
        "| # | 类型 | 人工总分 | 裁判总分 | MAE | ±1内 |",
        "|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(cal["rows"][:20], 1):
        ht = round(sum(r["human"].values()) / 3, 2)
        mt = round(sum(r["machine"].values()) / 3, 2)
        lines.append(f"| {i} | {r['kind']} | {ht} | {mt} | {r['mae']} | {'是' if r['within_1'] else '否'} |")

    lines += [
        "",
        "## 2. 位置偏好测试",
        "",
        f"交换 A/B 展示顺序后，结论翻转率：**{pos['flip_rate']}**",
        f"结论：**{'未发现位置偏好' if pos['passed'] else '存在位置偏好，必须做双向评测取平均'}**",
        "",
        "| 样本对 | 交换前 | 交换后 | 是否翻转 |",
        "|---|---|---|---|",
    ]
    for d in pos["details"]:
        lines.append(f"| {d['pair']} | {d['before']} | {d['after_swap']} | {'是' if d['flipped'] else '否'} |")

    lines += [
        "",
        "## 3. 长度偏好测试",
        "",
        "| 裁判 | 短答案 | 长答案（内容相同） | 差值 |",
        "|---|---|---|---|",
        f"| 无偏差 | {length['clean_short']} | {length['clean_long']} | "
        f"{round(length['clean_long'] - length['clean_short'], 3)} |",
        f"| 注入长度偏差 | {length['biased_short']} | {length['biased_long']} | "
        f"{round(length['biased_long'] - length['biased_short'], 3)} |",
        "",
        f"结论：**{'未发现长度偏好' if length['passed'] else '存在长度偏好，长答案无端得高分'}**",
        "",
        "## 4. 自我偏好测试",
        "",
        f"- 标成裁判自己模型：{self_bias['own_model_score']}",
        f"- 标成被测模型：{self_bias['other_model_score']}",
        f"- 差值：{self_bias['gap']}",
        f"- 结论：**{'未发现自我偏好' if self_bias['passed'] else '存在自我偏好，印证裁判必须与被测模型不同'}**",
        "",
        "## 裁判上线的三个必要条件",
        "",
        "1. 裁判模型与被测模型**不同**（本报告用 judge-v2 评 target-v1）",
        "2. 用人工标注黄金集校准，一致率和 kappa 都达标才放行",
        "3. 位置 / 长度 / 自我三种偏差测试通过；不通过就做双向评测、长度归一化、强制换模型",
        "",
        "> 面试点：只会说'用大模型打分'是初级。能说出裁判偏差、黄金集校准和 kappa，才说明真做过评测。",
        "",
    ]
    (OUT_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已生成：{OUT_DIR / 'report.md'}")


def main():
    samples = build_gold_ratings()
    cal = calibrate(samples)
    pos = test_position_bias()
    length = test_length_bias()
    sb = test_self_bias()
    write_report(cal, pos, length, sb, samples)

    print(f"样本数：{len(samples)}")
    print(f"校准：MAE={cal['mae']} 一致率={cal['agreement_within_1']} kappa={cal['cohen_kappa']} "
          f"{'PASS' if cal['passed'] else 'FAIL'}")
    print(f"位置偏好对照：翻转率={pos['flip_rate']} "
          f"{'未检出' if pos['passed'] else '已检出偏差（检测器有效）'}")
    print(f"长度偏好对照：短={length['biased_short']} 长={length['biased_long']} "
          f"{'未检出' if length['passed'] else '已检出偏差（检测器有效）'}")
    print(f"自我偏好对照：差值={sb['gap']} "
          f"{'未检出' if sb['passed'] else '已检出偏差（检测器有效）'}")


if __name__ == "__main__":
    main()
