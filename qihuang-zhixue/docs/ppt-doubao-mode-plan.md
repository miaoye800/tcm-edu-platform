# 「豆包式 PPT 生成」方案 v3

> 范围：幻灯生成的内容智能 + 渲染/导出双后端。**不动**对话 Agent、mindmap、其他资源类型。
> 目标：从「一次生成一份及格稿」升级为「分阶段可干预、逐页生成、单页可改、导出后端可插拔」。
> 前置文档：`~/.workbuddy/plans/stellar-thunder-darwin-VgbEmwom.md`（v2 质量升级方案）。本方案**取代其渲染层决策**，保留其质量网与设计空间结论。

---

## 零、一页结论

1. **豆包的智能不在渲染，在流程形态**：分阶段（材料 → 需求确认 → 大纲 → 逐页 → 可改），每一阶段停下来让人能干预。我们现在是「一次调用出整份」，把不确定性全压在一次生成里。
2. **必须做管线 v2，而不是改 v1**。`ALCHEMY_STEPS_V2` 已有 mindmap 九步先例，`steps_for()` 已支持按 `meta.pipeline_version` 分流。PPT 加一张七步表，老任务继续走四步，互不干扰。
3. **推翻 v2 方案的一个决策：Frame 单源改到后端（Python），不再是前端。** 理由：要接 ppt-master 的 `svg_to_pptx.py`（Python），Frame 必须能被 Python 消费；若仍由前端生成，就回到「跨语言两套排版数字」——正是上一版卡死的主因。改为「后端产出 cqw 坐标，前端只负责把它们塞进样式」。
4. **接 ppt-master 只接它的转换器，不接它的生成方式**。它让模型逐页写 SVG：视觉上限高，但不可回归、改字即改坐标、10 页 10-20 分钟。我们反过来——**SVG 由确定性模板函数生成，再用它的转换器落地**，拿到 DrawingML 保真度，不承担不可回归的代价。
5. **导出后端三档可插拔**：`native`（现状 pptxgenjs）/ `svg-design`（默认，走 ppt-master 转换器）/ `svg-design-llm`（高级模式，模型写 SVG，默认关闭）。三者共用同一份 Frame，切换不改内容。
6. **投入 79 人天（不含缓冲）/ 87–95 人天（含缓冲）**，双人并行 10–13 周。第一期（M0-M3，49 人天）就能上线「听懂人话 + 七步流程 + 逐页生成 + 单页重写」，不需要等 ppt-master 接完。

---

## 一、豆包模式拆成五段三道门

| 段 | 做什么 | 我们的现状 | 差距 |
| --- | --- | --- | --- |
| 1 摄入 | 文件 / URL / 粘贴 / 主题 | 只有知识树选点 + 自由主题 | 缺材料摄入 |
| 2 定题 | 场景 × 受众 × 页数 × 深度，缺就填默认值，最多问一次 | 三个下拉（受众/页数/主题） | 缺「深度」与「叙事倾向」，且不进 prompt 的立骨 |
| 3 大纲 | route 级大纲，可增删改序 | 有（`mode=outline` 暂停等确认） | 大纲只有 title+points，没有 claim/purpose，撑不起逐页生成 |
| 4 逐页 | 每页独立生成 + 版式匹配 + 配图 | 整份一次生成 | **核心差距** |
| 5 改 | 单页重生成 / 自然语言改 / 换皮 | 只有手改字段 | 缺重生成与换皮 |

**三道门**（豆包靠这三处收敛不确定性，我们只做了第二道）：

- **G1 定题门**：参数齐了才生成，缺项用画像补，不追问。
- **G2 大纲门**：已有。保留，且升级为可编辑 claim + purpose。
- **G3 单页门**：每页生成完立刻做确定性校验，不合格**只重写这一页**。这是「不智能」最大的解药——整份生成时一页崩了只能整份重来。

---

## 二、现状诊断（代码依据）

| # | 事实 | 位置 | 后果 |
| --- | --- | --- | --- |
| 1 | `frame()` 注释明写「刻意不调模型」，`arc` 硬编码 `["性味归经","功效主治",…]` | `backend/app/ppt/agent.py:45-60` | 主题只要不是「讲一味药」就套不上；`audience` 只进了 prompt 一句话，不影响结构 |
| 2 | `SlideNode` 只有 `title/subtitle/bullets/columns(≤2 栏)/note` | `backend/app/ppt/schemas.py:34-44` | 内容承载上限 = 几条要点。「3 味药 × 4 维鉴别」无处安放，只能降维成 bullets |
| 3 | `columns` 上限 2 栏、每栏 5 条 | `draft.py:25-27` | 多对象对照从结构上就做不了 |
| 4 | 整份 2 次调用（outline + compose），单轮无回路 | `agent.py:148-153` | 一页崩、整份崩 |
| 5 | `_resolve_kind` 只看「几栏/几条/是否末页」 | `draft.py:202-218` | 版式不匹配内容，`if len(columns) >= 2: return "compare"` |
| 6 | `polish()` 只统计不改，`_deck_stats` 的 `grounded` 只数「有没有 index」 | `agent.py:156-164`、`service.py:303-313` | 没有质量门，也没有局部修复 |
| 7 | 六种 kind 全是文本容器 | `schemas.py:17` | 表格/流程/数值/警示四类信息被压成 bullets |

---

## 三、目标架构

```
材料/主题
   │
   ├─(1) brief   听懂 + 立骨：调模型 → Intent（13 值+置信+依据）＋ SelectionPlan（取材单）＋ DeckBrief（受众/深度/页预算/章节弧/组织器/密度）
   │
   ├─(2) outline 谋篇：调模型 → 每页 {title, claim, purpose, slot_refs}   ◀── G2 大纲门
   │
   ├─(3) compose 敷演：每页 1 次调用，并发 4 → blocks[]（内容单元）
   │        └─(4) verify 校验：确定性规则 + 仅不合格页定向重写 1 次  ◀── G3 单页门
   │
   ├─(5) layout  排版：纯函数，容量表 + 打分 → Frame（cqw 坐标，单源在后端）
   │
   ├─(6) render  成像：slide/frame 事件流式下发，前端逐页入舞台
   │
   └─(7) seal    封存：落库 + 质量报告 + 溯源角标
                 │
                 └── 导出后端（可插拔，共用同一份 Frame）
                     ├ native         pptxgenjs（前端，现状）
                     ├ svg-design     Frame → SVG（确定性） → ppt-master svg_to_pptx（默认）
                     └ svg-design-llm 模型写 SVG（高级模式，默认关闭）
```

