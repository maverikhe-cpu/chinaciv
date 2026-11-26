"""Wonders (奇观): sites of China picked by published lists, never by taste (docs/CN_PLAN.md §5.7).

* unesco_whs:    every World Heritage site in China (UNESCO's list; a transnational site once, at its Chinese part).
                 Cultural → kind `monument`; natural and mixed → `natural`.
* national_park: every formally established 国家公园 (the first batch of 2021-10-12). Kind `natural`; `cells` stays
                 null because no open boundary exists (the source gives points only).
* scenic_5a:     the 国家5A级旅游景区 of the first batch (2007), leaving out those whose core is already a World
                 Heritage wonder (hand-written table FIVE_A_IN_WHS below, each with the WHS it belongs to).
                 Kind `landmark`.
* university:    the 首轮“一流大学建设高校” (2017, A and B classes) that are still on the 2022 second-round 双一流 list,
                 without military universities. The plan's first choice, "the 20 with the largest R&D", cannot be used:
                 教育部 publishes no per-university R&D table online, so `rd_cny` is null everywhere and the rule text
                 says so. Kind `university`.

Every wonder carries `desc`: a short Chinese sentence with the data-backed reason (入选年份, 设立日期, 面积, 批次…).
`geo` says how the source found the point; `approx` is true when it is only a county/prefecture seat or a province
centroid. Sites the map cannot place (no coordinates, or no Chinese land hex within 2 rings) are listed in
`unplaced`, never silently dropped.

Writes web/data/wonders.json (eager, about 100 kB).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from ..core.china import PROVINCES
from ..core.store import load_interim, load_interim_payload, write_web
from .locate import Locator, _is_num, _load

REQUIRES = ["_world", "cn_boundaries", "cn_wonders", "cities.json"]  # cities.json optional at build time (cbsa)
PRODUCES = ["wonders.json"]

VERSION = 2
RULE_IDS = ("unesco_whs", "national_park", "scenic_5a", "university")
KINDS = {"natural", "monument", "landmark", "university"}
APPROX_GEO = {"county_seat", "prefecture_seat", "province_centroid"}
CATEGORY_ZH = {"cultural": "文化遗产", "natural": "自然遗产", "mixed": "文化与自然双遗产"}
UNESCO_KIND = {"cultural": "monument", "natural": "natural", "mixed": "natural"}

# First-batch 5A areas whose core is a component of a World Heritage site (exact MCT directory name → WHC id).
# These are already UNESCO wonders, so the scenic_5a rule skips them.
FIVE_A_IN_WHS = {
    "故宫博物院": 439,                                  # 明清故宫
    "天坛公园": 881,                                    # 天坛
    "颐和园": 880,                                      # 颐和园
    "八达岭—慕田峪长城旅游区": 438,                      # 长城
    "秦皇岛市山海关景区": 438,                           # 长城（山海关段）
    "嘉峪关市嘉峪关文物景区": 438,                       # 长城（嘉峪关）
    "承德市承德避暑山庄及周围寺庙景区": 703,
    "大同市云冈石窟景区": 1039,
    "忻州市五台山风景名胜区": 1279,
    "苏州市苏州园林（拙政园－留园－虎丘）": 813,
    "杭州市杭州西湖风景区": 1334,
    "黄山市黄山风景区": 547,
    "厦门市鼓浪屿风景名胜区": 1541,
    "南平市武夷山风景名胜区": 911,
    "九江市庐山市庐山风景名胜区": 778,
    "泰安市泰山景区": 437,
    "济宁市明故城三孔旅游区": 704,                       # 曲阜孔庙、孔林和孔府
    "郑州市嵩山少林景区": 1305,                          # 登封“天地之中”历史古迹（少林寺建筑群）
    "洛阳市龙门石窟景区": 1003,
    "张家界市武陵源－天门山旅游区": 640,
    "大足石刻景区": 912,
    "成都市青城山－都江堰旅游景区": 1001,
    "乐山市峨眉山景区": 779,
    "阿坝州九寨沟景区": 637,
    "昆明市石林风景区": 1248,                            # 中国南方喀斯特（石林）
    "桂林市漓江风景区": 1248,                            # 中国南方喀斯特（桂林喀斯特）
    "西安市秦始皇帝陵博物院景区": 441,
    "昌吉回族自治州天山天池风景名胜区": 1414,             # 新疆天山（博格达）
}

# Multi-part World Heritage sites whose UNESCO point is not the part readers know: WHC id → (5A record whose point is
# used instead, note). The 5A record's point comes from the same source (cn_wonders, Wikidata coordinates).
WHS_POINT_FROM_5A = {
    439: ("故宫博物院", "标在北京故宫；UNESCO 数据给出的点位于沈阳故宫"),
}

# Map labels.
UNESCO_SHORT = {
    439: "明清故宫", 441: "秦始皇陵", 449: "周口店", 637: "九寨沟", 638: "黄龙", 640: "武陵源", 703: "避暑山庄",
    704: "曲阜三孔", 705: "武当山", 707: "布达拉宫", 778: "庐山", 779: "峨眉山—乐山大佛", 880: "颐和园", 881: "天坛",
    1001: "青城山—都江堰", 1002: "西递、宏村", 1004: "明清皇家陵寝", 1039: "云冈石窟", 1083: "三江并流",
    1110: "澳门历史城区", 1111: "哈尼梯田", 1112: "开平碉楼", 1135: "高句丽王城王陵", 1213: "大熊猫栖息地",
    1248: "南方喀斯特", 1292: "三清山", 1305: "天地之中", 1334: "杭州西湖", 1335: "中国丹霞", 1388: "澄江化石地",
    1389: "元上都", 1442: "丝绸之路", 1474: "土司遗址", 1508: "左江花山岩画", 1509: "神农架", 1561: "泉州",
    1592: "良渚古城", 1606: "黄渤海候鸟栖息地", 1638: "巴丹吉林沙漠", 1665: "景迈山古茶林", 1714: "北京中轴线",
    1736: "西夏陵", 1765: "景德镇瓷业遗存",
}
SCENIC_SHORT = {
    "天津古文化街旅游区（津门故里）": "天津古文化街", "盘山风景名胜区": "盘山", "保定市白洋淀景区": "白洋淀",
    "沈阳市沈阳植物园": "沈阳植物园", "大连市老虎滩海洋公园—老虎滩极地馆": "老虎滩",
    "延边朝鲜族自治州长白山景区": "长白山", "长春市伪满皇宫博物馆": "伪满皇宫", "哈尔滨市太阳岛景区": "太阳岛",
    "东方明珠广播电视塔": "东方明珠", "苏州市周庄古镇景区": "周庄", "南京市钟山风景名胜区－中山陵园风景区": "钟山",
    "无锡市中央电视台无锡影视基地三国水浒城景区": "三国水浒城", "温州市雁荡山风景区": "雁荡山",
    "舟山市普陀山风景区": "普陀山", "池州市九华山风景区": "九华山", "吉安市井冈山市井冈山风景旅游区": "井冈山",
    "烟台市蓬莱阁-三仙山-八仙过海旅游区": "蓬莱阁", "焦作市云台山－神农山－青天河风景区": "云台山",
    "武汉市黄鹤楼公园": "黄鹤楼", "宜昌市三峡大坝－屈原故里文化旅游区": "三峡大坝", "衡阳市衡山旅游区": "衡山",
    "广州市番禺区长隆旅游度假区": "长隆", "深圳市南山区华侨城旅游度假区": "华侨城",
    "三亚市南山文化旅游区": "三亚南山", "三亚市南山大小洞天旅游区": "大小洞天", "安顺市黄果树瀑布景区": "黄果树瀑布",
    "安顺市西秀区龙宫景区": "龙宫", "丽江市玉龙雪山景区": "玉龙雪山", "西安市华清宫景区": "华清宫",
    "延安市黄帝陵景区": "黄帝陵", "平凉市崆峒山风景名胜区": "崆峒山", "石嘴山市沙湖旅游景区": "沙湖",
    "中卫市沙坡头旅游景区": "沙坡头", "吐鲁番市葡萄沟风景区": "葡萄沟", "阿勒泰地区喀纳斯景区": "喀纳斯",
    "小三峡－小小三峡旅游区": "小三峡",
}
PARK_ID = {"三江源国家公园": "sanjiangyuan", "大熊猫国家公园": "giant-panda", "东北虎豹国家公园": "tiger-leopard",
           "海南热带雨林国家公园": "hainan-rainforest", "武夷山国家公园": "wuyishan"}
_SUFFIX = re.compile(r"(风景名胜区|风景旅游区|旅游景区|文化旅游区|旅游度假区|风景区|旅游区|景区)$")


def province_name(code: str | None) -> str:
    return PROVINCES[code][1] if code in PROVINCES else (code or "")


def _slug(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def _num(v):
    """Interim number → int when integral; None stays None (never 0)."""
    if not _is_num(v):
        return None
    return int(v) if float(v).is_integer() else v


def _fmt(v: float) -> str:
    return f"{v:,.0f}" if v >= 100 else f"{v:g}"


def _scenic_short(name: str) -> str:
    if name in SCENIC_SHORT:
        return SCENIC_SHORT[name]
    return _SUFFIX.sub("", name) or name


# --- placement ------------------------------------------------------------------------------------------

JITTER_KM = (1, 2, 4, 8)


def _place(loc: Locator, lon, lat, filed: str | None = None, rings: int = 2):
    """Map position of a site: x/y, the Chinese land cell (nearest within `rings` rings for coastal points), county,
    city (`cbsa`) and province (`state`). When the point's county lies in another province than the source files
    the site under (a point on a provincial border, against simplified outlines), the nearest county of the filed
    province within a few km wins; otherwise the point's own county decides."""
    if not (_is_num(lon) and _is_num(lat)):
        return None
    x, y, cell = loc.place(lon, lat, rings)
    if cell < 0:
        return None
    county = loc.county_at(x, y, cell)
    filed = filed if filed in loc.mapped else None
    if filed and county and county[:2] != filed:
        for r in JITTER_KM:
            hit = next((c for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r), (r, r), (-r, r), (r, -r), (-r, -r))
                        for c in [loc.county_at(x + dx, y + dy)] if c and c[:2] == filed), None)
            if hit:
                county = hit
                break
    state = loc.province_of(county, cell, None) if county else loc.province_of(None, cell, filed)
    if state not in loc.mapped:
        return None
    xs, ys = loc.settle(x, y, cell)
    return {"x": xs, "y": ys, "cell": cell, "county": county, "cbsa": loc.city_of(county), "state": state}


