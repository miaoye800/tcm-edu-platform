# M1 知识图谱消费化 · 验收报告

> 计划文件：`quantum-cascade-curie-sBJoS4di.md`（P0 / S1 / S2 / S3 / S4 / S5 / S6）
> 验收日期：2026-11-05　被测库：`backend/.data/qihuang.db`（实体 1708 / 边 5512）

## 一、结论

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | `detect_seeds()` 公开 + `fuzzy=False` 严格模式 | ✅ |
| S1 | 契约层 `contract.py`：6 个能力 + `plan()` 确定性路由 | ✅ |
| S2 | 模型投影 `projection.py`：1200 token 预算 + 七档降级 | ✅ |
| S3 | 100 条判据表 + 反过拟合四法 | ✅ 指标全达标 |
| S4 | 接主链路 `rag_agent` / `explain_agent` / 意图路由 | ✅ |
| S6 | 前端「未显示 N 条」+ 后端透出截断数 | ✅ |
| S5 | 验收：测试全绿 + 5 问句端到端 | ✅ |
| S5 | 盲测（模型只看说明书选能力） | ✅ 20/20（`docs/kg-m1-blindtest.md`） |

**测试**：`pytest backend/tests` → **783 passed, 1 xfailed**；`npm test`（前端 18 个契约 spec）全绿。

## 二、判据表指标（S3）

| 指标 | 口径 | 实测 | 上限 |
|---|---|---|---|
| 漏调 | 开发集 64 条该调却判 None | **0（0.0%）** | 10% |
| 误调 | 反向 20 条不该调却调了 | **1（5.0%）** | 8% |
| 选错 | 开发集调了但选错能力 | **0（0.0%）** | 12% |
| holdout | 16 条正向，单独报数 | **0 漏 / 0 错** | 不设闸 |

唯一一条误调是 `人参和藏红花能一起用吗`：「藏红花」不在库，实体识别把它当成「红花」的原文提及，于是凑出第二味药。已在 `test_kg_judgement.py` 用 `xfail(strict=True)` 钉住——将来修好会让测试变红，逼着把标记删掉。

## 三、5 个真实问句端到端（S5）

`python scripts/eval_kg_contract.py` → **5/5 通过**。

| # | 问句 | 能力 | 结果 |
|---|---|---|---|
| 1 | 麻黄和桂枝为什么常一起用 | ask | 相须边 22160 / p149；投影后 5 条关系，773 token |
| 2 | 麻黄和桂枝有什么区别 | compare | 教材鉴别边 24158；差异 7 项 |
| 3 | 人参和藜芦能一起用吗 | ask | 十八反边 22132 / p128 / tier=1；3 条关系，627 token |
| 4 | 麻黄能治哪些证 | ask（单实体） | 药证链 ≥3 条 |
| 5 | 处方「人参9g，藜芦3g」有没有问题 | review | verdict=block，checks.compatible=fail |

## 四、主链路实际输出（节选）

问「人参和藜芦有什么区别」，答案末尾：

```
教材依据：
- 禁忌：十八反（教材 128：藜芦反人参、西洋参、党参、丹参、玄参……）
- 四气：人参 微温；藜芦 寒
- 功效：人参 大补元气、复脉固脱、补脾益肺等；藜芦 涌吐风痰、杀虫
```

三件事和以前不一样：

1. 有**页码和原文**，不是模型自己组织的一段话；
2. **禁忌排第一行**——先讲四气五味，用户可能已经去抓药了；
3. 两味药**都点到**（以前只念 `docs[0]`，第二味药在答案里彻底消失）。

## 五、改动清单

**新增**

- `backend/app/kg/contract.py` — 6 能力声明 + `plan()` + 9 个 `normalize_*`
- `backend/app/kg/projection.py` — 模型投影（ask/path/compare/reverse，review 不投）
- `backend/tests/kg_judgement_set.json` — 100 条判据（80 正 / 20 反）
- `backend/tests/test_kg_judgement.py` — 21 条
- `backend/tests/test_agents_kg_bridge.py` — 21 条
- `backend/scripts/eval_kg_contract.py` — 5 问句验收
- `backend/scripts/run_kg_blindtest.py` — 盲测提示词与评分表

**修改**

- `app/kg/store.py` — 新增 `get_entities()` 批量取
- `app/kg/traverse.py` — `Subgraph.total_nodes` / `hidden_nodes`；邻居批量取名
- `app/kg/compare.py` — `_shared_relations` 一次查全名字
- `app/kg/ask.py` — 合并子图时聚合 `total_nodes`
- `app/agents/graph.py` — `_kg_expand` 接线 + 关系卡渲染 + 答案长度治理
- `app/llm/router.py` — 「图谱」不再无条件算资源生成；知识问答补硬关系词
- `app/config.py` — 4 个 `kg_agent_*` 配置
- `app/api/kg.py` — meta 透出 `truncated` / `total_nodes`
- `frontend/src/{types/graph.ts,stores/graph.ts,components/graph/GraphView.vue}` — 「未显示 N 条」
- `frontend/tests/kg-graph-p7-p8.component.spec.mjs` — 修弱断言（原来命中的是注释）