**单一事实来源**：`Frame` 由 `backend/app/ppt/layout/` 生成，四个消费方共用——`FrameSurface.vue`（DOM 预览）、`svg_render.py`（SVG）、`pptxFrame.ts`（pptxgenjs）、`svg_to_pptx`（DrawingML）。**不允许任何消费方自己算坐标。**

---

## 四、数据契约

### 4.1 purpose 十值枚举

`cover / agenda / section / points / compare / matrix / process / data / warning / summary`

与 v2 草稿的差异：把 `bullets` 改名为 `points`（避免和字段重名），新增 `points` 之外的九种都对应一种信息关系。

### 4.2 Block：内容单元

```python
# backend/app/ppt/blocks.py（新建）
BlockType = Literal["point", "matrix", "steps", "figures", "alert", "quote", "def"]

class Block(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: BlockType
    # type=point
    text: str = Field(default="", max_length=60)
    # type=matrix：rows[0] 是表头，cols 是列名
    cols: list[str] = Field(default_factory=list, max_length=4)
    rows: list[list[str]] = Field(default_factory=list, max_length=5)
    # type=steps
    steps: list[dict[str, str]] = Field(default_factory=list, max_length=5)   # {"label","detail"}
    # type=figures
    figures: list[dict[str, str]] = Field(default_factory=list, max_length=3) # {"value","unit","label"}
    # type=alert
    severity: str = Field(default="", max_length=16)   # 十八反 / 十九畏 / 毒性
    # type=quote / def
    source: str = Field(default="", max_length=120)
    caption: str = Field(default="", max_length=60)
    # 来源档位：决定是否渲染「推断」角标（见 6.3）
    evidence: Literal["textbook", "material", "model"] = "textbook"
```

设计规则：

- **一个 Block 只表达一种信息关系**，不允许混合。混合就是没想清楚，退回 `point`。
- `matrix` 的 `rows[0]` 是表头。这一条让「行头列 + 数据列」的渲染无歧义。
- 上限全部来自 16:9 的实测（`matrix` 3 列 × 4 行、`figures` 3 个数字、`steps` 5 步），与 v2 容量表一致。

### 4.3 SlideNode 增量（全部带默认值）

```python
class SlideNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # …既有字段不动…
    purpose: SlidePurpose = "points"           # 版式意图，后端按结构重算
    claim: str = Field(default="", max_length=24)   # 一句话判断句结论
    slots: list[str] = Field(default_factory=list, max_length=8)  # 该页覆盖了取材单的哪几项
    blocks: list[Block] = Field(default_factory=list, max_length=6)
    density: Literal["sparse", "normal", "dense"] | None = None
```

`SelectionPlan`（取材单）**不落库到 `DeckContent`**：它是生成期的中间产物，存在 `task.steps_done` 与 `meta` 里；落库会让「改取材单」和「改稿子」变成两个事实来源。前端拿它渲染勾选面板，改完只触发单页重生成。

**兼容性三条硬约束**（任一漏掉就是静默丢字段或 422）：

1. 全部默认值 → 老 `content_json` 能被 `extra="forbid"` 正常读入。
2. `frontend/src/composables/usePptStudio.ts:35` 的 `sanitizeSlide` 是**手写对象**，必须显式透传四个新字段，否则「保存成功但字段丢失」。
3. `frontend/src/types/ppt.ts` 的 `SlideNode` 同步；`SLIDE_LIMITS` 加 `claim: 24`。

加一条往返测试：`加载 v1 fixture → sanitizeSlide → save → 读回`，断言 `blocks` 与 `purpose` 不丢。

---

## 五、七道工序（管线 v2）

### 5.1 工序表

```python
# backend/app/resources/constants.py 追加
ALCHEMY_STEPS_V2["ppt"] = (
    {"id": "brief",   "label": "立骨", "variants": ("立起讲稿骨架...", "定下这一课讲给谁...", "先想清楚要证明什么...")},
    {"id": "outline", "label": "谋篇", "variants": ("谋篇定页序...", "勾出每页的主张...", "排布章节先后...")},
    {"id": "compose", "label": "敷演", "variants": ("逐页敷演...", "把主张写成一页...", "一页一件事...")},
    {"id": "verify",  "label": "校勘", "variants": ("校勘每页依据...", "复核数字与槽位...", "挑出不合格的那一页...")},
    {"id": "layout",  "label": "布版", "variants": ("按内容布版...", "为每页选定形制...", "让版式跟着内容走...")},
    {"id": "render",  "label": "成像", "variants": ("落墨成像...", "一页一页推上舞台...", "让稿子自己翻页...")},
    {"id": "seal",    "label": "封存", "variants": ("封存入库...", "记下这次的版本...", "收笔，交给你改...")},
)
```

`steps_for()` 改为：

```python
if resource_type in ALCHEMY_STEPS_V2 and pipeline_version >= 2:
    return ALCHEMY_STEPS_V2[resource_type]
```

新任务在 `meta` 写 `pipeline_version=2`；老任务读不到这个键，继续四步表 —— 与 mindmap 完全同构，**老四步表一个字不改**。

### 5.2 每步的输入/输出/耗时/失败处置

| 步 | 调用模型 | 输入 | 输出 | 预算 | 失败处置 |
| --- | --- | --- | --- | --- | --- |
| brief | 1 次 | topic + docs 摘要 + 画像 + 场景 | `DeckBrief` | 1.5s | 回落硬编码默认 brief（`templates.py` 已有能力） |
| outline | 1 次 | brief + docs + sources | `list[OutlineSlide]`（含 claim/purpose） | 4s | 回落 `deterministic_outline`；<3 页判为失败 |
| compose | N 次并发 4 | 单页 outline + 该页需要的 docs 切片 | `list[Block]` | 12 页 ≈ 12s | 单页超时 45s 或非法 → 该页落模板页，页位保留 |
| verify | 0 次（规则）+ ≤3 次（重写） | 全稿 + docs | `DeckQualityReport` | 1s | 重写仍不合格 → 保留原页，报告里标黄 |
| layout | 0 | slide + theme + density | `Frame` | 50ms | Frame 构建异常 → 该页降级 `points` 版式 |
| render | 0 | Frame | 逐页事件 | — | — |
| seal | 0 | 全稿 + 报告 | 落库 + `deck_verify` 事件 | 200ms | — |

