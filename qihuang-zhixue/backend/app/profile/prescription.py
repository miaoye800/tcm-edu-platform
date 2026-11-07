"""处方审查的**旧契约适配层**。

真正的实现已经搬到 ``app/kg/prescription.py``——它读 ``kg_edge`` 的 T1 边
（十八反/十九畏，双向）、做妊娠禁忌分级、并用「证候层 + 功效层」两层判断对症。

这个文件只负责把新的 ``ReviewResult`` 摊平成 ``PrescriptionEvaluation``，
因为 ``modes.py`` / ``update.py`` / ``evaluation.py`` 都依赖它的
``signal()`` 字段名。**不要在这里加业务判断**——加在这里的逻辑，
图谱侧和前端面板都看不到。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.profile.store import DB_PATH

if TYPE_CHECKING:  # 只用于类型标注，运行时不导入
    from app.kg.prescription import ReviewResult


@dataclass(frozen=True)
class PrescriptionEvaluation:
    """Traceable checks derived solely from the submitted case and prescription."""

    symptom_match: bool
    symptom_match_status: str
    compatible: bool
    dosage_ok: bool
    toxic_misuse: bool
    findings: list[dict[str, Any]]
    normalized_herbs: list[dict[str, Any]]
    knowledge_base_refs: list[str]
    quote: str
    method: str = "no_key_rule_fallback"
    #: 新增字段：处方整体结论与可追溯证据链。旧调用方不用它们，新面板用。
    verdict: str = "pass"
    trace: dict[str, Any] = field(default_factory=dict)

    def signal(self) -> dict[str, Any]:
        return {
            "\u5bf9\u75c7": self.symptom_match,
            "symptom_match_status": self.symptom_match_status,
            "\u914d\u4f0d": self.compatible,
            "\u5242\u91cf": self.dosage_ok,
            "\u8bef\u7528\u6bd2\u70c8": self.toxic_misuse,
            "prescription_findings": self.findings,
            "normalized_herbs": self.normalized_herbs,
            "knowledge_base_refs": self.knowledge_base_refs,
            "quote": self.quote or None,
            "basis": "explicit",
            "extraction_method": self.method,
            "prescription_verdict": self.verdict,
            "prescription_trace": self.trace,
        }


def evaluate_prescription(payload: dict[str, Any], db_path: Path = DB_PATH) -> PrescriptionEvaluation:
    """Evaluate a free prescription without assuming a single canonical formula."""

    # 延迟导入：``app.kg.prescription`` → ``app.kg.store`` → ``app.profile.store``
    # 会触发 ``app.profile.__init__``，而它又会一路导回本模块。在模块顶层导入会
    # 形成循环。放到调用时导入，两个包就都能独立被最先加载。
    from app.kg.prescription import review as kg_review

    return to_evaluation(kg_review(payload, db_path))


def to_evaluation(result: "ReviewResult") -> PrescriptionEvaluation:
    return PrescriptionEvaluation(
        symptom_match=result.symptom_match,
        symptom_match_status=result.symptom_match_status,
        compatible=result.compatible,
        dosage_ok=result.dosage_ok,
        toxic_misuse=result.toxic_misuse,
        findings=result.finding_dicts(),
        normalized_herbs=[line.as_dict() for line in result.herbs],
        knowledge_base_refs=result.knowledge_base_refs,
        quote=result.quote,
        method=result.method,
        verdict=result.verdict,
        trace=result.trace,
    )


__all__ = ["PrescriptionEvaluation", "evaluate_prescription", "to_evaluation"]
