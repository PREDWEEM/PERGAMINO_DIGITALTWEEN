# -*- coding: utf-8 -*-
"""Actualizador meteorológico robusto PREDWEEM — Pergamino.

Mantiene SIGA–INTA como fuente observada prioritaria y añade:
- depuración explícita de observaciones incompletas;
- TMEDIA derivada únicamente desde Tmax/Tmin cuando falta;
- ECMWF IFS histórico provisional para cada fecha vencida sin SIGA válido;
- ECMWF IFS ENS emparejado por miembro, sin completar nulos con cero;
- P50 coherente para TMAX, TMIN, TMEDIA y precipitación;
- validación diaria y escritura atómica.
"""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd

import actualizar_meteo_pergamino as base


MIN_MIEMBROS = 30
FRACCION_MINIMA = 0.80
HORAS_DIA = 24
URL_HIST = "https://archive-api.open-meteo.com/v1/archive"
MODELO_HIST = "ecmwf_ifs"

COLUMNAS = [
    "Fecha", "TMAX", "TMIN", "Prec", "TMEDIA",
    "TMAX_Media_Ens", "TMIN_Media_Ens", "TMEDIA_Media_Ens", "Prec_Media_Ens",
    "TMAX_P10", "TMAX_P50", "TMAX_P90",
    "TMIN_P10", "TMIN_P50", "TMIN_P90",
    "TMEDIA_P10", "TMEDIA_P50", "TMEDIA_P90",
    "Prec_P10", "Prec_P50", "Prec_P90",
    "Prob_Prec_ge_1mm", "Prob_Prec_ge_5mm",
    "Prob_Prec_ge_10mm", "Prob_Prec_ge_30mm",
    "GD_Tb2", "Fuente", "TipoDato", "CalidadDato", "N_miembros",
    "Latitud_grilla", "Longitud_grilla", "Elevacion_grilla_m", "Emision_UTC",
]
base.COLUMNAS_COMPLETAS = COLUMNAS


def columnas(df: pd.DataFrame) -> pd.DataFrame:
    salida = df.copy()
    for columna in COLUMNAS:
        if columna not in salida.columns:
            salida[columna] = np.nan
    return salida[COLUMNAS]


def resumen(fechas: list[str], limite: int = 20) -> str:
    texto = ", ".join(fechas[:limite])
    return texto + (f", ... ({len(fechas)} fechas)" if len(fechas) > limite else "")


