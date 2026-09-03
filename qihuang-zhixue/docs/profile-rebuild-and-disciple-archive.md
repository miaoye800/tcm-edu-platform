# 重塑画像与弟子档案模块代码梳理

本文档整理项目中“重塑画像 / 焕醒画像 / 弟子档案”相关的前后端实现，重点说明七维画像的定义、每个画像维度的重塑办法和判定标准、证据如何收集、画像如何更新、雷达图如何绘制，以及相关接口、状态、组件和测试覆盖。

## 1. 模块范围

该模块主要包含两条用户路径：

1. **重塑画像 / 焕醒画像**：用户通过收徒游戏、对话问诊、开药实战、被动埋点等方式提交行为或回答，系统把这些客观信号转成 EvidenceCard，再融合为七维画像。
2. **弟子档案**：前端读取画像、画像叙事、雷达图和薄弱点，在档案页展示当前弟子的学习状态、优势、短板、证据来源和建议。

核心原则是：**先取证，再融合，再绘制，再叙事**。后端不直接把用户一句话写成最终结论，而是先转成可追溯的证据卡，再通过规则融合、三师评审和护栏得到画像维度。

## 2. 七维画像模型

后端画像结构定义在 `backend/app/profile/schema.py`。最终 Profile 包含七个维度：

| 维度 key | 中文名 | 类型 | 主要含义 | 前端雷达轴 |
| --- | --- | --- | --- | --- |
| `knowledge_base` | 知识基础 | 数值型 | 中医药基础知识、药性功效、配伍剂量掌握情况 | 知识基础 |
| `learning_goal` | 学习目标 | 分类型 | 用户为什么学中医药 | 目标清晰度 |
| `learning_style` | 认知/学习风格 | 分类型 | 用户更适合图谱、文本、口诀等学习方式 | 风格集中度 |
| `weak_points` | 易错点与薄弱知识点 | 混合型 | 错题、放弃题、处方失败、自述薄弱点 | 薄弱热区 |
| `pace` | 学习进度与节奏 | 数值型 | 每日学习时长、目标周期、节奏快慢 | 学习节奏 |
| `motivation` | 兴趣与动机偏好 | 混合型 | 内驱、外驱、被动、挑战意愿 | 动机强度 |
| `resource_preference` | 资源类型偏好 | 分类型 | 偏好的学习资源形态 | 资源集中度 |

允许的分类标签：

| 维度 | 允许标签 |
| --- | --- |
| `learning_goal` | 应试提分、临床实用、兴趣科普、学术深造、其他 |
| `learning_style` | 视觉型、文字型、记忆(口诀)型 |
| `motivation` | 内驱、外驱、内外混合、被动 |
| `resource_preference` | 知识图谱、拟人化短剧、汤头歌诀、医案文选 |

每个维度除了分数或标签外，还带有：

- `confidence`：置信度，0 到 1。
- `evidence`：参与该维度判断的证据卡。
- `narrative`：可选叙事文本。
- 分类维度的 `distribution`：不同标签的权重分布。
- 分类维度的 `derived_score`：由分布集中度换算出的雷达分数。

## 3. EvidenceCard：画像重塑的最小证据单位

证据结构定义在 `backend/app/profile/evidence.py`：

```text
EvidenceCard
- dimension: 影响哪个画像维度
- source: 证据来源，例如 quiz、prescription、dialogue、passive
- fact: 可读事实描述
- objective_signal: 结构化客观信号
- weight: 权重
- timestamp: 发生时间
```

该结构是画像模块的核心边界。所有画像入口都先产出 EvidenceCard，再交给融合层。这样做的好处是：

- 可以追溯画像结论来自哪次回答、哪道题、哪次对话或哪条行为。
- 可以把主观判断延后到统一融合层处理。
- 可以支持增量更新，而不是每次从零生成画像。
- 可以在前端展示“证据采集簿”和低置信度维度。

## 4. 后端 API

画像 API 在 `backend/app/api/profile.py`，路由前缀为 `/api/profile`。

| 接口 | 方法 | 用途 |
| --- | --- | --- |
| `/api/profile` | GET | 读取当前登录用户画像 |
| `/api/profile/{user_id}` | GET | 读取指定用户画像，带权限校验 |
| `/api/profile/narrative` | GET | 读取当前用户画像叙事 |
| `/api/profile/{user_id}/narrative` | GET | 读取指定用户画像叙事 |
| `/api/profile/cold-start` | POST | 冷启动生成画像 |
| `/api/profile/session/start` | POST | 开始一次画像重塑会话 |
| `/api/profile/session/answer` | POST | 提交画像会话某一步回答 |
| `/api/profile/session/finish` | POST | 结束画像会话并持久化 |
| `/api/profile/dialogue/chat` | POST | 对话式画像 SSE 流 |

权限逻辑：

- 所有接口都通过 `get_current_user` 获取当前用户。
- 如果请求里带了 `user_id`，后端会校验是否等于当前用户。
- 不允许跨用户读取或重塑画像。

