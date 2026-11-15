"""``path.focus``：把用户说出的知识点定位到地图上，并给一个可点的动作。

**不自动跳页**。用户问「合谷在哪」时，他可能只是想听一句解释；把他强行
拽到地图页，等于替他决定"你现在要看地图"。所以这条能力只回一个动作按钮：
「在地图上看『合谷』→ /path?focus=...」。点不点由他决定。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.auth.models import AuthUser
from app.path.focus import resolve
from app.profile.store import DB_PATH

from ..errors import CapabilityError, ErrorCode
from ..spec import CapabilityResult, CapabilitySpec, ErrorSpec
from . import CapabilityInput


class PathFocusInput(CapabilityInput):
    query: str = Field("", max_length=200, description="用户原话里的知识点：麻黄、合谷、清热药，也可以直接给 node_id")
    node_id: str = Field("", max_length=120, description="已知的节点 id；比 query 优先")


SPEC = CapabilitySpec(
    name="path.focus",
    version="1.0",
    kind="query",
    title="在地图上定位",
    one_line="把「我想学麻黄」「合谷在哪」定位到地图上的那一关，并给出可点的跳转",
    use_when=(
        "用户问某个知识点在地图/经脉上的位置：如「合谷在哪」「麻黄在第几关」",
        "用户说想学某个具体内容：如「我想学麻黄」「带我去看清热药」",
    ),
    avoid_when=(
        "要解释知识本身：用 kg.lookup / kg.ask，定位不等于讲解",
        "要规划整条路线：用 plan.create",
        "说法太泛（「我想学中医」）：先问清是哪一块，别硬定位",
    ),
    triggers=("在哪", "在哪里", "第几关", "哪个穴", "我想学", "带我去看"),
    input_model=PathFocusInput,
    output_shape={
        "ok": "bool，false 表示没定位到，请看 candidates",
        "node_id": "str",
        "node_name": "str",
        "layer_id": "str，学科图层",
        "acupoint": "dict，穴名与代号",
        "open_url": "str，/path?focus=…&layer=…",
        "confidence": "float，0~1",
        "candidates": "list，没定位到时给的候选（最多 5 条）",
        "ui_actions": "list，前端渲染成可点按钮，不自动跳页",
    },
    audiences=("agent", "frontend", "human"),
    errors=(ErrorSpec(ErrorCode.INVALID_INPUT, 422, "没说要找什么", "query 或 node_id 至少给一个"),),
    side_effects=(),
    requires=("登录用户", "知识树"),
    cost="无模型调用",
    latency="通常 < 50 毫秒",
    examples=("合谷在哪", "我想学麻黄", "带我去看清热药"),
)


async def handler(payload: PathFocusInput, actor: AuthUser, *, invocation_id: str, db_path: Any = None) -> CapabilityResult:
    path = db_path or DB_PATH
    query = (payload.query or "").strip()
    if not query and not payload.node_id:
        raise CapabilityError(ErrorCode.INVALID_INPUT, "没说要找什么", remedy="在 query 里给一个知识点名称")

    result = resolve(payload.node_id or query, db_path=path)
    body: dict[str, Any] = {
        "ok": bool(result["ok"]),
        "capability": SPEC.name,
        "capability_version": SPEC.version,
        "invocation_id": invocation_id,
        **result,
    }
    if result["ok"]:
        name = result["node_name"]
        body["ui_actions"] = [{"label": f"在地图上看「{name}」", "url": result["open_url"]}]
        body["summary"] = f"「{name}」在地图上找到了（置信度 {result['confidence']:.2f}）。"
    else:
        names = "、".join(item["name"] for item in result["candidates"][:3])
        body["ui_actions"] = [
            {"label": f"在地图上看「{item['name']}」", "url": f"/path?focus={item['node_id']}"}
            for item in result["candidates"][:3]
        ]
        body["summary"] = f"没定位到「{result['query']}」" + (f"；是不是{names}？" if names else "。")
    return CapabilityResult(http_status=200, body=body)


SPECS = ((SPEC, handler),)

__all__ = ["SPEC", "SPECS", "handler"]
