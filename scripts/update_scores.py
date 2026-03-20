#!/usr/bin/env python3
"""
Fetch NCAA tournament game results from Kalshi API and update games.json.
Runs as a GitHub Actions cron job.

Uses a frontier-based approach:
1. Build the bracket tree from games.json winners
2. Find the "frontier" — matchups where both teams are known but no winner yet
3. Match frontier games against Kalshi events
4. Write winners/odds back, creating new game entries for R32+ as needed
"""

import json
import os
import re
import sys
import time
import base64
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Kalshi API config
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES_TICKER = "KXNCAAMBGAME"

# Tournament date range (only process games in this window)
TOURNAMENT_START = "2026-03-19"
TOURNAMENT_END = "2026-04-10"

# Paths
GAMES_JSON = Path(__file__).parent.parent / "data" / "games.json"

# Bracket structure: region → list of R1 game IDs (in bracket order, pairs feed into next round)
REGION_R1_IDS = {
    "East":    [1, 2, 3, 4, 5, 6, 7, 8],
    "West":    [9, 10, 11, 12, 13, 14, 15, 16],
    "South":   [17, 18, 19, 20, 21, 22, 23, 24],
    "Midwest": [25, 26, 27, 28, 29, 30, 31, 32],
}

# Round progression
ROUND_ORDER = ["round1", "round2", "sweet16", "elite8", "final4", "championship"]

# Final Four matchups: (region1, region2) pairs
FF_MATCHUPS = [("East", "South"), ("West", "Midwest")]

# Kalshi team abbreviation → our games.json team name
KALSHI_TEAM_MAP = {
    # East region
    "DUKE": "Duke", "SIENA": "Siena", "SIE": "Siena",
    "OSU": "Ohio St", "TCU": "TCU",
    "SJU": "St. John's", "UNI": "Northern Iowa",
    "KU": "Kansas", "KAN": "Kansas",
    "CBU": "Cal Baptist", "CALB": "Cal Baptist",
    "CALIFORNIA BAPTIST": "Cal Baptist",
    "LOU": "Louisville", "USF": "South Florida",
    "MSU": "Michigan St", "NDSU": "North Dakota St",
    "UCLA": "UCLA", "UCF": "UCF",
    "UCONN": "UConn", "CONN": "UConn", "FUR": "Furman",
    # West region
    "ARIZ": "Arizona", "ARI": "Arizona",
    "LIU": "Long Island",
    "VILL": "Villanova", "USU": "Utah St",
    "WIS": "Wisconsin", "HP": "High Point",
    "ARK": "Arkansas", "HAW": "Hawaii", "HAWA": "Hawaii",
    "BYU": "BYU", "TEX": "Texas",
    "GONZ": "Gonzaga", "GU": "Gonzaga",
    "KENN": "Kennesaw St", "KSU": "Kennesaw St",
    "MIA": "Miami (FL)", "MIAF": "Miami (FL)",
    "MIZ": "Missouri", "MOU": "Missouri",
    "PUR": "Purdue",
    "QUEEN": "Queens (N.C.)", "QU": "Queens (N.C.)",
    "QUEENS UNIVERSITY": "Queens (N.C.)",
    # South region
    "FLA": "Florida",
    "PV": "Prairie View A&M", "PVAM": "Prairie View A&M",
    "CLEM": "Clemson", "IOWA": "Iowa",
    "VAN": "Vanderbilt",
    "MCNS": "McNeese", "MCN": "McNeese",
    "NEB": "Nebraska", "TROY": "Troy",
    "UNC": "North Carolina", "VCU": "VCU",
    "ILL": "Illinois", "PENN": "Penn",
    "SMC": "Saint Mary's",
    "TXAM": "Texas A&M", "TAM": "Texas A&M",
    "HOU": "Houston", "HOUST": "Houston",
    "IDHO": "Idaho", "IDAH": "Idaho",
    # Midwest region
    "MICH": "Michigan", "HOW": "Howard",
    "UGA": "Georgia", "GA": "Georgia",
    "SLU": "Saint Louis",
    "TTU": "Texas Tech", "AKR": "Akron",
    "BAMA": "Alabama", "ALA": "Alabama",
    "HOFS": "Hofstra", "HOF": "Hofstra",
    "TENN": "Tennessee",
    "MOH": "Miami (Ohio)", "MOHI": "Miami (Ohio)",
    "SMU": "SMU",
    "UVA": "Virginia", "VA": "Virginia",
    "WRST": "Wright St",
    "UK": "Kentucky", "KEN": "Kentucky",
    "ISU": "Iowa St", "IAST": "Iowa St",
    "SC": "Santa Clara", "SCU": "Santa Clara",
    "TNST": "Tennessee St",
}

