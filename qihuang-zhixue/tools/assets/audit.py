#!/usr/bin/env python
"""素材登记的构建期校验。**只用标准库**，CI 可以直接跑。

    python tools/assets/audit.py

为什么是标准库：这条检查要在没有图像依赖、没有虚拟环境的 CI 里也能跑。
它查的是「登记表与磁盘是否自洽」，不需要解码图片——需要解码的那条（归一化可复现）
单独标注为 dev-only，不在 CI 里跑。

十条对应 ``docs/studios/assets.md`` §9。任何一条不过就以非零码退出。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "frontend" / "public"
REGISTRY = PUBLIC / "assets" / "registry.json"
FRONTEND_SRC = ROOT / "frontend" / "src"
LICENSES_PY = ROOT / "backend" / "app" / "resources" / "licenses.py"


def _load_licenses() -> tuple[set[str], set[str], set[str]]:
    """从后端加载许可白名单，保证「入库」与「运行时选材」用的是同一张表。

    这里不 ``import app.resources.licenses``：本脚本的规矩是只用标准库、
    不需要虚拟环境，而 ``backend/`` 不在 sys.path 上。``licenses.py`` 特意
    写成无依赖的常量模块，正是为了这种按路径加载。

    读不到就退回内置的那份并**打印警告**——不能因为后端挪了位置就静默放行
    或静默拒绝。
    """

    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("_qh_licenses", LICENSES_PY)
        if spec is None or spec.loader is None:
            raise ImportError("spec 为空")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        allowed = set(module.LICENSE_ALLOWLIST)
        needs = set(module.LICENSES_NEEDING_ATTRIBUTION)
        demo = set(getattr(module, "DEMO_ONLY_LICENSES", ()))
        if not allowed:
            raise ValueError("白名单为空")
        return allowed, needs, demo
    except Exception as exc:  # noqa: BLE001 - 兜底必须能跑完，否则 CI 直接红
        print(f"[警告] 读不到 {LICENSES_PY}（{exc}），使用内置白名单；可能与运行时不一致")
        return (
            {"PD-old", "CC0", "CC-BY-4.0", "MIT", "Apache-2.0"},
            {"CC-BY-4.0"},
            {"CC-BY-NC-4.0"},
        )


#: 许可白名单。不在表里的一律拒绝——「来源不明」不是一种许可。
#: 唯一来源是 ``backend/app/resources/licenses.py``：过去两张表不一致，
#: 导致图标通过入库却在选材时被判 -1。
ALLOWED_LICENSES, NEEDS_ATTRIBUTION, DEMO_ONLY = _load_licenses()
SLOT_CLASSES = {"evidence", "decorative"}
KINDS = {"plate", "photo", "motif", "generated"}
#: 体积预算（字节）。与 assets.md §10 一致。
MAX_BYTES = {"plate": 200 * 1024, "photo": 400 * 1024, "motif": 80 * 1024, "generated": 400 * 1024}
TOTAL_BUDGET = 8 * 1024 * 1024
ASSET_ID = re.compile(r"^(plate|photo|motif|generated)-[a-z0-9]+(-[a-z0-9]+)*-\d{2}$")
#: 代码里引用资产 id 的字面量。用于对治 ``XiaoqiMascot.vue`` 式静默兜底：
#: 引用一个不存在的文件，页面会安静地空一块，谁也不知道。
ASSET_LITERAL = re.compile(r"""["'`]((?:plate|photo|motif|generated)-[a-z0-9-]+-\d{2})["'`]""")


def load() -> dict:
    if not REGISTRY.exists():
        return {"version": 1, "assets": []}
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def audit() -> list[str]:
    data = load()
    assets = data.get("assets") or []
    problems: list[str] = []
    total = 0
    seen_ids: set[str] = set()
    seen_digest: dict[str, str] = {}

    for asset in assets:
        asset_id = str(asset.get("id") or "")
        kind = str(asset.get("kind") or "")
        slot_class = str(asset.get("slot_class") or "")
        license_id = str(asset.get("license") or "")
        file_rel = str(asset.get("file") or "")

        # 1. 文件存在
        target = PUBLIC / file_rel if file_rel else None
        if not target or not target.exists():
            problems.append(f"{asset_id}: 文件不存在（{file_rel}）")
            continue
        payload = target.read_bytes()
        total += len(payload)

        # 2. sha256 与实际一致
        digest = hashlib.sha256(payload).hexdigest()
        if str(asset.get("sha256") or "") != digest:
            problems.append(f"{asset_id}: sha256 不符（登记 {str(asset.get('sha256'))[:8]}…，实际 {digest[:8]}…）")

        # 3. 许可在白名单
        if license_id not in ALLOWED_LICENSES:
            problems.append(f"{asset_id}: 许可不在白名单（{license_id}）")

        # 4. 槽位类别合法
        if slot_class not in SLOT_CLASSES:
            problems.append(f"{asset_id}: slot_class 非法（{slot_class}）")

        # 5. CC-BY 必须署名
        if license_id in NEEDS_ATTRIBUTION and not str(asset.get("attribution") or "").strip():
            problems.append(f"{asset_id}: CC-BY 缺 attribution")

        # 6. 体积
        limit = MAX_BYTES.get(kind, 400 * 1024)
        if len(payload) > limit:
            problems.append(f"{asset_id}: 单张 {len(payload) // 1024}KB 超预算 {limit // 1024}KB")

        # 8. subjects 至少一项，或显式「通用」
        if not (asset.get("subjects") or []):
            problems.append(f"{asset_id}: subjects 为空")

        # 命名与去重
        if not ASSET_ID.match(asset_id):
            problems.append(f"{asset_id}: id 不合命名规则 {{kind}}-{{subject}}-{{seq}}")
        if asset_id in seen_ids:
            problems.append(f"{asset_id}: id 重复")
        seen_ids.add(asset_id)
        if kind not in KINDS:
            problems.append(f"{asset_id}: kind 非法（{kind}）")
        # 9. 同一文件不得登记两次。
        #    过去这条按「同 subject + 同 treatment」判重，整包套组一进来就整排报错：
        #    九只造型各异的陶罐都叫「陶罐」，但它们是九张不同的图。真正要防的是
        #    同一个文件占了两个 id，那看字节就够了。
        if digest in seen_digest:
            problems.append(f"{asset_id}: 与 {seen_digest[digest]} 字节完全相同（重复登记）")
        seen_digest[digest] = asset_id

    if total > TOTAL_BUDGET:
        problems.append(f"总量 {total // 1024}KB 超预算 {TOTAL_BUDGET // 1024}KB")

    # 7. 代码里的资产 id 字面量必须在登记表里
    known = {str(asset.get("id") or "") for asset in assets}
    if FRONTEND_SRC.exists():
        for path in FRONTEND_SRC.rglob("*"):
            if path.suffix not in {".ts", ".vue", ".js"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for match in ASSET_LITERAL.finditer(text):
                if match.group(1) not in known:
                    problems.append(f"{path.relative_to(ROOT)}: 引用了未登记的资产 {match.group(1)}")

    return problems


def demo_only_assets() -> list[str]:
    """使用演示期许可的素材 id。

    这类许可（``CC BY-NC`` 一族）只在本项目当前「比赛演示、不对外商业服务」
    的定位下成立。商业化之前必须逐条替换，所以每次跑 audit 都要把它们数出来，
    而不是等人记起这件事。
    """

    return [
        str(asset.get("id") or "")
        for asset in (load().get("assets") or [])
        if str(asset.get("license") or "") in DEMO_ONLY
    ]


def main() -> int:
    problems = audit()
    if problems:
        print("素材登记表校验未通过：")
        for item in problems:
            print(f"  - {item}")
        return 1
    data = load()
    count = len(data.get("assets") or [])
    demo = demo_only_assets()
    if demo:
        print(f"[提示] {len(demo)}/{count} 条素材使用演示期许可（{', '.join(sorted(DEMO_ONLY))}）：")
        print(f"       {', '.join(demo[:6])}{' …' if len(demo) > 6 else ''}")
        print("       商业化上线前必须替换这些素材。")
    print(f"素材登记表校验通过（{count} 条）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
