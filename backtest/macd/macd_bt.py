#!/usr/bin/env python3
"""
MACD 金叉/死叉策略回测 —— 传艺科技 002866.SZ
诚实版：含手续费、印花税、滑点、T+1、涨跌停无法成交，信号次日开盘执行（无未来函数）。
"""
import sys, datetime as dt

PATH = sys.argv[1] if len(sys.argv) > 1 else "data_002866.csv"

# ---------- 读数据 ----------
rows = []
with open(PATH) as f:
    raw = f.read().strip()
for rec in raw.split(";"):
    rec = rec.strip()
    if not rec:
        continue
    d, o, c = rec.split(",")
    rows.append((d, float(o), float(c)))

dates = [r[0] for r in rows]
opens = [r[1] for r in rows]
closes = [r[2] for r in rows]
N = len(rows)

# ---------- MACD(12,26,9) ----------
def ema(series, n):
    k = 2.0 / (n + 1)
    out = [series[0]]
    for x in series[1:]:
        out.append(x * k + out[-1] * (1 - k))
    return out

ema12 = ema(closes, 12)
ema26 = ema(closes, 26)
dif = [a - b for a, b in zip(ema12, ema26)]
dea = ema(dif, 9)

WARMUP = 60  # 跳过 EMA 预热期，避免初始化偏差

# ---------- 成本参数 ----------
COMM      = 0.00025   # 佣金 万分之2.5，双边
COMM_MIN  = 5.0       # 最低佣金 5 元
STAMP     = 0.0005    # 印花税 万分之5，仅卖出
TRANSFER  = 0.00001   # 过户费 十万分之1，双边
SLIP      = 0.001     # 滑点 千分之1，双边
LIMIT     = 0.095     # 深主板涨跌停 ±10%，留点余量判定封板

CAPITAL0 = 100000.0

def buy_cost(amount):
    return max(amount * COMM, COMM_MIN) + amount * TRANSFER

def sell_cost(amount):
    return max(amount * COMM, COMM_MIN) + amount * STAMP + amount * TRANSFER


def run(with_costs=True):
    cash = CAPITAL0
    shares = 0
    entry_px = None
    entry_date = None
    trades = []
    equity = []          # (date, equity)
    pending = None       # "BUY" / "SELL"

    for i in range(N):
        # --- 先按昨日收盘产生的挂单，在今日开盘尝试成交 ---
        if pending and i > 0:
            op = opens[i]
            prev_c = closes[i - 1]
            limit_up   = op >= prev_c * (1 + LIMIT)
            limit_down = op <= prev_c * (1 - LIMIT)

            if pending == "BUY" and shares == 0:
                if limit_up:
                    pass                       # 一字涨停买不进，挂单顺延
                else:
                    px = op * (1 + SLIP) if with_costs else op
                    lots = int(cash // (px * 100))
                    if lots > 0:
                        amt = lots * 100 * px
                        fee = buy_cost(amt) if with_costs else 0.0
                        if amt + fee <= cash:
                            cash -= amt + fee
                            shares = lots * 100
                            entry_px = px
                            entry_date = dates[i]
                    pending = None

            elif pending == "SELL" and shares > 0:
                if limit_down:
                    pass                       # 一字跌停卖不掉，挂单顺延
                else:
                    px = op * (1 - SLIP) if with_costs else op
                    amt = shares * px
                    fee = sell_cost(amt) if with_costs else 0.0
                    cash += amt - fee
                    ret = px / entry_px - 1.0
                    trades.append({
                        "in": entry_date, "out": dates[i],
                        "in_px": entry_px, "out_px": px, "ret": ret,
                    })
                    shares = 0
                    entry_px = None
                    pending = None
            else:
                pending = None

        # --- 今日收盘后判断信号，挂到明日开盘 ---
        if i >= WARMUP and i >= 1:
            gold = dif[i] > dea[i] and dif[i - 1] <= dea[i - 1]
            dead = dif[i] < dea[i] and dif[i - 1] >= dea[i - 1]
            if gold and shares == 0:
                pending = "BUY"
            elif dead and shares > 0:
                pending = "SELL"

        equity.append((dates[i], cash + shares * closes[i]))

    # 末日强制平仓
    if shares > 0:
        px = closes[-1] * (1 - SLIP) if with_costs else closes[-1]
        amt = shares * px
        fee = sell_cost(amt) if with_costs else 0.0
        cash += amt - fee
        trades.append({"in": entry_date, "out": dates[-1] + "(强平)",
                       "in_px": entry_px, "out_px": px,
                       "ret": px / entry_px - 1.0})
        shares = 0
        equity[-1] = (dates[-1], cash)

    return cash, trades, equity


def max_dd(eq):
    peak = -1e18
    worst = 0.0
    for _, v in eq:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1.0)
    return worst


