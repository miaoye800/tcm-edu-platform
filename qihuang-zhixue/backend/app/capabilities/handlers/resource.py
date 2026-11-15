"""``resource.generate``（占位）：把「生成文档/试卷/故事/幻灯」收进同一张契约表。

**这一期不实现转发**，只声明。理由：另外四类资源还是 v1 四步管线，产物形状
与导图不同（没有结构、没有三档依据），现在就统一成一种 artifact 会逼出
四个特例字段，契约反而更脏。等它们各自升级到能表达「依据」时再接。

先把位置占住，是为了让 ``GET /api/capabilities`` 从第一天就是完整的目录——
调用方读到的「没有这个能力」和「有这个能力但还没接」是两件事。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..errors import CapabilityError, ErrorCode
from ..spec import ArtifactSpec, CapabilityResult, CapabilitySpec, ErrorSpec
from . import CapabilityInput


class ResourceGenerateInput(CapabilityInput):
    resource_type: Literal["doc", "quiz", "story"] = Field(..., description="要生成哪类资源")
    topic: str = Field("", max_length=200, description="主题")
    node_id: str = Field("", max_length=64, description="挂到哪个知识节点下")


SPEC = CapabilitySpec(
    name="resource.generate",
    version="0.1",
    kind="job",
    title="生成其它学习资源（未接）",
    one_line="文档、试卷、故事、幻灯的统一入口；本期只占位，调用返回 501",
    use_when=("需要文档/试卷/故事，且调用方愿意等它接完",),
    avoid_when=(
        "要导图：用 mindmap.generate，那条已经通了",
        "要幻灯：用 ppt.generate，那条也已经通了",
        "要文稿：用 doc.generate，那条也已经通了",
        "现在就要产物：直接调既有的 /api/resource/tasks 端点",
    ),
    triggers=("生成文档", "出份试卷", "讲个故事"),
    input_model=ResourceGenerateInput,
    output_shape={"ok": "bool", "message": "str"},
    audiences=("agent", "human"),
    artifact=ArtifactSpec(kind="resource", open_url_template="/resources/{resource_id}"),
    # 与 mindmap.generate 同一条规矩：job 类必须支持幂等，接上时照抄那套即可。
    idempotency=True,
    errors=(ErrorSpec(ErrorCode.NOT_IMPLEMENTED, 501, "这条契约还没接", "暂时用 /api/resource/tasks"),),
    deprecation="等 quiz/story 升到带依据的管线后接上，届时去掉 501；ppt 与 doc 已由各自能力接管。",
)


async def handler(payload: ResourceGenerateInput, actor, *, invocation_id: str, db_path=None) -> CapabilityResult:
    raise CapabilityError(
        ErrorCode.NOT_IMPLEMENTED,
        "resource.generate 本期只占位",
        remedy="导图已通；其它类型请调 POST /api/resource/tasks",
    )


SPECS = ((SPEC, handler),)
