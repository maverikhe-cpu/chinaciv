"""The map itself: hex grid, land/water, relief, land cover, provinces, counties, rivers, the 南海诸岛 inset.

China edition (docs/CN_PLAN.md §2, §5.1). The frame and projection are fixed in core/geo.py (Albers 25°/47°N,
105°E; 5471 × 4455 map km, 25 km hexes → 128 × 120 cells). National, provincial and county boundaries come only
from cn_boundaries (DataV GeoAtlas, following the standard map); Natural Earth supplies neighbours (China point of
view), lakes and rivers. ETOPO1 gives elevation, cn_landcover (MODIS MCD12Q1 IGBP via NASA GIBS) the land cover.

Also saves interim "_world" (grid + projection + per-cell province/county/water/cover + county → prefecture) so
the other builders (and build/locate.py) can drop their data onto the same hexes.
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path

from ..core import china
from ..core.geo import MAP_HEIGHT_KM, MAP_WIDTH_KM, InsetProjection, Shape, ShapeIndex, china_projection
from ..core.hexgrid import SQRT3, HexGrid
from ..core.store import load_interim, save_interim, write_web

REQUIRES = ["cn_boundaries", "natural_earth", "etopo", "cn_landcover"]
PRODUCES = ["_world", "world.json"]

VERSION = 3
HEX_RADIUS_KM = 25.0

# Cell owner codes (besides province index >= 0)
WATER, FOREIGN = -1, -2

SAR_CITIES = ("810000", "820000")  # 香港, 澳门: their seat hexes are forced to the SAR (CN_PLAN §5.1)

NOTICE = {
    "text": "本地图仅供个人学习，未申请审图号。",
    "boundary": "国界、省界及南海断续线依据阿里云 DataV.GeoAtlas 行政区划数据（源自高德开放平台，依据标准地图）绘制；"
    "邻国取自 Natural Earth（中国视角版）。",
    "sources": ["cn_boundaries", "natural_earth"],
}

# --- tables (written to world.json so the frontend and Almanac read one definition) ---------

LAND, OCEAN, COAST, LAKE = range(4)
TIDAL_SEA_SHARE = 0.5  # share of ETOPO samples below 0 m that keeps a sea-centred hex as water
COAST_DEPTH_M = -200
WATER_TABLE = [
    {"id": LAND, "name": "陆地", "rule": "六边形的 7 个采样点（中心 + 6 个）中至少 4 个落在中国省级行政区（DataV）或邻国（Natural Earth）轮廓内；"
     "但若中心点在海上且 ETOPO1 平均高程不高于海平面，或至少一半 ETOPO1 采样点低于海平面，则仍为水域（如杭州湾等潮汐海湾）。"
     "香港、澳门的政府驻地所在六边形强制为陆地。"},
    {"id": OCEAN, "name": "远海", "color": "#1b4266", "rule": "深于 200 米且不与陆地相邻的海域。"},
    {"id": COAST, "name": "近海", "color": "#2d6d97", "rule": "与陆地六边形相邻，或 ETOPO1 平均水深浅于 200 米的海域。"},
    {"id": LAKE, "name": "湖泊", "color": "#3a80b0", "rule": "水域六边形（多数采样点不在陆地上）中 Natural Earth 1:10m 湖泊采样点多于海洋采样点，或平均高程高于海平面。"},
]

WATER_NOTE = (
    "中国的省级轮廓（DataV.GeoAtlas）与 Natural Earth 邻国轮廓并不完全吻合，二者重叠处以中国省界为准（先判省界）。"
    "ETOPO1 平均高程高于海平面（且不到一半采样点低于海平面）的海洋六边形，若至少 4 个相邻六边形为陆地，或同时与中国和邻国陆地相邻，"
    "则改为陆地，归属相邻陆地中的多数。不足半个六边形宽的水库算作陆地，河流从中穿过。中心点在海上且 ETOPO1 平均高程不高于海平面"
    "或至少一半采样点低于海平面的潮汐海湾保持为水域。海洋六边形的高程为其低于海平面的 ETOPO1 采样点平均值（即水深）。"
)

FLAT, HILLS, MOUNTAINS, PEAKS = range(4)
LAND_MIN_ALT_M = -100  # ETOPO samples below this inside a land hex are treated as sea floor
RELIEF_TABLE = [
    {"id": FLAT, "name": "平原", "rule": "六边形内高程标准差小于 85 米。"},
    {"id": HILLS, "name": "丘陵", "rule": "高程标准差 ≥ 85 米。"},
    {"id": MOUNTAINS, "name": "山地", "rule": "高程标准差 ≥ 260 米，或平均高程 ≥ 2300 米。"},
    {"id": PEAKS, "name": "高山", "rule": "平均高程 ≥ 2700 米（高原与高山）。"},
]
RELIEF_NOTE = (
    "取自 ETOPO1 采样（间距约 9 千米，每个六边形约 20 个），只用高程 ≥ −100 米的采样点，海湾和海底不计；若无合格采样点则全部使用。"
    "若六边形较低的 60% 采样点是平坦低地（相差不到 60 米且低于 400 米），起伏降低一级（谷底）。水域六边形一律为平原。"
)

URBAN_MIN_PCT = 20
# id, key, name, colour, cn_landcover groups, rule. Ids and keys are fixed by docs/DATA_CONTRACTS.md.
COVER = [
    (1, "forest_deciduous", "落叶林", "#4f7a35", ["forest_deciduous"], "森林（三类合计）为最大类，且落叶林（落叶针叶林、落叶阔叶林）为最大的森林类型。"),
    (2, "forest_evergreen", "常绿林", "#2f5a3a", ["forest_evergreen"], "森林为最大类，且常绿林（常绿针叶林、常绿阔叶林）为最大的森林类型。"),
    (3, "forest_mixed", "混交林", "#43703f", ["forest_mixed"], "森林为最大类，且混交林（含多树草原，树木覆盖 30–60%）为最大的森林类型。"),
    (4, "scrub", "灌丛与荒漠草原", "#c9b07a", ["scrub"], "灌丛（郁闭灌丛、稀疏灌丛）为最大类。"),
    (5, "grassland", "草原", "#a9b35a", ["grassland"], "草地与稀树草原为最大类。"),
    (6, "pasture", "农林镶嵌", "#8fb04e", ["pasture"], "耕地与自然植被镶嵌（小块农田夹杂林草）为最大类。"),
    (7, "cropland", "耕地", "#c2b84e", ["cropland"], "耕地为最大类。"),
    (8, "wetland", "湿地", "#4f8a6e", ["wetland"], "永久湿地为最大类。"),
    (9, "urban", "城镇", "#8a8378", ["developed"], f"城市建设用地占六边形陆地采样点的 ≥ {URBAN_MIN_PCT}%（无论其余为何）。"
     "MODIS 500 米城市像元偏少，故阈值低于美国版的 30%。"),
    (10, "barren", "裸地与沙漠", "#bfae95", ["barren"], "裸地（岩石、沙漠、戈壁）为最大类。"),
    (11, "ice", "冰雪", "#e8eef2", ["ice"], "永久冰雪为最大类。"),
]
COVER_NOTE = (
    "取自 MODIS MCD12Q1 v061 IGBP 土地覆盖 2024 年（经 NASA GIBS 以 0.02° 约 2 千米像元提供），在每个陆地六边形内按 2 千米格点采样"
    "（约 470 个点）。水体与无数据像元不计；份额按其余陆地点计算并取整到百分比，城镇阈值按取整后的城市建设用地份额判断。"
    "邻国陆地也有覆盖类型（雾化显示），但份额（pct）只给中国陆地六边形。"
)
# Groups competing for the dominant cover (urban is decided by its threshold first). Dict order breaks ties.
GROUPS = {
    "forest": ("forest_deciduous", "forest_evergreen", "forest_mixed"), "scrub": ("scrub",), "grassland": ("grassland",),
    "pasture": ("pasture",), "cropland": ("cropland",), "wetland": ("wetland",), "barren": ("barren",), "ice": ("ice",),
}
FOREST_ID = {"forest_deciduous": 1, "forest_evergreen": 2, "forest_mixed": 3}
GROUP_ID = {"scrub": 4, "grassland": 5, "pasture": 6, "cropland": 7, "wetland": 8, "barren": 10, "ice": 11}
URBAN_ID = 9
PCT = {
    "forest": ("forest_deciduous", "forest_evergreen", "forest_mixed"), "crop": ("cropland",), "pasture": ("pasture",),
    "grass": ("grassland",), "shrub": ("scrub",), "wetland": ("wetland",), "developed": ("developed",),
}
NOT_LAND = ("water", "nodata")
COVER_STEP_KM = 2.0

# National boundary symbology (standard-map legend: 国界 and 未定国界). Every land edge between a Chinese hex and a
# foreign hex is 国界, except the sections listed here, drawn as 未定国界: the boundary with the Kashmir region, whose
# status is undetermined (the 1963 China–Pakistan boundary agreement is provisional pending the settlement of the
# Kashmir dispute). A foreign hex belongs to a section when its country (Natural Earth a3) is listed and its centre
# lies in the lon/lat box. Extend this table to re-class other sections after checking the current standard-map
# sample (bzdt.ch.mnr.gov.cn).
UNDEFINED_BOUNDARY = [
    {"id": "kashmir", "name": "克什米尔地区", "a3": ["IND", "PAK"], "bbox": [72.0, 32.0, 80.6, 37.6]},
]
BOUNDARY_NOTE = (
    "陆地国界按标准地图图例分为“国界”与“未定国界”两种符号：与克什米尔地区相邻的边界（其归属未定，1963 年中巴边界协定为临时协定）"
    "画作未定国界，其余陆地国界画作国界。线位为 DataV 国界在六边形网格上的近似。"
)

RIVER_MAX_RANK = 8
INSET_THIN_KM = 1.2  # inset vertex spacing (map km; × 1/0.3 ≈ 4 km on the ground)


def classify_relief(mean: float, std: float) -> int:
    if mean >= 2700:
        return PEAKS
    if std >= 260 or mean >= 2300:
        return MOUNTAINS
    if std >= 85:
        return HILLS
    return FLAT


def is_valley_floor(samples: list) -> bool:
    """True when the lower 60% of a hex's land samples is flat lowland (within 60 m, under 400 m): the hex is mostly
    valley floor even if a range at its edge makes the whole hex's spread look hilly or mountainous."""
    if len(samples) < 6:
        return False
    v = sorted(samples)
    lo = v[: max(3, int(len(v) * 0.6))]
    return lo[-1] < 400 and lo[-1] - lo[0] < 60


