"""规则兜底：没有模型、模型不给工具、或给了空轮次时的确定性判断。

**为什么不把它写成「意图分类器」**：分类器只能回答「是哪一类」，而这里真正要
决定的是一件更窄的事——**这一次该不该起一个生成任务**。起了就得等十几秒、
占一份资源；起错了比不回答更伤。

三条纪律：

1. 每条规则必须带**反例**。只写「导图」会命中「打开我的导图」，那是导航不是生成。
2. 生成规则排在查询规则之前。同时出现「讲稿」和「写一份」时，前者更具体。
3. 规则只用于兜底，**不覆盖模型**。模型给了 tool_call 就按模型的走——它看过完整
   工具描述，比关键词更懂「我要的是一份能讲的东西，不是一张图」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .tools import JOB_CAPABILITIES

#: 「做 12 页」「要二十页」这类说法里，页数是用量词表达的，词表匹配不到。
#: 放一条窄正则比往 triggers 里塞「页」（会误吃「第三页讲了什么」）安全得多。
_PAGES = re.compile(r"(做|要|来|整|出)\s*[0-9一二三四五六七八九十百]+\s*页")


@dataclass(frozen=True)
class Route:
    capability: str
    args: dict[str, Any]
    reason: str


#: 生成规则。``triggers`` 命中且 ``anti`` 未命中才算。
#: 顺序即优先级：试卷 → 漫剧 → 幻灯 → 导图 → 文稿。前两条最具体（「考考我」
#: 不会配上别的产物），所以排在容易误伤的前面。
_GENERATE_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "quiz.generate",
        ("出题", "卷子", "试卷", "测验", "考考我", "练习题", "做题"),
        ("打开", "看看", "错题", "成绩"),
    ),
    (
        "story.generate",
        ("漫剧", "短剧", "情景剧", "剧本", "分镜", "故事"),
        ("打开", "看看", "讲了什么"),
    ),
    (
        "ppt.generate",
        ("ppt", "幻灯", "讲稿", "课件", "演示文稿", "做几页", "页幻灯片"),
        ("打开", "看看", "导出"),
    ),
    (
        "mindmap.generate",
        ("思维导图", "导图", "关系图", "梳理成图", "画一张图", "画个图"),
        ("打开", "看看", "导出"),
    ),
    (
        "doc.generate",
        ("文档", "文稿", "讲义", "提纲", "综述", "写一篇", "写一份", "整理成文"),
        ("导图", "ppt", "幻灯", "讲稿"),
    ),
)

#: 查询规则。只收「一句话就能判断该查什么」的问法；两味药的区别这类需要实体抽取的
#: 判断留给模型——规则拆不出「麻黄」和「桂枝」，拆错了查出来的东西更误导。
#: 第三项是「这句话要落到哪个参数上」。空串表示这条规则不需要参数——
#: 统一塞 ``question`` 会让 profile.view 收到一个它不认的字段，被白名单挡回来。
_QUERY_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    # 「想学什么、多久要考」这类需求排在前面：它最容易被后面的「为什么」「怎么」
    # 之类的词抢走，而结论差得最远——一个要给路线，一个要给解释。
    ("plan.create", ("学习计划", "复习计划", "备考计划", "考研", "备考", "从哪开始", "先学什么", "怎么安排"), "goal"),
    # 定位排在画像之前：「我想学麻黄」里有「学」，但用户要的是位置，不是自评。
    ("path.focus", ("我想学", "在哪", "在哪里", "第几关", "哪个穴", "带我去看"), "query"),
    ("profile.view", ("我的画像", "我最弱", "我的短板", "我的水平", "学习状况", "哪块弱", "哪个维度"), ""),
    ("kb.search", ("教材里有没有", "有没有收录", "知识库里有", "原文里说"), "query"),
    ("kg.ask", ("一起用", "能不能", "可以同用", "配伍", "十八反", "十九畏", "为什么"), "question"),
)


def _hit(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def route(text: str) -> Route | None:
    """返回一条确定的路线；判断不出来就返回 ``None``（交给模型或直答）。"""

    clean = " ".join(str(text or "").split())
    if not clean:
        return None
    lowered = clean.lower()

    for capability, triggers, anti in _GENERATE_RULES:
        if _hit(lowered, triggers) and not _hit(lowered, anti):
            return Route(capability=capability, args={"query": clean[:200]}, reason=f"命中产物词：{_first_hit(lowered, triggers)}")

    if _PAGES.search(lowered) and not _hit(lowered, ("打开", "看看", "导出")):
        return Route(capability="ppt.generate", args={"query": clean[:200]}, reason="命中页数说法")

    for capability, triggers, field in _QUERY_RULES:
        if _hit(lowered, triggers):
            args = {field: clean[:200]} if field else {}
            return Route(capability=capability, args=args, reason=f"命中查询词：{_first_hit(lowered, triggers)}")

    return None


def _first_hit(text: str, words: tuple[str, ...]) -> str:
    for word in words:
        if word in text:
            return word
    return ""


def is_job(capability: str) -> bool:
    return capability in JOB_CAPABILITIES


def compose_fallback_answer(results: list[tuple[str, dict[str, Any], bool]], text: str = "") -> str:
    """没有模型时，把工具结果拼成一句人话。

    这不是「假装有模型」：它只复述工具已经算出来的结论（图谱的 ``answer``、
    产物的受理状态），不推断、不补写。拼不出来就明说，比编一句像样的空话好。

    **什么情况下什么都不做，也不该报一遍功能清单**：以前这里返回
    「我在。可以直接说要做什么：做导图、做幻灯、写文稿，或者查两味药的关系。」
    ——用户说「我想学中药学，两年后考研」，得到的是四个按钮，等于把问题退回给他。
    现在改成：接住他说的那句话，只问一个能推进的关键问题。
    """

    if not results:
        ask = " ".join(str(text or "").split())[:30]
        if ask:
            return f"「{ask}」这条我还接得不够准。你更想要一句结论，还是一份能自己拿去用的东西？"
        return "你直接说要做什么就行，我按你的说法来。"

    lines: list[str] = []
    for capability, payload, ok in results:
        if not ok:
            message = str(payload.get("message") or "这一步没走通")
            remedy = str(payload.get("remedy") or "")
            lines.append(f"{message}{('。' + remedy) if remedy else ''}")
            continue
        if capability in JOB_CAPABILITIES:
            lines.append("任务已经受理，接下来会先出方案给你确认，确认后再生成成品。")
            continue
        if capability == "path.focus":
            # 定位成功就说"找到了"；没定位到就把候选念出来——不假装知道。
            summary = str(payload.get("summary") or "")
            lines.append(summary or ("找到了。" if payload.get("ok") else "没定位到这一个。"))
            continue
        if capability == "plan.create":
            summary = str(payload.get("summary") or "")
            lines.append(summary or "路线已经排出来了。")
            continue
        answer = payload.get("answer") or payload.get("summary")
        if isinstance(answer, str) and answer.strip():
            lines.append(answer.strip())
            continue
        relations = payload.get("relations")
        if isinstance(relations, list) and relations:
            lines.append(f"查到 {len(relations)} 条相关关系，已在下面的工具行里列出。")
            continue
        items = payload.get("items")
        if isinstance(items, list) and items:
            names = "、".join(str(item.get("name") or "") for item in items[:5] if isinstance(item, dict))
            lines.append(f"教材里查到 {len(items)} 条：{names}。")
            continue
        if payload.get("coverage") == "out_of_corpus":
            lines.append("教材里没有直接对应的条目，往下只能按通用知识来说——这点我会标出来。")
            continue
        lines.append("查到了结果，但没有可以直接复述的结论。")

    return "\n\n".join(lines)
