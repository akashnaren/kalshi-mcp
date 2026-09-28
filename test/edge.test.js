import assert from "node:assert/strict";
import test from "node:test";
import {
  evaluateEntry,
  feeDomeCents,
  flbBand,
  fractionalKellyDollars,
  kellyFull,
} from "../src/edge.js";

test("fee dome peaks near 2 cents at 50 cents and is zero at the wings", () => {
  assert.equal(feeDomeCents(0.5, 1), 2);
  assert.equal(feeDomeCents(0.15, 1), 1);
  assert.equal(feeDomeCents(0.97, 1), 1);
  assert.equal(flbBand(0.08), "<10¢");
  assert.equal(flbBand(0.15), "10–25¢");
  assert.equal(flbBand(0.5), "25–75¢");
  assert.equal(flbBand(0.98), "≥90¢");
});

test("entry skips 10 cent takes unless edge is 8 cents, and skips 97 cent favorites", () => {
  const longshot = evaluateEntry({ pModel: 0.12, ask: 0.08, bid: 0.07, horizonDays: 5, depth: 100, settlementMatch: 1 });
  assert.equal(longshot.ok, false);
  assert.equal(longshot.reason, "longshot_take");

  const allowed = evaluateEntry({ pModel: 0.2, ask: 0.08, bid: 0.07, horizonDays: 5, depth: 100, settlementMatch: 1 });
  assert.equal(allowed.ok, true);
  assert.equal(allowed.stake_mode, "fixed_2");
  assert.equal(allowed.side_exec, "maker");
  assert.equal(allowed.maker_flag, true);
  assert.ok(allowed.edge_net_cents >= 8);

  const favorite = evaluateEntry({ pModel: 0.99, ask: 0.98, bid: 0.97, horizonDays: 2, depth: 50, settlementMatch: 1 });
  assert.equal(favorite.ok, false);
  assert.equal(favorite.reason, "favorite_wr");

  const weather = evaluateEntry({ pModel: 0.48, ask: 0.4, bid: 0.39, horizonDays: 3, depth: 80, settlementMatch: 1 });
  assert.equal(weather.ok, true);
  assert.equal(weather.maker_flag, true);
  assert.equal(weather.side_exec, "maker");
  assert.ok(weather.edge_net_cents >= 4);
  assert.equal(weather.depth_at_limit, 80);
  assert.equal(weather.edge_after_fees, weather.edge_net_cents);

  const wide = evaluateEntry({ pModel: 0.7, ask: 0.4, bid: 0.2, horizonDays: 3, depth: 80, settlementMatch: 1 });
  assert.equal(wide.reason, "wide_spread");

  const thin = evaluateEntry({ pModel: 0.7, ask: 0.4, bid: 0.39, horizonDays: 3, depth: 10, settlementMatch: 1 });
  assert.equal(thin.reason, "thin_book");

  const ambiguous = evaluateEntry({ pModel: 0.7, ask: 0.4, bid: 0.39, settlementMatch: 0.4, depth: 80, horizonDays: 2 });
  assert.equal(ambiguous.reason, "ambiguous_settlement");
});

test("λ stays 0.25 unless an explicit OOS calibration log is passed", () => {
  const full = kellyFull(0.6, 0.4);
  assert.equal(Math.round(full * 1000) / 1000, 0.333);
  const cold = fractionalKellyDollars({ p: 0.6, price: 0.4, bankroll: 71, history: { allows_scale: false } });
  const warm = fractionalKellyDollars({ p: 0.6, price: 0.4, bankroll: 71, history: { allows_scale: true } });
  assert.equal(cold, warm);
  const oos = fractionalKellyDollars({ p: 0.6, price: 0.4, bankroll: 71, oosCalibration: true });
  assert.equal(Math.round(oos / cold), 2);
  const halted = fractionalKellyDollars({ p: 0.6, price: 0.4, bankroll: 71, oosCalibration: true, drawdown: 0.4 });
  assert.equal(halted, 0);
});
