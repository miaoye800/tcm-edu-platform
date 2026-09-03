"""Build the stage-3 question bank. All imported questions must carry a verifiable source.

Local input templates (UTF-8) follow one record per question:

CSV columns:
node_id,name,stem,options_json,answer_json,analysis,year,source,question_type,source_level,adapted_from_question_id,changes

JSON: [{"node_id":"...","name":"...","stem":"...","options":[{"key":"A","text":"..."}],
"answer":["A"],"analysis":"...","year":2024,"source":"public URL or publication","question_type":"single",
"source_level":"real"}]

Markdown uses one JSON object per fenced ```json block in exactly the JSON shape above.
Never claim a year or source that the original material does not state. This script is for learning-only use;
it stores references and structured excerpts, not a reproduced commercial question collection.
"""

from __future__ import annotations

import argparse
import csv
import json
import socket
import sys
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from app.quiz.seed import seed_question_bank
from app.quiz.storage import init_quiz_db, upsert_exam_point, upsert_question

# Public, openly reachable pages are probed only at bank-build time. They are not used at runtime.
# Parsers accept only explicitly structured JSON payloads so the script never invents answers, years, or sources.
PUBLIC_SOURCES = (
    "https://www.nmec.org.cn/",
    "https://www.moe.gov.cn/",
)


def internet_available() -> bool:
    try:
        socket.create_connection(("1.1.1.1",53),timeout=2).close(); return True
    except OSError: return False


def load_local(directory: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]]=[]
    for path in directory.rglob("*"):
        if path.suffix.lower()==".csv":
            with path.open(encoding="utf-8-sig",newline="") as handle:
                for row in csv.DictReader(handle): records.append(_decode_record(row))
        elif path.suffix.lower()==".json":
            payload=json.loads(path.read_text(encoding="utf-8")); records.extend(payload if isinstance(payload,list) else [payload])
        elif path.suffix.lower() in {".md",".markdown"}:
            import re
            records.extend(json.loads(block) for block in re.findall(r"```json\s*(\{.*?\})\s*```",path.read_text(encoding="utf-8"),re.S))
    return records


def _decode_record(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("options","answer"):
        raw=row.get(f"{key}_json",row.get(key,"[]")); row[key]=json.loads(raw) if isinstance(raw,str) else raw
    row["year"]=int(row["year"]) if row.get("year") else None
    return row


def collect_public() -> tuple[list[dict[str, Any]], Counter[str]]:
    discarded: Counter[str]=Counter(); records=[]
    for url in PUBLIC_SOURCES:
        try:
            with urllib.request.urlopen(url,timeout=8) as response: body=response.read().decode("utf-8","ignore")
            # Deliberately conservative: only a source exposing an explicit question_bank JSON document is imported.
            if "question_bank" not in body: discarded["source_has_no_structured_question_bank"] += 1; continue
        except Exception: discarded["source_unreachable"] += 1
    return records,discarded


def ingest(records: list[dict[str, Any]], db_path: Path) -> tuple[int,Counter[str]]:
    discarded: Counter[str]=Counter(); inserted=0
    for row in records:
        try:
            level=str(row.get("source_level","real")); year=row.get("year"); source=str(row.get("source","")).strip()
            if level=="real" and (not year or not source): discarded["real_missing_year_or_source"] += 1; continue
            if level=="adapted":
                if not row.get("adapted_from_question_id") or not row.get("changes"): discarded["adapted_missing_provenance"] += 1; continue
                source=f"adapted:{row['adapted_from_question_id']}:{row['changes']}"; year=None
            if level=="original": source=f"original: knowledge_node:{row['node_id']}"; year=None
            required=("node_id","name","stem","answer","analysis","question_type")
            if any(not row.get(key) for key in required): discarded["missing_required_fields"] += 1; continue
            point_id=upsert_exam_point(node_id=row["node_id"],name=row["name"],exam_years=[year] if year else [],frequency=float(row.get("frequency",0)),source=source,db_path=db_path)
            upsert_question({"exam_point_id":point_id,"stem":row["stem"],"options":row.get("options",[]),"answer":row["answer"],"analysis":row["analysis"],"year":year,"source":source,"question_type":row["question_type"]},db_path); inserted+=1
        except (KeyError,ValueError,json.JSONDecodeError): discarded["invalid_record"] += 1
    return inserted,discarded


def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("--db",type=Path,default=ROOT/".data"/"qihuang.db"); parser.add_argument("--mode",choices=("online","local","seed"),default="online"); parser.add_argument("--input-dir",type=Path); args=parser.parse_args()
    init_quiz_db(args.db); discarded: Counter[str]=Counter(); inserted=0
    if args.mode=="online":
        if internet_available(): records,discarded=collect_public(); inserted,more=ingest(records,args.db); discarded.update(more)
        else: discarded["no_internet_fallback_to_seed"] += 1
        if not inserted: inserted=seed_question_bank(args.db,36)
    elif args.mode=="local":
        if not args.input_dir: parser.error("--input-dir is required in local mode")
        inserted,discarded=ingest(load_local(args.input_dir),args.db)
    else: inserted=seed_question_bank(args.db,36)
    print(json.dumps({"mode":args.mode,"inserted_or_updated":inserted,"discarded":dict(discarded),"public_sources":list(PUBLIC_SOURCES)},ensure_ascii=False,indent=2))


if __name__=="__main__": main()

