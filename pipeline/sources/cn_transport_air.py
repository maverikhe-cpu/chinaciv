"""Civil transport airports: CAAC 全国民用运输机场生产统计公报 (latest year), placed by ICAO code.

Interim `cn_transport_air` (docs/CN_PLAN.md §4.7). Three downloads:

* CAAC (中国民用航空局) statistics column → the newest "全国民用运输机场生产统计公报" article → its attachment
  "全国民用运输机场吞吐量排名.xlsx": every civil transport airport on the mainland with 旅客吞吐量 (人次),
  货邮吞吐量 (吨) and 起降架次, this year and the previous year ("上年同期"), as printed.
* Wikidata (CC0): every item with an ICAO code starting Z/VH/VM/RC and its Chinese labels. The 公报 names airports
  only as "城市/机场" (e.g. "上海/浦东"); the name is matched to a Wikidata item whose Chinese label contains the
  airport part (and, when several do, also the city part). `ICAO_OVERRIDES` pins the few the labels miss.
* OurAirports `airports.csv` (public domain): coordinates and IATA by ICAO. Wikidata P625/P238 are the fallback.

Decisions (the plan leaves these open):

* Year: the newest 公报 found at fetch time (2025 as of 2026-11). Discovery reads the CAAC column index; the
  chosen article URL is cached in the manifest, so `--offline` reuses the same files.
* Blank cells are null (the publisher leaves cargo blank for small airports; it does not print 0).
* `cargo_t` is rounded to 0.1 t (the xlsx carries float noise from kg).
* Extra fields beyond the plan: `prefecture` (6-digit adcode of the prefecture-level unit, or the 省直辖县级
  adcode for e.g. 琼海/博鳌), `county` (county adcode) — found by point-in-polygon on the `cn_boundaries`
  interim (DataV, GCJ-02; the WGS-84 airport points are not converted, ≤ 0.6 km). If `cn_boundaries` has not
  been processed yet these are null; process `cn_boundaries` first. Points just off the polygons (airports on
  reclaimed land) are looked up again at small offsets up to ~5 km.
* `extra` (香港/澳门/台湾): the CAAC 公报 covers the mainland only. Their own authorities' annual figures were not
  fetchable as a plain file without a key (HKIA publishes only press releases/PDF fact sheets, the Taiwan CAA
  site refused the connection), so `extra` is empty and `extra_note` says so. Their airports are not given 0.
"""

from __future__ import annotations

import csv
import json
import re
import urllib.parse
from pathlib import Path

from ..core.geo import Shape
from ..core.paths import INTERIM
from ..core.source import Source, SourceInfo
from ._resource_util import xlsx_rows

CAAC_BASE = "https://www.caac.gov.cn/XXGK/XXGK/TJSJ/"
CAAC_INDEX = CAAC_BASE + "index_1216.html"
TITLE_RE = re.compile(r'href="\./(\d{6}/t\d+_\d+\.html)"[^>]*title="((\d{4})年全国民用运输机场生产统计公报)"')
XLSX_RE = re.compile(r'href="\./(P\d+\.xlsx)"[^>]*>\s*(\d{4})年全国民用运输机场吞吐量排名')

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
WIKIDATA_QUERY = """SELECT ?a ?icao ?iata ?coord (GROUP_CONCAT(DISTINCT ?l; separator="|") AS ?labels) WHERE {
 ?a wdt:P239 ?icao.
 FILTER(STRSTARTS(?icao,"Z") || STRSTARTS(?icao,"VH") || STRSTARTS(?icao,"VM") || STRSTARTS(?icao,"RC"))
 OPTIONAL{?a wdt:P238 ?iata} OPTIONAL{?a wdt:P625 ?coord}
 OPTIONAL{ {?a rdfs:label ?l} UNION {?a skos:altLabel ?l}
   FILTER(lang(?l) IN ("zh","zh-hans","zh-cn","zh-hant","zh-tw","zh-hk")) }
} GROUP BY ?a ?icao ?iata ?coord"""
OURAIRPORTS = "https://davidmegginson.github.io/ourairports-data/airports.csv"

