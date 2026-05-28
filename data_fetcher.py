"""数据获取层 —— 基于 akshare 的 A 股数据获取，全部免费无需 API Key。

数据源：
- 雪球 (Xueqiu): 个股实时行情（PE/PB/量比/换手率齐全）
- 新浪 (Sina): 全市场行情（用于市场广度统计）
- 东方财富 (EastMoney): 资金流向、财务指标、K线历史
"""

import akshare as ak
import pandas as pd
import numpy as np
from typing import Optional
from datetime import datetime, timedelta
from pathlib import Path


# ---- 工具函数 ----

def _to_xq_symbol(code: str) -> str:
    """将纯数字代码转为雪球格式 (SH600519 / SZ000001)。"""
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return f"SH{code}"
    elif code.startswith(("0", "3", "2")):
        return f"SZ{code}"
    elif code.startswith(("4", "8")):
        return f"BJ{code}"
    return f"SH{code}"


def _to_daily_symbol(code: str) -> str:
    """将纯数字代码转为 stock_zh_a_daily 格式 (sh600519 / sz000001)。"""
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return f"sh{code}"
    elif code.startswith(("0", "3", "2")):
        return f"sz{code}"
    elif code.startswith(("4", "8")):
        return f"bj{code}"
    return f"sh{code}"


def _detect_market(code: str) -> str:
    """判断所属市场 (sh/sz/bj)，用于 fund_flow 接口。"""
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return "sh"
    elif code.startswith(("0", "3", "2")):
        return "sz"
    elif code.startswith(("4", "8")):
        return "bj"
    return "sh"


def _safe_float(val) -> float:
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


# ---- 股票列表 ----

def get_stock_list() -> list[tuple[str, str]]:
    """获取全部 A 股代码和名称列表。"""
    try:
        df = ak.stock_info_a_code_name()
        return [(row["code"], row["name"]) for _, row in df.iterrows()]
    except Exception:
        return []


def search_stock(keyword: str, stock_list: list) -> list[tuple[str, str]]:
    """根据关键字模糊搜索股票。"""
    kw = keyword.strip().upper()
    results = [(c, n) for c, n in stock_list if kw in c or kw in n]
    results.sort(key=lambda x: (x[0] != keyword.zfill(6), x[0]))
    return results[:20]


# ---- 实时行情（雪球） ----

def _load_xq_token() -> Optional[str]:
    """加载雪球 token。优先级：环境变量 > Streamlit secrets > 本地文件。"""
    import os

    # 1. 环境变量（Streamlit Cloud secrets 也会注入为环境变量）
    token = os.environ.get("XQ_TOKEN") or os.environ.get("xq_token")
    if token:
        return token

    # 2. Streamlit secrets（TOML 格式，本地 + 云端通用）
    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            token = st.secrets.get("xq_token") or st.secrets.get("XQ_TOKEN")
            if token:
                return token
    except Exception:
        pass

    # 3. 本地 .xqtoken 文件（开发环境）
    token_path = Path(__file__).parent / ".xqtoken"
    if token_path.exists():
        return token_path.read_text().strip()

    return None


def get_realtime(code: str) -> Optional[dict]:
    """获取单只股票实时行情（雪球数据源，含 PE/PB/换手率）。"""
    try:
        symbol = _to_xq_symbol(code)
        token = _load_xq_token()
        df = ak.stock_individual_spot_xq(symbol=symbol, token=token)
        data = dict(zip(df["item"].values, df["value"].values))
        return {
            "code": str(code).zfill(6),
            "name": str(data.get("名称", "")),
            "price": _safe_float(data.get("现价")),
            "change_pct": _safe_float(data.get("涨幅")),
            "change_amount": _safe_float(data.get("涨跌")),
            "volume": _safe_float(data.get("成交量")),
            "amount": _safe_float(data.get("成交额")),
            "amplitude": _safe_float(data.get("振幅")),
            "high": _safe_float(data.get("最高")),
            "low": _safe_float(data.get("最低")),
            "open": _safe_float(data.get("今开")),
            "pre_close": _safe_float(data.get("昨收")),
            "volume_ratio": _compute_volume_ratio(code),
            "turnover_rate": _safe_float(data.get("周转率")),
            "pe_dynamic": _safe_float(data.get("市盈率(动)")),
            "pe_ttm": _safe_float(data.get("市盈率(TTM)")),
            "pe_static": _safe_float(data.get("市盈率(静)")),
            "pb": _safe_float(data.get("市净率")),
            "eps": _safe_float(data.get("每股收益")),
            "nav_per_share": _safe_float(data.get("每股净资产")),
            "total_market_cap": _safe_float(data.get("流通值")),
            "high_52w": _safe_float(data.get("52周最高")),
            "low_52w": _safe_float(data.get("52周最低")),
            "dividend_yield": _safe_float(data.get("股息率(TTM)")),
        }
    except Exception:
        return None


