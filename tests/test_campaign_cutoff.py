"""Fuentes SIGA/ECMWF y cierre de la campaña 2026, sin consultas de red."""

from datetime import date, timedelta
import json
from pathlib import Path

import pandas as pd
import pytest

import actualizar_meteo_pergamino as base
import actualizar_meteo_pergamino_robusto as updater
from campaign import CAMPANIA_END
from predweem_twin.weather import operational_weather_window

ROOT = Path(__file__).parents[1]


def weather_series(start, end, kind):
    frame = pd.DataFrame({"Fecha": pd.date_range(start, end).strftime("%Y-%m-%d")})
    frame["TMAX"], frame["TMIN"], frame["TMEDIA"], frame["Prec"] = 20., 10., 15., 1.
    frame["TipoDato"] = kind
    frame["Fuente"] = {
        "Observado": "SIGA_INTA_PERGAMINO",
        "Provisional": "ECMWF_IFS_HISTORICO", "Pronostico": "ECMWF_IFS_ENS_025",
    }[kind]
    frame["CalidadDato"] = {
        "Observado": "Observado_estacion",
        "Provisional": "Provisional_hasta_reemplazo_SIGA", "Pronostico": "Pronostico_P50",
    }[kind]
    if kind == "Pronostico":
        for column in ("TMAX", "TMIN", "TMEDIA", "Prec"):
            frame[column + "_P50"] = frame[column]
            frame[column + "_Media_Ens"] = frame[column]
        frame["N_miembros"] = 51
    return updater.columnas(frame)


def test_campaign_end_is_shared_by_twin_and_updater():
    assert base.CAMPANIA_END == CAMPANIA_END == date(2026, 10, 1)
    assert base.CAMPANIA_START == date(2026, 1, 1)


@pytest.mark.parametrize("cutoff,expected", [("2026-09-28", 3), ("2026-10-01", 0), ("2026-10-10", 0)])
def test_twin_horizon_stops_at_campaign_end(cutoff, expected):
    frame = pd.DataFrame({"Fecha": pd.date_range("2026-09-25", "2026-10-10")})
    window, meta = operational_weather_window(frame, as_of=cutoff)
    assert window.Fecha.max() == pd.Timestamp("2026-10-01")
    assert meta["forecast_days_expected"] == expected
    assert meta["forecast_days_available"] == expected
    assert meta["complete"]


@pytest.mark.parametrize("today", [date(2026, 9, 19), date(2026, 9, 29), date(2026, 10, 1), date(2026, 10, 8)])
def test_update_keeps_observed_provisional_and_forecast_separate(monkeypatch, tmp_path, today):
    last_observed = min(today - timedelta(days=4), base.CAMPANIA_END)
    yesterday = min(today - timedelta(days=1), base.CAMPANIA_END)
    last_forecast = min(today + timedelta(days=6), base.CAMPANIA_END)
    observed = weather_series(base.CAMPANIA_START, last_observed, "Observado")
    provisional = weather_series(last_observed + timedelta(days=1), yesterday, "Provisional")
    forecast = (
        weather_series(today, last_forecast, "Pronostico")
        if today <= base.CAMPANIA_END else weather_series(today, today, "Pronostico").iloc[0:0]
    )
    monkeypatch.setattr(base, "hoy_argentina", lambda: today)

    def get_siga(start, end, archivo_forzado=None):
        assert end == yesterday
        return observed, "test"

    monkeypatch.setattr(base, "obtener_siga_dataframe", get_siga)
    monkeypatch.setattr(updater, "cargar_provisional", lambda start, end: provisional.loc[
        pd.to_datetime(provisional.Fecha).dt.date.between(start, end)
    ])
    monkeypatch.setattr(updater, "cargar_ensamble", lambda: forecast)
    monkeypatch.setattr(base, "ARCHIVO_MAESTRO_DEFAULT", tmp_path / "meteo.csv")
    monkeypatch.setattr(base, "ARCHIVO_SIGA_CACHE", tmp_path / "siga.csv")
    monkeypatch.setattr(base, "ARCHIVO_ESTADO", tmp_path / "state.json")

    result = updater.ejecutar()

    expected_end = base.CAMPANIA_END if today > base.CAMPANIA_END else last_forecast
    assert result.Fecha.max() == expected_end.isoformat()
    assert len(result) == (expected_end - base.CAMPANIA_START).days + 1
    assert result.TipoDato.eq("Observado").sum() == len(observed)
    assert result.TipoDato.eq("Provisional").sum() == len(provisional)
    assert result.TipoDato.eq("Pronostico").sum() == len(forecast)
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["fecha_fin_campania"] == "2026-10-01"
    assert state["huecos_finales"] == []
    assert state["sitio"] == "Pergamino" and state["estacion_siga"] == "A872814"


def test_closed_campaign_does_not_query_ensemble(monkeypatch):
    monkeypatch.setattr(base, "hoy_argentina", lambda: date(2026, 10, 2))

    def unexpected(*args, **kwargs):
        raise AssertionError("No debe consultar ECMWF ENS después del cierre")

    monkeypatch.setattr(base, "consultar_ecmwf_ens", unexpected)
    assert base.cargar_pronostico_ecmwf().empty


def test_missing_siga_days_become_contiguous_ranges():
    observed = weather_series("2026-01-01", "2026-01-10", "Observado")
    observed = observed[~observed.Fecha.isin(["2026-01-03", "2026-01-04", "2026-01-08"])]
    missing = updater.fechas_faltantes(observed, date(2026, 1, 1), date(2026, 1, 12))
    assert updater.rangos(missing) == [
        (date(2026, 1, 3), date(2026, 1, 4)), (date(2026, 1, 8), date(2026, 1, 8)),
        (date(2026, 1, 11), date(2026, 1, 12)),
    ]


def test_siga_cleaning_never_fills_precipitation_with_zero():
    observed = weather_series("2026-02-01", "2026-02-03", "Observado")
    observed.loc[1, "Prec"] = None
    observed.loc[2, "TMEDIA"] = None
    cleaned, derived, discarded = updater.depurar_observaciones(observed)
    assert discarded == ["2026-02-02"]
    assert derived == ["2026-02-03"]
    assert cleaned.Fecha.tolist() == ["2026-02-01", "2026-02-03"]
    assert cleaned.loc[cleaned.Fecha.eq("2026-02-03"), "TMEDIA"].iloc[0] == 15.0


@pytest.mark.parametrize("path", ["meteo_daily.csv", "data/calibration/pergamino_2026_weather.csv"])
def test_shipped_weather_is_continuous_valid_and_labelled(path):
    frame = pd.read_csv(ROOT / path)
    dates = pd.to_datetime(frame.Fecha)
    assert dates.tolist() == pd.date_range(dates.min(), dates.max()).tolist()
    assert dates.min() == pd.Timestamp("2026-01-01") and dates.max().date() <= CAMPANIA_END
    assert (frame.TMAX >= frame.TMIN).all() and (frame.Prec >= 0).all()
    assert frame[["TMAX", "TMIN", "Prec", "TMEDIA"]].notna().all().all()
    assert set(frame.TipoDato) <= {"Observado", "Provisional", "Pronostico"}
    assert frame.loc[frame.TipoDato.eq("Observado"), "Fuente"].eq("SIGA_INTA_PERGAMINO").all()
