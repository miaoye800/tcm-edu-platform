"""ProfileAgent 的 LangGraph 节点。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, TypedDict, cast

from app.llm.clients import ChatMessage
from app.llm.router import ModelRouter
from app.profile.models import DiagnosticQuestion, ProfileDimension, StudentProfile, WeakPointDimension

ProfileIntent = Literal["profile_setup", "knowledge_qa", "other"]


class TraceItem(TypedDict):
    node: str
    summary: str


class ProfileState(TypedDict):
    user_id: str
    db_path: str | None
    messages: list[ChatMessage]
    diagnostic_answers: dict[str, str]
    intent: ProfileIntent | None
    dialogue_features: dict[str, Any]
    questions: list[dict[str, str]]
    quiz_features: dict[str, Any]
    profile: dict[str, Any]
    trace: list[TraceItem]


async def router_node(state: ProfileState) -> ProfileState:
    """识别冷启动意图，并通过条件边决定是否进入画像构建链路。"""

    router = ModelRouter()
    intent = await router.classify(state["messages"])
    next_state = copy_state(state)
    next_state["intent"] = cast(ProfileIntent, intent)
    append_trace(next_state, "profile_router", f"识别意图：{intent}")
    return next_state


async def dialogue_extract_node(state: ProfileState) -> ProfileState:
    """从首轮对话抽取专业、目标、基础和偏好线索。"""

    text = last_user_content(state["messages"])
    router = ModelRouter()
    try:
        await router.chat(
            "generate",
            [
                {"role": "system", "content": "你是学生画像抽取助手，只抽取专业、目标、基础自述、学习偏好线索。"},
                {"role": "user", "content": text},
            ],
        )
    except NotImplementedError:
        pass

    features = {
        "major": _pick_major(text),
        "goal": _pick_goal(text),
        "base_level": _pick_base_level(text),
        "style": _pick_learning_style(text),
        "motivation": _pick_motivation(text),
        "resource": _pick_resource_preference(text),
    }
    next_state = copy_state(state)
    next_state["dialogue_features"] = features
    append_trace(next_state, "dialogue_extract_agent", f"抽取首轮画像线索：{features['goal']}")
    return next_state


async def diagnostic_question_node(state: ProfileState) -> ProfileState:
    """基于解表药知识库生成冷启动诊断小测。"""

    router = ModelRouter()
    try:
        await router.chat(
            "generate",
            [
                {"role": "system", "content": "你是中药学诊断小测出题助手，只围绕解表药生成题目。"},
                {"role": "user", "content": "请生成 3-5 道解表药诊断题。"},
            ],
        )
    except NotImplementedError:
        pass

    questions = [
        DiagnosticQuestion(
            id="q1",
            question="麻黄的核心功效之一是什么？",
            answer="发汗解表",
            topic="麻黄功效",
            skill="功效识记",
        ),
        DiagnosticQuestion(
            id="q2",
            question="桂枝与麻黄鉴别时，桂枝更突出哪类作用？",
            answer="温通经脉",
            topic="麻黄-桂枝鉴别",
            skill="鉴别题",
        ),
        DiagnosticQuestion(
            id="q3",
            question="紫苏叶除解表散寒外，还常用于哪类中焦问题？",
            answer="行气和胃",
            topic="紫苏叶应用",
            skill="主治应用",
        ),
        DiagnosticQuestion(
            id="q4",
            question="香薷常被称为哪类季节性解表要药？",
            answer="夏月解表",
            topic="香薷特点",
            skill="特点辨析",
        ),
    ]
    next_state = copy_state(state)
    next_state["questions"] = [question.model_dump(mode="json") for question in questions]
    append_trace(next_state, "diagnostic_question_agent", "生成 4 道解表药诊断题")
    return next_state


async def diagnostic_eval_node(state: ProfileState) -> ProfileState:
    """评估诊断小测结果，形成知识基础和薄弱点线索。"""

    router = ModelRouter()
    try:
        await router.chat(
            "reason",
            [
                {"role": "system", "content": "你是中药学诊断评估助手，输出掌握度和薄弱点摘要。"},
                {"role": "user", "content": str(state["diagnostic_answers"])},
            ],
        )
    except NotImplementedError:
        pass

    correct_count = 0
    weak_points: list[str] = []
    for question in state["questions"]:
        qid = question["id"]
        expected = question["answer"]
        actual = state["diagnostic_answers"].get(qid, "")
        if expected and expected in actual:
            correct_count += 1
        else:
            weak_points.append(question["topic"])

    total = max(len(state["questions"]), 1)
    next_state = copy_state(state)
    next_state["quiz_features"] = {
        "correct_count": correct_count,
        "total": total,
        "weak_points": weak_points,
        "score": round(correct_count / total * 100),
    }
    append_trace(next_state, "diagnostic_eval_agent", f"诊断正确 {correct_count}/{total}")
    return next_state


async def profile_synthesis_node(state: ProfileState) -> ProfileState:
    """综合对话和小测线索，生成 7 维初始画像。"""

    router = ModelRouter()
    try:
        await router.chat(
            "generate",
            [
                {"role": "system", "content": "你是学生画像合成助手，输出 7 维画像分数和依据。"},
                {"role": "user", "content": str({"dialogue": state["dialogue_features"], "quiz": state["quiz_features"]})},
            ],
        )
    except NotImplementedError:
        pass

    profile = _build_profile_from_features(
        user_id=state["user_id"],
        dialogue=state["dialogue_features"],
        quiz=state["quiz_features"],
    )
    next_state = copy_state(state)
    next_state["profile"] = profile.model_dump(mode="json")
    append_trace(next_state, "profile_synthesis_agent", "合成 7 维初始学生画像")
    return next_state


def route_by_intent(state: ProfileState) -> ProfileIntent:
    """LangGraph 条件边路由函数。"""

    return state["intent"] or "other"


def initial_state(
    user_id: str,
    first_message: str,
    diagnostic_answers: dict[str, str] | None = None,
    db_path: str | None = None,
) -> ProfileState:
    """构造冷启动图初始状态。"""

    return {
        "user_id": user_id,
        "db_path": db_path,
        "messages": [{"role": "user", "content": first_message}],
        "diagnostic_answers": dict(diagnostic_answers or _default_answers()),
        "intent": None,
        "dialogue_features": {},
        "questions": [],
        "quiz_features": {},
        "profile": {},
        "trace": [],
    }


def copy_state(state: ProfileState) -> ProfileState:
    """复制状态，避免节点间共享可变对象。"""

    return {
        "user_id": state["user_id"],
        "db_path": state["db_path"],
        "messages": list(state["messages"]),
        "diagnostic_answers": dict(state["diagnostic_answers"]),
        "intent": state["intent"],
        "dialogue_features": dict(state["dialogue_features"]),
        "questions": [dict(item) for item in state["questions"]],
        "quiz_features": dict(state["quiz_features"]),
        "profile": dict(state["profile"]),
        "trace": list(state["trace"]),
    }


def append_trace(state: ProfileState, node: str, summary: str) -> None:
    """追加可观测执行轨迹。"""

    state["trace"].append({"node": node, "summary": summary})


def last_user_content(messages: list[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    if messages:
        return str(messages[-1].get("content", ""))
    return ""


def _build_profile_from_features(user_id: str, dialogue: dict[str, Any], quiz: dict[str, Any]) -> StudentProfile:
    quiz_score = int(quiz.get("score", 0))
    base_level = str(dialogue.get("base_level", "入门"))
    base_bonus = {"零基础": -10, "入门": 0, "一般": 5, "较好": 12}.get(base_level, 0)
    knowledge_score = _clamp(max(35, quiz_score) + base_bonus)
    weak_items = [str(item) for item in quiz.get("weak_points", [])]
    weak_score = _clamp(100 - len(weak_items) * 15 if weak_items else 82)

    return StudentProfile(
        user_id=user_id,
        knowledge_base=ProfileDimension(
            score=knowledge_score,
            evidence=f"基础自述为{base_level}，诊断小测得分 {quiz_score}。",
        ),
        learning_style=ProfileDimension(
            score=68,
            evidence=f"首轮表达偏好：{dialogue.get('style', '结构化讲解')}。",
        ),
        weak_points=WeakPointDimension(
            score=weak_score,
            evidence="诊断小测暴露的薄弱点。" if weak_items else "暂未发现明显薄弱点。",
            items=weak_items,
        ),
        goal=ProfileDimension(
            score=72,
            evidence=f"学习目标：{dialogue.get('goal', '系统掌握中药学基础')}。",
        ),
        pace=ProfileDimension(
            score=64,
            evidence="冷启动阶段默认采用稳步推进节奏。",
        ),
        motivation=ProfileDimension(
            score=70,
            evidence=f"动机线索：{dialogue.get('motivation', '备考与临床应用')}。",
        ),
        resource_preference=ProfileDimension(
            score=66,
            evidence=f"资源偏好：{dialogue.get('resource', '图表+例题')}。",
        ),
        updated_at=datetime.now(UTC),
    )


def _pick_major(text: str) -> str:
    if "中医学" in text:
        return "中医学"
    if "中药学" in text:
        return "中药学"
    return "中医药相关专业"


def _pick_goal(text: str) -> str:
    if "考研" in text:
        return "考研复习"
    if "考试" in text or "期末" in text:
        return "课程考试提分"
    if "临床" in text:
        return "临床辨证应用"
    return "系统掌握中药学基础"


def _pick_base_level(text: str) -> str:
    if "零基础" in text or "刚开始" in text:
        return "零基础"
    if "薄弱" in text or "一般" in text:
        return "一般"
    if "较好" in text or "还可以" in text:
        return "较好"
    return "入门"


def _pick_learning_style(text: str) -> str:
    if "图" in text or "表" in text:
        return "图表归纳"
    if "口诀" in text:
        return "口诀记忆"
    if "案例" in text:
        return "案例推理"
    return "结构化讲解"


def _pick_motivation(text: str) -> str:
    if "兴趣" in text or "喜欢" in text:
        return "兴趣驱动"
    if "考试" in text or "考研" in text:
        return "考试驱动"
    return "课程学习"


def _pick_resource_preference(text: str) -> str:
    if "视频" in text:
        return "视频讲解"
    if "题" in text:
        return "练习题"
    if "图" in text or "表" in text:
        return "图表资料"
    return "图表+例题"


def _default_answers() -> dict[str, str]:
    return {
        "q1": "发汗解表",
        "q2": "温通经脉",
        "q3": "行气和胃",
        "q4": "夏月解表",
    }


def _clamp(value: int) -> int:
    return max(0, min(100, value))
