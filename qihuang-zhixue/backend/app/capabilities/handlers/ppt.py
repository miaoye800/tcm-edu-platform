"""``ppt.generate``：幻灯工坊的对外入口，与 ``mindmap.generate`` 同形。

同形的意思是四件事都对得上，而不是「都返回 202」：

1. 交即返回（202 + task_id + stream_url），生成要十几秒，不能同步等；
2. artifact 而不是内嵌内容——调用方拿到 open_url，不是二十页 JSON；
3. 幂等：``client_invocation_id`` 相同且参数相同只受理一次；
4. 工序表与 SSE 事件从 ``resources/constants`` 派生，不另写一份。

与导图的差别只有一处，且必须体现在参数里：**幻灯有设计步与选材步**。
调用方可以传 ``theme`` / ``page_count`` / ``audience`` 作为初始值，
但版式与素材由工坊在 design、select 两步决定，并在确认单里给用户过目——
所以这两个字段的说明写的是「建议」而不是「最终」。
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from app.auth.models import AuthUser
from app.profile.store import DB_PATH
from app.resources import storage
from app.resources.constants import CURRENT_PIPELINE_VERSION, steps_for

from ..errors import CapabilityError, ErrorCode
from ..spec import ArtifactSpec, CapabilityResult, CapabilitySpec, ErrorSpec
from . import CapabilityInput

FREE_TOPIC_NODE_ID = "general"
THEME_IDS = ("paper", "ink", "celadon", "cinnabar")


class PptGenerateInput(CapabilityInput):
    query: str = Field("", max_length=200, description="要讲什么。完整一句比两个字更好：例如「给规培医师讲人参的用法与禁忌」")
    node_id: str = Field("", max_length=64, description="挂到哪个知识节点下；留空则落到自由主题节点")
    audience: str = Field("", max_length=40, description="讲给谁听；留空按「中医药专业本科生」并在设计单里写明这个假设")
    page_count: int = Field(12, ge=6, le=16, description="建议页数 6~16；实际页数以用户确认的方案为准")
    theme: Literal["paper", "ink", "celadon", "cinnabar"] | str = Field("paper", description="纸面主题：paper/ink/celadon/cinnabar")
    mode: Literal["one_shot", "outline"] = Field("one_shot", description="one_shot = 一路跑完；outline = 谋篇后停下等确认（v3 起确认点在设计步）")


SPEC = CapabilitySpec(
    name="ppt.generate",
    version="1.0",
    kind="job",
    title="生成幻灯片",
    one_line="把一句讲授需求做成一份可编辑、标了依据、设计先确认的幻灯",
    use_when=(
        "要做一份讲稿：如「做一份人参的讲稿，12 页」",
        "要给课堂/规培/科普准备演示：如「给规培医师讲十八反」",
        "已有资源要重做一版：先用 ppt.generate 重新起稿，再在编辑器里改",
    ),
    avoid_when=(
        "只要一段文字说明：用 doc.generate（或直接回答），出幻灯是杀鸡用牛刀",
        "要一张关系图：用 mindmap.generate——导图不做页，幻灯不做树",
        "要改已有幻灯的某一页：那是编辑器的事，不要重新生成整份",
    ),
    triggers=("幻灯", "PPT", "讲稿", "演示", "课件", "做几页"),
    input_model=PptGenerateInput,
    output_shape={
        "ok": "bool",
        "capability": "str，固定 ppt.generate",
        "capability_version": "str",
        "invocation_id": "str，受理号；重放时不变",
        "status": "accepted",
        "replayed": "bool，true 表示这次是重放，task_id 与上次相同",
        "task_id": "str，配合 stream_url 订阅进度",
        "stream_url": "str，GET 后得到 SSE：step_start / plan / design / pause / slot_resolution / slide / deck_verify / done",
        "artifact": "见 artifact 段；resource_id 与 open_url 在 done 之后才有值",
    },
    audiences=("agent", "frontend", "human"),
    artifact=ArtifactSpec(kind="ppt", open_url_template="/ppt?resource={resource_id}", resolve_on="done"),
    pipeline=steps_for("ppt", CURRENT_PIPELINE_VERSION),
    events=("step_start", "step_done", "plan", "design", "pause", "slot_resolution", "slide", "deck_verify", "done", "error"),
    idempotency=True,
    errors=(
        ErrorSpec(ErrorCode.TOPIC_EMPTY, 422, "没有说要讲什么", "在 query 里给一个主题，如「人参的用法与禁忌」"),
        ErrorSpec(ErrorCode.INVALID_INPUT, 422, "参数越界", "页数 6~16；主题取 paper/ink/celadon/cinnabar"),
        ErrorSpec(ErrorCode.NOT_FOUND, 404, "知识节点不存在", "换一个 node_id，或留空走自由主题"),
        ErrorSpec(ErrorCode.IDEMPOTENCY_MISMATCH, 409, "同一请求号配了不同参数", "换一个 client_invocation_id"),
    ),
    side_effects=("新建一条炼制任务", "完成后在用户资源库落一份幻灯"),
    requires=("登录用户", "本地中药学知识库（可缺，缺则全部标为模型补充）"),
    cost="无模型 key 时零调用；有 key 时一次结构化生成",
    latency="通常 8~25 秒；无 key 时 3 秒内",
    examples=("做一份人参的讲稿，12 页", "给规培医师讲十八反", "中医科普：阴阳"),
)


async def handler(payload: PptGenerateInput, actor: AuthUser, *, invocation_id: str, db_path: Any = DB_PATH) -> CapabilityResult:
    query = " ".join(payload.query.split())
    if not query:
        raise CapabilityError(ErrorCode.TOPIC_EMPTY, "请先说要讲什么", remedy="在 query 里给一个主题")
    theme = str(payload.theme or "").strip()
    if theme and theme not in THEME_IDS:
        raise CapabilityError(ErrorCode.INVALID_INPUT, f"不认识的纸面主题：{theme}", remedy="取 paper / ink / celadon / cinnabar")

    node_id = payload.node_id or FREE_TOPIC_NODE_ID
    if not storage.node_exists(node_id, db_path):
        raise CapabilityError(ErrorCode.NOT_FOUND, f"知识节点不存在：{node_id}", remedy="留空走自由主题")

    params: dict[str, Any] = {
        "topic": query[:30],
        "query": query[:120],
        "page_count": payload.page_count,
        "mode": payload.mode,
        "theme": theme or "paper",
        "client_invocation_id": payload.client_invocation_id,
    }
    if payload.audience.strip():
        params["audience"] = payload.audience.strip()[:40]

    task = storage.create_task(
        str(uuid4()),
        actor.user_id,
        node_id,
        "ppt",
        db_path,
        meta={"params": params, "pipeline_version": CURRENT_PIPELINE_VERSION, "invocation_id": invocation_id},
    )
    return CapabilityResult(
        http_status=202,
        body={
            "ok": True,
            "capability": SPEC.name,
            "capability_version": SPEC.version,
            "invocation_id": invocation_id,
            "status": "accepted",
            "replayed": False,
            "task_id": task["task_id"],
            "stream_url": f"/api/resource/tasks/{task['task_id']}/stream",
            "artifact": {
                "kind": "ppt",
                "resource_id": None,
                "open_url": None,
                "theme_id": theme or "paper",
                "resolve_on": "done",
            },
        },
    )


SPECS = ((SPEC, handler),)
