# 素材管线 · 规范

> 分册之一。本文件是**素材的唯一事实源**：`README.md` 与 `ppt.md` 只引用不复述。导图不使用位图，文档只用结构图表，均不在此范围内。

---

## 1. 定位与边界

素材管线只服务幻灯工坊的两类槽位：

| 槽位类别 | 服务对象 | 允许来源 |
|---|---|---|
| `decorative` | 封面点题位、章节页、尾页的装饰 | 参数化母题 / 公版图谱 / 生成图（限额） |
| `evidence` | 需要"这就是某味药/某个部位"的画面 | 公版实拍 / 教材图 / 图谱 / 用户材料，**不得生成** |

三条边界，先立规矩再谈实现：

1. **分两层：主库零网络，远图层按需只读**。本地库（`local.herbs` / `local.heritage`）是开发期采集、运行期只读文件与登记表；B / C 类远程渠道在**生成期**按需检索，产物**只钉 URL 不下载**（§5.7）。运行时不写仓库、不做「顺手抓一张存起来」。
2. **登记表是唯一入口**。任何**落盘**的图片不先登记就不许被引用；代码里不允许出现绕过登记表的硬编码路径。远程图不进登记表，进的是稿件的 `asset_refs`（带 `url` 与许可），两者不混。
3. **宁少勿滥**。每个 subject 一张主图即可；宁可降级成纯排版，也不放一张"差不多"的图去冒充。

---

## 2. 目录与命名

```
frontend/public/assets/
├── registry.json          # 登记表（唯一索引）
├── pictorial/             # 位图，处理后 WebP
│   ├── plate-renshen-01.webp
│   └── photo-chenpi-01.webp
└── motifs/                # 参数化母题的验收快照（仅评审与截图基线用）

tools/assets/
├── ingest.py              # 采集：下载 → 验许可 → 归一化 → 写登记表
├── normalize.py           # 归一化算子（可独立运行）
└── audit.py               # 构建期校验（CI 调用）
```

命名规则：`{kind}-{subject}-{seq}`，全部小写、连字符分隔、不含中文与空格。`kind` 取 `plate`（公版图谱） / `photo`（实拍） / `motif`（母题快照） / `generated`（生成图）/ `ornament`（角花·边带等装饰纹样）/ `ground-texture`（整页底纹）。后两种来自装饰素材包（Openclipart CC0，`scripts/fetch-ornaments.mjs` 维护），只能进装饰位、不得进证据位与图位。

---

## 3. 登记表 schema

`registry.json` 是一个对象：`{"version": 1, "assets": [ ... ]}`。每条字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | ✅ | `{kind}-{subject}-{seq}`，全局唯一 |
| `kind` | `plate` / `photo` / `motif` / `generated` / `ornament` / `ground-texture` | ✅ | 决定默认 treatment；ornament / ground-texture 只进装饰位 |
| `slot_class` | `evidence` / `decorative` | ✅ | 选型时硬过滤，不匹配直接排除 |
| `subjects` | string[] | ✅ | 主题词（人参 / 阴阳 / 通用） |
| `herb_ids` | string[] | — | 关联知识库实体 id，用于精确命中 |
| `tags` | string[] | — | 科属、药用部位、风格词 |
| `file` | string | ✅ | 相对 `public/` 的路径 |
| `w` / `h` / `bytes` | int | ✅ | 用于 aspect 与体积判定 |
| `sha256` | string | ✅ | 完整性校验，防"同名替换" |
| `license` | string | ✅ | 白名单内（见 §4） |
| `source_url` | string | ✅ | 原图页面（不是直链） |
| `attribution` | string | CC-BY 必填 | 作者 + 出处，渲染时显示 |
| `treatment` | string | ✅ | 归一化配方 id（见 §6） |
| `added_at` | string(ISO) | ✅ | |
| `review` | `{reviewer, date, note}` | ✅ | 人工过目记录；未评审的图不得进库 |

不进登记表的图不存在——这句话要落成测试（§9 第 7 条）。

---

## 4. 许可白名单

