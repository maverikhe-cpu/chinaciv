"""High-speed rail, conventional main railways and expressways in China from OpenStreetMap (Overpass API).

Natural Earth's 1:10m roads/railroads are far too sparse for China's high-speed network, so this source queries
OpenStreetMap through the public Overpass API (no key), tile by tile, and generalises the result to the map scale.

Fetch
  * The national outline (DataV GeoAtlas 100000.json, same file as cn_boundaries; downloaded into this source's
    own raw folder so the module stays independent) gives the query mask: China + a 1° buffer, on a 0.25° raster.
  * The window lon 72-135°E, lat 17-54°N is cut into 3°×3° tiles (instead of the plan's "by province" bboxes:
    regular tiles keep every query about the same size; equivalent coverage). Only tiles touching the mask are
    queried. One Overpass query per tile asks for
        way[railway=rail][highspeed=yes][!service]   (high-speed candidates)
        way[railway=rail][usage=main][!service]      (conventional main lines)
        way[highway=motorway]                        (expressways; motorway_link ramps excluded)
    and uses `convert` to keep only the tags needed (highway, railway, name, ref, usage, highspeed, maxspeed),
    gzip-compressed (`overpass/<tile>.json.gz`). A tile whose answer carries an Overpass runtime error (timeout,
    out of memory) is discarded and re-queried as four quarter tiles; network errors / 5xx from every mirror
    are retried in up to ROUNDS passes with growing pauses, then the tile is split too. Mirrors are tried in order
    (maps.mail.ru, overpass-api.de, overpass.private.coffee); the manifest records the URL actually used.
    WORKERS tiles are fetched in parallel; afterwards any tile whose manifest entry was lost to a concurrent
    manifest write is re-fetched alone, so every cached file stays in the manifest.
    Note: maps.mail.ru uses a HARICA TLS root that some Python CA bundles lack (macOS /etc/ssl/cert.pem); if
    fetch reports CERTIFICATE_VERIFY_FAILED, point SSL_CERT_FILE at a bundle with the system roots, e.g.
    `security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain > roots.pem`.

Process (decisions not fixed by docs/CN_PLAN.md §4.9 are documented here)
  * Classes. `hsr`: railway=rail + highspeed=yes, excluding usage test/industrial/military/tourism and depot links
    (names with 动车段/动车所/走行线). Design speed proxy: per line name, the highest numeric `maxspeed` of its
    highspeed ways (segment maxspeed drops near stations, the line maximum approximates design speed). A line is
    `hsr` when that speed is ≥ 250 km/h or unknown (OSM's highspeed=yes convention); highspeed lines whose known
    maximum is < 250 km/h (e.g. 200 km/h 城际/客货共线) go to `rail` with `"highspeed": true`.
    `rail`: railway=rail + usage=main (no service tracks). `expressway`: highway=motorway (国家 G… and 省级 S…).
  * OSM ways sharing an end node and the same (class, name, ref) are merged into chains.
  * Clip: vertices outside China + 1° buffer (0.25° raster mask) are dropped (lines split there).
  * Parallel duplicates (double track, dual carriageways, parallel tracks in yards) are removed within each class:
    chains are processed longest first, densified to ≤ 0.5 km, and points within ~1-2 km (3×3 neighbourhood on a
    1 km grid) of an already kept line of the same class are dropped; remaining runs < 1.5 km are dropped.
    Different classes are never deduplicated against each other.
  * Each kept run is simplified with Douglas-Peucker at 2 km tolerance in map km (China Albers), lon/lat rounded
    to 4 decimals (~10 m).
  * `stats`: total km per class after deduplication (single-counted network length), all lines and those inside
    China's national outline (segment midpoint test), plus counts.

Interim (docs/CN_PLAN.md §4.9, plus additive fields):
  hsr:        [{"name", "ref", "speed": int|null, "line": [[lon, lat], …]}]
  rail:       [{"name", "usage": "main"|"branch", "highspeed": bool, "speed": int|null, "line"}]
              (usage "branch" only for 200-249 km/h highspeed branch lines; conventional branch lines are not queried)
  expressway: [{"ref", "name", "line"}]
  stats, tolerance_km, osm_base (Overpass data timestamp)
Extract of 2026-11-26 (osm_base 2026-11-26T04:33Z): hsr 225 lines / 43,163 km inside China, rail 97,306 km,
expressway 182,277 km (all lines incl. the 1° buffer: 43,194 / 107,247 / 183,472 km); interim ≈ 0.9 MB.
© OpenStreetMap contributors, ODbL 1.0. Coordinates are WGS-84 (not converted, see CN_PLAN global rules).
"""

