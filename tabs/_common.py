"""Shared helpers for tab modules."""

from __future__ import annotations

import json

import streamlit as st
import streamlit.components.v1 as components

from data import _clean_str, _compact_key


def text(value) -> str:
    cleaned = _clean_str(value)
    return cleaned if cleaned else "—"


def encased_field(label: str, value) -> None:
    cleaned = _clean_str(value)
    if not cleaned:
        return
    with st.container(border=True):
        st.caption(label)
        st.write(cleaned)


def first_present(row, *names):
    for name in names:
        if name in getattr(row, "index", []):
            return row.get(name)
        if isinstance(row, dict) and name in row:
            return row.get(name)
    wanted = {_compact_key(name) for name in names}
    for col in list(getattr(row, "index", [])):
        if _compact_key(col) in wanted:
            return row.get(col)
    if isinstance(row, dict):
        for col, value in row.items():
            if _compact_key(col) in wanted:
                return value
    return None


def download_graphviz_png(chart, filename: str, label: str = "Download this chart") -> None:
    """PNG from the same DOT the on-screen chart uses, rendered with viz.js.

    Rasterizing Graphviz SVG with drawImage() often yields a white PNG (XML
    prolog / pt units). canvg paints the SVG onto the canvas instead.
    """
    payload = json.dumps(
        {"dot": chart.source, "filename": filename, "label": label},
        ensure_ascii=True,
    )
    components.html(
        f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <style>
    html, body {{
      margin: 0;
      padding: 0;
      background: transparent;
      font-family: Source Sans Pro, Helvetica Neue, Helvetica, Arial, sans-serif;
    }}
    button {{
      height: 38px;
      padding: 0 16px;
      border: 1px solid rgba(49, 51, 63, 0.2);
      border-radius: 0.5rem;
      background: rgb(255, 255, 255);
      color: rgb(49, 51, 63);
      font-size: 14px;
      font-weight: 400;
      cursor: pointer;
    }}
    button:hover {{ border-color: rgb(255, 75, 75); color: rgb(255, 75, 75); }}
    button:disabled {{ opacity: 0.6; cursor: wait; }}
    .err {{ color: #b42318; font-size: 13px; padding: 6px 2px; }}
  </style>
</head>
<body>
  <button id="dl" type="button"></button>
  <div id="err" class="err" hidden></div>
  <script type="module">
    const payload = {payload};
    const btn = document.getElementById("dl");
    const err = document.getElementById("err");
    btn.textContent = payload.label;

    function fail(message) {{
      err.hidden = false;
      err.textContent = message;
      btn.disabled = false;
      btn.textContent = payload.label;
    }}

    function downloadBlob(blob, name) {{
      const href = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = href;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(href), 2000);
    }}

    function cleanSvg(svg) {{
      return String(svg)
        .replace(/^\\s*<\\?xml[^>]*>\\s*/i, "")
        .replace(/<!DOCTYPE[^>]*>\\s*/i, "");
    }}

    function parseSvgSize(svg) {{
      const w = svg.match(/\\bwidth="([0-9.]+)/);
      const h = svg.match(/\\bheight="([0-9.]+)/);
      const vb = svg.match(/viewBox="[^"]*?([0-9.]+)\\s+([0-9.]+)\\s*"/);
      return {{
        w: (w ? parseFloat(w[1]) : 0) || (vb ? parseFloat(vb[1]) : 800),
        h: (h ? parseFloat(h[1]) : 0) || (vb ? parseFloat(vb[2]) : 600),
      }};
    }}

    async function svgToPngBlob(svgText) {{
      const {{ Canvg }} = await import("https://cdn.jsdelivr.net/npm/canvg@4.0.3/+esm");
      const size = parseSvgSize(svgText);
      const scale = 2;
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.ceil(size.w * scale));
      canvas.height = Math.max(1, Math.ceil(size.h * scale));
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      const renderer = await Canvg.fromString(ctx, svgText);
      renderer.resize(canvas.width, canvas.height, "xMidYMid meet");
      await renderer.render();
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
      if (!blob || blob.size < 200) throw new Error("empty-png");
      return blob;
    }}

    btn.addEventListener("click", async () => {{
      btn.disabled = true;
      btn.textContent = "Building image…";
      err.hidden = true;
      try {{
        const {{ instance }} = await import("https://cdn.jsdelivr.net/npm/@viz-js/viz@3.11.0/+esm");
        const viz = await instance();
        const svgText = cleanSvg(viz.renderString(payload.dot, {{ engine: "dot", format: "svg" }}));
        if (!svgText.includes("<svg")) throw new Error("no-svg");
        try {{
          downloadBlob(await svgToPngBlob(svgText), payload.filename);
        }} catch (pngErr) {{
          const svgName = payload.filename.replace(/\\.png$/i, ".svg");
          downloadBlob(new Blob([svgText], {{ type: "image/svg+xml;charset=utf-8" }}), svgName);
          fail("PNG export failed, so the Graphviz SVG was downloaded instead.");
        }}
        btn.textContent = payload.label;
      }} catch (e) {{
        fail("Could not build the download image. Check network access to the Graphviz renderer.");
      }} finally {{
        btn.disabled = false;
      }}
    }});
  </script>
</body>
</html>
        """,
        height=56,
    )
