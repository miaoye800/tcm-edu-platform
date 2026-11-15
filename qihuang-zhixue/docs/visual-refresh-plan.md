# 全站观感更新计划（Visual Refresh）

> 状态：待你确认方向后执行。本文只做诊断与方案，不改动任何视觉代码。
> 日期：2026-10-29

---

## 0. 一句话

把**浅色升为唯一的设计基准**（现在它是补丁），把**深色推倒重做**（现在它是默认值堆出来的），
底层用三层 token 替代现在 198 个平铺变量，并把 344 处硬编码颜色收回 token。

---

## 1. 现状诊断（都是实测数字，不是感觉）

### 1.1 主题机制是反的

```
:root                     ← 暗色，198 个变量，是「默认」
[data-qh-theme='light']   ← 浅色，只覆盖 49 个
```

**115 个颜色类 token 在浅色下根本没有定义**，仍然沿用暗色值。
这就是「浅色有些地方没适配」的根因 —— 不是忘了调，是结构上没覆盖到。

典型漏网：`--qh-color-jade`、`--qh-gold`、`--qh-color-paper`、
`--qh-graph-*`（全部 60+ 个图谱变量）、`--qh-story-*`、`--qh-button-*`。

### 1.2 硬编码颜色 344 处，40 个文件

`check-tokens.mjs` 把 `components/`、`views/`、`config/` 整目录豁免了，
所以这些地方的颜色不响应主题。

| 目录 | 处数 |
|---|---|
| `components/profile/` | 85 |
| `views/MeridianDemo.vue`（dev 演示页） | 53 |
| `components/learning/` | 30 |
| `views/AssessmentView.vue` | 24 |
| `config/alchemy-stage.ts` | 22 |
| `views/PathView.vue` | 19 |
| `styles/story.scss` | 16 |
| `components/layout/` | 10 |

> 注：`MeridianDemo` / `Stage4Lab` / `AlchemyDemo` 是开发用的演示页，
> 可以只保证「不报错」不追求观感。真正要修的是 profile / learning / assessment / path。
>
> 上表是当时的审计快照。其中 `views/PathView.vue` 与 `MindmapThemesDemo.vue`
> 已删除（前者被 `MeridianMapView` 取代，后者是退役的版式演示页），这两行的处数不再可复算。

### 1.3 知识图谱是「浅色孤岛」

`--qh-graph-*` 60 多个变量只在 `:root` 定义，暗色下它们用的仍是**浅蓝背景 `#c9e3fe`**。
所以暗色模式里会出现一块发亮的浅蓝卡片 —— 这是最刺眼的适配缺口。
（这也和上一轮刚定稿的 `graph-visual-spec.md` 有关，改深色时要给它补一套暗色采样值。）

### 1.4 深色为什么丑

不是配色丑，是**手法堆叠**：

- `--qh-page-bg` 叠了 4 层（3 个径向光斑 + 1 个线性渐变）
- `--qh-card-fill` 是半透明渐变 + `backdrop-filter: blur(16px)` + 内外两层阴影
- 结果：卡片边界靠「玻璃反光」而不是层级差，暗底上看着发糊、发脏

对比度实测（按卡片中段 `#1f3230` 计算，不是估计值）：

| 用途 | 变量 | 合成色 | 对比度 | 判定 |
|---|---|---|---|---|
| 正文 | `--qh-text` | `#eef5ef` | 12.16 | AAA |
| 正文 0.82 | `rgba(238,245,239,.82)` | `#c9d2cd` | 8.72 | AAA |
| 次要文字 | `--qh-text-muted` 0.60 | `#92a29c` | 5.05 | 仅 AA |
| 极淡文字 | `--qh-color-text-faint` 0.16 | `#40514f` | 1.61 | 不达标 |

结论：正文没问题，**次要文字停在 AA、极淡文字只有 1.61**。
深色重做时把次要文字提到 ≥ 7:1，极淡文字只许用在纯装饰位。

### 1.5 两个结构性隐患

**① 首屏必然闪一下主题。** `index.html` 里没有任何 pre-hydration 脚本，
`initializeTheme()` 在 `main.ts` 里才执行 —— 页面先按默认色渲染，JS 跑起来才切。
现在默认是暗色，用户感知不明显；一旦默认改成浅色，暗色用户每次刷新都会闪一下。
S1 必须补 `<head>` 内的内联脚本。

**② Tailwind 其实已经装了。** `tailwindcss 3.4.17` 在依赖里，但只 `@tailwind utilities`
（`src/styles/tailwind.css` 仅 2 行，无 reset 无 component 层），专供 Stage 4 页面用。
所以本计划的口径是：**维持现状，不扩大 Tailwind 使用面**，主体系仍是 SCSS token。

