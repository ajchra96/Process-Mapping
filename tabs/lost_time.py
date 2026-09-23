"""Lost-time analysis tab and the independent step-history subsection."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from data import (
    ProcessData,
    _clean_str,
    build_line_graph,
    hours_fillcolors,
    pareto_hours,
    weekly_hours,
)


TABLE_COLS = [
    "Date",
    "StepID",
    "PFMEAID",
    "Issue",
    "Hours",
    "Corrective Action",
    "Product",
]


def _text(value) -> str:
    cleaned = _clean_str(value)
    return cleaned if cleaned else "—"


def _date_window(events: pd.DataFrame, key: str, label: str) -> tuple | None:
    if events.empty:
        return None
    min_d = events["Date"].min().date()
    max_d = events["Date"].max().date()
    if min_d == max_d:
        st.caption(f"{label}: {min_d}")
        return min_d, max_d
    return st.slider(label, min_value=min_d, max_value=max_d, value=(min_d, max_d), key=key)


def _event_table(events: pd.DataFrame, caption: str) -> None:
    st.caption(caption)
    if events.empty:
        st.info("No lost-time events in this slice.")
        return
    visible = events.copy()
    keep = [c for c in TABLE_COLS if c in visible.columns]
    visible = visible[keep]
    visible["Date"] = visible["Date"].dt.strftime("%Y-%m-%d")
    visible["Hours"] = visible["Hours"].map(lambda v: round(float(v), 2))
    for col in visible.columns:
        if visible[col].dtype == object:
            visible[col] = visible[col].fillna("")
    st.dataframe(visible, hide_index=True, width="stretch")


def _line_chart(frame: pd.DataFrame, empty_message: str) -> None:
    if frame.empty or frame.select_dtypes("number").sum().sum() <= 0:
        st.caption(empty_message)
        return
    st.line_chart(frame, width="stretch")


def render_lost_time_tab(model: ProcessData) -> None:
    if model.lost_time.empty:
        st.info("No lost-time events with hours in this workbook.")
        return

    lines = model.lines()
    if "lt_analysis_lines" not in st.session_state:
        st.session_state.lt_analysis_lines = list(lines)
    else:
        valid = [line for line in st.session_state.lt_analysis_lines if line in lines]
        if valid != st.session_state.lt_analysis_lines:
            st.session_state.lt_analysis_lines = valid

    selected_lines = st.multiselect("Lines", options=lines, key="lt_analysis_lines")
    window = _date_window(model.lost_time, "lt_analysis_window", "Line window")
    if window is None:
        return
    start, end = window

    in_window = model.lost_time_window(start, end)
    mapped = model.lost_time_for_lines(in_window, selected_lines)
    unmapped = model.lost_time_unmapped(in_window)

    if not selected_lines:
        st.info("Select at least one line.")
        return

    steps = model.steps_for_lines(selected_lines)
    hours_by_step: dict[str, float] = {}
    if not mapped.empty:
        if "_step_tokens" in mapped.columns:
            for tokens, hours in zip(mapped["_step_tokens"], mapped["Hours"]):
                for sid in tokens or []:
                    hours_by_step[sid] = hours_by_step.get(sid, 0.0) + float(hours)
        else:
            hours_by_step = mapped.groupby("StepID")["Hours"].sum().to_dict()
    fills = hours_fillcolors(steps["StepID"].tolist(), hours_by_step)
    total_hours = float(mapped["Hours"].sum()) if not mapped.empty else 0.0

    st.caption(
        "Box fill is that step’s share of mapped lost hours in the window "
        f"(light → dark). {total_hours:.1f} h mapped to these lines."
    )
    chart = build_line_graph(
        steps,
        selected_step=None,
        all_steps=model.process_map,
        rankdir="LR",
        step_fillcolors=fills,
        highlight_selected=False,
    )
    st.graphviz_chart(chart, width="content")

    if not unmapped.empty:
        st.caption(
            f"{len(unmapped)} events / {unmapped['Hours'].sum():.1f} h have no StepID "
            "on the Process Map and are not shown on the chart."
        )

    st.markdown("**Pareto**")
    grouping = st.radio(
        "Group by",
        options=["Zone", "Step", "Failure mode", "Sub-process", "Product"],
        horizontal=True,
        key="lt_pareto_group",
    )
    if grouping == "Zone":
        pareto = pareto_hours(mapped, "Zone")
    elif grouping == "Step":
        pareto = pareto_hours(mapped, "StepID", "Process")
    elif grouping == "Failure mode":
        tagged = mapped[mapped["PFMEAID"].notna()] if not mapped.empty else mapped
        pareto = pareto_hours(tagged, "PFMEAID", "Failure mode")
    elif grouping == "Sub-process":
        pareto = pareto_hours(mapped, "Sub-process")
    else:
        pareto = pareto_hours(mapped, "Product")

    if grouping == "Failure mode" and (mapped.empty or mapped["PFMEAID"].isna().all()):
        st.caption("No events tagged to a failure mode yet.")
    elif pareto.empty:
        st.caption("No mapped lost hours in this window.")
    else:
        st.bar_chart(pareto.set_index("Label")["Hours"], width="stretch")
        st.caption(f"{len(pareto)} groups · {pareto['Hours'].sum():.1f} h")


def render_step_lost_time(model: ProcessData, step: pd.Series) -> None:
    st.markdown("**Lost time**")
    if model.lost_time.empty:
        st.info("No lost-time events with hours in this workbook.")
        return

    step_id = step["StepID"]
    if st.session_state.get("lt_step_table_for") != step_id:
        st.session_state.lt_step_table_for = step_id
        st.session_state.lt_step_table_source = "step"

    window = _date_window(model.lost_time, "lt_step_window", "This step window")
    if window is None:
        return
    start, end = window
    in_window = model.lost_time_window(start, end)
    step_events = model.lost_time_for_step(in_window, step_id)

    st.caption("Hours by week for this step")
    _line_chart(
        weekly_hours(step_events),
        "No lost-time events for this step in the window.",
    )

    controls = model.controls_for_step(step_id)
    control_ids = [cid for cid in controls["ControlID"].tolist() if cid]
    control_labels = {
        row["ControlID"]: f"{row['ControlID']}  ·  {_text(row.get('Variable'))}"
        for _, row in controls.iterrows()
        if _clean_str(row.get("ControlID"))
    }

    def _mark_control() -> None:
        st.session_state.lt_step_table_source = "control"

    def _mark_fmea() -> None:
        st.session_state.lt_step_table_source = "fmea"

    st.caption("Hours by process-sheet control")
    if not control_ids:
        st.caption("No process-sheet controls documented for this step yet.")
        selected_controls: list[str] = []
    else:
        if f"lt_step_controls_{step_id}" not in st.session_state:
            st.session_state[f"lt_step_controls_{step_id}"] = list(control_ids)
        selected_controls = st.multiselect(
            "Process sheet entries",
            options=control_ids,
            format_func=lambda cid: control_labels.get(cid, cid),
            key=f"lt_step_controls_{step_id}",
            on_change=_mark_control,
        )
        selected_controls = [cid for cid in selected_controls if cid in control_ids]
        control_events = step_events[step_events["ControlID"].isin(selected_controls)]
        if not selected_controls:
            st.caption("Select at least one control.")
        elif control_events.empty:
            st.caption("No events tagged to a failure mode for these controls yet.")
        else:
            series = weekly_hours(control_events, "ControlID")
            rename = {cid: control_labels.get(cid, cid) for cid in series.columns}
            _line_chart(series.rename(columns=rename), "No events tagged to a failure mode for these controls yet.")

    fmeas = model.fmeas_for_step(step_id)
    fmea_ids = [fid for fid in fmeas["PFMEAID"].tolist() if fid] if "PFMEAID" in fmeas.columns else []
    fmea_labels = {
        row["PFMEAID"]: f"{row['PFMEAID']}  ·  {_text(row.get('Failure mode'))}"
        for _, row in fmeas.iterrows()
        if _clean_str(row.get("PFMEAID"))
    }

    st.caption("Hours by PFMEA entry")
    if not fmea_ids:
        st.caption("No PFMEA rows documented for this step’s controls yet.")
        selected_fmeas: list[str] = []
    else:
        if f"lt_step_fmeas_{step_id}" not in st.session_state:
            st.session_state[f"lt_step_fmeas_{step_id}"] = list(fmea_ids)
        selected_fmeas = st.multiselect(
            "PFMEA entries",
            options=fmea_ids,
            format_func=lambda fid: fmea_labels.get(fid, fid),
            key=f"lt_step_fmeas_{step_id}",
            on_change=_mark_fmea,
        )
        selected_fmeas = [fid for fid in selected_fmeas if fid in fmea_ids]
        fmea_events = step_events[step_events["PFMEAID"].isin(selected_fmeas)]
        if not selected_fmeas:
            st.caption("Select at least one failure mode.")
        elif fmea_events.empty:
            st.caption("No events tagged to a failure mode yet.")
        else:
            series = weekly_hours(fmea_events, "PFMEAID")
            rename = {fid: fmea_labels.get(fid, fid) for fid in series.columns}
            _line_chart(series.rename(columns=rename), "No events tagged to a failure mode yet.")

    source = st.session_state.get("lt_step_table_source", "step")
    if source == "control" and control_ids:
        rows = step_events[step_events["ControlID"].isin(selected_controls)]
        caption = "Rows for selected controls"
    elif source == "fmea" and fmea_ids:
        rows = step_events[step_events["PFMEAID"].isin(selected_fmeas)]
        caption = "Rows for selected failure modes"
    else:
        rows = step_events
        caption = "Rows for this step"
    _event_table(rows, caption)
