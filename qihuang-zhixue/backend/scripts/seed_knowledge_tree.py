"""Seed the initial Chinese materia medica teaching tree into SQLite.

Run from backend: ``python scripts/seed_knowledge_tree.py``. Deterministic IDs and
upserts make repeated runs safe and allow later content additions.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.path.store import DB_PATH, upsert_edge, upsert_node


# The initial scope follows the standard 中药学 chapter order. Each subtype has
# three representative points; it is intentionally a seed, not a closed syllabus.
CHAPTERS: list[tuple[str, list[tuple[str, list[str]]]]] = [
    ("解表药", [("发散风寒药", ["麻黄", "桂枝", "紫苏"]), ("发散风热药", ["薄荷", "牛蒡子", "桑叶"])]),
    ("清热药", [("清热泻火药", ["石膏", "知母", "栀子"]), ("清热解毒药", ["金银花", "连翘", "蒲公英"])]),
    ("泻下药", [("攻下药", ["大黄", "芒硝", "番泻叶"]), ("润下药", ["火麻仁", "郁李仁", "松子仁"])]),
    ("祛风湿药", [("祛风湿散寒药", ["独活", "威灵仙", "川乌"]), ("祛风湿清热药", ["秦艽", "防己", "桑枝"])]),
    ("化湿药", [("芳香化湿药", ["藿香", "佩兰", "苍术"]), ("燥湿健脾药", ["厚朴", "砂仁", "草果"])]),
    ("利水渗湿药", [("利水消肿药", ["茯苓", "猪苓", "泽泻"]), ("利湿退黄药", ["茵陈", "金钱草", "虎杖"])]),
    ("温里药", [("温中散寒药", ["干姜", "肉桂", "吴茱萸"]), ("回阳救逆药", ["附子", "丁香", "小茴香"])]),
    ("理气药", [("行气药", ["陈皮", "枳实", "木香"]), ("疏肝理气药", ["香附", "柴胡", "川楝子"])]),
    ("消食药", [("消食化积药", ["山楂", "神曲", "麦芽"]), ("消食健胃药", ["莱菔子", "鸡内金", "谷芽"])]),
    ("驱虫药", [("杀虫消积药", ["使君子", "苦楝皮", "槟榔"]), ("驱虫止痒药", ["雷丸", "鹤草芽", "南瓜子"])]),
    ("止血药", [("凉血止血药", ["大蓟", "小蓟", "地榆"]), ("化瘀止血药", ["三七", "茜草", "蒲黄"])]),
    ("活血化瘀药", [("活血止痛药", ["川芎", "延胡索", "郁金"]), ("活血调经药", ["当归", "丹参", "桃仁"])]),
    ("化痰止咳平喘药", [("温化寒痰药", ["半夏", "天南星", "白芥子"]), ("清化热痰药", ["贝母", "瓜蒌", "竹茹"])]),
    ("安神药", [("重镇安神药", ["朱砂", "磁石", "龙骨"]), ("养心安神药", ["酸枣仁", "柏子仁", "远志"])]),
    ("平肝息风药", [("平抑肝阳药", ["石决明", "牡蛎", "代赭石"]), ("息风止痉药", ["羚羊角", "钩藤", "天麻"])]),
    ("开窍药", [("芳香开窍药", ["麝香", "冰片", "石菖蒲"]), ("开窍醒神药", ["苏合香", "安息香", "蟾酥"])]),
    ("补虚药", [
        ("补气药", ["人参", "黄芪", "白术"]),
        ("补血药", ["当归", "熟地黄", "白芍"]),
        ("补阴药", ["麦冬", "沙参", "枸杞子"]),
        ("补阳药", ["鹿茸", "巴戟天", "杜仲"]),
    ]),
    ("收涩药", [("固表止汗药", ["麻黄根", "浮小麦", "五味子"]), ("涩肠止泻药", ["诃子", "肉豆蔻", "赤石脂"])]),
]

# Explicit high/low values prevent all representative drugs from looking equally
# important to an exam-oriented planner. Unlisted representatives are "常考".
HIGH_FREQUENCY_POINTS = {
    "麻黄": 0.95, "桂枝": 0.9, "石膏": 0.9, "知母": 0.8, "金银花": 0.85,
    "连翘": 0.85, "大黄": 0.95, "茯苓": 0.9, "泽泻": 0.8, "附子": 0.9,
    "干姜": 0.8, "陈皮": 0.85, "枳实": 0.8, "当归": 0.95, "黄芪": 0.9,
    "人参": 0.9, "白术": 0.85, "半夏": 0.85, "川芎": 0.85, "丹参": 0.8,
    "三七": 0.85, "天麻": 0.8, "五味子": 0.8,
}
LOW_FREQUENCY_POINTS = {
    "松子仁": 0.2, "鹤草芽": 0.2, "雷丸": 0.2, "南瓜子": 0.2,
    "蟾酥": 0.15, "安息香": 0.2, "朱砂": 0.25, "川乌": 0.25,
}


def point_exam_frequency(name: str) -> float:
    """Return high (0.8-1), common (0.5-0.7), or low (0.1-0.3) exam weight."""

    return HIGH_FREQUENCY_POINTS.get(name, LOW_FREQUENCY_POINTS.get(name, 0.6))


def seed(db_path: Path = DB_PATH) -> tuple[int, int]:
    """Upsert the initial tree and its acyclic prerequisite relations."""

    nodes, edges = 0, 0
    general_id = "general"
    upsert_node({"id": general_id, "name": "中药学总论", "level": "domain", "description": "性味归经、升降浮沉、配伍与用药禁忌", "exam_frequency": 1.0, "sort_hint": 0}, db_path)
    nodes += 1
    general_subtype_id = f"{general_id}.s1"
    upsert_node({"id": general_subtype_id, "name": "基本理论", "level": "subtype", "parent_id": general_id, "exam_frequency": 1.0, "sort_hint": 1}, db_path)
    upsert_edge({"from_node_id": general_id, "to_node_id": general_subtype_id, "rel_type": "hierarchy", "order_sensitivity": "strict", "reason": "总论层级归属"}, db_path)
    nodes += 1
    edges += 1
    general_points = ["性味归经", "升降浮沉", "配伍禁忌"]
    for index, name in enumerate(general_points, 1):
        point_id = f"{general_id}.p{index}"
        upsert_node({"id": point_id, "name": name, "level": "point", "parent_id": general_subtype_id, "exam_frequency": point_exam_frequency(name), "sort_hint": index}, db_path)
        upsert_edge({"from_node_id": general_subtype_id, "to_node_id": point_id, "rel_type": "hierarchy", "order_sensitivity": "strict", "reason": "总论层级归属"}, db_path)
        nodes += 1
        edges += 1
        if index > 1:
            upsert_edge({"from_node_id": f"{general_id}.p{index - 1}", "to_node_id": point_id, "order_sensitivity": "strict", "reason": "总论按教材顺序递进"}, db_path)
            edges += 1

    previous_domain: str | None = None
    for chapter_index, (chapter, subtypes) in enumerate(CHAPTERS, 1):
        domain_id = f"d{chapter_index:02d}"
        upsert_node({"id": domain_id, "name": chapter, "level": "domain", "description": f"《中药学》{chapter}章节", "exam_frequency": 0.8, "sort_hint": chapter_index}, db_path)
        upsert_edge({"from_node_id": general_id, "to_node_id": domain_id, "order_sensitivity": "strict", "reason": "各论学习以前需掌握中药学总论"}, db_path)
        nodes += 1
        edges += 1
        if previous_domain:
            upsert_edge({"from_node_id": previous_domain, "to_node_id": domain_id, "order_sensitivity": "free", "reason": "章节间可按复习安排并行，不设硬性先后"}, db_path)
            edges += 1
        previous_domain = domain_id
        previous_subtype: str | None = None
        for subtype_index, (subtype, points) in enumerate(subtypes, 1):
            subtype_id = f"{domain_id}.s{subtype_index}"
            upsert_node({"id": subtype_id, "name": subtype, "level": "subtype", "parent_id": domain_id, "exam_frequency": 0.75, "sort_hint": subtype_index}, db_path)
            upsert_edge({"from_node_id": domain_id, "to_node_id": subtype_id, "rel_type": "hierarchy", "order_sensitivity": "strict", "reason": "章节层级归属"}, db_path)
            nodes += 1
            edges += 1
            if previous_subtype:
                upsert_edge({"from_node_id": previous_subtype, "to_node_id": subtype_id, "order_sensitivity": "strict", "reason": "子类按教材编排顺序学习"}, db_path)
                edges += 1
            previous_subtype = subtype_id
            previous_point: str | None = None
            for point_index, point in enumerate(points, 1):
                point_id = f"{subtype_id}.p{point_index}"
                upsert_node({"id": point_id, "name": point, "level": "point", "parent_id": subtype_id, "exam_frequency": point_exam_frequency(point), "sort_hint": point_index}, db_path)
                upsert_edge({"from_node_id": subtype_id, "to_node_id": point_id, "rel_type": "hierarchy", "order_sensitivity": "strict", "reason": "子类层级归属"}, db_path)
                nodes += 1
                edges += 1
                if previous_point:
                    upsert_edge({"from_node_id": previous_point, "to_node_id": point_id, "order_sensitivity": "strict", "reason": "代表药物按教材顺序学习"}, db_path)
                    edges += 1
                previous_point = point_id
    return nodes, edges


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the PathAgent knowledge tree")
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    args = parser.parse_args()
    count = seed(args.db_path)
    print(f"seeded {count[0]} nodes and {count[1]} edges into {args.db_path}")
