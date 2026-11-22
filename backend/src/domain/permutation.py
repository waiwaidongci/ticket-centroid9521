"""按演出场次互换两套去向的置换检验。

每封留出件带两个固定去向：来信分流（含人工分诊）和多数类基线。
每个演出场次在一次组合中整体保留或整体互换两套去向，共 2^S 个组合。
对每个组合分别计算分流宏 F1 与基线宏 F1，统计差值不低于实测差值的组合占比，
用来判断分流领先多数类基线是否只是同场次来信扎堆造成的巧合。

人工分诊仍按验收口径计入真实队列的漏分；宏 F1 使用训练标签与留出标签的并集，
零分母计零，与 ``domain.analysis`` 的口径一致。
"""

from collections import Counter

from .analysis import prepare_acceptance

# 场次数量上限：2^24 ≈ 1677 万组合，避免异常批次让后台无限计算。
MAX_SESSIONS = 24
# 每个后台批次结清的组合数，进度落库后可从断点继续。
BATCH_SIZE = 256
# 与 analysis._prediction 的并列判定一致的浮点容差。
TOLERANCE = 1e-12


def macro_f1(actuals, predictions, labels):
    """两套策略共用标签并集计算宏 F1，零分母计零。"""
    support = Counter(actuals)
    routed = Counter(predictions)
    correct = Counter(
        actual for actual, predicted in zip(actuals, predictions) if predicted == actual
    )
    total = 0.0
    for label in labels:
        denominator = support[label] + routed[label]
        if denominator:
            total += 2 * correct[label] / denominator
    return total / len(labels)


def _holdout_sessions(holdout):
    missing = [ticket["ticket_id"] for ticket in tickets_with_missing_session(holdout)]
    if missing:
        raise ValueError(
            "留出件缺少演出场次，显著性核对无法启动："
            + "、".join(missing[:10])
            + ("……" if len(missing) > 10 else "")
            + "。请在导入 CSV 的「演出场次」列补齐后再运行。"
        )
    return [ticket["session"] for ticket in holdout]


def tickets_with_missing_session(holdout):
    return [ticket for ticket in holdout if not ticket.get("session")]


def build_snapshot(rows, normalized_params):
    """固定开跑时的场次与两套预测，返回可持久化、可断点续算的快照。"""
    prepared = prepare_acceptance(rows, normalized_params)
    holdout = prepared["holdout"]
    predictions = prepared["predictions"]
    labels = prepared["labels"]
    majority = prepared["majority"]

    session_names = _holdout_sessions(holdout)
    unique_sessions = sorted(set(session_names))
    if len(unique_sessions) > MAX_SESSIONS:
        raise ValueError(
            f"留出件覆盖 {len(unique_sessions)} 个演出场次，超过 {MAX_SESSIONS} 个的核对上限，"
            "请合并场次或更换批次。"
        )

    actual = [ticket["label"] for ticket in holdout]
    # 两套去向：分流策略沿用验收预测（人工分诊也是一个去向，照计漏分）；
    # 基线策略所有留出件都投向训练多数队列。
    model_destination = [row["predicted"] for row in predictions]
    baseline_destination = [majority] * len(holdout)

    session_index = {name: index for index, name in enumerate(unique_sessions)}
    ticket_sessions = [session_index[name] for name in session_names]

    observed_model = macro_f1(actual, model_destination, labels)
    observed_baseline = macro_f1(actual, baseline_destination, labels)
    observed_delta = observed_model - observed_baseline

    # 每个场次翻转时受影响的留出件下标；续算时直接复用，不重新预测。
    flips = [
        [index for index, sid in enumerate(ticket_sessions) if sid == session_id]
        for session_id in range(len(unique_sessions))
    ]
    return {
        "labels": labels,
        "majority": majority,
        "sessions": unique_sessions,
        "ticket_sessions": ticket_sessions,
        "tickets": [
            {
                "ticket_id": holdout[index]["ticket_id"],
                "session": session_names[index],
                "actual": actual[index],
                "model_destination": model_destination[index],
                "baseline_destination": baseline_destination[index],
            }
            for index in range(len(holdout))
        ],
        "flips": flips,
        "actual": actual,
        "model_destination": model_destination,
        "baseline_destination": baseline_destination,
        "observed_model_f1": observed_model,
        "observed_baseline_f1": observed_baseline,
        "observed_delta": observed_delta,
        "total_combinations": 1 << len(unique_sessions),
        "params": prepared["params"],
        "threshold": prepared["threshold"],
    }


def evaluate_combination(mask, snapshot):
    """按位掩码决定各场次保留(0)或互换(1)，返回该组合的两套去向。"""
    model = list(snapshot["model_destination"])
    baseline = list(snapshot["baseline_destination"])
    flips = snapshot["flips"]
    for session_id, members in enumerate(flips):
        if mask & (1 << session_id):
            for index in members:
                model[index], baseline[index] = baseline[index], model[index]
    return model, baseline


def count_batch(snapshot, start, end):
    """结清 [start, end) 内的组合，返回命中数（差值达到实测差值的组合数）。"""
    labels = snapshot["labels"]
    actual = snapshot["actual"]
    target = snapshot["observed_delta"] - TOLERANCE
    hits = 0
    for mask in range(start, end):
        model, baseline = evaluate_combination(mask, snapshot)
        delta = macro_f1(actual, model, labels) - macro_f1(actual, baseline, labels)
        if delta >= target:
            hits += 1
    return hits


def next_batch(settled, total):
    """返回下一个未结清组合区间，全部结清时返回 None。"""
    if settled >= total:
        return None
    return settled, min(settled + BATCH_SIZE, total)


def summary(snapshot, hits, total):
    """组装页面展示口径。"""
    ratio = hits / total if total else 0.0
    return {
        "state": "ok" if ratio <= 0.05 else "warning",
        "headline": (
            f"{len(snapshot['sessions'])} 个演出场次、{len(snapshot['tickets'])} 封留出件，"
            f"共 {total} 个整场互换组合。"
        ),
        "note": (
            "每个场次整体保留或互换「分流去向/多数类基线」两套去向，人工分诊照计真实队列漏分；"
            "统计宏 F1 差值不低于实测差值的组合占比，占比越低越能排除同场次扎堆的巧合解释。"
        ),
        "metrics": [
            {"label": "达到实测差的组合数", "value": hits,
             "detail": "组合的分流宏 F1 减基线宏 F1 不低于实测差值"},
            {"label": "组合总数", "value": total,
             "detail": f"{len(snapshot['sessions'])} 个场次，每场保留或互换，共 2 的 {len(snapshot['sessions'])} 次方"},
            {"label": "占比（经验 p 值）", "value": f"{ratio:.2%}",
             "detail": "达到实测差的组合数 ÷ 组合总数"},
            {"label": "实测宏 F1 差值", "value": round(snapshot["observed_delta"], 6),
             "detail": "未互换时分流宏 F1 减多数类基线宏 F1"},
            {"label": "实测分流宏 F1", "value": round(snapshot["observed_model_f1"], 6),
             "detail": f"人工分诊计入漏分，标签并集 {len(snapshot['labels'])} 个队列等权平均"},
            {"label": "实测基线宏 F1", "value": round(snapshot["observed_baseline_f1"], 6),
             "detail": f"多数类基线全部投向{snapshot['majority']}"},
        ],
    }
