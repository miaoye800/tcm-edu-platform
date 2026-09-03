"""All model-facing QuizAgent prompt fragments live here."""

SEMANTIC_GRADING_SYSTEM = """You grade a Chinese medicine learning response. Return only JSON:
{\"score\":0-100,\"missing_points\":[string],\"comment\":string}.
Grade only against the provided answer points. Give one concise Chinese comment and do not invent facts."""

