"""真题生成器测试（``scripts/gen_questions.py``）。

守的四条：

1. **选项必须是有实体的药名**——这是错题归因能不能落地的前提。选项是「性味归经」
   这类知识点名，归因第一步就断，学生答错只会收到「无法归因」。
2. **一道题只有一个正确答案**——靠唯一性前提保证，测试里按同样口径独立复算。
3. **题干引的必须是教材原文子串**——不能是「看起来像原文的转述」。
4. **占位题清得干净**——按 ``source`` 前缀清，不按题干某句话清（那样只清三分之一）。
"""

from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
from pathlib import Path

import pytest

from app.profile.store import connect
from app.quiz.storage import init_quiz_db


def _load(module_name: str, relative: str):
    path = Path(__file__).resolve().parents[1] / relative
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator():
    return _load("gen_questions", "scripts/gen_questions.py")


@pytest.fixture(scope="module")
def seeded_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """只播种知识树，不建图谱。

    这样 ``build()`` 拿不到鉴别边和禁忌边，产出的全是「功效→药 / 主治→药」两类——
    正好用来验最核心的约束。禁忌题另有一条测试单独覆盖。
    """

    db_path = tmp_path_factory.mktemp("qbank") / "qbank.db"
    _load("qbank_seed_tree", "scripts/seed_knowledge_tree.py").seed(db_path)
    init_quiz_db(db_path)
    return db_path


def test_generated_options_are_always_herb_names(generator, seeded_db: Path) -> None:
    questions, stats = generator.build(seeded_db)

    assert questions, "生成器一道题都没出，先看 skipped 的统计"
    # 只认教材立过条目的药。药材 JSON 里另有 9 条十八反/十九畏的写法（乌头、牙硝…），
    # 它们是别名桩，出现在选项里会造出「两个正确答案」。
    textbook_names = set(generator.load_entries())
    for item in questions:
        for option in item["options"]:
            assert option["text"] in textbook_names, f"选项「{option['text']}」不是教材条目"


def test_options_never_hold_two_names_for_the_same_drug(generator, seeded_db: Path) -> None:
    """选项里不许出现别名桩，也不许同一味药出现两次。

    实测踩过：`川乌` 和 `乌头` 同时出现在一道题的选项里，二者是同一味药——这题就有
    两个正确答案，判卷会把答对的学生判错。根因是出题池用了全部 162 条药材记录，
    而教材只认其中 153 条（另外 9 条是配伍原文里的写法）。

    别名关系从数据里推，不写死：`chapter` 为空、`aliases` 指向教材条目的就是桩。
    """

    herbs = generator.load_herbs()
    entries = generator.load_entries()
    alias_of = {
        name: str(herb["aliases"][0])
        for name, herb in herbs.items()
        if name not in entries and not herb.get("chapter") and herb.get("aliases")
    }
    assert alias_of, "一条别名桩都没识别出来，这条测试就形同虚设"

    questions, _ = generator.build(seeded_db)
    for item in questions:
        texts = [option["text"] for option in item["options"]]
        stubs = [text for text in texts if text in alias_of]
        assert not stubs, f"选项里混进了别名桩 {stubs}：{texts}"
        canonical = [alias_of.get(text, text) for text in texts]
        assert len(set(canonical)) == len(canonical), f"同一味药出现了两次：{texts}"


def test_every_question_has_exactly_one_correct_option(generator, seeded_db: Path) -> None:
    questions, _ = generator.build(seeded_db)

    for item in questions:
        keys = [option["key"] for option in item["options"]]
        assert len(keys) == len(set(keys)) == 4
        assert len(item["answer"]) == 1
        assert item["answer"][0] in keys


def test_effect_question_quotes_the_textbook_verbatim(generator, seeded_db: Path) -> None:
    """题干里「」包着的功效串，必须是教材【功效】字段的字面子串。"""

    questions, _ = generator.build(seeded_db)
    entries = generator.load_entries()
    checked = 0
    for item in questions:
        if item["kind"] != generator.KIND_EFFECT:
            continue
        quote = re.search(r"「(.+?)」", item["stem"]).group(1)
        assert quote in entries[item["herb"]]["effects_text"]
        checked += 1
    assert checked > 0


def test_effect_distractors_never_share_the_same_effect_text(generator, seeded_db: Path) -> None:
    """干扰项的功能若和正确答案一模一样，这题就有两个正确答案。"""

    questions, _ = generator.build(seeded_db)
    entries = generator.load_entries()

    def effects_text(name: str) -> str:
        return str(entries.get(name, {}).get("effects_text") or "")

    for item in questions:
        if item["kind"] != generator.KIND_EFFECT:
            continue
        correct = effects_text(item["herb"])
        for option in item["options"]:
            if option["text"] == item["herb"]:
                continue
            assert effects_text(option["text"]) != correct


def test_indication_distractors_do_not_carry_the_same_syndrome(generator, seeded_db: Path) -> None:
    """干扰项若也治这个证，题目同样有两个正确答案——子串包含也算。"""

    questions, _ = generator.build(seeded_db)
    herbs = generator.load_herbs()

    for item in questions:
        if item["kind"] != generator.KIND_INDICATION:
            continue
        syndrome = re.search(r"「(.+?)」", item["stem"]).group(1)
        for option in item["options"]:
            if option["text"] == item["herb"]:
                continue
            for row in herbs[option["text"]].get("indications") or []:
                other = str(row.get("syndrome") or "")
                assert not (other and (other == syndrome or other in syndrome or syndrome in other))


def test_stems_are_unique_per_exam_point(generator, seeded_db: Path) -> None:
    """`real_question` 的唯一键是 (exam_point_id, stem)，撞键会静默覆盖掉一道题。"""

    questions, _ = generator.build(seeded_db)
    seen = [(item["node_id"], item["stem"]) for item in questions]
    assert len(seen) == len(set(seen))


def test_purge_removes_all_three_placeholder_templates(generator, tmp_path: Path) -> None:
    """占位题有三种模板，按题干里的一句话清只能清掉三分之一。"""

    from app.quiz.seed import seed_question_bank

    db_path = tmp_path / "purge.db"
    _load("purge_seed_tree", "scripts/seed_knowledge_tree.py").seed(db_path)
    init_quiz_db(db_path)
    seed_question_bank(db_path, target_count=36)

    with connect(db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM real_question").fetchone()[0]
        kinds = {row[0] for row in conn.execute("SELECT DISTINCT question_type FROM real_question")}
    assert before == 36
    assert kinds == {"single", "short", "case"}

    purged = generator.purge_placeholders(db_path)
    assert purged == 36

    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM real_question").fetchone()[0] == 0
        # 考点也一并清掉，不留空考点干扰统计。
        assert conn.execute("SELECT COUNT(*) FROM exam_point").fetchone()[0] == 0


def test_persist_writes_resolvable_questions(generator, seeded_db: Path) -> None:
    """落库后每道题的选项都能解析成图谱实体——归因的前提。"""

    questions, _ = generator.build(seeded_db)
    written = generator.persist(questions, seeded_db)
    assert written == len(questions)

    with connect(seeded_db) as conn:
        rows = conn.execute("SELECT options_json FROM real_question").fetchall()
    assert len(rows) == written

    herb_names = set(generator.load_herbs())
    for (raw,) in rows:
        for option in json.loads(raw):
            assert option["text"] in herb_names

    # 再跑一次不应重复插入（唯一键 + 稳定 id）。
    generator.persist(questions, seeded_db)
    with connect(seeded_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM real_question").fetchone()[0] == written
