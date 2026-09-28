import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { findBestBets } from "../src/scan.js";
import { market, signal } from "./helpers.js";

test("scan source never places an order", () => {
  const source = fs.readFileSync(new URL("../src/scan.js", import.meta.url), "utf8");
  assert.equal(source.includes("createOrder"), false);
  assert.equal(source.includes("place_order"), false);
  assert.equal(source.includes("cancelOrder"), false);
});

test("scan pages only up to the cap and ranks the liquid cheap side", async () => {
  const calls = [];
  const client = {
    async getMarkets(query) {
      calls.push(query);
      if (!query.cursor) {
        return {
          markets: [market({ ticker: "PAGE-1", yes_ask_dollars: "0.4000", yes_bid_dollars: "0.3900" })],
          cursor: "next",
        };
      }
      return {
        markets: [market({ ticker: "PAGE-2" })],
        cursor: "again",
      };
    },
    async createOrder() {
      throw new Error("scan must not create orders");
    },
  };
  const result = await findBestBets({
    client,
    signals: [
      signal({ market_ticker: "PAGE-1", confidence: 0.9, key: "rcp_polling_average", detail: "RCP average is 72 percent" }),
      signal({ market_ticker: "PAGE-2", confidence: 0.8 }),
    ],
    options: { max_pages: 2, limit: 5 },
  });
  assert.equal(calls.length, 2);
  assert.equal(calls[0].cursor, undefined);
  assert.equal(calls[1].cursor, "next");
  assert.equal(calls[0].mve_filter, "exclude");
  assert.deepEqual(result.recommendations.map((row) => row.ticker), ["PAGE-2", "PAGE-1"]);
  assert.equal(result.policy.auto_trade, false);
  assert.equal(result.policy.score, "confidence * payout / stake");
  assert.equal(result.recommendations[0].keys[0], "nhc_cone_includes_city");
  assert.equal(result.scanned_markets, 2);
});

test("series scope is one query, not one query per market", async () => {
  const calls = [];
  const client = {
    async getMarkets(query) {
      calls.push(query);
      return {
        markets: [
          market({ ticker: "KXFED-26DEC-T1", event_ticker: "KXFED-26DEC", yes_ask_dollars: "0.2000", yes_bid_dollars: "0.1800" }),
          market({ ticker: "OTHER-1", event_ticker: "OTHER-26", yes_ask_dollars: "0.1000", yes_bid_dollars: "0.0900" }),
        ],
        cursor: "",
      };
    },
  };
  const result = await findBestBets({
    client,
    signals: [signal({
      market_ticker: "",
      series_ticker: "KXFED",
      confidence: 0.7,
      key: "fedwatch_cut_prob",
      detail: "FedWatch prices a 78 percent chance of a cut",
    })],
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].series_ticker, "KXFED");
  assert.deepEqual(result.recommendations.map((row) => row.ticker), ["KXFED-26DEC-T1"]);
  assert.equal(result.skipped.no_signal, 1);
});
