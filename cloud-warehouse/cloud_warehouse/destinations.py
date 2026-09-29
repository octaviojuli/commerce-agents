"""Versioned travel names and explicit country groups, not itinerary certification."""

import re
import unicodedata

VERSION = 2
# Small operator-owned vocabulary; country codes are identifiers, not a geographic
# claim about a supplier itinerary. No third-party database is bundled.
COUNTRIES = {
    "DE": ("德国", "Germany"),
    "FR": ("法国", "France"),
    "IT": ("意大利", "Italy"),
    "CH": ("瑞士", "Switzerland"),
    "ES": ("西班牙", "Spain"),
    "PT": ("葡萄牙", "Portugal"),
    "GB": ("英国", "United Kingdom", "英格兰", "苏格兰"),
    "IE": ("爱尔兰", "Ireland"),
    "NL": ("荷兰", "Netherlands"),
    "BE": ("比利时", "Belgium"),
    "LU": ("卢森堡", "Luxembourg"),
    "AT": ("奥地利", "Austria"),
    "CZ": ("捷克", "Czechia"),
    "HU": ("匈牙利", "Hungary"),
    "GR": ("希腊", "Greece"),
    "NO": ("挪威", "Norway"),
    "SE": ("瑞典", "Sweden"),
    "DK": ("丹麦", "Denmark"),
    "FI": ("芬兰", "Finland"),
    "IS": ("冰岛", "Iceland"),
    "AD": ("安道尔", "Andorra"),
    "LI": ("列支敦士登", "Liechtenstein"),
    "PL": ("波兰", "Poland"),
    "HR": ("克罗地亚", "Croatia"),
    "SI": ("斯洛文尼亚", "Slovenia"),
    "SK": ("斯洛伐克", "Slovakia"),
    "RS": ("塞尔维亚", "Serbia"),
    "LK": ("斯里兰卡", "Sri Lanka"),
    "IN": ("印度", "India"),
    "JP": ("日本", "Japan"),
    "KR": ("韩国", "South Korea"),
    "SG": ("新加坡", "Singapore"),
    "MY": ("马来西亚", "Malaysia"),
    "TH": ("泰国", "Thailand"),
    "AU": ("澳大利亚", "Australia", "澳洲"),
    "NZ": ("新西兰", "New Zealand"),
    "US": ("美国", "United States"),
    "CA": ("加拿大", "Canada"),
    "MA": ("摩洛哥", "Morocco"),
    "GI": ("直布罗陀", "Gibraltar"),
}
GROUPS = {
    "西葡": ("ES", "PT"),
    "法意瑞": ("FR", "IT", "CH"),
    "德法意瑞": ("DE", "FR", "IT", "CH"),
    "德法瑞意": ("DE", "FR", "CH", "IT"),
    "德意法瑞": ("DE", "IT", "FR", "CH"),
    "德意瑞法": ("DE", "IT", "CH", "FR"),
    "意德法瑞": ("IT", "DE", "FR", "CH"),
    "法瑞意德": ("FR", "CH", "IT", "DE"),
    "卢德比法意瑞": ("LU", "DE", "BE", "FR", "IT", "CH"),
    "荷比卢": ("NL", "BE", "LU"),
    "荷比意瑞": ("NL", "BE", "IT", "CH"),
    "英苏爱": ("GB", "IE"),
    "新马泰": ("SG", "MY", "TH"),
    "新马": ("SG", "MY"),
    "澳新": ("AU", "NZ"),
    "美加": ("US", "CA"),
}
EUROPE = tuple(
    [
        "DE",
        "FR",
        "IT",
        "CH",
        "ES",
        "PT",
        "GB",
        "IE",
        "NL",
        "BE",
        "LU",
        "AT",
        "CZ",
        "HU",
        "GR",
        "NO",
        "SE",
        "DK",
        "FI",
        "IS",
        "AD",
        "LI",
        "PL",
        "HR",
        "SI",
        "SK",
        "RS",
    ]
)
# Travel-sales regions are explicit vocabulary, separate from strict geographic
# taxonomies. A named country takes precedence over its broad regional context.
REGIONS = {
    "欧洲": EUROPE,
    "西欧": tuple(
        ["DE", "FR", "IT", "CH", "NL", "BE", "LU", "AT", "LI", "GB", "IE", "ES", "PT", "AD"]
    ),
    "北欧": tuple(["NO", "SE", "DK", "FI", "IS"]),
    "东欧": tuple(["AT", "CZ", "HU", "PL", "HR", "SI", "SK", "RS"]),
    "南欧": tuple(["IT", "ES", "PT", "GR", "AD"]),
    "东西欧": EUROPE,
    "东南亚": ("SG", "MY", "TH"),
}
CITIES = {
    "巴黎": ("PAR", "FR", "Paris"),
    "罗马": ("ROM", "IT", "Rome"),
    "米兰": ("MIL", "IT", "Milan"),
    "威尼斯": ("VCE", "IT", "Venice"),
    "法兰克福": ("FRA", "DE", "Frankfurt"),
    "慕尼黑": ("MUC", "DE", "Munich"),
    "柏林": ("BER", "DE", "Berlin"),
    "苏黎世": ("ZRH", "CH", "Zurich"),
    "日内瓦": ("GVA", "CH", "Geneva"),
    "阿姆斯特丹": ("AMS", "NL", "Amsterdam"),
    "布鲁塞尔": ("BRU", "BE", "Brussels"),
    "马德里": ("MAD", "ES", "Madrid"),
    "巴塞罗那": ("BCN", "ES", "Barcelona"),
    "里斯本": ("LIS", "PT", "Lisbon"),
    "维也纳": ("VIE", "AT", "Vienna"),
    "伦敦": ("LON", "GB", "London"),
}
ALIASES = {
    alias.casefold(): ("country", code) for code, names in COUNTRIES.items() for alias in names
}
ALIASES.update({alias: ("group", alias) for alias in GROUPS})
ALIASES.update({alias: ("region", alias) for alias in REGIONS})
ALIASES.update(
    {alias.casefold(): ("city", name) for name, row in CITIES.items() for alias in (name, row[2])}
)
TOKEN = re.compile(
    "|".join(re.escape(x) for x in sorted(ALIASES, key=lambda x: (-len(x), x))), re.I
)


