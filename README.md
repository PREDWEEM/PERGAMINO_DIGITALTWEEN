# PREDWEEM Digital Twin · Pergamino

Gemelo digital de *Lolium multiflorum* para Pergamino, construido sobre el motor de
[PREDWEEM/LOLIUM-PERGA2026](https://github.com/PREDWEEM/LOLIUM-PERGA2026)
(commit `c55e346`, termoinhibición 26 °C). Integra meteorología SIGA + ECMWF,
observaciones por lote, cobertura de rastrojo, escenarios y asimilación de conteos,
con la misma arquitectura que el gemelo de Tres Arroyos
([PREDWEEM/TREASA_digitaltween](https://github.com/PREDWEEM/TREASA_digitaltween)).
La red neuronal se conserva sin cambios.

**PREDWEEM by Guillermo R. Chantre.** Copyright © 2026 Guillermo R. Chantre /
PREDWEEM. Todos los derechos reservados. Consulte [COPYRIGHT.md](COPYRIGHT.md).

> **Léase antes de usar:** el motor de Pergamino adelanta la emergencia de 2026 entre
> 3 y 7 semanas respecto de los conteos y casi no simula el flujo de junio. El
> porcentaje acumulado depende de una referencia de una sola campaña. Detalles y cifras
> en [MODEL_PROVENANCE.md](MODEL_PROVENANCE.md).

## Ejecutar

```bash
python -m pip install -r requirements.txt
streamlit run app.py
python -m pytest -q
```

En Streamlit Community Cloud, seleccione `PREDWEEM/PERGAMINO_DIGITALTWEEN`,
rama `main` y archivo principal `app.py`.

## Qué reproduce y qué cambia

| | Pergamino (este repositorio) |
| --- | --- |
| Motor | `predweem_twin/core.py`, equivalente a `simular_emergencia_local` del monolito (prueba con salidas guardadas, tolerancia 1e-9) |
| Parámetros | cobertura 80 %, Wmax 18,81 mm, termoinhibición 26 °C, choque 45 mm (techo 0,75), latencia JD 25 |
| Desfase de emergencia | 15 días fijos, entre dos filtros de primer pico (`EMERREL_SIN_LAG` conserva la señal previa) |
| Techo decreciente del 15/04 | No se usa (código conservado, desactivado) |
| Meteorología | SIGA–INTA Pergamino `A872814` + puente ECMWF IFS + pronóstico ECMWF ENS P50 |
| Referencia estacional | Campaña local 2026 (10 conteos); disponible desde el 03/07/2026 |
| Calibración local 2026 | Experimental, **desactivada por defecto** |

## Funcionamiento

- **Configuración del gemelo:** lote y fecha, meteorología y cobertura, parámetros.
- **Estado del lote:** emergencia acumulada, flujo diario o semanal, curva base, estado
  actualizado y banda de control 600–800 °Cd desde el primer pico.
- **Porcentaje «aún no estimable»:** antes del 03/07/2026 no hay campaña de referencia;
  la interfaz informa el flujo absoluto, el tiempo térmico y la alerta de inicio, pero
  no un porcentaje. No equivale a emergencia nula.
- **Observaciones:** CSV/XLS/XLSX de flujos (plantas/m²) o emergencia acumulada;
  repeticiones y cobertura opcional. Se guardan por lote en `data/twin_state.db`
  (no versionado; en discos efímeros conserve los originales).
- **Cobertura:** constante o serie FECHA + COBERTURA_PCT interpolada.
- **Escenarios:** cambios exploratorios de lluvia y temperatura sin tocar lo guardado.
- **Trazabilidad:** parámetros, procedencia, asimilación y descarga CSV de la trayectoria diaria.

## Actualización de datos

[`actualizar_meteo.yml`](.github/workflows/actualizar_meteo.yml) corre a las 07:30 y 15:30
(hora de Argentina), descarga SIGA, completa huecos con ECMWF marcados como
`Provisional`, agrega el pronóstico P50 y valida la serie antes de guardar
`meteo_daily.csv`, `data/estado_actualizacion_meteo.json` y el histórico de
pronósticos. Los secretos opcionales `SIGA_PARAMS_JSON`, `SIGA_POST_DATA_JSON` y
`SIGA_HEADERS_JSON` se mantienen como en LOLIUM-PERGA2026.

La campaña meteorológica cierra el **01/10/2026** (`campaign.py` y `CAMPANIA_END` en
`actualizar_meteo_pergamino.py`). Para una campaña nueva actualice ambas constantes; los
conteos de 2026 ya integran la referencia y se suman los de cada campaña nueva
(`data/reference/pergamino_<año>_counts.csv`).

### Mantener activo (opcional)

[`mantener_activo.yml`](.github/workflows/mantener_activo.yml) visita la aplicación cada
cuatro horas para reducir la hibernación de Streamlit. Está inactivo hasta definir la
variable de repositorio `PERGAMINO_APP_URL` con la URL del despliegue
(*Settings → Secrets and variables → Actions → Variables*). No garantiza disponibilidad continua.

## Datos incluidos

| Archivo | Contenido |
| --- | --- |
| `data/calibration/pergamino_2026_counts.csv` | 10 conteos 2026 (plantas/m² por intervalo) más el cero convencional del 01/04 |
| `data/calibration/pergamino_2026_original.xlsx` | `VALIDA.xlsx` original |
| `data/calibration/pergamino_2026_weather.csv` | Meteorología del ajuste (SIGA, 10 días ECMWF provisionales) |
| `data/calibration/pergamino_2026{,_fit,_holdout,_source}.*` | Perfil experimental, ajuste, evaluación temporal y procedencia |
| `tests/golden/` | Salidas del monolito para la prueba de equivalencia |

Para regenerar el perfil experimental: `python scripts/calibrate_site.py`.

## Límites conocidos

- Dos campañas de evaluación (2024 digitalizada de un boletín; 2026 con 10 conteos), sin repeticiones ni validación independiente.
- Adelanto de la emergencia modelada en 2026 y primer flujo el 05/03 pese a que no hubo emergencia en febrero–marzo; la alerta de inicio hereda ese adelanto.
- El desfase de 15 días mejora 2026 pero empeora 2024: es un corrimiento empírico.
- No se verificó el aspecto visual en un navegador ni el despliegue en Streamlit Community Cloud (las 180 pruebas pasan en GitHub Actions).