def dominant_cover(counts: Counter, land: int) -> int:
    """Cover id for a hex from its land-cover group counts (land = points that are not water or no-data)."""
    if not land:
        return 0
    if round(100 * counts["developed"] / land) >= URBAN_MIN_PCT:  # same rounding as pct.developed
        return URBAN_ID
    best, best_n = None, 0
    for g, members in GROUPS.items():
        n = sum(counts[m] for m in members)
        if n > best_n:
            best, best_n = g, n
    if best is None:
        return 0  # only developed land below the urban threshold
    if best == "forest":
        return FOREST_ID[max(GROUPS["forest"], key=lambda m: counts[m])]
    return GROUP_ID[best]


def simplify(points: list[tuple[float, float]], min_km: float) -> list[tuple[float, float]]:
    out = [points[0]]
    for p in points[1:-1]:
        if math.dist(p, out[-1]) >= min_km:
            out.append(p)
    out.append(points[-1])
    return out


def clip_segment(p, q, w, h):
    """Liang–Barsky: the part of segment p→q inside [0,w]×[0,h], or None."""
    (x0, y0), (x1, y1) = p, q
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for pk, qk in ((-dx, x0), (dx, w - x0), (-dy, y0), (dy, h - y0)):
        if pk == 0:
            if qk < 0:
                return None
            continue
        t = qk / pk
        if pk < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return None
    return (x0 + t0 * dx, y0 + t0 * dy), (x0 + t1 * dx, y0 + t1 * dy)


