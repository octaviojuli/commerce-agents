"""Itinerary attachments (PDF, DOCX) to evidence-linked route content and a detail page.

Pipeline: ``reader.read`` → (``vision.transcribe``) → ``segment.segment`` →
``extract.extract`` → ``models.RouteContent`` → ``render.page``. ``scripts/run.py`` drives it
for one file or a corpus.
"""

from .models import SCHEMA, RouteContent

__all__ = ["SCHEMA", "RouteContent"]
