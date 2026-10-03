"""Referencia estacional para normalizar ejecuciones meteorológicas parciales."""

from __future__ import annotations

from pathlib import Path
from hashlib import sha256
import json

import numpy as np
import pandas as pd


def calendar_reference_days(dates):
    """Coordenada común por mes/día; conserva 29/02 entre 28/02 y 01/03."""
    dates = pd.DatetimeIndex(pd.to_datetime(dates))
    days = dates.dayofyear.to_numpy(dtype=float)
    days[dates.is_leap_year & (dates.month > 2)] -= 1
    days[(dates.month == 2) & (dates.day == 29)] = 59.5
    return days


LOCAL_COUNT_PATTERN = "pergamino_{year}_counts.csv"
LOCAL_DIRECTORIES = ("data/calibration", "data/reference")


def local_count_files(root: str | Path) -> dict[int, Path]:
    """Archivos de conteos locales ``pergamino_<año>_counts.csv`` por campaña."""
    root = Path(root)
    found: dict[int, Path] = {}
    for directory in LOCAL_DIRECTORIES:
        for path in sorted((root / directory).glob("pergamino_*_counts.csv")):
            token = path.name.removeprefix("pergamino_").removesuffix("_counts.csv")
            if token.isdigit() and len(token) == 4:
                if int(token) in found:
                    raise ValueError(f"Hay dos archivos de conteos para la campaña {token}.")
                found[int(token)] = path
    return found


def _local_counts(root, year, path):
    relative = path.relative_to(root).as_posix()
    counts = pd.read_csv(path)
    if not {"FECHA", "PLM2"}.issubset(counts.columns) or len(counts) < 2:
        raise ValueError(f"La referencia {year} requiere FECHA y PLM2 y al menos dos visitas.")
    dates = pd.to_datetime(counts["FECHA"], errors="raise").dt.normalize()
    flows = pd.to_numeric(counts["PLM2"], errors="raise").to_numpy(float)
    if (dates.isna().any() or dates.duplicated().any()
            or not dates.is_monotonic_increasing or not dates.dt.year.eq(year).all()
            or not np.isfinite(flows).all() or (flows < 0).any()
            or flows.sum() <= 0 or flows[0] != 0):
        raise ValueError(f"Conteos {year} inválidos o sin cero inicial delimitador.")
    metadata = {
        "path": relative, "sha256": sha256(path.read_bytes()).hexdigest(),
        "start": dates.iloc[0].date().isoformat(), "end": dates.iloc[-1].date().isoformat(),
        "sample_count": len(counts), "window_total_plm2": float(flows.sum()),
        "initial_zero_reference": True,
        "processing": "acumulado / total registrado; interpolación lineal entre visitas",
        "scope": "ventana registrada; no certifica el cierre biológico de la campaña",
    }
    source_file = path.with_name(f"pergamino_{year}_source.json")
    if source_file.exists():
        source = json.loads(source_file.read_text(encoding="utf-8"))
        metadata.update(source_file=source_file.relative_to(root).as_posix(),
                        weather=source.get("weather_nature"),
                        initial_zero_note=source.get("initial_zero_note"))
    return dates, flows, metadata


