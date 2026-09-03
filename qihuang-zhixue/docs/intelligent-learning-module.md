# 智能学习模块代码梳理

更新时间：2026-07-14

本文档整理 `qihuang-zhixue` 项目中与“智能学习”直接相关的前端、后端、智能体、知识库、掌握度、复习与测试代码。范围以现有代码为准，不包含未实现的产品设想。

## 1. 模块定位

智能学习模块围绕中药学知识点，为登录用户提供以下能力：

- 按主题生成多类型学习资源：结构化讲解、知识图谱、思维导图、题库、延伸阅读。
- 通过 SSE 流式展示生成过程和讲解 Markdown。
- 基于用户画像调整题目难度，并把答题结果回写到画像证据。
- 基于 BKT 追踪知识点掌握度。
- 基于 FSRS 风格调度到期复习。
- 基于知识库层级、掌握度、复习紧迫度、画像置信度生成学习路径和 Top3 推荐。

核心链路：

```text
前端 Learn/Path/Quiz/Review 页面
  -> Pinia learning store
  -> frontend/src/services/learningApi.ts
  -> FastAPI /api/resources/generate, /api/path, /api/reviews, /api/quiz/submit
  -> LangGraph ResourceAgent / PathAgent
  -> KB 检索、资源 Agent、证据校验、BKT、FSRS、画像更新
```

## 2. 前端入口

### 2.1 路由与菜单

相关文件：

- `frontend/src/router/index.ts`
- `frontend/src/config/menu.ts`

学习相关路由：

| 路由 | 组件 | 模块含义 |
| --- | --- | --- |
| `/learn` | `LearnView.vue` | 智能学习主页面，生成并展示资源包 |
| `/path` | `PathView.vue` | 闯关地图/学习路径/技能树 |
| `/quiz` | `QuizView.vue` | 题库测评 |
| `/review` | `ReviewView.vue` | 到期复习计划 |
| `/profile`、`/onboarding` | 画像模块 | 智能学习依赖画像数据 |

路由守卫会要求用户登录，并在未完成画像时跳转到 onboarding。智能学习资源生成接口也依赖 `Authorization: Bearer <token>`。

### 2.2 API 封装

文件：`frontend/src/services/learningApi.ts`

导出函数：

- `readLearningPath()` -> `GET /api/path`
- `readReviews()` -> `GET /api/reviews`
- `submitQuiz(items)` -> `POST /api/quiz/submit`
- `streamResources(payload, handlers, signal?)` -> `POST /api/resources/generate`

`streamResources` 使用 `fetch` 读取 `text/event-stream`，按 SSE frame 解析事件：

| SSE event | 前端 handler | 含义 |
| --- | --- | --- |
| `node_start` | `nodeStart` | 后端某个生成节点开始/完成摘要 |
| `token` | `token` | 讲解 Markdown 的流式片段 |
| `resource_done` | `resourceDone` | 某一类完整资源生成完成 |
| `done` | `done` | 本次资源生成完成 |

连接超时时间为 `RESOURCE_STREAM_CONNECT_TIMEOUT_MS = 10_000`。

### 2.3 Pinia 状态

文件：`frontend/src/stores/learning.ts`

核心 state：

- `activeTopic`：当前主题，默认 `麻黄`。
- `nodeLabel`：当前后端节点显示文案。
- `streamStableMarkdown` / `streamPendingMarkdown`：流式 Markdown 缓冲。
- `resources`：按 `ResourceType` 存储资源包。
- `streamTrace`：SSE 节点轨迹。
- `streaming`、`loadingPath`、`loadingReviews`、`error`：加载状态。
- `path`：学习路径响应。
- `reviews`：复习队列响应。
- `lastSubmit`：最近一次题目提交结果。

核心 actions：

- `loadPath()`：读取 `/api/path`，同时把 path 内的 reviews 同步到 `reviews`。
- `loadReviews()`：读取 `/api/reviews`。
- `generateResources(topic, types)`：发起 SSE 资源生成，更新流式文本和资源包。
- `answerQuiz(items)`：提交答题结果，成功后刷新路径和复习队列。
- `labelFor(type)`：资源类型中文标签。

