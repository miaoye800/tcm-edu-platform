"""素材工具循环：**模型自己搜、自己看、自己决定用哪张**。

这是整套「美术资源」的核心机制。它替代的是「按关键词派活」那条老路：老路里
代码决定哪一页配什么图，于是不管讲什么课，出来的都是同一批图。

## 循环长什么样

```
system   你是这份讲稿的美术指导。工具清单、可用渠道、判定标准见下。
user     共 N 页，每页要什么你自己判断；找不到可以不配图。
   ↕     最多 12 轮 / 90 秒 / 生图 3 张
① search_assets(source, query, kind, limit) → 最多 8 条候选
② look_at(url, question)                   → 看一眼，判断主体对不对
③ assign_asset(unit_id, asset_id, why)     → 钉到某页
④ generate_image(unit_id, prompt)          → 实在没有就自己画
⑤ finish()                                 → 收工
```

允许的真实行为：搜到 8 条 → 看其中 2 条 → 判定「模糊残片」不合格 → 换英文词再搜
→ 仍不合格 → 生成 → 绑定。**这不是把「选图」写成结构化输出，是让模型真的动手**。

## 三条边界（写在代码里，不靠提示词自觉）

1. **证据位只能用 `EVIDENCE_CHANNELS`**。模型给证据位绑一张 Heritage 版画会被直接拒绝，
   并把拒绝原因回给它——让它在下一轮改，而不是静静地产出一张「看起来对」的错图。
2. **一轮失败不影响整份稿子**。任何工具异常都变成 ``{"ok": false, ...}`` 回给模型；
   循环整体失败则返回空结果，由 `select` 兜底。
3. **结果冻结**。绑定结果连同 url / 许可 / 理由一起写进稿件，此后刷新、重连、导出
   都不再重跑——模型的不确定性只存在于这一次循环里。
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..llm.router import ModelRouter
from . import channels, materials
from .design import DeckDesign
from .providers import wanx

logger = logging.getLogger(__name__)

#: 轮次上限。每轮可以含多个工具调用，所以 12 轮已经够「搜-看-再搜-生成-绑定」跑两遍。
MAX_ROUNDS = 12
#: 墙钟上限。超时必须带着已有结果返回，不能把生成流程挂在这里。
MAX_SECONDS = 90.0
#: 生图预算。一份稿子最多三张，且只能进装饰位。
GEN_BUDGET = 3
#: 单页最多引用几张。
PER_PAGE = 2
#: 单次检索返回几条。8 条是「够挑」与「不把上下文灌满」的折中。
SEARCH_LIMIT = 8
#: 喂给视觉模型的图最长边。太大白烧 token，太小看不清纹样。
VISION_MAX_EDGE = 768

#: 页面用途 → 人话。写进 prompt，比让模型去猜 `archetype` 好。
ARCHETYPE_LABEL = {
    "cover": "封面",
    "agenda": "目录",
    "section": "章节页",
    "points": "要点页",
    "compare": "对照页",
    "matrix": "矩阵页",
    "process": "流程图",
    "data": "数据页",
    "warning": "警示页",
    "summary": "收束页",
}


@dataclass(slots=True)
class Assignment:
    """一次绑定：某页的某个槽位用哪张素材。"""

    unit_id: str
    slot: str
    candidate: materials.Candidate
    why: str = ""

    def as_resolution(self) -> dict[str, Any]:
        """转成 `SlotResolution` 的形状，好让 `draft` 那条链路不用改。"""

        candidate = self.candidate
        return {
            "unit_id": self.unit_id,
            "slot": self.slot,
            "requested": {"subject": candidate.title, "style": candidate.kind},
            "result": "generated" if candidate.source == "gen.image" else "hit",
            "asset_id": candidate.id,
            "provenance": "generated" if candidate.source == "gen.image" else "library",
            "score": 100,
            "license": candidate.license,
            "attribution": candidate.attribution,
            "file": "",
            "url": candidate.url,
            "thumb": candidate.thumb,
            "source": candidate.source,
            "pinned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "fallback_reason": self.why[:60],
        }


@dataclass(slots=True)
class LoopResult:
    """循环的产出。``rounds`` / ``calls`` / ``notes`` 都进 meta，便于事后判断质量。"""

    assignments: list[Assignment] = field(default_factory=list)
    rounds: int = 0
    calls: int = 0
    generated: int = 0
    notes: list[str] = field(default_factory=list)
    ran: bool = False

    def resolutions(self) -> list[dict[str, Any]]:
        return [item.as_resolution() for item in self.assignments]


# ── 工具定义 ────────────────────────────────────────────────────────────────

#: 给模型的工具清单。**每个 source 的专长与许可都写在描述里**——模型读到的是这段
#: 说明，不是一串网址。不给说明，它只会一直用 `auto`，等于没有选择。
TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_assets",
            "description": (
                "检索素材。source 决定去哪儿找：\n"
                "- `local`：本地已审素材库（48 条）。五味饮片实拍 + 复古博物画。零延迟，先查它。\n"
                "- `api.wellcome`：Wellcome 医学史馆藏。《本草品汇精要》明抄彩绘、《本草纲目》金陵本、"
                "针灸图。**唯一能给「药材形态证据」的远程源**，许可是 pdm(等效 CC0) 与 CC-BY，"
                "少数 CC-BY-NC 只能当装饰。\n"
                "- `api.cleveland`：克利夫兰美术馆，CC0，免 key。中国水墨绢本 951 件、陶瓷器物、版画。"
                "强在**氛围与器物**，搜具体药材基本没有。\n"
                "- `api.iconify`：图标（微软 Fluent 优先）。返回 currentColor SVG，会跟着文字色变。"
                "图标只能当符号，**不能占半版**。\n"
                "- `api.phylopic`：生物单色剪影，适合章节页的极简标志。\n"
                "- `web.pngimg`：透明底实物抠图，CC BY-NC 4.0（仅演示期可用，必须署名）。"
                "**没有中草药类目**，只能按分类页取（nature/flowers/objects/tableware/vegetables/food）。\n"
                "- `auto`：由代码按 kind 路由，本地优先。不知道去哪就填它。\n"
                "query 写中文即可，工具层会自动译成英文。搜不到**换词再搜**，别放弃得太早。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "enum": [
                        "auto", "local", "api.wellcome", "api.cleveland", "api.iconify",
                        "api.phylopic", "web.pngimg",
                    ]},
                    "query": {"type": "string", "description": "检索词，中文即可。如「黄芪」「水墨山水」「针灸」。"},
                    "kind": {
                        "type": "string",
                        "enum": ["herb", "object", "scene", "plate", "pattern", "texture", "icon", "silhouette", "photo", "hand-drawn"],
                        "description": "要什么类型。herb=药材形态，scene=山水场景，object=器物，icon=图标。",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 12},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "look_at",
            "description": (
                "看一眼候选图。返回画面描述、是否切题、是否清晰。\n"
                "**必须看图再绑定**：馆藏检索按相关度排序，排第一的经常是模糊残片或只有装裱绢边的"
                "空页。只挑最像的两三张看，不要逐张看（省时间）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_id": {"type": "string", "description": "候选的 id（来自 search_assets）。"},
                    "question": {"type": "string", "description": "你想确认什么，如「这是黄芪的根还是叶？」"},
                },
                "required": ["asset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assign_asset",
            "description": (
                "把一张素材钉到某页的某个槽位。\n"
                "- `slot=\"evidence\"`：用于「这就是某味药」的实证画面，**只能是 local 或 api.wellcome 的"
                "免 NC 条目**，否则会被拒绝。\n"
                "- `slot=\"figure\"`：装饰配图，任何渠道都行。\n"
                "一页最多两张。宁可不配，也不要拿相近的东西凑——错图比无图伤害大。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "unit_id": {"type": "string", "description": "页码标识，见任务清单。"},
                    "asset_id": {"type": "string"},
                    "slot": {"type": "string", "enum": ["figure", "evidence"], "default": "figure"},
                    "why": {"type": "string", "description": "一句话理由，会写进稿件给人看。"},
                },
                "required": ["unit_id", "asset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": (
                f"文生图兜底。每份稿最多 {GEN_BUDGET} 张，**只能进装饰位**，不能当药材证据。\n"
                "prompt 写中文，说清主体、画法、底色。例如「黄芪饮片，米白宣纸背景，淡墨线描，无文字」。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "unit_id": {"type": "string"},
                    "prompt": {"type": "string"},
                    "size": {"type": "string", "enum": ["square", "portrait", "landscape"], "default": "square"},
                },
                "required": ["unit_id", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "收工。不配图的页面就留空，渲染层会走无图版式，那比配错图好。",
            "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}},
        },
    },
]


# ── 任务描述 ────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class PageAsk:
    """一页的需求。只写「这页要什么」，不写「用哪张」。"""

    unit_id: str
    index: int
    archetype: str
    subject: str
    slot: str
    slot_class: str
    style: str
    priority: str

    def describe(self) -> str:
        label = ARCHETYPE_LABEL.get(self.archetype, self.archetype)
        kind = "证据图（这就是它本来的样子）" if self.slot_class == "evidence" else "装饰配图"
        must = "必须" if self.priority == "required" else "可选"
        style = f"，风格偏 {self.style}" if self.style else ""
        return (
            f"- unit_id=`{self.unit_id}`（第 {self.index + 1} 页 · {label}）"
            f"需要 {kind}：主体「{self.subject}」{style}（{must}）"
        )


def asks_of(design: DeckDesign) -> list[PageAsk]:
    """把设计单的槽位摊平成任务清单。

    没有槽位的页面不出现——**「这页不需要图」本身就是一个决定**，不该塞进清单让模型
    再判断一次。封面例外：封面通常需要一个主视觉，设计单若没给槽位就不提。
    """

    out: list[PageAsk] = []
    for index, page in enumerate(design.pages):
        for slot in page.slots:
            if slot.slot not in {"figure", "evidence"}:
                continue
            subject = (slot.subject or "").strip() or "通用装饰"
            out.append(
                PageAsk(
                    unit_id=page.unit_id or f"p{index + 1}",
                    index=index,
                    archetype=page.archetype,
                    subject=subject,
                    slot="evidence" if slot.slot == "evidence" else "figure",
                    slot_class="evidence" if slot.slot_class == "evidence" or slot.slot == "evidence" else "decorative",
                    style=str(slot.style or ""),
                    priority=str(slot.priority or "optional"),
                )
            )
    return out


SYSTEM_PROMPT = """\
你是这份中医药讲稿的**美术指导**。你的任务不是"每页配一张图"，而是判断：
哪几页真的需要图、需要什么样的图、现有素材里哪一张配得上。

