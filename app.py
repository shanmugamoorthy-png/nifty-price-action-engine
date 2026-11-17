import os
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
st.set_page_config(page_title="NIFTY Pure Price Action V4.7.3", page_icon="📈", layout="wide")
st.title("NIFTY Pure Price Action Engine — V4.7.3")
st.caption("Advanced market structure • BOS / CHoCH / liquidity sweeps • Dhan API • Telegram")

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
        from price_action import add_price_action_signals
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
    st.subheader("V4.7.3 — NIFTY Options Buying Backtest")
    st.write("NIFTY Spot generates the signal; the corresponding rolling NIFTY option premium is used for entry, stop, target and P&L. Bullish setup buys CE; bearish setup buys PE. This module is a research backtester only and never places orders.")
    st.warning("Initial V4.7 supports intraday option buying only. Each trade is pinned to the actual strike returned at entry and closed before an overnight/rolling-contract change. Positional option holding is intentionally excluded until exact expiry/contract identity can be locked for every historical bar.")
    opt_hist = load_saved()
    if opt_hist is None or opt_hist.empty:
        st.error("Download the NIFTY Spot 5-minute history first. V4.7 uses it to generate bullish and bearish signals.")
    else:
        from options_data import download_rolling_option
        from options_backtest import run_option_buying_backtest
        oc1, oc2 = st.columns(2)
        with oc1:
            opt_start = st.date_input("Options data start", value=max(opt_hist.index.min().date(), opt_hist.index.max().date()-timedelta(days=30)), key="v47_opt_start")
        with oc2:
            opt_end = st.date_input("Options data end", value=opt_hist.index.max().date(), key="v47_opt_end")
        oc3, oc4, oc5 = st.columns(3)
        with oc3:
            opt_expiry_flag = st.selectbox("Expiry type", ["WEEK", "MONTH"], index=0, key="v47_expiry_flag")
        with oc4:
            opt_expiry_code = st.selectbox("Expiry sequence", [1, 2, 3], index=0, format_func=lambda v: {1:"Near expiry",2:"Next expiry",3:"Far expiry"}[v], key="v47_expiry_code")
        with oc5:
            opt_interval = st.selectbox("Option candle timeframe", [5, 15], index=0, key="v47_interval")
        st.caption("Dhan Support confirmed expired-options history permits only ONE calendar day per API request. V4.7.3 downloads dates sequentially, waits between requests, and caches each completed day so an interrupted download can resume. Near expiry = expiry code 1; ATM is used in this release.")
        opt_strike = st.selectbox("Strike selection", ["ATM"], index=0, key="v47_strike")
        pc1, pc2, pc3, pc4 = st.columns(4)
        with pc1:
            premium_stop = st.number_input("Option premium stop (₹ points)", min_value=0.05, value=20.0, step=1.0, key="v47_premium_stop")
        with pc2:
            premium_target = st.number_input("Option premium target (₹ points)", min_value=0.05, value=40.0, step=1.0, key="v47_premium_target")
        with pc3:
            option_qty = st.number_input("Quantity (units, not lots)", min_value=1, value=1, step=1, key="v47_qty")
        with pc4:
            option_slippage = st.number_input("Premium slippage per side (₹)", min_value=0.0, value=0.50, step=0.05, key="v47_slippage")
        option_brokerage = st.number_input("Brokerage + fees estimate per round trip (₹)", min_value=0.0, value=40.0, step=5.0, key="v47_brokerage")
        if opt_start > opt_end:
            st.error("Options start date must be on or before end date.")
        elif opt_start < opt_hist.index.min().date() or opt_end > opt_hist.index.max().date():
            st.warning("The selected option test dates must be within the saved NIFTY Spot candle coverage.")
        else:
            call_file = os.path.join(DATA_DIR, f"nifty_options_call_{opt_expiry_flag.lower()}_{opt_expiry_code}_{opt_interval}m.csv")
            put_file = os.path.join(DATA_DIR, f"nifty_options_put_{opt_expiry_flag.lower()}_{opt_expiry_code}_{opt_interval}m.csv")
            fetch_options = st.button("Download / refresh Dhan NIFTY option premiums (CE + PE)", type="primary", key="v47_fetch_options")
            if fetch_options:
                try:
                    opt_client = get_client()
                    prog = st.progress(0, text="Starting Dhan one-day-at-a-time option history download...")
                    def opt_progress(p, msg):
                        prog.progress(min(int(p*100),100), text=msg)
                    with st.spinner("Downloading actual NIFTY option premium candles..."):
                        cache_root = os.path.join(DATA_DIR, "nifty_options_daily_cache")
                        call_df = download_rolling_option(opt_client, opt_start, opt_end+timedelta(days=1), "CALL", opt_expiry_flag, opt_expiry_code, opt_strike, opt_interval, opt_progress, cache_dir=cache_root)
                        put_df = download_rolling_option(opt_client, opt_start, opt_end+timedelta(days=1), "PUT", opt_expiry_flag, opt_expiry_code, opt_strike, opt_interval, opt_progress, cache_dir=cache_root)
                    call_df.to_csv(call_file, index_label="timestamp")
                    put_df.to_csv(put_file, index_label="timestamp")
                    st.session_state["v47_options_downloaded"] = {"call": call_file, "put": put_file, "start": str(opt_start), "end": str(opt_end)}
                    st.success(f"Saved actual option candles: CE {len(call_df):,} bars; PE {len(put_df):,} bars.")
                    st.caption(f"Files saved on persistent disk: {call_file} and {put_file}")
                except Exception as exc:
                    st.error(f"Dhan option-history download failed: {exc}")
            call_exists, put_exists = os.path.exists(call_file), os.path.exists(put_file)
            if call_exists and put_exists:
                st.success("Saved CE and PE premium datasets are available for this expiry setting and timeframe.")
                if st.button("Run V4.7 option-buying backtest", type="primary", key="v47_run_options"):
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
                            st.session_state["v47_option_results"] = {
                                "call": (call_trades, call_metrics), "put": (put_trades, put_metrics),
                                "start": opt_start, "end": opt_end, "expiry": f"{opt_expiry_flag} / code {opt_expiry_code}",
                                "timeframe": opt_interval, "quantity": int(option_qty),
                            }
                    except Exception as exc:
                        st.error(f"V4.7 option backtest failed: {exc}")
            if "v47_option_results" in st.session_state:
                vr = st.session_state["v47_option_results"]
                st.markdown(f"### Results: {vr['start']} to {vr['end']} • {vr['expiry']} • {vr['timeframe']}m")
                st.caption(f"Quantity: {vr['quantity']} units per trade. P&L is in INR from actual option premium OHLC, less simulated slippage and entered brokerage/fees estimate.")
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
                        f"v47_nifty_{key.lower()}_option_trades.csv", "text/csv", key=f"v47_download_{key}")
                st.info("Execution model: NIFTY signal confirmed at candle close; option bought at next available option candle open. CE uses the bullish 50% Strong Breakout signal. PE uses bearish BOS/CHoCH, ORB, previous-day-break and liquidity-sweep events (not a mirrored bearish CHoCH-HL model). Premium stop/target checked against option OHLC; if both touch in one candle, stop is assumed first. Trades are intraday only and close when the rolling strike changes or at session square-off. No orders are placed.")