def _wonder(wid, name, short, kind, rule, metrics, desc, pos, *, geo=None, **extra) -> dict:
    return {"id": wid, "name": name, "short": short, "kind": kind, "rule": rule, "rank": None, "metrics": metrics,
            "desc": desc, **pos, "cells": None, "curated": False, "geo": geo, "approx": geo in APPROX_GEO, **extra}


def _rank(rows: list[dict]) -> list[dict]:
    for k, w in enumerate(rows, 1):
        w["rank"] = k
    return rows


# --- rules ----------------------------------------------------------------------------------------------

def unesco(loc, sites: list[dict], unplaced: list, scenic: list[dict] | None = None) -> list[dict]:
    by_name = {a["name"]: a for a in scenic or []}
    out = []
    for s in sorted(sites, key=lambda s: (s["inscribed"] or 9999, s["whc_id"])):
        extra = {}
        alt = WHS_POINT_FROM_5A.get(s["whc_id"])
        if alt and _is_num((by_name.get(alt[0]) or {}).get("lon")):
            a = by_name[alt[0]]
            s = dict(s, lon=a["lon"], lat=a["lat"], province=a.get("province"), geo=a.get("geo"))
            extra["point_note"] = alt[1]
        cat = CATEGORY_ZH.get(s["category"], s["category"])
        provs = s.get("provinces") or ([s["province"]] if s.get("province") else [])
        area = _num(s.get("area_ha")) or None  # UNESCO's export gives 0 for an unrecorded area (青城山—都江堰)
        desc = f"{s['inscribed']}年列入联合国教科文组织《世界遗产名录》（{cat}），符合标准{s['criteria']}"
        if area:
            desc += f"，遗产区面积{_fmt(area)}公顷"
        if len(provs) > 1:
            desc += f"，分布于{'、'.join(province_name(p) for p in provs)}"
        if s.get("transnational"):
            desc += "；跨国联合申报，此处标在其中国部分"
        desc += "。"
        pos = _place(loc, s.get("lon"), s.get("lat"), s.get("province"))
        if not pos:
            unplaced.append({"rule": "unesco_whs", "name": s["name"], "reason": "无坐标或附近无中国陆地格"})
            continue
        metrics = {"whc_id": s["whc_id"], "category": s["category"], "inscribed": s["inscribed"],
                   "criteria": s["criteria"], "area_ha": area, "transnational": bool(s.get("transnational")),
                   "provinces": provs}
        out.append(_wonder(f"whc-{s['whc_id']}", s["name"], UNESCO_SHORT.get(s["whc_id"], s["name"]),
                           UNESCO_KIND.get(s["category"], "monument"), "unesco_whs", metrics, desc, pos,
                           geo=s.get("geo") or "unesco", name_en=s.get("name_en"), **extra))
    return _rank(out)


