"""[T3] scripts/eval_consistency.py + 金标骨架 —— 验收测试(测试先行,Planner 预写,coder 禁改)。

验收 collab #21 / 分类规格 v1.0 §6:
- M1-b/M2/M3/M4 指标计算函数(纯函数,喂构造数据验算法);
- 门槛表与 report 结构(M2 0.95 硬闸 / M3 客观 0.8 硬闸 / M3 mood 0.55 仅报告 / M4 0.9 硬闸 / M1-b 仅报告);
- M1-b 聚合器支持注入 runner(不打真实模型);
- 金标骨架 eval/golden/(schema + ≥10 条样本,覆盖 ≥4 个 primary_category,9 类闭集,无隐私);
- CLI --help 冒烟。
全部离线,无网络调用。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "eval_consistency.py"
GOLDEN_DIR = ROOT / "eval" / "golden"

# 规格 §5.2 定版 9 类闭集
PRIMARY_CATEGORIES = {"人物", "活动事件", "美食", "风景空镜", "建筑空间",
                      "物品产品", "宠物动物", "交通旅途", "其他"}


def _load_module():
    spec = importlib.util.spec_from_file_location("eval_consistency", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ec():
    assert SCRIPT.exists(), "scripts/eval_consistency.py 不存在"
    return _load_module()


# ---- M1-b:同图复现(modal agreement + facet Jaccard) ----------------------

def test_modal_agreement_all_same(ec):
    assert ec.modal_agreement_rate(["人物"] * 5) == 1.0


def test_modal_agreement_majority(ec):
    # 5 跑里 4 次众数 → 0.8
    assert ec.modal_agreement_rate(["人物", "人物", "风景空镜", "人物", "人物"]) == pytest.approx(0.8)


def test_modal_agreement_empty_raises(ec):
    with pytest.raises(ValueError):
        ec.modal_agreement_rate([])


def test_mean_pairwise_jaccard_identical(ec):
    assert ec.mean_pairwise_jaccard([["海边", "治愈"], ["海边", "治愈"]]) == 1.0


def test_mean_pairwise_jaccard_disjoint(ec):
    assert ec.mean_pairwise_jaccard([["海边"], ["室内"]]) == 0.0


def test_mean_pairwise_jaccard_partial(ec):
    # {海边} vs {海边,治愈}:J = 1/2;仅一对 → 0.5
    assert ec.mean_pairwise_jaccard([["海边"], ["海边", "治愈"]]) == pytest.approx(0.5)


def test_mean_pairwise_jaccard_both_empty_is_one(ec):
    # 双空集视为一致(都没标出东西 = 没有分歧)
    assert ec.mean_pairwise_jaccard([[], []]) == 1.0


def test_mean_pairwise_jaccard_single_set_is_one(ec):
    assert ec.mean_pairwise_jaccard([["海边"]]) == 1.0


# ---- M2:pHash 同组一致率 ---------------------------------------------------

def test_group_consistency_half(ec):
    groups = {"g1": ["人物", "人物"], "g2": ["人物", "美食"]}
    assert ec.group_consistency_rate(groups) == pytest.approx(0.5)


def test_group_consistency_ignores_singletons(ec):
    # 单成员组不参与统计(没有"组内一致"可言)
    groups = {"g1": ["人物"], "g2": ["美食", "美食"]}
    assert ec.group_consistency_rate(groups) == 1.0


def test_group_consistency_no_eligible_groups(ec):
    # 无 ≥2 成员组 → 空虚真(返回 1.0),报告侧另有计数
    assert ec.group_consistency_rate({"g1": ["人物"]}) == 1.0


# ---- M3:Cohen's κ ----------------------------------------------------------

def test_kappa_perfect(ec):
    assert ec.cohens_kappa(["人物", "美食", "其他"], ["人物", "美食", "其他"]) == pytest.approx(1.0)


def test_kappa_independence_is_zero(ec):
    # po=0.5, pe=0.5 → κ=0
    assert ec.cohens_kappa(["x", "x", "y", "y"], ["x", "y", "x", "y"]) == pytest.approx(0.0)


def test_kappa_known_value(ec):
    # po=0.75, pe=0.5 → κ=0.5
    assert ec.cohens_kappa(["x", "x", "x", "y"], ["x", "x", "y", "y"]) == pytest.approx(0.5)


def test_kappa_length_mismatch_raises(ec):
    with pytest.raises(ValueError):
        ec.cohens_kappa(["x"], ["x", "y"])


def test_kappa_constant_perfect_agreement(ec):
    # 两标注者全程同一类(pe=1 退化):完全一致 → 1.0
    assert ec.cohens_kappa(["x", "x"], ["x", "x"]) == pytest.approx(1.0)


# ---- M4:覆盖率 -------------------------------------------------------------

def test_coverage_rate(ec):
    assert ec.coverage_rate(["人物", "其他", "风景空镜", "其他"]) == pytest.approx(0.5)


def test_coverage_empty_is_zero(ec):
    assert ec.coverage_rate([]) == 0.0


# ---- 门槛表与报告(规格 §6 数字写死,防漂移) --------------------------------

def test_thresholds_match_spec(ec):
    t = ec.THRESHOLDS
    assert t["m2_primary_consistency"]["value"] == pytest.approx(0.95)
    assert t["m2_primary_consistency"]["mode"] == "hard"
    assert t["m3_kappa_objective"]["value"] == pytest.approx(0.8)
    assert t["m3_kappa_objective"]["mode"] == "hard"
    assert t["m3_kappa_mood"]["value"] == pytest.approx(0.55)
    assert t["m3_kappa_mood"]["mode"] == "report_only"
    assert t["m4_coverage"]["value"] == pytest.approx(0.9)
    assert t["m4_coverage"]["mode"] == "hard"
    # M1-b 本切片 report-only(基线稳定后才转硬闸)
    assert t["m1b_primary"]["mode"] == "report_only"
    assert t["m1b_facet_jaccard"]["mode"] == "report_only"


def test_build_report_structure_and_gates(ec):
    rep = ec.build_report({
        "m2_primary_consistency": 0.90,   # 硬闸 miss
        "m3_kappa_objective": 0.85,       # 硬闸 pass
        "m3_kappa_mood": 0.40,            # report-only miss → 不进硬闸失败
        "m4_coverage": 0.95,              # 硬闸 pass
        "m1b_primary": None,              # 没跑 → 诚实输出 None
    })
    json.dumps(rep, ensure_ascii=False)   # 必须可序列化
    assert "m2_primary_consistency" in rep["summary"]["hard_gate_failures"]
    assert "m3_kappa_mood" not in rep["summary"]["hard_gate_failures"]
    assert "m3_kappa_mood" in rep["summary"]["report_only_misses"]
    by_name = {m["name"]: m for m in rep["metrics"]}
    assert by_name["m3_kappa_objective"]["passed"] is True
    assert by_name["m1b_primary"]["value"] is None
    assert by_name["m1b_primary"]["passed"] is None


# ---- M1-b 聚合器(注入 runner,不打真实模型) --------------------------------

def test_run_m1b_with_injected_runner(ec):
    seq = {
        "r1": [{"primary_category": "人物", "facets": {"scene": ["海边"], "mood": ["治愈"]}},
               {"primary_category": "人物", "facets": {"scene": ["海边"], "mood": ["热闹"]}}],
        "r2": [{"primary_category": "美食", "facets": {"scene": ["室内"]}},
               {"primary_category": "美食", "facets": {"scene": ["室内"]}}],
    }
    calls = {"n": 0}

    def fake_runner(record_id, k_index):
        calls["n"] += 1
        return seq[record_id][k_index]

    out = ec.run_m1b(["r1", "r2"], k=2, runner=fake_runner)
    assert calls["n"] == 4
    per = {p["id"]: p for p in out["per_record"]}
    assert per["r1"]["primary_consistency"] == 1.0
    # r1 facet 全集:{海边,治愈} vs {海边,热闹} → J = 1/3
    assert per["r1"]["facet_jaccard"] == pytest.approx(1 / 3)
    assert per["r2"]["facet_jaccard"] == 1.0
    assert out["primary_consistency_mean"] == 1.0
    assert out["facet_jaccard_mean"] == pytest.approx((1 / 3 + 1.0) / 2)
    assert out["k"] == 2


# ---- 金标骨架 eval/golden/ ---------------------------------------------------

def _golden_samples():
    files = sorted((GOLDEN_DIR / "samples").glob("*.json"))
    return [(f, json.loads(f.read_text(encoding="utf-8"))) for f in files]


def test_golden_schema_exists():
    assert (GOLDEN_DIR / "schema.json").exists(), "eval/golden/schema.json 不存在"
    schema = json.loads((GOLDEN_DIR / "schema.json").read_text(encoding="utf-8"))
    req = set(schema.get("required", []))
    assert {"id", "media_type", "primary_category"} <= req


def test_golden_samples_at_least_ten():
    assert len(_golden_samples()) >= 10


def test_golden_samples_valid_and_diverse():
    samples = _golden_samples()
    ids = set()
    cats = set()
    for path, s in samples:
        assert s["id"] not in ids, f"重复 id: {s['id']}"
        ids.add(s["id"])
        assert s["media_type"] in ("video", "photo")
        assert s["primary_category"] in PRIMARY_CATEGORIES, \
            f"{path.name}: {s['primary_category']} 不在 9 类闭集"
        cats.add(s["primary_category"])
    assert len(cats) >= 4, f"样本只覆盖 {len(cats)} 个 primary_category,需 ≥4"


def test_golden_samples_no_privacy():
    # 金标样本是要进 git 的:不得含本机路径 / 网盘个人目录结构
    for path, s in _golden_samples():
        text = json.dumps(s, ensure_ascii=False)
        for needle in ("/Users/", "/var/", "C:\\", "baidu", "netdisk"):
            assert needle not in text, f"{path.name} 含疑似隐私片段: {needle}"


# ---- CLI 冒烟(不打网络) -----------------------------------------------------

def test_cli_help_exits_zero():
    r = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    assert "consistency" in (r.stdout + r.stderr).lower() or "一致" in (r.stdout + r.stderr)


# ---- CLI 集成(端到端,不打网络;补 collab #21 主用途的覆盖盲区) --------------
# 纯函数全绿 ≠ CLI 能跑。--records-dir 是 #21 的主用途,必须真出报告、不崩。

def _write_record(d, rid, primary, media="photo", **facets):
    rec = {"id": rid, "media_type": media, "primary_category": primary}
    rec.update(facets)
    (d / f"{rid}.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")


def test_cli_records_dir_produces_report(tmp_path):
    """--records-dir:对现有旁车算 M2/M4 并输出结构化 JSON 报告到 stdout,且不得崩。
    (报告结构同 build_report:{"metrics":[{name,value,passed}],"summary":{...}})"""
    d = tmp_path / "recs"; d.mkdir()
    _write_record(d, "r1", "人物")
    _write_record(d, "r2", "美食")
    _write_record(d, "r3", "其他")
    _write_record(d, "r4", "风景空镜")
    r = subprocess.run([sys.executable, str(SCRIPT), "--records-dir", str(d)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"--records-dir 应正常退出,实际崩了:\n{r.stderr[-600:]}"
    rep = json.loads(r.stdout)
    assert "metrics" in rep and "summary" in rep, "报告需含 metrics/summary"
    by = {m["name"]: m for m in rep["metrics"]}
    # 4 条里 3 条非"其他" → M4 覆盖率 0.75
    assert by["m4_coverage"]["value"] == pytest.approx(0.75)


def test_cli_golden_dir_computes_m3(tmp_path):
    """--golden-dir:必须真用上算 M3 κ(旁车标注当预测、golden 当 ground truth,按 id 对齐),
    不能解析了参数却空输出。"""
    recs = tmp_path / "recs"; recs.mkdir()
    gold = tmp_path / "gold"; gold.mkdir()
    preds = {"r1": "人物", "r2": "美食", "r3": "人物", "r4": "其他"}
    truth = {"r1": "人物", "r2": "美食", "r3": "人物", "r4": "风景空镜"}
    for rid, c in preds.items():
        _write_record(recs, rid, c)
    for rid, c in truth.items():
        _write_record(gold, rid, c)
    r = subprocess.run([sys.executable, str(SCRIPT),
                        "--records-dir", str(recs), "--golden-dir", str(gold)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"--golden-dir 应正常退出:\n{r.stderr[-600:]}"
    rep = json.loads(r.stdout)
    by = {m["name"]: m for m in rep["metrics"]}
    assert "m3_kappa_objective" in by, "给了 --golden-dir 就应算 M3"
    assert by["m3_kappa_objective"]["value"] is not None, "--golden-dir 给了却没算 M3(空输出)"
