"""Wonder candidates for China: UNESCO World Heritage, 国家公园, 国家5A级旅游景区, “双一流”建设高校.

Four official lists, each placed with coordinates (docs/CN_PLAN.md §4.11). All sub-lists carry `province`
(2-digit code) found by point-in-polygon against the cn_boundaries interim (which must be processed first);
universities and 5A areas also carry `city` (the prefecture adcode holding the point, or null).

1. UNESCO World Heritage List — UNESCO's own open-data portal (data.unesco.org, dataset `whc001`, the same
   records as whc.unesco.org/en/list, which itself sits behind a Cloudflare challenge and cannot be scripted).
   Every property whose `iso_codes` contains CN: official Chinese name (`name_zh`), English name, category,
   year of inscription, criteria, area (ha), transboundary flag, coordinates. Taiwan has none (not a State
   Party); 澳门历史城区 is listed under China. Six records have no `name_zh` (鼓浪屿, 可可西里, 大运河, 丝绸之路,
   红河哈尼梯田, 新疆天山): their Chinese name is the Wikidata label of the item whose P757 equals the WHC id
   (`name_src` "wikidata"; for 新疆天山, whose item has no Chinese label, its identical all-ideograph Japanese
   label). Coordinates: UNESCO's site point (`geo_point_2d`); for a serial site with several
   components in China (or whose point lies abroad, e.g. 丝绸之路), the Chinese component nearest to that point
   (so a site's marker always sits on a real component, never on an empty centroid). `provinces` = every
   province holding a component.

2. 国家公园 — the officially established national parks. As of 2026-11 these are still only the first batch of
   five, established 2021-10-12 (三江源, 大熊猫, 东北虎豹, 海南热带雨林, 武夷山; 黄河口, 钱江源—百山祖, 卡拉麦里 …
   are 创建区, not yet established). Names and 保护面积 are parsed from the official announcement as carried by
   新华网 (光明日报, 2021-10-13); `provinces` = provinces named in the park's sentence. Coordinates (`geo`):
   Wikidata P625 of the item with that Chinese label → zh.wikipedia article coordinates → OpenStreetMap
   Nominatim (a feature whose name starts with the park's name: 大熊猫 = the park's boundary relation, 东北虎豹
   = a marker in 珲春 inside the park) → the province centroid for a single-province park (海南热带雨林: the
   announcement says “位于海南岛中部”). `rings` is null (no open official boundary file).

3. 国家5A级旅游景区 — the 文化和旅游部 “旅游资源” directory (www.mct.gov.cn/tourism, directory 4 “5A级旅游景区”,
   a public JSON API answering POST requests): name, province (one or two), year. No coordinates are published,
   so each area is geocoded, method recorded in `geo`:
     "wikidata"         — matched to a Wikidata item that is an instance of “AAAAA-rated tourist attraction”
                          (Q6838244) in the same province, by normalised Chinese name (place prefixes and generic
                          suffixes such as 景区/风景名胜区/旅游区 removed; one name must contain the other, a
                          2-character label only as the start of the name);
     "unesco"           — else matched the same way to a UNESCO World Heritage site in that province (point above);
     "wikidata_search"  — else the first Wikidata search hit (search term = the normalised name up to the first
                          separator) that is not an article, lies in the area's province (and in the prefecture
                          the name mentions, if any) and whose label agrees;
     "county_seat"      — else the seat (DataV) of a county/district whose name appears in the area's name;
     "prefecture_seat"  — else the seat of the prefecture whose name appears in the area's name;
     null               — else lon/lat null.
   `designated` = the first year MCT lists ("2007/2018年" → 2007, the year text is kept in `year_text`);
   `batch` = 1 for the first batch (2007), otherwise null (later designations are not numbered batches).

4. “双一流”建设高校 — 教育部 财政部 国家发展改革委《第二轮“双一流”建设高校及建设学科名单》(教研函〔2022〕1号, PDF
   attachment, parsed with a minimal stdlib PDF text extractor) plus the first-round list (2017, docx) for the
   一流大学建设高校 A/B classes (`world_class_2017`: "A" | "B" | null). Military universities (国防科技大学,
   海军军医大学, 空军军医大学) are dropped (no military content): 144 of the 147. Names are as published in 2022
   (上海体育学院 has since been renamed 上海体育大学). Coordinates (`geo`): Wikidata P625 of the Chinese-labelled
   higher-education institution in China (Q38723 subclass; several campus points → the one in the prefecture
   named in the university name, else the first in sorted order) → Wikidata search API → OSM Nominatim
   (amenity=university with the same name, a campus of it, or the renamed school).
   R&D (`rd_cny`, `rd_year`): 教育部《高等学校科技统计资料汇编》 is a printed book with no machine-readable
   per-university table online, so both are null for every university (unknown, not zero).

Coordinates: UNESCO and Wikidata are WGS-84, DataV seats GCJ-02; never converted (≤ 0.6 km).
`vintage` is a single string (the Almanac prints it); the per-list vintages are in `vintages` (dict).
"""

from __future__ import annotations

import difflib
import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib

from ..core import fetch
from ..core.geo import Shape
from ..core.china import PROVINCES
from ..core.paths import INTERIM
from ..core.source import Source, SourceInfo

UNESCO_URL = ("https://data.unesco.org/api/explore/v2.1/catalog/datasets/whc001/exports/json?"
              "where=search(iso_codes,%22CN%22)&select=id_no,name_en,name_zh,date_inscribed,category,criteria_txt,"
              "area_hectares,states_names,iso_codes,transboundary,coordinates,geo_point_2d")
PARKS_URL = "https://www.news.cn/politics/2021-10/13/c_1127950871.htm"
MCT_URL = "https://www.mct.gov.cn/tourism/api/content/getContentListByDirId"
MCT_BODY = {"directoryId": 4, "page": 1, "size": 1000}
MOE_2022_URL = "http://www.moe.gov.cn/srcsite/A22/s7065/202202/W020220214318455516037.pdf"
MOE_2017_URL = "http://www.moe.gov.cn/srcsite/A22/moe_843/201709/W020170921369558197676.docx"
ZHWIKI = ("https://zh.wikipedia.org/w/api.php?action=query&prop=coordinates&format=json&formatversion=2"
          "&redirects=1&titles=")