## 5. 存储结构

SQLite 持久化逻辑在 `backend/app/profile/store.py`。数据库路径为：

```text
backend/app/.data/qihuang.db
```

主要表：

| 表 | 用途 |
| --- | --- |
| `profile` | 存储用户最终 Profile JSON |
| `evidence` | 存储 EvidenceCard |
| `profile_sessions` | 存储画像重塑会话状态 |
| `chat_sessions` | 存储对话式画像聊天消息 |
| `profile_update_state` | 存储各维度增量更新状态 |

连接时启用：

- WAL 模式。
- `foreign_keys=ON`。
- `busy_timeout=5000`。
- `synchronous=NORMAL`。

读取画像时，如果用户还没有画像，会返回一个空 Profile，保证前端不用处理空对象。

## 6. 画像重塑入口

### 6.1 冷启动画像

冷启动入口是 `/api/profile/cold-start`，核心实现：

- `backend/app/profile/agent.py`
- `backend/app/profile/nodes.py`

冷启动使用 LangGraph 节点链路：

```text
profile_router
-> dialogue_extract_agent
-> diagnostic_question_agent
-> diagnostic_eval_agent
-> profile_synthesis_agent
-> profile_persist_agent
```

主要步骤：

1. `profile_router` 判断用户输入是否属于画像相关意图。
2. `dialogue_extract_agent` 从第一句话中抽取学习目标、基础水平、学习风格、薄弱点、动机、资源偏好、节奏等线索。
3. `diagnostic_question_agent` 从中药知识库生成诊断题。
4. `diagnostic_eval_agent` 根据诊断题答案产生知识基础和薄弱点证据。
5. `profile_synthesis_agent` 调用评分层生成七维画像。
6. `profile_persist_agent` 持久化证据和画像。

对话抽取有严格追溯要求：LLM 抽取出的结论必须能在原始话术中找到对应片段，否则会被丢弃。没有 LLM 或 LLM 输出不合法时，会退化到规则抽取。

### 6.2 会话式重塑画像

会话模式定义在 `backend/app/profile/modes.py`。

支持五种模式：

| mode | 名称 | 入口场景 | 特点 |
| --- | --- | --- | --- |
| `apprentice` | 收徒游戏 | 完整引导式画像 | 多幕剧情，覆盖多维证据 |
| `quickcard` | 身份卡+30秒速判 | 快速画像 | 身份卡加少量诊断题 |
| `practice` | 开药实战 | 处方能力画像 | 重点影响知识基础和薄弱点 |
| `passive` | 纯被动埋点 | 用户跳过问答 | 用点击、停留、跳过等行为生成弱证据 |
| `dialogue` | 对话式画像 | 低压力问诊式画像 | SSE 流式对话，实时更新雷达和命格卡 |

会话公共流程：

1. 前端调用 `startProfileSession(mode)`。
2. 后端创建 `ProfileSession`，返回第一步。
3. 前端按 step 渲染问题或控件。
4. 用户提交回答，调用 `answerProfileSession`。
5. 后端把回答转成 EvidenceCard，推进下一步。
6. 完成后调用 `finishProfileSession`。
7. 后端持久化证据，融合画像，返回雷达变化和总结。

## 7. apprentice：收徒游戏画像

`apprentice` 是最完整的重塑路径，步骤如下：

| step id | 名称 | 采集内容 | 影响维度 |
| --- | --- | --- | --- |
| `apprentice_prologue` | 序幕·叩问初心 | 身份卡选择 | 学习目标、动机、知识基础 |
| `apprentice_act1_quiz` | 一幕·药圃初认 | 诊断题 | 知识基础、薄弱点、学习风格 |
| `apprentice_act2_prescription` | 二幕·初次抓药 | 开方判断 | 知识基础、薄弱点 |
| `apprentice_act3_resource` | 三幕·师父药宝 | 资源偏好选择 | 资源类型偏好 |
| `apprentice_act4_rule` | 四幕·立下学规 | 学习时长、目标周期、挑战意愿 | 学习节奏、动机 |

身份卡映射：

| 身份卡 | 学习目标 | 动机 |
| --- | --- | --- |
| 考前冲刺 | 应试提分 | 外驱 |
| 临床学徒 | 临床实用 | 内驱 |
| 草木好奇者 | 兴趣科普 | 内驱 |
| 研读派 | 学术深造 | 内外混合 |

身份卡对知识基础也会产生低权重先验，默认权重较低，避免用户随手选择直接决定最终画像。

## 8. quickcard：身份卡+30秒速判

`quickcard` 是快速画像入口，适合用户不愿完成完整问答时使用。

它包含：

- 身份卡选择。
- 少量诊断题。
- 时间或快速判断信号。

产生的证据通常比 apprentice 少，因此置信度较低。后续会通过增量事件继续补全画像。

## 9. practice：开药实战画像

`practice` 主要围绕处方判断，核心函数是 `_prescription_card`。

客观信号字段：