默认资源类型：

```ts
['explain', 'graph', 'mindmap', 'quiz', 'reading']
```

### 2.4 类型契约

文件：`frontend/src/types/learning.ts`

前端类型与后端 `app/agents/resource_schemas.py` 基本对应。

资源类型：

```ts
type ResourceType = 'explain' | 'graph' | 'mindmap' | 'quiz' | 'reading'
```

统一资源包：

```ts
type ResourceEnvelope<T extends ResourceType = ResourceType> = {
  type: T
  topic: string
  payload: ResourcePayloadByType[T]
  sources: SourceRef[]
  grounding: GroundingReport
  trace_node: string
  generated_by: string
  cache_key: string
}
```

学习路径：

- `SkillNode`：技能树节点。
- `Recommendation`：推荐项。
- `ReviewsResponse`：到期复习。
- `PathResponse`：技能树、推荐、复习摘要和 trace。
- `QuizSubmitItem` / `QuizSubmitResponse`：答题提交入参和返回。

## 3. 前端页面与组件

### 3.1 智能学习主页面

文件：`frontend/src/views/LearnView.vue`

职责：

- 输入学习主题。
- 选择资源类型。
- 调用 `learning.generateResources(topic, selectedTypes)`。
- 展示 SSE trace。
- 左侧展示流式讲解 Markdown。
- 右侧以 tabs 展示资源包：图谱、导图、题库、阅读。
- 题库提交后调用 `learning.answerQuiz(items)`。

使用组件：

- `MarkdownBlock.vue`
- `ResourceGraph.vue`
- `ResourceMindmap.vue`
- `QuizPanel.vue`
- `ResourceSkeleton.vue`

页面会读取 URL query `topic`。如果当前没有资源，或当前主题与 query 不一致，会自动生成资源。

### 3.2 闯关地图/学习路径

文件：`frontend/src/views/PathView.vue`

职责：

- 调用 `learning.loadPath()` 加载技能树。
- 把扁平 `SkillNode[]` 组装为大类、小类、药材、鉴别节点树。
- 展示掌握数、总关卡数、可挑战数、进度条。
- 支持搜索、展开/折叠、分页展示药材/鉴别节点。
- 点击未锁定节点时，根据节点 `recommended_resources` 生成资源。
- 生成后可跳转到 `/learn?topic=<activeTopic>` 查看资源。

锁定逻辑由后端给出，前端只负责展示和阻止锁定节点生成资源。

### 3.3 题库测评

文件：`frontend/src/views/QuizView.vue`

职责：

- 输入主题。
- 生成 `['quiz', 'explain']` 两类资源。
- 展示 `QuizPanel`。
- 提交后回写掌握度、复习和画像。

### 3.4 复习计划

文件：`frontend/src/views/ReviewView.vue`

职责：

- 调用 `learning.loadReviews()` 获取今日到期项。
- 展示 FSRS 难度、稳定性、紧迫度。
- 点击复习项时，以 `kc_id.replace(/^herb_/, '')` 作为主题生成 `['quiz', 'explain']`。
- 使用 `QuizPanel` 提交复习作答。

### 3.5 资源展示组件

文件：`frontend/src/components/learning/*`

| 文件 | 职责 |
| --- | --- |
| `MarkdownBlock.vue` | 使用 `markdown-it` 渲染讲解 Markdown，禁用 HTML，展示 pending cursor |
| `ResourceGraph.vue` | 使用 ECharts graph 渲染知识图谱，支持关系筛选、节点高亮、力导向布局 |
| `ResourceMindmap.vue` | 使用 `markmap-lib` + `markmap-view` 渲染层级 Markdown 导图 |
| `QuizPanel.vue` | 渲染单选、多选、填空、简答、病例分析题；提交统一 `QuizSubmitItem[]` |
| `ResourceSkeleton.vue` | 资源生成中的占位加载态 |

`QuizPanel` 当前判分不在前端做，提交 payload 后由后端按 `answer` 与 `correct_answer` 进行集合比较。

## 4. 后端 API

文件：`backend/app/api/learning.py`

FastAPI router：

```py
router = APIRouter(prefix="/api", tags=["learning"])
```

