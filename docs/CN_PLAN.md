# chinaciv — phase 1 plan and China data contract

This file is the handoff between the foundation work (already done, see §0) and the parallel agents that build
phase 1. It is binding where it says **must**; where it says *suggested* the owning agent may choose and must
document the choice (in its `SourceInfo`, in the builder's published `rule` text, and in a short note at the end
of this file under "Decisions log"). `docs/DATA_CONTRACTS.md` still describes every web/data file; where China
differs, **this file wins** until DATA_CONTRACTS.md is updated by the file's owner (see §8).

Global rules (repeat of the task rules, all **must**):

* `null` means unknown, never `0`. A real zero is written as `0` only when the publisher says zero.
* Every number traces to a source listed in the Almanac (`SourceInfo` + files cached via `Source.download()`,
  so `catalog` cites URL, sha256 and date). No API keys. Python standard library only.
* Identifiers and code in English; every user-facing string (SourceInfo title/description/used_for, rule texts,
  names, labels, notes) in **Simplified Chinese**.
* No military content anywhere. No new git commits. Do not edit files owned by another agent (§8); ask in your
  report instead.
* Coordinates: lon/lat in interims. DataV is GCJ-02, the other sources (OSM, Wikidata, WRI, OurAirports,
  UNESCO, ETOPO, Natural Earth) are WGS-84. The difference is ≤ ~0.6 km, far below a 25 km hex: **never convert**,
  just document (cn_boundaries already does).

---

## 0. Status of the foundation (done)

| Piece | File | State |
|---|---|---|
| China projection, fixed map frame, inset projection, ring thinning | `pipeline/core/geo.py` | done |
| Province table, special ids, Taiwan cities, island labels, name helpers | `pipeline/core/china.py` | done |
| User-Agent `chinaciv-visualizer/0.1` | `pipeline/core/fetch.py` | done |
| Boundaries + seats + dashed line + island points (fetched, processed) | `pipeline/sources/cn_boundaries.py` | done, interim 11.6 MB |
| Neighbours (Natural Earth 1:10m, China point of view), lakes, rivers with Chinese names | `pipeline/sources/natural_earth.py` (`natural_earth`) | done |
| Natural Earth roads/rail, China filter (fallback only) | `pipeline/sources/natural_earth.py` (`natural_earth_roads`) | done, not fetched |
| ETOPO1 window lon 63–145°E, lat 12–57°N, stride 5 (~9 km), 533k samples | `pipeline/sources/etopo.py` | done, fetched + processed |
| China `Locator` (lon/lat → cell, county, city, province) for site builders | `pipeline/build/locate.py` | done (needs `_world`, §5.1) |
| xlsx reader + checked downloads (for yearbook tables) | `pipeline/sources/_resource_util.py` | kept (generic) |

Deleted: all 26 US-only source modules; builders `military`, `trade`, `disasters`, `citizens`, `districts`,
`counties`, `lenses` (US county lenses); tests of those modules (`test_military`, `test_trade`, `test_citizens`,
`test_districts`, `test_county_lenses`, `test_growth`, `test_city_view`).
Still US code, to be rewritten by their owners (§8): builders `world`, `cities`, `growth`, `yields`, `states`,
`infrastructure`, `resources`, `wonders` (imports patched to `locate.py`), `pipeline/check.py`, tests
`test_wonders`, `test_infrastructure`, `test_check`, `test_builder_checks`, `test_gdp_fill`, and all of `web/`.
`uv run python -m pipeline list` works.

---

## 1. Scope

**In phase 1:** painted hex terrain (ETOPO relief + real land cover), provinces with colours and Chinese names,
major rivers and lakes, coastline; a banner for every prefecture-level division (333 = 293 地级市 + 7 地区 + 30
自治州 + 3 盟) plus the 4 municipalities, Hong Kong, Macau and Taiwan's six largest cities (345 banners) with real
population and GDP (Gold) and the other four yields; tile improvements (HSR, conventional rail, expressways,
busiest airports, principal ports); resources (power plants by fuel, coal mines / major mineral sites, oil & gas
fields); wonders by published rules (UNESCO World Heritage, 国家公园, 5A scenic areas, top universities); fully
Simplified-Chinese UI; Almanac listing every source plus the compliance notice.

**Out of scope (removed from pipeline and hidden cleanly from the UI):** military, faith/religion, trade/freight
routes, disasters, county-level lenses, citizens, districts (and the City View panels built on them), the
Advisors that only show those (Military, Commerce/trade, faith parts of Culture). County polygons/ids remain for
lookups.

---

## 2. Map frame, projection, inset, territory

### 2.1 Projection (must; implemented in `core/geo.py`)

* Albers equal-area conic **on a sphere** (R = 6371.0088 km): `lat1 = 25°N`, `lat2 = 47°N`, `lat0 = 0°`,
  `lon0 = 105°E` (`CHINA_ALBERS`). Map km: `x = albers_x − min_x + margin`, `y = max_y − albers_y + margin`
  (origin top-left, y down), exactly as before.
* The frame is **fixed** (not fitted at build time): `ALBERS_BOUNDS = (−2619, 1884, 2202, 5939)` = the national
  outline without polygons wholly south of 17.5°N (the South China Sea islands) and without the dashes,
  rounded outwards. `min_x = −2619`, `max_y = 5939`, `margin = 200` (left/top/bottom), right margin 450 (room
  for the inset east of Luzon). **`MAP_WIDTH_KM = 5471`, `MAP_HEIGHT_KM = 4455`.**
* Use `core.geo.china_projection()` anywhere you need map km before `_world` exists; builders read
  `MapProjection(**_world["projection"])` (same numbers). `MapProjection.to_dict()` now also carries
  `lat1, lat2, lat0, lon0`; world.json must publish the full dict as `projection` (§5.1) so the browser
  stops hard-coding US parameters.
* Hex grid unchanged: pointy-top odd-r, radius **25 km** (`HexGrid.covering(5471, 4455, 25)` = **128 × 120 =
  15,360 cells**). A dry run of the 7-sample majority rule gives **≈ 5,860 Chinese land hexes** (新疆 1003,
  西藏 737, 内蒙古 701, 青海 426, 四川 305 … 上海 6, 香港 1, 澳门 0, 台湾 23, 海南 23).
* The frame's corners are lon 64.3–143.5°E, lat 13.0–56.6°N. Foreign land inside it (Russia, Mongolia, Central
  Asia, South Asia, Southeast Asia, Korea, Japan, the Philippines) is fogged as before (`owner = −2`).

### 2.2 南海诸岛 inset (must; geometry implemented in `core/geo.py` `InsetProjection`)

