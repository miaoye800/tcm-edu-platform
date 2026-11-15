"""选材步：把一个槽位的**需求**解析成**具体资产**，并留下可查的理由。

四条边界，来自 ``docs/studios/assets.md``：

1. **运行时零网络**。这里只读本地登记表与本地文件，采集只发生在开发期脚本里。
2. **登记表是唯一入口**。任何图片不先登记就不许被引用；代码里没有硬编码路径。
3. **四级阶梯**：参数化母题 → 库内命中 → 库内平替 → 生图 / 降级。
4. **确定性**。同分按 ``id`` 字典序取第一个——同一份设计永远得到同一份素材，
   否则截图基线没有意义。

每个槽位的解析结果整份写进 ``meta.slot_resolution[]``，这是「这页为什么长这样」
的唯一答案：用户问「为什么封面没图」，答案在这里，不在日志里。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ..resources.licenses import LICENSE_ALLOWLIST, LICENSES_NEEDING_ATTRIBUTION
from .design import DeckDesign, DesignSlot, PageDesign, SlotResolution
from .motifs import motifs_for

#: 许可白名单的唯一来源在 ``app/resources/licenses.py``——这里曾经和
#: ``tools/assets/audit.py`` 各写一份且不一致（audit 放行 MIT/Apache，
#: 选材时却判 -1），造成「图标入库了却永远选不中」。此处只做 re-export，
#: ``service.py`` 仍按 ``from app.ppt.select import LICENSE_ALLOWLIST`` 取用。
__all__ = ["LICENSE_ALLOWLIST", "LICENSES_NEEDING_ATTRIBUTION"]

#: 每份幻灯最多生成几张图。生成图只进装饰位，且必须可删。
IMAGE_BUDGET = 3
#: 命中阈值。「精确命中」是 100 起；30 起算平替。
HIT_SCORE = 100
SUBSTITUTE_SCORE = 30
#: 允许被裁切到目标比例时的加分。
CROP_SCORE = 10
#: 命中一个检索关键词的加分。关键词是模型为「去库里搜」专门写的短词，
#: 命中一个就该比一句画面描述更值钱。
KEYWORD_SCORE = 28


@runtime_checkable
class ImageProvider(Protocol):
    """生图接口（预留）。默认没有实现，``available()`` 恒为 False。

    契约不是为了将来接上方便，而是为了**现在就划清边界**：生成结果只能登记成
    ``kind=generated``、``slot_class=decorative``，且必须带 prompt/model/seed，
    否则不可复现、不可替换。
    """

    name: str

    def available(self) -> bool: ...

    async def generate(self, prompt: str, *, size: tuple[int, int], style_tokens: dict[str, str]) -> dict[str, Any]: ...


class Asset(BaseModel):
    """登记表里的一条。字段与 ``assets.md`` §3 逐项对应。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    kind: str = "plate"
    slot_class: str = "decorative"
    subjects: list[str] = Field(default_factory=list)
    herb_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    file: str = ""
    w: int = 0
    h: int = 0
    bytes: int = 0
    sha256: str = ""
    license: str = ""
    source_url: str = ""
    attribution: str = ""
    treatment: str = ""
    added_at: str = ""
    review: dict[str, str] = Field(default_factory=dict)


def registry_path() -> Path:
    """登记表位置：``frontend/public/assets/registry.json``。

    后端读前端的 public 目录看着别扭，但反过来更糟：登记表若放后端，前端渲染
    还要多一次请求。同一份 JSON、同一个仓库，路径写在一处即可。
    """

    return Path(__file__).resolve().parents[3] / "frontend" / "public" / "assets" / "registry.json"


_REGISTRY_CACHE: dict[str, tuple[float, list[Asset]]] = {}


def load_registry(path: Path | None = None) -> list[Asset]:
    """读登记表。文件不存在时返回空表——**空库必须能生成**，不能报错。"""

    target = path or registry_path()
    try:
        mtime = target.stat().st_mtime
    except OSError:
        return []
    key = str(target)
    cached = _REGISTRY_CACHE.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    assets: list[Asset] = []
    for item in raw.get("assets") or []:
        try:
            assets.append(Asset.model_validate(item))
        except Exception:  # noqa: BLE001 - 一条坏记录不该让整份资产库不可用
            continue
    _REGISTRY_CACHE[key] = (mtime, assets)
    return assets


