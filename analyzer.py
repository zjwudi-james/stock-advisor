"""评分引擎 —— 技术面、资金面、情绪面、基本面四维度综合评分。"""

import pandas as pd
import numpy as np
from typing import Optional


class StockAnalyzer:
    """A 股多因子评分器。每个维度 0-100 分，根据市场环境动态调整权重。"""

    # 默认权重（中性市场）
    BASE_WEIGHTS = {
        "technical": 0.25,
        "fund_flow": 0.25,
        "sentiment": 0.25,
        "fundamental": 0.25,
    }

    # 不同市场环境的权重调整
    REGIME_WEIGHTS = {
        "bull": {"technical": 0.25, "fund_flow": 0.20, "sentiment": 0.20, "fundamental": 0.35},
        "bear": {"technical": 0.25, "fund_flow": 0.15, "sentiment": 0.15, "fundamental": 0.45},
        "range": {"technical": 0.35, "fund_flow": 0.30, "sentiment": 0.15, "fundamental": 0.20},
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
        index_data: Optional[dict] = None,
        sector_info: Optional[dict] = None,
    ) -> dict:
        dims = {}
        if hist is not None and not hist.empty:
            dims["technical"] = self.score_technical(realtime, hist)
        if flow is not None and not flow.empty:
            dims["fund_flow"] = self.score_fund_flow(flow)
        if market is not None:
            dims["sentiment"] = self.score_sentiment(realtime, hist, market, index_data)
        if financials is not None or (realtime and realtime.get("pe_dynamic")):
            dims["fundamental"] = self.score_fundamental(financials, realtime)

        # 市场环境评估 + 动态权重
        env = self._assess_market_environment(index_data, market)
        DIMENSION_WEIGHTS = self.REGIME_WEIGHTS.get(env["regime"], self.BASE_WEIGHTS)

        total, recommendation, weight_info = self._aggregate(dims, DIMENSION_WEIGHTS)
        data_quality = self._data_quality_note(dims)

        # 风控：止损位、目标位、仓位建议
        risk = self._calculate_risk_params(realtime, hist, total, env["regime"])

        return {
            "code": code,
            "name": name,
            "total_score": total,
            "recommendation": recommendation,
            "dimensions": dims,
            "weight_info": weight_info,
            "data_quality_note": data_quality,
            "market_env": env,
            "risk": risk,
            "dynamic_weights": DIMENSION_WEIGHTS,
            "sector_info": sector_info,
        }

    # ---- 聚合与建议 ----

    def _aggregate(self, dims: dict, weights: dict) -> tuple:
        active = {k: v for k, v in dims.items() if v.get("score") is not None}
        if not active:
            return 50, "数据不足", "全部数据获取失败，默认中性50分"

        total_weight = sum(weights.get(k, 0) for k in active)
        scale = 1.0 / total_weight if total_weight > 0 else 1.0
        total = 0.0
        for k, v in active.items():
            total += v["score"] * weights.get(k, 0) * scale

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

        missing = [k for k in weights if k not in dims]
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
        volatility = self._score_volatility(closes, highs, lows, hist)

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

    def _score_volatility(self, closes, highs, lows, hist: pd.DataFrame = None) -> dict:
        score = 50.0
        sigs = []
        last = len(closes) - 1

        # ---- BOLL ----
        ma20 = pd.Series(closes).rolling(20).mean().values
        std20 = pd.Series(closes).rolling(20).std().values
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20

        if std20[last] > 0 and ma20[last] > 0:
            width = (upper[last] - lower[last]) / ma20[last]
            pos = (closes[last] - lower[last]) / (upper[last] - lower[last] + 1e-10)

            if pos > 0.90:
                score += 8
                sigs.append("触及BOLL上轨（强势）")
            elif pos < 0.12:
                score += 15
                sigs.append("触及BOLL下轨（超跌反弹预期）")
            elif 0.35 < pos < 0.65:
                score += 5
                sigs.append("BOLL中轨附近（稳健）")

            if width < 0.1:
                score += 5
                sigs.append("BOLL收窄（变盘信号）")

        # ---- ATR 波动率 ----
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

        # ---- 量价分析 ----
        if hist is not None and "volume" in hist.columns:
            volumes = hist["volume"].values
            score += self._score_volume_price(closes, volumes, sigs, last)

        return {"score": max(0, min(100, score)), "signals": sigs}

    def _score_volume_price(self, closes: np.ndarray, volumes: np.ndarray, sigs: list, last: int) -> float:
        """量价关系评分，返回加分增量。"""
        delta = 0.0
        if last < 25:
            return delta

        # 近5日 vs 前15日均量
        vol_recent = np.mean(volumes[-5:])
        vol_base = np.mean(volumes[-20:-5]) if len(volumes) >= 20 else np.mean(volumes[:-5])
        vol_ratio = vol_recent / (vol_base + 1)

        # 近5日和近20日价格变化
        price_5d = (closes[last] - closes[last - 5]) / (closes[last - 5] + 1e-10)
        price_20d = (closes[last] - closes[max(0, last - 20)]) / (closes[max(0, last - 20)] + 1e-10)

        # --- 量价方向组合 ---
        if price_5d > 0.02 and vol_ratio > 1.2:
            delta += 10
            sigs.append("价涨量增（健康上涨）")
        elif price_5d > 0.02 and vol_ratio < 0.7:
            delta -= 10
            sigs.append("价涨量缩（上涨乏力）")
        elif price_5d < -0.02 and vol_ratio > 1.5:
            if price_20d < -0.15:
                delta += 8
                sigs.append("低位放量下跌（疑似主力吸筹）")
            else:
                delta -= 8
                sigs.append("放量下跌（抛压较重）")
        elif price_5d < -0.02 and vol_ratio < 0.7:
            delta += 12
            sigs.append("价跌量缩（抛压减轻，可能见底）")
        elif price_5d < -0.02 and vol_ratio < 1.05:
            # 阴跌但不放量 → 非恐慌性下跌
            delta += 6
            sigs.append("缩量阴跌（非恐慌抛售，关注企稳）")

        # --- 放量突破 ---
        if last >= 20:
            vol_today = volumes[last]
            ma20_vol = np.mean(volumes[-21:-1]) if len(volumes) >= 21 else np.mean(volumes[:-1])
            price_change_today = (closes[last] - closes[last - 1]) / (closes[last - 1] + 1e-10)
            if vol_today > ma20_vol * 1.5 and price_change_today > 0.015:
                delta += 12
                sigs.append("放量突破（量价齐升）")
            elif vol_today > ma20_vol * 1.5 and price_change_today < -0.015:
                delta -= 8
                sigs.append("放量下挫（主力出货迹象）")

        # --- 缩量回踩后企稳 ---
        if last >= 5:
            prev_3_vol = np.mean(volumes[-4:-1])
            prev_10_vol = np.mean(volumes[-11:-1]) if len(volumes) >= 11 else prev_3_vol
            prev_3_ret = (closes[last - 1] - closes[last - 4]) / (closes[last - 4] + 1e-10)
            if prev_3_ret < -0.01 and prev_3_vol < prev_10_vol * 0.85 and volumes[last] > prev_3_vol * 1.2:
                delta += 8
                sigs.append("缩量回踩后放量企稳")

        # --- 量价背离 ---
        if last >= 60:
            # 顶背离：价格新高，量能递减
            recent_peak = np.max(closes[-15:])
            prev_peak = np.max(closes[-45:-15])
            if prev_peak > 0 and recent_peak > prev_peak:
                recent_peak_idx = last - 15 + np.argmax(closes[-15:])
                prev_peak_idx = last - 45 + np.argmax(closes[-45:-15])
                vol_at_recent = np.mean(volumes[max(0, recent_peak_idx - 2):min(len(volumes), recent_peak_idx + 3)])
                vol_at_prev = np.mean(volumes[max(0, prev_peak_idx - 2):min(len(volumes), prev_peak_idx + 3)])
                if vol_at_recent < vol_at_prev * 0.7:
                    delta -= 10
                    sigs.append("量价顶背离（新高无量）")

            # 底背离：价格新低，量能萎缩
            recent_trough = np.min(closes[-15:])
            prev_trough = np.min(closes[-45:-15])
            if prev_trough > 0 and recent_trough < prev_trough:
                recent_tr_idx = last - 15 + np.argmin(closes[-15:])
                prev_tr_idx = last - 45 + np.argmin(closes[-45:-15])
                vol_at_recent = np.mean(volumes[max(0, recent_tr_idx - 2):min(len(volumes), recent_tr_idx + 3)])
                vol_at_prev = np.mean(volumes[max(0, prev_tr_idx - 2):min(len(volumes), prev_tr_idx + 3)])
                if vol_at_recent < vol_at_prev * 0.8:
                    delta += 10
                    sigs.append("量价底背离（新低缩量）")

        # --- 均量线趋势 ---
        if len(volumes) >= 20:
            vol_ma5 = pd.Series(volumes).rolling(5).mean().values
            vol_ma20_arr = pd.Series(volumes).rolling(20).mean().values
            if vol_ma5[last] > vol_ma20_arr[last] * 1.3:
                delta += 5
                sigs.append("成交量放大（市场关注度提升）")
            elif vol_ma5[last] < vol_ma20_arr[last] * 0.5:
                delta -= 3
                sigs.append("成交量萎缩（交投清淡）")

        return delta

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
        index_data: Optional[dict] = None,
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

        # 指数趋势影响
        if index_data:
            up_count = 0
            for code, info in index_data.items():
                if info.get("trend") == "上涨":
                    up_count += 1
                elif info.get("trend") == "下跌":
                    up_count -= 1
            if up_count >= 2:
                score += 8
                sigs.append("三大指数偏强")
            elif up_count <= -2:
                score -= 10
                sigs.append("三大指数偏弱")

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

    # ============ 市场环境评估 ============

    def _assess_market_environment(self, index_data: Optional[dict], market: Optional[dict]) -> dict:
        """评估当前市场环境：牛市/熊市/震荡。"""
        if index_data is None:
            return {
                "regime": "range",
                "confidence": 0.0,
                "signal": "无指数数据，默认震荡市",
            }
        trends = []
        total_change = 0.0
        count = 0
        details = {}
        for code, info in index_data.items():
            trend = info.get("trend", "震荡")
            details[info.get("name", code)] = {
                "price": info["price"],
                "change_pct": info["change_pct"],
                "trend": trend,
            }
            if trend == "上涨":
                trends.append(1)
            elif trend == "下跌":
                trends.append(-1)
            else:
                trends.append(0)
            total_change += info.get("change_pct", 0)
            count += 1
        if count == 0:
            return {"regime": "range", "confidence": 0.0, "signal": "指数数据异常"}

        avg_trend = sum(trends) / len(trends)
        avg_change = total_change / count

        # 综合判定
        if avg_trend >= 0.6 and avg_change > 0:
            regime = "bull"
            signal = "三大指数多头排列，市场处于上升趋势，可积极操作"
            confidence = min(0.9, 0.5 + avg_trend * 0.3 + abs(avg_change) * 0.02)
        elif avg_trend <= -0.6 and avg_change < 0:
            regime = "bear"
            signal = "三大指数空头排列，市场处于下降趋势，建议减仓或空仓观望"
            confidence = min(0.9, 0.5 + abs(avg_trend) * 0.3 + abs(avg_change) * 0.02)
        else:
            regime = "range"
            signal = "指数走势分化或震荡，市场方向不明，可高抛低吸但控制仓位"
            confidence = 0.4 + abs(avg_trend) * 0.2

        return {
            "regime": regime,
            "confidence": round(confidence, 2),
            "signal": signal,
            "indices": details,
        }

    # ============ 风控参数计算 ============

    def _calculate_risk_params(
        self,
        realtime: Optional[dict],
        hist: Optional[pd.DataFrame],
        score: int,
        regime: str,
    ) -> dict:
        """综合计算止损、止盈、仓位建议。"""
        cur_price = realtime["price"] if realtime else 0
        if hist is None or hist.empty or cur_price <= 0:
            return {
                "stop_loss": None,
                "take_profit_1": None,
                "take_profit_2": None,
                "risk_reward": None,
                "position_pct": None,
                "position_note": "数据不足，无法计算风控参数",
            }
        closes = hist["close"].values
        highs = hist["high"].values
        lows = hist["low"].values
        atr = self._calc_atr(closes, highs, lows)
        atr_pct = atr / cur_price

        # 止损位（基于 ATR + 关键支撑）
        support = self._find_nearest_support(closes, lows, cur_price)
        atr_stop = cur_price - 2.5 * atr
        stop_loss = max(support, atr_stop) if support > 0 else atr_stop
        stop_loss_pct = (cur_price - stop_loss) / cur_price * 100

        # 止盈目标
        resistance = self._find_nearest_resistance(closes, highs, cur_price)
        tp1 = cur_price + (cur_price - stop_loss) * 2.0
        tp2 = cur_price + (cur_price - stop_loss) * 3.5
        if resistance > 0 and resistance < tp1:
            tp1 = resistance
            tp2 = resistance * 1.05
        tp1_pct = (tp1 - cur_price) / cur_price * 100
        tp2_pct = (tp2 - cur_price) / cur_price * 100

        # 盈亏比
        risk = cur_price - stop_loss
        reward = tp1 - cur_price
        rr = reward / risk if risk > 0 else 0

        # 仓位建议
        position = self._position_advice(score, regime, atr_pct, rr)
        return {
            "stop_loss": round(stop_loss, 2),
            "stop_loss_pct": round(stop_loss_pct, 1),
            "take_profit_1": round(tp1, 2),
            "take_profit_1_pct": round(tp1_pct, 1),
            "take_profit_2": round(tp2, 2),
            "take_profit_2_pct": round(tp2_pct, 1),
            "risk_reward": round(rr, 1),
            "atr": round(atr, 2),
            "atr_pct": round(atr_pct * 100, 2),
            "key_support": round(support, 2) if support > 0 else None,
            "key_resistance": round(resistance, 2) if resistance > 0 else None,
            "position_pct": position["pct"],
            "position_level": position["level"],
            "position_note": position["note"],
        }

    def _calc_atr(self, closes: np.ndarray, highs: np.ndarray, lows: np.ndarray, period: int = 14) -> float:
        """计算 ATR。"""
        if len(closes) < period + 1:
            return float(np.std(closes[-min(20, len(closes)):]) * 0.5)
        prev_close = closes[-period-1:-1]
        tr = np.maximum(
            highs[-period:] - lows[-period:],
            np.maximum(
                np.abs(highs[-period:] - prev_close),
                np.abs(lows[-period:] - prev_close),
            ),
        )
        return float(np.mean(tr))

    def _find_nearest_support(self, closes: np.ndarray, lows: np.ndarray, cur: float) -> float:
        """找最近的支撑位。"""
        if len(lows) < 20:
            return 0.0
        recent_lows = lows[-60:] if len(lows) >= 60 else lows
        min_val = np.min(recent_lows[recent_lows < cur * 0.98]) if np.any(recent_lows < cur * 0.98) else 0
        if min_val <= 0:
            ma20 = float(np.mean(closes[-20:]))
            min_val = ma20 * 0.95 if ma20 < cur else cur * 0.92
        # 聚类找局部低点
        candidates = sorted(recent_lows[(recent_lows > cur * 0.75) & (recent_lows < cur * 0.98)], reverse=True)
        if len(candidates) > 0:
            clusters = []
            cluster = [candidates[0]]
            for c in candidates[1:]:
                if abs(c - cluster[-1]) / cur < 0.02:
                    cluster.append(c)
                else:
                    clusters.append(float(np.mean(cluster)))
                    cluster = [c]
            clusters.append(float(np.mean(cluster)))
            return clusters[0] if clusters else float(min_val)
        return float(min_val)

    def _find_nearest_resistance(self, closes: np.ndarray, highs: np.ndarray, cur: float) -> float:
        """找最近的压力位。"""
        if len(highs) < 20:
            return 0.0
        recent_highs = highs[-60:] if len(highs) >= 60 else highs
        candidates = sorted(recent_highs[(recent_highs > cur * 1.02) & (recent_highs < cur * 1.25)])
        if len(candidates) > 0:
            clusters = []
            cluster = [candidates[0]]
            for c in candidates[1:]:
                if abs(c - cluster[-1]) / cur < 0.02:
                    cluster.append(c)
                else:
                    clusters.append(float(np.mean(cluster)))
                    cluster = [c]
            clusters.append(float(np.mean(cluster)))
            return clusters[0] if clusters else 0.0
        return float(np.max(recent_highs)) * 0.95 if np.max(recent_highs) > cur * 1.05 else cur * 1.1

    def _position_advice(self, score: int, regime: str, atr_pct: float, rr: float) -> dict:
        """根据评分、市场环境、波动率、盈亏比给出仓位建议。"""
        # 基础仓位由评分决定
        if score >= 80:
            base_pct = 25
        elif score >= 70:
            base_pct = 18
        elif score >= 60:
            base_pct = 12
        elif score >= 50:
            base_pct = 8
        elif score >= 40:
            base_pct = 5
        else:
            base_pct = 0

        # 市场环境调整
        regime_mult = {"bull": 1.2, "range": 0.85, "bear": 0.4}
        base_pct *= regime_mult.get(regime, 0.85)

        # 波动率惩罚（高波动降低仓位）
        if atr_pct > 0.05:
            base_pct *= 0.6
        elif atr_pct > 0.035:
            base_pct *= 0.8

        # 盈亏比惩罚
        if rr < 1.5:
            base_pct *= 0.5
        elif rr > 3.0:
            base_pct *= 1.1

        pct = max(0, min(30, round(base_pct)))

        if pct >= 20:
            level = "重仓"
        elif pct >= 12:
            level = "中等仓位"
        elif pct >= 5:
            level = "轻仓试探"
        else:
            level = "不建议参与"

        notes = {
            "bull": "牛市环境下可适当积极",
            "bear": "熊市环境，严格控制仓位",
            "range": "震荡市，灵活控制仓位",
        }
        note = notes.get(regime, "")
        if atr_pct > 0.05:
            note += "；高波动标的，注意控制风险"
        if rr < 1.5:
            note += "；盈亏比偏低"

        return {"pct": pct, "level": level, "note": note}

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
