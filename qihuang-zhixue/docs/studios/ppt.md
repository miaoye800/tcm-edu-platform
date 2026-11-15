# 幻灯工坊 · 升级计划

> 分册之一。共同部分（8 步形状、两处闸门、来源三档、槽位两类）见 `README.md`；素材规格见 `assets.md`，本文只引用不复述。

---

## 1. 升级目标

把「讲什么」之外的另一半——**「长什么样」——变成用户能在生成前看到并确认、系统能复用、改动好坏可测的一等公民**。

三条具体目标：

1. 用户说「米白宣纸风、12 页」时，系统给出的不是四个颜色值，而是一份**可渲染的设计单**：每页版式、密度、纸底、角饰、图位、设计理由与假设；用户改完确认，成品必须遵守。
2. 「按页生成」名副其实：一页一次调用，一页失败只重做那一页，其他页不动。
3. 素材不足不再表现为"丑"或"占位符"，而是表现为**一个被明确定义的槽位按四级阶梯降级**，且每一次降级都留痕可查。

---

## 2. 现状问题

每条都给坐标，便于实施时定位。

### 2.1 流程与内容

1. **讲授顺序写死在提示词里**。`OUTLINE_SYSTEM_PROMPT` 明写「讲授顺序固定为：封面 → 导览 → 性味归经 → 功效 → 主治 → 鉴别/配伍 → 用量与禁忌 → 总结」（`backend/app/ppt/prompts.py:18`），最后一句才是"若资料不足就少一页"。用户确认的方案在这里没有位置——这是"确认完又偷偷回到固定模板"的实现级根因。
2. **版式由页序决定，不由内容决定**。`_resolve_kind` 先判 `index == 0 → cover`、`total >= 3 and index == total - 1 → summary`（`backend/app/ppt/draft.py:288-304`），再看结构。所以"第 8 页我想放对照表"这件事，在数据层就无法表达。
3. **一页只能存一句话**。`SlideNode` 只有 `kind / title / subtitle / bullets(≤6) / columns(≤2) / note / source_indexes`（`backend/app/ppt/schemas.py:34-62`）。对照、流程、矩阵、数据卡、警示、引文六类内容无处存放，只能降维成一条 bullet，这就是"12 页里八九页长得一样"的直接原因。
4. **整份一次生成**。`compose` 一次 `structured_create` 返回 `DeckDraft` 全量 `slides`（`backend/app/ppt/agent.py:102`、`:128-140`），没有逐页调用、没有单页重试、没有页内失败隔离。
5. **受众假设静默**。`PptParams.parse` 在用户未说明时直接填「中医药专业本科生」（`backend/app/ppt/params.py:39`），方案里不写这个假设（`planning.py:382` 直接取 `ppt_params.audience`），用户只能从成品反推。

### 2.2 设计与视觉

6. **主题 = 5 个 hex × 4 套**。`frontend/src/config/pptThemes.ts:32-61` 每套只有 `bg/fg/muted/accent/line` 五个色值，四套主题共用同一套版式几何。没有字体对、字号阶梯、纸底配方、纹样、印章、密度——"米白宣纸风"没有表达载体。
7. **版式是 if 分支，不是数据**。`SlideSurface.vue` 里 `kind === 'cover'`（`:10`）、`kind === 'section'`（`:40`）、其余一个 `v-else`（`:68`）承载 agenda/bullets/compare/summary 四种。预览与导出各写一份坐标（`SlideSurface.vue` 的 cqw 与 `pptxExport.ts:28-41` 的 pt/英寸），靠注释约定"两边一起改"。
8. **版心比例反了**。当前 `padding: 5.2cqw 6cqw 4.2cqw`（`frontend/src/components/ppt/SlideSurface.vue:219`，三值简写 = 上 5.2 / 左右 6 / 下 4.2），上下窄、左右宽；地脚与页码共用正文区，没有独立定位。

### 2.3 素材与资产

9. **全链路零图片**。`schemas.SlideNode`、`draft.DraftSlide`、`prompts.COMPOSE_SYSTEM_PROMPT`、`SlideSurface.vue`、`pptxExport.ts` 五处均无图片字段或调用；导出只用 `addText / addShape / addNotes`。
10. **素材库不存在，且已有两次失败先例**。`frontend/public/story/herbs/*.svg` 四个 777 字节占位卡（渐变 + 两个圆 + 一行字，除药名外逐字节同构）；`components/ui/XiaoqiMascot.vue:20` 引用不存在的 `src/assets/mascot/expressions/` 目录，靠 `@error` 静默兜底。这两处证明的不是"素材不可用"，而是"手工资产 + 无登记表"必然腐化。
11. **知识库的图片字段是空的**。`kb/models.py:189` 有 `Media(portrait_url, specimen_url)`，但 `.chroma/herbs.json` 451 条里 `media` 键出现 0 次。所以**证据位当前覆盖率为 0**，第一版必须允许"无图"，而不是允许"补一张生成图"。

### 2.4 校验与工序

