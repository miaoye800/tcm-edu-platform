# 画像真实模型评测边界

截至会话 10，画像对话链路支持两类可重复结果：

- 未配置模型 Key：规则抽取与评分，`extraction_method/provenance=no_key_rule_fallback`。
- 已配置但模型不可用、超时、HTTP 401/402/429/5xx 或返回非法结构：规则抽取与评分，`extraction_method/provenance=rule_fallback`。

模型路由会对 429 和 5xx 进行有限重试；401、402、非法响应不重试。用户可见 SSE 不包含 `[MOCK:...]`、`FallbackReason`、供应商响应正文或内部异常类名。规则结果不会标记为模型结果。

后续取得额度后，可使用脱敏固定对话样本调用 `profile_extract`，逐项检查：JSON 可解析、quote 是用户原文子串、维度/标签合法、证据 metadata 完整、输出不含最终分数决定字段，以及非法响应进入 `rule_fallback`。评测不得持久化 API Key 或上传真实用户数据。

本会话真实模型仅观察到供应商 HTTP 402（余额不足），因此**未验证模型质量**。该边界不影响无 Key 或模型不可用时的产品可用性与诊断性。
