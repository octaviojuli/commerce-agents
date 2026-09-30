"""Fictional ACME samples; no model calls."""

import io
import json
import random
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from route_kit.extract import (  # noqa: E402
    check_day,
    check_route,
    clean_name,
    is_risky,
    missing_numbers,
)
from route_kit.llm import Model, fence  # noqa: E402
from route_kit.models import ChildNode, Day, DayOut, Item, Node, Price, RouteOut  # noqa: E402
from route_kit.reader import Line, _drop_repeated, read  # noqa: E402
from route_kit.render import page, write_index  # noqa: E402
from route_kit.segment import _header, segment  # noqa: E402
from route_kit.vision import PictureText, cover_image, transcribe  # noqa: E402

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def para(text):
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def row(*cells):
    return "<w:tr>" + "".join(f"<w:tc>{para(c)}</w:tc>" for c in cells) + "</w:tr>"


def docx(tmp_path, body):
    xml = f'<?xml version="1.0"?><w:document {W}><w:body>{body}</w:body></w:document>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    path = tmp_path / "acme.docx"
    path.write_bytes(buffer.getvalue())
    return path


def sample(tmp_path):
    body = (
        para("ACME 山海经典 3 天")
        + "<w:tbl>"
        + row("DAY-1", "上海-甲城", "早餐:× 午餐:× 晚餐:含", "3星")
        + row("DAY-2", "甲城", "早餐:含", "3星")
        + row("DAY-3", "甲城-上海", "早餐:含", "家")
        + "</w:tbl>"
        + "<w:tbl>"
        + row("第一天", "上海-300KM-甲城")
        + row(
            "【甲城 市区观光】(总观光时间不少于1小时)甲城是一座虚构的港口城市,老城沿海湾展开,街巷之间保留着早年的石砌码头与仓库。"
            "【ACME 灯塔(外观)】白色灯塔立在防波堤尽头,是港口的标志。【ACME 博物馆 含门票】馆藏丰富,展示港口的航海历史与造船工艺。"
        )
        + row("餐饮:早餐:自理 午餐:自理 晚餐:中式团餐")
        + row("住宿:甲城或周边")
        + row("第二天", "甲城")
        + row("全天自由活动。推荐自行前往【ACME 海滩】。")
        + row("住宿:甲城或周边")
        + row("第三天", "甲城-上海")
        + row("乘车返回上海,结束行程。")
        + row("费用包含")
        + row("1、全程住宿; 2、行程所列门票")
        + "</w:tbl>"
    )
    return docx(tmp_path, body)


def test_reader_keeps_body_order_and_table_rows(tmp_path):
    doc = read(sample(tmp_path))
    assert doc.media == "docx"
    assert doc.lines[0].text == "ACME 山海经典 3 天"
    assert any(
        line.kind == "row" and line.text.startswith("DAY-1 | 上海-甲城") for line in doc.lines
    )
    assert [line.id for line in doc.lines] == list(range(1, len(doc.lines) + 1))


def test_segment_prefers_detail_and_attaches_overview(tmp_path):
    layout = segment(read(sample(tmp_path)))
    assert [block.day for block in layout.days] == [1, 2, 3]
    units = {u.id: u.text for u in layout.units}
    assert units[layout.days[0].header_unit].startswith("第一天")
    assert all(block.overview_units for block in layout.days)
    assert not any("费用包含" in units[u] for u in layout.days[-1].units)


def test_long_rows_are_cut_before_brackets(tmp_path):
    layout = segment(read(sample(tmp_path)))
    texts = [u.text for u in layout.units]
    assert any(t.startswith("【ACME 灯塔") for t in texts)


