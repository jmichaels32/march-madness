#!/usr/bin/env python3
"""
Fetch NCAA tournament game results from Kalshi API and update games.json.
Runs as a GitHub Actions cron job.
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

# Kalshi team abbreviation → our games.json team name
# Built from observing Kalshi event tickers and titles
KALSHI_TEAM_MAP = {
    # East region
    "DUKE": "Duke",
    "SIENA": "Siena",
    "SIE": "Siena",
    "OSU": "Ohio St",
    "TCU": "TCU",
    "SJU": "St. John's",
    "UNI": "Northern Iowa",
    "KU": "Kansas",
    "KAN": "Kansas",
    "CBU": "Cal Baptist",
    "CALB": "Cal Baptist",
    "LOU": "Louisville",
    "USF": "South Florida",
    "MSU": "Michigan St",
    "NDSU": "North Dakota St",
    "UCLA": "UCLA",
    "UCF": "UCF",
    "UCONN": "UConn",
    "CONN": "UConn",
    "FUR": "Furman",
    # West region
    "ARIZ": "Arizona",
    "ARI": "Arizona",
    "LIU": "Long Island",
    "VILL": "Villanova",
    "USU": "Utah St",
    "WIS": "Wisconsin",
    "HP": "High Point",
    "ARK": "Arkansas",
    "HAW": "Hawaii",
    "BYU": "BYU",
    "TEX": "Texas",
    "GONZ": "Gonzaga",
    "GU": "Gonzaga",
    "KENN": "Kennesaw St",
    "KSU": "Kennesaw St",
    "MIA": "Miami (FL)",
    "MIAF": "Miami (FL)",
    "MIZ": "Missouri",
    "MOU": "Missouri",
    "PUR": "Purdue",
    "QUEEN": "Queens (N.C.)",
    "QU": "Queens (N.C.)",
    # South region
    "FLA": "Florida",
    "PV": "Prairie View A&M",
    "PVAM": "Prairie View A&M",
    "CLEM": "Clemson",
    "IOWA": "Iowa",
    "VAN": "Vanderbilt",
    "MCNS": "McNeese",
    "MCN": "McNeese",
    "NEB": "Nebraska",
    "TROY": "Troy",
    "UNC": "North Carolina",
    "VCU": "VCU",
    "ILL": "Illinois",
    "PENN": "Penn",
    "SMC": "Saint Mary's",
    "TXAM": "Texas A&M",
    "TAM": "Texas A&M",
    "HOU": "Houston",
    "HOUST": "Houston",
    "IDHO": "Idaho",
    "IDAH": "Idaho",
    # Midwest region
    "MICH": "Michigan",
    "HOW": "Howard",
    "UGA": "Georgia",
    "GA": "Georgia",
    "SLU": "Saint Louis",
    "TTU": "Texas Tech",
    "AKR": "Akron",
    "BAMA": "Alabama",
    "ALA": "Alabama",
    "HOFS": "Hofstra",
    "HOF": "Hofstra",
    "TENN": "Tennessee",
    "MOH": "Miami (Ohio)",
    "MOHI": "Miami (Ohio)",
    "SMU": "SMU",
    "UVA": "Virginia",
    "VA": "Virginia",
    "WRST": "Wright St",
    "UK": "Kentucky",
    "KEN": "Kentucky",
    "ISU": "Iowa St",
    "IAST": "Iowa St",
    "SC": "Santa Clara",
    "SCU": "Santa Clara",
    "TNST": "Tennessee St",
}


def sign_request(private_key_pem: str, api_key_id: str, method: str, path: str) -> dict:
    """Create Kalshi auth headers using RSA-PSS signing."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        print("ERROR: cryptography package not installed", file=sys.stderr)
        sys.exit(1)

    timestamp_ms = str(int(time.time() * 1000))
    message = f"{timestamp_ms}{method}{path}"

    private_key = serialization.load_pem_private_key(
        private_key_pem.encode(), password=None
    )
    signature = private_key.sign(
        message.encode(),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )

    return {
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
    }


def kalshi_get(path: str, private_key_pem: str, api_key_id: str) -> dict:
    """Make authenticated GET request to Kalshi API."""
    sign_path = path.split("?")[0]
    headers = sign_request(private_key_pem, api_key_id, "GET", sign_path)
    headers["Accept"] = "application/json"

    url = f"{KALSHI_BASE}{path}"
    req = Request(url, headers=headers, method="GET")

    try:
        with urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"Kalshi API error {e.code}: {body}", file=sys.stderr)
        raise


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


