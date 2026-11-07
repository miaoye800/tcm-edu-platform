"""统计 LLM token 用量，回答"钱花在哪"。

Run from backend: ``python scripts/llm_cost_report.py``
                  ``python scripts/llm_cost_report.py --days 7``

只报 token 数，不硬编码单价 —— 各家价格和阶梯规则会变，自己按账单乘。
数据来源是 ``app/llm/usage.py`` 写入的 llm_usage 表；replay 命中的请求不产生
新行（因为没真调模型），这正是我们想看到的。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import resolve_database_path  # noqa: E402


def _table(headers: list[str], rows: list[tuple[object, ...]]) -> str:
    if not rows:
        return "  （无数据）"
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))
    lines = ["  " + "  ".join(header.ljust(widths[i]) for i, header in enumerate(headers))]
    lines.append("  " + "  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        lines.append("  " + "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM token 用量统计")
    parser.add_argument("--days", type=int, default=7, help="统计最近多少天，默认 7")
    args = parser.parse_args()

    db_path = resolve_database_path()
    if not db_path.exists():
        print(f"数据库不存在：{db_path}")
        return 1

    since = (datetime.now(timezone.utc) - timedelta(days=max(1, args.days))).isoformat(
        timespec="seconds"
    )
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='llm_usage'"
        ).fetchone()
        if not exists:
            print("llm_usage 表还不存在 —— 说明还没有走过新版 ModelRouter 的真实调用。")
            return 0

        total = conn.execute(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(prompt_tokens),0) AS pin, "
            "COALESCE(SUM(completion_tokens),0) AS pout FROM llm_usage WHERE created_at >= ?",
            (since,),
        ).fetchone()
        by_day = conn.execute(
            "SELECT substr(created_at,1,10) AS day, COUNT(*) AS calls, "
            "SUM(prompt_tokens) AS pin, SUM(completion_tokens) AS pout "
            "FROM llm_usage WHERE created_at >= ? GROUP BY day ORDER BY day DESC",
            (since,),
        ).fetchall()
        by_task = conn.execute(
            "SELECT task_type, COUNT(*) AS calls, SUM(prompt_tokens) AS pin, "
            "SUM(completion_tokens) AS pout FROM llm_usage WHERE created_at >= ? "
            "GROUP BY task_type ORDER BY (SUM(prompt_tokens)+SUM(completion_tokens)) DESC",
            (since,),
        ).fetchall()
        by_model = conn.execute(
            "SELECT provider, model, COUNT(*) AS calls, SUM(prompt_tokens) AS pin, "
            "SUM(completion_tokens) AS pout FROM llm_usage WHERE created_at >= ? "
            "GROUP BY provider, model ORDER BY (SUM(prompt_tokens)+SUM(completion_tokens)) DESC",
            (since,),
        ).fetchall()
        replay_count = 0
        has_replay = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='llm_replay'"
        ).fetchone()
        if has_replay:
            replay_count = int(
                conn.execute("SELECT COUNT(*) FROM llm_replay").fetchone()[0]
            )

    total_tokens = int(total["pin"]) + int(total["pout"])
    print(f"\n最近 {args.days} 天 · {db_path.name}")
    print(f"  真实调用 {total['calls']} 次 | 输入 {total['pin']:,} | 输出 {total['pout']:,} | 合计 {total_tokens:,} tokens")
    print(f"  已录制可回放 {replay_count} 条（这些请求再跑不会产生费用）\n")

    print("按天：")
    print(_table(
        ["日期", "调用", "输入", "输出"],
        [(row["day"], row["calls"], f"{row['pin']:,}", f"{row['pout']:,}") for row in by_day],
    ))

    print("\n按任务类型（token 从多到少）：")
    print(_table(
        ["task_type", "调用", "输入", "输出", "合计"],
        [
            (
                row["task_type"],
                row["calls"],
                f"{row['pin']:,}",
                f"{row['pout']:,}",
                f"{int(row['pin']) + int(row['pout']):,}",
            )
            for row in by_task
        ],
    ))

    print("\n按模型：")
    print(_table(
        ["provider", "model", "调用", "输入", "输出"],
        [
            (row["provider"], row["model"], row["calls"], f"{row['pin']:,}", f"{row['pout']:,}")
            for row in by_model
        ],
    ))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