def test_checker_drops_invented_names_and_numbers(tmp_path):
    layout = segment(read(sample(tmp_path)))
    units = {u.id: u.text for u in layout.units}
    block = layout.days[0]
    lighthouse = next(u for u in block.units if "灯塔" in units[u])
    museum = next(u for u in block.units if "博物馆" in units[u])
    group_unit = next(u for u in block.units if "市区观光" in units[u])
    out = DayOut(
        items=[
            Node(
                type="group",
                name="甲城 市区观光",
                duration_text="总观光时间不少于1小时",
                cite=[group_unit],
                children=[
                    ChildNode(name="ACME 灯塔(外观)", visit_mode="inside", cite=[lighthouse]),
                    ChildNode(
                        name="ACME 博物馆", visit_mode="inside", ticket="included", cite=[museum]
                    ),
                ],
            ),
            Node(type="poi", name="虚构不存在的塔", cite=[lighthouse]),
            Node(type="poi", name="ACME 博物馆", duration_text="约 90 分钟", cite=[museum]),
        ]
    )
    day, cited = check_day(
        out,
        Day(day=1, units=block.units, overview_units=block.overview_units),
        units,
        block.header_unit,
    )
    names = [n.name for n in day.items]
    assert "虚构不存在的塔" not in names
    group = day.items[0]
    assert [c.name for c in group.children] == ["ACME 灯塔", "ACME 博物馆"]
    assert group.children[1].ticket == "included"
    assert group.children[1].visit_mode == "inside"  # 含门票 implies entry
    assert group.children[0].visit_mode == "unknown"  # the source says 外观, not 入内
    assert day.items[1].duration_text == ""  # 90 is not in the source
    codes = {i["code"] for i in day.issues}
    assert {"NAME_NOT_IN_SOURCE", "ATTRIBUTE_UNSUPPORTED", "NUMBER_NOT_IN_SOURCE"} <= codes
    assert lighthouse in cited and museum in cited
    # A node's issues carry its name: `path` indexes the model's list, not the kept one.
    by_code = {(i["code"], i.get("subject")) for i in day.issues}
    assert ("NAME_NOT_IN_SOURCE", "虚构不存在的塔") in by_code
    assert ("ATTRIBUTE_UNSUPPORTED", "ACME 灯塔") in by_code
    assert ("NUMBER_NOT_IN_SOURCE", "ACME 博物馆") in by_code


def test_clean_name_strips_visit_and_ticket_notes():
    assert clean_name("ACME 灯塔(外观)") == "ACME 灯塔"
    assert clean_name("ACME 宫 含门票、讲解") == "ACME 宫"
    assert clean_name("特别安排---ACME 小火车") == "ACME 小火车"


def test_page_embeds_data_without_external_resources():
    html = page({"title": "ACME __DATA__ 线路", "days": [], "units": [{"text": "</script><!--"}]})
    data = html.split('id="data"', 1)[1].split("</script>", 1)[0]
    assert "<" not in data.split(">", 1)[1] and "\\u003c/script>" in data
    assert "<title>ACME __DATA__ 线路</title>" in html
    assert "http://" not in html.split("<script>")[0] and "https://" not in html


def day_of(units: dict[int, str], out: DayOut) -> Day:
    ids = sorted(units)
    day, _ = check_day(out, Day(day=1, units=ids), units, ids[0])
    return day


def test_negated_words_never_support_an_attribute():
    units = {
        1: "第一天 甲城",
        2: "【ACME 宫】不含门票，不入内，外观约20分钟",
        3: "【ACME 馆】提供免费WiFi",
    }
    day = day_of(
        units,
        DayOut(
            items=[
                Node(type="poi", name="ACME 宫", visit_mode="inside", ticket="included", cite=[2]),
                Node(type="poi", name="ACME 馆", ticket="free_entry", cite=[3]),
            ]
        ),
    )
    palace, hall = day.items
    assert (palace.visit_mode, palace.ticket, hall.ticket) == ("unknown", "unknown", "unknown")


def test_outside_beside_the_name_outranks_inside_elsewhere():
    units = {1: "第一天 甲城", 2: "【ACME 宫】入内参观，【ACME 塔】外观"}
    day = day_of(
        units, DayOut(items=[Node(type="poi", name="ACME 塔", visit_mode="inside", cite=[2])])
    )
    assert day.items[0].visit_mode == "unknown"


def test_paid_type_needs_paid_words_and_highlight_needs_its_words():
    units = {1: "第一天 甲城", 2: "游览【ACME 花园】", 3: "特别安排【ACME 游船】"}
    day = day_of(
        units,
        DayOut(
            items=[
                Node(type="optional", name="ACME 花园", highlight=True, cite=[2]),
                Node(type="activity", name="ACME 游船", highlight=True, cite=[3]),
            ]
        ),
    )
    garden, boat = day.items
    assert (garden.type, garden.highlight) == ("activity", False)
    assert boat.highlight is True


