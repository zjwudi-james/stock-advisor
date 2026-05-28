"""日跟踪脚本：加载当月 watchlist，检查 top 20 股票当日表现并更新跟踪报告。

用法：python daily_track.py [--year-month 2026-06]

功能：
  1. 加载 watchlist/YYYY-MM.json 中的 top 20
  2. 获取每只股票实时行情（~30s）
  3. 计算每日综合评分（基于实时数据 + 60日K线）
  4. 检查预警：止损/止盈/异动/评分大幅变化
  5. 保存当日快照 JSON
  6. 更新累计跟踪 summary.md
"""

import sys
import io
import os
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from data_fetcher import get_realtime_today, get_sina_spot_dict, get_index_data, get_history

BASE_DIR = Path(r"C:\Users\zhangjie\Claude\投资分析\实战演练")
WATCHLIST_DIR = BASE_DIR / "watchlist"


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def load_watchlist(ym_str):
    """加载当月 watchlist JSON。"""
    path = WATCHLIST_DIR / f"{ym_str}.json"
    if not path.exists():
        log(f"ERROR: watchlist 不存在: {path}")
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_tracking_dir(ym_str):
    """获取当月跟踪目录，不存在则创建。"""
    d = BASE_DIR / "tracking" / ym_str
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_summary_path(ym_str):
    return get_tracking_dir(ym_str) / "summary.md"


def check_alerts(entry, realtime, risk):
    """检查预警条件，返回告警列表。"""
    alerts = []
    cur = realtime["price"]
    entry_price = risk.get("entry_price", cur)
    stop_loss = risk.get("stop_loss", 0)
    tp1 = risk.get("take_profit_1", 0)
    chg = realtime.get("change_pct", 0)
    turnover = realtime.get("turnover_rate", 0)

    # 止损检查
    if stop_loss > 0 and cur <= stop_loss:
        alerts.append(f"🚨 跌破止损位 {stop_loss:.2f}（当前 {cur:.2f}）")
    elif stop_loss > 0:
        dist_to_stop = (cur - stop_loss) / stop_loss * 100
        if dist_to_stop < 2:
            alerts.append(f"⚠️ 接近止损位 {stop_loss:.2f}（距{dist_to_stop:.1f}%）")

    # 止盈检查
    if tp1 > 0 and cur >= tp1:
        alerts.append(f"🎯 触及第一目标 {tp1:.2f}（当前 {cur:.2f}），建议减仓 1/2")
    elif tp1 > 0:
        dist_to_tp = (tp1 - cur) / cur * 100
        if dist_to_tp < 5:
            alerts.append(f"🎯 接近第一目标 {tp1:.2f}（距{dist_to_tp:.1f}%）")

    # 日内异动
    if abs(chg) > 5:
        alerts.append(f"📊 日涨跌幅异常 {chg:+.1f}%")
    if turnover > 15:
        alerts.append(f"📊 换手率异常 {turnover:.1f}%（过热）")
    elif turnover < 0.3 and turnover > 0:
        alerts.append(f"📊 换手率极低 {turnover:.2f}%（冷清）")

    # 距入场价变化
    if entry_price > 0:
        total_chg = (cur - entry_price) / entry_price * 100
        if total_chg < -10:
            alerts.append(f"📉 累计跌幅 {total_chg:.1f}%（从入场 {entry_price:.2f}）")
        elif total_chg > 15:
            alerts.append(f"📈 累计涨幅 {total_chg:.1f}%（从入场 {entry_price:.2f}）")

    return alerts


