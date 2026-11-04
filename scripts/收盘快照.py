# -*- coding: utf-8 -*-
"""
红利工作台 · 收盘快照
用途：每个交易日 16:00 自动运行，做两件事
  1. 抓池内全部标的收盘价，追加到「历史收盘价.csv」（同一日期同一代码只保留一条）
  2. 查分红公告，与页面内置的分红额比对，有差异写入「分红差异-YYYYMMDD.txt」

无第三方依赖，只用标准库。由 Windows 计划任务调用，也可手动双击运行。
股票池从 红利买点工作台.html 里的 SEED 读取，改池子不用改本脚本。
"""
import csv
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

HERE = os.path.abspath(os.environ.get("HL_ROOT") or os.path.dirname(os.path.abspath(__file__)))
# 本地目录里叫「红利买点工作台.html」，推到 git 上的仓库里叫 index.html，两个都认
HTML = os.path.join(HERE, "红利买点工作台.html")
if not os.path.exists(HTML):
    HTML = os.path.join(HERE, "index.html")
CSV_HIST = os.path.join(HERE, "历史收盘价.csv")
BOLL_JSON = os.path.join(HERE, "boll.json")
LOG = os.path.join(HERE, "快照运行日志.txt")

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/",
}


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def read_pool():
    """从 html 里读出股票池（单一数据源）"""
    src = open(HTML, encoding="utf-8").read()
    blk = re.search(r"var SEED = \[(.*?)\n\];", src, re.S)
    if not blk:
        raise RuntimeError("没能在 html 里找到 SEED 股票池")
    rows = re.findall(
        r'\["([0-9]{5,6})","([^"]+)","(sh|sz|hk)","([^"]+)",([0-9.]+),"([A-Z]+)"\]',
        blk.group(1))
    return [{"code": r[0], "name": r[1], "market": r[2], "sector": r[3],
             "dps": float(r[4]), "tax": r[5]} for r in rows]


def qcode(s):
    return ("hk" + s["code"]) if s["market"] == "hk" else (s["market"] + s["code"])