def national_parks(loc, parks: list[dict], unplaced: list) -> list[dict]:
    out = []
    for p in sorted(parks, key=lambda p: -(p.get("area_km2") or 0)):
        provs = p.get("provinces") or ([p["province"]] if p.get("province") else [])
        area = _num(p.get("area_km2"))
        est = p.get("established")
        if est and re.fullmatch(r"\d{4}-\d{2}-\d{2}", est):
            y, m, d = (int(t) for t in est.split("-"))
            desc = f"{y}年{m}月{d}日正式设立的首批国家公园"
        else:
            desc = "国家公园"
        if area:
            desc += f"，面积约{area / 1e4:g}万平方千米" if area >= 1e4 else f"，面积约{_fmt(area)}平方千米"
        if provs:
            desc += f"，地跨{'、'.join(province_name(q) for q in provs)}"
        desc += "。"
        pos = _place(loc, p.get("lon"), p.get("lat"), p.get("province"))
        if not pos:
            unplaced.append({"rule": "national_park", "name": p["name"], "reason": "无坐标或附近无中国陆地格"})
            continue
        metrics = {"established": p.get("established"), "area_km2": area, "provinces": provs}
        wid = "np-" + PARK_ID.get(p["name"], _slug(p["name"]))
        out.append(_wonder(wid, p["name"], p["name"].removesuffix("国家公园"), "natural", "national_park", metrics,
                           desc, pos, geo=p.get("geo"), wikidata=p.get("wikidata")))
    return _rank(out)