# Reverse map: our team name → set of possible Kalshi abbreviations
TEAM_TO_ABBREVS = {}
for abbr, team in KALSHI_TEAM_MAP.items():
    TEAM_TO_ABBREVS.setdefault(team, set()).add(abbr)


def kalshi_get_unauthenticated(path: str) -> dict:
    """Make unauthenticated GET request to Kalshi public API."""
    url = f"https://api.elections.kalshi.com/v1{path}"
    req = Request(url, headers={"Accept": "application/json"}, method="GET")
    with urlopen(req) as resp:
        return json.loads(resp.read().decode())


def parse_event_date(event_ticker: str) -> str | None:
    """Extract date from ticker like KXNCAAMBGAME-26MAR19TCUOSU → 2026-03-19."""
    m = re.search(r"-(\d{2})(MAR|APR)(\d{2})", event_ticker)
    if not m:
        return None
    year = f"20{m.group(1)}"
    month = "03" if m.group(2) == "MAR" else "04"
    day = m.group(3)
    return f"{year}-{month}-{day}"


def is_tournament_date(date_str: str) -> bool:
    """Check if date falls within tournament window."""
    return TOURNAMENT_START <= date_str <= TOURNAMENT_END


def normalize(name: str) -> str:
    """Normalize team name for comparison."""
    return (name.strip().rstrip(".")
            .lower()
            .replace("'", "").replace("\u02bb", "")
            .replace("hawai i", "hawaii"))


# ─── Bracket tree building ────────────────────────────────────

def build_games_by_id(games: list) -> dict:
    """Index games by ID."""
    return {g["id"]: g for g in games}


def find_game_by_teams(games: list, team1: str, team2: str, round_key: str) -> dict | None:
    """Find a game in games list matching both teams and round."""
    n1, n2 = normalize(team1), normalize(team2)
    for g in games:
        if g.get("round") != round_key:
            continue
        gt1, gt2 = normalize(g["team1"]), normalize(g["team2"])
        if (n1 == gt1 and n2 == gt2) or (n1 == gt2 and n2 == gt1):
            return g
    return None


def get_winner_seed(game: dict) -> int | None:
    """Get the seed of the winning team."""
    if not game or not game.get("winner"):
        return None
    if game["winner"] == game["team1"]:
        return game.get("seed1")
    return game.get("seed2")


