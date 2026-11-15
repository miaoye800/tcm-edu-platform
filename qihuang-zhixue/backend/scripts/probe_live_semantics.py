"""真实模型语义抽样探针（手工运行，不进 pytest）。

目的：用尽可能少的调用，检查几种典型用户说法在真实模型下的抽取结果，
与我们预设的规则（组件量表、原话可追溯校验、证据不重复出证）是否一致。

调用预算：剧情批量抽取 3 次 + 对话式单轮抽取 4 次 + 评分 1 次 = 8 次。

运行：
    ./.venv/Scripts/python.exe scripts/probe_live_semantics.py

结果落在 ``.tmpverify/live-probe-*.json``，控制台同时打印摘要。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# 真模型、真失败可见：live 模式跳过回放缓存；关掉 mock 兜底，避免
# "看着有 8 条证据，其实全是模板文本"这种假通过。
# ``--mock`` 只用于脚本自检：确认解析/打印路径不崩，不花额度。
_MOCK = "--mock" in sys.argv
os.environ["LLM_MODE"] = "mock" if _MOCK else "live"
os.environ["QIHUANG_DB_PATH"] = str(BACKEND / ".tmpverify" / "live-probe.db")
os.environ["LLM_FALLBACK_TO_MOCK"] = "false"
os.environ["LLM_TIMEOUT_SECONDS"] = "120"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.llm.router import ModelRouter  # noqa: E402
from app.profile.modes import (  # noqa: E402
    STORY_EXTRACTION_BATCHES,
    _story_extract_batch,
    _story_extraction_messages,
    _try_llm_dialogue_turn,
)

CALLS: list[dict] = []
_orig_chat = ModelRouter.chat
_CALL_LOG = BACKEND / ".tmpverify" / f"live-probe-calls-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"


async def _recording_chat(self, task_type, messages, **kw):  # noqa: ANN001, ANN003
    started = time.perf_counter()
    text = await _orig_chat(self, task_type, messages, **kw)
    CALLS.append(
        {
            "task": task_type,
            "seconds": round(time.perf_counter() - started, 2),
            "chars": len(text or ""),
            "raw": text,
        }
    )
    # 每次调用立刻落盘：脚本后面崩了，这笔额度也不算白花。
    _CALL_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _CALL_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"task": task_type, "raw": text}, ensure_ascii=False) + "\n")
    return text


ModelRouter.chat = _recording_chat  # type: ignore[assignment]


# 一份像真人答法的剧情回顾（五幕），刻意混合了具体与含糊、明确与跑题。
STORY_TRANSCRIPT = "\n".join(
    [
        "【第一幕】用户：我大三，中医学专业，方剂学刚学完一遍，但是背不太住（choice，normal）",
        "【第二幕】用户：胸胁苦满、口苦咽干，我第一反应是小柴胡汤，不过剂量记不准（choice，normal）",
        "【第三幕】用户：麻黄和桂枝我老是混，考试基本靠背口诀（choice，slow）",
        "【第四幕】用户：我一般晚上在自习室看书，B站上也会刷一些方剂讲解视频（text，normal）",
        "【第五幕】用户：我想两年后考中医内科的研究生，以后能自己看病（text，normal）",
        "【第一幕】用户：这个我不太清楚（choice，fast）",
    ]
)

# 对话式四句：分别考"薄弱点自述""学习偏好""长期目标""价值取向"。
# 第四句正是系统提示词里举的例子，用来验证模型会不会越权推断。
DIALOGUE_CASES = [
    {
        "id": "weak_point",
        "target": "weak_points",
        "utterance": "我基础还行吧，就是方剂背不住，考试老在这上面丢分",
        "expect": "应出一条 weak_points 证据，且不得顺手编学习风格/资源偏好",
    },
    {
        "id": "preference",
        "target": "learning_style",
        "utterance": "B站上看视频多，老师讲得快我就跟不上",
        "expect": "应落 learning_style 或 resource_preference；quote 必须含 B站",
    },
    {
        "id": "long_goal",
        "target": "learning_goal",
        "utterance": "两年后想考中医内科的研究生，以后能自己看病",
        "expect": "learning_goal 应为考研/深造方向，不得推断每天学习时长",
    },
    {
        "id": "value",
        "target": "motivation",
        "utterance": "我想治病救人",
        "expect": "最多出目标表达 + 内驱动机线索；不得推断行动计划/资格/优先级",
    },
    {
        # 用户实测报的第四轮：先在学习方式上取舍（看视频 > 动手），再给出
        # 就业层面的目标取舍（考公比临床"好一点"）。这两句都带明确比较，
        # 应当分别落 learning_style/comparative_choice 与
        # learning_goal/priority_signal；漏了就是"收集到考公却漏掉优先级"。
        "id": "priority_tradeoff",
        "target": "learning_goal",
        "utterance": (
            "看视频学理论，动手是实践，我觉得都很重要，非要说的话，看视频可能多一点，"
            "但是我其实有点想考公务员，因为现在就业环境不太好，考公会好一点"
        ),
        "history": (
            "我: 我学得很认真的，每天都花俩小时以上，已经坚持了一年半，"
            "经常就是看B站里的学习视频，有时候会去实验室配中药实战\n"
            "小岐: 一年半每天两小时还去实验室实战，这份认真真难得。"
            "要是哪天时间紧，看视频学和动手配药只能保一个，你先放哪个？"
        ),
        "expect": "应出现 comparative_choice 或 priority_signal，quote 含“考公”或“只能保一个”",
    },
]


def _card_view(card) -> dict:
    signal = card.objective_signal or {}
    return {
        "dimension": str(card.dimension),
        "component": card.component,
        "quote": card.quote or "",
        "claim": card.normalized_claim or "",
        "evidence_type": str(signal.get("evidence_type") or ""),
        "quality": dict(card.quality_factors or {}),
        "quality_reason": card.quality_reason or "",
        "group": card.evidence_group_id or "",
    }


async def probe_story(indices: list[int] | None = None) -> dict:
    picks = [
        (index, batch)
        for index, batch in enumerate(STORY_EXTRACTION_BATCHES)
        if indices is None or index in indices
    ]
    print(f"\n=== 剧情批量抽取（{len(picks)} 批）===")
    results = await asyncio.gather(
        *(
            _story_extract_batch(None, dimensions, limit, transcript=STORY_TRANSCRIPT)  # type: ignore[arg-type]
            for _, (dimensions, limit) in picks
        )
    )
    out = []
    for (_, (dimensions, limit)), cards in zip(picks, results):
        print(f"\n[{'+'.join(dimensions)}] 收入 {len(cards)} 条 / 上限 {limit}")
        for card in cards:
            view = _card_view(card)
            print(
                f"  - {view['dimension']}/{view['component']} "
                f"《{view['quote']}》→ {view['claim']}"
            )
        out.append(
            {
                "dimensions": list(dimensions),
                "limit": limit,
                "kept": len(cards),
                "cards": [_card_view(card) for card in cards],
            }
        )
    return {"transcript": STORY_TRANSCRIPT, "batches": out, "cards": [c for r in results for c in r]}


async def probe_dialogue(ids: list[str] | None = None) -> dict:
    cases = [case for case in DIALOGUE_CASES if ids is None or case["id"] in ids]
    print(f"\n=== 对话式单轮抽取（{len(cases)} 句）===")
    out = []
    for case in cases:
        before = len(CALLS)
        extraction, note = await _try_llm_dialogue_turn(
            case["utterance"],
            case["target"],
            1,
            [],
            {},
            dialogue_history=case.get("history") or case["utterance"],
        )
        raw_call = CALLS[before] if len(CALLS) > before else {}
        atoms = list(extraction.atomic_evidence or [])
        scans = [
            {
                "dimension": str(item.dimension),
                "status": item.status,
                "reason": item.reason,
                "components": list(item.components or []),
            }
            for item in (extraction.dimension_scan or [])
        ]
        legacy = {}
        for key in ("learning_goal", "weak_points", "learning_style", "motivation", "base_level", "resource_preference"):
            signal = getattr(extraction, key, None)
            value = getattr(signal, "value", None) if signal is not None else None
            if value not in (None, "", [], {}):
                legacy[key] = {
                    "value": value,
                    "evidence": getattr(signal, "evidence", None),
                    "confidence": getattr(signal, "confidence", None),
                    "card_names": list(getattr(signal, "card_names", None) or []),
                }
        print(f"\n[{case['id']}] {case['utterance']}")
        print(f"  期望：{case['expect']}")
        print(f"  方法={extraction.extraction_method} 令牌长度={raw_call.get('chars')} 耗时={raw_call.get('seconds')}s note={note or '-'}")
        if extraction.extraction_error:
            print(f"  错误：{extraction.extraction_error}")
        print(f"  原子证据 {len(atoms)} 条：")
        for atom in atoms:
            print(f"    · {atom.dimension}/{atom.component} 《{atom.quote}》→ {atom.normalized_claim}")
        if legacy:
            print(f"  legacy 字段：{json.dumps(legacy, ensure_ascii=False)}")
        print("  维度扫描：" + "；".join(f"{s['dimension']}={s['status']}" for s in scans))
        out.append(
            {
                "case": case,
                "chars": raw_call.get("chars"),
                "seconds": raw_call.get("seconds"),
                "method": extraction.extraction_method,
                "error": extraction.extraction_error,
                "atomic": [
                    {
                        "dimension": str(a.dimension),
                        "component": a.component,
                        "quote": a.quote,
                        "claim": a.normalized_claim,
                        "status": a.collection_status,
                        "relation": a.semantic_relation,
                        "assessment": a.assessment,
                        "quality": dict(a.quality_factors or {}),
                        "quality_reason": a.quality_reason,
                    }
                    for a in atoms
                ],
                "legacy": legacy,
                "scan": scans,
                "raw": raw_call.get("raw"),
            }
        )
    return {"cases": out}


async def probe_scoring(story_cards: list) -> dict:
    from app.profile.jury import score_dimension

    candidates = [card for card in story_cards if str(card.dimension) == "learning_goal"]
    if not candidates:
        print("\n=== 评分类跳过：剧情抽取没有产出 learning_goal 证据 ===")
        return {"skipped": "no_learning_goal_cards"}
    print(f"\n=== 单维评分（learning_goal / {len(candidates)} 条证据）===")
    result = await score_dimension("learning_goal", story_cards, force_llm=True)
    payload = {
        "component_scores": dict(result.component_scores or {}),
        "label": result.label,
        "radar_score": result.radar_score,
        "confidence": result.confidence,
        "coverage": result.coverage,
        "warnings": [w.model_dump() if hasattr(w, "model_dump") else str(w) for w in (result.warnings or [])],
        "narrative": result.narrative,
        "method": result.method,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:2000])
    return payload


def _arg_values(name: str) -> list[str]:
    """``--story=0,1`` / ``--dialogue=preference`` 这种限定，用来只补跑一部分。"""

    for arg in sys.argv:
        if arg.startswith(f"--{name}="):
            return [value for value in arg.split("=", 1)[1].split(",") if value]
    return []


async def main() -> None:
    started = time.perf_counter()
    story_ids = _arg_values("story")
    dialogue_ids = _arg_values("dialogue")
    story = (
        await probe_story([int(value) for value in story_ids] if story_ids else None)
        if "--no-story" not in sys.argv
        else {"transcript": STORY_TRANSCRIPT, "batches": [], "cards": []}
    )
    dialogue = (
        await probe_dialogue(dialogue_ids or None)
        if "--no-dialogue" not in sys.argv
        else {"cases": []}
    )
    scoring = {} if "--no-scoring" in sys.argv else await probe_scoring(story["cards"])

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "calls": [{"task": c["task"], "seconds": c["seconds"], "chars": c["chars"]} for c in CALLS],
        "story": {
            "transcript": story["transcript"],
            "batches": story["batches"],
        },
        "dialogue": dialogue,
        "scoring": scoring,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
    }
    out_dir = BACKEND / ".tmpverify"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"live-probe-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 原始响应单独存一份，方便回看模型到底写了什么。
    raw_path = path.with_suffix(".raw.json")
    raw_path.write_text(
        json.dumps([{"task": c["task"], "raw": c["raw"]} for c in CALLS], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n调用 {len(CALLS)} 次，用时 {report['elapsed_seconds']}s")
    print(f"报告：{path}")
    print(f"原始：{raw_path}")


if __name__ == "__main__":
    asyncio.run(main())
