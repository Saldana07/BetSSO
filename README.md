# Football Value Betting — Motor de "Apuesta Segura" Diaria

## Estructura del proyecto

```
backend/
  app/            FastAPI: main.py, models.py (SQLAlchemy = esquema DB), schemas.py, api/
  ml/
    features/     elo.py, poisson_xg.py, form.py
    training/     train_classifier.py (XGBoost 1X2), train_goals_regressor.py (LightGBM goles)
    value_betting/ ev_kelly.py (EV + Kelly fraccional)
    backtesting/   backtest.py (gate obligatorio de producción)
    ingestion/     (pendiente: conectores API-Football / football-data.co.uk)
  jobs/           generate_daily_pick.py (job diario)
  sql/schema.sql  DDL puro (referencia / init de docker-compose)
docker-compose.yml
```

## Levantar el entorno

```bash
docker compose up -d db
cp backend/.env.example backend/.env
pip install -r backend/requirements.txt
uvicorn app.main:app --reload --app-dir backend
```

La API queda en `http://localhost:8000`, con `/api/predictions/daily-pick` y `/api/predictions/daily-candidates`.

## Estrategia contra el overfitting

El riesgo de overfitting es el problema #1 en modelos deportivos: es muy fácil
lograr 65% de "accuracy" en backtest por fuga de datos o suerte estadística, y
perder dinero real en producción. Se aplican estas capas de defensa:

1. **Validación temporal (walk-forward), nunca k-fold aleatorio.**
   `TimeSeriesSplit` en [train_classifier.py](backend/ml/training/train_classifier.py)
   asegura que el modelo nunca se entrena con partidos "futuros" respecto a los
   de validación. Un k-fold aleatorio filtraría información de la temporada
   completa (incluyendo forma post-partido) hacia el pasado.

2. **Prevención estricta de fuga de datos (leakage).**
   - Solo se usan cuotas **de apertura** para features de entrenamiento;
     las cuotas de **cierre** (`snapshot_type='CLOSING'`) se usan únicamente
     para evaluar el backtest, nunca como input del modelo, porque ya
     incorporan información que el mercado descubrió después.
   - Estadísticas post-partido (posesión final, xG final) nunca se usan como
     feature para predecir ese mismo partido — solo alimentan el promedio
     histórico/rodante hacia partidos futuros.

3. **Holdout final fuera de la validación cruzada.**
   El último 15% cronológico del dataset (`train_classifier.py`) se reserva
   para calibración isotónica y queda completamente fuera del entrenamiento
   y de los folds de CV.

4. **Regularización explícita y modelos poco profundos.**
   `max_depth=4` en XGBoost, `num_leaves=16` en LightGBM, más `reg_alpha`/
   `reg_lambda` (L1/L2) y `subsample`/`colsample_bytree` < 1.0. Datasets de
   fútbol son ruidosos (goles son eventos de baja frecuencia) — modelos muy
   profundos memorizan ruido, no señal.

5. **Early stopping** sobre el fold de validación de cada partición temporal,
   evitando que el modelo siga ajustando tras el punto óptimo.

6. **Calibración de probabilidades (isotónica).**
   Un modelo con buen `accuracy`/`log_loss` puede tener probabilidades mal
   calibradas (ej. dice 70% pero acierta 55% de las veces). Como el cálculo
   de EV y Kelly (`ev_kelly.py`) depende directamente de la probabilidad, una
   probabilidad no calibrada invalida toda la lógica de value betting aunque
   el modelo "clasifique bien".

7. **Backtest con gate de producción obligatorio.**
   `backtest.py::passes_production_gate` exige:
   - **Volumen mínimo** (`min_bets_for_significance=100`): pocas apuestas dan
     yields engañosos por varianza.
   - **Yield positivo** sobre datos 100% fuera de muestra.
   - **Drawdown máximo acotado** (<30%): un modelo puede tener yield positivo
     pero con rachas de pérdida inaceptables.
   Ningún modelo se marca `model_version` activo en `Prediction` sin pasar
   este gate.

8. **Ensembling implícito Poisson + ML.**
   `poisson_xg.py` dota de un prior basado en teoría estadística (no solo
   ML puro). Mezclar un modelo paramétrico (Poisson/Dixon-Coles) con modelos
   de árboles (XGBoost/LightGBM) reduce la varianza de un único enfoque y
   actúa como regularizador natural.

9. **Monitoreo de drift post-despliegue.**
   `backtest_results` se recalcula periódicamente con datos nuevos; una caída
   sostenida de `accuracy`/`yield_pct` respecto al backtest original es la
   señal para reentrenar (los patrones tácticos del fútbol cambian por
   temporada — fichajes, cambios de entrenador, reglas).

## Filtro de "apuesta segura" (resumen de la lógica de negocio)

`jobs/generate_daily_pick.py` evalúa **todos** los mercados
(`1X2`, `DOUBLE_CHANCE`, `OU_1.5`, `OU_2.5`, `BTTS`) de los partidos del día,
calcula EV y Kelly fraccional por selección, descarta cuotas fuera de
`[1.50, 2.00]` o con `historical_accuracy` por debajo del umbral, y marca
como `is_pick_of_day=True` el candidato de **mayor EV** que sobrevive el
filtro. El endpoint `/api/predictions/daily-pick` solo lee ese resultado
precomputado — no recalcula el modelo en cada request.