def build_frontier(games: list) -> list[dict]:
    """
    Build the bracket tree and return the frontier:
    games where both teams are known but no winner yet.

    Returns list of dicts: {team1, team2, seed1, seed2, round, region, game (or None)}
    """
    by_id = build_games_by_id(games)
    frontier = []

    # Also collect games that already have results (for completeness)
    # but we only return frontier games

    # ── Regional rounds (R1 → R2 → S16 → E8) ──
    region_winners = {}  # region → E8 winner team name + seed

    for region, r1_ids in REGION_R1_IDS.items():
        # R1 games already exist in games.json
        r1_games = [by_id.get(gid) for gid in r1_ids]

        # Check R1 frontier
        for g in r1_games:
            if g and g.get("status") != "final" and g.get("team1") and g.get("team2"):
                existing = find_game_by_teams(games, g["team1"], g["team2"], "round1")
                frontier.append({
                    "team1": g["team1"], "team2": g["team2"],
                    "seed1": g.get("seed1"), "seed2": g.get("seed2"),
                    "round": "round1", "region": region,
                    "game": existing,
                })

        # Build R2 from R1 winners (pairs: 0+1, 2+3, 4+5, 6+7)
        prev_round = r1_games
        for round_key in ["round2", "sweet16", "elite8"]:
            current_round = []
            for i in range(0, len(prev_round), 2):
                g1 = prev_round[i]
                g2 = prev_round[i + 1] if i + 1 < len(prev_round) else None

                w1 = g1.get("winner") if g1 else None
                w2 = g2.get("winner") if g2 else None

                if w1 and w2:
                    # Both teams known — check if game exists or is on frontier
                    existing = find_game_by_teams(games, w1, w2, round_key)
                    if existing and existing.get("status") == "final":
                        # Game done, not on frontier
                        current_round.append(existing)
                    else:
                        # On the frontier
                        s1 = get_winner_seed(g1)
                        s2 = get_winner_seed(g2)
                        frontier.append({
                            "team1": w1, "team2": w2,
                            "seed1": s1, "seed2": s2,
                            "round": round_key, "region": region,
                            "game": existing,  # May be None for R2+
                        })
                        # Use a stub so next round can reference it
                        current_round.append(existing or {"winner": None})
                else:
                    # One or both teams unknown — not on frontier yet
                    current_round.append({"winner": None})

            prev_round = current_round

        # Track E8 winner for Final Four
        if prev_round and len(prev_round) == 1:
            e8 = prev_round[0]
            if e8 and e8.get("winner"):
                region_winners[region] = {
                    "team": e8["winner"],
                    "seed": get_winner_seed(e8),
                }

    # ── Final Four ──
    for i, (r1, r2) in enumerate(FF_MATCHUPS):
        w1_info = region_winners.get(r1)
        w2_info = region_winners.get(r2)
        if w1_info and w2_info:
            t1, t2 = w1_info["team"], w2_info["team"]
            existing = find_game_by_teams(games, t1, t2, "final4")
            if existing and existing.get("status") == "final":
                # Done
                pass
            else:
                frontier.append({
                    "team1": t1, "team2": t2,
                    "seed1": w1_info["seed"], "seed2": w2_info["seed"],
                    "round": "final4", "region": None,
                    "game": existing,
                })

    # ── Championship ──
    # Need both FF winners
    ff_winners = []
    for r1, r2 in FF_MATCHUPS:
        w1_info = region_winners.get(r1)
        w2_info = region_winners.get(r2)
        if w1_info and w2_info:
            t1, t2 = w1_info["team"], w2_info["team"]
            ff_game = find_game_by_teams(games, t1, t2, "final4")
            if ff_game and ff_game.get("winner"):
                winner_seed = get_winner_seed(ff_game)
                ff_winners.append({"team": ff_game["winner"], "seed": winner_seed})
            else:
                ff_winners.append(None)
        else:
            ff_winners.append(None)

    if len(ff_winners) == 2 and ff_winners[0] and ff_winners[1]:
        t1, t2 = ff_winners[0]["team"], ff_winners[1]["team"]
        existing = find_game_by_teams(games, t1, t2, "championship")
        if not existing or existing.get("status") != "final":
            frontier.append({
                "team1": t1, "team2": t2,
                "seed1": ff_winners[0]["seed"], "seed2": ff_winners[1]["seed"],
                "round": "championship", "region": None,
                "game": existing,
            })

    return frontier


# ─── Kalshi matching ──────────────────────────────────────────

