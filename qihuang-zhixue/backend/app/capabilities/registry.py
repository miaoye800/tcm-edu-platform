"""注册表。一个能力只登记一次，重复登记直接炸——静默覆盖过一次就很难查。

handler 与 spec 分开存：spec 是纯数据（喂给文档与 tool schema），handler 才碰 IO。
这样「有哪些能力」在没有数据库、没有网络的环境里也答得出来。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .spec import CapabilityResult, CapabilitySpec

Handler = Callable[..., Awaitable[CapabilityResult]]
#: 调用方身份，由 http 层从鉴权结果里取，handler 不自己解析 token。
Actor = Any


@dataclass(frozen=True)
class Entry:
    spec: CapabilitySpec
    handler: Handler


_REGISTRY: dict[str, Entry] = {}


def register(spec: CapabilitySpec, handler: Handler) -> Entry:
    if not spec.name or "." not in spec.name:
        # 「mindmap.generate」这种两段式是刻意的：前缀是领域，调用方按前缀筛能力。
        raise ValueError(f"能力名要写成 <domain>.<verb>：{spec.name!r}")
    if spec.name in _REGISTRY:
        raise ValueError(f"能力重复注册：{spec.name}")
    entry = Entry(spec=spec, handler=handler)
    _REGISTRY[spec.name] = entry
    return entry


def get(name: str) -> Entry | None:
    return _REGISTRY.get(name)


def all_specs() -> list[CapabilitySpec]:
    """稳定顺序：按名字排。文档 diff 才不会因为注册顺序变来变去。"""

    return [entry.spec for _, entry in sorted(_REGISTRY.items())]


def tool_schemas(prefix: str = "") -> list[dict[str, Any]]:
    return [spec.tool_schema() for spec in all_specs() if spec.name.startswith(prefix)]


def snapshot() -> dict[str, Any]:
    """给 docs 与判据测试用的快照。带 schema 版本号，改了字段能被发现。"""

    return {
        "schema_version": 1,
        "count": len(_REGISTRY),
        "capabilities": [spec.public() for spec in all_specs()],
    }


def clear() -> None:
    """只给测试用。生产代码不该清空注册表。"""

    _REGISTRY.clear()