INDEX_FILE = "caac_index.html"
WD_FILE = "wikidata_airports.csv"
OA_FILE = "ourairports_airports.csv"

# 公报 names whose Wikidata labels do not contain the airport part (renamed airports, new items).
ICAO_OVERRIDES = {
    "和田/昆冈": "ZWTN",  # 和田机场, renamed 和田昆冈机场
    "巴里坤/大河": "ZWLK",  # 巴里坤大河机场 (no Chinese label on Wikidata at fetch time)
}
# ICAO prefixes of foreign airports that share the Z… block (North Korea, Mongolia).
FOREIGN_ICAO = ("ZK", "ZM")


# --- parsing ------------------------------------------------------------------


def _num(text: str) -> float | None:
    t = (text or "").replace(",", "").strip()
    if not t or t in {"-", "–", "—", "…"}:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _int(text: str) -> int | None:
    v = _num(text)
    return None if v is None else int(round(v))


def parse_ranking(rows) -> list[dict]:
    """Rows of the CAAC ranking sheet ({column letter: text}) → airport records in sheet order.

    Layout (2021–2025 files): A 机场 "城市/机场"; B–E 旅客 名次/本期/上年/增减; F–I 货邮 (吨); J–M 起降架次.
    Data starts after the "合计" row and ends at the "注：" footnote. Blank cells → None.
    """
    out, started = [], False
    for r in rows:
        name = (r.get("A") or "").strip()
        if not started:
            started = name == "合计"
            continue
        if not name or name.startswith("注"):
            break
        cargo = _num(r.get("G", ""))
        out.append({
            "name": name,
            "passengers": _int(r.get("C", "")),
            "passengers_prev": _int(r.get("D", "")),
            "cargo_t": None if cargo is None else round(cargo, 1),
            "movements": _int(r.get("K", "")),
        })
    if not out:
        raise ValueError("CAAC ranking sheet: no rows after 合计")
    return out


def _norm_label(s: str) -> str:
    return s.replace("機場", "机场").replace("國際", "").replace("国际", "").strip()


def load_wikidata(path: Path) -> list[dict]:
    items: dict[str, dict] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            icao = (r.get("icao") or "").strip().upper()
            if not icao or icao.startswith(FOREIGN_ICAO):
                continue
            it = items.setdefault(icao, {"icao": icao, "iata": None, "coord": None, "labels": set(), "qid": None})
            it["qid"] = it["qid"] or r["a"].rsplit("/", 1)[-1]
            it["iata"] = it["iata"] or (r.get("iata") or "").strip() or None
            m = re.match(r"Point\(([-\d.]+) ([-\d.]+)\)", r.get("coord") or "")
            if m and not it["coord"]:
                it["coord"] = (float(m.group(1)), float(m.group(2)))
            it["labels"].update(_norm_label(x) for x in (r.get("labels") or "").split("|") if x)
    return list(items.values())


def match_icao(name: str, wd: list[dict]) -> str | None:
    """CAAC "城市/机场" → ICAO via Wikidata Chinese labels (unique match only)."""
    if name in ICAO_OVERRIDES:
        return ICAO_OVERRIDES[name]
    city, _, ap = name.partition("/")
    ap = ap or city
    cand = [w for w in wd if w["icao"].startswith("Z") and any(ap in lab for lab in w["labels"])]
    if len(cand) > 1:
        narrowed = [w for w in cand if any(city in lab for lab in w["labels"])]
        cand = narrowed or cand
    if len(cand) > 1:
        exact = {name, city + ap + "机场", ap + "机场"}
        narrowed = [w for w in cand if w["labels"] & exact]
        cand = narrowed or cand
    return cand[0]["icao"] if len(cand) == 1 else None


def load_ourairports(path: Path) -> dict[str, dict]:
    by_icao = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["iso_country"] not in {"CN", "HK", "MO", "TW"}:
                continue
            for code in (r.get("icao_code"), r.get("gps_code"), r.get("ident")):
                code = (code or "").strip().upper()
                if re.fullmatch(r"[A-Z]{4}", code) and code not in by_icao:
                    by_icao[code] = r
    return by_icao


