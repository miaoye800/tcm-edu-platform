"""命令行运行 LangGraph 学习智能体。"""

from __future__ import annotations

import argparse
import asyncio

from app.agents.graph import export_mermaid, run_agent


async def main() -> int:
    parser = argparse.ArgumentParser(description="运行岐黄智学 LangGraph 最小闭环")
    parser.add_argument("--user-id", default="anonymous", help="当前学生用户 ID")
    parser.add_argument("question", help="用户问题")
    args = parser.parse_args()

    export_mermaid()
    state = await run_agent(args.question, user_id=args.user_id)

    print("trace:")
    for item in state["trace"]:
        print(f"- {item}")
    print("\nanswer:")
    print(state["answer"])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