### 1.6 本轮已完成

- 右下角「小岐」悬浮助手已移除（`App.vue` + `MascotBubble.vue` + 残留样式），
  契约测试同步改为「已退休」断言。

---

## 2. 目标与非目标

### 目标

1. **浅色成为主设计**：`index.html` 默认浅色，首次访问即浅色；暗色是可选项。
2. **浅色 100% 适配**：198 个 token 全部有浅色定义，0 处「浅色下仍是暗色值」。
3. **深色重做为独立设计**，不是浅色反相。
4. **双主题可自动验证**：脚本能扫出「未定义的 token」「硬编码颜色」「对比度不达标」。
5. 图谱、故事、炼药等特殊场景都有对应主题，不再出现「浅色孤岛」。

### 非目标

- 不改信息架构、不改路由、不改交互流程。
- **不扩大 Tailwind 使用面**（已装 3.4.17，但只用于 Stage 4 的 `@tailwind utilities`；
  主体系继续是 SCSS token + Element Plus）。
- 不重做故事模块（`story.scss` 自成一套纸感体系，单独保留）。
- 不动 dev 演示页的观感（只保证不崩）。

---

## 3. 架构：把 198 个平铺变量拆成三层

现在是「一层平铺」，语义和原始色混在一起，所以没法系统化适配。改成三层：

```scss
/* ── Tier 1：原始色阶，与主题无关，永远不变 ───────────────── */
:root {
  --qh-jade-50:  #eef8f3;  --qh-jade-300: #8fd0ad;
  --qh-jade-500: #3f9c78;  --qh-jade-700: #2a6b53;  --qh-jade-900: #16382c;
  --qh-ink-0:    #ffffff;  --qh-ink-50:  #f8faf9;  --qh-ink-100: #eef2f0;
  --qh-ink-300:  #c8d3ce;  --qh-ink-500: #7a8a83;  --qh-ink-700: #3d4b45;
  --qh-ink-900:  #16211d;  --qh-ink-950: #0c1310;
  --qh-gold-300: #e7c274;  --qh-gold-600: #a76b08;
  --qh-coral-400:#e9a79c;  --qh-coral-600: #c46a5d;
}

/* ── Tier 2：语义层，主题切换只动这一层 ────────────────────── */
:root, [data-qh-theme='light'] {
  --qh-surface-page:    var(--qh-ink-50);
  --qh-surface-card:    var(--qh-ink-0);
  --qh-surface-sunken:  var(--qh-ink-100);
  --qh-surface-raised:  var(--qh-ink-0);
  --qh-text-primary:    var(--qh-ink-900);
  --qh-text-secondary:  var(--qh-ink-700);
  --qh-text-muted:      var(--qh-ink-500);
  --qh-border-subtle:   color-mix(in srgb, var(--qh-ink-900) 8%, transparent);
  --qh-border-strong:   color-mix(in srgb, var(--qh-ink-900) 16%, transparent);
  --qh-accent:          var(--qh-jade-500);
  --qh-accent-quiet:    var(--qh-jade-50);
  --qh-shadow-card:     0 1px 2px rgb(0 0 0 / .04), 0 8px 24px rgb(0 0 0 / .06);
}

[data-qh-theme='dark'] {
  --qh-surface-page:    var(--qh-ink-950);
  --qh-surface-card:    #131b18;
  --qh-surface-sunken:  #0f1614;
  --qh-surface-raised:  #1a2420;
  --qh-text-primary:    #e6efe9;
  --qh-text-secondary:  #a9bbb2;
  --qh-text-muted:      #7a8a83;
  --qh-border-subtle:   color-mix(in srgb, #ffffff 8%, transparent);
  --qh-border-strong:   color-mix(in srgb, #ffffff 16%, transparent);
  --qh-accent:          var(--qh-jade-300);
  --qh-accent-quiet:    color-mix(in srgb, var(--qh-jade-300) 14%, transparent);
  --qh-shadow-card:     0 1px 2px rgb(0 0 0 / .4), 0 12px 32px rgb(0 0 0 / .35);
}

/* ── Tier 3：组件层，只在必要时存在 ───────────────────────── */
:root { --qh-nav-item-active-bg: var(--qh-accent-quiet); }
```

**收益**：深色只需写 ~40 行语义映射，而不是复制 198 个变量；
新增组件只能引用 Tier 2，天然双主题。

---

## 4. 深色方向候选（选 1 个）

