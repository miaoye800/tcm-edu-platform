"""处方审查器：用 KG 的 T1/T2 关系回答「这张方子能不能开」。

旧实现（``app/profile/prescription.py``）有两个硬伤：

1. **十八反只查一个方向**。``_incompatible_pairs`` 遍历每味药的
   ``herb.incompatibility.shibafan``，而教材的写法是「藜芦反诸参辛芍」——
   规则写在**藜芦**名下，不是写在**人参**名下。于是「人参 + 藜芦」这种
   最经典的错误处方，只要用户把藜芦写在后面就可能漏检。现在统一读
   ``kg_edge`` 的 tier=1 边（构建期已双向落库），按**无序对**匹配。
2. **没有妊娠禁忌检查**。教材 p131 明确分了「禁用」「慎用」两级，
   旧实现完全没做。

另外把「对症」拆成两层，并**明说哪一层命中的**：

- **证候层（强证据）**：``herb --主治--> syndrome`` 的 T2 边。
  命中就能给出教材页码 + 原文，这是图谱真正不可替代的地方。
- **功效层（弱证据）**：``data/curated/symptom_lexicon.json`` 的词表。

为什么需要两层：**图谱只能回答「证 → 药」，回答不了「话 → 证」**。
患者说「嗓子疼得厉害」，教材里没有这个词，必须先映射到「咽喉肿痛」这个证，
才谈得上查图。而词表是人工维护的受控词汇，覆盖有限，所以证候层命中不了时
不能直接判「不对症」——那会把大量正确处方误杀。两层分开报，用户自己看得见
证据强弱。

``ReviewFinding.quote`` 永远是**用户原文的片段**（前端要能高亮），
教材原文放在 ``evidence`` 里。两者不混——混了就没法验证「这句是不是他写的」。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.kb.ingest import load_herbs
from app.kb.models import Herb, Toxicity
from app.kb.store import backend_root
from app.kg import store
from app.kg import symptoms
from app.kg.constants import Tier
from app.kg.entities import syndrome_id
from app.kg.symptoms import SyndromeCandidate
from app.profile.store import DB_PATH

CURATED_DIR = "data/curated"

#: 处方审查规则版本。finding 里带上它，错题归因才能说清「按哪版规则判的」。
RULE_VERSION = "kg.rx.v1"

#: 严重度排序：block 永远排第一。
_SEVERITY_RANK = {"block": 0, "warn": 1, "info": 2}

#: 同严重度内的排序。数值越小越靠前。
#:
#: 依据是「错用一次会造成多大伤害」：
#:   1. 十八反/十九畏/妊娠禁忌 —— 绝对禁忌，教材明文「不可同用」
#:   2. 毒性超量 —— 单味药过量，有剂量空间可以调整
#:   3. 未结构化/未识别 —— 我们看不懂，不是处方错
#:   4. 证候不匹配 —— 判断性问题，最软
_KIND_RANK = {
    "incompatible_pair": 1,
    "pregnancy_forbidden": 1,
    "pregnancy_caution": 1,
    "toxic_misuse": 2,
    "dosage_exceeded": 2,
    "dose_abnormal": 2,
    "herb_not_structured": 3,
    "no_recognized_herb": 3,
    "toxicity_unknown": 3,
    "case_unresolved": 4,
    "case_mismatch": 4,
}

#: 妊娠相关线索词。命中才做妊娠禁忌检查——不问孕妇就不该报「孕妇禁用」。
_PREGNANCY_HINTS = ("妊娠", "怀孕", "孕妇", "孕期", "怀胎", "安胎")

#: 炮制前缀，用于从原文还原「生麻黄」「炙甘草」这种写法。
_PREPARATION_PREFIX = r"(?:炙|炒|酒制|姜制|醋制|麸炒|生|煅|煨|蜜炙|清炒)?"


# --------------------------------------------------------------------------- #
# 结果结构
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class HerbLine:
    """处方里的一味药：原文写法 + 归一化后的实体。

    ``herb`` 为 ``None`` 表示这味药在图谱里有身份、但知识库还没收录它的
    性味功效（``status='orphan'``）。这类药做不了剂量/功效检查，但**能做
    T1 冲突检查**——十八反里的藜芦、甘遂、大戟、芫花、巴豆全都在这一类，
    而它们恰恰是最需要查的。旧实现只认有 monograph 的 76 味药，导致
    「人参 + 藜芦」这种经典错误根本检不出来。
    """

    entity_id: str
    name: str
    raw_name: str
    dose_g: float | None
    quote: str
    preparation: str | None
    refs: list[str]
    herb: Herb | None = None

    @property
    def has_monograph(self) -> bool:
        return self.herb is not None

    @property
    def dose_range(self) -> list[float | None] | None:
        """教材给的剂量区间 ``[min_g, max_g]``；没有结构化条目时是 ``None``。

        端点也要给出来，前端才能显示「写了 3g，教材是 0.3~0.6g」这种对照。
        只报一句「超出范围」而不给范围，学生没法判断差多远。
        """
        if self.herb is None:
            return None
        return [self.herb.dosage.min_g, self.herb.dosage.max_g]

    @property
    def dose_status(self) -> str:
        """剂量判定。``unchecked``/``missing``/``ok``/``below``/``above``。

        ``unchecked``（没条目，判不了）与 ``missing``（有条目，但用户没写剂量）
        必须分开——把「判不了」显示成「没问题」是本项目反复踩的坑。
        """
        if self.herb is None:
            return "unchecked"
        if self.dose_g is None:
            return "missing"
        if _dose_in_range(self.herb, self.dose_g):
            return "ok"
        lower = self.herb.dosage.min_g
        return "below" if lower is not None and self.dose_g < lower else "above"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "raw_name": self.raw_name,
            "dose_g": self.dose_g,
            "dose_range": self.dose_range,
            "dose_status": self.dose_status,
            "preparation": self.preparation,
            "knowledge_base_refs": self.refs,
            "has_monograph": self.has_monograph,
        }


@dataclass(frozen=True)
class ReviewFinding:
    """一条可追溯的检查结论。

    ``quote`` 必须是用户原文的子串（前端要能定位高亮）；教材依据放 ``evidence``。
    ``edge_ids`` 指回 ``kg_edge``，错题归因时可以从这条 finding 直接跳到图谱上
    的那条边。
    """

    kind: str
    severity: str  # block / warn / info
    quote: str
    reason: str
    rule: str
    knowledge_base_refs: list[str] = field(default_factory=list)
    edge_ids: list[int] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "quote": self.quote,
            "reason": self.reason,
            "rule": self.rule,
            "knowledge_base_refs": self.knowledge_base_refs or ["kb:local_herb_index:no_match"],
            "edge_ids": self.edge_ids,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class ReviewResult:
    verdict: str  # pass / warn / block
    symptom_match: bool
    symptom_match_status: str  # matched / mismatched / uncertain
    compatible: bool
    dosage_ok: bool
    toxic_misuse: bool
    findings: list[ReviewFinding]
    herbs: list[HerbLine]
    knowledge_base_refs: list[str]
    quote: str
    trace: dict[str, Any] = field(default_factory=dict)
    method: str = "no_key_rule_fallback"
    #: 四项检查的三态结论：``pass`` / ``fail`` / ``unknown``。
    #:
    #: **必须有三态。** 布尔值只有「过」和「不过」，没有「没查」——
    #: 草乌在图谱里只有身份没有性味功效，剂量和毒性根本无从判断，
    #: 用 ``dosage_ok=True`` 表达就成了「合规」，等于把「不知道」
    #: 说成「没问题」。这三项分开存，前端才画得出灰色的「未校验」。
    checks: dict[str, str] = field(default_factory=dict)

    def finding_dicts(self) -> list[dict[str, Any]]:
        return [item.as_dict() for item in self.findings]

    @property
    def blocking(self) -> list[ReviewFinding]:
        return [item for item in self.findings if item.severity == "block"]


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #


def review(
    payload: dict[str, Any],
    db_path: Path = DB_PATH,
    *,
    syndrome_hint: Sequence[SyndromeCandidate] | None = None,
) -> ReviewResult:
    """审查一张处方。纯规则 + 图遍历，**同步、不调模型**。

    ``syndrome_hint`` 用于注入 LLM 闭集映射的结果（见 ``symptom_llm.py``）：
    口语描述「一咽口水就疼」靠教材索引匹配不到时，先由模型从证候清单里挑，
    再把结果从这里喂进来重跑一遍。这样同步调用方（``modes.py``）完全不受影响。
    """

    case_text = _case_text(payload)
    prescription_text = _prescription_text(payload)
    lines = _parse_herbs(payload, prescription_text, db_path)

    findings: list[ReviewFinding] = []
    refs: list[str] = []
    for line in lines:
        refs.extend(ref for ref in line.refs if ref not in refs)
        findings.append(
            ReviewFinding(
                kind="herb_normalized",
                severity="info",
                quote=line.quote,
                reason=f"normalized to {line.name}",
                rule=f"{RULE_VERSION}.normalize",
                knowledge_base_refs=line.refs,
            )
        )
        if line.herb is None:
            # 教材里提过这味药，但知识库还没它的性味功效，所以剂量/毒性/功效
            # 三项都没法判。明说「没校验」，不要让它看起来像「校验通过」。
            findings.append(
                ReviewFinding(
                    kind="herb_not_structured",
                    severity="warn",
                    quote=line.quote,
                    reason=f"{line.name} 尚无结构化条目，剂量、毒性、功效三项未校验",
                    rule=f"{RULE_VERSION}.coverage",
                    knowledge_base_refs=line.refs,
                )
            )

    findings.extend(_unknown_mentions(prescription_text, lines))
    unknown_mentions = [f for f in findings if f.kind == "unrelated_herb" and f.severity == "warn"]

    # ---- 剂量 / 毒性 ----
    dosage_ok = True
    toxic_misuse = False
    #: 这三味药我们根本没数据，对应项必须报「未校验」而不是「合规」。
    dosage_unknown = False
    toxicity_unknown_flag = False
    for line in lines:
        herb = line.herb
        if herb is None:
            dosage_unknown = True
            toxicity_unknown_flag = True
            continue  # 孤儿药材没有剂量区间/毒性分级，跳过而不是瞎判
        if line.dose_g is None:
            # 处方上没写克数，就无从判断「在不在区间内」。
            dosage_unknown = True
        if line.dose_g is not None and not _dose_in_range(herb, line.dose_g):
            dosage_ok = False
            findings.append(
                ReviewFinding(
                    kind="dose_abnormal",
                    severity="warn",
                    quote=line.quote,
                    reason=f"{herb.name} 用量 {line.dose_g:g}g 超出教材区间 {_range_text(herb)}",
                    rule=f"{RULE_VERSION}.dosage",
                    knowledge_base_refs=line.refs,
                    evidence=_dose_evidence(line),
                )
            )
        if _is_toxic(herb) and (line.dose_g is None or not _dose_in_range(herb, line.dose_g)):
            toxic_misuse = True
            findings.append(
                ReviewFinding(
                    kind="toxic_misuse",
                    severity="block",
                    quote=line.quote,
                    reason=f"{herb.name} 有毒，{_dose_text(line)}，未落在教材安全区间 {_range_text(herb)}",
                    rule=f"{RULE_VERSION}.toxicity",
                    knowledge_base_refs=line.refs,
                    evidence=_toxicity_evidence(line),
                )
            )
        elif _toxicity_unknown(herb):
            # 教材没写毒性 ≠ 无毒。既不报安全，也不阻断，只把「没查到」说清楚。
            toxicity_unknown_flag = True
            findings.append(
                ReviewFinding(
                    kind="toxicity_unknown",
                    severity="warn",
                    quote=line.quote,
                    reason=f"{herb.name} 毒性未标注，未做毒性校验",
                    rule=f"{RULE_VERSION}.toxicity",
                    knowledge_base_refs=line.refs,
                    evidence=_toxicity_evidence(line),
                )
            )

    # ---- T1：十八反 / 十九畏 ----
    conflicts = _t1_conflicts(lines, db_path)
    for item in conflicts:
        page, ref = item.page, item.ref
        findings.append(
            ReviewFinding(
                kind="incompatible_pair",
                severity="block",
                quote=_pair_quote(item.left, item.right, prescription_text),
                reason=f"{item.left.name} 与 {item.right.name} 属{item.relation}，不可同方",
                rule=f"{RULE_VERSION}.t1.{item.rule_id}",
                knowledge_base_refs=[*item.left.refs, *item.right.refs],
                edge_ids=[item.edge_id] if item.edge_id is not None else [],
                evidence=[
                    {
                        "ref": ref or (f"《中药学》p{page}" if page else "《中药学》"),
                        "page": page,
                        "quote": item.quote,
                    }
                ],
            )
        )

    # ---- T1：妊娠禁忌 ----
    findings.extend(_pregnancy_findings(case_text, lines, prescription_text))

    # ---- 对症 ----
    symptom_match, status, trace = _match_symptoms(case_text, lines, db_path, syndrome_hint)
    if status == "uncertain":
        # 没写病例时**不报**这条：空 quoted 的「证候无法映射」是噪音，
        # 用户只是来查配伍禁忌的。有病例文本却映射不到，才值得说。
        if case_text:
            findings.append(
                ReviewFinding(
                    kind="case_unresolved",
                    severity="warn",
                    quote=case_text,
                    reason="case symptoms could not be mapped to a supported KB indication/effect target",
                    rule=f"{RULE_VERSION}.case",
                    knowledge_base_refs=refs,
                )
            )
    elif not symptom_match:
        findings.append(
            ReviewFinding(
                kind="case_mismatch",
                severity="warn",
                quote=case_text,
                reason="no selected herb has a KB indication matching the case target",
                rule=f"{RULE_VERSION}.case",
                knowledge_base_refs=refs,
            )
        )
    else:
        matched_names = set(trace.get("matched_herbs") or [])
        for line in lines:
            if line.name in matched_names:
                continue
            findings.append(
                ReviewFinding(
                    kind="unrelated_herb",
                    severity="info",
                    quote=line.quote,
                    reason=f"{line.name} does not match the identified case target",
                    rule=f"{RULE_VERSION}.case",
                    knowledge_base_refs=line.refs,
                )
            )

    if not lines:
        dosage_ok = False
        # 一味药都没认出来，是**我们没看懂**，不是处方有问题。
        # 判 block 等于对用户说「这方子不能用」——而我们连方子写了什么都没读出来。
        # 之前「芩连膏」这种成药名就因此被判 block，属于冤枉人。
        # 这里只提示改写，不阻断。
        findings.append(
            ReviewFinding(
                kind="no_recognized_herb",
                severity="warn",
                quote=prescription_text,
                reason="未能从处方中识别出任何一味药，请按「药名+剂量」逐味书写后重新审查",
                rule=f"{RULE_VERSION}.normalize",
            )
        )

    compatible = bool(lines) and not conflicts and not toxic_misuse and not _pregnancy_blocked(findings)
    verdict = "block" if any(item.severity == "block" for item in findings) else (
        "warn" if any(item.severity == "warn" for item in findings) else "pass"
    )

    # 排序：先按严重度，同严重度内按**医学严重性**。
    #
    # 十八反/十九畏/妊娠禁忌是绝对禁忌，比剂量超量更严重——学生看到的
    # 第一条结论必须是最要命的那个。以前这里是 append 顺序，而剂量检查
    # 先于 T1 配伍检查，所以「人参9g，藜芦3g」会先报藜芦超量、再报反药，
    # 把最严重的问题排到了第二条。
    findings.sort(key=lambda item: (_SEVERITY_RANK.get(item.severity, 9), _KIND_RANK.get(item.kind, 50)))

    # 三态结论。**顺序是「有错就说错，没错再说没查，最后才说没问题」**——
    # 一张方子里只要有一味查不了，这一项就不能报「合规」。
    def _tri(failed: bool, unknown: bool) -> str:
        return "fail" if failed else ("unknown" if unknown else "pass")

    checks = {
        # 配伍走 T1 硬约束表，孤儿药也查得到；但处方里出现**完全不认识**的
        # 药名时，那一对就查不了，只能说未校验。
        #
        # 这里**只算十八反/十九畏/妊娠禁忌**，不把毒性误用并进来：
        # 单味巴豆 0.5g 没有「配伍」可言，把它算成「配伍有禁忌」是标签错位。
        "compatible": _tri(
            bool(conflicts) or _pregnancy_blocked(findings),
            bool(unknown_mentions),
        ),
        "dosage": _tri(not dosage_ok, dosage_unknown),
        "toxicity": _tri(toxic_misuse, toxicity_unknown_flag),
        "symptom": "matched" if symptom_match else ("uncertain" if status == "uncertain" else "mismatched"),
    }

    return ReviewResult(
        verdict=verdict,
        symptom_match=symptom_match,
        symptom_match_status=status,
        compatible=compatible,
        dosage_ok=dosage_ok,
        toxic_misuse=toxic_misuse,
        findings=findings,
        herbs=lines,
        knowledge_base_refs=refs,
        quote=prescription_text or case_text,
        trace=trace,
        checks=checks,
    )


# --------------------------------------------------------------------------- #
# 解析：原文 → 药材实体
# --------------------------------------------------------------------------- #


def _case_text(payload: dict[str, Any]) -> str:
    return " ".join(
        str(payload.get(key) or "").strip()
        for key in ("case", "symptoms", "scenario")
        if str(payload.get(key) or "").strip()
    )


def _prescription_text(payload: dict[str, Any]) -> str:
    return " ".join(
        str(payload.get(key) or "").strip()
        for key in ("prescription", "answer", "selected_herbs", "herbs")
        if str(payload.get(key) or "").strip()
    )


@dataclass(frozen=True)
class _HerbRef:
    """名字 → 实体。``herb`` 为 None 即「有身份无内容」的孤儿药材。"""

    entity_id: str
    name: str
    herb: Herb | None


def _herb_index(db_path: Path) -> dict[str, _HerbRef]:
    """名字/别名 → 实体索引。**必须包含图谱里的孤儿药材**。

    只用 ``load_herbs()`` 会漏掉藜芦、甘遂、大戟、芫花、巴豆这些只有身份、
    没有 monograph 的药——而它们正是十八反/十九畏/妊娠禁忌里的主角。
    """

    key = str(db_path)
    cached = _HERB_INDEX.get(key)
    if cached is not None:
        return cached

    monograph = {herb.id: herb for herb in load_herbs()}
    index: dict[str, _HerbRef] = {}
    for entity in store.list_entities(kind="herb", db_path=db_path):
        index[entity.name] = _HerbRef(entity.id, entity.name, monograph.get(entity.id))
    for herb in monograph.values():
        index.setdefault(herb.name, _HerbRef(herb.id, herb.name, herb))
        for alias in herb.aliases:
            if alias:
                index.setdefault(alias, _HerbRef(herb.id, herb.name, herb))
    # 教材用名 → 标准名（熟地 → 熟地黄）
    for alias_name, canonical_name in (_curated().get("name_aliases") or {}).items():
        target = index.get(str(canonical_name))
        if target is not None and alias_name:
            index.setdefault(str(alias_name), target)

    _HERB_INDEX[key] = index
    return index


_HERB_INDEX: dict[str, dict[str, _HerbRef]] = {}


def _parse_herbs(payload: dict[str, Any], text: str, db_path: Path) -> list[HerbLine]:
    """把处方文本（或结构化列表）归一化成 HerbLine。

    结构化输入优先——用户已经给了 ``[{name, dose}]`` 就别再猜。
    文本输入按名字长度倒序匹配，避免「甘草」抢走「炙甘草」。
    """

    by_name = _herb_index(db_path)
    lines: list[HerbLine] = []
    seen: set[str] = set()
    #: 已被占用的原文区间。防止「牵牛子」匹配后「牵牛」又从里面抠一遍，
    #: 变成两味并不存在的药——这两个名字在图谱里是两个实体。
    consumed: list[tuple[int, int]] = []

    structured = payload.get("herbs") or payload.get("selected_herbs")
    if isinstance(structured, list):
        for item in structured:
            raw_name = (
                str(item.get("name") or item.get("herb") or "").strip()
                if isinstance(item, dict)
                else str(item).strip()
            )
            ref = by_name.get(raw_name)
            if ref is None or ref.entity_id in seen:
                continue
            dose = _as_float(item.get("dose") or item.get("dosage") or item.get("grams")) if isinstance(item, dict) else None
            quote = raw_name if raw_name in text else text
            lines.append(_line(ref, raw_name, dose, quote))
            seen.add(ref.entity_id)

    for raw_name, ref in sorted(by_name.items(), key=lambda kv: len(kv[0]), reverse=True):
        if ref.entity_id in seen:
            continue
        start = text.find(raw_name)
        if start < 0:
            continue
        end = start + len(raw_name)
        if any(start < taken_end and taken_start < end for taken_start, taken_end in consumed):
            continue
        consumed.append((start, end))
        quote = _quote_for_name(raw_name, text)
        prepared = _raw_name_with_preparation(raw_name, quote)
        lines.append(_line(ref, prepared, _dose_after_name(raw_name, text), quote))
        seen.add(ref.entity_id)

    return lines


def _line(ref: _HerbRef, raw_name: str, dose: float | None, quote: str) -> HerbLine:
    refs = [f"herb:{ref.entity_id}"]
    if ref.herb is not None:
        refs.extend(source.ref for source in ref.herb.sources if source.ref)
    # 一味药的 5 条出处往往指向同一本教材同一节，``ref`` 字符串完全相同（只差页码与
    # 摘录）。原样拼进 ``knowledge_base_refs`` 会让前端把同一句引用重复渲染五遍，
    # 所以按首次出现顺序去重；页码与原文在 findings[].evidence 里另有完整保留。
    refs = list(dict.fromkeys(refs))
    return HerbLine(
        entity_id=ref.entity_id,
        name=ref.name,
        raw_name=raw_name,
        dose_g=dose,
        quote=quote,
        preparation=_preparation(raw_name),
        refs=refs,
        herb=ref.herb,
    )


def _unknown_mentions(text: str, lines: Sequence[HerbLine]) -> list[ReviewFinding]:
    """带剂量但认不出的药名——这比「没写」更值得提醒。"""

    known = {line.raw_name for line in lines} | {line.name for line in lines}
    findings: list[ReviewFinding] = []
    seen: set[str] = set()
    for match in re.finditer(r"([\u4e00-\u9fff]{2,6})(?=\s*\d+(?:\.\d+)?\s*(?:g|克))", text, flags=re.I):
        name = match.group(1)
        if name in known or name in seen:
            continue
        seen.add(name)
        findings.append(
            ReviewFinding(
                kind="unrelated_herb",
                severity="warn",
                quote=name,
                reason="material is not present in the local herb knowledge base",
                rule=f"{RULE_VERSION}.normalize",
            )
        )
    return findings


# --------------------------------------------------------------------------- #
# T1：十八反 / 十九畏 / 妊娠禁忌
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Conflict:
    """一对 T1 冲突。``edge_id`` 为 None 表示走的是 curated 降级路径。"""

    left: HerbLine
    right: HerbLine
    relation: str
    page: int | None
    ref: str
    quote: str
    edge_id: int | None
    rule_id: str


def _t1_conflicts(lines: Sequence[HerbLine], db_path: Path) -> list[_Conflict]:
    """无序对匹配 T1 边。

    读 ``kg_edge`` 的 tier=1。图还没建（库里一条 T1 都没有）时退回
    ``data/curated/incompatibility.json`` 的分组规则，保证功能不依赖构建步骤。
    """

    if len(lines) < 2:
        return []
    by_id = {line.entity_id: line for line in lines}
    results: list[_Conflict] = []

    edges = store.edges_touching(list(by_id), tiers=(int(Tier.CONSTRAINT),), db_path=db_path)
    for edge in edges:
        left = by_id.get(edge.source_id)
        right = by_id.get(edge.target_id)
        if left is None or right is None:
            continue  # 只有一端在处方里，不算冲突
        evidence = edge.evidence[0] if edge.evidence else {}
        results.append(
            _Conflict(
                left=left,
                right=right,
                relation=edge.relation_type,
                page=_as_int(evidence.get("page")),
                ref=str(evidence.get("ref") or ""),
                quote=str(evidence.get("quote") or edge.label),
                edge_id=edge.id,
                rule_id=str(evidence.get("rule_id") or edge.relation_type),
            )
        )

    if results:
        return results
    return _t1_conflicts_from_curated(lines)


def _t1_conflicts_from_curated(lines: Sequence[HerbLine]) -> list[_Conflict]:
    """降级路径：直接读 curated 分组，不依赖 ``kg_edge``。"""

    payload = _curated()
    names = {line.name for line in lines}
    by_name = {line.name: line for line in lines}
    results: list[_Conflict] = []
    for key, relation in (("shibafan", "十八反"), ("shijiuwei", "十九畏")):
        for rule in payload.get(key) or []:
            group_a = {str(item) for item in rule.get("a") or []}
            group_b = {str(item) for item in rule.get("b") or []}
            for a in sorted(group_a & names):
                for b in sorted(group_b & names):
                    if a == b:
                        continue
                    results.append(
                        _Conflict(
                            left=by_name[a],
                            right=by_name[b],
                            relation=relation,
                            page=_as_int(rule.get("page")),
                            ref=str(rule.get("book_ref") or ""),
                            quote=str(rule.get("quote") or ""),
                            edge_id=None,
                            rule_id=str(rule.get("rule_id") or key),
                        )
                    )
    return results


def _pregnancy_findings(
    case_text: str, lines: Sequence[HerbLine], prescription_text: str
) -> list[ReviewFinding]:
    """妊娠禁忌：只在病例文本提到妊娠时才检查。"""

    if not any(hint in case_text for hint in _PREGNANCY_HINTS):
        return []
    payload = _curated().get("pregnancy") or {}
    names = {line.name: line for line in lines}
    findings: list[ReviewFinding] = []
    for key, kind, severity in (
        ("forbidden", "pregnancy_forbidden", "block"),
        ("caution", "pregnancy_caution", "warn"),
    ):
        section = payload.get(key) or {}
        listed = {str(item) for item in section.get("herbs") or []}
        for name in sorted(listed & set(names)):
            line = names[name]
            findings.append(
                ReviewFinding(
                    kind=kind,
                    severity=severity,
                    quote=_pair_quote(line, None, prescription_text),
                    reason=f"{name} 属妊娠{section.get('severity') or ('禁用' if key == 'forbidden' else '慎用')}药",
                    rule=f"{RULE_VERSION}.pregnancy.{key}",
                    knowledge_base_refs=line.refs,
                    evidence=[
                        {
                            "ref": f"《中药学》p{section.get('page')}" if section.get("page") else "《中药学》",
                            "page": _as_int(section.get("page")),
                            "quote": str(section.get("quote") or ""),
                        }
                    ],
                )
            )
    return findings


def _pregnancy_blocked(findings: Iterable[ReviewFinding]) -> bool:
    return any(item.kind == "pregnancy_forbidden" for item in findings)


# --------------------------------------------------------------------------- #
# 对症：证候层（强）+ 功效层（弱）
# --------------------------------------------------------------------------- #


def _lexicon_entry(case_text: str) -> dict[str, Any] | None:
    """按词表顺序找第一个命中的条目。顺序即优先级，与旧实现一致。"""

    if not case_text:
        return None
    for entry in _lexicon().get("entries") or []:
        if any(str(term) in case_text for term in entry.get("terms") or []):
            return entry
    return None


def _match_symptoms(
    case_text: str,
    lines: Sequence[HerbLine],
    db_path: Path,
    syndrome_hint: Sequence[SyndromeCandidate] | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """返回 ``(是否对症, 状态, 追溯信息)``。

    证候候选**不再来自手写词表**，而是 ``app/kg/symptoms.py`` 从教材自动建的
    倒排索引（证候名 + 证候名的 2–4 字片段 + 299 条主治描述切分出的症状短语
    + 口语别名，共 2109 条）。
    ``syndrome_hint`` 用于把 LLM 闭集映射的结果注入进来（见 ``symptom_llm.py``）。

    判定顺序：

    1. **证候层**（强）：候选证候里，有没有哪味药在教材里明确主治它 → 有药证链边
    2. **功效层**（弱）：没覆盖到时，退一步看功效关键词
    3. 两层都不中 → 有候选就是 ``mismatched``（知道该治什么，但药不对），
       没候选才是 ``uncertain``（连该治什么都不知道）
    """

    trace: dict[str, Any] = {
        "layer": None,
        "syndromes": [],
        "candidates": [],
        "matched_herbs": [],
        "effect_tags": [],
    }
    candidates = (
        list(syndrome_hint)
        if syndrome_hint is not None
        else symptoms.candidates(case_text, top_k=5, db_path=db_path)
    )
    trace["candidates"] = [item.as_dict() for item in candidates]

    entry = _lexicon_entry(case_text)

    if not lines:
        # 一味药都没认出来：知道目标就是 mismatched，不知道就是 uncertain。
        return False, ("mismatched" if candidates or entry else "uncertain"), trace

    # ---- 证候层 ----
    if candidates:
        matched, edges, alternatives = _syndrome_coverage(
            lines, [item.name for item in candidates], db_path
        )
        trace["syndromes"] = [{"id": item.syndrome_id, "name": item.name} for item in candidates]
        trace["syndrome_alternatives"] = alternatives
        if matched:
            trace["layer"] = "syndrome"
            trace["matched_herbs"] = matched
            trace["edges"] = edges
            return True, "matched", trace

    # ---- 功效层 A（数据驱动）：和「候选证候的主治药」共享同一条功效 ----
    #
    # 这层取代了手写关键词的大部分工作：候选证候是「发热咽痛」，教材里主治它的是
    # 板蓝根（清热解毒、凉血、利咽）；处方里的金银花也是「清热解毒」→ 共享同一条
    # 功效项，判为对症。整条链路没有一个手写词。
    if candidates:
        expected = _expected_effects([item.name for item in candidates], db_path)
        shared: dict[str, list[str]] = {}
        for line in lines:
            if line.herb is None:
                continue
            common = sorted(set(line.herb.effects) & expected)
            if common:
                shared[line.name] = common
        trace["expected_effects"] = sorted(expected)
        if shared:
            trace["layer"] = "effect_overlap"
            trace["matched_herbs"] = list(shared)
            trace["shared_effects"] = shared
            return True, "matched", trace

    # ---- 功效层 B（手写兜底）：功效关键词 ----
    if entry is None:
        return False, ("mismatched" if candidates else "uncertain"), trace

    tags = tuple(str(tag) for tag in entry.get("effect_tags") or [])
    require_all = bool(entry.get("require_all_effect_tags"))
    matched_names = []
    for line in lines:
        if line.herb is not None and _herb_matches_target(line.herb, tags, require_all=require_all):
            matched_names.append(line.name)
    trace["layer"] = "effect_tag" if matched_names else None
    trace["effect_tags"] = list(tags)
    trace["matched_herbs"] = matched_names
    if matched_names:
        return True, "matched", trace
    return False, "mismatched", trace


def _expected_effects(candidate_names: Sequence[str], db_path: Path) -> set[str]:
    """候选证候在教材里由哪些药主治 → 这些药的功效集合。

    这是「教材自己给出的标准答案」：既然 板蓝根 主治 发热咽痛，那它靠的就是
    「清热解毒」。处方里别的药只要也具备这条功效，就是对症的——不需要人去写
    「咽痛 → 清热、解毒」这种映射。
    """

    ids = {syndrome_id(name) for name in candidate_names}
    if not ids:
        return set()
    herb_ids: set[str] = set()
    for edge in store.edges_touching(list(ids), tiers=(int(Tier.FACT),), db_path=db_path):
        if edge.relation_type != "药证链":
            continue
        other = edge.target_id if edge.source_id in ids else edge.source_id
        entity = store.get_entity(other, db_path)
        if entity is not None and entity.kind == "herb":
            herb_ids.add(entity.id)
    monograph = {herb.id: herb for herb in load_herbs()}
    effects: set[str] = set()
    for herb_id in herb_ids:
        herb = monograph.get(herb_id)
        if herb is not None:
            effects.update(herb.effects)
    return effects


def _syndrome_coverage(
    lines: Sequence[HerbLine], syndrome_names: Sequence[str], db_path: Path
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    """处方里哪些药有「主治该证」的教材条目。

    第三项返回**教材里真正主治该证的药**——即使用户没开，也告诉他该用哪味，
    这是「图谱作为服务」比关键词匹配多出来的东西。
    """

    ids = {syndrome_id(name) for name in syndrome_names}
    by_id = {line.entity_id: line for line in lines}
    matched: list[str] = []
    edges: list[dict[str, Any]] = []

    touching = store.edges_touching(
        [*by_id, *ids], tiers=(int(Tier.FACT),), db_path=db_path
    )
    for edge in touching:
        if edge.relation_type != "药证链":
            continue
        herb_id, syn_id = (
            (edge.source_id, edge.target_id)
            if edge.source_id in by_id
            else (edge.target_id, edge.source_id)
        )
        if herb_id not in by_id or syn_id not in ids:
            continue
        evidence = edge.evidence[0] if edge.evidence else {}
        line = by_id[herb_id]
        if line.name not in matched:
            matched.append(line.name)
        syndrome_entity = store.get_entity(syn_id, db_path)
        edges.append(
            {
                "edge_id": edge.id,
                "herb": line.name,
                "syndrome": syndrome_entity.name if syndrome_entity else syn_id,
                "relation_type": edge.relation_type,
                "page": _as_int(evidence.get("page")),
                # 知识库 JSON 没有页码，但有章节路径（第八章·第一节·麻黄）。
                # 页码等 P4 教材抽取补齐；现在至少能定位到节。
                "ref": str(evidence.get("ref") or ""),
                "quote": str(evidence.get("quote") or edge.label),
            }
        )

    alternatives: list[dict[str, Any]] = []
    for syn_id in ids:
        neighbours = store.edges_of(syn_id, tiers=(int(Tier.FACT),), db_path=db_path)
        names = []
        for edge in neighbours:
            if edge.relation_type != "药证链":
                continue
            other = edge.target_id if edge.source_id == syn_id else edge.source_id
            entity = store.get_entity(other, db_path)
            if entity is not None and entity.kind == "herb":
                names.append(entity.name)
        if names:
            entity = store.get_entity(syn_id, db_path)
            alternatives.append(
                {"syndrome": entity.name if entity else syn_id, "herbs": sorted(set(names))}
            )
    return matched, edges, alternatives


def _herb_matches_target(herb: Herb, tags: tuple[str, ...], *, require_all: bool = False) -> bool:
    """功效层：在**功效项**里找关键词。

    这是**弱证据**，只用来兜住词表覆盖不到的表述。命中不代表教材明文主治该证，
    所以 trace 里会标成 ``effect_tag``。

    **只搜功效，不搜主治说明。** 早期版本把主治的 ``syndrome + description``
    也拼进来一起搜，那时说明文字很短，没出事；教材抽全之后每条说明是一整段
    「治肺气虚弱，咳嗽无力，气短喘促，咳痰清稀」，症状词到处都是：

        病例「咳嗽痰多」+ 人参 → 命中「咳嗽」「痰」→ 判成对症

    人参治的是虚咳，痰湿咳嗽不是它的证。功效是**归过一的闭集**，说明是自由文本，
    拿自由文本当关键词库就是在做我们本来要取代的那种关键词匹配。宁可这一层
    漏判（退回 mismatched/uncertain），也不能把不对症说成对症。
    """

    text = " ".join(herb.effects)
    if require_all:
        return all(tag in text for tag in tags)
    return any(tag in text for tag in tags)


# --------------------------------------------------------------------------- #
# 剂量 / 毒性
# --------------------------------------------------------------------------- #


def _dose_in_range(herb: Herb, dose: float) -> bool:
    lower = herb.dosage.min_g
    upper = herb.dosage.max_g
    return (lower is None or dose >= lower) and (upper is None or dose <= upper)


#: 教材明写有毒的三种分级。``UNKNOWN`` 不在里面——没写不等于有毒。
_TOXIC_LEVELS = frozenset({Toxicity.MINOR, Toxicity.TOXIC, Toxicity.SEVERE})


def _is_toxic(herb: Herb) -> bool:
    """只有教材**明写**有毒才算有毒。

    ``Toxicity.UNKNOWN`` 必须排除在外。以前这里写的是
    ``str(herb.toxicity) not in {"无毒", "Toxicity.NONE"}``，于是「毒性未知」
    也被判成有毒，直接出一条 block —— 等于把「教材没提」说成「这方子不能开」。
    和「认不出药名不能判 block」是同一条原则：不知道不能伪装成有问题。
    """
    return herb.toxicity in _TOXIC_LEVELS


def _toxicity_unknown(herb: Herb) -> bool:
    return herb.toxicity == Toxicity.UNKNOWN


def _range_text(herb: Herb) -> str:
    """教材剂量区间的中文写法。区间缺失时如实说「教材未给区间」。"""
    lower, upper = herb.dosage.min_g, herb.dosage.max_g
    if lower is not None and upper is not None:
        return f"{lower:g}~{upper:g}g"
    if lower is not None:
        return f"≥{lower:g}g"
    if upper is not None:
        return f"≤{upper:g}g"
    return "教材未给区间"


def _dose_text(line: HerbLine) -> str:
    return f"处方写了 {line.dose_g:g}g" if line.dose_g is not None else "处方未写剂量"


def _herb_ref(line: HerbLine) -> str:
    """一味药的教材出处。``refs[0]`` 是 ``herb:xxx``，教材那条在它后面。"""
    for ref in line.refs:
        if ref.startswith("《"):
            return ref
    return f"《中药学》·{line.name}"


def _dose_evidence(line: HerbLine) -> list[dict[str, Any]]:
    """剂量类结论的出处：教材【用法用量】原文。

    没有它，「超范围」就只是一句我们自己的判断——学生无从核对 3g 到底是
    超了还是没超。``quote`` 直接取 ``dosage.text``（教材原文子串）。
    """
    if line.herb is None or not line.herb.dosage.text:
        return []
    return [{"ref": _herb_ref(line), "page": None, "quote": line.herb.dosage.text}]


def _toxicity_evidence(line: HerbLine) -> list[dict[str, Any]]:
    """毒性类结论的出处：教材【用法用量】+【使用注意】原文。

    两条分开给，不拼成一句话——拼出来的就不是原文子串了，没法核对。
    """
    herb = line.herb
    if herb is None:
        return []
    evidence: list[dict[str, Any]] = []
    if herb.dosage.text:
        evidence.append({"ref": _herb_ref(line), "page": None, "quote": herb.dosage.text})
    for caution in herb.cautions[:1]:
        if caution:
            evidence.append({"ref": _herb_ref(line), "page": None, "quote": caution})
    return evidence


# --------------------------------------------------------------------------- #
# 数据加载与文本工具
# --------------------------------------------------------------------------- #


def _curated() -> dict[str, Any]:
    path = backend_root() / CURATED_DIR / "incompatibility.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _lexicon() -> dict[str, Any]:
    """功效层词表（``effect_tags``）。

    这里**不再有** ``syndromes`` 字段——「话 → 证」已经交给
    ``app/kg/symptoms.py`` 从教材自动生成的倒排索引，手写清单只保留
    机器学不出来的口语别名（``symptom_aliases.json``）。
    """

    path = backend_root() / CURATED_DIR / "symptom_lexicon.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def reset_caches() -> None:
    """知识库或词表变更后清缓存。"""

    _HERB_INDEX.clear()
    symptoms.reset_cache()


def _pair_quote(left: HerbLine, right: HerbLine | None, text: str) -> str:
    """冲突项的引用片段：尽量取覆盖两味药的那一段原文，取不到就退回单味。"""

    if right is None:
        return left.quote or left.raw_name

    left_at = text.find(left.raw_name)
    right_at = text.find(right.raw_name)
    if left_at < 0 or right_at < 0:
        return left.quote or left.raw_name

    start = min(left_at, right_at)
    # 用每味药**自己**的长度算终点。以前取的是两者长度的最大值，
    # 名字不等长时会把后面那味药截掉半截。
    end = max(left_at + len(left.raw_name), right_at + len(right.raw_name))
    # 顺手把紧跟的剂量框进来：「人参9g，藜芦」看着像被切断了。
    tail = re.match(r"\s*\d{1,3}(?:\.\d+)?\s*(?:g|克)", text[end:])
    if tail:
        end += tail.end()
    span = text[start:end].strip()
    return span or left.quote or left.raw_name


def _dose_after_name(name: str, text: str) -> float | None:
    match = re.search(
        re.escape(name) + r"(?:[（(][^）)]*[）)])?\s*(\d{1,3}(?:\.\d+)?)\s*(?:g|克)",
        text,
        flags=re.I,
    )
    return _as_float(match.group(1)) if match else None


def _quote_for_name(name: str, text: str) -> str:
    match = re.search(re.escape(name) + r"[^,;，；。\n]*", text)
    return match.group(0).strip() if match else name


def _preparation(raw_name: str) -> str | None:
    match = re.search(r"[（(]([^）)]+)[）)]", raw_name)
    if match:
        return match.group(1).strip()
    prefix = re.match(_PREPARATION_PREFIX, raw_name)
    return prefix.group(0) if prefix and prefix.group(0) else None


def _raw_name_with_preparation(name: str, quote: str) -> str:
    match = re.search(_PREPARATION_PREFIX + re.escape(name), quote)
    return match.group(0) if match else name


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = [
    "HerbLine",
    "ReviewFinding",
    "ReviewResult",
    "RULE_VERSION",
    "review",
    "reset_caches",
]
