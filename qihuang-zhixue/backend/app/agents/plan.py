"""Structured L1 learning-path CLI."""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.path.service import create_l1_plan


async def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="打印岐黄智学 L1 学习路径骨架")
    parser.add_argument("user_id", help="学生用户 ID")
    args = parser.parse_args()

    result = await create_l1_plan(args.user_id)

    print(f"版本: v{result.version} fallback={result.fallback}")
    print(result.suggestion)
    print("\n大方向:")
    for stage in result.stages:
        print(
            f"- {stage.order}. {stage.domain_id} | locked={stage.order_locked} | {stage.reason}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
