// Running-total step chart, drawn as plain SVG with a hover readout.

import { esc, money, shortDate } from "./format.js";

const HEIGHT = 260;
const MARGIN = { top: 12, right: 12, bottom: 28, left: 48 };
const DAY = 86400000;

function niceStep(max, targetTicks) {
  const raw = max / targetTicks;
  const power = 10 ** Math.floor(Math.log10(raw));
  const lead = raw / power;
  return (lead <= 1 ? 1 : lead <= 2 ? 2 : lead <= 5 ? 5 : 10) * power;
}

export function drawChart(container, rides, until) {
  if (!rides.length) {
    container.innerHTML = `<p class="empty">No rides yet.</p>`;
    return;
  }

  const width = container.clientWidth;
  const plotW = width - MARGIN.left - MARGIN.right;
  const plotH = HEIGHT - MARGIN.top - MARGIN.bottom;

  // Time runs from just before the first ride to now, and at least a day.
  const first = rides[0].when.getTime();
  const end = Math.max(until.getTime(), rides[rides.length - 1].when.getTime(), first + DAY);
  const start = first - (end - first) * 0.04;
  const total = rides[rides.length - 1].running_total;
  const step = niceStep(total, 4);
  const top = Math.ceil(total / step) * step;

  const x = (time) => MARGIN.left + ((time - start) / (end - start)) * plotW;
  const y = (value) => MARGIN.top + plotH - (value / top) * plotH;

  let line = `M${x(start)},${y(0)}`;
  let level = 0;
  for (const ride of rides) {
    line += `H${x(ride.when)}V${y(ride.running_total)}`;
    level = ride.running_total;
  }
  line += `H${x(end)}`;
  const area = `${line}V${y(0)}Z`;

  let grid = "";
  for (let value = 0; value <= top + step / 2; value += step) {
    grid += `<line class="grid" x1="${MARGIN.left}" x2="${width - MARGIN.right}" y1="${y(value)}" y2="${y(value)}"/>
      <text class="axis-text" x="${MARGIN.left - 8}" y="${y(value)}" dy="0.32em" text-anchor="end">${money(value, step < 1 ? 2 : 0)}</text>`;
  }

  const tickCount = Math.max(2, Math.min(6, Math.floor(plotW / 110)));
  const withYear = end - start > 300 * DAY;
  let ticks = "";
  let lastLabel = "";
  for (let i = 0; i < tickCount; i++) {
    const time = start + ((end - start) * i) / (tickCount - 1);
    const label = shortDate(new Date(time), withYear);
    if (label === lastLabel) continue; // a short span would repeat the same day
    lastLabel = label;
    const anchor = i === 0 ? "start" : i === tickCount - 1 ? "end" : "middle";
    ticks += `<text class="axis-text" x="${x(time)}" y="${HEIGHT - 8}" text-anchor="${anchor}">${label}</text>`;
  }

  // Individual markers only while there are few enough to read.
  const dots =
    rides.length <= 40
      ? rides.map((r) => `<circle class="dot" cx="${x(r.when)}" cy="${y(r.running_total)}" r="4"/>`).join("")
      : "";

  container.innerHTML = `
    <svg viewBox="0 0 ${width} ${HEIGHT}" height="${HEIGHT}" role="img"
         aria-label="Running total saved, reaching ${money(level)} over ${rides.length} rides. The rides table below has the same figures.">
      ${grid}${ticks}
      <path class="area" d="${area}"/>
      <path class="line" d="${line}"/>
      ${dots}
      <line class="crosshair" y1="${MARGIN.top}" y2="${MARGIN.top + plotH}" hidden />
      <circle class="dot focus" r="5" hidden />
      <rect class="hit" x="${MARGIN.left}" y="${MARGIN.top}" width="${plotW}" height="${plotH}" fill="transparent"/>
    </svg>
    <div class="tooltip" hidden></div>`;

  const crosshair = container.querySelector(".crosshair");
  const focus = container.querySelector(".focus");
  const tooltip = container.querySelector(".tooltip");
  const hit = container.querySelector(".hit");

  function show(event) {
    const pointerX = event.clientX - container.getBoundingClientRect().left;
    const ride = rides.reduce((best, r) =>
      Math.abs(x(r.when) - pointerX) < Math.abs(x(best.when) - pointerX) ? r : best
    );
    const cx = x(ride.when);
    const cy = y(ride.running_total);
    crosshair.setAttribute("x1", cx);
    crosshair.setAttribute("x2", cx);
    focus.setAttribute("cx", cx);
    focus.setAttribute("cy", cy);
    tooltip.innerHTML = `
      <div class="tip-date">${shortDate(ride.when, true)}</div>
      <div>${esc(ride.name)} · +${money(ride.total_saved)}</div>
      <div class="tip-total">${money(ride.running_total)} total</div>`;
    setHidden(false);
    const tipW = tooltip.offsetWidth;
    const left = cx + 12 + tipW > width ? cx - 12 - tipW : cx + 12;
    tooltip.style.transform = `translate(${Math.max(0, left)}px, ${Math.max(0, cy - 60)}px)`;
  }

  // SVG elements have no `hidden` property, so toggle the attribute.
  function setHidden(hidden) {
    for (const el of [crosshair, focus, tooltip]) el.toggleAttribute("hidden", hidden);
  }

  hit.addEventListener("pointermove", show);
  hit.addEventListener("pointerdown", show);
  hit.addEventListener("pointerleave", () => setHidden(true));
}