def _compute_volume_ratio(code: str) -> float:
    """计算量比：当日成交量 / 近5日日均成交量。"""
    try:
        hist = get_history(code, days=10)
        if hist is None or hist.empty or len(hist) < 6:
            return 1.0
        vols = hist["volume"].values
        today_vol = vols[-1]
        avg_5 = vols[-6:-1].mean()
        if avg_5 > 0:
            return round(today_vol / avg_5, 2)
        return 1.0
    except Exception:
        return 1.0


# ---- 实时行情（Baostock 备用，雪球已失效） ----

def _to_bs_symbol(code: str) -> str:
    """将纯数字代码转为 baostock 格式 (sh.600519 / sz.000001)。"""
    code = str(code).zfill(6)
    if code.startswith(("6", "9")):
        return f"sh.{code}"
    elif code.startswith(("0", "3", "2")):
        return f"sz.{code}"
    elif code.startswith(("4", "8")):
        return f"bj.{code}"
    return f"sh.{code}"


def get_sina_spot_dict() -> Optional[dict]:
    """获取 Sina 全市场行情，返回 {纯代码: row_data} 字典。一次调用，全体缓存。

    带重试机制：Sina 接口偶发 JSON 解析失败，重试最多 3 次。
    """
    import time
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_spot()
            if df is None or df.empty:
                if attempt < 2:
                    time.sleep(5)
                continue
            result = {}
            for _, row in df.iterrows():
                code = str(row[df.columns[0]])
                if code.startswith(("sh", "sz", "bj")) and len(code) >= 2:
                    pure = code[2:]
                    result[pure] = row
            if result:
                return result
            if attempt < 2:
                time.sleep(5)
        except Exception:
            if attempt < 2:
                time.sleep(5)
    return None


