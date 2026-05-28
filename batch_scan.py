"""批量扫描科创板+创业板，输出综合评分前10。Sina全市场初筛 + XQ精细打分 + 深度分析。"""

import sys
import io
import time
import akshare as ak
from data_fetcher import (
    get_realtime, get_history, get_fund_flow,
    get_financials, get_market_overview, get_index_data,
)
from analyzer import StockAnalyzer

TARGET_PREFIXES = ("sh688", "sz300", "sz301")


def quick_scan_sina():
    """Sina全市场行情（一次API调用），筛选科创板+创业板并快速打分。"""
    print("  调用 Sina stock_zh_a_spot()...")
    t0 = time.time()
    df = None
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_spot()
            break
        except Exception as e:
            print(f"  尝试 {attempt + 1}/3 失败: {e}")
            if attempt < 2:
                print("  等待5秒后重试...")
                time.sleep(5)
    if df is None:
        print("  Sina接口3次均失败，改用XQ逐只扫描（较慢）")
        return None
    print(f"  获取 {len(df)} 只, 耗时 {time.time() - t0:.0f}s")

    # 列: 0=代码(sh600519), 1=名称, 2=最新价, 4=涨跌幅, 7=昨收, 9=最高, 10=最低, 11=成交量, 12=成交额
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
            # 涨跌幅（偏好小幅回调或温和上涨）
            if -2 < chg < 0:
                score += 6
            elif 0 <= chg < 3:
                score += 4
            elif chg > 5:
                score -= 4
            elif chg < -5:
                score -= 2

            # 成交额（流动性）
            if amt > 1e9:
                score += 8
            elif amt > 3e8:
                score += 5
            elif amt < 5e7:
                score -= 5

            # 振幅
            high = float(row[df.columns[9]])
            low = float(row[df.columns[10]])
            pre_close = float(row[df.columns[7]])
            if pre_close > 0:
                amp = (high - low) / pre_close * 100
                if 3 < amp < 7:
                    score += 5
                elif amp > 10:
                    score -= 3

            pure_code = code[2:]
            candidates.append((pure_code, str(row[df.columns[1]]), round(score)))
        except (ValueError, TypeError, IndexError):
            continue

    candidates.sort(key=lambda x: x[2], reverse=True)
    print(f"  筛选出 {len(candidates)} 只, top5: {[(c[0], c[1], c[2]) for c in candidates[:5]]}")
    return candidates


def deep_scan(candidates, market, index_data):
    """精细打分+深度分析。先对初筛前200用XQ精打，top50深度分析。"""
    analyzer = StockAnalyzer()

    # 阶段1: XQ精细打分（前200）
    top200 = candidates[:200]
    print(f"  XQ精细打分: {len(top200)} 只...")
    refined = []
    for i, (code, name, sina_score) in enumerate(top200):
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
            print(f"    XQ进度: {i + 1}/{len(top200)}")

    refined.sort(key=lambda x: x[2], reverse=True)

    # 阶段2: 深度四维分析（前50）
    top50 = refined[:50]
    print(f"  深度四维分析: {len(top50)} 只...")
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
            results.append((code, name, result))
            print(f"    [{i + 1}/{len(top50)}] {code} {name} => {result['total_score']}分 {result['recommendation']}")
        except Exception as e:
            print(f"    [{i + 1}] {code} {name} => ERROR: {e}")
            continue

    results.sort(key=lambda x: x[2]["total_score"], reverse=True)
    return results


def print_results(results):
    print("\n" + "=" * 60)
    print("综合评分 TOP 10")
    print("=" * 60)
    for rank, (code, name, r) in enumerate(results[:10], 1):
        env = r.get("market_env", {})
        risk = r.get("risk", {})
        dims = r["dimensions"]
        scores = "/".join(
            str(dims.get(k, {}).get("score", "-"))
            for k in ["technical", "fund_flow", "sentiment", "fundamental"]
        )
        print(f"{rank:2}. {code} {name}")
        print(f"    综合: {r['total_score']}分 | 建议: {r['recommendation']} | 四维: [{scores}]")
        print(f"    大盘: {env.get('regime', 'N/A')} | 仓位: {risk.get('position_pct', 0)}% | "
              f"止损: {risk.get('stop_loss', 0)} | 盈亏比: {risk.get('risk_reward', 0)}:1")
        sigs = []
        for dk in ["technical", "fund_flow", "sentiment", "fundamental"]:
            sigs.extend(dims.get(dk, {}).get("signals", [])[:2])
        print(f"    信号: {'; '.join(sigs[:6])}")
        print()
    print("完成")


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    print("=" * 60)
    print("科创板 + 创业板 批量扫描")
    print("=" * 60)

    print("\n[1/3] Sina 全市场初筛...")
    candidates = quick_scan_sina()
    if not candidates:
        print("\nSina初筛失败，启用后备方案：XQ扫描300只样本...")
        from data_fetcher import get_stock_list
        all_stocks = get_stock_list()
        targets = [(c, n) for c, n in all_stocks if c.startswith(("688", "300", "301"))]
        # 均匀采样300只
        step = max(1, len(targets) // 300)
        sample = targets[::step][:300]
        print(f"  样本数: {len(sample)} (从 {len(targets)} 只中采样)")
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
            if (i + 1) % 50 == 0:
                print(f"  采样进度: {i + 1}/{len(sample)}")
        candidates.sort(key=lambda x: x[2], reverse=True)
        print(f"  有效数据: {len(candidates)}")

        # 采样模式下直接深度分析前50
        print("\n  深度分析前50...")
        top50 = candidates[:50]
        analyzer = StockAnalyzer()
        results = []
        for i, (code, name, quick_score, rt) in enumerate(top50):
            try:
                hist = get_history(code, days=250)
                flow = get_fund_flow(code, days=20)
                financials = get_financials(code)
                result = analyzer.analyze(
                    code=code, name=name, realtime=rt, hist=hist,
                    flow=flow, financials=financials, market=market,
                    index_data=index_data,
                )
                results.append((code, name, result))
                print(f"    [{i + 1}] {code} {name} => {result['total_score']}分")
            except Exception as e:
                print(f"    [{i + 1}] {code} {name} => ERROR")
                continue
        results.sort(key=lambda x: x[2]["total_score"], reverse=True)
        print_results(results)
        return

    print("\n[2/3] 大盘环境...")
    market = get_market_overview()
    index_data = get_index_data()
    print(f"  大盘: {bool(market)}, 指数: {bool(index_data)}")

    print("\n[3/3] 深度分析...")
    results = deep_scan(candidates, market, index_data)
    print_results(results)


if __name__ == "__main__":
    main()
