"""排计划之前先问一轮：缺什么就问什么，问完再排。

## 为什么必须有一个闸门

规划需要的三件事——**期限、范围、起点**——决定了同一份画像会排出完全不同的路线：

* 期限决定 breadth/pace/depth（两年考研要考点导向、节奏紧；兴趣了解要铺开）；
* 范围决定学几门课（"先把中药学完"与"六门一起铺"是两份计划）；
* 起点决定砍不砍（已经学过中药学的人不需要 200 小时的中药学）。

过去这三件事全靠一张关键词表从一句话里猜：猜到了就翻开，猜不到就**按默认值直接排**。
用户只写了"帮我安排别的内容"，系统就默默按"全面 / 中等节奏 / 零基础"排了一份 291 节的
计划——他既没被问过，也不知道默认值从哪来。这不是个性化，是把猜测伪装成结论。

## 为什么要"按他的话来问"

第一版实现是三条写死的题（何时用 / 覆盖哪些 / 什么水平），不管用户说了什么都原样弹出来。
用户写了"想通过这学期中药学期末考试"，仍然被问"你打算什么时候用上这些内容？"和
"这一轮想覆盖哪些内容？"——期限和范围他自己已经说了。那不是追问，是一张与他无关的表单。

所以现在分两层：

1. **问不问**（``clarify``）——确定性判定，可复现。只问真的缺的，一条都不缺就一条不问。
2. **怎么问**（``tailor``）——模型读原话，把题面改写成"对着他这句话"的样子
   （"你说要过中药学期末考，中药学现在什么水平？"）。模型只能改措辞，**不能增删问题、
   不能改选项的值**：哪些缺口要问是事实判断，不该由模型临场决定。

``tailor`` 失败（没模型、超时、返回形状不对）就退回确定性题面，用户不会卡在这一步。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Sequence

from app.path.subjects import BY_ID, SUBJECTS, Subject, aliases_of, mentions

#: 期限词。命中任意一个就认为用户说过"什么时候要"。
_DEADLINE_WORDS = (
    "考研", "考试", "期末", "期中", "考证", "执业", "考编", "考博",
    "个月", "周", "星期", "暑假", "寒假", "毕业", "今年", "明年",
)
#: 起点词。说过"学过/没学过/零基础"就不用再问。
_BASELINE_WORDS = (
    "零基础", "没学过", "没接触", "自学过", "学过", "学过一点", "有基础", "跨考", "本科",
)
#: 范围词。点名了学科（或"全部/都要"）就不用再问。
_SCOPE_WORDS = ("全部", "都学", "全学", "六门", "所有", "随便")

#: 「为什么学、什么时候要考」必须真的影响取舍，否则用户说了等于没说（P0-8）。
#: 关键词表刻意做得小：它只做**确定性的翻译**，不做语义理解——理解归模型。
#:
#: 放在这里而不是 ``draft``：``service.create_l1_plan`` 与 ``draft`` 两条入口都要用，
#: 而 ``draft`` 反过来 import ``service``（循环）。判定口径只能有一份，
#: 否则对话里排的与向导里排的会因为一处漏改而给出两种 breadth。
_EXAM_WORDS = ("考研", "考试", "期末", "期中", "考证", "刷题", "提分", "执业")
_INTEREST_WORDS = ("兴趣", "了解", "随便看看", "扩宽", "休闲")
_SYSTEM_WORDS = ("系统", "打基础", "全面", "从头")


def planning_from_goal(goal: str, base: Any) -> Any:
    """把一句目标翻成规划参数（breadth/pace/depth）。``goal`` 为空时原样返回。"""

    text = " ".join(str(goal or "").split())
    if not text:
        return base
    updates: dict[str, Any] = {}
    if any(word in text for word in _EXAM_WORDS):
        updates.update({"breadth": "考点导向", "pace": "紧", "depth": "深"})
    elif any(word in text for word in _INTEREST_WORDS):
        updates.update({"breadth": "兴趣优先", "pace": "松"})
    elif any(word in text for word in _SYSTEM_WORDS):
        updates.update({"breadth": "全面", "depth": "深"})
    return base.model_copy(update=updates) if updates else base


#: 题面里允许模型改写的最大长度。多出来的直接截断——一段 300 字的追问不是追问。
_MAX_QUESTION = 40
_MAX_HINT = 60
_MAX_PLACEHOLDER = 40
_MAX_LABEL = 16


@dataclass(frozen=True)
class Option:
    value: str
    label: str
    detail: str = ""
    #: 选择后追加到诉求里的那句话。模型与关键词表都读得到。
    phrase: str = ""
    #: 这一项是不是"从用户原话里读到/推断出的答案"。前端默认选中它。
    default: bool = False


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    hint: str
    options: tuple[Option, ...] = ()
    multi: bool = False
    allow_text: bool = True
    #: 自由输入框的提示语。**每题不同**——以前三题共用"例如「明年 3 月」「实习前」"，
    #: 问到"这几门课什么水平"时那个例子是错的（用户照着填会得到一堆日期当水平）。
    placeholder: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "question": self.question,
            "hint": self.hint,
            "multi": self.multi,
            "allow_text": self.allow_text,
            "placeholder": self.placeholder,
            "options": [
                {
                    "value": item.value,
                    "label": item.label,
                    "detail": item.detail,
                    "phrase": item.phrase,
                    "default": item.default,
                }
                for item in self.options
            ],
        }

    def with_text(self, *, question: str = "", hint: str = "", placeholder: str = "",
                  labels: dict[str, str] | None = None) -> "Question":
        """换一层皮，值域不变。``tailor`` 只走这一个入口，所以选项值不可能被改飞。"""

        merged = labels or {}
        options = tuple(
            Option(
                value=item.value,
                label=merged.get(item.value, "").strip()[:_MAX_LABEL] or item.label,
                detail=item.detail,
                phrase=item.phrase,
                default=item.default,
            )
            for item in self.options
        )
        return Question(
            id=self.id,
            question=question.strip()[:_MAX_QUESTION] or self.question,
            hint=hint.strip()[:_MAX_HINT] or self.hint,
            options=options,
            multi=self.multi,
            allow_text=self.allow_text,
            placeholder=placeholder.strip()[:_MAX_PLACEHOLDER] or self.placeholder,
        )


@dataclass(frozen=True)
class ClarifyResult:
    needs: bool
    questions: tuple[Question, ...] = ()
    missing: tuple[str, ...] = ()
    #: 用户不答时系统会用的默认值。界面要写出来，不能让它悄悄生效。
    assumptions: tuple[str, ...] = ()
    #: 已经能确定的事实，给界面回显（"我理解你要……"）。
    understood: tuple[str, ...] = ()
    #: 从原话里点名的学科（结构化，不是给人看的中文）。**它就是本次规划的范围**，
    #: 直接传给 ``PathAgent(subjects=...)``——不靠模型自觉遵守一句中文。
    subjects: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "needs": self.needs,
            "missing": list(self.missing),
            "questions": [item.to_dict() for item in self.questions],
            "assumptions": list(self.assumptions),
            "understood": list(self.understood),
            "subjects": [
                {"subject_id": item, "name": BY_ID[item].title}
                for item in self.subjects
                if item in BY_ID
            ],
        }

    def with_questions(self, questions: Sequence[Question]) -> "ClarifyResult":
        return ClarifyResult(
            needs=bool(questions),
            questions=tuple(questions),
            missing=self.missing,
            assumptions=self.assumptions,
            understood=self.understood,
            subjects=self.subjects,
        )


def _deadline_question(named: Sequence[Subject]) -> Question:
    who = "、".join(subject.title for subject in named)
    if who:
        # 点名了学科，"用上这些内容"就太含糊：他要的是拿着中药学去考试。
        question = f"{who}打算什么时候用上？"
        placeholder = "例如：这学期期末 / 明年 3 月 / 两年后考研"
    else:
        question = "你打算什么时候用上这些内容？"
        placeholder = "例如：这学期期末 / 明年 3 月 / 没有期限"
    return Question(
        id="deadline",
        question=question,
        hint="期限会改变取舍：赶考试就抓考点、慢学就铺开讲透。",
        placeholder=placeholder,
        options=(
            Option("exam_near", "3 个月内要考", "按考点密度排，先拿分", "3 个月内要考试，按考点导向排"),
            Option("exam_far", "一年内考研/考证", "系统铺开，深度优先", "一年内考研，系统学完"),
            Option("term", "这学期期末", "跟着课程进度走", "这学期期末要考，跟课程进度"),
            Option("interest", "没有期限，想学扎实", "全面铺开，节奏放松", "没有期限，兴趣优先，慢学"),
        ),
    )


def _scope_question() -> Question:
    return Question(
        id="scope",
        question="这一轮想覆盖哪些内容？",
        hint="点一门就只排那一门；一门都不点，就按先修顺序六门都排。",
        placeholder="也可以写具体范围，例如「只要中药学的总论和各论」",
        multi=True,
        options=tuple(
            Option(
                subject.id,
                subject.title,
                _scope_detail(subject.id),
                f"重点学{subject.title}",
            )
            for subject in SUBJECTS
        ),
    )


def _scope_detail(subject_id: str) -> str:
    return {
        "herb": "药性与功效",
        "formula": "配伍与方证",
        "basic": "阴阳藏象",
        "diagnosis": "四诊辨证",
        "acup": "经络腧穴",
        "internal": "病证辨治",
    }.get(subject_id, "")


def _baseline_question(named: Sequence[Subject]) -> Question:
    if named:
        who = "、".join(subject.title for subject in named)
        question = f"{who}你现在大概什么水平？"
        hint = "说过的那部分会跳过或只做回顾，不再从零排。"
        placeholder = "例如：课上听过但没背过 / 药性背过一轮"
    else:
        question = "这几门课你现在大概什么水平？"
        hint = "学过的那部分会跳过或只做回顾，不再从零排。"
        placeholder = "例如：零基础 / 上过课没系统学 / 学过一遍要应考"
    return Question(
        id="baseline",
        question=question,
        hint=hint,
        placeholder=placeholder,
        options=(
            Option("zero", "基本零基础", "从绪论开始排", "基本零基础，从头学"),
            Option("some", "上过课，但没系统学", "跳过绪论，重点在分类与辨析", "上过课但没系统学，跳过绪论"),
            Option("exam_ready", "学过一遍，要应考", "只排重点与易错", "已经学过一遍，要应考，只排重点"),
        ),
    )


def _hit(text: str, words: Iterable[str]) -> bool:
    return any(word in text for word in words)


#: 说完就否定的说法：句中带这些词时，这门课不是**要学的范围**，而是"已经会了"。
#: 只用明确的过去式（"学过""已经掌握"），不用"掌握""学完"——后者是目标不是前提。
_MASTERED_WORDS = ("已经掌握", "已掌握", "已经学", "已学", "学过", "上过", "背过", "修过", "有基础", "已经会")


def _clauses(text: str) -> list[str]:
    return [chunk.strip() for chunk in re.split(r"[，,。；;！!？?\n]", text) if chunk.strip()]


def named_subjects(request: str) -> list[Subject]:
    """原话里点名**要学**的学科，按教材顺序。多处调用（clarify / draft / service）共用一份判定。

    **必须区分"要学"与"已经会"**：范围现在被当成硬边界去裁域，只做子串匹配的话，
    「我已经掌握中药学，想补方剂」会被读成"只排中药学"——正好是用户抱怨的那个
    反向故障（他要方剂，系统给他中药学）。所以先按标点切句，含过去式说法的
    那句不参与范围判定；判定"已掌握某科"由 ``proposal`` 另走证据，别在这里顺手做。

    只在"已经会"的句子里出现的学科会被整体排除；一句"要学"的都没有时返回空，
    范围退回全图，让模型按证据取舍——比凭一句"学过"就把六门课砍成一门安全。
    """

    text = " ".join(str(request or "").split())
    if not text:
        return []
    clauses = _clauses(text) or [text]
    wanted = [chunk for chunk in clauses if not _hit(chunk, _MASTERED_WORDS)]
    if not wanted:
        return []
    return [subject for subject in SUBJECTS if any(mentions(chunk, subject.id) for chunk in wanted)]


def clarify(request: str, *, context: Any = None, answers: dict[str, str] | None = None) -> ClarifyResult:
    """判断"能不能现在就排"，缺什么就出什么问题。

    ``answers`` 已有值时不再重复问同一项——用户在向导里答过一次，回头改目标
    不该把同一组问题再弹一遍。

    **一句话都不写的时候不问**：不是"三条都缺所以弹三题"，而是让他先写一句
    （向导里空着就点按钮，弹出来的三题与他无关，等于没读他的话）。
    """

    text = " ".join(str(request or "").split())
    answered = {str(key) for key in (answers or {}) if str((answers or {}).get(key) or "").strip()}
    named = named_subjects(text)
    understood: list[str] = []
    missing: list[str] = []
    questions: list[Question] = []
    assumptions: list[str] = []

    if not text and not answered:
        return ClarifyResult(
            needs=False,
            understood=("还没写目标",),
            assumptions=("先说一句为什么学、什么时候要，我才能按你的情况排",),
        )

    # ① 期限
    if "deadline" in answered or _hit(text, _DEADLINE_WORDS):
        understood.append("期限已说明")
    else:
        missing.append("deadline")
        questions.append(_deadline_question(named))
        assumptions.append("没答期限：按「全面、中等节奏」排，慢一点但不漏内容")

    # ② 范围：点名了学科、或有"全部"这类词，就不问
    if "scope" in answered or named or _hit(text, _SCOPE_WORDS):
        if named:
            understood.append("范围：" + "、".join(subject.title for subject in named))
        else:
            understood.append("范围：六门课")
    else:
        missing.append("scope")
        questions.append(_scope_question())
        assumptions.append("没答范围：六门课全部排进来，按先修顺序推进")

    # ③ 起点：图里有没有任何"学过"的证据
    has_evidence = False
    if context is not None:
        mastery = getattr(context, "mastery", {}) or {}
        route_state = getattr(context, "route_state", {}) or {}
        has_evidence = any(
            getattr(state, "p_mastery", 0.0) >= 0.6 for state in mastery.values()
        ) or any(status == "completed" for status in route_state.values())
    if "baseline" in answered or _hit(text, _BASELINE_WORDS) or has_evidence:
        understood.append("起点有依据")
    else:
        missing.append("baseline")
        questions.append(_baseline_question(named))
        assumptions.append("没答起点：按零基础排，绪论与基础章都保留")

    # 用户在诉求里点名了学科但没说先后（"帮我安排中药学和方剂学"）→ 不追问，
    # 交给学科先修顺序。问"先学哪个"是模型该按教材事实回答的，不该消耗用户一轮。
    return ClarifyResult(
        needs=bool(questions),
        questions=tuple(questions[:3]),
        missing=tuple(missing),
        assumptions=tuple(assumptions),
        understood=tuple(understood),
        subjects=tuple(subject.id for subject in named),
    )


#: 让模型改写题面的调用签名。与 ``PathAgent`` 共用同一个 llm_call 形状。
LLMCall = Callable[[list[dict[str, str]]], Awaitable[str]]

_TAILOR_PROMPT = """你在帮一个中医学习平台设计「排计划前的追问」。