| 字段 | 含义 |
| --- | --- |
| `对症` | 药物是否对应病例关键词 |
| `配伍` | 药物搭配是否合理 |
| `剂量` | 剂量是否填写且在合理区间 |
| `误用毒烈` | 是否错误使用毒烈药或剂量过大 |

规则示例：

- 剂量必须存在，且通常在 1 到 30g 区间。
- 出现毒烈药且剂量过高，会触发 `误用毒烈`。
- 对症、配伍、剂量失败都会形成薄弱点。
- 处方证据对 `knowledge_base` 权重较高，默认约 1.5。

该模式直接体现“能不能把知识用于处方场景”，所以比身份卡更接近能力判断。

## 10. passive：被动埋点画像

`passive` 用于用户跳过画像流程或不愿填写问答时。

事件映射：

| 行为 | 影响维度 |
| --- | --- |
| 重看、replay、跳过、skip | 薄弱点 |
| 带 `resource_type` 的行为 | 资源类型偏好 |
| 停留、点击、stay、click | 动机 |
| 其他弱行为 | 学习风格 |

被动证据的权重较低，主要用于“先点亮一点画像”，不应该直接形成高置信结论。

## 11. dialogue：对话式画像

对话式画像是当前最复杂的重塑路径，后端仍在 `backend/app/profile/modes.py`，前端主要在：

- `frontend/src/views/OnboardingView.vue`
- `frontend/src/components/profile/ProfileDialogue.vue`
- `frontend/src/components/profile/profileDialogueStreamState.ts`

### 11.1 对话目标

对话开场问题：

```text
先随便聊聊：你最近为什么想学中医药？是为了考试、临床、研究，还是单纯好奇？
```

对话最少轮数：

```text
DIALOGUE_MIN_TURNS = 3
```

主要收敛目标：

- `learning_goal`
- `motivation`

只有达到最少轮数，并且这两个核心维度置信度都达到 `0.5`，才认为对话画像可以收敛。

### 11.2 目标选择

每轮回答后，后端会：

1. 抽取当前回答中的证据卡。
2. 将新证据临时加入当前 session。
3. 用对话专用融合参数计算各维度置信度。
4. 找到置信度最低、最需要补问的维度。
5. 生成下一轮问题。

对话场景下置信度增长更快：

```text
DIALOGUE_CONFIDENCE_WEIGHT_K = 1.5
```

相比全局融合默认 K=3.0，对话里少量高质量回答就可以让维度先进入“已取证”状态。

### 11.3 SSE 流式事件

`/api/profile/dialogue/chat` 返回 SSE。前端通过 `streamProfileDialogueChat` 消费。

典型事件顺序：

```text
thinking
message
message_done
evidence
identity_cards
radar
state
done
```

事件含义：

| event | 含义 |
| --- | --- |
| `thinking` | 模型推理流，用于前端显示思考中 |
| `message` | 可见助手回复片段 |
| `message_done` | 可见回复结束 |
| `evidence` | 本轮抽取到的证据卡 |
| `identity_cards` | 七维命格卡状态 |
| `radar` | 当前画像雷达 |
| `state` | 对话状态、目标维度、进度 |
| `error` | 错误事件 |
| `done` | 本轮流结束 |

设计上先输出可见回复，再输出画像证据，避免用户感知为“系统只在后台打分，不在认真回应”。

### 11.4 对话证据面板

后端会生成 evidence panel，按维度归类：

- 当前值或标签。
- 分布。
- 分数。
- 置信度。
- 证据引用。
- 原话片段。

前端将这些内容展示为“证据采集簿”。

### 11.5 命格卡

对话式画像会把七维画像渲染成“命格牌阵”。

部分命名规则：

| 维度 | 标签或分段 | 展示名称 |
| --- | --- | --- |
| `motivation` | 内驱 | 求道者 |
| `motivation` | 外驱 | 赶考人 |
| `motivation` | 内外混合 | 双修者 |
| `motivation` | 被动 | 随缘客 |
| `resource_preference` | 知识图谱 | 观脉者 |
| `resource_preference` | 拟人化短剧 | 入戏郎 |
| `resource_preference` | 汤头歌诀 | 诵诀生 |
| `resource_preference` | 医案文选 | 读案客 |
| `learning_style` | 视觉型 | 观象者 |
| `learning_style` | 文字型 | 研文客 |
| `learning_style` | 记忆(口诀)型 | 记颂客 |

知识基础按分数映射：

| 分段 | 展示名称 |
| --- | --- |
| 零基础 | 药童 |
| 入门 | 学徒 |
| 一般 | 坐堂医 |
| 较好 | 杏林手 |
| 熟练 | 大医 |

学习节奏锚点：

| 分数 | 展示名称 |
| --- | --- |
| 20 | 文火慢炖 |
| 55 | 温火常煎 |
| 85 | 武火急煎 |

命格卡是否揭示取决于置信度。达到阈值后会保持揭示，不会因为后续置信度短暂变化而回退。

## 12. 各画像维度的重塑办法和标准

