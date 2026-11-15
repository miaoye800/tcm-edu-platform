# AI 服务助手 · 总控对话 Agent 实施计划

> 读者：实现者与评审者。
> 定位：这是**编排层**的方案，不重写工坊。工坊怎么炼，见 `docs/studios/`；能力怎么声明，见 `docs/capabilities.md`。
> 本文只回答三个问题：用户一句话进来，**谁来判断**、**判断完调什么**、**结果怎么回到对话里**。

---

## 1. 一句话结论

把「选能力」这件事从散装 REST 判断，收敛为**一张能力注册表 → 一份工具清单 → 一个有界工具循环**；对话只负责三件事：听懂、派活、把产物卡片放回消息流。工坊、图谱、画像全部以能力形式接入，新增能力=注册一次，不改 Agent。

前端不是「再造一个聊天框」，而是**对话 + 方案卡 + 产物卡**三段式：聊得清、改得动、打开得了。

---

## 2. 现状盘点（事实，不是计划）

### 2.1 已经就绪的

| 层 | 现状 | 位置 |
|---|---|---|
| 能力契约 | 13 条能力，`kind` 分 job/query/command，统一 `dispatch.invoke`、幂等受理、错误码、artifact 模板 | `backend/app/capabilities/` |
| 工具 schema | `registry.tool_schemas()` 已能导出 OpenAI 形状的 tools；`use_when`/`avoid_when` 都带真实问句 | `capabilities/spec.py:136` |
| 模型工具调用 | `ModelRouter.chat_tools()` → `ToolTurn`，带重试、用量计量、replay、失败返回空轮次 | `app/llm/router.py:189` |
| 有界工具循环范式 | 素材循环：12 轮 / 90 秒 / 预算 3 张 / 单轮异常转 `{ok:false}` 回给模型 | `app/ppt/agent_loop.py` |
| 生成任务流 | `POST /api/resource/tasks` 建任务；`GET /api/resource/tasks/{id}/stream` 推工序、`plan`、`pause`、`done` | `app/api/resources.py` |
| 确认闸门 | `plan/revise`、`plan/confirm` 收 `{plan, design}`，v3 任务从 `gate_step` 续跑 | `resources/service.py` |
| 前端流客户端 | `streamResourceTask()`：fetch + Bearer + SSE 解析 + 断流判定 | `services/resourceApi.ts:61` |
| 对话状态机雏形 | `useMindmapChat` 只做「一句话 → 一次调用」，注释里写明未来要接到通用 Agent | `composables/useMindmapChat.ts` |

**这张表的意义**：不需要新造编排基础设施。要做的是把已有的三块（注册表、工具循环、任务流）焊在一起，并补上会话层。

### 2.2 缺口（按会绊倒实现的顺序）

| # | 缺口 | 后果 | 处理 |
|---|---|---|---|
| G1 | `doc.generate` 不存在，但 `ppt.py` 的 `avoid_when` 已经在引用它 | Agent 被自己的工具描述指向一个 404 | P0 补契约，或先把描述改掉 |
| G2 | `resource.generate` 是 501 占位，`quiz` 只有散装 `/api/quiz/generate` | 题库/试卷无法作为工具被调用 | P1 补 `quiz.generate` |
| G3 | `capability-invocations/{id}` 把 artifact 写死成 `kind="mindmap"`、`/mindmap?resource=` | 幻灯任务的轮询回包指向导图页 | P0 改为从 `spec.artifact` 派生 |
| G4 | `tool_schema()` 把 `mindmap.generate` 变成 `mindmap_generate`，但没有反向映射 | Agent 收到工具名后无法可靠还原能力名 | P0 建显式映射表，**禁止字符串反解** |
| G5 | `CapabilityInput` 是 `extra="ignore"` | 模型把 `audience` 传给导图会被静默吞掉，用户以为生效 | P0 适配器做白名单校验，把未知字段回给模型 |
| G6 | 没有会话表、没有消息表、没有 run 表 | 刷新即失忆，无法多轮 | P0 建三张表 |
| G7 | `/assistant` 路由是 `redirect: '/dashboard'`，菜单项只改 `stores/assistant.ts` 的布尔值 | 点进去没有页面 | P1 建真实页面 |

