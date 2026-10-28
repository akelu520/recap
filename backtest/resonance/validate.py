#!/usr/bin/env python3
"""
题材轮动雷达 · 规则回放验证

数据: 2023-10 ~ 2026-09（约 710 个交易日），前复权日线
入场: 信号日次日开盘价（不使用信号日之后才知道的价格）
收益: 次日开盘 -> 第 5 / 20 个交易日收盘
超额: 个股收益 - 同期深证成指收益（同样口径）

检查项:
  1. 共振事件（题材内 >=3 只同日满分）之后的超额收益
  2. 单票孤立信号（满分但题材没共振）之后的超额收益 —— 检验「共振比单票靠谱」
  3. 无条件基准（任意一天买入）
  4. 剔除 2024-09-24「924 行情」窗口后结果是否还成立
  5. 分年份是否稳定
"""
import statistics as st, datetime as dt, sys
from collections import defaultdict

RS_GAP, TRIGGER, GAP_DAYS = 3.0, 3, 10
NAMES = {"dexhand": "灵巧手", "aicompute": "AI算力", "memory": "存储芯片",
         "ssb": "固态电池", "lowalt": "低空经济"}

# ---------- load ----------
IDX = {}
for rec in open("idx.txt").read().strip().split(";"):
    d, r5, f5, f20 = rec.split(",")
    IDX[d] = (float(r5), float(f5), float(f20))
DAYS = sorted(IDX)
POS = {d: i for i, d in enumerate(DAYS)}

STK, BASE = {}, {}
for line in open("stocks.txt"):
    line = line.strip()
    if not line:
        continue
    code, name, theme, u5, u20, cand = line.split("|")
    rows = {}
    for rec in cand.split(";"):
        d, r5, f5, f20 = rec.split(",")
        rows[d] = (float(r5), float(f5), float(f20))
    STK[code] = (name, theme, rows)
    BASE[code] = (float(u5), float(u20))

idx_u5 = st.mean(v[1] for v in IDX.values())
idx_u20 = st.mean(v[2] for v in IDX.values())

def sess(d):          # 数据源时间戳 = 前一日 16:00 UTC -> 交易日 +1 天
    x = dt.date(2000 + int(d[:2]), int(d[2:4]), int(d[4:])) + dt.timedelta(days=1)
    return x.isoformat()

def in924(d):
    return "240920" <= d <= "241015"

# ---------- 每只票每天：是否满分 ----------
hits = defaultdict(lambda: defaultdict(list))      # theme -> day -> [(code, ex5, ex20)]
for code, (name, theme, rows) in STK.items():
    for d, (r5, f5, f20) in rows.items():
        if d not in IDX:
            continue
        ir5, if5, if20 = IDX[d]
        if r5 - ir5 >= RS_GAP:
            hits[theme][d].append((code, f5 - if5, f20 - if20))

# ---------- 共振事件，按 10 个交易日去重 ----------
episodes = []
fire_days = defaultdict(set)
for theme, days in hits.items():
    last = -999
    for d in sorted(days):
        if len(days[d]) >= TRIGGER:
            fire_days[theme].add(d)
            if POS[d] - last > GAP_DAYS:
                hs = days[d]
                episodes.append({
                    "theme": theme, "d": d, "n": len(hs),
                    "who": [STK[c][0] for c, _, _ in hs],
                    "ex5": st.mean(h[1] for h in hs), "ex20": st.mean(h[2] for h in hs),
                })
            last = POS[d]

# ---------- 单票事件：共振内 vs 孤立，同一只票 10 日内只算一次 ----------
res_ev, iso_ev = [], []
for theme, days in hits.items():
    for code in [c for c, v in STK.items() if v[1] == theme]:
        last = -999
        for d in sorted(days):
            for c, ex5, ex20 in days[d]:
                if c != code:
                    continue
                if POS[d] - last <= GAP_DAYS:
                    continue
                last = POS[d]
                (res_ev if d in fire_days[theme] else iso_ev).append((d, ex5, ex20))

def summ(xs, label):
    if not xs:
        print(f"  {label:<26} 无样本"); return
    e5 = [x[0] for x in xs]; e20 = [x[1] for x in xs]
    n = len(xs)
    def t(v):
        if len(v) < 3: return float("nan")
        s = st.stdev(v)
        return st.mean(v) / (s / len(v) ** 0.5) if s else float("nan")
    w = sum(1 for v in e20 if v > 0) / n
    print(f"  {label:<26} n={n:>3}  5日超额 均{st.mean(e5):+6.1f}% 中{st.median(e5):+6.1f}%  "
          f"20日超额 均{st.mean(e20):+6.1f}% 中{st.median(e20):+6.1f}%  20日胜率 {w*100:4.0f}%  t={t(e20):+.1f}")

print("=" * 108)
print(f"区间 {sess(DAYS[0])} ~ {sess(DAYS[-1])}（{len(DAYS)} 个交易日，前复权）  "
      f"规则: 量>=1.8x · 站上MA20且上涨 · 5日超额>={RS_GAP}%  题材>={TRIGGER}只=共振")
print("=" * 108)

print("\n【1】共振事件（题材层面，10 日去重）")
summ([(e["ex5"], e["ex20"]) for e in episodes], "全部")
summ([(e["ex5"], e["ex20"]) for e in episodes if not in924(e["d"])], "剔除 924 窗口")

print("\n【2】单票层面：共振中的票 vs 孤立放量的票（同票 10 日去重）")
summ([(a, b) for _, a, b in res_ev], "共振日的满分票")
summ([(a, b) for _, a, b in iso_ev], "孤立满分票（题材没共振）")
summ([(a, b) for d, a, b in res_ev if not in924(d)], "共振日满分票 · 剔除924")
summ([(a, b) for d, a, b in iso_ev if not in924(d)], "孤立满分票 · 剔除924")

print("\n【3】无条件基准：任意一天次日开盘买入")
bx5 = st.mean(b[0] for b in BASE.values()) - idx_u5
bx20 = st.mean(b[1] for b in BASE.values()) - idx_u20
print(f"  25只票平均 5日超额 {bx5:+.1f}%  20日超额 {bx20:+.1f}%   （个股本身在这段时间平均就跑赢指数这么多）")

print("\n【4】分年份（共振事件）")
for y in ["23", "24", "25", "26"]:
    summ([(e["ex5"], e["ex20"]) for e in episodes if e["d"].startswith(y)], f"20{y}年")

print("\n【5】分题材（共振事件）")
for th in NAMES:
    summ([(e["ex5"], e["ex20"]) for e in episodes if e["theme"] == th], NAMES[th])

print("\n【6】逐个共振事件")
print(f"  {'信号日':<12}{'题材':<10}{'满分':>4}  {'5日超额':>8} {'20日超额':>9}  涉及")
for e in sorted(episodes, key=lambda x: x["d"]):
    tag = " ← 924" if in924(e["d"]) else ""
    print(f"  {sess(e['d']):<12}{NAMES[e['theme']]:<10}{e['n']:>3}只  {e['ex5']:+7.1f}% {e['ex20']:+8.1f}%  "
          f"{'、'.join(e['who'])}{tag}")
