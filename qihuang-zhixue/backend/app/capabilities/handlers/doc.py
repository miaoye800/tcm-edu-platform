"""``doc.generate``：文稿工坊的对外入口，与 ``ppt.generate`` 同形。

同形的意思是四件事都对得上：

1. 交即返回（202 + task_id + stream_url），写作要十几秒到几十秒，不能同步等；
2. artifact 而不是内嵌正文——调用方拿到 open_url，不是几千字 JSON；
3. 幂等：``client_invocation_id`` 相同且参数相同只受理一次；
4. 工序表与 SSE 事件从 ``resources/constants`` 派生，不另写一份。

**与幻灯的差别只有一处，且必须体现在参数里**：文稿的体裁不是调用方指定，
而是由工坊在 ``design`` 步按主题判定、给用户过目后再落笔。理由：同一个题目，
「讲解」和「学习计划」的章节结构完全不同；让调用方传一个 ``genre`` 字段，
等于把体裁判定挪到一个它看不见 scheme 的地方。

**``materials`` 是本能力独有的入口**：用户带来的原文是唯一能与教材并列的
依据档位，写作时按 ``user_material`` 计入，不进 ``model`` 档。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import Field

from app.auth.models import AuthUser
from app.profile.store import DB_PATH
from app.resources import storage
from app.resources.constants import CURRENT_PIPELINE_VERSION, steps_for
from app.resources.planning import MATERIAL_LIMIT

from ..errors import CapabilityError, ErrorCode
from ..spec import ArtifactSpec, CapabilityResult, CapabilitySpec, ErrorSpec
from . import CapabilityInput

FREE_TOPIC_NODE_ID = "general"
#: 与 ``resources/planning.py`` 的上限一致；多传的直接截断，不报错。
MAX_MATERIALS = 5


class DocGenerateInput(CapabilityInput):
    query: str = Field("", max_length=200, description="要写什么。完整一句比两个字更好：例如「写一篇人参的用法与禁忌讲义」")
    node_id: str = Field("", max_length=64, description="挂到哪个知识节点下；留空则落到自由主题节点")
    materials: list[str] = Field(
        default_factory=list,
        max_length=MAX_MATERIALS,
        description="用户带来的材料（最多 5 段）。会按 user_material 档计入依据，不当作模型补充",
    )
    mode: str = Field("one_shot", description="one_shot = 一路跑完；outline = 谋篇后停下等确认（v3 起确认点在 design 步）")


SPEC = CapabilitySpec(
    name="doc.generate",
    version="1.0",
    kind="job",
    title="生成文稿",
    one_line="把一句写作诉求做成一份分节、标了引文、体裁先确认的文稿",
    use_when=(
        "要一篇文字成品：如「写一篇人参的用法与禁忌」",
        "要讲义/学习计划/操作指南/对比分析：如「整理一份解表药复习提纲」",
        "要长文而不是几句话：如「综述一下十八反的研究现状」",
    ),
    avoid_when=(
        "只要一段口头解释：直接回答更省时间，不必落一份文稿",
        "要一张关系图：用 mindmap.generate——文稿不做树",
        "要讲给别人听、要分页：用 ppt.generate——文稿不做页",
        "要改已有文稿的某一节：那是编辑器的事，不要重新生成整篇",
    ),
    triggers=("文档", "文稿", "文章", "讲义", "提纲", "综述", "写一篇"),
    input_model=DocGenerateInput,
    output_shape={
        "ok": "bool",
        "capability": "str，固定 doc.generate",
        "capability_version": "str",
        "invocation_id": "str，受理号；重放时不变",
        "status": "accepted",
        "replayed": "bool，true 表示这次是重放，task_id 与上次相同",
        "task_id": "str，配合 stream_url 订阅进度",
        "stream_url": "str，GET 后得到 SSE：step_start / plan / design / pause / block / verify / done",
        "artifact": "见 artifact 段；resource_id 与 open_url 在 done 之后才有值",
    },
    audiences=("agent", "frontend", "human"),
    artifact=ArtifactSpec(kind="doc", open_url_template="/resources/{resource_id}", resolve_on="done", evidence_tiers=("textbook", "graph", "model")),
    pipeline=steps_for("doc", CURRENT_PIPELINE_VERSION),
    events=("step_start", "step_done", "plan", "design", "pause", "block", "verify", "done", "error"),
    idempotency=True,
    errors=(
        ErrorSpec(ErrorCode.TOPIC_EMPTY, 422, "没有说要写什么", "在 query 里给一个主题或诉求"),
        ErrorSpec(ErrorCode.INVALID_INPUT, 422, "参数越界", "query ≤200 字；materials ≤5 段"),
        ErrorSpec(ErrorCode.NOT_FOUND, 404, "知识节点不存在", "换一个 node_id，或留空走自由主题"),
        ErrorSpec(ErrorCode.IDEMPOTENCY_MISMATCH, 409, "同一请求号配了不同参数", "换一个 client_invocation_id"),
    ),
    side_effects=("新建一条炼制任务", "完成后在用户资源库落一份文稿"),
    requires=("登录用户", "本地中药学知识库（可缺，缺则全部标为模型补充）"),
    cost="无模型 key 时零调用；有 key 时按节生成",
    latency="通常 15~60 秒，取决于篇幅；无 key 时 3 秒内",
    examples=("写一篇人参的用法与禁忌", "整理一份解表药复习提纲", "对比麻黄与桂枝的临床应用"),
)


def _clean_materials(raw: list[str]) -> list[str]:
    """与 planning.user_materials 同一套收敛：去空白、限长、去空、截断到 5 段。"""

    cleaned = [" ".join(str(item).split())[:MATERIAL_LIMIT] for item in raw or []]
    return [item for item in cleaned if item][:MAX_MATERIALS]


async def handler(payload: DocGenerateInput, actor: AuthUser, *, invocation_id: str, db_path: Any = DB_PATH) -> CapabilityResult:
    query = " ".join(payload.query.split())
    if not query:
        raise CapabilityError(ErrorCode.TOPIC_EMPTY, "请先说要写什么", remedy="在 query 里给一个主题或诉求")

    node_id = payload.node_id or FREE_TOPIC_NODE_ID
    if not storage.node_exists(node_id, db_path):
        raise CapabilityError(ErrorCode.NOT_FOUND, f"知识节点不存在：{node_id}", remedy="留空走自由主题")

    params: dict[str, Any] = {
        # topic 是设计步与方案步骤读的短标题；query 是完整诉求，_task_topic 优先读它。
        "topic": query[:30],
        "query": query[:200],
        "client_invocation_id": payload.client_invocation_id,
    }
    materials = _clean_materials(payload.materials)
    if materials:
        params["materials"] = materials
    if payload.mode == "outline":
        params["mode"] = "outline"

    task = storage.create_task(
        str(uuid4()),
        actor.user_id,
        node_id,
        "doc",
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
                "kind": "doc",
                "resource_id": None,
                "open_url": None,
                "resolve_on": "done",
            },
        },
    )


SPECS = ((SPEC, handler),)
