"""resources builder (China): selection thresholds, naming, grouping, summary and its check()."""

import json
import tempfile
import unittest
from pathlib import Path

from pipeline.build import resources as res
from pipeline.core.hexgrid import HexGrid

GRID = HexGrid(10.0, 12, 10)


class FakeLocator:
    """lon/lat are map km; every cell is land of province 11, except column 0 (sea) and row 9 (Hong Kong, 81)."""

    def __init__(self):
        self.grid = GRID
        self.codes = ["11", "81"]
        self.owner = [-1 if GRID.col_row(i)[0] == 0 else (1 if GRID.col_row(i)[1] == 9 else 0) for i in range(GRID.size)]

    def land_cell(self, x, y, rings=1):
        c = GRID.cell_at(x, y)
        if c < 0:
            return -1
        if self.owner[c] >= 0:
            return c
        if rings < 1:
            return -1
        land = [n for n in GRID.neighbours(c) if self.owner[n] >= 0]
        return land[0] if land else -1

    def place(self, lon, lat, rings=1):
        return lon, lat, self.land_cell(lon, lat, rings)

    def settle(self, x, y, cell):
        return round(x, 1), round(y, 1)

    def county_at(self, x, y, cell=None):
        return "110101" if cell is not None and cell >= 0 and self.owner[cell] == 0 else None

    def province_of(self, county, cell, filed=None):
        if filed:
            return filed
        if county:
            return county[:2]
        return self.codes[self.owner[cell]] if cell >= 0 and self.owner[cell] >= 0 else None

    def city_of(self, county):
        return "110000" if county else None


class FakePlaces:
    def name(self, county, state, x, y):
        return "东城区"


def centre(col, row):
    return GRID.center(col, row)


def plant(fuel, mw, col=5, row=5, name=None, name_en="X plant", **kw):
    x, y = centre(col, row)
    return {"name": name, "name_en": name_en, "fuel": fuel, "capacity_mw": mw, "year": 2010, "year_last": 2015,
            "units": 2, "owner": "A Co", "lon": x, "lat": y, "area": "China", **kw}


class NamingTest(unittest.TestCase):
    def test_zh_name_prefers_the_plant_variant(self):
        self.assertEqual(res.zh_name("江阴利港2台100万千瓦机组扩建项目, 中信江苏利港发电厂"), "中信江苏利港发电厂")
        self.assertEqual(res.zh_name("三峡水电站"), "三峡水电站")
        self.assertIsNone(res.zh_name(None))

    def test_short_mine_drops_the_company(self):
        self.assertEqual(res.short_mine("中国神华能源股份有限公司哈尔乌素露天煤矿"), "哈尔乌素露天煤矿")
        self.assertEqual(res.short_mine("白云鄂博矿"), "白云鄂博矿")

    def test_owner_only_when_single(self):
        self.assertEqual(res._owner("Taiwan Power Co"), "Taiwan Power Co")
        self.assertIsNone(res._owner("A Co; B Co"))
        self.assertIsNone(res._owner(None))

    def test_field_kind(self):
        self.assertEqual(res.field_kind({"kind": "gas"}), "gas")
        self.assertEqual(res.field_kind({"kind": "oil_gas", "oil_bbl": 8, "production": 10}), "oil")
        self.assertEqual(res.field_kind({"kind": "oil_gas", "oil_bbl": 2, "production": 10}), "gas")
        self.assertEqual(res.field_kind({"kind": "oil_gas", "oil_bbl": None, "production": None}), "oil")


