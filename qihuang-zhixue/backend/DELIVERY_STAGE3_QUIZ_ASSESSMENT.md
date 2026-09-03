# 阶段 3 后端交付报告：题库、QuizAgent 与混合考核

## 1. 改动文件

- `app/quiz/constants.py`：唯一的考核权重、阈值、倒计时与防重样常量表。
- `app/quiz/schemas.py`：严格 Pydantic 题目、下发题目、判分与考核请求契约。
- `app/quiz/prompts.py`：语义判分提示词集中管理。
- `app/quiz/storage.py`：题库、答题历史、错题与考核会话 DDL、读写接口。
- `app/quiz/seed.py`：按 `knowledge_node` 生成可重跑的原创种子题。
- `app/quiz/service.py`：组卷、来源校验、服务端判分、BKT/画像回流与考核编排。
- `app/api/quiz.py`：`/api/quiz/*`、`/api/assessment/*`。
- `app/api/graph.py`：增加 `GET /api/graph/default-center`。
- `app/main.py`：注册 quiz router。
- `scripts/build_question_bank.py`：online/local/seed 三模式建库工具。
- `tests/test_quiz_assessment.py`：阶段 3 API 与存储单测。

## 2. 关键结构摘录

```sql
CREATE TABLE IF NOT EXISTS exam_point (
  id TEXT PRIMARY KEY, node_id TEXT NOT NULL REFERENCES knowledge_node(id), name TEXT NOT NULL,
  exam_years_json TEXT NOT NULL DEFAULT '[]', frequency REAL NOT NULL DEFAULT 0,
  source TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(node_id, name)
);
CREATE TABLE IF NOT EXISTS real_question (
  id TEXT PRIMARY KEY, exam_point_id TEXT NOT NULL REFERENCES exam_point(id), stem TEXT NOT NULL,
  options_json TEXT NOT NULL DEFAULT '[]', answer_json TEXT NOT NULL, analysis TEXT NOT NULL,
  year INTEGER, source TEXT NOT NULL,
  question_type TEXT NOT NULL CHECK(question_type IN ('single','multi','case','short')),
  created_at TEXT NOT NULL, UNIQUE(exam_point_id, stem)
);
CREATE TABLE IF NOT EXISTS mistake_record (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, question_id TEXT NOT NULL,
  exam_point_id TEXT NOT NULL, answer_json TEXT NOT NULL, expected_json TEXT NOT NULL,
  remediation_json TEXT NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT,
  UNIQUE(user_id, question_id, created_at)
);
```

内部题组 JSON schema（服务端使用，严禁直接下发）：

```json
{
  "scenario": "practice|assessment",
  "questions": [{
    "id": "string", "stem": "string", "options": [{"key": "A", "text": "string"}],
    "answer": ["A 或主观题要点"], "analysis": "string",
    "question_type": "single|multi|case|short", "exam_point_id": "string",
    "source_level": "real|adapted|original", "source_detail": {}
  }]
}
```

`real` 必须有 `source_detail.year` 与 `source_detail.source`；`adapted` 必须有
`adapted_from_question_id` 与 `changes`；`original` 必须有 `knowledge_node_id`。
下发投影严格删除 `answer`、`analysis`。

```python
ASSESSMENT_PASS_THRESHOLDS = {"宽松": 60, "标准": 70, "严格": 80}
ASSESSMENT_GATE_WEIGHTS = {"quick": 0.50, "case": 0.35, "optional": 0.15}
QUICK_QUESTION_COUNT = 3
QUICK_RECOMMENDED_SECONDS = 45
CASE_RECOMMENDED_SECONDS = 180
OPTIONAL_RECOMMENDED_SECONDS = 120
RECENT_QUESTION_LIMIT = 20
HIGH_FUN_PREFERENCE = 0.70
```

考核状态机：`start(started)` -> `answer`（每题安全判分、BKT、画像、错题写入） ->
`finish(finished)`。所有关卡回答完毕才允许 finish。综合分按已激活关卡的权重归一；达标后用
既有 `user_path_state.status="completed"`，其 `meta` 保存 `assessment_id/score/gate_scores/threshold/finished_at`。

## 3. API 变更

