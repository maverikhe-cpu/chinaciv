// China map furniture required by the standard map (docs/CN_PLAN.md §2.2–2.3), as two built-in static layers:
//
//   cn-dashes (z 52)  the parts of the 南海断续线 inside the main frame (world.json `dash_line`) and the labelled
//                     islands that are too small for a hex (钓鱼岛, 赤尾屿, 东沙群岛, 西沙群岛, 三沙市 seat: `islands`)
//   cn-inset  (z 92)  the 南海诸岛 inset in the lower-right corner (world.json `inset`): an opaque framed box with the
//                     South China Sea land (China in its province colours, neighbours in grey), all ten dashes, island
//                     group labels and its title. Vector only: the hexes under the box stay ordinary water.
//
// Both are registered by main.js (not an extension: the map must never be drawn without them). They are data
// driven: a world.json without `dash_line` / `islands` / `inset` (e.g. the US test data) draws nothing.
import { css, mix } from "../color.js";
import { SERIF_CN, SANS, esc } from "../format.js";

const DASH_FILL = "#f2d58a";
const DASH_CASE = "rgba(28,16,6,.8)";
const LAND_CN = [222, 204, 160];
const LAND_FOREIGN = "#7d838a";
const SEA = "#2b5a82";
const SEA_DEEP = "#1d4468";
const FRAME = "#c9a45c";
const FRAME_DARK = "#2a1d0a";

const flatOk = (p) => Array.isArray(p) && p.length >= 4 && p.every(Number.isFinite);

function tracePts(ctx, pts, close) {
  ctx.moveTo(pts[0], pts[1]);
  for (let k = 2; k < pts.length; k += 2) ctx.lineTo(pts[k], pts[k + 1]);
  if (close) ctx.closePath();
}

// A dash: 2-point lines (or open polylines) are stroked; closed rings (DataV draws the dashes as thin polygons) are
// filled and outlined. `s` = css px per km; widths in css px.
function drawDash(ctx, pts, s, widthPx) {
  if (!flatOk(pts)) return;
  const n = pts.length / 2;
  const closed = n >= 4 && Math.hypot(pts[0] - pts[pts.length - 2], pts[1] - pts[pts.length - 1]) < 1e-6;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.beginPath();
  tracePts(ctx, pts, closed);
  if (closed) {
    ctx.lineWidth = (widthPx * 0.9) / s;
    ctx.strokeStyle = DASH_CASE;
    ctx.stroke();
    ctx.fillStyle = DASH_FILL;
    ctx.fill();
    ctx.lineWidth = (widthPx * 0.5) / s;
    ctx.strokeStyle = DASH_FILL;
    ctx.stroke();
  } else {
    ctx.lineWidth = (widthPx + 2) / s;
    ctx.strokeStyle = DASH_CASE;
    ctx.stroke();
    ctx.lineWidth = widthPx / s;
    ctx.strokeStyle = DASH_FILL;
    ctx.stroke();
  }
}

// A label with a dark halo, in screen px at world point (x, y) (ctx in world transform; o.toScreen / o.screen()).
// Inset label placement by id (dx in css px, dy in label heights).
const INSET_PLACE = {
  xisha: { align: "right", dx: -6, dy: -0.75 },
  zhongsha: { align: "left", dx: 6, dy: 0.75 },
  460300: { align: "right", dx: -5, dy: 0.6 },
};

function label(ctx, o, x, y, text, { px = 11, color = "#f6ead0", dx = 0, dy = 0, align = "left", font = SERIF_CN, weight = "" } = {}) {
  const [sx, sy] = o.toScreen(x, y);
  ctx.save();
  o.screen();
  ctx.font = `${weight ? weight + " " : ""}${px.toFixed(1)}px ${font}`;
  ctx.textAlign = align;
  ctx.textBaseline = "middle";
  ctx.lineJoin = "round";
  ctx.lineWidth = Math.max(2, px * 0.28);
  ctx.strokeStyle = "rgba(14,10,5,.78)";
  ctx.strokeText(text, sx + dx, sy + dy);
  ctx.fillStyle = color;
  ctx.fillText(text, sx + dx, sy + dy);
  ctx.restore();
}

