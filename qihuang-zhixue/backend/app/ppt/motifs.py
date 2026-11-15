"""参数化母题目录。**这里只放数据，渲染在前端。**

为什么母题不做成手画 SVG：``frontend/public/story/herbs/*.svg`` 那四个 777 字节
占位卡已经证明过一次——手工资产会腐化，参数化函数不会。母题由代码现场生成，
好处是随主题换色、无限多样、零许可、零体积。

分工：

- 后端定义**目录**（有哪些母题、参数范围、适用主题），是唯一事实源；
- ``python -m app.ppt.motifs`` 把它写成 ``frontend/src/render/motifs.catalog.ts``；
- 前端 ``src/render/motifs/`` 每个母题一个渲染函数，入参是主题色板 + 参数对象。

目录落在后端而不是前端，是因为 ``select`` 步要按「主题 + 槽位类别」判断
哪些母题可用——判断发生在服务端，前端只负责画。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: 母题族。``ground`` 铺满整页当底纹，``ornament`` 是角饰，``rule`` 是界格/分隔，
#: ``seal`` 是印章，``scene`` 是远景（山、水）。
Family = str


@dataclass(frozen=True)
class MotifParam:
    """一个可调参数。范围写死，是为了让「无限多样」不至于变成「随机到难看」。"""

    name: str
    low: float
    high: float
    default: float
    unit: str = ""


@dataclass(frozen=True)
class Motif:
    id: str
    label: str
    family: Family
    slot: str
    themes: tuple[str, ...]
    params: tuple[MotifParam, ...] = field(default_factory=tuple)
    note: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "family": self.family,
            "slot": self.slot,
            "themes": list(self.themes),
            "params": [
                {"name": item.name, "low": item.low, "high": item.high, "default": item.default, "unit": item.unit}
                for item in self.params
            ],
            "note": self.note,
        }


_ALL_THEMES = ("paper", "ink", "celadon", "cinnabar")

#: 首批 12 个。数量不是目标——每个都要有「什么时候用它」的明确场景，
#: 否则就会退回「堆一堆装饰，看起来很忙」。
MOTIFS: tuple[Motif, ...] = (
    Motif(
        id="paper-plain",
        label="素纸",
        family="ground",
        slot="ground",
        themes=_ALL_THEMES,
        params=(MotifParam("grain", 0, 1, 0.18, "强度"),),
        note="默认底纹。只有极轻的颗粒，几乎看不见，但去掉就会像 PPT 纯色背景。",
    ),
    Motif(
        id="paper-chain",
        label="帘纹",
        family="ground",
        slot="ground",
        themes=("paper", "celadon", "cinnabar"),
        params=(MotifParam("gap", 6, 18, 11, "cqw"), MotifParam("opacity", 0.02, 0.12, 0.05)),
        note="手工纸的帘条纹。适合讲药材、炮制、古籍的稿子；技术汇报会显得旧。",
    ),
    Motif(
        id="paper-mottle",
        label="斑驳",
        family="ground",
        slot="ground",
        themes=("paper", "cinnabar", "ink"),
        params=(MotifParam("blots", 3, 9, 5, "处"), MotifParam("opacity", 0.02, 0.1, 0.04)),
        note="几处极淡的水渍。用在封面和尾页，正文页用会干扰阅读。",
    ),
    Motif(
        id="mountain-layers",
        label="层叠远山",
        family="scene",
        slot="ground",
        themes=_ALL_THEMES,
        params=(MotifParam("layers", 2, 4, 3, "层"), MotifParam("height", 0.18, 0.42, 0.28, "页高比")),
        note="封面/尾页的地平线。三到四层最像水墨，超过四层会糊成灰块。",
    ),
    Motif(
        id="mountain-single",
        label="孤峰",
        family="scene",
        slot="ground",
        themes=("paper", "ink"),
        params=(MotifParam("lean", -0.2, 0.2, 0.06), MotifParam("height", 0.2, 0.5, 0.34, "页高比")),
        note="一座偏锋的峰，留白多。适合只有一句话的章节页。",
    ),
    Motif(
        id="corner-vine",
        label="卷草",
        family="ornament",
        slot="ornament",
        themes=("paper", "cinnabar"),
        params=(
            MotifParam("leaves", 2, 5, 3, "叶"),
            MotifParam("scale", 0.75, 1.2, 1),
            MotifParam("frame", 0, 1, 1, "界栏"),
        ),
        note="角隅双线 + 内填卷草。frame=0 只留内花，给正文页那种放不下界栏的角落用。",
    ),
    Motif(
        id="corner-branch",
        label="折枝",
        family="ornament",
        slot="ornament",
        themes=_ALL_THEMES,
        params=(
            MotifParam("leaves", 3, 7, 4, "叶"),
            MotifParam("angle", -20, 30, 8, "度"),
            MotifParam("frame", 0, 1, 1, "界栏"),
        ),
        note="比卷草更直、更疏，枝梢聚三片叶收笔。信息密的页用它不吵。",
    ),
    Motif(
        id="corner-bamboo",
        label="竹叶",
        family="ornament",
        slot="ornament",
        themes=("paper", "celadon"),
        params=(MotifParam("leaves", 3, 9, 5, "叶"), MotifParam("frame", 0, 1, 1, "界栏")),
        note="一根细枝、几撇竹叶，叶长枝短才像竹子。讲方剂、性味时最贴合。",
    ),
    Motif(
        id="corner-fret",
        label="回纹",
        family="ornament",
        slot="ornament",
        themes=_ALL_THEMES,
        params=(MotifParam("rings", 2, 4, 3, "圈"), MotifParam("frame", 0, 1, 1, "界栏")),
        note="同心方框，明式书口的花边。不用曲线，靠间距的匀——最稳的一款。",
    ),
    Motif(
        id="corner-scroll",
        label="缠枝",
        family="ornament",
        slot="ornament",
        themes=("ink", "celadon"),
        params=(MotifParam("loops", 2, 4, 3, "环"), MotifParam("frame", 0, 1, 1, "界栏")),
        note="主茎盘绕一圈再收回中心，压得住深色底。浅色底上会显得脏。",
    ),
    Motif(
        id="grid-vertical",
        label="竖界格",
        family="rule",
        slot="ornament",
        themes=_ALL_THEMES,
        params=(MotifParam("columns", 3, 10, 6, "条"), MotifParam("inset", 0.02, 0.12, 0.06)),
        note="古籍的竖界格。封面上叠竖排标题最像书页；正文页别用，会和文字抢线。",
    ),
    Motif(
        id="grid-silk",
        label="乌丝栏",
        family="rule",
        slot="ornament",
        themes=("paper", "cinnabar"),
        params=(MotifParam("rows", 6, 14, 9, "行"), MotifParam("opacity", 0.05, 0.18, 0.1)),
        note="横向细栏线。适合「要点式」页面的低调分栏，密度大时不要用。",
    ),
    Motif(
        id="seal-square",
        label="朱印",
        family="seal",
        slot="ornament",
        themes=_ALL_THEMES,
        params=(MotifParam("chars", 2, 4, 2, "字"), MotifParam("rotation", -6, 6, -3, "度")),
        note="一方小印，收在尾页右下或封面标题右侧。全篇只用一次。",
    ),
    Motif(
        id="water-ripple",
        label="水纹",
        family="scene",
        slot="ground",
        themes=("paper", "celadon", "ink"),
        params=(MotifParam("lines", 2, 6, 4, "道"), MotifParam("amplitude", 0.004, 0.02, 0.01)),
        note="几道极淡的水波。讲方剂、煎服法时比远山贴题。",
    ),
)


def catalog() -> dict[str, Any]:
    """给前端与文档用的目录。顺序固定，便于生成物 diff。"""

    return {
        "version": 1,
        "families": ["ground", "scene", "ornament", "rule", "seal"],
        "motifs": [motif.public() for motif in MOTIFS],
    }


def motifs_for(slot: str, theme_id: str) -> list[Motif]:
    """某个槽位在某主题下可用的母题。``select`` 的第 1 级阶梯就查这里。"""

    return [motif for motif in MOTIFS if motif.slot == slot and theme_id in motif.themes]


def by_id(motif_id: str) -> Motif | None:
    return next((motif for motif in MOTIFS if motif.id == motif_id), None)


_TS_HEADER = """/**
 * 母题目录（**生成物，不要手改**）。
 *
 * 来源：`backend/app/ppt/motifs.py`；重新生成：
 *   cd backend && ./.venv/Scripts/python.exe -m app.ppt.motifs
 *
 * 手改会在下一次生成时被覆盖，也正是 `docs/studios/assets.md` §8 里
 * 「不用手画 SVG」那条规矩的技术保障。
 */

export interface MotifParamSpec {
  name: string
  low: number
  high: number
  default: number
  unit?: string
}

export interface MotifSpec {
  id: string
  label: string
  family: string
  slot: string
  themes: string[]
  params: MotifParamSpec[]
  note: string
}

export const MOTIF_FAMILIES = %(families)s as const
"""


def write_typescript(target: Path | None = None) -> Path:
    """把目录写成前端可 import 的 TS 文件。构建期调用，运行时不写盘。"""

    path = target or _default_ts_path()
    data = catalog()
    body = json.dumps(data["motifs"], ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _TS_HEADER % {"families": json.dumps(data["families"], ensure_ascii=False)}
        + "\n"
        + "export const MOTIFS: MotifSpec[] = "
        + body
        + "\n\nexport const motifById = (id: string): MotifSpec | undefined => MOTIFS.find(m => m.id === id)\n",
        encoding="utf-8",
    )
    return path


def _default_ts_path() -> Path:
    # backend/app/ppt/motifs.py → 仓库根 → frontend/src/render/motifs.catalog.ts
    return Path(__file__).resolve().parents[3] / "frontend" / "src" / "render" / "motifs.catalog.ts"


if __name__ == "__main__":  # pragma: no cover - 构建脚本
    print(write_typescript())