### 12.1 知识基础 `knowledge_base`

类型：数值型，0 到 100。

主要证据来源：

| 来源 | 证据信号 |
| --- | --- |
| 诊断题 | 正误、是否放弃、题目难度、知识点 |
| 开药实战 | 对症、配伍、剂量、误用毒烈 |
| 身份卡 | 基础水平先验 |
| 对话 | 用户自述基础、掌握程度、学习经历 |
| 冷启动诊断 | 诊断题答案 |

规则映射：

| 信号 | 分数倾向 |
| --- | --- |
| `correct=true` | 100 |
| `correct=false` | 0 |
| `gave_up=true` | 20 |
| `accuracy` / `correct_rate` | 按比例转成 0-100 |
| `base_level=零基础` | 10 |
| `base_level=入门` | 30 |
| `base_level=一般` | 50 |
| `base_level=较好` | 70 |
| `base_level=熟练` | 85 |

处方场景中，如果对症、配伍、剂量都较好且没有误用毒烈，知识基础会上升。若出现误用毒烈，会被护栏压制。

护栏：

- 没有证据时，高分不能超过 50。
- 正确率 0% 时，高分上限压到 35。
- 正确率 100% 时，低分至少到 60。
- 出现 `误用毒烈=true` 时，高分上限压到 55。
- 剂量错误时，高分上限压到 80。

时间衰减：

- 半衰期 30 天。
- 较新的答题和处方证据权重更高。

### 12.2 学习目标 `learning_goal`

类型：分类型。

允许标签：

- 应试提分
- 临床实用
- 兴趣科普
- 学术深造
- 其他

主要证据来源：

| 来源 | 信号 |
| --- | --- |
| 身份卡 | 考前冲刺、临床学徒、草木好奇者、研读派 |
| 对话 | “考试”“临床”“好奇”“研究”等表达 |
| 交互事件 | 目标相关选择 |
| 冷启动 | 第一句话中的学习目的 |

融合方式：

- 每张证据卡对某个标签投票。
- 归一化后形成 `distribution`。
- 权重最高的标签作为最终 `label`。
- 雷达上的“目标清晰度”来自分布集中度，某一目标越集中，分数越高。

特殊规则：

- 如果用户表达了目标，但不属于四个标准标签，可以落入“其他”。
- “其他”必须有明确证据，不是默认兜底结论。
- 非允许标签会被护栏过滤。

### 12.3 学习风格 `learning_style`

类型：分类型。

允许标签：

- 视觉型
- 文字型
- 记忆(口诀)型

主要证据来源：

| 来源 | 信号 |
| --- | --- |
| 诊断题 | 题型和答题线索 |
| 对话 | 用户自述喜欢图、文字、口诀 |
| 被动埋点 | 用户停留的内容形态 |
| 资源选择 | 间接偏好 |

融合方式：

- 与学习目标类似，采用分类投票。
- `distribution` 表示各风格权重。
- 雷达分数是偏好集中度，而不是“能力高低”。

注意：

- 学习风格不是优劣判断。
- 多种风格分布接近时，雷达值会低一些，表示风格尚未稳定或证据不足。

### 12.4 薄弱点 `weak_points`

类型：混合型，核心是条目列表。

主要证据来源：

| 来源 | 信号 |
| --- | --- |
| 错题 | topic、knowledge_point、label |
| 放弃题 | gave_up |
| 开药失败 | 对症失败、配伍失败、剂量失败、误用毒烈 |
| 对话 | 用户自述薄弱点，且必须可追溯到原话 |
| 被动埋点 | 重看、跳过等行为 |

生成标准：

- 错题和放弃题会生成薄弱点证据。
- 处方中的失败项会生成弱点标签。
- 对话抽取出的薄弱点必须有原文片段支撑。
- 后端按标签聚合强度。

护栏：

- `weak_points` 必须来自错误、失败或明确自述证据。
- 没有证据支撑的薄弱点会被删除。
- LLM 不能凭空新增薄弱点。

雷达含义：

- 这里的雷达值不是“越高越好”，而是“薄弱热区强度”。
- 薄弱点越集中、越多，热区值越高。
- 前端会额外展示薄弱点列表，避免只看雷达误读。

### 12.5 学习节奏 `pace`

类型：数值型，0 到 100。

主要证据来源：

| 来源 | 信号 |
| --- | --- |
| 学规步骤 | 每日学习分钟数、目标天数 |
| 对话 | 用户自述学习频率和期限 |
| 被动行为 | 后续可扩展 |

分数标准：

| 信号 | 分数倾向 |
| --- | --- |
| `daily_minutes < 15` | 20 |
| `15 <= daily_minutes <= 40` | 55 |
| `daily_minutes > 40` | 85 |
| `target_days <= 14` | 85 |
| `target_days <= 45` | 55 |
| `target_days > 45` | 25 |

护栏区间：

