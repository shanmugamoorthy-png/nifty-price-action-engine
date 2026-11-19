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


# V4.10 — Combined options-entry filter experiment

V4.10 preserves the V4.9 contract-pinned, lot-based options backtester and adds a separate filtered run. The original unfiltered baseline run remains available.

- Strong signal-candle confirmation (optional): CALL requires a bullish candle; PUT requires a bearish candle; absolute candle body must be at least 50% of the candle range; close must be in the top 30% for CALL or bottom 30% for PUT. This is evaluated on the completed signal candle before the next-candle option entry.
- Maximum one entry per option direction per trading day (optional): the CALL and PUT engines each allow at most one entry per date.
- Both filters are enabled by default in the V4.10 filtered run.
- The app compares V4.10 results with the uploaded 18-session V4.9 CSV baseline: 94 trades, 35.11% win rate, gross P&L -₹5,018, slippage ₹6,110, brokerage ₹3,760, net P&L -₹14,888. The baseline excludes 6 Nov 2026.
- Comparison is meaningful only when date range, expiry, timeframe, lot size, slippage and brokerage match. The reference baseline is a historical uploaded sample, not a forecast.

The V4.10 filters are research hypotheses, not evidence of profitability. Run against the same saved Dhan option datasets first; do not place live orders based on these results.


# V4.11 — Combined candle confirmation-strength matrix

V4.11 preserves the V4.9 baseline and V4.10 filtered run. It adds a separate matrix testing candle-body strength thresholds of 50%, 60%, and 70%, each combined with a selectable close-location threshold (top/bottom 30%, 25%, or 20%). The one-trade-per-option-direction-per-day limit remains enabled for every V4.11 run. CALL and PUT results are reported separately and combined.

For a controlled comparison, use exactly the same date range, expiry type/code, option timeframe, lot count, historical lot size, premium stop/target, slippage, and brokerage as V4.10. The 50% body + top/bottom 30% row is the closest control. These are research experiments; no strategy is assumed profitable.


# V4.12 — NIFTY Spot volume-confirmed options buying experiment

V4.12 preserves the V4.9 unfiltered baseline, V4.10 combined filter, and V4.11 body-strength matrix. It adds a separate volume-confirmation matrix using the saved NIFTY Spot 5-minute candle volume (not option premium volume).

- Fixed price-action confirmation: candle body >= 60% of candle range; CALL candle must be bullish and close in the top 30%; PUT candle must be bearish and close in the bottom 30%.
- Volume direction confirmation is inherent in the green CALL / red PUT candle requirement.
- RVOL thresholds tested independently: 1.2x, 1.5x and 2.0x.
- RVOL = current completed candle volume / mean volume of the previous 20 completed candles. Current candle is excluded from the rolling average; missing/zero volume and insufficient lookback history do not pass.
- Maximum one entry per direction per trading day remains enabled.
- CALL and PUT are reported separately and together. All costs and lot assumptions are inherited from the same UI controls as previous tests.
- Requires valid `volume` in the saved NIFTY Spot dataset. If volume is missing or unusable, the test stops with an explanatory message; it does not silently substitute zero volume.

Use identical dates, expiry, option timeframe, lots, historical lot size, premium stop/target, slippage and brokerage when comparing V4.12 to V4.11. No live orders are placed. Results are research only.


# V4.13 — Breakout + retest options-buying experiment

V4.13 preserves the V4.9 baseline, V4.10 combined filters, V4.11 confirmation-strength matrix and V4.12 volume matrix. It adds a separate no-volume breakout-retest run.

- Uses NIFTY Spot OHLC signals and actual Dhan fixed-absolute-strike option premium candles.
- Bullish/CE and bearish/PE retests are generated separately and symmetrically.
- A breakout event arms its associated structure level (CHoCH-HL pullback breakout, opening-range boundary, previous-day high/low, or confirmed swing high/low where available).
- A later candle must retest/trade through the level and close back on the breakout side.
- Retest candle must have at least 60% body-to-range, correct candle direction, and close in the top 30% (CALL) or bottom 30% (PUT).
- The breakout candle itself cannot qualify as its own retest. A setup expires after 12 subsequent Spot candles (one hour on 5-minute data); a close back through the level invalidates it.
- Option entry remains on the next available option candle open; absolute strike pinning, premium stop/target, intraday square-off, lot sizing, slippage and brokerage are unchanged.
- Maximum one CALL and one PUT entry per trading day. No volume filter is used in V4.13.
- Retest signals are a new research hypothesis and may materially reduce trade count. No live orders are placed.

Compare V4.13 against V4.11's 60% no-volume control only with identical date coverage, expiry, timeframe, lot size, premium stop/target, slippage and brokerage. The code tests include synthetic bullish and bearish retest cases; live Dhan dataset results must be generated in the deployed app.


# V4.14 — Extended historical validation of V4.13 (entry logic frozen)