| 许可 | 可用于 | 署名要求 |
|---|---|---|
| `PD-old`（作者逝世逾 100 年 / 出版逾 100 年） | evidence + decorative | 建议在来源表标注；不强制 |
| `CC0` | evidence + decorative | 无 |
| `CC-BY-4.0` | evidence + decorative | **必须**在页面或来源表显示 `attribution` |
| `Heritage-Free`（Heritage Library 套组） | decorative | 建议标注；**禁止再配布素材文件本身** |
| `CC-BY-NC-4.0` | **仅装饰位** | **必须**署名；见下「演示期例外」 |
| `MIT / Apache-2.0`（图标类，若将来引入） | decorative | 许可证文本 |

一律拒绝：`CC-BY-NC-SA*`（要求以相同方式共享，会传染到整份课件）、`CC-BY-ND*`、来源不可考、无水印去除授权、社交平台截图、AI 图库二次分发。

### 4.1 演示期例外：`CC BY-NC`

`pngimg.com` 是 `CC BY-NC 4.0`：允许非商业使用，但课件可能被用户拿去做收费培训，我们控制不了下游。

本项目当前定位是**比赛演示，不对外提供商业服务**，因此放行这一族许可。但放行的方式不是「忘了这回事」，而是：

- 写进 `licenses.py` 的 `DEMO_ONLY_LICENSES`
- `audit.py` **每次运行都把它数出来并打印**
- 契约测试守着「非商业许可只能从这道门进，且必须被标出来」

换句话说：允许，但记账。商业化之前必须把这些素材逐条替换掉。

**已有反例必须点名**：`frontend/src/assets/profile-cards/` 下 22 张 2.3–3.4MB 的插画，无源文件、无许可、无作者——这类**不得**纳入本管线，也不得作为"以后有素材了"的替代方案。

---

## 5. 采集：`tools/assets/ingest.py`

### 5.1 边界

- **开发期工具**，不进服务端代码路径；运行时禁网。
- **不做爬虫、不做站内搜索抓取**。输入是一份人工维护的 URL 清单（`tools/assets/sources.yaml`），逐条采集。这样速率、版权、可复现都可控。
- 每次采集写 `tools/assets/sources.lock`：`{url, fetched_at, sha256, license, page_title}`，保证别人能复现同一批文件。

### 5.2 流程

```
sources.yaml(人工)
  → 下载原图（限速、限并发 2、失败重试 1 次）
  → 抓取页面元数据，人工确认 license 字段填写
  → normalize.py 归一化 → pictorial/*.webp
  → 计算 sha256 / w / h / bytes
  → 写入 registry.json（review 留空 → 人工过目后填）
  → audit.py 校验
```

### 5.3 来源优先级

| 优先级 | 来源 | 适用 | 说明 |
|---|---|---|---|
| 0 | **Wellcome Collection**（`api.wellcomecollection.org`） | 药材主图、中医古籍装饰 | `pdm`（Public Domain Mark）+ IIIF 直链带 CORS。《本草品汇精要》明抄彩绘 92 张、《本草纲目》1590 金陵初刻 148 张、针灸图与 17 世纪本草若干。**免 key、可检索、缓存 28 天**，是当前唯一能规模化补证据位的源 |
| 1 | 《植物名实图考》（吴其濬，1848） | 药材主图（线描） | PD-old，线描加淡彩，与纸墨语言天然同源 |
| 2 | 《本草图谱》（岩崎常正，1828） | 同上（补覆盖） | PD-old，设色更饱和，需降色 |
| 3 | 《本草纲目》金陵本 / 味连本插图 | 同上（补覆盖） | PD-old，木刻线条粗，适合极简范式 |
| 4 | Wikimedia Commons / Internet Archive / BHL | 上述三处的扫描件载体 | 必须在页面确认 PD 或 CC0；**本机网络对 Wikimedia / archive.org 不可达**，实际走不通 |
| 5 | Wikimedia CC0 饮片实拍 | **证据位**（药材形态、炮制） | 项目已有 5 张先例：`frontend/public/story/herbs/`，许可记录在 `story/README.md` |

第 0 项是新增的主源。取舍理由：它是**彩色精绘**（不是木刻填色），宣纸底、朱丝栏、蓝底金字药名框，与幻灯的纸墨语言比 Heritage 那批西方版画更同源；且能按 `query=` 检索，模型可以自己换词重搜，而 §5.3 原本的 1–3 项都只能人工维护 URL 清单。

