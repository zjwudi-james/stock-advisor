"""A 股股票分析顾问 —— Streamlit 界面。"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from data_fetcher import (
    get_stock_list, search_stock, get_realtime, get_history,
    get_fund_flow, get_financials, get_market_overview,
)
from analyzer import StockAnalyzer

st.set_page_config(
    page_title="A股分析顾问",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---- 样式 ----
st.markdown("""
<style>
    .rec-buy { background: #dc3545; color: white; padding: 12px 24px; border-radius: 8px; font-size: 22px; font-weight: bold; text-align: center; }
    .rec-accumulate { background: #fd7e14; color: white; padding: 12px 24px; border-radius: 8px; font-size: 22px; font-weight: bold; text-align: center; }
    .rec-hold { background: #6c757d; color: white; padding: 12px 24px; border-radius: 8px; font-size: 22px; font-weight: bold; text-align: center; }
    .rec-reduce { background: #0d6efd; color: white; padding: 12px 24px; border-radius: 8px; font-size: 22px; font-weight: bold; text-align: center; }
    .rec-sell { background: #198754; color: white; padding: 12px 24px; border-radius: 8px; font-size: 22px; font-weight: bold; text-align: center; }
    .dim-card { background: #f8f9fa; border-radius: 8px; padding: 16px; text-align: center; border: 1px solid #dee2e6; }
</style>
""", unsafe_allow_html=True)

rec_styles = {
    "买入": "rec-buy",
    "增持": "rec-accumulate",
    "持有": "rec-hold",
    "减持": "rec-reduce",
    "卖出": "rec-sell",
}

# ---- 初始化 ----
if "stock_list" not in st.session_state:
    with st.spinner("加载股票列表..."):
        st.session_state.stock_list = get_stock_list()
if "analyzer" not in st.session_state:
    st.session_state.analyzer = StockAnalyzer()
if "market_cache" not in st.session_state:
    st.session_state.market_cache = None


# ---- 侧边栏 ----
with st.sidebar:
    st.header("关于本工具")
    st.markdown("""
    **A 股股票分析顾问**

    基于四个维度综合评分：
    - **技术面** (25%) — MA/MACD/RSI/KDJ/BOLL
    - **资金面** (25%) — 主力资金流向
    - **情绪面** (25%) — 市场广度/量比/换手率
    - **基本面** (25%) — PE/PB/ROE/增长

    数据来源：雪球 + 东方财富 + 新浪（akshare）

    ⚠️ 仅供参考，不构成投资建议
    """)
    st.caption(f"已加载 {len(st.session_state.stock_list)} 只股票")

    st.divider()
    if st.button("刷新市场数据", use_container_width=True):
        with st.spinner("获取市场概况（约15秒）..."):
            st.session_state.market_cache = get_market_overview()
    if st.session_state.market_cache:
        m = st.session_state.market_cache
        st.caption(f"涨: {m['up_count']} | 跌: {m['down_count']}")
        st.caption(f"成交额: {m['total_amount']/1e8:.0f}亿")


# ---- 主界面 ----
st.title("A股股票分析顾问")

col1, col2 = st.columns([3, 1])
with col1:
    keyword = st.text_input(
        "输入股票代码或名称", placeholder="例如：600519 或 茅台",
        key="search_input"
    )
with col2:
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("随机一只", use_container_width=True):
        import random
        if st.session_state.stock_list:
            code, name = random.choice(st.session_state.stock_list)
            st.session_state.search_input = name
            st.rerun()

if not keyword:
    st.info("请输入股票代码或名称开始分析")
    st.stop()

# 搜索匹配
matches = search_stock(keyword, st.session_state.stock_list)
if not matches:
    st.warning("未找到匹配的股票，请检查输入")
    st.stop()

# 股票选择
selected = st.selectbox(
    f"找到 {len(matches)} 只股票，请选择",
    matches,
    format_func=lambda x: f"{x[1]}（{x[0]}）",
    key="selected_stock"
)

if not selected:
    st.stop()

code, name = selected

# ============ 数据获取 ============
with st.spinner(f"正在获取 {name}({code}) 的实时数据..."):
    realtime = get_realtime(code)
    market = st.session_state.market_cache

with st.spinner("正在获取历史K线..."):
    hist = get_history(code, days=250)

with st.spinner("正在获取资金流向..."):
    flow = get_fund_flow(code, days=20)

with st.spinner("正在获取财务数据..."):
    financials = get_financials(code)

# ============ 分析 ============
result = st.session_state.analyzer.analyze(
    code=code, name=name, realtime=realtime, hist=hist,
    flow=flow, financials=financials, market=market,
)

# ============ 顶部横幅：实时价格 + 建议 ============
st.markdown("---")

if realtime:
    r = realtime
    cols = st.columns([2, 1, 1, 1, 1, 1, 1, 1])
    with cols[0]:
        price_str = f"{r['price']:.2f}"
        change_str = f"{r['change_pct']:+.2f}%"
        st.metric("最新价", price_str, delta=change_str, delta_color="normal")
    with cols[1]:
        st.metric("最高", f"{r['high']:.2f}")
    with cols[2]:
        st.metric("最低", f"{r['low']:.2f}")
    with cols[3]:
        vol_yi = r["amount"] / 1e8
        st.metric("成交额", f"{vol_yi:.1f}亿")
    with cols[4]:
        st.metric("换手率", f"{r['turnover_rate']:.2f}%")
    with cols[5]:
        st.metric("量比", f"{r['volume_ratio']:.2f}")
    with cols[6]:
        st.metric("动态PE", f"{r['pe_dynamic']:.1f}")
    with cols[7]:
        st.metric("市净率", f"{r['pb']:.2f}")

    st.markdown("---")
else:
    st.error("无法获取实时行情数据，请稍后重试")
    st.stop()

# ============ 综合评分 + 建议 ============
rec = result["recommendation"]
score = result["total_score"]

col_score, col_rec, col_note = st.columns([1, 2, 3])
with col_score:
    st.markdown(f"""
    <div style="text-align:center; padding:20px; background:#f8f9fa; border-radius:12px;">
        <div style="font-size:14px; color:#6c757d;">综合评分</div>
        <div style="font-size:48px; font-weight:bold; color:#212529;">{score}</div>
        <div style="font-size:12px; color:#6c757d;">/ 100</div>
    </div>
    """, unsafe_allow_html=True)
with col_rec:
    st.markdown(f"""
    <div class="{rec_styles.get(rec, 'rec-hold')}">
        建议：{rec}
    </div>
    """, unsafe_allow_html=True)
    if result["weight_info"]:
        st.caption(f"⚠️ {result['weight_info']}")
with col_note:
    st.info(result["data_quality_note"])

st.markdown("---")

# ============ 四维度评分卡 ============
dims = result["dimensions"]
dim_names = {"technical": "技术面", "fund_flow": "资金面", "sentiment": "情绪面", "fundamental": "基本面"}
dim_keys = ["technical", "fund_flow", "sentiment", "fundamental"]

cols = st.columns(4)
for i, dk in enumerate(dim_keys):
    dim_data = dims.get(dk, {})
    dim_score = dim_data.get("score")
    with cols[i]:
        if dim_score is not None:
            color = (
                "#dc3545" if dim_score >= 65 else
                "#fd7e14" if dim_score >= 50 else
                "#6c757d" if dim_score >= 35 else
                "#0d6efd" if dim_score >= 20 else
                "#198754"
            )
            st.markdown(f"""
            <div style="text-align:center; padding:12px; background:#f8f9fa; border-radius:8px; border-left:4px solid {color};">
                <div style="font-size:12px; color:#6c757d;">{dim_names[dk]}</div>
                <div style="font-size:32px; font-weight:bold; color:{color};">{dim_score}</div>
                <div style="font-size:11px; color:#6c757d;">/ 100</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div style="text-align:center; padding:12px; background:#f8f9fa; border-radius:8px; border-left:4px solid #ccc;">
                <div style="font-size:12px; color:#6c757d;">{dim_names[dk]}</div>
                <div style="font-size:18px; color:#999; margin-top:8px;">无数据</div>
            </div>
            """, unsafe_allow_html=True)

# ============ 技术面详细分析 ============
st.markdown("---")

tech_data = dims.get("technical", {})
with st.expander(f"技术面详细分析（得分: {tech_data.get('score', 'N/A')}）", expanded=True):
    if tech_data and hist is not None:
        # 信号
        signals = tech_data.get("signals", [])
        if signals:
            cols_sig = st.columns(3)
            for idx, s in enumerate(signals):
                cols_sig[idx % 3].markdown(f"• {s}")

        # K线图 + 均线 + MACD
        df = hist.tail(120).copy()
        if not df.empty:
            df["MA5"] = df["close"].rolling(5).mean()
            df["MA10"] = df["close"].rolling(10).mean()
            df["MA20"] = df["close"].rolling(20).mean()
            df["MA60"] = df["close"].rolling(60).mean()

            ema12 = df["close"].ewm(span=12).mean()
            ema26 = df["close"].ewm(span=26).mean()
            df["DIF"] = ema12 - ema26
            df["DEA"] = df["DIF"].ewm(span=9).mean()
            df["MACD"] = 2 * (df["DIF"] - df["DEA"])

            # 计算BOLL
            df["BOLL_MID"] = df["close"].rolling(20).mean()
            std20 = df["close"].rolling(20).std()
            df["BOLL_UP"] = df["BOLL_MID"] + 2 * std20
            df["BOLL_DN"] = df["BOLL_MID"] - 2 * std20

            # 计算RSI
            delta = df["close"].diff()
            gain = delta.clip(lower=0)
            loss = (-delta).clip(lower=0)
            avg_gain = gain.ewm(alpha=1/14).mean()
            avg_loss = loss.ewm(alpha=1/14).mean()
            rs = avg_gain / (avg_loss + 1e-10)
            df["RSI"] = 100 - 100 / (1 + rs)

            fig = make_subplots(
                rows=3, cols=1,
                shared_xaxes=True,
                vertical_spacing=0.03,
                row_heights=[0.55, 0.25, 0.20],
                subplot_titles=("K线图", "MACD", "RSI")
            )

            # K线
            fig.add_trace(go.Candlestick(
                x=df["date"], open=df["open"], high=df["high"],
                low=df["low"], close=df["close"],
                name="K线", showlegend=False,
            ), row=1, col=1)
            for ma, color in [("MA5", "blue"), ("MA10", "orange"), ("MA20", "purple"), ("MA60", "gray")]:
                fig.add_trace(go.Scatter(
                    x=df["date"], y=df[ma], name=ma,
                    line=dict(width=1, color=color),
                ), row=1, col=1)

            # BOLL
            fig.add_trace(go.Scatter(
                x=df["date"], y=df["BOLL_UP"], name="BOLL上轨",
                line=dict(width=0.5, color="rgba(0,0,0,0.3)"), showlegend=False,
            ), row=1, col=1)
            fig.add_trace(go.Scatter(
                x=df["date"], y=df["BOLL_DN"], name="BOLL下轨",
                line=dict(width=0.5, color="rgba(0,0,0,0.3)"),
                fill="tonexty", fillcolor="rgba(0,0,0,0.05)", showlegend=False,
            ), row=1, col=1)

            # MACD
            colors_macd = ["#ef5350" if v >= 0 else "#26a69a" for v in df["MACD"]]
            fig.add_trace(go.Bar(
                x=df["date"], y=df["MACD"], name="MACD",
                marker_color=colors_macd, showlegend=False,
            ), row=2, col=1)
            fig.add_trace(go.Scatter(
                x=df["date"], y=df["DIF"], name="DIF",
                line=dict(width=1, color="#ef5350"), showlegend=False,
            ), row=2, col=1)
            fig.add_trace(go.Scatter(
                x=df["date"], y=df["DEA"], name="DEA",
                line=dict(width=1, color="#26a69a"), showlegend=False,
            ), row=2, col=1)

            # RSI
            fig.add_trace(go.Scatter(
                x=df["date"], y=df["RSI"], name="RSI",
                line=dict(width=1.5, color="#7b1fa2"), showlegend=False,
            ), row=3, col=1)
            fig.add_hline(y=70, line_dash="dash", line_color="gray", row=3, col=1)
            fig.add_hline(y=30, line_dash="dash", line_color="gray", row=3, col=1)

            fig.update_layout(
                height=700, margin=dict(l=10, r=10, t=30, b=10),
                xaxis_rangeslider_visible=False,
                hovermode="x unified",
            )
            fig.update_xaxes(showgrid=False)
            fig.update_yaxes(showgrid=True, gridcolor="#f0f0f0")
            st.plotly_chart(fig, use_container_width=True)

# ============ 资金面详细分析 ============
flow_data = dims.get("fund_flow", {})
with st.expander(f"资金面详细分析（得分: {flow_data.get('score', 'N/A')}）"):
    if flow_data and flow is not None and not flow.empty:
        signals = flow_data.get("signals", [])
        for s in signals:
            st.markdown(f"• {s}")

        if "main_net" in flow.columns:
            fdf = flow.tail(20).copy()
            fdf["net_str"] = fdf["main_net"].apply(
                lambda x: f"{x/1e4:.0f}万" if abs(x) < 1e8 else f"{x/1e8:.1f}亿"
            )
            fig = go.Figure()
            colors_flow = ["#ef5350" if v >= 0 else "#26a69a" for v in fdf["main_net"]]
            fig.add_trace(go.Bar(
                x=fdf["date"], y=fdf["main_net"] / 1e4,
                marker_color=colors_flow,
                text=fdf["net_str"], textposition="outside",
                name="主力净流入(万)",
            ))
            fig.add_hline(y=0, line_width=1, line_color="gray")
            fig.update_layout(
                height=350, margin=dict(l=10, r=10, t=10, b=10),
                yaxis_title="万元",
            )
            st.plotly_chart(fig, use_container_width=True)

            # 各类型资金表格
            st.caption("近5日资金流向明细")
            recent = fdf.tail(5).sort_values("date", ascending=False)
            display_cols = ["date"]
            for c in ["main_net", "super_large_net", "large_net", "mid_net", "small_net"]:
                if c in recent.columns:
                    display_cols.append(c)
            if len(display_cols) > 1:
                disp = recent[display_cols].copy()
                for c in display_cols:
                    if c != "date":
                        disp[c] = disp[c].apply(lambda x: f"{x/1e4:.0f}万")
                disp = disp.rename(columns={
                    "date": "日期", "main_net": "主力净流入",
                    "super_large_net": "超大单", "large_net": "大单",
                    "mid_net": "中单", "small_net": "小单",
                })
                st.dataframe(disp, use_container_width=True, hide_index=True)
    else:
        st.caption("暂无资金流向数据")

# ============ 情绪面详细分析 ============
sent_data = dims.get("sentiment", {})
with st.expander(f"情绪面详细分析（得分: {sent_data.get('score', 'N/A')}）"):
    if sent_data:
        signals = sent_data.get("signals", [])
        for s in signals:
            st.markdown(f"• {s}")
        st.caption("""
        **关于情绪面数据**：本系统的情绪面评分使用市场活跃度替代指标
        （市场涨跌比、涨跌停比、量比、换手率），而非真实的新闻或社交媒体情感分析。
        这些指标反映市场活跃程度，但不能直接代表投资者对特定股票的情绪态度。
        """)

    if market:
        st.markdown("**今日市场概况**")
        mk_cols = st.columns(6)
        mk_cols[0].metric("上涨家数", market.get("up_count", 0))
        mk_cols[1].metric("下跌家数", market.get("down_count", 0))
        mk_cols[2].metric("涨停", market.get("limit_up", 0))
        mk_cols[3].metric("跌停", market.get("limit_down", 0))
        mk_cols[4].metric("平均涨跌", f"{market.get('avg_change', 0):.2f}%")
        mk_cols[5].metric("总成交额", f"{market.get('total_amount', 0)/1e8:.0f}亿")

# ============ 基本面详细分析 ============
fund_data = dims.get("fundamental", {})
with st.expander(f"基本面详细分析（得分: {fund_data.get('score', 'N/A')}）"):
    if fund_data:
        signals = fund_data.get("signals", [])
        for s in signals:
            st.markdown(f"• {s}")

    if financials:
        st.markdown("**最新财务指标**")
        f_cols = st.columns(4)
        fin_items = [
            ("ROE", "roe", "%", "净资产收益率"),
            ("毛利率", "gross_margin", "%", ""),
            ("营收增长率", "revenue_growth", "%", ""),
            ("净利润增长率", "profit_growth", "%", ""),
            ("资产负债率", "debt_ratio", "%", ""),
            ("每股收益", "eps", "元", ""),
            ("流动比率", "current_ratio", "", ""),
            ("速动比率", "quick_ratio", "", ""),
        ]
        for idx, (label, key, unit, _) in enumerate(fin_items):
            val = financials.get(key)
            if val is not None and val != 0:
                f_cols[idx % 4].metric(label, f"{val:.2f}{unit}")
        st.caption("""
        **关于基本面数据**：数据来源于东方财富，更新频率为季度。
        最新数据可能反映上一季度的财务状况，存在一定的时滞。
        建议结合最新公告和行业报告综合判断。
        """)
    else:
        st.caption("暂无详细财务数据。动态PE/PB已在上方实时行情中显示。")

# ============ 免责声明 ============
st.markdown("---")
st.markdown("""
<div style="background:#fff3cd; border:1px solid #ffc107; border-radius:8px; padding:12px; font-size:13px; color:#856404;">
<strong>⚠️ 免责声明</strong><br>
本分析基于公开市场数据（来源：雪球/东方财富/新浪），仅供参考，<strong>不构成投资建议</strong>。<br>
• 技术指标基于历史数据计算，具有天然滞后性<br>
• 情绪面使用市场广度、量比等替代指标，非NLP情感分析<br>
• 基本面数据更新有季度滞后<br>
• 资金流向数据为估算口径，与实际情况可能存在偏差<br>
<strong>投资有风险，入市需谨慎。请结合自身情况独立判断。</strong>
</div>
""", unsafe_allow_html=True)
