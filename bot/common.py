"""共通処理：設定の読み込み、時刻、HTTP取得、通知。"""
from __future__ import annotations

import datetime as dt
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parent.parent

VENUES = {
    "01": "桐生", "02": "戸田", "03": "江戸川", "04": "平和島", "05": "多摩川", "06": "浜名湖",
    "07": "蒲郡", "08": "常滑", "09": "津", "10": "三国", "11": "びわこ", "12": "住之江",
    "13": "尼崎", "14": "鳴門", "15": "丸亀", "16": "児島", "17": "宮島", "18": "徳山",
    "19": "下関", "20": "若松", "21": "芦屋", "22": "福岡", "23": "唐津", "24": "大村",
}
NAME_TO_JCD = {v: k for k, v in VENUES.items()}

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
_last_request = 0.0
REQUEST_INTERVAL = 1.2  # 公式サイトへの負荷を抑えるため、リクエスト間隔をあける


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def now_jst() -> dt.datetime:
    return dt.datetime.now(JST)


def log(*args):
    print(now_jst().strftime("%H:%M:%S"), *args, flush=True)


def http_get(url: str, *, binary: bool = False, retries: int = 3, timeout: int = 30):
    global _last_request
    for attempt in range(1, retries + 1):
        wait = REQUEST_INTERVAL - (time.time() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.time()
        try:
            r = _session.get(url, timeout=timeout)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            if binary:
                return r.content
            r.encoding = r.apparent_encoding if not r.encoding or r.encoding.lower() == "iso-8859-1" else r.encoding
            return r.text
        except requests.RequestException as e:
            log(f"取得失敗 ({attempt}/{retries}) {url}: {e}")
            time.sleep(3 * attempt)
    return None


def notify(title: str, message: str, click: str | None = None, priority: int = 4, dry_run: bool = False):
    """ntfy.sh にプッシュ通知を送る。トピック名は環境変数 NTFY_TOPIC（GitHubのSecret）。"""
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    log("通知:", title, "|", message.replace("\n", " / "))
    if dry_run:
        return
    if not topic:
        log("NTFY_TOPIC が設定されていないため通知を送れません")
        return
    payload = {"topic": topic, "title": title, "message": message, "priority": priority, "tags": ["speedboat"]}
    if click:
        payload["click"] = click
    try:
        requests.post("https://ntfy.sh/", json=payload, timeout=20).raise_for_status()
    except requests.RequestException as e:
        log("通知の送信に失敗:", e)


def kyoteibiyori_url(jcd: str, rno: int, hd: str) -> str:
    return f"https://kyoteibiyori.com/race_shusso.php?place_no={int(jcd)}&race_no={rno}&hiduke={hd}&slider=1"
