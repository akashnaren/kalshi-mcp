import assert from "node:assert/strict";
import { test } from "node:test";

import { presentFills, presentPositions } from "../src/portfolio.js";
import { presentMarkets } from "../src/markets.js";

test("positions keep official market and event fields and do not invent recovery", () => {
  const presented = presentPositions({
    cursor: "next",
    market_positions: [
      {
        ticker: "KXTEST",
        position_fp: "-3.00",
        market_exposure_dollars: "1.5000",
        realized_pnl_dollars: "0.2500",
        fees_paid_dollars: "0.0100",
        average_price_dollars: "0.5600",
        total_traded_dollars: "4.0000",
      },
    ],
    event_positions: [
      {
        event_ticker: "KXEVENT",
        total_cost_dollars: "4.0000",
        total_cost_shares_fp: "3.00",
        event_exposure_dollars: "1.5000",
      },
    ],
  });

  assert.deepEqual(presented.market_positions, [
    {
      ticker: "KXTEST",
      position_fp: "-3.00",
      market_exposure_dollars: "1.5000",
      realized_pnl_dollars: "0.2500",
      fees_paid_dollars: "0.0100",
      average_price_dollars: "0.5600",
      total_traded_dollars: "4.0000",
      qty: "-3.00",
      avg: "0.5600",
    },
  ]);
  assert.deepEqual(presented.event_positions, [
    {
      event_ticker: "KXEVENT",
      total_cost_dollars: "4.0000",
      total_cost_shares_fp: "3.00",
      event_exposure_dollars: "1.5000",
      ticker: "KXEVENT",
      qty: "3.00",
    },
  ]);
  const text = JSON.stringify(presented);
  assert.equal(text.includes("remaining"), false);
  assert.equal(text.includes("side"), false);
  assert.equal(text.includes("\"mark\""), false);
});

test("a position side or mark is copied only when the API sends it", () => {
  const presented = presentPositions({
    market_positions: [{ ticker: "KXTEST", position: 2, side: "yes", mark_price_dollars: "0.4200" }],
    event_positions: [],
  });
  assert.deepEqual(presented.market_positions, [
    {
      ticker: "KXTEST",
      side: "yes",
      position: 2,
      mark_price_dollars: "0.4200",
      qty: 2,
      mark: "0.4200",
    },
  ]);
});

test("fills keep official side, quantity, and prices", () => {
  const presented = presentFills({
    cursor: "",
    fills: [
      {
        fill_id: "f1",
        ticker: "KXTEST",
        outcome_side: "no",
        count_fp: "1.00",
        yes_price_dollars: "0.4000",
        no_price_dollars: "0.6000",
        fee_cost: "0.0100",
        is_taker: true,
      },
    ],
  });
  assert.deepEqual(presented.fills, [
    {
      fill_id: "f1",
      ticker: "KXTEST",
      outcome_side: "no",
      count_fp: "1.00",
      yes_price_dollars: "0.4000",
      no_price_dollars: "0.6000",
      fee_cost: "0.0100",
      is_taker: true,
      side: "no",
      qty: "1.00",
    },
  ]);
});

test("list_markets keeps the compact public shape", () => {
  assert.deepEqual(
    presentMarkets({
      cursor: "c",
      markets: [
        {
          ticker: "KXTEST",
          title: "Test",
          status: "open",
          yes_bid: 40,
          yes_ask: 42,
          volume: 10,
          yes_bid_dollars: "0.4000",
          unrelated: "drop",
        },
      ],
    }),
    {
      count: 1,
      markets: [
        {
          ticker: "KXTEST",
          title: "Test",
          status: "open",
          yes_bid: 40,
          yes_ask: 42,
          volume: 10,
          yes_bid_dollars: "0.4000",
        },
      ],
      cursor: "c",
    },
  );
});