---

## 3. 总体架构

```
┌──────────────────────────────────────────────────────────────┐
│ 前端 /assistant                                               │
│  左：会话列表   中：消息流（文本/工具/方案卡/产物卡）  右：本次产出 │
└───────────────────────────┬──────────────────────────────────┘
                            │ POST /api/assistant/chat/stream (SSE)
┌───────────────────────────▼──────────────────────────────────┐
│ 编排层 app/assistant/                                          │
│  router.py   规则兜底（无 key / 超轮次 / 模型没给 tool_call）      │
│  loop.py     有界循环：8 轮 / 60 秒 / 每轮 ≤2 生成 / 结果冻结     │
│  tools.py    工具面：注册表 → tools[]，tool_call → dispatch      │
│  bridge.py   任务桥：202 → 订阅资源流 → 工序进度 / 方案卡 / 产物卡  │
│  store.py    conversation / message / run / artifact           │
└───────────────────────────┬──────────────────────────────────┘
                            │ dispatch.invoke(name, body, actor)
┌───────────────────────────▼──────────────────────────────────┐
│ 能力层 app/capabilities/（已存在，唯一事实源）                   │
│  生成 job：mindmap.generate / ppt.generate / doc.generate …     │
│  查询 query：kg.ask / kg.compare / kg.path / kg.review …        │
│  画像 query：profile.view（新增）                               │
└──────────────────────────────────────────────────────────────┘
```

### 3.1 三条不可让渡的边界

1. **Agent 不直连数据库、不直连服务函数**。所有对外动作必须经过 `dispatch.invoke`。理由：能力层的幂等、错误码、artifact 语义都在 dispatch 里；绕过一次，那套约定就废了。
2. **导航不由模型决定**。模型只在最终回答里返回 `ui_actions`，前端渲染成按钮。理由：让模型调 `navigate` 只会多一次往返，且它没有「用户在哪个页面」的信息。
3. **生成任务不等在工具循环里**。工具调用只拿 202 + `task_id` 就返回，进度由 `bridge.py` 旁路推进。理由：一次生成 10~25 秒，等在循环里会吃掉所有轮次预算。

---

## 4. 后端设计

### 4.1 目录与职责

```
backend/app/assistant/
├── __init__.py
├── contract.py   # 会话/消息/run 的 pydantic 模型与状态机枚举
├── store.py      # sqlite 三张表 + 查询；复用 app.profile.store.DB_PATH 与 WAL 约定
├── tools.py      # tool_schemas() 组装 + tool_name ↔ capability 显式映射 + 白名单校验
├── loop.py       # 有界工具循环（仿 ppt/agent_loop.py）
├── router.py     # 规则兜底路由
├── bridge.py     # 生成任务桥：受理 → 订阅 → 事件翻译
├── events.py     # SSE 事件信封与序列化
└── api.py        # 路由：会话 CRUD + chat/stream + run 快照/取消
```

`api.py` 只做 HTTP 与鉴权，循环逻辑全在 `loop.py`——与 `ppt/agent_loop.py` 的分层保持一致，便于在没有 FastAPI 的环境里测循环。

### 4.2 数据模型

沿用现有 sqlite（`DB_PATH`），不引入新存储。三张表 + 一张产物表：

