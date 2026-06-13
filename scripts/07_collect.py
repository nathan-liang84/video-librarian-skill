"""07_collect —— 服务端归集(§14.C)

读「选择清单」(用户在 06 match 报告里勾的文件名) → 查 manifest.json 取网盘身份
→ 把查到的送 `source.collect()` 服务端零带宽归集到交付夹;查不到的进缺文件报告,
不留 CLI 静默吞掉。

隐私门(§14.3):
- root 非法 → raise,CLI 不产出报告
- 写操作默认 dry-run,需显式 --execute 才真写
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

# 消费 adapters/source_base.py 已声明的 Source / SourceItem 契约;
# 这里只 import,不动 adapters/。
from adapters.source_base import Source, SourceItem  # noqa: F401


# ---------- 选择清单 ----------

def load_selection(path: str) -> list[str]:
    """读 JSON 选择清单。支持 ``["a.mp4", ...]`` 或 ``{"picks": [...]}``。

    - 文件不存在:raise FileNotFoundError
    - 解析后列表为空:raise ValueError
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"selection file not found: {path}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "picks" in data:
        picks = data["picks"]
    elif isinstance(data, list):
        picks = data
    else:
        raise ValueError("selection file must be a list or {picks: [...]}")
    if not isinstance(picks, list):
        raise ValueError("selection.picks must be a list")
    if len(picks) == 0:
        raise ValueError("selection is empty")
    out = [str(x) for x in picks]
    return out


# ---------- 解析 manifest ----------

def _index_manifest(manifest: dict) -> dict[str, dict]:
    """对 manifest 做名字索引,new_name 优先、original_name 回退。

    返回: ``{name: item}``;若重名,先到的优先(在 new_name / original_name 双层都按顺序)。
    这里我们构建一个 ``new_name_index`` + ``original_name_index``,在 resolve_picks 时
    按「全局 new_name 优先」语义挑选。
    """
    items = manifest.get("items") if isinstance(manifest, dict) else None
    if not isinstance(items, list):
        raise ValueError("manifest must be a dict with an 'items' list")
    new_name_index: dict[str, dict] = {}
    original_name_index: dict[str, dict] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        nn = it.get("new_name")
        if isinstance(nn, str) and nn:
            new_name_index.setdefault(nn, it)
        on = it.get("original_name")
        if isinstance(on, str) and on:
            original_name_index.setdefault(on, it)
    return {"new_name": new_name_index, "original_name": original_name_index}


def resolve_picks(picks: list[str], manifest: dict) -> tuple[list[dict], list[str]]:
    """按名字在 manifest 查身份,返回 ``(resolved, missing)``。

    - 名字优先匹配 ``new_name``(全局优先),回退 ``original_name``。
    - 重名取第一个出现。
    - 保持 picks 原序。
    """
    idx = _index_manifest(manifest)
    nn_idx = idx["new_name"]
    on_idx = idx["original_name"]

    resolved: list[dict] = []
    seen: set[str] = set()
    missing: list[str] = []
    for name in picks:
        if name in seen:
            # 同一份选择清单里重复点名:resolved 只出一次,missing 也不重复
            continue
        seen.add(name)
        item = nn_idx.get(name)
        if item is None:
            item = on_idx.get(name)
        if item is None:
            missing.append(name)
            continue
        resolved.append({
            "name": name,
            "remote_path": item.get("remote_path"),
            "fs_id": item.get("fs_id"),
            "md5": item.get("md5"),
        })
    return resolved, missing


# ---------- 隐私门 + 计划构建 ----------

def _validate_root(root: str) -> str:
    """§14.3:root 必填;为空 / ``/`` / 纯空白 → raise ValueError。"""
    if root is None:
        raise ValueError("root is required")
    if not isinstance(root, str):
        raise ValueError("root must be a string")
    if root.strip() == "":
        raise ValueError("root must not be blank")
    if root.strip() == "/":
        raise ValueError("root must not be '/'")
    return root


def _validate_delivery(delivery_name: str) -> str:
    """§14.3:delivery_name 非法(空 / 含 ``/`` / 纯空白)→ raise ValueError。"""
    if delivery_name is None:
        raise ValueError("delivery_name is required")
    if not isinstance(delivery_name, str):
        raise ValueError("delivery_name must be a string")
    if delivery_name.strip() == "":
        raise ValueError("delivery_name must not be blank")
    if "/" in delivery_name:
        raise ValueError("delivery_name must not contain '/'")
    return delivery_name


def build_collect_plan(
    *,
    root: str,
    delivery_name: str,
    resolved: list[dict],
    missing: list[str],
) -> dict:
    """组装归集计划 + 走隐私门。"""
    r = _validate_root(root)
    d = _validate_delivery(delivery_name)
    dest_dir = f"{r.rstrip('/')}/{d}"
    return {
        "dest_dir": dest_dir,
        "items": resolved,
        "missing": list(missing),
        "count": len(resolved),
    }


