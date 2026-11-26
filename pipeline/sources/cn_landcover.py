"""Land cover over China and its neighbours: MODIS MCD12Q1 (IGBP scheme), via NASA GIBS, on a 0.02° lon/lat grid.

Product: MODIS Terra+Aqua Combined Land Cover Type, yearly, 500 m (MCD12Q1 Collection 6.1, LC_Type1 = IGBP
classification), year YEAR. NASA GIBS serves it as the WMS layer ``MODIS_Combined_L3_IGBP_Land_Cover_Type_Annual``.
GIBS colours every class with one fixed RGB (colormap MODIS_IGBP_Land_Cover_Type.xml, v1.3, below) and resamples
with nearest neighbour, so each RGB in the returned PNG decodes back to exactly one IGBP class. The PNG (8-bit
RGBA, non-interlaced) is decoded with zlib + the PNG row filters, standard library only (``decode_png``).

Why this product: ESA WorldCover / Terrascope WCS timed out from here, NASA NEO (the plan's first suggestion)
now redirects to a web page, and GIBS answers reliably with exact class colours and no key. MCD12Q1 is the
standard global annual land cover at 500 m; we take it at 0.02° (≈ 2.2 km × 1.8–2.2 km), which gives about 400
samples per 25 km hex, like NLCD did in usaciv.

Grid (decided here; CN_PLAN §4.4 leaves the window open beyond the minimum frame):
  lon 63–145°E × lat 3–57°N, 0.02° pixels, 4100 columns × 2700 rows, pixel-centre registration
  (lon0 = 63.01, lat0 = 56.99, dlat = −0.02). The window goes south to 3°N, past the frame's 12°N, so the
  南海诸岛 inset region (down to 曾母暗沙, ~3.97°N) is covered too; most of that is sea (water).
  Fetched as 12 WMS tiles of 1025 × 900 px (TILES_LON × TILES_LAT), stitched in process().

Class codes and mapping onto our cover groups (``groups``; world.py maps each sample through this table):

  code  IGBP class (GIBS colour)                    中文            group
  0     no data / transparent / unclassified (255)  无数据          nodata
  1     Evergreen needleleaf forest   33,138,33     常绿针叶林      forest_evergreen
  2     Evergreen broadleaf forest    49,204,49     常绿阔叶林      forest_evergreen
  3     Deciduous needleleaf forest   152,204,49    落叶针叶林      forest_deciduous
  4     Deciduous broadleaf forest    150,250,150   落叶阔叶林      forest_deciduous
  5     Mixed forest                  141,186,141   混交林          forest_mixed
  6     Closed shrubland              186,141,141   郁闭灌丛        scrub
  7     Open shrubland                245,222,179   稀疏灌丛        scrub
  8     Woody savanna (trees 30–60%)  218,235,157   多树草原        forest_mixed
  9     Savanna (trees 10–30%)        255,213,0     稀树草原        grassland
  10    Grassland                     240,185,103   草地            grassland
  11    Permanent wetland             71,131,181    永久湿地        wetland
  12    Cropland                      250,239,115   耕地            cropland
  13    Urban and built-up            255,0,0       城市建设用地    developed
  14    Cropland/natural veg. mosaic  153,147,86    耕地与自然植被镶嵌  pasture
  15    Permanent snow and ice        255,255,255   永久冰雪        ice
  16    Barren                        191,191,189   裸地（荒漠、戈壁、裸岩）  barren
  17    Water (IGBP 0 and 17; GIBS gives both one colour)  134,202,227  水体  water

Choices: woody savanna is a 30–60 % tree canopy class (mostly the hill forests of south China and Yunnan, which
MODIS rarely calls closed forest), so it counts as forest (mixed); savanna (10–30 % trees) counts as grassland.
The cropland/natural-vegetation mosaic (small fields among woods, common in the southern hills) becomes
"pasture", the second farmland group, so it stays distinct from pure cropland. MODIS urban pixels are sparse at
2 km; world.py may lower its urban threshold for this raster (CN_PLAN §5.1).
"""

