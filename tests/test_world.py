"""World builder helpers (build/world.py) and province helpers (build/states.py), China edition."""

import unittest
from collections import Counter

from pipeline.build import states, world
from pipeline.core.hexgrid import HexGrid


class ValleyFloorTest(unittest.TestCase):
    def test_flat_lowland_with_a_range_at_the_edge(self):
        self.assertTrue(world.is_valley_floor([20, 25, 30, 35, 40, 45, 50, 900, 1500, 2200]))

    def test_real_mountains(self):
        self.assertFalse(world.is_valley_floor([800, 1100, 1400, 1700, 2000, 2300, 2600, 2900]))

    def test_too_few_samples(self):
        self.assertFalse(world.is_valley_floor([10, 20, 1500]))


class CoverTest(unittest.TestCase):
    def test_urban_threshold_wins(self):
        c = Counter({"developed": world.URBAN_MIN_PCT, "cropland": 100 - world.URBAN_MIN_PCT})
        self.assertEqual(world.dominant_cover(c, 100), world.URBAN_ID)

    def test_forest_type(self):
        c = Counter({"forest_evergreen": 30, "forest_mixed": 25, "cropland": 40})
        self.assertEqual(world.dominant_cover(c, 95), 2)  # forest (55) beats cropland; evergreen is the largest type

    def test_largest_group(self):
        self.assertEqual(world.dominant_cover(Counter({"barren": 60, "grassland": 40}), 100), 10)
        self.assertEqual(world.dominant_cover(Counter({"pasture": 60, "cropland": 40}), 100), 6)

    def test_no_land(self):
        self.assertEqual(world.dominant_cover(Counter({"water": 100}), 0), 0)

    def test_cover_ids_match_contract(self):
        from pipeline import check

        for cid, key, *_ in world.COVER:
            self.assertEqual(check.COVER_KEYS[cid], key)


class GeometryTest(unittest.TestCase):
    def test_clip_ring_rect(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
        out = world.clip_ring_rect(square, 5, -1, 20, 20)
        self.assertEqual(sorted(out), sorted([(5, 0), (10, 0), (10, 10), (5, 10)]))
        self.assertEqual(world.clip_ring_rect(square, 20, 20, 30, 30), [])

    def test_dash_centerline_follows_the_band(self):
        # A thin curved band from (0, 10) down to (0, 0), bulging to x = 2.
        left = [(-0.1 + 2 * (1 - abs(5 - y) / 5), y) for y in range(10, -1, -1)]
        right = [(0.1 + 2 * (1 - abs(5 - y) / 5), y) for y in range(0, 11)]
        dash = {"rings": [left + right + [left[0]]], "line": [[0, 10], [0, 0]]}
        line = world.dash_centerline(dash, n=10)
        # The band's ends are square (two corners each), so the end points may sit half a band width off.
        self.assertAlmostEqual(line[0][0], 0.0, delta=0.15)
        self.assertAlmostEqual(line[-1][1], 0.0, delta=0.15)
        mid = min(line, key=lambda p: abs(p[1] - 5))
        self.assertAlmostEqual(mid[0], 2.0, delta=0.05)

    def test_dash_centerline_without_ring(self):
        self.assertEqual(world.dash_centerline({"rings": [], "line": [[1, 2], [3, 4]]}), [(1, 2), (3, 4)])

    def test_production_grid(self):
        self.assertTrue(world.is_production_grid({"radius": 25.0, "cols": 128, "rows": 120}))
        self.assertFalse(world.is_production_grid({"radius": 25.0, "cols": 3, "rows": 2}))


class ProvinceTest(unittest.TestCase):
    def test_text_em(self):
        self.assertEqual(states.text_em("内蒙古"), 3.0)
        self.assertAlmostEqual(states.text_em("AB"), 1.2)

    def test_label_anchor(self):
        g = HexGrid(25.0, 12, 10)
        owner = [0 if 2 <= g.col_row(i)[0] <= 9 and 2 <= g.col_row(i)[1] <= 7 else -1 for i in range(g.size)]
        lab = states.label_anchor(g, owner, 0, "新疆")
        self.assertIsNotNone(lab)
        self.assertGreaterEqual(lab["size"], states.MIN_LABEL_KM)
        self.assertIsNone(states.label_anchor(g, owner, 1, "澳门"))

    def test_basis_value(self):
        ind = {"emp": {"A": 5, "B": 1, "C": 2, "D": None, "E": 4}, "rd_expenditure": 9.0}
        self.assertEqual(states.basis_value(("persons", ["A"]), {}, ind), 5)
        self.assertIsNone(states.basis_value(("persons", ["B", "C", "D", "E"]), {}, ind), "a null section nulls the sum")
        self.assertEqual(states.basis_value(("cny", "rd_expenditure"), {}, ind), 9.0)
        self.assertEqual(states.basis_value(("cny", "va_primary"), {"va_primary": 3}, ind), 3)
        self.assertIsNone(states.basis_value(None, {"gdp": 1}, ind))

    def test_local_bases_need_ninety_percent(self):
        ids = [str(k) for k in range(10)]
        stats = {"cities": {i: {"gdp": 1, "va_primary": 1 if int(i) < 9 else None, "va_secondary": 1 if int(i) < 7 else None}
                            for i in ids}}
        ind = {"cities": {i: {"emp": {}} for i in ids}}
        b = states.local_bases(stats, ind, ids)
        self.assertEqual(b["food"], ("cny", "va_primary"))  # 9/10
        self.assertIsNone(b["production"])  # 7/10 < 75%
        self.assertEqual(b["gold"], ("cny", "gdp"))
        self.assertIsNone(b["culture"])

    def test_economy_keeps_only_the_stats_year(self):
        rec = {"pop": 110, "pop_year": 2022, "pop_census": 100, "gdp": 5.0, "gdp_year": 2023}
        e = states.economy(rec, None, {"gold": ("cny", "gdp")}, 2023)
        self.assertIsNone(e["population"])
        self.assertIsNone(e["growth"])
        self.assertEqual(e["gdp"], 5.0)
        self.assertEqual(e["yields"]["gold"], 5.0)
        self.assertIsNone(e["jobs"])
        e = states.economy({**rec, "pop_year": 2023}, None, {}, 2023)
        self.assertEqual(e["growth"], 0.1)


if __name__ == "__main__":
    unittest.main()