用户刚写了一句他的诉求。系统已经判定出**还缺哪些信息**（missing），这些缺口不能增删，
你只负责把每一题的**问法**改成"对着他这句话说"的样子：

1. `question`：一句针对他的问句，引用他已经说过的内容（40 字内）。
   例：他说"想通过这学期中药学期末考试"，缺起点 → "中药学你现在什么水平？"
   不要写成"你打算什么时候用上这些内容"这种和他无关的通用问句。
2. `hint`：一句话说明答了会怎样（60 字内）。
3. `placeholder`：自由输入框的示例，必须和这题相关（40 字内）。
   问水平就别举例"明年 3 月"。
4. `options[].label`：选项名（16 字内），`value` 必须原样保留。

不许新增或删除题目，不许新增选项，不许改 value，不许编造用户没说过的事实。

只输出 JSON：
{"questions":[{"id":"...","question":"...","hint":"...","placeholder":"...",
  "options":[{"value":"...","label":"..."}]}]}"""


async def tailor(
    result: ClarifyResult,
    request: str,
    *,
    llm_call: LLMCall | None = None,
) -> ClarifyResult:
    """让模型按用户原话改写题面。**失败了就原样返回**——追问不该成为卡点。

    只换措辞，不换事实：题目集合、选项值、缺项列表都由 ``clarify`` 定死。
    """

    if not result.needs or not str(request or "").strip():
        return result

    payload = {
        "request": " ".join(str(request).split()),
        "understood": list(result.understood),
        "missing": [
            {
                "id": question.id,
                "current_question": question.question,
                "current_hint": question.hint,
                "options": [{"value": item.value, "label": item.label} for item in question.options],
            }
            for question in result.questions
        ],
    }
    messages = [
        {"role": "system", "content": _TAILOR_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        raw = await (llm_call(messages) if llm_call is not None else _router_call(messages))
        data = json.loads(_strip_fence(raw))
    except Exception:  # noqa: BLE001 - 追问的措辞是锦上添花，任何失败都不该挡住用户
        return result

    revised: dict[str, dict[str, Any]] = {}
    for item in data.get("questions") or []:
        if isinstance(item, dict) and str(item.get("id") or ""):
            revised[str(item["id"])] = item

    out: list[Question] = []
    for question in result.questions:
        patch = revised.get(question.id)
        if not isinstance(patch, dict):
            out.append(question)
            continue
        labels = {
            str(option.get("value") or ""): str(option.get("label") or "")
            for option in patch.get("options") or []
            if isinstance(option, dict)
        }
        out.append(
            question.with_text(
                question=str(patch.get("question") or ""),
                hint=str(patch.get("hint") or ""),
                placeholder=str(patch.get("placeholder") or ""),
                labels=labels,
            )
        )
    return result.with_questions(out)


def _strip_fence(raw: str) -> str:
    """模型爱在 JSON 外面套 ```json。剥掉，别为此多一次重试。"""

    text = str(raw or "").strip()
    if "```" not in text:
        return text
    body = text.split("```", 2)
    inner = body[1] if len(body) > 1 else text
    if inner.lstrip().lower().startswith("json"):
        inner = inner.lstrip()[4:]
    return inner.strip()