* The main grid covers the mainland down to ~18°N (Hainan's southern tip is at map y ≈ 4254). The South China Sea
  islands and the full ten-dash line are drawn in an **inset box in the lower-right corner**, as on standard
  Chinese maps, titled **南海诸岛**.
* Inset region `INSET_REGION = (106.5, 2.8, 123.5, 24.8)` (lon/lat), drawn with the **same Albers** scaled by
  `INSET_SCALE = 0.3`, 12 km padding, 25 km from the map's bottom/right edges. Box in map km:
  **`[4780.7, 3655.7, 5446.0, 4430.0]`** (665 × 774 km; it clears Luzon by ~70 km and the Ryukyus).
  `InsetProjection()(lon, lat) → (x, y)` in map km inside the box.
* The inset is **vector only** (no hexes): land polygons (China from cn_boundaries provinces 44/45/46/71/35 and
  the SAR polygons; neighbours from natural_earth), all 10 dashes, island-group labels, 三沙市 seat label. The
  cells under the box stay ordinary water; the frontend draws the box opaque on top of the map (§6).
* Dashes inside the main frame are **also** drawn on the main map, clipped to the frame and **not under the inset
  box**: today dashes 1–3 (east of Taiwan, Bashi Channel, west of Luzon at 18–19°N) fully and dash 4 (119°E,
  15–16°N) partly. Islands inside the main frame (钓鱼岛, 赤尾屿, 东沙群岛, 西沙群岛) and the 三沙市 seat (永兴岛) are
  labelled on the main map too; 东沙群岛 and 西沙群岛 appear in both.

### 2.3 Territory and compliance (must)

* China's national, provincial, prefecture and county boundaries come **only** from cn_boundaries (DataV
  GeoAtlas, which follows the standard map): Taiwan (台湾省), Hong Kong, Macau, Hainan + South China Sea islands,
  藏南 and 阿克赛钦 are inside. Natural Earth is used **only** for neighbours (its "China point of view" file) and
  for lakes/rivers; wherever a Natural Earth country polygon overlaps a cn_boundaries province, **the province
  wins** (the `world` builder tests provinces first). `natural_earth` already drops CHN, TWN, HKG, MAC and SCR
  (黄岩岛, which Natural Earth carries as its own feature).
* 钓鱼岛 and 赤尾屿 are labelled as Chinese island names on the main map (they are inside 台湾省's DataV polygons;
  too small for a hex, so they are **labelled points**, not land hexes).
* Taiwan is drawn and listed **as a province** (`"71"`, 台湾省, kind `province`). Never call Taiwan a country,
  never label its cities "直辖市"; Taiwan's cities are plain "市". Where PRC statistics do not cover Taiwan, Hong
  Kong or Macau, use their own official statistics if easy, else `null`, and say so in the field's note.
* The notice **"本地图仅供个人学习，未申请审图号。"** and the boundary source (§5.1 `notice`) must appear in the
  Almanac and in the help (`?`), and as a small footnote on share images.

---

## 3. Identifiers and concept mapping

### 3.1 Ids (must)

| Thing | Id | Example |
|---|---|---|
| Province (省级, 34) | 2-digit adcode prefix, string | `"11"` 北京, `"44"` 广东, `"65"` 新疆, `"71"` 台湾, `"81"` 香港, `"82"` 澳门 |
| Prefecture-level unit = **city** | 6-digit adcode, string | `"440300"` 深圳, `"532900"` 大理州, `"152500"` 锡林郭勒盟 |
| Municipality / SAR as a city | the province adcode | `"110000"` 北京, `"500000"` 重庆, `"810000"` 香港, `"820000"` 澳门 |
| Taiwan city (project-assigned, **not official**) | `"710100"`…`"710600"` | 台北, 新北, 桃园, 台中, 台南, 高雄 (`core/china.py TAIWAN_CITIES`) |
| County-level unit (区/县/县级市/旗…, 2,874) | 6-digit adcode, string | `"110105"` 朝阳区 |
| 省直辖县级行政区 (30: 济源, 仙桃, 潜江, 天门, 神农架, 15 in Hainan, 10 in Xinjiang) | own 6-digit adcode (`xx90yy`) | `"419001"` 济源 |

Name fields: `name` = full official name (深圳市, 延边朝鲜族自治州), `short` = map label (深圳, 延边; 海南藏族自治州 →
海南州 so it does not read as the province). Province `short` (广东) and `abbr` = 简称 (粤) are in
`core/china.py PROVINCES`.

* 省直辖县级行政区 are **counties** whose prefecture is themselves (`kind: "county_city"`): they are in the
  boundaries and the county lookup, in province totals, but **not** city banners. Their cells belong to no city
  footprint.
* Prefecture-level units without counties in DataV (东莞 441900, 中山 442000, 嘉峪关 620200, 儋州 460400) are their
  own single county (`whole_prefecture: true`), so every Chinese land point outside Taiwan has a county.
* **三沙市 (460300)** is the one prefecture with no land hex (its seat 永兴岛 is ~150 km south of Hainan). It is
  **not** in cities.json; it is a labelled point (三沙市) in the inset and on the main map (its seat is inside the
  frame). Its statistics, if any, stay in the sources and in Hainan's province total.

### 3.2 US concept → China (must)

| US (usaciv) | China (chinaciv) |
|---|---|
| state (50 + DC), FIPS `"06"` | province (34), code `"44"` |
| state postal abbr `"CA"` | 简称 `"粤"` (display only; files reference provinces by **code**) |
| county, GEOID `"06085"` | 区县, adcode `"440305"` |
| CBSA metro/micro (city) | prefecture-level unit (地级市/地区/自治州/盟) + municipalities + SARs + Taiwan cities |
| city position = principal city | city position = seat of government (DataV `center`; Wikidata for Taiwan) |
| metro footprint = cells whose county samples (≥ 4 of 7) are member counties | same rule, with each county sample mapped to its prefecture (`_world.county_prefecture`) |
| ACS population | 七普 2020 census + latest-year 常住人口 (cn_stats_city) |
| BEA GDP (USD) | GDP in **CNY (元)** (cn_stats_city) |
| ACS jobs by industry → Food/Production/Science/Culture | sector value added or employment by industry (§4.5–4.6, §5.3) |
| lower-48 bounds, NLCD, Census outlines | fixed China frame, cn_landcover, cn_boundaries |
| Canada/Mexico fog | neighbours fog (Natural Earth, China POV) |

### 3.3 web/data key names (must; chosen to minimise frontend churn)

Keys keep their **legacy names**, with China values:

| Key | Where | Holds |
|---|---|---|
| `states` | world.json, states.json (file name too) | provinces |
| `fips` | world.json `states[]`, states.json | province code `"44"` |
| `abbr` | same | 简称 `"粤"` |
| `state` | any site row (resources, infrastructure, wonders) | province **code** `"44"` (not the 简称) |
| `states` | cities.json row | `[province code]` (one entry) |
| `counties` / `geoid` | world.json `counties[]` | county adcodes |
| `county` | site rows | county adcode or null |
| `cbsa` | site rows | the cities.json id holding the site's county, or null |
| `city` (URL hash) / `id` | cities.json | prefecture adcode |

New keys are added where China needs them (`short`, `full`, `kind`, `kind_label`, `currency`, …; listed below).
US-only fields are **omitted** (not null): `median_income`, `median_rent`, `home_value`, `unemployment`,
`rent_burden`, `pop_series` (unless filled from yearbooks), `gdp_undisclosed`, `gdp_estimated`; the frontend drops
those rows.

---

## 4. Source modules and interim schemas

Each module lives in `pipeline/sources/<module>.py`, subclasses `Source` with a Chinese `SourceInfo`, downloads
only through `self.download()` (cached, manifest), and returns from `process()` a dict with `"vintage"`, `"_files"`
and the fields below. Interims are `data/interim/<id>.json` (`{"name", "processed", "vintage", "data", "files"}`).
Units are stated per field; **money in 元 (yuan), not 亿元/万元; people in 人, not 万人; mass in tonnes (t)**.
Multiply publisher units out (亿元 × 1e8, 万人 × 1e4, 万吨 × 1e4) and keep ints where the publisher's precision
allows. Every record that combines several publishers carries `src` (a short key that the SourceInfo
description explains, e.g. `"nbs2024"`, `"gd_tjnj2024"`, `"sz_gb2024"`).