def get_realtime_today(code: str, sina_dict: Optional[dict] = None) -> Optional[dict]:
    """获取当日行情。优先级：雪球 > Sina > Baostock。

    如果配置了 .xqtoken，优先用雪球实时接口（有 PE/PB/换手率）。
    否则尝试 Sina 当天数据，最后回退 Baostock。
    """
    # 第一步：尝试雪球（有 token 且可用时最快最全）
    xq = get_realtime(code)
    if xq is not None and xq["price"] > 0:
        xq["_source"] = "xueqiu"
        return xq

    result = None

    # 第二步：从 Sina 字典获取当天价格（如果有）
    if sina_dict is not None and code in sina_dict:
        row = sina_dict[code]
        try:
            # row 是 pandas Series，用 .iloc 按位置访问
            result = {
                "code": str(code).zfill(6),
                "name": str(row.iloc[1]) if len(row) > 1 else "",
                "price": _safe_float(row.iloc[2]),
                "change_pct": _safe_float(row.iloc[4]),
                "change_amount": _safe_float(row.iloc[3]),
                "volume": _safe_float(row.iloc[11]),
                "amount": _safe_float(row.iloc[12]),
                "high": _safe_float(row.iloc[9]),
                "low": _safe_float(row.iloc[10]),
                "open": _safe_float(row.iloc[8]),
                "pre_close": _safe_float(row.iloc[7]),
                "amplitude": 0, "volume_ratio": 1.0,
                "turnover_rate": 0, "pe_dynamic": 0, "pe_ttm": 0,
                "pb": 0, "eps": 0, "nav_per_share": 0,
                "total_market_cap": 0, "high_52w": 0, "low_52w": 0,
                "dividend_yield": 0, "_source": "sina",
            }
            if result["pre_close"] > 0:
                result["amplitude"] = round(
                    (result["high"] - result["low"]) / result["pre_close"] * 100, 2
                )
        except (ValueError, TypeError, IndexError):
            result = None

    # 第二步：Sina 失败则回退 Baostock
    if result is None:
        bs_data = get_realtime_bs(code)
        if bs_data:
            bs_data["_source"] = "baostock"
            return bs_data
        return None

    # 第三步：用 Baostock 补充 PE/PB/换手率
    try:
        import baostock as bs
        symbol = _to_bs_symbol(code)
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        bs.login()
        try:
            rs = bs.query_history_k_data_plus(
                symbol, "date,peTTM,pbMRQ,turn,volume",
                start_date=start_date, end_date=end_date,
                frequency="d", adjustflag="3"
            )
            if rs.error_code == "0" and rs.data:
                latest = rs.data[-1]
                result["pe_dynamic"] = float(latest[1]) if len(latest) > 1 else 0
                result["pe_ttm"] = float(latest[1]) if len(latest) > 1 else 0
                result["pb"] = float(latest[2]) if len(latest) > 2 else 0
                result["turnover_rate"] = float(latest[3]) if len(latest) > 3 else 0

                # 量比 = 今日量 / 近5日均量（用 Baostock T-5~T-1 数据）
                if result["volume"] > 0 and len(rs.data) >= 6:
                    vols = [float(r[4]) for r in rs.data[-6:-1]]
                    avg_vol = sum(vols) / len(vols)
                    if avg_vol > 0:
                        result["volume_ratio"] = round(result["volume"] / avg_vol, 2)
        finally:
            bs.logout()
    except Exception:
        pass

    return result


def get_realtime_bs(code: str) -> Optional[dict]:
    """获取单只股票最新日线数据（Baostock，含 PE/PB/换手率）。

    替代已失效的雪球 get_realtime()。Baostock 数据为最近交易日收盘数据。
    """
    try:
        import baostock as bs
        symbol = _to_bs_symbol(code)

        # 获取最近2个交易日数据（确保有昨收）
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")

        bs.login()
        try:
            rs = bs.query_history_k_data_plus(
                symbol,
                "date,open,high,low,close,preclose,volume,amount,peTTM,pbMRQ,turn",
                start_date=start_date, end_date=end_date,
                frequency="d", adjustflag="3"
            )
            if rs.error_code != "0" or not rs.data:
                return None

            # 字段: date[0], open[1], high[2], low[3], close[4], preclose[5],
            #        volume[6], amount[7], peTTM[8], pbMRQ[9], turn[10]
            latest = rs.data[-1]
            cur_price = float(latest[4])  # close
            pre_close = float(latest[5])  # preclose
            vol_today = float(latest[6])
            amount = float(latest[7])
            high = float(latest[2])
            low = float(latest[3])
            open_price = float(latest[1])

            # 涨跌幅
            if pre_close > 0:
                change_pct = (cur_price - pre_close) / pre_close * 100
            else:
                change_pct = 0

            # 振幅
            if pre_close > 0:
                amplitude = (high - low) / pre_close * 100
            else:
                amplitude = 0

            # 量比（近5日均量）
            if len(rs.data) >= 6:
                vols = [float(r[6]) for r in rs.data[-6:-1]]
                avg_vol = sum(vols) / len(vols)
                volume_ratio = round(vol_today / avg_vol, 2) if avg_vol > 0 else 1.0
            else:
                volume_ratio = 1.0

            return {
                "code": str(code).zfill(6),
                "name": "",
                "price": cur_price,
                "change_pct": round(change_pct, 2),
                "change_amount": round(cur_price - pre_close, 2),
                "volume": vol_today,
                "amount": amount,
                "amplitude": round(amplitude, 2),
                "high": high,
                "low": low,
                "open": open_price,
                "pre_close": pre_close,
                "volume_ratio": volume_ratio,
                "turnover_rate": float(latest[10]) if len(latest) > 10 else 0,
                "pe_dynamic": float(latest[8]) if len(latest) > 8 else 0,
                "pe_ttm": float(latest[8]) if len(latest) > 8 else 0,
                "pe_static": 0,
                "pb": float(latest[9]) if len(latest) > 9 else 0,
                "eps": 0,
                "nav_per_share": 0,
                "total_market_cap": 0,
                "high_52w": 0,
                "low_52w": 0,
                "dividend_yield": 0,
            }
        finally:
            bs.logout()
    except Exception:
        return None


