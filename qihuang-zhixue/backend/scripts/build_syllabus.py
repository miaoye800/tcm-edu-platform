"""生成教材目录事实源 ``backend/data/syllabus/<subject>.json``。

为什么要有这一步
----------------
地图上的「章 / 节 / 穴位」必须能对回教材某一页。之前是从 ``knowledge_node``
反推结构，而知识树只有 22 个 domain、把《中药学》总论压成 1 章 1 节
（「基本理论」下面挂 3 个点），于是总论只剩「性味归经 / 升降浮沉 / 配伍禁忌」
三行，教材里真正的前 7 章（起源与发展、产地采集贮藏、炮制、性能、配伍、
用药禁忌、剂量与用法）全丢了；各论那 9 个本来不分节的章，还被硬造了一个
同名假节来凑层级。

这个脚本把目录从**教材本身**取出来。学科写在下方 ``SUBJECTS`` 注册表里，
接新学科只加一项，**不要另写一套造图逻辑**——目录的形态对所有学科都一样：

* 各论 —— 来自该学科的教材条目 JSON，每条自带 ``chapter_no`` / ``chapter`` /
  ``section`` / ``page``，字段是抽取时从书上带下来的，不是猜的。
  （中药学：``data/curated/textbook_entries.json``，442 条、21 章、38 节。）
* 总论 —— 条目文件不含总论，按教材印刷目录逐条录入（``GENERAL_CHAPTERS``），
  并保留目录来源供核对。

产出是**唯一事实源**：``authoring.py`` 只读它，不再读知识树、也不再让模型
编总论小节。换教材版本 = 改这一个文件，其余不动。

用法::

    python scripts/build_syllabus.py                 # 生成 herb
    python scripts/build_syllabus.py --check         # 只校验，不写盘
    python scripts/build_syllabus.py --subject herb  # 显式指定学科
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "syllabus"

# 脚本以 ``python scripts/build_syllabus.py`` 运行时，sys.path[0] 是 scripts/ 而不是
# backend/，于是 ``import app.meridian`` 会失败。显式补一下，不让调用方式影响结果。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --------------------------------------------------------------------------- 总论

#: 教材印刷目录，逐章录入。``sections`` 为空表示该章不分节——**不要**为了
#: 凑层级造一个同名假节，那是上一版把「化湿药」变成「第一节 化湿药」的根源。
GENERAL_CHAPTERS: list[dict[str, Any]] = [
    {
        "no": 1,
        "title": "中药的起源和中药学的发展",
        "sections": [],
    },
    {
        "no": 2,
        "title": "中药的产地、采集与贮藏",
        "sections": ["中药的产地", "中药的采集", "中药的贮藏"],
    },
    {
        "no": 3,
        "title": "中药的炮制",
        "sections": ["中药炮制的目的", "中药炮制的方法"],
    },
    {
        "no": 4,
        "title": "中药的性能",
        "sections": ["四气", "五味", "升降浮沉", "归经", "中药的毒性"],
    },
    {
        "no": 5,
        "title": "中药的配伍",
        "sections": [],
    },
    {
        "no": 6,
        "title": "中药的用药禁忌",
        "sections": ["配伍禁忌", "证候用药禁忌", "妊娠用药禁忌", "服药饮食禁忌"],
    },
    {
        "no": 7,
        "title": "中药的剂量与用法",
        "sections": ["中药的剂量", "中药的用法"],
    },
]

#: 总论这几个节与既有知识树里的知识点同名，可直接对上（其余节暂无节点）。
GENERAL_NODE_ALIASES: dict[str, str] = {
    "四气": "四气",
    "五味": "五味",
    "升降浮沉": "升降浮沉",
    "归经": "性味归经",
    "配伍禁忌": "配伍禁忌",
}

#: 方剂学整本目录（上篇总论 6 章 + 下篇各论 21 章）。
#: 这本书**各论章号从第一章重新开始**，所以内部统一编号成 1–27 连续，
#: 另用 ``book_no`` 记住书上印的「各论第几章」——地图上要能同时说清
#: 「第 7 章」和「各论第一章 解表剂」，不能只留一个。
#: 节名逐条对自《方剂学》(李冀/左铮云, 中国中医药出版社, 2021, ISBN 9787513268714)
#: 及配套《方剂学记忆手册》目录。总论第三—六章书内只有"一、二、三"无节号，
#: 一律按**不分节**处理，不造"第一节 病证分类"这种教材里没有的节号。
FORMULA_TOC: list[dict[str, Any]] = [
    # ---- 上篇 总论 ----
    {"part": "总论", "book_no": 1, "title": "方剂的起源与发展", "sections": []},
    {
        "part": "总论",
        "book_no": 2,
        "title": "方剂与治法",
        "sections": ["方剂与治法的关系", "常用治法"],
    },
    {"part": "总论", "book_no": 3, "title": "方剂的分类", "sections": []},
    {"part": "总论", "book_no": 4, "title": "方剂的剂型", "sections": []},
    {"part": "总论", "book_no": 5, "title": "方剂的煎服法", "sections": []},
    {"part": "总论", "book_no": 6, "title": "方剂的组方原则与变化", "sections": []},
    # ---- 下篇 各论 ----
    {
        "part": "各论",
        "book_no": 1,
        "title": "解表剂",
        "sections": ["辛温解表剂", "辛凉解表剂", "扶正解表剂"],
    },
    {
        "part": "各论",
        "book_no": 2,
        "title": "泻下剂",
        "sections": ["寒下剂", "温下剂", "润下剂", "逐水剂", "攻补兼施剂"],
    },
    {
        "part": "各论",
        "book_no": 3,
        "title": "和解剂",
        "sections": ["和解少阳剂", "调和肝脾剂", "调和寒热剂"],
    },
    {
        "part": "各论",
        "book_no": 4,
        "title": "清热剂",
        "sections": [
            "清气分热剂",
            "清营凉血剂",
            "清热解毒剂",
            "气血两清剂",
            "清脏腑热剂",
            "清虚热剂",
        ],
    },
    {
        "part": "各论",
        "book_no": 5,
        "title": "祛暑剂",
        # 首版这里只有 3 节，漏了「祛暑清热剂」（清络饮）。补自各章方名与节名同时
        # 出现的《方剂歌诀》教学文档（见 parse_formula_gejue.py），不是凭印象加的。
        "sections": ["祛暑解表剂", "祛暑清热剂", "祛暑利湿剂", "祛暑益气剂"],
    },
    {
        "part": "各论",
        "book_no": 6,
        "title": "温里剂",
        "sections": ["温中祛寒剂", "回阳救逆剂", "温经散寒剂"],
    },
    {
        "part": "各论",
        "book_no": 7,
        "title": "表里双解剂",
        "sections": ["解表清里剂", "解表温里剂", "解表攻里剂"],
    },
    {
        "part": "各论",
        "book_no": 8,
        "title": "补益剂",
        "sections": ["补气剂", "补血剂", "气血双补剂", "补阴剂", "补阳剂", "阴阳并补剂"],
    },
    {
        "part": "各论",
        "book_no": 9,
        "title": "固涩剂",
        "sections": ["固表止汗剂", "敛肺止咳剂", "涩肠固脱剂", "涩精止遗剂", "固崩止带剂"],
    },
    {
        "part": "各论",
        "book_no": 10,
        "title": "安神剂",
        "sections": ["重镇安神剂", "补养安神剂", "交通心肾剂"],
    },
    {"part": "各论", "book_no": 11, "title": "开窍剂", "sections": ["凉开剂", "温开剂"]},
    {"part": "各论", "book_no": 12, "title": "理气剂", "sections": ["行气剂", "降气剂"]},
    {"part": "各论", "book_no": 13, "title": "理血剂", "sections": ["活血祛瘀剂", "止血剂"]},
    {"part": "各论", "book_no": 14, "title": "治风剂", "sections": ["疏散外风剂", "平息内风剂"]},
    {"part": "各论", "book_no": 15, "title": "治燥剂", "sections": ["轻宣外燥剂", "滋润内燥剂"]},
    {
        "part": "各论",
        "book_no": 16,
        "title": "祛湿剂",
        "sections": [
            "化湿和胃剂",
            "清热祛湿剂",
            "利水渗湿剂",
            "温化寒湿剂",
            "祛湿化浊剂",
            "祛风胜湿剂",
        ],
    },
    {
        "part": "各论",
        "book_no": 17,
        "title": "祛痰剂",
        "sections": ["燥湿化痰剂", "清热化痰剂", "润燥化痰剂", "温化寒痰剂", "治风化痰剂"],
    },
    {"part": "各论", "book_no": 18, "title": "消食剂", "sections": ["消食化滞剂", "健脾消食剂"]},
    {"part": "各论", "book_no": 19, "title": "驱虫剂", "sections": []},
    {"part": "各论", "book_no": 20, "title": "涌吐剂", "sections": []},
    {
        "part": "各论",
        "book_no": 21,
        "title": "治痈疡剂",
        "sections": ["散结消痈剂", "托里透脓剂", "补虚敛疮剂"],
    },
]

#: 《中医基础理论》（新世纪第5版，郑洪新、杨柱，中国中医药出版社 2021，ISBN 9787513269056）。
#: 目录逐节抄自该版教材版权页，节下条目在 ``basic_entries.json`` 里。
#: 这本书没有「总论 / 各论」之分：绪论按**总纲**处理（part=总论），
#: 第一—九章是正文（part=各论）。分部分只影响地图上的标签，不影响编号。
BASIC_TOC: list[dict[str, Any]] = [
    {"part": "绪论", "book_no": 0, "title": "绪论", "sections": []},
    {
        "part": "各论",
        "book_no": 1,
        "title": "中医学的哲学基础",
        "sections": ["气一元论", "阴阳学说", "五行学说"],
    },
    {
        "part": "各论",
        "book_no": 2,
        "title": "中医学的主要思维方式",
        "sections": ["象思维", "系统思维", "变易思维"],
    },
    {
        "part": "各论",
        "book_no": 3,
        "title": "藏象",
        "sections": ["概述", "五脏", "六腑", "奇恒之腑", "脏腑之间的关系"],
    },
    {
        "part": "各论",
        "book_no": 4,
        "title": "精气血津液神",
        "sections": ["精", "气", "血", "津液", "神", "精气血津液神之间的关系"],
    },
    {
        "part": "各论",
        "book_no": 5,
        "title": "经络",
        "sections": ["概述", "十二经脉", "奇经八脉", "经别、经筋、皮部、别络", "经络的生理功能和应用"],
    },
    {
        "part": "各论",
        "book_no": 6,
        "title": "体质",
        "sections": ["体质的概念与构成要素", "体质的生理学基础与形成因素", "体质的分类", "体质学说的应用"],
    },
    {
        "part": "各论",
        "book_no": 7,
        "title": "病因",
        "sections": ["外感病因", "内伤病因", "病理产物性病因", "其他病因"],
    },
    {
        "part": "各论",
        "book_no": 8,
        "title": "病机",
        "sections": ["发病", "基本病机", "内生五邪", "疾病传变"],
    },
    {
        "part": "各论",
        "book_no": 9,
        "title": "养生与防治原则",
        "sections": ["养生", "治未病", "治则"],
    },
]

#: 《中医诊断学》（新世纪第5版，李灿东、方朝义，中国中医药出版社 2021-06，ISBN 9787513268493）。
#: 绪论 + 九章、27 节。本版章目与第一章前两节的节名核对自出版社书目页；
#: 其余节名逐条对自同社上一版（2016，ISBN 9787513234009）目录，两版节次一致。
#: 绪论里"一、二、三"是行文标题而不是节号，按**不分节**处理，那五条直接作知识点。
DIAGNOSIS_TOC: list[dict[str, Any]] = [
    {
        "part": "绪论",
        "book_no": 0,
        "title": "绪论",
        "sections": [],
        "entries": [
            "中医诊断学的发展简史",
            "中医诊断学的主要内容",
            "中医诊断的基本原理",
            "中医诊断的基本原则",
            "学习中医诊断学的方法",
        ],
    },
    {
        "part": "各论",
        "book_no": 1,
        "title": "望诊",
        "sections": ["全身望诊", "局部望诊", "舌诊", "望小儿食指络脉", "望排出物"],
    },
    {
        "part": "各论",
        "book_no": 2,
        "title": "闻诊",
        "sections": ["听声音", "嗅气味"],
    },
    {
        "part": "各论",
        "book_no": 3,
        "title": "问诊",
        "sections": ["问诊的意义及方法", "问诊的内容", "问现在症"],
    },
    {
        "part": "各论",
        "book_no": 4,
        "title": "切诊",
        "sections": ["脉诊", "按诊"],
    },
    {
        "part": "各论",
        "book_no": 5,
        "title": "八纲辨证",
        "sections": ["八纲基本证", "八纲证之间的关系"],
    },
    {
        "part": "各论",
        "book_no": 6,
        "title": "病性辨证",
        "sections": ["六淫辨证", "阴阳虚损辨证", "气血辨证", "津液辨证"],
    },
    {
        "part": "各论",
        "book_no": 7,
        "title": "病位辨证",
        "sections": ["脏腑辨证", "六经辨证", "卫气营血辨证", "三焦辨证", "经络辨证"],
    },
    {
        "part": "各论",
        "book_no": 8,
        "title": "中医诊断思维与应用",
        "sections": ["中医诊断思维方法", "中医诊断思维的应用"],
    },
    {
        "part": "各论",
        "book_no": 9,
        "title": "中医医案与病历书写",
        "sections": ["中医医案", "中医病历书写"],
    },
]

#: 《针灸学》（新世纪第5版，梁繁荣、王华，中国中医药出版社 2021-07，ISBN 9787513268127）。
#: 绪论 + 上篇（经络腧穴 3 章）+ 中篇（刺灸法 2 章）+ 下篇（治疗 2 章）+ 附篇（4 章）。
#: 上篇三章的 23 个节名逐条对自出版社书目页；篇/章结构另有 2024 年安徽中医药大学
#: 专升本《针灸学》考试大纲（指定本版）作旁证。
#: **中篇、下篇、附篇各章教材只印章名、未印节号**，一律按不分节处理——不为了
#: 层级好看去编"第一节 毫针刺法"这种书上没有的节号。补节必须对照纸质目录。
ACUP_TOC: list[dict[str, Any]] = [
    {
        "part": "绪论",
        "book_no": 0,
        "title": "绪论",
        "sections": [],
        "entries": ["针灸学发展简史", "针灸学的对外传播", "针灸学的基本内容和学习方法"],
    },
    {
        "part": "上篇",
        "book_no": 1,
        "title": "经络总论",
        "sections": [
            "经络的发现",
            "经络系统的组成",
            "经络的标本、根结和气街、四海",
            "经络的作用和经络学说的临床应用",
        ],
    },
    {
        "part": "上篇",
        "book_no": 2,
        "title": "腧穴总论",
        "sections": ["腧穴的分类和命名", "腧穴的主治特点和规律", "特定穴", "腧穴的定位方法"],
    },
    {
        "part": "上篇",
        "book_no": 3,
        "title": "经络腧穴各论",
        "sections": [
            "手太阴肺经及其腧穴",
            "手阳明大肠经及其腧穴",
            "足阳明胃经及其腧穴",
            "足太阴脾经及其腧穴",
            "手少阴心经及其腧穴",
            "手太阳小肠经及其腧穴",
            "足太阳膀胱经及其腧穴",
            "足少阴肾经及其腧穴",
            "手厥阴心包经及其腧穴",
            "手少阳三焦经及其腧穴",
            "足少阳胆经及其腧穴",
            "足厥阴肝经及其腧穴",
            "奇经八脉及其相关腧穴",
            "十五络脉及其络穴",
            "常用经外奇穴",
        ],
    },
    {"part": "中篇", "book_no": 4, "title": "刺灸法总论", "sections": []},
    {"part": "中篇", "book_no": 5, "title": "刺灸法各论", "sections": []},
    {"part": "下篇", "book_no": 6, "title": "针灸治疗总论", "sections": []},
    {"part": "下篇", "book_no": 7, "title": "针灸治疗各论", "sections": []},
    {"part": "附篇", "book_no": 8, "title": "针灸研究进展", "sections": []},
    {"part": "附篇", "book_no": 9, "title": "古代人体部位释义", "sections": []},
    {"part": "附篇", "book_no": 10, "title": "子午流注针法与灵龟八法", "sections": []},
    {"part": "附篇", "book_no": 11, "title": "针灸歌赋选", "sections": []},
]

#: 《中医内科学》（新世纪第4版，张伯礼、吴勉华，中国中医药出版社 2017，ISBN 9787513234825）。
#: 总论 + 各论八章、53 节，逐节抄自该版目录（带页码）。
#: 总论是"一、二、三"式行文标题，按不分节处理，六条内容直接作知识点。
#: 章内"附：心瘅 / 附：吐酸"这类附病证不单列为节（教材本身也是附在节下）。
INTERNAL_TOC: list[dict[str, Any]] = [
    {
        "part": "总论",
        "book_no": 0,
        "title": "总论",
        "sections": [],
        "entries": [
            "中医内科学的定义、性质和范围",
            "中医内科学发展简史",
            "中医内科疾病的分类方法",
            "中医内科疾病的辨证方法",
            "中医内科疾病的治疗法则",
            "中医内科学的教学要求和学习方法",
        ],
    },
    {
        "part": "各论",
        "book_no": 1,
        "title": "肺系病证",
        "sections": ["感冒", "咳嗽", "哮病", "喘证", "肺痈", "肺痨", "肺胀", "肺痿"],
    },
    {
        "part": "各论",
        "book_no": 2,
        "title": "心系病证",
        "sections": ["心悸", "胸痹心痛", "心衰", "厥证", "不寐"],
    },
    {
        "part": "各论",
        "book_no": 3,
        "title": "脑系病证",
        "sections": ["头痛", "眩晕", "中风", "痴呆", "癫狂", "痫病"],
    },
    {
        "part": "各论",
        "book_no": 4,
        "title": "脾胃系病证",
        "sections": ["胃痛", "痞满", "呕吐", "噎膈", "呃逆", "腹痛", "泄泻", "痢疾", "便秘"],
    },
    {
        "part": "各论",
        "book_no": 5,
        "title": "肝胆病证",
        "sections": ["胁痛", "黄疸", "积聚", "鼓胀", "瘿病", "疟疾"],
    },
    {
        "part": "各论",
        "book_no": 6,
        "title": "肾系病证",
        "sections": ["水肿", "淋证", "癃闭", "阳痿", "遗精"],
    },
    {
        "part": "各论",
        "book_no": 7,
        "title": "气血津液病证",
        "sections": ["郁证", "血证", "痰饮", "消渴", "自汗盗汗", "内伤发热", "虚劳", "肥胖", "癌病"],
    },
    {
        "part": "各论",
        "book_no": 8,
        "title": "肢体经络病证",
        "sections": ["痹证", "痉证", "痿证", "颤证", "腰痛"],
    },
]

#: 契约里已声明、尚未接教材目录的学科（见 ``app/meridian/schema.py`` 的 subject 字面量）。
#: 放在这里只是为了让体检表把它们列出来——**没有目录就造不出图**，不许用别的学科顶上。
#: 现在六门全部接上，这里留空是**有意的**：下一门学科接进来之前，先在这里登记，
#: 体检表就能提前把"缺目录"摆出来，而不是等人问「其他学科呢」。
PLANNED_SUBJECTS: dict[str, str] = {}

#: 学科注册表。接新学科只加一项，**不要为新学科写新的造图逻辑**——目录形态都一样。
#: 两种来源，按有没有 ``toc`` 分流（是"数据长什么样"的分流，不是"哪个学科"的分流）：
#:
#: * ``entries``：教材条目 JSON 自带 ``chapter_no / chapter / section``，各论从条目长出来，
#:   总论用 ``general`` 人工录入（中药学就是这种，442 条条目在源数据里）。
#: * ``toc``：整本目录人工录入（方剂学没有带章节的条目文件，只有印刷目录可用）。
#:   此时章节结构照样来自教材，只是没有方名——**不许为了"看起来满"去编方名**。
SUBJECTS: dict[str, dict[str, Any]] = {
    "herb": {
        "title": "中药学",
        "entries": ROOT / "data" / "curated" / "textbook_entries.json",
        "entry_kind": "herb_group",
        "unit": "味",
        "textbook": {
            "title": "中药学",
            "edition": "第10版",
            "editor": "钟赣生",
            "publisher": "中国中医药出版社",
        },
        "general": GENERAL_CHAPTERS,
        "toc_source": (
            "《中药学》第10版（钟赣生主编，中国中医药出版社）印刷目录："
            "总论第一章—第七章；各论第八章—第二十八章。"
            "核对来源 https://book.cppinfo.cn/Encyclopedias/home/Index/4288630 "
            "（国家出版发行信息公共服务平台·本书目录页）"
        ),
    },
    "formula": {
        "title": "方剂学",
        "toc": FORMULA_TOC,
        # 方名条目与目录是**两份独立来源**：目录来自教材版权页/记忆手册，
        # 方名来自《方剂歌诀》教学文档（方名与章节同时给出）。合并时按节名精确匹配，
        # 对不上直接报错，不按序号猜。
        "entries": ROOT / "data" / "curated" / "formula_entries.json",
        "entry_kind": "formula_group",
        "unit": "首",
        "textbook": {
            "title": "方剂学",
            "edition": "新世纪第5版（十四五规划）",
            "editor": "李冀、左铮云",
            "publisher": "中国中医药出版社",
        },
        "toc_source": (
            "《方剂学》（李冀、左铮云主编，中国中医药出版社，2021，ISBN 9787513268714）"
            "上篇总论 6 章 + 下篇各论 21 章。"
            "核对来源 https://book.cppinfo.cn/Encyclopedias/home/index?id=4295561 "
            "与配套《方剂学记忆手册》目录（同社同版）。"
            "注意：本书各论章号从「第一章」重新起算，本文件内部编号为连续的 1–27，"
            "书上序号另存 book_no。"
        ),
    },
    "basic": {
        "title": "中医基础理论",
        "toc": BASIC_TOC,
        "entries": ROOT / "data" / "curated" / "basic_entries.json",
        "entry_kind": "concept",
        "unit": "条",
        "textbook": {
            "title": "中医基础理论",
            "edition": "新世纪第5版（十四五规划）",
            "editor": "郑洪新、杨柱",
            "publisher": "中国中医药出版社",
        },
        "toc_source": (
            "《中医基础理论》（郑洪新、杨柱主编，中国中医药出版社，2021，ISBN 9787513269056）"
            "绪论 + 九章、38 节。目录逐节取自该版版权页，"
            "核对来源 https://www.iyunshu.com/item.html?itemId=2109090001656236 。"
            "本书不分总论/各论：绪论按总纲处理（part=总论），第一—九章为正文（part=各论）。"
        ),
    },
    "diagnosis": {
        "title": "中医诊断学",
        "toc": DIAGNOSIS_TOC,
        "entry_kind": "concept",
        "unit": "条",
        "textbook": {
            "title": "中医诊断学",
            "edition": "新世纪第5版（十四五规划）",
            "editor": "李灿东、方朝义",
            "publisher": "中国中医药出版社",
        },
        "toc_source": (
            "《中医诊断学》（李灿东、方朝义主编，中国中医药出版社，2021-06，"
            "ISBN 9787513268493）绪论 + 九章、27 节。"
            "章目与第一章前两节的节名核对来源 "
            "https://book.cppinfo.cn/Encyclopedias/home/index?id=4418364 ；"
            "其余节名逐条对自同社上一版（2016，ISBN 9787513234009）目录，两版节次一致。"
            "绪论里「一、二、三」是行文标题不是节号，按不分节处理，五条内容直接作知识点。"
        ),
    },
    "acup": {
        "title": "针灸学",
        "toc": ACUP_TOC,
        "entry_kind": "concept",
        "unit": "条",
        "textbook": {
            "title": "针灸学",
            "edition": "新世纪第5版（十四五规划）",
            "editor": "梁繁荣、王华",
            "publisher": "中国中医药出版社",
        },
        "toc_source": (
            "《针灸学》（梁繁荣、王华主编，中国中医药出版社，2021-07，ISBN 9787513268127）"
            "绪论 + 上篇经络腧穴 3 章 + 中篇刺灸法 2 章 + 下篇治疗 2 章 + 附篇 4 章。"
            "上篇三章共 23 个节名逐条对自 https://book.cppinfo.cn/Encyclopedias/home/Index/4288613 ；"
            "篇/章结构另有 2024 年安徽中医药大学专升本《针灸学》考试大纲（指定本版）旁证："
            "https://www.hlsok.com/ah/news/64195.html 。"
            "注意：中篇、下篇、附篇各章教材只印章名、不印节号，本文件按不分节处理。"
        ),
        "entries_note": "中篇/下篇/附篇各章教材未印节号，按章级单节点处理；补节须对照纸质目录",
    },
    "internal": {
        "title": "中医内科学",
        "toc": INTERNAL_TOC,
        "entry_kind": "concept",
        "unit": "条",
        "textbook": {
            "title": "中医内科学",
            "edition": "新世纪第4版（十三五规划）",
            "editor": "张伯礼、吴勉华",
            "publisher": "中国中医药出版社",
        },
        "toc_source": (
            "《中医内科学》（张伯礼、吴勉华主编，中国中医药出版社，2017，ISBN 9787513234825）"
            "总论 + 各论八章、53 节，逐节抄自该版目录（带页码）。"
            "核对来源 https://book.cppinfo.cn/Encyclopedias/home/index?id=4314466 "
            "与同版目录页 https://baike.baidu.com/item/中医内科学（第4版/本科中医药类/配增值）/66250278 。"
            "总论是「一、二、三」式行文标题，按不分节处理；"
            "章内「附：心瘅」这类附病证不单列为节。"
        ),
    },
}

_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_to_int(text: str) -> int:
    """把「第八章」「二十四」这类中文序数转成整数。只处理 1–99。"""

    raw = re.sub(r"[第章]", "", text.strip())
    if not raw:
        raise ValueError(f"无法解析章号：{text!r}")
    if raw.isdigit():
        return int(raw)
    if raw == "十":
        return 10
    if raw.startswith("十"):
        return 10 + _CN_DIGITS.get(raw[1], 0)
    if "十" in raw:
        head, tail = raw.split("十", 1)
        return _CN_DIGITS.get(head, 1) * 10 + _CN_DIGITS.get(tail, 0)
    if raw in _CN_DIGITS:
        return _CN_DIGITS[raw]
    raise ValueError(f"无法解析章号：{text!r}")


def _sections_from_titles(
    titles: list[str],
    *,
    chapter_title: str,
    kind: str,
    entries: list[str] | None = None,
) -> list[dict[str, Any]]:
    """把「节名列表」摊成节结构；空列表 = 该章不分节，给一个 virtual 节占位。

    不分节时**不能**造一个和章同名的「第一节」——那是上一版把「化湿药」变成
    「第一节 化湿药」的根源。``virtual=True`` 让前端知道"这一章就是一层"。

    ``entries`` 只在不分节时可用：书上用「一、二、三」分行写的内容（中医内科学
    总论那样）本身就是知识点，挂在虚拟节下面即可——既没造节号，也没把内容丢掉。
    """

    if not titles:
        if entries:
            return [
                {
                    "no": 1,
                    "title": chapter_title,
                    "kind": kind,
                    "virtual": True,
                    "entries": list(entries),
                }
            ]
        return [
            {"no": 1, "title": chapter_title, "kind": kind, "virtual": True, "entries": []}
        ]
    if entries:
        raise ValueError(
            f"第「{chapter_title}」章既给了节名又给了章级条目：节名存在时条目应挂在节下，"
            "不要两处都写（会重复计数）"
        )
    return [
        {"no": i, "title": t, "kind": kind, "virtual": False, "entries": []}
        for i, t in enumerate(titles, start=1)
    ]


def _attach_entries(chapters: list[dict[str, Any]], doc: dict[str, Any], *, path: Path) -> None:
    """把「章 → 节 → 方名」条目挂到目录上。**按节名精确匹配，对不上就报错。**

    不按序号匹配：教材改版时节号会挪，节名基本不动；按序号匹配会把「第二节」的
    方名挂到别人身上，而且看起来完全正常。找不到节名时宁可停下——这正是上一版
    「每节只有一个节名占位」的原因，不能用猜的办法填满。
    """

    # 章号在两部里可能重号（方剂学总论 1–6 与各论 1–6 都叫「第一章」），
    # 所以先收各论，总论只在**这个号没被占**时补进来。中基的绪论是总论、书上第 1 章，
    # 正文从第 2 章起，靠 setdefault 才不会被漏掉。
    by_book = {int(ch["book_no"]): ch for ch in chapters if ch["part"] == "各论"}
    for ch in chapters:
        if ch["part"] != "各论":
            by_book.setdefault(int(ch["book_no"]), ch)
    unmatched_chapters: list[str] = []
    unmatched_sections: list[str] = []
    filled = 0

    for src in doc["chapters"]:
        book_no = int(src["book_no"])
        target = by_book.get(book_no)
        if target is None:
            unmatched_chapters.append(f"第{book_no}章 {src['title']}")
            continue
        if target["title"] != src["title"]:
            raise ValueError(
                f"章名对不上：目录「{target['title']}」/ 条目「{src['title']}」（各论第{book_no}章）"
            )
        sec_by_title = {s["title"]: s for s in target["sections"]}
        for sec in src["sections"]:
            hit = sec_by_title.get(sec["title"])
            if hit is None:
                unmatched_sections.append(f"各论第{book_no}章 第{sec['no']}节 {sec['title']}")
                continue
            names: list[str] = []
            levels: dict[str, str] = {}
            for entry in sec["entries"]:
                # 两种形态都收：``"麻黄汤"`` 与 ``{"name": "麻黄汤", "importance": "掌握"}``。
                # 有等级就存（能当考频依据），没有就不编——中基的条目本身没有等级。
                if isinstance(entry, str):
                    names.append(entry)
                else:
                    names.append(str(entry["name"]))
                    if entry.get("importance"):
                        levels[str(entry["name"])] = str(entry["importance"])
            if not names:
                continue
            hit["entries"] = names
            if levels:
                # 掌握★ / 熟悉* / 了解▲ 是文档给的考纲等级，比编一个 exam_frequency 可靠。
                # 前端不用可以不管，但别丢——丢了就再也说不清"为什么这首先学"。
                hit["importance"] = levels
            filled += 1

    if unmatched_chapters or unmatched_sections:
        raise ValueError(
            "条目里的章节在教材目录中找不到，拒绝按猜测挂靠："
            f"章 {unmatched_chapters}；节 {unmatched_sections}"
        )
    if filled == 0:
        raise ValueError(f"{path.name} 里没有任何节匹配上，检查 book_no 与节名")


def _build_from_toc(subject: str, spec: dict[str, Any]) -> dict[str, Any]:
    """整本目录人工录入的学科（方剂学）。章号连续，书上序号存 ``book_no``。

    为什么不能直接用书上序号当主体：方剂学的各论从「第一章」重新起算，
    总论 6 章 + 各论 21 章 = 两个 c01。内部必须统一成 c01–c27 才不撞键，
    但地图上又必须说得出「各论第一章 解表剂」，所以两套号都存。
    """

    kind = spec.get("entry_kind", "entry_group")
    chapters: list[dict[str, Any]] = []
    for idx, ch in enumerate(spec["toc"], start=1):
        chapters.append(
            {
                "no": idx,
                "book_no": int(ch["book_no"]),
                "part": ch["part"],
                "title": ch["title"],
                "sections": _sections_from_titles(
                    ch["sections"],
                    chapter_title=ch["title"],
                    kind=kind,
                    entries=ch.get("entries"),
                ),
            }
        )

    entries_spec = spec.get("entries")
    entries_doc: dict[str, Any] | None = None
    if entries_spec:
        path = Path(entries_spec)
        if not path.exists():
            raise FileNotFoundError(
                f"{path} 不存在。方名条目由 `python scripts/parse_formula_gejue.py` 生成，"
                "不接受手写或模型生成。"
            )
        entries_doc = json.loads(path.read_text(encoding="utf-8"))
        _attach_entries(chapters, entries_doc, path=path)  # type: ignore[arg-type]

    textbook = {
        **spec["textbook"],
        "toc_source": spec["toc_source"],
        **({"entries_note": spec["entries_note"]} if spec.get("entries_note") else {}),
    }
    if entries_doc:
        src = entries_doc["source"]
        textbook["entries_source"] = {
            "title": src["title"],
            "url": src["url"],
            # PDF 来源给文件指纹；网页目录没有文件可算，就不编一个假的。
            **({"sha256": src["sha256"]} if src.get("sha256") else {}),
            **({"cites": src["cites"]} if src.get("cites") else {}),
            **({"isbn": src["isbn"]} if src.get("isbn") else {}),
            **({"coverage_note": entries_doc["coverage_note"]} if entries_doc.get("coverage_note") else {}),
        }
    return {
        "subject": subject,
        "title": spec["title"],
        "textbook": textbook,
        "schema_version": 1,
        "chapters": chapters,
    }


def build(subject: str) -> dict[str, Any]:
    """按学科注册表生成目录。所有学科走同一条路，没有 per-subject 分支。"""

    spec = SUBJECTS[subject]
    if spec.get("toc"):
        return _build_from_toc(subject, spec)

    entries_path = Path(spec["entries"])
    if not entries_path.exists():
        raise FileNotFoundError(
            f"学科 {subject!r} 的教材条目文件不存在：{entries_path}。\n"
            "目录必须从教材条目长出来，不接受手写章节或让模型编——"
            "条目文件里每条要带 chapter_no / chapter / section / page 四个字段。"
        )
    payload = json.loads(entries_path.read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = payload["entries"]
    general_spec = spec["general"]

    # 缺字段的条目会让某一章悄悄塌成一节，图上只看着"这章内容少"。
    # 接入新学科时，这是最该在入口就拦住的一类问题。
    required = ("name", "chapter_no", "chapter")
    broken = [i for i, it in enumerate(entries) if any(not it.get(k) for k in required)]
    if broken:
        sample = [entries[i] for i in broken[:3]]
        raise ValueError(
            f"{entries_path.name} 里 {len(broken)} 条缺 {required} 之一，无法定位到章：{sample}"
        )

    # 各论：把条目按 (章号, 章名) → 节名 归拢。节名缺失 = 该章不分节。
    grouped: dict[tuple[int, str], dict[str | None, list[dict[str, Any]]]] = {}
    for item in entries:
        no = cn_to_int(str(item["chapter_no"]))
        chapter = str(item["chapter"]).strip()
        section = (item.get("section") or "").strip() or None
        grouped.setdefault((no, chapter), {}).setdefault(section, []).append(item)

    chapters: list[dict[str, Any]] = []

    # 总论（教材前若干章，人工录入）
    for ch_spec in general_spec:
        chapters.append(
            {
                "no": ch_spec["no"],
                "book_no": ch_spec["no"],  # 中药学总论与全书同号，两套号重合
                "part": "总论",
                "title": ch_spec["title"],
                "sections": _sections_from_titles(
                    ch_spec["sections"], chapter_title=ch_spec["title"], kind="concept"
                ),
            }
        )

    # 各论（教材后续章，从条目里长出来）
    general_max = max((c["no"] for c in general_spec), default=0)
    for (no, title), sec_map in sorted(grouped.items()):
        if no <= general_max:
            raise ValueError(f"章号 {no} 落在总论区间，但条目来自各论（{title}）——教材版本对不上")
        sections = []
        # ★ 节序按**书中出现位置**（该节最小页码）排，不能按节名字符串排。
        #   照名字排的话「清热凉血药」会跑到「清热泻火药」前面（凉 U+51C9 < 泻 U+6CF3），
        #   于是教材第一节变成第四节，地图上的学习顺序整章错位。
        def _first_page(item: tuple[str | None, list[dict[str, Any]]]) -> int:
            pages = [int(it.get("page") or 0) for it in item[1] if it.get("page")]
            return min(pages) if pages else 10**6

        ordered = sorted(sec_map.items(), key=_first_page)
        for idx, (sec_title, items) in enumerate(ordered, start=1):
            items.sort(key=lambda it: int(it.get("page") or 0))
            sections.append(
                {
                    "no": idx,
                    "title": sec_title or title,
                    "kind": spec.get("entry_kind", "entry_group"),
                    "virtual": sec_title is None,
                    "entries": [it["name"] for it in items],
                }
            )
        chapters.append(
            {
                "no": no,
                "book_no": no,  # 中药学全书连续编号，两套号重合
                "part": "各论",
                "title": title,
                "sections": sections,
            }
        )

    chapters.sort(key=lambda c: c["no"])
    return {
        "subject": subject,
        "title": spec["title"],
        "textbook": {**spec["textbook"], "toc_source": spec["toc_source"]},
        "schema_version": 1,
        "chapters": chapters,
    }


def validate(doc: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    chapters = doc["chapters"]

    nos = [c["no"] for c in chapters]
    if nos != list(range(1, len(nos) + 1)):
        problems.append(f"章号必须从 1 连续：{nos[:5]}…{nos[-3:]}")

    # 书上章号在篇内必须连续（起点不一定是 1：中药学各论从第八章起，
    # 方剂学各论从第一章重新起算）。起点用篇内最小值，只查"有没有跳号"。
    for part in {c.get("part") for c in chapters}:
        book = sorted(c["book_no"] for c in chapters if c.get("part") == part)
        if book != list(range(book[0], book[0] + len(book))):
            problems.append(f"{part} 的书上章号不连续：{book}")

    seen_sections: set[str] = set()
    for ch in chapters:
        if not ch["sections"]:
            problems.append(f"第{ch['no']}章 {ch['title']} 没有任何节")
        snos = [s["no"] for s in ch["sections"]]
        if snos != list(range(1, len(snos) + 1)):
            problems.append(f"第{ch['no']}章 节号不连续：{snos}")
        for sec in ch["sections"]:
            key = f"{ch['no']}.{sec['no']}"
            if key in seen_sections:
                problems.append(f"节 key 重复：{key}")
            seen_sections.add(key)
            if not sec["title"].strip():
                problems.append(f"第{ch['no']}章第{sec['no']}节 标题为空")
            names = sec["entries"]
            if len(names) != len(set(names)):
                dup = sorted({n for n in names if names.count(n) > 1})
                problems.append(f"第{ch['no']}章 {sec['title']} 有重复条目：{dup[:5]}")
    return problems


def digest(doc: dict[str, Any]) -> str:
    raw = json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def summarise(doc: dict[str, Any], unit: str = "条") -> None:
    chapters = doc["chapters"]
    general = [c for c in chapters if c["part"] == "总论"]
    specific = [c for c in chapters if c["part"] == "各论"]
    sections = [s for c in chapters for s in c["sections"]]
    real = [s for s in sections if not s["virtual"]]
    entries = [n for s in sections for n in s["entries"]]
    print(f"学科        {doc['title']}（{doc['textbook']['edition']}·{doc['textbook']['editor']}）")
    print(f"章          {len(chapters)}（总论 {len(general)} / 各论 {len(specific)}）")
    print(f"节          {len(sections)}（教材真节 {len(real)} / 章级虚拟节 {len(sections) - len(real)}）")
    print(f"穴位单元    {len(sections)}")
    print(f"知识点      {len(entries)}（教材条目，单位「{unit}」）")
    print(f"指纹        {digest(doc)}")
    print()
    for ch in chapters:
        mark = {"总论": "总", "各论": "各"}.get(ch["part"], ch["part"][:1])
        n = sum(len(s["entries"]) for s in ch["sections"])
        virt = sum(1 for s in ch["sections"] if s["virtual"])
        tail = f"（{virt} 个虚拟节）" if virt else ""
        # 绪论不占章号（书上没有「第零章」），别打「第 0 章」。
        label = f"第{ch['book_no']:>2}章" if int(ch["book_no"]) > 0 else "　绪论　"
        print(
            f"  [{mark}] {ch['part']} {label} {ch['title']:<12}"
            f" {len(ch['sections']):>2} 节 {n:>3} {unit}{tail}"
        )


def _content_coverage() -> dict[str, tuple[int, int]]:
    """学科 → (刻度总数, 其中指向有正文节点的数量)。

    只读不写；库不存在或缺表时返回空表，让体检表显示「未造图」而不是崩掉——
    这个脚本经常在新拉的仓库里第一个跑，那时 ``map_section`` 还不存在。
    """

    import sqlite3

    from app.meridian import store

    try:
        conn = store.connect(store.DB_PATH)
    except (OSError, sqlite3.OperationalError):
        return {}
    try:
        rows = conn.execute(
            """
            SELECT s.layer_id,
                   COUNT(*) AS total,
                   SUM(CASE WHEN COALESCE(TRIM(k.description), '') <> '' THEN 1 ELSE 0 END) AS filled
              FROM map_point_slot p
              JOIN map_section s ON s.id = p.section_id
              LEFT JOIN knowledge_node k ON k.id = p.node_id
             GROUP BY s.layer_id
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()
    return {str(k): (int(t), int(f or 0)) for k, t, f in rows}


