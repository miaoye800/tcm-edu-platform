"""讲稿的取证：**库里有什么**，以及**素材库里有什么**。

两个问题都要答，且要答在同一次提示词里：

1. 「知识库里有什么」——教材目录（几门课、多少章、这句话落在哪一章）与药材条目。
   答不出来，模型就只能写通用套话，用户看到的就是「从来不用我们的库」。
2. 「素材库里有什么」——素材渠道清单。这是幻灯独有的：文稿不缺图，讲稿缺。
   答不出来，模型会写「配一张本草图」，而不知道本地只有五味饮片、馆藏能给明代彩绘、
   图标只能当符号不能占半版。

这里只做**事实汇总**，不做取舍：用哪一门课、用哪张图，由模型读整句话决定
（``ppt.spec.compose_deck_spec`` 与 ``ppt.agent_loop``）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.mindmap import catalog
from app.mindmap.catalog import Coverage

from . import channels


@dataclass(slots=True)
class DeckEvidence:
    """一次讲稿任务的库情。"""

    topic: str
    coverage: Coverage | None = None
    #: 「本库覆盖：……」，给用户看的一句话。
    library: str = ""
    #: 素材渠道摘要，直接进提示词的 assets_library。
    asset_menu: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def grounded(self) -> bool:
        return self.coverage is not None and not self.coverage.empty

    def digest(self) -> dict[str, Any]:
        """进方案 meta 的形状。只放能核对的数，不放大段文本。"""

        subjects = [subject.title for subject in self.coverage.subjects] if self.coverage else []
        chapter = self.coverage.chapter_info.title if self.coverage and self.coverage.chapter_info else ""
        return {
            "subjects": subjects,
            "chapter": chapter,
            "asset_channels": len(channels.CHANNELS),
            "notes": self.notes[:4],
        }


def asset_menu(*, limit: int = 12) -> str:
    """素材库的**使用说明**。**给模型读**，所以写清「能给什么、怎么取、能进哪个位」。

    只列 ready / verified 的渠道：blocked 的写进去只会让模型去试一条走不通的路。
    以前这里只写「id（标签，证据位可用）：能给什么；许可 X」，模型知道有图库却
    不知道**怎么要**——它写出「一张中药房背景」时不会想到要去哪个渠道、按什么词搜。
    """

    lines: list[str] = [
        "素材库按「用途」查，不按渠道名找。每条渠道都标了 purposes（适合什么画面）、"
        "how（怎么取）、slots（能不能当证据）。",
    ]
    for channel in channels.CHANNELS:
        if channel.status == "blocked":
            continue
        if len(lines) > limit:
            break
        slots = "证据位+装饰位" if channel.usable_for_evidence else "仅装饰位"
        purposes = "、".join(channel.purposes)
        scale = f"，{channel.scale}" if channel.scale else ""
        lines.append(
            f"- {channel.id}（{channel.label}｜{slots}）：{channel.what}。"
            f"取法：{channel.how}。适合：{purposes}。许可：{channel.license}{scale}。"
            + (f"注意：{channel.note}" if channel.note else "")
        )
    lines.append(
        "本地库还有一批程序生成的母题（纸纹、远山、角饰、界格、印章），"
        "按 motif_menu 里的 id 直接选，零成本、不需要检索。"
    )
    return "\n".join(lines)


def build_deck_evidence(topic: str, docs: list[Any] | None = None) -> DeckEvidence:
    """把「这句话落在库里哪儿」与「有哪些图库」合成一份取证结果。"""

    text = " ".join(str(topic or "").split())
    coverage = catalog.match(text) if text else None
    notes: list[str] = []
    library = catalog.digest(coverage)
    if coverage is None:
        # 库外主题也要说清库里有什么：用户才知道「没用上」是因为确实没有，
        # 而不是系统偷懒。但库里有这味药的条目时不能这么说——那是另一回事。
        if docs:
            notes.append(f"本库收有这味药的条目，但没有对应的教材章节；章节结构由模型提出")
        else:
            notes.append(f"主题未落在本库教材目录内；本库覆盖：{_titles()}")
    else:
        titles = "、".join(subject.title for subject in coverage.subjects)
        if coverage.chapter_info is not None:
            notes.append(f"这句话落在《{titles}》{coverage.chapter_info.title}")
        else:
            notes.append(f"这句话落在《{titles}》")
    menu = asset_menu()
    if docs:
        notes.append(f"检索到 {len(docs)} 条药材条目")
    return DeckEvidence(topic=text, coverage=coverage, library=library, asset_menu=menu, notes=notes)


def _titles() -> str:
    subjects = catalog.load_catalog().subjects
    return "、".join(subject.title for subject in subjects) or "（空）"
