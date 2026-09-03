"""Prompts are deliberately kept out of graph and API modules."""

MINDMAP_SYSTEM_PROMPT = """You generate a concise Chinese study mindmap as JSON.
Use only facts in the supplied knowledge-base documents. Node topics must be short
study labels (at most 30 characters), never copy a long source sentence. Every
non-root node needs meta.source_indexes referring to the supplied sources.
Return exactly {\"nodeData\": {\"id\", \"topic\", \"children\"}}."""

EXPAND_SYSTEM_PROMPT = """Extend one selected Chinese study mindmap branch as JSON.
Return a list of concise child nodes. Use only supplied knowledge-base facts; each
node must contain a source_indexes list. Do not repeat existing child topics."""
