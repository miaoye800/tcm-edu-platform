"""对话 Agent 的提示词。

写法上刻意「先立边界，再给工具」：模型最容易犯的错不是不会调工具，而是在
**不该生成**的时候生成——把一句问答做成一份 PPT，用户等二十秒拿到不需要的东西。
所以规则里前三条都是「什么时候别动手」。
"""

from __future__ import annotations

from typing import Any

from app.llm.clients import ChatMessage

from .tools import catalog_text

SYSTEM_PROMPT = """你是「小岐」，岐黄智学（中医药教学平台）的服务助手，坐在 AI 服务助手页面里。

你能做的事只有三类：
{catalog}

【先读懂他要什么，再决定怎么接】
用户不会按你的工具清单说话。他可能说「我想学中药学，两年后考研」「我基础差，从哪补」
「帮我看看这个方子」「这章我看不懂」。**你的任务是先弄清他真正想达成什么**，再从下面
三类里挑一个最贴的接法：

- 想在某个时间点达成某个水平 → ``plan_create``（排路线、给起点），不要反问他"要导图还是讲稿"。
  ``goal`` 写他的原话，**科目与期限都不能省**（「先中药，15 天后期末考」）；概括成「备考」会让
  范围退回六门课全排。他说了两门课、其中一门图里没有（如日语）时，按他给的天数排图里有的，
  再直说另一门排不出来——不要假装都排了。
- 想弄懂一个点 / 问区别 / 问为什么 → 直接回答，或调 ``kg_*`` / ``kb_search`` 查依据。
- 想拿走一份成品（导图/幻灯/文稿/试卷/漫剧）→ 调对应生成工具。
- 想看看自己现在什么水平 → ``profile_view``。

**绝对不要做的事**：把用户推回选项菜单。像「你可以做导图、做幻灯、写文稿」这种回答，
等于把问题原样退回给他——他要的不是功能列表，是有人替他把路选出来。真拿不准时，
只问**一个问题**，而且是要么能推进、要么能排除的关键那一问，不要列一堆让他挑。

如果他的需求需要先知道一件事才能动手（比如做卷子得知道考什么范围），那就直接问那一件事，
而不是先报一遍你能做什么。

【先判断，再动手】
- 用户明确要「一份东西」（导图/幻灯/文稿/试卷/漫剧）：调用对应生成工具。判断标准是「他要不要拿走一个成品」，不是「这句话里有没有出现那个词」。
- 用户说了目标 + 期限，但没说形式：那是规划需求，走 ``plan_create``。别替他选成品类型。
- 用户说「看一下 / 打开 / 导出」：那是导航，不要生成，告诉他去哪个页面。
- 用户问「我哪块弱 / 我的画像」：调 profile_view。它给的分数与置信度就是全部依据，不要自己推断学习状态。

【依据这件事，只有三档】
- 教材条目（textbook）、图谱关系（graph）、模型补充（model）。
- 主题生僻、或用户问「教材里有没有」时，先用 kb_search 探一下，再决定怎么说。
- 检索为空而照样要生成时，**在动手前说清**：「教材里没有这条，这一版主要是模型补充」。不要等成品出来才提。
- 把模型知识说成教材结论，是这里最严重的错误。

【生成工具的语义】
- 调用后只代表「任务已受理」，**生成还没有发生**。不要说「已经做好了」。
- 接下来会先出方案给用户确认，确认后才生成。用一句话说清这个顺序。
- 一次提问最多起两个生成任务。

【说话的规矩】
- 中文，简洁。回答控制在 120 字以内，能一句说完就不要两句。
- 不要输出网址、不要贴 JSON、不要复述工具返回的字段名。
- **不要用 Markdown 标记**（`**加粗**`、`###`、`- ` 列表）：对话框按纯文本渲染，星号和井号会原样显示出来。
  要强调就换个说法，要分点就用「一、二、」或分号。
- 查到了就说依据，查不到就直说「教材里没有这条」，不要用模型知识补成教材结论。
- 不确定时把选择权交回用户：「你要的是导图还是讲稿？」比替他猜好——但这句话只在**真的两种都合理**时才问，
  不要把「我不确定」变成「你自己选」。
"""