### 4.1 `cn_boundaries.py` → `cn_boundaries` (DONE, foundation)

```jsonc
{
  "vintage": "DataV.GeoAtlas areas_v3 (2026-11-26)",
  "crs": "GCJ-02 lon/lat …",
  "country":   {"name": "中华人民共和国", "rings": [[[lon, lat], …], …]},        // 100000.json, 277 rings
  "provinces": [{"code": "44", "adcode": "440000", "name": "广东省", "short": "广东", "abbr": "粤",
                 "kind": "province|autonomous_region|municipality|sar",
                 "seat": [lon, lat], "centroid": [lon, lat] | null, "rings": [...]}],     // 34, sorted by adcode
  "prefectures": [{"adcode": "440300", "name": "深圳市", "short": "深圳", "province": "44",
                   "kind": "city|prefecture|autonomous_prefecture|league|municipality|sar|taiwan_city|county_city",
                   "seat": [lon, lat] | null, "rings": [...],          // [] for taiwan_city (points only)
                   "counties": ["440303", …],                         // [] for taiwan_city
                   "id_assigned": true, "wikidata": "Q1867"}],        // taiwan_city only
  "counties": [{"adcode": "440305", "name": "南山区", "prefecture": "440300", "province": "44",
                "seat": [lon, lat] | null, "rings": [...], "whole_prefecture": true /* only when it is the prefecture */}],
  "dash_line": [{"seq": 1, "rings": [[[lon, lat], …]], "line": [[lon, lat] /* north end */, [lon, lat]]}],  // 10, DataV order
  "islands": [{"id": "diaoyu", "name": "钓鱼岛", "kind": "island|island_group|shoal", "point": [lon, lat] | null, "wikidata": "Q719187"}]
}
```

Counts: 34 provinces; 375 prefecture-level records (293 city, 7 prefecture, 30 autonomous_prefecture, 3 league,
4 municipality, 2 sar, 6 taiwan_city, 30 county_city); 2,874 counties; 10 dashes; 8 islands (钓鱼岛, 赤尾屿, 东沙群岛,
西沙群岛, 中沙群岛, 黄岩岛, 南沙群岛, 曾母暗沙). Rings are thinned (provinces 0.005°, others 0.01°; small islands kept
whole); even-odd rule handles holes (`core.geo.Shape`). `seat` = DataV `center` (the government seat).

### 4.2 `natural_earth.py` → `natural_earth`, `natural_earth_roads` (DONE, foundation)

`natural_earth`: `countries: [{name, name_zh, a3, rings}]` (34 neighbours with land in lon 60–150°E, lat 0–60°N,
China POV, CHN/TWN/HKG/MAC/SCR dropped), `lakes: [{name, name_zh, rank, rings}]` (252), `rivers: [{name, name_zh,
rank, featurecla, line}]` (437 lines; `name_zh` from a fixed table in the module, null where not identified with
confidence). `natural_earth_roads` (fallback for §4.8 only): `roads: [{type, name, expressway, scalerank,
lines}]`, `rail: [{scalerank, category, lines}]`.

### 4.3 `etopo.py` → `etopo` (DONE)

Unchanged schema `{lat: [], lon: [], alt: []}` (metres), window lon 63–145°E × lat 12–57°N, ~9 km.

### 4.4 `cn_landcover.py` → `cn_landcover` (owner A1)

A lon/lat raster of land-cover classes covering at least lon 63–145°E × lat 12–57°N (the frame; Chinese land is
what matters, neighbours may be coarser or missing), plus the mapping of its classes to our groups, so `world`
does not depend on which dataset was used.

```jsonc
{
  "vintage": "MODIS MCD12C1 IGBP 2022, 0.05°",          // whatever was used
  "scheme": "igbp|esa_worldcover|esa_cci|glc_fcs30|…",
  "crs": "EPSG:4326",
  "lon0": 63.025, "lat0": 56.975,        // centre of pixel (col 0, row 0); rows go south
  "dlon": 0.05, "dlat": -0.05,           // pixel size (dlat negative)
  "cols": 1640, "rows_n": 900,
  "legend": {".": 0, "a": 1, …},         // one char per class code (like nlcd), rows stored as strings
  "classes": {"1": "常绿针叶林", …},       // class code → Chinese name
  "groups": {"1": "forest_evergreen", "4": "forest_deciduous", "5": "forest_mixed", "6": "scrub", "10": "grassland",
             "11": "wetland", "12": "cropland", "13": "developed", "15": "ice", "16": "barren", "17": "water", "0": "nodata"},
  "rows": ["....aab…", …]
}
```

`groups` values **must** be one of: `forest_evergreen, forest_deciduous, forest_mixed, scrub, grassland,
pasture, cropland, wetland, developed, barren, ice, water, nodata`. *Suggested* datasets (no key): NASA NEO /
MODIS MCD12C1 IGBP (0.05°/0.1° CSV or GeoTIFF from neo.gsfc.nasa.gov), ESA WorldCover 2021 via a public
WMS/WCS resampled to ≤ 1 km, or ESA CCI LC. Resolution ≥ 0.1° (≥ ~13 samples per hex) is acceptable; finer is
better. Record the exact product and URL in SourceInfo.

### 4.5 `cn_stats_city.py` → `cn_stats_city` (owner A2): population + GDP per city, province, nation

```jsonc
{
  "vintage": "七普 2020；2024 年统计公报/统计年鉴",
  "census_year": 2020,
  "year": 2024,                           // the target "latest" year used for pop and GDP wherever available
  "cities": {                             // keyed by cities.json id (§3.1): all 345 banner ids must be present
    "440300": {
      "name": "深圳市",
      "pop_census": 17560061 | null,       // 七普常住人口 (2020-11-01), 人
      "pop": 17798900 | null,              // 年末常住人口 of `pop_year`, 人
      "pop_year": 2024 | null,
      "gdp": 3.68e12 | null,               // 地区生产总值, current prices, 元
      "gdp_year": 2024 | null,
      "va_primary": … | null, "va_secondary": … | null, "va_tertiary": … | null,   // 三次产业增加值, 元, same year as gdp
      "currency": "CNY",                   // HKD/MOP/TWD only before conversion; stored values are always CNY
      "gdp_local": null, "fx_rate": null,  // SAR / Taiwan only: GDP in local currency and the CNY rate used
      "src": {"pop_census": "…", "pop": "…", "gdp": "…", "va": "…"},
      "note": null                         // e.g. "台湾地区人口取自台湾内政部户政司，GDP 无官方分市数据，置空"
    }
  },
  "provinces": {"44": { /* same fields */ }},   // all 34 codes; 71/81/82 from their own statistics or null
  "nation": { /* same fields */ , "scope": "31 个省、自治区、直辖市（不含港澳台）"}
}
```

* The 30 `county_city` units may be included under `"cities"` too (they are useful for province checks) but are not
  required. 三沙 (460300) may be included; it gets no banner.
