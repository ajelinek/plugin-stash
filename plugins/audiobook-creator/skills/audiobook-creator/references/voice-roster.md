# Voice roster

Six curated voices -- three female, three male -- deliberately narrow (Kokoro's
full bank has far more) so that in multi-voice story mode a listener can
actually tell characters apart by ear without seeing a name on screen. Each
gender pair mixes US/UK accents specifically to maximize the perceptual gap
between same-gender voices.

| Voice ID | Gender | Accent | Role | Notes |
|---|---|---|---|---|
| `af_heart` | Female | US | Default narrator | Neutral/warm. Plain-mode default. |
| `bf_isabella` | Female | UK | Alt 1 | Accent contrast from `af_heart`. |
| `af_nicole` | Female | US | Alt 2 | Different timbre from `af_heart` despite same accent. |
| `am_michael` | Male | US | Default narrator | Neutral. Plain-mode default if the user wants a male voice. |
| `bm_george` | Male | UK | Alt 1 | Accent contrast. |
| `am_fenrir` | Male | US | Alt 2 | Deeper/distinct timbre. |

These are the **only** valid values for `lines[].voice` in the script --
`start_synthesis` rejects anything else before synthesizing. Don't reach
for other IDs from Kokoro's full voice bank even though the library
technically supports them; the whole point of narrowing to 6 is that a
listener can learn to recognize each one across an audiobook.

## Choosing voices

**Plain mode:** one voice for the entire document. Default to `af_heart`
unless the user asks for a male voice (`am_michael`) or names a specific
voice from the table. Never use more than one voice in plain mode, even for
quoted dialogue in the source -- predictability is the point of this mode.

**Story mode:** you decide whether multiple voices improve the read, and if
so, which lines get which voice (see
[story-mode-guide.md](story-mode-guide.md)). A common pattern: one narrator
voice carries the framing/connective narration, and a second, contrasting
voice (opposite gender, or same gender but the accent-contrasting alt) takes
a recurring character's lines. Don't reach for a third voice unless the
content genuinely has three distinct recurring speakers -- more voices than
the material supports just makes a listener work to track who's talking.

## Distinctiveness

Kokoro's per-voice audio is what it is -- there's no tuning knob here, only
the choice of *which* 6 IDs to expose. If a listening pass turns up two
voices that read as too similar in practice, the fix is to swap the roster
table above for different IDs from Kokoro's bank, not to try to compensate
for it elsewhere in the pipeline.
