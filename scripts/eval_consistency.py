"""M1-M4 一致性指标计算与报告。

纯 stdlib 实现,不打网络,不依赖第三方库。
供 eval/golden/ 金标评测与 run_m1b(M1-b 聚合器)使用。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter
from itertools import combinations
from typing import Any, Callable


THRESHOLDS: dict[str, dict[str, Any]] = {
    "m1b_primary":            {"value": 1.0,  "mode": "report_only"},
    "m1b_facet_jaccard":      {"value": 0.95, "mode": "report_only"},
    "m2_primary_consistency": {"value": 0.95, "mode": "hard"},
    "m3_kappa_objective":     {"value": 0.8,  "mode": "hard"},
    "m3_kappa_mood":          {"value": 0.55, "mode": "report_only"},
    "m4_coverage":            {"value": 0.9,  "mode": "hard"},
}


def modal_agreement_rate(labels: list[str]) -> float:
    """返回与众数一致的比例。空列表 raise ValueError。"""
    if not labels:
        raise ValueError("labels must be non-empty")
    counts = Counter(labels)
    top, top_n = counts.most_common(1)[0]
    return top_n / len(labels)


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    inter = a & b
    return len(inter) / len(union)


def mean_pairwise_jaccard(sets: list[list[str]]) -> float:
    """所有无序对 Jaccard 平均;双空集的一对记 1.0;不足 2 个集合返回 1.0。"""
    norm = [set(s) for s in sets]
    if len(norm) < 2:
        return 1.0
    total = 0.0
    n = 0
    for a, b in combinations(norm, 2):
        total += _jaccard(a, b)
        n += 1
    if n == 0:
        return 1.0
    return total / n


def group_consistency_rate(groups: dict[str, list[str]]) -> float:
    """成员 ≥2 的组里"全组同标签"的组占比;单成员组忽略;无合格组返回 1.0。"""
    eligible = [labels for labels in groups.values() if len(labels) >= 2]
    if not eligible:
        return 1.0
    consistent = sum(1 for labels in eligible if len(set(labels)) == 1)
    return consistent / len(eligible)


def cohens_kappa(a: list[str], b: list[str]) -> float:
    """标准 Cohen's κ。长度不等 raise ValueError;pe==1 退化:一致返回 1.0,否则 0.0。"""
    if len(a) != len(b):
        raise ValueError("a and b must have the same length")
    n = len(a)
    if n == 0:
        return 1.0
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    ca = Counter(a)
    cb = Counter(b)
    pe = sum((ca[k] / n) * (cb[k] / n) for k in set(ca) | set(cb))
    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def coverage_rate(labels: list[str], other_label: str = "其他") -> float:
    """非"其他"占比;空列表返回 0.0。"""
    if not labels:
        return 0.0
    non_other = sum(1 for x in labels if x != other_label)
    return non_other / len(labels)


def build_report(values: dict[str, float | None]) -> dict:
    """根据 THRESHOLDS 构造报告。"""
    metrics: list[dict[str, Any]] = []
    hard_failures: list[str] = []
    report_only_misses: list[str] = []

    for name, val in values.items():
        cfg = THRESHOLDS.get(name)
        if cfg is None:
            metrics.append({
                "name": name,
                "value": val,
                "threshold": None,
                "mode": "unknown",
                "passed": None,
            })
            continue
        threshold = cfg["value"]
        mode = cfg["mode"]
        if val is None:
            passed: bool | None = None
        else:
            passed = float(val) >= float(threshold)
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
                report_only_misses.append(name)

    return {
        "spec": "CLASSIFICATION_TAGGING_SPEC v1.0 §6",
        "metrics": metrics,
        "summary": {
            "hard_gate_failures": hard_failures,
            "report_only_misses": report_only_misses,
        },
    }


