import asyncio

from app.llm.router import ModelRouter


async def main() -> None:
    router = ModelRouter()
    messages = [{"role": "user", "content": "请解释黄芪的功效。"}]

    for task_type in ["classify", "reason", "vision"]:
        client = router.select(task_type)
        mode = "MOCK" if client.__class__.__name__ == "MockClient" else "REAL"
        print(f"{task_type} -> {client.model_name} ({client.provider}, {mode})")

    print("stream -> ", end="")
    async for token in router.chat_stream("reason", messages):
        print(token, end="", flush=True)
    print()


if __name__ == "__main__":
    asyncio.run(main())
