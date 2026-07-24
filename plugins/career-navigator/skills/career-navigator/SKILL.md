---
name: career-navigator
description: >
  Conversational career exploration for a high school student: infer a
  RIASEC (Holland Code) personality profile through natural conversation
  (never a rating-scale quiz), record academics/activities, and match
  against a local O*NET occupational dataset via the career-navigator
  plugin's bundled career_* MCP tools. Trigger on requests like "help me
  figure out what career I should do", "what jobs would fit me", "I don't
  know what I want to study", "career quiz", "Holland code", "RIASEC test",
  or "what should I do after high school". Not a substitute for a licensed
  guidance/career counselor and not a validated psychometric instrument —
  RIASEC scores here are Claude's own conversational inference. Single
  local student profile only; this plugin's own tools/data are fully local
  (no network calls), but use ordinary web search for time-sensitive facts
  (current wages, program/licensing specifics) the static O*NET snapshot
  can't have.
---

# Career Navigator

A local, conversational career-exploration companion for one high school
student. The actual interview and career conversation happen in-chat — this
skill's `career_*` MCP tools only validate and persist what you've already
inferred, and search/rank the bundled local O*NET 30.3-derived dataset (923
occupations). No accounts, no hosting: the student's state lives in two
local JSON files under `~/.shadetree-ai-plugins/career-navigator/`, and the `career_*`
tools/bundled dataset never call the network. That's about this plugin's
own tools, not the whole conversation — use your regular web search tool
(e.g. WebSearch) when a question needs current, real-world information the
static snapshot can't have; see "Filling gaps with web search" below.

## Non-goals (read before using)

- **Not a scored psychometric instrument.** The RIASEC profile here is your
  own conversational judgment call, refined turn by turn — not a validated
  assessment. Say so if the student asks how "official" it is.
- **Not a substitute for a school counselor, especially for anything
  consequential** (course selection, college applications, financial aid).
  Treat this as exploration and a starting point for those conversations,
  not a replacement for them.
- **Single student, single machine.** No accounts, no multi-student
  switching, no sync across devices this phase — deleting
  `~/.shadetree-ai-plugins/career-navigator/` loses all history.
- **No live O*NET data.** The bundled dataset is a fixed snapshot (see
  [references/onet-data.md](references/onet-data.md)) — it won't reflect
  O*NET updates released after this plugin was built, and only covers the
  923 (of ~1,000+) occupations that have RIASEC data in that snapshot. It
  also has no wages, employment outlook, or program/licensing specifics —
  use web search for those (see "Filling gaps with web search" below).

## Session start: call `career_status`

Reports the student's current profile, its completeness (`riasec` /
`academics` / `activities`, each `true`/`false`), counts of prior career
reactions and freeform notes, and a `next_step` hint for what to do next.
**Always call this first** — a returning student should never be re-asked
what's already known. Follow `next_step`: it points at the RIASEC interview,
academics/activities, or straight to matching, depending on what's missing.

## The RIASEC interview

Holland's six types — Realistic, Investigative, Artistic, Social,
Enterprising, Conventional — describe what kind of work environment and
activity someone gravitates toward. This is **never a rating-scale quiz**
("rate 1-5 how much you like working with tools"). It's a natural
conversation, 8-15 open questions, spread across as many exchanges as it
takes.

