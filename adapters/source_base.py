"""数据源适配器(Source) —— 把"本地目录"和"网盘"统一成同一组操作。

与 adapters/base.py 的 StoreAdapter(把记录写出到飞书/旁车)**正交**:
- StoreAdapter = 结果写出(下游)
- Source       = 素材读入 + 网盘侧文件操作(上游)

01 起各阶段只依赖 Source 接口,不直接碰 os.walk / HTTP,从而本地与网盘共用同一条管线。

实现拆分(见 docs/NETDISK_PIPELINE.md §12):
- LocalSource  (P1-N2):把现有 01_scan 的 os.walk + ffprobe/EXIF 行为零变化包进来。
- BaiduSource  (P1-N3/N4):xpan list/filemetas + streaming/dlink 抽帧 + filemanager 写操作。

读操作(list/stat/frames)为抽象方法,任何 Source 必须实现;
写操作(rename/mkdir/collect/put_sidecar)默认抛 NotImplementedError —— 只读阶段(Phase 1)
的数据源无需实现,Phase 2/3 再按后端补齐。

负责人:Opus 4.8(契约)。字段/接口改动属协作章程契约红线,须走 Opus 评审。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

# record.id 取内容指纹前 16 位(与 01_scan 的 sha1_file(path)[:16] 同宽)。
_ID_WIDTH = 16


def derive_record_id(*, sha1: Optional[str] = None, md5: Optional[str] = None) -> Optional[str]:
    """由内容指纹派生 record.id(16 hex)。

    身份语义:record.id = 内容指纹,**不是** fs_id/路径。
    - 本地记录:sha1(文件内容)[:16](01_scan 现有逻辑)。
    - 网盘记录:优先用 filemetas 返回的 md5[:16](免下载即可拿到,与本地 SHA1 同宽同构)。
    两者都缺 → 返回 None,由调用方降级兜底(如 fs_id+size 哈希)并标注不参与跨副本去重。
    """
    h = sha1 or md5
    return h[:_ID_WIDTH] if h else None


@dataclass
class SourceItem:
    """数据源里的一个素材条目 —— 成为 Record 之前的中间体。

    只承载"定位 + 身份 + 原始元数据",不含理解结果。各阶段从 SourceItem 构建/补全 Record。
    """
    path: str                                # 本地绝对路径,或网盘远端路径
    media_type: str                          # "video" | "photo"
    size: int = 0
    sha1: Optional[str] = None               # 本地内容 SHA1(LocalSource 填)
    content_md5: Optional[str] = None        # 网盘 filemetas 的 md5(BaiduSource 填)
    fs_id: Optional[str] = None              # 网盘操作锚点(rename/move/copy);**不是** record.id
    remote_path: Optional[str] = None        # 网盘内路径(人读;改名/归集后会变)
    shot_at: Optional[str] = None            # 拍摄/修改时间(若数据源能免下载拿到)
    raw: dict[str, Any] = field(default_factory=dict)  # 后端原始返回(调试/扩展)

    @property
    def record_id(self) -> Optional[str]:
        """内容身份(16 hex)。优先 sha1(本地),其次 content_md5(网盘);都没有则 None。"""
        return derive_record_id(sha1=self.sha1, md5=self.content_md5)


class Source(ABC):
    """数据源统一接口。

    read:  list / stat / frames  —— 抽象,必须实现。
    write: rename / mkdir / collect / put_sidecar —— 默认 NotImplementedError(Phase 2/3)。
    """

    #: 数据源标识,写入 Record.source(如 "local" / "baidu")。
    name: str = "base"

    # ---- 读(Phase 1)----
    @abstractmethod
    def list(self, root: str) -> Iterable[SourceItem]:
        """递归枚举 root 下的素材条目。

        网盘实现负责翻页 + 补 md5/size/thumbs;本地实现走 os.walk + 探测。
        返回可迭代(允许惰性/生成器,便于大目录流式处理)。
        """
        ...

    @abstractmethod
    def stat(self, item: SourceItem) -> SourceItem:
        """补全单条元数据并返回(本地 ffprobe/EXIF;网盘 filemetas)。"""
        ...

    @abstractmethod
    def frames(self, item: SourceItem, dest_dir: Path, *, cap: int = 8) -> list[Path]:
        """取至多 cap 张关键帧到 dest_dir,返回帧文件路径列表。

        约定**不下整片**:视频走 HLS(streaming)按需抽帧;照片这类小文件可整下后本地抽。
        """
        ...

    # ---- 写(Phase 2/3;只读数据源可不实现)----
    def rename(self, item: SourceItem, new_name: str) -> bool:
        """把 item 改名为 new_name(仅文件名,不含目录)。返回是否成功。"""
        raise NotImplementedError(f"{self.name} 不支持 rename")

    def mkdir(self, path: str) -> str:
        """新建目录,返回其标识(本地路径或网盘 path/fs_id)。"""
        raise NotImplementedError(f"{self.name} 不支持 mkdir")

    def collect(self, items: list[SourceItem], dest_dir: str, *, move: bool = False) -> int:
        """把 items 归集到 dest_dir(默认 copy,move=True 时移动)。返回成功条数。"""
        raise NotImplementedError(f"{self.name} 不支持 collect")

    def put_sidecar(self, item: SourceItem, payload: dict[str, Any]) -> bool:
        """把旁车 JSON 写回数据源(网盘=上传)。本地由 05_store 直接写,无需经此。"""
        raise NotImplementedError(f"{self.name} 不支持 put_sidecar")

    def close(self) -> None:
        """释放资源(HTTP 会话等)。默认无操作。"""
        return None
