#!/usr/bin/env python3
"""
Fetch NCAA tournament game results from Kalshi API and update games.json.
Runs as a GitHub Actions cron job.
"""

import json
import os
import sys
import time
import base64
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Kalshi API config
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES_TICKER = "KXNCAAMBGAME"

# Paths
GAMES_JSON = Path(__file__).parent.parent / "data" / "games.json"

# Mapping from Kalshi event title team names → our games.json team names
# Kalshi uses names like "Michigan St." and we use "Michigan St" (no period sometimes)
# We'll match by normalizing both sides


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
    # Sign without query params
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


def normalize_team_name(name: str) -> str:
    """Normalize team name for comparison."""
    return (
        name.strip()
        .rstrip(".")
        .replace("(FL)", "(FL)")
        .replace("(OH)", "(Ohio)")
    )


def extract_teams_from_title(title: str) -> tuple:
    """Extract team names from Kalshi event title like 'TCU at Duke'."""
    parts = title.split(" at ")
    if len(parts) == 2:
        return normalize_team_name(parts[0]), normalize_team_name(parts[1])
    return None, None


def find_matching_game(games: list, team1_kalshi: str, team2_kalshi: str) -> dict | None:
    """Find the game in games.json that matches the Kalshi event teams."""
    for game in games:
        g_team1 = normalize_team_name(game["team1"])
        g_team2 = normalize_team_name(game["team2"])
        t1 = normalize_team_name(team1_kalshi)
        t2 = normalize_team_name(team2_kalshi)

        # Match in either order
        if (t1 == g_team1 and t2 == g_team2) or (t1 == g_team2 and t2 == g_team1):
            return game
        # Fuzzy: check if one contains the other (handles "St. John's" vs "St. John's")
        if (t1 in g_team1 or g_team1 in t1) and (t2 in g_team2 or g_team2 in t2):
            return game
        if (t1 in g_team2 or g_team2 in t1) and (t2 in g_team1 or g_team1 in t2):
            return game

    return None


def main():
    api_key_id = os.environ.get("KALSHI_API_KEY_ID", "")
    private_key_pem = os.environ.get("KALSHI_PRIVATE_KEY", "")

    if not api_key_id or not private_key_pem:
        print("Missing KALSHI_API_KEY_ID or KALSHI_PRIVATE_KEY env vars", file=sys.stderr)
        sys.exit(1)

    # Load current games.json
    with open(GAMES_JSON) as f:
        games_data = json.load(f)

    games = games_data["games"]

    # Fetch all NCAA game events from Kalshi
    path = f"/events?series_ticker={SERIES_TICKER}&limit=200&with_nested_markets=true"
    try:
        resp = kalshi_get(path, private_key_pem, api_key_id)
    except Exception as e:
        print(f"Failed to fetch Kalshi events: {e}", file=sys.stderr)
        sys.exit(1)

    events = resp.get("events", [])
    print(f"Found {len(events)} Kalshi NCAA events")

    updated = False
    for event in events:
        title = event.get("title", "")
        markets = event.get("markets", [])

        away_team, home_team = extract_teams_from_title(title)
        if not away_team or not home_team:
            print(f"  Skipping event (can't parse title): {title}")
            continue

        # Find matching game in our data
        game = find_matching_game(games, away_team, home_team)
        if not game:
            print(f"  No match for: {title} ({away_team} vs {home_team})")
            continue

        # Check if any market has settled with result "yes"
        winner = None
        for market in markets:
            if market.get("result") == "yes":
                # The market ticker_name or title tells us who won
                # Market ticker ends with team abbreviation
                market_title = market.get("title", "")
                # Title format: "Duke Winner" or "TCU Winner"
                winner_name = market_title.replace(" Winner", "").strip()
                if winner_name:
                    winner = winner_name
                    break

        if not winner:
            # Check market status - if still active, game is either upcoming or live
            any_active = any(m.get("status") == "active" for m in markets)
            any_finalized = any(m.get("status") == "finalized" for m in markets)

            if any_active and not any_finalized:
                # Check last_price to see if game might be in progress
                # (prices near 0.95+ or 0.05- suggest game is nearly decided)
                # For now, just mark as upcoming unless we have better signal
                if game["status"] != "upcoming":
                    pass  # Don't downgrade status
            continue

        # Map Kalshi winner name back to our games.json team name
        matched_winner = None
        for team_field in ["team1", "team2"]:
            our_name = game[team_field]
            kalshi_name = normalize_team_name(winner)
            our_normalized = normalize_team_name(our_name)
            if kalshi_name == our_normalized or kalshi_name in our_normalized or our_normalized in kalshi_name:
                matched_winner = our_name
                break

        if not matched_winner:
            print(f"  Winner '{winner}' doesn't match teams in game: {game['team1']} vs {game['team2']}")
            continue

        # Update game if changed
        if game["winner"] != matched_winner or game["status"] != "final":
            print(f"  Updating: {game['team1']} vs {game['team2']} → Winner: {matched_winner}")
            game["winner"] = matched_winner
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
