"""岐黄智学多智能体最小闭环。"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agents.graph import State, build_graph, export_mermaid, run_agent

__all__ = ["State", "build_graph", "export_mermaid", "run_agent"]


def __getattr__(name: str) -> Any:
    """按需导入 LangGraph，避免运行子模块时提前触发第三方 warning。"""

    if name in __all__:
        from app.agents import graph

        return getattr(graph, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