NOMINATIM = "https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&accept-language=zh&q="
WD_SEARCH = "https://www.wikidata.org/w/api.php?action=wbsearchentities&language=zh&uselang=zh&format=json&limit=3&type=item&search="
WD_CLAIMS = "https://www.wikidata.org/w/api.php?action=wbgetclaims&format=json&property=P625&entity="
WD_HASSTMT = "https://www.wikidata.org/w/api.php?action=query&list=search&format=json&srsearch=haswbstatement:P757="
WD_LABELS = "https://www.wikidata.org/w/api.php?action=wbgetentities&format=json&props=labels|aliases&languages=zh|zh-cn|zh-hans|zh-sg|zh-hant|zh-tw|zh-hk|ja&ids="
WD_COORDS = "https://www.wikidata.org/w/api.php?action=query&prop=coordinates&format=json&formatversion=2&titles="
SPARQL = "https://query.wikidata.org/sparql?format=json&query="
LANGS = '("zh","zh-hans","zh-cn")'
Q_5A = ("SELECT ?item ?coord ?l WHERE { ?item wdt:P31 wd:Q6838244 ; wdt:P625 ?coord . "
        "?item rdfs:label|skos:altLabel ?l . FILTER(LANG(?l) IN " + LANGS + ") }")
Q_UNI = ("SELECT ?item ?coord ?l WHERE { ?item wdt:P17 wd:Q148 ; wdt:P31/wdt:P279* wd:Q38723 ; wdt:P625 ?coord . "
         "?item rdfs:label|skos:altLabel ?l . FILTER(LANG(?l) IN " + LANGS + ") }")
Q_PARK = ("SELECT ?item ?coord ?l WHERE { ?item wdt:P17 wd:Q148 ; wdt:P625 ?coord . "
          "?item rdfs:label|skos:altLabel ?l . FILTER(LANG(?l) IN " + LANGS + ") "
          "FILTER(STR(?l) IN (%s)) }")

MILITARY = {"国防科技大学", "海军军医大学", "空军军医大学", "第二军医大学", "第四军医大学"}
CATEGORY = {"Cultural": "cultural", "Natural": "natural", "Mixed": "mixed"}
_GENERIC = ("国家级旅游景区", "旅游景区", "风景名胜区", "名胜风景区", "生态旅游区", "文化旅游区", "旅游度假区",
            "文化景区", "生态景区", "旅游区", "风景区", "景区", "名胜区", "景点", "旅游")
_PUNCT = re.compile(r"[\s·•・．.、,，\-－—–~～/／()（）\"“”'‘’《》]")


# --- pure parsing helpers (unit-tested) --------------------------------------------------------------------

def parse_unesco(rows: list[dict]) -> list[dict]:
    """UNESCO whc001 rows → site dicts (no province yet). Keeps rows whose iso_codes include CN."""
    out = []
    for r in rows:
        codes = [c.upper() for c in (r.get("iso_codes") or [])]
        if "CN" not in codes:
            continue
        pts = []
        geom = (r.get("coordinates") or {}).get("geometry") or {}
        if geom.get("type") == "MultiPoint":
            pts = [(float(x), float(y)) for x, y in geom.get("coordinates") or []]
        elif geom.get("type") == "Point" and geom.get("coordinates"):
            pts = [tuple(map(float, geom["coordinates"]))]
        gp = r.get("geo_point_2d") or {}
        area = r.get("area_hectares")
        out.append({
            "whc_id": int(r["id_no"]),
            "name": (r.get("name_zh") or r.get("name_en") or "").strip(),
            "name_en": (r.get("name_en") or "").strip(),
            "category": CATEGORY.get(r.get("category"), (r.get("category") or "").lower() or None),
            "inscribed": int(str(r["date_inscribed"])[:4]) if r.get("date_inscribed") else None,
            "criteria": r.get("criteria_txt") or None,
            "area_ha": float(area) if area not in (None, "") else None,
            "transnational": str(r.get("transboundary")).lower() == "true" or len(codes) > 1,
            "states": codes,
            "_point": (float(gp["lon"]), float(gp["lat"])) if gp.get("lon") is not None else None,
            "_components": pts,
        })
    out.sort(key=lambda s: s["whc_id"])
    return out


def parse_parks(html: str) -> list[dict]:
    """The 2021 announcement → [{name, area_km2, provinces}] in order of appearance. `provinces` = province codes
    named in the park's own sentence ("跨四川、陕西和甘肃三省"), in the order written."""
    text = re.sub(r"<[^>]+>", "", html)
    text = re.sub(r"\s+", "", text)
    out, seen = [], set()
    shorts = sorted(((v[1], k) for k, v in PROVINCES.items()), key=lambda t: -len(t[0]))
    for m in re.finditer(r"([\u4e00-\u9fff]{2,8}国家公园)([^。]{0,40}?)保护面积([\d.]+)(万)?平方公里", text):
        name = re.split(r"首批|等|设立|这里是", m.group(1))[-1]
        if name in seen or not name.endswith("国家公园") or len(name) < 6:
            continue
        seen.add(name)
        val = float(m.group(3)) * (1e4 if m.group(4) else 1)
        provs = sorted({c for sh, c in shorts if sh in m.group(2)}, key=lambda c: m.group(2).index(PROVINCES[c][1]))
        out.append({"name": name, "area_km2": int(round(val)), "provinces": provs})
    return out


def year_of(text: str | None) -> int | None:
    m = re.search(r"(19|20)\d{2}", text or "")
    return int(m.group(0)) if m else None


def parse_mct(doc: dict) -> list[dict]:
    """MCT tourism directory JSON → [{name, designated, batch, year_text, provinces}]."""
    out = []
    for r in doc["data"]["contentList"]["content"]:
        if r.get("isDel"):
            continue
        provs = [str(c)[:2] for c in (r.get("provinceCodeDb") or r.get("provinceCode"), r.get("provinceCode2"),
                                      r.get("provinceCode3")) if c]
        y = year_of(r.get("year"))
        out.append({"name": r["name"].strip(), "designated": y, "batch": 1 if y == 2007 else None,
                    "year_text": r.get("year"), "provinces": list(dict.fromkeys(provs)), "mct_id": r.get("id")})
    return out