## Ingesta de datos (API-Football)

`ml/ingestion/` implementa el conector real contra API-Football (acceso directo,
no vía RapidAPI — header `x-apisports-key`). Validado end-to-end contra la API
real:

```bash
python -m ml.ingestion.sync_fixtures --date 2026-09-14                    # todas las ligas del día
python -m ml.ingestion.sync_odds --date 2026-09-14 --snapshot-type LIVE
python -m ml.ingestion.sync_injuries --league 39 --season 2024            # ver limitación abajo
```

**Limitaciones confirmadas del plan Free (100 req/día, 10 req/min):**
- `/fixtures?date=X` **sin** filtro de liga funciona con datos reales de HOY
  (todas las ligas disponibles). En cambio, `/fixtures?date=X&league=Y&season=Z`
  (consulta acotada a una liga) está bloqueado para la temporada en curso —
  solo permite temporadas 2022-2024. Por eso `sync_fixtures.py` se usa sin
  `--league` en el plan Free.
- `/odds` funciona igual para la temporada/fecha actual, tanto por fecha como
  por `fixture_id` — confirmado con datos reales (William Hill, Bet365,
  Pinnacle, etc.)
- `/injuries` con filtro de liga+temporada está bloqueado en Free para la
  temporada actual (mismo patrón que fixtures por liga) — el pipeline de
  lesiones (`sync_injuries.py`, columnas `injuries_count`/`key_players_out`)
  queda implementado pero sin datos reales hasta subir de plan.

Un bug real se encontró y corrigió durante las pruebas: en
[mappers.py](backend/ml/ingestion/mappers.py), la variable `market` se
reasignaba dentro del loop de valores de un mismo mercado Over/Under,
causando que solo la primera línea (`Over 1.5`) se guardara y el resto
(`Under 1.5`, `Over/Under 2.5`) se descartara silenciosamente. Confirmado
con un partido real (Deportivo Riestra vs. Lanús, Liga Profesional
Argentina): pasó de 73 a 95 filas de cuotas tras el fix, cubriendo los 12
mercados/selecciones esperados.

## Entrenamiento completo con datos reales (primera corrida end-to-end)

Fuente: histórico de FiveThirtyEight SPI (espejo CC0 en Kaggle — ver sección
anterior), 61,464 partidos reales, 2016-2023, 40 ligas.

```bash
python -m ml.training.build_dataset --output data/processed/training_features.parquet
python -m ml.training.train_classifier --features data/processed/training_features.parquet
python -m ml.training.train_goals_regressor --features data/processed/training_features.parquet
python -m ml.backtesting.run_historical_backtest --features data/processed/training_features.parquet
```

**Resultados reales (no simulados/inventados):**
- Clasificador 1X2: accuracy ~48-50% en validación temporal (vs. 44% de la
  línea base "siempre predecir victoria local"), log loss ~1.02.
- Regresor de goles (LightGBM Poisson): deviance ~1.12-1.17 por fold.
- Backtest (ver advertencia metodológica abajo): 495 apuestas evaluadas,
  accuracy 59.8%, yield +3.86%, drawdown máximo 11.01% → **pasa el gate de
  producción**.

**Advertencia metodológica sobre el backtest:** este dataset no trae cuotas
reales de casas de apuestas, así que `run_historical_backtest.py` simula una
"cuota de mercado" invirtiendo las probabilidades del propio modelo de 538
(`ext_prob_home/draw/away`, guardadas en `matches` durante la importación)
con un margen del 6%, y mide si NUESTRO modelo le gana a esa referencia. El
resultado debe leerse como *"nuestro modelo bate al modelo de 538 antes de
comisión"*, no como ROI esperado contra un mercado real. El backtest contra
cuotas reales de casas de apuestas queda pendiente hasta acumular histórico
vía `ml/ingestion/sync_odds.py`.

**Bugs reales encontrados y corregidos durante esta corrida** (todos por
probar contra datos/librerías reales en vez de asumir que el código
funcionaba):
1. `mappers.py` (turno anterior): variable `market` se reasignaba dentro del
   loop de Over/Under, perdiendo silenciosamente todas las líneas menos la
   primera.
2. `build_dataset.py`: una liga cuyo primer partido procesado terminaba 0-0
   dejaba el promedio de goles de esa liga en exactamente 0.0, y la
   siguiente división por ese promedio (en `team_strengths`) explotaba.
   Corregido con un prior bayesiano simple sobre el promedio de la liga.
3. `train_classifier.py`: `CalibratedClassifierCV(cv="prefit")` fue removido
   en scikit-learn reciente; reemplazado por `FrozenEstimator` (el patrón
   oficial desde sklearn 1.6).
