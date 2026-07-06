#!/usr/bin/env python3
"""
Global F1 Battle 2026 – Dashboard Generator
Usage: python generate_dashboard.py <input.csv> [output.html]

CSV format:
  Date, Grand Prix, Tag, Player1 (Tag1), Player2 (Tag2), ...
Tag column values:
  O  = ordinary/completed race
  P  = previous race (second-most-recent, used as "before last race")
  L  = last race (most recent, used for race-result column)
  N  = not happened yet (future – ignored)
  (blank / other rows are also ignored)
"""

import sys
import csv
import json
import os
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_csv(path: str):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = [row for row in reader]
    return rows


def parse_data(rows):
    """
    Returns:
      players          : list of player column header strings (full "Name (Tag)")
      races            : list of {"label": str, "tag": str, "scores": {player: int}}
      cumulative       : {player: [cum_score_after_race_0, ..., after_race_n]}
      last_race_row    : row dict for tag=="L"
      prev_race_row    : row dict for tag=="P"
    """
    # Identify player columns (everything after Date, Grand Prix, Tag)
    fixed_cols = {"Date", "Grand Prix", "Tag"}
    players = [k for k in rows[0].keys() if k not in fixed_cols]

    races = []
    for row in rows:
        tag = row["Tag"].strip().upper()
        # Skip future/unpopulated races – only process completed ones
        if tag not in ("O", "P", "L"):
            continue
        label = row["Grand Prix"].strip()
        scores = {}
        for p in players:
            val = row[p].strip()
            try:
                scores[p] = int(val)
            except ValueError:
                scores[p] = 0
        races.append({"label": label, "tag": tag, "scores": scores})

    # Build cumulative totals
    cumulative = {p: [] for p in players}
    running = {p: 0 for p in players}
    for race in races:
        for p in players:
            running[p] += race["scores"][p]
            cumulative[p].append(running[p])

    last_race_row = next((r for r in reversed(races) if r["tag"] == "L"), None)
    prev_race_row = next((r for r in reversed(races) if r["tag"] == "P"), None)

    return players, races, cumulative, last_race_row, prev_race_row


def rank_players(players, totals):
    """Return players sorted by totals descending, with 1-based ranks."""
    sorted_p = sorted(players, key=lambda p: totals[p], reverse=True)
    return sorted_p  # ranked list


def compute_leaderboard(players, races, cumulative):
    """
    Returns list of dicts sorted by final cumulative total (desc):
      rank, player, prev_total, race_pts, new_total, gap_to_leader, rank_change
    """
    last_race_idx = None
    prev_race_idx = None
    for i, r in enumerate(races):
        if r["tag"] == "L":
            last_race_idx = i
        if r["tag"] == "P":
            prev_race_idx = i

    # New totals (after L race)
    if last_race_idx is not None:
        new_totals = {p: cumulative[p][last_race_idx] for p in players}
    else:
        # No L race – use latest available
        latest = max(i for i, r in enumerate(races) if cumulative[players[0]][i] is not None)
        new_totals = {p: cumulative[p][latest] for p in players}

    # Prev totals (after P race, i.e. before L race)
    if prev_race_idx is not None:
        prev_totals = {p: cumulative[p][prev_race_idx] for p in players}
    elif last_race_idx is not None and last_race_idx > 0:
        prev_totals = {p: cumulative[p][last_race_idx - 1] for p in players}
    else:
        prev_totals = {p: 0 for p in players}

    # Race points for L race
    if last_race_idx is not None:
        race_pts = {p: races[last_race_idx]["scores"][p] for p in players}
    else:
        race_pts = {p: 0 for p in players}

    # Rankings
    new_ranked = rank_players(players, new_totals)
    prev_ranked = rank_players(players, prev_totals)
    prev_rank_map = {p: i + 1 for i, p in enumerate(prev_ranked)}
    new_rank_map = {p: i + 1 for i, p in enumerate(new_ranked)}

    leader_total = new_totals[new_ranked[0]]

    board = []
    for p in new_ranked:
        new_rank = new_rank_map[p]
        prev_rank = prev_rank_map[p]
        rank_change = prev_rank - new_rank  # positive = moved up
        board.append({
            "rank": new_rank,
            "player": p,
            "prev_total": prev_totals[p],
            "race_pts": race_pts[p],
            "new_total": new_totals[p],
            "gap": new_totals[p] - leader_total,  # <=0
            "rank_change": rank_change,
        })
    return board


def per_race_scores(players, races):
    """Returns {player: [pts_race0, pts_race1, ...]} for all races (excluding start row)."""
    result = {p: [] for p in players}
    for r in races:
        for p in players:
            result[p].append(r["scores"][p])
    return result


def player_stats(players, races, cumulative):
    """Returns {player: {avg, best, best_race_label, total}}"""
    stats = {}
    # Only count races after the first row (which is start with 0s)
    race_rows = [r for r in races if r["label"].strip().upper() != "00 - START"
                 and r["tag"] in ("O", "P", "L")]
    for p in players:
        pts = [r["scores"][p] for r in race_rows]
        if pts:
            avg = sum(pts) / len(pts)
            best = max(pts)
            best_label = race_rows[pts.index(best)]["label"]
        else:
            avg, best, best_label = 0, 0, "-"
        final_idx = None
        for i, r in enumerate(races):
            if r["tag"] in ("O", "P", "L"):
                final_idx = i
        total = cumulative[p][final_idx] if final_idx is not None else 0
        stats[p] = {"avg": round(avg, 1), "best": best,
                    "best_race": best_label, "total": total}
    return stats


