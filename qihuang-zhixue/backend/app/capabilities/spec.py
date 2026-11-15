"""能力声明的数据结构。纯 dataclass + pydantic 模型，没有任何 IO。

字段取舍的理由，逐条：

``kind``
    ``job`` 是「提交即返回、跑完再取结果」（导图生成 10~20 秒，不能同步等）；
    ``query`` 是「问一句答一句」（图谱查询）；
    ``command`` 是「改一个既定产物的一处」（换成鱼骨图）。三种调用方写法不同，
    混成一个会让调用方猜该不该轮询。

``use_when`` / ``avoid_when``
    每条都带一个真实问句。只写「适用于关系查询」对模型没有约束力——它需要
    看到「长什么样的句子」。这条在 ``kg/contract.py`` 已被验证过一次。

``artifact``
    产物在哪。没有这个字段，调用方只能把整棵树塞进上下文——那既贵又没用，
    用户要的是「打开看」，不是「读一遍」。

``audiences``
    同一份声明要喂给模型、前端、人三种读者。前端只需要 ``title/one_line/artifact``，
    模型需要 ``tool_schema``，文档需要全部。写清楚受众，才不会为了省 token
    把说明删成一句空话。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from app.resources.constants import StepSpec

CapabilityKind = Literal["job", "query", "command"]
Audience = Literal["agent", "frontend", "human"]


@dataclass(frozen=True)
class ErrorSpec:
    """一条可预期的失败。``remedy`` 是给调用方的下一步，不是给人看的道歉。"""

    code: str
    http_status: int
    message: str
    remedy: str


#: 管线的一步。**这里不另定义**，直接复用 ``resources/constants.py`` 的 TypedDict：
#: 以前两处同名不同构（这里少一个 variants），导致「工序表单一事实源」只是口号——
#: 契约、前端标签、SSE 的 step_label 各自能漂移。
#:
#: 现在只有一份定义，handler 直接 ``steps_for(type, CURRENT_PIPELINE_VERSION)``。


@dataclass(frozen=True)
class ArtifactSpec:
    """产物的形状。

    ``open_url_template`` 用 ``{resource_id}`` 占位：生成中 resource_id 还没有，
    只能先给模板，``resolve_on`` 说明什么时候能填上。
    """

    kind: str
    open_url_template: str
    resolve_on: Literal["done", "accepted"] = "done"
    structure: str = "auto"
    theme: str = ""
    #: 产物里最能回答「这图靠不靠得住」的三个数。
    evidence_tiers: tuple[str, ...] = ("textbook", "graph", "model")
    #: 同一个能力可能落成**不同体裁**，而体裁决定打开哪一页：``plan.present``
    #: 既能出导图（``/mindmap``）也能出图文（``/resources``），选哪个要到跑完
    #: 才知道。这里只补「与主体裁不同」的那几种，主体裁仍用 ``open_url_template``，
    #: 免得每个能力都把自己写两遍。
    variants: tuple[tuple[str, str], ...] = ()

    def template_for(self, kind: str) -> str:
        """某种体裁的打开地址模板；认不得的体裁回落到主模板。

        存在的理由：以前「声明地址的地方」与「拼地址的地方」是两处
        （spec 里写 mindmap、``present.open_url_for`` 里自己 if/else），
        结果导图对得上、图文对不上，只有用户点开那一份才发现。
        """

        for name, template in self.variants:
            if name == kind:
                return template
        return self.open_url_template


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    version: str
    kind: CapabilityKind
    title: str
    one_line: str
    use_when: tuple[str, ...]
    avoid_when: tuple[str, ...]
    triggers: tuple[str, ...]
    input_model: type[BaseModel]
    output_shape: dict[str, str]
    audiences: tuple[Audience, ...] = ("agent", "frontend", "human")
    artifact: ArtifactSpec | None = None
    pipeline: tuple[StepSpec, ...] = ()
    events: tuple[str, ...] = ()
    idempotency: bool = False
    errors: tuple[ErrorSpec, ...] = ()
    side_effects: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    cost: str = ""
    latency: str = ""
    examples: tuple[str, ...] = ()
    deprecation: str = ""

    # -- 派生视图 ---------------------------------------------------------

    def public(self) -> dict[str, Any]:
        """GET /api/capabilities 里的一条。不含 input schema 之外的实现细节。"""

        data: dict[str, Any] = {
            "name": self.name,
            "version": self.version,
            "kind": self.kind,
            "title": self.title,
            "one_line": self.one_line,
            "use_when": list(self.use_when),
            "avoid_when": list(self.avoid_when),
            "audiences": list(self.audiences),
            "input_schema": self.input_model.model_json_schema(),
            "output_shape": self.output_shape,
            "pipeline": [{"id": step["id"], "label": step["label"]} for step in self.pipeline],
            "events": list(self.events),
            "idempotency": self.idempotency,
            "errors": [{"code": item.code, "http_status": item.http_status, "message": item.message} for item in self.errors],
            "side_effects": list(self.side_effects),
            "requires": list(self.requires),
            "cost": self.cost,
            "latency": self.latency,
            "examples": list(self.examples),
        }
        if self.artifact is not None:
            data["artifact"] = {
                "kind": self.artifact.kind,
                "open_url_template": self.artifact.open_url_template,
                "resolve_on": self.artifact.resolve_on,
                "structure": self.artifact.structure,
                "theme": self.artifact.theme,
                "evidence_tiers": list(self.artifact.evidence_tiers),
                "variants": [{"kind": kind, "open_url_template": tpl} for kind, tpl in self.artifact.variants],
            }
        if self.deprecation:
            data["deprecation"] = self.deprecation
        return data

    def tool_schema(self) -> dict[str, Any]:
        """给对话 Agent 的 tool 定义。description 自足，不依赖外部文档。"""

        return {
            "name": self.name.replace(".", "_"),
            "description": self.description(),
            "parameters": self.input_model.model_json_schema(),
        }

    def description(self) -> str:
        """纯文本说明。盲测、文档、tool description 吃同一份。"""

        def bullets(items: tuple[str, ...]) -> str:
            return "\n".join(f"- {item}" for item in items)

        parts = [
            f"【能力】{self.name} — {self.one_line}",
            f"【什么时候该用】\n{bullets(self.use_when)}",
            f"【什么时候别用】\n{bullets(self.avoid_when)}",
        ]
        if self.artifact is not None:
            parts.append(f"【产物】{self.artifact.kind}，完成后打开 {self.artifact.open_url_template}")
            # 同一能力能落成几种体裁时说清楚，否则模型只会说"给你导成导图"，
            # 而用户要的是能打印的文稿时它不知道该改哪个参数。
            for kind, template in self.artifact.variants:
                parts.append(f"【产物·另一体裁】{kind}，完成后打开 {template}")
        if self.latency:
            parts.append(f"【耗时】{self.latency}")
        if self.errors:
            parts.append("【失败】" + "；".join(f"{item.code}={item.message}" for item in self.errors))
        return "\n".join(parts)


@dataclass
class CapabilityResult:
    """统一返回值。``http_status`` 只在这里出现一次，handler 不自己拼响应。"""

    http_status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)
