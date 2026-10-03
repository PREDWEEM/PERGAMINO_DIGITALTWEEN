"""Reproduce las cifras de validación de MODEL_PROVENANCE.md (distancia de variación total)."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from predweem_twin.core import ModelParameters, PracticalANNModel, run_predweem

ROOT = Path(__file__).parents[1]
MODEL = PracticalANNModel.from_directory(ROOT / "models")


def _flow(weather, **kwargs):
    result = run_predweem(weather, MODEL, ModelParameters(**kwargs))
    return result.set_index("Fecha")["EMERREL"]


def tvd_2024(flow):
    """Curva DIMA 2024 por quincenas (referencia relativa digitalizada)."""
    counts = pd.read_csv(ROOT / "data/reference/pergamino_2024_counts.csv", parse_dates=["FECHA"])
    ends = counts.FECHA.iloc[1:].tolist()
    starts = [counts.FECHA.iloc[0] + pd.Timedelta(days=1)] + [e + pd.Timedelta(days=1) for e in ends[:-1]]
    observed = counts.PLM2.iloc[1:].to_numpy(float)
    modelled = np.array([flow.loc[a:b].sum() for a, b in zip(starts, ends)])
    return 0.5 * np.abs(observed / observed.sum() - modelled / modelled.sum()).sum() * 100


def tvd_2026(flow):
    counts = pd.read_csv(ROOT / "data/calibration/pergamino_2026_counts.csv", parse_dates=["FECHA"])
    cumulative = flow.cumsum()
    at = [cumulative.loc[:d].iloc[-1] for d in counts.FECHA]
    modelled = np.diff(at)
    observed = counts.PLM2.iloc[1:].to_numpy(float)
    return 0.5 * np.abs(observed / observed.sum() - modelled / modelled.sum()).sum() * 100


@pytest.fixture(scope="module")
def weather():
    w24 = pd.read_csv(ROOT / "data/reference/pergamino_2024_weather.csv", parse_dates=["Fecha"])
    w24 = w24.loc[w24.Fecha.le("2024-07-31"), ["Fecha", "TMAX", "TMIN", "Prec"]]
    w26 = pd.read_csv(ROOT / "meteo_daily.csv", parse_dates=["Fecha"])
    w26 = w26.loc[w26.Fecha.le("2026-07-03"), ["Fecha", "TMAX", "TMIN", "Prec"]]
    return w24, w26


@pytest.mark.parametrize("kwargs,expected_2024,expected_2026", [
    ({}, 30.5, 47.8),
    ({"lag_emergencia_dias": 0}, 15.3, 58.0),
    ({"umbral_termoinhibicion": 20.0}, 74.4, 58.2),
    ({"umbral_termoinhibicion": 20.0, "lag_emergencia_dias": 0}, 60.1, 64.8),
])
def test_documented_validation_figures(weather, kwargs, expected_2024, expected_2026):
    w24, w26 = weather
    assert tvd_2024(_flow(w24, **kwargs)) == pytest.approx(expected_2024, abs=0.06)
    assert tvd_2026(_flow(w26, **kwargs)) == pytest.approx(expected_2026, abs=0.06)


def test_2026_modelled_start_is_earlier_than_the_observed_one(weather):
    _, w26 = weather
    flow = _flow(w26)
    assert flow[flow > 0].index[0] == pd.Timestamp("2026-03-05")
    cumulative = flow.cumsum() / flow.sum()
    assert cumulative[cumulative >= .50].index[0] == pd.Timestamp("2026-04-11")  # observado: 21/05
