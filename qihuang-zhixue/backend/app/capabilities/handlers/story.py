"""``story.generate``：漫剧工坊的对外入口。

形态与 ``doc.generate`` 一致，参数只多一个「几场」：

- 交即返回 202，进度走 ``stream_url``；
- 产物 kind 是 ``story``，打开地址仍是通用资源页（``/resources/{id}``）——
  资源详情页已经把 story 交给文稿式排版渲染，这里不再另造一个查看器；
- 方案与设计单（场次 / 出场人 / 每场立什么冲突）在 ``design`` 步一起确认。

**为什么漫剧先只做「文本分镜」**：视频合成是另一条产线（渲染、配音、时长对齐），
把它塞进同一个任务会让「确认方案 → 出片」之间多出几分钟不可见的等待。
先把分镜与台词做对，播放形态再迭代——``artifact.kind`` 不变，前端不必改。
"""

from __future__ import annotations

from typing import Any
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


class StoryGenerateInput(CapabilityInput):
    query: str = Field("", max_length=200, description="讲哪一味药、哪个医案。例如「把麻黄汤证讲成一出短剧」")
    node_id: str = Field("", max_length=64, description="挂到哪个知识节点下；留空则落到自由主题节点")
    scenes: int = Field(4, ge=2, le=8, description="几场。2~8；默认 4（起承转合）")
    mode: str = Field("one_shot", description="one_shot = 确认方案后一路跑完")


SPEC = CapabilitySpec(
    name="story.generate",
    version="1.0",
    kind="job",
    title="生成漫剧分镜",
    one_line="把一味药或一段医案编成一出短剧：先定场次与角色，确认后写台词",
    use_when=(
        "要把知识点讲成故事：如「把麻黄汤证讲成一出短剧」",
        "要有人物与冲突的素材：如「编一个学徒用错桂枝的故事」",
    ),
    avoid_when=(
        "要严格的知识结构：用 mindmap.generate——故事不为覆盖率负责",
        "要引用逐条可核的讲义：用 doc.generate",
        "要题目：用 quiz.generate",
    ),
    triggers=("漫剧", "短剧", "故事", "情景剧", "剧本", "分镜"),
    input_model=StoryGenerateInput,
    output_shape={
        "ok": "bool",
        "capability": "str，固定 story.generate",
        "capability_version": "str",
        "invocation_id": "str，受理号；重放时不变",
        "status": "accepted",
        "replayed": "bool",
        "task_id": "str，配合 stream_url 订阅进度",
        "stream_url": "str，GET 后得到 SSE：step_start / plan / design / pause / items_verify / done",
        "artifact": "kind=story；resource_id 与 open_url 在 done 之后才有值",
    },
    audiences=("agent", "frontend", "human"),
    artifact=ArtifactSpec(kind="story", open_url_template="/resources/{resource_id}", resolve_on="done", evidence_tiers=("textbook", "graph", "model")),
    pipeline=steps_for("story", CURRENT_PIPELINE_VERSION),
    events=("step_start", "step_done", "plan", "design", "pause", "items_verify", "done", "error"),
    idempotency=True,
    errors=(
        ErrorSpec(ErrorCode.TOPIC_EMPTY, 422, "没有说讲什么", "在 query 里给一味药或一段医案"),
        ErrorSpec(ErrorCode.INVALID_INPUT, 422, "参数越界", "scenes 取 2~8"),
        ErrorSpec(ErrorCode.NOT_FOUND, 404, "知识节点不存在", "换一个 node_id，或留空走自由主题"),
        ErrorSpec(ErrorCode.IDEMPOTENCY_MISMATCH, 409, "同一请求号配了不同参数", "换一个 client_invocation_id"),
    ),
    side_effects=("新建一条炼制任务", "完成后在用户资源库落一份漫剧分镜"),
    requires=("登录用户", "本地中药学知识库（可缺，缺则按通用知识编，并在 verify 里说明）"),
    cost="无模型 key 时零调用；有 key 时按场次生成",
    latency="通常 15~45 秒；无 key 时 3 秒内",
    examples=("把麻黄汤证讲成一出短剧", "编一个学徒误用十八反的故事", "四场讲清桂枝汤的配伍"),
)


async def handler(payload: StoryGenerateInput, actor: AuthUser, *, invocation_id: str, db_path: Any = DB_PATH) -> CapabilityResult:
    query = " ".join(payload.query.split())
    if not query:
        raise CapabilityError(ErrorCode.TOPIC_EMPTY, "请先说讲什么", remedy="在 query 里给一味药或一段医案")

    node_id = payload.node_id or FREE_TOPIC_NODE_ID
    if not storage.node_exists(node_id, db_path):
        raise CapabilityError(ErrorCode.NOT_FOUND, f"知识节点不存在：{node_id}", remedy="留空走自由主题")

    params: dict[str, Any] = {
        "topic": query[:30],
        "query": query[:200],
        "scene_count": int(payload.scenes),
        "client_invocation_id": payload.client_invocation_id,
    }
    if payload.mode == "outline":
        params["mode"] = "outline"

    task = storage.create_task(
        str(uuid4()),
        actor.user_id,
        node_id,
        "story",
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
                "kind": "story",
                "resource_id": None,
                "open_url": None,
                "resolve_on": "done",
            },
        },
    )


SPECS = ((SPEC, handler),)