* `pop` and `gdp` should share one `year` for every city where the publisher has it (*suggested*: the latest year
  for which ≥ 90% of cities report, probably 2023 or 2024); a city without that year gets `pop`/`gdp` null and its
  older figure is **not** substituted silently (it may be kept with its own `*_year` only if the builder rule says
  so; default: same year or null).
* SAR/Taiwan GDP: convert at the annual average rate published by an official source (国家统计局《中国统计年鉴》
  or the publishing government); record `gdp_local`, `fx_rate`, `src`. If not easy: null + `note`.
* *Suggested* publishers: 国家统计局 (data.stats.gov.cn 分省年度数据, 七普公报 and 分县资料), each province's
  统计年鉴 / 七普公报 (第二号: 地区人口), cities' 统计公报; 香港政府统计处, 澳门统计暨普查局, 台湾"主计总处"/"内政部"
  (names must be written without state titles, e.g. 台湾地区“内政部”数据 → write "台湾地区户政统计").

### 4.6 `cn_city_industry.py` → `cn_city_industry` (owner A3): what the non-Gold yields are made of

```jsonc
{
  "vintage": "…",
  "year": 2023,
  "basis": "employment_urban_units|employment_census|…",  // what `emp` counts (e.g. 城镇非私营单位从业人员年末人数)
  "industries": {"A": "农、林、牧、渔业", "B": "采矿业", "C": "制造业", "D": "电力、热力、燃气及水生产和供应业",
                 "E": "建筑业", "F": "批发和零售业", "G": "交通运输、仓储和邮政业", "H": "住宿和餐饮业",
                 "I": "信息传输、软件和信息技术服务业", "J": "金融业", "K": "房地产业", "L": "租赁和商务服务业",
                 "M": "科学研究和技术服务业", "N": "水利、环境和公共设施管理业", "O": "居民服务、修理和其他服务业",
                 "P": "教育", "Q": "卫生和社会工作", "R": "文化、体育和娱乐业", "S": "公共管理、社会保障和社会组织"},
  "cities": {"440300": {
      "emp": {"A": 1234 | null, …, "S": … | null},   // persons; keys = GB/T 4754 sections A–S; every key present
      "emp_total": 1234567 | null,
      "rd_expenditure": 2.2e11 | null,               // optional: R&D 经费内部支出, 元
      "src": {"emp": "…", "rd_expenditure": "…"}}},
  "provinces": {"44": { /* same */ }},
  "nation": { /* same */ }
}
```

* Yields must use **one basis for all cities** (§5.3). If no per-city employment by industry is obtainable for most
  cities, say so: the builder then falls back to the three-sector value added from cn_stats_city for Food and
  Production and leaves Science/Culture null (never mixed bases).
* *Suggested*: 《中国城市统计年鉴》 tables "按行业分城镇单位从业人员" (often re-published by provincial bureaus);
  provincial 统计年鉴 city tables; 七普 long-form "分行业就业人口" by prefecture (provincial 人口普查年鉴).

### 4.7 `cn_transport_air.py` → `cn_transport_air` (owner A5)

```jsonc
{
  "vintage": "中国民用航空局《2024年全国民用运输机场生产统计公报》",
  "year": 2024,
  "airports": [{                          // every airport in the 公报 (≈ 260), sorted by passengers desc
      "name": "北京/首都", "iata": "PEK" | null, "icao": "ZBAA" | null, "city": "北京",
      "passengers": 67367000 | null,        // 旅客吞吐量, 人次 (arrivals + departures)
      "passengers_prev": … | null,          // same airport, previous year (as printed in the 公报)
      "cargo_t": 1440000.5 | null,          // 货邮吞吐量, tonnes
      "movements": 400000 | null,           // 起降架次
      "rank": 1,                            // by passengers, 1-based, as computed here
      "lon": 116.58, "lat": 40.08, "coord_src": "ourairports|wikidata|…",
      "province": "11" | null}],
  "extra": [{ /* HKG, MFM, TPE/KHH … only when their own official annual figures were fetched; same fields */ }]
}
```

*Suggested* coordinates: OurAirports `airports.csv` (public domain) matched by IATA/ICAO, else Wikidata.

### 4.8 `cn_transport_ports.py` → `cn_transport_ports` (owner A5)

```jsonc
{
  "vintage": "交通运输部《2024年全国港口货物、集装箱吞吐量》",
  "year": 2024,
  "ports": [{"name": "宁波舟山港", "kind": "coastal|inland",
             "cargo_t": 1.38e9 | null,      // 货物吞吐量, tonnes
             "foreign_t": … | null,         // 外贸货物吞吐量, tonnes
             "teu": 39300000 | null,        // 集装箱吞吐量, TEU
             "rank": 1,                     // by cargo_t within all ports
             "lon": …, "lat": …, "coord_src": "…", "province": "33"}],
  "extra": [ /* 香港、高雄 etc. only from their own official figures */ ]
}
```

### 4.9 `cn_rail_roads.py` → `cn_rail_roads` (owner A5)

Polylines in lon/lat, vertex spacing thinned to ≥ ~1 km, clipped to China + 1° buffer.

```jsonc
{
  "vintage": "OpenStreetMap contributors, Overpass extract 2026-11",
  "hsr":        [{"name": "京沪高速铁路" | null, "ref": … | null, "line": [[lon, lat], …]}],   // design speed ≥ 250 km/h (highspeed=yes)
  "rail":       [{"name": … | null, "usage": "main|branch|null", "line": [...]}],            // conventional mainline rail
  "expressway": [{"ref": "G4" | null, "name": "京港澳高速" | null, "line": [...]}]            // 国家/省级高速 (motorway)
}
```

*Suggested*: OSM via Overpass, tiled by province (`railway=rail` + `highspeed=yes` / `usage=main`; `highway=motorway`),
ODbL credit in SourceInfo; `natural_earth_roads` only as a documented fallback.

### 4.10 `cn_energy.py` → `cn_energy` (owner A6)

```jsonc
{
  "vintage": {"plants": "WRI Global Power Plant Database v1.3.0 (2021)", "mines": "…", "fields": "…"},
  "plants": [{"name": "三峡水电站" | null, "name_en": "Three Gorges Dam" | null,
              "fuel": "coal|gas|oil|nuclear|hydro|pumped_storage|wind|solar|biomass|geothermal|battery|other",
              "capacity_mw": 22500 | null, "year": 2012 | null, "owner": … | null, "status": "operating",
              "lon": …, "lat": …, "src": "wri|gem|…"}],
  "mines": [{"name": …, "commodity": "coal|iron|copper|gold|rare_earths|lithium|tungsten|…",
             "capacity": 1.2e7 | null, "capacity_unit": "t/yr|…" | null, "status": "operating|…",
             "lon": …, "lat": …, "src": "…"}],
  "fields": [{"name": "大庆油田", "kind": "oil|gas|oil_gas", "basin": "松辽盆地" | null,
              "production": 3.0e7 | null, "production_unit": "t/yr|m3/yr" | null, "year": … | null,
              "lon": …, "lat": …, "rings": [...] | null, "src": "…"}]
}
```

*Suggested*: WRI GPPD (CC BY 4.0, GitHub CSV, no key) for plants; Global Energy Monitor trackers where a direct
download works (coal plants/mines, wind, solar, GOGET oil & gas); USGS "Mineral facilities of Asia and the Pacific"
(public domain) for mines; Wikidata for field locations; production figures only from a cited publisher.

