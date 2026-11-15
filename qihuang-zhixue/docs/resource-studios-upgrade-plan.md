# 三种资源工坊 · 索引与已落地记录

> 本文**不再是方案载体**。三个工坊的升级方案见 `docs/studios/`，此处只保留索引、已落地事实与被取代的旧结论，避免出现两份互相矛盾的计划。

---

## 1. 方案在哪

| 想了解 | 去哪里 |
|---|---|
| 三工坊共同的 8 步能力形状、两处闸门、分道表、验收矩阵 | [`studios/README.md`](./studios/README.md) |
| 幻灯：设计步、装帧系统、版式原型、逐页生成与校验 | [`studios/ppt.md`](./studios/ppt.md) |
| 思维导图：结构参与生成、分层生成、来源下钻 | [`studios/mindmap.md`](./studios/mindmap.md) |
| 文档：章节级写作、引文真校验、体裁进设计步 | [`studios/doc.md`](./studios/doc.md) |
| 素材：登记表、许可、采集、归一化、选型与降级 | [`studios/assets.md`](./studios/assets.md) |
| 幻灯的早期详细方案（`purpose`/`Block`/`Frame`/导出后端） | [`ppt-doubao-mode-plan.md`](./ppt-doubao-mode-plan.md)（部分结论已被取代，见 §4） |
| 能力契约的机器可读清单 | [`capabilities.md`](./capabilities.md)（由 `python -m app.capabilities.docs` 生成，勿手改） |

---

## 2. 已落地事实

以下内容已在代码中生效，不属于计划，改动时需同步更新本节。

### 2.1 方案契约与确认闸门

- **契约**：`backend/app/resources/planning.py` 的 `ResourcePlan` 由目标、受众、组织、呈现、`units[]`、`gaps`、`evidence`、`clarification`、`revision` 组成；每个 `PlanUnit` 带 `label`（一页/一节/一支）、`intent`、`detail`、`source`、`evidence`、`gap`。`source` 只取 `knowledge_base` / `user_material` / `model` 三档。
- **不是分类器**：`task_shape()` 只提供 temporal / comparison / planning / procedure 四类候选骨架作为提案提示，不把开放请求改写成固定模板。
- **闸门**：`backend/app/resources/service.py` 的 `plan` 步落库后推 `plan` 事件并暂停（`pause{reason:"await_plan"}`），未确认不进入生成步。确认接口对空方案、空标题、超上限（ppt 20 / doc 8 / mindmap 8）直接拒绝。
- **修订**：`revise()` 合并处理一轮反馈；`goal` 变更视为推翻重提，整份重拟；模型不可用时返回 `applied=False` + `notice`，方案一字不动。前端修订时把面板里手改过的那一版一并回传（`revise(feedback, goal, plan)`），服务端不从最初那版重算。
- **恢复**：`GET /api/resource/tasks/{task_id}` 返回快照；前端在 `?task=` 时优先还原待确认方案，已确认未跑完则续流，已完成则打开成品。旧任务（`pipeline_version < 2`）走原流程；PPT 旧大纲确认接口对 v2 任务返回 409，避免两条确认路径并存。

### 2.2 生成阶段消费确认方案（不静默重规划）

- **幻灯**：`_outline_from_plan()` + `strict_outline=True`（跳过 `MIN_SLIDES` 模板兜底，否则 3 页方案会被换成 10 页药材模板）；页数以方案为准（`normalize_deck` 的 `limit_override` 绕开左栏建议值）；模型漏页由 `align_deck_to_outline()` 按大纲补齐，大纲外的页丢弃。
- **导图**：`skeleton_from_plan()` 按方案分支重建一级骨架；命中不到教材依据的分支保留并标 `model`，不整枝删除。
- **文档**：`_plan_from_confirmed()` 把 `label→heading`、`intent→goal`。

### 2.3 依据可追溯与校验

- `_verify_unit()` 把"声称知识库却无引用"的单元降级为 `model` 并写 `gap`。
- `_settle_plan_deck()` 在方案无知识库依据时清空封面引用索引，封面不冒充教材。
- `_deck_verify()` 逐页给出 `source` 与 `issues`；`_doc_verify()` 把逐节档位写回标题块 `props.evidence` 并输出 `source_map`；`_plan_coverage()` 报方案分支落地率。
- 三者都只报问题、落 `meta.verify`，不改写正文。

### 2.4 测试与人工复核

- 方案面板与闸门的契约由 `frontend/tests/plan-negotiation.component.spec.mjs` 守护（三工坊的 `@revise/@confirm/@cancel`、确认后才生成、`?task=` 恢复、逐节/逐页/分支依据上屏）；后端对应 `backend/tests/test_resource_plan.py`。
- 人工复核脚本：`node frontend/scripts/verify-plan-flow.mjs [doc|ppt|mindmap]`，连真实后端与模型跑一遍「面板 → 手改 → 修订 → 刷新恢复 → 确认 → 成品 → 依据」，截图落 `.tmpverify/shots/`。
- 能力契约文档由 `python -m app.capabilities.docs` 生成，`docs.check()` 会校验不过期。

