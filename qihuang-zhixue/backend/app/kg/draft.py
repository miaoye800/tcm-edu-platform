"""教材抽取草稿（P4.4）：状态机 + 签字记录 + 抽样台账。

## 为什么要有这一层

``data/herbs/*.json`` 是 source of truth，直接进处方审查。抽取是**自动**的：
正则改错一个字符，442 味药会一次性全写坏，而且没人会立刻发现——处方审查
只会安静地开始报「未收录」，看起来像教材没写。

所以中间必须有一道闸：抽取结果先落 ``herb_draft``，``status='approved'``
才允许写 JSON。这道闸的价值不在「多一张表」，而在两件事：

1. **可追溯**：谁在什么时候把哪条从 ``pending`` 改成了 ``approved``。
2. **可反推**：``herb_draft_patch`` 记下人工把哪个字段从什么改成了什么。
   只有终态的话，看一百条 ``approved`` 也推不出「闸门到底漏了什么」。

## 状态机

::

    pending ──┬─→ auto_passed ──┬─→ approved
              │                 └─→ rejected
              ├─→ conflict ─────┬─→ approved
              │                 └─→ rejected
              ├─→ approved
              └─→ rejected

- ``pending``：机器判不了，等人看（自检有疑点）
- ``auto_passed``：门禁全过、自检零疑点，等人抽样确认
- ``conflict``：与已有数据冲突（重名/字段差异过大），**必须**人裁定
- ``approved`` / ``rejected``：终态，不再变

``approved`` / ``rejected`` 是终态这件事很重要：如果允许「已入库」退回
``pending``，一次误操作就能让已经进了处方审查的药突然消失。

## 抽样为什么也要落库

计划要求「首批 50 味 100% 人工；之后毒性药 100%、flag 100%、其余 20% 随机」
和「每批随机 3 味反向核对」。这些规则如果只写在文档里，实际执行就是
「复核页上有个随机抽查按钮，点不点随缘」——系统不知道谁看过什么，
于是拦不住任何人把没复核的批次入库。

落成 ``herb_draft_sample`` 之后，「抽满没有」变成一次 SQL 查询，
``promote`` 可以直接拿它当门槛。
"""

from __future__ import annotations

import json
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.kg.schema import ensure_schema
from app.profile.store import DB_PATH, connect

#: 五个状态，顺序即流转方向（前三个是非终态）。
STATUSES = ("pending", "auto_passed", "conflict", "approved", "rejected")

#: 终态：进去就不再出来。
TERMINAL = frozenset({"approved", "rejected"})

#: 允许的流转。``approved``/``rejected`` 空元组 = 终态。
ALLOWED: dict[str, tuple[str, ...]] = {
    "pending": ("auto_passed", "conflict", "approved", "rejected"),
    "auto_passed": ("conflict", "approved", "rejected"),
    "conflict": ("approved", "rejected"),
    "approved": (),
    "rejected": (),
}

#: 抽样的四种理由，对应计划里的四条规则。
SAMPLE_REASONS = ("first50", "toxic", "flag", "random20", "reverse")


class IllegalTransition(ValueError):
    """试图把草稿从终态改回去，或跳到不存在的状态。"""


@dataclass(frozen=True)
class Draft:
    id: int
    herb_name: str
    block_id: str
    page_start: int | None
    page_end: int | None
    payload: dict[str, Any]
    gate_report: dict[str, Any]
    status: str
    model: str
    prompt_hash: str
    created_at: str
    reviewed_by: str | None
    reviewed_at: str | None
    review_note: str | None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL


@dataclass(frozen=True)
class Patch:
    id: int
    draft_id: int
    field: str
    old_value: str
    new_value: str
    reason: str
    created_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _draft_from_row(row: Sequence[Any]) -> Draft:
    def _load(raw: Any) -> dict[str, Any]:
        try:
            value = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    return Draft(
        id=int(row[0]),
        herb_name=str(row[1]),
        block_id=str(row[2] or ""),
        page_start=row[3],
        page_end=row[4],
        payload=_load(row[5]),
        gate_report=_load(row[6]),
        status=str(row[7]),
        model=str(row[8] or ""),
        prompt_hash=str(row[9] or ""),
        created_at=str(row[10]),
        reviewed_by=row[11],
        reviewed_at=row[12],
        review_note=row[13],
    )