V4.14 preserves all prior strategy versions and adds chronological in-sample / out-of-sample reporting for the unchanged V4.13 breakout-retest option strategy.

- Entry rules remain frozen: 60% directional candle body, top/bottom 30% close location, structure breakout followed by a later retest, 12 subsequent 5-minute candle expiry, next option candle entry, maximum one CALL and one PUT per day.
- No volume filter and no parameter optimisation.
- Uses selected saved NIFTY Spot history and matching fixed-strike option premium history.
- User selects the out-of-sample start date. Trades are classified by entry date; earlier trades are in-sample, later trades are out-of-sample.
- Reports separate period summaries, CALL/PUT net P&L, monthly results, drawdown and losing streak, with CSV downloads.
- For credible validation, download at least 6–12 months of matching Spot and option history before running. Verify the historical contract lot size. A single manual lot-size input applies to all selected trades and does not automatically account for historical lot-size changes.
- Use out-of-sample results as the main evidence. Do not repeatedly tune on the same out-of-sample period.

# V4.15 — Download saved CALL / PUT premium CSV files

V4.15 preserves the V4.14 historical-validation logic and adds direct export buttons beside the saved CE/PE dataset status in the Historical download section.

- **Download saved CALL (CE) CSV** exports the existing CALL premium CSV for the selected expiry type, expiry sequence and candle timeframe.
- **Download saved PUT (PE) CSV** exports the existing PUT premium CSV for the same selection.
- **Download coverage report (JSON)** exports the skipped-date coverage manifest when present.
- Exports read the existing files from `DATA_DIR` (on Render, `/var/data` when configured) and do not call Dhan, refresh data, alter files, or change credentials.
- Select the same expiry type, expiry sequence and timeframe used when the data was downloaded. If a file is not present for that selection, the app displays a not-found message rather than exporting a different dataset.


# V4.16 — Append-safe monthly options history builder

V4.16 changes only the historical options data-collection workflow and retains the existing V4.14 validation logic and V4.15 CSV export controls.

- Select no more than 31 calendar days for each options-download batch.
- After the CALL and PUT requests for a batch complete, rows are merged into the existing saved CE/PE files instead of replacing them. The merge key is `(timestamp, absolute strike)`, so multiple strikes at the same candle timestamp are retained, while a newly downloaded duplicate for the same contract/time updates the prior row.
- The existing daily/offset cache remains in use. If a batch is interrupted, rerunning that date range reuses completed cached day/offset requests.
- The skipped-date coverage manifest is merged across batches. Dates reprocessed in the current batch are refreshed in the manifest, while older skipped dates outside the batch are retained.
- For a 12-month test, download one calendar month at a time, then select the full accumulated date range for V4.14 backtesting.

**Important:** The merge is separated by the currently selected expiry type, expiry sequence and timeframe because each combination has its own saved files. Do not switch these settings midway through collecting one intended dataset. Lot sizes remain a manual backtest input and must be checked for each historical contract regime.

Automated tests cover overlapping-row replacement, retention of distinct absolute strikes at the same timestamp, preservation of prior months, and cumulative skipped-date manifests. Live Dhan/Render operation still requires verification in the user's deployed service.

## V4.17 — Read-only local data diagnostics

V4.17 adds a **Run local Spot + CALL + PUT data validation** action in the historical options section. It reads the existing NIFTY Spot, accumulated CALL and accumulated PUT files directly from `DATA_DIR` on the Render service and generates a compact downloadable `nifty_v417_data_validation.json` report. It does not call Dhan, upload the large CSVs, or modify the saved datasets.

The report includes row counts, first/last timestamps, unique dates/timestamps, duplicate timestamp/strike rows, invalid timestamps, strike coverage counts, CALL/PUT date intersections, Spot timestamp matching (exact and within five minutes), skipped-date manifest contents, and a warning that historical lot sizes must be verified independently. Spot-session gaps are evaluated only within the available option-history date span, to avoid incorrectly flagging older Spot-only history.

This is a structural/coverage diagnostic, not proof that every contract was tradable or that the historical lot size is correct. Review the report before running an extended backtest.

## V4.18 — Historical lot-size-aware validation

V4.18 adds a separate extended backtest that applies a manually verified lot-size schedule by effective date. Enter non-overlapping `start_date`, `end_date`, and `lot_size` rows that cover the entire selected test period. The app rejects missing, overlapping, non-positive, or incomplete schedules. It then runs the unchanged V4.13 breakout-retest signals one trading day at a time using that day's configured lot size. CALL and PUT results are reported separately for in-sample and out-of-sample periods, with trade-level lot size and quantity included in the export.

The application does **not** infer historical lot sizes or contract expiry lot sizes from the option candle CSV. Users must verify each effective date and lot size against official NSE contract specifications or broker contract metadata before running. If lot size changed during the test period, enter a new schedule row at the correct effective date. This is a research backtest, not a broker order system.