### 2.5 设计步与素材槽位（pipeline v3）

- **工序表**：`backend/app/resources/constants.py` 的 `ALCHEMY_STEPS_V3` 是唯一来源（三工坊各自的步），`steps_for / step_ids / gate_step` 供服务端与能力契约共用；`CURRENT_PIPELINE_VERSION = 3`，v1/v2 任务按版本回落，不迁移。前端不再有静态工序表，标签来自服务端下发的 `steps`。
- **设计步**：`ppt/design.py` 给十种版式原型（封面/导览/章节/要点/对照/矩阵/流程/数据/警示/收束）、七种内容块、三档密度与容量上限；`propose_design()` 是确定性提案（不调模型），`normalize_design()` 收口。确认接口 `POST /resource/tasks/{id}/plan/confirm` 收 `{plan, design}`，v3 时把设计单落 `steps["design"]`，续跑位置由 `gate_step` 算。
- **结构化内容**：`SlideNode` 增 `purpose` / `blocks` / `asset_refs`；`normalize_deck` 按版式把同一批 bullets 拆成不同块（对照→两栏矩阵、流程→steps、数据→数值卡、警示→alert），前端 `SlideSurface` 按 `purpose` + `blocks` 排版，旧稿按 `kind` 回落。
- **素材槽位**：`ppt/select.py` 四级阶梯（参数化 → 库内命中 ≥100 → 平替 ≥30 并标「示意」→ 生成 ≤3 或降级无图版式）；证据位**绝不**生成、**绝不**用母题顶替。槽位只写需求，`asset_id` 由选型器给，结果落 `meta.slot_resolution[]` 并在 `slot_resolution` 事件里推给前端。
- **母题**：12 个参数化母题由代码生成（`backend/app/ppt/motifs.py` 目录 → `frontend/src/render/motifs.catalog.ts` 生成物 → `frontend/src/render/motifs.ts` 渲染），随主题换色、零许可、零体积；纸面/角饰的「处方名 → 母题」映射在 `frontend/src/render/skin.ts`。
- **几何单一来源**：`frontend/src/render/frames.ts`（版心 8.4/9/7.2、字号七级、容量表）同时供线框 `WireframePreview`、成品 `SlideSurface`、导出 `pptxExport`；「页 → 版式 + 块」的换算在 `frontend/src/utils/slideBlocks.ts`，屏幕与导出读同一份。
- **验收页**：`/_lab/ppt`（仅 dev）一屏渲出十种版式，可切纸面与角饰对版。

### 2.5.1 不固定版式与模型自取素材（M1）

十种版式原型那一层已经跑通，但跑出来十二页里八九页一个样：模型只能从十个里挑一个，挑完几何是写死的。M1 把"信息结构"与"视觉方案"拆开，并把素材取用从代码派活改成工具循环。

- **场景 DSL**：`ppt/scene.py` / `render/scene.ts` 定九个基元（asset / motif / rect / rule / circle / text / seal / group）+ background，坐标单位 cqw（100 × 56.25），颜色只认角色名不认 hex。`archetype` 退为语义标签（校验与降级用），不参与绘制；渲染器不认识任何版式。模型没给场景时由 `scene_fallback.py` 铺安全版，十原型共用四个构造器。
- **模型真的在写场景**：`ppt/scene_art.py` 逐页调模型排版——`validate_scene` 校验 → 竖排框机械修正（宽 ≈ 字号 × 1.6，高 ≈ 字数 × 字号 × 1.15）→ 硬伤检查（标题必须在场 / 素材 id 必须在清单 / 元素 ≥ 4 / 文字不许互相压住）→ 不过回灌一次 → 再不过退回兜底。记账落 `deck.scene_stats` → `meta.scene_art`。真机实测三页全过，两处坑（竖排框语义相反、文字压字）已写进代码。
- **工具循环**：`ppt/agent_loop.py` 五工具（`search_assets` / `look_at` / `assign_asset` / `generate_image` / `finish`），上限 12 轮 / 90 秒 / 生图 3 张 / 单页 2 张。证据位只能用 `EVIDENCE_CHANNELS`、NC 许可只能进装饰位——拒绝写在代码里并把理由回灌，不靠提示词自觉。结果与 url / 许可 / 理由一并冻结进 `meta.slot_resolution` + `meta.art_frozen`。
- **渠道目录**：`ppt/channels.py` 十九条渠道分 A（本地）/ B（有检索 API）/ C（无检索站点，只能点名）/ D（生成）/ E（参数化）/ F（字体）六类，`ROUTES` 给用途优先序，`channels.json` 由 `python -m app.ppt.channels` 生成。
- **远程素材**：`ppt/materials.py` 四个 driver（wellcome / cleveland / iconify / phylopic）+ 分类页点名 + `_local_candidates`，**不落盘**，全部走 wsrv 代理（一次解决 CORS、尺寸、webp、缓存）。前端 `render/assetSrc.ts` 统一地址解析，断图整块不渲染、版式不塌。
- **生图**：`ppt/providers/wanx.py` 走 `qwen-image-2.1-pro`，产物落 `frontend/public/assets/generated/`。
- **LLM 层**：`clients.chat_tools()` 返回整条 message（`tool_calls` 的 id 必须原样回传），`replay.py` 增 `response_message` 列并自动迁移旧表。

