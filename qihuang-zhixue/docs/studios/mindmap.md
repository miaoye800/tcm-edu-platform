# 思维导图工坊 · 升级计划

> 分册之一。共同部分见 `README.md`。本文的核心差别：**这个工坊不消费任何位图素材**，它的 "design" 是结构与视觉语法。

---

## 1. 升级目标

1. **结构参与生成**：用户选「四面发散」，不只是换个画法——分支数量、每支深度、左右分配、叶子密度都随之变化。结构不再是渲染属性。
2. **单支失败不损坏整棵树**：分层生成、逐层落库，某支失败只重试该支。
3. **来源从「整条素材」下钻到「具体字段 / 边」**：节点点得开，看得到 `name / type / ref / quote`。
4. **视觉语法可确认**：节点形状、连线、层级色、折叠策略进 `design` 步，与结构一起确认。

---

## 2. 现状问题

### 2.1 结构是"画法"，不是"内容"

1. **`structure` 被明确声明为视图属性**。`backend/app/mindmap/appearance.py:1-5` 原文：「两者都是**视图属性**，不参与内容生成，也不影响树的形状」。落库时只写进 `meta.mindmap_structure`（`backend/app/resources/service.py:946`）；生成侧只拿到问法推断出的 `intent.structure_hint`（`backend/app/mindmap/agent.py:89`），**没有任何代码读 `params["structure"]`**。
2. **左右分配是机械交替**。`archetypes.py:386`、`plan_tree.py:45`/`:53` 统一写 `direction = index % 2`。前端确实把这个值带进了渲染数据（`frontend/src/utils/mindmapTree.ts:150`），但分配规则与分支语义无关——"病因在左、症状在右"这类意图表达不了，因为 `index` 只是遍历序号。
3. **结构适配只到第一层**。`skeleton_from_plan`（`backend/app/mindmap/plan_tree.py:27`）用确认方案重建一级分支，但叶子仍由 archetype 提供（`_leaves(...)`）。用户把第二节改成"重点讲配伍"，二级以下的内容不会跟着变。
4. **结构折叠不可预知**。折叠在渲染层按 `maxDepth` 执行（`mindmapTree.ts:139`），而生成侧并不知道最终用哪个结构、会被折到第几层。结果是"生成时 5 层、看到 3 层"，且无人报错。

### 2.2 生成与交互

5. **一次调用出全树**。`agent.py:154` 单次 `structured_create` 返回整棵 `MindmapDraft`，60–120 秒静默；`expand` 分支扩展是确定性拼字段（`agent.py:93`），模型不参与，所以"展开这一支"得到的其实是同一批标签的重排。
6. **前端不展示来源**。`components/mindmap/StudioCanvas.vue:302` 只把 `source_indexes` 存进节点 meta，节点点不开出处；`sources[]` 已经随资源落库，却没有消费方。
7. **`_concept` 硬编码中医联想**。`archetypes.py:355-361` 强制生成"与中药学的联系"一支，与提示词里「不要硬扯中医」（`prompts.py:70-75`）自相矛盾。

### 2.3 契约一致性

8. **层数上限三处不一致**：能力契约 2–5（`backend/app/capabilities/handlers/mindmap.py:47`）、入参校验 2–4（`backend/app/mindmap/params.py:14-15`）、归一允许 2–5（`backend/app/mindmap/draft.py:33-35`）。实际生效的是最严的那个：**请求 5 层会被 422 拒**。
9. **工序表四份口径**：后端 10 步含 `shape`（`backend/app/resources/constants.py:82-87`）、能力契约 9 步无 `plan`（`handlers/mindmap.py:83-93`）、前端兜底 9 步把 `plan` 用作"谋篇立骨"（`frontend/src/config/mindmapStudio.ts:29-39`）、文档前端 5 步。契约里没有 `plan` 步与 `pause` 事件，对"读契约编程"的调用方而言，用户确认闸门是隐形的。
10. **生成期没有依据兜底**。节点可以声明 `textbook` 而 `source_indexes=[]`。保存期有意不重判（`tree_schema.py:127-137` 注释说明了理由：手改过的节点仍带着原来的出身，不该因为改几个字就被判成推断），所以这个缺口只能在**生成期**补，而现在生成期没有这一道判断。

---

## 3. 结构调整方向

### 3.1 `design` 步：结构 + 视觉语法（明确不含插图）

导图的 design 由四项构成，全部进确认单：

| 项 | 取值 | 对生成的影响 |
|---|---|---|
| 结构 | `logic-right` / `mind-both` / `org-down` / `fishbone` | 分支预算、深度分配、`direction` 语义 |
| 主题 | `indigo` / `verdant` / `violet` / `inkCream` | 仅视觉，不影响内容 |
| 视觉语法 | 节点形状（药牌 / 纯文字 / 白底细框）、连线（直线 / 曲线 / 括号）、层级色（4 档） | 仅视觉 |
| 折叠策略 | `自动折叠`（按结构 maxDepth）/ `展开到 3 层` / `全部展开` | 决定 `shape` 步的裁剪上限 |

**为什么不做位图**：写进文档，避免以后反复讨论。

