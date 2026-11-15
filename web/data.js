// Loads data/rides.json and adds the few derived values the views need.
// Kept separate from the page so any future view (a WebGL scene, say) can
// import the same data without touching the DOM code.

export const KG_PER_LB = 0.45359237;

export async function loadReport(url = "data/rides.json") {
  const response = await fetch(url, { cache: "no-cache" });
  if (!response.ok) throw new Error(`Could not load ${url} (${response.status})`);
  const report = await response.json();

  let runningTotal = 0;
  for (const ride of report.rides) {
    ride.when = new Date(ride.date);
    ride.co2_saved_lbs = ride.co2_saved_kg / KG_PER_LB;
    if (!ride.excluded) runningTotal += ride.total_saved;
    ride.running_total = runningTotal;
  }
  report.counted = report.rides.filter((ride) => !ride.excluded);
  report.updated = new Date(report.updated_at);
  return report;
}
