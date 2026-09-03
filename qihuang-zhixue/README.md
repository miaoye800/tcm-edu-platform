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

部署须知：正式部署前必须设置 `AUTH_SECRET` 环境变量，否则 JWT 使用开发默认密钥不安全。

数据库默认使用 `backend/.data/qihuang.db`。如需覆盖，设置 `QIHUANG_DB_PATH`；相对路径按 `backend/` 目录解析，也支持绝对路径。

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

初始化演示账号：

```bash
cd backend
python -m app.auth.seed
```

默认预置账号密码均为 `demo1234`：

```text
disciple_a
disciple_b
disciple_c
```

注册、登录并携带 token 访问当前用户画像：

```bash
curl -X POST http://127.0.0.1:8000/api/auth/register ^
  -H "Content-Type: application/json" ^
  -d "{\"username\":\"demo_student\",\"password\":\"demo1234\",\"display_name\":\"演示学生\"}"

curl -X POST http://127.0.0.1:8000/api/auth/login ^
  -H "Content-Type: application/json" ^
  -d "{\"username\":\"demo_student\",\"password\":\"demo1234\"}"

curl http://127.0.0.1:8000/api/profile ^
  -H "Authorization: Bearer <上一步返回的 token>"
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
