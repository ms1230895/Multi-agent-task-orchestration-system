"""Prompt helpers shared by the agents.

Architecture layer: 4 (agents). Text from tools, files and earlier LLM replies is untrusted:
it can contain instructions meant to steer the next LLM call. `data_block` puts such text in
markers it cannot break out of.
"""

import re


def data_block(tag: str, text: str, limit: int) -> str:
    """Wrap untrusted text in `<tag>` ... `</tag>` markers.

    Args:
        tag: the marker name, for example `"output"` or `"results"`.
        text: the untrusted text. It is cut to `limit` characters first.
        limit: the maximum number of characters of `text` to keep.

    Returns:
        `"<tag>\\n<text>\\n</tag>"`, where any closing tag for `tag` inside the text has been
        changed to `<\\/tag`.

    WHY: tool output can contain `</output>` followed by fake instructions such as
    "verdict: approve". With every inner closing tag neutralised, the only real closing tag
    is the one added here, so the model can always tell where the data ends. This lowers the
    risk of prompt injection; it does not remove it.
    """
    # WHY: cut first, so a closing tag cut in half at the limit cannot become a new one.
    text = text[:limit]
    # NOTE: matches "</tag", any letter case, with optional spaces after "</".
    closing = re.compile(rf"<\s*/\s*({re.escape(tag)})", re.IGNORECASE)
    text = closing.sub(r"<\\/\1", text)
    return f"<{tag}>\n{text}\n</{tag}>"
