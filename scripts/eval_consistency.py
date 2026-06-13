"""M1-M4 consistency evaluation metrics for VideoLibrarian.

Pure stdlib implementation. No third-party dependencies, no network I/O.
See CLASSIFICATION_TAGGING_SPEC §6 for thresholds.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from itertools import combinations
from typing import Callable


THRESHOLDS: dict = {
    "m1b_primary":            {"value": 1.0,  "mode": "report_only"},
    "m1b_facet_jaccard":      {"value": 0.95, "mode": "report_only"},
    "m2_primary_consistency": {"value": 0.95, "mode": "hard"},
    "m3_kappa_objective":     {"value": 0.8,  "mode": "hard"},
    "m3_kappa_mood":          {"value": 0.55, "mode": "report_only"},
    "m4_coverage":            {"value": 0.9,  "mode": "hard"},
}


def modal_agreement_rate(labels: list[str]) -> float:
    """Fraction of labels that agree with the mode.

    Empty list raises ValueError.
    """
    if not labels:
        raise ValueError("labels must be non-empty")
    counts = Counter(labels)
    mode_count = max(counts.values())
    return mode_count / len(labels)


def _jaccard(a: set, b: set) -> float:
    """Jaccard similarity; both empty sets -> 1.0 (per spec)."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    inter = a & b
    return len(inter) / len(union)


def mean_pairwise_jaccard(sets: list[list[str]]) -> float:
    """Mean Jaccard over all unordered pairs.

    - Double-empty pair is counted as 1.0.
    - Fewer than 2 sets returns 1.0.
    """
    if len(sets) < 2:
        return 1.0
    total = 0.0
    n = 0
    for a, b in combinations(sets, 2):
        total += _jaccard(set(a), set(b))
        n += 1
    return total / n if n else 1.0


def group_consistency_rate(groups: dict[str, list[str]]) -> float:
    """Among groups with >=2 members, fraction where all labels match.

    Single-member groups are ignored. No eligible group -> 1.0.
    """
    eligible = [labels for labels in groups.values() if len(labels) >= 2]
    if not eligible:
        return 1.0
    consistent = sum(1 for labels in eligible if len(set(labels)) == 1)
    return consistent / len(eligible)


def cohens_kappa(a: list[str], b: list[str]) -> float:
    """Standard Cohen's kappa.

    Mismatched lengths raise ValueError.
    When pe == 1 (degenerate): perfect agreement -> 1.0, else 0.0.
    """
    if len(a) != len(b):
        raise ValueError("a and b must have equal length")
    n = len(a)
    if n == 0:
        return 1.0
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    ca = Counter(a)
    cb = Counter(b)
    pe = sum((ca[k] / n) * (cb[k] / n) for k in set(ca) | set(cb))
    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1.0 - pe)


def coverage_rate(labels: list[str], other_label: str = "其他") -> float:
    """Fraction of labels that are NOT the 'other' label.

    Empty list -> 0.0.
    """
    if not labels:
        return 0.0
    return sum(1 for x in labels if x != other_label) / len(labels)


def build_report(values: dict[str, float | None]) -> dict:
    """Build the §6 evaluation report.

    For each metric whose name matches a THRESHOLDS key, attach threshold and
    mode. `passed` is True when value >= threshold, False when value < threshold,
    None when value is None (not run).
    """
    metrics: list[dict] = []
    hard_gate_failures: list[str] = []
    report_only_misses: list[str] = []

    for name, value in values.items():
        if name in THRESHOLDS:
            t = THRESHOLDS[name]
            threshold = t["value"]
            mode = t["mode"]
        else:
            threshold = None
            mode = "report_only"
        if value is None:
            passed = None
        else:
            passed = value >= threshold
        metrics.append({
            "name": name,
            "value": value,
            "threshold": threshold,
            "mode": mode,
            "passed": passed,
        })
        if passed is False:
            if mode == "hard":
                hard_gate_failures.append(name)
            else:
                report_only_misses.append(name)

    return {
        "spec": "CLASSIFICATION_TAGGING_SPEC v1.0 §6",
        "metrics": metrics,
        "summary": {
            "hard_gate_failures": hard_gate_failures,
            "report_only_misses": report_only_misses,
        },
    }


def run_m1b(
    record_ids: list[str],
    k: int,
    runner: Callable[[str, int], dict],
) -> dict:
    """M1-b aggregator.

    For each record, runs `runner(record_id, k_idx)` K times. Computes
    primary consistency via modal agreement on primary_category, and facet
    Jaccard across the K sets of facets.
    """
    per_record: list[dict] = []
    for rid in record_ids:
        primaries: list[str] = []
        facet_sets: list[list[str]] = []
        for k_idx in range(k):
            out = runner(rid, k_idx)
            primaries.append(out["primary_category"])
            merged: list[str] = []
            for vals in out.get("facets", {}).values():
                merged.extend(vals)
            facet_sets.append(merged)
        per_record.append({
            "id": rid,
            "primary_consistency": modal_agreement_rate(primaries),
            "facet_jaccard": mean_pairwise_jaccard(facet_sets),
        })
    if per_record:
        primary_mean = sum(r["primary_consistency"] for r in per_record) / len(per_record)
        facet_mean = sum(r["facet_jaccard"] for r in per_record) / len(per_record)
    else:
        primary_mean = 1.0
        facet_mean = 1.0
    return {
        "per_record": per_record,
        "primary_consistency_mean": primary_mean,
        "facet_jaccard_mean": facet_mean,
        "k": k,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eval_consistency",
        description="Evaluate M1-M4 classification consistency metrics (CLASSIFICATION_TAGGING_SPEC §6).",
    )
    parser.add_argument(
        "--records-dir",
        default=None,
        help="Directory of record JSON files to evaluate (optional).",
    )
    parser.add_argument(
        "--report-out",
        default=None,
        help="Path to write the JSON report (optional).",
    )
    parser.add_argument(
        "--golden-dir",
        default=None,
        help="Directory of golden samples for benchmark comparison (optional).",
    )
    return parser


def _evaluate_records_dir(records_dir: str) -> dict:
    """Best-effort field-missing-aware evaluation of records in a directory.

    Honors the spec note: when primary_category is absent, counts as missing
    rather than guessing.
    """
    primaries: list[str] = []
    missing_count = 0
    total = 0
    for name in sorted(os.listdir(records_dir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(records_dir, name), encoding="utf-8") as f:
            rec = json.load(f)
        total += 1
        if "primary_category" in rec and rec["primary_category"] is not None:
            primaries.append(rec["primary_category"])
        else:
            missing_count += 1

    values: dict[str, float | None] = {}
    if primaries:
        values["m1b_primary"] = modal_agreement_rate(primaries)
        values["m4_coverage"] = coverage_rate(primaries)
    else:
        values["m1b_primary"] = None
        values["m4_coverage"] = None

    values["missing_primary_count"] = float(missing_count)
    values["total_records"] = float(total)

    report = build_report(values)
    report["meta"] = {
        "records_dir": records_dir,
        "missing_primary_count": missing_count,
        "total_records": total,
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not (args.records_dir or args.report_out or args.golden_dir):
        parser.print_help()
        return 0

    report: dict = {
        "spec": "CLASSIFICATION_TAGGING_SPEC v1.0 §6",
        "metrics": [],
        "summary": {"hard_gate_failures": [], "report_only_misses": []},
    }

    if args.records_dir:
        report = _evaluate_records_dir(args.records_dir)

    if args.report_out:
        with open(args.report_out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    if report.get("summary", {}).get("hard_gate_failures"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
