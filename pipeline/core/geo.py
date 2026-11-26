"""Small, dependency-free geometry helpers.

Everything on the map lives in "map km": an Albers equal-area projection of China (central meridian 105°E,
standard parallels 25°N and 47°N, latitude of origin 0°, on a sphere), shifted so the top-left of the map is
(0, 0) and y points *down* (like screen coordinates). Both the hex grid and the browser use this space.

The main map frame is fixed here (not fitted to data at build time), so every source and builder can project
points before the `world` builder has run, and the frontend agrees with the pipeline. It covers the mainland,
Taiwan and Hainan; the South China Sea islands and the dashed boundary line south of the frame are drawn in the
南海诸岛 inset box in the lower-right corner (see `InsetProjection` and docs/CN_PLAN.md §2).
"""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from typing import IO, Iterable, Iterator

R_EARTH_KM = 6371.0088

# --- China map constants (docs/CN_PLAN.md §2; web/js reads them from world.json `projection` / `inset`) -------

CHINA_ALBERS = {"lat1": 25.0, "lat2": 47.0, "lat0": 0.0, "lon0": 105.0}
# Bounds of the national outline (DataV 100000_full, provinces only, ignoring polygons wholly south of 17.5°N and
# the dashed line) in raw Albers km (y up), rounded outwards to whole km. Fixed so the frame never moves.
MAIN_MIN_LAT = 17.5
ALBERS_BOUNDS = (-2619.0, 1884.0, 2202.0, 5939.0)  # x0, y0, x1, y1
MAP_MARGIN_KM = 200.0  # left, top and bottom margin
MAP_RIGHT_MARGIN_KM = 450.0  # wider on the right: room for the inset box east of Luzon
MAP_WIDTH_KM = ALBERS_BOUNDS[2] - ALBERS_BOUNDS[0] + MAP_MARGIN_KM + MAP_RIGHT_MARGIN_KM  # 5471
MAP_HEIGHT_KM = ALBERS_BOUNDS[3] - ALBERS_BOUNDS[1] + 2 * MAP_MARGIN_KM  # 4455

# 南海诸岛 inset: this lon/lat window (all ten dashes, the Pearl River mouth to Borneo) drawn with the main
# projection scaled by INSET_SCALE, in a box INSET_GAP_KM from the bottom-right corner of the map.
INSET_REGION = (106.5, 2.8, 123.5, 24.8)  # lon_min, lat_min, lon_max, lat_max
INSET_SCALE = 0.3
INSET_PAD_KM = 12.0
INSET_GAP_KM = 25.0


class Albers:
    """Albers equal-area conic on a sphere. Defaults: China (CHINA_ALBERS)."""

    def __init__(self, lat1=25.0, lat2=47.0, lat0=0.0, lon0=105.0):
        p1, p2, p0 = map(math.radians, (lat1, lat2, lat0))
        self.lon0 = math.radians(lon0)
        self.n = (math.sin(p1) + math.sin(p2)) / 2
        self.c = math.cos(p1) ** 2 + 2 * self.n * math.sin(p1)
        self.rho0 = R_EARTH_KM * math.sqrt(self.c - 2 * self.n * math.sin(p0)) / self.n

    def __call__(self, lon: float, lat: float) -> tuple[float, float]:
        rho = R_EARTH_KM * math.sqrt(self.c - 2 * self.n * math.sin(math.radians(lat))) / self.n
        theta = self.n * (math.radians(lon) - self.lon0)
        return rho * math.sin(theta), self.rho0 - rho * math.cos(theta)

    def inverse(self, x: float, y: float) -> tuple[float, float]:
        """Albers km → (lon, lat)."""
        dy = self.rho0 - y
        rho = math.hypot(x, dy)
        theta = math.atan2(x, dy)
        s = (self.c - (rho * self.n / R_EARTH_KM) ** 2) / (2 * self.n)
        return math.degrees(self.lon0 + theta / self.n), math.degrees(math.asin(max(-1.0, min(1.0, s))))


