"""Natural Earth: neighbouring countries (China's point of view), lakes, rivers, and fallback roads/railroads.

China's own outline never comes from here: it is cn_boundaries (DataV, standard map). Natural Earth gives the
neighbours only, from the 1:10m "point of view: China" countries file (ne_10m_admin_0_countries_chn), so India,
Bhutan, Japan and the others are drawn without China's claimed areas. China, Taiwan, Hong Kong and Macau are
dropped from it, and the `world` builder lets cn_boundaries win wherever the two overlap.
"""

from ..core.geo import bbox_intersects, geojson_lines, geojson_rings, load_geojson
from ..core.source import Source, SourceInfo

# Pinned to a release tag so the content (and sha256) cannot change under the same URL.
TAG = "v5.1.2"
BASE = f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/{TAG}/geojson"
# lon/lat window: the main map rectangle (core/geo.py, lon 64.3–143.5°E, lat 13.0–56.6°N) plus the 南海诸岛 inset
# region (106.5–123.5°E, 2.8–24.8°N), so neighbours' coasts appear in the inset too.
BBOX = (60.0, 0.0, 150.0, 60.0)
MAIN_BBOX = (63.0, 12.0, 145.0, 57.0)  # rivers and roads: main map only
# Never foreign land. SCR = Scarborough Reef (黄岩岛), which the Natural Earth file carries as its own feature.
CHINA_A3 = {"CHN", "TWN", "HKG", "MAC", "SCR"}


# Chinese names for the rivers in China (Natural Earth's river file has English names only). Names this project
# could not identify with confidence are left out (name_zh null): never guessed. "Min" is two rivers: see _river_zh.
RIVER_ZH = {
    "Chang Jiang": "长江", "Yangtze": "长江", "Jinsha": "金沙江", "Tongtian": "通天河", "Tuotuo": "沱沱河",
    "Huang": "黄河", "Heilong Jiang": "黑龙江", "Amur": "黑龙江", "Argun’": "额尔古纳河", "Hailar": "海拉尔河",
    "Songhua": "松花江", "Di’er Songhua": "第二松花江", "Nen": "嫩江", "Ussuri": "乌苏里江", "Tumen": "图们江",
    "Yalu": "鸭绿江", "Liao": "辽河", "Xiliao": "西辽河", "Xar Moron": "西拉木伦河", "Daling": "大凌河",
    "Luan": "滦河", "Hai": "海河", "Yongding": "永定河", "Sanggan": "桑干河", "Huai": "淮河", "Ying": "颍河",
    "Wei": "渭河", "Jing": "泾河", "Fen": "汾河", "Qin": "沁河", "Wuding": "无定河", "Han": "汉江", "Dan": "丹江",
    "Jialing": "嘉陵江", "Wu": "乌江", "Tuo": "沱江", "Dadu": "大渡河", "Yalong": "雅砻江", "Xiang": "湘江",
    "Zi": "资水", "Yuan": "沅江", "Gan": "赣江", "Fuchun": "富春江", "Jiulong": "九龙江", "Xi": "西江",
    "Xun": "浔江", "Hongshui": "红水河", "Nanpan": "南盘江", "Yu": "郁江", "Yong": "邕江", "You": "右江",
    "Zuo": "左江", "Liu": "柳江", "Rong": "融江", "Bei": "北江", "Dong": "东江", "Mei": "梅江", "Hong": "元江",
    "Lancang": "澜沧江", "Za": "扎曲", "Nu": "怒江", "Yarlung": "雅鲁藏布江", "Dihang": "雅鲁藏布江",
    "Maquan": "马泉河", "Shiquan": "狮泉河", "Sutlej": "象泉河", "Zayü": "察隅河", "Ertix": "额尔齐斯河",
    "Ile": "伊犁河", "Künes": "巩乃斯河", "Tarim": "塔里木河", "Yarkant": "叶尔羌河", "Yurungkax": "玉龙喀什河",
    "Aksu": "阿克苏河", "Toxkan": "托什干河", "Konqi": "孔雀河", "Qarqan": "车尔臣河", "Qumar": "楚玛尔河",
    "Buh": "布哈河", "Tao’er": "洮儿河", "Chaor": "绰尔河", "Nemor": "讷谟尔河", "Muling": "穆棱河",
    "Herlen": "克鲁伦河", "Wujia": "乌加河", "Yi": "沂河", "Zhang": "漳河", "Shandian": "闪电河",
}
LAKE_ZH = {"Hulun Nuur": "呼伦湖", "Uvs Nuur": "乌布苏湖"}  # Natural Earth's name_zh is traditional for these