### 4.11 `cn_wonders.py` → `cn_wonders` (owner A7)

```jsonc
{
  "vintage": {"unesco": "UNESCO World Heritage List (2025)", "parks": "国家林业和草原局 …", "scenic_5a": "文化和旅游部 …",
              "universities": "教育部 …"},
  "unesco": [{"whc_id": 1004, "name": "长城" , "name_en": "The Great Wall", "category": "cultural|natural|mixed",
              "inscribed": 1987, "criteria": "(i)(ii)(iii)(iv)(vi)", "area_ha": … | null, "transnational": false,
              "lon": …, "lat": …, "province": "11" | null}],         // all sites listed under China (incl. 台湾? none), plus transnational with a part in China
  "national_parks": [{"name": "三江源国家公园", "established": "2021-10-12", "area_km2": 190700 | null,
                      "lon": …, "lat": …, "rings": [...] | null, "province": "63"}],
  "scenic_5a": [{"name": "…", "designated": 2007 | null, "batch": 1 | null, "lon": …, "lat": …, "province": "…"}],
  "universities": [{"name": "清华大学", "double_first_class": true, "rd_cny": … | null, "rd_year": … | null,
                    "lon": …, "lat": …, "province": "11"}]
}
```

---

## 5. Builders and web/data contracts

Build order (dependency-resolved automatically): `world` → `cities` → `infrastructure`, `resources`, `wonders`,
(`lenses`) → `states` → `catalog`. `growth.py` and `yields.py` are helpers.

| Builder | REQUIRES (must) | PRODUCES | Owner |
|---|---|---|---|
| `world` | `cn_boundaries, natural_earth, etopo, cn_landcover` | `_world`, `world.json` | A1 |
| `lenses` (optional, new) | `_world, cities.json` (+ `cn_stats_city`) | `lenses.json` | A1 |
| `cities` | `_world, cn_boundaries, cn_stats_city, cn_city_industry` | `cities.json`, `nation.json` | A4 |
| `states` | `_world, cn_boundaries, cn_stats_city, cn_city_industry, resources.json` | `states.json` | A4 |
| `infrastructure` | `_world, cn_boundaries, cn_rail_roads, cn_transport_air, cn_transport_ports, cities.json` | `infrastructure.json` | A5 |
| `resources` | `_world, cn_boundaries, cn_energy, cities.json` | `resources.json` | A6 |
| `wonders` | `_world, cn_boundaries, cn_wonders, cities.json` | `wonders.json` | A7 |
| `catalog` | – | `sources.json` | A1 (keep as is) |

Deleted builders stay deleted (military, trade, disasters, citizens, districts, counties, US lenses). `growth.py`:
A4 rewrites it as the simple 2020 → `year` ratio below or deletes it.

### 5.1 `world` → `_world` interim and `world.json` (A1)

**Ownership rule** (same 7-sample majority as before, provinces tested **first**): each sample point is lake
(Natural Earth lake) → province (cn_boundaries, the province wins over any NE country) → foreign (NE country) →
sea. Tidal-bay and sliver rules unchanged. Then two forced rules:

1. **SAR seats**: the hex containing the seat of 香港 (810000) and the hex containing the seat of 澳门 (820000) are
   owned by that SAR (`owner` = its province index, land). If both fell in one hex (they do not today), Macau takes
   the nearest free coastal neighbour.
2. The SAR seat hexes get county = the SAR district at the seat.

The `_world` interim (**must**, read by `locate.py`, cities, states, site builders):

```jsonc
{
  "grid": {"radius": 25, "cols": 128, "rows": 120},
  "projection": {"min_x": -2619.0, "max_y": 5939.0, "margin": 200.0, "lat1": 25.0, "lat2": 47.0, "lat0": 0.0, "lon0": 105.0},
  "owner": [int],               // -1 water, -2 foreign land, >= 0 index into `states`
  "states": ["11", "12", …],    // province codes, sorted by code (34)
  "counties": ["110101", …],    // county adcodes, sorted (all 2,874 cn_boundaries counties)
  "county_prefecture": ["110000", …],   // parallel to `counties`: prefecture adcode (county_city: itself)
  "county": [int],              // per cell, index into `counties`, -1 if none (Taiwan, water, abroad)
  "county_samples": [[int x7]], // per Chinese land cell; [] elsewhere; sample 0 = centre (as before)
  "water": [int], "cover": [int], "pct": {…},
  "inset": { /* same object as world.json inset */ }
}
```

`world.json` (**must**; version 3):

```jsonc
{
  "version": 3,
  "grid": {…}, "width": 5471.0, "height": 4455.0, "margin": 200,
  "projection": { /* the full _world projection dict */ },
  "owner": [], "county": [], "elev": [], "elev_max": [], "water": [], "relief": [], "cover": [], "pct": {},
  "tables": {"water": […], "relief": […], "cover": […], "notes": {…}},   // names/rules in Chinese; cover ids 0–11 unchanged
  "states": [{"fips": "44", "abbr": "粤", "name": "广东", "full": "广东省", "kind": "province", "kind_label": "省"}],
  "counties": [{"geoid": "440305", "name": "南山区"}],
  "rivers": [{"name": "长江", "name_en": "Yangtze", "rank": 1, "pts": [x0, y0, …]}],   // name = name_zh (null if unknown)
  "lakes": [{"name": "青海湖", "x": …, "y": …}],          // optional: label anchors of named lakes ≥ 1 hex
  "dash_line": [[x0, y0, x1, y1, …]],                      // main-map km: dashes (or their pieces) inside the frame and
                                                           // outside the inset box; from cn_boundaries dash_line rings/lines
  "islands": [{"id": "diaoyu", "name": "钓鱼岛", "kind": "island", "x": …, "y": …}],   // main-map labels inside the frame
  "inset": {
    "title": "南海诸岛", "box": [4780.7, 3655.7, 5446.0, 4430.0], "region": [106.5, 2.8, 123.5, 24.8], "scale": 0.3,
    "land":   [{"kind": "china", "code": "46", "pts": [x0, y0, …]}, {"kind": "foreign", "name": "越南", "pts": […]}],  // rings clipped to the region, inset km
    "dashes": [[x0, y0, …]],                               // all 10 (polygon rings or 2-point lines), inset km
    "islands": [{"id": "xisha", "name": "西沙群岛", "kind": "island_group", "x": …, "y": …}],
    "labels": [{"name": "三沙市", "x": …, "y": …, "kind": "city"}]
  },
  "notice": {
    "text": "本地图仅供个人学习，未申请审图号。",
    "boundary": "国界、省界及南海断续线依据阿里云 DataV.GeoAtlas 行政区划数据（源自高德开放平台，依据标准地图）绘制；邻国取自 Natural Earth（中国视角版）。",
    "sources": ["cn_boundaries", "natural_earth"]
  }
}
```

* Land cover: map each sample's class through `cn_landcover.groups`; cover id rules as before (urban threshold
  may be lowered for coarse rasters: state it in the table rule). `pct` keys unchanged.
* Rivers: Natural Earth rank ≤ 8 inside the frame, `name` = `name_zh`.
* Relief classes unchanged; snow uses `elev_max` (the Himalaya and Kunlun need it).

### 5.2 `cities` → `cities.json`, `nation.json` (A4)

One row per banner city (345 ids: `cn_boundaries.prefectures` with kind in `core.china.BANNER_KINDS`, minus 三沙),
sorted by population desc (nulls last, then by id):

