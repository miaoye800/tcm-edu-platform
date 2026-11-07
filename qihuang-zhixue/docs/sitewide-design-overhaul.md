# 全站设计改造方案

> 2026-10-29 · 基于 `pbakaus/impeccable`（93,673★，2026-10-29 仍在更新）
> 取代 `visual-refresh-plan.md` 的「选一个深色配色」思路

---

## 0. 先纠正我自己

我给你的三版深色样张（v1/v2/v3），方向是错的。impeccable 的 calibration 一节点名了
AI 生成界面的四大俗套，第二条就是：

> **near-black with one neon accent and glowing edges**
> （近黑底 + 一个霓虹强调色 + 发光边缘）

v1 是玉青细线，v2 加了经络和印章，v3 加了环形进度和更大的光晕——**三版都在这个俗套里，
只是效果加了多少的区别**。你觉得「太素」「都不是特别好」，根因不是装饰不够。

更关键的是我把 `bolder` 理解反了。impeccable 的定义是：

> "Bolder" is an amplification request... **The reflex answer, reaching for more effects,
> is the opposite of bold; reject it first.**
> **Make the one decisive move completely, then quiet everything around it so the move is legible.
> If every element got louder, the section got flatter.**

我 v3 里 Hero 有光、KPI 有渐变数字、卡片有回纹角标、侧栏有云纹、顶栏有彩线——
**每个元素都在中高音量，所以整体是平的**。这正好是它说的 "if every element got louder,
the section got flatter"。

---

## 1. impeccable 是什么

| | |
|---|---|
| 仓库 | `pbakaus/impeccable` · 93,673★ · 5,530 fork · Apache 2.0 |
| 定位 | "The design vocabulary for your coding agent" |
| 形态 | SKILL.md + 23 个命令 + Rust CLI（`npx impeccable`）+ 设计检测 hook + VS Code 扩展 |
| 关键词 | polish / bolder / critique / audit / **anti-slop** |

**它和之前那几个 skill 的根本差别：它是操作手册，不是禁令清单。**

23 个命令里，直接对应你诉求的：

| 命令 | 干什么 | 对你这个项目 |
|---|---|---|
| `bolder` | 把平庸的设计放大到它该有的音量 | 治「太素」的正解 |
| `colorize` | 给单色 UI 加策略性色彩 | 现在只有玉青一个色，缺角色分工 |
| `typeset` | 重做字体层级 | 标题现在靠宋体撑，是被点名的套路 |
| `layout` | 修间距、节奏、视觉层级 | 全站密度节奏没设计过 |
| `delight` | 加个性与记忆点 | 「不素」的合规做法 |
| `overdrive` | 突破常规限制 | 给 Experience 页面用 |
| `shape` | 写代码前先规划 UX/UI | 全站改造的第一步 |
| `document` | 从现有代码生成 DESIGN.md | 项目现在没有设计契约 |
| `critique` / `audit` / `polish` | 评审 / 技术审计 / 出货前打磨 | 逐页收口 |
| `onboard` | 首运行、空状态、激活流程 | onboarding 和所有空状态 |

---

## 2. 核心洞察：不是所有页面都该「不素」

这是我之前完全搞错的地方。impeccable 把界面按**访客的成功是什么**分成四种模式：

| 模式 | 访客在做什么 | 设计要求 |
|---|---|---|
| **Persuade** | 做决定、采取行动 | 争取注意力，设计就是产品 |
| **Operate** | 完成一个任务 | **可扫描性、一致性优先；品牌活在细节里** |
| **Read** | 理解某个东西 | 为理解而结构化，阅读体验值得停留 |
| **Experience** | 人就在作品里 | **让内容主导，界面退后** |

而且它明确写了 Operate 的底线：

> **Product UI's failure mode isn't flatness, it's strangeness without purpose.**
> Product defaults to **Restrained**. Accent color used for primary actions, current selection,
> and state indicators only, **not decoration**.
> **Familiarity is often a feature here.**

**所以「素」在仪表盘上不是缺陷，是要求。** 你现在的问题是：
**18 个页面全部套同一个 app shell，全部按 Operate 的克制标准做，没有一页承担表达职责。**
这才是「全站都素」的真正原因——不是配色问题，是**模式单一**。

---

## 3. 18 个路由的模式分派

现状取自 `src/router/index.ts` 的 `meta.mode`（`embedded` / `fullscreen`）。

### 该保持 Operate（克制是对的）

| 路由 | 页面 | 现在 | 判断 |
|---|---|---|---|
| `/dashboard` | 总览仪表盘 | embedded | Operate ✅ 保持克制 |
| `/quiz` | 题库测评 | embedded | Operate ✅ |
| `/mistakes` | 错题本 | embedded | Operate ✅ |
| `/review` | 复习计划 | embedded | Operate ✅ |
| `/resources` | 我的资源库 | embedded | Operate ✅ |
| `/resources/:id` | 资源查看 | embedded | Read（正文可读性优先） |
| `/assessment/:nodeId` | 过关试炼 | fullscreen | Operate + 专注模式（去掉一切干扰） |

