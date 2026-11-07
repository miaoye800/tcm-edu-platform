"""文档块编辑与导出的接口契约。

这组测试守住三条线：
  1. `_doc_blocks` 仍然产出旧 6 种 type 且每块带 `text`（旧前端与既有测试依赖）
  2. `PATCH /document` 的版本语义（成功 +1，旧版本 409）
  3. 只有 doc 资源可编辑、只有属主可写、非法块类型被拒
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.resources import get_resource_db_path
from app.main import app
from app.path.store import upsert_node
from app.resources import service as resource_service
from app.resources import storage as resource_storage
from app.resources.doc_schema import sanitize_meta
from tests.helpers import auth_session


def _seed_doc(db_path: Path, user_id: str, *, resource_type: str = "doc", blocks: list[dict] | None = None) -> str:
    upsert_node({"id": "mahuang", "name": "麻黄", "level": "point"}, db_path)
    resource_id = str(uuid4())
    resource_storage.create_resource(
        {
            "id": resource_id,
            "user_id": user_id,
            "node_id": "mahuang",
            "type": resource_type,
            "title": "麻黄 · 讲解",
            "content": {"blocks": blocks or [{"type": "paragraph", "text": "发汗解表 [0]"}]},
            "sources": [{"出处": "本草纲目", "原文摘录": "麻黄", "定位": "草部"}],
            "notes": "",
            "status": "ready",
            "created_at": "2026-11-04T00:00:00+00:00",
            "meta": {},
        },
        db_path,
    )
    return resource_id


def test_doc_blocks_keep_legacy_fields_and_add_structure() -> None:
    blocks = resource_service._doc_blocks(
        {
            "sections": [
                {
                    "title": "功效",
                    "content": "发汗解表",
                    "claims": ["发汗解表", "宣肺平喘"],
                    "sources": [{"ref": "教材 p12", "quote": "麻黄，发汗解表。"}],
                },
                {"title": "用量与使用注意", "content": "2–10g", "claims": [], "sources": []},
            ]
        }
    )

    # 旧契约：六种 type 名 + 每块必有 text
    assert {"heading", "paragraph", "bullet", "table", "callout", "quote"} <= {block["type"] for block in blocks}
    assert all(isinstance(block.get("text"), str) for block in blocks)
    # 新契约：稳定 id 与结构化 props
    assert len({block["id"] for block in blocks}) == len(blocks)

    heading = next(block for block in blocks if block["type"] == "heading")
    assert heading["props"]["level"] == 2
    table = next(block for block in blocks if block["type"] == "table")
    assert table["props"]["columns"] == ["要点", "依据"]
    assert table["props"]["rows"][0][0] == "发汗解表"
    # 表格正文同时保留 Markdown，供导出与降级渲染
    assert table["text"].startswith("| 要点 | 依据 |")
    callout = next(block for block in blocks if block["type"] == "callout")
    assert callout["props"]["tone"] == "warn"
    quote = next(block for block in blocks if block["type"] == "quote")
    assert quote["props"]["source"]["原文摘录"] == "麻黄，发汗解表。"


def test_save_document_increments_version_and_conflicts(tmp_path: Path) -> None:
    db_path = tmp_path / "qihuang.db"
    app.dependency_overrides[get_resource_db_path] = lambda: db_path
    client = TestClient(app)
    session = auth_session(client, "doc-owner", db_path)
    resource_id = _seed_doc(db_path, session["user"]["user_id"])

    payload = {
        "version": 1,
        "blocks": [
            {"id": "blk_h", "type": "heading1", "text": "总论"},
            {"id": "blk_t", "type": "todo", "text": "记住五味", "props": {"checked": True}},
        ],
        "meta": {"icon": "📜", "cover": "jade", "generator": "hacked"},
    }
    first = client.patch(f"/api/resources/{resource_id}/document", json=payload, headers=session["headers"])
    assert first.status_code == 200, first.text
    assert first.json() == {"version": 2, "block_count": 2}

    detail = client.get(f"/api/resources/{resource_id}", headers=session["headers"]).json()
    assert [block["type"] for block in detail["content"]["blocks"]] == ["heading1", "todo"]
    assert detail["content"]["blocks"][1]["props"]["checked"] is True
    assert detail["meta"]["doc_version"] == 2
    # 服务端专属键不允许被客户端覆盖
    assert "generator" not in detail["meta"]
    assert detail["node_name"] == "麻黄"

    replay = client.patch(f"/api/resources/{resource_id}/document", json=payload, headers=session["headers"])
    assert replay.status_code == 409
    assert "版本" in replay.json()["detail"]


def test_save_document_rejects_non_doc_and_bad_blocks(tmp_path: Path) -> None:
    db_path = tmp_path / "qihuang.db"
    app.dependency_overrides[get_resource_db_path] = lambda: db_path
    client = TestClient(app)
    session = auth_session(client, "doc-guard", db_path)

    mindmap_id = _seed_doc(db_path, session["user"]["user_id"], resource_type="mindmap")
    rejected = client.patch(
        f"/api/resources/{mindmap_id}/document",
        json={"version": 1, "blocks": [{"id": "a", "type": "paragraph", "text": "x"}]},
        headers=session["headers"],
    )
    assert rejected.status_code == 422

    doc_id = _seed_doc(db_path, session["user"]["user_id"])
    unknown = client.patch(
        f"/api/resources/{doc_id}/document",
        json={"version": 1, "blocks": [{"id": "a", "type": "database", "text": "x"}]},
        headers=session["headers"],
    )
    assert unknown.status_code == 422

    empty = client.patch(
        f"/api/resources/{doc_id}/document",
        json={"version": 1, "blocks": []},
        headers=session["headers"],
    )
    assert empty.status_code == 422


def test_save_document_is_scoped_to_owner(tmp_path: Path) -> None:
    db_path = tmp_path / "qihuang.db"
    app.dependency_overrides[get_resource_db_path] = lambda: db_path
    client = TestClient(app)
    owner = auth_session(client, "doc-a", db_path)
    intruder = auth_session(client, "doc-b", db_path)
    resource_id = _seed_doc(db_path, owner["user"]["user_id"])

    response = client.patch(
        f"/api/resources/{resource_id}/document",
        json={"version": 1, "blocks": [{"id": "a", "type": "paragraph", "text": "x"}]},
        headers=intruder["headers"],
    )
    assert response.status_code == 404


def test_export_markdown_matches_block_structure(tmp_path: Path) -> None:
    db_path = tmp_path / "qihuang.db"
    app.dependency_overrides[get_resource_db_path] = lambda: db_path
    client = TestClient(app)
    session = auth_session(client, "doc-export", db_path)
    resource_id = _seed_doc(
        db_path,
        session["user"]["user_id"],
        blocks=[
            {"id": "h", "type": "heading2", "text": "核心定位"},
            {"id": "t", "type": "todo", "text": "记住五味", "props": {"checked": True}},
            {"id": "b", "type": "table", "props": {"columns": ["性", "味"], "rows": [["温", "辛"]]}},
        ],
    )

    response = client.get(f"/api/resources/{resource_id}/document.md", headers=session["headers"])
    assert response.status_code == 200
    assert "text/markdown" in response.headers["content-type"]
    body = response.text
    assert "## 核心定位" in body
    assert "- [x] 记住五味" in body
    assert "| 性 | 味 |" in body


def test_sanitize_meta_only_allows_editor_keys() -> None:
    clean = sanitize_meta({"icon": "🌿", "cover": "jade", "fullWidth": True, "generator": "x", "params": {"a": 1}})
    assert clean == {"icon": "🌿", "cover": "jade", "fullWidth": True}
    # 超长字符串被截断，避免把大对象塞进 meta
    assert len(sanitize_meta({"icon": "x" * 500})["icon"]) == 200