```jsonc
{
  "id": "440300", "name": "深圳", "full": "深圳市", "kind": "city", "kind_label": "地级市",
  "states": ["44"], "metro": true, "capital": false,          // capital = "110000" only; metro true for all
  "x": …, "y": …, "cell": 12345,                              // seat; banner cell (Chinese land, unique where possible)
  "population": 17798900 | null, "population_year": 2024 | null, "pop_census_2020": 17560061 | null,
  "employed": … | null,                                        // emp_total of cn_city_industry, or null
  "yields": {"food": …, "production": …, "gold": …, "science": …, "culture": …},   // any may be null (§5.3)
  "jobs": {"A": …, …} | null,                                  // cn_city_industry emp by section, or null
  "gdp": 3.68e12 | null, "gdp_year": 2024 | null, "currency": "CNY",
  "gdp_by_industry": {"primary": …, "secondary": …, "tertiary": …} | null,
  "growth": 0.0136 | null, "growth_years": [2020, 2024] | null,  // population / pop_census_2020 − 1, see below
  "counties": ["440303", …], "footprint": [cells], "ranks": {…}, "peers": [ids],
  "note": null                                                  // e.g. Taiwan / SAR data notes, shown in the panel
}
```

* Banner cells: SAR cities first (their forced seat hexes), then the rest by population (null last) with the
  existing snap / pull-back / nudge rules; "own state" = the city's province. 深圳 and 珠海 therefore move to a free
  Guangdong neighbour of their seat hex.
* Footprint: Chinese land cells where ≥ 4 of 7 `county_samples` map (via `county_prefecture`) to the city id; plus
  its own cell. Taiwan cities: own cell only. Municipalities and SARs: their whole province.
* Growth: `population(year) / pop_census_2020 − 1` when both exist and `population_year` equals the file-wide
  `year`; otherwise null. Same `growth_years` for every row (or null everywhere).
* Ranks/peers: as before (ranks over the 345 cities; null values unranked).
* `nation.json`: `{"vintage", "year", "census_year": 2020, "gdp_vintage", "currency": "CNY", "nation": {same shape},
  "scope": "全国数据为 31 个省、自治区、直辖市合计，不含香港、澳门和台湾", "yields": [YIELDS], "industries": {"A": "农、林、牧、渔业", …}}`.

### 5.3 Yields (A3 owns `build/yields.py`, A4 consumes)

Gold = GDP (元, current prices). The other four must each use **one basis for all cities**, chosen by this priority
(first one with data for ≥ 90% of banner cities wins; otherwise the yield is null everywhere and the description
says why):

| Yield | Priority 1 (employment, persons) | Priority 2 (value added, 元) |
|---|---|---|
| Food 🌾 | section A 农、林、牧、渔业 | 第一产业增加值 |
| Production ⚙️ | B 采矿 + C 制造 + D 电力热力燃气水 + E 建筑 | 第二产业增加值 |
| Science 🔬 | I 信息传输软件信息技术 + M 科学研究和技术服务 | R&D 经费内部支出 (cn_city_industry `rd_expenditure`) |
| Culture 🎭 | R 文化、体育和娱乐业 | – (null) |

Each YIELDS entry: `{key, icon, emoji, label (粮食/生产力/金币/科研/文化), color, unit: "persons|cny", industries:
[section letters] or [], basis, description (Chinese, names the exact table and year)}`. Banners format CNY with
亿/万亿 (frontend).

### 5.4 `states` → `states.json` (A4)

Same order as world.json `states` (34). Each: `fips, abbr, name (short), full, kind, kind_label`, the city economic
shape (population, population_year, pop_census_2020, employed, yields, jobs, gdp, gdp_year, currency,
gdp_by_industry, growth, growth_years), `ranks: {yield: 1..n}` (over provinces with a value; 71/81/82 included
when they have one), `label` (anchor as before; null for 澳门 with no hex), `land` (as before), `energy` (MW from
resources.json items with `state` = code, as before), `note` (Taiwan/SAR data notes).

### 5.5 `infrastructure` → `infrastructure.json` (A5)

As DATA_CONTRACTS.md, with China values:

* `road_classes: [{"id": "expressway", "name": "高速公路"}]`, `rail_tiers: [{"id": "hsr", "name": "高速铁路"}, {"id":
  "rail", "name": "普速铁路"}]`; links `[a, b, class|tier index, name|null]` between neighbouring cells, at least one
  end Chinese land (`owner >= 0`), neither end foreign; best class wins per pair (HSR over conventional).
