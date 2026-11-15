"""文稿的取证：这句话落在库里的哪门课、能拿到哪些可引用的材料。

升级前文稿只认「药材条目」——``retrieve_resource_docs`` 返回的是 ``data/herbs``
里的单味药，主题一换成「中药学怎么学」「英语四级加中药学」，召回到的东西要么
没有，要么不相干，于是 ``grounded=False``，提示词退到「按通用知识写作」。
用户看到的就是「生成出来的东西完全没用知识库」。

可库里明明有六门课的教材目录（``data/syllabus``）、442 味药、几百首方。
所以这里把取证拆成两路，都由 ``mindmap.catalog`` 这一份事实源回答：

* **结构**：这句话命中哪门课 / 哪一章 → 章节名、篇名就是材料，目录本身就是
  可引用的出处（「结构来自这本书的目录」是事实，不是编造）；
* **条目**：药名命中时，药材条目的原文片段照旧进引用池。

两路合起来才是「本库有什么」。判据只回答「有没有、叫什么」，
「该怎么组织」仍然交给模型读整句话（``docs/spec.py``）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.mindmap import catalog
from app.mindmap.catalog import Coverage

#: 引用池上限。再多只会把提示词撑满，正文里也用不到第 13 条。
MAX_SOURCES = 12
#: 单条原文给模型的长度上限。资源里存完整摘录，只在这里截断。
PROMPT_QUOTE_LIMIT = 300


@dataclass
class DocEvidence:
    """一次文稿写作能拿到的全部依据。"""

    topic: str = ""
    #: 这句话落在库里哪里。``None`` = 库里确实没有对应学科。
    coverage: Coverage | None = None
    #: 「本库有什么」的一段话，直接进提示词。
    library: str = ""
    #: 引用池：``{出处, 原文摘录, 定位}``，下标就是正文里的 ``[n]``。
    quotes: list[dict[str, str]] = field(default_factory=list)
    #: 覆盖不到的说明，会原样出现在方案卡与对话栏。
    notes: list[str] = field(default_factory=list)

    @property
    def grounded(self) -> bool:
        return bool(self.quotes)

    @property
    def subjects(self) -> tuple[str, ...]:
        return self.coverage.keys if self.coverage is not None else ()

    @property
    def chapter(self) -> str:
        info = self.coverage.chapter_info if self.coverage is not None else None
        return info.title if info is not None else ""

    def digest(self) -> dict[str, Any]:
        """给方案/落笔提示词用的摘要。不含整份目录，目录单独走 ``library``。"""

        return {
            "grounded": self.grounded,
            "subjects": list(self.subjects),
            "chapter": self.chapter,
            "sources": len(self.quotes),
            "notes": list(self.notes)[:3],
        }


def build_doc_evidence(topic: str, docs: list[Any] | None = None) -> DocEvidence:
    """把「主题 + 召回的条目」变成一份取证结果。任何一个环节失败都不编。"""

    text = str(topic or "")
    evidence = DocEvidence(topic=text)
    try:
        evidence.coverage = catalog.match(text)
    except Exception:  # noqa: BLE001 - 目录坏掉只是少一路依据，不该拦住写作
        evidence.coverage = None

    try:
        evidence.library = catalog.digest(evidence.coverage)
    except Exception:  # noqa: BLE001
        evidence.library = ""

    quotes: list[dict[str, str]] = []
    if evidence.coverage is not None:
        try:
            ref = catalog.source_ref(evidence.coverage)
            quote = str(ref.get("quote") or "").strip()
            if quote:
                quotes.append(
                    {
                        "出处": str(ref.get("name") or ""),
                        "原文摘录": quote[:PROMPT_QUOTE_LIMIT],
                        "定位": str(ref.get("ref") or ""),
                    }
                )
        except Exception:  # noqa: BLE001
            pass

    for doc in docs or []:
        if not isinstance(doc, dict):
            continue
        for source in doc.get("sources") or []:
            if not isinstance(source, dict):
                continue
            quote = str(source.get("quote") or "").strip()
            if not quote:
                continue
            row = {
                "出处": str(doc.get("name") or ""),
                "原文摘录": quote[:PROMPT_QUOTE_LIMIT],
                "定位": str(source.get("ref") or ""),
            }
            if row not in quotes:
                quotes.append(row)
            if len(quotes) >= MAX_SOURCES:
                break
        if len(quotes) >= MAX_SOURCES:
            break

    evidence.quotes = quotes[:MAX_SOURCES]
    if evidence.coverage is None:
        evidence.notes.append(f"主题不在本库教材范围内（本库覆盖：{catalog.titles_line()}），正文按通用知识写")
    elif not quotes:
        evidence.notes.append("本库有这门课，但没有取到可引用的原文片段，正文结构与术语仍按教材目录")
    return evidence


__all__ = ["DocEvidence", "build_doc_evidence", "MAX_SOURCES", "PROMPT_QUOTE_LIMIT"]
