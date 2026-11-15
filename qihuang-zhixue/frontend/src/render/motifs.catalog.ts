/**
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

export const MOTIF_FAMILIES = ["ground", "scene", "ornament", "rule", "seal"] as const

export const MOTIFS: MotifSpec[] = [
  {
    "id": "paper-plain",
    "label": "素纸",
    "family": "ground",
    "slot": "ground",
    "themes": [
      "paper",
      "ink",
      "celadon",
      "cinnabar"
    ],
    "params": [
      {
        "name": "grain",
        "low": 0,
        "high": 1,
        "default": 0.18,
        "unit": "强度"
      }
    ],
    "note": "默认底纹。只有极轻的颗粒，几乎看不见，但去掉就会像 PPT 纯色背景。"
  },
  {
    "id": "paper-chain",
    "label": "帘纹",
    "family": "ground",
    "slot": "ground",
    "themes": [
      "paper",
      "celadon",
      "cinnabar"
    ],
    "params": [
      {
        "name": "gap",
        "low": 6,
        "high": 18,
        "default": 11,
        "unit": "cqw"
      },
      {
        "name": "opacity",
        "low": 0.02,
        "high": 0.12,
        "default": 0.05,
        "unit": ""
      }
    ],
    "note": "手工纸的帘条纹。适合讲药材、炮制、古籍的稿子；技术汇报会显得旧。"
  },
  {
    "id": "paper-mottle",
    "label": "斑驳",
    "family": "ground",
    "slot": "ground",
    "themes": [
      "paper",
      "cinnabar",
      "ink"
    ],
    "params": [
      {
        "name": "blots",
        "low": 3,
        "high": 9,
        "default": 5,
        "unit": "处"
      },
      {
        "name": "opacity",
        "low": 0.02,
        "high": 0.1,
        "default": 0.04,
        "unit": ""
      }
    ],
    "note": "几处极淡的水渍。用在封面和尾页，正文页用会干扰阅读。"
  },
  {
    "id": "mountain-layers",
    "label": "层叠远山",
    "family": "scene",
    "slot": "ground",
    "themes": [
      "paper",
      "ink",
      "celadon",
      "cinnabar"
    ],
    "params": [
      {
        "name": "layers",
        "low": 2,
        "high": 4,
        "default": 3,
        "unit": "层"
      },
      {
        "name": "height",
        "low": 0.18,
        "high": 0.42,
        "default": 0.28,
        "unit": "页高比"
      }
    ],
    "note": "封面/尾页的地平线。三到四层最像水墨，超过四层会糊成灰块。"
  },
  {
    "id": "mountain-single",
    "label": "孤峰",
    "family": "scene",
    "slot": "ground",
    "themes": [
      "paper",
      "ink"
    ],
    "params": [
      {
        "name": "lean",
        "low": -0.2,
        "high": 0.2,
        "default": 0.06,
        "unit": ""
      },
      {
        "name": "height",
        "low": 0.2,
        "high": 0.5,
        "default": 0.34,
        "unit": "页高比"
      }
    ],
    "note": "一座偏锋的峰，留白多。适合只有一句话的章节页。"
  },
  {
    "id": "corner-vine",
    "label": "卷草",
    "family": "ornament",
    "slot": "ornament",
    "themes": [
      "paper",
      "cinnabar"
    ],
    "params": [
      {
        "name": "leaves",
        "low": 2,
        "high": 5,
        "default": 3,
        "unit": "叶"
      },
      {
        "name": "scale",
        "low": 0.75,
        "high": 1.2,
        "default": 1,
        "unit": ""
      },
      {
        "name": "frame",
        "low": 0,
        "high": 1,
        "default": 1,
        "unit": "界栏"
      }
    ],
    "note": "角隅双线 + 内填卷草。frame=0 只留内花，给正文页那种放不下界栏的角落用。"
  },
  {
    "id": "corner-branch",
    "label": "折枝",
    "family": "ornament",
    "slot": "ornament",
    "themes": [
      "paper",
      "ink",
      "celadon",
      "cinnabar"
    ],
    "params": [
      {
        "name": "leaves",
        "low": 3,
        "high": 7,
        "default": 4,
        "unit": "叶"
      },
      {
        "name": "angle",
        "low": -20,
        "high": 30,
        "default": 8,
        "unit": "度"
      },
      {
        "name": "frame",
        "low": 0,
        "high": 1,
        "default": 1,
        "unit": "界栏"
      }
    ],
    "note": "比卷草更直、更疏，枝梢聚三片叶收笔。信息密的页用它不吵。"
  },
  {
    "id": "corner-bamboo",
    "label": "竹叶",
    "family": "ornament",
    "slot": "ornament",
    "themes": [
      "paper",
      "celadon"
    ],
    "params": [
      {
        "name": "leaves",
        "low": 3,
        "high": 9,
        "default": 5,
        "unit": "叶"
      },
      {
        "name": "frame",
        "low": 0,
        "high": 1,
        "default": 1,
        "unit": "界栏"
      }
    ],
    "note": "一根细枝、几撇竹叶，叶长枝短才像竹子。讲方剂、性味时最贴合。"
  },
  {
    "id": "corner-fret",
    "label": "回纹",
    "family": "ornament",
    "slot": "ornament",
    "themes": [
      "paper",
      "ink",
      "celadon",
      "cinnabar"
    ],
    "params": [
      {
        "name": "rings",
        "low": 2,
        "high": 4,
        "default": 3,
        "unit": "圈"
      },
      {
        "name": "frame",
        "low": 0,
        "high": 1,
        "default": 1,
        "unit": "界栏"
      }
    ],
    "note": "同心方框，明式书口的花边。不用曲线，靠间距的匀——最稳的一款。"
  },
  {
    "id": "corner-scroll",
    "label": "缠枝",
    "family": "ornament",
    "slot": "ornament",
    "themes": [
      "ink",
      "celadon"
    ],
    "params": [
      {
        "name": "loops",
        "low": 2,
        "high": 4,
        "default": 3,
        "unit": "环"
      },
      {
        "name": "frame",
        "low": 0,
        "high": 1,
        "default": 1,
        "unit": "界栏"
      }
    ],
    "note": "主茎盘绕一圈再收回中心，压得住深色底。浅色底上会显得脏。"
  },
  {
    "id": "grid-vertical",
    "label": "竖界格",
    "family": "rule",
    "slot": "ornament",
    "themes": [
      "paper",
      "ink",
      "celadon",
      "cinnabar"
    ],
    "params": [
      {
        "name": "columns",
        "low": 3,
        "high": 10,
        "default": 6,
        "unit": "条"
      },
      {
        "name": "inset",
        "low": 0.02,
        "high": 0.12,
        "default": 0.06,
        "unit": ""
      }
    ],
    "note": "古籍的竖界格。封面上叠竖排标题最像书页；正文页别用，会和文字抢线。"
  },
  {
    "id": "grid-silk",
    "label": "乌丝栏",
    "family": "rule",
    "slot": "ornament",
    "themes": [
      "paper",
      "cinnabar"
    ],
    "params": [
      {
        "name": "rows",
        "low": 6,
        "high": 14,
        "default": 9,
        "unit": "行"
      },
      {
        "name": "opacity",
        "low": 0.05,
        "high": 0.18,
        "default": 0.1,
        "unit": ""
      }
    ],
    "note": "横向细栏线。适合「要点式」页面的低调分栏，密度大时不要用。"
  },
  {
    "id": "seal-square",
    "label": "朱印",
    "family": "seal",
    "slot": "ornament",
    "themes": [
      "paper",
      "ink",
      "celadon",
      "cinnabar"
    ],
    "params": [
      {
        "name": "chars",
        "low": 2,
        "high": 4,
        "default": 2,
        "unit": "字"
      },
      {
        "name": "rotation",
        "low": -6,
        "high": 6,
        "default": -3,
        "unit": "度"
      }
    ],
    "note": "一方小印，收在尾页右下或封面标题右侧。全篇只用一次。"
  },
  {
    "id": "water-ripple",
    "label": "水纹",
    "family": "scene",
    "slot": "ground",
    "themes": [
      "paper",
      "celadon",
      "ink"
    ],
    "params": [
      {
        "name": "lines",
        "low": 2,
        "high": 6,
        "default": 4,
        "unit": "道"
      },
      {
        "name": "amplitude",
        "low": 0.004,
        "high": 0.02,
        "default": 0.01,
        "unit": ""
      }
    ],
    "note": "几道极淡的水波。讲方剂、煎服法时比远山贴题。"
  }
]

export const motifById = (id: string): MotifSpec | undefined => MOTIFS.find(m => m.id === id)