12. **校验只查骨架**。`_deck_verify`（`backend/app/resources/service.py:385-435`）只查：标题重复、命中确认方案、缺页、空页、引用越界、页级档位。不查溢出风险、字号越阶、装饰预算、许可、来源诚实、purpose 与 blocks 是否一致。
13. **工序表前后端错位**。前端 `PPT_STEPS` 仍是 v1 的 `frame/outline/compose/polish`（`frontend/src/config/pptStudio.ts:16-21`），后端 v2 是 `plan/compose/verify/seal`（`backend/app/resources/constants.py:82-87`）。后端其实已经把正确标签塞进 `plan` 事件（`service.py:696-701` 的 `steps`），但前端只取 `plan` 丢弃 `steps`（`usePptStudio.ts:220-222`）。
14. **能力契约缺位**。`resource.generate` 对 ppt 直接 501（`backend/app/capabilities/handlers/resource.py:51-56`），PPT 至今没有对外可调用的入口。

---

## 3. 结构调整方向

按优先级排序，前四项是本轮重心。

### 3.1 流程实例化：8 步在幻灯工坊长什么样

| 步 | 幻灯工坊的具体动作 | 产物 | 失败处置 |
|---|---|---|---|
| understand | 抽取用途/受众/篇幅/风格；主题过宽触发 G0 | 主题理解 + 假设清单 | 抽取失败 → 用原话作为主题，标注"未解析" |
| assess | 知识覆盖（充分/部分/未覆盖）+ 素材覆盖（哪些槽位填得满）+ 缺口清单 | 双覆盖报告 | 检索失败 → 全部记 `model` 档，gap 写明 |
| plan | 逐页：label / intent / detail / 来源档 | `ResourcePlan`（页为单位） | 模型失败 → 候选骨架，标"按通用结构给出" |
| design | 逐页：版式原型 / 密度 / 槽位；整份：主题 / 纸底 / 栅格 / 字体对 / 角饰集 / 理由 / 假设 | `DeckDesign` + 12 页线框 | 设计失败 → 版式全走 `points`，主题取感知到的默认 |
| **G1** | 结构与设计一次确认；可分别改、可推翻重拟 | `confirmed_plan` + `confirmed_design` | — |
| select | 逐槽解析：参数化 → 库内命中 → 平替 → 生成/降级 | `slot_resolution` | 全阶失败 → 降级版式，不留坏图 |
| compose | 逐页调用；每页生成后立刻单页 check，失败定向重写 ≤1 次 | `blocks[]` + `asset_refs` | 重写仍失败 → 保留原稿 + 报原因，不阻断其他页 |
| check | 五类校验（见 §3.7） | `verify` | 只修相关页，不重跑整份 |
| deliver | 落库 + 缩略图 + 逐页预览 + 导出 | `resource_id / open_url / stream_url` | 断点续跑：从已确认方案与已完成页继续 |

对应你列的七点，第 1 点落在 `understand + G0`，第 5 点落在 `compose` 的逐页回路，其余一一对应，不再赘述。

### 3.2 设计步：`DeckDesign` 与线框预览

**契约**（后端 pydantic，前端镜像 TS，字段全部带默认值以保证旧数据可读）

```python
class DesignSlot:
    slot: Literal["figure", "evidence", "ornament", "ground"]
    role: str                 # 主图 / 配图 / 角饰 / 底纹
    subject: str              # 人参 / 阴阳 / 无 —— 是"要找什么"，不是资产 id
    style: Literal["plate", "line", "photo", "motif", "none"]
    priority: Literal["required", "optional"]
    aspect: Literal["portrait", "square", "landscape", "any"]

class PageDesign:
    unit_id: str              # 对应 PlanUnit.id
    archetype: str            # 十种版式原型之一
    density: Literal["airy", "normal", "dense"]
    slots: list[DesignSlot]
    note: str                 # 一句话：为什么这一页这样排

class DeckDesign:
    theme_id: str             # paper / ink / celadon / cinnabar
    paper: str                # 纸底配方 id
    grid: str                 # 版心 / 栅格 id
    type_pair: str            # 字体对 id
    ornament_set: str         # 角饰母题集 id
    pages: list[PageDesign]
    rationale: list[str]      # 3–5 条，给用户看的理由
    assumptions: list[str]    # 受众 / 场景假设，与 G0 呼应
```

三条硬规矩：

1. **槽位只写需求，不写资产 id**。模型编造 `asset_id` 会重演 `XiaoqiMascot` 式的坏引用；解析权在 `select` 步。
2. **设计不可用自由文本表达**。不能渲染的东西用户判断不了、系统也校验不了。`rationale` 是给人看的注释，不参与渲染与校验。
3. **设计单必须可看**。`DeckDesign` + 12 页线框缩略图同时提交。

**线框预览规格**

- 由**同一渲染内核**在 0.3 保真度渲染：真实版式几何 + 主题配色 + 图位灰块（带槽位图标与"主图/角饰/底纹"文字）+ 4 字占位文案。
- 缩略图 4 列 × 3 行，点击放大；右侧常驻「主题 / 版式 / 密度 / 图位」四个可改项。
- 面板可执行的动作：换主题（4 选 1）、改某页版式（10 选 1）、调密度（3 档）、图位「换一张 / 不要图 / 用母题」。
- 前提条件：版式必须是数据（`Frame`），不是 `SlideSurface.vue` 里的 `kind` 分支。这一步不做，线框就只能另写一套，预览与成品必然不一致。

### 3.3 装帧系统：三条不变 + 三条变

**不变（契约，生成期与导出期用同一纯函数重算，不落库）**