def race_winners(players, races):
    """Returns list of {label, winner, pts} for each completed race."""
    winners = []
    for r in races:
        if r["tag"] not in ("O", "P", "L"):
            continue
        if r["label"].strip().upper() == "00 - START":
            continue
        best_p = max(players, key=lambda p: r["scores"][p])
        winners.append({
            "label": r["label"],
            "winner": best_p,
            "pts": r["scores"][best_p],
        })
    return winners


# ---------------------------------------------------------------------------
# Colour palette (16 distinct F1-flavoured colours)
# ---------------------------------------------------------------------------

PLAYER_COLORS = [
    "#E8002D",  # F1 Red
    "#00D2BE",  # Mercedes teal
    "#FF8000",  # McLaren orange
    "#0600EF",  # Williams blue
    "#DC0000",  # Ferrari red variant
    "#1E41FF",  # Racing Bulls blue
    "#B6BABD",  # Haas silver
    "#52E252",  # Lime green
    "#FF1E00",  # Red variant
    "#C92D4B",  # Aston pink
    "#006F62",  # Aston green
    "#FFF500",  # Yellow
    "#3671C6",  # Alpine blue
    "#FF87BC",  # Pink
    "#A6C84F",  # Olive lime
    "#9B0000",  # Dark red
]


# ---------------------------------------------------------------------------
# HTML / JS template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Global F1 Battle 2026</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg:        #0a0e1a;
    --panel:     #111827;
    --panel2:    #161f2e;
    --border:    #1e2d45;
    --accent:    #e8002d;
    --accent2:   #00d2be;
    --text:      #e8eaf0;
    --text-dim:  #7a8ba0;
    --green:     #00e676;
    --red:       #ff1744;
    --gold:      #ffd700;
    --silver:    #c0c0c0;
    --bronze:    #cd7f32;
    --mono:      'JetBrains Mono', 'Courier New', monospace;
    --sans:      'Inter', 'Segoe UI', Arial, sans-serif;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    min-height: 100vh;
  }

  /* ── Header ─────────────────────────────────────────── */
  .header {
    background: linear-gradient(135deg, #0d1220 0%, #1a0a10 50%, #0d1220 100%);
    border-bottom: 2px solid var(--accent);
    padding: 18px 32px;
    display: flex;
    align-items: center;
    gap: 20px;
  }
  .header-flag {
    font-size: 2.2rem;
    flex-shrink: 0;
  }
  .header-title {
    flex: 1;
  }
  .header-title h1 {
    font-size: 1.6rem;
    font-weight: 800;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: var(--text);
  }
  .header-title h1 span { color: var(--accent); }
  .header-subtitle {
    font-size: 0.75rem;
    color: var(--text-dim);
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-top: 2px;
  }
  .header-badge {
    background: var(--accent);
    color: #fff;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    padding: 4px 10px;
    border-radius: 2px;
  }

  /* ── Stat Cards ──────────────────────────────────────── */
  .stats-strip {
    display: flex;
    gap: 12px;
    padding: 16px 24px;
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    overflow-x: auto;
    flex-wrap: wrap;
  }
  .stat-card {
    background: var(--panel2);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 10px 16px;
    min-width: 160px;
    flex: 1;
  }
  .stat-card .label {
    font-size: 0.65rem;
    color: var(--text-dim);
    letter-spacing: 1.5px;
    text-transform: uppercase;
    margin-bottom: 4px;
  }
  .stat-card .value {
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .stat-card .sub {
    font-size: 0.7rem;
    color: var(--text-dim);
    margin-top: 2px;
  }
  .stat-card.accent  { border-left: 3px solid var(--accent); }
  .stat-card.accent2 { border-left: 3px solid var(--accent2); }
  .stat-card.gold    { border-left: 3px solid var(--gold); }
  .stat-card.green   { border-left: 3px solid var(--green); }

  /* ── Main layout ─────────────────────────────────────── */
  .main {
    padding: 20px 24px;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }

  /* ── Section titles ──────────────────────────────────── */
  .section-title {
    font-size: 0.7rem;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: var(--text-dim);
    border-left: 3px solid var(--accent);
    padding-left: 8px;
    margin-bottom: 12px;
  }

  /* ── Chart containers ────────────────────────────────── */
  .chart-panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 16px 16px 8px;
  }
  .chart-tabs {
    display: flex;
    gap: 8px;
    margin-bottom: 12px;
  }
  .tab-btn {
    background: var(--panel2);
    border: 1px solid var(--border);
    color: var(--text-dim);
    padding: 5px 14px;
    border-radius: 3px;
    font-size: 0.72rem;
    letter-spacing: 1px;
    text-transform: uppercase;
    cursor: pointer;
    transition: all .15s;
  }
  .tab-btn:hover { border-color: var(--accent2); color: var(--text); }
  .tab-btn.active { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 700; }

  .chart-wrap { width: 100%; }
  .chart-hidden { display: none; }
  .chart-canvas-wrap {
    position: relative;
    height: 520px;
  }
  .heatmap-wrap {
    overflow-x: auto;
    overflow-y: hidden;
  }
  @media (max-width: 900px) { .chart-canvas-wrap { height: 460px; } }
  @media (max-width: 600px) { .chart-canvas-wrap { height: 520px; } }

  /* ── Leaderboard ─────────────────────────────────────── */
  .lb-table {
    width: 100%;
    border-collapse: collapse;
    font-family: var(--mono);
    font-size: 0.82rem;
  }
  .lb-table th {
    background: #0d1220;
    color: var(--text-dim);
    font-size: 0.65rem;
    letter-spacing: 1.5px;
    text-transform: uppercase;
    padding: 8px 10px;
    text-align: center;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }
  .lb-table th.wrap-hdr { white-space: normal; word-break: break-word; line-height: 1.2; }
  .lb-table td {
    padding: 7px 10px;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
    text-align: center;
  }
  .lb-table td.player-td { text-align: left; }
  .lb-table tr:last-child td { border-bottom: none; }
  .lb-table tr:hover td { background: rgba(255,255,255,0.03); }

  .pos-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 26px; height: 26px;
    border-radius: 3px;
    font-weight: 800;
    font-size: 0.85rem;
    background: var(--panel2);
    color: var(--text);
  }
  .pos-badge.p1 { background: var(--gold); color: #000; }
  .pos-badge.p2 { background: var(--silver); color: #000; }
  .pos-badge.p3 { background: var(--bronze); color: #000; }

  .team-dot {
    display: inline-block;
    width: 8px; height: 8px;
    border-radius: 50%;
    margin-right: 8px;
    flex-shrink: 0;
  }
  .player-name {
    display: flex;
    align-items: center;
  }
  .player-name-text {
    display: flex;
    flex-direction: column;
    line-height: 1.3;
  }
  .player-person {
    font-weight: 600;
    white-space: nowrap;
  }
  .player-team {
    font-size: 0.75em;
    color: var(--text-dim);
    white-space: nowrap;
  }

  .change-up   { color: var(--green); font-weight: 700; }
  .change-down { color: var(--red);   font-weight: 700; }
  .change-same { color: var(--text-dim); }

  .gap-col { color: var(--text-dim); }
  .gap-col.leader { color: var(--accent2); }

  .pts-col { color: var(--text); font-weight: 600; }
  .race-pts-pos { color: var(--green); }
  .race-pts-neg { color: var(--red); }

  /* avg bar */
  .lb-table td:nth-child(8) { text-align: left; }
  .avg-bar-wrap {
    display: flex;
    align-items: center;
    justify-content: flex-start;
    gap: 6px;
  }
  .avg-bar {
    height: 6px;
    border-radius: 3px;
    background: var(--accent2);
    opacity: 0.7;
  }
  .avg-val { font-size: 0.78rem; color: var(--text-dim); min-width: 36px; }

  /* ── Race winners rows ───────────────────────────────── */
  .winner-row {
    background: var(--panel2);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 8px 12px;
    margin-bottom: 6px;
    display: flex;
    align-items: flex-start;
    gap: 12px;
    flex-wrap: wrap;
  }
  .winner-row-header {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 160px;
    flex-shrink: 0;
  }
  .wr-name {
    font-size: 0.82rem;
    font-weight: 700;
    color: var(--text);
    white-space: nowrap;
  }
  .wr-badge {
    background: var(--accent);
    color: #fff;
    font-size: 0.6rem;
    font-weight: 700;
    letter-spacing: 1px;
    padding: 2px 6px;
    border-radius: 2px;
    white-space: nowrap;
  }
  .winner-races {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
    align-items: center;
  }
  .win-pill {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 2px 7px;
    font-size: 0.65rem;
    color: var(--text-dim);
    white-space: nowrap;
    cursor: default;
  }
  .win-pill:hover { border-color: var(--accent2); color: var(--text); }

  /* ── Footer ──────────────────────────────────────────── */
  .footer {
    text-align: center;
    padding: 16px;
    font-size: 0.66rem;
    color: var(--text-dim);
    border-top: 1px solid var(--border);
    letter-spacing: 1px;
  }

  /* ── Responsive — Tablet (≤900px) ───────────────────── */
  @media (max-width: 900px) {
    .header { padding: 14px 20px; }
    .header-title h1 { font-size: 1.3rem; }
    .stats-strip { padding: 12px 16px; gap: 10px; }
    .stat-card { min-width: 140px; }
    .main { padding: 14px 16px; }
    /* Hide avg/best columns to reduce table width */
    .lb-table th:nth-child(8),
    .lb-table td:nth-child(8),
    .lb-table th:nth-child(9),
    .lb-table td:nth-child(9) { display: none; }
  }

  /* ── Responsive — Mobile (≤600px) ───────────────────── */
  @media (max-width: 600px) {
    .header { padding: 10px 14px; gap: 10px; }
    .header-flag { font-size: 1.5rem; }
    .header-title h1 { font-size: 1rem; letter-spacing: 1px; }
    .header-subtitle { font-size: 0.62rem; letter-spacing: 0.5px; }
    .header-badge { font-size: 0.62rem; padding: 3px 7px; }

    .stats-strip { padding: 10px 10px; gap: 8px; }
    .stat-card {
      min-width: calc(50% - 4px);
      flex: 1 1 calc(50% - 4px);
      padding: 8px 12px;
    }
    .stat-card .value { font-size: 0.95rem; }

    .main { padding: 10px 10px; gap: 12px; }
    .chart-panel { padding: 12px 10px 6px; }

    .chart-tabs { flex-wrap: wrap; gap: 6px; }
    .tab-btn {
      flex: 1 1 calc(50% - 3px);
      max-width: calc(50% - 3px);
      text-align: center;
      padding: 7px 6px;
      font-size: 0.62rem;
      letter-spacing: 0.5px;
    }

    /* Leaderboard: hide AVG/RACE(8), BEST RACE(9) only; PREV TOTAL visible */
    .lb-table th:nth-child(8),
    .lb-table td:nth-child(8),
    .lb-table th:nth-child(9),
    .lb-table td:nth-child(9) { display: none; }

    /* Wrap only multi-word column headers to save horizontal space */
    .lb-table th.wrap-hdr { white-space: normal; word-break: break-word; line-height: 1.2; letter-spacing: 0.5px; }

    /* Allow player name cell (col 3) to wrap for two-line layout */
    .lb-table td:nth-child(3) { white-space: normal; max-width: 120px; }
    .player-team { white-space: normal; word-break: break-word; }

    .lb-table { font-size: 0.62rem; }
    .lb-table th,
    .lb-table td { padding: 5px 4px; }
    .pos-badge { width: 20px; height: 20px; font-size: 0.68rem; }

    .winner-row-header { min-width: 120px; }
    .wr-name { font-size: 0.75rem; }
    .win-pill { font-size: 0.6rem; padding: 2px 5px; }
  }

  /* ── Responsive — Very small (≤380px) ───────────────── */
  @media (max-width: 380px) {
    .header-flag { display: none; }
    .header-title h1 { font-size: 0.95rem; }
    .stat-card { flex: 1 1 100%; }
    /* Also hide GAP column to give more room */
    .lb-table th:nth-child(7),
    .lb-table td:nth-child(7) { display: none; }
  }
</style>
</head>
<body>

<!-- ══ HEADER ══════════════════════════════════════════════════════════════ -->
<div class="header">
  <div class="header-flag">🏁</div>
  <div class="header-title">
    <h1>Global <span>F1</span> Battle <span>__YEAR__</span></h1>
    <div class="header-subtitle">Fantasy League · Season Standings · __SUBTITLE__</div>
  </div>
  <div class="header-badge">__BADGE__</div>
</div>

<!-- ══ STAT CARDS ══════════════════════════════════════════════════════════ -->
<div class="stats-strip">__STAT_CARDS__</div>

<!-- ══ MAIN ════════════════════════════════════════════════════════════════ -->
<div class="main">

  <!-- ── Cumulative / Rank charts ──────────────────────── -->
  <div class="chart-panel">
    <div class="section-title">Championship Points Chart</div>
    <div class="chart-tabs">
      <button class="tab-btn active" onclick="showChart('cum')">Cumulative Points</button>
      <button class="tab-btn" onclick="showChart('rank')">Position Battle</button>
      <button class="tab-btn" onclick="showChart('heat')">Race Heatmap</button>
      <button class="tab-btn" onclick="showChart('dot')">Score Spread</button>
    </div>
    <div id="chart-cum"  class="chart-wrap"><div class="chart-canvas-wrap"><canvas id="canvas-cum"></canvas></div></div>
    <div id="chart-rank" class="chart-wrap chart-hidden"><div class="chart-canvas-wrap"><canvas id="canvas-rank"></canvas></div></div>
    <div id="chart-heat" class="chart-wrap chart-hidden"><div class="heatmap-wrap"><canvas id="canvas-heat"></canvas></div></div>
    <div id="chart-dot"  class="chart-wrap chart-hidden"><div class="chart-canvas-wrap"><canvas id="canvas-dot"></canvas></div></div>
  </div>

  <!-- ── Leaderboard ───────────────────────────────────── -->
  <div class="chart-panel">
    <div class="section-title">Timing Tower — __LAST_RACE_LABEL__</div>
    <div style="overflow-x:auto;">
      <table class="lb-table">
        <thead>
          <tr>
            <th>POS</th>
            <th>CHG</th>
            <th style="text-align:left">PLAYER</th>
            <th class="wrap-hdr">PREV TOTAL</th>
            <th class="wrap-hdr">RACE PTS</th>
            <th class="wrap-hdr">NEW TOTAL</th>
            <th>GAP</th>
            <th class="wrap-hdr">AVG / RACE</th>
            <th class="wrap-hdr">BEST RACE</th>
          </tr>
        </thead>
        <tbody>
__LEADERBOARD_ROWS__
        </tbody>
      </table>
    </div>
  </div>

  <!-- ── Winners grid ─────────────────────────────────── -->
  <div class="chart-panel">
    <div class="section-title">Individual Race Winners</div>
    <div class="winners-list">__WINNER_CARDS__</div>
  </div>

</div><!-- /main -->

<div class="footer">Global F1 Battle __YEAR__ · Data from F1 Fantasy League Game · Built by Saul Dobilas</div>

<script>
// ── Chart data injected from Python ─────────────────────
const DATA = __JSON_DATA__;

// ── Per-player colours (keyed by player name) ──────────
const PCOLORS = __PLAYER_COLORS__;

// ── Chart registry & lazy-init state ────────────────────
const charts = {};
const chartInited = { cum: false, rank: false, heat: false, dot: false };

// ── Tab switching ────────────────────────────────────────
function showChart(id) {
  ['cum','rank','heat','dot'].forEach(k => {
    document.getElementById('chart-' + k).classList.toggle('chart-hidden', k !== id);
  });
  document.querySelectorAll('.tab-btn').forEach((btn, i) => {
    btn.classList.toggle('active', ['cum','rank','heat','dot'][i] === id);
  });
  if (!chartInited[id]) {
    chartInited[id] = true;
    renderChart(id);
  } else if (id !== 'heat' && charts[id]) {
    setTimeout(() => charts[id].resize(), 10);
  }
}

// ── Resize on window resize ─────────────────────────────
window.addEventListener('resize', () => {
  ['cum','rank','dot'].forEach(id => { if (charts[id]) charts[id].resize(); });
  if (chartInited.heat) drawHeatmap();
});

// ── Shared Chart.js defaults ─────────────────────────────
Chart.defaults.color = '#7a8ba0';
Chart.defaults.font.family = "'JetBrains Mono', 'Courier New', monospace";
Chart.defaults.font.size = 10;

function sharedOptions(yTitle, reverseY) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 350 },
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: {
        position: 'bottom',
        labels: {
          color: '#e8eaf0',
          boxWidth: 10, boxHeight: 10,
          padding: 8,
          font: { size: 9 },
          usePointStyle: true,
          pointStyle: 'circle',
          generateLabels: chart => {
            const orig = Chart.defaults.plugins.legend.labels.generateLabels(chart);
            return orig.map(item => ({
              ...item,
              text: item.text.includes(' (') ? item.text.split(' (')[0] : item.text
            }));
          }
        }
      },
      tooltip: {
        backgroundColor: '#0d1525',
        borderColor: '#e8002d',
        borderWidth: 1,
        titleColor: '#7a8ba0',
        bodyColor: '#e8eaf0',
        padding: 10,
        bodyFont: { size: 10 }
      }
    },
    scales: {
      x: {
        ticks: { color: '#7a8ba0', maxRotation: 45 },
        grid: { color: '#1a2640' },
        border: { color: '#1e2d45' }
      },
      y: {
        reverse: !!reverseY,
        ticks: { color: '#7a8ba0' },
        grid: { color: '#1a2640' },
        border: { color: '#1e2d45' },
        title: { display: true, text: yTitle, color: '#7a8ba0', font: { size: 10 } }
      }
    }
  };
}