4. `backtest.py`: el bankroll inicial (1.0) era igual al stake por apuesta
   (1.0) — apostando el 100% del capital en cada apuesta, lo que podía volver
   el equity negativo y producía drawdowns sin sentido (>100%, se observó
   302% antes del fix). Corregido separando `starting_bankroll` (100) de
   `stake_unit` (1, es decir 1% del bankroll por apuesta).

## Job diario conectado a los modelos entrenados (primera predicción real)

`ml/serving/predict.py` calcula features "en vivo" (Elo/forma/Poisson
reconstruidos desde el histórico real en DB hasta hoy, con la misma lógica
que `build_dataset.py`) y las combina con los modelos ya entrenados para
producir probabilidades por mercado. `jobs/generate_daily_pick.py` ahora usa
esto en producción (`python -m jobs.generate_daily_pick`).

**Primera corrida real, verificada a través del endpoint HTTP real:**
```
GET /api/predictions/daily-pick
{"match_id":153,"home_team":"Deportivo Riestra","away_team":"Lanus",
 "league":"Liga Profesional Argentina","market":"OU_1.5","selection":"OVER",
 "bookmaker_odds":1.76,"model_probability":0.7489,"expected_value":0.3181,
 "confidence_label":"BAJA"}
```

**Dos bugs más encontrados y corregidos** en esta integración:
1. `BacktestResult` tenía `created_at` en `schema.sql` pero nunca se mapeó en
   `models.py` — el job crasheaba al ordenar por esa columna. Agregado el
   mapeo faltante.
2. Varias casas de apuestas ofrecen la misma selección de un mercado con
   cuotas distintas, pero `predictions` es única por
   `(match, modelo, mercado, selección)` — el job intentaba insertar una fila
   por bookmaker y violaba la restricción única. Corregido para quedarse con
   la **mejor cuota disponible** por selección antes de evaluar EV/Kelly.

**Por qué `confidence_label` da "BAJA" a pesar de EV +31.8%:** el backtest
que persistimos en `backtest_results` solo cubre el mercado `1X2`; `OU_1.5`
todavía no tiene una `historical_accuracy` validada, así que el sistema
correctamente se abstiene de etiquetar esta selección como alta confianza.
Más importante aún: Deportivo Riestra y Lanús son equipos sincronizados hoy
por primera vez desde API-Football, sin historial propio en nuestra base
(Elo arranca en el valor por defecto, `home_elo_diff≈0`) — la predicción es
mecánicamente correcta pero con señal casi nula todavía. Esto es exactamente
la limitación de "cold start" documentada en `ml/serving/predict.py`: hace
falta (a) acumular partidos reales de estos equipos vía `sync_fixtures.py`
día a día, o (b) reconciliar nombres de equipos entre API-Football y el
histórico de FiveThirtyEight para heredar su Elo/forma ya calculados.

## Frontend (React + Vite + Tailwind)

`frontend/` — interfaz real para ver el Pick del Día sin tocar JSON crudo.
Vite hace proxy de `/api` hacia `http://127.0.0.1:8000` (`vite.config.ts`),
así que no hace falta configurar CORS ni URLs hardcodeadas.

```bash
cd frontend
npm install   # solo la primera vez
npm run dev
```

Abre `http://localhost:5173`. Muestra la tarjeta del pick del día (equipos,
mercado, cuota, EV, sugerencia de Kelly, badge de confianza) y una tabla con
el resto de candidatos evaluados ese día. Maneja los tres estados reales:
cargando, sin pick disponible (404 del backend) y error de conexión.
Probado en desktop y mobile (375px).

## Top N picks + filtro de confianza

El sistema pasó de mostrar un único "pick del día" a un **top 5** (uno por
partido, ordenados por EV), con un filtro por nivel de confianza
(`GET /api/predictions/top-picks?min_confidence=ALTA|MEDIA|BAJA`). El job
diario ahora también es **idempotente** (borra las predicciones previas del
día antes de recalcular, así se puede re-correr sin violar la restricción
única cuando las cuotas se mueven).

Se sincronizaron cuotas reales de los 17 partidos SCHEDULED de hoy (no solo
uno), y se extendió `run_historical_backtest.py` para validar también el
mercado `DOUBLE_CHANCE` (derivable de las probabilidades 1X2 de 538) además
de `1X2`.

**Resultado real, honesto:** con las dos accuracies validadas hasta ahora
(1X2: 59.8%, Doble Oportunidad: 59.9%), **ningún mercado llega al 60%
necesario para "ALTA"** — el filtro `ALTA` devuelve correctamente un 404
("no hay picks con esa confianza hoy") en vez de forzar un resultado. Los
mercados `OU_1.5`/`OU_2.5`/`BTTS` ni siquiera tienen accuracy todavía: 538
no provee probabilidades de líneas de gol, así que esos mercados solo se
podrán validar cuando se acumule histórico real de cuotas vía
`sync_odds.py`. Esto es una limitación real de los datos, no un bug.

**Dos bugs más corregidos** en este cambio:
- `sync_odds.py` pedía cuotas de los 163 partidos del día (incluyendo 130 ya
  terminados), desperdiciando cuota diaria de API-Football sin necesidad —
  corregido para filtrar solo partidos `SCHEDULED`.
