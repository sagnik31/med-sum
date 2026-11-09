"""
The document pipeline as a LangGraph graph: classify, transcribe, parse,
validate, repair and render, with every node recorded as a step of its
run (observability) and the graph's state checkpointed in Postgres.

Local only: LangSmith (LangChain's cloud tracing) is switched off here,
whatever the environment says, so no run is ever sent anywhere. Runs are
traced in the database instead (observability).
"""

import os

for _name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
    os.environ[_name] = "false"
