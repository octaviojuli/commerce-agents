# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The .pdf 行程附件 read as document lines: pdftotext's layout text normalised into the
.docx row shape, the three .pdf day-header shapes rewritten, a scan refused."""

import pytest

from tour.api.itinerary_source import document_lines, split_days
from tour.api.pdf_source import PDFTOTEXT, ImageOnlyPdf, normalise_lines
from tour.api.route_parser import _meals


def test_column_gaps_become_cells_and_dashes_one_dash():
    lines = normalise_lines(
        [
            "用餐        早：自理         中：自理        晚：自理",
            "住宿   自理      交通：旅游用车",
            "",
        ]
    )
    assert lines == ["用餐 || 早：自理 || 中：自理 || 晚：自理", "住宿 || 自理 || 交通：旅游用车"]
    assert normalise_lines(["DAY   上海⸺科伦坡", " 1    餐食：X"])[0].startswith(
        "第1天 上海-科伦坡"
    )


def test_a_drawn_day_number_is_read_from_the_bare_number_line_after_it():
    """``DAY   上海⸺科伦坡`` with the number as a picture, ``1    餐食：X    参考航班：…`` under
    it, a 时差 note between them on the first day, a wrapped title on the sixth."""
    lines = normalise_lines(
        [
            "DAY     昆明⸺科伦坡                   参考航班：昆明-科伦坡 MU713 18:50-21:10",
            "                                 时差：斯里兰卡比中国晚 2.5 小时",
            " 1      餐食：X",
            "        交通：旅游用车",
            "从昆明出发。",
            " DAY    西南部海滨（",
            "             - 车程约2.5小时）科伦坡-科伦坡机场乘返程航班",
            "  6     餐食：早午                酒店：X",
        ]
    )
    assert lines == [
        "第1天 昆明-科伦坡 || 参考航班：昆明-科伦坡 MU713 18:50-21:10",
        "餐食：X",
        "时差：斯里兰卡比中国晚 2.5 小时",
        "交通：旅游用车",
        "从昆明出发。",
        "第6天 西南部海滨（- 车程约2.5小时）科伦坡-科伦坡机场乘返程航班",
        "餐食：早午",
        "酒店：X",
    ]


def test_a_day_written_as_a_bare_number_needs_its_row_to_follow():
    """``1`` alone, then the 用餐 row, is a day; a lone ``1`` before prose (a page number) is not;
    ``2       西南部海滨-科伦坡`` is a day by its title; a DAY line with no number in sight
    counts."""
    assert normalise_lines(
        ["     1", "用餐   早：自理", " 2       西南部海滨-科伦坡", "早餐后。"]
    ) == [
        "第1天",
        "用餐 || 早：自理",
        "第2天 西南部海滨-科伦坡",
        "早餐后。",
    ]
    assert normalise_lines(["1", "斯里兰卡旧称锡兰。"]) == ["1", "斯里兰卡旧称锡兰。"]
    assert normalise_lines(["DAY   上海-科伦坡", "抵达。", "DAY   科伦坡-丹布勒"]) == [
        "DAY-1 上海-科伦坡",
        "抵达。",
        "DAY-2 科伦坡-丹布勒",
    ]
    assert normalise_lines(["酒店：A / B", "或同级"]) == ["酒店：A / B 或同级"]


def test_split_days_reads_the_labelled_cells_of_the_pdf_rows():
    days = split_days(
        [
            "第1天 昆明-科伦坡 || 参考航班：昆明-科伦坡 MU713 18:50-21:10",
            "餐食：X",
            "交通：旅游用车 || 酒店：Goldi Sands / Arie Lagoon 或同级",
            "从昆明出发。",
            "第 02 天 || 托莱多",
            "餐：/ || 住：飞机上 || 行：无",
        ]
    )
    d1, d2 = days
    assert d1.title == "昆明-科伦坡" and d1.meals == "X"
    assert d1.hotel == "Goldi Sands / Arie Lagoon 或同级"
    assert d1.text == "参考航班：昆明-科伦坡 MU713 18:50-21:10；从昆明出发。"
    assert d2.day_no == 2 and d2.meals == "/" and d2.hotel == "飞机上"


def test_the_short_meal_forms():
    m = _meals("早午晚")
    assert (m.breakfast.included, m.lunch.included, m.dinner.included) == (True, True, True)
    m = _meals("早、/、/")
    assert (m.breakfast.included, m.lunch.included, m.dinner.included) == (True, False, False)
    m = _meals("X")
    assert (m.breakfast.included, m.lunch.included, m.dinner.included) == (False, False, False)
    assert _meals("酒店内早餐，午餐当地餐").breakfast.included is None


def _pdf(text_lines: list[str]) -> bytes:
    """A one-page PDF with Helvetica text, one line per entry, as pdftotext reads it."""
    content = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({t}) Tj T*" for t in text_lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        " /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = "%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode("latin-1")


@pytest.mark.skipif(PDFTOTEXT is None, reason="pdftotext is not installed")
def test_document_lines_reads_a_pdf_and_refuses_a_scan(monkeypatch):
    monkeypatch.setattr("tour.api.pdf_source.MIN_TEXT_CHARS", 0)
    assert document_lines(_pdf(["DAY 1 Shanghai - Colombo", "Hotel: Goldi Sands"])) == [
        "DAY 1 Shanghai - Colombo",
        "Hotel: Goldi Sands",
    ]
    monkeypatch.setattr("tour.api.pdf_source.MIN_TEXT_CHARS", 300)
    with pytest.raises(ImageOnlyPdf):
        document_lines(_pdf(["DAY 1 Shanghai - Colombo"]))
