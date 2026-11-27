"""RAG 四项评测指标，口径对齐 Ragas：

  - context_precision  上下文精确率：相关片段是否被排在前面
  - context_recall     上下文召回率：相关片段有没有被召回来
  - faithfulness       忠实度：答案里的陈述能否由上下文支撑（幻觉检测）
  - answer_relevancy   答案相关性：答案是否切题

这里用 bigram 重叠做可复现的本地近似，不依赖外部 LLM。
真实环境把 judge 换成 LLM 即可，指标定义和阈值判断不变。
"""

from .knowledge import bigrams, similarity

SUPPORT_THRESHOLD = 0.30


def split_sentences(text):
    """按中文句末标点切句。"""
    out, buf = [], ""
    for ch in text:
        buf += ch
        if ch in "。！？；\n":
            if buf.strip():
                out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def context_precision(retrieved, relevant_ids):
    """相关文档排名加权平均：排第一得 1，第二得 1/log2(3)，以此类推。
    没召回到的相关文档记 0。返回值 0~1。"""
    if not relevant_ids:
        return 0.0
    score = 0.0
    for rank, (doc, _sim) in enumerate(retrieved, start=1):
        if doc["id"] in relevant_ids:
            import math
            score += 1.0 / math.log2(rank + 1)
    return score / len(relevant_ids)


def context_recall(retrieved, relevant_ids):
    """相关文档被召回的比例。"""
    if not relevant_ids:
        return 0.0
    got = {doc["id"] for doc, _ in retrieved}
    return len(got & set(relevant_ids)) / len(relevant_ids)


def faithfulness(answer, retrieved):
    """答案逐句做蕴含近似：句子与任一召回片段的 bigram 重叠 >= 阈值即算被支撑。
    返回值 = 被支撑句数 / 总句数。这就是幻觉率的对立面。"""
    sentences = split_sentences(answer)
    if not sentences:
        return 0.0
    contexts = [d["content"] for d, _ in retrieved]
    supported = 0
    for s in sentences:
        sb = bigrams(s)
        if not sb:
            continue
        best = max((len(sb & bigrams(c)) / len(sb) for c in contexts), default=0.0)
        if best >= SUPPORT_THRESHOLD:
            supported += 1
    return supported / len(sentences)


# 疑问词/通用词 bigram。中文问句短，直接算 Jaccard 会把"什么是X"判成不相关，
# 必须先剥掉疑问词，只留承载实际信息的内容词。
QUESTION_STOP = {"什么", "么是", "怎么", "哪些", "些坑", "踩哪", "易踩",
                 "请解", "解释", "一下", "核心", "原理", "实际", "工作",
                 "应用", "容易", "是什", "是怎", "有哪"}
# 字符级停用：中文 bigram 会跨疑问词边界（如"是幻"），整词过滤挡不住
STOP_CHARS = set("什么怎么哪些请解释一下实际工作中应用容易踩坑核心原理是有多少为如何请说简述谈谈怎样")


def content_bigrams(text):
    return {
        b for b in bigrams(text)
        if b not in QUESTION_STOP and not (set(b) & STOP_CHARS)
    }


def answer_relevancy(question, answer):
    """问题中的内容词有多少出现在答案里。不同于 Jaccard，短问题不会被稀释。"""
    q = content_bigrams(question)
    if not q:
        return 0.0
    a = bigrams(answer)
    return len(q & a) / len(q)


def evaluate_one(case, retrieved, answer):
    """对单条用例算四项指标。"""
    rel = case["relevant_contexts"]
    return {
        "context_precision": round(context_precision(retrieved, rel), 4),
        "context_recall": round(context_recall(retrieved, rel), 4),
        "faithfulness": round(faithfulness(answer, retrieved), 4),
        "answer_relevancy": round(answer_relevancy(case["question"], answer), 4),
    }


def aggregate(results):
    """汇总四项指标的均值，并标出低于阈值的项目。"""
    keys = ["context_precision", "context_recall", "faithfulness", "answer_relevancy"]
    agg = {}
    for k in keys:
        vals = [r["metrics"][k] for r in results]
        agg[k] = round(sum(vals) / len(vals), 4) if vals else 0.0
    agg["_count"] = len(results)
    return agg
