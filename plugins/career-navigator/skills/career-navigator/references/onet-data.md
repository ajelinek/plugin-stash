# The bundled O*NET dataset

## Source and license

`src/shadetree_ai_plugins_career_navigator/data/onet_occupations.json` is a trimmed,
pre-processed extract of the **O*NET 30.3 Database**
(https://www.onetcenter.org/database.html), sponsored by the U.S. Department
of Labor's Employment and Training Administration (USDOL/ETA). O*NET 30.3 is
licensed **CC BY 4.0** — free to use commercially or non-commercially, with
attribution.

Required attribution (include it wherever this data is presented at any
distance from this repo, e.g. in a report generated for a client):

> This product includes information from O\*NET OnLine by the U.S.
> Department of Labor, Employment and Training Administration (USDOL/ETA).
> Used under the CC BY 4.0 license. O\*NET® is a trademark of USDOL/ETA.

## Why a bundled snapshot, not a runtime download

O*NET's own flat files are ~50-100MB and change on USDOL/ETA's own release
schedule (quarterly, major update yearly). This repo's plugin pattern has no
mechanism for a first-run network download (see the root `CLAUDE.md`'s
`uv`-missing hook section: this repo deliberately never auto-fetches
anything unattended onto a client's machine), and a fully offline plugin is
simpler to reason about and test. So the dataset is committed as a single
~1.3MB JSON file, pre-joined and pre-trimmed to only what the matching logic
in `matching.py` actually uses — a deliberate summary of O*NET, not the full
database (O*NET also has Abilities, Work Activities, Work Context, Work
Values, the full ~35-element Skills/Knowledge scores beyond each
occupation's top 5, Tools & Technology, Related Occupations, and wage/
employment outlook — none of that is in this file). It won't reflect O*NET
releases after 30.3 — regenerate it (see below) to pick up a newer release.

## Build process

Joined the following O*NET 30.3 CSV tables
(`https://www.onetcenter.org/dl_files/database/db_30_3_csv/<name>.csv`) on
`O*NET-SOC Code`:

| Table | Used for |
|---|---|
| `occupation_data.csv` | `title`, `description` |
| `career_interest_types.csv` | RIASEC scores — O*NET's "Occupational Interests" scale (`OI`, range 1-7) per Realistic/Investigative/Artistic/Social/Enterprising/Conventional element |
| `job_zones.csv` + `job_zone_reference.csv` | `job_zone` (1-5) + its human-readable label |
| `education.csv` + `education_categories.csv` | `typical_education` — the modal (highest Data Value) "Required Level of Education" category, resolved to its label |
| `essential_skills.csv` | `top_skills` — top 5 skill elements by Importance (`IM`) score |
| `knowledge.csv` | `top_knowledge` — top 5 knowledge elements by Importance (`IM`) score |
| `task_ratings.csv` | `tasks` — top 5 task statements by Importance (`IM`) score |
| `work_styles.csv` | `top_work_styles` — top 5 Work Styles elements by Work Styles Impact (`WI`) score. Work Styles is O*NET's closest concept to "grit"/persistence — the element list includes Persistence, Achievement/Effort, Dependability, Initiative, Adaptability, Stress Tolerance |

Only the **923** occupations (of 1,016 total in `occupation_data.csv`) that
have all six RIASEC elements present in `career_interest_types.csv` were
kept — the rest have no RIASEC data in this O*NET release and can't be
matched on that axis. RIASEC scores are rounded to 2 decimals; `top_codes` is
the 3 highest-scoring letters, descending.

`tasks` and `top_work_styles` were added in a second pass, after the initial
build, by `scripts/enrich_onet_dataset.py` — a real, reusable script (unlike
the rest of this dataset's original one-time-manual build) that fetches
`task_ratings.csv`/`work_styles.csv` from the same O*NET 30.3 release and
joins them onto the existing 923 records by O*NET-SOC Code, without adding,
removing, or otherwise touching any other field. An occupation with no rated
tasks or work styles in this release (29 and 32 of the 923, respectively)
gets an empty list for that field rather than being dropped. Re-run it any
time (`python3 scripts/enrich_onet_dataset.py` from the plugin root) — it's
idempotent against the current file.

The rest of the dataset (everything except `tasks`/`top_work_styles`) was a
one-time manual build with no committed script. To regenerate it against a
newer O*NET release, re-fetch the tables above for the new version number
and re-run the same join/trim logic, then replace
`src/shadetree_ai_plugins_career_navigator/data/onet_occupations.json`, re-run
`scripts/enrich_onet_dataset.py`, and bump the plugin's version per the root
`CLAUDE.md`'s release steps.

## Field schema (`onet_occupations.json`)

Each entry:

```json
{
  "soc_code": "15-1252.00",
  "title": "Software Developers",
  "description": "...",
  "riasec": {"R": 1.24, "I": 5.41, "A": 1.93, "S": 1.85, "E": 4.42, "C": 5.79},
  "top_codes": ["C", "I", "E"],
  "job_zone": 4,
  "job_zone_label": "Job Zone Four: Considerable Preparation Needed",
  "typical_education": "Bachelor's Degree",
  "top_skills": ["Reading Comprehension", "Active Listening", "..."],
  "top_knowledge": ["Computers and Electronics", "Mathematics", "..."],
  "tasks": ["Modify existing software to correct errors...", "..."],
  "top_work_styles": ["Analytical Thinking", "Integrity", "..."]
}
```

`tasks` and `top_work_styles` can be an empty list for the small number of
occupations (29 and 32 of 923, respectively) that had no rated data for that
element in this O*NET release — always check for an empty list rather than
assuming every occupation has 5 of each.

`riasec` values are on O*NET's own 1-7 scale — **not** the same 0-100 scale
`career_update_profile` uses for the *student's* self-reported-by-Claude
scores. The two are never compared numerically; matching uses `top_codes`
rank-order overlap only (see `matching.py`), which sidesteps needing to
reconcile the two scales.