### 2.6 文稿与导图的设计单

- 文稿 `DocDesign`（体裁 / 版式 / 引用体例 / 图表 / 目标字数）在 `design` 步产出，驱动 `docs/agent.py` 的篇幅（`target_words`）与 `_doc_verify` 的真引文计数（`bad_citations` 由清洗前的记账得出，不再恒为 0）。
- 导图 `MindmapDesign`（结构 / 主题 / 节点形 / 连线 / 折叠 / 分支预算 / 深度上限）在 `design` 步产出，经 `params` 进入生成；层数上限统一为 2–5。

---

## 3. 已确认但尚未落地

| 条目 | 分册 | 现状 |
|---|---|---|
| G0 关键歧义闸门（最多一问、附 3–4 候选、可"帮我选"） | `studios/README.md` §3.2 | 未落地；`clarification` 只在 revise 流程里有 |
| 素材库真实条目 | `studios/assets.md` | 已入库 48 条（5 条 CC0 饮片实拍 + 43 张 Heritage 复古插画），证据位可命中五味药；远程渠道可补齐其余（Wellcome 馆藏本草图，钉 URL），但许可禁止再配布的那几家只许引用不许落盘 |
| 文稿的 `select`（引文/图表素材槽位） | `studios/doc.md` §3.5 | 未落地；文稿目前无素材槽位 |
| 截图基线与导出保真 | `studios/README.md` §7 | 十版式 diff 基线已落地（`npm run baseline:ppt`）；导出保真仍只覆盖版式与图位，纸纹与角饰按设计不进 pptx |

---

## 4. 被取代的旧结论

| # | 旧结论 | 出处 | 现结论 |
|---|---|---|---|
| 1 | 美术资源分四层（排版 / 图形语法 / 实证图 / 点题图），四层都做 | 本文件旧版 §3 | 改**两层三槽**：设计语言层（参数化）+ 素材层（证据槽 / 装饰槽）。理由：把生成逻辑与外部资产并列，会让 `select` 无法用同一套机制处理 |
| 2 | 生图按**位置**限制（仅封面/扉页/尾页，≤3 张） | 本文件旧版 §3 | 按**槽位类别**限制：`decorative` 可生，`evidence` 绝不。位置相同不代表性质相同 |
| 3 | 实证图绝不生成（止于"不生成"，未定义缺失时怎么办） | 本文件旧版 §3 | 保留"绝不生成"，并补：证据槽缺失即空并进 `gaps`，不得用风格插图顶替 |
| 4 | 氛围照只用于封面与扉页 | 本文件旧版 §3 | 降级为条件启用：仅"实景/纪实"类主题，且必须过归一化算子；四套纸墨主题默认不用照片 |
| 5 | 母版库宁缺毋滥，没真设计就只用排版 | 本文件旧版 §3 | 母题不手工画，改**参数化生成器**（8–12 个函数）。占位卡的教训是"手工资产不可持续"，不是"素材不可用" |
| 6 | D1：不解禁位图（或只允许装饰性位图） | `ppt-doubao-mode-plan.md` §十五 | **取代**：证据位禁用生成图；装饰位可有限使用（≤3、标注、可删）。其余（`purpose` 十值、`Block` 七类型、`Frame` 契约、导出后端可插拔）**继续有效** |
| 7 | 7 步形状（understand / assess / plan / select / compose / check / deliver） | 本文件旧版 | 扩为 **8 步**，新增 `design`，且确认闸门覆盖"结构 + 设计" |
| 8 | 分期 U0–U5（以质量基线起步） | 本文件旧版 §6 | 改为 **S0–S5**（以契约统一起步，见 `studios/README.md` §7）。U0 的评测样例要求保留在验收矩阵里 |

---

## 5. 不再维护

原第 3、4、5 章（PPT / 导图 / 文档各自的升级内容与验收）已整体迁入 `studios/` 三份分册，此处删除以免出现第二份方案。旧第 6、7 章的分期与测试策略，可用部分已并入 `studios/README.md` 的分期与验收矩阵。
