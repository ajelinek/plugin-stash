# Kokoro Prosody & Expression Guide

Kokoro is a lightweight TTS engine (82M parameters) that achieves natural,
expressive narration without complex emotional markup. Instead, it uses:
**punctuation, voice selection, and speed control** to shape how text is read.
Understanding these tools prevents flat, monotone audiobooks.

## 1. Speed Control

**Global speed** (applied to every line, unless that line sets its own):
```
start_synthesis(script=..., speed=0.9)
```

**Per-line speed** (override for specific lines in the script):
```json
{
  "chapters": [{
    "lines": [
      {"voice": "af_heart", "text": "Action sequence here!", "speed": 1.15},
      {"voice": "af_heart", "text": "A moment of quiet reflection.", "speed": 0.85}
    ]
  }]
}
```

**Recommended ranges:**
- **0.85–0.95:** Dramatic, serious, or meditative content; lowers energy for emphasis
- **0.95–1.05:** Natural audiobook narration (default); neutral pacing
- **1.05–1.15:** Energetic passages, dialogue, action sequences; adds urgency
- **1.15+:** Fast-paced advertising, announcements, or comedic timing
- **0.5–0.85:** Very slow for particularly solemn or important moments

**When to use per-line speed:**
- Slower (0.85–0.9) for emotional peaks, plot twists, or warnings
- Faster (1.1–1.2) for dialogue, action, or comedic beats
- Revert to normal (1.0) for regular narration between peaks

## 2. Voice Selection

Six curated voices available; choose to match content tone. See
[voice-roster.md](voice-roster.md) for full descriptions and use cases.

**For plain mode:** Pick one voice for the entire narration. `af_heart` (warm
female) is the default; `am_michael` is the standard male choice.

**For story mode:** Assign voices to different characters and narrators.
Different voices naturally convey different emotional registers—use this to
reinforce character personality or narrative perspective.

**Voice psychology matters:** A warm, intimate voice makes a personal essay
feel closer; a deeper voice adds gravitas to instruction; distinct timbres
make character dialogue unambiguous.

## 3. Punctuation-Driven Prosody

Kokoro's core prosody mechanism. Everything below affects intonation and pacing
*without* requiring special markup—just use these punctuation marks naturally.

### Punctuation Effects

| Mark | Effect | Use for |
|---|---|---|
| **`.` (period)** | Full stop, long pause, intonation resets | Clear sentence boundaries; separates thoughts |
| **`,` (comma)** | Short breath pause, keeps flow | Clauses within a sentence; natural breathing |
| **`;` (semicolon)** | Mid-length pause (between comma and period) | Connecting related but distinct ideas |
| **`:` (colon)** | Pause with forward anticipation | Introducing a list, explanation, or quotation |
| **`—` (em-dash)** | Parenthetical pause, lighter interruption | Asides, sudden thoughts, or interjections |
| **`…` (ellipsis)** | Trailing pause with falling intonation | Dramatic effect, trailing off, suspense |
| **`?` (question mark)** | Rising intonation on yes/no questions | Questions expecting an answer |
| **`!` (exclamation)** | Higher energy and emphasis | Excitement, urgency, or strong emotion. Use one — `!!!` passes through to the model as three marks and isn't a documented intensity control |

### Best Practices for Punctuation

**Break long clauses:** Don't write "When the character stepped into the room he saw the painting on the wall and gasped." Instead: "When the character stepped into the room, he saw the painting on the wall. He gasped."

**Vary sentence structure:** Mix short sentences (for impact) with longer ones
(for flow). This creates natural rhythm and prevents monotone pacing.

**Use em-dashes for asides:** "She walked—slowly, carefully—toward the exit."
This creates a natural pause around the aside.

**Strategic ellipsis for drama:** "He reached for the handle… and pulled."
The ellipsis adds suspense before the reveal.

## 4. Emphasis: there is no markup

**Kokoro-onnx has no emphasis, stress, or SSML syntax. Anything you type gets
spoken.** The text goes to espeak-ng, which converts it to phonemes literally,
brackets and angle brackets and all:

```
"This is the [best](+2) outcome."   ->  ðɪs ɪz ðə bˈɛst(plˈʌs tˈuː) ˈaʊtkʌm.
                                        ...the narrator says "best plus two."

"Use <emphasis>now</emphasis>."     ->  jˈuːs ˈɛmfəsɪs nˈaʊ slˈæʃ ˈɛmfəsɪs.
                                        ...the narrator says "emphasis now slash emphasis."
```

The `[word](+1)` notation belongs to Misaki, the G2P used by the PyTorch
`kokoro` package. This plugin runs `kokoro-onnx`, which phonemizes through
espeak-ng instead. Don't reach for it, don't reach for SSML, and don't invent
a markup of your own -- all three end up in the audio as spoken words.

Emphasis here comes from four things, and only these four:

1. **Sentence structure.** A short sentence on its own line lands harder than
   the same clause buried mid-paragraph.
2. **Punctuation.** See section 3 -- this is the real prosody control.
3. **Per-line `speed`.** Slow a line down to give it weight.
4. **Voice choice.** In story mode, a different voice is the strongest
   emphasis available.

To stress one word inside a sentence, split the sentence so the word carries
its own line, or rewrite so it lands at the end of a clause.

## 5. Capitalization: ignored, and that's fine

Case has **no effect** on the output. espeak-ng normalizes it away before
phonemizing, so these are byte-identical downstream:

