"""China's administrative boundaries from Alibaba Cloud DataV GeoAtlas (standard-map based), plus the dashed line.

DataV GeoAtlas (https://datav.aliyun.com/portal/school/atlas/area_selector) publishes GeoJSON for every
province, prefecture and county, derived from AutoNavi (高德) district data, which follows the national standard
map (标准地图): Taiwan, Hong Kong, Macau, Hainan with the South China Sea islands, 藏南 and 阿克赛钦 are inside the
national outline, and the ten-dash line (南海断续线) ships as a separate feature (adchar "JD") in 100000_full.

Files (areas_v3/bound):
  100000_full.json  provinces (34) + the dashed line         100000.json  national outline
  <adcode>_full.json  children of a province / city (≈ 360 files; DataV answers 403/404 for units without children)

Coordinates are GCJ-02 lon/lat as published (≤ ~0.6 km from WGS-84, far below a 25 km hex); they are not converted.
Taiwan has no subdivisions in DataV (GB/T 2260 lists only 710000): its six largest cities are added as points,
placed by Wikidata coordinates, with project-assigned ids (core/china.py TAIWAN_CITIES). Named islands (钓鱼岛,
赤尾屿, the South China Sea island groups) are label points, also placed by Wikidata (core/china.py ISLANDS).
See docs/CN_PLAN.md §4.1 for the interim schema.
"""

from __future__ import annotations

import json
import time
import urllib.error

from ..core import fetch
from ..core.china import (ISLANDS, MUNICIPALITIES, PROVINCES, SARS, TAIWAN, TAIWAN_CITIES, is_county_city,
                          prefecture_kind, province_code, short_name)
from ..core.geo import geojson_rings, thin_ring
from ..core.source import Source, SourceInfo

BASE = "https://geo.datav.aliyun.com/areas_v3/bound"
WIKIDATA = "https://www.wikidata.org/w/api.php?action=wbgetclaims&format=json&property=P625&entity="
TOL_PROVINCE = 0.005  # degrees (~0.5 km): vertex thinning, far below the 25 km hex
TOL_LOCAL = 0.01  # prefectures and counties (~1 km): point-in-polygon lookups only
DIGITS = 4  # ~10 m


def _rings(geom: dict, tol: float) -> list[list[list[float]]]:
    out = []
    for ring in geojson_rings(geom):
        r = thin_ring(ring, tol)
        if len(r) >= 4:
            out.append([[round(x, DIGITS), round(y, DIGITS)] for x, y in r])
    return out


def _pt(v) -> list[float] | None:
    if isinstance(v, str):  # areas_v2 style "lon,lat"
        v = [float(t) for t in v.split(",")]
    return [round(float(v[0]), 5), round(float(v[1]), 5)] if v else None


