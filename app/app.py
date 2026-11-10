"""EU Compliance Copilot: the Streamlit front end of eucc.agent.

    Databricks Apps: app.yaml runs `streamlit run app.py` as the app's service principal.
    Locally:         uv run streamlit run app/app.py   (CLI profile "free", your permissions)

Settings come from the env (eucc.ui.AppConfig); texts, badges and citation links from eucc.ui.
On the first load in a server process, a background thread runs SELECT 1 on the SQL warehouse,
which auto-stops when idle and takes 1-2 minutes to start (ADR-0006); the page shows "warming up
data tools" until it is done. The chat history lives in st.session_state (one per browser tab).
"""

import os
import threading
from dataclasses import dataclass, field
from typing import Any

# Outside Databricks Apps (no service principal in the env) authenticate with a CLI profile.
if "DATABRICKS_CLIENT_ID" not in os.environ:
    os.environ.setdefault("DATABRICKS_CONFIG_PROFILE", os.environ.get("EUCC_PROFILE", "free"))
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

import mlflow  # noqa: E402
import streamlit as st  # noqa: E402

from eucc import agent, ui  # noqa: E402
from eucc.tools import sql as sql_tool  # noqa: E402

CONFIG = ui.AppConfig.from_env(os.environ)
if not CONFIG.tracing:
    mlflow.tracing.disable()

WARMUP_POLL_SECONDS = 2


# ---------------------------------------------------------------- shared resources


@st.cache_resource
def agent_deps() -> agent.AgentDeps:
    """One set of tools per server process (the energy catalog is read once)."""
    return agent.default_deps(
        catalog=CONFIG.catalog,
        schema=CONFIG.schema,
        index=CONFIG.index,
        model=CONFIG.llm_endpoint,
        warehouse=CONFIG.warehouse_id,
    )


@dataclass
class Warmup:
    done: threading.Event = field(default_factory=threading.Event)
    error: str | None = None


def _warm(warmup: Warmup) -> None:
    try:
        sql_tool.run_sql("SELECT 1", warehouse=CONFIG.warehouse_id)
    except Exception as error:  # Shown on the page; the data tool retries on the next question.
        warmup.error = f"{type(error).__name__}: {error}"
    finally:
        warmup.done.set()


@st.cache_resource
def warmup() -> Warmup:
    """Started once per server process, on the first page load."""
    state = Warmup()
    threading.Thread(target=_warm, args=(state,), name="eucc-warmup", daemon=True).start()
    return state


# ---------------------------------------------------------------- rendering


def render_result(result: dict[str, Any], lang: str) -> None:
    label, color, icon = ui.badge(result["status"], lang)
    st.badge(label, color=color, icon=icon)
    st.markdown(ui.answer_markdown(result["answer"]))

    citations = result.get("citations") or []
    if citations:
        st.markdown(f"**{ui.text(lang, 'citations')}**")
        st.markdown("\n".join(f"- {ui.citation_markdown(c, lang)}" for c in citations))

    data = result.get("data")
    if data is not None and "error" in data:
        st.warning(ui.text(lang, "data_error", error=data["error"]))
    elif data is not None:
        st.markdown(f"**{ui.text(lang, 'data')}**")
        if rows := ui.data_rows(data):
            decimals = st.column_config.NumberColumn(format=ui.DECIMAL_FORMAT)
            st.dataframe(
                rows,
                hide_index=True,
                width="stretch",
                column_config={column: decimals for column in ui.decimal_columns(data)},
            )
        else:
            st.caption(ui.text(lang, "no_rows"))

    route = result.get("route") or {}
    with st.expander(ui.text(lang, "details")):
        st.markdown(f"**{ui.text(lang, 'regulation_query')}**")
        st.text(route.get("regulation_query") or ui.text(lang, "none"))
        st.markdown(f"**{ui.text(lang, 'sql')}**")
        if result.get("sql"):
            st.code(result["sql"], language="sql")
        else:
            st.text(ui.text(lang, "none"))
    st.caption(
        f"{ui.text(lang, 'route')}: {ui.route_text(route, lang)} · "
        f"{ui.text(lang, 'latency')}: {ui.latency_text(result.get('latency_ms'), lang)}"
    )


def render_entry(entry: dict[str, Any], lang: str) -> None:
    with st.chat_message("user"):
        st.markdown(ui.escape_markdown(entry["question"]))
    with st.chat_message("assistant"):
        if "result" in entry:
            render_result(entry["result"], lang)
        else:
            st.error(ui.text(lang, "failed", error=entry["error"]))


def ask(question: str, lang: str) -> dict[str, Any]:
    with st.spinner(ui.text(lang, "thinking")):
        try:
            return {"question": question, "result": agent.run(question, deps=agent_deps())}
        except Exception as error:  # Auth, network, endpoint down: keep the page alive.
            return {"question": question, "error": f"{type(error).__name__}: {error}"}


def show_warmup(state: Warmup, lang: str) -> None:
    """The warm-up note, polled by a fragment until the thread is done, then a full rerun."""
    if state.done.is_set():
        if state.error:
            st.warning(ui.text(lang, "warm_failed", error=state.error))
        return

    @st.fragment(run_every=WARMUP_POLL_SECONDS)
    def poll() -> None:
        if state.done.is_set():
            st.rerun()
        st.info(ui.text(lang, "warming"), icon=":material/hourglass_top:")
        st.caption(ui.text(lang, "warm_hint"))

    poll()


# ---------------------------------------------------------------- page

st.set_page_config(page_title="EU Compliance Copilot", page_icon=":material/gavel:")
st.session_state.setdefault("history", [])
st.session_state.setdefault("ui_lang", ui.DEFAULT_UI_LANG)

title_column, lang_column = st.columns([4, 1], vertical_alignment="bottom")
with lang_column:
    st.segmented_control(
        "Language / Sprache",
        options=list(ui.UI_LANGS),
        format_func=str.upper,
        key="ui_lang",
        label_visibility="collapsed",
    )
lang = ui.ui_lang(st.session_state.get("ui_lang"))  # None when the switch is clicked off.
with title_column:
    st.title(ui.text(lang, "title"))
st.caption(ui.text(lang, "subtitle"))

show_warmup(warmup(), lang)

st.markdown(f"**{ui.text(lang, 'examples')}**")
example_columns = st.columns(2)
for i, example in enumerate(ui.EXAMPLES[lang]):
    if example_columns[i % 2].button(example, key=f"example_{lang}_{i}", width="stretch"):
        st.session_state["pending"] = example

for entry in st.session_state["history"]:
    render_entry(entry, lang)

question = st.chat_input(ui.text(lang, "placeholder")) or st.session_state.pop("pending", None)
if question:
    with st.chat_message("user"):
        st.markdown(ui.escape_markdown(question))
    with st.chat_message("assistant"):
        entry = ask(question, lang)
        if "result" in entry:
            render_result(entry["result"], lang)
        else:
            st.error(ui.text(lang, "failed", error=entry["error"]))
    st.session_state["history"].append(entry)

# Footer of the main page (not the sidebar, which can be collapsed): on screen on every rerun.
st.divider()
st.caption(f":material/gavel: {ui.text(lang, 'disclaimer')}")

with st.sidebar:
    st.caption(
        ui.text(
            lang,
            "about",
            llm=CONFIG.llm_endpoint,
            embedding=CONFIG.embedding_endpoint,
            index=CONFIG.index,
        )
    )