# --- administrative lookup (shared with cn_transport_ports) --------------------------------------------------


class AdminLocator:
    """lon/lat → (province code, prefecture adcode, county adcode) on the cn_boundaries interim, or Nones."""

    OFFSETS = [(0, 0)] + [(dx * s, dy * s) for s in (0.01, 0.02, 0.04) for dx, dy in
                          ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1))]

    def __init__(self, boundaries: dict | None = None):
        if boundaries is None:
            path = INTERIM / "cn_boundaries.json"
            boundaries = json.loads(path.read_text())["data"] if path.exists() else None
        self.ok = boundaries is not None
        if not self.ok:
            return
        self.provinces = [(p["code"], Shape(p["rings"])) for p in boundaries["provinces"]]
        self.prefectures = [(p["adcode"], p["province"], Shape(p["rings"]))
                            for p in boundaries["prefectures"] if p["rings"]]
        self.counties: dict[str, list] = {}
        for c in boundaries["counties"]:
            self.counties.setdefault(c["prefecture"], []).append((c["adcode"], Shape(c["rings"])))
        self.by_adcode = {p["adcode"]: p for p in boundaries["prefectures"]}
        self.by_adcode.update({c["adcode"]: c for c in boundaries["counties"]})

    def locate(self, lon: float, lat: float) -> tuple[str | None, str | None, str | None]:
        if not self.ok:
            return None, None, None
        for dx, dy in self.OFFSETS:
            x, y = lon + dx, lat + dy
            for adcode, prov, shape in self.prefectures:
                if shape.contains(x, y):
                    county = next((c for c, s in self.counties.get(adcode, ()) if s.contains(x, y)), None)
                    return prov, adcode, county
        for dx, dy in self.OFFSETS:  # Taiwan (no prefecture polygons) or a gap between prefectures
            for code, shape in self.provinces:
                if shape.contains(lon + dx, lat + dy):
                    return code, None, None
        # Small islands simplified away in the polygons (e.g. 永兴岛, seat of 三沙): nearest county seat ≤ ~11 km.
        best, best_d = None, 0.1 ** 2
        for c in self.by_adcode.values():
            s = c.get("seat")
            if s and "prefecture" in c and (d := (s[0] - lon) ** 2 + (s[1] - lat) ** 2) < best_d:
                best, best_d = c, d
        if best:
            return best["province"], best["prefecture"], best["adcode"]
        return None, None, None


# --- source -------------------------------------------------------------------