def score(asset: Asset, want: DesignSlot, *, theme_id: str = "") -> int:
    """给一条资产打分。规则与 ``assets.md`` §7.2 一一对应，确定性、无随机。"""

    # 类别只锁一个方向：**证据位不能拿装饰图充数**，反过来可以。
    #
    # 一张 CC0 饮片实拍登记成 evidence（它证明「这味药长什么样」），
    # 但同一张图放在要点页右侧当配图也完全正当——那只是用途变了，图还是真的。
    # 反过来则不行：生成图 / 母题再好看，也不能放进 evidence 槽当「形态证据」。
    #
    # 早先这里要求两边严格相等，结果是「主要功效」页想配一张药材照永远落空：
    # 图在库里、槽位在页上，就是接不上。
    if want.slot_class == "evidence" and asset.slot_class != "evidence":
        return -1
    # 装饰资产（角花/边带/底纹）只进装饰位：配图页不该抓一张纹样当插图。
    # 反向不设卡——装饰位想拿一张馆藏彩绘当点缀，是合法用法。
    if want.slot in ("figure", "evidence") and asset.kind in ("ornament", "ground-texture"):
        return -1
    if asset.license and asset.license not in LICENSE_ALLOWLIST:
        return -1
    total = 0
    subject = (want.subject or "").strip()
    if subject and subject != "无":
        # 装饰槽的 subject 说的是标签语言（「回纹角花」），tags 里就有这个词；
        # 只比 subjects 会让「回纹角花」对上 tags=["角花","回纹"] 落空。
        candidates = [*asset.subjects]
        if want.slot in ("ornament", "ground"):
            candidates.extend(asset.tags)
        if subject in asset.subjects:
            total += HIT_SCORE
        elif any(subject in item or item in subject for item in candidates if len(item) >= 2):
            total += 60
        else:
            # 模型的 subject 常是一句画面描述（「明代本草彩绘图谱一页」），
            # 与登记表的「Ming herbal」永远不可能相等。按字重叠算相似度，
            # 让「描述」也能落在对的图上，而不是一律判无。
            overlap = max((_overlap(subject, item) for item in asset.subjects), default=0.0)
            if overlap >= 0.6:
                total += 55
            elif overlap >= 0.35:
                total += 25
    words = [word for word in want.keywords if len(word) >= 2]
    if words:
        blob = " ".join([*asset.subjects, *asset.tags, *asset.herb_ids])
        hits = sum(1 for word in words if word in blob)
        total += KEYWORD_SCORE * hits
        if want.slot in ("ornament", "ground") and asset.kind in ("ornament", "ground-texture") and hits:
            # 装饰检索没有 subject 描述的重叠兜底，只有 tags：一个词命中
            # 够 substitute（页面不空窗），两个词是干净命中（不打「示意」标）。
            total += 32 * min(hits, 2)
    if asset.kind == "motif" and want.style == "motif":
        total += 30
    if asset.kind == want.style:
        total += 40
    # 装饰的槽位-种类对位：角花槽选 tileable 底纹、底纹槽选单只角花，
    # 词全命中也会发生（「回纹」两边都有 tag）。同分时按 id 排序会把
    # ground-fret-tileable 排到 ornament-corner-antique 前面——所以对位
    # 必须显式加分，不能指望分数自然拉开。
    if want.slot == "ornament" and asset.kind == "ornament":
        total += 25
    if want.slot == "ground" and asset.kind == "ground-texture":
        total += 25
    # 角花槽的形状先验：素材包里的 SVG 有「角花形」与「整幅图案」两种，
    # 词全命中也分不开（回纹既是角花 tag 也是横带 tag）。整幅图案当角花
    # 就是四角一块实心花砖（2026-11-15 实拍实锤）。tag 语言是唯一的形状
    # 证据：卷草/藤蔓/古典是角花资产的标注词，横带/边框是横带资产的。
    if want.slot == "ornament":
        tags = set(asset.tags)
        if tags & {"卷草", "藤蔓", "古典", "角饰"}:
            total += 12
        if tags & {"横带", "边框"}:
            total -= 10
    wanted_aspect = _aspect_of(want.aspect)
    if wanted_aspect and asset.w and asset.h:
        if _same_aspect(asset.w, asset.h, wanted_aspect):
            total += 20
        elif _croppable(asset.w, asset.h, wanted_aspect):
            total += CROP_SCORE
    if max(asset.w, asset.h) < 800:
        total -= 50
    # 主题不合：资产若显式标了适用主题，而当前主题不在其中，扣分。
    # 归一化算子本身与主题无关（见 assets.md §6），所以这里看的是 tags 而不是 treatment。
    theme_tags = [tag for tag in asset.tags if tag in ("paper", "ink", "celadon", "cinnabar")]
    if theme_id and theme_tags and theme_id not in theme_tags:
        total -= 30
    return total


