"""Configured text-only model calls for evidence-bound itinerary editing."""

import json
import os

import anthropic
from pydantic import ValidationError


class ModelEditor:
    def __init__(self, settings=None):
        settings = os.environ if settings is None else settings
        self.model = settings.get("TOUR_CONTENT_MODEL") or settings.get("TOUR_MODEL")
        self.review_model = settings.get("TOUR_REVIEW_MODEL") or self.model
        self.sample_percent = max(
            0, min(100, int(settings.get("TOUR_REVIEW_SAMPLE_PERCENT", "10")))
        )
        self.extraction_model = settings.get("TOUR_EXTRACTION_MODEL")
        key = settings.get("ANTHROPIC_API_KEY")
        if not self.model or not key:
            raise ValueError("ROUTE_EDITOR_NOT_CONFIGURED")
        self.client = anthropic.Anthropic(
            api_key=key,
            base_url=settings.get("ANTHROPIC_BASE_URL") or None,
            timeout=45,
            max_retries=0,
        )

    def __call__(self, prompt, data, schema):
        try:
            response = self.client.messages.create(
                model=self.review_model
                if schema.__name__ == "FactReview"
                else self.extraction_model
                if schema.__name__ == "Candidate" and self.extraction_model
                else self.model,
                max_tokens=12000 if schema.__name__ == "Candidate" else 6500,
                temperature=0,
                thinking={"type": "disabled"},
                system=prompt,
                messages=[
                    {
                        "role": "user",
                        "content": "<supplier_data>\n"
                        + json.dumps(data, ensure_ascii=False)
                        + "\n</supplier_data>",
                    }
                ],
                tools=[
                    {
                        "name": "submit_result",
                        "description": "Return the requested typed editing or review result.",
                        "input_schema": schema.model_json_schema(),
                    }
                ],
                tool_choice={"type": "tool", "name": "submit_result"},
            )
            calls = [b for b in response.content if b.type == "tool_use"]
            if (
                len(calls) != 1
                or calls[0].name != "submit_result"
                or response.stop_reason == "max_tokens"
            ):
                raise ValueError("EDITOR_RESPONSE_SHAPE")
            return schema.model_validate(calls[0].input)
        except anthropic.APIError:
            raise ValueError("EDITOR_MODEL_UNAVAILABLE") from None
        except (ValidationError, json.JSONDecodeError):
            raise ValueError("EDITOR_SCHEMA_INVALID") from None

    def close(self):
        self.client.close()