from __future__ import annotations

import struct
import zlib

from ..core.source import Source, SourceInfo

YEAR = 2024
LAYER = "MODIS_Combined_L3_IGBP_Land_Cover_Type_Annual"
WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
COLORMAP_URL = "https://gibs.earthdata.nasa.gov/colormaps/v1.3/MODIS_IGBP_Land_Cover_Type.xml"

RES = 0.02  # degrees per pixel
LON_W, LON_E = 63.0, 145.0
LAT_S, LAT_N = 3.0, 57.0
TILES_LON, TILES_LAT = 4, 3  # 82° / 4 = 20.5° = 1025 px; 54° / 3 = 18° = 900 px

# GIBS colormap v1.3 (MODIS_IGBP_Land_Cover_Type.xml): RGB → our class code. IGBP 0 and 17 (water) share a colour.
COLORS = {
    (33, 138, 33): 1, (49, 204, 49): 2, (152, 204, 49): 3, (150, 250, 150): 4, (141, 186, 141): 5,
    (186, 141, 141): 6, (245, 222, 179): 7, (218, 235, 157): 8, (255, 213, 0): 9, (240, 185, 103): 10,
    (71, 131, 181): 11, (250, 239, 115): 12, (255, 0, 0): 13, (153, 147, 86): 14, (255, 255, 255): 15,
    (191, 191, 189): 16, (134, 202, 227): 17, (100, 100, 100): 0,  # 255 "unclassified" → no data
}
LEGEND = {0: ".", **{c: chr(ord("a") + c - 1) for c in range(1, 17)}, 17: "w"}
CLASSES = {
    0: "无数据", 1: "常绿针叶林", 2: "常绿阔叶林", 3: "落叶针叶林", 4: "落叶阔叶林", 5: "混交林", 6: "郁闭灌丛",
    7: "稀疏灌丛", 8: "多树草原", 9: "稀树草原", 10: "草地", 11: "永久湿地", 12: "耕地", 13: "城市建设用地",
    14: "耕地与自然植被镶嵌", 15: "永久冰雪", 16: "裸地", 17: "水体",
}
GROUPS = {
    0: "nodata", 1: "forest_evergreen", 2: "forest_evergreen", 3: "forest_deciduous", 4: "forest_deciduous",
    5: "forest_mixed", 6: "scrub", 7: "scrub", 8: "forest_mixed", 9: "grassland", 10: "grassland", 11: "wetland",
    12: "cropland", 13: "developed", 14: "pasture", 15: "ice", 16: "barren", 17: "water",
}
ALLOWED_GROUPS = {"forest_evergreen", "forest_deciduous", "forest_mixed", "scrub", "grassland", "pasture",
                  "cropland", "wetland", "developed", "barren", "ice", "water", "nodata"}


def tiles() -> list[tuple[int, int, float, float, float, float, int, int]]:
    """(row, col, lat_s, lon_w, lat_n, lon_e, width_px, height_px) for every WMS tile, north row first."""
    dlon = (LON_E - LON_W) / TILES_LON
    dlat = (LAT_N - LAT_S) / TILES_LAT
    out = []
    for r in range(TILES_LAT):
        n = LAT_N - r * dlat
        for c in range(TILES_LON):
            w = LON_W + c * dlon
            out.append((r, c, round(n - dlat, 6), round(w, 6), round(n, 6), round(w + dlon, 6),
                        round(dlon / RES), round(dlat / RES)))
    return out


