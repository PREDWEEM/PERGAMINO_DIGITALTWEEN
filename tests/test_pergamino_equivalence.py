"""Equivalencia del motor del gemelo con el monolito de LOLIUM-PERGA2026.

Las salidas de ``tests/golden/monolith_golden.csv.gz`` fueron generadas
ejecutando ``simular_emergencia_local`` del commit c55e346 de
``PREDWEEM/LOLIUM-PERGA2026`` (ver ``tests/golden/monolith_golden_cases.json``)
sobre la meteorología SIGA 2024 y la serie operativa 2026.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from predweem_twin.core import (
    ModelParameters,
    PracticalANNModel,
    apply_emergence_lag,
    run_predweem,
)

ROOT = Path(__file__).parents[1]
GOLDEN = ROOT / "tests/golden"
SPEC = json.loads((GOLDEN / "monolith_golden_cases.json").read_text(encoding="utf-8"))
GOLD = pd.read_csv(GOLDEN / "monolith_golden.csv.gz")
MODEL = PracticalANNModel.from_directory(ROOT / "models")
OVERRIDES = {
    "lag_emergencia_dias": "lag_emergencia_dias",
    "umbral_termoinhibicion": "umbral_termoinhibicion",
    "cobertura_pct": "cobertura_pct",
    "w_max": "w_max",
    "umbral_choque_hidrico": "umbral_choque_hidrico",
    "techo_choque": "techo_choque",
    "exponente_kr": "exponente_kr",
}


def _weather(key):
    return pd.read_csv(GOLDEN / f"{key}_weather.csv")


@pytest.mark.parametrize("climate", ["w2024", "w2026"])
@pytest.mark.parametrize("case", list(SPEC["cases"]))
def test_twin_engine_matches_monolith(climate, case):
    kwargs = {**SPEC["base"], **SPEC["cases"][case]}
    params = ModelParameters(**{OVERRIDES[k]: v for k, v in kwargs.items()})
    result = run_predweem(_weather(climate), MODEL, params)
    expected = GOLD[(GOLD["clima"] == climate) & (GOLD["caso"] == case)].reset_index(drop=True)
    assert len(result) == len(expected)
    assert result["Fecha"].dt.strftime("%Y-%m-%d").tolist() == expected["Fecha"].tolist()
    for column in ("EMERREL", "EMERAC", "EMERAC_NORMALIZADA", "EMERREL_RAW_ANN", "W_superficial"):
        np.testing.assert_allclose(result[column].to_numpy(float), expected[column].to_numpy(float),
                                   rtol=0, atol=1e-9, err_msg=column)
    for column in ("Termoinhibida", "Choque_Hidrico", "Primer_Pico_Habilitado"):
        assert result[column].astype(bool).tolist() == expected[column].astype(bool).tolist(), column


def test_default_parameters_are_pergamino():
    params = ModelParameters()
    assert (params.cobertura_pct, params.w_max, params.umbral_termoinhibicion) == (80.0, 18.81, 26.0)
    assert (params.umbral_choque_hidrico, params.techo_choque, params.latencia_jd) == (45.0, 0.75, 25)
    assert params.lag_emergencia_dias == 15
    assert params.decay_enabled is False
    assert (params.latitud, params.longitud) == (-33.9443, -60.5745)


def test_lag_shifts_rate_and_keeps_audit_column():
    frame = pd.DataFrame({"EMERREL": [0.0, 0.5, 0.25, 0.0, 0.0, 0.0]})
    shifted = apply_emergence_lag(frame, 2)
    assert shifted["EMERREL"].tolist() == [0.0, 0.0, 0.0, 0.5, 0.25, 0.0]
    assert shifted["EMERREL_SIN_LAG"].tolist() == frame["EMERREL"].tolist()
    assert apply_emergence_lag(frame, -1)["EMERREL"].tolist() == [0.5, 0.25, 0.0, 0.0, 0.0, 0.0]
    assert apply_emergence_lag(frame, 0)["EMERREL"].tolist() == frame["EMERREL"].tolist()
    assert apply_emergence_lag(frame, 99)["EMERREL"].sum() == 0.0


def test_lag_moves_first_emergence_exactly_fifteen_days():
    weather = _weather("w2026")
    base = run_predweem(weather, MODEL, ModelParameters())
    free = run_predweem(weather, MODEL, ModelParameters.sin_lag())
    first_base = base.loc[base["EMERREL"] > 0, "Fecha"].iloc[0]
    first_free = free.loc[free["EMERREL"] > 0, "Fecha"].iloc[0]
    assert (first_base - first_free).days >= 15
