"""Generate a static HTML review page for one run in runs/predictions.db.

Reads every prediction row (ok and failed) for a run, joins in ground-truth
country from the split CSVs (see eval/score.py), computes distance_km for
successful predictions via eval.scorer.haversine_km, and writes a single
self-contained HTML file -- one card per prediction, sortable by distance,
filterable by region and by ok/failed status. No server: open the file
directly in a browser.

Usage:
    uv run python -m eval.report                     # most recent run
    uv run python -m eval.report --run <run_id>
    uv run python -m eval.report --run <run_id> --out path/to/file.html
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from agent.store import connect
from eval.score import latest_run_id, load_run_meta, load_true_countries_for_run
from eval.scorer import haversine_km

RUNS_DIR = Path(__file__).parent.parent / "runs"

ROW_COLUMNS = [
    "image_id",
    "split",
    "continent",
    "status",
    "true_lat",
    "true_lon",
    "pred_lat",
    "pred_lon",
    "pred_country",
    "confidence",
    "evidence",
    "reasoning",
    "raw_response",
    "error",
]


def load_all_rows(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    query = f"SELECT {', '.join(ROW_COLUMNS)} FROM predictions WHERE run_id = ? ORDER BY id"
    return [dict(zip(ROW_COLUMNS, row)) for row in conn.execute(query, (run_id,)).fetchall()]


def build_cards(rows: list[dict], true_countries: dict[str, str]) -> list[dict]:
    cards = []
    for row in rows:
        true_country = true_countries.get(row["image_id"]) or None
        pred_country = row["pred_country"]

        distance_km = None
        country_correct = None
        if row["status"] == "ok":
            distance_km = haversine_km(row["pred_lat"], row["pred_lon"], row["true_lat"], row["true_lon"])
            if pred_country and true_country:
                country_correct = pred_country.upper() == true_country.upper()

        evidence = None
        if row["evidence"]:
            try:
                evidence = json.loads(row["evidence"])
            except (json.JSONDecodeError, TypeError):
                evidence = None

        cards.append(
            {
                "image_id": row["image_id"],
                "split": row["split"],
                "region": row["continent"],
                "status": row["status"],
                "true_lat": row["true_lat"],
                "true_lon": row["true_lon"],
                "pred_lat": row["pred_lat"],
                "pred_lon": row["pred_lon"],
                "distance_km": distance_km,
                "true_country": true_country,
                "pred_country": pred_country,
                "country_correct": country_correct,
                "confidence": row["confidence"],
                "evidence": evidence,
                "reasoning": row["reasoning"],
                "raw_response": row["raw_response"],
                "error": row["error"],
                "mapillary_url": f"https://www.mapillary.com/app/?pKey={row['image_id']}&focus=photo",
            }
        )
    return cards


PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>bollard run {run_id}</title>
<style>
  :root {{
    --bg: #f5f6f8; --panel: #ffffff; --border: #dfe3e8; --text: #1c2126; --muted: #667085;
    --ok: #1a7f4e; --ok-bg: #e6f6ee; --fail: #b3261e; --fail-bg: #fdecea;
    --strong: #2f5fd6; --weak: #8a93a6; --shadow: 0 1px 2px rgba(16,24,40,.06);
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #14171c; --panel: #1c2027; --border: #2c313a; --text: #e6e9ee; --muted: #9aa4b2;
      --ok: #3ecf8e; --ok-bg: #12312a; --fail: #ff6b64; --fail-bg: #3a1a1a;
      --strong: #7ba1ff; --weak: #6b7280; --shadow: 0 1px 2px rgba(0,0,0,.4);
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--text);
    font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }}
  header {{
    position: sticky; top: 0; z-index: 10; background: var(--panel);
    border-bottom: 1px solid var(--border); padding: 14px 20px;
  }}
  h1 {{ margin: 0 0 4px; font-size: 17px; }}
  .meta {{ color: var(--muted); font-size: 12.5px; }}
  .toolbar {{
    display: flex; flex-wrap: wrap; gap: 18px; align-items: center;
    margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border);
  }}
  .toolbar label {{ font-size: 12px; color: var(--muted); margin-right: 6px; }}
  .toolbar select {{
    background: var(--bg); color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 5px 8px; font-size: 13px;
  }}
  .chip-group {{ display: flex; flex-wrap: wrap; gap: 6px; }}
  .chip {{
    border: 1px solid var(--border); border-radius: 999px; padding: 4px 11px;
    font-size: 12.5px; cursor: pointer; user-select: none; background: var(--panel); color: var(--text);
  }}
  .chip[data-active="true"] {{ background: var(--strong); border-color: var(--strong); color: #fff; }}
  #count {{ margin-left: auto; color: var(--muted); font-size: 12.5px; }}
  main {{ padding: 18px 20px 60px; }}
  #grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px;
  }}
  .card {{
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px; box-shadow: var(--shadow); display: flex; flex-direction: column; gap: 8px;
  }}
  .card-top {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }}
  .card-id {{ font-weight: 600; font-size: 13px; }}
  .card-id a {{ color: inherit; text-decoration: none; }}
  .card-id a:hover {{ text-decoration: underline; }}
  .badge {{
    font-size: 11px; font-weight: 600; border-radius: 999px; padding: 2px 8px; white-space: nowrap;
  }}
  .badge.ok {{ background: var(--ok-bg); color: var(--ok); }}
  .badge.failed {{ background: var(--fail-bg); color: var(--fail); }}
  .badge.region {{ background: var(--bg); color: var(--muted); border: 1px solid var(--border); }}
  .distance {{ font-size: 22px; font-weight: 700; }}
  .distance.near {{ color: var(--ok); }}
  .distance.mid {{ color: #b98900; }}
  .distance.far {{ color: var(--fail); }}
  .row {{ display: flex; justify-content: space-between; font-size: 12.5px; color: var(--muted); }}
  .row b {{ color: var(--text); font-weight: 600; }}
  .country-line {{ font-size: 13px; }}
  .country-line .correct {{ color: var(--ok); font-weight: 600; }}
  .country-line .wrong {{ color: var(--fail); font-weight: 600; }}
  details {{ font-size: 12.5px; }}
  summary {{ cursor: pointer; color: var(--muted); }}
  .evidence-item {{ margin: 6px 0; padding-left: 8px; border-left: 2px solid var(--border); }}
  .evidence-item .cue {{ font-weight: 600; }}
  .evidence-item .weight {{ font-size: 11px; margin-left: 6px; }}
  .weight.strong {{ color: var(--strong); }}
  .weight.weak {{ color: var(--weak); }}
  .reasoning {{ font-size: 12.5px; color: var(--muted); font-style: italic; }}
  .error-box {{
    background: var(--fail-bg); color: var(--fail); border-radius: 6px; padding: 8px;
    font-size: 12px; white-space: pre-wrap; word-break: break-word;
  }}
  .raw {{
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px;
    white-space: pre-wrap; word-break: break-word; color: var(--muted);
    max-height: 160px; overflow-y: auto; margin-top: 4px;
  }}
  #empty {{ display: none; text-align: center; color: var(--muted); padding: 60px 0; }}
</style>
</head>
<body>
<header>
  <h1>bollard &mdash; run {run_id}</h1>
  <div class="meta">
    split={split} &middot; model={model} &middot; prompt_version={prompt_version} &middot;
    {n_total} total &middot; {n_ok} ok &middot; {n_failed} failed
  </div>
  <div class="toolbar">
    <div>
      <label>Sort</label>
      <select id="sortSelect">
        <option value="dist_asc">Distance: near &rarr; far</option>
        <option value="dist_desc">Distance: far &rarr; near</option>
        <option value="conf_desc">Confidence: high &rarr; low</option>
        <option value="conf_asc">Confidence: low &rarr; high</option>
        <option value="id_asc">Image ID</option>
      </select>
    </div>
    <div>
      <label>Status</label>
      <span class="chip-group" id="statusChips"></span>
    </div>
    <div>
      <label>Region</label>
      <span class="chip-group" id="regionChips"></span>
    </div>
    <span id="count"></span>
  </div>
</header>
<main>
  <div id="grid"></div>
  <div id="empty">No predictions match the current filters.</div>
</main>
<script>
const DATA = {data_json};

const state = {{ status: "all", regions: new Set(DATA.map(d => d.region)), sort: "dist_asc" }};

function esc(s) {{
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}})[c]);
}}

function distanceClass(km) {{
  if (km === null) return "";
  if (km <= 25) return "near";
  if (km <= 200) return "mid";
  return "far";
}}

function cardHtml(d) {{
  const statusBadge = d.status === "ok"
    ? '<span class="badge ok">ok</span>'
    : '<span class="badge failed">failed</span>';

  let body = "";
  if (d.status === "ok") {{
    const dist = d.distance_km === null ? "&mdash;" : d.distance_km.toFixed(2) + " km";
    body += `<div class="distance ${{distanceClass(d.distance_km)}}">${{dist}}</div>`;
    body += `<div class="row"><span>true</span><b>${{d.true_lat.toFixed(4)}}, ${{d.true_lon.toFixed(4)}}</b></div>`;
    body += `<div class="row"><span>pred</span><b>${{d.pred_lat.toFixed(4)}}, ${{d.pred_lon.toFixed(4)}}</b></div>`;

    if (d.true_country || d.pred_country) {{
      const cls = d.country_correct === true ? "correct" : d.country_correct === false ? "wrong" : "";
      body += `<div class="country-line">country: pred <b>${{esc(d.pred_country) || "&mdash;"}}</b> vs true <b>${{esc(d.true_country) || "&mdash;"}}</b> <span class="${{cls}}">${{d.country_correct === true ? "\\u2713" : d.country_correct === false ? "\\u2717" : ""}}</span></div>`;
    }}

    if (d.confidence !== null && d.confidence !== undefined) {{
      body += `<div class="row"><span>confidence</span><b>${{d.confidence}}</b></div>`;
    }}

    if (d.reasoning) {{
      body += `<div class="reasoning">${{esc(d.reasoning)}}</div>`;
    }}

    if (Array.isArray(d.evidence) && d.evidence.length) {{
      const items = d.evidence.map(e => `
        <div class="evidence-item">
          <span class="cue">${{esc(e.cue)}}</span>
          <span class="weight ${{esc(e.weight)}}">${{esc(e.weight)}}</span>
          <div>${{esc(e.observation)}}</div>
        </div>`).join("");
      body += `<details><summary>evidence (${{d.evidence.length}})</summary>${{items}}</details>`;
    }}
  }} else {{
    body += `<div class="error-box">${{esc(d.error)}}</div>`;
    if (d.raw_response) {{
      body += `<details><summary>raw response</summary><div class="raw">${{esc(d.raw_response)}}</div></details>`;
    }}
  }}

  return `
    <div class="card" data-status="${{d.status}}" data-region="${{esc(d.region)}}">
      <div class="card-top">
        <div class="card-id"><a href="${{esc(d.mapillary_url)}}" target="_blank" rel="noopener">${{esc(d.image_id)}} ↗</a></div>
        <div style="display:flex; gap:6px;">
          <span class="badge region">${{esc(d.region)}}</span>
          ${{statusBadge}}
        </div>
      </div>
      ${{body}}
    </div>`;
}}

function sortData(list) {{
  const withRank = (val, fallback) => (val === null || val === undefined ? fallback : val);
  const sorted = list.slice();
  switch (state.sort) {{
    case "dist_asc":
      sorted.sort((a, b) => withRank(a.distance_km, Infinity) - withRank(b.distance_km, Infinity));
      break;
    case "dist_desc":
      sorted.sort((a, b) => withRank(b.distance_km, -Infinity) - withRank(a.distance_km, -Infinity));
      break;
    case "conf_desc":
      sorted.sort((a, b) => withRank(b.confidence, -Infinity) - withRank(a.confidence, -Infinity));
      break;
    case "conf_asc":
      sorted.sort((a, b) => withRank(a.confidence, Infinity) - withRank(b.confidence, Infinity));
      break;
    case "id_asc":
      sorted.sort((a, b) => a.image_id.localeCompare(b.image_id));
      break;
  }}
  return sorted;
}}

function render() {{
  let list = DATA.filter(d => (state.status === "all" || d.status === state.status) && state.regions.has(d.region));
  list = sortData(list);

  const grid = document.getElementById("grid");
  const empty = document.getElementById("empty");
  document.getElementById("count").textContent = `Showing ${{list.length}} of ${{DATA.length}}`;

  if (list.length === 0) {{
    grid.innerHTML = "";
    empty.style.display = "block";
  }} else {{
    empty.style.display = "none";
    grid.innerHTML = list.map(cardHtml).join("");
  }}
}}

function buildChips(containerId, values, isActive, onToggle) {{
  const container = document.getElementById(containerId);
  container.innerHTML = "";
  values.forEach(v => {{
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = v.label;
    chip.dataset.active = String(isActive(v.value));
    chip.addEventListener("click", () => {{
      onToggle(v.value);
      chip.dataset.active = String(isActive(v.value));
      render();
    }});
    container.appendChild(chip);
  }});
}}

const regions = Array.from(new Set(DATA.map(d => d.region))).sort();

buildChips("statusChips",
  [{{label: "All", value: "all"}}, {{label: "OK", value: "ok"}}, {{label: "Failed", value: "failed"}}],
  v => state.status === v,
  v => {{ state.status = v; }});

buildChips("regionChips",
  regions.map(r => ({{label: r, value: r}})),
  v => state.regions.has(v),
  v => {{ state.regions.has(v) ? state.regions.delete(v) : state.regions.add(v); }});

document.getElementById("sortSelect").addEventListener("change", e => {{
  state.sort = e.target.value;
  render();
}});

render();
</script>
</body>
</html>
"""


