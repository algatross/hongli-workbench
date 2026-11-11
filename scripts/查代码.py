# -*- coding: utf-8 -*-
"""股票名称 → 代码 查询（录入自选股用）

用法：
    python lookup_stock.py 贵州茅台 长江电力 中国神华
    python lookup_stock.py --file names.txt

只保留 A 股与港股，自动过滤期货 / 英股 / 粉单等噪声。
"""
import io
import json
import sys
import time
import urllib.parse
import urllib.request

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}
# MktNum: 0=深A 1=沪A 116=港股
KEEP = {"0": "sz", "1": "sh", "116": "hk"}


def search(kw):
    url = ("https://searchapi.eastmoney.com/api/suggest/get"
           "?input=%s&type=14&token=D43BF722C8E33BDC906FB84D85E326E8&count=12"
           % urllib.parse.quote(kw))
    try:
        d = json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers=UA), timeout=20).read().decode("utf-8", "ignore"))
    except Exception as e:
        return [], "请求失败: %s" % e
    items = (d.get("QuotationCodeTable") or {}).get("Data") or []
    out = []
    for x in items:
        mk = str(x.get("MktNum") or "")
        if mk not in KEEP:
            continue
        out.append({"code": x.get("Code"), "name": x.get("Name"),
                    "market": KEEP[mk], "type": x.get("SecurityTypeName")})
    return out, None


def main():
    args = sys.argv[1:]
    names = []
    if args and args[0] == "--file":
        names = [l.strip() for l in open(args[1], encoding="utf-8") if l.strip()]
    else:
        names = args
    if not names:
        print("用法: python lookup_stock.py 名称 [名称...]")
        return 1

    result = {}
    for n in names:
        hits, err = search(n)
        if err:
            print("%-10s %s" % (n, err))
            continue
        if not hits:
            print("%-10s ✗ 无匹配" % n)
            result[n] = None
            continue
        # 优先取名称完全一致的
        exact = [h for h in hits if h["name"] == n]
        pick = exact[0] if exact else hits[0]
        result[n] = pick
        extra = "" if len(hits) == 1 else "  （另有 %d 个候选）" % (len(hits) - 1)
        print("%-10s → %-8s %-8s %-4s %s%s"
              % (n, pick["code"], pick["name"], pick["market"], pick["type"], extra))
        if len(hits) > 1:
            for h in hits[1:4]:
                print("            候选: %s %s %s" % (h["code"], h["name"], h["type"]))
        time.sleep(0.25)

    print()
    print("JSON:")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
