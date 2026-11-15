# 文档工坊 · 升级计划

> 分册之一。共同部分见 `README.md`。本文的核心差别：**这个工坊的 "design" 是体裁与版式，"select" 的产物是块与引文，不是资产**。

---

## 1. 升级目标

1. **章节级写作**：长文可行（不受写死的字数上限约束），且「只重写某一节」可行。
2. **引文从「下标合法」升级到「该句确实被该引文支持」**：做不到全自动核对的部分，明确标为模型补充，而不是给个整数就宣称有据。
3. **体裁与篇幅进 `design` 步**：不再由关键词规则偷偷决定，用户可以看见并改。
4. **来源与缺口在成品上可见**：每节显示依据档位，点得开到具体引文。

---

## 2. 现状问题

### 2.1 生成粒度

1. **单次调用写整篇**。`_write` 只发一次请求（`backend/app/docs/agent.py:223`），返回整篇 Markdown，随后一次性转块（`:174`）。没有章节级调用、没有单节重试、没有失败隔离——某一节写坏，只能整篇重来。
2. **字数写死**。提示词两处都写「总长 1200-2500 字」（`backend/app/docs/prompts.py:55` 与 `:74`）。8000 字的长文没有路径，用户要"一份万字讲义"时系统只会给一份 2500 字的。
3. **章节数写死 4–8**（提示词 `prompts.py:30` 一轮 + `planning.py:48` 的 `MAX_UNITS["doc"]=8`），且无确认方案时套固定章节：`_HERB_SECTIONS`（`agent.py:70`）或 `_GENERAL_SECTIONS`（`:71`）。

### 2.2 引文与依据

4. **`bad_citations` 恒为 0**。`generate()` 在 `agent.py:177` 先做 `_sanitize_citations` 删掉越界标记，`_doc_verify`（`backend/app/resources/service.py:441-491`）再去统计时已无可统计。这个指标现在是装饰品。
5. **quote 块直接算引用**。`service.py:467-468`：出现一个 `quote` 块就给本节 `citations += 1`。贴一段引文就能把整节从 `model` 变成 `knowledge_base`，与正文是否引用毫无关系。
6. **档位语义漂移**。`props.evidence` 的真实含义是"这一节出现了编号或引文块"，不是"这一节的事实被支持"（`service.py:470-479` 计算并写回）。
7. **无模型兜底是空壳**。`_fallback_markdown`（`agent.py`）不带 `[n]`，所以整篇所有节都被判 `model`，来源统计等于全空。

### 2.3 交互与交付

8. **用户材料前端无入口**。后端支持 `planning.user_materials`（`MATERIAL_LIMIT=2000`、≤5 条，`planning.py:50`），`useDocStudio.ts` 与 `DocStudioView.vue` 里没有任何 `materials` 字段——用户带来的材料只能被丢弃。
9. **工序表错位**。前端 `DOC_STEPS` 是 v1 五步 `source/relate/distill/verify/layout`（`frontend/src/config/docStudio.ts:16-22`），后端四步 `plan/compose/verify/seal`（`constants.py:88-93`）。第 5 格「排版成卷」永远不会亮。
10. **导出只有 Markdown**。`GET /api/resources/{id}/doc/export`（`backend/app/api/resources.py:288`）只输出 `.md`，没有 docx。
11. **块类型缺四类**。现有 16 类（`backend/app/resources/doc_schema.py:14-21`：paragraph / heading1-3 / bulleted / numbered / todo / toggle / quote / callout / divider / code / table / image / columns / page）里没有公式、脚注、图注、参考文献。

---

## 3. 结构调整方向

### 3.1 `design` 步：体裁、版式、引用体例、图表策略

文档的 design 由四项构成，全部进确认单：

| 项 | 取值 | 影响 |
|---|---|---|
| 体裁 | 讲解 / 对比 / 操作指南 / 学习计划 / 综述 / 案例 | 结构与语言风格；替代关键词规则 |
| 版式 | 讲义 / 报告 / 手册 / 纪要 | 标题层级、是否带摘要、是否分栏、段落密度 |
| 引用体例 | 脚注式（正文 `[n]` + 文末来源表）/ 尾注式 / 无编号（纯讲述） | 影响 `select` 的引文装配与 check |
| 图表策略 | 允许表格 / 允许流程图 / 仅文字 | 影响块类型选择 |

配套两条：

