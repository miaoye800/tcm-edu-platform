"""SQLite persistence for exam points, questions, attempts, and assessments."""

from __future__ import annotations

import json
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.profile.store import DB_PATH, connect


def init_quiz_db(db_path: Path = DB_PATH) -> None:
    with closing(connect(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS exam_point (
                id TEXT PRIMARY KEY, node_id TEXT NOT NULL REFERENCES knowledge_node(id), name TEXT NOT NULL,
                exam_years_json TEXT NOT NULL DEFAULT '[]', frequency REAL NOT NULL DEFAULT 0,
                source TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(node_id, name)
            );
            CREATE TABLE IF NOT EXISTS real_question (
                id TEXT PRIMARY KEY, exam_point_id TEXT NOT NULL REFERENCES exam_point(id), stem TEXT NOT NULL,
                options_json TEXT NOT NULL DEFAULT '[]', answer_json TEXT NOT NULL, analysis TEXT NOT NULL,
                year INTEGER, source TEXT NOT NULL,
                question_type TEXT NOT NULL CHECK(question_type IN ('single','multi','case','short')),
                created_at TEXT NOT NULL, UNIQUE(exam_point_id, stem)
            );
            CREATE TABLE IF NOT EXISTS mistake_record (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, question_id TEXT NOT NULL,
                exam_point_id TEXT NOT NULL, answer_json TEXT NOT NULL, expected_json TEXT NOT NULL,
                remediation_json TEXT NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT,
                UNIQUE(user_id, question_id, created_at)
            );
            CREATE TABLE IF NOT EXISTS quiz_attempt (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, question_id TEXT NOT NULL,
                exam_point_id TEXT NOT NULL, correct INTEGER NOT NULL, score INTEGER NOT NULL,
                answer_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS assessment_session (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, node_id TEXT NOT NULL, status TEXT NOT NULL,
                payload_json TEXT NOT NULL, result_json TEXT, created_at TEXT NOT NULL, finished_at TEXT
            );
            CREATE TABLE IF NOT EXISTS assessment_answer (
                assessment_id TEXT NOT NULL REFERENCES assessment_session(id), question_id TEXT NOT NULL,
                gate TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(assessment_id, question_id)
            );
            CREATE INDEX IF NOT EXISTS idx_real_question_point ON real_question(exam_point_id);
            CREATE INDEX IF NOT EXISTS idx_quiz_attempt_user_recent ON quiz_attempt(user_id, id DESC);
            CREATE INDEX IF NOT EXISTS idx_mistake_user_created ON mistake_record(user_id, created_at DESC);
            """
        )
        conn.commit()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def upsert_exam_point(*, node_id: str, name: str, exam_years: list[int], frequency: float, source: str, db_path: Path = DB_PATH) -> str:
    init_quiz_db(db_path)
    point_id = f"ep_{node_id}_{abs(hash(name)) % 10**10}"
    with closing(connect(db_path)) as conn:
        conn.execute(
            """INSERT INTO exam_point(id,node_id,name,exam_years_json,frequency,source,created_at) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(node_id,name) DO UPDATE SET exam_years_json=excluded.exam_years_json,
            frequency=excluded.frequency, source=excluded.source""",
            (point_id, node_id, name, json.dumps(sorted(set(exam_years))), frequency, source, _now()),
        )
        row = conn.execute("SELECT id FROM exam_point WHERE node_id=? AND name=?", (node_id, name)).fetchone()
        conn.commit()
    return str(row[0])


def upsert_question(question: dict[str, Any], db_path: Path = DB_PATH) -> str:
    init_quiz_db(db_path)
    qid = str(question.get("id") or f"rq_{uuid4().hex}")
    with closing(connect(db_path)) as conn:
        conn.execute(
            """INSERT INTO real_question(id,exam_point_id,stem,options_json,answer_json,analysis,year,source,question_type,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(exam_point_id,stem) DO UPDATE SET
            options_json=excluded.options_json,answer_json=excluded.answer_json,analysis=excluded.analysis,
            year=excluded.year,source=excluded.source,question_type=excluded.question_type""",
            (qid, question["exam_point_id"], question["stem"], json.dumps(question.get("options", []), ensure_ascii=False),
             json.dumps(question["answer"], ensure_ascii=False), question["analysis"], question.get("year"),
             question["source"], question["question_type"], _now()),
        )
        row = conn.execute("SELECT id FROM real_question WHERE exam_point_id=? AND stem=?", (question["exam_point_id"], question["stem"])).fetchone()
        conn.commit()
    return str(row[0])


def list_questions_for_node(node_id: str, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    init_quiz_db(db_path)
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """SELECT q.id,p.id,p.node_id,p.name,p.exam_years_json,p.frequency,p.source,
            q.stem,q.options_json,q.answer_json,q.analysis,q.year,q.source,q.question_type
            FROM real_question q JOIN exam_point p ON p.id=q.exam_point_id WHERE p.node_id=?""", (node_id,)
        ).fetchall()
    return [_question_row(row) for row in rows]


def list_all_questions(db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    init_quiz_db(db_path)
    with closing(connect(db_path)) as conn:
        rows = conn.execute("""SELECT q.id,p.id,p.node_id,p.name,p.exam_years_json,p.frequency,p.source,
            q.stem,q.options_json,q.answer_json,q.analysis,q.year,q.source,q.question_type
            FROM real_question q JOIN exam_point p ON p.id=q.exam_point_id""").fetchall()
    return [_question_row(row) for row in rows]


def get_question(question_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    return next((item for item in list_all_questions(db_path) if item["id"] == question_id), None)


def _question_row(row: Any) -> dict[str, Any]:
    return {"id": row[0], "exam_point_id": row[1], "node_id": row[2], "exam_point_name": row[3],
            "exam_years": json.loads(row[4]), "frequency": float(row[5]), "point_source": row[6],
            "stem": row[7], "options": json.loads(row[8]), "answer": json.loads(row[9]), "analysis": row[10],
            "year": row[11], "source": row[12], "question_type": row[13]}


def recent_history(user_id: str, db_path: Path = DB_PATH, limit: int = 20) -> tuple[set[str], set[str]]:
    init_quiz_db(db_path)
    with closing(connect(db_path)) as conn:
        rows = conn.execute("SELECT question_id,exam_point_id FROM quiz_attempt WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()
    return {str(row[0]) for row in rows}, {str(row[1]) for row in rows}


def save_attempt(*, user_id: str, question_id: str, exam_point_id: str, correct: bool, score: int, answer: list[str], db_path: Path = DB_PATH) -> None:
    init_quiz_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute("INSERT INTO quiz_attempt(user_id,question_id,exam_point_id,correct,score,answer_json,created_at) VALUES(?,?,?,?,?,?,?)", (user_id,question_id,exam_point_id,int(correct),score,json.dumps(answer,ensure_ascii=False),_now()))
        conn.commit()


def save_mistake(*, user_id: str, question: dict[str, Any], answer: list[str], remediation: dict[str, Any], db_path: Path = DB_PATH) -> None:
    init_quiz_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute("INSERT INTO mistake_record(user_id,question_id,exam_point_id,answer_json,expected_json,remediation_json,created_at) VALUES(?,?,?,?,?,?,?)", (user_id,question["id"],question["exam_point_id"],json.dumps(answer,ensure_ascii=False),json.dumps(question["answer"],ensure_ascii=False),json.dumps(remediation,ensure_ascii=False),_now()))
        conn.commit()


def create_assessment(*, user_id: str, node_id: str, payload: dict[str, Any], db_path: Path = DB_PATH) -> str:
    init_quiz_db(db_path); assessment_id = f"as_{uuid4().hex}"
    with closing(connect(db_path)) as conn:
        conn.execute("INSERT INTO assessment_session(id,user_id,node_id,status,payload_json,created_at) VALUES(?,?,?,?,?,?)", (assessment_id,user_id,node_id,"started",json.dumps(payload,ensure_ascii=False),_now())); conn.commit()
    return assessment_id


def get_assessment(assessment_id: str, user_id: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    init_quiz_db(db_path)
    with closing(connect(db_path)) as conn:
        row = conn.execute("SELECT id,node_id,status,payload_json,result_json,created_at,finished_at FROM assessment_session WHERE id=? AND user_id=?", (assessment_id,user_id)).fetchone()
        answers = conn.execute("SELECT question_id,gate,result_json FROM assessment_answer WHERE assessment_id=?", (assessment_id,)).fetchall()
    if not row: return None
    return {"id":row[0],"node_id":row[1],"status":row[2],"payload":json.loads(row[3]),"result":json.loads(row[4]) if row[4] else None,"created_at":row[5],"finished_at":row[6],"answers":[{"question_id":x[0],"gate":x[1],"result":json.loads(x[2])} for x in answers]}


def save_assessment_answer(assessment_id: str, question_id: str, gate: str, result: dict[str, Any], db_path: Path = DB_PATH) -> None:
    init_quiz_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute("INSERT INTO assessment_answer(assessment_id,question_id,gate,result_json,created_at) VALUES(?,?,?,?,?) ON CONFLICT(assessment_id,question_id) DO UPDATE SET result_json=excluded.result_json,created_at=excluded.created_at", (assessment_id,question_id,gate,json.dumps(result,ensure_ascii=False),_now())); conn.commit()


def finish_assessment(assessment_id: str, result: dict[str, Any], db_path: Path = DB_PATH) -> None:
    init_quiz_db(db_path)
    with closing(connect(db_path)) as conn:
        conn.execute("UPDATE assessment_session SET status='finished',result_json=?,finished_at=? WHERE id=?", (json.dumps(result,ensure_ascii=False),_now(),assessment_id)); conn.commit()