def run_m1b(record_ids: list[str], k: int, runner: Callable[..., dict]) -> dict:
    """M1-b 聚合器:对每条记录跑 K 次,计算 primary_consistency 与 facet_jaccard。"""
    if k <= 0:
        raise ValueError("k must be positive")

    per_record: list[dict[str, Any]] = []
    primary_vals: list[float] = []
    facet_vals: list[float] = []

    for rid in record_ids:
        primaries: list[str] = []
        facet_sets: list[set] = []
        for i in range(k):
            res = runner(rid, i)
            primaries.append(res["primary_category"])
            facets = res.get("facets", {}) or {}
            merged: set = set()
            for vals in facets.values():
                if vals:
                    for v in vals:
                        merged.add(v)
            facet_sets.append(merged)

        p_cons = modal_agreement_rate(primaries)
        f_jacc = mean_pairwise_jaccard([list(s) for s in facet_sets])
        per_record.append({
            "id": rid,
            "primary_consistency": p_cons,
            "facet_jaccard": f_jacc,
        })
        primary_vals.append(p_cons)
        facet_vals.append(f_jacc)

    return {
        "per_record": per_record,
        "primary_consistency_mean": statistics.fmean(primary_vals) if primary_vals else 1.0,
        "facet_jaccard_mean": statistics.fmean(facet_vals) if facet_vals else 1.0,
        "k": k,
    }


def _scan_records_dir(records_dir: str) -> tuple[list[dict], list[str]]:
    """扫描目录下的 JSON 记录文件,缺失 primary_category 的记 missing。"""
    records: list[dict] = []
    missing_ids: list[str] = []
    for name in sorted(os.listdir(records_dir)):
        if not name.lower().endswith(".json"):
            continue
        p = os.path.join(records_dir, name)
        try:
            with open(p, "r", encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        records.append(rec)
        if "primary_category" not in rec:
            missing_ids.append(rec.get("id", name))
    return records, missing_ids


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eval_consistency",
        description="M1-M4 一致性指标计算与报告(consistency metrics)",
    )
    p.add_argument("--records-dir", default=None, help="目录评测:扫描 JSON 记录")
    p.add_argument("--golden-dir", default=None, help="金标目录:eval/golden/samples")
    p.add_argument("--report-out", default=None, help="输出 JSON 报告到该路径")
    p.add_argument("--k", type=int, default=5, help="M1-b 重跑次数")
    return p


def _load_golden(golden_dir: str) -> list[dict]:
    samples: list[dict] = []
    if not golden_dir or not os.path.isdir(golden_dir):
        return samples
    for name in sorted(os.listdir(golden_dir)):
        if not name.lower().endswith(".json"):
            continue
        p = os.path.join(golden_dir, name)
        try:
            with open(p, "r", encoding="utf-8") as f:
                samples.append(json.load(f))
        except (OSError, json.JSONDecodeError):
            continue
    return samples


def main(argv: list[str] | None = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)

    values: dict[str, float | None] = {}

    if args.golden_dir:
        gold = _load_golden(args.golden_dir)
        if gold:
            primaries = [g["primary_category"] for g in gold if g.get("primary_category")]
            if primaries:
                values["m2_primary_consistency"] = group_consistency_rate(
                    {"golden": primaries}
                )
                values["m4_coverage"] = coverage_rate(primaries)
            moods_a = []
            moods_b = []
            for g in gold:
                m = g.get("mood") or []
                if len(m) >= 2:
                    moods_a.append(m[0])
                    moods_b.append(m[1] if len(m) > 1 else m[0])
            if moods_a and moods_b:
                values["m3_kappa_mood"] = cohens_kappa(moods_a, moods_b)
            shot_a = [g.get("shot_type", "") for g in gold]
            shot_b = [g.get("shot_type", "") for g in gold]
            values["m3_kappa_objective"] = cohens_kappa(shot_a, shot_b) if shot_a else None

    if args.records_dir:
        records, missing = _scan_records_dir(args.records_dir)
        if records:
            primaries = [r.get("primary_category") for r in records if "primary_category" in r]
            if primaries:
                values["m2_primary_consistency"] = group_consistency_rate(
                    {"records": primaries}
                )
                values["m4_coverage"] = coverage_rate(primaries)

    rep = build_report(values)
    rep["missing_primary_category"] = missing if args.records_dir else []
    payload = json.dumps(rep, ensure_ascii=False)

    if args.report_out:
        with open(args.report_out, "w", encoding="utf-8") as f:
            f.write(payload)
    else:
        print(payload)

    return 0


if __name__ == "__main__":
    sys.exit(main())
