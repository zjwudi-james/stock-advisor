"""月全量扫描：遍历科创板+创业板，输出 top 20 到 watchlist JSON。

用法：python monthly_scan.py [--year-month 2026-06]

三轮漏斗：
  1. Sina 全市场初筛（2000+ 只，~30s）→ top 300
  2. XQ 精细打分（300 只，~8min）→ top 50
  3. 深度四维分析（50 只，~7min）→ top 20 写入 JSON
"""

import sys
import io
import os
import json
import time
import argparse
from datetime import datetime
from pathlib import Path

import akshare as ak

# 确保可以从 stock-advisor 目录导入
sys.path.insert(0, str(Path(__file__).parent))
from data_fetcher import get_realtime, get_history, get_fund_flow, get_financials, get_index_data
from analyzer import StockAnalyzer

TARGET_PREFIXES = ("sh688", "sz300", "sz301")
OUTPUT_DIR = Path(r"C:\Users\zhangjie\Claude\投资分析\实战演练\watchlist")


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def sina_quick_scan():
    """第一轮：Sina 全市场初筛，返回 (candidates, market_dict)。"""
    log("  [1/3] Sina 全市场初筛...")
    df = None
    delays = [10, 20, 30]
    for attempt, delay in enumerate(delays):
        try:
            t0 = time.time()
            df = ak.stock_zh_a_spot()
            log(f"    Sina OK: {len(df)} 只, {time.time() - t0:.0f}s")
            break
        except Exception as e:
            log(f"    尝试 {attempt + 1}/3 失败: {e}")
            if attempt < 2:
                log(f"    等待 {delay}s 后重试...")
                time.sleep(delay)

    if df is None:
        log("    Sina 3次均失败")
        return None

    # 解析 Sina 数据：0=代码, 1=名称, 2=最新价, 4=涨跌幅, 7=昨收, 9=最高, 10=最低, 11=成交量, 12=成交额
    candidates = []
    for _, row in df.iterrows():
        code = str(row[df.columns[0]])
        if not code.startswith(TARGET_PREFIXES):
            continue
        try:
            price = float(row[df.columns[2]])
            chg = float(row[df.columns[4]])
            vol = float(row[df.columns[11]])
            amt = float(row[df.columns[12]])
            if price <= 0 or vol <= 0:
                continue
            score = 50.0
            if -2 < chg < 0:
                score += 6
            elif 0 <= chg < 3:
                score += 4
            elif chg > 5:
                score -= 4
            elif chg < -5:
                score -= 2
            if amt > 1e9:
                score += 8
            elif amt > 3e8:
                score += 5
            elif amt < 5e7:
                score -= 5
            high = float(row[df.columns[9]])
            low = float(row[df.columns[10]])
            pre_close = float(row[df.columns[7]])
            if pre_close > 0:
                amp = (high - low) / pre_close * 100
                if 3 < amp < 7:
                    score += 5
                elif amp > 10:
                    score -= 3
            candidates.append((code[2:], str(row[df.columns[1]]), round(score)))
        except (ValueError, TypeError, IndexError):
            continue

    candidates.sort(key=lambda x: x[2], reverse=True)
    log(f"    筛选出 {len(candidates)} 只科创板+创业板")
    return candidates