# ---------- 执行归集 ----------

def execute_collection(
    plan: dict,
    source: Source,
    *,
    dry_run: bool = True,
    move: bool = False,
) -> dict:
    """执行(或不执行)归集。

    - ``dry_run=True``(默认,§14.3):不调用 source 写方法,status=``"dry_run"``。
    - ``dry_run=False``: ``source.mkdir(dest_dir)`` → ``source.collect(items, dest_dir, move=move)``,
      status=``"done"``。
    - source 抛异常 → status=``"error"``,**捕获不向上抛**,留 CLI 做本地下发兜底。
    """
    dest_dir = plan["dest_dir"]
    items_meta: list[dict] = list(plan.get("items") or [])
    missing: list[str] = list(plan.get("missing") or [])

    if dry_run:
        return {
            "status": "dry_run",
            "dest_dir": dest_dir,
            "collected": 0,
            "missing": missing,
            "moved": False,
            "error": None,
        }

    # 把 manifest 的 dict 描述包成 SourceItem 喂给 source.collect
    src_items: list[SourceItem] = []
    for it in items_meta:
        src_items.append(
            SourceItem(
                path=it.get("remote_path") or it.get("name") or "",
                media_type="video",  # 归集阶段不重新探测,留占位
                size=0,
                sha1=None,
                content_md5=it.get("md5"),
                fs_id=it.get("fs_id"),
                remote_path=it.get("remote_path"),
                shot_at=None,
                raw=dict(it),
            )
        )

    try:
        source.mkdir(dest_dir)
        collected = int(source.collect(src_items, dest_dir, move=move) or 0)
    except Exception as exc:  # noqa: BLE001  —— 按契约捕获不向上抛
        return {
            "status": "error",
            "dest_dir": dest_dir,
            "collected": 0,
            "missing": missing,
            "moved": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    return {
        "status": "done",
        "dest_dir": dest_dir,
        "collected": collected,
        "missing": missing,
        "moved": bool(move),
        "error": None,
    }


# ---------- CLI ----------

def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="07_collect", description="服务端归集(§14.C)")
    p.add_argument("--selection", required=True, help="选择清单 JSON 路径")
    p.add_argument("--manifest", required=True, help="state/manifest.json 路径")
    p.add_argument("--root", required=True, help="网盘内归集根目录(必填,隐私门)")
    p.add_argument("--delivery", required=True, help="本次交付夹名(隐私门:禁空/禁 /)")
    p.add_argument("--report", required=True, help="归集报告输出 JSON 路径")
    p.add_argument("--execute", action="store_true", help="真实写(默认 dry-run)")
    p.add_argument("--move", action="store_true", help="移动而非复制")
    p.add_argument(
        "--source",
        default="baidu",
        help="数据源后端标识(默认 baidu;测试会注入 FakeSource)",
    )
    return p


def _write_json(path: str, payload: dict) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_source(name: str) -> Source:
    """按名字取数据源实例。

    真实 BaiduSource 由 adapters.baidu_source 提供;测试会 monkeypatch 此函数注入 FakeSource,
    所以本模块自身不打网络。
    """
    if name == "baidu":
        try:
            from adapters.baidu_source import BaiduSource  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"cannot load BaiduSource: {exc}") from exc
        return BaiduSource()
    raise ValueError(f"unknown source: {name}")


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_argparser().parse_args(argv)

    # 隐私门:root / delivery 非法 → 返非 0、不产出报告
    try:
        _validate_root(args.root)
        _validate_delivery(args.delivery)
    except ValueError as exc:
        print(f"[07_collect] invalid root/delivery: {exc}", file=sys.stderr)
        return 2

    # 读选择清单 + manifest
    try:
        picks = load_selection(args.selection)
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        print(f"[07_collect] missing input: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"[07_collect] bad input: {exc}", file=sys.stderr)
        return 4
    except json.JSONDecodeError as exc:
        print(f"[07_collect] manifest is not valid JSON: {exc}", file=sys.stderr)
        return 5

    resolved, missing = resolve_picks(picks, manifest)

    plan: dict
    try:
        plan = build_collect_plan(
            root=args.root,
            delivery_name=args.delivery,
            resolved=resolved,
            missing=missing,
        )
    except ValueError as exc:
        # 二次校验(理论上 CLI 已把门,但留兜底:不产出报告)
        print(f"[07_collect] invalid plan: {exc}", file=sys.stderr)
        return 2

    source = _load_source(args.source)
    result = execute_collection(
        plan,
        source,
        dry_run=not args.execute,
        move=bool(args.move),
    )

    report = {
        "plan": plan,
        "result": result,
        "selection_path": args.selection,
        "manifest_path": args.manifest,
    }
    _write_json(args.report, report)
    return 0 if result.get("status") in ("dry_run", "done") else 6


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