```sql
CREATE TABLE IF NOT EXISTS assistant_conversation (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'active',   -- active / archived
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assistant_conversation_user
    ON assistant_conversation(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS assistant_message (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    run_id          TEXT,
    role            TEXT NOT NULL,                  -- user / assistant / tool
    content_json    TEXT NOT NULL DEFAULT '{}',     -- 结构化消息（见 5.3）
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assistant_message_conv
    ON assistant_message(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS assistant_run (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    user_message_id TEXT NOT NULL,
    status          TEXT NOT NULL,                  -- running / awaiting_plan / awaiting_task / done / failed / cancelled
    steps_json      TEXT NOT NULL DEFAULT '[]',     -- 工具调用轨迹，排查与前端回放共用
    usage_json      TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    finished_at     TEXT
);

CREATE TABLE IF NOT EXISTS assistant_artifact (
    id           TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    call_id      TEXT NOT NULL,
    capability   TEXT NOT NULL,
    task_id      TEXT NOT NULL DEFAULT '',
    resource_id  TEXT NOT NULL DEFAULT '',
    kind         TEXT NOT NULL,
    open_url     TEXT NOT NULL DEFAULT '',
    state        TEXT NOT NULL DEFAULT 'accepted',  -- accepted / awaiting_plan / done / failed
    created_at   TEXT NOT NULL
);
```

**为什么单独建 `assistant_artifact` 而不塞进消息 JSON**：右栏「本次产出」要按会话聚合、要能在产物完成后回填 `resource_id`，塞进消息 JSON 就得整行读出再改，并发下必丢写。

**为什么 `steps_json` 存整条轨迹**：断线重连时要能把「我调了什么、现在跑到哪」原样还原，重跑一遍循环既贵又会生成第二份产物。

### 4.3 工具面

#### 4.3.1 工具的来源

`tools.list_for(agent="assistant")` 从注册表读，不手写清单：

```python
# 生成类（job）：返回 202，进度旁路
mindmap_generate  ppt_generate  doc_generate  quiz_generate
# 查询类（query）：同步返回，进上下文
kg_ask  kg_compare  kg_lookup  kg_path  kg_review  kg_attribute
kb_search  resource_list  resource_get
# 画像类（query）
profile_view
```

三个决定：

1. **`command` 类不进对话工具面**（`mindmap.present`、`mindmap.expand`）。它们作用于「已打开的那张图」，而对话里没有「当前图」这个上下文；硬接进来，模型会在没有 resource_id 时瞎猜。
2. **`kb.search` 目前是 501**，先注册但不进工具面，等检索层收敛后再放。理由：给模型一个必然返回 501 的工具，等于教它重试。
3. **工具数量控制在 14 以内**。超过这个量，工具选择准确率会掉，且 prompt 占比过高。后续能力优先合并（如 `kg_path` 可被 `kg_ask` 覆盖）。

#### 4.3.2 工具名映射

```python
# tools.py
_TOOL_TO_CAPABILITY = {spec.tool_schema()["name"]: spec.name for spec in all_specs()}
_CAPABILITY_TO_TOOL = {cap: tool for tool, cap in _TOOL_TO_CAPABILITY.items()}
```

**禁止 `name.replace("_", ".")`**。今天恰好都只有两段，明天出现 `kg.attribute.detail` 就会把 `kg_attribute_detail` 反解成三段里错的那一段。映射表是显式的，名字改了就报 `KeyError`，而不是静默调错能力。

#### 4.3.3 参数白名单校验

`CapabilityInput` 的 `extra="ignore"` 是给前端顺手带旁路字段用的；对模型不能这样。适配器在 dispatch 前比对 `spec.input_model.model_fields`：

```python
unknown = set(body) - set(spec.input_model.model_fields) - {"params"}
if unknown:
    return {"ok": False, "code": "assistant.unknown_params",
            "message": f"这些参数这个能力不认：{sorted(unknown)}",
            "accepted": sorted(spec.input_model.model_fields)}
```

模型拿到 `accepted` 列表后会在下一轮改对；默默吞掉的结果是用户看到「按 12 页做的」但实际按 10 页——这比报错更坏。

### 4.4 有界循环

