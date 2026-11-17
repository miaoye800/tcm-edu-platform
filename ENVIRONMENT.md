# 环境配置与依赖清单

配套 [README.md](README.md) 使用。本文覆盖四件事：运行环境版本、环境变量模板（逐项说明）、依赖清单、配置类常见故障。

---

## 一、运行环境

| 组件 | 版本 | 备注 |
| --- | --- | --- |
| Python | 3.12（开发用 3.12.9） | 依赖里 `chromadb 0.6.3`、`langgraph 1.1.10` 对 3.10 以下不保证可用 |
| Node.js | 22（开发用 22.22.2） | 需要 npm 10，依赖 `package-lock.json` 安装 |
| 浏览器 | Chrome / Edge 最新版 | 3D 经脉场景需要 WebGL2 + 浮点渲染目标（EXT_color_buffer_float） |
| 操作系统 | Windows / macOS / Linux | 开发在 Windows，部署在 Linux；路径均用 `pathlib` 处理 |
| 外部服务 | 无（不需要数据库或 Redis） | SQLite 文件 + 本地 Chroma 持久化目录 |

内存建议 4 GB 以上。首次构建前端时 `node_modules` 约 700 MB，属于正常。

---

## 二、环境变量

### 2.1 模板

后端所有配置项集中在 `backend/.env.example`，复制成 `backend/.env` 后按需修改：

```bash
cd backend
cp .env.example .env      # Windows: copy .env.example .env
```

完整模板（与仓库内文件一致）：

```dotenv
APP_NAME=qihuang-backend
ENV=dev
CORS_ORIGINS=["http://127.0.0.1:5173","http://localhost:5173"]
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=your-key-here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL_PRO=deepseek-v4-pro
# 只认 deepseek-flash（V4.1 Flash）与 deepseek-v4-pro；旧名 deepseek-v4-flash 会多烧推理 token。
DEEPSEEK_MODEL_FLASH=deepseek-flash
ZHIPU_API_KEY=
# 千问（阿里云百炼）。开通后每个模型独立赠送 100 万 token（90 天有效），
# 换模型即换额度，适合作为手动真实测试与 DeepSeek 欠费时的兜底。
QWEN_API_KEY=
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
# qwen-flash 的免费额度已耗尽（403 Free quota exhausted），默认走 qwen3.8-flash。
QWEN_MODEL_FLASH=qwen3.8-flash
QWEN_MODEL_PLUS=qwen-plus
QWEN_MODEL_MAX=qwen-max
# 录制回放：live=真调并录制，replay=只回放（e2e 用，未命中会报错），
# auto=先回放未命中再真调，mock=强制假数据。测试脚本请显式设为 replay。
LLM_MODE=auto
# 默认关闭思考模式。抽取、分类、评分类任务不需要推理 token；
# 需要推理的调用点在代码里显式传 thinking={"type":"enabled"}。
LLM_THINKING_DEFAULT=disabled
MOCK_STREAM_DELAY=0.03
LLM_TIMEOUT_SECONDS=60
# 避免服务端已受理但客户端超时时重复扣费；需要时再显式调大。
LLM_MAX_RETRIES=0
LLM_FALLBACK_TO_MOCK=true
# 默认配置（代码内置）：profile_jury 使用 Flash。只有人工复核等场景才建议启用：
# LLM_TASK_MODEL_MAP={"profile_jury":"deepseek_model_pro"}
# 生产必须改为随机长字符串，不能使用示例占位值。
AUTH_SECRET=replace-with-random-long-secret-in-production
AUTH_TOKEN_EXPIRE_DAYS=7
# 正式默认 backend/.data/qihuang.db；可用绝对路径或相对 backend 的路径覆盖。
QIHUANG_DB_PATH=backend/.data/qihuang.db
EMBEDDING_API_KEY=
EMBEDDING_BASE_URL=https://open.bigmodel.cn/api/paas/v4
EMBEDDING_MODEL=embedding-3

# 检索与 GraphRAG。三组开关分开，是为了出问题能只关一半：
# kg_expand_* 是资源生成时沿图补药材，kg_agent_* 是问答时沿图补关系依据。
RETRIEVAL_SCORE_THRESHOLD=0.35
KG_EXPAND_ENABLED=true
KG_EXPAND_MAX_DOCS=2
KG_SEED_THRESHOLD=0.28
KG_AGENT_ENABLED=true
KG_AGENT_TIMEOUT_SECONDS=2.0
KG_AGENT_MAX_RELATIONS=6
KG_AGENT_LIMIT=40
```

### 2.2 逐项说明

