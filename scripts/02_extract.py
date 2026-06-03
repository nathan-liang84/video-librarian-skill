#!/usr/bin/env python3
"""阶段2:抽取。视频抽关键帧 + 抽音轨→ASR;照片直读;生成缩略图/雪碧图。

负责人:GPT-5.4。

抽帧策略(读 config.extract):
- 场景切换检测 ffmpeg select='gt(scene,<scene_threshold>)' 为主
- 每 sample_interval_sec 秒均匀采样兜底
- 受 max_frames_per_minute / max_frames_per_video 限制;短视频(<10s)至少 min_frames_short_clip 帧
ASR:ffmpeg 抽音轨 → faster-whisper 转写(无音轨/无人声则 has_speech=False)。
缩略图:代表帧 320px;视频可选生成 3x3 雪碧图。
产物路径写回 record(frames 临时目录、thumbnail、sprite、transcript、has_speech),status→extracted。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.manifest import Manifest  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="state/manifest.json")
    ap.add_argument("--workdir", default="tmp", help="抽帧/音轨临时目录")
    args = ap.parse_args()

    manifest = Manifest(Path(args.manifest)).load()
    # TODO(GPT-5.4): 对 status=pending 的记录抽帧/ASR/缩略图,回写并置 extracted
    manifest.save()
    raise NotImplementedError


if __name__ == "__main__":
    sys.exit(main())