def _overlap(left: str, right: str) -> float:
    """两段中文的字面相似度（按 2-gram 覆盖率）。

    只用于「描述 vs 标注」的粗判：``明代本草彩绘图谱`` 与 ``Ming herbal painting``
    其实指的是同一类东西，但翻译与写法都不一致；能靠字面命中的是
    ``黄芪饮片`` 与 ``黄芪`` 这种，剩下的交给 keywords。
    """

    left = "".join(str(left or "").split())
    right = "".join(str(right or "").split())
    if len(left) < 2 or len(right) < 2:
        return 0.0
    grams = {left[index : index + 2] for index in range(len(left) - 1)}
    other = {right[index : index + 2] for index in range(len(right) - 1)}
    if not grams or not other:
        return 0.0
    return len(grams & other) / min(len(grams), len(other))


def resolve_slots(
    design: DeckDesign,
    *,
    registry: list[Asset] | None = None,
    provider: ImageProvider | None = None,
) -> list[SlotResolution]:
    """逐个槽位走四级阶梯。返回顺序与 ``design.pages`` 一致，便于对照。"""

    assets = registry if registry is not None else load_registry()
    generated = 0
    # 装饰种子按**整份稿子**算，不按页算：同一份设计永远得到同一套底纹与角饰
    # （截图基线才有意义），但不同主题会落到不同母题。
    #
    # 这一句修的是一个真 bug：``_parametric`` 原来恒取 ``candidates[0]``，
    # 于是底纹永远是 ``paper-plain``，主题里配的另几个母题等于没写。
    deck_seed = _deck_seed(design)
    results: list[SlotResolution] = []
    for page in design.pages:
        for slot in page.slots:
            resolution, used = _resolve_one(
                page, slot, assets, design.theme_id, provider, generated, deck_seed, design=design
            )
            if used:
                generated += 1
            results.append(resolution)
    return results


def _deck_seed(design: DeckDesign) -> str:
    """整份稿子的装饰种子：主题 + 前三页的 unit_id。

    只看前三页而不是全部，是为了「多一页不该换掉整份稿子的角饰」；
    主题与首几页已经足够把不同稿子拉开。
    """

    head = "|".join(page.unit_id for page in design.pages[:3])
    return f"{design.theme_id}|{head}"


def _stable_index(text: str, size: int) -> int:
    """把字符串稳定映射到 ``[0, size)``。同输入同输出，跨进程跨机器一致。"""

    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % size


def _library_pick(
    base: SlotResolution,
    assets: list[Asset],
    slot: DesignSlot,
    theme_id: str,
    *,
    allow_ties: bool = False,
) -> SlotResolution | None:
    """在库里挑一张：干净命中 / 近替都算解，返回 None 交回上层降级。

    近替（substitute）在图位上会带「示意」标注，所以阈值与主流程一致：
    HIT_SCORE 是「就是它」，SUBSTITUTE_SCORE 是「最接近的一张」。
    ``allow_ties`` 给装饰位用：策展包里三张对称角花同分命中是常态，
    图位要求「唯一的那张」才不降级，装饰位并列择一即可（按 id 排序取第一，
    确定性不变）。
    """

    ranked = sorted(
        ((score(asset, slot, theme_id=theme_id), asset) for asset in assets),
        key=lambda item: (-item[0], item[1].id),
    )
    best = next(((value, asset) for value, asset in ranked if value > 0), None)
    if best and best[0] >= HIT_SCORE and (allow_ties or _unique_top(ranked, best[0])):
        value, asset = best
        return base.model_copy(
            update={
                "result": "hit",
                "provenance": "library",
                "asset_id": asset.id,
                "score": value,
                "license": asset.license,
                "attribution": asset.attribution,
                "file": asset.file,
            }
        )
    if best and best[0] >= SUBSTITUTE_SCORE:
        value, asset = best
        return base.model_copy(
            update={
                "result": "substitute",
                "provenance": "library",
                "asset_id": asset.id,
                "score": value,
                "license": asset.license,
                "attribution": asset.attribution,
                "file": asset.file,
                "fallback_reason": "库里没有精确命中，取最接近的一张，页面标「示意」",
            }
        )
    return None