| 项 | 规格 |
|---|---|
| 画布 | 16:9，唯一的尺寸单位是 cqw（导出时换算成 pt，换算表由同一函数给出） |
| 版心 | 左右 9 cqw、天头 8.4 cqw、地脚 7.2 cqw；内容只在版心内，装饰可出血 |
| 页眉 | 0.5pt 横线贯穿版心 + 左端 1.2cqw 朱砂短线 + 中线鱼尾；左章节号、右主题名 |
| 地脚 | 左：依据序号与缺口标注；右：页码。位置恒定，不随内容浮动 |
| 字号阶梯（7 级） | 7.6 / 4.6 / 3.2 / 2.2 / 1.8 / 1.5 / 1.3 cqw，依次对应封面主标、页标题、小节标题、正文、次要信息、注释、角标与页码 |
| 行高（3 档） | 标题 1.25、正文 1.65、密集 1.45 |
| 装饰词汇（8 种） | 横线 / 竖线 / 色块 / 斑马格 / 连接线 / 角标 / 大号数字 / 印章；**每页装饰预算 ≤2** |
| 角饰 | **外框 + 内花**两段：距边 2.2 / 3.8 两道平行线 + 转角方胜，内填卷草/折枝/竹叶/回纹/缠枝。四角同一母题镜像，尺寸封面档 14cqw、正文档 9.5cqw |
| 内容分布 | 内容块在版心内**纵向均分**（`space-evenly`），单块时居中；栏内条目同样均分。不许「堆在顶上、下半页空着」 |

角饰为什么是「外框 + 内花」而不是一根藤蔓（实测踩过的坑，别改回去）：

一根藤有起点、有方向、有终点——它是**一幅画**。把它镜像到纸的四角，得到的是四条方向各异、各自断在半路的草，看着像没排完的线稿。传统笺纸的角花从来是**两段**：外框给「这是一个角」的身份（双线 + 方胜），内花给手写的温度（叶、卷须、方框）。所以：

- 外框五款共用（`cornerFrame`），换内花不换框，全篇四角的语言才统一；
- 内花从内框拐角**起笔压在线条上**，不是「贴在角里」——茎要长在框上才有生长关系；
- 正文页传 `frame=0` 只留内花：版心左右只有 9cqw，塞进界栏就是一道没画完的短线；
- 全墨色。朱砂留给竖线、印章、分隔线——角花一上色就变成贴纸。

**变（跟内容与主题走）**

1. **版式跟内容走**：由 `blocks` 结构重算 `purpose`，不由页序决定。
2. **主题整套换**：配色 + 字体对 + 纸底 + 纹样 + 印章一起换，不允许只换色。
3. **密度三档**：`airy`（≤3 个内容块）/ `normal`（≤5）/ `dense`（≤7），与容量表联动。

**三种页面的范式**

- **封面三选一**：`界格`（竖排界格线 + 朱印，最不依赖图）/ `满版`（点题图压暗，标题压左下 1/3）/ `极简`（标题 + 朱砂竖线 + 留白）。有图用满版，无图用界格或极简——**这是"素材缺失不崩"的第一道保险**。
- **章节页**：巨号汉字序号（17cqw、15% 透明）+ 竖线 + 章节标题；序号随章节自动，不占正文。
- **尾页**：与封面**反相**（宣纸主题深底、墨夜主题浅底），一行结语 + 印章；不写"谢谢观看"。

### 3.4 内容块与十种版式原型

**`Block` 七类型**（沿用 `docs/ppt-doubao-mode-plan.md` §4.2，不重复设计）：`point` / `steps` / `matrix` / `figures` / `alert` / `quote` / `def`。

**`SlideNode` 增量**（全部带默认值，旧数据可读）

```python
purpose: SlidePurpose = "points"   # 十值：cover/agenda/section/points/compare/matrix/process/data/warning/summary
blocks: list[Block] = []           # ≤6
asset_refs: list[AssetRef] = []    # select 产出，不是模型产出
```

**十种版式原型与容量**

> 下表**只是语义与容量，不含几何**。`archetype` 只做语义标签：参与校验（对照页必须两栏）、参与降级（图版页无图时改什么），**不参与绘制**。几何由谁定见 §3.4.1。

| archetype | 允许的块 | 容量上限 | 典型视觉 |
|---|---|---|---|
| cover | point | 主标 ≤18 字、副标 ≤24 字 | 界格 / 满版 / 极简 |
| agenda | steps | ≤6 条，每条 ≤14 字 | 序号 + 横线 |
| section | point | 序号 + 标题 ≤16 字 | 巨号汉字序号 |
| points | point | ≤5 条，每条 ≤40 字 | 单栏，左齐 |
| compare | matrix | 2 栏 × ≤4 条，栏题 ≤10 字 | 双栏 + 中线 |
| matrix | matrix | ≤3×3，单元 ≤8 字 | 斑马格 |
| process | steps | ≤4 步，每步 ≤12 字 | 连接线 + 大号数字 |
| data | figures | ≤3 个数字卡，数值 ≤6 字符 | 数据卡 |
| warning | alert | ≤2 条，每条 ≤36 字 | 朱砂竖条 |
| summary | point + def | ≤4 条 | 收束排版 |

