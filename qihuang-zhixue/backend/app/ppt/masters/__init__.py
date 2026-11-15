"""母版目录：五套手工设计的视觉系统，单一事实源在后端。

- ``by_id`` / ``menu`` / ``default_master_for``：模型侧（spec._payload 的
  master_menu）与设计侧（design.propose_design 的回填）都从这里取；
- ``python -m app.ppt.masters`` 生成 ``frontend/src/config/pptMasters.ts``
  （**生成物，禁手改**，与 motifs.catalog.ts 同一套规矩）。

母版 = 后端代码确定性产出 SceneSpec；几何不问模型，模型只选系统、填内容。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import Master, MasterStyle, PageContext  # noqa: F401 - 对外稳定出口
from .celadon import MASTER as _CELADON
from .editorial import MASTER as _EDITORIAL
from .ink import MASTER as _INK
from .modern import MASTER as _MODERN
from .xuan import MASTER as _XUAN

#: 目录。顺序即菜单顺序（稳定的，别按字母排——宣纸是默认门面）。
MASTERS: dict[str, Master] = {
    master.spec.id: master
    for master in (_XUAN, _INK, _CELADON, _MODERN, _EDITORIAL)
}

#: 场合 → 默认母版。基线（无模型）与「模型选了不认识的母版」都回落到这里。
GENRE_DEFAULT_MASTER: dict[str, str] = {
    "lecture": "xuan-songkai",
    "orientation": "xuan-songkai",
    "brief": "modern-sans",
    "training": "celadon-wenkai",
    "defense": "ink-gold",
    "pitch": "ink-gold",
}

#: 没说场合时的默认（与 spec 的 genre 兜底一致）。
FALLBACK_MASTER = "xuan-songkai"


def by_id(master_id: str | None) -> Master | None:
    text = str(master_id or "").strip()
    return MASTERS.get(text) if text else None


def default_master_for(genre: str | None) -> str:
    return GENRE_DEFAULT_MASTER.get(str(genre or ""), FALLBACK_MASTER)


def menu() -> list[dict[str, Any]]:
    """给模型的母版菜单：id / label / tagline / 场合 / 色板变体摘要。"""
    return [master.spec.menu_entry() for master in MASTERS.values()]


def catalog() -> dict[str, Any]:
    """给前端与文档用的目录。顺序固定，便于生成物 diff。"""
    return {
        "version": 1,
        "masters": [master.spec.public() for master in MASTERS.values()],
        "genre_defaults": dict(GENRE_DEFAULT_MASTER),
    }


_TS_HEADER = """\
/**
 * 幻灯母版目录（**生成物，不要手改**）。
 *
 * 来源：`backend/app/ppt/masters/`；重新生成：
 *   cd backend && ./.venv/Scripts/python.exe -m app.ppt.masters
 *
 * 方案卡「长什么样」读这里：母版名、tagline、色板色块、装饰组。
 * 手改会在下一次生成时被覆盖。
 */

export interface MasterPaletteVariant {
  label: string
  bg: string
  fg: string
  muted: string
  accent: string
  line: string
}

export interface MasterDecorationQuery {
  motif?: string
  subject?: string
  keywords?: string[]
}

export interface MasterDecorationSet {
  id: string
  label: string
  ornament: MasterDecorationQuery
  ground: MasterDecorationQuery
}

export interface MasterSpec {
  id: string
  label: string
  tagline: string
  genres: string[]
  theme: string
  type_pair: string
  palettes: MasterPaletteVariant[]
  background: Record<string, string>
  decorations: MasterDecorationSet[]
  page_types: string[]
}

export const GENRE_DEFAULT_MASTER: Record<string, string> = %(genre_defaults)s

"""


def write_typescript(target: Path | None = None) -> Path:
    """把目录写成前端可 import 的 TS 文件。构建期调用，运行时不写盘。"""
    path = target or _default_ts_path()
    data = catalog()
    body = json.dumps(data["masters"], ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _TS_HEADER % {"genre_defaults": json.dumps(data["genre_defaults"], ensure_ascii=False)}
        + "export const PPT_MASTERS: MasterSpec[] = "
        + body
        + "\n\nexport const masterById = (id: string | undefined | null): MasterSpec | undefined =>\n"
        + "  PPT_MASTERS.find((item) => item.id === id)\n",
        encoding="utf-8",
    )
    return path


def _default_ts_path() -> Path:
    # backend/app/ppt/masters/__init__.py → 仓库根 → frontend/src/config/pptMasters.ts
    # 注意比 motifs.py 多一层：masters 是 ppt 下的子包。
    return Path(__file__).resolve().parents[4] / "frontend" / "src" / "config" / "pptMasters.ts"


if __name__ == "__main__":  # pragma: no cover - 构建脚本
    print(write_typescript())