function lineDatasets(dataKey) {
  return DATA.players.map(p => ({
    label: p,
    data: DATA[dataKey][p],
    borderColor: PCOLORS[p],
    backgroundColor: PCOLORS[p],
    pointRadius: 4,
    pointHoverRadius: 7,
    borderWidth: 2,
    tension: 0.1,
    fill: false
  }));
}

// ── Option C: Race Heatmap (pure canvas, no Chart.js) ────────
function drawHeatmap() {
  const canvas = document.getElementById('canvas-heat');
  if (!canvas) return;

  // Skip the '00 - Start' column
  const raceIdxs = DATA.race_labels.reduce((a, l, i) => { if (!l.startsWith('00')) a.push(i); return a; }, []);
  const raceLabels = raceIdxs.map(i => DATA.race_labels[i]);
  const players = DATA.players;

  const mob = window.innerWidth <= 600;
  const CELL_W  = mob ? 54  : 70;
  const CELL_H  = mob ? 26  : 32;
  const LABEL_W = mob ? 78  : 108;
  const HDR_H   = 46;
  const W = LABEL_W + raceLabels.length * CELL_W;
  const H = HDR_H   + players.length   * CELL_H;

  canvas.width  = W;  canvas.height = H;
  canvas.style.width  = W + 'px';
  canvas.style.height = H + 'px';
  canvas.parentElement.style.height = H + 'px';

  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#0d1525';
  ctx.fillRect(0, 0, W, H);

  // Per-race min/max for colour normalisation
  const raceStats = raceIdxs.map(ri => {
    const s = players.map(p => DATA.per_race[p][ri]);
    return { min: Math.min(...s), max: Math.max(...s) };
  });

  function cellColor(score, min, max) {
    const t = max > min ? (score - min) / (max - min) : 0.5;
    // Navy → bright teal
    return `rgb(${Math.round((1-t)*18+t*0)},${Math.round((1-t)*38+t*210)},${Math.round((1-t)*68+t*190)})`;
  }

  // Race column headers
  const hdrFont = (mob ? '7.5' : '9') + "px 'JetBrains Mono',monospace";
  ctx.font = hdrFont;  ctx.fillStyle = '#7a8ba0';  ctx.textAlign = 'center';
  raceLabels.forEach((lbl, ci) => {
    const x = LABEL_W + ci * CELL_W + CELL_W / 2;
    const p = lbl.split(' - ');
    if (p.length === 2) { ctx.fillText(p[0], x, 14); ctx.fillText(p[1], x, 30); }
    else { ctx.fillText(lbl, x, HDR_H / 2 + 4); }
  });

  // Player rows
  const nameFont = (mob ? '8.5' : '10') + "px 'JetBrains Mono',monospace";
  const valFont  = 'bold ' + (mob ? '9' : '11') + "px 'JetBrains Mono',monospace";
  players.forEach((p, ri) => {
    const y   = HDR_H + ri * CELL_H;
    const sName = p.includes(' (') ? p.split(' (')[0] : p;

    // Player label
    ctx.font = nameFont;  ctx.fillStyle = '#e8eaf0';  ctx.textAlign = 'right';
    ctx.fillText(sName, LABEL_W - 10, y + CELL_H / 2 + 3);
    // Player colour dot
    ctx.beginPath();
    ctx.arc(LABEL_W - 4, y + CELL_H / 2, 3, 0, Math.PI * 2);
    ctx.fillStyle = PCOLORS[p] || '#888';  ctx.fill();

    // Score cells
    raceIdxs.forEach((origIdx, ci) => {
      const score = DATA.per_race[p][origIdx];
      const { min, max } = raceStats[ci];
      const cx = LABEL_W + ci * CELL_W;
      ctx.fillStyle = cellColor(score, min, max);
      ctx.fillRect(cx + 1, y + 1, CELL_W - 2, CELL_H - 2);
      const t = max > min ? (score - min) / (max - min) : 0.5;
      ctx.font = valFont;
      ctx.fillStyle = t > 0.45 ? '#001015' : '#c8d0e0';
      ctx.textAlign = 'center';
      ctx.fillText(String(score), cx + CELL_W / 2, y + CELL_H / 2 + 4);
    });
  });

  // Grid lines
  ctx.strokeStyle = '#1e2d45';  ctx.lineWidth = 1;
  for (let ci = 0; ci <= raceLabels.length; ci++) {
    const x = LABEL_W + ci * CELL_W + 0.5;
    ctx.beginPath(); ctx.moveTo(x, HDR_H); ctx.lineTo(x, H); ctx.stroke();
  }
  for (let ri = 0; ri <= players.length; ri++) {
    const y = HDR_H + ri * CELL_H + 0.5;
    ctx.beginPath(); ctx.moveTo(LABEL_W, y); ctx.lineTo(W, y); ctx.stroke();
  }
}