def tile_url(lat_s: float, lon_w: float, lat_n: float, lon_e: float, width: int, height: int) -> str:
    # WMS 1.3.0 with EPSG:4326 takes the bbox as lat_min,lon_min,lat_max,lon_max.
    return (f"{WMS}?SERVICE=WMS&REQUEST=GetMap&VERSION=1.3.0&LAYERS={LAYER}&STYLES=&CRS=EPSG:4326"
            f"&BBOX={lat_s:g},{lon_w:g},{lat_n:g},{lon_e:g}&WIDTH={width}&HEIGHT={height}"
            f"&FORMAT=image/png&TRANSPARENT=TRUE&TIME={YEAR}-01-01")


def tile_file(r: int, c: int) -> str:
    return f"mcd12q1_igbp_{YEAR}_{RES:g}deg_r{r}c{c}.png"


def _unfilter(raw: bytes, width: int, height: int, bpp: int) -> bytearray:
    """Undo PNG per-row filters (types 0–4). Returns width*height*bpp bytes."""
    stride = width * bpp
    out = bytearray(stride * height)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ftype = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if ftype == 1:  # Sub
            for x in range(bpp, stride):
                line[x] = (line[x] + line[x - bpp]) & 255
        elif ftype == 2:  # Up
            line = bytearray((a + b) & 255 for a, b in zip(line, prev))
        elif ftype == 3:  # Average
            for x in range(stride):
                left = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + ((left + prev[x]) >> 1)) & 255
        elif ftype == 4:  # Paeth
            for x in range(stride):
                a = line[x - bpp] if x >= bpp else 0
                b = prev[x]
                c = prev[x - bpp] if x >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        elif ftype != 0:
            raise ValueError(f"bad PNG filter type {ftype} in row {y}")
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return out


