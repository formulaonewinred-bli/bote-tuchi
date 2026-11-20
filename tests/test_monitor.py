from bot import monitor, official
from bot.official import Race, Racer

class FakeRates:
    count = 1
    def nige(self, t): return {"1001":(80.0,30),"1003":(60.0,30)}.get(t,(None,0))
    def nigashi(self, t): return {"2002":(40.0,20)}.get(t,(None,0))

CFG = dict(nige_min=70, nigashi_min=35, min_samples=0, target_race12=True, target_yusho=True, target_junyu=True,
           exclude_lady_races=True, exclude_grades=["G1"], kikaku={"児島":[4]}, require_wakunari=True, exhibit_retry_times=0,
           odds_first="1-2-3", odds_second="1-3-2", odds_first_max=10.0, notify_unverified=True)

def mk(rno, t1="1001", lady=False):
    rs=[Racer(t1,"A","A1",lady),Racer("2002","B","A2",lady)]+[Racer(f"30{i}0","C","B1",lady) for i in range(4)]
    return Race("16", rno, f"{8+rno}:00", rs)

def test_build_and_check(monkeypatch):
    monkeypatch.setattr(official,"fetch_jcds",lambda hd:[("16","is-ippan"),("11","is-G1b")])
    monkeypatch.setattr(official,"fetch_races",lambda j,hd:[mk(3),mk(4),mk(10),mk(11),mk(12),mk(12,"1003")])
    monkeypatch.setattr(official,"fetch_race_type",lambda j,r,hd:{10:"予選",11:"準優勝戦",12:"優勝戦"}.get(r,"予選"))
    cfg2 = dict(CFG, target_race12=False, junyu_12r_only=True)
    monkeypatch.setattr(official,"fetch_race_type",lambda j,r,hd:{10:"予選",11:"準優勝戦",12:"準優勝戦"}.get(r,"予選"))
    t2,_ = monitor.build_targets("20261116",cfg2,FakeRates())
    assert [(x.race.rno,x.kinds) for x in t2]==[(4,["企画レース"]),(12,["準優勝戦"])], [(x.race.rno,x.kinds) for x in t2]
    monkeypatch.setattr(official,"fetch_race_type",lambda j,r,hd:{10:"予選",11:"準優勝戦",12:"優勝戦"}.get(r,"予選"))
    t,lady = monitor.build_targets("20261116",CFG,FakeRates())
    got=[(x.race.rno,x.kinds) for x in t]
    assert got==[(4,["企画レース"]),(11,["準優勝戦"]),(12,["12R","優勝戦"])], got
    monkeypatch.setattr(official,"fetch_exhibition_order",lambda j,r,hd:[1,2,3,4,5,6])
    monkeypatch.setattr(official,"fetch_odds3t",lambda j,r,hd:{"1-2-3":4.5,"1-3-2":6.0,"1-2-4":9.0})
    v,txt=monitor.check(t[0],"20261116",CFG,wait=False); assert v=="notify", txt
    c3 = dict(CFG, odds_top2_allowed=["1-2-3","1-3-2","1-2-4"])
    monkeypatch.setattr(official,"fetch_odds3t",lambda j,r,hd:{"1-2-4":4.0,"1-3-2":6.0,"1-2-3":7.0})
    v,txt=monitor.check(t[0],"20261116",c3,wait=False); assert v=="notify", txt
    monkeypatch.setattr(official,"fetch_odds3t",lambda j,r,hd:{"1-2-3":4.0,"1-3-4":6.0,"1-2-4":7.0})
    v,txt=monitor.check(t[0],"20261116",c3,wait=False); assert v=="exclude" and "2番人気が1-3-4" in txt
    monkeypatch.setattr(official,"fetch_odds3t",lambda j,r,hd:{"1-2-3":11.0,"1-3-2":12.0})
    v,txt=monitor.check(t[0],"20261116",CFG,wait=False); assert v=="exclude" and "11.0" in txt
    monkeypatch.setattr(official,"fetch_exhibition_order",lambda j,r,hd:[1,2,6,3,4,5])
    v,txt=monitor.check(t[0],"20261116",CFG,wait=False); assert v=="exclude" and "枠なり" in txt
    monkeypatch.setattr(official,"fetch_races",lambda j,hd:[mk(12,lady=True)])
    t,lady = monitor.build_targets("20261116",CFG,FakeRates()); assert t==[] and lady==["児島12R"]

def test_run_auto_state(monkeypatch, tmp_path):
    import datetime as dt
    monkeypatch.setattr(monitor, "STATE_DIR", tmp_path)
    monkeypatch.setattr(monitor, "CHECKS", tmp_path / "checks.csv")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    now = dt.datetime(2026,11,16,12,0,tzinfo=monitor.JST)
    monkeypatch.setattr(monitor, "now_jst", lambda: now)
    monkeypatch.setattr(monitor.time, "sleep", lambda s: None)
    r_past = mk(12); r_past.deadline = "11:00"
    r_soon = mk(11); r_soon.deadline = "12:10"
    r_done = mk(4);  r_done.deadline = "12:30"
    T = [monitor.Target(r_past,["12R"],80,40), monitor.Target(r_soon,["準優勝戦"],80,40), monitor.Target(r_done,["企画レース"],80,40)]
    monkeypatch.setattr(monitor, "build_targets", lambda hd,c,r:(T,[]))
    sent=[]
    monkeypatch.setattr(monitor, "notify", lambda title,msg,**k: sent.append(title))
    monkeypatch.setattr(monitor, "check", lambda t,hd,c,**k:("notify","ok"))
    monitor.save_state("20261116", {"summary": False, "done": {"16-4":"exclude"}}, "x")
    monitor.run_auto(dict(CFG, check_minutes_before=18, morning_summary=True), FakeRates())
    st = monitor.load_state("20261116")
    assert st["summary"] and st["done"] == {"16-4":"exclude","16-12":"missed","16-11":"notify"}, st
    assert len(sent) == 2  # 一覧 + 16-11
    sent.clear(); monitor.run_auto(dict(CFG, check_minutes_before=18, morning_summary=True), FakeRates())
    assert sent == []  # 2回目は何も送らない
