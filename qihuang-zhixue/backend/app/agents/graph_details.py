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
from app.profile.store import DB_PATH


async def node_detail(node_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    cached = get_detail(node_id, db_path)
    if cached is not None:
        return cached
    name = node_name(node_id, db_path)
    herb = next((item for item in load_herbs() if item.name == name or item.id == node_id), None)
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


def _exam_frequency(node_id: str, db_path: Path) -> dict[str, Any]:
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT exam_frequency,description FROM knowledge_node WHERE id=?", (node_id,)).fetchone()
    return {"exam_frequency": float(row[0]) if row else 0.0, "说明": str(row[1]) if row and row[1] else ""}
