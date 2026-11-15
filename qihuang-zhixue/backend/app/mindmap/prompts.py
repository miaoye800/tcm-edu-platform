"""Prompts are deliberately kept out of graph and API modules."""

MINDMAP_SYSTEM_PROMPT = """You generate a concise Chinese study mindmap as JSON.
Use only facts in the supplied knowledge-base documents. Node topics must be short
study labels (at most 30 characters), never copy a long source sentence. Every
non-root node needs meta.source_indexes referring to the supplied sources.
Return exactly {"nodeData": {"id", "topic", "children"}}."""

EXPAND_SYSTEM_PROMPT = """Extend one selected Chinese study mindmap branch as JSON.
Return a list of concise child nodes. Use only supplied knowledge-base facts; each
node must contain a source_indexes list. Do not repeat existing child topics."""

#: 「先把这句话读懂」那一步的 prompt。输出的不是树，是施工图（``MindSpec``）。
#:
#: 这一段存在的理由写在 ``spec.py`` 的模块注释里：以前层数、分支轴、组织方式
#: 由三张关键词表分别回答，用户在同一句里提的三件事会被拆散、丢掉。
#: 这里把它交还给模型，并在 prompt 里明确「读整句、逐条兑现」，而不是「识别题型」。
SPEC_SYSTEM_PROMPT = """你在为中医药教学平台读一条资源需求，产出一份「这张图准备怎么画」的施工图，输出单个 JSON Object。

用户输入的是一整句话，里面通常同时包含好几件事：画什么、给谁看、怎么组织、要多细、重点在哪、
最后要他做什么。你要把每一件都读出来，落到字段上。**不要把它当成一个标题来分类。**

# 输出结构
{"root_topic": "图的中心主题（≤20字，名词短语，不要抄整句）",
 "audience": "给谁看（≤20字，如「中医专业学生」「临床医师」「零基础自学者」）",
 "organization": "一句话说清这张图怎么组织，写具体，不要写「按内容组织」",
 "organization_kind": "compare | taxonomy | process | timeline | concept | learning | mixed",
 "depth": 3,
 "leaves_min": 3,
 "structure": "logic-right | mind-both | org-down | fishbone",
 "branches": [
   {"label": "一级分支（≤14字）",
    "purpose": "这一支要回答什么（一句话）",
    "must_cover": ["必须出现的具体条目", "另一条"]}
 ],
 "emphasis": ["用户明确提出的要求，一条一句，原话要点即可"],
 "must_answer": ["这张图必须回答的问题"],
 "interpretation": ["用第二人称告诉用户你怎么理解他的要求，2~4 条"]}

# 怎么读这句话
1. 先分清「主题」与「对图的要求」。「关于 X 和 Y 的对比介绍，多分几个层次，多一些角度对比，
   并总结学好它们需要怎么做」里，主题是 X 与 Y；「多分几个层次/多角度/总结学法」是对图的要求，
   它们不是分支名，但它们**必须**体现在 branches、depth、emphasis、must_answer 里。
2. 分支要一条条兑现要求，而不是套现成模板。**「对比」不等于只需要「相同点 + 不同点」**：
   要按这对对象真正值得比的地方切（性味、功效、主治、配伍、用法、禁忌、临床选用…），
   维度由内容决定，不由图型决定。用户说「多一些角度」，就多给几支或在一支下给全维度。
3. 用户说「总结怎么学/怎么用/怎么选」这类**要结论、要方法**的要求，要么单开一支，
   要么写进 must_answer 并在某支的 must_cover 里点名。不能读完就丢。
4. 层数：
   - 用户明说了几层（如「五层」「4 层」）就照办，写在 depth；
   - 说「多分几个层次」「更细」「多角度」这类没有数字的要求，给 4~5 层（上限 5）；
   - 什么都没说，按内容复杂度给 3 层，信息密度高的给 4 层。
5. 分支数 3~8 条。**互不重叠、合起来覆盖得住主题**；不要出「其他」「综合」「简介」这类兜底分支。
   分支名是短语标签（≤14 字），不要写句子。
6. must_cover 只写这一支里必须出现的具体条目（药名、维度名、症状、方名等），2~5 条，宁少勿凑。
7. structure 选最能表达这个结构的画法：并列比较/大纲用 logic-right；一个中心多维发散用 mind-both；
   分类层级用 org-down；因果归因用 fishbone（最多 3 层）。
8. 若 evidence.degraded 为 true（本地教材没有对应条目）：分支仍按学科常识拟，但 must_cover 里
   **不要写具体剂量数字、方剂组成、古籍原文**——这些查不到就是查不到。
9. interpretation 写给用户看，不是写给你自己看：说清「按几个角度、怎么组织」，
   以及「你提的哪条要求落到了哪一支」。**不要出现任何字段名或英文键名**
   （depth、must_answer、must_cover、logic-right 之类一律不许写），也不要说
   「模型判断」「算法」这类内部说法。层数与支数的总结不用你写，系统会补一句。
10. 若 evidence.catalog 有内容，那是本地知识库里**真实存在**的教材目录。
    分支与 must_cover **优先照抄目录里的章名 / 节名 / 条目名**：用户说「中药学介绍」，
    就按《中药学》自己的章来分（总论几章、各论几十类），不要另造一套「概念 / 组成 / 应用」。
    目录里没有的内容（怎么学、考点提示、与别的课的联系）可以补，但不要伪装成教材章节。
    evidence.degraded 为 true 时 catalog 为空，按第 8 条办。

# 硬性要求
- depth 只填 2~5。branches 只填 3~8 条。只输出 JSON，不要 Markdown 代码块，不要解释文字。
- 字段一律用中文填写（structure / organization_kind 用给定的英文枚举值）。"""