class AlbersEllipsoid:
    """Ellipsoidal Albers equal-area (Snyder 1987, eqs. 14-3..14-6), output in metres.

    Generic: pass the raster's own parameters (e.g. EPSG:5070 for a US raster was lat1 29.5, lat2 45.5,
    lat0 23, lon0 -96). Defaults are the China parameters on GRS80. Used to look up rasters stored in an
    Albers CRS; lon/lat rasters need no projection.
    """

    def __init__(self, lat1=25.0, lat2=47.0, lat0=0.0, lon0=105.0, a=6378137.0, inv_f=298.257222101):
        f = 1 / inv_f
        self.a = a
        self.e2 = f * (2 - f)
        self.e = math.sqrt(self.e2)
        self.lon0 = math.radians(lon0)
        p1, p2, p0 = map(math.radians, (lat1, lat2, lat0))
        m1, m2 = self._m(p1), self._m(p2)
        q1, q2, q0 = self._q(p1), self._q(p2), self._q(p0)
        self.n = (m1 * m1 - m2 * m2) / (q2 - q1)
        self.c = m1 * m1 + self.n * q1
        self.rho0 = a * math.sqrt(self.c - self.n * q0) / self.n

    def _m(self, phi: float) -> float:
        s = math.sin(phi)
        return math.cos(phi) / math.sqrt(1 - self.e2 * s * s)

    def _q(self, phi: float) -> float:
        s, e = math.sin(phi), self.e
        return (1 - self.e2) * (s / (1 - self.e2 * s * s) - math.log((1 - e * s) / (1 + e * s)) / (2 * e))

    def __call__(self, lon: float, lat: float) -> tuple[float, float]:
        rho = self.a * math.sqrt(self.c - self.n * self._q(math.radians(lat))) / self.n
        theta = self.n * (math.radians(lon) - self.lon0)
        return rho * math.sin(theta), self.rho0 - rho * math.cos(theta)


@dataclass
class MapProjection:
    """lon/lat → map km (origin top-left, y down). Serialised in _world / world.json as `projection`."""

    min_x: float
    max_y: float
    margin: float
    lat1: float = CHINA_ALBERS["lat1"]
    lat2: float = CHINA_ALBERS["lat2"]
    lat0: float = CHINA_ALBERS["lat0"]
    lon0: float = CHINA_ALBERS["lon0"]

    def __post_init__(self):
        self._albers = Albers(self.lat1, self.lat2, self.lat0, self.lon0)

    def __call__(self, lon: float, lat: float) -> tuple[float, float]:
        x, y = self._albers(lon, lat)
        return x - self.min_x + self.margin, self.max_y - y + self.margin

    def inverse(self, x: float, y: float) -> tuple[float, float]:
        """map km → (lon, lat)."""
        return self._albers.inverse(x + self.min_x - self.margin, self.max_y + self.margin - y)

    def to_dict(self) -> dict:
        return {"min_x": self.min_x, "max_y": self.max_y, "margin": self.margin,
                "lat1": self.lat1, "lat2": self.lat2, "lat0": self.lat0, "lon0": self.lon0}


def china_projection() -> MapProjection:
    """The fixed main-map projection (same for every builder; the `world` builder stores it in _world)."""
    return MapProjection(ALBERS_BOUNDS[0], ALBERS_BOUNDS[3], MAP_MARGIN_KM, **CHINA_ALBERS)


class InsetProjection:
    """lon/lat → map km inside the 南海诸岛 inset box (lower-right corner of the main map).

    The inset uses the main Albers projection, scaled by `scale` and translated so the Albers bounding box of
    `region` (sampled along its edges) fills the box minus `pad`. So shapes match the main map, only smaller.
    `box` = (x0, y0, x1, y1) in map km; `contains_lonlat()` tells whether a point is in the region.
    """

    def __init__(self, main: MapProjection | None = None, region=INSET_REGION, scale=INSET_SCALE,
                 pad=INSET_PAD_KM, gap=INSET_GAP_KM, map_size=(MAP_WIDTH_KM, MAP_HEIGHT_KM)):
        main = main or china_projection()
        self.region, self.scale, self.pad = tuple(region), scale, pad
        self._albers = Albers(main.lat1, main.lat2, main.lat0, main.lon0)
        lon0, lat0, lon1, lat1 = region
        pts = []
        for k in range(41):
            t = k / 40
            lon, lat = lon0 + t * (lon1 - lon0), lat0 + t * (lat1 - lat0)
            pts += [self._albers(lon, lat0), self._albers(lon, lat1), self._albers(lon0, lat), self._albers(lon1, lat)]
        self._ax0, self._ay1 = min(p[0] for p in pts), max(p[1] for p in pts)
        w = (max(p[0] for p in pts) - self._ax0) * scale + 2 * pad
        h = (self._ay1 - min(p[1] for p in pts)) * scale + 2 * pad
        x1, y1 = map_size[0] - gap, map_size[1] - gap
        self.box = (round(x1 - w, 1), round(y1 - h, 1), round(x1, 1), round(y1, 1))

    def __call__(self, lon: float, lat: float) -> tuple[float, float]:
        ax, ay = self._albers(lon, lat)
        return (self.box[0] + self.pad + (ax - self._ax0) * self.scale,
                self.box[1] + self.pad + (self._ay1 - ay) * self.scale)

    def contains_lonlat(self, lon: float, lat: float) -> bool:
        lon0, lat0, lon1, lat1 = self.region
        return lon0 <= lon <= lon1 and lat0 <= lat <= lat1

    def to_dict(self) -> dict:
        return {"box": list(self.box), "region": list(self.region), "scale": self.scale, "pad": self.pad}


