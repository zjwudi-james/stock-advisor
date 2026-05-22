"""评分引擎 —— 技术面、资金面、情绪面、基本面四维度综合评分。"""

import pandas as pd
import numpy as np
from typing import Optional


class StockAnalyzer:
    """A 股多因子评分器。每个维度 0-100 分，等权汇总。"""

    DIMENSION_WEIGHTS = {
        "technical": 0.25,
        "fund_flow": 0.25,
        "sentiment": 0.25,
        "fundamental": 0.25,
    }

    # ---- 主入口 ----

    def analyze(
        self,
        code: str,
        name: str,
        realtime: Optional[dict],
        hist: Optional[pd.DataFrame],
        flow: Optional[pd.DataFrame],
        financials: Optional[dict],
        market: Optional[dict],
    ) -> dict:
        dims = {}
        if hist is not None and not hist.empty:
            dims["technical"] = self.score_technical(realtime, hist)
        if flow is not None and not flow.empty:
            dims["fund_flow"] = self.score_fund_flow(flow)
        if market is not None:
            dims["sentiment"] = self.score_sentiment(realtime, hist, market)
        if financials is not None or (realtime and realtime.get("pe_dynamic")):
            dims["fundamental"] = self.score_fundamental(financials, realtime)

        total, recommendation, weight_info = self._aggregate(dims)
        data_quality = self._data_quality_note(dims)

        return {
            "code": code,
            "name": name,
            "total_score": total,
            "recommendation": recommendation,
            "dimensions": dims,
            "weight_info": weight_info,
            "data_quality_note": data_quality,
        }

    # ---- 聚合与建议 ----

    def _aggregate(self, dims: dict) -> tuple:
        active = {k: v for k, v in dims.items() if v.get("score") is not None}
        if not active:
            return 50, "数据不足", "全部数据获取失败，默认中性50分"

        total_weight = sum(self.DIMENSION_WEIGHTS[k] for k in active)
        scale = 1.0 / total_weight if total_weight > 0 else 1.0
        total = 0.0
        for k, v in active.items():
            total += v["score"] * self.DIMENSION_WEIGHTS[k] * scale

        total = round(total)

        if total >= 80:
            rec = "买入"
        elif total >= 65:
            rec = "增持"
        elif total >= 40:
            rec = "持有"
        elif total >= 25:
            rec = "减持"
        else:
            rec = "卖出"

        missing = [k for k in self.DIMENSION_WEIGHTS if k not in dims]
        weight_info = ""
        if missing:
            names = {"technical": "技术面", "fund_flow": "资金面",
                     "sentiment": "情绪面", "fundamental": "基本面"}
            weight_info = "缺少维度：" + "、".join(names.get(m, m) for m in missing) + \
                          "，已自动重分配权重"

        return total, rec, weight_info

    def _data_quality_note(self, dims: dict) -> str:
        missing = []
        for k in ["technical", "fund_flow", "sentiment", "fundamental"]:
            if k not in dims or dims[k].get("score") is None:
                names = {"technical": "技术面", "fund_flow": "资金面",
                         "sentiment": "情绪面", "fundamental": "基本面"}
                missing.append(names[k])
        if not missing:
            return "所有维度数据获取正常"
        return "数据缺失：" + "、".join(missing) + "。评分基于现有维度计算，仅供参考。"

    # ============ 技术面（25%） ============

    def score_technical(self, realtime: Optional[dict], hist: pd.DataFrame) -> dict:
        closes = hist["close"].values
        highs = hist["high"].values
        lows = hist["low"].values

        trend = self._score_trend(closes)
        momentum = self._score_momentum(closes, highs, lows)
        volatility = self._score_volatility(closes, highs, lows)

        score = trend["score"] * 0.40 + momentum["score"] * 0.30 + volatility["score"] * 0.30
        all_signals = trend["signals"] + momentum["signals"] + volatility["signals"]
        return {
            "score": round(score),
            "sub_scores": {"趋势": trend, "动量": momentum, "波动": volatility},
            "signals": all_signals,
        }

    def _score_trend(self, closes: np.ndarray) -> dict:
        score = 50.0
        sigs = []
        ma5 = pd.Series(closes).rolling(5).mean().values
        ma10 = pd.Series(closes).rolling(10).mean().values
        ma20 = pd.Series(closes).rolling(20).mean().values
        ma60 = pd.Series(closes).rolling(60).mean().values

        last = len(closes) - 1
        if last < 60:
            return {"score": 50.0, "signals": ["数据不足，趋势评分中性"]}

        # MA 排列
        if ma5[last] > ma10[last] > ma20[last] > ma60[last]:
            score += 30
            sigs.append("MA多头排列")
        elif ma5[last] < ma10[last] < ma20[last] < ma60[last]:
            score -= 30
            sigs.append("MA空头排列")
        elif ma5[last] > ma10[last] > ma20[last]:
            score += 15
            sigs.append("短期均线多头")
        elif ma5[last] < ma10[last] < ma20[last]:
            score -= 15
            sigs.append("短期均线空头")

        # MACD
        ema12 = pd.Series(closes).ewm(span=12).mean().values
        ema26 = pd.Series(closes).ewm(span=26).mean().values
        dif = ema12 - ema26
        dea = pd.Series(dif).ewm(span=9).mean().values
        macd_bar = 2 * (dif - dea)

        if dif[last] > dea[last] and dif[last] > 0:
            score += 15
            sigs.append("MACD零轴上金叉")
        elif dif[last] < dea[last] and dif[last] < 0:
            score -= 15
            sigs.append("MACD零轴下死叉")
        elif dif[last] > dea[last]:
            score += 5
        elif dif[last] < dea[last]:
            score -= 5

        # MACD 拐点
        if last >= 2 and dif[last] > dif[last - 1] and dif[last - 1] <= dif[last - 2]:
            score += 5
            sigs.append("DIF拐头向上")
        if last >= 2 and macd_bar[last] > macd_bar[last - 1] and macd_bar[last - 1] <= macd_bar[last - 2]:
            score += 3

        # 价格 vs MA20
        if ma20[last] > 0:
            dev = (closes[last] - ma20[last]) / ma20[last] * 100
            if dev > 5:
                score -= 10
                sigs.append(f"偏离MA20 {dev:.1f}%（偏高）")
            elif dev < -5:
                score += 10
                sigs.append(f"偏离MA20 {dev:.1f}%（超跌）")
            elif -2 < dev < 2:
                score += 5
                sigs.append("价格靠近MA20")

        return {"score": max(0, min(100, score)), "signals": sigs}

    def _score_momentum(self, closes, highs, lows) -> dict:
        score = 50.0
        sigs = []
        last = len(closes) - 1

        # RSI (14)
        delta = np.diff(closes)
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_gain = pd.Series(gain).ewm(alpha=1/14).mean().values
        avg_loss = pd.Series(loss).ewm(alpha=1/14).mean().values
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - 100 / (1 + rs)
        rsi_val = rsi[-1]  # diff reduces length by 1

        if rsi_val > 80:
            score -= 20
            sigs.append(f"RSI={rsi_val:.0f}（超买）")
        elif rsi_val > 65:
            score += 8
            sigs.append(f"RSI={rsi_val:.0f}（偏强）")
        elif rsi_val < 20:
            score += 20
            sigs.append(f"RSI={rsi_val:.0f}（超卖）")
        elif rsi_val < 35:
            score -= 8
            sigs.append(f"RSI={rsi_val:.0f}（偏弱）")
        else:
            score += 5
            sigs.append(f"RSI={rsi_val:.0f}（中性）")

        # KDJ
        low_n = pd.Series(lows).rolling(9).min().values
        high_n = pd.Series(highs).rolling(9).max().values
        rsv = (closes - low_n) / (high_n - low_n + 1e-10) * 100
        k = pd.Series(rsv).ewm(com=2).mean().values
        d = pd.Series(k).ewm(com=2).mean().values
        j = 3 * k - 2 * d

        if j[last] > 100:
            score -= 10
            sigs.append("KDJ超买")
        elif j[last] < 0:
            score += 10
            sigs.append("KDJ超卖")
        if last >= 1 and k[last] > d[last] and k[last-1] <= d[last-1]:
            score += 10
            sigs.append("KDJ金叉")
        elif last >= 1 and k[last] < d[last] and k[last-1] >= d[last-1]:
            score -= 10
            sigs.append("KDJ死叉")

        return {"score": max(0, min(100, score)), "signals": sigs}

    def _score_volatility(self, closes, highs, lows) -> dict:
        score = 50.0
        sigs = []
        last = len(closes) - 1

        # BOLL
        ma20 = pd.Series(closes).rolling(20).mean().values
        std20 = pd.Series(closes).rolling(20).std().values
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20

        if std20[last] > 0 and ma20[last] > 0:
            width = (upper[last] - lower[last]) / ma20[last]
            pos = (closes[last] - lower[last]) / (upper[last] - lower[last] + 1e-10)

            if pos > 0.95:
                score += 8
                sigs.append("触及BOLL上轨（强势）")
            elif pos < 0.05:
                score += 15
                sigs.append("触及BOLL下轨（超跌反弹预期）")
            elif 0.4 < pos < 0.6:
                score += 5
                sigs.append("BOLL中轨附近（稳健）")

            if width < 0.1:
                score += 5
                sigs.append("BOLL收窄（变盘信号）")

        # ATR 波动率
        tr = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(
                np.abs(highs[1:] - closes[:-1]),
                np.abs(lows[1:] - closes[:-1])
            )
        )
        atr = pd.Series(tr).rolling(14).mean().values
        if len(atr) > 14 and closes[last] > 0:
            atr_pct = atr[-1] / closes[last] * 100
            if atr_pct > 5:
                score -= 5
                sigs.append(f"高波动 ATR%={atr_pct:.1f}%")

        return {"score": max(0, min(100, score)), "signals": sigs}

    # ============ 资金面（25%） ============

    def score_fund_flow(self, flow: pd.DataFrame) -> dict:
        score = 50.0
        sigs = []

        if flow.empty or "main_net" not in flow.columns:
            return {"score": 50.0, "sub_scores": {}, "signals": ["资金流向数据不足"]}

        main = flow["main_net"].values
        # 净流入动量
        recent_5 = main[-5:] if len(main) >= 5 else main
        avg_5 = np.mean(recent_5)
        std_all = np.std(main) if len(main) > 1 else 1.0
        momentum_z = avg_5 / (std_all + 1e-10)

        if momentum_z > 1.0:
            score += 25
            sigs.append("主力资金显著净流入")
        elif momentum_z > 0.3:
            score += 12
            sigs.append("主力资金小幅净流入")
        elif momentum_z < -1.0:
            score -= 25
            sigs.append("主力资金显著净流出")
        elif momentum_z < -0.3:
            score -= 12
            sigs.append("主力资金小幅净流出")

        # 大单主导度
        if "super_large_net" in flow.columns and "large_net" in flow.columns:
            big = flow["super_large_net"].values + flow["large_net"].values
            total = (
                big
                + flow["mid_net"].values
                + flow["small_net"].values
                + 1e-10
            )
            big_ratio = np.mean(big[-5:]) / (np.mean(np.abs(total[-5:])) + 1e-10)
            if big_ratio > 0.5:
                score += 15
                sigs.append("大单资金主导")
            elif big_ratio < -0.5:
                score -= 15
                sigs.append("大单资金流出主导")

        # 持续性
        if len(main) >= 5:
            streak = 0
            for v in main[::-1]:
                if v > 0:
                    streak += 1
                else:
                    break
            if streak >= 5:
                score += 10
                sigs.append(f"连续{streak}日净流入")
            elif streak == 0:
                neg_streak = 0
                for v in main[::-1]:
                    if v < 0:
                        neg_streak += 1
                    else:
                        break
                if neg_streak >= 3:
                    score -= 10
                    sigs.append(f"连续{neg_streak}日净流出")

        return {"score": max(0, min(100, score)), "signals": sigs}

    # ============ 情绪面（25%） ============

    def score_sentiment(
        self,
        realtime: Optional[dict],
        hist: Optional[pd.DataFrame],
        market: dict,
    ) -> dict:
        score = 50.0
        sigs = []

        # 市场广度
        up = market.get("up_count", 0)
        down = market.get("down_count", 0)
        total = up + down
        if total > 0:
            breadth = up / total * 100
            if breadth > 70:
                score += 15
                sigs.append(f"市场普涨（{breadth:.0f}%上涨）")
            elif breadth > 55:
                score += 7
                sigs.append(f"市场偏强（{breadth:.0f}%上涨）")
            elif breadth < 30:
                score -= 15
                sigs.append(f"市场普跌（{100-breadth:.0f}%下跌）")
            elif breadth < 45:
                score -= 7
                sigs.append(f"市场偏弱（{100-breadth:.0f}%下跌）")

        # 涨跌停比
        limit_up = market.get("limit_up", 0)
        limit_down = market.get("limit_down", 0)
        if limit_up + limit_down > 0:
            ratio = limit_up / (limit_up + limit_down) * 100
            if ratio > 80:
                score += 10
                sigs.append("涨停家数远多于跌停")
            elif ratio < 20:
                score -= 10
                sigs.append("跌停家数远多于涨停")

        # 量比
        if realtime:
            vol_ratio = realtime.get("volume_ratio", 1.0)
            if vol_ratio > 2.0:
                score += 10
                sigs.append(f"量比 {vol_ratio:.1f}（显著放量）")
            elif vol_ratio > 1.2:
                score += 5
                sigs.append(f"量比 {vol_ratio:.1f}（温和放量）")
            elif vol_ratio < 0.5:
                score -= 5
                sigs.append(f"量比 {vol_ratio:.1f}（缩量）")

        # 换手率
        if realtime:
            turnover = realtime.get("turnover_rate", 0)
            if 2 < turnover < 8:
                score += 10
                sigs.append(f"换手率 {turnover:.1f}%（活跃）")
            elif turnover >= 10:
                score -= 5
                sigs.append(f"换手率 {turnover:.1f}%（过热）")
            elif turnover < 0.5:
                score -= 8
                sigs.append(f"换手率 {turnover:.1f}%（冷清）")

        return {"score": max(0, min(100, score)), "signals": sigs}

    # ============ 基本面（25%） ============

    def score_fundamental(self, financials: Optional[dict], realtime: Optional[dict]) -> dict:
        score = 50.0
        sigs = []

        # 估值（PE / PB）
        if realtime:
            pe = realtime.get("pe_dynamic", 0)
            pb = realtime.get("pb", 0)
            if 0 < pe < 15:
                score += 12
                sigs.append(f"PE={pe:.1f}（低估）")
            elif 15 <= pe < 30:
                score += 5
                sigs.append(f"PE={pe:.1f}（合理）")
            elif 60 <= pe < 200:
                score -= 8
                sigs.append(f"PE={pe:.1f}（偏高）")
            elif pe >= 200 or pe <= 0:
                score -= 15
                sigs.append(f"PE={pe:.1f}（异常）")

            if 0 < pb < 1.5:
                score += 8
                sigs.append(f"PB={pb:.2f}（低市净率）")
            elif pb > 10:
                score -= 5
                sigs.append(f"PB={pb:.2f}（偏高）")

        if financials is None:
            if realtime is None:
                return {"score": None, "signals": ["无基本面数据"]}
            sigs.insert(0, "仅含估值数据，无详细财报")
            return {"score": max(0, min(100, score)), "signals": sigs}

        # 盈利
        roe = financials.get("roe", 0)
        gross_margin = financials.get("gross_margin", 0)
        if roe > 20:
            score += 10
            sigs.append(f"ROE={roe:.1f}%（优秀）")
        elif roe > 10:
            score += 5
            sigs.append(f"ROE={roe:.1f}%（良好）")
        elif roe > 0:
            score -= 5
            sigs.append(f"ROE={roe:.1f}%（一般）")
        elif roe < 0:
            score -= 12
            sigs.append(f"ROE={roe:.1f}%（亏损）")

        if gross_margin > 60:
            score += 8
            sigs.append(f"毛利率={gross_margin:.1f}%（高）")
        elif gross_margin < 15:
            score -= 5
            sigs.append(f"毛利率={gross_margin:.1f}%（低）")

        # 增长
        rev_g = financials.get("revenue_growth", 0)
        profit_g = financials.get("profit_growth", 0)
        if profit_g > 30:
            score += 12
            sigs.append(f"净利润增长{profit_g:.1f}%")
        elif profit_g > 10:
            score += 6
            sigs.append(f"净利润增长{profit_g:.1f}%")
        elif profit_g < -20:
            score -= 12
            sigs.append(f"净利润下滑{profit_g:.1f}%")
        elif profit_g < 0:
            score -= 6
            sigs.append(f"净利润下降{profit_g:.1f}%")

        if rev_g > 20:
            score += 5
            sigs.append(f"营收增长{rev_g:.1f}%")
        elif rev_g < -10:
            score -= 5
            sigs.append(f"营收下降{rev_g:.1f}%")

        # 安全
        debt = financials.get("debt_ratio", 0)
        if debt > 80:
            score -= 10
            sigs.append(f"资产负债率{debt:.1f}%（高杠杆）")
        elif debt < 40:
            score += 5
            sigs.append(f"资产负债率{debt:.1f}%（稳健）")

        return {"score": max(0, min(100, score)), "signals": sigs}
