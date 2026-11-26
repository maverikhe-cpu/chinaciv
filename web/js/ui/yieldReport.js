// Top-bar reports: click a national stat (population, a yield, unemployment) for a Civ-style dropdown with its
// definition, what makes it up, and state / metro leaderboards.
//
// The first half is pure data (statDefs, buildReport): plain objects from nation.json, states.json, cities.json and
// lenses.json, no DOM, so it is easy to check from the console (`window.__app.reports`). The second half renders.
import { compact, dollars, esc, int, money, pct, isMoneyUnit } from "../format.js";
import { hasIcon } from "../icons.js";
import { icon, glyph } from "./icon.js";
import { DISTRICT_ICON, SECTOR_LABEL, SECTOR_ICON, industryIconKey } from "./infoPanel.js";

export const METRO_MIN = 500000; // cities in growth / specialisation / per-person rankings
export const BIG_METRO_MIN = 1000000; // cities in the unemployment rankings
const TOP = 8;
const GDP_ROWS = 8; // industries shown before "Show all"
// A related map lens for some reports (shown only when lenses.json has it). These are related measures, not the
// report's own total, and the button says so.
const LENS_FOR = { population: "density", food: "cropland" };
// The advisor dashboard (ext/advisors) that explores each report further; `industry` preselects the Economy
// advisor's specialisation ranking. Buttons show only when the advisors extension is installed.
const ADVISOR_FOR = {
  population: [{ id: "explorer", label: "探索者" }],
  gold: [{ id: "economy", label: "经济" }],
  food: [{ id: "economy", label: "经济" }],
  production: [{ id: "economy", label: "经济" }, { id: "energy", label: "能源与资源" }],
  science: [{ id: "economy", label: "经济" }, { id: "culture", label: "文化（大学）" }],
  culture: [{ id: "culture", label: "文化" }],
};
const advisorApp = () => (typeof window !== "undefined" && window.__app?.hasMode?.("advisor") ? window.__app : null);

const fin = Number.isFinite;
const ratio = (a, b) => (fin(a) && fin(b) && b > 0 ? a / b : null);
export const signedPct = (g, d = 1) => (fin(g) ? `${g >= 0 ? "+" : "−"}${Math.abs(g * 100).toFixed(d)}%` : "–");

// Counts to about three significant figures (leaderboards: 1.12亿 vs 1.11亿); format.js compact already does it.
export function count3(n) {
  if (!fin(n)) return "–";
  return Math.abs(n) >= 1e4 ? compact(n) : int(n);
}

// Formats a value of a stat (money → ¥, "rate" → %, anything else → compact count).
export function fmtStat(def, v) {
  if (!fin(v)) return "–";
  if (isMoneyUnit(def.unit)) return money(v);
  if (def.unit === "rate") return pct(v);
  return compact(v);
}

// Every top-bar stat in display order: population, the yields from nation.json, unemployment.
export function statDefs(nation) {
  const n = nation?.nation || {};
  const yields = (nation?.yields || []).map((y) => ({
    key: y.key,
    label: y.label,
    name: y.key === "gold" ? `${y.label}（GDP）` : y.label,
    color: y.color,
    yield: y,
    unit: y.unit,
    description: y.description || "",
    source: y.key === "gold" ? nation.gdp_vintage || nation.vintage || "" : nation.vintage || "",
    value: y.key === "gold" ? n.gdp ?? n.yields?.gold ?? null : n.yields?.[y.key] ?? null,
  }));
  const popYear = n.population_year ?? nation?.year;
  return [
    {
      key: "population", label: "人口", name: "常住人口", color: "#e8dcc0", iconId: "pop", emoji: "👥", unit: "people",
      description: `年末常住人口${popYear ? `（${popYear} 年）` : ""}。${nation?.scope || ""}`, source: nation?.vintage || "", value: n.population ?? null,
    },
    ...yields,
    // usaciv only: chinaciv publishes no unemployment rate, so the stat is left out rather than shown as "–".
    ...(fin(n.unemployment) ? [{
      key: "unemployment", label: "失业率", name: "失业率", color: "#e8dcc0", iconId: "unemployment", emoji: "😐",
      unit: "rate", description: "失业人数 ÷ 劳动力。", source: nation?.vintage || "", value: n.unemployment,
    }] : []),
  ];
}