def decode_png(data: bytes) -> tuple[int, int, list[tuple[int, int, int, int]]]:
    """Decode an 8-bit, non-interlaced PNG (RGB, RGBA or palette) into (width, height, RGBA pixels row-major)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    pos, idat, palette, trns = 8, [], None, b""
    width = height = depth = ctype = interlace = None
    while pos < len(data):
        (n,) = struct.unpack(">I", data[pos:pos + 4])
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if width is None:
        raise ValueError("PNG without IHDR")
    if depth != 8 or interlace != 0 or ctype not in (2, 3, 6):
        raise ValueError(f"unsupported PNG (bit depth {depth}, colour type {ctype}, interlace {interlace})")
    bpp = {2: 3, 3: 1, 6: 4}[ctype]
    px = _unfilter(zlib.decompress(b"".join(idat)), width, height, bpp)
    if ctype == 6:
        pixels = list(zip(px[0::4], px[1::4], px[2::4], px[3::4]))
    elif ctype == 2:
        pixels = [(r, g, b, 255) for r, g, b in zip(px[0::3], px[1::3], px[2::3])]
    else:
        if palette is None:
            raise ValueError("palette PNG without PLTE")
        lut = [(*rgb, trns[i] if i < len(trns) else 255) for i, rgb in enumerate(palette)]
        pixels = [lut[i] for i in px]
    return width, height, pixels


def classify(pixels, unknown: dict | None = None) -> list[int]:
    """RGBA pixels → class codes. Transparent pixels are no data (0); unknown colours are counted in `unknown`."""
    out = []
    for r, g, b, a in pixels:
        if a == 0:
            out.append(0)
            continue
        code = COLORS.get((r, g, b))
        if code is None:
            if unknown is not None:
                unknown[(r, g, b)] = unknown.get((r, g, b), 0) + 1
            code = 0
        out.append(code)
    return out


def encode_rows(codes: list[int], width: int) -> list[str]:
    chars = [LEGEND[c] for c in codes]
    return ["".join(chars[i:i + width]) for i in range(0, len(chars), width)]


class CnLandcover(Source):
    info = SourceInfo(
        id="cn_landcover",
        title=f"MODIS 土地覆盖类型（MCD12Q1，IGBP 分类，{YEAR} 年）",
        publisher="美国国家航空航天局（NASA）LP DAAC / MODIS 陆地科学组；经 NASA GIBS 地图服务提供",
        homepage="https://lpdaac.usgs.gov/products/mcd12q1v061/",
        license="NASA 地球观测数据，无使用限制（公开数据政策）。Friedl, M., Sulla-Menashe, D., 2022, "
        "MCD12Q1 MODIS/Terra+Aqua Land Cover Type Yearly L3 Global 500m SIN Grid V061, doi:10.5067/MODIS/MCD12Q1.061",
        description=f"MODIS 年度土地覆盖产品（500 米，IGBP 17 类，{YEAR} 年），由 NASA GIBS 的 WMS 图层 {LAYER} "
        f"以最近邻重采样为 {RES}° 经纬网格（约 2 公里）的 PNG 图块（经度 63–145°E、纬度 3–57°N，覆盖全国含台湾、"
        "海南及南海诸岛插图范围），按 GIBS 固定色表还原为类别。每个六边形内约 400 个采样点决定其地表"
        "（森林、灌丛与荒漠、草地、耕地、湿地、城市、裸地、冰雪）及各类占比。类别映射：常绿针叶林/常绿阔叶林→常绿林；"
        "落叶针叶林/落叶阔叶林→落叶林；混交林、多树草原→混交林；郁闭/稀疏灌丛→灌丛与荒漠；稀树草原、草地→草地；"
        "耕地→耕地；耕地与自然植被镶嵌→牧场与杂作；永久湿地→湿地；城市建设用地→城市；裸地→裸地；永久冰雪→冰雪。",
        used_for=["六边形地表类型（生物群系）", "森林、耕地、城市用地占比"],
    )

    def fetch(self, force=False):
        for r, c, s, w, n, e, wpx, hpx in tiles():
            self.download(tile_url(s, w, n, e, wpx, hpx), tile_file(r, c), force=force, expect_binary=True)

    def process(self):
        width = round((LON_E - LON_W) / RES)
        height = round((LAT_N - LAT_S) / RES)
        grid = [None] * height  # list of per-row code lists
        unknown: dict = {}
        files = []
        for r, c, s, w, n, e, wpx, hpx in tiles():
            name = tile_file(r, c)
            files.append(name)
            tw, th, pixels = decode_png((self.raw_dir / name).read_bytes())
            if (tw, th) != (wpx, hpx):
                raise ValueError(f"{name}: got {tw}×{th} px, expected {wpx}×{hpx}")
            codes = classify(pixels, unknown)
            y0 = round((LAT_N - n) / RES)
            x0 = round((w - LON_W) / RES)
            for y in range(th):
                row = grid[y0 + y]
                if row is None:
                    row = grid[y0 + y] = [0] * width
                row[x0:x0 + tw] = codes[y * tw:(y + 1) * tw]
        if any(row is None for row in grid):
            raise ValueError("land-cover tiles do not cover the whole grid")
        if unknown:
            top = sorted(unknown.items(), key=lambda kv: -kv[1])[:10]
            print(f"    warning: {sum(unknown.values())} pixels with colours outside the GIBS colormap → no data: {top}")
        rows = ["".join(LEGEND[v] for v in row) for row in grid]
        assert set(GROUPS.values()) <= ALLOWED_GROUPS
        return {
            "vintage": f"MODIS MCD12Q1 v061 IGBP {YEAR} (500 m), via NASA GIBS WMS, resampled to {RES}°",
            "_files": files,
            "scheme": "igbp",
            "crs": "EPSG:4326",
            # Centre of pixel (col 0, row 0) and pixel size in degrees; rows go south (dlat negative).
            "lon0": round(LON_W + RES / 2, 6), "lat0": round(LAT_N - RES / 2, 6),
            "dlon": RES, "dlat": -RES,
            "cols": width, "rows_n": height,
            "legend": {ch: code for code, ch in LEGEND.items()},
            "classes": {str(k): v for k, v in CLASSES.items()},
            "groups": {str(k): v for k, v in GROUPS.items()},
            "rows": rows,
        }
