"""知识库种子数据校验入口。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.kb.models import Herb


@dataclass(frozen=True)
class ValidationIssue:
    file: Path
    index: int
    item_id: str
    message: str


@dataclass(frozen=True)
class ValidationReport:
    total: int
    passed: int
    issues: list[ValidationIssue]
    verified_false: int = 0
    confidence_low: int = 0


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_items(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    raise ValueError("JSON 顶层必须是对象或对象数组")


def validate_herb_files(data_dir: Path | None = None) -> ValidationReport:
    """加载 data/herbs 下全部 JSON 并逐条校验。"""

    herbs_dir = data_dir or _backend_root() / "data" / "herbs"
    total = 0
    passed = 0
    verified_false = 0
    confidence_low = 0
    issues: list[ValidationIssue] = []

    for path in sorted(herbs_dir.glob("*.json")):
        try:
            items = _load_items(path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            issues.append(ValidationIssue(file=path, index=0, item_id="", message=str(exc)))
            continue

        for index, item in enumerate(items, start=1):
            total += 1
            item_id = str(item.get("id", ""))
            try:
                Herb.model_validate(item)
            except ValidationError as exc:
                issues.append(
                    ValidationIssue(
                        file=path,
                        index=index,
                        item_id=item_id,
                        message=str(exc),
                    )
                )
            else:
                passed += 1
                if item.get("verified") is False:
                    verified_false += 1
                if item.get("confidence") == "low":
                    confidence_low += 1

    return ValidationReport(
        total=total,
        passed=passed,
        issues=issues,
        verified_false=verified_false,
        confidence_low=confidence_low,
    )


async def main() -> int:
    report = validate_herb_files()
    print(f"总数: {report.total}")
    print(f"格式通过数: {report.passed}")
    print(f"错误: {len(report.issues)}")
    print(f"verified=false: {report.verified_false}")
    print(f"confidence=low: {report.confidence_low}")

    if report.issues:
        print("错误明细:")
        for issue in report.issues:
            rel_path = issue.file.relative_to(_backend_root())
            item_label = issue.item_id or f"第 {issue.index} 条"
            print(f"- {rel_path} [{item_label}]: {issue.message}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
