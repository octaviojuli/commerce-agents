"""Local-only material recognition: unconfirmed candidates, never automatic traveler edits."""

import base64
import json
import re
import shutil
import subprocess
import tempfile
from datetime import date
from io import BytesIO
from pathlib import Path

from PIL import Image

from . import copilot_assets, copilot_crypto
from . import copilot_records as records
from .persistence import transaction


def _check(value, digit):
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return digit.isdigit() and sum(
        (0 if c == "<" else alphabet.find(c)) * [7, 3, 1][i % 3] for i, c in enumerate(value)
    ) % 10 == int(digit)


def _date(value, *, birthday=False):
    try:
        year = int(value[:2]) + 2000
        if birthday and year > date.today().year:
            year -= 100
        return date(year, int(value[2:4]), int(value[4:6])).isoformat()
    except (ValueError, TypeError):
        return None


def candidates(text_value):
    """Recognize bounded passport MRZ or identity-number fields; all require a human review."""
    lines = [re.sub(r"\s+", "", line.upper()) for line in text_value.splitlines()]
    for n, line in enumerate(lines[:-1]):
        following = lines[n + 1]
        if (
            line.startswith("P<")
            and len(line) == 44
            and len(following) == 44
            and re.fullmatch(r"[A-Z0-9<]{44}", following)
        ):
            name = " ".join(line[5:].replace("<<", " ").replace("<", " ").split())
            checks = {
                "document_number": _check(following[:9], following[9]),
                "birthday": _check(following[13:19], following[19]),
                "document_expiry": _check(following[21:27], following[27]),
            }
            return [
                {
                    "name": name,
                    "birthday": _date(following[13:19], birthday=True),
                    "document_type": "passport",
                    "document_number": following[:9].replace("<", ""),
                    "document_expiry": _date(following[21:27]),
                    "confirmed": False,
                    "checks": checks,
                }
            ]
    numbers = re.findall(
        r"(?<![0-9])[1-9][0-9]{16}[0-9X](?![0-9])", re.sub(r"[ \t]", "", text_value.upper())
    )
    result = []
    for number in numbers[:3]:
        try:
            born = date(int(number[6:10]), int(number[10:12]), int(number[12:14])).isoformat()
        except ValueError:
            continue
        checksum = (
            "10X98765432"[
                sum(
                    int(v) * w
                    for v, w in zip(
                        number[:17],
                        [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2],
                        strict=True,
                    )
                )
                % 11
            ]
            == number[-1]
        )
        result.append(
            {
                "name": "",
                "birthday": born,
                "document_type": "identity",
                "document_number": number,
                "document_expiry": None,
                "confirmed": False,
                "checks": {"document_number": checksum},
            }
        )
    return result


def recognize_bytes(body, media_type):
    # Private temporary files are removed on every completion/failure. No model or
    # external OCR endpoint receives an image, a document number or recognized text.
    if media_type != "application/pdf":
        with Image.open(BytesIO(body)) as source:
            if source.width * source.height > 20_000_000 or max(source.size) > 10000:
                return []
            source.verify()
    with tempfile.TemporaryDirectory(prefix="advisor-material-") as directory:
        root = Path(directory)
        original = root / "input"
        original.write_bytes(body)
        original.chmod(0o600)
        text_value = ""
        if media_type == "application/pdf" and shutil.which("pdftotext"):
            output = subprocess.run(
                ["pdftotext", "-f", "1", "-l", "1", "-layout", str(original), "-"],
                capture_output=True,
                timeout=10,
                check=False,
            )
            text_value = output.stdout[:30000].decode("utf-8", errors="replace")
            recognized = candidates(text_value)
            if recognized:
                return recognized
        if not shutil.which("tesseract"):
            return []
        if media_type == "application/pdf":
            if not shutil.which("pdftoppm"):
                return []
            subprocess.run(
                [
                    "pdftoppm",
                    "-f",
                    "1",
                    "-l",
                    "1",
                    "-scale-to",
                    "2000",
                    "-singlefile",
                    "-png",
                    str(original),
                    str(root / "page"),
                ],
                capture_output=True,
                timeout=12,
                check=True,
            )
            original = root / "page.png"
        output = subprocess.run(
            ["tesseract", str(original), "stdout", "-l", "eng", "--psm", "6"],
            capture_output=True,
            timeout=15,
            check=True,
            env=None,
        )
        return candidates(output.stdout[:30000].decode("utf-8", errors="replace"))


def recognize(engine, actor, store, identifier, asset_id, request):
    from sqlalchemy import text

    from .persistence import Forbidden

    with transaction(engine, actor) as conn:
        records.checked(conn, identifier, request.expected_version)
        owner = conn.scalar(
            text("SELECT deal_id FROM advisor_asset WHERE id=:id"), {"id": asset_id}
        )
        if owner != identifier:
            raise Forbidden("材料不属于此跟单")
    material = copilot_assets.download(engine, actor, store, asset_id)
    try:
        result = recognize_bytes(material["body"], material["media_type"])
        status = "review_required" if result else "manual_required"
    except (OSError, ValueError, Image.DecompressionBombError, subprocess.SubprocessError):
        result, status = [], "manual_required"
    with transaction(engine, actor) as conn:
        _, version = records.checked(conn, identifier, request.expected_version)
        return records.append(
            conn,
            actor,
            identifier,
            "material_review",
            {
                "asset_id": str(asset_id),
                "encrypted_candidates": base64.b64encode(
                    copilot_crypto.encrypt(json.dumps(result).encode(), asset_id)
                ).decode(),
                "status": status,
                "notice": "本地识别结果仅供人工核对。未找到可识别的证件区时，请对照原件手工录入。",
            },
            version,
            str(request.request_id),
        )
