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
st.set_page_config(page_title="NIFTY Pure Price Action V3", page_icon="📈", layout="wide")
st.title("NIFTY Pure Price Action Engine — V3")
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

tabs = st.tabs(["Dashboard", "Historical download", "Backtest", "Setup research", "Telegram"])
with tabs[0]:
    st.subheader("Signal design")
    st.markdown("""
    - Confirmed swing pivots; levels become active only after right-side confirmation bars close.
    - BOS and CHoCH from breaks of the latest confirmed swing high/low.
    - Liquidity sweeps: wick through a confirmed swing followed by a close back inside.
    - Opening range breakout: first three 5-minute candles, actionable after 09:30 IST.
    - Previous trading-day high/low breaks.
    - Combined entry = any non-conflicting event above. No extra breakout-distance or retest filter.
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
    st.subheader("Setup-isolation research")
    st.write("Each setup and direction is tested independently. One trade can belong to only one tested setup stream.")
    research_data = load_saved()
    if research_data is not None and not research_data.empty:
        rc1, rc2 = st.columns(2)
        with rc1:
            research_start = st.date_input(
                "Research start date",
                value=max(research_data.index.min().date(), research_data.index.max().date()-timedelta(days=365)),
                key="research_start"
            )
        with rc2:
            research_end = st.date_input(
                "Research end date", value=research_data.index.max().date(), key="research_end"
            )
        research_mode = st.selectbox("Portfolio mode", ["Intraday", "Positional", "Both"], index=0)
        st.caption("Recommended first test: Bullish CHoCH, Intraday, then compare the other isolated setups.")
        if st.button("Run setup-isolation research", type="primary"):
            if research_start > research_end:
                st.error("Research start must be on or before research end.")
            else:
                modes = ("Intraday", "Positional") if research_mode == "Both" else (research_mode,)
                common = dict(
                    stop_points=stop_points,
                    target_points=target_points,
                    brokerage_per_round_trip_inr=brokerage_inr,
                    slippage_points_per_side=slippage,
                    start_date=research_start,
                    end_date=research_end,
                )
                with st.spinner("Testing each setup independently. Five-year tests can take a little time..."):
                    summary, detail = run_setup_isolation(research_data, modes=modes, **common)
                st.session_state["research_summary"] = summary
                st.session_state["research_details"] = detail
                st.success("Setup-isolation tests completed.")
        if "research_summary" in st.session_state:
            summary = st.session_state["research_summary"]
            st.dataframe(summary, use_container_width=True, hide_index=True)
            st.download_button(
                "Download setup comparison CSV",
                summary.to_csv(index=False).encode("utf-8"),
                "nifty_setup_isolation_summary.csv",
                "text/csv",
            )
            selected = st.selectbox("Download detailed trades for setup", list(st.session_state["research_details"].keys()))
            trades = st.session_state["research_details"][selected]
            st.write(f"{selected}: {len(trades)} trades")
            if not trades.empty:
                st.dataframe(trades, use_container_width=True)
            st.download_button(
                "Download selected setup trades",
                trades.to_csv(index=False).encode("utf-8"),
                "nifty_selected_setup_trades.csv",
                "text/csv",
            )
            st.info("Net points include simulated slippage. Brokerage is displayed separately in ₹ and is not subtracted from index points. These are historical simulations, not live performance.")
    else:
        st.info("Download the historical 5-minute dataset first. Setup research runs on the saved OHLC candle data.")

with tabs[4]:
    st.subheader("Telegram test")
    st.write("Continuous BUY/SELL monitoring runs in the separate Render background worker.")
    if st.button("Send Telegram test message"):
        ok, info = send_telegram("NIFTY Pure Price Action V3: Telegram test successful.")
        st.success(info) if ok else st.error(info)
    st.info("Entry alerts are deduplicated by completed candle timestamp. Exit alerts are not yet simulated as live broker-position exits; no orders are placed.")
