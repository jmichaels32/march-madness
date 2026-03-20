# March Madness Family Picks

A lightweight static site that tracks family March Madness bracket picks, shows a leaderboard, and displays game results.

## Project Structure

```
├── index.html          # Main page
├── styles.css          # Styles
├── app.js              # All logic: data loading, scoring, rendering
├── data/
│   ├── picks.json      # Family members and their bracket picks
│   └── games.json      # Game results (update manually or via script)
└── README.md
```

## Quick Start

Serve locally with any static file server:

```bash
# Python
python3 -m http.server 8000

# Node
npx serve .
```

Then open `http://localhost:8000`.

## How It Works

1. `picks.json` contains each family member's picks per round
2. `games.json` contains game results (status: `final`, `live`, or `upcoming`)
3. `app.js` computes scores by matching picks against results
4. The page auto-refreshes game data every 60 seconds

## Scoring

| Round | Points |
|-------|--------|
| Round of 64 | 1 |
| Round of 32 | 2 |
| Sweet 16 | 4 |
| Elite 8 | 8 |
| Final Four | 16 |
| Championship | 32 |

Edit the `SCORING` object in `app.js` to change point values.

## Updating Game Results

Edit `data/games.json` directly. Each game has:
- `status`: `"final"`, `"live"`, or `"upcoming"`
- `winner`: team name (only when `status` is `"final"`)
- `liveScore1` / `liveScore2`: current scores for live games

## Deploy to GitHub Pages

1. Create a GitHub repo and push this project
2. Go to **Settings → Pages**
3. Set source to **Deploy from a branch**, pick `main` / `root`
4. Your site will be live at `https://<user>.github.io/<repo>/`

## Adding Live Scores (Optional)

The site works fully static with manual `games.json` updates. To add live scores:

### Option A: GitHub Actions cron
Create a GitHub Action that fetches scores from an API and commits updated `games.json` every few minutes. No API key exposed to the browser.

### Option B: Serverless proxy (Netlify/Cloudflare)
1. Create a function at `api/scores.js` that fetches from ESPN/sportsdata.io using a secret API key
2. Change `DATA_URLS.games` in `app.js` to `"/api/scores"`
3. Deploy to Netlify (`netlify deploy`) or Cloudflare Pages

Example Netlify function (`netlify/functions/scores.js`):
```js
export default async (req) => {
  const resp = await fetch("https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?groups=100");
  const data = await resp.json();
  // Transform ESPN data to match your games.json shape
  return new Response(JSON.stringify(transformed), {
    headers: { "Content-Type": "application/json" },
  });
};
```
