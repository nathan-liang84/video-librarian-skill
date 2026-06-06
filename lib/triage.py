"""照片分诊(triage)助手(纯函数,可单测)。

职责:在调模型之前(成本控制点)做"砍量+归一"决策:
1. 垃圾启发式:判定 junk_reason(screenshot/document/meme;None=保留)
2. 感知哈希:pHash + 汉明距离,识别"近重复/连拍"
3. 归组:相似 + 时间近的合并成组、挑选代表

注:本模块**不产出 `content_kind`**。`content_kind` 字段是「目录级媒体类型」
(video/photo/mixed,由 01_scan 写,见 PR #29);照片是否为垃圾(截图/文档/表情包)
的结论统一用 ``is_junk`` + ``junk_reason`` 表达,不再占用 content_kind。

设计原则(同 lib/imaging.py):
- **不依赖项目其它业务对象**:只接 path / dict,与 record/manifest/schema 解耦。
- **优雅降级,绝不抛**:缺 imagehash / PIL / 文件坏一律返回保守结果(None/原组)。
- **保守**:宁可漏过不可错杀(issue #4 边界);拿不到充分信息时默认判"照片"。
- **本模块是 P1b 纯函数层**:与集成接缝(单独 issue)严格分离 —— 不读 lib/record、
  不读 lib/manifest、不读 schema,不引入任何新 status/字段。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


# ---------- 1. 垃圾启发式 ----------

# 常见屏幕分辨率(WxH,iOS/Android 桌面 / 主流壁纸 / 截屏常见输出)
# 与"无相机 EXIF"组合判定截图;任一命中 + 无 EXIF 才判截图,降低误杀。
_SCREEN_RESOLUTIONS: frozenset[tuple[int, int]] = frozenset({
    # iPhone
    (750, 1334), (1080, 1920), (1170, 2532), (1179, 2556), (1284, 2778),
    (1290, 2796), (1320, 2868), (1206, 2622),  # iPhone 12~16 系列
    (640, 1136), (1242, 2688), (1125, 2436),  # iPhone 5~11 系列
    # Android 主流
    (1080, 2160), (1080, 2340), (1080, 2400), (1440, 2560), (1440, 3120),
    (1440, 3200), (1600, 2560), (1800, 2400), (1920, 1080),
    # 横屏/桌面壁纸
    (1920, 1080), (2560, 1440), (3840, 2160), (2560, 1600), (2880, 1800),
})

# 路径关键词(大小写不敏感):命中直接判截图,不依赖 EXIF
_SCREENSHOT_PATH_TOKENS = ("screenshot", "截屏", "截图", "screen shot")


def _normalize_res(resolution: Any) -> tuple[int, int] | None:
    if not resolution:
        return None
    if isinstance(resolution, (tuple, list)) and len(resolution) == 2:
        try:
            return int(resolution[0]), int(resolution[1])
        except (TypeError, ValueError):
            return None
    if isinstance(resolution, str) and "x" in resolution.lower():
        try:
            w_str, h_str = resolution.lower().split("x", 1)
            return int(w_str), int(h_str)
        except (TypeError, ValueError):
            return None
    return None


def classify_content(
    path: Any,
    *,
    resolution: Any = None,
    has_camera_exif: bool | None = None,
) -> str | None:
    """判定照片是否为垃圾,返回 ``junk_reason``。

    返回 ``junk_reason``:
        - ``None``  —— 不是垃圾(当作正常照片保留)
        - 短码字符串 —— 垃圾原因:``"screenshot" / "document" / "meme"``
          (调用方据此置 ``is_junk=True`` + ``junk_reason=<短码>``)

    本函数**不再返回 content_kind**:照片子类(截图/文档/表情包)不占用 record
    的 ``content_kind`` 字段(那是 01_scan 的目录级 video/photo/mixed),
    其信息已由 ``junk_reason`` 完整表达。

    启发式(保守,宁可漏过不可错杀):
        1. 路径含 screenshot / 截屏 / 截图 → ``"screenshot"``
        2. 无相机 EXIF 且分辨率命中常见屏幕尺寸 → ``"screenshot"``
        3. 无相机 EXIF 且分辨率非典型屏幕 → ``None``
           (文档/表情包判定需要读图分析色彩复杂度,超出本纯函数范围,
           issue #4 边界:本函数不读图,留待集成层或后续启发式)
        4. 拿不到充分信息 → ``None``(默认安全,保留)

    本函数不读图、不依赖 PIL;纯基于 path + 启发式参数。
    """
    # 1. 路径关键词(强信号)
    try:
        name = str(Path(path).name).lower() if path else ""
        full = str(path).lower() if path else ""
    except Exception:  # noqa: BLE001
        name = ""
        full = ""

    for token in _SCREENSHOT_PATH_TOKENS:
        if token in name or token in full:
            return "screenshot"

    # 2. 屏幕尺寸 + 无 EXIF
    norm_res = _normalize_res(resolution)
    if has_camera_exif is False and norm_res is not None:
        if norm_res in _SCREEN_RESOLUTIONS:
            return "screenshot"
        # 非典型屏幕分辨率但也无 EXIF:保守保留(可能是屏幕录像截取/网络下载图,
        # 留待集成层用更深启发式过滤)
        return None

    # 3/4. 有 EXIF 或无信息 → 保留
    return None


# ---------- 2. 感知哈希 ----------

def _imagehash_available() -> bool:
    """imagehash 是否可用(可 import + Pillow 可用)。供环境探测用,不抛。"""
    try:
        import imagehash  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def phash(path: Any) -> str | None:
    """读图 → imagehash.phash → 16 字符十六进制串(64 bit)。

    缺 imagehash / PIL / 文件坏 / 格式不支持 / 任何异常 → ``None``,绝不抛。
    """
    try:
        import imagehash  # noqa: WPS433
        from PIL import Image  # noqa: WPS433
    except ImportError:
        return None

    try:
        src = Path(path)
        if not src.is_file():
            return None
        with Image.open(src) as im:
            h = imagehash.phash(im)
        # ImageHash 对象 str() 是 16 字符 hex;直接 str() 输出最稳
        return str(h) if h is not None else None
    except Exception:  # noqa: BLE001
        return None


# ---------- 3. 汉明距离 ----------

# 视作"不相似"的保守大数:大于任意正常阈值(默认 5 / 64)
_HAMMING_INVALID = 10**9


def hamming(a: Any, b: Any) -> int:
    """两个等长十六进制串的汉明距离。

    - 输入必须同长 hex(忽略大小写);否则返回 ``_HAMMING_INVALID`` 视为不相似。
    - 非字符串 / 长度不一致 / 含非 hex 字符 → 大数,不抛。
    """
    if not isinstance(a, str) or not isinstance(b, str):
        return _HAMMING_INVALID
    if len(a) != len(b) or len(a) == 0:
        return _HAMMING_INVALID
    try:
        ba = bytes.fromhex(a)
        bb = bytes.fromhex(b)
    except ValueError:
        return _HAMMING_INVALID
    xor = int.from_bytes(ba, "big") ^ int.from_bytes(bb, "big")
    # bin(xor).count("1") 在 Python 3.10+ 有 int.bit_count(); 用最稳的 bin().count
    return bin(xor).count("1")


# ---------- 4. 近重复归组 ----------

def _parse_shot_at(shot_at: Any) -> float | None:
    """shot_at → epoch 秒。None / 解析失败 → None(不参与时间近邻判定)。"""
    if not shot_at or not isinstance(shot_at, str):
        return None
    from datetime import datetime, timezone
    text = shot_at.strip()
    for parser in (
        lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")),
        lambda s: datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"),
    ):
        try:
            dt = parser(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (ValueError, TypeError):
            continue
    return None


def group_near_duplicates(
    items: list[dict[str, Any]],
    *,
    max_distance: int = 5,
    time_window_sec: int = 3,
) -> list[list[dict[str, Any]]]:
    """按 pHash + 时间近邻归组。

    输入 items: ``[{"id": str, "phash": str, "shot_at": str|None}, ...]``
        - id 必填,用于产出
        - phash 缺/None → 该项各自单独成组
        - shot_at 缺/无法解析 → 仍按 pHash 近邻(不强求时间一致)

    同组条件:**两者都满足**
        - ``hamming(phash_i, phash_j) < max_distance``
        - 都解析得了时间时 ``|t_i - t_j| <= time_window_sec``(任一缺时间则忽略时间约束)

    返回:list[list[dict]];每一项内部 dict 保持原样。**单独的项也成 1-组**(不丢弃)。
    """
    n = len(items)
    # 阶段 1:phash=None 的项直接各自成一组
    direct_groups: list[list[dict[str, Any]]] = []
    pool: list[tuple[int, dict[str, Any]]] = []  # (原 index, item) — 有 phash 的项
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        h = item.get("phash")
        if not h or not isinstance(h, str):
            direct_groups.append([item])
        else:
            pool.append((idx, item))

    # 阶段 2:Union-Find 把 pool 里有 phash 的项合并
    parent = list(range(len(pool)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # 路径压缩
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # 预解析时间(供后面 O(1) 比较)
    times: list[float | None] = [_parse_shot_at(it.get("shot_at")) for _, it in pool]

    for i in range(len(pool)):
        for j in range(i + 1, len(pool)):
            h_i = pool[i][1].get("phash")
            h_j = pool[j][1].get("phash")
            if not h_i or not h_j:
                continue
            d = hamming(h_i, h_j)
            if d >= max_distance:
                continue
            # 时间近邻约束(任一缺时间则忽略)
            t_i, t_j = times[i], times[j]
            if t_i is not None and t_j is not None:
                if abs(t_i - t_j) > time_window_sec:
                    continue
            union(i, j)

    # 阶段 3:按根聚拢
    groups_map: dict[int, list[dict[str, Any]]] = {}
    for idx, (_, item) in enumerate(pool):
        r = find(idx)
        groups_map.setdefault(r, []).append(item)

    return list(groups_map.values()) + direct_groups


# ---------- 5. 选代表 ----------

def pick_representative(members: list[dict[str, Any]]) -> Any:
    """从一组候选中挑代表。

    排序键:**分辨率面积(降序)→ has_exif(降序,True 优先)→ 输入顺序(稳定)**。
    返回 ``members[i]["id"]``。空列表 → ``None``。
    """
    if not members:
        return None

    def _area(m: dict[str, Any]) -> int:
        norm = _normalize_res(m.get("resolution"))
        if norm is None:
            return 0
        return norm[0] * norm[1]

    def _has_exif(m: dict[str, Any]) -> int:
        v = m.get("has_exif")
        return 1 if v else 0

    best = max(
        members,
        key=lambda m: (_area(m), _has_exif(m)),
    )
    return best.get("id")