def thin_ring(ring: list, tol: float) -> list:
    """Drop vertices closer than `tol` (same units as the ring) to the last kept one; keep the ring closed.
    Rings that would fall under 4 points (small islands) are returned unchanged."""
    if len(ring) <= 4:
        return list(ring)
    out = [ring[0]]
    for p in ring[1:-1]:
        if abs(p[0] - out[-1][0]) >= tol or abs(p[1] - out[-1][1]) >= tol:
            out.append(p)
    out.append(ring[-1])
    return out if len(out) >= 4 else list(ring)


def ring_contains(ring: list[tuple[float, float]], x: float, y: float) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


class Shape:
    """A (multi)polygon as a bag of rings; even-odd rule handles holes."""

    def __init__(self, rings: Iterable[list[tuple[float, float]]]):
        self.rings = [r for r in rings if len(r) >= 3]
        xs = [p[0] for r in self.rings for p in r] or [0]
        ys = [p[1] for r in self.rings for p in r] or [0]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))

    def contains(self, x: float, y: float) -> bool:
        x0, y0, x1, y1 = self.bbox
        if x < x0 or x > x1 or y < y0 or y > y1:
            return False
        inside = False
        for r in self.rings:
            if ring_contains(r, x, y):
                inside = not inside
        return inside


class ShapeIndex:
    """Bucket shapes by coarse grid square so point lookups only test a few shapes."""

    def __init__(self, shapes: list[Shape], cell_km: float = 100.0):
        self.shapes = shapes
        self.cell = cell_km
        self.buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
        for k, s in enumerate(shapes):
            x0, y0, x1, y1 = s.bbox
            for gx in range(int(x0 // cell_km), int(x1 // cell_km) + 1):
                for gy in range(int(y0 // cell_km), int(y1 // cell_km) + 1):
                    self.buckets[(gx, gy)].append(k)

    def locate(self, x: float, y: float) -> int:
        for k in self.buckets.get((int(x // self.cell), int(y // self.cell)), ()):
            if self.shapes[k].contains(x, y):
                return k
        return -1


# --- file formats -----------------------------------------------------------

KML_NS = "{http://www.opengis.net/kml/2.2}"


def read_kml_placemarks(fileobj: IO[bytes]) -> Iterator[tuple[dict, list[list[tuple[float, float]]]]]:
    """Yield (properties, rings) for each Placemark in a Census cartographic KML."""
    for _, el in ET.iterparse(fileobj):
        if el.tag != KML_NS + "Placemark":
            continue
        props = {sd.get("name"): (sd.text or "") for sd in el.iter(KML_NS + "SimpleData")}
        rings = []
        for coords in el.iter(KML_NS + "coordinates"):
            pts = []
            for tok in (coords.text or "").split():
                lon, lat = tok.split(",")[:2]
                pts.append((round(float(lon), 5), round(float(lat), 5)))
            rings.append(pts)
        yield props, rings
        el.clear()


def geojson_rings(geom: dict) -> list[list[tuple[float, float]]]:
    t, c = geom["type"], geom["coordinates"]
    polys = [c] if t == "Polygon" else c if t == "MultiPolygon" else []
    return [[(round(x, 5), round(y, 5)) for x, y, *_ in ring] for poly in polys for ring in poly]


def geojson_lines(geom: dict) -> list[list[tuple[float, float]]]:
    t, c = geom["type"], geom["coordinates"]
    lines = [c] if t == "LineString" else c if t == "MultiLineString" else []
    return [[(round(x, 5), round(y, 5)) for x, y, *_ in line] for line in lines]


def load_geojson(path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["features"]


def bbox_intersects(points: Iterable[tuple[float, float]], bbox: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = bbox
    return any(x0 <= x <= x1 and y0 <= y <= y1 for x, y in points)
