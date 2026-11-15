"""注册表的装配。一个 ``install()``，可重复调用。

为什么不在模块 import 时注册：测试要能清空重来，而且 ``import app.capabilities``
本身不该有副作用（文档生成器会 import 它，导一次注册一次就乱了）。
"""

from __future__ import annotations

from . import registry
from .handlers import doc as doc_handlers
from .handlers import kb as kb_handlers
from .handlers import mindmap as mindmap_handlers
from .handlers import plan as plan_handlers
from .handlers import path_focus as path_focus_handlers
from .handlers import plan_present as plan_present_handlers
from .handlers import ppt as ppt_handlers
from .handlers import profile as profile_handlers
from .handlers import quiz as quiz_handlers
from .handlers import resource as resource_handlers
from .handlers import story as story_handlers
from . import kg_bridge

_INSTALLED = False


def install(*, force: bool = False) -> None:
    global _INSTALLED
    if _INSTALLED and not force:
        return
    if force:
        registry.clear()
    for spec, handler in mindmap_handlers.SPECS:
        registry.register(spec, handler)
    for spec, handler in ppt_handlers.SPECS:
        registry.register(spec, handler)
    for spec, handler in doc_handlers.SPECS:
        registry.register(spec, handler)
    for spec, handler in quiz_handlers.SPECS:
        registry.register(spec, handler)
    for spec, handler in story_handlers.SPECS:
        registry.register(spec, handler)
    for spec, handler in profile_handlers.SPECS:
        registry.register(spec, handler)
    for spec, handler in plan_handlers.SPECS:
        registry.register(spec, handler)
    for spec, handler in plan_present_handlers.SPECS:
        registry.register(spec, handler)
    for spec, handler in path_focus_handlers.SPECS:
        registry.register(spec, handler)
    for spec, handler in resource_handlers.SPECS:
        registry.register(spec, handler)
    for spec, handler in kb_handlers.SPECS:
        registry.register(spec, handler)
    for spec, handler in kg_bridge.specs():
        registry.register(spec, handler)
    _INSTALLED = True


def reset() -> None:
    """只给测试用。"""

    global _INSTALLED
    registry.clear()
    _INSTALLED = False