def _resolve_one(
    page: PageDesign,
    slot: DesignSlot,
    assets: list[Asset],
    theme_id: str,
    provider: ImageProvider | None,
    generated: int,
    deck_seed: str = "",
    *,
    design: DeckDesign | None = None,
) -> tuple[SlotResolution, bool]:
    requested = {
        "subject": slot.subject,
        "style": slot.style,
        "aspect": slot.aspect,
        "priority": slot.priority,
        "keywords": list(slot.keywords),
        "composition": slot.composition,
    }
    base = SlotResolution(unit_id=page.unit_id, slot=slot.slot, requested=requested)

    # 「不挂角花」是一个答案，不是缺口。历史设计单里可能还留着角饰槽位
    # （用户在方案卡上把角花改成 none 之前建的），这里必须挡住参数化阶梯——
    # 否则它照样抽一朵卷草回来，用户看到的还是「每个 PPT 同一个角花」。
    # 但方案里还有一种写法：{motif:'none', subject:'回纹角花', keywords:[...]}——
    # 「不要程序画的，去库里找真纹样」。带检索词的槽位必须放行，否则
    # 「要真角花」永远等价于「什么都没有」。
    if slot.slot == "ornament" and design is not None:
        # subject 默认值「无」不算检索词，只有真正写了字才算。
        wants_real_ornament = str(slot.subject or "").strip() not in ("", "无") or bool(list(slot.keywords))
        if not wants_real_ornament and str(getattr(design, "ornament_motif", "") or "") in ("", "none"):
            return base.model_copy(update={"result": "none", "fallback_reason": "方案未选角饰"}), False

    # 阶梯 0：设计单已经把这一处的母题定死了。**不再按种子抽**——
    # 以前「主题里配了几个角饰，实际永远选中卷草」，因为 _parametric 用的是
    # 稳定种子，模型/用户改的 ornament_motif 完全没参与。
    preferred = _preferred_motif(slot, design)
    if preferred:
        return base.model_copy(update={"result": "parametric", "provenance": "parametric", "asset_id": preferred}), False

    # 阶梯 0.5：装饰槽带检索词（subject/keywords）就是在说「要真纹样」——
    # 方案里的写法是 {motif:'none', subject:'回纹角花', keywords:['回纹','纹样']}。
    # 装饰素材包（角花/回纹框/剪纸底纹）在这里接上；**先搜库、搜不到再落母题**，
    # 否则「要真纹样」永远等于程序画的卷草（这就是「角饰是内置的」那条病）。
    if slot.slot in ("ornament", "ground") and (str(slot.subject or "").strip() not in ("", "无") or list(slot.keywords)):
        picked = _library_pick(base, assets, slot, theme_id, allow_ties=True)
        if picked is not None:
            return picked, False

    # 阶梯 1：能算的先用代码算。底纹 / 角饰 / 界格 / 印章一律走这里。
    # 注意顺序：背景图（style=photo 的 ground）**先搜库**，搜不到再落母题，
    # 否则「用一张中药房实拍当背景」永远等于 paper-plain。
    if slot.slot not in ("figure", "evidence") and slot.style != "photo":
        parametric = _parametric(slot, theme_id, seed=deck_seed)
        if parametric:
            return base.model_copy(update={"result": "parametric", "provenance": "parametric", "asset_id": parametric}), False

    picked = _library_pick(base, assets, slot, theme_id)
    if picked is not None:
        return picked, False

    # 背景图没搜到：落回母题（纸纹），不留一块空背景。
    if slot.slot == "ground":
        motif = (getattr(design, "paper_motif", "") if design is not None else "") or _parametric(slot, theme_id, seed=deck_seed)
        if motif:
            return (
                base.model_copy(
                    update={
                        "result": "parametric",
                        "provenance": "parametric",
                        "asset_id": motif,
                        "fallback_reason": "背景图没有合适的一张，改用纸纹",
                    }
                ),
                False,
            )

    # 阶梯 4a：生图。只对装饰位开放，且受预算约束。
    if provider is not None and _available(provider) and slot.slot != "evidence" and generated < IMAGE_BUDGET:
        return (
            base.model_copy(
                update={
                    "result": "generated",
                    "provenance": "generated",
                    "asset_id": "",
                    "fallback_reason": f"库内无可用素材，交由 {getattr(provider, 'name', 'provider')} 生成，页面标「AI 生成示意」",
                }
            ),
            True,
        )

    # 阶梯 4b：降级。不许留灰块、不许拿风格插图冒充证据。
    reason = "证据位不接受生成图，且库内没有可核出处" if slot.slot == "evidence" else "库内无可用素材，改为无图版式"
    if slot.priority == "required":
        reason = f"{reason}；该槽位标记为必需，校验会报 slot_unfilled"
    return base.model_copy(update={"result": "none", "provenance": "none", "fallback_reason": reason}), False


