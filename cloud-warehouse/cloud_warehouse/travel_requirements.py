"""Ground model requirement patches in the active human message, before saving."""

import calendar
import re
from datetime import date

from . import destinations as geo

GEO_FIELDS = {
    "destinations",
    "destination_regions",
    "destination_examples",
    "excluded_destinations",
}


def _field(values, message):
    return {"value": list(dict.fromkeys(values)), "source": "said", "evidence": message, "hint": ""}


def location_intent(message):
    required, examples, excluded, regions = [], [], [], []
    for clause in re.split(r"[，,；;。！？!?]|但是|不过|但", message):
        example = re.search(r"比如|例如|譬如|举例|像|之类|等(?:国家|地|都|也|均|的|，|$)", clause)
        force = re.search(r"必须|一定|必去|都要|都去|全部|均需", clause)
        for match, kind, key in geo.mentions(clause):
            before, after = clause[: match.start()], clause[match.end() :]
            negative = re.search(r"不去|不要去|排除|不含|不包括|避开", before) or re.match(
                r"(?:都)?(?:不去|不要去|排除|不包括)", after
            )
            optional = example and not force
            optional = optional or bool(re.match(r"(?:也)?(?:不一定|可去可不去|可选)", after))
            values = (
                [geo.COUNTRIES[c][0] for c in geo.GROUPS[key]]
                if kind == "group"
                else [geo.COUNTRIES[key][0]]
                if kind == "country"
                else [key]
            )
            if negative:
                excluded.extend(values)
            elif optional:
                examples.extend(values)
            elif kind == "region":
                regions.extend(values)
            else:
                required.extend(values)
    if re.search(r"不(?:一定|必|要求).{0,8}(?:都去|全去|全含|全包括|全包含)", message):
        examples.extend(required)
        required = []
    excluded = list(dict.fromkeys(excluded))
    required = [x for x in dict.fromkeys(required) if x not in excluded]
    examples = [x for x in dict.fromkeys(examples) if x not in excluded and x not in required]
    regions = list(dict.fromkeys(regions))
    return required, examples, excluded, regions


def normalize(fields, message, brief, today: date):
    result = dict(fields)
    if GEO_FIELDS.intersection(fields):
        required, examples, excluded, regions = location_intent(message)
        if required or examples or excluded or regions:
            # Explicit corrections operate on the durable requirements. A new
            # destination statement replaces them; a negation alone does not.
            if re.search(r"加上|再加|还要去|也要去", message) and not re.search(
                r"改成|改去|换成|只去", message
            ):
                required = [*(brief.destinations.value or []), *required]
            if excluded and not required and not regions:
                required = [x for x in brief.destinations.value or [] if x not in excluded]
                examples = [x for x in brief.destination_examples.value or [] if x not in excluded]
                regions = brief.destination_regions.value or []
            # Existing exclusions persist unless the person explicitly changes
            # destination scope or withdraws an exclusion.
            if not re.search(r"改成|改去|换成|重新找|取消.*(?:排除|限制)|可以去", message):
                excluded = list(
                    dict.fromkeys([*(brief.excluded_destinations.value or []), *excluded])
                )
            required = [x for x in required if x not in excluded]
            needed = geo.canonical_terms(" ".join(required or regions))
            if not needed and examples:
                # Named examples without a region do not manufacture a search
                # universe; preserve an already established region if available.
                needed = brief.destinations.value or []
            result.update(
                {
                    "destinations": _field(needed, message),
                    "destination_regions": _field(regions, message),
                    "destination_examples": _field(examples, message),
                    "excluded_destinations": _field(excluded, message),
                }
            )
    if "window" in result and result["window"].get("value"):
        value = dict(result["window"])
        years = re.findall(r"(?<!\d)(20\d{2})(?:年|[-/])", message)
        # A month without a year cannot be tagged as a fully explicit date.
        if not years:
            value.update(source="inferred", hint="原话未写年份，按当前业务日期推断；待顾问确认")
        months = list(
            re.finditer(
                r"(?<!\d)(1[0-2]|[1-9]|十二|十一|十|[一二三四五六七八九])月(?:份)?", message
            )
        )
        if len(months) == 1:
            match = months[0]
            tail = message[match.end() :]
            # Normalize only a whole-month request. Preserve days, ranges,
            # holidays and qualifiers rather than expanding them to a month.
            if not re.match(
                r"\s*(?:\d|[一二三四五六七八九十]|上旬|中旬|下旬|初|底|末|中|前|后|至|到|[-—~])",
                tail,
            ):
                chinese = {
                    name: n
                    for n, name in enumerate(
                        [
                            "一",
                            "二",
                            "三",
                            "四",
                            "五",
                            "六",
                            "七",
                            "八",
                            "九",
                            "十",
                            "十一",
                            "十二",
                        ],
                        1,
                    )
                }
                month = int(match[1]) if match[1].isdigit() else chinese[match[1]]
                year = int(years[0]) if years else today.year + (month < today.month)
                value["value"] = {
                    "start": f"{year}-{month:02d}-01",
                    "end": f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}",
                }
                value["evidence"] = message
                if not years:
                    value["hint"] = f"原话只说{month}月，按业务日期推断为{year}年{month}月；待确认"
        result["window"] = value
    return result
