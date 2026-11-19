import os
import json
from datetime import date, timedelta
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from dotenv import load_dotenv
from config import SECURITY_ID, EXCHANGE_SEGMENT, INSTRUMENT, DATA_DIR
from dhan_api import DhanClient, DhanAPIError
from backtest import run_backtest, run_both_modes
from research import run_setup_isolation
from telegram_alert import send_telegram

load_dotenv()
os.makedirs(DATA_DIR, exist_ok=True)
DATA_FILE = os.path.join(DATA_DIR, "nifty_5min.csv")
st.set_page_config(page_title="NIFTY Pure Price Action V4.24.2", page_icon="📈", layout="wide")
st.title("NIFTY Pure Price Action Engine — V4.24.2 profitability backtest")
st.caption("Advanced market structure • BOS / CHoCH / liquidity sweeps • Dhan API • Telegram • V4.12 Spot-volume research • V4.16 append-safe monthly CE/PE history • V4.17 data diagnostics • V4.18 expiry lot-size-aware validation • V4.20 full-history signal diagnostics • V4.22 resumable reconciliation • V4.23 per-signal rejection reasons • V4.24.2 low-memory CALL/PUT profitability")

with st.sidebar:
    st.header("Dhan connection")
    token = st.text_input("Dhan access token", value=os.getenv("DHAN_ACCESS_TOKEN", ""), type="password")
    client_id = st.text_input("Dhan client ID", value=os.getenv("DHAN_CLIENT_ID", ""), type="password")
    st.caption(f"Security ID {SECURITY_ID} • {EXCHANGE_SEGMENT} • {INSTRUMENT}")
    st.divider()
    st.header("Backtest assumptions")
    stop_points = st.number_input("Stop loss (index points)", min_value=1.0, value=30.0, step=1.0)
    target_points = st.number_input("Target (index points)", min_value=1.0, value=60.0, step=1.0)
    slippage = st.number_input("Slippage (points per side)", min_value=0.0, value=1.0, step=0.25)
    brokerage_inr = st.number_input("Estimated brokerage per round trip (₹)", min_value=0.0, value=20.0, step=5.0)
    st.caption("Brokerage is reported in ₹ separately; it is never subtracted from index points.")
    st.divider()
    st.caption("Signals only. No automatic orders.")

def get_client():
    if not token:
        raise DhanAPIError("Enter Dhan access token in the sidebar or set DHAN_ACCESS_TOKEN.")
    return DhanClient(token, client_id)

def load_saved():
    if not os.path.exists(DATA_FILE):
        return None
    frame = pd.read_csv(DATA_FILE, parse_dates=["timestamp"], index_col="timestamp")
    return frame.sort_index()

def render_results(label, result):
    signals, trades, metrics = result
    st.subheader(label)
    cols = st.columns(4)
    keys = list(metrics.keys())
    for c, key in zip(cols, keys):
        c.metric(key, metrics[key])
    if not trades.empty:
        chart = trades.copy()
        chart["equity_points"] = chart["net_points"].cumsum()
        fig = go.Figure(go.Scatter(x=chart["exit_time"], y=chart["equity_points"], mode="lines", name="Net points"))
        fig.update_layout(height=320, xaxis_title="Exit time", yaxis_title="Cumulative net index points")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(trades, use_container_width=True)
        st.download_button(f"Download {label} trades CSV", trades.to_csv(index=False).encode(),
                           f"{label.lower().replace(' ','_')}_trades.csv", "text/csv")
    else:
        st.info("No trades in the selected period.")

tabs = st.tabs(["Dashboard", "Historical download", "Backtest", "Setup research", "Telegram", "NIFTY Options Buying"])
with tabs[0]:
    st.subheader("Signal design")
    st.markdown("""
    - Confirmed swing pivots; levels become active only after right-side confirmation bars close.
    - BOS and CHoCH from breaks of the latest confirmed swing high/low.
    - Liquidity sweeps: wick through a confirmed swing followed by a close back inside.
    - Opening range breakout: first three 5-minute candles, actionable after 09:30 IST.
    - Previous trading-day high/low breaks.
    - V4.3 baseline: bullish CHoCH, then a later confirmed higher low, then a candle close above the pivot-low candle high. Entry is next candle open.
    - V4.6 retest: after breakout, retain the correct breakout level, then wait up to 48 bars for a later touch and close back at/above it; cancel on structure failure or expiry. Entry is next candle open.
    - V4.4 benchmark / V4.6 threshold tests: bullish breakout candle with body/range at least 40%, 50%, or 60%, and close in the upper 25% of its range. Entry is next candle open.
    - Combined entry = any non-conflicting event above. No indicators, OI or volume filters.
    """)
    st.info("OI and volume are excluded from signal generation. NIFTY Spot is an index, not a tradable contract; results are index points.")
    hist = load_saved()
    if hist is not None:
        st.metric("Saved 5-minute candles", f"{len(hist):,}")
        st.write(f"Coverage: {hist.index.min()} to {hist.index.max()}")
    else:
        st.warning("No historical CSV found on the configured data path yet.")

with tabs[1]:
    st.subheader("Five-year NIFTY Spot 5-minute downloader")
    st.write("Downloads in 85-calendar-day batches, below Dhan's 90-day per-request limit.")
    if st.button("Download / refresh five years from Dhan", type="primary"):
        try:
            client = get_client()
            progress = st.progress(0)
            message = st.empty()
            def update(p, msg):
                progress.progress(p); message.write(msg)
            with st.spinner("Requesting historical candles from Dhan..."):
                hist = client.download_five_years(interval=5, progress=update)
            hist.to_csv(DATA_FILE, index_label="timestamp")
            st.success(f"Saved {len(hist):,} candles to {DATA_FILE}")
            st.write(f"From {hist.index.min()} to {hist.index.max()}")
            st.download_button("Download CSV backup", hist.to_csv(index_label="timestamp").encode(),
                               "nifty_5min.csv", "text/csv")
        except Exception as exc:
            st.error(str(exc))
    saved = load_saved()
    if saved is not None:
        st.write(f"Saved dataset: **{len(saved):,} candles**")
        st.download_button("Download saved CSV", saved.to_csv(index_label="timestamp").encode(),
                           "nifty_5min.csv", "text/csv")

with tabs[2]:
    st.subheader("Independent intraday and positional backtests")
    uploaded = st.file_uploader("Optional: upload Dhan OHLC CSV", type=["csv"])
    hist = None
    if uploaded:
        hist = pd.read_csv(uploaded)
        if "timestamp" in hist.columns:
            hist["timestamp"] = pd.to_datetime(hist["timestamp"])
            hist = hist.set_index("timestamp")
        else:
            hist.index = pd.to_datetime(hist.index)
        hist.columns = [str(c).lower() for c in hist.columns]
    else:
        hist = load_saved()
    if hist is not None and not hist.empty:
        c1, c2 = st.columns(2)
        with c1:
            start_date = st.date_input("Backtest start", value=max(hist.index.min().date(), hist.index.max().date()-timedelta(days=30)))
        with c2:
            end_date = st.date_input("Backtest end", value=hist.index.max().date())
        if start_date > end_date:
            st.error("Start date must be on or before end date.")
        else:
            if st.button("Run BOTH independent backtests", type="primary"):
                common = dict(stop_points=stop_points, target_points=target_points,
                              brokerage_per_round_trip_inr=brokerage_inr,
                              slippage_points_per_side=slippage,
                              start_date=start_date, end_date=end_date)
                with st.spinner("Running independent portfolios..."):
                    intraday, positional = run_both_modes(hist, **common)
                st.session_state["last_intraday"] = intraday
                st.session_state["last_positional"] = positional
                st.success("Both backtests completed.")
            if "last_intraday" in st.session_state:
                render_results("Intraday", st.session_state["last_intraday"])
                render_results("Positional", st.session_state["last_positional"])
                st.caption("Execution: signal candle close, next candle open entry. If stop and target both touch in one candle, stop is assumed first. Slippage is charged on both entry and exit. Brokerage is a separate INR estimate; statutory charges are not included.")
    else:
        st.info("Download the five-year dataset first or upload an OHLC CSV.")



