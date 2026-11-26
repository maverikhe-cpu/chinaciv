"""infrastructure builder (China): hex rasterisation of lines, site placement rules and its check()."""

import json
import tempfile
import unittest
from pathlib import Path

from pipeline.build import infrastructure as infra
from pipeline.core.hexgrid import HexGrid

GRID = HexGrid(10.0, 12, 10)


class IdentityProj:
    """lon/lat are already map km in these tests."""

    def __call__(self, lon, lat):
        return lon, lat


def centre(cell):
    return GRID.center(*GRID.col_row(cell))


class HexLineTest(unittest.TestCase):
    def test_walk_steps_are_neighbours_and_ends_are_kept(self):
        for a, b in [(0, GRID.size - 1), (13, 58), (5, 5), (40, 41), (GRID.index(11, 0), GRID.index(0, 9))]:
            path = infra.hex_walk(GRID, a, b)
            self.assertEqual(path[0], a)
            self.assertEqual(path[-1], b)
            self.assertEqual(len(path), infra.hex_distance(GRID, a, b) + 1)
            for u, v in zip(path, path[1:]):
                self.assertIn(v, GRID.neighbours(u))

    def test_straighten_drops_corner_clips(self):
        # Row 2 → row 3 → row 2 along a row boundary: the row-3 cell only clips a corner.
        a, b, c = GRID.index(2, 2), GRID.index(2, 3), GRID.index(3, 2)
        self.assertIn(c, GRID.neighbours(a))
        self.assertEqual(infra.straighten(GRID, [a, b, c]), [a, c])

    def test_horizontal_line_along_row_boundary_does_not_zigzag(self):
        # y halfway between rows 2 and 3: plain sampling alternates between the two rows.
        y = (centre(GRID.index(0, 2))[1] + centre(GRID.index(0, 3))[1]) / 2
        runs = infra.sample_cells(GRID, [(1.0, y), (180.0, y)])
        links = {l for run in runs for l in infra.links_of(GRID, run)}
        cells = {c for l in links for c in l}
        rows = [GRID.col_row(c)[1] for c in cells]
        self.assertGreater(len(links), 5)
        # Each column is crossed once: no up-down sawtooth.
        self.assertLessEqual(len(links), len(cells) - 1)
        self.assertTrue(set(rows) <= {2, 3})

    def test_keep_breaks_runs(self):
        y = centre(GRID.index(0, 4))[1]
        runs = infra.sample_cells(GRID, [(1.0, y), (180.0, y)], keep=lambda c: not 4 <= GRID.col_row(c)[0] <= 5)
        self.assertEqual(len(runs), 2)