function islandMark(ctx, o, x, y, kind, r = 2.2) {
  const [sx, sy] = o.toScreen(x, y);
  ctx.save();
  o.screen();
  ctx.beginPath();
  if (kind === "city") ctx.rect(sx - r, sy - r, 2 * r, 2 * r);
  else ctx.arc(sx, sy, kind === "island_group" ? r * 0.8 : r, 0, Math.PI * 2);
  ctx.fillStyle = kind === "shoal" ? "#9ccbe8" : "#f6ead0";
  ctx.fill();
  ctx.lineWidth = 1;
  ctx.strokeStyle = "rgba(14,10,5,.85)";
  ctx.stroke();
  ctx.restore();
}

// A group without island outlines in the data (e.g. 中沙群岛, mostly submerged): a small cluster of dots.
function islandCluster(ctx, o, x, y, r = 1.3) {
  const [sx, sy] = o.toScreen(x, y);
  ctx.save();
  o.screen();
  ctx.beginPath();
  for (const [dx, dy] of [[-3, -1], [0, 2], [3, -1.5], [1, -3.5]]) {
    ctx.moveTo(sx + dx + r, sy + dy);
    ctx.arc(sx + dx, sy + dy, r, 0, Math.PI * 2);
  }
  ctx.fillStyle = "#f6ead0";
  ctx.fill();
  ctx.lineWidth = 0.7;
  ctx.strokeStyle = "rgba(14,10,5,.85)";
  ctx.stroke();
  ctx.restore();
}

const inBox = (b, x, y) => Array.isArray(b) && x >= b[0] && x <= b[2] && y >= b[1] && y <= b[3];

