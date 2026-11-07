"""文稿写作管线（app/docs）的契约。

守四条线：
  1. Markdown → 块：标题/列表/表格/提示块/折叠块都能还原，不丢结构
  2. 体裁判定：要计划就给计划，要对比就给对比，不能一律当讲解
  3. 跨领域主题：知识库无命中时照样出稿，不许出现「无相关中药信息」
  4. 引用：来源池按片段展开（不再整篇同一句），越界或非数字标记一律清掉
"""

from __future__ import annotations

import asyncio
import json

from app.agents.resource_context import retrieve_resource_docs
from app.docs import DocAgent
from app.docs.agent import build_source_pool, classify_kind, derive_title
from app.docs.markdown_blocks import markdown_to_blocks
from app.resources.doc_markdown import blocks_to_markdown


def test_markdown_to_blocks_restores_notion_shapes() -> None:
    markdown = "\n".join(
        [
            "## 第一周：认药性",
            "",
            "熟悉人参的性味，",
            "不要贪多。",
            "",
            "- 性味归经",
            "  - 甘、微苦，微温",
            "- 功效主治",
            "- [x] 完成自测",
            "",
            "> [!TIP] 每天复述 20 分钟。",
            "",
            "> ⚠️ 实证忌用。",
            "",
            "| 阶段 | 重点 |",
            "| --- | --- |",
            "| 第一周 | 性味 |",
            "",
            "```python",
            'print("ok")',
            "```",
            "",
            "---",
        ]
    )

    blocks = markdown_to_blocks(markdown)
    kinds = [block["type"] for block in blocks]

    assert kinds[0] == "heading2"
    assert "toggle" in kinds, "带子项的列表项应折叠为 toggle"
    assert "todo" in kinds and next(b for b in kinds if b == "todo")
    callouts = [block for block in blocks if block["type"] == "callout"]
    assert {item["props"]["tone"] for item in callouts} == {"tip", "warn"}
    table = next(block for block in blocks if block["type"] == "table")
    assert table["props"]["columns"] == ["阶段", "重点"]
    assert table["props"]["rows"] == [["第一周", "性味"]]
    assert next(block for block in blocks if block["type"] == "code")["props"]["lang"] == "python"
    assert "divider" in kinds
    # 中文软换行合并时不加空格
    paragraph = next(block for block in blocks if block["type"] == "paragraph")
    assert paragraph["text"] == "熟悉人参的性味，不要贪多。"

    # 往返：导出的 Markdown 再解析，标题与表格不丢
    again = markdown_to_blocks(blocks_to_markdown(blocks))
    assert [b["type"] for b in again if b["type"] == "heading2"] == ["heading2"]
    assert any(b["type"] == "table" for b in again)


def test_classify_kind_follows_user_intent() -> None:
    assert classify_kind("我想学习人参的相关内容，给我制订一份计划") == "学习计划"
    assert classify_kind("人参和党参有什么区别") == "对比分析"
    assert classify_kind("炮制人参的具体步骤是什么") == "操作指南"
    assert classify_kind("讲讲人参这味药") == "讲解"


def test_derive_title_is_not_the_raw_request() -> None:
    title = derive_title("请生成一份人工智能基础知识的文档", "讲解", [])
    assert "请生成" not in title
    assert "文档" not in title
    assert title == "人工智能基础知识"


