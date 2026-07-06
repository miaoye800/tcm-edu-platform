"""资源生成组 CLI。"""

from __future__ import annotations

import argparse
import asyncio

from app.agents.resource_schemas import ResourceType, payload_to_summary
from app.agents.resources_graph import DEFAULT_RESOURCE_TYPES, export_resource_mermaid, run_resource_generation


async def main() -> int:
    parser = argparse.ArgumentParser(description="运行岐黄智学资源生成组")
    parser.add_argument("topic", help="学习主题，例如：麻黄")
    parser.add_argument(
        "--types",
        default=",".join(DEFAULT_RESOURCE_TYPES),
        help="逗号分隔的资源类型：explain,graph,mindmap,quiz,reading",
    )
    parser.add_argument("--max-concurrency", type=int, default=5, help="LangGraph 并行节点最大并发数")
    args = parser.parse_args()

    resource_types = _parse_types(args.types)
    export_resource_mermaid()
    state = await run_resource_generation(
        topic=args.topic,
        resource_types=resource_types,
        max_concurrency=args.max_concurrency,
    )

    print("resources:")
    for resource_type in resource_types:
        resource = state["resources"].get(resource_type)
        if resource is None:
            print(f"- {resource_type}: 未生成")
            continue
        print(
            f"- {resource.type}: {payload_to_summary(resource.payload)} | "
            f"grounding.score={resource.grounding.score:.2f} | cache_key={resource.cache_key}"
        )

    print("\ntrace:")
    for item in state["trace"]:
        print(f"- {item.node}: {item.summary}")
    return 0


def _parse_types(raw: str) -> list[ResourceType]:
    allowed = set(DEFAULT_RESOURCE_TYPES)
    parsed = [item.strip() for item in raw.split(",") if item.strip()]
    invalid = [item for item in parsed if item not in allowed]
    if invalid:
        raise SystemExit(f"不支持的资源类型：{','.join(invalid)}")
    return [item for item in parsed if item in allowed] or list(DEFAULT_RESOURCE_TYPES)  # type: ignore[return-value]


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