def scenic_5a(loc, areas: list[dict], unesco_ids: set, unplaced: list) -> tuple[list[dict], list[str]]:
    """First-batch (2007) 5A areas, minus those inside a World Heritage wonder. Ordered by province code, then the
    directory's own order (this is a listing order, not a ranking)."""
    first = [a for a in areas if a.get("batch") == 1]
    names = {a["name"] for a in first}
    stale = sorted(set(FIVE_A_IN_WHS) - names)
    if stale:
        raise SystemExit(f"wonders: FIVE_A_IN_WHS names not in the first 5A batch (directory changed?): {stale}")
    out, in_whs = [], []
    for i, a in sorted(enumerate(first), key=lambda t: (t[1].get("province") or "99", t[0])):
        whc = FIVE_A_IN_WHS.get(a["name"])
        if whc is not None and whc in unesco_ids:
            in_whs.append(a["name"])
            continue
        pos = _place(loc, a.get("lon"), a.get("lat"), a.get("province"))
        if not pos:
            unplaced.append({"rule": "scenic_5a", "name": a["name"],
                             "reason": "来源未能定位（名称无地名线索）" if a.get("lon") is None else "附近无中国陆地格"})
            continue
        desc = f"{a.get('designated') or 2007}年首批国家5A级旅游景区（文化和旅游部公布）。"
        if a.get("geo") in APPROX_GEO:
            desc += "位置为所在" + {"county_seat": "区县驻地", "prefecture_seat": "地级市驻地",
                                    "province_centroid": "省份中心"}[a["geo"]] + "，系近似。"
        metrics = {"designated": a.get("designated"), "batch": a.get("batch")}
        wid = "5a-" + (a.get("wikidata") or _slug(a["name"]))
        out.append(_wonder(wid, a["name"], _scenic_short(a["name"]), "landmark", "scenic_5a", metrics, desc, pos,
                           geo=a.get("geo"), wikidata=a.get("wikidata")))
    return _rank(out), in_whs


