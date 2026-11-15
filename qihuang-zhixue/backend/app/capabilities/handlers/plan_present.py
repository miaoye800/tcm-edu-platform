"""``plan.present``：把已确认的路线导成导图或文稿，**不重新谋篇**。

为什么不是复用 ``mindmap.generate`` / ``doc.generate``：那两条会按自己的
问法判定重新组织内容（导图自己长树、文稿自己走七节骨架）。计划已经谈定了
结构，再猜一遍等于把用户确认过的取舍推翻。这里只做「翻译既定结构」。

能力是 ``query``：不留任务、不排队。计划的结构已经在库里，导一份快照是
毫秒级的；做成 job 只会让用户多等一次轮询。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.auth.models import AuthUser
from app.meridian.attach import attach_plan
from app.path.plan_store import get_plan_version, latest_draft, list_plan_versions
from app.path.present import ARTIFACT_SPEC, DOC_ARCHETYPES, DOC_ARCHETYPE_SPECS, MINDMAP_NODE_LIMIT, MINDMAP_SPEC, present_plan
from app.path.service import current_path
from app.path.subjects import title_of
from app.path.units import outline_for
from app.profile.store import DB_PATH

from ..errors import CapabilityError, ErrorCode
from ..spec import CapabilityResult, CapabilitySpec, ErrorSpec
from . import CapabilityInput

#: 呈现在对话里只给一句人话 + 一张卡。节点清单不搬进去——用户要的是打开看。
MAX_LINES = 6


class PlanPresentInput(CapabilityInput):
    version: int = Field(0, ge=0, description="哪一版计划；0 = 最近一版草稿，没有草稿则用当前生效路线")
    present: Literal["auto", "mindmap", "doc"] = Field("auto", description="体裁；auto = 按画像与规模确定性选择")
    structure: str = Field("", max_length=20, description="导图结构：logic-right / mind-both / org-down / fishbone；留空用默认")
    theme: str = Field("", max_length=20, description="配色：indigo / verdant / violet / inkCream")
    doc_archetype: str = Field(
        "",
        max_length=20,
        description="文稿体裁：route_guide（说明书）/ task_cards（任务卡）/ syllabus_map（考纲对照表）；留空按期限与规模选",
    )


SPEC = CapabilitySpec(
    name="plan.present",
    version="1.0",
    kind="query",
    title="把路线导成导图或文稿",
    one_line="把已经确认的学习路线整理成一张能点回地图的导图，或一份能打印的文稿",
    use_when=(
        "用户说「把这条路线画成图」「导出一份计划」「给我一份能打印的」",
        "刚确认完计划，想要一份能带走的材料",
    ),
    avoid_when=(
        "还没确认路线：先走 plan.create 或两段式向导",
        "只讲某个知识点：用 doc.generate / mindmap.generate，那是内容创作",
        "要看地图本身：打开 /path，不需要产物",
    ),
    triggers=("导成图", "导出计划", "打印", "路线图", "计划文档"),
    input_model=PlanPresentInput,
    output_shape={
        "ok": "bool",
        "kind": "str，mindmap | doc",
        "resource_id": "str",
        "open_url": "str，直接可打开",
        "title": "str",
        "reason": "str，为什么选这个体裁",
        "node_count": "int",
    },
    audiences=("agent", "human"),
    artifact=ARTIFACT_SPEC,
    errors=(
        ErrorSpec(ErrorCode.NOT_FOUND, 404, "还没有可导出的路线", "先确认一份计划"),
        ErrorSpec(ErrorCode.INVALID_INPUT, 422, "体裁参数不认识", "present 取 auto / mindmap / doc"),
    ),
    side_effects=("写一份只读快照到资源库（同一版本同一体裁只产一份）"),
    requires=("登录用户", "一份已确认或草稿态的学习计划"),
    cost="无模型调用",
    latency="通常 < 300 毫秒",
    examples=("把这条路线画成图", "导出一份能打印的学习计划"),
)


def _nodes_from_active(user_id: str, db_path: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    view = current_path(user_id, db_path=db_path)
    plan = view["plan"]
    stages = list(plan.get("stages") or [])
    nodes: list[dict[str, Any]] = []
    for domain_id, payload in (plan.get("domains") or {}).items():
        for node in payload.get("nodes") or []:
            nodes.append({**node, "domain_id": domain_id})
    nodes.sort(key=lambda item: (int(item.get("stage") or 0), int(item.get("seq") or 0)))
    return nodes, stages, int(plan.get("version") or 0)


def _nodes_from_draft(item: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    payload = item["payload"]
    stages = list(payload.get("stages") or payload.get("skeleton", {}).get("stages") or [])
    nodes = [
        {**node, "domain_id": node.get("domain_id") or ""}
        for node in payload.get("nodes") or []
    ]
    return nodes, stages, int(item["version"])


def _with_names(nodes: list[dict[str, Any]], db_path: Any) -> None:
    """给每个点补中文名，**就地改**。

    计划 payload 里的点是 ``NodePlan``（只有 id），早年确认的版本更没有 name。
    不补的话导图的叶子是一屏 ``b01.s1.p1``——用户打开一张"导图"，看到的全是编号。
    """

    missing = [node for node in nodes if not str(node.get("name") or "").strip()]
    if not missing:
        return
    from app.path.store import list_nodes

    names = {str(item["id"]): str(item.get("name") or "") for item in list_nodes(db_path=db_path)}
    for node in missing:
        node["name"] = names.get(str(node.get("node_id") or ""), "") or str(node.get("node_id") or "")


def _title_of(profile: Any) -> str:
    goal = str(getattr(getattr(profile, "goal", None), "normalized", "") or "").strip()
    return f"{goal}·学习路线" if goal else "我的学习路线"


def _decorate(nodes: list[dict[str, Any]], stages: list[dict[str, Any]]) -> None:
    """给每个点补上它落在**哪一节、哪一章**（就地改）。

    这件事不能交给呈现层去"从祖先链反推"：知识图里没有"学科"这一层，
    而总论那种篇容器里"章"根本不在祖先链上。确认过的 ``stages`` 才是权威。
    """

    by_order = {int(row.get("order") or 0): row for row in stages}
    by_domain: dict[str, dict[str, Any]] = {}
    for row in stages:
        by_domain.setdefault(str(row.get("domain_id") or ""), row)
    for node in nodes:
        if str(node.get("unit_title") or "").strip():
            continue
        row = by_order.get(int(node.get("stage") or 0)) or by_domain.get(str(node.get("domain_id") or ""))
        if row is None:
            # 同一域拆成多节时，按点集认领：点在哪一节，行就在哪一节。
            point_id = str(node.get("node_id") or "")
            row = next(
                (item for item in stages if point_id and point_id in [str(x) for x in (item.get("point_ids") or [])]),
                None,
            )
        if row is None:
            continue
        node.setdefault("unit_id", str(row.get("stage_id") or row.get("domain_id") or ""))
        node["unit_title"] = str(row.get("unit_title") or row.get("name") or "")
        node["chapter_title"] = str(row.get("chapter_title") or "")
        node["subject_name"] = str(row.get("subject_name") or "")


def _reasons_from(suggestion: str) -> list[str]:
    """把"小岐建议：A；B；C"拆成三条依据。**不重新编理由**，只做切分。"""

    text = str(suggestion or "").strip()
    if not text:
        return []
    text = text.split("：", 1)[-1] if text.startswith("小岐建议") else text
    parts = [item.strip(" 。；") for item in text.replace("；", ";").split(";")]
    return [item for item in parts if item][:4]


def _textbook_line(stages: list[dict[str, Any]]) -> str:
    """教材出处。用户要拿它对目录，所以写"书名 + 版次 + 主编"，不写"教材"。"""

    subject_ids: list[str] = []
    for row in stages:
        subject_id = str(row.get("subject_id") or "")
        if subject_id and subject_id not in subject_ids:
            subject_ids.append(subject_id)
    pieces: list[str] = []
    for subject_id in subject_ids:
        outline = outline_for(subject_id)
        if outline is None:
            continue
        book = outline.textbook or {}
        title = str(book.get("title") or outline.title or title_of(subject_id))
        edition = str(book.get("edition") or "")
        editor = str(book.get("editor") or "")
        pieces.append(f"《{title}》{edition}" + (f"（{editor}）" if editor else ""))
    return "；".join(pieces)


def _route_meta(version: int, user_id: str, db_path: Any) -> dict[str, Any]:
    """这一版路线的"上下文三件套"：预算 / 取舍理由 / 被跳过的单元。

    取不到就返回空，呈现层会退化成"按章节顺序推进"的说法——不能因为它缺了
    就把整份导出打回去。
    """

    def _from(payload: dict[str, Any]) -> dict[str, Any]:
        skeleton = payload.get("skeleton") or {}
        return {
            "budget": payload.get("budget") or skeleton.get("budget") or {},
            "suggestion": payload.get("suggestion") or skeleton.get("suggestion") or "",
            "excluded": payload.get("excluded") or skeleton.get("excluded") or [],
        }

    if version:
        item = get_plan_version(user_id, version, db_path)
        if item is not None:
            return _from(item["payload"])
    for item in reversed(list_plan_versions(user_id, db_path)):
        if item["plan_kind"] == "l1":
            return _from(item["payload"])
    return {"budget": {}, "suggestion": "", "excluded": []}


def _budget_of(version: int, user_id: str, db_path: Any) -> dict[str, Any]:
    return _route_meta(version, user_id, db_path)["budget"]


def _extras(
    *,
    stages: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """呈现层要的上下文：预算、取舍理由、被跳过的节、教材出处。

    每一样都来自用户确认过的那一版，**不在这里重新解释一遍**——
    "凭什么砍了三章"只能有一份答案，否则说明书、导图、对话里的说法会互相对不上。
    """

    return {
        "budget": meta.get("budget") or {},
        "reasons": _reasons_from(str(meta.get("suggestion") or "")),
        "skipped_units": meta.get("excluded") or [],
        "textbook": _textbook_line(stages),
        "totals": {"points": len(nodes)},
    }


def _is_route_version(item: dict[str, Any]) -> bool:
    """这一版是不是一条**完整路线**（可以整份导出）。

    确认明细会一次追加 1 条 l1 + 每个域 1 条 l2，而接口返回的 ``version`` 是
    ``versions[-1]``——也就是**最后一个域的 l2 行号**。拿它当"路线版本号"去导出，
    得到的只是那一章：实测确认 81 个点，导出来 2 个。

    所以版本号只能说"这份计划是指到哪的"，能不能整份导出要看它是什么行。
    """

    return item["plan_kind"] == "l1" or item["stage"] in ("skeleton", "detail")


def _nodes_from_confirmed_route(user_id: str, db_path: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """**这一次确认**的整条路线：同一批 active 行。

    不能直接用 `_nodes_from_active`：`current_path` 是按域累积的视图，它会把
    历史上确认过的所有域都并进来（实测本次确认 85 个点，累积视图 133 个）。
    地图要的是累积视图——用户走过的路都得在；但导出的应该是"我这次确认了什么"，
    否则用户砍掉的章会从旧快照里爬回来。

    "同一批"用 ``confirmed_at`` 认：一次确认在同一个事务里写完，时间戳一致。
    """

    items = [item for item in list_plan_versions(user_id, db_path) if item["status"] == "active"]
    if not items:
        return [], [], 0
    latest = items[-1]
    stamp = latest.get("confirmed_at")
    batch = [item for item in items if item.get("confirmed_at") == stamp] if stamp else []
    if not batch:
        # 早期数据没有 confirmed_at：退回"最后一条 l1 + 它之后的 l2"。
        l1_at = max((i for i, item in enumerate(items) if item["plan_kind"] == "l1"), default=-1)
        batch = items[l1_at:] if l1_at >= 0 else items

    nodes: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    version = 0
    for item in batch:
        payload = item["payload"]
        if item["plan_kind"] == "l1":
            stages = list(payload.get("stages") or stages)
            version = int(item["version"])
        elif item["plan_kind"] == "l2":
            domain_id = str(item["domain_id"] or payload.get("domain_id") or "")
            nodes.extend({**node, "domain_id": domain_id} for node in payload.get("nodes") or [])
    nodes.sort(key=lambda item: (int(item.get("stage") or 0), int(item.get("seq") or 0)))
    return nodes, stages, version or int(latest["version"])


async def handler(payload: PlanPresentInput, actor: AuthUser, *, invocation_id: str, db_path: Any = None) -> CapabilityResult:
    path = db_path or DB_PATH

    item: dict[str, Any] | None = None
    if payload.version:
        item = get_plan_version(actor.user_id, payload.version, path)
        if item is None:
            raise CapabilityError(ErrorCode.NOT_FOUND, f"没有第 {payload.version} 版计划", remedy="先确认一份计划")

    if item is not None and not _is_route_version(item):
        # 单章线路（l2）：只能导出这一章，这是它的全部内容，不是缺陷。
        nodes, stages, version = _nodes_from_draft(item)
    elif item is not None and item["status"] == "draft":
        # 草稿：导出用户正在看的那一版，而不是库里当前生效的那一版。
        nodes, stages, version = _nodes_from_draft(item)
    elif item is not None:
        # 已确认的整条路线（l1 active 行）。**必须导出"这一批"，不是累积视图**：
        # l1 自己的 `nodes` 按设计恒为空（`current_path` 读 l1 只取 stages），
        # 逐点在按域分装的 l2 行里；而 `_nodes_from_active` 会把历史确认过的域
        # 一起并进来，用户砍掉的章会从旧快照里爬回来。
        nodes, stages, version = _nodes_from_confirmed_route(actor.user_id, path)
    else:
        draft = latest_draft(actor.user_id, path)
        if draft is not None and draft["stage"] == "detail":
            nodes, stages, version = _nodes_from_draft(draft)
        else:
            # 没有待确认草稿：导"最近一次确认的那一批"。
            # 不用 `_nodes_from_active`——那是按域累积的地图视图，会把历史上
            # 确认过的所有域都并进来（实测本次 85 个点、累积视图 133 个）。
            nodes, stages, version = _nodes_from_confirmed_route(actor.user_id, path)
            if not nodes:
                nodes, stages, version = _nodes_from_active(actor.user_id, path)

    if not nodes:
        raise CapabilityError(ErrorCode.NOT_FOUND, "这条路线还没有展开到逐点，导出来只有空壳", remedy="先确认一份明细草稿")

    _with_names(nodes, path)
    _decorate(nodes, stages)

    attached = attach_plan(nodes, db_path=path)
    for node in nodes:
        info = attached["mapped"].get(str(node.get("node_id") or ""))
        if info:
            node["acupoint_name"] = info.get("acupoint_name") or ""
            node["acupoint_code"] = info.get("acupoint_code") or ""
            # 穴位挂的节名比 l1 行上的章名更细；但**不覆盖**已经由单位行填好的
            # chapter_title——map_section 里的章号来自教材，两者应一致，
            # 不一致时以路线为准（用户确认的是路线）。
            node.setdefault("chapter_title", info.get("chapter_title") or "")

    from app.profile.store import get_profile

    profile = get_profile(actor.user_id, path)
    title = _title_of(profile)
    result = present_plan(
        actor.user_id,
        nodes=nodes,
        title=title,
        stages=stages,
        prefer=payload.present,
        structure=payload.structure,
        theme=payload.theme,
        version=version,
        doc_archetype=payload.doc_archetype,
        extras=_extras(
            stages=stages,
            nodes=nodes,
            meta=_route_meta(version, actor.user_id, path),
        ),
        db_path=path,
    )

    body = {
        "ok": True,
        "capability": SPEC.name,
        "capability_version": SPEC.version,
        "invocation_id": invocation_id,
        "kind": result["kind"],
        # 可选的呈现形态：导图 + 三种文稿，每一种都自带四条说明
        # （应含内容 / 展现形式 / 创作原理 / 适用价值）。
        # 只给名字的话用户没有依据选，最后永远停在默认那一个。
        "archetype": result.get("archetype", ""),
        "archetype_label": result.get("archetype_label", ""),
        "presentation_options": [
            {"value": "mindmap", **MINDMAP_SPEC},
            *({"value": key, **DOC_ARCHETYPE_SPECS[key]} for key in DOC_ARCHETYPES),
        ],
        "resource_id": result["resource_id"],
        "open_url": result["open_url"],
        "title": result["title"],
        "reason": result["reason"],
        "node_count": result["node_count"],
        "reused": result["reused"],
        # 实际导出的是哪一版。调用方（向导 / 对话）复述或再点一次时用它，
        # 别自己去猜——"最近一版"在两次请求之间可能已经前进了。
        "plan_version": version,
        "unmapped": attached["unmapped"][:MAX_LINES],
        "summary": (
            f"导成{'导图' if result['kind'] == 'mindmap' else '文稿'}了："
            f"{result['node_count']} 个知识点，{result['reason']}。"
        ),
        # 快照型产物：不需要轮询，调用方直接拿这张卡。
        "artifact": {
            "kind": result["kind"],
            "resource_id": result["resource_id"],
            "open_url": result["open_url"],
            "title": result["title"],
            "instant": True,
        },
    }
    return CapabilityResult(http_status=200, body=body)


SPECS = ((SPEC, handler),)

__all__ = ["MINDMAP_NODE_LIMIT", "SPEC", "SPECS", "handler"]
