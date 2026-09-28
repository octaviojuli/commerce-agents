"""Travellers' document scans: encrypted at rest, read locally, checked, confirmed by the advisor.

Recognition reads a passport's machine-readable zone with its check digits. No image, number
or recognized text is sent to a model or an external service.
"""

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import db, store
from .need import Need

MAX_BYTES = 12 * 1024 * 1024
TYPES = {"image/jpeg", "image/png", "application/pdf"}


def _key():
    raw = os.environ.get("ADVISOR_MATERIAL_KEY", "")
    key = base64.b64decode(raw) if raw else b""
    if len(key) != 32:
        raise RuntimeError("ADVISOR_MATERIAL_KEY 必须是 32 字节的 base64 密钥")
    return key


def seal(data: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(_key()).encrypt(nonce, data, b"advisor-material")


def unseal(blob: bytes) -> bytes:
    return AESGCM(_key()).decrypt(blob[:12], blob[12:], b"advisor-material")


def _check(value: str, digit: str) -> bool:
    weights = (7, 3, 1)

    def v(c):
        return int(c) if c.isdigit() else 0 if c == "<" else ord(c) - 55

    return str(sum(v(c) * weights[i % 3] for i, c in enumerate(value)) % 10) == digit


def _date(value: str, *, birthday=False):
    try:
        yy, mm, dd = int(value[:2]), int(value[2:4]), int(value[4:6])
    except ValueError:
        return None
    this = date.today().year % 100
    century = 1900 if birthday and yy > this else 2000
    try:
        return date(century + yy, mm, dd)
    except ValueError:
        return None


def read_mrz(text: str):
    lines = [re.sub(r"\s+", "", line.upper()) for line in text.splitlines()]
    for i in range(len(lines) - 1):
        first, second = lines[i], lines[i + 1]
        if not first.startswith("P") or len(second) < 44 or len(first) < 30:
            continue
        second = second[:44]
        number, number_check = second[0:9], second[9]
        born, born_check = second[13:19], second[19]
        expiry, expiry_check = second[21:27], second[27]
        names = first[5:].split("<<", 1)
        surname = names[0].replace("<", " ").strip()
        given = names[1].replace("<", " ").strip() if len(names) > 1 else ""
        birthday, expires = _date(born, birthday=True), _date(expiry)
        return {
            "name": f"{surname} {given}".strip(),
            "number": number.replace("<", ""),
            "birthday": birthday.isoformat() if birthday else None,
            "expiry": expires.isoformat() if expires else None,
            "checks": {
                "number": _check(number, number_check),
                "birthday": _check(born, born_check),
                "expiry": _check(expiry, expiry_check),
            },
        }
    return None


def recognize(body: bytes, media_type: str):
    if not shutil.which("tesseract"):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / ("scan.pdf" if media_type == "application/pdf" else "scan.img")
        path.write_bytes(body)
        target = path
        if media_type == "application/pdf" and shutil.which("pdftoppm"):
            subprocess.run(
                [
                    "pdftoppm",
                    "-f",
                    "1",
                    "-l",
                    "1",
                    "-r",
                    "300",
                    "-png",
                    str(path),
                    str(Path(tmp) / "p"),
                ],
                check=False,
                capture_output=True,
                timeout=60,
            )
            pages = sorted(Path(tmp).glob("p*.png"))
            if pages:
                target = pages[0]
        out = subprocess.run(
            ["tesseract", str(target), "stdout", "-l", "eng", "--psm", "6"],
            check=False,
            capture_output=True,
            timeout=90,
        )
        return read_mrz(out.stdout.decode("utf-8", errors="replace"))


def slots(need: Need):
    """One slot per traveller, in party order: adults, children, seniors."""
    party = need.get("party")
    if not party:
        return []
    out = [{"slot": i, "role": "成人", "age": None} for i in range(party.adults or 0)]
    out += [
        {"slot": len(out) + i, "role": "儿童", "age": c.age}
        for i, c in enumerate(party.children or [])
    ]
    base = len(out)
    out += [{"slot": base + i, "role": "老人", "age": s.age} for i, s in enumerate(party.seniors)]
    return out


def upload(engine, owner, root: Path, deal_id, filename, media_type, body: bytes):
    if media_type not in TYPES:
        raise ValueError("只收 JPG、PNG 或 PDF 扫描件")
    if len(body) > MAX_BYTES:
        raise ValueError("单个文件不能超过 12 MB")
    with engine.connect() as conn:
        deal = store.deal(conn, owner, deal_id)
    recognized = recognize(body, media_type)
    directory = root / str(owner.org_id) / str(deal_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / os.urandom(12).hex()
    path.write_bytes(seal(body))
    need = Need.model_validate(deal["need"])
    slot = match_slot(recognized, need, taken=_taken(engine, owner, deal_id))
    with engine.begin() as conn:
        row = store.add(
            conn,
            owner,
            db.documents,
            deal_id=deal_id,
            traveler=slot,
            filename=filename[:200],
            media_type=media_type,
            path=str(path),
            recognized=base64.b64encode(seal(json.dumps(recognized or {}).encode())).decode()
            if recognized
            else None,
            status="review",
        )
    return view(row, deal)


def _taken(engine, owner, deal_id):
    with engine.connect() as conn:
        return {
            r["traveler"]
            for r in store.rows(conn, owner, db.documents, deal_id)
            if r["traveler"] is not None
        }


def age_on(birthday: str, when: date):
    b = date.fromisoformat(birthday)
    return when.year - b.year - ((when.month, when.day) < (b.month, b.day))


def match_slot(recognized, need: Need, taken=()):
    if not recognized or not recognized.get("birthday"):
        return None
    age = age_on(recognized["birthday"], date.today())
    for s in slots(need):
        if s["slot"] in taken:
            continue
        if s["role"] == "儿童" and s["age"] is not None and abs(s["age"] - age) <= 1:
            return s["slot"]
        if s["role"] == "老人" and age >= 60:
            return s["slot"]
        if s["role"] == "成人" and 18 <= age < 60:
            return s["slot"]
    return None


def view(row, deal):
    recognized = (
        json.loads(unseal(base64.b64decode(row["recognized"]))) if row.get("recognized") else None
    )
    checks = []
    if recognized:
        departure = deal.get("departure") or {}
        back = departure.get("return_date") or departure.get("date")
        if recognized.get("expiry") and back:
            months = (date.fromisoformat(recognized["expiry"]) - date.fromisoformat(back)).days / 30
            checks.append(
                {
                    "ok": months >= 6,
                    "text": "护照回程后仍有 6 个月以上有效期"
                    if months >= 6
                    else "护照在回程后 6 个月内到期，需要换发",
                }
            )
        if not all((recognized.get("checks") or {}).values()):
            checks.append({"ok": False, "text": "机读区校验位没对上，请对照原件核对"})
        need = Need.model_validate(deal["need"])
        slot = next((s for s in slots(need) if s["slot"] == row["traveler"]), None)
        if slot and slot["age"] is not None and recognized.get("birthday"):
            age = age_on(recognized["birthday"], date.today())
            checks.append(
                {
                    "ok": abs(age - slot["age"]) <= 1,
                    "text": f"{age} 岁"
                    + (
                        " · 与报价一致"
                        if abs(age - slot["age"]) <= 1
                        else f" · 报价按 {slot['age']} 岁"
                    ),
                }
            )
    number = (recognized or {}).get("number") or ""
    return {
        "id": str(row["id"]),
        "traveler": row["traveler"],
        "filename": row["filename"],
        "status": row["status"],
        "name": (recognized or {}).get("name"),
        "number": number[:2] + "****" + number[-4:] if len(number) >= 6 else None,
        "birthday": (recognized or {}).get("birthday"),
        "expiry": (recognized or {}).get("expiry"),
        "checks": checks,
        "recognized": bool(recognized),
    }


def overview(engine, owner, deal_id):
    with engine.connect() as conn:
        deal = store.deal(conn, owner, deal_id)
        rows = store.rows(conn, owner, db.documents, deal_id, order=db.documents.c.created_at)
    need = Need.model_validate(deal["need"])
    docs = [view(r, deal) for r in rows]
    travellers = []
    for s in slots(need):
        doc = next((d for d in docs if d["traveler"] == s["slot"]), None)
        travellers.append(
            {
                **s,
                "document": doc,
                "state": "confirmed"
                if doc and doc["status"] == "confirmed"
                else "review"
                if doc
                else "missing",
            }
        )
    done = sum(1 for t in travellers if t["state"] == "confirmed")
    return {
        "travellers": travellers,
        "unmatched": [d for d in docs if d["traveler"] is None],
        "done": done,
        "total": len(travellers),
    }


def confirm(engine, owner, deal_id, document_id, traveler=None):
    with engine.begin() as conn:
        row = store.one(conn, owner, db.documents, document_id, deal_id=deal_id)
        if row["deal_id"] != deal_id:
            raise store.NotFound("证件不存在")
        values = {"status": "confirmed"}
        if traveler is not None:
            values["traveler"] = traveler
        store.change(conn, owner, db.documents, document_id, **values)
    return overview(engine, owner, deal_id)