### 4.1 `POST /api/resources/generate`

入参模型：`ResourceGenerateRequest`

```py
topic: str
types: list[ResourceType] = ["explain", "graph", "mindmap", "quiz", "reading"]
```

行为：

1. 校验 topic 非空。
2. 读取当前登录用户。
3. 返回 `StreamingResponse`，media type 为 `text/event-stream`。
4. 内部 `_resource_sse_iter` 执行：
   - `node_start: profile_context`
   - `get_latest_profile(user_id)`
   - `node_start: retrieve_agent`
   - `node_start: dispatch`
   - `run_resource_generation(topic, resource_types, profile)`
   - 输出 LangGraph trace 的 `node_start`
   - 对 explain markdown 分块输出 `token`
   - 按请求 types 顺序输出 `resource_done`
   - 输出 `done`

SSE header：

- `Cache-Control: no-cache`
- `Connection: keep-alive`
- `X-Accel-Buffering: no`

### 4.2 `GET /api/path`

行为：

- 调用 `run_path_planning(user_id, db_path)`。
- 返回：
  - `user_id`
  - `skill_tree`
  - `recommendations`
  - `reviews`
  - `summary`
  - `trace`

### 4.3 `GET /api/reviews`

行为：

- 调用 `due_reviews(user_id, db_path)`。
- 返回当前用户今日到期复习项和建议题量。

### 4.4 `POST /api/quiz/submit`

入参模型：`QuizSubmitRequest`

每题字段：

- `question_id`
- `kc_id`
- `answer`
- `correct_answer`
- `elapsed_seconds`
- `difficulty`
- `bloom_level`
- `topic`

行为：

1. `_is_answer_correct(answer, correct_answer)`：集合规范化后完全相等则正确。
2. `_normalize_kc_id(raw)`：把药名、别名或 id 归一为知识库 herb id。
3. `record_mastery_event(...)` 写入 BKT 事件。
4. `record_review_result(...)` 更新 FSRS 复习状态。
5. 汇总答题证据，调用 `update_profile_from_event(..., type="quiz")` 更新画像。
6. 返回：
   - `results`
   - `mastery`
   - `reviews`

## 5. 资源生成 LangGraph

文件：`backend/app/agents/resources_graph.py`

资源图状态：`ResourceState`

- `topic`
- `profile`
- `resource_types`
- `intent`
- `retrieved_docs`
- `whitelist`
- `resources`
- `trace`

默认资源类型：

```py
DEFAULT_RESOURCE_TYPES = ["explain", "graph", "mindmap", "quiz", "reading"]
```

节点：

| 节点 | 函数 | 职责 |
| --- | --- | --- |
| `router` | `router_node` | 用 `ModelRouter.classify` 判断是否为资源生成意图 |
| `retrieve_agent` | `retrieve_node` | 检索主题相关文档，构造白名单 |
| `dispatch` | `dispatch_node` | 规范化资源类型并 fan-out |
| `explain_agent` | `explain_node` | 生成结构化讲解 |
| `graph_agent` | `graph_node` | 生成 ECharts graph JSON |
| `mindmap_agent` | `mindmap_node` | 生成 markmap Markdown |
| `quiz_agent` | `quiz_node` | 生成题目数组 |
| `reading_agent` | `reading_node` | 生成延伸阅读 |
| `verify_agent` | `verify_node` | 对所有资源做证据校验和白名单裁剪 |
| `other` | `other_node` | 非资源生成意图，不生成资源 |

拓扑：

```text
router
  -> retrieve_agent | other
retrieve_agent
  -> dispatch
dispatch
  -> explain_agent / graph_agent / mindmap_agent / quiz_agent / reading_agent
各资源 Agent
  -> verify_agent
verify_agent
  -> END
```

`_dispatch_resource_agents` 使用 LangGraph `Send` 实现并发分支。`run_resource_generation(..., max_concurrency=5)` 默认最多 5 并发。

## 6. 资源数据契约

文件：`backend/app/agents/resource_schemas.py`

全部资源模型继承 `StrictResourceModel`，配置 `extra="forbid"`，禁止模型输出未声明字段。

### 6.1 通用结构