_SELECT = (
    "SELECT id, herb_name, block_id, page_start, page_end, payload_json,"
    " gate_report_json, status, model, prompt_hash, created_at,"
    " reviewed_by, reviewed_at, review_note FROM herb_draft"
)


def status_from_gate(gate_report: dict[str, Any], has_conflict: bool = False) -> str:
    """门禁报告 → 草稿初态。

    优先级：有 fail → ``rejected``（门禁已经判死了，不用浪费人）；
    有 conflict → ``conflict``（必须人裁定）；有 warn → ``pending``（等人看）；
    什么都没有 → ``auto_passed``（等抽样确认）。

    **``pending`` 不是「坏」**：它只表示「机器不敢替人签字」。
    """

    hits = gate_report.get("hits") or []
    if any(hit.get("severity") == "fail" for hit in hits):
        return "rejected"
    if has_conflict or gate_report.get("worst") == "conflict":
        return "conflict"
    if hits:
        return "pending"
    return "auto_passed"


def upsert_draft(
    herb_name: str,
    payload: dict[str, Any],
    *,
    block_id: str = "",
    page_start: int | None = None,
    page_end: int | None = None,
    gate_report: dict[str, Any] | None = None,
    model: str = "",
    prompt_hash: str = "",
    db_path: Path = DB_PATH,
) -> tuple[int, bool]:
    """写入或更新一条草稿。返回 ``(id, 是否新建)``。

    **已到终态的草稿不会被重跑覆盖。** 这是幂等性的关键：重跑抽取不该把
    人签过的字抹掉。要重判得先显式 ``reopen()``。
    """

    ensure_schema(db_path)
    gate_report = gate_report or {}
    status = status_from_gate(gate_report)
    now = _now()

    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT id, status FROM herb_draft WHERE herb_name=? AND block_id=?",
            (herb_name, block_id),
        ).fetchone()
        if row:
            draft_id, current = int(row[0]), str(row[1])
            if current in TERMINAL:
                return draft_id, False
            conn.execute(
                "UPDATE herb_draft SET page_start=?, page_end=?, payload_json=?,"
                " gate_report_json=?, status=?, model=?, prompt_hash=? WHERE id=?",
                (
                    page_start,
                    page_end,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(gate_report, ensure_ascii=False),
                    status,
                    model,
                    prompt_hash,
                    draft_id,
                ),
            )
            conn.commit()
            return draft_id, False

        cursor = conn.execute(
            "INSERT INTO herb_draft (herb_name, block_id, page_start, page_end,"
            " payload_json, gate_report_json, status, model, prompt_hash, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                herb_name,
                block_id,
                page_start,
                page_end,
                json.dumps(payload, ensure_ascii=False),
                json.dumps(gate_report, ensure_ascii=False),
                status,
                model,
                prompt_hash,
                now,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid or 0), True


def get_draft(draft_id: int, db_path: Path = DB_PATH) -> Draft | None:
    ensure_schema(db_path)
    with closing(connect(db_path)) as conn:
        row = conn.execute(f"{_SELECT} WHERE id=?", (draft_id,)).fetchone()
    return _draft_from_row(row) if row else None


def get_by_name(herb_name: str, db_path: Path = DB_PATH) -> Draft | None:
    ensure_schema(db_path)
    with closing(connect(db_path)) as conn:
        row = conn.execute(f"{_SELECT} WHERE herb_name=? ORDER BY id LIMIT 1", (herb_name,)).fetchone()
    return _draft_from_row(row) if row else None


def list_drafts(
    status: str | None = None, db_path: Path = DB_PATH, limit: int | None = None
) -> list[Draft]:
    ensure_schema(db_path)
    sql = _SELECT
    params: list[Any] = []
    if status:
        sql += " WHERE status=?"
        params.append(status)
    sql += " ORDER BY id"
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    with closing(connect(db_path)) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_draft_from_row(row) for row in rows]


