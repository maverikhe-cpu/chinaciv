// Number formatting in Chinese style: 万 (10⁴), 亿 (10⁸), 万亿 (10¹²); money in yuan (¥) unless the data says
// otherwise (setCurrency, called by main.js from nation.json `currency`, so the US test data still reads in $).
const UNITS = [[1e12, "万亿"], [1e8, "亿"], [1e4, "万"]];

// Significant digits: 1.23万亿, 12.3亿, 123亿, 1234万 (never "10000万": a value that rounds up moves to the next unit).
function scaled(n, d, unit) {
  const v = n / d;
  const a = Math.abs(v);
  const s = a >= 1000 ? Math.round(v).toString() : a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : v.toFixed(2);
  return s.replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1") + unit;
}

export function compact(n) {
  if (n == null || Number.isNaN(n)) return "–";
  const a = Math.abs(n);
  for (let k = 0; k < UNITS.length; k++) {
    const [d, unit] = UNITS[k];
    if (a >= d) {
      // 9999.6万 would print 10000万: hand it to the bigger unit instead.
      if (k > 0 && Math.round(a / d) >= 10000) return scaled(n, UNITS[k - 1][0], UNITS[k - 1][1]);
      if (unit === "万" && a >= 1e6) return Math.round(n / d) + unit; // 1780万, not 1779.89万
      return scaled(n, d, unit);
    }
  }
  if (a >= 1000) return Math.round(n).toLocaleString("zh-CN");
  return a < 10 && a !== Math.round(a) ? n.toFixed(1).replace(/\.0$/, "") : String(Math.round(n));
}

let currency = { code: "CNY", sym: "¥" };
const SYMBOLS = { CNY: "¥", RMB: "¥", USD: "$", HKD: "HK$", MOP: "MOP$", TWD: "NT$" };
/** Set the currency of money values (ISO code from nation.json; default CNY). */
export function setCurrency(code) {
  const c = String(code || "CNY").toUpperCase();
  currency = { code: c, sym: SYMBOLS[c] ?? "¥" };
}
export const currencySymbol = () => currency.sym;
export const isMoneyUnit = (u) => u === "cny" || u === "usd" || u === "yuan" || u === "money";

export const money = (n) => (n == null || Number.isNaN(n) ? "–" : (n < 0 ? "−" : "") + currency.sym + compact(Math.abs(n)));
/** Full-precision money: ¥1,234,567. */
export const dollars = (n) => (n == null || Number.isNaN(n) ? "–" : currency.sym + Math.round(n).toLocaleString("zh-CN"));
export const yuan = dollars;
export const int = (n) => (n == null || Number.isNaN(n) ? "–" : Math.round(n).toLocaleString("zh-CN"));
export const pct = (x, d = 1) => (x == null || Number.isNaN(x) ? "–" : (x * 100).toFixed(d) + "%");

export function yieldValue(y, v) {
  return isMoneyUnit(y?.unit) ? money(v) : compact(v);
}

/** Short population for banners and lists: 2187万, 1.41亿, 86万, 9.6万. */
export function popShort(n) {
  if (n == null || Number.isNaN(n)) return "–";
  return compact(n);
}

export const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

/** Chinese serif stack for canvas and CSS (Google Fonts "Noto Serif SC" when it loaded, else the system's Song). */
export const SERIF_CN = '"Noto Serif SC", "Source Han Serif SC", "Source Han Serif CN", "Songti SC", STSong, SimSun, serif';
/** Latin-first serif (digits and Latin from Palatino, Chinese from the Chinese serif). */
export const SERIF = `"Palatino Linotype", Palatino, "Book Antiqua", Georgia, ${SERIF_CN}`;
export const SANS = '-apple-system, system-ui, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif';