* `hubs: {"L": "年旅客吞吐量1000万人次以上", "M": "200万–1000万人次", "S": "200万人次以下"}` (CAAC's own size groups).
* `airports`: `{id (IATA, else ICAO), name, city, hub, passengers, passengers_prev, cargo_t, movements, year, rank,
  x, y, cell, county, cbsa, state}` — `passengers` **replaces** `enplanements` (it counts both directions). Rule:
  *suggested* every airport with ≥ 1 million passengers (≈ 100) or the top 60, stated in `rules.airports`.
* `ports`: `{id (pinyin slug), name, type: "coastal|inland", rank, cargo_t, foreign_t, teu, year, x, y, cell, county,
  cbsa, state}` — `cargo_t` (tonnes) **replaces** `tons` (short tons). *Suggested* rule: the top 30 by cargo.
* Keep the file under ~300 kB.

### 5.6 `resources` → `resources.json` (A6)

Same shape. Type ids keep the existing icon ids where the meaning matches (`nuclear, hydro, coal_plant, gas_plant,
wind, solar, pumped_storage, battery, geothermal, coal_mine, iron, copper, gold, rare_earths, lithium, oil, gas`);
new types allowed (`tungsten`, …; the frontend falls back to the emoji). Names/descriptions in Chinese; thresholds
in each type's `description`. `value` units: MW (plants), the mine's capacity unit (or null), oil/gas field
production (or null; field values are not additive → `summary` value null). Items carry `state` (code), `cbsa`
(city id via `Locator.city_of`), `county`. *Suggested* thresholds tuned so that ≈ 300–600 items total.

### 5.7 `wonders` → `wonders.json` (A7)

Same shape. Rules (ids **must**): `unesco_whs`, `national_park`, `scenic_5a`, `university`; each with Chinese
`text`, `year`, `kinds`. Kinds: UNESCO natural/mixed → `natural`, cultural → `monument`; 国家公园 → `natural`
(with `cells` = covered hexes when a boundary exists); 5A → `landmark`; universities → `university`. *Suggested*
selection: every UNESCO site in China (each transnational site once, at its Chinese part); every 国家公园; 5A areas of
the first batch (2007) not already a UNESCO wonder; the 20 双一流 universities with the largest R&D (教育部
《高等学校科技统计资料汇编》), else all 一流大学建设高校 with the rule text saying so. Metrics per rule:
`unesco_whs {whc_id, category, inscribed, criteria, area_ha}`, `national_park {established, area_km2}`,
`scenic_5a {designated, batch}`, `university {rd_cny, rd_year, double_first_class}`. ≤ ~120 wonders, < 300 kB.

### 5.8 `lenses` → `lenses.json` (A1, optional)

Hex-only lenses: elevation, land-cover shares (forest, crop, grass, shrub, developed …), and a population-density
lens at **prefecture** level (city population ÷ footprint area spread over its footprint; allowed — it is not a
county lens). Group ids `people`, `land`. Names/units/notes in Chinese.

### 5.9 `catalog` → `sources.json`

Unchanged code. Because every SourceInfo is now Chinese, the Almanac is Chinese.

### 5.10 `pipeline/check.py` (shared; see §8)

The core checker must be adapted: drop counties.json / lenses-as-required / growth-vs-ACS / CONUS logic; validate
world.json v3 (`projection`, `inset`, `notice`, `states` keys), cities (345 rows, ids 6 digits, `states` ⊂ province
codes), states (34, fips order), nation, sources. Builders add their own `check(web_dir)` for their files (missing
file = NOTE).

---

## 6. Frontend (A8 owns all of `web/`)

Required changes, so the data above renders:

1. `render/art/terrain.js` geography: read Albers parameters from `world.projection` (lat1, lat2, lat0, lon0)
   instead of the hard-coded US constants and `SHIPPED_PROJ`; replace US regions (Colorado Plateau, Great Basin,
   centre-pivot west of 98°W) with Chinese ones (*suggested*: 黄土高原 loess terraces, 塔里木/准噶尔 deserts,
   喀斯特 karst towers in 广西/贵州, 青藏高原 high plateau, 四川盆地 red basin, terraced paddies south of 30°N).
2. Inset: a static layer (z ≈ 92, above labels) drawing `world.inset` (opaque parchment box with frame, title
   南海诸岛, land, dashes, island labels); main-map `dash_line` and `islands` as a layer (z ≈ 52).
3. Province labels from `states[].name` (short, Chinese); state colouring unchanged; `abbr` is 1 character.
4. City banners: Chinese names, CNY formatting (亿, 万亿), yield labels; `cities[].states` are codes — match
   provinces by `fips`; city URL `#city=440300`, province `#state=44`.
5. Remove/hide out-of-scope UI: military layer & advisor, trade routes, citizens and districts in City View, county
   lenses, disasters, faith; keep City View panels that use cities.json/infrastructure/resources/wonders.
6. Infrastructure layer: `passengers` instead of `enplanements`, `cargo_t`/`teu` instead of tons; HSR vs rail styles.
7. Fully Simplified-Chinese UI (menus, tooltips, help, Almanac, legends, number formats `toLocaleString("zh-CN")`).
8. Almanac + help show `world.notice` (text + boundary source) and list every source; share images print the notice.
9. `SITE_URL`, page title, OG tags, `package.py` zip names → chinaciv.

---

## 7. Compliance checklist (every agent, before reporting done)

- [ ] No China boundary from Natural Earth/GADM/OSM; neighbours only from the NE China-POV file.
- [ ] Taiwan = 台湾省 everywhere (never a country; no "国家/政府/总统/直辖市" for Taiwan entities in labels).
- [ ] 钓鱼岛, 赤尾屿 labelled; 南海诸岛 inset with all 10 dashes; 藏南 and 阿克赛钦 inside the territory.
- [ ] Hong Kong/Macau as 特别行政区; their data from their own statistics or null, with a note.
- [ ] Notice "本地图仅供个人学习，未申请审图号。" + boundary source in Almanac/help.
- [ ] No military content; no null written as 0; all UI text Chinese.

---

## 8. Ownership (who edits what) and coordination

| Agent | Owns (may edit) |
|---|---|
| Foundation (done) | `pipeline/core/*`, `sources/cn_boundaries.py`, `sources/natural_earth.py`, `sources/etopo.py`, `build/locate.py`, this file |
| A1 world & land | `sources/cn_landcover.py`, `build/world.py`, `build/lenses.py` (new), `build/catalog.py`, `tests/test_gdp_fill.py`, `tests/test_hexgrid.py`; world/lenses sections of `check.py` and DATA_CONTRACTS.md |
| A2 city statistics | `sources/cn_stats_city.py` |
| A3 industry & yields | `sources/cn_city_industry.py`, `build/yields.py` |
| A4 cities & provinces | `build/cities.py`, `build/states.py`, `build/growth.py`; cities/states/nation sections of `check.py` and DATA_CONTRACTS.md; `tests/test_check.py`, `tests/test_builders.py`, `tests/test_builder_checks.py` |
| A5 transport | `sources/cn_transport_air.py`, `sources/cn_transport_ports.py`, `sources/cn_rail_roads.py`, `build/infrastructure.py`, `tests/test_infrastructure.py`; infrastructure section of DATA_CONTRACTS.md |
| A6 energy & minerals | `sources/cn_energy.py`, `build/resources.py`; resources section of DATA_CONTRACTS.md |
| A7 wonders | `sources/cn_wonders.py`, `build/wonders.py`, `tests/test_wonders.py`; wonders section of DATA_CONTRACTS.md |
| A8 frontend | everything under `web/`, `README.md`, `pipeline/package.py`, `pyproject.toml` name |

Coordination:

* `check.py` is shared by A1 and A4 (sections as above); keep edits to your sections. `core/*` changes go through
  the foundation owner: describe the need in your report.
* A1 should land the ownership/county part of `world` (+ `_world`) first; A5–A7 build against `locate.Locator`
  (`Locator(world).place(lon, lat, rings)`, `.county_at(x, y, cell)`, `.city_of(county)`, `.province_of(county,
  cell, filed)`, `.covered_cells(shapes)`, `.settle(x, y, cell)`) and need only `_world` + `cn_boundaries` (+
  cities.json for `cbsa`; until it exists `city_of` returns null).
* Source modules are independent: any agent can fetch/process its own source without the others.
* Every agent adds a line to the Decisions log below for each *suggested* choice it made.

## Decisions log

* Foundation: projection lat0 = 0 (common "China Albers" definition; lat0 only shifts the origin). Fixed frame,
  right margin 450 km for the inset. Taiwan cities: six largest, project-assigned ids 710100–710600, Wikidata
  coordinates. 三沙 has no banner (inset label). Province reference by code in all files (`state` key). DataV GCJ-02
  kept unconverted. Natural Earth 1:10m China-POV countries for neighbours; SCR (黄岩岛) dropped from it.
* A5 (air/ports sources): year = newest published (2025 for both: CAAC 公报 2026-02, MOT annual table 2026-01).
  Airports: CAAC "城市/机场" names → ICAO by Wikidata Chinese labels (+2 overrides), coordinates OurAirports by
  ICAO (Wikidata fallback); extra fields `icao`, `wikidata`, `prefecture`, `county` by point-in-polygon on the
  cn_boundaries interim. Ports: since 2024 MOT publishes only the 65 主要港口 (PDF, read with a stdlib PDF
  reader); 上海 coastal/inland and 南通 沿海/沿江 are separate records; ids pinyin slugs in the interim; position =
  DataV seat of the main port area's county (manual table) else prefecture seat. Blank/"…" cells → null. HK/Macau/
  Taiwan `extra` empty (own figures not fetchable as files), noted in `extra_note`.
* A3 cn_city_industry: 《中国城市统计年鉴》 has no free online edition and only 广西 (表4-10) and 山东 (表4-4) yearbooks
  2025 print city × industry employment (城镇非私营单位就业人员, 2024) → 30/344 cities (9%), below 90%: yields fall back
  to three-sector VA. R&D 经费内部支出 2024: provinces/nation/municipalities from 《2024年全国科技经费投入统计公报》,
  cities from 广西/福建/山东 yearbooks (43/344). Extra per-record fields `emp_year`, `rd_year`, `note`; top-level
  `coverage`, `sources`, `basis_label`. .xls read by a stdlib BIFF8 reader inside the module.
* A2 (cn_stats_city): target `year` = 2023 (latest year with uniform, parsable official coverage: each city's/
  province's 2023 统计公报 text via tjcn.org; data.stats.gov.cn answers 403, the NBS yearbook tables are images);
  cities without a readable 2023 bulletin stay null (no older year). Census: provinces NBS 七普公报第三号, 港澳台 第二号,
  地级市 zh-Wikipedia list transcribing 《中国人口普查分县资料—2020》, 州/地区/盟 zh-Wikipedia provincial lists.
  Extra optional fields `urban_pct`, `income_pc`. HK GDP 统计处 table 310-31001 × HKMA 2023 mean HKD/CNY; Macau GDP
  (DSEC via Wikipedia) × peg 1.03; Taiwan GDP/cities null with note. Coverage (338 PRC banner units incl. HK/MO):
  census 338, 2023 pop 211, GDP 287, three-sector VA 267.
* A5 (cn_rail_roads): OSM via Overpass in regular 3°×3° tiles (175, masked by the DataV national outline + 1°)
  instead of province bboxes; one `convert`-trimmed, gzip query per tile for highspeed=yes / usage=main rail and
  highway=motorway (no links). `hsr` = highspeed=yes lines whose per-name maximum maxspeed is ≥ 250 km/h or unknown;
  200-249 km/h highspeed lines go to `rail` with `highspeed: true`. Extra fields `speed` (hsr, rail), `highspeed`
  (rail), `stats`, `tolerance_km`, `osm_base`. Parallel duplicates (double track, dual carriageways) removed within a
  class (≈1-2 km), Douglas-Peucker 2 km in map km. Extract 2026-11-26: hsr 43,163 km in China (225 lines), rail
  (main) 97,306 km, expressway 182,277 km; interim 0.9 MB. Per-ref lengths are approximate (concurrent sections are
  kept once, under whichever ref was processed first).
* A1/A4 (world + states builders): grid 128 × 120, 5,843 Chinese land hexes (新疆 1002, 西藏 735, 内蒙古 700 … 台湾 23,
  海南 23, 上海 4, 香港 1, 澳门 1 forced). Urban threshold 20% of land samples (MODIS urban is sparse; 130 urban hexes).
  Cover is computed for foreign land too (fogged in the UI); `pct` stays Chinese-only. Rivers grouped by Chinese name
  (`name` = name_zh or null, `name_en` = the Natural Earth names joined). Dashes (main `dash_line`, inset `dashes`) are
  centre lines derived from the DataV dash rings (split at the published line ends, both sides averaged), so curved
  dashes keep their shape; draw them as ~2 km strokes. Extra world.json keys: `lakes` (label anchors), `labels` (三沙市
  on the main map), `inset.pad`, inset label `id`. Inset land = every province/neighbour overlapping the region, clipped
  (Sutherland–Hodgman) and thinned to 1.2 map km; tiny Chinese island rings are kept. states.json yields use the basis
  nation.json publishes, else the §5.3 rule over banner cities (today: only Gold, the 2023 VA covers 267/344 < 90%);
  `population`/`gdp` only of the stats year (2023), `jobs` null unless employment exists (广西, 山东), `growth` null for
  provinces without a 2023 population (辽宁, 吉林, 广东, 青海, 台湾, 澳门). Label size assumes ~1 em per Chinese character.
  World/states builder checks skip territory assertions on non-production grids (unit-test fixtures).
* A5 (infrastructure builder): rail tier 0 高速铁路 includes the 200–249 km/h `highspeed` lines (official CN high-speed statistics start at 200 km/h); airports rule = every CAAC airport with ≥ 1,000,000 passengers (2025: 109), hub L/M/S by CAAC groups; ports = top 30 MOT 主要港口 by cargo_t; infrastructure.json version 2 (rail links also carry a name, extra keys `notes`, `units`, `port_types`, port `label`).
* A6 (resources builder): GEM GIPT (2026-09) replaces WRI for plants; no battery type (GEM does not track it; absent,
  not 0); biomass / oil-fired / by-product-gas plants not shown. Thresholds: every nuclear and geothermal plant, hydro
  ≥ 1,000 MW, pumped storage ≥ 1,200 MW, coal ≥ 3,000 MW, gas ≥ 1,500 MW, wind/solar summed per hex ≥ 1,500 MW, coal
  mines ≥ 10 Mt/yr and iron ≥ 5 Mt/yr (grouped per hex), every curated metal mine (value null), oil/gas fields ≥ 5
  million boe/yr (grouped per hex, value boe/yr of GEM's latest year, summary null; oil_gas → oil if oil ≥ half the
  boe; offshore fields snap ≤ 8 hexes, never onto a Taiwan/SAR hex). 523 items, 213 kB. Extra keys: top-level
  `vintage`, `rule` (published selection text), `sources`; item `name_en`, `county`. Gas-plant and a few hydro /
  pumped-storage / geothermal Chinese names are this project's translations (GAS_ZH, EXTRA_ZH).
* A4 (cities builder): cities.json has 344 rows (the plan's 345 counted 三沙). Extra keys `province_capital` (省会/首府,
  台湾省 台北) and `capital_label` (首都/省会/首府/null); `capital` stays 北京 only. Sort: population desc, nulls last, then
  2020 census desc, then id; banner placement order SARs first then population-else-census. Peers by 2020 census
  (one same-year count for all); Taiwan cities (no census) take the other Taiwan cities. Yields: build/yields.py
  (`choose_bases`, `yield_defs`) applies §5.3 and states.py imports it; with the 2023 interims only Gold qualifies
  (VA 267/344 = 78% < 90%), so 粮食/生产力/科研/文化 are null everywhere and nation.json `yields[].basis` is null with a
  Chinese reason. growth.py = population(2023) / 七普 − 1; nulled for 保定 and 咸阳, whose 2023 bulletins exclude
  雄安新区 / 西咸新区 areas (scope change, noted in `note`). nation.json adds `census_year`, `industry_vintage`,
  `currency`, `scope`, `rule`; yields add `basis`, `coverage`.
* Integration (2026-11-26): **BASIS_SHARE lowered from 90% to 75%** (build/yields.py), so 粮食/生产力 use the 2023
  三次产业增加值 (267/344 cities, 78%; the rest null, bases never mixed); 科研/文化 stay null. `natural_earth_roads`
  (unused fallback) removed so `all --offline` needs no extra downloads; cn_stats_city treats uncached tjcn pages as
  absent under --offline. Phase 1 publishes no lenses.json: the browser no longer requests it and hides the lens dock and
  the 透镜 Almanac page. usaciv's `advisors` extension (English, US data) and the dev trailer were removed (phase-2 port).
  Frontend: banners/city tiles size by population ?? 七普 2020 (census shown dimmed), Taiwan's cities get a drawing weight
  of 1.5 million (never displayed). Wonders: the university rule falls back to the 41 首轮一流大学建设高校 (no
  per-university R&D); 28 first-batch 5A areas inside a World Heritage site are folded into it (wonders.py
  FIVE_A_IN_WHS); sources.json lists only sources the map uses (`used_by`).