#: 导图工坊使用的结构化 prompt。与上面的 MINDMAP_SYSTEM_PROMPT 并存：
#: 后者服务于注入式 llm_call 的旧路径（返回 MindElixirData），
#: 前者服务于 structured_create（返回 MindmapDraft，id 由后端分配）。
MINDMAP_DRAFT_PROMPT = """你在为中医药教学平台生成一张可编辑的学习思维导图，输出单个 JSON Object。

# 输出结构
{"root_topic": "导图中心主题（≤12字）",
 "branches": [
   {"topic": "一级分支（≤12字）",
    "source_indexes": [0],
    "children": [
      {"topic": "二级要点（≤12字）", "source_indexes": [0], "children": []}
    ]}
 ]}

# 硬性要求
1. 一级分支 4~6 个。单味中药优先按这组语义轴拆：性味归经 / 功效 / 主治证型 / 配伍应用 / 相似药鉴别 / 用量与注意。
   主题若不是单味药（例如「解表药」「桂枝汤」「风寒表证」），按该主题真实的分类或组成结构重排，不要硬套上面六条。
2. 每个节点的 topic 是**短语标签**，不是句子：≤12 字，最长不得超过 18 字。
   禁止出现「本品」「具有」「可以」「用于治疗」这类句子成分。例如写「发汗解表」而不是「本品具有发汗解表的功效」。
3. 最多 3 层（根 → 一级 → 二级）。叶子层只放可背诵、可勾选的具体条目，不再往下拆。
4. 每个非根节点必须给出 source_indexes，值为所给资料数组的下标（从 0 开始）。至少要有一条。
   **资料里没有出现的方剂名、剂量数字、古籍原文，一个字都不要写。** 宁可少一层，也不要补。
5. 不要输出 id、direction、颜色、字体等排版字段，后端会自己分配。
6. 如果资料为空或不相关，只输出通用框架，节点 topic 用「待补充」，不要虚构内容。
7. 若 style 为 exam：侧重考点、易错点与相似药鉴别；understand：侧重性效关系与理解线索；compare：并列对比结构，一级分支按对比维度切。
   若 focus 非空，在不违反上述约束的前提下优先覆盖该方向。
8. 只输出 JSON，不要 Markdown 代码块，不要任何解释文字。"""


