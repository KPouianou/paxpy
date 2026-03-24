"""Format ConflictReport instances for CLI, JSON, and SARIF output.

Takes the final list of ConflictReport objects produced by the analysis
pipeline and renders them in one of three formats:

  cli   — Human-readable terminal output with severity indicators.
  json  — Machine-readable dict suitable for json.dumps().
  sarif — Static Analysis Results Interchange Format (SARIF 2.1.0) dict,
          suitable for GitHub Code Scanning upload.

Depends on: types only. No I/O performed — callers handle writing to stdout
or files.
"""

from __future__ import annotations

from paxpy import __version__
from paxpy.types import Compatibility, ConflictReport, ConflictType

# SARIF severity levels mapped from Compatibility
_SARIF_LEVEL: dict[Compatibility, str] = {
    Compatibility.INCOMPATIBLE: "error",
    Compatibility.SUSPICIOUS: "warning",
    Compatibility.COMPATIBLE: "note",
    Compatibility.UNKNOWN: "warning",
}

# CLI severity prefix
_CLI_PREFIX: dict[Compatibility, str] = {
    Compatibility.INCOMPATIBLE: "[ERROR]",
    Compatibility.SUSPICIOUS: "[WARN] ",
    Compatibility.COMPATIBLE: "[OK]   ",
    Compatibility.UNKNOWN: "[?]    ",
}

# Tier badge
_TIER_BADGE = {1: "T1", 2: "T2"}

# ConflictType short labels
_TYPE_LABEL: dict[ConflictType, str] = {
    ConflictType.DATA_FLOW: "DATA_FLOW",
    ConflictType.CONFLUENCE: "CONFLUENCE",
    ConflictType.OVERRIDE_ASSIGNMENT: "OVERRIDE",
    ConflictType.CONTROL_DEPENDENCY: "CONTROL",
}


def format_cli(reports: list[ConflictReport]) -> str:
    """Render conflict reports as a human-readable CLI string.

    Produces one block per conflict with: severity indicator, conflict type,
    direction (A→B or B→A), source/sink file and function, tier, and
    compatibility verdict with explanation.

    Args:
        reports: List of ConflictReport instances to render.

    Returns:
        Multi-line string ready for printing to stdout. Returns a "no conflicts"
        summary when reports is empty.
    """
    if not reports:
        return f"paxpy v{__version__}: no semantic conflicts detected.\n"

    lines: list[str] = [f"paxpy v{__version__}: {len(reports)} conflict(s) detected.\n"]

    for i, report in enumerate(reports, start=1):
        ip = report.interference
        cr = report.compatibility

        prefix = _CLI_PREFIX.get(cr.compatibility, "[?]    ")
        tier = _TIER_BADGE.get(ip.tier, f"T{ip.tier}")
        ctype = _TYPE_LABEL.get(ip.conflict_type, ip.conflict_type.value)
        direction = "A→B" if ip.direction == "A_to_B" else "B→A"

        lines.append(f"{'─' * 60}")
        lines.append(
            f"{prefix} #{i}  {ctype}  {direction}  [{tier}]  {cr.compatibility.value.upper()}"
        )
        lines.append(f"  Source : {report.source_file}  {report.source_function}")
        lines.append(f"  Sink   : {report.sink_file}  {report.sink_function}")
        if ip.path_nodes:
            lines.append(f"  Path   : {len(ip.path_nodes)} node(s)")
        if cr.explanation:
            lines.append(f"  Note   : {cr.explanation}")
        lines.append("")

    return "\n".join(lines)


def format_json(reports: list[ConflictReport]) -> dict:
    """Render conflict reports as a serialisable dict.

    Top-level keys: "version", "conflict_count", "conflicts" (list of dicts).
    Each conflict dict mirrors the ConflictReport structure with all enum
    values converted to their string values and Path objects converted to
    strings.

    Args:
        reports: List of ConflictReport instances to serialise.

    Returns:
        Dict safe to pass to json.dumps().
    """
    conflicts = []
    for report in reports:
        ip = report.interference
        cr = report.compatibility
        conflicts.append(
            {
                "direction": ip.direction,
                "conflict_type": ip.conflict_type.value,
                "tier": ip.tier,
                "source_node": ip.source_node,
                "sink_node": ip.sink_node,
                "path_length": len(ip.path_nodes),
                "source_function": report.source_function,
                "sink_function": report.sink_function,
                "source_file": report.source_file,
                "sink_file": report.sink_file,
                "compatibility": cr.compatibility.value,
                "explanation": cr.explanation,
            }
        )
    return {
        "version": __version__,
        "conflict_count": len(reports),
        "conflicts": conflicts,
    }


def format_sarif(reports: list[ConflictReport]) -> dict:
    """Render conflict reports in SARIF 2.1.0 format.

    Produces a SARIF log dict with one run. Each ConflictReport becomes one
    result with ruleId derived from conflict_type, level derived from
    compatibility, and locations pointing to source and sink files/lines.

    Args:
        reports: List of ConflictReport instances.

    Returns:
        SARIF 2.1.0 log dict ready for json.dumps().

    References:
        https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html
    """
    results = []
    for report in reports:
        ip = report.interference
        cr = report.compatibility
        level = _SARIF_LEVEL.get(cr.compatibility, "warning")

        # Parse line number from source_node if available (format: "path:line:col")
        source_line = _node_lineno(ip.source_node)
        sink_line = _node_lineno(ip.sink_node)

        result = {
            "ruleId": f"paxpy/{ip.conflict_type.value}",
            "level": level,
            "message": {
                "text": (
                    f"{_TYPE_LABEL.get(ip.conflict_type, ip.conflict_type.value)} conflict "
                    f"({'A→B' if ip.direction == 'A_to_B' else 'B→A'}, "
                    f"tier {ip.tier}): {cr.explanation}"
                )
            },
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": report.source_file},
                        "region": {"startLine": source_line},
                    }
                }
            ],
            "relatedLocations": [
                {
                    "id": 1,
                    "message": {"text": "Sink location"},
                    "physicalLocation": {
                        "artifactLocation": {"uri": report.sink_file},
                        "region": {"startLine": sink_line},
                    },
                }
            ],
            "properties": {
                "tier": ip.tier,
                "direction": ip.direction,
                "source_function": report.source_function,
                "sink_function": report.sink_function,
                "compatibility": cr.compatibility.value,
            },
        }
        results.append(result)

    rules = [
        {
            "id": f"paxpy/{ct.value}",
            "name": ct.name,
            "shortDescription": {"text": f"Semantic merge conflict: {ct.value}"},
        }
        for ct in ConflictType
    ]

    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "paxpy",
                        "version": __version__,
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }


def _node_lineno(node_id: str) -> int:
    """Extract line number from a NodeId string 'path:line:col'. Returns 1 on failure."""
    parts = node_id.split(":")
    if len(parts) >= 2:
        try:
            return int(parts[-2])
        except ValueError:
            pass
    return 1
