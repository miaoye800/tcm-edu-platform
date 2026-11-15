"""P2（模型 · 坐标 · 资产）的验收测试。

守四件事，每件都是"错了但看不出来"的那种：

1. **二进制布局**：我们自己打包的 skin.bin 必须能被按偏移读回来，否则 P3 前端
   加载会得到一坨乱码，而且只有在浏览器里才炸。
2. **重心坐标自洽**：``(skin_tri, bary)`` 重建出来的位置要等于原始坐标。这条是
   "换模型时 362 穴不漂" 的唯一保障。
3. **首屏体积**：≤ 3 MB 是 P0 定下的硬门禁，不能靠"看起来挺小"。
4. **精度边界如实暴露**：``verified`` 全 0、``coord_review.csv`` 有内容、
   ``/point`` 对软引用给 404 而不是假装成功——**不假装精确**本身就是验收项。
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pytest

from app.meridian import asset, coords, crosscheck, store

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "meridian"


def _has_atlas() -> bool:
    return (OUT / "manifest.json").exists() and (OUT / "skin.bin").exists()


requires_assets = pytest.mark.skipif(
    not _has_atlas(), reason="先跑 python -m app.meridian.asset --build"
)


# --------------------------------------------------------------------------- 几何


def test_pack_and_read_back_are_identical() -> None:
    """打包 → 按 atlas 偏移读回，顶点与索引必须逐字节一致。"""

    pos = np.array([[0.0, 1.0, 2.0], [1.0, 2.0, 3.0], [2.0, 3.0, 4.0]], dtype=np.float32)
    nrm = np.array([[0, 32767, 0], [0, 32767, 0], [0, 32767, 0]], dtype=np.int16)
    idx = np.array([[0, 1, 2]], dtype=np.uint32)
    payload = asset.pack_bin(pos, nrm, idx)

    # 按上游布局（pos → pad → nrm → pad → idx）反解
    restored_pos = np.frombuffer(payload[: pos.nbytes], dtype="<f4").reshape(-1, 3)
    offset = pos.nbytes + (-pos.nbytes) % 4
    restored_nrm = np.frombuffer(payload[offset : offset + nrm.nbytes], dtype="<i2").reshape(-1, 3)
    offset += nrm.nbytes + (-nrm.nbytes) % 4
    restored_idx = np.frombuffer(payload[offset : offset + idx.nbytes], dtype="<u4").reshape(-1, 3)

    assert np.array_equal(restored_pos, pos)
    assert np.array_equal(restored_nrm, nrm)
    assert np.array_equal(restored_idx, idx)


def test_vertex_normals_point_outward() -> None:
    """重算的法线要跟三角形法线同向，否则光照会反（肉看起来是黑的）。"""

    pos = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    idx = np.array([[0, 1, 2]], dtype=np.uint32)
    nrm = asset.vertex_normals(pos, idx)
    # 逆时针三角形的法线指向 +z
    assert nrm[:, 2].min() > 32000


def test_vertex_normals_never_degenerate() -> None:
    """零长度法线一个都不能留。

    法线互相抵消的顶点（薄片、尖角、孤立面）如果原样留成 (0,0,0)，前端
    `MeshStandardMaterial` 对它做 `normalize` 得到 NaN；裸渲染只是那几个面黑掉，
    一旦叠上 bloom，NaN 会被半浮点模糊链加性摊满整帧 —— 整屏透明。
    这条断言守的就是那次全黑。
    """

    # 一片正反两个三角形完全对消的"纸"，中间顶点法线必然为 0
    pos = np.array(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        dtype=np.float32,
    )
    idx = np.array([[0, 1, 2], [0, 2, 1], [0, 1, 3]], dtype=np.uint32)
    nrm = asset.vertex_normals(pos, idx).astype(np.float64)
    lengths = np.linalg.norm(nrm, axis=1)
    assert lengths.min() > 32767 * 0.5, f"存在退化法线：{lengths}"
    assert not np.isnan(nrm).any()


def test_voxel_simplify_reduces_but_stays_in_bounds() -> None:
    """体素简化：面数降下来，包围盒不能被撑大（撑大说明顶点漂了）。"""

    rng = np.random.default_rng(7)
    pos = rng.random((400, 3)).astype(np.float32)
    idx = np.array([[i, i + 1, i + 2] for i in range(0, 400 - 2, 3)], dtype=np.uint32)

    s_pos, s_idx = asset.voxel_simplify(pos, idx, 0.2)
    assert s_pos.shape[0] < pos.shape[0]
    assert np.all(s_pos.min(axis=0) >= pos.min(axis=0) - 1e-6)
    assert np.all(s_pos.max(axis=0) <= pos.max(axis=0) + 1e-6)
    # 退化面（三点合并成两点）必须被丢掉，否则渲染时是黑斑
    assert np.all(s_idx[:, 0] != s_idx[:, 1])
    assert np.all(s_idx[:, 1] != s_idx[:, 2])


# --------------------------------------------------------------------------- 坐标


def test_barycentric_reconstructs_the_point() -> None:
    """``bary`` 重建必须等于原点。这是"换模型不漂"的全部技术前提。"""

    a = np.array([0.0, 0.0, 0.0])
    b = np.array([1.0, 0.0, 0.0])
    c = np.array([0.0, 1.0, 0.0])
    p = a * 0.2 + b * 0.3 + c * 0.5
    closest, bary = coords.closest_on_triangle(p, a, b, c)
    rebuilt = a * bary[0] + b * bary[1] + c * bary[2]
    assert np.allclose(closest, p)
    assert np.allclose(rebuilt, p)
    assert abs(bary.sum() - 1.0) < 1e-9


def test_point_outside_triangle_still_sums_to_one() -> None:
    """落在面外的点，最近点在边上，重心坐标仍归一（一项为 0，不做截断）。"""

    a = np.array([0.0, 0.0, 0.0])
    b = np.array([1.0, 0.0, 0.0])
    c = np.array([0.0, 1.0, 0.0])
    p = np.array([2.0, -1.0, 0.0])
    closest, bary = coords.closest_on_triangle(p, a, b, c)
    assert abs(bary.sum() - 1.0) < 1e-9
    assert np.allclose(closest, b)


@requires_assets
def test_skin_tri_bary_reconstruct_the_stored_xyz() -> None:
    """库里 361 个穴位的 (skin_tri, bary) 重建出来要等于 x3d/y3d/z3d。"""

    pos, idx = coords.skin_geometry()
    conn = store.connect(store.DB_PATH)
    try:
        rows = conn.execute(
            "SELECT id, x3d, y3d, z3d, skin_tri, skin_bary FROM meridian_acupoint "
            "WHERE skin_tri >= 0"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) >= 361

    worst = 0.0
    for _nid, x, y, z, tri, bary in rows:
        u, v, w = (float(t) for t in str(bary).split(","))
        verts = pos[idx[int(tri)]]
        rebuilt = verts[0] * u + verts[1] * v + verts[2] * w
        worst = max(worst, float(np.linalg.norm(rebuilt - np.array([x, y, z]))))
    # 中线穴的 x 被强制归零（否则左右镜像出两条线），重建会差 0.5 mm 以内；
    # 其余穴位存的就是投影点本身，误差只来自重心坐标保留 5 位小数。
    assert worst < 1e-3, f"最大重建误差 {worst*1000:.3f} mm"


@requires_assets
def test_no_acupoint_is_behind_the_body_axis() -> None:
    """只存一侧，且必须是 x>0 那一侧。混进 x<0 的点在图上就是一条经横穿身体。"""

    conn = store.connect(store.DB_PATH)
    try:
        bad = conn.execute(
            # 容差 0.1 mm：中线穴落在 x≈0，投影后的浮点噪声是 -1e-8 级别，
            # 用严格 ``< 0`` 会把「中线」判成「跑到对侧」。
            "SELECT id FROM meridian_acupoint WHERE skin_tri >= 0 AND x3d < -1e-4"
        ).fetchall()
        height = conn.execute("SELECT MAX(y3d) - MIN(y3d) FROM meridian_acupoint").fetchone()[0]
    finally:
        conn.close()
    assert bad == [], f"这些穴位跑到对侧了：{bad}"
    assert 1.6 < float(height) < 1.9, "身高不在成人范围，坐标系可能被换过"


# --------------------------------------------------------------------------- 资产门禁


@requires_assets
def test_first_screen_is_under_three_megabytes() -> None:
    """P0 定的硬门禁。835k 面全量加载在移动端是废的，所以只看首屏那一份。"""

    manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["first_screen_bytes"] <= 3_000_000
    skin = next(a for a in manifest["assets"] if a["name"] == "skin")
    assert skin["gzip_bytes"] <= 1_500_000, "皮肤单件超过 1.5 MB，说明没做压缩"
    packed = (OUT / "skin.bin.gz").read_bytes()
    assert len(packed) == skin["gzip_bytes"]
    assert len(gzip.decompress(packed)) == skin["bytes"]


@requires_assets
def test_licenses_separate_code_from_geometry() -> None:
    """MIT 与 CC BY 4.0 必须分开记：上游明确禁止把模型按 MIT 再许可。"""

    manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    by_asset = {x["asset"]: x for x in manifest["licenses"]}
    assert by_asset["coordinates"]["license"] == "MIT"
    assert by_asset["geometry"]["license"] == "CC BY 4.0"
    assert "BodyParts3D" in manifest["attribution"]
    assert "CC BY 4.0" in manifest["attribution"]


# --------------------------------------------------------------------------- 校验


@requires_assets
def test_symmetry_check_catches_the_flipped_ankle_point() -> None:
    """仆参（BL61）右点吸附到了脚底，左右差 11.8 cm。这条必须被报出来。"""

    from app.meridian.asset import load_surface

    rows, stats = crosscheck.symmetry_report(load_surface())
    ids = {r["id"] for r in rows}
    assert "BL61" in ids
    assert stats["paired"] >= 300
    assert stats["max_mm"] > 100


@requires_assets
def test_bone_cun_rules_land_in_a_plausible_range() -> None:
    """每寸 21 mm 左右是合理的（成人 1 寸约 2.5 cm，我们量的是两点直线，会偏短）。"""

    points = crosscheck._load_points(store.DB_PATH)
    _rows, stats = crosscheck.bone_cun_report(points)
    assert 15 <= stats["per_cun_mm"] <= 30
    assert stats["rules"] >= 8


# --------------------------------------------------------------------------- 接口


@pytest.fixture(scope="module")
def client():
    if not _has_atlas():
        pytest.skip("需要先构建资产与坐标")
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def test_atlas_returns_fourteen_channels_and_the_whole_catalogue(client) -> None:
    resp = client.get("/api/meridian/atlas")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["channels"]) == 14
    assert len(data["acupoints"]) == 362
    assert len(data["layers"]) == 6
    assert data["precision"]["verified"] == 0, "没人逐穴核过就必须是 0，不许批量置 1"
    placed = [a for a in data["acupoints"] if a["skin_tri"] >= 0]
    assert len(placed) == 361
    # 首屏 JSON 不该塞正文：105 KB 是这个量级的上限
    assert len(resp.content) < 300_000


def test_atlas_layer_carries_textbook_position(client) -> None:
    """地图要能自己答「这是第几章第几节」，不能回头去读后端的 syllabus 文件。"""

    data = client.get("/api/meridian/atlas?layer=herb").json()
    section = data["layer"]["sections"][0]
    for key in ("chapter_no", "book_chapter_no", "chapter_title", "part", "section_no", "virtual"):
        assert key in section
    assert section["points"], "每一节都要有知识点，否则点开是空的"


def test_point_returns_textbook_fields(client) -> None:
    data = client.get("/api/meridian/point/d01.s1.p1").json()
    assert data["name"] == "麻黄"
    assert "辛" in data["description"]
    assert set(data["content"]) >= {"药性", "功效", "应用"}
    assert data["content_source"]["page"] == 148


def test_soft_reference_point_says_so_instead_of_500(client) -> None:
    resp = client.get("/api/meridian/point/herb:不存在的药")
    assert resp.status_code == 404
    assert resp.json()["detail"]["display_name"] == "不存在的药"


def test_paths_are_fourteen_and_have_length(client) -> None:
    channels = client.get("/api/meridian/paths").json()["channels"]
    assert len(channels) == 14
    assert all(ch["length_m"] and ch["length_m"] > 0.1 for ch in channels)
    assert all(len(ch["points"]) > 1 for ch in channels)


def test_asset_whitelist_blocks_path_traversal(client) -> None:
    """``name`` 直接进文件名，必须白名单键控。"""

    assert client.get("/api/meridian/asset/../platform.db").status_code == 404
    assert client.get("/api/meridian/asset/manifest.json").status_code == 404
    ok = client.get("/api/meridian/asset/skin.bin.gz")
    assert ok.status_code == 200
    # `.gz` 走传输层压缩：Content-Encoding 告诉浏览器先解压，前端 `arrayBuffer()`
    # 直接拿到裸二进制。所以 content-type 是 octet-stream，不是 application/gzip
    # （后者表示"这个文件本身是一个 gzip 包"，浏览器会另存成 .gz 而不会解压）。
    assert ok.headers["content-type"] == "application/octet-stream"
    assert ok.headers["content-encoding"] == "gzip"
