from bot import official
from bot.stats import parse_k_text

def boat_row(n, st):
    return f'<tr><td colspan="3"><div class="table1_boatImage1"><span class="table1_boatImage1Number is-type{n}">{n}</span><span class="table1_boatImage1Line"></span><span class="table1_boatImage1Time">{st}</span></div></td></tr>'

def test_exhibition():
    html = '<table><thead><tr><th>スタート展示</th></tr></thead><tbody>' + ''.join(boat_row(n,'.1'+str(n)) for n in [1,2,3,4,5,6]) + '</tbody></table>'
    assert official.parse_exhibition_order(html) == [1,2,3,4,5,6]
    html2 = html.replace('is-type4">4','is-type6">6',1)
    # 前付け例
    rows = ''.join(boat_row(n,'.15') for n in [1,2,6,3,4,5])
    assert official.parse_exhibition_order('<table><tr><th>スタート展示</th></tr>'+rows+'</table>') == [1,2,6,3,4,5]
    empty = ''.join(boat_row(n,'') for n in range(1,7))
    assert official.parse_exhibition_order('<table><tr><th>スタート展示</th></tr>'+empty+'</table>') == []

def test_odds():
    # 実ページと同じ構造：1着6列 × 20行、4行ごとに2着セル
    import itertools
    rows = []
    vals = {}
    v = 1.0
    for first in range(1,7):
        others = [b for b in range(1,7) if b != first]
        for s in others:
            for t in [b for b in others if b != s]:
                vals[(first,s,t)] = round(v,1); v += 1.3
    for blk in range(5):
        for k in range(4):
            cells = []
            for first in range(1,7):
                others = [b for b in range(1,7) if b != first]
                s = others[blk]
                t = [b for b in others if b != s][k]
                if k == 0:
                    cells.append(f'<td rowspan="4">{s}</td>')
                cells.append(f'<td>{t}</td><td class="oddsPoint">{vals[(first,s,t)]}</td>')
            rows.append('<tr>'+''.join(cells)+'</tr>')
    html = '<table><thead><tr><th>x</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table>'
    odds = official.parse_odds3t(html)
    assert len(odds) == 120
    for (f,s,t),x in vals.items():
        assert odds[f"{f}-{s}-{t}"] == x

def test_raceindex_and_type():
    tr = '''<tr><td class="is-fs14 is-fBold"><a href="/owpc/pc/race/racelist?rno=12&amp;jcd=16&amp;hd=20261115">12R</a></td><td>16:21</td><td>発売終了</td>''' + ''.join(
        f'<td><div><a href="/owpc/pc/data/racersearch/profile?toban=40{i}9&amp;hd=20261115">山田　　太郎{i}</a></div><div class="{"is-lady" if i==2 else "is-empty"}"></div><div class="is-fs12">A1</div></td>' for i in range(1,7)) + '<td>x</td></tr>'
    html = f'<table><thead><tr><th>締切予定時刻</th></tr></thead><tbody>{tr}</tbody></table>'
    races = official.parse_raceindex(html, "16")
    assert races[0].rno == 12 and races[0].deadline == "16:21" and len(races[0].racers) == 6
    assert races[0].racers[0].toban == "4019" and races[0].racers[1].lady and races[0].racers[0].name == "山田太郎1"
    assert official.parse_race_type('<h3 class="title16_titleDetail__add2020">\n\t\t準優勝戦　　　\n\t\t1800m\n\t</h3>') == "準優勝戦"
    idx = ('<table><tr><td>x</td><td class="is-ippan "></td><td><a href="/owpc/pc/race/raceindex?jcd=02&amp;hd=20261115">a</a></td></tr>'
           '<tr><td>x</td><td class="is-G1b "></td><td><a href="/owpc/pc/race/raceindex?jcd=11&amp;hd=20261115">b</a></td></tr></table>')
    assert official.parse_index(idx) == [("02","is-ippan"),("11","is-G1b")]

K = """STARTK
24KBGN
大　村［成績］      11/15    ＢＴＳ杯
［払戻金］         ３連単           ３連複
          1R  1-2-3     1230    1-2-3      330

   1R       予選                 H1800m  晴　  風  北東　 1m  波　  1cm
  着 艇 登番 　選　手　名　　ﾓｰﾀｰ ﾎﾞｰﾄ 展示 進入 ｽﾀｰﾄﾀｲﾐﾝｸ ﾚｰｽﾀｲﾑ 逃げ　　　
-------------------------------------------------------------------------------
  01  1 4074 柳　生　　泰　二 50   18  6.77   1    0.15     1.50.7
  02  2 4835 山　田　　太　郎 41   22  6.80   2    0.17     1.52.1
  03  3 3333 鈴　木　　一　郎 12   33  6.79   3    0.16     1.53.0
  04  4 4444 佐　藤　　健　　 13   34  6.81   4    0.18     1.54.0
  05  5 5555 高　橋　　誠　　 14   35  6.82   5    0.19     1.55.0
  06  6 3666 田　中　　翔　　 15   36  6.83   6    0.20      .  .
        単勝     1          130
        複勝     1          110  2          150

   2R       予選                 H1800m  晴　  風  北東　 1m  波　  1cm
  着 艇 登番 　選　手　名　　ﾓｰﾀｰ ﾎﾞｰﾄ 展示 進入 ｽﾀｰﾄﾀｲﾐﾝｸ ﾚｰｽﾀｲﾑ 差し　　　
-------------------------------------------------------------------------------
  01  2 4835 山　田　　太　郎 41   22  6.80   2    0.17     1.52.1
  02  1 4074 柳　生　　泰　二 50   18  6.77   1    0.15     1.50.7
24KEND
"""

def test_k_parse():
    rows = parse_k_text(K, "20261115")
    assert rows == [
        {"date":"20261115","jcd":"24","rno":1,"c1":"4074","c2":"4835","nige":1},
        {"date":"20261115","jcd":"24","rno":2,"c1":"4074","c2":"4835","nige":0},
    ], rows
