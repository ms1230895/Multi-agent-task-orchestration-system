"""Text helpers shared by the task-file validation and the checkers.

Architecture layer: 7 (Benchmark). Pure functions, no files, no network, no LLM (Hard rule 6).
They live in their own file so `tasks.py` and `checkers.py` can both use them without
importing each other.
"""

import re
import unicodedata

# WHY: the model writes numbers like `29,031.69` (first-run problem P5) and sometimes uses a
# no-break space (U+00A0), a narrow no-break space (U+202F) or a thin space (U+2009) as a
# thousands separator. All of them must read as one number.
_THOUSANDS_SEPARATORS = ",   "

# WHY: a number is: an optional minus, an integer part (groups of exactly 3 digits after a
# separator, or plain digits), an optional decimal part and an optional exponent.
# - `(?<![A-Za-z0-9])-` makes the minus a sign only when no letter or digit comes before it,
#   so `2026-10-16` is three numbers and `-5 degrees` is -5.
# - `(?!\d)` after the grouped form stops `1,0001` from reading as 1000 followed by 1.
# - Digits are written `[0-9]` so other scripts' digits are not read by accident.
_NUMBER = re.compile(
    rf"""
    (?P<sign>(?<![A-Za-z0-9])-)?
    (?P<int>
        [0-9]{{1,3}}(?:[{_THOUSANDS_SEPARATORS}][0-9]{{3}})+(?![0-9])
        |
        [0-9]+
    )
    (?P<frac>\.[0-9]+)?
    (?P<exp>[eE][+-]?[0-9]+)?
    """,
    re.VERBOSE,
)

# WHY: a letter or digit right before or after a keyword means it is only part of a longer
# word ("art" inside "Mozart"). `[^\W_]` is "a letter or digit" (underscore excluded).
_NOT_ALNUM_BEFORE = r"(?<![^\W_])"
_NOT_ALNUM_AFTER = r"(?![^\W_])"


def extract_numbers(text: str) -> list[float]:
    """Return every number in the text, in order.

    Args:
        text: any text, for example an agent's final answer.

    Returns:
        The numbers as floats. Handles integers, decimals, thousands separators
        (`1,048,576`), scientific notation (`1.5e10`) and a leading minus sign.

    Not handled (documented limits): decimal commas (`8,45`), the Unicode minus sign,
    `x10^` notation and numbers written in words.

    Why: the `number` check passes when any extracted number matches the expected one.
    """
    numbers: list[float] = []
    for match in _NUMBER.finditer(text):
        # WHY: drop the separators before `float()`, which does not accept them.
        integer = re.sub(f"[{_THOUSANDS_SEPARATORS}]", "", match["int"])
        literal = f"{integer}{match['frac'] or ''}{match['exp'] or ''}"
        value = float(literal)
        numbers.append(-value if match["sign"] else value)
    return numbers


def normalize_words(text: str) -> str:
    """Lower-case, remove accents, and collapse whitespace.

    Why: "García Márquez" must match the keyword "Marquez", and a line break inside a name
    must not stop a match.
    """
    # WHY: NFKD splits "é" into "e" plus an accent mark, which we then drop.
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_marks.casefold().split())


def contains_phrase(text: str, phrase: str) -> bool:
    """Tell whether `phrase` appears in `text` as a whole word or phrase.

    Args:
        text: the text to search.
        phrase: the keyword or phrase.

    Returns:
        True when the normalised phrase is in the normalised text with no letter or digit
        directly before or after it. An empty phrase never matches.
    """
    wanted = normalize_words(phrase)
    if not wanted:
        return False
    pattern = f"{_NOT_ALNUM_BEFORE}{re.escape(wanted)}{_NOT_ALNUM_AFTER}"
    return re.search(pattern, normalize_words(text)) is not None


def normalize_lines(text: str) -> str:
    """Normalise line endings and blank edges, keeping capitals, spaces inside lines and quotes.

    Why: a file that ends with a newline holds the same text as one that does not, and
    Windows line endings are not a real difference. Anything else (quotes added, capitals
    changed) is a real difference for the `exact` check.
    """
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in unified.split("\n")]
    return "\n".join(lines).strip("\n")
