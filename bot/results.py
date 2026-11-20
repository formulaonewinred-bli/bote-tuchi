"""チェックしたレースの結果（3連単の着順と払戻）を取り込み、的中まとめ REPORT.md を作る。

結果は公式の競走成績ファイル（翌日公開）から取る。
  python -m bot.results        # 結果の取り込み + REPORT.md の作成
"""
from __future__ import annotations

import csv
import datetime as dt
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from .common import ROOT, VENUES, http_get, load_config, log, now_jst
from .stats import BOAT_LINE, RACE_DIST, RACE_HEAD, extract_lzh, k_url

CHECKS = ROOT / "data" / "checks.csv"
RESULTS = ROOT / "data" / "results.csv"
REPORT = ROOT / "REPORT.md"
STATE_DIR = ROOT / "state"
RESULT_FIELDS = ["date", "jcd", "rno", "trifecta", "payout"]
CHECK_FIELDS = ["date", "jcd", "venue", "rno", "deadline", "kinds", "race_type", "nige", "nigashi", "exhibition",
                "top1", "top1_odds", "top2", "top2_odds", "odds_123", "odds_132", "odds_124", "verdict", "reason"]
# 新しいオッズ条件（1・2番人気がともに 1-2-3/1-3-2/1-2-4）を使い始めた日
RULE_CHANGED = "20261119"


# ---------- 競走成績ファイルから結果を読む ----------

def parse_k_results(text: str) -> dict[tuple[str, int], tuple[str, str]]:
    """{(場コード, R): (3連単の組番 "1-2-3", 払戻金(100円あたり) or "")}"""
    out: dict[tuple[str, int], tuple[str, str]] = {}
    jcd, rno = None, None
    places: dict[str, int] = {}
    payout = ""

    def flush():
        if jcd and rno and all(k in places for k in ("01", "02", "03")):
            out[(jcd, rno)] = (f"{places['01']}-{places['02']}-{places['03']}", payout)

    for raw in text.splitlines():
        line = unicodedata.normalize("NFKC", raw)
        m = re.match(r"^(\d{2})KBGN", line.strip())
        if m:
            flush(); jcd = m.group(1); rno = None; places = {}; payout = ""
            continue
        m = RACE_HEAD.match(line)
        if m and RACE_DIST.search(line):
            flush(); rno = int(m.group(1)); places = {}; payout = ""
            continue
        if not rno:
            continue
        m = BOAT_LINE.match(raw)
        if m and m.group(1) in ("01", "02", "03") and m.group(1) not in places:
            places[m.group(1)] = int(m.group(2))
            continue
        m = re.search(r"3連単\s+(\d)-(\d)-(\d)\s+([\d,]+)", line)
        if m:
            payout = m.group(4).replace(",", "")
    flush()
    return out


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write(path: Path, fields: list[str], rows: list[dict]):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def backfill_checks_from_state():
    """checks.csv を作る前（11/16〜）の判定を state/ から補う（オッズ等は空欄）。"""
    checks = _read(CHECKS)
    have = {(r["date"], r["jcd"], str(r["rno"])) for r in checks}
    added = 0
    for f in sorted(STATE_DIR.glob("*.json")):
        import json
        hd = f.stem
        for key, verdict in json.loads(f.read_text(encoding="utf-8")).get("done", {}).items():
            if verdict not in ("notify", "exclude"):
                continue
            jcd, rno = key.split("-")
            if (hd, jcd, rno) in have:
                continue
            checks.append({"date": hd, "jcd": jcd, "venue": VENUES.get(jcd, jcd), "rno": rno, "verdict": verdict,
                           "reason": "（記録開始前）"})
            added += 1
    if added:
        checks.sort(key=lambda r: (r["date"], r.get("deadline") or "", r["jcd"], int(r["rno"])))
        _write(CHECKS, CHECK_FIELDS, checks)
        log(f"state から {added} 件を補完")


def fill_results():
    checks = _read(CHECKS)
    results = _read(RESULTS)
    have = {(r["date"], r["jcd"], str(r["rno"])) for r in results}
    today = now_jst().strftime("%Y%m%d")
    need = sorted({r["date"] for r in checks
                   if (r["date"], r["jcd"], str(r["rno"])) not in have and r["date"] < today})
    for hd in need:
        d = dt.datetime.strptime(hd, "%Y%m%d").date()
        blob = http_get(k_url(d), binary=True)
        if not blob:
            log(f"{hd}: 成績ファイルがまだありません")
            continue
        res = parse_k_results(extract_lzh(blob))
        n = 0
        for r in checks:
            if r["date"] != hd or (hd, r["jcd"], str(r["rno"])) in have:
                continue
            got = res.get((r["jcd"], int(r["rno"])))
            if got:
                results.append({"date": hd, "jcd": r["jcd"], "rno": r["rno"], "trifecta": got[0], "payout": got[1]})
                have.add((hd, r["jcd"], str(r["rno"])))
                n += 1
        log(f"{hd}: 結果 {n} 件を取り込み")
    _write(RESULTS, RESULT_FIELDS, results)


# ---------- まとめ ----------