// ── Option D: Score Spread — range bars + player dots ───────
function renderDotChart(ctx) {
  const raceIdxs = DATA.race_labels.reduce((a, l, i) => { if (!l.startsWith('00')) a.push(i); return a; }, []);
  const raceLabels = raceIdxs.map(i => DATA.race_labels[i]);

  const rangeData = raceIdxs.map(ri => {
    const scores = DATA.players.map(p => DATA.per_race[p][ri]);
    return [Math.min(...scores), Math.max(...scores)];
  });

  const datasets = [
    {
      type: 'bar',
      label: 'Score Range',
      data: rangeData,
      backgroundColor: 'rgba(255,255,255,0.07)',
      borderColor:     'rgba(255,255,255,0.16)',
      borderWidth: 1,
      barPercentage: 0.55,
      categoryPercentage: 0.95,
      order: 10
    },
    ...DATA.players.map(p => ({
      type: 'line',
      label: p,
      data: raceIdxs.map(ri => DATA.per_race[p][ri]),
      borderColor: PCOLORS[p],
      backgroundColor: PCOLORS[p],
      pointRadius: 5,
      pointHoverRadius: 9,
      showLine: false,
      order: 1
    }))
  ];

  const opts = sharedOptions('Points Scored Per Race', false);
  opts.interaction = { mode: 'nearest', intersect: false, axis: 'xy' };
  opts.plugins.tooltip = Object.assign({}, opts.plugins.tooltip, {
    callbacks: {
      title: items => raceLabels[items[0]?.dataIndex] || '',
      label: c => {
        if (c.dataset.label === 'Score Range')
          return ` Spread: ${c.raw[0]}–${c.raw[1]} pts`;
        const n = c.dataset.label.includes(' (') ? c.dataset.label.split(' (')[0] : c.dataset.label;
        return ` ${n}: ${c.parsed.y} pts`;
      }
    }
  });

  charts.dot = new Chart(ctx, {
    type: 'bar',
    data: { labels: raceLabels, datasets },
    options: opts
  });
}