def pdf_lines(data: bytes) -> list[str]:
    """Text lines of a simple PDF (Flate streams, Type0 fonts with ToUnicode CMaps, Tj/TD/Tm operators):
    glyphs grouped by baseline y, sorted by x, pages in /Kids order."""
    objs = {int(m.group(1)): m.group(2) for m in re.finditer(rb"(\d+) 0 obj(.*?)endobj", data, re.S)}

    def stream(o: bytes) -> bytes:
        m = re.search(rb"stream\r?\n(.*?)endstream", o, re.S)
        if not m:
            return b""
        raw = m.group(1)
        return zlib.decompressobj().decompress(raw) if b"FlateDecode" in o else raw

    def cmap(b: bytes) -> dict[int, str]:
        out = {}
        for blk in re.findall(rb"beginbfchar(.*?)endbfchar", b, re.S):
            for a, u in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
                out[int(a, 16)] = bytes.fromhex(u.decode()).decode("utf-16-be", "replace")
        for blk in re.findall(rb"beginbfrange(.*?)endbfrange", b, re.S):
            for a, z, u in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
                a, z, u = int(a, 16), int(z, 16), int(u, 16)
                for i in range(a, z + 1):
                    out[i] = chr(u + i - a)
        return out

    def page_ids() -> list[int]:
        cat = next((o for o in objs.values() if re.search(rb"/Type\s*/Catalog", o)), b"")
        root = re.search(rb"/Pages\s+(\d+) 0 R", cat)
        order: list[int] = []

        def walk(n: int):
            o = objs.get(n, b"")
            kids = re.search(rb"/Kids\s*\[([^\]]*)\]", o)
            if kids and re.search(rb"/Type\s*/Pages", o):
                for k in re.findall(rb"(\d+) 0 R", kids.group(1)):
                    walk(int(k))
            elif o:
                order.append(n)
        if root:
            walk(int(root.group(1)))
        return order

    lines: list[str] = []
    op = re.compile(rb"/(\w+)\s+[\d.]+\s+Tf|([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+Tm"
                    rb"|([-\d.]+)\s+([-\d.]+)\s+TD|<([0-9A-Fa-f]*)>\s*Tj")
    for n in page_ids():
        o = objs[n]
        fonts = {}
        fm = re.search(rb"/Font\s*<<(.*?)>>", o, re.S)
        for name, ref in re.findall(rb"/(\w+)\s+(\d+) 0 R", fm.group(1) if fm else b""):
            tu = re.search(rb"/ToUnicode\s+(\d+) 0 R", objs.get(int(ref), b""))
            fonts[name.decode()] = cmap(stream(objs[int(tu.group(1))])) if tu else {}
        cm = re.search(rb"/Contents\s*(\[[^\]]*\]|\d+ 0 R)", o)
        refs = [int(r) for r in re.findall(rb"(\d+) 0 R", cm.group(1))] if cm else []
        content = b"".join(stream(objs[r]) for r in refs if r in objs)
        font: dict = {}
        x = y = 0.0
        scale = 1.0
        rows: dict[float, list] = {}
        for t in op.finditer(content):
            if t.group(1):
                font = fonts.get(t.group(1).decode(), {})
            elif t.group(2):
                scale, x, y = float(t.group(2)), float(t.group(6)), float(t.group(7))
            elif t.group(8):
                x += float(t.group(8)) * scale
            else:
                h = t.group(10).decode()
                s = "".join(font.get(int(h[i:i + 4], 16), "") for i in range(0, len(h), 4))
                rows.setdefault(round(y), []).append((x, s))
        for yy in sorted(rows):
            lines.append("".join(s for _, s in sorted(rows[yy])))
    return lines


_UNI_LINE = re.compile(r"^([\u4e00-\u9fff（）()]{2,20}?(?:大学|学院)(?:（[\u4e00-\u9fff]{2,4}）)?)：")


def parse_dfc_2022(lines: list[str]) -> list[str]:
    """Second-round list: every line 'XX大学：学科…' starts a university (order = 学校代码)."""
    out = []
    for ln in lines:
        m = _UNI_LINE.match(ln.strip())
        if m and m.group(1) not in out:
            out.append(m.group(1).replace("(", "（").replace(")", "）"))
    return out


def parse_dfc_2017(paragraphs: list[str]) -> dict[str, str]:
    """First-round list → {name: "A"|"B"} for 一流大学建设高校 (A类 / B类 paragraphs)."""
    out, cls = {}, None
    for p in paragraphs:
        p = p.strip()
        if re.match(r"^\d+\.\s*A类", p):
            cls = "A"
        elif re.match(r"^\d+\.\s*B类", p):
            cls = "B"
        elif p.startswith("二、"):
            cls = None
        elif cls and "、" in p:
            for n in p.split("、"):
                if n.strip():
                    out[n.strip()] = cls
    return out


def _same_school(osm_name: str, name: str) -> bool:
    """OSM campus name ↔ list name: same name, a campus of it ("长安大学渭水校区"), or the renamed school
    ("上海体育学院" → "上海体育大学": same stem before 大学/学院, stem ≥ 3 characters)."""
    stem = re.sub(r"(大学|学院)$", "", name)
    return osm_name.startswith(name) or (len(stem) >= 3 and re.sub(r"(大学|学院).*$", "", osm_name) == stem)


def docx_paragraphs(path) -> list[str]:
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    return ["".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p)) for p in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)]


def sparql_rows(doc: dict) -> list[tuple[str, float, float, str]]:
    """Wikidata SPARQL JSON (?item ?coord ?l) → [(qid, lon, lat, label)]."""
    out = []
    for b in doc["results"]["bindings"]:
        m = re.match(r"Point\(([-\d.eE]+) ([-\d.eE]+)\)", b["coord"]["value"])
        if m:
            out.append((b["item"]["value"].rsplit("/", 1)[-1], float(m.group(1)), float(m.group(2)),
                        b["l"]["value"].strip()))
    return out


