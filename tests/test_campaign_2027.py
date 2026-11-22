"""Reinicio automático en 2027, sin acceso a servicios externos.

Las series de 2027 de estas pruebas son fixtures sintéticos.
"""

import contextlib
import importlib
import io
import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import campaign
from predweem_twin import weather

ROOT = Path(__file__).resolve().parents[1]
_MODULES = sorted(path.stem for path in ROOT.glob("actualizar_meteo_*.py"))
_ROBUST = next((name for name in _MODULES if name.endswith("_robusto")), None)
# Módulo con la configuración de campaña y, si existe, actualizador robusto que lo usa.
updater = importlib.import_module(next(name for name in _MODULES if not name.endswith("_robusto")))
runner = importlib.import_module(_ROBUST) if _ROBUST else updater
CAMPAIGN_GLOBALS = ("CAMPANIA_ANIO", "CAMPANIA_START", "CAMPANIA_END",
                    "ARCHIVO_MAESTRO_DEFAULT", "ARCHIVO_SIGA_CACHE", "ARCHIVO_ESTADO")


def series(first, last, kind):
    frame = pd.DataFrame({
        "Fecha": pd.date_range(first, last).strftime("%Y-%m-%d"),
        "TMAX": 20.0, "TMIN": 10.0, "TMEDIA": 15.0, "Prec": 1.0,
        "TipoDato": kind,
    })
    if kind == "Pronostico":
        for column in ("TMAX", "TMIN", "TMEDIA", "Prec"):
            frame[column + "_P50"] = frame[column]
        frame["N_miembros"] = 50
    return updater.asegurar_columnas(frame)


@pytest.fixture
def campaign_2027(monkeypatch, tmp_path):
    for name in CAMPAIGN_GLOBALS:  # monkeypatch restaura 2026 al terminar.
        monkeypatch.setattr(updater, name, getattr(updater, name))
    updater.configurar_campania(2027)
    monkeypatch.setattr(updater, "ARCHIVO_MAESTRO_DEFAULT", tmp_path / "meteo.csv")
    monkeypatch.setattr(updater, "ARCHIVO_ESTADO", tmp_path / "state.json")
    monkeypatch.setattr(updater, "ARCHIVO_SIGA_CACHE", tmp_path / "siga.csv")
    monkeypatch.setattr(updater, "DIRECTORIO_PRONOSTICOS", tmp_path / "forecasts")
    return tmp_path


def run_update(monkeypatch, base, today, observed=None, bridge=None, siga_error=None):
    calls = {"siga": 0, "bridge": []}

    def siga(start, end, archivo_forzado=None):
        calls["siga"] += 1
        if siga_error:
            raise RuntimeError(siga_error)
        return observed, "test"

    def provisional(dates):
        calls["bridge"].append(list(dates))
        if not dates:
            return updater.asegurar_columnas(pd.DataFrame())
        return series(min(dates), max(dates), "Provisional")

    monkeypatch.setattr(updater, "hoy_argentina", lambda: today)
    monkeypatch.setattr(updater, "obtener_siga_dataframe", siga)
    def provisional_range(start, end):
        return provisional([start + timedelta(days=d) for d in range((end - start).days + 1)])

    forecast = series(today, today + timedelta(days=6), "Pronostico")
    if runner is not updater:  # Actualizador robusto (Pergamino, San Pedro).
        monkeypatch.setattr(runner, "cargar_provisional", provisional_range)
        name = "cargar_ensamble" if hasattr(runner, "cargar_ensamble") else "cargar_ens"
        monkeypatch.setattr(runner, name, lambda: forecast)
    elif hasattr(updater, "cargar_provisionales_ecmwf"):
        monkeypatch.setattr(updater, "cargar_provisionales_ecmwf", provisional)
    else:  # Puente sólo después de la última observación.
        monkeypatch.setattr(updater, "cargar_provisional_ecmwf", provisional_range)
    monkeypatch.setattr(updater, "consultar_ecmwf_ens", lambda: {})
    monkeypatch.setattr(updater, "procesar_ecmwf_ens", lambda data: forecast)
    with contextlib.redirect_stdout(io.StringIO()):
        if runner is updater:
            result = updater.construir_meteo_daily(base / "meteo.csv")
        else:
            result = runner.ejecutar()
    assert (base / "meteo.csv").exists()
    return result, calls


