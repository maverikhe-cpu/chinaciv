"""cn_wonders parsing: UNESCO rows, the national-park announcement, the MCT 5A directory, the 双一流 lists
(PDF text extraction and the 2017 docx classes), Wikidata SPARQL rows and scenic-name matching."""

import io
import unittest
import zipfile
import zlib

from pipeline.sources import cn_wonders as w


def _pdf(page_ops: bytes, cmap: bytes) -> bytes:
    """A tiny one-page PDF with a Type0 font whose ToUnicode CMap is `cmap` and content `page_ops`."""
    content = zlib.compress(page_ops)
    tu = zlib.compress(cmap)
    objs = [
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj",
        b"2 0 obj<</Type/Pages/Count 1/Kids[ 3 0 R ]>>endobj",
        b"3 0 obj<</Type/Page/Parent 2 0 R/Resources<</Font<</FT8 5 0 R >>>>/Contents 4 0 R >>endobj",
        b"4 0 obj<</Filter/FlateDecode/Length %d>>stream\r\n" % len(content) + content + b"\r\nendstream\nendobj",
        b"5 0 obj<</Type/Font/Subtype/Type0/Encoding/Identity-H/ToUnicode 6 0 R >>endobj",
        b"6 0 obj<</Filter/FlateDecode/Length %d>>stream\r\n" % len(tu) + tu + b"\r\nendstream\nendobj",
    ]
    return b"%PDF-1.7\n" + b"\n".join(objs) + b"\n%%EOF"


class UnescoTest(unittest.TestCase):
    def test_rows_keep_china_and_parse_fields(self):
        rows = [
            {"id_no": "1714", "name_zh": "北京中轴线", "name_en": "Beijing Central Axis", "category": "Cultural",
             "date_inscribed": "2024", "criteria_txt": "(iii)(iv)", "area_hectares": 589.0, "iso_codes": ["CN"],
             "transboundary": "false", "geo_point_2d": {"lon": 116.39, "lat": 39.9},
             "coordinates": {"geometry": {"type": "MultiPoint", "coordinates": [[116.39, 39.9]]}}},
            {"id_no": "1442", "name_zh": "丝绸之路", "name_en": "Silk Roads", "category": "Cultural",
             "date_inscribed": "2014", "criteria_txt": "(ii)", "area_hectares": None,
             "iso_codes": ["CN", "KG", "KZ"], "transboundary": "true", "geo_point_2d": {"lon": 75.0, "lat": 42.0},
             "coordinates": {"geometry": {"type": "MultiPoint", "coordinates": [[108.9, 34.2], [75.0, 42.0]]}}},
            {"id_no": "1", "name_en": "Elsewhere", "category": "Natural", "date_inscribed": "1990",
             "iso_codes": ["JP"]},
        ]
        out = w.parse_unesco(rows)
        self.assertEqual([s["whc_id"] for s in out], [1442, 1714])
        axis = out[1]
        self.assertEqual((axis["name"], axis["category"], axis["inscribed"], axis["area_ha"]),
                         ("北京中轴线", "cultural", 2024, 589.0))
        self.assertFalse(axis["transnational"])
        silk = out[0]
        self.assertTrue(silk["transnational"])
        self.assertIsNone(silk["area_ha"])  # unknown stays null, never 0
        self.assertEqual(silk["_components"], [(108.9, 34.2), (75.0, 42.0)])


class ParksTest(unittest.TestCase):
    def test_announcement_areas_and_provinces(self):
        html = ("<p>　　三江源国家公园地处青藏高原腹地，保护面积19.07万平方公里，实现了源头整体保护。</p>"
                "<p>　　大熊猫国家公园跨四川、陕西和甘肃三省，保护面积2.2万平方公里，是野生大熊猫集中分布区。</p>"
                "<p>　　海南热带雨林国家公园位于海南岛中部，保护面积4269平方公里，保存了热带雨林。</p>")
        out = w.parse_parks(html)
        self.assertEqual([(p["name"], p["area_km2"]) for p in out],
                         [("三江源国家公园", 190700), ("大熊猫国家公园", 22000), ("海南热带雨林国家公园", 4269)])
        self.assertEqual(out[1]["provinces"], ["51", "61", "62"])
        self.assertEqual(out[2]["provinces"], ["46"])
        self.assertEqual(out[0]["provinces"], [])


