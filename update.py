#!/usr/bin/env python3
"""Scheduled ESPN pull -> normalize -> push to Supabase league_data.
Runs in GitHub Actions. Reuses the same normalizer as the local rebuild.
No third-party deps (urllib + stdlib only)."""
import os, json, datetime, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LEAGUE_ID = os.environ.get("LEAGUE_ID", "848479247")
SEASON = os.environ.get("SEASON", "2026")
SB_URL = os.environ["SUPABASE_URL"].rstrip("/")
SB_KEY = os.environ["SUPABASE_KEY"]  # service_role secret, injected as a GitHub Actions secret

def fetch_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))

def warn(msg):
    # shows as a yellow annotation on the Actions run; the run still succeeds
    print("::warning::" + msg)

LEAGUE_BASE = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}"
ESPN = (LEAGUE_BASE +
        "?view=mSettings&view=mTeam&view=mRoster&view=mMatchup&view=mMatchupScore&view=mStandings&view=mDraftDetail&view=mMembers&view=mSchedule")
d = fetch_json(ESPN)
# Fail the run (=> GitHub emails a failure notice) rather than write an empty league over good data.
if not d.get("teams") or not d.get("schedule"):
    raise SystemExit("ESPN returned no teams/schedule - not writing to Supabase")

POS = {1:'QB',2:'RB',3:'WR',4:'TE',5:'K',16:'DST'}
LINEUP = {0:'QB',1:'TQB',2:'RB',3:'RB/WR',4:'WR',5:'WR/TE',6:'TE',7:'OP',16:'DST',17:'K',18:'P',19:'HC',20:'BE',21:'IR',23:'FLEX'}
PRO = {0:'FA',1:'ATL',2:'BUF',3:'CHI',4:'CIN',5:'CLE',6:'DAL',7:'DEN',8:'DET',9:'GB',10:'TEN',11:'IND',12:'KC',13:'LV',14:'LAR',15:'MIA',16:'MIN',17:'NE',18:'NO',19:'NYG',20:'NYJ',21:'PHI',22:'ARI',23:'PIT',24:'LAC',25:'SF',26:'SEA',27:'TB',28:'WSH',29:'CAR',30:'JAX',33:'BAL',34:'HOU'}
ACQ = {0:'DRAFT',1:'?',2:'ADD',3:'TRADE',None:'-'}
STAT = {3:'Pass Yds',4:'Pass TD',19:'2PT Pass',20:'Pass Int',24:'Rush Yds',25:'Rush TD',26:'2PT Rush',42:'Rec Yds',43:'Rec TD',44:'2PT Rec',53:'Reception',72:'Fum Lost',63:'Fum Ret TD',74:'FG 50+',77:'FG 40-49',80:'FG <40',82:'FG Miss',86:'PAT Made',88:'PAT Miss',89:'Def 0 Pts All',90:'Def 1-6 All',91:'Def 7-13 All',92:'Def 14-17 All',93:'Def Blk TD',95:'Def Int',96:'Def Fum Rec',97:'Def Blk',98:'Def Safety',99:'Def Sack',101:'KR TD',102:'PR TD',103:'INT Ret TD',104:'FR Ret TD',105:'Def TD',106:'Def Fum Force',120:'Def Pts All',121:'Def 28-34 All',122:'Def 21-27 All',123:'Def 35-45 All',127:'Def Yds All',134:'Def 46-59 All',135:'Def 60+ All',201:'Def 2PT Ret',205:'KR/PR Yds',8:'Rush Att'}
NICK = {1:'Falcons',2:'Bills',3:'Bears',4:'Bengals',5:'Browns',6:'Cowboys',7:'Broncos',8:'Lions',9:'Packers',10:'Titans',11:'Colts',12:'Chiefs',13:'Raiders',14:'Rams',15:'Dolphins',16:'Vikings',17:'Patriots',18:'Saints',19:'Giants',20:'Jets',21:'Eagles',22:'Cardinals',23:'Steelers',24:'Chargers',25:'49ers',26:'Seahawks',27:'Buccaneers',28:'Commanders',29:'Panthers',30:'Jaguars',33:'Ravens',34:'Texans'}

members = {m['id']: m for m in d.get("members", [])}
pmap = {}
for t in d.get("teams", []):
    for e in (t.get("roster") or {}).get("entries", []):
        pl = e.get("playerPoolEntry", {}).get("player", {})
        if pl.get("id"):
            pmap[pl["id"]] = {"name": pl.get("fullName"), "pos": POS.get(pl.get("defaultPositionId"), '?'), "proTeam": PRO.get(pl.get("proTeamId"), '?')}
