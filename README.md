# NIFTY Pure Price Action Engine — V4.8.1

V4.7 retains the V4.6 NIFTY Spot backtester and adds a separate NIFTY Options Buying research tab. No automatic orders are placed.

## V4.8.1 option-buying model

- Signal source: saved NIFTY 50 Spot 5-minute OHLC (security ID 13, `IDX_I`, `INDEX`).
- Bullish: V4.5/V4.6 50% Strong Breakout confirmation buys a NIFTY CE.
- Bearish: bearish BOS/CHoCH, opening-range breakdown, previous-day-low breakdown, or bearish liquidity sweep buys a NIFTY PE. This is the current engine's bearish price-action event set; it is not represented as a mirrored bearish CHoCH-HL sequence.
- Option data: Dhan rolling expired-options endpoint `/charts/rollingoption`, underlying security ID `13`, segment `NSE_FNO`, instrument `OPTIDX`, expiry flag `WEEK` or `MONTH`, expiry code `1`/`2`/`3`, ATM strike, option type `CALL`/`PUT`.
- Premium entry: next available option candle open after the completed NIFTY signal candle. Stop/target are measured in option premium points, not NIFTY index points.
- P&L: premium points × user-entered quantity, less premium slippage and entered brokerage/fees estimate. Quantity is in units, not lots; check the applicable contract lot size before converting to lots.
- Intraday only: each trade is closed at session end or at session end; the exact absolute strike is tracked across ATM-relative offset series. Dates exceeding the supported ±10 near-expiry offset range are excluded completely from the dataset. Positional options are deliberately disabled because rolling historical series do not provide a safe fixed expiry/contract identity for a multi-day held position in this implementation.
- No option selling, live orders, or auto execution.

## First test settings

Start with a 10–30 trading-day sample: `WEEK`, expiry code `1` (near expiry), `ATM`, 5-minute option candles, premium stop 20, premium target 40, quantity 1 unit, premium slippage ₹0.50 per side, and brokerage/fees ₹40 per round trip (replace this with your actual cost estimate). Download CE and PE histories, then run the backtest. Expand the date range only after this short test succeeds.

Dhan documents rolling expired-options data for up to five years, requests up to 30 calendar days, and fields including OHLC, strike and spot. The app checks that actual strike values are present before running. See Dhan's market-data reference: https://github.com/dhan-oss/dhanhq-skills/blob/main/skills/dhanhq/references/market-data.md

## Deployment

Use the same Render service and GitHub repository. Keep `DATA_DIR=/var/data`, existing persistent disk, Dhan credentials, Telegram credentials, and all existing V4.6 settings unchanged. New CE/PE CSV files are stored in the configured persistent data directory.

## Testing

Run `pytest -q`. V4.8.1 includes tests for CE entries, PE entries, fixed-strike tracking, daily request caching, and skipping unsupported wide-ATM sessions while continuing later dates.


## V4.7.1 API request compatibility fix

The rolling-options request now sends `interval` as an integer (for example, `5`) to match the Dhan Python SDK parameter typing. This addresses a likely cause of Dhan HTTP 400 / DH-905 validation errors. The exact cause must still be confirmed by a successful request against the user's Dhan account.

## V4.7.2 rolling-options date-window fix

Dhan's rolling-option API counts both `fromDate` and `toDate` inclusively and allows at most 30 calendar dates per request. The application stores the selected UI end date as an exclusive boundary, so the downloader now converts it to an inclusive `toDate` and splits requests into at most 30 inclusive calendar dates. For example, 2026-10-17 through 2026-11-16 is sent as 2026-10-17..2026-11-15, then 2026-11-16..2026-11-16.

This fixes the 31-inclusive-day request that caused DH-905 for the default 2026-10-17 through 2026-11-16 selection. The Dhan response still needs to be verified from the user's account after deployment.

## V4.7.3 — Dhan support-confirmed one-day request rule

The user supplied a Dhan Support email stating that expired-options history can now be fetched for **up to one day of data per API request**. For multiple dates, the client must iterate one date at a time and observe applicable rate limits. V4.7.3 follows that account-specific support instruction (which supersedes the earlier 30-day assumption): every request has identical `fromDate` and `toDate` values.

- CE and PE dates are requested sequentially, one calendar day per request.
- A 1.05-second pause is applied between requests; transient HTTP 429/5xx/network errors receive bounded exponential backoff.
- Each completed date is cached independently under `DATA_DIR/nifty_options_daily_cache/`. If the app is interrupted, rerunning the same date range reuses completed days and continues with missing days.
- Daily requests include OHLC, volume, OI, IV, strike and spot in `requiredData`.
- Update the Render environment variable `DHAN_ACCESS_TOKEN` with the newly regenerated token. Do not paste the token into source code or GitHub. The app's sidebar token field can be used for a temporary session, but the Render environment variable is the persistent setting.