`purpose` 由 `blocks` 结构**重算**，同分按声明顺序，保证确定性；不采信模型自报（沿用 `draft.py:9-10` 的教训）。

#### 3.4.1 从「版式原型」到「场景」——上一节的十种原型不参与绘制

上面那张十行表**只是语义与容量**，不是几何。它回答「这页承载什么信息、最多几条」，不回答「标题放哪、图放多大」。两件事混在一起，就是「十二页里八九页长得一样」的根因：模型只能从十个里挑一个，挑完几何是写死的。

所以视觉层换了实现：**`Scene`（场景 DSL），九个基元，不给任何版式**。

| 层 | 谁定 | 作用 |
|---|---|---|
| `purpose` / `blocks` | 模型 | 信息结构。参与校验（对照页必须两栏）与降级（图版页无图时改什么） |
| `scene` | 模型 | 视觉方案。图层、坐标、字号、竖排、角花、印章，全在数据里 |
| 渲染器 | 代码 | 按基元画，不认识任何版式 |

**九个基元**（`backend/app/ppt/scene.py` / `frontend/src/render/scene.ts` 同源）：

| 基元 | 用途 | 关键字段 |
|---|---|---|
| `asset` | 库内素材 | `asset` / `fit` / `caption` |
| `motif` | 参数化母题 | `motif` / `params` |
| `rect` | 色块、描边块 | `fill` / `stroke` / `radius` / `rotate` |
| `rule` | 横竖细线 | `orient` / `weight` / `dash` |
| `circle` | 圆点、环形 | `fill` / `stroke` |
| `text` | 文字 | `role` / `size` / `font` / `write`（横排竖排） / `tracking` |
| `seal` | 印章 | `content`（≤4 字） / `shape` |
| `group` | 相对坐标分组 | `items`（子元素坐标 0..1） |
| `background` | 纸母题 / 满版图 / 纯色 | `kind` / `motif` / `asset` / `veil` |

三条硬规矩：

1. **颜色只认角色名**（`fg` / `muted` / `accent` / `line` / `bg`），不认 hex。同一份场景要能在 12 套配色下都成立；写死 `B5443A` 就只在一套下对。契约测试守着。
2. **坐标是 cqw**，画布 100 × 56.25，`box = (x, y, w, h)`。**允许负值**——出血是手法，不是错误；上限 ±25。
3. **元素数 24 是预算，48 是合法性**。超预算警告，超硬上限拒绝。排得密是手艺，摊成碎块是错。

**两张差别极大的页面，只差一个字段**：

```jsonc
// 参考图 1：角花 + 左图 + 右竖排
"background": { "kind": "motif", "motif": "paper-plain" }

// 参考图 2：整幅照片铺底压字
"background": { "kind": "asset", "asset": "photo-xxx-01", "fit": "cover",
                "veil": { "color": "fg", "opacity": 0.45, "direction": "from_bottom" } }
```

其余元素（大字、细线、色块）一个都不改。这就是「不限定模板」的落点：基元足够小，组合空间足够大。

**谁在写这份场景：`scene_art.paint_scenes()`。** 逐页一次调用（并发 3 路，上限 16 页），夹在 `compose` 之后、落库之前：

| 步 | 做什么 | 不过怎么办 |
|---|---|---|
| 请求 | 给一页简报：标题、内容块、可用素材（带许可与出处）、纸与角饰、画布与版心 | — |
| 校验 | `validate_scene`：基元合法性、出血上限、元素预算 | 退回兜底 |
| 机械修正 | 竖排框改回"一列"（宽 ≈ 字号 × 1.6，高 ≈ 字数 × 字号 × 1.15）；要缩到 3.2 cqw 以下才装得下的，不缩，判为"装不下" | — |
| 硬伤检查 | 标题必须出现在场景里 / 素材 id 必须在清单里 / 元素不少于 4 / 文字不许互相压住 | **回灌一次**，只告诉它哪一条不对 |
| 收尾 | 采用。页码不写进场景——地脚是装帧契约，由渲染层统一盖 | 两次不过 → 兜底 |

记账写进 `deck.scene_stats` → `meta.scene_art`：排了几页、哪几页退回、为什么。**实测三页全过**，而且每次的分区都不一样（竖排标题 + 单列正文 / 左文右图 + 四角 + 印章 / 短横分隔），模型给的设计理由也不是模板。

实跑暴露的两个真问题，都写进了代码：

1. **竖排的框语义和横排相反**。模型按横排习惯写 `w=20, h=4.6`，竖排会变成一条压在正文上的长列。这是坐标系约定不是设计判断，所以机械修正，不退回。
2. **文字压字**是最常见的硬伤：竖排标题一路写下来，撞上左手边那几行正文。面积比不到 25% 也已很难看（细长条压住横排的角），所以判据加了「两个方向都压进去过半字宽/字高」。第一次不过就把这句话回灌给模型，它通常能把文字挪到竖列右边。

**兜底不是模板库。** `scene_fallback.py` 在模型没给场景时铺一个安全版（统一头 + 内容栈 + 纸 + 角花），十个原型共用四个构造器。它只在失败时接管，永远产出同一份可预测结果——模板是「用户可选的东西」，兜底是「安全网」，前者收走设计权，后者不。

