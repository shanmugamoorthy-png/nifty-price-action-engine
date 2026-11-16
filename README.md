# NIFTY Pure Price Action Engine V3

## What changed in V3
- Advanced price action: confirmed swing pivots, BOS, CHoCH, liquidity sweeps, opening-range breaks, previous-day high/low breaks.
- Combined entry with no extra breakout-distance filter and no mandatory retest.
- Separate intraday and positional portfolios/backtests.
- Correct cost accounting: slippage affects index-point fills; brokerage is reported separately in INR and is never subtracted from index points.
- Conservative OHLC execution: if stop and target both touch in the same candle, stop is assumed first.
- Five-year Dhan 5-minute downloader in 85-calendar-day batches.
- Streamlit dashboard, Telegram test, continuous polling worker.
- Persistent Render disk for historical data and separate worker state disk.

## Dhan setup
Dhan v2 intraday endpoint: `POST https://api.dhan.co/v2/charts/intraday`
- `securityId=13`
- `exchangeSegment=IDX_I`
- `instrument=INDEX`
- `interval=5`
- `oi=false`

Dhan documents 1/5/15/25/60-minute candles and a maximum 90-calendar-day request window. Five-year availability is subject to the instrument being active and the user's Dhan API/data access.

## Cost accounting
- `gross_points`: raw index points before slippage.
- `net_points`: index points after adverse slippage on entry and exit.
- `brokerage_inr`: user-entered estimated brokerage per completed round trip, shown in INR separately.
- This project does NOT convert NIFTY Spot index points into rupees. It does not include exchange transaction charges, GST, STT, stamp duty or other statutory fees.
- For actual ₹ P&L, backtest a specified NIFTY futures or options contract with its lot size, price, expiry and actual charges. NIFTY Spot is not directly tradable.

## Local run
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill .env locally; never commit it.
streamlit run app.py
```

## Render deployment
1. Push all project files to GitHub. Never upload `.env`, API tokens or Telegram secrets.
2. For a new Render Blueprint, select this repository; `render.yaml` defines the Streamlit web service and separate alert worker.
3. Both services use Starter compute and each has its own 1 GB persistent disk. The worker disk stores alert deduplication state; the web disk stores the CSV.
4. Enter the same Dhan and Telegram secrets for both services when prompted.
5. If updating an existing manually-created service, update its code from GitHub and add a persistent disk at `/var/data` in the Render dashboard. Create the worker service separately or apply the Blueprint.
6. Verify the worker logs show `NIFTY alert worker started`.

A Render disk belongs to one service and cannot be shared between the web service and worker. The worker only needs its own small alert-state database; it fetches current candles directly from Dhan.

## Worker behaviour
The worker polls recent 5-minute historical candles every `POLL_SECONDS` (default 60), ignores unfinished candles, and maintains separate virtual Intraday and Positional alert positions. It sends BUY/SELL entry and EXIT alerts for stop, target, opposite signal and intraday square-off. It is not an exchange websocket and does not guarantee delivery during API outages, expired tokens, market holidays or service downtime. It does not place orders or manage actual broker positions.

## Backtest assumptions
- Strategy signals are calculated on candle close and entered at the next candle open.
- Intraday positions are squared off using the 15:15 candle close (approx 15:20 IST); no new intraday entries after 15:00.
- Positional trades can carry overnight.
- Opposite completed-candle signal exits at next candle open.
- If stop and target are both touched in a candle, stop is filled first.
- A gap through a stop is filled at the candle open.
- One open position per independent portfolio.
- Backtest is educational research, not a promise of future performance.


## V4 setup-isolation research
The `Setup research` tab runs ten separate directional streams:
- Bullish/bearish CHoCH
- Bullish/bearish BOS
- Bullish/bearish liquidity sweeps
- Bullish/bearish opening-range breaks
- Previous-day high/low breaks

Each stream is independently backtested in Intraday and/or Positional mode. Overlapping combined signal labels are not added together. The app exports a summary CSV and the detailed trades for a selected setup. Start with Bullish CHoCH Intraday as the first hypothesis, but do not treat the earlier small sample as proof of an edge.


## V4.1 performance update
The Setup Research tab now runs exactly one selected directional setup per click, rather than ten backtests sequentially. Confirmed price-action events are cached by Streamlit for reuse across setup selections. Select Bullish CHoCH first, run Intraday, then change the setup/mode and run again. This reduces the work per request and avoids the previous all-setups-at-once wait.


## V4.2 — CHoCH + Higher Low confirmation
The primary research hypothesis is a bullish CHoCH followed by a later confirmed higher low:
1. A bullish CHoCH is registered only when a candle closes above the latest confirmed swing high while the previous structure trend is bearish.
2. The baseline is the latest confirmed swing low available at that CHoCH.
3. A later swing low must be confirmed using the configured right-side pivot bars and must be strictly higher than the baseline.
4. The strategy signal is timestamped on the pivot confirmation bar, not the historical pivot bar. Backtest entry is at the next candle open.
5. A lower/equal confirmed low invalidates that CHoCH attempt.

The Setup Research tab defaults to the full saved candle date range. Select `Both (separate)` to run independent Intraday and Positional portfolios and inspect/download their results separately. Costs: slippage is applied adversely to entry and exit fills in index points; estimated brokerage is reported in INR separately, never subtracted from index points. NIFTY Spot remains a non-tradable index reference, so results are index points rather than rupee P&L.


## V4.4 — CHoCH + Higher Low + Pullback-High Breakout
The V4.4 research setup adds a third causal confirmation after the V4.2 sequence:
1. Register bullish CHoCH and store the latest confirmed swing-low baseline.
2. Wait for a later confirmed swing low strictly above that baseline.
3. Identify the actual pivot-low candle (the pullback candle) and store its high.
4. Only after the higher-low confirmation bar, require a later candle to CLOSE above that pullback candle high.
5. Emit the entry signal on that breakout candle close; backtest entry is the next candle open.
6. If price trades back to or below the pre-CHoCH baseline before breakout, cancel the pending setup.

The V4.2 `choch_hl_up` event remains available as a separate comparison setup. The new `choch_hl_breakout_up` stream is the V4.4 default in Setup Research. This is a research hypothesis, not a claim of profitability.


## V4.4 entry-variant comparison

The Setup Research tab now exposes three independent bullish CHoCH + Higher Low entry streams:

1. **V4.4 baseline — Pullback-High Breakout:** close above the high of the confirmed pivot-low candle.
2. **V4.4 Variant 1 — Breakout + Retest Hold:** after the baseline breakout, a later candle must touch the broken level and close back at or above it. The signal is on the retest confirmation candle; entry is next candle open.
3. **V4.4 Variant 2 — Strong Breakout Candle:** baseline breakout must be bullish, real body at least 50% of candle range, and close in the upper 25% of the candle range.

Run each setup separately with identical dates, stop, target, slippage, and brokerage. Compare Intraday and Positional independently. These are research variants, not proof of profitability.
