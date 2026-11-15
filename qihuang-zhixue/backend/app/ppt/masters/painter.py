"""母版上色器：把 deck + design + （已挂在页上的）素材解析结果逐页画成 Scene。

与 ``scene_art.paint_scenes`` 的分工：这里**一次模型调用都没有**——母版命中时
几何全部由代码确定性产出，模型在整条链路上只剩「选母版、选页型、填内容」。

失败策略（与计划一致）：
- ``design.master_id`` 为空或未命中 → 返回 ``{"skipped": ...}``，调用方退回
  scene_art（LLM 自由排版），老方案回放路径因此原样保留；
- 母版构造器抛异常（代码 bug）→ 记日志 + 直接退 ``scene_fallback``，
  不再花 LLM 调用。
"""

from __future__ import annotations

import logging
from typing import Any

from ..scene import validate_scene
from ..scene_fallback import fallback_scene
from . import by_id
from .base import PAGE_ROLES, PageContext

logger = logging.getLogger(__name__)

#: 与 scene_art 一致：一份稿子最多排前 16 页。
MAX_PAGES = 16
#: bullets 容量上限。超出截断 + warning（宽松读：内容多不是错，但版面装不下）。
MAX_BULLETS = 6


def _design_data(design: Any) -> dict[str, Any]:
    if design is None:
        return {}
    if isinstance(design, dict):
        return design
    if hasattr(design, "model_dump"):
        return design.model_dump()
    return dict(design)


def master_id_of(design: Any) -> str:
    """设计单里的母版 id。dict / pydantic / None 都宽松读。"""
    return str(_design_data(design).get("master_id") or "").strip()


def _palette_of(design: dict[str, Any], master: Any) -> dict[str, str]:
    """母版色板变体 + 设计单上的覆盖（look.palette 的语义就是「对母版的覆盖」）。"""
    variant_index = _clamp_int(design.get("accent_variant"), 0, len(master.spec.palette_variants) - 1)
    palette = dict(master.spec.palette(variant_index))
    override = design.get("palette")
    if isinstance(override, dict):
        for key in ("bg", "fg", "muted", "accent", "line"):
            value = str(override.get(key) or "").strip()
            if value.startswith("#") and len(value) >= 7:
                palette[key] = value.upper()[:7]
    return palette


