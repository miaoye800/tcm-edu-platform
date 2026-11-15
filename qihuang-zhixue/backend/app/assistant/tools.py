"""对话 Agent 的工具面：注册表 → tools[]，tool_call → dispatch。

这个模块只做四件事，每件都对着一类会悄悄出错的地方：

1. **暴露哪些能力**用白名单写死，不用 ``all_specs()`` 全量。理由：``command`` 类
   （``mindmap.present``/``expand``）作用于「已打开的那张图」，对话里没有这个上下文。
   白名单的代价是新增能力要来这里加一行——这一行必须写，因为它同时是一个
   “它在对话里有足够的上下文吗”的检查点。

2. **工具名 ↔ 能力名用显式映射**。``spec.tool_schema()`` 把点换成下划线，反向
   ``replace("_", ".")`` 今天恰好对，明天出现三段名字就会解错。映射表在名字变了
   之后直接 ``KeyError``，比静默调错能力好。

3. **参数白名单校验**。``CapabilityInput`` 是 ``extra="ignore"``——那是给前端顺手
   带旁路字段用的。模型多传一个 ``audience``，静默吞掉的结果是用户以为按受众写了，
   实际没生效。这里把不认的字段列回给模型，它下一轮会改对。

4. **结果裁剪**。查询类返回的是整棵关系网，原样塞回上下文既贵又淹没有效信息。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.capabilities import dispatch, registry
from app.capabilities.bootstrap import install
from app.capabilities.errors import CapabilityError
from app.capabilities.spec import CapabilitySpec

#: 进对话工具面的能力。顺序即 prompt 里的顺序：先生成、再查询。
#: 每加一条都要问两个问题：它在对话里有没有足够的上下文？它失败时用户能看懂吗？
EXPOSED: tuple[str, ...] = (
    "mindmap.generate",
    "ppt.generate",
    "doc.generate",
    "quiz.generate",
    "story.generate",
    "kg.ask",
    "kg.compare",
    "kg.lookup",
    "kg.path",
    "kg.review",
    "kg.attribute",
    "kb.search",
    "profile.view",
    "plan.create",
    "plan.present",
    "path.focus",
)

#: 生成类：调用即建任务，不等结果。
JOB_CAPABILITIES = frozenset({"mindmap.generate", "ppt.generate", "doc.generate", "quiz.generate", "story.generate"})

#: 回灌给模型的单条工具结果上限。超过就换成预览 + 截断标记——
#: 模型需要的是「有没有、是什么」，不是把整张表读完。
RESULT_LIMIT = 6000

#: 不暴露给模型的字段。``kg_bridge`` 的动态输入模型里带 ``db_path``（供内部调用
#: 传库路径），它 ``exclude=True`` 只影响序列化、不影响 schema；不挡住的话，
#: 模型会看到一个能改数据库路径的参数。
HIDDEN_PARAMS = frozenset({"db_path"})


def parameters_of(spec: CapabilitySpec) -> dict[str, Any]:
    """工具参数 schema：去掉内部字段，其余照抄输入模型。"""

    schema = spec.input_model.model_json_schema()
    properties = schema.get("properties") or {}
    for key in list(properties):
        if key in HIDDEN_PARAMS:
            del properties[key]
    required = schema.get("required")
    if isinstance(required, list):
        schema["required"] = [key for key in required if key not in HIDDEN_PARAMS]
    return schema


@dataclass
class ToolOutcome:
    """一次工具调用的结果，三种读者共用。

    ``payload`` 回灌给模型，``summary`` 给前端工具行，``artifact`` 给产物卡。
    三者不合并：模型要看细节，用户要看一句话，卡片要结构化字段。
    """

    ok: bool
    capability: str
    payload: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    task_id: str = ""
    artifact: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    remedy: str = ""


def _entries() -> dict[str, CapabilitySpec]:
    install()
    specs: dict[str, CapabilitySpec] = {}
    for name in EXPOSED:
        entry = registry.get(name)
        if entry is None:  # pragma: no cover - 白名单写错时立刻炸，而不是少一个工具
            raise RuntimeError(f"工具面引用了未注册的能力：{name}")
        specs[name] = entry.spec
    return specs


def tool_name(capability: str) -> str:
    return capability.replace(".", "_")


def capability_of(name: str) -> str | None:
    """工具名 → 能力名。显式比对，不做字符串反解。"""

    for capability in EXPOSED:
        if tool_name(capability) == name:
            return capability
    return None


def tool_schemas() -> list[dict[str, Any]]:
    """OpenAI 形状的 tools。description 直接吃 ``spec.description()``。"""

    tools: list[dict[str, Any]] = []
    for spec in _entries().values():
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool_name(spec.name),
                    "description": spec.description(),
                    "parameters": parameters_of(spec),
                },
            }
        )
    return tools


def catalog_text() -> str:
    """给 system prompt 的一行式目录。比整份 schema 便宜，且能一眼看全。"""

    rows = []
    for spec in _entries().values():
        kind = "提交即返回，完成后出产物" if spec.name in JOB_CAPABILITIES else "同步返回，结果可直接引用"
        rows.append(f"- {tool_name(spec.name)}（{spec.title}）：{spec.one_line}。{kind}")
    return "\n".join(rows)


def title_of(capability: str) -> str:
    """能力的中文名，给工具行用。

    以前前端自己维护一张 ``mindmap_generate → 生成导图`` 的映射表，新能力一加
    就漏——漏了工具行就显示 ``plan.create`` 这种字段名。标题本来就在 spec 里，
    从声明取，别让第二处再抄一遍。
    """

    entry = registry.get(capability)
    return entry.spec.title if entry is not None else capability


# --------------------------------------------------------------------------- #
# 参数校验
# --------------------------------------------------------------------------- #


def _check_args(spec: CapabilitySpec, args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """返回 ``(清洗后的参数, 错误)``。错误形状与工具失败一致，便于统一回灌。"""

    if not isinstance(args, dict):
        return {}, {"ok": False, "code": "assistant.bad_arguments", "message": "参数必须是对象"}
    body: dict[str, Any] = {}
    allowed = set(spec.input_model.model_fields) - HIDDEN_PARAMS
    unknown = [key for key in args if key not in allowed and key != "params"]
    if unknown:
        return {}, {
            "ok": False,
            "code": "assistant.unknown_params",
            "message": f"{spec.name} 不认这些参数：{'、'.join(sorted(unknown))}",
            "accepted": sorted(allowed),
            "remedy": "只传 accepted 里列出的字段；主题类内容放 query。",
        }
    for key, value in args.items():
        if key in allowed:
            body[key] = value
    return body, None


def _shrink(payload: Any) -> tuple[Any, bool]:
    """超长结果裁成预览。返回 ``(payload, 是否截断)``。"""

    try:
        blob = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        return {"preview": str(payload)[:RESULT_LIMIT]}, True
    if len(blob) <= RESULT_LIMIT:
        return payload, False
    return {"preview": blob[:RESULT_LIMIT], "truncated": True}, True


def _summary_for(spec: CapabilitySpec, body: dict[str, Any]) -> str:
    if spec.name in JOB_CAPABILITIES:
        return f"已受理：{spec.title}"
    # 规划类的「结果」就是阶段与起点。只说「已完成」等于什么也没说。
    if spec.name == "plan.create":
        start = str(body.get("start_stage") or "")
        stages = int(body.get("total_stages") or 0)
        if stages:
            return f"{stages} 个阶段" + (f"，从「{start}」开始" if start else "")
    items = body.get("items") or body.get("facts") or body.get("results")
    if isinstance(items, list):
        return f"{spec.title}：{len(items)} 条"
    return f"{spec.title}：已完成"


async def call_tool(
    name: str,
    args: dict[str, Any],
    actor: Any,
    *,
    db_path: Any = None,
) -> ToolOutcome:
    """执行一次工具调用。**所有异常都变成 ToolOutcome**，不向上抛。

    理由：一次工具失败不该炸掉整轮对话。模型拿到 ``ok:false`` + ``remedy`` 后
    还能换参数、换能力，或者诚实地说「这一步没走通」。
    """

    capability = capability_of(name)
    if capability is None:
        return ToolOutcome(
            ok=False,
            capability="",
            payload={"ok": False, "code": "assistant.unknown_tool", "message": f"没有这个工具：{name}"},
            error_code="assistant.unknown_tool",
            summary="这个工具不存在",
        )

    spec = _entries()[capability]
    body, bad = _check_args(spec, args)
    if bad is not None:
        return ToolOutcome(
            ok=False,
            capability=capability,
            payload=bad,
            summary=str(bad.get("message") or "参数不合法"),
            error_code=str(bad.get("code") or "assistant.bad_arguments"),
            remedy=str(bad.get("remedy") or ""),
        )

    kwargs: dict[str, Any] = {"db_path": db_path} if db_path is not None else {}
    try:
        result = await dispatch.invoke(capability, body, actor, **kwargs)
    except CapabilityError as exc:
        return ToolOutcome(
            ok=False,
            capability=capability,
            payload={"ok": False, "code": str(exc.code), "message": exc.detail, "remedy": exc.remedy},
            summary=exc.detail,
            error_code=str(exc.code),
            remedy=exc.remedy,
        )
    except Exception as exc:  # noqa: BLE001 - 未预期异常也要变回可读结果，不能炸整轮
        return ToolOutcome(
            ok=False,
            capability=capability,
            payload={"ok": False, "code": "assistant.tool_crashed", "message": f"{type(exc).__name__}: {exc}"},
            summary="这一步没走通",
            error_code="assistant.tool_crashed",
            remedy="换个问法，或先把主题说得更具体。",
        )

    data = dict(result.body or {})
    # 快照型产物（plan.present）：结果当场就绪，没有任务可轮询。
    # 以前这类能力只能在对话里说一句「导好了」，用户还得自己去资源库找——
    # 产物卡的意义就是「这一句话旁边就有个能点开的东西」。
    instant = data.pop("artifact", None)
    if spec.name in JOB_CAPABILITIES:
        artifact = dict(data.get("artifact") or {})
        task_id = str(data.get("task_id") or "")
        # 回灌给模型的必须是「已受理」，不能是「已生成」——生成还没发生。
        payload = {
            "ok": True,
            "capability": spec.name,
            "task_id": task_id,
            "status": "accepted",
            "note": "任务已受理。进度会直接显示给用户，你只需说明接下来会发生什么（例如先出方案等她确认），不要说已经完成。",
        }
        return ToolOutcome(
            ok=True,
            capability=capability,
            payload=payload,
            summary=f"已受理：{spec.title}",
            task_id=task_id,
            artifact=artifact,
        )

    payload, truncated = _shrink(data)
    if truncated and isinstance(payload, dict):
        payload.setdefault("note", "结果过长已截断，回答时只引用能确认的部分。")
    return ToolOutcome(
        ok=True,
        capability=capability,
        payload=payload if isinstance(payload, dict) else {"data": payload},
        summary=_summary_for(spec, data),
        artifact=dict(instant) if isinstance(instant, dict) else {},
    )
