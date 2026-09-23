"""Main surface. Sidebar loads the workbook; tabs render the views."""

from __future__ import annotations

import streamlit as st

from data import load_workbook
from tabs import (
    render_build_tab,
    render_documents_tab,
    render_flow_tab,
    render_lost_time_tab,
    render_troubleshoot_tab,
)

st.set_page_config(
    page_title="Process viewer",
    page_icon="▣",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _reset_session_for_new_file() -> None:
    for key in [
        "selected_lines",
        "selected_step",
        "selected_control_id",
        "process_sheet_table",
        "flow_orientation",
        "ts_symptom",
        "ts_node",
        "ts_path",
        "ts_path_root",
        "lt_analysis_lines",
        "lt_analysis_window",
        "lt_pareto_group",
        "lt_step_window",
        "lt_step_table_source",
        "lt_step_table_for",
        "working_sheets",
        "raw_sheets",
        "workbook_bytes",
        "build_dirty",
        "build_lines",
        "build_step",
        "build_orientation",
    ]:
        st.session_state.pop(key, None)


def _sidebar_loader() -> None:
    st.sidebar.header("Workbook")
    uploaded = st.sidebar.file_uploader(
        "Process workbook (.xlsx)",
        type=["xlsx"],
        accept_multiple_files=False,
    )
    pdfs = st.sidebar.file_uploader(
        "Optional SOP PDFs",
        type=["pdf"],
        accept_multiple_files=True,
    )
    st.session_state.pdf_files = {item.name: item.getvalue() for item in (pdfs or [])}

    if uploaded is None:
        st.session_state.pop("model", None)
        st.session_state.pop("source_name", None)
        st.session_state.pop("file_token", None)
        return

    file_token = f"{uploaded.name}:{getattr(uploaded, 'size', '')}:{getattr(uploaded, 'file_id', '')}"
    same_file = st.session_state.get("file_token") == file_token and "model" in st.session_state
    if not same_file:
        try:
            model = load_workbook(uploaded.getvalue(), source_name=uploaded.name)
        except Exception as exc:
            st.sidebar.error(f"Could not read workbook: {exc}")
            st.session_state.pop("model", None)
            return
        _reset_session_for_new_file()
        st.session_state.model = model
        st.session_state.source_name = uploaded.name
        st.session_state.file_token = file_token
        st.session_state.workbook_bytes = uploaded.getvalue()
        st.session_state.raw_sheets = model.raw_sheets
        st.session_state.working_sheets = None
        st.session_state.build_dirty = False

    model = st.session_state.model
    st.sidebar.success(f"Loaded {uploaded.name}")
    st.sidebar.caption(
        f"{len(model.process_map)} steps · {len(model.lines())} lines · "
        f"{len(model.troubleshooting)} tree nodes · {len(model.work_instructions)} SOPs"
    )


def main() -> None:
    _sidebar_loader()
    st.title("Process viewer")
    st.caption("Upload the process workbook, pick a line, then walk a step.")

    model = st.session_state.get("model")
    if model is None:
        st.info("Upload a process workbook in the sidebar to start. Nothing is stored on the server after the session ends.")
        return

    flow_tab, build_tab, lost_tab, ts_tab, docs_tab = st.tabs(
        ["Process flow", "Build", "Lost time", "Troubleshoot", "Documents"]
    )
    with flow_tab:
        render_flow_tab(model)
    with build_tab:
        render_build_tab(model)
    with lost_tab:
        render_lost_time_tab(model)
    with ts_tab:
        render_troubleshoot_tab(model)
    with docs_tab:
        render_documents_tab(model)


if __name__ == "__main__":
    main()
