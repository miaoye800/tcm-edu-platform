"""L4：让模型做**闭集选择**，把自由表述映射到教材证候。

为什么必须有这一层：患者说「一咽口水就疼，还有点烧」，教材里没有任何一个词
和这句话字面重合。倒排索引和字符重叠都救不了，只有模型能理解。

为什么必须是**闭集**：

1. 模型不给自由生成的机会——只能从候选清单里挑编号，或回答「都不合适」。
   返回的编号会被校验，不在清单里的一律丢弃。
2. 候选清单来自图谱里的单证名（当前 385 个），不是模型自己想出来的词。
   教材的「主治清单」条目不算证候，不进清单（见 ``symptoms.is_list_entry``）。
   这样「映射结果」永远是图谱里的一个真实节点，能继续沿图查下去。
3. 模型不给理由、不给解释，只给编号和置信度——**理由由图谱提供**，
   来自教材原文。模型的话一句都不进最终答案。

没有 API key 时直接返回空，不做任何猜测。``method`` 会标明这次有没有用模型。
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from app.kg.entities import syndrome_id
from app.kg.schema import ensure_schema
from app.kg.symptoms import SyndromeCandidate, known_syndromes
from app.profile.store import DB_PATH, connect

#: 交给模型的候选上限。
#:
#: 曾经是 40，结果是个隐蔽的召回灾难：224 个证候按名字排序后取前 40，
#: 「风寒表实证」「热毒证」「气虚证」「脾胃虚寒证」全都不在里面——
#: 模型再聪明也选不到清单上没有的东西，等于闭集被截成了残集。
#:
#: 算一下账就清楚该全量给：证候名平均 5 个字，**全部 385 个单证名合计约
#: 1900 字（≈2.5k prompt token）**，单次成本在厘的量级；而漏掉一个证候的
#: 代价是整条审查链失效。所以上限设成「够装下当前全部证候」，并且**只在真的
#: 超限时才截断**——截断时按与原文的字面相关度排序，而不是按拼音/笔画顺序瞎砍。
#:
#: 这个数不是拍的：300 是上一版（224 个证候）留下的，教材证名全量入库后
#: 立刻被 385 顶穿，测试 ``test_llm_candidate_pool_is_the_whole_closed_set``
#: 当场报「截断到 300」——这正是它存在的意义。留到 600 是给教材扩充的余量。
MAX_SHORTLIST = 600

_SYSTEM = """你是中医症状术语归一化器。任务：把患者的口语描述映射到**给定清单里**的证候编号。

规则：
1. 只能从用户给出的编号清单里选，不许自创、不许改写清单里的名字。
2. 最多选 3 个，按可能性从高到低。
3. 如果清单里没有合适的，返回空数组。**不要勉强匹配**。
4. 只输出 JSON，不要解释。

