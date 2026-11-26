"""Power plants, coal and iron-ore mines, major metal mines and oil & gas fields in China (cn_energy).

Publishers (all open, no key):

* **Global Energy Monitor (GEM)**, CC BY 4.0. GEM publishes the data behind its public tracker maps as plain
  files on its CDN; each map's ``config.js`` (globalenergymonitor.github.io/interim-maps/trackers/<map>/) names the
  current file, so fetch reads the config first and falls back to the pinned file below.
    - Global Integrated Power Tracker (GIPT) map CSV: every power unit ≥ the trackers' size floors (coal, oil/gas,
      nuclear, hydro incl. pumped storage, wind, utility-scale solar, bioenergy, geothermal), one row per unit.
      → ``plants``.
    - Global Coal Plant Tracker (GCPT) and Global Nuclear Power Tracker (GNPT) map files: used **only** for the
      Chinese plant names (``name-noneng``), joined to GIPT by GEM project id.
    - Global Coal Mine Tracker (GCMT) → coal ``mines``; Global Iron Ore Mines Tracker (GIOMT) → iron ``mines``.
    - Global Oil and Gas Extraction Tracker (GOGET) → ``fields``.
* **Wikidata** (CC0): coordinates of a short curated list of major non-ferrous / rare-earth / lithium mines
  (``METAL_MINES``: QID + Chinese name + commodity). No open dataset with current per-mine capacity exists for these,
  so ``capacity`` is null. The list is a documented editorial choice: well-known flagship deposits of each commodity
  whose Wikidata item has plausible coordinates (Jinchuan nickel was dropped: its Wikidata point is ~300 km off).

Not used: WRI Global Power Plant Database v1.3.0 (2021; superseded for China by GIPT 2026, mixing both would double
count), USGS minfac (2007, city-centroid coordinates).

Decisions (CN_PLAN §4.10 leaves these open):

* Areas: GEM ``country-area1`` China, Taiwan, Hong Kong, Macao (Taiwan, HK, Macau are part of the map; GEM files
  them separately). Rows tagged with any other country are dropped (so e.g. a binational dam filed under a neighbour
  is not included).
* Only **operating** units / mines / fields are kept (status "operating"); construction, mothballed, retired etc.
  are left out. Battery storage is not tracked by GEM (no battery plants; never written as 0).
* Plants = GIPT units summed per (GEM project id, fuel). ``capacity_mw`` = sum of operating units (MW, GEM
  nameplate); ``year`` = earliest start year of those units; ``year_last`` = latest; ``units`` = count; position =
  the largest unit's coordinates; ``owner`` = largest unit's owner with GEM's share brackets removed.
* Fuel mapping: coal → coal; oil/gas → ``gas`` when GEM's fuel list names fossil gas / LNG / coalbed methane or is
  empty (GEM's oil & gas plant tracker is gas-dominated), ``oil`` when only fossil liquids, ``other`` when only
  industrial by-product gases (blast-furnace / coke-oven gas); hydropower → ``pumped_storage`` for tech
  "pumped storage", else ``hydro``; wind → wind (``offshore`` true for GEM offshore techs); utility-scale solar →
  solar (``tech`` "solar_thermal" for CSP); bioenergy → biomass; geothermal → geothermal.
* Names: ``name_en`` = GEM English name; ``name`` = Chinese name from GCPT/GNPT ``name-noneng`` (coal, nuclear),
  from ``HYDRO_ZH`` for the largest hydro / pumped-storage plants, else null (the builder falls back to name_en).
* Mines: ``capacity`` in t/yr (GCMT Mtpa × 1e6; GIOMT thousand t/yr × 1e3); GCMT also gives ``production``
  (t/yr, × 1e6) with ``production_year``. ``name`` = GEM Chinese name where given (GIOMT's "(Chinese)" tag removed).
* Fields: ``production`` = GEM's combined figure in **barrels of oil equivalent per year** (``production_unit``
  "boe/yr", GEM million boe/y × 1e6), comparable across oil and gas; ``oil_bbl``/``oil_year`` (barrels/yr) and
  ``gas_m3``/``gas_year`` (m³/yr) as GEM reports them. ``year`` = the later of the two. ``kind`` from which products
  GEM reports (else from the field name). Chinese ``name`` built from the English name with ``FIELD_PLACE_ZH``
  (place) + suffix (油田/气田/…), null when the place is not in the table; ``basin`` from the operator / offshore block.

Coordinates are WGS-84 as published (not converted; see CN_PLAN global rules).
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict

from ..core.source import Source, SourceInfo

GEM_MAPS = "https://globalenergymonitor.github.io/interim-maps/trackers"
GEM_CDN = "https://publicgemdata.nyc3.cdn.digitaloceanspaces.com"
# map id → (pinned data file used when config.js cannot be read, kind)
TRACKERS = {
    "integrated-power": f"{GEM_CDN}/Current_maps/integrated-power/2026-09/integrated_map_2026-09.csv",
    "gcpt": f"{GEM_CDN}/interim_maps/gcpt_map_2026-08.geojson",
    "gnpt": f"{GEM_CDN}/Current_maps/gnpt/gnpt_map_2026-08.geojson",
    "gcmt": f"{GEM_CDN}/Current_maps/gcmt-smp/gcmt-smp_map_2026-08.geojson",
    "giomt": f"{GEM_CDN}/Current_maps/giomt/giomt_map_2026-09.geojson",
    "goget": f"{GEM_CDN}/interim_maps/goget_map_2026-03.geojson",
}
TRACKER_NAMES = {
    "integrated-power": "Global Integrated Power Tracker", "gcpt": "Global Coal Plant Tracker",
    "gnpt": "Global Nuclear Power Tracker", "gcmt": "Global Coal Mine Tracker",
    "giomt": "Global Iron Ore Mines Tracker", "goget": "Global Oil and Gas Extraction Tracker",
}
WIKIDATA = "https://www.wikidata.org/w/api.php?action=wbgetentities&format=json&props=labels%7Cclaims&languages=zh%7Cen&ids="

AREAS = {"China": None, "Taiwan": "71", "Hong Kong": "81", "Macao": "82", "Macau": "82"}

# Curated major metal / rare-earth / lithium mines: (QID, Chinese name, commodity). Coordinates from Wikidata P625.
METAL_MINES = [
    ("Q812094", "白云鄂博矿", "rare_earths"),        # 稀土 + 铁，世界最大稀土矿
    ("Q24377198", "牦牛坪稀土矿", "rare_earths"),
    ("Q11071515", "德兴铜矿", "copper"),
    ("Q13484154", "永平铜矿", "copper"),
    ("Q16509542", "甲玛铜多金属矿", "copper"),
    ("Q137860328", "多宝山铜矿", "copper"),
    ("Q137860272", "拉拉铜矿", "copper"),
    ("Q107490065", "铜绿山铜铁矿", "copper"),
    ("Q15229586", "金峰金矿", "gold"),
    ("Q8044426", "锡矿山锑矿", "antimony"),
    ("Q12172652", "柿竹园多金属矿", "tungsten"),
    ("Q137860496", "荡坪钨矿", "tungsten"),
    ("Q16968831", "甲基卡锂矿", "lithium"),
    ("Q1058308", "扎布耶盐湖", "lithium"),
    ("Q137942359", "西台吉乃尔盐湖", "lithium"),
    ("Q137942361", "东台吉乃尔盐湖", "lithium"),
    ("Q137860409", "凡口铅锌矿", "lead_zinc"),
    ("Q137860446", "金顶铅锌矿", "lead_zinc"),
    ("Q24261879", "锡铁山铅锌矿", "lead_zinc"),
    ("Q137860759", "平果铝土矿", "bauxite"),
]

# Chinese names of the largest hydro / pumped-storage plants (GEM name without " hydroelectric plant").
HYDRO_ZH = {
    "Three Gorges Dam": "三峡水电站", "Baihetan": "白鹤滩水电站", "Xiluodu": "溪洛渡水电站", "Wudongde": "乌东德水电站",
    "Xiangjiaba": "向家坝水电站", "Nuozhadu": "糯扎渡水电站", "Longtan": "龙滩水电站", "Jinping Stage 2": "锦屏二级水电站",
    "Laxiwa": "拉西瓦水电站", "Xiaowan": "小湾水电站", "Pubugou": "瀑布沟水电站", "Fengning": "丰宁抽水蓄能电站",
    "Jinping Stage 1": "锦屏一级水电站", "Ertan": "二滩水电站", "Guanyinyan": "观音岩水电站", "Lianghekou": "两河口水电站",
    "Goupitan": "构皮滩水电站", "Gezhouba": "葛洲坝水电站", "Dagangshan": "大岗山水电站", "Changheba": "长河坝水电站",
    "Liyuan": "梨园水电站", "Yangjiang": "阳江抽水蓄能电站", "Jin'anqiao": "金安桥水电站", "Guangzhou": "广州抽水蓄能电站",
    "Guandi": "官地水电站", "Huizhou": "惠州抽水蓄能电站", "Maerdang": "玛尔挡水电站", "Ludila": "鲁地拉水电站",
    "Changlongshan": "长龙山抽水蓄能电站", "Ahai": "阿海水电站", "Lijiaxia": "李家峡水电站", "Huangdeng": "黄登水电站",
    "Shuibuya": "水布垭水电站", "Yantan": "岩滩水电站", "Xiaolangdi": "小浪底水电站", "Longkaikou": "龙开口水电站",
    "Jixi": "绩溪抽水蓄能电站", "Liaoning Qingyuan": "清原抽水蓄能电站", "Wendeng": "文登抽水蓄能电站",
    "Tianhuangping": "天荒坪抽水蓄能电站", "Jinyun": "缙云抽水蓄能电站", "Baishan": "白山水电站", "Jinghong": "景洪水电站",
    "Pengshui": "彭水水电站", "Houziyan": "猴子岩水电站", "Wuqiangxi": "五强溪水电站", "Manwan": "漫湾水电站",
    "Mingtan": "明潭抽水蓄能电站", "Datengxia": "大藤峡水利枢纽", "Jiangsu Liyang": "溧阳抽水蓄能电站",
    "Gongboxia": "公伯峡水电站", "Xianju": "仙居抽水蓄能电站", "Yangfanggou": "杨房沟水电站", "Fengman": "丰满水电站",
    "Zhouning": "周宁抽水蓄能电站", "Tuoba": "托巴水电站", "Xiamen": "厦门抽水蓄能电站", "Shuikou": "水口水电站",
    "Zhen’an": "镇安抽水蓄能电站", "Miaowei": "苗尾水电站", "Ninghai": "宁海抽水蓄能电站", "Dunhua": "敦化抽水蓄能电站",
    "Dachaoshan": "大朝山水电站", "Jurong": "句容抽水蓄能电站", "Tianshengqiao Stage 2": "天生桥二级水电站",
    "Longyangxia": "龙羊峡水电站", "Wujiangdu": "乌江渡水电站", "Qingyuan": "清远抽水蓄能电站", "Liujiaxia": "刘家峡水电站",
    "Bailianhe Pumped Storage": "白莲河抽水蓄能电站", "Yangqu": "羊曲水电站", "Yongtai": "永泰抽水蓄能电站",
    "Baoquan": "宝泉抽水蓄能电站", "Nanning": "南宁抽水蓄能电站", "Geheyan": "隔河岩水电站",
}

# Oil & gas field place names (GOGET English → Chinese). Offshore blocks keep their number (流花16-2油田).
FIELD_PLACE_ZH = {
    "Sartu": "萨尔图", "Keshen": "克深", "Yan'an": "延安", "Bozi": "博孜", "Xingshugang": "杏树岗", "Yulin": "榆林",
    "Fuling": "涪陵", "Puguang": "普光", "Moxi": "磨溪", "Anyue": "安岳", "Kela": "克拉", "Jing'an": "靖安",
    "Changning": "长宁", "Tianfu": "天府", "Shenmu": "神木", "Lamadian": "喇嘛甸", "Gaoshiti": "高石梯",
    "Jingbian": "靖边", "Gudao": "孤岛", "Mahu": "玛湖", "Jiyuan": "姬塬", "Qingcheng": "庆城", "Fuman": "富满",
    "Shunbei": "顺北", "Sebei": "涩北", "Yuanba": "元坝", "Chunxiao/Shirakaba": "春晓", "Chunxiao": "春晓",
    "Dabei": "大北", "Fengcheng": "风城", "Wuqi": "吴起", "Luojiazhai": "罗家寨", "Daniudi": "大牛地", "Weiyuan": "威远",
    "Hujianshan": "胡尖山", "Qingshen": "庆深", "Shuguang": "曙光", "Qinshui": "沁水", "Hade": "哈得", "Dina": "迪那",
    "Linxing": "临兴", "Zhongjiang": "中江", "Tuha": "吐哈", "Kelameili": "克拉美丽", "Dongsheng": "东胜",
    "Cainan": "彩南", "Daji": "大吉", "Tieshanpo": "铁山坡", "Chuanxi": "川西", "Shapingchang": "沙坪场", "Tahe": "塔河",
    "Bajiaochang": "八角场", "Gudong": "孤东", "Zizhou": "子洲", "Guang'an": "广安", "Dongxin": "东辛", "Xifeng": "西峰",
    "Chunfeng": "春风", "Mizhi": "米脂", "Putaohua": "葡萄花", "Weirong": "威荣", "Luliang": "陆梁", "Gasikule": "尕斯库勒",
    "Chunguang": "春光", "Halahatang": "哈拉哈塘", "Aershan": "阿尔善", "Lunnan": "轮南", "Xin'anbian": "新安边",
    "Huanjiang": "环江", "Tazhong": "塔中", "Hadexun": "哈得逊", "Chaoyanggou": "朝阳沟", "Yichuan": "宜川",
    "Heshui": "合水", "Songnan": "松南", "Songfangtun": "宋芳屯", "Chepaizi": "车排子", "Qingyang": "庆阳", "Ansai": "安塞",
    "Tainan": "台南", "Tuziluoke": "吐孜洛克", "Mosuowan": "莫索湾", "Jiyang": "济阳", "Changling": "长岭",
    "Taipingtun": "太平屯", "Yingdong": "英东", "Hutubi": "呼图壁", "Hailar": "海拉尔", "Fushan": "福山",
    "Yushulin": "榆树林", "Chengdao": "埕岛", "Xujiaweizi": "徐家围子", "Yaha": "牙哈", "Longhupao": "龙虎泡",
    "Pucheng": "濮城", "Shixi": "石西", "Xiazijie": "夏子街", "Dongping": "东坪", "Xinchang": "新场", "Huaqing": "华庆",
    "Qingshimao": "青石峁", "Xinglongtai": "兴隆台", "Shengtuo": "胜坨", "Wolonghe": "卧龙河", "Kekeya": "柯克亚",
    "Kunbei": "昆北", "Huanxiling": "欢喜岭", "Ziliujing": "自流井", "Longgang": "龙岗", "Donghetang": "东河塘",
    "Yakela": "雅克拉", "Zhenbei": "镇北", "Yingmaili": "英买力", "Madong": "玛东", "Nanpu": "南堡", "Nanchuan": "南川",
    "Hechuan": "合川", "Qijiang": "綦江", "Jianghan": "江汉", "Wubaiti": "五百梯", "Qintong": "溱潼", "Gaoyou": "高邮",
    "Mabi": "马必", "Daning": "大宁", "Yanchuannan": "延川南", "Jinlong": "金龙", "Xiaoguai": "小拐", "Dalaoba": "大涝坝",
    "Mobei": "莫北", "Penglai": "蓬莱", "Renqiu": "任丘", "Changbei": "长北", "Zhaozhou": "肇州", "Yongle": "永乐",
    "Hetianhe": "和田河", "Nanbaxian": "南八仙", "Shinan": "石南", "Dukouhe-Qilibei": "渡口河—七里北", "Chaheji": "岔河集",
    "Kenxi": "垦西", "Xujiahe": "须家河", "Bayantala": "巴彦塔拉", "Baolige": "宝力格", "Suderte": "苏德尔特",
    "Huhenuoren": "呼和诺仁", "Surennuoer": "苏仁诺尔", "Aer": "阿尔", "Nanliang": "南梁", "Shuangjiaba": "双家坝",
    "Qijiabei": "齐家北", "Aonan": "敖南", "Hongde": "洪德", "Huangjiang": "环江", "Zhahaquan": "扎哈泉",
    # offshore blocks (CNOOC)
    "Liuhua": "流花", "Lufeng": "陆丰", "Huizhou": "惠州", "Enping": "恩平", "Xijiang": "西江", "Panyu": "番禺",
    "Wenchang": "文昌", "Weizhou": "涠洲", "Dongfang": "东方", "Ledong": "乐东", "Lingshui": "陵水", "Yacheng": "崖城",
    "Bozhong": "渤中", "Luda": "旅大", "Suizhong": "绥中", "Jinzhou": "锦州", "Caofeidian": "曹妃甸",
    "Qinhuangdao": "秦皇岛", "Kenli": "垦利", "Qikou": "歧口", "Liwan": "荔湾", "Wushi": "乌石", "Pinghu": "平湖",
}
FIELD_SUFFIX_ZH = [  # longest first
    ("Oil and Gas Field", "油气田", "oil_gas"), ("Oil and Gas Asset", "油气田", "oil_gas"),
    ("Oil and Gas Phase", "油气田", "oil_gas"), ("Oil Phase", "油田", "oil"), ("Gas Phase", "气田", "gas"),
    ("Shale Gas Field", "页岩气田", "gas"), ("Tight Gas Field", "致密气田", "gas"), ("CBM Gas Field", "煤层气田", "gas"),
    ("CBM Oil Field", "煤层气田", "gas"), ("Shale Oil Field", "页岩油田", "oil"), ("Tight Oil Field", "致密油田", "oil"),
    ("Oil Field", "油田", "oil"), ("Gas Field", "气田", "gas"), ("Gas Asset", "气田", "gas"), ("Oil Asset", "油田", "oil"),
]
OFFSHORE_BASIN = {
    **dict.fromkeys(["Bozhong", "Luda", "Suizhong", "Jinzhou", "Caofeidian", "Qinhuangdao", "Kenli", "Qikou"], "渤海湾盆地"),
    **dict.fromkeys(["Liuhua", "Lufeng", "Huizhou", "Enping", "Xijiang", "Panyu", "Liwan", "Wenchang"], "珠江口盆地"),
    **dict.fromkeys(["Weizhou", "Wushi"], "北部湾盆地"), **dict.fromkeys(["Dongfang", "Ledong"], "莺歌海盆地"),
    **dict.fromkeys(["Lingshui", "Yacheng"], "琼东南盆地"), **dict.fromkeys(["Chunxiao/Shirakaba", "Chunxiao", "Pinghu"], "东海陆架盆地"),
}
OPERATOR_BASIN = [  # (substring of GEM operator, basin), first match wins
    ("Hulunbuir", "海拉尔盆地"), ("Erlian", "二连盆地"), ("Daqing Oilfield Chongqing", "四川盆地"), ("Daqing", "松辽盆地"),
    ("Jilin", "松辽盆地"), ("Northeast Oil", "松辽盆地"), ("Changqing", "鄂尔多斯盆地"), ("Yanchang", "鄂尔多斯盆地"),
    ("North China Petroleum Bureau", "鄂尔多斯盆地"), ("Talimu", "塔里木盆地"), ("Tarim", "塔里木盆地"),
    ("Northwest", "塔里木盆地"), ("Xinjiang Oilfield", "准噶尔盆地"), ("Tuha", "吐哈盆地"), ("Qinghai", "柴达木盆地"),
    ("Qianhai", "柴达木盆地"), ("Southwest", "四川盆地"), ("Xi'nan", "四川盆地"), ("Puguang", "四川盆地"),
    ("Jianghan Oilfield", "四川盆地"), ("Chongqing Shale", "四川盆地"), ("Shengli", "渤海湾盆地"), ("Liaohe", "渤海湾盆地"),
    ("Huabei", "渤海湾盆地"), ("Jidong", "渤海湾盆地"), ("Zhongyuan Oilfield Company Pucheng", "渤海湾盆地"),
    ("Coalbed Methane", "沁水盆地"), ("Shanxi Coal Bed", "沁水盆地"),
]

OPERATOR_ZH = [  # (substring of GEM operator, Chinese operating company), first match wins
    ("Daqing", "大庆油田"), ("Changqing", "长庆油田"), ("Talimu", "塔里木油田"), ("Tarim", "塔里木油田"),
    ("Xinjiang Oilfield", "新疆油田"), ("PetroChina Southwest", "西南油气田"), ("Xi'nan Oil", "西南油气田"),
    ("Shengli", "胜利油田"), ("Qinghai", "青海油田"), ("Qianhai", "青海油田"), ("Liaohe", "辽河油田"),
    ("Huabei", "华北油田"), ("Tuha", "吐哈油田"), ("PetroChina Jilin", "吉林油田"), ("Jidong", "冀东油田"),
    ("Yanchang", "延长石油"), ("Jianghan", "江汉油田"), ("Zhongyuan", "中原油田"), ("Sinopec Northwest", "西北油田"),
    ("Sinopec Southwest", "西南油气分公司"), ("North China Petroleum Bureau", "华北油气分公司"), ("Henan Oilfield", "河南油田"),
    ("Jiangsu Oilfield", "江苏油田"), ("East China Oil", "华东油气分公司"), ("Northeast Oil", "东北油气分公司"),
    ("Chongqing Shale", "重庆页岩气公司"), ("PetroChina Coalbed", "中石油煤层气公司"), ("CNOOC", "中国海油"),
]

_SHARE = re.compile(r"\s*\[[^\]]*\]")


def _num(v) -> float | None:
    """GEM number text → float; '' / 'unknown' / '>' forms → None."""
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _year(v) -> int | None:
    n = _num(v)
    return int(n) if n is not None and 1850 <= n <= 2100 else None


def _owner(v: str) -> str | None:
    s = _SHARE.sub("", v or "").strip(" ;")
    return s or None


def plant_fuel(asset_type: str, tech: str, fuel: str) -> str | None:
    """Map a GIPT unit (asset-type, tech-type, fuel) to our fuel id (see module docstring)."""
    a, t, f = asset_type.strip().lower(), tech.strip().lower(), fuel.strip().lower()
    if a == "coal":
        return "coal"
    if a == "oil/gas":
        if not f or "fossil gas" in f or "lng" in f or "coalbed" in f:
            return "gas"
        if "fossil liquids" in f:
            return "oil"
        return "other"
    if a == "nuclear":
        return "nuclear"
    if a == "hydropower":
        return "pumped_storage" if t == "pumped storage" else "hydro"
    if a == "wind":
        return "wind"
    if a == "utility-scale solar":
        return "solar"
    if a == "bioenergy":
        return "biomass"
    if a == "geothermal":
        return "geothermal"
    return None


def aggregate_plants(rows, zh_names: dict[str, str] | None = None) -> list[dict]:
    """GIPT unit rows (dicts with the CSV's columns) → one record per (project id, fuel), operating units only."""
    zh_names = zh_names or {}
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("country-area1") not in AREAS or r.get("status", "").strip().lower() != "operating":
            continue
        fuel = plant_fuel(r.get("asset-type", ""), r.get("tech-type", ""), r.get("fuel", ""))
        lat, lon = _num(r.get("Latitude")), _num(r.get("Longitude"))
        if fuel is None or lat is None or lon is None:
            continue
        groups[(r.get("project-id") or r.get("name"), fuel)].append(r)
    out = []
    for (pid, fuel), units in groups.items():
        caps = [_num(u.get("capacity")) for u in units]
        known = [c for c in caps if c is not None]
        big = max(units, key=lambda u: _num(u.get("capacity")) or 0)
        years = [y for y in (_year(u.get("start-year")) for u in units) if y]
        name_en = big.get("name") or None
        short = re.sub(r"\s+hydroelectric plant$", "", name_en or "")
        rec = {
            "name": zh_names.get(pid) or (HYDRO_ZH.get(short) if fuel in ("hydro", "pumped_storage") else None),
            "name_en": name_en,
            "fuel": fuel,
            "capacity_mw": round(sum(known), 1) if known else None,
            "year": min(years) if years else None,
            "year_last": max(years) if years else None,
            "units": len(units),
            "owner": _owner(big.get("owner", "")),
            "status": "operating",
            "lon": round(_num(big["Longitude"]), 5),
            "lat": round(_num(big["Latitude"]), 5),
            "area": big.get("country-area1"),
            "gem_id": pid,
            "src": "gem_gipt",
        }
        techs = {u.get("tech-type", "").lower() for u in units}
        if fuel == "wind":
            rec["offshore"] = any(t.startswith("offshore") for t in techs)
        if fuel == "solar" and any("thermal" in t for t in techs):
            rec["tech"] = "solar_thermal"
        out.append(rec)
    out.sort(key=lambda p: (-(p["capacity_mw"] or 0), p["name_en"] or ""))
    return out


def field_name_zh(name_en: str) -> tuple[str | None, str | None, str | None]:
    """GOGET English field name → (Chinese name or None, kind from the suffix or None, place key or None)."""
    s = re.sub(r"\s*\([^)]*\)\s*$", "", name_en or "").strip()  # trailing "(Province, China)"
    s = re.sub(r"\s*\([^)]*\)", "", s).strip()                    # inner "(Deep Sea No.1 I)"
    for suf, zh, kind in FIELD_SUFFIX_ZH:
        if s.endswith(" " + suf):
            stem = s[: -len(suf) - 1].strip()
            m = re.match(r"^(?P<place>[A-Za-z'’/\- ]+?)(?:\s+(?P<num>\d[\dA-Za-z/\- ]*))?$", stem)
            if not m:
                return None, kind, None
            place = m.group("place").strip()
            num = (m.group("num") or "").split(" ")[0].rstrip("/-")  # "11-2 2 … Phase" → block 11-2
            if place in FIELD_PLACE_ZH:
                return f"{FIELD_PLACE_ZH[place]}{num}{zh}", kind, place
            return None, kind, place
    return None, None, None


def field_basin(operator: str, place: str | None) -> str | None:
    if place and place in OFFSHORE_BASIN:
        return OFFSHORE_BASIN[place]
    for key, basin in OPERATOR_BASIN:
        if key in (operator or ""):
            return basin
    return None


def parse_fields(features) -> list[dict]:
    out = []
    for f in features:
        p = f.get("properties") or {}
        if p.get("country-area1") not in AREAS or (p.get("status") or "").lower() != "operating":
            continue
        lat, lon = _num(p.get("Latitude")), _num(p.get("Longitude"))
        if lat is None or lon is None:
            continue
        oil, gas = _num(p.get("prod-oil")), _num(p.get("prod-gas"))
        oil_y, gas_y = _year(p.get("prod-year-oil")), _year(p.get("prod-year-gas"))
        zh, kind_name, place = field_name_zh(p.get("name", ""))
        if oil is not None and gas is not None:
            kind = "oil_gas"
        elif oil is not None:
            kind = "oil"
        elif gas is not None:
            kind = "gas"
        else:
            kind = kind_name
        if kind_name == "oil_gas":  # the name says both; GEM may report only one product
            kind = "oil_gas"
        boe = _num(p.get("capacity"))
        years = [y for y in (oil_y, gas_y) if y]
        out.append({
            "name": zh,
            "name_en": re.sub(r"\s*\([^)]*\)\s*$", "", p.get("name", "")).strip() or None,
            "kind": kind,
            "basin": field_basin(p.get("operator", ""), place),
            "operator": p.get("operator") or None,
            "operator_zh": next((zh_ for key, zh_ in OPERATOR_ZH if key in (p.get("operator") or "")), None),
            "production": round(boe * 1e6) if boe is not None else None,
            "production_unit": "boe/yr" if boe is not None else None,
            "year": max(years) if years else None,
            "oil_bbl": round(oil * 1e6) if oil is not None else None, "oil_year": oil_y,
            "gas_m3": round(gas * 1e6) if gas is not None else None, "gas_year": gas_y,
            "start_year": _year(p.get("start-year")),
            "lon": round(lon, 5), "lat": round(lat, 5), "rings": None,
            "gem_id": p.get("project-id"), "src": "gem_goget",
        })
    out.sort(key=lambda r: (-(r["production"] or 0), r["name_en"] or ""))
    return out


def _clean_zh(v) -> str | None:
    s = re.sub(r"\s*\((?:Chinese|中文)\)\s*$", "", (v or "").strip())
    return s or None


def parse_mines(gcmt_features, giomt_features) -> list[dict]:
    out = []
    for f in gcmt_features:
        p = f.get("properties") or {}
        if p.get("country-area1") not in AREAS or (p.get("status") or "").lower() != "operating":
            continue
        lat, lon = _num(p.get("Latitude")), _num(p.get("Longitude"))
        if lat is None or lon is None:
            continue
        cap, prod = _num(p.get("capacity")), _num(p.get("prod-coal"))
        out.append({
            "name": _clean_zh(p.get("name-noneng")), "name_en": p.get("name") or None, "commodity": "coal",
            "capacity": round(cap * 1e6) if cap is not None else None, "capacity_unit": "t/yr" if cap is not None else None,
            "production": round(prod * 1e6) if prod is not None else None,
            "production_year": _year(p.get("prod-year-coal")) if prod is not None else None,
            "mine_type": (p.get("mine-type") or "").lower() or None, "coal_grade": (p.get("coal-grade") or "").lower() or None,
            "workforce": int(_num(p.get("workforce"))) if _num(p.get("workforce")) is not None else None,
            "year": _year(p.get("start-year")), "owner": _owner(p.get("owner", "")), "status": "operating",
            "lon": round(lon, 5), "lat": round(lat, 5), "gem_id": p.get("project-id"), "src": "gem_gcmt",
        })
    for f in giomt_features:
        p = f.get("properties") or {}
        if p.get("country-area1") not in AREAS or (p.get("status") or "").lower() != "operating":
            continue
        lat, lon = _num(p.get("Latitude")), _num(p.get("Longitude"))
        if lat is None or lon is None:
            continue
        cap = _num(p.get("capacity"))  # thousand tonnes per year
        out.append({
            "name": _clean_zh(p.get("name-noneng")), "name_en": p.get("name") or None, "commodity": "iron",
            "capacity": round(cap * 1e3) if cap is not None else None, "capacity_unit": "t/yr" if cap is not None else None,
            "production": None, "production_year": None, "year": _year(p.get("start-year")),
            "owner": _owner(p.get("owner", "")), "status": "operating",
            "lon": round(lon, 5), "lat": round(lat, 5), "gem_id": p.get("project-id"), "src": "gem_giomt",
        })
    return out


def parse_wikidata_mines(entities: dict) -> list[dict]:
    out = []
    for qid, zh, commodity in METAL_MINES:
        e = entities.get(qid) or {}
        try:
            v = e["claims"]["P625"][0]["mainsnak"]["datavalue"]["value"]
            lon, lat = float(v["longitude"]), float(v["latitude"])
        except (KeyError, IndexError, TypeError, ValueError):
            print(f"      {qid} {zh}: no coordinates on Wikidata, skipped")
            continue
        if not (73 <= lon <= 136 and 17 <= lat <= 54):
            print(f"      {qid} {zh}: coordinates outside China ({lon}, {lat}), skipped")
            continue
        out.append({
            "name": zh, "name_en": (e.get("labels", {}).get("en") or {}).get("value"), "commodity": commodity,
            "capacity": None, "capacity_unit": None, "production": None, "production_year": None, "year": None,
            "owner": None, "status": "operating", "lon": round(lon, 5), "lat": round(lat, 5),
            "wikidata": qid, "src": "wikidata",
        })
    return out


class CnEnergy(Source):
    info = SourceInfo(
        id="cn_energy",
        title="中国发电厂、煤矿与金属矿山、油气田",
        publisher="Global Energy Monitor（全球能源监测）；矿山坐标补充：Wikidata",
        homepage="https://globalenergymonitor.org/projects/global-integrated-power-tracker/",
        license="Global Energy Monitor 数据 CC BY 4.0；Wikidata CC0",
        description="发电厂：Global Energy Monitor《全球综合电力追踪》（GIPT）地图数据，逐机组记录（煤电、气电、油电、核电、"
        "常规水电、抽水蓄能、风电、集中式光伏/光热、生物质、地热），只取“运行中”机组，按电厂（GEM 项目编号）与燃料汇总装机容量"
        "（兆瓦，铭牌容量），投产年取最早机组；煤电与核电的中文厂名取自 GEM 煤电/核电追踪的中文名，大型水电与抽蓄的中文名为本项目对照翻译。"
        "GEM 不追踪储能电站，故无电池储能数据（为空，非 0）。煤矿：GEM《全球煤矿追踪》（运行中，产能 万吨/年按百万吨×10⁶ 换算为吨/年，"
        "并附 GEM 记录的产量与年份）；铁矿：GEM《全球铁矿追踪》（运行中，产能千吨/年换算为吨/年）。"
        "主要有色金属、稀土、锂矿：本项目按“各矿种代表性大型矿山”精选的名单（白云鄂博、牦牛坪、德兴铜矿、甲基卡等），坐标取自 Wikidata，"
        "无公开的逐矿产能数据，产能为空。油气田：GEM《全球油气开采追踪》（GOGET）中国运行中的油气田，产量按 GEM 的桶油当量/年，"
        "另列原油（桶/年）与天然气（立方米/年）产量及年份；中文田名与所属盆地由本项目按地名对照表生成，无对照者为空。"
        "台湾、香港、澳门的电厂按 GEM 各自的国家/地区字段并入。坐标为 WGS-84，未作转换。src 键：gem_gipt、gem_gcmt、gem_giomt、"
        "gem_goget、wikidata。",
        used_for=["资源：发电厂（按燃料）", "资源：煤矿、铁矿与主要金属/稀土/锂矿", "资源：油气田", "省级发电装机统计"],
    )

    # --- fetch ---------------------------------------------------------------------------------------------

    def _data_url(self, tracker: str, force: bool) -> str:
        """Current data file named in the tracker map's config.js; the pinned URL if that cannot be read."""
        try:
            path = self.download(f"{GEM_MAPS}/{tracker}/config.js", f"gem/config_{tracker}.js", force=force)
            m = re.search(r"""(?m)^\s*(?:csv|geojson)\s*:\s*['"]([^'"]+)['"]""", path.read_text(encoding="utf-8"))
            if m and m.group(1).startswith("https://"):
                return m.group(1)
        except Exception as exc:  # network or parse problem: fall back to the pinned file
            print(f"      {tracker}: config.js not usable ({exc}); using the pinned file")
        return TRACKERS[tracker]

    def _local(self, tracker: str) -> str:
        cfg = self.raw_dir / "gem" / f"config_{tracker}.js"
        url = TRACKERS[tracker]
        if cfg.exists():
            m = re.search(r"""(?m)^\s*(?:csv|geojson)\s*:\s*['"]([^'"]+)['"]""", cfg.read_text(encoding="utf-8"))
            if m and m.group(1).startswith("https://"):
                url = m.group(1)
        return f"gem/{url.rsplit('/', 1)[-1]}"

    def fetch(self, force: bool = False) -> None:
        for tracker in TRACKERS:
            url = self._data_url(tracker, force)
            self.download(url, f"gem/{url.rsplit('/', 1)[-1]}", force=force)
        ids = "%7C".join(q for q, _, _ in METAL_MINES)
        self.download(WIKIDATA + ids, "wikidata/metal_mines.json", force=force)

    # --- process -------------------------------------------------------------------------------------------

    def _features(self, tracker: str) -> list[dict]:
        return json.loads((self.raw_dir / self._local(tracker)).read_text(encoding="utf-8"))["features"]

    def process(self) -> dict:
        files = [self._local(t) for t in TRACKERS] + ["wikidata/metal_mines.json"]
        files += [f"gem/config_{t}.js" for t in TRACKERS if (self.raw_dir / f"gem/config_{t}.js").exists()]

        zh: dict[str, str] = {}
        for tracker in ("gcpt", "gnpt"):
            for f in self._features(tracker):
                p = f.get("properties") or {}
                n = _clean_zh(p.get("name-noneng"))
                if n and p.get("country-area1") in AREAS and p.get("project-id"):
                    zh.setdefault(p["project-id"], n)
        with open(self.raw_dir / self._local("integrated-power"), encoding="utf-8", newline="") as fh:
            plants = aggregate_plants(csv.DictReader(fh), zh)

        mines = parse_mines(self._features("gcmt"), self._features("giomt"))
        entities = json.loads((self.raw_dir / "wikidata/metal_mines.json").read_text(encoding="utf-8")).get("entities", {})
        mines += parse_wikidata_mines(entities)
        fields = parse_fields(self._features("goget"))

        def ver(t):
            m = re.search(r"(\d{4}-\d{2})", self._local(t))
            return f"Global Energy Monitor {TRACKER_NAMES[t]}（{m.group(1) if m else '?'} 版地图数据）"

        totals = defaultdict(float)
        for p in plants:
            totals[p["fuel"]] += p["capacity_mw"] or 0
        print("      plants:", len(plants), {k: round(v) for k, v in sorted(totals.items(), key=lambda x: -x[1])})
        print("      mines:", len(mines), "fields:", len(fields))
        return {
            "vintage": {"plants": ver("integrated-power"),
                        "mines": f"{ver('gcmt')}；{ver('giomt')}；主要金属矿坐标 Wikidata",
                        "fields": ver("goget")},
            "plants": plants,
            "mines": mines,
            "fields": fields,
            "_files": files,
        }