以下 star 数取自 GitHub API，查询时间 2026-10-29。

| 方案 | 参考项目（star） | 视觉特征 | 适合 | 风险 |
|---|---|---|---|---|
| **A 中性冷灰** | `satnaing/shadcn-admin` (16.3k)<br>`tabler/tabler` (42.0k) | 近黑中性底 `#0b0d0e`，1px 细边框划分层级，**不用玻璃/不用阴影**，圆角 8–10px | 最稳、最现代、最好维护；和你现在的「去装饰」图谱语言同源 | 会显得「像所有 shadcn 项目」，品牌感弱 |
| **B 近黑高对比** | `twentyhq/twenty` (60.0k)<br>`makeplane/plane` (61.6k) | `#09090b` 底 + 单一锐利强调色，字重更粗、留白更大、信息密度高 | 现代 SaaS 感强，暗色最「高级」 | 高对比长时间阅读易疲劳，需要额外调正文对比度 |
| **C 暖调砚夜** | 结构参考 `tabler/tabler`，配色自研 | 深墨绿褐底 `#101915` + 玉青/金色点缀，保留岐黄的中医纸感 | **品牌延续性最好**，和浅色的玉青/金/宣纸一脉相承 | 暖暗底最难调，容易脏；需要自己采样，没有现成参考 |
| **D 稳妥同代** | `ant-design/ant-design-pro` (38.9k)<br>`vbenjs/vue-vben-admin` (33.7k) | 中灰 `#141414` + 品牌蓝，标准后台观感 | 迁移成本最低，和 Element Plus 同代 | 观感最普通，解决不了「深色太丑」的初衷 |

**我的建议：A 做骨架 + C 的配色**。用 shadcn/Tabler 的层级手法（细边框、无玻璃、8px 圆角、
surface 三级明度差），配色换成岐黄的玉青/墨绿，这样既现代又不丢品牌。

> 无论选哪个，都要先做一件事：**去掉 `backdrop-filter` 和 4 层渐变背景**。
> 这是现在「暗色发糊」的直接原因，跟选哪个方向无关。

---

## 5. 可接入的设计 Skill（选 1~2 个）

下载量/星数取自 SkillHub 检索，时间 2026-10-29。**现在不安装，等你点头再装。**

| Skill | 热度 | 主要干什么 | 在这个项目里怎么用 |
|---|---|---|---|
| **Frontend Design**<br>`frontend-design` | 42,320 下载 | 视觉方向决策：排版、配色、动效、文案的「有主张的选择」，专门对抗 AI 默认审美（奶油底+衬线、近黑+荧光绿、报纸网格三种套路） | **定方向阶段用**。让它针对「中医学习」给出 2–3 个非模板化的视觉提案，我们挑一个 |
| **Frontend Design Pro**<br>`frontend-design-pro` | 29,905 下载<br>22 ★ | 设计规范 + 反模式清单，带 `/audit` `/polish` `/critique` `/bolder` `/quieter` 等审查命令 | **审现有页面用**。跑 `/audit` 扫出「卡片套卡片」「彩色底灰字」「弹性动画」这类具体病灶 |
| **Tailwind Design System**<br>`tailwind-design-system` | 10,026 下载<br>8 ★ | 用 CVA 建可主题化组件库：设计令牌、变体、暗色模式、响应式 | **只借方法论，不装**。项目虽有 Tailwind 3.4.17 但只开了 utilities，主体系是 SCSS；可以照它的 token→变体分层来设计 Tier 2/3 |
| **Web Design**<br>`web-design` | 9,259 下载 | CSS 实现模式：Grid vs Flex 选型、间距节奏、响应式断点、可访问性 | **实现阶段用**。app shell 栅格、侧栏折叠、断点收敛时查它 |
| **UI Design System**<br>`afrexai-ui-design-system` | 8,088 下载 | 产品设计师方法论：用户研究 → 结构 → 可访问配色与排版系统 → 像素级界面 | 偏重方法论，和 Frontend Design 有重叠，二选一即可 |
| **Design Review**<br>`design-review` | 2,303 下载 | 质量门控：任何视觉任务交付前强制自查（含 Linear / Vercel dashboard 作为质量基准） | **验收阶段用**。每阶段结束跑一次，避免「我觉得好看」就交付 |

**我的建议：`frontend-design`（定方向）+ `design-review`（守交付）**。
一个管往哪走，一个管别翻车。`tailwind-design-system` 只借方法论不装。

---

## 6. 执行阶段

每阶段结束都有可验收产物，不通过不进下一阶段。

