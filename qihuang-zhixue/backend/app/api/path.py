"""Knowledge graph and three-level PathAgent APIs."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.path.schema import AdjustRequest, AdjustResponse, CompleteNodeRequest, CustomNodeRequest, DeferRequest, ExpandRequest, ExpandResponse, PlanRequest, PlanResponse, ReorderRequest
from app.path.service import PathConflict, add_custom_node, complete_node, create_l1_plan, create_l2_plan, create_l3_adjustment, current_path, defer_node, reorder_path
from app.path.store import DB_PATH, list_edges, list_tree

router = APIRouter(prefix="/api/path", tags=["path"])


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

    return await create_l1_plan(current_user.user_id, db_path=db_path, time_budget=payload.time_budget)


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