| API | 请求示例 | 响应要点 |
|---|---|---|
| `POST /api/quiz/generate?node_id=general.p1` | 无 body | `questions[]`，不含答案解析 |
| `POST /api/quiz/grade` | `{"question_id":"rq_x","answer":["A"]}` | `correct/score/missing_points/comment/analysis/remediation` |
| `POST /api/assessment/start` | `{"node_id":"general.p1"}` | `assessment_id/threshold/gates[]`，题目无答案解析 |
| `POST /api/assessment/answer` | `{"assessment_id":"as_x","question_id":"rq_x","answer":"..."}` | 与 grade 相同，且写入会话 |
| `POST /api/assessment/finish` | `{"assessment_id":"as_x"}` | `score/gate_scores/threshold/passed/weak_exam_point_ids/remediation` |
| `GET /api/assessment/{id}` | 无 body | 已落库的考核结果；已判题才会包含该题解析 |
| `GET /api/graph/default-center` | 无 body | `{"center_id":"当前节点或null"}` |

`/api/quiz/grade` 和 assessment 一律以 `question_id` 从服务器题库读取 `answer_json`；不接收
`correct_answer`。旧 `/api/quiz/submit` 未变。

## 4. 新增常量理由

- 宽松/标准/严格 60/70/80 是考核综合分门槛，和 BKT 的 `0.8` 掌握度阈值完全独立。
- 50/35/15 保证基础快答仍是主导，病例体现迁移能力，画像触发的表达关不反客为主。
- 45/180/120 秒是展示用建议倒计时，不参与判分。
- 20 道近期历史和 8 个最近考点用于降权，优先避免重复题面；题库不足时允许退化。

## 5. 建库脚本

```powershell
cd backend
python scripts/build_question_bank.py --mode online
python scripts/build_question_bank.py --mode local --input-dir .\question-materials
python scripts/build_question_bank.py --mode seed
```

online 模式只在建库时探测网络，候选公开入口目前为国家医学考试网和研招网；仅当页面提供明确的
结构化 `question_bank` 数据时才导入。否则报告 `source_has_no_structured_question_bank`，不会猜测年份、来源、答案；无网络或无可验证记录自动走 seed。运行时 API 不联网。

本次 `backend/.data/qihuang.db` 结果：`exam_point=36`、`real_question=36`、`mistake_record=0`。
这 36 道均为 `original: knowledge_node:<id>`，没有真实题导入。online 探测报告中两个候选源均因无可验证结构化题库被丢弃；无伪造年份/来源。

本地格式模板（CSV 表头）：

```csv
node_id,name,stem,options_json,answer_json,analysis,year,source,question_type,source_level,adapted_from_question_id,changes
general.p1,性味归经,题干,"[{""key"":""A"",""text"":""...""}]","[""A""]",解析,2024,https://example.org/source,single,real,,
general.p1,性味归经,改编题,"[]","[""性味归经""]",解析,,原题来源,short,adapted,rq_origin,将单选改为简答
```

JSON 为上述列名对应对象数组；Markdown 每个 ````json` 代码块放一个同形 JSON 对象。

## 6. 测试

`tests/test_quiz_assessment.py` 覆盖：来源硬规则与下发脱敏、不同画像组卷、近期题降权、无 Key
语义关键词降级、错题/remediation/BKT/画像事件、三接口完整考核流程及 `completed` 写回、建库幂等。

专用测试结果：`5 passed`。既有 `tests/test_learning_api.py`：`3 passed`。最终在独立
`--basetemp` 目录串行运行 `pytest -q`：`223 passed, 1 warning in 75.61s`；唯一 warning 是既有
`/api/path` 兼容入口的 deprecation 提示。

## 7. 未尽事项与假设

- 仓库没有既有错题本实现，因此新建 `mistake_record`；其写入与补救链接由 grade 统一负责。
- 公开真题常受版权和页面结构限制。当前脚本只导入明确结构化且年源齐全的记录；应由内容团队提供授权本地 CSV/JSON 后增量运行 local 模式。
- 阶段 2 旧 `agents/quiz_agent.py` 保持资源适配职责，建议前端逐步把练习/过关迁至新 API 后再将旧生成器标记 deprecated；本会话未修改它。
- 旧 `/api/quiz/submit` 仍信任客户端 `correct_answer`，这是已识别安全缺口。保留是兼容要求；后续前端迁移完成后应废弃或改为仅接收 `question_id`。