```python
MAX_ROUNDS = 8          # 含首轮规划；够「查→判→生成→收尾」跑两遍
MAX_SECONDS = 60.0      # 墙钟上限；超时带着已有结果收束
MAX_JOBS_PER_RUN = 2    # 一次提问最多起两个生成任务
```

结构（与 `ppt/agent_loop.py` 同构，便于复用同一套判据与测试手法）：

```
system   能力清单 + 什么时候用/别用 + 输出要求（先说要做什么，再调工具）
user     用户这句话 + 会话摘要 + 画像要点 + 本轮可用工具
  ↕
① 模型返回 tool_calls（0..n）
② 逐个执行：生成类立刻返回 202；查询类当场返回结果
③ 工具结果回灌（成功给摘要，失败给错误码 + remedy）
④ 无 tool_call 或达到上限 → 收束：一句话 + ui_actions
```

**收束不是「再问一次模型」**，而是把最后一轮的自然语言直接当答案。理由：多一次收束调用只为了排版，成本与延迟都不划算，且会让「已经生成完了」和「还在想」混在一起。

#### 4.4.1 规则兜底（router.py）

只在三种情况下介入，避免与模型抢判断：

| 触发 | 行为 |
|---|---|
| 模型层是 Mock（无 key） | 关键词规则：产物词（导图/图/PPT/讲稿/文档/提纲/试卷）+ 动词（做/出/生成/来一份）→ 对应生成；否则走检索或直答模板 |
| 模型返回 0 个 tool_call 但意图明确 | 用规则补一次工具调用，再走一轮 |
| 超过 `MAX_ROUNDS` 仍无产物且用户明确要求生成 | 用最后一次 `query` 直接起任务，并告知「按你的原话先出了一版」 |

规则表放 `router.py` 的 `_RULES`，每条必须带反例（与 `use_when/avoid_when` 同一纪律）：

```python
("mindmap", ("导图", "思维导图", "关系图", "梳理成图"), ("有什么区别", "多少钱")),
("ppt",     ("ppt", "幻灯", "讲稿", "课件", "演示"), ("打开我的",)),
("doc",     ("文档", "提纲", "讲义", "总结成文"), ("题库", "试卷")),
("quiz",    ("试卷", "出题", "练习题", "考我"), ("讲稿",)),
```

### 4.5 生成任务桥（bridge.py）

这是整个方案最容易做错的地方，单独说清。

```
tool_call mindmap_generate
  → dispatch.invoke → 202 {task_id}
  → 写 assistant_artifact(state=accepted)
  → 工具结果回给模型：{ok:true, task_id, "已受理，进度会实时显示给用户"}
  → 模型给一句「我先起草方案」
  → bridge 启动订阅，run 状态 running 保持不变

订阅资源流：
  step 类事件  → 翻译成 tool_progress（call_id 不变，前端据此更新那一条）
  plan / design / pause(await_plan)
              → 推 plan_card，写 artifact(state=awaiting_plan)
              → run 置 awaiting_plan，本轮 SSE 关闭（等待用户）
  done        → 推 artifact 卡（resource_id / open_url / evidence / verify）
              → artifact 置 done，run 置 done
  error       → 推 error 事件 + remedy，run 置 failed
```

**为什么本轮在 `pause` 时就关闭连接**：工坊的闸门语义是「用户确认后才生成」。若把连接挂着等用户抬手，中间任何一次网络抖动都会让「确认」这一步丢在半路；关闭连接、把状态落在 run 与 artifact 上，重进页面就能原样恢复。

**确认走既有接口，不复制**：

```
POST /api/resource/tasks/{task_id}/plan/confirm   {plan, design}
  → 已有：校验、落库、按 gate_step 续跑
  → 前端随后 streamResourceTask(task_id) 订阅续流
```

