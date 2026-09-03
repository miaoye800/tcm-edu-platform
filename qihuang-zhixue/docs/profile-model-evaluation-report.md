# 画像与自由处方质量评测报告

## 范围与隐私

本会话新增 `backend/tests/fixtures/profile_eval_cases.json`（13 条）和
`prescription_eval_cases.json`（6 条）。样本是人工编写的中文短句和处方，覆盖考研、兴趣、转行、临床及混合目标；稳定/碎片/周期/不可安排的时间；明确、谦虚和模糊基础自述；视频、题目、病例、图谱、实践及混合风格；错误、重复错误、回避和间接弱项；否定、闲聊以及隐晦表达。处方样本覆盖两个合理解、异常剂量、毒性药材、未知药名和无法解析病例。样本不含真实用户对话、个人标识、API Key 或供应商响应正文。

## 入口与运行

仓库中未发现 `backend/AGENTS.md`，本会话未创建新的无关规范文件。

```powershell
cd backend
python scripts/evaluate_profile_quality.py --output ..\deliverables\profile-quality-baseline.json
python scripts/evaluate_prescription_quality.py --output ..\deliverables\prescription-quality-baseline.json
```

未来取得额度后，必须显式指定固定样本、任务和模型版本，例如：

```powershell
python scripts/evaluate_profile_quality.py --mode model --model deepseek-chat --version 2026-09-02
```

当前 `--mode model` 只产生 `model_not_evaluated`，不会调用供应商。缺少 Key、HTTP 402、非法 JSON 和超时在模型运行器接入后分别计数；不会把规则回退通过率当作模型质量，也不会保存 Key 或上传真实数据。评测器不写入 profile、radar、session。

## no-key 规则基线

最近一次运行（2026-09-02）结果：

| 指标 | 结果 |
|---|---:|
| evidence extraction precision / recall | 0.3846 / 0.3846 |
| quote 子串通过率 | 1.0000 |
| 七维名称合法率 | 1.0000 |
| metadata（basis/confidence/method/KB ref）合法率 | 1.0000 |
| 否定表达误判率 | 0.0000 |
| 重复运行确定性 | 1.0000 |
| 重复自述衰减 | PASS |
| self-report 客观维度置信度上限 | PASS |
| jury 锚点范围检查 | PASS |
| guardrail 最终约束 | PASS |
| provenance 一致性 | PASS (`no_key_rule_fallback`) |

以上是规则路径观察结果，不是模型质量结论。当前真实供应商 Key 返回 HTTP 402，因此本会话明确记录：**未验证模型质量**。

## 自由处方矩阵

处方评估只输出 `symptom_match_status`、兼容性、剂量/毒性观察和可追溯 findings；不绕过 `fusion -> jury -> guardrail` 写画像分数。最近一次运行：finding quote 子串通过率 1.0000、KB 引用格式/定位率 1.0000、finding recall 1.0000、无法解析病例为 `uncertain`，provenance 均为 `no_key_rule_fallback`。两个合理处方均标记 `multiple_solution_allowed`，不会因答案不唯一判错。

每个 finding 包含原文 quote、`knowledge_base_refs`、规则名和原因。异常剂量、毒性误用、未知药名、禁忌配伍和病例不匹配均可单独定位；失败案例还包含 `case_id`、脱敏输入、失败指标、实际/期望摘要、回退状态、provenance 和建议修复方向。

## 边界与已知限制

隐晦表达的规则召回率仍低于完整覆盖，后续应在固定样本上扩展词法/语义规则并保持 quote 可追溯。真实模型评测需外部额度和明确模型版本；温度应固定为 0，并记录样本哈希、响应结构校验、错误分类和耗时。评测失败不得阻塞无 Key 用户流程。

本会话未修改其他会话的既有工作树内容，也未调整命格卡翻牌资格、SSE 顺序或前端隐私门禁。
