"""Rank markets by confidence, payout, and stake. No HTTP and no orders."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_DOWN

DO_NOT_PLACE = "Finance Engineer may place under the caps with confirm true"
LONGSHOT_EDGE = Decimal("0.08")
MAKER_NEAR_CENTS = Decimal("2")
_DAY = Decimal(24)
FORMULA = "estimated_confidence * payout_ratio / stake_needed"
RESEARCH_NOTE = "no confidence supplied; not a recommendation"

_TICKER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{2,48}$")
_CATEGORIES = frozenset({"Politics", "Crypto", "Sports", "Weather", "Finance", "Macro"})
_VAGUE = frozenset({
    "gut", "feeling", "hunch", "intuition", "vibe", "guess", "opinion", "yolo",
})
_FEE_BLIND = re.compile(r"fee[-\s]?blind|without fees|ignoring fees", re.IGNORECASE)
_OPEN = frozenset({"active", "open"})
_FOUR = Decimal("0.0001")
_CENT = Decimal("0.01")
_ONE = Decimal("1")


_TAKER_RATE = Decimal("0.07")
_MAKER_RATE = Decimal("0.0175")
_FEE_MULTIPLIER_KEYS = ("fee_multiplier", "series_fee_multiplier", "fee_multiplier_fp")
_CORR_ABSENT = frozenset({"none", "null"})

# Gaps-pack series fee catalog (Kalshi schedule effective 2026-07-07).
# Longest matching prefix on series_ticker, ticker, or event_ticker wins.
# catalog: listed in the Non-Standard table. inference: weather is absent from that table.
# default (no match): M_taker 1, M_maker 0. payload: fee_multiplier overrides both sides.
# KXMVE maker M is 2 for the whole prefix. The uncorrelated-NFL-combo exception is deferred.
# KXNBAGAME was not in the PDF extract. It matches KXNBA, so maker M is 1. Do not assume a free maker.
_SERIES_FEE_CATALOG: tuple[tuple[str, Decimal, Decimal, str], ...] = (
    ("KXNFLGAME", Decimal(1), Decimal(1), "catalog"),
    ("KXWNBAGAME", Decimal(1), Decimal(1), "catalog"),
    ("KXNBAEAST", Decimal(1), Decimal(1), "catalog"),
    ("KXNBAWEST", Decimal(1), Decimal(1), "catalog"),
    ("KXMLBGAME", Decimal(1), Decimal(1), "catalog"),
    ("KXNCAAFGAME", Decimal(1), Decimal(1), "catalog"),
    ("KXUCLGAME", Decimal(1), Decimal(1), "catalog"),
    ("KXATPMATCH", Decimal(1), Decimal(1), "catalog"),
    ("KXWTAMATCH", Decimal(1), Decimal(1), "catalog"),
    ("KXWCGAME", Decimal(1), Decimal(1), "catalog"),
    ("KXCPIYOY", Decimal(1), Decimal(1), "catalog"),
    ("KXFEDDECISION", Decimal(1), Decimal(1), "catalog"),
    ("KXRATECUTCOUNT", Decimal(1), Decimal(1), "catalog"),
    ("KXPAYROLLS", Decimal(1), Decimal(1), "catalog"),
    ("KXNASDAQ100Y", Decimal(1), Decimal(1), "catalog"),
    ("KXBTCMAX150", Decimal(1), Decimal(1), "catalog"),
    ("KXGAMBLINGREPEAL", Decimal(0), Decimal(0), "catalog"),
    ("KXELECTIRAN", Decimal(0), Decimal(0), "catalog"),
    ("KXGREENLAND", Decimal(0), Decimal(0), "catalog"),
    ("KXLALIGA", Decimal(1), Decimal(1), "catalog"),
    ("KXNCAAF", Decimal(1), Decimal(1), "catalog"),
    ("KXMLB", Decimal(1), Decimal(1), "catalog"),
    ("KXNBA", Decimal(1), Decimal(1), "catalog"),
    ("KXNFL", Decimal(1), Decimal(1), "catalog"),
    ("KXCPI", Decimal(1), Decimal(1), "catalog"),
    ("KXFED", Decimal(1), Decimal(1), "catalog"),
    ("KXGDP", Decimal(1), Decimal(1), "catalog"),
    ("KXINXY", Decimal(1), Decimal(1), "catalog"),
    ("KXBTCY", Decimal(0), Decimal(0), "catalog"),
    ("KXETHY", Decimal(0), Decimal(0), "catalog"),
    ("KXHIGH", Decimal(1), Decimal(0), "inference"),
    ("KXDOED", Decimal(0), Decimal(0), "catalog"),
    ("KXMVE", Decimal(1), Decimal(2), "catalog"),
    ("KXSB", Decimal(1), Decimal(1), "catalog"),
    ("KXU3", Decimal(1), Decimal(1), "catalog"),
)


def _schedule_fee_cents(price: Decimal, contracts: Decimal, *, rate: Decimal, multiplier: Decimal) -> Decimal:
    """ceil(M * rate * contracts * P * (1-P) * 100). Rate is 0.07 taker or 0.0175 maker."""
    if multiplier <= 0 or not (Decimal(0) < price < Decimal(1)) or contracts <= 0:
        return Decimal(0)
    raw = multiplier * rate * contracts * price * (Decimal(1) - price) * Decimal(100)
    return raw.to_integral_value(rounding=ROUND_CEILING)


def series_multiplier(market: dict | None) -> Decimal | None:
    """Series fee multiplier M when the market payload has one. None means use the defaults."""
    if not isinstance(market, dict):
        return None
    for key in _FEE_MULTIPLIER_KEYS:
        raw = market.get(key)
        if raw is None or raw == "" or isinstance(raw, bool):
            continue
        try:
            if isinstance(raw, str):
                value = Decimal(raw.strip())
            elif isinstance(raw, int):
                value = Decimal(raw)
            elif isinstance(raw, float):
                value = Decimal(format(raw, ".6f"))
            else:
                continue
        except (InvalidOperation, ValueError):
            continue
        if value < 0 or value > 100:
            continue
        return value
    return None


def series_fees(market: dict | None) -> tuple[Decimal, Decimal, str]:
    """(M_taker, M_maker, source) before fee_cents_est and edge_net.

    A payload multiplier sets both sides and wins over the prefix catalog.
    Otherwise the longest series prefix on series_ticker, ticker, or event_ticker wins.
    """
    payload = series_multiplier(market)
    if payload is not None:
        return payload, payload, "payload"
    best: tuple[int, Decimal, Decimal, str] | None = None
    if isinstance(market, dict):
        for key in ("series_ticker", "ticker", "event_ticker"):
            raw = market.get(key)
            if not isinstance(raw, str) or not raw.strip():
                continue
            needle = raw.strip().upper()
            for prefix, m_taker, m_maker, source in _SERIES_FEE_CATALOG:
                if needle.startswith(prefix) and (best is None or len(prefix) > best[0]):
                    best = (len(prefix), m_taker, m_maker, source)
    if best is None:
        return Decimal(1), Decimal(0), "default"
    return best[1], best[2], best[3]


def fee_dome_cents(
    price: Decimal,
    contracts: Decimal = Decimal(1),
    *,
    multiplier: Decimal | None = None,
) -> Decimal:
    """Taker fee in cents: ceil(M_taker * 0.07 * C * P * (1-P) * 100). None means M_taker 1."""
    return _schedule_fee_cents(
        price,
        contracts,
        rate=_TAKER_RATE,
        multiplier=Decimal(1) if multiplier is None else multiplier,
    )


def maker_fee_cents(
    price: Decimal,
    contracts: Decimal = Decimal(1),
    *,
    multiplier: Decimal | None = None,
) -> Decimal:
    """Maker fee in cents: ceil(M_maker * 0.0175 * C * P * (1-P) * 100). None or 0 is free."""
    if multiplier is None:
        return Decimal(0)
    return _schedule_fee_cents(price, contracts, rate=_MAKER_RATE, multiplier=multiplier)


def depth_haircut_cents(depth: Decimal | None) -> Decimal:
    """Cents removed when the ask shows fewer than 3 contracts. Capped at 2. Zero at or above 3."""
    if depth is None or depth >= Decimal(3):
        return Decimal(0)
    if depth <= 0:
        return Decimal(2)
    return min(Decimal(3) - depth, Decimal(2)).quantize(Decimal("0.01"))


def flb_band(price: Decimal) -> str:
    if price <= Decimal("0.10"):
        return "<10¢"
    if price < Decimal("0.25"):
        return "10–25¢"
    if price < Decimal("0.75"):
        return "25–75¢"
    if price < Decimal("0.90"):
        return "75–90¢"
    return "≥90¢"


def stake_mode_for(price: Decimal) -> str:
    if price < Decimal("0.25"):
        return "fixed_2"
    if price >= Decimal("0.50"):
        return "modest"
    return "kelly"


def side_exec_for(ask: Decimal, bid: Decimal | None) -> str:
    """Maker when a one-cent rest can sit strictly below the ask."""
    if bid is not None and ask > bid and (ask - bid) >= _CENT:
        return "maker"
    return "taker"


def spread_cents_for(ask: Decimal, bid: Decimal | None) -> Decimal | None:
    if bid is None or ask <= bid:
        return None
    return ((ask - bid) * Decimal(100)).quantize(Decimal("0.01"))


def taker_longshot_allowed(*, allow_longshot: bool, edge: Decimal) -> bool:
    """Taker quotes in the <10¢ band stay only for a named longshot with edge ≥ 0.08."""
    return allow_longshot and edge >= LONGSHOT_EDGE


def hold_to_res_default(*, days: Decimal, net_cents: Decimal, fee_cents: Decimal) -> bool:
    """True when DTR is at most 7 and a round trip costs more than the remaining edge.

    There is no settlement fee. A round trip is two trade fees. This is not a take-profit percent.
    """
    return days <= Decimal(7) and (fee_cents * 2) > net_cents


def corr_group_hint(belief: dict, market: dict) -> str:
    named = belief.get("corr_group")
    if isinstance(named, str) and named:
        return named
    event = market.get("event_ticker")
    if isinstance(event, str):
        slug = re.sub(r"[^a-z0-9]+", "_", event.lower()).strip("_")
        if _KEY_RE.fullmatch(slug):
            return slug
    tag = belief.get("category_tag")
    if isinstance(tag, str):
        slug = re.sub(r"[^a-z0-9]+", "_", tag.lower()).strip("_")
        if _KEY_RE.fullmatch(slug):
            return slug
    return "unspecified_event"


def edge_net_cents(
    confidence: Decimal,
    ask: Decimal,
    bid: Decimal | None,
    *,
    depth: Decimal | None = None,
    multiplier: Decimal | None = None,
) -> Decimal:
    """(confidence - ask) in cents, minus the taker fee dome, half the spread, and the depth haircut."""
    fee = fee_dome_cents(ask, Decimal(1), multiplier=multiplier)
    spread = Decimal(0)
    if bid is not None and bid > 0 and ask > bid:
        spread = (ask - bid) * Decimal(100) / Decimal(2)
    gross = (confidence - ask) * Decimal(100)
    return (gross - fee - spread - depth_haircut_cents(depth)).quantize(Decimal("0.01"))


def score_formula(confidence: Decimal, payout_ratio: Decimal, stake: Decimal) -> Decimal:
    """estimated_confidence * payout_ratio / stake_needed, four decimal places."""
    return (confidence * payout_ratio / stake).quantize(_FOUR)


@dataclass(frozen=True)
class Options:
    min_volume: Decimal = Decimal("20")
    min_lifetime_volume: Decimal = Decimal("1000")
    min_ask_size: Decimal = Decimal("1")
    min_confidence: Decimal = Decimal("0.55")
    min_edge: Decimal = Decimal("0.08")
    min_hours: Decimal = Decimal("2")
    max_hours: Decimal = Decimal("1440")
    max_price: Decimal = Decimal("0.84")
    max_risk: Decimal = Decimal("5")
    limit: int = 5
    max_pages: int = 1
    page_size: int = 100
    min_price: Decimal = Decimal("0.02")
    price_ceiling: Decimal = Decimal("0.85")


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _plain(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _q4(value: Decimal) -> str:
    return format(value.quantize(_FOUR), "f")


def _money(value: Decimal) -> str:
    return format(value.quantize(_CENT, rounding=ROUND_DOWN), "f")


def _int_arg(args: dict, field: str, default: int, low: int, high: int) -> int:
    if field not in args or args[field] is None:
        return default
    raw = args[field]
    if isinstance(raw, bool):
        raise RuntimeError(f"{field} must be an integer from {low} to {high}")
    if isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        raw = int(raw.strip())
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise RuntimeError(f"{field} must be an integer from {low} to {high}")
    if raw < low or raw > high:
        raise RuntimeError(f"{field} must be an integer from {low} to {high}")
    return raw


def _decimal_arg(args: dict, field: str, default: Decimal, low: Decimal, high: Decimal) -> Decimal:
    if field not in args or args[field] is None:
        return default
    raw = args[field]
    if isinstance(raw, bool):
        raise RuntimeError(f"{field} must be from {_plain(low)} to {_plain(high)}")
    try:
        if isinstance(raw, str):
            value = Decimal(raw.strip())
        elif isinstance(raw, int):
            value = Decimal(raw)
        elif isinstance(raw, float):
            value = Decimal(format(raw, ".6f"))
        else:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        raise RuntimeError(f"{field} must be from {_plain(low)} to {_plain(high)}") from None
    if value < low or value > high:
        raise RuntimeError(f"{field} must be from {_plain(low)} to {_plain(high)}")
    return value


def parse_options(args: dict) -> Options:
    min_hours = _decimal_arg(args, "min_hours_to_expiry", Decimal("2"), Decimal("0"), Decimal("168"))
    max_hours = _decimal_arg(args, "max_hours_to_expiry", Decimal("1440"), Decimal("1"), Decimal("8760"))
    if max_hours <= min_hours:
        raise RuntimeError("max_hours_to_expiry must be greater than min_hours_to_expiry")
    return Options(
        min_volume=_decimal_arg(args, "min_volume", Decimal("20"), Decimal("0"), Decimal("1000000")),
        min_lifetime_volume=_decimal_arg(
            args, "min_lifetime_volume", Decimal("1000"), Decimal("0"), Decimal("100000000")
        ),
        min_ask_size=_decimal_arg(args, "min_ask_size", Decimal("1"), Decimal("0"), Decimal("100000")),
        min_confidence=_decimal_arg(args, "min_confidence", Decimal("0.55"), Decimal("0.50"), Decimal("0.99")),
        min_edge=_decimal_arg(args, "min_edge", Decimal("0.08"), Decimal("0"), Decimal("0.90")),
        min_hours=min_hours,
        max_hours=max_hours,
        max_price=_decimal_arg(args, "max_price", Decimal("0.84"), Decimal("0.05"), Decimal("0.84")),
        max_risk=_decimal_arg(args, "max_risk_dollars", Decimal("5"), Decimal("1"), Decimal("25")),
        limit=_int_arg(args, "limit", 5, 1, 10),
        max_pages=_int_arg(args, "max_pages", 1, 1, 4),
        page_size=_int_arg(args, "page_size", 100, 1, 200),
    )


def _unit(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise RuntimeError(f"{field} must be from 0 to 1")
    try:
        if isinstance(value, str):
            number = Decimal(value.strip())
        elif isinstance(value, int):
            number = Decimal(value)
        elif isinstance(value, float):
            number = Decimal(format(value, ".6f"))
        else:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        raise RuntimeError(f"{field} must be from 0 to 1") from None
    if number < 0 or number > 1:
        raise RuntimeError(f"{field} must be from 0 to 1")
    return number


def _wx_gap(value: object) -> Decimal:
    """Optional weather gap in probability points. Logged only. Not an entry gate."""
    if isinstance(value, bool):
        raise RuntimeError("wx_gap_pp must be a number from -100 to 100")
    try:
        if isinstance(value, str):
            number = Decimal(value.strip())
        elif isinstance(value, int):
            number = Decimal(value)
        elif isinstance(value, float):
            number = Decimal(format(value, ".6f"))
        else:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        raise RuntimeError("wx_gap_pp must be a number from -100 to 100") from None
    if number < Decimal("-100") or number > Decimal("100"):
        raise RuntimeError("wx_gap_pp must be a number from -100 to 100")
    return number


def _confidence(value: object) -> Decimal:
    if isinstance(value, bool):
        raise RuntimeError("confidence must be greater than 0 and at most 1")
    try:
        if isinstance(value, str):
            number = Decimal(value.strip())
        elif isinstance(value, int):
            number = Decimal(value)
        elif isinstance(value, float):
            number = Decimal(format(value, ".6f"))
        else:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        raise RuntimeError("confidence must be greater than 0 and at most 1") from None
    if number <= 0 or number > 1:
        raise RuntimeError("confidence must be greater than 0 and at most 1")
    return number


def _evidence(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RuntimeError("evidence must be a string")
    text = " ".join(value.split())
    if not text or "PRIVATE KEY" in text or "-----BEGIN" in text:
        return None
    return text[:240]


def parse_beliefs(raw: object) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RuntimeError("beliefs must be a list")
    if len(raw) > 20:
        raise RuntimeError("beliefs must contain at most 20 items")
    found: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise RuntimeError("each belief must be an object")
        ticker = item.get("ticker")
        if not isinstance(ticker, str) or _TICKER_RE.fullmatch(ticker.strip()) is None:
            raise RuntimeError("ticker must be a market ticker")
        ticker = ticker.strip()
        side_raw = item.get("side")
        if not isinstance(side_raw, str) or side_raw.strip().lower() not in {"yes", "no"}:
            raise RuntimeError("side must be yes or no")
        side = side_raw.strip().lower()
        key = (ticker, side)
        if key in seen:
            raise RuntimeError(f"duplicate belief for {ticker} {side}")
        seen.add(key)
        belief = {
            "ticker": ticker,
            "side": side,
            "confidence": _confidence(item.get("confidence")),
            "evidence": _evidence(item.get("evidence")),
        }
        if "key" in item and item.get("key") not in (None, ""):
            name = item.get("key")
            if not isinstance(name, str) or _KEY_RE.fullmatch(name) is None or name in _VAGUE:
                raise RuntimeError("key must be a concrete snake_case indicator")
            belief["key"] = name
        if "category_tag" in item and item.get("category_tag") not in (None, ""):
            tag = item.get("category_tag")
            if tag not in _CATEGORIES:
                raise RuntimeError("category_tag must be Politics, Crypto, Sports, Weather, Finance, or Macro")
            belief["category_tag"] = tag
        if "corr_group" in item and item.get("corr_group") not in (None, ""):
            group = item.get("corr_group")
            absent = isinstance(group, str) and group.strip().lower() in _CORR_ABSENT
            if not isinstance(group, str) or absent or _KEY_RE.fullmatch(group) is None:
                raise RuntimeError("corr_group must be a snake_case risk driver")
            belief["corr_group"] = group
        if "model_sources" in item and item.get("model_sources") not in (None, ""):
            sources = item.get("model_sources")
            if not isinstance(sources, str) or not (8 <= len(sources.strip()) <= 200):
                raise RuntimeError("model_sources must name the model")
            belief["model_sources"] = sources.strip()
        if "allow_longshot" in item and item.get("allow_longshot") is not None:
            flag = item.get("allow_longshot")
            if not isinstance(flag, bool):
                raise RuntimeError("allow_longshot must be a boolean")
            belief["allow_longshot"] = flag
        if "wx_gap_pp" in item and item.get("wx_gap_pp") not in (None, ""):
            belief["wx_gap_pp"] = _wx_gap(item.get("wx_gap_pp"))
        if "settlement_match_score" in item and item.get("settlement_match_score") not in (None, ""):
            belief["settlement_match_score"] = _unit(item.get("settlement_match_score"), "settlement_match_score")
        found.append(belief)
    return found


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _first_decimal(market: dict, keys: tuple[str, ...]) -> Decimal | None:
    for key in keys:
        number = _decimal(market.get(key))
        if number is not None:
            return number
    return None


def _price(market: dict, dollar_key: str, cent_key: str) -> Decimal | None:
    amount = _decimal(market.get(dollar_key))
    if amount is not None and amount > 0:
        return amount
    cents = market.get(cent_key)
    if isinstance(cents, bool):
        return None
    if isinstance(cents, int) and cents > 0:
        return Decimal(cents) / Decimal(100)
    return None


def _notional(market: dict) -> Decimal:
    amount = _decimal(market.get("notional_value_dollars"))
    if amount is None or amount <= 0:
        return _ONE
    return amount


def _expiry(market: dict, now: datetime) -> tuple[float, str] | None:
    for key in ("close_time", "expected_expiration_time", "latest_expiration_time"):
        parsed = parse_time(market.get(key))
        if parsed is not None:
            return (parsed - now).total_seconds() / 3600, market.get(key) if isinstance(market.get(key), str) else parsed.isoformat()
    return None


def _title(market: dict, side: str) -> str:
    specific = market.get("yes_sub_title") if side == "yes" else market.get("no_sub_title")
    for value in (market.get("title"), specific, market.get("ticker")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _side_quote(market: dict, side: str) -> tuple[Decimal | None, Decimal | None]:
    if side == "yes":
        return _price(market, "yes_ask_dollars", "yes_ask"), _first_decimal(market, ("yes_ask_size_fp",))
    return (
        _price(market, "no_ask_dollars", "no_ask"),
        _first_decimal(market, ("no_ask_size_fp", "yes_bid_size_fp")),
    )


def _tradable(market: dict) -> bool:
    status = market.get("status")
    if status not in _OPEN:
        return False
    kind = market.get("market_type")
    return kind in (None, "binary")


def _empty_counts(scanned: int) -> dict[str, int]:
    return {
        "scanned": scanned,
        "ranked": 0,
        "queued": 0,
        "missing": 0,
        "truncated": 0,
        "skipped_market": 0,
        "skipped_expiry": 0,
        "skipped_liquidity": 0,
        "skipped_price": 0,
        "skipped_confidence": 0,
        "skipped_edge": 0,
        "skipped_risk": 0,
        "skipped_flb": 0,
        "skipped_fee_blind": 0,
        "skipped_settlement": 0,
        "skipped_yes_replicate": 0,
    }


def _volumes(market: dict) -> tuple[Decimal | None, Decimal | None]:
    """24h volume is the weaker proxy. volume / volume_fp is lifetime."""
    volume_24h = _first_decimal(market, ("volume_24h_fp", "volume_24h"))
    lifetime = _first_decimal(market, ("volume_fp", "volume"))
    return volume_24h, lifetime


def _volume_ok(volume_24h: Decimal | None, lifetime: Decimal | None, options: Options) -> bool:
    if lifetime is not None and lifetime >= options.min_lifetime_volume:
        return True
    return volume_24h is not None and volume_24h >= options.min_volume


def _volume_floor(volume_24h: Decimal | None, lifetime: Decimal | None, options: Options) -> str:
    if lifetime is not None and lifetime >= options.min_lifetime_volume:
        return "lifetime"
    return "24h"


def _screen(market: dict, side: str, options: Options, now: datetime) -> tuple[dict | None, str | None]:
    """Shared liquidity, time, and price checks. Confidence is applied by the caller."""
    if not _tradable(market):
        return None, "skipped_market"
    expiry = _expiry(market, now)
    if expiry is None:
        return None, "skipped_expiry"
    hours, close_time = expiry
    if hours < float(options.min_hours) or hours > float(options.max_hours):
        return None, "skipped_expiry"
    volume_24h, lifetime = _volumes(market)
    if not _volume_ok(volume_24h, lifetime, options):
        return None, "skipped_liquidity"
    stake, ask_size = _side_quote(market, side)
    bid = _price(market, "yes_bid_dollars", "yes_bid") if side == "yes" else _price(market, "no_bid_dollars", "no_bid")
    if stake is None or stake < options.min_price or stake >= options.price_ceiling or stake > options.max_price:
        return None, "skipped_price"
    notional = _notional(market)
    profit = notional - stake
    if profit <= 0:
        return None, "skipped_price"
    if ask_size is None or ask_size < options.min_ask_size:
        return None, "skipped_liquidity"
    return {
        "hours": hours,
        "close_time": close_time,
        "volume_24h": volume_24h,
        "volume_lifetime": lifetime,
        "volume_floor": _volume_floor(volume_24h, lifetime, options),
        "stake": stake,
        "bid": bid,
        "ask_size": ask_size,
        "notional": notional,
        "profit": profit,
        "title": _title(market, side),
    }, None


def _row(market: dict, belief: dict, screened: dict, options: Options) -> tuple[dict | None, str | None]:
    confidence: Decimal = belief["confidence"]
    if confidence < options.min_confidence:
        return None, "skipped_confidence"
    implied = screened["stake"] / screened["notional"]
    edge = confidence - implied
    if edge < options.min_edge:
        return None, "skipped_edge"
    text_blob = " ".join(
        part for part in (belief.get("evidence"), belief.get("model_sources")) if isinstance(part, str)
    )
    if _FEE_BLIND.search(text_blob):
        return None, "skipped_fee_blind"
    match = belief.get("settlement_match_score")
    if match is not None and match < 1:
        return None, "skipped_settlement"
    payload_m = series_multiplier(market)
    m_taker, m_maker, fee_source = series_fees(market)
    exec_side = side_exec_for(screened["stake"], screened.get("bid"))
    fee_one = fee_dome_cents(screened["stake"], Decimal(1), multiplier=m_taker)
    fee_est = (
        maker_fee_cents(screened["stake"], Decimal(1), multiplier=m_maker)
        if exec_side == "maker"
        else fee_one
    )
    net_cents = edge_net_cents(
        confidence,
        screened["stake"],
        screened.get("bid"),
        depth=screened.get("ask_size"),
        multiplier=m_taker,
    )
    band = flb_band(screened["stake"])
    dollar_cap = options.max_risk
    if band == "<10¢":
        dollar_cap = min(dollar_cap, Decimal("2"))
        if exec_side == "taker" and not taker_longshot_allowed(
            allow_longshot=belief.get("allow_longshot") is True,
            edge=edge,
        ):
            return None, "skipped_flb"
        if exec_side != "taker" and net_cents < Decimal(8):
            return None, "skipped_flb"
    affordable = int((dollar_cap / screened["stake"]).to_integral_value(rounding=ROUND_DOWN))
    size_cap = int(screened["ask_size"].to_integral_value(rounding=ROUND_DOWN))
    contracts = min(affordable, size_cap)
    if contracts < 1:
        return None, "skipped_risk"
    risk = screened["stake"] * Decimal(contracts)
    payout_ratio = screened["profit"] / screened["stake"]
    score = score_formula(confidence, payout_ratio, screened["stake"])
    days = (Decimal(str(screened["hours"])) / _DAY).quantize(Decimal("0.01"))
    spread = spread_cents_for(screened["stake"], screened.get("bid"))
    hold = hold_to_res_default(days=days, net_cents=net_cents, fee_cents=fee_one)
    maker_near = exec_side == "maker" and fee_one <= MAKER_NEAR_CENTS
    hint = corr_group_hint(belief, market)
    volume_24h = screened.get("volume_24h")
    lifetime = screened.get("volume_lifetime")
    stake_text = _q4(screened["stake"])
    ratio_text = _q4(payout_ratio)
    conf_text = _q4(confidence)
    edge_text = _q4(edge)
    risk_text = _money(risk)
    side = belief["side"]
    ticker = belief["ticker"]
    title = screened["title"]
    parts = [
        (
            f"{side.upper()} on {title} ({ticker}): pay {stake_text} per contract, "
            f"profit {_q4(screened['profit'])} if that side wins (payout ratio {ratio_text})."
        ),
        f"Confidence {conf_text} against the ask is an edge of {edge_text}.",
        f"Cap risk at {risk_text} dollars ({contracts} contracts).",
    ]
    evidence = belief.get("evidence")
    if evidence:
        text = str(evidence)
        if text[-1] not in ".!?":
            text = f"{text}."
        parts.append(text)
    closing = DO_NOT_PLACE[:1].upper() + DO_NOT_PLACE[1:]
    parts.append(f"{closing}.")
    row = {
        "ticker": ticker,
        "event_ticker": market.get("event_ticker") if isinstance(market.get("event_ticker"), str) else None,
        "title": title,
        "side": side,
        "estimated_confidence": conf_text,
        "implied_price": _q4(implied),
        "assumed_edge": edge_text,
        "payout_ratio": ratio_text,
        "stake_needed": stake_text,
        "profit_per_contract": _q4(screened["profit"]),
        "score": format(score, "f"),
        "suggested_contracts": contracts,
        "suggested_max_dollars_risked": risk_text,
        "volume_24h": _q4(volume_24h) if isinstance(volume_24h, Decimal) else None,
        "volume_lifetime": _q4(lifetime) if isinstance(lifetime, Decimal) else None,
        "volume_floor": screened.get("volume_floor"),
        "ask_size": _q4(screened["ask_size"]),
        "side_exec": exec_side,
        "SIDE_EXEC": exec_side,
        "maker_flag": exec_side == "maker",
        "days_to_res": format(days, "f"),
        "DAYS_TO_RES": format(days, "f"),
        "spread_cents": format(spread, "f") if spread is not None else None,
        "depth_at_ask": _q4(screened["ask_size"]),
        "fee_cents_est": format(fee_est, "f"),
        "m_taker": _plain(m_taker),
        "m_maker": _plain(m_maker),
        "fee_m_source": fee_source,
        "series_fee_multiplier": _plain(payload_m) if payload_m is not None else None,
        "corr_group_hint": hint,
        "hold_to_res_default": hold,
        "HOLD_TO_RES_DEFAULT": hold,
        "hours_to_expiry": f"{screened['hours']:.2f}",
        "close_time": screened["close_time"],
        "evidence": evidence,
        "key": belief.get("key"),
        "category_tag": belief.get("category_tag"),
        "corr_group": belief.get("corr_group"),
        "model_sources": belief.get("model_sources"),
        "edge_net_cents": format(net_cents, "f"),
        "flb_band": flb_band(screened["stake"]),
        "fee_entry_cents": format(fee_one, "f"),
        "fee_dome": (
            "ceil(M_taker * 0.07 * contracts * price * (1 - price)) cents; "
            "maker is ceil(M_maker * 0.0175 * contracts * price * (1 - price)). "
            "Series lookup before the score. No settlement fee."
        ),
        "kelly_frac": "0.25",
        "stake_mode": stake_mode_for(screened["stake"]),
        "yes_no_replicate": "clear",
        "rationale": " ".join(parts),
        "do_not_place": DO_NOT_PLACE,
        "_score": score,
        "_edge": edge,
        "_maker_near": maker_near,
    }
    gap = belief.get("wx_gap_pp")
    if isinstance(gap, Decimal):
        row["wx_gap_pp"] = _plain(gap)
    return row, None


def _drop_dominated_yes(ranked: list[dict], by_ticker: dict[str, dict], counts: dict[str, int]) -> list[dict]:
    """Drop a yes buy when the yes mids in that event sum above 1 plus fees."""
    legs: dict[str, list[tuple[str, Decimal, Decimal]]] = {}
    for market in by_ticker.values():
        event = market.get("event_ticker")
        ticker = market.get("ticker")
        if not isinstance(event, str) or not isinstance(ticker, str):
            continue
        bid = _price(market, "yes_bid_dollars", "yes_bid")
        ask = _price(market, "yes_ask_dollars", "yes_ask")
        if bid is None or ask is None:
            continue
        mid = (bid + ask) / Decimal(2)
        m_taker, _m_maker, _source = series_fees(market)
        fee = fee_dome_cents(mid, Decimal(1), multiplier=m_taker) / Decimal(100)
        legs.setdefault(event, []).append((ticker, mid, fee))
    dominated: set[str] = set()
    cheaper: set[str] = set()
    for group in legs.values():
        if len(group) < 2:
            continue
        if sum(mid for _ticker, mid, _fee in group) <= Decimal(1) + sum(fee for _ticker, _mid, fee in group):
            continue
        best = min(group, key=lambda item: (item[1], item[0]))
        cheaper.add(best[0])
        for ticker, _mid, _fee in group:
            if ticker != best[0]:
                dominated.add(ticker)
    kept: list[dict] = []
    for row in ranked:
        if row.get("side") == "yes" and row.get("ticker") in dominated:
            counts["skipped_yes_replicate"] += 1
            continue
        if row.get("side") == "yes" and row.get("ticker") in cheaper:
            row["yes_no_replicate"] = "cheaper_leg"
        kept.append(row)
    return kept


def rank_markets(
    markets: list[dict],
    beliefs: list[dict],
    options: Options,
    now: datetime,
) -> tuple[list[dict], dict[str, int], list[str]]:
    by_ticker: dict[str, dict] = {}
    for market in markets:
        if not isinstance(market, dict):
            continue
        ticker = market.get("ticker")
        if isinstance(ticker, str) and ticker not in by_ticker:
            by_ticker[ticker] = market
    counts = _empty_counts(len(by_ticker))
    missing: list[str] = []
    ranked: list[dict] = []
    for belief in beliefs:
        market = by_ticker.get(belief["ticker"])
        if market is None:
            missing.append(belief["ticker"])
            counts["missing"] += 1
            continue
        screened, reason = _screen(market, belief["side"], options, now)
        if screened is None:
            counts[reason or "skipped_market"] += 1
            continue
        row, reason = _row(market, belief, screened, options)
        if row is None:
            counts[reason or "skipped_market"] += 1
            continue
        ranked.append(row)
    ranked = _drop_dominated_yes(ranked, by_ticker, counts)
    # Maker quotes whose taker fee is within 2 cents sort one score quantum ahead.
    # The displayed score stays estimated_confidence * payout_ratio / stake_needed.
    ranked.sort(
        key=lambda item: (
            -(item["_score"] + (Decimal("0.0001") if item.get("_maker_near") else Decimal(0))),
            -item["_edge"],
            item["ticker"],
            item["side"],
        )
    )
    shown = ranked[: options.limit]
    counts["truncated"] = len(ranked) - len(shown)
    counts["ranked"] = len(shown)
    for index, row in enumerate(shown, start=1):
        row["rank"] = index
        row.pop("_score", None)
        row.pop("_edge", None)
        row.pop("_maker_near", None)
    for row in ranked[options.limit :]:
        row.pop("_score", None)
        row.pop("_edge", None)
        row.pop("_maker_near", None)
    return shown, counts, missing


def research_queue(markets: list[dict], options: Options, now: datetime) -> tuple[list[dict], dict[str, int]]:
    counts = _empty_counts(0)
    queued: list[tuple[Decimal, str, dict]] = []
    seen: set[str] = set()
    for market in markets:
        if not isinstance(market, dict):
            continue
        ticker = market.get("ticker")
        if not isinstance(ticker, str) or ticker in seen:
            continue
        seen.add(ticker)
        counts["scanned"] += 1
        if not _tradable(market):
            counts["skipped_market"] += 1
            continue
        expiry = _expiry(market, now)
        if expiry is None or expiry[0] < float(options.min_hours) or expiry[0] > float(options.max_hours):
            counts["skipped_expiry"] += 1
            continue
        volume_24h, lifetime = _volumes(market)
        if not _volume_ok(volume_24h, lifetime, options):
            counts["skipped_liquidity"] += 1
            continue
        offers: list[tuple[str, Decimal]] = []
        reasons: list[str] = []
        for side in ("yes", "no"):
            screened, reason = _screen(market, side, options, now)
            if screened is None:
                if reason:
                    reasons.append(reason)
                continue
            offers.append((side, screened["stake"]))
        if not offers:
            if reasons and all(reason == "skipped_liquidity" for reason in reasons):
                counts["skipped_liquidity"] += 1
            else:
                counts["skipped_price"] += 1
            continue
        hours, close_time = expiry
        yes_ask, _yes_size = _side_quote(market, "yes")
        no_ask, _no_size = _side_quote(market, "no")
        item = {
            "ticker": ticker,
            "title": _title(market, "yes"),
            "yes_ask": _q4(yes_ask) if yes_ask is not None else None,
            "no_ask": _q4(no_ask) if no_ask is not None else None,
            "volume_24h": _q4(volume_24h) if volume_24h is not None else None,
            "volume_lifetime": _q4(lifetime) if lifetime is not None else None,
            "volume_floor": _volume_floor(volume_24h, lifetime, options),
            "hours_to_expiry": f"{hours:.2f}",
            "close_time": close_time,
            "note": RESEARCH_NOTE,
        }
        queued.append((min(price for _side, price in offers), ticker, item))
    queued.sort(key=lambda item: (item[0], item[1]))
    shown = [item for _price, _ticker, item in queued[: options.limit]]
    counts["queued"] = len(shown)
    counts["truncated"] = len(queued) - len(shown)
    return shown, counts


def filters_payload(options: Options) -> dict[str, str | int]:
    return {
        "formula": FORMULA,
        "min_volume": _plain(options.min_volume),
        "min_lifetime_volume": _plain(options.min_lifetime_volume),
        "min_ask_size": _plain(options.min_ask_size),
        "min_confidence": _plain(options.min_confidence),
        "min_edge": _plain(options.min_edge),
        "min_hours_to_expiry": _plain(options.min_hours),
        "max_hours_to_expiry": _plain(options.max_hours),
        "max_price": _plain(options.max_price),
        "max_risk_dollars": _plain(options.max_risk),
        "limit": options.limit,
        "max_pages": options.max_pages,
        "page_size": options.page_size,
    }
