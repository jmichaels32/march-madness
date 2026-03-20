// =============================================================
// CONFIG
// =============================================================

const SCORING = {
  round1: 1, round2: 2, sweet16: 4,
  elite8: 8, final4: 16, championship: 32,
};

const ROUND_LABELS = {
  round1: "R64", round2: "R32", sweet16: "S16",
  elite8: "E8", final4: "F4", championship: "Ch",
};

const ROUND_ORDER = ["round1", "round2", "sweet16", "elite8", "final4", "championship"];

const PERSON_COLORS = {
  "Ben": "#3b82f6", "Charlotte": "#ec4899", "Jason": "#f59e0b", "Andrea": "#8b5cf6",
  "Nathan": "#10b981", "Nana": "#f97316", "Brent": "#06b6d4", "Susun": "#ef4444",
  "Jack": "#84cc16",
};

const REGIONS_LEFT = ["East", "South"];
const REGIONS_RIGHT = ["West", "Midwest"];
const ALL_REGIONS = ["East", "West", "South", "Midwest"];

// Pick offsets: where each region's picks start within each round's pick array
const REGION_PICK_OFFSETS = {
  East:    { round1: 0,  round2: 0,  sweet16: 0, elite8: 0 },
  West:    { round1: 8,  round2: 4,  sweet16: 2, elite8: 1 },
  South:   { round1: 16, round2: 8,  sweet16: 4, elite8: 2 },
  Midwest: { round1: 24, round2: 12, sweet16: 6, elite8: 3 },
};

// Games per region per round
const GAMES_PER_ROUND = { round1: 8, round2: 4, sweet16: 2, elite8: 1 };

const DATA_URLS = { picks: "data/picks.json", games: "data/games.json" };
const REFRESH_INTERVAL = 60_000;

// =============================================================
// DATA
// =============================================================

async function fetchJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`Fetch failed: ${url}`);
  return r.json();
}

async function loadData() {
  const [picks, games] = await Promise.all([
    fetchJSON(DATA_URLS.picks), fetchJSON(DATA_URLS.games),
  ]);
  return { picks, games };
}

// =============================================================
// BRACKET LOGIC
// =============================================================

function buildGameLookup(games) {
  const byId = {};
  const byTeams = {};
  for (const g of games) {
    byId[g.id] = g;
    const key1 = `${g.round}|${g.team1}|${g.team2}`;
    const key2 = `${g.round}|${g.team2}|${g.team1}`;
    byTeams[key1] = g;
    byTeams[key2] = g;
  }
  return { byId, byTeams };
}

function buildEliminatedSet(games) {
  const eliminated = new Set();
  for (const g of games) {
    if (g.status === "final" && g.winner) {
      const loser = g.winner === g.team1 ? g.team2 : g.team1;
      eliminated.add(loser);
    }
  }
  return eliminated;
}

/**
 * Build bracket slots for one region across all regional rounds.
 * Returns { round1: [...slots], round2: [...], sweet16: [...], elite8: [...] }
 * Each slot: { team1, team2, seed1, seed2, winner, score1, score2, status, picks: {name: team} }
 */
function buildRegionBracket(region, gamesData, picksData, gameLookup, eliminated) {
  const rounds = {};
  const regionRounds = ["round1", "round2", "sweet16", "elite8"];

  // R1: get from games data (IDs correspond to bracket positions)
  const r1Offset = REGION_PICK_OFFSETS[region].round1; // 0, 8, 16, 24
  const r1Games = [];
  for (let i = 0; i < 8; i++) {
    const gameId = r1Offset + i + 1; // IDs are 1-indexed
    const g = gameLookup.byId[gameId];
    const slot = {
      team1: g?.team1 || "TBD", team2: g?.team2 || "TBD",
      seed1: g?.seed1, seed2: g?.seed2,
      winner: g?.winner || null,
      score1: g?.score1, score2: g?.score2,
      status: g?.status || "upcoming",
      clock: g?.clock, liveScore1: g?.liveScore1, liveScore2: g?.liveScore2,
      odds1: g?.odds1 ?? null, odds2: g?.odds2 ?? null,
      picks: {},
    };
    // Map picks
    for (const member of picksData.members) {
      const pickIdx = REGION_PICK_OFFSETS[region].round1 + i;
      const pick = member.picks.round1?.[pickIdx] || null;
      if (pick) slot.picks[member.name] = pick;
    }
    r1Games.push(slot);
  }
  rounds.round1 = r1Games;

  // R2+: compute matchups from previous round winners
  let prevRound = r1Games;
  for (let ri = 1; ri < regionRounds.length; ri++) {
    const roundKey = regionRounds[ri];
    const numGames = GAMES_PER_ROUND[roundKey];
    const slots = [];

    for (let i = 0; i < numGames; i++) {
      const parent1 = prevRound[i * 2];
      const parent2 = prevRound[i * 2 + 1];
      const t1 = parent1?.winner || null;
      const t2 = parent2?.winner || null;

      // Look up actual game result if both teams are known
      let g = null;
      if (t1 && t2) {
        g = gameLookup.byTeams[`${roundKey}|${t1}|${t2}`] || null;
      }

      const slot = {
        team1: t1 || "TBD", team2: t2 || "TBD",
        seed1: t1 ? (parent1.winner === parent1.team1 ? parent1.seed1 : parent1.seed2) : null,
        seed2: t2 ? (parent2.winner === parent2.team1 ? parent2.seed1 : parent2.seed2) : null,
        winner: g?.winner || null,
        score1: g?.score1, score2: g?.score2,
        status: g?.status || "upcoming",
        clock: g?.clock, liveScore1: g?.liveScore1, liveScore2: g?.liveScore2,
        odds1: g?.odds1 ?? null, odds2: g?.odds2 ?? null,
        picks: {},
      };

      for (const member of picksData.members) {
        const pickIdx = REGION_PICK_OFFSETS[region][roundKey] + i;
        const pick = member.picks[roundKey]?.[pickIdx] || null;
        if (pick) slot.picks[member.name] = pick;
      }
      slots.push(slot);
    }
    rounds[roundKey] = slots;
    prevRound = slots;
  }

  return rounds;
}