第 5 项要注意：现有 5 张是**饮片实拍**，讲"炮制/鉴别"时能用；讲"原植物"时不能用古图谱之外的图。

### 5.4 本地原图入库：`tools/assets/local.py`

`ingest.py` 的第一条规矩是「只下载清单里的 URL」，为的是不退化爬虫。但有一批图**根本不在网上**——它已经在仓库里了（`frontend/public/story/herbs/` 那五张 CC0 饮片），缺的只是元数据与归一化。所以另起一个脚本：

```
tools/assets/local.json（人工写元数据：id / 许可 / 来源页 / 主题词 / 配方）
  → normalize.py 归一化 → public/assets/pictorial/{id}.webp
  → 算 sha256 / w / h / bytes
  → 合并进 registry.json（同 id 整条覆盖，元数据以 local.json 为准）
  → audit.py 校验
```

两件事必须分开的理由：混在一个脚本里，`--url` 参数迟早会被加回来，"不抓网"这条硬约束就磨钝了。

**当前库内 5 条**（全部 CC0 1.0，来源与作者见 `frontend/public/story/README.md`）：

| id | 主题词 | 处理 | 体积 |
|---|---|---|---|
| `photo-huangqi-01` | 黄芪 | photo-toned 1600×1232 | 57 KB |
| `photo-dangshen-01` | 党参 | 同上 | 54 KB |
| `photo-taizishen-01` | 太子参 | 同上 | 79 KB |
| `photo-chenpi-01` | 陈皮 | 同上 | 67 KB |
| `photo-jinyinhua-01` | 金银花 | 同上 | 118 KB |

`review.reviewer` 现在填的是 `agent-review`，note 里写明「待人眼复核」——过目这件事不能由脚本代签。

### 5.5 整包套组入库：`tools/assets/vendor.py`

`local.py` 处理「逐张挑好的图」，元数据是人写的。整包下载的插画套组没法逐张手写，只能算出来——所以多一层：

```
tools/assets/*.zip（Heritage 免费套组，含 License PDF）
  → vendor.py 解包 → normalize.py（vintage-keep）
  → 逐张调大模型看一眼，得出 subject / alt / tags
  → tools/assets/inventory.json（中间态，标签按 asset_id 缓存，重跑不重新花钱）
  → local.generated.json → local.py --entries 写进 registry
```

三步分开的理由：**打标要联网、要花钱**，归一化不要。中间态落成 `inventory.json` 之后，改配方、改尺寸都可以重跑几十遍而不重新调用模型。

打标不是可选装饰。第一批 43 张里，模型给出的主体名是「双耳瓜棱陶罐」「单耳鱼鳞纹陶杯」「拟人化药瓶」这种粒度——若一律填「器皿」，检索时九张图无法区分，等于入库了却接不上任何一页。

**当前 Heritage 套组入库 43 条**（`Heritage-Free`，见 §4）：

| 套组 | 条数 | 内容 | 单张 |
|---|---|---|---|
| Bird Paintings 02 | 7 | 彩绘鸟类（埃及风、鸳鸯、水禽） | 60–120 KB |
| Flying High | 5 | 水墨飞燕，大量留白 | 20–40 KB |
| Hasegawa Flowers 02 | 4 | 浮世绘菊（黄/粉/卷瓣） | 90–190 KB |
| Nature by Merian 07 | 3 | 梅里安博物画（无花果、蛇、天蚕蛾） | 200 KB |
| Nature Symphony 15 / 17 | 6 | 松枝花鸟 | 100–280 KB |
| Under The Sea 04 | 9 | 海星、贝壳、对虾线描 | 80–160 KB |
| Vases & Flower Pots 02 | 9 | 陶罐花器线描（含拟人药瓶） | 24–63 KB |

**许可的两个要点**（套组内 `License_HeritageLibrary.pdf` 原文）：

1. 是 **CC0**，可商用可修改，可用于 applications 与 print materials。
2. 但明文 **禁止再配布**：不得转售、不得单独分发、不得打包成素材包提供下载。

所以本仓库只保留归一化后的产物用于生成课件，**不在任何界面上提供素材下载入口**。这一条要跟着代码走，改界面时别顺手加个「下载原图」。