**版式手法进提示词，不进代码。** 「竖排标题宜配竖线界格」「主图出血比居中稳」「满版图必须给文字留低对比区域」——这些写成设计知识给模型读，它可以组合；写成 24 个 renderer，它只能照抄。

### 3.5 槽位与四级取用阶梯

槽位两类、许可白名单、登记表字段、归一化算子全部在 `assets.md`。本文只定幻灯侧的三条：

1. **页面级记录**：每页 `asset_refs[]` 写明 `slot / asset_id / provenance / license / attribution`；`provenance` 取 `parametric | library | generated | none`。
2. **降级不许留坏图**：阶梯走到第 4 级仍无解时，改版式（有图版式 → 无图版式），而不是放灰块或占位符。封面按 §3.3 的「界格/极简」兜底。
3. **角标规则**：`library` 平替必须标「示意」；`generated` 必须标「AI 生成示意」并可在编辑态一键删除；`parametric` 不标。

#### 3.5.1 从「按关键词派活」到「工具循环」

四级阶梯回答「走到哪一级」，没回答「谁来走」。上一版让代码走：读 `subject`、查登记表、算分、取第一个——结果是不管讲什么课，配的都是同一批图，因为**代码不认识内容，只认识字符串匹配**。

现在换成模型自己动手，代码只给工具和边界（`backend/app/ppt/agent_loop.py`）：

| 工具 | 作用 | 边界在哪 |
|---|---|---|
| `search_assets(source, query, kind, limit)` | 检索，一次最多 8 条候选 | 渠道清单（`channels.py`）+ `EVIDENCE_CHANNELS` |
| `look_at(asset_id, question)` | 看一眼，判断主体对不对 | 缩到 768px 再送视觉模型，省 token 且看得清纹样 |
| `assign_asset(unit_id, asset_id, slot, why)` | 钉到某页某槽 | `slot=evidence` 时渠道与许可双闸 |
| `generate_image(unit_id, prompt)` | 文生图兜底 | 每稿 ≤3 张、只进装饰位 |
| `finish()` | 收工，没配上的页留空 | 渲染层走无图版式 |

循环上限：**12 轮 / 90 秒 / 生图 3 张 / 单页 2 张**。一轮可含多个工具调用，所以 12 轮够「搜 → 看 → 换词重搜 → 生成 → 绑定」跑两遍。

三条边界写在代码里，不靠提示词自觉：

1. **证据位拒装饰源**。给证据位绑一张 Heritage 版画会被当场拒绝，并把拒绝理由回灌给模型，让它在下一轮改——而不是静静产出一张「看起来对」的错图。`CC-BY-NC` 同样拒；`unit_id` 不在任务清单里也拒。
2. **任何工具异常变成 `{"ok": false, ...}` 回给模型**，循环整体失败返回空结果，由 `select` 兜底。取素材只是一份稿子里最不重要的那十几秒，不能让它挂掉整份稿子。
3. **结果冻结**。绑定连同 `url / license / attribution / why` 写进 `meta.slot_resolution`，并置 `meta.art_frozen = True`；此后刷新、重连、导出都不再重跑——模型的不确定性只存在于这一次循环里。

真机实测（4 页设计单）：`ran=True rounds=10 calls=20 gen=1`，四页全部落定：封面取到《本草品汇精要》式明抄彩绘人参，证据位用本地黄芪饮片，对照页取克利夫兰「Landscape after Ni Zan」，警示页因馆藏无炮制图改为生成。模型给的理由是人话：「《本草品汇精要》式明抄彩绘人参，根形与植株俱全，做封面博物画气质最正。」——不是模板，是看过图之后写的。

为什么不是「把选图写成结构化输出」：结构化输出只能让它在给定候选里挑，挑不到就编。工具循环让它**能去别处找**，也能**承认找不到**——后者更重要。

#### 3.5.2 十九条渠道，六类

素材能不能用，模型不该靠猜。`channels.py` 给出一份**给模型读的菜单**，字段刻意少（它要在一次上下文里读完十九条）：

| 类 | 含义 | 检索能力 | 落盘 | 代表 |
|---|---|---|---|---|
| A | 本地库 | 按 registry 查 | ✅ | `local.heritage` 43 条 / `local.herbs` 5 条 |
| B | 有检索 API | **能换词重搜** | 多为 url-only | `api.wellcome` / `api.cleveland` / `api.iconify` / `api.phylopic` |
| C | 无检索站点 | **只能点名式取用** | 看许可 | `web.opendoodles` / `web.pngimg` / `web.linustock` |
| D | 生成 | — | memory-only | `gen.image` |
| E | 参数化母题 | 14 个 id | 自产 | `param.motif` |
| F | 字体对 | 8 套 | 系统字体 | `font.pair` |

渠道明细、逐条许可、`ROUTES` 顺序见 `assets.md` §5.6，本文只留三条交叉点：

1. **C 类能用的前提是「只能点名」**。模型不能自己拼 URL，只能在 `CATEGORY_PAGES` 登记过的分类里指名（pngimg 的 nature / flowers / objects / tableware / vegetables / food）。给一个无检索站点「搜一下」，它只会编出一条 404——四类里最容易高估的就是 C。
2. **`EVIDENCE_CHANNELS` 是硬约束**，只有 `local.herbs` 与 `api.wellcome`；新增渠道忘了归类，契约测试会挂。
3. **`ROUTES` 是顺序不是建议**：`herb-evidence` 只有两站，中间不许插「通用植物图」；生成永远不打头。