### S0 · 基线冻结（半天）
- 给现有 8 个主要页面各拍「浅色 / 深色」两张截图，存 `deliverables/theme-baseline/`
- 写脚本 `scripts/audit-theme.mjs`：输出「未定义 token 清单」「硬编码颜色清单」「对比度不达标清单」
- 产物：一张基线对比图 + 一份债务清单

### S1 · token 三层化（1–2 天）
- 拆 `tokens.scss` 为 `palette.scss` / `semantic.scss` / `components.scss`
- **浅色成为 `:root` 默认**，暗色改为 `[data-qh-theme='dark']` 覆盖
- `useTheme` 默认值改 `light`，`index.html` 首屏加防闪烁脚本（读 localStorage 前置）
- 验收：`audit-theme.mjs` 报告「未定义 token = 0」

### S2 · 浅色补全 + 硬编码回收（2–3 天）
- 补齐 115 个未覆盖 token 的浅色值
- 回收 344 处硬编码：profile(85) → learning(30) → assessment(24) → path(19) → layout(10)
- 把 `check-tokens.mjs` 的豁免目录从「整目录」收窄到「具体遗留文件白名单」，
  防止再退化
- 验收：浅色下逐页走查无「暗色残留」，硬编码 ≤ 20 处（仅 dev 演示页）

### S3 · 深色重做（3–4 天）
- 按选定方向重写 `[data-qh-theme='dark']` 语义层（约 40 行）
- 删掉 `backdrop-filter`、4 层背景渐变，改为 surface 三级明度差
- 正文对比度提到 ≥ 7:1（AAA 正文）
- 验收：深色截图与参考项目并排对比，无明显违和

### S4 · 图谱双主题（1–2 天）
- 给 `graph-visual-spec.md` 补一套暗色采样值（背景、四档层级色、边色）
- `--qh-graph-*` 全部进语义层，暗色下不再是浅蓝孤岛
- 验收：图谱在两种主题下都跑一遍 8 项观感清单

### S5 · 组件与页面逐个过（3–4 天）
- 按依赖顺序：`ui/` 基础件 → `layout/` → `learning/` → `profile/` → `views/`
- 每个页面在双主题下各截一张，进验收目录
- 验收：8 个主页面 × 2 主题 = 16 张对比图

### S6 · 终验（1 天）
- 跑 `design-review` skill 的检查项
- `npm test` / `check:tokens` / `vue-tsc` 全绿
- 新增契约测试：`tests/theme-tokens.spec.mjs`
  - 断言浅色覆盖全部语义 token
  - 断言无 `backdrop-filter` 出现在主题层
  - 断言硬编码颜色数不超过阈值

---

## 7. 验收标准（可量化）

| 项 | 标准 |
|---|---|
| 浅色 token 覆盖 | 语义层 100%，缺口 0 |
| 硬编码颜色 | ≤ 20 处，且全部在 dev 演示页 |
| 正文对比度 | 浅色 ≥ 7:1，暗色 ≥ 7:1（WCAG AAA 正文） |
| 次要文字对比度 | ≥ 4.5:1（AA） |
| 边框/分隔线 | ≥ 3:1（非文本对比） |
| 主题切换 | 无闪烁，无 FOUC，刷新后保持 |
| 页面覆盖 | 8 个主页面 × 2 主题全绿 |
| 图谱 | 双主题各过 8 项观感清单 |

---

## 8. 风险与取舍

| 风险 | 影响 | 应对 |
|---|---|---|
| 344 处硬编码回收工作量大 | S2 可能超期 | 按页面优先级分批，dev 演示页不修 |
| 改 token 会牵动故事模块 | 故事模块观感可能变 | `story.scss` 自成体系，本轮不动，只在语义层做兼容映射 |
| 浅色默认化会让老用户「界面变了」 | 感知突兀 | 尊重 localStorage，已选暗色的用户保持暗色；只对首次访问者给浅色 |
| 深色方向选错要返工 | 3–4 天 | S3 开始前先做「一个页面（Dashboard）」的完整样张给你确认，通过后再铺开 |
| 图谱暗色值需要重新采样 | 上一轮观感结论可能被推翻 | 暗色单独采一套，不套用浅色数值，验收清单独立 |

---

## 9. 需要你决定的三件事

1. **深色方向**：A 中性冷灰 / B 近黑高对比 / C 暖调砚夜 / **A+C 混合（建议）**
2. **接入哪些 skill**：`frontend-design` + `design-review`（建议），或你指定
3. **默认主题**：首次访问给浅色（建议）/ 跟随系统 `prefers-color-scheme`

确认后我从 S0 开始，先出基线对比图再动代码。
