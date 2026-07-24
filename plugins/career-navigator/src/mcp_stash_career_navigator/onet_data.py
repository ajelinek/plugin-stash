"""Loader for the bundled O*NET occupation dataset.

`data/onet_occupations.json` is a trimmed, pre-processed extract of the O*NET
30.3 Database (onetcenter.org/database.html), licensed CC BY 4.0 by the U.S.
Department of Labor / Employment and Training Administration. It was built by
joining `occupation_data`, `career_interest_types` (RIASEC/Occupational
Interest scores), `job_zones`, `education`, `essential_skills`, `knowledge`,
`task_ratings`, and `work_styles`, keeping only the 923 occupations that have
RIASEC data and only the fields this plugin's matching logic uses (top tasks
and top work styles were added later by
`scripts/enrich_onet_dataset.py` — see that script and
skills/career-navigator/references/onet-data.md for the full field-by-field
provenance and attribution text).

Committed as a single JSON file rather than downloaded on first run: every
client gets identical, working-offline data the moment they install, with no
runtime dependency on onetcenter.org staying reachable or unchanged.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

RIASEC_CODES = ("R", "I", "A", "S", "E", "C")

RIASEC_LABELS = {
    "R": "Realistic",
    "I": "Investigative",
    "A": "Artistic",
    "S": "Social",
    "E": "Enterprising",
    "C": "Conventional",
}

_cache: dict[Path, list[dict[str, Any]]] = {}


def onet_data_path() -> Path:
    override = os.environ.get("MCP_STASH_CAREER_NAVIGATOR_ONET_PATH")
    if override:
        return Path(override)
    return Path(__file__).parent / "data" / "onet_occupations.json"


def load_occupations() -> list[dict[str, Any]]:
    path = onet_data_path()
    if path not in _cache:
        with open(path, encoding="utf-8") as f:
            _cache[path] = json.load(f)
    return _cache[path]


def find_by_soc_code(soc_code: str) -> dict[str, Any] | None:
    for occ in load_occupations():
        if occ["soc_code"] == soc_code:
            return occ
    return None