// ── Render chart by id ─────────────────────────────────
function renderChart(id) {
  if (id === 'heat') { drawHeatmap(); return; }
  const canvas = document.getElementById('canvas-' + id);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  if (id === 'cum') {
    const opts = sharedOptions('Cumulative Points', false);
    const mob = window.innerWidth <= 600;
    opts.plugins.tooltip.callbacks = {
      title: items => items[0]?.label || '',
      label: c => {
        const n = c.dataset.label.includes(' (') ? c.dataset.label.split(' (')[0] : c.dataset.label;
        return ' ' + n + ': ' + c.parsed.y + ' pts';
      }
    };
    if (mob) {
      // On mobile: show tooltip only for the nearest tapped point (single player)
      opts.interaction = { mode: 'nearest', intersect: true, axis: 'xy' };
    }
    charts.cum = new Chart(ctx, {
      type: 'line',
      data: { labels: DATA.race_labels, datasets: lineDatasets('cumulative') },
      options: opts
    });

  } else if (id === 'rank') {
    const rankIdxs = DATA.race_labels.reduce((a, l, i) => { if (!l.startsWith('00')) a.push(i); return a; }, []);
    const rankLabels = rankIdxs.map(i => DATA.race_labels[i]);
    const opts = sharedOptions('Position  (1 = Leader)', true);
    opts.scales.y.ticks = Object.assign({}, opts.scales.y.ticks, {
      stepSize: 1,
      callback: v => Number.isInteger(v) ? 'P' + v : ''
    });
    opts.plugins.tooltip.callbacks = {
      title: items => rankLabels[items[0]?.dataIndex] || '',
      label: c => {
        const n = c.dataset.label.includes(' (') ? c.dataset.label.split(' (')[0] : c.dataset.label;
        return ' ' + n + ': P' + c.parsed.y;
      }
    };
    if (window.innerWidth <= 600) {
      opts.interaction = { mode: 'nearest', intersect: true, axis: 'xy' };
    }
    charts.rank = new Chart(ctx, {
      type: 'line',
      data: {
        labels: rankLabels,
        datasets: DATA.players.map(p => ({
          label: p,
          data: rankIdxs.map(i => DATA.rankings[p][i]),
          borderColor: PCOLORS[p],
          backgroundColor: PCOLORS[p],
          pointRadius: 4,
          pointHoverRadius: 7,
          borderWidth: 2,
          tension: 0.1,
          fill: false
        }))
      },
      options: opts
    });

  } else if (id === 'dot') {
    renderDotChart(ctx);
  }
}