**延迟账**：首屏（大纲可见）≈ 6s；整稿 ≈ 20-24s。现在整稿是 12-18s。多花 6s 换来「一页崩不拖累整份」，值得。若实测超 30s，把 `compose` 并发从 4 提到 6，不加模型调用次数。

**工序数与断言**：`test_ppt_api.py:92` 断言 `step_start == 4`，属于 v1 用例，**保持绿**。新增 `test_ppt_api_v2.py` 断言 7 次、断言大纲暂停后 resume 从 `compose` 起（不是从 `brief` 重跑）。

### 5.3 service.py 的改动点

`stream_task` 的分支目前按 `spec["id"]` 匹配（`service.py:518/546/555`）。v2 需要新增：

```python
elif rt == "ppt" and spec["id"] == "brief":     # 调模型出 brief，落 done["brief"]
elif rt == "ppt" and spec["id"] == "compose":   # v2 分支：逐页并发
elif rt == "ppt" and spec["id"] == "verify":    # 规则 + 定向重写
elif rt == "ppt" and spec["id"] == "layout":    # 纯函数出 frame
elif rt == "ppt" and spec["id"] == "render":    # 推 slide/frame 事件
elif rt == "ppt" and spec["id"] == "seal":      # 落库
```

**关键**：老 `compose` 分支（`service.py:546`）与 v2 的 `compose` 同名，必须靠 `_pipeline_version(task) >= 2` 分流，不能靠 id 判断。这是本次改动最容易出事的一处，先写测试再改。

事件增量（全部新增，不改老事件语义）：

- `brief`：`{brief: {...}}`
- `slide`：在既有 payload 上加 `frame` 字段（可为 null，前端读到就用，读不到走老渲染）
- `deck_verify`：`stats` 换成质量报告五项 + `split_pages` + `export_backend`

---

## 六、内容智能：听懂 → 取舍 → 标源

> 本节是 v3 相对 v2 的核心修正。v2 用 `required_slots` 强制覆盖九个字段，等于把「字段搬运」写成硬约束——每份稿都会长成「九字段朗读版」，而它的信息量不超过数据库，用户不如直接查。改为三段：模型听懂用户要什么 → 产出可编辑的取材单 → 每块内容标来源档位。

### 6.1 听懂：模型判，规则只守门

**为什么不用规则判。** 导图模块用规则判问法（`mindmap/intents.py`），前提是**封闭域 + 短问句**：输入来自知识树或 ≤120 字的一句话，问法只有八种，中文有稳定句式（「…的区别」「哪些药能…」）。PPT 面对的是自由表达——「给规培生讲讲古代人参为什么贵，带点现代市场对比」——规则一条都命中不了，只能落回 `monograph`，也就是退回「只认单味药」的老病。**域变了，工具就得换。**

**判定与立骨合并成一次模型调用**（不新增调用次数）：

```python
class Intent(BaseModel):
    kind: IntentKind                      # 13 值封闭枚举，禁止自创
    confidence: float = Field(ge=0, le=1)
    evidence: list[str]                    # 依据输入里的哪几个字
    organizers: list[str]                  # timeline/dimension/causal/priority/sequence
```

`IntentKind` = 导图九类（`monograph/taxonomy/comparison/reverse/filter/relation/syndrome/exam/concept`）+ 教学向四类（`evolution` 沿革 / `application` 应用 / `mechanism` 机理 / `popular` 科普）。

三条工程约束，缺一条判定就不可信：

1. **枚举封闭**：`kind` 只能取 13 值之一。模型自创分类等于没判。
2. **evidence 可校验**：`evidence` 里的词必须出现在用户原话中（子串匹配），否则本次判定作废、重试一次。它把「模型说它懂了」变成「模型能指出它凭什么懂」，而后者可以机器验。
3. **低置信度不猜**：`confidence < 0.55` 时不硬判，把 2~3 个候选顶到取材单顶部让用户点一下——在界面上承认歧义，好过自信地判错。

规则仍然在，但只管三件与「理解」无关的事：**入口守门**（空输入 / 纯标点 / 超 200 字走材料路径）、**缓存键**（规范化问句做 key，重试不重复付费）、**输出校验**（枚举合法 + evidence 命中 + 取材单非空）。不过就重试一次，再不过降级到规则判定，最后降级到 `monograph`。三层降级，每层都可用。

**评测集是这一层唯一的硬保证**：40 条真实说法 × 期望 `kind`（含 8 条「怪说法」），断言准确率 ≥ 88%，且判错样本必须落在低置信度区间。错了就针对那一类加提示词或 few-shot——**规则用来修具体的错，不做通用判断**。

### 6.2 取舍：取材单（SelectionPlan）

用户要的从来不是「这个实体的全部字段」，而是「这个任务需要的部分」。所以第二步产出**取材单**，逐项三态：

```python
class SelectionItem(BaseModel):
    item: str                                              # 字段名 / 库外知识点 / 材料片段
    decision: Literal["must", "nice", "skip"]
    reason: str = ""                                       # 一句话，给用户看
    evidence_level: Literal["textbook", "material", "model"]

class SelectionPlan(BaseModel):
    items: list[SelectionItem]
    organizers: list[str]                                  # 叙事组织器，至少一个
    gaps: list[str]                                        # 库里没有、需模型补的点
```

**这是整个方案最关键的一处：取舍结果必须外露、可勾选、可改。**