def xq_fallback_scan():
    """Sina 失败时的后备：XQ 采样 500 只。"""
    log("  [1/3-alt] Sina 失败，XQ 后备采样...")
    from data_fetcher import get_stock_list
    all_stocks = get_stock_list()
    targets = [(c, n) for c, n in all_stocks if c.startswith(("688", "300", "301"))]
    step = max(1, len(targets) // 500)
    sample = targets[::step][:500]
    log(f"    采样 {len(sample)} 只（从 {len(targets)} 中步长 {step}）")

    candidates = []
    for i, (code, name) in enumerate(sample):
        try:
            rt = get_realtime(code)
            if rt is None or rt["price"] <= 0:
                continue
            score = 50.0
            pe = rt.get("pe_dynamic", 0)
            if 0 < pe < 20:
                score += 12
            elif 20 <= pe < 40:
                score += 4
            elif pe >= 200 or pe <= 0:
                score -= 8
            pb = rt.get("pb", 0)
            if 0 < pb < 2:
                score += 6
            chg = rt.get("change_pct", 0)
            if -3 < chg < 0:
                score += 5
            turnover = rt.get("turnover_rate", 0)
            if 2 < turnover < 10:
                score += 6
            candidates.append((code, name, round(score), rt))
        except Exception:
            continue
        if (i + 1) % 100 == 0:
            log(f"    采样进度: {i + 1}/{len(sample)} ({len(candidates)} 有效)")
    candidates.sort(key=lambda x: x[2], reverse=True)
    return candidates


def xq_refine(candidates):
    """第二轮：XQ 精细打分，取 top300 用雪球实时数据重新评分。"""
    top_n = candidates[:300]
    # 检查 candidates 是否已有 rt 数据（后备模式）还是只有 code/name/score（Sina 模式）
    has_rt = len(candidates[0]) >= 4 if candidates else False

    if has_rt:
        # 后备模式：已有 rt 数据，直接取前 300
        log(f"  [2/3] XQ 精细打分（已有数据）: {len(top_n)} 只")
        # 用更完整的数据重新打分
        refined = []
        for code, name, old_score, rt in top_n:
            score = 50.0
            pe = rt.get("pe_dynamic", 0)
            if 0 < pe < 20:
                score += 12
            elif 20 <= pe < 40:
                score += 4
            elif pe >= 200 or pe <= 0:
                score -= 8
            pb = rt.get("pb", 0)
            if 0 < pb < 2:
                score += 6
            elif pb > 10:
                score -= 4
            turnover = rt.get("turnover_rate", 0)
            if 2 < turnover < 10:
                score += 6
            elif turnover < 0.5:
                score -= 3
            vr = rt.get("volume_ratio", 1.0)
            if vr > 1.5:
                score += 4
            refined.append((code, name, round(score), rt))
        refined.sort(key=lambda x: x[2], reverse=True)
        return refined
    else:
        # Sina 模式：需要逐个获取 XQ 数据
        log(f"  [2/3] XQ 精细打分: {len(top_n)} 只...")
        refined = []
        for i, (code, name, sina_score) in enumerate(top_n):
            try:
                rt = get_realtime(code)
                if rt is None or rt["price"] <= 0:
                    continue
                score = 50.0
                pe = rt.get("pe_dynamic", 0)
                if 0 < pe < 20:
                    score += 12
                elif 20 <= pe < 40:
                    score += 4
                elif pe >= 200 or pe <= 0:
                    score -= 8
                pb = rt.get("pb", 0)
                if 0 < pb < 2:
                    score += 6
                elif pb > 10:
                    score -= 4
                turnover = rt.get("turnover_rate", 0)
                if 2 < turnover < 10:
                    score += 6
                elif turnover < 0.5:
                    score -= 3
                vr = rt.get("volume_ratio", 1.0)
                if vr > 1.5:
                    score += 4
                refined.append((code, name, round(score), rt))
            except Exception:
                continue
            if (i + 1) % 50 == 0:
                log(f"    XQ 进度: {i + 1}/{len(top_n)} ({len(refined)} 有效)")
        refined.sort(key=lambda x: x[2], reverse=True)
        return refined


def deep_analyze_top50(refined, market, index_data):
    """第三轮：深度四维分析 top 50。"""
    top50 = refined[:50]
    log(f"  [3/3] 深度四维分析: {len(top50)} 只...")
    analyzer = StockAnalyzer()
    results = []
    for i, (code, name, refined_score, rt) in enumerate(top50):
        try:
            hist = get_history(code, days=250)
            flow = get_fund_flow(code, days=20)
            financials = get_financials(code)
            result = analyzer.analyze(
                code=code, name=name, realtime=rt, hist=hist,
                flow=flow, financials=financials, market=market,
                index_data=index_data,
            )
            # 用当前价作为 entry_price
            result["risk"]["entry_price"] = rt["price"]
            results.append((code, name, result))
            dims = result["dimensions"]
            scores = "/".join(str(dims.get(k, {}).get("score", "-")) for k in ["technical", "fund_flow", "sentiment", "fundamental"])
            log(f"    [{i + 1}/{len(top50)}] {code} {name} => {result['total_score']}分 {result['recommendation']} [{scores}]")
        except Exception as e:
            log(f"    [{i + 1}] {code} {name} => ERROR: {e}")
            continue
    results.sort(key=lambda x: x[2]["total_score"], reverse=True)
    return results


def clean_for_json(obj):
    """将 numpy 类型转为 Python 原生类型，确保 JSON 可序列化。"""
    import numpy as np
    if isinstance(obj, dict):
        return {k: clean_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_for_json(v) for v in obj]
    elif isinstance(obj, (np.integer,)):
        return int(obj)
    elif isinstance(obj, (np.floating,)):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def save_results(results, ym_str, market, index_data):
    """保存 top 20 到 JSON。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"{ym_str}.json"

    env_data = {}
    if results:
        env_data = results[0][2].get("market_env", {})

    top20 = []
    for rank, (code, name, r) in enumerate(results[:20], 1):
        dims = r["dimensions"]
        sigs = []
        for dk in ["technical", "fund_flow", "sentiment", "fundamental"]:
            sigs.extend(dims.get(dk, {}).get("signals", [])[:3])
        top20.append({
            "rank": rank,
            "code": code,
            "name": name,
            "total_score": r["total_score"],
            "recommendation": r["recommendation"],
            "dimensions": {
                k: {
                    "score": dims.get(k, {}).get("score"),
                    "signals": dims.get(k, {}).get("signals", []),
                }
                for k in ["technical", "fund_flow", "sentiment", "fundamental"]
            },
            "risk": r.get("risk", {}),
            "signals": sigs[:8],
        })

    output = {
        "scan_date": datetime.now().strftime("%Y-%m-%d"),
        "year_month": ym_str,
        "market_env": env_data,
        "index_data": index_data,
        "top20": top20,
        "total_scanned": len(results) if results else 0,
    }

    output = clean_for_json(output)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    log(f"\nWatchlist 已保存: {output_path}")
    return output_path


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description="月全量扫描科创板+创业板")
    parser.add_argument("--year-month", type=str, default=None,
                        help="指定月份（如 2026-06），默认当前月")
    args = parser.parse_args()

    if args.year_month:
        ym_str = args.year_month
    else:
        ym_str = datetime.now().strftime("%Y-%m")

    log("=" * 60)
    log(f"月扫描: 科创板+创业板 — {ym_str}")
    log("=" * 60)

    # 第一轮：Sina 初筛（或 XQ 后备）
    candidates = sina_quick_scan()
    if candidates is None:
        candidates = xq_fallback_scan()
    if not candidates:
        log("ERROR: 无有效股票数据")
        return

    # 大盘环境
    log("\n  获取大盘环境...")
    t0 = time.time()
    index_data = get_index_data()
    market = None  # Sina 已获取全市场数据，不需要再调 get_market_overview
    log(f"  指数: {bool(index_data)}, {time.time() - t0:.0f}s")

    # 第二轮：XQ 精细打分
    refined = xq_refine(candidates)

    # 第三轮：深度分析
    results = deep_analyze_top50(refined, market, index_data)

    # 保存
    save_results(results, ym_str, market, index_data)

    # 输出 top 10
    print("\n" + "=" * 60)
    print(f"TOP 10 — {ym_str}")
    print("=" * 60)
    for rank, (code, name, r) in enumerate(results[:10], 1):
        risk = r.get("risk", {})
        dims = r["dimensions"]
        scores = "/".join(str(dims.get(k, {}).get("score", "-")) for k in ["technical", "fund_flow", "sentiment", "fundamental"])
        print(f"{rank:2}. {code} {name}  {r['total_score']}分 {r['recommendation']}  [{scores}]  "
              f"止损:{risk.get('stop_loss', 0):.2f}  仓位:{risk.get('position_pct', 0)}%  "
              f"盈亏比:{risk.get('risk_reward', 0):.1f}:1")

    log("完成。")


if __name__ == "__main__":
    main()
