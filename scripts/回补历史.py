# -*- coding: utf-8 -*-
"""
红利工作台 · 历史收盘价一次性回补

用途：把股票池里每只标的**过去 N 个交易日**的真实收盘价补进「历史收盘价.csv」。

为什么需要它：
    收盘快照.py 只抓「当下这一刻」的行情，错过一天就永久缺一天。历史窗口
    （历史收盘价、布林、Power BI 曲线）如果从今天才开始攒，要等一年才有一年。

口径（与 收盘快照.py 写入的行完全一致）：
    · 不复权真实成交价（fqt=0）—— 和券商 App 显示的价格、和已有记录对得上
    · 涨跌幅% 由「今日收盘 / 上一交易日收盘」自算，不是接口给的复权涨跌幅
    · 同一 (日期, 代码) 只保留一条：已存在的旧记录**不覆盖**，
      只补「历史里缺的日期」，避免把原先真实抓取的盘中值改成回补值

无第三方依赖，只用标准库。

用法：
    python backfill_history.py                # 默认回补近 1 年
    python backfill_history.py --years 2      # 回补近 2 年
    python backfill_history.py --dry-run      # 只统计会补多少行，不落盘
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

try:  # Windows 控制台默认 GBK，中文会炸
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.abspath(os.environ.get("HL_ROOT") or os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(HERE, "红利买点工作台.html")
if not os.path.exists(HTML):
    HTML = os.path.join(HERE, "index.html")
CSV_HIST = os.path.join(HERE, "历史收盘价.csv")
LOG = os.path.join(HERE, "快照运行日志.txt")

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
HEADER = ["日期", "代码", "名称", "市场", "板块", "收盘价", "昨收", "涨跌幅%", "行情时间", "抓取时间"]


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def read_pool():
    """从 html 里读股票池（单一数据源，与 收盘快照.py 同一套正则）"""
    src = open(HTML, encoding="utf-8").read()
    blk = re.search(r"var SEED = \[(.*?)\n\];", src, re.S)
    if not blk:
        raise RuntimeError("没能在 html 里找到 SEED 股票池")
    rows = re.findall(
        r'\["([0-9]{5,6})","([^"]+)","(sh|sz|hk)","([^"]+)",([0-9.]+),"([A-Z_]+)"\]',
        blk.group(1))
    return [{"code": r[0], "name": r[1], "market": r[2], "sector": r[3],
             "dps": float(r[4]), "tax": r[5]} for r in rows]


def secid(s):
    """东财证券 id：沪市 1. / 深市 0. / 港股 116."""
    if s["market"] == "hk":
        return "116." + s["code"]
    return ("1." if s["market"] == "sh" else "0.") + s["code"]


def fetch_kline(s, lmt):
    """东财日 K，**不复权**（fqt=0），返回 [(日期, 收盘, 昨收), ...] 按日期升序

    只请求 f51(日期) / f53(收盘)，够用且响应小。
    东财对密集请求会直接断连，这里失败即返回空，由上层按轮次补抓。
    """
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
           "?secid=%s&klt=101&fqt=0&lmt=%d&end=20500101"
           "&fields1=f1&fields2=f51,f53" % (secid(s), lmt))
    try:
        req = urllib.request.Request(url, headers=UA)
        d = json.loads(urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore"))
        kl = (d.get("data") or {}).get("klines") or []
    except Exception:
        return []
    out = []
    for row in kl:
        parts = str(row).split(",")
        if len(parts) < 2:
            continue
        try:
            c = float(parts[1])
        except Exception:
            continue
        if c > 0:
            out.append((parts[0], c))
    return out


def _is_intraday(row, day):
    """判断这条记录是不是「盘中价」（而不是收盘价）

    两种特征：
      · 行情时间为 A股 YYYYMMDDhhmmss / 港股 YYYY/MM/DD hh:mm:ss，且时刻早于 15:00
      · 行情时间列被写成「日K收盘」，那是收盘价，不算盘中
    """
    qt = str(row[8] if len(row) > 8 else "")
    if qt == "日K收盘":
        return False
    num = re.sub(r"[^0-9]", "", qt)
    if len(num) >= 8 and num[:8] == day.replace("-", ""):
        if len(num) >= 12:
            hh = int(num[8:10])
            mm = int(num[10:12])
            return hh * 60 + mm < 15 * 60
        return False
    return False


def read_history():
    """读已有历史，返回 {(日期, 代码): row}"""
    rows = {}
    if not os.path.exists(CSV_HIST):
        return rows
    with open(CSV_HIST, encoding="utf-8-sig", newline="") as f:
        rd = csv.reader(f)
        next(rd, None)
        for r in rd:
            if len(r) >= 10:
                rows[(r[0], r[1])] = r
    return rows


def write_history(rows):
    """原子写回（先写 .tmp 再 replace，中途崩溃不会毁掉原文件）"""
    tmp = CSV_HIST + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for k in sorted(rows.keys()):
            w.writerow(rows[k])
    os.replace(tmp, CSV_HIST)


def main():
    ap = argparse.ArgumentParser(description="回补红利工作台的历史收盘价")
    ap.add_argument("--years", type=float, default=1.0, help="回补几年（默认 1）")
    ap.add_argument("--conc", type=int, default=4, help="并发数（默认 4，6 会被东财限流）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不落盘")
    ap.add_argument("--fix-intraday", action="store_true",
                    help="用日K收盘价覆盖历史上的盘中价记录（行情时间 <15:00 或标为盘中快照的）")
    args = ap.parse_args()

    lmt = int(args.years * 250) + 30          # 一年约 250 个交易日，多取 30 根冗余
    log("=== 历史回补开始（近 %.1f 年 / 约 %d 根日K / 不复权）===" % (args.years, lmt))

    if not os.path.exists(HTML):
        log("错误：找不到 %s" % HTML)
        return 1

    pool = read_pool()
    log("股票池 %d 只（A股 %d / 港股 %d）"
        % (len(pool), sum(1 for s in pool if s["market"] != "hk"),
           sum(1 for s in pool if s["market"] == "hk")))

    before = read_history()
    log("已有历史 %d 条，覆盖 %d 个交易日" % (len(before), len({k[0] for k in before})))

    # 抓取：4 并发 + 最多 3 轮补抓（东财会随机断连，隔一下再试通常就通）
    got, todo = {}, list(pool)
    for attempt in range(3):
        if not todo:
            break
        if attempt:
            time.sleep(1.0 * attempt)
        with ThreadPoolExecutor(max_workers=args.conc) as ex:
            res = list(zip(todo, ex.map(lambda x: fetch_kline(x, lmt), todo)))
        nxt = []
        for s, kl in res:
            if kl:
                got[s["code"]] = (s, kl)
            else:
                nxt.append(s)
        todo = nxt
        log("  第 %d 轮：成功 %d 只，仍缺 %d 只" % (attempt + 1, len(got), len(todo)))

    if todo:
        log("  ⚠ 最终没抓到的 %d 只：%s" % (len(todo), ", ".join(x["name"] for x in todo)))

    # 合并：默认只补缺的日期，不覆盖已有记录（已有的是当天真实抓取的盘中值）
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = dict(before)
    added = 0
    kept = 0
    fixed = 0
    for code, (s, kl) in got.items():
        prev = None
        for day, close in kl:
            chg = (close / prev - 1) * 100 if prev else 0.0
            key = (day, code)
            if key in rows:
                old = rows[key]
                # --fix-intraday：把历史上的盘中价换成日K收盘价
                # 今天不动：盘中运行留下的当日记录，等今天收盘后自然会由快照脚本覆盖
                if args.fix_intraday and day != datetime.now().strftime("%Y-%m-%d") and _is_intraday(old, day):
                    try:
                        oldc = float(old[5])
                    except Exception:
                        oldc = None
                    if oldc is None or abs(oldc - close) > 1e-6:
                        rows[key] = [day, code, s["name"], s["market"], s["sector"],
                                     "%.3f" % close, ("%.3f" % prev) if prev else "",
                                     ("%.2f" % chg) if prev else "", "日K收盘", now]
                        fixed += 1
                        prev = close
                        continue
                kept += 1
                prev = close
                continue
            rows[key] = [day, code, s["name"], s["market"], s["sector"],
                         "%.3f" % close, ("%.3f" % prev) if prev else "",
                         ("%.2f" % chg) if prev else "", "日K收盘",
                         now]
            added += 1
            prev = close

    days_all = sorted({k[0] for k in rows})
    log("合并完成：新增 %d 条，保留原有 %d 条，修正盘中价 %d 条，共 %d 条 / %d 个交易日"
        % (added, kept, fixed, len(rows), len(days_all)))
    if days_all:
        log("  日期范围：%s ~ %s" % (days_all[0], days_all[-1]))

    # 完整性检查：每只票应有的天数 vs 实际
    per = {}
    for (d_, c_) in rows:
        per[c_] = per.get(c_, 0) + 1
    short = [(code, n) for code, n in per.items() if n < len(days_all) * 0.8]
    if short:
        log("  ⚠ 覆盖不足（<80%% 交易日）的标的 %d 只：%s"
            % (len(short), ", ".join("%s(%d天)" % x for x in short[:10])))

    if args.dry_run:
        log("--dry-run：不写入文件")
        log("=== 历史回补结束（演练）===")
        return 0

    if added == 0 and fixed == 0 and os.path.exists(CSV_HIST):
        log("没有需要补/修的数据，文件未改动")
        log("=== 历史回补结束 ===")
        return 0

    write_history(rows)
    log("已写入 %s" % os.path.basename(CSV_HIST))
    log("=== 历史回补结束 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
