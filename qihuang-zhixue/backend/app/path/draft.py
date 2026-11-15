"""两段式草稿：先谈「学哪些章」，谈定再展开「逐点怎么学」（计划 §7）。

三条不变量，缺一条这个模块就不该存在：

1. **草稿不碰进度**。``PATCH`` 只改 payload、只追加 ``status='draft'`` 的行；
   ``_seed_plan_states`` 只在确认明细时调用一次。否则用户取消后地图上会留
   一片幽灵点位——那些点没学、也不在计划里，却长得像在学。
2. **确认 = 追加**。确认不改草稿行，而是再追加一条 ``status='active'``、
   ``parent_version`` 指向草稿的行。历史一个字节都不动，``reorder`` /
   ``complete_node`` / ``_next_node_id`` 全都不用改。
3. **非法编辑整单拒**。顺序违反 strict 先修边时返回 409 且**草稿不变**：
   部分应用会让用户看到一份"看起来改了、其实只改了一半"的计划。

部分成功的情况（点了个不存在的节点、掌握度越界）走 ``rejected[]``，不整单拒——
这些是用户手滑，不是计划不成立。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.meridian.attach import attach_plan
from app.path.clarify import compose_request, planning_from_goal, subject_hints
from app.path.plan_store import (
    find_reusable_l1,
    get_plan_version,
    is_draft_confirmed,
    latest_draft,
    save_plan_version,
)
from app.path.planner import PathAgent
from app.path.schema import NodePlan, PathPlan
from app.path.service import _seed_plan_states, load_planning_context
from app.path.store import DB_PATH
from app.path.subjects import subject_of, title_of
from app.profile.schema import TimeBudget

#: 一次明细草稿最多展开几个方向。展开一个要 5–20 秒（多域逐个 ask 模型），
#: 19 个方向一次全展开会把请求挂死在网关上。剩下的进 ``pending_domains``。
MAX_DETAIL_DOMAINS = 4


class DraftConflict(ValueError):
    """草稿操作被拒。``code`` 直接给前端分支用，不要让她去抠字符串。"""

    def __init__(self, code: str, message: str, *, detail: Any = None, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        self.status = status


class DraftOp(BaseModel):
    """一条编辑动作。八个 op 共用一个模型，避免前端要认八种 schema。"""

    model_config = ConfigDict(extra="forbid")

    op: Literal[
        "reorder",
        "remove",
        "include",
        "set_budget",
        "set_node",
        "drop_node",
        "add_custom",
        "defer",
    ]
    ids: list[str] = Field(default_factory=list)
    domain_id: str = ""
    after: str = ""
    total_days: int | None = Field(default=None, ge=1, le=3650)
    daily_minutes: int | None = Field(default=None, ge=1, le=600)
    node_id: str = ""
    target_mastery: float | None = Field(default=None, ge=0.0, le=1.0)
    resource_hint: str = Field("", max_length=60)
    text: str = Field("", max_length=200)
    reason: str = Field("", max_length=60)

    @model_validator(mode="after")
    def _required_fields(self) -> "DraftOp":
        if self.op in {"reorder", "remove"} and not self.ids:
            raise ValueError(f"{self.op} 需要 ids")
        if self.op == "include" and not self.domain_id:
            raise ValueError("include 需要 domain_id")
        if self.op in {"set_node", "drop_node", "defer"} and not self.node_id:
            raise ValueError(f"{self.op} 需要 node_id")
        if self.op == "add_custom" and not self.text.strip():
            raise ValueError("add_custom 需要 text")
        return self


class DraftPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ops: list[DraftOp] = Field(min_length=1, max_length=50)


# --------------------------------------------------------------------------- 生成草稿


def _name_of(agent: PathAgent, domain_id: str) -> str:
    node = agent._nodes.get(domain_id)  # noqa: SLF001 - 同一模块族，不另开查询
    return node.name if node is not None else domain_id


def _exclusion_reasons(proposal: dict[str, Any] | None) -> dict[str, str]:
    """模型说过为什么砍这个方向就照抄；没说就写一句中性的，不替它编理由。

    三处来源都要读：学科级 ``subjects[].decision="skip"``（整科跳过）、
    章节级 ``skip_chapters``、旧形状 ``coverage.exclude``。少读一处，
    "为什么没有中药学"就会因为模型换了个字段而突然没有答案。
    """

    if not proposal:
        return {}
    raw = proposal.get("raw") or {}
    reasons: dict[str, str] = {}
    subject_reason: dict[str, str] = {}
    for item in raw.get("subjects") or []:
        if not isinstance(item, dict) or item.get("decision") != "skip":
            continue
        subject_id = str(item.get("subject_id") or "")
        reason = str(item.get("reason") or "").strip()
        if subject_id and reason:
            subject_reason[subject_id] = reason
    exempt = {str(item) for item in (proposal.get("exempt_subjects") or [])}
    for domain_id in proposal.get("exempt_domains") or []:
        subject_id = subject_of(str(domain_id))
        if subject_id not in exempt:
            continue
        reasons[str(domain_id)] = subject_reason.get(subject_id) or "按「已有基础」整科跳过"
    for item in raw.get("skip_chapters") or []:
        if not isinstance(item, dict):
            continue
        domain_id = str(item.get("domain_id") or "")
        if item.get("reason"):
            reasons.setdefault(domain_id, str(item["reason"]).strip())
    for item in raw.get("skip_units") or []:
        if not isinstance(item, dict):
            continue
        unit_id = str(item.get("unit_id") or "")
        if unit_id and item.get("reason"):
            reasons.setdefault(unit_id, str(item["reason"]).strip())
    for item in (raw.get("coverage") or {}).get("exclude") or []:
        if not isinstance(item, dict):
            continue
        domain_id = str(item.get("domain_id") or "")
        if domain_id and item.get("reason"):
            reasons.setdefault(domain_id, str(item["reason"]).strip())
    return reasons


def _rejected_rows(proposal: dict[str, Any] | None) -> list[dict[str, str]]:
    if not proposal:
        return []
    return [
        {
            "code": str(item.get("code") or ""),
            "target": str(item.get("target") or ""),
            "message": str(item.get("message") or ""),
        }
        for item in (proposal.get("rejected") or [])
        if isinstance(item, dict)
    ]


def _stage_rows(
    agent: PathAgent,
    plan: PathPlan,
    *,
    skip_units: set[str] | None = None,
) -> list[dict[str, Any]]:
    """把 L1 的「章序列」摊成「单元序列」。

    **这是用户看到的那张安排表**。以前一行一章，于是总论一行顶掉教材前七章、
    "解表药"一行顶掉发散风寒药/发散风热药两节。粒度由 ``units`` 决定
    （小节 > 节 > 章，逐级下降），域之间的先后仍由 L1 决定——模型谈的是
    学哪几门课、哪几章，**章内部怎么切是教材事实，不该让模型再猜一遍**。
    """

    skipped = set(skip_units or ())
    rows: list[dict[str, Any]] = []
    for stage in sorted(plan.stages, key=lambda item: item.order):
        for unit in agent.units_of(stage.domain_id):
            # 章还在学、但这一节不学（模型按单元砍的那一档）。注意**不能**把它
            # 当成"已掌握"：跳过 ≠ 会，只是这次不排。
            if unit.id in skipped:
                continue
            rows.append(_unit_row(agent, unit, len(rows) + 1, stage.order_locked, stage.reason))
    return rows


def _unit_row(
    agent: PathAgent,
    unit: Any,
    order: int,
    order_locked: bool,
    reason: str,
) -> dict[str, Any]:
    """一行安排表。**标题只写节/小节名，不拼章名。**

    章的信息单独放在 ``chapter_title``：界面用分组表达层级，标签里再拼一遍
    "解表药·发散风寒药"既啰嗦又让用户以为那是一节的名字。
    """

    row = unit.to_row()
    row.update(
        {
            "order": order,
            "order_locked": bool(order_locked),
            # 估时按**这一行的点集**算，不按整章：一行只含一节时，
            # 章级分钟数会让用户以为"这一节要 2046 分钟"。
            "estimated_minutes": agent.estimate_points_minutes(unit.point_ids),
            "reason": reason,
        }
    )
    return row


def _rows_from_dump(agent: PathAgent, stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从已落库的 L1 payload 重建骨架行（复用时走这条）。

    **两种 payload 要分开处理**，混在一起就是灾难：

    * **单元行**（带 ``unit_id``／``stage_id``）——已经是教材粒度，一行就是一个单元。
      按行复用，一行都不增不减：用户上一版砍掉的节必须保持被砍。
    * **章行**（只有 ``domain_id``）——老 payload 一行一章。只取它选中的域与先后，
      单元按教材层级重新展开。

    以前不分，一律按 ``domain_id`` 展开：单元级 payload 里"中药学总论"那 18 行
    各自再展开整域的 18 个单元，于是同一节在安排表里出现 18 次
    （实测 293 行 → 1649 行，界面上是"每节重复 18 遍"）。
    """

    rows: list[dict[str, Any]] = []
    expanded_domains: set[str] = set()
    emitted: set[str] = set()
    for stage in sorted(stages, key=lambda item: int(item.get("order") or 0)):
        domain_id = str(stage.get("domain_id") or "")
        unit_id = str(stage.get("unit_id") or stage.get("stage_id") or "")
        unit = agent.curriculum.unit(unit_id) if unit_id else None
        if unit is not None:
            # 已经存坏的旧 payload 里同一单元可能连着出现 N 次：按第一次的位置留一行，
            # 别原样再抄一遍（那等于把"每节重复 18 遍"永久固化下去）。
            if unit.id in emitted:
                continue
            emitted.add(unit.id)
            rows.append(
                _unit_row(
                    agent,
                    unit,
                    len(rows) + 1,
                    bool(stage.get("order_locked")),
                    str(stage.get("reason") or ""),
                )
            )
            expanded_domains.add(unit.domain_id)
            continue
        if not domain_id or domain_id in expanded_domains:
            continue
        expanded_domains.add(domain_id)
        for item in agent.units_of(domain_id):
            if item.id in emitted:
                continue
            emitted.add(item.id)
            rows.append(
                _unit_row(
                    agent,
                    item,
                    len(rows) + 1,
                    bool(stage.get("order_locked")),
                    str(stage.get("reason") or ""),
                )
            )
    return rows