def _clamp_int(value: Any, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return low


def _composition_of(design: dict[str, Any], index: int) -> str:
    pages = design.get("pages") or []
    if isinstance(pages, list) and index < len(pages) and isinstance(pages[index], dict):
        return str(pages[index].get("composition") or "auto")
    return "auto"


def _figure_of(refs: list[Any]) -> dict[str, Any] | None:
    """figure / evidence 槽的解析结果 → 图位描述。"""
    for ref in refs:
        item = ref.model_dump() if hasattr(ref, "model_dump") else (dict(ref) if isinstance(ref, dict) else {})
        if item.get("slot") not in ("figure", "evidence"):
            continue
        if item.get("result") not in ("hit", "substitute", "parametric"):
            continue
        if not (item.get("url") or item.get("file") or item.get("asset_id")):
            continue
        asset_id = str(item.get("asset_id") or "")
        if not asset_id:
            continue
        kind = "motif" if item.get("result") == "parametric" else "asset"
        return {"id": asset_id, "kind": kind, "caption": str(item.get("note") or "")}
    return None


def _ornament_of(refs: list[Any]) -> tuple[list[str], str]:
    """ornament 槽 → (真纹样资产 ids, 程序母题 id)。"""
    ids: list[str] = []
    motif = ""
    for ref in refs:
        item = ref.model_dump() if hasattr(ref, "model_dump") else (dict(ref) if isinstance(ref, dict) else {})
        if item.get("slot") != "ornament":
            continue
        if item.get("result") not in ("hit", "substitute", "parametric"):
            continue
        asset_id = str(item.get("asset_id") or "")
        if not asset_id:
            continue
        if item.get("result") == "parametric":
            motif = asset_id
        else:
            ids.append(asset_id)
    return ids, motif


def _ground_of(refs: list[Any], default: dict[str, Any]) -> dict[str, Any]:
    """ground 槽 → 背景描述。空则母版默认。"""
    for ref in refs:
        item = ref.model_dump() if hasattr(ref, "model_dump") else (dict(ref) if isinstance(ref, dict) else {})
        if item.get("slot") != "ground":
            continue
        if item.get("result") not in ("hit", "substitute", "parametric"):
            continue
        asset_id = str(item.get("asset_id") or "")
        if not asset_id:
            continue
        if item.get("result") == "parametric":
            return {"kind": "motif", "id": asset_id}
        return {"kind": "asset", "id": asset_id}
    return dict(default)


def _context(
    slide: Any,
    *,
    master: Any,
    design: dict[str, Any],
    palette: dict[str, str],
    index: int,
    total: int,
    deck_title: str,
) -> PageContext:
    refs = list(getattr(slide, "asset_refs", []) or [])
    blocks = [
        block.model_dump() if hasattr(block, "model_dump") else dict(block)
        for block in (getattr(slide, "blocks", []) or [])
    ]
    columns = [
        col.model_dump() if hasattr(col, "model_dump") else dict(col)
        for col in (getattr(slide, "columns", []) or [])
    ]
    bullets = [str(item) for item in (getattr(slide, "bullets", []) or [])]
    truncated = len(bullets) > MAX_BULLETS
    purpose = str(getattr(slide, "purpose", "") or "points")
    if purpose not in PAGE_ROLES:
        purpose = "points"
    ornament_ids, ornament_motif = _ornament_of(refs)
    # 背景统一成三种形状：{"kind": "solid"} / {"kind": "motif"|"asset", "id": ...}。
    # 母版默认背景存的是 {"kind": "motif", "motif": "paper-chain"}，这里翻译一次。
    ground = _ground_of(refs, master.spec.background)
    if ground.get("kind") == "motif" and not ground.get("id"):
        ground = {"kind": "motif", "id": str(ground.get("motif") or "paper-plain")}
    elif ground.get("kind") not in ("motif", "asset", "solid"):
        ground = {"kind": "motif", "id": "paper-plain"}
    return PageContext(
        purpose=purpose,
        title=str(getattr(slide, "title", "") or ""),
        subtitle=str(getattr(slide, "subtitle", "") or ""),
        bullets=bullets[:MAX_BULLETS],
        blocks=blocks,
        columns=columns,
        note=str(getattr(slide, "note", "") or ""),
        figure=_figure_of(refs),
        ornament_ids=ornament_ids,
        ornament_motif=ornament_motif,
        ground=ground,
        palette=palette,
        type_pair=str(design.get("type_pair") or master.spec.type_pair),
        composition=_composition_of(design, index),
        index=index,
        total=total,
        deck_title=deck_title,
        truncated=truncated,
    )


def _fallback_for(slide: Any, ctx: PageContext, master: Any) -> Any:
    """构造器失败时的安全网：scene_fallback 的确定性版式，气质回落「纸+墨+朱砂」。"""
    ground = ctx.ground or {}
    paper_motif = str(ground.get("id") or "") if ground.get("kind") == "motif" else ""
    return fallback_scene(
        archetype=ctx.purpose,
        title=ctx.title,
        subtitle=ctx.subtitle,
        blocks=ctx.blocks,
        bullets=ctx.bullets,
        columns=ctx.columns,
        figure_id=str((ctx.figure or {}).get("id") or ""),
        figure_caption=str((ctx.figure or {}).get("caption") or ""),
        figure_kind=str((ctx.figure or {}).get("kind") or "asset"),
        page_no=ctx.index + 1,
        total=ctx.total,
        deck_title=ctx.deck_title,
        paper_motif=paper_motif or "paper-plain",
        ornament_motif=ctx.ornament_motif,
    )


def paint_master_scenes(
    deck: Any,
    *,
    design: Any,
    deck_title: str = "",
    only: list[int] | None = None,
) -> dict[str, Any]:
    """按母版逐页排版。返回与 ``paint_scenes`` 同形状的记账。"""
    master = by_id(master_id_of(design))
    if master is None:
        return {"skipped": "no_master"}

    slides = list(getattr(deck, "slides", []) or [])
    if not slides:
        return {"skipped": "no_slides"}

    data = _design_data(design)
    palette = _palette_of(data, master)
    total = len(slides)
    wanted = set(only) if only is not None else None
    targets = [index for index in range(min(total, MAX_PAGES)) if wanted is None or index in wanted]
    if not targets:
        return {"pages": total, "painted": 0, "fallback": [], "warnings": [], "model": f"master:{master.spec.id}"}

    painted = 0
    fallback: list[dict[str, Any]] = []
    warnings: list[str] = []
    for index in targets:
        slide = slides[index]
        ctx = _context(
            slide,
            master=master,
            design=data,
            palette=palette,
            index=index,
            total=total,
            deck_title=deck_title,
        )
        try:
            scene = master.paint(ctx)
        except Exception as error:  # noqa: BLE001 - 构造器是代码 bug，退安全网并记日志
            logger.warning("master %s failed on page %s: %s", master.spec.id, index + 1, error)
            scene = _fallback_for(slide, ctx, master)
            fallback.append({"page": index + 1, "reason": f"master_error: {str(error).splitlines()[0][:100]}"})
        scene, page_warnings = validate_scene(scene)
        if scene is None:
            scene = _fallback_for(slide, ctx, master)
            fallback.append({"page": index + 1, "reason": (page_warnings[0] if page_warnings else "scene_invalid")})
            page_warnings = []
        else:
            painted += 1
        if ctx.truncated:
            warnings.append(f"page{index + 1}: bullets truncated to {MAX_BULLETS}")
        warnings.extend(f"page{index + 1}: {item}" for item in page_warnings)
        slide.scene = scene

    return {
        "pages": total,
        "painted": painted,
        "fallback": fallback,
        "warnings": warnings[:24],
        "model": f"master:{master.spec.id}",
    }
