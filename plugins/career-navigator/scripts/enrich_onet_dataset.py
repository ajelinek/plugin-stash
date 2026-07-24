"""One-time (repeatable) build step: enriches the bundled O*NET occupation
dataset with `tasks` (top 5 task statements by O*NET's own Importance
rating) and `top_work_styles` (top 5 Work Styles by Work Styles Impact) —
see skills/career-navigator/references/onet-data.md for full provenance and
why these two fields were added.

Run from the plugin root:

    python3 scripts/enrich_onet_dataset.py

Downloads task_ratings.csv and work_styles.csv from the same O*NET 30.3
release (https://www.onetcenter.org/dl_files/database/db_30_3_csv/) already
used to build onet_occupations.json, joins them onto the existing 923
records by O*NET-SOC Code, and rewrites the dataset file in place. Does not
add, remove, or otherwise touch any existing occupation or field — an
occupation with no rated tasks/work styles in this O*NET release just gets
an empty list for that field rather than being dropped.
"""

from __future__ import annotations

import csv
import io
import json
import urllib.request
from collections import defaultdict
from pathlib import Path

BASE_URL = "https://www.onetcenter.org/dl_files/database/db_30_3_csv"
DATA_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "shadetree_ai_plugins_career_navigator"
    / "data"
    / "onet_occupations.json"
)
TOP_N = 5


def _fetch_csv(name: str) -> list[dict[str, str]]:
    url = f"{BASE_URL}/{name}.csv"
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 - fixed onetcenter.org URL
        text = resp.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def _top_tasks(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    by_soc: dict[str, list[tuple[float, str, str]]] = defaultdict(list)
    for row in rows:
        if row["Scale ID"] != "IM":
            continue
        by_soc[row["O*NET-SOC Code"]].append(
            (float(row["Data Value"]), row["Task ID"], row["Task"])
        )
    result = {}
    for soc, tasks in by_soc.items():
        tasks.sort(key=lambda t: (-t[0], t[1]))
        seen: set[str] = set()
        top: list[str] = []
        for _, task_id, text in tasks:
            if task_id in seen:
                continue
            seen.add(task_id)
            top.append(text)
            if len(top) == TOP_N:
                break
        result[soc] = top
    return result


def _top_work_styles(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    by_soc: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for row in rows:
        if row["Scale ID"] != "WI":
            continue
        by_soc[row["O*NET-SOC Code"]].append((float(row["Data Value"]), row["Element Name"]))
    result = {}
    for soc, styles in by_soc.items():
        styles.sort(key=lambda s: -s[0])
        result[soc] = [name for _, name in styles[:TOP_N]]
    return result


def main() -> None:
    occupations = json.loads(DATA_PATH.read_text(encoding="utf-8"))

    print("Fetching task_ratings.csv...")
    tasks_by_soc = _top_tasks(_fetch_csv("task_ratings"))
    print("Fetching work_styles.csv...")
    work_styles_by_soc = _top_work_styles(_fetch_csv("work_styles"))

    missing_tasks = missing_styles = 0
    enriched = []
    for occ in occupations:
        soc = occ["soc_code"]
        tasks = tasks_by_soc.get(soc, [])
        work_styles = work_styles_by_soc.get(soc, [])
        if not tasks:
            missing_tasks += 1
        if not work_styles:
            missing_styles += 1
        occ = dict(occ)
        occ["tasks"] = tasks
        occ["top_work_styles"] = work_styles
        enriched.append(occ)

    DATA_PATH.write_text(
        json.dumps(enriched, separators=(",", ":"), ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"Wrote {len(enriched)} occupations. "
        f"Missing tasks: {missing_tasks}, missing work styles: {missing_styles}"
    )


if __name__ == "__main__":
    main()