- Al mapear `BacktestResult.created_at` en el turno anterior, SQLAlchemy
  empezó a enviar `NULL` explícito en el INSERT en vez de dejar que la BD
  aplicara su default — violaba el `NOT NULL`. Corregido con
  `server_default=func.now()`.

## Intento de mejorar la accuracy (resultado honesto: sin mejora significativa)

Se probaron dos mejoras legítimas para intentar que algún mercado supere el
60% de accuracy necesario para "ALTA":

1. **Feature de días de descanso** (`home_rest_days`, `away_rest_days`,
   `rest_days_diff` en [build_dataset.py](backend/ml/training/build_dataset.py)
   y [predict.py](backend/ml/serving/predict.py), calculado igual en
   entrenamiento y en producción). Resultado: accuracy 48.9% vs. 48.8%
   anterior — dentro del ruido, sin mejora real.
2. **Ensamble XGBoost + LightGBM** (promedio de probabilidades). Resultado:
   48.92% vs. 48.88% de XGBoost solo — tampoco mejora real. Se descartó el
   ensamble (no se justifica la complejidad/costo de inferencia extra para
   una diferencia dentro del ruido); se conserva el feature de descanso por
   ser teóricamente sólido y no tener costo, aunque no se pueda medir su
   aporte con este dataset.

**Conclusión honesta:** el techo actual (~48-50% de accuracy en 1X2) es un
problema de **features, no de algoritmo** — ni afinar hiperparámetros ni
combinar modelos lo va a mover mientras la señal de entrada sea la misma
(Elo, Poisson, forma, descanso). Para subir de verdad hace falta información
que hoy no tenemos: alineaciones/lesiones reales (bloqueado en el plan Free
de API-Football), estadísticas de partido (tiros, posesión — no vienen en
el histórico de FiveThirtyEight), o historial cabeza a cabeza específico
entre cada par de equipos. Mover el umbral de "ALTA" artificialmente para
que salga algo sería falsear la etiqueta, así que se mantiene en 60%.

## El bug más grave del proyecto: apostar a ciegas (corregido)

Los picks generados mostraban EV de **+12% a +31.8%**. En un mercado líquido
una ventaja real vive entre +2% y +8%, así que esos números no eran una
señal buena: eran una alarma.

**Diagnóstico con datos duros:**
- El modelo predijo `P(Over 1.5) = 0.749` para Riestra vs Lanús.
- La tasa base global de Over 1.5 en nuestros 61,431 partidos históricos es
  `0.7463`.
- Es decir: **el modelo estaba devolviendo literalmente el promedio de la
  población**, no una estimación de ese partido.
- Confirmado en los 5 picks: todos tenían **0-1 partidos de historial y
  `elo_diff` exactamente 0.0**. El modelo no sabía absolutamente nada de
  esos equipos.

**Por qué era peor que inútil:** al ordenar los picks por EV descendente, el
sistema seleccionaba los partidos donde el mercado MÁS discrepaba de nuestra
ignorancia — es decir, **rankeaba por "dónde es más probable que estemos
equivocados"**. Una anti-ventaja: peor que apostar al azar.

**Tres correcciones:**
1. **Guarda de historial** (`min_team_history_matches=10`): ningún pick sale
   si ambos equipos no tienen historial real suficiente.
2. **Techo de EV plausible** (`max_plausible_ev=0.20`): un EV enorme es casi
   siempre un modelo mal calibrado, no una casa de apuestas equivocada.
3. **Reconciliación de identidades**
   ([reconcile_teams.py](backend/ml/ingestion/reconcile_teams.py)): los
   equipos de API-Football se vinculan con sus equivalentes del histórico de
   538 (normalizando acentos + un mapa de alias para "Inter"/"Internazionale",
   "SC Braga"/"Braga", etc.), para que un partido de hoy herede años de Elo y
   forma en vez de arrancar en 1500. 25 equipos reconciliados en 8 ligas.

**Resultado medible:**

| | Antes | Después |
|---|---|---|
| Picks emitidos | 5 | 1 |
| Historial detrás del pick | 0-1 partidos | 127 y 27 partidos |
| `elo_diff` | 0.0 (sin información) | -45.0 (señal real) |
| EV del mismo partido | +23.6% (ficticio) | +9.4% (plausible) |
| Stake Kelly sugerido | 10.5% del bankroll | 3.6% del bankroll |
| Partidos descartados | 0 | 16 por datos insuficientes |

Menos picks es aquí **más** efectivo: un sistema que se abstiene cuando no
sabe nada es rentable; uno que apuesta sobre ruido y lo etiqueta como +EV
pierde dinero con confianza.

**Advertencia sobre la reconciliación:** el histórico de 538 termina en junio
de 2023, así que el Elo heredado tiene ~3 años de antigüedad (plantillas,
ascensos/descensos). Es mucho mejor que arrancar en 1500, pero no es un Elo
al día; se irá actualizando solo a medida que `sync_fixtures.py` acumule
resultados reales.

## Auditoría: correcciones críticas aplicadas

Una revisión completa encontró cuatro problemas serios, ya corregidos:

