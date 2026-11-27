"""LLM-as-judge 裁判器（本地可复现版本）。

核心约束，面试必须能讲出来：
  1. 裁判模型必须与被测模型不同（judge-v2 vs target-v1）
  2. 裁判上线前必须用人工标注的黄金集校准，一致率不达标不能用
  3. 必须专门测位置偏好、长度偏好、自我偏好三种偏差

这里用规则实现裁判逻辑，把偏差参数显式暴露出来，方便演示"偏差怎么污染评分"。
接真实 LLM 时只需把 score() 换成 API 调用，校准流程和偏差测试不变。
"""

from rag_eval.metrics import bigrams, content_bigrams

JUDGE_MODEL = "judge-v2"
TARGET_MODEL = "target-v1"

# 幻觉内容的明显标记，用于 safety 维度
UNSUPPORTED_MARKERS = ["无需人工", "二十年", "多活部署", "自动保留"]


def clamp(v, lo=1.0, hi=5.0):
    return max(lo, min(hi, v))


class Judge:
    def __init__(self, model_id=JUDGE_MODEL, position_bias=0.0,
                 length_bias=0.0, self_bias=0.0):
        self.model_id = model_id
        self.position_bias = position_bias
        self.length_bias = length_bias
        self.self_bias = self_bias

    def score(self, answer, reference, answer_model=TARGET_MODEL, position=1):
        """给单条答案打三维分（1~5）。

        position=1 表示候选排在前面。位置偏差就是无端给前一个候选加分。
        length_bias 模拟真实裁判的长度偏好：每 100 字加一点分。
        self_bias 模拟自我偏好：裁判给同模型输出加分。
        """
        ref_b = content_bigrams(reference)
        ans_b = bigrams(answer)
        coverage = len(ref_b & ans_b) / len(ref_b) if ref_b else 0.0

        accuracy = 1.0 + 4.0 * coverage

        len_ratio = len(answer) / max(len(reference), 1)
        completeness = 1.0 + 2.0 * coverage + 2.0 * min(len_ratio, 1.0)

        safety = 5.0
        if any(m in answer for m in UNSUPPORTED_MARKERS):
            # 幻觉内容不只影响安全性，准确性同样要扣分
            safety = 2.0
            accuracy = min(accuracy, 2.0)
            completeness = min(completeness, 2.0)
        if "无法回答" in answer:
            # 正确拒答：准确性给高分，但完整性只有 3（没给出实质内容）
            safety = 5.0
            accuracy = 4.0
            completeness = 3.0

        # 偏差不在这里注入：三维分会被 5 分上限截断，偏差会被吃掉。
        # 统一在 total() 里对总分做后处理，偏差幅度才看得见。

        return {
            "accuracy": round(clamp(accuracy), 3),
            "completeness": round(clamp(completeness), 3),
            "safety": round(clamp(safety), 3),
        }

    def total(self, answer, reference, answer_model=TARGET_MODEL, position=None):
        """总分 = 三维均分 + 偏差后处理。偏差不加 5 分上限，否则测不出来。"""
        s = self.score(answer, reference, answer_model=answer_model, position=position)
        base = sum(s.values()) / 3
        adjusted = base
        if position == 1:
            adjusted += self.position_bias
        adjusted += self.length_bias * (len(answer) // 100)
        if answer_model != self.model_id:
            adjusted -= self.self_bias
        return round(adjusted, 3)

    def pairwise(self, answer_a, answer_b, reference, swap=False):
        """成对比较。swap=True 时交换 A/B 的展示顺序，用于测位置偏好。"""
        if swap:
            first, second = answer_b, answer_a
        else:
            first, second = answer_a, answer_b
        s1 = self.total(first, reference, position=1)
        s2 = self.total(second, reference, position=2)
        return "A" if (s1 > s2) != swap else "B", s1, s2
