"""通义千问生图（DashScope 多模态生成端点）。

用途只有一个：**装饰位在没有对应素材时补一张**。三条边界写在代码里而不是提示词里：

1. 每份稿最多 3 张（预算在 `agent_loop.py` 里扣）；
2. **禁止进证据位**——生成图不能用来证明「这就是黄芪」；
3. 必须记 ``prompt / model``，并**落盘**：接口返回的是 OSS 临时地址，
   过期后图就没了，不落盘的生成图等于一张会消失的图。

落盘位置与本地素材库同一套：``frontend/public/assets/generated/{id}.webp``。
它不是「远程素材」，是我们自己的产物，本地保存才可复现。
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from typing import Any

import httpx

from ...config import get_settings

logger = logging.getLogger(__name__)

#: 实测可用的模型名。``qwen-image`` / ``qwen-image-plus`` 都返回 Model not exist。
DEFAULT_MODEL = "qwen-image-2.1-pro"
ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
TIMEOUT = 45.0

#: 允许的尺寸。生图接口只认这几个「档」，不是任意宽高。
SIZES = {
    "square": "1024*1024",
    "portrait": "768*1152",
    "landscape": "1280*720",
}

#: 输出目录。与 `select.registry_path()` 同一套推导。
_ASSET_DIR = Path(__file__).resolve().parents[4] / "frontend" / "public" / "assets" / "generated"


def available() -> bool:
    return bool(get_settings().qwen_api_key)


def output_dir() -> Path:
    _ASSET_DIR.mkdir(parents=True, exist_ok=True)
    return _ASSET_DIR


async def generate(
    prompt: str,
    *,
    size: str = "square",
    model: str = DEFAULT_MODEL,
    asset_id: str = "",
) -> dict[str, Any]:
    """生成一张图并落盘。

    返回 ``{"ok": bool, "asset_id": str, "local": str, "remote": str, "prompt": str, "model": str}``。
    **失败也返回 dict，不抛异常**：生图是最外层兜底，它挂了只是少一张图。
    """

    settings = get_settings()
    if not settings.qwen_api_key:
        return {"ok": False, "reason": "no_key", "prompt": prompt}

    payload = {
        "model": model,
        "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
        "parameters": {"size": SIZES.get(size, SIZES["square"]), "n": 1},
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                ENDPOINT,
                headers={
                    "Authorization": f"Bearer {settings.qwen_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if response.status_code >= 400:
                logger.info("生图失败 %s：%s", response.status_code, response.text[:200])
                return {"ok": False, "reason": f"HTTP{response.status_code}", "prompt": prompt}
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("生图不可达：%s", exc.__class__.__name__)
        return {"ok": False, "reason": exc.__class__.__name__, "prompt": prompt}

    remote = _first_image(data)
    if not remote:
        return {"ok": False, "reason": "no_image", "prompt": prompt}

    local = await _persist(remote, asset_id)
    return {
        "ok": bool(local),
        "asset_id": asset_id,
        "local": local,
        "remote": remote,
        "prompt": prompt,
        "model": model,
        "reason": "" if local else "save_failed",
    }


def _first_image(data: dict[str, Any]) -> str:
    try:
        content = data["output"]["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    for item in content or []:
        if isinstance(item, dict) and item.get("image"):
            return str(item["image"])
    return ""


async def _persist(url: str, asset_id: str) -> str:
    """把生成结果落到本地，返回前端可用的相对路径。

    OSS 地址是临时的（过期即 404），不落盘就等于「生成过一次，第二天全裂」。
    转成 webp 顺带压体积——生成图常有几百 KB，版面上只需要 1600px 宽。
    """

    if not asset_id:
        return ""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url)
            if response.status_code >= 400:
                return ""
            raw = response.content
    except httpx.HTTPError:
        return ""

    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        target = output_dir() / f"{asset_id}.png"
        target.write_bytes(raw)
        return f"/assets/generated/{target.name}"

    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.width > 1600:
                ratio = 1600 / image.width
                image = image.resize((1600, max(1, int(image.height * ratio))))
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="WEBP", quality=88)
    except (OSError, ValueError):
        return ""

    target = output_dir() / f"{asset_id}.webp"
    target.write_bytes(buffer.getvalue())
    return f"/assets/generated/{target.name}"


def b64_of(path: Path) -> str:  # pragma: no cover - 调试用
    return base64.b64encode(path.read_bytes()).decode("ascii")