function buildFinalFour(regionBrackets, gamesData, picksData, gameLookup) {
  // Semifinal 1: East E8 winner vs South E8 winner
  // Semifinal 2: West E8 winner vs Midwest E8 winner
  const eastWinner = regionBrackets.East.elite8[0];
  const southWinner = regionBrackets.South.elite8[0];
  const westWinner = regionBrackets.West.elite8[0];
  const midwestWinner = regionBrackets.Midwest.elite8[0];

  function makeSemi(parent1, parent2, ffIndex) {
    const t1 = parent1?.winner || null;
    const t2 = parent2?.winner || null;
    let g = null;
    if (t1 && t2) g = gameLookup.byTeams[`final4|${t1}|${t2}`] || null;
    const slot = {
      team1: t1 || "TBD", team2: t2 || "TBD",
      seed1: t1 ? (parent1.winner === parent1.team1 ? parent1.seed1 : parent1.seed2) : null,
      seed2: t2 ? (parent2.winner === parent2.team1 ? parent2.seed1 : parent2.seed2) : null,
      winner: g?.winner || null,
      score1: g?.score1, score2: g?.score2,
      status: g?.status || "upcoming",
      odds1: g?.odds1 ?? null, odds2: g?.odds2 ?? null,
      picks: {},
    };
    for (const member of picksData.members) {
      const pick = member.picks.final4?.[ffIndex] || null;
      if (pick) slot.picks[member.name] = pick;
    }
    return slot;
  }

  const semi1 = makeSemi(eastWinner, southWinner, 0);
  const semi2 = makeSemi(westWinner, midwestWinner, 1);

  // Championship
  const ct1 = semi1.winner || null;
  const ct2 = semi2.winner || null;
  let cg = null;
  if (ct1 && ct2) cg = gameLookup.byTeams[`championship|${ct1}|${ct2}`] || null;
  const champ = {
    team1: ct1 || "TBD", team2: ct2 || "TBD",
    seed1: null, seed2: null,
    winner: cg?.winner || null,
    score1: cg?.score1, score2: cg?.score2,
    status: cg?.status || "upcoming",
    odds1: cg?.odds1 ?? null, odds2: cg?.odds2 ?? null,
    picks: {},
  };
  for (const member of picksData.members) {
    const pick = member.picks.championship?.[0] || null;
    if (pick) champ.picks[member.name] = pick;
  }

  return { semi1, semi2, champ };
}

// =============================================================
// SCORING
// =============================================================

function computeStandings(picksData, gamesData) {
  const winners = {};
  const eliminated = buildEliminatedSet(gamesData.games);
  for (const r of ROUND_ORDER) winners[r] = new Set();
  // Count decided games per round (to know total possible)
  const decidedPerRound = {};
  for (const r of ROUND_ORDER) decidedPerRound[r] = 0;
  for (const g of gamesData.games) {
    if (g.status === "final" && g.winner) {
      winners[g.round].add(g.winner);
      decidedPerRound[g.round]++;
    }
  }

  return picksData.members.map(member => {
    let total = 0;
    let correct = 0, wrong = 0, pending = 0;
    const roundScores = {};
    for (const round of ROUND_ORDER) {
      let rs = 0;
      const picks = member.picks[round] || [];
      for (const team of picks) {
        if (!team) continue;
        if (winners[round].has(team)) {
          rs += SCORING[round];
          correct++;
        } else if (eliminated.has(team)) {
          wrong++;
        } else {
          pending++;
        }
      }
      roundScores[round] = rs;
      total += rs;
    }
    // Max possible = current total + pending picks * their round values
    let maxPossible = total;
    for (const round of ROUND_ORDER) {
      const picks = member.picks[round] || [];
      for (const team of picks) {
        if (!team) continue;
        if (!winners[round].has(team) && !eliminated.has(team)) {
          maxPossible += SCORING[round];
        }
      }
    }
    return { name: member.name, total, roundScores, correct, wrong, pending, maxPossible };
  }).sort((a, b) => b.total - a.total);
}

// =============================================================
// RENDERING
// =============================================================

