# 岐黄智学

面向中医学专业学生的个性化学习多智能体系统比赛 demo。本仓库采用 monorepo 结构，当前 T0 仅初始化前后端最小可运行项目。

## 目录结构

```text
qihuang-zhixue/
  backend/
    app/
    tests/
  frontend/
    src/
```

## 启动后端

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

## 启动前端

```bash
cd frontend
npm install
npm run dev
```

默认访问：

```text
http://127.0.0.1:5173
```

前端会通过 Vite 代理调用后端 `GET /health`。

## 后端测试

```bash
cd backend
pytest
```
