"""Process-flow tab and the step workspace under the chart."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from data import ProcessData, _clean_str, build_line_graph, build_sipoc_graph
from tabs._common import download_graphviz_png, encased_field, text
from tabs.lost_time import render_step_lost_time

PROCESS_SHEET_COLS = [
    "ControlID",
    "M Category",
    "Variable",
    "Type",
    "Standard",
    "Rationale",
    "Owner",
]


def render_flow_tab(model: ProcessData) -> None:
    lines = model.lines()
    if not lines:
        st.info("Process Map has no lines.")
        return

    if "selected_lines" not in st.session_state:
        st.session_state.selected_lines = list(lines)
    else:
        valid_lines = [line for line in st.session_state.selected_lines if line in lines]
        if valid_lines != st.session_state.selected_lines:
            st.session_state.selected_lines = valid_lines

    selected_lines = st.multiselect(
        "Lines to inspect",
        options=lines,
        key="selected_lines",
    )
    st.radio(
        "Chart layout",
        options=["Horizontal", "Vertical"],
        horizontal=True,
        key="flow_orientation",
    )
    steps = model.steps_for_lines(selected_lines)
    if steps.empty:
        st.info("Select at least one line.")
        return

    step_ids = steps["StepID"].tolist()
    if st.session_state.get("selected_step") not in step_ids:
        st.session_state.selected_step = step_ids[0]

    labels = {
        row["StepID"]: f"{row['StepID']}  ·  {row['Process']}  ·  {row['Line']}"
        for _, row in steps.iterrows()
    }
    st.selectbox(
        "Step",
        options=step_ids,
        format_func=lambda sid: labels.get(sid, sid),
        key="selected_step",
    )

    rankdir = "TB" if st.session_state.get("flow_orientation") == "Vertical" else "LR"
    line_chart = build_line_graph(
        steps,
        st.session_state.selected_step,
        all_steps=model.process_map,
        rankdir=rankdir,
    )
    st.graphviz_chart(line_chart, width="content")
    download_graphviz_png(line_chart, "process-flow.png", "Download this chart")

    step = model.step_row(st.session_state.selected_step)
    if step is None:
        st.warning("Selected StepID is not on the Process Map.")
        return

    st.divider()
    _render_step_workspace(model, step)


def _render_step_workspace(model: ProcessData, step: pd.Series) -> None:
    st.subheader(step["Process"])
    st.caption(f"{step['StepID']}  ·  {step['Line']}  ·  {step['Zone']}")
    st.write(text(step.get("Definition")))

    sipoc = model.sipoc_for_step(step["StepID"])
    st.markdown("**Flow in and out**")
    if sipoc.empty:
        st.info("No SIPOC documented for this step yet.")
    else:
        st.graphviz_chart(build_sipoc_graph(step, sipoc), width="stretch")
        inputs = sipoc[sipoc["Flow type"] == "Input"]
        outputs = sipoc[sipoc["Flow type"] == "Output"]
        left, center, right = st.columns(3)
        with left:
            st.caption("Inputs")
            if inputs.empty:
                st.write("None documented.")
            for _, row in inputs.iterrows():
                _sipoc_card(row)
        with center:
            st.caption("Process")
            with st.container(border=True):
                st.markdown(f"**{step['StepID']}**")
                st.write(step["Process"])
                st.caption(step["Zone"])
        with right:
            st.caption("Outputs")
            if outputs.empty:
                st.write("None documented.")
            for _, row in outputs.iterrows():
                _sipoc_card(row)

    st.markdown("**Process sheet**")
    controls = model.controls_for_step(step["StepID"])
    if controls.empty:
        st.info("No process-sheet controls documented for this step yet.")
        st.session_state.pop("selected_control_id", None)
    else:
        display_cols = [c for c in PROCESS_SHEET_COLS if c in controls.columns]
        visible = controls[display_cols].copy()
        for col in visible.columns:
            if visible[col].isna().all():
                visible = visible.drop(columns=[col])
            else:
                visible[col] = visible[col].fillna("")
        event = st.dataframe(
            visible,
            hide_index=True,
            width="stretch",
            on_select="rerun",
            selection_mode="single-row",
            key=f"process_sheet_table_{step['StepID']}",
        )
        rows = event.selection.rows if event and event.selection else []
        if rows:
            chosen = controls.iloc[rows[0]]
            control_id = _clean_str(chosen.get("ControlID"))
            st.session_state.selected_control_id = control_id
            st.caption(f"Selected control: {text(control_id)} · {text(chosen.get('Variable'))}")
            _render_control_detail(model, control_id)
        else:
            st.caption("Select a control row to open related PFMEA / PM.")

    symptoms = model.symptoms(step["StepID"])
    wis = model.work_instructions
    step_wis = wis[wis["_step_tokens"].map(lambda tokens: step["StepID"] in tokens)] if not wis.empty else wis

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Troubleshooting**")
        if symptoms.empty:
            st.caption("None documented for this step.")
        else:
            for i, (_, row) in enumerate(symptoms.iterrows(), start=1):
                title = _clean_str(row.get("Type")) or _clean_str(row.get("ID"))
                st.write(f"{i}. {title}")
    with c2:
        st.markdown("**SOPs**")
        if step_wis.empty:
            st.caption("None documented for this step.")
        else:
            for i, (_, row) in enumerate(step_wis.iterrows(), start=1):
                title = _clean_str(row.get("Title")) or _clean_str(row.get("WI_ID"))
                st.write(f"{i}. {title}")

    render_step_lost_time(model, step)


def _sipoc_card(row: pd.Series) -> None:
    with st.container(border=True):
        title = text(row.get("Item"))
        if row.get("CTQ") == "Yes":
            st.markdown(f"**{title}** · CTQ")
        else:
            st.markdown(f"**{title}**")
        role = _clean_str(row.get("Party role"))
        party = _clean_str(row.get("Party"))
        if role or party:
            st.caption(" · ".join([p for p in [role, party] if p]))
        req = _clean_str(row.get("Requirement"))
        if req:
            st.write(req)


def _render_control_detail(model: ProcessData, control_id: str | None) -> None:
    fmea = model.pfmea_for_control(control_id)
    pms = model.pm_for_control(control_id)

    if fmea.empty and pms.empty:
        st.info("No PFMEA or PM rows for this control.")
        return

    if not fmea.empty:
        st.markdown("**PFMEA for this control**")
        for _, row in fmea.iterrows():
            with st.container(border=True):
                st.markdown(f"**{text(row.get('PFMEAID'))}** · {text(row.get('Asset'))}")
                encased_field("Function", row.get("Function"))
                encased_field("Requirement", row.get("Requirement"))
                encased_field("Failure mode", row.get("Failure mode"))
                encased_field("Cause", row.get("Cause"))
                encased_field("Prevention", row.get("Prev. control"))
                encased_field("Detection", row.get("Det. control"))
                encased_field("Frequency", row.get("Frequency"))

    if not pms.empty:
        st.markdown("**Maintenance PM for this control**")
        for _, row in pms.iterrows():
            with st.container(border=True):
                st.markdown(f"**{text(row.get('PMID'))}** · {text(row.get('Asset'))}")
                st.write(text(row.get("Task")))
                extra = [p for p in [
                    f"Freq: {row['Freq']}" if _clean_str(row.get("Freq")) else None,
                    f"If PM fails: {row['If the PM fails']}" if _clean_str(row.get("If the PM fails")) else None,
                ] if p]
                if extra:
                    st.caption(" · ".join(extra))
