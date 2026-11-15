"""从注册表生成 ``docs/capabilities.md`` 与快照 JSON。

文档不手写。手写的文档一定会和代码分叉——这个项目里已经有前例（导图的
「三步流程」在三处写法各不相同）。这里把声明当唯一源头，文档是一次渲染。

用法：

    python -m app.capabilities.docs            # 写文件
    python -m app.capabilities.docs --check    # 只比对，过期就非零退出
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import registry
from .bootstrap import install

ROOT = Path(__file__).resolve().parents[3]  # 仓库根：backend/app/capabilities/docs.py 往上三层
DOC_PATH = ROOT / "docs" / "capabilities.md"
SNAPSHOT_PATH = ROOT / "docs" / "capabilities.snapshot.json"

_KIND_LABEL = {"job": "提交即返回", "query": "同步查询", "command": "同步命令"}


def _bullets(items: tuple[str, ...]) -> str:
    return "\n".join(f"- {item}" for item in items) or "- （无）"


def render(snapshot: dict[str, Any]) -> str:
    caps = snapshot["capabilities"]
    lines = [
        "# 能力契约",
        "",
        "> 本文件由 `python -m app.capabilities.docs` 生成，**不要手改**。",
        "> 改契约请改 `backend/app/capabilities/handlers/*.py` 里的 `CapabilitySpec`。",
        "",
        f"共 {snapshot['count']} 个能力，schema 版本 {snapshot['schema_version']}。",
        "",
        "## 目录",
        "",
        "| 能力 | 类型 | 一句话 | 产物 |",
        "|---|---|---|---|",
    ]
    for cap in caps:
        artifact = cap.get("artifact", {})
        lines.append(
            f"| [`{cap['name']}`](#{cap['name'].replace('.', '')}) | {_KIND_LABEL.get(cap['kind'], cap['kind'])} | {cap['one_line']} | {artifact.get('kind', '—')} |"
        )

    lines += ["", "## 调用协议", "", "```http", "POST /api/capabilities/{name}", "Content-Type: application/json", "", "{", '  "client_invocation_id": "chat-7f3a-turn-12:mindmap:1",', '  "query": "麻黄和桂枝有什么区别",', '  "params": { "depth": 3, "structure": "auto" },', '  "context": { "conversation_id": "c_9f1" }', "}", "```", ""]
    lines += [
        "`params` 可以省略——顶层字段与 `params` 里的同名字段等价，顶层优先。",
        "",
        "- `client_invocation_id` 幂等键：**同样的键 + 同样的参数**只受理一次，重放返回同一个 `task_id`，响应里 `replayed: true`。",
        "- 同一个键配了不同参数是调用方的 bug，返回 409 `capability.idempotency_mismatch`。",
        "- `job` 类返回 202 + `task_id` + `stream_url`；`query` / `command` 类同步 200。",
        "",
        "轮询兜底与取消：",
        "",
        "```http",
        "GET  /api/capability-invocations/{invocation_id}",
        "POST /api/capability-invocations/{invocation_id}/cancel",
        "```",
        "",
        "## 能力",
        "",
    ]

    for cap in caps:
        anchor = cap["name"].replace(".", "")
        lines += [f"### {cap['name']}", "", f"**{cap['title']}** — {cap['one_line']}", ""]
        lines += [f"- 版本：`{cap['version']}`　类型：{_KIND_LABEL.get(cap['kind'], cap['kind'])}　幂等：{'是' if cap['idempotency'] else '否'}"]
        if cap.get("latency"):
            lines.append(f"- 耗时：{cap['latency']}")
        if cap.get("cost"):
            lines.append(f"- 成本：{cap['cost']}")
        if cap.get("requires"):
            lines.append(f"- 依赖：{'、'.join(cap['requires'])}")
        lines.append("")
        lines += ["**什么时候该用**", "", _bullets(tuple(cap["use_when"])), ""]
        lines += ["**什么时候别用**", "", _bullets(tuple(cap["avoid_when"])), ""]
        artifact = cap.get("artifact")
        if artifact:
            lines += [
                "**产物**",
                "",
                f"- 类型：`{artifact['kind']}`",
                f"- 打开地址：`{artifact['open_url_template']}`（{artifact['resolve_on']} 后可填）",
            ]
            # 一种能力落成几种体裁时，每种体裁的地址不一样。文档里漏掉，
            # 接的人只能读源码才知道 /resources 也是这条能力的产物。
            for variant in artifact.get("variants") or []:
                lines.append(f"- 也可能是 `{variant['kind']}`：`{variant['open_url_template']}`")
            lines.append("")
        if cap["pipeline"]:
            lines += ["**工序**", "", " → ".join(step["label"] for step in cap["pipeline"]), ""]
        if cap["events"]:
            lines += [f"**流事件**：{'、'.join(f'`{event}`' for event in cap['events'])}", ""]
        if cap["errors"]:
            lines += ["**可预期失败**", "", "| 码 | HTTP | 说明 |", "|---|---|---|"]
            lines += [f"| `{err['code']}` | {err['http_status']} | {err['message']} |" for err in cap["errors"]]
            lines.append("")
        if cap.get("side_effects"):
            lines += ["**副作用**", "", _bullets(tuple(cap["side_effects"])), ""]
        if cap.get("examples"):
            lines += ["**例子**", "", _bullets(tuple(f"「{item}」" for item in cap["examples"])), ""]
        if cap.get("deprecation"):
            lines += [f"> {cap['deprecation']}", ""]
        del anchor

    return "\n".join(lines).rstrip() + "\n"


def build() -> tuple[str, dict[str, Any]]:
    install()
    snapshot = registry.snapshot()
    return render(snapshot), snapshot


def write(doc_path: Path = DOC_PATH, snapshot_path: Path = SNAPSHOT_PATH) -> tuple[Path, Path]:
    text, snapshot = build()
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(text, encoding="utf-8")
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return doc_path, snapshot_path


def check(doc_path: Path = DOC_PATH, snapshot_path: Path = SNAPSHOT_PATH) -> list[str]:
    """返回过期项。空列表表示文档与代码一致。"""

    text, snapshot = build()
    stale: list[str] = []
    if not doc_path.exists() or doc_path.read_text(encoding="utf-8") != text:
        stale.append(str(doc_path))
    expected = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
    if not snapshot_path.exists() or snapshot_path.read_text(encoding="utf-8") != expected:
        stale.append(str(snapshot_path))
    return stale


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--check" in args:
        stale = check()
        if stale:
            print("文档已过期：" + "、".join(stale))
            print("跑一次 python -m app.capabilities.docs 重新生成。")
            return 1
        print("能力文档与代码一致")
        return 0
    doc, snap = write()
    print(f"已生成 {doc}")
    print(f"已生成 {snap}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
