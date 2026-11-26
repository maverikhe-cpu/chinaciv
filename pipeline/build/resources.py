"""Tile resources: China's real power plants, mines and oil & gas fields, placed on the hex map (CN_PLAN §5.6).

Like strategic resources on a Civ tile, but only meaningful sites. The selection rule is published (in Chinese) in
`resources.json` `rule` and in each type's `description`; in short:

* Power plants (GEM Global Integrated Power Tracker via the cn_energy interim; operating units only, nameplate MW
  summed per GEM project): every nuclear plant; hydro >= 1,000 MW (Three Gorges, Baihetan, ...); pumped storage
  >= 1,200 MW; coal >= 3,000 MW; gas >= 1,500 MW; every geothermal plant (only three exist). Wind and solar are
  filed as thousands of projects, so their capacity is **summed per hex** and a hex is shown at >= 1,500 MW.
  Biomass (largest plant < 500 MW), oil-fired and by-product-gas plants are not shown. GEM does not track battery
  storage, so there is no battery type (absent, not 0).
* Coal mines (GEM Global Coal Mine Tracker): operating mines with capacity >= 10 Mt/yr (production when capacity is
  unknown). Iron-ore mines (GEM Global Iron Ore Mines Tracker): capacity >= 5 Mt/yr. Mines of one type in one hex
  are combined into one item (value = summed capacity), so a basin of giant pits (准格尔, 神东) is one icon per hex.
* Major non-ferrous / rare-earth / lithium mines: every site of cn_energy's curated list (Wikidata coordinates;
  capacity unknown → value null).
* Oil and gas fields (GEM Global Oil and Gas Extraction Tracker): operating fields producing >= 5 million barrels of
  oil equivalent a year (GEM's latest reported year per field). oil_gas fields count as oil when oil is at least
  half of the boe, else gas. Fields of one type in one hex are combined; offshore fields snap to the nearest Chinese
  land hex up to FIELD_RINGS hexes away. Field values mix reporting years, so `summary` leaves them unsummed.

Names: Chinese where cn_energy has one (coal, nuclear, large hydro, fields), else a Chinese translation from GAS_ZH
(gas plants, translated by this project from GEM's English name), else a descriptive name from the county
("瓜州县风电场群"); GEM's English name is kept in `name_en`.

Each item carries `state` (province code; GEM's Taiwan / Hong Kong / Macao area tags win), `county` (adcode or null:
DataV has no Taiwan counties) and `cbsa` (cities.json id via Locator.city_of, null until cities.json exists).
`types` is in rank order (rarest/most significant first) for the frontend's one-icon-per-cell rule.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from ..core import china
from ..core.store import load_interim, load_interim_payload, write_web
from .locate import Locator

REQUIRES = ["_world", "cn_boundaries", "cn_energy", "cities.json"]
PRODUCES = ["resources.json"]

SRC = "cn_energy"

# --- thresholds ---------------------------------------------------------------------------------------------
PLANT_MIN = {"nuclear": 0, "hydro": 1000, "pumped_storage": 1200, "coal": 3000, "gas": 1500, "geothermal": 0}  # MW
CLUSTER_MIN = {"wind": 1500, "solar": 1500}  # MW summed per hex
COAL_MINE_MIN = 10_000_000  # t/yr
IRON_MINE_MIN = 5_000_000  # t/yr
FIELD_MIN = 5_000_000  # boe/yr
OFFSHORE_RINGS = 3  # offshore wind snaps to Chinese land up to this many hexes away (other plants and mines: 1)
FIELD_RINGS = 8  # offshore oil & gas fields (Bohai, Pearl River Mouth, East China Sea basins)
METALS = ("copper", "gold", "rare_earths", "lithium", "tungsten", "antimony", "lead_zinc", "bauxite")

PLANT_TYPE = {"coal": "coal_plant", "gas": "gas_plant"}
GEM = "Global Energy Monitor（CC BY 4.0）"
OPERATING = "只计在运机组（GEM 状态 operating），装机为铭牌容量（MW）之和，按 GEM 项目归并。"
CLUSTER = ("风电、光伏项目往往被拆成许多期备案，因此先把同一六边形格内所有在运项目的装机相加，"
           "格内合计达到门槛才显示一个图标。")

RULE = (
    "资源只显示有分量的站点，规则如下（数据：Global Energy Monitor 2026 年版全球电力、煤矿、铁矿、油气开采追踪数据，"
    "CC BY 4.0；主要有色金属/稀土/锂矿坐标来自 Wikidata）："
    f"核电站全部显示；常规水电站装机 ≥ {PLANT_MIN['hydro']:,} MW；抽水蓄能 ≥ {PLANT_MIN['pumped_storage']:,} MW；"
    f"燃煤电厂 ≥ {PLANT_MIN['coal']:,} MW；燃气电厂 ≥ {PLANT_MIN['gas']:,} MW；地热电站全部显示；"
    f"风电、光伏按六边形格合计装机 ≥ {CLUSTER_MIN['wind']:,} MW。"
    f"煤矿产能 ≥ {COAL_MINE_MIN // 10_000:,} 万吨/年；铁矿产能 ≥ {IRON_MINE_MIN // 10_000:,} 万吨/年；同格同类矿合并为一个图标。"
    "主要有色金属、稀土、锂矿为 cn_energy 的精选清单（全部显示，产能无公开数据）。"
    f"油气田年产量 ≥ {FIELD_MIN // 10_000:,} 万桶油当量（GEM 最近报告年份），同格同类合并，海上油气田标在最近的中国陆地格。"
    "生物质、燃油及工业副产煤气电厂不显示；GEM 不追踪电池储能，因此没有储能图标（缺数据，不是 0）。"
    "台湾、香港、澳门的电厂按 GEM 的地区标注计入各自省级单位。")


def _t(id, name, category, emoji, color, unit, description):
    return {"id": id, "name": name, "category": category, "emoji": emoji, "icon": f"r-{id}", "color": color,
            "unit": unit, "description": description, "source": SRC}


def _metal(id, name, emoji, color):
    return _t(id, name, "mineral", emoji, color, "吨/年",
              f"cn_energy 精选的国内代表性{name.removesuffix('矿')}矿山（全部显示），坐标来自 Wikidata；"
              "单矿产能没有公开数据，数值为空（未知，不是 0）。")


TYPES = [
    _t("nuclear", "核电站", "energy", "☢️", "#e8d44d", "MW",
       f"每一座有在运机组的核电站。数值为在运机组铭牌装机之和。{OPERATING}"),
    _t("hydro", "水电站", "energy", "💧", "#3fa7d6", "MW",
       f"装机 ≥ {PLANT_MIN['hydro']:,} MW 的常规水电站（含三峡、白鹤滩、溪洛渡）。{OPERATING}"),
    _t("coal_mine", "煤矿", "mineral", "⛏️", "#4a4038", "吨/年",
       f"核定产能 ≥ {COAL_MINE_MIN // 10_000:,} 万吨/年的在产煤矿（GEM Global Coal Mine Tracker；产能缺失时用产量）。"
       "同一格内的多座煤矿合并为一个图标，数值为产能之和。"),
    _metal("rare_earths", "稀土矿", "🧲", "#b05fc7"),
    _metal("lithium", "锂矿", "🧪", "#c7e3f0"),
    _metal("tungsten", "钨矿", "🔩", "#6f7f8f"),
    _metal("antimony", "锑矿", "🪨", "#8e8e9e"),
    _metal("gold", "金矿", "🥇", "#d4af37"),
    _metal("copper", "铜矿", "🥉", "#b87333"),
    _metal("lead_zinc", "铅锌矿", "🪨", "#7d8a99"),
    _metal("bauxite", "铝土矿", "🧱", "#c98b5b"),
    _t("iron", "铁矿", "mineral", "⛓️", "#8a4b2a", "吨/年",
       f"产能 ≥ {IRON_MINE_MIN // 10_000:,} 万吨/年的在产铁矿（GEM Global Iron Ore Mines Tracker）。"
       "同一格内的多座铁矿合并为一个图标，数值为产能之和。"),
    _t("coal_plant", "燃煤电厂", "energy", "🏭", "#5a5a5a", "MW",
       f"在运燃煤装机 ≥ {PLANT_MIN['coal']:,} MW 的电厂。{OPERATING}"),
    _t("gas_plant", "燃气电厂", "energy", "⚡", "#f08a24", "MW",
       f"在运燃气装机 ≥ {PLANT_MIN['gas']:,} MW 的电厂（含 LNG、煤层气）。中文名由本项目据 GEM 英文名翻译。{OPERATING}"),
    _t("geothermal", "地热电站", "energy", "♨️", "#d9534f", "MW",
       f"每一座在运地热电站（全国仅三座，装机都很小）。{OPERATING}"),
    _t("pumped_storage", "抽水蓄能", "energy", "🔄", "#2f7fb8", "MW",
       f"装机 ≥ {PLANT_MIN['pumped_storage']:,} MW 的抽水蓄能电站：由上下两座水库组成的电网“蓄电池”。{OPERATING}"),
    _t("wind", "风电", "energy", "🌬️", "#9fd8cb", "MW",
       f"风电装机，六边形格合计 ≥ {CLUSTER_MIN['wind']:,} MW 时显示。{CLUSTER}"
       f"海上风电标在最近的中国海岸格（最远 {OFFSHORE_RINGS} 格）。{OPERATING}"),
    _t("solar", "光伏", "energy", "☀️", "#ffc93c", "MW",
       f"集中式光伏（含少量光热）装机，六边形格合计 ≥ {CLUSTER_MIN['solar']:,} MW 时显示（不含屋顶分布式）。"
       f"{CLUSTER}{OPERATING}"),
    _t("oil", "油田", "energy", "🛢️", "#3a2f2a", "桶油当量/年",
       f"年产量 ≥ {FIELD_MIN // 10_000:,} 万桶油当量的在产油田（GEM Global Oil and Gas Extraction Tracker，"
       "各油田为 GEM 最近报告年份的产量，年份不一）。油气兼产的田以石油占油当量一半以上者归为油田。"
       f"同格多个油田合并；海上油田标在最近的中国陆地格（最远 {FIELD_RINGS} 格）。年份不一，合计值不汇总。"),
    _t("gas", "气田", "energy", "🔥", "#8fb3c9", "桶油当量/年",
       f"年产量 ≥ {FIELD_MIN // 10_000:,} 万桶油当量的在产气田（含页岩气、致密气、煤层气田；GEM Global Oil and Gas "
       "Extraction Tracker，各气田为 GEM 最近报告年份的产量）。同格多个气田合并；海上气田标在最近的中国陆地格。"
       "年份不一，合计值不汇总。"),
]
RANK = {t["id"]: i for i, t in enumerate(TYPES)}
NOT_ADDITIVE = {"oil", "gas"}
SUMMARY_NOTE = ("仅统计地图上显示的站点：每类都有规模门槛（见各类说明），因此这些合计不是全国或各省的全部装机或产量。"
                "油气田产量年份不一，不做加总（为空）。")

# Gas plants >= PLANT_MIN["gas"]: GEM English name → Chinese name (this project's translation; GEM has none).
GAS_ZH = {
    "Datan power station": "大潭发电厂",
    "Black Point power station": "龙鼓滩发电厂",
    "Hsinta power station": "兴达发电厂",
    "Tunghsiao power station": "通霄发电厂",
    "Yudean Huizhou Gas power station": "粤电惠州天然气发电厂",
    "Dongguan Ningzhou Gas power station": "东莞宁洲燃气电厂",
    "Huaneng Chongqing Liangjiang Gas power station": "华能重庆两江燃机电厂",
    "Hangzhou Banshan power station": "杭州半山发电厂",
    "CNOOC Zhongshan Jiaming Gas power station": "中海油中山嘉明电厂",
    "Shenzhen Energy East power station": "深能东部电厂",
    "Sichuan Investment Group Dazhou Gas power station": "川投达州燃气电厂",
    "Huadian Qishuyan Gas power station": "华电戚墅堰燃机电厂",
    "Fong Der power station": "丰德发电厂",
    "CKI Lamma power station": "南丫发电厂",
    "Guangzhou Pearl River LNG power station": "广州珠江 LNG 电厂",
    "Huaneng Dongguan Gas Thermal power station": "华能东莞燃机热电厂",
    "Huaneng Beijing power station": "华能北京热电厂",
    "Chentangzhuang power station": "陈塘庄热电厂",
    "Shenzhen Energy Guangming Gas power station": "深能光明燃气电厂",
    "Dongguan Zhongtang Gas Thermal power station": "东莞中堂燃气热电厂",
    "Anji Meixi Gas power station": "安吉梅溪燃气电厂",
    "Wangting power station": "望亭发电厂",
    "Shenergy Shanghai Lingang Gas power station": "申能上海临港燃气电厂",
    "Huachen Zhangjiagang Huaxing Gas power station": "张家港华兴燃气电厂",
    "Huadian Shanghai Fengxian Gas power station": "华电上海奉贤燃机电厂",
    "CNOOC Putian Gas power station": "中海油莆田燃气电厂",
    "Jinjiang Gas power station": "晋江燃气电厂",
    "Yuehua Huangpu power station": "粤华黄埔电厂",
}
# Hydro / pumped-storage / geothermal plants above the thresholds that cn_energy leaves without a Chinese name
# (GEM English name → this project's translation), and one field.
EXTRA_ZH = {
    "Suwalong hydroelectric plant": "苏洼龙水电站",
    "Tianshengqiao Stage 1 hydroelectric plant": "天生桥一级水电站",
    "Shatuo hydroelectric plant": "沙沱水电站",
    "Yingliangbao hydroelectric plant": "硬梁包水电站",
    "Tingzikou hydroelectric plant": "亭子口水电站",
    "Wanjiazhai hydroelectric plant": "万家寨水电站",
    "Danjiangkou hydroelectric plant": "丹江口水电站",
    "Silin hydroelectric plant": "思林水电站",
    "Zhexi hydroelectric plant": "柘溪水电站",
    "Guangzhao hydroelectric plant": "光照水电站",
    "Jishixia hydroelectric plant": "积石峡水电站",
    "Sanbanxi hydroelectric plant": "三板溪水电站",
    "Tibetan Yangyi geothermal power plant": "羊易地热电站",
    "Qingshui geothermal power plant": "清水地热电站",
    "Sihuangziping geothermal power plant": "四磺子坪地热电站",
    "Fukang hydroelectric plant": "阜康抽水蓄能电站",
    "Heimifeng hydroelectric plant": "黑麋峰抽水蓄能电站",
    "Hohhot hydroelectric plant": "呼和浩特抽水蓄能电站",
    "Hongping hydroelectric plant": "洪屏抽水蓄能电站",
    "Huanggou hydroelectric plant": "荒沟抽水蓄能电站",
    "Jinzhai hydroelectric plant": "金寨抽水蓄能电站",
    "Meizhou II hydroelectric plant": "梅州抽水蓄能电站二期",
    "Meizhou hydroelectric plant": "梅州抽水蓄能电站",
    "Panlong hydroelectric plant": "蟠龙抽水蓄能电站",
    "Pushihe hydroelectric plant": "蒲石河抽水蓄能电站",
    "Shenzhen hydroelectric plant": "深圳抽水蓄能电站",
    "Tianchi hydroelectric plant": "天池抽水蓄能电站",
    "Tongbai hydroelectric plant": "桐柏抽水蓄能电站",
    "Xianyou hydroelectric plant": "仙游抽水蓄能电站",
    "Xilongchi hydroelectric plant": "西龙池抽水蓄能电站",
    "Yimeng hydroelectric plant": "沂蒙抽水蓄能电站",
    "Changbei Tight I Gas Phase": "长北致密气田（一期）",
}
AREA_CODE = {"Taiwan": china.TAIWAN if hasattr(china, "TAIWAN") else "71", "Hong Kong": "81", "Macao": "82",
             "Macau": "82"}
MINE_KIND = {"surface": "露天矿", "underground": "井工矿", "surface/underground": "露天/井工矿"}
_PLANT_WORD = re.compile(r"(电厂|電廠|电站|電站|热电|熱電|发电|發電)")


def _fmt(n: float) -> str:
    return f"{n:,.0f}"


def _wan(n: float) -> str:
    """Tonnes / barrels as 万 with a sensible precision ('4,000 万')."""
    v = n / 1e4
    return f"{v:,.0f} 万" if v >= 100 else f"{v:,.1f} 万".replace(".0 万", " 万")


def _years(a, b) -> str:
    if not a:
        return ""
    return f"{a} 年投运" if not b or a == b else f"{a}–{b} 年投运"


def zh_name(name: str | None) -> str | None:
    """GEM sometimes lists several Chinese names, comma-separated ('江阴利港…扩建项目, 中信江苏利港发电厂'): prefer
    the shortest variant that names a plant, else the first."""
    if not name:
        return None
    parts = [p.strip() for p in re.split(r"[,，]\s*", name) if p.strip()]
    plants = [p for p in parts if _PLANT_WORD.search(p) and "项目" not in p and "工程" not in p]
    return min(plants, key=len) if plants else parts[0]


def _owner(owner: str | None) -> str | None:
    """GEM's owner when it names a single company; None for a shareholder list (its order is not by share)."""
    return owner.strip() or None if owner and ";" not in owner else None