**应用与跨域**

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_NAME` | `qihuang-backend` | 出现在 `/health` 返回与 API 标题 |
| `ENV` | `dev` | 环境标记 |
| `CORS_ORIGINS` | 本机 5173 | JSON 数组。前端换端口或用域名访问时必须加进来，否则浏览器侧报跨域 |

**模型**

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `LLM_PROVIDER` | `deepseek` | 主力 provider |
| `DEEPSEEK_API_KEY` | 空 | 填了才会真调 DeepSeek |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | 兼容 OpenAI 协议 |
| `DEEPSEEK_MODEL_PRO` | `deepseek-v4-pro` | 少量需要强推理的调用点 |
| `DEEPSEEK_MODEL_FLASH` | `deepseek-flash` | 绝大多数任务的默认档 |
| `QWEN_API_KEY` | 空 | 阿里云百炼，DeepSeek 额度用尽时的兜底 |
| `QWEN_MODEL_FLASH` / `PLUS` / `MAX` | 见模板 | 百炼按模型分别赠送额度，换模型即换额度 |
| `ZHIPU_API_KEY` | 空 | 智谱，供多模态（`glm-4.5v`）与 embedding 使用 |
| `EMBEDDING_*` | 智谱 `embedding-3` | 教材条目的向量化；不配则检索退化为关键词匹配 |

**调用行为**

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `LLM_MODE` | `auto` | `live` 真调并录制 / `replay` 只回放 / `auto` 先回放后真调 / `mock` 假数据。测试脚本显式设 `replay` |
| `LLM_THINKING_DEFAULT` | `disabled` | 默认关思考模式。抽取、分类、评分不需要推理 token，开着会多烧 10 倍以上 |
| `MOCK_STREAM_DELAY` | `0.03` | mock 流式输出的间隔秒数，用于前端调试打字效果 |
| `LLM_TIMEOUT_SECONDS` | `60` | 单次调用超时 |
| `LLM_MAX_RETRIES` | `0` | 默认不自动重试：服务端已受理但客户端超时，重发会重复计费 |
| `LLM_FALLBACK_TO_MOCK` | `true` | 无密钥或调用失败时回退到内置样例，保证流程不中断 |
| `LLM_TASK_MODEL_MAP` | 代码内置 | JSON，整表覆盖语义。只写要改的键即可，其余键由代码补默认 |

**安全与存储**

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `AUTH_SECRET` | 开发默认值 | JWT 签名密钥。**生产必须换随机长字符串**，用默认值时后端启动会打印告警 |
| `AUTH_TOKEN_EXPIRE_DAYS` | `7` | token 有效期（天） |
| `QIHUANG_DB_PATH` | `backend/.data/qihuang.db` | 相对路径按 `backend/` 解析，也可写绝对路径；首次启动自动建表 |
| `CORS_ORIGINS` | 见上 | 生产要加实际访问域名 |

**检索与图谱**

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RETRIEVAL_SCORE_THRESHOLD` | `0.35` | 向量命中低于此分数视为未召回 |
| `KG_EXPAND_ENABLED` | `true` | 资源生成时沿图补药材（GraphRAG 扩展） |
| `KG_EXPAND_MAX_DOCS` | `2` | 一次补几味，太大等于把图摊成一张毛毯 |
| `KG_SEED_THRESHOLD` | `0.28` | 够格当扩展起点的分数 |
| `KG_AGENT_ENABLED` | `true` | 问答时沿图补关系依据 |
| `KG_AGENT_TIMEOUT_SECONDS` | `2.0` | 本地只读查询正常 <20ms，超过 2 秒宁可退化成普通检索 |
| `KG_AGENT_MAX_RELATIONS` | `6` | 一张关系卡最多几条关系 |
| `KG_AGENT_LIMIT` | `40` | 每个种子最多取多少条边 |

### 2.3 前端变量

前端没有 `.env` 文件，只有一个可选变量，用于让 dev server 代理到别的后端端口：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `QH_API_TARGET` | `http://127.0.0.1:8000` | 在 `vite.config.ts` 里读取，只影响 `npm run dev` 的 `/api`、`/health` 代理；生产构建不受影响 |

```bash
QH_API_TARGET=http://127.0.0.1:8010 npm run dev
```

### 2.4 生成一个可用的 AUTH_SECRET

