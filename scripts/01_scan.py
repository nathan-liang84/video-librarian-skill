#!/usr/bin/env python3
"""阶段1:盘点。遍历输入目录 → 内容指纹去重 → 读元数据 → 建/更新 manifest。

负责人:GPT-5.4。

要点:
- 递归遍历,识别视频(mp4/mov/mkv/avi...)与照片(jpg/png/heic...)扩展名。
- id = 文件内容 SHA1 前16位(大文件可分块哈希)。同 id 视为重复,跳过。
- 视频用 ffprobe 取 时长/分辨率/fps/codec/创建时间/GPS;照片读 EXIF(拍摄时间/设备/GPS)。
- 每个文件 upsert 一条 status=pending 的 Record 到 manifest。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.manifest import Manifest  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="素材目录")
    ap.add_argument("--manifest", default="state/manifest.json")
    args = ap.parse_args()

    manifest = Manifest(Path(args.manifest)).load()
    # TODO(GPT-5.4): 遍历 args.input,计算指纹、读元数据、构造 Record、manifest.upsert
    manifest.save()
    raise NotImplementedError


if __name__ == "__main__":
    sys.exit(main())