**1. No se medía nada.** `predictions` no registraba si un pick ganó o
perdió, así que era imposible responder *"¿esto gana dinero?"* — la única
pregunta que importa. El backtest mide el pasado simulado; esto no existía.
Ahora hay columnas de liquidación (`settled_at`, `is_win`, `profit_loss`),
un job [settle_predictions.py](backend/jobs/settle_predictions.py) que las
llena cuando terminan los partidos, un endpoint `/api/predictions/performance`
y una barra de resultados en el frontend — **con aviso explícito cuando la
muestra es demasiado pequeña** para significar algo.

**2. El pick era inaccionable.** Decía "cuota 1.65" sin decir en qué casa, y
esa cuota es la mejor entre ~9 casas. Ahora `predictions.bookmaker` guarda la
casa y la tarjeta muestra "en Bet365".

**3. Cuotas fantasma.** El job tomaba la cuota más alta entre *todos* los
snapshots históricos, así que podía calcular EV sobre un precio de hace horas
que el mercado ya movió. Ahora `_latest_odds` usa solo la foto más reciente y
descarta cuotas con más de `max_odds_age_hours` (6h) de antigüedad.

**4. Todo el proyecto estaba sin versionar.** Ya está en git, con `.env`
(que contiene la API key real) correctamente excluido.

También se añadieron los primeros tests: la resolución de apuestas
([settlement.py](backend/ml/value_betting/settlement.py)) decide si tu
historial de rentabilidad es real o basura, así que tiene 34 casos cubriendo
los cuatro mercados y fallando ruidosamente ante un mercado desconocido en
vez de inventar un resultado.

### Flujo diario completo

```bash
# 1. Traer resultados de ayer y liquidar los picks emitidos
python -m ml.ingestion.sync_fixtures --date AYER
python -m jobs.settle_predictions

# 2. Preparar hoy
python -m ml.ingestion.sync_fixtures --date HOY
python -m ml.ingestion.reconcile_teams --apply     # imprescindible: sin esto los picks pierden su historial
python -m ml.ingestion.sync_odds --date HOY --snapshot-type LIVE
python -m jobs.generate_daily_pick
```

## Mercado de córners

**Datos.** FiveThirtyEight no trae córners, así que se incorporó un espejo en
Kaggle de football-data.co.uk (`data/raw/club_football/Matches.csv`, licencia
abierta, descargable sin login): **122,111 partidos con córners de 22 ligas
europeas, desde 2000 hasta pocos días atrás** — datos actuales, no de 2023.
Se usa directamente desde el CSV (tercer namespace de nombres, resuelto con
normalización + alias en [corners.py](backend/ml/features/corners.py)).

**Modelo.** Poisson multiplicativo sobre tasas rodantes de córners a favor /
en contra por equipo (últimos 20 partidos), reutilizando la maquinaria de
goles. Total ~ Poisson(λ_local + λ_visitante) → P(Over/Under línea).

**Validación cronológica** ([validate_corners.py](backend/ml/backtesting/validate_corners.py),
3,927 partidos de 2025-2026, sin fuga temporal):

| Versión | Brier vs base | Calibración |
|---|---|---|
| Sin encogimiento | **peor** en las 4 líneas | sobreconfiado: 78% predicho → 67% real; 25% → 39% |
| Con prior bayesiano (20 partidos) | **mejor** en las 4 líneas | 55.7% → 57.1%; 73.6% → 72.3% |

La primera versión ordenaba bien pero escalaba mal: un promedio de 20
partidos es ruidoso y sus extremos regresan a la media. El encogimiento
hacia el promedio de liga lo corrigió — misma técnica que `LEAGUE_PRIOR_WEIGHT`
en goles.

**Solo se apuestan las líneas 8.5 y 9.5.** En 10.5 y 11.5 la accuracy alta
(73%) es puro desbalance de clases (casi siempre sale Under); persistirla
habría hecho que el sistema etiquetara esos picks como alta confianza por
una ilusión estadística. Solo se conservan las líneas donde el modelo supera
la tasa base en Brier *y* en accuracy.

**Liquidación.** Los picks de córners necesitan los córners reales del
partido: `settle_predictions.py` los trae de `/fixtures/statistics` (1
petición por partido apostado, ~0-3/día) y deja pendiente lo que la API aún
no publicó, en vez de inventar un resultado. Solo se ingieren líneas `.5`
porque una línea entera puede empatar y la liquidación no contempla
devoluciones.

**Ventaja honesta:** pequeña. Brier 0.2392 vs 0.2403 (línea 8.5). Los córners
son un mercado difícil y el modelo lo sabe: sus probabilidades se quedan
cerca de la tasa base salvo evidencia fuerte. Si produce +EV real contra las
casas dependerá de que el mercado de córners sea menos eficiente que el de
goles — plausible, pero solo lo dirá el seguimiento de resultados.

## Etiqueta de confianza basada en habilidad real (rediseño)

La etiqueta anterior usaba `accuracy ≥ 0.60` para "ALTA", y eso era
engañoso por dos razones que córners dejó en evidencia:

