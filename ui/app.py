"""Entry point of the Streamlit app: picks the mode and the pages.

Architecture layer: 9 (interface). Start it with `python -m streamlit run ui/app.py` from the
project root (`python -m` puts the root on `sys.path`, so `from src...` works).

Two modes. Live mode shows the Run page and can start real LLM runs. Replay-only mode shows
only the trace explorer and the dashboard and has no code path to `run_task`. Replay-only is
the default, so a copy of the app that is hosted somewhere can never spend quota or run tools.
"""

from collections.abc import Callable
from dataclasses import dataclass

import streamlit as st

from src.config import ConfigError, get_settings, get_ui_settings
from ui.pages import dashboard, trace_explorer

# WHY: one place for the page titles, so the app and its test agree on them.
RUN_TITLE = "Run a task"
EXPLORER_TITLE = "Trace explorer"
DASHBOARD_TITLE = "Benchmark dashboard"


def resolve_mode() -> tuple[bool, str]:
    """Decide between live and replay-only mode.

    Returns:
        `(live, note)`. `live` is True only when `UI_ALLOW_LIVE_RUNS` is true and the main
        settings load. `note` says why live runs are off (`""` in live mode). A `ConfigError`
        text names the variable, never its value (Hard rule 7).
    """
    try:
        ui_settings = get_ui_settings()
    except ConfigError as error:
        return False, str(error)
    if not ui_settings.allow_live_runs:
        return False, "Live runs are off. Set UI_ALLOW_LIVE_RUNS=true in .env to show the Run page."
    try:
        get_settings()
    except ConfigError as error:
        return False, str(error)
    return True, ""


@dataclass(frozen=True)
class PageSpec:
    """What the app needs to make one page: its title, its URL name and the function that draws it.

    WHY a plain record and not `st.Page`: a test can read the page list without a running
    Streamlit app.
    """

    title: str
    url_path: str
    render: Callable[[], None]


def pages_for(live: bool) -> list[PageSpec]:
    """Return the pages of a mode.

    Args:
        live: True for live mode.

    Returns:
        The explorer and the dashboard, plus the Run page first in live mode.

    WHY: a page that is not in the list cannot be opened, so replay-only mode cannot reach the
    Run page. The Run page module is imported only in live mode (defence in depth).
    """
    pages = [
        PageSpec(EXPLORER_TITLE, "explorer", trace_explorer.render),
        PageSpec(DASHBOARD_TITLE, "dashboard", dashboard.render),
    ]
    if live:
        from ui.pages import live_run  # noqa: PLC0415 - imported only in live mode, see WHY

        pages.insert(0, PageSpec(RUN_TITLE, "run", live_run.render))
    return pages


def main() -> None:
    """Draw the app: page config, mode note, navigation, then the chosen page."""
    st.set_page_config(page_title="Multi-agent orchestrator", layout="wide")
    live, note = resolve_mode()
    page = st.navigation(
        [st.Page(spec.render, title=spec.title, url_path=spec.url_path) for spec in pages_for(live)]
    )
    if note:
        st.sidebar.info(note)
    page.run()


# WHY: Streamlit runs the script as `__main__`; the guard lets a test import this module
# without drawing a page.
if __name__ == "__main__":
    main()