const industryLabel = (nation, k, gdp = false) => {
  const full = (gdp ? nation.gdp_industries?.[k] : null) || SECTOR_LABEL[k] || nation.industries?.[k] || (k === "government" ? "政府" : k);
  return { label: full.replace(/\s*[(（].*[)）]\s*$/, ""), full };
};
const industryIcon = (k) => {
  if (SECTOR_ICON[k]) return SECTOR_ICON[k][0];
  const ik = industryIconKey(k);
  return hasIcon(`i-${ik}`) ? `i-${ik}` : k === "government" && hasIcon("i-public_admin") ? "i-public_admin" : null;
};
const industryEmoji = (k) => SECTOR_ICON[k]?.[1] || DISTRICT_ICON[industryIconKey(k)] || "•";

// Areas the leaderboards rank: provinces (index into states.json = world.json states) and cities.
function areas(states, cities) {
  const list = Array.isArray(states) ? states : [];
  const st = list
    .map((o, i) => (o ? { kind: "state", ref: i, name: o.name || o.abbr || "?", sub: o.kind_label || "", o } : null))
    .filter(Boolean);
  // a city's province: its code (chinaciv) or postal code (usaciv), shown by the province's short name
  const provName = (ref) => list.find((s) => s && (s.fips === ref || s.abbr === ref))?.name || ref;
  const metros = (Array.isArray(cities) ? cities : [])
    .filter((c) => c && c.metro !== false)
    .map((c) => ({ kind: "city", ref: c.id, name: c.name, sub: (c.states || []).map(provName).join("–"), o: c }));
  return { st, metros };
}

// Top TOP rows by valueOf(o); null values are skipped. opts: {asc, min (population), fmt, exact (full-precision
// text for the row's tooltip), subOf}
function rank(list, valueOf, { asc = false, min = 0, fmt = count3, exact = int, subOf = null } = {}) {
  return list
    .filter((r) => !min || (r.o.population ?? 0) >= min)
    .map((r) => ({ r, v: valueOf(r.o) }))
    .filter((x) => fin(x.v))
    .sort((a, b) => (asc ? a.v - b.v : b.v - a.v))
    .slice(0, TOP)
    .map(({ r, v }, i) => ({ rank: i + 1, kind: r.kind, ref: r.ref, name: r.name, sub: subOf ? subOf(r.o, r.sub) : r.sub, value: v, text: fmt(v), exact: exact ? exact(v) : "" }));
}

// Which provinces the rankings cover. The national total follows the NBS scope (31 provinces, without Hong Kong,
// Macau and Taiwan) while the province list may include them where their own statistics exist: say so.
function stateScope(n, st, nation = {}) {
  const label = "省份";
  if (!st.length) return { label, note: "" };
  const ranked = st.filter((r) => fin(r.o.population)).length;
  const note = nation.scope ? `全国合计：${nation.scope}；省份排名只比较有数据的 ${ranked} 个省级行政区。` : "";
  return { label, inMap: label, note };
}

// Sorted [{r, v}] of every area with a value (for extremes and medians).
function sorted(list, valueOf, min = 0) {
  return list
    .filter((r) => !min || (r.o.population ?? 0) >= min)
    .map((r) => ({ r, v: valueOf(r.o) }))
    .filter((x) => fin(x.v))
    .sort((a, b) => b.v - a.v);
}

/**
 * The report for one stat, as plain data:
 * {key, def, total, totalText, context: [str], definition, source,
 *  breakdown: {title, note, groups: [{title, rows: [{label, full, iconId, emoji, value, text, sub, tone, strong}]}],
 *              collapseAfter} | null,
 *  boards: {note, modes: [{id, label, hint, states: [row], metros: [row]}]},   row: {rank, kind, ref, name, sub, value, text}
 *  lens: {id, name} | null}
 */
export function buildReport(key, { nation, states, cities, lenses, lensVintage } = {}) {
  const def = statDefs(nation).find((d) => d.key === key);
  if (!def) return null;
  const n = nation.nation || {};
  const { st, metros } = areas(states, cities);
  const scope = stateScope(n, st, nation);
  const fmt = (v) => fmtStat(def, v);
  const rep = {
    key, def, total: def.value, totalText: fmt(def.value), context: [], definition: def.description, source: def.source,
    breakdown: null, boards: { note: "", statesNote: scope.note, modes: [] }, lens: null, scope,
  };
  const lensId = LENS_FOR[key];
  const lens = lensId && Array.isArray(lenses) ? lenses.find((l) => l.id === lensId) : null;
  if (lens) {
    const vintage = lens.source && /^acs/i.test(lens.source) && typeof lensVintage === "string" ? lensVintage : "";
    rep.lens = { id: lens.id, name: lens.name || lens.id, unit: lens.unit || "", vintage };
  }

  if (key === "population") reportPopulation(rep, n, st, metros);
  else if (key === "unemployment") reportUnemployment(rep, n, st, metros);
  else if (key === "gold") reportGold(rep, nation, n, st, metros);
  else if (isMoneyUnit(def.unit)) reportValue(rep, def, nation, n, st, metros);
  else reportJobs(rep, def, nation, n, st, metros);
  return rep;
}