class CnBoundaries(Source):
    info = SourceInfo(
        id="cn_boundaries",
        title="中国行政区划边界（省、地、县）与南海断续线",
        publisher="阿里云 DataV.GeoAtlas（数据源自高德开放平台，依据标准地图）；台湾城市与岛屿坐标：Wikidata",
        homepage="https://datav.aliyun.com/portal/school/atlas/area_selector",
        license="DataV.GeoAtlas 免费公开数据，仅供学习研究；Wikidata 坐标 CC0",
        description="省级（34 个，含台湾省、香港、澳门）、地级与县级行政区划边界，国界轮廓（含藏南、阿克赛钦、"
        "南海诸岛）与南海断续线（十段线）。坐标为 GCJ-02 经纬度（与 WGS-84 相差不足 1 公里，远小于 25 公里的六边形，"
        "未作转换）。行政中心坐标取 DataV 的 center 字段（政府驻地）。台湾省在 DataV 中无下级区划：台湾的六座主要城市以 "
        "Wikidata 坐标标注为点，编号为本项目自定（非官方代码）。钓鱼岛、赤尾屿及南海诸岛各群岛的标注点坐标亦取自 Wikidata。",
        used_for=["国界、省界与六边形归属", "地级市范围（城市占地六边形）与县级查找", "城市行政中心位置",
                  "南海诸岛插图与断续线", "钓鱼岛、赤尾屿标注"],
    )

    # --- fetch ---------------------------------------------------------------------------------------------

    def _get(self, adcode: str, full: bool) -> dict | None:
        name = f"{adcode}_full" if full else str(adcode)
        try:
            path = self.download(f"{BASE}/{name}.json", f"datav/{name}.json", force=getattr(self, "_force", False))
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):  # DataV answers 403/404 for units without children (东莞, 中山, 儋州 …)
                exc.close()
                print(f"    - {name}.json not published (HTTP {exc.code}); using the parent's polygon")
                return None
            raise
        return json.loads(path.read_text(encoding="utf-8"))

    def _wikidata(self, qid: str) -> list[float] | None:
        rel = f"wikidata/{qid}_P625.json"
        if not (self.raw_dir / rel).exists() and not self.offline:
            time.sleep(1.5)  # Wikidata answers 429 to quick bursts
        try:
            path = self.download(WIKIDATA + qid, f"wikidata/{qid}_P625.json", force=getattr(self, "_force", False))
        except (urllib.error.URLError, fetch.OfflineError) as exc:
            print(f"    ! Wikidata {qid}: {exc}")
            return None
        claims = json.loads(path.read_text(encoding="utf-8")).get("claims", {}).get("P625", [])
        for c in claims:
            v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
            if v and "latitude" in v:
                return [round(v["longitude"], 5), round(v["latitude"], 5)]
        return None

    def _provinces_full(self) -> dict:
        return self._get("100000", full=True)

    def _plan(self):
        """(province features, {province adcode: its _full or None}, {city adcode: its _full or None})."""
        top = self._provinces_full()
        provs = [f for f in top["features"] if f["properties"].get("adcode") and f["properties"].get("level") == "province"]
        prov_full, city_full = {}, {}
        for f in provs:
            p = f["properties"]
            ad = str(p["adcode"])
            prov_full[ad] = self._get(ad, full=True) if p.get("childrenNum") else None
            if prov_full[ad] is None or province_code(ad) in MUNICIPALITIES | SARS:
                continue  # municipality / SAR children are districts: no deeper level needed
            for cf in prov_full[ad]["features"]:
                cp = cf["properties"]
                cad = str(cp.get("adcode"))
                if cp.get("childrenNum") and not is_county_city(cad):
                    city_full[cad] = self._get(cad, full=True)
        return top, provs, prov_full, city_full

    def fetch(self, force=False):
        self._force = force
        self._get("100000", full=False)
        _, provs, prov_full, city_full = self._plan()
        print(f"    {len(provs)} provinces, {sum(v is not None for v in prov_full.values())} province files, "
              f"{sum(v is not None for v in city_full.values())} prefecture files")
        for _, _, qid in TAIWAN_CITIES:
            self._wikidata(qid)
        for _, _, _, qid in ISLANDS:
            self._wikidata(qid)
        self._force = False

    # --- process -------------------------------------------------------------------------------------------

    def process(self):
        top, provs, prov_full, city_full = self._plan()
        files = ["datav/100000_full.json", "datav/100000.json"]
        files += [f"datav/{ad}_full.json" for ad, d in prov_full.items() if d is not None]
        files += [f"datav/{ad}_full.json" for ad, d in city_full.items() if d is not None]

        outline = self._get("100000", full=False)
        country = {"name": "中华人民共和国", "rings": [r for f in outline["features"] for r in _rings(f["geometry"], TOL_PROVINCE)]}

        provinces, prefectures, counties = [], [], []
        for f in sorted(provs, key=lambda f: str(f["properties"]["adcode"])):
            p = f["properties"]
            ad = str(p["adcode"])
            code = province_code(ad)
            full, short, abbr, kind = PROVINCES[code]
            if p.get("name") != full:
                print(f"    ! province {ad}: DataV name {p.get('name')!r} differs from the table's {full!r}")
            rings = _rings(f["geometry"], TOL_PROVINCE)
            provinces.append({"code": code, "adcode": ad, "name": full, "short": short, "abbr": abbr, "kind": kind,
                              "seat": _pt(p.get("center")), "centroid": _pt(p.get("centroid")), "rings": rings})
            children = prov_full.get(ad)
            if code in MUNICIPALITIES | SARS:
                # The whole municipality / SAR is one prefecture-level city; its districts are its counties.
                pre = {"adcode": ad, "name": full, "short": short, "province": code, "kind": prefecture_kind(ad, full),
                       "seat": _pt(p.get("center")), "rings": rings, "counties": []}
                for cf in (children or {"features": []})["features"]:
                    cp = cf["properties"]
                    if not cp.get("adcode"):
                        continue
                    cad = str(cp["adcode"])
                    counties.append({"adcode": cad, "name": cp["name"], "prefecture": ad, "province": code,
                                     "seat": _pt(cp.get("center")), "rings": _rings(cf["geometry"], TOL_LOCAL)})
                    pre["counties"].append(cad)
                prefectures.append(pre)
                continue
            if code == TAIWAN:
                continue  # no subdivisions in DataV; Taiwan's cities are added as points below
            for cf in (children or {"features": []})["features"]:
                cp = cf["properties"]
                if not cp.get("adcode"):
                    continue
                cad, cname = str(cp["adcode"]), cp["name"]
                pk = prefecture_kind(cad, cname, cp.get("level"))
                pre = {"adcode": cad, "name": cname, "short": short_name(cname), "province": code, "kind": pk,
                       "seat": _pt(cp.get("center")), "rings": _rings(cf["geometry"], TOL_LOCAL), "counties": []}
                sub = city_full.get(cad)
                if sub is None:
                    # No county level (东莞, 中山, 嘉峪关, 儋州, the 省直辖县级 units): the unit is its own county.
                    counties.append({"adcode": cad, "name": cname, "prefecture": cad, "province": code,
                                     "seat": pre["seat"], "rings": pre["rings"], "whole_prefecture": True})
                    pre["counties"].append(cad)
                else:
                    for kf in sub["features"]:
                        kp = kf["properties"]
                        if not kp.get("adcode"):
                            continue
                        kad = str(kp["adcode"])
                        counties.append({"adcode": kad, "name": kp["name"], "prefecture": cad, "province": code,
                                         "seat": _pt(kp.get("center")), "rings": _rings(kf["geometry"], TOL_LOCAL)})
                        pre["counties"].append(kad)
                prefectures.append(pre)

        missing = []
        for cad, name, qid in TAIWAN_CITIES:
            seat = self._wikidata(qid)
            if seat is None:
                missing.append(name)
            prefectures.append({"adcode": cad, "name": name, "short": short_name(name), "province": TAIWAN,
                                "kind": "taiwan_city", "seat": seat, "rings": [], "counties": [],
                                "id_assigned": True, "wikidata": qid})
        files += [f"wikidata/{qid}_P625.json" for *_, qid in TAIWAN_CITIES]

        islands = []
        for iid, name, kind, qid in ISLANDS:
            pt = self._wikidata(qid)
            if pt is None:
                missing.append(name)
            islands.append({"id": iid, "name": name, "kind": kind, "point": pt, "wikidata": qid})
        files += [f"wikidata/{qid}_P625.json" for *_, qid in ISLANDS]
        if missing:
            print(f"    ! no Wikidata coordinates for: {', '.join(missing)} (point left null)")

        dash_line = []
        jd = [f for f in top["features"] if f["properties"].get("adchar") == "JD"]
        for f in jd:
            for k, ring in enumerate(geojson_rings(f["geometry"]), 1):
                pts = [[round(x, DIGITS), round(y, DIGITS)] for x, y in ring]
                # The dash is a thin polygon; its two farthest-apart vertices are the dash's ends.
                a, b = max(((p, q) for i, p in enumerate(pts) for q in pts[i + 1:]),
                           key=lambda pq: (pq[0][0] - pq[1][0]) ** 2 + (pq[0][1] - pq[1][1]) ** 2)
                if a[1] < b[1]:
                    a, b = b, a  # north end first
                dash_line.append({"seq": k, "rings": [pts], "line": [a, b]})

        banner = [p for p in prefectures if p["kind"] not in ("county_city",)]
        kinds = {}
        for p in prefectures:
            kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
        print(f"    {len(provinces)} provinces, {len(prefectures)} prefecture-level units {kinds}, "
              f"{len(counties)} county-level units, {len(dash_line)} dashes, {len(islands)} island labels")
        print(f"    {len(banner)} city banners (prefecture-level + municipalities + SARs + Taiwan cities)")
        no_seat = [p["name"] for p in prefectures if not p["seat"]]
        if no_seat:
            print(f"    ! no seat point: {', '.join(no_seat)}")
        return {
            "vintage": "DataV.GeoAtlas areas_v3 (" + self._retrieved() + ")",
            "_files": files,
            "crs": "GCJ-02 lon/lat (as published by DataV/AutoNavi; ≤ ~0.6 km from WGS-84)",
            "country": country,
            "provinces": provinces,
            "prefectures": prefectures,
            "counties": counties,
            "dash_line": dash_line,
            "islands": islands,
        }

    def _retrieved(self) -> str:
        m = fetch.read_manifest(self.id).get("datav/100000_full.json", {})
        return (m.get("retrieved") or "")[:10] or "unknown date"