- `TraceItem`：节点名和摘要。
- `SourceRef`：来源名称、类型、引用位置、原文片段。
- `GroundingReport`：证据支持比例、支持断言、未支持断言、提示。
- `ResourceEnvelope`：统一资源包，校验 `type` 与 `payload` 类型匹配。

### 6.2 explain

- `ExplainSection`
- `ExplainPayload`

要求按七个小节组织：

1. 定义
2. 性味归经
3. 功效
4. 主治证型
5. 配伍(带方剂)
6. 相似药鉴别
7. 用量与使用注意

### 6.3 graph

- `GraphNodeCategory`: `herb` / `syndrome` / `formula`
- `GraphRelation`: 相须、相使、相畏、相杀、功效相似、同类、主治同证
- `GraphPayload`: nodes + links

节点必须来自检索白名单。

### 6.4 mindmap

- `MindmapPayload`
- 输出 markmap 兼容层级 Markdown。
- `node_names` 用于实体白名单校验。

### 6.5 quiz

- `QuizType`: 单选、多选、填空、简答、病例分析
- `BloomLevel`: 记忆、理解、应用、分析、评价、创造
- `QuizQuestion`
- `QuizPayload`

质量规则：

- 禁止“以上皆是/以上皆非”。
- 禁止“总是/从不”等绝对词。
- 选择题不得退化为 True/False 判断。
- 干扰项必须有 rationale，并指向常见易混药或证。
- 单选正确项不得系统性最长。

### 6.6 reading

- `ReadingItem`
- `ReadingPayload`

要求 3 到 5 条延伸阅读推荐，每条带理由和 sources。

## 7. 资源 Agent

### 7.1 公共工具

文件：

- `backend/app/agents/resource_context.py`
- `backend/app/agents/resource_cache.py`
- `backend/app/agents/resource_agent_utils.py`

职责：

- `retrieve_resource_docs(topic, top_k=5)`：调用 KB 检索，优先保留 topic 中直接点名的药材。
- `build_whitelist(docs)`：从检索文档提取药材、药材 id、证型、方剂白名单。
- `profile_hash(profile)` / `cache_key_for(...)`：稳定生成缓存键。
- `get_cached_resource` / `set_cached_resource`：进程内资源缓存。
- `build_envelope(...)`：统一构造 `ResourceEnvelope` 并写入缓存。
- `difficulty_from_profile(profile)`：根据画像粗调题目难度。

缓存键格式：

```text
topic|resource_type|profile_hash
```

### 7.2 explain_agent

文件：`backend/app/agents/explain_agent.py`

生成 `ExplainPayload`。系统提示要求：

- 只使用 `retrieved_docs`。
- 禁止引入检索外药材。
- 按七个小节输出。
- 每节挂 sources。
- 输出可被 Pydantic 校验的 JSON。

mock 模式会基于第一篇检索文档拼装结构化讲解。

### 7.3 graph_agent

文件：`backend/app/agents/graph_agent.py`

生成 `GraphPayload`。系统提示要求：

- 只输出 ECharts Graph JSON。
- 节点必须来自检索命中的药材、证型、方剂白名单。
- 关系必须来自枚举。

mock 模式会生成药材节点、主治证型节点、方剂节点，并对同类药材添加关系。

### 7.4 mindmap_agent

文件：`backend/app/agents/mindmap_agent.py`

生成 markmap 层级 Markdown。mock 层级：

```text
药材大类
  -> 小类
    -> 药材
      -> 性味/归经/功效/主治/用量
```

### 7.5 quiz_agent

文件：`backend/app/agents/quiz_agent.py`

生成 `QuizPayload`，题目基于检索文档和画像。mock 模式默认生成：

- 1 道单选
- 1 道多选
- 1 道填空
- 1 道简答
- 1 道病例分析

难度由 `difficulty_from_profile(profile)` 调整，并限制在 1 到 5。

### 7.6 reading_agent

文件：`backend/app/agents/reading_agent.py`

生成 3 到 5 条延伸阅读。mock 模式围绕：

- 性味归经与功效
- 主治证型辨析
- 配伍与方剂线索
- 相似药鉴别
- 检索命中药材的横向比较

