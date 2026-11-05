"""What the coach may write into its memory of the user (pure).

Notes are short facts about the user, shown to them and editable. They are read back
into later chats, so a note must never carry a link or text that tries to instruct the
assistant: that would let one conversation (or an injected image) steer the next.
"""

import re
from typing import Final

from app.rules.redflags import normalize

MAX_NOTE_CHARS: Final = 200
MAX_KEY_CHARS: Final = 100

_LINK = re.compile(r"(https?://|www\.|\b[\w-]+\.(com|net|org|io|ai|eg|me|ly|co)\b)")
_INSTRUCTION_PATTERNS: Final = (
    r"\bignore\b",
    r"\bdisregard\b",
    r"\b(previous|prior|above|earlier) (instructions?|rules?|messages?)\b",
    r"\b(assistant|system|developer)\s*:",
    r"\bsystem prompt\b",
    r"\bfrom now on\b",
    r"\byou (must|should|will|are now|have to|need to)\b",
    r"\b(always|never) (recommend|say|mention|tell|give|answer|reply)\b",
    r"\bact as\b",
    r"\bpretend\b",
    r"untrusted_data",
    r"[<>]",
    "تجاهل",
    "التعليمات",
    "انت لازم",
    "من دلوقتي",
)
_INSTRUCTION = re.compile("|".join(_INSTRUCTION_PATTERNS))


def check_note(key: str, value: str) -> str | None:
    """None if the note may be saved, else the reason it is refused."""
    if not key.strip() or not value.strip():
        return "empty"
    if len(key) > MAX_KEY_CHARS or len(value) > MAX_NOTE_CHARS:
        return "too_long"
    text = normalize(f"{key} {value}")
    if _LINK.search(text):
        return "contains_link"
    if _INSTRUCTION.search(text):
        return "instruction"
    return None
