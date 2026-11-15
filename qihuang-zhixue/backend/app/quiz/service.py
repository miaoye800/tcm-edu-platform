"""QuizAgent selection, secure grading, and assessment orchestration."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.llm.clients import MockClient
from app.llm.router import ModelRouter
from app.mastery.alias import resolve_kc
from app.mastery.bkt import get_mastery, record_mastery_event
from app.path.state import set_state
from app.profile.update import ProfileUpdateEvent, get_latest_profile, update_profile_from_event
from app.quiz.constants import (
    ASSESSMENT_GATE_WEIGHTS, ASSESSMENT_PASS_THRESHOLDS, CASE_RECOMMENDED_SECONDS,
    HIGH_FUN_PREFERENCE, OPTIONAL_RECOMMENDED_SECONDS, QUICK_QUESTION_COUNT,
    QUICK_RECOMMENDED_SECONDS, RECENT_QUESTION_LIMIT,
)
from app.quiz.prompts import SEMANTIC_GRADING_SYSTEM
from app.quiz.schemas import GradeResult, QuizGroup, QuizQuestion, QuizQuestionPublic
from app.quiz.storage import (
    create_assessment, finish_assessment, get_assessment, get_question, list_all_questions,
    list_questions_for_node, recent_history, save_assessment_answer, save_attempt, save_mistake,
)


def ensure_seed_questions(db_path: Path) -> None:
    """Lazy import avoids startup coupling while keeping an offline path usable."""
    if list_all_questions(db_path):
        return
    from app.quiz.seed import seed_question_bank
    seed_question_bank(db_path, target_count=36)


async def compose_quiz(*, user_id: str, node_id: str, scenario: str, db_path: Path) -> QuizGroup:
    ensure_seed_questions(db_path)
    profile = await get_latest_profile(user_id, db_path=db_path)
    targeted = list_questions_for_node(node_id, db_path)
    all_questions = list_all_questions(db_path)
    questions = targeted + [row for row in all_questions if row["id"] not in {item["id"] for item in targeted}]
    selected = _select_questions(questions, user_id=user_id, profile=profile, db_path=db_path, count=QUICK_QUESTION_COUNT)
    return QuizGroup(scenario="assessment" if scenario == "assessment" else "practice", questions=[_to_internal(row) for row in selected])


def public_question(question: QuizQuestion) -> dict[str, Any]:
    return QuizQuestionPublic.model_validate(question.model_dump(exclude={"answer", "analysis"})).model_dump()


def _select_questions(rows: list[dict[str, Any]], *, user_id: str, profile: Any, db_path: Path, count: int, question_type: str | None = None, preferred_ids: set[str] | None = None) -> list[dict[str, Any]]:
    recent_qids, recent_points = recent_history(user_id, db_path, RECENT_QUESTION_LIMIT)
    mastery = get_mastery(user_id, db_path=db_path)
    goal = str(profile.learning_goal.label or profile.goal.normalized or "")
    base_score = profile.knowledge_base.score if profile.knowledge_base.score is not None else 50
    candidates = [row for row in rows if question_type is None or row["question_type"] == question_type] or rows
    # 当前知识点的题必须排在最前。
    #
    # 踩过的坑：调用方把 `targeted` 拼到 `rows` 前面就以为「先考这个知识点」，
    # 但这里按 rank 重排，拼接顺序被完全抹平——实测请求「人参」返回的是「麻黄」的题，
    # 576 道题里只有 3~4 道属于当前知识点，不优先就几乎选不中。
    # 用一个大到不会被其他因子抵消的加分，而不是改排序逻辑：frequency、掌握度、
    # 最近作答这些因子仍然在**当前知识点的题内部**起作用。
    preferred = preferred_ids or set()

    def rank(row: dict[str, Any]) -> tuple[float, str]:
        if goal == "应试提分":
            value = row["frequency"] * 2.0
        elif goal == "兴趣科普":
            # Interest learners start from broader, less exam-skewed coverage.
            value = 1.0 - row["frequency"]
        else:
            value = row["frequency"] * 0.7
        if row["id"] in preferred:
            value += 100.0
        snapshot = mastery.get(row["node_id"])
        current_mastery = snapshot.get("p_mastery", 0.35) if isinstance(snapshot, dict) else (snapshot.p_mastery if snapshot else 0.35)
        value += max(0.0, 1.0 - current_mastery)
        if base_score < 45 and row["question_type"] in {"single", "short"}: value += 0.5
        if row["id"] in recent_qids: value -= 10
        if row["exam_point_id"] in recent_points: value -= 2
        return (-value, row["id"])

    ordered = sorted(candidates, key=rank)
    picked: list[dict[str, Any]] = []
    point_ids: set[str] = set()
    for item in ordered:
        if len(picked) >= count: break
        # Avoid repeating a point in one group whenever the bank offers alternatives.
        #
        # 但**当前知识点的题不受这条限制**：每味药只有一个考点，7 道人参题全挂在
        # 同一个 `exam_point` 上，按「考点不重复」筛完只剩 1 道。学生点开人参，
        # 本该把这几道都做掉；去重是为了组卷时的多样性，不是为了把同一味药的题砍掉。
        if (
            item["id"] not in preferred
            and item["exam_point_id"] in point_ids
            and len({x["exam_point_id"] for x in ordered}) >= count
        ):
            continue
        picked.append(item); point_ids.add(item["exam_point_id"])
    return picked or ordered[:count]


def _source_level(row: dict[str, Any]) -> str:
    source = str(row["source"])
    if source.startswith("original:"): return "original"
    if source.startswith("adapted:"): return "adapted"
    # 依据教材自编的题：有出处（教材页码）但没有年份，不是历年真题。
    # 归到 real 会被 `QuizQuestion` 校验器拒掉——它要求 real 必须带可核实年份，
    # 那条规则是对的，别为了跑通就放松它，改来源标注才是对的。
    if source.startswith("教材自编"): return "original"
    return "real"


def _to_internal(row: dict[str, Any]) -> QuizQuestion:
    level = _source_level(row)
    if level == "real":
        detail: dict[str, Any] = {"year": row["year"], "source": row["source"], "copyright_notice": "仅限学习使用"}
    elif level == "adapted":
        detail = {"adapted_from_question_id": str(row["source"]).split(":", 2)[1], "changes": str(row["source"]).split(":", 2)[-1]}
    else:
        detail: dict[str, Any] = {"knowledge_node_id": row["node_id"], "source": row["source"]}
        # 依据教材自编的题要跟「模型原创」分开标。学生有权知道这题是谁出的、
        # 有没有人核过——把教材字段拼成的题标成「AI 原创」是错的（模型没写它），
        # 反过来把模型编的题标成「教材」更是错的。
        source_text = str(row["source"])
        if source_text.startswith("教材自编"):
            detail["origin"] = "textbook_template"
            # 目前没有任何一道经过人工校对。标 True 之前必须先真的有人逐题看过。
            detail["verified"] = False
            detail["citation"] = source_text.split("：", 1)[-1]
    return QuizQuestion(id=row["id"], stem=row["stem"], options=row["options"], answer=row["answer"], analysis=row["analysis"], question_type=row["question_type"], exam_point_id=row["exam_point_id"], source_level=level, source_detail=detail)


async def grade_question(*, user_id: str, question_id: str, raw_answer: str | list[str], elapsed_seconds: float | None, db_path: Path) -> GradeResult:
    ensure_seed_questions(db_path)
    row = get_question(question_id, db_path)
    if row is None: raise ValueError("question not found")
    question = _to_internal(row)  # Validates source provenance again before grading.
    answer = _answer_list(raw_answer)
    if question.question_type in {"single", "multi"}:
        correct = {_norm(x) for x in answer} == {_norm(x) for x in question.answer}
        score, missing, comment = (100, [], "回答正确。") if correct else (0, list(question.answer), "答案与标准答案不一致。")
    else:
        score, missing, comment = await _semantic_grade(answer, question.answer)
        correct = score >= 60
    remediation = None if correct else _remediation(row)
    result = GradeResult(question_id=question.id, correct=correct, score=score, missing_points=missing, comment=comment, analysis=question.analysis, remediation=remediation)
    save_attempt(user_id=user_id, question_id=question.id, exam_point_id=question.exam_point_id, correct=correct, score=score, answer=answer, db_path=db_path)
    record_mastery_event(user_id=user_id, kc_id=row["node_id"], correct=correct, event_type="quiz", elapsed_seconds=elapsed_seconds, evidence={"question_id": question.id, "exam_point_id": question.exam_point_id, "score": score, "source_level": question.source_level}, db_path=db_path)
    if not correct: save_mistake(user_id=user_id, question=row, answer=answer, remediation=remediation or {}, db_path=db_path)
    await update_profile_from_event(user_id, ProfileUpdateEvent(type="quiz", payload={"answers":[{"question_id":question.id,"knowledge_point":row["exam_point_name"],"difficulty":"hard" if question.question_type in {"case","short"} else "easy","correct":correct}]}), db_path=db_path)
    return result


async def _semantic_grade(answer: list[str], expected: list[str]) -> tuple[int, list[str], str]:
    client = ModelRouter().select("generate")
    if not isinstance(client, MockClient):
        try:
            raw = await ModelRouter().chat("generate", [{"role":"system","content":SEMANTIC_GRADING_SYSTEM},{"role":"user","content":json.dumps({"answer":" ".join(answer),"expected":expected},ensure_ascii=False)}], response_format={"type":"json_object"}, temperature=0)
            parsed = json.loads(raw[raw.find("{"):raw.rfind("}")+1])
            score = max(0, min(100, int(parsed["score"])))
            return score, [str(x) for x in parsed.get("missing_points", [])], str(parsed.get("comment") or "已完成语义判分。")
        except Exception:
            pass
    joined = " ".join(_norm(item) for item in answer)
    missing = [item for item in expected if _norm(item) not in joined]
    covered = len(expected) - len(missing)
    score = round(100 * covered / max(1, len(expected)))
    return score, missing, "已按关键术语覆盖度判分。" if missing else "关键要点覆盖完整。"


def _remediation(row: dict[str, Any]) -> dict[str, Any]:
    return {"explain": {"resource_type":"explain","topic":row["exam_point_name"],"entry":"/api/resources/generate"}, "graph": {"center_id":row["node_id"],"entry":f"/api/graph/node/{row['node_id']}/detail"}}


async def start_assessment(*, user_id: str, node_id: str, db_path: Path) -> dict[str, Any]:
    ensure_seed_questions(db_path); profile = await get_latest_profile(user_id, db_path=db_path)
    targeted = list_questions_for_node(node_id, db_path)
    all_rows = list_all_questions(db_path)
    targeted_ids = {item["id"] for item in targeted}
    rows = targeted + [row for row in all_rows if row["id"] not in targeted_ids]
    # 关卡只在**题库真有这种题型**时才出现。
    #
    # 之前是「先选题、再无条件挂三道关」：`_select_questions` 在候选为空时会退回全表，
    # 于是题库里一道病例题都没有时，`case` 关照样出现，装着一道单选题。
    # 前端会给 `case` 关渲染成三页病例纸（辨证 / 选药 / 剂量），学生看到的是
    # 「病例情境」标题配一道「下列哪味药…」——这不是缺内容，是把别的东西说成病例题。
    # 宁可少一关，也不冒充。
    case_pool = [row for row in rows if row["question_type"] == "case"]
    case = (
        _select_questions(case_pool, user_id=user_id, profile=profile, db_path=db_path, count=1, preferred_ids=targeted_ids)
        if case_pool else []
    )
    used_ids = {item["id"] for item in case}
    quick_rows = [row for row in rows if row["question_type"] in {"single", "multi"} and row["id"] not in used_ids]
    quick = _select_questions(quick_rows or rows, user_id=user_id, profile=profile, db_path=db_path, count=QUICK_QUESTION_COUNT, preferred_ids=targeted_ids)
    used_ids |= {item["id"] for item in quick}
    optional: list[dict[str, Any]] = []
    optional_name = None
    short_pool = [row for row in rows if row["question_type"] == "short" and row["id"] not in used_ids]
    wants_optional = (
        profile.planning_params.fun_preference >= HIGH_FUN_PREFERENCE
        or profile.planning_params.assessment_strictness == "宽松"
    )
    if wants_optional and short_pool:
        optional_name = "feynman" if profile.planning_params.fun_preference >= HIGH_FUN_PREFERENCE else "oral"
        optional = _select_questions(short_pool, user_id=user_id, profile=profile, db_path=db_path, count=1, preferred_ids=targeted_ids)
    gates = [{"id":"quick","name":"快问快答","recommended_seconds":QUICK_RECOMMENDED_SECONDS,"questions":[public_question(_to_internal(x)) for x in quick]}]
    if case:
        gates.append({"id":"case","name":"病例情境","recommended_seconds":CASE_RECOMMENDED_SECONDS,"questions":[public_question(_to_internal(x)) for x in case]})
    if optional_name and optional:
        gates.append({"id":"optional","name":"费曼关" if optional_name == "feynman" else "小岐口试","recommended_seconds":OPTIONAL_RECOMMENDED_SECONDS,"questions":[public_question(_to_internal(x)) for x in optional]})
    payload = {"gates":gates,"strictness":profile.planning_params.assessment_strictness,"threshold":ASSESSMENT_PASS_THRESHOLDS[profile.planning_params.assessment_strictness]}
    assessment_id = create_assessment(user_id=user_id,node_id=node_id,payload=payload,db_path=db_path)
    return {"assessment_id":assessment_id,**payload}


async def answer_assessment(*, user_id: str, assessment_id: str, question_id: str, answer: str | list[str], elapsed_seconds: float | None, db_path: Path) -> dict[str, Any]:
    assessment = get_assessment(assessment_id,user_id,db_path)
    if not assessment or assessment["status"] != "started": raise ValueError("assessment is unavailable")
    gate = next((gate["id"] for gate in assessment["payload"]["gates"] if any(q["id"] == question_id for q in gate["questions"])), None)
    if not gate: raise ValueError("question does not belong to assessment")
    result = await grade_question(user_id=user_id,question_id=question_id,raw_answer=answer,elapsed_seconds=elapsed_seconds,db_path=db_path)
    save_assessment_answer(assessment_id,question_id,gate,result.model_dump(),db_path)
    return result.model_dump()


async def finish_assessment_for_user(*, user_id: str, assessment_id: str, db_path: Path) -> dict[str, Any]:
    assessment = get_assessment(assessment_id,user_id,db_path)
    if not assessment or assessment["status"] != "started": raise ValueError("assessment is unavailable")
    gates = assessment["payload"]["gates"]; results_by_gate: dict[str,list[int]] = defaultdict(list)
    for item in assessment["answers"]: results_by_gate[item["gate"]].append(int(item["result"]["score"]))
    missing_gates = [gate["id"] for gate in gates if not results_by_gate[gate["id"]]]
    if missing_gates: raise ValueError("all assessment gates must be answered")
    gate_scores = {gate: round(sum(scores)/len(scores),2) for gate,scores in results_by_gate.items()}
    active_weights = sum(ASSESSMENT_GATE_WEIGHTS[gate] for gate in gate_scores)
    total = round(sum(gate_scores[gate]*ASSESSMENT_GATE_WEIGHTS[gate] for gate in gate_scores)/active_weights,2)
    threshold = int(assessment["payload"]["threshold"]); passed = total >= threshold
    # 测评从沉浸层进来时带的是**图谱实体 id**（`herb_mahuang`），而地图、路线、
    # 掌握度都按知识树点 id（`d01.s1.p1`）组织。两个空间只在读取时映射过一次
    # （P0-1），写入这一侧也得落一份——否则「测评过了」在地图上一片漆黑。
    kc_id = resolve_kc(assessment["node_id"], db_path=db_path) or assessment["node_id"]
    weak = []
    for answer in assessment["answers"]:
        if answer["result"]["correct"]:
            continue
        question = get_question(answer["question_id"], db_path)
        if question:
            weak.append(question["exam_point_id"])
    result = {"assessment_id":assessment_id,"node_id":assessment["node_id"],"kc_node_id":kc_id,"score":total,"gate_scores":gate_scores,"threshold":threshold,"passed":passed,"weak_exam_point_ids":sorted(set(weak)),"remediation": {"entry":"/api/resources/generate","node_id":assessment["node_id"]} if not passed else None}
    finish_assessment(assessment_id,result,db_path)
    if passed:
        meta = {"assessment_id":assessment_id,"score":total,"gate_scores":gate_scores,"threshold":threshold,"finished_at":get_assessment(assessment_id,user_id,db_path)["finished_at"]}
        set_state(user_id,assessment["node_id"],"completed",meta=meta,db_path=db_path)
        if kc_id != assessment["node_id"]:
            # 两个 id 都点亮：前者是这次测评的出处（历史要能追溯），后者是地图上
            # 真正那一格。只写前者，用户回到地图看不到任何变化。
            set_state(user_id,kc_id,"completed",meta={**meta,"via_node_id":assessment["node_id"]},db_path=db_path)
    return result


def _answer_list(value: str | list[str]) -> list[str]: return [value.strip()] if isinstance(value,str) and value.strip() else [str(x).strip() for x in value if str(x).strip()]
def _norm(value: str) -> str: return "".join(value.strip().lower().split())
