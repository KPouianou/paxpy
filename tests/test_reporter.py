"""Tests for reporter.py."""

from __future__ import annotations

import json

from paxpy.reporter import format_cli, format_json, format_sarif
from paxpy.types import (
    Compatibility,
    CompatibilityResult,
    ConflictReport,
    ConflictType,
    InterferencePath,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_report(
    direction: str = "A_to_B",
    conflict_type: ConflictType = ConflictType.DATA_FLOW,
    tier: int = 1,
    compatibility: Compatibility = Compatibility.INCOMPATIBLE,
    explanation: str = "Type mismatch detected.",
    source_function: str = "foo",
    sink_function: str = "bar",
    source_file: str = "/repo/a.py",
    sink_file: str = "/repo/b.py",
    source_node: str = "/repo/a.py:10:4",
    sink_node: str = "/repo/b.py:20:8",
) -> ConflictReport:
    ip = InterferencePath(
        direction=direction,  # type: ignore[arg-type]
        conflict_type=conflict_type,
        tier=tier,  # type: ignore[arg-type]
        source_node=source_node,
        sink_node=sink_node,
        path_nodes=[source_node, sink_node],
    )
    cr = CompatibilityResult(compatibility=compatibility, explanation=explanation)
    return ConflictReport(
        interference=ip,
        compatibility=cr,
        source_function=source_function,
        sink_function=sink_function,
        source_file=source_file,
        sink_file=sink_file,
    )


# ---------------------------------------------------------------------------
# format_cli
# ---------------------------------------------------------------------------


def test_format_cli_no_conflicts():
    output = format_cli([])
    assert "no semantic conflicts" in output.lower()


def test_format_cli_single_conflict():
    report = make_report()
    output = format_cli([report])
    assert "1 conflict" in output
    assert "foo" in output
    assert "bar" in output


def test_format_cli_shows_direction():
    report = make_report(direction="A_to_B")
    output = format_cli([report])
    assert "A→B" in output


def test_format_cli_shows_b_to_a():
    report = make_report(direction="B_to_A")
    output = format_cli([report])
    assert "B→A" in output


def test_format_cli_shows_tier():
    report = make_report(tier=2)
    output = format_cli([report])
    assert "T2" in output


def test_format_cli_shows_conflict_type():
    report = make_report(conflict_type=ConflictType.CONTROL_DEPENDENCY)
    output = format_cli([report])
    assert "CONTROL" in output


def test_format_cli_shows_compatibility():
    report = make_report(compatibility=Compatibility.SUSPICIOUS)
    output = format_cli([report])
    assert "SUSPICIOUS" in output.upper()


def test_format_cli_multiple_conflicts():
    reports = [make_report(), make_report(conflict_type=ConflictType.CONFLUENCE)]
    output = format_cli(reports)
    assert "2 conflict" in output
    assert "#1" in output
    assert "#2" in output


def test_format_cli_shows_explanation():
    report = make_report(explanation="int returned, subscript used at sink.")
    output = format_cli([report])
    assert "subscript" in output


def test_format_cli_returns_string():
    assert isinstance(format_cli([]), str)


# ---------------------------------------------------------------------------
# format_json
# ---------------------------------------------------------------------------


def test_format_json_structure():
    result = format_json([])
    assert "version" in result
    assert "conflict_count" in result
    assert "conflicts" in result


def test_format_json_no_conflicts():
    result = format_json([])
    assert result["conflict_count"] == 0
    assert result["conflicts"] == []


def test_format_json_single_conflict():
    report = make_report()
    result = format_json([report])
    assert result["conflict_count"] == 1
    conflict = result["conflicts"][0]
    assert conflict["direction"] == "A_to_B"
    assert conflict["conflict_type"] == "DATA_FLOW"
    assert conflict["tier"] == 1
    assert conflict["source_function"] == "foo"
    assert conflict["sink_function"] == "bar"
    assert conflict["compatibility"] == "incompatible"


def test_format_json_is_json_serialisable():
    reports = [make_report(), make_report(compatibility=Compatibility.UNKNOWN)]
    result = format_json(reports)
    # Should not raise
    serialised = json.dumps(result)
    assert isinstance(serialised, str)


def test_format_json_path_length():
    report = make_report()
    result = format_json([report])
    assert result["conflicts"][0]["path_length"] == 2


def test_format_json_has_explanation():
    report = make_report(explanation="test explanation")
    result = format_json([report])
    assert result["conflicts"][0]["explanation"] == "test explanation"


# ---------------------------------------------------------------------------
# format_sarif
# ---------------------------------------------------------------------------


def test_format_sarif_schema():
    result = format_sarif([])
    assert result["version"] == "2.1.0"
    assert "$schema" in result


def test_format_sarif_has_runs():
    result = format_sarif([])
    assert "runs" in result
    assert len(result["runs"]) == 1


def test_format_sarif_tool_name():
    result = format_sarif([])
    tool = result["runs"][0]["tool"]["driver"]
    assert tool["name"] == "paxpy"


def test_format_sarif_has_rules():
    result = format_sarif([])
    rules = result["runs"][0]["tool"]["driver"]["rules"]
    rule_ids = [r["id"] for r in rules]
    assert "paxpy/DATA_FLOW" in rule_ids
    assert "paxpy/CONTROL_DEPENDENCY" in rule_ids


def test_format_sarif_single_result():
    report = make_report()
    result = format_sarif([report])
    results = result["runs"][0]["results"]
    assert len(results) == 1
    r = results[0]
    assert r["ruleId"] == "paxpy/DATA_FLOW"
    assert r["level"] == "error"  # INCOMPATIBLE → error


def test_format_sarif_warning_for_suspicious():
    report = make_report(compatibility=Compatibility.SUSPICIOUS)
    result = format_sarif([report])
    assert result["runs"][0]["results"][0]["level"] == "warning"


def test_format_sarif_is_json_serialisable():
    reports = [make_report()]
    result = format_sarif(reports)
    serialised = json.dumps(result)
    assert isinstance(serialised, str)


def test_format_sarif_has_locations():
    report = make_report()
    result = format_sarif([report])
    r = result["runs"][0]["results"][0]
    assert len(r["locations"]) >= 1
    assert "physicalLocation" in r["locations"][0]


def test_format_sarif_related_locations():
    report = make_report()
    result = format_sarif([report])
    r = result["runs"][0]["results"][0]
    assert len(r["relatedLocations"]) >= 1
