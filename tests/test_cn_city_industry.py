"""cn_city_industry: the stdlib .xls reader and the yearbook-table interpretation."""

import struct
import tempfile
import unittest
from pathlib import Path

from pipeline.sources import cn_city_industry as M

PREFS = [
    {"adcode": "370100", "name": "济南市", "short": "济南", "province": "37"},
    {"adcode": "370200", "name": "青岛市", "short": "青岛", "province": "37"},
    {"adcode": "222400", "name": "延边朝鲜族自治州", "short": "延边", "province": "22"},
]


def _rec(rtype: int, body: bytes) -> bytes:
    return struct.pack("<HH", rtype, len(body)) + body


def _make_xls(path: Path) -> None:
    """A tiny BIFF8 workbook in an OLE2 container: SST split across a CONTINUE (with a width change),
    LABELSST, NUMBER, RK (integer and /100), MULRK and a blank cell."""
    s1 = "济南市"  # wide (UTF-16) string
    s2 = "Total"
    head = struct.pack("<II", 3, 3)
    sst = head + struct.pack("<HB", len(s1), 1) + s1.encode("utf-16-le") + struct.pack("<HB", len(s2), 0) + s2.encode()
    # third string "ABCDEF" starts compressed in SST, continues *wide* in the CONTINUE record
    sst += struct.pack("<HB", 6, 0) + b"ABC"
    cont = b"\x01" + "DEF".encode("utf-16-le")
    bof = _rec(0x0809, struct.pack("<HHHHII", 0x0600, 0x0005, 0, 0, 0, 0))
    eof = _rec(0x000A, b"")

    def rk_int(n):
        return (n << 2) | 2

    sheet = (bof
             + _rec(0x00FD, struct.pack("<HHHI", 0, 0, 0, 0))  # A1 = 济南市
             + _rec(0x0203, struct.pack("<HHHd", 0, 1, 0, 1234.5))  # B1 = 1234.5
             + _rec(0x027E, struct.pack("<HHHI", 1, 0, 0, rk_int(42)))  # A2 = 42
             + _rec(0x027E, struct.pack("<HHHI", 1, 1, 0, rk_int(4250) | 1))  # B2 = 42.5 (RK ×100)
             + _rec(0x00BD, struct.pack("<HH", 2, 0) + struct.pack("<HI", 0, rk_int(7)) + struct.pack("<HI", 0, rk_int(8))
                    + struct.pack("<H", 1))  # A3 = 7, B3 = 8
             + _rec(0x00FD, struct.pack("<HHHI", 3, 1, 0, 2))  # B4 = ABCDEF; A4 blank
             + eof)
    name = b"\x06\x00Sheet1"  # cch, flags(compressed)
    boundsheet_len = 4 + 4 + 2 + len(name)
    globals_wo_bs = bof + _rec(0x00FC, sst) + _rec(0x003C, cont) + eof
    sheet_pos = len(globals_wo_bs) + boundsheet_len
    boundsheet = _rec(0x0085, struct.pack("<IBB", sheet_pos, 0, 0) + name)
    stream = bof + boundsheet + _rec(0x00FC, sst) + _rec(0x003C, cont) + eof + sheet
    assert stream.index(sheet) == sheet_pos
    stream += b"\x00" * (4096 - len(stream))  # ≥ mini-stream cutoff: regular sectors
    n = len(stream) // 512
    fat = [0xFFFFFFFD, 0xFFFFFFFE] + [3 + i for i in range(n - 1)] + [0xFFFFFFFE]
    fat += [0xFFFFFFFF] * (128 - len(fat))
    hdr = bytearray(512)
    hdr[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHH", hdr, 0x18, 0x3E, 3, 0xFFFE, 9)
    struct.pack_into("<H", hdr, 0x20, 6)
    struct.pack_into("<IIIIIIII", hdr, 0x2C, 1, 1, 0, 4096, 0xFFFFFFFE, 0, 0xFFFFFFFE, 0)
    struct.pack_into("<109I", hdr, 0x4C, 0, *([0xFFFFFFFF] * 108))

    def entry(nm, etype, start, size):
        e = bytearray(128)
        raw = (nm + "\0").encode("utf-16-le")
        e[: len(raw)] = raw
        struct.pack_into("<HB", e, 0x40, len(raw), etype)
        struct.pack_into("<III", e, 0x44, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF)
        struct.pack_into("<II", e, 0x74, start, size)
        return bytes(e)

    dirsec = entry("Root Entry", 5, 0xFFFFFFFE, 0) + entry("Workbook", 2, 2, len(stream)) + bytes(256)
    path.write_bytes(bytes(hdr) + struct.pack("<128I", *fat) + dirsec + stream)


class XlsReader(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.xls"
            _make_xls(p)
            rows = M.xls_rows(p)
        self.assertEqual(rows[0], ["济南市", 1234.5])
        self.assertEqual(rows[1], [42.0, 42.5])
        self.assertEqual(rows[2], [7.0, 8.0])
        self.assertEqual(rows[3], [None, "ABCDEF"])


class Interpretation(unittest.TestCase):
    def test_industry_of(self):
        self.assertEqual(M.industry_of("农、林、 牧、渔业 Agriculture, Forestry"), "A")
        self.assertEqual(M.industry_of("电力、热力、燃气及水生产和供应业Electricity"), "D")
        self.assertEqual(M.industry_of("水利、环境和公共设施管理业"), "N")
        self.assertEqual(M.industry_of("公共管理、社会保障和社会组织"), "S")
        self.assertEqual(M.industry_of("文化、 体育和 娱乐业 Culture"), "R")
        self.assertEqual(M.industry_of("信息传输、软件和信息技术服务业"), "I")
        self.assertIsNone(M.industry_of("合计 Total"))
        self.assertIsNone(M.industry_of("Region"))

    def test_place_matcher(self):
        m = M.PlaceMatcher(PREFS, "37")
        self.assertEqual(m.match("济 南 市"), "370100")
        self.assertEqual(m.match("  青岛市 Qingdao"), "370200")
        self.assertEqual(m.match("全省总计"), M.PlaceMatcher.TOTAL)
        self.assertEqual(m.match("山东省"), M.PlaceMatcher.TOTAL)
        self.assertIsNone(m.match("延边州"))  # other province
        self.assertEqual(M.PlaceMatcher(PREFS, "22").match("延边州"), "222400")

    def test_emp_city(self):
        names = [M.SECTIONS[s] for s in M.SECTIONS]
        rows = [
            ["表 各市分行业就业人员"], ["单位：万人"],
            ["地区", "Region", "总计", ""] + [""] * 19,
            ["", "", "", "城镇非私营单位"] + [""] * 19,
            ["", "", "", ""] + names,
            ["全省总计", "Total", 5315.0, 30.0] + [1.0] * 19,
            ["济 南 市", "Jinan", 484.2, 19.0] + [1.0] * 18 + [None],
            ["青岛市", "Qingdao", 540.4, 20.0] + ["-"] + [0.5] * 18,
            ["注：说明"],
        ]
        out = M.parse_emp_city(rows, M.PlaceMatcher(PREFS, "37"))
        self.assertEqual(out["370100"]["emp_total"], 190000)  # the 非私营 column, not the all-employment total
        self.assertEqual(out["370100"]["emp"]["A"], 10000)
        self.assertIsNone(out["370100"]["emp"]["S"])  # blank → null, never 0
        self.assertIsNone(out["370200"]["emp"]["A"])  # "-" → null
        self.assertEqual(out["370200"]["emp"]["B"], 5000)
        self.assertEqual(set(out["370200"]["emp"]), set(M.SECTIONS))
        self.assertEqual(out[M.PlaceMatcher.TOTAL]["emp_total"], 300000)

    def test_rd_city_picks_year_and_unit(self):
        rows = [
            ["各市R&D活动情况"],
            ["地区", "Region", "2023年", "", "2024年", ""],
            ["", "", "R&D人员(人年)", "R&D经费(万元)", "R&D人员(人年)", "R&D经费(万元)"],
            ["全区", "Guangxi", 1.0, 100.0, 2.0, 200.0],
            ["济南市", "Jinan", 1.0, 10.0, 2.0, 20.5],
        ]
        out = M.parse_rd_city(rows, M.PlaceMatcher(PREFS, "37"), 2024)
        self.assertEqual(out["370100"], 205000)

    def test_rd_provinces(self):
        tables = [[["地区", "2024年R&D经费（亿元）", "2024年R&D经费投入强度（%）"],
                   ["全国", "36326.8", "2.69"], ["北京", "3278.4", "6.58"], ["山东", "2597.3", "2.64"]]]
        prov, nation = M.parse_rd_provinces(tables)
        self.assertEqual(nation, 3632680000000)
        self.assertEqual(prov["11"], 327840000000)
        self.assertEqual(prov["37"], 259730000000)

    def test_html_tables_colspan(self):
        t = M.html_tables("<table><tr><td colspan=2>a</td><td>b</td></tr><tr><td>1</td><td>2</td><td>3</td></tr></table>")
        self.assertEqual(t[0], [["a", "", "b"], ["1", "2", "3"]])


if __name__ == "__main__":
    unittest.main()