def test_day_notes_titles_and_stay_names_are_held_to_the_source():
    units = {1: "第一天 甲城", 2: "住宿：ACME 酒店或同级", 3: "注：请携带护照"}
    out = DayOut(
        title="甲城 3 小时深度游",
        notes=[{"text": "请携带护照", "cite": [3]}, {"text": "全程免小费", "cite": [3]}],
        stay={"kind": "hotel", "names": ["ACME 酒店", "虚构大饭店"], "cite": [2]},
    )
    day = day_of(units, out)
    assert [n.text for n in day.notes] == ["请携带护照"]
    assert day.stay.names == ["ACME 酒店"]
    assert day.title == "第一天 甲城"  # 3 is not in the day's text


def test_meal_column_dash_means_no_meals():
    units = {1: "DATE | ITINERARY | MEAL | HOTEL", 2: "DAY-1 | 上海 | / |", 3: "第一天 上海"}
    day, _ = check_day(DayOut(), Day(day=1, units=[3], overview_units=[2]), units, 3)
    assert day.meals.lunch.status == "self"
    units[2] = "DAY-1 | 上海 | 早餐:含 | /"  # "/" in the HOTEL column says nothing about meals
    day, _ = check_day(DayOut(), Day(day=1, units=[3], overview_units=[2]), units, 3)
    assert day.meals.lunch.status == "unknown"


def test_route_items_need_evidence_and_real_numbers():
    units = {1: "费用包含：全程住宿、所列门票", 2: "团费 12999 元/人", 3: "西班牙 葡萄牙 12晚"}
    out = RouteOut(
        title="全程无购物纯玩团",
        nights=9,
        countries=["西班牙", "虚构国"],
        inclusions=[
            Item(text="全程住宿、所列门票、ACME", cite=[]),
            Item(text="全程住宿", cite=[1]),
        ],
        prices=[Price(label="团费", amount="13999", text="团费 12999 元/人", cite=[2])],
    )
    out, issues, _ = check_route(out, units, [1, 2, 3])
    assert out.title == "" and out.nights is None and out.countries == ["西班牙"]
    assert [x.text for x in out.inclusions] == ["全程住宿"]
    assert out.prices == [] and any(i["path"].startswith("prices") for i in issues)


def test_numbers_compare_whole():
    assert missing_numbers("1 小时", "约10小时") == ["1"]
    assert missing_numbers("1299 元", "1,299元/人") == []


def test_table_rows_with_amounts_are_risky():
    assert is_risky("小费 | 5欧 | 每天")
    assert not is_risky("名称 | 价格 | 时长")


def test_docx_keeps_columns_nested_tables_and_reads_text_boxes_once(tmp_path):
    nested = "<w:tbl>" + row("内表", "甲") + "</w:tbl>"
    box = (
        '<w:p xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><w:r>'
        "<mc:AlternateContent><mc:Choice><w:t>文本框</w:t></mc:Choice>"
        "<mc:Fallback><w:t>文本框</w:t></mc:Fallback></mc:AlternateContent></w:r></w:p>"
    )
    body = (
        "<w:tbl>"
        + row("D1", "含", "含", "")
        + f"<w:tr><w:tc>{para('外表')}{nested}</w:tc></w:tr>"
        + "</w:tbl>"
        + box
    )
    texts = [line.text for line in read(docx(tmp_path, body)).lines]
    assert texts == ["D1 | 含 | 含 |", "外表", "内表 | 甲", "文本框"]


def test_day_ranges_and_overview_after_programme(tmp_path):
    assert _header("第2天至第4天 海上巡游") == (2, 4)
    body = (
        para("第1天 甲城")
        + para("游览【ACME 花园】，漫步老城街巷，傍晚返回酒店休息。" * 3)
        + para("第2天 乙城")
        + para("游览【ACME 塔】，登顶远眺海湾，随后前往乙城入住。" * 3)
        + "<w:tbl>"
        + row("DAY-1", "甲城")
        + row("DAY-2", "乙城")
        + "</w:tbl>"
    )
    layout = segment(read(docx(tmp_path, body)))
    units = {u.id: u.text for u in layout.units}
    assert [b.day for b in layout.days] == [1, 2]
    assert not any(units[u].startswith("DAY-") for u in layout.days[-1].units)