assistant 侧只做两件事：`artifact.state = done`、把结果挂回那条 `plan_card` 消息。复制一份 confirm 意味着两套版本分流（v2/v3）语义，迟早不一致。

### 4.6 事件协议

与 `resources/service.py` 的 `envelope()` 保持同一形状（`event / step_index / total_steps / step_label / payload`），前端可以只写一套解析。

| 事件 | payload | 前端动作 |
|---|---|---|
| `message_start` | `{message_id, run_id, conversation_id}` | 建占位气泡 |
| `token` | `{text}` | 追加渲染 |
| `tool_start` | `{call_id, tool, title, args}` | 插一条工具行 |
| `tool_progress` | `{call_id, step_index, total_steps, step_label}` | 更新工具行进度 |
| `tool_result` | `{call_id, ok, summary, error_code, remedy}` | 工具行收尾；失败可重试 |
| `plan_card` | `{call_id, task_id, capability, plan, design}` | 渲染内嵌方案卡 |
| `artifact` | `{call_id, kind, resource_id, open_url, evidence, verify}` | 渲染产物卡 |
| `ui_actions` | `{actions: [{type:"open", target:"/mindmap?resource=…", label}]}` | 渲染按钮 |
| `done` | `{message_id, run_id, status, usage}` | 解除停止按钮、落库 |
| `error` | `{code, message, remedy}` | 错误条 + remedy |

**`token` 必须有，但不必逐字**：模型在工具前后各说一句，是按「段」流出的。按 20~40 字一批推送足够，逐字推只会增加前端渲染压力，不增加可读性。

### 4.7 HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/assistant/conversations` | 建会话（返回 id） |
| GET | `/api/assistant/conversations` | 列表（分页、按更新时间倒序） |
| GET | `/api/assistant/conversations/{id}` | 消息 + 本次产出，用于刷新恢复 |
| PATCH | `/api/assistant/conversations/{id}` | 改名 / 归档 |
| POST | `/api/assistant/chat/stream` | SSE；body `{conversation_id, text, client_turn_id, refs?}` |
| GET | `/api/assistant/runs/{run_id}` | 快照：状态、轨迹、待确认产物 |
| GET | `/api/assistant/runs/{run_id}/stream` | 断线重连，重放已缓存事件（仿 `_EVENT_CACHE`） |
| POST | `/api/assistant/runs/{run_id}/cancel` | 停生成；同时调 `capability-invocations/{id}/cancel` |

幂等：`client_turn_id` 与 `capability_invocation` 同一套约定——同一个 id 重放返回同一 `run_id`，不产生第二条用户消息。

### 4.8 失败与降级矩阵

| 失败 | 用户看到 | 系统动作 |
|---|---|---|
| 模型超时 / 无 key | 规则路由的正常回答 | 循环降级，不报错 |
| 模型给了不存在的工具 | 「这一步没走通」+ 可重试 | 回灌 `capability.unknown`，下一轮收束 |
| 参数不合法 | 错误条 + `remedy` | 回灌 `accepted` 字段表 |
| 生成任务失败 | 产物卡置「没炼成」+ 重试按钮 | `artifact.state=failed`，保留 task_id |
| 流中断 | 顶部「连接已断，点此续上」 | `GET runs/{id}/stream` 重放 |
| 同轮两个生成任务 | 两张卡并列 | 上限 2，第三个直接拒 |
| 画像为空 | 不阻断；按默认假设走并写明 | 读 `profile.view` 返回 `{}` |

---

## 5. 前端设计

### 5.1 入口与路由

- `config/menu.ts`：「学习助手·小岐」改为 **AI 服务助手**，保留小岐人格名作为副标题；去掉 `assistantTrigger`，改为普通 `route: '/assistant'`。
- `router/index.ts`：`/assistant` 从 `redirect` 改为真实组件 `AssistantView`，`meta: { group: '浮层', mode: 'embedded' }`。
- `stores/assistant.ts`：保留布尔值给顶栏按钮用，但语义从「开抽屉」改为「跳页面」（`show()` → `router.push('/assistant')` 或保留供未来侧滑）。
- `AppTopbar` 的入口保留：点它等同「新建对话」。

