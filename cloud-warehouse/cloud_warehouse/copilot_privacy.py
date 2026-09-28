"""Customer document encryption, masked reads and explicit owner-authorized disclosure."""

import base64
from copy import deepcopy

from sqlalchemy import text

from . import copilot_crypto
from .changes import Conflict, audit
from .persistence import Forbidden, transaction


def mask(value):
    return "•" * max(4, len(value) - 4) + value[-4:] if value else ""


def decode(body, identifier):
    result = deepcopy(body)
    for index, traveler in enumerate(result.get("travelers", [])):
        cipher = traveler.pop("document_cipher", None)
        if cipher:
            traveler["document_number"] = copilot_crypto.decrypt(
                base64.b64decode(cipher), f"customer:{identifier}:{index}"
            ).decode()
    return result


def seal(body, identifier, previous=None):
    result = deepcopy(body)
    before = decode(previous, identifier) if previous else {"travelers": []}
    for index, traveler in enumerate(result.get("travelers", [])):
        number = traveler.get("document_number", "")
        if "•" in number:
            old = before["travelers"][index] if index < len(before["travelers"]) else {}
            if traveler["name"] != old.get("name") or number != mask(
                old.get("document_number", "")
            ):
                raise Conflict("旅客顺序或姓名已变，请重新填写证件号")
            number = old["document_number"]
        traveler["document_number"] = ""
        if number:
            traveler["document_cipher"] = base64.b64encode(
                copilot_crypto.encrypt(number.encode(), f"customer:{identifier}:{index}")
            ).decode()
    return result


def view(row):
    result = dict(row)
    body = deepcopy(row["body"])
    for index, traveler in enumerate(body.get("travelers", [])):
        cipher = traveler.pop("document_cipher", None)
        if cipher:
            number = copilot_crypto.decrypt(
                base64.b64decode(cipher), f"customer:{row['id']}:{index}"
            ).decode()
            traveler["document_number"] = mask(number)
        else:
            traveler["document_number"] = mask(traveler.get("document_number", ""))
    result["body"] = body
    return result


def reveal(engine, actor, identifier, index):
    with transaction(engine, actor) as conn:
        row = (
            conn.execute(text("SELECT body FROM advisor_customer WHERE id=:id"), {"id": identifier})
            .mappings()
            .one_or_none()
        )
        if row is None or index >= len(row["body"].get("travelers", [])) or index < 0:
            raise Forbidden("旅客不存在或不属于当前顾问")
        audit(conn, actor, "advisor.document_revealed", identifier, {"traveler_index": index})
        return {
            "document_number": decode(row["body"], identifier)["travelers"][index][
                "document_number"
            ]
        }