function reportPopulation(rep, n, st, metros) {
  const yrs = Array.isArray(n.growth_years) && n.growth_years.length === 2 ? n.growth_years : null;
  const since = yrs ? `（较 ${yrs[0]} 年）` : "";
  if (fin(n.growth)) rep.context.push(`${signedPct(n.growth)}${since}`);
  const emp = ratio(n.employed, n.population);
  if (emp != null) rep.context.push(`从业人员 ${compact(n.employed)}，占常住人口 ${pct(emp)}`);

  const g = (o) => o.growth;
  const row = (x, prefix = "") => ({
    label: `${prefix}${x.r.name}`, sub: x.r.kind === "city" ? x.r.sub : "", value: x.v, text: signedPct(x.v), tone: x.v >= 0 ? "good" : "bad",
  });
  const groups = [];
  if (fin(n.growth)) groups.push({ rows: [{ label: "全国", value: n.growth, text: signedPct(n.growth), tone: n.growth >= 0 ? "good" : "bad", strong: true }] });
  const extremes = (list, what) => {
    if (list.length < 2) return;
    const k = Math.min(3, Math.floor(list.length / 2));
    const top = list.slice(0, k), bottom = list.slice(-k).reverse();
    groups.push({ title: `增长最快的${what}`, rows: top.map((x) => row(x)) });
    groups.push({ title: bottom.every((x) => x.v < 0) ? `人口减少的${what}` : `增长最慢的${what}`, rows: bottom.map((x) => row(x)) });
  };
  extremes(sorted(st, g), "省份");
  extremes(sorted(metros, g, METRO_MIN), `城市（${compact(METRO_MIN)}人以上）`);
  if (groups.length) rep.breakdown = { title: yrs ? `${yrs[0]}–${yrs[1]} 年人口变化` : "人口变化", note: rep.scope.note, groups };

  // Size ranking: the stats-year population, else the 2020 census (marked), so an area with only a census figure
  // still takes its place instead of dropping out of the list.
  const size = (o) => (fin(o.population) ? o.population : fin(o.pop_census_2020) ? o.pop_census_2020 : null);
  const censusSub = (o, sub) => (fin(o.population) || !fin(o.pop_census_2020) ? sub : [sub, "七普 2020"].filter(Boolean).join(" · "));
  rep.boards.modes = [
    {
      id: "abs", label: "人口最多", hint: "按年末常住人口排列；该年数据暂缺的以七普 2020 人口代替并标注。",
      states: rank(st, size, { subOf: censusSub }), metros: rank(metros, size, { subOf: censusSub }),
    },
    {
      id: "rel", label: "增长最快", hint: `人口变化${since}；只比较常住人口 ${compact(METRO_MIN)} 以上的城市。`,
      states: rank(st, g, { fmt: signedPct, exact: (v) => signedPct(v, 2) }), metros: rank(metros, g, { fmt: signedPct, exact: (v) => signedPct(v, 2), min: METRO_MIN }),
    },
  ];
}

function reportUnemployment(rep, n, st, metros) {
  const u = (o) => o.unemployment;
  if (fin(n.unemployment) && n.unemployment > 0) rep.context.push(`劳动力中约每 ${Math.round(1 / n.unemployment)} 人有 1 人失业`);
  const ss = sorted(st, u);
  if (ss.length >= 2) rep.context.push(`${rep.scope.label}介于 ${pct(ss.at(-1).v)} 到 ${pct(ss[0].v)} 之间`);

  const tone = (v) => (!fin(n.unemployment) || Math.abs(v / n.unemployment - 1) < 0.03 ? null : v > n.unemployment ? "bad" : "good");
  const row = (label, x) => ({ label: `${label}: ${x.r.name}`, sub: x.r.kind === "city" ? x.r.sub : "", value: x.v, text: pct(x.v), tone: tone(x.v) });
  const spread = (list) => (list.length >= 3 ? [row("最高", list[0]), row("中位", list[Math.floor(list.length / 2)]), row("最低", list.at(-1))] : []);
  const groups = [];
  if (fin(n.unemployment)) groups.push({ rows: [{ label: "全国", value: n.unemployment, text: pct(n.unemployment), strong: true }] });
  const sRows = spread(ss), mRows = spread(sorted(metros, u, BIG_METRO_MIN));
  if (sRows.length) groups.push({ title: rep.scope.label, rows: sRows });
  if (mRows.length) groups.push({ title: `大城市（${compact(BIG_METRO_MIN)}人以上）`, rows: mRows });
  if (groups.length) rep.breakdown = { title: "分布", note: rep.scope.note, groups };

  const hint = `只比较常住人口 ${compact(BIG_METRO_MIN)} 以上的城市。`;
  const o = { fmt: pct, exact: (v) => pct(v, 2) };
  rep.boards.modes = [
    { id: "abs", label: "最高", hint, states: rank(st, u, o), metros: rank(metros, u, { ...o, min: BIG_METRO_MIN }) },
    { id: "rel", label: "最低", hint, states: rank(st, u, { ...o, asc: true }), metros: rank(metros, u, { ...o, asc: true, min: BIG_METRO_MIN }) },
  ];
}

