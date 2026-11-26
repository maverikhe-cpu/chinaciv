"""Parsing logic of pipeline/sources/cn_stats_city.py (bulletin text, Wikipedia tables, NBS census table)."""

import unittest

from pipeline.sources import cn_stats_city as m

BULLETIN_HTML = """<html><body><div>首页 统计公报</div>
<p>点击：709次 &nbsp;根据广东省地区生产总值统一核算结果，2023 年揭阳市地区生产总值为 2445.03 亿元，同比增长7.5%。
其中，第一产业增加值为 230.09 亿元，同比增长4.8%；第二产业增加值为 913.79 亿元，同比增长 14.4%；
第三产业增加值为1301.15亿元，同比增长 3.7%。人均地区生产总值 43322 元，增长7.1%。</p>
<p>年末全市户籍人口218.0万人。年末全市常住人口[3]557.20万人，其中城镇常住人口300.1万人，常住人口城镇化率为53.86%。</p>
<p>全市居民人均可支配收入28521元。</p>
<div>站内搜索： 年鉴下载</div></body></html>"""


class BulletinTest(unittest.TestCase):
    def test_full_bulletin(self):
        r = m.parse_bulletin(m.bulletin_text(BULLETIN_HTML))
        self.assertAlmostEqual(r["gdp"], 2445.03e8)
        self.assertAlmostEqual(r["va_primary"], 230.09e8)
        self.assertAlmostEqual(r["va_tertiary"], 1301.15e8)
        self.assertAlmostEqual(r["pop"], 557.20e4)  # not the 户籍 or 城镇 sub-totals
        self.assertEqual(r["urban_pct"], 53.86)
        self.assertEqual(r["income_pc"], 28521)

    def test_fullwidth_digits_and_wanyuan(self):
        t = m.bulletin_text("点击：1次 全年全市地区生产总值实现1871．5亿元，比上年增长5．1％。站内搜索")
        self.assertAlmostEqual(m.parse_gdp(t), 1871.5e8)
        t = m.bulletin_text("点击：1次 实现地区生产总值（GDP）7782689万元，比上年增长5.0%。站内搜索")
        self.assertAlmostEqual(m.parse_gdp(t), 7782689e4)

    def test_rejects_per_capita_and_regional_totals(self):
        t = m.bulletin_text("点击：1次 人均地区生产总值60580元。东部地区生产总值652084亿元。站内搜索")
        self.assertIsNone(m.parse_gdp(t))

    def test_inconsistent_sectors_dropped(self):
        t = m.bulletin_text("点击：1次 地区生产总值886.7亿元。第一产业增加值460.4亿元，第二产业增加值716.2亿元，"
                            "第三产业增加值1020.9亿元。站内搜索")
        r = m.parse_bulletin(t)
        self.assertIsNone(r["va_primary"])
        self.assertAlmostEqual(r["gdp"], 886.7e8)

    def test_partial_sectors_dropped(self):
        r = m.parse_bulletin(m.bulletin_text("点击：1次 地区生产总值100亿元。第二产业增加值40亿元。站内搜索"))
        self.assertIsNone(r["va_secondary"])

    def test_missing_text_gives_nulls(self):
        r = m.parse_bulletin(m.bulletin_text("点击：1次 附件:某市2023年统计公报.pdf 站内搜索"))
        self.assertTrue(all(v is None for v in r.values()))

    def test_index(self):
        html = ('<li><a href="/tjgb/19gd/37985.html" title="揭阳市2023年国民经济和社会发展统计公报">x</a></li>'
                '<li><a href="/tjgb/19gd/37000.html" title="揭阳市2022年国民经济和社会发展统计公报">x</a></li>'
                '<li><a href="/tjgb/24gz/1.html" title="&#8205;贵州省2023年国民经济和社会发展 统计公报">x</a></li>')
        self.assertEqual(m.bulletin_index(html), [("/tjgb/19gd/37985.html", "揭阳市"), ("/tjgb/24gz/1.html", "贵州省")])


