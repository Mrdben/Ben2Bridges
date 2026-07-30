"""HTTP API for the Ben2Bridges recommendation engine."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from algorithm.data_pipeline import DataValidationError
from algorithm.recommendation import generate_recommendation


PROJECT_ROOT = Path(__file__).resolve().parent


def configured_path(variable: str, default: str) -> Path:
    """Resolve an environment-configured project path."""

    path = Path(os.getenv(variable, default))
    return path if path.is_absolute() else PROJECT_ROOT / path


NBI_PATH = configured_path("BEN2_NBI", "website/Data/PA 2025.csv")
PREDICTIONS_PATH = configured_path(
    "BEN2_PREDICTIONS",
    "algorithm/data/mock_model_predictions.csv",
)
COUNTIES_PATH = configured_path("BEN2_COUNTIES", "algorithm/data/pa_counties.csv")

default_origins = {
    "https://ben2bridges-pa.netlify.app",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
}
configured_origins = {
    origin.strip()
    for origin in os.getenv("BEN2_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
}
allowed_origins = sorted(default_origins | configured_origins)

app = FastAPI(
    title="Ben2Bridges Recommendation API",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


class RecommendRequest(BaseModel):
    budget: float = Field(gt=0)
    strategy: Literal["balanced", "safety", "traffic", "equity"] = "balanced"
    county_fips: str | None = None
    district: int | None = None


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "Ben2Bridges Recommendation API",
        "status": "ok",
        "documentation": "/docs",
    }


@app.get("/api/health")
def health() -> dict[str, object]:
    files = {
        "nbi": NBI_PATH.is_file(),
        "predictions": PREDICTIONS_PATH.is_file(),
        "counties": COUNTIES_PATH.is_file(),
    }
    return {
        "status": "ready" if all(files.values()) else "missing_data",
        "files": files,
        "prediction_file": PREDICTIONS_PATH.name,
    }


@app.post("/api/recommend")
def recommend(request: RecommendRequest) -> dict[str, object]:
    if request.county_fips is not None and request.district is not None:
        raise HTTPException(
            status_code=400,
            detail="county_fips and district are mutually exclusive",
        )

    try:
        return generate_recommendation(
            NBI_PATH,
            PREDICTIONS_PATH,
            COUNTIES_PATH,
            budget=request.budget,
            strategy=request.strategy,
            county_fips=request.county_fips,
            penndot_district=request.district,
        )
    except (DataValidationError, OSError, pd.errors.ParserError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
