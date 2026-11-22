"""用固定留出批验收剧场来信的质心分流。"""

import re
from collections import Counter
from math import fsum, isclose, log, sqrt
from unicodedata import normalize

from .spec import MANUAL_QUEUE, QUEUES, SPEC


_TOKEN_RUNS = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+|[a-z0-9]+")
_PREDICTION_COLUMNS = (
    ("ticket_id", "留出来信编号"), ("actual", "人工归口"), ("candidate", "最接近的训练队列"),
    ("predicted", "分流去向"), ("similarity", "最高余弦分"), ("matched_terms", "命中词数"),
    ("outcome", "分流判定"), ("reason", "判定依据"),
    ("baseline", "多数类去向"),
)
_QUEUE_COLUMNS = (
    ("queue", "队列"), ("training", "训练件数"), ("support", "留出归口量"),
    ("routed", "预计接件量"), ("correct", "正确分流"), ("precision", "精确率"),
    ("recall", "召回率"), ("f1", "F1"), ("baseline_f1", "基线 F1"),
    ("top_terms", "质心代表词"), ("handling", "接件说明"),
)


def validate_rows(rows):
    """校验 CSV 来信格式和编号唯一性，返回规范化副本。"""
    if not isinstance(rows, (list, tuple)) or not rows:
        raise ValueError("来信明细为空，请按样例导入带归口和批次的记录。")
    tickets = []
    seen = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"第 {index} 条来信格式有误。")
        ticket = {}
        for field in SPEC["fields"]:
            key = field["key"]
            value = row.get(key)
            value = value.strip() if isinstance(value, str) else ""
            if not value or len(value) > field.get("max_length", 300):
                raise ValueError(f"第 {index} 条来信的「{field['label']}」缺失或长度超限。")
            if field["type"] == "select" and value not in field["options"]:
                raise ValueError(f"第 {index} 条来信的「{field['label']}」须为：{'、'.join(field['options'])}。")
            ticket[key] = value
        identifier = normalize("NFKC", ticket["ticket_id"]).casefold()
        if identifier in seen:
            raise ValueError(f"来信编号「{ticket['ticket_id']}」重复，同一编号只能出现于一个批次。")
        seen.add(identifier)
        tickets.append(ticket)
    return tickets


def _parameters(params):
    if not isinstance(params, dict):
        raise ValueError("验收参数格式有误。")
    values = {}
    for field in SPEC["params"]:
        key = field["key"]
        value = params.get(key, field["default"])
        types = (int,) if field["type"] == "int" else (int, float)
        if type(value) not in types or not field["min"] <= value <= field["max"]:
            unit = "整数" if field["type"] == "int" else "数值"
            raise ValueError(f"「{field['label']}」须为 {field['min']} 至 {field['max']} 的{unit}。")
        values[key] = value
    return values


def _counts(text):
    tokens = Counter()
    for run in _TOKEN_RUNS.findall(normalize("NFKC", text).casefold()):
        if run.isascii() or len(run) == 1:
            tokens[run] += 1
        else:
            tokens.update(run[index:index + 2] for index in range(len(run) - 1))
    return tokens


def _unit(vector):
    norm = sqrt(fsum(value * value for value in vector.values()))
    return {term: value / norm for term, value in vector.items()} if norm else {}


def _vector(counts, idf):
    return _unit({term: count * idf[term] for term, count in counts.items() if term in idf})


def _fit(training, label_counts, max_terms):
    documents = [_counts(ticket["text"]) for ticket in training]
    frequency = Counter(term for document in documents for term in document)
    if not frequency:
        raise ValueError("训练批提取不到有效词，请补录含汉字、字母或数字的来信。")
    terms = sorted(frequency, key=lambda term: (-frequency[term], term))
    idf = {
        term: 1 + log((1 + len(training)) / (1 + frequency[term]))
        for term in terms[:max_terms or None]
    }
    sums = {label: Counter() for label in label_counts}
    for ticket, document in zip(training, documents):
        sums[ticket["label"]].update(_vector(document, idf))
    centroids = {
        label: {term: weight / label_counts[label] for term, weight in weights.items()}
        for label, weights in sums.items()
    }
    return idf, centroids


def _prediction(ticket, idf, directions, threshold, majority):
    vector = _vector(_counts(ticket["text"]), idf)
    scores = {
        label: min(1.0, fsum(weight * direction.get(term, 0.0) for term, weight in vector.items()))
        for label, direction in directions.items()
    }
    best = max(scores.values())
    leaders = sorted(
        label for label, score in scores.items() if isclose(score, best, rel_tol=0, abs_tol=1e-12)
    )
    actual = ticket["label"]
    predicted = MANUAL_QUEUE
    candidate = " / ".join(leaders) if best else ""
    if not vector:
        reason = "无训练词表命中"
    elif len(leaders) > 1:
        reason = "最高余弦分并列"
    elif best < threshold and not isclose(best, threshold, rel_tol=0, abs_tol=1e-12):
        reason = "最高余弦分低于阈值"
    else:
        predicted = leaders[0]
        reason = "最高余弦分达到阈值"
    if predicted == MANUAL_QUEUE:
        outcome = "转人工"
    else:
        outcome = "命中" if predicted == actual else "错投"
    return {
        "ticket_id": ticket["ticket_id"], "actual": actual, "candidate": candidate,
        "predicted": predicted, "similarity": round(best, 6), "matched_terms": len(vector),
        "outcome": outcome, "reason": reason, "baseline": majority,
    }


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else 0.0


