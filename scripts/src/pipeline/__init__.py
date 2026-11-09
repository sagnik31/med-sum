"""
The document pipeline as a LangGraph graph: classify, transcribe, parse,
validate, repair and render, with every node recorded as a step of its
run (observability) and the graph's state checkpointed in Postgres.
"""
