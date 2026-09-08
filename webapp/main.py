"""Minimal FastAPI app wrapping agent/predict.py's model-calling logic.

Two ways to get a round going: upload your own photo, or pull a random image
from eval/test.csv (ground truth known there, so a real score is possible).
Each endpoint returns the agent's full prediction (lat/lon/country/confidence/
evidence/reasoning) plus ground truth (if known) in one response -- the
frontend is what withholds it from view until the user hits "Reveal" after
typing their own guess. There is no server-side round state; nothing here is
written to runs/predictions.db, which is reserved for the batch eval
pipeline. A file-backed daily counter (webapp/rate_limit.py) blocks new model
calls past 50/day.

eval/test.csv is read directly from this endpoint, which lives outside eval/
-- an explicit exception to the "nothing outside eval/ reads test.csv" rule,
made because this feature (human baseline against held-out rounds) was asked
for by name.

Run:
    uv run uvicorn webapp.main:app --reload
Then open http://127.0.0.1:8000/
"""

from __future__ import annotations

import base64
import csv
import os
import random
from pathlib import Path

import anthropic
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.image_utils import crop_bottom, strip_exif
from agent.predict import (
    MODEL,
    PROMPT_PATH,
    PredictionParseError,
    call_model,
    fetch_and_encode_image,
    load_dotenv_value,
)
from eval.scorer import haversine_km
from webapp.rate_limit import DailyLimitExceeded, check_and_increment, current_count

EVAL_DIR = Path(__file__).parent.parent / "eval"
TEST_CSV_PATH = EVAL_DIR / "test.csv"
STATIC_DIR = Path(__file__).parent / "static"

SUPPORTED_UPLOAD_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

app = FastAPI(title="bollard playground")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def get_anthropic_client() -> anthropic.Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY") or load_dotenv_value("ANTHROPIC_API_KEY")
    if not key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not set on the server")
    return anthropic.Anthropic(api_key=key)


def get_mapillary_token() -> str:
    token = os.environ.get("MAPILLARY_TOKEN") or load_dotenv_value("MAPILLARY_TOKEN")
    if not token:
        raise HTTPException(status_code=500, detail="MAPILLARY_TOKEN not set on the server")
    return token


def agent_result_from(fields_or_error) -> dict:
    if isinstance(fields_or_error, PredictionParseError):
        return {"status": "failed", "error": str(fields_or_error), "raw_response": fields_or_error.raw_response}
    fields = fields_or_error
    return {
        "status": "ok",
        "lat": fields["pred_lat"],
        "lon": fields["pred_lon"],
        "country": fields["pred_country"],
        "confidence": fields["confidence"],
        "evidence": fields["evidence"],
        "reasoning": fields["reasoning"],
    }


def run_model_or_raise(client: anthropic.Anthropic, image_b64: str, media_type: str) -> dict:
    try:
        check_and_increment()
    except DailyLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    prompt_text = PROMPT_PATH.read_text()
    try:
        fields = call_model(client, prompt_text, image_b64, media_type=media_type)
        return agent_result_from(fields)
    except PredictionParseError as exc:
        return agent_result_from(exc)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
def status() -> dict:
    return {"daily_limit": 50, "used_today": current_count()}


class ScoreRequest(BaseModel):
    user_lat: float
    user_lon: float
    true_lat: float | None = None
    true_lon: float | None = None
    agent_lat: float | None = None
    agent_lon: float | None = None


@app.post("/api/score")
def score(req: ScoreRequest) -> dict:
    """Score a guess against truth and/or the agent using the project's own scorer."""
    result = {"user_vs_truth_km": None, "agent_vs_truth_km": None, "user_vs_agent_km": None}
    has_truth = req.true_lat is not None and req.true_lon is not None
    has_agent = req.agent_lat is not None and req.agent_lon is not None

    if has_truth:
        result["user_vs_truth_km"] = haversine_km(req.user_lat, req.user_lon, req.true_lat, req.true_lon)
        if has_agent:
            result["agent_vs_truth_km"] = haversine_km(req.agent_lat, req.agent_lon, req.true_lat, req.true_lon)
    if has_agent:
        result["user_vs_agent_km"] = haversine_km(req.user_lat, req.user_lon, req.agent_lat, req.agent_lon)

    return result


@app.post("/api/predict/random")
def predict_random() -> dict:
    with TEST_CSV_PATH.open(newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise HTTPException(status_code=500, detail="eval/test.csv is empty")
    row = random.choice(rows)

    mapillary_token = get_mapillary_token()
    try:
        image_b64 = fetch_and_encode_image(row["image_id"], mapillary_token)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"failed to fetch image: {exc}") from exc

    client = get_anthropic_client()
    agent = run_model_or_raise(client, image_b64, "image/jpeg")

    return {
        "image_data_url": f"data:image/jpeg;base64,{image_b64}",
        "image_id": row["image_id"],
        "region": row.get("continent"),
        "true_lat": float(row["lat"]),
        "true_lon": float(row["lon"]),
        "true_country": row.get("country") or None,
        "agent": agent,
    }


@app.post("/api/predict/upload")
async def predict_upload(file: UploadFile = File(...)) -> dict:
    content_type = file.content_type or "image/jpeg"
    if content_type not in SUPPORTED_UPLOAD_TYPES:
        raise HTTPException(status_code=400, detail=f"unsupported image type: {content_type}")

    raw_bytes = await file.read()
    if content_type == "image/jpeg":
        try:
            raw_bytes = strip_exif(raw_bytes)
        except ValueError:
            pass  # not a well-formed JPEG we can parse -- send as-is rather than fail the round
    try:
        raw_bytes = crop_bottom(raw_bytes)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"could not process uploaded image: {exc}") from exc
    image_b64 = base64.standard_b64encode(raw_bytes).decode("utf-8")

    client = get_anthropic_client()
    agent = run_model_or_raise(client, image_b64, content_type)

    return {
        "image_data_url": f"data:{content_type};base64,{image_b64}",
        "image_id": None,
        "region": None,
        "true_lat": None,
        "true_lon": None,
        "true_country": None,
        "agent": agent,
    }
