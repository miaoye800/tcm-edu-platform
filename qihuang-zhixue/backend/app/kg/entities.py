"""实体身份与解析：id 约定、注册表构建、统一 resolve。

**这一层解决的是「同一个东西在不同表里有不同 id」的问题。**

历史包袱（必须兼容，否则前端会掉 mock）：
- ``knowledge_node.id``：``d17.s1.p1`` 这种确定性树 id
- ``graph_entity.id``：``herb_<sha1前14位>`` 这种名字哈希 id
- ``data/herbs/*.json``：``herb_mahuang`` 这种语义 id

新约定：
- herb      → 直接用 JSON 里的 ``herb_mahuang``（稳定、可读、已被 combinations 引用）
- syndrome  → ``syn_<证候名>``
- formula   → 直接用 ``formula_mahuangtang``
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from contextlib import closing
from pathlib import Path
from typing import Iterable

from app.kb.ingest import load_formulas, load_herbs, load_syndromes
from app.kb.store import backend_root
from app.kg import store
from app.kg.store import KgEntity
from app.profile.store import DB_PATH, connect

CURATED_DIR = "data/curated"


def _load_pinyin_fix() -> dict[str, str]:
    """PDF 文本层拼音错位的校正表（``data/curated/pinyin_fix.json``）。

    只覆盖 ``pinyin`` 显示字段，不碰 ``slug``——slug 决定 ``herb_xxx`` id。
    文件缺失时返回空表，不报错。
    """

    path = backend_root() / CURATED_DIR / "pinyin_fix.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(name): str(item.get("pinyin") or "")
        for name, item in (payload.get("fixes") or {}).items()
        if isinstance(item, dict) and item.get("pinyin")
    }


_PINYIN_FIX = _load_pinyin_fix()


def _load_slug_fix() -> dict[str, str]:
    """拼音丢字母导致 slug 也错时的校正表（``data/curated/slug_fix.json``）。

    和 ``pinyin_fix`` 分开是有意的：前者只改给人看的拼音，后者**改实体 id**。
    合成一个文件，改显示值时就容易顺手把 id 一起改了。
    """

    path = backend_root() / CURATED_DIR / "slug_fix.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(name): str(slug)
        for name, slug in (payload.get("slugs") or {}).items()
        if slug
    }


_SLUG_FIX = _load_slug_fix()


# --------------------------------------------------------------------------- #
# id 约定
# --------------------------------------------------------------------------- #


def _digest(value: str, length: int = 10) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


def syndrome_id(name: str) -> str:
    return f"syn_{name}"


def category_id(name: str) -> str:
    return f"cat_{_digest(name)}"


def symptom_id(name: str) -> str:
    return f"sx_{_digest(name)}"


def kind_of(entity_id: str) -> str:
    """从 id 前缀猜类型，用于无法查到实体时的兜底展示。"""

    if entity_id.startswith("herb_"):
        return "herb"
    if entity_id.startswith("syn_"):
        return "syndrome"
    if entity_id.startswith("formula_"):
        return "formula"
    if entity_id.startswith("cat_"):
        return "category"
    if entity_id.startswith("sx_"):
        return "symptom"
    return "herb"


# --------------------------------------------------------------------------- #
# 注册表构建（纯数据 → 实体，不写库）
# --------------------------------------------------------------------------- #


def _herb_file_index() -> dict[str, str]:
    """herb_id → 相对 backend 的 JSON 路径，供 payload_ref 使用。"""

    index: dict[str, str] = {}
    herbs_dir = backend_root() / "data" / "herbs"
    for path in sorted(herbs_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                index[str(item["id"])] = str(path.relative_to(backend_root())).replace("\\", "/")
    return index


def load_curated_herbs() -> dict[str, dict[str, str]]:
    """教材涉及但知识库未收录的药材：id → {name, pinyin}。

    只提供「身份」，不提供任何性味功效。这些实体会被标成 ``status='orphan'``。
    兼容两种格式：``{"herb_x": "名字"}`` 与 ``{"herb_x": {"name": ..., "pinyin": ...}}``。
    """

    path = backend_root() / CURATED_DIR / "herb_id_names.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw = payload.get("herbs") or {}
    result: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            name = str(value.get("name") or "").strip()
            pinyin = str(value.get("pinyin") or "").strip()
        else:
            name, pinyin = str(value or "").strip(), ""
        if name:
            result[str(key)] = {"name": name, "pinyin": pinyin}
    return result


def _tree_ids_by_name(db_path: Path) -> dict[str, str]:
    """知识树 point 节点名 → 节点 id。只有 point 层才是具体药材/知识点。"""

    with closing(connect(db_path)) as conn:
        try:
            rows = conn.execute(
                "SELECT name, id FROM knowledge_node WHERE level='point'"
            ).fetchall()
        except Exception:
            return {}
    # 同名多节点时取 id 字典序最小，保证可复现。
    result: dict[str, str] = {}
    for name, node_id in sorted(rows, key=lambda row: str(row[1])):
        result.setdefault(str(name), str(node_id))
    return result


def load_curated_aliases() -> dict[str, str]:
    """教材用名 → 知识库标准名。例：熟地 → 熟地黄。"""

    path = backend_root() / CURATED_DIR / "incompatibility.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw = payload.get("name_aliases") or {}
    return {
        str(key): str(value)
        for key, value in raw.items()
        if not str(key).startswith("_") and value
    }


def _split_pinyin(raw: str) -> tuple[str, str]:
    """教材拼音 → (带空格的拼音, 无分隔的 id 片段)。

    教材写法是 ``Yùlǐrén（《神农本草经》）``，去掉括号内容后按音节切开，
    得到 ``yu li ren`` / ``yuliren``。

    **带调元音不等于音节结束。** 声调符号标在韵母的**主元音**上，而韵母
    可能还没写完::

        Gǎo  → G + ǎ + o      （ao 韵，o 在主元音后面）
        Gāi  → G + ā + i
        Qiáo → Q + i + á + o
        Běn  → B + ě + n      （n 韵尾）

    只看「带调字符」就断句，``Gǎoběn`` 会切成 ``Ga / obe / n``，拼音字段
    变成 ``ga obe n``。所以带调元音之后还要吃掉：最多一个非带调元音，
    再吃掉 n / ng 韵尾。

    ``slug`` 取的是全部字母的拼接，与切分点无关——所以修这里不会让任何
    已有的 ``herb_xxx`` id 发生变化。
    """

    text = str(raw or "").split("（")[0].split("(")[0].strip()
    if not text:
        return "", ""
    # 先归一成 [(小写字母, 是否带调)]，丢掉汉字与分隔符
    letters: list[tuple[str, bool]] = []
    for char in text:
        if char in " ,，、·.":
            continue
        decomposed = unicodedata.normalize("NFD", char)
        plain = "".join(c for c in decomposed if not unicodedata.combining(c))
        if not plain.isascii() or not plain.isalpha():
            # 非拉丁字符（汉字、书名号残留）不参与拼音
            continue
        letters.append((plain.lower(), len(decomposed) != len(plain)))

    syllables: list[str] = []
    buffer = ""
    index = 0
    while index < len(letters):
        letter, toned = letters[index]
        buffer += letter
        index += 1
        if not toned:
            continue
        # 韵母没写完：主元音后面可能还跟一个元音（ao / ai / ou / ei / iao …）
        if index < len(letters) and not letters[index][1] and letters[index][0] in "aeiouü":
            buffer += letters[index][0]
            index += 1
        # 韵尾 n / ng
        if index < len(letters) and letters[index][0] == "n":
            buffer += "n"
            index += 1
            if index < len(letters) and letters[index][0] == "g":
                buffer += "g"
                index += 1
        syllables.append(buffer)
        buffer = ""
    if buffer:
        syllables.append(buffer)
    return " ".join(syllables), "".join(syllables)


def load_textbook_entries() -> dict[str, dict[str, str]]:
    """教材**词表**：教材立了独立条目的每一味药（名字、拼音、章节、页码）。

    这是 P1 的第三个对齐源。没有它，树上「教材有、知识库还没抽到数据」的药
    会被误报成「教材未收录」——把「我们没做」说成「教材没有」，同样是假话。
    """

    path = backend_root() / CURATED_DIR / "textbook_entries.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, dict[str, str]] = {}
    for item in payload.get("entries") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in result:
            continue
        pinyin, slug = _split_pinyin(str(item.get("pinyin") or ""))
        # PDF 文本层有两类损伤，都靠人工校正表救：
        #   1. 声调字母整个丢失、音节错位（「Fúílng」= 茯苓）→ 显示值靠 pinyin_fix
        #   2. 丢字母后 slug 也错（`fuilng`）→ 靠 slug_fix
        # slug 决定 `herb_xxx` id，改它等于改实体 id，所以校正值必须**与库里
        # 已有 id 对齐**（茯苓库里早就叫 `herb_fuling`），否则同一味药会变两个实体。
        fixed = _PINYIN_FIX.get(name)
        slug = _SLUG_FIX.get(name, slug)
        result[name] = {
            "pinyin": fixed or pinyin,
            "slug": slug,
            "chapter": str(item.get("chapter") or ""),
            # chapter_no 只用于拼证据出处（「第二十四章·补气药·人参 p412」），
            # 不参与对齐——对齐看的是名字，不是章节号。
            "chapter_no": str(item.get("chapter_no") or ""),
            "section": str(item.get("section") or ""),
            # **页码统一成 1 基**：`parse.py` 用 `enumerate(pages)` 记的是 PDF 的
            # 0 基索引，而人翻书数的是「第几页」。实测：人参条目落在索引 631，
            # 书上是第 632 页。对外一律 +1，否则给读者的页码永远差一页——
            # 「可溯源」给错页码，比不给更糟。
            "page": _page_1based(item.get("page")),
        }
    return result


def _page_1based(raw: object) -> str:
    """把教材条目里的 0 基页码转成书上的人读页码；非数字返回空串。"""

    text = str(raw or "").strip()
    if not text.isdigit():
        return ""
    return str(int(text) + 1)


def textbook_entry(name: str) -> dict[str, str] | None:
    """这个名字在教材里有没有独立条目。只读，查不到就是没有。"""

    return load_textbook_entries().get((name or "").strip())


#: 教材书名。证据 ref 统一用这个前缀，读者拿到就能翻书。
BOOK = "《中药学》新世纪第四版（钟赣生主编）"


def textbook_page_ref(name: str, book: str = BOOK) -> tuple[int | None, str]:
    """用教材条目拼一条**带页码**的出处：``(page, ref)``；查不到返回 ``(None, "")``。

    为什么需要它：P2 重建三层边时，来自 ``data/herbs/*.json`` 的边只能给出
    「第二十四章·补气药·人参」这种章级出处——点开看到的是章节，不是页码，
    读者没法翻到原文那一页核对。教材条目表里有 page，用得上就用。

    **查不到就返回 None，不猜页码。** 编一个页码比没有页码更糟：它会让
    「可溯源」这条红线变成一句空话。
    """

    entry = textbook_entry(name)
    if not entry:
        return None, ""
    page = str(entry.get("page") or "").strip()
    if not page.isdigit():
        return None, ""
    where = "·".join(
        part
        for part in (
            entry.get("chapter_no"),
            entry.get("chapter"),
            entry.get("section"),
            (name or "").strip(),
        )
        if part
    )
    return int(page), f"{book} p{page}·{where}" if where else f"{book} p{page}"


#: 炮制前缀 → 教材里出现这些词时才认。只用于**生成别名**，不生成新实体：
#: `焦栀子` 就是 `栀子` 的炮制品，不该在图上多出一个孤立节点。
#:
#: 两条证据通道，任一条成立才建别名：
#:
#: 1. **教材原文直接写了这个词**（``生栀子走气分…焦栀子及栀子炭入血分``）
#:    —— 字面子串命中，最硬的证据。
#: 2. **【用法用量】说明了怎么炮制**（``发汗解表宜生用，止咳平喘多炙用``）
#:    —— 教材没写「生麻黄」这三个字，但写了「宜生用」，所以生麻黄/炙麻黄成立。
#:
#: 刻意不做「前缀 × 全部药名」的笛卡尔积：那会造出「炙知母」「盐人参」这种
#: 没人用的名字，和编功效是同一种错。
PREPARATION_TRIGGERS: dict[str, tuple[str, ...]] = {
    "生": ("生用", "生品", "生者"),
    "炙": ("炙用", "炙品"),
    "蜜炙": ("蜜炙", "蜜制"),
    "炒": ("炒用", "清炒", "麸炒", "土炒"),
    "焦": ("炒焦", "焦用", "炒炭"),
    "姜": ("姜制", "姜汁炙", "姜汁炒"),
    "醋": ("醋炙", "醋制", "醋炒"),
    "盐": ("盐炙", "盐制", "盐水炒"),
    "酒": ("酒炙", "酒制", "酒炒"),
    "煅": ("煅用", "煅制", "煅存性"),
    "制": ("制用", "制用者"),
    "煨": ("煨用", "煨制"),
}

#: 允许「原文里字面出现」的前缀。比上面那张表宽——只要教材真写了「栀子炭」，
#: 就不需要它在用法用量里再解释一遍。
LITERAL_PREFIXES: tuple[str, ...] = (
    "麸炒",
    "土炒",
    "清炒",
    "蜜炙",
    "生",
    "炙",
    "炒",
    "焦",
    "炭",
    "煅",
    "醋",
    "酒",
    "盐",
    "姜",
    "制",
    "煨",
)


def _preparation_prefixes_for(name: str, usage: str, quotes: str) -> list[str]:
    """从该味药**自己的教材原文**里读出它有哪些炮制品。读不出就是没有。"""

    found: list[str] = []
    for prefix, triggers in PREPARATION_TRIGGERS.items():
        if any(trigger in (usage or "") for trigger in triggers):
            found.append(prefix)
    for prefix in LITERAL_PREFIXES:
        if f"{prefix}{name}" in (quotes or "") and prefix not in found:
            found.append(prefix)
    return found


def build_registry(db_path: Path = DB_PATH) -> tuple[list[KgEntity], list[tuple[str, str, str]]]:
    """构建全部实体与别名。返回 ``(entities, aliases)``，由调用方决定是否写库。"""

    tree_ids = _tree_ids_by_name(db_path)
    herb_files = _herb_file_index()
    entities: list[KgEntity] = []
    aliases: list[tuple[str, str, str]] = []
    seen_ids: set[str] = set()
    #: 药名 → (【用法用量】里的用法说明, 该味药的教材原文全部 quote)。
    #: 炮制品别名只从这两处读依据，不从别处猜。
    herb_text: dict[str, tuple[str, str]] = {}

    # ---- 1. 药材（有 monograph，approved） ----
    for herb in load_herbs():
        usage = str(getattr(getattr(herb, "dosage", None), "usage_notes", "") or "")
        quotes = " ".join(str(getattr(source, "quote", "") or "") for source in herb.sources)
        herb_text[herb.name] = (usage, quotes)
        entities.append(
            KgEntity(
                id=herb.id,
                kind="herb",
                name=herb.name,
                pinyin=herb.pinyin,
                knowledge_node_id=tree_ids.get(herb.name),
                status="approved",
                payload_ref=herb_files.get(herb.id),
                extra={
                    "category": str(herb.category),
                    "subcategory": herb.subcategory,
                    "verified": herb.verified,
                    "has_monograph": True,
                },
            )
        )
        seen_ids.add(herb.id)
        aliases.append((herb.name, herb.id, "name"))
        for alias in herb.aliases:
            if alias and alias != herb.name:
                aliases.append((alias, herb.id, "herb.aliases"))

    # ---- 2. 被引用但无 monograph 的药材（orphan，名字可信、内容未知） ----
    #
    # 按**名字**去重，不只是按 id。只按 id 去重会漏掉这种情况：某味药的正规
    # 条目 id 是 `herb_baibiandou`，而引用里出现的是「扁豆」→ `herb_biandou`。
    # 两个 id 不同，于是建出两个同名实体，撞上 `UNIQUE(kind, name)`，
    # 整个注册表写库失败。有 monograph 的药不可能是 orphan——它已经有条目了。
    monograph_names = {entity.name for entity in entities if entity.kind == "herb"}
    for herb_id, item in load_curated_herbs().items():
        if herb_id in seen_ids:
            continue
        name = item["name"]
        if name in monograph_names:
            continue
        entities.append(
            KgEntity(
                id=herb_id,
                kind="herb",
                name=name,
                pinyin=item.get("pinyin", ""),
                knowledge_node_id=tree_ids.get(name),
                status="orphan",
                extra={
                    "has_monograph": False,
                    "note": "被教材或其它药材引用，但尚无结构化条目；等教材抽取补齐",
                },
            )
        )
        seen_ids.add(herb_id)
        aliases.append((name, herb_id, "name"))

    # ---- 2b. 教材有独立条目、但还没有结构化药材数据的药（draft） ----
    #
    # 为什么必须建：知识树是按教材目录建的（``textbook_entries.json``），
    # 而实体注册表原来只认 ``data/herbs/*.json``。两边差出来的 52 味药
    # （三七/天麻/山楂/酸枣仁…）在树上点开就被标成「教材未收录」——
    # 教材明明收了，是我们没抽数据。把「没做」说成「没有」同样是假话。
    #
    # 只给「身份」（名字/拼音/章节/页码），不给任何性味功效：
    # 有了身份就能解析、能搜到、能说清楚「缺什么」，比留个空洞诚实。
    textbook = load_textbook_entries()
    by_name = {entity.name: entity.id for entity in entities if entity.kind == "herb"}
    for name, item in sorted(textbook.items()):
        if name in by_name:
            continue
        slug = item.get("slug") or _digest(name)
        entity_id = f"herb_{slug}"
        if entity_id in seen_ids:
            # 拼音撞车（同音异药）时退到哈希后缀，绝不覆盖已有实体
            entity_id = f"herb_{slug}_{_digest(name, 6)}"
        if entity_id in seen_ids:
            continue
        entities.append(
            KgEntity(
                id=entity_id,
                kind="herb",
                name=name,
                pinyin=item.get("pinyin", ""),
                knowledge_node_id=tree_ids.get(name),
                status="draft",
                extra={
                    "has_monograph": False,
                    "chapter": item.get("chapter", ""),
                    "section": item.get("section", ""),
                    "page": item.get("page", ""),
                    "note": "教材立了独立条目，但还没有结构化药材条目（data/herbs 里没有）",
                },
            )
        )
        seen_ids.add(entity_id)
        by_name[name] = entity_id
        aliases.append((name, entity_id, "name"))

    # ---- 2c. 教材用名 → 标准名（熟地 → 熟地黄） ----
    for alias_name, canonical_name in load_curated_aliases().items():
        target_id = by_name.get(canonical_name)
        if target_id and alias_name != canonical_name:
            aliases.append((alias_name, target_id, "textbook_alias"))

    # ---- 2d. 炮制品别名（焦栀子 → 栀子、生麻黄 → 麻黄） ----
    #
    # **只在该味药自己的教材原文提到这个炮制法时才生成**。理由：
    # 「醋人参」「盐人参」这类名字没人在用，凭空生成 24 个前缀 × 176 味药
    # 就是在编别名，和编功效是同一种错。
    # 判据：用法用量/炮制/原文里出现「生用」「炙用」「炒焦」「醋炙」等词。
    all_names = {entity.name for entity in entities}
    for canonical, (usage, quotes) in sorted(herb_text.items()):
        entity_id = by_name.get(canonical)
        if entity_id is None:
            continue
        for prefix in _preparation_prefixes_for(canonical, usage, quotes):
            candidate = f"{prefix}{canonical}"
            if candidate in all_names or candidate == canonical:
                continue
            aliases.append((candidate, entity_id, "preparation"))
            all_names.add(candidate)

    # ---- 3. 方剂（组成确定，功效未补齐） ----
    for fid, item in load_formulas().items():
        name = str(item.get("name") or fid)
        entities.append(
            KgEntity(
                id=fid,
                kind="formula",
                name=name,
                pinyin=str(item.get("pinyin") or ""),
                status="approved",
                payload_ref="data/formulas/_index.json",
                extra={
                    "composition_size": len(item.get("composition") or []),
                    "incomplete": bool(item.get("incomplete", True)),
                },
            )
        )
        seen_ids.add(fid)
        aliases.append((name, fid, "name"))

    # ---- 4. 证候（从药材主治反推，症状/舌脉未补齐） ----
    for name, item in load_syndromes().items():
        sid = syndrome_id(name)
        if sid in seen_ids:
            continue
        entities.append(
            KgEntity(
                id=sid,
                kind="syndrome",
                name=name,
                status="approved",
                payload_ref="data/syndromes/_index.json",
                extra={
                    "related_herb_count": len(item.get("related_herbs") or []),
                    "verified": bool(item.get("verified", False)),
                    "derived_from": item.get("derived_from", ""),
                },
            )
        )
        seen_ids.add(sid)
        aliases.append((name, sid, "name"))

    return entities, aliases


# --------------------------------------------------------------------------- #
# resolve：把任意历史 id / 名字 / 别名解析成一个实体
# --------------------------------------------------------------------------- #


def _legacy_entity_name(entity_id: str, db_path: Path) -> str | None:
    """旧 ``graph_entity``（herb_<sha1>）→ 名字。表不存在时返回 None。"""

    with closing(connect(db_path)) as conn:
        try:
            row = conn.execute(
                "SELECT name FROM graph_entity WHERE id=?", (entity_id,)
            ).fetchone()
        except Exception:
            return None
    return str(row[0]) if row else None


def _tree_node_name(node_id: str, db_path: Path) -> str | None:
    with closing(connect(db_path)) as conn:
        try:
            row = conn.execute(
                "SELECT name FROM knowledge_node WHERE id=?", (node_id,)
            ).fetchone()
        except Exception:
            return None
    return str(row[0]) if row else None


def resolve(query: str, db_path: Path = DB_PATH) -> KgEntity | None:
    """把任意 id / 名字 / 别名解析成实体。

    解析顺序（前命中即返回）：
    1. ``kg_entity.id`` 精确
    2. ``kg_alias.alias`` 精确
    3. ``kg_entity.name`` 精确
    4. ``knowledge_node.id`` → 节点名 → 2/3
    5. 旧 ``graph_entity.id`` → 名字 → 1/2/3

    只读，不写库。查不到就是查不到——不猜、不造。
    """

    raw = (query or "").strip()
    if not raw:
        return None

    candidates: list[str] = [raw]

    tree_name = _tree_node_name(raw, db_path)
    if tree_name:
        candidates.append(tree_name)

    legacy_name = _legacy_entity_name(raw, db_path)
    if legacy_name:
        candidates.append(legacy_name)

    for key in candidates:
        entity = store.get_entity(key, db_path)
        if entity is not None:
            return entity
    for key in candidates:
        hits = store.find_by_alias(key, db_path)
        if hits:
            return hits[0]
    for key in candidates:
        hits = store.find_by_name(key, db_path=db_path)
        if hits:
            return hits[0]
    return None


def resolve_many(queries: Iterable[str], db_path: Path = DB_PATH) -> list[KgEntity]:
    """批量解析，跳过解析不到的（调用方自己决定要不要报 orphan）。"""

    result: list[KgEntity] = []
    seen: set[str] = set()
    for query in queries:
        entity = resolve(query, db_path)
        if entity is None or entity.id in seen:
            continue
        seen.add(entity.id)
        result.append(entity)
    return result


def display_name(entity_id: str, db_path: Path = DB_PATH) -> str:
    """永远给一个人类可读的名字。解析不到时返回「教材未收录」而不是原始 id。

    **必须先走 ``resolve``**：只查 ``kg_entity.id`` 的话，传进来的是名字或别名
    （``人参``、``炙麻黄``）就会查不到，于是把一个明明存在的药材显示成
    「教材未收录」——这跟把不存在的药材显示成有内容一样是假话。
    """

    entity = resolve(entity_id, db_path)
    if entity is not None:
        return entity.name
    node_name = _tree_node_name(entity_id, db_path)
    if node_name:
        return node_name
    legacy = _legacy_entity_name(entity_id, db_path)
    if legacy:
        return legacy
    return "教材未收录"


def tree_points_without_entity(db_path: Path = DB_PATH) -> list[dict[str, str]]:
    """知识树里没有对应实体的 point 节点——「教材未收录」的诚实清单。"""

    with closing(connect(db_path)) as conn:
        try:
            rows = conn.execute(
                """
                SELECT n.id, n.name FROM knowledge_node n
                LEFT JOIN kg_entity e ON e.knowledge_node_id = n.id
                WHERE n.level='point' AND e.id IS NULL
                ORDER BY n.sort_hint, n.id
                """
            ).fetchall()
        except Exception:
            return []
    return [{"node_id": str(row[0]), "name": str(row[1])} for row in rows]


__all__ = [
    "syndrome_id",
    "category_id",
    "symptom_id",
    "kind_of",
    "load_curated_herbs",
    "load_curated_aliases",
    "load_textbook_entries",
    "textbook_entry",
    "textbook_page_ref",
    "BOOK",
    "build_registry",
    "resolve",
    "resolve_many",
    "display_name",
    "tree_points_without_entity",
]