// ── Init 'cum' chart immediately on load ─────────────────
chartInited.cum = true;
renderChart('cum');

</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Short display name (strip tag in parentheses for tight spaces)
# ---------------------------------------------------------------------------

def short_name(full_name: str) -> str:
    """Returns just the part before ' (' e.g. 'Vaidas K'"""
    if " (" in full_name:
        return full_name[:full_name.index(" (")]
    return full_name


# ---------------------------------------------------------------------------
# Build HTML pieces
# ---------------------------------------------------------------------------

def build_stat_cards(players, races, cumulative, board, leaderboard_label):
    last_race = next((r for r in reversed(races) if r["tag"] == "L"), None)
    
    # Leader
    leader = board[0]
    leader_card = f"""
    <div class="stat-card accent">
      <div class="label">🏆 Leader</div>
      <div class="value">{short_name(leader['player'])}</div>
      <div class="sub">{leader['new_total']:,} pts</div>
    </div>"""

    # Biggest mover this race
    movers = sorted(board, key=lambda x: x["rank_change"], reverse=True)
    biggest_up = movers[0]
    move_label = f"+{biggest_up['rank_change']} position{'s' if abs(biggest_up['rank_change'])!=1 else ''}" if biggest_up["rank_change"] > 0 else "No movement"
    mover_card = f"""
    <div class="stat-card green">
      <div class="label">📈 Biggest Mover</div>
      <div class="value">{short_name(biggest_up['player'])}</div>
      <div class="sub">{move_label} · {biggest_up['race_pts']:+} pts</div>
    </div>"""

    # Highest race score this race
    if last_race:
        top_racer = max(players, key=lambda p: last_race["scores"][p])
        top_score = last_race["scores"][top_racer]
        top_card = f"""
    <div class="stat-card gold">
      <div class="label">⚡ Best This Race</div>
      <div class="value">{short_name(top_racer)}</div>
      <div class="sub">{top_score} pts · {leaderboard_label}</div>
    </div>"""
    else:
        top_card = ""

    completed = [r for r in races if r["tag"] in ("O", "P", "L") and r["label"].strip().upper() != "00 - START"]
    if completed:
        # Count race wins per player; ties within a race count as wins for all tied players.
        wins = {p: 0 for p in players}
        for r in completed:
            best_score = max(r["scores"][p] for p in players)
            for p in players:
                if r["scores"][p] == best_score:
                    wins[p] += 1

        top_win_count = max(wins.values())
        top_winners = [p for p in players if wins[p] == top_win_count]
        winners_display = ", ".join(short_name(p) for p in top_winners)
        plural = "s" if top_win_count != 1 else ""

        consist_card = f"""
    <div class="stat-card accent2">
      <div class="label">🥇 Most Race Wins</div>
      <div class="value">{winners_display}</div>
      <div class="sub">{top_win_count} win{plural} · {len(completed)} races</div>
    </div>"""
    else:
        consist_card = ""

    # Races completed
    races_done = len(completed)
    races_card = f"""
    <div class="stat-card">
      <div class="label">🏎 Races</div>
      <div class="value">{races_done}</div>
      <div class="sub">Completed this season</div>
    </div>"""

    return leader_card + mover_card + top_card + consist_card + races_card


