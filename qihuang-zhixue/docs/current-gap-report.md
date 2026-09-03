# Current Gap Report

审计范围：画像、问诊（dialogue）、收徒（apprentice）、雷达、SQLite 迁移及对应前端契约。未修改评分算法和视觉页面。

## 实际问题与风险

- `backend/AGENTS.md` 不存在；`frontend/AGENTS.md` 已读取并遵守。
- 默认数据库路径已统一为 `backend/.data/qihuang.db`，可由 `QIHUANG_DB_PATH` 覆盖（相对路径按 backend 目录解析）。profile 与 mastery 存储复用同一解析路径。
- `profile_sessions` 迁移会补齐 `step_index`、`persisted`、`step_data_json`、`evidence_cards_json` 等字段；若旧表完全缺少 `session_id`，旧表会先保留为带 UTC 时间戳的备份表，新表不自动猜测旧行的 session_id。
- 收徒动漫演出依赖的 `StoryStage`、`frontend/public/story` 和最终 `seg_*.mp4`/视频资源均不存在；当前仍使用现有 ProfileStep 表单流程。按设计应由演出层追加，不影响后端证据/评分契约。
- 未发现本次“收徒 start 500”复现：临时库和 `backend/.data/qihuang.db` 均返回 200，首步 `apprentice_prologue`。

## 接口契约核对

- `POST /api/profile/session/start`：请求 `{user_id?, mode}`；返回 `session_id/user_id/mode/status/step/current_stage/finished/evidence_count`。
- `POST /api/profile/session/answer`：请求 `session_id + step_id + payload`（兼容 `answer`、`response_time_ms`）；返回下一步、状态和新增 `evidence_cards`。dialogue 额外返回会话摘要、雷达及命格卡相关状态。
- `POST /api/profile/session/finish`：按用户校验 session，持久化证据/画像并返回雷达与变化摘要。
- profile/radar 使用七维画像；雷达暴露 `axes`、`values`、`confidences` 及低置信状态，未改动评分契约。

## 测试与运行结果

- 定向：`pytest -q backend/tests/test_modes.py backend/tests/test_store_migration.py` -> **43 passed**。
- 后端全量：在 `backend` 目录运行 `pytest -q` -> **223 passed, 1 warning**（`run_path_planning` 弃用警告）。
- 前端：`npm test` -> **全部通过**（10 个组件契约脚本）。
- 构建：`npm run build` -> **成功**；仅有 Rollup 注释提示和 chunk size >500 kB 警告。
- SQLite 烟测：临时 SQLite 与 `backend/.data/qihuang.db` 均执行 `profile/session/start` 成功（HTTP 200）；`profile`/`session` 相关初始化与迁移可执行。

## 后续依赖

1. 为缺失 `session_id` 的旧 `profile_sessions` 备份表制定人工核验与可恢复导入方案。
2. 生成并放置收徒分段视频到 `frontend/public/story/`，实现 StoryStage 及缺失资源降级呈现。
3. 用真实 API key 做问诊温暖度、弱点可达性、命格卡翻牌和雷达实时更新的端到端验收。