### 5.2 布局

三栏，最大宽度 1280，中栏 760 是阅读宽度（与 Word 版心接近，中文长段落更稳）。

```
┌───────────┬────────────────────────────────┬─────────────┐
│ 会话列表   │  消息流                          │ 本次产出     │
│ + 新建     │  ├ 用户                          │ ├ 产物卡     │
│ 今天/近7天  │  ├ 小岐（无气泡，左侧竖线）       │ ├ 方案（待确认）│
│ 搜索       │  ├ 工具行（细进度）              │ └ 依据摘要   │
│           │  ├ 方案卡 / 产物卡               │             │
│           │  输入区（多行 / @ / ⌘）           │             │
└───────────┴────────────────────────────────┴─────────────┘
```

**右栏默认收起**，有产物或待确认方案时滑出。理由：空对话时右栏是废面积；有产物时它是「这次聊出了什么」的索引，比在长消息流里回滚找卡片快。

### 5.3 消息类型（与后端 `content_json` 一一对应）

```ts
type AssistantMessage =
  | { type: 'text'; text: string; sources?: Source[] }
  | { type: 'tool'; callId: string; tool: string; title: string;
      state: 'running' | 'ok' | 'failed'; step?: {i: number; total: number; label: string};
      error?: { code: string; message: string; remedy?: string } }
  | { type: 'plan'; callId: string; taskId: string; capability: string;
      plan: ResourcePlan; design: Record<string, unknown>; confirmed: boolean }
  | { type: 'artifact'; callId: string; kind: 'mindmap'|'ppt'|'doc'|'quiz';
      resourceId?: string; openUrl?: string; evidence?: Evidence; verify?: Verify }
  | { type: 'error'; code: string; message: string; remedy?: string }
```

**工具行不是装饰**。它要能展开看到「调了什么、传了什么、为什么失败」——这是对话系统最难排查的部分，把它藏起来的代价是每次出问题都要翻日志。

### 5.4 方案卡：复用而不是复制

`components/resources/PlanReview.vue` 现在是整屏面板。对话里需要紧凑版。

推荐做法：把「草稿编辑 + 修订 + 确认」的逻辑抽成 `composables/usePlanDraft.ts`，`PlanReview` 与新的 `components/assistant/PlanCard.vue` 共用这一份状态机，各自只做皮肤。

**不推荐**在 `PlanCard` 里重写一遍增删单元、改名、修订反馈——那是两份必然分叉的逻辑，而 `plan-negotiation.component.spec.mjs` 只守得住其中一份。

### 5.5 空态

空态是产品的一部分，不是占位。四张能力卡（生成 / 查图谱 / 看画像 / 更多），每张写清「能问什么」，各配一句可直接点的示例：

```
做一张解表药的导图        → mindmap
给我讲十八反，做 12 页    → ppt
麻黄和桂枝有什么区别      → kg.compare
我的画像里哪块最弱        → profile.view
```

四个人口覆盖四类工具，用户第一次就知道边界在哪。

### 5.6 视觉：简洁但不失特点

沿用现有 `styles/tokens.scss` 的 `--qh-*`，不引入新色板。三个可辨识点：

1. **不使用左侧头像气泡堆叠**。助手消息用 3px 竖线 + 纸面色底，与工坊「卷轴」的气质一致；用户消息右下对齐、浅底。
2. **工序进度是一条细线**，不是转圈。工坊的八步/六步工序本来就是这个产品的仪式感，对话里把它压成一行 `设计版式 2/6`，比 spinner 信息量高得多。
3. **宋体标题 + 楷体引文**。`--qh-font-display` 已具备；引文块（教材原文）用楷体，一眼区分「谁说的」与「小岐说的」。

