"""Aviso preventivo de inicio; no modifica la trayectoria ni el reloj térmico."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .weather import forecast_mask


def onset_window_from_observations(observations, as_of) -> tuple[tuple, dict]:
    """Ventana de inicio de la emergencia que implican los conteos hasta el corte.

    Devuelve ``((desde, hasta), info)``. ``hasta`` es la fecha del primer
    conteo positivo: ya había plantas. ``desde`` es el día siguiente al último
    conteo en cero anterior (el intervalo de un conteo termina en su fecha), y
    sólo existe si hay un conteo en cero explícito; sin él sólo hay cota
    superior. Si todavía no hay conteos positivos pero sí uno en cero, sólo hay
    cota inferior: ``(desde, None)``. Sólo se usan conteos de la campaña del
    corte y hasta el corte. ``((None, None), info)`` si no hay conteos válidos.
    """
    info = {"source": None, "first_positive": None, "last_zero": None}
    if observations is None or len(observations) == 0:
        return (None, None), info
    cutoff = pd.Timestamp(as_of).tz_localize(None).normalize()
    frame = observations.copy()
    dates = pd.to_datetime(frame["Fecha"], errors="coerce").dt.tz_localize(None).dt.normalize()
    for column in ("Flujo_observado_PLM2", "Observado"):
        if column not in frame:
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        valid = dates.notna() & np.isfinite(values) & dates.le(cutoff) & dates.dt.year.eq(cutoff.year)
        if not valid.any():
            continue
        table = pd.DataFrame({"Fecha": dates[valid], "valor": values[valid]}).sort_values("Fecha")
        positive = table[table["valor"] > 0]
        first = positive["Fecha"].iloc[0] if not positive.empty else None
        zeros = table[(table["valor"] <= 0) & ((table["Fecha"] < first) if first is not None else True)]
        last_zero = zeros["Fecha"].iloc[-1] if not zeros.empty else None
        if first is None and last_zero is None:
            continue
        info.update(
            source=column,
            first_positive=first.date().isoformat() if first is not None else None,
            last_zero=last_zero.date().isoformat() if last_zero is not None else None,
        )
        earliest = last_zero + pd.Timedelta(days=1) if last_zero is not None else None
        return (earliest, first), info
    return (None, None), info


def onset_alert(trajectory, as_of, observations=None, enabled=True) -> dict:
    """Consulta el primer pico del motor en la campaña hasta el corte + 7 días.

    Requiere la trayectoria desde el comienzo de la campaña. Los conteos son
    opcionales y solo se consultan hasta el corte. Una visita positiva prueba
    que ya había emergencia, sin convertirla en fecha exacta de inicio.
    """
    cutoff = pd.Timestamp(as_of).tz_localize(None).normalize()
    horizon = pd.date_range(cutoff + pd.Timedelta(days=1), periods=7)
    result = dict(
        enabled=bool(enabled), horizon_days=7, status="disabled", level="info",
        title="Alerta preventiva de inicio desactivada", message="",
        model_onset_date=None, first_positive_date=None,
        monitoring_alert_date=None,
        forecast_days_available=0, mode="Sin datos",
    )
    if not enabled:
        return result
    frame = trajectory.copy()
    frame["Fecha"] = pd.to_datetime(frame["Fecha"], errors="coerce").dt.tz_localize(None).dt.normalize()
    frame = frame.loc[frame["Fecha"].dt.year.eq(cutoff.year) & frame["Fecha"].le(horizon[-1])]
    frame = frame.loc[~frame["Fecha"].duplicated(keep=False)].sort_values("Fecha")
    if "Primer_Pico_Habilitado" in frame:
        reached = frame.loc[frame["Primer_Pico_Habilitado"].eq(True), "Fecha"]
        if not reached.empty:
            result["model_onset_date"] = reached.iloc[0].date().isoformat()
            # Referencia gráfica calculada, no registro de una emisión pasada.
            result["monitoring_alert_date"] = (
                reached.iloc[0] - pd.Timedelta(days=7)
            ).date().isoformat()

    if observations is not None and not observations.empty:
        dates = pd.to_datetime(observations["Fecha"], errors="coerce").dt.tz_localize(None).dt.normalize()
        positive = pd.Series(False, index=observations.index)
        for column in ("Flujo_observado_PLM2", "Observado"):
            if column in observations:
                values = pd.to_numeric(observations[column], errors="coerce")
                positive |= values.gt(0) & np.isfinite(values)
        dates = dates[positive & dates.le(cutoff) & dates.dt.year.eq(cutoff.year)]
        if not dates.empty:
            first = dates.min()
            message = (
                f"Había plantas a más tardar el {first:%d/%m/%Y}. "
                "El primer conteo positivo no fija el día exacto de inicio. "
                "Continúe el seguimiento de los nuevos nacimientos."
            )
            if "Inicio_Anclado" in frame and frame["Inicio_Anclado"].fillna(False).astype(bool).any():
                since = pd.to_datetime(frame["Inicio_Ventana_Desde"], errors="coerce").dropna()
                since = since.iloc[0] if len(since) else None
                result["anchored"] = True
                result["onset_window"] = [
                    since.date().isoformat() if since is not None else None,
                    first.date().isoformat(),
                ]
                span = (f"entre el {since:%d/%m/%Y} y el {first:%d/%m/%Y}"
                        if since is not None else f"a más tardar el {first:%d/%m/%Y}")
                message += f" Inicio anclado a los conteos: {span}."
                if result["model_onset_date"]:
                    message += (" Primer pico y tiempo térmico desde el "
                                f"{pd.Timestamp(result['model_onset_date']):%d/%m/%Y}.")
            result.update(
                status="observed", mode="Conteo de campo", level="warning",
                title="Emergencia ya registrada en el lote",
                first_positive_date=first.date().isoformat(),
                message=message,
            )
            return result

    onset = pd.Timestamp(result["model_onset_date"]) if result["model_onset_date"] else None
    if onset is not None and onset <= cutoff:
        result.update(
            status="started", mode="Inicio modelado",
            title=f"Inicio modelado: {onset:%d/%m/%Y}",
            message="El modelo ya activó el primer pico. La fecha es estimada; "
                    "contraste con una recorrida cuando sea posible.",
        )
        return result

    future = frame.loc[frame["Fecha"].isin(horizon)].copy()
    if "Primer_Pico_Habilitado" not in future or "EMERREL" not in future:
        result.update(status="unavailable", title="Inicio no evaluable", message="Faltan datos del inicio modelado.")
        return result
    values = pd.to_numeric(future["EMERREL"], errors="coerce")
    future = future.loc[np.isfinite(values) & values.ge(0) & future["Primer_Pico_Habilitado"].notna()]
    available = len(future)
    result["forecast_days_available"] = available
    result["mode"] = "Sin horizonte meteorológico" if not available else "Revisión retrospectiva / fuente no verificada"
    if available and forecast_mask(future).all():
        result["mode"] = "Pronóstico disponible; emisión no documentada"
        if "EMISION_UTC" in future:
            emissions = pd.to_datetime(future["EMISION_UTC"], errors="coerce", utc=True)
            day_end = (cutoff + pd.Timedelta(days=1)).tz_localize("America/Argentina/Buenos_Aires")
            if emissions.notna().all() and emissions.lt(day_end).all():
                result["mode"] = "Pronóstico disponible al corte"
            elif emissions.notna().any():
                result["mode"] = "Revisión retrospectiva / emisión posterior al corte"
    # Una señal positiva en un horizonte parcial también merece vigilancia.
    if onset is not None and onset in set(future["Fecha"]):
        days = (onset - cutoff).days
        result.update(
            status="watch", level="warning",
            title="Alerta preventiva: posible inicio en los próximos 7 días",
            message=f"Inicio modelado para el {onset:%d/%m/%Y} (dentro de {days} días). "
                    "Priorice una recorrida para detectar los primeros nacimientos. "
                    f"Horizonte disponible: {available}/7 días. La fecha puede cambiar con la meteorología.",
        )
    elif available < 7:
        result.update(
            status="unavailable", title="Alerta de inicio: horizonte incompleto",
            message=f"Disponibles {available}/7 días. No se puede descartar un inicio en la próxima semana.",
        )
    else:
        result.update(
            status="no_signal", title="Sin inicio previsto en los próximos 7 días",
            message="El modelo no activa el primer pico en este horizonte. "
                    "Esto no descarta nacimientos: mantenga el seguimiento del lote.",
        )
    return result
