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

test("entry skips longshot takes and 97 cent favorites, and keeps a real edge", () => {
  const longshot = evaluateEntry({ pModel: 0.2, ask: 0.08, bid: 0.07, category: "Politics", horizonDays: 5, depth: 40, settlementMatch: 1 });
  assert.equal(longshot.ok, false);
  assert.equal(longshot.reason, "longshot_take");

  const favorite = evaluateEntry({ pModel: 0.99, ask: 0.98, bid: 0.97, category: "Sports", horizonDays: 2, depth: 50, settlementMatch: 1 });
  assert.equal(favorite.ok, false);
  assert.equal(favorite.reason, "favorite_wr");

  const weather = evaluateEntry({ pModel: 0.46, ask: 0.4, bid: 0.39, category: "Weather", horizonDays: 3, depth: 80, settlementMatch: 1 });
  assert.equal(weather.ok, true);
  assert.equal(weather.maker_flag, true);
  assert.ok(weather.edge_net_cents >= 3);
  assert.equal(weather.depth_at_limit, 80);

  const ambiguous = evaluateEntry({ pModel: 0.7, ask: 0.4, bid: 0.39, settlementMatch: 0.4, depth: 20, horizonDays: 2 });
  assert.equal(ambiguous.reason, "ambiguous_settlement");
});

test("quarter Kelly is the cold size and half Kelly is the warm size", () => {
  const full = kellyFull(0.6, 0.4);
  assert.equal(Math.round(full * 1000) / 1000, 0.333);
  const cold = fractionalKellyDollars({ p: 0.6, price: 0.4, bankroll: 71, history: { allows_scale: false } });
  const warm = fractionalKellyDollars({ p: 0.6, price: 0.4, bankroll: 71, history: { allows_scale: true } });
  assert.ok(warm > cold);
  assert.equal(Math.round(warm / cold), 2);
  const halted = fractionalKellyDollars({ p: 0.6, price: 0.4, bankroll: 71, history: { allows_scale: true }, drawdown: 0.4 });
  assert.equal(halted, 0);
});