```
"The GPU was faster."  ->  ðə dʒˌiːpˌiːjˈuː wʌz fˈæstɚ.
"The gpu was faster."  ->  ðə dʒˌiːpˌiːjˈuː wʌz fˈæstɚ.
"STOP right there."    ->  stˈɑːp ɹˈaɪt ðˈɛɹ.
"stop right there."    ->  stˈɑːp ɹˈaɪt ðˈɛɹ.
```

Acronyms get letter-expanded automatically whether or not you capitalize them
(`fbi` and `FBI` both read as "eff-bee-eye"), so you don't need to do anything
to make that happen. Write normal prose and move on. ALL CAPS buys nothing --
not emphasis, not volume, not clarity.

### What you DO need to normalize: numbers and symbols

espeak's number handling is mechanical and often wrong for narration. Spell
these out yourself when the reading matters:

| You write | It reads as | Write instead |
|---|---|---|
| `$1,234.50` | "dollar one, two hundred thirty four. fifty" | "one thousand two hundred thirty four dollars and fifty cents" |
| `2026-08-19` | "two thousand twenty six dash zero eight dash nineteen" | "August nineteenth, twenty twenty six" |
| `3.14` | "three. fourteen" | "three point one four" |

This is the one place where preprocessing the text genuinely improves the
audio -- it's also part of why `text` is documented as plain prose rather
than raw source.

## 6. Techniques to Prevent Monotone Reading

### Text Structure
- **Shorter sentences for impact.** Compare "The character entered and was
  shocked by what they saw" (dull) vs. "The character entered. They stopped.
  Before them: a shocking sight." (engaging)
- **Vary clause count per sentence.** Mix single-clause ("He left.") with
  multi-clause ("As the sun set over the valley, casting long shadows across
  the hills, he finally understood.")
- **Break paragraphs strategically.** Each new paragraph gets a natural pause
  in the audio (separate lines in the script become `line_gap` pauses).

### Pacing & Speed
- Use slower speeds (0.9) for serious/emotional moments to add weight
- Use faster speeds (1.05–1.1) for dialogue or action to add energy
- Return to neutral (1.0) for regular narration

### Voice Diversity
- Story mode: Let different characters have different voices. This alone
  prevents monotone by forcing natural variation.
- Plain mode: Single voice throughout, so rely more heavily on punctuation,
  speed, and structure.

### Punctuation Rhythm
- Open with a short sentence (punchy)
- Build with longer, connected clauses (flow)
- Close with a short sentence (impact)

Example: "The door creaked open. Inside, shadows danced across faded
photographs and dusty shelves, each object a memory frozen in time. She took
a breath."

## 7. Common Mistakes to Avoid

| Mistake | Why It's Wrong | Fix |
|---|---|---|
| Long paragraphs without punctuation | Creates monotone blocks | Add commas, semicolons, or break into sentences |
| ALL CAPS for emphasis | Case is normalized away — it changes nothing | Split the sentence so the word lands alone, or slow the line down |
| Too much punctuation | Sounds choppy and unnatural | Let natural English pacing guide you |
| Identical sentence structure throughout | Boring rhythm | Vary short + long, simple + complex |
| Same voice for everything (story mode) | Character confusion | Assign distinct voices to key characters |
| Speed 1.0 for entire document | Flat energy | Use 0.9–0.95 for serious parts, 1.05–1.1 for energy |
| SSML tags, `[word](+2)`, or any other markup | Not parsed — spoken aloud as literal words | Stick to punctuation, structure, speed, and voice |

## 8. Practical Example: Before & After

### Before (Monotone)
```json
{
  "lines": [
    {
      "voice": "af_heart",
      "text": "The morning sun broke through the clouds as she walked down the quiet street thinking about everything that had happened and what it all meant."
    }
  ]
}
```

### After (Engaging)
```json
{
  "lines": [
    {"voice": "af_heart", "text": "The morning sun broke through the clouds.", "speed": 0.95},
    {"voice": "af_heart", "text": "She walked down the quiet street.", "speed": 0.9},
    {"voice": "af_heart", "text": "Thinking. Remembering. Processing.", "speed": 0.85},
    {"voice": "af_heart", "text": "And slowly, understanding came.", "speed": 0.95}
  ]
}
```

The second version:
- Uses shorter, punchier sentences
- Adds pauses between thoughts (line breaks)
- Slows down (`0.85–0.9`) for introspective moments
- Returns to normal pace (`0.95`) for narrative flow

## 9. Language Support

The script's `lang` field takes `en-us` (default) or `en-gb`, and nothing
else -- `start_synthesis` rejects any other value at validation time. It
selects the pronunciation variant, not the voice: every voice on the roster is
already US or UK English, and `lang` is what decides whether, say, "schedule"
comes out with a /sk/ or a /ʃ/. Match it to the voice you picked when the
difference would be jarring.

## 10. When to Use What

| Scenario | Tool(s) | Example |
|---|---|---|
| Generic audiobook narration | Voice + structure | Use `af_heart`, vary sentence length |
| Dramatic or tense moment | Speed + punctuation | `"…and then it happened."`  at speed 0.9 |
| Character dialogue | Voice + structure | Assign `am_michael` to male character, `af_heart` to female |
| Important announcement | Structure + speed | `"This part is critical."` on its own line at speed 0.9 |
| Quiet reflection | Speed + pauses | 0.85 speed with sentence breaks |
| Technical content | Spelled-out numbers | "returns ninety nine point nine percent" rather than "99.9%" |

---

**Bottom line:** Kokoro achieves expressiveness through *simplicity* — there
is no markup layer to reach for, and trying to invent one puts literal
brackets in the narration. Punctuation and sentence structure do most of the
work; speed and voice choice do the rest.