with tabs[3]:
    st.subheader("Setup-isolation research — V4.6")
    st.write("Compare the preserved V4.4 benchmark, V4.6 40%/50%/60% breakout thresholds, and the repaired retest setup. V4.6 adds chronological out-of-sample validation, monthly diagnostics, losing-streak analysis, and a 1:2 target comparison. Run one setup at a time; intraday and positional portfolios are independent.")
    research_data = load_saved()
    if research_data is not None and not research_data.empty:
        from price_action import add_price_action_signals, add_breakout_retest_signals
        from backtest import run_backtest
        from research import SETUPS
        # Prepare cached signals once per dataset. Streamlit hashes the uploaded/saved frame.
        @st.cache_data(show_spinner="Calculating confirmed price-action events once...")
        def cached_research_signals(frame):
            return add_price_action_signals(frame)

        rc1, rc2 = st.columns(2)
        with rc1:
            research_start = st.date_input(
                "Research start date",
                value=research_data.index.min().date(),
                key="research_start_v46"
            )
        with rc2:
            research_end = st.date_input(
                "Research end date", value=research_data.index.max().date(), key="research_end_v46"
            )

        setup_labels = [label for label, col in SETUPS]
        selected_setup = st.selectbox(
            "Select ONE setup",
            setup_labels,
            index=setup_labels.index("V4.3 Baseline — Pullback-High Breakout"),
            key="selected_setup_v46"
        )
        selected_column = dict(SETUPS)[selected_setup]
        research_mode = st.selectbox(
            "Portfolio mode", ["Both (separate)", "Intraday", "Positional"], index=0, key="research_mode_v46"
        )
        st.caption("Five-year default: earliest saved candle through latest saved candle. Both mode runs two independent portfolios, not a combined position.")
        if st.button("Run selected setup", type="primary", key="run_selected_setup_v46"):
            if research_start > research_end:
                st.error("Research start must be on or before research end.")
            else:
                try:
                    with st.spinner("Preparing cached price-action signals..."):
                        signals = cached_research_signals(research_data)
                    progress = st.progress(0, text="Starting selected setup backtest...")
                    common = dict(
                        stop_points=stop_points,
                        target_points=target_points,
                        brokerage_per_round_trip_inr=brokerage_inr,
                        slippage_points_per_side=slippage,
                        start_date=research_start,
                        end_date=research_end,
                        signal_filter=selected_column,
                        signals_precomputed=True,
                    )
                    selected_modes = ("Intraday", "Positional") if research_mode == "Both (separate)" else (research_mode,)
                    results = {}
                    for idx, mode_name in enumerate(selected_modes, start=1):
                        results[mode_name] = run_backtest(signals, mode=mode_name, **common)
                        progress.progress(int(idx / len(selected_modes) * 100),
                                          text=f"Completed {mode_name} portfolio ({idx}/{len(selected_modes)})")
                    st.session_state["v46_research_results"] = {
                        "setup": selected_setup, "column": selected_column, "results": results,
                        "start": research_start, "end": research_end
                    }
                except Exception as exc:
                    st.error(f"Research failed: {exc}")


        if "v46_research_results" in st.session_state:
            saved_result = st.session_state["v46_research_results"]
            st.subheader(f'{saved_result["setup"]} — {saved_result["start"]} to {saved_result["end"]}')
            for mode_name, result_tuple in saved_result["results"].items():
                signal_frame, trades, metrics = result_tuple
                st.markdown(f"#### {mode_name} portfolio")
                detected = int(signal_frame.loc[
                    (signal_frame.index.date >= saved_result["start"]) &
                    (signal_frame.index.date <= saved_result["end"]), selected_column
                ].fillna(False).astype(bool).sum()) if saved_result["column"] in signal_frame.columns else 0
                st.caption(f"Confirmed setup signals in selected date range: {detected:,} | Completed trades: {len(trades):,}")
                metric_cols = st.columns(4)
                for col, key in zip(metric_cols, list(metrics.keys())[:4]):
                    col.metric(key, metrics[key])
                metric_cols2 = st.columns(4)
                for col, key in zip(metric_cols2, list(metrics.keys())[4:8]):
                    col.metric(key, metrics[key])
                st.write(f"Completed trades: {len(trades)}")
                if not trades.empty:
                    st.dataframe(trades, use_container_width=True)
                st.download_button(
                    f"Download {mode_name} trades CSV",
                    trades.to_csv(index=False).encode("utf-8"),
                    f"choch_hl_{mode_name.lower()}_trades.csv",
                    "text/csv",
                    key=f"download_{mode_name.lower()}_v46"
                )
            st.info("V4.3 baseline: close above pivot-low candle high. V4.6 retest fix: retains the breakout level from the confirmed higher-low event, then requires a later retest and close back at/above that level within 48 bars. V4.6 benchmark remains 50%; V4.6 compares 40%, 50%, and 60% bullish candle body/range thresholds, each requiring close in upper 25% of range. All signals are confirmed at candle close and enter next candle open. Costs and NIFTY index-point reporting are unchanged.")

        st.divider()
        st.subheader("V4.6 — Chronological out-of-sample validation")
        st.write("Choose a setup and a chronological split. The training window ends before the out-of-sample window begins; trades are not shuffled. The out-of-sample period is the key validation result, not the training result.")
        from validation import run_walk_forward, monthly_summary, run_target_ratio_comparison
        default_split = research_data.index.min().date() + timedelta(days=int((research_data.index.max().date() - research_data.index.min().date()).days * 0.70))
        wf1, wf2, wf3 = st.columns(3)
        with wf1:
            wf_setup = st.selectbox("Validation setup", setup_labels,
                index=setup_labels.index("V4.5 Strong Breakout — 50% body") if "V4.5 Strong Breakout — 50% body" in setup_labels else 0,
                key="v46_wf_setup")
        with wf2:
            wf_mode = st.selectbox("Validation portfolio", ["Intraday", "Positional"], key="v46_wf_mode")
        with wf3:
            wf_test_start = st.date_input("Out-of-sample starts", value=default_split, key="v46_wf_test_start")
        st.caption("Training: first saved date through the day before the selected test start. Out-of-sample: selected test start through latest saved candle. Keep the same setup, costs and dates when comparing variants.")
        if st.button("Run V4.6 walk-forward validation", type="primary", key="v46_run_walk"):
            if wf_test_start <= research_data.index.min().date() or wf_test_start > research_data.index.max().date():
                st.error("Choose an out-of-sample start after the first saved date and no later than the last saved date.")
            else:
                try:
                    wf_signals = cached_research_signals(research_data)
                    wf_train_end = wf_test_start - timedelta(days=1)
                    train_result, test_result = run_walk_forward(
                        wf_signals, dict(SETUPS)[wf_setup], research_data.index.min().date(), wf_train_end,
                        wf_test_start, research_data.index.max().date(), stop_points=stop_points,
                        target_points=target_points, brokerage_per_round_trip_inr=brokerage_inr,
                        slippage_points_per_side=slippage, mode=wf_mode)
                    rr_table, rr_details = run_target_ratio_comparison(
                        wf_signals, dict(SETUPS)[wf_setup], wf_test_start, research_data.index.max().date(),
                        stop_points=stop_points, baseline_target_points=target_points,
                        brokerage_per_round_trip_inr=brokerage_inr,
                        slippage_points_per_side=slippage, mode=wf_mode)
                    st.session_state["v46_walk_results"] = {
                        "setup": wf_setup, "mode": wf_mode, "train": train_result,
                        "test": test_result, "rr_table": rr_table, "rr_details": rr_details,
                        "test_start": wf_test_start, "test_end": research_data.index.max().date()
                    }
                except Exception as exc:
                    st.error(f"Walk-forward validation failed: {exc}")
        if "v46_walk_results" in st.session_state:
            wr = st.session_state["v46_walk_results"]
            st.markdown(f"### {wr['setup']} — {wr['mode']}")
            st.markdown("#### Training period")
            st.json(wr["train"][2])
            st.markdown(f"#### Out-of-sample period ({wr['test_start']} to {wr['test_end']})")
            st.json(wr["test"][2])
            test_monthly = monthly_summary(wr["test"][1])
            st.markdown("#### Out-of-sample monthly performance")
            if test_monthly.empty:
                st.warning("No completed out-of-sample trades. There is no performance evidence for this period.")
            else:
                st.dataframe(test_monthly, use_container_width=True)
                st.download_button("Download OOS monthly summary CSV", test_monthly.to_csv(index=False).encode("utf-8"),
                    "v46_oos_monthly_summary.csv", "text/csv", key="v46_monthly_download")
            st.markdown("#### Out-of-sample target comparison")
            st.dataframe(wr["rr_table"], use_container_width=True)
            st.caption("The 1:2 row uses target = 2 × stop distance. If your configured target is already twice the stop, both rows intentionally match. Brokerage is shown separately in INR; statutory charges and contract-level rupee P&L are not included.")
            for trade_label, trade_result in wr["rr_details"].items():
                if not trade_result[1].empty:
                    st.download_button(f"Download {trade_label} OOS trades CSV", trade_result[1].to_csv(index=False).encode("utf-8"),
                        f"v46_{trade_label.lower().replace(' ', '_')}_oos_trades.csv", "text/csv",
                        key=f"v46_download_{trade_label.lower().replace(' ', '_')}")
    else:
        st.info("Download the historical 5-minute dataset first. Setup research and chronological out-of-sample validation run on the saved OHLC candle data.")

with tabs[4]:
    st.subheader("Telegram test")
    st.write("Continuous BUY/SELL monitoring runs in the separate Render background worker.")
    if st.button("Send Telegram test message"):
        ok, info = send_telegram("NIFTY Pure Price Action V4.6: Telegram test successful.")
        st.success(info) if ok else st.error(info)
    st.info("Entry alerts are deduplicated by completed candle timestamp. Exit alerts are not yet simulated as live broker-position exits; no orders are placed.")

