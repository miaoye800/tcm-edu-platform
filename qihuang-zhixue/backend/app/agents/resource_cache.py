"""资源生成组的进程内幂等缓存。"""

from __future__ import annotations

from threading import Lock

from app.agents.resource_schemas import ResourceEnvelope

_CACHE: dict[str, ResourceEnvelope] = {}
_LOCK = Lock()


def get_cached_resource(cache_key: str) -> ResourceEnvelope | None:
    with _LOCK:
        return _CACHE.get(cache_key)


def set_cached_resource(resource: ResourceEnvelope) -> None:
    with _LOCK:
        _CACHE[resource.cache_key] = resource


def clear_resource_cache() -> None:
    with _LOCK:
        _CACHE.clear()

