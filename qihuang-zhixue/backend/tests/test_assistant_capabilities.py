"""P1~P4 新增能力与编排行为的判据测试。

这份文件守的是「扩展时不许偷懒」这件事本身：

1. **新增产物只加声明，不加分支**：``quiz`` / ``story`` 的产物类型、工序、
   闸门位置全部从 ``CapabilitySpec`` 与 ``constants`` 派生。谁在桥或接口里
   又写了一份 kind 映射，这里会红。
2. **闸门对全体产物一致**：试卷/漫剧也必须「先出方案再动手」，否则对话里
   点一下就直接炼，用户在拿到成品前看不到任何可确认的东西。
3. **查询能力要能真的回答**：``profile.view`` 得给出最弱维度与置信度，
   ``kb.search`` 得给出覆盖率与可引用的原文，不能是 501 或一段套话。
4. **记住前情**：长会话要压摘要，且摘要覆盖到哪一条要落库。
5. **引用既有资源**：``refs`` 要真的进到模型的输入里，而不是被默默丢掉。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.assistant import router as rules
from app.assistant import store
from app.assistant.bridge import kind_for
from app.assistant.prompts import build_messages, digest_of
from app.assistant.tools import JOB_CAPABILITIES
from app.capabilities import registry
from app.capabilities.bootstrap import install
from app.resources.constants import CURRENT_PIPELINE_VERSION, gate_step, step_ids

ACTOR = SimpleNamespace(user_id="u_new_caps")


@pytest.fixture(autouse=True)
def _registry() -> None:
    install()


# --------------------------------------------------------------------------- #
# 新增产物：声明一致
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("capability", "kind"),
    [("quiz.generate", "quiz"), ("story.generate", "story"), ("doc.generate", "doc"), ("ppt.generate", "ppt")],
)
def test_new_artifacts_derive_kind_from_spec(capability: str, kind: str) -> None:
    entry = registry.get(capability)
    assert entry is not None
    assert entry.spec.kind == "job"
    assert entry.spec.artifact is not None
    assert entry.spec.artifact.kind == kind
    # 桥不另写映射：kind 必须与声明一致，否则产物卡与打开地址会各说各话。
    assert kind_for(capability) == kind
    assert capability in JOB_CAPABILITIES


@pytest.mark.parametrize("resource_type", ["ppt", "doc", "mindmap", "quiz", "story"])
def test_every_workshop_pauses_before_generating(resource_type: str) -> None:
    """所有工坊的确认点都落在 design 步，且工序表里真的有这一步。"""

    ids = step_ids(resource_type, CURRENT_PIPELINE_VERSION)
    assert "plan" in ids, f"{resource_type} 缺 plan 步，方案卡无从谈起"
    assert "design" in ids, f"{resource_type} 缺 design 步"
    assert gate_step(resource_type, CURRENT_PIPELINE_VERSION) == "design"


def test_quiz_and_story_plans_have_their_own_shape() -> None:
    """方案不能全都长成文稿的样子：考什么、几场戏，是两类不同的问题。"""

    from app.resources import planning

    quiz = planning._fallback_plan("quiz", "解表药", {}, [])
    story = planning._fallback_plan("story", "麻黄汤证", {}, [])
    assert quiz.kind == "quiz"
    assert story.kind == "story"
    assert [unit.label for unit in quiz.units] != [unit.label for unit in story.units]
    # 方案卡上能改的是 detail。只塞 intent 会让用户看到四行没内容的空白框。
    assert all(unit.detail for unit in quiz.units)
    assert all(unit.detail for unit in story.units)


def test_story_title_drops_the_framing_verb_but_keeps_extra_requests() -> None:
    """「把 X 讲成一出短剧」的壳可以剥，用户加在后面的要求不能剥。"""

    from app.resources.planning import story_subject

    assert story_subject("把麻黄汤证讲成一出短剧") == "麻黄汤证"
    assert story_subject("将桂枝汤编成一幕戏") == "桂枝汤"
    # 后半句是用户真的提了要求，剥掉就等于把他要的东西删了。
    assert story_subject("把麻黄汤证讲成一出短剧并强调禁忌") == "把麻黄汤证讲成一出短剧并强调禁忌"
    assert story_subject("麻黄汤证") == "麻黄汤证"


def test_structured_plan_marks_textbook_units_only_when_docs_exist() -> None:
    """固定骨架不等于免检：检索到条目才标教材，取不到仍要降级成模型补充。"""

    from app.resources import planning

    units = (("考点范围", "圈定考什么", True), ("题型与题量", "各出几道", False))
    docs = [{"name": "麻黄"}]
    tiers = {"knowledge_base": ["麻黄 · 性味"], "user_material": [], "model": []}

    grounded = planning._structured_plan("quiz", "出一份试卷", units, "解表药", docs, {}, tiers)  # type: ignore[arg-type]
    sources = [unit.source for unit in grounded.units]
    assert sources == ["knowledge_base", "model"], "题型配比是编排决定，不该假称来自教材"

    bare = planning._structured_plan("quiz", "出一份试卷", units, "解表药", [], {}, {**tiers, "knowledge_base": []})  # type: ignore[arg-type]
    assert all(unit.source == "model" for unit in bare.units)
    assert all(unit.evidence == [] for unit in bare.units)


# --------------------------------------------------------------------------- #
# 查询能力
# --------------------------------------------------------------------------- #


def test_profile_view_reports_weakest_dimension(tmp_path: Path) -> None:
    from app.capabilities.handlers import profile as profile_handler

    payload = profile_handler.ProfileViewInput()
    result = asyncio.run(profile_handler.handler(payload, ACTOR, invocation_id="inv_1", db_path=tmp_path / "p.db"))
    body = result.body
    assert body["ok"] is True
    assert body["capability"] == "profile.view"
    # 空画像要如实说「还没有证据」，不能编一个最弱维度出来。
    assert body["has_profile"] is False
    assert "还没有" in body["summary"]
    assert body["weakest"] == []


def test_kb_search_reports_coverage_and_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.capabilities.handlers import kb as kb_handler

    async def fake_retrieve(topic: str, top_k: int = 5):
        return [
            {
                "id": "herb_mahuang",
                "name": "麻黄",
                "category": "解表药",
                "nature": "温",
                "flavor": ["辛", "微苦"],
                "meridians": ["肺", "膀胱"],
                "effects": ["发汗解表", "宣肺平喘"],
            }
        ]

    monkeypatch.setattr(kb_handler, "retrieve_resource_docs", fake_retrieve)
    payload = kb_handler.KbSearchInput(query="麻黄")
    body = asyncio.run(kb_handler.handler(payload, ACTOR, invocation_id="inv_kb")).body
    assert body["coverage"] == "in_corpus"
    assert body["items"][0]["name"] == "麻黄"
    assert "发汗解表" in body["items"][0]["quote"]


def test_kb_search_says_out_of_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.capabilities.handlers import kb as kb_handler

    async def empty(topic: str, top_k: int = 5):
        return []

    monkeypatch.setattr(kb_handler, "retrieve_resource_docs", empty)
    body = asyncio.run(kb_handler.handler(kb_handler.KbSearchInput(query="外星药材"), ACTOR, invocation_id="i")).body
    assert body["coverage"] == "out_of_corpus"
    assert body["items"] == []


# --------------------------------------------------------------------------- #
# 规则兜底：认出新增的两类产物与两条查询
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "capability"),
    [
        ("出一份解表药的卷子", "quiz.generate"),
        ("考考我麻黄和桂枝的区别", "quiz.generate"),
        ("把麻黄汤证讲成一出短剧", "story.generate"),
        ("编个剧本讲讲十八反", "story.generate"),
        ("我的画像里哪块最弱", "profile.view"),
        ("教材里有没有说桂枝反藜芦", "kb.search"),
        ("做一张解表药的导图", "mindmap.generate"),
        ("给规培医生讲十八反，做 12 页", "ppt.generate"),
        ("帮我做个计划，我想学中药学，两年后考研", "plan.create"),
        ("我基础差，从哪开始补", "plan.create"),
    ],
)
def test_rules_route_to_new_capabilities(text: str, capability: str) -> None:
    route = rules.route(text)
    assert route is not None, f"{text} 没命中任何规则"
    assert route.capability == capability


def test_plan_route_passes_the_goal_not_a_query_field() -> None:
    """plan.create 收的是 goal；塞 query 会被白名单打回，这条规则就永远失败。"""

    route = rules.route("我想学中药学，两年后考研")
    assert route is not None
    assert route.capability == "plan.create"
    assert set(route.args) == {"goal"}


def test_profile_route_carries_no_bogus_argument() -> None:
    """profile.view 不收 question，塞进去会被白名单打回，等于这条规则永远失败。"""

    route = rules.route("我最弱的是哪一块")
    assert route is not None
    assert route.args == {}


# --------------------------------------------------------------------------- #
# 记忆：摘要与引用
# --------------------------------------------------------------------------- #


def test_summary_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "s.db"
    conversation = store.create_conversation("u1", "摘要", db)
    cid = conversation["conversation_id"]
    assert store.get_summary(cid, db)["summary"] == ""

    store.update_summary(cid, "用户要一份解表药的导图，已给出方案。", "m_1", db)
    got = store.get_summary(cid, db)
    assert "解表药" in got["summary"]
    assert got["summary_upto"] == "m_1"


def test_digest_keeps_only_what_user_said() -> None:
    history = [
        {"role": "user", "content": "做一张解表药的导图"},
        {"role": "assistant", "content": "好的，先给你方案。"},
        {"role": "user", "content": "再加两味药"},
    ]
    text = digest_of(history)
    assert "解表药" in text and "再加两味药" in text
    assert "好的，先给你方案" not in text


def test_refs_reach_the_model_input() -> None:
    messages = build_messages("在第三支下再加两味药", [], refs=["r_123"])
    assert "r_123" in messages[-1]["content"]


def test_summary_is_background_not_turns() -> None:
    """摘要要进 system，不能伪装成一条历史消息——否则模型会把它当成用户说的话。"""

    messages = build_messages("我们刚才说要几页", [], summary="用户要一份 12 页的讲稿。")
    assert messages[0]["role"] == "system"
    assert "12 页" in messages[0]["content"]
    assert len(messages) == 2


# --------------------------------------------------------------------------- #
# 端到端：试卷 / 漫剧也要「先出方案，确认后才炼」
# --------------------------------------------------------------------------- #


class _FakePayload:
    def __init__(self, data: dict) -> None:
        self._data = data

    def model_dump(self, mode: str = "json") -> dict:  # noqa: ARG002 - 对齐 pydantic 签名
        return self._data


class _FakeEnvelope:
    def __init__(self, payload: dict) -> None:
        self.payload = _FakePayload(payload)
        self.sources: list = []


def _collect(iterator) -> list[dict]:
    async def run() -> list[dict]:
        return [event async for event in iterator]

    return asyncio.run(run())


def _seed(tmp_path: Path) -> Path:
    db = tmp_path / "flow.db"
    from app.path.store import upsert_node

    upsert_node({"id": "general", "name": "中药学总论", "level": "domain"}, db)
    return db


@pytest.mark.parametrize(
    ("capability", "payload", "fake_content", "expected_kind"),
    [
        ("quiz.generate", {"query": "解表药", "count": 6}, {"questions": [{"id": "q1"}, {"id": "q2"}]}, "quiz"),
        ("story.generate", {"query": "麻黄汤证", "scenes": 3}, {"scenes": [{"index": 1}, {"index": 2}, {"index": 3}], "blocks": []}, "story"),
    ],
)
def test_new_generation_pauses_then_produces_a_resource(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capability: str,
    payload: dict,
    fake_content: dict,
    expected_kind: str,
) -> None:
    """两道闸门、一次产物、一条可打开的地址。

    生成器被替换成确定性的假信封：这里要证的是**管线**（暂停→确认→续跑→落库），
    不是模型出题好不好——后者有各自的 agent 测试。
    """

    from app.capabilities import dispatch
    from app.resources import service as resource_service
    from app.resources import storage

    async def fake_quiz(topic: str, retrieved_docs, profile=None):  # noqa: ANN001, ARG001
        return _FakeEnvelope(fake_content)

    async def fake_story(topic: str, retrieved_docs, profile=None):  # noqa: ANN001, ARG001
        return dict(fake_content), []

    monkeypatch.setattr(resource_service, "generate_quiz_resource", fake_quiz)
    monkeypatch.setattr(resource_service, "generate_story_resource", fake_story)

    db = _seed(tmp_path)
    accepted = asyncio.run(dispatch.invoke(capability, payload, ACTOR, db_path=db))
    assert accepted.http_status == 202, accepted.body
    assert accepted.body["artifact"]["kind"] == expected_kind
    task_id = accepted.body["task_id"]

    first = _collect(resource_service.stream_task(task_id, ACTOR.user_id, db))
    assert first[-1]["event"] == "pause", "没停就开炼，方案卡无从确认"
    plan = next(event for event in first if event["event"] == "plan")["payload"]["plan"]
    assert plan["kind"] == expected_kind

    # 等价于 POST /plan/confirm 的写入部分：确认后从闸门那一步续跑。
    task = storage.get_task(task_id, ACTOR.user_id, db)
    steps = dict(task["steps_done"])
    steps["plan"] = plan
    steps["plan_confirmed"] = True
    steps.pop("awaiting_plan", None)
    steps.pop("active_step", None)
    storage.update_task(task_id, ACTOR.user_id, status="pending", current_step=1, steps_done=steps, meta={"confirmed_plan": plan}, db_path=db)
    resource_service.reset_progress(task_id)

    rest = _collect(resource_service.stream_task(task_id, ACTOR.user_id, db))
    done = rest[-1]
    assert done["event"] == "done", done
    assert done["payload"]["resource_id"]
    # 产物上的 verify 必须跟着 done 一起出去，否则产物卡没法显示依据情况。
    assert "grounded" in done["payload"]["verify"]

    stored = storage.get_task(task_id, ACTOR.user_id, db)
    assert stored["status"] == "done"
    assert stored["resource_type"] == expected_kind


# --------------------------------------------------------------------------- #
# 扩展接缝：新增一种能力，编排层零改动
# --------------------------------------------------------------------------- #


def test_new_capability_needs_no_agent_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """注册一种新产物，工具面、catalog、产物类型三处自动跟上。

    这是 P4 的承诺：新增能力 = 一份 handler + 白名单一行 + 一份测试。
    如果哪天要在 loop / bridge 里再写一个 if，这条会先红。
    """

    from app.assistant import tools as tool_module
    from app.capabilities.spec import ArtifactSpec, CapabilityResult, CapabilitySpec
    from app.capabilities.handlers import CapabilityInput

    class _Input(CapabilityInput):
        query: str = ""

    async def handler(payload, actor, *, invocation_id, db_path=None):  # noqa: ANN001, ARG001
        return CapabilityResult(http_status=200, body={"ok": True})

    spec = CapabilitySpec(
        name="game.generate",
        version="1.0",
        kind="job",
        title="生成开药闯关",
        one_line="按证型出一关配药题",
        use_when=("要一个可玩的练习",),
        avoid_when=("要静态题目：用 quiz.generate",),
        triggers=("闯关", "开药游戏"),
        input_model=_Input,
        output_shape={"ok": "bool"},
        audiences=("agent",),
        artifact=ArtifactSpec(kind="game", open_url_template="/clinic?resource={resource_id}"),
    )
    registry.register(spec, handler)
    monkeypatch.setattr(tool_module, "EXPOSED", (*tool_module.EXPOSED, "game.generate"))

    try:
        schemas = {item["function"]["name"] for item in tool_module.tool_schemas()}
        assert "game_generate" in schemas
        assert "开药闯关" in tool_module.catalog_text()
        # 产物类型从声明派生：没有任何一处需要为 game 写映射。
        assert kind_for("game.generate") == "game"
    finally:
        # 注册表是全局的：不收尾会污染后面的用例（别的用例会看见一个没有工序的
        # job 能力，然后报一个跟它毫无关系的错）。
        install(force=True)


def test_story_fallback_is_a_readable_script() -> None:
    """模型没给出场次时，兜底也得是一份能读的剧本，而不是空页。"""

    from app.agents.story_agent import fallback_story

    content = fallback_story("麻黄", [])
    assert len(content["scenes"]) >= 3
    assert any(block["type"] == "heading" for block in content["blocks"])
    assert any(block["props"].get("speaker") for block in content["blocks"] if block["type"] == "paragraph")
    # 来源要标出来：兜底内容同样是「教材复述」，不能冒充模型原创。
    assert content["fallback"]


def test_items_verify_counts_scenes_for_story() -> None:
    """漫剧数的是场次，不是 items。数错字段会让产物卡上写「共 0 条」。"""

    from app.resources.service import _items_verify

    stats = _items_verify("story", {"scenes": [{"index": 1}, {"index": 2}]}, 3)
    assert stats["items"] == 2
    assert stats["grounded"] is True
    assert "2 条" in stats["note"]


# --------------------------------------------------------------------------- #
# 规划能力：用户说「想学什么、多久考」时，得真的排出一条路线
# --------------------------------------------------------------------------- #


def _fake_plan(stages: list[tuple[str, int]], nodes: list[tuple[str, int]]):
    """仿 ``PlanResponse``：handler 读的是 ``.stages`` / ``.nodes``（L1 只排阶段，
    ``nodes`` 恒为空，知识点靠 L2 展开）。"""

    from types import SimpleNamespace

    return SimpleNamespace(
        stages=[SimpleNamespace(domain_id=domain, order=order, reason="先修图topo_sort第1位") for domain, order in stages],
        nodes=[SimpleNamespace(node_id=node, stage=stage, seq=index + 1) for index, (node, stage) in enumerate(nodes)],
        suggestion="所有画像维度均为保守默认",
        fallback=False,
    )


def test_plan_capability_turns_stages_into_plain_words(monkeypatch) -> None:
    """阶段 reason 是算法日志（「先修图topo_sort第1位」），不能原样念给用户。"""

    from app.capabilities.handlers import plan as plan_handler

    async def fake_l1(user_id: str, **kwargs):  # noqa: ANN001, ARG001
        return _fake_plan([("general", 1), ("d1", 2)], [])

    class _Expanded:
        from types import SimpleNamespace

        nodes = [
            SimpleNamespace(
                node_id="p1",
                seq=1,
                stage=1,
                resource_hint="图文讲解",
                highlight="normal",
                target_mastery=0.8,
                reason="先修图topo_sort第1位",
            )
        ]

    async def fake_l2(user_id: str, domain_id: str, **kwargs):  # noqa: ANN001, ARG001
        return _Expanded()

    monkeypatch.setattr(plan_handler, "create_l1_plan", fake_l1)
    monkeypatch.setattr(plan_handler, "create_l2_plan", fake_l2)
    monkeypatch.setattr(plan_handler, "_names", lambda _path: {"general": {"name": "中药学总论"}, "d1": {"name": "解表药"}})

    body = asyncio.run(plan_handler.handler(plan_handler.PlanCreateInput(goal="两年后考研", total_days=730), ACTOR, invocation_id="i")).body

    assert body["start_stage"] == "中药学总论"
    assert body["has_nodes"] is True
    assert body["summary"].startswith("你要的是「两年后考研」")
    # 阶段说明里不能出现算法内部词汇。
    assert all("topo_sort" not in stage["why"] for stage in body["stages"])


def test_plan_capability_says_so_when_nothing_came_back(monkeypatch) -> None:
    """知识树为空时不能编一份计划出来。"""

    from app.capabilities.handlers import plan as plan_handler

    async def fake_l1(user_id: str, **kwargs):  # noqa: ANN001, ARG001
        return _fake_plan([], [])

    monkeypatch.setattr(plan_handler, "create_l1_plan", fake_l1)
    monkeypatch.setattr(plan_handler, "_names", lambda _path: {})

    body = asyncio.run(plan_handler.handler(plan_handler.PlanCreateInput(), ACTOR, invocation_id="i")).body
    assert body["stages"] == []
    assert body["has_nodes"] is False
    assert "没排出来" in body["summary"]


# --------------------------------------------------------------------------- #
# 兜底话术：不许把用户推回选项菜单
# --------------------------------------------------------------------------- #


def test_fallback_answer_does_not_push_the_user_back_to_a_menu() -> None:
    """这是用户直接提过的问题：说「我想学习中药学，两年后考研」，
    回一句「你可以做导图、做幻灯、写文稿」等于把问题原样退回去。"""

    text = rules.compose_fallback_answer([], "我想学习中药学，用以两年之后的考研")
    assert "我想学习中药学" in text, "至少要接住用户说的那句话"
    for menu in ("做导图", "做幻灯", "写文稿", "查两味药"):
        assert menu not in text, f"兜底话术不该列功能清单：{menu}"


def test_fallback_answer_reads_the_plan_summary() -> None:
    """有规划结果时，兜底话术直接用规划自己那句话，不再另编。"""

    text = rules.compose_fallback_answer([("plan.create", {"summary": "从「中药学总论」开始，全程 22 个阶段"}, True)], "考研")
    assert text == "从「中药学总论」开始，全程 22 个阶段"


def test_plan_capability_passes_the_goal_into_planning(monkeypatch) -> None:
    """``goal`` 不能只用来拼摘要：范围与规划参数都从这句话里读。

    真机故障：用户说「先中药，15 天后期末考」，拿到的却是从「中医学的哲学基础」
    起步的六门课先修链。原因是这条 handler 只把 time_budget 传下去，
    goal 在离开 handler 的那一刻就没了——规划器既不知道范围，也不知道在备考。
    """

    from app.capabilities.handlers import plan as plan_handler

    seen: dict = {}

    async def fake_l1(user_id: str, **kwargs):  # noqa: ANN001, ARG001
        seen.update(kwargs)
        return _fake_plan([("general", 1), ("d01", 2)], [])

    async def fake_l2(user_id: str, domain_id: str, **kwargs):  # noqa: ANN001, ARG001
        return SimpleNamespace(nodes=[])

    monkeypatch.setattr(plan_handler, "create_l1_plan", fake_l1)
    monkeypatch.setattr(plan_handler, "create_l2_plan", fake_l2)
    monkeypatch.setattr(
        plan_handler,
        "_names",
        lambda _path: {"general": {"name": "中药学总论"}, "d01": {"name": "解表药"}},
    )

    body = asyncio.run(
        plan_handler.handler(
            plan_handler.PlanCreateInput(goal="先中药，15 天后期末考", total_days=30), ACTOR, invocation_id="i"
        )
    ).body

    assert seen["request_text"] == "先中药，15 天后期末考"
    # 范围要结构化交出来：图里排不出的科目（日语）模型才知道边界在哪
    assert body["scope"]["scoped"] is True
    assert body["scope"]["covered"] == [{"subject_id": "herb", "name": "中药学"}]
    assert "范围只到《中药学》" in body["summary"]
    assert body["total_stages"] == 2
