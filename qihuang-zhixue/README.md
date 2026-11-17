# 岐黄智学（工程源码）

本目录是「岐黄智学」的实际工程，后端 `backend/`（FastAPI）、前端 `frontend/`（Vue 3 + TypeScript）。

项目介绍、技术架构、安装运行、团队信息写在仓库根目录的 [README.md](../README.md)，环境变量与依赖清单见 [ENVIRONMENT.md](../ENVIRONMENT.md)。这里只补几条跟工程目录直接相关的事：

```bash
# 起后端
cd backend && python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt && cp .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# 起前端
cd frontend && npm ci && npm run dev
```

- 演示账号：`disciple_a` / `disciple_b` / `disciple_c`，密码 `demo1234`（`python -m app.auth.seed` 初始化）
- 后端测试：`cd backend && pytest`；前端契约：`cd frontend && npm test`
- 能力契约文档由代码生成，改完能力跑 `python -m app.capabilities.docs` 重新产出 `docs/capabilities.md`
- `backend/data/meridian/*.gz` 是 3D 经脉场景的唯一数据源，缺了场景会崩，别删
