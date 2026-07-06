"""学习路径规划 CLI。"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.agents.path_agent import export_path_mermaid, run_path_planning


async def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="打印岐黄智学学习路径、推荐与复习队列")
    parser.add_argument("user_id", help="学生用户 ID")
    args = parser.parse_args()

    export_path_mermaid()
    state = await run_path_planning(args.user_id)

    print("技能树:")
    for node in state["skill_tree"]:
        prerequisites = ", ".join(node["prerequisites"]) or "无"
        print(
            f"- {node['id']} | {node['title']} | status={node['status']} | "
            f"mastery={node['mastery']:.2f} | prerequisites={prerequisites}"
        )

    print("\nTop3 推荐:")
    if not state["recommendations"]:
        print("- 暂无推荐")
    for item in state["recommendations"]:
        print(
            f"- {item['title']} ({item['kc_id']}) | score={item['score']:.2f} | "
            f"mastery={item['mastery']:.2f} | confidence={item['confidence']:.2f} | {item['reason']}"
        )

    print("\n今日复习:")
    print(f"建议题量: {state['reviews']['suggested_count']}")
    if not state["reviews"]["items"]:
        print("- 暂无到期复习")
    for item in state["reviews"]["items"]:
        print(
            f"- {item['kc_id']} | due_at={item['due_at']} | "
            f"urgency={item['urgency']:.2f} | last_rating={item['last_rating']}"
        )

    print("\ntrace:")
    for item in state["trace"]:
        print(f"- {item['node']}: {item['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
