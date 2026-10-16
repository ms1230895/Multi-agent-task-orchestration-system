"""Turn trace records into safe, colour-coded HTML for the Streamlit pages.

Architecture layer: 9 (interface). No Streamlit import, so a test can check the HTML without
a browser. The words and colours come from `src.console.format_record` and `HTML_COLOURS`,
the same ones the terminal uses.
"""

import html
from typing import Any

from src.console import ERROR, HTML_COLOURS, format_record


def record_html(record: Any) -> str:
    """Return the HTML of one trace record: one `<div>` per line, coloured by role.

    Args:
        record: a trace record `{ts, run_id, model, event, data}`. Anything else is tolerated.

    Returns:
        HTML text. Every piece of record text goes through `html.escape`, so `<script>` or an
        `<img onerror=...>` coming from an LLM, a web page or a file stays plain text.

    WHY: trace text is untrusted. Escaping here, and showing the result with `st.html` (which
    does no Markdown), keeps every value inert. The only attribute values that are not escaped
    text are hex colours from our own `HTML_COLOURS` table.
    """
    # WHY: a display function must not stop the page. A record in an odd shape falls back to
    # one plain line instead of an exception.
    try:
        lines = format_record(record if isinstance(record, dict) else {})
    except Exception:  # noqa: BLE001 - see WHY above
        lines = [("(record could not be shown)", ERROR)]

    parts = []
    for text, colour in lines:
        hex_colour = HTML_COLOURS.get(colour)
        colour_css = f"color:{hex_colour};" if hex_colour else ""
        parts.append(f'<div style="{colour_css}white-space:pre-wrap">{html.escape(text)}</div>')
    return "".join(parts)


def timeline_html(records: list[Any]) -> str:
    """Return the HTML of many records in order.

    WHY: the live page and the trace explorer both show a timeline, so they share this one
    function and cannot drift apart.
    """
    return "".join(record_html(record) for record in records)
