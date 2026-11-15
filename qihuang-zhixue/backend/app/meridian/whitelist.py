"""名称白名单：模型写出一个不存在的药，用户是看不出来的。

白名单的来源是 ``kg_entity`` 里已核准的实体（``kind='herb'`` 465 条中 451 条
``approved``，``formula`` 40 条），加 362 个穴位名。

**未命中不报错，进 ``pending_terms``。** 报错会让模型换一个更保守的说法从而丢掉
真实内容；静默接受则等于把错误直接交给用户。挂在中间才对：内容照收，但明确标注
"这个词没在校核名录里"，由人抽检。
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path
from typing import Any

from app.meridian import store

#: 归一化时要剥掉的东西：空白、全角括号里的炮制/别名说明。
_BRACKETS = re.compile(r"[（(][^）)]*[）)]")
_SPACES = re.compile(r"\s+")


def normalize(name: str) -> str:
    """``"黄芪（炙）"`` → ``"黄芪"``；``" 甘 草 "`` → ``"甘草"``。"""

    return _SPACES.sub("", _BRACKETS.sub("", name or "")).strip()


def load_approved_names(db_path: Path | str = store.DB_PATH) -> set[str]:
    """从知识图谱实体 + 穴位名录建白名单。"""

    names: set[str] = set()
    with closing(store.connect(Path(db_path))) as conn:
        try:
            rows = conn.execute(
                "SELECT name FROM kg_entity WHERE kind IN ('herb','formula','syndrome') AND status = 'approved'"
            ).fetchall()
            names.update(normalize(r[0]) for r in rows if r[0])
        except sqlite3.OperationalError:
            # 新库 / 裁剪过的库可能还没有 kg_entity。此时白名单退化成"只有穴位名"，
            # 待核项会变多——这是安全的方向（宁可多标，不要把没核过的名字当成核过）。
            pass
        for row in conn.execute("SELECT name FROM meridian_acupoint"):
            names.add(normalize(row[0]))
    names.discard("")
    return names


def load_terms_by_kind(kind: str, db_path: Path | str = store.DB_PATH) -> list[str]:
    with closing(store.connect(Path(db_path))) as conn:
        rows = conn.execute(
            "SELECT name FROM kg_entity WHERE kind = ? AND status = 'approved' ORDER BY name", (kind,)
        ).fetchall()
    return [normalize(r[0]) for r in rows if r[0]]


def check_terms(terms: Iterable[str], approved: set[str] | None = None, *, db_path: Path | str = store.DB_PATH) -> dict[str, Any]:
    """把一组名称分成「命中」与「待核」两堆。"""

    known = approved if approved is not None else load_approved_names(db_path)
    hit: list[str] = []
    pending: list[str] = []
    for raw in terms:
        name = normalize(raw)
        if not name:
            continue
        (hit if name in known else pending).append(name)
    return {"hit": hit, "pending": pending}


def scan_text(text: str, approved: set[str] | None = None, *, db_path: Path | str = store.DB_PATH) -> list[str]:
    """从一段中文里抽出**命中白名单**的名称，按最长优先。

    用途是给节标题找锚点（"辛温解表药 · 麻黄、桂枝"）。抽不到是正常的：
    节标题本来多是分类名（"发散风寒药"），不是药名。
    """

    known = approved if approved is not None else load_approved_names(db_path)
    if not known:
        return []
    ordered = sorted(known, key=len, reverse=True)
    found: list[str] = []
    buf = normalize(text)
    for name in ordered:
        if len(name) >= 2 and name in buf:
            found.append(name)
            buf = buf.replace(name, "\u0000")
    return found


def pending_of_sections(sections: Iterable[Any], approved: set[str] | None = None, *, db_path: Path | str = store.DB_PATH) -> list[str]:
    """收集若干小节的 ``sample_terms`` 里没通过白名单的名称（去重、保序）。"""

    known = approved if approved is not None else load_approved_names(db_path)
    seen: dict[str, None] = {}
    for sec in sections:
        for raw in getattr(sec, "sample_terms", []) or []:
            name = normalize(raw)
            if name and name not in known:
                seen.setdefault(name, None)
    return list(seen)


__all__ = [
    "check_terms",
    "load_approved_names",
    "load_terms_by_kind",
    "normalize",
    "pending_of_sections",
    "scan_text",
]