def daily_score(realtime, hist, baseline_score):
    """计算每日综合评分（0-100）。

    基于实时数据 + 60日K线快速计算，四个维度：
    - 估值(25分): PE/PB
    - 动量(25分): RSI/KDJ/价格位置
    - 情绪(25分): 换手率/量比/涨跌幅
    - 趋势(25分): MA排列/MACD
    """
    score = 50.0
    signals = []

    # ---- 估值（25分） ----
    pe = realtime.get("pe_dynamic", 0)
    pb = realtime.get("pb", 0)
    if 0 < pe < 15:
        score += 15
        signals.append(f"PE={pe:.1f}(低估)")
    elif 15 <= pe < 30:
        score += 8
        signals.append(f"PE={pe:.1f}(合理)")
    elif 60 <= pe < 200:
        score -= 8
        signals.append(f"PE={pe:.1f}(偏高)")
    elif pe >= 200 or pe <= 0:
        score -= 12
        signals.append(f"PE={pe:.1f}(异常)")
    if 0 < pb < 1.5:
        score += 10
        signals.append(f"PB={pb:.2f}(低)")
    elif pb > 10:
        score -= 5
        signals.append(f"PB={pb:.2f}(高)")

    # ---- 情绪（25分） ----
    chg = realtime.get("change_pct", 0)
    turnover = realtime.get("turnover_rate", 0)
    vr = realtime.get("volume_ratio", 1.0)

    if -2 < chg < 0:
        score += 5
        signals.append("微跌回调")
    elif 0 <= chg < 2:
        score += 3
    elif chg > 4:
        score -= 3
        signals.append(f"急涨{chg:+.1f}%")
    elif chg < -4:
        score += 3
        signals.append(f"急跌{chg:+.1f}%")

    if 2 < turnover < 8:
        score += 10
        signals.append(f"换手{turnover:.1f}%(活跃)")
    elif turnover >= 10:
        score -= 3
        signals.append(f"换手{turnover:.1f}%(过热)")
    elif turnover < 0.5:
        score -= 5
        signals.append("换手极低(冷清)")

    if vr > 1.5:
        score += 5
        signals.append("放量")
    elif vr < 0.5:
        score -= 3
        signals.append("缩量")

    # ---- 趋势 + 动量（25分，来自60日K线） ----
    if hist is not None and not hist.empty and len(hist) >= 30:
        closes = hist["close"].values
        last = len(closes) - 1

        # MA 排列
        ma5 = pd.Series(closes).rolling(5).mean().values
        ma10 = pd.Series(closes).rolling(10).mean().values
        ma20 = pd.Series(closes).rolling(20).mean().values
        if ma5[last] > ma10[last] > ma20[last]:
            score += 12
            signals.append("短均多头")
        elif ma5[last] < ma10[last] < ma20[last]:
            score -= 8
            signals.append("短均空头")
        elif ma5[last] > ma10[last]:
            score += 5

        # MACD
        ema12 = pd.Series(closes).ewm(span=12).mean().values
        ema26 = pd.Series(closes).ewm(span=26).mean().values
        dif = ema12 - ema26
        dea = pd.Series(dif).ewm(span=9).mean().values
        if dif[last] > dea[last] and dif[last] > 0:
            score += 8
            signals.append("MACD多头")
        elif dif[last] < dea[last] and dif[last] < 0:
            score -= 6
            signals.append("MACD空头")

        # RSI
        delta = np.diff(closes)
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_gain = pd.Series(gain).ewm(alpha=1/14).mean().values
        avg_loss = pd.Series(loss).ewm(alpha=1/14).mean().values
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - 100 / (1 + rs)
        rsi_val = rsi[-1]
        if rsi_val > 75:
            score -= 5
            signals.append(f"RSI={rsi_val:.0f}(超买)")
        elif rsi_val < 25:
            score += 5
            signals.append(f"RSI={rsi_val:.0f}(超卖)")
        elif 40 < rsi_val < 60:
            score += 3
    else:
        # 无历史数据，基于价格变化估计
        signals.append("无K线数据")

    final_score = max(0, min(100, round(score)))
    return {"score": final_score, "signals": signals[:6]}


