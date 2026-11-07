"""把 ``app/kg/contract.py`` 的六个图谱能力接进统一注册表。

**为什么不改造 ``kg/contract.py`` 本身**：那六个能力是「同步查询、秒回、无产物」
的形状。硬塞 ``kind=artifact/pipeline`` 会让每条声明背一堆 0 值字段，
``tests/test_kg_contract.py`` 的判据和文档全部失真。适配比改造便宜。

**适配做什么**：把 ``tool_input`` 的字典（它是给人看的说明）翻成真正的
pydantic 输入模型，并把调用转给 ``app/kg/`` 里的纯函数——不是转给 HTTP 端点。
端点是给浏览器用的，带 ``Depends``；能力层不经过鉴权依赖，直接用已经
通过鉴权的 ``actor``。

**identity 归一**：六条里 ``lookup`` / ``ask`` / ``compare`` / ``path`` 都接受
药名或 id，统一在 ``_resolve_ids`` 里过一遍 ``entities.resolve``，让
「甘草」和「herb:181」走到同一条路上。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field, create_model

from app.kg import contract as kg_contract
from app.kg.ask import ask as kg_ask
from app.kg.attribution import attribute_mistake
from app.kg.compare import compare as kg_compare
from app.kg.compare import find_path as kg_find_path
from app.kg.prescription import review as review_prescription
from app.kg.reverse import reverse_lookup as kg_reverse_lookup
from app.profile.store import DB_PATH

from .handlers import CapabilityInput
from .spec import ArtifactSpec, CapabilityResult, CapabilitySpec, ErrorSpec
from .errors import CapabilityError, ErrorCode

#: 图谱能力的 tool_input 里哪些是数组、哪些带默认值，靠这张小表补类型。
_ARRAY_FIELDS = {"seed_ids", "herbs"}
_INT_FIELDS = {"hops", "limit", "max_hops", "top_k"}


def _input_model(name: str, tool_input: dict[str, str]):
    """把 ``{"question": "string", "seed_ids": "string[]?"}`` 翻成模型。

    ``?`` 后缀表示可省——这正是 ``contract.py`` 里已有的约定，不另立一套。
    """

    fields: dict[str, Any] = {}
    for key, spec in tool_input.items():
        optional = spec.strip().endswith("?")
        if key in _ARRAY_FIELDS:
            annotation: Any = list[str]
            default: Any = Field(default_factory=list, description=spec)
        elif key in _INT_FIELDS:
            annotation = int
            default = Field(default=0, description=spec)
        else:
            annotation = str
            default = Field(default="", description=spec)
        if not optional and annotation is str:
            fields[key] = (str, Field(..., description=spec))
        else:
            fields[key] = (annotation, default)
    fields["db_path"] = (Any, Field(default=None, exclude=True))
    return create_model(f"Kg{name.title()}Input", __base__=CapabilityInput, **fields)


# --------------------------------------------------------------------------- #
# 调用
# --------------------------------------------------------------------------- #


def _arg(payload: Any, key: str, default: Any = "") -> Any:
    value = getattr(payload, key, default)
    return default if value is None else value


async def _ask(payload: Any, actor: Any, *, db_path: Path = DB_PATH) -> dict[str, Any]:
    if not _arg(payload, "question"):
        raise CapabilityError(ErrorCode.INVALID_INPUT, "缺 question", remedy="给出完整问句")
    hops = _arg(payload, "hops", 0) or 1
    limit = _arg(payload, "limit", 0) or 12
    result = kg_ask(
        str(_arg(payload, "question")),
        db_path=db_path,
        hops=int(hops),
        limit=int(limit),
        seed_ids=list(_arg(payload, "seed_ids", [])) or None,
    )
    return kg_contract.normalize_ask(result)


async def _lookup(payload: Any, actor: Any, *, db_path: Path = DB_PATH) -> dict[str, Any]:
    query = str(_arg(payload, "query"))
    if not query:
        raise CapabilityError(ErrorCode.INVALID_INPUT, "缺 query", remedy="给出药名、证候名或症状")
    mode = str(_arg(payload, "mode") or "auto")
    if mode in ("reverse", "symptom"):
        return kg_reverse_lookup(query, top_k=int(_arg(payload, "top_k", 0) or 3), db_path=db_path)
    from app.kg import entities as kg_entities

    entity = kg_entities.resolve(query, db_path)
    return kg_contract.normalize_resolve(query, entity)


async def _compare(payload: Any, actor: Any, *, db_path: Path = DB_PATH) -> dict[str, Any]:
    left, right = str(_arg(payload, "left")), str(_arg(payload, "right"))
    if not left or not right:
        raise CapabilityError(ErrorCode.INVALID_INPUT, "缺 left / right", remedy="给出两味药的名或 id")
    return kg_contract.normalize_compare(kg_compare(left, right, db_path=db_path))


async def _path(payload: Any, actor: Any, *, db_path: Path = DB_PATH) -> dict[str, Any]:
    start, end = str(_arg(payload, "from_id")), str(_arg(payload, "to_id"))
    if not start or not end:
        raise CapabilityError(ErrorCode.INVALID_INPUT, "缺 from_id / to_id", remedy="给出起点与终点实体")
    hops = int(_arg(payload, "max_hops", 0) or 4)
    return kg_contract.normalize_path(kg_find_path(start, end, max_hops=hops, db_path=db_path))


async def _review(payload: Any, actor: Any, *, db_path: Path = DB_PATH) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for key in ("prescription", "case", "symptoms"):
        value = str(_arg(payload, key))
        if value:
            body[key] = value
    herbs = _arg(payload, "herbs", [])
    if herbs:
        body["herbs"] = herbs
    if not body:
        raise CapabilityError(ErrorCode.INVALID_INPUT, "处方是空的", remedy="给 prescription 或 herbs")
    result = review_prescription(body, db_path)
    return kg_contract.normalize_review(result)


async def _attribute(payload: Any, actor: Any, *, db_path: Path = DB_PATH) -> dict[str, Any]:
    mistake_id = str(_arg(payload, "mistake_id"))
    if not mistake_id:
        raise CapabilityError(ErrorCode.INVALID_INPUT, "缺 mistake_id", remedy="给出错题记录 id")
    # 只算不算存：能力调用不该悄悄写库。
    return attribute_mistake(mistake_id, db_path=db_path, persist=False)


_ADAPTERS = {
    "ask": _ask,
    "lookup": _lookup,
    "compare": _compare,
    "path": _path,
    "review": _review,
    "attribute": _attribute,
}

_KIND = {
    "ask": "query",
    "lookup": "query",
    "compare": "query",
    "path": "query",
    "review": "query",
    "attribute": "query",
}


def _spec(source: kg_contract.Capability) -> CapabilitySpec:
    """一条 kg 声明 → 一条统一声明。字段一一对应，不增不减。"""

    return CapabilitySpec(
        name=f"kg.{source.name}",
        version="1.0",
        kind="query",  # type: ignore[arg-type]
        title=source.title,
        one_line=source.one_line,
        use_when=source.use_when,
        avoid_when=source.avoid_when,
        triggers=source.triggers,
        input_model=_input_model(source.name, source.tool_input),
        output_shape={key: str(value) for key, value in source.output_shape.items()},
        audiences=("agent", "human"),
        artifact=ArtifactSpec(kind="facts", open_url_template="/graph", resolve_on="accepted"),
        errors=(ErrorSpec(ErrorCode.INVALID_INPUT, 422, "参数不合法", "看 use_when 里的问句样例"),),
        requires=("登录用户", "本地中药学知识图谱"),
        side_effects=("只读",) if source.name != "attribute" else (),
        cost="不调模型",
        latency="10~200 毫秒",
    )


def _bind(source: kg_contract.Capability):
    adapter = _ADAPTERS[source.name]

    async def handler(payload: Any, actor: Any, *, invocation_id: str, db_path: Any = None) -> CapabilityResult:
        body = await adapter(payload, actor, db_path=Path(db_path or DB_PATH))
        # capability 放在展开之后：kg 的归一化函数自己会塞一个短名（'lookup'），
        # 那个是历史写法，对外要统一成 kg.lookup。
        return CapabilityResult(http_status=200, body={"ok": True, **body, "capability": f"kg.{source.name}"})

    return handler


def specs():
    """六条 ``(spec, handler)``，顺序与 ``kg_contract.CAPABILITY_ORDER`` 一致。"""

    return tuple((_spec(kg_contract.get(name)), _bind(kg_contract.get(name))) for name in kg_contract.CAPABILITY_ORDER)
