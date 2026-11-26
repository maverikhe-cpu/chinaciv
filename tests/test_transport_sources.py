"""Parsing logic of the cn_transport_air (CAAC xlsx) and cn_transport_ports (MOT PDF) sources."""

import unittest
import zlib

from pipeline.core.geo import Shape
from pipeline.sources.cn_transport_air import AdminLocator, match_icao, parse_ranking
from pipeline.sources.cn_transport_ports import (
    COL0, COL_W, _wan, parse_ports, port_name, read_pdf_cells, table_rows,
)


def _row(a, c="", d="", g="", k=""):
    return {"A": a, "B": "", "C": c, "D": d, "E": "", "F": "", "G": g, "H": "", "I": "", "J": "", "K": k}


class CaacRankingTest(unittest.TestCase):
    def test_rows_between_total_and_note(self):
        rows = [
            _row("2025年全国民用运输机场吞吐量排名"),
            _row("机场", "旅客吞吐量（人次）"),
            _row("合计", "1529045687", "1459517710", "21864226.640000019", "12447526"),
            _row("上海/浦东", "84994548", "76787039", "4091940.932", "557043"),
            _row("连城/冠豸山", "", "134827", "", ""),
            _row("注：铜仁德江机场、蚌埠滕湖机场已经颁证，尚未正式通航。"),
            _row("不应读到"),
        ]
        out = parse_ranking(rows)
        self.assertEqual([r["name"] for r in out], ["上海/浦东", "连城/冠豸山"])
        self.assertEqual(out[0], {"name": "上海/浦东", "passengers": 84994548, "passengers_prev": 76787039,
                                  "cargo_t": 4091940.9, "movements": 557043})
        # blank cells are unknown, never 0
        self.assertIsNone(out[1]["passengers"])
        self.assertIsNone(out[1]["cargo_t"])
        self.assertEqual(out[1]["passengers_prev"], 134827)

    def test_no_total_row_is_an_error(self):
        with self.assertRaises(ValueError):
            parse_ranking([_row("机场")])


class MatchIcaoTest(unittest.TestCase):
    wd = [
        {"icao": "ZSPD", "labels": {"上海浦东机场", "上海/浦东"}},
        {"icao": "ZSSS", "labels": {"上海虹桥机场"}},
        {"icao": "ZBAA", "labels": {"北京首都机场"}},
        {"icao": "ZBAD", "labels": {"北京大兴机场"}},
        {"icao": "ZLXY", "labels": {"西安咸阳机场"}},
        {"icao": "ZLYA", "labels": {"延安南泥湾机场"}},
        {"icao": "ZBDS", "labels": {"鄂尔多斯伊金霍洛机场"}},
        {"icao": "ZBXA", "labels": {"伊金霍洛旗某机场"}},  # a second label with the airport part
    ]

    def test_airport_part(self):
        self.assertEqual(match_icao("上海/浦东", self.wd), "ZSPD")
        self.assertEqual(match_icao("北京/大兴", self.wd), "ZBAD")

    def test_city_part_disambiguates(self):
        self.assertEqual(match_icao("鄂尔多斯/伊金霍洛", self.wd), "ZBDS")

    def test_override_and_miss(self):
        self.assertEqual(match_icao("和田/昆冈", self.wd), "ZWTN")
        self.assertIsNone(match_icao("某地/不存在", self.wd))


class AdminLocatorTest(unittest.TestCase):
    def test_point_in_polygon_and_offsets(self):
        sq = [[(0, 0), (1, 0), (1, 1), (0, 1)]]
        bnd = {
            "provinces": [{"code": "44", "rings": sq}, {"code": "71", "rings": [[(5, 5), (6, 5), (6, 6), (5, 6)]]}],
            "prefectures": [{"adcode": "440300", "province": "44", "rings": sq, "seat": [0.5, 0.5]},
                            {"adcode": "710100", "province": "71", "rings": [], "seat": [5.5, 5.5]}],
            "counties": [{"adcode": "440305", "prefecture": "440300", "province": "44", "rings": sq,
                          "seat": [0.5, 0.5]}],
        }
        loc = AdminLocator(bnd)
        self.assertEqual(loc.locate(0.5, 0.5), ("44", "440300", "440305"))
        self.assertEqual(loc.locate(1.01, 0.5), ("44", "440300", "440305"))  # just off the coast
        self.assertEqual(loc.locate(5.5, 5.5), ("71", None, None))  # Taiwan: province only
        self.assertEqual(loc.locate(3, 3), (None, None, None))
        self.assertIsInstance(loc.prefectures[0][2], Shape)


def _cell(col, y, text):
    return (COL0 + col * COL_W + 20, y, text)


