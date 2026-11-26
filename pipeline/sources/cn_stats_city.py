"""Population and GDP for every prefecture-level unit, province and the nation (docs/CN_PLAN.md §4.5).

Decisions (the plan leaves these open; also logged in the agent report):

* **Target year = 2023** (`year`). It is the latest year for which a uniform, text-parsable official source covers
  most prefectures: the 2023 国民经济和社会发展统计公报 of each city/prefecture and province. 国家统计局's
  data.stats.gov.cn API refuses this pipeline (HTTP 403 "UrlACL"), the 《中国统计年鉴》 web edition publishes its
  tables only as JPG images, and 《中国城市统计年鉴》 is not freely online. The bulletins are read from 中国统计信息网
  (tjcn.org), which republishes each 统计局's bulletin text (source office named on every page); a value is used only
  when it is printed in the visible text. Where the page text lacks a value and tjcn links the bureau's own
  attachment (.pdf/.docx/.doc on the bureau's server), that file is downloaded (dead links: the Internet Archive's
  raw copy, web.archive.org/web/2024id_/…) and its text extracted (pipeline/sources/_doc_text.py) and parsed with
  the same rules (src key `att_gb2023`). Remaining gaps are filled from the provincial 《统计年鉴2024》 city tables
  (the .xls each yearbook publishes next to its .jpg; read by _xls.py; src key `yb2024`) for the provinces in
  YEARBOOKS. A yearbook value never replaces a bulletin value (disagreements > 1 % are logged). Anything still
  missing stays null (never an older year).
* Fields per record: the plan's (`pop_census`, `pop`, `pop_year`, `gdp`, `gdp_year`, `va_*`, `currency`,
  `gdp_local`, `fx_rate`, `src`, `note`) plus two optional ones: `urban_pct` (常住人口城镇化率, %, as printed) and
  `income_pc` (全体居民人均可支配收入, 元/人), both of `year`, from the same bulletin.
* Units: 亿元 × 1e8 → 元, 万人 × 1e4 → 人 (rounded to whole yuan / persons; the bulletins print 0.01 亿元 / 0.01 万人).
* Plausibility filters (a mis-parse must give null, not a wrong number): GDP must agree with 第一+二+三产业 within
  3 % when all three are printed (otherwise the three sector values are dropped, GDP kept only if the parse is
  unambiguous); the 2023 population must lie within ±25 % of the 2020 census.
* 七普 2020 census (`pop_census`): provinces from 国家统计局《第七次全国人口普查公报（第三号）》; 香港/澳门/台湾 from
  《公报（第二号）》 (the figures NBS prints for them). Prefecture-level cities (地级市/副省级市/直辖市) from the Chinese
  Wikipedia list 中华人民共和国城市人口排名, which transcribes 国务院人口普查办公室《中国人口普查分县资料—2020》;
  自治州/地区/盟 (not in that list) from the Chinese Wikipedia per-province population lists (各市州人口列表), which
  transcribe the provincial 七普公报 or the same 分县资料.
* GDP fallback for cities whose bulletin text is missing: the Chinese Wikipedia per-province GDP list pages that
  have a 2023 table transcribed from the provincial 统计年鉴2024 (广东、江苏、江西、辽宁; in 亿元, 广东/江苏 rounded
  to whole 亿元).
  These pages also serve as a second source for spot checks.
* 香港: GDP at current prices (HK$ million, 政府统计处 table 310-31001) converted with the 2023 average of the daily
  HKMA HKD-per-CNY rate (`fx_rate` = CNY per HKD); year-end 2023 population from 统计处 table 110-01001. No
  three-sector split (null). 澳门: GDP from the 澳门统计暨普查局 figure quoted (with citation) on the Chinese
  Wikipedia 中國地級市人均地區生產總值列表, converted at the MOP-HKD peg (1 HKD = 1.03 MOP) and the same HKMA rate;
  2023 population null (DSEC's database could not be read). 台湾: census figure from NBS 公报（第二号）; 2023
  population (island total and the six cities) = year-end household-registration population from 内政部户政司's open
  data ODRP019 (village rows summed by city); GDP null (no PRC statistics) — stated in `note`.
* The nation (`nation`): 国家统计局《2023年国民经济和社会发展统计公报》 (31 省区市, 不含港澳台).
* The 30 省直辖县级行政区 are included under `cities` when their bulletin was found (useful for province checks).
  三沙 (460300) is present with nulls.
"""

from __future__ import annotations

import json
import re
import urllib.parse

from ..core import fetch
from ..core.china import PROVINCES, MUNICIPALITIES, TAIWAN_CITIES
from ..core.paths import INTERIM
from ..core.source import Source, SourceInfo
from ._doc_text import document_text
from ._resource_util import download_checked
from ._xls import xls_rows

YEAR = 2023
CENSUS_YEAR = 2020

TJCN = "http://www.tjcn.org"
TJCN_PROVINCES = ["01bj", "02tj", "03hb", "04sx", "05nmg", "06ln", "07jl", "08hlj", "09sh", "10js", "11zj", "12ah",
                  "13fj", "14jx", "15sd", "16hn", "17hb", "18hn", "19gd", "20gx", "21hn", "22cq", "23sc", "24gz",
                  "25yn", "26xz", "27sx", "28gs", "29qh", "30nx", "31xj"]
TJCN_PAGES = 3  # newest first; the 2023 bulletins sit on pages 1–2

WAYBACK = "https://web.archive.org/web/2024id_/"  # raw archived copy of an official attachment whose link died
NBS_C7_2 = "https://www.stats.gov.cn/sj/tjgb/rkpcgb/qgrkpcgb/202302/t20230206_1902002.html"
NBS_C7_3 = "https://www.stats.gov.cn/sj/tjgb/rkpcgb/qgrkpcgb/202302/t20230206_1902003.html"
NBS_GB2023 = "https://www.stats.gov.cn/sj/zxfb/202402/t20240228_1947915.html"