#### 3.5.3 远程素材：钉 URL、走代理、断图降级

远程素材**不落盘**。稿子里存的是「地址 + 元数据」，不是图：

```jsonc
{ "unit_id": "s1", "slot": "figure", "asset_id": "plate-445da94b",
  "url": "https://iiif.wellcomecollection.org/image/V0039…/full/1600,/0/default.jpg",
  "thumb": "https://wsrv.nl/?url=…&w=480&output=webp",
  "license": "CC0", "source": "api.wellcome", "pinned_at": "2026-11-09T…" }
```

为什么不落盘：许可禁止再配布的那几家落盘就是再配布；而馆藏 IIIF 地址是稳定的（OSS 临时地址不是）。钉住原图、渲染时按需取，比往仓库里塞三百张图干净。

**所有远程图过一次 wsrv 代理**（`materials.proxy_url()`），它同时解决四件事：

| 问题 | 不代理的后果 | 代理后 |
|---|---|---|
| CORS | Cleveland / pngimg 原图没有 CORS 头，屏幕能显示、**导出时一张都进不去** | 带 CORS |
| 尺寸 | 馆藏原图 4000px，一页只用 800 | `w=` 按需 |
| 格式 | JPEG 数百 KB | webp，约 1/8 |
| 缓存 | 每次重取 | 一年 |

代价是多一跳，所以只对**远程**用，本地库直接读文件。

**断图必须降级，不能留裂图。** `SceneSurface.vue` 给每张图记一个 `failed` 集合，失败即整块不渲染（背景图另记 `__bg__`，与前景分开）。实测把地址故意写错：图位消失、文字照旧、版式不塌——**缺图是版式问题，不是错误提示问题**。

地址解析只有一个入口 `render/assetSrc.ts`：稿内钉了 URL 就用它，否则回落本地 `/assets/{id}.webp`。预览、胶片条、pptx 导出三条路径共用这一个函数——三处各写一份，就是三处各裂一次。

### 3.6 成稿、校验与单页重写

**一次成稿，逐页核，定点修。** 这三件事分属三个模块，谁也不替谁做决定：

| 阶段 | 谁做 | 做什么 | 不做 |
|---|---|---|---|
| 成稿 | `ppt/agent.py::compose` | 一次调用写出整份 `DeckDraft`，按设计单逐页注入 `archetype`/`density`/`capacity` | 不逐页调模型——十二页十二次调用既慢，也让页与页的语气断掉 |
| 校验 | `service._deck_verify` | 五类问题逐页列出：结构、容量、依据、设计、资产 | **只报不改**。校验完偷偷重写，用户签过的方案就成了一张废纸 |
| 重写 | `ppt/revise.py` | 只重写「这一页没写对」的那几页 | 不改标题、不碰素材、不碰别的页 |

**compose 的输入是约束，不是形容词**（`_compose_payload`）：

```json
{
  "topic": "人参", "audience": "本科生", "theme": "paper",
  "outline": [{"title": "...", "points": ["..."], "intent": "...", "source_indexes": [0]}],
  "sources": [{"name": "中药学", "ref": "补虚药·人参"}],
  "herbs": [{"name": "人参", "evidence": "..."}],
  "design": [{"archetype": "compare", "density": "normal"}, {"archetype": "points", "density": "airy"}],
  "capacity": {"density": "normal", "blocks": 5}
}
```

输出只有 `slides[]`（`blocks` + `source_indexes` + `note`）。不输出 `archetype`（设计单已定）、不输出 `asset_id`（select 已定）。

**重写只认三类问题。** `revise.fixable()` 是唯一判据，与校验器的措辞对齐：

| 问题 | 为什么能靠重写解决 |
|---|---|
| `内容块超限` / `超出 X 密度上限` / `版式 Y 不接受 Z` / `单块字数超限` | 是「这页写多了、写错了块型」，删到装得下即可 |
| `页面没有内容` | 这页只剩标题，补正文 |
| `引用越界` | 页脚标的依据超出这次取到的材料条数 |

不归它管的两类，各有去处：**资产问题**（许可、署名、生成图进证据位）是 `select` 步定下的，改正文改不了它，硬改只会把「为什么用这张图」的理由抹掉；**不在确认方案内**是标题跑偏，要重排方案，不是重写一页。

**回路**（`revise_pages`）：

```
逐页取 fixable(issues)，最多 3 页
  → 模型重写这一页 → 用校验器自己的判据复检
      → 通过：落页，旧场景作废，只重排这一页
      → 不过：把「还差什么」原话回灌一次
          → 再不过：放弃，保留原稿并记 skipped 原因
  → 全部改完，重跑一次 _deck_verify
```

四个边界写在代码里，不写在提示词里：

1. **标题不可改**。`PagePatch` 里根本没有 title 字段——模型想改也没有地方写。
2. **一次最多三页**。半数以上的页都要重写，那本来就是该整份重跑；超出记 `over_budget`。
3. **两次不过就放手**。继续改只会把这一页越改越薄（实测最常见的退化是删成一句「本法需辨证使用」）。
4. **旧场景必须作废**。内容变了版面不改，会看到「新正文 + 按旧正文排的框」，比不改更糟。重排只传 `only=[被改的页]`，其余页连场景都不碰。