def build_leaderboard_rows(board, players, races, cumulative, player_color_map):
    from statistics import mean
    completed = [r for r in races if r["tag"] in ("O", "P", "L") and r["label"].strip().upper() != "00 - START"]
    max_avg = 1
    if completed:
        all_avgs = [mean([r["scores"][p] for r in completed]) for p in players]
        max_avg = max(all_avgs) if all_avgs else 1

    rows_html = ""
    for entry in board:
        p = entry["player"]
        rank = entry["rank"]
        color = player_color_map[p]

        # Position badge class
        pc = {1: "p1", 2: "p2", 3: "p3"}.get(rank, "")
        badge = f'<span class="pos-badge {pc}">{rank}</span>'

        # Rank change
        chg = entry["rank_change"]
        if chg > 0:
            chg_html = f'<span class="change-up">▲{chg}</span>'
        elif chg < 0:
            chg_html = f'<span class="change-down">▼{abs(chg)}</span>'
        else:
            chg_html = f'<span class="change-same">—</span>'

        # Gap to leader
        gap = entry["gap"]
        if gap == 0:
            gap_html = '<span class="gap-col leader">LEADER</span>'
        else:
            gap_html = f'<span class="gap-col">{gap:,}</span>'

        # Race pts colour
        rpts = entry["race_pts"]
        rpts_class = "race-pts-pos" if rpts > 0 else ("race-pts-neg" if rpts < 0 else "pts-col")
        rpts_str = f'+{rpts}' if rpts > 0 else str(rpts)

        # Avg bar
        if completed:
            avg = mean([r["scores"][p] for r in completed])
            bar_w = int((avg / max_avg) * 80)
            avg_html = f'''<div class="avg-bar-wrap">
              <div class="avg-bar" style="width:{bar_w}px"></div>
              <span class="avg-val">{avg:.0f}</span>
            </div>'''
        else:
            avg_html = "—"

        # Best race
        if completed:
            all_pts = [(r["scores"][p], r["label"]) for r in completed]
            best_val, best_lbl = max(all_pts, key=lambda x: x[0])
            best_str = f'{best_val} pts · {best_lbl}'
        else:
            best_str = "—"

        # Split player name into person part and team part
        if " (" in p and p.endswith(")"):
            person_part = p[:p.index(" (")]
            team_part = p[p.index(" (")+2:-1]
        else:
            person_part = p
            team_part = ""
        team_line = f'<span class="player-team">{team_part}</span>' if team_part else ""

        rows_html += f"""          <tr>
            <td>{badge}</td>
            <td>{chg_html}</td>
            <td class="player-td"><div class="player-name"><span class="team-dot" style="background:{color}"></span><div class="player-name-text"><span class="player-person">{person_part}</span>{team_line}</div></div></td>
            <td class="pts-col">{entry['prev_total']:,}</td>
            <td class="{rpts_class}">{rpts_str}</td>
            <td class="pts-col"><b>{entry['new_total']:,}</b></td>
            <td>{gap_html}</td>
            <td>{avg_html}</td>
            <td style="font-size:0.72rem; color:#7a8ba0">{best_str}</td>
          </tr>
"""
    return rows_html


