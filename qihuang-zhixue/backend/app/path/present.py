"""把一份**已谈定**的计划呈现成导图或文稿（计划 §8）。

这里的核心不是"生成"，是"不要重新生成"。计划的唯一价值就是结构已经谈好：
路径、顺序、每节要学什么都确认过了。所以呈现层**不调模型、不重新谋篇**，
只把既定结构翻译成几种可读形态：

- 导图：学科 → 章 → 节 → 知识点，叶子带 ``node_id``，点一下能跳回地图；
- 文稿：三种体裁（见下），每种回答一个不同的问题。

## 为什么文稿要有三种

以前只有一种：把知识点逐条铺成 ``名称　目标掌握度 0.82　图文讲解+随堂测验``。
那是一张**没有任何决策信息**的清单——用户看完不知道从哪开始、要花多少天、
为什么砍了那几章，也打印不出来当计划用。而"一份计划该长什么样"本来就没有
唯一答案：赶考的人要任务卡，带教老师要考纲对照表，自己心里没底的人要说明书。

所以按"它要回答什么"分三种，各给一份：

* ``route_guide`` **路线说明书** —— 回答"为什么这么排、第一步是什么"；
* ``task_cards`` **学习任务卡** —— 回答"今天具体做什么"；
* ``syllabus_map`` **考纲对照表** —— 回答"每一节在教材哪一章、多少考点"。

体裁选择用确定性信号（显式 > 画像偏好 > 规模 > 默认），不交给模型：
同一个人两次问同一件事，拿到不同格式，用户第一反应是"系统不稳定"。
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence
from pathlib import Path

from app.capabilities.spec import ArtifactSpec
from app.path.store import DB_PATH, list_nodes
from app.profile.store import get_profile
from app.resources import storage

#: 两种体裁的落地页都在这份声明里。**放在这里而不是 handler 里**：
#: 拼地址的人（本模块）和声明地址的人必须是同一处。分开写的后果已经出现过一次——
#: 导图的模板对得上，文稿那条只在 ``open_url_for`` 的 if/else 里，能力文档里查不到。
ARTIFACT_SPEC = ArtifactSpec(
    kind="mindmap",
    open_url_template="/mindmap?resource={resource_id}",
    variants=(("doc", "/resources/{resource_id}"),),
)

#: 知识点数量超过这个规模，导图在手机上就不可读了——手机上导图是缩略图，
#: 25 个叶子挤在一屏里只能靠拖动找节点，不如看文稿。
MINDMAP_NODE_LIMIT = 24
#: 章数超过这个值也判给文稿：导图一级分支太多会横向溢出。
MINDMAP_STAGE_LIMIT = 6

VISUAL_LABELS = {"知识图谱", "思维导图"}
TEXT_LABELS = {"教材精读", "讲义"}

#: 文稿体裁。值会进能力参数与前端选择器，改名要同步改三处。
DOC_ARCHETYPES: tuple[str, ...] = ("route_guide", "task_cards", "syllabus_map")

#: 三种体裁的「说明书」——**这就是给用户看的那四条**。
#:
#: 以前这四段只写在三个 builder 的 docstring 里：写的人知道，选的人看不到。
#: 于是"三种体裁"在界面上退化成三个看不懂的按钮，用户只能回到默认那一个。
#: 现在它是唯一事实源：选择器的卡片、能力响应、文档都从这张表取。
#:   contents  应含内容   form      展现形式
#:   principle 创作原理   merit     适用价值
#:   when      什么时候该选它（一句话，放在卡片第一行）
#:
#: "适用价值"叫 `merit` 而不是 `value`：选项本身还有一个 `value`（体裁名），
#: 两个 value 撞在一起时 `{"value": key, **spec}` 会把体裁名覆盖成一段中文。
DOC_ARCHETYPE_SPECS: dict[str, dict[str, str]] = {
    "route_guide": {
        "label": "路线说明书",
        "when": "想知道「为什么这么排、从哪开始」",
        "contents": "一句结论（起点 / 总量 / 预计时长）、取舍依据、六科份量表、"
        "分章编号的学习顺序、第一步与下一步、三种调整方式",
        "form": "标题 + 结论卡 + 表格 + 分级标题 + 编号清单",
        "principle": "做决策需要一个**承诺点**（从这件事开始）和一个**边界**（不学什么）。"
        "把这两样放在最前面，后面的细节才有落点；反过来先堆清单，人读完也不知道从哪下手。",
        "merit": "自学推进、向老师或家长交代计划、打印出来贴在桌上",
    },
    "task_cards": {
        "label": "学习任务卡",
        "when": "期限紧、每天要动手时",
        "contents": "一步一张卡：学哪一节、几个知识点、大约多久、做完勾掉；"
        "卡与卡之间写明前置关系，卡内只放动作不放理由",
        "form": "卡片流（一张卡 = 一个动作 + 一个勾选 + 预计时长），按天或按步分页",
        "principle": "把「计划」翻译成「动作」。执行阶段的人不想再读理由，"
        "他只想看到下一件要做的事——所以理由全部上移到说明书，卡片只留可执行项。",
        "merit": "短期冲刺、每天打开就能开工、进度一眼可见",
    },
    "syllabus_map": {
        "label": "考纲对照表",
        "when": "以考试为目标、需要核对覆盖",
        "contents": "教材章 → 节 → 知识点 → 是否在本次路线内 → 考点频次，逐行可核对；"
        "没纳入的行留空并标注原因，不隐藏",
        "form": "多列对照表，行列与教材一致，缺口留白而不是省略",
        "principle": "应试场景的第一需求是「有没有漏」。对照表把缺口**显性化**——"
        "留白比任何叙述都可信，因为用户自己就能逐条查。",
        "merit": "考前查漏、跟教材逐条校对、给老师看覆盖度",
    },
}
DOC_ARCHETYPE_LABELS: dict[str, str] = {key: spec["label"] for key, spec in DOC_ARCHETYPE_SPECS.items()}
DEFAULT_DOC_ARCHETYPE = "route_guide"

#: 导图的说明书。和三种文稿摆在同一张选择器里，所以四条说明的字段一模一样——
#: 少一条，用户就会看到"三种有解释、一种只有名字",更选不出来。
MINDMAP_SPEC: dict[str, str] = {
    "label": "思路导图",
    "when": "想看清「这一堆东西怎么挂在一起」",
    "contents": "学科 → 章 → 节 → 知识点四层，每个叶子都能点回人体地图上那个穴位",
    "form": "可展开折叠的树（导图工坊同一套渲染），纵向四面发散，手机上可拖动",
    "principle": "结构本身就是记忆线索：先看见「它在哪一层」，再决定记什么。"
    "清单只能表达先后，导图才能表达归属。",
    "merit": "复盘时俯瞰全局、讲给别人听、发现自己漏了哪一支",
}


def choose_presentation(
    profile: Any,
    nodes: list[dict[str, Any]],
    *,
    stages: int = 0,
    prefer: str = "",
) -> tuple[str, str]:
    """返回 ``(kind, reason)``。``kind`` 只可能是 ``mindmap`` / ``doc``。"""

    choice = (prefer or "").strip()
    if choice in ("mindmap", "doc"):
        return choice, "你指定了这个格式"
    if choice in ("auto", ""):
        choice = ""

    preference = getattr(profile, "resource_preference", None)
    confidence = float(getattr(preference, "confidence", 0.0) or 0.0)
    label = str(getattr(preference, "label", "") or "")
    style = str(getattr(getattr(profile, "learning_style", None), "label", "") or "")
    if confidence >= 0.5:
        if label in VISUAL_LABELS or style == "视觉型":
            return "mindmap", f"你的资源偏好是「{label}」（置信度 {confidence:.2f}）"
        if label in TEXT_LABELS:
            return "doc", f"你的资源偏好是「{label}」"

    total_stages = stages or len({node.get("stage") for node in nodes})
    if len(nodes) > MINDMAP_NODE_LIMIT or total_stages > MINDMAP_STAGE_LIMIT:
        return "doc", f"{len(nodes)} 个知识点跨 {total_stages} 个阶段，导图在手机上读不动"
    return "mindmap", "知识点集中，导图能点回地图"


def choose_doc_archetype(
    nodes: Sequence[dict[str, Any]],
    *,
    preferred: str = "",
    total_days: int = 0,
) -> tuple[str, str]:
    """文稿体裁的确定性选择。返回 ``(archetype, reason)``。"""

    if preferred in DOC_ARCHETYPES:
        return preferred, f"你选了「{DOC_ARCHETYPE_LABELS[preferred]}」"
    if total_days and total_days <= 120 and len(nodes) <= 160:
        # 短期冲刺：用户最需要"今天做什么"，说明书那种"为什么"他已经知道了。
        return "task_cards", f"{total_days} 天的短期计划，切成每天能勾的任务更用得着"
    if len(nodes) >= 200:
        # 铺得很开：先给他一张能核对的对照表，而不是几百行"目标掌握度"。
        return "syllabus_map", f"{len(nodes)} 个知识点，先给一张能逐条核对的表"
    return DEFAULT_DOC_ARCHETYPE, "默认给说明书：讲清为什么这么排、第一步做什么"


def _catalog(db_path: Path) -> dict[str, dict[str, Any]]:
    try:
        return {str(node["id"]): node for node in list_nodes(db_path=db_path)}
    except Exception:  # noqa: BLE001 - 读不到名字就用 id，别让整条能力炸掉
        return {}


def _topic(text: str, limit: int = 30) -> str:
    clean = " ".join(str(text or "").split())
    return clean[:limit] or "未命名"


def _hours(minutes: int) -> str:
    if minutes <= 0:
        return "—"
    if minutes < 60:
        return f"{minutes} 分钟"
    hours = minutes / 60
    return f"{hours:.1f} 小时" if hours < 10 else f"{hours:.0f} 小时"


def _group_by_subject(stages: Sequence[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """按学科切块，保持传入顺序（阶段顺序就是学科成块的）。"""

    out: list[tuple[str, list[dict[str, Any]]]] = []
    for row in stages:
        name = str(row.get("subject_name") or "其他")
        if out and out[-1][0] == name:
            out[-1][1].append(row)
        else:
            out.append((name, [row]))
    return out


def _group_by_chapter(stages: Sequence[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    out: list[tuple[str, list[dict[str, Any]]]] = []
    for row in stages:
        name = str(row.get("chapter_title") or "")
        if out and out[-1][0] == name:
            out[-1][1].append(row)
        else:
            out.append((name, [row]))
    return out


# --------------------------------------------------------------------------- 导图


def _empty_root(title: str) -> dict[str, Any]:
    return {"id": "root", "topic": _topic(title), "children": []}


def _branch(parent: dict[str, Any], key: str, topic: str, meta: dict[str, Any]) -> dict[str, Any]:
    existing = next((child for child in parent["children"] if child.get("meta", {}).get("branch_id") == key), None)
    if existing is not None:
        return existing
    node = {"id": f"n{abs(hash(key)) % 10**9}", "topic": _topic(topic, 24), "meta": {"branch_id": key, **meta}, "children": []}
    parent["children"].append(node)
    return node


def build_mindmap(
    *,
    title: str,
    nodes: Iterable[dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
    stages: Sequence[dict[str, Any]] = (),
    structure: str = "",
    theme: str = "",
) -> dict[str, Any]:
    """**学科 → 章 → 节 → 知识点**。

    层级从 ``stages``（确认过的单元行）来，不从知识图的祖先链来：图里没有"学科"
    这一层，而"章 → 节"在总论那种篇容器里是错的（总论一个域装着七个章）。
    同一章的小节因此必然相邻——这正是用户在图上要看到的"能连成一条路"。
    """

    root = _empty_root(title)
    stage_rows = {str(row.get("stage_id") or row.get("unit_id") or row.get("domain_id") or ""): row for row in stages}
    counter = 0
    seen: set[str] = set()

    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id") or "")
        if not node_id:
            continue
        stage_key = str(node.get("unit_id") or node.get("stage_id") or node.get("domain_id") or "")
        row = stage_rows.get(stage_key) or {}
        subject = str(row.get("subject_name") or node.get("subject_name") or "")
        chapter = str(row.get("chapter_title") or node.get("chapter_title") or "")
        unit_title = str(row.get("unit_title") or row.get("name") or node.get("unit_title") or "")

        if chapter or unit_title:
            parent = _branch(root, f"subject:{subject or '学习路线'}", subject or "学习路线", {"level": "subject", "evidence": "plan"})
            if chapter:
                parent = _branch(parent, f"chapter:{subject}:{chapter}", chapter, {"level": "chapter", "evidence": "textbook"})
            if unit_title:
                parent = _branch(
                    parent,
                    f"unit:{stage_key or unit_title}",
                    unit_title,
                    {"level": "unit", "evidence": "textbook", "stage_id": stage_key},
                )
        else:
            # 没有确认过的单元行（老快照、外部调用）：退回知识图祖先链。
            # **逐点退**，不要整棵树退成一层——那样"解表药 / 发散风寒药"会平铺，
            # 用户看到的是一堆并列的穴位，正是这次要消掉的现象。
            parent = _place_by_catalog(root, catalog, node_id)

        if node_id in seen:
            continue
        seen.add(node_id)
        counter += 1
        parent["children"].append(
            {
                "id": f"p{counter}",
                "topic": _topic(node.get("name") or node_id),
                "meta": {
                    "node_id": node_id,
                    "acupoint": node.get("acupoint_name") or "",
                    "target_mastery": round(float(node.get("target_mastery") or 0.0), 2),
                    "evidence": "textbook",
                    "level": "point",
                },
                "children": [],
            }
        )

    return {
        "template_id": theme or "indigo",
        "choice_reason": "按已确认的学习路线生成，未重新谋篇",
        "mindmap_data": {"nodeData": root},
        "sources": [],
        "intent": "plan",
        "archetype": "plan",
        "structure_hint": structure or "logic-right",
        "degraded": False,
    }


def _chain(catalog: dict[str, dict[str, Any]], node_id: str) -> list[dict[str, Any]]:
    """从根到该点的祖先链（不含自身）。用于把层级补出来。"""

    chain: list[dict[str, Any]] = []
    current = catalog.get(node_id)
    seen: set[str] = set()
    while current is not None and current.get("parent_id") and current["parent_id"] in catalog:
        parent_id = str(current["parent_id"])
        if parent_id in seen:
            break
        seen.add(parent_id)
        current = catalog[parent_id]
        chain.append(current)
    return list(reversed(chain))


def _place_by_catalog(root: dict[str, Any], catalog: dict[str, dict[str, Any]], node_id: str) -> dict[str, Any]:
    """按知识图祖先链把点挂到最深的容器下，返回该容器。"""

    parent = root
    for item in _chain(catalog, node_id):
        parent = _branch(
            parent,
            f"cat:{item['id']}",
            str(item.get("name") or item["id"]),
            {"level": item.get("level", ""), "evidence": "textbook"},
        )
    return parent


# --------------------------------------------------------------------------- 文稿


def _block(id: str, type: str, text: str = "", *, props: dict[str, Any] | None = None, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": id, "type": type, "text": text, "props": props or {}, "meta": meta or {}}


def _totals_of(stages: Sequence[dict[str, Any]], nodes: Sequence[dict[str, Any]]) -> dict[str, int]:
    return {
        "units": len(stages),
        "chapters": len({(str(row.get("domain_id") or ""), str(row.get("chapter_title") or "")) for row in stages}),
        "points": len(nodes),
        "minutes": sum(int(row.get("estimated_minutes") or 0) for row in stages),
    }


def _row_meta(row: dict[str, Any]) -> dict[str, Any]:
    """文稿块回到地图所需的锚点。

    ``stage_id`` 是安排表的行键（一穴一行），地图按它定位到对应穴位；
    ``domain_id`` 兜底给只认域的老页面。两者都空时前端不显示"去地图"。
    """

    return {
        "stage_id": str(row.get("stage_id") or row.get("unit_id") or row.get("domain_id") or ""),
        "unit_id": str(row.get("unit_id") or ""),
        "domain_id": str(row.get("domain_id") or ""),
        "chapter_title": str(row.get("chapter_title") or ""),
    }


def build_route_guide(
    *,
    title: str,
    nodes: Sequence[dict[str, Any]],
    stages: Sequence[dict[str, Any]],
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """**路线说明书**：回答"为什么这么排、从哪开始、怎么改"。

    应含内容：一句结论（起点/总量/预计时长）、取舍依据、总量表、分科顺序、
    第一步与下一步、三条调整方式。
    展现形式：标题 + 结论卡 + 表格 + 分级标题 + 编号清单。
    创作原理：人做决策需要一个"承诺点"（从这件事开始）和一个"边界"（不学什么），
    说明书把这两样放在最前面，后面的细节才有落点。
    适用价值：自学、向老师/家长交代计划、打印出来贴在桌上。
    """

    info = extras or {}
    totals = _totals_of(stages, nodes)
    first = stages[0] if stages else {}
    second = stages[1] if len(stages) > 1 else {}
    budget = info.get("budget") or {}
    skipped = info.get("skipped_units") or []

    blocks: list[dict[str, Any]] = [_block("h_root", "heading1", _topic(title, 60))]
    lead = (
        f"这份路线共 {totals['units']} 节、{totals['points']} 个知识点，"
        f"按当前目标预计 {_hours(totals['minutes'])}。"
    )
    if first:
        lead += f"**从「{first.get('unit_title') or first.get('name')}」开始**"
        lead += f"（第 1 / {totals['units']} 步，约 {_hours(int(first.get('estimated_minutes') or 0))}）。"
    blocks.append(_block("b_lead", "callout", lead, props={"tone": "key", "icon": "始"}))

    if budget.get("total_days"):
        blocks.append(
            _block(
                "b_budget",
                "paragraph",
                f"时间预算：{int(budget['total_days'])} 天 × 每天 {int(budget.get('daily_minutes') or 90)} 分钟。"
                f"按这个预算，平均每天要推进约 {_hours(max(1, totals['minutes'] // max(1, int(budget['total_days']))))}。",
            )
        )

    # ① 为什么这么排 —— 直接从已确认的取舍里取，不重新编理由
    blocks.append(_block("h_why", "heading2", "为什么这么排"))
    reasons = [str(item) for item in (info.get("reasons") or []) if str(item).strip()]
    if reasons:
        for index, reason in enumerate(reasons[:4], 1):
            blocks.append(_block(f"r_why_{index}", "bulleted", reason))
    else:
        blocks.append(_block("r_why_default", "paragraph", "按教材章节顺序与前修关系推进；同一章的内容放在一起，学完再进入下一章。"))
    if skipped:
        names = "、".join(str(item.get("unit_title") or item.get("name") or "") for item in skipped[:6])
        rest = f" 等 {len(skipped)} 节" if len(skipped) > 6 else ""
        blocks.append(
            _block(
                "b_skipped",
                "callout",
                f"这次先不学：{names}{rest}。这些不删库，随时可以在「定取舍」里加回来。",
                props={"tone": "info", "icon": "舍"},
            )
        )

    # ② 总量表 —— 一眼看清每门课的份量
    blocks.append(_block("h_total", "heading2", "六门课各占多少"))
    rows: list[list[str]] = []
    for subject, group in _group_by_subject(stages):
        rows.append(
            [
                subject,
                str(len(group)),
                str(sum(int(row.get("point_count") or 0) for row in group)),
                _hours(sum(int(row.get("estimated_minutes") or 0) for row in group)),
            ]
        )
    if rows:
        blocks.append(
            _block(
                "t_subject",
                "table",
                "",
                props={"header": True, "columns": ["学科", "节数", "知识点", "预计"], "rows": rows},
            )
        )

    # ③ 顺序 —— 按章分组的编号清单，节名后面只挂"多少味药/多少首方"
    blocks.append(_block("h_order", "heading2", "学习顺序"))
    counter = 0
    for chapter, group in _group_by_chapter(stages):
        blocks.append(_block(f"h_ch_{abs(hash(chapter)) % 10**8}", "heading3", chapter or "未分章"))
        for row in group:
            counter += 1
            unit_title = str(row.get("unit_title") or row.get("name") or "")
            blocks.append(
                _block(
                    f"step_{counter}",
                    "numbered",
                    f"{unit_title}　{int(row.get('point_count') or 0)} 个知识点 · 约 {_hours(int(row.get('estimated_minutes') or 0))}",
                    props={"order": counter, "stage_id": str(row.get("stage_id") or row.get("domain_id") or "")},
                    meta=_row_meta(row),
                )
            )

    # ④ 第一步 + 下一步 —— 用户确认完最想知道的两件事
    if first:
        blocks.append(_block("h_next", "heading2", "第一步，以及之后"))
        nxt = f"学完接着是「{second.get('unit_title') or second.get('name')}」。" if second else "学完这一节就到终点了。"
        blocks.append(
            _block(
                "b_first",
                "callout",
                f"第一步：**{first.get('unit_title') or first.get('name')}**"
                f"（{int(first.get('point_count') or 0)} 个知识点，约 {_hours(int(first.get('estimated_minutes') or 0))}）。{nxt}",
                props={"tone": "warm", "icon": "一"},
                meta=_row_meta(first),
            )
        )

    # ⑤ 怎么改 —— 计划被改是常态，告诉他在哪改比说"可随时调整"有用
    blocks.append(_block("h_edit", "heading2", "这份计划怎么改"))
    blocks.append(_block("e1", "bulleted", "不想学某一节：在地图上点那个穴位 → 「暂缓」，路线里保留位置，之后再捡起来。"))
    blocks.append(_block("e2", "bulleted", "想换顺序：回「定取舍」的导图里拖，前修关系不允许的顺序会被拦下并说明原因。"))
    blocks.append(_block("e3", "bulleted", "想加内容：在导图底部输入，加进来的条目会排在当前这一章末尾。"))
    return {"blocks": blocks}


def build_task_cards(
    *,
    title: str,
    nodes: Sequence[dict[str, Any]],
    stages: Sequence[dict[str, Any]],
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """**学习任务卡**：回答"今天具体做什么"。

    应含内容：每天一张卡（日期序号 + 本节目标 + 今天要过的知识点 + 收尾自测）。
    展现形式：可勾选清单（todo），一天一卡，卡头写明"第 N 天 / 哪个节"。
    创作原理：执行阶段的阻力不是"不知道学什么"，而是"开始不了"。把 30 分钟的
    具体动作写成 3–6 条可勾项，启动成本从"规划"降到"打勾"。
    适用价值：有明确期限的备考、每天固定时长的日常推进、需要打卡留痕的场景。
    """

    info = extras or {}
    daily = int((info.get("budget") or {}).get("daily_minutes") or 90)
    daily = max(20, min(daily, 240))
    by_unit: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        by_unit.setdefault(str(node.get("unit_id") or node.get("domain_id") or ""), []).append(node)

    blocks: list[dict[str, Any]] = [_block("h_root", "heading1", _topic(title, 60))]
    totals = _totals_of(stages, nodes)
    blocks.append(
        _block(
            "b_lead",
            "callout",
            f"按每天 {daily} 分钟切好，共 {max(1, -(-totals['minutes'] // daily))} 张卡、{totals['points']} 个知识点。"
            "做完一卡勾一卡；勾不完就顺延，不必补昨天的。",
            props={"tone": "key", "icon": "卡"},
        )
    )

    day = 0
    filled = 0
    for row in stages:
        unit_title = str(row.get("unit_title") or row.get("name") or "")
        chapter = str(row.get("chapter_title") or "")
        points = by_unit.get(str(row.get("stage_id") or row.get("domain_id") or ""), [])
        if not points:
            continue
        index = 0
        while index < len(points):
            day += 1
            take = max(1, min(len(points) - index, max(1, daily // 30)))
            chunk = points[index : index + take]
            index += take
            filled += 1
            head = f"第 {day} 天 · {unit_title}"
            if chapter and chapter != unit_title:
                head = f"第 {day} 天 · {unit_title}（{chapter}）"
            blocks.append(_block(f"h_day_{day}", "heading3", head))
            for point in chunk:
                name = str(point.get("name") or point.get("node_id") or "")
                hint = str(point.get("resource_hint") or "")
                blocks.append(
                    _block(
                        f"d{day}_p{abs(hash(str(point.get('node_id')))) % 10**8}",
                        "todo",
                        f"{name}　{hint}".strip(),
                        props={"checked": False},
                        meta={"node_id": str(point.get("node_id") or "")},
                    )
                )
            blocks.append(
                _block(
                    f"d{day}_check",
                    "todo",
                    "收尾自测：合上书复述今天的要点，错一处记一笔",
                    props={"checked": False},
                )
            )
    if not filled:
        blocks.append(_block("b_empty", "callout", "这份计划还没有展开到逐点，先确认一份。", props={"tone": "info", "icon": "注"}))
    return {"blocks": blocks}


def build_syllabus_map(
    *,
    title: str,
    nodes: Sequence[dict[str, Any]],
    stages: Sequence[dict[str, Any]],
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """**考纲对照表**：回答"每一节在教材哪一章、有多少考点"。

    应含内容：章/节/知识点数/预计时长/考点热度/目标掌握度 六列，逐节一行。
    展现形式：表格（可打印、可对着教材勾）。
    创作原理：可核对的表比可阅读的段落更容易被信任——每一行都能拿教材翻到，
    用户核对过一行，就会相信整份计划。
    适用价值：带教老师审计划、跨教材版本对照、备考时按考点密度二次取舍。
    """

    info = extras or {}
    totals = _totals_of(stages, nodes)
    per_unit: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        per_unit.setdefault(str(node.get("unit_id") or node.get("domain_id") or ""), []).append(node)

    blocks: list[dict[str, Any]] = [_block("h_root", "heading1", _topic(title, 60))]
    source = str(info.get("textbook") or "")
    blocks.append(
        _block(
            "b_lead",
            "paragraph",
            f"{totals['units']} 节 / {totals['chapters']} 章 / {totals['points']} 个知识点，"
            f"预计 {_hours(totals['minutes'])}。"
            + (f"层级依据：{source}。" if source else "层级按教材章节展开。"),
        )
    )
    rows: list[list[str]] = []
    for row in stages:
        stage_id = str(row.get("stage_id") or row.get("domain_id") or "")
        points = per_unit.get(stage_id, [])
        if not points:
            continue
        hot = sum(1 for item in points if str(item.get("highlight")) == "exam_hot")
        target = max((float(item.get("target_mastery") or 0) for item in points), default=0.0)
        rows.append(
            [
                str(row.get("chapter_title") or "—"),
                str(row.get("unit_title") or row.get("name") or ""),
                str(len(points)),
                _hours(int(row.get("estimated_minutes") or 0)),
                f"{hot}/{len(points)}" if hot else "—",
                f"{target:.2f}",
            ]
        )
    if rows:
        blocks.append(
            _block(
                "t_all",
                "table",
                "",
                props={"header": True, "columns": ["章", "节", "知识点", "预计", "高频点", "目标掌握度"], "rows": rows},
            )
        )
    blocks.append(
        _block(
            "b_tip",
            "callout",
            "这张表可以对着教材逐行核对：节名与教材目录一致、知识点数对得上，就说明这份路线的粒度没跑偏。",
            props={"tone": "info", "icon": "核"},
        )
    )
    return {"blocks": blocks}


_DOC_BUILDERS = {
    "route_guide": build_route_guide,
    "task_cards": build_task_cards,
    "syllabus_map": build_syllabus_map,
}


def build_doc(
    *,
    title: str,
    nodes: Iterable[dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
    stages: Iterable[dict[str, Any]] = (),
    archetype: str = DEFAULT_DOC_ARCHETYPE,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """文稿入口。``archetype`` 不认识时退回说明书，不抛。"""

    node_list = [node for node in nodes if isinstance(node, dict)]
    stage_list = [row for row in stages if isinstance(row, dict)]
    builder = _DOC_BUILDERS.get(archetype or "", build_route_guide)
    return builder(title=title, nodes=node_list, stages=stage_list, extras=extras)


def present_plan(
    user_id: str,
    *,
    nodes: list[dict[str, Any]],
    title: str,
    stages: list[dict[str, Any]] | None = None,
    prefer: str = "auto",
    structure: str = "",
    theme: str = "",
    version: int = 0,
    doc_archetype: str = "",
    extras: dict[str, Any] | None = None,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """落一份只读快照并返回它的地址。同一版本同一体裁只产一份。"""

    profile = get_profile(user_id, db_path=db_path)
    kind, reason = choose_presentation(profile, nodes, stages=len(stages or []), prefer=prefer)
    catalog = _catalog(db_path)
    archetype = ""
    if kind == "doc":
        budget = (extras or {}).get("budget") or {}
        archetype, archetype_reason = choose_doc_archetype(
            nodes,
            preferred=doc_archetype,
            total_days=int(budget.get("total_days") or 0),
        )
        reason = archetype_reason
    suffix = f"_{archetype}" if archetype else ""
    resource_id = (
        f"plan_{version}_{kind}{suffix}"
        if version
        else f"plan_{abs(hash((user_id, kind, title, archetype))) % 10**10}_{kind}{suffix}"
    )

    existing = storage.get_resource(resource_id, user_id, db_path)
    if existing is not None:
        return {
            "ok": True,
            "kind": kind,
            "archetype": archetype,
            "archetype_label": DOC_ARCHETYPE_LABELS.get(archetype, ""),
            "resource_id": resource_id,
            "open_url": open_url_for(kind, resource_id),
            "title": existing.get("title") or title,
            "reason": reason,
            "reused": True,
            "node_count": len(nodes),
        }

    content = (
        build_mindmap(
            title=title,
            nodes=nodes,
            catalog=catalog,
            stages=stages or [],
            structure=structure,
            theme=theme,
        )
        if kind == "mindmap"
        else build_doc(
            title=title,
            nodes=nodes,
            catalog=catalog,
            stages=stages or [],
            archetype=archetype,
            extras=extras,
        )
    )
    anchor = str((nodes[0] if nodes else {}).get("domain_id") or "")
    if not anchor or not storage.node_exists(anchor, db_path):
        # ``user_resource.node_id`` 是外键：锚点不存在时硬写会 IntegrityError。
        # 自由主题节点是唯一保证存在的落点。
        anchor = "general"
    storage.create_resource(
        {
            "id": resource_id,
            "user_id": user_id,
            "node_id": anchor,
            "type": kind,
            "title": _topic(title, 60),
            "content": content,
            "sources": [],
            "notes": reason,
            "status": "ready",
            "created_at": storage.now(),
            "meta": {"plan_present": True, "plan_version": version, "reason": reason, "archetype": archetype},
        },
        db_path=db_path,
    )
    return {
        "ok": True,
        "kind": kind,
        "archetype": archetype,
        "archetype_label": DOC_ARCHETYPE_LABELS.get(archetype, ""),
        "resource_id": resource_id,
        "open_url": open_url_for(kind, resource_id),
        "title": _topic(title, 60),
        "reason": reason,
        "reused": False,
        "node_count": len(nodes),
    }


def open_url_for(kind: str, resource_id: str) -> str:
    """地址从 :data:`ARTIFACT_SPEC` 派生。

    以前这里是 ``if kind == "mindmap": ... else: ...``，等于把同一张映射表抄了
    第二份，只有测试盯着 mindmap 那一半。现在两种体裁都在声明里，改地址只改一处。
    """

    return ARTIFACT_SPEC.template_for(kind).format(resource_id=resource_id)


__all__ = [
    "ARTIFACT_SPEC",
    "DEFAULT_DOC_ARCHETYPE",
    "DOC_ARCHETYPES",
    "DOC_ARCHETYPE_LABELS",
    "DOC_ARCHETYPE_SPECS",
    "MINDMAP_NODE_LIMIT",
    "MINDMAP_STAGE_LIMIT",
    "MINDMAP_SPEC",
    "build_doc",
    "build_mindmap",
    "build_route_guide",
    "build_syllabus_map",
    "build_task_cards",
    "choose_doc_archetype",
    "choose_presentation",
    "open_url_for",
    "present_plan",
]
