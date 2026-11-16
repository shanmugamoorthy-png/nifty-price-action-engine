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