工作方式：
1. 先 `search_assets`，看标题与许可；只对最像的两三张调 `look_at` 看一眼。
2. 主体不对 / 模糊 / 只有装裱边 → **换词再搜**（中文换英文、具体换笼统、换同义词）。
3. 实在没有 → `generate_image`（装饰位才可用），或者干脆不配。
4. 确定用哪张 → `assign_asset`，`why` 写一句人话理由。
5. 都处理完 → `finish`。

硬规矩（违反会被工具拒绝，不是提醒）：
- 证据位（讲"这就是某味药"）只能用 `local` 与 `api.wellcome` 的免 NC 条目；
  查不到就留空，**绝不拿别的药或"通用植物图"顶替**。
- 证书位（CC-BY-NC）只能进装饰位，且必须带署名。
- 图标只能当符号，不要当主图。
- 一页最多两张，一份稿最多 {gen} 张生成图。
- 宁可不配图。空着的图位会换成无图版式，读者看不出缺；配错图则整页都错。

可用渠道与路由：
{hint}
"""


# ── 循环本体 ────────────────────────────────────────────────────────────────


class AssetLoop:
    """一次循环的全部状态。**刻意做成对象**：工具执行、预算扣减、拒绝理由都需要共享状态。"""

    def __init__(self, *, router: ModelRouter, task_type: str, asks: list[PageAsk]) -> None:
        self.router = router
        self.task_type = task_type
        self.asks = asks
        self.pool: dict[str, materials.Candidate] = {}
        self.assignments: dict[tuple[str, str], Assignment] = {}
        self.generated = 0
        self.calls = 0
        self.notes: list[str] = []
        self.finished = False

    # ── 工具执行 ────────────────────────────────────────────────────────

    async def _search(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "query 不能为空"}
        source = str(args.get("source") or "auto")
        kind = str(args.get("kind") or "")
        limit = min(max(int(args.get("limit") or SEARCH_LIMIT), 1), 12)
        found = await materials.search(source, query, kind=kind, limit=limit)
        for item in found:
            self.pool[item.id] = item
        return {
            "ok": True,
            "count": len(found),
            "candidates": [item.as_dict() for item in found],
            "translated": materials.translate(query),
            "note": "" if found else "这个源没搜到。换个词、换个源，或者判定该页不配图。",
        }

    async def _look(self, args: dict[str, Any]) -> dict[str, Any]:
        asset_id = str(args.get("asset_id") or "")
        candidate = self.pool.get(asset_id)
        if candidate is None:
            return {"ok": False, "error": f"没有这个候选：{asset_id}。先 search_assets。"}
        question = str(args.get("question") or "")
        verdict = await look_at(candidate, self.router, self.task_type, question=question)
        return verdict

    async def _assign(self, args: dict[str, Any]) -> dict[str, Any]:
        unit_id = str(args.get("unit_id") or "").strip()
        asset_id = str(args.get("asset_id") or "").strip()
        slot = str(args.get("slot") or "figure")
        why = str(args.get("why") or "")[:80]

        candidate = self.pool.get(asset_id)
        if candidate is None:
            return {"ok": False, "error": f"asset_id `{asset_id}` 不在候选里。只能绑定 search_assets 返回过的。"}
        if unit_id not in {ask.unit_id for ask in self.asks}:
            return {"ok": False, "error": f"unit_id `{unit_id}` 不在任务清单里。"}

        if slot == "evidence":
            if candidate.source not in channels.EVIDENCE_CHANNELS:
                return {
                    "ok": False,
                    "error": (
                        f"`{candidate.source}` 不能用于证据位。证据位只能用 "
                        f"{sorted(channels.EVIDENCE_CHANNELS)}。改绑 figure，或换源重搜。"
                    ),
                }
            if candidate.license in {"CC-BY-NC-4.0"}:
                return {"ok": False, "error": "非商业许可的图不能作为药材证据，只能装饰。"}

        used = sum(1 for (uid, _), _item in self.assignments.items() if uid == unit_id)
        if used >= PER_PAGE:
            return {"ok": False, "error": f"这一页已经配了 {PER_PAGE} 张，够了。"}

        self.assignments[(unit_id, slot)] = Assignment(unit_id=unit_id, slot=slot, candidate=candidate, why=why)
        return {"ok": True, "assigned": asset_id, "unit_id": unit_id, "slot": slot, "license": candidate.license}

    async def _generate(self, args: dict[str, Any]) -> dict[str, Any]:
        if self.generated >= GEN_BUDGET:
            return {"ok": False, "error": f"生成图预算已用完（{GEN_BUDGET} 张/份）。这页留空。"}
        unit_id = str(args.get("unit_id") or "").strip()
        prompt = str(args.get("prompt") or "").strip()
        if unit_id not in {ask.unit_id for ask in self.asks}:
            return {"ok": False, "error": f"unit_id `{unit_id}` 不在任务清单里。"}
        if not prompt:
            return {"ok": False, "error": "prompt 不能为空"}

        asset_id = materials.asset_id_of("generated", "wanx", f"{unit_id}:{prompt}:{self.generated}")
        result = await wanx.generate(prompt, size=str(args.get("size") or "square"), asset_id=asset_id)
        if not result.get("ok"):
            return {"ok": False, "error": f"生图失败（{result.get('reason')}）。这页改走无图版式。"}

        self.generated += 1
        candidate = materials.Candidate(
            id=asset_id,
            title=prompt[:40],
            source="gen.image",
            kind="generated",
            url=result.get("local") or "",
            thumb=result.get("local") or "",
            license="自产",
            attribution=f"AI 生成示意 · {result.get('model')}",
            slot_class="decorative",
            note="AI 生成示意",
            extra={"remote": result.get("remote", ""), "prompt": prompt},
        )
        self.pool[asset_id] = candidate
        return {
            "ok": True,
            "asset_id": asset_id,
            "note": "生成完成。现在用 assign_asset 绑定，且只能绑 figure 槽位。",
        }

    async def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        if name == "search_assets":
            return await self._search(args)
        if name == "look_at":
            return await self._look(args)
        if name == "assign_asset":
            return await self._assign(args)
        if name == "generate_image":
            return await self._generate(args)
        if name == "finish":
            self.finished = True
            summary = str(args.get("summary") or "")
            if summary:
                self.notes.append(summary[:120])
            return {"ok": True}
        return {"ok": False, "error": f"未知工具：{name}"}

    # ── 主循环 ──────────────────────────────────────────────────────────

    async def run(self) -> LoopResult:
        if not self.asks:
            return LoopResult(notes=["设计单没有图位，跳过取图循环。"])

        started = time.monotonic()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT.format(gen=GEN_BUDGET, hint=channels.SYSTEM_HINT)},
            {"role": "user", "content": self._task_text()},
        ]

        rounds = 0
        while rounds < MAX_ROUNDS and time.monotonic() - started < MAX_SECONDS and not self.finished:
            rounds += 1
            turn = await self.router.chat_tools(self.task_type, messages, tools=TOOLS)
            if not turn.tool_calls:
                # 模型没调工具就收尾：把它的正文留下当备注，不再逼它继续。
                if turn.text:
                    self.notes.append(turn.text[:160])
                break
            messages.append(turn.message or {"role": "assistant", "content": turn.text})
            for call in turn.tool_calls:
                if not call.arguments and call.raw_arguments:
                    result: dict[str, Any] = {
                        "ok": False,
                        "error": f"参数不是合法 JSON：{call.raw_arguments[:120]}。重写一次。",
                    }
                else:
                    result = await self.execute(call.name, call.arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            if self.finished:
                break

        result = LoopResult(
            assignments=list(self.assignments.values()),
            rounds=rounds,
            calls=self.calls,
            generated=self.generated,
            notes=self.notes,
            ran=True,
        )
        if rounds >= MAX_ROUNDS:
            result.notes.append(f"达到轮次上限 {MAX_ROUNDS}，按已有结果收工。")
        return result

    def _task_text(self) -> str:
        lines = [f"这份稿子共 {len(self.asks)} 个图位，逐条判断：", ""]
        lines.extend(ask.describe() for ask in self.asks)
        lines.append("")
        lines.append(
            "顺序建议：先 `local`（够用就别去远程），再按需 `api.wellcome`（证据）/ "
            "`api.cleveland`（氛围），最后 `generate_image`。"
            "定下来之前，至少对你打算用的那张调一次 `look_at`。"
        )
        return "\n".join(lines)


async def resolve_with_tools(
    design: DeckDesign,
    *,
    router: ModelRouter,
    task_type: str = "generate",
) -> LoopResult:
    """入口。**任何异常都退化成空结果**，由 `select` 兜底，不让整份稿子失败。"""

    asks = asks_of(design)
    if not asks:
        return LoopResult(notes=["设计单没有图位。"])
    try:
        return await AssetLoop(router=router, task_type=task_type, asks=asks).run()
    except Exception as exc:  # noqa: BLE001 - 循环挂了不该拖死生成
        logger.warning("素材工具循环失败，回落选型器：%s", exc)
        return LoopResult(notes=[f"循环异常：{exc.__class__.__name__}"])


# ── 看图 ────────────────────────────────────────────────────────────────────


VISION_PROMPT = """\
你在帮一份中医药讲稿挑选配图。看这张图，只回答三件事：