| 「人参的发展历史」 | 内容 |
| --- | --- |
| 保留 4 项 | 别名与基原 / 产地与栽培 / 本草记载 / 药用部位 |
| 删掉 5 项 | 功效主治 / 配伍应用 / 用法用量 / 使用禁忌 / 鉴别要点 |
| 补充 3 项 | 朝代沿革 / 栽培史转折 / 现代应用（标 `model`） |

- 删除**不是少写，是不出现**：历史题里写「用法用量 3~9g」不是内容不够，是答错了题。
- 取材单随大纲同屏下发，勾一下即可增删；改完只影响对应页，不重生成整份。**判错的代价接近一次点击**——这是「模型也会错意」的唯一正解：不追求一次判对，让判错不成为事故。
- **默认值也要显示**：用户只说「人参」时按场景给默认（课前提要 → 九项全留；评委汇报 → 只留功效/鉴别/用量），并把「它默认给我讲了九项」摆在界面上，而不是藏在 prompt 里。

### 6.3 标源：三档依据

复用导图的档位机制，PPT 用 `textbook | material | model`：

| 档位 | 含义 | 前端表现 |
| --- | --- | --- |
| `textbook` | 出自教材字段，可指到 `source_indexes` | 溯源角标 |
| `material` | 出自用户粘贴/上传的材料 | 「你的材料」角标 |
| `model` | 模型补充（沿革、类比、课外内容） | 「推断」角标，与导图同款 |

**不设占比上限。** 占比由主题性质决定：课外题（咖啡发展史）100% 模型是正常的，设 30% 上限反而逼它砍内容。要管的是**每句话的档位是否诚实**——管欺骗，不管比例。

**课外主题的中医桥接**（用户要的「和中医药结合」）：主体照实讲（全标 `model`），收束时接中医视角——与茶的对照（`textbook`）、「提神耗气」与现代作息的关系、性味归经的**类比**。红线一条：类比必须标「类比/推断」，不能以 `textbook` 口吻出现。把咖啡的「苦、温、归心经」写成教材结论，是造假不是创意。

### 6.4 逐页生成

- 每页一次调用，输入只有「该页的 outline 条目 + 命中该页槽位的 docs 切片 + 邻页标题（防重复）」。**不给整份大纲**：给了模型就会复读别页内容。
- 输出 `blocks[]`，不输出 kind（kind 由后端按 blocks 结构重算，沿用 `_resolve_kind` 的「不采信自报」原则）。
- 并发 4，`asyncio.Semaphore(4)`；单页 45s 超时；失败页用 `templates.py` 的确定性页填充，**页位保留**（页数恒定是前端进度条的隐含契约）。
- 幂等：每页结果按 `(task_id, slide_index)` 缓存进 `done["slides"][i]`，重连不重跑。

### 6.5 校勘（critic）：确定性判据，模型只做定向重写

确定性判定（`backend/app/ppt/quality.py`，纯函数，可单测）：

| 指标 | 算法 | 门限 |
| --- | --- | --- |
| `forbidden_hits` | 取材单标 `skip` 的字段/词面出现在正文 | **0**（负向硬拦） |
| `must_coverage` | 标 `must` 的项被该页覆盖的比例 | ≥ 80% |
| `evidence_honest` | 每块 block 是否带档位；`model` 块是否可渲染角标 | 100% |
| `number_hits` | 页面数字能在 `docs[].evidence_text` 搜到（`model` 块除外） | 100% |
| `cite_valid` | `source_indexes` 未越界 + 词面重叠 ≥ 1 词 | ≥ 70% |
| `claim_missing` | 无 claim 的内容页数 | 0 |
| `block_fill` | 每块内容占容量带比例（防空壳） | ≥ 0.4 |
| `purpose_entropy` | 版式分布熵 | 仅报告 |

**判据方向是这次最实质的改动。** 早前设的「必须新增 ≥2 个字段外的关系」是正向配额，可被注水——模型随手加两句「由此可见」「影响深远」就凑够了，判据形同虚设。改为**负向断言**（不该出现的不得出现）：无法作弊，而且它证明的是系统**敢于不写**。

**只对不合格页做重写**，把「哪一条不合格」原样喂回去（不喂全稿）。重写最多 1 次；仍不合格就保留原文并在报告里标黄 —— 静默改字比不改更伤信任（`agent.py:157` 的既有价值观）。

### 6.6 局部重写（用户触发）

新增 `POST /api/ppt/rewrite-slide`：

```
入参：{task_id | content, slide_index, instruction, mode: "rewrite" | "expand" | "condense" | "relayout"}
出参：{slide: SlideNode, diff: [{field, before, after}], quality: {...}}
```

三条约束：

1. **白名单校验**：`content` 变体必须整份过一遍 `DeckContent` 校验，不接受单页裸 JSON。
2. **必须回执 diff**：前端把「改了哪几个字」显示出来，不做静默替换。
3. `mode=relayout` 不调模型，只跑 `layout` 的 top-3 候选（零成本换排法）。

### 6.7 检索扩面

现在 `retrieve_resource_docs(topic)` 只认单味药结构。实测三种任务返回同一批素材（「人参的发展历史」/「人参皂苷 Rg1 的药理」/「《伤寒论》里人参的用法演变」→ 都是「人参、党参、黄芪」），说明**检索层就把任务抹掉了**。加一层「主题 → 文档集合」：

- 主题命中药材 → 现有路径（不变）。
- 主题命中方剂 → 拆成「组成药材」+ 方剂条目，`differentiation` 用作 matrix 的天然来源。
- 主题命中章节/病证 → 走 `effects` 倒排取 3-5 味药，organizer 改为 dimension。
- 主题在库内无对应实体（`intent=concept`）→ **不检索**，走模型知识 + `material` 档，并明示「无教材依据」。

**这一层不做，前面六节全部只能服务单味药主题。** 排期上必须和 M2 同批。

---

## 七、版式与渲染

### 7.1 容量表 + 确定性打分（纯函数）

