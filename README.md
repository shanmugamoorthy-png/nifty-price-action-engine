# NIFTY Pure Price Action Engine

Streamlit dashboard for NIFTY Spot using DhanHQ v2 historical intraday API.

## Features
- Five-year 5-minute historical downloader (85-calendar-day batches; Dhan limit is 90 days/request).
- Pure price-action setups: confirmed swing breaks (BOS), opening-range breakout, previous-day high/low breaks, breakout retests and false-break rejection.
- Intraday and positional backtesting.
- Separate always-on worker checks completed candles and sends BUY/SELL/EXIT Telegram alerts.
- No indicator, OI or volume dependency for signal generation.
- No automatic order placement.

## Important limitations / assumptions
1. NIFTY Spot is an index, not a tradable security. Backtest output is index points, not option/futures rupee P&L.
2. Five-year availability is subject to Dhan Data API entitlement, token validity, and instrument data availability.
3. Dhan intraday endpoint accepts intervals 1, 5, 15, 25, 60 and up to 90 calendar days per request.
4. Historical data is downloaded to `data/nifty_5min.csv`. Render ephemeral disks are not durable. Use a paid persistent disk or object storage for production data.
5. The Render Blueprint defines a separate always-on background worker for Telegram BUY/SELL/EXIT alerts. Both web and worker services use paid Starter plans and persistent disks. Set `LIVE_MODE` to `Intraday` or `Positional` in the worker environment.
6. Backtest assumptions are simplified: next-candle open fills, fixed stop/target, conservative stop-first if both touched within one bar, one position at a time. Validate with more granular data before real trading.

## Run locally
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in your own credentials in .env; never commit it.
streamlit run app.py
```

## Dhan configuration
- Security ID: 13
- Exchange segment: IDX_I
- Instrument: INDEX
- Interval: 5
- API base: https://api.dhan.co/v2
- Historical endpoint: POST /charts/intraday

## GitHub
Create a private repository, upload project files, and keep `.env` excluded. Add the four secret environment variables in Render dashboard.

## Render
1. Push this folder to GitHub.
2. In Render choose New > Blueprint and select the repository.
3. `render.yaml` declares a paid Starter web service and a paid Starter background worker, each with a persistent disk. Render plan and disk charges apply.
4. Enter secrets when prompted. Configure `LIVE_MODE` as `Intraday` or `Positional` on the worker.
5. Deploy and open the service URL. The worker independently sends alerts while running.

## Telegram
Create a bot with BotFather, send it a message, then obtain your chat ID using Telegram's bot API or a trusted bot utility. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in Render.

## Strategy logic notes
Confirmed swing levels are only made available after `pivot_right` bars have elapsed, reducing look-ahead bias. ORB uses the 09:15–09:30 IST opening range. Signals are based on completed 5-minute candles.
