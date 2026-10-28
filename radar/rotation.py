#!/usr/bin/env python3
"""
板块启动探测器 —— 找"资金在轮进来"的那一天

核心思路：个股放量只是个股事件；一个池子里多只票同一天放量 + 跑赢大盘，
才是资金在做板块轮动。所以真正的信号在「共振」，不在单票。

个股当日打分（0-3 分）：
  +1  量能：成交量 >= 过去20日均量 × VOL_MULT
  +1  价格：当日上涨 且 收盘站上20日均线
  +1  强度：个股5日涨幅 − 指数5日涨幅 >= RS_GAP

板块信号：当日 >= TRIGGER_N 只个股拿到满分 3 分
"""
import sys
from collections import OrderedDict

VOL_MULT  = 1.8      # 放量倍数门槛
RS_GAP    = 0.03     # 5日相对指数超额 >= 3%
TRIGGER_N = 3        # 几只共振算板块启动
MA_N      = 20


def load(path):
    series, idx, cur = OrderedDict(), None, None
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            code, name = line.split("|")
            cur = (code, name)
            continue
        rows = []
        for rec in line.split(";"):
            d, c, v = rec.split(",")
            rows.append((d, float(c), float(v)))
        if cur[0].startswith("399") or cur[0].startswith("000001.SH"):
            idx = {d: c for d, c, _ in rows}
        else:
            series[cur] = rows
    return series, idx


def analyze(path):
    series, idx = load(path)
    dates = [d for d, _, _ in next(iter(series.values()))]
    hits = OrderedDict((d, []) for d in dates)
    detail = {}

    for (code, name), rows in series.items():
        closes = [r[1] for r in rows]
        vols = [r[2] for r in rows]
        for i in range(MA_N + 5, len(rows)):
            d = rows[i][0]
            score, why = 0, []

            avg_v = sum(vols[i - MA_N:i]) / MA_N
            ratio = vols[i] / avg_v if avg_v else 0
            if ratio >= VOL_MULT:
                score += 1
                why.append(f"量{ratio:.1f}x")

            ma = sum(closes[i - MA_N:i]) / MA_N
            up = closes[i] > closes[i - 1]
            if up and closes[i] > ma:
                score += 1
                why.append("站上MA20")

            r5 = closes[i] / closes[i - 5] - 1
            ir = (idx[d] / idx[dates[i - 5]] - 1) if idx and d in idx else 0
            rs = r5 - ir
            if rs >= RS_GAP:
                score += 1
                why.append(f"超额{rs*100:+.0f}%")

            if score == 3:
                hits[d].append(name)
                detail[(d, name)] = " ".join(why)

    print("=" * 70)
    print(f"池子 {len(series)} 只 | 门槛: 量>={VOL_MULT}x 均量, 5日超额>={RS_GAP*100:.0f}%, "
          f"{TRIGGER_N}只共振")
    print("=" * 70)
    fired = [(d, v) for d, v in hits.items() if len(v) >= TRIGGER_N]
    if not fired:
        print("区间内无板块级信号")
    for d, names in fired:
        print(f"\n★ {d}  {len(names)}/{len(series)} 只共振  ← 板块启动信号")
        for n in names:
            print(f"    {n:<10} {detail[(d, n)]}")

    # 个股信号时间线
    print("\n" + "-" * 70)
    print("逐日个股命中（看共振是怎么累积起来的）")
    print("-" * 70)
    for d, names in hits.items():
        if names:
            mark = "  ★" if len(names) >= TRIGGER_N else ""
            print(f"{d}  {len(names)} 只: {'、'.join(names)}{mark}")

    # 大盘同期表现
    if idx:
        print("\n" + "-" * 70)
        first_fire = fired[0][0] if fired else None
        if first_fire:
            i = dates.index(first_fire)
            print(f"信号日 {first_fire} 之后，到 {dates[-1]}：")
            for (code, name), rows in series.items():
                a, b = rows[i][1], rows[-1][1]
                print(f"  {name:<10} {a:>8.2f} → {b:>8.2f}   {(b/a-1)*100:+7.1f}%")
            ia, ib = idx[first_fire], idx[dates[-1]]
            print(f"  {'深证成指':<10} {ia:>8.0f} → {ib:>8.0f}   {(ib/ia-1)*100:+7.1f}%")


if __name__ == "__main__":
    analyze(sys.argv[1] if len(sys.argv) > 1 else "basket.txt")
