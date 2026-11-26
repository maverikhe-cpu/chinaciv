"""Per-prefecture employment by industry and R&D expenditure: what the non-Gold yields are made of.

docs/CN_PLAN.md §4.6 (interim schema) and §5.3 (how yields use it).

What exists, and what this module therefore does
-------------------------------------------------
The preferred source, 《中国城市统计年鉴》 ("按行业分城镇单位从业人员" for all ~297 地级以上城市), is not published
online in any free, machine-readable form (国家统计局 sells it in print/CD; data.stats.gov.cn's API now answers 403).
The provincial statistical yearbooks are online, but a survey of the 2025 editions (data year 2024) showed that
only a few print a *city × industry* employment table; most print only per-city totals or three-sector splits:

  has city × industry employment:  广西 (表4-10), 山东 (表4-4)
  checked, only totals/three sectors: 广东, 湖北, 河南, 陕西, 山西, 福建, 江西, 吉林, 天津 (municipality, whole only)

So the employment-by-industry part covers 30 banner cities (广西 14, 山东 16), far below the 90% that §5.3 needs:
**the yields builder must fall back to three-sector value added (cn_stats_city) for Food/Production**. The data is
still kept (cities.json `employed`/`jobs` for those cities, and for future use) on one basis only:
城镇非私营单位就业人员年末人数 ("employment_urban_nonprivate"). The 广西 table carries fractional persons (the bureau
apportions units spanning several cities); values are rounded to whole persons.

R&D 经费内部支出 (Science priority 2):
  * provinces + nation + the 4 municipalities: 《2024年全国科技经费投入统计公报》 (国家统计局、科学技术部、财政部),
    one uniform table for all 31 provinces (亿元 × 1e8).
  * prefecture cities: the provincial yearbooks that print it by city: 广西 (表19-15), 福建 (表15-5), 山东 (表19-20).
  * 香港、澳门、台湾: not in mainland statistics; null with a note (never 0).

Industry → GB/T 4754-2017 section letters (keys of `emp`, every key always present, null = not published):
  A 农、林、牧、渔业  B 采矿业  C 制造业  D 电力、热力、燃气及水生产和供应业  E 建筑业  F 批发和零售业
  G 交通运输、仓储和邮政业  H 住宿和餐饮业  I 信息传输、软件和信息技术服务业  J 金融业  K 房地产业
  L 租赁和商务服务业  M 科学研究和技术服务业  N 水利、环境和公共设施管理业  O 居民服务、修理和其他服务业
  P 教育  Q 卫生和社会工作  R 文化、体育和娱乐业  S 公共管理、社会保障和社会组织
Yield mapping (§5.3, implemented in build/yields.py): Food = A; Production = B+C+D+E; Science = I+M
(or rd_expenditure); Culture = R.

Parsing is generic so that more provinces can be added to TABLES as they are found: tables are read from the
publisher's .xls (a small stdlib BIFF8/OLE2 reader below) or HTML, the industry header row is the row naming ≥ 12
industries, place rows are matched to cn_boundaries prefecture names of that province, and units come from the
table's "单位：万人/人/万元/亿元" line. A blank or "-" cell is null; a publisher's 0 stays 0.
"""

from __future__ import annotations

import html.parser
import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from ..core.china import BANNER_KINDS, PROVINCES
from ..core.paths import INTERIM
from ..core.source import Source, SourceInfo

SECTIONS = {
    "A": "农、林、牧、渔业", "B": "采矿业", "C": "制造业", "D": "电力、热力、燃气及水生产和供应业", "E": "建筑业",
    "F": "批发和零售业", "G": "交通运输、仓储和邮政业", "H": "住宿和餐饮业", "I": "信息传输、软件和信息技术服务业",
    "J": "金融业", "K": "房地产业", "L": "租赁和商务服务业", "M": "科学研究和技术服务业",
    "N": "水利、环境和公共设施管理业", "O": "居民服务、修理和其他服务业", "P": "教育", "Q": "卫生和社会工作",
    "R": "文化、体育和娱乐业", "S": "公共管理、社会保障和社会组织",
}
# keyword(s) that identify each section in a yearbook header (Chinese part only; exactly one must match)
_KEYWORDS = [
    ("A", ("农、林、牧、渔", "农林牧渔")), ("B", ("采矿",)), ("C", ("制造",)), ("D", ("电力", "燃气及水")),
    ("E", ("建筑",)), ("F", ("批发",)), ("G", ("交通运输",)), ("H", ("住宿",)), ("I", ("信息传输",)),
    ("J", ("金融",)), ("K", ("房地产",)), ("L", ("租赁",)), ("M", ("科学研究",)), ("N", ("水利",)),
    ("O", ("居民服务",)), ("P", ("教育",)), ("Q", ("卫生",)), ("R", ("文化、体育", "文化体育")),
    ("S", ("公共管理",)),
]
NO_STATS = {"71": "台湾", "81": "香港", "82": "澳门"}
NO_STATS_NOTE = "港澳台不在内地统计年鉴与全国科技经费公报的统计范围内，本项暂缺（置空，非 0）"
EXCLUDED_CITIES = {"460300"}  # 三沙: no banner (CN_PLAN §3.1)

