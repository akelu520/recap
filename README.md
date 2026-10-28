# recap

A 股收盘复盘工具：题材轮动雷达、规则回测、模拟盘。纯 Python 标准库，无第三方依赖。

> 不构成投资建议。题材雷达的「共振」规则经三年回测**没有可靠优势**（见下文），只能当作值得看一眼的线索，不是买入信号。

## 目录

| 目录 | 内容 |
|---|---|
| `radar/` | 题材轮动雷达：快照计算、每晚更新脚本、页面模板 |
| `backtest/resonance/` | 雷达共振规则三年回测（2023-10 ~ 2026-09） |
| `backtest/macd/` | 传艺科技 002866 MACD 金叉/死叉回测 |
| `paper/` | 模拟盘引擎（T+1、涨跌停、成本、写死的风控） |

所有脚本都从**当前目录**读写文件，运行前先 `cd` 到脚本所在目录。

## radar/ · 题材轮动雷达

线上页面：https://claude.ai/artifact/DXCyWCxk2vJ33ro3FjXEx5 （仅本人可见）。每个工作日北京时间 16:30 由云端定时任务更新数据。

**规则**：个股每天 0–3 分，三条各 1 分——

1. 成交量 ≥ 前 20 日均量 × 1.8
2. 当日上涨且收盘站上 20 日均线
3. 5 日涨幅 − 深证成指 5 日涨幅 ≥ 3 个百分点

题材内满分票 ≥ 3 只即「共振」。参数在 `config.json` 的 `rules`。

| 文件 | 作用 |
|---|---|
| `compute.py` | 内嵌行情数据，生成 `snapshot.json` 和 `config.json`（初版快照） |
| `nightly.py` | 定时任务用：读 `config.json` + `raw.json`（Longbridge 行情）→ `snapshot.json` |
| `radar_tpl.html` | 页面模板，`/*SEED*/null/*END*/` 处填快照 |
| `radar.html` | 已填入 2026-10-28 快照的页面 |
| `rotation.py` + `basket.txt` | 第一版探测器，只有灵巧手 5 只票 |

```bash
cd radar && python3 compute.py      # 重新生成 snapshot.json
cd radar && python3 rotation.py     # 灵巧手单题材复盘
```

## backtest/resonance/ · 共振规则回测

```bash
cd backtest/resonance && python3 validate.py   # 输出应与 report.txt 一致
```

710 个交易日，前复权，信号次日开盘买入，对比深证成指算超额：

| | 20 日超额均值 | 胜率 |
|---|---|---|
| 共振 30 次 | +3.2% | 57% |
| 同一批票任意一天买入（基准） | +3.0% | — |
| 共振日的满分票 | +2.3% | 46% |
| 孤立放量的满分票 | +4.9% | 51% |

分年份 2024 +6.4% → 2025 −1.7% → 2026 −2.4%。偏差都偏乐观（票池按今天的热度挑、一字板买不进、没算成本）。**结论：没有可靠优势。**

## backtest/macd/ · MACD 回测

```bash
cd backtest/macd && python3 macd_bt.py [data.csv]   # 默认 data_002866.csv
```

传艺科技 2022-09 ~ 2026-10，含真实成本、T+1、涨跌停：满仓 −47.9%，36 笔交易胜率 33.3%，盈亏比 0.69。零成本也亏 −42.8%，亏损来自信号本身。

## paper/ · 模拟盘

```bash
python3 paper/paper.py backtest <csv> <code>
python3 paper/paper.py tick <code> <open> <close> [prev]
python3 paper/paper.py report
```

风控写死：单票 ≤30%、总仓位 ≤80%、单日亏 5% 熔断、单日 ≤5 笔、价格 <1 元不碰，另有一键清仓。同样的 MACD 信号加上 30% 单票上限后，亏损从 −47.9% 降到 −15.3%。状态写在当前目录的 `paper_state.json` / `paper_log.jsonl`（已 gitignore）。

## 数据来源

行情来自 Longbridge（`candlesticks`，算跨月收益要 `forward_adjust: true`）。K 线时间戳是 T16:00Z，交易日 = 标签日期 + 1 天。Longbridge 每秒有调用上限，并发不要超过 3 个。
