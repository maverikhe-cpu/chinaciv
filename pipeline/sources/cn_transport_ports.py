"""Principal ports: 交通运输部《YYYY年港口货物、集装箱吞吐量》(全国主要港口, latest full year).

Interim `cn_transport_ports` (docs/CN_PLAN.md §4.8). The Ministry of Transport publishes the table monthly (综合
规划司, 统计数据 → 港口吞吐量); the annual issue "YYYY年港口货物、集装箱吞吐量" (released late January) carries
the full-year cumulative figures. Since 2024 it lists the 主要港口 of 《全国港口与航道布局规划》 (65 ports, coastal
and inland) instead of all 规模以上港口. It is published as a PDF only; `read_pdf_cells` is a small
standard-library reader for this kind of PDF (uncompressed object table, FlateDecode content streams, Identity-H
fonts with ToUnicode maps, one BT…ET block per text run).

Table: 港口 | 货物吞吐量 (万吨) 累计, 同比 | 外贸货物吞吐量 (万吨) 累计, 同比 | 集装箱吞吐量 (万TEU) 累计, 同比.
Rows: 全国总计, "1.沿海合计", province rows, then their ports ("其中：大连", then indented "营口" …), and the
same again under "2.内河合计". Province rows sit at the left margin, port rows start with "其中：" or are
indented. The three municipalities (天津, 上海, 重庆) have no port rows: their province row **is** the port.
Province rows without ports (e.g. 福建 inland, 贵州, 云南) are totals, not ports, and are skipped.

Decisions (the plan leaves these open):

* Units multiplied out: 万吨 × 1e4 → tonnes, 万TEU × 1e4 → TEU (the publisher's precision is 1 万).
* Blank cells and "…" (below one unit) are null — the publisher's note says a blank is "无该项数据或为零", so it is
  not written as 0. "–" only affects the growth columns, which are not kept.
* 上海 appears twice (coastal 81,153 万吨 and inland 8,032 万吨), and 南通 as 南通(沿海)/南通(沿江): each row is
  its own record, as published, with `kind` coastal/inland; the inland 上海 record is named "上海港（内河）".
* `id` is a pinyin slug (`PINYIN`), `name` the label + "港".
* Position: the publisher gives none. Each port is placed at the DataV seat (cn_boundaries interim, GCJ-02, not
  converted) of the county that holds its main port area (`PORT_COUNTY`, e.g. 宁波舟山 → 北仑区, 上海 → 浦东新区,
  黄骅 → 黄骅市), else of its prefecture. `coord_src` says which ("datav_county_seat" / "datav_prefecture_seat").
  Run `process cn_boundaries` first; without it positions are null. Good to a few tens of km for spread-out
  port groups (北部湾港 = 钦州/北海/防城港 is placed at 钦州港 区).
* `extra` (香港, 澳门, 高雄 …): not in the Ministry table; their own figures were not fetched → empty, see
  `extra_note`.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import zlib

from ..core.paths import INTERIM
from ..core.source import Source, SourceInfo

MOT_INDEX = "https://www.mot.gov.cn/shuju/tongjishuju/gangkoutuntuliang/"
INDEX_PAGES = ["index.html", "index_1.html"]
ANNUAL_RE = re.compile(r'href="(https://xxgk\.mot\.gov\.cn/[^"]+\.html)"[^>]*>\s*(?:<span[^>]*>)?\s*'
                       r'(\d{4})年港口货物、集装箱吞吐量\s*<')
PDF_RE = re.compile(r'href="\./(P\d+\.pdf)"[^>]*>\s*(\d{4})年港口货物、集装箱吞吐量')

# Column x boundaries (PDF points) of the six numeric columns; label text is left of COL0.
COL0, COL_W, NCOLS = 148.56, 62.28, 6
LABEL_INDENT_X = 108.0  # port rows without "其中：" start right of this; province rows start at ~92

MUNICIPALITY_PORTS = {"天津": "120000", "上海": "310000", "重庆": "500000"}

# label (as printed, whitespace removed) → pinyin slug
PINYIN = {
    "大连": "dalian", "营口": "yingkou", "秦皇岛": "qinhuangdao", "黄骅": "huanghua", "唐山": "tangshan",
    "天津": "tianjin", "烟台": "yantai", "青岛": "qingdao", "日照": "rizhao", "上海": "shanghai",
    "连云港": "lianyungang", "南通(沿海)": "nantong-coastal", "宁波舟山": "ningbo-zhoushan", "温州": "wenzhou",
    "福州": "fuzhou", "厦门": "xiamen", "汕头": "shantou", "深圳": "shenzhen", "广州": "guangzhou",
    "珠海": "zhuhai", "湛江": "zhanjiang", "北部湾港": "beibuwan", "海口": "haikou", "洋浦": "yangpu",
    "哈尔滨": "harbin", "佳木斯": "jiamusi", "济宁": "jining", "南京": "nanjing", "镇江": "zhenjiang",
    "苏州": "suzhou", "南通(沿江)": "nantong-river", "徐州": "xuzhou", "无锡": "wuxi", "淮安": "huaian",
    "杭州": "hangzhou", "嘉兴内河": "jiaxing-inland", "湖州": "huzhou", "马鞍山": "maanshan", "芜湖": "wuhu",
    "安庆": "anqing", "合肥": "hefei", "蚌埠": "bengbu", "南昌": "nanchang", "九江": "jiujiang", "周口": "zhoukou",
    "武汉": "wuhan", "黄石": "huangshi", "襄阳": "xiangyang", "荆州": "jingzhou", "宜昌": "yichang",
    "长沙": "changsha", "岳阳": "yueyang", "常德": "changde", "佛山": "foshan", "肇庆": "zhaoqing",
    "清远": "qingyuan", "南宁": "nanning", "柳州": "liuzhou", "贵港": "guigang", "梧州": "wuzhou",
    "来宾": "laibin", "重庆": "chongqing", "泸州": "luzhou", "宜宾": "yibin",
}
# label → prefecture adcode where the label is not the prefecture's short name
PORT_PREFECTURE = {
    "黄骅": "130900", "宁波舟山": "330200", "北部湾港": "450700", "洋浦": "460400",
    "南通(沿海)": "320600", "南通(沿江)": "320600", "嘉兴内河": "330400", **MUNICIPALITY_PORTS,
}
# label → county adcode of the main port area (coastal ports far from the city seat). Others: prefecture seat.
PORT_COUNTY = {
    "大连": "210213", "营口": "210804", "黄骅": "130983", "唐山": "130209", "天津": "120116", "青岛": "370211",
    "日照": "371102", "上海": "310115", "连云港": "320703", "南通(沿海)": "320623", "宁波舟山": "330206",
    "福州": "350105", "厦门": "350205", "深圳": "440308", "广州": "440115", "珠海": "440404", "湛江": "440803",
    "北部湾港": "450702", "海口": "460105", "苏州": "320585", "南京": "320113",
}


# --- minimal PDF text reader ------------------------------------------------------


def _objects(pdf: bytes) -> dict[int, bytes]:
    return {int(n): body for n, body in re.findall(rb"(?<![0-9])(\d+) 0 obj(.*?)endobj", pdf, re.S)}


def _stream(obj: bytes) -> bytes:
    m = re.search(rb"stream\r?\n(.*?)endstream", obj, re.S)
    if not m:
        raise ValueError("PDF object has no stream")
    data = m.group(1)
    return zlib.decompress(data) if b"/FlateDecode" in obj.split(b"stream", 1)[0] else data


def _tounicode(cmap: str) -> dict[int, str]:
    out: dict[int, str] = {}
    for blk in re.findall(r"beginbfchar(.*?)endbfchar", cmap, re.S):
        for a, u in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            out[int(a, 16)] = bytes.fromhex(u).decode("utf-16-be")
    for blk in re.findall(r"beginbfrange(.*?)endbfrange", cmap, re.S):
        for a, z, u in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            for k in range(int(a, 16), int(z, 16) + 1):
                out[k] = chr(int(u, 16) + k - int(a, 16))
    return out


def read_pdf_cells(pdf: bytes) -> list[list[tuple[float, float, str]]]:
    """Per page, the text runs as (x, y, text), y downwards as drawn (this PDF flips the CTM).

    Handles only what WPS/Office tables export: Identity-H (2-byte) fonts with /ToUnicode, a `Tm` per BT block,
    hex strings shown with Tj. Run text inside a block is concatenated (TD moves within a run are ignored).
    """
    objs = _objects(pdf)
    pages = []
    for _, body in sorted(objs.items()):
        if b"/Type /Page" not in body or b"/Contents" not in body:
            continue
        content = _stream(objs[int(re.search(rb"/Contents (\d+) 0 R", body).group(1))]).decode("latin-1")
        fonts = {}
        for name, ref in re.findall(rb"/(\w+) (\d+) 0 R", body.split(b"/Font", 1)[1].split(b">>", 1)[0]):
            tu = re.search(rb"/ToUnicode (\d+) 0 R", objs[int(ref)])
            fonts[name.decode()] = _tounicode(_stream(objs[int(tu.group(1))]).decode("latin-1")) if tu else {}
        cells = []
        for bt in re.findall(r"BT(.*?)ET", content, re.S):
            f = re.search(r"/(\w+) [\d.]+ Tf", bt)
            tm = re.search(r"([-\d.]+) ([-\d.]+) Tm", bt)
            if not f or not tm:
                continue
            cmap = fonts.get(f.group(1), {})
            text = "".join(cmap.get(int(h[i:i + 4], 16), "")
                           for h in re.findall(r"<([0-9A-Fa-f]+)>", bt) for i in range(0, len(h), 4))
            cells.append((float(tm.group(1)), float(tm.group(2)), text))
        pages.append(cells)
    return pages


def table_rows(cells: list[tuple[float, float, str]], tol: float = 3.0) -> list[tuple[str, float, list[str]]]:
    """Group runs into rows by y (within `tol`): (label, x of the label's first glyph, [6 column texts])."""
    rows: list[list[tuple[float, float, str]]] = []
    for c in sorted(cells, key=lambda c: c[1]):
        if rows and abs(c[1] - rows[-1][0][1]) <= tol:
            rows[-1].append(c)
        else:
            rows.append([c])
    out = []
    for row in rows:
        row.sort()
        label_runs = [(x, t) for x, _, t in row if x < COL0]
        label = re.sub(r"\s+", "", "".join(t for _, t in label_runs).replace("　", ""))
        x0 = next((x for x, t in label_runs if t.strip()), None)
        cols = [""] * NCOLS
        for x, _, t in row:
            if x >= COL0:
                k = int((x - COL0) // COL_W)
                if 0 <= k < NCOLS:
                    cols[k] += t
        out.append((label, x0 if x0 is not None else 0.0, [re.sub(r"\s+", "", c) for c in cols]))
    return out


def _wan(text: str) -> int | None:
    """'143192' (万) → 1431920000; blank / '…' / '–' → None."""
    t = text.replace(",", "")
    if not re.fullmatch(r"-?\d+(\.\d+)?", t):
        return None
    return int(round(float(t) * 1e4))


def parse_ports(rows: list[tuple[str, float, list[str]]]) -> list[dict]:
    """Table rows → port records (name label, kind, cargo_t, foreign_t, teu, province_label) in table order."""
    ports, kind, province, pending = [], None, None, None

    def flush_municipality():
        nonlocal pending
        if pending is not None:  # a province row with no ports under it
            label, cols = pending
            if label in MUNICIPALITY_PORTS:
                ports.append(_record(label, kind_of_pending, cols, label))
            pending = None

    kind_of_pending = None
    for label, x0, cols in rows:
        if not label:
            continue
        if label.startswith("注"):
            break
        if label.endswith("沿海合计"):
            flush_municipality()
            kind = "coastal"
            continue
        if label.endswith("内河合计"):
            flush_municipality()
            kind = "inland"
            continue
        if kind is None:
            continue
        if label.startswith("其中："):
            pending = None
            ports.append(_record(label[3:], kind, cols, province))
        elif x0 > LABEL_INDENT_X:
            ports.append(_record(label, kind, cols, province))
        else:
            flush_municipality()
            province, pending, kind_of_pending = label, (label, cols), kind
    flush_municipality()
    return ports


def _record(label: str, kind: str, cols: list[str], province: str | None) -> dict:
    return {"label": label, "kind": kind, "cargo_t": _wan(cols[0]), "foreign_t": _wan(cols[2]),
            "teu": _wan(cols[4]), "province_label": province}


def port_name(label: str, split: bool = False) -> str:
    """Display name: label + 港 (连云港 → 连云港港, 贵港 → 贵港港, as officially named; 北部湾港 is already a
    port name). `split`: the label also has a coastal row (上海), so this inland row gets "（内河）"."""
    m = re.fullmatch(r"(.+?)\((.+)\)", label)
    if m:
        return f"{m.group(1)}港（{m.group(2)}）"
    if split:
        return f"{label}港（内河）"
    return label if label == "北部湾港" else label + "港"


# --- source -------------------------------------------------------------------


class CnTransportPorts(Source):
    info = SourceInfo(
        id="cn_transport_ports",
        title="全国主要港口货物、集装箱吞吐量（年度）",
        publisher="中华人民共和国交通运输部（综合规划司）",
        homepage="https://www.mot.gov.cn/shuju/tongjishuju/gangkoutuntuliang/",
        license="交通运输部政府信息公开（注明出处使用）",
        description="交通运输部发布的年度《港口货物、集装箱吞吐量》表（自 2024 年起为《全国港口与航道布局规划》所列"
        "全国主要港口，沿海与内河共 65 个）：各港口全年货物吞吐量、外贸货物吞吐量（万吨，换算为吨）和集装箱吞吐量"
        "（万标准箱，换算为标准箱）。原表为 PDF，逐格读取；空白或“…”（不足最小单位）记为空值。上海港沿海与内河、"
        "南通港沿海与沿江分别列示。原表不给位置：港口标在其主要港区所在区县（或所在地级行政区）的政府驻地"
        "（阿里云 DataV 行政区划），仅为近似位置。不含香港、澳门、台湾地区港口（空值，非 0）。",
        used_for=["地图上的港口（按货物吞吐量排序）", "城市详情中的港口", "交通设施统计"],
    )

    def _annual(self, force: bool) -> tuple[int, str]:
        found = []
        for page in INDEX_PAGES:
            p = self.download(MOT_INDEX + page, f"mot_{page}", force=force)
            found += [(int(y), url) for url, y in ANNUAL_RE.findall(p.read_text(encoding="utf-8", errors="replace"))]
            if found:
                break
        if not found:
            raise ValueError("MOT index: no 'YYYY年港口货物、集装箱吞吐量' annual issue found")
        return max(found)

    def fetch(self, force=False):
        year, article = self._annual(force)
        page = self.download(article, f"mot_{year}_article.html", force=force)
        m = PDF_RE.search(page.read_text(encoding="utf-8", errors="replace"))
        if not m:
            raise ValueError(f"{article}: no PDF attachment")
        self.download(urllib.parse.urljoin(article, m.group(1)), f"mot_{year}_ports.pdf", force=force,
                      expect_binary=True)

    def process(self):
        files = sorted(self.raw_dir.glob("mot_*_ports.pdf"))
        if not files:
            raise FileNotFoundError("mot_<year>_ports.pdf not fetched")
        pdf = files[-1]
        year = int(re.search(r"mot_(\d{4})_", pdf.name).group(1))
        rows = [r for page in read_pdf_cells(pdf.read_bytes()) for r in table_rows(page)]
        recs = parse_ports(rows)
        if len(recs) < 40:
            raise ValueError(f"{pdf.name}: only {len(recs)} ports parsed")

        path = INTERIM / "cn_boundaries.json"
        bnd = json.loads(path.read_text())["data"] if path.exists() else None
        if bnd is None:
            print("    ! cn_boundaries interim missing: positions/province/prefecture left null")
        pref_by_short = {}
        units = {}
        if bnd:
            for p in bnd["prefectures"]:
                pref_by_short.setdefault(p["short"], p["adcode"])
                units[p["adcode"]] = p
            for c in bnd["counties"]:
                units.setdefault(c["adcode"], c)

        coastal_labels = {r["label"] for r in recs if r["kind"] == "coastal"}
        ports = []
        for r in recs:
            label = r["label"]
            if label not in PINYIN:
                raise ValueError(f"port {label!r} has no pinyin id; add it to PINYIN")
            pref = PORT_PREFECTURE.get(label) or pref_by_short.get(label)
            split = r["kind"] == "inland" and label in coastal_labels  # 上海 inland: a second record
            county = None if split else PORT_COUNTY.get(label)
            if bnd and (pref not in units or (county and county not in units)):
                raise ValueError(f"port {label!r}: adcode {pref}/{county} not in cn_boundaries")
            at = units.get(county) if county else units.get(pref)
            seat = (at or {}).get("seat")
            slug = PINYIN[label] + ("-inland" if split else "")
            ports.append({
                "id": slug,
                "name": port_name(label, split),
                "label": label,
                "kind": r["kind"],
                "cargo_t": r["cargo_t"],
                "foreign_t": r["foreign_t"],
                "teu": r["teu"],
                "rank": None,
                "lon": seat[0] if seat else None,
                "lat": seat[1] if seat else None,
                "coord_src": (("datav_county_seat" if county else "datav_prefecture_seat") if seat else None),
                "province": (units.get(pref) or {}).get("province") if bnd else None,
                "prefecture": pref if bnd else None,
                "county": county,
            })
        ports.sort(key=lambda p: -(p["cargo_t"] if p["cargo_t"] is not None else -1))
        for i, p in enumerate(ports, 1):
            p["rank"] = i if p["cargo_t"] is not None else None
        if len({p["id"] for p in ports}) != len(ports):
            raise ValueError("duplicate port ids")
        return {
            "vintage": f"交通运输部《{year}年港口货物、集装箱吞吐量》（全国主要港口）",
            "_files": [pdf.name],
            "year": year,
            "units": {"cargo_t": "吨", "foreign_t": "吨", "teu": "标准箱（TEU）"},
            "ports": ports,
            "extra": [],
            "extra_note": "香港、澳门、台湾地区港口不在交通运输部统计范围内；其官方数据暂未纳入（空值，非 0）。",
        }
