"""有界工具循环：一次提问 → 若干次工具调用 → 一段回答。

上限写死在代码里，不靠提示词自觉。三个数字各有依据：

- ``MAX_ROUNDS = 8``：够「查 → 判 → 生成 → 收尾」跑两遍。再多说明模型在原地打转。
- ``MAX_SECONDS = 60``：不含生成等待。用户能接受的「小岐正在想」大约是这么久。
- ``MAX_JOBS = 2``：一次提问起三份产物，用户要在一屏里比较三样东西，那不叫效率。

**模型优先，规则兜底**。模型给了 tool_call 就按它的走；没给、或压根没有模型
（无 key），才走 ``router.route``。反过来会让关键词规则盖住模型的判断，而模型
看过完整的 use_when/avoid_when。

**无 key 时不假装思考**：直接按规则执行，再用工具结果拼一句人话。让 MockClient
先吐一句 ``[MOCK:...]`` 再被覆盖，只会白跑一次调用。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from app.llm.clients import MockClient
from app.llm.router import ModelRouter

from . import router as rules
from . import store
from .bridge import TaskBridge, kind_for
from .contract import ToolStep
from .events import RunChannel, envelope
from .prompts import SUMMARY_TRIGGER, build_messages, summarize_history
from .tools import JOB_CAPABILITIES, call_tool, capability_of, title_of, tool_schemas

MAX_ROUNDS = 8
MAX_SECONDS = 60.0
MAX_JOBS = 2
#: 进上下文的历史条数。与 ``build_messages`` 的默认值一致——这个数被两处用到
#: （截历史、判断哪一段该进摘要），写两个数迟早对不上。
HISTORY_WINDOW = 12
#: 分段推送的字数。逐字推只会增加前端渲染压力，不增加可读性。
TOKEN_CHUNK = 48


async def _say(channel: RunChannel, text: str) -> None:
    if not text:
        return
    for start in range(0, len(text), TOKEN_CHUNK):
        channel.publish(envelope("token", {"text": text[start : start + TOKEN_CHUNK]}))
        await asyncio.sleep(0)


def _tool_message(call_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(payload, ensure_ascii=False)}


def _profile_hint(user_id: str, db_path: Any) -> str:
    """一句话的画像摘要，进 system，用来调措辞与默认受众。

    刻意只取「有置信度支撑的维度」：把一条 0.1 置信度的维度写成「你最弱的是
    炮制」，模型就会顺着这个说法讲下去，用户还以为系统真知道。取不到就返回空串，
    让模型按通用读者说话——这比编一个受众稳。
    """

    try:
        from app.profile.store import get_profile
        from app.profile.update import profile_to_response

        response = profile_to_response(get_profile(user_id, db_path))
        axes = [axis for axis in (response.get("radar") or {}).get("axes", []) if isinstance(axis, dict)]
        solid = [
            (str(axis.get("label") or ""), axis.get("value"), float(axis.get("confidence") or 0))
            for axis in axes
            if axis.get("value") is not None and float(axis.get("confidence") or 0) >= 0.25
        ]
        if not solid:
            return ""
        solid.sort(key=lambda item: float(item[1]))
        weakest = "、".join(f"{label}（{score}）" for label, score, _ in solid[:2])
        return f"最弱的两维：{weakest}。可用于选例子与篇幅，但不要主动评判用户。"
    except Exception:  # noqa: BLE001 - 画像读不到不该影响对话
        return ""


def _node_hint(node_id: str, user_id: str, db_path: Any) -> str:
    """他现在站在哪一关。**只给结构，不给描述文本**。

    把节点描述整段塞进 system，模型会顺着它把教材念一遍；而用户这次问的
    可能完全是另一件事。所以这里只给"学科 · 章 · 小节 · 穴名（代号）/ 目标掌握度"，
    够模型判断该举哪类例子，又不会被当成话题。
    """

    if not node_id:
        return ""
    try:
        from app.meridian.store import describe_point
        from app.path.state import get_state

        detail = describe_point(node_id, db_path)
        if not detail:
            return ""
        bits: list[str] = []
        for key in ("layer_title", "chapter_title", "section_title"):
            value = str(detail.get(key) or "").strip()
            if value and value not in bits:
                bits.append(value)
        try:
            state = get_state(user_id, node_id, db_path=db_path) or {}
        except Exception:  # noqa: BLE001 - 状态读不到不影响这句提示
            state = {}
        parts = [" · ".join(bits) if bits else "", f"知识点「{detail.get('name') or node_id}」"]
        acupoint = str(detail.get("acupoint_name") or "").strip()
        if acupoint:
            code = str(detail.get("acupoint_code") or "").strip()
            parts.append(f"穴位 {acupoint}" + (f"（{code}）" if code else ""))
        status = str(state.get("status") or "").strip()
        if status:
            parts.append(f"当前状态 {status}")
        return "；".join(part for part in parts if part)
    except Exception:  # noqa: BLE001 - 上下文丢了也不能让这一轮失败
        return ""


#: 参数里带 node_id 的生成类能力。从地图带过来的上下文直接填进去，
#: 不让模型"记得"再传一次——它会漏，漏了就只能生成一份跟当前知识点无关的东西。
_NODE_AWARE = frozenset({"mindmap.generate", "ppt.generate", "doc.generate", "quiz.generate", "story.generate"})


def _with_node_context(capability: str, args: dict[str, Any], node_id: str) -> dict[str, Any]:
    """确定性兜底：用户在某个知识点上说"做个导图"，就做这个知识点的。

    只在参数**为空**时填，从不覆盖模型显式给的值——模型明确要别的节点时，
    它比我们更清楚用户说了什么。
    """

    if not node_id:
        return args
    if capability in _NODE_AWARE:
        if not str(args.get("node_id") or "").strip():
            args["node_id"] = node_id
        return args
    if capability.startswith(("kg.", "kb.")):
        context = dict(args.get("context") or {})
        context.setdefault("node_id", node_id)
        args["context"] = context
    return args


async def _maybe_summarize(
    conversation_id: str,
    history: list[dict[str, Any]],
    router: ModelRouter,
    *,
    keyless: bool,
    db_path: Any,
) -> None:
    """把「已经掉出历史窗口」的那一段压成备忘。

    只压窗口外的部分，且把上一版摘要一起喂进去——否则每压一次就丢掉比十轮更早的一切。
    摘要失败不影响这一轮：用户已经拿到回答了，记忆是锦上添花。
    """

    if len(history) < SUMMARY_TRIGGER:
        return
    older = history[:-HISTORY_WINDOW]
    if len(older) < 8:
        return

    previous = store.get_summary(conversation_id, db_path)
    upto = str(older[-1].get("message_id") or "")
    if not upto or upto == previous.get("summary_upto"):
        return  # 这一段已经压过，不重复花钱

    seed = previous.get("summary") or ""
    payload: list[dict[str, Any]] = []
    if seed:
        payload.append({"role": "user", "content": f"（此前的备忘）{seed}"})
    payload.extend(older)

    if keyless:
        # 无 key 时不调模型：MockClient 会吐一段 [MOCK]，把它当记忆存进去比没有更糟。
        from .prompts import digest_of

        text = digest_of(payload)
    else:
        text = await summarize_history(router, payload)
    if text:
        store.update_summary(conversation_id, text, upto, db_path)


async def run_turn(
    *,
    conversation_id: str,
    run_id: str,
    text: str,
    history: list[dict[str, Any]],
    actor: Any,
    channel: RunChannel,
    db_path: Any,
    model_router: ModelRouter | None = None,
    refs: list[str] | None = None,
    node_id: str = "",
) -> dict[str, Any]:
    """跑完一轮，返回 ``{status, text, steps, artifacts}``。异常不外抛。"""

    router = model_router or ModelRouter()
    keyless = isinstance(router.select("assistant"), MockClient)

    summary = store.get_summary(conversation_id, db_path).get("summary", "")
    messages = build_messages(
        text,
        history,
        refs=refs,
        summary=summary,
        profile_hint=_profile_hint(actor.user_id, db_path),
        node_hint=_node_hint(node_id, actor.user_id, db_path),
        max_history=HISTORY_WINDOW,
    )
    tools = tool_schemas()
    bridge = TaskBridge(
        channel=channel,
        run_id=run_id,
        conversation_id=conversation_id,
        user_id=actor.user_id,
        db_path=db_path,
    )

    steps: list[ToolStep] = []
    ui_actions: list[dict[str, str]] = []
    results: list[tuple[str, dict[str, Any], bool]] = []
    seen: dict[str, dict[str, Any]] = {}
    job_count = 0
    final_text = ""
    deadline = time.monotonic() + MAX_SECONDS

    async def execute(capability: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
        nonlocal job_count
        args = _with_node_context(capability, dict(args), node_id)
        # 工具行上的名字取 spec.title（「排一份学习路线」），不是能力字段名。
        # 前端以前自己维护映射表，新能力一加就漏，工具行会显示 plan.create。
        step = ToolStep(
            call_id=call_id,
            tool=capability.replace(".", "_"),
            capability=capability,
            title=title_of(capability),
            args=args,
            started_at=time.time(),
        )
        steps.append(step)
        channel.publish(envelope("tool_start", {"call_id": call_id, "tool": step.tool, "capability": capability, "title": step.title, "args": args}))

        if capability in JOB_CAPABILITIES and job_count >= MAX_JOBS:
            payload = {"ok": False, "code": "assistant.job_budget", "message": f"这一轮已经起了 {MAX_JOBS} 个生成任务，先把这两份看完再起新的。"}
            step.state, step.error_code, step.summary = "failed", "assistant.job_budget", str(payload["message"])
            step.finished_at = time.time()
            results.append((capability, payload, False))
            channel.publish(envelope("tool_result", {"call_id": call_id, "ok": False, **payload}))
            return payload

        outcome = await call_tool(step.tool, args, actor, db_path=db_path)
        step.finished_at = time.time()
        step.state = "ok" if outcome.ok else "failed"
        step.summary = outcome.summary
        step.error_code = outcome.error_code
        step.remedy = outcome.remedy
        step.task_id = outcome.task_id
        results.append((capability, outcome.payload, outcome.ok))
        for action in outcome.payload.get("ui_actions") or []:
            if isinstance(action, dict) and action.get("url"):
                ui_actions.append({"label": str(action.get("label") or ""), "url": str(action["url"])})
        channel.publish(
            envelope(
                "tool_result",
                {
                    "call_id": call_id,
                    "ok": outcome.ok,
                    "capability": capability,
                    "summary": outcome.summary,
                    "error_code": outcome.error_code,
                    "remedy": outcome.remedy,
                    "task_id": outcome.task_id,
                },
            )
        )

        if outcome.task_id:
            job_count += 1
            kind = kind_for(capability)
            store.add_artifact(
                run_id=run_id,
                conversation_id=conversation_id,
                call_id=call_id,
                capability=capability,
                task_id=outcome.task_id,
                kind=kind,
                state="accepted",
                db_path=db_path,
            )
            bridge.start(call_id=call_id, capability=capability, task_id=outcome.task_id, kind=kind)
        elif outcome.artifact:
            # 快照型产物：结果当次就绪，直接落一张 done 卡并发事件。
            # 这类能力（plan.present）没有任务流，走 bridge 会挂在等流上。
            artifact = outcome.artifact
            kind = str(artifact.get("kind") or kind_for(capability))
            store.add_artifact(
                run_id=run_id,
                conversation_id=conversation_id,
                call_id=call_id,
                capability=capability,
                task_id="",
                kind=kind,
                open_url=str(artifact.get("open_url") or ""),
                state="done",
                resource_id=str(artifact.get("resource_id") or ""),
                db_path=db_path,
            )
            channel.publish(
                envelope(
                    "artifact",
                    {
                        "call_id": call_id,
                        "capability": capability,
                        "kind": kind,
                        "resource_id": str(artifact.get("resource_id") or ""),
                        "open_url": str(artifact.get("open_url") or ""),
                        "title": str(artifact.get("title") or ""),
                        "verify": {},
                    },
                )
            )

        return outcome.payload

    try:
        if keyless:
            route = rules.route(text)
            if route is not None:
                await execute(route.capability, route.args, "rule_0")
            final_text = rules.compose_fallback_answer(results, text)
        else:
            rounds = 0
            hinted = False
            while rounds < MAX_ROUNDS and time.monotonic() < deadline:
                rounds += 1
                turn = await router.chat_tools("assistant", messages, tools=tools, temperature=0.3)

                if turn.tool_calls:
                    if turn.message:
                        messages.append(turn.message)
                    for call in turn.tool_calls:
                        capability = capability_of(call.name)
                        if capability is None:
                            # 工具名不认识：回给模型，让它下一轮改。直接失败会让用户
                            # 看到一个「莫名其妙结束了」的回答。
                            messages.append(_tool_message(call.id, {"ok": False, "code": "assistant.unknown_tool", "message": f"没有这个工具：{call.name}"}))
                            continue
                        fingerprint = f"{capability}:{json.dumps(call.arguments, ensure_ascii=False, sort_keys=True)}"
                        if fingerprint in seen:
                            # 同一轮里重复调同一能力同一参数：能力层会幂等返回同一个
                            # task，但这里会多挂一个订阅者，同一条流被读两遍。
                            messages.append(_tool_message(call.id, seen[fingerprint]))
                            continue
                        payload = await execute(capability, call.arguments, call.id)
                        seen[fingerprint] = payload
                        messages.append(_tool_message(call.id, payload))
                    continue

                final_text = str(turn.text or "").strip()

                # 模型这一轮没调工具。**不替它执行**——关键词表只认「导图」「讲稿」
                # 这类词，认不出「我想学中药学，两年后考研」该走规划而不是出图；
                # 替它执行等于替它做错决定。规则命中只作为一句提示交回去，
                # 让模型自己判断：这确实是个动作，还是用户只想要个答案。
                if not steps and not hinted:
                    hinted = True
                    hint = rules.route(text)
                    if hint is not None:
                        messages.append({"role": "user", "content": f"（系统提示：这句话看起来包含一个动作意图——{hint.reason}。若确实是，请调用相应工具；若用户只是想知道答案，就按你的判断直接回答。）"})
                        continue
                break

            if not steps and rounds >= MAX_ROUNDS:
                # 打转打满还没动手，说明提示没起作用。至少给一句话，别让界面空着。
                final_text = final_text or ""

            if not final_text:
                # 模型什么都没说（真会这样：拿到工具结果后只回了个空）。再要一句，
                # 不要直接甩兜底——兜底话术不认识用户在说什么。
                closer = await router.chat_tools(
                    "assistant",
                    [*messages, {"role": "user", "content": "（系统注：请用一句话回应用户刚才那句话，不要罗列功能清单。）"}],
                    tools=tools,
                    temperature=0.3,
                )
                final_text = str(closer.text or "").strip()

            if not final_text:
                final_text = rules.compose_fallback_answer(results, text)

        # 生成任务在模型收束之后才等：先让用户看到「我先起草方案」，再让进度
        # 条走起来。顺序反过来，界面会先安静十几秒。
        await _say(channel, final_text)
        content = {
            "type": "text",
            "text": final_text,
            "steps": [step.model_dump(mode="json") for step in steps],
        }
        if ui_actions:
            # 定位成功的动作按钮：不自动跳页，写进消息体（刷新后还在）+ 推一次事件。
            content["ui_actions"] = ui_actions
            channel.publish(envelope("ui_actions", {"actions": ui_actions}))
        # 先落这一条再等任务：方案卡是桥在等待期间追加的，顺序必须是
        # 「用户 → 小岐的话 → 方案卡」，反过来刷新后像是方案先于解释出现。
        store.append_message(conversation_id, "assistant", content, run_id=run_id, db_path=db_path)
        bridge.mark_chat_persisted()
        await bridge.wait()

        status = "done"
        if bridge.awaiting_plan:
            status = "awaiting_plan"
        elif bridge.failed:
            status = "failed"

        store.update_run(run_id, status=status, steps=[step.model_dump(mode="json") for step in steps], finished=True, db_path=db_path)
        await _maybe_summarize(conversation_id, history, router, keyless=keyless, db_path=db_path)
        return {
            "status": status,
            "text": final_text,
            "steps": [step.model_dump(mode="json") for step in steps],
            "artifacts": [dict(item.__dict__) for item in bridge.outcomes],
        }

    except asyncio.CancelledError:
        bridge.cancel()
        store.update_run(run_id, status="cancelled", finished=True, db_path=db_path)
        raise
    except Exception as exc:  # noqa: BLE001 - 任何未预期异常都要给用户一句人话
        # 先把桥停掉再落错误：桥在等「正文已落库」的闸门，这一轮既然没走到那一步，
        # 它就会一直挂着，顺带把资源流也占住。
        bridge.cancel()
        channel.publish(envelope("error", {"code": "assistant.failed", "message": "这一轮没跑完", "remedy": "稍后重试；若反复失败，把这句话说得更具体一些。"}))
        store.append_message(
            conversation_id,
            "assistant",
            {"type": "error", "text": "这一轮没跑完", "steps": [step.model_dump(mode="json") for step in steps]},
            run_id=run_id,
            db_path=db_path,
        )
        store.update_run(run_id, status="failed", finished=True, db_path=db_path)
        return {"status": "failed", "text": "", "steps": [step.model_dump(mode="json") for step in steps], "error": f"{type(exc).__name__}: {exc}"}
