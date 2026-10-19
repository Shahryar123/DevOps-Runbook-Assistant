"""Streamlit chat UI for the runbook assistant.

Run: uv run streamlit run src/runbook_assistant/app.py

UI only: answering, trace links, feedback, index checks, secret masking
and rate limits live in service.py / guardrails/; this file decides what to
show and where.

How Streamlit runs this file:
- Rerun model: the whole script runs top to bottom on every interaction
  (sending a message, clicking a button, choosing a setup). There are no
  callbacks to wire up; the page is rebuilt from the current state each time.
- st.session_state: a dict that survives reruns for one browser tab. The chat
  history lives there, otherwise it would be lost on the next rerun.
- st.cache_resource: runs a function once per server process and shares the
  result across reruns and users (models, vector store, compiled graph).
- st.cache_data: like cache_resource but for plain data, with an expiry (ttl).
- Chat elements: st.chat_message draws one bubble; st.chat_input is the
  message box pinned to the bottom, and returns the text once on submit.
"""

import logging
from typing import Any
from uuid import uuid4

import streamlit as st
from langchain_core.documents import Document

from runbook_assistant import service
from runbook_assistant.config import (
    ConfigError,
    Settings,
    configure_tracing,
    get_settings,
)
from runbook_assistant.guardrails.input import too_long
from runbook_assistant.guardrails.rate_limit import client_ip
from runbook_assistant.rag import NO_ANSWER
from runbook_assistant.retriever import RunbookInfo
from runbook_assistant.service import AnswerResult, IndexStatus, SharedLimits

logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("runbook_assistant.app")

INGEST_COMMAND = "uv run python -m runbook_assistant.ingest"
SETUP_LABELS = {
    "chain": "Chain: one search, no checks",
    "graph": "Graph: grading + retry, no router",
    "graph_routing": "Graph + router: the full assistant",
}

Message = dict[str, Any]


# --- Cached resources ------------------------------------------------------------


@st.cache_resource(show_spinner="Loading the assistant...")
def load_setup(setup: str) -> object:
    """Build the chain or graph once per setup, shared by every rerun and user."""
    return service.load_setup(setup)


@st.cache_resource
def shared_limits() -> SharedLimits:
    """One per-IP limiter and one daily cap for the whole app process: every
    session and user shares them (cache_resource returns the same object)."""
    return service.new_shared_limits(get_settings())


@st.cache_data(ttl=60, show_spinner=False)
def load_runbook_index() -> tuple[IndexStatus, list[RunbookInfo]]:
    """Re-checked at most once a minute, so a fresh ingest shows up without
    restarting the app."""
    return service.runbook_index(get_settings())


# --- Rendering ---------------------------------------------------------------------


def render_sidebar(settings: Settings, runbooks: list[RunbookInfo]) -> str:
    """Setup selector, Clear chat, About. Returns the chosen setup."""
    with st.sidebar:
        setup = st.radio(
            "Setup",
            service.SETUPS,
            index=service.SETUPS.index(settings.ui_default_setup),
            format_func=SETUP_LABELS.get,
            help="Which version of the assistant answers. Same as the eval setups.",
        )
        if st.button("Clear chat", width="stretch"):
            st.session_state.messages = []
            st.rerun()  # start a fresh run so the cleared history is drawn

        st.divider()
        st.subheader("About")
        st.markdown(
            "Answers DevOps troubleshooting questions **only** from the team's "
            f'runbooks. If no runbook covers it, it says "{NO_ANSWER}" '
            "instead of guessing."
        )
        st.markdown(f"**Runbooks available ({len(runbooks)})**")
        st.markdown(
            "\n".join(
                f"- {rb.title}  \n  `{rb.source}` · {rb.topic}" for rb in runbooks
            )
        )
    return setup