def kalshi_title_to_teams(title: str) -> tuple[str, str] | None:
    """Parse Kalshi event title and map team names via KALSHI_TEAM_MAP."""
    parts = title.split(" at ")
    if len(parts) != 2:
        return None
    raw_away = parts[0].strip().rstrip(".")
    raw_home = parts[1].strip().rstrip(".")

    # Try mapping via KALSHI_TEAM_MAP (title often uses abbreviations like "LIU")
    away = KALSHI_TEAM_MAP.get(raw_away.upper(), raw_away)
    home = KALSHI_TEAM_MAP.get(raw_home.upper(), raw_home)

    return away, home


def match_event_to_frontier(event: dict, frontier: list) -> dict | None:
    """
    Match a Kalshi event to a frontier game by checking if both
    teams in the event title match a frontier entry.
    """
    parsed = kalshi_title_to_teams(event.get("title", ""))
    if not parsed:
        return None
    away, home = parsed
    away_n = normalize(away)
    home_n = normalize(home)

    for fg in frontier:
        t1 = normalize(fg["team1"])
        t2 = normalize(fg["team2"])

        # Both Kalshi teams must match both frontier teams (in either order)
        m1 = (away_n == t1 or away_n in t1 or t1 in away_n)
        m2 = (home_n == t2 or home_n in t2 or t2 in home_n)
        m3 = (away_n == t2 or away_n in t2 or t2 in away_n)
        m4 = (home_n == t1 or home_n in t1 or t1 in home_n)

        if (m1 and m2) or (m3 and m4):
            return fg

    return None


def resolve_winner_from_markets(markets: list) -> str | None:
    """Find the winner team name from settled Kalshi markets."""
    for market in markets:
        if market.get("result") != "yes":
            continue
        ticker = market.get("ticker", market.get("ticker_name", ""))
        suffix = ticker.rsplit("-", 1)[-1].upper() if "-" in ticker else ""
        mapped = KALSHI_TEAM_MAP.get(suffix)
        if mapped:
            return mapped
    return None


def extract_odds(markets: list) -> dict:
    """Extract win probabilities from active Kalshi markets."""
    odds = {}
    for market in markets:
        if market.get("status") != "active":
            continue
        m_ticker = market.get("ticker", market.get("ticker_name", ""))
        suffix = m_ticker.rsplit("-", 1)[-1].upper() if "-" in m_ticker else ""
        mapped = KALSHI_TEAM_MAP.get(suffix)
        price = market.get("last_price", market.get("yes_bid"))
        if mapped and price is not None:
            pct = round(price * 100) if price < 1 else round(price)
            odds[mapped] = min(pct, 99)
    return odds


# ─── ESPN scores ──────────────────────────────────────────────

# ESPN shortDisplayName → our games.json team name (only where they differ)
ESPN_TEAM_MAP = {
    "Ohio State": "Ohio St",
    "N Dakota St": "North Dakota St",
    "Michigan St": "Michigan St",
    "Hawai'i": "Hawaii",
    "High Point": "High Point",
    "Kennesaw St": "Kennesaw St",
    "Queens": "Queens (N.C.)",
    "Miami": "Miami (FL)",
    "PVAMU": "Prairie View A&M",
    "Prairie View": "Prairie View A&M",
    "Saint Mary's": "Saint Mary's",
    "N Iowa": "Northern Iowa",
    "Cal Baptist": "Cal Baptist",
    "S Florida": "South Florida",
    "Utah State": "Utah St",
    "Wright State": "Wright St",
    "Wright St": "Wright St",
    "Iowa State": "Iowa St",
    "Tennessee State": "Tennessee St",
    "Tennessee St": "Tennessee St",
    "Texas A&M": "Texas A&M",
    "Long Island": "Long Island",
    "LIU": "Long Island",
    "Santa Clara": "Santa Clara",
    "N Carolina": "North Carolina",
    "Miami (OH)": "Miami (Ohio)",
}


def espn_team_name(espn_name: str) -> str:
    """Map ESPN team name to our games.json team name."""
    return ESPN_TEAM_MAP.get(espn_name, espn_name)