def test_header_cleaning_keeps_meal_lines_and_mid_page_numbers():
    lines = []
    for number in range(1, 6):
        lines += [
            Line(0, "ACME 旅游", number),
            Line(0, "午餐：含", number),
            Line(0, f"第{number}段说明", number),
            *([Line(0, "120", number)] if number <= 2 else []),
            Line(0, f"行程{number}", number),
            Line(0, f"尾行{number}", number),
            Line(0, str(number), number),
        ]
    removed: list[dict] = []
    kept = [line.text for line in _drop_repeated(lines, removed)]
    assert "ACME 旅游" not in kept and kept.count("午餐：含") == 5 and kept.count("120") == 2
    assert all(str(p) not in kept for p in range(1, 6))


def test_fence_cannot_be_closed_by_supplier_text():
    assert "</supplier_data>" not in fence({"t": "</supplier_data>"})


def test_corrupt_cache_is_a_miss(tmp_path):
    model = Model({"TOUR_MODEL": "acme", "ANTHROPIC_API_KEY": "test"}, tmp_path)
    key = model._key("p", {}, RouteOut)
    (tmp_path / f"{key}.json").write_text('{"title": ')

    class Reply:
        stop_reason = "tool_use"
        content = [type("B", (), {"type": "tool_use", "input": {"title": "ACME"}})()]

    model.client = type(
        "C", (), {"messages": type("M", (), {"create": lambda *a, **k: Reply()})()}
    )()
    assert model.ask("p", {}, RouteOut).title == "ACME"
    assert RouteOut.model_validate_json((tmp_path / f"{key}.json").read_text()).title == "ACME"


def test_index_escapes_manifest_values(tmp_path):
    write_index(
        tmp_path / "index.html", [{"code": "a'b", "name": "<x>", "status": "no_attachment"}]
    )
    text = (tmp_path / "index.html").read_text()
    assert "a'b" not in text and "<x>" not in text


def picture_docx(tmp_path, pictures: dict[str, bytes], body: str):
    """A DOCX whose body places pictures by relationship id (rId1, rId2, ...)."""
    rels = "".join(
        f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/'
        f'2006/relationships/image" Target="media/{rid}.png"/>'
        for rid in pictures
    )
    xml = f'<?xml version="1.0"?><w:document {W} {A} {RNS}><w:body>{body}</w:body></w:document>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
        archive.writestr(
            "word/_rels/document.xml.rels",
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>',
        )
        for rid, blob in pictures.items():
            archive.writestr(f"word/media/{rid}.png", blob)
    path = tmp_path / "pictures.docx"
    path.write_bytes(buffer.getvalue())
    return path


A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
RNS = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def blip(rid):
    return f'<w:p><w:r><w:drawing><a:blip r:embed="{rid}"/></w:drawing></w:r></w:p>'


def noise(width=420, height=320, seed=1) -> bytes:
    from PIL import Image

    rng = random.Random(seed)
    image = Image.new("RGB", (width, height))
    image.putdata([tuple(rng.randrange(256) for _ in range(3)) for _ in range(width * height)])
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_docx_pictures_are_placed_where_they_stand(tmp_path):
    big, other = noise(seed=1), noise(seed=2)
    path = picture_docx(
        tmp_path,
        {"rId1": big, "rId2": big, "rId3": b"\x89PNG tiny", "rId4": other},
        blip("rId1") + para("第1天 甲城") + blip("rId2") + blip("rId3") + blip("rId4"),
    )
    assert [line.kind for line in read(path).lines] == ["para"]  # off by default
    doc = read(path, pictures=True)
    kinds = [line.kind for line in doc.lines]
    assert kinds == ["picture", "para", "picture"]  # repeated and tiny pictures skipped
    assert len(doc.pictures) == 2


