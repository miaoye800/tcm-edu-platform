"""症状词表：**从教材自动生成**，而不是靠人手写。

为什么不能手写：患者说「嗓子疼」「拉肚子」「没劲儿」，教材写「咽喉肿痛」
「呕吐泄泻」「气虚欲脱」。人工映射表永远补不全——说法是开放的，词表是封闭的。
上一版只有 20 个词，覆盖面确实不够，这个判断是对的。

但这不代表只能靠模型。教材里已经有大量现成的症状描述：

- 76 味人工 monograph 的 549 条 ``indications``，每条都带 ``description``
  （"恶寒发热、无汗、头身疼痛、脉浮紧"）
- 教材抽出的 1201 个证名（385 个单证名 + 816 条「主治清单」条目）

把它们切碎建倒排索引，就有 **6262 条症状短语**，是手写表的 300 倍，而且**跟着
教材一起增长**——教材补一条主治，词表自动多一条。

所以这里做四级，逐级放宽，每级都标明来源：

| 级 | 方法 | 能覆盖什么 | 可靠性 |
|---|---|---|---|
| L1 ``name`` | 证候名原样出现在文本里 | 「风寒表实证」 | 最高 |
| L2 ``alias`` | 口语别名 + 教材并列单项 | 「嗓子疼」→「咽喉肿痛」 | 高 |
| L2 ``phrase`` | 教材症状短语倒排 | 「恶寒发热」「无汗」「脉浮紧」 | 高 |
| L3 ``fuzzy`` | 字符二元组重叠 | 「咳嗽有痰」≈「咳嗽痰多」 | 中，仅兜底 |

**口语别名**（「嗓子疼」→「咽喉肿痛」）机器学不出来，单独放
``data/curated/symptom_aliases.json``，只收高频、明确、一对一的那批。
它只是 L2 的补充，不是主体。教材自己拆出来的并列单项（「湿温初起」）也走
别名层，但那是**教材原文的切分**，不是我们的猜测。

L3 之后还不中，就该轮到 LLM 做**闭集选择**（``symptom_llm.py``）——
注意是「从候选里选」，不是「自由生成」，这样才可控可校验。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from app.kb.ingest import load_herbs, load_syndrome_aliases, load_syndromes
from app.kb.store import backend_root
from app.kg.entities import syndrome_id
from app.profile.store import DB_PATH

CURATED_DIR = "data/curated"

#: 症状短语切分符。教材描述里「大汗、大吐、大泻或久病大失血后元气虚脱」这种并列。
_SPLIT = re.compile(r"[、，；。或及和]")

#: 太短（单字）没有区分度，太长（整句）匹配不上。
_MIN_TERM = 2
_MAX_TERM = 12

#: L3 模糊层的「查询覆盖率」下限。低于这个值基本是噪音；调低会开始冤枉人。
_FUZZY_FLOOR = 0.6

#: 证候名超过这个长度就开始降权。
#:
#: 教材把「一组主治」写成一个条目（「咽喉肿痛，口舌生疮，痈肿疔疮」）。
#: 这类名字在倒排索引里能命中很多短语，得分天然高，会把单个具体证候压死。
#: 8 字是正常证候名的上限（「风寒表实证」5 字，「肝阳上亢」4 字）。
_LONG_NAME = 8

#: 每超出 1 个字的降权系数。
_LONG_NAME_PENALTY = 0.12

#: 并列标点。教材用它把「一组主治」串成一条::
#:
#:     暑湿表证，湿温初起，发热倦怠，胸闷不舒
_LIST_SEP = re.compile(r"[，、；;]")


def is_list_entry(name: str) -> bool:
    """这条「证名」其实是教材的**主治清单**，不是一个证候。

    教材的【应用】是按「一组主治」编排的，一条里塞着好几个证。它在图谱里
    仍然要作为实体存在（药证链指着它，教材原文就是这么写的），但**不能当成
    一个证候名用**：

    - 拿去给模型挑（闭集映射）没有意义，模型挑出来的是「一串主治」不是证；
    - 拆成 2–4 字片段建索引会造出跨词边界的垃圾片段——上面那串的尾片段是
      「不舒」，于是「最近有点不舒服」会命中一条藿香主治，审查器就从
      「不知道你想治什么」变成「你开的药不对症」。**把不知道伪装成知道，
      再拿去冤枉人**，这比漏判坏得多。
    """

    return bool(_LIST_SEP.search(name or ""))


@dataclass(frozen=True)
class SyndromeCandidate:
    """一个候选证候，带命中依据。"""

    syndrome_id: str
    name: str
    score: float
    layer: str  # name / phrase / fuzzy / alias
    matched_terms: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.syndrome_id,
            "name": self.name,
            "score": self.score,
            "layer": self.layer,
            "matched_terms": self.matched_terms,
        }


@dataclass
class _Index:
    #: 短语 → 该短语出现过的证候名
    terms: dict[str, set[str]]
    #: 证候名 → 拼接后的描述文本（L3 用）
    profiles: dict[str, str]
    #: 别名 → 证候名
    aliases: dict[str, list[str]]
    max_term_len: int


_INDEX: dict[str, _Index] = {}


def build_index(db_path: Path = DB_PATH) -> _Index:
    """从知识库 JSON 构建倒排索引。进程内缓存，按 db_path 区分。"""

    key = str(db_path)
    cached = _INDEX.get(key)
    if cached is not None:
        return cached

    terms: dict[str, set[str]] = {}
    profiles: dict[str, str] = {}

    def add(term: str, syndrome: str) -> None:
        term = term.strip()
        if not (_MIN_TERM <= len(term) <= _MAX_TERM) or not syndrome:
            return
        terms.setdefault(term, set()).add(syndrome)

    for herb in load_herbs():
        for indication in herb.indications:
            syndrome = indication.syndrome.strip()
            if not syndrome:
                continue
            add(syndrome, syndrome)
            profiles[syndrome] = f"{profiles.get(syndrome, '')} {indication.description}".strip()
            for fragment in _SPLIT.split(indication.description or ""):
                add(fragment, syndrome)

    for name in load_syndromes():
        add(name, name)
        profiles.setdefault(name, "")
        # 主治清单条目不生成片段，理由见 ``is_list_entry``。
        # 整串本身仍然进索引：用户整句照抄教材时还能直接命中。
        # 拆开的单项在别名表里，走下面的 alias 循环进索引。
        if is_list_entry(name):
            continue
        # 证候名的片段也建索引，让「片段」能反向定位到「全名」：
        #   肿痛 → 咽喉肿痛      （患者说「喉咙肿痛」，教材写「咽喉肿痛」）
        #   热毒 → 热毒证
        # 2 字片段**只取首尾**：中间片段几乎都是跨词边界的垃圾——
        # 「心气不足」中间掏出「气不」，会把「今天天气不错」判成心气不足。
        # 3–4 字片段足够长，跨边界撞车的概率可以忽略，所以全取。
        for size in (2, 3, 4):
            if len(name) <= size:
                continue
            if size == 2:
                starts = (0, len(name) - size)
            else:
                starts = range(len(name) - size + 1)
            for start in starts:
                add(name[start : start + size], name)

    aliases = _load_aliases()
    for alias, targets in aliases.items():
        for target in targets:
            add(alias, target)

    index = _Index(
        terms=terms,
        profiles=profiles,
        aliases=aliases,
        max_term_len=max((len(term) for term in terms), default=0),
    )
    _INDEX[key] = index
    return index


def candidates(
    text: str,
    *,
    top_k: int = 5,
    db_path: Path = DB_PATH,
    allow_fuzzy: bool = True,
) -> list[SyndromeCandidate]:
    """病例文本 → 候选证候。按证据强度排序，不做最终判定。"""

    text = (text or "").strip()
    if not text:
        return []
    index = build_index(db_path)

    # ---- L1 + L2：最长优先、区间不重叠 ----
    matched: list[tuple[str, int]] = []  # (term, 位置)
    consumed: list[tuple[int, int]] = []
    for term in sorted(index.terms, key=len, reverse=True):
        start = text.find(term)
        if start < 0:
            continue
        end = start + len(term)
        if any(start < taken_end and taken_start < end for taken_start, taken_end in consumed):
            continue
        consumed.append((start, end))
        matched.append((term, start))

    if matched:
        return _rank(matched, index, top_k)

    if not allow_fuzzy:
        return []
    return _fuzzy(text, index, top_k)


def _rank(matched: list[tuple[str, int]], index: _Index, top_k: int) -> list[SyndromeCandidate]:
    """按「短语长度 × 稀有度」加权。

    两个因素缺一不可：

    - **长度**：「脉微欲绝」比「无力」具体得多。
    - **稀有度（IDF）**：「咽痛」在几十个证候里都出现，几乎不区分；
      「热毒」只挂在少数几个证候上，命中就是强信号。不按 IDF 加权的话，
      泛词会把专词压死——实测「热毒咽痛」会排到「温疫时毒」而不是「热毒证」。
    """

    total_syndromes = max(len(index.profiles), 1)
    weights: dict[str, float] = {}
    evidence: dict[str, list[str]] = {}
    layers: dict[str, str] = {}
    for term, _ in matched:
        syndromes = index.terms.get(term) or set()
        if not syndromes:
            continue
        df = max(len(syndromes), 1)
        idf = math.log(1.0 + total_syndromes / df)
        weight = (float(len(term)) ** 1.2) * idf
        for syndrome in syndromes:
            weights[syndrome] = weights.get(syndrome, 0.0) + weight
            evidence.setdefault(syndrome, []).append(term)
            # 证候名原样命中 → 记 name 层，优先级最高
            if term == syndrome:
                layers[syndrome] = "name"
            elif term in index.aliases:
                layers.setdefault(syndrome, "alias")
            else:
                layers.setdefault(syndrome, "phrase")

    if not weights:
        return []
    ceiling = max(weights.values()) or 1.0

    def adjusted(name: str, score: float) -> float:
        """长证名降权。

        教材把「一组主治」写成一个条目，比如「热毒疮疡，水火烫伤」「咽喉肿痛，
        口舌生疮，痈肿疔疮」。这类名字字符多、能命中的短语也多，不打折的话
        会把「咽喉肿痛」这种**单个具体证候**压死——实测「热毒咽痛」的 top5
        全被长名占满，射干那条正确的药证链就接不上了。

        ``name`` 层豁免：证候名原样出现时，名字再长也是精确命中。
        """

        if layers.get(name) == "name":
            return score
        extra = max(0, len(name) - _LONG_NAME)
        return score / (1.0 + extra * _LONG_NAME_PENALTY)

    ordered = sorted(
        weights.items(), key=lambda item: (-adjusted(item[0], item[1]), item[0])
    )
    return [
        SyndromeCandidate(
            syndrome_id=syndrome_id(name),
            name=name,
            score=round(adjusted(name, score) / ceiling, 4),
            layer=layers.get(name, "phrase"),
            matched_terms=sorted(set(evidence[name]), key=len, reverse=True)[:6],
        )
        for name, score in ordered[:top_k]
    ]


def _fuzzy(text: str, index: _Index, top_k: int) -> list[SyndromeCandidate]:
    """字面兜底层。**故意设得很保守**，宁可返回空。

    口径是「查询覆盖率」``|target ∩ source| / |target|``，不是 Jaccard。
    原来用 Jaccard 是个错误：查询只有几个二元组，profile 是「证候名 + 整段主治
    描述」，分母被撑到几十，得分永远低于阈值——这一层事实上是死代码。
    覆盖率只问「查询里有几成能在候选里找到」，才是这里要问的问题。

    但覆盖率也有陷阱：阈值太低时，一个泛二元组（「大便」）就能让整句无关文本
    命中。而误判的代价比漏判大——审查器一旦有了候选，就会从「不知道你想治什么」
    （uncertain）变成「你开的药不对症」（mismatched），这是在冤枉人。
    所以阈值取 0.6，宁可不中。

    真正口语化的长句（「一咽口水就疼」）这一层也救不了，那是 L4 闭集映射的活。
    """

    target = _bigrams(text)
    if not target:
        return []
    scored: list[tuple[float, str]] = []
    for name, profile in index.profiles.items():
        source = _bigrams(f"{name} {profile}")
        if not source:
            continue
        overlap = len(target & source)
        if not overlap:
            continue
        score = overlap / len(target)
        if score >= _FUZZY_FLOOR:
            scored.append((score, name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [
        SyndromeCandidate(
            syndrome_id=syndrome_id(name),
            name=name,
            score=round(score, 4),
            layer="fuzzy",
            matched_terms=[],
        )
        for score, name in scored[:top_k]
    ]


def _bigrams(text: str) -> set[str]:
    compact = re.sub(r"[\s，。；、,.;:：/（）()《》<>【】\[\]\"'“”‘’]+", "", text or "")
    return {compact[i : i + 2] for i in range(len(compact) - 1)}


def _load_aliases() -> dict[str, list[str]]:
    """别名表：两个来源合并。

    - ``data/curated/symptom_aliases.json``：36 条**口语**别名（「嗓子疼」→
      「咽喉肿痛」）。机器学不出来，只能人工写。
    - ``data/syndromes/_textbook.json`` 的 ``aliases``：教材自己拆出来的
      **并列单项**（「暑湿表证，湿温初起，发热倦怠，胸闷不舒」→ 四个单项）。

    第二项必须接进来。教材的【应用】按「一组主治」编排，一条里塞好几个证；
    如果不把拆开的单项登记成别名，这条证候就只能靠整串原文命中，患者说
    「湿温初起」永远搜不到——实测 816 条清单里有 346 条这样搜不到。
    """

    result: dict[str, list[str]] = {}
    path = backend_root() / CURATED_DIR / "symptom_aliases.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        for alias, targets in (payload.get("aliases") or {}).items():
            if str(alias).startswith("_"):
                continue
            if isinstance(targets, str):
                targets = [targets]
            cleaned = [str(item) for item in targets if item]
            if alias and cleaned:
                result[str(alias)] = cleaned

    # 教材别名排在后面：口语别名是人工挑过的，冲突时以它为准。
    for alias, target in load_syndrome_aliases().items():
        if alias and target:
            result.setdefault(alias, [target])
    return result


def reset_cache() -> None:
    _INDEX.clear()


def known_syndromes(db_path: Path = DB_PATH) -> list[str]:
    """LLM 闭集映射的候选池：**只收单证名**。

    教材的「主治清单」条目（``暑湿表证，湿温初起，发热倦怠，胸闷不舒``）不是
    证候，不该出现在给模型挑的清单里——让模型在 800 多条主治清单里找「哪个
    像证候」，挑出来的东西没有医学含义，还会把真正需要的证名挤出候选窗口。
    这类条目由字面层负责（整串命中，或拆开的单项经别名命中），不需要模型。

    所以这里返回的是「证候」的全集，而不是「图谱里所有 syndrome 类型节点」的
    全集——两者差的就是这批清单条目，差别是**有意的**。
    """

    return sorted(name for name in load_syndromes() if not is_list_entry(name))


__all__ = [
    "SyndromeCandidate",
    "build_index",
    "candidates",
    "is_list_entry",
    "known_syndromes",
    "reset_cache",
]
