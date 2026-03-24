"""Format ConflictReport instances for CLI, JSON, and SARIF output.

Takes the final list of ConflictReport objects produced by the analysis
pipeline and renders them in one of three formats:

  cli   — Human-readable coloured terminal output with severity indicators.
  json  — Machine-readable dict suitable for json.dumps().
  sarif — Static Analysis Results Interchange Format (SARIF 2.1.0) dict,
          suitable for GitHub Code Scanning upload.

Depends on: types only. No I/O performed — callers handle writing to stdout
or files.
"""

from __future__ import annotations

from paxpy.types import ConflictReport


def format_cli(reports: list[ConflictReport]) -> str:
    """Render conflict reports as a human-readable CLI string.

    Produces one block per conflict with: severity indicator, conflict type,
    direction (A→B or B→A), source/sink file and function, tier, and
    compatibility verdict with explanation.

    Args:
        reports: List of ConflictReport instances to render.

    Returns:
        Multi-line string ready for printing to stdout. Empty string if no
        conflicts are found (prints a "no conflicts" summary instead).
    """
    raise NotImplementedError("TODO")


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
    raise NotImplementedError("TODO")


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
    raise NotImplementedError("TODO")
