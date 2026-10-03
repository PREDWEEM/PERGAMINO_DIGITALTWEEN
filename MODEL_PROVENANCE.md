# Procedencia científica del gemelo de Pergamino

Este documento separa lo que el gemelo **hereda sin cambios**, lo que **verifica
una prueba automática** y lo que **sólo está evaluado de forma preliminar**.
Las cifras de la sección de validación las reproduce `tests/test_pergamino_validation.py`
con los datos del repositorio.

## Activos originales

| Activo | Origen | Estado |
| --- | --- | --- |
| `models/IW.npy`, `LW.npy`, `bias_IW.npy`, `bias_out.npy` | LOLIUM-PERGA2026, commit `c55e346` | Copia byte a byte (SHA-256 coincidente con el repositorio de origen). No se reentrena la red. |
| `modelo_clusters_k3.pkl` | LOLIUM-PERGA2026 | **No se incluye.** El gemelo no usa clústeres para normalizar. |
| `meteo_daily.csv`, `actualizar_meteo_pergamino*.py`, `postprocesar_prec_p50_pergamino.py` | LOLIUM-PERGA2026, commit `c55e346` (datos del `21bd1d4`) | Copia sin cambios; el workflow de actualización es el mismo. |
| `data/calibration/pergamino_2026_original.xlsx` | `VALIDA.xlsx` de LOLIUM-PERGA2026 | Original conservado; SHA-256 registrado en `pergamino_2026_source.json`. |
| `data/reference/pergamino_2024_{counts,weather,source}` | Curva DIMA 2024 digitalizada y meteorología SIGA A872814 2024 | Incorporados el 03/10/2026; procedencia y SHA-256 en `pergamino_2024_source.json`. |

## Correspondencia del motor

`predweem_twin/core.py` reproduce `simular_emergencia_local` del monolito
(`app_emergenciacombinado_core.py`, commit `c55e346`, termoinhibición 26 °C):

1. Entradas de la ANN: día juliano, TMAX, TMIN, Prec.
2. Choque hídrico: `Prec_3d ≥ 45 mm`, entre JD 26 y 110, con piso `0,75`.
3. Balance hídrico superficial (Wmax 18,81 mm; Ke y modulador térmico por cobertura 80 %), factor sigmoide e interrupción con humedad relativa < 0,20.
4. Recarga (`cummax` de `Prec ≥ Wmax`), termoinhibición (media de 5 días ≥ 26 °C) y latencia (JD ≤ 25).
5. Filtro de primer pico (> 0,20), **desfase fijo de emergencia de 15 días** y segundo filtro de primer pico.

**Verificación:** `tests/test_pergamino_equivalence.py` compara 14 corridas (2 series
meteorológicas × 7 configuraciones: base, sin desfase, desfase −5, termoinhibición
20 °C, cobertura 50 %/Wmax 30, choque 60 mm/techo 0,5 y Kr = 1) contra salidas
del monolito guardadas en `tests/golden/`, con tolerancia absoluta de 1e-9. Las
series son SIGA Pergamino enero–julio de 2024 y la serie operativa 2026.

### Diferencias deliberadas con el gemelo de Tres Arroyos

- **Sin techo decreciente del 15/04** (`decay_enabled=False`): el monolito de Pergamino no lo usa. El código se conserva, desactivado, y sus columnas de auditoría siguen presentes.
- **Con desfase de emergencia** (`lag_emergencia_dias=15`), que el motor de Tres Arroyos no tiene. Se guarda la señal previa en `EMERREL_SIN_LAG`.
- Parámetros propios: cobertura 80 %, choque 45 mm con techo 0,75, latitud −33,9443, longitud −60,5745.
- Una fila con TMAX < TMIN **detiene** la corrida con un error explícito (el monolito la procesaba). El actualizador ya descarta esas filas de SIGA.

## Validación disponible (preliminar)

Métrica: distancia de variación total (TVD) entre la distribución observada de la
emergencia por intervalos y la simulada; 0 % es coincidencia perfecta. Con las réplicas
de otros ensayos el piso de ruido es de unos 16–18 puntos, de modo que diferencias
menores no son interpretables. Motor oficial = termoinhibición 26 °C y desfase 15 d.

| Configuración | TVD 2024 (curva DIMA, quincenal) | TVD 2026 (10 conteos) | Primer flujo modelado 2024 / 2026 |
| --- | --- | --- | --- |
| **Oficial (26 °C, desfase 15 d)** | 30,5 % | 47,8 % | 07/03 / 05/03 |
| 26 °C, sin desfase | 15,3 % | 58,0 % | 21/02 / 18/02 |
| 20 °C, desfase 15 d (valor previo de la interfaz) | 74,4 % | 58,2 % | 26/04 / 06/04 |
| 20 °C, sin desfase | 60,1 % | 64,8 % | 11/04 / 22/03 |

- La curva 2024 (`data/reference/pergamino_2024_counts.csv`) es una digitalización de la curva de
  Pergamino del Boletín 3 de la Red DIMA, estimada a mano a partir de una captura: un valor por quincena,
  en unidades relativas (eje 0–100), no plantas/m², con incertidumbre de digitalización no cuantificada.
  No es un conteo propio.
