# Process viewer

Streamlit app that reads a local process workbook (Process Map, SIPOC, Process Sheet, PFMEA, Troubleshooting, Work Instructions, Maintenance PM) and lets you inspect a line, a step, and a troubleshooting path.

The workbook is uploaded in the session. It is not written to disk.

## Local run

```bash
pip install -r requirements.txt
# system Graphviz is required for st.graphviz_chart
# macOS: brew install graphviz
# Debian/Ubuntu: sudo apt-get install graphviz
streamlit run streamlit_app.py
```

## Streamlit Community Cloud

1. Push this folder to a GitHub repo. Do not commit the plant workbook if it is sensitive.
2. Deploy `streamlit_app.py`.
3. Cloud installs Python deps from `requirements.txt` and the `graphviz` OS package from `packages.txt`.

## Layout

- `streamlit_app.py` — sidebar upload + tabs
- `tabs.py` — one function per tab
- `data.py` — workbook parse, joins, Graphviz builders