async def _router_call(messages: list[dict[str, str]]) -> str:
    from app.llm.router import ModelRouter

    return await ModelRouter().chat("reason", messages, temperature=0, response_format={"type": "json_object"})


def compose_request(request: str, answers: dict[str, str] | None = None, *, questions: Iterable[Question] = ()) -> str:
    """把用户原话与追问答案拼成一段**模型能读、关键词表也能读**的诉求。

    不能只把答案塞进结构化字段：``planning_from_goal`` 与提案 prompt 读的都是
    这一段文字。答案不进去，用户答了"3 个月内要考"，路线还是按"兴趣优先"排。

    多选的答案（范围）以逗号连起来传，这里**逐个**翻成选项自带的说法：
    `"herb,formula"` 要变成"重点学中药学、重点学方剂学"，而不是原样丢一串
    英文 id 给模型——它读不懂 `herb`，关键词表也认不出"中药学"。
    """

    base = " ".join(str(request or "").split())
    phrases: list[str] = []
    index = {question.id: question for question in questions}
    for key, value in (answers or {}).items():
        answer = str(value or "").strip()
        if not answer:
            continue
        question = index.get(key)
        options = {item.value: item for item in (question.options if question else ())}
        picked: list[str] = []
        for token in [part.strip() for part in answer.replace("，", ",").replace("、", ",").split(",") if part.strip()]:
            option = options.get(token)
            if option is not None and option.phrase:
                picked.append(option.phrase)
            elif option is not None:
                picked.append(option.label)
            else:
                # 自由文本（"下个月 15 号"）也要带语义标签，模型才知道那是期限。
                # 用短标签而不是整句问话：问句拼进诉求会变成
                # "……（补充：你打算什么时候用上这些内容：下个月 15 号）"。
                picked.append(_fallback_phrase(key, token))
        phrases.extend(picked)
    if not phrases:
        return base
    joined = "；".join(dict.fromkeys(phrases))
    return f"{base}（补充：{joined}）".strip()


def _fallback_phrase(key: str, answer: str) -> str:
    """自由文本答案也要带上语义标签，否则模型看到一串孤立短语。"""

    labels = {"deadline": "期限", "scope": "范围", "baseline": "起点"}
    return f"{labels.get(key, key)}：{answer}"


def subject_hints(request: str) -> list[str]:
    """诉求里点名了哪些学科（供界面回显"我理解你要学……"）。"""

    return [subject.id for subject in named_subjects(request)]


__all__ = [
    "ClarifyResult",
    "LLMCall",
    "Option",
    "Question",
    "clarify",
    "compose_request",
    "named_subjects",
    "planning_from_goal",
    "subject_hints",
    "tailor",
]