def fetch_espn_scores(games: list) -> bool:
    """
    Fetch scores from ESPN for games that are final or in-progress.
    Only queries dates where we have active/final games.
    Returns True if any games were updated.
    """
    # Collect dates we need to query — from games on the frontier or recently finished
    # We'll query today and yesterday to catch games that just finished
    from datetime import timedelta
    today = datetime.now(timezone.utc)
    dates_to_check = set()
    # Check today, tomorrow (UTC is ahead of ET), and yesterday
    for delta in range(-1, 2):
        d = today + timedelta(days=delta)
        dates_to_check.add(d.strftime("%Y%m%d"))

    updated = False
    all_espn_games = []

    for date_str in sorted(dates_to_check):
        try:
            # No groups filter — we match by team name against our games.json
            url = (f"https://site.api.espn.com/apis/site/v2/sports/basketball/"
                   f"mens-college-basketball/scoreboard?dates={date_str}&limit=100")
            req = Request(url, headers={"Accept": "application/json"})
            with urlopen(req) as resp:
                data = json.loads(resp.read().decode())
            all_espn_games.extend(data.get("events", []))
        except Exception as e:
            print(f"  ESPN fetch failed for {date_str}: {e}", file=sys.stderr)

    print(f"Fetched {len(all_espn_games)} ESPN events")

    for espn_event in all_espn_games:
        status_name = espn_event.get("status", {}).get("type", {}).get("name", "")
        if status_name not in ("STATUS_FINAL", "STATUS_IN_PROGRESS"):
            continue

        comps = espn_event.get("competitions", [{}])[0]
        competitors = comps.get("competitors", [])
        if len(competitors) != 2:
            continue

        # Extract team info
        teams = []
        for c in competitors:
            t = c.get("team", {})
            name = espn_team_name(t.get("shortDisplayName", ""))
            score = c.get("score")
            try:
                score = int(score) if score else None
            except (ValueError, TypeError):
                score = None
            teams.append({"name": name, "score": score})

        # Find matching game in games.json
        for game in games:
            gt1, gt2 = normalize(game["team1"]), normalize(game["team2"])
            et1, et2 = normalize(teams[0]["name"]), normalize(teams[1]["name"])

            matched = False
            if (gt1 == et1 or gt1 in et1 or et1 in gt1) and \
               (gt2 == et2 or gt2 in et2 or et2 in gt2):
                s1, s2 = teams[0]["score"], teams[1]["score"]
                matched = True
            elif (gt1 == et2 or gt1 in et2 or et2 in gt1) and \
                 (gt2 == et1 or gt2 in et1 or et1 in gt2):
                s1, s2 = teams[1]["score"], teams[0]["score"]
                matched = True

            if matched:
                if s1 is not None and s1 != game.get("score1"):
                    game["score1"] = s1
                    updated = True
                if s2 is not None and s2 != game.get("score2"):
                    game["score2"] = s2
                    updated = True

                # Also update live status from ESPN
                if status_name == "STATUS_IN_PROGRESS" and game.get("status") != "final":
                    if game.get("status") != "live":
                        game["status"] = "live"
                        updated = True
                elif status_name == "STATUS_FINAL" and game.get("status") != "final":
                    # ESPN says final — update winner from score if Kalshi hasn't set it
                    if not game.get("winner") and s1 is not None and s2 is not None:
                        if s1 > s2:
                            game["winner"] = game["team1"]
                        elif s2 > s1:
                            game["winner"] = game["team2"]
                        game["status"] = "final"
                        game.pop("odds1", None)
                        game.pop("odds2", None)
                        updated = True
                        print(f"  ESPN final: {game['team1']} {s1} - {game['team2']} {s2}")
                break

    return updated


# ─── Main ─────────────────────────────────────────────────────

