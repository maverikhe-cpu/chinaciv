"""Fixed reference tables for China (docs/CN_PLAN.md §3): province codes and names, special ids.

Province code = the 2-digit prefix of the GB/T 2260 行政区划代码 (adcode), e.g. "11" 北京, "44" 广东,
"71" 台湾, "81" 香港, "82" 澳门. Prefecture-level and county-level ids are 6-digit adcodes (strings).
Names are the official full names (as DataV GeoAtlas / 民政部 list them); `short` is the usual map label and
`abbr` the one-character 简称 (used where the US code used the postal abbreviation).
"""

from __future__ import annotations

import re

# code: (full name, short label, 简称, kind)
PROVINCES: dict[str, tuple[str, str, str, str]] = {
    "11": ("北京市", "北京", "京", "municipality"),
    "12": ("天津市", "天津", "津", "municipality"),
    "13": ("河北省", "河北", "冀", "province"),
    "14": ("山西省", "山西", "晋", "province"),
    "15": ("内蒙古自治区", "内蒙古", "蒙", "autonomous_region"),
    "21": ("辽宁省", "辽宁", "辽", "province"),
    "22": ("吉林省", "吉林", "吉", "province"),
    "23": ("黑龙江省", "黑龙江", "黑", "province"),
    "31": ("上海市", "上海", "沪", "municipality"),
    "32": ("江苏省", "江苏", "苏", "province"),
    "33": ("浙江省", "浙江", "浙", "province"),
    "34": ("安徽省", "安徽", "皖", "province"),
    "35": ("福建省", "福建", "闽", "province"),
    "36": ("江西省", "江西", "赣", "province"),
    "37": ("山东省", "山东", "鲁", "province"),
    "41": ("河南省", "河南", "豫", "province"),
    "42": ("湖北省", "湖北", "鄂", "province"),
    "43": ("湖南省", "湖南", "湘", "province"),
    "44": ("广东省", "广东", "粤", "province"),
    "45": ("广西壮族自治区", "广西", "桂", "autonomous_region"),
    "46": ("海南省", "海南", "琼", "province"),
    "50": ("重庆市", "重庆", "渝", "municipality"),
    "51": ("四川省", "四川", "川", "province"),
    "52": ("贵州省", "贵州", "贵", "province"),
    "53": ("云南省", "云南", "云", "province"),
    "54": ("西藏自治区", "西藏", "藏", "autonomous_region"),
    "61": ("陕西省", "陕西", "陕", "province"),
    "62": ("甘肃省", "甘肃", "甘", "province"),
    "63": ("青海省", "青海", "青", "province"),
    "64": ("宁夏回族自治区", "宁夏", "宁", "autonomous_region"),
    "65": ("新疆维吾尔自治区", "新疆", "新", "autonomous_region"),
    "71": ("台湾省", "台湾", "台", "province"),
    "81": ("香港特别行政区", "香港", "港", "sar"),
    "82": ("澳门特别行政区", "澳门", "澳", "sar"),
}
PROVINCE_KIND_LABEL = {"province": "省", "autonomous_region": "自治区", "municipality": "直辖市", "sar": "特别行政区"}

MUNICIPALITIES = {"11", "12", "31", "50"}
SARS = {"81", "82"}
TAIWAN = "71"
CAPITAL_CITY = "110000"  # the national capital (北京); cities.json `capital`

# Prefecture-level kinds (cities.json `kind`) and their labels.
PREFECTURE_KIND_LABEL = {
    "city": "地级市",
    "prefecture": "地区",
    "autonomous_prefecture": "自治州",
    "league": "盟",
    "municipality": "直辖市",
    "sar": "特别行政区",
    "taiwan_city": "市",  # Taiwan's cities: placed by Wikidata, ids project-assigned (see TAIWAN_CITIES)
    "county_city": "省直辖县级行政区",  # 省直辖县级: in the boundaries/county lookup, but not a city banner
}
BANNER_KINDS = {"city", "prefecture", "autonomous_prefecture", "league", "municipality", "sar", "taiwan_city"}

# Taiwan: GB/T 2260 lists only 710000 (no subdivisions) and DataV ships no Taiwan children. Phase 1 places
# Taiwan's six largest cities as banners. The ids are PROJECT-ASSIGNED (not official codes), north to south.
# Coordinates come from Wikidata (P625) at fetch time; population/GDP from Taiwan's own statistics or null.
TAIWAN_CITIES = [  # id, name, Wikidata item
    ("710100", "台北市", "Q1867"),
    ("710200", "新北市", "Q244898"),
    ("710300", "桃园市", "Q115256"),
    ("710400", "台中市", "Q245023"),
    ("710500", "台南市", "Q140631"),
    ("710600", "高雄市", "Q181557"),
]

# Named islands / island groups drawn as labelled points (too small for a hex, or in the inset).
# id, name, kind, Wikidata item. Shown on the main map where their position is inside the frame (and not under
# the inset box), and in the 南海诸岛 inset where inside the inset region.
ISLANDS = [
    ("diaoyu", "钓鱼岛", "island", "Q719187"),
    ("chiwei", "赤尾屿", "island", "Q5354453"),
    ("dongsha", "东沙群岛", "island_group", "Q698931"),
    ("xisha", "西沙群岛", "island_group", "Q274388"),
    ("zhongsha", "中沙群岛", "island_group", "Q197893"),
    ("huangyan", "黄岩岛", "island", "Q628716"),
    ("nansha", "南沙群岛", "island_group", "Q215664"),
    ("zengmu", "曾母暗沙", "shoal", "Q707510"),
]

_ETHNIC = ("朝鲜族", "土家族", "苗族", "侗族", "布依族", "傣族", "景颇族", "傈僳族", "藏族", "彝族", "白族", "哈尼族",
           "壮族", "羌族", "回族", "蒙古族", "蒙古", "哈萨克", "柯尔克孜", "黎族", "维吾尔")
_SHORT_SUFFIX = re.compile(r"(特别行政区|自治州|自治县|地区|林区|市|盟|县|区)$")


def province_code(adcode) -> str:
    return str(adcode)[:2]


def is_county_city(adcode) -> bool:
    """省直辖县级行政区 (济源, 仙桃, 石河子, 五指山 …): adcodes of the form xx90yy."""
    return str(adcode)[2:4] == "90"


def short_name(name: str) -> str:
    """'深圳市' → '深圳', '延边朝鲜族自治州' → '延边', '锡林郭勒盟' → '锡林郭勒', '香港特别行政区' → '香港'.
    An autonomous prefecture whose stem equals a province label keeps '州' ('海南藏族自治州' → '海南州')."""
    m = _SHORT_SUFFIX.search(name)
    if not m:
        return name
    stem = name[: m.start()]
    if m.group(1) in ("自治州", "自治县"):
        changed = True
        while changed:
            changed = False
            for e in _ETHNIC:
                if stem.endswith(e) and len(stem) > len(e) + 1:
                    stem = stem[: -len(e)]
                    changed = True
        if m.group(1) == "自治州" and stem in {p[1] for p in PROVINCES.values()}:
            stem += "州"
    return stem if len(stem) >= 2 else name


def prefecture_kind(adcode: str, name: str, level: str | None = None) -> str:
    code = province_code(adcode)
    if code in MUNICIPALITIES and str(adcode).endswith("0000"):
        return "municipality"
    if code in SARS and str(adcode).endswith("0000"):
        return "sar"
    if is_county_city(adcode):
        return "county_city"
    if name.endswith("自治州"):
        return "autonomous_prefecture"
    if name.endswith("地区"):
        return "prefecture"
    if name.endswith("盟"):
        return "league"
    return "city"