def test_campaign_rolls_over_on_january_first_with_separate_files():
    assert campaign.default_campaign_year(date(2026, 12, 31)) == 2026
    assert campaign.default_campaign_year(date(2027, 1, 1)) == 2027
    assert campaign.campaign_bounds(2027) == (date(2027, 1, 1), date(2027, 10, 1))
    legacy = campaign.campaign_weather_path(2026)
    new = campaign.campaign_weather_path(2027)
    assert legacy == campaign.WEATHER_FILE
    assert new == legacy.with_name(f"{legacy.stem}_2027{legacy.suffix}")
    assert (ROOT / legacy).exists()
    with pytest.raises(ValueError, match="no habilitada"):
        campaign.campaign_bounds(2028)


def test_updater_writes_2027_files_without_touching_2026():
    try:
        updater.configurar_campania(2027)
        names = {updater.ARCHIVO_MAESTRO_DEFAULT, updater.ARCHIVO_SIGA_CACHE, updater.ARCHIVO_ESTADO}
        assert all("_2027" in path.name for path in names)
        assert updater.CAMPANIA_START == date(2027, 1, 1)
        assert updater.CAMPANIA_END == date(2027, 10, 1)
    finally:
        updater.configurar_campania(2026)
    assert updater.ARCHIVO_MAESTRO_DEFAULT == updater.ARCHIVO_MAESTRO_BASE


def test_january_first_runs_with_forecast_only(monkeypatch, campaign_2027):
    result, calls = run_update(monkeypatch, campaign_2027, date(2027, 1, 1))
    assert calls["siga"] == 0
    assert result.Fecha.tolist() == pd.date_range("2027-01-01", "2027-01-07").strftime("%Y-%m-%d").tolist()
    assert result.TipoDato.eq("Pronostico").all()
    state = json.loads((campaign_2027 / "state.json").read_text())
    assert state["campania"] == 2027
    assert state.get("fin_campania", state.get("fecha_fin_campania")) == "2027-10-01"
    assert state["ultima_observacion_siga"] is None
    assert state["huecos_finales"] == []


def test_siga_without_2027_data_is_bridged_only_at_start(monkeypatch, campaign_2027):
    empty = updater.asegurar_columnas(pd.DataFrame())
    result, calls = run_update(monkeypatch, campaign_2027, date(2027, 1, 4), observed=empty)
    assert calls["bridge"] == [[date(2027, 1, 1), date(2027, 1, 2), date(2027, 1, 3)]]
    assert result.Fecha.min() == "2027-01-01"
    assert not result.Fecha.lt("2027-01-01").any()

    _, calls = run_update(monkeypatch, campaign_2027, date(2027, 1, 4), siga_error="sin respuesta")
    assert calls["bridge"][0][0] == date(2027, 1, 1)

    if runner is updater:  # El actualizador robusto conserva su puente provisional.
        with pytest.raises(ValueError, match="SIGA no aportó"):
            run_update(monkeypatch, campaign_2027, date(2027, 2, 1), observed=empty)
    with pytest.raises(RuntimeError, match="sin respuesta"):
        run_update(monkeypatch, campaign_2027, date(2027, 2, 1), siga_error="sin respuesta")


def test_mid_campaign_2027_uses_only_2027_observations(monkeypatch, campaign_2027):
    observed = series("2027-01-01", "2027-03-09", "Observado")
    result, _ = run_update(monkeypatch, campaign_2027, date(2027, 3, 10), observed=observed)
    assert result.Fecha.min() == "2027-01-01"
    assert result.Fecha.max() == "2027-03-16"
    assert len(result) == (date(2027, 3, 16) - date(2027, 1, 1)).days + 1