def status() -> int:
    """体检：每个学科的教材条目、总论、目录、章→经表齐不齐。

    接新学科时先跑这个——缺的东西一次全列出来，比跑到一半报错省事。
    """

    from app.meridian import hints, store, syllabus  # 脚本侧导入，避免顶层循环依赖

    ready = set(syllabus.available())
    coverage = _content_coverage()
    print(f"{'学科':<8} {'教材条目':<10} {'总论章':<8} {'目录':<8} {'章→经表':<10} {'正文':<10} 说明")
    for key, spec in SUBJECTS.items():
        if spec.get("toc"):
            n_entries = f"TOC {len(spec['toc'])}章"
            n_general = str(sum(1 for c in spec["toc"] if c["part"] == "总论"))
        else:
            entries = Path(spec["entries"])
            n_entries = "-"
            if entries.exists():
                n_entries = str(len(json.loads(entries.read_text(encoding="utf-8"))["entries"]))
            n_general = str(len(spec["general"])) if spec.get("general") else "缺"
        doc_state = "已生成" if key in ready else "未生成"
        table = hints.CHANNEL_HINTS.get(key) or {}
        hint_state = f"{len(table)} 章" if table else "缺"
        # 「正文」这一列回答的是一个比"有没有目录"更实际的问题：**点开穴位有没有东西看**。
        # 结构做对了但正文全空，地图只是好看的空壳，所以它必须出现在体检表里。
        total, filled = coverage.get(key, (0, 0))
        body_state = f"{filled}/{total}" if total else "未造图"
        note = ""
        if key not in ready:
            note = "可跑 build_syllabus 生成目录"
        if not table:
            note = (note + "；" if note else "") + "需在 hints.py 补章→经表才能上地图"
        if total and filled == 0:
            note = (note + "；" if note else "") + "刻度均无正文，点开是空的"
        if spec.get("entries_note"):
            note = (note + "；" if note else "") + spec["entries_note"]
        print(
            f"{key:<8} {n_entries:<10} {n_general:<8} {doc_state:<8} {hint_state:<10} "
            f"{body_state:<10} {note}"
        )
    # 已在契约里声明、但还没接教材目录的学科。**显式列出来**，而不是等人问「其他学科呢」——
    # 空缺要看得见，才不会以为已经做完了。
    for key, title in PLANNED_SUBJECTS.items():
        if key in SUBJECTS:
            continue
        print(f"{key:<8} {'—':<10} {'—':<8} {'未接入':<8} {'缺':<10} 待接《{title}》教材目录")
    print()
    print("接入顺序：教材条目 JSON → SUBJECTS 注册 → 总论章目录 → build_syllabus → hints.py 章→经表 → assign")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成教材目录事实源")
    ap.add_argument("--subject", default="herb", choices=sorted(SUBJECTS))
    ap.add_argument("--check", action="store_true", help="只校验，不写盘")
    ap.add_argument("--status", action="store_true", help="体检各学科的目录与章→经表是否齐备")
    args = ap.parse_args(argv)

    if args.status:
        return status()

    doc = build(args.subject)
    problems = validate(doc)
    summarise(doc, unit=str(SUBJECTS[args.subject].get("unit", "条")))
    if problems:
        print("\n校验失败：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    if args.check:
        print("\n校验通过（未写盘）")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{args.subject}.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n已写入 {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