- Elevar la termoinhibición de 20 a 26 °C mejora ambas campañas (PR #10 de LOLIUM-PERGA2026).
- **El desfase de 15 días no mejora de forma consistente:** reduce el error en 2026 (58,0 → 47,8 %) y lo empeora en 2024 (15,3 → 30,5 %). Es un corrimiento empírico, no un mecanismo.
- **2026, hitos (25 / 50 / 75 %):** modelo 30/03, 11/04, 01/05; observado (por fecha de visita) 22/04, 21/05, 17/06. El modelo se adelanta entre 3 y 7 semanas y concentra 74 % de su flujo en abril–mayo y menos de 1 % entre junio y el 3 de julio, mientras que en el campo los intervalos que terminan entre el 01/06 y el 03/07 aportaron 43 % de las plantas contadas. **Es el principal error conocido del motor.**
- El motor modela un primer flujo el 05/03/2026, pero según el responsable del ensayo no hubo emergencia en febrero ni marzo. La alerta de inicio y el tiempo térmico desde el primer pico heredan ese adelanto.

No hay validación independiente fuera de estas dos campañas. Las dos se usaron para evaluar el umbral de termoinhibición; este repositorio no documenta con qué datos se fijó el desfase de 15 días, que ya figuraba en el motor de origen.

## Referencia estacional

Dos campañas, con igual peso y cada una normalizada por su propio total:

- **2024** (`data/reference/pergamino_2024_counts.csv`): curva DIMA digitalizada, 12 quincenas de febrero a julio más un cero convencional el 31/01. Valores relativos; disponible desde el 31/07/2024.
- **2026** (`data/calibration/pergamino_2026_counts.csv`): 10 conteos del 13/04 al 03/07, más un cero convencional el 01/04 que delimita el primer intervalo según la indicación del responsable (no hubo emergencia en febrero ni marzo). Se asume que `PLM2` es la emergencia del intervalo (plantas/m²); no hay repeticiones. Disponible desde el 03/07/2026.
- Una campaña entra en la referencia **desde su último conteo**. Hasta el 02/07/2026 la referencia es **sólo 2024**; desde el 03/07/2026 son 2024 y 2026. Antes del 31/07/2024 no hay referencia y el porcentaje figura como «aún no estimable».
- **Consecuencia para 2026:** las dos campañas tienen un timing muy distinto (2024: febrero–mayo, 91 % del progreso al día 120; 2026: abril–julio, 45 % al 30/04). Con la referencia 2024 sola, el porcentaje acumulado de 2026 antes del 03/07 sale muy sobreestimado (motor oficial: 91,5 % el 30/04 y 99,2 % el 21/05). Desde julio, el pool de dos curvas da una referencia intermedia que no representa a ninguna de las dos. Con dos campañas, P10 y P90 no miden variabilidad.
- La referencia se actualiza sola al agregar `data/reference/pergamino_<año>_counts.csv` (cada archivo debe comenzar con un cero que delimite el primer intervalo; si existe `pergamino_<año>_source.json` con el SHA-256 de los conteos, se valida al cargar).
- No se usa la referencia de clústeres de Tres Arroyos ni de otras localidades.

## Calibración local 2026 (experimental, desactivada por defecto)

`scripts/calibrate_site.py` ajusta una transformación monótona `G(F)` (desplazamiento y
pendiente) a los 10 intervalos de 2026. Resultado: desplazamiento −1,15, pendiente 0,6
(**en el límite inferior permitido**). RMSE de ajuste 409 → 305 plantas/m² por intervalo;
en cuatro intervalos posteriores evaluados con ajustes previos, el calibrado queda
más cerca del observado en los cuatro, pero esa mejora ocurre porque la base simula
casi cero en junio y la transformación sólo redistribuye el progreso. No corrige el
adelanto estructural y no es una validación independiente (`independent_season: false`).
Por eso la interfaz arranca con el interruptor desactivado.

El progreso `F` del ajuste usa el total simulado de la ventana muestreada con
meteorología realizada; en operación se estima con la referencia local. El efecto
de ese cambio de denominador no está evaluado.

## Meteorología

- Observaciones SIGA–INTA Pergamino (estación `A872814`), puente provisional ECMWF IFS histórico para huecos y pronóstico ECMWF IFS ENS 0,25° (P50 operativo), como en LOLIUM-PERGA2026.
- La campaña operativa termina el 01/10/2026 (`campaign.py` y `CAMPANIA_END` en el actualizador). **Para una campaña nueva hay que actualizar ambas constantes** y agregar los conteos de la campaña anterior como referencia.
- La serie de calibración 2026 tiene 10 días provisionales (01–04/01, 06/01 y 27/09–01/10).

## Qué no está verificado

- La batería completa (180 pruebas, incluidas las de gráficos y la recarga en caliente con `streamlit.testing`) pasa en GitHub Actions con las dependencias de `requirements.txt` (pull request #1). No hubo ejecución en un navegador: no se comprobó el aspecto visual de la interfaz ni el despliegue en Streamlit Community Cloud.
- Las unidades de la curva 2024 y su digitalización quincenal no están verificadas contra los datos originales de la Red DIMA.
