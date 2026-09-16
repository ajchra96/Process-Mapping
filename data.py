"""Load the process workbook and expose reusable queries + Graphviz builders."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd
from graphviz import Digraph
from PIL import Image, ImageDraw, ImageFont

# Drying return points at a StepID that is not on the map.
NEXT_ID_ALIASES = {"B-TP-07-01": "B-TP-07-02"}

ZONE_COLORS = [
    "#E8F1F8",
    "#EAF4EA",
    "#F8F0E3",
    "#F3EAF6",
    "#F8EAEA",
    "#E8F5F3",
    "#F4F1E6",
    "#ECEFF4",
]


def _clean_str(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def _clean_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]
    for col in out.columns:
        if out[col].dtype == object:
            out[col] = out[col].map(lambda v: v.strip() if isinstance(v, str) else v)
    return out


def split_ids(value: Any) -> list[str]:
    """Split grouped StepIDs: commas, semicolons, or '. ' used in the tree sheet."""
    text = _clean_str(value)
    if not text:
        return []
    parts = re.split(r"\s*[,;]\s*|\.\s+", text)
    return [p.strip() for p in parts if p.strip()]


def gv_escape(text: Any, max_len: int = 48) -> str:
    raw = _clean_str(text) or ""
    raw = raw.replace("\\", " ").replace('"', "'").replace("\n", " ")
    if len(raw) > max_len:
        raw = raw[: max_len - 1].rstrip() + "…"
    return raw


@dataclass
class ProcessData:
    overview: pd.DataFrame
    process_map: pd.DataFrame
    sipoc: pd.DataFrame
    process_sheet: pd.DataFrame
    pfmea: pd.DataFrame
    troubleshooting: pd.DataFrame
    work_instructions: pd.DataFrame
    maintenance_pm: pd.DataFrame
    source_name: str

    def lines(self) -> list[str]:
        series = self.process_map["Line"].dropna().map(str).map(str.strip)
        # Preserve workbook order.
        seen: list[str] = []
        for line in series:
            if line and line not in seen:
                seen.append(line)
        return seen

    def steps_for_lines(self, lines: list[str]) -> pd.DataFrame:
        if not lines:
            return self.process_map.iloc[0:0]
        mask = self.process_map["Line"].isin(lines)
        return self.process_map.loc[mask].reset_index(drop=True)

    def step_row(self, step_id: str) -> pd.Series | None:
        hits = self.process_map[self.process_map["StepID"] == step_id]
        if hits.empty:
            return None
        return hits.iloc[0]

    def sipoc_for_step(self, step_id: str) -> pd.DataFrame:
        if not step_id:
            return self.sipoc.iloc[0:0]
        mask = self.sipoc["_step_tokens"].map(lambda tokens: step_id in tokens)
        return self.sipoc.loc[mask].reset_index(drop=True)

    def controls_for_step(self, step_id: str) -> pd.DataFrame:
        hits = self.process_sheet[self.process_sheet["StepID"] == step_id]
        return hits.reset_index(drop=True)

    def pfmea_for_control(self, control_id: str | None) -> pd.DataFrame:
        if not control_id:
            return self.pfmea.iloc[0:0]
        hits = self.pfmea[self.pfmea["ControlID"] == control_id]
        return hits.reset_index(drop=True)

    def pm_for_control(self, control_id: str | None) -> pd.DataFrame:
        if not control_id:
            return self.maintenance_pm.iloc[0:0]
        hits = self.maintenance_pm[self.maintenance_pm["ControlID"] == control_id]
        return hits.reset_index(drop=True)

    def symptoms(self, step_id: str | None = None) -> pd.DataFrame:
        tree = self.troubleshooting
        if tree.empty:
            return tree
        symptoms = tree[tree["Type2"] == "Symptom"].copy()
        if step_id:
            mask = symptoms["_step_tokens"].map(lambda tokens: step_id in tokens)
            symptoms = symptoms.loc[mask]
        return symptoms.reset_index(drop=True)

    def ts_by_id(self) -> dict[str, pd.Series]:
        if self.troubleshooting.empty:
            return {}
        return {row["ID"]: row for _, row in self.troubleshooting.iterrows() if _clean_str(row.get("ID"))}

    def wis_for_step(self, step_id: str | None = None) -> pd.DataFrame:
        if step_id:
            mask = self.work_instructions["_step_tokens"].map(lambda tokens: step_id in tokens)
            hits = self.work_instructions.loc[mask]
            if not hits.empty:
                return hits.reset_index(drop=True)
        return self.work_instructions.reset_index(drop=True)


def load_workbook(file_obj, source_name: str = "upload") -> ProcessData:
    sheets = pd.read_excel(file_obj, sheet_name=None)

    required = [
        "Process Map",
        "SIPOC",
        "Process Sheet",
        "PFMEA",
        "Troubleshooting Guide",
        "Work Instructions",
        "Maintenance PM",
    ]
    missing = [name for name in required if name not in sheets]
    if missing:
        raise ValueError("Workbook is missing sheets: " + ", ".join(missing))

    required_cols = {
        "Process Map": ["StepID", "Next Step ID", "Line", "Zone", "Process", "Definition"],
        "SIPOC": ["StepID", "Flow type", "Item"],
        "Process Sheet": ["StepID", "Control", "M"],
        "PFMEA": ["ControlID", "Failure mode"],
        "Troubleshooting Guide": ["ID", "Type2", "Prompt", "If yes", "If no"],
        "Work Instructions": ["WI_ID", "Title", "File", "StepID"],
    }
    problems = []
    for sheet, cols in required_cols.items():
        have = {str(c).strip() for c in sheets[sheet].columns}
        miss = [c for c in cols if c not in have]
        if miss:
            problems.append(f"{sheet}: {', '.join(miss)}")
    if problems:
        raise ValueError("Workbook is missing columns: " + "; ".join(problems))

    overview = _clean_df(sheets.get("Structure Overview", pd.DataFrame()))
    process_map = _clean_df(sheets["Process Map"])
    sipoc = _clean_df(sheets["SIPOC"])
    process_sheet = _clean_df(sheets["Process Sheet"])
    pfmea = _clean_df(sheets["PFMEA"])
    troubleshooting = _clean_df(sheets["Troubleshooting Guide"])
    work_instructions = _clean_df(sheets["Work Instructions"])
    maintenance_pm = _clean_df(sheets["Maintenance PM"])

    process_map["Line"] = process_map["Line"].map(lambda v: _clean_str(v) or "")
    process_map["Zone"] = process_map["Zone"].map(lambda v: _clean_str(v) or "")
    process_map["StepID"] = process_map["StepID"].map(lambda v: _clean_str(v) or "")
    process_map["NextIDs"] = process_map["Next Step ID"].map(_resolve_next_ids)

    sipoc["_step_tokens"] = sipoc["StepID"].map(split_ids)
    sipoc["Flow type"] = sipoc["Flow type"].map(lambda v: (_clean_str(v) or "").title())
    sipoc["CTQ"] = sipoc["CTQ"].map(_as_yes_no)

    process_sheet["ControlID"] = process_sheet["ControlID"].map(_clean_str)
    process_sheet["StepID"] = process_sheet["StepID"].map(_clean_str)

    pfmea["ControlID"] = pfmea["ControlID"].map(_clean_str)
    pfmea["FmeaID"] = pfmea["FmeaID"].map(_clean_str)
    pfmea = pfmea[pfmea["ControlID"].notna()].reset_index(drop=True)

    troubleshooting["ID"] = troubleshooting["ID"].map(_clean_str)
    troubleshooting["Type2"] = troubleshooting["Type2"].map(_clean_str)
    troubleshooting["_step_tokens"] = troubleshooting["StepID"].map(split_ids)

    work_instructions["_step_tokens"] = work_instructions["StepID"].map(split_ids)
    maintenance_pm["ControlID"] = maintenance_pm["ControlID"].map(_clean_str)

    return ProcessData(
        overview=overview,
        process_map=process_map,
        sipoc=sipoc,
        process_sheet=process_sheet,
        pfmea=pfmea,
        troubleshooting=troubleshooting,
        work_instructions=work_instructions,
        maintenance_pm=maintenance_pm,
        source_name=source_name,
    )


def _resolve_next_ids(value: Any) -> list[str]:
    ids = split_ids(value)
    resolved = []
    for item in ids:
        resolved.append(NEXT_ID_ALIASES.get(item, item))
    return resolved


def _as_yes_no(value: Any) -> str | None:
    text = _clean_str(value)
    if text is None:
        return None
    lowered = text.lower()
    if lowered in {"yes", "y", "true", "1"}:
        return "Yes"
    if lowered in {"no", "n", "false", "0"}:
        return "No"
    return text


def zone_color_map(zones: list[str]) -> dict[str, str]:
    colors = {}
    for i, zone in enumerate(zones):
        colors[zone] = ZONE_COLORS[i % len(ZONE_COLORS)]
    return colors


def _stub_id(prefix: str, text: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    return f"{prefix}_{safe or 'unknown'}"


def build_line_graph(
    steps: pd.DataFrame,
    selected_step: str | None = None,
    all_steps: pd.DataFrame | None = None,
) -> Digraph:
    chart = Digraph("line_flow")
    chart.attr(rankdir="LR", splines="spline", nodesep="0.45", ranksep="0.70")
    chart.attr(
        "node",
        shape="box",
        style="rounded,filled",
        fontname="Helvetica",
        fontsize="14",
        color="#8A93A0",
        fillcolor="#F7F8FA",
    )
    chart.attr("edge", color="#5C6670", arrowsize="0.8")
    chart.attr("graph", fontname="Helvetica", fontsize="13")

    if steps.empty:
        chart.node("empty", "No steps for the selected line(s)")
        return chart

    lookup = all_steps if all_steps is not None else steps
    line_by_id = {row["StepID"]: row["Line"] for _, row in lookup.iterrows()}

    known = set(steps["StepID"])
    zones = list(dict.fromkeys(steps["Zone"].tolist()))
    colors = zone_color_map(zones)

    for i, zone in enumerate(zones):
        with chart.subgraph(name=f"cluster_{i}") as cluster:
            cluster.attr(
                label=gv_escape(zone, 60),
                style="rounded,filled",
                color="#D0D5DD",
                fillcolor=colors[zone],
                fontsize="13",
            )
            zone_steps = steps[steps["Zone"] == zone]
            for _, row in zone_steps.iterrows():
                step_id = row["StepID"]
                label = f"{step_id}\\n{gv_escape(row['Process'], 40)}"
                attrs = {}
                if step_id == selected_step:
                    attrs = {
                        "fillcolor": "#C8102E",
                        "fontcolor": "white",
                        "color": "#8E0B20",
                        "penwidth": "2.0",
                        "fontsize": "16",
                    }
                cluster.node(step_id, label, **attrs)

    drawn_targets: set[str] = set()
    for _, row in steps.iterrows():
        src = row["StepID"]
        for dest in row["NextIDs"]:
            edge_attrs: dict[str, str] = {}
            if dest == "Next station":
                node_id = dest
                if node_id not in drawn_targets:
                    chart.node(
                        node_id,
                        "Next station",
                        shape="hexagon",
                        fillcolor="#FFFFFF",
                        style="rounded,filled,dashed",
                    )
                    drawn_targets.add(node_id)
                edge_attrs["style"] = "dashed"
            elif dest not in known:
                dest_line = line_by_id.get(dest)
                if dest_line:
                    node_id = _stub_id("next_line", dest_line)
                    if node_id not in drawn_targets:
                        chart.node(
                            node_id,
                            f"Next line\\n{gv_escape(dest_line, 42)}",
                            fillcolor="#FFFFFF",
                            style="rounded,filled,dashed",
                            fontcolor="#344054",
                        )
                        drawn_targets.add(node_id)
                else:
                    node_id = dest
                    if node_id not in drawn_targets:
                        chart.node(
                            node_id,
                            f"{dest}\\n(outside selection)",
                            fillcolor="#FFFFFF",
                            style="rounded,filled,dashed",
                            fontcolor="#667085",
                        )
                        drawn_targets.add(node_id)
                edge_attrs["style"] = "dashed"
                edge_attrs["color"] = "#98A2B3"
            else:
                node_id = dest
            chart.edge(src, node_id, **edge_attrs)
    return chart


def export_graph_image(chart: Digraph, fmt: str = "png") -> bytes | None:
    """Prefer Graphviz `dot` when present; otherwise None."""
    try:
        return chart.pipe(format=fmt)
    except Exception:
        return None


def _load_font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines[:3]


def export_line_image(
    steps: pd.DataFrame,
    selected_step: str | None = None,
    all_steps: pd.DataFrame | None = None,
) -> bytes:
    """PNG of the selected line. Pure Python so the download button always works."""
    box_w, box_h = 230, 88
    gap_x, top, left = 36, 58, 28
    lookup = all_steps if all_steps is not None else steps
    line_by_id = {row["StepID"]: row["Line"] for _, row in lookup.iterrows()}
    known = set(steps["StepID"]) if not steps.empty else set()

    cards: list[dict[str, Any]] = []
    for _, row in steps.iterrows():
        cards.append(
            {
                "id": row["StepID"],
                "title": str(row["StepID"]),
                "subtitle": str(row["Process"]),
                "zone": str(row["Zone"]),
                "selected": row["StepID"] == selected_step,
                "stub": False,
            }
        )

    stub_ids: list[str] = []
    seen_stubs: set[str] = set()
    for _, row in steps.iterrows():
        for dest in row["NextIDs"]:
            if dest in known:
                continue
            if dest == "Next station":
                label, key = "Next station", "Next station"
            else:
                dest_line = line_by_id.get(dest)
                label = f"Next line: {dest_line}" if dest_line else dest
                key = dest_line or dest
            if key in seen_stubs:
                continue
            seen_stubs.add(key)
            stub_ids.append(key)
            cards.append(
                {
                    "id": key,
                    "title": "Next line" if dest != "Next station" else "Next station",
                    "subtitle": dest_line or dest,
                    "zone": "",
                    "selected": False,
                    "stub": True,
                }
            )

    count = max(len(cards), 1)
    width = left + count * (box_w + gap_x) + left
    height = top + box_h + 40
    image = Image.new("RGB", (width, height), "#F7F8FA")
    draw = ImageDraw.Draw(image)
    title_font = _load_font(16)
    body_font = _load_font(13)
    zone_font = _load_font(12)

    positions = {}
    last_zone = None
    for i, card in enumerate(cards):
        x = left + i * (box_w + gap_x)
        y = top
        positions[card["id"]] = (x, y, x + box_w, y + box_h)
        if card["zone"] and card["zone"] != last_zone:
            draw.text((x, 18), card["zone"][:42], fill="#344054", font=zone_font)
            last_zone = card["zone"]
        fill = "#C8102E" if card["selected"] else ("#FFFFFF" if card["stub"] else "#E8F1F8")
        outline = "#8E0B20" if card["selected"] else "#98A2B3"
        draw.rounded_rectangle((x, y, x + box_w, y + box_h), radius=10, fill=fill, outline=outline, width=2)
        ink = "white" if card["selected"] else "#1D2433"
        draw.text((x + 10, y + 8), card["title"][:28], fill=ink, font=title_font)
        wrapped = _wrap(draw, card["subtitle"], body_font, box_w - 20)
        for line_i, line in enumerate(wrapped):
            draw.text((x + 10, y + 34 + line_i * 16), line, fill=ink, font=body_font)

    for _, row in steps.iterrows():
        src = row["StepID"]
        if src not in positions:
            continue
        for dest in row["NextIDs"]:
            if dest in known:
                target = dest
            elif dest == "Next station":
                target = "Next station"
            else:
                target = line_by_id.get(dest) or dest
            if target not in positions:
                continue
            x1 = positions[src][2]
            y1 = (positions[src][1] + positions[src][3]) // 2
            x2 = positions[target][0]
            y2 = (positions[target][1] + positions[target][3]) // 2
            draw.line((x1, y1, x2, y2), fill="#5C6670", width=2)
            draw.polygon([(x2, y2), (x2 - 8, y2 - 5), (x2 - 8, y2 + 5)], fill="#5C6670")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def build_sipoc_graph(step_row: pd.Series, sipoc: pd.DataFrame) -> Digraph:
    chart = Digraph("sipoc")
    chart.attr(rankdir="LR", splines="spline", nodesep="0.4", ranksep="0.8")
    chart.attr("node", shape="box", style="rounded,filled", fontname="Helvetica", fontsize="10")
    chart.attr("edge", color="#5C6670", arrowsize="0.7")

    process_id = step_row["StepID"]
    process_label = f"{process_id}\\n{gv_escape(step_row['Process'], 40)}"
    chart.node(
        "PROCESS",
        process_label,
        fillcolor="#C8102E",
        fontcolor="white",
        color="#8E0B20",
        penwidth="1.4",
    )

    inputs = sipoc[sipoc["Flow type"] == "Input"].reset_index(drop=True)
    outputs = sipoc[sipoc["Flow type"] == "Output"].reset_index(drop=True)

    if inputs.empty and outputs.empty:
        chart.node("none", "No SIPOC rows for this step", fillcolor="#F7F8FA")
        return chart

    for i, row in inputs.iterrows():
        node_id = f"IN_{i}"
        badge = " · CTQ" if row.get("CTQ") == "Yes" else ""
        label = f"IN{badge}\\n{gv_escape(row.get('Item'), 40)}"
        chart.node(node_id, label, fillcolor="#E8F1F8", color="#8AA6C1")
        chart.edge(node_id, "PROCESS")

    for i, row in outputs.iterrows():
        node_id = f"OUT_{i}"
        badge = " · CTQ" if row.get("CTQ") == "Yes" else ""
        label = f"OUT{badge}\\n{gv_escape(row.get('Item'), 40)}"
        chart.node(node_id, label, fillcolor="#EAF4EA", color="#8BB38B")
        chart.edge("PROCESS", node_id)

    return chart


def build_tree_graph(tree: pd.DataFrame, current_id: str | None) -> Digraph:
    chart = Digraph("ts_tree")
    chart.attr(rankdir="TB", splines="spline", nodesep="0.25", ranksep="0.35")
    chart.attr("node", shape="box", style="rounded,filled", fontname="Helvetica", fontsize="9")
    chart.attr("edge", color="#5C6670", arrowsize="0.6", fontsize="8")

    if tree.empty:
        chart.node("empty", "No tree")
        return chart

    colors = {"Symptom": "#F8EAEA", "Question": "#E8F1F8", "Action": "#EAF4EA"}
    for _, row in tree.iterrows():
        node_id = row["ID"]
        kind = row.get("Type2") or ""
        label = f"{node_id}\\n{kind}"
        attrs = {"fillcolor": colors.get(kind, "#F7F8FA")}
        if node_id == current_id:
            attrs.update(fillcolor="#C8102E", fontcolor="white", color="#8E0B20")
        if _clean_str(row.get("Stop")) == "Y":
            attrs["peripheries"] = "2"
        chart.node(node_id, label, **attrs)

    ids = set(tree["ID"])
    for _, row in tree.iterrows():
        src = row["ID"]
        yes_id = _clean_str(row.get("If yes"))
        no_id = _clean_str(row.get("If no"))
        if yes_id and yes_id in ids:
            chart.edge(src, yes_id, label="yes" if yes_id != no_id else "")
        if no_id and no_id in ids and no_id != yes_id:
            chart.edge(src, no_id, label="no")
    return chart
