"""pipeline.check on a tiny synthetic China dataset (world.json v3, a 3×2 grid)."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from pipeline import check

YIELDS = {"food": 10, "production": 20, "gold": 3.5e9, "science": 5, "culture": None}
ECON = {"population": 1000, "population_year": 2023, "pop_census_2020": 990, "employed": 500, "yields": YIELDS,
        "jobs": {"A": 10, "B": None}, "gdp": 3.5e9, "gdp_year": 2023, "currency": "CNY", "growth": 0.0101,
        "growth_years": [2020, 2023]}


def dataset():
    """Grid 3×2: cells 0, 1, 3, 4 are province 0 (counties 0/1), cell 2 is ocean, cell 5 foreign land."""
    owner = [0, 0, -1, 0, 0, -2]
    world = {
        "version": 3, "grid": {"radius": 25.0, "cols": 3, "rows": 2}, "width": 120.0, "height": 60.0, "margin": 0,
        "projection": {"min_x": 0.0, "max_y": 0.0, "margin": 0.0, "lat1": 25.0, "lat2": 47.0, "lat0": 0.0, "lon0": 105.0},
        "owner": owner, "county": [0, 0, -1, 1, 1, -1], "elev": [10, 20, -50, 30, 40, 100], "elev_max": [20, 40, 0, 50, 90, 200],
        "water": [0, 0, 1, 0, 0, 0], "relief": [0, 1, 0, 0, 2, 1], "cover": [1, 7, 0, 9, 5, 4],
        "pct": {k: [50 if k == "forest" and o >= 0 else 0 for o in owner] for k in check.PCT_KEYS},
        "tables": {
            "water": [{"id": i, "name": n} for i, n in enumerate(["陆地", "远海", "近海", "湖泊"])],
            "relief": [{"id": i, "name": n} for i, n in enumerate(["平原", "丘陵", "山地", "高山"])],
            "cover": [{"id": 0, "key": "none", "name": "无"}]
            + [{"id": i, "key": k, "name": k, "color": "#123456"} for i, k in enumerate(check.COVER_KEYS) if i],
        },
        "states": [{"fips": "11", "abbr": "京", "name": "北京", "full": "北京市", "kind": "municipality", "kind_label": "直辖市"},
                   {"fips": "12", "abbr": "津", "name": "天津", "full": "天津市", "kind": "municipality", "kind_label": "直辖市"}],
        "counties": [{"geoid": "110101", "name": "东城区"}, {"geoid": "110102", "name": "西城区"}],
        "rivers": [{"name": "长江", "name_en": "Yangtze", "rank": 1, "pts": [1.0, 1.0, 50.0, 30.0]},
                   {"name": None, "name_en": "Some River", "rank": 6, "pts": [2.0, 2.0, 20.0, 20.0]}],
        "lakes": [{"name": "青海湖", "x": 10.0, "y": 10.0, "cells": 1}],
        "dash_line": [[100.0, 5.0, 104.0, 20.0]],
        "islands": [{"id": "diaoyu", "name": "钓鱼岛", "kind": "island", "x": 110.0, "y": 10.0}],
        "labels": [{"name": "三沙市", "x": 90.0, "y": 20.0, "kind": "city", "id": "460300"}],
        "inset": {"title": "南海诸岛", "box": [80.0, 30.0, 118.0, 58.0], "region": [106.5, 2.8, 123.5, 24.8], "scale": 0.3,
                  "land": [{"kind": "china", "code": "46", "pts": [82.0, 32.0, 90.0, 32.0, 86.0, 36.0]},
                           {"kind": "foreign", "name": "越南", "pts": [81.0, 40.0, 84.0, 40.0, 82.0, 44.0]}],
                  "dashes": [[90.0 + k, 40.0, 91.0 + k, 42.0] for k in range(10)],
                  "islands": [{"id": "xisha", "name": "西沙群岛", "kind": "island_group", "x": 95.0, "y": 45.0}],
                  "labels": [{"name": "三沙市", "x": 96.0, "y": 46.0, "kind": "city"}]},
        "notice": {"text": "本地图仅供个人学习，未申请审图号。", "boundary": "DataV.GeoAtlas", "sources": ["cn_boundaries"]},
    }
    # Ranks: equal values keep population order; 天津 has the higher GDP per person (same GDP, fewer people).
    city_ranks = {"population": 1, "gdp": 1, "gdp_per_capita": 2, "growth": 1,
                  **{k: (None if v is None else 1) for k, v in YIELDS.items()}}
    cities = [
        {"id": "110000", "name": "北京", "full": "北京市", "states": ["11"], "metro": True, "capital": True,
         "x": 1.0, "y": 1.0, "cell": 0, **ECON, "counties": ["110101"], "footprint": [0, 1],
         "pop_series": {"2020": 990, "2023": 1000}, "ranks": city_ranks, "peers": ["120000"]},
        {"id": "120000", "name": "天津", "full": "天津市", "states": ["12"], "metro": True, "capital": False,
         "x": 40.0, "y": 40.0, "cell": 4, **ECON, "population": 10, "counties": ["110102"], "footprint": [3, 4],
         "pop_series": None, "ranks": {k: (None if v is None else (1 if k == "gdp_per_capita" else 2))
                                       for k, v in city_ranks.items()}, "peers": ["110000"]},
    ]
    # Two provinces, so ranks have an order to check. Yields: 北京 > 天津 except culture (null in both).
    ranks = {k: (None if v is None else 1) for k, v in YIELDS.items()}
    small = {k: (None if v is None else v / 2) for k, v in YIELDS.items()}
    land = {k: (0.5 if k == "forest" else 0.0) for k in check.PCT_KEYS}
    energy = {"unit": "MW", "total": 100, "by_type": {"nuclear": 100}, "sites": {"nuclear": 1}, "note": "仅统计地图上的电厂"}
    meta = {"kind": "municipality", "kind_label": "直辖市", "note": None}
    states = [{"fips": "11", "abbr": "京", "name": "北京", "full": "北京市", **meta, **ECON, "ranks": ranks,
               "label": {"x": 10, "y": 10, "angle": 0, "size": 14}, "land": land, "energy": energy},
              {"fips": "12", "abbr": "津", "name": "天津", "full": "天津市", **meta, **ECON, "yields": small,
               "ranks": {k: (None if v is None else 2) for k, v in ranks.items()}, "land": None, "label": None,
               "energy": {**energy, "total": 0, "by_type": {"nuclear": 0}, "sites": {"nuclear": 0}}}]
    nation = {"vintage": "七普 2020；2023 年统计公报", "year": 2023, "gdp_vintage": None, "nation": ECON, "industries": {"A": "农、林、牧、渔业"},
              "yields": [{"key": k, "icon": f"y-{k}", "emoji": "*", "label": k, "color": "#7fbf3f",
                          "unit": "cny", "description": "d"} for k in check.YIELD_KEYS]}
    lenses = {"vintage": "2023", "groups": [{"id": "people", "name": "人口"}],
              "lenses": [{"id": "density", "group": "people", "name": "人口密度", "icon": "l-people", "emoji": "*",
                          "unit": "人/平方千米", "format": "float", "scale": "log", "domain": [1, 2000],
                          "ramp": ["#000000", "#ffffff"], "higher_is": "neutral", "source": "cn_stats_city", "description": "d",
                          "values": [10.0, 10.0, None, 20.0, 20.0, None]}]}
    sources = [{"id": "cn_stats_city", "title": "城市统计", "publisher": "国家统计局", "homepage": "h", "license": "pd",
                "description": "d", "used_for": ["cities"], "vintage": "2023",
                "files": [{"file": "a", "url": "u", "retrieved": "t", "sha256": "s", "bytes": 1}]}]
    resources = {"types": [{"id": "nuclear", "name": "核电", "category": "energy", "emoji": "*", "icon": "r-n", "color": "#e8d44d",
                            "unit": "MW", "description": "≥ 1 MW", "source": "cn_stats_city"}],
                 "rule": "示例规则",
                 "items": [{"type": "nuclear", "name": "示例核电站", "x": 5.0, "y": 5.0, "cell": 0, "value": 100, "detail": "",
                            "state": "11", "cbsa": "110000"}],
                 "summary": {"note": "仅统计地图上的站点", "national": {"nuclear": {"count": 1, "value": 100, "unit": "MW"}},
                             "states": {"11": {"nuclear": {"count": 1, "value": 100, "unit": "MW"}}}}}
    interim = {
        "_world": {"data": {"counties": ["110101", "110102"], "county": [0, 0, -1, 1, 1, -1],
                            "county_samples": [[0] * 7, [0] * 7, [], [1] * 7, [1] * 7, []]}},
    }
    return {"world.json": world, "cities.json": cities, "states.json": states, "nation.json": nation,
            "lenses.json": lenses, "sources.json": sources, "resources.json": resources}, interim


class CheckTest(unittest.TestCase):
    def run_check(self, web, interim):
        with tempfile.TemporaryDirectory() as tmp:
            wd, idir = Path(tmp) / "web", Path(tmp) / "interim"
            wd.mkdir()
            idir.mkdir()
            for name, doc in web.items():
                if doc is not None:
                    (wd / name).write_text(json.dumps(doc))
            for name, doc in interim.items():
                (idir / f"{name}.json").write_text(json.dumps(doc))
            return check.run(wd, idir, quiet=True)

    def levels(self, rep, level):
        return [m for lv, m in rep.lines if lv == level]

    def test_clean_dataset_passes(self):
        rep = self.run_check(*dataset())
        self.assertEqual(self.levels(rep, "FAIL"), [])
        self.assertEqual(self.levels(rep, "WARN"), [])
        self.assertTrue(any(m.startswith("world.json: v3") for m in self.levels(rep, "PASS")))

    def test_missing_optional_is_a_note(self):
        web, interim = dataset()
        web["resources.json"] = None
        web["lenses.json"] = None
        for s in web["states.json"]:
            s["energy"] = None  # what states.py writes when resources.json is not built
        rep = self.run_check(web, interim)
        self.assertFalse(rep.failed)
        notes = " | ".join(self.levels(rep, "NOTE"))
        self.assertIn("resources.json", notes)
        self.assertIn("lenses.json", notes)

    def test_oil_summary_value_is_null(self):
        web, interim = dataset()
        res = web["resources.json"]
        res["types"].append({**res["types"][0], "id": "oil", "unit": "boe/yr"})
        res["items"].append({**res["items"][0], "type": "oil", "value": 5000})
        res["summary"]["national"]["oil"] = {"count": 1, "value": None, "unit": "boe/yr"}
        res["summary"]["states"]["11"]["oil"] = {"count": 1, "value": None, "unit": "boe/yr"}
        self.assertFalse(self.run_check(web, interim).failed)
        res["summary"]["national"]["oil"]["value"] = 5000
        self.assertTrue(self.run_check(web, interim).failed)

    def test_missing_required_fails(self):
        web, interim = dataset()
        web["states.json"] = None
        self.assertTrue(self.run_check(web, interim).failed)

    def test_us_world_fails(self):
        web, interim = dataset()
        web["world.json"]["version"] = 2
        rep = self.run_check(web, interim)
        self.assertTrue(any("version" in m for m in self.levels(rep, "FAIL")))

    def test_contract_violations(self):
        cases = {
            "lens value on water": lambda w: w["lenses.json"]["lenses"][0]["values"].__setitem__(2, 5.0),
            "lens wrong length": lambda w: w["lenses.json"]["lenses"][0]["values"].pop(),
            "owner out of range": lambda w: w["world.json"]["owner"].__setitem__(0, 7),
            "relief on water": lambda w: w["world.json"]["relief"].__setitem__(2, 2),
            "pct over 100": lambda w: w["world.json"]["pct"]["crop"].__setitem__(0, 90),
            "cities unsorted": lambda w: w["cities.json"].reverse(),
            "duplicate city id": lambda w: w["cities.json"][1].__setitem__("id", "110000"),
            "city id not 6 digits": lambda w: w["cities.json"][1].__setitem__("id", "12000"),
            "bad yield key": lambda w: w["cities.json"][0].__setitem__("yields", {"food": 1}),
            "currency not CNY": lambda w: w["states.json"][0].__setitem__("currency", "USD"),
            "population without year": lambda w: w["cities.json"][0].__setitem__("population_year", None),
            "state order": lambda w: w["states.json"].reverse(),
            "bad resource type": lambda w: w["resources.json"]["items"][0].__setitem__("type", "unicorn"),
            "sources without sha": lambda w: w["sources.json"][0]["files"][0].pop("sha256"),
            "rank too big": lambda w: w["states.json"][0]["ranks"].__setitem__("food", 9),
            "string population": lambda w: w["nation.json"]["nation"].__setitem__("population", "1000"),
            "water above sea level": lambda w: w["world.json"]["elev"].__setitem__(2, 500),
            "state missing econ fields": lambda w: [w["states.json"][0].pop(k) for k in ("gdp", "population_year")],
            "state missing full name": lambda w: w["states.json"][0].pop("full"),
            "ranks missing yield keys": lambda w: w["states.json"][0].__setitem__("ranks", {"food": 1}),
            "ranks not a permutation": lambda w: w["states.json"][1]["ranks"].__setitem__("food", 1),
            "ranks against yield order": lambda w: (w["states.json"][0]["ranks"].__setitem__("gold", 2),
                                                   w["states.json"][1]["ranks"].__setitem__("gold", 1)),
            "rank on null yield": lambda w: w["states.json"][0]["ranks"].__setitem__("culture", 1),
            "nation missing econ fields": lambda w: [w["nation.json"]["nation"].pop(k) for k in ("population", "gdp", "currency")],
            "yield unit workers": lambda w: w["nation.json"]["yields"][0].__setitem__("unit", "workers"),
            "pct lens as percent": lambda w: w["lenses.json"]["lenses"].append(
                {**w["lenses.json"]["lenses"][0], "id": "share", "format": "pct", "scale": "linear",
                 "values": [50.0, 20.0, None, 10.0, 5.0, None]}),
            "city in unknown province": lambda w: w["cities.json"][0].__setitem__("states", ["京"]),
            "metro not a bool": lambda w: w["cities.json"][0].__setitem__("metro", "yes"),
            "capital not a bool": lambda w: w["cities.json"][1].__setitem__("capital", 0),
            "river rank not int": lambda w: w["world.json"]["rivers"][0].__setitem__("rank", "x"),
            "river name not text": lambda w: w["world.json"]["rivers"][0].__setitem__("name", 5),
            "pct on foreign cell": lambda w: w["world.json"]["pct"]["forest"].__setitem__(5, 30),
            "growth over 25%": lambda w: w["cities.json"][0].__setitem__("growth", 0.4),
            "growth as percent": lambda w: w["states.json"][0].__setitem__("growth", 3.1),
            "growth_years reversed": lambda w: w["cities.json"][0].__setitem__("growth_years", [2023, 2020]),
            "growth without years": lambda w: w["nation.json"]["nation"].__setitem__("growth_years", None),
            "city missing growth": lambda w: w["cities.json"][1].pop("growth"),
            "nation growth null within a window": lambda w: w["nation.json"]["nation"].__setitem__("growth", None),
            "growth window ends before nation year": lambda w: [
                o.__setitem__("growth_years", [2020, 2022])
                for o in (*w["cities.json"], *w["states.json"], w["nation.json"]["nation"])],
            "growth windows differ": lambda w: w["cities.json"][1].__setitem__("growth_years", [2021, 2023]),
            # world.json v3
            "projection without lat1": lambda w: w["world.json"]["projection"].pop("lat1"),
            "province code not 2 digits": lambda w: w["world.json"]["states"][0].__setitem__("fips", "1"),
            "county geoid 5 digits": lambda w: w["world.json"]["counties"][0].__setitem__("geoid", "11010"),
            "inset with 9 dashes": lambda w: w["world.json"]["inset"]["dashes"].pop(),
            "inset land outside box": lambda w: w["world.json"]["inset"]["land"][0]["pts"].__setitem__(0, 5.0),
            "inset missing title": lambda w: w["world.json"]["inset"].pop("title"),
            "notice missing": lambda w: w["world.json"].pop("notice"),
            "notice without boundary": lambda w: w["world.json"]["notice"].pop("boundary"),
            "dash not a polyline": lambda w: w["world.json"]["dash_line"].__setitem__(0, [1.0, 2.0, 3.0]),
            "island without id": lambda w: w["world.json"]["islands"][0].pop("id"),
            # City view
            "footprint without own cell": lambda w: w["cities.json"][0].__setitem__("footprint", [1]),
            "footprint unsorted": lambda w: w["cities.json"][0].__setitem__("footprint", [1, 0]),
            "footprint on water": lambda w: w["cities.json"][0].__setitem__("footprint", [0, 2]),
            "city missing footprint": lambda w: w["cities.json"][0].pop("footprint"),
            "county adcode not a string": lambda w: w["cities.json"][0].__setitem__("counties", [110101]),
            "pop_series negative": lambda w: w["cities.json"][0]["pop_series"].__setitem__("2023", -5),
            "pop_series bad year": lambda w: w["cities.json"][0]["pop_series"].__setitem__("y2", 5),
            "peer is self": lambda w: w["cities.json"][0].__setitem__("peers", ["110000"]),
            "unknown peer": lambda w: w["cities.json"][0].__setitem__("peers", ["999999"]),
            "city ranks missing key": lambda w: w["cities.json"][0]["ranks"].pop("gdp_per_capita"),
            "city ranks against value": lambda w: (w["cities.json"][0]["ranks"].__setitem__("gdp_per_capita", 1),
                                                   w["cities.json"][1]["ranks"].__setitem__("gdp_per_capita", 2)),
            "city rank on null yield": lambda w: w["cities.json"][0]["ranks"].__setitem__("culture", 1),
            # provinces land / energy
            "land as percent": lambda w: w["states.json"][0]["land"].__setitem__("forest", 50),
            "land missing key": lambda w: w["states.json"][0]["land"].pop("crop"),
            "energy total wrong": lambda w: w["states.json"][0]["energy"].__setitem__("total", 999),
            "energy without note": lambda w: w["states.json"][0]["energy"].pop("note"),
            "energy sites disagree": lambda w: w["states.json"][0]["energy"]["sites"].__setitem__("nuclear", 0),
            # resources state / cbsa / summary
            "resource unknown province": lambda w: w["resources.json"]["items"][0].__setitem__("state", "京"),
            "resource unknown cbsa": lambda w: w["resources.json"]["items"][0].__setitem__("cbsa", "999999"),
            "resource missing cbsa": lambda w: w["resources.json"]["items"][0].pop("cbsa"),
            "summary missing": lambda w: w["resources.json"].pop("summary"),
            "summary count off": lambda w: w["resources.json"]["summary"]["national"]["nuclear"].__setitem__("count", 2),
            "summary value off": lambda w: w["resources.json"]["summary"]["states"]["11"]["nuclear"].__setitem__("value", 5),
            "summary missing province": lambda w: w["resources.json"]["summary"].__setitem__("states", {}),
        }
        for label, mutate in cases.items():
            web, interim = dataset()
            web = copy.deepcopy(web)
            mutate(web)
            with self.subTest(label):
                self.assertTrue(self.run_check(web, interim).failed, label)

    def test_zero_yield_warns(self):
        """null means unknown; a 0 is a WARN (a stand-in for unknown?)."""
        web, interim = dataset()
        web["cities.json"][0]["yields"] = {**YIELDS, "food": 0}
        web["cities.json"][0]["ranks"] = {**web["cities.json"][0]["ranks"], "food": 2}
        web["cities.json"][1]["ranks"] = {**web["cities.json"][1]["ranks"], "food": 1}
        rep = self.run_check(web, interim)
        self.assertFalse(rep.failed)
        self.assertTrue(any("exactly 0" in m for m in self.levels(rep, "WARN")))

    def test_null_population_sorts_last(self):
        web, interim = dataset()
        c = web["cities.json"][1]
        c.update(population=None, population_year=None, growth=None, pop_census_2020=None)
        c["ranks"] = {**c["ranks"], "population": None, "gdp_per_capita": None, "growth": None}
        web["cities.json"][0]["ranks"] = {**web["cities.json"][0]["ranks"], "gdp_per_capita": 1}
        self.assertFalse(self.run_check(web, interim).failed)
        web["cities.json"].reverse()
        self.assertTrue(self.run_check(web, interim).failed)

    def test_null_growth_is_fine(self):
        web, interim = dataset()
        web["cities.json"][1] = {**web["cities.json"][1], "growth": None}
        web["cities.json"][1]["ranks"] = {**web["cities.json"][1]["ranks"], "growth": None}
        web["states.json"][1] = {**web["states.json"][1], "growth": None}  # a province without a 2023 population
        rep = self.run_check(web, interim)
        self.assertFalse(rep.failed)
        self.assertTrue(any("cities growth 2020→2023: 1 of 2" in m for m in self.levels(rep, "NOTE")))
        self.assertTrue(any(m.startswith("growth: one window 2020→2023") for m in self.levels(rep, "PASS")))

    def test_quality_warnings(self):
        web, interim = dataset()
        web["cities.json"][1]["cell"] = 0  # shares 北京's cell
        web["cities.json"][1]["footprint"] = [0, 3, 4]
        web["world.json"]["rivers"].append({"name": "Lost", "rank": 5, "pts": [-500.0, -500.0, -400.0, -450.0]})
        rep = self.run_check(web, interim)
        warns = " | ".join(self.levels(rep, "WARN"))
        self.assertFalse(rep.failed)
        for text in ("more than one city", "outside the map"):
            self.assertIn(text, warns)

    def test_city_on_water_and_null_lens_share(self):
        web, interim = dataset()
        web["cities.json"][1]["cell"] = 2
        web["cities.json"][1]["footprint"] = [2, 3, 4]
        web["lenses.json"]["lenses"][0]["values"][3] = None
        rep = self.run_check(web, interim)
        warns = " | ".join(self.levels(rep, "WARN"))
        self.assertIn("non-land cells: 1", warns)
        self.assertIn("lens density: 25.0%", warns)

    def test_county_coverage(self):
        """A county smaller than the sampling is a NOTE; a Chinese land cell without a county is a WARN."""
        web, interim = dataset()
        interim["_world"]["data"]["counties"].append("110105")
        web["world.json"]["counties"].append({"geoid": "110105", "name": "朝阳区"})
        rep = self.run_check(web, interim)
        self.assertTrue(any("2 of 3 counties" in m and "110105" in m for m in self.levels(rep, "NOTE")))
        self.assertEqual(self.levels(rep, "WARN"), [])
        interim["_world"]["data"]["county"][3] = -1
        rep = self.run_check(web, interim)
        self.assertTrue(any("without a county: 1" in m for m in self.levels(rep, "WARN")))


if __name__ == "__main__":
    unittest.main()