### 该脱离 app shell（这是「不定死 app shell」的正解）

| 路由 | 页面 | 现在 | 应改为 | 理由 |
|---|---|---|---|---|
| `/path` | 闯关地图 | **embedded** | **Experience 全屏** | 地图本身是作品，塞进 236px 侧栏旁边就废了 |
| `/atlas` | 中药图谱 | **embedded** | **Experience 全屏** | 同上，图谱需要整块画布 |
| `/profile` | 弟子档案 | **embedded** | **Experience** | 这是学习成果的展示面，不是设置页 |
| `/apprentice` | 收徒剧情 | fullscreen ✅ | Experience ✅ 已对 | — |
| `/theater` | 漫剧剧场 | embedded | Experience 全屏 | 未上线，规划时就别套壳 |
| `/clinic` | 开药游戏 | embedded | Experience 全屏 | 同上 |

### 该按 Persuade 做（现在完全没做）

| 路由 | 页面 | 判断 |
|---|---|---|
| `/login` | 登录 | **Persuade** —— 用户第一次见到这个产品，现在是最弱的一页 |
| `/onboarding` | 焕醒画像 | **Persuade** —— 说服用户投入 5 分钟做画像，决定后续全部体验 |

---

## 4. 色彩策略：按模式分档，不是一个方案套全站

impeccable 给了四档策略：

| 策略 | 定义 | 适用 |
|---|---|---|
| **Restrained** | 中性色（带世界色调）+ 一个强调色 | Operate / Read 的底线 |
| **Committed** | **一个饱和色承担 30–60% 的画面** | Operate 的个别页面、Experience |
| **Full palette** | 3–4 个命名角色 | Persuade / Experience |
| **Drenched** | 整个画面就是那个颜色 | Persuade 的首屏 |

原文一句关键：

> **Color commits at page scale: fields that own whole regions,
> not accents scattered over a neutral ground.**

**我 v1–v3 全是「中性底上撒一点玉色」= Restrained。** 这就是素的机制性原因。
仪表盘保持 Restrained 没问题，但 `/path` `/atlas` `/login` `/onboarding` 必须升到
Committed 或 Drenched，否则全站永远是一个音量。

---

## 5. 七个候选视觉世界

按 impeccable 的推导法：从**受众的文化世界**里列 7 个具体的器物/场所/仪式，
每个说清「为什么共鸣」和「长什么样（底与主色、字体声音、一个标志性特征）」，
且**必须跨 3 个以上材质家族**——否则说明推导停在了题材最显眼的那个东西上。

我第一版列出来 7 个里有 5 个是「纸」（宣纸、线装、标本册、节气历、药包），
这正是它警告的失败模式，推倒重来。下面这版跨 **木 / 铜 / 陶瓷 / 石 / 纸 / 织物** 六个家族。

