"""生成人工复核单页（自包含 HTML，双击就能开）。

## 复核不是「一条条看」

442 味药，非中医专业的人看不动，也不该看——他判断不了「大补元气」和
「补脾益肺」哪个更准。所以复核页只问一个问题：

    **右边这些字，能不能在左边原文里找到？切开的位置对不对？**

这是「找不同」，不需要中医知识。而这个问题里 99% 的部分机器已经答过了
（G008 原文子串 + selfcheck 的结构自检），人只需要看机器判不了的那几条。

## 页面怎么用

1. 默认停在「待复核」——只有 selfcheck 标了疑点的条目，通常个位数
2. 每条左边是教材原文，右边是抽出来的字段，每个字段旁自动标「原文命中」
3. 三个按钮：通过 / 有问题（写一句）/ 看不懂
4. 随时点「导出结论」，得到一份 JSON，回填给脚本就能把结论落库

用法::

    python -m scripts.textbook.review_sheet
    # 产出 deliverables/review_sheet.html
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from app.kg import draft

BACKEND = Path(__file__).resolve().parents[2]
ENTRIES = BACKEND / "data" / "curated" / "textbook_entries.json"
NORMALIZED = BACKEND / "data" / "curated" / "textbook_normalized.json"
QUEUE = BACKEND / "data" / "curated" / "review_queue.json"
DECISIONS = BACKEND / "data" / "curated" / "review_decisions.json"
OUT_HTML = BACKEND.parent / "deliverables" / "review_sheet.html"

#: 反向核对用的原文栏。**故意不含【功效】**——反向核对的意义就是让人
#: 先自己从【应用】推一遍，看见了抽取值再写就等于抄。
REVERSE_SOURCE = ("药性", "应用", "用法用量")

#: 原文里给用户看的字段（顺序即阅读顺序）。
SOURCE_FIELDS = ("药性", "性味", "功效", "应用", "用法用量", "使用注意", "鉴别用药")


def _squash(text: str) -> str:
    return "".join((text or "").split())


def _loose(text: str) -> str:
    """去掉顿号/逗号/分号再比——教材自己会把「归心，胃经」写成逗号。

    绿豆和冰片的【药性】原文就是逗号：「甘，寒。归心，胃经。」
    我们统一成顿号是对的，但严格子串比对会报「原文没找到」，
    等于让人去核对一个不是问题的问题。
    """

    return re.sub(r"[、，,；;]", "", _squash(text))


def _prep_note(prep: dict[str, str]) -> str:
    """把「生用→清热泻火 / 煅用→收湿」拼成一行给人看的分组说明。"""

    groups: dict[str, list[str]] = {}
    for term, label in prep.items():
        groups.setdefault(label, []).append(term)
    return " / ".join(f"{label}→{'、'.join(terms)}" for label, terms in groups.items())


def build_payload() -> dict:
    entries = {item["name"]: item for item in json.loads(ENTRIES.read_text(encoding="utf-8"))["entries"]}
    normalized = {item["name"]: item for item in json.loads(NORMALIZED.read_text(encoding="utf-8"))["herbs"]}
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    flagged = {item["name"]: item["issues"] for item in queue["queue"]}

    # 已裁定的疑点。页面上先铺好，人不用重复判一遍。
    decisions: dict[str, dict] = {}
    if DECISIONS.exists():
        for item in json.loads(DECISIONS.read_text(encoding="utf-8")).get("decisions") or []:
            if item.get("verdict") == "ok":
                decisions[str(item.get("herb", ""))] = {
                    "state": "ok",
                    "note": f"[{item.get('code')}] {item.get('reason', '')}",
                }

    rows = []
    for name, entry in entries.items():
        norm = normalized.get(name)
        if not norm:
            continue
        source = {field: entry["fields"].get(field, "") for field in SOURCE_FIELDS if entry["fields"].get(field)}
        whole = _squash("".join(source.values()))
        whole_loose = _loose("".join(source.values()))

        # 每个抽取字段自带「原文命中」标记——这一栏是**机器判的**，人不用管。
        # 毒性是例外：教材的编排惯例是「有毒的才标」，所以「无毒」本来就不在
        # 原文里。标成「原文没找到」会让人以为抽错了，其实是推断值。
        inferred_toxicity = norm.get("toxicity_source") == "inferred"
        checks = []
        # 原文按炮制品分了组的（石膏生用/煅用、何首乌制/生），把归属摆出来：
        # 功效词已经剥成干净的了，不说一句人就看不出「收湿」是煅用的。
        prep = norm.get("effect_prep") or {}
        if prep:
            checks.append(
                {
                    "label": "功效分组",
                    "value": _prep_note(prep),
                    "hit": True,
                    "note": "原文分组标记，已按组归属",
                }
            )
        for label, value, note in (
            ("四气", norm.get("nature"), ""),
            ("五味", "、".join(norm.get("flavors") or []), ""),
            ("归经", "、".join(norm.get("meridians") or []), ""),
            ("毒性", norm.get("toxicity"), "教材未标，按无毒处理" if inferred_toxicity else ""),
            ("剂量", norm.get("dosage_text", "")[:40], ""),
        ):
            if not value:
                continue
            hit = None if note else _squash(value)[:6] in whole
            if hit is False:
                loose = _loose(value)
                if loose and loose in whole_loose:
                    hit = True
                    note = "原文标点不同，已规范"
            checks.append({"label": label, "value": value, "hit": hit, "note": note})

        rows.append(
            {
                "name": name,
                "page": (entry.get("page") or 0) + 1,
                "chapter": norm.get("category", ""),
                "section": norm.get("section", ""),
                "source": source,
                "reverse_source": {
                    field: entry["fields"][field]
                    for field in REVERSE_SOURCE
                    if entry["fields"].get(field)
                },
                "fields": {
                    "四气": norm.get("nature", ""),
                    "五味": norm.get("flavors") or [],
                    "归经": norm.get("meridians") or [],
                    "毒性": norm.get("toxicity", ""),
                    "功效": norm.get("effects") or [],
                    "主治": [item.get("syndrome") or "（未切出证名）" for item in (norm.get("indications") or [])],
                    "剂量": norm.get("dosage_text", ""),
                    "使用注意": norm.get("cautions") or [],
                },
                "checks": checks,
                "issues": flagged.get(name, []),
            }
        )

    # 草稿层的签字状态与抽样台账。复核页要能回答「这条谁签的、抽样看过没有」，
    # 否则页面和库里的真相是两套。
    statuses: dict[str, dict] = {}
    samples: list[dict] = []
    try:
        for record in draft.list_drafts():
            statuses[record.herb_name] = {
                "status": record.status,
                "reviewer": record.reviewed_by or "",
                "note": record.review_note or "",
            }
        samples = draft.samples()
    except Exception:  # noqa: BLE001 - 库还没建时页面照样要能开
        pass

    for row in rows:
        row["draft"] = statuses.get(row["name"], {})

    sample_rows = [
        {
            "name": item["herb_name"],
            "reason": item["reason"],
            "verdict": item["verdict"],
            "note": item["note"],
            "reviewer": item["reviewed_by"] or "",
        }
        for item in samples
    ]

    return {
        "stats": queue["stats"],
        "decisions": decisions,
        "drafts": {
            "counts": _draft_counts(),
            "unseen": len([item for item in samples if not item["verdict"]]),
            "samples": len(samples),
        },
        "sample_rows": sample_rows,
        "rows": sorted(rows, key=lambda item: (-len(item["issues"]), item["page"])),
    }


def _draft_counts() -> dict[str, int]:
    try:
        return draft.counts()
    except Exception:  # noqa: BLE001
        return {}


TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>P4 教材抽取 · 人工复核</title>
<style>
  :root {
    --bg: #f6f5f2; --card: #fff; --line: #e4e1d9; --fg: #23211d; --soft: #6b665c;
    --ok: #2f7a4f; --warn: #b06a1f; --bad: #a63a2c; --accent: #7a5c3e;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--fg);
         font: 14px/1.65 -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; }
  header { position: sticky; top: 0; z-index: 9; background: rgba(246,245,242,.94);
           backdrop-filter: blur(8px); border-bottom: 1px solid var(--line); padding: 14px 24px; }
  h1 { margin: 0 0 4px; font-size: 17px; letter-spacing: .02em; }
  .sub { color: var(--soft); font-size: 12.5px; }
  .bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 12px; }
  .stat { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
          padding: 6px 12px; font-size: 12.5px; color: var(--soft); }
  .stat b { color: var(--fg); font-size: 15px; margin-right: 4px; }
  button { font: inherit; cursor: pointer; border-radius: 7px; border: 1px solid var(--line);
           background: var(--card); padding: 6px 12px; color: var(--fg); }
  button:hover { border-color: #c9c3b6; }
  button.on { background: var(--fg); color: #fff; border-color: var(--fg); }
  button.ok { border-color: #bcd8c6; color: var(--ok); }
  button.ok.on { background: var(--ok); color: #fff; border-color: var(--ok); }
  button.bad { border-color: #e2c3bd; color: var(--bad); }
  button.bad.on { background: var(--bad); color: #fff; border-color: var(--bad); }
  main { padding: 20px 24px 80px; max-width: 1180px; margin: 0 auto; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
          margin-bottom: 16px; overflow: hidden; }
  .card-head { display: flex; justify-content: space-between; gap: 12px; align-items: baseline;
               padding: 12px 16px; border-bottom: 1px solid var(--line); background: #fbfaf7; }
  .name { font-size: 15px; font-weight: 600; }
  .meta { color: var(--soft); font-size: 12px; }
  .issues { margin: 10px 16px 0; padding: 8px 12px; background: #fdf6ea; border: 1px solid #efe0c6;
            border-radius: 8px; color: var(--warn); font-size: 12.5px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0; }
  @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
  .pane { padding: 14px 16px; }
  .pane + .pane { border-left: 1px solid var(--line); }
  @media (max-width: 900px) { .pane + .pane { border-left: 0; border-top: 1px solid var(--line); } }
  .pane h4 { margin: 0 0 8px; font-size: 12px; letter-spacing: .08em; color: var(--soft);
             text-transform: uppercase; font-weight: 600; }
  .src { margin-bottom: 10px; }
  .src b { color: var(--accent); font-weight: 600; font-size: 12px; }
  .src p { margin: 2px 0 0; font-size: 13px; color: #3a3730; }
  .fld { display: flex; gap: 8px; align-items: flex-start; padding: 5px 0; border-bottom: 1px dashed #efedE6; }
  .fld:last-child { border-bottom: 0; }
  .fld .k { width: 62px; flex: none; color: var(--soft); font-size: 12.5px; }
  .fld .v { flex: 1; font-size: 13px; }
  .tag { flex: none; font-size: 11px; padding: 1px 6px; border-radius: 999px; border: 1px solid; }
  .tag.hit { color: var(--ok); border-color: #bcd8c6; background: #f2f8f4; }
  .tag.miss { color: var(--bad); border-color: #e2c3bd; background: #fbf3f2; }
  .tag.infer { color: var(--soft); border-color: var(--line); background: #f7f6f2; }
  .decide { display: flex; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--line);
            background: #fbfaf7; align-items: center; flex-wrap: wrap; }
  .decide .note { flex: 1; min-width: 180px; }
  .decide input { width: 100%; font: inherit; padding: 6px 10px; border-radius: 7px;
                  border: 1px solid var(--line); background: #fff; }
  .done { color: var(--ok); font-size: 12.5px; }
  .empty { text-align: center; color: var(--soft); padding: 60px 0; }
  .boot { max-width: 640px; margin: 40px auto; padding: 28px 32px; background: var(--card);
          border: 1px solid var(--line); border-radius: 12px; text-align: center; color: var(--soft); }
  .boot h2 { margin: 0 0 10px; font-size: 16px; color: var(--fg); }
  .boot p { margin: 6px 0; font-size: 13px; }
  .boot code { background: #f2f0ea; padding: 2px 6px; border-radius: 5px; color: var(--accent); }
  footer { position: fixed; bottom: 0; left: 0; right: 0; background: rgba(246,245,242,.96);
           border-top: 1px solid var(--line); padding: 10px 24px; display: flex;
           justify-content: space-between; align-items: center; font-size: 12.5px; color: var(--soft); }
</style>
</head>
<body>
<header>
  <h1>P4 教材抽取 · 人工复核</h1>
  <div class="sub">
    左：教材原文　右：抽出来的字段　每个字段后面那个小标签是<b>机器判的</b>——它检查这几个字能不能在左边原文里找到。<br>
    你只需要判断一件事：<b>切开的位置对不对</b>。不需要懂中医。
  </div>
  <div class="bar">
    <button id="m-flag" class="on">待复核 (<span id="n-flag"></span>)</button>
    <button id="m-ledger">抽样台账 (<span id="n-ledger"></span>)</button>
    <button id="m-reverse">反向核对</button>
    <button id="m-all">全部</button>
    <span class="stat"><b id="s-total"></b>条总数</span>
    <span class="stat"><b id="s-signed"></b>条已签字</span>
    <span class="stat"><b id="s-unseen"></b>条抽样未看</span>
    <span class="stat"><b id="s-flag"></b>条需人看</span>
  </div>
</header>

<main id="list"></main>

<!-- 脚本没跑起来时这条会留在页面上。白屏最容易被误当成「没有条目」，
     所以宁可显式说一句「脚本被禁了、换个方式打开」。 -->
<div id="boot" class="boot">
  <h2>页面没能显示内容</h2>
  <p>多半是这个环境禁用了 JavaScript（内嵌预览面板常见），也可能是数据没加载完。</p>
  <p>换这两种方式之一打开：</p>
  <p><code>http://127.0.0.1:8000/review</code></p>
  <p>或直接双击 <code>qihuang-zhixue/deliverables/review_sheet.html</code></p>
</div>

<footer>
  <span id="progress">尚未开始</span>
  <span>
    <button id="export">导出结论 JSON</button>
  </span>
</footer>

<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
const DATA = JSON.parse(document.getElementById('payload').textContent);
const boot = document.getElementById('boot');
if (boot) boot.remove();
const DECISIONS = JSON.parse(localStorage.getItem('p4-review') || '{}');
// 已经复核过的结论先铺上（review_decisions.json）。浏览器里没存过就以档案为准，
// 存过就以浏览器为准——人现场改的判断比旧档案新。
for (const [name, d] of Object.entries(DATA.decisions || {})) {
  if (!DECISIONS[name] || !DECISIONS[name].state) DECISIONS[name] = d;
}
// 队列空了就别停在「待复核」这一屏——那会是一片空白，看着像页面坏了。
// 有疑点先看疑点，没有就落到抽样台账：那才是这批数据现在该被看的东西。
let mode = DATA.stats.needs_review ? 'flag' : 'ledger';

document.getElementById('s-total').textContent = DATA.stats.total;
document.getElementById('s-signed').textContent = (DATA.drafts.counts || {}).approved || 0;
document.getElementById('s-unseen').textContent = DATA.drafts.unseen;
document.getElementById('s-flag').textContent = DATA.stats.needs_review;
document.getElementById('n-flag').textContent = DATA.stats.needs_review;
document.getElementById('n-ledger').textContent = DATA.drafts.samples;

// 抽样台账：每条样本一行，说明它为什么被抽中、结论是谁签的。
const REASON_LABEL = {
  first50: '首批全检', toxic: '毒性药全检', flag: '疑点全检',
  random20: '随机 20%', reverse: '反向核对',
};
const STATUS_LABEL = {
  pending: '等人看', auto_passed: '机器全过', conflict: '待裁定',
  approved: '已签字', rejected: '不入库',
};
// 反向核对：只有这 3 味，且只给原文（不含【功效】）。
const REVERSE_NAMES = DATA.sample_rows.filter(s => s.reason === 'reverse').map(s => s.name);
const LEDGER = new Set(DATA.sample_rows.map(s => s.name));

function esc(s) {
  return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

function pickRows() {
  if (mode === 'flag') return DATA.rows.filter(r => r.issues.length);
  if (mode === 'all') return DATA.rows;
  if (mode === 'reverse') return DATA.rows.filter(r => REVERSE_NAMES.includes(r.name));
  if (mode === 'ledger') return DATA.rows.filter(r => LEDGER.has(r.name));
  return DATA.rows;
}

// 抽样台账用表格，不用卡片：它的用途是「一眼看出抽满没有」，
// 卡片一屏放不下 170 条，反而看不清。
function renderLedger() {
  const list = document.getElementById('list');
  const order = Object.keys(REASON_LABEL);
  const rows = DATA.sample_rows.slice().sort(
    (a, b) => order.indexOf(a.reason) - order.indexOf(b.reason) || a.name.localeCompare(b.name, 'zh')
  );
  if (!rows.length) { list.innerHTML = '<div class="empty">还没有抽样记录。先跑 scripts.textbook.sample_plan。</div>'; return; }
  const byReason = {};
  rows.forEach(r => { byReason[r.reason] = (byReason[r.reason] || 0) + 1; });
  const head = Object.entries(byReason)
    .map(([k, v]) => `<span class="stat">${esc(REASON_LABEL[k] || k)} <b>${v}</b></span>`).join(' ');
  list.innerHTML = `<div class="card"><div class="card-head"><div>
      <span class="name">抽样台账</span>
      <span class="meta">　共 ${rows.length} 条，未看 ${DATA.drafts.unseen} 条</span></div></div>
    <div style="padding:12px 16px">${head}</div>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead><tr style="text-align:left;color:var(--soft);font-size:12px">
        <th style="padding:6px 16px">药名</th><th>抽样理由</th><th>结论</th><th>谁签的</th><th>说明</th>
      </tr></thead><tbody>
      ${rows.map(r => `<tr style="border-top:1px solid var(--line)">
        <td style="padding:6px 16px">${esc(r.name)}</td>
        <td>${esc(REASON_LABEL[r.reason] || r.reason)}</td>
        <td>${r.verdict ? esc(r.verdict) : '<span style="color:var(--warn)">未看</span>'}</td>
        <td style="color:var(--soft)">${esc(r.reviewer || '—')}</td>
        <td style="color:var(--soft)">${esc((r.note || '').slice(0, 60))}</td>
      </tr>`).join('')}
      </tbody></table></div>`;
}

// 反向核对：**只给原文，不给【功效】**。人先自己写一遍，点「比对」才展开。
// 这是唯一能抓出「整批都系统性偏了一点」的方法——先看答案再写，写出来的
// 一定是答案。
function renderReverse() {
  const list = document.getElementById('list');
  const rows = pickRows();
  if (!rows.length) { list.innerHTML = '<div class="empty">本批没有反向核对样本。</div>'; return; }
  list.innerHTML = rows.map(r => {
    const src = Object.entries(r.reverse_source)
      .map(([k, v]) => `<div class="src"><b>【${esc(k)}】</b><p>${esc(v)}</p></div>`).join('');
    const d = DECISIONS[r.name] || {};
    return `
    <section class="card" data-name="${esc(r.name)}">
      <div class="card-head">
        <div><span class="name">${esc(r.name)}</span>
        <span class="meta">　p${r.page}　${esc(r.chapter)}</span></div>
        <span class="meta">反向核对</span>
      </div>
      <div class="grid">
        <div class="pane"><h4>教材原文（不含【功效】）</h4>${src}</div>
        <div class="pane">
          <h4>先自己写一遍</h4>
          <p class="meta" style="margin:0 0 8px">只看左边，你认为这味药的功效是什么？写下来再比对。</p>
          <textarea class="mine" rows="4" style="width:100%;font:inherit;padding:8px;border:1px solid var(--line);border-radius:7px"
            placeholder="例如：发汗解表，宣肺平喘">${esc(d.mine || '')}</textarea>
          <div class="reveal" style="display:none;margin-top:12px;padding-top:10px;border-top:1px solid var(--line)">
            <h4 style="margin-bottom:6px">教材【功效】与抽取值</h4>
            ${fieldRow('功效', r.fields['功效'], null)}
          </div>
        </div>
      </div>
      <div class="decide">
        <button data-act="reveal">比对</button>
        <button class="ok ${d.state === 'ok' ? 'on' : ''}" data-act="ok">✓ 一致</button>
        <button class="bad ${d.state === 'bad' ? 'on' : ''}" data-act="bad">✗ 有偏差</button>
        <span class="note"><input placeholder="偏差在哪（可空）" value="${esc(d.note || '')}" /></span>
      </div>
    </section>`;
  }).join('');

  list.querySelectorAll('.card').forEach(card => {
    const name = card.dataset.name;
    const area = card.querySelector('.mine');
    card.querySelector('[data-act="reveal"]').onclick = () => {
      card.querySelector('.reveal').style.display = 'block';
    };
    area.onchange = () => {
      const cur = DECISIONS[name] || {};
      DECISIONS[name] = { ...cur, mine: area.value };
      localStorage.setItem('p4-review', JSON.stringify(DECISIONS));
    };
    card.querySelectorAll('[data-act="ok"],[data-act="bad"]').forEach(btn => {
      btn.onclick = () => {
        DECISIONS[name] = {
          state: btn.dataset.act,
          mine: area.value,
          note: card.querySelector('.note input').value,
        };
        localStorage.setItem('p4-review', JSON.stringify(DECISIONS));
        render();
      };
    });
  });
  updateProgress();
}

function fieldRow(label, value, hit, note) {
  const v = Array.isArray(value) ? (value.length ? value.join('；') : '（空）') : (value || '（空）');
  const tag = note
    ? `<span class="tag infer">${esc(note)}</span>`
    : hit === null ? '' : (hit ? '<span class="tag hit">原文命中</span>' : '<span class="tag miss">原文没找到</span>');
  return `<div class="fld"><span class="k">${esc(label)}</span><span class="v">${esc(v)}</span>${tag}</div>`;
}

function render() {
  if (mode === 'ledger') return renderLedger();
  if (mode === 'reverse') return renderReverse();
  const rows = pickRows();
  const list = document.getElementById('list');
  if (!rows.length) { list.innerHTML = '<div class="empty">这一类没有条目。</div>'; updateProgress(); return; }
  list.innerHTML = rows.map((r, i) => {
    const d = DECISIONS[r.name] || {};
    const dr = r.draft || {};
    const src = Object.entries(r.source)
      .map(([k, v]) => `<div class="src"><b>【${esc(k)}】</b><p>${esc(v)}</p></div>`).join('');
    const checks = r.checks.map(c => fieldRow(c.label, c.value, c.hit, c.note)).join('');
    const issues = r.issues.length
      ? `<div class="issues">机器标了 ${r.issues.length} 条疑点：<br>${r.issues.map(esc).join('<br>')}</div>` : '';
    const status = dr.status
      ? `<span class="tag ${dr.status === 'approved' ? 'hit' : dr.status === 'rejected' ? 'miss' : 'infer'}">${esc(STATUS_LABEL[dr.status] || dr.status)}</span>` : '';
    return `
    <section class="card" data-name="${esc(r.name)}">
      <div class="card-head">
        <div>
          <span class="name">${esc(r.name)}</span>
          <span class="meta">　p${r.page}　${esc(r.chapter)}${r.section ? ' · ' + esc(r.section) : ''}</span>
        </div>
        <span class="meta">${status}　${i + 1} / ${rows.length}</span>
      </div>
      ${issues}
      <div class="grid">
        <div class="pane"><h4>教材原文</h4>${src}</div>
        <div class="pane">
          <h4>抽出来的字段</h4>
          ${fieldRow('四气', r.fields['四气'], null)}
          ${fieldRow('五味', r.fields['五味'], null)}
          ${fieldRow('归经', r.fields['归经'], null)}
          ${fieldRow('毒性', r.fields['毒性'], null)}
          ${fieldRow('功效', r.fields['功效'], null)}
          ${fieldRow('主治', r.fields['主治'], null)}
          ${fieldRow('剂量', r.fields['剂量'], null)}
          ${fieldRow('使用注意', r.fields['使用注意'], null)}
          <div style="margin-top:10px;padding-top:8px;border-top:1px solid var(--line)">
            <h4 style="margin-bottom:6px">机器核对（自动）</h4>
            ${checks}
          </div>
        </div>
      </div>
      <div class="decide">
        <button class="ok ${d.state === 'ok' ? 'on' : ''}" data-act="ok">✓ 通过</button>
        <button class="bad ${d.state === 'bad' ? 'on' : ''}" data-act="bad">✗ 有问题</button>
        <button class="${d.state === 'skip' ? 'on' : ''}" data-act="skip">? 看不懂</button>
        <span class="note"><input placeholder="有问题就写一句（可空）" value="${esc(d.note || '')}" /></span>
        ${d.state ? `<span class="done">已记：${d.state === 'ok' ? '通过' : d.state === 'bad' ? '有问题' : '跳过'}</span>` : ''}
      </div>
    </section>`;
  }).join('');

  list.querySelectorAll('.card').forEach(card => {
    const name = card.dataset.name;
    card.querySelectorAll('[data-act]').forEach(btn => {
      btn.onclick = () => {
        const note = card.querySelector('.note input').value;
        DECISIONS[name] = { state: btn.dataset.act, note };
        localStorage.setItem('p4-review', JSON.stringify(DECISIONS));
        render();
      };
    });
    const input = card.querySelector('.note input');
    input.onchange = () => {
      const cur = DECISIONS[name] || {};
      DECISIONS[name] = { state: cur.state || '', note: input.value };
      localStorage.setItem('p4-review', JSON.stringify(DECISIONS));
    };
  });
  updateProgress();
}

function updateProgress() {
  const done = Object.values(DECISIONS).filter(d => d.state).length;
  const bad = Object.values(DECISIONS).filter(d => d.state === 'bad').length;
  document.getElementById('progress').textContent =
    done ? `已处理 ${done} 条，其中标了问题 ${bad} 条` : '尚未开始';
}

const MODES = ['flag', 'ledger', 'reverse', 'all'];
MODES.forEach(m => { document.getElementById('m-' + m).onclick = () => setMode(m); });
function setMode(m) {
  mode = m;
  MODES.forEach(id => document.getElementById(id).classList.remove('on'));
  document.getElementById('m-' + m).classList.add('on');
  render();
}

document.getElementById('export').onclick = () => {
  const rows = Object.entries(DECISIONS)
    .filter(([, d]) => d.state)
    .map(([name, d]) => ({ name, state: d.state, note: d.note || '' }));
  const blob = new Blob([JSON.stringify({ version: 'review_result_v1', reviewed_at: new Date().toISOString(), rows }, null, 1)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'review_result.json';
  a.click();
};

// 初始模式由队列决定（队列空则直接进「全部」），按钮高亮要跟着走。
setMode(mode);
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="生成人工复核单页")
    parser.add_argument("--out", type=Path, default=OUT_HTML)
    args = parser.parse_args()

    payload = build_payload()
    html = TEMPLATE.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"条目 {len(payload['rows'])}，需人看 {payload['stats']['needs_review']}")
    print(f"html={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