_COMPANY = re.compile(r"^.{2,}?(有限责任公司|股份有限公司|有限公司|集团公司)")


def short_mine(name: str | None) -> str | None:
    """'中国神华能源股份有限公司哈尔乌素露天煤矿' → '哈尔乌素露天煤矿' (keeps the name when little would remain)."""
    if not name:
        return name
    rest = _COMPANY.sub("", name, count=1).strip()
    return rest if len(rest) >= 3 else name


class Places:
    """Chinese place names for descriptive labels: county (DataV) → else nearest prefecture seat of the province →
    else the province short name."""

    def __init__(self, loc: Locator):
        b = load_interim("cn_boundaries")
        self.loc = loc
        self.county = {c["adcode"]: c["name"] for c in b["counties"]}
        self.prefectures = [(p["province"], p["name"], loc.proj(*p["seat"])) for p in b["prefectures"] if p.get("seat")]

    def name(self, county: str | None, state: str | None, x: float, y: float) -> str:
        if county and county in self.county:
            return self.county[county]
        near = [(abs(px - x) + abs(py - y), n) for prov, n, (px, py) in self.prefectures if prov == state]
        if near:
            return min(near)[1]
        return china.PROVINCES[state][1] if state in china.PROVINCES else "海上"


def _item(type_id, name, x, y, cell, value, detail, filed=None, name_en=None):
    it = {"type": type_id, "name": name, "x": x, "y": y, "cell": cell,
          "value": None if value is None else round(value), "detail": detail, "_filed": filed}
    if name_en and name_en != name:
        it["name_en"] = name_en
    return it