| purpose | 容量 | 骨架 |
| --- | --- | --- |
| `matrix` | 2~3 列 × 3~4 行，单元格 ≤ 12 字 | 左 18cqw 行头列 + 右侧等分数据列 |
| `process` | 3~5 节点，说明 ≤ 14 字 | 横向等分 + 圆形序号 + 细横线 |
| `data` | 1~3 卡，数字 ≤ 6 字 | 巨型数字 8~11cqw + 标签 + 来源 |
| `warning` | 2~4 条 × ≤ 24 字 | 左侧 0.7cqw 通高 `alert` 竖条 + chip |
| `points` | ≤ 5 条 × 40 字 | 单列要点 |
| `compare` | 2 栏 × ≤ 4 条 × 30 字 | 左右等分 |

打分（只含确定项，无模型）：

```
100 - 30×溢出风险 - 20×容量浪费 - 15×本稿已用次数
    - 12×与前一页同版式 - 8×与密度档不匹配 + 5×与风格倾向一致
```

同分取声明顺序，保证确定性。**`purpose` 由 blocks 结构重算，不读模型自报。**

### 7.2 Frame 契约（后端产出）

```python
class FrameElement(BaseModel):
    role: str            # 16 个语义角色 + 8 个装饰角色
    span: tuple[int, int]  # 栅格列区间，x/w 由此推出
    y: float
    h: float
    text: str = ""
    size: float = 0     # cqw
    align: str = "left"

class Frame(BaseModel):
    purpose: str
    grid: list[float]    # 12 栅格边界
    elements: list[FrameElement]
    palette: dict[str, str]
```

- 单位 cqw（1cqw = 0.1in），零转换层。
- `span` 而非 `x/w`：对齐规则校验 span，避开浮点误差。
- 字号只允许阶梯七级：`6.2 / 4.6 / 3.4 / 2.6 / 2.05 / 1.85 / 1.25`。
- 行高只允许三档：`1.22 / 1.3 / 1.5`。
- **不落库**：Frame 是生成期与导出期用同一纯函数重算的产物，落库等于把渲染细节写进数据库。
- 装饰角色单独一层（`accentRule/alertRule/divider/panelTint/panelZebra/connector/badge/artNum`），只允许 `rect/roundRect/line/ellipse` 四种基元，文本一律独立元素——shape 里塞文字会让 PowerPoint 双击编辑崩掉。

### 7.3 前端只做一件事：把 cqw 数字塞进样式

`FrameSurface.vue` 接 `frame` prop，用绝对定位 + 行内 `cqw` 渲染，**不自己算任何坐标**。老六种版式继续走 `SlideSurface.vue`（一行不改，标 `FROZEN`）。分流判定：

```
useFrame(slide, event) = event.frame != null && slide.purpose in FRAME_COVERED
```

`FRAME_COVERED` 期一只含四种新形状；期二逐步扩到十种。回滚开关放 `renderMode.ts`（构建期常量 + `?frame=0`），舞台与胶片条必须共用。

---

## 八、导出后端可插拔（ppt-master 接入处）

### 8.1 三档后端

```python
# backend/app/ppt/export/base.py（新建）
class ExportBackend(Protocol):
    id: str
    def available(self) -> bool: ...
    async def export(self, deck: DeckContent, frames: list[Frame], out: Path) -> Path: ...
```

| id | 实现 | 依赖 | 何时用 |
| --- | --- | --- | --- |
| `native` | 前端 `pptxExport.ts`（现状，348 行） | pptxgenjs | 老稿、离线、快速导出 |
| `svg-design` | `Frame → SVG`（确定性）→ `svg_to_pptx.py` | python-pptx + skia-pathops + uharfbuzz | **新稿默认** |
| `svg-design-llm` | 模型逐页写 SVG → 同一转换器 | 同上 + 长上下文模型 | 高级模式，默认关闭 |

**三档共用同一份 Frame**，所以「换后端」不会改内容、不会改页数，只改字形与几何精度。

### 8.2 ppt-master 的接入方式（关键工程细节）

**为什么值得接**：它的转换器把 SVG 落到 DrawingML 原生形状，突破了 pptxgenjs 的四个硬顶 —— 渐变不支持、图片裁剪不支持、文本描边不支持、`roundRect` 圆角是比例值对不上 CSS px。

**为什么不能接它的生成方式**：模型逐页写 SVG 意味着（a）没有像素基线，回归网失效；（b）文本变成 shape 树，用户在编辑面板改一个字就要重算整页坐标；（c）10 页 10-20 分钟，与 SSE 七步工序不兼容；（d）token 消耗约 10 倍。

落地步骤：

1. **vendor 而非 pip**：`backend/vendor/ppt_master/`，pin 到具体 commit，只保留 `skills/ppt-master/scripts/svg_to_pptx.py` 与其 import 的模块。记录 commit hash 与 MIT 署名（`NOTICE` 文件 + 关于页）。
2. **不装 PyMuPDF**：它只被 `pdf_to_md.py` 用，是 AGPL-3.0，与主体 MIT 不同。我们不摄入 PDF 到转换链路，明确剔除。
3. **子进程隔离**：`asyncio.create_subprocess_exec(sys.executable, "-m", "app.ppt.export.bridge", manifest_json)`，临时目录、无网络、超时 90s、并发 1（pptx 写盘非并发安全）。
4. **SVG 输入白名单**（`svg_subset.py`）：只接受我们自己 `svg_render.py` 的产出，且逐项断言禁用 `<filter>/<mask>/<pattern>/<textPath>/<foreignObject>/<use>` 与渐变填充。**不做用户上传 SVG 的解析**——这是一条攻击面，且没有产品价值。
5. **字体单一**：SVG 里只写 `SimSun` / `Microsoft YaHei`（与 pptx 导出同族）。DOM 与 SVG 用同一组字体名，避免「预览是黑体、导出是宋体」。
6. **失败即降级**：转换器抛错 → 该次导出回落 `native`，并在回执里明示 `export_backend_used`。**不静默降级**。

### 8.3 新 API

```
POST /api/ppt/export
入参：{content: DeckContent, backend: "native" | "svg-design", theme_id}
出参：{url, bytes, backend_used, warnings[]}
```

前端导出按钮改为「先试后端导出，失败再本地 `pptxExport`」。老的前端导出路径**保留不删**（离线可用是产品能力，不是技术债）。

### 8.4 一致性断言（这是这条路线的验收核心）

同一份 Frame 喂三个消费方，必须几何一致：