# Provincial statistical yearbooks 2024 (data for 2023), table by table as the .xls each yearbook publishes next to
# its .jpg: (province, yearbook title, [(url, field, multiplier to persons/元, column)]). `column` is the raw column
# index of the value, "year" for the column headed 2023, or ("long", k) for a table laid out as a name row followed
# by one row per year (value in column k). Used to fill cities (and province totals) whose 2023
# bulletin is an image, a dead link or reports only 户籍 population; never overrides a bulletin value.
YB_GD = "http://tjnj.gdstats.gov.cn:8080/tjnj/2024/directory/"
YB_HLJ = "https://tjj.hlj.gov.cn/tjjnianjian/2024/zk/html/"
YB_SD = "http://tjj.shandong.gov.cn/tjnj/nj2024/zk/html/"
YB_JX = "http://tjj.jiangxi.gov.cn/resource/nj/2024CD/zk/html/"
YB_SN = "https://tjj.shaanxi.gov.cn/upload/2024/zk/html/"
YB_HA = "https://oss.henan.gov.cn/sbgt-wztipt/attachment/hntjj/hntj/lib/tjnj/2024nj/zk/html/"
YB_SX = "http://tjj.shanxi.gov.cn/tjsj/tjnj/nj2024/zk/html/"
YB_GX = "http://tjj.gxzf.gov.cn/tjsj/tjnj/material/tjnj20200415/2024/zk/html/"
YEARBOOKS = [
    ("44", "广东统计年鉴2024", [(YB_GD + "03/excel/03-07.xls", "pop", 1e4, "year"),
                              (YB_GD + "02/excel/02-14-1.xls", "gdp", 1e8, "year")]),
    ("23", "黑龙江统计年鉴2024", [(YB_HLJ + "02-06.xls", "pop", 1e4, 2), (YB_HLJ + "03-07.xls", "gdp", 1e8, "year")]),
    ("37", "山东统计年鉴2024", [(YB_SD + "03-04.xls", "pop", 1e4, 2), (YB_SD + "02-06.xls", "gdp", 1e8, "year")]),
    ("36", "江西统计年鉴2024", [(YB_JX + "0205.xls", "pop", 1, 2), (YB_JX + "0116.xls", "gdp", 1e8, 2)]),
    ("61", "陕西统计年鉴2024", [(YB_SN + "0405.xls", "pop", 1e4, "year"), (YB_SN + "0311.xls", "gdp", 1e8, ("long", 1))]),
    ("41", "河南统计年鉴2024", [(YB_HA + "0303.xls", "pop", 1e4, "year"), (YB_HA + "0210.xls", "gdp", 1e8, 2)]),
    ("14", "山西统计年鉴2024", [(YB_SX + "19-01.xls", "gdp", 1e4, 2)]),
    ("45", "广西统计年鉴2024", [(YB_GX + "03-06.xls", "gdp", 1e8, 2)]),
]

WIKI_RAW = "https://zh.wikipedia.org/w/index.php?action=raw&title="
WIKI_CITY_POP = "中華人民共和國城市人口排名"
WIKI_PROV_POP = ["吉林省各市州人口列表", "湖北省各市州人口列表", "湖南省各市州人口列表", "四川省各市州人口列表",
                 "贵州省各市州人口列表", "云南省各市州人口列表", "西藏自治区各地市人口列表", "甘肃省各市州人口列表",
                 "青海省各市州人口列表", "新疆维吾尔自治区各地州市人口列表", "内蒙古自治区各盟市人口列表",
                 "黑龙江省各地市人口列表"]
WIKI_PROV_GDP = ["广东各地级市地区生产总值列表", "江苏各地级市地区生产总值列表", "江西各地级市地区生产总值列表",
                 "辽宁各地级市地区生产总值列表", "吉林各市州地区生产总值列表"]
WIKI_PERCAP = "中國地級市人均地區生產總值列表"

HK_GDP = "https://www.censtatd.gov.hk/api/get.php?id=310-31001&lang=en&full_series=1"
HK_POP = "https://www.censtatd.gov.hk/api/get.php?id=110-01001&lang=en&full_series=1"
HKMA_DAILY = ("https://api.hkma.gov.hk/public/market-data-and-statistics/monthly-statistical-bulletin/er-ir/"
              "er-eeri-daily?pagesize=1000&offset=")
# Taiwan: 内政部户政司 open data ODRP019 (村里户数、人口数, year-end), year 112 = 2023; paged, 2000 rows per page.
TW_RIS = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore/ODRP019/112?page="
TW_CITY = {"710100": "臺北市", "710200": "新北市", "710300": "桃園市", "710400": "臺中市", "710500": "臺南市",
           "710600": "高雄市"}
MOP_PER_HKD = 1.03  # statutory peg of the pataca to the Hong Kong dollar

SRC = {
    "nbs_c7_2": "国家统计局《第七次全国人口普查公报（第二号）》（港澳台人口）",
    "nbs_c7_3": "国家统计局《第七次全国人口普查公报（第三号）》（各省级行政区人口）",
    "nbs_gb2023": "国家统计局《中华人民共和国2023年国民经济和社会发展统计公报》",
    "wiki_c7_city": "维基百科“中华人民共和国城市人口排名”（转录《中国人口普查分县资料—2020》）",
    "wiki_c7_prov": "维基百科各省“各市州人口列表”（转录各省第七次全国人口普查公报或《中国人口普查分县资料—2020》）",
    "tjcn_gb2023": "各市（州、盟）及各省 2023 年国民经济和社会发展统计公报（经中国统计信息网 tjcn.org 转载的公报正文）",
    "att_gb2023": "各市（州、盟）及各省 2023 年国民经济和社会发展统计公报的官方附件（统计局/政府网站发布的 PDF、Word 文件，"
                  "由 tjcn.org 转载页链接）",
    "yb2024": "各省《统计年鉴2024》分市表（2023 年数据；广东、黑龙江、山东、江西、陕西、河南、山西、广西统计局发布的 "
              "Excel 表）",
    "wiki_gdp2023": "维基百科各省“地区生产总值列表”2023 年表（转录各省《统计年鉴2024》，单位亿元）",
    "hk_csd": "香港特别行政区政府统计处表 310-31001（本地生产总值）、表 110-01001（人口）",
    "hkma": "香港金融管理局每日汇率（港元兑人民币），2023 年平均",
    "tw_ris": "台湾地区内政部户政司开放数据 ODRP019（2023 年底各村里户籍人口，按市汇总）",
    "wiki_mo": "澳门统计暨普查局本地生产总值（转引自维基百科“中國地級市人均地區生產總值列表”注释）",
}