def transition(
    draft_id: int,
    to_status: str,
    *,
    reviewer: str = "",
    note: str = "",
    db_path: Path = DB_PATH,
) -> str:
    """流转到 ``to_status``，返回新状态。非法流转抛 :class:`IllegalTransition`。

    非法流转**不静默通过**：``approved → pending`` 如果能走通，一次误点就能
    让已经进了处方审查的药凭空消失，而且没有任何痕迹说明是谁干的。
    """

    if to_status not in STATUSES:
        raise IllegalTransition(f"未知状态 {to_status!r}，只认 {STATUSES}")
    current = get_draft(draft_id, db_path)
    if current is None:
        raise IllegalTransition(f"草稿 {draft_id} 不存在")
    if to_status == current.status:
        return current.status
    if to_status not in ALLOWED[current.status]:
        raise IllegalTransition(
            f"草稿 {draft_id}（{current.herb_name}）不能从 {current.status} 变成 {to_status}"
        )

    with closing(connect(db_path)) as conn:
        conn.execute(
            "UPDATE herb_draft SET status=?, reviewed_by=?, reviewed_at=?,"
            " review_note=COALESCE(NULLIF(?, ''), review_note) WHERE id=?",
            (to_status, reviewer or None, _now(), note, draft_id),
        )
        conn.commit()
    return to_status


def reopen(draft_id: int, db_path: Path = DB_PATH) -> str:
    """把终态草稿退回 ``pending``，供教材换版/抽取重做时用。

    单独开一个函数而不是放宽状态机：**退回必须是一次显式动作**，
    而且要留痕（写一条 patch 记录），不能顺手就发生。
    """

    current = get_draft(draft_id, db_path)
    if current is None:
        raise IllegalTransition(f"草稿 {draft_id} 不存在")
    if current.status not in TERMINAL:
        return current.status
    add_patch(draft_id, "status", current.status, "pending", "重开（reopen）", db_path=db_path)
    with closing(connect(db_path)) as conn:
        conn.execute(
            "UPDATE herb_draft SET status='pending', reviewed_by=NULL,"
            " reviewed_at=NULL WHERE id=?",
            (draft_id,),
        )
        conn.commit()
    return "pending"


def add_patch(
    draft_id: int,
    field: str,
    old_value: Any,
    new_value: Any,
    reason: str = "",
    db_path: Path = DB_PATH,
) -> int:
    """记一条人工修改。``old_value``/``new_value`` 不是字符串时按 JSON 存。"""

    ensure_schema(db_path)

    def _dump(value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    with closing(connect(db_path)) as conn:
        cursor = conn.execute(
            "INSERT INTO herb_draft_patch (draft_id, field, old_value, new_value,"
            " reason, created_at) VALUES (?,?,?,?,?,?)",
            (draft_id, field, _dump(old_value), _dump(new_value), reason, _now()),
        )
        conn.commit()
        return int(cursor.lastrowid or 0)


def patches(draft_id: int, db_path: Path = DB_PATH) -> list[Patch]:
    ensure_schema(db_path)
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT id, draft_id, field, old_value, new_value, reason, created_at"
            " FROM herb_draft_patch WHERE draft_id=? ORDER BY id",
            (draft_id,),
        ).fetchall()
    return [
        Patch(
            id=int(row[0]),
            draft_id=int(row[1]),
            field=str(row[2]),
            old_value=str(row[3]),
            new_value=str(row[4]),
            reason=str(row[5]),
            created_at=str(row[6]),
        )
        for row in rows
    ]


# ---------------------------------------------------------------- 抽样台账


def add_sample(
    draft_id: int, batch: str, reason: str, db_path: Path = DB_PATH
) -> bool:
    """登记一条待复核样本。已登记过则返回 ``False``（幂等）。"""

    if reason not in SAMPLE_REASONS:
        raise ValueError(f"未知抽样理由 {reason!r}，只认 {SAMPLE_REASONS}")
    ensure_schema(db_path)
    with closing(connect(db_path)) as conn:
        exists = conn.execute(
            "SELECT 1 FROM herb_draft_sample WHERE draft_id=? AND batch=? AND reason=?",
            (draft_id, batch, reason),
        ).fetchone()
        if exists:
            return False
        conn.execute(
            "INSERT INTO herb_draft_sample (draft_id, batch, reason) VALUES (?,?,?)",
            (draft_id, batch, reason),
        )
        conn.commit()
    return True


