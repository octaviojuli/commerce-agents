# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The .pdf 行程附件 read as document lines: pdftotext's layout text normalised into the
.docx row shape, the three .pdf day-header shapes rewritten, a scan refused."""

import subprocess

import pytest

from tour.api.itinerary_source import document_lines, split_days
from tour.api.pdf_source import (
    PDFTOTEXT,
    ImageOnlyPdf,
    LocatedLine,
    PdfReadError,
    normalise_lines,
    pdf_lines,
    pdf_text,
)
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
    return _pdf_pages([text_lines])


def _pdf_pages(pages: list[list[str]]) -> bytes:
    """A real multipage ACME fixture, including blank physical pages."""
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages)))
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for index, lines in enumerate(pages):
        content = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({t}) Tj T*" for t in lines) + " ET"
        objects.extend(
            [
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {5 + 2 * index} 0 R"
                " /Resources << /Font << /F1 3 0 R >> >> >>",
                f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
            ]
        )
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


@pytest.mark.parametrize("mode", ["layout", "default"])
@pytest.mark.skipif(PDFTOTEXT is None, reason="pdftotext is not installed")
def test_pdf_physical_pages_include_blank_pages_and_repeated_text(monkeypatch, mode):
    monkeypatch.setattr("tour.api.pdf_source.MIN_TEXT_CHARS", 0)
    lines = pdf_lines(_pdf_pages([["ACME same"], [], ["ACME same", "D1 ACME city"]]), mode)
    assert lines == ["ACME same", "ACME same", "D1 ACME city"]
    assert [line.pages for line in lines] == [(1,), (3,), (3,)]


def test_page_evidence_survives_normalized_cross_page_merges_and_reordering():
    def read(values):
        return normalise_lines([LocatedLine(value, (page,)) for page, value in values])

    lines = read([(1, "第1天"), (2, "ACME 城市"), (2, "酒店：ACME"), (3, "或同级")])
    assert lines == ["第1天 ACME 城市", "酒店：ACME 或同级"]
    assert [line.pages for line in lines] == [(1, 2), (2, 3)]
    lines = read([(1, "DAY   ACME（"), (2, "- 城市）"), (2, "备注"), (3, "1   餐食：早")])
    assert lines == ["第1天 ACME（- 城市）", "餐食：早", "备注"]
    assert [line.pages for line in lines] == [(1, 2, 3), (3,), (2,)]
    lines = read(
        [
            (1, "第1天 ACME 城市"),
            (1, "游览城市。"),
            (1, "餐：早 午 晚 住：ACME 行：车"),
            (2, "另一城市"),
            (3, "长段行程，游览景点。"),
            (3, "第2天"),
            (4, "继续游览。"),
        ]
    )
    header = next(line for line in lines if line.startswith("第2天"))
    assert header == "第2天 另一城市" and header.pages == (2, 3)
    assert next(line for line in lines if line == "继续游览。").pages == (4,)


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


@pytest.mark.parametrize("failure", ["missing", "os", "timeout", "exit"])
def test_pdf_extraction_errors_are_classified_without_stderr(monkeypatch, failure):
    monkeypatch.setattr("tour.api.pdf_source.PDFTOTEXT", None if failure == "missing" else "acme")

    def run(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("acme", 60)
        if failure == "os":
            raise OSError("secret path")
        return subprocess.CompletedProcess(args, 1, b"", b"secret source text")

    monkeypatch.setattr("tour.api.pdf_source.subprocess.run", run)
    expected = {
        "missing": "DOCUMENT_PARSER_UNAVAILABLE",
        "os": "DOCUMENT_PARSER_UNAVAILABLE",
        "timeout": "DOCUMENT_PARSE_TIMEOUT",
        "exit": "DOCUMENT_PDF_READ_FAILED",
    }[failure]
    with pytest.raises(PdfReadError) as error:
        pdf_text(b"%PDF-ACME")
    assert str(error.value) == expected


def test_a_day_number_printed_against_the_middle_of_a_paragraph_starts_its_day():
    """The 行程详情 table of a .pdf writes the day number in a left column against the middle
    of the right column's paragraph. The paragraph above it is that day's too, so the header
    goes where the day began — after the 餐/住/行 row that closed the day before it."""
    lines = normalise_lines(
        [
            "第 02 天        蓝湾镇-雪顶镇",
            "                早餐后前往雪顶镇，沿途是连绵的葡萄园与教堂钟楼。",
            "餐：早 午 晚     住：雪顶镇山景酒店     行：旅游用车",
            "                上午前往山谷深处，白色的瀑布与周围的绿色植物搭配得恰到好处。",
            "第 03 天        随后乘车前往特别安排的老城散步，晚间返回酒店休息。",
            "餐：早 午 晚     住：老城精选酒店       行：旅游用车",
            "                老城-蓝湾镇",
            "                早上：酒店早餐",
            "第 04 天",
            "                晚上：晚餐自理后入住酒店。",
            "餐：早 / 晚      住：蓝湾镇精选酒店     行：旅游用车",
        ]
    )
    assert lines[3] == "第 03 天"
    assert lines[4].startswith("上午前往山谷深处")
    # A day number on a line of its own inside the block goes back to the block's title line.
    assert "第 04 天 老城-蓝湾镇" in lines
    days = split_days(lines)
    assert [day.day_no for day in days] == [2, 3, 4]
    assert "白色的瀑布" in days[1].text and "白色的瀑布" not in days[0].text
    assert days[2].title == "老城-蓝湾镇" and "晚餐自理" in days[2].text


def test_a_day_number_in_the_same_row_as_its_餐_line_still_starts_the_day():
    lines = normalise_lines(["第 12 天   餐：/    住：温暖的家   行：飞机"])
    assert lines == ["第 12 天", "餐：/ || 住：温暖的家 || 行：飞机"]
    day = split_days(lines)[0]
    assert (day.day_no, day.hotel, day.meals) == (12, "温暖的家", "/")


def test_a_page_edge_date_stamp_is_not_part_of_the_sentence():
    line = (
        "上午参观老城的主教座堂与回廊，随后前往佛罗9.23伦萨深度游，"
        "沿途约1.5小时车程，傍晚抵达酒店休息，晚餐后自由活动。"
    )
    assert "佛罗伦萨深度游" in normalise_lines([line])[0]
    assert "约1.5小时车程" in normalise_lines([line])[0]


def test_a_private_use_character_between_two_names_is_a_separator():
    assert normalise_lines(["第 2 天   上海米兰-310KM-佛罗伦萨"])[0].endswith(
        "上海-米兰-310KM-佛罗伦萨"
    )
    assert normalise_lines([" 全程国际连锁四星酒店"])[0] == "全程国际连锁四星酒店"


def test_a_justified_prose_line_keeps_its_words_whole():
    """A page that spreads a paragraph to the margin leaves runs of spaces inside the
    sentence; they are the printing and not a column, so the words stay whole (华尔街, not
    华尔 || 街)."""
    prose = (
        "早餐后前往华尔街金融区，这里是纽约乃至全球的金融中心，街口的铜牛雕像是游客必到的"
        "打卡点，我们在此停留拍照，随后步行前往   华尔   街铜牛与自由女神像观景平台。"
    )
    assert "||" not in normalise_lines([prose])[0]
    assert "华尔街铜牛" in normalise_lines([prose])[0].replace(" ", "")
    # A row that opens with a label is a row however long it runs.
    assert normalise_lines(["用餐   早：酒店内   中：自理   晚：当地餐"])[0].count("||") == 3


def test_a_numbered_line_under_the_terms_is_no_day_header():
    """``1 / 2 / 3`` under 服务所不含项目 is the numbering of the terms; a day header there
    would swallow the rest of the document."""
    lines = normalise_lines(
        [
            "     1",
            "用餐   早：自理",
            "游览老城。",
            "服 务 所 不 含 项 目",
            "     2   护照费用",
            "     3   个人消费",
        ]
    )
    assert lines[0] == "第1天"
    assert lines[-2:] == ["2 || 护照费用", "3 || 个人消费"]


def test_first_centered_day_stays_after_explicit_detail_heading():
    lines = normalise_lines(
        [
            LocatedLine("D1 ACME 概览", (2,)),
            LocatedLine("行程详情：", (3,)),
            LocatedLine("ACME 城市", (3,)),
            LocatedLine("集合后办理手续，随后乘车前往机场。", (3,)),
            LocatedLine("第 01 天   请按领队指引办理登记手续。", (3,)),
            LocatedLine("餐：/   住：飞机上   行：飞机", (3,)),
        ]
    )
    day = split_days(lines)[0]
    assert day.day_no == 1 and day.title == "ACME 城市"
    assert "集合后办理手续" in day.text
    assert "概览" not in day.text
    assert next(line for line in lines if line.startswith("第")).pages == (3,)


def test_sparse_image_pages_are_located_without_reading_image_text(monkeypatch):
    from types import SimpleNamespace

    from tour.api.pdf_source import unread_image_pages

    monkeypatch.setattr("tour.api.pdf_source.PDFIMAGES", "pdfimages")

    def run(command, **kwargs):
        assert command == ["pdfimages", "-list", "-"]
        assert kwargs["input"] == b"%PDF-ACME"
        return SimpleNamespace(
            returncode=0,
            stdout=b"page num type width height\n1 0 image 400 600\n2 1 image 300 200\n3 2 image 500 500\n",
        )

    monkeypatch.setattr("tour.api.pdf_source.subprocess.run", run)
    lines = [LocatedLine("ACME", (1,)), LocatedLine("正文" * 100, (2,))]
    assert unread_image_pages(b"%PDF-ACME", lines) == [1, 3]