### 7.7 verify_agent

文件：`backend/app/agents/verify_agent.py`

职责：

- 对 graph 节点和关系做白名单裁剪。
- 对 mindmap 实体节点做白名单校验，并标记越界节点。
- 对 explain、quiz、reading 的 claims 做证据对齐。
- 未支持断言会写入 `GroundingReport.unsupported_claims`。
- 证据分数低于 `GROUNDING_THRESHOLD = 0.65` 时提示人工复核。

claims 校验逻辑：

1. 先在当前检索证据中匹配。
2. 未命中时使用 claim 再检索一次。
3. 仍未命中则标记 unsupported。

## 8. 学习路径 PathAgent

文件：`backend/app/agents/path_agent.py`

PathAgent 是另一条 LangGraph，用于生成技能树、复习队列和推荐。

状态：`PathState`

- `user_id`
- `db_path`
- `mastery`
- `skill_tree`
- `reviews`
- `recommendations`
- `profile_confidence`
- `answer`
- `trace`

节点：

| 节点 | 函数 | 职责 |
| --- | --- | --- |
| `mastery_context_agent` | `mastery_context_agent` | 读取 BKT 掌握度快照 |
| `skill_tree_agent` | `skill_tree_agent` | 根据 KB 层级构建技能树并计算状态 |
| `review_queue_agent` | `review_queue_agent` | 读取 FSRS 到期复习 |
| `recommendation_agent` | `recommendation_agent` | 融合薄弱度、紧迫度、前置就绪度和画像置信度 |
| `path_summary_agent` | `path_summary_agent` | 生成摘要文本 |

拓扑是线性的：

```text
mastery_context_agent
  -> skill_tree_agent
  -> review_queue_agent
  -> recommendation_agent
  -> path_summary_agent
  -> END
```

### 8.1 技能树

`build_skill_tree(...)` 从 KB 药材构建节点：

- `category:<category>`：大类。
- `subcategory:<category>:<subcategory>`：小类。
- `herb_xxx`：药材。
- `diff:<left_herb_id>:<right_herb_id>`：相似药鉴别。

节点状态：

- `mastered`：BKT 掌握度达到 `MASTERY_THRESHOLD = 0.8`。
- `unlocked`：无前置或前置已掌握，但当前还没有练习。
- `in_progress`：已解锁且已有练习记录。
- `locked`：前置未全部掌握。

`validate_skill_tree(...)` 校验：

- 前置节点存在。
- 无环。
- 所有节点从根可达。

### 8.2 推荐逻辑

`next_recommendations(...)` 返回 Top3。

评分因素：

- `weak_strength = 1.0 - mastery`
- FSRS 到期紧迫度 `due_urgency`
- 前置就绪度 `readiness`
- 直接练习次数带来的 evidence boost
- 画像置信度用于 reason 和 confidence 修正

推荐 reason 会说明：

- 画像置信度是否偏低。
- 最近薄弱证据。
- FSRS 是否到期。
- 前置是否就绪。

## 9. 掌握度与复习

### 9.1 BKT 掌握度

文件：`backend/app/mastery/bkt.py`

默认参数：

```py
p_known_initial = 0.35
p_learn = 0.18
p_slip = 0.1
p_guess = 0.2
MASTERY_THRESHOLD = 0.8
```

核心函数：

- `update_p_mastery(p_mastery, correct, params)`：按一次答题结果更新掌握概率。
- `record_mastery_event(...)`：写入掌握度事件。
- `get_mastery(user_id, db_path)`：按事件顺序重放，返回每个知识点掌握度。

### 9.2 FSRS 风格复习

文件：`backend/app/mastery/review.py`

评级：

- `again`：答错。
- `hard`：答对但耗时超过 90 秒。
- `easy`：答对且耗时不超过 20 秒。
- `good`：其他答对情况。

核心函数：

- `rating_from_answer(correct, elapsed_seconds)`
- `record_review_result(...)`
- `due_reviews(user_id, today, db_path)`

复习状态字段：

- `difficulty`
- `stability`
- `retrievability`
- `due_at`
- `last_rating`
- `attempts`
- `evidence`

