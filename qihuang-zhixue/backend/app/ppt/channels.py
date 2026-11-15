"""素材渠道目录：**模型能选的全部链路，以及每条链路的规矩**。

这份目录不是给人看的清单，是**给模型读的菜单**——它决定了模型在「这一页需要一张人参
图」时知道自己有哪几个地方可以去、按什么顺序试、什么时候必须停手。

## 三条设计原则

1. **渠道 ≠ 图片**。渠道是「去哪儿拿」，具体拿哪张由 `select` 决定。模型只提需求，
   不许自己编 asset id——编出来的引用会静默兜底成空白页。
2. **落盘与否先于内容**。一条渠道能不能把文件存进仓库，决定它能进哪个槽位，比它能
   找到什么更重要。`linustock` 的线描再合适也只能 pin URL，因为许可禁止再配布。
3. **顺序是规则，不是建议**。`ROUTES` 里的顺序会被执行器逐条试，试完仍无解才降级。
   让模型自由挑「从哪开始找」，等于让它在每页重新发明一遍成本模型。

## 为什么分 A/B/C 三类

A 本地库零成本零风险，B 有检索 API 可以换词重搜，C 只有固定的分类页、搜不到就是没有。
三类的能力边界不同，混成一个列表会让模型对 C 类产生「再搜一次就有了」的错觉。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

#: 渠道大类。A 本地 / B 有检索 API / C 无检索站点 / D 生成 / E 参数化 / F 字体。
ChannelClass = Literal["A", "B", "C", "D", "E", "F"]

#: 用途标签。模型按用途查路由，不按渠道名找。
Purpose = Literal[
    "herb-evidence",   # 某味药的实证画面（根 / 茎 / 饮片 / 炮制）
    "oriental-deco",   # 中式装饰：古籍图谱、国画、器物
    "western-deco",    # 西式复古插画
    "icon",            # 图标与符号
    "silhouette",      # 单色剪影
    "texture",         # 纸纹、布料、材质
    "photo",           # 通用实拍
    "pattern",         # 纹样、底纹
    "hand-drawn",      # 手绘小元素
]

#: 落盘策略。决定这条渠道的产物能否进 `registry.json`。
Persist = Literal["library", "url-only", "memory-only"]


@dataclass(frozen=True)
class Channel:
    """一条渠道。字段刻意少：模型要在一次上下文里读完十九条。"""

    id: str
    cls: ChannelClass
    label: str
    what: str  #: 能给什么，一句话
    how: str  #: 怎么取，一句话
    purposes: tuple[Purpose, ...]
    license: str
    persist: Persist
    slot_classes: tuple[str, ...]  #: 能进哪些槽位（evidence / decorative）
    status: Literal["ready", "verified", "blocked"]
    note: str = ""
    entry: str = ""  #: 入口 URL 或 API 根
    scale: str = ""  #: 规模

    @property
    def usable_for_evidence(self) -> bool:
        return "evidence" in self.slot_classes


#: 十九条渠道。顺序按「先本地、再 API、再无接口、最后生成」排，与执行顺序一致。
CHANNELS: tuple[Channel, ...] = (
    # ── A 本地库 ────────────────────────────────────────────────────────
    Channel(
        id="local.heritage",
        cls="A",
        label="Heritage Library 复古插画",
        what="修复过的彩版博物画：花鸟、海星贝壳、陶罐花器、浮世绘菊、水墨飞燕",
        how="按 subject 查 registry.json，直接拿 file",
        purposes=("western-deco", "oriental-deco"),
        license="Heritage-Free",
        persist="library",
        slot_classes=("decorative",),
        status="ready",
        note="CC0 但明文禁止再配布素材文件本身；只在成品里用，不提供下载",
        scale="43 条",
    ),
    Channel(
        id="local.herbs",
        cls="A",
        label="饮片实拍（本地 CC0）",
        what="黄芪、党参、太子参、陈皮、金银花的饮片实拍",
        how="按 subject 查 registry，slot_class=evidence",
        purposes=("herb-evidence",),
        license="CC0",
        persist="library",
        slot_classes=("evidence", "decorative"),
        status="ready",
        note="只覆盖五味药；缺的药必须降级，不许拿别的药顶替",
        scale="5 条",
    ),
    Channel(
        id="local.ornaments",
        cls="A",
        label="中式装饰纹样（本地 CC0 矢量）",
        what="真素材装饰：角花（古典/藤蔓/对称）、回纹框与横带、花草边带、剪纸底纹、卍字底纹",
        how="按 subject/keywords 查 registry（kind=ornament / ground-texture），直接拿 file",
        purposes=("corner-ornament", "border-band", "ground-texture", "oriental-deco"),
        license="CC0",
        persist="library",
        slot_classes=("decorative",),
        status="ready",
        note="25 条人工策展（Openclipart 公有领域）。检索词用标签语言：角花/角饰/回纹/横带/边框/剪纸/底纹/卍字",
        scale="25 条",
    ),
    # ── B 有检索 API ────────────────────────────────────────────────────
    Channel(
        id="api.wellcome",
        cls="B",
        label="Wellcome Collection",
        what="中医古籍图谱：《本草品汇精要》明抄彩绘 92 张、《本草纲目》1590 刻本 148 张、针灸图、17 世纪本草",
        how="GET /catalogue/v2/images?query=；图像走 IIIF /image/{id}/full/1200,/0/default.jpg",
        purposes=("herb-evidence", "oriental-deco"),
        license="pdm / cc-by / cc-by-nc（逐条取 license 字段）",
        persist="url-only",
        slot_classes=("evidence", "decorative"),
        status="verified",
        note="IIIF 带 CORS、缓存 28 天；只取 pdm 与 cc-by，cc-by-nc 仅限装饰位",
        entry="https://api.wellcomecollection.org/catalogue/v2/images",
        scale="300+ 条",
    ),
    Channel(
        id="api.cleveland",
        cls="B",
        label="Cleveland Museum of Art",
        what="中国书画绢本、陶瓷、器物；`chinese painting` 命中 951 件水墨",
        how="GET /api/artworks?q=；原图建议经 wsrv 代理",
        purposes=("oriental-deco", "pattern"),
        license="CC0",
        persist="url-only",
        slot_classes=("decorative",),
        status="verified",
        note="免 key；API 有 CORS，原图无 CORS 需 wsrv；1600px 代理后约 32KB",
        entry="https://openaccess-api.clevelandart.org/api",
    ),
    Channel(
        id="api.iconify",
        cls="B",
        label="Iconify（含微软 Fluent / 菜鸟 / Phosphor）",
        what="二十万级图标，SVG，支持 currentColor 自动跟随文字色",
        how="GET /{prefix}:{name}.svg；前缀列表见 collections",
        purposes=("icon",),
        license="按图标集（Fluent 为 MIT，菜鸟为自定义）",
        persist="library",
        slot_classes=("decorative",),
        status="verified",
        note="图标首选。MIT 类可落盘，自定义许可的只 pin URL",
        entry="https://api.iconify.design",
    ),
    Channel(
        id="api.smithsonian",
        cls="B",
        label="Smithsonian Open Access",
        what="植物标本、民俗器物、馆藏照片",
        how="GET /openaccess/api/v1.0/search?q=&api_key=",
        purposes=("photo", "oriental-deco"),
        license="CC0 为主，部分受限",
        persist="url-only",
        slot_classes=("decorative",),
        status="verified",
        note="DEMO_KEY 限 30 次/小时，正式使用需申请 key",
        entry="https://api.si.edu/openaccess/api/v1.0",
    ),
    Channel(
        id="api.met",
        cls="B",
        label="The Met",
        what="中国陶瓷、绘画、织物",
        how="GET /public/collection/v1.1/search?q=&limit=（v1 已于 2026-10 退休）",
        purposes=("oriental-deco", "pattern"),
        license="CC0（须查 isPublicDomain）",
        persist="url-only",
        slot_classes=("decorative",),
        status="verified",
        note="命中率高但公开域比例低，必须先看 isPublicDomain 再取图",
        entry="https://collectionapi.metmuseum.org/public/collection/v1.1",
    ),
    Channel(
        id="api.vam",
        cls="B",
        label="V&A",
        what="中国纹样、外销瓷、装饰图案",
        how="GET /v2/objects/search?q=&page_size=",
        purposes=("pattern", "oriental-deco"),
        license="多为 CC BY",
        persist="url-only",
        slot_classes=("decorative",),
        status="verified",
        note="适合做纹样底，不适合做主图；CC BY 必须带 attribution",
        entry="https://api.vam.ac.uk/v2",
    ),
    Channel(
        id="api.phylopic",
        cls="B",
        label="PhyloPic",
        what="生物单色剪影（动植物轮廓）",
        how="GET /images?filter_name={分类单元}&build=562",
        purposes=("silhouette", "icon"),
        license="CC0 / CC BY",
        persist="url-only",
        slot_classes=("decorative",),
        status="verified",
        note="必须带 build 参数，否则 307 重定向后取不到内容；适合章节页极简符号",
        entry="https://api.phylopic.org",
    ),
    # ── C 无检索站点 ────────────────────────────────────────────────────
    Channel(
        id="web.opendoodles",
        cls="C",
        label="Open Doodles",
        what="手绘涂鸦人物与小物",
        how="按站点分组页取名，无检索",
        purposes=("hand-drawn", "western-deco"),
        license="商用与个人均免费，无需署名",
        persist="library",
        slot_classes=("decorative",),
        status="verified",
        note="九条 C 类里唯一许可最干净的一条，可以落盘",
        entry="https://www.opendoodles.com",
    ),
    Channel(
        id="web.pngimg",
        cls="C",
        label="pngimg",
        what="透明底实物抠图（约 12 万张，34 个分类，**无中草药类目**）",
        how="只能按分类页取；直链无 CORS，必须经 wsrv 代理",
        purposes=("photo", "icon"),
        license="CC BY-NC 4.0",
        persist="url-only",
        slot_classes=("decorative",),
        status="verified",
        note="演示期放行（记入 DEMO_ONLY_LICENSES），商业化前必须逐条替换；无搜索接口",
        entry="https://pngimg.com",
        scale="约 12 万",
    ),
    Channel(
        id="web.unsplash",
        cls="C",
        label="Unsplash",
        what="通用实拍照片、纹理",
        how="图床直链 https://images.unsplash.com/photo-{id}；官方 API 需 key",
        purposes=("photo", "texture"),
        license="Unsplash License（可商用、不可转售）",
        persist="url-only",
        slot_classes=("decorative",),
        status="verified",
        note="`source.unsplash.com` 已下线；没有 key 就没有检索，只能走人工清单",
        entry="https://unsplash.com",
    ),
    Channel(
        id="web.pexels",
        cls="C",
        label="Pexels",
        what="通用实拍照片",
        how="CDN 直链可用，API 暂停签发",
        purposes=("photo", "texture"),
        license="Pexels License",
        persist="url-only",
        slot_classes=("decorative",),
        status="blocked",
        note="CDN 200 + CORS + 1 年缓存，但 API 不签发，只能人工准备 URL 清单",
        entry="https://www.pexels.com",
    ),
    Channel(
        id="web.linustock",
        cls="C",
        label="Linustock",
        what="日式线描插画",
        how="按分类页取，只能 pin URL",
        purposes=("hand-drawn",),
        license="禁止再配布",
        persist="url-only",
        slot_classes=("decorative",),
        status="verified",
        note="**只能运行时引用，绝不能下载进仓库**——进了就是再配布",
        entry="https://www.linustock.com",
    ),
    Channel(
        id="web.enpitsu-sozai",
        cls="C",
        label="えんぴつ素材",
        what="铅笔手绘小素材",
        how="按分类页取，只能 pin URL",
        purposes=("hand-drawn",),
        license="禁止再配布",
        persist="url-only",
        slot_classes=("decorative",),
        status="verified",
        note="同上，只 pin 不落盘",
        entry="https://enpitsu-sozai.com",
    ),
    Channel(
        id="web.rakugakiicon",
        cls="C",
        label="らくがきアイコン",
        what="涂鸦风图标",
        how="按分类页取，只能 pin URL",
        purposes=("hand-drawn", "icon"),
        license="禁止再配布",
        persist="url-only",
        slot_classes=("decorative",),
        status="verified",
        note="同上，只 pin 不落盘",
        entry="https://rakugakiicon.com",
    ),
    # ── D 生成 ──────────────────────────────────────────────────────────
    Channel(
        id="gen.image",
        cls="D",
        label="文生图",
        what="无对应素材时的兜底灵感图",
        how="POST 生图接口，返回文件路径",
        purposes=("western-deco", "hand-drawn"),
        license="自产",
        persist="memory-only",
        slot_classes=("decorative",),
        status="ready",
        note="每份稿 ≤3 张；**禁止进证据位**；必须记 prompt/model/seed，否则不可复现",
    ),
    # ── E 参数化 ────────────────────────────────────────────────────────
    Channel(
        id="param.motif",
        cls="E",
        label="参数化母题",
        what="纸底 3、远山 2、角花 3、界格 2、印章 1、水纹 1，共 14 个，代码现场生成 SVG",
        how="给母题 id + 参数对象，渲染层出 SVG",
        purposes=("texture", "pattern", "icon"),
        license="无（自产）",
        persist="library",
        slot_classes=("decorative",),
        status="ready",
        note="零成本零风险，**应当优先于任何远程渠道**；同一母题换参数即换样子",
        scale="14 个",
    ),
    # ── F 字体 ──────────────────────────────────────────────────────────
    Channel(
        id="font.pair",
        cls="F",
        label="字体对",
        what="八套「标题 + 正文」组合：宋楷、楷宋、黑宋、思源宋、思源黑、仿宋、隶楷、行楷",
        how="设计单里给 type_pair id，渲染层读两个 CSS 变量",
        purposes=("icon",),
        license="系统字体",
        persist="library",
        slot_classes=("decorative",),
        status="ready",
        note="**PPTX 不能嵌入字体**，导出只写字体名，放映端没有就替换",
        scale="8 套",
    ),
)


#: 用途 → 渠道优先序。执行器逐条试，全试完仍无解才降级。
#: 顺序不是偏好，是成本：本地 → 有检索 → 无检索 → 生成。
ROUTES: dict[str, tuple[str, ...]] = {
    "herb-evidence": ("local.herbs", "api.wellcome"),
    "corner-ornament": ("local.ornaments", "param.motif"),
    "border-band": ("local.ornaments", "param.motif"),
    "ground-texture": ("local.ornaments", "param.motif"),
    "oriental-deco": ("local.ornaments", "param.motif", "api.wellcome", "api.cleveland", "api.met", "api.vam", "local.heritage"),
    "western-deco": ("local.heritage", "web.opendoodles", "gen.image"),
    "icon": ("param.motif", "api.iconify", "api.phylopic", "web.rakugakiicon"),
    "silhouette": ("param.motif", "api.phylopic"),
    "texture": ("local.ornaments", "param.motif", "web.unsplash", "web.pexels"),
    "photo": ("web.unsplash", "web.pexels", "web.pngimg", "api.smithsonian"),
    "pattern": ("local.ornaments", "param.motif", "api.vam", "api.met", "api.cleveland"),
    "hand-drawn": ("web.opendoodles", "web.linustock", "web.enpitsu-sozai", "param.motif"),
}

#: 证据位只能用这两条。写死在代码里，不靠提示词自觉。
EVIDENCE_CHANNELS: frozenset[str] = frozenset({"local.herbs", "api.wellcome"})


def route(purpose: str) -> tuple[Channel, ...]:
    """按用途取渠道序列。未登记的用途返回空——空即降级，不猜。"""
    ids = ROUTES.get(purpose, ())
    by_id = {channel.id: channel for channel in CHANNELS}
    return tuple(by_id[cid] for cid in ids if cid in by_id)


def by_id(channel_id: str) -> Channel | None:
    return next((channel for channel in CHANNELS if channel.id == channel_id), None)


def catalog() -> dict[str, object]:
    """给模型读的紧凑目录。按类分组，每组只留模型需要的字段。"""
    groups: dict[str, list[dict[str, object]]] = {}
    for channel in CHANNELS:
        groups.setdefault(channel.cls, []).append(
            {
                "id": channel.id,
                "what": channel.what,
                "how": channel.how,
                "license": channel.license,
                "persist": channel.persist,
                "slots": list(channel.slot_classes),
                "status": channel.status,
                "note": channel.note,
            }
        )
    return {
        "classes": {
            "A": "本地库，零成本零风险，优先",
            "B": "有检索 API，搜不到可以换词重搜",
            "C": "无检索站点，只能在已知分类页里点名，搜不到就是没有",
            "D": "生成，兜底，有配额",
            "E": "参数化母题，代码现场画",
            "F": "字体对",
        },
        "channels": groups,
        "routes": {key: list(value) for key, value in ROUTES.items()},
        "evidence_channels": sorted(EVIDENCE_CHANNELS),
        "total": len(CHANNELS),
    }


#: 给模型的渠道说明。与目录同源，改了目录这句不会漂。
SYSTEM_HINT = """\
你可以从这些渠道取素材，按用途查 routes：
- A 本地库（local.herbs / local.heritage）零成本，先查；
- B 有检索 API（wellcome / cleveland / iconify / phylopic …）可以换词重搜；
- C 无检索站点只能在分类页里点名，搜不到就是没有，别反复试；
- E 参数化母题优先于一切远程渠道，纸纹、角花、界格都该自己画；
- D 生成是兜底，一份稿最多 3 张，且只能是装饰。

硬规矩：
1. 证据位（讲「这就是某味药」）只能用 local.herbs 与 api.wellcome 的 pdm 条目；
   查不到就降级成无图版式，**不许拿别的药或"通用植物图"顶替**。
2. persist=url-only 的渠道只能把 URL 钉进稿子，不许下载进仓库；
   persist=library 才能落盘。
3. 先 look 再 assign：取到候选先看主体对不对，不对就换词重搜；
   不许未看图就占位。
4. 一份稿最多 12 轮取素材，单页引用不超过 2 张，生成图不超过 3 张。
"""


def write_json(target: Path | None = None) -> Path:
    """把目录写成 `channels.json`。给人看与前端读，不参与运行时判断。"""
    path = target or Path(__file__).resolve().parents[3] / "docs" / "studios" / "channels.json"
    payload = {
        "version": 1,
        "note": "由 backend/app/ppt/channels.py 生成，不要手改。",
        "channels": [
            {**asdict(channel), "purposes": list(channel.purposes), "slot_classes": list(channel.slot_classes)}
            for channel in CHANNELS
        ],
        "routes": {key: list(value) for key, value in ROUTES.items()},
        "hint": SYSTEM_HINT,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


if __name__ == "__main__":
    print(write_json())