def mentions(value):
    value = unicodedata.normalize("NFKC", value)
    for match in TOKEN.finditer(value):
        # English words must not match an unrelated larger word.
        if match[0].isascii() and (
            (
                match.start()
                and value[match.start() - 1].isascii()
                and value[match.start() - 1].isalpha()
            )
            or (
                match.end() < len(value)
                and value[match.end()].isascii()
                and value[match.end()].isalpha()
            )
        ):
            continue
        kind, key = ALIASES[match[0].casefold()]
        yield match, kind, key


def names(value):
    result = []
    for _, kind, key in mentions(value):
        values = (
            [COUNTRIES[c][0] for c in GROUPS[key]]
            if kind == "group"
            else [COUNTRIES[key][0]]
            if kind == "country"
            else [key]
        )
        result.extend(x for x in values if x not in result)
    return result


def country_codes(value, *, cities=False):
    result = set()
    for _, kind, key in mentions(value):
        if kind == "country":
            result.add(key)
        elif kind == "group":
            result.update(GROUPS[key])
        elif cities and kind == "city":
            result.add(CITIES[key][1])
    return result


def pattern(value):
    """Country/region aliases are OR; separate requested countries remain AND."""
    key = ALIASES.get(value.casefold())
    if key is None:
        return re.escape(value)
    kind, identifier = key
    if kind == "city":
        terms = [identifier, CITIES[identifier][2]]
    else:
        codes = (
            {identifier}
            if kind == "country"
            else set(REGIONS.get(identifier, GROUPS.get(identifier, ())))
        )
        terms = [alias for code in sorted(codes) for alias in COUNTRIES[code]]
        terms += [
            alias for name, row in CITIES.items() if row[1] in codes for alias in (name, row[2])
        ]
        terms += [
            name
            for name, values in GROUPS.items()
            if (identifier in values if kind == "country" else bool(set(values) & codes))
        ]
        if kind == "region":
            terms += [name for name, values in REGIONS.items() if set(values) <= codes]
    return (
        "("
        + "|".join(
            ("(^|[^a-z])" + re.escape(t.casefold()) + "([^a-z]|$)") if t.isascii() else re.escape(t)
            for t in dict.fromkeys(terms)
        )
        + ")"
    )


def canonical_terms(query):
    result, end = [], 0
    for match, kind, key in mentions(query):
        result.extend(_unknown(query[end : match.start()]))
        result += (
            [COUNTRIES[c][0] for c in GROUPS[key]]
            if kind == "group"
            else [COUNTRIES[key][0]]
            if kind == "country"
            else [key]
        )
        end = match.end()
    result.extend(_unknown(query[end:]))
    result = list(dict.fromkeys(result))
    codes = set().union(*(country_codes(x, cities=True) for x in result))
    # A region is context when specific countries within it were stated. Do not
    # add a literal region-name constraint or erase an unrelated named region.
    return [x for x in result if x not in REGIONS or not codes.intersection(REGIONS[x])]


def _unknown(value):
    value = re.sub(r"出境游|旅游线路|旅游|线路|跟团游|跟团|多国", " ", value)
    return [
        x for x in re.split(r"[\s+＋、，,/→]+", value) if x and x not in {"和", "及", "与", "以及"}
    ]