- **字数目标可配**：`design.target_words`（默认按体裁给区间，例如讲解 1500–3000、讲义 4000–8000、报告 2000–5000），替代写死的 1200–2500。上限用于 `compose` 的容量提示，下限用于 check。
- **体裁不再由关键词猜**：`classify_kind`（`agent.py:47-89`）降级为 `design` 的**初始建议值**，用户可改；改了就按改的走。

### 3.2 `select` 步：块类型 + 引文片段 + 图表

- **块类型**：按版式与图表策略从 20 类里选（现有 16 + 新增 4）。
- **引文片段**：`build_source_pool`（`agent.py:122-144`）产出池子（上限 `MAX_SOURCES=12`，单条 `PROMPT_QUOTE_LIMIT=300`），按节分配子集——而不是全篇共用一个大池。
- **图表**：从 `table / flow / figure-caption` 三选，全部由结构数据生成（表格来自对照块，流程图来自步骤块），不引位图。

### 3.3 章节级写作

```
for section in confirmed_plan.units:
    compose(section)  # 输入 = 全篇主线 + 本节目标 + 前后节标题 + 本节引文子集 + 字数预算
    单节 check → 不通过定向重写一次 → 仍不通过：保留占位并回报，不重写全文
```

- 全篇主线（标题 + 目标 + 组织方式）每节都带，避免各节自说自话。
- 前后节标题用于衔接与去重。
- 单节失败写入 `sections[].issues`，其余节照常完成；成品允许"某一节待补"，但必须在回执里说清是哪一节、为什么。

### 3.4 引文校验做真

四项改动，缺一不可：

1. **先记账后清洗**：`_sanitize_citations` 改为返回 `(blocks, removed[])`，把被删的越界/非数字标记记下来；`_doc_verify` 用清洗前的统计，`bad_citations` 才是真数。
2. **quote 不再自动算引用**：`quote` 只证明"这里有引文"，不证明"正文被它支持"。要计入 `knowledge_base`，必须本节正文出现有效 `[n]`。
3. **空引用即降级**：本节声明 `knowledge_base` 但正文无 `[n]`、也无与池子匹配的引文块 → 判 `model` 并在 `source_map` 写明原因。
4. **段落级出处**：`[n]` 必须指向该段可用池子里的条目（现有的池子分配已能支持），跨段引用记一条 `loose_citation`。

**诚实边界**：规则能验证"索引在池内、该节确实引用了池内条目"，**不能验证"这句话被那段原文支持"**。后者标为「待人工核对」并计数，不用"校验通过"掩盖。

### 3.5 块类型补四类

新增 `formula`（公式）、`footnote`（脚注）、`figure`（图注，含来源字段）、`reference`（参考文献条目）。四类都要在 `doc_schema` 的 `DocBlockType`、`markdown_blocks` 的转换、`doc_markdown` 的往返里同时出现，否则保存即丢。

### 3.6 用户材料入口与 docx 导出

- 左栏加「材料」区：粘贴文本或上传纯文本，进 `materials`（≤5 条、单条 ≤2000 字），作为 `user_material` 档独立计。
- docx 导出：块模型已足够，`reference` 块生成文末来源表；导出保真要求"块 ↔ docx ↔ 块"往返不丢结构类型。

---

## 4. 可检查的交付物

| # | 交付物 | 判定 |
|---|---|---|
| 1 | 章节对应率 | 方案 `section` ↔ 成品 `heading`，`{planned, matched, missing}` |
| 2 | `bad_citations` 真实计数 | 人为注入 3 条越界引用，计数为 3（现状必为 0） |
| 3 | 逐节 `props.evidence` 与 `source_map` | 每节档位有据可查；无 `[n]` 的节不得标 `knowledge_base` |
| 4 | 章节字数与目标偏差 | 每节字数 / 预算，偏差 >40% 记一条 |
| 5 | 无效引文清单 | 越界、非数字、跨段引用分三类列出 |
| 6 | 单节重写隔离 | 人为让第 3 节失败：其余节完整、成品可用、`issues` 指明第 3 节 |
| 7 | 长文可行 | 8000 字目标能生成（不再被 2500 字上限截断） |
| 8 | 用户材料进档位 | 粘贴一段材料后，相关节的 `evidence` 出现 `user_material` |
| 9 | 块往返 | `docx → 块 → docx` 后 20 类块类型不丢（含新增 4 类） |
| 10 | 工序表同源 | 前端不再有 `DOC_STEPS` 静态表；第 5 格「排版成卷」不再出现 |
