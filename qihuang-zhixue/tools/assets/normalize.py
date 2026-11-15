#!/usr/bin/env python
"""归一化算子：让异源素材看起来像一套。

**多样性来自采集，统一性来自算子。** 四个配方 id 对应四类来源：

- ``plate-wash``：古本草图谱设色件（去底 → 降色 → 叠纸 → 羽化 → 裁剪 → WebP）
- ``plate-line``：纯线描件（跳过降色的中间调，只加深线条）
- ``photo-toned``：实拍件（去底减半，保留纹理，降色强度 50%）
- ``vintage-keep``：带透明通道的复古插画（裁内容外框 → 轻微叠纸 → 羽化 → 保留 **alpha 与原色** → WebP）

``vintage-keep`` 是唯一不做 duotone 的彩色配方。原因：Heritage 一类套组是
修复过的彩版画，降成墨赭两色会把整套的设色洗掉——那正是它们值钱的地方。
透明区必须留着：贴到纸面上时它应当透出纸色，而不是被填成一块白方。

依赖 Pillow（**仅开发期**）：

    pip install pillow
    python tools/assets/normalize.py 原图.png 输出.webp --recipe plate-wash

产品运行时不调用这个文件：运行时零网络，也不做图像处理。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

#: 主题纸色。与 ``frontend/src/config/pptThemes.ts`` 的 paper 主题一致。
PAPER = (250, 247, 240)
#: duotone 两端：墨与**赭石**。
#:
#: 赭石是偏褐的黄红（矿物颜料「赭石」的色），不是朱砂。用朱砂当亮端，
#: 降色后的药材图会整幅发粉——那是「印错了」，不是「做旧了」。
INK = (31, 27, 22)
OCHRE = (150, 113, 78)
#: 目标比例。与 ``DesignSlot.aspect`` 对应。
ASPECTS = {"portrait": 3 / 4, "square": 1.0, "landscape": 4 / 3, "wide": 16 / 9}
RECIPES = ("plate-wash", "plate-line", "photo-toned", "vintage-keep")


def normalize(
    source: Path,
    target: Path,
    *,
    recipe: str = "plate-wash",
    aspect: str = "portrait",
    long_edge: int = 1600,
    quality: int = 82,
) -> dict[str, int]:
    """跑一遍算子，返回尺寸与体积。参数全部显式，便于复现。"""

    try:
        from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat
    except ImportError:  # pragma: no cover - 只在缺依赖时命中
        raise SystemExit("需要 Pillow：pip install pillow（仅开发期依赖）") from None

    if recipe not in RECIPES:
        raise SystemExit(f"未知配方 {recipe}，可选：{'、'.join(RECIPES)}")

    opened = Image.open(source)

    # 透明件走独立分支：它的每一步都与「把原图压成 RGB 再降色」相反。
    if recipe == "vintage-keep":
        return _vintage_keep(
            opened, target, aspect=aspect, long_edge=long_edge, quality=quality,
            Image=Image, ImageChops=ImageChops, ImageFilter=ImageFilter,
        )

    image = opened.convert("RGB")

    # 1 去底：采样四角中位数，把扫描件的偏黄纸底拉到主题纸色。
    if recipe != "photo-toned":
        paper_rgb = _corner_median(image, Image, ImageStat)
        image = _shift_toward_paper(image, paper_rgb, Image)
    else:
        # 实拍件减半：全量会把手边的真实纹理一起洗掉，反而更像贴图。
        paper_rgb = _corner_median(image, Image, ImageStat)
        image = _shift_toward_paper(image, paper_rgb, Image, strength=0.5)

    # 2 降色：灰阶映射到 ink / ochre，保留层次；线描件不做，否则细线会糊。
    if recipe != "plate-line":
        amount = 0.5 if recipe == "photo-toned" else 1.0
        image = _duotone(image, Image, amount=amount)

    # 3 叠纸：与纸色正片叠底，融进底色。实拍件跳过，保住立体感。
    if recipe != "photo-toned":
        paper = Image.new("RGB", image.size, PAPER)
        image = Image.blend(image, ImageChops.multiply(image, paper), 0.12)

    # 4 羽化：轻微高斯，避免硬切贴上去像剪贴画。
    image = image.filter(ImageFilter.GaussianBlur(0.6))

    # 5 裁剪：居中裁到目标比例，留 6% 内边距——贴边的图在纸面上显得挤。
    image = _crop_to(image, ASPECTS.get(aspect, 3 / 4), Image, ImageOps)

    # 6 输出：WebP，长边受限。
    if max(image.size) > long_edge:
        ratio = long_edge / max(image.size)
        image = image.resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))), Image.LANCZOS)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, "WEBP", quality=quality, method=6)
    return {"w": image.width, "h": image.height, "bytes": target.stat().st_size}


def _vintage_keep(image, target, *, aspect, long_edge, quality, Image, ImageChops, ImageFilter):
    """保留 alpha 与原色的复古插画。与主流程相反：不去底、不降色。

    三步，理由都来自实测：

    1. **裁到内容外框**。这类套组四周留白极多（实测 42%–92% 的像素是透明），
       不裁就会在版面上缩成中间一小条。
    2. **轻微叠纸 5%**。只压实体区的 RGB，alpha 一位都不动——alpha 一旦被
       混合，边缘会浮出一圈灰边，贴到纸面上就是毛边。
    3. **按比例补透明边**，而不是裁剪。宁可留白，也不要切掉花枝与器耳。
    """

    has_alpha = image.mode in ("RGBA", "LA") or "transparency" in image.info
    image = image.convert("RGBA") if has_alpha else image.convert("RGB").convert("RGBA")

    bbox = image.split()[-1].getbbox()
    if bbox:
        image = image.crop(bbox)

    rgb = image.convert("RGB")
    paper = Image.new("RGB", rgb.size, PAPER)
    rgb = Image.blend(rgb, ImageChops.multiply(rgb, paper), 0.05)
    image = Image.merge("RGBA", (*rgb.split(), image.split()[-1]))

    image = _fit_to(image, ASPECTS.get(aspect, 3 / 4), Image)

    if max(image.size) > long_edge:
        ratio = long_edge / max(image.size)
        image = image.resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))), Image.LANCZOS)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, "WEBP", quality=quality, method=6)
    return {"w": image.width, "h": image.height, "bytes": target.stat().st_size}


def _fit_to(image, target_ratio: float, Image):
    """按比例居中补透明边。比裁剪更安全：宁可留白，不要切掉主体。"""

    width, height = image.size
    if height == 0:
        return image
    ratio = width / height
    if abs(ratio - target_ratio) < 0.01:
        return image
    if ratio > target_ratio:
        new_height = int(width / target_ratio)
        canvas = Image.new("RGBA", (width, new_height), (0, 0, 0, 0))
        canvas.paste(image, (0, (new_height - height) // 2))
    else:
        new_width = int(height * target_ratio)
        canvas = Image.new("RGBA", (new_width, height), (0, 0, 0, 0))
        canvas.paste(image, ((new_width - width) // 2, 0))
    return canvas


def _corner_median(image, Image, ImageStat) -> tuple[int, int, int]:
    """四角各取一小块，逐通道取中位数。均值会被角落的污渍带偏。"""

    width, height = image.size
    patch = max(8, min(width, height) // 20)
    boxes = [
        (0, 0, patch, patch),
        (width - patch, 0, width, patch),
        (0, height - patch, patch, height),
        (width - patch, height - patch, width, height),
    ]
    channels: list[list[float]] = [[], [], []]
    for box in boxes:
        median = ImageStat.Stat(image.crop(box)).median
        for index in range(3):
            channels[index].append(median[index])
    return tuple(int(sorted(values)[len(values) // 2]) for values in channels)  # type: ignore[return-value]


def _shift_toward_paper(image, sampled: tuple[int, int, int], Image, *, strength: float = 1.0):
    """逐通道线性映射到纸色。整幅图统一缩放，不做局部修正——局部修正会留疤。"""

    shifted = []
    for index, channel in enumerate(image.split()):
        target = PAPER[index]
        source = sampled[index] or 1
        scale = target / source
        lut = [min(255, max(0, int((value * scale - target) * strength + target))) for value in range(256)]
        shifted.append(channel.point(lut))
    return Image.merge("RGB", shifted)


def _duotone(image, Image, *, amount: float):
    gray = image.convert("L")
    tinted = Image.merge(
        "RGB",
        (
            gray.point(lambda value: int(INK[0] + (OCHRE[0] - INK[0]) * value / 255)),
            gray.point(lambda value: int(INK[1] + (OCHRE[1] - INK[1]) * value / 255)),
            gray.point(lambda value: int(INK[2] + (OCHRE[2] - INK[2]) * value / 255)),
        ),
    )
    return Image.blend(image, tinted, amount)


def _crop_to(image, target_ratio: float, Image, ImageOps):
    width, height = image.size
    ratio = width / height
    if ratio > target_ratio:
        new_width = int(height * target_ratio)
        box = ((width - new_width) // 2, 0, (width + new_width) // 2, height)
    else:
        new_height = int(width / target_ratio)
        box = (0, (height - new_height) // 2, width, (height + new_height) // 2)
    cropped = image.crop(box)
    pad = int(min(cropped.size) * 0.06)
    return ImageOps.expand(cropped, border=pad, fill=PAPER)


def main() -> int:
    parser = argparse.ArgumentParser(description="素材归一化")
    parser.add_argument("source")
    parser.add_argument("target")
    parser.add_argument("--recipe", default="plate-wash", choices=RECIPES)
    parser.add_argument("--aspect", default="portrait", choices=sorted(ASPECTS))
    parser.add_argument("--long-edge", type=int, default=1600)
    args = parser.parse_args()
    info = normalize(Path(args.source), Path(args.target), recipe=args.recipe, aspect=args.aspect, long_edge=args.long_edge)
    print(" ".join(f"{key}={value}" for key, value in info.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
