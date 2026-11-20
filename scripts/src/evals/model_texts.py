"""
Every text the project sends a model as its instructions, by name: the
pipeline's prompt files, the overview prompt, the tool repair's notes, the
tool probe's and the MCP eval's system prompts, and the MCP server's
instructions to its client. One list, so the history's prompt hashes and
the prompt-contamination test can't miss one.
"""

from pathlib import Path


def model_texts() -> dict[str, str]:
    """Each instruction text, by name (files by their prompt name)."""
    from evals import mcp_eval, toolprobe
    from medsum_mcp.tools import INSTRUCTIONS
    from pipeline import tool_repair
    from pipeline.prompts import FILES, prompt
    from summary.build import OVERVIEW_PROMPT_FILE

    texts = {name: prompt(name) for name in FILES}
    texts["summary_overview"] = OVERVIEW_PROMPT_FILE.read_text(encoding="utf-8")
    texts["tool_repair.reading_note"] = tool_repair.READING_NOTE
    texts["tool_repair.final_note"] = tool_repair.FINAL_NOTE
    texts["toolprobe.system"] = toolprobe.SYSTEM
    texts["mcp_eval.system"] = mcp_eval.SYSTEM
    texts["mcp_server.instructions"] = INSTRUCTIONS
    return texts


def text_files() -> dict[str, Path]:
    """The instruction texts that are files, by name."""
    from pipeline.prompts import FILES
    from summary.build import OVERVIEW_PROMPT_FILE

    return {**FILES, "summary_overview": OVERVIEW_PROMPT_FILE}