function esc(str) {
  if (!str) return "";
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

function isLive(slot) {
  // If we have scores but no winner, the game is in progress
  return slot.score1 != null && slot.score2 != null && !slot.winner;
}

function getPickStatus(slot, team, eliminated) {
  if (!team || team === "TBD") return "pending";
  if (slot.status === "final" && slot.winner) {
    return team === slot.winner ? "correct" : "wrong";
  }
  if (eliminated.has(team)) return "wrong";
  if (isLive(slot)) return "live";
  return "pending";
}

// =============================================================
// PICKS POPOVER
// =============================================================

function closePicksPopover() {
  const existing = document.querySelector(".picks-popover");
  if (existing) existing.remove();
}

function showPicksPopover(slot, eliminated, cellEl) {
  closePicksPopover();

  const popover = document.createElement("div");
  popover.className = "picks-popover";

  const isGameFinal = slot.status === "final" && slot.winner;
  const isGameLive = isLive(slot);

  // ── Status badge ──
  const statusBadge = document.createElement("div");
  statusBadge.className = "pop-status-badge";
  if (isGameFinal) {
    statusBadge.textContent = "FINAL";
    statusBadge.classList.add("final");
  } else if (isGameLive) {
    statusBadge.textContent = "LIVE";
    statusBadge.classList.add("live");
  } else {
    statusBadge.textContent = "UPCOMING";
  }
  popover.appendChild(statusBadge);

  // ── Scoreboard: two team rows ──
  const teams = [
    { name: slot.team1, seed: slot.seed1, score: slot.score1, odds: slot.odds1, isWinner: isGameFinal && slot.winner === slot.team1 },
    { name: slot.team2, seed: slot.seed2, score: slot.score2, odds: slot.odds2, isWinner: isGameFinal && slot.winner === slot.team2 },
  ];

  const scoreboard = document.createElement("div");
  scoreboard.className = "pop-scoreboard";

  for (const t of teams) {
    const row = document.createElement("div");
    row.className = "pop-team-row" + (t.isWinner ? " winner" : "") +
      (isGameFinal && !t.isWinner ? " loser" : "");

    const seedEl = document.createElement("span");
    seedEl.className = "pop-seed";
    seedEl.textContent = t.seed != null ? t.seed : "";
    row.appendChild(seedEl);

    const nameEl = document.createElement("span");
    nameEl.className = "pop-team-name";
    nameEl.textContent = t.name || "TBD";
    row.appendChild(nameEl);

    // Odds
    if (t.odds != null && !isGameFinal) {
      const oddsEl = document.createElement("span");
      oddsEl.className = "pop-odds";
      if (t.odds >= 60) oddsEl.classList.add("favored");
      else if (t.odds <= 40) oddsEl.classList.add("underdog");
      oddsEl.textContent = `${t.odds}%`;
      row.appendChild(oddsEl);
    }

    // Score
    const scoreEl = document.createElement("span");
    scoreEl.className = "pop-score";
    scoreEl.textContent = t.score != null ? t.score : "–";
    row.appendChild(scoreEl);

    scoreboard.appendChild(row);
  }
  popover.appendChild(scoreboard);

  // ── Picks section ──
  const picksHeader = document.createElement("div");
  picksHeader.className = "pop-picks-header";
  picksHeader.textContent = "Picks";
  popover.appendChild(picksHeader);

  // Group picks by team
  const byTeam = {};
  const noPick = [];
  const allNames = Object.keys(PERSON_COLORS);

  for (const name of allNames) {
    const pick = slot.picks[name];
    if (!pick) {
      noPick.push(name);
    } else {
      if (!byTeam[pick]) byTeam[pick] = [];
      byTeam[pick].push(name);
    }
  }

  const teamOrder = [slot.team1, slot.team2, ...Object.keys(byTeam).filter(t => t !== slot.team1 && t !== slot.team2)];
  for (const team of teamOrder) {
    const pickers = byTeam[team];
    if (!pickers || pickers.length === 0) continue;

    const teamGroup = document.createElement("div");
    teamGroup.className = "pop-pick-group";

    const teamLabel = document.createElement("div");
    teamLabel.className = "pop-pick-team";
    const isW = isGameFinal && slot.winner === team;
    const isL = isGameFinal && slot.winner && slot.winner !== team;
    if (isW) teamLabel.classList.add("winner");
    if (isL) teamLabel.classList.add("loser");
    teamLabel.textContent = team;
    teamGroup.appendChild(teamLabel);

    const pickersList = document.createElement("div");
    pickersList.className = "pop-pick-names";
    for (const name of pickers) {
      const pickerEl = document.createElement("span");
      pickerEl.className = "pop-picker";
      const status = getPickStatus(slot, slot.picks[name], eliminated);
      pickerEl.classList.add(status);
      const dot = document.createElement("span");
      dot.className = "pop-picker-dot";
      dot.style.backgroundColor = PERSON_COLORS[name] || "#888";
      pickerEl.appendChild(dot);
      pickerEl.appendChild(document.createTextNode(name));
      pickersList.appendChild(pickerEl);
    }
    teamGroup.appendChild(pickersList);
    popover.appendChild(teamGroup);
  }

  if (noPick.length > 0) {
    const noPickGroup = document.createElement("div");
    noPickGroup.className = "pop-pick-group no-pick";
    const label = document.createElement("div");
    label.className = "pop-pick-team";
    label.textContent = "No pick";
    noPickGroup.appendChild(label);
    const list = document.createElement("div");
    list.className = "pop-pick-names";
    for (const name of noPick) {
      const el = document.createElement("span");
      el.className = "pop-picker pending";
      const dot = document.createElement("span");
      dot.className = "pop-picker-dot";
      dot.style.backgroundColor = PERSON_COLORS[name] || "#888";
      dot.style.opacity = "0.3";
      el.appendChild(dot);
      el.appendChild(document.createTextNode(name));
      list.appendChild(el);
    }
    noPickGroup.appendChild(list);
    popover.appendChild(noPickGroup);
  }

  // Position: append to body, then position near the cell
  document.body.appendChild(popover);

  const cellRect = cellEl.getBoundingClientRect();
  const popRect = popover.getBoundingClientRect();

  // Try to position below the cell, centered
  let top = cellRect.bottom + 6;
  let left = cellRect.left + (cellRect.width / 2) - (popRect.width / 2);

  // Keep within viewport
  if (top + popRect.height > window.innerHeight - 10) {
    top = cellRect.top - popRect.height - 6;
  }
  left = Math.max(8, Math.min(left, window.innerWidth - popRect.width - 8));
  top = Math.max(8, top);

  popover.style.top = `${top}px`;
  popover.style.left = `${left}px`;

  // Close on outside click (next tick to avoid immediate close)
  requestAnimationFrame(() => {
    function onClickOutside(e) {
      if (!popover.contains(e.target) && !cellEl.contains(e.target)) {
        closePicksPopover();
        document.removeEventListener("click", onClickOutside);
        document.removeEventListener("touchstart", onClickOutside);
      }
    }
    document.addEventListener("click", onClickOutside);
    document.addEventListener("touchstart", onClickOutside);
  });
}

// =============================================================
// GAME CELL RENDERING
// =============================================================

function getFilterStatus(slot, eliminated, person) {
  if (!person) return null;
  const pick = slot.picks[person];
  if (!pick) return "no-pick";
  const status = getPickStatus(slot, pick, eliminated);
  if (status === "correct") return "correct";
  if (status === "wrong") return "wrong";
  return "pending";
}

function renderGameCell(slot, eliminated, roundKey) {
  const cell = document.createElement("div");
  cell.className = "game-cell";
  if (slot.status === "final") cell.classList.add("has-result");
  if (isLive(slot)) cell.classList.add("live");

  // Apply person filter classes
  if (filterPerson) {
    const fs = getFilterStatus(slot, eliminated, filterPerson);
    if (fs) cell.classList.add(`filter-${fs}`);
  }

  // Click to show picks popover
  cell.addEventListener("click", (e) => {
    e.stopPropagation();
    showPicksPopover(slot, eliminated, cell);
  });

  const teams = [
    { name: slot.team1, seed: slot.seed1, isTeam1: true },
    { name: slot.team2, seed: slot.seed2, isTeam1: false },
  ];

  for (const t of teams) {
    const row = document.createElement("div");
    row.className = "team-row";

    const isWinner = slot.status === "final" && slot.winner === t.name;
    const isLoser = slot.status === "final" && slot.winner && slot.winner !== t.name;
    if (isWinner) row.classList.add("winner");
    if (isLoser) row.classList.add("loser");

    // Mark the team this person picked
    if (filterPerson && slot.picks[filterPerson] === t.name) {
      row.classList.add("person-picked");
    }

    // Seed
    if (t.seed != null) {
      const seedEl = document.createElement("span");
      seedEl.className = "seed";
      seedEl.textContent = t.seed;
      row.appendChild(seedEl);
    }

    // Team name
    const nameEl = document.createElement("span");
    nameEl.className = "team-name";
    nameEl.textContent = t.name || "TBD";
    row.appendChild(nameEl);

    // Pick dots (after name, before score so score stays far-right)
    const dotsContainer = document.createElement("span");
    dotsContainer.className = "pick-dots";
    for (const [person, pickedTeam] of Object.entries(slot.picks)) {
      if (pickedTeam === t.name) {
        const dot = document.createElement("span");
        dot.className = "pick-dot";
        const status = getPickStatus(slot, pickedTeam, eliminated);
        dot.classList.add(status);
        dot.style.backgroundColor = PERSON_COLORS[person] || "#888";
        dot.title = person;
        dotsContainer.appendChild(dot);
      }
    }
    row.appendChild(dotsContainer);

    // Odds badge for active games
    const odds = t.isTeam1 ? slot.odds1 : slot.odds2;
    if (odds != null && slot.status !== "final") {
      const oddsEl = document.createElement("span");
      oddsEl.className = "team-odds";
      if (odds >= 60) oddsEl.classList.add("favored");
      else if (odds <= 40) oddsEl.classList.add("underdog");
      oddsEl.textContent = `${odds}%`;
      row.appendChild(oddsEl);
      if (odds >= 50 && !filterPerson) {
        row.style.background = `rgba(74, 222, 128, ${(odds - 50) * 0.003})`;
      }
    }

    // Score (always far-right, fixed width)
    const scoreVal = t.isTeam1 ? slot.score1 : slot.score2;
    if (scoreVal != null) {
      const scoreEl = document.createElement("span");
      scoreEl.className = "team-score";
      scoreEl.textContent = scoreVal;
      row.appendChild(scoreEl);
    }

    cell.appendChild(row);
  }

  // Status badge for live/upcoming
  if (isLive(slot) && slot.clock) {
    const badge = document.createElement("div");
    badge.className = "game-status-badge live";
    badge.textContent = slot.clock;
    cell.appendChild(badge);
  }

  return cell;
}

function renderRegion(regionName, regionData, eliminated, side) {
  const regionEl = document.createElement("div");
  regionEl.className = "region";

  const header = document.createElement("div");
  header.className = "region-header";
  header.textContent = regionName;
  regionEl.appendChild(header);

  const roundsContainer = document.createElement("div");
  roundsContainer.className = "region-rounds";

  const roundKeys = ["round1", "round2", "sweet16", "elite8"];

  for (const roundKey of roundKeys) {
    const col = document.createElement("div");
    col.className = `round-col ${roundKey === "round1" ? "r1" : ""}`;

    const slots = regionData[roundKey] || [];
    for (const slot of slots) {
      const wrapper = document.createElement("div");
      wrapper.className = "game-cell-wrapper";
      wrapper.appendChild(renderGameCell(slot, eliminated, roundKey));
      col.appendChild(wrapper);
    }
    roundsContainer.appendChild(col);
  }

  regionEl.appendChild(roundsContainer);
  return regionEl;
}

function renderBracket(regionBrackets, finalFour, eliminated) {
  const bracket = document.getElementById("bracket");
  bracket.innerHTML = "";

  // Left side: East, South
  const leftSide = document.createElement("div");
  leftSide.className = "bracket-side left";
  for (const r of REGIONS_LEFT) {
    leftSide.appendChild(renderRegion(r, regionBrackets[r], eliminated, "left"));
  }
  bracket.appendChild(leftSide);

  // Center: Final Four + Championship
  const center = document.createElement("div");
  center.className = "bracket-center";

  const ff1Label = document.createElement("div");
  ff1Label.className = "ff-label";
  ff1Label.textContent = "Semifinal";
  center.appendChild(ff1Label);

  const ff1Cell = document.createElement("div");
  ff1Cell.className = "center-game";
  ff1Cell.appendChild(renderGameCell(finalFour.semi1, eliminated, "final4"));
  center.appendChild(ff1Cell);

  const champLabel = document.createElement("div");
  champLabel.className = "champ-label";
  champLabel.textContent = "Championship";
  center.appendChild(champLabel);

  const champCell = document.createElement("div");
  champCell.className = "center-game";
  champCell.appendChild(renderGameCell(finalFour.champ, eliminated, "championship"));
  center.appendChild(champCell);

  const ff2Label = document.createElement("div");
  ff2Label.className = "ff-label";
  ff2Label.textContent = "Semifinal";
  center.appendChild(ff2Label);

  const ff2Cell = document.createElement("div");
  ff2Cell.className = "center-game";
  ff2Cell.appendChild(renderGameCell(finalFour.semi2, eliminated, "final4"));
  center.appendChild(ff2Cell);

  bracket.appendChild(center);

  // Right side: West, Midwest
  const rightSide = document.createElement("div");
  rightSide.className = "bracket-side right";
  for (const r of REGIONS_RIGHT) {
    rightSide.appendChild(renderRegion(r, regionBrackets[r], eliminated, "right"));
  }
  bracket.appendChild(rightSide);
}

function renderLegend(picksData) {
  const legend = document.getElementById("legend");
  legend.innerHTML = "";
  for (const member of picksData.members) {
    const item = document.createElement("span");
    item.className = "legend-item";
    const dot = document.createElement("span");
    dot.className = "legend-dot";
    dot.style.backgroundColor = PERSON_COLORS[member.name] || "#888";
    item.appendChild(dot);
    item.appendChild(document.createTextNode(member.name));
    legend.appendChild(item);
  }
}

function renderLeaderboardCard(container, s, rank) {
  const card = document.createElement("div");
  card.className = `lb-card rank-${rank}`;

  const color = PERSON_COLORS[s.name] || "#888";
  const maxScore = s.maxPossible || s.total;
  const barWidth = maxScore > 0 ? (s.total / maxScore) * 100 : 0;

  const rankLabels = { 1: "1st", 2: "2nd", 3: "3rd" };
  const rankText = rankLabels[rank] || `${rank}th`;

  card.innerHTML = `
    <div class="lb-card-left">
      <div class="lb-card-rank">${rankText}</div>
      <div class="lb-card-dot" style="background:${color}; box-shadow: 0 0 8px ${color}44"></div>
      <div class="lb-card-info">
        <div class="lb-card-name" style="color:${color}">${esc(s.name)}</div>
        <div class="lb-card-record">
          <span class="lb-stat correct">${s.correct}</span>
          <span class="lb-stat wrong">${s.wrong}</span>
          <span class="lb-stat pending">${s.pending}</span>
        </div>
      </div>
    </div>
    <div class="lb-card-right">
      <div class="lb-card-score">${s.total}</div>
      <div class="lb-card-max">of ${maxScore}</div>
    </div>
  `;

  // Progress bar
  const barContainer = document.createElement("div");
  barContainer.className = "lb-bar";
  const barFill = document.createElement("div");
  barFill.className = "lb-bar-fill";
  barFill.style.width = barWidth + "%";
  barFill.style.background = `linear-gradient(90deg, ${color}88, ${color})`;
  barContainer.appendChild(barFill);
  card.appendChild(barContainer);

  // Round breakdown row
  const rounds = document.createElement("div");
  rounds.className = "lb-rounds";
  const roundLabels = { round1: "R64", round2: "R32", sweet16: "S16", elite8: "E8", final4: "F4", championship: "Ch" };
  for (const rk of ROUND_ORDER) {
    const val = s.roundScores[rk] || 0;
    const chip = document.createElement("span");
    chip.className = "lb-round-chip" + (val > 0 ? " has-pts" : "");
    chip.innerHTML = `<span class="lb-round-label">${roundLabels[rk]}</span><span class="lb-round-val">${val}</span>`;
    rounds.appendChild(chip);
  }
  card.appendChild(rounds);

  container.appendChild(card);
}

function renderLeaderboard(standings, gamesData) {
  const container = document.getElementById("leaderboard-body");
  container.innerHTML = "";

  const updatedEl = document.getElementById("last-updated");
  if (gamesData.lastUpdated) {
    updatedEl.textContent = `Updated: ${new Date(gamesData.lastUpdated).toLocaleString()}`;
  }

  standings.forEach((s, i) => renderLeaderboardCard(container, s, i + 1));
}

// =============================================================
// MOBILE BRACKET VIEW
// =============================================================

let mobileSelectedRegion = "East";
let filterPerson = null; // null = show all

function renderMobileRegionTabs() {
  const container = document.getElementById("mobile-region-tabs");
  if (!container) return;
  container.innerHTML = "";
  const tabs = ["East", "West", "South", "Midwest", "Final Four"];
  for (const tab of tabs) {
    const btn = document.createElement("button");
    btn.className = "mobile-tab" + (tab === mobileSelectedRegion ? " active" : "");
    btn.textContent = tab;
    btn.addEventListener("click", () => {
      mobileSelectedRegion = tab;
      renderMobileRegionTabs();
      renderMobileRegionContent();
    });
    container.appendChild(btn);
  }
}

let _mobileRenderData = null;

function renderMobileRegionContent() {
  const container = document.getElementById("mobile-region-content");
  if (!container || !_mobileRenderData) return;
  container.innerHTML = "";

  const { regionBrackets, finalFour, eliminated } = _mobileRenderData;

  if (mobileSelectedRegion === "Final Four") {
    renderMobileFFContent(container, finalFour, eliminated);
    return;
  }

  const region = mobileSelectedRegion;
  const data = regionBrackets[region];
  if (!data) return;

  const roundKeys = ["round1", "round2", "sweet16", "elite8"];
  const roundNames = { round1: "Round of 64", round2: "Round of 32", sweet16: "Sweet 16", elite8: "Elite 8" };

  for (const roundKey of roundKeys) {
    const slots = data[roundKey] || [];
    if (slots.length === 0) continue;

    const section = document.createElement("div");
    section.className = "mobile-round";

    const label = document.createElement("div");
    label.className = "mobile-round-label";
    label.textContent = roundNames[roundKey];
    section.appendChild(label);

    for (const slot of slots) {
      section.appendChild(renderMobileGameCard(slot, eliminated));
    }
    container.appendChild(section);
  }
}

function renderMobileFFContent(container, finalFour, eliminated) {
  // Semi 1
  const s1Label = document.createElement("div");
  s1Label.className = "mobile-round-label";
  s1Label.textContent = "Semifinal — East vs South";
  container.appendChild(s1Label);
  container.appendChild(renderMobileGameCard(finalFour.semi1, eliminated));

  // Semi 2
  const s2Label = document.createElement("div");
  s2Label.className = "mobile-round-label";
  s2Label.textContent = "Semifinal — West vs Midwest";
  container.appendChild(s2Label);
  container.appendChild(renderMobileGameCard(finalFour.semi2, eliminated));

  // Championship
  const cLabel = document.createElement("div");
  cLabel.className = "mobile-round-label champ";
  cLabel.textContent = "Championship";
  container.appendChild(cLabel);
  container.appendChild(renderMobileGameCard(finalFour.champ, eliminated));
}

function renderMobileGameCard(slot, eliminated) {
  const card = document.createElement("div");
  card.className = "mobile-game-card";
  if (slot.status === "final") card.classList.add("has-result");
  if (isLive(slot)) card.classList.add("live");

  // Apply person filter classes
  if (filterPerson) {
    const fs = getFilterStatus(slot, eliminated, filterPerson);
    if (fs) card.classList.add(`filter-${fs}`);
  }

  const teams = [
    { name: slot.team1, seed: slot.seed1, isTeam1: true },
    { name: slot.team2, seed: slot.seed2, isTeam1: false },
  ];

  for (const t of teams) {
    const row = document.createElement("div");
    row.className = "mobile-team-row";
    const isWinner = slot.status === "final" && slot.winner === t.name;
    const isLoser = slot.status === "final" && slot.winner && slot.winner !== t.name;
    if (isWinner) row.classList.add("winner");
    if (isLoser) row.classList.add("loser");

    // Seed + name
    const info = document.createElement("div");
    info.className = "mobile-team-info";
    if (t.seed != null) {
      const seedEl = document.createElement("span");
      seedEl.className = "mobile-seed";
      seedEl.textContent = t.seed;
      info.appendChild(seedEl);
    }
    const nameEl = document.createElement("span");
    nameEl.className = "mobile-team-name";
    nameEl.textContent = t.name || "TBD";
    info.appendChild(nameEl);

    // Score (use score1/score2 — ESPN populates these for live and final)
    const scoreVal = t.isTeam1 ? slot.score1 : slot.score2;
    if (scoreVal != null) {
      const scoreEl = document.createElement("span");
      scoreEl.className = "mobile-team-score";
      scoreEl.textContent = scoreVal;
      info.appendChild(scoreEl);
    }

    // Odds badge (show alongside score for live games, not just instead of)
    const odds = t.isTeam1 ? slot.odds1 : slot.odds2;
    if (odds != null && slot.status !== "final") {
      const oddsEl = document.createElement("span");
      oddsEl.className = "mobile-team-odds";
      if (odds >= 60) oddsEl.classList.add("favored");
      else if (odds <= 40) oddsEl.classList.add("underdog");
      oddsEl.textContent = `${odds}%`;
      info.appendChild(oddsEl);
      if (odds >= 50 && !filterPerson) {
        row.style.background = `rgba(74, 222, 128, ${(odds - 50) * 0.003})`;
      }
    }

    row.appendChild(info);

    // Pickers row
    const pickers = document.createElement("div");
    pickers.className = "mobile-pickers";
    for (const [person, pickedTeam] of Object.entries(slot.picks)) {
      if (pickedTeam === t.name) {
        const chip = document.createElement("span");
        chip.className = "mobile-picker-chip";
        const status = getPickStatus(slot, pickedTeam, eliminated);
        chip.classList.add(status);
        // Dim non-filtered people when filter is active
        if (filterPerson && person !== filterPerson) {
          chip.classList.add("dimmed");
        }
        if (filterPerson && person === filterPerson) {
          chip.classList.add("highlighted");
        }
        const dot = document.createElement("span");
        dot.className = "mobile-picker-dot";
        dot.style.backgroundColor = PERSON_COLORS[person] || "#888";
        chip.appendChild(dot);
        chip.appendChild(document.createTextNode(person));
        pickers.appendChild(chip);
      }
    }
    row.appendChild(pickers);

    // When filtering, highlight the team row if this person picked this team
    if (filterPerson && slot.picks[filterPerson] === t.name) {
      row.classList.add("person-picked");
    }

    card.appendChild(row);
  }

  return card;
}

function onFilterChange() {
  renderFilterBars();
  renderMobilePersonSummary();
  renderMobileRegionContent();
  // Re-render desktop bracket with filter applied
  if (_mobileRenderData) {
    const { regionBrackets, finalFour, eliminated } = _mobileRenderData;
    renderBracket(regionBrackets, finalFour, eliminated);
    renderMiniBracket(regionBrackets, finalFour, eliminated);
  }
}

function renderFilterBar(containerId) {
  const bar = document.getElementById(containerId);
  if (!bar) return;
  bar.innerHTML = "";

  const label = document.createElement("span");
  label.className = "filter-label";
  label.textContent = "Filter:";
  bar.appendChild(label);

  // "All" button
  const allBtn = document.createElement("button");
  allBtn.className = "filter-btn" + (filterPerson === null ? " active" : "");
  allBtn.textContent = "All";
  allBtn.addEventListener("click", () => {
    filterPerson = null;
    onFilterChange();
  });
  bar.appendChild(allBtn);

  // Person buttons
  for (const name of Object.keys(PERSON_COLORS)) {
    const btn = document.createElement("button");
    btn.className = "filter-btn" + (filterPerson === name ? " active" : "");
    btn.style.borderColor = filterPerson === name ? PERSON_COLORS[name] : "transparent";
    const dot = document.createElement("span");
    dot.className = "filter-dot";
    dot.style.backgroundColor = PERSON_COLORS[name];
    btn.appendChild(dot);
    btn.appendChild(document.createTextNode(name));
    btn.addEventListener("click", () => {
      filterPerson = filterPerson === name ? null : name;
      onFilterChange();
    });
    bar.appendChild(btn);
  }
}

function renderFilterBars() {
  renderFilterBar("mobile-filter-bar");
  renderFilterBar("desktop-filter-bar");
}

function renderMobileFilterBar() {
  renderFilterBars();
}

function renderMobilePersonSummary() {
  const container = document.getElementById("mobile-person-summary");
  if (!container) return;
  container.innerHTML = "";

  if (!filterPerson || !_mobileRenderData) return;

  const { regionBrackets, finalFour, eliminated } = _mobileRenderData;
  const person = filterPerson;

  // Count correct, wrong, pending picks across all rounds
  let correct = 0, wrong = 0, pending = 0, total = 0;

  function countSlot(slot) {
    const pick = slot.picks[person];
    if (!pick) return;
    total++;
    const status = getPickStatus(slot, pick, eliminated);
    if (status === "correct") correct++;
    else if (status === "wrong") wrong++;
    else pending++;
  }

  for (const region of ALL_REGIONS) {
    const data = regionBrackets[region];
    for (const rk of ["round1", "round2", "sweet16", "elite8"]) {
      for (const slot of (data[rk] || [])) countSlot(slot);
    }
  }
  countSlot(finalFour.semi1);
  countSlot(finalFour.semi2);
  countSlot(finalFour.champ);

  const summary = document.createElement("div");
  summary.className = "person-summary-content";

  const nameEl = document.createElement("span");
  nameEl.className = "person-summary-name";
  nameEl.style.color = PERSON_COLORS[person];
  nameEl.textContent = person;
  summary.appendChild(nameEl);

  const stats = document.createElement("span");
  stats.className = "person-summary-stats";
  stats.innerHTML = `<span class="stat-correct">${correct}</span> correct · <span class="stat-wrong">${wrong}</span> wrong · <span class="stat-pending">${pending}</span> pending`;
  summary.appendChild(stats);

  container.appendChild(summary);
}

function renderMobileBracket(regionBrackets, finalFour, eliminated) {
  _mobileRenderData = { regionBrackets, finalFour, eliminated };
  renderMobileFilterBar();
  renderMobilePersonSummary();
  renderMobileRegionTabs();
  renderMobileRegionContent();
  renderMiniBracket(regionBrackets, finalFour, eliminated);
}

// =============================================================
// MINI BRACKET (mobile overview)
// =============================================================

function renderMiniBracket(regionBrackets, finalFour, eliminated) {
  const container = document.getElementById("mini-bracket");
  if (!container) return;
  container.innerHTML = "";

  // Reuse the exact desktop bracket structure, just inside the mini wrapper
  // Left side: East, South
  const leftSide = document.createElement("div");
  leftSide.className = "bracket-side left";
  for (const r of REGIONS_LEFT) {
    leftSide.appendChild(renderRegion(r, regionBrackets[r], eliminated, "left"));
  }
  container.appendChild(leftSide);

  // Center: Final Four + Championship
  const center = document.createElement("div");
  center.className = "bracket-center";

  const ff1Label = document.createElement("div");
  ff1Label.className = "ff-label";
  ff1Label.textContent = "Semifinal";
  center.appendChild(ff1Label);

  const ff1Cell = document.createElement("div");
  ff1Cell.className = "center-game";
  ff1Cell.appendChild(renderGameCell(finalFour.semi1, eliminated, "final4"));
  center.appendChild(ff1Cell);

  const champLabel = document.createElement("div");
  champLabel.className = "champ-label";
  champLabel.textContent = "Championship";
  center.appendChild(champLabel);

  const champCell = document.createElement("div");
  champCell.className = "center-game";
  champCell.appendChild(renderGameCell(finalFour.champ, eliminated, "championship"));
  center.appendChild(champCell);

  const ff2Label = document.createElement("div");
  ff2Label.className = "ff-label";
  ff2Label.textContent = "Semifinal";
  center.appendChild(ff2Label);

  const ff2Cell = document.createElement("div");
  ff2Cell.className = "center-game";
  ff2Cell.appendChild(renderGameCell(finalFour.semi2, eliminated, "final4"));
  center.appendChild(ff2Cell);

  container.appendChild(center);

  // Right side: West, Midwest
  const rightSide = document.createElement("div");
  rightSide.className = "bracket-side right";
  for (const r of REGIONS_RIGHT) {
    rightSide.appendChild(renderRegion(r, regionBrackets[r], eliminated, "right"));
  }
  container.appendChild(rightSide);

  // After render, fix the scroll container height to match scaled content
  requestAnimationFrame(() => {
    const scrollEl = container.closest(".mini-bracket-scroll");
    if (scrollEl) {
      const scaledHeight = container.offsetHeight * 0.7;
      scrollEl.style.height = scaledHeight + "px";
    }
  });
}

function renderMobileLeaderboard(standings, gamesData) {
  const container = document.getElementById("mobile-leaderboard-body");
  if (!container) return;
  container.innerHTML = "";

  const updatedEl = document.getElementById("mobile-last-updated");
  if (updatedEl && gamesData.lastUpdated) {
    updatedEl.textContent = `Updated: ${new Date(gamesData.lastUpdated).toLocaleString()}`;
  }

  standings.forEach((s, i) => renderLeaderboardCard(container, s, i + 1));
}

// =============================================================
// MAIN
// =============================================================

async function refresh() {
  try {
    const { picks, games } = await loadData();
    const gameLookup = buildGameLookup(games.games);
    const eliminated = buildEliminatedSet(games.games);

    // Build bracket for each region
    const regionBrackets = {};
    for (const region of ALL_REGIONS) {
      regionBrackets[region] = buildRegionBracket(region, games, picks, gameLookup, eliminated);
    }

    const finalFour = buildFinalFour(regionBrackets, games, picks, gameLookup);
    const standings = computeStandings(picks, games);

    // Store data for filter re-renders
    _mobileRenderData = { regionBrackets, finalFour, eliminated };

    renderLegend(picks);
    renderFilterBars();
    renderBracket(regionBrackets, finalFour, eliminated);
    renderLeaderboard(standings, games);
    renderMobileBracket(regionBrackets, finalFour, eliminated);
    renderMobileLeaderboard(standings, games);
  } catch (err) {
    console.error("Error:", err);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  refresh();
  if (REFRESH_INTERVAL > 0) setInterval(refresh, REFRESH_INTERVAL);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closePicksPopover();
  });
});
