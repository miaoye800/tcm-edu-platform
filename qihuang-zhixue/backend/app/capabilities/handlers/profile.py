"""``profile.view``：把学习画像摊开给人看和给模型读。

这是一条**只读**能力，回答的是「我现在什么样」而不是「我该学什么」。分开的理由：
「哪块最弱」必须给具体维度与置信度，否则就是一段套话；而「该学什么」要读路径
规划，两者的数据来源与失败模式都不一样，混成一条能力会让调用方拿不准自己在问什么。

**为什么不走 ``get_latest_profile``**：那个入口会跑「用户视角陪审团」（一次模型
调用）来刷新维度，用在对话里的随手一问既慢又贵。这里读的是已落库的最新画像，
并如实报出每个维度的置信度——低置信度就写低置信度，这是本能力的诚实底线。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.auth.models import AuthUser
from app.profile.store import DB_PATH, get_profile
from app.profile.update import profile_to_response

from ..errors import ErrorCode
from ..spec import CapabilityResult, CapabilitySpec, ErrorSpec
from . import CapabilityInput

#: 低于这个置信度的维度不进「最弱」排序——拿一条 0.1 置信度的维度说
#: 「你这块最弱」，等于用噪声下结论。
MIN_CONFIDENCE = 0.25


class ProfileViewInput(CapabilityInput):
    dimension: str = Field("", max_length=40, description="只看某一维（如 knowledge_base / weak_points）；留空看全部")


SPEC = CapabilitySpec(
    name="profile.view",
    version="1.0",
    kind="query",
    title="查看学习画像",
    one_line="读出七个维度的分数、置信度与最弱项，用于「我哪块最弱」这类问题",
    use_when=(
        "问自己的学习状况：如「我的画像里哪块最弱」",
        "问某个维度：如「我的知识基础怎么样」",
        "要看依据：如「你凭什么说我记不牢」",
    ),
    avoid_when=(
        "要学习计划：那是路径规划的活，不是画像",
        "问教材知识：用 kg_* 六能力",
    ),
    triggers=("画像", "我最弱", "我的水平", "我的短板", "学习状况", "我的进度"),
    input_model=ProfileViewInput,
    output_shape={
        "ok": "bool",
        "capability": "str，固定 profile.view",
        "has_profile": "bool，false 表示还没有任何画像证据",
        "dimensions": "list，每项含 key / label / score / confidence / provenance",
        "weakest": "list，分数最低且置信度达标的 1~2 维",
        "weak_points": "list，带出现频次的薄弱知识点标签",
        "summary": "str，一句可直接念给用户听的话",
    },
    audiences=("agent", "human"),
    errors=(ErrorSpec(ErrorCode.INVALID_INPUT, 422, "维度名不认识", "留空看全部，或传 knowledge_base / weak_points 等"),),
    side_effects=(),
    requires=("登录用户",),
    cost="纯本地读取，无模型调用",
    latency="毫秒级",
    examples=("我的画像里哪块最弱", "我的知识基础怎么样"),
)


def _confidence_of(axis: dict[str, Any]) -> float:
    try:
        return float(axis.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


async def handler(payload: ProfileViewInput, actor: AuthUser, *, invocation_id: str, db_path: Any = None) -> CapabilityResult:
    """读最新画像并压成一段可引用的事实。"""

    profile = get_profile(actor.user_id, db_path or DB_PATH)
    response = profile_to_response(profile)
    axes = [axis for axis in (response.get("radar") or {}).get("axes", []) if isinstance(axis, dict)]

    wanted = payload.dimension.strip()
    if wanted:
        axes = [axis for axis in axes if axis.get("key") == wanted]

    dimensions = [
        {
            "key": str(axis.get("key") or ""),
            "label": str(axis.get("label") or ""),
            "score": axis.get("value"),
            "confidence": round(_confidence_of(axis), 2),
            "provenance": str(axis.get("provenance") or "unknown"),
        }
        for axis in axes
    ]

    # 只在「看得见」的维度里排名次：置信度不达标的维度要么缺证据，要么刚建立，
    # 把它们排进最弱项会把用户引到一条没有数据支撑的路上。
    rankable = [item for item in dimensions if item["score"] is not None and item["confidence"] >= MIN_CONFIDENCE]
    weakest = sorted(rankable, key=lambda item: float(item["score"]))[:2]

    weak_points_raw = (response.get("weak_points") or {}).get("items") or []
    weak_points = [
        {"label": str(item.get("label") or ""), "strength": int(item.get("strength") or 1)}
        for item in weak_points_raw
        if isinstance(item, dict)
    ][:5]

    has_profile = bool(rankable) or bool(weak_points)
    if not has_profile:
        summary = "画像里还没有足够的证据：做过一次测评或几道练习之后，我才说得上哪块弱。"
    elif weakest:
        detail = "、".join(f"{item['label']}（{item['score']}）" for item in weakest)
        summary = f"目前最弱的是 {detail}；依据置信度 {weakest[0]['confidence']:.2f}，做题后会更新。"
    else:
        summary = "各维度都有分数，但置信度还不够，暂时不排最弱项。"

    return CapabilityResult(
        http_status=200,
        body={
            "ok": True,
            "capability": SPEC.name,
            "capability_version": SPEC.version,
            "invocation_id": invocation_id,
            "has_profile": has_profile,
            "dimensions": dimensions,
            "weakest": weakest,
            "weak_points": weak_points,
            "summary": summary,
        },
    )


SPECS = ((SPEC, handler),)