# ---- 历史 K 线 ----

def get_history(code: str, days: int = 250) -> Optional[pd.DataFrame]:
    """获取个股历史日 K 线数据（前复权）。"""
    try:
        symbol = _to_daily_symbol(code)
        df = ak.stock_zh_a_daily(
            symbol=symbol, start_date="20240101", end_date="20991231", adjust="qfq"
        )
        if df.empty:
            return None
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").tail(days)
        # ensure required columns exist
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            if col not in df.columns:
                return None
        return df[["date", "open", "high", "low", "close", "volume", "amount"]]
    except Exception:
        return None


# ---- 资金流向 ----

def get_fund_flow(code: str, days: int = 20) -> Optional[pd.DataFrame]:
    """获取个股资金流向数据。"""
    try:
        market = _detect_market(code)
        df = ak.stock_individual_fund_flow(stock=str(code), market=market)
        if df.empty:
            return None
        df = df.rename(columns={
            "日期": "date",
            "主力净流入-净额": "main_net",
            "主力净流入-净占比": "main_ratio",
            "超大单净流入-净额": "super_large_net",
            "超大单净流入-净占比": "super_large_ratio",
            "大单净流入-净额": "large_net",
            "大单净流入-净占比": "large_ratio",
            "中单净流入-净额": "mid_net",
            "中单净流入-净占比": "mid_ratio",
            "小单净流入-净额": "small_net",
            "小单净流入-净占比": "small_ratio",
        })
        cols = ["date"]
        for c in ["main_net", "main_ratio", "super_large_net", "super_large_ratio",
                   "large_net", "large_ratio", "mid_net", "mid_ratio",
                   "small_net", "small_ratio"]:
            if c in df.columns:
                cols.append(c)
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").tail(days)[cols]
    except Exception:
        return None


# ---- 财务指标 ----

def get_financials(code: str) -> Optional[dict]:
    """获取个股最新财务指标。"""
    try:
        symbol = str(code).zfill(6)
        df = ak.stock_financial_analysis_indicator(symbol=symbol, start_year="2024")
        if df.empty:
            return None
        latest = df.iloc[-1]
        return {
            "roe": _safe_float(latest.get("净资产收益率(%)")),
            "gross_margin": _safe_float(latest.get("销售毛利率(%)")),
            "net_margin": _safe_float(latest.get("销售净利率(%)")),
            "revenue_growth": _safe_float(latest.get("营业收入增长率(%)")),
            "profit_growth": _safe_float(latest.get("净利润增长率(%)")),
            "debt_ratio": _safe_float(latest.get("资产负债率(%)")),
            "eps": _safe_float(latest.get("每股收益")),
            "current_ratio": _safe_float(latest.get("流动比率")),
            "quick_ratio": _safe_float(latest.get("速动比率")),
        }
    except Exception:
        return None


# ---- 指数行情 ----