def clip_polyline(pts, w, h) -> list[list[tuple[float, float]]]:
    """Split a polyline into the pieces that lie inside the map rectangle."""
    pieces, cur = [], []
    for p, q in zip(pts, pts[1:]):
        seg = clip_segment(p, q, w, h)
        if seg is None:
            if len(cur) >= 2:
                pieces.append(cur)
            cur = []
            continue
        a, b = seg
        if cur and math.dist(cur[-1], a) < 1e-9:
            cur.append(b)
        else:
            if len(cur) >= 2:
                pieces.append(cur)
            cur = [a, b]
        if b != q:  # left the map inside this segment
            pieces.append(cur)
            cur = []
    if len(cur) >= 2:
        pieces.append(cur)
    return pieces


def land_runs(pts, is_land, step: float = 2.0) -> list[list[tuple[float, float]]]:
    """Split a projected polyline (densified every `step` km) into the runs whose points satisfy is_land."""
    runs, cur = [], []
    for k, (p, q) in enumerate(zip(pts, pts[1:])):
        n = max(1, math.ceil(math.dist(p, q) / step))
        for j in range(0 if k == 0 else 1, n + 1):
            t = j / n
            pt = (p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1]))
            if is_land(pt):
                cur.append(pt)
            else:
                if len(cur) >= 2:
                    runs.append(cur)
                cur = []
    if len(cur) >= 2:
        runs.append(cur)
    return runs


def join_lines(lines: list[list[tuple[float, float]]], tol: float = 0.5) -> list[list[tuple[float, float]]]:
    """Greedily join polylines whose ends meet (within `tol` km), reversing pieces as needed."""
    lines = [list(l) for l in lines]
    joined = True
    while joined:
        joined = False
        for a in range(len(lines)):
            for b in range(len(lines)):
                if a == b or not lines[a] or not lines[b]:
                    continue
                la, lb = lines[a], lines[b]
                for la2, lb2 in ((la, lb), (la, lb[::-1]), (la[::-1], lb), (la[::-1], lb[::-1])):
                    if math.dist(la2[-1], lb2[0]) <= tol:
                        lines[a] = la2 + lb2[1:]
                        lines[b] = []
                        joined = True
                        break
        lines = [l for l in lines if l]
    return lines


