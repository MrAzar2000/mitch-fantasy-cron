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

ESPN = (f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}"
        "?view=mSettings&view=mTeam&view=mRoster&view=mMatchup&view=mMatchupScore&view=mStandings&view=mDraftDetail&view=mMembers&view=mSchedule")
d = fetch_json(ESPN)

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

hist = json.load(open(HERE + "/history.json", encoding="utf-8"))
finals = any(m["winner"] != "UNDECIDED" for m in schedule)
liveWeek = None
for m in schedule:
    if m["week"] == cur and m["winner"] == "UNDECIDED" and (m["homeLive"] > 0 or m["awayLive"] > 0):
        liveWeek = cur; break
out = {"meta": {"leagueId": d["id"], "leagueName": d['settings']['name'], "season": d["seasonId"], "size": d['settings']['size'], "scoringType": d['settings']['scoringSettings'].get("scoringType"), "ppr": 0.5, "regularSeasonWeeks": d['settings']['scheduleSettings'].get("matchupPeriodCount"), "playoffTeamCount": d['settings']['scheduleSettings'].get("playoffTeamCount"), "currentScoringPeriod": cur, "liveWeek": liveWeek, "divisions": [{"id": dv.get("id"), "name": dv.get("name")} for dv in d['settings'].get("scheduleSettings", {}).get("divisions", [])], "lastUpdated": datetime.datetime.now(datetime.timezone.utc).isoformat(), "historySpan": [hist["seasons"][0]["year"], hist["seasons"][-1]["year"]]}, "teams": teams, "scoring": scoring, "draft": draft, "schedule": schedule, "rosters": rosters, "records": records, "weeklyPlayed": finals, "history": hist, "faab": faab}

# push to Supabase
body = json.dumps({"id": "current", "data": out}).encode("utf-8")
req = urllib.request.Request(SB_URL + "/rest/v1/league_data?on_conflict=id", data=body, method="POST",
    headers={"apikey": SB_KEY, "Authorization": "Bearer " + SB_KEY, "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"})
with urllib.request.urlopen(req, timeout=90) as r:
    print("Supabase upsert:", r.status, "| week:", cur, "| liveWeek:", liveWeek, "| finals:", finals, "| teams:", len(teams))
