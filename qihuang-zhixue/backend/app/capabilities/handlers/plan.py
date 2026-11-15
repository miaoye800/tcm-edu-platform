"""``plan.create``：把「我想学什么、多久之后要考」接成一份学习路线。

**这条能力的存在理由，是补上一个对话里真实存在的缺口**：用户说「帮我做一份计划，
我想学习中药学，两年后考研」时，生成工坊（导图/幻灯/文稿/试卷/漫剧）一条都对不上，
``profile.view`` 也只是「我现在什么样」。没有这条能力，模型只能把用户推回
「你可以做导图、做幻灯、写文稿」——那不是回答，是把问题原样退回给用户。

规划本身不在这里实现，一律转调 ``app.path.service.create_l1_plan``：路径算法、
依赖排序、阶段划分、落库版本都归它管。**本文件只做两件事**：把「两年」翻成
``TimeBudget``，把返回的结构翻成人话（id → 名称、节点 → 可读的学习项）。

**诚实边界**（写进 spec，也写进输出）：
- ``goal`` 里点名的学科就是**本次范围**（硬边界，在裁域阶段生效，不是写给模型看的一句中文），
  点名的期限/考试意图会翻成 breadth/pace/depth；但阶段之间的**先后**恒由先修依赖决定，
  不会因为用户说"先学清热"就把清热药提到总论前面；
- 图里没有的科目（如日语）排不出来，``scope`` 会写出实际覆盖到哪几门，不含糊其辞；
- 没有做过测评、没有掌握度数据时，能排出阶段骨架但排不出具体知识点，
  这时 ``has_nodes`` 为 false，summary 直接说「先做一次测评」，不假装计划已经齐了。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.auth.models import AuthUser
from app.path.clarify import named_subjects
from app.path.service import create_l1_plan, create_l2_plan
from app.path.store import list_nodes
from app.path.subjects import subject_of, title_of
from app.profile.schema import TimeBudget
from app.profile.store import DB_PATH

from ..errors import CapabilityError, ErrorCode
from ..spec import ArtifactSpec, CapabilityResult, CapabilitySpec, ErrorSpec
from . import CapabilityInput

#: 输出里阶段与知识点的条数上限。对话回答要的是「从哪开始、分几步」，
#: 不是把整棵树念一遍——念完没人记得住，还挤掉了模型该说的那句话。
MAX_STAGES = 6
MAX_NODES = 10


class PlanCreateInput(CapabilityInput):
    goal: str = Field(
        "",
        max_length=200,
        description=(
            "用户原话，**必须带上他点名要学的科目**与期限，例如「先中药，15 天后期末考」。"
            "科目就是本次规划的范围，认不出来就会退回六门课全排；不要概括成「备考」两个字"
        ),
    )
    total_days: int | None = Field(None, ge=1, le=3650, description="总共可用多少天。两年写 730；留空用画像里的默认预算")
    daily_minutes: int | None = Field(None, ge=1, le=600, description="每天能投入多少分钟；留空用画像里的默认预算")


SPEC = CapabilitySpec(
    name="plan.create",
    version="1.1",
    kind="query",
    title="排一份学习路线",
    one_line="按知识依赖与可用时间排出先学什么、后学什么；不是查资料，是排顺序",
    use_when=(
        "用户要一份学习计划：如「帮我做个计划，我想学习中药学，两年后考研」",
        "问该从哪开始：如「我基础差，先把哪几块补上」",
        "给了期限：如「三个月内过一遍方剂」",
    ),
    avoid_when=(
        "只问某个知识点：用 kg_* 直接答",
        "要看自己现在什么水平：用 profile.view——那是画像，不是路线",
        "要一份能拿走的材料：用 doc.generate / mindmap.generate",
    ),
    triggers=("学习计划", "复习计划", "规划", "从哪开始", "先学什么", "考研", "备考", "怎么安排"),
    input_model=PlanCreateInput,
    output_shape={
        "ok": "bool",
        "capability": "str，固定 plan.create",
        "scope": "dict，requested（用户点名的学科）/ covered（实际排到的学科）/ scoped（是否按学科裁过域）/ domain_count",
        "has_nodes": "bool，false 表示第一个阶段的展开没成功，只有阶段路线",
        "stages": f"list，最多 {MAX_STAGES} 项，含 name / order / why",
        "start_stage": "str，第一个阶段的名称",
        "start_nodes": f"list，最多 {MAX_NODES} 项，第一阶段里具体先学哪几个点",
        "total_stages": "int，阶段总数（可能多于 stages 里的条数）",
        "fallback": "bool，true 表示路径算法退到了保守默认",
        "summary": "str，一句可直接念给用户听的话",
    },
    audiences=("agent", "human"),
    # 计划本身不是"任务"，是一份可以点开的路线：给它一张产物卡，
    # 否则对话里排完计划只剩一行文字，用户还得自己找入口（P0-4）。
    artifact=ArtifactSpec(kind="path", open_url_template="/path?focus={resource_id}"),
    errors=(
        ErrorSpec(ErrorCode.INVALID_INPUT, 422, "时间预算不合法", "total_days 取 1~3650，daily_minutes 取 1~600"),
        ErrorSpec(ErrorCode.CONFLICT, 409, "规划没能完成", "先做一次测评，或稍后重试"),
    ),
    side_effects=("存一版 L1 路径计划", "展开第一个阶段并存一版 L2 计划", "为计划里的知识点初始化学习状态"),
    requires=("登录用户", "知识树"),
    cost="有模型 key 时一次模型调用；无 key 时按依赖与掌握度直接排",
    latency="通常 5~20 秒",
    examples=("帮我做个计划，我想学习中药学，两年后考研", "三个月内过一遍方剂，怎么安排"),
)


def _names(db_path: Any) -> dict[str, dict[str, Any]]:
    """id → 节点。整棵树一次读完，比按 id 逐个查库便宜。"""

    try:
        return {str(node.get("id") or ""): node for node in list_nodes(db_path=db_path) if isinstance(node, dict)}
    except Exception:  # noqa: BLE001 - 读不到名字不该让整条能力炸掉
        return {}


def _budget_of(payload: PlanCreateInput, user_id: str, path: Any) -> TimeBudget | None:
    """只在用户给了数时覆盖画像里的预算；没给就返回 None，让算法用默认值。

    「两年后考研」只说了跨度，没说每天学多久。这时只覆盖 ``total_days``，
    每天时长沿用用户自己的画像设置——那是他的作息，不该由我们替他定。
    """

    days = payload.total_days
    minutes = payload.daily_minutes
    if days is None and minutes is None:
        return None
    if days is None:
        raise CapabilityError(ErrorCode.INVALID_INPUT, "给了每天时长，但没说总共多少天", remedy="再给一个 total_days，例如 730")

    if minutes is None:
        minutes = 90
        try:
            from app.profile.store import get_profile

            minutes = int(get_profile(user_id, path).planning_params.time_budget.daily_minutes or 90)
        except Exception:  # noqa: BLE001 - 画像读不到就用一个保守值，不拦这条能力
            pass

    return TimeBudget(total_days=max(1, int(days)), daily_minutes=max(1, int(minutes)))


async def handler(payload: PlanCreateInput, actor: AuthUser, *, invocation_id: str, db_path: Any = None) -> CapabilityResult:
    path = db_path or DB_PATH
    goal = " ".join(payload.goal.split())

    try:
        # ``goal`` 必须真的进规划，不能只用来拼摘要。以前这里只传 time_budget，
        # 于是「先中药，15 天后期末考」这句话对算法完全不可见：范围退回全图、
        # 计划参数退回画像默认值，用户拿到的是从「中医学的哲学基础」起步的
        # 85 个阶段——他要的是中药学，系统给他的是六门课的先修链。
        result = await create_l1_plan(
            actor.user_id,
            db_path=path,
            time_budget=_budget_of(payload, actor.user_id, path),
            request_text=goal,
        )
    except CapabilityError:
        raise
    except Exception as exc:  # noqa: BLE001 - 规划失败要说人话，不能把栈丢给对话
        raise CapabilityError(ErrorCode.CONFLICT, f"规划没能完成：{type(exc).__name__}", remedy="先做一次测评，或稍后重试") from exc

    catalog = _names(path)
    stage_rows = [
        {
            "name": str(catalog.get(stage.domain_id, {}).get("name") or stage.domain_id),
            "order": int(stage.order),
            # 不搬 ``stage.reason`` 原文：那是算法的推理日志（「先修图topo_sort第1位…
            # confidence=0.00<0.5」），模型会照着念给用户听。这里只留一句人话。
            "why": f"按先修顺序排在第 {int(stage.order)} 位",
        }
        for stage in sorted(result.stages, key=lambda item: int(item.order))
    ]

    # 范围要单独交出来，别让模型从阶段名字里猜：用户说「中药学 + 日语」时，
    # 图里只有中药学，模型得知道这份计划覆盖到哪儿为止，才不会把没排的部分
    # 说成"已经排好了"。
    named = [subject.id for subject in named_subjects(goal)]
    covered = sorted({subject_of(stage.domain_id) for stage in result.stages} - {""})
    scoped = bool(named) and set(covered) <= set(named)
    scope = {
        "requested": [{"subject_id": item, "name": title_of(item)} for item in named],
        "covered": [{"subject_id": item, "name": title_of(item)} for item in covered],
        # true = 这次真的按学科裁了域；false = 没点名学科或认不出，走的是全图
        "scoped": scoped,
        "domain_count": len(stage_rows),
    }

    # L1 只排阶段，知识点要靠 ``expand_domain`` 逐域展开（见 ``planner.plan_l1``
    # 里 ``PathPlan(stages=..., nodes=[])``）。用户问「从哪开始」时，光有 22 个
    # 阶段名没用——所以顺手展开第一个阶段，把「第一步先学哪几个点」一起给出来。
    start_nodes: list[dict[str, Any]] = []
    start_node_id = ""
    if stage_rows:
        first_domain = sorted(result.stages, key=lambda item: int(item.order))[0].domain_id
        try:
            expanded = await create_l2_plan(actor.user_id, first_domain, db_path=path)
        except Exception:  # noqa: BLE001 - 展开失败只是少一段细节，不该让整条能力失败
            expanded = None
        if expanded is not None:
            ordered_nodes = sorted(expanded.nodes, key=lambda item: int(item.seq))
            start_node_id = ordered_nodes[0].node_id if ordered_nodes else ""
            start_nodes = [
                {
                    "name": str(catalog.get(node.node_id, {}).get("name") or node.node_id),
                    "resource_hint": node.resource_hint,
                    "highlight": node.highlight,
                    "target_mastery": round(float(node.target_mastery), 2),
                }
                for node in sorted(expanded.nodes, key=lambda item: int(item.seq))
            ]

    has_nodes = bool(start_nodes)
    if not stage_rows:
        summary = "这份路线没排出来：知识树里没有可用的阶段。"
    else:
        head = "、".join(item["name"] for item in start_nodes[:3])
        # 说清"这份路线到底覆盖哪儿"。用户同时提了图外的内容（"中药学 + 日语"）时，
        # 不写这一句他就会以为日语也排进去了。
        if scoped:
            names = "、".join(f"《{title_of(item)}》" for item in covered)
            lead = f"共 {len(stage_rows)} 个阶段，范围只到{names}。"
        else:
            lead = f"全程 {len(stage_rows)} 个阶段，按先修顺序排。"
        summary = (
            f"从「{stage_rows[0]['name']}」开始，{lead}"
            + (f"第一步先学 {head} 这几项。" if head else "")
            + f"时间按{_budget_text(payload)}摊。"
        )

    if goal:
        # 规则兜底会把整句话塞进 goal。摘要里只取前半句，不然一句话回答变成抄录。
        summary = f"你要的是「{goal[:40]}」。{summary}"

    return CapabilityResult(
        http_status=200,
        body={
            "ok": True,
            "capability": SPEC.name,
            "capability_version": SPEC.version,
            "invocation_id": invocation_id,
            "goal": goal,
            "scope": scope,
            "has_nodes": has_nodes,
            "stages": stage_rows[:MAX_STAGES],
            "start_stage": stage_rows[0]["name"] if stage_rows else "",
            "start_nodes": start_nodes[:MAX_NODES],
            "total_stages": len(stage_rows),
            "fallback": bool(result.fallback),
            "summary": summary,
        },
    )


def _budget_text(payload: PlanCreateInput) -> str:
    if payload.total_days:
        return f" {int(payload.total_days)} 天"
    return "画像里的默认预算"


SPECS = ((SPEC, handler),)