1. **No descontaba la tasa base.** 60.4% en Over 8.5 córners es ~0
   habilidad (casi siempre sale Over: base 59.9%); 60% en victoria local es
   una ventaja enorme (base 44%). La accuracy cruda no los distingue.
2. **Premiaba el EV alto**, cuando un EV grande es más a menudo un modelo mal
   calibrado que una casa equivocada (la lección del "apostar a ciegas").

Ahora ([confidence.py](backend/ml/value_betting/confidence.py), con tests):

    habilidad = accuracy − base_accuracy

donde `base_accuracy` es lo que acertaría alguien sin información: la clase
mayoritaria en mercados validados por clasificación (córners), o la
probabilidad implícita media de las cuotas tomadas en mercados validados por
apuestas (1X2, doble oportunidad — superarla es literalmente ganarle al
mercado). `backtest_results` guarda ambas; cada pick guarda una foto de la
evidencia (`historical_skill`, `backtest_n_bets`) y la tarjeta la muestra.
Umbrales: MEDIA ≥ +2 pts, ALTA ≥ +5 pts, siempre con ≥100 apuestas de
backtest; el EV solo actúa como piso, nunca sube la etiqueta.

**Lo que la tabla cuenta ahora, comparable entre mercados:**

| Mercado | Accuracy | Base | Habilidad | n |
|---|---|---|---|---|
| 1X2 (local) | 0.591 | 0.573 | **+1.8 pts** | 489 |
| Doble oportunidad | 0.602 | 0.592 | +1.0 | 990 |
| Córners 9.5 | 0.533 | 0.523 | +1.0 | 3,927 |
| Córners 8.5 | 0.604 | 0.599 | +0.6 | 3,927 |

**Ningún mercado alcanza los +2 pts de "MEDIA"**, así que hoy todos los
picks son BAJA. Es la verdad: con n≈500 el error estándar es ~2.2 pts, o sea
que ni el +1.8 de 1X2 es distinguible de cero. La etiqueta vieja llamaba
"MEDIA" al pick de córners de hoy; la nueva dice BAJA y muestra por qué
("+0.6 pts sobre la tasa base"). Los umbrales se fijaron por principio (qué
es una ventaja profesional: 2-5% de yield) y no se bajaron para que el
modelo actual "aprobara" — eso sería el mismo engaño con otro número.

Los backtests ahora se persisten con `--persist` (antes se insertaban a
mano), así que la tabla es reproducible.

## Simulación de Montecarlo (pestaña en el detalle del partido)

`GET /api/predictions/{match_id}/simulation` corre 10,000 simulaciones del
partido ([monte_carlo.py](backend/ml/simulation/monte_carlo.py), 7 tests) y
el frontend las muestra en una pestaña "Simulación Montecarlo" dentro del
detalle de cada pick (clic en la tarjeta), separada de la vista general.

**Por qué no es "una Poisson muestreada 10,000 veces".** Eso daría, salvo
ruido de muestreo (±0.5%), exactamente las mismas probabilidades que ya
calcula `market_probabilities()` en forma cerrada; comparar eso con el modelo
base y llamar "valor" a la diferencia sería una ilusión estadística. Lo que
sí aporta:

- **Incertidumbre sobre la fuerza de los equipos.** Cada corrida muestrea los
  goles esperados de una Gamma centrada en la estimación cuya dispersión
  decrece con el historial (mezcla Gamma-Poisson = Binomial Negativa). Con 5
  partidos de historial, P(local) cae entre **14% y 73%**; con 50, entre 31%
  y 52%. Ese intervalo es lo que la fórmula cerrada no da, y es la
  visualización más honesta del problema del cold start.
- **Distribución completa** de goles totales y marcadores más probables.

**Cómo leer la comparación (y qué NO es valor).** La pestaña muestra tres
estimaciones: Montecarlo, el clasificador XGBoost (un modelo *distinto*: la
Poisson es solo una de sus features) y el mercado sin margen. Que dos modelos
nuestros discrepen mide **incertidumbre**, no valor; el valor solo existe
contra el mercado. La conclusión solo señala "candidato a apuesta de valor"
cuando **ambos** modelos superan al mercado en ≥3 pts *y* coinciden entre sí
(desacuerdo ≤8 pts). Si discrepan, lo dice y no habla de valor aunque uno de
los dos supere al mercado.

Un detalle de implementación que el test descubrió: el primer intervalo de
confianza hacía bootstrap sobre los resultados simulados, lo que solo mide el
ruido de Montecarlo (~0.5%) y no cambiaba con el historial. El intervalo
correcto es la dispersión de P(resultado | λ) *a través de los λ muestreados*.
La semilla es el `match_id`, para que el resultado sea reproducible entre
recargas.

## Pasada de QA y pulido

Revisión completa: 59 tests automatizados, los 9 endpoints (incluidos 404 y
422 con mensajes claros), y la UI en escritorio, móvil (375 px) y modo claro.
Lo que apareció y se corrigió:

