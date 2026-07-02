"""药材知识库检索命令。"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.kb.retriever import search


async def main() -> int:
    parser = argparse.ArgumentParser(description="检索药材知识库")
    parser.add_argument("query", help="检索问题或关键词")
    parser.add_argument("--top-k", type=int, default=3, help="返回条数")
    parser.add_argument("--category", default=None, help="按药材一级分类过滤")
    args = parser.parse_args()

    hits = await search(args.query, top_k=args.top_k, category=args.category)
    for index, hit in enumerate(hits, start=1):
        sources = json.dumps(hit.sources, ensure_ascii=False)
        verified = str(hit.verified).lower()
        print(f"{index}. {hit.name}\t得分={hit.score:.4f}\tverified={verified}\tsources={sources}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
