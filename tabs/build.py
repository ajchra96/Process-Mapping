"""Build tab — add / edit / remove steps, SIPOC, controls, and PFMEA."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from data import (
    ProcessData,
    _clean_str,
    build_line_graph,
    export_workbook,
    next_control_id,
    next_fmea_id,
    rebuild_model_from_working,
    remove_step_id_from_next,
    sipoc_without_step,
    split_ids,
    step_families,
    suggest_step_id,
    working_frames,
)
from tabs._common import download_graphviz_png, text


def _choice_index(options: list[str], value, default: int = 0) -> int:
    current = _clean_str(value) or ""
    if current in options:
        return options.index(current)
    return default


def _ensure_working(model: ProcessData) -> dict[str, pd.DataFrame]:
    stored = st.session_state.get("working_sheets")
    if not stored:
        st.session_state.working_sheets = working_frames(model)
        st.session_state.build_dirty = bool(st.session_state.get("build_dirty"))
    return st.session_state.working_sheets


def _commit(source_name: str) -> None:
    raw = st.session_state.get("raw_sheets") or {}
    model = rebuild_model_from_working(raw, st.session_state.working_sheets, source_name)
    st.session_state.model = model
    st.session_state.build_dirty = True


def _append_row(sheet: str, row: dict) -> None:
    frame = st.session_state.working_sheets[sheet]
    st.session_state.working_sheets[sheet] = pd.concat(
        [frame, pd.DataFrame([row])],
        ignore_index=True,
    )


def _replace_row(sheet: str, index: int, row: dict) -> None:
    frame = st.session_state.working_sheets[sheet].copy()
    for key, value in row.items():
        if key not in frame.columns:
            frame[key] = None
        frame.at[index, key] = value
    st.session_state.working_sheets[sheet] = frame.reset_index(drop=True)


def _drop_rows(sheet: str, mask) -> None:
    frame = st.session_state.working_sheets[sheet]
    st.session_state.working_sheets[sheet] = frame.loc[~mask].reset_index(drop=True)


def render_build_tab(model: ProcessData) -> None:
    working = _ensure_working(model)
    source_name = st.session_state.get("source_name") or model.source_name

    top = st.columns([3, 1, 1])
    with top[0]:
        st.caption(
            "Working copy in this tab only. IDs for controls and PFMEA are assigned. "
            "Download the workbook to keep changes; refresh discards them."
        )
        if st.session_state.get("build_dirty"):
            st.warning("This session has unpublished edits.")
    with top[1]:
        payload = export_workbook(st.session_state.get("raw_sheets") or {}, working)
        base = source_name.rsplit(".", 1)[0] if source_name else "Processes"
        st.download_button(
            "Download workbook",
            data=payload,
            file_name=f"{base}-updated.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with top[2]:
        if st.button("Discard edits"):
            raw_bytes = st.session_state.get("workbook_bytes")
            if raw_bytes:
                from data import load_workbook

                restored = load_workbook(raw_bytes, source_name=source_name)
                st.session_state.model = restored
                st.session_state.raw_sheets = restored.raw_sheets
                st.session_state.working_sheets = working_frames(restored)
                st.session_state.build_dirty = False
                st.rerun()

    process_map = working["Process Map"]
    lines = []
    for line in process_map.get("Line", pd.Series(dtype=str)).tolist():
        text_line = _clean_str(line)
        if text_line and text_line not in lines:
            lines.append(text_line)
    if not lines:
        st.info("Process Map is empty. Add a step to start.")
        _add_step_form(process_map, lines)
        return

    current_lines = [
        line for line in (st.session_state.get("build_lines") or lines) if line in lines
    ] or list(lines)
    if st.session_state.get("build_lines") != current_lines:
        st.session_state.build_lines = current_lines

    selected_lines = st.multiselect("Lines", options=lines, key="build_lines")
    st.radio("Chart layout", options=["Horizontal", "Vertical"], horizontal=True, key="build_orientation")

    visible = process_map[process_map["Line"].isin(selected_lines)].reset_index(drop=True)
    step_ids = [_clean_str(s) for s in visible.get("StepID", pd.Series(dtype=str)).tolist() if _clean_str(s)]
    if not step_ids:
        st.info("Select a line or add a step.")
        _add_step_form(process_map, lines)
        return

    if st.session_state.get("build_step") not in step_ids:
        st.session_state.build_step = step_ids[0]

    rebuilt = rebuild_model_from_working(
        st.session_state.get("raw_sheets") or {},
        working,
        source_name,
    )
    chart_steps = rebuilt.steps_for_lines(selected_lines)
    rankdir = "TB" if st.session_state.get("build_orientation") == "Vertical" else "LR"
    chart = build_line_graph(
        chart_steps,
        st.session_state.build_step,
        all_steps=rebuilt.process_map,
        rankdir=rankdir,
    )
    st.graphviz_chart(chart, width="content")
    download_graphviz_png(chart, "process-flow.png", "Download this chart")

    labels = {
        _clean_str(row["StepID"]): f"{row['StepID']}  ·  {row.get('Process') or ''}  ·  {row.get('Line') or ''}"
        for _, row in visible.iterrows()
        if _clean_str(row.get("StepID"))
    }
    st.selectbox(
        "Step",
        options=step_ids,
        format_func=lambda sid: labels.get(sid, sid),
        key="build_step",
    )

    with st.expander("Add step", expanded=False):
        _add_step_form(process_map, lines)

    step_id = st.session_state.build_step
    hits = process_map[process_map["StepID"] == step_id]
    if hits.empty:
        st.warning("Selected StepID is not on the working map.")
        return
    step_index = int(hits.index[0])
    step = hits.iloc[0]

    _edit_step_form(process_map, step_index, step, lines, source_name)
    _sipoc_block(working, step, source_name)
    _controls_block(working, step, source_name)


def _add_step_form(process_map: pd.DataFrame, lines: list[str]) -> None:
    families = step_families(process_map) or ["B-P", "B-T", "B-TP", "B-S"]
    all_ids = [_clean_str(s) for s in process_map.get("StepID", pd.Series(dtype=str)).tolist() if _clean_str(s)]
    with st.form("build_add_step"):
        col_a, col_b = st.columns(2)
        with col_a:
            line = st.selectbox("Line", options=lines or [""], key="add_step_line") if lines else ""
            new_line = st.text_input("Or new line name", key="add_step_new_line")
            zone = st.text_input("Zone", key="add_step_zone")
            family = st.selectbox("ID family", options=families, key="add_step_family")
            suggested = suggest_step_id(process_map, family)
            step_id = st.text_input("StepID", value=suggested)
        with col_b:
            process = st.text_input("Process name", key="add_step_process")
            definition = st.text_area("Definition", key="add_step_def")
            next_ids = st.multiselect("Next step(s)", options=all_ids, key="add_step_next")
        submitted = st.form_submit_button("Create step")
    if not submitted:
        return
    line_value = _clean_str(new_line) or _clean_str(line)
    sid = _clean_str(step_id)
    if not sid:
        st.error("StepID is required.")
        return
    if sid in all_ids:
        st.error(f"{sid} already exists.")
        return
    if not line_value:
        st.error("Line is required.")
        return
    if not _clean_str(process):
        st.error("Process name is required.")
        return
    row = {col: None for col in process_map.columns}
    row.update(
        {
            "StepID": sid,
            "Next Step ID": ", ".join(next_ids),
            "Line": line_value,
            "Zone": _clean_str(zone),
            "Process": _clean_str(process),
            "Definition": _clean_str(definition),
        }
    )
    _append_row("Process Map", row)
    st.session_state.build_step = sid
    current = list(st.session_state.get("build_lines") or [])
    if line_value and line_value not in current:
        st.session_state.build_lines = current + [line_value]
    _commit(st.session_state.get("source_name") or "upload")
    st.rerun()


def _edit_step_form(
    process_map: pd.DataFrame,
    step_index: int,
    step: pd.Series,
    lines: list[str],
    source_name: str,
) -> None:
    step_id = _clean_str(step.get("StepID")) or ""
    all_ids = [
        sid
        for sid in (_clean_str(s) for s in process_map.get("StepID", pd.Series(dtype=str)).tolist())
        if sid and sid != step_id
    ]
    current_next = split_ids(step.get("Next Step ID"))
    current_next = [sid for sid in current_next if sid in all_ids]
    line_options = list(lines)
    current_line = _clean_str(step.get("Line")) or ""
    if current_line and current_line not in line_options:
        line_options.append(current_line)

    st.markdown("**Step**")
    with st.form(f"build_edit_step_{step_id}"):
        col_a, col_b = st.columns(2)
        with col_a:
            line = st.selectbox(
                "Line",
                options=line_options,
                index=line_options.index(current_line) if current_line in line_options else 0,
            )
            zone = st.text_input("Zone", value=_clean_str(step.get("Zone")) or "")
            status = st.text_input("Status", value=_clean_str(step.get("Status")) or "")
        with col_b:
            process = st.text_input("Process name", value=_clean_str(step.get("Process")) or "")
            next_ids = st.multiselect("Next step(s)", options=all_ids, default=current_next)
        definition = st.text_area("Definition", value=_clean_str(step.get("Definition")) or "")
        save = st.form_submit_button("Save step")
        remove = st.form_submit_button("Remove this step")

    if save:
        _replace_row(
            "Process Map",
            step_index,
            {
                "Line": _clean_str(line),
                "Zone": _clean_str(zone),
                "Process": _clean_str(process),
                "Definition": _clean_str(definition),
                "Next Step ID": ", ".join(next_ids),
                "Status": _clean_str(status),
            },
        )
        _commit(source_name)
        st.rerun()

    if remove:
        working = st.session_state.working_sheets
        working["Process Map"] = remove_step_id_from_next(
            working["Process Map"][working["Process Map"]["StepID"] != step_id].reset_index(drop=True),
            step_id,
        )
        working["SIPOC"] = sipoc_without_step(working["SIPOC"], step_id)
        sheet = working["Process Sheet"]
        control_ids = [
            _clean_str(cid)
            for cid in sheet.loc[sheet.get("StepID") == step_id, "ControlID"].tolist()
            if _clean_str(cid)
        ] if not sheet.empty and "ControlID" in sheet.columns else []
        working["Process Sheet"] = sheet[sheet.get("StepID") != step_id].reset_index(drop=True)
        if control_ids:
            working["PFMEA"] = working["PFMEA"][~working["PFMEA"]["ControlID"].isin(control_ids)].reset_index(drop=True)
        remaining = [
            _clean_str(s)
            for s in working["Process Map"].get("StepID", pd.Series(dtype=str)).tolist()
            if _clean_str(s)
        ]
        st.session_state.build_step = remaining[0] if remaining else None
        _commit(source_name)
        st.rerun()


def _sipoc_block(working: dict[str, pd.DataFrame], step: pd.Series, source_name: str) -> None:
    step_id = _clean_str(step.get("StepID")) or ""
    sipoc = working["SIPOC"]
    tokens_col = sipoc["StepID"].map(split_ids) if not sipoc.empty and "StepID" in sipoc.columns else pd.Series(dtype=object)
    mask = tokens_col.map(lambda tokens: step_id in tokens) if not sipoc.empty else pd.Series(dtype=bool)
    rows = sipoc.loc[mask] if not sipoc.empty else sipoc
    all_ids = [
        sid
        for sid in (
            _clean_str(s) for s in working["Process Map"].get("StepID", pd.Series(dtype=str)).tolist()
        )
        if sid
    ]

    st.markdown("**SIPOC**")
    if rows.empty:
        st.caption("No SIPOC rows on this step yet.")
        options = []
    else:
        options = list(rows.index)
        labels = {
            idx: f"{text(sipoc.at[idx, 'Flow type'])} · {text(sipoc.at[idx, 'Item'])}"
            for idx in options
        }
        selected = st.selectbox(
            "SIPOC row",
            options=options,
            format_func=lambda idx: labels.get(idx, str(idx)),
            key=f"build_sipoc_row_{step_id}",
        )
        row = sipoc.loc[selected]
        attached = split_ids(row.get("StepID"))
        attached = [sid for sid in attached if sid in all_ids] or [step_id]
        with st.form(f"build_edit_sipoc_{step_id}_{selected}"):
            flow = st.selectbox(
                "Flow type",
                options=["Input", "Output"],
                index=0 if (_clean_str(row.get("Flow type")) or "Input").title() != "Output" else 1,
            )
            item = st.text_input("Item", value=_clean_str(row.get("Item")) or "")
            requirement = st.text_area("Requirement", value=_clean_str(row.get("Requirement")) or "")
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                role = st.text_input("Party role", value=_clean_str(row.get("Party role")) or "")
            with col_b:
                party = st.text_input("Party", value=_clean_str(row.get("Party")) or "")
            with col_c:
                ctq = st.selectbox(
                    "CTQ",
                    options=["", "Yes", "No"],
                    index=_choice_index(["", "Yes", "No"], row.get("CTQ")),
                )
            shared = st.multiselect(
                "Shared with steps",
                options=all_ids,
                default=attached,
                help="This row belongs to every selected StepID.",
            )
            save = st.form_submit_button("Save SIPOC row")
            remove = st.form_submit_button("Remove SIPOC row")
        if save:
            if not shared:
                st.error("Pick at least one StepID so the row stays attached.")
            else:
                _replace_row(
                    "SIPOC",
                    int(selected),
                    {
                        "StepID": ", ".join(shared),
                        "Flow type": flow,
                        "Item": _clean_str(item),
                        "Requirement": _clean_str(requirement),
                        "Party role": _clean_str(role),
                        "Party": _clean_str(party),
                        "CTQ": _clean_str(ctq),
                        "Line": _clean_str(row.get("Line")) or _clean_str(step.get("Line")),
                        "Zone": _clean_str(row.get("Zone")) or _clean_str(step.get("Zone")),
                        "Process": _clean_str(row.get("Process")) or _clean_str(step.get("Process")),
                    },
                )
                _commit(source_name)
                st.rerun()
        if remove:
            _drop_rows("SIPOC", sipoc.index == selected)
            _commit(source_name)
            st.rerun()

    with st.expander("Add SIPOC row"):
        with st.form(f"build_add_sipoc_{step_id}"):
            flow = st.selectbox("Flow type", options=["Input", "Output"])
            item = st.text_input("Item")
            requirement = st.text_area("Requirement")
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                role = st.text_input("Party role")
            with col_b:
                party = st.text_input("Party")
            with col_c:
                ctq = st.selectbox("CTQ", options=["", "Yes", "No"])
            shared = st.multiselect(
                "Shared with steps",
                options=all_ids,
                default=[step_id],
            )
            create = st.form_submit_button("Create SIPOC row")
        if create:
            if not _clean_str(item):
                st.error("Item is required.")
            elif not shared:
                st.error("Pick at least one StepID.")
            else:
                row = {col: None for col in working["SIPOC"].columns} if not working["SIPOC"].empty else {}
                row.update(
                    {
                        "StepID": ", ".join(shared),
                        "Line": _clean_str(step.get("Line")),
                        "Zone": _clean_str(step.get("Zone")),
                        "Process": _clean_str(step.get("Process")),
                        "Flow type": flow,
                        "Item": _clean_str(item),
                        "Requirement": _clean_str(requirement),
                        "Party role": _clean_str(role),
                        "Party": _clean_str(party),
                        "CTQ": _clean_str(ctq),
                    }
                )
                _append_row("SIPOC", row)
                _commit(source_name)
                st.rerun()


def _controls_block(working: dict[str, pd.DataFrame], step: pd.Series, source_name: str) -> None:
    step_id = _clean_str(step.get("StepID")) or ""
    sheet = working["Process Sheet"]
    controls = sheet[sheet.get("StepID") == step_id].copy() if not sheet.empty else sheet
    control_ids = [
        cid
        for cid in (_clean_str(v) for v in controls.get("ControlID", pd.Series(dtype=str)).tolist())
        if cid
    ]

    st.markdown("**Process sheet**")
    preview_id = next_control_id(step_id, sheet)
    if control_ids:
        selected = st.selectbox(
            "Control",
            options=control_ids,
            key=f"build_control_{step_id}",
        )
        hits = sheet[sheet["ControlID"] == selected]
        index = int(hits.index[0])
        row = hits.iloc[0]
        with st.form(f"build_edit_control_{selected}"):
            variable = st.text_input("Variable", value=_clean_str(row.get("Variable")) or "")
            m_cat = st.text_input("M Category", value=_clean_str(row.get("M Category")) or "")
            kind = st.text_input("Type", value=_clean_str(row.get("Type")) or "")
            standard = st.text_area("Standard", value=_clean_str(row.get("Standard")) or "")
            rationale = st.text_area("Rationale", value=_clean_str(row.get("Rationale")) or "")
            owner = st.text_input("Owner", value=_clean_str(row.get("Owner")) or "")
            status = st.text_input("Status", value=_clean_str(row.get("Status")) or "")
            st.caption(f"ControlID {selected} is locked.")
            save = st.form_submit_button("Save control")
            remove = st.form_submit_button("Remove control")
        if save:
            _replace_row(
                "Process Sheet",
                index,
                {
                    "Variable": _clean_str(variable),
                    "M Category": _clean_str(m_cat),
                    "Type": _clean_str(kind),
                    "Standard": _clean_str(standard),
                    "Rationale": _clean_str(rationale),
                    "Owner": _clean_str(owner),
                    "Status": _clean_str(status),
                },
            )
            _commit(source_name)
            st.rerun()
        if remove:
            _drop_rows("Process Sheet", sheet["ControlID"] == selected)
            pfmea = working["PFMEA"]
            if not pfmea.empty and "ControlID" in pfmea.columns:
                working["PFMEA"] = pfmea[pfmea["ControlID"] != selected].reset_index(drop=True)
            _commit(source_name)
            st.rerun()
        _pfmea_block(working, selected, source_name)
    else:
        st.caption("No controls on this step yet.")

    with st.expander("Add control"):
        st.caption(f"Next ControlID: `{preview_id}`")
        with st.form(f"build_add_control_{step_id}"):
            variable = st.text_input("Variable")
            m_cat = st.text_input("M Category")
            kind = st.text_input("Type")
            standard = st.text_area("Standard")
            rationale = st.text_area("Rationale")
            owner = st.text_input("Owner")
            create = st.form_submit_button("Create control")
        if create:
            if not _clean_str(variable):
                st.error("Variable is required.")
            else:
                row = {col: None for col in sheet.columns} if not sheet.empty else {}
                row.update(
                    {
                        "ControlID": preview_id,
                        "StepID": step_id,
                        "Variable": _clean_str(variable),
                        "M Category": _clean_str(m_cat),
                        "Type": _clean_str(kind),
                        "Standard": _clean_str(standard),
                        "Rationale": _clean_str(rationale),
                        "Owner": _clean_str(owner),
                    }
                )
                _append_row("Process Sheet", row)
                st.session_state[f"build_control_{step_id}"] = preview_id
                _commit(source_name)
                st.rerun()


def _pfmea_block(working: dict[str, pd.DataFrame], control_id: str, source_name: str) -> None:
    pfmea = working["PFMEA"]
    rows = pfmea[pfmea.get("ControlID") == control_id] if not pfmea.empty else pfmea
    fmea_ids = [
        fid
        for fid in (_clean_str(v) for v in rows.get("PFMEAID", pd.Series(dtype=str)).tolist())
        if fid
    ]
    preview_id = next_fmea_id(control_id, pfmea)

    st.markdown("**PFMEA**")
    if fmea_ids:
        selected = st.selectbox(
            "Failure mode",
            options=fmea_ids,
            key=f"build_fmea_{control_id}",
        )
        hits = pfmea[pfmea["PFMEAID"] == selected]
        index = int(hits.index[0])
        row = hits.iloc[0]
        with st.form(f"build_edit_fmea_{selected}"):
            asset = st.text_input("Asset", value=_clean_str(row.get("Asset")) or "")
            function = st.text_input("Function", value=_clean_str(row.get("Function")) or "")
            requirement = st.text_area("Requirement", value=_clean_str(row.get("Requirement")) or "")
            mode = st.text_area("Failure mode", value=_clean_str(row.get("Failure mode")) or "")
            cause = st.text_area("Cause", value=_clean_str(row.get("Cause")) or "")
            prev_c = st.text_area("Prevention", value=_clean_str(row.get("Prev. control")) or "")
            det_c = st.text_area("Detection", value=_clean_str(row.get("Det. control")) or "")
            freq = st.text_input("Frequency", value=_clean_str(row.get("Frequency")) or "")
            status = st.text_input("Status", value=_clean_str(row.get("Status")) or "")
            st.caption(f"PFMEAID {selected} is locked.")
            save = st.form_submit_button("Save PFMEA")
            remove = st.form_submit_button("Remove PFMEA")
        if save:
            _replace_row(
                "PFMEA",
                index,
                {
                    "Asset": _clean_str(asset),
                    "Function": _clean_str(function),
                    "Requirement": _clean_str(requirement),
                    "Failure mode": _clean_str(mode),
                    "Cause": _clean_str(cause),
                    "Prev. control": _clean_str(prev_c),
                    "Det. control": _clean_str(det_c),
                    "Frequency": _clean_str(freq),
                    "Status": _clean_str(status),
                },
            )
            _commit(source_name)
            st.rerun()
        if remove:
            _drop_rows("PFMEA", pfmea["PFMEAID"] == selected)
            _commit(source_name)
            st.rerun()
    else:
        st.caption("No PFMEA rows on this control yet.")

    with st.expander("Add PFMEA"):
        st.caption(f"Next PFMEAID: `{preview_id}`")
        with st.form(f"build_add_fmea_{control_id}"):
            asset = st.text_input("Asset")
            function = st.text_input("Function")
            requirement = st.text_area("Requirement")
            mode = st.text_area("Failure mode")
            cause = st.text_area("Cause")
            prev_c = st.text_area("Prevention")
            det_c = st.text_area("Detection")
            freq = st.text_input("Frequency")
            create = st.form_submit_button("Create PFMEA")
        if create:
            if not _clean_str(mode):
                st.error("Failure mode is required.")
            else:
                row = {col: None for col in pfmea.columns} if not pfmea.empty else {}
                row.update(
                    {
                        "PFMEAID": preview_id,
                        "ControlID": control_id,
                        "Asset": _clean_str(asset),
                        "Function": _clean_str(function),
                        "Requirement": _clean_str(requirement),
                        "Failure mode": _clean_str(mode),
                        "Cause": _clean_str(cause),
                        "Prev. control": _clean_str(prev_c),
                        "Det. control": _clean_str(det_c),
                        "Frequency": _clean_str(freq),
                    }
                )
                _append_row("PFMEA", row)
                st.session_state[f"build_fmea_{control_id}"] = preview_id
                _commit(source_name)
                st.rerun()