1. 导图的信息密度靠层级与连线，插图会抢层级，把"结构"退化成"配图"。
2. 素材管线（`assets.md`）服务的是"证据位 / 点题位"，导图两者都没有——节点上的图只能是装饰，而装饰在导图里等于噪声。
3. 一旦允许配图，check 要新增"图文语义一致"判断，而规则判断不了，只能靠人看，等于给自动生成开了个不可验的口子。

### 3.2 结构真正参与生成：`shape` 步的三项输入

把结构翻译成三个可执行参数，写进 `shape` 步（而不是 `render` 步）：

1. **分支预算**：`mind-both` 一级 4–8 支且强制 ≥2 左 ≥2 右；`org-down` 一级 3–6 支、层级 ≥3；`fishbone` 一级 3–5 支（主骨）、每支 ≤2 层；`logic-right` 一级 3–8 支、深度可到 5。
2. **深度上限**：按结构的 maxDepth 裁层，超过即不生成（而不是生成了再折）。生成了再折的代价是"用户数到 5 层，屏幕上只有 3 层"。
3. **`direction` 语义化**：由 `subject` 语义分配，而不是 `index % 2`。规则示例：对比类 `mind-both` 按"甲/乙"分左右；因果类 `fishbone` 按"因/果"分上下；`org-down` 不分配 `direction`（纵向结构里它无意义）。

验收口径：`mindmap_verify` 新增 `structure_fold`（被折叠节点数）与 `branch_budget_used`（实际/预算）。折叠数 >0 时必须在回执里明示，不允许静默。

### 3.3 `select` 步：骨架 + 主题 + 图标词表

- **骨架**：一级来自确认方案（`skeleton_from_plan`），叶子来自 archetype 的槽位定义。本轮把"叶子也来自方案"补上——方案单元里的 `detail` 要能落到二级节点。
- **主题**：从 `design` 取，`appearance.normalize_theme` 归一。
- **图标词表**：允许一组**内置**的单色图标语义位（如"注意 / 禁忌 / 鉴别"），不引外部图标集，不引位图。理由是 Element Plus 图标风格与纸墨语言不符（`@element-plus/icons-vue` 是当前唯一可用图标集），而自建图标要过设计评审，本轮不做。

### 3.4 分层生成与单支重试

```
shape    → 主干（一级分支 + 每支预算）
compose  → 第 1 层：逐支生成二级
         → 第 2 层：只有超预算的支才继续
         每层落库，单支失败只重试该支（≤2 次）
         → 全部完成后合并成一棵树
```

关键约束：

- 每次落库的中间态可读（`task.steps_done["branches"]`），断线可续。
- 单支重试不重跑整棵；重试次数与原因写入 `verify.retries[]`。
- 合并时做一次全局去重与父子语义检查（同级不得出现父子同义反复）。

### 3.5 来源下钻与诚实降级

- 节点点击 → 侧栏显示该节点 `source_indexes` 对应的 `sources[i]`：`name / type / ref / quote`（字段已在 `resource_context.source_refs` 里齐全）。
- 导出（SVG / PNG）时：`model` 档节点带「推断」角标（已有机制，`mindmapTree.ts:154`）；导出附"来源表"或在页脚标注角标含义。
- **降级规则**：在**生成期**对新节点判一次——声明 `textbook|graph` 但 `source_indexes=[]` → 直接生成为 `model`，并记一条 `downgraded[]`。保存期不重判（现有注释的理由成立：用户手改不应改变内容出身）；但用户在前端清空某节点的引用时，界面要提示"档位可能失真"，把判断交回给人。

### 3.6 工序表与契约的单一事实源

- 能力契约的 `pipeline` 改为从 `resources/constants.steps_for("mindmap", 2)` 派生，不再手写第二份。
- 契约 `events` 补 `plan` 与 `pause`（确认闸门是一等公民，调用方必须能从契约知道"要二次确认"）。
- 层数上限统一为 2–5（取最宽的，因为结构支持 5 层；把 `params.MAX_DEPTH` 从 4 改到 5，或让能力契约声明 2–4 并同步三个渲染侧——二选一，但只能有一个数字）。

---

## 4. 可检查的交付物

| # | 交付物 | 判定 |
|---|---|---|
| 1 | 结构影响生成 | 同一主题分别用 `mind-both` 与 `org-down` 生成，一级分支数与深度分配不同；差异来自 `shape` 参数而非渲染 |
| 2 | 分支覆盖率 `plan_coverage` | `{planned, matched, missing}`，缺失支逐条列出 |
| 3 | 三档计数守恒 | `textbook + graph + model == 节点总数`（已有口径，补断言） |
| 4 | 结构折叠数 | `structure_fold = 0` 或回执中明示折叠位置 |
| 5 | 单支重试隔离 | 人为让某支失败：其余支完整、整树可用、`retries[]` 有记录 |
| 6 | 节点来源可点开 | 每个带 `source_indexes` 的节点能展开 `name/type/ref/quote` |
| 7 | 降级可查 | 生成期声明 `textbook` 却无出处的节点被直接生成为 `model`，并出现在 `downgraded[]` |
| 8 | 工序表同源 | 契约 `pipeline`、前端标签、SSE `step_label` 三者逐字一致（一条契约测试） |
| 9 | 层数上限唯一 | 请求 5 层在三个入口都通过；请求 6 层在三个入口都 422 |