def resolve_winner_from_markets(markets: list, event_title: str) -> str | None:
    """
    Find the winner from settled markets.
    Returns the full team name from the event title.
    """
    # Parse teams from event title ("TCU at Ohio St.")
    parts = event_title.split(" at ")
    if len(parts) != 2:
        return None
    away_name = parts[0].strip().rstrip(".")
    home_name = parts[1].strip().rstrip(".")

    for market in markets:
        if market.get("result") != "yes":
            continue

        # Get ticker suffix (team abbreviation)
        ticker = market.get("ticker", market.get("ticker_name", ""))
        # Ticker format: KXNCAAMBGAME-26MAR19TCUOSU-TCU
        suffix = ticker.rsplit("-", 1)[-1].upper() if "-" in ticker else ""

        # Map suffix to our team name
        mapped = KALSHI_TEAM_MAP.get(suffix)
        if mapped:
            return mapped

        # Fallback: try to match suffix against event title teams
        if suffix and suffix.upper() in away_name.upper().replace(" ", "").replace(".", ""):
            return away_name
        if suffix and suffix.upper() in home_name.upper().replace(" ", "").replace(".", ""):
            return home_name

        # Last resort: use market title if it has "Winner" format
        market_title = market.get("title", "")
        if "Winner" in market_title:
            winner_name = market_title.replace("Winner", "").strip().rstrip(".")
            if winner_name:
                return winner_name

    return None


def normalize(name: str) -> str:
    """Normalize team name for comparison."""
    return name.strip().rstrip(".").lower()


def find_matching_game(games: list, winner_name: str, event_title: str) -> dict | None:
    """Find the game in games.json that contains the winner team."""
    # Parse both teams from event title
    parts = event_title.split(" at ")
    if len(parts) != 2:
        return None
    away = parts[0].strip().rstrip(".")
    home = parts[1].strip().rstrip(".")

    # Map both Kalshi names to our names
    away_mapped = None
    home_mapped = None

    # Try direct normalization match
    for game in games:
        t1 = normalize(game["team1"])
        t2 = normalize(game["team2"])
        a = normalize(away)
        h = normalize(home)

        # Both teams must match (in either order)
        match1 = (a == t1 or a in t1 or t1 in a)
        match2 = (h == t2 or h in t2 or t2 in h)
        match3 = (a == t2 or a in t2 or t2 in a)
        match4 = (h == t1 or h in t1 or t1 in h)

        if (match1 and match2) or (match3 and match4):
            return game

    return None


def main():
    api_key_id = os.environ.get("KALSHI_API_KEY_ID", "")
    private_key_pem = os.environ.get("KALSHI_PRIVATE_KEY", "")

    # Load current games.json
    with open(GAMES_JSON) as f:
        games_data = json.load(f)

    games = games_data["games"]

    # Fetch events from Kalshi (public API works for reading)
    try:
        resp = kalshi_get_unauthenticated(
            f"/events?series_ticker={SERIES_TICKER}&limit=200&with_nested_markets=true"
        )
    except Exception as e:
        print(f"Failed to fetch Kalshi events: {e}", file=sys.stderr)
        sys.exit(1)

    events = resp.get("events", [])
    print(f"Fetched {len(events)} Kalshi NCAA events")

    # Filter to tournament dates only
    tournament_events = []
    for event in events:
        ticker = event.get("ticker", "")
        event_date = parse_event_date(ticker)
        if event_date and is_tournament_date(event_date):
            tournament_events.append(event)

    print(f"Filtered to {len(tournament_events)} tournament-date events")

    updated = False
    for event in tournament_events:
        title = event.get("title", "")
        ticker = event.get("ticker", "")
        markets = event.get("markets", [])

        # Only process settled events
        any_settled = any(m.get("result") in ("yes", "no") for m in markets)
        if not any_settled:
            continue

        # Find the winner
        winner = resolve_winner_from_markets(markets, title)
        if not winner:
            print(f"  Could not determine winner for: {title}")
            continue

        # Find matching game in our data
        game = find_matching_game(games, winner, title)
        if not game:
            event_date = parse_event_date(ticker)
            print(f"  No match for: {title} (date: {event_date}, winner: {winner})")
            continue

        # Update game if changed
        if game["winner"] != winner or game["status"] != "final":
            old_winner = game.get("winner", "none")
            print(f"  Updating: {game['team1']} vs {game['team2']} → Winner: {winner} (was: {old_winner})")
            game["winner"] = winner
            game["status"] = "final"
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
