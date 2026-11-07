"""错误码与它的 HTTP 映射。

错误码是**契约的一部分**：调用方靠它决定是重试、换参数、还是告诉用户。
所以码本身要稳定（``mindmap.topic_empty`` 不许改名），文案可以改。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    """可预期失败。5xx 不在这里——那是 bug，不是契约。"""

    UNKNOWN_CAPABILITY = "capability.unknown"
    INVALID_INPUT = "capability.invalid_input"
    NOT_FOUND = "capability.not_found"
    FORBIDDEN = "capability.forbidden"
    CONFLICT = "capability.conflict"
    IDEMPOTENCY_MISMATCH = "capability.idempotency_mismatch"
    TOPIC_EMPTY = "mindmap.topic_empty"
    RESOURCE_NOT_FOUND = "mindmap.resource_not_found"
    ARTIFACT_NOT_READY = "mindmap.artifact_not_ready"
    STRUCTURE_UNKNOWN = "mindmap.structure_unknown"
    THEME_UNKNOWN = "mindmap.theme_unknown"
    ALREADY_FINISHED = "invocation.already_finished"
    NOT_IMPLEMENTED = "capability.not_implemented"


#: 码 → (HTTP 状态, 默认文案)。文案只在这里写一次。
_TABLE: dict[ErrorCode, tuple[int, str]] = {
    ErrorCode.UNKNOWN_CAPABILITY: (404, "没有这个能力"),
    ErrorCode.INVALID_INPUT: (422, "参数不合法"),
    ErrorCode.NOT_FOUND: (404, "找不到"),
    ErrorCode.FORBIDDEN: (403, "没有权限"),
    ErrorCode.CONFLICT: (409, "状态冲突"),
    ErrorCode.IDEMPOTENCY_MISMATCH: (409, "同一个请求号被用于不同的请求"),
    ErrorCode.TOPIC_EMPTY: (422, "请先说要画什么"),
    ErrorCode.RESOURCE_NOT_FOUND: (404, "这份导图不存在"),
    ErrorCode.ARTIFACT_NOT_READY: (409, "导图还在生成中"),
    ErrorCode.STRUCTURE_UNKNOWN: (422, "不认识这种结构"),
    ErrorCode.THEME_UNKNOWN: (422, "不认识这套配色"),
    ErrorCode.ALREADY_FINISHED: (409, "已经结束了，不能再取消"),
    ErrorCode.NOT_IMPLEMENTED: (501, "这条契约还没接"),
}


@dataclass
class CapabilityError(Exception):
    """可预期失败。dispatch 统一转成响应体，handler 只管抛。"""

    code: ErrorCode
    detail: str = ""
    remedy: str = ""
    http_status: int = 0

    def __post_init__(self) -> None:
        status, default = _TABLE[self.code]
        self.http_status = self.http_status or status
        self.detail = self.detail or default
        super().__init__(self.detail)

    def body(self) -> dict[str, object]:
        payload: dict[str, object] = {"ok": False, "code": str(self.code), "message": self.detail}
        if self.remedy:
            payload["remedy"] = self.remedy
        return payload
