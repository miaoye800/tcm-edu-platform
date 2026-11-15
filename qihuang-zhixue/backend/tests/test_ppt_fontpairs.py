"""字对单一事实源（``app/ppt/fontpairs.py``）的契约。

前端的 ``pptFontPairs`` id 由 ``ppt-studio.spec.mjs`` 从这个文件里读出来对表，
所以这里盯住的其实是「后端菜单本身没有走样」。
"""

from __future__ import annotations

from app.ppt.fontpairs import (
    DEFAULT_TYPE_PAIR,
    TYPE_PAIRS,
    TYPE_PAIR_MENU,
    TYPE_PAIR_TEXT,
    coerce_type_pair,
)


def test_legacy_ids_map_forward() -> None:
    """旧方案回放里的 serif-song / serif-kai / sans-hei 必须映射到新表，不许 raise。"""
    assert coerce_type_pair("serif-song") == "serif-full"
    assert coerce_type_pair("serif-kai") == "kai-song"
    assert coerce_type_pair("sans-hei") == "sans-full"


def test_unknown_and_blank_fall_back_to_default() -> None:
    assert coerce_type_pair("中国风") == DEFAULT_TYPE_PAIR
    assert coerce_type_pair("") == DEFAULT_TYPE_PAIR
    assert coerce_type_pair(None) == DEFAULT_TYPE_PAIR


def test_menu_ids_match_the_table() -> None:
    assert tuple(item["type_pair"] for item in TYPE_PAIR_MENU) == TYPE_PAIRS
    assert all(item["label"] == TYPE_PAIR_TEXT[item["type_pair"]] for item in TYPE_PAIR_MENU)
    # 默认对必须在表里，否则 coerce 的回落值本身就不合法。
    assert DEFAULT_TYPE_PAIR in TYPE_PAIRS