function reportGold(rep, nation, n, st, metros) {
  const gdp = (o) => o.gdp ?? o.yields?.gold ?? null;
  const pp = ratio(rep.total, n.population);
  // GDP and population can come from different years: say which.
  const popYear = fin(n.population_year) ? n.population_year : fin(nation.year) ? nation.year : null;
  const years = n.gdp_year && popYear && n.gdp_year !== popYear ? `${n.gdp_year} 年 GDP ÷ ${popYear} 年常住人口` : "GDP ÷ 常住人口";
  if (pp != null) rep.context.push(`人均 ${money(pp)}（${years}）`);
  if (n.gdp_year) rep.context.push(`${n.gdp_year} 年，当年价格`);

  const rows = Object.entries(n.gdp_by_industry || {})
    .filter(([, v]) => fin(v))
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => {
      const { label, full } = industryLabel(nation, k, true);
      const s = ratio(v, rep.total);
      return { label, full, iconId: industryIcon(k), emoji: industryEmoji(k), value: v, text: money(v), sub: s != null ? pct(s) : "" };
    });
  if (rows.length) rep.breakdown = { title: "按产业分的 GDP", groups: [{ rows }], collapseAfter: GDP_ROWS };

  const perPerson = (o) => ratio(gdp(o), o.population);
  rep.boards.modes = [
    { id: "abs", label: "GDP 最高", states: rank(st, gdp, { fmt: money, exact: dollars }), metros: rank(metros, gdp, { fmt: money, exact: dollars }) },
    {
      id: "rel", label: "人均 GDP", hint: `${years}；只比较常住人口 ${compact(METRO_MIN)} 以上的城市。`,
      states: rank(st, perPerson, { fmt: money, exact: dollars }), metros: rank(metros, perPerson, { fmt: money, exact: dollars, min: METRO_MIN }),
    },
  ];
}

// A yield measured in money (value added, R&D spending): the total, its share of GDP, and the leaders.
function reportValue(rep, def, nation, n, st, metros) {
  const key = def.key;
  const share = ratio(rep.total, n.gdp);
  if (share != null) rep.context.push(`占 GDP 的 ${pct(share)}${n.gdp_year ? `（${n.gdp_year} 年）` : ""}`);
  const pp = ratio(rep.total, n.population);
  if (pp != null) rep.context.push(`人均 ${money(pp)}`);
  const y = (o) => o.yields?.[key];
  const sh = (o) => ratio(y(o), o.gdp ?? o.yields?.gold);
  rep.boards.modes = [
    { id: "abs", label: "最高", states: rank(st, y, { fmt: money, exact: dollars }), metros: rank(metros, y, { fmt: money, exact: dollars }) },
    {
      id: "rel", label: "占 GDP 比重", hint: `该项 ÷ 本地 GDP；只比较常住人口 ${compact(METRO_MIN)} 以上的城市。`,
      states: rank(st, sh, { fmt: (v) => pct(v), exact: (v) => pct(v, 2) }), metros: rank(metros, sh, { fmt: (v) => pct(v), exact: (v) => pct(v, 2), min: METRO_MIN }),
    },
  ];
}

