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


# Workbook headers the plant file has used. Incoming names on the left
# become the canonical names the app reads. Matching is case-insensitive.
#
# "Control" is the process-sheet variable name. On PFMEA / PM / tree it is
# the ControlID foreign key — do not apply PROCESS_SHEET_ALIASES there.
PROCESS_SHEET_ALIASES = {
    "M": "M Category",
    "M Category": "M Category",
    "Control": "Variable",
    "Variable": "Variable",
    "How set": "Rationale",
    "Rationale": "Rationale",
}

COLUMN_ALIASES = {
    **PROCESS_SHEET_ALIASES,
    "FmeaID": "PFMEAID",
    "FMEAID": "PFMEAID",
    "FMEA ID": "PFMEAID",
    "PFMEA ID": "PFMEAID",
    "PFMEAID": "PFMEAID",
    "Component": "Asset",
    "Asset": "Asset",
    "Next Step ID": "Next Step ID",
    "NextStepID": "Next Step ID",
    "Next StepID": "Next Step ID",
    "Flow type": "Flow type",
    "Flow Type": "Flow type",
    "Type2": "Type2",
    "Node type": "Type2",
    "Node Type": "Type2",
}

CANONICAL_SHEETS = [
    "Structure Overview",
    "Process Map",
    "SIPOC",
    "Process Sheet",
    "PFMEA",
    "Troubleshooting Guide",
    "Work Instructions",
    "Maintenance PM",
    "Lost Time DB",
    "Root Cause",
]

SHEET_ALIASES = {
    "structure overview": "Structure Overview",
    "overview": "Structure Overview",
    "process map": "Process Map",
    "processmap": "Process Map",
    "map": "Process Map",
    "sipoc": "SIPOC",
    "process sheet": "Process Sheet",
    "processsheet": "Process Sheet",
    "process controls": "Process Sheet",
    "controls": "Process Sheet",
    "pfmea": "PFMEA",
    "fmea": "PFMEA",
    "troubleshooting guide": "Troubleshooting Guide",
    "troubleshooting": "Troubleshooting Guide",
    "trouble shooting": "Troubleshooting Guide",
    "work instructions": "Work Instructions",
    "work instruction": "Work Instructions",
    "wi": "Work Instructions",
    "maintenance pm": "Maintenance PM",
    "maintenance": "Maintenance PM",
    "pm": "Maintenance PM",
    "lost time db": "Lost Time DB",
    "lost time": "Lost Time DB",
    "root cause": "Root Cause",
}


