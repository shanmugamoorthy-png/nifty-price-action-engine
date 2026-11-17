# NIFTY Pure Price Action Engine — V4.7

V4.7 retains the V4.6 NIFTY Spot backtester and adds a separate NIFTY Options Buying research tab. No automatic orders are placed.

## V4.7 option-buying model

- Signal source: saved NIFTY 50 Spot 5-minute OHLC (security ID 13, `IDX_I`, `INDEX`).
- Bullish: V4.5/V4.6 50% Strong Breakout confirmation buys a NIFTY CE.
- Bearish: bearish BOS/CHoCH, opening-range breakdown, previous-day-low breakdown, or bearish liquidity sweep buys a NIFTY PE. This is the current engine's bearish price-action event set; it is not represented as a mirrored bearish CHoCH-HL sequence.
- Option data: Dhan rolling expired-options endpoint `/charts/rollingoption`, underlying security ID `13`, segment `NSE_FNO`, instrument `OPTIDX`, expiry flag `WEEK` or `MONTH`, expiry code `1`/`2`/`3`, ATM strike, option type `CALL`/`PUT`.
- Premium entry: next available option candle open after the completed NIFTY signal candle. Stop/target are measured in option premium points, not NIFTY index points.
- P&L: premium points × user-entered quantity, less premium slippage and entered brokerage/fees estimate. Quantity is in units, not lots; check the applicable contract lot size before converting to lots.
- Intraday only: each trade is closed at session end or before the rolling ATM strike changes. Positional options are deliberately disabled because rolling historical series do not provide a safe fixed expiry/contract identity for a multi-day held position in this implementation.
- No option selling, live orders, or auto execution.

## First test settings

Start with a 10–30 trading-day sample: `WEEK`, expiry code `1` (near expiry), `ATM`, 5-minute option candles, premium stop 20, premium target 40, quantity 1 unit, premium slippage ₹0.50 per side, and brokerage/fees ₹40 per round trip (replace this with your actual cost estimate). Download CE and PE histories, then run the backtest. Expand the date range only after this short test succeeds.

Dhan documents rolling expired-options data for up to five years, requests up to 30 calendar days, and fields including OHLC, strike and spot. The app checks that actual strike values are present before running. See Dhan's market-data reference: https://github.com/dhan-oss/dhanhq-skills/blob/main/skills/dhanhq/references/market-data.md

## Deployment

Use the same Render service and GitHub repository. Keep `DATA_DIR=/var/data`, existing persistent disk, Dhan credentials, Telegram credentials, and all existing V4.6 settings unchanged. New CE/PE CSV files are stored in the configured persistent data directory.

## Testing

Run `python -m unittest discover -s tests -v`. V4.7 includes tests for CE entries, PE entries, premium target/P&L, and forced close when the rolling strike changes.


## V4.7.1 API request compatibility fix

The rolling-options request now sends `interval` as an integer (for example, `5`) to match the Dhan Python SDK parameter typing. This addresses a likely cause of Dhan HTTP 400 / DH-905 validation errors. The exact cause must still be confirmed by a successful request against the user's Dhan account.

## V4.7.2 rolling-options date-window fix

Dhan's rolling-option API counts both `fromDate` and `toDate` inclusively and allows at most 30 calendar dates per request. The application stores the selected UI end date as an exclusive boundary, so the downloader now converts it to an inclusive `toDate` and splits requests into at most 30 inclusive calendar dates. For example, 2026-10-17 through 2026-11-16 is sent as 2026-10-17..2026-11-15, then 2026-11-16..2026-11-16.

This fixes the 31-inclusive-day request that caused DH-905 for the default 2026-10-17 through 2026-11-16 selection. The Dhan response still needs to be verified from the user's account after deployment.
