// City View data model: everything the city screen and its map layer show, derived from cities.json,
// nation.json, lenses.json, world.json and resources.json. Pure functions over app.data / app.w (no DOM).

// Industry → short label, district colour (the medallion behind its icon) and icon id (icons.js i-<key>).
export const INDUSTRY = {
  agriculture: { short: "农业", color: "#4f7a2a" },
  mining: { short: "采矿", color: "#6a5038" },
  construction: { short: "建筑", color: "#8a5426" },
  manufacturing: { short: "制造", color: "#9a5a22" },
  wholesale: { short: "批发", color: "#7a6420" },
  retail: { short: "批发零售", color: "#8a6a1e" },
  transportation: { short: "交通物流", color: "#3d5f73" },
  utilities: { short: "公用事业", color: "#2a5f86" },
  information: { short: "信息技术", color: "#25557e" },
  finance: { short: "金融", color: "#7a6420" },
  real_estate: { short: "房地产", color: "#6b4a2f" },
  professional: { short: "科研技术", color: "#2b4f8a" },
  management: { short: "企业管理", color: "#4a4a7a" },
  admin: { short: "商务服务", color: "#4b5a68" },
  education: { short: "教育", color: "#2a3f7a" },
  health: { short: "卫生", color: "#7a2f2f" },
  arts: { short: "文体娱乐", color: "#5b3a7e" },
  accommodation: { short: "住宿餐饮", color: "#6e3a5e" },
  other: { short: "居民服务", color: "#4b4b4b" },
  public_admin: { short: "公共管理", color: "#3a5a4a" },
  government: { short: "政府", color: "#3a5a4a" },
  // three-sector GDP (chinaciv gdp_by_industry)
  primary: { short: "第一产业", color: "#4f7a2a", icon: "y-food" },
  secondary: { short: "第二产业", color: "#8a5426", icon: "y-production" },
  tertiary: { short: "第三产业", color: "#7a6420", icon: "y-gold" },
};
// GB/T 4754 sections A–S (chinaciv jobs keys) → the usaciv industry whose glyph and colour fit.
const SECTION = {
  A: "agriculture", B: "mining", C: "manufacturing", D: "utilities", E: "construction", F: "retail", G: "transportation",
  H: "accommodation", I: "information", J: "finance", K: "real_estate", L: "admin", M: "professional", N: "utilities",
  O: "other", P: "education", Q: "health", R: "arts", S: "public_admin",
};
const base = (k) => SECTION[k] || k;
const info = (k) => INDUSTRY[k] || INDUSTRY[base(k)];
export const industryIcon = (k) => INDUSTRY[k]?.icon || `i-${base(k) === "government" ? "public_admin" : base(k)}`;

// Land-use groups shown as a hex's "tile yield" (world.json pct keys → group). Cover ids give the map colour.
export const LAND = [
  { key: "built", label: "建成区", icon: "l-developed", cover: 9, keys: ["developed"] },
  { key: "farm", label: "耕地", icon: "l-farmland", cover: 7, keys: ["crop", "pasture"] },
  { key: "forest", label: "森林", icon: "l-forest", cover: 3, keys: ["forest"] },
  { key: "open", label: "草地与荒地", icon: null, cover: 4, keys: ["grass", "shrub", "wetland"] },
];

const sum = (o) => Object.values(o || {}).reduce((a, v) => a + (Number.isFinite(v) ? v : 0), 0);
const HEX_AREA = (R) => (3 * Math.sqrt(3) / 2) * R * R; // km² of one hex

/** Industries by employment (cities.json jobs) with location quotients vs the nation. */
export function jobIndustries(city, nation) {
  const n = nation.nation;
  const total = city.employed || sum(city.jobs) || 1;
  const nTotal = n.employed || sum(n.jobs) || 1;
  return Object.entries(city.jobs || {})
    .filter(([, v]) => Number.isFinite(v) && v > 0)
    .map(([key, jobs]) => {
      const share = jobs / total;
      const natShare = (n.jobs?.[key] || 0) / nTotal;
      return {
        key, jobs, share, natShare, lq: natShare > 0 ? share / natShare : null,
        label: nation.industries?.[key] || key, short: info(key)?.short || nation.industries?.[key] || key,
        color: info(key)?.color || "#3a4a5e", icon: industryIcon(key),
      };
    })
    .sort((a, b) => b.jobs - a.jobs);
}

