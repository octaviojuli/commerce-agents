"""Personal numbers a customer pastes into a chat are not kept or shown to the model.

Documents go through `papers`, where they are sealed; a turn keeps only what selling needs.
"""

import re

PATTERNS = (
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("身份证号", re.compile(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])")),
    ("护照号", re.compile(r"(?<![0-9A-Za-z])[CEGDHKPS][A-Z]?\d{7,8}(?![0-9A-Za-z])")),
)


def mask(text: str) -> tuple[str, list[str]]:
    """The text with personal numbers replaced, and the kinds that were found."""
    found = []
    for label, pattern in PATTERNS:
        text, n = pattern.subn(f"〔{label}已隐藏〕", text)
        if n:
            found.append(label)
    return text, found
