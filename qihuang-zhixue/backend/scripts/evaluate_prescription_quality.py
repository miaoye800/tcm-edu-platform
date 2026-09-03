"""Run the fixed, sanitized free-prescription evaluation matrix."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.profile.evaluation import evaluate_prescription_fixture, load_fixture


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=Path("tests/fixtures/prescription_eval_cases.json"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate_prescription_fixture(load_fixture(args.fixture))
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
