"""M1-M4 一致性指标计算与报告生成。

纯标准库,不打网络,不依赖第三方。
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from itertools import combinations
from typing import Callable, Iterable


THRESHOLDS: dict = {
    "m1b_primary":            {"value": 1.0,  "mode": "report_only"},
    "m1b_facet_jaccard":      {"value": 0.95, "mode": "report_only"},
    "m2_primary_consistency": {"value": 0.95, "mode": "hard"},
    "m3_kappa_objective":     {"value": 0.8,  "mode": "hard"},
    "m3_kappa_mood":          {"value": 0.55, "mode": "report_only"},
    "m4_coverage":            {"value": 0.9,  "mode": "hard"},
}


def modal_agreement_rate(labels: list[str]) -> float:
    """与众数一致的比例。"""
    if not labels:
        raise ValueError("labels must be non-empty")
    counts = Counter(labels)
    _, top_count = counts.most_common(1)[0]
    return top_count / len(labels)


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def mean_pairwise_jaccard(sets: list[list[str]]) -> float:
    """所有无序对的 Jaccard 平均。"""
    if len(sets) < 2:
        return 1.0
    pairs = list(combinations(range(len(sets)), 2))
    if not pairs:
        return 1.0
    total = 0.0
    for i, j in pairs:
        total += _jaccard(set(sets[i]), set(sets[j]))
    return total / len(pairs)


def group_consistency_rate(groups: dict[str, list[str]]) -> float:
    """成员 >=2 的组里"全组同标签"的组占比。"""
    qualified = [labels for labels in groups.values() if labels is not None and len(labels) >= 2]
    if not qualified:
        return 1.0
    all_same = sum(1 for labels in qualified if len(set(labels)) == 1)
    return all_same / len(qualified)


def cohens_kappa(a: list[str], b: list[str]) -> float:
    """标准 Cohen's κ,允许负值。"""
    if len(a) != len(b):
        raise ValueError("a and b must have the same length")
    n = len(a)
    if n == 0:
        raise ValueError("a and b must be non-empty")

    po = sum(1 for x, y in zip(a, b) if x == y) / n

    if po == 1.0:
        return 1.0

    cat_a = sorted(set(a))
    cat_b = sorted(set(b))
    categories = sorted(set(cat_a) | set(cat_b))

    counts = {c: 0 for c in categories}
    for x in a:
        counts[x] += 1
    margin_a = {c: counts[c] / n for c in categories}

    counts_b = {c: 0 for c in categories}
    for y in b:
        counts_b[y] += 1
    margin_b = {c: counts_b[c] / n for c in categories}

    pe = sum(margin_a[c] * margin_b[c] for c in categories)

    if pe == 1.0:
        return 0.0

    return (po - pe) / (1.0 - pe)


def coverage_rate(labels: list[str], other_label: str = "其他") -> float:
    """非"其他"占比。"""
    if not labels:
        return 0.0
    kept = sum(1 for x in labels if x != other_label)
    return kept / len(labels)


def build_report(values: dict[str, float | None]) -> dict:
    """根据指标值与 THRESHOLDS 生成报告。"""
    metrics = []
    hard_failures = []
    report_misses = []
    for name, val in values.items():
        if name not in THRESHOLDS:
            continue
        threshold = THRESHOLDS[name]["value"]
        mode = THRESHOLDS[name]["mode"]
        if val is None:
            passed = None
        else:
            passed = bool(val >= threshold)
        metrics.append({
            "name": name,
            "value": val,
            "threshold": threshold,
            "mode": mode,
            "passed": passed,
        })
        if passed is False:
            if mode == "hard":
                hard_failures.append(name)
            elif mode == "report_only":
                report_misses.append(name)
    return {
        "spec": "CLASSIFICATION_TAGGING_SPEC v1.0 §6",
        "metrics": metrics,
        "summary": {
            "hard_gate_failures": hard_failures,
            "report_only_misses": report_misses,
        },
    }


def run_m1b(record_ids: list[str], k: int, runner: Callable) -> dict:
    """M1-b 一致性聚合。"""
    per_record = []
    prim_vals = []
    facet_vals = []
    for rid in record_ids:
        primaries = []
        facet_sets = []
        for ki in range(k):
            res = runner(rid, ki)
            primaries.append(res["primary_category"])
            merged: list[str] = []
            for v in res["facets"].values():
                merged.extend(v)
            facet_sets.append(merged)
        pc = modal_agreement_rate(primaries)
        fj = mean_pairwise_jaccard(facet_sets)
        per_record.append({
            "id": rid,
            "primary_consistency": pc,
            "facet_jaccard": fj,
        })
        prim_vals.append(pc)
        facet_vals.append(fj)

    def _mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    return {
        "per_record": per_record,
        "primary_consistency_mean": _mean(prim_vals),
        "facet_jaccard_mean": _mean(facet_vals),
        "k": k,
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="eval_consistency",
        description="计算 M1-M4 一致性指标并产出 JSON 报告(consistency / 一致性)",
    )
    parser.add_argument("--records-dir", help="记录目录(批量评测入口)")
    parser.add_argument("--report-out", help="输出 JSON 报告路径")
    parser.add_argument("--golden-dir", default="eval/golden/samples", help="金标样本目录")
    parser.add_argument("--k", type=int, default=5, help="M1-b 重复运行次数")
    args = parser.parse_args(argv)

    # CLI 仅做最小骨架:解析参数、加载金标、构建空报告占位。
    report = build_report({
        "m1b_primary": None,
        "m1b_facet_jaccard": None,
        "m2_primary_consistency": None,
        "m3_kappa_objective": None,
        "m3_kappa_mood": None,
        "m4_coverage": None,
    })

    if args.report_out:
        with open(args.report_out, "w", encoding="utf-8") as f:
            f.write(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