with tabs[5]:
    st.subheader("V4.10 — NIFTY Options Buying (Lot-Based + Entry Filters)")
    st.write("NIFTY Spot generates the signal; the corresponding rolling NIFTY option premium is used for entry, stop, target and P&L. Bullish setup buys CE; bearish setup buys PE. This module is a research backtester only and never places orders.")
    st.warning("V4.9 supports intraday option buying only. Each trade is pinned to the absolute strike selected at entry. The downloader fetches the ATM-relative offset series needed to follow that same strike as NIFTY moves; an ATM change alone never closes the position. Dates requiring more than ±10 strikes are skipped completely and listed in a coverage warning; later dates continue downloading. Positional holding remains disabled.")
    opt_hist = load_saved()
    if opt_hist is None or opt_hist.empty:
        st.error("Download the NIFTY Spot 5-minute history first. V4.8.1 uses it to generate bullish and bearish signals.")
    else:
        from options_data import download_rolling_option, merge_saved_option_csv, merge_skipped_dates
        from options_backtest import run_option_buying_backtest, calculate_rvol
        oc1, oc2 = st.columns(2)
        with oc1:
            opt_start = st.date_input("Options data start", value=max(opt_hist.index.min().date(), opt_hist.index.max().date()-timedelta(days=30)), key="v48_opt_start")
        with oc2:
            opt_end = st.date_input("Options data end", value=opt_hist.index.max().date(), key="v48_opt_end")
        oc3, oc4, oc5 = st.columns(3)
        with oc3:
            opt_expiry_flag = st.selectbox("Expiry type", ["WEEK", "MONTH"], index=0, key="v48_expiry_flag")
        with oc4:
            opt_expiry_code = st.selectbox("Expiry sequence", [1, 2, 3], index=0, format_func=lambda v: {1:"Near expiry",2:"Next expiry",3:"Far expiry"}[v], key="v48_expiry_code")
        with oc5:
            opt_interval = st.selectbox("Option candle timeframe", [5, 15], index=0, key="v48_interval")
        st.caption("Dhan Support confirmed expired-options history permits only ONE calendar day per API request. V4.8.1 fetches ATM first, then the required ATM±offset series for each day so an absolute strike can be tracked through the session. Each daily/offset response is cached for resume. Near expiry = expiry code 1.")
        st.info("V4.16 monthly-batch mode: download no more than 31 calendar days at a time. Each completed batch is MERGED into the saved CE/PE CSV files; earlier months are not overwritten. Daily cache resumes previously completed requests.")
        opt_strike = st.selectbox("Strike selection", ["ATM"], index=0, key="v48_strike")
        pc1, pc2, pc3, pc4 = st.columns(4)
        with pc1:
            premium_stop = st.number_input("Option premium stop (₹ points)", min_value=0.05, value=20.0, step=1.0, key="v49_premium_stop")
        with pc2:
            premium_target = st.number_input("Option premium target (₹ points)", min_value=0.05, value=40.0, step=1.0, key="v49_premium_target")
        with pc3:
            option_lots = st.number_input("Number of lots", min_value=1, value=1, step=1, key="v49_lots")
        with pc4:
            option_slippage = st.number_input("Premium slippage per unit per side (₹)", min_value=0.0, value=0.50, step=0.05, key="v49_slippage")
        option_lot_size = st.number_input("Contract lot size (units per lot)", min_value=1, value=65, step=1, key="v49_lot_size", help="Confirm the historical contract lot size for the selected expiry from Dhan/NSE contract metadata. Do not assume 65 for every historical expiry.")
        option_qty = int(option_lots) * int(option_lot_size)
        st.info(f"Position quantity: {option_lots} lot(s) × {option_lot_size} units = {option_qty:,} option units. Lot size is an explicit input and must match the selected historical contract.")
        option_brokerage = st.number_input("Brokerage + fees estimate per round trip (₹ per trade)", min_value=0.0, value=40.0, step=5.0, key="v49_brokerage")
        st.markdown("#### V4.10 combined filters")
        st.caption("This is a separate research run. The original V4.9 backtest button and behaviour remain available for comparison.")
        v410_confirm = st.checkbox("Require stronger signal-candle confirmation", value=True, key="v410_confirm",
            help="CALL: green candle, body at least 50% of range, close in top 30%. PUT: red candle, body at least 50% of range, close in bottom 30%.")
        v410_one_per_day = st.checkbox("Maximum one entry per direction per trading day", value=True, key="v410_one_day")
        if opt_start > opt_end:
            st.error("Options start date must be on or before end date.")
        elif opt_start < opt_hist.index.min().date() or opt_end > opt_hist.index.max().date():
            st.warning("The selected option test dates must be within the saved NIFTY Spot candle coverage.")
        else:
            call_file = os.path.join(DATA_DIR, f"nifty_options_v48_call_{opt_expiry_flag.lower()}_{opt_expiry_code}_{opt_interval}m.csv")
            put_file = os.path.join(DATA_DIR, f"nifty_options_v48_put_{opt_expiry_flag.lower()}_{opt_expiry_code}_{opt_interval}m.csv")
            batch_days = (opt_end - opt_start).days + 1
            if batch_days > 31:
                st.warning(f"This selection is {batch_days} calendar days. For reliable monthly batches, select 31 days or fewer before downloading. You can still select the full accumulated period later for backtesting.")
                fetch_options = st.button("Download / refresh fixed-strike Dhan option premiums (CE + PE)", type="primary", key="v49_fetch_options", disabled=True)
            else:
                fetch_options = st.button("Download / refresh fixed-strike Dhan option premiums (CE + PE)", type="primary", key="v49_fetch_options")
            if fetch_options:
                try:
                    opt_client = get_client()
                    prog = st.progress(0, text="Starting Dhan one-day-at-a-time option history download...")
                    def opt_progress(p, msg):
                        prog.progress(min(int(p*100),100), text=msg)
                    with st.spinner("Downloading actual NIFTY option premium candles..."):
                        cache_root = os.path.join(DATA_DIR, "nifty_options_v48_daily_cache")
                        call_df = download_rolling_option(opt_client, opt_start, opt_end+timedelta(days=1), "CALL", opt_expiry_flag, opt_expiry_code, opt_strike, opt_interval, opt_progress, cache_dir=cache_root)
                        put_df = download_rolling_option(opt_client, opt_start, opt_end+timedelta(days=1), "PUT", opt_expiry_flag, opt_expiry_code, opt_strike, opt_interval, opt_progress, cache_dir=cache_root)

                    # V4.16: append/merge this batch into the accumulated fixed-strike CSVs.
                    merged_call = merge_saved_option_csv(call_file, call_df)
                    merged_put = merge_saved_option_csv(put_file, put_df)
                    import json
                    manifest_file = os.path.join(DATA_DIR, f"nifty_options_v48_skipped_{opt_expiry_flag.lower()}_{opt_expiry_code}_{opt_interval}m.json")
                    old_manifest = {}
                    if os.path.exists(manifest_file):
                        try:
                            with open(manifest_file, "r", encoding="utf-8") as mf:
                                old_manifest = json.load(mf)
                        except Exception:
                            old_manifest = {}
                    skipped_manifest = {
                        "CALL": merge_skipped_dates(old_manifest.get("CALL", []), call_df.attrs.get("skipped_dates", []), opt_start, opt_end),
                        "PUT": merge_skipped_dates(old_manifest.get("PUT", []), put_df.attrs.get("skipped_dates", []), opt_start, opt_end),
                        "start": min(str(old_manifest.get("start", opt_start)), str(opt_start)),
                        "end": max(str(old_manifest.get("end", opt_end)), str(opt_end)),
                        "last_batch": {"start": str(opt_start), "end": str(opt_end)},
                    }
                    previous_batches = old_manifest.get("batches", []) if isinstance(old_manifest.get("batches", []), list) else []
                    skipped_manifest["batches"] = previous_batches + [{"start": str(opt_start), "end": str(opt_end)}]
                    with open(manifest_file, "w", encoding="utf-8") as mf:
                        json.dump(skipped_manifest, mf, indent=2)
                    st.session_state["v48_options_downloaded"] = {"call": call_file, "put": put_file, "manifest": manifest_file, "start": str(opt_start), "end": str(opt_end)}
                    st.success(f"Batch merged without replacing previous months. Total saved: CE {len(merged_call):,} rows; PE {len(merged_put):,} rows.")
                    skipped_call = skipped_manifest["CALL"]
                    skipped_put = skipped_manifest["PUT"]
                    if skipped_call or skipped_put:
                        st.warning(f"Coverage gaps retained in the report: {len(skipped_call)} CALL date(s) and {len(skipped_put)} PUT date(s) exceeded the supported ±10 strike range.")
                        if skipped_call:
                            st.write("CALL skipped dates:", ", ".join(x["date"] for x in skipped_call))
                        if skipped_put:
                            st.write("PUT skipped dates:", ", ".join(x["date"] for x in skipped_put))
                    else:
                        st.success("No dates exceeded the supported strike range in the accumulated manifest.")
                    st.caption(f"Accumulated files on persistent disk: {call_file}, {put_file}, and coverage manifest {manifest_file}")
                except Exception as exc:
                    st.error(f"Dhan option-history download failed: {exc}")
            call_exists, put_exists = os.path.exists(call_file), os.path.exists(put_file)
            manifest_file = os.path.join(DATA_DIR, f"nifty_options_v48_skipped_{opt_expiry_flag.lower()}_{opt_expiry_code}_{opt_interval}m.json")
            if call_exists and put_exists:
                st.success("Saved CE and PE premium datasets are available for this expiry setting and timeframe.")
                import json
                if os.path.exists(manifest_file):
                    try:
                        with open(manifest_file, "r", encoding="utf-8") as mf:
                            saved_manifest = json.load(mf)
                        skip_c = saved_manifest.get("CALL", [])
                        skip_p = saved_manifest.get("PUT", [])
                        if skip_c or skip_p:
                            st.warning("These saved datasets have incomplete date coverage. Dates listed below are excluded from both contract entry and exit evaluation; interpret results only over the remaining covered sessions.")
                            if skip_c: st.caption("CALL excluded dates: " + ", ".join(x["date"] for x in skip_c))
                            if skip_p: st.caption("PUT excluded dates: " + ", ".join(x["date"] for x in skip_p))
                    except Exception as manifest_exc:
                        st.warning(f"Could not read skipped-date manifest: {manifest_exc}")

                # V4.15/V4.16: export the accumulated premium datasets directly from the
                # persistent disk. This does not call Dhan or modify the source files.
                st.markdown("#### Download saved option CSV files")
                st.caption("These buttons export the existing CE/PE files for the expiry type, expiry sequence and timeframe selected above. No new Dhan API request is made.")
                dl1, dl2 = st.columns(2)
                if call_exists:
                    with open(call_file, "rb") as f:
                        call_csv_bytes = f.read()
                    dl1.download_button(
                        "Download saved CALL (CE) CSV",
                        data=call_csv_bytes,
                        file_name=os.path.basename(call_file),
                        mime="text/csv",
                        key="v415_download_call_csv",
                    )
                    st.caption(f"CALL file size: {len(call_csv_bytes):,} bytes")
                else:
                    dl1.info("Saved CALL CSV not found for this selection.")
                if put_exists:
                    with open(put_file, "rb") as f:
                        put_csv_bytes = f.read()
                    dl2.download_button(
                        "Download saved PUT (PE) CSV",
                        data=put_csv_bytes,
                        file_name=os.path.basename(put_file),
                        mime="text/csv",
                        key="v415_download_put_csv",
                    )
                    st.caption(f"PUT file size: {len(put_csv_bytes):,} bytes")
                else:
                    dl2.info("Saved PUT CSV not found for this selection.")
                if os.path.exists(manifest_file):
                    with open(manifest_file, "rb") as f:
                        coverage_bytes = f.read()
                    st.download_button(
                        "Download coverage report (JSON)",
                        data=coverage_bytes,
                        file_name=os.path.basename(manifest_file),
                        mime="application/json",
                        key="v415_download_coverage_json",
                    )

                # V4.17: on-Render diagnostics; reads existing files only and never uploads large CSVs.
                st.markdown("#### V4.17 — Local saved-data validation")
                st.caption("Creates a small JSON report on Render from the saved Spot, CALL and PUT files. Read-only: no Dhan API calls, no CSV downloads, and no changes to your saved datasets.")
                if st.button("Run local Spot + CALL + PUT data validation", key="v417_run_diagnostics"):
                    try:
                        from data_diagnostics import build_diagnostic_report
                        with st.spinner("Checking saved CSV files on Render..."):
                            report = build_diagnostic_report(opt_hist, call_file, put_file, manifest_file)
                        st.session_state["v417_diagnostic_report"] = report
                        st.success("Validation report generated. Your large CSV files remain on Render.")
                    except Exception as diagnostic_exc:
                        st.error(f"Data validation could not complete: {diagnostic_exc}")
                diagnostic_report = st.session_state.get("v417_diagnostic_report")
                if diagnostic_report:
                    dcall, dput = diagnostic_report.get("CALL", {}), diagnostic_report.get("PUT", {})
                    dc1, dc2, dc3 = st.columns(3)
                    dc1.metric("Spot candles", f"{diagnostic_report.get('spot', {}).get('rows', 0):,}")
                    dc2.metric("CALL rows", f"{dcall.get('rows', 0):,}")
                    dc3.metric("PUT rows", f"{dput.get('rows', 0):,}")
                    st.write(f"**CALL date range:** {dcall.get('first_date') or 'Not available'} to {dcall.get('last_date') or 'Not available'}")
                    st.write(f"**PUT date range:** {dput.get('first_date') or 'Not available'} to {dput.get('last_date') or 'Not available'}")
                    st.write(f"**Dates present in both CALL and PUT:** {len(diagnostic_report.get('call_put_date_intersection', []))}")
                    st.write(f"**CALL dates missing from PUT:** {len(diagnostic_report.get('call_dates_missing_from_put', []))}")
                    st.write(f"**PUT dates missing from CALL:** {len(diagnostic_report.get('put_dates_missing_from_call', []))}")
                    st.write(f"**Exact Spot timestamp match:** CALL {dcall.get('exact_timestamp_match_pct', 'N/A')}% • PUT {dput.get('exact_timestamp_match_pct', 'N/A')}%")
                    st.write(f"**Within 5-minute Spot match:** CALL {dcall.get('within_5m_match_pct', 'N/A')}% • PUT {dput.get('within_5m_match_pct', 'N/A')}%")
                    st.download_button("Download compact V4.17 validation report (JSON)", data=json.dumps(diagnostic_report, indent=2, default=str), file_name="nifty_v417_data_validation.json", mime="application/json", key="v417_download_diagnostic_json")
                    st.warning("This report validates file structure and timestamp coverage, not historical lot size or complete contract tradability. Confirm historical lot sizes separately before trusting rupee P&L.")

                if st.button("Run V4.9 baseline (no extra filters) option backtest", type="primary", key="v49_run_options"):
                    try:
                        with st.spinner("Calculating causal NIFTY signals and matching option contracts..."):
                            spot_signals = add_price_action_signals(opt_hist)
                            call_bars = pd.read_csv(call_file, parse_dates=["timestamp"], index_col="timestamp").sort_index()
                            put_bars = pd.read_csv(put_file, parse_dates=["timestamp"], index_col="timestamp").sort_index()
                            # Ensure timezone-naive IST timestamps on both datasets.
                            for frame in (call_bars, put_bars):
                                if getattr(frame.index, "tz", None) is not None:
                                    frame.index = frame.index.tz_convert("Asia/Kolkata").tz_localize(None)
                                frame.columns = [str(c).lower() for c in frame.columns]
                            call_bars = call_bars.loc[(call_bars.index.date >= opt_start) & (call_bars.index.date <= opt_end)]
                            put_bars = put_bars.loc[(put_bars.index.date >= opt_start) & (put_bars.index.date <= opt_end)]
                            spot_signals = spot_signals.loc[(spot_signals.index.date >= opt_start) & (spot_signals.index.date <= opt_end)]
                            # The 50% bullish breakout is the selected long-CE setup; bearish PE uses the engine's bearish price-action events.
                            spot_signals["buy_setup"] = spot_signals.get("choch_hl_strong_breakout_50_up", False)
                            # bearish price-action events (BOS/CHoCH, ORB, prior-day break, liquidity sweep)
                            bearish_cols = [c for c in ["bos_down", "choch_down", "orb_down", "prev_day_break_down", "liquidity_sweep_down"] if c in spot_signals.columns]
                            spot_signals["sell_setup"] = spot_signals[bearish_cols].fillna(False).any(axis=1) if bearish_cols else False
                            call_trades, call_metrics = run_option_buying_backtest(
                                spot_signals, call_bars, "CALL", premium_stop, premium_target,
                                quantity=int(option_qty), slippage_per_side=option_slippage,
                                brokerage_round_trip=option_brokerage)
                            put_trades, put_metrics = run_option_buying_backtest(
                                spot_signals, put_bars, "PUT", premium_stop, premium_target,
                                quantity=int(option_qty), slippage_per_side=option_slippage,
                                brokerage_round_trip=option_brokerage)
                            st.session_state["v48_option_results"] = {
                                "call": (call_trades, call_metrics), "put": (put_trades, put_metrics),
                                "start": opt_start, "end": opt_end, "expiry": f"{opt_expiry_flag} / code {opt_expiry_code}",
                                "timeframe": opt_interval, "quantity": int(option_qty),
                                "lots": int(option_lots), "lot_size": int(option_lot_size),
                            }
                    except Exception as exc:
                        st.error(f"V4.9 baseline option backtest failed: {exc}")
                if st.button("Run V4.10 BOTH filters (confirmation + one trade/day)", type="primary", key="v410_run_options"):
                    try:
                        with st.spinner("Running V4.10 filtered option backtest..."):
                            spot_signals = add_price_action_signals(opt_hist)
                            call_bars = pd.read_csv(call_file, parse_dates=["timestamp"], index_col="timestamp").sort_index()
                            put_bars = pd.read_csv(put_file, parse_dates=["timestamp"], index_col="timestamp").sort_index()
                            for frame in (call_bars, put_bars):
                                if getattr(frame.index, "tz", None) is not None:
                                    frame.index = frame.index.tz_convert("Asia/Kolkata").tz_localize(None)
                                frame.columns = [str(c).lower() for c in frame.columns]
                            call_bars = call_bars.loc[(call_bars.index.date >= opt_start) & (call_bars.index.date <= opt_end)]
                            put_bars = put_bars.loc[(put_bars.index.date >= opt_start) & (put_bars.index.date <= opt_end)]
                            spot_signals = spot_signals.loc[(spot_signals.index.date >= opt_start) & (spot_signals.index.date <= opt_end)].copy()
                            spot_signals["buy_setup"] = spot_signals.get("choch_hl_strong_breakout_50_up", False)
                            bearish_cols = [c for c in ["bos_down", "choch_down", "orb_down", "prev_day_break_down", "liquidity_sweep_down"] if c in spot_signals.columns]
                            spot_signals["sell_setup"] = spot_signals[bearish_cols].fillna(False).any(axis=1) if bearish_cols else False
                            filter_args = {
                                "max_trades_per_day": 1 if v410_one_per_day else None,
                                "require_candle_confirmation": bool(v410_confirm),
                                "confirmation_body_min": 0.50,
                                "confirmation_close_edge": 0.70,
                            }
                            call_trades, call_metrics = run_option_buying_backtest(
                                spot_signals, call_bars, "CALL", premium_stop, premium_target,
                                quantity=int(option_qty), slippage_per_side=option_slippage,
                                brokerage_round_trip=option_brokerage, **filter_args)
                            put_trades, put_metrics = run_option_buying_backtest(
                                spot_signals, put_bars, "PUT", premium_stop, premium_target,
                                quantity=int(option_qty), slippage_per_side=option_slippage,
                                brokerage_round_trip=option_brokerage, **filter_args)
                            st.session_state["v410_option_results"] = {
                                "call": (call_trades, call_metrics), "put": (put_trades, put_metrics),
                                "start": opt_start, "end": opt_end, "expiry": f"{opt_expiry_flag} / code {opt_expiry_code}",
                                "timeframe": opt_interval, "quantity": int(option_qty),
                                "lots": int(option_lots), "lot_size": int(option_lot_size),
                                "confirmation": bool(v410_confirm), "one_per_day": bool(v410_one_per_day),
                            }
                    except Exception as exc:
                        st.error(f"V4.10 filtered option backtest failed: {exc}")
            st.markdown("#### V4.11 combined confirmation-strength experiment")
            st.caption("Keeps V4.10 one-trade-per-direction/day rule. Tests candle-body strength at 50%, 60%, and 70%, combined with the same close-location rule (CALL top 30%, PUT bottom 30%).")
            v411_close_edge = st.selectbox("V4.11 close-location threshold", [0.70, 0.75, 0.80], index=0,
                format_func=lambda v: {0.70:"Top/bottom 30%",0.75:"Top/bottom 25%",0.80:"Top/bottom 20%"}[v],
                key="v411_close_edge")
            if st.button("Run V4.11 combined test matrix (50% / 60% / 70%)", type="primary", key="v411_run_options"):
                try:
                    with st.spinner("Running V4.11 strength matrix on the same saved Dhan datasets..."):
                        spot_signals = add_price_action_signals(opt_hist)
                        call_bars = pd.read_csv(call_file, parse_dates=["timestamp"], index_col="timestamp").sort_index()
                        put_bars = pd.read_csv(put_file, parse_dates=["timestamp"], index_col="timestamp").sort_index()
                        for frame in (call_bars, put_bars):
                            if getattr(frame.index, "tz", None) is not None:
                                frame.index = frame.index.tz_convert("Asia/Kolkata").tz_localize(None)
                            frame.columns = [str(c).lower() for c in frame.columns]
                        call_bars = call_bars.loc[(call_bars.index.date >= opt_start) & (call_bars.index.date <= opt_end)]
                        put_bars = put_bars.loc[(put_bars.index.date >= opt_start) & (put_bars.index.date <= opt_end)]
                        spot_signals = spot_signals.loc[(spot_signals.index.date >= opt_start) & (spot_signals.index.date <= opt_end)].copy()
                        spot_signals["buy_setup"] = spot_signals.get("choch_hl_strong_breakout_50_up", False)
                        bearish_cols = [c for c in ["bos_down", "choch_down", "orb_down", "prev_day_break_down", "liquidity_sweep_down"] if c in spot_signals.columns]
                        spot_signals["sell_setup"] = spot_signals[bearish_cols].fillna(False).any(axis=1) if bearish_cols else False
                        matrix = {}
                        for body_min in (0.50, 0.60, 0.70):
                            call_t, call_m = run_option_buying_backtest(
                                spot_signals, call_bars, "CALL", premium_stop, premium_target,
                                quantity=int(option_qty), slippage_per_side=option_slippage,
                                brokerage_round_trip=option_brokerage, max_trades_per_day=1,
                                require_candle_confirmation=True, confirmation_body_min=body_min,
                                confirmation_close_edge=float(v411_close_edge))
                            put_t, put_m = run_option_buying_backtest(
                                spot_signals, put_bars, "PUT", premium_stop, premium_target,
                                quantity=int(option_qty), slippage_per_side=option_slippage,
                                brokerage_round_trip=option_brokerage, max_trades_per_day=1,
                                require_candle_confirmation=True, confirmation_body_min=body_min,
                                confirmation_close_edge=float(v411_close_edge))
                            matrix[f"{int(body_min*100)}%"] = {"call":(call_t,call_m),"put":(put_t,put_m)}
                        st.session_state["v411_option_results"] = {
                            "matrix":matrix, "start":opt_start, "end":opt_end,
                            "expiry":f"{opt_expiry_flag} / code {opt_expiry_code}",
                            "timeframe":opt_interval, "quantity":int(option_qty),
                            "lots":int(option_lots), "lot_size":int(option_lot_size),
                            "close_edge":float(v411_close_edge), "stop":float(premium_stop),
                            "target":float(premium_target), "slippage":float(option_slippage),
                            "brokerage":float(option_brokerage)}
                except Exception as exc:
                    st.error(f"V4.11 combined test failed: {exc}")
            if "v411_option_results" in st.session_state:
                vr11 = st.session_state["v411_option_results"]
                st.markdown("### V4.11 combined test matrix")
                st.caption(f"{vr11['start']} to {vr11['end']} • {vr11['expiry']} • {vr11['timeframe']}m • {vr11['lots']} lot(s) × {vr11['lot_size']} = {vr11['quantity']} units • close location top/bottom {int((1-vr11['close_edge'])*100)}% • one trade per direction/day.")
                summary_rows = []
                for strength, sides in vr11["matrix"].items():
                    all_trades = [t for t,_ in sides.values() if not t.empty]
                    combined = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
                    summary_rows.append({"Body strength":strength,"Trades":len(combined),
                        "Win rate":f"{(combined['net_pnl_inr']>0).mean()*100:.2f}%" if not combined.empty else "0.00%",
                        "Gross P&L":float(combined["gross_pnl_inr"].sum()) if not combined.empty else 0.0,
                        "Slippage":float(combined["slippage_inr"].sum()) if not combined.empty else 0.0,
                        "Brokerage":float(combined["brokerage_inr"].sum()) if not combined.empty else 0.0,
                        "Net P&L":float(combined["net_pnl_inr"].sum()) if not combined.empty else 0.0})
                    for side_name, side_key in (("CALL","call"),("PUT","put")):
                        side_trades, side_metrics = sides[side_key]
                        if not side_trades.empty:
                            st.markdown(f"#### {strength} body — {side_name}")
                            st.dataframe(side_trades, use_container_width=True)
                            st.download_button(f"Download V4.11 {strength} {side_name} CSV",
                                side_trades.to_csv(index=False).encode("utf-8"),
                                f"v411_{strength.replace('%','')}_{side_name.lower()}_trades.csv",
                                "text/csv", key=f"v411_dl_{strength}_{side_key}")
                st.dataframe(pd.DataFrame(summary_rows), hide_index=True, use_container_width=True)
                st.caption("Compare each row against V4.10 using identical dates, expiry, timeframe, lots, lot size, stop, target, slippage and brokerage. The 50% row is the closest V4.10-equivalent control; only the confirmation threshold changes.")
            st.markdown("#### V4.12 — 60% price action + Spot volume confirmation")
            st.caption("Uses NIFTY Spot 5-minute volume. Each signal candle must be direction-confirmed (green CALL / red PUT), have at least 60% body-to-range, close in the top/bottom 30%, and meet the selected RVOL threshold. RVOL = current completed candle volume ÷ average volume of the PREVIOUS 20 completed candles. The current candle is excluded from its average.")
            st.info("Volume is taken from saved NIFTY Spot candles, not option premium volume. At least 20 prior valid volume candles are required. Missing, zero or invalid volume is not treated as confirmation.")
            if st.button("Run V4.12 volume matrix (RVOL 1.2x / 1.5x / 2.0x)", type="primary", key="v412_run_options"):
                try:
                    with st.spinner("Calculating V4.12 volume-confirmed trades from saved NIFTY Spot and option candles..."):
                        spot_all = add_price_action_signals(opt_hist)
                        if "volume" not in spot_all.columns:
                            raise ValueError("Saved NIFTY Spot dataset has no volume column. Download fresh 5-minute Spot candles from Dhan before running V4.12.")
                        spot_all["volume"] = pd.to_numeric(spot_all["volume"], errors="coerce")
                        if int((spot_all["volume"] > 0).sum()) < 21:
                            raise ValueError("Insufficient valid NIFTY Spot volume. V4.12 needs at least 21 positive-volume candles.")
                        spot_all["v412_rvol"] = calculate_rvol(spot_all["volume"], lookback=20).to_numpy()
                        spot_signals_base = spot_all.loc[(spot_all.index.date >= opt_start) & (spot_all.index.date <= opt_end)].copy()
                        spot_signals_base["buy_setup"] = spot_signals_base.get("choch_hl_strong_breakout_50_up", False)
                        bearish_cols = [c for c in ["bos_down", "choch_down", "orb_down", "prev_day_break_down", "liquidity_sweep_down"] if c in spot_signals_base.columns]
                        spot_signals_base["sell_setup"] = spot_signals_base[bearish_cols].fillna(False).any(axis=1) if bearish_cols else False
                        call_bars = pd.read_csv(call_file, parse_dates=["timestamp"], index_col="timestamp").sort_index()
                        put_bars = pd.read_csv(put_file, parse_dates=["timestamp"], index_col="timestamp").sort_index()
                        for frame in (call_bars, put_bars):
                            if getattr(frame.index, "tz", None) is not None:
                                frame.index = frame.index.tz_convert("Asia/Kolkata").tz_localize(None)
                            frame.columns = [str(c).lower() for c in frame.columns]
                        call_bars = call_bars.loc[(call_bars.index.date >= opt_start) & (call_bars.index.date <= opt_end)]
                        put_bars = put_bars.loc[(put_bars.index.date >= opt_start) & (put_bars.index.date <= opt_end)]
                        v412_matrix = {}
                        for threshold in (1.2, 1.5, 2.0):
                            filtered = spot_signals_base.loc[spot_signals_base["v412_rvol"] >= threshold].copy()
                            call_t, call_m = run_option_buying_backtest(
                                filtered, call_bars, "CALL", premium_stop, premium_target,
                                quantity=int(option_qty), slippage_per_side=option_slippage,
                                brokerage_round_trip=option_brokerage, max_trades_per_day=1,
                                require_candle_confirmation=True, confirmation_body_min=0.60,
                                confirmation_close_edge=0.70)
                            put_t, put_m = run_option_buying_backtest(
                                filtered, put_bars, "PUT", premium_stop, premium_target,
                                quantity=int(option_qty), slippage_per_side=option_slippage,
                                brokerage_round_trip=option_brokerage, max_trades_per_day=1,
                                require_candle_confirmation=True, confirmation_body_min=0.60,
                                confirmation_close_edge=0.70)
                            v412_matrix[f"{threshold:.1f}x"] = {"call":(call_t,call_m),"put":(put_t,put_m)}
                        st.session_state["v412_option_results"] = {
                            "matrix":v412_matrix, "start":opt_start, "end":opt_end,
                            "expiry":f"{opt_expiry_flag} / code {opt_expiry_code}",
                            "timeframe":opt_interval, "quantity":int(option_qty),
                            "lots":int(option_lots), "lot_size":int(option_lot_size),
                            "stop":float(premium_stop), "target":float(premium_target),
                            "slippage":float(option_slippage), "brokerage":float(option_brokerage)}
                except Exception as exc:
                    st.error(f"V4.12 volume backtest failed: {exc}")
            st.markdown("#### V4.13 — 60% breakout + retest experiment")
            st.caption("No volume filter. A completed breakout arms a structure level; a later candle must retest the level and close back beyond it with a directional 60% body and top/bottom 30% close. Entry is on the next option candle. Retest expires after 12 Spot candles (one hour on 5-minute data).")
            if st.button("Run V4.13 breakout + retest option backtest", type="primary", key="v413_run_options"):
                try:
                    with st.spinner("Building causal breakout-retest signals and matching fixed-strike option candles..."):
                        spot_signals = add_price_action_signals(opt_hist)
                        spot_signals = add_breakout_retest_signals(
                            spot_signals, body_min=0.60, close_edge=0.70, max_wait_bars=12)
                        spot_signals = spot_signals.loc[
                            (spot_signals.index.date >= opt_start) & (spot_signals.index.date <= opt_end)].copy()
                        spot_signals["buy_setup"] = spot_signals["v413_buy_retest"].fillna(False)
                        spot_signals["sell_setup"] = spot_signals["v413_sell_retest"].fillna(False)
                        spot_signals["signal_reason"] = ""
                        spot_signals.loc[spot_signals["buy_setup"], "signal_reason"] = "v413_bullish_breakout_retest_60"
                        spot_signals.loc[spot_signals["sell_setup"], "signal_reason"] = "v413_bearish_breakout_retest_60"
                        call_bars = pd.read_csv(call_file, parse_dates=["timestamp"], index_col="timestamp").sort_index()
                        put_bars = pd.read_csv(put_file, parse_dates=["timestamp"], index_col="timestamp").sort_index()
                        for frame in (call_bars, put_bars):
                            if getattr(frame.index, "tz", None) is not None:
                                frame.index = frame.index.tz_convert("Asia/Kolkata").tz_localize(None)
                            frame.columns = [str(c).lower() for c in frame.columns]
                        call_bars = call_bars.loc[(call_bars.index.date >= opt_start) & (call_bars.index.date <= opt_end)]
                        put_bars = put_bars.loc[(put_bars.index.date >= opt_start) & (put_bars.index.date <= opt_end)]
                        call_t, call_m = run_option_buying_backtest(
                            spot_signals, call_bars, "CALL", premium_stop, premium_target,
                            quantity=int(option_qty), slippage_per_side=option_slippage,
                            brokerage_round_trip=option_brokerage, max_trades_per_day=1)
                        put_t, put_m = run_option_buying_backtest(
                            spot_signals, put_bars, "PUT", premium_stop, premium_target,
                            quantity=int(option_qty), slippage_per_side=option_slippage,
                            brokerage_round_trip=option_brokerage, max_trades_per_day=1)
                        st.session_state["v413_option_results"] = {
                            "call":(call_t,call_m), "put":(put_t,put_m),
                            "start":opt_start, "end":opt_end,
                            "expiry":f"{opt_expiry_flag} / code {opt_expiry_code}",
                            "timeframe":opt_interval, "quantity":int(option_qty),
                            "lots":int(option_lots), "lot_size":int(option_lot_size),
                            "stop":float(premium_stop), "target":float(premium_target),
                            "slippage":float(option_slippage), "brokerage":float(option_brokerage)}
                except Exception as exc:
                    st.error(f"V4.13 breakout-retest backtest failed: {exc}")
            if "v413_option_results" in st.session_state:
                vr13 = st.session_state["v413_option_results"]
                st.markdown("### V4.13 breakout + retest results")
                st.caption(f"{vr13['start']} to {vr13['end']} • {vr13['expiry']} • option {vr13['timeframe']}m • {vr13['lots']} lot(s) × {vr13['lot_size']} = {vr13['quantity']} units • 60% body / 30% close-location • no volume filter • one trade per direction/day.")
                all13 = []
                for side_name, side_key in (("CALL","call"),("PUT","put")):
                    trades13, metrics13 = vr13[side_key]
                    st.markdown(f"#### {side_name}")
                    mcols13 = st.columns(4)
                    for col, metric_name in zip(mcols13, list(metrics13.keys())[:4]):
                        col.metric(metric_name, metrics13[metric_name])
                    if not trades13.empty:
                        all13.append(trades13)
                        st.dataframe(trades13, use_container_width=True)
                        st.download_button(f"Download V4.13 {side_name} CSV",
                            trades13.to_csv(index=False).encode("utf-8"),
                            f"v413_{side_key}_breakout_retest_trades.csv", "text/csv",
                            key=f"v413_dl_{side_key}")
                    else:
                        st.caption("No completed trades for this side.")
                if all13:
                    joined13 = pd.concat(all13, ignore_index=True).sort_values("exit_time")
                    st.markdown("#### Combined V4.13 summary")
                    st.dataframe(pd.DataFrame([{
                        "Trades":len(joined13),
                        "Win rate":f"{(joined13['net_pnl_inr']>0).mean()*100:.2f}%",
                        "Gross P&L ₹":joined13["gross_pnl_inr"].sum(),
                        "Slippage ₹":joined13["slippage_inr"].sum(),
                        "Brokerage ₹":joined13["brokerage_inr"].sum(),
                        "Net P&L ₹":joined13["net_pnl_inr"].sum()
                    }]), hide_index=True, use_container_width=True)
                else:
                    st.info("V4.13 generated no completed trades. Do not loosen the rules simply to force trades.")
                st.caption("Compare with V4.11 60% no-volume control only when dates, expiry, timeframe, lot size, premium stop/target, slippage and brokerage are identical. Historical test only; no orders are placed.")
            st.markdown("#### V4.14 — Extended historical validation (V4.13 rules unchanged)")
            st.caption("Runs the same breakout + retest entry logic over a longer saved history, then separates earlier in-sample trades from later out-of-sample trades. No parameter optimisation is performed.")
            default_oos = opt_start + timedelta(days=max(30, (opt_end-opt_start).days*7//10))
            v414_oos_start = st.date_input("V4.14 out-of-sample start date", value=min(default_oos,opt_end), min_value=opt_start, max_value=opt_end, key="v414_oos_start")
            st.info("For meaningful validation, first download at least 6–12 months of NIFTY Spot and matching option history. Use the same historical expiry lot size; a single lot-size input cannot automatically adjust across historical expiry changes.")
            if st.button("Run V4.14 extended validation", type="primary", key="v414_run"):
                try:
                    with st.spinner("Running unchanged V4.13 signals across the selected historical period..."):
                        spot14 = add_price_action_signals(opt_hist)
                        spot14 = add_breakout_retest_signals(spot14, body_min=0.60, close_edge=0.70, max_wait_bars=12)
                        spot14 = spot14.loc[(spot14.index.date >= opt_start) & (spot14.index.date <= opt_end)].copy()
                        spot14["buy_setup"] = spot14["v413_buy_retest"].fillna(False)
                        spot14["sell_setup"] = spot14["v413_sell_retest"].fillna(False)
                        spot14["signal_reason"] = ""
                        spot14.loc[spot14["buy_setup"], "signal_reason"] = "v413_bullish_breakout_retest_60"
                        spot14.loc[spot14["sell_setup"], "signal_reason"] = "v413_bearish_breakout_retest_60"
                        c14 = pd.read_csv(call_file, parse_dates=["timestamp"], index_col="timestamp").sort_index()
                        p14 = pd.read_csv(put_file, parse_dates=["timestamp"], index_col="timestamp").sort_index()
                        for frame in (c14,p14):
                            if getattr(frame.index,"tz",None) is not None:
                                frame.index=frame.index.tz_convert("Asia/Kolkata").tz_localize(None)
                            frame.columns=[str(c).lower() for c in frame.columns]
                        c14=c14.loc[(c14.index.date>=opt_start)&(c14.index.date<=opt_end)]
                        p14=p14.loc[(p14.index.date>=opt_start)&(p14.index.date<=opt_end)]
                        tc14,mc14=run_option_buying_backtest(spot14,c14,"CALL",premium_stop,premium_target,quantity=int(option_qty),slippage_per_side=option_slippage,brokerage_round_trip=option_brokerage,max_trades_per_day=1)
                        tp14,mp14=run_option_buying_backtest(spot14,p14,"PUT",premium_stop,premium_target,quantity=int(option_qty),slippage_per_side=option_slippage,brokerage_round_trip=option_brokerage,max_trades_per_day=1)
                        trades14=pd.concat([t.assign(option_side=side) for t,side in [(tc14,"CALL"),(tp14,"PUT")] if not t.empty],ignore_index=True) if (not tc14.empty or not tp14.empty) else pd.DataFrame()
                        if not trades14.empty:
                            trades14["entry_time"]=pd.to_datetime(trades14["entry_time"])
                            trades14["exit_time"]=pd.to_datetime(trades14["exit_time"])
                            trades14["period"]=trades14["entry_time"].dt.date.map(lambda d:"Out-of-sample" if d>=v414_oos_start else "In-sample")
                        st.session_state["v414_results"]={"trades":trades14,"start":opt_start,"end":opt_end,"oos_start":v414_oos_start,"expiry":f"{opt_expiry_flag}/{opt_expiry_code}","timeframe":opt_interval,"lots":int(option_lots),"lot_size":int(option_lot_size),"quantity":int(option_qty),"stop":float(premium_stop),"target":float(premium_target),"slippage":float(option_slippage),"brokerage":float(option_brokerage)}
                except Exception as exc:
                    st.error(f"V4.14 validation failed: {exc}")
            if "v414_results" in st.session_state:
                r14=st.session_state["v414_results"]; t14=r14["trades"]
                st.markdown("### V4.14 chronological validation results")
                st.caption(f"{r14['start']} to {r14['end']} • OOS from {r14['oos_start']} • {r14['expiry']} • {r14['timeframe']}m • {r14['lots']} lot(s) × {r14['lot_size']} = {r14['quantity']} units")
                def _v414_metrics(frame):
                    if frame.empty: return {"Trades":0,"Win rate":"N/A","Gross P&L ₹":0.0,"Slippage ₹":0.0,"Brokerage ₹":0.0,"Net P&L ₹":0.0,"Max drawdown ₹":0.0,"Max losing streak":0}
                    ordered=frame.sort_values("exit_time").copy()
                    net= pd.to_numeric(ordered["net_pnl_inr"],errors="coerce").fillna(0)
                    equity=net.cumsum(); dd=equity-equity.cummax()
                    streak=cur=0
                    for val in net:
                        cur=cur+1 if val<0 else 0; streak=max(streak,cur)
                    return {"Trades":len(ordered),"Win rate":f"{(net>0).mean()*100:.2f}%","Gross P&L ₹":ordered["gross_pnl_inr"].sum(),"Slippage ₹":ordered["slippage_inr"].sum(),"Brokerage ₹":ordered["brokerage_inr"].sum(),"Net P&L ₹":net.sum(),"Max drawdown ₹":dd.min(),"Max losing streak":streak}
                for period14 in ("In-sample","Out-of-sample"):
                    st.markdown(f"#### {period14}")
                    part14=t14[t14["period"]==period14] if not t14.empty else pd.DataFrame()
                    st.dataframe(pd.DataFrame([_v414_metrics(part14)]),hide_index=True,use_container_width=True)
                    if not part14.empty:
                        for side14 in ("CALL","PUT"):
                            sideframe=part14[part14["option_side"]==side14]
                            st.caption(f"{side14}: {len(sideframe)} trades • Net ₹{sideframe['net_pnl_inr'].sum():,.2f}")
                if not t14.empty:
                    t14["month"]=t14["entry_time"].dt.to_period("M").astype(str)
                    monthly14=t14.groupby(["period","month"],as_index=False).agg(trades=("net_pnl_inr","size"),net_pnl_inr=("net_pnl_inr","sum"),gross_pnl_inr=("gross_pnl_inr","sum"),slippage_inr=("slippage_inr","sum"),brokerage_inr=("brokerage_inr","sum"))
                    st.markdown("#### Monthly performance")
                    st.dataframe(monthly14,use_container_width=True)
                    st.download_button("Download V4.14 monthly summary CSV",monthly14.to_csv(index=False).encode("utf-8"),"v414_monthly_summary.csv","text/csv",key="v414_monthly_dl")
                    st.download_button("Download V4.14 all trades CSV",t14.to_csv(index=False).encode("utf-8"),"v414_all_trades.csv","text/csv",key="v414_trades_dl")
                else:
                    st.warning("No completed V4.14 trades in the selected period. This is not evidence of profitability.")
                st.caption("The out-of-sample period is the decisive validation. Do not tune settings using OOS results and then report the same period as an independent test.")
            st.markdown("#### V4.18 — Historical lot-size-aware validation")
            st.caption("Runs unchanged V4.13 breakout-retest signals with a manually verified effective-date lot-size schedule. CALL and PUT are reported separately. The app does not guess historical lot sizes or infer them from option candles.")
            st.warning("Before running, verify each lot-size effective date and quantity from official NSE contract specifications / Dhan contract files. The schedule must cover the entire selected test period without gaps or overlaps. Incorrect schedule values produce incorrect rupee P&L.")
            v418_default_schedule = pd.DataFrame([
                {"start_date": opt_start, "end_date": opt_end, "lot_size": None}
            ])
            v418_schedule = st.data_editor(v418_default_schedule, num_rows="dynamic", use_container_width=True, key="v418_lot_schedule_editor",
                column_config={
                    "start_date": st.column_config.DateColumn("Effective from", format="DD/MM/YYYY"),
                    "end_date": st.column_config.DateColumn("Effective through", format="DD/MM/YYYY"),
                    "lot_size": st.column_config.NumberColumn("Verified units per lot", min_value=1, step=1, help="Enter the official lot size effective during this date range.")
                })
            if st.button("Run V4.18 lot-size-aware validation", type="primary", key="v418_run"):
                try:
                    from lot_schedule import validate_lot_schedule, run_scheduled_backtest
                    schedule18 = validate_lot_schedule(v418_schedule, opt_start, opt_end)
                    with st.spinner("Running daily backtests using the verified lot-size schedule..."):
                        spot18 = add_price_action_signals(opt_hist)
                        spot18 = add_breakout_retest_signals(spot18, body_min=0.60, close_edge=0.70, max_wait_bars=12)
                        spot18 = spot18.loc[(spot18.index.date >= opt_start) & (spot18.index.date <= opt_end)].copy()
                        spot18["buy_setup"] = spot18["v413_buy_retest"].fillna(False)
                        spot18["sell_setup"] = spot18["v413_sell_retest"].fillna(False)
                        spot18["signal_reason"] = ""
                        spot18.loc[spot18["buy_setup"], "signal_reason"] = "v413_bullish_breakout_retest_60"
                        spot18.loc[spot18["sell_setup"], "signal_reason"] = "v413_bearish_breakout_retest_60"
                        c18 = pd.read_csv(call_file, parse_dates=["timestamp"], index_col="timestamp").sort_index()
                        p18 = pd.read_csv(put_file, parse_dates=["timestamp"], index_col="timestamp").sort_index()
                        for frame18 in (c18, p18):
                            if getattr(frame18.index, "tz", None) is not None:
                                frame18.index = frame18.index.tz_convert("Asia/Kolkata").tz_localize(None)
                            frame18.columns = [str(col).lower() for col in frame18.columns]
                        c18 = c18.loc[(c18.index.date >= opt_start) & (c18.index.date <= opt_end)]
                        p18 = p18.loc[(p18.index.date >= opt_start) & (p18.index.date <= opt_end)]
                        tc18 = run_scheduled_backtest(spot18, c18, "CALL", premium_stop, premium_target, schedule18, opt_start, opt_end, int(option_lots), option_slippage, option_brokerage, 1)
                        tp18 = run_scheduled_backtest(spot18, p18, "PUT", premium_stop, premium_target, schedule18, opt_start, opt_end, int(option_lots), option_slippage, option_brokerage, 1)
                        trades18 = pd.concat([x for x in (tc18, tp18) if not x.empty], ignore_index=True) if (not tc18.empty or not tp18.empty) else pd.DataFrame()
                        if not trades18.empty:
                            trades18["entry_time"] = pd.to_datetime(trades18["entry_time"])
                            trades18["exit_time"] = pd.to_datetime(trades18["exit_time"])
                            trades18["period"] = trades18["entry_time"].dt.date.map(lambda d: "Out-of-sample" if d >= v414_oos_start else "In-sample")
                        st.session_state["v418_results"] = {"trades": trades18, "schedule": schedule18, "start": opt_start, "end": opt_end, "oos_start": v414_oos_start, "lots": int(option_lots), "stop": float(premium_stop), "target": float(premium_target), "slippage": float(option_slippage), "brokerage": float(option_brokerage)}
                except Exception as exc:
                    st.error(f"V4.18 validation failed: {exc}")
            if "v418_results" in st.session_state:
                r18 = st.session_state["v418_results"]
                t18 = r18["trades"]
                st.markdown("### V4.18 results — historical lot sizes applied")
                st.dataframe(r18["schedule"], hide_index=True, use_container_width=True)
                st.caption(f"{r18['start']} to {r18['end']} • OOS from {r18['oos_start']} • {r18['lots']} lot(s) • V4.13 signal rules unchanged")
                def _v418_stats(frame18):
                    if frame18.empty:
                        return {"Trades": 0, "Win rate": "N/A", "Gross P&L ₹": 0.0, "Slippage ₹": 0.0, "Brokerage ₹": 0.0, "Net P&L ₹": 0.0, "Max drawdown ₹": 0.0}
                    z = frame18.sort_values("exit_time").copy()
                    net = pd.to_numeric(z["net_pnl_inr"], errors="coerce").fillna(0)
                    eq = net.cumsum()
                    return {"Trades": len(z), "Win rate": f"{(net > 0).mean()*100:.2f}%", "Gross P&L ₹": z["gross_pnl_inr"].sum(), "Slippage ₹": z["slippage_inr"].sum(), "Brokerage ₹": z["brokerage_inr"].sum(), "Net P&L ₹": net.sum(), "Max drawdown ₹": (eq-eq.cummax()).min()}
                for period18 in ("In-sample", "Out-of-sample"):
                    st.markdown(f"#### {period18}")
                    subset18 = t18[t18["period"] == period18] if not t18.empty else pd.DataFrame()
                    st.dataframe(pd.DataFrame([_v418_stats(subset18)]), hide_index=True, use_container_width=True)
                    for side18 in ("CALL", "PUT"):
                        side_subset18 = subset18[subset18["option_side"] == side18] if not subset18.empty else pd.DataFrame()
                        st.caption(f"{side18}: {len(side_subset18)} trades • Net P&L ₹{side_subset18['net_pnl_inr'].sum():,.2f}" if not side_subset18.empty else f"{side18}: 0 trades")
                if not t18.empty:
                    st.markdown("#### CALL vs PUT performance")
                    side_summary18 = t18.groupby(["period", "option_side"], as_index=False).agg(trades=("net_pnl_inr", "size"), win_rate_pct=("net_pnl_inr", lambda x: (x > 0).mean()*100), gross_pnl_inr=("gross_pnl_inr", "sum"), slippage_inr=("slippage_inr", "sum"), brokerage_inr=("brokerage_inr", "sum"), net_pnl_inr=("net_pnl_inr", "sum"))
                    st.dataframe(side_summary18, hide_index=True, use_container_width=True)
                    st.download_button("Download V4.18 all trades CSV", t18.to_csv(index=False).encode("utf-8"), "v418_all_trades.csv", "text/csv", key="v418_trades_dl")
                    st.download_button("Download V4.18 CALL/PUT summary CSV", side_summary18.to_csv(index=False).encode("utf-8"), "v418_call_put_summary.csv", "text/csv", key="v418_summary_dl")
                else:
                    st.warning("No completed V4.18 trades in the selected period.")
                st.caption("V4.18 applies the user-entered lot size by effective date. It does not automatically verify the schedule against NSE records. Verify official contract specifications before relying on INR results. Research only; no orders are placed.")

            st.markdown("#### V4.20 — Full-history signal and option-entry diagnostics")
            st.caption("The diagnostic date range is independent of the monthly downloader batch dates. V4.20 reads the complete saved Spot, CALL and PUT files, displays their actual row counts/date ranges, and lets you select an independent overlapping period. Read-only: no Dhan requests, no saved-file changes, and no strategy changes.")
            st.info("V4.19 reused the downloader's current date inputs, which could restrict diagnostics to the latest month. V4.20 separates diagnostic dates from download batch dates.")
            try:
                call20 = pd.read_csv(call_file, parse_dates=["timestamp"], index_col="timestamp").sort_index()
                put20 = pd.read_csv(put_file, parse_dates=["timestamp"], index_col="timestamp").sort_index()
                spot20_full = opt_hist.copy()
                starts20 = [pd.Timestamp(spot20_full.index.min()).date(), pd.Timestamp(call20.index.min()).date(), pd.Timestamp(put20.index.min()).date()]
                ends20 = [pd.Timestamp(spot20_full.index.max()).date(), pd.Timestamp(call20.index.max()).date(), pd.Timestamp(put20.index.max()).date()]
                common_start20, common_end20 = max(starts20), min(ends20)
                st.write(f"**Saved Spot:** {len(spot20_full):,} rows • {spot20_full.index.min()} to {spot20_full.index.max()}")
                st.write(f"**Saved CALL:** {len(call20):,} rows • {call20.index.min()} to {call20.index.max()}")
                st.write(f"**Saved PUT:** {len(put20):,} rows • {put20.index.min()} to {put20.index.max()}")
                if common_start20 > common_end20:
                    st.error("The saved Spot, CALL and PUT files have no overlapping date range. Check expiry/timeframe selection.")
                else:
                    d20c1, d20c2 = st.columns(2)
                    with d20c1:
                        diag_start20 = st.date_input("V4.20 diagnostic start (independent of download batch)", value=common_start20, min_value=common_start20, max_value=common_end20, key="v420_diag_start")
                    with d20c2:
                        diag_end20 = st.date_input("V4.20 diagnostic end", value=common_end20, min_value=common_start20, max_value=common_end20, key="v420_diag_end")
                    if st.button("Run V4.20 full-history monthly diagnostics", type="primary", key="v420_run_diagnostics"):
                        if diag_start20 > diag_end20:
                            st.error("Diagnostic start must be on or before diagnostic end.")
                        else:
                            from historical_diagnostics import build_monthly_historical_diagnostic
                            with st.spinner("Counting full-history monthly Spot signals and checking option matches..."):
                                spot20 = add_price_action_signals(spot20_full)
                                spot20 = add_breakout_retest_signals(spot20, body_min=0.60, close_edge=0.70, max_wait_bars=12)
                                diag20 = build_monthly_historical_diagnostic(spot20, call20, put20, diag_start20, diag_end20)
                                st.session_state["v420_diagnostic"] = {"table": diag20, "start": diag_start20, "end": diag_end20}
                            st.success("V4.20 full-history diagnostics completed.")
                if "v420_diagnostic" in st.session_state:
                    d20 = st.session_state["v420_diagnostic"]
                    monthly20 = d20["table"]
                    st.markdown("### V4.20 monthly diagnostic results")
                    st.caption(f"{d20['start']} to {d20['end']} • complete saved files • unchanged V4.13 rules • no trades executed")
                    st.dataframe(monthly20, hide_index=True, use_container_width=True)
                    if not monthly20.empty:
                        totals20 = {"Months checked": len(monthly20), "Spot candles": int(monthly20["spot_rows_5m"].sum()),
                            "Spot trading dates": int(monthly20["spot_trading_dates"].sum()),
                            "CALL signals": int(monthly20["CALL_spot_signals"].sum()),
                            "CALL option rows": int(monthly20["CALL_option_rows"].sum()),
                            "CALL next-candle same-strike matches": int(monthly20["CALL_signals_with_next_candle_same_strike"].sum()),
                            "PUT signals": int(monthly20["PUT_spot_signals"].sum()),
                            "PUT option rows": int(monthly20["PUT_option_rows"].sum()),
                            "PUT next-candle same-strike matches": int(monthly20["PUT_signals_with_next_candle_same_strike"].sum())}
                        st.markdown("#### Diagnostic totals")
                        st.dataframe(pd.DataFrame([totals20]), hide_index=True, use_container_width=True)
                        st.download_button("Download V4.20 full-history diagnostic CSV", monthly20.to_csv(index=False).encode("utf-8"), "v420_full_history_diagnostics.csv", "text/csv", key="v420_monthly_csv")
                    st.caption("Zero Spot signals means unchanged V4.13 rules did not trigger that month. Signals without an ATM offset=0 match indicate missing ATM data. ATM matches without a next-candle same-strike match indicate entry coverage issues. Monthly option-row counts confirm whether the full saved option CSV is being read.")
            except Exception as exc:
                st.error(f"V4.20 diagnostics failed while loading saved files: {exc}")

            st.markdown("#### V4.21 — Monthly signal-to-trade reconciliation")
            st.caption("Compares V4.13 signals, ATM/next-bar strike availability, and actual entries from the same production option backtester with the V4.18 one-entry-per-direction-per-day rule. Read-only: no Dhan requests and no saved data changes.")
            try:
                if "v420_diagnostic" in st.session_state:
                    v421_default_start = st.session_state["v420_diagnostic"]["start"]
                    v421_default_end = st.session_state["v420_diagnostic"]["end"]
                else:
                    v421_default_start = opt_start
                    v421_default_end = opt_end
                v421c1, v421c2 = st.columns(2)
                with v421c1:
                    v421_start = st.date_input("V4.21 reconciliation start", value=v421_default_start, key="v421_start")
                with v421c2:
                    v421_end = st.date_input("V4.21 reconciliation end", value=v421_default_end, key="v421_end")
                if st.button("Run V4.21 monthly trade reconciliation", type="primary", key="v421_run"):
                    if v421_start > v421_end:
                        st.error("V4.21 start date must be on or before end date.")
                    else:
                        from trade_reconciliation import build_v421_reconciliation
                        with st.spinner("Re-running the unchanged production entry engine and reconciling monthly entries..."):
                            spot21 = add_price_action_signals(opt_hist)
                            spot21 = add_breakout_retest_signals(spot21, body_min=0.60, close_edge=0.70, max_wait_bars=12)
                            spot21["v413_buy_retest"] = spot21["v413_buy_retest"].fillna(False)
                            spot21["v413_sell_retest"] = spot21["v413_sell_retest"].fillna(False)
                            call21 = pd.read_csv(call_file, parse_dates=["timestamp"], index_col="timestamp").sort_index()
                            put21 = pd.read_csv(put_file, parse_dates=["timestamp"], index_col="timestamp").sort_index()
                            result21 = build_v421_reconciliation(spot21, call21, put21, v421_start, v421_end, premium_stop, premium_target)
                            st.session_state["v421_reconciliation"] = {"table": result21, "start": v421_start, "end": v421_end}
                        st.success("V4.21 reconciliation completed.")
                if "v421_reconciliation" in st.session_state:
                    r21 = st.session_state["v421_reconciliation"]
                    t21 = r21["table"]
                    st.markdown("### V4.21 monthly reconciliation results")
                    st.caption(f"{r21['start']} to {r21['end']} • unchanged V4.13 signals • production backtester • one entry per direction/day")
                    st.dataframe(t21, hide_index=True, use_container_width=True)
                    if not t21.empty:
                        totals21 = t21.groupby("side", as_index=False).agg(
                            signals=("spot_signals", "sum"), atm_missing=("atm_missing", "sum"),
                            next_timestamp_missing=("next_timestamp_missing", "sum"),
                            same_strike_next_bar_missing=("same_strike_next_bar_missing", "sum"),
                            next_bar_candidates=("next_bar_candidates", "sum"),
                            actual_backtest_entries=("actual_backtest_entries", "sum"),
                            signals_not_entered_by_full_backtest=("signals_not_entered_by_full_backtest", "sum"))
                        st.markdown("#### CALL / PUT reconciliation totals")
                        st.dataframe(totals21, hide_index=True, use_container_width=True)
                        st.download_button("Download V4.21 reconciliation CSV", t21.to_csv(index=False).encode("utf-8"), "v421_trade_reconciliation.csv", "text/csv", key="v421_csv")
                    st.warning("Signals not entered by the full backtest are a residual category: it can include the one-entry-per-day limit, an already-open position, time cutoffs, and other production-engine sequencing rules. Do not interpret this residual as a single rejection cause.")
            except Exception as exc:
                st.error(f"V4.21 reconciliation failed: {exc}")

            st.markdown("#### V4.22 — Resumable one-month reconciliation")
            st.caption("Processes only one month per click, saves each completed month to /var/data, and resumes after a disconnect. No Dhan requests. Monthly runs are diagnostic approximations: position state does not carry between months.")
            try:
                from monthly_reconciliation import run_one_month, load_progress, reset_progress, progress_key
                v422a, v422b = st.columns(2)
                with v422a:
                    v422_start = st.date_input("V4.22 start", value=date(2025,12,1), key="v422_start")
                with v422b:
                    v422_end = st.date_input("V4.22 end", value=date(2026,10,31), key="v422_end")
                if v422_start <= v422_end:
                    key422 = progress_key(v422_start, v422_end, premium_stop, premium_target, call_file, put_file, DATA_FILE)
                    progress422 = load_progress(DATA_DIR, key422)
                    months422 = pd.period_range(pd.Timestamp(v422_start).to_period('M'), pd.Timestamp(v422_end).to_period('M'), freq='M')
                    done422 = set(progress422.keys())
                    remaining422 = [m for m in months422 if str(m) not in done422]
                    st.write(f"Completed months: {len(months422)-len(remaining422)} / {len(months422)}")
                    if remaining422:
                        st.info(f"Next month: {remaining422[0]}. Press once and wait for completion before continuing.")
                    if st.button("Process next V4.22 month", disabled=not remaining422, key="v422_next"):
                        with st.spinner(f"Processing {remaining422[0]} only..."):
                            run_one_month(DATA_DIR, key422, remaining422[0], DATA_FILE, call_file, put_file,
                                          v422_start, v422_end, premium_stop, premium_target)
                        st.success(f"Saved {remaining422[0]} to persistent disk. Press again for the next month.")
                        st.rerun()
                    if progress422:
                        frames422 = [pd.DataFrame(rows) for rows in progress422.values() if rows]
                        if frames422:
                            table422 = pd.concat(frames422, ignore_index=True).sort_values(['month','side'])
                            st.dataframe(table422, hide_index=True, use_container_width=True)
                            st.download_button("Download V4.22 monthly reconciliation CSV", table422.to_csv(index=False).encode(),
                                               "v422_monthly_reconciliation.csv", "text/csv", key="v422_export")
                    st.caption("Completed results survive a web connection failure. To change dates or premium stop/target, a separate progress set is created automatically.")
                else:
                    st.error("V4.22 start must be on or before end.")
            except Exception as exc:
                st.error(f"V4.22 month processing failed: {exc}")

            st.markdown("#### V4.23 — Per-signal rejection-reason diagnostics")
            st.caption("Runs the unchanged V4.13 breakout/retest signals and the production V4.18 one-entry-per-direction/day option engine one month at a time. Produces one row per CALL/PUT signal with the selected strike, expected next option candle, actual entry/exit, and a rejection label. No Dhan requests and no changes to saved market data.")
            st.warning("V4.23 is a monthly diagnostic approximation: open positions do not carry across month boundaries. Rejection labels distinguish directly verifiable data/cutoff/daily-limit cases; any unexplained residual is explicitly labelled ENGINE_SEQUENCE_OR_UNEXPORTED_STATE rather than guessed.")
            try:
                from monthly_rejection import v423_key, run_v423_month, load_v423_progress
                v423a, v423b = st.columns(2)
                with v423a:
                    v423_start = st.date_input("V4.23 start", value=date(2025,12,1), key="v423_start")
                with v423b:
                    v423_end = st.date_input("V4.23 end", value=date(2026,10,31), key="v423_end")
                if v423_start <= v423_end:
                    key423 = v423_key(v423_start, v423_end, premium_stop, premium_target, call_file, put_file, DATA_FILE)
                    progress423 = load_v423_progress(DATA_DIR, key423)
                    months423 = pd.period_range(pd.Timestamp(v423_start).to_period("M"), pd.Timestamp(v423_end).to_period("M"), freq="M")
                    done423 = set(progress423.keys())
                    remaining423 = [m for m in months423 if str(m) not in done423]
                    st.write(f"V4.23 completed months: {len(months423)-len(remaining423)} / {len(months423)}")
                    if remaining423:
                        st.info(f"Next month: {remaining423[0]}. Press once and wait for completion.")
                    if st.button("Process next V4.23 diagnostic month", disabled=not remaining423, type="primary", key="v423_next"):
                        with st.spinner(f"Reconciling individual signals for {remaining423[0]}..."):
                            run_v423_month(DATA_DIR, key423, remaining423[0], DATA_FILE, call_file, put_file,
                                           v423_start, v423_end, premium_stop, premium_target)
                        st.success(f"Saved V4.23 signal ledger for {remaining423[0]}.")
                        st.rerun()
                    if progress423:
                        ledgers423=[]; summaries423=[]
                        for month_data in progress423.values():
                            if month_data.get("ledger"): ledgers423.append(pd.DataFrame(month_data["ledger"]))
                            if month_data.get("summary"): summaries423.append(pd.DataFrame(month_data["summary"]))
                        if summaries423:
                            summary423=pd.concat(summaries423,ignore_index=True).sort_values(["month","side"])
                            st.markdown("##### Monthly rejection summary")
                            st.dataframe(summary423,hide_index=True,use_container_width=True)
                            st.download_button("Download V4.23 monthly rejection summary",summary423.to_csv(index=False).encode("utf-8"),"v423_monthly_rejection_summary.csv","text/csv",key="v423_summary_csv")
                        if ledgers423:
                            ledger423=pd.concat(ledgers423,ignore_index=True).sort_values(["signal_time","side"])
                            st.markdown("##### Individual signal ledger")
                            st.dataframe(ledger423,hide_index=True,use_container_width=True)
                            st.download_button("Download V4.23 individual signal ledger",ledger423.to_csv(index=False).encode("utf-8"),"v423_individual_signal_ledger.csv","text/csv",key="v423_ledger_csv")
                            reasons423=ledger423.loc[ledger423.status.eq("NOT_ENTERED")].groupby(["side","rejection_reason"],as_index=False).size()
                            st.markdown("##### Not-entered signals by recorded reason")
                            st.dataframe(reasons423,hide_index=True,use_container_width=True)
                    st.caption("Progress is saved atomically under /var/data and resumes after a disconnect. Changing dates, premium stop/target, or source files creates a separate progress set.")
                else:
                    st.error("V4.23 start must be on or before end.")
            except Exception as exc:
                st.error(f"V4.23 diagnostics failed: {exc}")

            st.markdown("#### V4.24 — Full historical profitability (CALL + PUT)")
            st.caption("V4.24.3 low-memory chronological backtest. CALL is streamed and checkpointed first, then PUT; only one side and small CSV chunks are in memory at a time. Separate trade/monthly CSV reports and a combined comparison. Uses saved weekly option candles only; no Dhan API calls.")
            st.warning("Lot-size schedule below is prefilled from the values previously entered: 75 units through 23-Dec-2025 and 65 units from 24-Dec-2025. Verify these against the official historical contract specifications before relying on rupee P&L. Brokerage is an estimate; statutory charges, taxes and exchange levies may differ.")
            try:
                from io import StringIO
                from price_action import add_price_action_signals, add_breakout_retest_signals
                from v424_backtest import run_v424_csv_resumable
                v424c1, v424c2 = st.columns(2)
                with v424c1:
                    v424_start = st.date_input("V4.24 backtest start", value=date(2025, 12, 1), key="v424_start")
                with v424c2:
                    v424_end = st.date_input("V4.24 backtest end", value=date(2026, 10, 31), key="v424_end")
                v424_stop, v424_target, v424_lots = st.columns(3)
                with v424_stop:
                    v424_pstop = st.number_input("V4.24 premium stop (₹ points)", min_value=0.05, value=20.0, step=1.0, key="v424_stop")
                with v424_target:
                    v424_ptarget = st.number_input("V4.24 premium target (₹ points)", min_value=0.05, value=40.0, step=1.0, key="v424_target")
                with v424_lots:
                    v424_lots_n = st.number_input("V4.24 lots per trade", min_value=1, value=1, step=1, key="v424_lots")
                v424_cost1, v424_cost2 = st.columns(2)
                with v424_cost1:
                    v424_slip = st.number_input("V4.24 slippage per unit per side (₹)", min_value=0.0, value=0.50, step=0.05, key="v424_slippage")
                with v424_cost2:
                    v424_broker = st.number_input("V4.24 brokerage estimate per round trip (₹)", min_value=0.0, value=40.0, step=5.0, key="v424_brokerage")
                schedule_default = "start_date,end_date,lot_size\n2025-12-01,2025-12-23,75\n2025-12-24,2026-10-31,65"
                schedule_text = st.text_area("Historical lot-size schedule (edit only after checking official contract data)", value=schedule_default, height=100, key="v424_lot_schedule")
                if opt_expiry_flag != "WEEK" or opt_interval != 5:
                    st.error("V4.24 is restricted to WEEK expiry and 5-minute option candles. Select WEEK and 5 minutes above.")
                elif v424_start > v424_end:
                    st.error("V4.24 start date must be on or before end date.")
                elif st.button("Run V4.24 full CALL + PUT profitability backtest", type="primary", key="v424_run"):
                    try:
                        schedule424 = pd.read_csv(StringIO(schedule_text))
                        spot424 = add_price_action_signals(opt_hist.copy())
                        spot424 = add_breakout_retest_signals(spot424, body_min=0.60, close_edge=0.70, max_wait_bars=12)
                        import gc
                        v424_progress = st.progress(0, text="Preparing low-memory backtest...")
                        v424_status = st.empty()
                        def v424_update(done, total, side, day):
                            # Each side has its own day counter; keep UI updates light.
                            v424_progress.progress(min(100, int(done / max(total, 1) * 100)),
                                text=f"{side}: completed {done}/{total} sessions; latest {day}")
                            v424_status.write(f"Saved {side} session {done} of {total} to /var/data. If interrupted, press Run again to resume.")
                        with st.spinner("Processing CALL first, then PUT. Option CSVs are streamed in small chunks; do not close this page..."):
                            call_trades24, call_metrics24, call_monthly24, call_done24, call_total24, call_missing24 = run_v424_csv_resumable(
                                spot424, call_file, "CALL", schedule424, v424_start, v424_end, DATA_DIR,
                                int(v424_lots_n), v424_pstop, v424_ptarget, v424_slip, v424_broker,
                                chunksize=6000, progress_callback=v424_update)
                            gc.collect()
                            v424_progress.progress(0, text="CALL complete. Starting PUT...")
                            put_trades24, put_metrics24, put_monthly24, put_done24, put_total24, put_missing24 = run_v424_csv_resumable(
                                spot424, put_file, "PUT", schedule424, v424_start, v424_end, DATA_DIR,
                                int(v424_lots_n), v424_pstop, v424_ptarget, v424_slip, v424_broker,
                                chunksize=6000, progress_callback=v424_update)
                            gc.collect()
                        st.session_state["v424_results"] = {
                            "start": str(v424_start), "end": str(v424_end),
                            "call_trades": call_trades24, "put_trades": put_trades24,
                            "call_metrics": call_metrics24, "put_metrics": put_metrics24,
                            "call_monthly": call_monthly24, "put_monthly": put_monthly24,
                            "call_missing": call_missing24, "put_missing": put_missing24,
                            "expiry": opt_expiry_flag, "expiry_code": int(opt_expiry_code), "interval": int(opt_interval),
                            "lots": int(v424_lots_n), "stop": float(v424_pstop), "target": float(v424_ptarget),
                            "slippage": float(v424_slip), "brokerage": float(v424_broker)}
                        st.success(f"V4.24 completed. CALL: {call_done24}/{call_total24} sessions; PUT: {put_done24}/{put_total24} sessions. Separate reports are ready below.")
                        missing_all24 = pd.concat([call_missing24, put_missing24], ignore_index=True)
                        if not missing_all24.empty:
                            st.warning(f"{len(missing_all24)} signal(s) had no matching option candles and were excluded from P&L. Review the missing-data report below.")
                            st.dataframe(missing_all24, hide_index=True, use_container_width=True)
                            st.download_button("Download V4.24 missing option data report", missing_all24.to_csv(index=False).encode("utf-8"), "v424_missing_option_data.csv", "text/csv", key="v424_missing_data_csv")
                        else:
                            st.success("No signal dates were missing option candles.")
                    except Exception as exc:
                        st.error(f"V4.24 backtest could not complete: {exc}")
                if "v424_results" in st.session_state:
                    r24 = st.session_state["v424_results"]
                    st.markdown(f"### V4.24 results — {r24['start']} to {r24['end']}")
                    st.caption(f"Weekly expiry sequence {r24['expiry_code']} • 5-minute options • {r24['lots']} lot(s) • premium SL ₹{r24['stop']:.2f} • target ₹{r24['target']:.2f} • slippage ₹{r24['slippage']:.2f}/unit/side • brokerage estimate ₹{r24['brokerage']:.2f}/trade")
                    summary24 = pd.DataFrame([{"Side": "CALL", **r24["call_metrics"]}, {"Side": "PUT", **r24["put_metrics"]}])
                    st.markdown("##### CALL vs PUT comparison")
                    st.dataframe(summary24, hide_index=True, use_container_width=True)
                    st.download_button("Download V4.24 CALL + PUT comparison", summary24.to_csv(index=False).encode("utf-8"), "v424_call_put_comparison.csv", "text/csv", key="v424_compare_csv")
                    for side24, key24 in (("CALL", "call"), ("PUT", "put")):
                        trades24 = r24[f"{key24}_trades"]
                        monthly24 = r24[f"{key24}_monthly"]
                        st.markdown(f"##### {side24} — trade report")
                        st.dataframe(trades24, hide_index=True, use_container_width=True)
                        st.download_button(f"Download V4.24 {side24} trades CSV", trades24.to_csv(index=False).encode("utf-8"), f"v424_{key24}_trades.csv", "text/csv", key=f"v424_{key24}_trades_csv")
                        st.markdown(f"##### {side24} — monthly report")
                        st.dataframe(monthly24, hide_index=True, use_container_width=True)
                        st.download_button(f"Download V4.24 {side24} monthly CSV", monthly24.to_csv(index=False).encode("utf-8"), f"v424_{key24}_monthly.csv", "text/csv", key=f"v424_{key24}_monthly_csv")
                    st.caption("V4.24.3 preserves the V4.13 signal rules. No-signal Spot dates do not require option candles; missing option candles on actual signal dates are listed separately and excluded from P&L. Option CSVs are streamed in 6,000-row chunks and each completed session is checkpointed under /var/data; rerunning resumes completed sessions. Each position is intraday and is squared off by the existing premium engine; daily position state is therefore carried through each full session. CALL and PUT are tested separately, each with at most one entry per direction per day. No counterfactual trades are added for signals rejected by the daily limit or cutoff.")
            except Exception as exc:
                st.error(f"V4.24 module could not be loaded: {exc}")

            if "v412_option_results" in st.session_state:
                vr12 = st.session_state["v412_option_results"]
                st.markdown("### V4.12 volume-confirmed results")
                st.caption(f"{vr12['start']} to {vr12['end']} • {vr12['expiry']} • option {vr12['timeframe']}m • {vr12['lots']} lot(s) × {vr12['lot_size']} = {vr12['quantity']} units • 60% body + direction + close-location confirmation • one trade per direction/day.")
                rows12 = []
                for threshold, sides in vr12["matrix"].items():
                    combined12 = [t for t,_ in sides.values() if not t.empty]
                    all12 = pd.concat(combined12, ignore_index=True) if combined12 else pd.DataFrame()
                    rows12.append({"RVOL threshold":threshold,"Trades":len(all12),
                        "Win rate":f"{(all12['net_pnl_inr']>0).mean()*100:.2f}%" if not all12.empty else "0.00%",
                        "Gross P&L ₹":float(all12["gross_pnl_inr"].sum()) if not all12.empty else 0.0,
                        "Slippage ₹":float(all12["slippage_inr"].sum()) if not all12.empty else 0.0,
                        "Brokerage ₹":float(all12["brokerage_inr"].sum()) if not all12.empty else 0.0,
                        "Net P&L ₹":float(all12["net_pnl_inr"].sum()) if not all12.empty else 0.0})
                    for side_name, side_key in (("CALL","call"),("PUT","put")):
                        trades12, metrics12 = sides[side_key]
                        st.markdown(f"#### RVOL {threshold} — {side_name}")
                        if not trades12.empty:
                            st.dataframe(trades12, use_container_width=True)
                            st.download_button(f"Download V4.12 {threshold} {side_name} CSV",
                                trades12.to_csv(index=False).encode("utf-8"),
                                f"v412_rvol_{threshold.replace('.','')}_{side_name.lower()}_trades.csv",
                                "text/csv", key=f"v412_dl_{threshold}_{side_key}")
                        else:
                            st.caption("No completed trades for this side at this threshold.")
                st.dataframe(pd.DataFrame(rows12), hide_index=True, use_container_width=True)
                st.caption("Use identical date range, expiry, option timeframe, lots, historical lot size, premium stop/target, slippage and brokerage when comparing V4.12 with V4.11. RVOL is calculated from previous 20 completed Spot candles; no future data is used.")
            if "v410_option_results" in st.session_state:
                vr10 = st.session_state["v410_option_results"]
                st.markdown("### V4.10 filtered results")
                st.caption(f"{vr10['start']} to {vr10['end']} • {vr10['expiry']} • {vr10['timeframe']}m • {vr10['lots']} lot(s) × {vr10['lot_size']} = {vr10['quantity']} units. Confirmation: {vr10['confirmation']}; one trade/day/direction: {vr10['one_per_day']}.")
                combined_trades = []
                for label, key in (("BUY CE", "call"), ("BUY PE", "put")):
                    trades10, metrics10 = vr10[key]
                    st.markdown(f"#### {label}")
                    mcols10 = st.columns(4)
                    for col, metric_name in zip(mcols10, list(metrics10.keys())[:4]):
                        col.metric(metric_name, metrics10[metric_name])
                    if not trades10.empty:
                        combined_trades.append(trades10)
                        st.dataframe(trades10, use_container_width=True)
                        st.download_button(f"Download V4.10 {key.upper()} CSV", trades10.to_csv(index=False).encode("utf-8"),
                            f"v410_nifty_{key}_option_trades.csv", "text/csv", key=f"v410_download_{key}")
                if combined_trades:
                    all10 = pd.concat(combined_trades, ignore_index=True).sort_values("exit_time")
                    net10 = float(all10["net_pnl_inr"].sum())
                    wins10 = int((all10["net_pnl_inr"] > 0).sum())
                    st.markdown("#### Comparison with your uploaded V4.9 baseline")
                    compare10 = pd.DataFrame([
                        {"Metric": "Trades", "Uploaded baseline": 94, "V4.10 filtered": len(all10)},
                        {"Metric": "Win rate", "Uploaded baseline": "35.11%", "V4.10 filtered": f"{(all10['net_pnl_inr'] > 0).mean()*100:.2f}%"},
                        {"Metric": "Gross P&L", "Uploaded baseline": "-₹5,018.00", "V4.10 filtered": f"₹{all10['gross_pnl_inr'].sum():,.2f}"},
                        {"Metric": "Slippage", "Uploaded baseline": "₹6,110.00", "V4.10 filtered": f"₹{all10['slippage_inr'].sum():,.2f}"},
                        {"Metric": "Brokerage", "Uploaded baseline": "₹3,760.00", "V4.10 filtered": f"₹{all10['brokerage_inr'].sum():,.2f}"},
                        {"Metric": "Net P&L", "Uploaded baseline": "-₹14,888.00", "V4.10 filtered": f"₹{net10:,.2f}"},
                    ])
                    st.dataframe(compare10, hide_index=True, use_container_width=True)
                    st.caption("Baseline figures are calculated from the two CSVs uploaded on 18 Nov 2026. Compare only when date coverage, expiry, timeframe, lot size and costs match. The baseline has an excluded 6 Nov session.")
                else:
                    st.info("V4.10 generated no completed trades with the selected filters. This is a valid result; do not relax filters solely to force trades.")
            if "v48_option_results" in st.session_state:
                vr = st.session_state["v48_option_results"]
                st.markdown(f"### Results: {vr['start']} to {vr['end']} • {vr['expiry']} • {vr['timeframe']}m")
                st.caption(f"Position size: {vr['lots']} lot(s) × {vr['lot_size']} units = {vr['quantity']} units per trade. P&L is in INR from actual option premium OHLC, less simulated slippage and entered brokerage/fees estimate.")
                for label, key in (("BUY CE — bullish NIFTY signals", "call"), ("BUY PE — bearish NIFTY signals", "put")):
                    trades, metrics = vr[key]
                    st.markdown(f"#### {label}")
                    mcols = st.columns(4)
                    for col, metric_name in zip(mcols, list(metrics.keys())[:4]):
                        col.metric(metric_name, metrics[metric_name])
                    mcols2 = st.columns(3)
                    for col, metric_name in zip(mcols2, list(metrics.keys())[4:]):
                        col.metric(metric_name, metrics[metric_name])
                    st.write(f"Completed trades: {len(trades):,}")
                    if not trades.empty:
                        st.dataframe(trades, use_container_width=True)
                    st.download_button(f"Download {key.upper()} option trades CSV", trades.to_csv(index=False).encode("utf-8"),
                        f"v48_nifty_{key.lower()}_option_trades.csv", "text/csv", key=f"v48_download_{key}")
                st.info("V4.9 execution model: NIFTY signal confirmed at candle close; the ATM absolute strike at that signal is selected and bought at the next option candle open. Subsequent bars are matched by the same absolute strike across ATM-relative offset series. A change in ATM does not close the trade. CE uses the bullish 50% Strong Breakout signal. PE uses bearish BOS/CHoCH, ORB, previous-day-break and liquidity-sweep events. Premium stop/target use option OHLC; if both touch in one candle, stop is assumed first. Intraday only; square-off at 15:15. No orders are placed.")

