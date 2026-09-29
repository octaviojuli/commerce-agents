"""Read a route's current publication using its existing departure ownership."""

from sqlalchemy import text


def select_publication(conn, current, departure_id):
    # Content dates, cities and day counts describe the itinerary. They never
    # establish or revoke the upstream route/departure relationship.
    if departure_id is not None:
        owned = conn.execute(
            text("SELECT id FROM departure WHERE id=:id AND product_id=:product"),
            {"id": departure_id, "product": current["product_id"]},
        ).scalar_one_or_none()
        if owned is None:
            return None  # Live departure RLS and product ownership still apply.
    return current
