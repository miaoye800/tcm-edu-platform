"""Read API for the knowledge graph.

这里有两个契约并存：

- ``GET /api/kg/*``（``app/api/kg.py``）：新契约，图谱作为可查询服务。
- ``GET /api/graph/*``（本文件）：旧契约，前端 ``GraphView.vue`` 依赖它的
  ``{center_id, nodes, edges, sources}`` 结构。

本文件现在**内部走 KG**（``app.kg``），由 ``app.kg.adapters`` 适配回旧结构。
所以前端一行不用改，但拿到的已经是「T1 硬约束 + T2 事实关系」的新图，
不再是旧的 58% 相似边大乱炖。

``graph_cache`` 不再参与服务：那里面是旧规则生成的假图，读它等于继续骗人。
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from app.agents.graph_details import node_detail
from app.auth.dependencies import get_current_user
from app.auth.models import AuthUser
from app.kg.adapters import missing_brief, no_edge_brief, placeholder_for_unresolved, placeholder_graph, to_graph_content, tree_level
from app.kg.entities import resolve as kg_resolve
from app.kg.traverse import neighbors
from app.profile.store import DB_PATH, connect
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
    """Give the graph a stable entry point from the learner's current path node.

    但**当前节点未必是药材**。总论的 `general.p1`（性味归经）也是学习路径上的
    合法节点，把它当中心，图里只有一个孤立点——实测打开 /atlas 就是「1 个节点 /
    0 条关系」的空白画布。所以当前节点只在**真能出图**时才用；出不了图就退到
    关系最多的那味药，而不是让用户对着空画布。
    """

    current = [item for item in list_states(current_user.user_id, db_path) if item["status"] == "current"]
    if current:
        candidate = str(current[-1]["node_id"])
        entity = kg_resolve(candidate, db_path)
        if entity is not None:
            subgraph = neighbors(entity.id, hops=1, limit=2, db_path=db_path)
            if subgraph.edges:
                return {"center_id": candidate}
    return {"center_id": _busiest_herb_node(db_path)}


def _tree_level(center_id: str, db_path: Path) -> str | None:
    """中心节点在知识树里的层级。

    实现已移到 ``app/kg/adapters.tree_level``：``/api/kg/neighbors`` 也要做同一
    判断，两份实现迟早会说出两套话。这里只保留旧名字，避免改动调用点。
    """

    return tree_level(center_id, db_path)


def _missing_brief(center_id: str, db_path: Path) -> str:
    """解析不到实体时说清楚「缺的是哪一类东西」，实现见 ``adapters.missing_brief``。"""

    return missing_brief(center_id, db_path)


def _busiest_herb_node(db_path: Path) -> str | None:
    """关系最多的药材对应的树节点。用作兜底入口，不编映射：只要真节点。"""

    with closing(connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT e.knowledge_node_id AS node_id, COUNT(g.id) AS degree
            FROM kg_entity e
            JOIN kg_edge g ON g.source_id = e.id OR g.target_id = e.id
            WHERE e.kind = 'herb'
              AND e.status = 'approved'
              AND e.knowledge_node_id IS NOT NULL
            GROUP BY e.id
            ORDER BY degree DESC, e.id
            LIMIT 1
            """
        ).fetchone()
    return str(row[0]) if row else None


@router.get('/{center_id}')
async def get_center_graph(
    center_id: str,
    hops: int = Query(default=1, ge=1, le=3, description="扩展跳数"),
    limit: int = Query(default=40, ge=5, le=120, description="节点上限"),
    _current_user: AuthUser = Depends(get_current_user),
    db_path: Path = Depends(get_graph_db_path),
) -> dict[str, object]:
    """按中心节点返回子图。只走 T1+T2，T3 相似边不进图。

    必须注册在 /node/... 与 /default-center 之后，否则会把它们当成 center_id。

    解析不到时返回「教材未收录」单节点而不是 404——404 会让前端
    ``stores/graph.ts`` 回落 ``mockMahuangGraph``，用户看到的是 15 个硬编码
    节点的假图。宁可显示「没有」，也不要显示「假的」。
    """
    entity = kg_resolve(center_id, db_path)
    if entity is None:
        return placeholder_for_unresolved(center_id, db_path)

    subgraph = neighbors(entity.id, hops=hops, limit=limit, db_path=db_path)
    if not subgraph.edges:
        return placeholder_graph(entity.id, db_path, brief=no_edge_brief(entity))
    return to_graph_content(subgraph, db_path)