def _preferred_motif(slot: DesignSlot, design: DeckDesign | None) -> str:
    """设计单钉死的母题。只有「方案真的选了」才返回，否则空串走原阶梯。"""

    if design is None:
        return ""
    if slot.slot == "ornament":
        motif = str(getattr(design, "ornament_motif", "") or "")
        # 明确写了 none 就不画角花——这一条以前不存在，于是每份稿子四角开花。
        return "" if motif in ("", "none") else motif
    if slot.slot == "ground" and slot.style == "motif":
        return str(getattr(design, "paper_motif", "") or "")
    return ""


def _parametric(slot: DesignSlot, theme_id: str, *, seed: str = "") -> str:
    """能由母题生成的槽位，返回母题 id；不能则空串。

    注意 ``figure`` 不走这里：主插图是「点题位」，需要真实图像；母题只负责
    底纹 / 角饰 / 界格 / 印章这四类**构图**元素。

    候选多于一个时按 ``seed`` 稳定取一个，而不是恒取 ``[0]``。原来那句
    ``candidates[0].id`` 让底纹恒为 ``paper-plain``——主题里配的另几个母题
    写在目录里，却永远轮不到。
    """

    if slot.slot in ("figure", "evidence"):
        return ""
    candidates = motifs_for(slot.slot, theme_id) or motifs_for("ground", theme_id)
    if not candidates:
        return ""
    if len(candidates) == 1 or not seed:
        return candidates[0].id
    return candidates[_stable_index(f"{seed}|{slot.slot}", len(candidates))].id


def _available(provider: ImageProvider) -> bool:
    try:
        return bool(provider.available())
    except Exception:  # noqa: BLE001 - 生图服务异常不能拖垮整份稿子
        return False


def _unique_top(ranked: list[tuple[int, Asset]], top: int) -> bool:
    return sum(1 for value, _ in ranked if value == top) == 1


def _aspect_of(aspect: str) -> float:
    return {"portrait": 3 / 4, "square": 1.0, "landscape": 4 / 3}.get(aspect, 0.0)


def _same_aspect(w: int, h: int, want: float) -> bool:
    return abs((w / h if h else 0) - want) <= 0.06


def _croppable(w: int, h: int, want: float) -> bool:
    """裁到目标比例后还剩多少。剩得太少等于把主体裁掉，不算可用。"""

    if not w or not h:
        return False
    ratio = w / h
    keep = min(ratio, want) / max(ratio, want)
    return keep >= 0.72


def summary(results: list[SlotResolution]) -> dict[str, Any]:
    """给回执用的计数：四级各几个、有没有必需槽位落空。"""

    counts: dict[str, int] = {}
    for item in results:
        counts[item.result] = counts.get(item.result, 0) + 1
    return {
        "total": len(results),
        "counts": counts,
        "unfilled": [
            f"{item.unit_id}:{item.slot}"
            for item in results
            if item.result == "none" and item.requested.get("priority") == "required"
        ],
    }