GX = "http://tjj.gxzf.gov.cn/tjsj/tjnj/material/tjnj20200415/2025/zk/html/"
SD = "http://tjj.shandong.gov.cn/tjnj/nj2025/zk/html/"
FJ = "https://tjj.fujian.gov.cn/tongjinianjian/dz2025/zk/html/"
NBS_RD = "https://www.stats.gov.cn/sj/zxfb/202509/t20250929_1961429.html"


@dataclass(frozen=True)
class Table:
    key: str  # the `src` value written on every record taken from this table
    kind: str  # "emp_city" | "rd_city" | "rd_provinces"
    province: str | None
    year: int
    url: str
    title: str


TABLES = [
    Table("gx_tjnj2025", "emp_city", "45", 2024, GX + "04-10.xls",
          "《广西统计年鉴2025》表4-10 城镇非私营单位分市分行业从业人员（2024年）"),
    Table("sd_tjnj2025", "emp_city", "37", 2024, SD + "04-04.xls",
          "《山东统计年鉴2025》表4-4 各市年底就业人员数（2024年底，城镇非私营单位分行业）"),
    Table("gx_tjnj2025", "rd_city", "45", 2024, GX + "19-15.xls",
          "《广西统计年鉴2025》表19-15 各市研究与试验发展（R&D）活动情况"),
    Table("fj_tjnj2025", "rd_city", "35", 2024, FJ + "15-05.xls",
          "《福建统计年鉴2025》表15-5 各设区市研究与试验发展（R&D）经费情况（2024年）"),
    Table("sd_tjnj2025", "rd_city", "37", 2024, SD + "19-20.xls",
          "《山东统计年鉴2025》表19-20 R&D经费支出情况（2024年，按地区分）"),
    Table("nbs_rd2024", "rd_provinces", None, 2024, NBS_RD,
          "国家统计局、科学技术部、财政部《2024年全国科技经费投入统计公报》表 分地区R&D经费"),
]


# --- xls (BIFF8 in an OLE2 compound file), standard library only ----------------------------------------------