def main():
    # Load current games.json
    with open(GAMES_JSON) as f:
        games_data = json.load(f)

    games = games_data["games"]

    # Build the frontier from current bracket state
    frontier = build_frontier(games)
    print(f"Frontier has {len(frontier)} games")
    for fg in frontier:
        print(f"  {fg['round']}: {fg['team1']} vs {fg['team2']} "
              f"({'has entry' if fg['game'] else 'needs entry'})")

    if not frontier:
        print("No frontier games — tournament may be complete.")
        return False

    # Fetch events from Kalshi
    try:
        resp = kalshi_get_unauthenticated(
            f"/events?series_ticker={SERIES_TICKER}&limit=200&with_nested_markets=true"
        )
    except Exception as e:
        print(f"Failed to fetch Kalshi events: {e}", file=sys.stderr)
        sys.exit(1)

    events = resp.get("events", [])
    print(f"Fetched {len(events)} Kalshi NCAA events")

    # Filter to tournament dates
    tournament_events = []
    for event in events:
        ticker = event.get("ticker", "")
        event_date = parse_event_date(ticker)
        if event_date and is_tournament_date(event_date):
            tournament_events.append(event)

    print(f"Filtered to {len(tournament_events)} tournament-date events")

    updated = False
    next_id = max((g["id"] for g in games), default=0) + 1

    for event in tournament_events:
        title = event.get("title", "")
        markets = event.get("markets", [])

        # Match this event to a frontier game
        fg = match_event_to_frontier(event, frontier)
        if not fg:
            continue

        any_settled = any(m.get("result") in ("yes", "no") for m in markets)
        any_active = any(m.get("status") == "active" for m in markets)

        # Ensure the game entry exists in games.json
        game = fg["game"]
        if not game:
            # Create new game entry for R2+
            game = {
                "id": next_id,
                "round": fg["round"],
                "region": fg["region"],
                "seed1": fg["seed1"],
                "seed2": fg["seed2"],
                "team1": fg["team1"],
                "team2": fg["team2"],
                "score1": None,
                "score2": None,
                "status": "upcoming",
                "winner": None,
            }
            games.append(game)
            fg["game"] = game
            next_id += 1
            updated = True
            print(f"  Created game entry: {fg['team1']} vs {fg['team2']} ({fg['round']})")

        if any_settled:
            winner = resolve_winner_from_markets(markets)
            if not winner:
                print(f"  Could not determine winner for: {title}")
                continue

            if game.get("winner") != winner or game.get("status") != "final":
                old_winner = game.get("winner", "none")
                print(f"  Result: {game['team1']} vs {game['team2']} → Winner: {winner} (was: {old_winner})")
                game["winner"] = winner
                game["status"] = "final"
                game.pop("odds1", None)
                game.pop("odds2", None)
                updated = True

        elif any_active:
            if game.get("status") == "final":
                continue

            odds = extract_odds(markets)
            if odds:
                t1_odds = odds.get(game["team1"])
                t2_odds = odds.get(game["team2"])

                # Normalize to sum to 100
                if t1_odds is not None and t2_odds is not None:
                    if t1_odds + t2_odds != 100:
                        t2_odds = 100 - t1_odds
                elif t1_odds is not None:
                    t2_odds = 100 - t1_odds
                elif t2_odds is not None:
                    t1_odds = 100 - t2_odds

                if t1_odds is not None and t1_odds != game.get("odds1"):
                    game["odds1"] = t1_odds
                    updated = True
                if t2_odds is not None and t2_odds != game.get("odds2"):
                    game["odds2"] = t2_odds
                    updated = True

    # ── Fetch scores from ESPN ──
    print("\nFetching scores from ESPN...")
    espn_updated = fetch_espn_scores(games)
    if espn_updated:
        updated = True

    if updated:
        games_data["lastUpdated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(GAMES_JSON, "w") as f:
            json.dump(games_data, f, indent=2)
            f.write("\n")
        print("games.json updated!")
    else:
        print("No changes needed.")

    return updated


if __name__ == "__main__":
    changed = main()
    # Set GitHub Actions output
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")