def _table(title, columns, rows):
    return {"title": title, "columns": [{"key": key, "label": label} for key, label in columns], "rows": rows}


def _queue_summary(labels, counts, centroids, predictions, majority):
    support = Counter(row["actual"] for row in predictions)
    routed = Counter(row["predicted"] for row in predictions)
    correct = Counter(row["actual"] for row in predictions if row["outcome"] == "命中")
    total = len(predictions)
    rows = []
    model_scores = []
    baseline_scores = []
    for label in labels:
        f1 = _ratio(2 * correct[label], support[label] + routed[label])
        baseline_f1 = _ratio(2 * support[label], support[label] + total) if label == majority else 0.0
        model_scores.append(f1)
        baseline_scores.append(baseline_f1)
        centroid = centroids.get(label, {})
        top_terms = sorted(centroid, key=lambda term: (-centroid[term], term))[:6]
        handling = QUEUES[label]
        if not centroid:
            handling += " 当前训练词表下无可用质心，请补充训练件或扩大词表。"
        rows.append({
            "queue": label, "training": counts[label], "support": support[label], "routed": routed[label],
            "correct": correct[label], "precision": round(_ratio(correct[label], routed[label]), 6),
            "recall": round(_ratio(correct[label], support[label]), 6),
            "f1": round(f1, 6), "baseline_f1": round(baseline_f1, 6),
            "top_terms": "、".join(top_terms), "handling": handling,
        })
    rows.append({
        "queue": MANUAL_QUEUE, "training": None, "support": None, "routed": routed[MANUAL_QUEUE],
        "correct": None, "precision": None, "recall": None, "f1": None, "baseline_f1": None,
        "top_terms": "", "handling": "值班客服先确认来件诉求，再转交对应业务组。",
    })
    return rows, fsum(model_scores) / len(labels), fsum(baseline_scores) / len(labels)


def analyze(rows, normalized_params):
    tickets = sorted(validate_rows(rows), key=lambda ticket: ticket["ticket_id"])
    params = _parameters(normalized_params)
    training = [ticket for ticket in tickets if ticket["split"] == "train"]
    holdout = [ticket for ticket in tickets if ticket["split"] == "test"]
    if not training or not holdout:
        raise ValueError("必须同时提供 train 训练件和 test 留出件，批次以导入标记为准。")
    counts = Counter(ticket["label"] for ticket in training)
    if len(counts) < 2:
        raise ValueError("训练批至少需要两个已标注队列，才能验收分流。")
    majority = min(counts, key=lambda label: (-counts[label], label))
    idf, centroids = _fit(training, counts, params["max_terms"])
    directions = {label: _unit(centroid) for label, centroid in centroids.items()}
    threshold = params["min_similarity"]
    predictions = [_prediction(ticket, idf, directions, threshold, majority) for ticket in holdout]
    labels = sorted(counts.keys() | {ticket["label"] for ticket in holdout})
    queues, macro_f1, baseline_f1 = _queue_summary(labels, counts, centroids, predictions, majority)

    outcomes = Counter(row["outcome"] for row in predictions)
    manual = outcomes["转人工"]
    wrong = outcomes["错投"]
    total = len(holdout)
    coverage = (total - manual) / total
    delta = macro_f1 - baseline_f1
    return {
        "state": "warning" if manual or wrong or delta <= 0 else "ok",
        "headline": f"留出 {total} 封，自动分流 {total - manual} 封（错投 {wrong} 封），人工分诊 {manual} 封。",
        "note": f"训练 {len(training)} 封，使用 {len(idf)} 个训练词，最低相似度 {threshold:g}。多数类基线全部投向{majority}。",
        "metrics": [
            {"label": "分流宏 F1", "value": round(macro_f1, 6), "detail": f"{len(labels)} 个队列等权平均，人工分诊计入漏分"},
            {"label": "多数类宏 F1", "value": round(baseline_f1, 6), "detail": f"训练批多数队列为{majority}（{counts[majority]} 封）"},
            {"label": "宏 F1 差值", "value": round(delta, 6), "detail": "分流宏 F1 减多数类宏 F1"},
            {"label": "自动分流覆盖率", "value": f"{coverage:.1%}", "detail": f"{total - manual}/{total} 封进入业务队列"},
            {"label": "人工分诊量", "value": manual, "detail": "阈值、并列和未命中词表的逐件原因见明细"},
        ],
        "tables": [
            _table("逐件分流明细", _PREDICTION_COLUMNS, predictions),
            _table("队列负载与验收指标", _QUEUE_COLUMNS, queues),
        ],
        "charts": [{
            "title": "留出来信的预计队列接件量", "kind": "bar",
            "series": [{"label": row["queue"], "value": row["routed"]} for row in queues],
        }],
        "sections": [{
            "title": "队列复核与排班",
            "text": (
                "预计接件量汇总模型去向，人工归口量汇总留出标签。错投会占用接件组的时间，也会让应接件组漏收。"
                "结合逐件依据复核错投和分诊原因，沿用固定留出批比较阈值前后的覆盖率与宏 F1。"
            ),
        }],
    }
