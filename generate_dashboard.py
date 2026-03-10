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
        if r["tag"] not in ("N", "P", "L"):
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
<script src="https://cdn.plot.ly/plotly-2.32.0.min.js"></script>
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
    --mono:      'Courier New', Courier, monospace;
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
    text-align: left;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }
  .lb-table td {
    padding: 7px 10px;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }
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

  .change-up   { color: var(--green); font-weight: 700; }
  .change-down { color: var(--red);   font-weight: 700; }
  .change-same { color: var(--text-dim); }

  .gap-col { color: var(--text-dim); }
  .gap-col.leader { color: var(--accent2); }

  .pts-col { color: var(--text); font-weight: 600; }
  .race-pts-pos { color: var(--green); }
  .race-pts-neg { color: var(--red); }

  /* avg bar */
  .avg-bar-wrap {
    display: flex;
    align-items: center;
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
      <button class="tab-btn" onclick="showChart('bar')">Points Per Race</button>
    </div>
    <div id="chart-cum"  class="chart-wrap"></div>
    <div id="chart-rank" class="chart-wrap chart-hidden"></div>
    <div id="chart-bar"  class="chart-wrap chart-hidden"></div>
  </div>

  <!-- ── Leaderboard ───────────────────────────────────── -->
  <div class="chart-panel">
    <div class="section-title">Timing Tower — __LAST_RACE_LABEL__</div>
    <div style="overflow-x:auto;">
      <table class="lb-table">
        <thead>
          <tr>
            <th>POS</th>
            <th>PLAYER</th>
            <th style="text-align:right">PREV TOTAL</th>
            <th style="text-align:right">RACE PTS</th>
            <th style="text-align:right">NEW TOTAL</th>
            <th style="text-align:right">GAP</th>
            <th style="text-align:center">CHG</th>
            <th>AVG / RACE</th>
            <th>BEST RACE</th>
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

<div class="footer">Global F1 Battle __YEAR__ · Data auto-generated from league CSV · Built with Plotly.js</div>

<script>
// ── Chart data injected from Python ─────────────────────
const DATA = __JSON_DATA__;

// ── Per-player colours (keyed by player name) ──────────
const PCOLORS = __PLAYER_COLORS__;

// ── Tab switching (lazy-render rank/bar so legend sizes correctly) ──
const chartInited = { cum: true, rank: false, bar: false };
function showChart(id) {
  ['cum','rank','bar'].forEach(k => {
    document.getElementById('chart-' + k).classList.toggle('chart-hidden', k !== id);
  });
  document.querySelectorAll('.tab-btn').forEach((btn, i) => {
    btn.classList.toggle('active', ['cum','rank','bar'][i] === id);
  });
  if (!chartInited[id]) {
    chartInited[id] = true;
    if (id === 'rank') initRankChart();
    if (id === 'bar')  initBarChart();
  }
}

// ── Shared layout defaults ───────────────────────────────
const BASE_LAYOUT = {
  paper_bgcolor: '#111827',
  plot_bgcolor:  '#0d1525',
  font: { family: "'Courier New', Courier, monospace", color: '#e8eaf0', size: 11 },
  margin: { l: 60, r: 20, t: 110, b: 150 },
  legend: {
    orientation: 'h', x: 0, y: 1.02,
    xanchor: 'left', yanchor: 'bottom',
    font: { size: 10, color: '#e8eaf0' },
    bgcolor: 'rgba(0,0,0,0)',
    itemclick: 'toggle',
    itemdoubleclick: 'toggleothers'
  },
  xaxis: {
    gridcolor: '#1a2640', linecolor: '#1e2d45', tickcolor: '#1e2d45',
    tickfont: { size: 10, color: '#7a8ba0' },
    tickangle: -45
  },
  yaxis: {
    gridcolor: '#1a2640', linecolor: '#1e2d45', tickcolor: '#1e2d45',
    tickfont: { size: 10, color: '#7a8ba0' },
    zeroline: false
  },
  hovermode: 'x unified',
  hoverlabel: {
    bgcolor: '#0d1525', bordercolor: '#e8002d',
    font: { family: "'Courier New', monospace", size: 11, color: '#e8eaf0' }
  }
};

// ── Build Cumulative Points chart (rendered immediately, tab is visible) ──
(function() {
  const traces = DATA.players.map((p) => ({
    name: p,
    x: DATA.race_labels,
    y: DATA.cumulative[p],
    mode: 'lines+markers',
    line: { color: PCOLORS[p], width: 2 },
    marker: { color: PCOLORS[p], size: 5 },
    hovertemplate: '%{y} pts<extra>' + p + '</extra>'
  }));

  const layout = Object.assign({}, BASE_LAYOUT, {
    yaxis: Object.assign({}, BASE_LAYOUT.yaxis, { title: { text: 'Cumulative Points', font: { size: 11 } } }),
    height: 680
  });
  Plotly.newPlot('chart-cum', traces, layout, { responsive: true, displaylogo: false });
})();

// ── Position Battle (bump) – lazy, rendered on first tab click ──────
function initRankChart() {
  const traces = DATA.players.map((p) => ({
    name: p,
    x: DATA.race_labels,
    y: DATA.rankings[p],
    mode: 'lines+markers',
    line: { color: PCOLORS[p], width: 2 },
    marker: { color: PCOLORS[p], size: 5 },
    hovertemplate: 'P%{y}<extra>' + p + '</extra>'
  }));
  const layout = Object.assign({}, BASE_LAYOUT, {
    yaxis: Object.assign({}, BASE_LAYOUT.yaxis, {
      title: { text: 'Position', font: { size: 11 } },
      autorange: 'reversed',
      tickmode: 'linear', tick0: 1, dtick: 1
    }),
    height: 680
  });
  Plotly.newPlot('chart-rank', traces, layout, { responsive: true, displaylogo: false });
}

// ── Per-Race Points bar – lazy, rendered on first tab click ──────────
function initBarChart() {
  const traces = DATA.players.map((p) => ({
    name: p,
    x: DATA.race_labels,
    y: DATA.per_race[p],
    type: 'bar',
    marker: { color: PCOLORS[p], opacity: 0.85 },
    hovertemplate: '%{y} pts<extra>' + p + '</extra>'
  }));
  const layout = Object.assign({}, BASE_LAYOUT, {
    barmode: 'group',
    yaxis: Object.assign({}, BASE_LAYOUT.yaxis, { title: { text: 'Points Scored', font: { size: 11 } } }),
    height: 680
  });
  Plotly.newPlot('chart-bar', traces, layout, { responsive: true, displaylogo: false });
}


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

    # Most consistent (highest avg)
    from statistics import mean
    completed = [r for r in races if r["tag"] in ("N", "P", "L") and r["label"].strip().upper() != "00 - START"]
    if completed:
        avg_scores = {p: mean([r["scores"][p] for r in completed]) for p in players}
        best_avg_p = max(avg_scores, key=lambda p: avg_scores[p])
        consist_card = f"""
    <div class="stat-card accent2">
      <div class="label">🎯 Most Consistent</div>
      <div class="value">{short_name(best_avg_p)}</div>
      <div class="sub">Avg {avg_scores[best_avg_p]:.0f} pts/race · {len(completed)} races</div>
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
    completed = [r for r in races if r["tag"] in ("N", "P", "L") and r["label"].strip().upper() != "00 - START"]
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

        rows_html += f"""          <tr>
            <td>{badge}</td>
            <td><div class="player-name"><span class="team-dot" style="background:{color}"></span>{p}</div></td>
            <td style="text-align:right" class="pts-col">{entry['prev_total']:,}</td>
            <td style="text-align:right" class="{rpts_class}">{rpts_str}</td>
            <td style="text-align:right" class="pts-col"><b>{entry['new_total']:,}</b></td>
            <td style="text-align:right">{gap_html}</td>
            <td style="text-align:center">{chg_html}</td>
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
    completed_count = sum(1 for r in races if r["tag"] in ("N", "P", "L")
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