def hex_lattice(radius: float, step: float) -> list[tuple[float, float]]:
    """Offsets of a square lattice (spacing `step`) that fall inside a pointy-top hex."""
    half_w = SQRT3 / 2 * radius
    n = int(radius // step) + 1
    out = []
    for iy in range(-n, n + 1):
        for ix in range(-n, n + 1):
            x, y = (ix + 0.5) * step, (iy + 0.5) * step
            if abs(x) <= half_w and abs(y) <= radius - abs(x) / SQRT3:
                out.append((x, y))
    return out


def majority(values: list, centre) -> object:
    """Most common value; ties go to `centre` if it is among the tied, else to the first seen."""
    counts = Counter(values)
    top = max(counts.values())
    tied = [v for v in counts if counts[v] == top]
    return centre if centre in tied else tied[0]


# --- polygon / line helpers for the inset and the dashed line -----------------------------------------------


def clip_ring_rect(ring: list, x0: float, y0: float, x1: float, y1: float) -> list:
    """Sutherland–Hodgman clip of a closed ring to an axis-aligned rectangle (lon/lat or km). Returns an open ring
    (no repeated last point); [] when nothing is left. Edges along the rectangle are fine for filled drawing."""
    pts = [tuple(p) for p in ring]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    for axis, bound, keep_le in ((0, x0, False), (0, x1, True), (1, y0, False), (1, y1, True)):
        if not pts:
            return []
        out = []
        for k, cur in enumerate(pts):
            prev = pts[k - 1]
            cin = cur[axis] <= bound if keep_le else cur[axis] >= bound
            pin = prev[axis] <= bound if keep_le else prev[axis] >= bound
            if cin != pin:
                t = (bound - prev[axis]) / (cur[axis] - prev[axis])
                out.append((prev[0] + t * (cur[0] - prev[0]), prev[1] + t * (cur[1] - prev[1])))
            if cin:
                out.append(cur)
        pts = out
    return pts if len(pts) >= 3 else []


def thin_points(pts: list, min_dist: float) -> list:
    """Drop points closer than min_dist to the last kept one (open ring or polyline)."""
    if not pts:
        return []
    out = [pts[0]]
    for p in pts[1:]:
        if math.dist(p, out[-1]) >= min_dist:
            out.append(p)
    return out


def flat(pts, nd: int = 1) -> list:
    return [round(v, nd) for p in pts for v in p]


def dash_centerline(dash: dict, n: int = 16) -> list[tuple[float, float]]:
    """Centre line (lon/lat) of one DataV dash. The dash is a thin closed band; its ring is split at the points
    nearest the two published line ends into two sides, both resampled by arc length and averaged. Falls back to the
    2-point line when the ring is missing."""
    line = [tuple(p) for p in dash.get("line") or []]
    rings = dash.get("rings") or []
    if not rings or len(rings[0]) < 6 or len(line) != 2:
        return line
    ring = [tuple(p) for p in rings[0]]
    if ring[0] == ring[-1]:
        ring = ring[:-1]
    ia = min(range(len(ring)), key=lambda k: math.dist(ring[k], line[0]))
    ib = min(range(len(ring)), key=lambda k: math.dist(ring[k], line[1]))
    if ia == ib:
        return line
    rot = ring[ia:] + ring[:ia]
    jb = (ib - ia) % len(ring)
    side1 = rot[: jb + 1]  # from the north end to the south end
    side2 = (rot[jb:] + rot[:1])[::-1]  # also north → south

    def resample(chain):
        d = [0.0]
        for p, q in zip(chain, chain[1:]):
            d.append(d[-1] + math.dist(p, q))
        total = d[-1] or 1.0
        out, j = [], 0
        for k in range(n + 1):
            t = total * k / n
            while j < len(chain) - 2 and d[j + 1] < t:
                j += 1
            seg = d[j + 1] - d[j] or 1.0
            u = min(1.0, max(0.0, (t - d[j]) / seg))
            out.append((chain[j][0] + u * (chain[j + 1][0] - chain[j][0]), chain[j][1] + u * (chain[j + 1][1] - chain[j][1])))
        return out

    a, b = resample(side1), resample(side2)
    return [((p[0] + q[0]) / 2, (p[1] + q[1]) / 2) for p, q in zip(a, b)]


def build_inset(bounds: dict, ne: dict, proj) -> dict:
    """The 南海诸岛 inset (CN_PLAN §2.2): vector land, all ten dashes, island and 三沙市 labels, in map km."""
    ins = InsetProjection(proj)
    lon0, lat0, lon1, lat1 = ins.region

    def overlaps(rings):
        for r in rings:
            xs, ys = [p[0] for p in r], [p[1] for p in r]
            if max(xs) >= lon0 and min(xs) <= lon1 and max(ys) >= lat0 and min(ys) <= lat1:
                return True
        return False

    def project_rings(rings, keep_small: bool):
        out = []
        for r in rings:
            clipped = clip_ring_rect(r, lon0, lat0, lon1, lat1)
            if not clipped:
                continue
            pts = [ins(lon, lat) for lon, lat in clipped]
            thin = thin_points(pts, INSET_THIN_KM)
            if len(thin) < 3:
                if not keep_small:
                    continue
                thin = thin_points(pts, 0.05)  # tiny Chinese islands (西沙, 南沙 reefs) stay as specks
                if len(thin) < 3:
                    thin = pts[:3] if len(pts) >= 3 else []
                if len(thin) < 3:
                    continue
            out.append(flat(thin))
        return out

    land = []
    for p in bounds["provinces"]:
        if overlaps(p["rings"]):
            for pts in project_rings(p["rings"], keep_small=True):
                land.append({"kind": "china", "code": p["code"], "pts": pts})
    for c in ne["countries"]:
        if overlaps(c["rings"]):
            for pts in project_rings(c["rings"], keep_small=False):
                land.append({"kind": "foreign", "name": c.get("name_zh") or c["name"], "pts": pts})

    dashes = []
    for d in sorted(bounds["dash_line"], key=lambda d: d["seq"]):
        line = dash_centerline(d)
        dashes.append(flat([ins(lon, lat) for lon, lat in line]))

    islands = []
    for isl in bounds["islands"]:
        if isl.get("point") and ins.contains_lonlat(*isl["point"]):
            x, y = ins(*isl["point"])
            islands.append({"id": isl["id"], "name": isl["name"], "kind": isl["kind"], "x": round(x, 1), "y": round(y, 1)})
    labels = []
    sansha = next((p for p in bounds["prefectures"] if p["adcode"] == "460300"), None)
    if sansha and sansha.get("seat"):
        x, y = ins(*sansha["seat"])
        labels.append({"name": sansha["name"], "x": round(x, 1), "y": round(y, 1), "kind": "city", "id": "460300"})
    return {"title": "南海诸岛", **ins.to_dict(), "land": land, "dashes": dashes, "islands": islands, "labels": labels}


def build() -> None:
    bounds = load_interim("cn_boundaries")
    ne = load_interim("natural_earth")
    etopo = load_interim("etopo")
    lc = load_interim("cn_landcover")

    provinces = sorted(bounds["provinces"], key=lambda p: p["code"])
    codes = [p["code"] for p in provinces]
    pidx = {c: k for k, c in enumerate(codes)}
    counties = sorted(bounds["counties"], key=lambda c: c["adcode"])
    county_ids = [c["adcode"] for c in counties]
    cidx = {c: k for k, c in enumerate(county_ids)}
    county_prov = [pidx.get(c["province"], -1) for c in counties]
    county_pref = [c["adcode"] if china.is_county_city(c["adcode"]) else c["prefecture"] for c in counties]

    proj = china_projection()
    width, height = MAP_WIDTH_KM, MAP_HEIGHT_KM
    grid = HexGrid.covering(width, height, HEX_RADIUS_KM)
    print(f"    grid {grid.cols}×{grid.rows} = {grid.size} hexes of {HEX_RADIUS_KM:g} km")

    def shape(rings):
        return Shape([[proj(lon, lat) for lon, lat in r] for r in rings])

    prov_idx = ShapeIndex([shape(p["rings"]) for p in provinces])
    county_idx = ShapeIndex([shape(c["rings"]) for c in counties], cell_km=60)
    foreign_idx = ShapeIndex([shape(c["rings"]) for c in ne["countries"]], cell_km=200)
    lake_shapes = [shape(l["rings"]) for l in ne["lakes"]]
    lake_idx = ShapeIndex(lake_shapes)

    # --- elevation statistics per hex (land samples and all samples) -----------------------------
    stats_all = [[0, 0.0, 0.0] for _ in range(grid.size)]
    stats_land = [[0, 0.0, 0.0] for _ in range(grid.size)]
    samples_land = [[] for _ in range(grid.size)]  # land altitudes, for the peak height and the valley-floor test
    below_sea = [0] * grid.size  # ETOPO samples below 0 m ...
    below_sum = [0.0] * grid.size  # ... and their altitude sum
    for lat, lon, alt in zip(etopo["lat"], etopo["lon"], etopo["alt"]):
        i = grid.cell_at(*proj(lon, lat))
        if i < 0:
            continue
        if alt < 0:
            below_sea[i] += 1
            below_sum[i] += alt
        for st in (stats_all[i], stats_land[i]) if alt >= LAND_MIN_ALT_M else (stats_all[i],):
            st[0] += 1
            st[1] += alt
            st[2] += alt * alt
        if alt >= LAND_MIN_ALT_M:
            samples_land[i].append(alt)

    def sea_share(i):
        return below_sea[i] / stats_all[i][0] if stats_all[i][0] else 0.0

    def mean_std(st):
        n, s1, s2 = st
        if not n:
            return None, None
        m = s1 / n
        return m, math.sqrt(max(0.0, s2 / n - m * m))

    # --- ownership: majority of 7 sample points --------------------------------------------------
    # The centre plus six points 60% of the way to each corner. Each point is lake, a province (tested before any
    # Natural Earth country, so the DataV outline wins where they overlap), foreign land or other water; the
    # majority decides (the centre breaks ties). All seven also give an area-weighted county mix.
    offsets = [(0.0, 0.0)] + [
        (0.6 * HEX_RADIUS_KM * math.cos(math.radians(60 * k - 30)), 0.6 * HEX_RADIUS_KM * math.sin(math.radians(60 * k - 30)))
        for k in range(6)
    ]
    owner = [WATER] * grid.size
    water = [OCEAN] * grid.size
    for i in range(grid.size):
        cx, cy = grid.center(*grid.col_row(i))
        kinds = []
        for dx, dy in offsets:
            x, y = cx + dx, cy + dy
            if lake_idx.locate(x, y) >= 0:
                kinds.append("lake")
            elif (s := prov_idx.locate(x, y)) >= 0:
                kinds.append(s)
            elif foreign_idx.locate(x, y) >= 0:
                kinds.append(FOREIGN)
            else:
                kinds.append("sea")
        land = [k for k in kinds if not isinstance(k, str)]
        m_all, _ = mean_std(stats_all[i])
        # Tidal bays (杭州湾, 胶州湾) are narrower than half a hex; keep them water when the centre is sea and the
        # hex is mostly at or below sea level in ETOPO1.
        tidal = kinds[0] == "sea" and m_all is not None and (m_all <= 0 or sea_share(i) >= TIDAL_SEA_SHARE)
        if len(land) * 2 > len(kinds) and not tidal:
            cn = [k for k in land if k >= 0]
            n_foreign = len(land) - len(cn)
            if len(cn) > n_foreign or (len(cn) == n_foreign and kinds[0] != FOREIGN):
                owner[i] = majority(cn, kinds[0])
            else:
                owner[i] = FOREIGN
            water[i] = LAND
        else:
            wet = [k for k in kinds if isinstance(k, str)]
            # Points in neither the lake nor the land outlines are usually sea, but next to a lake they are
            # mostly gaps between the two datasets' shorelines, so a lake hex needs only to be above sea level.
            above_sea = m_all is not None and m_all > 0
            lakeish = "lake" in wet and (above_sea or majority(wet, kinds[0] if kinds[0] in wet else "lake") == "lake")
            water[i] = LAKE if lakeish else OCEAN

    # Sliver fix: DataV and Natural Earth outlines don't meet exactly, leaving water hexes on land along the
    # land borders. A non-lake water hex whose ETOPO mean is above sea level becomes land if at least 4 of its
    # neighbours are land, or if it touches both Chinese and foreign land. It takes its land neighbours' majority.
    slivers = []
    for i in range(grid.size):
        if water[i] != OCEAN:
            continue
        m, _ = mean_std(stats_all[i])
        land_nb = [owner[j] for j in grid.neighbours(i) if water[j] == LAND]
        border = FOREIGN in land_nb and any(o >= 0 for o in land_nb)
        if m is not None and m > 0 and sea_share(i) < TIDAL_SEA_SHARE and (len(land_nb) >= 4 or border):
            slivers.append((i, Counter(land_nb).most_common(1)[0][0]))
    for i, o in slivers:
        owner[i], water[i] = o, LAND
    print(f"    {len(slivers)} sliver water hexes turned into land")

    # Forced rule 1 (CN_PLAN §5.1): the hexes holding the seats of 香港 and 澳门 belong to the SAR.
    pref_by_id = {p["adcode"]: p for p in bounds["prefectures"]}
    forced: dict[int, str] = {}  # cell → SAR prefecture adcode
    for sar in SAR_CITIES:
        seat = (pref_by_id.get(sar) or {}).get("seat")
        if not seat:
            print(f"    WARNING: no seat for {sar}; its hex is not forced")
            continue
        sx, sy = proj(*seat)
        cell = grid.cell_at(sx, sy)
        if cell in forced:  # both seats in one hex: the later SAR (澳门) takes the nearest free neighbour
            free = [j for j in grid.neighbours(cell) if j not in forced and (water[j] == LAND or any(water[k] == LAND for k in grid.neighbours(j)))]
            if not free:
                print(f"    WARNING: no free neighbour for {sar}")
                continue
            cell = min(free, key=lambda j: math.dist(grid.center(*grid.col_row(j)), (sx, sy)))
        forced[cell] = sar
        was = (owner[cell], water[cell])
        owner[cell], water[cell] = pidx[sar[:2]], LAND
        print(f"    {sar} seat hex {cell} forced to {sar[:2]} (was owner {was[0]}, water {was[1]})")

    # --- counties for Chinese cells ----------------------------------------------------------------
    county = [-1] * grid.size
    county_samples: list[list[int]] = [[] for _ in range(grid.size)]
    for i in range(grid.size):
        if owner[i] >= 0:
            cx, cy = grid.center(*grid.col_row(i))
            county_samples[i] = [county_idx.locate(cx + dx, cy + dy) for dx, dy in offsets]
    taiwan = pidx.get(china.TAIWAN)
    # Hexes whose seven points all miss the county polygons (e.g. reclaimed coast) borrow the most common county of
    # their same-province neighbours for the centre sample. Taiwan has no counties in DataV: its cells stay -1.
    borrowed = 0
    for i in range(grid.size):
        smp = county_samples[i]
        if smp and max(smp) < 0 and owner[i] != taiwan:
            nb = [k for j in grid.neighbours(i) if owner[j] == owner[i] for k in county_samples[j] if k >= 0 and county_prov[k] == owner[i]]
            if nb:
                smp[0] = Counter(nb).most_common(1)[0][0]
                borrowed += 1
    # Forced rule 2: the SAR seat hexes get the SAR district at the seat (or the SAR district nearest to it).
    for cell, sar in forced.items():
        sx, sy = proj(*pref_by_id[sar]["seat"])
        k = county_idx.locate(sx, sy)
        if k < 0 or county_prefecture_of(county_pref, k) != sar:
            cands = [j for j, c in enumerate(counties) if county_pref[j] == sar and c.get("seat")]
            k = min(cands, key=lambda j: math.dist(proj(*counties[j]["seat"]), (sx, sy))) if cands else -1
        if k >= 0:
            county_samples[cell][0] = k
    # The hex's county must lie in its owner province (majority ownership can differ from the centre point): the
    # centre if it does, else the most common in-province sample, else any sample. county_samples keep every point.
    off_prov = 0
    for i, smp in enumerate(county_samples):
        valid = [k for k in smp if k >= 0]
        if not valid:
            continue
        same = [k for k in valid if county_prov[k] == owner[i]]
        pool = same or valid
        county[i] = smp[0] if smp[0] in pool else Counter(pool).most_common(1)[0][0]
        off_prov += not same
    no_county = sum(1 for i in range(grid.size) if owner[i] >= 0 and owner[i] != taiwan and county[i] < 0)
    print(f"    {borrowed} hexes borrowed a neighbour's county; {off_prov} have no county in their own province; "
          f"{no_county} Chinese land hexes outside Taiwan have no county")

    # --- elevation, water depth classes and relief -------------------------------------------------
    elev = [0] * grid.size
    relief = [FLAT] * grid.size
    elev_max = [0] * grid.size  # highest land sample (m): snow on high ranges whose mean is held down by valleys
    valley_floor = 0
    no_elev = 0
    for i in range(grid.size):
        if water[i] == LAND:
            m, sd = mean_std(stats_land[i])
            if m is None:
                m, sd = mean_std(stats_all[i])
                m = None if m is None else max(m, 0.0)
            if m is None:
                no_elev += 1
                continue  # elev 0, flat
            elev[i] = round(m)
            relief[i] = classify_relief(max(m, 0.0), sd)
            if relief[i] in (HILLS, MOUNTAINS) and is_valley_floor(samples_land[i]):
                relief[i] -= 1  # mostly flat lowland with a range at one edge
                valley_floor += 1
            if samples_land[i]:
                elev_max[i] = min(9000, max(0, round(max(samples_land[i]))))
        else:
            m, _ = mean_std(stats_all[i])
            if m is None:
                no_elev += 1
                elev[i] = -4000  # outside the elevation window: assume deep ocean
            elif water[i] != LAKE and below_sea[i]:
                elev[i] = round(below_sum[i] / below_sea[i])  # sea: mean depth of the samples under water
            else:
                elev[i] = min(round(m), 0)
            if water[i] == OCEAN:
                near_land = any(water[j] == LAND for j in grid.neighbours(i))
                water[i] = COAST if near_land or elev[i] > COAST_DEPTH_M else OCEAN
    print(f"    {no_elev} hexes without elevation samples; {valley_floor} valley-floor hexes eased one relief class")

    # --- land cover from cn_landcover (lon/lat raster) -----------------------------------------------
    rows, legend = lc["rows"], lc["legend"]
    groups = lc["groups"]
    group_of_char = {ch: groups.get(str(code), "nodata") for ch, code in legend.items()}
    lon0, lat0, dlon, dlat = lc["lon0"], lc["lat0"], lc["dlon"], lc["dlat"]
    ncols, nrows = lc["cols"], len(rows)
    lattice = hex_lattice(HEX_RADIUS_KM, COVER_STEP_KM)
    cover = [0] * grid.size
    pct = {k: [0] * grid.size for k in PCT}
    no_cover = 0
    for i in range(grid.size):
        if water[i] != LAND:
            continue
        cx, cy = grid.center(*grid.col_row(i))
        # Local linearisation of the inverse projection (exact to metres over a 25 km hex).
        lo_c, la_c = proj.inverse(cx, cy)
        lo_x, la_x = proj.inverse(cx + 1.0, cy)
        lo_y, la_y = proj.inverse(cx, cy + 1.0)
        a, b, c_, d = lo_x - lo_c, lo_y - lo_c, la_x - la_c, la_y - la_c
        counts: Counter = Counter()
        for ox, oy in lattice:
            lon = lo_c + a * ox + b * oy
            lat = la_c + c_ * ox + d * oy
            col, row = round((lon - lon0) / dlon), round((lat - lat0) / dlat)
            if 0 <= col < ncols and 0 <= row < nrows:
                counts[group_of_char.get(rows[row][col], "nodata")] += 1
        land = sum(n for g, n in counts.items() if g not in NOT_LAND)
        cover[i] = dominant_cover(counts, land)
        if owner[i] < 0:
            continue  # foreign land: cover (fogged) but no shares
        if not land:
            no_cover += 1
            continue
        for k, members in PCT.items():
            pct[k][i] = round(100 * sum(counts[m] for m in members) / land)
    print(f"    {len(lattice)} land-cover points per hex; {no_cover} Chinese hexes without land-cover samples; "
          f"urban hexes {sum(1 for i in range(grid.size) if owner[i] >= 0 and cover[i] == URBAN_ID)}")

    # --- rivers ----------------------------------------------------------------------------------
    # Lake centerlines are kept only where they cross land hexes (reservoirs narrower than half a hex), so rivers
    # don't stop in the middle of land; over water hexes they go.
    def on_land(pt):
        cell = grid.cell_at(*pt)
        return cell >= 0 and water[cell] == LAND

    by_river: dict[tuple, dict] = {}
    for r in ne["rivers"]:
        if r["rank"] > RIVER_MAX_RANK:
            continue
        line = [proj(lon, lat) for lon, lat in r["line"]]
        if (r.get("featurecla") or "").startswith("Lake Centerline"):
            parts = [run for p in clip_polyline(line, width, height) for run in land_runs(p, on_land)]
        else:
            parts = clip_polyline(line, width, height)
        if not parts:
            continue
        key = (r.get("name_zh") or r["name"], int(r["rank"]))
        rec = by_river.setdefault(key, {"name": r.get("name_zh"), "name_en": [], "parts": []})
        if r["name"] and r["name"] not in rec["name_en"]:
            rec["name_en"].append(r["name"])
        rec["parts"].extend(parts)
    rivers = []
    for (_, rank), rec in by_river.items():
        for piece in join_lines(rec["parts"]):
            if sum(math.dist(a, b) for a, b in zip(piece, piece[1:])) < 1.0:
                continue  # a corner clipped down to nothing
            pts = simplify(piece, 4.0)
            if len(pts) >= 2:
                rivers.append({"name": rec["name"], "name_en": " / ".join(rec["name_en"]) or None, "rank": rank,
                               "pts": [round(v, 1) for p in pts for v in p]})
    rivers.sort(key=lambda r: (r["rank"], r["name"] or "~", r["name_en"] or ""))

    # --- lake label anchors: named lakes covering at least one lake hex ------------------------------
    lakes = []
    lake_cells: dict[int, list[int]] = {}
    for i in range(grid.size):
        if water[i] == LAKE:
            k = lake_idx.locate(*grid.center(*grid.col_row(i)))
            if k >= 0:
                lake_cells.setdefault(k, []).append(i)
    for k, cells in lake_cells.items():
        name = ne["lakes"][k].get("name_zh")
        if not name:
            continue
        pts = [grid.center(*grid.col_row(i)) for i in cells]
        mx, my = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        best = min(pts, key=lambda p: math.dist(p, (mx, my)))
        lakes.append({"name": name, "x": round(best[0], 1), "y": round(best[1], 1), "cells": len(cells)})
    lakes.sort(key=lambda l: -l["cells"])

    # --- 南海诸岛 inset, the dashed line on the main map, island labels ------------------------------
    inset = build_inset(bounds, ne, proj)
    bx0, by0, bx1, by1 = inset["box"]

    def visible(pt):
        x, y = pt
        return 0 <= x <= width and 0 <= y <= height and not (bx0 <= x <= bx1 and by0 <= y <= by1)

    dash_line = []
    for d in sorted(bounds["dash_line"], key=lambda d: d["seq"]):
        line = [proj(lon, lat) for lon, lat in dash_centerline(d)]
        for run in land_runs(line, visible, step=1.0):
            pts = simplify(run, 15.0)
            if len(pts) >= 2 and math.dist(pts[0], pts[-1]) >= 2.0:
                dash_line.append(flat(pts))
    islands = []
    for isl in bounds["islands"]:
        if isl.get("point"):
            x, y = proj(*isl["point"])
            if visible((x, y)):
                islands.append({"id": isl["id"], "name": isl["name"], "kind": isl["kind"], "x": round(x, 1), "y": round(y, 1)})
    labels = []
    sansha = pref_by_id.get("460300")
    if sansha and sansha.get("seat") and visible(proj(*sansha["seat"])):
        x, y = proj(*sansha["seat"])
        labels.append({"name": sansha["name"], "x": round(x, 1), "y": round(y, 1), "kind": "city", "id": "460300"})
    print(f"    inset: {len(inset['land'])} land rings, {len(inset['dashes'])} dashes, {len(inset['islands'])} islands; "
          f"main map: {len(dash_line)} dash pieces, islands {[i['name'] for i in islands]}")

    # --- national boundary sections: foreign hexes whose edges with Chinese hexes are 未定国界 ---------------
    undefined = []
    for i in range(grid.size):
        if owner[i] != FOREIGN:
            continue
        if not any(owner[j] >= 0 for j in grid.neighbours(i)):
            continue
        cx, cy = grid.center(*grid.col_row(i))
        lon, lat = proj.inverse(cx, cy)
        k = next((foreign_idx.locate(cx + dx, cy + dy) for dx, dy in offsets
                  if foreign_idx.locate(cx + dx, cy + dy) >= 0), -1)
        a3 = ne["countries"][k].get("a3") if k >= 0 else None
        for sec in UNDEFINED_BOUNDARY:
            x0, y0, x1, y1 = sec["bbox"]
            if a3 in sec["a3"] and x0 <= lon <= x1 and y0 <= lat <= y1:
                undefined.append(i)
                break
    boundary = {"undefined": undefined, "sections": UNDEFINED_BOUNDARY, "note": BOUNDARY_NOTE}
    print(f"    national boundary: {len(undefined)} foreign hexes on 未定国界 sections")

    cover_table = [{"id": 0, "key": "none", "name": "无", "rule": "水域，或没有土地覆盖陆地采样点。"}] + [
        {"id": cid, "key": key, "name": name, "color": color, "groups": grp, "rule": rule}
        for cid, key, name, color, grp, rule in COVER
    ]
    tables = {
        "water": WATER_TABLE,
        "relief": RELIEF_TABLE,
        "cover": cover_table,
        "notes": {"water": WATER_NOTE, "relief": RELIEF_NOTE, "cover": COVER_NOTE},
    }
    states_out = []
    for p in provinces:
        full, short, abbr, kind = china.PROVINCES.get(p["code"], (p["name"], p.get("short") or p["name"], p.get("abbr"), p.get("kind")))
        states_out.append({"fips": p["code"], "abbr": abbr, "name": short, "full": full, "kind": kind,
                           "kind_label": china.PROVINCE_KIND_LABEL.get(kind)})
    write_web(
        "world.json",
        {
            "version": VERSION,
            "grid": grid.to_dict(),
            "width": round(width, 1),
            "height": round(height, 1),
            "margin": proj.margin,
            "projection": proj.to_dict(),
            "owner": owner,
            "county": county,
            "elev": elev,
            "elev_max": elev_max,
            "water": water,
            "relief": relief,
            "cover": cover,
            "pct": pct,
            "tables": tables,
            "states": states_out,
            "counties": [{"geoid": c["adcode"], "name": c["name"]} for c in counties],
            "rivers": rivers,
            "lakes": lakes,
            "dash_line": dash_line,
            "boundary": boundary,
            "islands": islands,
            "labels": labels,
            "inset": inset,
            "notice": NOTICE,
        },
    )
    save_interim(
        "_world",
        {
            "grid": grid.to_dict(),
            "projection": proj.to_dict(),
            "owner": owner,
            "states": codes,
            "counties": county_ids,
            "county_prefecture": county_pref,
            "county": county,
            "county_samples": county_samples,
            "water": water,
            "cover": cover,
            "pct": pct,
            "inset": inset,
        },
    )
    sizes = Counter(owner)
    print("    land hexes per province: " + ", ".join(f"{provinces[k]['short'] if provinces[k].get('short') else codes[k]} {sizes[k]}"
                                                    for k in sorted(range(len(codes)), key=lambda k: -sizes[k])))
    print(f"    Chinese land {sum(v for k, v in sizes.items() if k >= 0)}, foreign {sizes[FOREIGN]}, water {sizes[WATER]}")


def county_prefecture_of(county_pref: list[str], k: int) -> str | None:
    return county_pref[k] if 0 <= k < len(county_pref) else None


# --- builder check (pipeline/check.py part 3) ----------------------------------------------------------------


def is_production_grid(g) -> bool:
    """True for the fixed China frame's grid (128 × 120 at 25 km); test fixtures use tiny synthetic grids."""
    want = HexGrid.covering(MAP_WIDTH_KM, MAP_HEIGHT_KM, HEX_RADIUS_KM)
    return isinstance(g, dict) and (g.get("cols"), g.get("rows"), g.get("radius")) == (want.cols, want.rows, want.radius)


def check(web_dir: Path) -> list[tuple[str, str]]:
    """China-specific checks of world.json v3: the territory and compliance items of CN_PLAN §2.3 / §7."""
    import json

    p = Path(web_dir) / "world.json"
    if not p.exists():
        return [("NOTE", "world.json missing (run: uv run python -m pipeline build world)")]
    w = json.loads(p.read_text())
    out: list[tuple[str, str]] = []
    if w.get("version") != VERSION:
        return [("FAIL", f"world.json version {w.get('version')!r}, expected {VERSION}")]
    if not is_production_grid(w.get("grid")):
        return [("NOTE", f"world.json grid {w.get('grid')} is not the production frame: territory checks skipped")]
    codes = [s.get("fips") for s in w.get("states", [])]
    if codes != sorted(china.PROVINCES):
        out.append(("FAIL", f"world.json states must be the 34 province codes in order, got {len(codes)}"))
    owner = w.get("owner") or []
    sizes = Counter(owner)
    missing = [codes[k] for k in range(len(codes)) if not sizes.get(k)]
    if missing:
        out.append(("FAIL", f"provinces without any land hex: {missing}"))
    else:
        out.append(("PASS", f"all {len(codes)} provinces own land hexes (台湾 {sizes[codes.index('71')]}, 海南 "
                            f"{sizes[codes.index('46')]}, 香港 {sizes[codes.index('81')]}, 澳门 {sizes[codes.index('82')]})"))
    big = sorted(range(len(codes)), key=lambda k: -sizes.get(k, 0))[:3]
    if {codes[k] for k in big} != {"65", "54", "15"}:
        out.append(("WARN", f"largest provinces are {[codes[k] for k in big]}, expected 新疆/西藏/内蒙古"))
    names = {i.get("id") for i in w.get("islands", [])}
    need = {"diaoyu", "chiwei"}
    out.append(("PASS" if need <= names else "FAIL", f"main-map island labels: {sorted(names)} (钓鱼岛、赤尾屿 required)"))
    inset = w.get("inset") or {}
    nd = len(inset.get("dashes") or [])
    out.append(("PASS" if nd == 10 else "FAIL", f"inset 南海诸岛: {nd} dashes (10 required), {len(inset.get('land') or [])} land rings"))
    if inset.get("title") != "南海诸岛" or len(inset.get("box") or []) != 4:
        out.append(("FAIL", "inset needs title 南海诸岛 and a 4-number box"))
    if not w.get("dash_line"):
        out.append(("FAIL", "world.json dash_line: no dashes on the main map (dashes 1–4 lie inside the frame)"))
    notice = w.get("notice") or {}
    if "未申请审图号" not in (notice.get("text") or "") or not notice.get("boundary"):
        out.append(("FAIL", "world.json notice must carry the 审图号 notice and the boundary source"))
    proj = w.get("projection") or {}
    if not all(k in proj for k in ("min_x", "max_y", "margin", "lat1", "lat2", "lat0", "lon0")):
        out.append(("FAIL", "world.json projection must carry min_x, max_y, margin, lat1, lat2, lat0, lon0"))
    named = {r.get("name") for r in w.get("rivers", [])}
    want = {"长江", "黄河", "西江", "黑龙江", "澜沧江", "雅鲁藏布江", "淮河", "汉江"}
    lost = sorted(want - named)
    out.append(("WARN" if lost else "PASS", f"major rivers named in Chinese: {sorted(want & named)}" + (f"; missing {lost}" if lost else "")))
    county = w.get("county") or []
    tw = codes.index("71") if "71" in codes else -2
    nocounty = sum(1 for o, c in zip(owner, county) if o >= 0 and o != tw and c < 0)
    out.append(("WARN" if nocounty else "PASS", f"Chinese land hexes outside Taiwan without a county: {nocounty}"))
    return out