1. `what`：画面主体是什么（一句，30 字内）。若是残片、纯装裱边、模糊不可辨，直接说。
2. `fit`：是否适合作为「{subject}」的配图？只能是 yes / no / unsure。
3. `why`：一句话理由。

只输出 JSON，不要解释：{{"what": "...", "fit": "yes", "why": "..."}}
"""


async def look_at(
    candidate: materials.Candidate,
    router: ModelRouter,
    task_type: str,
    *,
    question: str = "",
) -> dict[str, Any]:
    """让模型看一眼候选图。

    走的是**和正文同一个 Flash 模型**（已实测：它对画面描述准确，关掉思考反而更稳，
    Pro 会顺着「复古插画」的语境编造）。取不到图或模型不回 JSON 时返回
    ``ok: false`` —— 调用方据此让模型「自己决定要不要信」，而不是伪造一个判断。
    """

    data_url = await _read_as_data_url(candidate.url or candidate.thumb)
    if not data_url:
        return {"ok": False, "error": "这张图取不到（可能源站抖动），换一张或直接绑定。"}

    subject = question or candidate.title
    messages = [
        {"role": "system", "content": "你只输出 JSON，不要 markdown 代码块。"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": VISION_PROMPT.format(subject=subject)},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]
    try:
        raw = await router.chat(task_type, messages, max_tokens=300)
    except Exception as exc:  # noqa: BLE001 - 看图失败不是致命错误
        return {"ok": False, "error": f"看图失败：{exc.__class__.__name__}"}

    parsed = _parse_json_object(raw)
    if not parsed:
        return {"ok": False, "error": "模型没给出可解析的判断，可以自行决定。", "raw": raw[:120]}
    return {
        "ok": True,
        "what": str(parsed.get("what") or "")[:80],
        "fit": str(parsed.get("fit") or "unsure"),
        "why": str(parsed.get("why") or "")[:80],
    }


async def _read_as_data_url(url: str) -> str:
    """取图 → 缩到 768 → base64 data URL。

    必须缩：原图 1600px 走视觉模型会白烧几百个 token，而判断「这是不是黄芪」用不上
    那个分辨率。取不到就返回空串，让上层走分支。
    """

    if not url:
        return ""
    if url.startswith("/"):
        # 本地素材：读文件比再发一次 HTTP 稳。
        from pathlib import Path

        root = Path(__file__).resolve().parents[3] / "frontend" / "public"
        path = root / url.lstrip("/")
        try:
            raw = path.read_bytes()
        except OSError:
            return ""
    else:
        try:
            async with httpx.AsyncClient(timeout=materials.HTTP_TIMEOUT, follow_redirects=True) as client:
                response = await client.get(url)
                if response.status_code >= 400:
                    return ""
                raw = response.content
        except httpx.HTTPError:
            return ""

    try:
        from PIL import Image
        import io

        with Image.open(io.BytesIO(raw)) as image:
            image = image.convert("RGB")
            if max(image.size) > VISION_MAX_EDGE:
                image.thumbnail((VISION_MAX_EDGE, VISION_MAX_EDGE))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=82)
            raw = buffer.getvalue()
    except ImportError:  # pragma: no cover
        pass
    except (OSError, ValueError):
        return ""

    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


def _parse_json_object(text: str) -> dict[str, Any]:
    """从模型输出里抠出第一个 JSON 对象。**容忍 markdown 代码块与前后废话。**"""

    raw = (text or "").strip()
    if not raw:
        return {}
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        value = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}