def universities(loc, unis: list[dict], unplaced: list) -> list[dict]:
    """首轮一流大学建设高校 (A then B, as in the 2017 list) still on the 2022 list; order within a class = the 2022
    list's order. Military universities are already absent from the source."""
    picked = [u for u in unis if u.get("world_class_2017") in ("A", "B") and u.get("double_first_class")]
    picked.sort(key=lambda u: u["world_class_2017"])  # stable: keeps list order within A and B
    out = []
    for u in picked:
        rd = _num(u.get("rd_cny"))
        desc = (f"2017年首轮“一流大学建设高校”（{u['world_class_2017']}类），2022年入选第二轮“双一流”建设高校名单（教育部等）。")
        desc += (f"{u['rd_year']}年研发经费{rd / 1e8:,.1f}亿元。" if rd and u.get("rd_year")
                 else "研发经费：教育部未公开分校数据。")
        pos = _place(loc, u.get("lon"), u.get("lat"), u.get("province"))
        if not pos:
            unplaced.append({"rule": "university", "name": u["name"], "reason": "无坐标或附近无中国陆地格"})
            continue
        metrics = {"rd_cny": rd, "rd_year": u.get("rd_year") if rd else None,
                   "double_first_class": bool(u.get("double_first_class")),
                   "world_class_2017": u["world_class_2017"]}
        wid = "univ-" + (u.get("wikidata") or _slug(u["name"]))
        out.append(_wonder(wid, u["name"], u["name"], "university", "university", metrics, desc, pos,
                           geo=u.get("geo"), wikidata=u.get("wikidata")))
    return _rank(out)


def rules_doc(counts: dict, data: dict, n_in_whs: int, unplaced: list | None = None) -> list[dict]:
    n_5a_off = sum(1 for u in unplaced or [] if u.get("rule") == "scenic_5a")
    uy = max((s["inscribed"] or 0) for s in data["unesco"]) if data["unesco"] else None
    n_dfc = sum(1 for u in data["universities"] if u.get("double_first_class"))
    return [
        {"id": "unesco_whs", "kind": "monument", "kinds": ["monument", "natural"], "year": uy,
         "text": f"中国境内全部{counts['unesco_whs']}处联合国教科文组织世界遗产（截至{uy}年）。文化遗产画作古迹，"
                 "自然遗产与文化和自然双遗产画作自然奇观；跨国项目只计一次，标在其中国部分；分布于多个省份的"
                 "遗产标在来源给出的一处组成部分上。按列入年份排序。"},
        {"id": "national_park", "kind": "natural", "kinds": ["natural"], "year": 2021,
         "text": f"全部{counts['national_park']}个正式设立的国家公园（2021年10月12日首批设立；其余候选区尚在创建中，"
                 "不计入）。按面积排序。来源只提供位置点，没有公开边界，因此不画覆盖范围。"},
        {"id": "scenic_5a", "kind": "landmark", "kinds": ["landmark"], "year": 2007,
         "text": f"2007年首批国家5A级旅游景区中、未被世界遗产奇观涵盖的{counts['scenic_5a'] + n_5a_off}处"
                 f"（首批共{sum(1 for a in data['scenic_5a'] if a.get('batch') == 1)}处，其中{n_in_whs}处的核心是世界遗产的"
                 f"组成部分，已作为世界遗产列出；全国5A级景区共{len(data['scenic_5a'])}处）。"
                 + (f"其中{n_5a_off}处来源未能定位，未上图。" if n_5a_off else "")
                 + "按省份排列，不代表排名。标注“近似”的位置为所在区县或地级市驻地。"},
        {"id": "university", "kind": "university", "kinds": ["university"], "year": 2022,
         "text": f"2017年首轮“一流大学建设高校”（A类、B类）中仍在2022年第二轮“双一流”名单上的{counts['university']}所"
                 f"（第二轮名单共{n_dfc}所非军队院校；军队院校不列入）。原计划按研发经费取前20名，但教育部"
                 "《高等学校科技统计资料汇编》没有公开的分校数据，因此研发经费为空，改用此规则。先A类后B类，"
                 "类内按名单顺序，不代表排名。"},
    ]