from __future__ import annotations

import gzip
import http.client
import json
import math
import re
import time
import urllib.error
import urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from ..core import fetch
from ..core.geo import Shape, ShapeIndex, china_projection, geojson_rings, thin_ring
from ..core.source import Source, SourceInfo

OUTLINE_URL = "https://geo.datav.aliyun.com/areas_v3/bound/100000.json"
OUTLINE_FILE = "datav/100000.json"
MIRRORS = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
WINDOW = (72, 17, 135, 54)  # lon0, lat0, lon1, lat1 (whole degrees, multiples of TILE from lon0/lat0)
TILE = 3  # degrees
MASK_CELL = 0.25  # degrees
MASK_BUFFER = 4  # cells (= 1°)
REQUEST_TIMEOUT = 300  # seconds per attempt
ROUNDS = 4  # passes over all mirrors before a tile is split (sleep 30 s, 60 s, … between passes)
# Query header: [timeout:180] and the default maxsize. Larger declared budgets make the servers queue the query
# (maps.mail.ru's front end answers 504 after ~25 s), so small tiles + retries beat big budgets.
WORKERS = 4  # parallel Overpass requests (maps.mail.ru allows 8 slots per client)
MAX_SPLIT = 2  # a 3° tile may be split into quarters twice (0.75°)

HSR_MIN_SPEED = 250  # km/h
DENSIFY_KM = 0.5
OCC_CELL_KM = 1.0
MIN_RUN_KM = 1.5
TOLERANCE_KM = 2.0
DIGITS = 4

TAGS = ("highway", "railway", "name", "ref", "usage", "highspeed", "maxspeed")
HSR_BAD_USAGE = {"test", "industrial", "military", "tourism", "freight"}
DEPOT = re.compile(r"动车段|动车所|走行线|存车")


def overpass_query(bbox: tuple[float, float, float, float]) -> str:
    """Overpass QL for one tile; bbox = (lon0, lat0, lon1, lat1)."""
    b = f"({bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]})"  # Overpass order: south, west, north, east
    conv = ",".join(f'{k}=t["{k}"]' for k in TAGS)
    return (
        "[out:json][timeout:180];("
        f'way["railway"="rail"]["highspeed"="yes"][!"service"]{b};'
        f'way["railway"="rail"]["usage"="main"][!"service"]{b};'
        f'way["highway"="motorway"]{b};'
        f");convert W ::id=id(),{conv},::geom=geom();out geom;"
    )


def tile_name(bbox) -> str:
    return "_".join(f"{v:g}" for v in bbox)


def quarter(bbox):
    x0, y0, x1, y1 = bbox
    xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
    return [(x0, y0, xm, ym), (xm, y0, x1, ym), (x0, ym, xm, y1), (xm, ym, x1, y1)]


def parse_speed(v) -> int | None:
    """OSM maxspeed → km/h (largest number in values like "350", "120 km/h", "80;160"); None if unusable."""
    if not v:
        return None
    s = str(v)
    if "mph" in s:
        return None
    nums = [int(n) for n in re.findall(r"\d+", s)]
    nums = [n for n in nums if 0 < n < 700]
    return max(nums) if nums else None


