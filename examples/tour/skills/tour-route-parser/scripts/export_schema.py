"""Write schema/route-content.schema.json from the pydantic contract."""

import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from route_kit.models import SCHEMA, RouteContent  # noqa: E402

schema = RouteContent.model_json_schema(by_alias=True)
schema["title"] = "RouteContent"
schema["$comment"] = f"{SCHEMA}: one itinerary attachment read into evidence-linked fields"
(root / "schema" / "route-content.schema.json").write_text(
    json.dumps(schema, ensure_ascii=False, indent=1)
)
print("written")