| 断言 | 方法 | 阈值 |
| --- | --- | --- |
| DOM ↔ SVG | 截图对照（`chrome --force-color-profile=srgb`，`deviceScaleFactor:1`） | 分区 diff < 0.3% |
| SVG ↔ pptx | 解压 pptx 读 `a:off/a:ext`，与 Frame 的 cqw×0.1in 对账 | 偏差 < 0.02in（0.2cqw） |
| 文本可编辑 | `<a:t>` 节点数 ≥ Frame 文本元素数，`<p:pic>` = 0 | 硬断言 |
| 字体不缺 | `document.fonts.check()` + 导出侧字体名对账 | 0 缺字页 |

---

## 九、编辑闭环

| 能力 | 入口 | 成本 | 期 |
| --- | --- | --- | --- |
| 改字段（现状） | 左侧面板 | 0 | 已有 |
| 单页换排法 | 页工具条「换个排法」，给 top-3 | 0（`layout` 纯函数） | 期一 |
| 单页重写 | 「重写这页」+ 可选指令 | 1 次调用 | 期一 |
| 整份换皮 | 预览卡（palette × 密度，12 个预设） | 0 | 期二 |
| 自然语言改页 | 对话框 | 1 次调用 + diff 回执 | 期二 |

**换皮只渲 3 页缩略图**（封面 + 内容 + 数据），不渲整份。成本在用户的决策疲劳，不在渲染。

---

## 十、质量网

1. **六条硬拦**：溢出、字号越阶、对比度、单页颜色数 ≤ 3、字体族 ≤ 2、栅格对齐。
2. **三条只提示**：留白率、密度、节奏（相对量，硬拦会误伤）。
3. **80 页金标集**：20 主题 × 4 预设，语义取自 `templates.py` 的 keyless 确定性产物，**不接模型**，永远可复现。
4. **两套基线**：`chrome-<major>/` 下 DOM 截图基线、`pptx-<commit>/` 下导出几何基线（因为转换器版本变了几何会动）。
5. **回归口径**：老六种版式渲染与导出**像素零变化**；新形状页 diff < 0.3%。

---

## 十一、分期

### M0 契约与兼容（5 人天）

- `backend/app/ppt/blocks.py` 新建：`Block` + 七种类型的规范化函数。
- `backend/app/ppt/schemas.py`：加 `SlidePurpose`、`SlideNode` 四字段（全默认）。
- `frontend/src/types/ppt.ts` 同步；`usePptStudio.ts:35` 的 `sanitizeSlide` 显式透传。
- 测试：`test_ppt_schema_v1_compat.py` —— 加载 v1 fixture、往返保存、断言不丢字段不 422。

**验收**：`pytest tests/test_ppt_api.py` 原样全绿；`npm test` 全绿。

### M1 管线 v2 骨架（6 人天）

- `constants.py` 加 `ALCHEMY_STEPS_V2["ppt"]` 七步；`steps_for()` 分流。
- `resources/service.py` 按 `pipeline_version` 分流 compose 分支（先写测试）。
- `frontend/src/config/pptStudio.ts` 的 `PPT_STEPS` 按 `pipeline_version` 二选一（不能写死四步，否则进度条只有 4 格却走 7 步）。
- 新增 `test_ppt_api_v2.py`：7 次 `step_start`、大纲暂停后 resume 从 `compose` 起、老任务仍 4 次。

**验收**：两条管线并存，各自测试全绿。

### M2 内容智能（26 人天，本方案核心）

> 相对早前版本的 15 人天，多出的是「听懂 + 取材单 + 三档依据 + 拒绝/桥接」四件，砍掉的是 `required_slots` 强制覆盖（约 -4）。净增约 11。

- `backend/app/agents/intent.py` 新建（**doc / ppt 共用**）：13 值 `IntentKind`、`Intent` 模型、evidence 子串校验、conf 阈值、三层降级；`templates` 兜底。
- `backend/app/agents/select.py` 新建：`SelectionPlan` / `SelectionItem`、三态判定、组织器校验（至少一个 organizer）。
- `backend/app/ppt/brief.py`：`DeckBrief`（**去掉 `required_slots`**，改为引用 `SelectionPlan`）+ prompt + 回落。
- `backend/app/ppt/agent.py`：`frame()` 改为 async 调模型（**签名变更，调用方仅 service.py 一处**），一次调用同时产出 intent / organizers / selection / arc；新增 `compose_slide(index)`。
- `backend/app/ppt/prompts.py`：`BRIEF_SYSTEM_PROMPT`（含要历史/对比/机理/科普等 13 类示例与 evidence 要求）、`SLIDE_SYSTEM_PROMPT`（单页，含取材单切片与档位声明）、`REWRITE_SYSTEM_PROMPT`；`OUTLINE_SYSTEM_PROMPT` 把写死的讲授顺序改为**取材单 + 组织器驱动**。
- `frontend/src/components/ppt/SelectionPanel.vue` 新建：取材单勾选、三态切换、低置信度候选 chips、`model` 档提示。
- `backend/app/ppt/quality.py`：八项指标（`forbidden_hits` / `must_coverage` / `evidence_honest` 为新增三项）+ `DeckQualityReport`。
- `backend/app/ppt/retrieve.py`：主题 → 文档集合（药材 / 方剂 / 章节三路）+ 库内无实体时不检索的分支。
- 拒绝出口：`intent=concept` 且模型自评不可靠时返回 `frame_only` 稿（只给骨架与问题清单，不编事实）。
- 测试：`test_intent.py`（40 条评测集，准确率 ≥ 88%，判错必须低置信度，evidence 不在原话即作废）、`test_ppt_selection.py`（skip 项不得进 prompt、must 覆盖判定）、`test_ppt_quality.py`（`forbidden_hits` 必须为 0、越界 index 不计有效引用）、`test_ppt_compose_slide.py`（单页失败不拖累整份）。

**验收**：评测集准确率 ≥ 88%；「人参的发展历史」产物中不出现 `dosage/cautions` 任何词面；课外主题（如咖啡史）可生成且每块 `model` 内容带角标；keyless 产物里四种新形状合计 ≥ 25%；单页失败率 < 5%；整稿延迟 < 30s。

