# Bollard

An agent that geolocates street-level photos and defends its reasoning, scored against ground truth.

## What this is, and what it is not

This is an **evaluation project** that happens to have an agent in it. The deliverable is a
results table, not a demo. Every feature request should be answerable with "and how will we
measure whether that helped?"

Not a wrapper. Not a chatbot. Not a SaaS.

## Core loop

1. Take an image with known lat/lon.
2. Agent reasons over visual evidence and outputs a structured guess.
3. Score with haversine distance in km against ground truth.
4. Log the full trace so failures can be read afterward.

## Structured output contract

Every prediction returns this shape. Do not change it without updating the scorer and
migrating existing runs.

```json
{
  "lat": 0.0,
  "lon": 0.0,
  "country": "ISO-3166 alpha-2",
  "confidence": 0.0,
  "evidence": [
    {"cue": "bollard", "observation": "...", "weight": "strong|weak"}
  ],
  "reasoning": "short prose"
}
```

`evidence` is the point of the project. A correct guess with garbage evidence is a failure
case and should be flagged, not celebrated.

## Data

- Source images from **Mapillary** (CC-BY, has an API, gives lat/lon in metadata).
- Do **not** scrape Google Street View. It is against their terms and it will sink the repo.
- Target ~300 images for v1. Stratify by continent, not by country, or the set will be 60% US.
- Hold out a test split from day one and never look at it during iteration.

## Metrics

Report all of these. A single average is misleading here because the error distribution has
a long tail.

- Median distance error (km)
- % within 25km / 200km / 1000km
- Country-level accuracy
- Per-region breakdown
- Evidence quality: manual spot-check of 30 traces, rated useful / plausible / hallucinated

## Experiments worth running

Each one is a row in the results table.

- One-shot guess vs. scratchpad reasoning
- With and without a retrieval index of country-specific visual cues
- Ablating individual cue types from the prompt (drop road markings, drop signage, etc.)
- Model comparison at fixed prompt
- Human baseline: play the same held-out rounds yourself and log your scores

## Stack

- Python 3.11+, `uv` for deps
- Anthropic API for the vision calls
- SQLite for runs and traces. One row per prediction, keep the raw response blob.
- Plain `matplotlib` for charts. No dashboard until the numbers are real.
- No framework. The agent loop here is small enough to own.

## Layout

```
bollard/
  data/          # image manifest + metadata, images gitignored
  prompts/       # versioned prompt files, never inline in code
  agent/         # prediction loop
  eval/          # scorer, metrics, plots
  runs/          # sqlite db, gitignored
notebooks/       # scratch only, nothing load-bearing
```

## Conventions

- Prompts live in `prompts/` as versioned files. Every run records which prompt version
  produced it. Untracked prompt edits make every prior number meaningless.
- Never overwrite a run. Append a new one.
- Every experiment gets a seed and a config dict saved alongside its results.
- Type hints on anything that crosses a module boundary. Skip them in notebooks.
- No retries that silently swallow errors. A failed prediction is data.

## Commands

```bash
uv run bollard predict --split dev --n 50      # run predictions
uv run bollard score --run <id>                # metrics for a run
uv run bollard compare --runs <id> <id>        # side-by-side table
uv run pytest                                  # scorer tests, must pass before any commit
```

## Working agreements for Claude

- Ask before adding a dependency.
- When adding a capability, write the metric that proves it worked in the same change.
- Do not refactor the scorer without being asked. Prior results depend on it.
- Small commits. One experiment per commit.
- If a change makes numbers better, say by how much and on which split. "Improved accuracy"
  with no number is not a report.