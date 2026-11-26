"""cities builder helpers (build/cities.py), the yield mapping (build/yields.py) and growth (build/growth.py)."""

import tempfile
import unittest

from pipeline.build import cities, growth, yields
from pipeline.core.hexgrid import HexGrid


def stats_ind(n=10, va=10, emp=0):
    """n banner cities; the first `va` have three-sector value added, the first `emp` employment by industry."""
    ids = [f"{i + 13:02d}0100" for i in range(n)]
    sc, ic = {}, {}
    for k, cid in enumerate(ids):
        sc[cid] = {"gdp": 1e11 * (k + 1), "gdp_year": 2023, "pop": 1e6, "pop_year": 2023, "pop_census": 9.8e5,
                   "va_primary": 1e10 if k < va else None, "va_secondary": 4e10 if k < va else None,
                   "va_tertiary": 5e10 if k < va else None}
        e = dict.fromkeys("ABCDEFGHIJKLMNOPQRS")
        if k < emp:
            e = {s: 100 for s in e}
        ic[cid] = {"emp": e, "emp_total": 1900 if k < emp else None, "rd_expenditure": None}
    stats = {"year": 2023, "cities": sc, "nation": {}}
    ind = {"year": 2024, "basis_label": "城镇非私营单位就业人员年末人数", "cities": ic, "nation": {}}
    return ids, stats, ind


class YieldBasisTest(unittest.TestCase):
    def test_value_added_wins_at_90_percent(self):
        ids, stats, ind = stats_ind(10, va=9)
        b = yields.choose_bases(stats, ind, ids)
        self.assertEqual(b["food"], ("cny", "va_primary"))
        self.assertEqual(b["production"], ("cny", "va_secondary"))
        self.assertEqual(b["gold"], ("cny", "gdp"))
        self.assertIsNone(b["science"])
        self.assertIsNone(b["culture"])

    def test_below_90_percent_is_null_everywhere(self):
        ids, stats, ind = stats_ind(10, va=7)
        b = yields.choose_bases(stats, ind, ids)
        self.assertIsNone(b["food"])
        defs = {y["key"]: y for y in yields.yield_defs(stats, ind, ids, b)}
        self.assertIsNone(defs["food"]["basis"])
        self.assertIn(f"{yields.BASIS_SHARE:.0%}", defs["food"]["description"])
        self.assertEqual(defs["food"]["unit"], "persons")  # priority-1 unit when null
        self.assertEqual(defs["gold"]["unit"], "cny")

    def test_employment_has_priority(self):
        ids, stats, ind = stats_ind(10, va=10, emp=10)
        b = yields.choose_bases(stats, ind, ids)
        self.assertEqual(b["food"], ("persons", ["A"]))
        self.assertEqual(b["culture"], ("persons", ["R"]))
        self.assertEqual(yields.basis_value(b["production"], stats["cities"][ids[0]], ind["cities"][ids[0]]), 400)

    def test_employment_sum_needs_every_section(self):
        rec = {"emp": {"I": 5, "M": None}}
        self.assertIsNone(yields.basis_value(("persons", ["I", "M"]), {}, rec))
        self.assertEqual(yields.basis_value(("persons", ["I"]), {}, rec), 5)

    def test_gold_only_for_the_stats_year(self):
        b = {"gold": ("cny", "gdp")}
        self.assertIsNone(yields.yields_of(b, {"gdp": 5.0, "gdp_year": 2022}, {}, 2023)["gold"])
        self.assertEqual(yields.yields_of(b, {"gdp": 5.0, "gdp_year": 2023}, {}, 2023)["gold"], 5.0)


class GrowthTest(unittest.TestCase):
    def test_rate(self):
        self.assertEqual(growth.rate(102, 100), 0.02)
        self.assertIsNone(growth.rate(None, 100))
        self.assertIsNone(growth.rate(100, None))
        self.assertIsNone(growth.rate(200, 100))  # beyond the sanity cap
        self.assertEqual(growth.window(2023), [2020, 2023])
        self.assertIsNone(growth.window(None))