class RasteriseTest(unittest.TestCase):
    def setUp(self):
        self.owner = [0] * GRID.size
        self.foreign = GRID.index(9, 5)
        self.owner[self.foreign] = -2
        self.y5 = centre(GRID.index(0, 5))[1]
        self.y7 = centre(GRID.index(0, 7))[1]

    def test_expressways_names_and_land_rules(self):
        data = {"expressway": [
            {"ref": "S12;G4", "name": "京港澳高速", "line": [(1.0, self.y5), (190.0, self.y5)]},
            {"ref": None, "name": "机场高速", "line": [(1.0, self.y7), (100.0, self.y7)]},
            {"ref": "G1501", "name": None, "line": [(1.0, self.y5), (60.0, self.y5)]},
        ]}
        roads = infra.build_roads(GRID, IdentityProj(), self.owner, data)
        pairs = [(a, b) for a, b, *_ in roads]
        self.assertEqual(len(pairs), len(set(pairs)))
        for a, b, cls, name in roads:
            self.assertLess(a, b)
            self.assertIn(b, GRID.neighbours(a))
            self.assertNotIn(self.foreign, (a, b))
            self.assertEqual(cls, 0)
        row5 = {r[3] for r in roads if GRID.col_row(r[0])[1] == 5 and GRID.col_row(r[1])[1] == 5}
        self.assertEqual(row5, {"G4"}, "G4 beats S12 and G1501 on shared links")
        row7 = {r[3] for r in roads if GRID.col_row(r[0])[1] == 7}
        self.assertEqual(row7, {"机场高速"})

    def test_hsr_wins_over_conventional_and_200kmh_counts_as_hsr(self):
        data = {"hsr": [{"name": "京沪高速线", "line": [(1.0, self.y5), (80.0, self.y5)]}],
                "rail": [{"name": "京沪线", "highspeed": False, "line": [(1.0, self.y5), (150.0, self.y5)]},
                         {"name": "快速线", "highspeed": True, "line": [(1.0, self.y7), (80.0, self.y7)]}]}
        rail = infra.build_rail(GRID, IdentityProj(), self.owner, data)
        by_name = {}
        for a, b, tier, name in rail:
            by_name.setdefault(name, set()).add(tier)
        self.assertEqual(by_name["京沪高速线"], {0})
        self.assertEqual(by_name["京沪线"], {1})
        self.assertEqual(by_name["快速线"], {0})
        self.assertEqual(len({(a, b) for a, b, *_ in rail}), len(rail))

    def test_water_only_links_dropped(self):
        owner = [-1] * GRID.size
        rail = infra.build_rail(GRID, IdentityProj(), owner,
                                {"rail": [{"name": "x", "line": [(1.0, self.y5), (150.0, self.y5)]}]})
        self.assertEqual(rail, [])

    def test_route_key_and_label(self):
        self.assertEqual(min(["S1", "G1501", "G4", "G45"], key=infra.route_key), "G4")
        self.assertEqual(infra.road_label({"ref": " ", "name": "沈海高速"}), "沈海高速")
        self.assertIsNone(infra.road_label({"ref": None, "name": None}))


class StubLocator:
    def __init__(self, cell):
        self.cell = cell
        self.world = {"counties": ["110101", "130209"]}

    def place(self, lon, lat, rings=1):
        return lon, lat, self.cell

    def county_at(self, x, y, cell=None):
        return "110101"

    def city_of(self, county):
        return "110000" if county and county.startswith("11") else None

    def province_of(self, county, cell, filed=None):
        return county[:2] if county else filed


class SitesTest(unittest.TestCase):
    def test_airport_rule_and_hubs(self):
        loc = StubLocator(GRID.index(3, 3))
        air = {"year": 2025, "airports": [
            {"name": "大", "iata": "AAA", "icao": "ZAAA", "passengers": 12_000_000, "lon": 1, "lat": 2, "county": "130209"},
            {"name": "中", "iata": None, "icao": "ZBBB", "passengers": 3_000_000, "lon": 1, "lat": 2, "county": "999999"},
            {"name": "小", "iata": "CCC", "icao": None, "passengers": 1_000_000, "passengers_prev": None, "lon": 1, "lat": 2},
            {"name": "微", "iata": "DDD", "passengers": 999_999, "lon": 1, "lat": 2},
            {"name": "空", "iata": "EEE", "passengers": None, "lon": 1, "lat": 2}]}
        dropped = []
        out = infra.build_airports(loc, air, dropped)
        self.assertEqual([a["id"] for a in out], ["AAA", "ZBBB", "CCC"])
        self.assertEqual([a["hub"] for a in out], ["L", "M", "S"])
        self.assertEqual(out[0]["county"], "130209")
        self.assertEqual(out[1]["county"], "110101", "unknown source county falls back to the polygon lookup")
        self.assertEqual(out[1]["cbsa"], "110000")
        self.assertIsNone(out[2]["passengers_prev"], "null stays null")
        self.assertEqual(dropped, [])

    def test_ports_top_n_and_offshore_drop(self):
        ports = {"year": 2025, "ports": [{"id": f"p{i}", "name": f"港{i}", "kind": "coastal", "cargo_t": 1000 - i,
                                          "rank": i + 1, "lon": 0, "lat": 0} for i in range(40)]}
        ports["ports"].append({"id": "nil", "name": "无", "cargo_t": None, "lon": 0, "lat": 0})
        out = infra.build_ports(StubLocator(5), ports, [])
        self.assertEqual(len(out), infra.PORT_TOP_N)
        self.assertEqual(out[0]["id"], "p0")
        dropped = []
        self.assertEqual(infra.build_ports(StubLocator(-1), ports, dropped), [])
        self.assertEqual(len(dropped), infra.PORT_TOP_N)


