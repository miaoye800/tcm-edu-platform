"""素材工具层：不联网也能验的部分全部在这里。

打真网的测试一律不写——它会随对方接口抖动而红，也会烧额度。所以每个 driver 都用
「喂一份真响应样本」的方式验解析、许可映射与 id 稳定性。
"""

from __future__ import annotations

import asyncio

import pytest

from app.ppt import materials


@pytest.fixture(autouse=True)
def _clear() -> None:
    materials.clear_cache()


def test_proxy_url_is_idempotent_and_encodes() -> None:
    raw = "https://openaccess-cdn.clevelandart.org/1969.116/1969.116_web.jpg"
    proxied = materials.proxy_url(raw, 1600)
    assert proxied.startswith("https://wsrv.nl/?url=")
    assert "w=1600" in proxied and "output=webp" in proxied
    # 原图 URL 必须整段编码，否则它自带的 ? & 会把外层参数截断。
    assert "?" not in proxied.split("url=", 1)[1].split("&w=")[0]
    assert materials.proxy_url(proxied, 400) == proxied, "已经是代理地址就不要再包一层"


def test_asset_id_is_stable_and_prefixed() -> None:
    first = materials.asset_id_of("plate", "wellcome", "L0039301")
    assert first == materials.asset_id_of("plate", "wellcome", "L0039301")
    assert first != materials.asset_id_of("plate", "wellcome", "L0039302")
    # 场景层对 asset 命名有硬校验（必须以 plate-/photo-/... 开头）。
    assert first.startswith("plate-")


@pytest.mark.parametrize(
    ("query", "expected"),
    [("黄芪", "astragalus"), ("人参", "ginseng"), ("水墨", "ink wash"), ("当归", "angelica sinensis")],
)
def test_translate_hits(query: str, expected: str) -> None:
    assert materials.translate(query) == expected


def test_translate_keeps_unknown_words() -> None:
    # 译不出来就原样返回。编一个近义词比搜不到更危险。
    assert materials.translate("气机升降") == "气机升降"


def test_translate_prefers_longest_match() -> None:
    # 「太子参」里有「参」，但整词表里有它，不能被拆碎。
    assert materials.translate("太子参") == "pseudostellaria"


def test_local_search_finds_herb_and_marks_evidence() -> None:
    found = materials._local_candidates("黄芪", "", 5)
    assert found, "本地库应有黄芪"
    assert found[0].slot_class == "evidence"
    assert found[0].license == "CC0"


def test_local_search_respects_evidence_filter() -> None:
    # 装饰素材不能顶进证据位——哪怕搜索词对得上。
    found = materials._local_candidates("菊花", "", 5, slot_class="evidence")
    assert all(item.slot_class == "evidence" for item in found)


def test_wellcome_maps_license_and_downgrades_nc(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "results": [
            {
                "source": {"title": "Panax ginseng"},
                "locations": [
                    {"license": {"id": "pdm"}, "url": "https://iiif.wellcomecollection.org/image/L1/full/1,/0/default.jpg"},
                ],
            },
            {
                "source": {"title": "Some herb photo"},
                "locations": [
                    {"license": {"id": "cc-by-nc"}, "url": "https://iiif.wellcomecollection.org/image/L2/full/1,/0/default.jpg"},
                ],
            },
            {
                "source": {"title": "no license"},
                "locations": [{"url": "https://iiif.wellcomecollection.org/image/L3/full/1,/0/default.jpg"}],
            },
        ]
    }

    async def fake_get(url, params=None):  # noqa: ANN001
        return payload

    monkeypatch.setattr(materials, "_get_json", fake_get)
    found = asyncio.run(materials.search_wellcome("人参", 5))
    assert len(found) == 2, "没有许可字段的条目必须丢掉"
    pdm, nc = found[0], found[1]
    assert pdm.license == "CC0" and pdm.slot_class == "evidence"
    assert nc.license == "CC-BY-NC-4.0" and nc.slot_class == "decorative", "非商业许可不能进证据位"
    assert pdm.url.startswith("https://wsrv.nl/"), "远程图必须走代理，否则导出时取不到"


def test_search_returns_empty_on_network_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(url, params=None):  # noqa: ANN001
        raise RuntimeError("net down")

    monkeypatch.setattr(materials, "_get_json", boom)
    assert asyncio.run(materials.search("api.cleveland", "山水")) == []


def test_search_caches_empty_result(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def counted(source, query, *, kind="", limit=8, category="", slot_class=""):
        calls["n"] += 1
        return []

    monkeypatch.setattr(materials, "_dispatch", counted)
    asyncio.run(materials.search("api.iconify", "不存在的词"))
    asyncio.run(materials.search("api.iconify", "不存在的词"))
    assert calls["n"] == 1, "空结果也要缓存，否则同一份稿子里会反复打同一个源"


def test_measure_reports_blank_and_size(tmp_path) -> None:  # noqa: ANN001
    pytest.importorskip("PIL")
    from PIL import Image

    path = tmp_path / "blank.png"
    Image.new("RGB", (400, 300), "white").save(path)
    stats = materials.measure(path)
    assert stats["w"] == 400 and stats["h"] == 300
    assert stats["blank"] > 0.95, "纯白页应当被判成空白"
    assert stats["edges"] < 0.05


def test_every_route_channel_is_known() -> None:
    from app.ppt import channels

    for purpose, order in channels.ROUTES.items():
        for channel_id in order:
            assert channels.by_id(channel_id) is not None, f"{purpose} 指向了不存在的渠道 {channel_id}"