### M3 排版与渲染（12 人天）

- `backend/app/ppt/layout/`：`capacity.py`、`score.py`、`frame.py`、`layouts/{matrix,process,data,warning}.py`。
- `backend/app/ppt/errors.py` 与 `measure.py`：六阻断 + 三提示。
- `frontend/src/components/ppt/FrameSurface.vue`：只读 `frame` prop，颜色只读 `--ppt-*`。
- `frontend/src/ppt/renderMode.ts`：分流 + 回滚开关，舞台与胶片条共用。
- 测试：`test_ppt_layout.py`（4 个 layout 的确定性快照、容量边界、打分排序稳定）。

**验收**：4 形状 × 4 palette = 16 张截图在 1280/1440/1920 三档无溢出；老六种零变化。

### M4 编辑闭环（8 人天）

- `POST /api/ppt/rewrite-slide`（白名单 + diff 回执）。
- 页工具条：换个排法（top-3）、重写这页。
- `PptStylePicker.vue`：3 页缩略图的换皮卡。

**验收**：重写后只该页变化（其余页 diff = 0）；换排法零模型调用。

### M5 ppt-master 导出后端（10 人天）

- `backend/vendor/ppt_master/`（pin commit + NOTICE）。
- `backend/app/ppt/export/{base,svg_render,svg_subset,bridge}.py`。
- `POST /api/ppt/export`；前端导出按钮改为「后端优先、失败回落」。
- 测试：`test_ppt_export_svg.py`（子集白名单、几何对账、`<p:pic>` 为 0、缺字检测）。

**验收**：20 页真实稿，文本可编辑率 100%、几何偏差 < 0.2cqw、体积 < 8MB、10 页导出 < 3s。**任一项不过关就退回 `native` 默认档，`svg-design` 降级为可选项**（这一步是 go/no-go 闸门）。

### M6 金标集与灰度（12 人天）

- 80 页金标集 + 两套基线。
- `FRAME_COVERED` 从 4 种灰度到 6 种，每步跑金标。
- 老稿抽查 20 份。

**验收**：阻断 0 违例；人工四维评分表（层级清晰 / 可读性 / 版面平衡 / 整份一致）填完；老稿 diff < 0.3%。

### 期二（下期，不在本期预算内）

- `FRAME_COVERED` 扩到 10 种，老 `SlideSurface.vue` 逐步退役。
- 材料摄入（PDF/DOCX/URL）——依赖 ppt-master 的 `source_to_md` 或自建。
- 材料摄入不做，`svg-design-llm` 永远只是玩具。

---

## 十二、风险

| # | 风险 | 触发信号 | 缓解 |
| --- | --- | --- | --- |
| 1 | **七步与四步双表并存变永久** | v2 上线 3 个月后仍有 v1 任务 | `pipeline_version=1` 的新建任务在 M4 后**禁止**（前端不再传，后端拒收），v1 只服务存量；给 `agent.py` 加行数预算只减不增 |
| 2 | `compose` 同名分支分流写错 | v1 任务走进 v2 分支 | 先写 `test_ppt_api_v2.py` 再改 `service.py`；分流条件只依赖 `_pipeline_version(task)` |
| 3 | 逐页并发把模型配额打满 | 429 增多 | 并发上限做成配置（默认 4）；429 时退避并降到 2，不重试失败页 |
| 4 | 单页失败降级后整份风格断裂 | 模板页与新页视觉不一致 | 降级页也走同一套 layout，只内容取模板；报告里标黄，不静默 |
| 5 | `svg-design` 几何对不上 | pptx 打开后文字压线 | M5 是 go/no-go 闸门；对不上就退回 `native`，不要把闸门当形式 |
| 6 | 转换器升级改几何 | 基线大面积 diff | vendor 锁 commit，升级走独立 PR + 全量金标 |
| 7 | `extra="forbid"` 打回存量稿 | 保存报 422 | 新字段全默认 + 往返测试（M0 验收项） |
| 8 | `sanitizeSlide` 漏字段 | 保存成功但 `blocks` 消失 | 往返测试 + 前端断言 `Object.keys` 覆盖 |
| 9 | 逐页生成延迟翻倍 | 实测 > 30s | 并发提到 6；再不行把 `arc` 控制在 3 章内（页预算 8） |
| 10 | 立骨模型跑偏 | 章节弧出现「概述」「意义」 | prompt 里白名单约束 + 结构校验 + 整份回落，不重试 |
| 11 | ppt-master MIT 署名缺失 | 法务/合规检查 | `NOTICE` + 关于页 + README 三处；vendor 目录保留 LICENSE |
| 12 | 禁用位图导致观感上限被压低 | 用户对比豆包「不如」 | 这是产品决策，见第十五节 D1 |

---

## 十三、明确不做

| 砍掉 | 为什么 |
| --- | --- |
| 模型逐页写 SVG 作为默认档 | 不可回归、改字即改坐标、10-20 分钟/10 页。只留 `svg-design-llm` 高级档 |
| 让模型自报 `kind` / `purpose` | 已有教训（`draft.py:9-10`），一律按结构重算 |
| Frame 落库 | 渲染细节进数据库，迁移成本不可逆 |
| 后端 Chromium 保真导出 | 只需可编辑交付；引 Playwright 带来字体缺失与容器体积问题 |
| 第 5 道工序加在 v1 表上 | v1 表有 4 次 `step_start` 断言；要 7 步就开 v2 表 |
| 用户上传 SVG 解析 | 攻击面 + 无产品价值 |
| PyMuPDF 进依赖 | AGPL-3.0，与主体 MIT 冲突；我们不摄入 PDF |
| 用 LLM 当审美裁判打分 | 不可回归、不可缓存、延迟翻倍 |
| 自动静默改文案 | 违反既有价值观（`agent.py:157`） |
| `transform: scale` 自适应 | 文字发虚、输入框点不准；继续用 cqw |

---

## 十四、投入