def _plant_type(fuel: str) -> str:
    return PLANT_TYPE.get(fuel, fuel)


def power_plants(loc: Locator, plants: list[dict]) -> tuple[list[dict], list[tuple]]:
    """Single plants meeting PLANT_MIN, plus (fuel, cell, group) clusters for wind / solar (named later)."""
    items, clusters = [], defaultdict(list)
    for p in plants:
        fuel, mw = p["fuel"], p["capacity_mw"] or 0
        if fuel not in PLANT_MIN and fuel not in CLUSTER_MIN:
            continue
        x, y, cell = loc.place(p["lon"], p["lat"], OFFSHORE_RINGS if p.get("offshore") else 1)
        if cell < 0:
            continue
        filed = AREA_CODE.get(p.get("area"))
        if fuel in CLUSTER_MIN:
            clusters[(fuel, cell)].append((p, x, y, filed))
            continue
        if mw < PLANT_MIN[fuel]:
            continue
        name = GAS_ZH.get(p["name_en"]) or zh_name(p["name"]) or EXTRA_ZH.get(p["name_en"]) or p["name_en"]
        unit_word = "台机组" if fuel != "nuclear" else "台机组（反应堆）"
        units = f"{p['units']} {unit_word}" if p.get("units") and fuel not in ("hydro", "pumped_storage") else ""
        detail = "，".join(s for s in (units,
                                        _years(p.get("year"), p.get("year_last")),
                                        f"业主 {_owner(p.get('owner'))}" if _owner(p.get("owner")) else "") if s)
        items.append(_item(_plant_type(fuel), name, x, y, cell, mw, detail, filed, p["name_en"]))
    out = []
    for (fuel, cell), group in clusters.items():
        if sum(g[0]["capacity_mw"] or 0 for g in group) >= CLUSTER_MIN[fuel]:
            out.append((fuel, cell, group))
    return items, out


