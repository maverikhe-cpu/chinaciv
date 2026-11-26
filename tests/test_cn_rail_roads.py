"""cn_rail_roads: Overpass element classification, chain merging, parallel-line dedupe, simplification."""

import unittest

from pipeline.sources import cn_rail_roads as rr


def way(i, coords, **tags):
    return {"type": "W", "id": i, "geometry": {"type": "LineString", "coordinates": coords},
            "tags": {k: tags.get(k, "") for k in rr.TAGS}}


class ParseTest(unittest.TestCase):
    def test_parse_speed(self):
        self.assertEqual(rr.parse_speed("350"), 350)
        self.assertEqual(rr.parse_speed("120 km/h"), 120)
        self.assertEqual(rr.parse_speed("80;160"), 160)
        self.assertIsNone(rr.parse_speed(""))
        self.assertIsNone(rr.parse_speed(None))
        self.assertIsNone(rr.parse_speed("signals"))
        self.assertIsNone(rr.parse_speed("60 mph"))

    def test_query_has_three_classes_and_overpass_bbox_order(self):
        q = rr.overpass_query((114, 21, 117, 24))
        self.assertIn("(21,114,24,117)", q)
        for frag in ('"highspeed"="yes"', '"usage"="main"', '"highway"="motorway"', "convert W", "out geom"):
            self.assertIn(frag, q)
        self.assertEqual(rr.tile_name((114, 21, 115.5, 22.5)), "114_21_115.5_22.5")
        self.assertEqual(len(rr.quarter((0, 0, 2, 2))), 4)


class ClassifyTest(unittest.TestCase):
    def test_classes_and_line_speed(self):
        L = [[116.0, 39.0], [116.1, 39.1]]
        els = [
            way(1, L, railway="rail", highspeed="yes", usage="main", name="京沪高铁", maxspeed="80"),   # station section
            way(2, L, railway="rail", highspeed="yes", usage="main", name="京沪高铁", maxspeed="350"),
            way(3, L, railway="rail", highspeed="yes", usage="main", name="某城际", maxspeed="200"),
            way(4, L, railway="rail", highspeed="yes", usage="main"),                                   # unknown speed
            way(5, L, railway="rail", usage="main", name="京广线", maxspeed="120"),
            way(6, L, railway="rail", usage="branch", name="支线"),                                     # not queried class
            way(7, L, railway="rail", highspeed="yes", usage="branch", name="京南动车段走行线"),        # depot link
            way(8, L, highway="motorway", ref="G4", name="京港澳高速"),
            way(8, L, highway="motorway", ref="G4", name="京港澳高速"),                                 # duplicate id
            way(9, [[116.0, 39.0]], highway="motorway"),                                                # degenerate
        ]
        got = rr.classify(els)
        by = [(c, a.get("name"), a.get("speed"), a.get("highspeed")) for c, a, _ in got]
        self.assertIn(("hsr", "京沪高铁", 350, None), by)
        self.assertEqual(sum(1 for c, n, *_ in by if n == "京沪高铁"), 2)  # both sections inherit 350
        self.assertIn(("rail", "某城际", 200, True), by)
        self.assertIn(("hsr", None, None, None), by)
        self.assertIn(("rail", "京广线", None, False), by)
        self.assertNotIn("支线", [n for _, n, *_ in by])
        self.assertFalse(any(n and "动车段" in n for _, n, *_ in by))
        exp = [a for c, a, _ in got if c == "expressway"]
        self.assertEqual(exp, [{"ref": "G4", "name": "京港澳高速"}])


class GeometryTest(unittest.TestCase):
    def test_merge_chains_joins_degree_two_ends_only(self):
        a = [(0, 0), (1, 0)]
        b = [(2, 0), (1, 0)]  # reversed
        c = [(2, 0), (3, 0)]
        d = [(1, 0), (1, 1)]  # makes (1, 0) a junction of degree 3
        merged = rr.merge_chains([a, b, c])
        self.assertEqual(len(merged), 1)
        self.assertEqual(sorted([merged[0][0], merged[0][-1]]), [(0, 0), (3, 0)])
        self.assertEqual(len(merged[0]), 4)
        self.assertEqual(len(rr.merge_chains([a, b, c, d])), 3)

    def test_douglas_peucker(self):
        pts = [(x * 0.5, 0.01 * (x % 2)) for x in range(41)]  # 20 km nearly straight
        self.assertEqual(rr.douglas_peucker(pts, 2.0), [pts[0], pts[-1]])
        bent = [(0, 0), (10, 5), (20, 0)]
        self.assertEqual(rr.douglas_peucker(bent, 2.0), bent)

    def test_dedupe_drops_parallel_carriageway_keeps_branch(self):
        north = [(0.0, 0.0), (50.0, 0.0)]
        south = [(0.0, 0.05), (50.0, 0.05)]       # 50 m apart: the other carriageway
        branch = [(25.0, 0.0), (25.0, 30.0)]      # leaves the trunk
        runs = rr.dedupe_runs([north, south, branch])
        idx = [i for i, _ in runs]
        self.assertEqual(idx.count(1), 0)
        self.assertIn(0, idx)
        self.assertIn(2, idx)
        br = [r for i, r in runs if i == 2][0]
        self.assertGreater(rr.length_km(br), 27)
        self.assertLess(br[0][1], 2.0)  # still touches the trunk

    def test_dedupe_respects_clip(self):
        line = [(0.0, 0.0), (100.0, 0.0)]
        runs = rr.dedupe_runs([line], inside=lambda x, y: x < 40 or x > 60)
        self.assertEqual(len(runs), 2)
        for _, r in runs:
            self.assertTrue(all(x <= 40 or x >= 60 for x, _ in r))


if __name__ == "__main__":
    unittest.main()