**Bug real en el filtro de seguridad de picks.** Exigía `accuracy >= 0.55`,
y eso **rechazaba córners 9.5 (+1.0 pts de habilidad) y aceptaba córners 8.5
(+0.6)**: elegía la línea con menos habilidad solo porque su tasa base era
más alta. Hoy había 4 partidos con cuotas de 9.5 en rango y producía cero
candidatos. Ahora [`is_safe_bet_candidate`](backend/ml/value_betting/ev_kelly.py)
usa habilidad (accuracy − base), igual que la etiqueta de confianza, y
**exige que el mercado tenga backtest** — antes `None` pasaba en silencio y
BTTS / Over 2.5 generaban picks sin ninguna validación (el hueco abierto de
la auditoría). Con tests.

**Piso de EV del 2%.** Un pick con +0.7% de EV no es una recomendación: no
cubre ni el movimiento normal de la línea. Coincide con el piso de "MEDIA".

**Reconciliación persistente.** El equipo canónico hereda el `api_team_id`
(y la liga el `api_league_id`), así que `sync_fixtures` los encuentra directo
y ya no recrea duplicados. Probado: tras fusionar 35 equipos, un re-sync deja
0 por reconciliar y los picks conservan su historial.

**`daily.bat`.** Los 6 pasos del flujo diario (resultados de ayer → liquidar
→ partidos de hoy → reconciliar → cuotas → picks) en un solo doble clic, con
la fecha UTC correcta.

**Detalles de UI.** La tabla de candidatos mostraba "Menos de" sin la línea;
"1 picks"; el fondo seguía haciendo scroll con el modal abierto.

## Backtest contra cuotas reales: el modelo NO le gana al mercado

Este es el resultado más importante del proyecto. Hasta aquí todo backtest
había sido contra un mercado *simulado* (probabilidades de 538 + un margen
inventado). [real_odds_backtest.py](backend/ml/backtesting/real_odds_backtest.py)
usa las **cuotas reales de cierre** de football-data.co.uk sobre **28,512
partidos posteriores al entrenamiento** (julio 2023 → septiembre 2026, 38
ligas), con features calculadas por el mismo `FeatureReplayer` que el
entrenamiento (refactor verificado bit a bit) y el modelo de producción tal
cual, sin reentrenar.

**¿Quién sabe más?** Brier del modelo 0.6143, del mercado 0.5996. Acierto
48.6% vs 50.2%. **El mercado está mejor calibrado: sabe más que nosotros.**

| Mercado | Precio | n | Habilidad | Yield |
|---|---|---|---|---|
| 1X2 local | promedio de casas | 1,645 | −2.6 pts | **−4.6%** |
| 1X2 local | mejor precio | 1,966 | −1.0 | −1.8% |
| 1X2 visitante | promedio | 310 | −6.3 | −11.7% |
| Over 2.5 | promedio | 2,706 | −2.4 | −4.5% |
| Over 2.5 | mejor precio | 3,241 | −0.1 | −0.3% |
| Under 2.5 | promedio | 2,119 | −2.4 | −4.5% |

Las ocho combinaciones tienen yield negativo. Con el mejor precio el modelo
se acerca al empate: **sigue al mercado y pierde exactamente el margen**. No
tiene ventaja; es un modelo que replica la opinión de las casas con más
ruido.

**Lección metodológica:** el backtest simulado daba +3.9% de yield en 1X2;
el real da −4.6%. Ocho puntos de diferencia. Las probabilidades de 538 más
un margen plano son un adversario mucho más débil que una línea de cierre
real — que ya incorpora alineaciones, lesiones, dinero informado y todo lo
que nuestro modelo no ve. Un backtest contra un mercado simulado puede
aprobar un modelo que pierde dinero.

**Consecuencias aplicadas:**
- La evidencia real se persistió y sustituye a la simulada: con habilidad
  negativa, el filtro **ya no emite picks de 1X2 ni Over/Under 2.5**.
- La fila simulada de Doble Oportunidad se retiró: deriva de las mismas
  probabilidades que fallaron y no hay cuotas reales para validarla.
- Los únicos picks que quedan son de **córners**, cuya validación es de
  calibración (no contra cuotas reales de córners, que no tenemos). Su
  habilidad (+0.6 / +1.0 pts) es *menor* que la que 1X2 mostraba contra el
  mercado simulado antes de fallar contra el real. Deben tratarse como
  **paper trading**: la única forma de validarlos es acumular liquidaciones
  reales.

**Lo que esto significa para el proyecto:** la infraestructura es sólida y
honesta — mide, liquida, se abstiene y ahora sabe que no tiene ventaja. Lo
que falta es un modelo con información que el mercado no tenga. Con Elo,
Poisson y forma calculados desde resultados públicos, eso no existe: las
casas ya tienen todo eso y más. Para tener ventaja real haría falta o
información privilegiada (alineaciones antes de que se muevan las líneas,
datos de tracking) o mercados menos eficientes que el 1X2 de ligas grandes.

## Closing Line Value: la pregunta que queda

El backtest real demostró que el modelo no supera la línea de **cierre**.
Queda una pregunta distinta y más justa: ¿supera la línea del **momento en
que apostamos**? Si las cuotas que tomamos por la mañana se mueven
sistemáticamente a nuestro favor antes del partido, hay ventaja aunque el
cierre nos gane — es lo que miden los apostadores profesionales (*Closing
Line Value*), y converge mucho antes que el P&L porque no depende de acertar
partidos, solo de tomar precios que el mercado luego confirma.