```bash
# macOS / Linux
python -c "import secrets; print(secrets.token_urlsafe(48))"
# Windows PowerShell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## 三、依赖清单

### 3.1 后端（`backend/requirements.txt`，8 项）

| 包 | 版本 | 用途 |
| --- | --- | --- |
| `fastapi` | 0.115.6 | HTTP 框架，路由与依赖注入 |
| `uvicorn[standard]` | 0.34.0 | ASGI 服务器（含 websockets，流式输出需要） |
| `pydantic-settings` | 2.7.1 | `.env` 读取与配置校验 |
| `PyJWT` | 2.13.0 | 登录 token 签发与校验 |
| `langgraph` | 1.1.10 | 多阶段编排（澄清 → 提案 → 校验 → 重试） |
| `chromadb` | 0.6.3 | 教材条目的向量库，本地持久化 |
| `httpx` | 0.28.1 | 模型服务调用与测试客户端 |
| `pytest` | 8.3.4 | 测试 |

安装：

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

`chromadb` 会连带装上 onnxruntime 等体积较大的依赖，首次安装约 3 到 5 分钟属正常。

### 3.2 前端（`frontend/package.json`）

运行时依赖按用途分组：

| 用途 | 包 |
| --- | --- |
| 框架与状态 | `vue` 3.5、`vue-router` 4.4、`pinia` 2.2、`@vueuse/core` 14.3 |
| 组件与动效 | `element-plus` 2.9、`@element-plus/icons-vue`、`vue-element-plus-x`、`motion-v`、`gsap`、`@inspira-ui/plugins` |
| 可视化 | `@antv/g6` 5.1（知识图谱）、`echarts` 5.6（雷达与统计）、`three` 0.186（经脉 3D） |
| 文档与成品 | `markdown-it` 14、`markmap-lib` / `markmap-view`、`mind-elixir`、`simple-mind-map` 0.14、`pptxgenjs` 4 |
| 样式与构建 | `sass` 1.83、`tailwindcss` 3.4、`postcss`、`autoprefixer`、`vite` 6、`@vitejs/plugin-vue` |

开发依赖：

| 用途 | 包 |
| --- | --- |
| 类型检查 | `typescript` 5.7、`vue-tsc` 2.2 |
| 端到端与截图 | `playwright` 1.61、`pixelmatch` 7.1、`pngjs` 7 |
| 类型声明 | `@types/three`、`@types/markdown-it` |

安装（按 lock 文件装，保证版本一致）：

```bash
cd frontend
npm ci           # 没有 lock 的场合用 npm install
```

常用脚本：

| 命令 | 作用 |
| --- | --- |
| `npm run dev` | 启动 dev server（127.0.0.1:5173） |
| `npm run build` | `vue-tsc --noEmit` + `vite build`，产物在 `frontend/dist` |
| `npm test` | 24 个组件契约脚本 |
| `npm run test:e2e:graph` | 图谱交互端到端（需先起前后端） |
| `npm run check:tokens` | 设计令牌一致性检查 |
| `npm run build:fonts` | 字体子集化（需本机 Python） |

### 3.3 数据与静态资源

这些是运行必需，已随仓库提供，不需要额外下载：

| 资源 | 位置 | 说明 |
| --- | --- | --- |
| 经脉几何 | `backend/data/meridian/*.bin.gz` | 浏览器直接加载的压缩几何（首屏约 585 KB）。未压缩的 `.bin` 不进仓库，仅供本地调试 |
| 经脉清单 | `backend/data/meridian/manifest.json` | 几何版本、来源与署名信息 |
| 教材与题库 | `backend/data/textbook/`、`curated/`、`herbs/`、`formulas/`、`syndromes/`、`syllabus/` | 445 条教材条目、知识树、题库种子 |
| 字体子集 | `frontend/public/fonts/` | 霞鹜文楷、思源黑体/宋体，含 `LICENSES` |
| 剧情素材 | `frontend/public/story/` | 海报与成片，页面直接引用 |

运行时自动生成的目录（不入仓库，可随时删）：`backend/.data/`（SQLite）、`backend/.chroma/`（向量库）、`frontend/.vite-cache/`。

---

## 四、常见配置故障

**1. 改了 `.env` 没生效。**
`Settings` 读的是**进程启动时的工作目录**下的 `.env`，即必须先 `cd backend` 再起 uvicorn。另外 `get_settings()` 带 `lru_cache`，`--reload` 之外的热改不会重载，改完要重启进程。

**2. 前端页面能开，但所有请求 401 / 跨域报错。**
前者是 `AUTH_SECRET` 变了导致旧 token 失效，重新登录即可；后者检查 `CORS_ORIGINS` 是否包含实际访问的地址（用 IP 或域名访问时，`127.0.0.1` 那条不生效）。

**3. 启动日志里有 `WARNING: AUTH_SECRET 未配置`。**
说明还在用开发默认密钥。本地无所谓，部署到公网前必须换掉，否则任何人都能伪造 token。

**4. 模型一直返回示例数据。**
按顺序查三处：`DEEPSEEK_API_KEY` / `QWEN_API_KEY` 是否为空；`LLM_FALLBACK_TO_MOCK` 是否为 `true`（为真时失败会静默回退）；`LLM_MODE` 是否为 `mock` 或 `replay`。想看真实调用就把 `LLM_MODE=live`。

**5. 3D 经脉页是黑屏或纯白。**
先确认浏览器支持 WebGL2 与浮点渲染目标（`chrome://gpu` 里看 `EXT_color_buffer_float`）。无独显或远程桌面环境可能只有软件渲染，帧率会很低但不至于黑屏；真黑屏多半是几何文件缺失，检查 `backend/data/meridian/` 下 `skin.bin.gz` 等三个文件是否在。

**6. `pytest` 在收集阶段大面积报错。**
不要给 pytest 指定项目内的 `--basetemp`。项目会在项目目录内累积临时目录，达到一定数量后触发运行环境的批量删除保护，表现为上百用例在 setup 阶段集体退出。用默认临时目录即可（`backend/pyproject.toml` 已说明）。

**7. 前端构建报 SCSS 路径错误。**
`src/views/*.vue` 引用 `../styles/tokens.scss`，`src/components/**` 引用 `../../styles/tokens.scss`。写错一层不会在类型检查时报错，只会在运行时白屏，新增页面后建议实拍一次确认。
