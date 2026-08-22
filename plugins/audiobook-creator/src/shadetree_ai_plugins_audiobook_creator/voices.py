"""The fixed voice roster.

Deliberately a closed set, not a passthrough to whatever Kokoro ships:
`references/voice-roster.md` documents these six and the skill picks from
them, so an unknown id is a script error worth failing on rather than
something to forward to the model and discover mid-synthesis.
"""

from __future__ import annotations

VOICE_ROSTER: dict[str, str] = {
    "af_heart": "Female, US, neutral/warm (plain-mode default)",
    "bf_isabella": "Female, UK",
    "af_nicole": "Female, US, alt timbre",
    "am_michael": "Male, US, neutral (plain-mode default if male requested)",
    "bm_george": "Male, UK",
    "am_fenrir": "Male, US, deeper/distinct timbre",
}

MIN_SPEED = 0.5
MAX_SPEED = 2.0

# Kokoro's tokenizer documents these two as its options, and every voice above
# is one or the other. `lang` selects the pronunciation variant, not the voice.
DEFAULT_LANG = "en-us"
SUPPORTED_LANGS = frozenset({"en-us", "en-gb"})