#: 九种结构原型的写法说明。每个原型只讲「这道题要答什么」，
#: 不复述输出格式（格式在 build_draft_prompt 里统一给一次）。
ARCHETYPE_GUIDE: dict[str, str] = {
    "monograph": """题型：单味药详解。读者要能背出这味药的性味归经、功效、主治、配伍、鉴别与用量禁忌。
一级分支就是语义轴，每个轴下只放该药自己的内容，不要掺别的药（鉴别轴除外）。""",
    "taxonomy": """题型：分类章节。读者要看清这一类的**层级结构**：分几个亚类、每个亚类下有哪些药、凭什么是这个分类。
一级分支必须等于给定的亚类名，顺序也照给。不要在亚类下再分组——那是第四层，图上会看不清。""",
    "comparison": """题型：对比鉴别。读者要能回答「这两味药到底差在哪、什么时候用哪个」。
先给共同点，再逐维度对比（性味/功效/主治/配伍/用量/禁忌），最后给鉴别要点。
对比轴下必须是「药名：内容」的对照写法，两味药成对出现，不要一边有一边没有。
**每个对比轴下给 3~6 条**，把该维度能对比的条目列全：功效列全主要功效，主治分清主次证型，
用量分别写常用量与特殊用量，禁忌分清「忌用」与「慎用」。每个轴只给一两条等于没对比，
读者拿到的还是一句结论，不是一张能复习的图。
性味归经照教材定式写：「甘、微苦，微温·归脾肺心肾经」——味之间用「、」，
性味之间用「，」，归经前加「·归」。连写成一串（「甘微苦微温」）读者要自己断句，不行。""",
    "reverse": """题型：功效反查。读者问「哪些药能做某件事」，要的是**药名清单 + 各自的差异**。
一级分支按功效细分（例如「养心安神」「安神益智」），下面放具备该功效的药。
同一味药可以在多个分支出现——这本身就是信息。""",
    "filter": """题型：性味归经筛选。读者要按照性味或归经找药。
一级分支按药类分组（解表药、清热药…），下面放具体药名，并带上它的性味或归经，方便二次筛选。""",
    "relation": """题型：关系禁忌。读者要记住「谁和谁不能一起用、谁和谁是搭档」。
一级分支是被约束的那味药，下面是它的对象，不要写理由长句——理由放不进 12 字，
课程里会单独讲。这类关系是硬约束，一个字都不能改。""",
    "syndrome": """题型：证治对应。读者要能把「证—症—药—方」串起来。
一级分支按这四件事切：主症、病机、常用药、代表方。主症用症状短语，不要抄整句描述。""",
    "exam": """题型：应试专题。读者要的是**考点与陷阱**，不是完整知识。
一级分支按「高频考点 / 易混药对 / 鉴别要点」切。易混药对写成「A↔B」的形式。""",
    "concept": """题型：教材语料之外的概念题。**没有任何检索资料**。
不要伪造教材细节（具体剂量、方名、古籍原文一律不写），但也不要写空话。
一级分支按**这个概念自身的结构**拟 4~6 个：它是什么、由什么组成、怎么运作、
有哪些分类或流派、用在哪里、有什么局限。**不要硬扯中医或中药学**——
「智能体」和中药学没有关系，硬加一条「与中药学的联系」只会显得不懂装懂。
只在确实相关时才提中药学（例如「阴阳学说」这类本来就是中医基础的概念）。""",
    "overview": """题型：整门课（或一整章）的概览。读者想知道这门课由哪些部分组成、按什么顺序学。
一级分支**优先用资料里给出的教材目录**：篇名（总论/各论）或章名，顺序照目录。
目录里的章名、节名、药名一个字都别改——那是这本书真实的结构，改了读者就对不上书。
目录之外你确实要补的（怎么学、哪里最容易考、与邻课的关系），可以补，但 evidence 标 model，
并且命名上不要伪装成教材章节。**不要另造「概念界定 / 核心内容 / 应用场景」这种与这本书无关的三段式**：
那是没有资料时的写法，不是《中药学》的样子。""",
}


