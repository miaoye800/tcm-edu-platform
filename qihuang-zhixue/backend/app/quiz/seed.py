"""Offline original seed generation based solely on existing knowledge nodes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.path.store import list_tree
from app.quiz.storage import upsert_exam_point, upsert_question


def _flatten(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]]=[]
    for node in nodes: result.append(node); result.extend(_flatten(node.get("children",[])))
    return result


def seed_question_bank(db_path: Path, target_count: int = 36) -> int:
    nodes = [node for node in _flatten(list_tree(db_path=db_path)) if node.get("level") == "point"]
    if not nodes: nodes = _flatten(list_tree(db_path=db_path))
    if not nodes: return 0
    names = [str(x["name"]) for x in nodes]
    added = 0
    for index in range(target_count):
        node = nodes[index % len(nodes)]; name = str(node["name"]); alternatives = [x for x in names if x != name][:3] or ["相关知识点"]
        point_id = upsert_exam_point(node_id=str(node["id"]),name=name,exam_years=[],frequency=float(node.get("exam_frequency",0)),source=f"original: knowledge_node:{node['id']}",db_path=db_path)
        kind = ("single","short","case")[index % 3]
        if kind == "single": stem=f"围绕知识点“{name}”进行复习时，下列哪项是本题对应的核心知识点？"; options=[{"key":"A","text":name},*[{"key":chr(66+i),"text":x} for i,x in enumerate(alternatives[:3])]]; answer=["A"]
        elif kind == "short": stem=f"请用一句话说明“{name}”的核心学习要点。"; options=[]; answer=[name]
        else: stem=f"病例学习中需要回顾与“{name}”相关的辨证选药思路，请写出应优先复习的知识点。"; options=[]; answer=[name]
        upsert_question({"exam_point_id":point_id,"stem":stem,"options":options,"answer":answer,"analysis":f"本题为依据知识库节点“{name}”生成的原创学习题，应回到该节点讲解与图谱复习。","year":None,"source":f"original: knowledge_node:{node['id']}","question_type":kind},db_path); added += 1
    return added

