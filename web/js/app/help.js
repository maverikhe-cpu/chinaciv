// Keyboard & mouse cheat-sheet.
import { icon } from "../ui/icon.js";

const isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "");
const MOD = isMac ? "⌘" : "Ctrl";

const ROWS = [
  ["移动", [
    [["拖动", "←↑→↓", "WASD"], "平移地图"],
    [["滚轮", "+", "–"], "放大、缩小"],
    [["双击"], "放大该处"],
    [["双指"], "捏合缩放、拖动平移（触屏）"],
    [["小地图"], "点击或拖动以跳转"],
  ]],
  ["探索", [
    [["点击城市"], "打开城市界面"],
    [["点击陆地"], "打开该省份"],
    [["/", `${MOD} K`], "搜索城市和省份"],
    [["R"], "显示或隐藏资源"],
  ]],
  ["面板", [
    [["?"], "本帮助"],
    [["Esc"], "依次关闭帮助、搜索、百科和面板"],
  ]],
];

// The compliance notice (world.json `notice`, docs/CN_PLAN.md §2.3); the fixed text is used when the data has none.
export const NOTICE_TEXT = "本地图仅供个人学习，未申请审图号；国界线依据标准地图数据绘制（来源见“地图说明”与“数据来源”）。";
export const ATTRIBUTION = "本项目改编自 Andrej Karpathy 的 usaciv（MIT 许可证）。";

export function noticeHtml(notice, { long = false } = {}) {
  const text = notice?.text ? String(notice.text) : "";
  const parts = [NOTICE_TEXT];
  if (text && !NOTICE_TEXT.startsWith(text.replace(/。$/, ""))) parts.push(text);
  if (long && notice?.boundary) parts.push(String(notice.boundary));
  return parts.map((t) => t.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c])).join(" ");
}

export function initHelp({ notice = null } = {}) {
  const modal = document.getElementById("help");
  if (!modal) return { open() {}, close() {}, get isOpen() { return false; } };
  const card = modal.querySelector(".help-card");
  let restore = null;
  card.innerHTML = `
    <button class="icon-btn help-close" data-close aria-label="关闭帮助">${icon("close", "✕", { size: 13 })}</button>
    <h2 id="help-title">${icon("help", "?", { size: 22 })}<span>操作说明</span></h2>
    <div class="help-cols">${ROWS.map(([title, rows]) => `
      <section><h3>${title}</h3>${rows.map(([keys, what]) => `
        <div class="help-row"><span class="keys">${keys.map((k) => `<kbd>${k}</kbd>`).join("")}</span><span>${what}</span></div>`).join("")}
      </section>`).join("")}</div>
    <p class="help-foot">每个数字都可追溯到公开数据：打开<b>百科</b>查看数据来源。地址栏中的链接总能重新打开当前视图。</p>
    <p class="help-foot help-notice"><b>地图说明：</b>${noticeHtml(notice, { long: true })}</p>
    <p class="help-foot">${ATTRIBUTION}源代码：<a href="chinaciv-source.zip" download>chinaciv-source.zip</a>（MIT 许可证；用 uv 运行，见其中的 README）。</p>`;

  const close = () => {
    if (modal.hidden) return;
    modal.hidden = true;
    if (restore && document.contains(restore)) restore.focus();
    restore = null;
  };
  modal.addEventListener("click", (e) => {
    if (e.target === modal || e.target.closest("[data-close]")) close();
  });
  modal.addEventListener("keydown", (e) => {
    if (e.key !== "Tab") return;
    e.preventDefault(); // the only focusable control is the close button
    card.querySelector("[data-close]").focus();
  });

  return {
    open() {
      if (!modal.hidden) return;
      restore = document.activeElement;
      modal.hidden = false;
      card.querySelector("[data-close]").focus();
    },
    close,
    toggle() { modal.hidden ? this.open() : close(); },
    get isOpen() { return !modal.hidden; },
  };
}
