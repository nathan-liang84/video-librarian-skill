#!/usr/bin/env python3
"""阶段6:脚本匹配。剪辑脚本 → 镜头需求 → 召回候选素材 + 推荐时间码。

负责人:Opus 4.8(解析 + 过滤 + 语义排序策略)。

流程:
1. TextModel.parse_script(script) → 镜头需求列表(scene/subjects/mood/shot_type/min_dur/keyword)。
2. 硬过滤:用受控字段 + 人物 + 时长在记录集上筛(结构化、可解释)。
3. 语义排序:TextModel.rank_candidates 对硬过滤结果按 summary/description 相似度排序。
4. 输出每个需求的候选清单:new_name + usable_clips 时间码 + 理由 + 置信度 + 缩略图。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.manifest import Manifest  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", required=True, help="剪辑脚本/分镜文本文件")
    ap.add_argument("--manifest", default="state/manifest.json")
    ap.add_argument("--top", type=int, default=3, help="每个镜头需求返回候选数")
    args = ap.parse_args()

    manifest = Manifest(Path(args.manifest)).load()
    # TODO(Opus 4.8): parse_script → 硬过滤 → rank → 打印候选清单
    raise NotImplementedError


if __name__ == "__main__":
    sys.exit(main())
