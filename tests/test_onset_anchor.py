"""Anclaje del inicio de la emergencia a los conteos de campo."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from predweem_twin.core import ModelParameters, PracticalANNModel, run_predweem
from predweem_twin.onset import onset_alert, onset_window_from_observations

ROOT = Path(__file__).resolve().parents[1]
MODEL = PracticalANNModel.from_directory(ROOT / "models")


def counts_2026():
    frame = pd.read_csv(ROOT / "data/calibration/pergamino_2026_counts.csv", parse_dates=["FECHA"])
    return frame.rename(columns={"FECHA": "Fecha", "PLM2": "Flujo_observado_PLM2"})


@pytest.fixture(scope="module")
def weather_2026():
    weather = pd.read_csv(ROOT / "meteo_daily.csv", parse_dates=["Fecha"])
    return weather.loc[weather.Fecha.le("2026-07-03"), ["Fecha", "TMAX", "TMIN", "Prec"]]


@pytest.fixture(scope="module")
def weather_2024():
    weather = pd.read_csv(ROOT / "data/reference/pergamino_2024_weather.csv", parse_dates=["Fecha"])
    return weather.loc[weather.Fecha.le("2024-07-31"), ["Fecha", "TMAX", "TMIN", "Prec"]]


def onset(frame):
    return frame.loc[frame["Primer_Pico_Habilitado"], "Fecha"].iloc[0]


def test_window_is_last_zero_to_first_positive_and_ignores_the_future():
    obs = counts_2026()
    window, info = onset_window_from_observations(obs, "2026-04-20")
    assert window == (pd.Timestamp("2026-04-02"), pd.Timestamp("2026-04-13"))
    assert info["last_zero"] == "2026-04-01" and info["first_positive"] == "2026-04-13"
    # Antes del primer conteo positivo sólo hay cota inferior; los posteriores no cuentan.
    assert onset_window_from_observations(obs, "2026-04-12")[0] == (pd.Timestamp("2026-04-02"), None)
    assert onset_window_from_observations(obs.iloc[1:], "2026-04-12")[0] == (None, None)
    assert onset_window_from_observations(obs, "2026-07-03")[0] == window


def test_without_an_explicit_zero_there_is_only_an_upper_bound():
    obs = counts_2026().iloc[1:]
    window, info = onset_window_from_observations(obs, "2026-05-01")
    assert window == (None, pd.Timestamp("2026-04-13")) and info["last_zero"] is None


def test_cumulative_observations_and_other_years_are_handled():
    cumulative = pd.DataFrame({"Fecha": ["2026-04-01", "2026-04-20", "2025-05-01"], "Observado": [0.0, 0.1, 0.5]})
    window, info = onset_window_from_observations(cumulative, "2026-05-01")
    assert window == (pd.Timestamp("2026-04-02"), pd.Timestamp("2026-04-20")) and info["source"] == "Observado"
    assert onset_window_from_observations(pd.DataFrame(), "2026-05-01")[0] == (None, None)


def test_no_window_leaves_the_engine_unchanged(weather_2026):
    base = run_predweem(weather_2026, MODEL, ModelParameters())
    explicit = run_predweem(weather_2026, MODEL, ModelParameters(), onset_window=(None, None))
    for column in ("EMERREL", "EMERAC", "TT_DESDE_PICO", "Primer_Pico_Habilitado"):
        pd.testing.assert_series_equal(base[column], explicit[column])
    assert not explicit["Inicio_Anclado"].any()
    assert explicit["Inicio_Anclaje_Motivo"].eq("sin anclaje").all()


def test_early_model_onset_is_delayed_into_the_observed_window(weather_2026):
    window, _ = onset_window_from_observations(counts_2026(), "2026-07-03")
    base = run_predweem(weather_2026, MODEL, ModelParameters())
    anchored = run_predweem(weather_2026, MODEL, ModelParameters(), onset_window=window)
    assert onset(base) == pd.Timestamp("2026-03-05")
    start = onset(anchored)
    assert window[0] <= start <= window[1]
    dates = anchored["Fecha"]
    assert (anchored.loc[dates < window[0], "EMERREL"] == 0).all()
    assert (anchored.loc[dates < start, "TT_DESDE_PICO"] == 0).all()
    assert anchored.loc[dates == start, "TT_DESDE_PICO"].iloc[0] > 0
    assert anchored["Inicio_Anclado"].all()
    assert pd.Timestamp(anchored["Inicio_Modelado_Sin_Ancla"].iloc[0]) == pd.Timestamp("2026-03-05")
    # Menos tiempo térmico acumulado a una misma fecha, y sin flujo antes de la ventana.
    cut = pd.Timestamp("2026-05-21")
    assert (anchored.set_index("Fecha").TT_DESDE_PICO[cut]
            < base.set_index("Fecha").TT_DESDE_PICO[cut])


def test_onset_inside_the_window_is_not_moved(weather_2026):
    window, _ = onset_window_from_observations(counts_2026(), "2026-07-03")
    params = ModelParameters(umbral_termoinhibicion=20.0)
    base = run_predweem(weather_2026, MODEL, params)
    anchored = run_predweem(weather_2026, MODEL, params, onset_window=window)
    assert window[0] <= onset(base) <= window[1]
    assert onset(anchored) == onset(base)
    pd.testing.assert_series_equal(base["TT_DESDE_PICO"], anchored["TT_DESDE_PICO"])


def test_late_model_onset_is_pulled_back_to_the_first_positive_count(weather_2024):
    window = (None, pd.Timestamp("2024-02-29"))
    base = run_predweem(weather_2024, MODEL, ModelParameters())
    anchored = run_predweem(weather_2024, MODEL, ModelParameters(), onset_window=window)
    assert onset(base) > window[1]
    assert onset(anchored) == window[1]
    assert (anchored.loc[anchored.Fecha < window[1], "TT_DESDE_PICO"] == 0).all()
    # No se inventa flujo: la emergencia modelada no cambia.
    pd.testing.assert_series_equal(base["EMERREL"], anchored["EMERREL"])


def test_invalid_window_is_rejected(weather_2026):
    with pytest.raises(ValueError, match="ventana"):
        run_predweem(weather_2026, MODEL, ModelParameters(),
                     onset_window=(pd.Timestamp("2026-05-01"), pd.Timestamp("2026-04-01")))


def test_alert_reports_the_anchored_window(weather_2026):
    obs = counts_2026()
    window, _ = onset_window_from_observations(obs, "2026-05-01")
    anchored = run_predweem(weather_2026, MODEL, ModelParameters(), onset_window=window)
    alert = onset_alert(anchored, "2026-05-01", observations=obs.loc[obs.Fecha.le("2026-05-01")])
    assert alert["status"] == "observed" and alert["anchored"] is True
    assert alert["onset_window"] == ["2026-04-02", "2026-04-13"]
    assert "Inicio anclado a los conteos: entre el 02/04/2026 y el 13/04/2026" in alert["message"]
    assert "a más tardar el 13/04/2026" in alert["message"]


def test_zero_count_without_positives_only_delays_the_onset(weather_2026):
    window, info = onset_window_from_observations(counts_2026(), "2026-04-10")
    assert window == (pd.Timestamp("2026-04-02"), None) and info["first_positive"] is None
    anchored = run_predweem(weather_2026, MODEL, ModelParameters(), onset_window=window)
    assert onset(anchored) >= window[0]
    assert (anchored.loc[anchored.Fecha < window[0], "EMERREL"] == 0).all()
    alert = onset_alert(anchored, "2026-04-10", observations=counts_2026().iloc[:1])
    assert alert["status"] == "started" and not alert.get("anchored")
    assert alert["model_onset_date"] >= "2026-04-02"
