"""Run the fixed, sanitized profile quality baseline.

Examples:
  python scripts/evaluate_profile_quality.py
  python scripts/evaluate_profile_quality.py --mode model --model deepseek-chat --version v1
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.profile.evaluation import evaluate_profile_fixture, load_fixture


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=Path("tests/fixtures/profile_eval_cases.json"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mode", choices=("no-key", "model"), default="no-key")
    parser.add_argument("--model", default="")
    parser.add_argument("--version", default="")
    args = parser.parse_args()
    report = evaluate_profile_fixture(load_fixture(args.fixture), mode=args.mode, model=args.model or None, version=args.version or None)
    report["task"] = "profile_extract"
    report["model"] = {"name": args.model or None, "version": args.version or None, "temperature": 0}
    if args.mode == "model":
        report["status"] = "model_not_evaluated"
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
