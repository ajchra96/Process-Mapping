"""Load the process workbook and expose reusable queries + Graphviz builders."""

from __future__ import annotations

import io
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

import pandas as pd
from graphviz import Digraph
from PIL import Image, ImageDraw, ImageFont

# Drying return points at a StepID that is not on the map.
NEXT_ID_ALIASES = {"B-TP-07-01": "B-TP-07-02"}

LINE_COLORS = [
    "#D6E6F5",
    "#D8EEDC",
    "#F3E4C8",
    "#E6D8F0",
    "#F4D6D6",
    "#D4EDEB",
    "#EEE6C9",
    "#DDE2EA",
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


def wrap_words(text: Any, width: int = 42) -> list[str]:
    """Word-wrap. Keeps the full string; does not truncate."""
    raw = _clean_str(text) or ""
    raw = raw.replace("\\", " ").replace('"', "'").replace("\n", " ")
    words = raw.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if len(trial) <= width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def gv_wrap(text: Any, width: int = 42) -> str:
    return "\\n".join(wrap_words(text, width))


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
    lost_time: pd.DataFrame
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

    def lost_time_window(self, start, end) -> pd.DataFrame:
        events = self.lost_time
        if events.empty:
            return events
        start_ts = pd.Timestamp(start).normalize()
        end_ts = pd.Timestamp(end).normalize() + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
        mask = (events["Date"] >= start_ts) & (events["Date"] <= end_ts)
        return events.loc[mask].reset_index(drop=True)

    def lost_time_for_lines(self, events: pd.DataFrame, lines: list[str]) -> pd.DataFrame:
        if events.empty or not lines:
            return events.iloc[0:0]
        mask = events["Line"].isin(lines)
        return events.loc[mask].reset_index(drop=True)

    def lost_time_for_step(self, events: pd.DataFrame, step_id: str) -> pd.DataFrame:
        if events.empty or not step_id:
            return events.iloc[0:0]
        return events.loc[events["StepID"] == step_id].reset_index(drop=True)

    def lost_time_unmapped(self, events: pd.DataFrame) -> pd.DataFrame:
        if events.empty:
            return events.iloc[0:0]
        mapped = events["StepID"].isin(set(self.process_map["StepID"]))
        return events.loc[~mapped].reset_index(drop=True)

    def fmeas_for_step(self, step_id: str) -> pd.DataFrame:
        controls = self.controls_for_step(step_id)
        control_ids = [cid for cid in controls["ControlID"].tolist() if cid]
        if not control_ids or self.pfmea.empty:
            return self.pfmea.iloc[0:0]
        hits = self.pfmea[self.pfmea["ControlID"].isin(control_ids)]
        return hits.reset_index(drop=True)

    def date_span(self) -> tuple[pd.Timestamp, pd.Timestamp] | None:
        if self.lost_time.empty:
            return None
        return self.lost_time["Date"].min(), self.lost_time["Date"].max()


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
        "Process Sheet": ["StepID"],
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

    lost_time = _prepare_lost_time(
        sheets.get("Lost Time DB", pd.DataFrame()),
        process_map,
        pfmea,
    )

    return ProcessData(
        overview=overview,
        process_map=process_map,
        sipoc=sipoc,
        process_sheet=process_sheet,
        pfmea=pfmea,
        troubleshooting=troubleshooting,
        work_instructions=work_instructions,
        maintenance_pm=maintenance_pm,
        lost_time=lost_time,
        source_name=source_name,
    )


def _resolve_next_ids(value: Any) -> list[str]:
    ids = split_ids(value)
    resolved = []
    for item in ids:
        resolved.append(NEXT_ID_ALIASES.get(item, item))
    return resolved


def _coerce_hours(value: Any) -> float | None:
    """Lost Time is stored as hours. Non-numeric cells are dropped."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number):
        return None
    hours = float(number)
    if hours <= 0:
        return None
    return hours


def _prepare_lost_time(raw: pd.DataFrame, process_map: pd.DataFrame, pfmea: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(
            columns=[
                "Date",
                "StepID",
                "FmeaID",
                "Sub-process",
                "Product",
                "Issue",
                "Corrective Action",
                "Who?",
                "Hours",
                "Line",
                "Zone",
                "Process",
                "ControlID",
                "Failure mode",
            ]
        )
    events = _clean_df(raw)
    if "Lost Time" not in events.columns or "Date" not in events.columns:
        return _prepare_lost_time(pd.DataFrame(), process_map, pfmea)

    events["Date"] = pd.to_datetime(events["Date"], errors="coerce")
    events["Hours"] = events["Lost Time"].map(_coerce_hours)
    events = events[events["Date"].notna() & events["Hours"].notna()].copy()
    if events.empty:
        return _prepare_lost_time(pd.DataFrame(), process_map, pfmea)

    events["StepID"] = events["StepID"].map(_clean_str) if "StepID" in events.columns else None
    events["FmeaID"] = events["FmeaID"].map(_clean_str) if "FmeaID" in events.columns else None
    if "Issue (What/Why/How)" in events.columns:
        events["Issue"] = events["Issue (What/Why/How)"].map(_clean_str)
    elif "Issue" in events.columns:
        events["Issue"] = events["Issue"].map(_clean_str)
    else:
        events["Issue"] = None
    for col in ("Sub-process", "Product", "Corrective Action", "Who?"):
        if col in events.columns:
            events[col] = events[col].map(_clean_str)
        else:
            events[col] = None

    map_cols = process_map[["StepID", "Line", "Zone", "Process"]].drop_duplicates("StepID")
    events = events.merge(map_cols, on="StepID", how="left")

    fmea_cols = pfmea[["FmeaID", "ControlID", "Failure mode"]].drop_duplicates("FmeaID") if not pfmea.empty else pd.DataFrame(
        columns=["FmeaID", "ControlID", "Failure mode"]
    )
    events = events.merge(fmea_cols, on="FmeaID", how="left")

    keep = [
        "Date",
        "StepID",
        "FmeaID",
        "Sub-process",
        "Product",
        "Issue",
        "Corrective Action",
        "Who?",
        "Hours",
        "Line",
        "Zone",
        "Process",
        "ControlID",
        "Failure mode",
    ]
    return events[keep].sort_values("Date").reset_index(drop=True)


def week_start(series: pd.Series) -> pd.Series:
    return series.dt.to_period("W-MON").dt.start_time


def weekly_hours(events: pd.DataFrame, group_col: str | None = None) -> pd.DataFrame:
    """Hours by week. One series if group_col is None, else one column per group."""
    if events.empty:
        return pd.DataFrame()
    frame = events.copy()
    frame["Week"] = week_start(frame["Date"])
    if not group_col:
        out = frame.groupby("Week", as_index=True)["Hours"].sum().sort_index().to_frame("Hours")
        return out
    frame[group_col] = frame[group_col].map(lambda v: _clean_str(v) or "—")
    out = (
        frame.groupby(["Week", group_col], as_index=False)["Hours"]
        .sum()
        .pivot(index="Week", columns=group_col, values="Hours")
        .fillna(0.0)
        .sort_index()
    )
    out.columns = [str(c) for c in out.columns]
    return out


def pareto_hours(events: pd.DataFrame, group_col: str, label_col: str | None = None) -> pd.DataFrame:
    if events.empty or group_col not in events.columns:
        return pd.DataFrame(columns=["Label", "Hours"])
    frame = events.copy()
    key = frame[group_col].map(lambda v: _clean_str(v) or "Unassigned")
    if label_col and label_col in frame.columns:
        extra = frame[label_col].map(lambda v: _clean_str(v) or "")
        label = [
            f"{item}  ·  {text}" if text and text != item else item
            for item, text in zip(key, extra)
        ]
    else:
        label = key
    grouped = (
        pd.DataFrame({"Label": label, "Hours": frame["Hours"]})
        .groupby("Label", as_index=False)["Hours"]
        .sum()
        .sort_values("Hours", ascending=False)
        .reset_index(drop=True)
    )
    return grouped


def hours_fillcolors(step_ids: list[str], hours_by_step: dict[str, float]) -> dict[str, str]:
    """Light → dark red by share of mapped hours. Zero-hour steps stay grey."""
    total = sum(hours_by_step.get(sid, 0.0) for sid in step_ids)
    empty = "#F2F4F7"
    low = (254, 237, 236)
    high = (136, 16, 29)
    fills: dict[str, str] = {}
    for sid in step_ids:
        hours = hours_by_step.get(sid, 0.0)
        if total <= 0 or hours <= 0:
            fills[sid] = empty
            continue
        share = hours / total
        rgb = tuple(int(low[i] + (high[i] - low[i]) * share) for i in range(3))
        fills[sid] = f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"
    return fills


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


def line_color_map(lines: list[str]) -> dict[str, str]:
    colors = {}
    for i, line in enumerate(lines):
        colors[line] = LINE_COLORS[i % len(LINE_COLORS)]
    return colors


def _stub_id(prefix: str, text: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    return f"{prefix}_{safe or 'unknown'}"


def build_line_graph(
    steps: pd.DataFrame,
    selected_step: str | None = None,
    all_steps: pd.DataFrame | None = None,
    rankdir: str = "LR",
    step_fillcolors: dict[str, str] | None = None,
    highlight_selected: bool = True,
) -> Digraph:
    chart = Digraph("line_flow")
    chart.attr(rankdir=rankdir, splines="spline", nodesep="0.45", ranksep="0.70")
    chart.attr(
        "node",
        shape="box",
        style="rounded,filled",
        fontname="Helvetica",
        fontsize="14",
        color="#8A93A0",
        fillcolor="#FFFFFF",
    )
    chart.attr("edge", color="#5C6670", arrowsize="0.8")
    chart.attr("graph", fontname="Helvetica", fontsize="13", bgcolor="white")

    if steps.empty:
        chart.node("empty", "No steps for the selected line(s)")
        return chart

    lookup = all_steps if all_steps is not None else steps
    line_by_id = {row["StepID"]: row["Line"] for _, row in lookup.iterrows()}

    known = set(steps["StepID"])
    zones = list(dict.fromkeys(steps["Zone"].tolist()))
    line_order = list(dict.fromkeys(steps["Line"].tolist()))
    fills = line_color_map(line_order)

    for i, zone in enumerate(zones):
        with chart.subgraph(name=f"cluster_{i}") as cluster:
            cluster.attr(
                label=gv_escape(zone, 60),
                style="rounded,filled",
                color="#D0D5DD",
                fillcolor="#FFFFFF",
                fontsize="13",
            )
            zone_steps = steps[steps["Zone"] == zone]
            for _, row in zone_steps.iterrows():
                step_id = row["StepID"]
                label = f"{step_id}\\n{gv_escape(row['Process'], 40)}"
                if step_fillcolors is not None:
                    fill = step_fillcolors.get(step_id, "#F2F4F7")
                else:
                    fill = fills.get(row["Line"], "#FFFFFF")
                attrs = {"fillcolor": fill}
                if highlight_selected and step_id == selected_step:
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
    """Exact Graphviz render when `dot` is installed; otherwise None."""
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


def _save_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def export_line_image(
    steps: pd.DataFrame,
    selected_step: str | None = None,
    all_steps: pd.DataFrame | None = None,
    rankdir: str = "LR",
) -> bytes:
    """PNG of the current line selection, colors, highlight, and orientation."""
    box_w, box_h = 210, 86
    gap, pad, header_h = 26, 28, 26
    lookup = all_steps if all_steps is not None else steps
    line_by_id = {row["StepID"]: row["Line"] for _, row in lookup.iterrows()}
    known = set(steps["StepID"]) if not steps.empty else set()
    line_order = list(dict.fromkeys(steps["Line"].tolist())) if not steps.empty else []
    fills = line_color_map(line_order)

    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for line in line_order:
        cards: list[dict[str, Any]] = []
        line_steps = steps[steps["Line"] == line]
        for _, row in line_steps.iterrows():
            cards.append(
                {
                    "id": row["StepID"],
                    "title": str(row["StepID"]),
                    "subtitle": str(row["Process"]),
                    "zone": str(row["Zone"]),
                    "line": line,
                    "selected": row["StepID"] == selected_step,
                    "stub": False,
                }
            )
        seen_stubs: set[str] = set()
        for _, row in line_steps.iterrows():
            for dest in row["NextIDs"]:
                if dest in known:
                    continue
                if dest == "Next station":
                    key, title, subtitle = "Next station", "Next station", ""
                else:
                    dest_line = line_by_id.get(dest)
                    key = dest_line or dest
                    title = "Next line" if dest_line else dest
                    subtitle = dest_line or dest
                if key in seen_stubs:
                    continue
                seen_stubs.add(key)
                cards.append(
                    {
                        "id": key,
                        "title": title,
                        "subtitle": subtitle,
                        "zone": "",
                        "line": line,
                        "selected": False,
                        "stub": True,
                    }
                )
        groups.append((line, cards))

    if not groups:
        image = Image.new("RGB", (480, 120), "#FFFFFF")
        ImageDraw.Draw(image).text((20, 48), "No steps for the selected line(s)", fill="#344054", font=_load_font(16))
        return _save_png(image)

    max_n = max(len(cards) for _, cards in groups)
    n_lines = len(groups)
    vertical = rankdir == "TB"
    if vertical:
        width = pad + n_lines * (box_w + gap) + pad
        height = pad + header_h + max_n * (box_h + gap) + pad
    else:
        width = pad + max_n * (box_w + gap) + pad
        height = pad + n_lines * (header_h + box_h + gap) + pad

    image = Image.new("RGB", (width, height), "#FFFFFF")
    draw = ImageDraw.Draw(image)
    title_font = _load_font(15)
    body_font = _load_font(12)
    zone_font = _load_font(11)
    positions: dict[str, tuple[int, int, int, int]] = {}

    for line_i, (line, cards) in enumerate(groups):
        if vertical:
            x0 = pad + line_i * (box_w + gap)
            draw.text((x0, pad), line[:36], fill="#344054", font=zone_font)
        else:
            y_header = pad + line_i * (header_h + box_h + gap)
            draw.text((pad, y_header), line[:60], fill="#344054", font=zone_font)
        last_zone = None
        for card_i, card in enumerate(cards):
            if vertical:
                x = pad + line_i * (box_w + gap)
                y = pad + header_h + card_i * (box_h + gap)
            else:
                x = pad + card_i * (box_w + gap)
                y = pad + line_i * (header_h + box_h + gap) + header_h
            positions[card["id"]] = (x, y, x + box_w, y + box_h)
            if card["zone"] and card["zone"] != last_zone and not vertical:
                last_zone = card["zone"]
            fill = "#C8102E" if card["selected"] else ("#FFFFFF" if card["stub"] else fills.get(card["line"], "#E8F1F8"))
            outline = "#8E0B20" if card["selected"] else "#98A2B3"
            draw.rounded_rectangle((x, y, x + box_w, y + box_h), radius=10, fill=fill, outline=outline, width=2)
            ink = "white" if card["selected"] else "#1D2433"
            draw.text((x + 10, y + 8), card["title"][:26], fill=ink, font=title_font)
            for line_i2, text in enumerate(wrap_words(card["subtitle"], 24)[:3]):
                draw.text((x + 10, y + 32 + line_i2 * 15), text, fill=ink, font=body_font)

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
            x1, y1, x2, y2 = positions[src]
            tx1, ty1, tx2, ty2 = positions[target]
            if vertical:
                ax, ay = (x1 + x2) // 2, y2
                bx, by = (tx1 + tx2) // 2, ty1
            else:
                ax, ay = x2, (y1 + y2) // 2
                bx, by = tx1, (ty1 + ty2) // 2
            draw.line((ax, ay, bx, by), fill="#5C6670", width=2)

    return _save_png(image)


def export_tree_image(tree: pd.DataFrame, current_id: str | None) -> bytes:
    """PNG of the troubleshooting tree with full wrapped prompts."""
    if tree.empty:
        image = Image.new("RGB", (400, 120), "#FFFFFF")
        ImageDraw.Draw(image).text((20, 48), "No tree", fill="#344054", font=_load_font(16))
        return _save_png(image)

    colors = {"Symptom": "#F8EAEA", "Question": "#E8F1F8", "Action": "#EAF4EA"}
    nodes: dict[str, dict[str, Any]] = {}
    for _, row in tree.iterrows():
        node_id = row["ID"]
        if not _clean_str(node_id):
            continue
        prompt_lines = wrap_words(row.get("Prompt"), 36)
        nodes[node_id] = {
            "id": node_id,
            "kind": row.get("Type2") or "",
            "lines": [f"{node_id}  ·  {row.get('Type2') or ''}"] + prompt_lines,
            "yes": _clean_str(row.get("If yes")),
            "no": _clean_str(row.get("If no")),
            "stop": _clean_str(row.get("Stop")) == "Y",
        }

    children: dict[str, list[str]] = defaultdict(list)
    incoming: set[str] = set()
    for node in nodes.values():
        for nxt in (node["yes"], node["no"]):
            if nxt and nxt in nodes and nxt not in children[node["id"]]:
                children[node["id"]].append(nxt)
                incoming.add(nxt)
    roots = [nid for nid in nodes if nid not in incoming] or list(nodes.keys())[:1]

    depth: dict[str, int] = {}
    queue = deque((rid, 0) for rid in roots)
    while queue:
        nid, d = queue.popleft()
        if nid in depth and depth[nid] <= d:
            continue
        depth[nid] = d
        for child in children.get(nid, []):
            queue.append((child, d + 1))

    layers: dict[int, list[str]] = defaultdict(list)
    seen: set[str] = set()
    for nid, d in sorted(depth.items(), key=lambda item: item[1]):
        if nid not in seen:
            layers[d].append(nid)
            seen.add(nid)
    for nid in nodes:
        if nid not in seen:
            layers[max(layers) + 1 if layers else 0].append(nid)

    box_w = 268
    line_h = 15
    pad_y = 10
    gap_x, gap_y, pad = 28, 24, 24
    heights = {
        nid: pad_y * 2 + line_h * max(len(nodes[nid]["lines"]), 1)
        for nid in nodes
    }
    max_row = max((len(row) for row in layers.values()), default=1)
    max_h = max(heights.values()) if heights else 80
    width = pad + max_row * (box_w + gap_x) + pad
    height = pad + sum(max_h + gap_y for _ in layers) + pad
    image = Image.new("RGB", (width, height), "#FFFFFF")
    draw = ImageDraw.Draw(image)
    font = _load_font(12)
    header_font = _load_font(13)
    positions: dict[str, tuple[int, int, int, int]] = {}

    y = pad
    for d in sorted(layers):
        row = layers[d]
        row_h = max(heights[nid] for nid in row)
        x = pad + max((max_row - len(row)) * (box_w + gap_x) // 2, 0)
        for nid in row:
            h = heights[nid]
            positions[nid] = (x, y, x + box_w, y + h)
            x += box_w + gap_x
        y += row_h + gap_y

    for nid, node in nodes.items():
        if nid not in positions:
            continue
        x1, y1, x2, y2 = positions[nid]
        selected = nid == current_id
        fill = "#C8102E" if selected else colors.get(node["kind"], "#FFFFFF")
        outline = "#8E0B20" if selected else "#98A2B3"
        width_line = 3 if node["stop"] else 2
        draw.rounded_rectangle((x1, y1, x2, y2), radius=8, fill=fill, outline=outline, width=width_line)
        ink = "white" if selected else "#1D2433"
        for i, text in enumerate(node["lines"]):
            draw.text((x1 + 8, y1 + pad_y + i * line_h), text[:42], fill=ink, font=header_font if i == 0 else font)

    for nid, node in nodes.items():
        if nid not in positions:
            continue
        x1, y1, x2, y2 = positions[nid]
        sx, sy = (x1 + x2) // 2, y2
        for label, dest in (("yes", node["yes"]), ("no", node["no"])):
            if not dest or dest == node["yes"] and label == "no":
                continue
            if dest not in positions:
                continue
            if label == "no" and dest == node["yes"]:
                continue
            tx1, ty1, tx2, ty2 = positions[dest]
            dx, dy = (tx1 + tx2) // 2, ty1
            draw.line((sx, sy, dx, dy), fill="#5C6670", width=2)
            if node["yes"] and node["no"] and node["yes"] != node["no"]:
                mx, my = (sx + dx) // 2, (sy + dy) // 2
                draw.text((mx + 4, my - 10), label, fill="#5C6670", font=font)

    return _save_png(image)


def build_sipoc_graph(step_row: pd.Series, sipoc: pd.DataFrame) -> Digraph:
    chart = Digraph("sipoc")
    chart.attr(rankdir="LR", splines="spline", nodesep="0.4", ranksep="0.8", bgcolor="white")
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
        chart.node("none", "No SIPOC rows for this step", fillcolor="#FFFFFF")
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
    chart.attr(rankdir="TB", splines="spline", nodesep="0.30", ranksep="0.42", bgcolor="white")
    chart.attr("node", shape="box", style="rounded,filled", fontname="Helvetica", fontsize="8")
    chart.attr("edge", color="#5C6670", arrowsize="0.6", fontsize="8")

    if tree.empty:
        chart.node("empty", "No tree")
        return chart

    colors = {"Symptom": "#F8EAEA", "Question": "#E8F1F8", "Action": "#EAF4EA"}
    for _, row in tree.iterrows():
        node_id = row["ID"]
        kind = row.get("Type2") or ""
        prompt = gv_wrap(row.get("Prompt"), 42)
        header = f"{node_id}  ·  {gv_escape(kind, 24)}"
        label = f"{header}\\n{prompt}" if prompt else header
        attrs = {"fillcolor": colors.get(kind, "#FFFFFF")}
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