### 5.7 交互细节

| 场景 | 行为 |
|---|---|
| Enter | 发送 |
| Shift+Enter | 换行 |
| `/` | 唤起快捷指令（`/mindmap` `/ppt` `/doc` `/quiz` `/ask`），插入的是意图前缀，不绕过 Agent |
| `@` | 引用既有资源（列表来自 `resource.list`），作为 `refs` 一并提交 |
| 生成中 | 输入框可继续打字，发送置灰；「停止」调 `runs/{id}/cancel` |
| 失败 | 工具行内联「重试」，重试用同一 `client_turn_id`，不产生重复任务 |
| 刷新 | 进 `/assistant?c={id}` 拉快照；有 `awaiting_plan` 则恢复方案卡 |

---

## 6. 分期与出口判据

不以「能聊」为完成标准。每期给可断言的出口。

### P0 · 地基（契约修正 + 会话层 + 单能力贯通）

交付：
- 修 G1/G3/G4/G5：补 `doc.generate` 声明（先接既有 doc 管线）、artifact 从 spec 派生、工具名映射表、参数白名单校验
- `app/assistant/` 六个模块骨架 + 三张表
- 规则路由 + 单工具（`kg.ask`）端到端
- 前端 `/assistant` 三栏骨架 + SSE 客户端 + 文本/工具/错误三类消息

出口判据：
- 无 key（mock）环境下，「麻黄和桂枝能一起用吗」能拿到图谱回答并落一条会话
- 同一条消息重发两次（同 `client_turn_id`），库里只有一条 user message、一条 run
- `pytest backend/tests/test_assistant_*.py` 全绿；`vue-tsc --noEmit` 干净

### P1 · 生成闭环（三工坊）

交付：
- `bridge.py` 任务桥 + 工序进度映射
- 内嵌方案卡（抽 `usePlanDraft`，`PlanCard` + `PlanReview` 共用）
- 产物卡 + 打开跳转 + 右栏聚合
- `quiz.generate` 契约（若排期紧，可后置到 P2，但 `doc.generate` 必须在 P1）
- 停止生成、断线重连、失败重试

出口判据：
- 「做一张解表药的导图」→ 方案卡 → 就地改一个分支 → 确认 → 成品卡 → 点开是 `/mindmap?resource=…`
- 确认前库里**没有**资源；确认后只有一份
- 断流后刷新页面，方案卡与进度原样恢复
- 三条工坊各跑一遍，产物 `verify` 与 `evidence` 在卡片上可见

### P2 · 查询与画像

交付：
- `kg` 六能力 + `kb.search`（实装后）+ `profile.view` 进工具面
- 先查后写：查不到教材依据时，生成前明确告知「这一版主要是模型补充」
- 引用 chips（教材条目 / 图谱边）与「依据不足」的显式文案

出口判据：
- 30 条盲测问句：选对工具 ≥90%、参数完整 ≥85%、不该生成时 0 次生成
- 「我的画像里哪块最弱」能给出具体维度与依据，不是一段套话

### P3 · 记忆与多产物

交付：
- 会话摘要（每 10 轮压一次，进 system）
- 画像联动：默认受众/学段从画像取，取不到写明假设
- `@` 引用既有资源、基于上次产物的增量指令（「在第三支下再加两味药」）
- 一次对话多个产物 + 右栏时间线

出口判据：
- 20 轮会话不超上下文预算；摘要后仍能答对「我们刚才说要几页」
- 「基于刚才那张图，把禁忌那一支展开」能定位到正确 resource 并只改那一支

### P4 · 扩展（漫剧 / 游戏）

交付：
- `story.generate`、`game.*` 按同一份 `CapabilitySpec` 注册
- 前端 `artifact` 卡新增两种 kind 的渲染
- **Agent 代码零改动**——这是本方案的核心承诺，P4 是它的验证期

