"""漫剧（short story）生成 agent。

为什么单独写一个，而不是把 story 映射到 ``reading``：``reading`` 产出的是
「延伸阅读推荐」（标题 + 一句推荐理由），把它当漫剧交付，用户看到的是一份书单。
形态差得太远，不算「同一件事的另一种呈现」，只能另起一份输出契约。

输出两份东西：

1. ``scenes`` —— 结构化的场次，给未来「分镜播放器」用（每场有出场人、场景、
   台词、知识点）。先存着，不改协议就能接播放器。
2. ``blocks`` —— 由场次派生的可读文稿块。资源详情页已经能渲染文稿块，因此在
   播放器落地之前，漫剧至少是一份能读的剧本——而不是一张空白页。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.agents.resource_context import ResourceDoc
from app.agents.resource_schemas import SourceRef
from app.agents.structured_output import generated_by, structured_create

SYSTEM_PROMPT = (
    "你是中药学情景短剧编剧。只基于 retrieved_docs 编写分镜，不得虚构药材功效或禁忌。"
    "硬规则：每场只推进一件事；知识点放在对白里，不写成旁白说教；"
    "台词要像人说话，每句不超过 40 字；涉及剂量与禁忌处必须与 retrieved_docs 一致。"
)


class StoryLine(BaseModel):
    speaker: str = Field(description="说话人，如「学徒」「老药工」")
    text: str = Field(description="台词，一句不超过 40 字")


class StoryScene(BaseModel):
    index: int = Field(description="第几场，从 1 开始")
    title: str = Field(description="这场的小标题，如「抓错药」")
    setting: str = Field(default="", description="场景一句话，如「药铺柜台，午后」")
    lines: list[StoryLine] = Field(default_factory=list, description="台词")
    knowledge_point: str = Field(default="", description="这场要落下的知识点")


class StoryPayload(BaseModel):
    title: str = Field(description="短剧标题")
    logline: str = Field(default="", description="一句话讲清这出戏讲什么")
    scenes: list[StoryScene] = Field(default_factory=list, description="场次")


def _mock_payload(topic: str, docs: list[ResourceDoc]) -> StoryPayload:
    """确定性兜底：按教材条目铺三场。

    没有模型时也要能交付一份能读的剧本，否则「无 key 也能跑通」只对三个老工坊成立。
    台词只复述条目里已有的功效与禁忌，不编。
    """

    doc = docs[0] if docs else None
    name = str(doc["name"]) if doc else topic[:12]
    effects = "、".join(doc["effects"][:3]) if doc else "对症用药"
    caution = str((doc["cautions"] or ["证候不符不可用"])[0]) if doc else "证候不符不可用"

    return StoryPayload(
        title=f"{name}的一夜",
        logline=f"学徒把{name}用错了人，老药工用三句话讲清它为什么只治那一种证。",
        scenes=[
            StoryScene(
                index=1,
                title="抓药",
                setting="药铺柜台，午后。",
                lines=[
                    StoryLine(speaker="学徒", text=f"师父，这位客人发热，我给他抓{name}。"),
                    StoryLine(speaker="老药工", text="先别急。你告诉我，他怕冷还是怕热？"),
                ],
                knowledge_point=f"{name}的功效是{effects}，用在证候相符的人身上。",
            ),
            StoryScene(
                index=2,
                title="问证",
                setting="柜台前，客人插话。",
                lines=[
                    StoryLine(speaker="客人", text="我怕冷，还出汗。"),
                    StoryLine(speaker="老药工", text=f"那就不是{name}的证。{caution}"),
                ],
                knowledge_point=caution,
            ),
            StoryScene(
                index=3,
                title="收方",
                setting="药铺后院，黄昏。",
                lines=[
                    StoryLine(speaker="学徒", text="原来同一个发热，药也不一样。"),
                    StoryLine(speaker="老药工", text=f"记住：{name}先看证，再看人。"),
                ],
                knowledge_point="先辨证，再选药；同一症状下，药随证变。",
            ),
        ],
    )


def _blocks_of(payload: StoryPayload) -> list[dict[str, Any]]:
    """把场次摊成文稿块：资源详情页已经会渲染这几类块，不再另造渲染器。"""

    blocks: list[dict[str, Any]] = [{"id": "st_h0", "type": "heading", "text": payload.title, "props": {"level": 1}}]
    if payload.logline:
        blocks.append({"id": "st_p0", "type": "paragraph", "text": payload.logline, "props": {}})
    for scene in payload.scenes:
        blocks.append(
            {
                "id": f"st_h{scene.index}",
                "type": "heading",
                "text": f"第 {scene.index} 场 · {scene.title}",
                "props": {"level": 2},
            }
        )
        if scene.setting:
            blocks.append({"id": f"st_s{scene.index}", "type": "quote", "text": scene.setting, "props": {}})
        for line_index, line in enumerate(scene.lines):
            blocks.append(
                {
                    "id": f"st_l{scene.index}_{line_index}",
                    "type": "paragraph",
                    "text": f"{line.speaker}：{line.text}",
                    "props": {"speaker": line.speaker},
                }
            )
        if scene.knowledge_point:
            blocks.append(
                {
                    "id": f"st_k{scene.index}",
                    "type": "callout",
                    "text": scene.knowledge_point,
                    "props": {"tone": "info", "icon": "知"},
                }
            )
    return blocks


def _sources_of(docs: list[ResourceDoc]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for doc in docs[:5]:
        quotes = doc.get("effects") or []
        sources.append(
            SourceRef(
                name=str(doc["name"]),
                type="教材",
                ref=str(doc.get("id") or doc["name"]),
                quote="、".join(quotes[:3]),
            ).model_dump(mode="json")
        )
    return sources


def fallback_story(topic: str, docs: list[ResourceDoc]) -> dict[str, Any]:
    """确定性兜底内容（含可读块）。给「模型没给出场次」时用，调用方负责标注来源。"""

    payload = _mock_payload(topic, docs)
    content = payload.model_dump(mode="json")
    content["blocks"] = _blocks_of(payload)
    content["fallback"] = "教材复述（模型未返回可用分镜）"
    return content


async def generate_story_resource(
    *,
    topic: str,
    retrieved_docs: list[ResourceDoc],
    profile: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """生成漫剧分镜，返回 ``(content, sources)``。"""

    payload = await structured_create(
        task_type="generate",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"topic={topic}\nprofile={profile or {}}\nretrieved_docs={retrieved_docs}"},
        ],
        response_model=StoryPayload,
        mock_factory=lambda: _mock_payload(topic, retrieved_docs),
    )
    content = payload.model_dump(mode="json")
    content["blocks"] = _blocks_of(payload)
    content["generated_by"] = generated_by("generate")
    return content, _sources_of(retrieved_docs)
