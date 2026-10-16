"""The test's own copy of the 30-task table from `docs/specs/benchmark.md` section 5.3 (AC2).

Why a separate copy: if the tests only read `tasks.json` back, an edited expected answer would
still pass. Here the values are typed by hand from the spec (Hard rule 6).
"""

from typing import Any

ExpectedTask = dict[str, Any]


def _num(value: float, tolerance: float = 0.0, file: str | None = None) -> dict[str, Any]:
    """Return a full `number` check as `model_dump()` shows it."""
    return {"type": "number", "file": file, "value": value, "tolerance": tolerance}


def _has(words: list[str], file: str | None = None) -> dict[str, Any]:
    """Return a full `contains` check with only an `all` list."""
    return {"type": "contains", "file": file, "all": words, "any": [], "none": []}


def _exact(text: str, file: str) -> dict[str, Any]:
    """Return a full `exact` check."""
    return {"type": "exact", "file": file, "text": text}


def _task(
    task_id: str,
    category: str,
    text: str,
    check: dict[str, Any],
    setup: dict[str, str] | None = None,
) -> ExpectedTask:
    """Build one expected task."""
    return {
        "id": task_id,
        "category": category,
        "task": text,
        "setup_files": setup or {},
        "checks": [check],
    }


EXPECTED_TASKS: list[ExpectedTask] = [
    _task("calc-01", "calc", "Use the calculator: what is 347 * 29 + 1563?", _num(11626)),
    _task("calc-02", "calc", "What is the square root of 7056? Use the calculator.", _num(84)),
    _task("calc-03", "calc", "Use the calculator to compute (2 ** 16 - 1) / 5.", _num(13107)),
    _task(
        "calc-04",
        "calc",
        "An investment of 5000 grows by 4% per year, compounded yearly. What is it worth "
        "after 10 years? Use the calculator and round to 2 decimal places.",
        _num(7401.22, 0.01),
    ),
    _task("calc-05", "calc", "What is 15% of 2480? Use the calculator.", _num(372)),
    _task("calc-06", "calc", "Use the calculator: what is 1234567 modulo 97?", _num(48)),
    _task(
        "calc-07",
        "calc",
        "Use the calculator: what is the natural logarithm of 1000, rounded to 4 decimal places?",
        _num(6.9078, 0.0001),
    ),
    _task(
        "calc-08",
        "calc",
        "A rectangle is 12.5 m long and 7.2 m wide. Use the calculator to find its area in "
        "square metres.",
        _num(90),
    ),
    _task(
        "search-01",
        "search",
        "Search the web: in what year did the Berlin Wall fall?",
        _num(1989),
    ),
    _task(
        "search-02",
        "search",
        "Search the web: what is the atomic number of gold?",
        _num(79),
    ),
    _task(
        "search-03",
        "search",
        "Search the web: who painted the ceiling of the Sistine Chapel?",
        _has(["Michelangelo"]),
    ),
    _task(
        "search-04",
        "search",
        "Search the web: what is the capital city of Australia?",
        _has(["Canberra"]),
    ),
    _task(
        "search-05",
        "search",
        "Search the web: in what year was version 0.9.0 of the Python programming language "
        "first released?",
        _num(1991),
    ),
    _task(
        "search-06",
        "search",
        "Search the web: what is the speed of light in a vacuum, in metres per second?",
        _num(299792458),
    ),
    _task(
        "search-07",
        "search",
        "Search the web: who wrote the novel One Hundred Years of Solitude?",
        _has(["Marquez"]),
    ),
    _task(
        "file-01",
        "file",
        "Write the text 'benchmark ok' to a file called status.txt.",
        _exact("benchmark ok", "status.txt"),
    ),
    _task(
        "file-02",
        "file",
        "Read the file notes.txt and tell me what its third line says.",
        _has(["harbor"]),
        {"notes.txt": "copper\nviolet\nharbor\n"},
    ),
    _task(
        "file-03",
        "file",
        "How many non-empty lines does the file items.txt have?",
        _num(7),
        {"items.txt": "pen\nbook\nlamp\n\ncup\nkey\nmap\nbox\n"},
    ),
    _task(
        "file-04",
        "file",
        "Read name.txt and write its contents in uppercase to a file called upper.txt.",
        _exact("ADA LOVELACE", "upper.txt"),
        {"name.txt": "ada lovelace\n"},
    ),
    _task(
        "file-05",
        "file",
        "Create a file called shopping.txt with three lines: eggs, milk, bread (one item per "
        "line, in this order, nothing else).",
        _exact("eggs\nmilk\nbread", "shopping.txt"),
    ),
    _task(
        "file-06",
        "file",
        "Read config.ini and tell me which port the server uses.",
        _num(8443),
        {"config.ini": "[server]\nport = 8443\nhost = example.local\n"},
    ),
    _task(
        "file-07",
        "file",
        "Write the text 'nested ok' to the file reports/today.txt.",
        _exact("nested ok", "reports/today.txt"),
    ),
    _task(
        "multi-01",
        "multi-tool",
        "Search the web for the year the Eiffel Tower was completed, use the calculator to "
        "find how many years passed between then and 2000, and write only that number to "
        "eiffel.txt.",
        _num(111, file="eiffel.txt"),
    ),
    _task(
        "multi-02",
        "multi-tool",
        "Read scores.txt, use the calculator to find the average of the numbers in it, and "
        "tell me the result.",
        _num(25),
        {"scores.txt": "12\n45\n33\n8\n27\n"},
    ),
    _task(
        "multi-03",
        "multi-tool",
        "Use the calculator to compute 2 ** 20 and write the result to power.txt.",
        _num(1048576, file="power.txt"),
    ),
    _task(
        "multi-04",
        "multi-tool",
        "Search the web for the atomic number of carbon and the atomic number of oxygen, "
        "then use the calculator to multiply the two numbers.",
        _num(48),
    ),
    _task(
        "multi-05",
        "multi-tool",
        "Read prices.txt, use the calculator to add up the prices, and write the total to "
        "total.txt.",
        _num(8.45, 0.001, file="total.txt"),
        {"prices.txt": "apple 1.20\nbread 2.50\ncheese 4.75\n"},
    ),
    _task(
        "multi-06",
        "multi-tool",
        "Search the web for the name of the first person to walk on the Moon, and write only "
        "their full name to moon.txt.",
        _has(["Neil", "Armstrong"], file="moon.txt"),
    ),
    _task(
        "multi-07",
        "multi-tool",
        "Search the web for the speed of light in metres per second, use the calculator to "
        "find how many metres light travels in 60 seconds, and write the result to light.txt.",
        _num(17987547480, file="light.txt"),
    ),
    _task(
        "multi-08",
        "multi-tool",
        "Read country.txt, search the web for the capital city of the country named in it, "
        "and write the capital to capital.txt.",
        _has(["Tokyo"], file="capital.txt"),
        {"country.txt": "Japan\n"},
    ),
]