def _cfb_stream(data: bytes, names: tuple[str, ...] = ("Workbook", "Book")) -> bytes:
    """The named stream of an OLE2 compound file (handles the FAT, the DIFAT chain and the mini stream)."""
    if data[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise ValueError("not an OLE2 compound file")
    ssz = 1 << struct.unpack_from("<H", data, 0x1E)[0]
    mssz = 1 << struct.unpack_from("<H", data, 0x20)[0]
    dir_start, = struct.unpack_from("<I", data, 0x30)
    cutoff, minifat_start, _n_minifat, difat_start, n_difat = struct.unpack_from("<IIIII", data, 0x38)

    def sector(i: int) -> bytes:
        return data[512 + i * ssz: 512 + (i + 1) * ssz]

    difat = [x for x in struct.unpack_from("<109I", data, 0x4C)]
    nxt = difat_start
    for _ in range(n_difat):
        if nxt >= 0xFFFFFFFA:
            break
        vals = struct.unpack(f"<{ssz // 4}I", sector(nxt))
        difat.extend(vals[:-1])
        nxt = vals[-1]
    fat: list[int] = []
    for s in difat:
        if s < 0xFFFFFFFA:
            fat.extend(struct.unpack(f"<{ssz // 4}I", sector(s)))

    def chain(start: int, table: list[int]) -> list[int]:
        out, seen = [], set()
        while start < 0xFFFFFFFA and start not in seen and start < len(table):
            seen.add(start)
            out.append(start)
            start = table[start]
        return out

    dirdata = b"".join(sector(s) for s in chain(dir_start, fat))
    entries = []
    for off in range(0, len(dirdata) - 127, 128):
        nlen, = struct.unpack_from("<H", dirdata, off + 0x40)
        name = dirdata[off: off + max(nlen - 2, 0)].decode("utf-16-le", "replace")
        etype = dirdata[off + 0x42]
        start, size = struct.unpack_from("<II", dirdata, off + 0x74)
        entries.append((name, etype, start, size))
    root = entries[0]
    for want in names:
        for name, etype, start, size in entries:
            if etype != 2 or name != want:
                continue
            if size >= cutoff:
                return b"".join(sector(s) for s in chain(start, fat))[:size]
            ministream = b"".join(sector(s) for s in chain(root[2], fat))
            minifat: list[int] = []
            for s in chain(minifat_start, fat):
                minifat.extend(struct.unpack(f"<{ssz // 4}I", sector(s)))
            return b"".join(ministream[m * mssz:(m + 1) * mssz] for m in chain(start, minifat))[:size]
    raise ValueError("no Workbook stream")


def _records(stream: bytes, pos: int = 0):
    while pos + 4 <= len(stream):
        rtype, rlen = struct.unpack_from("<HH", stream, pos)
        yield pos, rtype, stream[pos + 4: pos + 4 + rlen]
        pos += 4 + rlen


def _rk(v: int) -> float:
    if v & 2:
        num = float(v >> 2 if v < 0x80000000 else (v >> 2) - (1 << 30))
    else:
        num = struct.unpack("<d", struct.pack("<Q", (v & 0xFFFFFFFC) << 32))[0]
    return num / 100 if v & 1 else num


def _sst(chunks: list[bytes]) -> list[str]:
    """Shared strings; `chunks` = the SST record body followed by its CONTINUE bodies."""
    ci, pos = 0, 8
    total, = struct.unpack_from("<I", chunks[0], 4)
    out: list[str] = []

    def take(n: int) -> bytes:  # raw bytes that may span chunks (no flag byte: rich runs, ext data, headers)
        nonlocal ci, pos
        buf = b""
        while n > 0:
            if pos >= len(chunks[ci]):
                ci, pos = ci + 1, 0
            part = chunks[ci][pos: pos + n]
            buf += part
            pos += len(part)
            n -= len(part)
        return buf

    for _ in range(total):
        if ci >= len(chunks) or (ci == len(chunks) - 1 and pos >= len(chunks[ci])):
            break
        cch, = struct.unpack("<H", take(2))
        flags = take(1)[0]
        runs = struct.unpack("<H", take(2))[0] if flags & 8 else 0
        ext = struct.unpack("<I", take(4))[0] if flags & 4 else 0
        chars, wide = [], flags & 1
        left = cch
        while left > 0:
            if pos >= len(chunks[ci]):  # string continues in the next CONTINUE: new flag byte
                ci, pos = ci + 1, 0
                wide = chunks[ci][0] & 1
                pos = 1
            width = 2 if wide else 1
            n = min(left, (len(chunks[ci]) - pos) // width)
            raw = chunks[ci][pos: pos + n * width]
            chars.append(raw.decode("utf-16-le") if wide else raw.decode("latin-1"))
            pos += n * width
            left -= n
        take(runs * 4 + ext)
        out.append("".join(chars))
    return out


def xls_rows(path: Path, sheet: int = 0) -> list[list]:
    """Cell values of one worksheet of a BIFF8 .xls as a dense list of rows (str, float or None)."""
    stream = _cfb_stream(Path(path).read_bytes())
    sst: list[str] = []
    sheets: list[int] = []
    sst_chunks: list[bytes] | None = None
    for _pos, rtype, body in _records(stream):
        if rtype == 0x0085:  # BOUNDSHEET
            sheets.append(struct.unpack_from("<I", body, 0)[0])
        elif rtype == 0x00FC:  # SST
            sst_chunks = [body]
        elif rtype == 0x003C and sst_chunks is not None:  # CONTINUE of SST
            sst_chunks.append(body)
        else:
            if sst_chunks is not None:
                sst = _sst(sst_chunks)
                sst_chunks = None
            if rtype == 0x000A:  # EOF of the globals
                break
    if sst_chunks is not None:
        sst = _sst(sst_chunks)
    cells: dict[tuple[int, int], object] = {}
    pending_formula: tuple[int, int] | None = None
    for _pos, rtype, body in _records(stream, sheets[sheet]):
        if rtype == 0x00FD:  # LABELSST
            r, c, _xf, i = struct.unpack_from("<HHHI", body)
            cells[r, c] = sst[i] if i < len(sst) else ""
        elif rtype == 0x0203:  # NUMBER
            r, c, _xf, v = struct.unpack_from("<HHHd", body)
            cells[r, c] = v
        elif rtype == 0x027E:  # RK
            r, c, _xf, v = struct.unpack_from("<HHHI", body)
            cells[r, c] = _rk(v)
        elif rtype == 0x00BD:  # MULRK
            r, c = struct.unpack_from("<HH", body)
            n = (len(body) - 6) // 6
            for k in range(n):
                _xf, v = struct.unpack_from("<HI", body, 4 + 6 * k)
                cells[r, c + k] = _rk(v)
        elif rtype == 0x0204:  # LABEL (old style)
            r, c, _xf, cch, flags = struct.unpack_from("<HHHHB", body)
            raw = body[9:]
            cells[r, c] = raw[: cch * 2].decode("utf-16-le") if flags & 1 else raw[:cch].decode("latin-1")
        elif rtype == 0x0006:  # FORMULA: cached result
            r, c, _xf = struct.unpack_from("<HHH", body)
            res = body[6:14]
            if res[6:8] == b"\xff\xff":
                if res[0] == 0:  # string: value in the following STRING record
                    pending_formula = (r, c)
            else:
                cells[r, c] = struct.unpack("<d", res)[0]
        elif rtype == 0x0207 and pending_formula is not None:  # STRING
            cch, flags = struct.unpack_from("<HB", body)
            raw = body[3:]
            cells[pending_formula] = raw[: cch * 2].decode("utf-16-le") if flags & 1 else raw[:cch].decode("latin-1")
            pending_formula = None
        elif rtype == 0x000A:
            break
    if not cells:
        return []
    nr = max(r for r, _ in cells) + 1
    nc = max(c for _, c in cells) + 1
    return [[cells.get((r, c)) for c in range(nc)] for r in range(nr)]


# --- HTML tables ---------------------------------------------------------------------------------------------


class _TableParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._span = 1

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            try:
                self._span = max(1, int(dict(attrs).get("colspan") or 1))
            except ValueError:
                self._span = 1

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._row.extend([""] * (self._span - 1))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self.tables:
                self.tables[-1].append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def html_tables(text: str) -> list[list[list[str]]]:
    p = _TableParser()
    p.feed(text)
    return [t for t in p.tables if t]


# --- table interpretation ------------------------------------------------------------------------------------


def norm(v) -> str:
    """Cell text without any whitespace (yearbooks pad names: "济 南 市", full-width spaces)."""
    return re.sub(r"[\s　\xa0]+", "", "" if v is None else str(v))


def chinese_part(v) -> str:
    """The Chinese label before the English translation that yearbooks append in the same cell."""
    s = norm(v)
    m = re.search(r"[A-Za-z]", s)
    return s[: m.start()] if m else s


def industry_of(v) -> str | None:
    """GB/T 4754 section letter named by a header cell, or None (also None for totals and ambiguous text)."""
    s = chinese_part(v)
    if not s or "合计" in s or "总计" in s:
        return None
    hits = [letter for letter, kws in _KEYWORDS if any(k in s for k in kws)]
    return hits[0] if len(hits) == 1 else None


def to_number(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = norm(v).replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None  # "", "-", "…", "—": not published


def unit_factor(rows: list[list], upto: int, default: float = 1.0) -> float:
    """Multiplier from the table's unit line ("单位：万人" → 1e4) above row `upto`."""
    for row in rows[:upto]:
        for cell in row:
            s = norm(cell)
            m = re.match(r"单位[:：](.+)", s)
            if m:
                return _unit(m.group(1), default)
    return default


def _unit(s: str, default: float) -> float:
    for k, f in (("亿元", 1e8), ("万元", 1e4), ("万人", 1e4), ("元", 1.0), ("人", 1.0)):
        if s.startswith(k):
            return f
    return default


class PlaceMatcher:
    """Matches a row label to a prefecture of one province (or the province total)."""

    TOTAL = "__total__"

    def __init__(self, prefectures: list[dict], province: str):
        self.by_label: dict[str, str] = {}
        for p in prefectures:
            if p["province"] == province:
                self.by_label[norm(p["name"])] = p["adcode"]
                self.by_label.setdefault(norm(p["short"]), p["adcode"])
        prov = PROVINCES[province]
        self.totals = {"总计", "合计", "全省", "全区", "全市", "全省总计", "全区总计", "全省合计", "全区合计",
                       norm(prov[0]), norm(prov[1])}

    def match(self, label) -> str | None:
        s = chinese_part(label)
        if not s:
            return None
        if s in self.totals:
            return self.TOTAL
        if s in self.by_label:
            return self.by_label[s]
        for name, code in self.by_label.items():  # "延边州" vs "延边朝鲜族自治州"
            if len(s) >= 2 and (name.startswith(s) or s.startswith(name)) and len(name) >= 2:
                return code
        return None


def _row_label(row: list) -> str:
    for cell in row[:2]:
        if re.search(r"[一-鿿]", norm(cell)):
            return norm(cell)
    return ""


def parse_emp_city(rows: list[list], matcher: PlaceMatcher) -> dict[str, dict]:
    """{adcode | PlaceMatcher.TOTAL: {"emp": {A..S}, "emp_total": n}} from a city × industry table."""
    header = None
    for i, row in enumerate(rows):
        cols = {c: industry_of(v) for c, v in enumerate(row)}
        cols = {c: s for c, s in cols.items() if s}
        if len(set(cols.values())) >= 12:
            header = (i, cols)
            break
    if header is None:
        raise ValueError("no industry header row")
    h, cols = header
    factor = unit_factor(rows, h)
    first = min(cols)
    total_col = None
    for r in rows[max(0, h - 3): h + 1]:
        s = norm(r[first - 1]) if first - 1 < len(r) else ""
        if any(k in s for k in ("合计", "总计", "非私营", "Total")):
            total_col = first - 1
    out = {}
    for row in rows[h + 1:]:
        code = matcher.match(_row_label(row))
        if code is None or code in out:
            continue
        emp = {s: None for s in SECTIONS}
        for c, s in cols.items():
            v = to_number(row[c]) if c < len(row) else None
            emp[s] = None if v is None else int(round(v * factor))
        tv = to_number(row[total_col]) if total_col is not None and total_col < len(row) else None
        out[code] = {"emp": emp, "emp_total": None if tv is None else int(round(tv * factor))}
    return out


def _column_headers(rows: list[list], upto: int) -> list[str]:
    """Joined header text per column, with merged group labels (e.g. "2024年") carried rightwards."""
    width = max((len(r) for r in rows[:upto]), default=0)
    heads = [""] * width
    for row in rows[:upto]:
        carry = ""
        for c in range(width):
            s = norm(row[c]) if c < len(row) else ""
            if re.fullmatch(r"(19|20)\d\d年?(\.0)?", s):
                carry = s
            if s:
                heads[c] += "|" + s
            elif carry:
                heads[c] += "|" + carry
    return heads


def parse_rd_city(rows: list[list], matcher: PlaceMatcher, year: int) -> dict[str, float]:
    """{adcode | TOTAL: R&D 经费内部支出 in 元} from a per-city R&D table (picks the `year` column if years vary)."""
    first = next((i for i, r in enumerate(rows) if matcher.match(_row_label(r)) not in (None,)), None)
    if first is None:
        raise ValueError("no place rows")
    heads = _column_headers(rows, first)
    place_rows = [r for r in rows[first:] if matcher.match(_row_label(r)) is not None]
    numeric = {c for r in place_rows for c, v in enumerate(r) if to_number(v) is not None}
    cands = [c for c, t in enumerate(heads)
             if c in numeric and "R&D经费" in t and not any(k in t for k in ("外部", "强度", "人员", "基础", "应用",
                                                                              "试验发展", "政府", "企业资金"))]
    years = [c for c in cands if re.search(r"(19|20)\d\d", t := heads[c]) and "年" in t]
    if years:
        cands = [c for c in years if f"{year}年" in heads[c]]
    if not cands:
        raise ValueError("no R&D expenditure column")
    col = cands[0]
    t = heads[col]
    factor = _unit(re.search(r"(亿元|万元|元)", t).group(1), 1.0) if re.search(r"(亿元|万元|元)", t) \
        else unit_factor(rows, first, 1e4)
    out = {}
    for row in rows[first:]:
        code = matcher.match(_row_label(row))
        if code is None or code in out:
            continue
        v = to_number(row[col]) if col < len(row) else None
        out[code] = None if v is None else round(v * factor)
    return out


def parse_rd_provinces(tables: list[list[list[str]]]) -> tuple[dict[str, float], float | None]:
    """({province code: 元}, nation 元) from the national 公报's "地区 | R&D经费（亿元）" table."""
    by_short = {norm(p[1]): code for code, p in PROVINCES.items()}
    for t in tables:
        hdr = next((r for r in t if r and norm(r[0]) == "地区"), None)
        if hdr is None or not any("R&D经费" in norm(c) and "亿元" in norm(c) for c in hdr):
            continue
        col = next(i for i, c in enumerate(hdr) if "R&D经费" in norm(c) and "强度" not in norm(c))
        provinces, nation = {}, None
        for r in t:
            if len(r) <= col:
                continue
            v = to_number(r[col])
            label = norm(r[0])
            if label == "全国":
                nation = None if v is None else round(v * 1e8)
            elif label in by_short:
                provinces[by_short[label]] = None if v is None else round(v * 1e8)
        return provinces, nation
    raise ValueError("no provincial R&D table")


def blank_record() -> dict:
    return {"emp": {s: None for s in SECTIONS}, "emp_total": None, "emp_year": None, "rd_expenditure": None,
            "rd_year": None, "src": {"emp": None, "rd_expenditure": None}, "note": None}


# --- the source ----------------------------------------------------------------------------------------------


def _rel(t: Table) -> str:
    name = t.url.rstrip("/").rsplit("/", 1)[-1]
    return f"{t.key}/{name}" if t.kind != "rd_provinces" else f"{t.key}/gongbao.html"


class CnCityIndustry(Source):
    info = SourceInfo(
        id="cn_city_industry",
        title="分行业城镇非私营单位就业人员与 R&D 经费（地级市、省、全国）",
        publisher="广西壮族自治区统计局、山东省统计局、福建省统计局（各省统计年鉴2025）；国家统计局、科学技术部、财政部"
        "（2024年全国科技经费投入统计公报）",
        homepage="https://www.stats.gov.cn/sj/zxfb/202509/t20250929_1961429.html",
        license="政府公开统计资料（统计年鉴、统计公报），可自由引用并注明出处",
        description="按国民经济行业门类（GB/T 4754-2017，A–S 共 19 个门类）分的城镇非私营单位就业人员年末人数（2024 年），"
        "以及研究与试验发展（R&D）经费内部支出（2024 年）。《中国城市统计年鉴》无免费在线版本；各省统计年鉴中仅广西"
        "（表4-10，来源键 gx_tjnj2025）与山东（表4-4，sd_tjnj2025）公布分市分行业就业人员，其余省份只公布分市合计或三次产业，"
        "故分行业就业数据只覆盖这两省的 30 个地级市，其余城市置空（非 0）；广西原表含按比例分摊产生的小数，已四舍五入到人。"
        "R&D 经费：31 个省（区、市）、全国及四个直辖市取自《2024年全国科技经费投入统计公报》（nbs_rd2024，亿元换算为元）；"
        "地级市取自广西（表19-15）、福建（表15-5，fj_tjnj2025）、山东（表19-20）统计年鉴（万元换算为元）。"
        "港澳台不在上述统计范围内，置空。产量映射：粮食=A 农林牧渔业；生产力=B 采矿+C 制造+D 电力热力燃气水+E 建筑；"
        "科研=I 信息传输软件和信息技术服务+M 科学研究和技术服务（或 R&D 经费）；文化=R 文化体育和娱乐业。",
        used_for=["城市与省份的就业人数（覆盖城市）", "科研产量备选口径（R&D 经费）", "粮食、生产力、科研、文化产量的行业构成"],
    )

    def fetch(self, force: bool = False) -> None:
        for t in TABLES:
            self.download(t.url, _rel(t), force=force, expect_binary=t.url.endswith(".xls"))

    def _rows(self, t: Table) -> list[list]:
        path = self.raw_dir / _rel(t)
        if t.url.endswith(".xls"):
            return xls_rows(path)
        raw = path.read_bytes()
        for enc in ("utf-8", "gb18030"):
            try:
                return html_tables(raw.decode(enc))  # type: ignore[return-value]
            except UnicodeDecodeError:
                continue
        raise ValueError(f"cannot decode {path}")

    def process(self) -> dict:
        b = json.loads((INTERIM / "cn_boundaries.json").read_text(encoding="utf-8"))["data"]
        prefectures = b["prefectures"]
        cities = {p["adcode"]: blank_record() for p in prefectures
                  if p["kind"] in BANNER_KINDS and p["adcode"] not in EXCLUDED_CITIES}
        provinces = {code: blank_record() for code in PROVINCES}
        nation = blank_record()
        for code, rec in [*((c, cities[c]) for c in cities), *provinces.items()]:
            if code[:2] in NO_STATS:
                rec["note"] = NO_STATS_NOTE
        used, sources = [], {}

        for t in TABLES:
            rows = self._rows(t)
            used.append(_rel(t))
            sources.setdefault(t.key, []).append({"title": t.title, "url": t.url, "kind": t.kind, "year": t.year})
            if t.kind == "rd_provinces":
                prov, nat = parse_rd_provinces(rows)  # type: ignore[arg-type]
                for code, v in prov.items():
                    _set_rd(provinces[code], v, t)
                    if code + "0000" in cities:  # municipalities are cities too
                        _set_rd(cities[code + "0000"], v, t)
                _set_rd(nation, nat, t)
                continue
            matcher = PlaceMatcher(prefectures, t.province)
            if t.kind == "emp_city":
                parsed = parse_emp_city(rows, matcher)
                for code, v in parsed.items():
                    rec = provinces[t.province] if code == PlaceMatcher.TOTAL else cities.get(code)
                    if rec is not None:
                        rec.update(emp=v["emp"], emp_total=v["emp_total"], emp_year=t.year)
                        rec["src"]["emp"] = t.key
                        _check_sum(code, v, t)
            elif t.kind == "rd_city":
                for code, v in parse_rd_city(rows, matcher, t.year).items():
                    if code != PlaceMatcher.TOTAL and code in cities:
                        _set_rd(cities[code], v, t)

        n = len(cities)
        cov_emp = sum(1 for c in cities.values() if c["emp_total"] is not None)
        cov_rd = sum(1 for c in cities.values() if c["rd_expenditure"] is not None)
        print(f"    cities: {n}; employment by industry {cov_emp} ({cov_emp / n:.0%}); R&D {cov_rd} ({cov_rd / n:.0%})")
        return {
            "vintage": "各省统计年鉴2025（2024年数据）；2024年全国科技经费投入统计公报",
            "year": 2024,
            "basis": "employment_urban_nonprivate",
            "basis_label": "城镇非私营单位就业人员年末人数（人）",
            "industries": SECTIONS,
            "coverage": {"cities": n, "emp": cov_emp, "rd_expenditure": cov_rd,
                         "emp_share": round(cov_emp / n, 3), "rd_share": round(cov_rd / n, 3),
                         "note": "分行业就业仅广西、山东公布分市数据，不足 §5.3 所需的 90%，产量应回退到三次产业增加值口径"},
            "sources": sources,
            "cities": cities,
            "provinces": provinces,
            "nation": nation,
            "_files": sorted(set(used)),
        }


def _set_rd(rec: dict, v, t: Table) -> None:
    rec["rd_expenditure"] = v
    rec["rd_year"] = t.year if v is not None else None
    rec["src"]["rd_expenditure"] = t.key if v is not None else None


def _check_sum(code: str, v: dict, t: Table) -> None:
    parts = [x for x in v["emp"].values() if x is not None]
    if v["emp_total"] and parts:
        gap = abs(sum(parts) - v["emp_total"]) / v["emp_total"]
        if gap > 0.02:
            print(f"      warning: {t.key} {code}: sections sum {sum(parts)} vs total {v['emp_total']} ({gap:.1%})")