for pid, info in {4685720:("Bryce Young","QB","CAR"), 4426385:("Zach Charbonnet","RB","SEA"), 3055899:("Harrison Butker","K","KC"), -16019:("Giants D/ST","DST","NYG")}.items():
    pmap.setdefault(pid, {"name": info[0], "pos": info[1], "proTeam": info[2]})
try:
    CACHE = json.load(open(HERE + "/player_names.json", encoding="utf-8"))
except Exception:
    CACHE = {}
for pid, info in pmap.items():
    if info.get("name"): CACHE[str(pid)] = info
def resolve(pid):
    if pid in pmap: return pmap[pid]
    c = CACHE.get(str(pid))
    if c and c.get("name"): return c
    if pid is not None and pid < 0:
        tid = -pid - 16000
        if tid in NICK: return {"name": NICK[tid] + " D/ST", "pos": "DST", "proTeam": PRO.get(tid, '?')}
    return {"name": "Player " + str(pid), "pos": "?", "proTeam": "?"}

def tn(t): return t.get("name") or ((t.get("location", "") + " " + t.get("nickname", "")).strip())
def owner_of(t):
    parts = []
    for o in t.get("owners", []):
        m = members.get(o, {})
        nm = (m.get("firstName", "") + " " + m.get("lastName", "")).strip() or m.get("displayName", "")
        if nm: parts.append(nm)
    return ", ".join(parts)

teams = [{"id": t["id"], "name": tn(t), "abbrev": t.get("abbrev", "").strip(), "owner": owner_of(t), "divisionId": t.get("divisionId"), "draftProjRank": t.get("draftDayProjectedRank")} for t in d["teams"]]
scoring = [{"statId": si["statId"], "points": si["points"], "label": STAT.get(si["statId"], "Stat " + str(si["statId"]))} for si in d['settings']['scoringSettings'].get("scoringItems", []) if si.get("points")]
draft = []
for p in d.get("draftDetail", {}).get("picks", []):
    pi = resolve(p["playerId"])
    draft.append({"overall": p["overallPickNumber"], "round": p["roundId"], "pickInRound": p["roundPickNumber"], "teamId": p["teamId"], "playerId": p["playerId"], "name": pi["name"], "pos": pi["pos"], "proTeam": pi["proTeam"], "keeper": p.get("keeper", False)})
schedule = []
for m in d["schedule"]:
    h = m.get("home", {}); a = m.get("away", {})
    schedule.append({"id": m["id"], "week": m["matchupPeriodId"], "homeTeamId": h.get("teamId"), "awayTeamId": a.get("teamId"),
        "homeScore": round(h.get("totalPoints", 0) or 0, 2), "awayScore": round(a.get("totalPoints", 0) or 0, 2),
        "homeLive": round((h.get("totalPointsLive") if h.get("totalPointsLive") is not None else h.get("totalPoints", 0)) or 0, 2),
        "awayLive": round((a.get("totalPointsLive") if a.get("totalPointsLive") is not None else a.get("totalPoints", 0)) or 0, 2),
        "winner": m.get("winner", "UNDECIDED")})
cur = d.get("scoringPeriodId")
rosters = {}
for t in d["teams"]:
    lst = []
    for e in (t.get("roster") or {}).get("entries", []):
        pl = e.get("playerPoolEntry", {}).get("player", {})
        live = 0
        for st in pl.get("stats", []):
            if st.get("scoringPeriodId") == cur and st.get("statSourceId") == 0:
                live = round(st.get("appliedTotal", 0) or 0, 2)
        lst.append({"playerId": e.get("playerId"), "name": pl.get("fullName"), "pos": POS.get(pl.get("defaultPositionId"), '?'), "proTeam": PRO.get(pl.get("proTeamId"), '?'), "slot": LINEUP.get(e.get("lineupSlotId"), str(e.get("lineupSlotId"))), "acq": ACQ.get(e.get("acquisitionType"), '-'), "injury": pl.get("injuryStatus"), "live": live})
    rosters[str(t["id"])] = lst
records = {}
for t in d["teams"]:
    r = (t.get("record") or {}).get("overall", {})
    records[str(t["id"])] = {"wins": r.get("wins", 0), "losses": r.get("losses", 0), "ties": r.get("ties", 0), "pf": round(r.get("pointsFor", 0) or 0, 2), "pa": round(r.get("pointsAgainst", 0) or 0, 2), "streakType": r.get("streakType", "NONE"), "streakLength": r.get("streakLength", 0), "pct": r.get("percentage", 0), "playoffSeed": t.get("playoffSeed"), "projRank": t.get("currentProjectedRank")}
