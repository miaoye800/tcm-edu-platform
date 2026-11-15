"""``quiz.generate``：题库工坊的对外入口。

与 ``doc.generate`` 同形（交即返回、artifact 而不是内嵌题目、幂等、工序表派生），
差别只在参数与产物：

1. 参数收的是「考什么」与「出几道」，不是文风——卷式（题型配比 / 难度档）
   由 ``designs._quiz_design`` 在 design 步给出，用户确认后才拟题；
2. artifact 的 kind 是 ``quiz``，打开地址仍走通用的资源查看页——
   逐题做题的界面在题库，不在资源页，这里只保证「能找到那份卷子」。

**为什么把 count 收成参数而不是让模型数**：题量是用户最容易有主见的一个数
（「给我五道就行」），模型按「八道比较完整」自作主张时，用户只能重来一遍。
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


class QuizGenerateInput(CapabilityInput):
    query: str = Field("", max_length=200, description="考什么。例如「解表药的性味与配伍禁忌」")
    node_id: str = Field("", max_length=64, description="挂到哪个知识节点下；留空则落到自由主题节点")
    count: int = Field(8, ge=3, le=20, description="出几道题。3~20；默认 8")
    difficulty: str = Field("medium", description="easy / medium / hard；只影响配比，不改变考点范围")
    mode: str = Field("one_shot", description="one_shot = 确认方案后一路跑完")


SPEC = CapabilitySpec(
    name="quiz.generate",
    version="1.0",
    kind="job",
    title="生成试卷",
    one_line="按考点出一份可练的卷子：先给卷式与考点，确认后拟题设错",
    use_when=(
        "要一份练习卷：如「出一份解表药的卷子，八道题」",
        "要错题式辨析：如「考考我麻黄和桂枝的区别」",
        "要配伍禁忌的判断练习：如「出几道十八反的配伍题」",
    ),
    avoid_when=(
        "只想问一个答案：直接回答，不必出卷",
        "要知识结构：用 mindmap.generate",
        "要讲解材料：用 doc.generate——卷子不给解释",
    ),
    triggers=("出题", "卷子", "试卷", "练习", "测验", "考考我", "题库"),
    input_model=QuizGenerateInput,
    output_shape={
        "ok": "bool",
        "capability": "str，固定 quiz.generate",
        "capability_version": "str",
        "invocation_id": "str，受理号；重放时不变",
        "status": "accepted",
        "replayed": "bool",
        "task_id": "str，配合 stream_url 订阅进度",
        "stream_url": "str，GET 后得到 SSE：step_start / plan / design / pause / items_verify / done",
        "artifact": "kind=quiz；resource_id 与 open_url 在 done 之后才有值",
    },
    audiences=("agent", "frontend", "human"),
    artifact=ArtifactSpec(kind="quiz", open_url_template="/resources/{resource_id}", resolve_on="done", evidence_tiers=("textbook", "graph", "model")),
    pipeline=steps_for("quiz", CURRENT_PIPELINE_VERSION),
    events=("step_start", "step_done", "plan", "design", "pause", "items_verify", "done", "error"),
    idempotency=True,
    errors=(
        ErrorSpec(ErrorCode.TOPIC_EMPTY, 422, "没有说考什么", "在 query 里给一个考点或范围"),
        ErrorSpec(ErrorCode.INVALID_INPUT, 422, "参数越界", "count 取 3~20；difficulty 取 easy/medium/hard"),
        ErrorSpec(ErrorCode.NOT_FOUND, 404, "知识节点不存在", "换一个 node_id，或留空走自由主题"),
        ErrorSpec(ErrorCode.IDEMPOTENCY_MISMATCH, 409, "同一请求号配了不同参数", "换一个 client_invocation_id"),
    ),
    side_effects=("新建一条炼制任务", "完成后在用户资源库落一份试卷"),
    requires=("登录用户", "本地中药学知识库（可缺，缺则题目按通用知识出，并在 verify 里说明）"),
    cost="无模型 key 时零调用；有 key 时按题量生成",
    latency="通常 10~40 秒；无 key 时 3 秒内",
    examples=("出一份解表药的卷子，八道题", "考考我麻黄和桂枝的区别", "出五道十八反配伍题"),
)


async def handler(payload: QuizGenerateInput, actor: AuthUser, *, invocation_id: str, db_path: Any = DB_PATH) -> CapabilityResult:
    query = " ".join(payload.query.split())
    if not query:
        raise CapabilityError(ErrorCode.TOPIC_EMPTY, "请先说考什么", remedy="在 query 里给一个考点或范围")

    node_id = payload.node_id or FREE_TOPIC_NODE_ID
    if not storage.node_exists(node_id, db_path):
        raise CapabilityError(ErrorCode.NOT_FOUND, f"知识节点不存在：{node_id}", remedy="留空走自由主题")

    params: dict[str, Any] = {
        "topic": query[:30],
        "query": query[:200],
        "question_count": int(payload.count),
        "difficulty": payload.difficulty,
        "client_invocation_id": payload.client_invocation_id,
    }
    if payload.mode == "outline":
        params["mode"] = "outline"

    task = storage.create_task(
        str(uuid4()),
        actor.user_id,
        node_id,
        "quiz",
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
                "kind": "quiz",
                "resource_id": None,
                "open_url": None,
                "resolve_on": "done",
            },
        },
    )


SPECS = ((SPEC, handler),)
