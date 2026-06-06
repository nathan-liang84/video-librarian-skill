"""照片归一化助手(纯函数,可单测)。

职责:把任意来源的照片(含 HEIC、含 EXIF 方向)统一成「摆正的 RGB JPEG」,
供下游模型理解使用。

设计原则:
- **不依赖项目其它模块**:可独立 import、单测、跨阶段调用。
- **优雅降级,绝不抛异常**:缺 pillow-heif / PIL 缺失 / 文件损坏 / 格式不支持
  一律返回 False,调用方据此决定是否跳过或走旁路。
- **幂等**:register_heif() 可重复调用,内部去重。
- **副作用最小**:normalize_photo 只在 dst 写新文件,不修改 src;dst 父目录自动建。

本模块是 P1a-B(HEIC/方向)的"完全隔离"层,与 #22 的集成接缝严格分离:
- 不读 lib/record、不读 lib/manifest、不读 schema
- 不引入任何新 status、schema 字段
- 不依赖具体业务对象,只接受 Path
"""
from __future__ import annotations

from pathlib import Path

# register_heif 的幂等哨兵:成功注册后置 True,避免每次调用都重复执行。
_HEIF_REGISTERED: bool = False


def heif_available() -> bool:
    """pillow-heif 是否可用(可 import)。

    仅做 import 探测,不注册到 PIL;适合在环境探测/CLI 提示里轻量调用。
    任何异常都被吞掉,返回 False。
    """
    try:
        import pillow_heif  # noqa: F401
    except Exception:  # noqa: BLE001  - 缺依赖/导入失败都视作不可用
        return False
    return True


def register_heif() -> bool:
    """把 pillow-heif 注册到 PIL,使 ``Image.open`` 能读 .heic / .heif。

    幂等(可重复调用);未安装时返回 False 且不抛异常;成功注册返回 True。
    在 normalize_photo 之前调用一次即可。
    """
    global _HEIF_REGISTERED
    if _HEIF_REGISTERED:
        return True
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except Exception:  # noqa: BLE001
        return False
    _HEIF_REGISTERED = True
    return True


def normalize_photo(src: Path, dst: Path) -> bool:
    """把 src 照片摆正(按 EXIF Orientation)并转存为 RGB JPEG 到 dst。

    行为约定:
        - 调用前会**自动** register_heif(),以支持 HEIC(失败仍继续,普通格式不受影响)。
        - 用 :func:`PIL.ImageOps.exif_transpose` 按 EXIF Orientation 旋正。
        - 转 ``RGB`` 后保存为 JPEG(``quality=95``),保留 EXIF 元数据(若有)。
        - ``dst.parent`` 自动 ``mkdir(parents=True, exist_ok=True)``。
        - 成功返回 ``True``;任何异常(缺 PIL、文件损坏、格式不支持)返回 ``False``,不抛。
    """
    try:
        register_heif()

        from PIL import Image, ImageOps, UnidentifiedImageError  # noqa: WPS433

        src_path = Path(src)
        dst_path = Path(dst)

        if not src_path.is_file():
            return False

        # 读 + 摆正(按 EXIF Orientation)
        try:
            with Image.open(src_path) as im:
                im = ImageOps.exif_transpose(im)
                # 某些格式(尤其动图/CMYK)convert 前需先拷贝,ImageOps 已返回新图
                im = im.convert("RGB")
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                # 透传 EXIF(若有),便于下游保留拍摄时间等元数据
                exif_bytes = getattr(im, "info", {}).get("exif")
                save_kwargs: dict = {"quality": 95, "optimize": True}
                if exif_bytes is not None:
                    save_kwargs["exif"] = exif_bytes
                im.save(dst_path, format="JPEG", **save_kwargs)
        except (UnidentifiedImageError, OSError, ValueError, TypeError):
            return False
        except Exception:  # noqa: BLE001 - 任何 PIL 内部异常都视作失败
            return False

        return dst_path.is_file()
    except Exception:  # noqa: BLE001 - 顶层兜底,绝不抛
        return False