| 条件 | 允许区间 |
| --- | --- |
| 每日少于 15 分钟 | 0-30 |
| 每日 15-40 分钟 | 31-70 |
| 每日大于 40 分钟 | 71-100 |
| 目标周期小于等于 14 天 | 71-100 |
| 目标周期大于 60 天 | 0-30 |

时间衰减：

- `pace` 不设置半衰期。
- 当前规则认为学习节奏是用户计划状态，不像知识答题那样天然过期。

### 12.6 动机 `motivation`

类型：混合型，包含分数和标签。

允许标签：

- 内驱
- 外驱
- 内外混合
- 被动

主要证据来源：

| 来源 | 信号 |
| --- | --- |
| 身份卡 | 初始动机先验 |
| 学规步骤 | 是否接受挑战 |
| 对话 | 学习原因、兴趣、自主性表达 |
| 被动行为 | 点击、停留等弱信号 |

标签到分数倾向：

| 标签或信号 | 分数倾向 |
| --- | --- |
| 被动、佛系、外部推动 | 35 |
| 外驱 | 50 |
| 内外混合 | 65 |
| 内驱 | 75 |
| 接受挑战 | 80 |

护栏：

- 若证据显示“佛系、被动、外部推动”，高分上限压到 55。
- 若有接受挑战或 challenge 信号，低分至少到 55。

融合方式：

- 同时融合数值分和标签分布。
- 最终既有 `score`，也可能有 `label` 和 `distribution`。
- 置信度会同时考虑数值证据一致性和标签集中度。

### 12.7 资源类型偏好 `resource_preference`

类型：分类型。

允许标签：

- 知识图谱
- 拟人化短剧
- 汤头歌诀
- 医案文选

主要证据来源：

| 来源 | 信号 |
| --- | --- |
| apprentice 第三幕 | 用户选择“师父药宝” |
| 对话 | 用户自述喜欢什么学习材料 |
| 被动埋点 | 浏览、点击、停留的资源类型 |

融合方式：

- 分类投票。
- 分布越集中，雷达上的“资源集中度”越高。

注意：

- 这不是判断资源好坏，而是判断当前用户更偏好哪种资源。
- 被动埋点权重通常应低于主动选择。

## 13. 融合与评分

核心文件：

- `backend/app/profile/fusion.py`
- `backend/app/profile/jury.py`
- `backend/app/profile/guardrail.py`
- `backend/app/profile/update.py`

### 13.1 规则融合

规则融合入口：

```text
fuse_profile()
fuse_dimension()
```

数值型维度公式：

```text
score = sum(value * weight * decay) / sum(weight * decay)
```

置信度公式：

```text
confidence = min(1, total_weight / K) * (1 - normalized_variance)
```

默认：

```text
K = 3.0
NUMERIC_VARIANCE_NORMALIZER = 2500
```

含义：

- 权重越多，置信度越高。
- 证据越矛盾，方差越高，置信度越低。
- 旧证据在有半衰期的维度上会衰减。

分类维度融合：

1. 从证据中读取 `label`、`votes` 或 `distribution`。
2. 按权重累计每个标签。
3. 归一化成分布。
4. 最大权重标签作为最终标签。
5. 集中度越高，置信度越高，雷达分越高。

### 13.2 时间衰减

半衰期配置：

| 维度 | 半衰期 |
| --- | --- |
| `knowledge_base` | 30 天 |
| `weak_points` | 30 天 |
| 其他维度 | 不衰减 |

衰减公式：

```text
decay = 0.5 ** (delta_days / half_life_days)
```

知识和薄弱点会随时间变化，因此旧证据权重下降。目标、风格、资源偏好等暂时不做时间衰减。

### 13.3 三师评审

三师评审在 `backend/app/profile/jury.py`。

三位评审：

- 严师
- 宽师
- 中立

评审流程：

1. 每位老师基于证据给出分数或标签。
2. 数值型结果取中位数。
3. 计算分歧方差。
4. 方差越高，置信度越低。
5. 如果方差超过阈值，会触发自检重评，选择方差更低的一组。
6. 最终仍要经过护栏。

关键配置：

```text
JURY_VARIANCE_THRESHOLD = 120
JURY_CONFIDENCE_SCALE = 15
```

如果没有 LLM API key，或没有强制使用 LLM，三师评审会退化为规则融合，不会阻断画像生成。

### 13.4 数值锚点

知识基础锚点：

| 分数段 | 含义 |
| --- | --- |
| 0-20 | 药名认不全，性味归经空白 |
| 21-40 | 认识常见药名，但功效模糊 |
| 41-60 | 常用药功效掌握，配伍薄弱 |
| 61-80 | 功效和配伍较熟 |
| 81-100 | 配伍加减熟练，剂量精准 |

动机锚点：

| 分数段 | 含义 |
| --- | --- |
| 0-20 | 被动应付 |
| 21-40 | 外部推动 |
| 41-60 | 有意愿但需要目标 |
| 61-80 | 主动性强 |
| 81-100 | 高度自驱 |

节奏锚点：

