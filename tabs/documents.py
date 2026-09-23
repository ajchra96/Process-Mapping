"""Documents tab — work instructions and optional SOP PDFs."""

from __future__ import annotations

import streamlit as st

from data import ProcessData, _clean_str
from tabs._common import text


def render_documents_tab(model: ProcessData) -> None:
    uploads = st.session_state.get("pdf_files") or {}
    wis = model.work_instructions
    if wis.empty:
        st.info("No work instructions in this workbook.")
        return

    st.caption("SOPs from the Work Instructions sheet. Add matching PDFs in the sidebar to enable download.")
    for _, row in wis.iterrows():
        with st.container(border=True):
            st.markdown(f"**{text(row.get('Title'))}**")
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
