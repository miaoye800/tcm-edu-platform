"""Read API for GraphAgent node detail panels and cached subgraphs."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.agents.graph_details import node_detail
from app.agents.graph_intelligence import GraphAgent
from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.graph_storage import get_graph, node_name, put_graph
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


@router.get('/{center_id}')
async def get_center_graph(center_id: str, _current_user: AuthUser = Depends(get_current_user), db_path: Path = Depends(get_graph_db_path)) -> dict[str, object]:
    """按中心节点返回子图。优先读离线预计算的缓存，缺失时用规则层现算一次。

    必须注册在 /node/... 与 /default-center 之后，否则会把它们当成 center_id。
    """
    cached = get_graph(center_id, db_path)
    if cached is not None:
        return cached

    name = node_name(center_id, db_path) or center_id
    built = GraphAgent().build_rules_only(center_id=center_id, center_name=name)
    if built is None:
        raise HTTPException(status_code=404, detail='该节点没有可用的图谱数据')
    content, _relations = built
    payload = content.model_dump(mode='json')
    put_graph(center_id, payload, datetime.now(UTC).isoformat(), db_path)
    return payload
