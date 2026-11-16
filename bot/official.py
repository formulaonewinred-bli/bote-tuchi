"""BOAT RACE オフィシャルサイト（boatrace.jp）のページを読む。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from .common import http_get

BASE = "https://www.boatrace.jp/owpc/pc/race"


@dataclass
class Racer:
    toban: str
    name: str
    klass: str
    lady: bool


@dataclass
class Race:
    jcd: str
    rno: int
    deadline: str               # "HH:MM"
    racers: list[Racer] = field(default_factory=list)
    race_type: str = ""         # 予選 / 準優勝戦 / 優勝戦 など


# ---------- 解析（HTML文字列 → データ）----------

def parse_index(html: str) -> list[tuple[str, str]]:
    """本日のレース一覧 → [(場コード, グレード表示のclass名), ...]。

    グレードは開催一覧の行にあるセルの class（例: is-ippan, is-G1b など）で表される。
    """
    soup = BeautifulSoup(html, "html.parser")
    out: list[tuple[str, str]] = []
    seen = set()
    for a in soup.select('a[href*="raceindex?jcd="]'):
        m = re.search(r"jcd=(\d{2})", a["href"])
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        grade = ""
        tr = a.find_parent("tr")
        if tr:
            for td in tr.find_all("td"):
                cls = " ".join(td.get("class") or [])
                g = re.search(r"\bis-(ippan|SG\w*|PG1\w*|G[123]\w*)", cls, re.I)
                if g:
                    grade = g.group(0)
                    break
        out.append((m.group(1), grade))
    return out


def parse_raceindex(html: str, jcd: str) -> list[Race]:
    """場のレース一覧 → 各レースの締切予定時刻と出走選手。"""
    soup = BeautifulSoup(html, "html.parser")
    table = next((t for t in soup.find_all("table") if "締切予定時刻" in t.get_text()), None)
    if table is None:
        return []
    races = []
    for tr in table.select("tbody tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 2:
            continue
        m = re.search(r"(\d{1,2})R", tds[0].get_text())
        t = re.search(r"(\d{1,2}):(\d{2})", tds[1].get_text())
        if not m or not t:
            continue
        race = Race(jcd=jcd, rno=int(m.group(1)), deadline=f"{int(t.group(1)):02d}:{t.group(2)}")
        for td in tds:
            a = td.select_one('a[href*="racersearch/profile?toban="]')
            if not a:
                continue
            toban = re.search(r"toban=(\d+)", a["href"]).group(1)
            kl = td.select_one(".is-fs12")
            race.racers.append(Racer(
                toban=toban,
                name=re.sub(r"[\s　]+", "", a.get_text()),
                klass=kl.get_text(strip=True) if kl else "",
                lady=td.select_one(".is-lady") is not None,
            ))
        races.append(race)
    return races


def parse_race_type(html: str) -> str:
    """出走表ページ見出し（例「準優勝戦　1800m」）からレース種別を取り出す。"""
    soup = BeautifulSoup(html, "html.parser")
    h = soup.select_one(".title16_titleDetail__add2020")
    if not h:
        return ""
    text = h.get_text(" ", strip=True)
    text = re.sub(r"\d{3,4}m.*$", "", text).strip()
    return re.sub(r"[\s　]+", " ", text)


def parse_exhibition_order(html: str) -> list[int]:
    """直前情報のスタート展示 → コース順に並んだ艇番（未実施なら空リスト）。"""
    soup = BeautifulSoup(html, "html.parser")
    table = next((t for t in soup.find_all("table") if "スタート展示" in t.get_text()), None)
    if table is None:
        return []
    order = []
    for row in table.select(".table1_boatImage1"):
        num = row.select_one(".table1_boatImage1Number")
        st = row.select_one(".table1_boatImage1Time")
        n = num.get_text(strip=True) if num else ""
        # STが入っていない＝展示がまだ（並び順が仮の可能性がある）
        if not n.isdigit() or not (st and st.get_text(strip=True)):
            return []
        order.append(int(n))
    return order if len(order) == 6 else []


def parse_odds3t(html: str) -> dict[str, float]:
    """3連単オッズ表 → {"1-2-3": 8.3, ...}。

    表は 1着(1〜6号艇) の6列グループ × 20行。4行ごとに2着の艇番セル（rowspan=4）が入る。
    """
    soup = BeautifulSoup(html, "html.parser")
    table = next((t for t in soup.find_all("table") if t.select_one("td.oddsPoint")), None)
    if table is None:
        return {}
    odds: dict[str, float] = {}
    second = [None] * 6
    for tr in table.select("tbody tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td", recursive=False)]
        if len(cells) == 18:
            groups = [cells[i:i + 3] for i in range(0, 18, 3)]
            for j, (s, t, o) in enumerate(groups):
                second[j] = s
                _put(odds, j + 1, s, t, o)
        elif len(cells) == 12:
            groups = [cells[i:i + 2] for i in range(0, 12, 2)]
            for j, (t, o) in enumerate(groups):
                _put(odds, j + 1, second[j], t, o)
    return odds


def _put(odds, first, second, third, val):
    try:
        v = float(val.replace(",", ""))
    except (ValueError, AttributeError):
        return
    if second and third and v > 0:
        odds[f"{first}-{second}-{third}"] = v


# ---------- 取得（URL → データ）----------

def fetch_jcds(hd: str) -> list[tuple[str, str]]:
    html = http_get(f"{BASE}/index?hd={hd}")
    return parse_index(html) if html else []


def fetch_races(jcd: str, hd: str) -> list[Race]:
    html = http_get(f"{BASE}/raceindex?jcd={jcd}&hd={hd}")
    return parse_raceindex(html, jcd) if html else []


def fetch_race_type(jcd: str, rno: int, hd: str) -> str:
    html = http_get(f"{BASE}/racelist?rno={rno}&jcd={jcd}&hd={hd}")
    return parse_race_type(html) if html else ""


def fetch_exhibition_order(jcd: str, rno: int, hd: str) -> list[int]:
    html = http_get(f"{BASE}/beforeinfo?rno={rno}&jcd={jcd}&hd={hd}")
    return parse_exhibition_order(html) if html else []


def fetch_odds3t(jcd: str, rno: int, hd: str) -> dict[str, float]:
    html = http_get(f"{BASE}/odds3t?rno={rno}&jcd={jcd}&hd={hd}")
    return parse_odds3t(html) if html else {}