输出格式：{"picks": [{"index": 3, "confidence": 0.8}]}"""


@dataclass(frozen=True)
class LlmPick:
    index: int
    confidence: float


def available() -> bool:
    """有没有可用的真实模型。没有就完全跳过这一层。"""

    try:
        from app.llm.clients import MockClient
        from app.llm.router import ModelRouter

        return not isinstance(ModelRouter().select("generate"), MockClient)
    except Exception:
        return False


def _cache_key(text: str) -> str:
    """归一化后取哈希。标点和空白不影响语义，不该导致重复计费。"""

    compact = re.sub(r"[\s，。；、,.;:：/（）()《》<>【】\[\]\"'“”‘’]+", "", text or "")
    return hashlib.sha1(compact.encode("utf-8")).hexdigest()


def _cache_get(text: str, db_path: Path) -> list[SyndromeCandidate] | None:
    ensure_schema(db_path)
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT picks_json FROM kg_mapping_cache WHERE text_hash=?",
            (_cache_key(text),),
        ).fetchone()
    if not row:
        return None
    try:
        items = json.loads(row[0])
    except json.JSONDecodeError:
        return None
    result: list[SyndromeCandidate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        result.append(
            SyndromeCandidate(
                syndrome_id=str(item.get("id") or ""),
                name=str(item.get("name") or ""),
                score=float(item.get("score") or 0.0),
                layer="llm",
                matched_terms=[],
            )
        )
    return result


def _cache_put(text: str, picks: Sequence[SyndromeCandidate], model: str, db_path: Path) -> None:
    ensure_schema(db_path)
    payload = json.dumps(
        [{"id": item.syndrome_id, "name": item.name, "score": item.score} for item in picks],
        ensure_ascii=False,
    )
    with closing(connect(db_path)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO kg_mapping_cache(text_hash, text, picks_json, model, created_at) "
            "VALUES (?,?,?,?,?)",
            (
                _cache_key(text),
                text,
                payload,
                model,
                datetime.now(UTC).isoformat(),
            ),
        )
        conn.commit()


async def map_case_to_syndromes(
    case_text: str,
    *,
    shortlist: Sequence[str] | None = None,
    db_path: Path = DB_PATH,
    max_picks: int = 3,
    use_cache: bool = True,
) -> list[SyndromeCandidate]:
    """把病例文本映射到候选证候。失败、无 key、返回非法编号时一律返回空。

    缓存优先：命中就直接返回，**不产生任何模型调用**。教学场景里同一道病例会被
    反复提交，这一步能把额度消耗从「每次一问」压到「每个病例一问」。
    """

    text = (case_text or "").strip()
    if not text:
        return []

    if use_cache:
        cached = _cache_get(text, db_path)
        if cached is not None:
            return cached

    pool = list(shortlist) if shortlist else known_syndromes(db_path)
    if not pool:
        return []
    pool = _narrow(pool, text)

    if not available():
        return []

    listing = "\n".join(f"{index}. {name}" for index, name in enumerate(pool, 1))
    prompt = f"候选证候清单：\n{listing}\n\n患者描述：{text}\n\n请输出 JSON。"

    try:
        from app.llm.router import ModelRouter

        router = ModelRouter()
        raw = await router.chat(
            "generate",
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        model = getattr(getattr(router, "settings", None), "deepseek_model_flash", "") or ""
    except Exception:
        # 模型挂了不该让处方审查跟着挂——这一层本来就是可选的。
        return []

    picks = _parse_picks(raw)
    results: list[SyndromeCandidate] = []
    for pick in picks[:max_picks]:
        # 1-based 编号 → 清单下标。越界即丢弃，不做「猜他想选哪个」。
        if not 1 <= pick.index <= len(pool):
            continue
        name = pool[pick.index - 1]
        results.append(
            SyndromeCandidate(
                syndrome_id=syndrome_id(name),
                name=name,
                score=round(max(0.0, min(pick.confidence, 1.0)), 4),
                layer="llm",
                matched_terms=[],
            )
        )

    # 空结果也缓存：否则「确实没有合适证候」的病例每次都会重新问一遍。
    if use_cache:
        _cache_put(text, results, model, db_path)
    return results


def _narrow(pool: list[str], text: str) -> list[str]:
    """候选池超限时才收窄，且收窄方式必须和原文有关。

    当前 385 个单证名，离 ``MAX_SHORTLIST`` 还有余量，不会触发。这段是给
    「教材继续扩充到上千条」准备的——那时候 ``pool[:N]`` 就是纯随机丢弃，
    而按字面相关度排序至少能保住和原文沾边的那些。
    """

    if len(pool) <= MAX_SHORTLIST:
        return pool
    target = _bigrams(text)
    scored = sorted(
        pool,
        key=lambda name: (-len(target & _bigrams(name)), name),
    )
    return scored[:MAX_SHORTLIST]


def _bigrams(text: str) -> set[str]:
    compact = re.sub(r"[\s，。；、,.;:：/（）()《》<>【】\[\]\"'“”‘’]+", "", text or "")
    return {compact[i : i + 2] for i in range(len(compact) - 1)}


def _parse_picks(raw: str) -> list[LlmPick]:
    payload: Any
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw or "", flags=re.S)
        if not match:
            return []
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    if not isinstance(payload, dict):
        return []
    items = payload.get("picks")
    if not isinstance(items, list):
        return []

    picks: list[LlmPick] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        confidence = item.get("confidence", 0.5)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.5
        picks.append(LlmPick(index=index, confidence=confidence))
    picks.sort(key=lambda pick: -pick.confidence)
    return picks


__all__ = ["LlmPick", "available", "map_case_to_syndromes"]
