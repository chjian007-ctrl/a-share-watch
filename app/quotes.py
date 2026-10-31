#!/usr/bin/env python3
"""A-share near-real-time quotes CLI (public Eastmoney / Tencent / Sina endpoints; NOT Level-2).

Usage: venv/bin/python quotes.py <cmd> [args] [--json]
  spot CODE...        latest quote per stock
  auction CODE...     9:25 call-auction price/pct/amount and amount vs prev full day
  limitup [YYYYMMDD]  涨停池 + 跌停/炸板 counts + 昨日涨停表现
  breadth             whole-market up/down/flat + total turnover
  sectors [N]         top/bottom N industry & concept boards (change %, main net inflow)
  index               上证/深证成指/创业板指/科创50
  cb list|spot [--max-premium P] [--min-premium P] [--min-amount 亿] [--max-price X] [--min-price X]
               [--no-redeem] [--no-st] [--sort amount|premium|pct|stock_pct|price|remaining] [--top N] [--wide]
                      all listed SH/SZ convertible bonds (price, 正股, 转股价/价值/溢价率, 剩余规模, 评级, 强赎)
  cb by-stock CODE... underlying stock code -> its CB(s)
  cb redeem           强赎 status: announced / maturity / triggered / counting / not_redeem
"""
import sys, json, time, datetime as dt, warnings
from concurrent.futures import ThreadPoolExecutor
warnings.filterwarnings("ignore")
import requests