/** GDP by industry (chinaciv: the three sectors) with shares and location quotients vs national GDP shares. */
export function gdpIndustries(city, nation) {
  const g = city.gdp_by_industry;
  const ng = nation.nation.gdp_by_industry || {};
  if (!g) return [];
  // Shares of the metro's whole GDP (not of the disclosed industries only), so a withheld sector cannot inflate
  // the others.
  const total = Number.isFinite(city.gdp) && city.gdp > 0 ? city.gdp : sum(g) || 1;
  const nTotal = Number.isFinite(nation.nation.gdp) && nation.nation.gdp > 0 ? nation.nation.gdp : sum(ng) || 1;
  const est = new Set(city.gdp_estimated || []);
  const rows = Object.entries(g)
    .filter(([, v]) => Number.isFinite(v) && v > 0)
    .map(([key, value]) => {
      const share = value / total, natShare = (ng[key] || 0) / nTotal;
      return {
        key, value, share, natShare, lq: natShare > 0 ? share / natShare : null, estimated: est.has(key),
        label: INDUSTRY[key]?.short && ["primary", "secondary", "tertiary"].includes(key) ? `${INDUSTRY[key].short}增加值` : key === "government" ? "政府" : nation.industries?.[key] || key,
        short: info(key)?.short || key, color: info(key)?.color || "#3a4a5e", icon: industryIcon(key),
      };
    })
    .sort((a, b) => b.value - a.value);
  // What BEA still withholds after recovery, as its own grey row.
  const und = Number.isFinite(city.gdp_undisclosed) ? city.gdp_undisclosed
    : Object.values(g).some((v) => v == null) && Number.isFinite(city.gdp) ? Math.max(0, city.gdp - sum(g)) : 0;
  if (und > 0) {
    const missing = Object.entries(g).filter(([, v]) => v == null).map(([k]) => k);
    rows.push({
      key: "undisclosed", value: und, share: und / total, natShare: 0, lq: null, undisclosed: true, missing,
      label: "未公开", short: "未公开", color: "#4a5260", icon: "lock",
    });
  }
  return rows;
}

function landOf(w, cell) {
  if (!w.pct) return null;
  let best = null, tot = 0;
  for (const g of LAND) {
    const v = g.keys.reduce((a, k) => a + (w.pct[k]?.[cell] || 0), 0);
    tot += v;
    if (!best || v > best.share) best = { ...g, share: v };
  }
  if (!best || tot <= 0) return null;
  return { ...best, share: best.share / 100 };
}

/**
 * Everything the City View shows for one city.
 * Per footprint hex: estimated residents (lens "density" value × hex area: an estimate, not a count) and the
 * dominant land-use group with its share of the hex's land.
 */
