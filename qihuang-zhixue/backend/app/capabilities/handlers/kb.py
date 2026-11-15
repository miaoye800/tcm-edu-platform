"""``kb.search``：本地中药学知识库检索。

原先是个 501 占位。现在接上 ``resource_context.retrieve_resource_docs``——
生成侧用的就是这一条，因此「查得到」与「生成时用得上」是同一个答案，
不会再出现「检索说没有、生成却能引经据典」这种自相矛盾。

**返回值刻意分两层**：``coverage`` 回答「在不在库里」，``items`` 回答「库里怎么说」。
只给 items，调用方还得自己数条数来判断覆盖率；只给 coverage，又拿不到可引用的原文。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.agents.resource_context import retrieve_resource_docs

from ..errors import CapabilityError, ErrorCode
from ..spec import CapabilityResult, CapabilitySpec, ErrorSpec
from . import CapabilityInput


class KbSearchInput(CapabilityInput):
    query: str = Field("", max_length=200, description="要查什么")
    limit: int = Field(8, ge=1, le=40, description="最多返回几条")


SPEC = CapabilitySpec(
    name="kb.search",
    version="1.0",
    kind="query",
    title="检索本地中药学知识库",
    one_line="查某个词在不在本地教材语料里，并给出可引用的原文条目",
    use_when=(
        "要确认某个说法有没有教材依据：如「教材里有说桂枝反藜芦吗」",
        "要一组可引用的原文：如「找几段麻黄性味的原文」",
        "生成前先探底：看这次主题能不能落到教材上",
    ),
    avoid_when=(
        "要关系与配伍：用 kg.ask / kg.compare——检索只给文本段落",
        "要成图或成文：用 mindmap.generate / doc.generate",
    ),
    triggers=("查一下", "有没有收录", "知识库里", "教材里有没有", "原文"),
    input_model=KbSearchInput,
    output_shape={
        "ok": "bool",
        "capability": "str，固定 kb.search",
        "coverage": "str，in_corpus / out_of_corpus",
        "items": "list，每项含 name / category / quote / refs",
    },
    audiences=("agent", "human"),
    errors=(ErrorSpec(ErrorCode.TOPIC_EMPTY, 422, "没有说要查什么", "在 query 里给一个词或一句话"),),
    side_effects=(),
    requires=("本地中药学知识库",),
    cost="纯本地检索，无模型调用",
    latency="毫秒级",
    deprecation="",
    examples=("教材里有说桂枝反藜芦吗", "找几段麻黄性味的原文"),
)


def _quote_of(doc: dict[str, Any]) -> str:
    """摘一段能直接引用的原文。

    文献条目里没有 ``quote`` 字段（那是生成阶段拼出来的），这里按「性味归经 →
    功效」的顺序拼一句，而不是把整个 dict 丢回去让调用方自己读。
    """

    nature = str(doc.get("nature") or "").strip()
    flavor = "、".join(doc.get("flavor") or [])
    meridians = "、".join(doc.get("meridians") or [])
    effects = "、".join(doc.get("effects") or [])
    parts: list[str] = []
    if nature or flavor or meridians:
        parts.append(f"性味：{nature}{flavor}；归经：{meridians}")
    if effects:
        parts.append(f"功效：{effects}")
    return "。".join(parts)[:240]


async def handler(payload: KbSearchInput, actor: Any, *, invocation_id: str, db_path: Any = None) -> CapabilityResult:
    query = " ".join(payload.query.split())
    if not query:
        raise CapabilityError(ErrorCode.TOPIC_EMPTY, "请先说查什么", remedy="在 query 里给一个词或一句话")

    docs = await retrieve_resource_docs(query, top_k=max(1, min(payload.limit, 12)))
    items = [
        {
            "name": str(doc.get("name") or ""),
            "category": str(doc.get("category") or ""),
            "quote": _quote_of(doc),
            "refs": [str(doc.get("id") or "")],
        }
        for doc in docs[: payload.limit]
        if doc.get("name")
    ]
    return CapabilityResult(
        http_status=200,
        body={
            "ok": True,
            "capability": SPEC.name,
            "capability_version": SPEC.version,
            "invocation_id": invocation_id,
            "coverage": "in_corpus" if items else "out_of_corpus",
            "items": items,
        },
    )


SPECS = ((SPEC, handler),)