## 六、过程中发现并修掉的真 bug

| 问题 | 后果 | 修法 |
|---|---|---|
| 能力常量 `_LOOKUP/_PATH/_REVIEW` 与路由词表同名互相覆盖 | `plan()` 直接 `TypeError` | 能力常量统一 `_CAP_*` 前缀 |
| 归一化把 `quote` 改名 `text_span`，投影只认 `quote` | 接主链路后 **T1 禁忌与教材鉴别句被静默丢光**，不报错 | 投影层 `_span_of()` 三种形状都吃 |
| `normalize_compare` 丢 `edge_id` | 教材鉴别「有原文但点不进去」 | 契约层保留 `edge_id` |
| `get_entity` 每条边新开连接，`close()` 19ms/次 | ask 1.8s、compare 5.6s，全都超过 2s 超时 → 图谱等于不可用 | `get_entities()` 批量取；ask→0.21s、compare→0.32s |
| 答案截断按「模型草稿 + 结构化内容」顺序切 | 长回答被腰斩时，第一个掉的是**教材依据** | 结构化内容先占版面，草稿只吃剩余额度 |
| `router.classify` 把「图谱」算作资源生成 | 「说说图谱里麻黄和桂枝为什么配」被拐去做图 | 带生成动词才算资源需求 |

## 七、已知缺陷与本次不做

1. **子串误命中**：`藏红花`→`红花`（见 §2），影响「缺实体降级」判定的 1/20。
2. **不做诱导边**：外围点之间的边在 40 节点时最多 780 条，会糊成毛球，且 T1 的 80 条会被 T2 淹掉。
3. **不做「首屏只开 T1」**：实测麻黄 T1 边数 = **0**，默认只开 T1 会让首屏空图并触发「教材未收录」的假结论。默认仍是 `[1,2]`。
4. **盲测不进 CI**：`python scripts/run_kg_blindtest.py --live` 会消耗 API 额度，
   已人工跑过一次（20/20，见 `docs/kg-m1-blindtest.md`），但不设自动闸门——无网络
   环境不可复现。模型或说明书改动后需要重跑。

## 八、复跑方式

```bash
cd backend
.venv/Scripts/python.exe -m pytest tests/ -q          # 783 passed, 1 xfailed
.venv/Scripts/python.exe scripts/eval_kg_contract.py  # 5/5 通过
.venv/Scripts/python.exe scripts/run_kg_blindtest.py --live  # 盲测，会写 docs/kg-m1-blindtest.md
cd ../frontend && npm test                            # 18 个契约 spec
```

## 九、交付后复核（2026-11-05 二次审核）

按计划逐条核对交付物，发现 4 处「计划里有、代码里没有」的缺口，已补齐：

| # | 出处 | 缺口 | 处理 |
|---|---|---|---|
| 1 | 计划 §9.3 | 说了「默认不只看 T1，但要给一个『只看禁忌』预设按钮」，按钮没做 | `GraphView.vue` 增加预设按钮 + `constraintOnly` 状态，再点一次退回两层；spec 补断言防回退 |
| 2 | 计划 §7.5 第四法 | 反过拟合四法只落了三条，「每个触发词至少出现 3 次」缺失 | `test_triggers_are_not_one_off_words`：数 `plan()` 实际用某词接住几道题，少于 3 条即失败 |
| 3 | 计划 §十二.3 | 「119 条 page=None 允许无页码但 ref 必须非空」只有口头承诺，无断言 | `test_missing_page_is_not_missing_source`：扫真库 119 条证方链证据，断言 `page is None` 且 `ref` 非空 |
| 4 | 计划 §10 盲测 | 脚本已写但从未运行，`docs/kg-m1-blindtest.md` 不存在 | 实跑 `deepseek-chat`，**20/20**，报告已落盘 |

另外修了一个交付时就存在的问题（计划未要求，但直接影响答案质量）：

**关系排序按关系名码点排 —— ask 的第一行永远是「同类鉴别」**。用户问「麻黄和桂枝为什么常一起用」，
正文第一条却是鉴别句；问「麻黄能治哪些证」，第一条不是药证链。原因是 `_rank` 只用
`relation_type` 字符串比较，「同」(U+540C) 恰好排在「相」(U+76F8)、「药」(U+836F) 之前。

改法：`contract.relation_focus(question)` 给出问句焦点（配伍 / 方剂组成 / 主治），
投影层按「焦点优先 → 默认表（禁忌 > 配伍 > 组成 > 药证 > 鉴别）」排序。
实测三个问句的首条依次变为「相须」「药证链」「组成」，`test_relation_order_follows_question_focus` 锁住。
