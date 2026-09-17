"""One render function per tab. All data access goes through ProcessData."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from data import (
    ProcessData,
    _clean_str,
    build_line_graph,
    build_sipoc_graph,
    build_tree_graph,
    export_graph_image,
    export_line_image,
    export_tree_image,
)


def _text(value) -> str:
    cleaned = _clean_str(value)
    return cleaned if cleaned else "—"


def _download_chart(chart, filename: str, key: str, label: str = "Download chart image", fallback=None) -> None:
    png = export_graph_image(chart, "png") or fallback
    if not png:
        st.caption("Could not build a download image.")
        return
    st.download_button(
        label,
        data=png,
        file_name=filename,
        mime="image/png",
        key=key,
    )


def _encased_field(label: str, value) -> None:
    text = _clean_str(value)
    if not text:
        return
    with st.container(border=True):
        st.caption(label)
        st.write(text)


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
    _download_chart(
        line_chart,
        "process-flow.png",
        "download_line_png",
        "Download this chart",
        fallback=export_line_image(
            steps,
            st.session_state.selected_step,
            all_steps=model.process_map,
            rankdir=rankdir,
        ),
    )

    step = model.step_row(st.session_state.selected_step)
    if step is None:
        st.warning("Selected StepID is not on the Process Map.")
        return

    st.divider()
    _render_step_workspace(model, step)


def _render_step_workspace(model: ProcessData, step: pd.Series) -> None:
    st.subheader(step["Process"])
    st.caption(f"{step['StepID']}  ·  {step['Line']}  ·  {step['Zone']}")
    st.write(_text(step.get("Definition")))

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
        display_cols = [c for c in ["ControlID", "M", "Control", "Type", "Standard", "How set", "Owner"] if c in controls.columns]
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
            st.caption(f"Selected control: {_text(control_id)} · {_text(chosen.get('Control'))}")
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


def _sipoc_card(row: pd.Series) -> None:
    with st.container(border=True):
        title = _text(row.get("Item"))
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
                st.markdown(f"**{_text(row.get('FmeaID'))}** · {_text(row.get('Component'))}")
                _encased_field("Function", row.get("Function"))
                _encased_field("Requirement", row.get("Requirement"))
                _encased_field("Failure mode", row.get("Failure mode"))
                _encased_field("Cause", row.get("Cause"))
                _encased_field("Prevention", row.get("Prev. control"))
                _encased_field("Detection", row.get("Det. control"))
                _encased_field("Frequency", row.get("Frequency"))

    if not pms.empty:
        st.markdown("**Maintenance PM for this control**")
        for _, row in pms.iterrows():
            with st.container(border=True):
                st.markdown(f"**{_text(row.get('PMID'))}** · {_text(row.get('Asset'))}")
                st.write(_text(row.get("Task")))
                extra = [p for p in [
                    f"Freq: {row['Freq']}" if _clean_str(row.get("Freq")) else None,
                    f"If PM fails: {row['If the PM fails']}" if _clean_str(row.get("If the PM fails")) else None,
                ] if p]
                if extra:
                    st.caption(" · ".join(extra))


def render_troubleshoot_tab(model: ProcessData) -> None:
    symptoms = model.symptoms()
    if symptoms.empty:
        st.info("No troubleshooting rows in this workbook.")
        return

    by_id = model.ts_by_id()
    options = []
    labels = {}
    for _, row in symptoms.iterrows():
        key = row["ID"]
        options.append(key)
        labels[key] = f"{row.get('Type')}  ·  {row['ID']}  ·  {row.get('StepID')}"

    if "ts_symptom" not in st.session_state or st.session_state.ts_symptom not in options:
        st.session_state.ts_symptom = options[0]
        st.session_state.ts_node = options[0]
        st.session_state.ts_path = [options[0]]

    chosen = st.selectbox(
        "Symptom",
        options=options,
        format_func=lambda sid: labels.get(sid, sid),
        key="ts_symptom",
    )
    if st.session_state.get("ts_path_root") != chosen:
        st.session_state.ts_path_root = chosen
        st.session_state.ts_node = chosen
        st.session_state.ts_path = [chosen]

    current_id = st.session_state.ts_node
    node = by_id.get(current_id)
    if node is None:
        st.error(f"Node {current_id} is missing from the tree.")
        return

    path = st.session_state.get("ts_path") or [current_id]
    st.caption("Path: " + " → ".join(path))
    if len(path) > 1:
        if st.button("Back one step", key="ts_back"):
            path = path[:-1]
            st.session_state.ts_path = path
            st.session_state.ts_node = path[-1]
            st.rerun()

    kind = _text(node.get("Type2"))
    stop = _clean_str(node.get("Stop"))
    with st.container(border=True):
        st.markdown(f"**{kind}** · {current_id}")
        st.write(_text(node.get("Prompt")))
        meta = [p for p in [
            f"Who: {node['Who']}" if _clean_str(node.get("Who")) else None,
            f"Control: {node['ControlID']}" if _clean_str(node.get("ControlID")) else None,
        ] if p]
        if meta:
            st.caption(" · ".join(meta))
        if stop == "Y":
            st.error("Stop the job before you continue.")
        elif stop == "N":
            st.info("Job can stay running while you work this step.")

        how = _clean_str(node.get("How to check"))
        if how:
            st.write(how)

    yes_id = _clean_str(node.get("If yes"))
    no_id = _clean_str(node.get("If no"))

    def _go(next_id: str) -> None:
        st.session_state.ts_node = next_id
        st.session_state.ts_path = path + [next_id]
        st.rerun()

    if yes_id and no_id and yes_id != no_id:
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Yes", key=f"yes_{current_id}", width="stretch"):
                _go(yes_id)
        with c2:
            if st.button("No", key=f"no_{current_id}", width="stretch"):
                _go(no_id)
    elif yes_id or no_id:
        next_id = yes_id or no_id
        if st.button("Continue", key=f"cont_{current_id}"):
            _go(next_id)
    else:
        st.success("End of this path.")
        if st.button("Restart symptom", key="ts_restart"):
            st.session_state.ts_node = chosen
            st.session_state.ts_path = [chosen]
            st.rerun()

    with st.expander("Tree map"):
        group = model.troubleshooting
        if "Type" in group.columns and _clean_str(node.get("Type")):
            group = group[group["Type"] == node.get("Type")]
        tree_chart = build_tree_graph(group, current_id)
        st.graphviz_chart(tree_chart, width="stretch")
        _download_chart(
            tree_chart,
            "troubleshoot-tree.png",
            "download_tree_png",
            "Download this tree",
            fallback=export_tree_image(group, current_id),
        )


def render_documents_tab(model: ProcessData) -> None:
    uploads = st.session_state.get("pdf_files") or {}
    wis = model.work_instructions
    if wis.empty:
        st.info("No work instructions in this workbook.")
        return

    st.caption("SOPs from the Work Instructions sheet. Add matching PDFs in the sidebar to enable download.")
    for _, row in wis.iterrows():
        with st.container(border=True):
            st.markdown(f"**{_text(row.get('Title'))}**")
            st.caption(
                " · ".join(
                    [
                        p
                        for p in [
                            _clean_str(row.get("WI_ID")),
                            _clean_str(row.get("Audience")),
                            f"Step {_clean_str(row.get('StepID'))}" if _clean_str(row.get("StepID")) else None,
                            _clean_str(row.get("File")),
                        ]
                        if p
                    ]
                )
            )
            filename = _clean_str(row.get("File"))
            if filename and filename in uploads:
                st.download_button(
                    "Download PDF",
                    data=uploads[filename],
                    file_name=filename,
                    mime="application/pdf",
                    key=f"dl_{row.get('WI_ID')}",
                )
            elif filename:
                st.caption("PDF not uploaded this session.")
