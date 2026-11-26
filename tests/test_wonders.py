"""Wonders (China): the four published-list rules (UNESCO, 国家公园, first-batch 5A minus WHS, 一流大学建设高校)
and wonders.json's check()."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline.build import wonders

from test_check import dataset


def fake_place(loc, lon, lat, filed=None, rings=2):
    if lon is None:
        return None
    return {"x": 1.0, "y": 1.0, "cell": 0, "county": "110101", "cbsa": "110000", "state": filed or "11"}


def whs(whc_id, name, category="cultural", inscribed=2000, provinces=("11",), area=100.0, lon=116.0):
    return {"whc_id": whc_id, "name": name, "name_en": name, "category": category, "inscribed": inscribed,
            "criteria": "(i)", "area_ha": area, "transnational": False, "lon": lon, "lat": 40.0,
            "province": provinces[0], "provinces": list(provinces)}


class RuleTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(wonders, "_place", fake_place)
        p.start()
        self.addCleanup(p.stop)

    def test_unesco_kinds_order_nulls_and_unplaced(self):
        sites = [whs(1111, "后", "natural", 2013, area=0.0), whs(437, "泰山", "mixed", 1987),
                 whs(438, "长城", "cultural", 1987, ("11", "13", "62")), whs(9, "无点", lon=None)]
        unplaced = []
        out = wonders.unesco(None, sites, unplaced)
        self.assertEqual([w["id"] for w in out], ["whc-437", "whc-438", "whc-1111"])  # by year, then WHC id
        self.assertEqual([w["rank"] for w in out], [1, 2, 3])
        self.assertEqual([w["kind"] for w in out], ["natural", "monument", "natural"])  # mixed → natural
        self.assertIsNone(out[2]["metrics"]["area_ha"])  # UNESCO's 0 means unrecorded: null, never 0
        self.assertIn("1987年列入", out[1]["desc"])
        self.assertIn("北京、河北、甘肃", out[1]["desc"])
        self.assertEqual([u["name"] for u in unplaced], ["无点"])

    def test_forbidden_city_point_moves_to_beijing(self):
        sites = [whs(439, "明清故宫（北京故宫、沈阳故宫）", provinces=("21", "11"))]
        scenic = [{"name": "故宫博物院", "lon": 116.39, "lat": 39.92, "province": "11", "geo": "wikidata"}]
        out = wonders.unesco(None, sites, [], scenic)
        self.assertEqual(out[0]["state"], "11")
        self.assertTrue(out[0]["point_note"])
        self.assertEqual(out[0]["short"], "明清故宫")

    def test_parks_by_area_with_desc(self):
        parks = [{"name": "武夷山国家公园", "established": "2021-10-12", "area_km2": 1280, "lon": 1, "lat": 1,
                  "province": "35", "provinces": ["35", "36"], "geo": "wikidata"},
                 {"name": "三江源国家公园", "established": "2021-10-12", "area_km2": 190700, "lon": 1, "lat": 1,
                  "province": "63", "provinces": ["63"], "geo": "wikidata"}]
        out = wonders.national_parks(None, parks, [])
        self.assertEqual([w["id"] for w in out], ["np-sanjiangyuan", "np-wuyishan"])
        self.assertEqual(out[0]["short"], "三江源")
        self.assertIn("2021年10月12日", out[0]["desc"])
        self.assertIn("19.07万平方千米", out[0]["desc"])
        self.assertIn("福建、江西", out[1]["desc"])
        self.assertTrue(all(w["kind"] == "natural" and w["cells"] is None for w in out))

    def test_scenic_first_batch_minus_world_heritage(self):
        areas = [{"name": "泰安市泰山景区", "batch": 1, "designated": 2007, "lon": 1, "lat": 1, "province": "37"},
                 {"name": "武汉市黄鹤楼公园", "batch": 1, "designated": 2007, "lon": 1, "lat": 1, "province": "42",
                  "geo": "wikidata", "wikidata": "Q1"},
                 {"name": "天津古文化街旅游区（津门故里）", "batch": 1, "designated": 2007, "lon": 1, "lat": 1,
                  "province": "12", "geo": "prefecture_seat"},
                 {"name": "小三峡－小小三峡旅游区", "batch": 1, "designated": 2007, "lon": None, "lat": None,
                  "province": "50"},
                 {"name": "后来的景区", "batch": None, "designated": 2015, "lon": 1, "lat": 1, "province": "11"}]
        unplaced = []
        with mock.patch.object(wonders, "FIVE_A_IN_WHS", {"泰安市泰山景区": 437}):
            out, in_whs = wonders.scenic_5a(None, areas, {437}, unplaced)
        self.assertEqual(in_whs, ["泰安市泰山景区"])
        self.assertEqual([w["short"] for w in out], ["天津古文化街", "黄鹤楼"])  # by province code
        self.assertEqual(out[1]["id"], "5a-Q1")
        self.assertTrue(out[0]["approx"])
        self.assertIn("近似", out[0]["desc"])
        self.assertFalse(out[1]["approx"])
        self.assertEqual([u["name"] for u in unplaced], ["小三峡－小小三峡旅游区"])
        with mock.patch.object(wonders, "FIVE_A_IN_WHS", {"不存在的景区": 437}), self.assertRaises(SystemExit):
            wonders.scenic_5a(None, areas, {437}, [])

    def test_universities_a_then_b_rd_null(self):
        unis = [{"name": "新疆大学", "world_class_2017": "B", "double_first_class": True, "rd_cny": None,
                 "rd_year": None, "lon": 1, "lat": 1, "province": "65", "wikidata": "Q2"},
                {"name": "北京大学", "world_class_2017": "A", "double_first_class": True, "rd_cny": None,
                 "rd_year": None, "lon": 1, "lat": 1, "province": "11", "wikidata": "Q3"},
                {"name": "中国科学院大学", "world_class_2017": None, "double_first_class": True, "rd_cny": None,
                 "rd_year": None, "lon": 1, "lat": 1, "province": "11"}]
        out = wonders.universities(None, unis, [])
        self.assertEqual([w["name"] for w in out], ["北京大学", "新疆大学"])
        self.assertEqual([w["rank"] for w in out], [1, 2])
        self.assertIsNone(out[0]["metrics"]["rd_cny"])
        self.assertIsNone(out[0]["metrics"]["rd_year"])
        self.assertIn("未公开", out[0]["desc"])

    def test_tables_are_consistent(self):
        self.assertEqual(set(wonders.UNESCO_KIND.values()) | {"landmark", "university"}, wonders.KINDS)
        self.assertTrue(all(isinstance(v, int) for v in wonders.FIVE_A_IN_WHS.values()))


def good_doc():
    rule = lambda rid, kind, kinds=None: {"id": rid, "kind": kind, "kinds": kinds or [kind], "year": 2024, "text": "规则"}
    base = {"x": 1.0, "y": 1.0, "cell": 0, "cells": None, "county": "110101", "cbsa": "110000", "state": "11",
            "curated": False, "geo": "wikidata", "approx": False, "desc": "说明"}
    rows = []
    for rid, kind, n, metrics in [("unesco_whs", "monument", 20, {"whc_id": 1, "inscribed": 1987, "area_ha": None}),
                                  ("national_park", "natural", 5, {"area_km2": 100}),
                                  ("scenic_5a", "landmark", 10, {"designated": 2007, "batch": 1}),
                                  ("university", "university", 10, {"rd_cny": None, "rd_year": None})]:
        rows += [{**base, "id": f"{rid}-{k}", "name": "名", "short": "名", "kind": kind, "rule": rid, "rank": k,
                  "metrics": dict(metrics)} for k in range(1, n + 1)]
    return {"version": wonders.VERSION, "sources": ["cn_wonders"],
            "rules": [rule("unesco_whs", "monument", ["monument", "natural"]), rule("national_park", "natural"),
                      rule("scenic_5a", "landmark"), rule("university", "university")],
            "wonders": rows, "unplaced": []}


class WondersCheckTest(unittest.TestCase):
    def run_check(self, doc):
        web, _ = dataset()
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("world.json", "cities.json"):
                (Path(tmp) / name).write_text(json.dumps(web[name]))
            if doc is not None:
                (Path(tmp) / "wonders.json").write_text(json.dumps(doc))
            return wonders.check(Path(tmp))

    def test_missing_file_is_a_note(self):
        self.assertEqual([lv for lv, _ in self.run_check(None)], ["NOTE"])

    def test_valid_file_passes(self):
        lines = self.run_check(good_doc())
        self.assertFalse([m for lv, m in lines if lv in ("FAIL", "WARN")], lines)
        doc = good_doc()
        doc["wonders"][0]["kind"] = "natural"  # allowed by the UNESCO rule's kinds
        self.assertFalse([m for lv, m in self.run_check(doc) if lv in ("FAIL", "WARN")])

    def test_violations_fail(self):
        cases = {
            "ocean cell": lambda d: d["wonders"][0].update(cell=2),
            "foreign cell": lambda d: d["wonders"][0].update(cell=5),
            "rule": lambda d: d["wonders"][0].update(rule="nope"),
            "kind not in kinds": lambda d: d["wonders"][0].update(kind="university"),
            "unknown kinds": lambda d: d["rules"][0].update(kinds=["natural", "castle"]),
            "missing rule": lambda d: d["rules"].pop(),
            "rank": lambda d: d["wonders"][1].update(rank=1),
            "curated": lambda d: d["wonders"][-1].update(curated=True),
            "area zero": lambda d: d["wonders"][20]["metrics"].update(area_km2=0),
            "cells": lambda d: d["wonders"][0].update(cells=[5]),
            "cbsa": lambda d: d["wonders"][0].update(cbsa="999999"),
            "state": lambda d: d["wonders"][0].update(state="44"),
            "county province": lambda d: d["wonders"][0].update(state="12"),
            "county": lambda d: d["wonders"][0].update(county="110199"),
            "desc": lambda d: d["wonders"][0].update(desc=""),
            "inscribed": lambda d: d["wonders"][0]["metrics"].update(inscribed=None),
            "rd half": lambda d: d["wonders"][-1]["metrics"].update(rd_cny=1e9),
            "version": lambda d: d.update(version=1),
        }
        for what, change in cases.items():
            doc = copy.deepcopy(good_doc())
            change(doc)
            with self.subTest(what=what):
                self.assertTrue([m for lv, m in self.run_check(doc) if lv == "FAIL"], what)


if __name__ == "__main__":
    unittest.main()