def _river_zh(name: str | None, line) -> str | None:
    if name == "Min":  # 岷江 (Sichuan) or 闽江 (Fujian)
        return "岷江" if line and line[0][0] < 110 else "闽江"
    return RIVER_ZH.get(name or "")


def _zh(p: dict) -> str | None:
    v = p.get("NAME_ZH") or p.get("name_zh")
    return v.strip() if isinstance(v, str) and v.strip() else None


class NaturalEarth(Source):
    info = SourceInfo(
        id="natural_earth",
        title="Natural Earth 1:10m（邻国〔中国视角版〕、湖泊、河流）",
        publisher="Natural Earth",
        homepage="https://www.naturalearthdata.com/",
        license="公有领域",
        description="邻国陆地（取自“中国视角”版国界文件 ne_10m_admin_0_countries_chn，地图上以雾化显示），大型湖泊与主要河流"
        f"（含中文名）。中国国界与省界不取自 Natural Earth，而取自 DataV（见“中国行政区划边界”）。文件固定为 natural-earth-vector {TAG} 版本。",
        used_for=["邻国陆地（雾化显示）", "湖泊", "河流", "南海诸岛插图中的周边海岸"],
    )

    FILES = ["ne_10m_admin_0_countries_chn", "ne_10m_lakes", "ne_10m_rivers_lake_centerlines"]

    def _path(self, name):
        return f"{TAG}/{name}.geojson"

    def fetch(self, force=False):
        for name in self.FILES:
            self.download(f"{BASE}/{name}.geojson", self._path(name), force=force)

    def _features(self, name):
        return load_geojson(self.raw_dir / self._path(name))

    def process(self):
        # Every foreign country with land in the window, keeping only its rings there.
        countries = []
        for f in self._features("ne_10m_admin_0_countries_chn"):
            p = f["properties"]
            a3 = p.get("ADM0_A3") or p.get("SOV_A3")
            if a3 in CHINA_A3 or p.get("SOV_A3") == "CHN":
                continue
            rings = [r for r in geojson_rings(f["geometry"]) if bbox_intersects(r, BBOX)]
            if rings:
                countries.append({"name": p.get("ADMIN") or p.get("NAME"), "name_zh": _zh(p), "a3": a3, "rings": rings})
        lakes = []
        for f in self._features("ne_10m_lakes"):
            p = f["properties"]
            rings = geojson_rings(f["geometry"])
            if rings and bbox_intersects(rings[0], MAIN_BBOX):
                lakes.append({"name": p.get("name"), "name_zh": LAKE_ZH.get(p.get("name")) or _zh(p),
                              "rank": p.get("scalerank"), "rings": rings})
        rivers = []
        for f in self._features("ne_10m_rivers_lake_centerlines"):
            p = f["properties"]
            for line in geojson_lines(f["geometry"]):
                if bbox_intersects(line, MAIN_BBOX):
                    rivers.append({"name": p.get("name"), "name_zh": _river_zh(p.get("name"), line),
                                   "rank": p.get("scalerank", 10),
                                   "featurecla": p.get("featurecla"), "line": line})
        print(f"    {len(countries)} neighbouring countries, {len(lakes)} lakes, {len(rivers)} river lines in the window")
        return {
            "vintage": f"Natural Earth {TAG} (1:10m; countries: China point of view)",
            "_files": [self._path(n) for n in self.FILES],
            "countries": countries,
            "lakes": lakes,
            "rivers": rivers,
        }
