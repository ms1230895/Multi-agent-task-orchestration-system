"""Turn an LLM reply into a validated Pydantic model, without ever raising.

Architecture layer: 4 (agents). The planner and the critic both ask the LLM for JSON, and
both need the same behaviour: parse it, and when it is wrong, produce a short readable
error that can be sent back to the LLM in a single re-ask.
"""

import re

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, ValidationError

# WHY: an error text goes back into a prompt, so it must stay short.
MAX_ERROR_CHARS = 300

# NOTE: matches ```json ... ``` and ``` ... ``` fences, with the JSON inside as group 1.
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def reply_text(message: BaseMessage) -> str:
    """Return the text of an LLM reply as a plain `str`.

    WHY: in langchain-core 1.x, `message.text` is a `TextAccessor`, not a `str`. Wrapping it
    in `str(...)` makes both agents read the reply the same way and keeps Pylance happy.
    """
    return str(message.text)


def _describe(error: ValidationError) -> str:
    """Turn a Pydantic error into `field: problem; field: problem`, cut to the limit.

    WHY: Pydantic's own text is long and includes the input value; this keeps only what the
    LLM needs to fix its answer.
    """
    parts = []
    for item in error.errors():
        field = ".".join(str(p) for p in item["loc"]) or "reply"
        parts.append(f"{field}: {item['msg']}")
    return "; ".join(parts)[:MAX_ERROR_CHARS]


def parse_json_model[M: BaseModel](text: str, schema: type[M]) -> tuple[M | None, str | None]:
    """Find a JSON object in `text` and validate it with `schema`.

    Args:
        text: the raw reply of the LLM. It may have a code fence or chatter around the JSON.
        schema: the Pydantic model the JSON must match.

    Returns:
        `(model, None)` on success, or `(None, error_text)` on failure. Never raises.
    """
    # STEP 1: unwrap a Markdown code fence if there is one.
    # WHY: models often wrap JSON in ```json ... ``` even when told not to.
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1)

    # STEP 2: keep only the part from the first "{" to the last "}".
    # WHY: this drops chatter before and after the JSON without a full parser.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None, "no JSON object found"

    # STEP 3: validate against the schema.
    # WHY: `model_validate_json` reports broken JSON and wrong shapes as one error type.
    try:
        return schema.model_validate_json(text[start : end + 1]), None
    except ValidationError as exc:
        return None, _describe(exc)
