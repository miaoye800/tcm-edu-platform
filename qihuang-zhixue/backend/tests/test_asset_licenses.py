"""素材许可表的单一来源契约。

这条测试存在的理由是一次真实的事故：``tools/assets/audit.py`` 与
``app/ppt/select.py`` 曾经各写一份许可白名单，前者多出 ``MIT``/``Apache-2.0``。
于是图标通过入库校验写进了登记表，选材时却被 ``score()`` 判 ``-1`` 排除——
入库了等于没入，而且两边都不报错。

现在白名单只在 ``backend/app/resources/licenses.py`` 写一次，这里守住它。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"加载失败：{path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_audit_and_selector_share_one_license_table() -> None:
    from app.ppt.select import LICENSE_ALLOWLIST, LICENSES_NEEDING_ATTRIBUTION

    audit = _load_module(ROOT / "tools" / "assets" / "audit.py", "_qh_audit_probe")

    assert audit.ALLOWED_LICENSES == set(LICENSE_ALLOWLIST), (
        "入库校验与运行时选材的许可表不一致：\n"
        f"  只在 audit：{sorted(audit.ALLOWED_LICENSES - set(LICENSE_ALLOWLIST))}\n"
        f"  只在 select：{sorted(set(LICENSE_ALLOWLIST) - audit.ALLOWED_LICENSES)}"
    )
    assert audit.NEEDS_ATTRIBUTION == set(LICENSES_NEEDING_ATTRIBUTION)


def test_noncommercial_licenses_only_enter_through_the_demo_gate() -> None:
    """非商业许可只能以「演示期」身份进白名单，且必须被单独标出来。

    ``pngimg`` 是 ``CC BY-NC 4.0``：允许非商业使用，但课件可能被用户拿去做收费
    培训，我们控制不了下游。本项目当前定位是比赛演示、不对外提供商业服务，
    所以放行——但必须同时进 ``DEMO_ONLY_LICENSES``，让 ``audit.py`` 每次把它数出来。

    换句话说：允许，但记账。不允许的是「悄悄收进来、没人记得清理」。
    """

    from app.ppt.select import LICENSE_ALLOWLIST
    from app.resources.licenses import DEMO_ONLY_LICENSES

    nc = {item for item in LICENSE_ALLOWLIST if "NC" in item.upper()}
    assert nc <= set(DEMO_ONLY_LICENSES), (
        "这些非商业许可进了白名单却没进演示名单，商业化时会漏掉："
        f"{sorted(nc - set(DEMO_ONLY_LICENSES))}"
    )
    assert set(DEMO_ONLY_LICENSES) <= set(LICENSE_ALLOWLIST), (
        "演示名单里有白名单外的许可，标记是空的："
        f"{sorted(set(DEMO_ONLY_LICENSES) - set(LICENSE_ALLOWLIST))}"
    )