function reportJobs(rep, def, nation, n, st, metros) {
  const key = def.key;
  const nShare = ratio(rep.total, n.employed);
  if (nShare != null) rep.context.push(`占 ${compact(n.employed)} 从业人员的 ${pct(nShare)}`);
  const per1k = ratio(rep.total, n.population);
  if (per1k != null) rep.context.push(`每千名常住人口 ${Math.round(per1k * 1000)} 人`);

  const inds = Array.isArray(def.yield?.industries) ? def.yield.industries : [];
  const gdpOf = inds.map((k) => n.gdp_by_industry?.[k]);
  if (inds.length && gdpOf.every(fin) && fin(n.gdp)) {
    const sum = gdpOf.reduce((a, b) => a + b, 0);
    rep.context.push(`${inds.length > 1 ? "这些行业" : "该行业"}创造 GDP ${money(sum)}（占 ${pct(sum / n.gdp)}${n.gdp_year ? `，${n.gdp_year} 年` : ""}）`);
  }
  // Worker counts and GDP may come from different publications: credit both.
  if (inds.some((k) => fin(n.gdp_by_industry?.[k])) && nation.gdp_vintage && nation.gdp_vintage !== rep.source) {
    rep.source = `${rep.source ? `${rep.source}（从业人员）；` : ""}${nation.gdp_vintage}（GDP）`;
  }
  const rows = inds
    .map((k) => {
      const v = n.jobs?.[k];
      if (!fin(v)) return null;
      const { label, full } = industryLabel(nation, k);
      const s = ratio(v, n.employed);
      const g = n.gdp_by_industry?.[k];
      const sub = [s != null ? `占全部从业人员 ${pct(s)}` : "", fin(g) ? `GDP ${money(g)}` : ""].filter(Boolean).join(" · ");
      return { label, full, iconId: industryIcon(k), emoji: industryEmoji(k), value: v, text: compact(v), sub };
    })
    .filter(Boolean)
    .sort((a, b) => b.value - a.value);
  if (rows.length) rep.breakdown = { title: "各行业从业人员", note: def.yield?.basis ? `口径：${def.yield.basis}` : "", groups: [{ rows }] };

  const y = (o) => o.yields?.[key];
  const lq = (o) => (nShare ? ratio(ratio(y(o), o.employed), nShare) : null);
  const shareSub = (o) => {
    const s = ratio(y(o), o.employed);
    return s != null ? `占从业 ${pct(s)}` : "";
  };
  const fmtLq = (v) => `×${v.toFixed(2)}`;
  const exactLq = (v) => `×${v.toFixed(3)}`;
  rep.boards.modes = [
    { id: "abs", label: "人数最多", states: rank(st, y), metros: rank(metros, y) },
    {
      id: "rel", label: "专业化程度",
      hint: `本地${def.label}从业占比 ÷ 全国占比（${pct(nShare)}）；只比较常住人口 ${compact(METRO_MIN)} 以上的城市。`,
      states: rank(st, lq, { fmt: fmtLq, exact: exactLq, subOf: shareSub }), metros: rank(metros, lq, { fmt: fmtLq, exact: exactLq, subOf: shareSub, min: METRO_MIN }),
    },
  ];
}

// ---------------------------------------------------------------------------------------------------------------
// DOM

const defIcon = (def, size) => (def.yield ? glyph(def.yield, size) : icon(def.iconId, def.emoji, { size }));

function barsHtml(bd, expanded) {
  const all = bd.groups.flatMap((g) => g.rows);
  const max = Math.max(0, ...all.map((r) => Math.abs(r.value)));
  let shown = 0;
  const limit = bd.collapseAfter && !expanded ? bd.collapseAfter : Infinity;
  const groups = bd.groups
    .map((g) => {
      const rows = g.rows
        .filter(() => shown++ < limit)
        .map((r) => {
          const w = max > 0 ? (Math.abs(r.value) / max) * 100 : 0;
          const ic = r.iconId || r.emoji ? `<span class="yr-bic" aria-hidden="true">${icon(r.iconId, r.emoji, { size: 15 })}</span>` : "";
          const tip = [r.full || r.label, r.sub, r.text].filter(Boolean).join(" · ");
          return `<div class="yr-bar${r.strong ? " strong" : ""}${ic ? "" : " noic"}" title="${esc(tip)}">
            ${ic}<span class="yr-bl">${esc(r.label)}${r.sub ? ` <span class="muted">${esc(r.sub)}</span>` : ""}</span>
            <span class="yr-bv${r.tone ? " " + r.tone : ""}">${esc(r.text)}</span>
            <span class="yr-track"><i class="${r.tone || ""}" style="width:${w.toFixed(1)}%"></i></span></div>`;
        })
        .join("");
      if (!rows) return "";
      return `${g.title ? `<h4>${esc(g.title)}</h4>` : ""}${rows}`;
    })
    .join("");
  const more = bd.collapseAfter && all.length > bd.collapseAfter
    ? `<button class="yr-more" data-more aria-expanded="${expanded}">${expanded ? "收起" : `显示全部 ${all.length} 项`}</button>` : "";
  return groups + more;
}

