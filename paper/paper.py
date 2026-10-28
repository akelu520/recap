#!/usr/bin/env python3
"""
A 股模拟交易引擎 (paper trading) —— 纯标准库，无依赖

设计要点：回测 和 模拟盘 跑同一个 Broker、同一个 Strategy，只换数据源。
这样模拟盘里验证过的行为，和回测、和未来接实盘，是同一套逻辑。

    历史数据 ─┐
              ├─> Engine ─> Strategy ─> Broker(撮合/风控/账务) ─> 日志+状态
    实时行情 ─┘

A 股约束全部内置：T+1、涨跌停不能成交、最小 100 股、佣金/印花税/过户费/滑点。
风控层是代码写死的硬限制，策略改不了。

用法：
    python3 paper.py backtest data.csv       # 用历史数据跑
    python3 paper.py tick 002866 14.20 14.05 # 喂一根实时bar，状态自动续存
    python3 paper.py report                  # 看当前持仓和绩效
"""
from __future__ import annotations
import json, os, sys, math, datetime as dt
from dataclasses import dataclass, field, asdict

STATE_FILE = "paper_state.json"
LOG_FILE = "paper_log.jsonl"


# ============================================================
# 成本与规则参数
# ============================================================
@dataclass
class Rules:
    commission: float = 0.00025     # 佣金 万2.5 双边
    commission_min: float = 5.0     # 最低 5 元
    stamp_tax: float = 0.0005       # 印花税 万5 仅卖出
    transfer_fee: float = 0.00001   # 过户费 双边
    slippage: float = 0.001         # 滑点 千1 双边
    limit_pct: float = 0.095        # 涨跌停判定(主板±10%，留余量)
    lot_size: int = 100             # 最小 100 股

    def buy_fee(self, amount: float) -> float:
        return max(amount * self.commission, self.commission_min) + amount * self.transfer_fee

    def sell_fee(self, amount: float) -> float:
        return (max(amount * self.commission, self.commission_min)
                + amount * self.stamp_tax + amount * self.transfer_fee)


# ============================================================
# 风控层 —— 代码写死，策略碰不到
# ============================================================
@dataclass
class RiskLimits:
    max_position_pct: float = 0.30      # 单票最大占总资产比例
    max_total_pct: float = 0.80         # 总仓位上限(留现金)
    daily_loss_stop: float = 0.05       # 单日亏损达此比例 -> 当日熔断
    max_trades_per_day: int = 5         # 单日最多交易笔数
    min_price: float = 1.0              # 低于此价不碰(退市风险)


class RiskRejection(Exception):
    pass


# ============================================================
# 账户
# ============================================================
@dataclass
class Position:
    symbol: str
    shares: int = 0            # 总持股
    available: int = 0         # 可卖(T+1: 当日买入不可卖)
    cost: float = 0.0          # 持仓成本价
    entry_date: str = ""


@dataclass
class Account:
    cash: float
    initial: float
    positions: dict = field(default_factory=dict)
    day: str = ""
    day_start_equity: float = 0.0
    day_trades: int = 0
    halted_today: bool = False      # 熔断标记

    def equity(self, prices: dict) -> float:
        v = self.cash
        for sym, p in self.positions.items():
            px = prices.get(sym, p.cost)
            v += p.shares * px
        return v