def test_main_refuses_a_campaign_that_has_not_started(monkeypatch, tmp_path):
    for name in CAMPAIGN_GLOBALS:
        monkeypatch.setattr(updater, name, getattr(updater, name))
    monkeypatch.setattr(updater, "hoy_argentina", lambda: date(2026, 11, 22))
    monkeypatch.chdir(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("No debe descargar datos de una campaña futura")

    monkeypatch.setattr(updater, "obtener_siga_dataframe", forbidden)
    monkeypatch.setattr(updater, "consultar_ecmwf_ens", forbidden)
    with contextlib.redirect_stderr(io.StringIO()):
        if runner is updater:
            monkeypatch.setattr(sys, "argv", ["updater", "--year", "2027"])
            assert updater.main() == 1
        else:
            assert runner.main(["--year", "2027"]) == 1
    assert not list(tmp_path.rglob("*"))


def test_app_opens_latest_campaign_with_weather(tmp_path):
    legacy = tmp_path / campaign.campaign_weather_path(2026)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("Fecha\n2026-01-01\n")
    assert campaign.initial_campaign_year(tmp_path, date(2026, 11, 22)) == 2026
    # 1 de enero antes de la primera actualización: sigue mostrando 2026.
    assert campaign.initial_campaign_year(tmp_path, date(2027, 1, 1)) == 2026
    (tmp_path / campaign.campaign_weather_path(2027)).write_text("Fecha\n2027-01-01\n")
    assert campaign.initial_campaign_year(tmp_path, date(2027, 1, 1)) == 2027
    assert campaign.initial_campaign_year(tmp_path, date(2026, 12, 31)) == 2026


def test_app_weather_is_limited_to_the_selected_campaign(tmp_path):
    path = tmp_path / "both.csv"
    pd.DataFrame({"Fecha": ["2026-05-05", "2027-05-05", "2027-10-02"],
                  "TMAX": 20., "TMIN": 10., "Prec": [9., 2., 3.]}).to_csv(path, index=False)
    with pytest.raises(ValueError, match="varios años"):
        weather.read_weather_file(path)
    assert weather.read_weather_file(path, campaign_year=2027).Prec.tolist() == [2.]
    frame = pd.DataFrame({"Fecha": pd.date_range("2027-09-25", "2027-10-10")})
    window, meta = weather.operational_weather_window(frame, as_of="2027-09-28")
    assert window.Fecha.max() == pd.Timestamp("2027-10-01")
    assert meta["campaign_year"] == 2027
    assert meta["forecast_days_expected"] == 3


def _app_copy(tmp_path):
    for pattern in ("*.py", "*.json"):
        for source in ROOT.glob(pattern):
            shutil.copy2(source, tmp_path / source.name)
    legacy = ROOT / campaign.campaign_weather_path(2026)
    if legacy.parent == ROOT:
        shutil.copy2(legacy, tmp_path / legacy.name)
    for directory in ("models", "predweem_twin", "data"):
        shutil.copytree(ROOT / directory, tmp_path / directory,
                        ignore=shutil.ignore_patterns("*.db", "*.db-shm", "*.db-wal", "__pycache__",
                                                      "historico_pronosticos"))
    return tmp_path


def test_app_runs_2027_campaign_from_its_own_file(tmp_path):
    root = _app_copy(tmp_path)
    app = AppTest.from_file(str(root / "app.py"), default_timeout=60).run()
    assert not app.exception, [error.message for error in app.exception]
    app.selectbox(key="campaign_year").set_value(2027).run()
    assert not app.exception, [error.message for error in app.exception]
    assert any("2027" in info.value for info in app.info)

    frame = pd.concat([
        series("2027-01-01", "2027-05-04", "Observado"),
        series("2027-05-05", "2027-05-11", "Pronostico"),
    ], ignore_index=True)
    frame.to_csv(root / campaign.campaign_weather_path(2027), index=False)
    app.run()
    assert not app.exception, [error.message for error in app.exception]
    state_date = app.date_input(key="state_date_2027")
    assert state_date.value.year == 2027