class EconomyTest(unittest.TestCase):
    def test_other_year_is_null_and_split_needs_all_three(self):
        b = {k: None for k in yields.YIELD_KEYS} | {"gold": ("cny", "gdp")}
        e = cities.economy({"pop": 5, "pop_year": 2022, "pop_census": 4, "gdp": 10.0, "gdp_year": 2023,
                            "va_primary": 1.0, "va_secondary": None, "va_tertiary": 9.0}, None, b, 2023, [2020, 2023])
        self.assertIsNone(e["population"])
        self.assertIsNone(e["population_year"])
        self.assertIsNone(e["growth"])
        self.assertIsNone(e["gdp_by_industry"])
        self.assertEqual(e["gdp"], 10.0)
        self.assertEqual(e["yields"]["gold"], 10.0)
        self.assertIsNone(e["jobs"])
        self.assertEqual(e["currency"], "CNY")


class PlacementTest(unittest.TestCase):
    def setUp(self):
        self.grid = HexGrid(radius=25.0, cols=6, rows=4)
        n = self.grid.size
        self.owner = [0] * n
        self.owner[0] = -1  # water
        self.owner[5] = 1   # the SAR's hex

    def city(self, cid, cell, kind="city", state="44"):
        x, y = self.grid.center(*self.grid.col_row(cell))
        return {"id": cid, "name": cid, "kind": kind, "states": [state], "x": x, "y": y,
                "population": 1, "pop_census_2020": 1}

    def test_sar_first_unique_cells_and_snap(self):
        rows = [self.city("440300", 5), self.city("810000", 5, "sar", "81"), self.city("440100", 0),
                self.city("440400", 4)]
        log = cities.place_banners(rows, self.grid, self.owner, {"44": 0, "81": 1})
        cells = {c["id"]: c["cell"] for c in rows}
        self.assertEqual(cells["810000"], 5)
        self.assertNotEqual(cells["440300"], 5)
        self.assertEqual(self.owner[cells["440300"]], 0)
        self.assertGreaterEqual(self.owner[cells["440100"]], 0)
        self.assertEqual(len(set(cells.values())), 4)
        self.assertIn("440100", log["snapped"])

    def test_footprints_majority(self):
        world = {"county_prefecture": ["440300", "440100"], "owner": [0, 0, -1],
                 "county_samples": [[0, 0, 0, 0, 1, 1, 1], [0, 0, 0, 1, 1, 1, 1], []]}
        self.assertEqual(cities.footprints(world), {"440300": {0}, "440100": {1}})


class RanksTest(unittest.TestCase):
    def test_nulls_unranked_and_peers(self):
        rows = []
        for i in range(8):
            rows.append({"id": f"44{i:02d}00", "states": ["44"], "x": float(i), "y": 0.0,
                         "population": (8 - i) * 1e6 if i < 6 else None, "pop_census_2020": (8 - i) * 1e6,
                         "gdp": 1e11 if i != 2 else None, "growth": None,
                         "yields": {k: None for k in yields.YIELD_KEYS}})
        for i in range(6):
            rows.append({"id": f"71{i:02d}00", "states": ["71"], "x": 100.0 + i, "y": 0.0, "population": None,
                         "pop_census_2020": None, "gdp": None, "growth": None,
                         "yields": {k: None for k in yields.YIELD_KEYS}})
        cities.add_ranks(rows)
        self.assertEqual(rows[0]["ranks"]["population"], 1)
        # no 2023 figure: ranked by the 2020 census instead of dropping out; no figure at all: unranked
        self.assertEqual(rows[6]["ranks"]["population"], 7)
        self.assertIsNone(rows[8]["ranks"]["population"])
        self.assertIsNone(rows[2]["ranks"]["gdp"])
        self.assertIsNone(rows[0]["ranks"]["growth"])
        self.assertEqual(sorted(rows[8]["peers"]), sorted(r["id"] for r in rows[8:] if r is not rows[8]))
        self.assertEqual(len(rows[0]["peers"]), 5)
        self.assertTrue(all(p.startswith("44") for p in rows[0]["peers"]))


class CapitalTest(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(cities.capital_label("110000", "11"), "首都")
        self.assertEqual(cities.capital_label("440100", "44"), "省会")
        self.assertEqual(cities.capital_label("450100", "45"), "首府")
        self.assertEqual(cities.capital_label("710100", "71"), "省会")
        self.assertIsNone(cities.capital_label("440300", "44"))
        self.assertIsNone(cities.capital_label("310000", "31"))

    def test_check_without_file_is_a_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(cities.check(tmp)[0][0], "NOTE")


if __name__ == "__main__":
    unittest.main()
