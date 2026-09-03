"""Read API for GraphAgent node detail panels."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.agents.graph_details import node_detail
from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.profile.store import DB_PATH
from app.path.state import list_states

router = APIRouter(prefix='/api/graph', tags=['graph'])


def get_graph_db_path() -> Path:
    return DB_PATH


@router.get('/node/{node_id}/detail')
async def get_node_detail(node_id: str, _current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_graph_db_path)) -> dict[str, object]:
    detail = await node_detail(node_id, db_path)
    if detail is None:
        raise HTTPException(status_code=404, detail='知识节点或药材知识库记录不存在')
    return detail


@router.get('/default-center')
async def get_default_center(current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_graph_db_path)) -> dict[str, str | None]:
    """Give the graph a stable entry point from the learner's current path node."""
    current = [item for item in list_states(current_user.user_id, db_path) if item["status"] == "current"]
    return {"center_id": current[-1]["node_id"] if current else None}
