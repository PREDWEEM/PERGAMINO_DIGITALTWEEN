"""Flujos históricos compartidos por los gráficos y la intensidad semanal."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .seasonal import calendar_reference_days


def annual_historical_reference(reference, as_of):
    """Traslada el pool al calendario consultado sin inventar una cola anual.

    Las referencias locales son las campañas de Pergamino con conteos propios.
    Se conserva mes/día y un eventual 29/02 en la coordenada común 59.5.
    Fuera del eje histórico se deja NaN, no un supuesto de emergencia nula.
    El flujo diario es derivado del acumulado, no un conteo diario observado.
    """
    year = pd.Timestamp(as_of).year
    dates = pd.date_range(f"{year}-01-01", f"{year}-12-31")
    frame = pd.DataFrame({"Fecha": dates})
    days = calendar_reference_days(dates)
    axis = reference["Julian_days"].to_numpy(float)
    columns = ["Progreso_Mediano"] + [
        column for column in reference
        if column.startswith("Progreso_") and column.removeprefix("Progreso_").isdigit()
    ]
    for column in columns:
        frame[column] = np.interp(
            days, axis, reference[column].to_numpy(float),
            left=np.nan, right=np.nan,
        )
    # Los individuales conservan sus ventanas reales. El resumen mantiene
    # el total tras el cierre del archivo sólo como supuesto de referencia.
    for year in sorted(
        int(column.removeprefix("Progreso_")) for column in frame
        if column.removeprefix("Progreso_").isdigit()
    ):
        source = reference.attrs.get(f"source_{year}", {})
        if f"Progreso_{year}" in frame and source.get("end"):
            end_day = calendar_reference_days([source["end"]])[0]
            frame.loc[days > end_day, f"Progreso_{year}"] = np.nan
    frame["Flujo_Diario"] = frame["Progreso_Mediano"].diff().clip(lower=0)
    if axis[0] == 1:
        frame.loc[0, "Flujo_Diario"] = frame.loc[0, "Progreso_Mediano"]
    unsupported = reference.get("Flujo_No_Comparable", pd.Series(False, index=reference.index))
    changes = np.isin(days, axis[unsupported.to_numpy(bool)])
    frame["Cambio_Composicion_Pool"] = changes
    frame["Incremento_No_Distribuido"] = frame["Flujo_Diario"].fillna(frame["Progreso_Mediano"]).where(changes, 0.0)
    frame.loc[changes, "Flujo_Diario"] = np.nan
    frame.attrs["campaigns"] = reference["Campanas_Anos"].iloc[0]
    return frame


def weekly_flow_groups(dates, values):
    """Agrupa flujos diarios de lunes a domingo sin completar datos faltantes."""
    frame = pd.DataFrame({"Fecha": pd.to_datetime(dates), "Flujo": values})
    frame["Semana"] = frame["Fecha"] - pd.to_timedelta(frame["Fecha"].dt.dayofweek, unit="D")
    return frame.groupby("Semana", sort=True)


def historical_weekly_max(reference, as_of):
    """Máximo de semanas completas del pool visible (enero–1 de octubre).

    Usa el mismo calendario, resumen histórico y escala fraccional del gráfico.
    No toma el máximo diario ni el máximo de una campaña individual.
    """
    if reference is None or reference.empty:
        return None
    historical = annual_historical_reference(reference, as_of)
    end = pd.Timestamp(pd.Timestamp(as_of).year, 10, 1)
    historical = historical.loc[historical["Fecha"] <= end]
    totals = []
    for _, group in weekly_flow_groups(historical["Fecha"], historical["Flujo_Diario"]):
        valid = np.isfinite(group["Flujo"]) & group["Flujo"].ge(0)
        if group.loc[valid, "Fecha"].nunique() == 7:
            totals.append(float(group.loc[valid, "Flujo"].sum()))
    peak = max(totals, default=0.0)
    return peak if peak > 0 else None
