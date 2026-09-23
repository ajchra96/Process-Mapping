"""Offline checks used before deploy. Does not need the Graphviz binary."""

from __future__ import annotations

from collections import deque
import sys
from pathlib import Path

from data import (
    _clean_str,
    build_line_graph,
    build_sipoc_graph,
    build_tree_graph,
    load_workbook,
)

DEFAULT_WORKBOOK = Path(__file__).resolve().parents[1] / "Processes.xlsx"


def walk_tree(model, start_id: str) -> set[str]:
    by_id = model.ts_by_id()
    seen: set[str] = set()
    queue = deque([start_id])
    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        node = by_id.get(current)
        if node is None:
            raise AssertionError(f"Walk hit missing node {current}")
        for col in ("If yes", "If no"):
            nxt = _clean_str(node.get(col))
            if nxt:
                queue.append(nxt)
    return seen


def main() -> None:
    workbook = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_WORKBOOK
    model = load_workbook(workbook, source_name="verify")
    assert model.lines() == [
        "Bosch - Mogul HLM 35 RS",
        "Plant logistics / drying rooms",
        "Bosch - Starch Dryer TF 8000",
    ], model.lines()
    assert len(model.process_map) == 29
    assert model.process_map["StepID"].is_unique
    assert "B-TP-07-01" not in set(model.process_map["StepID"])
    drying = model.process_map.loc[model.process_map["StepID"] == "B-TP-06-01"].iloc[0]
    assert drying["NextIDs"] == ["B-TP-07-02"]

    sipoc_tokens: set[str] = set()
    for tokens in model.sipoc["_step_tokens"]:
        sipoc_tokens.update(tokens)
    assert "B-TP-07-02" in sipoc_tokens
    assert len(model.sipoc_for_step("B-P-01-01")) == 3
    assert len(model.controls_for_step("B-P-01-01")) == 5
    assert "M Category" in model.process_sheet.columns
    assert "Variable" in model.process_sheet.columns
    assert "Rationale" in model.process_sheet.columns
    assert "PFMEAID" in model.pfmea.columns
    assert "Asset" in model.pfmea.columns
    assert len(model.pfmea_for_control("PS-B-P-01-01-02")) == 4
    assert model.pfmea["ControlID"].notna().all()

    symptoms = model.symptoms()
    assert list(symptoms["ID"]) == ["S1-D"]
    reachable = walk_tree(model, "S1-D")
    assert reachable == set(model.troubleshooting["ID"]), reachable ^ set(model.troubleshooting["ID"])

    for line in model.lines():
        steps = model.steps_for_lines([line])
        assert not steps.empty
        source = build_line_graph(steps, steps["StepID"].iloc[0]).source
        assert "digraph" in source
        assert steps["StepID"].iloc[0] in source

    for step_id in model.process_map["StepID"]:
        row = model.step_row(step_id)
        sipoc = model.sipoc_for_step(step_id)
        source = build_sipoc_graph(row, sipoc).source
        assert "PROCESS" in source

    tree_src = build_tree_graph(model.troubleshooting, "S1-Q-1").source
    assert "S1-Q-1" in tree_src
    assert "Are BOTH sieve drums stopped?" in tree_src
    vertical = build_line_graph(
        model.steps_for_lines(model.lines()),
        model.process_map["StepID"].iloc[0],
        all_steps=model.process_map,
        rankdir="TB",
    ).source
    assert "rankdir=TB" in vertical

    assert "Hours" in model.lost_time.columns
    if not model.lost_time.empty:
        assert model.lost_time["Hours"].gt(0).all()
        assert model.lost_time["Date"].notna().all()
        span = model.date_span()
        assert span is not None
        windowed = model.lost_time_window(span[0], span[1])
        assert len(windowed) == len(model.lost_time)
        heat = build_line_graph(
            model.steps_for_lines(model.lines()),
            selected_step=None,
            all_steps=model.process_map,
            step_fillcolors={model.process_map["StepID"].iloc[0]: "#FEEDEd"},
            highlight_selected=False,
        ).source
        assert "#C8102E" not in heat
        default = build_line_graph(
            model.steps_for_lines([model.lines()[0]]),
            model.steps_for_lines([model.lines()[0]])["StepID"].iloc[0],
            all_steps=model.process_map,
        ).source
        assert "#C8102E" in default
    from data import export_workbook, next_control_id, next_fmea_id, working_frames

    assert next_control_id("B-P-01-01", model.process_sheet) == "PS-B-P-01-01-07"
    assert next_fmea_id("PS-B-P-01-01-02", model.pfmea) == "F-B-P-01-01-02-05"
    payload = export_workbook(model.raw_sheets or {}, working_frames(model))
    assert payload[:2] == b"PK"
    print("verify_workbook: ok")


if __name__ == "__main__":
    main()
