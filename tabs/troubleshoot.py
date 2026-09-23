"""Troubleshoot-tab walkthrough and tree."""

from __future__ import annotations

import streamlit as st

from data import ProcessData, _clean_str, build_tree_graph
from tabs._common import download_graphviz_png, text


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

    kind = text(node.get("Type2"))
    stop = _clean_str(node.get("Stop"))
    with st.container(border=True):
        st.markdown(f"**{kind}** · {current_id}")
        st.write(text(node.get("Prompt")))
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
        download_graphviz_png(tree_chart, "troubleshoot-tree.png", "Download this tree")
