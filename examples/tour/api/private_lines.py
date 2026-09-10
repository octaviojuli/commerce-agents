# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Which 线路 are not on general sale, and so are not a search result.

The agency's catalog carries, beside the lines it sells to anyone, the 包团 a customer chartered,
the 会销 one salesperson runs for their own group and the 定制 built for one party. A search for
欧洲 should not offer another advisor's 会销 as a choice. The ERP has no field for it — its
``route/list`` names, prices and tags a line and says nothing about who may sell it — so the
name is read for the words the agency's editors put there, and ``data/private-lines.json``
holds those words, to be trimmed or added to without a code change.

``saleType`` is the field asked of the ERP (``docs/erp-contract.md``): when it arrives on the
record, a value outside the file's ``public_sale_types`` makes the line private whatever the
name says, and the words become the fallback for a record that carries none.

A private line is kept out of search results and out of the boot listing snapshot, and stays
reachable by its id and by its full name: the advisor who runs that 会销 can open it, and
``line_type`` on the record says what it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from demo_common.storefront_fixtures import example_data_dir, load_json

from .erp_client import RouteRecord

DATA_DIR = example_data_dir(__file__)
RULES_FILE = "private-lines.json"


@dataclass(frozen=True)
class PrivateLineRules:
    """The words a private line's name carries, and the ``saleType`` values that mean public."""

    name_keywords: tuple[str, ...]
    public_sale_types: frozenset[str]

    def line_type(self, record: RouteRecord) -> str:
        """What the line is when it is not on general sale — the ERP's own ``saleType``, else
        the first word of the name that says so — and ``""`` for a line anyone may sell."""
        sale_type = record.sale_type.strip()
        if sale_type and sale_type not in self.public_sale_types:
            return sale_type
        if sale_type:
            return ""
        return next((word for word in self.name_keywords if word in record.route_name), "")

    def is_private(self, record: RouteRecord) -> bool:
        return bool(self.line_type(record))


def load_private_line_rules(data_dir: Path = DATA_DIR) -> PrivateLineRules:
    """``data/private-lines.json``, read once per process; a file with no keywords makes
    every line public."""
    raw = load_json(data_dir, RULES_FILE)
    return PrivateLineRules(
        name_keywords=tuple(str(word) for word in raw.get("name_keywords", []) if word),
        public_sale_types=frozenset(str(value) for value in raw.get("public_sale_types", [""])),
    )