def _excluded_rows(
    agent: PathAgent,
    included: set[str],
    proposal: dict[str, Any] | None,
    *,
    scope_domain_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """没进路线的单元，逐条列出来让用户能加回。

    ``included`` 是**单元 id**，不是域名。用域名判断会让"只砍了某一节"变成
    "整章都不在排除列表里"，用户回头找不到那一节。
    """

    reasons = _exclusion_reasons(proposal)
    rows: list[dict[str, Any]] = []
    for domain_id in scope_domain_ids or []:
        for unit in agent.units_of(domain_id):
            if unit.id in included:
                continue
            row = unit.to_row()
            row.update(
                {
                    "estimated_minutes": agent.estimate_points_minutes(unit.point_ids),
                    "why": (
                        reasons.get(unit.id)
                        or reasons.get(domain_id)
                        or "按画像、先修顺序与时间预算跳过；想学随时加回"
                    ),
                }
            )
            rows.append(row)
    return rows


def _budget_of(context: Any, time_budget: TimeBudget | None) -> TimeBudget | None:
    return time_budget or context.time_budget or context.planning_params.time_budget


#: 「为什么学、什么时候要考」的翻译住在 ``clarify``：``service`` 与这里两条入口
#: 都要用，而本模块反过来 import ``service``。判定口径只能有一份。



async def create_skeleton_draft(
    user_id: str,
    *,
    db_path: Path = DB_PATH,
    time_budget: TimeBudget | None = None,
    reuse: bool = True,
    goal: str = "",
    answers: dict[str, str] | None = None,
    llm_call: Any = None,
) -> dict[str, Any]:
    """生成骨架草稿。**不落 active 行、不写进度。**

    ``answers`` 是向导追问那一轮的答案（期限/范围/起点）。它和原话一起拼成
    ``request_text``：模型读这一段，关键词表也读这一段。只把答案放进结构化字段
    是不够的——用户答了"3 个月内要考"，路线还按"兴趣优先"排，等于没问。
    """

    request_text = compose_request(goal, answers)
    context = load_planning_context(
        user_id, db_path=db_path, time_budget=time_budget, request_text=request_text
    )
    if request_text:
        # 「考研」「兴趣了解」是取舍信号，不能只进摘要：它决定 breadth/pace/depth，
        # 也进 input_hash —— 同一份画像换个目标就该是另一份计划。
        context = context.model_copy(update={"planning_params": planning_from_goal(request_text, context.planning_params)})
    # 范围是**硬边界**：用户点名"中药学"就只排中药学，不靠模型自觉。
    # 从 request_text 里认学科（原话 + 追问答案都在这段里），认不出就全图参与。
    agent = PathAgent(context, subjects=subject_hints(request_text), llm_call=llm_call)
    scope_domain_ids = list(agent.proposal_scope().domain_ids)

    # 同画像同图 = 同一份取舍。已经有过一版非 fallback 的 L1 就照它出草稿：
    # 再问一遍模型只会得到漂移，而用户会以为自己两次看到的不是同一份计划。
    cached = find_reusable_l1(user_id, agent.input_hash, db_path=db_path) if reuse else None
    if cached is not None:
        cached_payload = cached["payload"]
        stages = _rows_from_dump(agent, cached_payload.get("stages") or [])
        proposal = cached_payload.get("proposal")
        payload = _skeleton_payload(
            agent,
            stages=stages,
            proposal=proposal,
            suggestion=str(cached_payload.get("suggestion") or ""),
            fallback=bool(cached_payload.get("fallback")),
            model_meta=cached_payload.get("model_meta"),
            input_snapshot=cached_payload.get("input_snapshot") or agent.input_snapshot,
            budget=_dump_budget(_budget_of(context, time_budget)),
            goal=request_text,
            scope_domain_ids=scope_domain_ids,
            scope_subjects=list(agent.subjects),
            reused_from=cached["version"],
        )
    else:
        result = await agent.plan_l1()
        payload = _skeleton_payload(
            agent,
            stages=_stage_rows(
                agent,
                result.plan,
                skip_units=set(((result.proposal or {}).get("skipped_units") or {})),
            ),
            proposal=result.proposal,
            suggestion=result.suggestion,
            fallback=result.fallback,
            model_meta=result.model_meta,
            input_snapshot=agent.input_snapshot,
            budget=_dump_budget(_budget_of(context, time_budget)),
            goal=request_text,
            scope_domain_ids=scope_domain_ids,
            scope_subjects=list(agent.subjects),
        )
    version = save_plan_version(
        user_id=user_id,
        plan_kind="l1",
        payload=payload,
        fallback=bool(payload["fallback"]),
        db_path=db_path,
        status="draft",
        stage="skeleton",
    )
    return {"version": version, **payload}


def _skeleton_payload(
    agent: PathAgent,
    *,
    stages: list[dict[str, Any]],
    proposal: dict[str, Any] | None,
    suggestion: str,
    fallback: bool,
    model_meta: dict[str, Any] | None,
    input_snapshot: dict[str, Any] | None,
    budget: dict[str, int] | None,
    goal: str,
    scope_domain_ids: list[str],
    scope_subjects: list[str] | None = None,
    reused_from: int | None = None,
) -> dict[str, Any]:
    """骨架草稿的 payload。两条来源（复用 / 新排）共用一处拼装。

    复用与新排以前各写一份 40 行字典，差异只在 ``reused_from``——结果是一边加了
    字段另一边忘加，用户"换一版"时界面少一块信息。现在只有一份。
    """

    included_units = {str(row.get("stage_id") or row.get("domain_id")) for row in stages}
    payload: dict[str, Any] = {
        "status": "draft",
        "stage": "skeleton",
        "fallback": bool(fallback),
        "suggestion": suggestion,
        "stages": stages,
        "nodes": [],
        # 传**全部**在范围内的域：`_excluded_rows` 自己跳过已选单元，
        # 所以"整章砍掉"和"只砍其中一节"都能正确落在排除列表里。
        "excluded": _excluded_rows(agent, included_units, proposal, scope_domain_ids=scope_domain_ids),
        "rejected": _rejected_rows(proposal),
        # 整科跳过的学科与理由：向导要能解释"为什么这份路线里没有中药学"。
        # 只放在 proposal 里等于让前端去翻模型原始 JSON，那是最容易走样的
        # 一处——模型换个字段名，用户就看到"什么都没说"。
        # 本次规划的学科范围：空 = 六门全参与。向导用它渲染"只排中药学"，
        # 而不是让用户从 265 节里自己推断"范围还是没生效"。
        "scope_subjects": [
            {"subject_id": item, "name": title_of(item)} for item in (scope_subjects or [])
        ],
        "exempt_subjects": [
            {"subject_id": item, "name": title_of(item)}
            for item in (proposal or {}).get("exempt_subjects", [])
        ],
        "skipped_chapters": (proposal or {}).get("skipped_chapters", {}),
        "proposal": proposal,
        "model_meta": model_meta,
        "input_snapshot": input_snapshot,
        "input_hash": agent.input_hash,
        "budget": budget,
        "goal": goal,
        "edits": [],
        "totals": _totals(stages),
    }
    if reused_from:
        payload["reused_from"] = reused_from
    return payload


def _totals(stages: list[dict[str, Any]]) -> dict[str, int]:
    """安排表头那几个数：**节数 / 章数 / 知识点 / 分钟**。

    以前表头写"22 章"，而 22 是"域节点个数"——总论那个容器占了 1，前七章没被算进去，
    各论一个章又只算 1。现在章数按 ``chapter_title`` 去重，节数按行数，两个数都对得上。
    """

    chapters = {(str(row.get("domain_id") or ""), str(row.get("chapter_title") or "")) for row in stages}
    return {
        "units": len(stages),
        "chapters": len(chapters),
        "points": sum(int(row.get("point_count") or 0) for row in stages),
        "minutes": sum(int(row.get("estimated_minutes") or 0) for row in stages),
    }


def _dump_budget(budget: TimeBudget | None) -> dict[str, int] | None:
    if budget is None:
        return None
    return {"total_days": int(budget.total_days), "daily_minutes": int(budget.daily_minutes)}


# --------------------------------------------------------------------------- 编辑草稿


def _positions(ids: list[str]) -> dict[str, int]:
    return {node_id: index for index, node_id in enumerate(ids)}


def _order_violations(pairs: list[tuple[str, str]], order: list[str]) -> list[dict[str, str]]:
    positions = _positions(order)
    out: list[dict[str, str]] = []
    for source, target in pairs:
        if source in positions and target in positions and positions[source] > positions[target]:
            out.append({"from": source, "to": target})
    return out


def _with_edit(payload: dict[str, Any], op: DraftOp) -> dict[str, Any]:
    return {**payload, "edits": [*payload.get("edits", []), op.model_dump(mode="json", exclude_none=True)]}


def _find(items: list[dict[str, Any]], key: str, value: str) -> dict[str, Any] | None:
    return next((item for item in items if str(item.get(key) or "") == value), None)


def row_key(row: dict[str, Any]) -> str:
    """安排表一行的标识：``stage_id``（新）→ ``unit_id``（并存期）→ ``domain_id``（老草稿）。

    三档回退不是为了"兼容好看"：库里躺着的历史草稿、已确认的 l1 payload、
    以及一串调用方都还拿着 ``domain_id`` 当行键。没有这条回退，
    用户打开半年前的路线再点一下 "上移"，前端就会收到"这个方向不在草稿里"。
    """

    for key in ("stage_id", "unit_id", "domain_id"):
        value = str(row.get(key) or "")
        if value:
            return value
    return ""


def _find_row(rows: list[dict[str, Any]], value: str) -> dict[str, Any] | None:
    """按行标识找一行；找不到时**再按域名找第一行**（老接口只传域名）。"""

    for row in rows:
        if row_key(row) == value:
            return row
    for row in rows:
        if str(row.get("domain_id") or "") == value:
            return row
    return None


def apply_ops(
    payload: dict[str, Any],
    ops: list[DraftOp],
    *,
    agent: PathAgent,
    stage: str,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """把 ops 应用到一份 payload 上，返回 (新 payload, rejected[])。

    幂等：同一个 op 应用两次结果一致（包括 include 一个已经在里面的方向）。
    """

    current: dict[str, Any] = {
        **payload,
        "stages": [dict(item) for item in payload.get("stages", [])],
        "nodes": [dict(item) for item in payload.get("nodes", [])],
        "excluded": [dict(item) for item in payload.get("excluded", [])],
    }
    rejected: list[dict[str, str]] = []

    for op in ops:
        if op.op == "reorder":
            if stage == "skeleton":
                known = {row_key(item) for item in current["stages"]}
                ids = list(op.ids)
                if set(ids) != known or len(ids) != len(known):
                    # 兼容"只给域名"的老调用：**必须仍然覆盖全部域**。
                    # 否则一个只写了一个域名的 reorder 会被当成合法请求，
                    # 结果是"其余章节凭空消失且没有任何报错"。
                    by_domain = {str(item.get("domain_id") or ""): row_key(item) for item in current["stages"]}
                    mapped = [by_domain.get(item, "") for item in ids]
                    if (
                        set(ids) != set(by_domain)
                        or not all(mapped)
                        or len(set(mapped)) != len(mapped)
                    ):
                        raise DraftConflict("path.unknown_target", "reorder 必须列出全部阶段，且不能重复")
                    ids = mapped
                by_id = {row_key(item): item for item in current["stages"]}
                current["stages"] = [{**by_id[row_id], "order": index} for index, row_id in enumerate(ids, 1)]
            else:
                known = {item["node_id"] for item in current["nodes"]}
                unknown = [node_id for node_id in op.ids if node_id not in known]
                if unknown:
                    rejected.extend({"code": "path.unknown_target", "target": node_id, "message": "这个节点不在草稿里"} for node_id in unknown)
                ordered = [node_id for node_id in op.ids if node_id in known]
                ordered += [node_id for node_id in known if node_id not in ordered]
                by_id = {item["node_id"]: item for item in current["nodes"]}
                current["nodes"] = [by_id[node_id] for node_id in ordered]
        elif op.op == "remove":
            for item_id in op.ids:
                if stage == "skeleton":
                    row = _find_row(current["stages"], item_id)
                    if row is None:
                        rejected.append({"code": "path.unknown_target", "target": item_id, "message": "这个方向不在草稿里"})
                        continue
                    key = row_key(row)
                    current["stages"] = [item for item in current["stages"] if row_key(item) != key]
                    if _find_row(current["excluded"], item_id) is None:
                        current["excluded"].append({**row, "why": "你移除了这一节；随时可以加回"})
                else:
                    row = _find(current["nodes"], "node_id", item_id)
                    if row is None:
                        rejected.append({"code": "path.unknown_target", "target": item_id, "message": "这个知识点不在草稿里"})
                        continue
                    current["nodes"] = [item for item in current["nodes"] if item["node_id"] != item_id]
        elif op.op == "include":
            target = op.domain_id
            row = _find(current["excluded"], "stage_id", target) or _find(current["excluded"], "unit_id", target)
            if row is None:
                # 只给了域名：把该域**所有**被排除的单元一起加回（用户点的是"整章加回"）
                picked = [item for item in current["excluded"] if str(item.get("domain_id") or "") == target]
                if not picked:
                    rejected.append({"code": "path.unknown_target", "target": target, "message": "没有可加回的章节"})
                    continue
                keys = {row_key(item) for item in picked}
                current["excluded"] = [item for item in current["excluded"] if row_key(item) not in keys]
                rows_to_add = [{key: value for key, value in item.items() if key != "why"} for item in picked]
            else:
                key = row_key(row)
                current["excluded"] = [item for item in current["excluded"] if row_key(item) != key]
                rows_to_add = [{key: value for key, value in row.items() if key != "why"}]
            cursor = next(
                (i + 1 for i, item in enumerate(current["stages"]) if row_key(item) == op.after or str(item.get("domain_id") or "") == op.after),
                len(current["stages"]),
            )
            for offset, added in enumerate(rows_to_add):
                current["stages"].insert(cursor + offset, added)
        elif op.op == "set_budget":
            budget = dict(current.get("budget") or {})
            if op.total_days is not None:
                budget["total_days"] = op.total_days
            if op.daily_minutes is not None:
                budget["daily_minutes"] = op.daily_minutes
            current["budget"] = budget or None
        elif op.op == "set_node":
            row = _find(current["nodes"], "node_id", op.node_id)
            if row is None:
                rejected.append({"code": "path.unknown_target", "target": op.node_id, "message": "这个知识点不在草稿里"})
            else:
                if op.target_mastery is not None:
                    row["target_mastery"] = round(float(op.target_mastery), 2)
                if op.resource_hint:
                    row["resource_hint"] = op.resource_hint
        elif op.op == "drop_node":
            row = _find(current["nodes"], "node_id", op.node_id)
            if row is None:
                rejected.append({"code": "path.unknown_target", "target": op.node_id, "message": "这个知识点不在草稿里"})
            else:
                current["nodes"] = [item for item in current["nodes"] if item["node_id"] != op.node_id]
                current.setdefault("dropped", []).append({"node_id": op.node_id, "name": row.get("name", ""), "why": "你略过了这一点；随时可以加回"})
        elif op.op == "defer":
            row = _find(current["nodes"], "node_id", op.node_id)
            if row is None:
                rejected.append({"code": "path.unknown_target", "target": op.node_id, "message": "这个知识点不在草稿里"})
            else:
                row["deferred"] = True
                row["defer_reason"] = op.reason or "以后再学"
        elif op.op == "add_custom":
            node_id = f"custom:{uuid4().hex[:12]}"
            domain_id = op.domain_id or (current["stages"][0]["domain_id"] if current["stages"] else "")
            current["nodes"].append(
                {
                    "node_id": node_id,
                    "name": op.text.strip(),
                    "stage": _stage_of(current, domain_id),
                    "seq": len(current["nodes"]) + 1,
                    "target_mastery": 0.8,
                    "resource_hint": "自定义主题学习",
                    "highlight": "normal",
                    "calibration": False,
                    "reason": "你加的一条",
                    "custom": True,
                    "domain_id": domain_id,
                }
            )

    return _resequence(current, stage=stage), rejected


def _stage_of(payload: dict[str, Any], domain_id: str) -> int:
    row = _find(payload.get("stages", []), "domain_id", domain_id)
    return int(row["order"]) if row else 1


def _stage_of_unit(payload: dict[str, Any], unit_id: str, *, domain_id: str = "") -> int:
    """节点落位用的阶段号 = **该单元那一行的 order**。

    以前是"域那一行"，一行一章时两者等价；现在一章可能拆成好几行，
    拿域号会让同一章的几节共用同一个 stage，``_next_node_id`` 按 (stage, seq)
    找下一关时会跳过整节。
    """

    row = _find_row(payload.get("stages", []), unit_id) if unit_id else None
    if row is None and domain_id:
        row = _find(payload.get("stages", []), "domain_id", domain_id)
    return int(row["order"]) if row else 1


def _resequence(payload: dict[str, Any], *, stage: str) -> dict[str, Any]:
    for index, row in enumerate(payload.get("stages", []), 1):
        row["order"] = index
    for index, row in enumerate(payload.get("nodes", []), 1):
        row["seq"] = index
    if stage == "skeleton":
        # 用户改完顺序后，表头的"共 N 节 / M 章 / 多少分钟"要跟着变，
        # 否则他会看到一张 12 节的表写着"共 22 章"。
        payload["totals"] = _totals(payload.get("stages", []))
    payload["edited"] = True
    return payload


def _unique_domains(rows: list[dict[str, Any]]) -> list[str]:
    """按**首次出现**去重成域序列。

    校验先修边只能按域判（边是域之间的），而一行现在是一节；直接把行序列丢进去
    会让 ``_positions`` 只记住某一行，判出来的位置是错的。同一章的几节聚在一起时，
    首次出现的位置就是整章的位置。
    """

    out: list[str] = []
    for row in rows:
        domain_id = str(row.get("domain_id") or "")
        if domain_id and domain_id not in out:
            out.append(domain_id)
    return out


def _validate(payload: dict[str, Any], *, agent: PathAgent, stage: str) -> None:
    """整单校验。不合法就抛 —— 草稿保持原样。"""

    if stage == "skeleton":
        order = _unique_domains(payload.get("stages", []))
        violations = _order_violations(agent.strict_domain_pairs(), order)
        if violations:
            raise DraftConflict(
                "path.strict_conflict",
                _conflict_message(agent, violations, order),
                detail={"violations": violations, "order": order},
            )
        # 砍掉一个仍被下游 strict 依赖的方向 = 计划不成立，不能静默通过
        included = set(order)
        dropped_strict = [
            (source, target)
            for source, target in agent.strict_domain_pairs()
            if target in included and source not in included
        ]
        if dropped_strict:
            detail = [
                {
                    "from": agent._nodes[source].name if source in agent._nodes else source,  # noqa: SLF001
                    "to": agent._nodes[target].name if target in agent._nodes else target,  # noqa: SLF001
                }
                for source, target in dropped_strict
            ]
            raise DraftConflict(
                "path.strict_conflict",
                f"删掉的方向是「{detail[0]['to']}」的先修，要么一起留、要么一起删",
                detail={"violations": [{"from": source, "to": target} for source, target in dropped_strict], "pairs": detail},
            )
    else:
        node_ids = [item["node_id"] for item in payload.get("nodes", [])]
        pairs: list[tuple[str, str]] = []
        seen: set[str] = set()
        for item in payload.get("stages", []):
            domain_id = str(item.get("domain_id") or "")
            if domain_id and domain_id not in seen:
                seen.add(domain_id)
                pairs.extend(agent.strict_point_pairs(domain_id))
        violations = _order_violations(pairs, node_ids)
        if violations:
            raise DraftConflict(
                "path.strict_conflict",
                _conflict_message(agent, violations, node_ids),
                detail={"violations": violations},
            )


def _conflict_message(agent: PathAgent, violations: list[dict[str, str]], order: list[str]) -> str:
    head = violations[0]
    names = getattr(agent, "_nodes", {})
    source = names[head["from"]].name if head["from"] in names else head["from"]
    target = names[head["to"]].name if head["to"] in names else head["to"]
    return f"「{source}」必须先于「{target}」——这条是先修关系，换个顺序就学不动了"


def patch_draft(user_id: str, version: int, ops: list[DraftOp], *, db_path: Path = DB_PATH) -> dict[str, Any]:
    item = get_plan_version(user_id, version, db_path)
    if item is None or item["status"] != "draft":
        raise DraftConflict("path.unknown_target", f"没有可编辑的第 {version} 版草稿", status=404)

    payload = item["payload"]
    stage = item["stage"] or "skeleton"
    context = load_planning_context(user_id, db_path=db_path)
    agent = PathAgent(context)
    edited, rejected = apply_ops(payload, ops, agent=agent, stage=stage)
    _validate(edited, agent=agent, stage=stage)
    edited["rejected"] = rejected
    new_version = save_plan_version(
        user_id=user_id,
        plan_kind="l1" if stage == "skeleton" else "l2",
        payload=edited,
        fallback=bool(edited.get("fallback")),
        db_path=db_path,
        status="draft",
        stage=stage,
        parent_version=version,
    )
    return {"version": new_version, "parent_version": version, **edited}


# --------------------------------------------------------------------------- 明细草稿


async def create_detail_draft(
    user_id: str,
    *,
    db_path: Path = DB_PATH,
    version: int | None = None,
    domains: list[str] | None = None,
    time_budget: TimeBudget | None = None,
    llm_call: Any = None,
    expand_all: bool = False,
) -> dict[str, Any]:
    """骨架确认后的逐点明细。

    ``domains`` 只收**已选中的单元/章**（不传 = 骨架里全部）。``expand_all=False``
    时一次最多展开 ``MAX_DETAIL_DOMAINS`` 个域，其余进 ``pending_domains``——
    这条是给"分批展开"的历史接口留的。新的向导只有一步确认，走 ``expand_all=True``：
    展开本身是确定性的（不调模型），一次全展开比让用户分四次点快得多。

    **只展开被选中的单元**：用户砍掉"发散风热药"之后，明细里不该再出现它的 13 味药。
    以前按整章展开，砍掉的节会从明细里爬回来。
    """

    skeleton = get_plan_version(user_id, version, db_path) if version is not None else latest_draft(user_id, db_path, stage="skeleton")
    if skeleton is None:
        raise DraftConflict("path.unknown_target", "先出骨架草稿，再展开明细", status=404)

    skeleton_payload = skeleton["payload"]
    rows = list(skeleton_payload.get("stages", []))
    wanted = {str(item) for item in (domains or []) if item}
    selected = [
        row
        for row in rows
        if not wanted
        or row_key(row) in wanted
        or str(row.get("domain_id") or "") in wanted
    ]
    if not selected:
        raise DraftConflict("path.unknown_target", "这几个方向都不在骨架里")
    # 按域归并，但保留**行**的顺序：一行是一节，节序就是用户确认过的顺序。
    domain_order: list[str] = []
    rows_by_domain: dict[str, list[dict[str, Any]]] = {}
    for row in selected:
        domain_id = str(row.get("domain_id") or "")
        if domain_id not in rows_by_domain:
            rows_by_domain[domain_id] = []
            domain_order.append(domain_id)
        rows_by_domain[domain_id].append(row)
    if expand_all:
        batch, pending = domain_order, []
    else:
        batch, pending = domain_order[:MAX_DETAIL_DOMAINS], domain_order[MAX_DETAIL_DOMAINS:]

    context = load_planning_context(user_id, db_path=db_path, time_budget=time_budget or _budget_from(skeleton_payload))
    agent = PathAgent(context, llm_call=llm_call)
    overrides = ((skeleton_payload.get("proposal") or {}).get("overrides")) or {}

    nodes: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    explored: list[str] = []
    deferred: list[dict[str, str]] = []
    # 明细列表要显示中文名。``NodePlan`` 里没有 name 字段（规划器只认 id），
    # 不在这里补，用户确认的就是 39 个 `b01.s1.p1`——他没法判断该不该砍。
    names = {str(node.id): str(node.name) for node in context.knowledge_nodes}
    for domain_id in batch:
        try:
            expanded = await agent.expand_domain(domain_id, overrides=overrides)
        except Exception as exc:  # noqa: BLE001 - 一个方向排不出来不该拖垮整份明细
            # 其余方向是好的，用户可以先确认它们；但失败的原因必须带回去，
            # 否则界面会表现为「确认了却少了两章」，而且没有任何解释。
            deferred.append({"domain_id": domain_id, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        explored.append(domain_id)
        skipped.extend({**item, "domain_id": domain_id} for item in expanded.skipped)
        by_point = {str(node.node_id): node for node in expanded.plan.nodes}
        for skeleton_row in rows_by_domain[domain_id]:
            wanted_points = [str(item) for item in skeleton_row.get("point_ids") or []]
            if not wanted_points:
                # 老草稿没有 point_ids：退回"整章"，行为与改造前一致
                wanted_points = [str(node.node_id) for node in expanded.plan.nodes]
            stage_no = int(skeleton_row.get("order") or 1)
            for point_id in wanted_points:
                node = by_point.get(point_id)
                if node is None:
                    continue
                payload_row = node.model_dump(mode="json")
                payload_row["name"] = names.get(point_id, "")
                payload_row.update(
                    {
                        "stage": stage_no,
                        "domain_id": domain_id,
                        "unit_id": row_key(skeleton_row),
                        "unit_title": str(skeleton_row.get("unit_title") or skeleton_row.get("name") or ""),
                        "chapter_title": str(skeleton_row.get("chapter_title") or ""),
                    }
                )
                nodes.append(payload_row)
    if not explored:
        raise DraftConflict(
            "path.unknown_target",
            "这几个方向都没排出来：" + "；".join(item["reason"] for item in deferred),
            status=409,
        )
    # ``expand_domain`` 的 seq 是**域内**序号（每个域都从 1 开始）。明细草稿跨域，
    # 必须重排成全局连续：``_next_node_id`` 是靠 (stage, seq) 找下一关的，
    # 两个域都有 seq=1 时它会跳过整个方向。
    for index, node in enumerate(nodes, 1):
        node["seq"] = index

    payload = {
        "status": "draft",
        "stage": "detail",
        "parent_version": skeleton["version"],
        "fallback": bool(skeleton_payload.get("fallback")),
        "suggestion": skeleton_payload.get("suggestion", ""),
        "stages": [row for row in selected if str(row.get("domain_id") or "") in explored],
        "nodes": nodes,
        "excluded": list(skeleton_payload.get("excluded", [])),
        "rejected": list(skeleton_payload.get("rejected", [])),
        "pending_domains": pending + [item["domain_id"] for item in deferred],
        "deferred": deferred,
        # 模型判过"已有基础、免修"的点。它们不在 nodes 里，但确认时要写进
        # user_path_state（否则用户会看到"我会的东西地图上一片漆黑"）。
        "skipped": skipped,
        "skeleton": {
            "version": skeleton["version"],
            "stages": rows,
            "proposal": skeleton_payload.get("proposal"),
            "model_meta": skeleton_payload.get("model_meta"),
            "input_snapshot": skeleton_payload.get("input_snapshot"),
            "input_hash": skeleton_payload.get("input_hash"),
            # 呈现层要"共几天 / 每天多久"和用户原话来决定文体，缺了它只能猜。
            "budget": skeleton_payload.get("budget"),
            "goal": skeleton_payload.get("goal"),
            "suggestion": skeleton_payload.get("suggestion"),
            "excluded": skeleton_payload.get("excluded", []),
        },
        "budget": skeleton_payload.get("budget"),
        "attach": attach_plan(nodes, db_path=db_path),
        "edits": [],
    }
    new_version = save_plan_version(
        user_id=user_id,
        plan_kind="l2",
        payload=payload,
        fallback=payload["fallback"],
        db_path=db_path,
        status="draft",
        stage="detail",
        parent_version=skeleton["version"],
    )
    return {"version": new_version, **payload}


def _budget_from(payload: dict[str, Any]) -> TimeBudget | None:
    budget = payload.get("budget")
    if not isinstance(budget, dict) or not budget.get("total_days"):
        return None
    return TimeBudget(
        total_days=int(budget["total_days"]),
        daily_minutes=int(budget.get("daily_minutes") or 90),
    )


# --------------------------------------------------------------------------- 确认


def confirm_draft(user_id: str, version: int, *, db_path: Path = DB_PATH) -> dict[str, Any]:
    """确认草稿：追加 active 行。**草稿行本身不变。**"""

    item = get_plan_version(user_id, version, db_path)
    if item is None or item["status"] != "draft":
        raise DraftConflict("path.unknown_target", f"没有第 {version} 版待确认草稿", status=404)
    if is_draft_confirmed(user_id, version, db_path):
        # 连点两下"就用这个顺序"不该确认出两条路线
        raise DraftConflict("path.unknown_target", f"第 {version} 版已经确认过，请重新排或改目标", status=409)

    payload = item["payload"]
    stage = item["stage"] or "skeleton"
    confirmed_at = datetime.now(UTC).isoformat()
    result: dict[str, Any] = {"stage": stage, "draft_version": version, "confirmed_at": confirmed_at, "versions": []}

    if stage == "detail":
        skeleton = payload.get("skeleton") or {}
        l1_payload = {
            "stages": skeleton.get("stages", []),
            "nodes": [],
            "suggestion": payload.get("suggestion", ""),
            "fallback": bool(payload.get("fallback")),
            "proposal": skeleton.get("proposal"),
            "model_meta": skeleton.get("model_meta"),
            "input_snapshot": skeleton.get("input_snapshot"),
            "verification_issues": [],
            "verify_attempts": 0,
            "confirmed_from": version,
            # 表头与地图路线条要用的汇总：节数/章数/点数/分钟。
            # 只在 l1 上算一次，地图每帧都读得到，不必逐次遍历节点。
            "totals": payload.get("totals") or _totals(skeleton.get("stages", [])),
            # 呈现与"为什么这么排"要读的三样，跟着 l1 走：一张路线说明书
            # 缺了预算就只能写"共 291 节"，用户拿不走。
            "budget": skeleton.get("budget"),
            "goal": skeleton.get("goal") or payload.get("goal") or "",
            "excluded": skeleton.get("excluded") or payload.get("excluded") or [],
        }
        # 整份明细的节点按域分装进各自的 l2 行；l1 只留阶段与取舍依据。
        # `current_path` 读 l1 时**只取 stages**，节点全部来自 l2——所以这里
        # 补 nodes 是无效的，别以为多写一份能"保住"内容。
        #
        # l1 这一行的 ``parent_version`` 指向**骨架草稿**：它承载的是取舍（stages
        # 和 proposal），这些正是骨架那一版的内容。l2 行才指向明细草稿。
        result["versions"].append(
            save_plan_version(
                user_id=user_id,
                plan_kind="l1",
                payload=l1_payload,
                fallback=bool(payload.get("fallback")),
                db_path=db_path,
                status="active",
                parent_version=int(skeleton.get("version") or version),
                confirmed_at=confirmed_at,
            )
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for node in payload.get("nodes", []):
            grouped.setdefault(str(node.get("domain_id") or ""), []).append(node)
        for domain_id, domain_nodes in grouped.items():
            l2_payload = {
                "domain_id": domain_id,
                "stages": [row for row in payload.get("stages", []) if row.get("domain_id") == domain_id],
                "nodes": domain_nodes,
                "fallback": bool(payload.get("fallback")),
                "verification_issues": [],
                "verify_attempts": 0,
                "confirmed_from": version,
            }
            result["versions"].append(
                save_plan_version(
                    user_id=user_id,
                    plan_kind="l2",
                    domain_id=domain_id,
                    payload=l2_payload,
                    fallback=bool(payload.get("fallback")),
                    db_path=db_path,
                    status="active",
                    parent_version=version,
                    confirmed_at=confirmed_at,
                )
            )
        _seed_plan_states(
            user_id,
            {"nodes": payload.get("nodes", [])},
            db_path=db_path,
            skipped=list(payload.get("skipped", [])),
        )
        result["attach"] = payload.get("attach") or attach_plan(payload.get("nodes", []), db_path=db_path)
    else:
        l1_payload = {
            "stages": payload.get("stages", []),
            "nodes": [],
            "suggestion": payload.get("suggestion", ""),
            "fallback": bool(payload.get("fallback")),
            "proposal": payload.get("proposal"),
            "model_meta": payload.get("model_meta"),
            "input_snapshot": payload.get("input_snapshot"),
            "verification_issues": [],
            "verify_attempts": 0,
            "confirmed_from": version,
            "totals": payload.get("totals") or _totals(payload.get("stages", [])),
        }
        result["versions"].append(
            save_plan_version(
                user_id=user_id,
                plan_kind="l1",
                payload=l1_payload,
                fallback=bool(payload.get("fallback")),
                db_path=db_path,
                status="active",
                parent_version=version,
                confirmed_at=confirmed_at,
            )
        )

    result["version"] = result["versions"][-1]
    return result


def draft_view(user_id: str, version: int | None = None, *, db_path: Path = DB_PATH) -> dict[str, Any]:
    item = get_plan_version(user_id, version, db_path) if version is not None else latest_draft(user_id, db_path)
    if item is None:
        raise DraftConflict("path.unknown_target", "还没有草稿", status=404)
    return {"version": item["version"], "stage": item["stage"], **item["payload"]}


async def finalize_draft(
    user_id: str,
    version: int,
    *,
    db_path: Path = DB_PATH,
    llm_call: Any = None,
) -> dict[str, Any]:
    """一步定稿：确认取舍 → 展开全部单元 → 确认明细。

    **为什么合成一步**：用户确认的是"学这些、不学那些"，这才是需要他判断的事。
    逐点明细与目标掌握度是**系统的活**——把 0.70~0.95 的滑杆摆给一个还没开始学
    中药学的人，他能做出的唯一判断是"这俩数字看起来差不多"。以前那一屏因此变成
    了"点下一步"的仪式，而不是决策。

    三步之间没有新的用户输入，所以不必拆成三次交互；任何一步失败都抛出去，
    不会留下"确认了一半"的路线（确认本身是追加行，失败即没有 active 行）。
    """

    skeleton = get_plan_version(user_id, version, db_path)
    if skeleton is None or skeleton["status"] != "draft":
        raise DraftConflict("path.unknown_target", f"没有第 {version} 版待确认草稿", status=404)

    first = confirm_draft(user_id, version, db_path=db_path)
    if (skeleton["stage"] or "skeleton") == "detail":
        # 已经是明细草稿：一步确认就够了，不要再展开一遍。
        return {**first, "finalized": True, "detail_version": version}

    detail = await create_detail_draft(
        user_id,
        db_path=db_path,
        version=version,
        time_budget=None,
        llm_call=llm_call,
        expand_all=True,
    )
    second = confirm_draft(user_id, detail["version"], db_path=db_path)
    return {
        **second,
        "finalized": True,
        "skeleton_version": version,
        "detail_version": detail["version"],
        "versions": [*first["versions"], *second["versions"]],
        "node_count": len(detail.get("nodes") or []),
        "totals": _totals(detail.get("stages") or []),
        "attach": detail.get("attach"),
    }


__all__ = [
    "DraftConflict",
    "DraftOp",
    "DraftPatchRequest",
    "MAX_DETAIL_DOMAINS",
    "apply_ops",
    "confirm_draft",
    "create_detail_draft",
    "create_skeleton_draft",
    "draft_view",
    "finalize_draft",
    "patch_draft",
    "row_key",
]