class CnTransportAir(Source):
    info = SourceInfo(
        id="cn_transport_air",
        title="全国民用运输机场生产统计公报（机场吞吐量排名）",
        publisher="中国民用航空局；机场位置：OurAirports、维基数据（Wikidata）",
        homepage="https://www.caac.gov.cn/XXGK/XXGK/TJSJ/index_1216.html",
        license="中国民用航空局政府信息公开（注明出处使用）；OurAirports 数据为公有领域；维基数据为 CC0",
        description="中国民用航空局每年发布的《全国民用运输机场生产统计公报》所附“全国民用运输机场吞吐量排名”表："
        "内地全部民用运输机场的旅客吞吐量（人次，进出港合计）、货邮吞吐量（吨）和起降架次，含本年与上年同期。"
        "公报只给出“城市/机场”名称，机场经维基数据中文标签匹配到 ICAO 代码，再按 ICAO 从 OurAirports 取经纬度"
        "和 IATA 代码（缺失时用维基数据坐标）；所在省份、地级行政区和区县由行政区划边界（阿里云 DataV）点位判定。"
        "公报不含香港、澳门、台湾地区机场，其数据未纳入（记为空值，而非 0）。空白单元格记为空值。",
        used_for=["地图上的机场（按旅客吞吐量排序与分级）", "城市详情中的机场", "交通设施统计"],
    )

    # ---- fetch

    def _latest(self, force: bool) -> tuple[int, str, str]:
        """(year, article URL, xlsx URL) of the newest 公报, from cached pages unless force."""
        index = self.download(CAAC_INDEX, INDEX_FILE, force=force).read_text(encoding="utf-8", errors="replace")
        found = sorted(((int(y), href, title) for href, title, y in TITLE_RE.findall(index)), reverse=True)
        if not found:
            raise ValueError("CAAC index: no 全国民用运输机场生产统计公报 link found")
        year, href, _ = found[0]
        article_url = urllib.parse.urljoin(CAAC_BASE, href)
        page = self.download(article_url, f"caac_{year}_article.html", force=force)
        m = XLSX_RE.search(page.read_text(encoding="utf-8", errors="replace"))
        if not m:
            raise ValueError(f"{article_url}: no 吞吐量排名 xlsx attachment")
        return year, article_url, urllib.parse.urljoin(article_url, m.group(1))

    def fetch(self, force=False):
        year, _, xlsx_url = self._latest(force)
        self.download(xlsx_url, f"caac_{year}_ranking.xlsx", force=force, expect_binary=True)
        self.download(WIKIDATA_SPARQL + "?" + urllib.parse.urlencode({"query": WIKIDATA_QUERY}), WD_FILE,
                      force=force, headers={"Accept": "text/csv"})
        self.download(OURAIRPORTS, OA_FILE, force=force)

    # ---- process

    def process(self):
        files = sorted(self.raw_dir.glob("caac_*_ranking.xlsx"))
        if not files:
            raise FileNotFoundError("caac_<year>_ranking.xlsx not fetched")
        xlsx = files[-1]
        year = int(re.search(r"caac_(\d{4})_", xlsx.name).group(1))
        rows = parse_ranking(xlsx_rows(xlsx))
        wd = load_wikidata(self.raw_dir / WD_FILE)
        wd_by_icao = {w["icao"]: w for w in wd}
        oa = load_ourairports(self.raw_dir / OA_FILE)
        admin = AdminLocator()
        if not admin.ok:
            print("    ! cn_boundaries interim missing: province/prefecture/county left null")

        airports, unmatched = [], []
        for r in rows:
            icao = match_icao(r["name"], wd)
            w, o = wd_by_icao.get(icao), oa.get(icao)
            lon = lat = None
            coord_src = None
            if o and o.get("latitude_deg"):
                lon, lat, coord_src = float(o["longitude_deg"]), float(o["latitude_deg"]), "ourairports"
            elif w and w["coord"]:
                (lon, lat), coord_src = w["coord"], "wikidata"
            if icao is None or lon is None:
                unmatched.append(r["name"])
            iata = ((o or {}).get("iata_code") or "").strip() or (w or {}).get("iata")
            prov, pref, county = admin.locate(lon, lat) if lon is not None else (None, None, None)
            airports.append({
                "name": r["name"],
                "iata": iata or None,
                "icao": icao,
                "wikidata": (w or {}).get("qid"),
                "city": r["name"].partition("/")[0],
                "passengers": r["passengers"],
                "passengers_prev": r["passengers_prev"],
                "cargo_t": r["cargo_t"],
                "movements": r["movements"],
                "rank": None,
                "lon": None if lon is None else round(lon, 5),
                "lat": None if lat is None else round(lat, 5),
                "coord_src": coord_src,
                "province": prov,
                "prefecture": pref,
                "county": county,
            })
        if unmatched:
            print(f"    ! {len(unmatched)} airports without ICAO/coordinates: {', '.join(unmatched)}")
        # Sort by passengers desc (null last, keeping sheet order among them); rank 1-based over non-null.
        airports.sort(key=lambda a: -(a["passengers"] if a["passengers"] is not None else -1))
        rank = 0
        for a in airports:
            if a["passengers"] is not None:
                rank += 1
                a["rank"] = rank
        return {
            "vintage": f"中国民用航空局《{year}年全国民用运输机场生产统计公报》",
            "_files": [xlsx.name, WD_FILE, OA_FILE],
            "year": year,
            "units": {"passengers": "人次", "cargo_t": "吨", "movements": "架次"},
            "airports": airports,
            "extra": [],
            "extra_note": "香港、澳门、台湾地区机场不在民航局公报范围内；其官方年度数据未能以文件形式获取，暂未纳入（空值，非 0）。",
        }