def test_general_topic_writes_document_without_knowledge_base() -> None:
    """跨领域主题（知识库必然无命中）也要出稿，且不得伪造引用。"""

    plan_json = json.dumps(
        {
            "title": "人工智能基础核心概念",
            "doc_kind": "讲解",
            "audience": "初学者",
            "thesis": "从概念到应用",
            "sections": [
                {"heading": "它是什么", "goal": "定义", "points": ["弱 AI 与强 AI"]},
                {"heading": "怎么运作", "goal": "机制", "points": ["监督学习"]},
                {"heading": "怎么用", "goal": "应用", "points": ["医疗影像"]},
                {"heading": "常见误区", "goal": "纠偏", "points": []},
            ],
            "grounded": False,
            "keywords": [],
        },
        ensure_ascii=False,
    )
    markdown = "\n".join(
        [
            "## 它是什么",
            "",
            "人工智能让机器模拟认知功能，通常分为弱人工智能与强人工智能[0]。",
            "",
            "## 怎么运作",
            "",
            "- 监督学习依赖标注数据",
            "",
            "## 怎么用",
            "",
            "医疗影像是最早落地的场景之一。",
            "",
            "## 常见误区",
            "",
            "把机器学习等同于人工智能是常见误解[herbs]。",
        ]
    )

    calls: list[str] = []

    async def fake_llm(messages: list[dict[str, str]]) -> str:
        system = messages[0]["content"]
        calls.append(system)
        return plan_json if "写作策划" in system else markdown

    result = asyncio.run(DocAgent(llm_call=fake_llm).generate("请生成一份人工智能基础知识的文档", []))

    assert result.title == "人工智能基础核心概念"
    assert result.grounded is False
    assert result.sources == []
    assert result.blocks, "跨领域主题不能产出空文档"
    assert len([b for b in result.blocks if b["type"] == "heading2"]) == 4
    text = " ".join(b["text"] for b in result.blocks)
    assert "无相关中药" not in text
    # 没有来源池时，任何 [n] 或占位式引用都不能留在正文里
    assert "[0]" not in text and "[herbs]" not in text
    assert len(calls) == 2


def test_grounded_topic_uses_distinct_source_quotes() -> None:
    """知识库命中时，来源池应是逐条片段，而不是整篇同一句话。"""

    docs = asyncio.run(retrieve_resource_docs("人参"))
    assert docs, "测试数据里应有「人参」条目"

    pool = build_source_pool(docs)
    assert len({item["原文摘录"] for item in pool}) > 1

    result = asyncio.run(DocAgent(llm_call=_stub_llm()).generate("讲讲人参的性味和功效", docs))
    assert result.grounded is True
    assert result.sources, "命中知识库时应带溯源"
    quotes = {item["原文摘录"] for item in result.sources}
    assert len(quotes) > 1, "每节都挂同一句引文是旧实现的 bug"


def _stub_llm():
    plan = json.dumps(
        {
            "title": "人参性味与功效",
            "doc_kind": "讲解",
            "sections": [
                {"heading": "性味归经", "goal": "记药性"},
                {"heading": "功效主治", "goal": "记功效"},
                {"heading": "用量与禁忌", "goal": "安全用药"},
            ],
            "grounded": True,
        },
        ensure_ascii=False,
    )
    markdown = "## 性味归经\n\n甘、微苦，微温[0]。\n\n## 功效主治\n\n大补元气[1]。\n\n## 用量与禁忌\n\n3~9g，实证忌用[3]。"

    async def fake(messages: list[dict[str, str]]) -> str:
        return plan if "写作策划" in messages[0]["content"] else markdown

    return fake


def test_generate_content_doc_returns_document_payload(monkeypatch) -> None:
    """服务层接的是新管线：载荷里要带标题、体裁与块，而不是旧的 sections。"""

    from app.docs.agent import DocResult
    from app.resources import service as resource_service

    class _FakeAgent:
        async def generate(self, request: str, docs=None) -> DocResult:  # noqa: ANN001
            return DocResult(
                title="人参三周学习计划",
                blocks=[{"id": "b1", "type": "paragraph", "text": "正文"}],
                sources=[],
                doc_kind="学习计划",
                grounded=False,
                meta={"source_mode": "general"},
            )

    monkeypatch.setattr(resource_service, "DocAgent", lambda: _FakeAgent())
    content, sources = asyncio.run(resource_service.generate_content("doc", "我想学人参，给我制订一份计划"))

    assert content["title"] == "人参三周学习计划"
    assert content["doc_kind"] == "学习计划"
    assert content["source_mode"] == "general"
    assert content["blocks"][0]["type"] == "paragraph"
    assert sources == []