def set_sample_verdict(
    draft_id: int,
    batch: str,
    reason: str,
    verdict: str,
    *,
    note: str = "",
    reviewer: str = "",
    db_path: Path = DB_PATH,
) -> None:
    if verdict not in ("", "ok", "bad", "skip"):
        raise ValueError(f"未知结论 {verdict!r}")
    ensure_schema(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute(
            "UPDATE herb_draft_sample SET verdict=?, note=?, reviewed_by=?,"
            " reviewed_at=? WHERE draft_id=? AND batch=? AND reason=?",
            (verdict, note, reviewer or None, _now(), draft_id, batch, reason),
        )
        conn.commit()


def samples(batch: str | None = None, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    ensure_schema(db_path)
    sql = (
        "SELECT s.id, s.draft_id, d.herb_name, s.batch, s.reason, s.verdict,"
        " s.note, s.reviewed_by, s.reviewed_at"
        " FROM herb_draft_sample s JOIN herb_draft d ON d.id = s.draft_id"
    )
    params: list[Any] = []
    if batch:
        sql += " WHERE s.batch=?"
        params.append(batch)
    sql += " ORDER BY s.draft_id, s.reason"
    with closing(connect(db_path)) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            "id": int(row[0]),
            "draft_id": int(row[1]),
            "herb_name": str(row[2]),
            "batch": str(row[3]),
            "reason": str(row[4]),
            "verdict": str(row[5]),
            "note": str(row[6] or ""),
            "reviewed_by": row[7],
            "reviewed_at": row[8],
        }
        for row in rows
    ]


def pending_samples(batch: str | None = None, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    """还没看过的样本。promote 的门槛就是「这个列表为空」。"""

    return [item for item in samples(batch, db_path) if not item["verdict"]]


def drop_unseen_not_in(batch: str, pairs: list[tuple[int, str]], db_path: Path = DB_PATH) -> int:
    """删掉本批次里「计划外且还没看」的样本，返回删除条数。

    计划会变（换了种子、改了状态筛选口径）。旧计划留下的空样本如果不清理，
    台账里就会挂着一条「该看」其实不该看的药，而 ``pending_samples`` 会一直
    非空——``promote`` 永远被拦着，人还以为自己漏看了什么。

    **只删 ``verdict=''`` 的**：已经填过结论的是人工成果，删了就是抹掉证据。
    """

    ensure_schema(db_path)
    keep = {(int(draft_id), str(reason)) for draft_id, reason in pairs}
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT id, draft_id, reason FROM herb_draft_sample WHERE batch=? AND verdict=''",
            (batch,),
        ).fetchall()
        stale = [
            int(row[0])
            for row in rows
            if (int(row[1]), str(row[2])) not in keep
        ]
        for sample_id in stale:
            conn.execute("DELETE FROM herb_draft_sample WHERE id=?", (sample_id,))
        conn.commit()
    return len(stale)


def counts(db_path: Path = DB_PATH) -> dict[str, int]:
    ensure_schema(db_path)
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM herb_draft GROUP BY status"
        ).fetchall()
    out = {status: 0 for status in STATUSES}
    out.update({str(row[0]): int(row[1]) for row in rows})
    out["total"] = sum(out[status] for status in STATUSES)
    return out


def approved_names(db_path: Path = DB_PATH) -> set[str]:
    ensure_schema(db_path)
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT herb_name FROM herb_draft WHERE status='approved'"
        ).fetchall()
    return {str(row[0]) for row in rows}


def seed(rows: Iterable[dict[str, Any]], db_path: Path = DB_PATH) -> tuple[int, int]:
    """批量写入草稿，返回 ``(新建, 已存在)``。"""

    created = existing = 0
    for row in rows:
        _, is_new = upsert_draft(
            str(row["herb_name"]),
            row.get("payload") or {},
            block_id=str(row.get("block_id") or ""),
            page_start=row.get("page_start"),
            page_end=row.get("page_end"),
            gate_report=row.get("gate_report") or {},
            model=str(row.get("model") or ""),
            prompt_hash=str(row.get("prompt_hash") or ""),
            db_path=db_path,
        )
        created += int(is_new)
        existing += int(not is_new)
    return created, existing


__all__ = [
    "ALLOWED",
    "Draft",
    "IllegalTransition",
    "Patch",
    "SAMPLE_REASONS",
    "STATUSES",
    "TERMINAL",
    "add_patch",
    "add_sample",
    "approved_names",
    "counts",
    "drop_unseen_not_in",
    "get_by_name",
    "get_draft",
    "list_drafts",
    "patches",
    "pending_samples",
    "reopen",
    "samples",
    "seed",
    "set_sample_verdict",
    "status_from_gate",
    "transition",
    "upsert_draft",
]