def _strip_generic(s: str) -> str:
    changed = True
    while changed:
        changed = False
        for g in _GENERIC:
            if s.endswith(g) and len(s) > len(g) + 1:
                s = s[: -len(g)]
                changed = True
    return s


def norm_name(name: str, prefixes: list[str] = ()) -> str:
    """Normalise a scenic-area name: drop punctuation and generic suffixes (景区, 风景名胜区, 旅游区 …), then leading
    place names (longest first, repeatedly, always leaving ≥ 2 characters), then generic suffixes again.
    "黄山市黄山风景区" → "黄山"."""
    s = _strip_generic(_PUNCT.sub("", name))
    changed = True
    while changed:
        changed = False
        for p in prefixes:
            if p and s.startswith(p) and len(s) > len(p) + 1:
                s = s[len(p):]
                changed = True
                break
    return _strip_generic(s)


def match_name(target: str, candidates: dict[str, object]) -> object | None:
    """Best candidate whose normalised label is contained in `target` or contains it; a 2-character label only
    as the start of `target` ("黄山" ✓, "古镇" in "和顺古镇" ✗). Ties broken by longest overlap, then similarity."""
    best, key = None, None
    for label, val in candidates.items():
        if len(label) < 2 or not (label in target or target in label):
            continue
        if len(label) == 2 and not target.startswith(label):  # "古镇" must not match "和顺古镇"
            continue
        k = (min(len(label), len(target)), difflib.SequenceMatcher(None, label, target).ratio())
        if key is None or k > key:
            best, key = val, k
    return best


# --- geography from the cn_boundaries interim ----------------------------------------------------------------

class _Geo:
    def __init__(self):
        path = INTERIM / "cn_boundaries.json"
        if not path.exists():
            raise FileNotFoundError("cn_wonders needs the cn_boundaries interim – run: "
                                    "uv run python -m pipeline process cn_boundaries")
        d = json.loads(path.read_text(encoding="utf-8"))["data"]
        self.provinces = [(p["code"], Shape(p["rings"]), p) for p in d["provinces"]]
        self.prefs = [(p["adcode"], Shape(p["rings"]), p) for p in d["prefectures"] if p.get("rings")]
        self.pref_rows = d["prefectures"]
        self.counties = d["counties"]

    def province(self, lon, lat, near: bool = False) -> str | None:
        """Province code at the point; with `near`, also probe up to ~5 km around it (small islands such as
        鼓浪屿 vanish from the thinned rings)."""
        if lon is None:
            return None
        probes = [(0.0, 0.0)]
        if near:
            probes += [(dx * r, dy * r) for r in (0.01, 0.025, 0.05) for dx, dy in
                       ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1))]
        for dx, dy in probes:
            for code, shp, _ in self.provinces:
                if shp.contains(lon + dx, lat + dy):
                    return code
        return None

    def prefecture(self, lon, lat) -> str | None:
        if lon is None:
            return None
        for ad, shp, _ in self.prefs:
            if shp.contains(lon, lat):
                return ad
        return None

    def place_names(self, prov: str) -> list[str]:
        """Province, prefecture and county names (full + short) in `prov`, longest first: prefixes to strip."""
        names = set()
        for code, _, p in self.provinces:
            if code == prov:
                names |= {p["name"], p["short"], p["short"] + "省"}
        for p in self.pref_rows:
            if p["province"] == prov:
                names |= {p["name"], p.get("short") or ""}
                if p["name"].endswith("自治州"):
                    names.add((p.get("short") or "")[:2] + "州")
        for c in self.counties:
            if c["province"] == prov:
                names.add(c["name"])
        return sorted((n for n in names if len(n) >= 2), key=len, reverse=True)


def _pt(lon, lat):
    return round(lon, 5), round(lat, 5)


# --- the source -------------------------------------------------------------------------------------------