def render_answer(result: AnswerResult) -> None:
    blocked_by = [
        e["guardrail"] for e in result["guardrail_events"] if e["action"] == "blocked"
    ]
    if blocked_by:
        st.warning(result["answer"], icon=":material/block:")
        st.caption(
            f"Stopped by the {blocked_by[0]} guardrail: nothing was searched "
            "or generated."
        )
    elif result["is_refusal"]:
        st.warning(result["answer"], icon=":material/search_off:")
        st.caption("None of the runbooks cover this question, so nothing was made up.")
    else:
        st.markdown(result["answer_body"])

    if result["command_warnings"]:
        st.warning(
            "**Risky commands in this answer.** Check the impact before running:\n"
            + "\n".join(
                f"- **{w.name}** ({w.severity}): {w.warning}"
                for w in result["command_warnings"]
            ),
            icon=":material/warning:",
        )


def render_chunk(doc: Document) -> None:
    meta = doc.metadata
    st.markdown(
        f"**{meta.get('title', meta['source'])}** · `{meta['source']}` · "
        f"{meta.get('section', '')}"
    )
    st.code(doc.page_content, language="markdown", wrap_lines=True)


def render_sources(result: AnswerResult) -> None:
    if not result["kept"]:
        return
    label = "Retrieved chunks" if result["is_refusal"] else "Sources"
    with st.expander(f"{label} ({len(result['kept'])})", icon=":material/menu_book:"):
        for doc in result["kept"]:
            render_chunk(doc)


def render_behind_the_scenes(result: AnswerResult) -> None:
    with st.expander("Behind the scenes", icon=":material/account_tree:"):
        st.markdown(
            f"- **Setup:** {SETUP_LABELS[result['setup']]}\n"
            f"- **Routed topic:** {result['topic']}\n"
            f"- **Retries:** {result['retry_count']}\n"
            f"- **Chunks:** {len(result['kept'])} kept, "
            f"{len(result['filtered_out'])} filtered out\n"
            f"- **Latency:** {result['latency_s']:.1f} s"
        )
        st.markdown("**Search queries**")
        for number, query in enumerate(result["search_queries"]):
            kind = "original" if number == 0 else "rewritten"
            st.markdown(f"{number + 1}. *({kind})* {query}")
        if result["filtered_out"]:
            st.markdown("**Filtered out by the grader**")
            st.markdown(
                "\n".join(
                    f"- `{doc.metadata['source']}` · {doc.metadata.get('section', '')}"
                    for doc in result["filtered_out"]
                )
            )
        if result["guardrail_events"]:
            st.markdown("**Guardrails triggered**")
            st.markdown(
                "\n".join(
                    f"- **{e['guardrail']}** {e['action']}"
                    + (f": {e['detail']}" if e["detail"] else "")
                    for e in result["guardrail_events"]
                )
            )
        else:
            st.markdown("**Guardrails triggered:** none")
        if result["trace_url"]:
            st.link_button(
                "Open trace in LangSmith",
                result["trace_url"],
                icon=":material/open_in_new:",
            )
        else:
            st.caption(f"Run ID: `{result['run_id']}`")


def render_feedback(message: Message, tracing_on: bool) -> None:
    """Thumbs + optional comment, sent once to LangSmith for this answer's run.

    Widget keys include the message's own id, so each answer has its own
    widgets and their values survive reruns.
    """
    if not tracing_on:
        st.caption("Turn on LangSmith tracing (LANGSMITH_TRACING) to rate answers.")
        return

    msg_id = message["id"]
    if message["feedback_sent"]:
        st.feedback("thumbs", key=f"rating_{msg_id}", disabled=True)
        st.caption(":material/check_circle: Thanks, your feedback was sent.")
        return

    rating = st.feedback("thumbs", key=f"rating_{msg_id}")
    if rating is None:
        return
    # A form batches its widgets: typing the comment doesn't trigger a rerun,
    # only the submit button does.
    with st.form(key=f"feedback_form_{msg_id}", border=False):
        comment = st.text_input("Comment (optional)", key=f"comment_{msg_id}")
        if st.form_submit_button("Send feedback"):
            try:
                service.send_feedback(message["result"]["run_id"], rating, comment)
            except Exception:
                logger.exception("Sending feedback failed")
                st.error("Couldn't send feedback to LangSmith. Please try again.")
            else:
                message["feedback_sent"] = True  # blocks a second submit
                st.rerun()