class PortTableTest(unittest.TestCase):
    def test_wan(self):
        self.assertEqual(_wan("143192"), 1431920000)
        self.assertEqual(_wan("-0.04"), -400)
        for t in ("", "…", "–"):
            self.assertIsNone(_wan(t))

    def test_rows_and_ports(self):
        cells = [
            (88, 127, "全国总计"), _cell(0, 126.8, " 1 833 806"),
            (84, 139.5, "1."), (92, 139.5, "沿海合计"), _cell(0, 139, "1 163 392"),
            (92, 151.7, "辽"), (99, 151.7, " "), (103, 151.7, "宁"), _cell(0, 151.2, "73 328"),
            (92, 163.6, "其中"), (105, 163.6, "："), (111, 163.6, "大"), (118, 163.6, "　"), (124, 163.6, "连"),
            _cell(0, 163.4, "29 958"), _cell(1, 163.4, "-6.2"), _cell(2, 163.4, "12 253"), _cell(4, 163.4, "548"),
            (92, 175.8, " "), (111, 175.8, "营"), (124, 175.8, "口"), _cell(0, 175.6, "21 965"),
            (92, 237.3, "天 津"), _cell(0, 236.9, "59 485"), _cell(4, 236.9, "2 403"),
            (92, 298.5, "上 海"), _cell(0, 298.1, "81 153"),
            (84, 555.5, "2."), (92, 555.5, "内河合计"), _cell(0, 555, "670 414"),
            (92, 567.9, "黑龙江"), _cell(0, 567.5, "252"),
            (92, 579.8, "其中：哈尔滨"), _cell(0, 579.4, "71"), _cell(4, 579.4, "…"),
            (92, 629.0, "上 海"), _cell(0, 628.6, "8 032"),
            (92, 641.3, "贵 州"), _cell(0, 641, "39"),
            (58, 700, "注：自2024年起发布全国主要港口数据"),
        ]
        rows = table_rows(cells)
        self.assertEqual(rows[3][0], "其中：大连")
        self.assertEqual(rows[3][2][:3], ["29958", "-6.2", "12253"])
        ports = parse_ports(rows)
        got = {(p["label"], p["kind"]): p for p in ports}
        self.assertEqual(set(got), {("大连", "coastal"), ("营口", "coastal"), ("天津", "coastal"),
                                    ("上海", "coastal"), ("哈尔滨", "inland"), ("上海", "inland")})
        self.assertEqual(got[("大连", "coastal")]["cargo_t"], 299580000)
        self.assertEqual(got[("大连", "coastal")]["foreign_t"], 122530000)
        self.assertEqual(got[("大连", "coastal")]["teu"], 5480000)
        self.assertIsNone(got[("营口", "coastal")]["teu"])  # blank → null
        self.assertIsNone(got[("哈尔滨", "inland")]["teu"])  # "…" → null
        self.assertEqual(got[("天津", "coastal")]["teu"], 24030000)
        self.assertEqual(got[("上海", "inland")]["cargo_t"], 80320000)

    def test_port_names(self):
        self.assertEqual(port_name("宁波舟山"), "宁波舟山港")
        self.assertEqual(port_name("连云港"), "连云港港")
        self.assertEqual(port_name("北部湾港"), "北部湾港")
        self.assertEqual(port_name("南通(沿海)"), "南通港（沿海）")
        self.assertEqual(port_name("上海", split=True), "上海港（内河）")


def _tiny_pdf() -> bytes:
    """One page, one Identity-H font whose ToUnicode maps 0001→宁 0002→波 0003→1 0004→空格."""
    cmap = (b"/CIDInit /ProcSet findresource begin\nbegincmap\n2 beginbfchar\n<0001> <5B81>\n<0002> <6CE2>\n"
            b"endbfchar\n1 beginbfrange\n<0003> <0003> <0031>\nendbfrange\n1 beginbfchar\n<0004> <0020>\n"
            b"endbfchar\nendcmap\nend\n")
    content = (b"BT /FT9 200 Tf 0.036 0 0 -0.036 92.0 359.5 Tm <00010002>Tj ET\n"
               b"BT /FT9 200 Tf 0.036 0 0 -0.036 182.0 359.2 Tm <0003>Tj 199 -0 TD<00040003>Tj ET\n")
    zc, zm = zlib.compress(content), zlib.compress(cmap)
    def stream(data: bytes) -> bytes:
        return b"<</Filter /FlateDecode /Length " + str(len(data)).encode() + b">>\rstream\r\n" + data + b"\r\nendstream"

    return b"".join([
        b"%PDF-1.7\n",
        b"1 0 obj\r<</Contents 2 0 R /Resources <</Font <</FT9 3 0 R>>>> /Type /Page>>\rendobj\r",
        b"2 0 obj\r" + stream(zc) + b"\rendobj\r",
        b"3 0 obj\r<</BaseFont /X /Encoding /Identity-H /Subtype /Type0 /ToUnicode 4 0 R /Type /Font>>\rendobj\r",
        b"4 0 obj\r" + stream(zm) + b"\rendobj\r",
        b"%%EOF\n",
    ])


class PdfReaderTest(unittest.TestCase):
    def test_text_runs(self):
        pages = read_pdf_cells(_tiny_pdf())
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0], [(92.0, 359.5, "宁波"), (182.0, 359.2, "1 1")])
        rows = table_rows(pages[0])
        self.assertEqual(rows, [("宁波", 92.0, ["11", "", "", "", "", ""])])


if __name__ == "__main__":
    unittest.main()