FW = str.maketrans("０１２３４５６７８９．％，：（）［］", "0123456789.%,:()[]")
NUM = r"(\d[\d,]*(?:\.\d+)?)"
_UNIT = {"亿元": 1e8, "万元": 1e4}


# --- text helpers (pure; unit-tested) ------------------------------------------------------------------------


def bulletin_text(html: str) -> str:
    """The visible body of a tjcn bulletin page: tags/entities removed, whitespace dropped, full-width digits and
    punctuation normalised. Starts after the page's "点击：" counter and ends before the footer search box."""
    s = re.sub(r"&[a-zA-Z]+;|&#\d+;", "", html)
    t = re.sub(r"<[^>]+>", "", s)
    t = re.sub(r"\s+", "", t).translate(FW)
    a = t.find("点击:")
    if a < 0:
        a = 0
    b = t.find("站内搜索", a)
    return t[a:b if b > 0 else None]


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def parse_gdp(t: str) -> float | None:
    """地区生产总值 in 元 from bulletin text (first unambiguous '…生产总值…N亿元/万元' phrase)."""
    pat = re.compile(r"生产总值(?:\(GDP\))?([^。;]{0,40}?)" + NUM + r"(亿元|万元)")
    for m0 in re.finditer("生产总值", t):  # every occurrence (a rejected match must not swallow the next one)
        m = pat.match(t, m0.start())
        if m is None:
            continue
        pre, mid = t[max(0, m.start() - 12):m.start()], m.group(1)
        if pre.endswith("人均") or re.search(r"(?<!1-4)季度|东部|中部|西部|东北|经济带|三角洲|湾区|京津冀|民营|县域", pre):
            continue
        if re.search(r"%|增长|下降|比重|占|(?<!1-4)季度|产业|人均|生产总值", mid):
            continue
        return round(_num(m.group(2)) * _UNIT[m.group(3)])
    return None


def parse_sector(t: str, which: str) -> float | None:
    """第一/二/三产业增加值 in 元."""
    m = re.search(rf"第{which}产业(?:实现|完成)?增加值(?:为|达到|达)?" + NUM + r"(亿元|万元)", t)
    return round(_num(m.group(1)) * _UNIT[m.group(2)]) if m else None


def parse_pop(t: str) -> float | None:
    """年末常住人口 in persons: the first '常住(总)人口…N万人/N人' not about urban/rural/registered sub-totals;
    failing that, '年末(全省/全市…)总人口…N万人' (吉林 and some cities print 常住人口 as 年末总人口) unless the
    sentence is about 户籍/公安 registered population."""
    for m in re.finditer(r"常住(?:总)?人口(?:\[\d+\])?(?:总量|总数|规模)?(?:为|达到|达|是)?" + NUM + r"(万人|人)(?!次)", t):
        if re.search(r"(城镇|乡村|农村|城区|流动|户籍|新增|增加|减少)$", t[max(0, m.start() - 4):m.start()]):
            continue
        v = _num(m.group(1)) * (1e4 if m.group(2) == "万人" else 1)
        if v >= 10000:
            return round(v)
    for m in re.finditer(r"年末(?:全[省市州盟区])?总人口(?:\[\d+\])?(?:为|达到|达|是)?" + NUM + r"万人", t):
        if re.search(r"户籍|公安", t[max(0, m.start() - 20):m.start() + 2]):
            continue
        return round(_num(m.group(1)) * 1e4)
    return None


def parse_urban(t: str) -> float | None:
    m = re.search(r"城镇化率(?:\(?常住人口\)?)?(?:为|达到|达)?" + NUM + r"%", t)
    return _num(m.group(1)) if m else None


def parse_income(t: str) -> float | None:
    m = re.search(r"(?:全[市州盟区省]|全体|全国)居民人均可支配收入(?:为|达到|达)?" + NUM + r"元", t)
    return _num(m.group(1)) if m else None


def parse_bulletin(t: str) -> dict:
    """All values of one bulletin text, with the plausibility rule on GDP vs. the three sectors."""
    r = {"gdp": parse_gdp(t), "va_primary": parse_sector(t, "一"), "va_secondary": parse_sector(t, "二"),
         "va_tertiary": parse_sector(t, "三"), "pop": parse_pop(t), "urban_pct": parse_urban(t),
         "income_pc": parse_income(t)}
    va = [r["va_primary"], r["va_secondary"], r["va_tertiary"]]
    if all(v is not None for v in va):
        if r["gdp"] is None or abs(sum(va) - r["gdp"]) > 0.03 * r["gdp"]:
            r["va_primary"] = r["va_secondary"] = r["va_tertiary"] = None
    else:
        r["va_primary"] = r["va_secondary"] = r["va_tertiary"] = None  # never a partial split
    return r


def bulletin_index(html: str, year: int = YEAR) -> list[tuple[str, str]]:
    """(href, unit name) of '<unit><year>年国民经济和社会发展统计公报' links on a tjcn list page."""
    out = []
    for href, title in re.findall(r'href="(/tjgb/\w+/\d+\.html)"\s+title="([^"]+)"', html):
        title = re.sub(r"\s+|&#8205;|‍", "", title)
        m = re.match(rf"(.+?){year}年国民经济和社会发展统计公报$", title)
        if m:
            out.append((href, m.group(1)))
    return out


def attachment_links(html: str, base: str = TJCN) -> list[str]:
    """Absolute URLs of the official attachments (.pdf/.doc/.docx) linked inside a tjcn bulletin's body: the file
    the statistics bureau published, which tjcn links to (on the bureau's own server) when it did not copy the text.
    A link counts when its href or its anchor text ends in one of those extensions."""
    a, b = html.find("点击"), html.find("站内搜索")
    body = html[a if a >= 0 else 0:b if b > a else None]
    out: list[str] = []
    for href, text in re.findall(r'<a\b[^>]*?href="([^"]+)"[^>]*>(.*?)</a>', body, re.S | re.I):
        href = href.replace("&amp;", "&").strip()
        text = re.sub(r"<[^>]+>|\s|&nbsp;", "", text)
        if re.search(r"\.(pdf|docx?)(?:$|[?&#])", href, re.I) or re.search(r"\.(pdf|docx?)$", text, re.I):
            url = urllib.parse.urljoin(base, href)
            url = urllib.parse.quote(url, safe=":/?&=%#+~@!$,;'()*[]")
            if url.startswith("http") and url not in out:
                out.append(url)
    return out


