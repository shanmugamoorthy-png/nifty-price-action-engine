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
st.set_page_config(page_title="NIFTY Pure Price Action V4.17", page_icon="📈", layout="wide")
st.title("NIFTY Pure Price Action Engine — V4.17 local data diagnostics")
st.caption("Advanced market structure • BOS / CHoCH / liquidity sweeps • Dhan API • Telegram • V4.12 Spot-volume research • V4.16 append-safe monthly CE/PE history • V4.17 read-only data diagnostics")

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

