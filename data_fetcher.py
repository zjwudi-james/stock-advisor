"""数据获取层 —— 基于 akshare 的 A 股数据获取，全部免费无需 API Key。

数据源：
- 雪球 (Xueqiu): 个股实时行情（PE/PB/量比/换手率齐全）
- 新浪 (Sina): 全市场行情（用于市场广度统计）
- 东方财富 (EastMoney): 资金流向、财务指标、K线历史
"""

import akshare as ak
import pandas as pd
from typing import Optional


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

def get_realtime(code: str) -> Optional[dict]:
    """获取单只股票实时行情（雪球数据源，含 PE/PB/换手率）。量比通过日线数据另行计算。"""
    try:
        symbol = _to_xq_symbol(code)
        df = ak.stock_individual_spot_xq(symbol=symbol)
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