# FAAB
acq = d['settings'].get('acquisitionSettings', {})
faab_budget = acq.get('acquisitionBudget', 100)
faab_teams = []
for t in d["teams"]:
    tc = t.get("transactionCounter") or {}
    spent = tc.get("acquisitionBudgetSpent", 0) or 0
    picks = []
    for e in (t.get("roster") or {}).get("entries", []):
        at = e.get("acquisitionType")
        if at in (0, 3, "DRAFT", "TRADE", "DRAFT_KEEPER") or at is None: continue
        info = resolve(e.get("playerId"))
        label = ACQ.get(at, str(at)) if isinstance(at, int) else str(at).title()
        picks.append({"playerId": e.get("playerId"), "name": info["name"], "pos": info["pos"], "proTeam": info["proTeam"], "date": e.get("acquisitionDate"), "acq": label})
    picks.sort(key=lambda x: (x["date"] or 0), reverse=True)
    faab_teams.append({"teamId": t["id"], "spent": spent, "remaining": faab_budget - spent, "adds": tc.get("acquisitions", 0), "drops": tc.get("drops", 0), "pickups": picks})
faab = {"budget": faab_budget, "usingFaab": acq.get('isUsingAcquisitionBudget', False), "teams": faab_teams}

# NFL kickoff times (epoch ms) for the site's "next kickoff" countdown. Best-effort: the
# site falls back to standard TNF/Sunday/MNF slots if this is missing.
nfl_kickoffs, pro_games, bye_weeks = [], {}, {}
try:
    pro = fetch_json(f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{SEASON}?view=proTeamSchedules_wl")
    seen = set()
    for pt in (pro.get("settings") or {}).get("proTeams", []):
        b = pt.get("byeWeek")
        if b: bye_weeks[PRO.get(pt.get("id"), str(pt.get("id")))] = b
        wk_games = (pt.get("proGamesByScoringPeriod") or {}).get(str(cur)) or []
        if wk_games and wk_games[0].get("date"):
            pro_games[PRO.get(pt.get("id"), str(pt.get("id")))] = int(wk_games[0]["date"])
        for games in (pt.get("proGamesByScoringPeriod") or {}).values():
            for g in games:
                if g.get("id") in seen or not g.get("date"): continue
                seen.add(g.get("id"))
                nfl_kickoffs.append(int(g["date"]))
    nfl_kickoffs.sort()
except Exception as e:
    warn(f"NFL schedule fetch failed (site uses fallback slots): {e}")

# This week's NFL games for the matchup screen: opponent, stadium, indoor flag and ESPN's
# forecast (AccuWeather, published a few days ahead). Best-effort: the site just shows no weather.
nfl_games = {}
try:
    sbd = fetch_json(f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={SEASON}&seasontype=2&week={cur}")
    for ev in sbd.get("events", []):
        c = (ev.get("competitions") or [{}])[0]
        v, sides = c.get("venue") or {}, c.get("competitors") or []
        wx = ev.get("weather") or c.get("weather") or {}
        for t in sides:
            ab = (t.get("team") or {}).get("abbreviation")
            if not ab: continue
            opp = next(((o.get("team") or {}).get("abbreviation") for o in sides if o is not t), None)
            nfl_games[ab] = {"opp": opp, "home": t.get("homeAway") == "home", "venue": v.get("fullName"),
                             "city": (v.get("address") or {}).get("city"), "indoor": bool(v.get("indoor")),
                             "wx": wx.get("displayValue"), "temp": wx.get("temperature")}
except Exception as e:
    warn(f"NFL scoreboard fetch failed (no weather on the site): {e}")

def sb_get_current():
    req = urllib.request.Request(SB_URL + "/rest/v1/league_data?id=eq.current&select=data",
        headers={"apikey": SB_KEY, "Authorization": "Bearer " + SB_KEY})
    with urllib.request.urlopen(req, timeout=90) as r:
        rows = json.loads(r.read().decode("utf-8"))
    return (rows[0].get("data") or {}) if rows else {}
try:
    prev = sb_get_current()
except Exception as e:
    prev = None
    print("could not read current Supabase data:", e)

# Weekly box scores: every rostered player's lineup slot, actual and projected points per week.
# Powers bench points, pickup value, win probability and the win calculator on the site.
# Finalized weeks are reused from the last write; the current and previous week are re-pulled
# every run (live scoring + stat corrections).
weekly, players, match_proj = {}, {}, {}
prev_weekly = (prev or {}).get("weekly") or {}
final_weeks = {w for w in {m["week"] for m in schedule} if all(x["winner"] != "UNDECIDED" for x in schedule if x["week"] == w)}
for w in range(1, (cur or 0) + 1):
    if str(w) in prev_weekly and w in final_weeks and w < (cur or 0) - 1:
        weekly[str(w)] = prev_weekly[str(w)]
        continue
    try:
        bx = fetch_json(LEAGUE_BASE + f"?view=mMatchupScore&view=mBoxscore&scoringPeriodId={w}",
            {"User-Agent": "Mozilla/5.0", "X-Fantasy-Filter": json.dumps({"schedule": {"filterMatchupPeriodIds": {"value": [w]}}})})
        wk = {}
        for m in bx.get("schedule", []):
            for side in ("home", "away"):
                sd = m.get(side) or {}
                if sd.get("teamId") is None: continue
                proj_live = sd.get("totalProjectedPointsLive", sd.get("totalProjectedPoints"))
                if proj_live is not None:
                    match_proj[(m["id"], side)] = round(proj_live, 2)
                rows = []
                for e in ((sd.get("rosterForCurrentScoringPeriod") or {}).get("entries") or []):
                    pe = e.get("playerPoolEntry") or {}
                    pl = pe.get("player") or {}
                    pid = e.get("playerId")
                    proj = 0
                    for st in pl.get("stats", []):
                        if st.get("scoringPeriodId") == w and st.get("statSourceId") == 1:
                            proj = st.get("appliedTotal", 0) or 0
                    rows.append([pid, e.get("lineupSlotId"), round(pe.get("appliedStatTotal", 0) or 0, 2), round(proj, 1)])
                    if pid is not None and str(pid) not in players:
                        info = resolve(pid) if pid in pmap or str(pid) in CACHE else {"name": pl.get("fullName"), "pos": POS.get(pl.get("defaultPositionId"), '?'), "proTeam": PRO.get(pl.get("proTeamId"), '?')}
                        if not info.get("name"): info = resolve(pid)
                        players[str(pid)] = [info["name"], info["pos"], info["proTeam"]]
                wk[str(sd["teamId"])] = rows
        weekly[str(w)] = wk
    except Exception as e:
        warn(f"week {w} box score fetch failed: {e}")
        if str(w) in prev_weekly: weekly[str(w)] = prev_weekly[str(w)]
# players referenced by reused weeks
for p in ((prev or {}).get("players") or {}).items():
    players.setdefault(p[0], p[1])
# weeks reused above keep the projections saved with them last time
for m in ((prev or {}).get("schedule") or []):
    for side in ("home", "away"):
        if m.get(side + "Proj") is not None:
            match_proj.setdefault((m["id"], side), m[side + "Proj"])
for m in schedule:
    hp, ap = match_proj.get((m["id"], "home")), match_proj.get((m["id"], "away"))
    if hp is not None: m["homeProj"] = hp
    if ap is not None: m["awayProj"] = ap

# Stat corrections: a player's points changing in a week that was already final on the last run.
# Kept as a running log (newest last, capped) for the site's Stat Corrections page.
corrections = list((prev or {}).get("corrections") or [])
_prev_sched = (prev or {}).get("schedule") or []
_prev_final = {w for w in {m["week"] for m in _prev_sched} if all(x.get("winner") != "UNDECIDED" for x in _prev_sched if x["week"] == w)}
_now_ms = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
for w in sorted(final_weeks & _prev_final):
    old_wk, new_wk = prev_weekly.get(str(w)) or {}, weekly.get(str(w)) or {}
    for tid, rows in new_wk.items():
        old = {r[0]: r for r in old_wk.get(tid, [])}
        for r in rows:
            o = old.get(r[0])
            if o and abs((o[2] or 0) - (r[2] or 0)) >= 0.05:
                info = players.get(str(r[0])) or ["Player %s" % r[0], "?", "?"]
                corrections.append({"ts": _now_ms, "week": w, "tid": int(tid), "pid": r[0], "name": info[0], "pos": info[1],
                                    "old": o[2], "new": r[2], "starter": r[1] not in (20, 21)})
corrections = corrections[-500:]

hist = json.load(open(HERE + "/history.json", encoding="utf-8"))
finals = any(m["winner"] != "UNDECIDED" for m in schedule)
liveWeek = None
for m in schedule:
    if m["week"] == cur and m["winner"] == "UNDECIDED" and (m["homeLive"] > 0 or m["awayLive"] > 0):
        liveWeek = cur; break
# Win-probability swing history: live-score samples per current-week matchup, powering the
# site's in-game win% graph. Appends a point only when a live score actually moves (so writes
# stay sparse) and only for the current week (the graph reads the current week). [ts, hLive,
# aLive, hProj, aProj]; the site reconstructs each point's win% with its own model. Best-effort:
# the site just hides the graph if this is missing, so never let it break the pipeline.
wp_hist = {}
try:
    prev_wph = (prev or {}).get("wpHistory") or {}
    for m in schedule:
        if m["week"] != cur or m.get("winner") != "UNDECIDED":
            continue
        hp, ap = m.get("homeProj"), m.get("awayProj")
        if hp is None or ap is None:
            continue
        arr = list(prev_wph.get(str(m["id"])) or [])
        hl, al = m.get("homeLive", 0) or 0, m.get("awayLive", 0) or 0
        if hl <= 0 and al <= 0:
            if arr:
                wp_hist[str(m["id"])] = arr
            continue
        last = arr[-1] if arr else None
        if (not last) or round(last[1], 1) != round(hl, 1) or round(last[2], 1) != round(al, 1):
            arr.append([_now_ms, round(hl, 2), round(al, 2), round(hp, 2), round(ap, 2)])
        wp_hist[str(m["id"])] = arr[-240:]
except Exception as e:
    warn(f"win-prob history failed: {e}")
out = {"meta": {"leagueId": d["id"], "leagueName": d['settings']['name'], "season": d["seasonId"], "size": d['settings']['size'], "scoringType": d['settings']['scoringSettings'].get("scoringType"), "ppr": 0.5, "regularSeasonWeeks": d['settings']['scheduleSettings'].get("matchupPeriodCount"), "playoffTeamCount": d['settings']['scheduleSettings'].get("playoffTeamCount"), "currentScoringPeriod": cur, "liveWeek": liveWeek, "divisions": [{"id": dv.get("id"), "name": dv.get("name")} for dv in d['settings'].get("scheduleSettings", {}).get("divisions", [])], "lastUpdated": datetime.datetime.now(datetime.timezone.utc).isoformat(), "historySpan": [hist["seasons"][0]["year"], hist["seasons"][-1]["year"]], "nflKickoffs": nfl_kickoffs, "proGames": pro_games, "nflGames": nfl_games, "byeWeeks": bye_weeks}, "teams": teams, "scoring": scoring, "draft": draft, "schedule": schedule, "rosters": rosters, "records": records, "weeklyPlayed": finals, "history": hist, "faab": faab, "weekly": weekly, "players": players, "corrections": corrections, "wpHistory": wp_hist}

# Only write when something actually changed (ignoring the timestamp itself), so an open
# page doesn't "refresh" when nothing moved and we don't poke the DB pointlessly off-peak.
def _sig(obj):
    o = json.loads(json.dumps(obj))
    if isinstance(o.get("meta"), dict):
        o["meta"].pop("lastUpdated", None)
    return json.dumps(o, sort_keys=True)

changed = True
if prev and _sig(prev) == _sig(out):
    changed = False
    # keep the prior timestamp so "Updated X" reflects the last REAL change, not this poll
    prev_ts = prev.get("meta", {}).get("lastUpdated")
    if prev_ts:
        out["meta"]["lastUpdated"] = prev_ts
elif prev is None:
    print("change-check skipped (could not read Supabase), writing anyway")

if not changed:
    print("No change since last pull — skipped write. | week:", cur, "| liveWeek:", liveWeek)
else:
    body = json.dumps({"id": "current", "data": out}).encode("utf-8")
    req = urllib.request.Request(SB_URL + "/rest/v1/league_data?on_conflict=id", data=body, method="POST",
        headers={"apikey": SB_KEY, "Authorization": "Bearer " + SB_KEY, "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"})
    with urllib.request.urlopen(req, timeout=90) as r:
        print("Supabase upsert:", r.status, "| week:", cur, "| liveWeek:", liveWeek, "| finals:", finals, "| teams:", len(teams))

# Heartbeat: a tiny separate row written every run, so the site can show "checked 1 min ago"
# even when nothing changed (the league row above is only written on real changes).
try:
    hb = json.dumps({"id": "heartbeat", "data": {"checked": datetime.datetime.now(datetime.timezone.utc).isoformat()}}).encode("utf-8")
    req = urllib.request.Request(SB_URL + "/rest/v1/league_data?on_conflict=id", data=hb, method="POST",
        headers={"apikey": SB_KEY, "Authorization": "Bearer " + SB_KEY, "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"})
    urllib.request.urlopen(req, timeout=30).close()
except Exception as e:
    warn(f"heartbeat write failed: {e}")
