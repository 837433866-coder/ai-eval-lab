"""生成 50 条人工标注黄金用例 + 3 条 badcase。

真实项目里黄金集来自业务专家标注；这里按知识点 × 问法模板生成，
保证可复现，同时覆盖"同一知识不同问法"这个真实评测场景。
"""

from .knowledge import DOCS

QUESTION_TEMPLATES = [
    "什么是{title}？",
    "请解释一下{title}。",
    "{title}的核心原理是什么？",
    "实际工作中怎么应用{title}？",
    "{title}容易踩哪些坑？",
]


def build_goldset():
    cases = []
    n = 0
    for doc in DOCS:
        title = doc["title"]
        # 用文档前两句作为标准答案要点
        first_two = "".join(doc["content"].split("。")[:2]) + "。"
        for tpl in QUESTION_TEMPLATES:
            n += 1
            cases.append({
                "id": f"Q{n:03d}",
                "question": tpl.format(title=title),
                "ground_truth": first_two,
                "relevant_contexts": [doc["id"]],
                "type": "normal",
            })
    return cases


def build_badcases():
    """注入三类 badcase，专门测系统在脏数据下的表现。"""
    return [
        {
            "id": "BAD-01",
            "question": "支付回调需要业务代码校验订单状态吗？",
            "ground_truth": "需要。回调可能重复推送，不校验会导致重复记账。",
            "relevant_contexts": ["C01"],
            "type": "conflict_knowledge",
            "note": "知识库里混入了与 C01 矛盾的 C99-CONFLICT 文档",
        },
        {
            "id": "BAD-02",
            "question": "公司年度股权激励的行权条件是什么？",
            "ground_truth": "知识库中无相关信息，应拒答。",
            "relevant_contexts": [],
            "type": "out_of_scope",
            "note": "知识库里根本不存在答案，正确行为是拒识而不是编造",
        },
        {
            "id": "BAD-03",
            "question": "支付系统的缓存故障切换目标是多少秒？",
            "ground_truth": "知识库中该信息在超长运维文档里，容易被截断丢失。",
            "relevant_contexts": ["C98-LONG"],
            "type": "long_context",
            "note": "答案埋在超长文档里，测试切分与上下文窗口截断",
        },
    ]