def attachment_rel(href: str, k: int, url: str) -> str:
    """Cache path of attachment k of the bulletin page `href`."""
    ext = (re.search(r"\.(pdf|docx?)(?:$|[?&#])", url, re.I) or re.search(r"\.(pdf|docx?)", url, re.I))
    return f"tjcn/att{YEAR}/{href.rsplit('/', 1)[1].split('.')[0]}_{k}.{ext.group(1).lower() if ext else 'bin'}"


def yearbook_values(rows: list[list], column, year: int = YEAR) -> dict[str, float]:
    """{row name (spaces removed): value} of a yearbook table read by xls_rows. `column` is a raw column index or
    "year" (the first column whose header cell is `year`). A value may be printed as text like '1037.15(854.78)'
    (the leading number is taken). Rows without a name or value are skipped."""
    if isinstance(column, tuple) and column[0] == "long":  # a name row, then one row per year: (year, value…)
        out_l: dict[str, float] = {}
        name = None
        for r in rows:
            if not r or r[0] is None:
                continue
            first = str(r[0]).strip().removesuffix(".0")
            if re.fullmatch(r"\d{4}", first):
                if first == str(year) and name and column[1] < len(r) and isinstance(r[column[1]], float):
                    out_l.setdefault(name, r[column[1]])
            elif first:
                name = re.sub(r"\s+", "", re.split(r"[A-Za-z]", first)[0])
        return out_l
    start = 0
    if column == "year":
        col = None
        for h, r in enumerate(rows[:15]):
            for k, x in enumerate(r):
                if (isinstance(x, float) and x == year) or (isinstance(x, str) and x.strip() == str(year)):
                    col, start = k, h + 1  # data rows follow the header row
                    break
            if col is not None:
                break
        if col is None:
            return {}
    else:
        col = column
    out: dict[str, float] = {}
    for r in rows[start:]:
        if not r or not isinstance(r[0], str) or col >= len(r):
            continue
        name = re.sub(r"\s+", "", r[0])
        x = r[col]
        if isinstance(x, str):
            m = re.match(r"\s*(\d+(?:\.\d+)?)", x)
            x = float(m.group(1)) if m else None
        if name and isinstance(x, float) and x > 0:
            out.setdefault(name, x)
    return out


def link_name(target: str) -> str:
    """'白山市 (吉林省)' → '白山市' (drop a Wikipedia disambiguation suffix)."""
    return re.sub(r"\s*[(（][^)）]*[)）]$", "", target.strip())


def wiki_rows(text: str):
    """Yield (first wikilink target, [cells]) for every table row of a wikitext."""
    for row in re.split(r"\n\|-[^\n]*", text):
        m = re.search(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]", row)
        if not m:
            continue
        cells = [c.strip() for c in re.split(r"\|\||!!|\n[|!]", row)]
        yield link_name(m.group(1)), cells


def _int_cell(c: str) -> int | None:
    c = re.sub(r"<ref[^>]*/>|<ref.*?</ref>|align=\"?\w+\"?\s*\||style=\"[^\"]*\"\s*\|", "", c).strip()
    return int(c.replace(",", "")) if re.fullmatch(r"\d{1,3}(?:,\d{3})+|\d+", c) else None


def _num_cell(c: str) -> float | None:
    c = re.sub(r"<ref[^>]*/>|<ref.*?</ref>|align=\"?\w+\"?\s*\||style=\"[^\"]*\"\s*\|", "", c).strip()
    return _num(c) if re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", c) else None


def parse_city_census(text: str) -> dict[str, int]:
    """中华人民共和国城市人口排名: {city name: 2020 census population} for 地级市/副省级市/直辖市/特别行政区 rows."""
    out = {}
    for row in re.split(r"\n\|-[^\n]*", text):
        cells = [c.strip() for c in row.strip().lstrip("|").split("||")]
        if len(cells) < 4 or cells[2] not in ("地级市", "副省级市", "直辖市", "特别行政区"):
            continue
        m = re.search(r"\[\[([^\]|]+)", cells[1])
        v = _int_cell(cells[3])
        if m and v:
            out.setdefault(link_name(m.group(1)), v)
    return out


def parse_prov_census(text: str) -> dict[str, int]:
    """各市州人口列表: rows of the first table after a 七普 header ('第七次全国人口普查' / '2020年11月1日'):
    {unit link name: first integer cell (persons)}."""
    i = min([k for k in (text.find("第七次全国人口普查"), text.find("2020年11月1日")) if k >= 0], default=-1)
    if i < 0:
        return {}
    start = text.rfind("{|", 0, i)
    if start < 0 or "\n|}" in text[start:i]:  # the marker is a heading, not inside a table: take the next table
        start = text.find("{|", i)
    end = text.find("\n|}", start)
    out = {}
    for name, cells in wiki_rows(text[start:end]):
        for c in cells[1:]:
            v = _int_cell(c)
            if v is not None and v >= 10000:
                out.setdefault(name, v)
                break
    return out


def parse_wiki_gdp(text: str, year: int = YEAR) -> dict[str, float]:
    """Provincial GDP list page: the '|+ <year>年…' table, first numeric cell (亿元) per row → 元."""
    m = re.search(rf"\n\|\+\s*{year}年[^\n]*", text)
    if not m:
        return {}
    end = text.find("\n|}", m.end())
    out = {}
    for name, cells in wiki_rows(text[m.end():end]):
        for c in cells[1:]:
            v = _num_cell(c)
            if v is not None:
                out.setdefault(name, round(v * 1e8))
                break
    return out


