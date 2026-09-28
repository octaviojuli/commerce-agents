import json
from types import SimpleNamespace

from cloud_warehouse.route_doc import Day, Quality, RouteDoc, Source, Summary
from cloud_warehouse.route_editing import EditedDay, FactReview
from tour.api.route_document_workflow import edit_step


def test_skill_resumes_and_never_overwrites_original(tmp_path, monkeypatch, capsys):
    doc = RouteDoc(
        route_id=1,
        route_code="ACME",
        name="ACME",
        department="",
        summary=Summary(days=2),
        days=[
            Day(
                day=n,
                title="ACME 港-20KM-ACME 城",
                places=["ACME 港", "ACME 城"],
                text="ACME 宫外观1小时",
            )
            for n in [1, 2]
        ],
        quality=Quality(completeness=1),
        source=Source(
            attachment_name="ACME.docx", attachment_url="", bytes=1, parsed_at=None, parser="ACME"
        ),
    )
    raw = doc.model_dump_json()
    (tmp_path / "candidate.json").write_text(raw)
    config = tmp_path / "model.env"
    config.write_text("TOUR_CONTENT_MODEL=ACME\nANTHROPIC_API_KEY=fictional")
    calls = []

    class Model:
        model = "ACME"

        def __init__(self, *args):
            pass

        def close(self):
            pass

        def __call__(self, prompt, data, schema):
            calls.append(schema)
            if schema is FactReview:
                return schema(
                    unsupported_claims=[], omitted_facts_or_conditions=[], changed_meanings=[]
                )
            return EditedDay(
                summary="ACME 宫外观1小时",
                blocks=[
                    {
                        "source_ids": [1],
                        "type": "visit",
                        "title": "ACME 宫外观",
                        "paragraphs": ["ACME 宫外观1小时"],
                    }
                ],
            )

    monkeypatch.setattr("tour.api.route_editor_model.ModelEditor", Model)
    args = SimpleNamespace(model_config=config, output=tmp_path)
    edit_step(args)
    assert json.loads(capsys.readouterr().out)["status"] == "editing"
    edit_step(args)
    assert json.loads(capsys.readouterr().out)["status"] == "complete"
    edit_step(args)
    assert len(calls) == 4
    assert (tmp_path / "candidate.json").read_text() == raw
    edited = json.loads((tmp_path / "edited-candidate.json").read_text())
    assert edited["days"][0]["title"] == "ACME 港 → ACME 城"
    assert edited["days"][0]["travel_reference"] == doc.days[0].title
