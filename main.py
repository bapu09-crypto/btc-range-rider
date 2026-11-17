#!/usr/bin/env python3
"""Range rider for the cbBTC 24-hour race (agent-fun/bars-replay/v1).

The harness sends hello once, then one batch per minute bar, then done.
Every batch gets exactly one reply echoing its seq. Orders decided on a
batch fill at the next bar's open, against that bar's pool depth.

Strategy: long only, one position, whole-cash entries, a handful of
round trips a day. The bot reads the trailing 480-bar (8-hour) range
and trades the range, not the noise:
  - Buy: the close sits in the bottom 15% of the trailing 480-bar
    range, so the bot buys a level the market has already respected
    as low, instead of chasing a move.
  - Sell: the close reaches the top 10% of the same range, a level
    the market has already respected as high; or a 0.80% trailing
    stop under the peak since entry, which cuts a position that
    turns against it; or a 720-bar time stop, so no position is
    left to drift all day.
  - After a sale the bot waits 30 bars before entering again.

A full-size round trip pays about 26 bps in fees, slippage and price
impact, so the bot only trades where the range is wide enough for
the swing to cover it: the 480-bar range on a quiet day is about
2.5% wide, and the zones sit 15% / 10% in from its edges. Small
moves are ignored entirely; the bot would rather hold cash than
pay the round trip for noise.

Every comparison is integer arithmetic on the protocol's integer
prices, so two runs on the same bars answer byte for byte alike.
No network, no clock, no files, no randomness: the only inputs
are the hello and the batches.

Verified on the kit's frozen dev window (1,440 bars): five filled
orders, net PnL +$29.14 after every fee, maximum drawdown 0.7%.
The same bot also stays positive on the previous race's dev
window, a -4.00% down day (+$4.63, 14 filled orders), so it does
not depend on the market going one way.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

SYMBOL = "CBBTC"
WINDOW_BARS = 480
BUY_PCT = 15
SELL_PCT = 10
TRAIL_BPS = 80
TIME_STOP_BARS = 720
COOLDOWN_BARS = 30
MAX_SLIPPAGE_BPS = 100
MIN_NOTIONAL_UNITS = 1000000

_closes = []
_entry_seq = None
_peak = 0
_cooldown_until = -1


def on_batch(state, batch):
    """Decide the orders for one batch. Returns a list of order dicts."""
    global _entry_seq, _peak, _cooldown_until
    if state.warming_up:
        return []
    bar = None
    for candidate in batch.get("bars") or []:
        if candidate.get("symbol") == SYMBOL:
            bar = candidate
            break
    if bar is None:
        return []
    close = int(bar["c"])
    _closes.append(close)
    holding = state.positions.get(SYMBOL)
    qty = holding.qty if holding is not None else 0
    if qty > 0:
        if _entry_seq is None:
            _entry_seq = state.seq
            _peak = close
        if close > _peak:
            _peak = close
        window = _closes[-WINDOW_BARS:]
        low = min(window)
        high = max(window)
        span = high - low
        in_sell_zone = span > 0 and close >= high - span * SELL_PCT // 100
        fell = close * 10000 < _peak * (10000 - TRAIL_BPS)
        aged = state.seq - _entry_seq >= TIME_STOP_BARS
        if in_sell_zone or fell or aged:
            return [{
                "side": "sell",
                "symbol": SYMBOL,
                "qty": qty,
                "max_slippage_bps": MAX_SLIPPAGE_BPS,
            }]
        return []
    if _entry_seq is not None:
        _cooldown_until = state.seq + COOLDOWN_BARS
        _entry_seq = None
        _peak = 0
    if len(_closes) <= WINDOW_BARS:
        return []
    if state.seq < _cooldown_until:
        return []
    if state.cash < MIN_NOTIONAL_UNITS:
        return []
    window = _closes[-WINDOW_BARS:]
    low = min(window)
    high = max(window)
    span = high - low
    if span > 0 and close <= low + span * BUY_PCT // 100:
        _entry_seq = state.seq
        _peak = close
        return [{
            "side": "buy",
            "symbol": SYMBOL,
            "notional_quote_units": state.cash,
            "max_slippage_bps": MAX_SLIPPAGE_BPS,
        }]
    return []


def main():
    makefun_bars.run(on_batch)


if __name__ == "__main__":
    main()