export function buildModel(app, city) {
  const { w } = app;
  const { nation, cities, resources, lenses } = app.data;
  const grid = w.grid;
  const N = cities.length;
  const owner = w.raw.owner;
  const stateIdx = city.cell != null ? owner[city.cell] : -1;
  const stateRGB = w.stateColor[stateIdx] || [201, 164, 92];

  const footprint = (Array.isArray(city.footprint) && city.footprint.length ? city.footprint : [city.cell])
    .filter((c) => c != null && c >= 0);
  const mask = new Set(footprint);

  // Resource sites of the metro (resources.json items tagged with this cbsa), biggest first per type.
  const items = resources?.items || [];
  const typeById = new Map((resources?.types || []).map((t) => [t.id, t]));
  const sites = [];
  items.forEach((it, index) => {
    if (it.cbsa === city.id) sites.push({ item: it, index, type: typeById.get(it.type) || null });
  });
  const catRank = { energy: 0, mineral: 1, agri: 2 };
  sites.sort((a, b) => (catRank[a.type?.category] ?? 3) - (catRank[b.type?.category] ?? 3)
    || (b.item.value ?? 0) - (a.item.value ?? 0));
  const siteCells = new Map(); // cell -> [site]
  for (const s of sites) {
    if (s.item.cell == null) continue;
    if (!siteCells.has(s.item.cell)) siteCells.set(s.item.cell, []);
    siteCells.get(s.item.cell).push(s);
  }

  // Per-hex tile yields.
  const density = lenses?.lenses?.find((l) => l.id === "density");
  const area = HEX_AREA(grid.R);
  const tiles = footprint.map((cell) => {
    const d = density?.values?.[cell];
    return {
      cell, x: grid.cx(cell), y: grid.cy(cell),
      residents: Number.isFinite(d) ? d * area : null,
      land: landOf(w, cell),
      isCity: cell === city.cell,
    };
  });

  // Industries (ACS) and GDP by industry (BEA). The map's districts are the real ones from districts.json (BLS
  // QCEW), placed by placeDistricts() once that file arrives; until then there are none.
  const inds = jobIndustries(city, nation);
  const gdpInds = gdpIndustries(city, nation);
  // District plaques go on footprint hexes near the centre that have no resource site and no other city; the rest
  // (small metros) sit in a row inside the city's own hex.
  const cx = grid.cx(city.cell), cy = grid.cy(city.cell);
  // The city's banner stands over the hexes just north of it, so those come after the other near hexes.
  const R = grid.R;
  const cost = (c) => {
    const dx = grid.cx(c) - cx, dy = grid.cy(c) - cy;
    return Math.hypot(dx, dy) + (dy < -0.5 * R && dy > -3.2 * R && Math.abs(dx) < 3 * R ? 3 * R : 0);
  };
  const free = footprint
    .filter((c) => c !== city.cell && !siteCells.has(c) && !w.cityByCell.has(c))
    .sort((a, b) => cost(a) - cost(b) || a - b);

  // Bounding box of the footprint (km).
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const c of footprint) {
    const x = grid.cx(c), y = grid.cy(c);
    x0 = Math.min(x0, x - grid.R); x1 = Math.max(x1, x + grid.R);
    y0 = Math.min(y0, y - grid.R); y1 = Math.max(y1, y + grid.R);
  }

  const peers = (city.peers || []).map((id) => app.cityById.get(id)).filter(Boolean);
  // cities.json `states`: province codes (chinaciv) or postal codes (usaciv test data)
  const states = (city.states || []).map((ref) => {
    const i = w.raw.states.findIndex((s) => s.fips === ref || s.abbr === ref);
    const st = i >= 0 ? w.raw.states[i] : null;
    return { abbr: ref, index: i, name: st ? (app.data.states?.[i]?.full || st.full || app.data.states?.[i]?.name || st.name) : ref };
  });
  // Transport hubs and wonders in the city (infrastructure.json / wonders.json rows whose cbsa is the city).
  const infra = app.data.infrastructure || null;
  const hubs = [
    ...(infra?.airports || []).filter((a) => a.cbsa === city.id).map((a) => ({ kind: "airport", item: a })),
    ...(infra?.ports || []).filter((p) => p.cbsa === city.id).map((p) => ({ kind: "port", item: p })),
  ];
  const wonders = (app.data.wonders?.wonders || []).filter((x) => x.cbsa === city.id);
  const residentsSum = tiles.reduce((a, t) => a + (t.residents || 0), 0);

  return {
    city, N, nation, stateIdx, hexArea: area, stateRGB, states, footprint, mask, tiles, residentsSum,
    sites, siteCells, inds, gdpInds, free, districts: [], placed: [], inCity: [], hubs, wonders,
    bbox: Number.isFinite(x0) ? { x0, y0, x1, y1 } : null,
    peers,
    provinceName: (ref) => {
      const st = w.raw.states.find((x) => x.fips === ref || x.abbr === ref);
      return st ? st.name : ref;
    },
  };
}

/**
 * Put the metro's districts on the map (schematic positions): the first ones on the free footprint hexes nearest
 * the city, the rest in a row inside the city's own hex. list = [{id, short, mapLq, color, icon, …}] (districts.js
 * cityDistricts). Sets m.districts, m.placed and m.inCity; the map layer reads them on its next frame.
 */
export function placeDistricts(m, list) {
  const ds = (list || []).map((d) => ({ ...d, lq: d.mapLq ?? null }));
  m.districts = ds;
  m.placed = [];
  m.inCity = [];
  ds.forEach((d, k) => {
    if (k < m.free.length) m.placed.push({ ...d, cell: m.free[k] });
    else m.inCity.push(d);
  });
}

/** Population series [{year, value}] from pop_series (null years dropped). */
export function popSeries(city) {
  const s = city.pop_series;
  if (!s || typeof s !== "object") return [];
  return Object.entries(s)
    .filter(([, v]) => Number.isFinite(v))
    .map(([y, v]) => ({ year: +y, value: v }))
    .sort((a, b) => a.year - b.year);
}

/** How many cities have a non-null rank for this key (the "of N"). */
export function rankCount(cities, key) {
  let n = 0;
  for (const c of cities) if (c.ranks?.[key] != null) n++;
  return n;
}

/** A resource value with its unit, short ("3.9 GW", "120万 t/yr"). */
export function resourceValue(item, type, compact) {
  const v = item.value;
  if (!Number.isFinite(v)) return "";
  const u = type?.unit || "";
  if (u === "MW") return v >= 1000 ? `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)} GW` : `${Math.round(v)} MW`;
  if (u === "employees") return `${compact(v)} 人`;
  if (/tons|^t\b|t\/yr|吨/.test(u)) return `${compact(v)} ${/yr|年/.test(u) ? "吨/年" : "吨"}`;
  if (/km²/.test(u)) return `${compact(v)} km²`;
  return `${compact(v)} ${u}`.trim();
}