def render_page(run_id: str, meta: dict, cards: list[dict], n_failed: int) -> str:
    data_json = json.dumps(cards).replace("</", "<\\/")
    return PAGE_TEMPLATE.format(
        run_id=run_id,
        split=meta["split"],
        model=meta["model"],
        prompt_version=meta["prompt_version"],
        n_total=meta["n_total"],
        n_ok=meta["n_total"] - n_failed,
        n_failed=n_failed,
        data_json=data_json,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a static HTML review page for a run")
    parser.add_argument("--run", dest="run_id", default=None, help="run id (default: most recent)")
    parser.add_argument("--out", dest="out_path", default=None, help="output HTML path")
    args = parser.parse_args()

    conn = connect()

    run_id = args.run_id or latest_run_id(conn)
    if run_id is None:
        print("no runs found in runs/predictions.db")
        return

    meta = load_run_meta(conn, run_id)
    if meta is None:
        print(f"run {run_id!r} not found in runs/predictions.db")
        return

    rows = load_all_rows(conn, run_id)
    true_countries = load_true_countries_for_run(conn, run_id)
    conn.close()

    cards = build_cards(rows, true_countries)
    n_failed = sum(1 for c in cards if c["status"] == "failed")

    out_path = Path(args.out_path) if args.out_path else RUNS_DIR / f"report_{run_id}.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_page(run_id, meta, cards, n_failed))

    print(f"wrote {len(cards)} cards ({n_failed} failed) -> {out_path}")


if __name__ == "__main__":
    main()