**覆盖缺口**：只有五味药。讲人参、当归、甘草这些课时，`select` 会正确判 `none` 并让版式降级（无图），而不是拿黄芪的图顶上。补覆盖的第 0 项（Wellcome）已实测可检索，是当前唯一可规模化的补法；`sources.json` 仍是空清单，因为往里面填 URL 是**人工确认许可**的动作，不能由脚本代劳。

### 5.6 渠道目录：`app/ppt/channels.py`

素材能不能用，模型不该靠猜。所以另立一份**给模型读的菜单**，十九条渠道分六类：

| 类 | 含义 | 检索能力 | 落盘 | 代表 |
|---|---|---|---|---|
| A | 本地库 | 按 registry 查 | ✅ | `local.heritage`（43 条）/ `local.herbs`（5 条） |
| B | 有检索 API | **能换词重搜** | 多为 url-only | `api.wellcome` / `api.cleveland` / `api.iconify` / `api.phylopic` |
| C | 无检索站点 | **只能在分类页点名** | 看许可 | `web.opendoodles` / `web.pngimg` / `web.linustock` |
| D | 生成 | — | memory-only | `gen.image`（≤3 张/稿，装饰位限定） |
| E | 参数化母题 | 14 个 id | 自产 | `param.motif` |
| F | 字体对 | 8 套 | 系统字体 | `font.pair` |

三条设计原则写进代码，不靠提示词自觉：

1. **落盘与否先于内容**。许可禁止再配布的三家（`linustock` / `enpitsu-sozai` / `rakugakiicon`）标 `url-only`，能把 URL 钉进稿子，不能下载进仓库——「引用」与「分发」的差别。
2. **`EVIDENCE_CHANNELS` 是硬约束**，只有 `local.herbs` 与 `api.wellcome`。加了新渠道而忘了把它纳入/排除，契约测试会挂。
3. **`ROUTES` 是顺序不是建议**：`herb-evidence` 只有两站，中间不许插「通用植物图」；生成永远不打头。

`channels.json` 由 `python -m app.ppt.channels` 生成，与 `docs/capabilities.md` 同一套「生成物不手改」的规矩。

### 5.7 运行时远程取用：`app/ppt/materials.py`

登记表管落盘素材，远程渠道管「这一刻能取到什么」。两者都是素材的来源，但规则不同，所以代码也分开：

| 层 | 谁决定 | 产物 | 许可责任 |
|---|---|---|---|
| 本地库 | 开发期人工采集 | 文件 + `registry.json` | 入库时已核 |
| 远程渠道 | 生成期模型检索 | `Candidate` → 稿件 `asset_refs` | 每条候选自带 `license` / `attribution` |

四条实现规矩：

1. **不落盘**。远程候选只给 `url` + `thumb` + 元数据。许可禁止再配布的那几家（`linustock` 等）落盘就是再配布；馆藏 IIIF 地址本身是稳定的，OSS 临时地址不是。
2. **全部过 wsrv 代理**（`proxy_url(url, width)` → `https://wsrv.nl/?url=…&w=…&output=webp`）。一次解决四件事：CORS（Cleveland / pngimg 原图没有，`fetch` 拿不到 blob，导出时一张都进不去）、尺寸、webp、一年缓存。实测 Wellcome IIIF 原图经代理 200、1.2MB、`image/webp`。
3. **失败必须是空列表，不是异常**。搜不到、断网、对方 403，都只意味着「这条渠道这次没货」；抛异常会打断整份稿子。没实现的渠道（缺 key、禁再配布）调过来同样返回空——**空即降级**。
4. **id 要稳定**。场景里 `asset` 只能以 `plate-/photo-/motif-/generated-` 开头，而远程 id 常是 `L0039301`。所以 id = 前缀 + `sha256(source:remote_id)[:8]`，同一张图每次都得到同一个 id——冻结之后才可复现。

**四个 driver**（只实现"能真取到东西"的那几条）：