BJ = dt.timezone(dt.timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
# push2.eastmoney.com itself is blocked/rate-limited from this box; numbered mirrors work.
EM_HOSTS = [f"{n}.push2.eastmoney.com" for n in (82, 28, 48, 63, 72, 88, 1)]
S = requests.Session(); S.headers.update(UA)
JSON_OUT = "--json" in sys.argv
VAL_OPTS = ("--max-premium", "--min-premium", "--min-amount", "--max-price", "--min-price", "--sort", "--top")
def _parse_args(argv):
    args, opts, i = [], {}, 0
    while i < len(argv):
        a = argv[i]
        if a in VAL_OPTS and i + 1 < len(argv): opts[a] = argv[i + 1]; i += 2; continue
        if "=" in a and a.split("=", 1)[0] in VAL_OPTS: k, v = a.split("=", 1); opts[k] = v; i += 1; continue
        if a.startswith("--"): opts[a] = True; i += 1; continue
        args.append(a); i += 1
    return args, opts
ARGS, OPTS = _parse_args(sys.argv[1:])

def now_bj(): return dt.datetime.now(BJ)
def ts(u):
    try: return dt.datetime.fromtimestamp(int(u), BJ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception: return None
def num(x):
    try:
        if x in ("-", "", None): return None
        return float(x)
    except Exception: return None

_EM_GOOD = []   # last mirror that answered (individual mirrors drop out at times)
def em_get(path, params, timeout=8):
    last = None
    import random
    hosts = EM_HOSTS[:]; random.shuffle(hosts)
    if _EM_GOOD and _EM_GOOD[0] in hosts: hosts.remove(_EM_GOOD[0]); hosts.insert(0, _EM_GOOD[0])
    for i, h in enumerate(hosts):
        if i: time.sleep(0.4)
        try:
            r = S.get(f"https://{h}{path}", params=params, timeout=timeout)
            if r.status_code == 200 and r.text.strip().startswith("{"):
                _EM_GOOD[:] = [h]; return r.json()
            last = f"{h} HTTP {r.status_code}"
        except Exception as e:
            last = f"{h} {type(e).__name__}"
    raise RuntimeError(f"Eastmoney failed: {last}")

def secid(code):
    code = code.strip()
    if code.startswith(("6", "9")) and not code.startswith(("92",)): return f"1.{code}"
    return f"0.{code}"   # 0/3 SZ, 4/8/92 BJ (m:0)
def tx_sym(code):
    if code.startswith(("6", "9")) and not code.startswith("92"): return "sh" + code
    if code.startswith(("4", "8", "92")): return "bj" + code
    return "sz" + code

# ---------------- output ----------------
def emit(obj):
    if JSON_OUT:
        print(json.dumps(obj, ensure_ascii=False, default=str)); return
    for k, v in obj.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            print(f"## {k} ({len(v)})")
            import pandas as pd
            with pd.option_context("display.width", 250, "display.max_columns", 30, "display.max_rows", 200):
                print(pd.DataFrame(v).to_string(index=False))
        else:
            print(f"{k}: {json.dumps(v, ensure_ascii=False, default=str) if isinstance(v,(dict,list)) else v}")

SPOT_F = "f2,f3,f4,f5,f6,f7,f8,f10,f12,f14,f15,f16,f17,f18,f124"
def em_row(d):
    return {"code": d.get("f12"), "name": d.get("f14"), "price": num(d.get("f2")), "pct": num(d.get("f3")),
            "open": num(d.get("f17")), "pre_close": num(d.get("f18")), "high": num(d.get("f15")), "low": num(d.get("f16")),
            "vol_hand": num(d.get("f5")), "amount_yi": round(num(d.get("f6")) / 1e8, 3) if num(d.get("f6")) is not None else None,
            "vol_ratio": num(d.get("f10")), "turnover_pct": num(d.get("f8")), "time": ts(d.get("f124"))}

def tx_quotes(codes_sym):
    r = S.get("https://qt.gtimg.cn/q=" + ",".join(codes_sym), timeout=8)
    r.encoding = "gbk"; out = {}
    for line in r.text.strip().split(";"):
        if "=\"" not in line: continue
        key = line.split("=")[0].strip().replace("v_", "")
        f = line.split("=\"", 1)[1].rstrip("\"").split("~")
        if len(f) < 50: continue
        t = f[30]
        out[key] = {"code": f[2], "name": f[1], "price": num(f[3]), "pct": num(f[32]), "open": num(f[5]),
                    "pre_close": num(f[4]), "high": num(f[33]), "low": num(f[34]), "vol_hand": num(f[36]),
                    "amount_yi": round(num(f[37]) * 1e4 / 1e8, 3) if num(f[37]) is not None else None,
                    "vol_ratio": num(f[49]), "turnover_pct": num(f[38]),
                    "time": f"{t[:4]}-{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}:{t[12:14]}" if len(t) >= 14 else t}
    return out

def cmd_spot(codes):
    if not codes: raise SystemExit("spot needs codes")
    try:
        j = em_get("/api/qt/ulist.np/get", {"fltt": 2, "invt": 2, "secids": ",".join(secid(c) for c in codes), "fields": SPOT_F})
        rows = [em_row(d) for d in (j.get("data") or {}).get("diff", [])]
        src = "eastmoney"
        if not rows: raise RuntimeError("empty")
    except Exception as e:
        q = tx_quotes([tx_sym(c) for c in codes]); rows = [q[tx_sym(c)] for c in codes if tx_sym(c) in q]
        src = f"tencent (eastmoney err: {e})"
    got = {r["code"] for r in rows}
    out = {"source": src, "fetched_at": now_bj().strftime("%F %T"), "rows": rows}
    miss = [c for c in codes if c not in got]
    if miss: out["not_found"] = miss
    return out

def tx_days(code):
    sym = tx_sym(code)
    r = S.get("https://web.ifzq.gtimg.cn/appstock/app/day/query", params={"code": sym}, timeout=8)
    return r.json()["data"][sym]["data"]   # list of {date, data:["HHMM price cumvol cumamt",...], prec}

def cmd_auction(codes):
    if not codes: raise SystemExit("auction needs codes")
    n = now_bj(); hm = n.hour * 100 + n.minute
    today = n.strftime("%Y%m%d")
    live = 925 <= hm < 930
    try: snap = tx_quotes([tx_sym(c) for c in codes])
    except Exception: snap = {}
    def one(c):
        try:
            days = tx_days(c)
            rec = {"code": c, "name": snap.get(tx_sym(c), {}).get("name")}
            td = [d for d in days if d.get("date") == today]
            prev = [d for d in days if d.get("date") != today and d.get("date", "") < today]
            prev_amt = num(prev[0]["data"][-1].split()[3]) if prev and prev[0]["data"] else None
            if live and tx_sym(c) in snap:
                s = snap[tx_sym(c)]; price = s["price"]; amt = (s["amount_yi"] or 0) * 1e8; vol = s["vol_hand"]
                pre = s["pre_close"]; rec["name"] = s["name"]; rec["src"] = "tencent snapshot (live auction)"; rec["time"] = s["time"]
            elif td and td[0]["data"]:
                b = td[0]["data"][0].split(); price, vol, amt = num(b[1]), num(b[2]), num(b[3]); pre = num(td[0].get("prec"))
                rec["src"] = f"tencent minute first bar {b[0]} (opening auction)"; rec["time"] = today
            else:
                return {"code": c, "error": "no auction data for today yet (before 9:25 or non-trading day)"}
            rec.update({"auction_price": price, "auction_pct": round((price / pre - 1) * 100, 2) if price and pre else None,
                        "auction_amount_wan": round(amt / 1e4, 1) if amt is not None else None, "auction_vol_hand": vol,
                        "pre_close": pre, "prev_day_amount_yi": round(prev_amt / 1e8, 3) if prev_amt else None,
                        "auction_amt_vs_prevday_pct": round(amt / prev_amt * 100, 2) if amt and prev_amt else None})
            return rec
        except Exception as e:
            return {"code": c, "error": f"{type(e).__name__}: {e}"}
    with ThreadPoolExecutor(8) as ex: rows = list(ex.map(one, codes))
    return {"fetched_at": n.strftime("%F %T"), "mode": "live 9:25-9:30 snapshot" if live else "historical first bar", "rows": rows}

def cmd_limitup(date=None):
    import akshare as ak
    date = date or now_bj().strftime("%Y%m%d")
    out = {"date": date, "fetched_at": now_bj().strftime("%F %T")}
    if dt.datetime.strptime(date, "%Y%m%d").weekday() >= 5:
        out["warning"] = "weekend date: Eastmoney returns the most recent trading day's pool instead"
    def safe(f):
        try: return f(date=date)
        except Exception as e: return e
    with ThreadPoolExecutor(4) as ex:
        zt, dtp, zb, prev = ex.map(safe, [ak.stock_zt_pool_em, ak.stock_zt_pool_dtgc_em, ak.stock_zt_pool_zbgc_em, ak.stock_zt_pool_previous_em])
    def ok(x): return not isinstance(x, Exception) and x is not None
    out["zt_count"] = len(zt) if ok(zt) else f"ERR {zt!r}"[:200]
    out["dt_count"] = len(dtp) if ok(dtp) else f"ERR {dtp!r}"[:200]
    out["zb_count"] = len(zb) if ok(zb) else f"ERR {zb!r}"[:200]
    if ok(zt) and ok(zb) and len(zt) + len(zb): out["zb_rate_pct"] = round(len(zb) / (len(zt) + len(zb)) * 100, 1)
    if ok(zt) and len(zt):
        out["max_lianban"] = int(zt["连板数"].max())
        out["lianban_ladder"] = {int(k): int(v) for k, v in zt["连板数"].value_counts().sort_index(ascending=False).items()}
        out["top_industries"] = zt["所属行业"].value_counts().head(8).to_dict()
        z = zt.sort_values(["连板数", "首次封板时间"], ascending=[False, True])
        out["zt_pool"] = [{"code": r["代码"], "name": r["名称"], "lb": int(r["连板数"]), "stat": r["涨停统计"],
                           "first_seal": r["首次封板时间"], "last_seal": r["最后封板时间"], "zb_times": int(r["炸板次数"]),
                           "seal_fund_yi": round(r["封板资金"] / 1e8, 2), "amount_yi": round(r["成交额"] / 1e8, 2),
                           "turnover": round(r["换手率"], 1), "industry": r["所属行业"]} for _, r in z.iterrows()]
    if ok(prev) and len(prev):
        p = prev["涨跌幅"]
        out["prev_zt_perf"] = {"n": len(prev), "avg_pct": round(p.mean(), 2), "up": int((p > 0).sum()), "down": int((p < 0).sum()),
                               "still_zt": int((prev["最新价"] >= prev["涨停价"] - 0.005).sum())}
        lb = prev[prev["昨日连板数"] >= 2]
        if len(lb): out["prev_lianban_avg_pct"] = round(lb["涨跌幅"].mean(), 2)
    elif not ok(prev): out["prev_zt_perf"] = f"ERR {prev!r}"[:200]
    return out

A_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
def em_clist_all(fs, fields, pz=100, workers=4):
    base = {"pz": pz, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f3", "fs": fs, "fields": fields}
    j = em_get("/api/qt/clist/get", {**base, "pn": 1}); d = j["data"]; total = d["total"]; rows = list(d["diff"])
    pages = range(2, (total + pz - 1) // pz + 1)
    def pg(p):
        for _ in range(3):
            try: return em_get("/api/qt/clist/get", {**base, "pn": p})["data"]["diff"]
            except Exception: time.sleep(0.5)
        raise RuntimeError(f"page {p} failed")
    with ThreadPoolExecutor(workers) as ex:
        for r in ex.map(pg, pages): rows += r
    return total, rows

def breadth_full():
    total, rows = em_clist_all(A_FS, "f2,f3,f6,f12,f124")
    up = down = flat = susp = ge5 = le5 = 0; amt = 0.0; tmax = 0
    for r in rows:
        p, a = num(r.get("f3")), num(r.get("f6"))
        if num(r.get("f2")) is None or p is None: susp += 1; continue
        if p > 0: up += 1
        elif p < 0: down += 1
        else: flat += 1
        if p >= 5: ge5 += 1
        if p <= -5: le5 += 1
        amt += a or 0; tmax = max(tmax, int(r.get("f124") or 0))
    return {"data_time": ts(tmax), "listed": total, "up": up, "down": down, "flat": flat, "no_trade": susp,
            "up_ge5pct": ge5, "down_le_minus5pct": le5, "turnover_yi": round(amt / 1e8, 1)}

def cmd_breadth(*opts):
    """Fast: up/down/flat from A股指数(000002)+深证A指(399107)+北证50(899050) breadth fields, 1 request.
    Legu adds 涨停/跌停 counts. '--full' / 'full' also scans the whole spot table (60 pages, may be slow/flaky)."""
    out = {"fetched_at": now_bj().strftime("%F %T")}
    try:
        j = em_get("/api/qt/ulist.np/get", {"fltt": 2, "invt": 2, "secids": "1.000002,0.399107,0.899050", "fields": "f6,f12,f14,f104,f105,f106,f124"})
        d = j["data"]["diff"]
        up = sum(x["f104"] for x in d); dn = sum(x["f105"] for x in d); fl = sum(x["f106"] for x in d)
        out.update({"source": "eastmoney index breadth (SH A + SZ A + BJ)", "data_time": ts(max(x["f124"] for x in d)),
                    "up": up, "down": dn, "flat": fl, "up_ratio_pct": round(up / max(up + dn + fl, 1) * 100, 1),
                    "turnover_yi": round(sum(x["f6"] for x in d) / 1e8, 1),
                    "turnover_by_mkt_yi": {x["f14"]: round(x["f6"] / 1e8, 1) for x in d}})
    except Exception as e:
        out["eastmoney_error"] = str(e)
    try:
        import akshare as ak
        lg = ak.stock_market_activity_legu()
        out["legu"] = {r["item"]: r["value"] for _, r in lg.iterrows()}
    except Exception as e:
        out["legu_error"] = f"{type(e).__name__}: {e}"[:200]
    if "full" in opts or "--full" in sys.argv:
        try: out["full_scan"] = breadth_full()
        except Exception as e: out["full_scan_error"] = str(e)
    if "up" not in out and "legu" not in out: raise RuntimeError("all breadth sources failed")
    return out

def cmd_sectors(n=10):
    n = int(n); f = "f3,f12,f14,f62,f104,f105,f128,f124"
    def board(fs, fid, po):
        j = em_get("/api/qt/clist/get", {"pn": 1, "pz": n, "po": po, "np": 1, "fltt": 2, "invt": 2, "fid": fid, "fs": fs, "fields": f})
        return [{"board": d["f14"], "code": d["f12"], "pct": num(d.get("f3")), "main_net_yi": round(num(d.get("f62")) / 1e8, 2) if num(d.get("f62")) is not None else None,
                 "up/down": f"{d.get('f104')}/{d.get('f105')}", "leader": d.get("f128"), "_t": d.get("f124")} for d in j["data"]["diff"]]
    jobs = {"industry_top": ("m:90+t:2", "f3", 1), "industry_bottom": ("m:90+t:2", "f3", 0), "industry_inflow_top": ("m:90+t:2", "f62", 1),
            "concept_top": ("m:90+t:3", "f3", 1), "concept_bottom": ("m:90+t:3", "f3", 0), "concept_inflow_top": ("m:90+t:3", "f62", 1)}
    out = {"source": "eastmoney boards", "fetched_at": now_bj().strftime("%F %T")}
    def run(k):
        try: return k, board(*jobs[k])
        except Exception as e: return k, f"ERR {e}"
    with ThreadPoolExecutor(3) as ex: res = dict(ex.map(run, jobs))
    tmax = 0
    for k in jobs:
        v = res[k]
        if isinstance(v, list):
            for x in v: tmax = max(tmax, int(x.pop("_t") or 0))
        out[k] = v
    out["data_time"] = ts(tmax)
    return out

IDX = [("1.000001", "sh000001", "上证指数"), ("0.399001", "sz399001", "深证成指"), ("0.399006", "sz399006", "创业板指"), ("1.000688", "sh000688", "科创50")]
def cmd_index():
    try:
        j = em_get("/api/qt/ulist.np/get", {"fltt": 2, "invt": 2, "secids": ",".join(i[0] for i in IDX), "fields": "f2,f3,f4,f6,f12,f14,f15,f16,f17,f18,f124"})
        rows = [{"name": d["f14"], "code": d["f12"], "last": d["f2"], "pct": d["f3"], "chg": d["f4"], "open": d["f17"], "high": d["f15"], "low": d["f16"],
                 "pre_close": d["f18"], "amount_yi": round(d["f6"] / 1e8, 1), "time": ts(d["f124"])} for d in j["data"]["diff"]]
        src = "eastmoney"
    except Exception as e:
        q = tx_quotes([i[1] for i in IDX])
        rows = [{"name": i[2], "last": q[i[1]]["price"], **{k: q[i[1]][k] for k in ("code", "pct", "open", "high", "low", "pre_close", "amount_yi", "time")}} for i in IDX if i[1] in q]
        src = f"tencent (eastmoney err: {e})"
    return {"source": src, "fetched_at": now_bj().strftime("%F %T"), "rows": rows}

# ---------------- convertible bonds (可转债) ----------------
import os, re as _re
CACHE_DIR = os.environ.get("QUOTES_CACHE_DIR") or os.path.join(os.environ.get("DATA_DIR") or os.path.dirname(os.path.abspath(__file__)), ".cache")
CB_FIELDS = "f2,f3,f5,f6,f8,f12,f13,f14,f26,f124,f227,f228,f229,f230,f232,f234,f235,f236,f237,f238,f239,f240,f241,f242"
JSL_H = {"X-Requested-With": "XMLHttpRequest", "Referer": "https://www.jisilu.cn/data/cbnew/", "Origin": "https://www.jisilu.cn"}

def cb_tx_sym(code): return ("sh" if code.startswith("11") or code.startswith("13") else "sz") + code
def ymd(v):
    v = str(v or "")
    if _re.fullmatch(r"\d{8}", v): return f"{v[:4]}-{v[4:6]}-{v[6:]}"
    return v[:10] or None

def _cache(name, max_age_s, fetch):
    """Small JSON file cache so repeated runs don't re-hit slow/static sources."""
    os.makedirs(CACHE_DIR, exist_ok=True); fp = os.path.join(CACHE_DIR, name)
    try:
        if time.time() - os.path.getmtime(fp) < max_age_s: return json.load(open(fp)), "cache"
    except Exception: pass
    try:
        d = fetch(); json.dump(d, open(fp, "w"), ensure_ascii=False); return d, "live"
    except Exception:
        if os.path.exists(fp): return json.load(open(fp)), "stale-cache"   # better than nothing
        raise

def jsl_redeem_rows():
    """Jisilu redeem list: public (no login), ALL listed CBs incl. 剩余规模 + 强赎 status. Guest cb_list_new is capped at 30 rows."""
    def f():
        r = S.post("https://www.jisilu.cn/data/cbnew/redeem_list/", params={"___jsl": f"LST___t={int(time.time()*1000)}"},
                   data={"rp": "50"}, headers=JSL_H, timeout=12)
        rows = [x["cell"] for x in r.json()["rows"]]
        if not rows: raise RuntimeError("jisilu redeem_list empty")
        return rows
    return _cache("jsl_redeem.json", 600, f)

def em_cb_static():
    """Eastmoney datacenter RPT_BOND_CB_LIST: rating, issue size, dates (not push2, so not affected by push2 limits)."""
    def f():
        out = {}; p = 1
        while True:
            j = S.get("https://datacenter-web.eastmoney.com/api/data/v1/get", params={
                "sortColumns": "PUBLIC_START_DATE", "sortTypes": "-1", "pageSize": "500", "pageNumber": p, "reportName": "RPT_BOND_CB_LIST",
                "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,CONVERT_STOCK_CODE,SECURITY_SHORT_NAME,RATING,ACTUAL_ISSUE_SCALE,DELIST_DATE,LISTING_DATE,EXPIRE_DATE,PUBLIC_START_DATE",
                "source": "WEB", "client": "WEB"}, timeout=12).json()
            for x in j["result"]["data"]: out[x["SECURITY_CODE"]] = x
            if p >= j["result"]["pages"]: break
            p += 1; time.sleep(0.3)
        return out
    return _cache("em_cb_static.json", 12 * 3600, f)

def jsl_redeem_status(x):
    icon = x.get("redeem_icon") or ""; real = x.get("redeem_real_days") or 0; need = x.get("redeem_count_days") or 15
    tot = x.get("redeem_total_days") or 30; txt = _re.sub(r"<[^>]+>", "", x.get("redeem_count") or "").strip()
    if icon == "R":
        if str(x.get("at_maturity")) == "1":
            return "maturity", f"到期赎回，最后交易日{x.get('delist_dt')}，到期赎回价{x.get('redeem_price')}"
        return "announced", f"已公告强赎，最后交易日{x.get('delist_dt')}，赎回价{x.get('redeem_price')}"
    if icon == "O": return "announced", f"公告要强赎: {x.get('force_redeem') or txt}"
    if icon == "B": return "triggered", f"已满足强赎条件({real}/{need})，待公司公告是否强赎"
    if icon == "G": return "not_redeem", f"公告不强赎: {x.get('force_redeem') or ''}"
    if real and real > 0: return "counting", f"强赎计数中 {real}/{need}（连续{tot}日内需{need}日≥强赎触发价）"
    return ("other:" + icon, txt or None) if icon else ("none", None)

def _f(v):
    try: return float(v)
    except Exception: return None

def em_cb_pages(pz=100):
    """Sequential paging (gentler on the mirrors than parallel), each page retried across all mirrors."""
    base = {"pz": pz, "po": 1, "np": 1, "fltt": 2, "invt": 2, "fid": "f6", "fs": "b:MK0354", "fields": CB_FIELDS}
    rows, p, total = [], 1, None
    while True:
        tries = 1 if p == 1 else 3   # page 1 failing = mirrors limited -> fall back fast
        for k in range(tries):
            try: d = em_get("/api/qt/clist/get", {**base, "pn": p}, timeout=6)["data"]; break
            except Exception:
                if k == tries - 1: raise
                time.sleep(1.5)
        total = d["total"]; rows += d["diff"]
        if len(rows) >= total or not d["diff"]: return rows
        p += 1; time.sleep(0.6)

def cb_universe():
    """All listed SH/SZ CBs. Primary: Eastmoney push2 比价表 (b:MK0354). Fallback: Jisilu list + Tencent quotes, computed premium."""
    notes = {}
    try: jsl, st = jsl_redeem_rows(); notes["jisilu_redeem"] = st
    except Exception as e: jsl = []; notes["jisilu_redeem_error"] = f"{type(e).__name__}: {e}"[:200]
    try: stat, st = em_cb_static(); notes["em_datacenter"] = st
    except Exception as e: stat = {}; notes["em_datacenter_error"] = f"{type(e).__name__}: {e}"[:200]
    jmap = {x["bond_id"]: x for x in jsl}
    rows = []
    try:
        diff = em_cb_pages()
        for d in diff:
            c = d.get("f12"); a = num(d.get("f6"))
            rows.append({"code": c, "name": d.get("f14"), "price": num(d.get("f2")), "pct": num(d.get("f3")),
                         "amount_yi": round(a / 1e8, 3) if a is not None else None, "turnover_pct": num(d.get("f8")),
                         "stock_code": d.get("f232"), "stock_name": d.get("f234"), "stock_price": num(d.get("f229")), "stock_pct": num(d.get("f230")),
                         "conv_price": num(d.get("f235")), "conv_value": num(d.get("f236")), "premium_pct": num(d.get("f237")),
                         "pure_bond_value": num(d.get("f227")), "force_redeem_trig_price": num(d.get("f240")),
                         "list_date": ymd(d.get("f26")), "conv_start": ymd(d.get("f242")), "time": ts(d.get("f124"))})
        src = "eastmoney push2 b:MK0354 (比价表)"
    except Exception as e:
        if not jsl: raise RuntimeError(f"eastmoney failed ({e}) and jisilu unavailable")
        src = f"fallback jisilu list + tencent quotes (eastmoney err: {e})"
        syms = []
        for x in jsl: syms += [cb_tx_sym(x["bond_id"]), tx_sym(x["stock_id"])]
        q = {}
        for i in range(0, len(syms), 60):
            try: q.update(tx_quotes(syms[i:i + 60]))
            except Exception: pass
            time.sleep(0.2)
        today = now_bj().strftime("%Y-%m-%d")
        for x in jsl:
            sd = stat.get(x["bond_id"])
            if sd is not None and not (sd.get("LISTING_DATE") and sd["LISTING_DATE"][:10] <= today): continue   # issued, not yet trading
            b = q.get(cb_tx_sym(x["bond_id"]), {}); s_ = q.get(tx_sym(x["stock_id"]), {})
            cp = _f(x.get("convert_price")); sp = s_.get("price") if s_ else _f(x.get("sprice")); bp = b.get("price") if b else _f(x.get("price"))
            cv = round(sp / cp * 100, 3) if sp and cp else None
            rows.append({"code": x["bond_id"], "name": b.get("name") or x["bond_nm"], "price": bp, "pct": b.get("pct"),
                         "amount_yi": b.get("amount_yi"), "turnover_pct": b.get("turnover_pct"),
                         "stock_code": x["stock_id"], "stock_name": x["stock_nm"], "stock_price": sp, "stock_pct": s_.get("pct"),
                         "conv_price": cp, "conv_value": cv, "premium_pct": round((bp / cv - 1) * 100, 2) if bp and cv else None,
                         "pure_bond_value": None, "force_redeem_trig_price": _f(x.get("force_redeem_price")),
                         "list_date": None, "conv_start": x.get("convert_dt"), "time": b.get("time")})
    today = now_bj().strftime("%Y-%m-%d")
    def listed(r):
        sd = stat.get(r["code"])
        if sd is not None: return bool(sd.get("LISTING_DATE")) and sd["LISTING_DATE"][:10] <= today
        return r.get("price") is not None
    rows = [r for r in rows if listed(r)]
    for r in rows:
        j = jmap.get(r["code"]); s_ = stat.get(r["code"], {})
        r["remaining_yi"] = _f(j.get("curr_iss_amt")) if j else None
        r["issue_yi"] = _f(j.get("orig_iss_amt")) if j else s_.get("ACTUAL_ISSUE_SCALE")
        r["rating"] = s_.get("RATING")
        r["stock_st"] = "ST" in (r.get("stock_name") or "") or "退" in (r.get("stock_name") or "")
        if j:
            r["redeem_flag"], r["redeem_note"] = jsl_redeem_status(j)
            r["maturity"] = j.get("maturity_dt")
        else:
            r["redeem_flag"], r["redeem_note"] = None, None
        if not r.get("list_date") and s_.get("LISTING_DATE"): r["list_date"] = ymd(s_["LISTING_DATE"])
    return rows, src, notes, stat

def _cb_filter(rows):
    def o(k, cast=float):
        v = OPTS.get(k)
        return cast(v) if v not in (None, True) else None
    mp, mnp, ma, xp, np_ = o("--max-premium"), o("--min-premium"), o("--min-amount"), o("--max-price"), o("--min-price")
    out = []
    for r in rows:
        if mp is not None and (r["premium_pct"] is None or r["premium_pct"] > mp): continue
        if mnp is not None and (r["premium_pct"] is None or r["premium_pct"] < mnp): continue
        if ma is not None and (r["amount_yi"] is None or r["amount_yi"] < ma): continue
        if xp is not None and (r["price"] is None or r["price"] > xp): continue
        if np_ is not None and (r["price"] is None or r["price"] < np_): continue
        if "--no-st" in OPTS and r.get("stock_st"): continue
        if "--no-redeem" in OPTS and r.get("redeem_flag") in ("announced", "maturity", "triggered"): continue
        out.append(r)
    key = OPTS.get("--sort", "amount")
    km = {"amount": ("amount_yi", True), "premium": ("premium_pct", False), "pct": ("pct", True), "stock_pct": ("stock_pct", True),
          "price": ("price", False), "remaining": ("remaining_yi", False)}
    f_, rev = km.get(key, km["amount"])
    out.sort(key=lambda r: (r[f_] is None, -(r[f_] or 0) if rev else (r[f_] or 0)))
    top = o("--top", int)
    return out[:top] if top else out

def _cb_meta(rows, src, notes):
    tmax = max((r["time"] for r in rows if r.get("time")), default=None)
    return {"source": src, "aux_sources": notes, "fetched_at": now_bj().strftime("%F %T"), "data_time": tmax}

CB_TEXT_COLS = ["code", "name", "price", "pct", "amount_yi", "stock_code", "stock_name", "stock_pct", "conv_price", "conv_value",
                "premium_pct", "remaining_yi", "rating", "redeem_flag"]
def cmd_cb(sub=None, *rest):
    sub = sub or "list"
    if sub in ("list", "spot"):
        rows, src, notes, _ = cb_universe(); n_all = len(rows); rows = _cb_filter(rows)
        out = {**_cb_meta(rows, src, notes), "listed_total": n_all, "filters": {k: v for k, v in OPTS.items() if k != "--json"}, "count": len(rows)}
        if not JSON_OUT and "--wide" not in OPTS: rows = [{k: r.get(k) for k in CB_TEXT_COLS} for r in rows]
        out["rows"] = rows; return out
    if sub in ("by-stock", "bystock", "stock"):
        codes = [c.strip() for c in rest if c.strip()]
        if not codes: raise SystemExit("cb by-stock needs stock codes")
        rows, src, notes, stat = cb_universe()
        res = []
        for c in codes:
            m = [r for r in rows if r["stock_code"] == c]
            pend = [{"code": k, "name": v.get("SECURITY_NAME_ABBR"), "public_start": ymd(v.get("PUBLIC_START_DATE")), "listing_date": ymd(v.get("LISTING_DATE"))}
                    for k, v in stat.items() if v.get("CONVERT_STOCK_CODE") == c and not v.get("DELIST_DATE") and k not in {r["code"] for r in rows}]
            e = {"stock_code": c, "has_cb": bool(m), "cbs": m}
            if pend: e["issued_not_listed"] = pend
            if not m:
                hist = [f"{k} {v.get('SECURITY_NAME_ABBR')} (delisted {ymd(v.get('DELIST_DATE'))})" for k, v in stat.items()
                        if v.get("CONVERT_STOCK_CODE") == c and v.get("DELIST_DATE")]
                e["note"] = "no listed CB for this stock" + (f"; past CBs: {', '.join(hist)}" if hist else "")
            res.append(e)
        return {**_cb_meta(rows, src, notes), "results": res}
    if sub in ("redeem", "qs"):
        jsl, st = jsl_redeem_rows()
        groups = {"announced": [], "maturity": [], "triggered": [], "counting": [], "not_redeem": []}
        for x in jsl:
            flag, note = jsl_redeem_status(x)
            if flag in groups or flag.startswith("other:"):
                groups.setdefault(flag, []).append({"code": x["bond_id"], "name": x["bond_nm"], "price": _f(x.get("price")), "stock_code": x["stock_id"],
                    "stock_name": x["stock_nm"], "stock_price": _f(x.get("sprice")), "conv_price": _f(x.get("convert_price")),
                    "trig_price": _f(x.get("force_redeem_price")), "days": f"{x.get('redeem_real_days')}/{x.get('redeem_count_days')}",
                    "remaining_yi": _f(x.get("curr_iss_amt")), "last_trade": x.get("delist_dt"), "note": note})
        groups["counting"].sort(key=lambda r: -int(r["days"].split("/")[0] or 0))
        return {"source": f"jisilu redeem_list (public, no login; {st})", "fetched_at": now_bj().strftime("%F %T"), "total_bonds": len(jsl),
                "legend": {"announced": "已公告强赎(避开)", "maturity": "临近到期赎回(避开)", "triggered": "已满足强赎条件、待公告(高风险)",
                           "counting": "强赎天数计数中", "not_redeem": "已公告本轮不强赎"},
                "counts": {k: len(v) for k, v in groups.items()}, **groups}
    raise SystemExit("cb subcommands: list|spot, by-stock CODE..., redeem")


def main():
    if not ARGS: print(__doc__); return 1
    c, rest = ARGS[0], ARGS[1:]
    t0 = time.time()
    try:
        fn = {"spot": lambda: cmd_spot(rest), "auction": lambda: cmd_auction(rest), "limitup": lambda: cmd_limitup(*rest[:1]),
              "breadth": lambda: cmd_breadth(*rest), "sectors": lambda: cmd_sectors(*rest[:1]), "index": cmd_index,
              "cb": lambda: cmd_cb(*rest)}[c]
    except KeyError:
        print(__doc__); return 1
    try:
        res = fn(); res["elapsed_s"] = round(time.time() - t0, 2); emit(res); return 0
    except Exception as e:
        emit({"error": f"{type(e).__name__}: {e}", "elapsed_s": round(time.time() - t0, 2)}); return 2

if __name__ == "__main__": sys.exit(main())
