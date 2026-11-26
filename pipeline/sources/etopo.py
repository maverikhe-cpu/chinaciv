import csv

from ..core.source import Source, SourceInfo

# ETOPO1 (1 arc-minute) via NOAA ERDDAP, sampled every 5th point (~9 km). The window covers the whole main-map
# rectangle (core/geo.py: lon 64.3–143.5°E, lat 13.0–56.6°N at its corners and edges) with a small pad. The
# 南海诸岛 inset is drawn as vector lines only, so it needs no elevation.
STRIDE = 5
LAT, LON = (12, 57), (63, 145)
URL = (
    "https://coastwatch.pfeg.noaa.gov/erddap/griddap/etopo180.csv"
    f"?altitude%5B({LAT[0]}):{STRIDE}:({LAT[1]})%5D%5B({LON[0]}):{STRIDE}:({LON[1]})%5D"
)
FILE = f"etopo_lat{LAT[0]}_{LAT[1]}_lon{LON[0]}_{LON[1]}_stride{STRIDE}.csv"


class Etopo(Source):
    info = SourceInfo(
        id="etopo",
        title="ETOPO1 全球地形起伏模型",
        publisher="美国国家海洋和大气管理局国家环境信息中心（NOAA NCEI，经 ERDDAP 提供）",
        homepage="https://www.ncei.noaa.gov/products/etopo-global-relief-model",
        license="公有领域（美国政府作品）",
        description="陆地高程与海洋水深（ETOPO1，1 角分；经 ERDDAP 每 5 点取 1，约 9 公里一个采样点）。"
        "每个六边形的地形（平原、丘陵、山地、高山）由其内部陆地采样点的平均高程与起伏（标准差）决定。",
        used_for=["地形（丘陵、山地）", "山体晕渲", "海洋水深"],
    )

    def fetch(self, force=False):
        self.download(URL, FILE, force=force)

    def process(self):
        lat, lon, alt = [], [], []
        with open(self.raw_dir / FILE, newline="") as f:
            rows = csv.reader(f)
            next(rows), next(rows)  # header + units
            for la, lo, al in rows:
                if al == "NaN":
                    continue
                lat.append(round(float(la), 4))
                lon.append(round(float(lo), 4))
                alt.append(int(float(al)))
        return {
            "vintage": "ETOPO1, 1 arc-minute, sampled every ~9 km",
            "_files": [FILE],
            "lat": lat,
            "lon": lon,
            "alt": alt,
        }