def test_transcribed_lines_replace_placeholders_and_find_the_cover(tmp_path):
    path = picture_docx(
        tmp_path,
        {"rId1": noise(seed=3), "rId2": noise(seed=4)},
        blip("rId1") + para("第1天 甲城") + blip("rId2"),
    )
    doc = read(path, pictures=True)

    class Fake:
        def ask(self, prompt, data, schema, **kwargs):
            if data["picture"].endswith("rId1.png"):
                return PictureText(kind="cover", lines=["ACME 海湾", "一价", "全含 "])
            return PictureText(kind="photo", lines=["招牌上的字"])

    records = transcribe(doc, Fake(), ThreadPoolExecutor(2))
    assert [(line.kind, line.text) for line in doc.lines] == [
        ("image", "ACME 海湾"),
        ("image", "一价"),
        ("image", "全含"),
        ("para", "第1天 甲城"),
    ]
    assert [line.id for line in doc.lines] == [1, 2, 3, 4]
    assert records[0]["cover"] and cover_image(doc)
    units = segment(doc).units
    assert [u.origin for u in units][:3] == ["image"] * 3


def test_field_lines_and_labels_leave_the_notes():
    units = {1: "第一天 甲城", 2: "行：汽车", 3: "独家安排---", 4: "注：当天不含导游"}
    out = DayOut(
        notes=[
            {"text": "行：汽车", "cite": [2]},
            {"text": "独家安排---", "cite": [3]},
            {"text": "注：当天不含导游", "cite": [4]},
        ],
        items=[Node(type="optional", name="自费推荐行程", cite=[3])],
    )
    day = day_of(units, out)
    assert [n.text for n in day.notes] == ["注：当天不含导游"]
    assert day.travel_text == "汽车" and day.items == []


def test_selling_points_are_short_distinct_tags():
    units = {1: "一价", 2: "全含", 3: "含全餐、推自费退团费、0购物", 4: "贴心赠送"}
    out = RouteOut(
        selling_points=[
            Item(text="一价全含", cite=[1, 2]),
            Item(text="含全餐、推自费退团费、0购物", cite=[3]),
            Item(text="贴心赠送", cite=[4]),
            Item(text="0购物", cite=[3]),
        ]
    )
    out, _, _ = check_route(out, units, [1, 2, 3, 4])
    assert [x.text for x in out.selling_points] == ["一价全含", "含全餐", "推自费退团费", "0购物"]


def test_headings_with_content_and_risky_field_lines_stay():
    units = {
        1: "第一天 甲城",
        2: "温馨提示",
        3: "如遇闭馆，改为外观，费用不退",
        4: "住宿：酒店不提供洗漱用品，请自备",
    }
    out = DayOut(
        items=[
            Node(
                type="notice",
                name="温馨提示",
                description="如遇闭馆，改为外观，费用不退",
                cite=[2, 3],
            )
        ],
        notes=[{"text": "住宿：酒店不提供洗漱用品，请自备", "cite": [4]}],
    )
    day = day_of(units, out)
    assert len(day.items) == 1 and [n.text for n in day.notes] == [units[4]]


def test_only_a_real_heading_makes_following_stops_paid():
    units = {
        1: "第一天 甲城",
        2: "上午：参观【ACME 宫】",
        3: "如时间允许可自费参加游船，约50欧/人",
        4: "随后前往【ACME 门】外观",
        5: "自费推荐行程：",
        6: "【ACME 岛】半日游",
    }
    out = DayOut(
        items=[
            Node(type="poi", name="ACME 门", cite=[4]),
            Node(type="poi", name="ACME 岛", cite=[6]),
        ]
    )
    gate, island = day_of(units, out).items
    assert gate.type == "poi" and island.type == "optional"


def test_selling_points_keep_numbers_and_short_tags():
    units = {1: "立减1,000元", 2: "纯玩", 3: "成都、重庆双飞"}
    out = RouteOut(
        selling_points=[
            Item(text="立减1,000元", cite=[1]),
            Item(text="纯玩", cite=[2]),
            Item(text="成都、重庆双飞", cite=[3]),
        ]
    )
    out, _, _ = check_route(out, units, [1, 2, 3])
    assert [x.text for x in out.selling_points] == ["立减1,000元", "纯玩", "成都、重庆双飞"]


def test_cover_facts_merge_only_within_a_category():
    units = {1: "去程航班：待定", 2: "回程航班：待定"}
    out = RouteOut(
        cover_facts=[
            Item(category="去程航班", text="待定", cite=[1]),
            Item(category="回程航班", text="待定", cite=[2]),
        ]
    )
    out, _, _ = check_route(out, units, [1, 2])
    assert len(out.cover_facts) == 2


