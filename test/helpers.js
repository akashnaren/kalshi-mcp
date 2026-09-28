export function market(overrides = {}) {
  return {
    ticker: "KXTEST-26-T1",
    event_ticker: "KXTEST-26",
    market_type: "binary",
    status: "active",
    notional_value_dollars: "1.0000",
    yes_bid_dollars: "0.1400",
    yes_ask_dollars: "0.1500",
    no_bid_dollars: "0.8500",
    no_ask_dollars: "0.8600",
    yes_bid_size_fp: "100.00",
    yes_ask_size_fp: "80.00",
    volume_24h_fp: "500.00",
    open_interest_fp: "400.00",
    yes_sub_title: "Above the strike",
    no_sub_title: "Below the strike",
    close_time: "2026-12-01T00:00:00Z",
    ...overrides,
  };
}

export function signal(overrides = {}) {
  return {
    key: "nhc_cone_includes_city",
    confidence: 0.8,
    side: "yes",
    detail: "NHC 5-day cone covers Miami as of 15:00Z",
    market_ticker: "KXTEST-26-T1",
    ...overrides,
  };
}

export function filters(overrides = {}) {
  return {
    min_confidence: 0.65,
    min_volume_24h: 200,
    min_open_interest: 100,
    max_spread: 0.08,
    min_top_size: 10,
    max_pages: 3,
    page_limit: 200,
    limit: 8,
    ...overrides,
  };
}
