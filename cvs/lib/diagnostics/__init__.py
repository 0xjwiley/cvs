"""Producer-side helpers that write the diagnostic contract; no policy or rendering."""

from .run_report import (
    RUN_DECK_INPUT_KEYS,
    RUN_DECK_PROVENANCE_FIELDS,
    REPORT_FILENAME,
    RunReportBuilder,
    default_run_dir,
    run_deck_provenance,
)

__all__ = [
    "RUN_DECK_INPUT_KEYS",
    "RUN_DECK_PROVENANCE_FIELDS",
    "REPORT_FILENAME",
    "RunReportBuilder",
    "default_run_dir",
    "run_deck_provenance",
]