def depurar_observaciones(
    observaciones: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Deriva solo TMEDIA; nunca rellena precipitación faltante con cero."""
    salida = columnas(observaciones)
    salida["Fecha_dt"] = pd.to_datetime(
        salida["Fecha"], errors="coerce"
    ).dt.normalize()

    for columna in ("TMAX", "TMIN", "TMEDIA", "Prec"):
        salida[columna] = pd.to_numeric(salida[columna], errors="coerce")

    derivar_tmedia = (
        salida["Fecha_dt"].notna()
        & salida["TMEDIA"].isna()
        & salida["TMAX"].notna()
        & salida["TMIN"].notna()
        & salida["TMAX"].between(-25, 55)
        & salida["TMIN"].between(-35, 45)
        & (salida["TMAX"] >= salida["TMIN"])
    )
    fechas_derivadas = (
        salida.loc[derivar_tmedia, "Fecha_dt"]
        .dt.strftime("%Y-%m-%d")
        .tolist()
    )
    salida.loc[derivar_tmedia, "TMEDIA"] = (
        salida.loc[derivar_tmedia, "TMAX"]
        + salida.loc[derivar_tmedia, "TMIN"]
    ) / 2.0
    salida.loc[
        derivar_tmedia, "CalidadDato"
    ] = "Observado_estacion_TMEDIA_derivada"

    invalidas = (
        salida["Fecha_dt"].isna()
        | salida[["TMAX", "TMIN", "TMEDIA", "Prec"]].isna().any(axis=1)
        | ~salida["TMAX"].between(-25, 55)
        | ~salida["TMIN"].between(-35, 45)
        | ~salida["TMEDIA"].between(-35, 55)
        | (salida["TMAX"] < salida["TMIN"])
        | ~salida["Prec"].between(0, 500)
    )
    descartadas = (
        salida.loc[invalidas & salida["Fecha_dt"].notna(), "Fecha_dt"]
        .dt.strftime("%Y-%m-%d")
        .tolist()
    )

    if fechas_derivadas:
        print(
            "ℹ️ TMEDIA SIGA derivada de Tmax/Tmin en: "
            + resumen(fechas_derivadas)
        )
    if descartadas:
        print(
            "⚠️ Filas SIGA incompletas o inválidas pasan a puente ECMWF: "
            + resumen(descartadas)
        )

    salida = salida.loc[~invalidas].copy()
    salida["GD_Tb2"] = np.maximum(0.0, salida["TMEDIA"] - base.TBASE)
    salida["Fecha"] = salida["Fecha_dt"].dt.strftime("%Y-%m-%d")
    salida = (
        salida.drop(columns=["Fecha_dt"])
        .drop_duplicates("Fecha", keep="last")
        .sort_values("Fecha")
        .reset_index(drop=True)
    )
    return columnas(salida), fechas_derivadas, descartadas


def fechas_faltantes(
    observaciones: pd.DataFrame,
    inicio: date,
    fin: date,
) -> list[date]:
    if inicio > fin:
        return []
    esperadas = pd.date_range(inicio, fin, freq="D")
    presentes = pd.DatetimeIndex(
        pd.to_datetime(observaciones["Fecha"], errors="coerce").dropna()
    ).normalize()
    return [fecha.date() for fecha in esperadas.difference(presentes)]


def rangos(fechas: list[date]) -> list[tuple[date, date]]:
    if not fechas:
        return []
    ordenadas = sorted(set(fechas))
    salida: list[tuple[date, date]] = []
    inicio = anterior = ordenadas[0]
    for actual in ordenadas[1:]:
        if actual == anterior + timedelta(days=1):
            anterior = actual
            continue
        salida.append((inicio, anterior))
        inicio = anterior = actual
    salida.append((inicio, anterior))
    return salida


def parametros_historicos(inicio: date, fin: date) -> dict[str, Any]:
    return {
        "latitude": base.LATITUD,
        "longitude": base.LONGITUD,
        "start_date": inicio.isoformat(),
        "end_date": fin.isoformat(),
        "models": MODELO_HIST,
        "timezone": base.ZONA_HORARIA,
        "temperature_unit": "celsius",
        "precipitation_unit": "mm",
        "cell_selection": "land",
    }


def normalizar_provisional(
    datos_diarios: pd.DataFrame,
    payload: dict[str, Any],
    inicio: date,
    fin: date,
) -> pd.DataFrame:
    salida = datos_diarios.copy()
    salida["Fecha"] = pd.to_datetime(
        salida["Fecha"], errors="coerce"
    ).dt.normalize()
    for columna in ("TMAX", "TMIN", "TMEDIA", "Prec"):
        salida[columna] = pd.to_numeric(salida[columna], errors="coerce")

    salida = salida.loc[
        salida["Fecha"].notna()
        & (salida["Fecha"].dt.date >= inicio)
        & (salida["Fecha"].dt.date <= fin)
    ].copy()

    esperadas = pd.date_range(inicio, fin, freq="D")
    faltantes = esperadas.difference(pd.DatetimeIndex(salida["Fecha"]))
    if len(faltantes):
        raise ValueError(
            "ECMWF histórico no devolvió todas las fechas: "
            + resumen(faltantes.strftime("%Y-%m-%d").tolist())
        )

    nulas = salida[["TMAX", "TMIN", "TMEDIA", "Prec"]].isna().any(axis=1)
    if nulas.any():
        raise ValueError(
            "ECMWF histórico devolvió valores nulos en: "
            + resumen(
                salida.loc[nulas, "Fecha"]
                .dt.strftime("%Y-%m-%d")
                .tolist()
            )
        )
    if (salida["TMAX"] < salida["TMIN"]).any():
        raise ValueError("ECMWF histórico devolvió TMAX menor que TMIN.")
    if (salida["Prec"] < 0).any():
        raise ValueError("ECMWF histórico devolvió precipitación negativa.")

    for variable in ("TMAX", "TMIN", "TMEDIA", "Prec"):
        salida[f"{variable}_P50"] = salida[variable]

    salida["GD_Tb2"] = np.maximum(0.0, salida["TMEDIA"] - base.TBASE)
    salida["Fuente"] = "ECMWF_IFS_HISTORICO"
    salida["TipoDato"] = "Provisional"
    salida["CalidadDato"] = "Provisional_hasta_reemplazo_SIGA"
    salida["N_miembros"] = 1
    salida["Latitud_grilla"] = payload.get("latitude", np.nan)
    salida["Longitud_grilla"] = payload.get("longitude", np.nan)
    salida["Elevacion_grilla_m"] = payload.get("elevation", np.nan)
    salida["Emision_UTC"] = base.fecha_utc_iso()
    salida["Fecha"] = salida["Fecha"].dt.strftime("%Y-%m-%d")
    return columnas(salida)


def provisional_diario(inicio: date, fin: date) -> pd.DataFrame:
    parametros = {
        **parametros_historicos(inicio, fin),
        "daily": (
            "temperature_2m_max,temperature_2m_min,"
            "temperature_2m_mean,precipitation_sum"
        ),
    }
    respuesta = base.solicitar_con_reintentos(
        "GET", URL_HIST, params=parametros
    )
    payload = respuesta.json()
    diario = payload.get("daily", {})
    requeridas = {
        "time",
        "temperature_2m_max",
        "temperature_2m_min",
        "precipitation_sum",
    }
    faltantes = requeridas.difference(diario)
    if faltantes:
        raise ValueError(
            "Faltan variables diarias en ECMWF histórico: "
            + ", ".join(sorted(faltantes))
        )

    tmax = pd.to_numeric(
        pd.Series(diario["temperature_2m_max"]), errors="coerce"
    )
    tmin = pd.to_numeric(
        pd.Series(diario["temperature_2m_min"]), errors="coerce"
    )
    if "temperature_2m_mean" in diario:
        tmedia = pd.to_numeric(
            pd.Series(diario["temperature_2m_mean"]), errors="coerce"
        )
    else:
        tmedia = (tmax + tmin) / 2.0
    prec = pd.to_numeric(
        pd.Series(diario["precipitation_sum"]), errors="coerce"
    )

    return normalizar_provisional(
        pd.DataFrame(
            {
                "Fecha": diario["time"],
                "TMAX": tmax,
                "TMIN": tmin,
                "TMEDIA": tmedia,
                "Prec": prec,
            }
        ),
        payload,
        inicio,
        fin,
    )


def provisional_horario(inicio: date, fin: date) -> pd.DataFrame:
    parametros = {
        **parametros_historicos(inicio, fin),
        "hourly": "temperature_2m,precipitation",
    }
    respuesta = base.solicitar_con_reintentos(
        "GET", URL_HIST, params=parametros
    )
    payload = respuesta.json()
    horario = payload.get("hourly", {})
    requeridas = {"time", "temperature_2m", "precipitation"}
    faltantes = requeridas.difference(horario)
    if faltantes:
        raise ValueError(
            "Faltan variables horarias en ECMWF histórico: "
            + ", ".join(sorted(faltantes))
        )

    datos = pd.DataFrame(
        {
            "Hora": pd.to_datetime(horario["time"], errors="coerce"),
            "Temp": pd.to_numeric(
                pd.Series(horario["temperature_2m"]), errors="coerce"
            ),
            "Prec_h": pd.to_numeric(
                pd.Series(horario["precipitation"]), errors="coerce"
            ),
        }
    ).dropna(subset=["Hora"])
    datos["Fecha"] = datos["Hora"].dt.normalize()

    diario = datos.groupby("Fecha", as_index=False).agg(
        TMAX=("Temp", "max"),
        TMIN=("Temp", "min"),
        TMEDIA=("Temp", "mean"),
        Prec=("Prec_h", "sum"),
        Horas_T=("Temp", "count"),
        Horas_P=("Prec_h", "count"),
    )
    diario = diario.loc[
        (diario["Horas_T"] == HORAS_DIA)
        & (diario["Horas_P"] == HORAS_DIA)
    ].drop(columns=["Horas_T", "Horas_P"])

    return normalizar_provisional(diario, payload, inicio, fin)


def cargar_provisional(inicio: date, fin: date) -> pd.DataFrame:
    print(f"🧩 ECMWF provisional: {inicio} a {fin}")
    try:
        return provisional_diario(inicio, fin)
    except Exception as error:
        print(f"⚠️ Reintento horario: {error}")
        return provisional_horario(inicio, fin)


def mapear_miembros(
    horario: dict[str, Any],
    variable: str,
) -> dict[str, str]:
    patron = re.compile(
        rf"^{re.escape(variable)}(?:_member(\d+))?$"
    )
    salida: dict[str, str] = {}
    for clave, valores in horario.items():
        coincidencia = patron.match(clave)
        if coincidencia and isinstance(valores, list):
            identificador = (
                "control"
                if coincidencia.group(1) is None
                else f"member{int(coincidencia.group(1)):03d}"
            )
            salida[identificador] = clave
    return salida


def procesar_ensamble(payload: dict[str, Any]) -> pd.DataFrame:
    horario = payload.get("hourly", {})
    tiempos = pd.Series(
        pd.to_datetime(horario.get("time", []), errors="coerce")
    )
    if tiempos.empty or tiempos.isna().any():
        raise ValueError("ECMWF ENS no contiene fechas horarias válidas.")

    temp_miembros = mapear_miembros(horario, "temperature_2m")
    prec_miembros = mapear_miembros(horario, "precipitation")
    comunes = sorted(set(temp_miembros).intersection(prec_miembros))
    requeridos = max(
        MIN_MIEMBROS,
        math.ceil(len(comunes) * FRACCION_MINIMA),
    )
    if len(comunes) < requeridos:
        raise ValueError(
            f"Solo hay {len(comunes)} miembros emparejados; "
            f"se requieren {requeridos}."
        )

    diarios: list[pd.DataFrame] = []
    for identificador in comunes:
        temperatura = pd.to_numeric(
            pd.Series(horario[temp_miembros[identificador]]),
            errors="coerce",
        )
        precipitacion = pd.to_numeric(
            pd.Series(horario[prec_miembros[identificador]]),
            errors="coerce",
        )
        if (
            len(temperatura) != len(tiempos)
            or len(precipitacion) != len(tiempos)
        ):
            continue

        miembro = pd.DataFrame(
            {
                "Hora": tiempos,
                "Temp": temperatura,
                "Prec_h": precipitacion,
            }
        )
        miembro["Fecha"] = miembro["Hora"].dt.normalize()
        diario = miembro.groupby("Fecha", as_index=False).agg(
            TMAX=("Temp", "max"),
            TMIN=("Temp", "min"),
            TMEDIA=("Temp", "mean"),
            Prec=("Prec_h", "sum"),
            Horas_T=("Temp", "count"),
            Horas_P=("Prec_h", "count"),
        )
        valido = (
            (diario["Horas_T"] == HORAS_DIA)
            & (diario["Horas_P"] == HORAS_DIA)
            & diario[["TMAX", "TMIN", "TMEDIA", "Prec"]]
            .notna()
            .all(axis=1)
            & (diario["TMAX"] >= diario["TMIN"])
            & (diario["Prec"] >= 0)
        )
        diario = diario.loc[
            valido, ["Fecha", "TMAX", "TMIN", "TMEDIA", "Prec"]
        ]
        diario["miembro"] = identificador
        diarios.append(diario)

    todos = (
        pd.concat(diarios, ignore_index=True)
        if diarios
        else pd.DataFrame()
    )
    if todos.empty:
        raise ValueError("Ningún miembro produjo días válidos.")

    registros: list[dict[str, Any]] = []
    for fecha, grupo in todos.groupby("Fecha"):
        cantidad = grupo["miembro"].nunique()
        if cantidad < requeridos:
            raise ValueError(
                f"{pd.Timestamp(fecha).date()}: "
                f"{cantidad} miembros válidos; se requieren {requeridos}."
            )

        series = {
            variable: grupo[variable]
            for variable in ("TMAX", "TMIN", "TMEDIA", "Prec")
        }
        p50 = {
            variable: float(serie.quantile(0.50))
            for variable, serie in series.items()
        }
        registro: dict[str, Any] = {
            "Fecha": pd.Timestamp(fecha).strftime("%Y-%m-%d"),
            **p50,
        }
        for variable, serie in series.items():
            registro[f"{variable}_Media_Ens"] = float(serie.mean())
            registro[f"{variable}_P10"] = float(serie.quantile(0.10))
            registro[f"{variable}_P50"] = p50[variable]
            registro[f"{variable}_P90"] = float(serie.quantile(0.90))

        precipitacion = series["Prec"]
        registro.update(
            {
                "Prob_Prec_ge_1mm": float(
                    (precipitacion >= 1).mean() * 100
                ),
                "Prob_Prec_ge_5mm": float(
                    (precipitacion >= 5).mean() * 100
                ),
                "Prob_Prec_ge_10mm": float(
                    (precipitacion >= 10).mean() * 100
                ),
                "Prob_Prec_ge_30mm": float(
                    (precipitacion >= 30).mean() * 100
                ),
                "GD_Tb2": max(0.0, p50["TMEDIA"] - base.TBASE),
                "Fuente": "ECMWF_IFS_ENS_025",
                "TipoDato": "Pronostico",
                "CalidadDato": "Mediana_ensamble_P50",
                "N_miembros": int(cantidad),
                "Latitud_grilla": payload.get("latitude", np.nan),
                "Longitud_grilla": payload.get("longitude", np.nan),
                "Elevacion_grilla_m": payload.get("elevation", np.nan),
                "Emision_UTC": base.fecha_utc_iso(),
            }
        )
        registros.append(registro)

    return (
        columnas(pd.DataFrame(registros))
        .sort_values("Fecha")
        .reset_index(drop=True)
    )


def cargar_ensamble() -> pd.DataFrame:
    if base.hoy_argentina() > base.CAMPANIA_END:
        return pd.DataFrame(columns=COLUMNAS)
    payload = base.consultar_ecmwf_ens()
    pronostico = procesar_ensamble(payload)
    pronostico = pronostico.loc[
        pd.to_datetime(pronostico["Fecha"]).dt.date <= base.CAMPANIA_END
    ].copy()
    base.DIRECTORIO_PRONOSTICOS.mkdir(parents=True, exist_ok=True)
    marca = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archivo = (
        base.DIRECTORIO_PRONOSTICOS
        / f"ecmwf_ifs_ens_025_pergamino_{marca}.csv"
    )
    base.escribir_csv_atomico(pronostico, archivo)
    return pronostico


def huecos(
    df: pd.DataFrame,
    inicio: date,
    fin: date,
) -> list[str]:
    esperadas = pd.date_range(inicio, fin, freq="D")
    presentes = pd.DatetimeIndex(
        pd.to_datetime(df["Fecha"], errors="coerce").dropna()
    ).normalize()
    return list(
        esperadas.difference(presentes).strftime("%Y-%m-%d")
    )


def validar(df: pd.DataFrame, fin: date) -> None:
    if df.empty:
        raise ValueError("La serie meteorológica final está vacía.")

    fechas = pd.to_datetime(df["Fecha"], errors="coerce")
    if fechas.isna().any():
        raise ValueError("Hay fechas inválidas en la serie final.")
    if (fechas.dt.date > base.CAMPANIA_END).any():
        raise ValueError("Hay fechas posteriores al cierre de campaña.")
    if fechas.duplicated().any():
        duplicadas = (
            fechas[fechas.duplicated()]
            .dt.strftime("%Y-%m-%d")
            .tolist()
        )
        raise ValueError(
            "Hay fechas duplicadas: " + resumen(duplicadas)
        )

    criticas = df[["TMAX", "TMIN", "TMEDIA", "Prec"]].apply(
        pd.to_numeric, errors="coerce"
    )
    nulas = criticas.isna().any(axis=1)
    if nulas.any():
        raise ValueError(
            "Hay datos meteorológicos nulos en: "
            + resumen(
                fechas[nulas].dt.strftime("%Y-%m-%d").tolist()
            )
        )
    if (criticas["TMAX"] < criticas["TMIN"]).any():
        raise ValueError("Hay TMAX menor que TMIN.")
    if (criticas["Prec"] < 0).any():
        raise ValueError("Hay precipitación negativa.")

    faltantes = huecos(df, base.CAMPANIA_START, fin)
    if faltantes:
        raise ValueError(
            "La serie final no es continua; faltan: "
            + resumen(faltantes)
        )

    pronostico = df["TipoDato"].astype(str).eq("Pronostico")
    if not pronostico.any() and base.hoy_argentina() <= base.CAMPANIA_END:
        raise ValueError("No hay filas de pronóstico.")
    for operativo, percentil in (
        ("TMAX", "TMAX_P50"),
        ("TMIN", "TMIN_P50"),
        ("TMEDIA", "TMEDIA_P50"),
        ("Prec", "Prec_P50"),
    ):
        if not np.allclose(
            pd.to_numeric(df.loc[pronostico, operativo]),
            pd.to_numeric(df.loc[pronostico, percentil]),
            atol=1e-9,
            equal_nan=False,
        ):
            raise ValueError(
                f"{operativo} no coincide con {percentil}."
            )

    miembros = pd.to_numeric(
        df.loc[pronostico, "N_miembros"], errors="coerce"
    )
    if miembros.isna().any() or (miembros < MIN_MIEMBROS).any():
        raise ValueError(
            "El pronóstico tiene menos de 30 miembros válidos."
        )


def ejecutar() -> pd.DataFrame:
    hoy = base.hoy_argentina()
    ayer = min(hoy - timedelta(days=1), base.CAMPANIA_END)

    observaciones, estado_siga = base.obtener_siga_dataframe(
        base.CAMPANIA_START, ayer
    )
    (
        observaciones,
        tmedia_derivada,
        observaciones_descartadas,
    ) = depurar_observaciones(observaciones)

    # La caché conserva únicamente observaciones SIGA completas y válidas.
    base.escribir_csv_atomico(observaciones, base.ARCHIVO_SIGA_CACHE)

    faltantes = fechas_faltantes(
        observaciones, base.CAMPANIA_START, ayer
    )
    rangos_faltantes = rangos(faltantes)
    bloques = [
        cargar_provisional(inicio, fin)
        for inicio, fin in rangos_faltantes
    ]
    provisionales = (
        columnas(pd.concat(bloques, ignore_index=True))
        if bloques
        else pd.DataFrame(columns=COLUMNAS)
    )

    pronostico = cargar_ensamble()
    pronostico = pronostico.loc[
        pd.to_datetime(
            pronostico["Fecha"], errors="coerce"
        ).dt.date >= hoy
    ].copy()
    if pronostico.empty and hoy <= base.CAMPANIA_END:
        raise ValueError(
            "ECMWF ENS no devolvió filas desde la fecha actual."
        )

    consolidado = columnas(
        pd.concat(
            [observaciones, provisionales, pronostico],
            ignore_index=True,
        )
    )
    consolidado["Fecha_dt"] = pd.to_datetime(
        consolidado["Fecha"], errors="coerce"
    )
    consolidado["_prioridad"] = (
        consolidado["TipoDato"]
        .map(
            {
                "Observado": 0,
                "Provisional": 1,
                "Pronostico": 2,
            }
        )
        .fillna(9)
    )
    consolidado = (
        consolidado.dropna(subset=["Fecha_dt"])
        .sort_values(["Fecha_dt", "_prioridad"])
        .drop_duplicates("Fecha_dt", keep="first")
        .sort_values("Fecha_dt")
    )

    fin_pronostico = pd.to_datetime(
        pronostico["Fecha"], errors="coerce"
    ).max().date() if not pronostico.empty else base.CAMPANIA_END
    consolidado = consolidado.loc[
        (consolidado["Fecha_dt"].dt.date >= base.CAMPANIA_START)
        & (consolidado["Fecha_dt"].dt.date <= fin_pronostico)
    ].copy()
    consolidado["Fecha"] = consolidado["Fecha_dt"].dt.strftime(
        "%Y-%m-%d"
    )
    consolidado = columnas(
        consolidado.drop(
            columns=["Fecha_dt", "_prioridad"]
        )
    ).reset_index(drop=True)

    validar(consolidado, fin_pronostico)
    base.escribir_csv_atomico(
        consolidado, base.ARCHIVO_MAESTRO_DEFAULT
    )

    estado = {
        "ejecucion_utc": base.fecha_utc_iso(),
        "sitio": "Pergamino",
        "latitud": base.LATITUD,
        "longitud": base.LONGITUD,
        "estacion_siga": "A872814",
        "estado_siga": estado_siga,
        "ultima_observacion_siga": (
            str(observaciones["Fecha"].max())
            if len(observaciones)
            else None
        ),
        "tmedia_siga_derivada": tmedia_derivada,
        "observaciones_siga_descartadas": (
            observaciones_descartadas
        ),
        "huecos_siga": [
            fecha.isoformat() for fecha in faltantes
        ],
        "rangos_provisionales": [
            {
                "inicio": inicio.isoformat(),
                "fin": fin.isoformat(),
            }
            for inicio, fin in rangos_faltantes
        ],
        "fuente_provisional": (
            "ECMWF_IFS_HISTORICO"
            if len(provisionales)
            else None
        ),
        "filas_provisionales": len(provisionales),
        "fecha_fin_campania": base.CAMPANIA_END.isoformat(),
        "fuente_pronostico": "ECMWF_IFS_ENS_025" if len(pronostico) else None,
        "estadistico_operativo": "P50",
        "inicio_pronostico": str(pronostico["Fecha"].min()) if len(pronostico) else None,
        "fin_pronostico": str(pronostico["Fecha"].max()) if len(pronostico) else None,
        "miembros_validos_min": int(
            pd.to_numeric(
                pronostico["N_miembros"], errors="coerce"
            ).min()
        ) if len(pronostico) else None,
        "huecos_finales": huecos(
            consolidado,
            base.CAMPANIA_START,
            fin_pronostico,
        ),
    }
    base.ARCHIVO_ESTADO.parent.mkdir(
        parents=True, exist_ok=True
    )
    base.ARCHIVO_ESTADO.write_text(
        json.dumps(estado, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        f"✅ SIGA={len(observaciones)}; "
        f"provisionales={len(provisionales)}; "
        f"pronóstico={len(pronostico)}"
    )
    return consolidado


if __name__ == "__main__":
    try:
        ejecutar()
    except Exception as error:
        print(
            f"❌ Error: {error}. "
            "No se reemplazó meteo_daily.csv.",
            file=sys.stderr,
        )
        raise SystemExit(1)