记账分两处：`deck_verify` 说的是「稿子现在什么样」，`deck_revise` 说的是「改过什么」（落 `meta.revise`）。合并存就回答不了后者。

### 3.7 校验扩展：五类

| 类 | 检查项 | 失败处置 |
|---|---|---|
| 结构 | 页序 ↔ 方案对应、`purpose` 与 `blocks` 一致、无自造页、无多页 | 报 `missing` / `extra`，标"不在确认方案内" |
| 容量 | 各块条数超限、总行高溢出风险（按版心高度估算）、单条字数超限 | 定向重写该页；超限两次则降到上一档密度 |
| 依据 | 引用索引有效、证据位是否绑定合法出处、`model` 块是否被标成教材 | 越界引用清零并记账；误标直接改档为 `model` |
| 设计 | 字号在 7 级内、装饰预算 ≤2、正文对比度 ≥4.5:1、栅格对齐 | 报违规项，不自动改（避免静默改设计） |
| 资产 | 许可存在、attribution 完整、`generated` 已标注、槽位类别未越界（生成图进证据位） | 越界 = fail，必须换资产或降级 |

**诚实边界**（写进回执，不写进营销文案）：规则能确认结构与索引有效，**不能证明一段医学陈述正确**。无法可靠核实的内容标 `model` 档，不得用"校验通过"制造确定感。

### 3.8 导出与保真

- 后端可插拔：`native`（现状 pptxgenjs，保留离线）/ `svg-design`（Frame → SVG → DrawingML）。
- **图位已进 pptx**（M0）：`exportDeckToPptx` 先 `preloadSceneAssets()` 把整份稿子的地址表合成一张 `id → src` 映射（`assetMapOf` 逐页合并 `asset_refs`），逐张经 `assetSrc()` 取图、转 JPEG base64，再按场景里 `asset` 元素的框 `addImage`。远程钉住的 URL 与本地文件走同一个函数，导出端不需要知道图在哪。三个决定：
  - 转 JPEG 而非直传 webp：PowerPoint 对 webp 的支持看版本，老版本显示红叉；
  - 用 JPEG 而非 PNG：图位是矩形裁切、无需透明，同一张 70KB 的 webp 转 PNG 近 1MB，JPEG 只有 1.5 倍；
  - 底色铺**主题纸色**而非白色：图片自带的透明边会自动融进纸面。
  - 实测：封面一张 59KB webp → pptx 177KB，内含 `ppt/media/image-1-1.jpeg` 128KB，`<p:pic>` 与 rels 均正确。
- **纸纹与角饰仍不进 pptx**：母题是 SVG，栅格化会让文件从几十 KB 涨到几 MB，而放映时那层淡纹几乎看不见。这是刻意取舍，不是遗漏。图与纸纹不是一回事——图是内容，纸纹是装饰。
- 已修正的旧判断：pptxgenjs **4.x 支持图片裁切**（`sizing: {type:'cover'}` → `<a:srcRect>`），此前文档写的「不支持图片裁切」是 3.x 的事。仍未支持的：渐变、`roundRect` 圆角与 CSS px 对齐。圆角裁切类版式在 `native` 下退化为直角，并在回执里写明 `export_backend_used`，**不静默降级**。
- 字体统一：DOM 与导出用同一组字体名（现状 DOM 用 Noto Serif SC，导出卖死 `SimSun`，换机器散版）。真修法是接通 `type_pair` 与四套 OFL 字体，见 M1。
- 保真断言：DOM ↔ SVG 分区 diff < 0.3%；SVG ↔ pptx 坐标偏差 < 0.02in；`<a:t>` 节点数 ≥ 文本元素数；`<p:pic>` 数量与图片元素数一致。
- **屏幕侧的回归门**：`scripts/shot-ppt-baseline.mjs`（`npm run baseline:ppt`）把十版式各截一张存进 `deliverables/ppt-baseline/`，之后每次改版式逐张 pixelmatch，超 0.5% 即失败。它管的是「改一处有没有把别的页带坏」，与上面那组导出断言是两件事：前者比像素，后者比坐标。

---

## 4. 可检查的交付物

每条都要能被现有测试体系接住。

| # | 交付物 | 判定 |
|---|---|---|
| 1 | **三张基准页**（封面 / 矩阵页 / 尾页）先在 HTML 里手工调到过评审，再反推成装帧契约 | 人工评审通过；跳过这一步就会重复今天的错误 |
| 2 | 十版式截图基线（默认纸面 / 角饰 / 主题各一张，共 10 张） | 改动前后 diff，超 0.5% 即 fail；换主题另算一套（`--update` 重写） |
| 3 | 线框 ↔ 成品一致性 | 同一 `DeckDesign` 渲两次，版式几何坐标一致（允许文案差异） |
| 4 | `layout_check` 扩展 | 溢出风险 / 字号越阶 / 栅格对齐 / 装饰预算 / purpose 与 blocks 一致，各一条用例 |
| 5 | 单页重写 | 只重写失败页；重写次数受限（≤3 页 / ≤2 轮）；失败保留原稿并报原因 |
| 6 | 每页 `asset_refs` 可展开到出处 | 无依据的块显示「模型补充」；生成图标「AI 生成示意」 |
| 7 | 断点续跑 | 生成中断后从「已确认方案 + 已完成页」继续，页数与内容不丢 |
| 8 | 导出保真 | §3.8 四条断言各一条用例；`export_backend_used` 在回执中可见 |
| 9 | 能力契约 | `ppt.generate` 与 `mindmap.generate` 同形：202 + task_id + stream_url + artifact；重复调用幂等 |
| 10 | 工序表同源 | 前端不再有 `PPT_STEPS` 静态表，标签全部来自后端 `step_label` |

