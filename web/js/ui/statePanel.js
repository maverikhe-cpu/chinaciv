import { css } from "../color.js";
import { esc, int, popShort } from "../format.js";
import { isCapital } from "../world.js";
import { yieldGrid, amenities, industries, gdpRow, sectors, popLine, growthSpan, showPanel } from "./infoPanel.js";

/**
 * Opens the panel for state `idx` (index into world.json states / states.json).
 * ctx: {w, nation, states (states.json or null), cities, onClose, onPedia, onCity(city)}
 */
export function openStatePanel(idx, ctx) {
  const { w, nation } = ctx;
  const n = nation.nation;
  const geo = w.raw.states[idx] || {};
  // states.json is in the same order as world.json states; fall back to matching by fips.
  let st = ctx.states?.[idx];
  if (st && geo.fips && st.fips && st.fips !== geo.fips) st = ctx.states.find((s) => s.fips === geo.fips);
  st = st || { ...geo };
  const name = st.name || geo.name || "省份";
  const abbr = st.abbr || geo.abbr || "";
  const code = st.fips || geo.fips || "";
  // Ranks are among the provinces that have a value; the denominator is the number ranked for that yield.
  const count = ctx.states?.length || w.raw.states.length;

  // cities.json `states` holds province codes (chinaciv); postal codes in the usaciv test data.
  const inProv = (c) => (c.states || []).some((r) => (code && r === code) || (abbr && r === abbr));
  // Largest first by the stats-year population, else the 2020 census (shown dimmed and marked), like the banners.
  const sizeOf = (c) => c.population ?? c.pop_census_2020 ?? -1;
  const all = (ctx.cities || []).filter(inProv).sort((a, b) => sizeOf(b) - sizeOf(a));
  const cities = all.slice(0, 10);
  const cityRows = cities.length
    ? `<div class="city-list">${cities
        .map((c) => {
          const multi = c.kind_label ? `<span class="muted"> · ${esc(c.kind_label)}</span>` : "";
          return `<button class="city-row" data-city="${esc(c.id)}" title="${esc(c.full || c.name)}">
            <span class="cr-name">${isCapital(c) ? "★ " : ""}${esc(c.name)}${multi}</span>
            <span class="cr-pop">${c.population == null && c.pop_census_2020 != null
              ? `<span class="muted" title="${esc(nation.year || "")} 年数据暂缺，此为七普 2020 人口">${popShort(c.pop_census_2020)}*</span>`
              : popShort(c.population)}</span><span class="cr-go" aria-hidden="true">›</span></button>`;
        })
        .join("")}</div>${all.length > cities.length ? `<p class="muted">共 ${all.length} 个地级行政区，按常住人口列出前 ${cities.length} 个。</p>` : ""}${
        cities.some((c) => c.population == null && c.pop_census_2020 != null) ? `<p class="muted">* ${esc(nation.year || "")} 年数据暂缺，按七普 2020 人口排列。</p>` : ""}`
    : `<p class="muted">数据中该省份没有地级城市。</p>`;

  const hasEcon = st.population != null || st.yields;
  const full = st.full || geo.full || "";
  const kindLabel = st.kind_label || geo.kind_label || "省级行政区";
  const econ = gdpRow(st) + amenities(st, n);
  const sec = sectors(st);
  showPanel({
    kind: "state",
    kindLabel,
    stripe: css(w.stateColor[idx] || [190, 190, 190]),
    title: name,
    sub: [full && full !== name ? esc(full) : "", abbr && abbr !== name ? `简称“${esc(abbr)}”` : "", code ? `代码 ${esc(code)}` : ""].filter(Boolean).join(" · "),
    headExtra: st.population != null
      ? popLine(st, growthSpan(st, n))
      : st.pop_census_2020 != null
        ? `<div class="cp-pop">常住人口（${esc(nation.census_year || 2020)} 年七普）<b>${int(st.pop_census_2020)}</b><span class="muted">　${esc(nation.year || "")} 年数据暂缺</span></div>`
        : hasEcon ? `<div class="cp-pop muted">常住人口：无数据</div>` : "",
    sections: [
      st.yields && { title: "产出 · 全国排名", html: `<div class="yield-grid">${yieldGrid(nation, st.yields, st.ranks, count)}</div>` },
      econ && { title: "经济", html: econ },
      sec && { title: "三次产业", html: sec },
      st.jobs && { title: "主要行业 · 从业人员", html: industries(st, nation, 8) },
      { title: "主要城市", html: cityRows },
      st.note && { title: "数据说明", html: `<p class="muted">${esc(st.note)}</p>` },
      !hasEcon && { title: "数据", html: `<p class="muted">本次数据构建没有省级统计（缺少 states.json）。</p>` },
    ],
    foot: `来源：${esc(nation.vintage || "")}${nation.gdp_vintage ? `；GDP：${esc(nation.gdp_vintage)}` : ""}。排名在有数据的省级行政区之间比较（1 为最大）。`,
    onClose: ctx.onClose,
    onPedia: ctx.onPedia,
    wire(panel) {
      panel.querySelectorAll("[data-city]").forEach((b) => {
        b.onclick = () => {
          const c = ctx.cities.find((x) => x.id === b.dataset.city);
          if (c) ctx.onCity(c);
        };
      });
    },
  });
}
