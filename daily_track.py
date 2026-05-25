"""日跟踪脚本：加载当月 watchlist，检查 top 20 股票当日表现并更新跟踪报告。

用法：python daily_track.py [--year-month 2026-06]

功能：
  1. 加载 watchlist/YYYY-MM.json 中的 top 20
  2. 获取每只股票实时行情（~30s）
  3. 检查预警：止损/止盈/异动
  4. 保存当日快照 JSON
  5. 更新累计跟踪 summary.md
"""

import sys
import io
import os
import json
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from data_fetcher import get_realtime, get_index_data

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

    # 获取大盘
    log("获取指数数据...")
    index_data = get_index_data()

    # 逐只检查
    entries = []
    alert_summary = []
    for item in top20:
        code = item["code"]
        name = item["name"]
        try:
            rt = get_realtime(code)
            if rt is None or rt["price"] <= 0:
                log(f"  {code} {name}: 数据获取失败")
                entries.append({
                    "code": code, "name": name,
                    "entry_price": item["risk"].get("entry_price", 0),
                    "current_price": None,
                    "change_from_entry_pct": None,
                    "today_change_pct": None,
                    "alerts": ["⚠️ 数据获取失败"],
                })
                continue

            risk = item.get("risk", {})
            alerts = check_alerts(item, rt, risk)
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
                "alerts": alerts,
            }
            entries.append(entry_data)
            if alerts:
                alert_summary.extend(alerts)
                log(f"  {code} {name}: 价格 {rt['price']:.2f} ({total_chg:+.1f}%) | 预警: {', '.join(alerts)}")
            else:
                log(f"  {code} {name}: 价格 {rt['price']:.2f} ({total_chg:+.1f}%)")
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
    print(f"跟踪: {len(valid_entries)}/{len(entries)} 有效 | 上涨: {up_count} | 下跌: {down_count} | 预警: {alert_count}")
    print(f"平均累计涨跌: {avg_chg:+.1f}%")
    if alert_summary:
        print(f"\n预警详情:")
        for a in alert_summary:
            print(f"  {a}")


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
        f.write("| # | 代码 | 名称 | 入场价 | 现价 | 累计涨跌 | 今日涨跌 | 预警 |\n")
        f.write("|---|------|------|--------|------|----------|----------|------|\n")
        for e in entries:
            ep = e.get("entry_price", 0)
            cp = e.get("current_price") or 0
            total = e.get("change_from_entry_pct") or 0
            today_chg = e.get("today_change_pct") or 0
            alert_str = " ".join(e.get("alerts", [])) if e.get("alerts") else "-"
            f.write(f"| {e['code']} | {e['name']} | {ep:.2f} | {cp:.2f} | {total:+.1f}% | {today_chg:+.1f}% | {alert_str} |\n")

        f.write(f"\n**今日统计**: 上涨 {up_count} / 下跌 {down_count} / 预警 {len(alert_summary)} / 平均累计涨跌 {avg_chg:+.1f}%\n")
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