| 里程碑 | 内容 | 人天 |
| --- | --- | --- |
| M0 | 契约与兼容 | 5 |
| M1 | 管线 v2 骨架 | 6 |
| M2 | 内容智能（听懂/取舍/标源/逐页/校勘/检索） | 26 |
| M3 | 排版与渲染 | 12 |
| M4 | 编辑闭环 | 8 |
| M5 | ppt-master 导出后端 | 10 |
| M6 | 金标集与灰度 | 12 |
| — | 缓冲（真机调试、字体、跨版本兼容） | 8–16 |
| **合计** | | **87–95（含缓冲）** |

不含：材料摄入（期二，约 15-20 人天）、图片资产管线（另 40-60 人天 + 设计资源）。

**节奏建议**：M0-M3（49 人天）先做，这一段不碰 ppt-master，上线即是「豆包式流程」且内容层已经听得懂人话；M5 是独立可裁剪的一段，go/no-go 不过就整段砍掉，不影响前四段的价值。

### 14.1 Token 账（实测口径，非估算）

基线实测（`麻黄`，7 份 doc / 7 条 sources，12 页大纲，CJK 按 0.6 tok/字、ASCII 0.3）：

| v1 现有两次调用 | 字符 | token |
| --- | --- | --- |
| `OUTLINE_SYSTEM_PROMPT` | 614 | 298 |
| outline payload | 2,032 | 815 |
| `COMPOSE_SYSTEM_PROMPT` | 852 | 395 |
| compose payload（含 12 页大纲） | 3,118 | 1,239 |
| 输出（大纲 ~350 + 整份草案 ~1,450） | — | ~1,800 |
| **v1 合计** | | **≈ 4,550** |

v2 每份 12 页稿：

| 调用 | 次数 | 输入 | 输出 | 说明 |
| --- | --- | --- | --- | --- |
| brief | 1 | 1,400 | 900 | 系统 800 + 3 份药材摘要 600；输出含 intent + 取材单 + 弧，比只出 brief 长约 750 |
| outline | 1 | 1,220 | 550 | 系统 420 + brief 150 + 摘要 600 + 来源 50 |
| compose | 12 | 16,200 | 2,400 | 每页 1,350 = **系统 1,000** + 单页素材 300 + 取材单切片 50 |
| rewrite | 0~3 | 1,800 | 500 | 只对不合格页，喂「哪条不合格」不喂全稿 |
| **合计** | 13~16 次 | **18,800~20,600** | **3,850~4,350** | 原始 token ≈ v1 的 5.0~5.5 倍 |

**两个必须写进实现的结论**：

1. **compose 的输入有 74% 是重复的 system prompt**（1,000 × 12 = 12,000）。所以必须让所有每页变量走 user 消息，`SLIDE_SYSTEM_PROMPT` 逐字节不变——否则上下文缓存不命中，成本按原价付。命中后计费等效输入 ≈ 8,000~9,800，总量 ≈ 11,900~14,150，约 v1 的 **2.6~3.1 倍**（不是 5 倍）。
2. **输出成了成本主项**。约 4,000 输出 token 按 4 倍于输入的单价计，占单份成本一半以上。「逐页少写、写准」比「压输入」更省钱；`matrix` 单元格 ≤ 12 字这条上限同时是质量约束和成本约束。

页数缩放公式（N = 页数，r = 重写页数）：

```
输入 ≈ 2,620 + 1,350 × N + 900 × r
输出 ≈ 1,450 +   200 × N + 250 × r
```

N=8 → ≈13.4k / 3.1k；N=12 → ≈18.8k / 3.9k；N=16 → ≈24.2k / 4.7k。

**三个会偷偷放大成本的点**：

- **失败重试必须按页**。整份重试会让 12 次调用翻倍；`asyncio.Semaphore(4)` + 单页超时 45s 的目的之一就是让失败局部化。
- **不要给逐页调用整份大纲**。给了模型就会复读别页内容，输出变长且质量下降（第六节已有此约束，成本上是同一件事）。
- **`svg-design-llm` 不是「贵一点」**。模型逐页写 SVG 每页输出 3,000~6,000 token，12 页 ≈ 40k~70k 输出，是 v2 的 15~20 倍。这是它默认关闭的第二个理由（第一个是不可回归）。

**单价换算**：按 Flash 档 ¥1/百万输入、¥4/百万输出估，单份 ≈ ¥0.025（缓存命中后）；Pro 档 ¥2/¥8 则 ≈ ¥0.051。v1 对应 ¥0.010 / ¥0.020。**不要用 Pro 跑逐页与 brief**——两者都是结构化填充，Flash 足够。

---

## 十五、需要拍板的三件事

| # | 决策 | 选项 | 影响 |
| --- | --- | --- | --- |
| D1 | 是否解禁位图（配图/图标） | A 继续禁 / B 允许装饰性位图 / C 允许配图 | 不解禁，「豆包级观感」上限被压低一大截；这是产品定位，不是工程问题 |
| D2 | 新稿默认导出后端 | A `svg-design` / B `native` | 选 A 则 M5 的闸门更严；选 B 则 ppt-master 只是可选增强，投入可砍到 5 人天 |
| D3 | 是否开放 `svg-design-llm` 高级档 | A 开（限高级用户，明确标注不可编辑/慢） / B 不开 | 开了要额外做沙箱与配额；不开可以省 3-4 人天 |

**默认建议**：D1 选 B（允许装饰性位图，禁止内容性图片）、D2 选 A（但 M5 闸门不过就退 B）、D3 选 B（先不开）。

---

## 附：本方案与 v2 方案的差异清单

| 项 | v2 方案 | 本方案 | 原因 |
| --- | --- | --- | --- |
| Frame 归属 | 前端生成 | **后端生成** | 要接 Python 转换器，单源必须在后端 |
| 工序数 | 4（不改） | **v2 表 7 步，v1 不动** | 逐页生成需要独立的 compose/verify/layout/render 边界 |
| 生成方式 | 整份一次 | **逐页并发** | 一页崩不拖累整份 |
| 版式数量 | 10 | 10（同） | — |
| 导出 | 只 pptxgenjs | **三档可插拔** | 突破 pptxgenjs 的渐变/裁剪/描边限制 |
| 质量网 | 五项指标 + 金标 | 同（沿用） | 结论仍成立 |
| 设计空间 | 4 palette × 3 密度 | 同（沿用） | 结论仍成立 |
