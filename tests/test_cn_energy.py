"""cn_energy parsing: GIPT unit aggregation and fuel mapping, GEM mines, GOGET fields and Chinese field names."""

import unittest

from pipeline.sources import cn_energy as ce


def unit(**kw):
    row = {"asset-type": "coal", "country-area1": "China", "name": "Test power station", "capacity": "600",
           "status": "operating", "start-year": "2010", "tech-type": "supercritical", "fuel": "coal: bituminous",
           "owner": "Owner Co [100%]", "Latitude": "30.0", "Longitude": "110.0", "project-id": "L1"}
    row.update(kw)
    return row


def feature(**props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": props}


class FuelTest(unittest.TestCase):
    def test_fuel_mapping(self):
        self.assertEqual(ce.plant_fuel("coal", "subcritical", ""), "coal")
        self.assertEqual(ce.plant_fuel("oil/gas", "combined cycle", "fossil gas: LNG"), "gas")
        self.assertEqual(ce.plant_fuel("oil/gas", "combined cycle", ""), "gas")
        self.assertEqual(ce.plant_fuel("oil/gas", "internal combustion", "fossil liquids: diesel"), "oil")
        self.assertEqual(ce.plant_fuel("oil/gas", "steam turbine", "industrial by-product: blast furnace gas"), "other")
        self.assertEqual(ce.plant_fuel("hydropower", "pumped storage", ""), "pumped_storage")
        self.assertEqual(ce.plant_fuel("hydropower", "conventional and pumped storage", ""), "hydro")
        self.assertEqual(ce.plant_fuel("utility-scale solar", "PV", ""), "solar")
        self.assertEqual(ce.plant_fuel("bioenergy", "", ""), "biomass")
        self.assertIsNone(ce.plant_fuel("battery", "", ""))


class PlantAggregationTest(unittest.TestCase):
    def test_units_sum_per_project_operating_only_and_names(self):
        rows = [
            unit(capacity="600", **{"start-year": "2012"}),
            unit(capacity="1000", Latitude="31.0", Longitude="111.0", owner="Big Co [51%]; Other [49%]"),
            unit(capacity="1000", status="construction"),           # not operating
            unit(capacity="", **{"start-year": ""}),                # unknown capacity: counted as a unit, not as 0
            unit(**{"country-area1": "Vietnam", "project-id": "L9"}),  # abroad
            unit(**{"country-area1": "Taiwan", "project-id": "L2", "asset-type": "wind", "tech-type": "Offshore hard mount",
                    "capacity": "", "name": "Formosa wind farm"}),
            unit(**{"project-id": "L3", "asset-type": "hydropower", "tech-type": "conventional storage",
                    "name": "Three Gorges Dam hydroelectric plant", "capacity": "22500"}),
        ]
        plants = {p["gem_id"]: p for p in ce.aggregate_plants(rows, {"L1": "测试电厂"})}
        self.assertEqual(set(plants), {"L1", "L2", "L3"})
        p = plants["L1"]
        self.assertEqual((p["name"], p["fuel"], p["capacity_mw"], p["units"]), ("测试电厂", "coal", 1600.0, 3))
        self.assertEqual((p["year"], p["year_last"]), (2010, 2012))
        self.assertEqual((p["lon"], p["lat"], p["owner"]), (111.0, 31.0, "Big Co; Other"))
        wind = plants["L2"]
        self.assertIsNone(wind["capacity_mw"])  # null, never 0
        self.assertTrue(wind["offshore"])
        self.assertEqual(wind["area"], "Taiwan")
        self.assertEqual(plants["L3"]["name"], "三峡水电站")
        self.assertEqual(plants["L3"]["fuel"], "hydro")


class MinesFieldsTest(unittest.TestCase):
    def test_mines_units_and_status(self):
        coal = [feature(**{"country-area1": "China", "status": "operating", "name": "Zhundong South Surface Mine",
                           "name-noneng": "准东南露天煤矿", "capacity": "40.0", "prod-coal": "", "prod-year-coal": "2023",
                           "Latitude": "44.8", "Longitude": "89.2", "start-year": "2017", "project-id": "M1"}),
                feature(**{"country-area1": "China", "status": "proposed", "name": "X", "capacity": "5",
                           "Latitude": "40", "Longitude": "110"})]
        iron = [feature(**{"country-area1": "China", "status": "operating", "name": "Hongge Mine",
                           "name-noneng": "红格矿(Chinese)", "capacity": "40,000", "Latitude": "26.6",
                           "Longitude": "101.9", "project-id": "P1"})]
        mines = ce.parse_mines(coal, iron)
        self.assertEqual(len(mines), 2)
        self.assertEqual((mines[0]["capacity"], mines[0]["capacity_unit"], mines[0]["production"]), (40_000_000, "t/yr", None))
        self.assertIsNone(mines[0]["production_year"])  # no production figure → no year either
        self.assertEqual((mines[1]["name"], mines[1]["commodity"], mines[1]["capacity"]), ("红格矿", "iron", 40_000_000))

    def test_wikidata_mines_skip_missing_or_far_coordinates(self):
        ent = {"Q812094": {"labels": {"en": {"value": "Bayan Obo mine"}},
                           "claims": {"P625": [{"mainsnak": {"datavalue": {"value": {"longitude": 109.97, "latitude": 41.78}}}}]}},
               "Q24377198": {"claims": {"P625": [{"mainsnak": {"datavalue": {"value": {"longitude": 10.0, "latitude": 41.0}}}}]}}}
        out = ce.parse_wikidata_mines(ent)
        self.assertEqual([m["wikidata"] for m in out], ["Q812094"])
        self.assertEqual((out[0]["commodity"], out[0]["capacity"]), ("rare_earths", None))

    def test_field_names(self):
        self.assertEqual(ce.field_name_zh("Sartu Oil Field (Heilongjiang, China)")[:2], ("萨尔图油田", "oil"))
        self.assertEqual(ce.field_name_zh("Liuhua 16-2 Oil Field (Guangdong, China)")[0], "流花16-2油田")
        self.assertEqual(ce.field_name_zh("Lingshui 17-2 (Deep Sea No.1 I) Gas Field (Hainan, China)")[0], "陵水17-2气田")
        self.assertEqual(ce.field_name_zh("Fuling Shale Gas Field (Chongqing, China)")[0], "涪陵页岩气田")
        self.assertEqual(ce.field_name_zh("Weizhou 11-2 2 Oil and Gas Phase (Guangxi, China)")[0], "涠洲11-2油气田")
        self.assertEqual(ce.field_name_zh("Nowhere Oil Field (Mars)")[:2], (None, "oil"))

    def test_fields_production_units_and_kind(self):
        feats = [feature(**{"country-area1": "China", "status": "operating", "name": "Bozi Oil and Gas Field (Xinjiang, China)",
                            "operator": "Petrochina Talimu Oilfield Company", "prod-oil": "", "prod-gas": "9600.0",
                            "prod-year-gas": "2024.0", "capacity": "56.4768", "Latitude": "41.5", "Longitude": "80.5"}),
                 feature(**{"country-area1": "China", "status": "decommissioning", "name": "Renqiu Oil Field (Hebei, China)",
                            "Latitude": "38.7", "Longitude": "116.1"})]
        (f,) = ce.parse_fields(feats)
        self.assertEqual((f["name"], f["kind"], f["basin"], f["operator_zh"]), ("博孜油气田", "oil_gas", "塔里木盆地", "塔里木油田"))
        self.assertEqual((f["production"], f["production_unit"], f["year"]), (56_476_800, "boe/yr", 2024))
        self.assertEqual((f["gas_m3"], f["oil_bbl"]), (9_600_000_000, None))


if __name__ == "__main__":
    unittest.main()