def cluster_items(clusters, places: Places, loc: Locator) -> list[dict]:
    items = []
    word = {"wind": "风电场", "solar": "光伏电站"}
    for fuel, cell, group in clusters:
        group.sort(key=lambda g: -(g[0]["capacity_mw"] or 0))
        mw = sum(g[0]["capacity_mw"] or 0 for g in group)
        tot = mw or 1
        x = sum(g[1] * (g[0]["capacity_mw"] or 0) for g in group) / tot
        y = sum(g[2] * (g[0]["capacity_mw"] or 0) for g in group) / tot
        lead, filed = group[0][0], group[0][3]
        county = loc.county_at(x, y, cell)
        state = loc.province_of(county, cell, filed)
        place = places.name(county, state, x, y)
        offshore = any(g[0].get("offshore") for g in group)
        label = ("海上" if offshore and all(g[0].get("offshore") for g in group) else "") + word[fuel]
        name = f"{place}{label}" + ("群" if len(group) > 1 else "")
        years = [g[0]["year"] for g in group if g[0].get("year")]
        detail = (f"本格 {len(group)} 个在运项目合计；最大为 {lead['name_en']}（{_fmt(lead['capacity_mw'])} MW）"
                  if len(group) > 1 else f"{lead['name_en']}")
        if years:
            detail += f"；{_years(min(years), max(years))}"
        if fuel == "solar" and any(g[0].get("tech") == "solar_thermal" for g in group):
            detail += "；含光热电站"
        if offshore:
            detail += "；含海上风电，标在最近的海岸格"
        items.append(_item(fuel, name, x, y, cell, mw, detail, filed))
    return items