def run_track(ym_str):
    """执行日跟踪。"""
    today = datetime.now().strftime("%Y-%m-%d")
    log("=" * 50)
    log(f"日跟踪: {today} — {ym_str}")
    log("=" * 50)

    # 加载 watchlist
    wl = load_watchlist(ym_str)
    if wl is None:
        return
    top20 = wl.get("top20", [])
    if not top20:
        log("ERROR: watchlist 中无数据")
        return
    log(f"跟踪 {len(top20)} 只股票")

    # 获取大盘 + Sina 全市场缓存（一次调用供所有股票复用）
    log("获取大盘和Sina全市场数据...")
    index_data = get_index_data()
    sina_dict = get_sina_spot_dict()
    log(f"  Sina缓存: {len(sina_dict) if sina_dict else 0} 只")

    # 逐只检查
    entries = []
    alert_summary = []
    for item in top20:
        code = item["code"]
        name = item["name"]
        baseline_score = item.get("total_score", 0)
        try:
            rt = get_realtime_today(code, sina_dict)
            if rt is None or rt["price"] <= 0:
                log(f"  {code} {name}: 数据获取失败")
                entries.append({
                    "code": code, "name": name,
                    "entry_price": item["risk"].get("entry_price", 0),
                    "current_price": None,
                    "change_from_entry_pct": None,
                    "today_change_pct": None,
                    "baseline_score": baseline_score,
                    "daily_score": None,
                    "score_change": None,
                    "alerts": ["⚠️ 数据获取失败"],
                })
                continue

            # 每日评分：获取60日K线做技术和趋势判断
            hist = None
            try:
                hist = get_history(code, days=60)
            except Exception:
                pass
            ds = daily_score(rt, hist, baseline_score)
            score_change = ds["score"] - baseline_score

            risk = item.get("risk", {})
            alerts = check_alerts(item, rt, risk)

            # 评分大幅变化预警
            if score_change <= -15:
                alerts.append(f"📉 评分大幅下降 {score_change:+d}（{baseline_score}→{ds['score']}）")
            elif score_change >= 10:
                alerts.append(f"📈 评分显著上升 {score_change:+d}（{baseline_score}→{ds['score']}）")

            entry_price = risk.get("entry_price", rt["price"])
            total_chg = (rt["price"] - entry_price) / entry_price * 100 if entry_price > 0 else 0

            entry_data = {
                "code": code,
                "name": name,
                "entry_price": round(entry_price, 2),
                "current_price": round(rt["price"], 2),
                "change_from_entry_pct": round(total_chg, 1),
                "today_change_pct": round(rt.get("change_pct", 0), 1),
                "turnover_rate": round(rt.get("turnover_rate", 0), 2),
                "volume_ratio": round(rt.get("volume_ratio", 1.0), 2),
                "baseline_score": baseline_score,
                "daily_score": ds["score"],
                "score_change": score_change,
                "daily_signals": ds["signals"],
                "alerts": alerts,
            }
            entries.append(entry_data)

            score_str = f"评分 {ds['score']}({score_change:+d})"
            if alerts:
                alert_summary.extend(alerts)
                log(f"  {code} {name}: {rt['price']:.2f} | {score_str} | 预警: {', '.join(alerts)}")
            else:
                log(f"  {code} {name}: {rt['price']:.2f} | {score_str}")
        except Exception as e:
            log(f"  {code} {name}: ERROR: {e}")

    # 保存当日快照
    tracking_dir = get_tracking_dir(ym_str)
    snapshot_path = tracking_dir / f"{today}.json"
    snapshot = {
        "date": today,
        "year_month": ym_str,
        "index_data": index_data,
        "entries": entries,
        "alert_summary": alert_summary,
    }

    # numpy 兼容
    def clean(obj):
        if isinstance(obj, dict):
            return {k: clean(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [clean(v) for v in obj]
        elif isinstance(obj, (np.integer,)):
            return int(obj)
        elif isinstance(obj, (np.floating,)):
            return float(obj)
        return obj

    snapshot = clean(snapshot)
    with open(snapshot_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    log(f"\n快照已保存: {snapshot_path}")

    # 更新 summary.md
    update_summary(ym_str, today, entries, alert_summary, index_data)

    # 打印当日简报
    print("\n" + "=" * 50)
    print(f"日跟踪简报 — {today}")
    print("=" * 50)
    alert_count = sum(1 for e in entries if e["alerts"])
    up_count = sum(1 for e in entries if e.get("change_from_entry_pct", 0) and e["change_from_entry_pct"] > 0)
    down_count = sum(1 for e in entries if e.get("change_from_entry_pct", 0) and e["change_from_entry_pct"] < 0)
    valid_entries = [e for e in entries if e["current_price"] is not None]
    avg_chg = sum(e.get("change_from_entry_pct", 0) for e in valid_entries) / len(valid_entries) if valid_entries else 0
    avg_daily_score = sum(e.get("daily_score", 0) or 0 for e in entries) / len(entries) if entries else 0
    avg_baseline = sum(e.get("baseline_score", 0) or 0 for e in entries) / len(entries) if entries else 0
    print(f"跟踪: {len(valid_entries)}/{len(entries)} 有效 | 上涨: {up_count} | 下跌: {down_count} | 预警: {alert_count}")
    print(f"平均累计涨跌: {avg_chg:+.1f}% | 平均日评: {avg_daily_score:.0f}分（月评: {avg_baseline:.0f}分）")
    # 评分有效性分析
    score_analysis = analyze_score_effectiveness(entries, ym_str, today)

    if alert_summary:
        print(f"\n预警详情:")
        for a in alert_summary[:10]:
            print(f"  {a}")
        if len(alert_summary) > 10:
            print(f"  ... 共 {len(alert_summary)} 条")

    print(f"\n--- 评分趋势 ---")
    print(f"月评最高3只 平均涨跌: {score_analysis['top3_baseline_avg_chg']:+.1f}%")
    print(f"月评最低3只 平均涨跌: {score_analysis['bot3_baseline_avg_chg']:+.1f}%")
    print(f"日评最高3只 平均涨跌: {score_analysis['top3_daily_avg_chg']:+.1f}%")
    print(f"评分方向与涨跌方向一致: {score_analysis['score_price_align']}/{score_analysis['valid_count']} 只")
    print(f"日评变化均值: {score_analysis['avg_score_change']:+.0f} 分")


def analyze_score_effectiveness(entries, ym_str, today):
    """分析评分是否对涨跌有区分度。"""
    valid = [e for e in entries if e.get("current_price") is not None and e.get("daily_score") is not None]
    if len(valid) < 6:
        return {
            "top3_baseline_avg_chg": 0, "bot3_baseline_avg_chg": 0,
            "top3_daily_avg_chg": 0, "score_price_align": 0, "valid_count": 0,
            "avg_score_change": 0,
        }

    # 按月评排序取高低组
    by_baseline = sorted(valid, key=lambda e: e.get("baseline_score", 0), reverse=True)
    top3_b = by_baseline[:3]
    bot3_b = by_baseline[-3:]
    top3_b_avg = sum(e.get("change_from_entry_pct", 0) for e in top3_b) / 3
    bot3_b_avg = sum(e.get("change_from_entry_pct", 0) for e in bot3_b) / 3

    # 按日评排序
    by_daily = sorted(valid, key=lambda e: e.get("daily_score", 0), reverse=True)
    top3_d = by_daily[:3]
    top3_d_avg = sum(e.get("change_from_entry_pct", 0) for e in top3_d) / 3

    # 评分变化方向 vs 涨跌方向一致性
    align = 0
    for e in valid:
        sc = e.get("score_change", 0)
        chg = e.get("change_from_entry_pct", 0)
        if (sc > 0 and chg > 0) or (sc < 0 and chg < 0):
            align += 1

    avg_sc = sum(e.get("score_change", 0) for e in valid) / len(valid)

    # 加载前一日快照，对比评分趋势
    prev_scores = {}
    tracking_dir = get_tracking_dir(ym_str)
    prev_date = None
    try:
        from datetime import timedelta
        prev_dt = datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)
        # 往前找最近的有效快照
        for offset in range(1, 8):
            check_dt = datetime.strptime(today, "%Y-%m-%d") - timedelta(days=offset)
            check_date = check_dt.strftime("%Y-%m-%d")
            check_path = tracking_dir / f"{check_date}.json"
            if check_path.exists():
                with open(check_path, "r", encoding="utf-8") as f:
                    prev_data = json.load(f)
                for pe in prev_data.get("entries", []):
                    if pe.get("daily_score") is not None:
                        prev_scores[pe["code"]] = pe["daily_score"]
                prev_date = check_date
                break
    except Exception:
        pass

    # 连续评分趋势
    if prev_scores:
        improved = sum(1 for e in valid if e.get("daily_score", 0) > prev_scores.get(e["code"], 0))
        declined = sum(1 for e in valid if e.get("daily_score", 0) < prev_scores.get(e["code"], 0))
        score_momentum = f"↑{improved} ↓{declined} (vs {prev_date})"
    else:
        score_momentum = "无前日数据"

    return {
        "top3_baseline_avg_chg": round(top3_b_avg, 1),
        "bot3_baseline_avg_chg": round(bot3_b_avg, 1),
        "top3_daily_avg_chg": round(top3_d_avg, 1),
        "score_price_align": align,
        "valid_count": len(valid),
        "avg_score_change": round(avg_sc, 0),
        "score_momentum": score_momentum,
    }


def update_summary(ym_str, today, entries, alert_summary, index_data):
    """更新（或创建）月度累计跟踪报告。"""
    sp = get_summary_path(ym_str)
    is_new = not sp.exists()

    valid = [e for e in entries if e["current_price"] is not None]
    up_count = sum(1 for e in valid if e["change_from_entry_pct"] > 0)
    down_count = sum(1 for e in valid if e["change_from_entry_pct"] < 0)
    avg_chg = sum(e["change_from_entry_pct"] for e in valid) / len(valid) if valid else 0

    with open(sp, "a" if not is_new else "w", encoding="utf-8") as f:
        if is_new:
            f.write(f"# {ym_str} 月度跟踪报告\n\n")
            f.write(f"扫描日期: {today}\n\n---\n\n")

        f.write(f"## {today}\n\n")
        f.write("| # | 代码 | 名称 | 入场价 | 现价 | 累计涨跌 | 今日涨跌 | 月评→日评 | 预警 |\n")
        f.write("|---|------|------|--------|------|----------|----------|-----------|------|\n")
        for e in entries:
            ep = e.get("entry_price", 0)
            cp = e.get("current_price") or 0
            total = e.get("change_from_entry_pct") or 0
            today_chg = e.get("today_change_pct") or 0
            bs = e.get("baseline_score") or 0
            ds = e.get("daily_score")
            ds_str = f"{ds}({e.get('score_change', 0):+d})" if ds is not None else "-"
            alert_str = " ".join(e.get("alerts", [])) if e.get("alerts") else "-"
            f.write(f"| {e['code']} | {e['name']} | {ep:.2f} | {cp:.2f} | {total:+.1f}% | {today_chg:+.1f}% | {bs}→{ds_str} | {alert_str} |\n")

        avg_daily = sum(e.get("daily_score", 0) or 0 for e in entries) / len(entries) if entries else 0
        avg_baseline = sum(e.get("baseline_score", 0) or 0 for e in entries) / len(entries) if entries else 0
        f.write(f"\n**今日统计**: 上涨 {up_count} / 下跌 {down_count} / 预警 {len(alert_summary)} / 平均累计涨跌 {avg_chg:+.1f}% / 平均日评 {avg_daily:.0f}分（月评 {avg_baseline:.0f}分）\n")

        # 评分有效性
        sa = analyze_score_effectiveness(entries, ym_str, today)
        f.write(f"\n**评分趋势**:\n")
        f.write(f"- 月评Top3平均涨跌: {sa['top3_baseline_avg_chg']:+.1f}% | 月评Bottom3: {sa['bot3_baseline_avg_chg']:+.1f}%\n")
        f.write(f"- 日评Top3平均涨跌: {sa['top3_daily_avg_chg']:+.1f}%\n")
        f.write(f"- 评分方向与涨跌一致: {sa['score_price_align']}/{sa['valid_count']} 只\n")
        f.write(f"- 日评变化均值: {int(sa['avg_score_change']):+d}分 | {sa['score_momentum']}\n")

        if alert_summary:
            for a in alert_summary:
                f.write(f"- {a}\n")
        f.write("\n---\n\n")

    log(f"跟踪报告已更新: {sp}")


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    import argparse
    parser = argparse.ArgumentParser(description="日跟踪科创板+创业板 top 20")
    parser.add_argument("--year-month", type=str, default=None,
                        help="指定月份（如 2026-06），默认当前月")
    args = parser.parse_args()

    ym_str = args.year_month or datetime.now().strftime("%Y-%m")
    run_track(ym_str)


if __name__ == "__main__":
    main()