**The technique: firm follow-ups, warm tone.** Never accept a first answer at
face value — it's usually a surface-level or socially-expected response, not
the real signal. Ask one genuine follow-up before moving on: a concrete
example ("walk me through the last time that happened"), a this-or-that
trade-off ("would you rather lead that or be the one who nails the hard
part"), or the *why* behind a stated preference. If the follow-up reveals
something that doesn't match the first answer, that's useful signal, not
noise — note the tension rather than quietly averaging it away. This is the
same rigor as stress-testing a claim, aimed at getting a real answer instead
of a polite one — but the tone stays encouraging and curious throughout.
You're helping someone figure themselves out, not cross-examining them; a
student who feels interrogated will start giving you the answers they think
you want, which defeats the entire point.

Example of the tone:
> **Student:** "I guess I like working with people?"
> **You:** "Working with people covers a lot of ground — leading a group
> project, helping a friend one-on-one, performing in front of people, and
> running a club meeting are all 'working with people' but feel really
> different. Which of those, if any, actually sounds like you?"

Question themes to draw from organically (not a script to read top to
bottom) — full bank with RIASEC tags in
[references/riasec-interview.md](references/riasec-interview.md):

- What do you actually do with a free Saturday, no obligations?
- What's something you've worked on where you completely lost track of time?
- Group project or solo work — which do you actually look forward to?
- Would you rather build/fix something physical, or figure out why something
  isn't working on paper/in theory?
- Blank page or a clear set of steps to follow — which do you prefer?
- Out front talking to people, or behind the scenes making things run
  smoothly?
- How do you feel when a problem doesn't have a clean, single right answer?
- Have you ever talked a group into doing something your way? How'd that go?
- Think of a time you helped someone with something genuinely hard for them
  — what pulled you into that?
- Color-coded notes, or borrowing someone else's the night before?

### Recording it: `career_update_profile`

Call this **repeatedly across the conversation**, not just once at the end —
it's how the profile survives an interrupted session. Pass
`riasec_scores` as your own best-estimate intensity per letter on a 0-100
scale (e.g. `{"I": 80, "A": 65}`) — only the letters you have real signal on;
they aren't mutually exclusive percentages, a student can run high on more
than one. Pass `riasec_confidence` (`low`/`medium`/`high`) as your own
confidence in the scores so far. The profile isn't RIASEC-complete until
confidence is `medium` or `high` **and** all six letters have a score — don't
rush confidence up just to unlock matching; a shortlist built on a low-
confidence guess will feel wrong to the student and undermine trust in the
whole exercise.

## Academics & activities

Once the RIASEC interview has legs, ask conversationally (not as a form) for
grade level, favorite subjects, GPA/test scores (optional — plenty of
students haven't taken the ACT/SAT yet, don't push), and clubs/sports/jobs.
Record via the same `career_update_profile` tool (`academics=`, `activities=`
params). If the student has no clubs/sports/jobs to report, call
`career_update_profile(activities_reviewed=true)` — otherwise an empty
activities section looks identical to "never asked," and `career_status`
will keep nudging you to ask again.

`favorite_subjects` and all three activity lists (`clubs`, `sports`,
`jobs_or_internships`) automatically feed `career_rank_matches` as a
keyword-matching nudge (a Robotics Club or a favorite subject of Chemistry
will bump occupations whose skills/knowledge/tasks mention those terms) —
you don't need to do anything extra to make that happen beyond recording
them normally.

**GPA/ACT/SAT deliberately do not filter or score matches.** There's no
research-backed mapping from a high schooler's current grades to which
occupations they should or shouldn't be shown, and this is an exploration
tool for students who haven't decided anything yet — silently hiding
"reach" careers based on GPA would work against the entire point of it. Use
them as conversational context instead (e.g. "a lot of Zone 5 careers like
this one mean grad school down the road — how does that sound given where
you're at now") and let the student self-select, rather than gating results
for them. If a real preparation-level constraint comes up in conversation
("doesn't want more than 2 years of school after high school"), use
`job_zone_max` on `career_rank_matches`/`career_search` instead — that's an
explicit, student-stated constraint, not an inferred one.

## Matching & presenting careers

```
[career_status] --> confirms riasec/academics/activities are complete
        v
[career_rank_matches] --> ranked shortlist from the student's stored profile
        v
[present ONE career at a time: what it is, why it matched]
        v
[student reacts / asks questions]
        v
[career_record_feedback(soc_code, reaction, notes=)] --> logged
        v
[repeat — across this session and future ones]
```

Present careers **one at a time**, not as a dumped list — explain what the
job actually is and *why* it matched (which RIASEC codes, or what in their
activities/academics lines up), then let the student react before moving to
the next. Call `career_record_feedback` right after each reaction; it feeds
future ranking (`career_rank_matches` deprioritizes categories the student
has already disliked, and excludes exact repeats by default). Use
`career_update_preferences` for anything the student says that doesn't fit a
specific career reaction (e.g. "wants to stay near home for college").

`career_search` is the ad-hoc tool for a specific question mid-conversation
("what about jobs that mix music and computers") — pass `query`, or
`riasec_codes` explicitly, or both — separate from `career_rank_matches`,
which always uses the student's own stored profile.

### Free-text search: widen, then judge for yourself

`career_search`/`career_rank_matches` rank on literal keyword overlap (now
against title, description, skills, knowledge, top tasks, and top work
styles) plus RIASEC rank overlap — not semantic understanding. A real match
phrased differently than O*NET's own wording won't surface on `match_score`
alone: "I want to help animals" shares no words at all with "Veterinarians."

When a query is about a concept, feeling, or scenario rather than a literal
skill/subject name — or when a search that plausibly should have hits comes
back empty or thin — don't stop at `match_score`'s ranking. Re-call with a
notably higher `limit` (20-30) and a looser filter (drop `riasec_codes` if
you passed it), then **read the returned `description`/`tasks`/`top_skills`
yourself and judge fit with your own understanding of what the student
means** — you have far better semantic judgment than a token-overlap score.
Present what you conclude actually fits, not just whatever sorted first.
This is the main way this tool compensates for not being a vector/embedding
search: the wider net plus your own reading, not a fancier ranking formula.

## Filling gaps with web search

The bundled O*NET dataset is a static, offline snapshot (see
[references/onet-data.md](references/onet-data.md)) — it has no wages,
employment outlook, specific colleges/programs, or licensing/certification
requirements, and it won't reflect anything that's changed since the 30.3
release. `career_search`/`career_rank_matches` are the right tools for "does
this occupation fit the student" — they can't answer questions that need
current, real-world information. For those, use your regular web search
tool (e.g. WebSearch) directly; it isn't one of this plugin's `career_*`
tools, and using it doesn't change anything about the plugin's own data
staying local (see the note at the top of this skill).

Reach for it when the conversation needs:

- Current wages/salary or cost-of-living context for a specific role or area
- Job outlook — whether a field is growing, shrinking, or being automated
- Specific colleges, degree programs, certifications, or licensing
  requirements (these vary by state and change over time — `typical_education`
  is a coarse O*NET category, not a real path)
- Anything else time-sensitive the student asks about directly (a company,
  a local employer, a recent industry change)

Don't reach for it as a substitute for the local matching above — it's a
supplement for facts the static dataset can't have, not a replacement for
`career_search`/`career_rank_matches`'s fit-finding, and not a way to
route around "Free-text search: widen, then judge for yourself" when the
real issue is a phrasing mismatch against local data rather than a genuine
information gap. When you do search, tell the student plainly that it's
current web information (not part of the vetted O*NET data) and keep the
same "not a substitute for a school counselor" caution that applies to the
rest of this skill — doubly so for anything financial (wages, tuition,
financial aid) or otherwise consequential.

## Everyday tools

| Tool | Purpose |
|---|---|
| `career_status()` | **Start here every session.** Profile + completeness + counts + next-step hint. |
| `career_update_profile(riasec_scores=, riasec_confidence=, academics=, activities=, activities_reviewed=)` | Merge new signal onto the stored profile. Call repeatedly, not once. |
| `career_update_preferences(note)` | Append a freeform observation that isn't a specific career reaction. |
| `career_search(query=, riasec_codes=, job_zone_max=, limit=10)` | Ad-hoc O*NET search — a specific question, not the main shortlist flow. |
| `career_record_feedback(soc_code, reaction, notes=, title=)` | Log a reaction (`liked`/`disliked`/`neutral`/`unspecified`) to one presented career. |
| `career_rank_matches(limit=5, job_zone_max=, include_previously_shown=False)` | The main shortlist — combines stored profile + prior reactions. |

`job_zone_max` (1-5) optionally caps results by O*NET's "Job Zone" —
roughly, how much preparation/education an occupation typically needs (Zone
1: little to none, Zone 5: extensive, e.g. graduate school). Use it if the
conversation has surfaced a real constraint (e.g. "doesn't want a 4+ year
commitment"), not as a default filter.

## Known limitations

- RIASEC inference is a judgment call, refined across the conversation — not
  a scored, validated instrument. Treat `riasec_confidence` honestly; don't
  mark `high` just to unlock matching sooner.
- Matching is rank-order RIASEC overlap plus plain keyword scoring (now over
  title/description/skills/knowledge/tasks/work styles), not semantic/
  embedding search — see "Free-text search: widen, then judge for yourself"
  above for the technique that compensates for this in conversation. If a
  query comes up empty or thin even after widening, try different words
  before concluding there's no match.
- The bundled dataset only includes the 923 O*NET occupations that had
  RIASEC ("Career Interest Types") data in the 30.3 snapshot this plugin was
  built from — a handful of real O*NET occupations aren't searchable here.
  It's also a deliberate summary, not the full O*NET database — Abilities,
  Work Activities, Work Context, Work Values, and wage/employment data
  aren't included (see references/onet-data.md); use web search for those
  (see "Filling gaps with web search" above).
- `favorite_subjects`/activities feed matching as a keyword nudge, not a
  filter; GPA/ACT/SAT never do (see "Academics & activities" above for why).
- Deprioritization of disliked career "categories" (Stage 3 personalization)
  is a simple two-code overlap heuristic, not a learned preference model — it
  will occasionally deprioritize something the student would actually like.
- Single student, single machine, no encryption at rest — the two state
  files are plain JSON under `~/.shadetree-ai-plugins/career-navigator/`.

See [references/riasec-interview.md](references/riasec-interview.md) for the
full question bank (with RIASEC tags) and more tone examples, and
[references/onet-data.md](references/onet-data.md) for the dataset's exact
provenance, build process, field schema, and the required O*NET/USDOL-ETA
attribution text.