| driver | 渠道 | 要点 |
|---|---|---|
| `search_wellcome` | `api.wellcome` | 许可逐条取：`pdm`→`CC0`、`cc-by`→`CC-BY-4.0`、`cc-by-nc`→`CC-BY-NC-4.0`（只能装饰位）；图像走 IIIF `/image/{id}/full/…` |
| `search_cleveland` | `api.cleveland` | 免 key、API 有 CORS、原图无 CORS；`chinese painting` 951 件水墨 |
| `search_iconify` | `api.iconify` | 许可**逐集**映射（fluent MIT / mdi Apache-2.0 / healthicons CC0 …）；指定前缀 0 条时放开前缀重搜并按偏好重排——不是没有图标，是那三个集没收这个词 |
| `search_phylopic` | `api.phylopic` | 必须带 `build=562`，否则 307 后取不到内容 |

外加 `_local_candidates`（查登记表）与 `fetch_category`（C 类点名取图，只认 `CATEGORY_PAGES` 里登记过的分类）。**`_kind_prefix` 的坑**：`herb` 一律归 `plate` 而不是 `photo`——馆藏本草图是版画不是照片，叫 `photo-` 会让下游误判选材。

`translate(query)` 只管检索词：中文命中 `ZH2EN` 即译（**最长匹配优先**，否则「太子参」会被「参」先吃掉成 ginseng），未命中原样透传——交给生图时中文直出反而更准。

`_CACHE` TTL 600 秒，**空结果也缓存**：模型在一份稿子里会把「黄芪」搜好几次（不同页、不同渠道），缓存空结果省的是重复的 9 秒超时。

`measure(path)` 量尺寸、留白率、边缘密度，给 `look_at` 之外的确定性判断用（图太小、四周全白、边缘全是噪点，不必再问模型）。

---

## 6. 归一化算子（`normalize.py`）

异源素材能看起来像一套，靠的是这一步——**多样性来自采集，统一性来自算子**。

| 步骤 | 处理 | 参数 |
|---|---|---|
| 1 去底 | 扫描件纸底（偏黄）白平衡到主题纸色 `#FAF7F0` | 采样四角中位数 |
| 2 降色 | duotone 映射到主题 `ink` / `ochre` 两色，保留 8 档灰阶 | 颜色取自 `pptThemes` |
| 3 叠纸 | 与纸纹正片叠底，融进底色 | `multiply, opacity 0.12` |
| 4 羽化 | 边缘 4px 高斯，避免硬切 | σ=4 |
| 5 裁剪 | 按 `aspect` 裁到统一比例，留 6% 内边距 | portrait 3:4 / square 1:1 / landscape 4:3 |
| 6 输出 | WebP q=82，长边 ≤1600 | — |

三个配方 id：

- `plate-wash`：古图谱设色件（步骤 1–6 全走）
- `plate-line`：纯线描件（跳过步骤 2 的中间调，只做线条加深）
- `photo-toned`：实拍件（步骤 1 减半，保留纹理，步骤 2 强度 50%）
- `vintage-keep`：**带透明通道的复古插画**，与上面三条相反——不去底、不降色，保留 alpha 与原色（见下）

### 6.1 `vintage-keep`：为什么不能降色

Heritage 一类套组是修复过的彩版画。降到墨赭两色会把设色洗掉，而设色正是它们值钱的地方。所以这条配方只做四件事：

| 步骤 | 处理 | 理由 |
|---|---|---|
| 1 裁内容外框 | 按 alpha 的 bbox 裁掉四周空白 | 实测 42%–92% 的像素是透明的，不裁会在版面上缩成中间一小条 |
| 2 叠纸 5% | 只压 RGB，alpha 一位不动 | alpha 一旦被混合，贴到纸面上会浮出一圈灰边 |
| 3 补透明边 | 按 `aspect` 居中补边，**不裁剪** | 宁可留白，也不要切掉花枝与器耳 |
| 4 输出 | WebP 保留 alpha，长边 ≤1100 | 版面最大也就 1/3 宽，1200 以上纯属浪费 |

体积实测：长边 1100 时线稿约 40 KB、彩绘约 200 KB。超 200 KB 的（彩绘精细件）由 `vendor.py` 逐级退让重压，而不是一刀切降全局——线稿本来只有 40 KB，不必陪着挨刀。

**验收口径**：同一主题下随机抽 6 张处理后的图并排给人看，若评审说不出"哪张不是一套"，即通过。这条写进 §9。

---

## 7. 选型器（`select` 步的实现规格）

### 7.1 输入

`DesignSlot`（subject / style / priority / aspect）+ 当前 `theme_id` + 该页 `slot_class`。

