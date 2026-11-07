"""Prompts are deliberately kept out of graph and API modules."""

MINDMAP_SYSTEM_PROMPT = """You generate a concise Chinese study mindmap as JSON.
Use only facts in the supplied knowledge-base documents. Node topics must be short
study labels (at most 30 characters), never copy a long source sentence. Every
non-root node needs meta.source_indexes referring to the supplied sources.
Return exactly {"nodeData": {"id", "topic", "children"}}."""

EXPAND_SYSTEM_PROMPT = """Extend one selected Chinese study mindmap branch as JSON.
Return a list of concise child nodes. Use only supplied knowledge-base facts; each
node must contain a source_indexes list. Do not repeat existing child topics."""

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
}


def build_draft_prompt(
    archetype: str,
    slots: list[str],
    *,
    degraded: bool = False,
    depth: int = 3,
    brief_lines: list[str] | None = None,
) -> str:
    """按原型拼 prompt。

    骨架（一级分支）由后端定好再交给模型，理由写在 ``archetypes.py``：
    分类题的骨架等于教材目录，让模型编只会编错；辨别题的骨架等于对比维度，
    也是确定的。模型真正擅长的是「在每个分支下把叶子写准」。

    ``depth`` 决定能不能再往下分一层。以前这里写死「最多 3 层」，
    用户说「必须包含四层」也只好三层——那不是模型不听话，是提示词没允许。
    """

    guide = ARCHETYPE_GUIDE.get(archetype, ARCHETYPE_GUIDE["monograph"])
    if slots:
        slot_line = "、".join(f"「{slot}」" for slot in slots)
        slot_rule = "一级分支必须原样使用上面这些（顺序也照此），**不要增删**。你负责在每个分支下写内容。"
    else:
        slot_line = "（本图没有给定骨架：由你按上面这个题型的真实结构拟 4~6 个一级分支）"
        slot_rule = "一级分支由你决定，但要**互不重叠、合起来覆盖得住主题**，不要出现「其他」这类兜底分支。"
    brief_lines = brief_lines or []
    # 层数决定「叶子之上还能不能再分组」。根=第 1 层，一级分支=第 2 层。
    if depth >= 4:
        depth_block = f"""# 层数：{depth} 层（这次是硬要求）
一级分支下**先分 2~4 个「分组」，再在分组下写条目**，分组本身要短（≤8 字，例如「分类」「代表药」「用法」）。
分组同样需要 ``source_indexes`` 与 ``evidence``。分不出组的分支（本来就只有两三条）保持直接挂叶子即可，
**不要为了凑层数硬造分组**。深度上限 {depth} 层，不要再往下分。"""
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
    return f"""你在为中医药教学平台把一张学习思维导图的**枝叶**写准，输出单个 JSON Object。

{guide}

# 已经定好的一级分支
{slot_line}
{slot_rule}

{depth_block}
{brief_block}
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
