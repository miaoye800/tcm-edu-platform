"""Demo for profile scoring foundation."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.llm.clients import DeepSeekClient
from app.profile.jury import build_jury_messages, score_dimension
from app.profile.schema import EvidenceCard

SHANGHAI_TZ = timezone(timedelta(hours=8))


async def _main(strict_llm: bool = False) -> None:
    cards = [
        EvidenceCard(
            dimension="knowledge_base",
            source="辨药",
            fact="辨药第1题答对，难度高",
            objective_signal={"correct": True, "difficulty": "hard"},
            weight=1.2,
            timestamp=datetime.now(SHANGHAI_TZ),
        ),
        EvidenceCard(
            dimension="knowledge_base",
            source="抓药实战",
            fact="桂枝配伍正确；清热药剂量偏低",
            objective_signal={"对症": True, "配伍": True, "剂量": False, "误用毒烈": False},
            weight=1.5,
            timestamp=datetime.now(SHANGHAI_TZ),
        ),
        EvidenceCard(
            dimension="pace",
            source="四幕·立下学规",
            fact="约定每日45分钟，14天完成阶段目标",
            objective_signal={"daily_minutes": 45, "target_days": 14, "accept_challenge": True},
            weight=1.0,
            timestamp=datetime.now(SHANGHAI_TZ),
        ),
    ]
    if strict_llm:
        await _strict_llm_probe(cards)
        return

    result = await score_dimension("knowledge_base", cards, settings=get_settings())
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


async def _strict_llm_probe(cards: list[EvidenceCard]) -> None:
    settings = get_settings()
    if not (settings.deepseek_api_key or "").strip():
        print("DeepSeek strict check: DEEPSEEK_API_KEY 未读取到，当前会走 MOCK/规则融合。")
        return

    messages = build_jury_messages("knowledge_base", cards)
    client = DeepSeekClient(
        model_name=settings.deepseek_model_pro,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    print(
        json.dumps(
            {
                "base_url": settings.deepseek_base_url,
                "model": settings.deepseek_model_pro,
                "thinking": {"type": "enabled"},
                "reasoning_effort": "high",
                "has_key": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    try:
        raw = await client.chat(
            messages,
            temperature=0,
            thinking={"type": "enabled"},
            reasoning_effort="high",
        )
    except Exception as exc:
        print(f"DeepSeek strict check failed: {exc}")
        return

    print("DeepSeek strict check succeeded. Raw response preview:")
    print(raw[:1200])


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Run profile scoring demo.")
    parser.add_argument("--strict-llm", action="store_true", help="Probe real DeepSeek jury call without MOCK fallback.")
    args = parser.parse_args()
    asyncio.run(_main(strict_llm=args.strict_llm))


if __name__ == "__main__":
    main()