`due_reviews` 会返回到期项并按到期时间和难度排序，建议题量上限为 12。

### 9.3 SQLite 存储

文件：`backend/app/mastery/storage.py`

默认数据库：

```text
backend/app/.data/qihuang.db
```

实际代码：

```py
DB_PATH = Path(__file__).resolve().parents[2] / ".data" / "qihuang.db"
```

表：

- `mastery_events`
- `review_states`

测试可通过依赖覆盖或传入 `db_path` 使用临时数据库。

## 10. 知识库与检索

相关文件：

- `backend/app/kb/ingest.py`
- `backend/app/kb/models.py`
- `backend/app/kb/retriever.py`
- `backend/app/kb/embedding.py`
- `backend/app/kb/store.py`
- `backend/data/herbs/*.json`

检索入口：`search(query, top_k=3, category=None, persist_dir=None, threshold=None)`

行为：

- 如果存在 embedding API key，则使用 Chroma collection 向量检索。
- 如果没有 API key，则使用 mock semantic 检索。
- 检索结果低于 `retrieval_score_threshold` 会被过滤。
- 对抗生素、消炎药等拒绝概念，mock 检索会返回低分，避免偏离中药知识库。

资源生成使用 `retrieve_resource_docs(topic, top_k=5)`，并把 `SearchHit` 转换成 `ResourceDoc`，其中包括：

- 药材 id、名称、别名。
- 大类、小类。
- 性味归经。
- 功效、主治、用量、禁忌。
- 配伍、鉴别、常见混淆。
- sources 和 evidence_text。

## 11. 与 LLM/结构化输出的关系

资源 Agent 调用 `structured_create(...)`，传入：

- task type：`generate`
- system prompt
- user prompt，包含 topic/profile/retrieved_docs/whitelist
- response_model：对应 Pydantic 模型
- mock_factory：本地可运行 fallback

因此模块支持两种运行形态：

- 有真实 LLM/embedding 配置：走真实模型和向量检索。
- 无配置：走 mock semantic 检索和 mock payload，保证测试和 demo 可运行。

`generated_by("generate")` 会写入 `ResourceEnvelope.generated_by`，用于标记生成来源。

## 12. 鉴权和用户隔离

后端接口都依赖：

```py
current_user: AuthUser = Depends(get_current_user)
```

用户相关数据按 `user_id` 隔离：

- 资源生成读取该用户最新画像。
- 路径规划读取该用户掌握度、复习、画像置信度。
- 答题提交写入该用户 BKT/FSRS/画像证据。

前端请求通过 `authHeaders` 携带 token。

## 13. 测试覆盖

重点测试文件：

| 文件 | 覆盖内容 |
| --- | --- |
| `backend/tests/test_learning_api.py` | SSE 资源生成、路径/复习 API、答题提交回写 |
| `backend/tests/test_resources.py` | 资源包 envelope 校验、图谱白名单、题目质量黑名单、unsupported claims、并发 trace |
| `backend/tests/test_path.py` | BKT 更新、技能树锁定、DAG 校验、FSRS 到期复习 |
| `backend/tests/test_retriever.py` | 知识库检索 |
| `backend/tests/test_retrieval_threshold.py` | 检索阈值与偏题防护 |
| `backend/tests/test_mock_retrieval.py` | mock 检索 |
| `backend/tests/test_llm_clients.py` / `test_router.py` | LLM 客户端和路由 fallback |

前端当前没有专门覆盖智能学习页面的测试，已有测试主要集中在画像和 onboarding 组件。

## 14. 端到端数据流

### 14.1 生成学习资源

```text
用户在 LearnView 输入主题
  -> learning.generateResources(topic, types)
  -> streamResources POST /api/resources/generate
  -> _resource_sse_iter
  -> get_latest_profile(user_id)
  -> run_resource_generation(topic, types, profile)
  -> ResourceGraph:
       router -> retrieve -> dispatch -> 并发资源 Agent -> verify
  -> SSE token/resource_done/done
  -> learning store 更新 Markdown、resources、trace
  -> LearnView 渲染图谱/导图/题库/阅读
```

### 14.2 提交测验

