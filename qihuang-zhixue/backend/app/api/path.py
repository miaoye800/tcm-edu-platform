"""Knowledge graph and three-level PathAgent APIs."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.path.clarify import clarify, tailor
from app.path.draft import (
    DraftConflict,
    DraftPatchRequest,
    confirm_draft,
    create_detail_draft,
    create_skeleton_draft,
    draft_view,
    finalize_draft,
    patch_draft,
)
from app.path.schema import AdjustRequest, AdjustResponse, CompleteNodeRequest, CustomNodeRequest, DeferRequest, ExpandRequest, ExpandResponse, PlanRequest, PlanResponse, ReorderRequest
from app.path.service import PathConflict, add_custom_node, complete_node, create_l1_plan, create_l2_plan, create_l3_adjustment, current_path, defer_node, reorder_path
from app.path.store import DB_PATH, list_edges, list_tree
from app.profile.schema import TimeBudget

router = APIRouter(prefix="/api/path", tags=["path"])


class DraftCreateRequest(BaseModel):
    """骨架草稿的输入。``reuse=False`` 对应向导里的「换一版试试」。"""

    model_config = ConfigDict(extra="forbid")

    time_budget: TimeBudget | None = None
    reuse: bool = True
    goal: str = Field("", max_length=600, description="为什么学、什么时候要考；会翻成 breadth/pace/depth")
    #: 追问那一轮的答案：deadline / scope / baseline → 选项 value 或自由文本。
    answers: dict[str, str] = Field(default_factory=dict)


class ClarifyRequest(BaseModel):
    """排之前的追问检查。只读：不落库、不调模型。"""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field("", max_length=600)
    answers: dict[str, str] = Field(default_factory=dict)


class DetailDraftRequest(BaseModel):
    """明细草稿的输入。``domains`` 留空 = 骨架里全部方向（服务端按批截断）。"""

    model_config = ConfigDict(extra="forbid")

    version: int | None = Field(default=None, ge=1)
    domains: list[str] | None = None
    time_budget: TimeBudget | None = None


def get_path_db_path() -> Path:
    """Path DB dependency; tests may replace it with a temporary SQLite file."""

    return DB_PATH


@router.get("/knowledge-tree")
async def read_knowledge_tree(
    domain_id: str | None = Query(default=None),
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_path_db_path),
) -> dict[str, object]:
    return {"domains": list_tree(domain_id=domain_id, db_path=db_path)}


@router.get("/prerequisites")
async def read_prerequisites(
    domain_id: str | None = Query(default=None),
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_path_db_path),
) -> dict[str, object]:
    return {"edges": list_edges(domain_id=domain_id, db_path=db_path)}


@router.post("/plan", response_model=PlanResponse)
async def plan_learning_path(
    payload: PlanRequest,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_path_db_path),
) -> PlanResponse:
    """Create and persist the complete L1 domain skeleton."""

    return await create_l1_plan(
        current_user.user_id,
        db_path=db_path,
        time_budget=payload.time_budget,
        reuse=not payload.replan,
        request_text=payload.request,
    )


@router.post("/expand", response_model=ExpandResponse)
async def expand_learning_domain(
    payload: ExpandRequest,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_path_db_path),
) -> ExpandResponse:
    """Lazily expand only the requested domain into point-level nodes."""

    try:
        return await create_l2_plan(
            current_user.user_id,
            payload.domain_id,
            db_path=db_path,
            time_budget=payload.time_budget,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/adjust", response_model=AdjustResponse)
async def adjust_learning_path(
    payload: AdjustRequest,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_path_db_path),
) -> AdjustResponse:
    """Persist one local L3 diff without reordering the whole path."""

    try:
        return create_l3_adjustment(
            current_user.user_id,
            payload.node_id,
            payload.bkt_snapshot,
            db_path=db_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/current")
async def read_current_path(current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_path_db_path)) -> dict[str, object]:
    return current_path(current_user.user_id, db_path=db_path)


# --------------------------------------------------------------------------- 两段式草稿


def _conflict(exc: DraftConflict) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message, "detail": exc.detail},
    )


@router.post("/plan/clarify")
async def clarify_plan(
    payload: ClarifyRequest,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_path_db_path),
) -> dict[str, object]:
    """排之前先问一轮：期限 / 范围 / 起点，缺哪项就出哪题。

    **问不问是确定性判断**（不落库、可复现）：只问真的缺的，一条不缺就一条不问。
    **怎么问交给模型**（``tailor``）：读用户原话，把题面改成"对着他这句话说"的样子，
    比如他说"想通过这学期中药学期末考试"，起点那题就会问"中药学你现在什么水平"，
    而不是通用的"这几门课你什么水平"。模型只能改措辞，不能增删问题或改选项值——
    模型挂了、超时、返回形状不对，一律退回确定性题面，用户不会卡在第一步。
    """

    try:
        context = load_planning_context(current_user.user_id, db_path=db_path, request_text=payload.goal)
    except Exception:  # noqa: BLE001 - 画像读不到也要能问问题，别把用户卡在第一步
        context = None
    result = clarify(payload.goal, context=context, answers=payload.answers)
    return (await tailor(result, payload.goal)).to_dict()


@router.post("/plan/draft")
async def create_plan_draft(
    payload: DraftCreateRequest,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_path_db_path),
) -> dict[str, object]:
    """生成**骨架**草稿。只追加 draft 行，不写进度、不进 ``current_path``。"""

    return await create_skeleton_draft(
        current_user.user_id,
        db_path=db_path,
        time_budget=payload.time_budget,
        reuse=payload.reuse,
        goal=payload.goal,
        answers=payload.answers,
    )


@router.get("/plan/draft")
async def read_latest_draft(
    version: int | None = Query(default=None, ge=1),
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_path_db_path),
) -> dict[str, object]:
    """向导刷新时取回当前草稿。没有草稿返回 404，让前端进第一步。"""

    try:
        return draft_view(current_user.user_id, version, db_path=db_path)
    except DraftConflict as exc:
        raise _conflict(exc) from exc


@router.patch("/plan/draft/{version}")
async def edit_plan_draft(
    version: int,
    payload: DraftPatchRequest,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_path_db_path),
) -> dict[str, object]:
    """统一编辑口。部分成功返回 ``rejected[]``；违反先修边返回 409 且草稿不变。"""

    try:
        return patch_draft(current_user.user_id, version, payload.ops, db_path=db_path)
    except DraftConflict as exc:
        raise _conflict(exc) from exc


@router.post("/plan/draft/{version}/confirm")
async def confirm_plan_draft(
    version: int,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_path_db_path),
) -> dict[str, object]:
    """确认骨架或明细：追加 active 行，草稿行本身不变。"""

    try:
        return confirm_draft(current_user.user_id, version, db_path=db_path)
    except DraftConflict as exc:
        raise _conflict(exc) from exc


@router.post("/plan/draft/{version}/finalize")
async def finalize_plan_draft(
    version: int,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_path_db_path),
) -> dict[str, object]:
    """一步定稿：确认取舍 → 展开全部单元 → 确认明细。

    向导只有三步（说目标 / 定取舍 / 确认），用户点「就用这个顺序」时调这里。
    不再有独立的"看细节 + 调掌握度"：那两屏要用户做的判断，他做不了也不需要做。
    """

    try:
        return await finalize_draft(current_user.user_id, version, db_path=db_path)
    except DraftConflict as exc:
        raise _conflict(exc) from exc


@router.post("/detail/draft")
async def create_detail_draft_endpoint(
    payload: DetailDraftRequest,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_path_db_path),
) -> dict[str, object]:
    """逐点明细草稿。多域逐个展开要 5–20 秒，超出的方向进 ``pending_domains``。"""

    try:
        return await create_detail_draft(
            current_user.user_id,
            db_path=db_path,
            version=payload.version,
            domains=payload.domains,
            time_budget=payload.time_budget,
        )
    except DraftConflict as exc:
        raise _conflict(exc) from exc


@router.post("/detail/draft/{version}/confirm")
async def confirm_detail_draft(
    version: int,
    current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_path_db_path),
) -> dict[str, object]:
    """确认明细：同一次调用里 ``attach_plan()``，地图立刻能点亮。"""

    try:
        return confirm_draft(current_user.user_id, version, db_path=db_path)
    except DraftConflict as exc:
        raise _conflict(exc) from exc


@router.post("/reorder")
async def reorder_learning_path(payload: ReorderRequest, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_path_db_path)) -> dict[str, object]:
    try:
        return reorder_path(current_user.user_id, payload.node_ids, db_path=db_path)
    except PathConflict as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "edges": exc.edges, "reason": exc.reason}) from exc


@router.post("/custom-node")
async def create_custom_node(payload: CustomNodeRequest, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_path_db_path)) -> dict[str, object]:
    return await add_custom_node(current_user.user_id, payload.text, db_path=db_path)


@router.post("/defer")
async def defer_learning_node(payload: DeferRequest, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_path_db_path)) -> dict[str, object]:
    return await defer_node(current_user.user_id, payload.node_id, payload.reason, payload.reason_text, db_path=db_path)


@router.post("/complete-node")
async def complete_learning_node(payload: CompleteNodeRequest, current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_path_db_path)) -> dict[str, object]:
    try:
        return complete_node(current_user.user_id, payload.node_id, payload.bkt_snapshot, db_path=db_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
