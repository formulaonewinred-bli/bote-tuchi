"""対象レースを探し、締切前に展示とオッズを確認して通知する。

GitHub Actions から30分おきに起動される（同時に動くのは1つだけ）。起動すると、その日の未処理のレースを
締切前チェックの時刻順にすべて処理する。処理済みのレースは state/日付.json に記録して二重通知を防ぐ。

使い方:
  python -m bot.monitor                               # 定期実行（GitHub Actions）
  python -m bot.monitor --mode dryrun-today           # 今日の対象レースを一覧にして通知（チェックはしない）
  python -m bot.monitor --mode test-past --date 20261115  # 過去日で判定を試し、結果を1通にまとめて通知
  python -m bot.monitor --mode test-notify            # 通知が届くかだけ確認
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import time
from dataclasses import dataclass

from . import official
from .common import JST, ROOT, VENUES, kyoteibiyori_url, load_config, log, notify, now_jst
from .stats import Rates



@dataclass
class Target:
    race: official.Race
    kinds: list[str]
    nige: float
    nigashi: float

    @property
    def venue(self) -> str:
        return VENUES.get(self.race.jcd, self.race.jcd)

    def label(self) -> str:
        kinds = "・".join(self.kinds)
        rt = self.race.race_type or ""
        return f"{rt}／{kinds}" if rt and rt not in kinds else (rt or kinds)


def at(hd: str, hhmm: str) -> dt.datetime:
    d = dt.datetime.strptime(hd, "%Y%m%d").date()
    h, m = map(int, hhmm.split(":"))
    return dt.datetime(d.year, d.month, d.day, h, m, tzinfo=JST)


def build_targets(hd: str, cfg: dict, rates: Rates):
    targets: list[Target] = []
    lady_excluded: list[str] = []
    excl = [g.upper() for g in cfg.get("exclude_grades", [])]
    for jcd, grade in official.fetch_jcds(hd):
        venue = VENUES.get(jcd, jcd)
        log(f"{venue}: グレード表示 {grade or '不明'}")
        if grade and any(g in grade.upper() for g in excl):
            log(f"{venue}: 除外グレードのため対象外")
            continue
        kikaku = set(cfg.get("kikaku", {}).get(venue) or [])
        for race in official.fetch_races(jcd, hd):
            if len(race.racers) < 2:
                continue
            r1, r2 = race.racers[0], race.racers[1]
            n1, s1 = rates.nige(r1.toban)
            n2, s2 = rates.nigashi(r2.toban)
            if n1 is None or n2 is None or n1 < cfg["nige_min"] or n2 < cfg["nigashi_min"]:
                continue
            if s1 < cfg.get("min_samples", 0) or s2 < cfg.get("min_samples", 0):
                continue
            is12 = race.rno == 12
            is_kikaku = race.rno in kikaku
            if not (is12 or is_kikaku or race.rno >= 9):  # 9R以降は準優・優勝の可能性があるので種別を確認
                continue
            race.race_type = official.fetch_race_type(jcd, race.rno, hd)
            rt = race.race_type
            kinds = []
            if cfg.get("target_race12", True) and is12:
                kinds.append("12R")
            if cfg.get("target_junyu", True) and "準優" in rt:
                kinds.append("準優勝戦")
            elif cfg.get("target_yusho", True) and "優勝戦" in rt:
                kinds.append("優勝戦")
            if is_kikaku:
                kinds.append("企画レース")
            if not kinds:
                continue
            if cfg.get("exclude_lady_races", True) and len(race.racers) == 6 and all(r.lady for r in race.racers):
                lady_excluded.append(f"{venue}{race.rno}R")
                continue
            targets.append(Target(race, kinds, n1, n2))
    targets.sort(key=lambda t: t.race.deadline)
    return targets, lady_excluded


def check(t: Target, hd: str, cfg: dict, *, wait: bool = True) -> tuple[str, str]:
    """締切前チェック。戻り値: (判定 'notify'|'exclude', 説明文)"""
    r = t.race
    deadline = at(hd, r.deadline)
    order: list[int] = []
    for i in range(cfg.get("exhibit_retry_times", 2) + 1):
        order = official.fetch_exhibition_order(r.jcd, r.rno, hd)
        if order or not wait:
            break
        if i < cfg.get("exhibit_retry_times", 2):
            interval = cfg.get("exhibit_retry_interval_sec", 120)
            if now_jst() + dt.timedelta(seconds=interval) > deadline - dt.timedelta(minutes=8):
                break
            log(f"{t.venue}{r.rno}R 展示未掲載、{interval}秒後に再確認")
            time.sleep(interval)
    odds = official.fetch_odds3t(r.jcd, r.rno, hd)
    ranked = sorted(odds.items(), key=lambda kv: kv[1])

    reasons, unverified, lines = [], [], []
    if cfg.get("require_wakunari", True):
        if not order:
            unverified.append("展示進入")
            lines.append("展示進入：⚠未確認")
        elif order != [1, 2, 3, 4, 5, 6]:
            reasons.append("展示が枠なりでない（" + "".join(map(str, order)) + "）")
        else:
            lines.append("展示進入：枠なり")
    if len(ranked) >= 2:
        (k1, v1), (k2, v2) = ranked[0], ranked[1]
        lines.append(f"3連単 1番人気 {k1} {v1:.1f}倍／2番人気 {k2} {v2:.1f}倍")
        if k1 != cfg["odds_first"]:
            reasons.append(f"1番人気が{k1}")
        if k2 != cfg["odds_second"]:
            reasons.append(f"2番人気が{k2}")
        if v1 >= cfg["odds_first_max"]:
            reasons.append(f"1番人気{v1:.1f}倍")
    else:
        unverified.append("オッズ")
        lines.append("3連単オッズ：⚠未確認")

    if reasons:
        return "exclude", "、".join(reasons)
    if unverified and not cfg.get("notify_unverified", True):
        return "exclude", "未確認（" + "・".join(unverified) + "）"
    r1, r2 = r.racers[0], r.racers[1]
    body = "\n".join([
        f"1号艇 {r1.name}（{r1.klass}）逃げ率 {t.nige:.1f}%／2号艇 {r2.name}（{r2.klass}）逃し率 {t.nigashi:.1f}%",
        *lines,
    ])
    return "notify", body


def title_of(t: Target) -> str:
    return f"🚤 {t.venue} {t.race.rno}R【{t.label()}】締切 {t.race.deadline}"


STATE_DIR = ROOT / "state"
MAX_RUN = dt.timedelta(hours=5, minutes=30)  # GitHub Actions の1ジョブ上限(6時間)より短く


def load_state(hd: str) -> dict:
    f = STATE_DIR / f"{hd}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {"summary": False, "done": {}}


def save_state(hd: str, state: dict, msg: str):
    """処理済みレースを記録し、GitHub上に保存する（次の実行と二重にならないように）。"""
    STATE_DIR.mkdir(exist_ok=True)
    f = STATE_DIR / f"{hd}.json"
    f.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    for attempt in range(4):
        subprocess.run(["git", "add", str(f)], cwd=ROOT)
        subprocess.run(["git", "commit", "-q", "-m", msg], cwd=ROOT)
        subprocess.run(["git", "pull", "-q", "--rebase", "-X", "theirs"], cwd=ROOT)
        if subprocess.run(["git", "push", "-q"], cwd=ROOT).returncode == 0:
            return
        time.sleep(3 + attempt * 3)
    log("状態の保存（push）に失敗しました")


def race_key(t: Target) -> str:
    return f"{t.race.jcd}-{t.race.rno}"


def run_auto(cfg: dict, rates: Rates):
    """その日の残りのレースを、締切前チェックの時刻順にすべて処理する。

    GitHub の定期実行は数時間遅れることがあるため、時間帯で区切らず、
    起動した時点で未処理のレースを全部担当する。処理済みは state/日付.json に記録。
    """
    began = now_jst()
    hd = began.strftime("%Y%m%d")
    state = load_state(hd)
    targets, lady_ex = build_targets(hd, cfg, rates)
    log(f"本日のチェック対象 {len(targets)} レース")
    for t in targets:
        log(f"  {t.race.deadline} {t.venue}{t.race.rno}R {t.label()} 逃げ{t.nige:.1f}% 逃し{t.nigashi:.1f}%"
            + ("（処理済み）" if race_key(t) in state["done"] else ""))

    if cfg.get("morning_summary", True) and not state.get("summary"):
        send_summary(targets, lady_ex)
        state["summary"] = True
        save_state(hd, state, f"state {hd}: summary")

    mins = cfg.get("check_minutes_before", 18)
    for t in targets:
        key = race_key(t)
        if key in state["done"]:
            continue
        deadline = at(hd, t.race.deadline)
        check_at = deadline - dt.timedelta(minutes=mins)
        now = now_jst()
        if now > deadline - dt.timedelta(minutes=3):
            log(f"{t.venue}{t.race.rno}R 締切が過ぎた/近すぎるためスキップ")
            state["done"][key] = "missed"
            save_state(hd, state, f"state {hd}: {key} missed")
            continue
        if check_at > now:
            if check_at - began > MAX_RUN:
                log("実行時間の上限が近いので、残りは次の実行に任せます")
                break
            log(f"{t.venue}{t.race.rno}R のチェックまで待機（{check_at:%H:%M}）")
            time.sleep((check_at - now).total_seconds())
        verdict, text = check(t, hd, cfg)
        if verdict == "notify":
            notify(title_of(t), text, click=kyoteibiyori_url(t.race.jcd, t.race.rno, hd))
        else:
            log(f"除外 {t.venue}{t.race.rno}R：{text}")
        state["done"][key] = verdict
        save_state(hd, state, f"state {hd}: {key} {verdict}")
    log("処理を終了します")


def send_summary(targets: list[Target], lady_ex: list[str], prefix: str = ""):
    if targets:
        lines = [f"{t.race.deadline} {t.venue}{t.race.rno}R {t.label()}" for t in targets]
        msg = "締切前に展示・オッズを確認し、条件を満たしたものだけ通知します\n" + "\n".join(lines)
        title = f"{prefix}本日のチェック対象 {len(targets)} レース"
    else:
        msg, title = "条件に合うレースはありませんでした", f"{prefix}本日の対象レースはありません"
    if lady_ex:
        msg += "\n女子戦のため除外：" + "、".join(lady_ex)
    notify(title, msg, priority=3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schedule", default="")
    ap.add_argument("--mode", default="auto")
    ap.add_argument("--date", default="")
    a = ap.parse_args()
    cfg = load_config()

    if a.mode == "test-notify":
        notify("[テスト] ボートレース通知", "通知のテストです。このメッセージが届いていれば設定はOKです。")
        return

    rates = Rates(cfg.get("stats_days", 180))
    log(f"逃げ率・逃し率の集計対象 {rates.count} レース")
    if rates.count == 0:
        notify("⚠ボートレース通知：統計データがありません",
               "GitHub の Actions で「stats」を手動実行して、逃げ率・逃し率のデータを作ってください。")
        return

    if a.mode == "list":  # 今日の対象をログに出すだけ（通知しない）
        build_targets(now_jst().strftime("%Y%m%d"), cfg, rates)
        return

    if a.mode == "dryrun-today":
        hd = now_jst().strftime("%Y%m%d")
        targets, lady_ex = build_targets(hd, cfg, rates)
        send_summary(targets, lady_ex, prefix="[テスト] ")
        return

    if a.mode == "test-past":
        hd = a.date or (now_jst() - dt.timedelta(days=1)).strftime("%Y%m%d")
        targets, lady_ex = build_targets(hd, cfg, rates)
        lines = []
        for t in targets:
            verdict, text = check(t, hd, cfg, wait=False)
            mark = "◎通知" if verdict == "notify" else "×除外"
            lines.append(f"{t.race.deadline} {t.venue}{t.race.rno}R {t.label()} → {mark}"
                         + ("" if verdict == "notify" else f"（{text}）"))
            log(lines[-1])
        msg = "\n".join(lines) or "条件に合うレースはありませんでした"
        if lady_ex:
            msg += "\n女子戦のため除外：" + "、".join(lady_ex)
        notify(f"[テスト] {hd} の判定結果 {len(targets)} レース", msg, priority=3)
        return

    run_auto(cfg, rates)

if __name__ == "__main__":
    main()
