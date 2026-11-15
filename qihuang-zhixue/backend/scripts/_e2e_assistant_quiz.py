"""端到端：把「要一份卷子」走完整条链，看方案卡、确认、产物。"""

import json
import sys

import httpx

BASE = "http://127.0.0.1:8000"

TEXT = sys.argv[1] if len(sys.argv) > 1 else "出一份解表药的卷子，六道题就行"
TURN = sys.argv[2] if len(sys.argv) > 2 else "e2e-quiz-1"


def parse(line: str) -> tuple[str, dict] | None:
    if not line.startswith("data: "):
        return None
    return "data", json.loads(line[6:])


with httpx.Client(base_url=BASE, timeout=180) as client:
    auth = client.post("/api/auth/guest", json={}).json()
    headers = {"Authorization": f"Bearer {auth['token']}"}

    conv = client.post("/api/assistant/conversations", json={"title": "卷子"}, headers=headers).json()
    payload = {"conversation_id": conv["conversation_id"], "text": TEXT, "client_turn_id": TURN}

    events: list[str] = []
    plan = None
    task_id = ""
    with client.stream("POST", "/api/assistant/chat/stream", json=payload, headers=headers) as response:
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            item = json.loads(line[6:])
            events.append(item["event"])
            if item["event"] == "plan_card":
                plan = item["payload"]["plan"]
                task_id = item["payload"]["task_id"]
            if item["event"] in ("done",):
                break

    print("PHASE1", events)
    if not plan:
        print("NO PLAN CARD")
        sys.exit(1)
    print("PLAN", plan.get("kind"), [unit.get("label") for unit in plan.get("units", [])])

    confirmed = client.post(
        f"/api/assistant/artifacts/{task_id}/plan/confirm",
        json={"plan": plan, "design": {}},
        headers=headers,
    )
    print("CONFIRM", confirmed.status_code, str(confirmed.json())[:160])

    tail: list[str] = []
    artifact = None
    with client.stream("GET", f"/api/assistant/artifacts/{task_id}/stream", headers=headers) as response:
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            item = json.loads(line[6:])
            tail.append(item["event"])
            if item["event"] == "artifact":
                artifact = item["payload"]
            if item["event"] in ("done", "error"):
                break
    print("PHASE2", tail[-6:])
    print("ARTIFACT", artifact)