def build() -> None:
    world = load_interim("_world")
    loc = Locator(world)
    payload = load_interim_payload("cn_wonders")
    data = payload["data"]
    unplaced: list[dict] = []
    w = unesco(loc, data["unesco"], unplaced, data["scenic_5a"])
    w += national_parks(loc, data["national_parks"], unplaced)
    five_a, in_whs = scenic_5a(loc, data["scenic_5a"], {s["whc_id"] for s in data["unesco"]}, unplaced)
    w += five_a
    w += universities(loc, data["universities"], unplaced)
    counts = Counter(x["rule"] for x in w)
    cats = Counter(s["category"] for s in data["unesco"])
    doc = {
        "version": VERSION,
        "vintage": data.get("vintages") or payload.get("vintage"),
        "sources": ["cn_wonders", "cn_boundaries"],
        "rules": rules_doc(counts, data, len(in_whs), unplaced),
        "national": {"unesco_whs": len(data["unesco"]), "unesco_by_category": dict(cats),
                     "national_parks": len(data["national_parks"]), "scenic_5a": len(data["scenic_5a"]),
                     "scenic_5a_first_batch": sum(1 for a in data["scenic_5a"] if a.get("batch") == 1),
                     "double_first_class": sum(1 for u in data["universities"] if u.get("double_first_class"))},
        "wonders": w,
        "unplaced": unplaced,
    }
    write_web("wonders.json", doc)
    print(f"    {len(w)} wonders: {dict(counts)}; approx positions: {sum(x['approx'] for x in w)}; "
          f"5A inside a WHS: {len(in_whs)}; unplaced: {[u['name'] for u in unplaced]}")


# --- check ----------------------------------------------------------------------------------------------

