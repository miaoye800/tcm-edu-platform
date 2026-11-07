"""把教材 PDF 的文本层抽出来并按页缓存，供后续扫描/抽取复用。

为什么单独做这一步：841 页的 PDF 每解析一次要几十秒，后面的章节定位、
功效词统计、条目抽取都要反复读同一份文本。抽一次存成 JSON，之后全部走缓存。

**这一步只读教材、只写缓存，不改任何业务数据。**
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

logging.disable(logging.CRITICAL)

DEFAULT_PDF = (
    Path(__file__).resolve().parents[4]
    / "中药学 (新世纪第四版；全国中医药行业高等教育十三五规划教材；"
    "全国高等中医药院校规划教材第十版) (钟赣生) "
    "(z-library.sk, 1lib.sk, z-lib.sk).pdf"
)

CACHE = Path(__file__).resolve().parent / "_cache" / "pages.json"


def extract(pdf: Path, cache: Path = CACHE) -> list[str]:
    """返回按 PDF 页码（0-based）索引的纯文本列表。"""

    if cache.exists():
        payload = json.loads(cache.read_text(encoding="utf-8"))
        if payload.get("source") == str(pdf):
            return payload["pages"]

    from pypdf import PdfReader

    reader = PdfReader(str(pdf))
    pages = [(page.extract_text() or "") for page in reader.pages]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps({"source": str(pdf), "pages": pages}, ensure_ascii=False),
        encoding="utf-8",
    )
    return pages


def main() -> None:
    parser = argparse.ArgumentParser(description="抽取教材 PDF 文本并缓存")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--force", action="store_true", help="忽略缓存重新抽取")
    args = parser.parse_args()

    if args.force and CACHE.exists():
        CACHE.unlink()

    pages = extract(args.pdf)
    total = sum(len(text) for text in pages)
    print(f"pages={len(pages)} chars={total} cache={CACHE}")


if __name__ == "__main__":
    main()