**当前状态**（与 `README.md` §7.1 同步）：

- 已落地：#3（线框与成品读同一份 `frames.ts`：版心 8.4/9/7.2、字号七级）、#4 的容量/设计/资产三段、#6（`asset_refs` 带 `note`，平替标「示意」、生成标「AI 生成示意」）、#8 的版式部分（导出按 `layoutOf/blocksOf` 画十种版式）、#9（`ppt.generate` 契约）、#10（前端工序表改由服务端下发，本地表仅离线兜底）。
- M0 追加（美术资源升级第一批）：
  - **底纹不再恒为 `paper-plain`**。`select._parametric` 原来直接 `candidates[0].id`，主题里配的另几个母题永远轮不到。改为按**整份稿子**的装饰种子（主题 + 前三页 `unit_id`）稳定取一个：同一份设计重渲结果不变（截图基线仍有效），不同主题落到不同母题。实测 4 主题 × 8 标题共 36 次抽样，6 个底纹母题全部出现。
  - **许可白名单收敛到单一来源** `app/resources/licenses.py`。此前 `select.py` 与 `tools/assets/audit.py` 各写一份且不一致（audit 放行 MIT/Apache，选材判 -1），造成「图标入库了却永远选不中」。`audit.py` 按路径加载同一份常量（不破坏"只用标准库"的规矩），并加契约测试守住。明确不收录 `CC BY-NC`（pngimg 等）：非商业条款与用户可能把课件用于收费培训冲突。
  - **导出带图**，见 §3.8。
- M1 追加（美术资源升级第二批，即「不固定版式 + 模型自取素材」）：
  - **场景 DSL 取代版式渲染器**。`scene.py` / `scene.ts` 定九基元，模型直接产出图层与坐标；`archetype` 退为语义标签。兜底 `scene_fallback.py` 十原型共用四个构造器，只在模型没给场景时接管（§3.4.1）。
  - **模型真的在写场景**。`scene_art.py` 逐页调模型排版：校验 → 竖排框机械修正 → 硬伤检查（标题 / 素材 id / 密度 / 文字压字）→ 不过回灌一次 → 再不过退回兜底。记账落 `meta.scene_art`。`:lab/ppt` 有「模型排的版面」一栏，用 `scripts/smoke_scene_art.py` 的真实产出对版。
  - **工具循环取代关键词派活**。`agent_loop.py` 五工具 + 12 轮 / 90 秒 / 3 张 / 2 张预算，证据位与许可双闸写在代码里，结果冻结进 `meta`（§3.5.1）。
  - **十九条渠道成册**。`channels.py` 分 A–F 六类，`channels.json` 由 `python -m app.ppt.channels` 生成（§3.5.2、`assets.md` §5.6）。
  - **远程素材可用**。`materials.py` 四个 driver（wellcome / cleveland / iconify / phylopic）+ 分类页点名 + wsrv 代理；`assetSrc.ts` 统一地址解析，断图降级（§3.5.3）。
  - **生图接通**。`providers/wanx.py` 走 `qwen-image-2.1-pro`，产物落 `frontend/public/assets/generated/`——OSS 临时地址不落盘第二天全裂。
  - **LLM 层放开工具调用**。`chat_tools()` 返回整条 `message`（`tool_calls` 的 `id` 与后续 `tool_call_id` 必须对上），`replay.py` 增 `response_message` 列并自动迁移。
- M2 追加（校验之后定点修）：
  - **单页重写**（#5）。`ppt/revise.py`：只认容量 / 空页 / 引用越界三类问题，最多三页，改完只重排这几页（`paint_scenes(only=[...])`）。标题不可改（`PagePatch` 里没有这个字段），两次不过就退回原稿并记 `skipped` 原因。记账分 `meta.verify`（现在什么样）与 `meta.revise`（改过什么）两处。
  - **前端要说一声**。`deck_verify` 事件带 `revised[]`，`PptStudioView` 在校验行里写「已重写第 N 页（装不下 / 空页 / 引用越界），其余页未动」——正文变了却不明说，用户会以为是自己记错了。
- **截图基线（#2）**：`npm run baseline:ppt` 截 `/_lab/ppt` 的十版式（每页 `.frame`，不含外层仪表盘），与 `deliverables/ppt-baseline/` 逐张 pixelmatch，超 0.5% 像素差即 exit 1 并把 diff 图写到同级 `diff-*.png`。改版式后确认无误再 `npm run baseline:ppt:update`。**负向验证过**：故意换掉一张基线，脚本报「1 张与基线不一致」并退出 1；还原后 10 张全部 0.00%。
- 未落地：#1（基准页是先按参考图与装帧规格实现的，尚未走「手工调过评审」那一步）、#7（断点续跑只在步粒度，不在页粒度）。