def build_winner_cards(winners, player_color_map):
    """Group races by winner, one row per player sorted by win count desc."""
    from collections import defaultdict
    wins_by_player = defaultdict(list)
    for w in winners:
        wins_by_player[w["winner"]].append(w)

    # Sort players by number of wins descending
    sorted_players = sorted(wins_by_player.keys(),
                            key=lambda p: len(wins_by_player[p]), reverse=True)

    html = ""
    for p in sorted_players:
        races = wins_by_player[p]
        color = player_color_map.get(p, "#888")
        name_short = short_name(p)
        win_count = len(races)
        plural = "S" if win_count != 1 else ""

        pills = "".join(
            f'<span class="win-pill" title="{w["pts"]} pts">{w["label"]}</span>'
            for w in races
        )

        html += f"""        <div class="winner-row" style="border-left: 3px solid {color}">
          <div class="winner-row-header">
            <span class="team-dot" style="background:{color}"></span>
            <span class="wr-name" title="{p}">{name_short}</span>
            <span class="wr-badge">{win_count} WIN{plural}</span>
          </div>
          <div class="winner-races">{pills}</div>
        </div>
"""
    return html


def compute_rankings_over_time(players, races, cumulative):
    """Returns {player: [rank_at_race0, rank_at_race1, ...]}"""
    ranks = {p: [] for p in players}
    for i in range(len(races)):
        totals_at_i = {p: cumulative[p][i] for p in players}
        sorted_p = sorted(players, key=lambda p: totals_at_i[p], reverse=True)
        rank_map = {p: j + 1 for j, p in enumerate(sorted_p)}
        for p in players:
            ranks[p].append(rank_map[p])
    return ranks


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def generate(csv_path: str, out_path: str):
    rows = load_csv(csv_path)
    players, races, cumulative, last_race, prev_race = parse_data(rows)
    board = compute_leaderboard(players, races, cumulative)
    winners = race_winners(players, races)
    stats = player_stats(players, races, cumulative)
    rankings_ot = compute_rankings_over_time(players, races, cumulative)

    # Assign colours
    player_color_map = {p: PLAYER_COLORS[i % len(PLAYER_COLORS)]
                        for i, p in enumerate(players)}

    # Race labels list
    race_labels = [r["label"] for r in races]

    # Per-race scores
    pr_scores = per_race_scores(players, races)

    # --- Determine display info ---
    last_race_label = last_race["label"] if last_race else "Season Total"
    completed_count = sum(1 for r in races if r["tag"] in ("O", "P", "L")
                          and r["label"].strip().upper() != "00 - START")
    subtitle = f"After {last_race_label}" if last_race else "Season in progress"
    badge = f"Race {completed_count}"
    # Try to extract year from CSV filename or data
    year = "2026"

    # --- JSON data blob ---
    # Sort players by final cumulative total (highest first) for legend ordering
    players_sorted = [entry["player"] for entry in board]
    json_data = {
        "players": players_sorted,
        "race_labels": race_labels,
        "cumulative": cumulative,
        "per_race": pr_scores,
        "rankings": rankings_ot,
        "stats": stats,
    }

    # --- Stat cards HTML ---
    stat_cards_html = build_stat_cards(players, races, cumulative, board, last_race_label)

    # --- Leaderboard rows ---
    lb_rows_html = build_leaderboard_rows(board, players, races, cumulative, player_color_map)

    # --- Winner cards ---
    winner_cards_html = build_winner_cards(winners, player_color_map)

    # --- Fill template ---
    html = HTML_TEMPLATE
    html = html.replace("__YEAR__", year)
    html = html.replace("__SUBTITLE__", subtitle)
    html = html.replace("__BADGE__", badge)
    html = html.replace("__STAT_CARDS__", stat_cards_html)
    html = html.replace("__LAST_RACE_LABEL__", last_race_label)
    html = html.replace("__LEADERBOARD_ROWS__", lb_rows_html)
    html = html.replace("__WINNER_CARDS__", winner_cards_html)
    html = html.replace("__JSON_DATA__", json.dumps(json_data, ensure_ascii=False))
    html = html.replace("__PLAYER_COLORS__", json.dumps(player_color_map, ensure_ascii=False))

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✓ Dashboard written to: {out_path}")


DEFAULT_CSV = "data/League_results.csv"
DEFAULT_OUT = "index.html"

if __name__ == "__main__":
    if len(sys.argv) < 2:
        csv_file = DEFAULT_CSV
        out_file = DEFAULT_OUT
        print(f"No arguments provided – using defaults: {csv_file} → {out_file}")
    else:
        csv_file = sys.argv[1]
        out_file = sys.argv[2] if len(sys.argv) >= 3 else Path(csv_file).stem + "_dashboard.html"
    generate(csv_file, out_file)