def fetch_quotes(pool):
    """腾讯行情，20 只一批"""
    codes = [qcode(s) for s in pool]
    px = {}
    for i in range(0, len(codes), 20):
        batch = codes[i:i + 20]
        url = "https://qt.gtimg.cn/q=" + ",".join(batch)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA["User-Agent"]})
            raw = urllib.request.urlopen(req, timeout=25).read().decode("gbk", "ignore")
        except Exception as e:
            log("  行情批次失败(%d): %s" % (i // 20 + 1, e))
            continue
        for line in raw.split(";"):
            m = re.match(r'v_([a-z]{2}[0-9]+)="([^"]*)"', line.strip())
            if not m:
                continue
            a = m.group(2).split("~")
            try:
                px[m.group(1)] = {"p": float(a[3]), "prev": float(a[4]),
                                  "time": a[30] if len(a) > 30 else ""}
            except Exception:
                pass
        time.sleep(0.2)
    return px


def write_history(pool, px, day):
    """按 (日期, 代码) 去重写入，已有当天记录则更新"""
    rows = {}
    header = ["日期", "代码", "名称", "市场", "板块", "收盘价", "昨收", "涨跌幅%", "行情时间", "抓取时间"]
    if os.path.exists(CSV_HIST):
        try:
            with open(CSV_HIST, encoding="utf-8-sig", newline="") as f:
                rd = csv.reader(f)
                next(rd, None)
                for r in rd:
                    if len(r) >= 10:
                        rows[(r[0], r[1])] = r
        except Exception as e:
            log("  读取历史 CSV 失败（将新建）: %s" % e)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today_key = day.replace("-", "")
    got = 0
    stale = 0
    for s in pool:
        q = px.get(qcode(s))
        if not q:
            continue
        # 行情时间 A股是「YYYYMMDDHHMMSS」，港股是「YYYY/MM/DD HH:MM:SS」，
        # 去掉分隔符后取前 8 位统一比对。日期不是今天说明休市 / 停牌，
        # 记进去只会污染历史曲线，直接跳过
        qt = str(q.get("time") or "")
        qt_norm = re.sub(r"[^0-9]", "", qt)
        if len(qt_norm) >= 8 and qt_norm[:8] != today_key:
            stale += 1
            continue
        chg = (q["p"] / q["prev"] - 1) * 100 if q["prev"] else 0.0
        rows[(day, s["code"])] = [day, s["code"], s["name"], s["market"], s["sector"],
                                 "%.3f" % q["p"], "%.3f" % q["prev"], "%.2f" % chg,
                                 q["time"], now]
        got += 1

    if stale:
        log("  跳过 %d 只（行情时间非今日，休市或停牌）" % stale)

    tmp = CSV_HIST + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for k in sorted(rows.keys()):
            w.writerow(rows[k])
    os.replace(tmp, CSV_HIST)
    return got, len(rows)


def fetch_dividends(since_year):
    """东财分红明细，按报告期倒序分页"""
    flt = "(REPORT_DATE>='%d-01-01')" % since_year
    base = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
            "?reportName=RPT_SHAREBONUS_DET"
            "&columns=SECURITY_CODE,PRETAX_BONUS_RMB,REPORT_DATE,EX_DIVIDEND_DATE"
            "&pageSize=500&sortColumns=REPORT_DATE&sortTypes=-1"
            "&filter=" + urllib.parse.quote(flt))
    acc, ann, plan = {}, {}, {}
    page, pages = 1, 1
    while page <= pages and page <= 30:
        try:
            req = urllib.request.Request(base + "&pageNumber=%d" % page, headers=UA)
            d = json.loads(urllib.request.urlopen(req, timeout=35).read().decode("utf-8", "ignore"))
        except Exception as e:
            log("  分红第 %d 页失败: %s" % (page, e))
            break
        res = d.get("result")
        if not res or not res.get("data"):
            break
        pages = res.get("pages", 1)
        for r in res["data"]:
            code = str(r.get("SECURITY_CODE") or "")
            b = r.get("PRETAX_BONUS_RMB")
            rd = (r.get("REPORT_DATE") or "")[:10]
            if not code or b is None or not rd:
                continue
            if not r.get("EX_DIVIDEND_DATE"):
                plan.setdefault(code, []).append((rd, b / 10.0))
                continue
            y = int(rd[:4])
            acc.setdefault(code, {})
            acc[code][y] = acc[code].get(y, 0.0) + b / 10.0
            if rd[5:] == "12-31":
                ann.setdefault(code, {})[y] = True
        page += 1
        time.sleep(0.25)
    return acc, ann, plan


def check_dividend(pool):
    """与内置分红额比对，返回差异列表与预案列表"""
    sy = datetime.now().year - 1
    acc, ann, plan = fetch_dividends(sy)
    diffs, plans = [], []
    for s in pool:
        if s["market"] == "hk":
            continue
        yrs, a = acc.get(s["code"]), ann.get(s["code"])
        if not yrs or not a:
            continue
        latest = max(int(y) for y in a)
        dps = yrs.get(latest, 0)
        if dps <= 0 or s["dps"] <= 0:
            continue
        dev = (dps / s["dps"] - 1) * 100
        if abs(dev) > 5:
            diffs.append((s["name"], s["code"], s["dps"], dps, latest, dev))
        for rd, pd in plan.get(s["code"], []):
            if int(rd[:4]) > latest:
                plans.append((s["name"], s["code"], rd, pd))
    return diffs, plans


def secid(s):
    """东财证券 id：沪市 1. / 深市 0. / 港股 116."""
    if s["market"] == "hk":
        return "116." + s["code"]
    return ("1." if s["market"] == "sh" else "0.") + s["code"]


def fetch_kline(s, lmt=30):
    """东财日 K（klt=101 日线 / fqt=1 前复权），返回 [(日期, 收盘), ...]

    注意：东财对密集请求会直接断连（Remote end closed connection），
    所以这里不做重试，交给上层按轮次补抓——重试扎堆反而更容易被掐。
    """
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
           "?secid=%s&klt=101&fqt=1&lmt=%d&end=20500101"
           "&fields1=f1&fields2=f51,f53" % (secid(s), lmt))
    try:
        req = urllib.request.Request(url, headers=UA)
        d = json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore"))
        kl = (d.get("data") or {}).get("klines") or []
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
    except Exception:
        return []