def _norm_key(name: Any) -> str:
    text = str(name).replace("\u00a0", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def _compact_key(name: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", _norm_key(name))


def _clean_df(df: pd.DataFrame, sheet: str | None = None) -> pd.DataFrame:
    out = df.copy()
    out.columns = [str(c).replace("\u00a0", " ").strip() for c in out.columns]
    out = _apply_column_aliases(out, sheet=sheet)
    for col in out.columns:
        if out[col].dtype == object:
            out[col] = out[col].map(lambda v: v.strip() if isinstance(v, str) else v)
    return out


def _canonical_col(name: Any, sheet: str | None = None) -> str:
    text = str(name).replace("\u00a0", " ").strip()
    compact = _compact_key(text)
    if compact == "controlid":
        return "ControlID"
    if sheet != "Process Sheet" and compact == "control":
        return "ControlID"
    aliases = PROCESS_SHEET_ALIASES if sheet == "Process Sheet" else COLUMN_ALIASES
    if sheet == "Process Sheet":
        aliases = {**COLUMN_ALIASES, **PROCESS_SHEET_ALIASES}
    alias_by_norm = {_norm_key(old): new for old, new in aliases.items()}
    alias_by_compact = {_compact_key(old): new for old, new in aliases.items()}
    return alias_by_norm.get(_norm_key(text), alias_by_compact.get(compact, text))


def _apply_column_aliases(df: pd.DataFrame, sheet: str | None = None) -> pd.DataFrame:
    """Rename known old or differently cased headers to the canonical names."""
    mapping = {}
    have = set(df.columns)
    sheet_aliases = PROCESS_SHEET_ALIASES if sheet == "Process Sheet" else {
        key: value for key, value in COLUMN_ALIASES.items() if key not in PROCESS_SHEET_ALIASES
    }
    if sheet == "Process Sheet":
        sheet_aliases = {**COLUMN_ALIASES, **PROCESS_SHEET_ALIASES}
    alias_by_norm = {_norm_key(old): new for old, new in sheet_aliases.items()}
    alias_by_compact = {_compact_key(old): new for old, new in sheet_aliases.items()}
    for col in df.columns:
        compact = _compact_key(col)
        if compact == "controlid" or (sheet != "Process Sheet" and compact == "control"):
            if col != "ControlID" and "ControlID" not in have:
                mapping[col] = "ControlID"
                have.add("ControlID")
            continue
        target = alias_by_norm.get(_norm_key(col)) or alias_by_compact.get(compact)
        if target and target != col and target not in have:
            mapping[col] = target
            have.add(target)
    return df.rename(columns=mapping) if mapping else df


def _control_id_column(frame: pd.DataFrame) -> str | None:
    if frame is None:
        return None
    for name in frame.columns:
        if _compact_key(name) == "controlid":
            return str(name)
    return None


def _canonical_sheet_name(name: Any) -> str:
    raw = str(name).replace("\u00a0", " ").strip()
    keyed = _norm_key(raw)
    compact = keyed.replace(" ", "")
    if keyed in SHEET_ALIASES:
        return SHEET_ALIASES[keyed]
    if compact in SHEET_ALIASES:
        return SHEET_ALIASES[compact]
    for canon in CANONICAL_SHEETS:
        if _norm_key(canon) == keyed or _norm_key(canon).replace(" ", "") == compact:
            return canon
    return raw


def _read_workbook_sheets(file_obj) -> dict[str, pd.DataFrame]:
    """Read every sheet from a path, buffer, bytes, or Streamlit upload."""
    payload: bytes | None
    if isinstance(file_obj, (bytes, bytearray)):
        payload = bytes(file_obj)
    elif hasattr(file_obj, "getvalue"):
        payload = file_obj.getvalue()
    elif hasattr(file_obj, "read"):
        if hasattr(file_obj, "seek"):
            try:
                file_obj.seek(0)
            except Exception:
                pass
        payload = file_obj.read()
        if hasattr(file_obj, "seek"):
            try:
                file_obj.seek(0)
            except Exception:
                pass
    else:
        payload = None

    if payload is not None:
        if not payload:
            raise ValueError("Workbook file is empty.")
        source: Any = io.BytesIO(payload)
    else:
        source = file_obj

    try:
        raw = pd.read_excel(source, sheet_name=None, engine="openpyxl")
    except Exception as exc:
        raise ValueError(f"Could not parse the .xlsx file ({exc}).") from exc

    sheets: dict[str, pd.DataFrame] = {}
    for name, frame in (raw or {}).items():
        canon = _canonical_sheet_name(name)
        if canon not in sheets or sheets[canon].empty:
            sheets[canon] = frame
    return sheets


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
    raw_sheets: dict[str, pd.DataFrame] | None = None

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
        if not control_id or self.pfmea.empty:
            return self.pfmea.iloc[0:0]
        column = _control_id_column(self.pfmea)
        if not column:
            return self.pfmea.iloc[0:0]
        hits = self.pfmea[self.pfmea[column] == control_id]
        return hits.reset_index(drop=True)

    def pm_for_control(self, control_id: str | None) -> pd.DataFrame:
        if not control_id or self.maintenance_pm.empty:
            return self.maintenance_pm.iloc[0:0]
        column = _control_id_column(self.maintenance_pm)
        if not column:
            return self.maintenance_pm.iloc[0:0]
        hits = self.maintenance_pm[self.maintenance_pm[column] == control_id]
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
        if "_step_tokens" in events.columns:
            wanted = set(self.steps_for_lines(lines)["StepID"])
            mask = events["_step_tokens"].map(lambda tokens: bool(wanted.intersection(tokens)))
            return events.loc[mask].reset_index(drop=True)
        mask = events["Line"].isin(lines)
        return events.loc[mask].reset_index(drop=True)

    def lost_time_for_step(self, events: pd.DataFrame, step_id: str) -> pd.DataFrame:
        if events.empty or not step_id:
            return events.iloc[0:0]
        if "_step_tokens" in events.columns:
            mask = events["_step_tokens"].map(lambda tokens: step_id in tokens)
            return events.loc[mask].reset_index(drop=True)
        return events.loc[events["StepID"] == step_id].reset_index(drop=True)

    def lost_time_unmapped(self, events: pd.DataFrame) -> pd.DataFrame:
        if events.empty:
            return events.iloc[0:0]
        known = set(self.process_map["StepID"])
        if "_step_tokens" in events.columns:
            mapped = events["_step_tokens"].map(lambda tokens: any(t in known for t in tokens))
            return events.loc[~mapped].reset_index(drop=True)
        mapped = events["StepID"].isin(known)
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
    sheets = _read_workbook_sheets(file_obj)
    model = _build_model(sheets, source_name=source_name)
    model.raw_sheets = {name: frame.copy() for name, frame in sheets.items()}
    return model


def _build_model(sheets: dict[str, pd.DataFrame], source_name: str = "upload") -> ProcessData:
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
        found = ", ".join(sheets.keys()) or "none"
        raise ValueError(
            "Workbook is missing sheets: "
            + ", ".join(missing)
            + f". Found: {found}."
        )

    required_cols = {
        "Process Map": ["StepID", "Next Step ID", "Line", "Zone", "Process", "Definition"],
        "SIPOC": ["StepID", "Flow type", "Item"],
        "Process Sheet": ["StepID"],
        "PFMEA": ["ControlID"],
        "Troubleshooting Guide": ["ID", "Type2", "Prompt", "If yes", "If no"],
        "Work Instructions": ["WI_ID", "Title", "File", "StepID"],
    }
    problems = []
    for sheet, cols in required_cols.items():
        have = {_canonical_col(c, sheet=sheet) for c in sheets[sheet].columns}
        miss = [c for c in cols if c not in have]
        if miss:
            problems.append(f"{sheet}: {', '.join(miss)}")
    if problems:
        raise ValueError("Workbook is missing columns: " + "; ".join(problems))

    overview = _clean_df(sheets.get("Structure Overview", pd.DataFrame()), sheet="Structure Overview")
    process_map = _clean_df(sheets["Process Map"], sheet="Process Map")
    sipoc = _clean_df(sheets["SIPOC"], sheet="SIPOC")
    process_sheet = _clean_df(sheets["Process Sheet"], sheet="Process Sheet")
    pfmea = _clean_df(sheets["PFMEA"], sheet="PFMEA")
    troubleshooting = _clean_df(sheets["Troubleshooting Guide"], sheet="Troubleshooting Guide")
    work_instructions = _clean_df(sheets["Work Instructions"], sheet="Work Instructions")
    maintenance_pm = _clean_df(sheets["Maintenance PM"], sheet="Maintenance PM")

    process_map["Line"] = process_map["Line"].map(lambda v: _clean_str(v) or "")
    process_map["Zone"] = process_map["Zone"].map(lambda v: _clean_str(v) or "")
    process_map["StepID"] = process_map["StepID"].map(lambda v: _clean_str(v) or "")
    process_map["NextIDs"] = process_map["Next Step ID"].map(_resolve_next_ids)

    sipoc["_step_tokens"] = sipoc["StepID"].map(split_ids)
    if "Flow type" in sipoc.columns:
        sipoc["Flow type"] = sipoc["Flow type"].map(lambda v: (_clean_str(v) or "").title())
    if "CTQ" in sipoc.columns:
        sipoc["CTQ"] = sipoc["CTQ"].map(_as_yes_no)

    if "ControlID" in process_sheet.columns:
        process_sheet["ControlID"] = process_sheet["ControlID"].map(_clean_str)
    process_sheet["StepID"] = process_sheet["StepID"].map(_clean_str)

    if "ControlID" in pfmea.columns:
        pfmea["ControlID"] = pfmea["ControlID"].map(_clean_str)
        pfmea = pfmea[pfmea["ControlID"].notna()].reset_index(drop=True)
    if "PFMEAID" in pfmea.columns:
        pfmea["PFMEAID"] = pfmea["PFMEAID"].map(_clean_str)

    troubleshooting["ID"] = troubleshooting["ID"].map(_clean_str)
    troubleshooting["Type2"] = troubleshooting["Type2"].map(_clean_str)
    if "StepID" in troubleshooting.columns:
        troubleshooting["_step_tokens"] = troubleshooting["StepID"].map(split_ids)
    else:
        troubleshooting["_step_tokens"] = [[] for _ in range(len(troubleshooting))]

    if "StepID" in work_instructions.columns:
        work_instructions["_step_tokens"] = work_instructions["StepID"].map(split_ids)
    else:
        work_instructions["_step_tokens"] = [[] for _ in range(len(work_instructions))]
    if "ControlID" in maintenance_pm.columns:
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
        raw_sheets=None,
    )


STEP_ID_RE = re.compile(r"^(.+)-(\d+)-(\d+)$")
CONTROL_ID_RE = re.compile(r"^PS-(.+)-(\d+)$")


def step_families(process_map: pd.DataFrame) -> list[str]:
    seen: list[str] = []
    for sid in process_map.get("StepID", pd.Series(dtype=str)).tolist():
        text = _clean_str(sid)
        if not text:
            continue
        match = STEP_ID_RE.match(text)
        family = match.group(1) if match else None
        if family and family not in seen:
            seen.append(family)
    return seen


def suggest_step_id(process_map: pd.DataFrame, family: str) -> str:
    family = (family or "").strip()
    if not family:
        family = "B-P"
    max_pair = (0, 0)
    found = False
    pat = re.compile(rf"^{re.escape(family)}-(\d+)-(\d+)$")
    for sid in process_map.get("StepID", pd.Series(dtype=str)).tolist():
        text = _clean_str(sid)
        if not text:
            continue
        match = pat.match(text)
        if match:
            found = True
            pair = (int(match.group(1)), int(match.group(2)))
            if pair > max_pair:
                max_pair = pair
    if not found:
        return f"{family}-01-01"
    return f"{family}-{max_pair[0]:02d}-{max_pair[1] + 1:02d}"


def next_control_id(step_id: str, process_sheet: pd.DataFrame) -> str:
    step_id = _clean_str(step_id) or ""
    prefix = f"PS-{step_id}-"
    max_n = 0
    series = process_sheet["ControlID"] if "ControlID" in process_sheet.columns else pd.Series(dtype=str)
    for cid in series.tolist():
        text = _clean_str(cid)
        if text and text.startswith(prefix):
            tail = text[len(prefix) :]
            if tail.isdigit():
                max_n = max(max_n, int(tail))
    return f"{prefix}{max_n + 1:02d}"


def next_fmea_id(control_id: str, pfmea: pd.DataFrame) -> str:
    control_id = _clean_str(control_id) or ""
    stem = control_id[3:] if control_id.startswith("PS-") else control_id
    prefix = f"F-{stem}-"
    max_n = 0
    series = pfmea["PFMEAID"] if "PFMEAID" in pfmea.columns else pd.Series(dtype=str)
    for fid in series.tolist():
        text = _clean_str(fid)
        if text and text.startswith(prefix):
            tail = text[len(prefix) :]
            if tail.isdigit():
                max_n = max(max_n, int(tail))
    return f"{prefix}{max_n + 1:02d}"


def working_frames(model: ProcessData) -> dict[str, pd.DataFrame]:
    """Editable copies without helper columns."""
    process_map = model.process_map.copy()
    if "NextIDs" in process_map.columns:
        if "Next Step ID" not in process_map.columns:
            process_map["Next Step ID"] = process_map["NextIDs"].map(
                lambda ids: ", ".join(ids) if isinstance(ids, list) else ""
            )
        process_map = process_map.drop(columns=["NextIDs"])
    sipoc = model.sipoc.copy()
    if "_step_tokens" in sipoc.columns:
        sipoc["StepID"] = sipoc["_step_tokens"].map(
            lambda tokens: ", ".join(tokens) if isinstance(tokens, list) else (_clean_str(tokens) or "")
        )
        sipoc = sipoc.drop(columns=["_step_tokens"])
    process_sheet = model.process_sheet.copy()
    pfmea = model.pfmea.copy()
    return {
        "Process Map": process_map.reset_index(drop=True),
        "SIPOC": sipoc.reset_index(drop=True),
        "Process Sheet": process_sheet.reset_index(drop=True),
        "PFMEA": pfmea.reset_index(drop=True),
    }


def rebuild_model_from_working(
    raw_sheets: dict[str, pd.DataFrame],
    working: dict[str, pd.DataFrame],
    source_name: str,
) -> ProcessData:
    merged = {name: frame.copy() for name, frame in (raw_sheets or {}).items()}
    for name, frame in working.items():
        merged[name] = frame.copy()
    model = _build_model(merged, source_name=source_name)
    model.raw_sheets = {name: frame.copy() for name, frame in (raw_sheets or {}).items()}
    return model


EXPORT_RENAMES = {
    "Process Sheet": {
        "M": "M Category",
        "Control": "Variable",
        "How set": "Rationale",
    },
    "PFMEA": {
        "FMEAID": "PFMEAID",
        "FmeaID": "PFMEAID",
        "FMEA ID": "PFMEAID",
        "Frequency ": "Frequency",
        "Component": "Asset",
    },
}


def _export_frame(sheet_name: str, frame: pd.DataFrame, original_columns: list[str] | None) -> pd.DataFrame:
    out = frame.copy()
    if sheet_name == "Process Map" and "NextIDs" in out.columns:
        out["Next Step ID"] = out["NextIDs"].map(lambda ids: ", ".join(ids) if isinstance(ids, list) else "")
        out = out.drop(columns=["NextIDs"])
    if sheet_name == "SIPOC" and "_step_tokens" in out.columns:
        out["StepID"] = out["_step_tokens"].map(
            lambda tokens: ", ".join(tokens) if isinstance(tokens, list) else (_clean_str(tokens) or "")
        )
        out = out.drop(columns=["_step_tokens"])
    renames = EXPORT_RENAMES.get(sheet_name, {})
    have = set(out.columns)
    mapping = {old: new for old, new in renames.items() if old in have and new not in have}
    if mapping:
        out = out.rename(columns=mapping)
    if original_columns:
        ordered = [col for col in original_columns if col in out.columns]
        extras = [col for col in out.columns if col not in ordered]
        out = out[ordered + extras]
    return out


def export_workbook(
    raw_sheets: dict[str, pd.DataFrame],
    working: dict[str, pd.DataFrame],
) -> bytes:
    """Rebuild an .xlsx: edited cascade sheets from the working copy, others untouched."""
    payload = io.BytesIO()
    original_headers = {name: [str(c) for c in frame.columns] for name, frame in (raw_sheets or {}).items()}
    with pd.ExcelWriter(payload, engine="openpyxl") as writer:
        names = list(raw_sheets.keys()) if raw_sheets else list(working.keys())
        for name in working:
            if name not in names:
                names.append(name)
        for name in names:
            if name in working:
                frame = _export_frame(name, working[name], original_headers.get(name))
            else:
                frame = raw_sheets[name]
            sheet = name[:31] if name else "Sheet"
            frame.to_excel(writer, sheet_name=sheet, index=False)
    return payload.getvalue()


def remove_step_id_from_next(process_map: pd.DataFrame, step_id: str) -> pd.DataFrame:
    out = process_map.copy()
    if "Next Step ID" not in out.columns:
        return out

    def _drop(value: Any) -> str:
        kept = [item for item in split_ids(value) if item != step_id]
        return ", ".join(kept)

    out["Next Step ID"] = out["Next Step ID"].map(_drop)
    return out


def sipoc_without_step(sipoc: pd.DataFrame, step_id: str) -> pd.DataFrame:
    out = sipoc.copy()
    if out.empty or "StepID" not in out.columns:
        return out
    kept_rows = []
    for _, row in out.iterrows():
        tokens = [item for item in split_ids(row.get("StepID")) if item != step_id]
        if not tokens:
            continue
        new_row = row.copy()
        new_row["StepID"] = ", ".join(tokens)
        kept_rows.append(new_row)
    if not kept_rows:
        return out.iloc[0:0]
    return pd.DataFrame(kept_rows).reset_index(drop=True)


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
                "PFMEAID",
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
    events = _clean_df(raw, sheet="Lost Time DB")
    hours_col = "Lost Time" if "Lost Time" in events.columns else None
    if hours_col is None or "Date" not in events.columns:
        return _prepare_lost_time(pd.DataFrame(), process_map, pfmea)

    events["Date"] = pd.to_datetime(events["Date"], errors="coerce")
    events["Hours"] = events[hours_col].map(_coerce_hours)
    events = events[events["Date"].notna() & events["Hours"].notna()].copy()
    if events.empty:
        return _prepare_lost_time(pd.DataFrame(), process_map, pfmea)

    events["StepID"] = events["StepID"].map(_clean_str) if "StepID" in events.columns else None
    events["_step_tokens"] = events["StepID"].map(split_ids)
    events["PFMEAID"] = events["PFMEAID"].map(_clean_str) if "PFMEAID" in events.columns else None
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
    known = set(map_cols["StepID"])
    events["_join_step"] = events["_step_tokens"].map(
        lambda tokens: next((t for t in tokens if t in known), None)
    )
    events = events.merge(map_cols, left_on="_join_step", right_on="StepID", how="left", suffixes=("", "_map"))
    if "StepID_map" in events.columns:
        events["StepID"] = events["StepID"].fillna(events["StepID_map"])
        events = events.drop(columns=["StepID_map"])
    events = events.drop(columns=["_join_step"])

    fmea_keep = [c for c in ("PFMEAID", "ControlID", "Failure mode") if c in pfmea.columns]
    if not pfmea.empty and "PFMEAID" in pfmea.columns:
        fmea_cols = pfmea[fmea_keep].drop_duplicates("PFMEAID")
    else:
        fmea_cols = pd.DataFrame(columns=["PFMEAID", "ControlID", "Failure mode"])
    if "PFMEAID" not in events.columns:
        events["PFMEAID"] = None
    events = events.merge(fmea_cols, on="PFMEAID", how="left")
    if "Failure mode" not in events.columns:
        events["Failure mode"] = None
    if "ControlID" not in events.columns:
        events["ControlID"] = None

    keep = [
        "Date",
        "StepID",
        "PFMEAID",
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
        "_step_tokens",
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