| # | 视觉世界 | 材质 | 为什么能承载这个产品 | 底与主色 | 字体声音 | 标志性特征 |
|---|---|---|---|---|---|---|
| **1** | **百子柜** | 木 + 黄铜 | 一整面墙的抽屉，每格一个药名。这是「分类与检索」的终极物理形态，正好对应知识库和图谱 | 深栗木 `#2a1f18` · 标签米白 `#e8dcc8` · 铜拉手 `#a8874e` | 手写标签体（抓药先生写的楷书），不是印刷宋 | 网格化抽屉阵列，每格黄铜小拉环；拉开时露出药材剖面 |
| **2** | **针灸铜人** | 青铜 | 北宋王惟一的铜人是中医史上第一个教学模型：外壳可开合、内藏脏腑、表面刻经络穴位，考试时灌水银看是否漏 | 青铜氧化 `#3d4a42` / `#5a6b5f` · 水银冷银 `#b8c4c0` · 穴位朱点 `#a8443a` | 铭文感：字距紧、笔画硬、近乎无衬线 | 人体轮廓上的经络刻线 + 穴位圆点；可开合的内脏层 |
| **3** | **戥子与药碾** | 黄铜 + 角质 | 戥子是称药的小秤，秤杆上有骨质秤星。「剂量精确」是中医区别于其他医学的核心，也是最难教的部分 | 黄铜 `#9a7b3f` · 角质秤星米白 `#ddd0b8` · 铁碾冷灰 `#4a4a48` | 刻度感：等宽、极小字号、精确对齐 | 秤杆上的星点阵列、刻度线、药碾的同心圆纹 |
| **4** | **青瓷药罐** | 陶瓷釉 | 龙泉粉青、越窑秘色——中药储存的容器。釉色的「温润」是东方审美的技术核心，和现在这个荧光绿完全是两个世界 | 粉青 `#a8c4b4` · 梅子青 `#6f8f7a` · 开片纹 `#d6e3da` | 圆润、无锐角，笔画收尾柔和 | 冰裂纹/开片、釉面高光、器形的柔和曲线 |
| **5** | **医案手稿** | 纸 + 墨 + 朱砂 | 中医真实的知识形态是医案：手写、有涂改、有朱笔批注。比任何「知识图谱」都更接近传承的真相 | 宣纸 `#f2ece0` · 墨 `#1c1917` · 朱砂批注 `#b5443a` · 靛蓝 `#2c4a6e` | 行楷（不是宋体）· 批注用朱笔小字 | 朱丝栏（红色竖格）、行间小字批注、压角印章 |
| **6** | **碑拓** | 石 + 拓印 | 拓片是**黑底白字**——现存最古老的「深色模式」。它天生适配暗色，而且视觉冲击极强，完全不是「近黑+霓虹」 | 拓墨 `#141210` · 纸白 `#e6e0d2` · 石花 `#8a8272` | 魏碑/汉隶：方笔、粗重、结构紧 | 黑底白字的反白、石花的斑驳、边款的细字 |
| **7** | **药囊锦缎** | 织物 | 香囊、药枕、锦缎——中医离日常生活最近的一面。织物的经纬和纹样是现成的网格系统 | 锦缎靛 `#2b3d5c` · 织金 `#c9a227` · 绛红 `#8e3a34` | 织造感：横平竖直，笔画等粗 | 经纬交织的纹理、云纹/回纹的连续图案、盘扣 |

**我的倾向：2（针灸铜人）或 6（碑拓）。**
理由：这两个都不在「宣纸 + 宋体 + 墨绿」这个一眼可猜的套路里；铜人的「教学模型」属性
和平台定位直接对应；碑拓的「黑底白字」天然解决暗色模式，且从未在同类产品里出现过。

但这是你的判断，不是我的——所以下面第 8 节我会问你要哪个。

---

## 6. 字体：两个必须避开的陷阱

**陷阱一：AI 默认字体清单。** impeccable 点名了 16 个「训练数据默认」：
Fraunces、Playfair Display、Cormorant、Lora、Crimson、Newsreader、Syne、Space Grotesk、
Space Mono、IBM Plex、Inter-as-display、DM Sans、DM Serif、Outfit、Plus Jakarta Sans、
Instrument Sans。用它们说明没在思考。

**陷阱二：题材联想。** 原文：

> "books wanting a serif, bookshops wanting hand-lettering, and tech wanting a mono
> are the associations the list exists to break"

**我 v2/v3 用宋体做标题 + 楷体做品牌名，正是「中医 → 书卷 → 宋体」这个联想。**
要换：字体应该从选定的**材质**出发（铜人的铭文、碑拓的魏碑、戥子的刻度），
不是从「中医应该文雅」出发。

---

## 7. 执行路径

| 阶段 | 做什么 | 产出 |
|---|---|---|
| **S0 立契约** | `document` 命令从现有代码生成 `DESIGN.md`，把现在真实在用的 token/组件/规则写下来 | `docs/DESIGN.md` |
| **S1 定世界** | 从上面 7 个里锁定 1 个，写 `## Direction contract`（THESIS / OWN-WORLD / STORY / FIRST VIEWPORT / FORM） | 契约进 DESIGN.md |
| **S2 定模式** | 按第 3 节把 18 个路由重新分派，改 `meta.mode` | 路由表 + 布局壳 |
| **S3 试点两页** | `/login`（Persuade，最弱的一页）+ `/path`（Experience，脱离 app shell） | 两个真实页面 |
| **S4 铺开** | 按模式分批：Experience 三页 → Operate 七页 → Read 一页 | 逐页 `polish` |
| **S5 收口** | `critique` 逐页打分 + `audit` 技术审计 + `polish` | 验收报告 |

**先做 S3 两页，不是全站一起动。** 这两页分别是「最弱」和「最该脱离壳」的代表，
做完你能立刻判断世界选对没有，避免全站返工。

---

## 8. 需要你定的三件事

1. **视觉世界选哪个？** 我倾向 2（针灸铜人）或 6（碑拓），但你可能有更对的直觉。
2. **`/path` 和 `/atlas` 脱离 app shell 全屏化，同意吗？** 这是「不定死 app shell」最实质的一步。
3. **浅色还做主设计吗？** 如果选碑拓，暗色反而是它的原生形态（拓片就是黑底白字），
   主次可能要反过来。这个决定会改第 4 节的色彩策略。
