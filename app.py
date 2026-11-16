import os
from datetime import date, timedelta
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from dotenv import load_dotenv
from config import SECURITY_ID, EXCHANGE_SEGMENT, INSTRUMENT
from dhan_api import DhanClient, DhanAPIError
from backtest import run_backtest
from live_monitor import check_latest_signal
from telegram_alert import send_telegram

load_dotenv()
st.set_page_config(page_title="NIFTY Pure Price Action Engine", page_icon="📈", layout="wide")
st.title("NIFTY Pure Price Action Engine")
st.caption("Price structure and candle behaviour only • Dhan API • Index-point backtesting • Telegram alerts")

with st.sidebar:
    st.header("Connection")
    token = st.text_input("Dhan access token", value=os.getenv("DHAN_ACCESS_TOKEN",""), type="password")
    client_id = st.text_input("Dhan client ID", value=os.getenv("DHAN_CLIENT_ID",""), type="password")
    st.caption(f"Security ID {SECURITY_ID} • {EXCHANGE_SEGMENT} • {INSTRUMENT}")
    st.divider()
    st.header("Strategy")
    mode = st.selectbox("Trading mode", ["Both","Intraday","Positional"])
    stop_points = st.number_input("Stop loss (NIFTY points)", min_value=5.0, value=30.0, step=5.0)
    target_points = st.number_input("Target (NIFTY points)", min_value=5.0, value=60.0, step=5.0)
    brokerage = st.number_input("Brokerage/round trip (₹)", min_value=0.0, value=20.0, step=5.0)
    slippage = st.number_input("Slippage (points per side)", min_value=0.0, value=1.0, step=0.5)
    st.divider()
    st.caption("No order placement. Signals and backtest only.")

def get_client():
    if not token:
        raise DhanAPIError("Enter Dhan access token in the sidebar or set DHAN_ACCESS_TOKEN.")
    return DhanClient(token, client_id)

tab1, tab2, tab3, tab4 = st.tabs(["Dashboard","Historical download","Backtest","Live alerts"])

with tab1:
    st.subheader("Price-action framework")
    st.markdown("""
    - **Structure:** confirmed swing highs/lows, HH/HL and LH/LL context
    - **Breakouts:** opening range, previous-day levels and break of structure
    - **Retests:** breakout level retest with close confirmation
    - **Rejections:** false break beyond a confirmed swing level
    - **Execution:** backtest enters on the next candle open
    """)
    st.info("Volume and OI are deliberately excluded from signal generation. The NIFTY Spot index is not itself a tradable instrument; reported P&L is in index points, not rupees.")

with tab2:
    st.subheader("Download up to five years of 5-minute candles")
    st.write("Dhan intraday history is requested in batches of no more than 90 calendar days.")
    if st.button("Download five years from Dhan", type="primary"):
        try:
            client = get_client()
            bar = st.progress(0)
            status = st.empty()
            def progress(p, msg):
                bar.progress(p); status.write(msg)
            with st.spinner("Downloading historical candles..."):
                hist = client.download_five_years(5, progress)
            os.makedirs("data", exist_ok=True)
            hist.to_csv("data/nifty_5min.csv", index_label="timestamp")
            st.session_state["hist"] = hist
            st.success(f"Downloaded {len(hist):,} candles, {hist.index.min()} to {hist.index.max()}. Saved data/nifty_5min.csv")
        except Exception as e:
            st.error(str(e))
    if os.path.exists("data/nifty_5min.csv"):
        try:
            saved = pd.read_csv("data/nifty_5min.csv", parse_dates=["timestamp"], index_col="timestamp")
            st.write(f"Saved dataset: **{len(saved):,} candles**")
            st.download_button("Download CSV", saved.to_csv().encode(), "nifty_5min.csv", "text/csv")
        except Exception as e:
            st.warning(f"Could not read saved CSV: {e}")

with tab3:
    st.subheader("Backtest")
    uploaded = st.file_uploader("Upload Dhan CSV if you already have historical candles", type=["csv"])
    hist = st.session_state.get("hist")
    if uploaded is not None:
        hist = pd.read_csv(uploaded)
        if "timestamp" in hist.columns:
            hist["timestamp"] = pd.to_datetime(hist["timestamp"])
            hist = hist.set_index("timestamp")
        else:
            hist.index = pd.to_datetime(hist.index)
    elif hist is None and os.path.exists("data/nifty_5min.csv"):
        hist = pd.read_csv("data/nifty_5min.csv", parse_dates=["timestamp"], index_col="timestamp")
    if hist is not None and len(hist):
        col1,col2 = st.columns(2)
        with col1:
            start = st.date_input("Start date", value=max(hist.index.min().date(), hist.index.max().date()-timedelta(days=365)))
        with col2:
            end = st.date_input("End date", value=hist.index.max().date())
        sample = hist.loc[(hist.index.date >= start) & (hist.index.date <= end)]
        if st.button("Run backtest", type="primary"):
            try:
                modes_to_run = ["Intraday", "Positional"] if mode == "Both" else [mode]
                for run_mode in modes_to_run:
                    st.markdown(f"### {run_mode} results")
                    signals, trades, metrics = run_backtest(sample, run_mode, stop_points=stop_points,
                        target_points=target_points, brokerage_per_trade=brokerage, slippage_points=slippage)
                    cols = st.columns(len(metrics))
                    for c,(k,v) in zip(cols, metrics.items()):
                        c.metric(k, v)
                    if not trades.empty:
                        eq = trades.copy()
                        eq["equity"] = eq["net_points"].cumsum()
                        fig = go.Figure(go.Scatter(x=eq["exit_time"], y=eq["equity"], mode="lines", name=f"{run_mode} equity"))
                        fig.update_layout(height=300, xaxis_title="Exit time", yaxis_title="Net points")
                        st.plotly_chart(fig, use_container_width=True)
                        st.dataframe(trades, use_container_width=True)
                        st.download_button(f"Download {run_mode} trades CSV", trades.to_csv(index=False).encode(), f"nifty_{run_mode.lower()}_trades.csv", "text/csv")
                    else:
                        st.warning(f"No {run_mode.lower()} trades generated in this date range.")
                st.caption("Backtest assumptions: next-bar-open entry, fixed stop/target, conservative stop-first handling if stop and target are touched in the same candle, one position at a time. Brokerage is an estimated user input.")
            except Exception as e:
                st.error(f"Backtest error: {e}")
    else:
        st.info("Download five-year data first or upload a compatible OHLC CSV.")

with tab4:
    st.subheader("Live completed-candle alerts")
    st.warning("This checks for a signal when you click the button. For continuous alerts, deploy an always-on monitor worker; a sleeping free web service cannot guarantee alerts.")
    if st.button("Check latest completed 5-minute candle", type="primary"):
        try:
            result, status = check_latest_signal(get_client(), stop_points, target_points)
            if result:
                st.success("Signal detected")
                st.code(result["message"])
                st.write("Telegram:", "Sent" if result["sent"] else "Not sent")
            else:
                st.info(status)
        except Exception as e:
            st.error(str(e))
    if st.button("Send Telegram test message"):
        ok, msg = send_telegram("NIFTY Pure Price Action Engine: Telegram connection test successful.")
        st.success(msg) if ok else st.error(msg)
