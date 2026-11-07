"""``kb.search``（占位）：本地知识库检索。

与 ``resource.generate`` 同理，只声明不转发。它的价值在于把「库里有什么」
这件事写清楚——调用方据 ``coverage`` 判断该不该改用 ``mindmap.generate``
的域外降级路径，而不是先拿到一张全是推断的图再猜为什么。
"""

from __future__ import annotations

from pydantic import Field

from ..errors import CapabilityError, ErrorCode
from ..spec import CapabilityResult, CapabilitySpec, ErrorSpec
from . import CapabilityInput


class KbSearchInput(CapabilityInput):
    query: str = Field("", max_length=200, description="要查什么")
    limit: int = Field(8, ge=1, le=40, description="最多返回几条")


SPEC = CapabilitySpec(
    name="kb.search",
    version="0.1",
    kind="query",
    title="检索本地中药学知识库（未接）",
    one_line="查教材条目、图谱边与证候；本期只占位，调用返回 501",
    use_when=("需要确认某个词在不在本地语料里",),
    avoid_when=("要成图：用 mindmap.generate；要关系：用 kg_* 六能力",),
    triggers=("查一下", "有没有收录", "知识库里"),
    input_model=KbSearchInput,
    output_shape={"ok": "bool", "coverage": "str，in_corpus / out_of_corpus", "items": "list"},
    audiences=("agent", "human"),
    errors=(ErrorSpec(ErrorCode.NOT_IMPLEMENTED, 501, "这条契约还没接", "暂用既有检索接口"),),
    cost="纯本地检索，无模型调用",
    latency="毫秒级",
    deprecation="等检索层收敛成一个服务后接上。",
)


async def handler(payload: KbSearchInput, actor, *, invocation_id: str, db_path=None) -> CapabilityResult:
    raise CapabilityError(ErrorCode.NOT_IMPLEMENTED, "kb.search 本期只占位", remedy="暂用既有检索接口")


SPECS = ((SPEC, handler),)