| 分数段 | 含义 |
| --- | --- |
| 0-30 | 慢，每日少于 15 分钟或目标宽松 |
| 31-70 | 中，每日 15-40 分钟 |
| 71-100 | 快，每日大于 40 分钟或周期紧 |

### 13.5 护栏

护栏在 `backend/app/profile/guardrail.py`。

主要职责：

- 限制数值型画像不能超出证据允许区间。
- 过滤非法分类标签。
- 删除无证据支撑的薄弱点。
- 避免 LLM 生成没有证据的画像结论。

典型规则：

- 没证据的数值维度不能高分。
- 知识基础若出现毒烈误用，高分上限受限。
- 被动动机不能被写成高自驱。
- 学习目标、资源偏好等只能使用允许标签。
- weak_points 必须可以追溯到错题、失败项或用户原话。

## 14. 增量更新机制

增量更新入口在 `backend/app/profile/update.py`。

事件类型：

| event type | 来源 |
| --- | --- |
| `quiz` | 测验、练习题 |
| `prescription` | 开方、处方练习 |
| `interaction` | 点击、停留、资源选择、学习计划 |

更新流程：

1. 事件转 EvidenceCard。
2. 证据写入数据库。
3. 找出受影响维度。
4. 先用规则融合更新这些维度。
5. 累加该维度 pending weight。
6. pending weight 达到阈值时触发三师评审。
7. 更新 Profile 并持久化。

关键阈值：

```text
DEFAULT_JURY_WEIGHT_THRESHOLD = 2.0
DEFAULT_JURY_RESCORE_INTERVAL = 24 hours
```

含义：

- 小证据先规则融合，避免频繁调用 LLM。
- 累计足够新证据后再触发三师评审。
- 同一维度 24 小时内不重复高频重评，除非 pending weight 达到阈值。

`get_latest_profile()` 读取画像时也会检查证据和更新状态，必要时刷新维度。

## 15. 雷达图绘制原理

后端雷达生成在 `backend/app/profile/radar.py`，前端兜底计算在 `frontend/src/utils/profileMath.ts`，SVG 绘制在 `frontend/src/components/profile/RadarChart.vue`。

### 15.1 后端雷达数据

`to_radar(profile)` 输出：

- `dimensions`
- `values`
- `confidences`
- `axes`

每个 axis 包含：

| 字段 | 含义 |
| --- | --- |
| `key` | 维度 key |
| `label` | 雷达轴名称 |
| `value` | 0-100 分 |
| `confidence` | 置信度 |
| `status` | `solid`、`dashed`、`dark` |
| `lit` | 是否点亮 |
| `placeholder` | 是否仍为占位 |
| `raw_label` | 原始标签或可读值 |

低置信阈值：

```text
LOW_CONFIDENCE_THRESHOLD = 0.5
```

状态规则：

| 状态 | 含义 |
| --- | --- |
| `dark` | 尚未点亮，几乎没有证据 |
| `dashed` | 有证据但置信度不足 |
| `solid` | 置信度达到阈值 |

### 15.2 各轴取值

| 维度 | 雷达值算法 |
| --- | --- |
| `knowledge_base` | 直接使用 score |
| `learning_goal` | 最大分类分布 * 100 |
| `learning_style` | 最大分类分布 * 100 |
| `weak_points` | 薄弱点强度求和 * 25 后截断 |
| `pace` | 直接使用 score |
| `motivation` | 直接使用 score |
| `resource_preference` | 最大分类分布 * 100 |

注意：

- 分类轴表达的是“集中度”，不是好坏。
- 薄弱点轴表达的是“热区强度”，不是能力分。
- 低置信轴会以虚线或暗色展示。

### 15.3 前端 SVG 绘制

`RadarChart.vue` 使用固定 viewBox：

```text
viewBox = 420 x 420
center = 210
radius = 132
labelRadius = 173
grid levels = 20 / 40 / 60 / 80 / 100
```

每个点坐标：

```text
angle = -PI / 2 + 2PI * index / axes.length
distance = value / 100 * radius
x = center + cos(angle) * distance
y = center + sin(angle) * distance
```

渲染逻辑：

- 多边形填充代表整体画像形态。
- 某个轴低置信时，该轴或相邻边可以虚线显示。
- 未点亮轴会更暗。
- 对话完成后有变化的轴会高亮。

## 16. 弟子档案页面

核心文件：

- `frontend/src/views/ProfileView.vue`
- `frontend/src/stores/profile.ts`
- `frontend/src/services/profileApi.ts`
- `frontend/src/utils/profileMath.ts`
- `frontend/src/components/profile/RadarChart.vue`

### 16.1 数据加载

页面挂载后：

1. 调用 `profileStore.loadProfile()`。
2. 调用 `profileStore.ensureNarrativeLoaded()`。
3. 渲染雷达、画像叙事、薄弱点和统计卡片。

`profileApi.ts` 中的接口：