def classify(elements: list[dict]) -> list[tuple[str, dict, list]]:
    """Overpass `convert` elements → [(cls, attrs, coords)] with cls hsr | rail | expressway.

    attrs: name, ref (None when empty), usage, speed (line design-speed proxy), highspeed flag for rail."""
    ways, seen = [], set()
    for e in elements:
        if e.get("id") in seen:
            continue
        seen.add(e.get("id"))
        coords = (e.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        t = {k: (str(v).strip() or None) for k, v in (e.get("tags") or {}).items()}
        ways.append((t, [(float(x), float(y)) for x, y, *_ in coords]))

    line_speed: dict[str, int] = {}
    for t, _ in ways:
        if t.get("railway") == "rail" and t.get("highspeed") == "yes" and t.get("name"):
            sp = parse_speed(t.get("maxspeed"))
            if sp is not None:
                line_speed[t["name"]] = max(sp, line_speed.get(t["name"], 0))

    out = []
    for t, coords in ways:
        name, ref = t.get("name"), t.get("ref")
        if t.get("highway") == "motorway":
            out.append(("expressway", {"ref": ref, "name": name}, coords))
            continue
        if t.get("railway") != "rail":
            continue
        usage = t.get("usage")
        if t.get("highspeed") == "yes" and usage not in HSR_BAD_USAGE and not (name and DEPOT.search(name)):
            speed = line_speed.get(name) if name else parse_speed(t.get("maxspeed"))
            if speed is None or speed >= HSR_MIN_SPEED:
                out.append(("hsr", {"name": name, "ref": ref, "speed": speed}, coords))
            elif usage in ("main", "branch"):
                out.append(("rail", {"name": name, "usage": usage, "highspeed": True, "speed": speed}, coords))
            continue
        if usage == "main":
            out.append(("rail", {"name": name, "usage": "main", "highspeed": False,
                                 "speed": line_speed.get(name) if name else None}, coords))
    return out


def merge_chains(lines: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Join polylines end to end where exactly two line ends meet at the same coordinate."""
    ends: dict[tuple, list[tuple[int, int]]] = defaultdict(list)
    for i, ln in enumerate(lines):
        ends[ln[0]].append((i, 0))
        ends[ln[-1]].append((i, 1))
    used = [False] * len(lines)

    def extend(chain: list, tail_line: int) -> None:
        """Grow `chain` at its end (chain[-1]) as long as the junction has degree 2."""
        cur = tail_line
        while True:
            at = ends[chain[-1]]
            if len(at) != 2:
                return
            nxt = [(j, side) for j, side in at if j != cur]
            if len(nxt) != 1 or used[nxt[0][0]]:
                return
            j, side = nxt[0]
            used[j] = True
            seg = lines[j] if side == 0 else lines[j][::-1]
            chain.extend(seg[1:])
            cur = j

    out = []
    for i, ln in enumerate(lines):
        if used[i]:
            continue
        used[i] = True
        chain = list(ln)
        extend(chain, i)
        chain.reverse()
        extend(chain, i)
        out.append(chain)
    return out


def densify(pts: list[tuple[float, float]], step: float) -> list[tuple[float, float]]:
    out = [pts[0]]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        d = math.hypot(x1 - x0, y1 - y0)
        n = max(1, math.ceil(d / step))
        for k in range(1, n + 1):
            out.append((x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n))
    return out


def douglas_peucker(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    if len(pts) <= 2:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = pts[a], pts[b]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        best, idx = -1.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            if L2 == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                d = abs(dy * (px - ax) - dx * (py - ay)) / math.sqrt(L2)
            if d > best:
                best, idx = d, i
        if best > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(pts, keep) if k]


def length_km(pts) -> float:
    return sum(math.hypot(x1 - x0, y1 - y0) for (x0, y0), (x1, y1) in zip(pts, pts[1:]))


def dedupe_runs(chains_km: list[list[tuple[float, float]]], inside=None, cell: float = OCC_CELL_KM,
                step: float = DENSIFY_KM, min_run: float = MIN_RUN_KM) -> list[tuple[int, list]]:
    """Drop parts of chains (map km) that run along an already kept chain. Returns [(chain index, run)].

    Chains are visited longest first. `inside(x, y)` (optional) marks points to keep at all (clip mask)."""
    occ: set[tuple[int, int]] = set()
    order = sorted(range(len(chains_km)), key=lambda i: -length_km(chains_km[i]))
    out = []
    for i in order:
        pts = densify(chains_km[i], step)
        cells = [(int(x // cell), int(y // cell)) for x, y in pts]
        free = [
            (inside is None or inside(x, y))
            and not any((cx + dx, cy + dy) in occ for dx in (-1, 0, 1) for dy in (-1, 0, 1))
            for (x, y), (cx, cy) in zip(pts, cells)
        ]
        k = 0
        while k < len(pts):
            if not free[k]:
                k += 1
                continue
            j = k
            while j + 1 < len(pts) and free[j + 1]:
                j += 1
            a = k - 1 if k > 0 and (inside is None or inside(*pts[k - 1])) else k
            b = j + 1 if j + 1 < len(pts) and (inside is None or inside(*pts[j + 1])) else j
            run = pts[a:b + 1]
            if len(run) >= 2 and length_km(run) >= min_run:
                out.append((i, run))
            k = j + 1
        occ.update(c for c, p in zip(cells, pts) if inside is None or inside(*p))
    return out


class CnRailRoads(Source):
    info = SourceInfo(
        id="cn_rail_roads",
        title="中国高速铁路、普速干线铁路与高速公路线形（OpenStreetMap）",
        publisher="OpenStreetMap 贡献者（经 Overpass API 提取）；查询范围依据阿里云 DataV.GeoAtlas 国界轮廓",
        homepage="https://www.openstreetmap.org/",
        license="© OpenStreetMap contributors，开放数据库许可协议 ODbL 1.0",
        description="通过 Overpass API 按 3°×3° 分块提取 OpenStreetMap 中全国（含台湾省、香港、澳门）的铁路与高速公路："
        "高速铁路为 railway=rail 且 highspeed=yes 的线路，按线路名取各路段最高限速（maxspeed）作为设计速度近似，"
        "≥250 km/h 或未标注者计为高铁，200 km/h 级线路计入普速铁路并标记；普速铁路为 usage=main 的干线；"
        "高速公路为 highway=motorway（国家高速 G 字头与省级高速 S 字头，不含匝道）。"
        "线形按地图比例尺概括：同类平行线（复线、双向分离车道）去重，Douglas-Peucker 2 km 容差简化，"
        "裁剪到国界外 1° 缓冲范围内。仅作地图示意，里程为概括后的近似值，不是官方统计。",
        used_for=["高速铁路", "普速铁路", "高速公路"],
    )

    # ---------------------------------------------------------------- fetch

    def _tiles(self, mask: set) -> list[tuple]:
        x0, y0, x1, y1 = WINDOW
        tiles = []
        for lon in range(x0, x1, TILE):
            for lat in range(y0, y1, TILE):
                bb = (lon, lat, lon + TILE, lat + TILE)
                if self._touches(mask, bb):
                    tiles.append(bb)
        return tiles

    @staticmethod
    def _touches(mask: set, bb) -> bool:
        i0, j0 = int(math.floor(bb[0] / MASK_CELL)), int(math.floor(bb[1] / MASK_CELL))
        i1, j1 = int(math.ceil(bb[2] / MASK_CELL)), int(math.ceil(bb[3] / MASK_CELL))
        return any((i, j) in mask for i in range(i0, i1) for j in range(j0, j1))

    def _fetch_tile(self, bb, depth: int, force: bool) -> None:
        rel = f"overpass/{tile_name(bb)}.json.gz"
        path = self.raw_dir / rel
        if path.exists() and not force:
            if self._tile_ok(path):
                return
            self.discard(rel)
        q = urllib.parse.quote(overpass_query(bb))
        last, runtime_error = None, False
        for rnd in range(ROUNDS):
            if rnd:
                wait = 30 * 2 ** (rnd - 1)
                print(f"      all mirrors failed; waiting {wait}s (round {rnd + 1}/{ROUNDS})")
                time.sleep(wait)
            for mirror in MIRRORS:
                try:
                    # fetch.download directly (same cache + manifest as Source.download) for a bounded timeout.
                    fetch.download(self.id, f"{mirror}?data={q}", rel, force=True, timeout=REQUEST_TIMEOUT,
                                   headers={"Accept-Encoding": "gzip"}, expect_binary=True)
                except fetch.OfflineError:
                    raise
                except (urllib.error.URLError, OSError, http.client.HTTPException, fetch.UnexpectedContentError) as exc:
                    print(f"      {mirror}: {exc}")
                    last = exc
                    continue
                if self._tile_ok(path):
                    return
                self.discard(rel)
                last, runtime_error = RuntimeError(f"Overpass runtime error for tile {bb}"), True
                break  # a timeout / memory error inside Overpass: split instead of retrying
            if runtime_error:
                break
        if depth < MAX_SPLIT:
            print(f"      splitting tile {bb}")
            for sub in quarter(bb):
                self._fetch_tile(sub, depth + 1, force)
            return
        raise RuntimeError(f"cn_rail_roads: could not fetch tile {bb}: {last}")

    @staticmethod
    def _read_tile(path) -> dict:
        raw = path.read_bytes()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return json.loads(raw)

    def _tile_ok(self, path) -> bool:
        try:
            d = self._read_tile(path)
        except (ValueError, OSError, EOFError):
            return False
        remark = d.get("remark") or ""
        return "elements" in d and "error" not in remark.lower()

    def fetch(self, force=False):
        self.download(OUTLINE_URL, OUTLINE_FILE, force=force)
        tiles = self._tiles(self._mask())
        todo = [bb for bb in tiles if force or not (self.raw_dir / f"overpass/{tile_name(bb)}.json.gz").exists()]
        print(f"    {len(tiles)} tiles of {TILE}°, {len(todo)} to fetch ({WORKERS} in parallel)")
        if todo:
            with ThreadPoolExecutor(WORKERS) as pool:
                for n, _ in enumerate(pool.map(lambda bb: self._fetch_tile(bb, 0, force), todo), 1):
                    if n % 10 == 0:
                        print(f"    {n}/{len(todo)} tiles done")
        # Parallel downloads update manifest.json concurrently (read-modify-write in core.fetch): re-fetch,
        # one at a time, any cached tile whose manifest entry was lost, so every file stays traceable.
        manifest = fetch.read_manifest(self.id)
        for p in sorted((self.raw_dir / "overpass").glob("*.json.gz")):
            rel = f"overpass/{p.name}"
            if not p.name.startswith(".") and rel not in manifest:
                print(f"    {rel}: manifest entry lost, re-fetching")
                bb = tuple(float(v) for v in p.name[:-len(".json.gz")].split("_"))
                self.discard(rel)
                self._fetch_tile(bb, 0, True)

    # ---------------------------------------------------------------- mask

    def _outline(self) -> list[list[tuple[float, float]]]:
        with open(self.raw_dir / OUTLINE_FILE, encoding="utf-8") as f:
            feats = json.load(f)["features"]
        return [thin_ring(r, 0.02) for ft in feats for r in geojson_rings(ft["geometry"])]

    def _mask(self) -> set[tuple[int, int]]:
        """0.25° cells within 1° of China's national outline (vertices + interior cell centres, dilated)."""
        rings = self._outline()
        shape = Shape(rings)
        core = {(int(math.floor(x / MASK_CELL)), int(math.floor(y / MASK_CELL))) for r in rings for x, y in r}
        x0, y0, x1, y1 = shape.bbox
        for i in range(int(x0 / MASK_CELL), int(x1 / MASK_CELL) + 1):
            for j in range(int(y0 / MASK_CELL), int(y1 / MASK_CELL) + 1):
                if (i, j) not in core and shape.contains((i + 0.5) * MASK_CELL, (j + 0.5) * MASK_CELL):
                    core.add((i, j))
        r = MASK_BUFFER
        return {(i + di, j + dj) for i, j in core for di in range(-r, r + 1) for dj in range(-r, r + 1)}

    # ---------------------------------------------------------------- process

    def process(self):
        # skip dot files (macOS AppleDouble "._*" companions on exFAT volumes)
        files = sorted(p for p in (self.raw_dir / "overpass").glob("*.json.gz") if not p.name.startswith("."))
        if not files:
            raise FileNotFoundError("cn_rail_roads: no Overpass tiles cached – run: uv run python -m pipeline fetch cn_rail_roads")
        elements, osm_base = [], set()
        for p in files:
            d = self._read_tile(p)
            if "error" in (d.get("remark") or "").lower():
                raise RuntimeError(f"{p.name}: Overpass error {d['remark']!r} – re-run fetch")
            osm_base.add((d.get("osm3s") or {}).get("timestamp_osm_base"))
            elements.extend(d.get("elements") or [])
        ways = classify(elements)
        print(f"    {len(files)} tiles, {len(elements)} elements, {len(ways)} ways kept")

        proj = china_projection()
        mask = self._mask()
        china = ShapeIndex([Shape(self._outline())], cell_km=1.0)

        def in_mask_km(x, y):
            lon, lat = proj.inverse(x, y)
            return (int(math.floor(lon / MASK_CELL)), int(math.floor(lat / MASK_CELL))) in mask

        out: dict[str, list] = {"hsr": [], "rail": [], "expressway": []}
        stats: dict = {}
        for cls in out:
            groups: dict[tuple, list] = defaultdict(list)
            for c, attrs, coords in ways:
                if c == cls:
                    groups[tuple(sorted(attrs.items()))].append(coords)
            chains, chain_attrs = [], []
            for key, lines in groups.items():
                for ch in merge_chains(lines):
                    chains.append([proj(x, y) for x, y in ch])
                    chain_attrs.append(dict(key))
            runs = dedupe_runs(chains, inside=in_mask_km)
            total = in_china = 0.0
            for i, run in runs:
                simp = douglas_peucker(run, TOLERANCE_KM)
                ll = [proj.inverse(x, y) for x, y in simp]
                total += length_km(simp)
                for (a, b), (c, d) in zip(simp, simp[1:]):
                    lon, lat = proj.inverse((a + c) / 2, (b + d) / 2)
                    if china.locate(lon, lat) >= 0:
                        in_china += math.hypot(c - a, d - b)
                out[cls].append({**chain_attrs[i], "line": [[round(x, DIGITS), round(y, DIGITS)] for x, y in ll]})
            stats[cls] = {"lines": len(out[cls]), "km": round(total), "km_in_china": round(in_china),
                          "vertices": sum(len(e["line"]) for e in out[cls])}
            print(f"    {cls}: {stats[cls]}")
        base = max((b for b in osm_base if b), default=None)
        return {
            "vintage": f"OpenStreetMap contributors, Overpass extract {base[:7] if base else '?'}",
            "_files": [OUTLINE_FILE] + [f"overpass/{p.name}" for p in files],
            "osm_base": base,
            "tolerance_km": TOLERANCE_KM,
            "stats": stats,
            **out,
        }