/** The static layers for the China map furniture; [] when world.json has none of it. */
export function chinaMapLayers(w) {
  const raw = w.raw || {};
  const layers = [];
  const box = raw.inset && Array.isArray(raw.inset.box) && raw.inset.box.length === 4 ? raw.inset.box : null;
  const dashes = Array.isArray(raw.dash_line) ? raw.dash_line.filter(flatOk) : [];
  const islands = Array.isArray(raw.islands) ? raw.islands.filter((p) => Number.isFinite(p?.x) && Number.isFinite(p?.y)) : [];

  if (dashes.length || islands.length) {
    layers.push({
      id: "cn-dashes",
      z: 52,
      sig: () => "1",
      draw(ctx, vp, _w, o) {
        const s = o.s;
        ctx.save();
        if (box) {
          // Nothing of the main map's dashes under the inset (the builder already clips; this is a safety net).
          ctx.beginPath();
          ctx.rect(-1e5, -1e5, 2e5 + raw.width, 2e5 + raw.height);
          ctx.rect(box[0], box[1], box[2] - box[0], box[3] - box[1]);
          ctx.clip("evenodd");
        }
        const wpx = Math.max(1.6, Math.min(4.5, o.Rpx * 0.16));
        for (const d of dashes) drawDash(ctx, d, s, wpx);
        ctx.restore();
        if (o.Rpx < 3.2) return;
        const px = Math.max(9.5, Math.min(14, 7 + o.Rpx * 0.25));
        for (const p of islands) {
          if (box && inBox(box, p.x, p.y)) continue;
          islandMark(ctx, o, p.x, p.y, p.kind, Math.max(1.8, Math.min(3.5, o.Rpx * 0.1)));
          label(ctx, o, p.x, p.y, p.name, { px, dx: 5 });
        }
      },
      hit(x, y, Rpx) {
        const r = 10 / (Rpx / (w.grid.R || 25)); // ~10 css px in km
        for (const p of islands) if (Math.hypot(p.x - x, p.y - y) <= r) return p;
        return null;
      },
      tooltip: (p) => `<b>${esc(p.name)}</b><br><span class="muted">${p.kind === "island_group" ? "群岛" : p.kind === "shoal" ? "暗沙" : p.kind === "city" ? "地级市驻地" : "岛屿"} · 中国领土</span>`,
    });
  }

  if (box) {
    const inset = raw.inset;
    const land = Array.isArray(inset.land) ? inset.land.filter((l) => flatOk(l?.pts)) : [];
    const idash = Array.isArray(inset.dashes) ? inset.dashes.filter(flatOk) : [];
    const ilabels = [...(inset.islands || []), ...(inset.labels || [])].filter((p) => Number.isFinite(p?.x) && Number.isFinite(p?.y) && p.name);
    const colorOf = (l) => {
      if (l.kind !== "china") return LAND_FOREIGN;
      const k = (raw.states || []).findIndex((st) => st.fips === l.code);
      const sc = k >= 0 ? w.stateColor?.[k] : null;
      return css(sc ? mix(LAND_CN, sc, 0.38) : LAND_CN);
    };
    const [x0, y0, x1, y1] = box;
    const bw = x1 - x0, bh = y1 - y0;
    // Small Chinese island rings (the 东沙/西沙/中沙/南沙 groups, 黄岩岛 …): centre and size, so each can be drawn as a
    // marker of at least ~1.5 css px when its true outline would be smaller than a pixel.
    const isles = [];
    for (const l of land) {
      if (l.kind !== "china") continue;
      let ax = Infinity, ay = Infinity, bx = -Infinity, by = -Infinity;
      for (let k = 0; k < l.pts.length; k += 2) {
        ax = Math.min(ax, l.pts[k]); bx = Math.max(bx, l.pts[k]);
        ay = Math.min(ay, l.pts[k + 1]); by = Math.max(by, l.pts[k + 1]);
      }
      const size = Math.max(bx - ax, by - ay);
      if (size < 8 && inBox(box, (ax + bx) / 2, (ay + by) / 2)) isles.push({ x: (ax + bx) / 2, y: (ay + by) / 2, size });
    }
    layers.push({
      id: "cn-inset",
      z: 92,
      sig: () => "1",
      draw(ctx, vp, _w, o) {
        if (vp.x1 < x0 - 40 || vp.x0 > x1 + 40 || vp.y1 < y0 - 40 || vp.y0 > y1 + 40) return;
        const s = o.s;
        // drop shadow + sea
        ctx.save();
        ctx.shadowColor = "rgba(0,0,0,.55)";
        ctx.shadowBlur = 14;
        ctx.shadowOffsetY = 3;
        ctx.fillStyle = SEA_DEEP;
        ctx.fillRect(x0, y0, bw, bh);
        ctx.restore();
        const g = ctx.createLinearGradient(x0, y0, x1, y1);
        g.addColorStop(0, SEA);
        g.addColorStop(1, SEA_DEEP);
        ctx.fillStyle = g;
        ctx.fillRect(x0, y0, bw, bh);
        // land, clipped to the box
        ctx.save();
        ctx.beginPath();
        ctx.rect(x0, y0, bw, bh);
        ctx.clip();
        for (const l of land) {
          ctx.beginPath();
          tracePts(ctx, l.pts, true);
          ctx.fillStyle = colorOf(l);
          ctx.fill();
          ctx.lineWidth = 0.8 / s;
          ctx.strokeStyle = l.kind === "china" ? "rgba(60,40,18,.7)" : "rgba(30,34,40,.6)";
          ctx.stroke();
        }
        // island markers: every small island ring at least ISLE_MIN_PX across (a dot cluster for each group)
        const ISLE_MIN_PX = Math.max(1.8, Math.min(3.6, (bw * s) / 150));
        ctx.beginPath();
        let nIsle = 0;
        for (const p of isles) {
          if (p.size * s >= ISLE_MIN_PX * 1.4) continue; // big enough to show as its own outline
          const r = ISLE_MIN_PX / 2 / s;
          ctx.moveTo(p.x + r, p.y);
          ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
          nIsle++;
        }
        if (nIsle) {
          ctx.lineWidth = 1.4 / s;
          ctx.strokeStyle = "rgba(30,18,6,.9)";
          ctx.stroke();
          ctx.fillStyle = css(mix(LAND_CN, [255, 255, 255], 0.35));
          ctx.fill();
        }
        const wpx = Math.max(1.3, Math.min(3.5, o.Rpx * 0.12));
        for (const d of idash) drawDash(ctx, d, s, wpx);
        ctx.restore();
        // frame: dark outer line, gold band, thin inner line
        ctx.lineJoin = "miter";
        ctx.strokeStyle = FRAME_DARK;
        ctx.lineWidth = 4 / s;
        ctx.strokeRect(x0, y0, bw, bh);
        ctx.strokeStyle = FRAME;
        ctx.lineWidth = 2.2 / s;
        ctx.strokeRect(x0, y0, bw, bh);
        const inset4 = 4 / s;
        ctx.strokeStyle = "rgba(236,208,142,.55)";
        ctx.lineWidth = 0.8 / s;
        ctx.strokeRect(x0 + inset4, y0 + inset4, bw - 2 * inset4, bh - 2 * inset4);
        // labels (screen px, scaled with the box but kept readable)
        const boxPx = bw * s;
        if (boxPx >= 70) {
          const lp = Math.max(8, Math.min(15, boxPx / 22));
          for (const p of ilabels) {
            const group = p.kind === "island_group";
            if (!group) islandMark(ctx, o, p.x, p.y, p.kind, Math.max(1.5, lp * 0.18));
            else if (!isles.some((q) => Math.hypot(q.x - p.x, q.y - p.y) < 25)) islandCluster(ctx, o, p.x, p.y);
            // 西沙群岛, 中沙群岛 and the 三沙市 seat sit close together: fixed offsets keep their labels apart.
            const at = INSET_PLACE[p.id] || {};
            label(ctx, o, p.x, p.y, p.name, {
              px: group ? lp * 1.02 : lp * 0.9, dx: at.dx ?? (group ? 8 : 4), dy: (at.dy ?? 0) * lp,
              align: at.align ?? "left", color: group ? "#fff2cf" : "#f1e4c3",
            });
          }
        }
        // title plaque, lower right inside the frame
        const title = inset.title || "南海诸岛";
        const tp = Math.max(9, Math.min(22, boxPx / 13));
        const [bx, by] = o.toScreen(x1, y1);
        ctx.save();
        o.screen();
        ctx.font = `600 ${tp.toFixed(1)}px ${SERIF_CN}`;
        if ("letterSpacing" in ctx) ctx.letterSpacing = `${(tp * 0.15).toFixed(1)}px`;
        const tw = ctx.measureText(title).width + tp * 0.9, th = tp * 1.55;
        const px0 = bx - tw - 6, py0 = by - th - 6;
        ctx.fillStyle = "rgba(18,13,6,.88)";
        ctx.fillRect(px0, py0, tw, th);
        ctx.strokeStyle = FRAME;
        ctx.lineWidth = 1;
        ctx.strokeRect(px0 + 0.5, py0 + 0.5, tw - 1, th - 1);
        ctx.fillStyle = "#ecd08e";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(title, px0 + tw / 2 + tp * 0.07, py0 + th / 2 + 0.5);
        if ("letterSpacing" in ctx) ctx.letterSpacing = "0px";
        if (boxPx >= 150 && Number.isFinite(inset.scale)) {
          ctx.font = `${Math.max(8, tp * 0.55).toFixed(1)}px ${SANS}`;
          ctx.fillStyle = "rgba(236,226,200,.8)";
          ctx.textAlign = "left";
          const [lx, ly] = o.toScreen(x0, y1);
          ctx.fillText(`比例为主图的 ${Math.round(inset.scale * 100)}%`, lx + 8, ly - 10);
        }
        ctx.restore();
      },
      hit: (x, y) => (inBox(box, x, y) ? { inset: true } : null),
      tooltip: () => `<b>${esc(inset.title || "南海诸岛")}</b><br><span class="muted">附图：南海诸岛及断续线（缩小绘制，仅示意位置）</span>`,
      click: () => true, // the box is furniture: a click inside it selects nothing underneath
    });
  }
  return layers;
}

/** The inset box (map km) or null: the camera's home view and the minimap keep it in frame. */
export const insetBox = (world) => (world?.inset && Array.isArray(world.inset.box) && world.inset.box.length === 4 ? world.inset.box : null);