def get_index_data() -> Optional[dict]:
    """获取三大指数实时行情和趋势判断（雪球实时数据，无需历史K线，速度较快）。

    趋势判断基于价格在52周范围内的位置：
    - 价格处于52周高位（>70%分位）且今日上涨 → 上涨趋势
    - 价格处于52周低位（<30%分位）且今日下跌 → 下跌趋势
    - 其他 → 震荡
    """
    indices = {
        "SH000001": "上证指数",
        "SZ399001": "深证成指",
        "SZ399006": "创业板指",
    }
    result = {}
    for code, name in indices.items():
        try:
            spot_df = ak.stock_individual_spot_xq(symbol=code)
            if spot_df.empty:
                continue
            spot = dict(zip(spot_df["item"].values, spot_df["value"].values))
            cur_price = _safe_float(spot.get("现价"))
            change_pct = _safe_float(spot.get("涨幅"))
            high_52w = _safe_float(spot.get("52周最高"))
            low_52w = _safe_float(spot.get("52周最低"))
            if cur_price <= 0:
                continue

            # 基于价格在52周范围的位置判断趋势
            if high_52w > 0 and low_52w > 0 and high_52w > low_52w:
                pos_52w = (cur_price - low_52w) / (high_52w - low_52w)
                if pos_52w > 0.7 and change_pct > 0:
                    trend = "上涨"
                elif pos_52w < 0.3 and change_pct < 0:
                    trend = "下跌"
                elif pos_52w > 0.6:
                    trend = "偏强震荡"
                elif pos_52w < 0.4:
                    trend = "偏弱震荡"
                else:
                    trend = "震荡"
            else:
                trend = "数据不足"

            result[code] = {
                "name": name,
                "price": cur_price,
                "change_pct": change_pct,
                "trend": trend,
                "high_52w": round(high_52w, 2) if high_52w > 0 else None,
                "low_52w": round(low_52w, 2) if low_52w > 0 else None,
            }
        except Exception:
            continue
    return result if result else None


def get_sector_strength() -> Optional[list]:
    """获取行业板块涨跌幅排名（用同花顺行业列表，新浪板块行情）。"""
    try:
        # 尝试同花顺行业分类
        industry_df = ak.stock_board_industry_name_ths()
        if industry_df.empty:
            return None
        result = []
        # 列名可能是乱码，用位置访问：第一列是 name, 第二列是 code
        cols = industry_df.columns.tolist()
        name_col = cols[0]
        code_col = cols[1]
        for _, row in industry_df.iterrows():
            result.append({
                "name": str(row[name_col]),
                "code": str(row[code_col]),
                "change_pct": 0.0,  # 同花顺行业列表无涨跌幅，后续用雪球补充
            })
        return result if result else None
    except Exception:
        return None


def get_stock_sector(code: str) -> Optional[str]:
    """获取个股所属行业板块（雪球）。"""
    try:
        df = ak.stock_individual_basic_info_xq(symbol=_to_xq_symbol(code))
        if df.empty:
            return None
        industry_row = df[df["item"] == "affiliate_industry"]
        if not industry_row.empty:
            raw = str(industry_row.iloc[0]["value"])
            # 解析 JSON 格式的行业信息 {'ind_code': 'BK0088', 'ind_name': '白酒'}
            import json
            try:
                obj = json.loads(raw.replace("'", '"'))
                return obj.get("ind_name", raw)
            except (json.JSONDecodeError, ValueError):
                return raw
        return None
    except Exception:
        return None


def get_sector_rank(sector_name: str, sector_list: list) -> Optional[dict]:
    """在板块排名中查找指定板块的位置。"""
    if not sector_name or not sector_list:
        return None
    for idx, s in enumerate(sector_list):
        if sector_name in s["name"] or s["name"] in sector_name:
            return {
                "name": s["name"],
                "change_pct": s.get("change_pct", 0),
                "rank": idx + 1,
                "total": len(sector_list),
                "percentile": round((idx + 1) / len(sector_list) * 100, 1),
            }
    return None


# ---- 市场概况 ----

def get_market_overview() -> Optional[dict]:
    """获取全市场行情概况（涨跌家数等，使用新浪数据源）。"""
    try:
        df = ak.stock_zh_a_spot()
        if df.empty:
            return None
        # 新浪列：代码, 名称, 最新价, 涨跌额, 涨跌幅, 买入, 卖出, 昨收, 今开, 最高, 最低, 成交量, 成交额, 时间
        change_col = df.columns[4]  # 涨跌幅
        vol_col = df.columns[11]     # 成交量
        amt_col = df.columns[12]     # 成交额

        total = len(df)
        up_count = int((df[change_col] > 0).sum())
        down_count = int((df[change_col] < 0).sum())
        flat_count = total - up_count - down_count
        avg_change = float(df[change_col].mean())
        total_amount = float(df[amt_col].sum())

        return {
            "total": total,
            "up_count": up_count,
            "down_count": down_count,
            "flat_count": flat_count,
            "avg_change": avg_change,
            "total_amount": total_amount,
            "limit_up": 0,
            "limit_down": 0,
        }
    except Exception:
        return None
