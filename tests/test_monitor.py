from bot import monitor, official
from bot.official import Race, Racer

class FakeRates:
    count = 1
    def nige(self, t): return {"1001":(80.0,30),"1003":(60.0,30)}.get(t,(None,0))
    def nigashi(self, t): return {"2002":(40.0,20)}.get(t,(None,0))

CFG = dict(nige_min=70, nigashi_min=35, min_samples=0, target_race12=True, target_yusho=True, target_junyu=True,
           exclude_lady_races=True, kikaku={"児島":[4]}, require_wakunari=True, exhibit_retry_times=0,
           odds_first="1-2-3", odds_second="1-3-2", odds_first_max=10.0, notify_unverified=True)

def mk(rno, t1="1001", lady=False):
    rs=[Racer(t1,"A","A1",lady),Racer("2002","B","A2",lady)]+[Racer(f"30{i}0","C","B1",lady) for i in range(4)]
    return Race("16", rno, f"{8+rno}:00", rs)

def test_build_and_check(monkeypatch):
    monkeypatch.setattr(official,"fetch_jcds",lambda hd:["16"])
    monkeypatch.setattr(official,"fetch_races",lambda j,hd:[mk(3),mk(4),mk(10),mk(11),mk(12),mk(12,"1003")])
    monkeypatch.setattr(official,"fetch_race_type",lambda j,r,hd:{10:"予選",11:"準優勝戦",12:"優勝戦"}.get(r,"予選"))
    t,lady = monitor.build_targets("20261116",CFG,FakeRates())
    got=[(x.race.rno,x.kinds) for x in t]
    assert got==[(4,["企画レース"]),(11,["準優勝戦"]),(12,["12R","優勝戦"])], got
    monkeypatch.setattr(official,"fetch_exhibition_order",lambda j,r,hd:[1,2,3,4,5,6])
    monkeypatch.setattr(official,"fetch_odds3t",lambda j,r,hd:{"1-2-3":4.5,"1-3-2":6.0,"1-2-4":9.0})
    v,txt=monitor.check(t[0],"20261116",CFG,wait=False); assert v=="notify", txt
    monkeypatch.setattr(official,"fetch_odds3t",lambda j,r,hd:{"1-2-3":11.0,"1-3-2":12.0})
    v,txt=monitor.check(t[0],"20261116",CFG,wait=False); assert v=="exclude" and "11.0" in txt
    monkeypatch.setattr(official,"fetch_exhibition_order",lambda j,r,hd:[1,2,6,3,4,5])
    v,txt=monitor.check(t[0],"20261116",CFG,wait=False); assert v=="exclude" and "枠なり" in txt
    monkeypatch.setattr(official,"fetch_races",lambda j,hd:[mk(12,lady=True)])
    t,lady = monitor.build_targets("20261116",CFG,FakeRates()); assert t==[] and lady==["児島12R"]