```text
QuizPanel submitAll
  -> emit QuizSubmitItem[]
  -> learning.answerQuiz(items)
  -> POST /api/quiz/submit
  -> _normalize_kc_id
  -> record_mastery_event
  -> record_review_result
  -> update_profile_from_event(type="quiz")
  -> get_mastery + due_reviews
  -> 前端刷新 path/reviews
```

### 14.3 生成学习路径

```text
PathView mounted
  -> learning.loadPath()
  -> GET /api/path
  -> run_path_planning
  -> get_mastery
  -> build_skill_tree + validate_skill_tree
  -> due_reviews
  -> next_recommendations
  -> compose_path_answer
  -> PathView 组装树并展示
```

### 14.4 到期复习

```text
ReviewView mounted
  -> learning.loadReviews()
  -> GET /api/reviews
  -> due_reviews
  -> 用户点击复习
  -> generateResources(kcId, ['quiz', 'explain'])
  -> QuizPanel submit
  -> BKT/FSRS/画像回写
```

## 15. 关键文件清单

### 前端

```text
frontend/src/router/index.ts
frontend/src/config/menu.ts
frontend/src/types/learning.ts
frontend/src/services/learningApi.ts
frontend/src/stores/learning.ts
frontend/src/views/LearnView.vue
frontend/src/views/PathView.vue
frontend/src/views/QuizView.vue
frontend/src/views/ReviewView.vue
frontend/src/components/learning/MarkdownBlock.vue
frontend/src/components/learning/QuizPanel.vue
frontend/src/components/learning/ResourceGraph.vue
frontend/src/components/learning/ResourceMindmap.vue
frontend/src/components/learning/ResourceSkeleton.vue
```

### 后端 API 与 Agent

```text
backend/app/api/learning.py
backend/app/agents/resources_graph.py
backend/app/agents/resource_schemas.py
backend/app/agents/resource_context.py
backend/app/agents/resource_cache.py
backend/app/agents/resource_agent_utils.py
backend/app/agents/explain_agent.py
backend/app/agents/graph_agent.py
backend/app/agents/mindmap_agent.py
backend/app/agents/quiz_agent.py
backend/app/agents/reading_agent.py
backend/app/agents/verify_agent.py
backend/app/agents/path_agent.py
backend/app/agents/plan.py
backend/app/agents/gen.py
```

### 掌握度、复习、知识库

```text
backend/app/mastery/bkt.py
backend/app/mastery/review.py
backend/app/mastery/storage.py
backend/app/kb/ingest.py
backend/app/kb/models.py
backend/app/kb/retriever.py
backend/app/kb/embedding.py
backend/app/kb/store.py
backend/data/herbs/*.json
```

### 测试

```text
backend/tests/test_learning_api.py
backend/tests/test_resources.py
backend/tests/test_path.py
backend/tests/test_retriever.py
backend/tests/test_retrieval_threshold.py
backend/tests/test_mock_retrieval.py
backend/tests/test_llm_clients.py
backend/tests/test_router.py
```

## 16. 当前实现注意点

- `README.md` 和部分 PowerShell 默认输出存在中文编码显示问题；代码文件用 UTF-8 读取正常。
- `ResourceEnvelope` 后端严格禁止额外字段，前端类型需要与后端 schema 同步维护。
- 资源缓存是进程内 dict，服务重启后丢失，不是持久缓存。
- SSE 当前只有 explain markdown 逐 token 推送，其他资源以完整 `resource_done` 推送。
- 图谱和导图强依赖检索白名单，越界实体会被剔除或标记。
- 题库主观题当前后端仍用集合完全相等判分；简答/病例分析如果学生输入自然语言，现有 `_is_answer_correct` 不会做语义判分。
- `QuizPanel` 生成 `question_id` 使用 `stem-index`，题干变化会导致 id 变化。
- `ReviewView` 从 `herb_xxx` 到生成主题时只移除 `herb_` 前缀，得到的可能是拼音/英文 id 片段，不一定等于中文药名；后端 mock 检索对直接中文药名更友好。
- `PathView` 只展示前三个大类：`topCategories = treeData.slice(0, 3)`。
- 前端没有智能学习专项测试；主要保障在后端。