class WikiTest(unittest.TestCase):
    def test_city_census(self):
        text = ("{|\n|-\n|[[广东]]||[[深圳市]]||副省级市||17,494,398||17,444,609||17,438,289\n"
                "|-\n|[[安徽]]||[[潜山市]]||县级市||441,224||1||1\n|}")
        self.assertEqual(m.parse_city_census(text), {"深圳市": 17494398})

    def test_prov_census(self):
        text = ("== 2020年 ==\n{| class=\"wikitable\"\n|+ 2020年人口列表\n|-\n! 行政区\n! [[常住人口]]（第七次全国人口普查）\n"
                "|-\n! 2020年11月1日\n|-\n! [[新疆维吾尔自治区]]\n| 25,852,345 || 全国第21位\n"
                "|-\n! [[昌吉州]]\n| 1,613,585 || 6 || 6.24\n|}\n== 2015年 ==\n{|\n|-\n! [[昌吉州]]\n| 1,428,587\n|}")
        self.assertEqual(m.parse_prov_census(text), {"新疆维吾尔自治区": 25852345, "昌吉州": 1613585})

    def test_prov_census_after_heading_and_disambiguation(self):
        text = ("== 第七次全国人口普查 ==\n正文\n{|\n|-\n! [[白山市 (吉林省)|白山市]]\n| 968,373\n|| 7 || 4.0\n|}")
        self.assertEqual(m.parse_prov_census(text), {"白山市": 968373})

    def test_wiki_gdp(self):
        text = ("== 2023年 ==\n{|\n|+ 2023年广东省各地级市地区生产总值列表\n|-\n! 行政区\n! 本币（亿元）\n"
                "|-\n![[深圳市]]\n| align=\"right\" |34,606|| align=\"right\" |4,911|| align=\"right\" | 1\n|}\n"
                "== 2022年 ==\n{|\n|+ 2022年\n|-\n![[深圳市]]\n| 32,388\n|}")
        self.assertEqual(m.parse_wiki_gdp(text), {"深圳市": 34606e8})

    def test_nbs_census3(self):
        html = ("<p>表 3-1 各地区人口</p><table><tr><td>全 国 [5]</td><td>1411778724</td><td>100.00</td></tr>"
                "<tr><td>北 京</td><td>21893095</td><td>1.55</td></tr><tr><td>内蒙古</td><td>24049155</td>"
                "<td>1.70</td></tr></table>")
        self.assertEqual(m.parse_nbs_census3(html), {"全国": 1411778724, "北京": 21893095, "内蒙古": 24049155})


class MoreBulletinTest(unittest.TestCase):
    def test_pop_in_persons_and_total_population(self):
        t = m.bulletin_text("点击：1次 年末全市常住人口3376503人,比上年末减少17969人。站内搜索")
        self.assertEqual(m.parse_pop(t), 3376503)
        t = m.bulletin_text("点击：1次 年末全省总人口为2339.41万人,人口减少数量由上年的27.68万人下降至8.28万人。站内搜索")
        self.assertEqual(m.parse_pop(t), 23394100)

    def test_registered_population_is_not_resident(self):
        for text in ("年末全市户籍总人口为149.95万人", "据公安年报数据显示,全区年末总人口378356人",
                     "全市城镇新增就业人口8823人"):
            self.assertIsNone(m.parse_pop(m.bulletin_text(f"点击：1次 {text}。站内搜索")), text)

    def test_gdp_rejected_match_does_not_hide_the_next(self):
        t = m.bulletin_text("点击：1次 根据地区生产总值统一核算结果,2023年1-4季度怒江州实现地区生产总值262.09亿元,"
                            "同比增长6.2%。站内搜索")
        self.assertAlmostEqual(m.parse_gdp(t), 262.09e8)

    def test_attachment_links(self):
        html = ('<a href="/x.html">首页</a> 点击：5次 <a href="http://tjj.example.gov.cn/a/gb.pdf">附件</a>'
                '<a href="https://example.gov.cn/download?id=1&amp;f=2">某市2023年公报.docx</a>'
                '<a href="/tjgb/1.html">下一篇</a> 站内搜索 <a href="/z.pdf">z</a>')
        self.assertEqual(m.attachment_links(html), ["http://tjj.example.gov.cn/a/gb.pdf",
                                                    "https://example.gov.cn/download?id=1&f=2"])


class YearbookTest(unittest.TestCase):
    def test_year_column(self):
        rows = [["3-7 各市年末常住人口数"], ["市别", "City", 2022.0, 2023.0], [],
                ["全    省", "Total", 12656.8, 12706.0], ["广    州", "Guangzhou", 1873.41, 1882.7], ["按经济区域分", None, None, None]]
        self.assertEqual(m.yearbook_values(rows, "year"), {"全省": 12706.0, "广州": 1882.7})

    def test_fixed_column_and_text_values(self):
        rows = [["地区", "Region", "年末总人口(万人)"], ["青 岛 市", "Qingdao", "1037.15(854.78)"]]
        self.assertEqual(m.yearbook_values(rows, 2), {"青岛市": 1037.15})

    def test_long_layout(self):
        rows = [["西安市 Xi'an", None], [2022.0, 11506.1], ["2023", 12010.76], ["铜川市 Tongchuan", None], ["2023", 512.4]]
        self.assertEqual(m.yearbook_values(rows, ("long", 1)), {"西安市": 12010.76, "铜川市": 512.4})


if __name__ == "__main__":
    unittest.main()
