"""把经脉与穴位名录种进库。

**只种名录，不种坐标。** 坐标与模型版本强绑定（换模型 = 362 个点全部重新吸附），
必须等 P2 把几何 ``geometry_version`` 冻死之后再写。现在就把坐标写进去，
等于让"几何是父级"这条不可逆决策提前失效。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.meridian import store

CATALOG_PATH = Path(__file__).resolve().parent / "data" / "acupoints.json"


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or CATALOG_PATH).read_text(encoding="utf-8"))


def seed_catalog(db_path: Path | str = store.DB_PATH, *, catalog_path: Path | None = None) -> dict[str, Any]:
    """写入 14 经 362 穴名录。重复执行安全（全 upsert）。"""

    catalog = load_catalog(catalog_path)
    store.init_db(db_path)

    version = catalog.get("geometry_version") or ""
    for ch in catalog["channels"]:
        store.upsert_channel(
            channel_id=ch["id"],
            abbr=ch.get("abbr") or ch["id"],
            title=ch["title"],
            kind=ch.get("kind", "meridian"),
            view_bias=ch.get("view_bias", "front"),
            sort_hint=int(ch.get("sort_hint", 0)),
            geometry_version=version,
            db_path=db_path,
        )

    for pt in catalog["acupoints"]:
        store.upsert_acupoint(
            acupoint_id=pt["id"],
            code=pt["code"],
            name=pt["name"],
            pinyin=pt.get("pinyin", ""),
            channel_id=pt["channel_id"],
            side=pt.get("side", "L"),
            t=float(pt["t"]),
            db_path=db_path,
        )

    return {
        "channels": len(catalog["channels"]),
        "acupoints": store.count_acupoints(db_path),
        "midline": sum(1 for p in catalog["acupoints"] if p.get("side") == "M"),
    }


__all__ = ["CATALOG_PATH", "load_catalog", "seed_catalog", "main"]


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI 入口
    import argparse

    pa = argparse.ArgumentParser(description="种入 14 经 362 穴名录（不含 3D 坐标）")
    pa.add_argument("--db", default=None)
    args = pa.parse_args(argv)
    info = seed_catalog(Path(args.db) if args.db else store.DB_PATH)
    print(f"经脉 {info['channels']} 条 · 穴位 {info['acupoints']} 个（中线 {info['midline']}）")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
