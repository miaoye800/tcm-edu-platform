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

export const GENRE_DEFAULT_MASTER: Record<string, string> = {"lecture": "xuan-songkai", "orientation": "xuan-songkai", "brief": "modern-sans", "training": "celadon-wenkai", "defense": "ink-gold", "pitch": "ink-gold"}

export const PPT_MASTERS: MasterSpec[] = [
  {
    "id": "xuan-songkai",
    "label": "宣纸宋楷",
    "tagline": "素纸、墨字、朱砂一点；古籍册页的静气",
    "genres": [
      "lecture",
      "orientation"
    ],
    "theme": "paper",
    "type_pair": "song-kai",
    "palettes": [
      {
        "label": "宣纸朱砂",
        "bg": "#FAF7F0",
        "fg": "#26221C",
        "muted": "#6B6459",
        "accent": "#A83B2E",
        "line": "#DDD6C8"
      },
      {
        "label": "素笺赭石",
        "bg": "#F5F1E8",
        "fg": "#2B2620",
        "muted": "#75705F",
        "accent": "#8C6A3F",
        "line": "#D9D2C0"
      }
    ],
    "background": {
      "kind": "motif",
      "motif": "paper-chain"
    },
    "decorations": [
      {
        "id": "real",
        "label": "真纹样角花",
        "ornament": {
          "subject": "卷草角花",
          "keywords": [
            "角花",
            "卷草"
          ]
        },
        "ground": {
          "motif": "paper-chain"
        }
      },
      {
        "id": "fret",
        "label": "回纹角花",
        "ornament": {
          "motif": "corner-fret"
        },
        "ground": {
          "motif": "paper-chain"
        }
      },
      {
        "id": "branch",
        "label": "折枝角花",
        "ornament": {
          "motif": "corner-branch"
        },
        "ground": {
          "motif": "paper-plain"
        }
      },
      {
        "id": "none",
        "label": "素面无饰",
        "ornament": {
          "motif": "none"
        },
        "ground": {
          "motif": "paper-plain"
        }
      }
    ],
    "page_types": [
      "cover",
      "agenda",
      "section",
      "points",
      "compare",
      "matrix",
      "process",
      "data",
      "warning",
      "summary"
    ]
  },
  {
    "id": "ink-gold",
    "label": "深墨金线",
    "tagline": "深墨底、米白字、一道金线；庄重的仪式感",
    "genres": [
      "defense",
      "pitch"
    ],
    "theme": "ink",
    "type_pair": "serif-full",
    "palettes": [
      {
        "label": "墨底金线",
        "bg": "#151719",
        "fg": "#F2EFE8",
        "muted": "#9A958A",
        "accent": "#C9A961",
        "line": "#3A3D42"
      },
      {
        "label": "玄青银线",
        "bg": "#10151C",
        "fg": "#EDEFF2",
        "muted": "#8B93A1",
        "accent": "#9DB4C8",
        "line": "#2E3641"
      }
    ],
    "background": {
      "kind": "solid"
    },
    "decorations": [
      {
        "id": "plain",
        "label": "素面金线",
        "ornament": {
          "motif": "none"
        },
        "ground": {
          "motif": ""
        }
      },
      {
        "id": "scroll",
        "label": "缠枝角花",
        "ornament": {
          "motif": "corner-scroll"
        },
        "ground": {
          "motif": "paper-mottle"
        }
      },
      {
        "id": "real",
        "label": "真纹样角花",
        "ornament": {
          "subject": "卷草角花",
          "keywords": [
            "角花",
            "卷草"
          ]
        },
        "ground": {
          "subject": "冰裂底纹",
          "keywords": [
            "冰裂",
            "底纹"
          ]
        }
      },
      {
        "id": "none",
        "label": "不装饰",
        "ornament": {
          "motif": "none"
        },
        "ground": {
          "motif": ""
        }
      }
    ],
    "page_types": [
      "cover",
      "agenda",
      "section",
      "points",
      "compare",
      "matrix",
      "process",
      "data",
      "warning",
      "summary"
    ]
  },
  {
    "id": "celadon-wenkai",
    "label": "青瓷文楷",
    "tagline": "青瓷浅绿、深绿字、竹叶一点；清楚又不端着",
    "genres": [
      "training",
      "orientation"
    ],
    "theme": "celadon",
    "type_pair": "kai-song",
    "palettes": [
      {
        "label": "青瓷竹色",
        "bg": "#F2F6F3",
        "fg": "#173B30",
        "muted": "#5E7A6E",
        "accent": "#2E7D5B",
        "line": "#C9D8CF"
      },
      {
        "label": "雨过天青",
        "bg": "#EEF3F6",
        "fg": "#1E3440",
        "muted": "#5F7480",
        "accent": "#3A7CA5",
        "line": "#CBD8DF"
      }
    ],
    "background": {
      "kind": "motif",
      "motif": "paper-plain"
    },
    "decorations": [
      {
        "id": "real",
        "label": "真纹样角花",
        "ornament": {
          "subject": "卷草角花",
          "keywords": [
            "角花",
            "卷草"
          ]
        },
        "ground": {
          "motif": "paper-plain"
        }
      },
      {
        "id": "bamboo",
        "label": "竹叶角花",
        "ornament": {
          "motif": "corner-bamboo"
        },
        "ground": {
          "motif": "paper-plain"
        }
      },
      {
        "id": "branch",
        "label": "折枝角花",
        "ornament": {
          "motif": "corner-branch"
        },
        "ground": {
          "motif": "paper-chain"
        }
      },
      {
        "id": "none",
        "label": "素面无饰",
        "ornament": {
          "motif": "none"
        },
        "ground": {
          "motif": "paper-plain"
        }
      }
    ],
    "page_types": [
      "cover",
      "agenda",
      "section",
      "points",
      "compare",
      "matrix",
      "process",
      "data",
      "warning",
      "summary"
    ]
  },
  {
    "id": "modern-sans",
    "label": "现代简约",
    "tagline": "浅灰白底、炭黑大字、一道蓝；零装饰的商务克制",
    "genres": [
      "brief",
      "training"
    ],
    "theme": "paper",
    "type_pair": "sans-full",
    "palettes": [
      {
        "label": "石墨蓝",
        "bg": "#F6F7F9",
        "fg": "#1F2530",
        "muted": "#5C6572",
        "accent": "#2F6BB4",
        "line": "#D8DDE4"
      },
      {
        "label": "暖灰橙",
        "bg": "#FAFAF8",
        "fg": "#23262B",
        "muted": "#6A6F76",
        "accent": "#B4552F",
        "line": "#E0DFDA"
      }
    ],
    "background": {
      "kind": "solid"
    },
    "decorations": [
      {
        "id": "plain",
        "label": "素面",
        "ornament": {
          "motif": "none"
        },
        "ground": {
          "motif": ""
        }
      },
      {
        "id": "paper",
        "label": "细纸纹",
        "ornament": {
          "motif": "none"
        },
        "ground": {
          "motif": "paper-plain"
        }
      },
      {
        "id": "real",
        "label": "几何纹样",
        "ornament": {
          "subject": "卷草角花",
          "keywords": [
            "角花",
            "卷草"
          ]
        },
        "ground": {
          "subject": "中式纹样",
          "keywords": [
            "纹样",
            "几何"
          ]
        }
      },
      {
        "id": "none",
        "label": "不装饰",
        "ornament": {
          "motif": "none"
        },
        "ground": {
          "motif": ""
        }
      }
    ],
    "page_types": [
      "cover",
      "agenda",
      "section",
      "points",
      "compare",
      "matrix",
      "process",
      "data",
      "warning",
      "summary"
    ]
  },
  {
    "id": "cinnabar-editorial",
    "label": "朱砂编辑部",
    "tagline": "暖朱砂题头带、大号宋体标题；杂志开扉页的编辑感",
    "genres": [
      "lecture",
      "pitch"
    ],
    "theme": "cinnabar",
    "type_pair": "serif-full",
    "palettes": [
      {
        "label": "朱砂深褐",
        "bg": "#FBF2ED",
        "fg": "#2A1815",
        "muted": "#7A6A60",
        "accent": "#A83B2E",
        "line": "#E3D2C6"
      },
      {
        "label": "墨赭暖纸",
        "bg": "#F7EFE8",
        "fg": "#33211B",
        "muted": "#7C6A5F",
        "accent": "#8C3B2E",
        "line": "#DFCDBE"
      }
    ],
    "background": {
      "kind": "motif",
      "motif": "paper-mottle"
    },
    "decorations": [
      {
        "id": "band",
        "label": "云纹横带",
        "ornament": {
          "subject": "回纹横带",
          "keywords": [
            "回纹",
            "横带"
          ]
        },
        "ground": {
          "motif": "paper-mottle"
        }
      },
      {
        "id": "scroll",
        "label": "卷草角花",
        "ornament": {
          "motif": "corner-scroll"
        },
        "ground": {
          "motif": "paper-mottle"
        }
      },
      {
        "id": "real",
        "label": "花边横带",
        "ornament": {
          "subject": "花边横带",
          "keywords": [
            "花边",
            "横带"
          ]
        },
        "ground": {
          "subject": "民间纹样",
          "keywords": [
            "纹样",
            "底纹"
          ]
        }
      },
      {
        "id": "none",
        "label": "素面无饰",
        "ornament": {
          "motif": "none"
        },
        "ground": {
          "motif": "paper-plain"
        }
      }
    ],
    "page_types": [
      "cover",
      "agenda",
      "section",
      "points",
      "compare",
      "matrix",
      "process",
      "data",
      "warning",
      "summary"
    ]
  }
]

export const masterById = (id: string | undefined | null): MasterSpec | undefined =>
  PPT_MASTERS.find((item) => item.id === id)