def check(web_dir) -> list[tuple[str, str]]:
    path = Path(web_dir) / "wonders.json"
    if not path.exists():
        return [("NOTE", "wonders.json not built")]
    d = json.loads(path.read_text())
    world = _load(web_dir, "world.json") or {}
    cities = _load(web_dir, "cities.json") or []
    g = world.get("grid") or {}
    n_cells = (g.get("cols") or 0) * (g.get("rows") or 0)
    owner = world.get("owner") or []
    counties = {c["geoid"] for c in world.get("counties") or []}
    codes = [s.get("fips") for s in world.get("states") or []]
    city_ids = {c["id"] for c in cities}

    def cell_ok(c):
        return isinstance(c, int) and not isinstance(c, bool) and (not n_cells or 0 <= c < n_cells) and \
            (not owner or (c < len(owner) and owner[c] >= 0))

    bad = []
    if d.get("version") != VERSION:
        bad.append(f"version must be {VERSION}")
    if not isinstance(d.get("sources"), list) or not d.get("sources"):
        bad.append("sources must be a non-empty list")
    rules = {r.get("id"): r for r in d.get("rules") or []}
    missing = [r for r in RULE_IDS if r not in rules]
    if missing:
        bad.append(f"rules missing: {missing}")
    if not all(isinstance(r.get("text"), str) and r["text"] and r.get("kind") in KINDS
               and set(r.get("kinds") or [r.get("kind")]) <= KINDS for r in rules.values()):
        bad.append("rules must each have an id, a kind, kinds ⊂ known kinds and Chinese text")
    if not isinstance(d.get("unplaced", []), list):
        bad.append("unplaced must be a list")
    ids, ranks, by_rule = set(), Counter(), Counter()
    for x in d.get("wonders") or []:
        tag = f"wonder {x.get('id')!r}"
        if not isinstance(x.get("id"), str) or x["id"] in ids:
            bad.append(f"{tag}: missing or duplicate id")
        ids.add(x.get("id"))
        r = rules.get(x.get("rule"))
        by_rule[x.get("rule")] += 1
        if r is None or x.get("kind") not in (r.get("kinds") or [r.get("kind")]):
            bad.append(f"{tag}: unknown rule or kind not allowed by its rule")
        if not isinstance(x.get("rank"), int) or isinstance(x.get("rank"), bool) or x["rank"] < 1:
            bad.append(f"{tag}: rank must be an int >= 1")
        ranks[(x.get("rule"), x.get("rank"))] += 1
        if not all(isinstance(x.get(k), str) and x[k] for k in ("name", "short", "desc")):
            bad.append(f"{tag}: name/short/desc missing")
        if not isinstance(x.get("metrics"), dict):
            bad.append(f"{tag}: metrics must be an object")
        if not cell_ok(x.get("cell")) or not _is_num(x.get("x")) or not _is_num(x.get("y")):
            bad.append(f"{tag}: bad cell/x/y (must be a Chinese land cell)")
        if x.get("cells") is not None and (not x["cells"] or not all(cell_ok(c) for c in x["cells"])):
            bad.append(f"{tag}: cells must be null or a non-empty list of Chinese land cells")
        if codes and x.get("state") not in codes:
            bad.append(f"{tag}: state must be a province code in world.json")
        elif owner and cell_ok(x.get("cell")) and codes and x.get("county") is None \
                and codes[owner[x["cell"]]] != x.get("state"):
            bad.append(f"{tag}: state differs from its cell's province and no county explains it")
        if x.get("county") is not None and (not re.fullmatch(r"\d{6}", str(x["county"]))
                                            or (counties and x["county"] not in counties)):
            bad.append(f"{tag}: county not a 6-digit adcode in world.json")
        if x.get("county") and x.get("state") and x["county"][:2] != x["state"]:
            bad.append(f"{tag}: county {x['county']} is not in province {x['state']}")
        if x.get("cbsa") is not None and city_ids and x["cbsa"] not in city_ids:
            bad.append(f"{tag}: cbsa not in cities.json")
        if not isinstance(x.get("curated"), bool) or x.get("curated") != bool((r or {}).get("curated")):
            bad.append(f"{tag}: curated must match its rule")
        if not isinstance(x.get("approx"), bool):
            bad.append(f"{tag}: approx must be a boolean")
        m = x.get("metrics") or {}
        for k in ("area_ha", "area_km2", "rd_cny"):
            if m.get(k) is not None and not (_is_num(m[k]) and m[k] > 0):
                bad.append(f"{tag}: metrics.{k} must be > 0 or null")
        if x.get("rule") == "unesco_whs" and not (isinstance(m.get("inscribed"), int) and 1987 <= m["inscribed"] <= 2100
                                                   and isinstance(m.get("whc_id"), int)):
            bad.append(f"{tag}: unesco metrics need whc_id and inscribed (1987…)")
        if x.get("rule") == "university" and (m.get("rd_cny") is None) != (m.get("rd_year") is None):
            bad.append(f"{tag}: rd_cny and rd_year must be both set or both null")
    dup = [k for k, v in ranks.items() if v > 1]
    if dup:
        bad.append(f"duplicate ranks within a rule: {dup[:3]}")
    empty = [r for r in RULE_IDS if r in rules and not by_rule[r]]
    if empty and d.get("wonders"):
        bad.append(f"rules with no wonder: {empty}")
    out = [("FAIL", f"wonders.json: {m}") for m in bad[:12]]
    if len(bad) > 12:
        out.append(("FAIL", f"wonders.json: … and {len(bad) - 12} more problems"))
    size = path.stat().st_size
    out.append(("WARN" if size > 300_000 else "PASS", f"wonders.json {size / 1e3:.0f} kB (eager budget ~300 kB)"))
    n = len(d.get("wonders") or [])
    if not bad:
        out.append(("PASS", f"wonders.json: {n} wonders ({', '.join(f'{v} {k}' for k, v in by_rule.items())})"))
    if not 40 <= n <= 160:
        out.append(("WARN", f"wonders.json: {n} wonders (expected about 40-160)"))
    unplaced = d.get("unplaced") or []
    if unplaced:
        out.append(("NOTE", f"wonders.json: {len(unplaced)} listed sites not placed: "
                            + ", ".join(str(u.get("name")) for u in unplaced[:6])))
    approx = sum(1 for x in d.get("wonders") or [] if x.get("approx"))
    if approx:
        out.append(("NOTE", f"wonders.json: {approx} wonders at an approximate (seat/centroid) position"))
    return out