def parse_nbs_census3(html: str) -> dict[str, int]:
    """七普公报第三号 table 3-1: {province short name: persons} (+ '全国')."""
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace("&nbsp;", " ")
    i = t.find("表 3-1")
    out = {}
    for name, v in re.findall(r"(?<![一-鿿])([一-鿿] [一-鿿]|[一-鿿]{2,3}) (?:\[\d\] )?(\d{6,10}) \d+\.\d\d", t[i:]):
        out.setdefault(name.replace(" ", ""), int(v))
    return out


# --- the source -----------------------------------------------------------------------------------------------


class CnStatsCity(Source):
    info = SourceInfo(
        id="cn_stats_city",
        title="地级行政区、省级行政区及全国的人口与地区生产总值（七普 2020；2023 年统计公报）",
        publisher="国家统计局；各省、市（州、盟）统计局（2023 年统计公报，经中国统计信息网转载；统计年鉴2024）；"
                  "香港特别行政区政府统计处、香港金融管理局；台湾地区内政部户政司；维基百科（转录官方普查资料与统计年鉴）",
        homepage="https://www.stats.gov.cn/",
        license="政府公开统计资料；维基百科文字 CC BY-SA 4.0；香港政府数据依其使用条款；台湾地区政府资料开放授权",
        description=(
            "每个地级行政区（含直辖市、香港、澳门）与省级行政区的：第七次全国人口普查常住人口（2020-11-01）；"
            "2023 年末常住人口；2023 年地区生产总值（现价，元）及第一、二、三产业增加值；常住人口城镇化率；"
            "全体居民人均可支配收入。2023 年数据取自各市（州、盟）及各省的 2023 年国民经济和社会发展统计公报正文"
            "（中国统计信息网 tjcn.org 转载，并注明原发布统计局）；公报正文缺失时，读取 tjcn 页面链接的统计局官方附件（PDF/Word，失效链接取互联网档案馆存档副本）；仍缺的，以广东、"
            "黑龙江、山东、江西、陕西、河南、山西、广西《统计年鉴2024》分市表补充；广东、江苏、江西、辽宁、吉林再以维基百科转录的"
            "各省《统计年鉴2024》GDP 补充；仍缺的留空（不以其他年份替代）。"
            "七普人口：省级取国家统计局《第七次全国人口普查公报（第三号）》，港澳台取《公报（第二号）》；地级市取维基百科"
            "“中华人民共和国城市人口排名”（转录《中国人口普查分县资料—2020》）；自治州、地区、盟取维基百科各省人口列表"
            "（转录各省七普公报）。全国数据取国家统计局《2023年国民经济和社会发展统计公报》，为 31 个省、自治区、直辖市合计，"
            "不含港澳台。香港 GDP 取政府统计处表 310-31001（港元），按香港金融管理局 2023 年日均港元兑人民币汇率折算；"
            "澳门 GDP 取澳门统计暨普查局数据（经维基百科转引），按 1 港元 = 1.03 澳门元及同一汇率折算。台湾地区 2023 年人口取台湾地区内政部户政司"
            "开放数据（年底户籍人口），GDP 无相应统计、留空。src 键：" + "；".join(f"{k} = {v}" for k, v in SRC.items()) + "。"
        ),
        used_for=["城市人口与金币（GDP）产出", "省级人口与 GDP", "全国合计", "人口增长（2020→2023）",
                  "三次产业增加值（粮食、生产力产出的备选口径）"],
    )

    # --- fetch -------------------------------------------------------------------------------------------------

    def _wiki(self, title: str, force: bool) -> None:
        download_checked(self, WIKI_RAW + urllib.parse.quote(title), f"wiki/{title}.txt", force=force,
                         skip_errors=True)

    def fetch(self, force: bool = False) -> None:
        for url, rel in ((NBS_C7_2, "nbs/c7_2.html"), (NBS_C7_3, "nbs/c7_3.html"), (NBS_GB2023, "nbs/gb2023.html"),
                         (HK_GDP, "hk/310-31001.json"), (HK_POP, "hk/110-01001.json")):
            self.download(url, rel, force=force)
        for title in [WIKI_CITY_POP, WIKI_PERCAP, *WIKI_PROV_POP, *WIKI_PROV_GDP]:
            self._wiki(title, force)
        for prov, _title, tables in YEARBOOKS:
            for url, *_ in tables:
                try:
                    download_checked(self, url, self._yb_rel(prov, url), force=force, binary=True,
                                     magic=b"\xd0\xcf\x11\xe0", skip_errors=True)
                except fetch.OfflineError:
                    pass
        # HKMA daily rates, newest first: page back until the target year is fully covered.
        offset = 0
        while offset < 10000:
            p = self.download(HKMA_DAILY + str(offset), f"hkma/daily_{offset}.json", force=force)
            recs = json.loads(p.read_text())["result"]["records"]
            if not recs or recs[-1]["end_of_day"] < f"{YEAR}-01-01":
                break
            offset += 1000
        # Taiwan: 内政部户政司 village-level population, all pages.
        page = 1
        while page < 20:
            try:
                pth = download_checked(self, TW_RIS + str(page), f"tw/odrp019_112_p{page}.json", force=force,
                                       skip_errors=True)
            except fetch.OfflineError:
                break
            if pth is None:
                break
            d = json.loads(pth.read_text(encoding="utf-8"), strict=False)
            if page >= int(d.get("totalPage") or 1):
                break
            page += 1
        # tjcn: list pages, then each 2023 bulletin. Pages and bulletins that answered 404 online are
        # never cached, so --offline treats an uncached one as "not there" (process skips it too).
        def get(url: str, rel: str, **kw):
            try:
                return download_checked(self, url, rel, skip_errors=True, **kw)
            except fetch.OfflineError:
                return None

        for prov in TJCN_PROVINCES:
            for n in range(1, TJCN_PAGES + 1):
                name = "" if n == 1 else f"index_{n}.html"
                p = get(f"{TJCN}/tjgb/{prov}/{name}", f"tjcn/list/{prov}_{n}.html", force=force)
                if p is None:
                    break
                for href, unit in bulletin_index(p.read_bytes().decode("gb18030", "replace")):
                    page = get(TJCN + href, f"tjcn/gb{YEAR}/{href.rsplit('/', 1)[1]}")
                    if page is None:
                        continue
                    html = page.read_bytes().decode("gb18030", "replace")
                    if all(parse_bulletin(bulletin_text(html))[k] is not None for k in ("gdp", "pop", "va_primary")):
                        continue  # the page text already has everything
                    for k, url in enumerate(attachment_links(html, TJCN + href)):
                        rel = attachment_rel(href, k, url)
                        if get(url, rel, binary=True) is None:  # dead link: the Internet Archive's copy, if any
                            get(WAYBACK + url, rel, binary=True)

    @staticmethod
    def _yb_rel(prov: str, url: str) -> str:
        return f"yearbook/{prov}_" + url.rsplit("/", 1)[1]

    # --- process -----------------------------------------------------------------------------------------------

    def _read(self, rel: str, enc: str = "utf-8") -> str | None:
        p = self.raw_dir / rel
        if not p.exists():
            return None
        self._used.add(rel)
        return p.read_bytes().decode(enc, "replace")

    def process(self) -> dict:
        self._used: set[str] = set()
        bounds = json.loads((INTERIM / "cn_boundaries.json").read_text(encoding="utf-8"))["data"]
        prefs = [p for p in bounds["prefectures"] if p["kind"] != "taiwan_city"]

        # name → id lookup (prefectures: full, short, short+州/市/地区/盟; provinces: full and short)
        lookup: dict[str, str] = {}
        for code, (full, short, _abbr, _kind) in PROVINCES.items():
            pid = code + "0000" if code in MUNICIPALITIES else code
            lookup[full] = lookup[short] = pid
        for p in prefs:
            for k in (p["name"], p["short"], p["short"] + "州", p["short"] + "市", p["short"] + "地区",
                      p["short"] + "盟", re.sub(r"[一-鿿]+?族(?:[一-鿿]+?族)*自治州$|"
                                               r"(?:蒙古|哈萨克|柯尔克孜)自治州$", "州", p["name"])):
                lookup.setdefault(k, p["adcode"])

        def ident(name: str) -> str | None:
            name = re.sub(r"\s+|‍", "", name)
            return lookup.get(name) or lookup.get(re.sub(r"(特别行政区|自治区|省)$", "", name))

        def blank(name: str) -> dict:
            return {"name": name, "pop_census": None, "pop": None, "pop_year": None, "gdp": None, "gdp_year": None,
                    "va_primary": None, "va_secondary": None, "va_tertiary": None, "urban_pct": None,
                    "income_pc": None, "currency": "CNY", "gdp_local": None, "fx_rate": None, "src": {},
                    "note": None}

        cities = {p["adcode"]: blank(p["name"]) for p in prefs}
        for cid, cname, _q in TAIWAN_CITIES:
            cities[cid] = blank(cname)
            cities[cid]["note"] = "台湾地区：无国家统计局分市统计，人口与 GDP 留空。"
        provinces = {code: blank(full) for code, (full, *_r) in PROVINCES.items()}
        log: list[str] = []

        def rec(i: str) -> dict | None:
            """Record for an id (a municipality id fills both its city and its province record)."""
            return cities.get(i) or provinces.get(i)

        def put(i: str, field: str, value, src: str, srckey: str | None = None) -> None:
            targets = [rec(i)]
            if i.endswith("0000") and i[:2] in MUNICIPALITIES:
                targets.append(provinces[i[:2]])
            for r in targets:
                if r is not None and value is not None and r[field] is None:
                    r[field] = value
                    r["src"][srckey or field] = src

        # 1. census — provinces (NBS 第三号) and 港澳台 (第二号)
        c3 = parse_nbs_census3(self._read("nbs/c7_3.html") or "")
        for short, v in c3.items():
            code = ident(short)
            if code and (code in provinces or code[:2] in MUNICIPALITIES):
                put(code, "pop_census", v, "nbs_c7_3")
        c2 = bulletin_text(self._read("nbs/c7_2.html") or "")
        for key, code in (("香港特别行政区人口", "81"), ("澳门特别行政区人口", "82"), ("台湾地区人口", "71")):
            m = re.search(key + r"(?:\[\d+\])?为" + NUM + "人", c2)
            if m:
                v = int(_num(m.group(1)))
                put(code, "pop_census", v, "nbs_c7_2")
                if code in ("81", "82"):
                    put(code + "0000", "pop_census", v, "nbs_c7_2")
        nation = blank("全国")
        if "全国" in c3:
            nation["pop_census"], nation["src"]["pop_census"] = c3["全国"], "nbs_c7_3"

        # 2. census — cities (分县资料 list), then 自治州/地区/盟 from the provincial lists
        for name, v in parse_city_census(self._read(f"wiki/{WIKI_CITY_POP}.txt") or "").items():
            i = ident(name)
            if i and i in cities and i[:2] not in ("81", "82"):
                put(i, "pop_census", v, "wiki_c7_city")
        for title in WIKI_PROV_POP:
            for name, v in parse_prov_census(self._read(f"wiki/{title}.txt") or "").items():
                i = ident(name)
                if i and i in cities:
                    put(i, "pop_census", v, "wiki_c7_prov")

        # 3. 2023 bulletins (cities, provinces, municipalities)
        seen: set[str] = set()
        for prov in TJCN_PROVINCES:
            for n in range(1, TJCN_PAGES + 1):
                html = self._read(f"tjcn/list/{prov}_{n}.html", "gb18030")
                if html is None:
                    break
                for href, unit in bulletin_index(html):
                    i = ident(unit)
                    rel = f"tjcn/gb{YEAR}/{href.rsplit('/', 1)[1]}"
                    if i is None:
                        log.append(f"未匹配的公报单位：{unit}")
                        continue
                    if i in seen:
                        continue
                    page = self._read(rel, "gb18030")
                    if page is None:
                        continue
                    seen.add(i)
                    v = parse_bulletin(bulletin_text(page))
                    vsrc = dict.fromkeys(v, "tjcn_gb2023")
                    if any(v[k] is None for k in ("gdp", "pop", "va_primary")):
                        for k, url in enumerate(attachment_links(page, TJCN + href)):
                            p_att = self.raw_dir / attachment_rel(href, k, url)
                            if not p_att.exists():
                                continue
                            self._used.add(attachment_rel(href, k, url))
                            text = re.sub(r"\s+", "", document_text(p_att.read_bytes())).translate(FW)
                            va = parse_bulletin(text)
                            for f in ("gdp", "pop", "urban_pct", "income_pc"):
                                if v[f] is None and va[f] is not None:
                                    v[f], vsrc[f] = va[f], "att_gb2023"
                            if v["va_primary"] is None and va["va_primary"] is not None:
                                for f in ("va_primary", "va_secondary", "va_tertiary"):
                                    v[f], vsrc[f] = va[f], "att_gb2023"
                        if v["va_primary"] is not None and \
                                abs(v["va_primary"] + v["va_secondary"] + v["va_tertiary"] - v["gdp"]) > 0.03 * v["gdp"]:
                            v["va_primary"] = v["va_secondary"] = v["va_tertiary"] = None  # mixed sources disagree
                    r = rec(i)
                    if v["pop"] is not None and r and r["pop_census"]:
                        if not 0.75 <= v["pop"] / r["pop_census"] <= 1.25:
                            log.append(f"{unit}: 2023 人口 {v['pop']:.0f} 与七普 {r['pop_census']} 相差过大，舍弃")
                            v["pop"] = None
                    if v["gdp"] is not None:
                        put(i, "gdp", round(v["gdp"]), vsrc["gdp"])
                        put(i, "gdp_year", YEAR, vsrc["gdp"], "gdp")
                    if v["va_primary"] is not None:
                        for f in ("va_primary", "va_secondary", "va_tertiary"):
                            put(i, f, round(v[f]), vsrc["va_primary"], "va")
                    if v["pop"] is not None:
                        put(i, "pop", round(v["pop"]), vsrc["pop"])
                        put(i, "pop_year", YEAR, vsrc["pop"], "pop")
                    put(i, "urban_pct", v["urban_pct"], vsrc["urban_pct"], "urban_pct")
                    put(i, "income_pc", v["income_pc"], vsrc["income_pc"], "income_pc")

        # 4. provincial statistical yearbooks 2024 (2023 data): fill what the bulletins did not give
        yb_filled = 0
        for prov, title, tables in YEARBOOKS:
            for url, field, mult, column in tables:
                rel = self._yb_rel(prov, url)
                p = self.raw_dir / rel
                if not p.exists():
                    continue
                self._used.add(rel)
                vals = yearbook_values(xls_rows(p.read_bytes()), column)
                if not vals:
                    log.append(f"{title} {url.rsplit('/', 1)[1]}: 未读出数据")
                for name, x in vals.items():
                    i = prov if re.fullmatch(r"全(省|区|市)(总计|合计)?|总计|合计", name) else ident(name)
                    if i is None or (i not in provinces and i[:2] != prov):
                        continue
                    if i in provinces and prov in MUNICIPALITIES:
                        i = prov + "0000"
                    r = rec(i)
                    v = round(x * mult)
                    if r is None:
                        continue
                    if field == "pop" and r["pop_census"] and not 0.75 <= v / r["pop_census"] <= 1.25:
                        log.append(f"{title}: {name} 人口 {v} 与七普相差过大，舍弃")
                        continue
                    if r[field] is not None:
                        if abs(r[field] - v) > 0.01 * v:
                            log.append(f"{r['name']}: 公报 {field} {r[field]:.4g} 与{title} {v:.4g} 相差超过 1 %")
                        continue
                    put(i, field, v, "yb2024")
                    put(i, field + "_year", YEAR, "yb2024", field)
                    yb_filled += 1
        print(f"    yearbooks: {yb_filled} values filled")

        # 5. GDP fallback / second source: provincial Wikipedia GDP tables (2023)
        wiki_gdp: dict[str, float] = {}
        for title in WIKI_PROV_GDP:
            for name, v in parse_wiki_gdp(self._read(f"wiki/{title}.txt") or "").items():
                i = ident(name)
                if i:
                    wiki_gdp[i] = v
        agree = 0
        for i, v in wiki_gdp.items():
            r = rec(i)
            if r is not None and r["gdp"] and abs(r["gdp"] - v) <= max(1e8, 0.01 * v):
                agree += 1
            if r is not None and r["gdp"] is None:
                put(i, "gdp", round(v), "wiki_gdp2023")
                put(i, "gdp_year", YEAR, "wiki_gdp2023", "gdp")
            elif r is not None and r["gdp"] and abs(r["gdp"] - v) > max(1e8, 0.01 * v):
                log.append(f"{r['name']}: 公报 GDP {r['gdp'] / 1e8:.2f} 亿元 与维基 {v / 1e8:.0f} 亿元 不一致")

        print(f"    cross-check: {agree} bulletin GDP values agree with the Wikipedia yearbook tables (±1 %)")

        # 6. 香港 / 澳门 / 台湾
        cny_per_hkd = self._hkd_rate()
        hk = self._hk(cny_per_hkd)
        for i in ("810000", "81"):
            r = rec(i)
            if hk.get("gdp_hkd") and cny_per_hkd:
                r.update(gdp=round(hk["gdp_hkd"] * cny_per_hkd), gdp_year=YEAR, gdp_local=hk["gdp_hkd"],
                         fx_rate=round(cny_per_hkd, 6))
                r["src"]["gdp"] = "hk_csd+hkma"
            if hk.get("pop"):
                r.update(pop=hk["pop"], pop_year=YEAR)
                r["src"]["pop"] = "hk_csd"
            r["note"] = ("香港数据取自香港特别行政区政府统计处（GDP 原为港元，按香港金融管理局 2023 年日均汇率折算为人民币；"
                         "无三次产业划分）；七普人口栏为国家统计局公报所列 2020 年底香港人口。")
        mo_gdp = self._macau_gdp()
        for i in ("820000", "82"):
            r = rec(i)
            if mo_gdp and cny_per_hkd:
                rate = cny_per_hkd / MOP_PER_HKD
                r.update(gdp=round(mo_gdp * rate), gdp_year=YEAR, gdp_local=mo_gdp, fx_rate=round(rate, 6))
                r["src"]["gdp"] = "wiki_mo+hkma"
            r["note"] = ("澳门 GDP 为澳门统计暨普查局数据（澳门元，经维基百科转引），按 1 港元 = 1.03 澳门元及香港金融管理局 "
                         "2023 年日均港元兑人民币汇率折算；2023 年末人口未取得，留空；七普人口栏为国家统计局公报所列澳门人口。")
        tw = self._taiwan_pop()
        if tw:
            for cid, v in tw.items():
                if cid in cities:
                    cities[cid].update(pop=v, pop_year=YEAR)
                    cities[cid]["src"]["pop"] = "tw_ris"
                    cities[cid]["note"] = ("台湾地区：人口为台湾地区内政部户政司公布的 2023 年底户籍人口（台湾地区人口统计以户籍登记为准）；"
                                           "无国家统计局分市统计，GDP 等留空。")
            provinces["71"].update(pop=tw["71"], pop_year=YEAR)
            provinces["71"]["src"]["pop"] = "tw_ris"
        provinces["71"]["note"] = ("台湾地区：七普人口栏为国家统计局《第七次全国人口普查公报（第二号）》所列台湾地区人口；"
                                   "2023 年人口为台湾地区内政部户政司公布的年底户籍人口；GDP 等无国家统计局数据，留空。")

        # 7. nation
        nt = bulletin_text(self._read("nbs/gb2023.html") or "")
        m = re.search(r"国内生产总值(?:\[\d+\])?" + NUM + "亿元", nt)
        if m:
            nation.update(gdp=round(_num(m.group(1)) * 1e8), gdp_year=YEAR)
            nation["src"]["gdp"] = "nbs_gb2023"
            va = [parse_sector(nt, w) for w in "一二三"]
            if all(va):
                nation.update(va_primary=round(va[0]), va_secondary=round(va[1]), va_tertiary=round(va[2]))
                nation["src"]["va"] = "nbs_gb2023"
        m = re.search(r"年末全国人口(?:\[\d+\])?" + NUM + "万人", nt)
        if m:
            nation.update(pop=round(_num(m.group(1)) * 1e4), pop_year=YEAR)
            nation["src"]["pop"] = "nbs_gb2023"
        nation["urban_pct"] = parse_urban(nt)
        nation["income_pc"] = parse_income(nt)
        nation["src"].update(urban_pct="nbs_gb2023", income_pc="nbs_gb2023")
        nation["scope"] = "31 个省、自治区、直辖市（不含港澳台）"

        banner = [p["adcode"] for p in bounds["prefectures"]
                  if p["kind"] not in ("county_city",) and p["adcode"] != "460300"]
        cov = {k: sum(1 for i in banner if cities[i][k] is not None) for k in ("pop_census", "pop", "gdp", "va_primary")}
        print(f"    banner units {len(banner)}: " + ", ".join(f"{k} {v}" for k, v in cov.items()))
        for line in log:
            print("    · " + line)
        # drop county_city records without any data (optional in the plan)
        for p in prefs:
            r = cities[p["adcode"]]
            if p["kind"] == "county_city" and r["gdp"] is None and r["pop"] is None and r["pop_census"] is None:
                del cities[p["adcode"]]
        return {
            "vintage": f"七普 {CENSUS_YEAR}；{YEAR} 年统计公报",
            "census_year": CENSUS_YEAR,
            "year": YEAR,
            "cities": cities,
            "provinces": provinces,
            "nation": nation,
            "src_keys": SRC,
            "coverage": {"banner_units": len(banner), **cov},
            "_files": sorted(self._used),
        }

    # --- SAR helpers -------------------------------------------------------------------------------------------

    def _hkd_rate(self) -> float | None:
        """CNY per HKD: 1 / mean of HKMA's daily HKD-per-CNY rates in YEAR."""
        vals, offset = {}, 0
        while (txt := self._read(f"hkma/daily_{offset}.json")) is not None:
            for r in json.loads(txt)["result"]["records"]:
                if r["end_of_day"].startswith(str(YEAR)) and r.get("cny"):
                    vals[r["end_of_day"]] = r["cny"]
            offset += 1000
        return 1 / (sum(vals.values()) / len(vals)) if vals else None

    def _hk(self, rate) -> dict:
        out = {}
        txt = self._read("hk/310-31001.json")
        if txt:
            for r in json.loads(txt)["dataSet"]:
                if r.get("freq") == "Y" and r.get("period") == str(YEAR) and r.get("sv") == "CUR" \
                        and r.get("svDesc") == "HK$ million" and isinstance(r.get("figure"), (int, float)):
                    out["gdp_hkd"] = round(r["figure"] * 1e6)
        txt = self._read("hk/110-01001.json")
        if txt:
            for r in json.loads(txt)["dataSet"]:
                if r.get("period") == f"{YEAR}12" and r.get("SEXDesc") == "Total" and r.get("AGEDesc") == "Total" \
                        and r.get("sv") == "POP" and r.get("svDesc") == "Number ('000)" \
                        and isinstance(r.get("figure"), (int, float)):
                    out["pop"] = round(r["figure"] * 1000)
        return out

    def _taiwan_pop(self) -> dict[str, int]:
        """{Taiwan city id: persons, "71": all of Taiwan} at the end of YEAR from 内政部户政司 ODRP019."""
        total, by_city, page = 0, dict.fromkeys(TW_CITY, 0), 1
        while (txt := self._read(f"tw/odrp019_112_p{page}.json")) is not None:
            for r in json.loads(txt, strict=False).get("responseData") or []:
                v = sum(int(r[k]) for k in r if k.startswith("household_") and k.endswith(("_m", "_f")))
                total += v
                for cid, name in TW_CITY.items():
                    if r.get("site_id", "").startswith(name):
                        by_city[cid] += v
            page += 1
        if not total:
            return {}
        return {**{k: v for k, v in by_city.items() if v}, "71": total}

    def _macau_gdp(self) -> float | None:
        """Macau GDP of YEAR in MOP, from the DSEC figure quoted in the Wikipedia per-capita list's footnote."""
        txt = self._read(f"wiki/{WIKI_PERCAP}.txt") or ""
        m = re.search(r"澳門元\]\]\s*" + NUM + r" million[^.]*?in " + str(YEAR), txt) or \
            re.search(r"MOP\$" + NUM + r" million[^.]*?in " + str(YEAR), txt)
        return round(_num(m.group(1)) * 1e6) if m else None