# ============================================================
# Broker —— 撮合 + 风控 + 账务。回测和模拟盘共用这一个。
# ============================================================
class Broker:
    def __init__(self, account: Account, rules: Rules = None, limits: RiskLimits = None):
        self.acct = account
        self.rules = rules or Rules()
        self.limits = limits or RiskLimits()
        self.trades: list[dict] = []
        self.events: list[dict] = []

    # ---------- 每日开盘前调用 ----------
    def new_day(self, date: str, prices: dict):
        a = self.acct
        if a.day != date:
            a.day = date
            a.day_start_equity = a.equity(prices)
            a.day_trades = 0
            a.halted_today = False
            for p in a.positions.values():   # T+1 解冻
                p.available = p.shares

    def _log(self, kind: str, **kw):
        rec = {"ts": dt.datetime.now().isoformat(timespec="seconds"),
               "date": self.acct.day, "kind": kind, **kw}
        self.events.append(rec)
        try:
            with open(LOG_FILE, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ---------- 风控检查 ----------
    def _check_common(self, prices: dict):
        a, L = self.acct, self.limits
        if a.halted_today:
            raise RiskRejection("当日已熔断")
        if a.day_trades >= L.max_trades_per_day:
            raise RiskRejection(f"当日交易已达上限 {L.max_trades_per_day} 笔")
        eq = a.equity(prices)
        if a.day_start_equity > 0:
            dd = eq / a.day_start_equity - 1
            if dd <= -L.daily_loss_stop:
                a.halted_today = True
                self._log("CIRCUIT_BREAK", drawdown=round(dd, 4))
                raise RiskRejection(f"单日亏损 {dd*100:.1f}% 触发熔断，当日停止交易")

    # ---------- 买 ----------
    def buy(self, symbol, price, prev_close, date, reason=""):
        """price: 本次拟成交价(开盘价/现价)。prev_close: 判涨停用。"""
        a, R, L = self.acct, self.rules, self.limits
        try:
            self._check_common({symbol: price})
            if price < L.min_price:
                raise RiskRejection(f"价格 {price} 低于下限 {L.min_price}")
            if prev_close and price >= prev_close * (1 + R.limit_pct):
                raise RiskRejection("一字涨停，无法买入")

            eq = a.equity({symbol: price})
            held = a.positions.get(symbol)
            held_val = held.shares * price if held else 0.0
            # 单票上限
            room_sym = eq * L.max_position_pct - held_val
            # 总仓位上限
            invested = sum(p.shares * price for p in a.positions.values())
            room_tot = eq * L.max_total_pct - invested
            budget = min(a.cash, room_sym, room_tot)
            if budget <= 0:
                raise RiskRejection("仓位上限，无可用额度")

            px = price * (1 + R.slippage)
            lots = int(budget // (px * R.lot_size))
            if lots <= 0:
                raise RiskRejection(f"可用额度 {budget:.0f} 不足 1 手")
            shares = lots * R.lot_size
            amount = shares * px
            fee = R.buy_fee(amount)
            if amount + fee > a.cash:
                shares -= R.lot_size
                if shares <= 0:
                    raise RiskRejection("现金不足")
                amount = shares * px
                fee = R.buy_fee(amount)

            a.cash -= amount + fee
            p = a.positions.get(symbol) or Position(symbol)
            total_cost = p.cost * p.shares + amount + fee
            p.shares += shares
            p.cost = total_cost / p.shares
            p.available += 0            # T+1：今日买入不可卖
            p.entry_date = date
            a.positions[symbol] = p
            a.day_trades += 1
            self._log("BUY", symbol=symbol, shares=shares, price=round(px, 3),
                      fee=round(fee, 2), cash=round(a.cash, 2), reason=reason)
            return {"ok": True, "shares": shares, "price": px, "fee": fee}
        except RiskRejection as e:
            self._log("REJECT_BUY", symbol=symbol, why=str(e), reason=reason)
            return {"ok": False, "why": str(e)}

    # ---------- 卖 ----------
    def sell(self, symbol, price, prev_close, date, reason=""):
        a, R = self.acct, self.rules
        try:
            self._check_common({symbol: price})
            p = a.positions.get(symbol)
            if not p or p.shares <= 0:
                raise RiskRejection("无持仓")
            if p.available <= 0:
                raise RiskRejection("T+1：当日买入股票不可卖出")
            if prev_close and price <= prev_close * (1 - R.limit_pct):
                raise RiskRejection("一字跌停，无法卖出")

            px = price * (1 - R.slippage)
            shares = p.available
            amount = shares * px
            fee = R.sell_fee(amount)
            a.cash += amount - fee
            pnl = (px - p.cost) * shares - fee
            ret = px / p.cost - 1 if p.cost else 0.0
            self.trades.append({"symbol": symbol, "in": p.entry_date, "out": date,
                                "in_px": round(p.cost, 3), "out_px": round(px, 3),
                                "shares": shares, "pnl": round(pnl, 2),
                                "ret": round(ret, 4), "reason": reason})
            p.shares -= shares
            p.available = 0
            if p.shares == 0:
                del a.positions[symbol]
            a.day_trades += 1
            self._log("SELL", symbol=symbol, shares=shares, price=round(px, 3),
                      fee=round(fee, 2), pnl=round(pnl, 2), ret=round(ret, 4),
                      cash=round(a.cash, 2), reason=reason)
            return {"ok": True, "shares": shares, "price": px, "pnl": pnl}
        except RiskRejection as e:
            self._log("REJECT_SELL", symbol=symbol, why=str(e), reason=reason)
            return {"ok": False, "why": str(e)}

    # ---------- kill switch ----------
    def liquidate_all(self, prices: dict, date: str):
        self.acct.halted_today = False
        self.acct.day_trades = 0
        for sym in list(self.acct.positions):
            p = self.acct.positions[sym]
            p.available = p.shares
            self.sell(sym, prices.get(sym, p.cost), None, date, reason="KILL_SWITCH")
        self.acct.halted_today = True
        self._log("KILL_SWITCH")


# ============================================================
# 策略接口 —— 回测和模拟盘用同一个
# ============================================================
class Strategy:
    name = "base"

    def on_bar(self, broker: Broker, bar: dict, hist: list):
        """bar: {date, symbol, open, close, prev_close}；hist: 过往 close 列表"""
        raise NotImplementedError


def _ema(xs, n):
    k = 2.0 / (n + 1)
    out = [xs[0]]
    for x in xs[1:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


class MacdStrategy(Strategy):
    """MACD 金叉买 / 死叉卖。信号由昨日收盘产生，今日开盘执行 —— 无未来函数。"""
    name = "macd_12_26_9"

    def __init__(self, fast=12, slow=26, signal=9, warmup=60):
        self.fast, self.slow, self.sig, self.warmup = fast, slow, signal, warmup
        self.pending = None

    def on_bar(self, broker, bar, hist):
        sym = bar["symbol"]
        # --- 先执行昨日挂单 ---
        if self.pending == "BUY":
            broker.buy(sym, bar["open"], bar["prev_close"], bar["date"], "MACD金叉")
            self.pending = None
        elif self.pending == "SELL":
            r = broker.sell(sym, bar["open"], bar["prev_close"], bar["date"], "MACD死叉")
            if r["ok"] or "T+1" not in r.get("why", ""):
                self.pending = None     # T+1 被拒则顺延到明天

        # --- 今日收盘后产生信号 ---
        closes = hist + [bar["close"]]
        if len(closes) <= self.warmup:
            return
        dif = [a - b for a, b in zip(_ema(closes, self.fast), _ema(closes, self.slow))]
        dea = _ema(dif, self.sig)
        gold = dif[-1] > dea[-1] and dif[-2] <= dea[-2]
        dead = dif[-1] < dea[-1] and dif[-2] >= dea[-2]
        holding = sym in broker.acct.positions
        if gold and not holding:
            self.pending = "BUY"
        elif dead and holding:
            self.pending = "SELL"


# ============================================================
# 状态持久化 —— 模拟盘要能跨天续跑
# ============================================================
def save_state(broker: Broker, strat: Strategy):
    a = broker.acct
    data = {"cash": a.cash, "initial": a.initial, "day": a.day,
            "day_start_equity": a.day_start_equity, "day_trades": a.day_trades,
            "halted_today": a.halted_today,
            "positions": {k: asdict(v) for k, v in a.positions.items()},
            "trades": broker.trades,
            "strategy": strat.name,
            "pending": getattr(strat, "pending", None)}
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def load_state(initial=100000.0):
    if not os.path.exists(STATE_FILE):
        return Account(cash=initial, initial=initial), []
    d = json.load(open(STATE_FILE))
    a = Account(cash=d["cash"], initial=d["initial"], day=d["day"],
                day_start_equity=d["day_start_equity"], day_trades=d["day_trades"],
                halted_today=d["halted_today"])
    a.positions = {k: Position(**v) for k, v in d["positions"].items()}
    return a, d.get("trades", [])


# ============================================================
# 绩效
# ============================================================
def report(broker: Broker, prices: dict, equity_curve=None):
    a = broker.acct
    eq = a.equity(prices)
    tot = eq / a.initial - 1
    ts = broker.trades
    wins = [t for t in ts if t["ret"] > 0]
    print("=" * 56)
    print(f"总资产   {eq:,.0f} 元   (起始 {a.initial:,.0f})")
    print(f"总收益   {tot*100:+.2f}%      现金 {a.cash:,.0f}")
    if ts:
        wr = len(wins) / len(ts)
        aw = sum(t['ret'] for t in wins) / len(wins) if wins else 0
        ls = [t for t in ts if t["ret"] <= 0]
        al = sum(t['ret'] for t in ls) / len(ls) if ls else 0
        gp, gl = sum(t['ret'] for t in wins), -sum(t['ret'] for t in ls)
        print(f"交易     {len(ts)} 笔    胜率 {wr*100:.1f}%    "
              f"平均盈 {aw*100:+.2f}% / 平均亏 {al*100:+.2f}%    "
              f"PF {gp/gl if gl else float('inf'):.2f}")
    if equity_curve:
        peak, dd = -1e18, 0.0
        for v in equity_curve:
            peak = max(peak, v); dd = min(dd, v / peak - 1)
        print(f"最大回撤 {dd*100:.1f}%")
    if a.positions:
        print("-" * 56)
        print(f"{'代码':<12}{'股数':>8}{'成本':>9}{'现价':>9}{'浮盈':>10}")
        for s, p in a.positions.items():
            px = prices.get(s, p.cost)
            print(f"{s:<12}{p.shares:>8}{p.cost:>9.3f}{px:>9.3f}"
                  f"{(px/p.cost-1)*100:>9.1f}%")
    print("=" * 56)


# ============================================================
# 驱动
# ============================================================
def run_backtest(csv_path, symbol="SIM", initial=100000.0):
    rows = []
    for rec in open(csv_path).read().strip().split(";"):
        if rec.strip():
            d, o, c = rec.strip().split(",")
            rows.append((d, float(o), float(c)))

    broker = Broker(Account(cash=initial, initial=initial))
    strat = MacdStrategy()
    hist, curve = [], []
    for i, (d, o, c) in enumerate(rows):
        prev_close = rows[i - 1][2] if i else None
        broker.new_day(d, {symbol: prev_close or o})
        strat.on_bar(broker, {"date": d, "symbol": symbol, "open": o,
                              "close": c, "prev_close": prev_close}, hist)
        hist.append(c)
        curve.append(broker.acct.equity({symbol: c}))
    # 收盘强平
    if symbol in broker.acct.positions:
        broker.acct.positions[symbol].available = broker.acct.positions[symbol].shares
        broker.acct.halted_today = False
        broker.sell(symbol, rows[-1][2], None, rows[-1][0], "END")
    report(broker, {symbol: rows[-1][2]}, curve)
    return broker


def run_tick(symbol, open_px, close_px, prev_close=None, date=None):
    """喂一根实时 bar。状态自动续存，适合每天收盘后跑一次。"""
    date = date or dt.date.today().isoformat()
    acct, old = load_state()
    broker = Broker(acct)
    broker.trades = old
    strat = MacdStrategy()
    hist = []           # 生产环境这里应从本地行情库读历史收盘
    broker.new_day(date, {symbol: prev_close or open_px})
    strat.on_bar(broker, {"date": date, "symbol": symbol, "open": open_px,
                          "close": close_px, "prev_close": prev_close}, hist)
    save_state(broker, strat)
    report(broker, {symbol: close_px})


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "backtest":
        run_backtest(sys.argv[2], symbol=sys.argv[3] if len(sys.argv) > 3 else "SIM")
    elif cmd == "tick":
        run_tick(sys.argv[2], float(sys.argv[3]), float(sys.argv[4]),
                 float(sys.argv[5]) if len(sys.argv) > 5 else None)
    elif cmd == "report":
        acct, old = load_state()
        b = Broker(acct); b.trades = old
        report(b, {})
    else:
        print(__doc__)
