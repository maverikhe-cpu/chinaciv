import { css } from "../color.js";
import { esc, int } from "../format.js";
import { isCapital } from "../world.js";
import { yieldGrid, amenities, industries, gdpRow, sectors, popLine, growthSpan, showPanel, hidePanel } from "./infoPanel.js";

// A city's provinces: cities.json `states` holds province codes (chinaciv) or postal codes (usaciv test data).
export function provinceIndex(w, ref) {
  return w.raw.states.findIndex((s) => s.fips === ref || s.abbr === ref);
}

/**
 * ctx: {w, nation, onClose, onPedia, onState(stateIndex)}
 */
export function openCityPanel(city, ctx) {
  const { w, nation } = ctx;
  const n = nation.nation;
  const owner = city.cell != null ? w.raw.owner[city.cell] : -1;
  const col = css(w.stateColor[owner] || [190, 190, 190]);

  // The city's province(s) as links to their panels.
  const states = (city.states || [])
    .map((ref) => ({ ref, i: provinceIndex(w, ref) }))
    .map(({ ref, i }) => {
      const st = i >= 0 ? w.raw.states[i] : null;
      const label = st ? st.full || st.name : ref;
      return st && ctx.onState
        ? `<button class="chip" data-state="${i}" title="打开${esc(st.full || st.name)}">${esc(label)}</button>`
        : `<span class="chip">${esc(label)}</span>`;
    })
    .join("");

  const growth = growthSpan(city, n);
  const econ = gdpRow(city) + amenities(city, n);
  const sec = sectors(city);
  const srcs = [nation.vintage, nation.gdp_vintage && nation.gdp_vintage !== nation.vintage ? `GDP：${nation.gdp_vintage}` : ""].filter(Boolean);

  showPanel({
    kind: "city",
    kindLabel: city.kind_label || "城市",
    stripe: col,
    title: city.name,
    titleHtml: `${isCapital(city) ? `<span class="cap" title="首都">★</span> ` : ""}${esc(city.name)}`,
    sub: `${esc(city.full && city.full !== city.name ? city.full : "")}${states ? `<div class="chips">${states}</div>` : ""}`,
    headExtra: city.population != null
      ? popLine(city, growth)
      : city.pop_census_2020 != null
        ? `<div class="cp-pop">常住人口（${esc(nation.census_year || 2020)} 年七普）<b>${int(city.pop_census_2020)}</b><span class="muted">　${esc(nation.year || "")} 年数据暂缺</span></div>`
        : `<div class="cp-pop muted">常住人口：无数据</div>`,
    sections: [
      { title: "产出", html: `<div class="yield-grid">${yieldGrid(nation, city.yields)}</div>` },
      econ && { title: "经济", html: econ },
      sec && { title: "三次产业", html: sec },
      city.jobs && { title: "就业结构 · 各行业从业人员", html: industries(city, nation) },
      city.note && { title: "数据说明", html: `<p class="muted">${esc(city.note)}</p>` },
    ],
    foot: `来源：${esc(srcs.join("；"))}。${city.jobs ? "绿色 ×N 表示该行业就业占比为全国的 N 倍。" : ""}`,
    onClose: ctx.onClose,
    onPedia: ctx.onPedia,
    wire(panel) {
      panel.querySelectorAll("[data-state]").forEach((b) => (b.onclick = () => ctx.onState(+b.dataset.state)));
    },
  });
}

export const closeCityPanel = hidePanel;
