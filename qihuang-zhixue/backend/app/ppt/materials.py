"""素材工具层：**模型的每一只手都在这里**。

设计要点三条，其余都是实现细节：

1. **不落盘**。远程素材只钉 URL，稿子里存的是「地址 + 元数据」，不是图。所以这里
   所有远程候选都过一次 ``proxy_url()``——wsrv 代理同时解决四件事：CORS（原图多数
   没有，导出时 fetch 会失败）、尺寸（显示 800 / 导出 1600）、格式（webp，32KB vs
   数百 KB）、缓存（一年）。
2. **失败必须是空列表，不是异常**。搜不到、断网、对方 403，都只意味着「这条渠道这次
   没货」；抛异常会把整份稿子的生成打断。所以每个 driver 自己吞掉网络错误。
3. **许可跟着候选走**。候选里带 ``license`` / ``attribution`` / ``slot_class``，
   模型看到 ``CC-BY-NC-4.0`` 就知道这张要署名、只能进装饰位。判定不靠提示词自觉，
   靠数据本身。

渠道清单见 ``channels.py``；这里只实现「能真的取到东西」的那几条，没实现的渠道
（pexels / unsplash 需 key、linustock 等禁再配布）调过来会返回空——**空即降级**。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from ..resources.licenses import LICENSE_ALLOWLIST
from . import channels
from .select import Asset, load_registry, registry_path

logger = logging.getLogger(__name__)

#: 单次请求超时。取素材是「顺带做的事」，不能拖住一份稿子——超过这个时间就当没搜到。
HTTP_TIMEOUT = 9.0
#: 查询缓存。同一份稿子里「黄芪」会被搜好几次（不同页、不同源），十秒级的缓存没意义。
CACHE_TTL = 600.0
#: wsrv 代理。所有远程图都从这里出，见模块 docstring 第 1 条。
PROXY = "https://wsrv.nl/"

USER_AGENT = "QihuangZhixue/1.0 (PPT asset retrieval; contact: local demo)"

#: 用途 → 素材大类。``search`` 的 auto 路由按这张表决定去哪些渠道。
KIND_PURPOSE: dict[str, str] = {
    "herb": "herb-evidence",
    "evidence": "herb-evidence",
    "object": "oriental-deco",
    "scene": "oriental-deco",
    "plate": "oriental-deco",
    "engraving": "oriental-deco",
    "ink": "oriental-deco",
    "pattern": "pattern",
    "texture": "texture",
    "icon": "icon",
    "silhouette": "silhouette",
    "photo": "photo",
    "hand-drawn": "hand-drawn",
}


@dataclass(slots=True)
class Candidate:
    """一条候选素材。**这就是模型在 search 之后看到的东西**。

    字段刻意少而全：它要能直接回答「这是哪张、多大、什么许可、能不能进证据位」，
    不需要模型再追问一轮。
    """

    id: str
    title: str
    source: str
    kind: str
    url: str
    thumb: str
    license: str
    attribution: str = ""
    slot_class: str = "decorative"
    w: int = 0
    h: int = 0
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self, *, compact: bool = True) -> dict[str, Any]:
        """给模型看的形状。``compact`` 去掉空字段——八个候选塞满空键会白烧 token。"""

        data = asdict(self)
        if not compact:
            return data
        return {key: value for key, value in data.items() if value not in ("", 0, {}, [])}


# ── URL 合成 ────────────────────────────────────────────────────────────────


def proxy_url(url: str, width: int = 1600) -> str:
    """把原图地址包成 wsrv 代理地址。

    不代理的后果很具体：Cleveland / Heritage / pngimg 的原图都**没有 CORS 头**，
    浏览器里 ``fetch`` 拿不到 blob，屏幕上能显示、导出 pptx 时一张都进不去。
    """

    if not url:
        return ""
    if url.startswith(PROXY):
        return url
    return f"{PROXY}?url={quote(url, safe='')}&w={int(width)}&output=webp"


def _asset_id(prefix: str, source: str, remote_id: str) -> str:
    """远程素材的稳定 id。

    场景里的 ``asset`` 只能以 ``plate-/photo-/generated-/motif-`` 开头（命名规则在
    ``scene.py`` 校验），而远程 id 往往是 ``L0039301`` 这种。所以拼一个
    「前缀 + 短哈希」：同一张图每次得到同一个 id，冻结后才可复现。
    """

    digest = hashlib.sha256(f"{source}:{remote_id}".encode("utf-8")).hexdigest()[:8]
    return f"{prefix}-{digest}"


def asset_id_of(prefix: str, source: str, remote_id: str) -> str:
    """公开的 id 生成器。循环里给生成图造 id 也用它，规则只有这一份。"""

    return _asset_id(prefix, source, remote_id)


def _kind_prefix(kind: str) -> str:
    """id 前缀。**语义要真**：馆藏本草图是版画，不是照片。

    一开始把 ``herb`` 也归成 ``photo``，于是 Wellcome 的明代彩绘被叫成
    ``photo-xxxx``——搜出来看着像实拍，实际是绢本设色。命名错了会误导后面的选材判断。
    """

    if kind == "photo":
        return "photo"
    if kind == "generated":
        return "generated"
    return "plate"


# ── 中文 → 英文 ─────────────────────────────────────────────────────────────

#: 检索词翻译表。馆藏接口只认英文，而模型写的是中文。
#: 命中即译，未命中原样透传（交给生图，中文直出反而更准）。
ZH2EN: dict[str, str] = {
    # 药材
    "人参": "ginseng",
    "黄芪": "astragalus",
    "黄耆": "astragalus",
    "当归": "angelica sinensis",
    "甘草": "licorice root",
    "陈皮": "dried tangerine peel",
    "半夏": "pinellia",
    "茯苓": "poria mushroom",
    "白术": "atractylodes",
    "白芍": "white peony root",
    "赤芍": "red peony root",
    "地黄": "rehmannia",
    "熟地": "prepared rehmannia",
    "川芎": "ligusticum",
    "丹参": "salvia miltiorrhiza",
    "柴胡": "bupleurum",
    "黄连": "coptis rhizome",
    "黄芩": "scutellaria",
    "大黄": "rhubarb root",
    "桂枝": "cinnamon twig",
    "麻黄": "ephedra",
    "生姜": "ginger",
    "大枣": "jujube",
    "枸杞": "goji berry",
    "菊花": "chrysanthemum",
    "金银花": "honeysuckle",
    "连翘": "forsythia",
    "薄荷": "mint herb",
    "五味子": "schisandra",
    "麦冬": "ophiopogon",
    "党参": "codonopsis",
    "太子参": "pseudostellaria",
    "山药": "chinese yam",
    "莲子": "lotus seed",
    "薏苡仁": "coix seed",
    "砂仁": "amomum",
    "厚朴": "magnolia bark",
    "苍术": "atractylodes lancea",
    "泽泻": "alisma",
    "猪苓": "polyporus",
    "附子": "aconite",
    "干姜": "dried ginger",
    "细辛": "asarum",
    "防风": "saposhnikovia",
    "羌活": "notopterygium",
    "独活": "angelica pubescens",
    "葛根": "kudzu root",
    "升麻": "cimicifuga",
    "石膏": "gypsum",
    "知母": "anemarrhena",
    "栀子": "gardenia fruit",
    "牡丹皮": "moutan bark",
    "桃仁": "peach kernel",
    "红花": "safflower",
    "牛膝": "achyranthes",
    "杜仲": "eucommia bark",
    "续断": "dipsacus",
    "何首乌": "polygonum multiflorum",
    "龙眼": "longan",
    "酸枣仁": "ziziphus seed",
    "远志": "polygala",
    "石菖蒲": "acorus",
    "天麻": "gastrodia",
    "钩藤": "uncaria",
    "全蝎": "scorpion",
    "蜈蚣": "centipede",
    "地龙": "earthworm",
    "蝉蜕": "cicada shell",
    # 题材与形式
    "中药": "chinese medicine",
    "草药": "medicinal herb",
    "药材": "medicinal herb",
    "饮片": "herbal medicine slice",
    "本草": "materia medica",
    "古籍": "ancient chinese book",
    "医书": "medical text",
    "针灸": "acupuncture",
    "经络": "meridian",
    "穴位": "acupoint",
    "药罐": "medicine jar",
    "药碾": "herb grinder",
    "戥子": "apothecary scale",
    "宣纸": "rice paper",
    "水墨": "ink wash",
    "山水": "landscape",
    "花鸟": "bird and flower",
    "人物": "figure painting",
    "竹": "bamboo",
    "松": "pine",
    "梅": "plum blossom",
    "兰": "orchid",
    "荷": "lotus",
    "牡丹": "peony",
    "仙鹤": "crane",
    "鹿": "deer",
    "云纹": "cloud pattern",
    "回纹": "fret pattern",
    "陶罐": "ceramic jar",
    "瓷器": "porcelain",
    "丝绸": "silk",
    "纹理": "texture",
    "纸纹": "paper texture",
}

#: 最长匹配优先。否则「太子参」会被「参」先吃掉，译成 ginseng。
_ZH_KEYS = sorted(ZH2EN, key=len, reverse=True)


def translate(query: str) -> str:
    """中文检索词 → 英文。

    整词命中优先；否则做最长前缀替换（「黄芪饮片」→ ``astragalus`` + 原样尾巴）。
    一句没命中就原样返回——**不编造**。馆藏搜不到自然降级，比译错成别的药安全。
    """

    text = (query or "").strip()
    if not text:
        return ""
    if text in ZH2EN:
        return ZH2EN[text]
    out = text
    for key in _ZH_KEYS:
        if key in out:
            out = out.replace(key, f" {ZH2EN[key]} ")
    normalized = re.sub(r"\s+", " ", out).strip()
    return normalized if normalized != text else text


# ── 缓存 ────────────────────────────────────────────────────────────────────

_CACHE: dict[tuple[Any, ...], tuple[float, list[Candidate]]] = {}


def _cache_get(key: tuple[Any, ...]) -> list[Candidate] | None:
    hit = _CACHE.get(key)
    if not hit:
        return None
    stored_at, value = hit
    if time.monotonic() - stored_at > CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_put(key: tuple[Any, ...], value: list[Candidate]) -> list[Candidate]:
    # 空结果也缓存：一次搜不到通常意味着这个词在这个源里就是没有，反复试是白花时间。
    _CACHE[key] = (time.monotonic(), value)
    return value


def clear_cache() -> None:
    _CACHE.clear()


# ── HTTP ────────────────────────────────────────────────────────────────────


async def _get_json(url: str, params: dict[str, Any] | None = None) -> Any | None:
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url, params=params, headers={"User-Agent": USER_AGENT})
            if response.status_code >= 400:
                logger.info("素材源返回 %s：%s", response.status_code, url)
                return None
            return response.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        logger.info("素材源不可达（%s）：%s", exc.__class__.__name__, url)
        return None


# ── 各渠道 driver ───────────────────────────────────────────────────────────


def _local_candidates(query: str, kind: str, limit: int, *, slot_class: str = "") -> list[Candidate]:
    """本地库检索。

    直接复用 ``select.score`` 的判定思路：subject 命中优先、kind 对齐加分。这里不
    追求排序精致，只要求「搜黄芪先出黄芪」——本地库只有 48 条，模糊匹配够用。
    """

    assets = load_registry()
    if not assets:
        return []
    terms = [item for item in re.split(r"[\s,，、/]+", query.strip()) if item]
    scored: list[tuple[int, Asset]] = []
    for asset in assets:
        if asset.license and asset.license not in LICENSE_ALLOWLIST:
            continue
        if slot_class == "evidence" and asset.slot_class != "evidence":
            continue
        total = 0
        for term in terms:
            if term in asset.subjects:
                total += 100
            elif any(term in subject or subject in term for subject in asset.subjects if len(subject) >= 2):
                total += 60
            if any(term in tag for tag in asset.tags):
                total += 25
        if kind and asset.kind == kind:
            total += 30
        if total > 0:
            scored.append((total, asset))
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [
        Candidate(
            id=asset.id,
            title="、".join(asset.subjects[:2]) or asset.id,
            source="local.herbs" if asset.slot_class == "evidence" else "local.heritage",
            kind=asset.kind,
            url=f"/assets/pictorial/{asset.id}.webp",
            thumb=f"/assets/pictorial/{asset.id}.webp",
            license=asset.license,
            attribution=asset.attribution,
            slot_class=asset.slot_class,
            w=asset.w,
            h=asset.h,
            note=asset.treatment,
        )
        for _, asset in scored[:limit]
    ]


#: Wellcome 的许可字段 → 我们的白名单值。**``pdm`` 就是 Public Domain Mark**，
#: 等效 CC0；写成 CC0 才能在 ``select`` 的许可表里过闸。
_WELLCOME_LICENSE = {"pdm": "CC0", "cc-by": "CC-BY-4.0", "cc-by-nc": "CC-BY-NC-4.0"}

_IIIF_ID = re.compile(r"/image/([^/]+)/")


async def search_wellcome(query: str, limit: int = 8, *, kind: str = "herb") -> list[Candidate]:
    """Wellcome Collection 图像检索。

    这是证据位的主力：``Ming herbal (painting)`` 92 条、《本草品汇精要》明抄彩绘，
    全是 pdm。图像走 IIIF，带 CORS、缓存 28 天。

    许可过滤是硬性的：只放 ``pdm`` / ``cc-by`` 进 ``slot_class=evidence``，
    ``cc-by-nc`` 降级为装饰位——非商业条款不能用来证明「这就是这味药」。
    """

    params = {"query": translate(query), "pageSize": max(limit * 3, 24), "page": 1}
    data = await _get_json("https://api.wellcomecollection.org/catalogue/v2/images", params)
    if not isinstance(data, dict):
        return []

    out: list[Candidate] = []
    for result in data.get("results") or []:
        title = str((result.get("source") or {}).get("title") or "").strip()
        for location in result.get("locations") or []:
            license_id = str((location.get("license") or {}).get("id") or "")
            license_name = _WELLCOME_LICENSE.get(license_id)
            if not license_name or license_name not in LICENSE_ALLOWLIST:
                continue
            match = _IIIF_ID.search(str(location.get("url") or ""))
            if not match:
                continue
            image_id = match.group(1)
            iiif = f"https://iiif.wellcomecollection.org/image/{image_id}/full/1600,/0/default.jpg"
            out.append(
                Candidate(
                    id=_asset_id(_kind_prefix(kind), "wellcome", image_id),
                    title=title or image_id,
                    source="api.wellcome",
                    kind=kind,
                    url=proxy_url(iiif, 1600),
                    thumb=proxy_url(iiif, 400),
                    license=license_name,
                    attribution="Wellcome Collection" + (f" · {title}" if title else ""),
                    slot_class="evidence" if license_name in {"CC0", "CC-BY-4.0"} else "decorative",
                    note=f"IIIF {image_id}",
                    extra={"image_id": image_id, "raw": iiif},
                )
            )
            break  # 一条记录只取第一个可用位置：同一张图的多种分辨率不是多个候选
        if len(out) >= limit:
            break
    return _dedupe(out)[:limit]


async def search_cleveland(query: str, limit: int = 8, *, kind: str = "scene") -> list[Candidate]:
    """Cleveland 美术馆开放接口。免 key、CC0。

    它的强项是**氛围与器物**（中国画 951 件水墨、陶瓷、织物），不是「某味药」——
    搜 ``ginseng`` 只有个位数。所以它进装饰位，不进证据位。
    """

    params = {"q": translate(query), "cc0": 1, "has_image": 1, "limit": max(limit * 2, 10)}
    data = await _get_json("https://openaccess-api.clevelandart.org/api/artworks/", params)
    if not isinstance(data, dict):
        return []

    out: list[Candidate] = []
    for item in data.get("data") or []:
        if str(item.get("share_license_status") or "").upper() != "CC0":
            continue
        images = item.get("images") or {}
        web = (images.get("web") or {}).get("url") or (images.get("print") or {}).get("url") or ""
        if not web:
            continue
        title = str(item.get("title") or "").strip()
        culture = "、".join(item.get("culture") or []) or ""
        technique = str(item.get("technique") or "").strip()
        out.append(
            Candidate(
                id=_asset_id(_kind_prefix(kind), "cleveland", str(item.get("id") or web)),
                title=title or "馆藏图像",
                source="api.cleveland",
                kind=kind,
                url=proxy_url(web, 1600),
                thumb=proxy_url(web, 400),
                license="CC0",
                attribution=f"Cleveland Museum of Art · {title}",
                slot_class="decorative",
                note=" · ".join(part for part in (culture, technique, str(item.get("creation_date") or "")) if part),
                extra={"accession": item.get("accession_number") or "", "raw": web},
            )
        )
    return _dedupe(out)[:limit]


async def search_iconify(query: str, limit: int = 8, *, prefixes: str = "fluent,ph,mdi") -> list[Candidate]:
    """Iconify 图标检索。返回 SVG 地址，``currentColor`` 直接跟随文字色。

    微软 Fluent 排第一：它是 Office 图表与界面图标那套，最贴「教学材料」的语气。
    """

    term = translate(query)
    data = await _get_json(
        "https://api.iconify.design/search",
        {"query": term, "limit": max(limit * 4, 32), "prefixes": prefixes},
    )
    # 指定前缀常常是 0 条（``herb`` 在 fluent/ph/mdi 里一个都没有），这不是「没有图标」，
    # 而是「这三个集没收这个词」。所以空结果要放开前缀再搜一次，并按偏好重排。
    if isinstance(data, dict) and not data.get("icons"):
        data = await _get_json(
            "https://api.iconify.design/search",
            {"query": term, "limit": max(limit * 4, 32)},
        )
    if not isinstance(data, dict):
        return []
    preferred = ("fluent", "ph", "mdi", "material-symbols", "healthicons")
    names = [name for name in (data.get("icons") or []) if isinstance(name, str) and ":" in name]
    names.sort(key=lambda name: preferred.index(name.split(":", 1)[0]) if name.split(":", 1)[0] in preferred else 99)
    out: list[Candidate] = []
    for name in names:
        if ":" not in name:
            continue
        prefix, icon = name.split(":", 1)
        svg = f"https://api.iconify.design/{prefix}/{icon}.svg"
        out.append(
            Candidate(
                id=_asset_id("motif", "iconify", name),
                title=name,
                source="api.iconify",
                kind="icon",
                url=svg,
                thumb=svg,
                license=ICON_LICENSES.get(prefix, "CC-BY-4.0"),
                attribution=f"Iconify · {prefix}",
                slot_class="decorative",
                note="currentColor，可直接跟随文字色",
            )
        )
    return out[:limit]


async def search_phylopic(query: str, limit: int = 6) -> list[Candidate]:
    """PhyloPic 生物剪影。**必须带 build 参数**，否则 307 后内容取不到。"""

    data = await _get_json(
        "https://api.phylopic.org/images",
        {"filter_name": translate(query), "build": 562, "page": 0},
    )
    if not isinstance(data, dict):
        return []
    items = (data.get("_embedded") or {}).get("items") or []
    out: list[Candidate] = []
    for item in items[:limit]:
        files = ((item.get("_links") or {}).get("thumbnailFiles") or [{}])[0]
        href = str(files.get("href") or "")
        if not href:
            continue
        out.append(
            Candidate(
                id=_asset_id("motif", "phylopic", str(item.get("uuid") or href)),
                title=str((item.get("attribution") or "silhouette"))[:60],
                source="api.phylopic",
                kind="silhouette",
                url=href,
                thumb=href,
                license="CC0",
                attribution=str(item.get("attribution") or "PhyloPic"),
                slot_class="decorative",
                note="单色剪影",
            )
        )
    return out


#: C 类站点的分类页白名单。模型不能自己拼 URL——只能在登记过的分类里点名。
CATEGORY_PAGES: dict[str, dict[str, str]] = {
    "web.pngimg": {
        "nature": "https://pngimg.com/images/nature",
        "flowers": "https://pngimg.com/images/flowers",
        "objects": "https://pngimg.com/images/objects",
        "tableware": "https://pngimg.com/images/tableware",
        "vegetables": "https://pngimg.com/images/vegetables",
        "food": "https://pngimg.com/images/food",
    },
    "web.opendoodles": {
        "all": "https://www.opendoodles.com/",
    },
}

#: 图标集 → 许可。**逐集写清楚**，不要用「非首选就 CC-BY」这种偷懒规则：
#: 写错许可比找不到图标严重得多——它会让审计以为要署名，实际不用，或者反过来。
ICON_LICENSES: dict[str, str] = {
    "fluent": "MIT",
    "ph": "MIT",
    "mdi": "Apache-2.0",
    "material-symbols": "Apache-2.0",
    "healthicons": "CC0",
    "tabler": "MIT",
    "lucide": "ISC",
}

_IMG_SRC = re.compile(r'src="([^"]+\.(?:png|jpg|jpeg|webp|svg))"', re.I)


async def fetch_category(source: str, category: str, limit: int = 8) -> list[Candidate]:
    """C 类渠道：按分类页点名取图。

    没有检索，所以**只有登记过的分类能取**。取到的直链一律进 wsrv：
    pngimg 的原图没有 CORS，前端 fetch 不到，导出时会静默丢图。

    许可如实标注：pngimg 是 CC BY-NC 4.0，只能进装饰位，且必须署名。
    """

    pages = CATEGORY_PAGES.get(source) or {}
    url = pages.get(category)
    if not url:
        return []
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": USER_AGENT})
            if response.status_code >= 400:
                return []
            html = response.text
    except httpx.HTTPError:
        return []

    out: list[Candidate] = []
    for match in _IMG_SRC.finditer(html):
        src = match.group(1)
        if src.startswith("//"):
            src = f"https:{src}"
        elif src.startswith("/"):
            src = f"https://{url.split('/')[2]}{src}"
        if not src.startswith("http"):
            continue
        name = src.rsplit("/", 1)[-1]
        out.append(
            Candidate(
                id=_asset_id("photo", source, src),
                title=name.rsplit(".", 1)[0].replace("-", " ")[:40],
                source=source,
                kind="photo",
                url=proxy_url(src, 1200),
                thumb=proxy_url(src, 400),
                license="CC-BY-NC-4.0" if source == "web.pngimg" else "CC0",
                attribution=url,
                slot_class="decorative",
                note="透明底抠图，建议按 line 配方处理后再上版面" if source == "web.pngimg" else "",
                extra={"raw": src},
            )
        )
        if len(out) >= limit:
            break
    return _dedupe(out)


def _dedupe(items: list[Candidate]) -> list[Candidate]:
    seen: set[str] = set()
    out: list[Candidate] = []
    for item in items:
        if item.id in seen:
            continue
        seen.add(item.id)
        out.append(item)
    return out


# ── 统一入口 ────────────────────────────────────────────────────────────────

#: 每条渠道的实现。没有实现的渠道（缺 key、禁再配布）不在表里，调用返回空。
_DRIVERS = {
    "api.wellcome": search_wellcome,
    "api.cleveland": search_cleveland,
    "api.iconify": search_iconify,
    "api.phylopic": search_phylopic,
}


async def search(
    source: str,
    query: str,
    *,
    kind: str = "",
    limit: int = 8,
    category: str = "",
    slot_class: str = "",
) -> list[Candidate]:
    """``search_assets`` 的真实实现。

    ``source="auto"`` 时按 ``kind`` 查 ``channels.ROUTES`` 逐条试，**第一条有货就停**：
    这是「成本从低到高」的排序，不是打分。已实测本地库先于远程，参数化先于生图。

    任何一条渠道抛异常都在这里兜住——工具层不该让一份稿子失败。
    """

    key = (source, query, kind, limit, category, slot_class)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    try:
        results = await _dispatch(source, query, kind=kind, limit=limit, category=category, slot_class=slot_class)
    except Exception as exc:  # noqa: BLE001 - 素材取不到不是致命错误
        logger.info("素材检索失败（%s/%s）：%s", source, query, exc)
        results = []
    return _cache_put(key, results)


async def _dispatch(
    source: str,
    query: str,
    *,
    kind: str,
    limit: int,
    category: str,
    slot_class: str,
) -> list[Candidate]:
    if source in {"local", "local.herbs", "local.heritage"}:
        wanted = "evidence" if source == "local.herbs" else "decorative" if source == "local.heritage" else slot_class
        return _local_candidates(query, kind, limit, slot_class=wanted)

    if source in CATEGORY_PAGES:
        return await fetch_category(source, category or "natural", limit)

    if source == "auto":
        purpose = KIND_PURPOSE.get(kind or "", "oriental-deco")
        # 参数化母题与字体没有「候选图」，它们由渲染层直接画；跳过。
        tried: list[Candidate] = []
        for channel in channels.route(purpose):
            if channel.id in {"param.motif", "font.pair", "gen.image"}:
                continue
            found = await _dispatch(
                channel.id,
                query,
                kind=kind,
                limit=limit - len(tried),
                category=category,
                slot_class=slot_class,
            )
            tried.extend(found)
            if len(tried) >= limit:
                break
        return _dedupe(tried)[:limit]

    driver = _DRIVERS.get(source)
    if not driver:
        return []
    if source in {"api.wellcome", "api.cleveland"}:
        return await driver(query, limit, kind=kind)  # type: ignore[operator]
    return await driver(query, limit)  # type: ignore[operator]


# ── 客观指标 ────────────────────────────────────────────────────────────────


def measure(path: Path) -> dict[str, Any]:
    """本地图的客观指标：尺寸、长宽比、留白率、边缘密度。

    这些不替代「看一眼」，但它们**便宜且不会幻觉**：长宽比不对、整张是空白、
    边缘密度过低（模糊），这几种废图靠指标就能先筛掉一半。
    """

    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - 环境缺 Pillow 时静默降级
        return {}

    try:
        with Image.open(path) as image:
            width, height = image.size
            gray = image.convert("L").resize((64, 64))
            pixels = list(gray.getdata())
    except (OSError, ValueError):
        return {}

    total = len(pixels) or 1
    light = sum(1 for value in pixels if value > 235) / total
    edges = 0
    for row in range(63):
        for col in range(63):
            here = pixels[row * 64 + col]
            if abs(here - pixels[row * 64 + col + 1]) > 24 or abs(here - pixels[(row + 1) * 64 + col]) > 24:
                edges += 1
    return {
        "w": width,
        "h": height,
        "aspect": round(width / height, 3) if height else 0,
        "blank": round(light, 3),
        "edges": round(edges / (63 * 63), 3),
    }


def registry_size() -> int:
    """登记表条数。给工具描述里写「本地有几张」用，不写死数字。"""

    return len(load_registry())


def registry_digest() -> str:
    path = registry_path()
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return "none"
