"""逃げ率・逃し率の元データ作り。

公式の競走成績ファイル（https://www1.mbrace.or.jp/od2/K/YYYYMM/kYYMMDD.lzh）を
1日ずつダウンロードし、各レースの「1コース進入選手・2コース進入選手・逃げで決まったか」を
data/course12.csv に貯める。

  逃げ率 = 1コースで逃げて1着になった回数 ÷ 1コース進入回数
  逃し率 = 2コースのとき1コースに逃げられた回数 ÷ 2コース進入回数

使い方: python -m bot.stats [--days 180]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

from .common import JST, ROOT, http_get, load_config, log, now_jst

DATA = ROOT / "data" / "course12.csv"
FIELDS = ["date", "jcd", "rno", "c1", "c2", "nige"]

RACE_HEAD = re.compile(r"^\s*(\d{1,2})R\s")
BOAT_LINE = re.compile(r"^\s*(\d{2}|[A-Z]\d|[A-Z])\s+([1-6])\s+(\d{4})\s+(.*)$")
RACE_DIST = re.compile(r"H\d{3,4}m")


def k_url(d: dt.date) -> str:
    return f"https://www1.mbrace.or.jp/od2/K/{d:%Y%m}/k{d:%y%m%d}.lzh"


def extract_lzh(blob: bytes) -> str:
    """lha コマンド（Ubuntuの lhasa パッケージ）で解凍し、Shift_JIS のテキストを返す。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "k.lzh"
        p.write_bytes(blob)
        try:
            subprocess.run(["lha", "xq", p.name], cwd=tmp, check=True, capture_output=True)
        except subprocess.CalledProcessError:
            subprocess.run(["lha", "x", p.name], cwd=tmp, check=True, capture_output=True)
        texts = [f for f in Path(tmp).iterdir() if f.suffix.upper() == ".TXT"]
        if not texts:
            raise RuntimeError("解凍結果にTXTファイルがありません")
        return texts[0].read_bytes().decode("cp932", errors="replace")


def parse_k_text(text: str, date_str: str) -> list[dict]:
    """競走成績テキストから、レースごとの1・2コース選手と逃げかどうかを取り出す。"""
    rows: list[dict] = []
    jcd = None
    rno = None
    kimarite = None
    entries: dict[int, tuple[str, str]] = {}  # course -> (toban, chakujun)

    def flush():
        if jcd and rno and kimarite and 1 in entries and 2 in entries:
            c1_toban, c1_chaku = entries[1]
            rows.append({
                "date": date_str, "jcd": jcd, "rno": rno,
                "c1": c1_toban, "c2": entries[2][0],
                "nige": 1 if (kimarite.startswith("逃げ") and c1_chaku == "01") else 0,
            })

    for line in text.splitlines():
        m = re.match(r"^(\d{2})KBGN", line.strip())
        if m:
            flush(); rno = None; entries = {}; kimarite = None
            jcd = m.group(1)
            continue
        if "ﾚｰｽﾀｲﾑ" in line or "レースタイム" in line:
            # 列見出し行の末尾に決まり手が書かれている
            tail = line.split("ﾚｰｽﾀｲﾑ")[-1] if "ﾚｰｽﾀｲﾑ" in line else line.split("レースタイム")[-1]
            kimarite = tail.strip().replace("　", "")
            continue
        m = RACE_HEAD.match(line)
        if m and RACE_DIST.search(line):
            flush(); entries = {}; kimarite = None
            rno = int(m.group(1))
            continue
        m = BOAT_LINE.match(line)
        if m and rno:
            chaku, _waku, toban, rest = m.groups()
            tokens = rest.split()  # 全角スペースでも区切られる
            k = next((i for i, t in enumerate(tokens) if t.isascii() and t.isdigit()), None)
            if k is None or k + 3 >= len(tokens):
                continue
            course = tokens[k + 3]
            if re.fullmatch(r"[1-6]", course) and int(course) not in entries:
                entries[int(course)] = (toban, chaku)
    flush()
    return rows


def load_rows() -> list[dict]:
    if not DATA.exists():
        return []
    with open(DATA, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_rows(rows: list[dict]):
    DATA.parent.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda r: (r["date"], r["jcd"], int(r["rno"])))
    with open(DATA, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def update(days: int):
    rows = load_rows()
    have = {r["date"] for r in rows}
    today = now_jst().date()
    cutoff = today - dt.timedelta(days=days + 7)
    rows = [r for r in rows if r["date"] >= cutoff.strftime("%Y%m%d")]
    added = 0
    for i in range(days, 0, -1):
        d = today - dt.timedelta(days=i)
        ds = d.strftime("%Y%m%d")
        if ds in have:
            continue
        blob = http_get(k_url(d), binary=True)
        if not blob:
            log(f"{ds}: 成績ファイルなし（開催なし or 未公開）")
            continue
        try:
            new = parse_k_text(extract_lzh(blob), ds)
        except Exception as e:  # noqa: BLE001
            log(f"{ds}: 解析失敗 {e}")
            continue
        log(f"{ds}: {len(new)} レース")
        rows.extend(new)
        added += len(new)
    save_rows(rows)
    log(f"追加 {added} レース / 合計 {len(rows)} レース")


class Rates:
    """選手ごとの逃げ率・逃し率。"""

    def __init__(self, days: int):
        since = (now_jst().date() - dt.timedelta(days=days)).strftime("%Y%m%d")
        self.c1 = defaultdict(lambda: [0, 0])  # toban -> [逃げ回数, 1コース回数]
        self.c2 = defaultdict(lambda: [0, 0])  # toban -> [逃げられた回数, 2コース回数]
        self.count = 0
        for r in load_rows():
            if r["date"] < since:
                continue
            n = int(r["nige"])
            self.c1[r["c1"]][0] += n; self.c1[r["c1"]][1] += 1
            self.c2[r["c2"]][0] += n; self.c2[r["c2"]][1] += 1
            self.count += 1

    def nige(self, toban: str) -> tuple[float | None, int]:
        a, b = self.c1.get(toban, (0, 0))
        return (a / b * 100 if b else None), b

    def nigashi(self, toban: str) -> tuple[float | None, int]:
        a, b = self.c2.get(toban, (0, 0))
        return (a / b * 100 if b else None), b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None)
    a = ap.parse_args()
    days = a.days or load_config().get("stats_days", 180)
    update(days)


if __name__ == "__main__":
    main()