class SelectionTest(unittest.TestCase):
    def test_plant_thresholds_and_clusters(self):
        loc = FakeLocator()
        plants = [
            plant("hydro", 22500, name="三峡水电站"),
            plant("hydro", 999),  # below threshold
            plant("nuclear", 900, col=6, name="小核电站"),  # every nuclear plant counts
            plant("coal", 2999),
            plant("biomass", 400),  # never shown
            plant("gas", 7539, name_en="Datan power station", area="Taiwan"),
            plant("wind", 800, col=3), plant("wind", 800, col=3),  # summed per hex: 1,600 MW
            plant("wind", 1400, col=8),  # alone below the cluster threshold
        ]
        items, clusters = res.power_plants(loc, plants)
        by = {(it["type"], it["name"]) for it in items}
        self.assertIn(("hydro", "三峡水电站"), by)
        self.assertIn(("nuclear", "小核电站"), by)
        self.assertIn(("gas_plant", "大潭发电厂"), by)
        self.assertEqual(len(items), 3)
        self.assertEqual([(f, len(g)) for f, _, g in clusters], [("wind", 2)])
        gas = next(it for it in items if it["type"] == "gas_plant")
        self.assertEqual(gas["_filed"], "71")
        self.assertEqual(gas["name_en"], "Datan power station")
        named = res.cluster_items(clusters, FakePlaces(), loc)
        self.assertEqual(named[0]["name"], "东城区风电场群")
        self.assertEqual(named[0]["value"], 1600)

    def test_mines_are_grouped_per_hex_and_thresholded(self):
        loc = FakeLocator()
        x, y = centre(4, 4)
        rows = [
            {"name": "甲公司有限公司A煤矿", "commodity": "coal", "capacity": 35_000_000, "lon": x, "lat": y},
            {"name": "B煤矿", "commodity": "coal", "capacity": 12_000_000, "lon": x + 1, "lat": y},
            {"name": "C煤矿", "commodity": "coal", "capacity": 9_000_000, "lon": x, "lat": y},  # below
            {"name": "白云鄂博矿", "commodity": "rare_earths", "capacity": None, "lon": x, "lat": y},
        ]
        items = res.mines(loc, FakePlaces(), rows)
        coal = next(it for it in items if it["type"] == "coal_mine")
        self.assertEqual(coal["value"], 47_000_000)
        self.assertTrue(coal["name"].startswith("A煤矿等 2 处"))
        ree = next(it for it in items if it["type"] == "rare_earths")
        self.assertIsNone(ree["value"])  # unknown, never 0

    def test_offshore_field_avoids_the_sar_hex(self):
        loc = FakeLocator()
        # a point in the sea (column 0) next to the Hong Kong row snaps to mainland land
        x, y = centre(0, 9)
        rows = [{"name": "流花油田", "kind": "oil", "production": 20_000_000, "year": 2024, "lon": x, "lat": y}]
        items = res.fields(loc, FakePlaces(), rows)
        self.assertEqual(len(items), 1)
        self.assertEqual(loc.codes[loc.owner[items[0]["cell"]]], "11")
        self.assertIn("海上", items[0]["detail"])


class SummaryAndCheckTest(unittest.TestCase):
    def items(self):
        loc = FakeLocator()
        items, _ = res.power_plants(loc, [plant("nuclear", 1000, name="甲核电站")])
        x, y = centre(4, 4)
        items += res.fields(loc, FakePlaces(), [{"name": "乙油田", "kind": "oil", "production": 9_000_000,
                                                  "lon": x, "lat": y}])
        res.locate(items, loc)
        return items

    def test_summary_values(self):
        items = self.items()
        types = [t for t in res.TYPES if t["id"] in {it["type"] for it in items}]
        s = res.summarize(items, types)
        self.assertEqual(s["national"]["nuclear"], {"count": 1, "value": 1000, "unit": "MW"})
        self.assertIsNone(s["national"]["oil"]["value"])  # not additive
        self.assertEqual(set(s["states"]), {"11"})
        self.assertEqual(items[0]["cbsa"], "110000")
        self.assertNotIn("_filed", items[0])

    def test_check(self):
        items = self.items()
        doc = {"rule": res.RULE, "types": res.TYPES, "items": items}
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(res.check(Path(tmp))[0][0], "NOTE")
            p = Path(tmp) / "resources.json"
            p.write_text(json.dumps(doc))
            self.assertEqual([lv for lv, _ in res.check(Path(tmp))], ["PASS"])
            items[0]["value"] = 0
            items[0]["state"] = "99"
            p.write_text(json.dumps(doc | {"rule": ""}))
            levels = [lv for lv, _ in res.check(Path(tmp))]
            self.assertEqual(levels.count("FAIL"), 2)  # state, zero (rule/name checks need the production grid)


if __name__ == "__main__":
    unittest.main()