出口判据：
- 新增一种能力，改动文件仅限 `handlers/story.py` + 前端卡片映射 + 一份测试

---

## 7. 测试策略

| 层 | 测什么 | 放哪 |
|---|---|---|
| 契约 | 每个进工具面的能力，其 `tool_schema()` 参数能被 `dispatch.validate` 接受 | `tests/test_assistant_tools.py` |
| 循环 | 超轮次收束、工具异常不炸整轮、生成上限 2、模型 0 调用时规则补位 | `tests/test_assistant_loop.py` |
| 桥 | 202 → 进度 → pause → 确认 → done 的事件序列 | `tests/test_assistant_bridge.py` |
| 幂等 | 同 `client_turn_id` 重放不产生第二条消息/任务 | `tests/test_assistant_store.py` |
| 前端 | 消息类型渲染、方案卡确认后才订阅续流、停止生成、断线恢复 | `frontend/tests/assistant-*.spec.mjs` |
| 盲测 | 30 条真实问句 → 期望工具序列 | `docs/assistant-blindtest.md`（P2 建） |

盲测是唯一的「好不好用」判据。契约测试只能证明不炸，证明不了它选对了工具。

---

## 8. 明确不做

1. **不做无界 ReAct**。轮次、时长、产物数三重上限写在代码里，不靠提示词自觉。
2. **不在对话里做编辑器**。改图进 `/mindmap`，改稿进 `/doc-studio`。对话里的改只到「方案层」，产物层改动一律跳转。
3. **不做多角色圆桌**。用户说的「多智能体」指能力分派，不是让几个 Agent 互相商量——那会把延迟乘三，且无法归因。
4. **不让模型生成 HTML/JS 直接渲染**。所有展示走既有卡片组件。
5. **P0~P3 不做语音与多模态输入**。留接口（`refs` 支持附件 id），不排期。

---

## 9. 风险与开放问题

| 风险 | 影响 | 缓解 |
|---|---|---|
| 模型选错工具 | 用户拿到不相干的产物 | 工具描述带 `avoid_when` 真问句（已有）；生成前先出方案卡，用户在生成前就能否掉 |
| 长连接不稳定 | 进度丢失 | run 快照 + 流重放；状态落库而非只存在内存 |
| 资源库污染 | 一次对话生成四五份 | 每轮 ≤2、每份都需用户确认或明确要求 |
| 成本 | flash 关闭思考后单轮仍有一次调用 | 结果冻结（同参数不重跑）；摘要压缩上下文 |

待你拍板的四件事（不影响开工，影响 P3 之后）：

1. **会话是否跨设备**：现在 sqlite 单机，换设备就看不到历史。若要跨端，得先定同步方案。
2. **游客可用到什么程度**：游客能查图谱、能生成几次？现有 guest 登录体系可直接接，但配额没定。
3. **模型档位是否暴露给用户**：给「快速/深入」两档，还是全部由 `LLM_TASK_MODEL_MAP` 静默决定。
4. **P1 是否连 `quiz.generate`**：题库现在走 `/api/quiz/generate`，补契约约半天，但会牵出「试卷算不算工坊」的产品定义。

---

## 10. 与其他文档的关系

| 文档 | 关系 |
|---|---|
| `docs/capabilities.md` | 机器生成的能力清单，本文的工具面从它派生；**不手改** |
| `docs/studios/README.md` | 工坊的 8 步形状与两处闸门，本文只调用不改写 |
| `docs/studios/ppt.md` / `mindmap.md` / `doc.md` | 三工坊的分册，方案卡里的 `plan`/`design` 语义以它们为准 |
| `docs/resource-studios-upgrade-plan.md` | 已落地事实索引；本文的 G1~G7 缺口与它不冲突 |
| `docs/sitewide-design-overhaul.md` | 站内视觉语言；本文 §5.6 只做对话场景的补充，不复述 token |