def load_local_seasonal_reference(root: str | Path, as_of=None) -> pd.DataFrame | None:
    """Pool local de Pergamino, con igual peso por campaña.

    Los conteos se normalizan por su propio total registrado. Una campaña sólo
    entra en la referencia desde su último conteo (``as_of`` >= fin de la
    ventana registrada), de modo que un corte no usa totales futuros. Devuelve
    ``None`` si ninguna campaña está disponible para el corte: el porcentaje
    acumulado queda entonces «aún no estimable» y se informa el flujo absoluto.
    Cada campaña debe empezar con un cero que delimite el primer intervalo.
    """
    root = Path(root)
    files = local_count_files(root)
    if not files:
        raise ValueError("No hay conteos locales pergamino_<año>_counts.csv.")
    # Validar también fuentes todavía no habilitadas; no aceptar datos dañados.
    records = {year: _local_counts(root, year, path) for year, path in files.items()}
    cutoff = pd.Timestamp(as_of).tz_localize(None).normalize() if as_of is not None else None
    if cutoff is not None and pd.isna(cutoff):
        raise ValueError("Fecha de corte de la referencia inválida.")
    used = {year: cutoff is None or cutoff >= dates.iloc[-1]
            for year, (dates, _, _) in records.items()}
    if not any(used.values()):
        return None
    axis_end = max(int(calendar_reference_days(dates).max())
                   for year, (dates, _, _) in records.items() if used[year])
    axis = np.arange(1, axis_end + 1, dtype=float)
    if any(used[year] and (dates.dt.month.eq(2) & dates.dt.day.eq(29)).any()
           for year, (dates, _, _) in records.items()):
        axis = np.sort(np.append(axis, 59.5))
    reference = pd.DataFrame({"Julian_days": axis})
    reference.attrs["calendar_basis"] = "month_day_nonleap_feb29_half"
    included, excluded = [], []
    for year, (dates, flows, metadata) in sorted(records.items()):
        metadata["used"] = used[year]
        reference.attrs[f"source_{year}"] = metadata
        reference[f"Referencia_{year}_Desde"] = metadata["end"]
        if used[year]:
            reference[f"Progreso_{year}"] = np.interp(
                reference.Julian_days, calendar_reference_days(dates), np.cumsum(flows) / flows.sum(),
                left=np.nan, right=1.0,
            )
            included.append(Path(metadata["path"]).name)
        else:
            excluded.append(f"{Path(metadata['path']).name} (disponible desde {dates.iloc[-1]:%d/%m/%Y})")
    years = sorted(year for year, selected in used.items() if selected)
    campaigns = reference[[f"Progreso_{year}" for year in years]]
    reference["N_Campanas_Dia"] = campaigns.notna().sum(axis=1)
    for q, column in [(0.10, "Progreso_P10"), (0.50, "Progreso_Mediano"), (0.90, "Progreso_P90")]:
        empirical = campaigns.quantile(q, axis=1)
        reference[column + "_Empirico"] = empirical
        # Ancla no decreciente cuando cambia la cantidad de campañas con
        # referencia para ese día del calendario.
        reference[column] = empirical.cummax()
    changed = reference.N_Campanas_Dia.diff().fillna(reference.N_Campanas_Dia).gt(0)
    increment = reference.Progreso_Mediano.diff().fillna(reference.Progreso_Mediano)
    # Un salto al ingresar una curva incompleta al inicio es cambio de
    # composición, no evidencia de nacimientos ocurridos ese día.
    reference["Flujo_No_Comparable"] = changed & increment.gt(1e-12)
    reference["N_Campanas"] = len(years)
    reference["Campanas_Anos"] = ", ".join(map(str, years))
    reference["Campanas"] = ", ".join(included)
    reference["Campanas_Excluidas"] = ", ".join(excluded)
    return reference


def reference_progress(
    reference: pd.DataFrame, julian_days, *, dates=None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Interpola cuantiles; en el pool local alinea por mes/día si hay fechas."""
    days = (calendar_reference_days(dates)
            if dates is not None and reference.attrs.get("calendar_basis") == "month_day_nonleap_feb29_half"
            else np.asarray(julian_days, dtype=float))
    axis = reference["Julian_days"].to_numpy(float)
    values = []
    for column in ("Progreso_P10", "Progreso_Mediano", "Progreso_P90"):
        values.append(
            np.interp(
                days,
                axis,
                reference[column].to_numpy(float),
                left=0.0,
                right=1.0,
            )
        )
    return tuple(values)


def partial_season_normalization(
    trajectory: pd.DataFrame,
    as_of,
    reference: pd.DataFrame,
) -> tuple[float | None, dict]:
    """Estima el total de señal estacional sin usar el fin del pronóstico.

    La señal acumulada de PREDWEEM se ancla, en la fecha del estado, al progreso
    mediano de campañas históricas, utilizando sólo fechas hasta el corte.
    Sin señal o progreso histórico suficiente, el porcentaje no es estimable.
    """
    cutoff = pd.Timestamp(as_of).tz_localize(None).normalize()
    if pd.isna(cutoff):
        raise ValueError("Fecha de corte de normalización inválida.")
    dates = pd.to_datetime(trajectory["Fecha"]).dt.tz_localize(None).dt.normalize()
    past = trajectory.loc[dates <= cutoff].sort_values("Fecha")
    if past.empty:
        return None, {"mode": "porcentaje aún no estimable", "reason": "Sin historia hasta el corte."}
    anchor = past.iloc[-1]
    p10, median, p90 = reference_progress(
        reference, [float(anchor["Julian_days"])], dates=[anchor["Fecha"]]
    )
    raw_cumulative = float(anchor["EMERAC"])
    metadata = {
        "mode": "porcentaje aún no estimable",
        "anchor_date": anchor["Fecha"],
        "reference_progress": float(median[0]),
    }
    if (not np.isfinite([raw_cumulative, median[0]]).all()
            or raw_cumulative <= 1e-12 or median[0] <= 0.01):
        return None, {**metadata, "reason": "Sin señal acumulada o progreso histórico mayor al 1% hasta el corte."}

    seasonal_total = float(raw_cumulative / median[0])
    if not np.isfinite(seasonal_total) or seasonal_total <= 1e-12:
        return None, {**metadata, "reason": "Denominador estacional no válido."}
    return seasonal_total, {
        **metadata,
        "mode": "referencia estacional histórica",
        "reference_p10": float(p10[0]),
        "reference_p90": float(p90[0]),
        "seasonal_signal_total": seasonal_total,
    }