def write_world(web, owner=None):
    owner = owner if owner is not None else [0] * GRID.size
    (web / "world.json").write_text(json.dumps({
        "grid": GRID.to_dict(), "owner": owner, "counties": [{"geoid": "110101", "name": "东城区"}],
        "states": [{"fips": "11"}]}))
    (web / "cities.json").write_text(json.dumps([{"id": "110000"}]))


def good_doc():
    a = GRID.index(3, 3)
    b = GRID.neighbours(a)[0]
    x, y = centre(a)
    return {
        "version": infra.VERSION, "vintage": {"roads": "v"}, "sources": ["cn_rail_roads"], "rules": infra.RULES,
        "road_classes": infra.ROAD_CLASSES, "rail_tiers": infra.RAIL_TIERS, "hubs": infra.HUBS,
        "roads": [[min(a, b), max(a, b), 0, "G4"]], "rail": [[min(a, b), max(a, b), 1, None]],
        "ports": [{"id": "p", "name": "P", "type": "coastal", "cargo_t": 5, "foreign_t": None, "teu": 0, "rank": 1,
                   "x": x, "y": y, "cell": a, "county": "110101", "cbsa": "110000", "state": "11"}],
        "airports": [{"id": "AAA", "hub": "S", "passengers": 1_200_000, "passengers_prev": None, "cargo_t": None,
                      "movements": None, "x": x + 3, "y": y, "cell": a, "county": None, "cbsa": None, "state": "11"}],
    }


class CheckTest(unittest.TestCase):
    def run_check(self, doc, owner=None):
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp)
            write_world(web, owner)
            if doc is not None:
                (web / "infrastructure.json").write_text(json.dumps(doc))
            return infra.check(web)

    def levels(self, lines):
        return {lv for lv, _ in lines}

    def test_missing_file_is_a_note(self):
        lines = self.run_check(None)
        self.assertEqual(self.levels(lines), {"NOTE"})

    def test_good_file_passes(self):
        lines = self.run_check(good_doc())
        self.assertNotIn("FAIL", self.levels(lines), lines)
        self.assertIn("PASS", self.levels(lines))

    def test_violations_fail(self):
        def non_neighbour(d):
            d["roads"][0][1] = d["roads"][0][0] + 5

        def dup(d):
            d["rail"].append(list(d["rail"][0]))

        def water_site(d):
            d["ports"][0]["cell"] = 0

        def bad_cbsa(d):
            d["airports"][0]["cbsa"] = "999999"

        def bad_state(d):
            d["ports"][0]["state"] = "粤"

        def small_airport(d):
            d["airports"][0]["passengers"] = 10

        def null_metric(d):
            d["ports"][0]["cargo_t"] = None

        def missing_key(d):
            del d["airports"]

        owner = [0] * GRID.size
        owner[0] = -1
        for change in (non_neighbour, dup, water_site, bad_cbsa, bad_state, small_airport, null_metric, missing_key):
            doc = good_doc()
            change(doc)
            with self.subTest(change.__name__):
                self.assertIn("FAIL", self.levels(self.run_check(doc, owner)))
        doc = good_doc()
        owner = [0] * GRID.size
        owner[doc["rail"][0][1]] = -2
        self.assertIn("FAIL", self.levels(self.run_check(doc, owner)))


if __name__ == "__main__":
    unittest.main()
