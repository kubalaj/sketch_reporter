import { drawChart } from "./chart.js";
import { loadReport } from "./data.js";
import { esc, money, number, shortDate } from "./format.js";

const $ = (id) => document.getElementById(id);
const DAY = 86400000;
// A payoff date extrapolated from less history than this is noise.
const MIN_DAYS_FOR_PROJECTION = 30;

function renderHero({ summary }) {
  $("total-saved").textContent = money(summary.total_saved);
  const pct = Math.min(summary.paid_off_pct ?? 0, 100);
  $("meter-fill").style.width = `${pct}%`;
  $("meter").setAttribute("aria-valuenow", pct);
  $("payoff-note").innerHTML =
    summary.remaining > 0
      ? `<strong>${number(summary.paid_off_pct, summary.paid_off_pct < 10 ? 2 : 1)}%</strong> of the
         ${money(summary.bike_cost, 0)} bike paid off · <strong>${money(summary.remaining)}</strong> to go`
      : `The ${money(summary.bike_cost, 0)} bike has paid for itself.`;
}

function renderTiles({ summary, counted, gas_price: gas, updated }) {
  const daysOfHistory = counted.length ? (updated - counted[0].when) / DAY : 0;
  let payoff = { value: "Too early", note: "Needs a month of rides" };
  if (summary.remaining <= 0) {
    payoff = { value: "Paid off", note: "" };
  } else if (summary.projected_payoff_date && daysOfHistory >= MIN_DAYS_FOR_PROJECTION) {
    const date = new Date(`${summary.projected_payoff_date}T12:00:00`);
    payoff = {
      value: date.toLocaleDateString("en-US", { month: "short", year: "numeric" }),
      note: "At the pace so far",
    };
  }

  const tiles = [
    { label: "Rides", value: number(summary.rides), note: `${number(summary.miles, 1)} miles` },
    {
      label: "Saved per ride",
      value: summary.rides ? money(summary.total_saved / summary.rides) : "–",
      note: "Average",
    },
    {
      label: "CO₂ avoided",
      value: `${number(summary.co2_saved_lbs, 1)} lb`,
      note: `${number(summary.co2_saved_kg, 1)} kg`,
    },
    {
      label: "Gas price",
      value: `${money(gas.dollars_per_gallon)}/gal`,
      note: gas.week
        ? `Bay Area average, week of ${shortDate(new Date(`${gas.week}T12:00:00`))}`
        : "Fixed price from config",
    },
    { label: "Projected payoff", ...payoff },
  ];

  $("tiles").innerHTML = tiles
    .map(
      (tile) => `
      <div class="tile">
        <p class="label">${tile.label}</p>
        <p class="tile-value">${tile.value}</p>
        <p class="tile-note">${tile.note}</p>
      </div>`
    )
    .join("");
}

function renderSplit({ summary }) {
  const parts = [
    { name: "Gas", value: summary.gas_saved, color: "var(--gas)" },
    { name: "Wear and tear", value: summary.wear_saved, color: "var(--wear)" },
  ];
  const total = summary.total_saved || 1;
  const share = (part) => `${Math.round((100 * part.value) / total)}%`;

  $("split-bar").innerHTML = parts
    .filter((part) => part.value > 0)
    .map((part) => `<span style="flex:${part.value};background:${part.color}"></span>`)
    .join("");
  $("split-bar").setAttribute(
    "aria-label",
    parts.map((part) => `${part.name} ${money(part.value)}, ${share(part)}`).join("; ")
  );
  $("split-legend").innerHTML = parts
    .map(
      (part) => `
      <li><span class="swatch" style="background:${part.color}"></span>${part.name}
        <strong>${money(part.value)}</strong> · ${share(part)}</li>`
    )
    .join("");
}

function renderRides({ rides }) {
  $("rides").innerHTML = [...rides]
    .reverse()
    .map(
      (ride) => `
      <tr class="${ride.excluded ? "excluded" : ""}">
        <td>${shortDate(ride.when, true)}</td>
        <td><a href="https://www.strava.com/activities/${encodeURIComponent(ride.id)}"
               target="_blank" rel="noopener">${esc(ride.name)}</a>${
                 ride.excluded ? `<span class="tag">not counted</span>` : ""
               }</td>
        <td class="num">${number(ride.miles, 1)}</td>
        <td class="num">${money(ride.gas_price)}</td>
        <td class="num">${money(ride.gas_saved)}</td>
        <td class="num">${money(ride.wear_saved)}</td>
        <td class="num total">${money(ride.total_saved)}</td>
        <td class="num">${number(ride.co2_saved_lbs, 1)}</td>
      </tr>`
    )
    .join("");
}

function renderFootnote({ rides, updated }) {
  const latest = rides[rides.length - 1];
  const rates = latest
    ? ` Driving costs assume ${number(latest.mpg)} mpg and ${money(latest.wear_per_mile)} a mile in wear and tear.`
    : "";
  $("footnote").textContent =
    `Updated ${updated.toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" })}.` +
    rates +
    " Each ride is priced at the average gas price for the week it happened.";
}

async function main() {
  let report;
  try {
    report = await loadReport();
  } catch (error) {
    $("status").textContent = `Couldn't load the ride data. ${error.message}`;
    return;
  }
  $("status").hidden = true;
  $("report").hidden = false;

  renderHero(report);
  renderTiles(report);
  renderSplit(report);
  renderRides(report);
  renderFootnote(report);

  const chart = () => drawChart($("chart"), report.counted, report.updated);
  chart();
  new ResizeObserver(chart).observe($("chart"));
}

main();