def _stats(rows: list[dict], bets: list[str]) -> dict:
    n = len(rows)
    hit = [r for r in rows if r["trifecta"] in bets]
    pay_known = all(r["payout"] for r in hit)
    ret = sum(int(r["payout"]) for r in hit if r["payout"])
    cost = n * 100 * len(bets)
    per = {b: sum(1 for r in rows if r["trifecta"] == b) for b in bets}
    in1 = sum(1 for r in rows if r["trifecta"].startswith("1-"))
    return {"n": n, "hit": len(hit), "ret": ret, "cost": cost, "per": per, "in1": in1, "pay_known": pay_known}


def _pct(a, b):
    return f"{a / b * 100:.0f}%" if b else "-"


def make_report():
    cfg = load_config()
    bets = cfg.get("odds_top2_allowed") or [cfg.get("odds_first", "1-2-3"), cfg.get("odds_second", "1-3-2")]
    checks = _read(CHECKS)
    res = {(r["date"], r["jcd"], str(r["rno"])): r for r in _read(RESULTS)}
    rows = []
    for c in checks:
        r = res.get((c["date"], c["jcd"], str(c["rno"])))
        if r:
            rows.append({**c, "trifecta": r["trifecta"], "payout": r["payout"]})
    pending = len(checks) - len(rows)

    def table(title: str, groups: list[tuple[str, list[dict]]]) -> list[str]:
        out = [f"### {title}", "",
               "| 区分 | レース数 | 的中 | 的中率 | 1号艇1着 | 回収率 | " + " | ".join(bets) + " |",
               "|---|---|---|---|---|---|" + "---|" * len(bets)]
        for name, g in groups:
            s = _stats(g, bets)
            roi = _pct(s["ret"], s["cost"]) + ("" if s["pay_known"] else "※")
            out.append(f"| {name} | {s['n']} | {s['hit']} | {_pct(s['hit'], s['n'])} | {_pct(s['in1'], s['n'])} | "
                       f"{roi if s['n'] else '-'} | " + " | ".join(str(s['per'][b]) for b in bets) + " |")
        return out + [""]

    new_rule = [r for r in rows if r["date"] >= RULE_CHANGED]
    notified = [r for r in new_rule if r["verdict"] == "notify"]
    excluded = [r for r in new_rule if r["verdict"] == "exclude"]

    def reason_group(r):
        t = r.get("reason", "")
        if "枠なり" in t:
            return "展示が枠なりでない"
        if "人気" in t and "倍" not in t:
            return "人気順が条件外"
        if "倍" in t:
            return "1番人気10倍以上"
        return "その他"

    by_reason = defaultdict(list)
    for r in excluded:
        by_reason[reason_group(r)].append(r)
    by_kind = defaultdict(list)
    for r in notified:
        by_kind[r.get("kinds") or "不明"].append(r)
    by_venue = defaultdict(list)
    for r in notified:
        by_venue[r["venue"]].append(r)

    lines = [
        "# 通知レースの的中まとめ", "",
        f"更新：{now_jst():%Y-%m-%d %H:%M}（毎朝自動更新）", "",
        f"- 的中＝3連単が **{' / '.join(bets)}** のどれか（この{len(bets)}点を各100円買った想定で回収率を計算）",
        f"- 集計対象：{RULE_CHANGED[4:6]}/{RULE_CHANGED[6:]}以降（今のオッズ条件になってから）。結果待ち {pending} 件",
        "- 「除外したレース」は、もし買っていたらどうだったか。条件の見直しに使います", "",
    ]
    lines += table("通知したレース vs 除外したレース", [("通知した", notified), ("除外した（合計）", excluded)])
    lines += table("除外理由ごと（もし買っていたら）", sorted(by_reason.items()))
    if by_kind:
        lines += table("通知したレース：種類ごと", sorted(by_kind.items()))
    if by_venue:
        lines += table("通知したレース：場ごと", sorted(by_venue.items()))
    lines += ["※ 払戻金が取れなかったレースを含むため、回収率は参考値", "",
              "### 直近の通知・判定（新しい順）", "",
              "| 日付 | 場 | R | 種類 | 判定 | 1番人気 | 2番人気 | 結果 | 払戻 | 的中 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: (x["date"], x.get("deadline") or ""), reverse=True)[:40]:
        verdict = "◎通知" if r["verdict"] == "notify" else "×除外"
        t1 = f"{r.get('top1','')} {r.get('top1_odds','')}".strip() or "-"
        t2 = f"{r.get('top2','')} {r.get('top2_odds','')}".strip() or "-"
        hit = "○" if r["trifecta"] in bets else ""
        pay = f"{int(r['payout']):,}円" if r["payout"] else "-"
        lines.append(f"| {r['date'][4:6]}/{r['date'][6:]} | {r['venue']} | {r['rno']} | {r.get('kinds') or '-'} | "
                     f"{verdict} | {t1} | {t2} | {r['trifecta']} | {pay} | {hit} |")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"REPORT.md を更新（結果あり {len(rows)} 件）")


def main():
    backfill_checks_from_state()
    fill_results()
    make_report()


if __name__ == "__main__":
    main()
