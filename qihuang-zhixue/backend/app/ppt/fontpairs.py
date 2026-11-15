"""字对（标题 + 正文字族的组合）的单一事实源。

前端 ``frontend/src/config/pptFonts.ts`` 的 ``pptFontPairs`` id 必须与本表的
``TYPE_PAIRS`` 完全一致——契约测试（``ppt-studio.spec.mjs``）逐个 id 对表。
两边各管一半：这里管「模型菜单与校验」，前端管「字体栈」（哪个 id 落到哪几个
@font-face 族）。

## 为什么只有五对

2026-11 重构前的菜单是四个 id（serif-song / serif-kai / sans-hei / song-kai），
而前端有八对、仅 ``song-kai`` 一个交集——模型四选三的选择实际全部静默回落
默认宋楷，「选了字体」这件事在屏幕上从未发生。收敛到五对并让两边同源后，
模型选什么、屏幕上就是什么。

## 旧 id 的兼容

老方案回放、老设计单里存着旧 id。``coerce_type_pair`` 把旧 id 映射到新表
（serif-song → serif-full、serif-kai → kai-song、sans-hei → sans-full），
映射不了的一律回落默认对，**不许 raise**（宽松读规矩）。
"""

from __future__ import annotations

from typing import Final

TYPE_PAIRS: Final[tuple[str, ...]] = ("song-kai", "kai-song", "hei-song", "serif-full", "sans-full")

DEFAULT_TYPE_PAIR: Final[str] = "song-kai"

#: 给模型的菜单文案：一对什么时候用，一句话。
TYPE_PAIR_TEXT: Final[dict[str, str]] = {
    "song-kai": "宋楷：标题楷体、正文宋体。讲义默认，最稳。",
    "kai-song": "楷宋：通篇楷体，手写感强，适合引经据典、金句页。",
    "hei-song": "黑宋：标题黑体、正文宋体，现代对比，适合汇报与数据页。",
    "serif-full": "全宋：标题正文都用宋体，字面均匀，投影与打印都清楚。",
    "sans-full": "全黑：标题正文都用黑体，屏幕阅读最清楚，适合阶梯教室与录屏。",
}

#: 历史 id → 新表。别在这里「顺手删掉」：回放旧方案靠它活着。
_LEGACY_ALIASES: Final[dict[str, str]] = {
    "serif-song": "serif-full",
    "serif-kai": "kai-song",
    "sans-hei": "sans-full",
}


def coerce_type_pair(value: object) -> str:
    """宽松读：旧 id 映射新表，未知回落默认。"""
    text = str(value or "").strip()
    text = _LEGACY_ALIASES.get(text, text)
    return text if text in TYPE_PAIRS else DEFAULT_TYPE_PAIR


#: 给模型的菜单（``_payload["type_pair_menu"]``）：id + 一句话什么时候用。
TYPE_PAIR_MENU: Final[list[dict[str, str]]] = [
    {"type_pair": pair, "label": TYPE_PAIR_TEXT[pair]} for pair in TYPE_PAIRS
]