### 7.2 打分（确定性，无随机）

| 条件 | 分值 |
|---|---|
| `slot_class` 不匹配 | 直接排除 |
| `subjects` 精确命中 | +100 |
| `herb_ids` 命中 | +100 |
| 同科属 / 同类别（tags 交集） | +60 |
| 通用母题 | +30 |
| `style` 与 `kind` 一致 | +40 |
| `aspect` 完全一致 | +20；可裁切到达标 +10 |
| 长边 < 800 | −50 |
| `treatment` 与当前主题不符 | −30 |

同分按 `id` 字典序取第一个——保证同一输入永远同一结果，截图基线才有意义。

### 7.3 四级阶梯

```
1 parametric : 该槽位由母题函数生成（底纹/角饰/界格/印章/图形语法）→ provenance=parametric
2 hit        : 打分 ≥ 阈值（100）且唯一最优 → provenance=library
3 substitute : 打分在 [30,100) 或退化到通用母题 → provenance=library，页面标「示意」
4 generate   : provider 可用且未超预算（≤3）→ provenance=generated，标「AI 生成示意」
   degrade   : provider 不可用或超预算 → 改版式（有图→无图），provenance=none
```

`priority=required` 的槽位走到 `degrade` 时，check 必须报一条 `slot_unfilled`，不得静默通过。

### 7.3.1 谁来走这个阶梯：先工具循环，再确定性打分

阶梯是**判定**，不是执行者。执行顺序现在是：

```
工具循环（agent_loop.resolve_with_tools）
  → 模型自己 search / look / assign / generate
  → 拿到绑定（含 url、许可、理由）
       ↓ 循环没跑起来 或 某页没绑上
确定性打分（select.resolve_slots）
  → 参数化 → 命中 → 平替 → 生成 → 降级
```

两者合并时**循环结果优先**，打分只补空缺。这样同一份设计单在「模型可用」和「模型不可用」两种情况下都能出稿，且前者不会退化成关键词匹配。

为什么不让打分当唯一入口：打分只认识字符串，不认识内容。它会把「人参」匹配到一张标题里含「人参」的模糊残片，而模型看一眼就知道那是装裱绢边。

为什么不让循环当唯一入口：模型会挂。断网、限流、JSON 写错，任何一次都可能让循环空手而归。这时候必须有确定性的兜底，而不是让整份稿子失败。

### 7.4 `slot_resolution` 形状

```json
{
  "unit_id": "s3",
  "slot": "figure",
  "requested": {"subject": "人参", "style": "plate", "aspect": "portrait"},
  "result": "hit",
  "asset_id": "plate-renshen-01",
  "provenance": "library",
  "score": 160,
  "license": "PD-old",
  "attribution": "《植物名实图考》卷一",
  "fallback_reason": ""
}
```

`result` 取 `parametric / hit / substitute / generated / none`。这个结构整份写进 `meta.slot_resolution[]`，是"为什么这页长这样"的唯一答案。

---

## 8. 母题生成器（参数化那一层）

不使用任何外部文件，全部由代码现场生成。

- **单一事实源**：`backend/app/ppt/motifs.py` 定义母题目录（id / 参数范围 / 适用主题 / 默认参数）；前端通过构建期生成的 `frontend/src/render/motifs.catalog.ts` 消费，该文件由脚本产出，**不手改**（与 `docs/capabilities.md` 同一套生成思路）。
- **渲染实现**：`frontend/src/render/motifs/` 下每个母题一个函数，入参为主题色板与参数对象，出参为 SVG 字符串（DOM 直接用，导出时序列化进 pptx 或转位图）。
- **首批母题 12 个**：纸底 3（素纸 / 帘纹 / 斑驳）、远山 2（层叠 / 孤峰）、角花 3（卷草 / 折枝 / 缠枝）、界格 2（竖界格 / 乌丝栏）、印章 1、水纹 1。
- **为什么不用手画 SVG**：`frontend/public/story/herbs/*.svg` 四个 777 字节占位卡就是这个模式失败后的样子——手工资产会腐化，参数化函数不会。

---

## 9. 构建期校验（`audit.py`，CI 必跑）

