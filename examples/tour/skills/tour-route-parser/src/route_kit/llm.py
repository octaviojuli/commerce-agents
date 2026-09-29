"""Typed model calls through an Anthropic-compatible endpoint, with a local result cache.

Configuration comes from an env file or the process: ``ANTHROPIC_API_KEY``,
``ANTHROPIC_BASE_URL`` (optional), and ``ROUTE_KIT_MODEL`` or ``TOUR_MODEL``. Supplier text is
sent as fenced data; the model answers only through one forced tool. Results are cached by
a hash of (model, prompt, input, schema) so a rerun only pays for what changed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
from pathlib import Path

from pydantic import BaseModel, ValidationError


class ModelError(RuntimeError):
    pass


def load_env(path: Path | None) -> dict:
    """Process environment, overridden by an explicit env file: a file names one model setup."""
    values = dict(os.environ)
    if path and path.exists():
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.removeprefix("export ").partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def fence(data) -> str:
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


class Model:
    def __init__(self, settings: dict, cache_dir: Path, *, max_tokens: int = 8000):
        import anthropic

        self.name = settings.get("ROUTE_KIT_MODEL") or settings.get("TOUR_MODEL")
        key = settings.get("ANTHROPIC_API_KEY")
        if not self.name or not key:
            raise ModelError("MODEL_NOT_CONFIGURED")
        self.client = anthropic.Anthropic(
            api_key=key,
            base_url=settings.get("ANTHROPIC_BASE_URL") or None,
            timeout=180,
            max_retries=2,
        )
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.base_url = settings.get("ANTHROPIC_BASE_URL") or ""
        self.max_tokens = max_tokens
        self.calls = 0
        self.cached = 0
        self._lock = threading.Lock()

    def _key(self, prompt: str, data: dict, schema: type[BaseModel], images: tuple = ()) -> str:
        pictures = [hashlib.sha256(blob).hexdigest() for _, blob in images]
        blob = json.dumps(
            [self.name, self.base_url, prompt, data, schema.model_json_schema(), pictures],
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    def ask(
        self,
        prompt: str,
        data: dict,
        schema: type[BaseModel],
        *,
        max_tokens: int | None = None,
        accept=None,
        feedback: str = "",
        images: tuple[tuple[str, bytes], ...] = (),
    ):
        """One forced tool call. ``images`` are (media type, bytes) shown before the data."""
        key = self._key(prompt + feedback, data, schema, images)
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            try:
                cached = schema.model_validate_json(path.read_text())
            except (OSError, ValidationError):
                cached = None  # a truncated or stale cache file is a miss
            if cached is not None and (accept is None or accept(cached)):
                with self._lock:
                    self.cached += 1
                return cached
            path.unlink(missing_ok=True)  # an empty or unusable answer is never reused
        # Supplier text (and problems that quote it) stays fenced: "<" is escaped so no
        # text inside can close the fence.
        content = "<supplier_data>\n" + fence(data) + "\n</supplier_data>"
        if feedback:
            content += (
                "\n<previous_attempt_problems>\n"
                + fence(feedback)
                + "\n</previous_attempt_problems>"
            )
        blocks: list[dict] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media,
                    "data": base64.b64encode(blob).decode(),
                },
            }
            for media, blob in images
        ]
        blocks.append({"type": "text", "text": content})
        last = None
        for attempt in range(2):
            try:
                response = self.client.messages.create(
                    model=self.name,
                    max_tokens=max_tokens or self.max_tokens,
                    temperature=0,
                    thinking={"type": "disabled"},
                    system=prompt,
                    messages=[{"role": "user", "content": blocks}],
                    tools=[
                        {
                            "name": "submit_result",
                            "description": "Return the extracted structure.",
                            "input_schema": schema.model_json_schema(),
                        }
                    ],
                    tool_choice={"type": "tool", "name": "submit_result"},
                )
                with self._lock:
                    self.calls += 1
                calls = [b for b in response.content if b.type == "tool_use"]
                if len(calls) != 1:
                    raise ModelError("MODEL_NO_TOOL_CALL")
                if response.stop_reason == "max_tokens":
                    raise ModelError("MODEL_OUTPUT_TRUNCATED")
                result = schema.model_validate(calls[0].input)
                if accept is not None and not accept(result):
                    raise ModelError("MODEL_EMPTY_RESULT")
                partial = path.with_suffix(f".{threading.get_ident()}.tmp")
                partial.write_text(result.model_dump_json())
                os.replace(partial, path)
                return result
            except (ValidationError, ModelError) as error:
                last = error
            except Exception as error:  # network or provider errors: back off once
                last = error
                time.sleep(3 * (attempt + 1))
        raise ModelError(f"MODEL_FAILED: {type(last).__name__}: {str(last)[:300]}")