class MctTest(unittest.TestCase):
    def test_years_batches_and_two_provinces(self):
        doc = {"data": {"contentList": {"content": [
            {"id": 1, "name": "故宫博物院", "provinceCodeDb": "110000", "year": "2007年", "isDel": False},
            {"id": 2, "name": "秦皇岛市山海关景区", "provinceCodeDb": "130000", "year": "2007/2018年"},
            {"id": 3, "name": "黄河壶口瀑布旅游区", "provinceCodeDb": "140000", "provinceCode2": "610000",
             "year": "2022年"},
            {"id": 4, "name": "已删除", "provinceCodeDb": "110000", "year": "2010年", "isDel": True},
            {"id": 5, "name": "无年份", "provinceCodeDb": "110000", "year": None},
        ]}}}
        out = w.parse_mct(doc)
        self.assertEqual([r["name"] for r in out], ["故宫博物院", "秦皇岛市山海关景区", "黄河壶口瀑布旅游区", "无年份"])
        self.assertEqual([(r["designated"], r["batch"]) for r in out], [(2007, 1), (2007, 1), (2022, None),
                                                                          (None, None)])
        self.assertEqual(out[2]["provinces"], ["14", "61"])


class UniversityListsTest(unittest.TestCase):
    def test_pdf_text_and_2022_list(self):
        # glyph ids 1..: 清 华 大 学 ： 物 理 外 交 院 （ 北 京 ）
        chars = "清华大学：物理外交院（北京）"
        cmap = b"begincmap\n%d beginbfchar\n" % len(chars) + b"".join(
            b"<%04X> <%04X>\n" % (i + 1, ord(c)) for i, c in enumerate(chars)) + b"endbfchar\nendcmap"
        gid = {c: i + 1 for i, c in enumerate(chars)}

        def tj(text, x, y):
            hexs = "".join("%04X" % gid[c] for c in text).encode()
            return b"BT /FT8 280 Tf 0.05 0 0 -0.05 %d %d Tm <" % (x, y) + hexs + b">Tj ET\n"

        ops = (tj("清华大学：", 80, 100) + tj("物理", 200, 100)            # one line, two runs
               + tj("外交学院：", 80, 120) + tj("物理", 200, 120)
               + tj("华北大学（北京）：物理", 80, 140)
               + tj("物理", 80, 160))                                     # continuation line, no university
        lines = w.pdf_lines(_pdf(ops, cmap))
        self.assertEqual(lines[0], "清华大学：物理")
        self.assertEqual(w.parse_dfc_2022(lines), ["清华大学", "外交学院", "华北大学（北京）"])

    def test_2017_classes(self):
        paras = ["“双一流”建设高校名单", "一、一流大学建设高校42所", "1. A类36所", "北京大学、中国人民大学、清华大学",
                 "2. B类6所", "东北大学、郑州大学", "二、一流学科建设高校95所", "北京交通大学、北京工业大学"]
        self.assertEqual(w.parse_dfc_2017(paras), {"北京大学": "A", "中国人民大学": "A", "清华大学": "A",
                                                   "东北大学": "B", "郑州大学": "B"})

    def test_docx_paragraphs(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", '<w:document><w:body><w:p><w:r><w:t>北京</w:t></w:r><w:r>'
                       '<w:t xml:space="preserve">大学</w:t></w:r></w:p><w:p><w:r><w:t>二</w:t></w:r></w:p>'
                       '</w:body></w:document>')
        buf.seek(0)
        self.assertEqual(w.docx_paragraphs(buf), ["北京大学", "二"])

    def test_renamed_school_matches_osm_name(self):
        self.assertTrue(w._same_school("上海体育大学", "上海体育学院"))
        self.assertTrue(w._same_school("长安大学渭水校区", "长安大学"))
        self.assertFalse(w._same_school("西北大学长安校区", "长安大学"))


class MatchingTest(unittest.TestCase):
    def test_sparql_rows(self):
        doc = {"results": {"bindings": [
            {"item": {"value": "http://www.wikidata.org/entity/Q71229"}, "coord": {"value": "Point(91.1169 29.6577)"},
             "l": {"value": "布达拉宫 "}},
            {"item": {"value": "http://www.wikidata.org/entity/Q1"}, "coord": {"value": "bad"}, "l": {"value": "x"}}]}}
        self.assertEqual(w.sparql_rows(doc), [("Q71229", 91.1169, 29.6577, "布达拉宫")])

    def test_norm_name_strips_places_and_generic_suffixes(self):
        places = sorted(["黄山市", "黄山", "安徽省"], key=len, reverse=True)
        self.assertEqual(w.norm_name("黄山市黄山风景区", places), "黄山")
        self.assertEqual(w.norm_name("扬州市瘦西湖风景区", ["扬州市"]), "瘦西湖")
        self.assertEqual(w.norm_name("苏州园林（拙政园－留园－虎丘）"), "苏州园林拙政园留园虎丘")

    def test_match_name(self):
        cands = {"黄山": "A", "古镇": "B", "西递宏村": "C"}
        self.assertEqual(w.match_name("黄山", cands), "A")
        self.assertIsNone(w.match_name("和顺古镇", {"古镇": "B"}))   # 2-character labels only as a prefix
        self.assertEqual(w.match_name("皖南古村落西递宏村", cands), "C")
        self.assertIsNone(w.match_name("泰山", cands))


if __name__ == "__main__":
    unittest.main()