class CnWonders(Source):
    info = SourceInfo(
        id="cn_wonders",
        title="世界遗产、国家公园、5A级景区与“双一流”高校",
        publisher="联合国教科文组织世界遗产中心（data.unesco.org）；国家林业和草原局（国家公园管理局），经新华网发布；"
        "文化和旅游部；教育部 财政部 国家发展改革委；坐标补充：Wikidata、OpenStreetMap、中文维基百科",
        homepage="https://data.unesco.org/explore/dataset/whc001/",
        license="UNESCO 开放数据（CC BY-SA 3.0 IGO）；中国政府公开信息；Wikidata CC0；OpenStreetMap ODbL；维基百科 CC BY-SA",
        description="① 世界遗产：UNESCO 官方开放数据平台的《世界遗产名录》（whc001 数据集），收录全部位于中国的遗产"
        "（含跨国遗产的中国部分；澳门历史城区计入；台湾不在名录中），取中文官方名称、类别、列入年份、标准、面积与坐标；"
        "6 处记录缺中文名（鼓浪屿、可可西里、大运河、丝绸之路、红河哈尼梯田、新疆天山），中文名取 Wikidata 对应条目标签。"
        "② 国家公园：2021年10月12日正式设立的首批5个国家公园（截至2026年11月尚无第二批正式设立），名称与保护面积"
        "取自新华网转载的官方发布；坐标依次取 Wikidata、中文维基百科、OpenStreetMap，均无则取所在省份（单省公园）"
        "的几何中心（海南热带雨林）。③ 国家5A级旅游景区：文化和旅游部“旅游资源”目录（共358家）"
        "的名称、所属省份与评定年份；官方未公布坐标，按以下顺序定位并逐条记录方法：Wikidata“5A级旅游景区”条目名称匹配"
        "→ 同省世界遗产名称匹配 → Wikidata 搜索（须位于该省）→ 名称中所含县区的政府驻地 → 名称中所含地级市的政府驻地 → 无法定位则为空。④ 高校：第二轮“双一流”建设高校"
        "名单（教研函〔2022〕1号，PDF 附件，147所）与首轮名单（2017年，一流大学建设高校A/B类）；军队院校（3所）不收录。"
        "坐标取自 Wikidata，个别缺失者取 OpenStreetMap。各高校科技（R&D）经费：教育部《高等学校科技统计资料汇编》无公开的逐校电子表，暂为空（未知，并非0）。"
        "省份与地级市由坐标落入 DataV 行政区划多边形确定。",
        used_for=["奇观：世界遗产", "奇观：国家公园", "奇观：5A级旅游景区", "奇观：“双一流”高校"],
    )

    # --- fetch ---------------------------------------------------------------------------------------------

    def _post_json(self, url: str, body: dict, rel: str, force: bool = False):
        payload = json.dumps(body).encode()

        def attempt(tmp):
            import hashlib
            req = urllib.request.Request(url, data=payload, method="POST", headers={
                "User-Agent": fetch.USER_AGENT, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
                b = resp.read()
                out.write(b)
            return hashlib.sha256(b).hexdigest(), len(b)

        return fetch._download_with(self.id, url, rel, attempt, force=force, note=" (POST)",
                                    extra={"method": "POST", "body": body})

    def _sparql(self, query: str, rel: str, force: bool = False):
        """Wikidata Query Service: one request per minute at most and no quick retries (WDQS answers 429 to
        bursts), so each try is a single request and failures wait a full minute before the next."""
        url = SPARQL + urllib.parse.quote(query)
        if (self.raw_dir / rel).exists() and not force or self.offline:
            return self.download(url, rel)
        headers = {"Accept": "application/sparql-results+json"}
        old_retries = fetch.RETRIES
        for attempt in range(4):
            wait = 62 - (time.monotonic() - getattr(self, "_last_sparql", -1e9))
            if wait > 0:
                print(f"      (waiting {wait:.0f}s for the Wikidata Query Service rate limit)")
                time.sleep(wait)
            self._last_sparql = time.monotonic()
            fetch.RETRIES = 1
            try:
                path = self.download(url, rel, force=force, headers=headers)
                json.loads(path.read_text(encoding="utf-8"))  # a truncated body is not cached
                return path
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError, ValueError) as exc:
                self.discard(rel)
                if attempt == 3:
                    raise
                print(f"      Wikidata attempt {attempt + 1} failed ({exc}); retrying after the rate-limit pause")
            finally:
                fetch.RETRIES = old_retries

    def _park_query(self, names: list[str]) -> str:
        return Q_PARK % ",".join(json.dumps(n, ensure_ascii=False) for n in names)

    def fetch(self, force=False):
        self.download(UNESCO_URL, "unesco/whc001_cn.json", force=force)
        if self._whs_missing_zh():
            self._sparql(self._whs_query(), "wikidata/whs_zh.json", force=force)
            for whc in self._whs_missing_zh():
                if whc in self._whs_zh_names():
                    continue
                try:  # the SPARQL label query missed it: CirrusSearch haswbstatement + the item's labels
                    hits = json.loads(self.download(WD_HASSTMT + whc, f"wikidata/whs_{whc}_search.json",
                                                    force=force).read_text(encoding="utf-8"))
                    titles = [h["title"] for h in hits.get("query", {}).get("search", [])][:1]
                    if titles:
                        self.download(WD_LABELS + titles[0], f"wikidata/whs_{whc}_labels.json", force=force)
                except (OSError, http.client.HTTPException, ValueError) as exc:
                    print(f"    ! Wikidata WHC {whc}: {exc}")
        html = self.download(PARKS_URL, "parks/xinhua_20211013.html", force=force)
        parks = parse_parks(html.read_text(encoding="utf-8", errors="replace"))
        self._sparql(self._park_query([p["name"] for p in parks]), "wikidata/parks.json", force=force)
        self.download(ZHWIKI + urllib.parse.quote("|".join(p["name"] for p in parks)), "parks/zhwiki.json",
                      force=force)
        for k, p in enumerate(parks):  # OSM Nominatim (1 request/s policy), used only where the above lack a point
            if not (self.raw_dir / f"parks/nominatim_{k}.json").exists() or force:
                time.sleep(1.2)
            self.download(NOMINATIM + urllib.parse.quote(p["name"]), f"parks/nominatim_{k}.json", force=force)
        self._post_json(MCT_URL, MCT_BODY, "mct/5a.json", force=force)
        self._sparql(Q_5A, "wikidata/5a.json", force=force)
        geo = _Geo()
        self._scenic(geo, self._unesco(geo), search=True, force=force)
        self.download(MOE_2022_URL, "moe/dfc_2022.pdf", force=force, expect_binary=True)
        self.download(MOE_2017_URL, "moe/dfc_2017.docx", force=force, expect_binary=True)
        self._sparql(Q_UNI, "wikidata/universities.json", force=force)
        still = []
        for name in self._uni_missing():  # not labelled as a higher-education institution: Wikidata search API
            if not any(self._wd_point(qid, force) for qid in self._wd_search(name, force)):
                still.append(name)
        for name in still:  # no Wikidata coordinates at all: OSM Nominatim (1 request/s policy)
            rel = f"osm/nominatim_{name}.json"
            if not (self.raw_dir / rel).exists() or force:
                time.sleep(1.5)
            try:
                self.download(NOMINATIM + urllib.parse.quote(name), rel, force=force)
            except (urllib.error.URLError, fetch.OfflineError) as exc:
                print(f"    ! Nominatim {name}: {exc}")

    def _uni_names(self) -> list[str]:
        names = parse_dfc_2022(pdf_lines((self.raw_dir / "moe/dfc_2022.pdf").read_bytes()))
        return [n for n in names if n not in MILITARY]

    def _uni_index(self) -> dict[str, dict[str, list]]:
        wd: dict[str, dict[str, list]] = {}
        for qid, lon, lat, label in sparql_rows(json.loads(self._read("wikidata/universities.json"))):
            label = label.replace("(", "（").replace(")", "）")
            wd.setdefault(label, {}).setdefault(qid, []).append((lon, lat))
        return wd

    def _uni_missing(self) -> list[str]:
        wd = self._uni_index()
        return [n for n in self._uni_names() if n not in wd]

    def _wd_search(self, name: str, force: bool = False) -> list[str]:
        rel = f"wikidata/search_{name}.json"
        if not (self.raw_dir / rel).exists() and not self.offline:
            time.sleep(3.0)
        try:
            path = self.download(WD_SEARCH + urllib.parse.quote(name), rel, force=force)
        except (urllib.error.URLError, fetch.OfflineError) as exc:
            print(f"    ! Wikidata search {name}: {exc}")
            return []
        hits = json.loads(path.read_text(encoding="utf-8")).get("search", [])
        return [h["id"] for h in hits if "article" not in (h.get("description") or "")]

    def _wd_point(self, qid: str, force: bool = False) -> tuple[float, float] | None:
        rel = f"wikidata/{qid}_P625.json"
        if not (self.raw_dir / rel).exists() and not self.offline:
            time.sleep(3.0)
        try:
            path = self.download(WD_CLAIMS + qid, rel, force=force)
        except (urllib.error.URLError, fetch.OfflineError) as exc:
            print(f"    ! Wikidata {qid}: {exc}")
            return None
        for c in json.loads(path.read_text(encoding="utf-8")).get("claims", {}).get("P625", []):
            v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
            if v and "latitude" in v:
                return v["longitude"], v["latitude"]
        return None

    # --- process -------------------------------------------------------------------------------------------

    def _read(self, rel):
        return (self.raw_dir / rel).read_text(encoding="utf-8", errors="replace")

    def process(self):
        geo = _Geo()
        unesco = self._unesco(geo)
        parks = self._parks(geo)
        scenic_files: list[str] = []
        scenic = self._scenic(geo, unesco, scenic_files)
        unis, uni_files = self._universities(geo)
        latest = max(s["inscribed"] for s in unesco if s["inscribed"])
        vintages = {
            "unesco": f"UNESCO 世界遗产名录（data.unesco.org whc001，截至{latest}年）",
            "parks": "国家林业和草原局：首批国家公园（2021年10月12日设立）",
            "scenic_5a": f"文化和旅游部 国家5A级旅游景区目录（{len(scenic)}家，截至{max(s['designated'] or 0 for s in scenic)}年）",
            "universities": "教育部等：第二轮“双一流”建设高校名单（2022年）",
        }
        print(f"    UNESCO {len(unesco)}, 国家公园 {len(parks)}, 5A {len(scenic)} "
              f"(located {sum(s['lon'] is not None for s in scenic)}), 高校 {len(unis)} "
              f"(located {sum(u['lon'] is not None for u in unis)})")
        return {
            "vintage": "；".join(vintages.values()),
            "vintages": vintages,
            "unesco": unesco, "national_parks": parks, "scenic_5a": scenic, "universities": unis,
            "_files": ["unesco/whc001_cn.json", "wikidata/whs_zh.json", "parks/xinhua_20211013.html", "wikidata/parks.json", "parks/zhwiki.json",
                       *[f"parks/nominatim_{k}.json" for k in range(len(parks))], "mct/5a.json",
                       "wikidata/5a.json", "moe/dfc_2022.pdf", "moe/dfc_2017.docx", "wikidata/universities.json", *uni_files, *scenic_files, *self._whs_files],
        }

    def _unesco(self, geo: _Geo) -> list[dict]:
        zh = self._whs_zh_names()
        out = []
        for s in parse_unesco(json.loads(self._read("unesco/whc001_cn.json"))):
            point, comps = s.pop("_point"), s.pop("_components")
            inside = [c for c in comps if geo.province(*c, near=True)]
            if len(inside) > 1 or (inside and (point is None or geo.province(*point, near=True) is None)):
                ref = point if point and geo.province(*point, near=True) else (
                    sum(c[0] for c in inside) / len(inside), sum(c[1] for c in inside) / len(inside))
                point = min(inside, key=lambda c: (c[0] - ref[0]) ** 2 + (c[1] - ref[1]) ** 2)
            lon, lat = _pt(*point) if point else (None, None)
            s.update(lon=lon, lat=lat, province=geo.province(lon, lat, near=True))
            s["provinces"] = sorted({p for p in (geo.province(*c) for c in comps) if p}
                                    | ({s["province"]} - {None}))
            if s["province"] in ("81", "82"):  # SAR sites: GCJ/WGS offsets push edge components into 珠海/深圳
                s["provinces"] = [s["province"]]
            if s["name"] == s["name_en"]:  # no name_zh in UNESCO's record: Wikidata label (P757 = whc_id)
                s["name"] = zh.get(str(s["whc_id"]), s["name"])
                s["name_src"] = "wikidata" if str(s["whc_id"]) in zh else "unesco_en"
            out.append(s)
        return out

    def _whs_missing_zh(self) -> list[str]:
        rows = json.loads(self._read("unesco/whc001_cn.json"))
        return sorted(str(r["id_no"]) for r in rows if "CN" in (r.get("iso_codes") or []) and not r.get("name_zh"))

    def _whs_query(self) -> str:
        ids = " ".join(json.dumps(i) for i in self._whs_missing_zh())
        return ("SELECT ?id ?l WHERE { VALUES ?id { " + ids + " } ?item wdt:P757 ?id ; rdfs:label ?l . "
                "FILTER(LANG(?l) IN (\"zh-cn\",\"zh-hans\",\"zh\")) }")

    def _whs_zh_names(self) -> dict[str, str]:
        self._whs_files: list[str] = []
        path = self.raw_dir / "wikidata/whs_zh.json"
        if not path.exists():
            return {}
        best: dict[str, tuple[int, str]] = {}
        rank = {"zh-cn": 0, "zh-hans": 1, "zh": 2}
        for b in json.loads(path.read_text(encoding="utf-8"))["results"]["bindings"]:
            r = rank.get(b["l"].get("xml:lang"), 3)
            k = b["id"]["value"]
            if k not in best or r < best[k][0]:
                best[k] = (r, b["l"]["value"].strip())
        out = {k: v for k, (_, v) in best.items()}
        for whc in self._whs_missing_zh():
            lp = self.raw_dir / f"wikidata/whs_{whc}_labels.json"
            if whc not in out and lp.exists():
                for ent in json.loads(lp.read_text(encoding="utf-8")).get("entities", {}).values():
                    labels = ent.get("labels", {})
                    # last resort, in order; "ja" only when the Japanese label is pure CJK ideographs (新疆天山 is
                    # spelt identically in Chinese and has no Chinese label on Wikidata)
                    for lang in ("zh-cn", "zh-hans", "zh", "zh-sg", "zh-hant", "zh-tw", "zh-hk", "ja"):
                        if lang in labels and (lang != "ja" or re.fullmatch(r"[\u4e00-\u9fff]+",
                                                                             labels[lang]["value"].strip())):
                            out[whc] = labels[lang]["value"].strip()
                            self._whs_files += [f"wikidata/whs_{whc}_search.json", f"wikidata/whs_{whc}_labels.json"]
                            break
        return out

    def _parks(self, geo: _Geo) -> list[dict]:
        parks = parse_parks(self._read("parks/xinhua_20211013.html"))
        wd = {}
        for qid, lon, lat, label in sparql_rows(json.loads(self._read("wikidata/parks.json"))):
            wd.setdefault(label, (qid, lon, lat))
        zhwiki = {}
        if (self.raw_dir / "parks/zhwiki.json").exists():
            q = json.loads(self._read("parks/zhwiki.json"))["query"]
            alias = {r["to"]: r["from"] for r in q.get("redirects", [])}
            for pg in q.get("pages", []):
                c = (pg.get("coordinates") or [None])[0]
                if c:
                    zhwiki[alias.get(pg["title"], pg["title"])] = (c["lon"], c["lat"])
        out = []
        for k, p in enumerate(parks):
            qid, lon, lat = wd.get(p["name"], (None, None, None))
            method = "wikidata" if lon is not None else None
            if lon is None and p["name"] in zhwiki:
                (lon, lat), method = zhwiki[p["name"]], "zhwiki"
            if lon is None and (self.raw_dir / f"parks/nominatim_{k}.json").exists():
                hits = [h for h in json.loads(self._read(f"parks/nominatim_{k}.json"))
                        if (h.get("name") or "").startswith(p["name"])]
                if hits:
                    lon, lat, method = float(hits[0]["lon"]), float(hits[0]["lat"]), "osm"
            if lon is None and len(p["provinces"]) == 1:
                row = next(r for c, _, r in geo.provinces if c == p["provinces"][0])
                if row.get("centroid"):
                    (lon, lat), method = row["centroid"], "province_centroid"
            if lon is not None:
                lon, lat = _pt(lon, lat)
            else:
                print(f"    ! no coordinates for {p['name']}")
            prov = geo.province(lon, lat)
            provs = p["provinces"] or ([prov] if prov else [])
            out.append({"name": p["name"], "established": "2021-10-12", "area_km2": p["area_km2"], "lon": lon,
                        "lat": lat, "rings": None, "province": prov if prov in provs or not provs else provs[0],
                        "provinces": provs, "geo": method, "wikidata": qid})
        return out

    def _scenic(self, geo: _Geo, unesco: list[dict] | None = None, files: list | None = None,
                search: bool = False, force: bool = False) -> list[dict]:
        """Geocode the MCT 5A list (method order in the module docstring). With `search=True` (fetch), areas
        still unplaced after the Wikidata-5A and UNESCO passes are looked up with the Wikidata search API."""
        rows = parse_mct(json.loads(self._read("mct/5a.json")))
        prefix_cache: dict[str, list[str]] = {}

        def prefixes(prov):
            if prov not in prefix_cache:
                prefix_cache[prov] = geo.place_names(prov)
            return prefix_cache[prov]

        wd_by_prov: dict[str, dict[str, tuple]] = {}
        prov_of_item: dict[str, str | None] = {}
        for qid, lon, lat, label in sparql_rows(json.loads(self._read("wikidata/5a.json"))):
            if qid not in prov_of_item:
                prov_of_item[qid] = geo.province(lon, lat)
            prov = prov_of_item[qid]
            if prov:
                wd_by_prov.setdefault(prov, {}).setdefault(norm_name(label, prefixes(prov)), (qid, lon, lat))
        un_by_prov: dict[str, dict[str, tuple]] = {}
        for u in unesco or []:
            if u["province"] and u["lon"] is not None:
                un_by_prov.setdefault(u["province"], {})[norm_name(u["name"].split("：")[0])] = (None, u["lon"],
                                                                                                    u["lat"])
        seats_county = {}
        for c in geo.counties:
            if c.get("seat") and not c.get("whole_prefecture"):
                seats_county.setdefault(c["province"], []).append((c["name"], c["seat"]))

        out, used = [], {}
        for r in rows:
            provs = [p for p in r["provinces"] if p]
            lon = lat = qid = method = None
            for table, how in ((wd_by_prov, "wikidata"), (un_by_prov, "unesco")):
                for prov in provs:
                    hit = match_name(norm_name(r["name"], prefixes(prov)), table.get(prov, {}))
                    if hit:
                        (qid, lon, lat), method = hit, how
                        break
                if method:
                    break
            if method is None and provs:
                term = norm_name(re.split(r"[—－\-•·・–(（]", r["name"])[0] or r["name"], prefixes(provs[0]))
                rel = f"wikidata/5a_search/{r['mct_id']}.json"
                if search and (not (self.raw_dir / rel).exists() or force):
                    self._wd_search_items(term, rel, force)
                if (self.raw_dir / rel).exists():
                    named = self._named_prefecture(r["name"], provs, geo)
                    hit = self._pick_search_hit(rel, term, provs, geo, prefixes, named["adcode"] if named else None)
                    if files is not None:
                        files += [rel + ".search", rel]
                    if hit:
                        (qid, lon, lat), method = hit, "wikidata_search"
            if method is None:
                text = _PUNCT.sub("", r["name"])
                for prov in provs:
                    cands = [(n, s) for n, s in seats_county.get(prov, []) if n in text and len(n) >= 2]
                    if cands:
                        _, (lon, lat) = max(cands, key=lambda t: len(t[0]))
                        method = "county_seat"
                        break
                    named = self._named_prefecture(r["name"], [prov], geo)
                    if named:
                        lon, lat = named["seat"]
                        method = "prefecture_seat"
                        break
            if lon is not None:
                lon, lat = _pt(lon, lat)
            if qid:
                used.setdefault(qid, []).append(r["name"])
            prov = geo.province(lon, lat) if lon is not None else None
            if prov not in provs:
                prov = provs[0] if provs else prov
            out.append({"name": r["name"], "designated": r["designated"], "batch": r["batch"],
                        "year_text": r["year_text"], "lon": lon, "lat": lat, "province": prov,
                        "provinces": r["provinces"], "city": geo.prefecture(lon, lat), "geo": method,
                        "wikidata": qid})
        dup = {q: n for q, n in used.items() if len(n) > 1}
        if dup:
            print(f"    ! {len(dup)} Wikidata items matched several 5A areas: {list(dup.values())[:5]}")
        return out

    def _wd_search_items(self, term: str, rel: str, force: bool = False):
        """Wikidata search (ordered hits with labels) → `<rel>.search`; the hits' coordinates (GeoData) → `<rel>`."""
        for attempt in range(3):
            try:
                if not (self.raw_dir / (rel + ".search")).exists() or force:
                    time.sleep(2.0)
                hits = json.loads(self.download(WD_SEARCH + urllib.parse.quote(term), rel + ".search",
                                                force=force).read_text(encoding="utf-8")).get("search", [])
                ids = [h["id"] for h in hits][:5]
                time.sleep(2.0)
                self.download(WD_COORDS + "|".join(ids or ["Q0"]), rel, force=force)
                return
            except urllib.error.HTTPError as exc:
                exc.close()
                if exc.code != 429 or attempt == 2:
                    print(f"    ! Wikidata search {term}: {exc}")
                    return
                time.sleep(30)  # Wikidata API throttling: back off
            except (OSError, http.client.HTTPException, fetch.OfflineError, ValueError) as exc:
                print(f"    ! Wikidata search {term}: {exc}")
                return

    @staticmethod
    def _named_prefecture(name: str, provs: list[str], geo: _Geo) -> dict | None:
        """The prefecture of `provs` whose name (full, short, or “XX州”) appears in the area's name (longest)."""
        text = _PUNCT.sub("", name)
        prefs = [p for p in geo.pref_rows if p["province"] in provs and p.get("seat") and
                 p["kind"] != "municipality" and
                 (p["name"] in text or (p.get("short") and len(p["short"]) >= 2 and p["short"] in text)
                  or (p["name"].endswith("自治州") and (p.get("short") or "")[:2] + "州" in text))]
        return max(prefs, key=lambda p: len(p["name"])) if prefs else None

    def _pick_search_hit(self, rel, term, provs, geo, prefixes, pref=None):
        """First search hit (in Wikidata's order) that is not a scholarly article, has a point inside one of
        the area's provinces and whose label or matched alias agrees with the normalised name."""
        spath, cpath = self.raw_dir / (rel + ".search"), self.raw_dir / rel
        if not (spath.exists() and cpath.exists()):
            return None
        pts = {}
        for pg in json.loads(cpath.read_text(encoding="utf-8")).get("query", {}).get("pages", []):
            c = (pg.get("coordinates") or [None])[0]
            if c:
                pts[pg["title"]] = (c["lon"], c["lat"])
        for h in json.loads(spath.read_text(encoding="utf-8")).get("search", [])[:5]:
            desc = h.get("description") or ""
            if "article" in desc or "文章" in desc:
                continue
            pt = pts.get(h["id"])
            if not pt or geo.province(*pt) not in provs:
                continue
            if pref and geo.prefecture(*pt) != pref:  # the name says which prefecture: the hit must be there
                continue
            labels = {norm_name(x, prefixes(provs[0])) for x in (h.get("label"), (h.get("match") or {}).get("text"))
                      if x}
            if any(match_name(term, {lab: True}) for lab in labels):
                return h["id"], pt[0], pt[1]
        return None

    def _universities(self, geo: _Geo) -> tuple[list[dict], list[str]]:
        first = parse_dfc_2017(docx_paragraphs(self.raw_dir / "moe/dfc_2017.docx"))
        wd = self._uni_index()
        files = []
        out = []
        for name in self._uni_names():
            lon = lat = qid = None
            method = None
            cands = wd.get(name)
            if cands:
                qid = min(cands, key=lambda q: int(q[1:]))  # oldest item = the institution itself
                pts = sorted(set(cands[qid]))
                pick = None
                for pt in pts:
                    ad = geo.prefecture(*pt)
                    row = next((p for p in geo.pref_rows if p["adcode"] == ad), None)
                    if row and row.get("short") and row["short"] in name:
                        pick = pt
                        break
                (lon, lat), method = (pick or pts[0]), "wikidata"
            elif (self.raw_dir / f"wikidata/search_{name}.json").exists():
                files.append(f"wikidata/search_{name}.json")
                for q in self._wd_search(name):
                    if (self.raw_dir / f"wikidata/{q}_P625.json").exists():
                        files.append(f"wikidata/{q}_P625.json")
                        pt = self._wd_point(q)
                        if pt:
                            qid, (lon, lat), method = q, pt, "wikidata_search"
                            break
            osm = self.raw_dir / f"osm/nominatim_{name}.json"
            if lon is None and osm.exists():
                files.append(f"osm/nominatim_{name}.json")
                hits = [h for h in json.loads(osm.read_text(encoding="utf-8"))
                        if h.get("category") == "amenity" and _same_school(h.get("name") or "", name)]
                if hits:
                    lon, lat, method = float(hits[0]["lon"]), float(hits[0]["lat"]), "osm"
            if lon is not None:
                lon, lat = _pt(lon, lat)
            else:
                print(f"    ! no coordinates for {name}")
            out.append({"name": name, "double_first_class": True,
                        "world_class_2017": first.get(name), "rd_cny": None, "rd_year": None,
                        "lon": lon, "lat": lat, "province": geo.province(lon, lat),
                        "city": geo.prefecture(lon, lat), "geo": method, "wikidata": qid})
        return out, files
