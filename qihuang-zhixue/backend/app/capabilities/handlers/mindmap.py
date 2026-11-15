"""导图能力：generate / get / present / expand。

四个能力对应四种真实动作，不是把 REST 端点换个名字：

- ``generate`` 交即返回（202 + task_id + stream_url），因为生成要 10~20 秒；
- ``get`` 问「这张图现在什么样」，答案里带三档依据与问法，供对话回答「靠不靠得住」；
- ``present`` 只改外观（结构/配色），同步 200，因为它不改内容；
- ``expand`` 给一个分支补叶子，同步 200。

**artifact 而不是内嵌内容**：调用方拿到的是 ``open_url``，不是整棵树。
把 100 个节点塞进对话上下文，既贵又没用——用户要的是点开看。
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from app.auth.models import AuthUser
from app.mindmap.appearance import (
    ALLOWED_STRUCTURE_IDS,
    ALLOWED_THEME_IDS,
    DEFAULT_STRUCTURE_ID,
    DEFAULT_THEME_ID,
    normalize_structure,
    normalize_theme,
)
from app.mindmap.brief import parse_brief
from app.mindmap.params import validate_mindmap_params
from app.profile.store import DB_PATH
from app.resources import service as resource_service
from app.resources import storage
from app.resources.constants import CURRENT_PIPELINE_VERSION, steps_for

from ..errors import CapabilityError, ErrorCode
from ..spec import ArtifactSpec, CapabilityResult, CapabilitySpec, ErrorSpec
from . import CapabilityInput

#: 自由主题统一挂在这个真实节点下：``user_resource.node_id`` 是外键，不能自造。
FREE_TOPIC_NODE_ID = "general"


class MindmapGenerateInput(CapabilityInput):
    query: str = Field("", max_length=200, description="要画什么。可以是单味药、分类、对比、关系或一句自然语言提问")
    node_id: str = Field("", max_length=64, description="挂到哪个知识节点下；留空则落到自由主题节点")
    depth: int = Field(3, ge=2, le=5, description="层数，2~5（句子里写了「四层」会覆盖此值）")
    branches: int = Field(6, ge=3, le=8, description="一级分支上限，3~8")
    style: Literal["exam", "understand", "compare"] = Field("understand", description="写法取向：应试 / 理解 / 对比")
    focus: str = Field("", max_length=60, description="想重点看的部分，可留空")
    structure: str = Field("auto", description="logic-right / mind-both / org-down / fishbone；auto = 按问法建议")
    theme: str = Field("", description="indigo / verdant / violet / inkCream；留空用默认")


class MindmapGetInput(CapabilityInput):
    resource_id: str = Field("", max_length=64, description="导图资源 id")


class MindmapPresentInput(CapabilityInput):
    resource_id: str = Field("", max_length=64, description="导图资源 id")
    structure: str = Field("", description="要换成哪种结构；留空不改")
    theme_id: str = Field("", description="要换成哪套配色；留空不改")


class MindmapExpandInput(CapabilityInput):
    resource_id: str = Field("", max_length=64, description="导图资源 id")
    node_path: list[str] = Field(default_factory=list, description="从根到目标分支的 id 路径")


_GENERATE_ERRORS = (
    ErrorSpec(ErrorCode.TOPIC_EMPTY, 422, "没有说要画什么", "在 query 里给一个主题，如「麻黄」或「解表药的分类」"),
    ErrorSpec(ErrorCode.INVALID_INPUT, 422, "参数越界", "层数 2~5、分支 3~8"),
    ErrorSpec(ErrorCode.NOT_FOUND, 404, "知识节点不存在", "换一个 node_id，或留空走自由主题"),
    ErrorSpec(ErrorCode.STRUCTURE_UNKNOWN, 422, "不认识这种结构", f"可选：{'、'.join(sorted(ALLOWED_STRUCTURE_IDS))}，或 auto"),
    ErrorSpec(ErrorCode.THEME_UNKNOWN, 422, "不认识这套配色", f"可选：{'、'.join(sorted(ALLOWED_THEME_IDS))}"),
    ErrorSpec(ErrorCode.IDEMPOTENCY_MISMATCH, 409, "同一请求号配了不同参数", "换一个 client_invocation_id"),
)

_ARTIFACT = ArtifactSpec(kind="mindmap", open_url_template="/mindmap?resource={resource_id}", resolve_on="done")

#: 工序表从 ``resources/constants`` 派生，不再手写第二份。
#:
#: 升级前这里是一份 9 步的手抄表：少了 ``plan``、顺序与后端不同、也没有 ``pause``
#: 事件——对「读契约编程」的调用方来说，用户确认闸门是隐形的。现在契约与 SSE
#: 读的是同一个 ``steps_for``，一致性由构造方式保证，不再靠测试守着。
_PIPELINE = steps_for("mindmap", CURRENT_PIPELINE_VERSION)


SPEC_GENERATE = CapabilitySpec(
    name="mindmap.generate",
    version="2.0",
    kind="job",
    title="生成思维导图",
    one_line="把一句中药学主题或问题，长成一张可编辑、标了依据来源的导图",
    use_when=(
        "要一张图看全某个主题：如「画一张麻黄的知识导图」",
        "要看清一类药的构成：如「解表药的分类」",
        "要摆开两味药的差异：如「麻黄和桂枝有什么区别」",
        "要从功效倒查药：如「哪些药能安神」",
        "要梳理硬性配伍禁忌：如「十八反有哪些」",
    ),
    avoid_when=(
        "只问一句话的答案：如「麻黄性味是什么」——直接答比出图快",
        "问教材里没有的方药：如「紫河车有什么功效」——会整棵树退化成推断，不如直说查不到",
        "要改已有导图的结构或配色：用 mindmap.present，不要重新生成",
    ),
    triggers=("导图", "画一张", "思维导图", "分类", "区别", "鉴别", "异同", "哪些药", "有哪些", "配", "组成"),
    input_model=MindmapGenerateInput,
    output_shape={
        "ok": "bool",
        "capability": "str，固定 mindmap.generate",
        "capability_version": "str，如 2.0",
        "invocation_id": "str，受理号；重放时不变",
        "status": "accepted",
        "replayed": "bool，true 表示这次是重放，task_id 与上次相同",
        "task_id": "str，配合 stream_url 订阅进度",
        "stream_url": "str，GET 后得到 SSE：step_start / node / mindmap_verify / done",
        "artifact": "见 artifact 段；resource_id 与 open_url 在 done 之后才有值",
    },
    audiences=("agent", "frontend", "human"),
    artifact=_ARTIFACT,
    pipeline=_PIPELINE,
    events=("step_start", "step_done", "plan", "pause", "node", "mindmap_verify", "done", "error"),
    idempotency=True,
    errors=_GENERATE_ERRORS,
    side_effects=("新建一条炼制任务", "完成后在用户资源库落一张导图"),
    requires=("登录用户", "本地中药学知识库"),
    cost="无模型 key 时零调用；有 key 时一次结构化生成",
    latency="通常 6~20 秒；无 key 时 2 秒内",
    examples=("解表药的分类", "麻黄与桂枝的区别", "十八反有哪些", "风寒表证用什么药"),
)

SPEC_GET = CapabilitySpec(
    name="mindmap.get",
    version="1.0",
    kind="query",
    title="读一张导图",
    one_line="给出这张图的规模、三档依据占比与当初的问法判定",
    use_when=(
        "要回答「这张图靠不靠得住」",
        "要接着改这张图之前，先看清它现在什么样",
    ),
    avoid_when=("要整棵树的内容：那是前端的事，读 content 会白烧上下文",),
    triggers=("这张图", "导图多大", "靠不靠得住", "依据"),
    input_model=MindmapGetInput,
    output_shape={
        "ok": "bool",
        "resource_id": "str",
        "title": "str",
        "nodes": "int，节点总数",
        "depth": "int",
        "evidence": "textbook / graph / model 三档节点数，和为节点总数",
        "intent": "问法判定结果：kind / label / note / degraded",
        "structure": "当前结构 id",
        "theme_id": "当前配色 id",
        "open_url": "str",
    },
    audiences=("agent", "frontend"),
    artifact=_ARTIFACT,
    errors=(ErrorSpec(ErrorCode.RESOURCE_NOT_FOUND, 404, "这份导图不存在", "确认 resource_id"),),
    requires=("登录用户",),
    latency="毫秒级",
)

SPEC_PRESENT = CapabilitySpec(
    name="mindmap.present",
    version="1.0",
    kind="command",
    title="换结构或配色",
    one_line="只改这张图的样子，不动它的内容",
    use_when=(
        "要换一种排布：如「换成鱼骨图」",
        "要换一套配色：如「用青绿那套」",
    ),
    avoid_when=("要改内容或补节点：那不是 present 的事，重新生成或手动改",),
    triggers=("换成", "改成", "配色", "鱼骨", "向下", "发散", "颜色"),
    input_model=MindmapPresentInput,
    output_shape={"ok": "bool", "resource_id": "str", "structure": "str", "theme_id": "str", "changed": "list[str]"},
    audiences=("agent", "frontend"),
    artifact=_ARTIFACT,
    errors=(
        ErrorSpec(ErrorCode.RESOURCE_NOT_FOUND, 404, "这份导图不存在", "确认 resource_id"),
        ErrorSpec(ErrorCode.STRUCTURE_UNKNOWN, 422, "不认识这种结构", f"可选：{'、'.join(sorted(ALLOWED_STRUCTURE_IDS))}"),
        ErrorSpec(ErrorCode.THEME_UNKNOWN, 422, "不认识这套配色", f"可选：{'、'.join(sorted(ALLOWED_THEME_IDS))}"),
    ),
    side_effects=("改写导图的 meta（结构/配色），不改树、不动版本号"),
    requires=("登录用户",),
    latency="毫秒级",
)

SPEC_EXPAND = CapabilitySpec(
    name="mindmap.expand",
    version="1.0",
    kind="command",
    title="给一个分支补叶子",
    one_line="某个分支还空着，按教材条目给它长出细目",
    use_when=("某个分支只有标题没有内容：如「把「配伍应用」展开」",),
    avoid_when=("整张图都没生成好：先 generate，再挑分支补",),
    triggers=("展开", "补全", "细化", "加点"),
    input_model=MindmapExpandInput,
    output_shape={"ok": "bool", "node_path": "list[str]", "added": "list，新增节点", "idempotent": "bool，已有子节点时 true"},
    audiences=("agent",),
    artifact=_ARTIFACT,
    errors=(
        ErrorSpec(ErrorCode.RESOURCE_NOT_FOUND, 404, "这份导图不存在", "确认 resource_id"),
        ErrorSpec(ErrorCode.INVALID_INPUT, 422, "分支路径不对", "node_path 从根节点 id 开始"),
    ),
    side_effects=("直接改写导图内容（不推进版本号，与既有扩写接口一致）"),
    requires=("登录用户", "本地中药学知识库"),
    latency="1~3 秒",
)


# --------------------------------------------------------------------------- #
# handlers
# --------------------------------------------------------------------------- #


def _resolve_structure(value: str) -> str:
    """``auto`` 与空值都表示「按问法建议」，交给下游 intent.structure_hint。"""

    text = (value or "").strip()
    if not text or text == "auto":
        return ""
    if text not in ALLOWED_STRUCTURE_IDS:
        raise CapabilityError(ErrorCode.STRUCTURE_UNKNOWN, f"不认识的结构：{text}")
    return text


def _resolve_theme(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if text not in ALLOWED_THEME_IDS:
        raise CapabilityError(ErrorCode.THEME_UNKNOWN, f"不认识的配色：{text}")
    return text


async def generate(payload: MindmapGenerateInput, actor: AuthUser, *, invocation_id: str, db_path: Any = DB_PATH) -> CapabilityResult:
    query = " ".join(payload.query.split())
    if not query:
        raise CapabilityError(ErrorCode.TOPIC_EMPTY, "请先说要画什么", remedy="在 query 里给一个主题")
    structure = _resolve_structure(payload.structure)
    theme = _resolve_theme(payload.theme)

    # 「必须包含四层」「每个分支三条」「要口诀」「用鱼骨」这些要求都写在句子里。
    # 接口参数只是默认值：用户当场说的话优先，否则那个输入框永远只是装饰。
    brief = parse_brief(f"{query} {payload.focus}".strip())
    overrides = brief.to_params()
    if brief.structure and not structure:
        # 句子里点了结构才算数；接口显式传了就以接口为准（那是用户点的按钮）。
        structure = brief.structure

    node_id = payload.node_id or FREE_TOPIC_NODE_ID
    if not storage.node_exists(node_id, db_path):
        raise CapabilityError(ErrorCode.NOT_FOUND, f"知识节点不存在：{node_id}", remedy="留空走自由主题")

    params: dict[str, Any] = {
        "topic": query[:30],
        # query 是完整提问：截断后的 topic 只用来当标题，辨问法要看全句。
        "query": query[:120],
        "depth": overrides.get("depth", payload.depth),
        "branches": overrides.get("branches", payload.branches),
        "style": overrides.get("style", payload.style),
        "focus": payload.focus,
        "client_invocation_id": payload.client_invocation_id,
    }
    if not brief.empty:
        # 提示词要的是逐条要求，不是原始句子；顺带存一份人话说明给对话栏用。
        params["brief"] = brief.prompt_lines()
        params["brief_note"] = brief.describe()
        if brief.leaves_min:
            params["leaves_min"] = brief.leaves_min
    if structure:
        params["structure"] = structure
    if theme:
        params["theme"] = theme
    try:
        validate_mindmap_params(params)
    except ValueError as exc:
        raise CapabilityError(ErrorCode.INVALID_INPUT, str(exc)) from exc

    task = storage.create_task(
        str(uuid4()),
        actor.user_id,
        node_id,
        "mindmap",
        db_path,
        meta={"params": params, "pipeline_version": 2, "invocation_id": invocation_id},
    )
    return CapabilityResult(
        http_status=202,
        body={
            "ok": True,
            "capability": SPEC_GENERATE.name,
            "capability_version": SPEC_GENERATE.version,
            "invocation_id": invocation_id,
            "status": "accepted",
            "replayed": False,
            "task_id": task["task_id"],
            "stream_url": f"/api/resource/tasks/{task['task_id']}/stream",
            "artifact": {
                "kind": "mindmap",
                "resource_id": None,
                "open_url": None,
                "structure": structure or "auto",
                "theme_id": theme or DEFAULT_THEME_ID,
                "resolve_on": "done",
            },
        },
    )


def _load_mindmap(resource_id: str, actor: AuthUser, db_path: Any) -> dict[str, Any]:
    resource = storage.get_resource(resource_id, actor.user_id, db_path) if resource_id else None
    if resource is None or resource.get("type") != "mindmap":
        raise CapabilityError(ErrorCode.RESOURCE_NOT_FOUND, "这份导图不存在", remedy="确认 resource_id")
    return resource


async def get(payload: MindmapGetInput, actor: AuthUser, *, invocation_id: str, db_path: Any = DB_PATH) -> CapabilityResult:
    resource = _load_mindmap(payload.resource_id, actor, db_path)
    meta = resource.get("meta") or {}
    content = resource.get("content") or {}
    # 只数不改：``_mindmap_verify`` 会裁剪越界依据，那是生成时的动作，
    # 读接口碰它等于一边读一边写。
    evidence = resource_service._mindmap_evidence_stats(content)
    return CapabilityResult(
        http_status=200,
        body={
            "ok": True,
            "resource_id": resource["id"],
            "title": resource.get("title", ""),
            "nodes": sum(evidence.values()),
            "depth": _depth_of(content),
            "evidence": evidence,
            "intent": {
                "kind": meta.get("intent", ""),
                "label": meta.get("intent_label", ""),
                "note": meta.get("intent_note", ""),
                "degraded": bool(meta.get("degraded")),
            },
            "structure": meta.get("mindmap_structure") or DEFAULT_STRUCTURE_ID,
            "theme_id": meta.get("mindmap_theme") or DEFAULT_THEME_ID,
            "open_url": f"/mindmap?resource={resource['id']}",
        },
    )


async def present(payload: MindmapPresentInput, actor: AuthUser, *, invocation_id: str, db_path: Any = DB_PATH) -> CapabilityResult:
    resource = _load_mindmap(payload.resource_id, actor, db_path)
    meta = resource.get("meta") or {}
    current_structure = str(meta.get("mindmap_structure") or DEFAULT_STRUCTURE_ID)
    current_theme = str(meta.get("mindmap_theme") or DEFAULT_THEME_ID)

    structure = current_structure
    if (payload.structure or "").strip():
        structure = _resolve_structure(payload.structure)
    theme_id = current_theme
    if (payload.theme_id or "").strip():
        text = payload.theme_id.strip()
        if text not in ALLOWED_THEME_IDS:
            raise CapabilityError(ErrorCode.THEME_UNKNOWN, f"不认识的配色：{text}")
        theme_id = text

    changed = [name for name, before, after in (("structure", current_structure, structure), ("theme_id", current_theme, theme_id)) if before != after]
    if changed:
        storage.update_mindmap_appearance(resource["id"], actor.user_id, structure=structure, theme_id=theme_id, db_path=db_path)
    # 没变也返回 200：命令的语义是「让它变成这样」，本来就是幂等的。
    return CapabilityResult(
        http_status=200,
        body={
            "ok": True,
            "resource_id": resource["id"],
            "structure": structure,
            "theme_id": theme_id,
            "changed": changed,
            "open_url": f"/mindmap?resource={resource['id']}",
        },
    )


async def expand(payload: MindmapExpandInput, actor: AuthUser, *, invocation_id: str, db_path: Any = DB_PATH) -> CapabilityResult:
    from app.api.resources import expand_mindmap  # 复用既有实现，不复制一份扩写逻辑
    from app.mindmap.schemas import ExpandRequest

    resource = _load_mindmap(payload.resource_id, actor, db_path)
    body = ExpandRequest(node_path=payload.node_path)
    # 既有端点返回 pydantic 模型，这里只做形状转换。
    result = await expand_mindmap(resource["id"], body, actor, db_path)
    return CapabilityResult(
        http_status=200,
        body={
            "ok": True,
            "resource_id": resource["id"],
            "node_path": list(result.node_path),
            "added": [node.model_dump(mode="json") for node in result.added_nodes],
            "idempotent": bool(result.idempotent),
        },
    )


def _depth_of(content: dict[str, Any]) -> int:
    root = (content.get("mindmap_data") or {}).get("nodeData") or {}
    deepest = 0
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        if not isinstance(node, dict):
            continue
        deepest = max(deepest, depth)
        stack.extend((child, depth + 1) for child in node.get("children") or [])
    return deepest


SPECS = (
    (SPEC_GENERATE, generate),
    (SPEC_GET, get),
    (SPEC_PRESENT, present),
    (SPEC_EXPAND, expand),
)