1. 登记表里每条 `file` 存在。
2. `sha256` 与文件实际值一致。
3. `license` 在白名单内。
4. `slot_class` ∈ `{evidence, decorative}`。
5. `license == CC-BY-4.0` 时 `attribution` 非空。
6. 总量与单张体积在预算内（§10）。
7. 代码中出现的 `asset-*` / `plate-*` / `photo-*` 字面量必须存在于登记表（对治 `XiaoqiMascot.vue:20` 式静默兜底）。
8. `subjects` 至少一项能在知识库实体里命中，或显式为 `通用`。
9. 同一**文件**不得登记两次（按 `sha256` 判重）。过去这条按「同 `subject` + 同 `treatment`」判重，整包套组一进来就整排报错——九只造型各异的陶罐都叫「陶罐」，但它们是九张不同的图。真正要防的是同一个文件占了两个 id，那看字节就够了。
10. 归一化可复现：随机抽 3 条重跑 `normalize.py`，输出 sha256 一致（允许 png→webp 编码器版本差异时放宽为 SSIM ≥ 0.995）。

---

## 10. 体积与性能预算

| 项 | 上限 |
|---|---|
| 单张 `plate` | ≤ 200 KB |
| 单张 `photo` | ≤ 400 KB |
| 首批总量 | ≤ 8 MB |
| 全量上限 | 30 MB（超过即要求外置 CDN，本轮不做） |
| 单页引用图片数 | ≤ 2（与装饰预算一致） |

---

## 11. 首批清单规格

只写规格，不落文件。

**药材主图**

- 规模：10–30 味；首批取教材重点药，补虚 / 解表 / 清热 / 理气四类各 5–8 味。
- 每味：1 张主图 + 0–1 张备选；`slot_class=decorative`（原植物线描图不是"这味药的实证"，讲炮制时不能顶替）。
- 来源：§5.3 的 1–3 优先。
- 验收：主体完整（根 / 茎 / 叶 / 花可辨）、扫描长边 ≥1200px、无明显污损与折痕。
- 缺口处理：查不到就空着，走 `degrade`，不做"通用植物图"替代。

**参数化母题**

12 个（§8），每个给：参数表、适用主题、验收截图、一段"什么时候用它"的说明。

---

## 12. 生图：`app/ppt/providers/wanx.py`（已接通，仅装饰位）

用途只有一个：**装饰位没有对应素材时补一张**。

```python
DEFAULT_MODEL = "qwen-image-2.1-pro"   # qwen-image / qwen-image-plus 实测返回 Model not exist
SIZES = {"square": "1024*1024", "portrait": "768*1152", "landscape": "1280*720"}

async def generate(prompt, *, size="square", model=DEFAULT_MODEL, asset_id="") -> dict
```

- 网关是 DashScope 的多模态生成端点，key 走 `qwen_api_key`；无 key 时 `available()` 为 `False`，循环里直接跳过生成这一选项。
- **必须落盘**，位置 `frontend/public/assets/generated/{id}.webp`。接口返回的是 OSS 临时地址，过期后图就没了——不落盘的生成图等于一张会消失的图。这一点与远程渠道相反：远程是"别人的图，只钉不存"，生成是"自己的产物，必须存"。
- 三条硬约束不靠提示词：每稿 ≤3 张（预算在 `agent_loop` 里扣）；**禁止进证据位**；必须记 `prompt / model`，否则不可复现。
- 失败返回 `{"ok": False, "error": ...}`，不抛异常——一张图画不出来不该让整份稿子失败。
- 实测：`qwen-image-2.1-pro` 一次成功，173 KB，画面为竹编簸箕摊药材 + 陶药罐 + 药臼 + 杵，淡墨线描，无文字无畸形。

---

## 13. 禁止事项

1. 运行时把远程素材**写进仓库**（下载、缓存落盘、「顺手抓一张存起来」）。远程只许钉 URL 引用，见 §5.7；落盘的只有自己的生成图。
2. 来源不明或"网上找的"图片入库（含现有 `profile-cards` 那类）。
3. 生成图进证据位；装饰图冒充实证（用通用植物图代替某味药的饮片图）。
4. 在登记表里写不存在的文件（`audit` 会挂）。
5. 手画母题 SVG 代替生成器。
6. 为了"素材丰富"而堆同一 subject 的多张相似图。
