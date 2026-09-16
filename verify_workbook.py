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

DEFAULT_WORKBOOK = Path("/home/workdir/attachments/Processes.xlsx")


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
    assert len(model.controls_for_step("B-P-01-01")) == 6
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
    print("verify_workbook: ok")


if __name__ == "__main__":
    main()