### Deployment

Replace the repository files with this V4.7.3 package and let Render deploy. Keep `DATA_DIR=/var/data`, the existing persistent disk, Dhan client ID, Telegram settings and saved NIFTY Spot history unchanged. After deployment, set the new `DHAN_ACCESS_TOKEN` in Render Environment, save changes and allow the service to restart. The first 31-day CE+PE download may take over a minute because it makes 62 sequential daily requests plus rate-limit pauses. Do not repeatedly click the download button while it is running.

### Verification

Automated tests verify that each request is exactly one calendar date, the interval is an integer, all required option fields are requested, and daily cache prevents already completed days from being downloaded again. A successful live response from the user's Dhan account is still required to confirm account entitlement and payload acceptance.

# V4.8 — Absolute-contract-pinned options backtest

V4.8 corrects the earlier V4.7.3 behavior that closed an open trade when the rolling ATM strike changed. An option buyer continues to hold the same absolute strike and expiry after ATM moves; ATM movement is not an exit condition.

## Contract tracking design

- At each NIFTY signal timestamp, the application reads Dhan's actual `strike` value from the ATM (`offset=0`) option series.
- Entry is the next option candle open for that absolute strike.
- The downloader first requests ATM for each calendar day. It reads the actual ATM strike range returned by Dhan and then requests the required relative series (`ATM+N`, `ATM-N`) to cover the range of ATM movement during that session.
- The merged CE/PE dataset retains multiple rows per timestamp, distinguished by their actual absolute `strike` and requested `offset`.
- While a position is open, the backtester looks up only `(timestamp, original absolute strike)`. A change in the ATM series does not close or replace the position.
- Positions remain intraday and square off at/after 15:15. Overnight holding is still disabled because this endpoint is rolling expiry-relative data and this release does not yet lock an exact expiry ID across multiple sessions.

## Dhan request handling

The user's Dhan Support email specifies one calendar day per expired-options API request. V4.8 retains this account-specific rule: each request has identical `fromDate` and `toDate`. Each daily offset series is cached separately, so a stopped download can resume without repeating completed requests. The app requests offsets only as wide as the actual ATM strike range for that day, capped at ±10 strikes (the documented near-expiry index-options range). If a session exceeds that coverage, the downloader stops with an explicit error instead of silently switching contracts.

This can require more than one API request per day. The app waits 1.05 seconds between successful uncached requests and applies bounded retry delays for transient HTTP 429/5xx/network errors. The first download may therefore take longer than V4.7.3, depending on the day's ATM movement.

## Backtest accounting

- Premium stop and target are in option premium points.
- P&L is `(exit premium - entry premium) × quantity`, less premium slippage and the configured round-trip brokerage/fees estimate.
- Quantity is units, not lots. Set the actual quantity/lot multiple for the contract when evaluating rupee results.
- If stop and target both fall inside one candle, the conservative assumption is that the stop executes first.
- No live orders are placed.

## V4.8 deployment

Replace the GitHub repository files with this V4.8 project. Keep the existing Render service, `DATA_DIR=/var/data`, persistent disk, `DHAN_CLIENT_ID`, regenerated `DHAN_ACCESS_TOKEN`, and Telegram settings. V4.8 writes new files named `nifty_options_v48_call_...csv` and `nifty_options_v48_put_...csv`, so the older V4.7 rolling-only exports are not mistaken for contract-pinned data.

After deployment, select the same short date range and settings, download CE+PE once, then run the V4.8 backtest. Verify that the downloaded CSV has multiple rows per timestamp with `strike` and `offset` columns. Review the generated trade CSV: there should be no `Rolling strike changed` exit reason.

## Validation

Run `pytest -q` and `python -m unittest discover -s tests -v`. The test suite includes a synthetic ATM-roll case verifying that a 24,000 option remains open and is tracked at absolute strike 24,000 even when ATM moves to 24,050.


## V4.8.1 unsupported-date handling

If the intraday ATM strike range on a date exceeds the Dhan near-expiry ATM±10 coverage, the downloader now excludes that entire date for the relevant option type, records the date and reason in a persistent JSON manifest, and continues with later dates. It does not retain partial ATM candles from that unsupported date. The app displays the skipped CALL and PUT dates and warns that the resulting backtest covers only the remaining complete sessions.

## V4.9 lot-based NIFTY options buying

- Choose number of lots and contract lot size separately.
- Total quantity = lots × lot size; all premium P&L and slippage calculations use total option units.
- Default lot size is 65 as an editable starting value for the current test contract. **Confirm the lot size for each historical expiry from the applicable Dhan/NSE contract metadata.** The app does not silently assume that one lot size applies to every expiry.
- Brokerage + fees is an editable round-trip estimate per completed trade.
- This is a historical research backtester only; it does not place orders.