function boardHtml(rows) {
  if (!rows?.length) return `<p class="muted yr-empty">无数据。</p>`;
  const max = Math.max(0, ...rows.map((r) => Math.abs(r.value)));
  return `<ol class="yr-list">${rows
    .map((r) => {
      const w = max > 0 ? (Math.abs(r.value) / max) * 100 : 0;
      const tip = `打开${r.name}${r.exact && r.exact !== r.text ? `（${r.exact}）` : ""}`;
      return `<li><button class="yr-row" data-kind="${r.kind}" data-ref="${esc(r.ref)}" title="${esc(tip)}">
        <span class="yr-rk">${r.rank}</span>
        <span class="yr-nm">${esc(r.name)}${r.sub ? ` <span class="muted">${esc(r.sub)}</span>` : ""}</span>
        <span class="yr-v">${esc(r.text)}</span>
        <span class="yr-track"><i style="width:${w.toFixed(1)}%"></i></span></button></li>`;
    })
    .join("")}</ol>`;
}

/**
 * Wire the top-bar buttons (rendered by topbar.js with data-report="<key>") to the dropdown #yield-report.
 * opts: {nation, states, cities, lenses (array), lensVintage, onState(index), onCity(city), onLens(lensId)}
 */
export function initYieldReports(opts) {
  const bar = document.getElementById("yields");
  const pop = document.getElementById("yield-report");
  const none = { open() {}, close() {}, toggle() {}, get isOpen() { return false; }, build: () => null };
  if (!bar || !pop) return none;
  const data = { nation: opts.nation, states: opts.states, cities: opts.cities, lenses: opts.lenses, lensVintage: opts.lensVintage };
  const cityById = new Map((opts.cities || []).map((c) => [String(c.id), c]));
  const cache = new Map();
  const build = (key) => {
    if (!cache.has(key)) cache.set(key, buildReport(key, data));
    return cache.get(key);
  };
  let current = null; // {key, btn, rep}
  let tab = opts.states?.length ? "states" : "metros"; // remembered across reports
  let mode = "abs";
  let expanded = false;

  const buttons = () => [...bar.querySelectorAll("[data-report]")];
  const isMobile = () => matchMedia("(max-width: 700px)").matches;

  function position() {
    if (!current) return;
    const top = document.getElementById("topbar")?.getBoundingClientRect().bottom ?? 40;
    pop.style.top = Math.round(top) + (isMobile() ? 0 : 6) + "px";
    if (isMobile()) {
      pop.style.left = "";
      return;
    }
    const r = current.btn.getBoundingClientRect();
    const w = pop.offsetWidth || 440;
    const left = Math.max(8, Math.min(innerWidth - w - 8, r.left - 14));
    pop.style.left = Math.round(left) + "px";
  }

  function render() {
    const { rep } = current;
    const def = rep.def;
    const modes = rep.boards.modes;
    if (!modes.some((m) => m.id === mode)) mode = modes[0]?.id || "abs";
    const m = modes.find((x) => x.id === mode);
    const hasStates = modes.some((x) => x.states.length), hasMetros = modes.some((x) => x.metros.length);
    if (tab === "states" && !hasStates && hasMetros) tab = "metros";
    pop.style.setProperty("--yc", def.color || "var(--gold-bright)");
    pop.innerHTML = `
      <div class="yr-head">
        <span class="yr-ic" style="color:${esc(def.color)}">${defIcon(def, 26)}</span>
        <div class="yr-titles"><div class="yr-kind">全国报告</div><h2 id="yr-title">${esc(def.name)}</h2></div>
        <button class="icon-btn yr-close" aria-label="关闭报告（Esc）" title="关闭（Esc）">${icon("close", "✕", { size: 12 })}</button>
      </div>
      <div class="yr-total"><b style="color:${esc(def.color)}">${esc(rep.totalText)}</b>
        ${rep.context.length ? `<ul class="yr-ctx">${rep.context.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}</div>
      ${rep.definition ? `<p class="yr-def">${esc(rep.definition)}</p>` : ""}
      ${rep.source ? `<div class="yr-src">来源：${esc(rep.source)}</div>` : ""}
      ${rep.breakdown ? `<section class="yr-sec"><h3>${esc(rep.breakdown.title)}</h3>
        ${rep.breakdown.note ? `<p class="yr-note">${esc(rep.breakdown.note)}</p>` : ""}
        <div class="yr-bars">${barsHtml(rep.breakdown, expanded)}</div></section>` : ""}
      ${modes.length ? `<section class="yr-sec"><h3>排行榜</h3>
        <div class="yr-controls">
          <div class="yr-tabs" role="tablist" aria-label="地区">
            <button role="tab" id="yr-tab-states" data-tab="states" aria-controls="yr-board" aria-selected="${tab === "states"}" tabindex="${tab === "states" ? 0 : -1}" ${hasStates ? "" : "disabled"}>省份</button>
            <button role="tab" id="yr-tab-metros" data-tab="metros" aria-controls="yr-board" aria-selected="${tab === "metros"}" tabindex="${tab === "metros" ? 0 : -1}" ${hasMetros ? "" : "disabled"}>城市</button>
          </div>
          ${modes.length > 1 ? `<div class="yr-modes" role="group" aria-label="排名方式">${modes
            .map((x) => `<button data-mode="${x.id}" aria-pressed="${x.id === mode}">${esc(x.label)}</button>`).join("")}</div>` : ""}
        </div>
        <p class="yr-note yr-hint">${esc(hintText(m))}</p>
        <div id="yr-board" role="tabpanel" aria-labelledby="yr-tab-${tab}">${boardHtml(m?.[tab])}</div></section>` : ""}
      ${rep.lens ? `<div class="yr-foot"><p class="yr-note yr-lens-note">相关指标（并非本项总量）：${esc(rep.lens.unit || rep.lens.name)}${rep.lens.vintage ? `（${esc(rep.lens.vintage)}）` : ""}。</p>
        <button class="btn yr-lens" data-lens="${esc(rep.lens.id)}">${icon("layers", "🗺️", { size: 14 })}相关地图：${esc(rep.lens.name)}透镜</button></div>` : ""}
      ${advisorButtons(rep.key)}`;
  }

  // "Open advisor" buttons (ext/advisors): a full dashboard on the report's subject.
  function advisorButtons(key) {
    const list = advisorApp() ? ADVISOR_FOR[key] || [] : [];
    if (!list.length) return "";
    return `<div class="yr-foot yr-adv">${list.map((a) => `<button class="btn yr-advisor" data-advisor="${esc(a.id)}"${a.industry ? ` data-industry="${esc(a.industry)}"` : ""}
      title="打开${esc(a.label.replace(/（.*）$/, ""))}顾问：图表、表格与地图联动">${icon("g-people", "👥", { size: 14 })}打开顾问：${esc(a.label)}</button>`).join("")}</div>`;
  }

  // The ranking's hint, plus (on the States tab) which states are ranked.
  function hintText(m) {
    const parts = [m?.hint || ""];
    if (tab === "states" && current.rep.boards.statesNote) parts.push(current.rep.boards.statesNote);
    return parts.filter(Boolean).join(" ");
  }

  // Re-render the leaderboard only (keeps focus on the control that changed it).
  function renderBoard() {
    const m = current.rep.boards.modes.find((x) => x.id === mode);
    pop.querySelectorAll("[data-tab]").forEach((b) => {
      const on = b.dataset.tab === tab;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
    });
    pop.querySelectorAll("[data-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
    const hint = pop.querySelector(".yr-hint");
    if (hint) hint.textContent = hintText(m);
    const board = pop.querySelector("#yr-board");
    if (board) {
      board.setAttribute("aria-labelledby", `yr-tab-${tab}`);
      board.innerHTML = boardHtml(m?.[tab]);
    }
  }

  function open(key, btn) {
    const rep = build(key);
    if (!rep) return;
    if (current && current.btn !== btn) current.btn.setAttribute("aria-expanded", "false");
    if (!current || current.key !== key) {
      expanded = false;
      mode = "abs"; // each report opens on its main ranking; the States / Metros tab is remembered
    }
    current = { key, btn, rep };
    for (const b of buttons()) b.classList.toggle("open", b === btn);
    btn.setAttribute("aria-expanded", "true");
    pop.setAttribute("aria-labelledby", "yr-title");
    render();
    pop.hidden = false;
    pop.scrollTop = 0;
    position();
    // Keep the anchor visible when the bar has scrolled (narrow screens).
    btn.scrollIntoView?.({ block: "nearest", inline: "nearest" });
    pop.focus({ preventScroll: true });
  }

  function close(restoreFocus = true) {
    if (!current) return;
    const { btn } = current;
    btn.setAttribute("aria-expanded", "false");
    btn.classList.remove("open");
    current = null;
    pop.hidden = true;
    pop.innerHTML = "";
    if (restoreFocus && document.contains(btn)) btn.focus();
  }

  bar.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-report]");
    if (!btn) return;
    if (current?.btn === btn) close();
    else open(btn.dataset.report, btn);
  });

  pop.addEventListener("click", (e) => {
    const t = e.target.closest("button");
    if (!t || !current) return;
    if (t.classList.contains("yr-close")) return close();
    if (t.dataset.tab) {
      tab = t.dataset.tab;
      return renderBoard();
    }
    if (t.dataset.mode) {
      mode = t.dataset.mode;
      return renderBoard();
    }
    if (t.hasAttribute("data-more")) {
      expanded = !expanded;
      const bars = pop.querySelector(".yr-bars");
      if (bars) bars.innerHTML = barsHtml(current.rep.breakdown, expanded);
      pop.querySelector("[data-more]")?.focus();
      return;
    }
    if (t.dataset.advisor) {
      const app = advisorApp();
      const params = { id: t.dataset.advisor, ...(t.dataset.industry ? { industry: t.dataset.industry } : {}) };
      close(false);
      return app?.setMode("advisor", params);
    }
    if (t.dataset.lens) {
      const id = t.dataset.lens;
      close();
      return opts.onLens?.(id);
    }
    if (t.dataset.kind === "state") {
      close(false);
      return opts.onState?.(Number(t.dataset.ref));
    }
    if (t.dataset.kind === "city") {
      const city = cityById.get(t.dataset.ref);
      if (!city) return close();
      close(false);
      return opts.onCity?.(city);
    }
  });

  // The report is a non-modal popover that logically follows its stat button: Shift+Tab from its first control
  // goes back to the button, Tab past its last control closes it and moves on to whatever follows the button.
  const FOCUSABLE = "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";
  const visible = (el) => el.getClientRects().length > 0 && !el.closest("[hidden]");
  pop.addEventListener("keydown", (e) => {
    if (e.key !== "Tab" || e.altKey || e.ctrlKey || e.metaKey || !current) return;
    const inside = [...pop.querySelectorAll(FOCUSABLE)].filter(visible);
    const first = inside[0], last = inside.at(-1);
    if (e.shiftKey && (e.target === pop || e.target === first)) {
      e.preventDefault();
      current.btn.focus();
    } else if (!e.shiftKey && (e.target === last || !inside.length)) {
      const all = [...document.querySelectorAll(FOCUSABLE)].filter((el) => visible(el) && !pop.contains(el));
      const next = all[all.indexOf(current.btn) + 1];
      e.preventDefault();
      close(!next);
      next?.focus();
    }
  });
  // From the button, Tab goes into the open report rather than to the next stat.
  bar.addEventListener("keydown", (e) => {
    if (e.key !== "Tab" || e.shiftKey || e.altKey || e.ctrlKey || e.metaKey || !current || e.target !== current.btn) return;
    const first = [...pop.querySelectorAll(FOCUSABLE)].find(visible);
    if (!first) return;
    e.preventDefault();
    first.focus();
  });
  // Focus moving anywhere else (search via "/" or Cmd+K, the share button, Tab elsewhere) closes the report, so
  // only one popover is ever open.
  document.addEventListener("focusin", (e) => {
    if (current && !pop.contains(e.target) && e.target !== current.btn) close(false);
  });

  // Tabs: arrow keys move between them (WAI-ARIA tabs pattern).
  pop.addEventListener("keydown", (e) => {
    const t = e.target.closest?.("[role=tab]");
    if (!t || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    const tabs = [...pop.querySelectorAll("[role=tab]:not([disabled])")];
    let i = tabs.indexOf(t);
    if (i < 0 || tabs.length < 2) return;
    i = e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : (i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    e.preventDefault();
    tab = tabs[i].dataset.tab;
    renderBoard();
    tabs[i].focus();
  });

  document.addEventListener("pointerdown", (e) => {
    if (current && !pop.contains(e.target) && !e.target.closest?.("[data-report]")) close(false);
  });
  window.addEventListener("resize", position);
  bar.addEventListener("scroll", position, { passive: true });
  document.getElementById("topbar")?.addEventListener("scroll", position, { passive: true });

  return {
    open(key) {
      const btn = buttons().find((b) => b.dataset.report === key);
      if (btn) open(key, btn);
    },
    close,
    toggle(key) {
      if (current?.key === key) close();
      else this.open(key);
    },
    build,
    get isOpen() { return !!current; },
    get key() { return current?.key ?? null; },
  };
}