**Cómo funciona.** API-Football conserva las cuotas pre-partido tras el
final (su última actualización, ~2 h antes del inicio, es el cierre). Al
liquidar, `settle_predictions.py` persiste ese snapshot como `CLOSING` en
`odds` (el esquema lo contemplaba desde el diseño inicial) y guarda en el
pick la cuota de cierre **de la misma casa** en la que se tomó el precio.
`CLV = cuota tomada / cuota de cierre − 1`. `/performance` reporta CLV medio
y % de picks que batieron el cierre; la barra de resultados lo muestra.

**Por qué misma casa y no "mejor precio".** El pick de ayer se tomó a 1.65
(mejor precio); al cierre Bet365 y Marathonbet bajaron a 1.57-1.58, pero
Betano seguía en 1.65. Mejor-contra-mejor daría CLV 0% y ocultaría que el
mercado sí se movió a favor. Una casa rezagada no debe enmascarar al
consenso; la comparación limpia es "mi precio en X contra el cierre de X".
(Ese pick, anterior a esta métrica, no tiene casa registrada y usa el
respaldo mejor-contra-mejor: por eso muestra 0%.)

**Qué esperar.** Nada concluyente antes de ~50-100 picks con cierre. Si el
CLV medio se asienta en positivo, hay ventaja real que el cierre absorbe y
conviene apostar más temprano; si es negativo o cero, el modelo no aporta
nada sobre el mercado en ningún momento del día, y esa será la respuesta
definitiva. `daily.bat` lo acumula solo.

## Automatización: el flujo corre solo

**El incidente.** Dos días sin ejecutar `daily.bat` y la app "no funcionaba":
los picks del día 15 seguían como `SCHEDULED` (nadie trajo sus resultados,
así que nunca se liquidaron ni entraron al % de acierto) y los días 16 y 17
no tenían ni un partido sincronizado. El código estaba bien; el flujo
dependía de una persona.

**Dos restricciones del plan Free que lo agravan:**
- `/fixtures?date=` solo acepta fechas en una ventana de **±1 día**. Si el
  flujo se salta un día, los resultados de esa fecha quedan fuera de la
  ventana para siempre… por fecha. `/fixtures?id=` no tiene esa restricción:
  [sync_pending_results.py](backend/jobs/sync_pending_results.py) recupera
  cada partido con picks pendientes **por ID** (1 petición por partido, no
  200 por día), sin importar cuántos días hayan pasado.
- Como consecuencia, la captura del cierre y la liquidación también dependen
  de correr el flujo; ahora son resistentes a días saltados.

**La solución de fondo:** una tarea programada de Windows
(`Apuesta Segura - flujo diario`) ejecuta `daily.bat auto` todos los días a
las 08:00 y, si el PC estaba apagado, **al encenderlo** (`StartWhenAvailable`).
`daily.bat` arranca Docker si hace falta, se pone al día con los picks
pendientes, liquida, captura cierres, trae los partidos de hoy y genera
picks. El registro queda en `backend/daily.log`. Para quitarla:

    Unregister-ScheduledTask -TaskName "Apuesta Segura - flujo diario"

**Sobre los días sin picks.** El 17 de septiembre (jueves) hubo 180 partidos
pero solo 10 por jugarse a la hora del flujo y ninguno con historial
suficiente: cero picks. Es el comportamiento correcto — el sistema prefiere
callarse a recomendar a ciegas — y la UI lo explica así en vez de sugerir
comandos.

**Estado real tras ponerse al día:** 3 picks recomendados liquidados (2G/1P,
+0.56 u), CLV medio **−1.8%**, 0 de 3 batieron el cierre. Con 3 datos es
ruido, pero un pick ilustra por qué el CLV importa más que ganar: Under 9.5
córners se tomó a 1.91, cerró a 2.02 (el mercado se movió *en contra*) y aun
así ganó. Ganar una apuesta que el mercado consideraba peor de lo que
pagamos no es ventaja; es suerte.

## Pendiente (próximos pasos)

- **Backtest contra cuotas reales.** `Matches.csv` trae cuotas históricas de
  cierre (`OddHome/OddDraw/OddAway`, `Over25/Under25`) para 122k partidos.
  Todo backtest hasta ahora fue contra un mercado *simulado* (538 + margen);
  este respondería "¿le gana al mercado de verdad?" con miles de apuestas.
  Es el paso más importante que queda.
- Validar BTTS y Over/Under de goles (hoy no generan picks porque no tienen
  backtest — correcto, pero limita la cobertura).
- Migraciones Alembic; hoy son `ALTER TABLE` a mano y `schema.sql` puede
  divergir de la BD real.
- `elo_history` / `teams.elo_rating` nunca se escriben: el Elo se recalcula
  desde cero en cada predicción (O(n²); hoy ~3 s, se degradará con más ligas).
- Scheduler (APScheduler o Programador de tareas de Windows) para `daily.bat`.