| 方法 | 后端接口 |
| --- | --- |
| `readProfile` | GET `/api/profile` |
| `readProfileNarrative` | GET `/api/profile/narrative` |
| `startProfileSession` | POST `/api/profile/session/start` |
| `answerProfileSession` | POST `/api/profile/session/answer` |
| `finishProfileSession` | POST `/api/profile/session/finish` |
| `streamProfileDialogueChat` | POST `/api/profile/dialogue/chat` |

画像读取超时较短，叙事读取超时较长：

- 画像读取约 800ms。
- 叙事读取约 8000ms。

失败时前端会使用 mock profile 或 mock narrative，保证页面可展示。

### 16.2 档案页展示内容

弟子档案页主要展示：

- 七维修行罗盘。
- 高置信维度数量。
- 证据卡数量。
- 低置信维度数量。
- 画像叙事。
- 优势维度。
- 薄弱点。
- 低置信提醒。
- 跳转到画像重塑页的按钮。

如果整体置信度低于阈值，或低置信维度过多，页面会提示用户去 `/onboarding?redirect=/profile` 继续补全画像。

### 16.3 Pinia Store

`frontend/src/stores/profile.ts` 负责：

- 保存 `profile`。
- 保存 `narrative`。
- 保存 loading/error 状态。
- 标记是否使用 mock。
- 计算整体置信度。
- 判断画像是否已经建立。
- 保存对话完成后的 radar changes。

整体置信度：

```text
overallConfidence = seven_dimensions_confidence_average
```

是否已经建立画像：

- 任一维度有 confidence。
- 或任一维度有 evidence。

低置信：

```text
overallConfidence < 0.5
```

## 17. 画像叙事生成

叙事逻辑在 `backend/app/profile/narrative.py`。

核心原则：**LLM 只参与选择和组织，不允许凭空创造事实**。

流程：

1. 从 Profile 构造七维事实表。
2. 低置信维度标记为“待补充”。
3. 识别优势、真实薄弱点、建议方向。
4. 可选调用 LLM 生成叙事计划。
5. 清洗 LLM 输出，只保留合法维度、合法标签、真实薄弱点。
6. 最终文本由后端模板根据受控事实渲染。

叙事输出包含：

- overview
- advantages
- weak_points
- pending_dimensions
- advice_intro
- method
- guardrails

护栏：

- 只能引用七维画像快照中的 score、label、weak_points、confidence、evidence_count。
- 低置信维度必须标为“待补充”。
- LLM 不能新增维度。
- LLM 不能新增无证据薄弱点。

## 18. 前端重塑画像页

核心文件：

- `frontend/src/views/OnboardingView.vue`
- `frontend/src/components/profile/ProfileDialogue.vue`
- `frontend/src/config/dialogueQuestions.ts`

### 18.1 模式卡片

页面标题是“焕醒你的弟子画像”。

模式卡片：

| 模式 | 前端文案 | 时长 |
| --- | --- | --- |
| `apprentice` | 收徒游戏 | 6-8min |
| `dialogue` | 对话问诊 | 2-4min |
| `practice` | 开药实战 | 3min |
| `passive` | 佛系旁观 | immediate |

### 18.2 step 渲染

`OnboardingView.vue` 根据后端返回的 `step.kind` 渲染不同输入：

- 身份卡选择。
- 诊断题。
- 处方输入。
- 资源选择。
- 学习计划。
- 被动事件。
- 对话组件。

提交时 `stepPayload()` 会把前端表单转换为后端需要的 payload。

### 18.3 对话组件

`ProfileDialogue.vue` 展示：

- 用户和助手气泡。
- 思考流。
- 快捷回复 chip。
- 输入框。
- 当前目标维度。
- 画像进度。
- 命格牌阵。
- 小雷达图。
- 证据采集簿。

`profileDialogueStreamState.ts` 负责把 SSE 事件应用到 UI 状态：

- 添加用户消息。
- 创建或复用流式助手消息。
- 追加 message chunk。
- 追加 reasoning chunk。
- 标记 message done。
- 应用 identity cards。

## 19. 前后端类型对应

前端类型在 `frontend/src/types/profile.ts`。

主要类型：

- `DimensionKey`
- `EvidenceCard`
- `WeakPoint`
- `DimensionPayload`
- `ProfileResponse`
- `ProfileNarrative`
- `RadarAxis`
- `ProfileSessionResponse`
- `ProfileDialogueStreamEvent`

这些类型基本镜像后端 schema，确保前端可以直接渲染七维画像、证据、叙事和雷达。

## 20. 测试覆盖

后端相关测试：

| 文件 | 覆盖内容 |
| --- | --- |
| `backend/tests/test_profile_update.py` | 增量更新、pending weight、24 小时节流、接口返回 confidence |
| `backend/tests/test_modes.py` | apprentice、quickcard、practice、passive、dialogue 会话流程 |
| `backend/tests/test_scoring.py` | 融合、三师评审、护栏、标签过滤、弱点追溯 |
| `backend/tests/test_radar.py` | 雷达转换 |
| `backend/tests/test_narrative.py` | 画像叙事护栏 |
| `backend/tests/test_profile_schema.py` | Profile schema |
| `backend/tests/test_store_migration.py` | 存储迁移 |
| `backend/tests/test_store_concurrency.py` | 并发存储 |