def render_message(message: Message, tracing_on: bool) -> None:
    with st.chat_message(message["role"]):
        if message["role"] == "user":
            st.markdown(message["content"])
            if message.get("masked"):
                st.caption(
                    f":material/lock: Masked before sending: {message['masked']}. "
                    "Never paste real secrets into a chat."
                )
        elif message.get("error"):
            st.error(message["error"], icon=":material/error:")
        else:
            result: AnswerResult = message["result"]
            render_answer(result)
            render_sources(result)
            render_behind_the_scenes(result)
            render_feedback(message, tracing_on)


# --- Handling a new question ----------------------------------------------------


def assistant_message(**fields: Any) -> Message:
    return {"role": "assistant", "id": uuid4().hex, "feedback_sent": False, **fields}


def rate_limit_message(settings: Settings) -> str | None:
    """Session limit (state in st.session_state, so it ends with the session),
    plus the per-IP and daily limits shared by all sessions."""
    if "rate_limiter" not in st.session_state:
        st.session_state.rate_limiter = service.new_session_limiter(settings)
    ip = client_ip(
        st.context.ip_address,
        st.context.headers,
        settings.rate_limit_trust_forwarded_for,
    )
    return service.check_rate_limits(
        st.session_state.rate_limiter, shared_limits(), ip, settings
    )


def answer_question(question: str, setup: str, settings: Settings) -> Message:
    """Validate, rate-limit, answer, and turn any failure into a friendly message.

    The length check comes first so an over-long question isn't counted
    against the rate limits.
    """
    if too_long(question, settings):
        return assistant_message(
            error=(
                f"That question is {len(question)} characters long. Please keep it "
                f"under {settings.max_question_chars}: describe the main symptom "
                "and the system involved."
            )
        )
    if limited := rate_limit_message(settings):
        return assistant_message(error=limited)
    try:
        with st.spinner("Searching the runbooks..."):
            return assistant_message(result=service.answer(question, setup))
    except Exception as exc:
        logger.exception("Answering failed (setup=%s)", setup)
        return assistant_message(error=service.friendly_error(exc))


# --- Page ----------------------------------------------------------------------------


def stop_with_index_help(status: IndexStatus) -> None:
    problem = (
        "No search database was found."
        if status == "missing"
        else "The search database is empty."
    )
    st.error(f"{problem} Load the runbooks first, then refresh this page:")
    st.code(INGEST_COMMAND, language="powershell")
    st.stop()


def main() -> None:
    st.set_page_config(
        page_title=Settings.model_fields["ui_title"].default,
        page_icon=":material/build:",
    )
    try:
        settings = get_settings()
    except ConfigError as exc:
        # One message listing every missing/invalid setting (see config.py).
        st.error("The app is not configured correctly.")
        st.code(str(exc), language="text")
        st.stop()
    configure_tracing(settings)

    st.title(settings.ui_title)
    st.caption(
        "Each question is answered on its own: the assistant doesn't remember "
        "earlier messages yet."
    )

    status, runbooks = load_runbook_index()
    if status != "ok":
        stop_with_index_help(status)

    setup = render_sidebar(settings, runbooks)
    load_setup(setup)
    tracing_on = service.tracing_enabled(settings)

    # Survives reruns; one history per browser tab.
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # Every rerun redraws the whole conversation from session_state.
    for message in st.session_state.messages:
        render_message(message, tracing_on)

    question = st.chat_input("Describe the problem, e.g. IIS app pool keeps stopping")
    if question and question.strip():
        question = question.strip()
        # Only the masked text is shown and stored; the raw question goes
        # straight to service.answer, which masks it again before invoke.
        shown, masked = service.mask_for_display(question)
        user = {"role": "user", "content": shown, "masked": masked}
        st.session_state.messages.append(user)
        render_message(user, tracing_on)

        with st.chat_message("assistant"):
            reply = answer_question(question, setup, settings)
        st.session_state.messages.append(reply)
        st.rerun()  # redraw from history, so the new answer gets its widgets


if __name__ == "__main__":
    main()
