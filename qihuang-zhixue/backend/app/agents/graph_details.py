"""Node detail payloads backed by the same evidence discipline as GraphAgent."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agents.graph_contract import GraphSource
from app.agents.graph_intelligence import source_for
from app.agents.resource_context import _herb_to_doc
from app.graph_storage import get_detail, node_name, put_detail
from app.kb.ingest import load_herbs
from app.kg.entities import resolve as kg_resolve
from app.profile.store import DB_PATH


async def node_detail(node_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    cached = get_detail(node_id, db_path)
    if cached is not None:
        return cached
    name = node_name(node_id, db_path)
    herb = next((item for item in load_herbs() if item.name == name or item.id == node_id), None)
    if herb is None:
        # 历史 id 有三种：knowledge_node id、旧 graph_entity 哈希 id、药材语义 id。
        # 前两种靠 resolve 归一后才能查到 monograph——否则从资源图谱点进非中心节点
        # 一律 404，前端只能本地拼一份残缺详情。
        entity = kg_resolve(node_id, db_path)
        if entity is not None and entity.kind == "herb":
            herb = next((item for item in load_herbs() if item.id == entity.id), None)
            if herb is not None and entity.knowledge_node_id:
                node_id = entity.knowledge_node_id
            elif herb is None:
                # 教材立了条目、但结构化字段还没抽（draft），或教材没立条（orphan）。
                # 这时 404 等于说「没这味药」——把「我们没抽」说成「不存在」。
                return _identity_only_detail(entity, node_id, db_path)
    if herb is None:
        return None
    doc = _herb_to_doc(herb, [source.model_dump(mode='json') for source in herb.sources])
    sources = [source_for(doc, f"{doc['name']}：{'；'.join(doc['effects'][:2])}")]
    detail = {
        "性味归经": f"性{doc['nature']}，味{'、'.join(doc['flavor'])}，归{'、'.join(doc['meridians'])}经。",
        "功效主治": {"功效": doc['effects'], "主治": doc['indications']},
        "常用剂量与炮制": {"剂量": doc['dosage'], "炮制": "知识库暂未覆盖"},
        "使用禁忌": doc['cautions'] or ["知识库暂未覆盖"],
        "经典出处": [item.出处 for item in sources],
        "关联考点": _exam_frequency(node_id, db_path),
        "sources": [item.model_dump(mode='json') for item in sources],
    }
    put_detail(node_id, detail, datetime.now(UTC).isoformat(), db_path)
    return detail


def _identity_only_detail(entity: Any, node_id: str, db_path: Path) -> dict[str, Any]:
    """只有身份、没有结构化条目的药材，详情也要给出来。

    字段结构与正常详情一致，缺的部分显式写「知识库暂未覆盖」——前端不用改，
    用户也不会看到「404 就等于这味药不存在」这种错误结论。
    """

    extra = entity.extra or {}
    where = "·".join(part for part in (extra.get("chapter"), extra.get("section")) if part)
    page = str(extra.get("page") or "")
    if page:
        where = f"{where} p{page}".strip()
    reason = (
        "教材未给该药立独立条目（仅在其他药材的附药/别名里出现）"
        if entity.status == "orphan"
        else "教材已收录，但性味/功效/主治等结构化字段尚未抽取"
    )
    return {
        "性味归经": "知识库暂未覆盖",
        "功效主治": {"功效": [], "主治": []},
        "常用剂量与炮制": {"剂量": "知识库暂未覆盖", "炮制": "知识库暂未覆盖"},
        "使用禁忌": ["知识库暂未覆盖"],
        "经典出处": [f"《中药学》(钟赣生·新世纪第四版)·{where}"] if where else [],
        "关联考点": _exam_frequency(node_id, db_path),
        "sources": [],
        "收录状态": {"status": entity.status, "说明": reason, "教材定位": where},
    }


def _exam_frequency(node_id: str, db_path: Path) -> dict[str, Any]:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT exam_frequency,description FROM knowledge_node WHERE id=?", (node_id,)).fetchone()
    return {"exam_frequency": float(row[0]) if row else 0.0, "说明": str(row[1]) if row and row[1] else ""}