前端相关测试：

| 文件 | 覆盖内容 |
| --- | --- |
| `frontend/tests/profile-dialogue.component.spec.mjs` | 对话组件流式状态和展示 |
| onboarding 相关 spec | Onboarding 流程、模式提交、对话事件处理 |

测试中特别验证了：

- apprentice 每幕都会产出预期证据。
- quickcard 会产出身份卡和限时判断证据。
- practice 的处方证据权重较高。
- passive 能把行为转成弱证据。
- dialogue SSE 顺序稳定。
- message 先于 evidence 出现。
- 抽取失败时仍能完成可见回复。
- 对话证据必须有可追溯原话。
- 三师评审无 API key 时退化到规则融合。
- 非法标签会被过滤。
- weak_points 不能凭空生成。

## 21. 关键文件索引

### 后端

| 文件 | 作用 |
| --- | --- |
| `backend/app/api/profile.py` | 画像 API |
| `backend/app/profile/schema.py` | 七维画像 schema |
| `backend/app/profile/evidence.py` | EvidenceCard |
| `backend/app/profile/store.py` | SQLite 存储 |
| `backend/app/profile/fusion.py` | 规则融合 |
| `backend/app/profile/jury.py` | 三师评审 |
| `backend/app/profile/guardrail.py` | 分数和标签护栏 |
| `backend/app/profile/update.py` | 增量更新 |
| `backend/app/profile/radar.py` | 后端雷达转换 |
| `backend/app/profile/narrative.py` | 画像叙事 |
| `backend/app/profile/agent.py` | 冷启动 LangGraph |
| `backend/app/profile/nodes.py` | 冷启动节点 |
| `backend/app/profile/modes.py` | 重塑画像会话模式 |

### 前端

| 文件 | 作用 |
| --- | --- |
| `frontend/src/types/profile.ts` | 画像相关 TS 类型 |
| `frontend/src/services/profileApi.ts` | 画像 API 客户端 |
| `frontend/src/stores/profile.ts` | 画像状态管理 |
| `frontend/src/utils/profileMath.ts` | 前端画像计算兜底 |
| `frontend/src/views/ProfileView.vue` | 弟子档案页 |
| `frontend/src/views/OnboardingView.vue` | 重塑画像页 |
| `frontend/src/components/profile/RadarChart.vue` | 雷达图 |
| `frontend/src/components/profile/ProfileDialogue.vue` | 对话式画像组件 |
| `frontend/src/components/profile/profileDialogueStreamState.ts` | 对话 SSE UI 状态 |
| `frontend/src/config/dialogueQuestions.ts` | 对话问题配置 |
| `frontend/src/components/profile/profileIdentityCardDescriptions.ts` | 命格卡描述 |
| `frontend/src/components/profile/profileDialogueModel.ts` | 对话画像前端模型 |

## 22. 当前实现的特点

1. **证据优先**：画像不直接来自主观总结，而是来自 EvidenceCard。
2. **七维固定**：前后端都围绕七个固定维度展开。
3. **低置信可见**：低置信维度不会被隐藏，而是用虚线、待补充、暗轴提示。
4. **LLM 受控**：LLM 可以参与抽取、评审和叙事，但最终会经过规则校验和护栏。
5. **增量演化**：画像不是一次性结果，后续练习、处方、交互都会继续更新。
6. **对话实时反馈**：对话过程中就能看到证据、雷达和命格卡变化。
7. **前端有兜底**：画像和叙事读取失败时前端有 mock 展示，避免页面崩溃。

## 23. 注意点与后续维护建议

1. **分类标签要同步维护**  
   后端 schema、fusion、guardrail 和前端类型/展示都依赖同一组标签。新增标签时要同时改后端允许列表、前端类型、展示文案和测试。

2. **weak_points 必须坚持可追溯**  
   薄弱点最容易被 LLM 扩写。当前护栏要求薄弱点来自错误、失败或原话，应继续保持。

3. **雷达值不全是能力分**  
   分类维度是集中度，薄弱点是热区强度。产品文案需要避免把所有轴都解释成“越高越好”。

4. **对话模式的置信度增长更快**  
   对话使用 `DIALOGUE_CONFIDENCE_WEIGHT_K=1.5`，比全局融合更容易点亮维度。若后续觉得对话过快定性，应优先调这个参数。

5. **三师评审不是唯一真相**  
   没有 LLM 时会退化到规则融合。业务上应把三师评审理解为增强评分层，而不是必需依赖。

6. **前端 mock 可能掩盖接口问题**  
   档案页有 mock fallback。调试线上问题时要关注 `usingMock` 状态，避免把 mock 误认为真实画像。

7. **画像叙事不是原始 LLM 文本**  
   当前叙事最终由模板渲染，LLM 只给计划。这一点对风控很重要，后续不要轻易改成直接展示 LLM 长文。