def _combine(type_id, group, fmt_one, value_of, places: Places, loc: Locator, what: str) -> dict:
    """One item for several sites of one type in one hex: named after the largest, value = summed value."""
    group.sort(key=lambda g: -(value_of(g[0]) or 0))
    lead, x, y, cell = group[0]
    vals = [value_of(g[0]) for g in group if value_of(g[0]) is not None]
    value = sum(vals) if vals else None
    def label(r):
        return short_mine(r.get("name")) or EXTRA_ZH.get(r.get("name_en")) or r.get("name_en")

    name = label(lead)
    if len(group) == 1:
        return _item(type_id, name, x, y, cell, value, fmt_one(lead), name_en=lead.get("name_en"))
    others = "、".join(label(g[0]) for g in group[1:4])
    more = f"等 {len(group) - 1} 处" if len(group) > 4 else ""
    detail = f"本格共 {len(group)} 处{what}，数值为合计；最大者：{fmt_one(lead)}；另有 {others}{more}"
    return _item(type_id, f"{name}等 {len(group)} 处", x, y, cell, value, detail, name_en=lead.get("name_en"))


def mines(loc: Locator, places: Places, rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for m in rows:
        c = m["commodity"]
        size = m.get("capacity") or m.get("production")
        if c == "coal" and (size or 0) < COAL_MINE_MIN:
            continue
        if c == "iron" and (size or 0) < IRON_MINE_MIN:
            continue
        if c not in ("coal", "iron") and c not in METALS:
            continue
        if m.get("lon") is None or m.get("lat") is None:
            continue
        x, y, cell = loc.place(m["lon"], m["lat"])
        if cell >= 0:
            t = "coal_mine" if c == "coal" else c
            groups[(t, cell)].append((m, x, y, cell))

    def size(m):
        return m.get("capacity") or m.get("production")

    def one(m):
        bits = []
        if m["commodity"] == "coal":
            bits.append(MINE_KIND.get(m.get("mine_type"), "煤矿"))
            if m.get("capacity"):
                bits.append(f"产能 {_wan(m['capacity'])}吨/年")
            if m.get("production"):
                bits.append(f"{m.get('production_year') or ''} 年产量 {_wan(m['production'])}吨".strip())
        elif m.get("capacity"):
            bits.append(f"产能 {_wan(m['capacity'])}吨/年")
        if _owner(m.get("owner")):
            bits.append(f"业主 {_owner(m['owner'])}")
        if m.get("year"):
            bits.append(f"{m['year']} 年投产")
        if m["commodity"] in METALS:
            bits.append("产能无公开数据")
        return "，".join(bits) or (m.get("name_en") or "")

    items = []
    for (t, cell), group in groups.items():
        items.append(_combine(t, group, one, size, places, loc, "煤矿" if t == "coal_mine" else "矿山"))
    return items


def field_kind(f: dict) -> str:
    if f["kind"] in ("oil", "gas"):
        return f["kind"]
    if f.get("oil_bbl") is not None and f.get("production"):
        return "oil" if f["oil_bbl"] >= 0.5 * f["production"] else "gas"
    return "oil"


SAR_TW = {"71", "81", "82"}


def snap_offshore(loc: Locator, x: float, y: float, cell: int) -> int:
    """An offshore site snapped onto a Taiwan / Hong Kong / Macao hex (Pearl River Mouth fields next to the one-hex
    SARs) moves to the nearest mainland land hex within FIELD_RINGS instead: those fields are mainland (CNOOC) fields,
    not the SAR's."""
    if loc.codes[loc.owner[cell]] not in SAR_TW:
        return cell
    g, best = loc.grid, None
    seen, frontier = {cell}, [cell]
    for _ in range(FIELD_RINGS):
        frontier = [n for c in frontier for n in g.neighbours(c) if n not in seen]
        seen.update(frontier)
        land = [n for n in frontier if loc.owner[n] >= 0 and loc.codes[loc.owner[n]] not in SAR_TW]
        if land:
            best = min(land, key=lambda n: (g.center(*g.col_row(n))[0] - x) ** 2 + (g.center(*g.col_row(n))[1] - y) ** 2)
            break
    return best if best is not None else cell


def fields(loc: Locator, places: Places, rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for f in rows:
        if (f.get("production") or 0) < FIELD_MIN or f.get("lon") is None:
            continue
        x, y, cell = loc.place(f["lon"], f["lat"], FIELD_RINGS)
        if cell < 0:
            continue
        offshore = loc.land_cell(x, y, 0) != cell
        if offshore:
            cell = snap_offshore(loc, x, y, cell)
        f = dict(f, _offshore=offshore)
        groups[(field_kind(f), cell)].append((f, x, y, cell))

    def one(f):
        bits = [b for b in (f.get("basin"), f"作业者 {f.get('operator_zh') or f.get('operator')}"
                            if (f.get("operator_zh") or f.get("operator")) else None) if b]
        bits.append(f"{f.get('year') or '?'} 年产量约 {_wan(f['production'])}桶油当量")
        if f.get("start_year"):
            bits.append(f"{f['start_year']} 年投产")
        if f["_offshore"]:
            bits.append("海上油气田，标在最近的中国陆地格")
        return "，".join(bits)

    items = []
    for (t, cell), group in groups.items():
        items.append(_combine(t, group, one, lambda f: f.get("production"), places, loc,
                              "油田" if t == "oil" else "气田"))
    return items


def locate(items: list[dict], loc: Locator) -> int:
    """Settle x/y onto the cell, set county / state / cbsa. Returns how many points were moved to the cell centre."""
    moved = 0
    for it in items:
        x, y = loc.settle(it["x"], it["y"], it["cell"])
        moved += (x, y) != (round(it["x"], 1), round(it["y"], 1))
        county = loc.county_at(it["x"], it["y"], it["cell"])
        it["x"], it["y"] = x, y
        it["state"] = loc.province_of(county, it["cell"], it.pop("_filed", None))
        it["county"] = county
        it["cbsa"] = loc.city_of(county)
    return moved


def summarize(items: list[dict], types: list[dict]) -> dict:
    """National and per-province {type: {count, value, unit}} over the mapped items."""
    def blank(t):
        return {"count": 0, "value": None if t["id"] in NOT_ADDITIVE else 0, "unit": t["unit"]}

    by_id = {t["id"]: t for t in types}
    national = {t["id"]: blank(t) for t in types}
    states: dict[str, dict] = {}
    for it in items:
        t = by_id[it["type"]]
        rows = [national[t["id"]]]
        if it["state"]:
            rows.append(states.setdefault(it["state"], {}).setdefault(t["id"], blank(t)))
        for row in rows:
            row["count"] += 1
            if row["value"] is not None and it["value"] is not None:
                row["value"] += it["value"]
    for row in [*national.values(), *(r for st in states.values() for r in st.values())]:
        if row["value"] is not None:
            row["value"] = round(row["value"])
    return {"note": SUMMARY_NOTE, "national": national, "states": dict(sorted(states.items()))}


def build() -> None:
    loc = Locator()
    places = Places(loc)
    payload = load_interim_payload(SRC)
    energy = payload["data"]
    items, clusters = power_plants(loc, energy["plants"])
    items += cluster_items(clusters, places, loc)
    items += mines(loc, places, energy["mines"])
    items += fields(loc, places, energy["fields"])
    items.sort(key=lambda it: (RANK[it["type"]], -(it["value"] or 0), it["name"]))
    moved = locate(items, loc)
    present = {it["type"] for it in items}
    types = [t for t in TYPES if t["id"] in present]
    vintage = payload.get("vintage")
    write_web("resources.json", {"vintage": vintage, "rule": RULE, "sources": [SRC], "types": types, "items": items,
                                 "summary": summarize(items, types)})
    counts = Counter(it["type"] for it in items)
    print("    " + ", ".join(f"{t['id']} {counts[t['id']]}" for t in TYPES))
    print(f"    {len(items)} items; {moved} sat off their land hex and were moved to its centre; "
          f"{sum(1 for it in items if it['cbsa'])} in a cities.json city")


# --- builder check ------------------------------------------------------------------------------------------


def check(web_dir: Path) -> list[tuple[str, str]]:
    """China-specific checks of resources.json (the generic shape/summary checks live in pipeline/check.py)."""
    path = Path(web_dir) / "resources.json"
    if not path.exists():
        return [("NOTE", "resources.json not built (optional; run: build resources --deps)")]
    doc = json.loads(path.read_text())
    items = doc.get("items") or []
    out: list[tuple[str, str]] = []
    bad_state = [it.get("name") for it in items if it.get("state") is not None and it["state"] not in china.PROVINCES]
    if bad_state:
        out.append(("FAIL", f"resources.json: {len(bad_state)} items whose state is not a province code, e.g. {bad_state[:3]}"))
    bad_county = [it.get("name") for it in items if it.get("county") is not None and
                  not (isinstance(it["county"], str) and len(it["county"]) == 6 and it["county"][:2] == it.get("state"))]
    if bad_county:
        out.append(("WARN", f"resources.json: {len(bad_county)} items whose county is not in their province, "
                            f"e.g. {bad_county[:3]}"))
    zeros = [it.get("name") for it in items if it.get("value") == 0]
    if zeros:
        out.append(("FAIL", f"resources.json: value 0 (unknown must be null) for {zeros[:3]}"))
    no_state = sum(1 for it in items if not it.get("state"))
    if no_state:
        out.append(("WARN", f"resources.json: {no_state} items without a province"))
    if "battery" in {t.get("id") for t in doc.get("types", [])}:
        out.append(("FAIL", "resources.json lists battery storage, which the source does not track"))
    wpath = Path(web_dir) / "world.json"
    if wpath.exists():
        from .world import is_production_grid

        if is_production_grid(json.loads(wpath.read_text()).get("grid")):
            # production data only (the synthetic test datasets carry neither a rule nor Chinese names)
            if not doc.get("rule"):
                out.append(("FAIL", "resources.json lacks the published selection 'rule'"))
            latin = [it.get("name") for it in items if not re.search(r"[\u4e00-\u9fff]", it.get("name") or "")]
            if latin:
                out.append(("WARN", f"resources.json: {len(latin)} items without a Chinese name, e.g. {latin[:3]}"))
            missing = [n for n, test in (
                ("三峡水电站", lambda it: it["type"] == "hydro" and "三峡" in it["name"]),
                ("白鹤滩水电站", lambda it: it["type"] == "hydro" and "白鹤滩" in it["name"]),
                ("大亚湾核电站", lambda it: it["type"] == "nuclear" and "大亚湾" in it["name"]),
                ("台湾的电厂", lambda it: it.get("state") == "71"),
                ("香港的电厂", lambda it: it.get("state") == "81"),
            ) if not any(test(it) for it in items)]
            if missing:
                out.append(("FAIL", f"resources.json is missing expected sites: {missing}"))
            if not 300 <= len(items) <= 700:
                out.append(("WARN", f"resources.json has {len(items)} items (thresholds aim at about 300–600)"))
    size = path.stat().st_size
    if size > 300_000:
        out.append(("WARN", f"resources.json is {size / 1e3:.0f} kB (eager files should stay under ~300 kB)"))
    if not any(lv == "FAIL" for lv, _ in out):
        per = Counter(it.get("type") for it in items)
        out.append(("PASS", f"resources.json: {len(items)} items, {len(per)} types, "
                            f"{sum(1 for it in items if it.get('state') in ('71', '81', '82'))} in 台湾/香港/澳门"))
    return out