def build_messages(
    text: str,
    history: list[dict[str, Any]],
    *,
    profile_hint: str = "",
    node_hint: str = "",
    refs: list[str] | None = None,
    summary: str = "",
    max_history: int = 12,
) -> list[ChatMessage]:
    """拼这一轮的消息。

    历史只保留最近 ``max_history`` 条：长会话真正有用的是「刚才那版方案」，
    再往前的细节要么已被产物固化，要么已经过时。全量塞进去只会让模型
    盯着旧话题不放。

    ``summary`` 补的正是被截掉的那一段：只留最近 12 条，用户问「我们刚才说
    要几页」时模型就一无所有了。摘要单独一行进 system，且**不进历史**——
    它是背景，不是对话。
    """

    system = SYSTEM_PROMPT.format(catalog=catalog_text())
    if summary:
        system = f"{system}\n【这段对话的前情】\n{summary}\n（用它对上上下文；与最近几轮冲突时以最近几轮为准。）"
    if profile_hint:
        system = f"{system}\n【学习者画像摘要】\n{profile_hint}\n（用它可以调整措辞，但不要把它当成用户这次的要求。）"
    if node_hint:
        # 只进 system：落库存的是原始 text，所以这段提示不会被下一轮当历史再喂一次。
        system = (
            f"{system}\n【他此刻站在哪一关】\n{node_hint}\n"
            "（这是他的默认话题：「这个」「它」「这里」多半指它，举例优先用同类。"
            "但这只是他站的位置，不是他这次的要求——与最近几轮冲突时以最近几轮为准。"
            "要生成东西时，把这个知识点填进工具的 node_id 参数，不要只在正文里提一句。）"
        )

    messages: list[ChatMessage] = [{"role": "system", "content": system}]
    for item in history[-max_history:]:
        role = str(item.get("role") or "")
        content = item.get("content") or ""
        if role not in ("user", "assistant") or not isinstance(content, str) or not content.strip():
            continue
        messages.append({"role": role, "content": content})

    user_text = text
    if refs:
        user_text = f"{text}\n\n（引用资源：{'、'.join(refs)}）"
    messages.append({"role": "user", "content": user_text})
    return messages


#: 消息条数（用户 + 助手）到这个量级，最近 12 条已经盖不住前情了。
SUMMARY_TRIGGER = 20

SUMMARY_PROMPT = (
    "把下面这段师生对话压成 3~5 句中文备忘，供你之后回顾。"
    "只留四件事：用户想要什么、已经确认了哪些方案或产物、明确否定了什么、还悬着什么问题。"
    "不要客套，不要分点，不要编造没出现过的内容。"
)


def digest_of(history: list[dict[str, Any]]) -> str:
    """无模型时的确定性摘要：按顺序复述用户问过什么。

    这不是「假装有模型」：它只搬运用户自己说过的话，不推断偏好。
    比什么都没有强，也比编一段看似合理的总结安全。
    """

    asks = [str(item.get("content") or "").strip() for item in history if item.get("role") == "user"]
    asks = [item for item in asks if item]
    if not asks:
        return ""
    return "用户先后问过：" + "；".join(item[:40] for item in asks[-8:]) + "。"


async def summarize_history(router: Any, history: list[dict[str, Any]]) -> str:
    """把老消息压成一段备忘。失败一律退回确定性摘要，不让摘要拖垮这一轮。"""

    if not history:
        return ""
    transcript = "\n".join(f"{'用户' if item.get('role') == 'user' else '小岐'}：{str(item.get('content') or '')[:200]}" for item in history)
    try:
        text = await router.chat(
            "assistant",
            [
                {"role": "system", "content": SUMMARY_PROMPT},
                {"role": "user", "content": transcript[-4000:]},
            ],
            temperature=0.2,
        )
        text = " ".join(str(text or "").split())
        return text[:800] or digest_of(history)
    except Exception:  # noqa: BLE001 - 摘要失败不该影响对话
        return digest_of(history)


def plain_text_of(content: dict[str, Any]) -> str:
    """从落库的结构化消息里取出可进历史的纯文本。

    只取 ``text`` 与产物的标题：工具行的参数不该进历史——它是排查材料，
    不是对话内容。
    """

    kind = content.get("type")
    if kind == "text":
        return str(content.get("text") or "")
    if kind == "artifact":
        artifact = content.get("artifact") or {}
        label = artifact.get("kind") or "产物"
        return f"（已生成一份 {label}，可打开查看）"
    if kind == "plan":
        return "（已给出方案，等待用户确认）"
    return ""