def test_trailing_label_is_cut_only_after_a_separator():
    units = {1: "第一天 甲城", 2: "下午游览约 1.5 小时，特别安排❀", 3: "出发前请仔细阅读温馨提示"}
    out = DayOut(notes=[{"text": units[2], "cite": [2]}, {"text": units[3], "cite": [3]}])
    assert [n.text for n in day_of(units, out).notes] == ["下午游览约 1.5 小时", units[3]]


def test_display_tidy_keeps_ranges_and_routes(tmp_path):
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        return
    html = page({"title": "t", "days": [], "units": []})
    script = html.split("<script>\n", 1)[1]
    source = script[script.index("const tidy=") : script.index("const T=")]
    cases = {
        "13:30 -- 15:50": "13:30–15:50",
        "焦特布尔——乌代布尔": "焦特布尔—乌代布尔",
        "特别升级6顿餐---牛排餐": "特别升级6顿餐：牛排餐",
        "1顿<墨鱼面>,1顿<烤鸡>;": "1顿「墨鱼面」，1顿「烤鸡」",
        "3-4星(含早)": "3-4星（含早）",
    }
    probe = source + f"console.log(JSON.stringify({list(cases)}.map(tidy)))"
    out = subprocess.run([node, "-e", probe], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == list(cases.values())


def test_a_text_layer_of_rare_characters_is_garbled_and_ordinary_text_is_not():
    from route_kit.reader import garbled

    ordinary = "早餐后前往景区参观游览，午餐后乘车返回酒店休息，晚上自由活动。" * 8
    # A font that maps 的、不、于 onto look-alike rare glyphs still reads as Chinese.
    broken = ordinary.replace("的", "癿").replace("不", "丌").replace("于", "亍") + "癿丌亍" * 40
    traditional = "早餐後前往景區參觀遊覽，午餐後乘車返回飯店休息，晚上自由活動。" * 8
    assert not garbled(ordinary)
    assert garbled(broken)
    assert not garbled(traditional)
    assert not garbled("癿丌亍" * 10)  # too short to judge


def test_no_shopping_needs_a_quote_from_the_cited_line_that_says_none():
    from route_kit.extract import NEGATION, norm

    def accepted(quote, line):
        return bool(norm(quote) and norm(quote) in norm(line) and NEGATION.search(quote))

    assert accepted("0 购物", "购物安排：全程 0 购物店安排")
    assert accepted("6无产品", "6无产品(无自费、无景交、无超市、无苗寨)")
    assert accepted("不进购物店", "全程不进购物店，品质游")
    assert not accepted(
        "特产超市", "购物：特产超市：哈尔滨游客服务中心"
    )  # names a shop, says nothing
    assert not accepted("0购物", "购物店：3个")  # not in the cited line


class _Fake:
    """A model that returns a fixed structured answer."""

    name = "fake"

    def __init__(self, answer):
        self.answer = answer

    def ask(self, prompt, data, schema, max_tokens=0):
        return self.answer


def _layout_of(texts):
    from route_kit.segment import Layout, Unit

    units = [Unit(i, i, t) for i, t in enumerate(texts, start=1)]
    return Layout(units, [], [u.id for u in units], "none", ["NO_DAY_HEADERS"])


def test_model_day_split_is_used_only_for_a_clean_sequence_of_the_registered_length():
    from route_kit.extract import DayStarts, split_days_by_model

    texts = [
        "封面",
        "特色",
        "厦门-重庆",
        "住宿：重庆",
        "重庆-武隆",
        "住宿：武隆",
        "武隆-厦门",
        "费用包含",
    ]

    def starts(*pairs):
        return DayStarts.model_validate({"starts": [{"day": d, "first_unit": u} for d, u in pairs]})

    layout, issues = _layout_of(texts), [{"code": "NO_DAY_HEADERS", "path": "days", "detail": ""}]
    split_days_by_model(layout, _Fake(starts((1, 3), (2, 5), (3, 7))), 3, issues)
    assert [b.day for b in layout.days] == [1, 2, 3]
    assert layout.days[0].units == [3, 4] and layout.days[2].units == [7, 8]
    assert layout.outside == [1, 2] and layout.method == "model"
    assert [i["code"] for i in issues] == ["DAYS_SPLIT_BY_MODEL"]

    for wrong in (
        starts((1, 3), (2, 5)),  # registered 3 days
        starts((1, 3), (2, 3), (3, 7)),  # not increasing
        starts((1, 3), (3, 5), (4, 7)),  # not 1..N
        starts((1, 3), (2, 99), (3, 100)),  # units that do not exist
    ):
        layout = _layout_of(texts)
        issues = []
        split_days_by_model(layout, _Fake(wrong), 3, issues)
        assert layout.days == [] and issues[0]["code"] == "DAY_SPLIT_REJECTED"


def test_meal_counts_are_compared_with_the_days_and_no_days_is_a_reason():
    from route_kit.confidence import assess
    from route_kit.models import Day, Item, Meal, MealCounts, Meals, Quality, RouteContent, Source

    def day(n, b, l_, d):
        return Day(
            day=n,
            meals=Meals(breakfast=Meal(status=b), lunch=Meal(status=l_), dinner=Meal(status=d)),
        )

    content = RouteContent(
        source=Source(file_name="a.pdf", media="pdf", sha256="x", parser="p"),
        days=[day(1, "unknown", "included", "self"), day(2, "included", "included", "included")],
        inclusions=[Item(text="用餐：4 早 7 正")],
        exclusions=[Item(text="单房差")],
        meal_counts=MealCounts(breakfast=4, main=7, raw="4 早 7 正", cite=[1]),
        quality=Quality(days_found=2, days_extracted=2, units=10, direct_units=9),
    )
    score, reasons = assess(content)
    assert any("早餐 4 餐，逐日为 1 餐" in r for r in reasons)
    assert any("正餐 7 餐，逐日为 3 餐" in r for r in reasons)
    assert score > 80
    content.days = []
    content.quality.days_found = content.quality.days_extracted = 0
    assert "没有识别出逐日行程" in assess(content)[1]


def test_page_labels_out_of_order_are_told_from_a_restart():
    from route_kit.reader import out_of_order

    assert out_of_order("第二天\n第三天\n第一天\n第四天\n第五天")  # a table read cell by cell
    assert out_of_order("第一天\n第三天\n第四天\n第二天\n第五天")
    assert not out_of_order(
        "第一天\n第二天\n第三天\n第一天\n第二天\n第三天"
    )  # overview, then programme
    assert not out_of_order("第一天\n第二天")  # too few labels to judge
    assert not out_of_order("D1\nD2\nD3\nD4")


def test_one_day_split_is_taken_only_when_the_registered_length_is_one_day():
    from route_kit.extract import DayStarts, split_days_by_model

    texts = [
        "封面",
        "特色",
        "集合：8:00 厦门",
        "游览云水谣古镇",
        "午餐",
        "返回厦门",
        "费用包含",
        "不含门票",
    ]
    one = DayStarts.model_validate({"starts": [{"day": 1, "first_unit": 3}]})

    layout, issues = _layout_of(texts), []
    split_days_by_model(layout, _Fake(one), 1, issues)
    assert [b.day for b in layout.days] == [1] and layout.days[0].units[0] == 3

    for registered in (None, 2):  # a single day is no answer for a trip of unknown or longer length
        layout, issues = _layout_of(texts), []
        split_days_by_model(layout, _Fake(one), registered, issues)
        assert layout.days == [] and issues[0]["code"] == "DAY_SPLIT_REJECTED"


def test_a_file_that_is_not_an_itinerary_stops_and_a_doubt_does_not():
    import pytest
    from route_kit.extract import DocumentKind, require_itinerary
    from route_kit.llm import ModelError
    from route_kit.reader import NotItinerary, ReadError

    layout = _layout_of(["凯撒定制手持小风扇（充电款）", "规格：USB 充电", "包装：彩盒"])
    with pytest.raises(NotItinerary) as stopped:
        require_itinerary(layout, _Fake(DocumentKind(kind="not_itinerary")), "定制小风扇")
    assert isinstance(stopped.value, ReadError) and stopped.value.code == "NOT_ITINERARY"

    require_itinerary(layout, _Fake(DocumentKind(kind="itinerary")), "云水谣一日游")

    class Failing(_Fake):
        def ask(self, *args, **kwargs):
            raise ModelError("MODEL_FAILED")

    require_itinerary(layout, Failing(None), "任何名称")  # a failed call never stops the parse
