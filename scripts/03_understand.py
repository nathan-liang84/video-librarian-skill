#!/usr/bin/env python3
"""阶段3:理解(核心智能环节)。M3 看帧(+主角参考图)+ M2.7 融合 → 结构化内容。

负责人:Opus 4.8(提示词工程 + 受控约束 + 融合逻辑)。

流程:
1. VisionModel(M3).analyze(frames, vocab, people_roster, ref_images, media_type)
   → scene/shot_type/camera_move/mood/lighting/subjects/actions/quality_score。
   关键约束:输出必须落在 vocab 枚举内;subjects 必须落在人物名册内(或 多人/空镜)。
2. TextModel(M2.7).summarize_and_tag(vision_result, transcript, metadata, vocab)
   → summary/description/tags/suggested_use/usable_clips/confidence。
3. 合并写回 record,status→understood;confidence<阈值 或 quality<阈值 → needs_review。

提示词模板放 prompts/(Opus 编写),强制 JSON 输出 + 枚举校验 + 失败重试。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.manifest import Manifest  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="state/manifest.json")
    ap.add_argument("--tier", choices=["quick", "refine"], default=None,
                    help="覆盖 config.cost_tier")
    args = ap.parse_args()

    manifest = Manifest(Path(args.manifest)).load()
    # TODO(Opus 4.8): 构建 vision/text 模型 → 逐条理解 → 校验受控枚举 → 回写
    manifest.save()
    raise NotImplementedError


if __name__ == "__main__":
    sys.exit(main())