def summarize(name, final, trades, equity, years):
    tot = final / CAPITAL0 - 1.0
    cagr = (final / CAPITAL0) ** (1 / years) - 1.0
    wins = [t for t in trades if t["ret"] > 0]
    losses = [t for t in trades if t["ret"] <= 0]
    wr = len(wins) / len(trades) if trades else 0.0
    aw = sum(t["ret"] for t in wins) / len(wins) if wins else 0.0
    al = sum(t["ret"] for t in losses) / len(losses) if losses else 0.0
    gp = sum(t["ret"] for t in wins)
    gl = -sum(t["ret"] for t in losses)
    pf = gp / gl if gl > 0 else float("inf")
    print(f"\n===== {name} =====")
    print(f"期末资金      {final:,.0f} 元   (起始 {CAPITAL0:,.0f})")
    print(f"总收益率      {tot*100:+.1f}%")
    print(f"年化          {cagr*100:+.1f}%")
    print(f"最大回撤      {max_dd(equity)*100:.1f}%")
    print(f"交易次数      {len(trades)} 笔   (平均每年 {len(trades)/years:.1f} 笔)")
    print(f"胜率          {wr*100:.1f}%   ({len(wins)} 胜 / {len(losses)} 负)")
    print(f"平均盈利      {aw*100:+.2f}%")
    print(f"平均亏损      {al*100:+.2f}%")
    print(f"盈亏比(PF)    {pf:.2f}")
    return tot, cagr


days = (dt.date.fromisoformat(dates[-1]) - dt.date.fromisoformat(dates[0])).days
years = days / 365.25

print("=" * 62)
print("传艺科技 002866.SZ  MACD(12,26,9) 金叉买 / 死叉卖")
print(f"区间 {dates[0]} → {dates[-1]}   共 {N} 个交易日 ({years:.2f} 年)")
print("执行假设：信号当日收盘产生 → 次日开盘成交；一字涨停不能买、一字跌停不能卖")
print(f"成本：佣金{COMM*1e4:.1f}‱(双边,最低{COMM_MIN:.0f}元) + 印花税{STAMP*1e4:.0f}‱(卖出)"
      f" + 过户费{TRANSFER*1e5:.0f}/10万 + 滑点{SLIP*1e3:.0f}‰(双边)")
print("=" * 62)

f_c, t_c, e_c = run(with_costs=True)
f_n, t_n, e_n = run(with_costs=False)

summarize("策略（含全部成本）", f_c, t_c, e_c, years)
summarize("策略（零成本理想态）", f_n, t_n, e_n, years)

# ---------- 买入持有基准 ----------
bh_entry = opens[WARMUP + 1]
bh_shares = int(CAPITAL0 // (bh_entry * 100)) * 100
bh_cash = CAPITAL0 - bh_shares * bh_entry - buy_cost(bh_shares * bh_entry)
bh_eq = [(dates[i], bh_cash + bh_shares * closes[i]) for i in range(WARMUP + 1, N)]
bh_final = bh_cash + bh_shares * closes[-1] - sell_cost(bh_shares * closes[-1])
bh_tot = bh_final / CAPITAL0 - 1
bh_cagr = (bh_final / CAPITAL0) ** (1 / years) - 1
print("\n===== 买入持有（同期，含买卖成本） =====")
print(f"期末资金      {bh_final:,.0f} 元")
print(f"总收益率      {bh_tot*100:+.1f}%")
print(f"年化          {bh_cagr*100:+.1f}%")
print(f"最大回撤      {max_dd(bh_eq)*100:.1f}%")

# ---------- 成本影响 ----------
print("\n===== 成本吃掉了多少 =====")
print(f"零成本总收益  {(f_n/CAPITAL0-1)*100:+.1f}%")
print(f"含成本总收益  {(f_c/CAPITAL0-1)*100:+.1f}%")
print(f"差额          {((f_n-f_c)/CAPITAL0)*100:.1f} 个百分点 "
      f"({f_n-f_c:,.0f} 元，{len(t_c)} 笔交易)")

# ---------- 逐笔明细 ----------
print("\n===== 逐笔交易（含成本） =====")
print(f"{'买入日':<12}{'卖出日':<16}{'买价':>8}{'卖价':>8}{'收益':>9}")
for t in t_c:
    print(f"{t['in']:<12}{t['out']:<16}{t['in_px']:>8.2f}{t['out_px']:>8.2f}{t['ret']*100:>8.1f}%")

# 连续亏损
streak = cur = 0
for t in t_c:
    cur = cur + 1 if t["ret"] <= 0 else 0
    streak = max(streak, cur)
print(f"\n最长连续亏损  {streak} 笔")