def boll_json(pool, period=20, mult=2.0, lmt=30, conc=4):
    """预算 BOLL(20,2) 写成 boll.json —— 页面优先读它，手机上就不必发上百个 K 线请求

    口径与页面完全一致：前复权收盘、20 日、±2 倍「总体」标准差（不是样本标准差）。
    并发实测：4 并发 11 秒可拿到 108/110；6 并发反而掉到 58/110（触发限流）。
    """
    from concurrent.futures import ThreadPoolExecutor

    bmap, todo = {}, list(pool)
    for attempt in range(4):
        if not todo:
            break
        if attempt:
            time.sleep(0.8 * attempt)          # 补抓前先让一让
        with ThreadPoolExecutor(max_workers=conc) as ex:
            res = list(zip(todo, ex.map(lambda x: fetch_kline(x, lmt), todo)))
        nxt = []
        for s, kl in res:
            closes = [c for _, c in kl]
            if len(closes) < period:
                nxt.append(s)
                continue
            arr = closes[-period:]
            mid = sum(arr) / period
            sd = (sum((x - mid) ** 2 for x in arr) / period) ** 0.5
            bmap[s["code"]] = {"lo": round(mid - mult * sd, 4),
                               "mid": round(mid, 4),
                               "up": round(mid + mult * sd, 4),
                               "n": len(closes), "d": kl[-1][0]}
        todo = nxt

    data = {"key": datetime.now().strftime("%Y%m%d"),
            "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "period": period, "mult": mult,
            "note": "前复权日线，20日±2倍总体标准差；由 收盘快照.py 生成",
            "map": bmap}
    tmp = BOLL_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, BOLL_JSON)
    return len(bmap), len(todo)


def main():
    log("=== 收盘快照开始 ===")
    if not os.path.exists(HTML):
        log("错误：找不到 %s" % HTML)
        return 1

    pool = read_pool()
    log("股票池 %d 只（A股 %d / 港股 %d）"
        % (len(pool), sum(1 for s in pool if s["market"] != "hk"),
           sum(1 for s in pool if s["market"] == "hk")))

    px = fetch_quotes(pool)
    log("行情抓到 %d 只" % len(px))

    day = datetime.now().strftime("%Y-%m-%d")
    got, total = write_history(pool, px, day)
    log("写入历史：今日 %d 条，累计 %d 条" % (got, total))

    try:
        bok, bbad = boll_json(pool)
        log("布林通道：算成 %d 只（K线不足 %d 只），已写入 %s" % (bok, bbad, os.path.basename(BOLL_JSON)))
    except Exception as e:
        log("布林通道生成失败：%s" % e)

    try:
        diffs, plans = check_dividend(pool)
    except Exception as e:
        log("分红检查失败：%s" % e)
        diffs, plans = [], []

    if diffs:
        fn = os.path.join(HERE, "分红差异-%s.txt" % datetime.now().strftime("%Y%m%d"))
        with open(fn, "w", encoding="utf-8") as f:
            f.write("红利工作台 · 分红差异报告  %s\n" % datetime.now().strftime("%Y-%m-%d %H:%M"))
            f.write("口径：最近完整财年各期「每10股派息(含税)」合计 ÷ 10，只计已实施\n")
            f.write("=" * 66 + "\n")
            f.write("%-10s %-8s %10s %10s %8s %10s\n" % ("名称", "代码", "内置值", "公告值", "财年", "偏差"))
            for n, c, old, neu, fy, dev in diffs:
                f.write("%-10s %-8s %10.4f %10.4f %8d %9.0f%%\n" % (n, c, old, neu, fy, dev))
            f.write("\n处理：打开 红利买点工作台.html → 数据页 → 检查分红公告 → 点提示条一键更新\n")
        log("发现 %d 只分红差异，已写入 %s" % (len(diffs), os.path.basename(fn)))
    else:
        log("分红检查：与公告一致，无需更新")

    if plans:
        log("另有 %d 只已公布下一年度分红预案（尚未实施）" % len(plans))

    log("=== 收盘快照结束 ===\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
