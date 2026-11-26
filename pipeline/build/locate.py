"""Shared geography for site builders (infrastructure, resources, wonders): lon/lat → map km → hex, county,
city and province. A helper module (no build()), so the registry does not run it.

Reads the `_world` interim (docs/CN_PLAN.md §5.1) and the county polygons of `cn_boundaries`; cities come from
web/data/cities.json when it exists (city ids are prefecture adcodes, so county → prefecture → city is a lookup).
Keys follow the legacy web/data names (CN_PLAN §3.3): `state` = 2-digit province code, `county` = 6-digit county
adcode, `cbsa` = cities.json id (6-digit prefecture adcode).
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core.geo import MapProjection, Shape, ShapeIndex
from ..core.hexgrid import HexGrid
from ..core.paths import WEB_DATA
from ..core.store import load_interim


def _d2(a, b) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _load(web_dir: Path, name: str):
    p = Path(web_dir) / name
    return json.loads(p.read_text()) if p.exists() else None


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class Locator:
    """lon/lat → (x, y, Chinese land cell) and map km → county adcode / city id / province code."""

    def __init__(self, world: dict | None = None, *, with_counties: bool = True, cities: list | None = None):
        world = world if world is not None else load_interim("_world")
        self.world = world
        self.proj = MapProjection(**world["projection"])
        self.grid = HexGrid(**world["grid"])
        self.owner = world["owner"]
        self.codes = list(world["states"])  # province code per owner index
        self.mapped = set(self.codes)
        self.county_ids: list[str] = []
        self.prefecture_of: dict[str, str] = dict(zip(world["counties"], world.get("county_prefecture") or []))
        self.county_index = None
        if with_counties:
            keep = set(world["counties"])
            shapes = []
            for c in load_interim("cn_boundaries")["counties"]:
                if c["adcode"] in keep and c["rings"]:
                    self.county_ids.append(c["adcode"])
                    self.prefecture_of.setdefault(c["adcode"], c["prefecture"])
                    shapes.append(Shape([[self.proj(lon, lat) for lon, lat in ring] for ring in c["rings"]]))
            self.county_index = ShapeIndex(shapes, 60.0)
        if cities is None:
            path = WEB_DATA / "cities.json"
            cities = json.loads(path.read_text()) if path.exists() else []
        self.cities = {c["id"]: c for c in cities}

    # --- cells --------------------------------------------------------------------------------------------

    def land_cell(self, x: float, y: float, rings: int = 1) -> int:
        """The Chinese land cell (owner >= 0) at x/y, else the nearest one within `rings` rings; -1 if none."""
        cell = self.grid.cell_at(x, y)
        if cell < 0:
            return -1
        if self.owner[cell] >= 0:
            return cell
        seen, frontier = {cell}, [cell]
        for _ in range(rings):
            ring = [n for c in frontier for n in self.grid.neighbours(c) if n not in seen]
            seen.update(ring)
            land = [n for n in ring if self.owner[n] >= 0]
            if land:
                return min(land, key=lambda n: _d2(self.grid.center(*self.grid.col_row(n)), (x, y)))
            frontier = ring
        return -1

    def place(self, lon: float, lat: float, rings: int = 1):
        """(x, y, cell) for a lon/lat point; cell -1 when no Chinese land cell is within `rings` rings."""
        x, y = self.proj(lon, lat)
        return x, y, self.land_cell(x, y, rings)

    def settle(self, x: float, y: float, cell: int) -> tuple[float, float]:
        """Rounded x/y, moved to the cell centre when the point is not inside its cell (snapped offshore sites)."""
        x, y = round(x, 1), round(y, 1)
        if self.grid.cell_at(x, y) != cell:
            x, y = (round(v, 1) for v in self.grid.center(*self.grid.col_row(cell)))
        return x, y

    def covered_cells(self, shapes: list[Shape]) -> list[int]:
        """Chinese land cells whose centre lies inside any of the shapes (shapes in map km)."""
        g, out = self.grid, set()
        dy = 1.5 * g.radius
        for s in shapes:
            x0, y0, x1, y1 = s.bbox
            for row in range(max(0, int(y0 / dy) - 1), min(g.rows, int(y1 / dy) + 2)):
                for col in range(g.cols):
                    cx, cy = g.center(col, row)
                    if x0 <= cx <= x1 and y0 <= cy <= y1 and s.contains(cx, cy):
                        idx = g.index(col, row)
                        if self.owner[idx] >= 0:
                            out.add(idx)
        return sorted(out)

    # --- administrative lookups ----------------------------------------------------------------------------

    def county_at(self, x: float, y: float, cell: int | None = None) -> str | None:
        """County adcode at the point; just offshore, the hex's own county; None (e.g. Taiwan, abroad)."""
        if self.county_index is not None:
            k = self.county_index.locate(x, y)
            if k >= 0:
                return self.county_ids[k]
        if cell is not None and cell >= 0 and self.world["county"][cell] >= 0:
            return self.world["counties"][self.world["county"][cell]]
        return None

    def city_of(self, county: str | None) -> str | None:
        """cities.json id holding the county (its prefecture's adcode), or None."""
        if not county:
            return None
        pre = self.prefecture_of.get(county)
        return pre if pre in self.cities else None

    def province_of(self, county: str | None, cell: int, filed: str | None = None) -> str | None:
        """The filed province code if mapped, else the county's province, else the cell owner's."""
        if filed and str(filed)[:2] in self.mapped:
            return str(filed)[:2]
        if county and county[:2] in self.mapped:
            return county[:2]
        return self.codes[self.owner[cell]] if cell is not None and cell >= 0 and self.owner[cell] >= 0 else None

    # Legacy name used by the US-era builders.
    state_of = province_of
