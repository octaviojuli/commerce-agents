"""Personal numbers a customer pastes into a chat are not kept or shown to the model.

Documents go through `papers`, where they are sealed; a turn keeps only what selling needs.
"""

import re

# Do not join separate lines or ordinary amounts when accepting pasted number spacing.
SEP = r"[ \t\u00a0\u3000\-－‐‑–]?"
MOBILE = rf"1[3-9]\d(?:{SEP}\d){{8}}"
PASSPORT = rf"[CEGDHKPS][A-Z]?(?:{SEP}\d){{7,8}}"
PATTERNS = (
    (
        "手机号",
        re.compile(
            r"(?<![0-9A-Za-z])(?:1[3-9]\d{9}|"
            r"1[3-9]\d[ \t\u00a0\u3000\-－‐‑–]\d{4}[ \t\u00a0\u3000\-－‐‑–]\d{4})"
            r"(?![0-9A-Za-z])"
        ),
    ),
    ("身份证号", re.compile(r"(?<![0-9A-Za-z])\d{17}[\dXx](?![0-9A-Za-z])")),
    ("护照号", re.compile(r"(?<![0-9A-Za-z])[CEGDHKPS][A-Z]?\d{7,8}(?![0-9A-Za-z])", re.I)),
)
LABELED = (
    (
        "手机号",
        re.compile(
            rf"((?:手机|电话|联系)(?:号码?|方式)?[：:\s]*)(?:\+?86{SEP})?({MOBILE})(?![0-9A-Za-z])",
            re.I,
        ),
    ),
    (
        "护照号",
        re.compile(
            rf"((?:护照(?:号码?)?|passport(?:\s*(?:no\.?|number))?)[：:\s]*)({PASSPORT})(?![0-9A-Za-z])",
            re.I,
        ),
    ),
)


def mask(text: str) -> tuple[str, list[str]]:
    """The text with personal numbers replaced, and the kinds that were found."""
    found = []
    for label, pattern in LABELED:
        text, n = pattern.subn(rf"\g<1>〔{label}已隐藏〕", text)
        if n:
            found.append(label)
    for label, pattern in PATTERNS:
        text, n = pattern.subn(f"〔{label}已隐藏〕", text)
        if n:
            found.append(label)
    return text, [label for label, _ in PATTERNS if label in found]