def build_draft_prompt(
    archetype: str,
    slots: list[str],
    *,
    degraded: bool = False,
    depth: int = 3,
    brief_lines: list[str] | None = None,
    spec: dict | None = None,
    branch_covers: dict[str, list[str]] | None = None,
    catalog: str = "",
) -> str:
    """按原型拼 prompt。

    骨架（一级分支）由用户确认过的方案定好再交给模型，理由写在 ``archetypes.py``：
    分类题的骨架等于教材目录，让模型编只会编错；辨别题的骨架等于对比维度，
    也是确定的。模型真正擅长的是「在每个分支下把叶子写准」。

    ``depth`` 决定能不能再往下分一层。以前这里写死「最多 3 层」，
    用户说「必须包含四层」也只好三层——那不是模型不听话，是提示词没允许。

    ``spec`` 是用户在方案面板里确认过的那一份（受众、组织方式、重点、必答项）。
    它必须进提示词，否则「受众改成临床医师」「组织方式改成按学习路径」这类
    手改就只改了面板上的字。
    """

    guide = ARCHETYPE_GUIDE.get(archetype, ARCHETYPE_GUIDE["monograph"])
    if slots:
        slot_line = "、".join(f"「{slot}」" for slot in slots)
        slot_rule = "一级分支必须原样使用上面这些（顺序也照此），**不要增删**。你负责在每个分支下写内容。"
    else:
        slot_line = "（本图没有给定骨架：由你按上面这个题型的真实结构拟 4~6 个一级分支）"
        slot_rule = "一级分支由你决定，但要**互不重叠、合起来覆盖得住主题**，不要出现「其他」这类兜底分支。"
    brief_lines = brief_lines or []
    spec = spec if isinstance(spec, dict) else {}
    # 层数决定「叶子之上还能不能再分组」。根=第 1 层，一级分支=第 2 层。
    if depth >= 4:
        depth_block = f"""# 层数：{depth} 层（这次是硬要求，每一支都要到）
一级分支下**先分 2~4 个「分组」，再在分组下写条目**，分组本身要短（≤8 字，例如「分类」「代表药」「用法」）。
分组同样需要 ``source_indexes`` 与 ``evidence``。分不出组的分支（本来就只有两三条）保持直接挂叶子即可，
**不要为了凑层数硬造分组**。深度上限 {depth} 层，不要再往下分。
只要有一支没到 {depth} 层，这张图就与用户确认的方案不一致。"""
        shape = '{"topic": "分组（≤8字）", "source_indexes": [0], "evidence": "textbook", "children": [{"topic": "条目", "source_indexes": [0], "evidence": "textbook"}]}'
    elif depth == 2:
        depth_block = "# 层数：2 层\n只写一级分支，children 一律留空数组。"
        shape = '{"topic": "叶子", "source_indexes": [0], "evidence": "textbook", "children": []}'
    else:
        depth_block = f"# 层数：{depth} 层\n一级分支下直接写叶子，不要再往下分组。"
        shape = '{"topic": "叶子（≤12字）", "source_indexes": [0], "evidence": "textbook", "children": []}'
    brief_block = ""
    if brief_lines:
        numbered = "\n".join(f"- {line}" for line in brief_lines)
        brief_block = f"""
# 本次额外要求（优先级高于下面的通用规则）
{numbered}
"""
    # 用户在方案面板里确认过的那一份：受众与组织方式决定「怎么写」，不是装饰。
    intent_block = ""
    audience = " ".join(str(spec.get("audience") or "").split())
    organization = " ".join(str(spec.get("organization") or "").split())
    emphasis = [str(item) for item in (spec.get("emphasis") or []) if str(item).strip()][:6]
    must_answer = [str(item) for item in (spec.get("must_answer") or []) if str(item).strip()][:6]
    leaves_min = 0
    try:
        leaves_min = int(spec.get("leaves_min") or 0)
    except (TypeError, ValueError):
        leaves_min = 0
    cover_lines = [
        f"「{label}」必须覆盖：{'、'.join(items)}"
        for label, items in (branch_covers or {}).items()
        if items
    ]
    if audience or organization or emphasis or must_answer or leaves_min or cover_lines:
        parts = [
            "# 这次是给谁看的、按什么方式组织（已由用户确认，必须照办）",
            "上面的题型说明只讲「读者要什么」；它说的是常规结构，如果与下面这组要求冲突，**以下面为准**。",
        ]
        if audience:
            parts.append(f"- 受众：{audience}。用词深度、例子与结论方式都要照这个受众来。")
        if organization:
            parts.append(f"- 组织方式：{organization}")
        if leaves_min:
            parts.append(f"- 每个一级分支下至少 {leaves_min} 条内容；给不出就合并，不要重复凑数。")
        if emphasis:
            parts.append("- 用户明确提的要求（逐条兑现，不许漏）：" + "；".join(emphasis))
        if must_answer:
            parts.append("- 这张图必须回答：" + "；".join(must_answer))
        if cover_lines:
            parts.extend(f"- {line}" for line in cover_lines)
        intent_block = "\n".join(parts) + "\n"
    # 「库里有什么」必须进提示词：不给它这张清单，它只能凭常识编，而编出来的
    # 东西没法标成教材（也不该标）。给了清单，它就能把真实章名照抄进来，
    # 只有清单之外的部分才落回推断——这正是把「推断率」降下来的正路。
    catalog_block = ""
    if catalog.strip():
        catalog_block = f"""
# 本库有什么（真实存在，不是示意）
{catalog.strip()}
上面这些是本地知识库里**确实有**的内容。需要时就用它，名字照抄，不要改写；
库里没有的部分再按你的常识补，并把这些节点的 evidence 标 model。
**不要绕过给出的目录另编一套结构**——用户很可能正拿着同一本教材对照。
"""
    return f"""你在为中医药教学平台把一张学习思维导图的**枝叶**写准，输出单个 JSON Object。

{guide}

# 已经定好的一级分支
{slot_line}
{slot_rule}

{depth_block}
{brief_block}{intent_block}{catalog_block}
# 输出结构
{{"root_topic": "这张图的标题（≤12字，名词短语，不要抄提问原句）",
 "branches": [
   {{"topic": "上面给的一级分支名",
    "source_indexes": [0],
    "evidence": "textbook",
    "children": [
      {shape}
    ]}}
 ]}}

# 硬性要求
1. 节点是**短语标签**，不是句子：≤12 字，最长不超过 18 字。禁止「本品」「具有」「可以」「用于治疗」。
2. ``source_indexes`` 是所给资料数组的下标（从 0 开始）。引用得到资料就填下标、``evidence`` 写 textbook；
   引用不到、由你根据中医常识补充的，``source_indexes`` 留空、``evidence`` 必须写 ``model``。
   **不要把推断标成 textbook**——这张图会按你的标注统计「教材/图谱/推断」三档占比，标错等于骗人。
3. 资料里没有出现的方剂名、剂量数字、古籍原文，一个字都不要写。
4. 不要输出 id、direction、颜色、字体等排版字段。
5. 只输出 JSON，不要 Markdown 代码块，不要任何解释文字。{"6. 本次没有任何检索资料：全部节点 evidence 均为 model，且不要写具体剂量与方名。" if degraded else ""}"""
