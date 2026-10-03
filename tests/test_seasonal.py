from pathlib import Path

import numpy as np
import pandas as pd

from predweem_twin.core import ModelParameters, PracticalANNModel, run_predweem
from predweem_twin.seasonal import load_local_seasonal_reference


ROOT = Path(__file__).parents[1]


def test_partial_run_uses_historical_season_instead_of_ending_at_one():
    weather = pd.read_csv(ROOT / "meteo_daily.csv")
    weather["Fecha"] = pd.to_datetime(weather["Fecha"]) + pd.DateOffset(years=1)  # fixture 2027
    cutoff = pd.Timestamp("2027-05-14")
    weather = weather[weather["Fecha"] <= cutoff + pd.Timedelta(days=7)]
    model = PracticalANNModel.from_directory(ROOT / "models")
    reference = load_local_seasonal_reference(ROOT, as_of=cutoff)

    result = run_predweem(
        weather, model, ModelParameters(cobertura_pct=60.0),
        normalization_as_of=cutoff, seasonal_reference=reference,
    )
    at_cutoff = result[result["Fecha"] <= cutoff].iloc[-1]

    assert np.isclose(at_cutoff["EMERAC_NORMALIZADA"], at_cutoff["Progreso_Estacional_Referencia"])
    assert result.iloc[-1]["EMERAC_NORMALIZADA"] < 1.0
    assert result["Normalizacion_Modo"].eq("referencia estacional histórica").all()


def test_reference_uses_only_pergamino_2026_counts():
    reference = load_local_seasonal_reference(ROOT, as_of="2027-01-01")
    assert reference["N_Campanas"].eq(1).all()
    assert reference["Campanas"].eq("pergamino_2026_counts.csv").all()
    assert reference["Campanas_Anos"].eq("2026").all()


def test_no_reference_is_returned_before_the_last_2026_count():
    assert load_local_seasonal_reference(ROOT, as_of="2026-07-02") is None
    assert load_local_seasonal_reference(ROOT, as_of="2026-07-03") is not None


def test_without_reference_the_percentage_is_not_estimable_not_zero():
    weather = pd.read_csv(ROOT / "meteo_daily.csv")
    weather["Fecha"] = pd.to_datetime(weather["Fecha"])
    cutoff = pd.Timestamp("2026-06-01")
    weather = weather[weather["Fecha"] <= cutoff + pd.Timedelta(days=7)]
    model = PracticalANNModel.from_directory(ROOT / "models")
    result = run_predweem(weather, model, ModelParameters(), normalization_as_of=cutoff,
                          seasonal_reference=None)
    assert result["EMERAC_NORMALIZADA"].isna().all()
    assert result["Normalizacion_Disponible"].eq(False).all()
    assert result["Normalizacion_Modo"].eq("porcentaje aún no estimable").all()
    assert result["EMERAC"].iloc[-1] > 0  # el acumulado absoluto sigue disponible
