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

## Vista "Todos los mercados": lo más favorable de cada partido

Petición: que el sistema cubra todo tipo de apuesta (goles, córners,
ganador…) y muestre lo más favorable de cada partido. El filtro estricto de
picks casi nunca mostraba nada, y eso no sirve para decidir.

**Lo que existe y lo que no.** Se revisaron los 60+ tipos de apuesta del feed
de cuotas: hay ganador, doble oportunidad, todas las líneas de goles,
totales por equipo, hándicap asiático, ambos marcan, marcador exacto,
córners y mercados por mitades. **No hay cuotas de remates ni de tarjetas**
en el feed, así que aunque tenemos 122k partidos con esas estadísticas, no
existe mercado contra el que apostarlas.

**Mercados nuevos (derivados de la misma matriz de marcadores Poisson):**
líneas de goles 0.5–4.5, totales por equipo 0.5–2.5, hándicap asiático en
líneas .5 (−2.5 a +2.5) y marcador exacto hasta 4-4. Todos con liquidación y
tests (106 en total). Solo líneas .5: las enteras y los cuartos implican
devoluciones que `settlement.py` no contempla.

**Dos capas, deliberadamente separadas:**
- **Picks del día** — sigue estricto: mercado validado, cuota en rango, EV
  plausible. Es la recomendación.
- **Todos los mercados** (`/api/predictions/{id}/markets`, pestaña en el
  detalle) — TODO lo que tiene cuota, con probabilidad del modelo,
  implícita, EV y la **validación** de cada mercado: `Validado` (backtest con
  habilidad ≥ 0), `Sin validar` (sin backtest) o `Sin ventaja` (backtest
  real: el modelo pierde ahí). Es análisis, no recomendación, y lo dice.

**"Lo más favorable" no es el EV más alto.** El EV más alto es casi siempre
el mayor error del modelo. El orden es: validados primero, luego sin
validar, luego sin ventaja; dentro de cada grupo por EV; los sospechosos
(EV > techo plausible) al final. Así lo primero de la lista es lo más
favorable *creíble*. Ejemplo real (Rayo–Espanyol): Under 2.5 goles muestra
EV +6.3% pero etiqueta roja "Sin ventaja −2.4 pts" — un EV positivo en un
mercado donde el modelo pierde contra las casas es la trampa que la tabla
hace visible en vez de esconder.

**Además:** lista "Partidos de hoy con análisis" (todo partido con datos,
haya o no picks) y enlace directo `?match=ID` para abrir/compartir un
análisis.

**Bug atrapado al probar:** el hándicap asiático salía con EV +1097%. En
API-Football el número es siempre la línea del **local** y `Away` es el
otro lado de esa misma línea (`Home -0.5` = local gana; `Away -0.5` =
visitante o empate). Yo lo leía como "el visitante da 0.5", invirtiendo el
mercado. El flag de EV sospechoso lo marcó de inmediato — para eso existe —
y el test del mapper ahora fija la semántica correcta.

## "¿Por qué no hay apuestas hoy?": tres causas y un arreglo

El 17 de septiembre (jueves) el sistema no emitió nada. Al investigar:

1. **Hora.** El flujo se corrió a mano a las 18:10 UTC; 162 de 180 partidos
   ya se habían jugado. La tarea automática corre a las 08:00 (13:00 UTC),
   antes del fútbol europeo. No se repite.
2. **Jueves.** Los 10 partidos restantes eran Liga 3 de Georgia, femenino
   nórdico, sub-17, Burundi… Sin historial. Rechazarlos es correcto.
3. **La causa estructural:** hubo **9 partidos de Europa League** con equipos
   que sí tenemos, y los 9 salieron con historial 0. Dos defectos del mismo
   tipo:
   - El historial se calculaba **por pareja equipo-liga**: Ajax tenía 206
     partidos en Eredivisie y 0 en Europa League. Eso descartaba *todas* las
     copas — Champions, Europa League, copas nacionales — justo lo que más
     interesa. Ahora [`compute_pre_match_features`](backend/ml/serving/predict.py)
     replaya la liga del partido **más las ligas domésticas de ambos
     equipos**: cada liga sigue siendo un sistema cerrado (Elo consistente) y
     el equipo se lleva su rating a la copa. El promedio de goles de
     referencia sigue siendo el de la competición del partido, no una mezcla.
     Costo: ~50 ms por partido.
   - La **reconciliación también era por liga**: solo cruzaba equipos dentro
     de ligas mapeadas, así que Hoffenheim (apareció solo vía Europa League)
     nunca se reconcilió. Nueva pasada global para competiciones no mapeadas:
     cruza por nombre contra todo el histórico, prefiere la fila con más
     partidos (la liga doméstica; 538 tiene al mismo club en varias filas) y
     bloquea nombres ambiguos ("Nacional", "Olimpia", "Platense"…). 101
     equipos fusionados, más alias para grafías distintas (`1899 Hoffenheim`
     → `TSG Hoffenheim`, `Red Bull Salzburg` → `FC Salzburg`).

**Resultado:** de los 9 partidos de Europa League, **4 pasan a ser
analizables** (Besiktas–Marseille 196/236, Real Sociedad–Bournemouth
244/170, Plzen–Union SG, Juventus–NEC). Los otros 5 tienen un rival que no
existe en el histórico (Levski, OFI, Ferencváros, Torreense, Lech con 6) y
siguen fuera, correctamente. Para hoy ya era tarde (estaban jugados); desde
mañana las copas entran al análisis.

## Ensayo retrospectivo: "¿cómo le fue a lo que analizó hoy?"

El sistema no emitió picks el 17/09, pero los partidos ya jugados se pueden
analizar *como si* hubiera corrido antes del inicio: los features solo usan
partidos anteriores y API-Football conserva las cuotas pre-partido.
[retro_analysis.py](backend/jobs/retro_analysis.py) hace eso y liquida "lo
más favorable" contra el resultado real. **No cuenta como rendimiento real**
(esos picks no se emitieron a tiempo); es un ensayo.

**Lo que destapó, en orden:**

1. **Cuotas de 1000+** (marcador exacto con favorito a 1.17) desbordaban la
   columna `NUMERIC(6,3)`. Entrada externa → validar en la frontera: se
   descartan cuotas > 500 (`MAX_ODDS`), con test.
2. **Un defecto en el ranking.** La primera corrida dio 5/6 aciertos y
   +2.50 u — pero "lo más favorable" en Wimbledon–MK Dons fue un córners con
   **EV −11.8%**, y en Besiktas–Marseille uno con +0.2%. Salían primeros solo
   porque córners es el único mercado validado, y encima ganaron: suerte
   disfrazada de acierto. Un EV negativo *nunca* es favorable. Ahora el orden
   es: EV real (≥ piso de pick) y no sospechoso primero; dentro de eso por
   credibilidad; lo demás al fondo. Si nada llega al piso, la UI dice
   **"Nada, hoy no"** en vez de destacar la apuesta menos mala.
3. **Un EV sospechoso tampoco puede ser titular**: si el flag dice
   "probablemente error del modelo", no es una oportunidad. Va al fondo.

**Resultado honesto del 17/09** (6 partidos analizables tras el arreglo de
copas): en 4 hubo algo favorable → **3 aciertos de 4, +0.68 u**; en 2
(Besiktas–Marseille, Juventus–NEC) el sistema no habría apostado. El
favorito 1X2 del modelo acertó 3 de 6 — el ~50% que ya conocíamos. Los tres
mercados acertados eran "sin validar" (hándicap, doble oportunidad, totales)
y el fallo fue el hándicap de Vejle–Brondby. Con n=4 es anécdota, no
evidencia; el mismo día, con la versión defectuosa del ranking, la cifra
era 5/6 y habría sido igual de irrelevante.

## "Le doy run pero no muestra apuestas": la automatización que no automatizaba

El 18/09 la app no mostraba nada. Causas, encadenadas:

1. **La tarea programada nunca corrió.** `LastRunTime: 1999`, código
   267011 ("aún no ha corrido"). La ocurrencia de las 08:00 se saltó (PC
   apagado o dormido) y `StartWhenAvailable` no la recuperó.
2. **Cuando por fin corrió, falló con código 1 y sin log.** El clásico de
   `cmd /c "ruta" args >> "log"`: cmd recorta la primera y la última comilla
   del comando y rompe la ruta. Como las rutas del proyecto no tienen
   espacios, la acción va **sin comillas**. Registro reproducible en
   [register_task.ps1](register_task.ps1).
3. **`start.bat` no generaba picks.** Solo levantaba servicios y decía
   "ejecuta daily.bat". Para el usuario, "darle run" es el acceso directo del
   escritorio; si eso no produce picks, la app "no funciona".

**Arreglos:**
- **Disparador al iniciar sesión** (2 min de espera para Docker) además de
  las 08:00. Si el PC estaba apagado, corre al encenderlo.
- **`daily.bat` es idempotente por día**: escribe `backend/.daily_done` con
  la fecha al terminar bien y se salta si ya corrió hoy (`daily.bat auto
  force` lo repite). Así `start.bat` y el disparador de sesión pueden
  llamarlo sin gastar cuota de API de más.
- **`start.bat` ejecuta `daily.bat auto` al final**: darle run = servicios
  arriba + picks del día.
- `start.bat` tenía el mismo bug de batch que rompió `daily.bat` (etiqueta
  dentro de un bloque `( )`); solo "funcionaba" porque Docker ya estaba
  arriba. Corregido. Y el backend ya no arranca con `--reload` (sus workers
  huérfanos sobrevivían al cerrar la ventana).

**Verificado:** la tarea ejecuta con código 0 y escribe el log; una segunda
ejecución el mismo día se salta ("Ya se ejecutó hoy"). El flujo del 18/09
sincronizó 431 partidos, 11 con historial, 5,124 cuotas y **3 picks**
(córners 9.5 en Albacete–Córdoba y Juve Stabia–Cesena, córners 8.5 en
Espanyol–Elche), todos con confianza baja — coherente con la evidencia.

## "Creo que tiene poco acierto": revisión de resultados y la sobreconfianza en córners

**Resultado real a 2026-09-18:** 6 picks liquidados, **2 ganados / 4
perdidos, −2.44 unidades** (yield −41%). Los 4 OVER de córners perdieron; el
único UNDER ganó. Closing Line Value medio **−0.3%**, positivo en 1 de 6: el
mercado no nos dio la razón después, que es la señal más informativa con
tan pocas apuestas.

Seis apuestas no prueban nada por sí solas (con 57% de acierto esperado,
2 de 6 o peor ocurre un 22% de las veces). Así que se buscó la causa en los
datos, no en la racha:

**El modelo de córners no tiene sesgo global pero sí es sobreconfiado en los
extremos**, medido sobre 6,926 partidos de 2025-26 fuera de muestra:

| Predicho (Over 9.5) | Real | n |
|---|---|---|
| 0.60–0.65 | 0.573 | 639 |
| >0.65 | **0.606** | 325 |

Y **los extremos son exactamente donde el sistema apuesta**: el ranking elige
el pick con mayor desacuerdo con la casa, y el mayor desacuerdo está donde
el modelo más se equivoca. Se estaba seleccionando el error del modelo, no
una ventaja (winner's curse). El "EV +13.5%" de Albacete–Córdoba era
principalmente eso.

Esto también responde "¿por qué siempre córners 9.5?": córners es el único
mercado que pasó validación (1X2/goles demostraron no ganarle a la cuota de
cierre), y dentro de córners la línea 9.5 es la que más se aleja de la tasa
base, o sea, la que más EV *aparente* produce.

**Arreglo:** calibración isotónica por línea
([calibrate_corners.py](backend/ml/training/calibrate_corners.py)), ajustada
sobre predicciones walk-forward y evaluada en un holdout posterior antes de
guardarse. Efecto fuera de muestra (ajuste 2024-25, holdout 2025-26):

| Over 9.5 con p cruda ≥ 0.65 | crudo dice | calibrado dice | real |
|---|---|---|---|
| n = 325 | 0.682 | 0.596 | 0.606 |

Brier mejora en ambas líneas. Aplicado a los 5 picks de córners emitidos,
**los 3 de mayor "EV" no habrían salido** (Albacete, Rayo, Juve Stabia; los
tres perdieron); los dos restantes (uno ganado, uno perdido) sí. El
calibrador vive en `models/corners_calibration.joblib` y `CornerModel` lo
aplica si existe; sin el archivo, las probabilidades salen crudas.

De paso, `CornerModel` pasó de filtrar 122k filas por predicción a un índice
por equipo con `searchsorted` (~1000× más rápido, salida bit-idéntica),
lo que hizo viable ajustar y validar sobre 14k partidos.

**Consecuencias honestas.** Con la recalibración, la línea 8.5 ya no supera
la tasa base en accuracy (61.8% vs 61.9%) y queda bloqueada; solo 9.5
conserva habilidad (+4.9 puntos, Brier 0.2467 vs 0.2499). Habrá **menos
picks**, y los que salgan tendrán EV más pequeño y más creíble. Sigue siendo
paper trading: la única prueba pendiente contra las casas es acumular CLV.

## "Siempre me das córners": tiros, tiros a puerta, tarjetas y faltas

La queja era legítima: córners era el único mercado validado, así que el
sistema solo emitía córners. Los mercados de goles (1X2, O/U, BTTS) siguen
bloqueados porque **demostraron no ganarle a la cuota de cierre** (backtest
real sobre 28,512 partidos); abrirlos sería mentir. La salida honesta era
buscar mercados con datos históricos reales, cuotas reales y señal medible.

**Datos.** `Matches.csv` (football-data.co.uk, 122k partidos) trae, además de
córners, tiros, tiros a puerta, faltas, amarillas y rojas de cada partido.
Y API-Football ofrece cuotas de `Total Shots`, `Total ShotOnGoal`, `Cards
Over/Under` y `Fouls. Total` — comprobado en partidos reales: Bet365 y dos
casas más las publican en ligas grandes (tarjetas también en segundas;
faltas solo 1-2 casas).

**Modelo.** El de córners, generalizado: [count_stats.py](backend/ml/features/count_stats.py)
con un `StatSpec` por estadística (columnas del CSV, líneas .5 que se
modelan, prior de liga). Tarjetas = amarillas + 2×rojas, la convención de
las casas. Misma receta: tasas rodantes de 20 partidos, encogimiento
bayesiano, Poisson del total, calibración isotónica por línea
([calibrate_count_stats.py](backend/ml/training/calibrate_count_stats.py)).

**Validación** ([validate_count_stats.py](backend/ml/backtesting/validate_count_stats.py),
2025-26, ~6,900 partidos fuera de muestra, línea central de cada mercado):

| Estadística | Acierto modelo | Tasa base | Brier modelo / base |
|---|---|---|---|
| Córners 9.5 | 55.6% | 50.7% | 0.2467 / 0.2499 |
| **Tiros totales 24.5** | **63.0%** | 51.0% | 0.2234 / 0.2499 |
| Tiros a puerta 8.5 | 57.2% | 52.3% | 0.2440 / 0.2495 |
| Tarjetas 4.5 | 60.1% | 56.5% | 0.2391 / 0.2457 |
| **Faltas 25.5** | **69.5%** | 59.5% | 0.2029 / 0.2410 |

Tiros y faltas dependen del estilo de juego (y del árbitro), mucho más
persistente que dónde cae un córner. Solo se persisten en `backtest_results`
las líneas que superan la tasa base en Brier **y** accuracy (26 de 30);
las demás quedan `SIN_VALIDAR` y no se apuestan.

**La advertencia que importa.** Ganarle a la tasa base de una línea fija no
es ganarle a Bet365, que también conoce estas tendencias y centra su línea
en cada partido (por eso ofrece 24.5 en un partido y 26.5 en otro). La
única prueba contra la casa es el CLV acumulado, exactamente como con
córners. La etiqueta "confianza ALTA" mide habilidad sobre la tasa base,
no sobre la casa.

**Liquidación.** `settle_predictions` trae de `/fixtures/statistics` (1
petición por partido apostado) todas las estadísticas a la vez y las guarda
en `matches` (`home_shots`, `home_shots_on_target`, `home_yellow`,
`home_red`, `home_fouls` y sus `away_*`). Un pick cuya estadística aún no
está publicada se deja pendiente, no se inventa.

## Ayer / Hoy / Mañana

La app tiene un selector de día. **Mañana** se genera como adelanto con las
cuotas de hoy (las casas abren 1-3 días antes) y se regenera al día
siguiente con las cuotas frescas; el flujo diario pasó de 6 a 8 pasos
(`daily.bat`: partidos, cuotas y picks también de mañana; la API permite
`/fixtures?date=` a ±1 día). **Ayer** muestra lo que el sistema recomendó
y cómo terminó: acertó/falló, unidades, marcador, estadística real (7
córners, 23 tiros...) y cuota de cierre. Los endpoints `top-picks`,
`matches/today` y `daily-candidates` aceptan `?day=YYYY-MM-DD` (UTC); el
job `generate_daily_pick` acepta `--date`.

Consumo de API: ~11 (hoy) + ~36 (mañana) peticiones de cuotas + partidos y
liquidación; cabe en las 100 diarias del plan gratuito, con margen.

## Revisión externa: qué era cierto, qué no, y qué cambió

Se contrastó punto por punto una revisión de la lógica contra el código.

**Bugs reales, corregidos:**

1. **Fuga en la calibración.** `train_classifier` hacía el CV temporal
   sobre el 100% del dataset y luego calibraba (isotónica) sobre el último
   15%; el último fold del `TimeSeriesSplit` ya había usado ese tramo para
   el early stopping. Mismo patrón en el regresor de goles. Ahora el holdout
   se separa ANTES del CV (`HOLDOUT_SPLIT`), y el backtest usa el mismo
   corte. Efecto medido: la "habilidad" del 1X2 en el backtest simulado pasó
   de +1.8 pts / yield +3.0% a **+0.1 pts / +0.2%** — parte de lo poco que
   veíamos era la fuga.
2. **Orden por liga en el replay.** `build_dataset` ordenaba por
   `(league_id, kickoff)`; como el estado Elo/forma es por equipo y viaja
   entre competiciones, un equipo llegaba a su partido de copa de 2017 con
   el Elo de su liga de 2023. Ahora orden cronológico global, igual que en
   predicción. Dataset y modelos regenerados.
3. **Hándicap ±0.5 ≠ 1X2.** Son el mismo evento físico pero salían de dos
   modelos (Poisson vs XGBoost) con EVs distintos. Ahora `AH_±0.5` se deriva
   del clasificador, como el 1X2 y la doble oportunidad.

**Cierto en teoría, verificado con datos antes de tocar:**

4. **Dixon-Coles.** Implementado (`scoreline_matrix(..., rho)`) y ajustado
   por máxima verosimilitud ([fit_dixon_coles_rho.py](backend/ml/training/fit_dixon_coles_rho.py),
   52k partidos / 9k holdout). Resultado: rho óptimo −0.04 en
   entrenamiento y **rho = 0 el mejor en holdout**; el −0.13 de los libros
   *empeora* la verosimilitud, porque el regresor LightGBM ya captura la
   dependencia de los marcadores bajos (0-0: 7.06% modelo vs 7.34% real).
   Queda implementado y apagado. Aplicar el valor de libro sin medir habría
   sido un retroceso.
5. **Binomial Negativa en conteos.** La sobredispersión *condicional* a la λ
   del partido es pequeña (α: córners 0.015, tiros 0.013, tarjetas 0.070,
   faltas 0.008). Activada: mejora el Brier en córners y tarjetas (~0.001),
   neutra en el resto. Correcta, no transformadora. Calibradores reajustados.

**Segunda auditoría (mismos hallazgos + tres nuevos):**

8. **Features muertas.** `home/away_injuries_impact` eran 0 en las 61,736
   filas (el plan gratuito no da lesiones). Un árbol nunca parte por una
   constante, así que no "metían ruido" como decía la auditoría, pero eran
   peso muerto: fuera de `FEATURE_COLUMNS` hasta que haya datos.
9. **Lección de paso — la semilla.** Al reentrenar dos veces con los mismos
   datos, el backtest simulado del 1X2 dio +0.1 pts / +0.2% y luego +2.8 pts
   / +4.9%. Solo cambió el azar interno de XGBoost (`subsample` sin
   `random_state`). Es la demostración más clara de que 459 apuestas
   simuladas son ruido. Semilla fijada; las filas simuladas NO se persisten
   como evidencia: el veredicto del 1X2 sigue siendo el del backtest con
   cuotas reales (negativo) y el mercado sigue bloqueado.
10. **Montecarlo desconectado del job.** Cierto, y deliberado por ahora: el
    intervalo epistémico del Montecarlo solo existe para los mercados de
    goles, que están bloqueados por el backtest real. Conectarlo hoy no
    cambiaría ningún pick. Para los mercados de conteo el papel equivalente
    lo hacen el encogimiento bayesiano y la calibración isotónica.

**Cierto pero no urgente / matizado:**

6. Replay O(N) por predicción: real, sigue en pendientes (hoy ~3 s por
   partido).
7. "No se desmargenan las cuotas": el EV usa la cuota real (es lo que se
   cobra) y el CLV compara la misma casa contra sí misma, así que ninguno
   está sesgado. Solo la columna informativa "prob. modelo vs cuota" muestra
   la implícita con margen. El Montecarlo ya desmargena.

## Dos tareas, no una: el flujo diario y la liquidación cada 2 horas

Lo que pasó el 19/09 mostró la fragilidad de depender de una sola corrida:
el flujo de las 07:07 corría *dentro* de la ventana de `start.bat` y murió
al cerrarla; por la tarde `start.bat` y el disparador "al iniciar sesión"
lanzaron **dos flujos en paralelo** que chocaron con el límite de 10
peticiones/minuto (65 s de espera por choque) y gastaron el doble de cuota.
Mientras tanto la app decía "pendiente de liquidar" para partidos ya
terminados, porque la liquidación solo ocurría a la mañana siguiente.

Ahora:

- **`daily.bat`** (tarea "flujo diario", 08:00 y 2 min tras iniciar sesión):
  partidos, cuotas y picks de hoy y mañana. Con **candado** (`.daily_lock`,
  `mkdir` atómico): un segundo flujo se retira sin hacer nada. `start.bat`
  lo lanza en su **propia ventana**, así que cerrar el lanzador no lo mata.
- **`settle.bat`** (tarea "liquidar", **cada 2 horas**): resultados y
  estadísticas de los partidos con picks pendientes, y liquidación. Cuesta
  ~3 peticiones por pick una sola vez, así que cabe en la cuota aunque corra
  12 veces al día. Comparte el candado. Log en `backend/settle.log`.
- El frontend se refresca solo cada 5 minutos.

Registro de ambas: `powershell -ExecutionPolicy Bypass -File register_task.ps1`.

Límite conocido: si un partido ya terminó y la casa retiró el mercado
(pasa con tarjetas/tiros en algunas casas), la cuota de cierre de ese pick
no se puede capturar y queda vacía; el CLV se calcula solo sobre los picks
que sí la tienen.

## Medir contra la casa, no contra el azar (revisión del 22/09)

Toda la validación anterior comparaba el modelo con la **tasa base** (el
azar). Eso no dice si le gana a Bet365, que también conoce las tendencias
y centra su línea en cada partido. Con 3-5 picks al día, medirlo solo con
picks llevaría meses. Tres cambios, medidos antes de hacerlos:

**1. Guardar y liquidar TODOS los mercados evaluados** (`market_evaluations`),
no solo los picks: probabilidad del modelo, mejor cuota y la probabilidad
de las casas **sin su margen** (mediana entre casas con ambos lados:
[market.py](backend/ml/value_betting/market.py); el margen medido en tiros,
tarjetas y córners es ~7%). [model_vs_market.py](backend/ml/value_betting/model_vs_market.py)
compara el Brier de ambos con la unidad correcta — el **partido**, porque
las 10 líneas de tiros de un partido aciertan o fallan juntas — y solo da
veredicto con 30+ partidos y |t| ≥ 2. En la app: tarjeta "Modelo vs casa".
Los partidos jugados desde el 14/09 se evaluaron con la última foto de
cuotas **anterior al inicio** ([backfill_evaluations.py](backend/jobs/backfill_evaluations.py)).
Primer veredicto (51 partidos): **en 1X2 la casa acierta más (t = 2.24)** —
confirma, con datos nuevos, el bloqueo que dictó el backtest con cuotas
reales. Tiros, tarjetas y faltas aún no tienen muestra.

**2. Resultado + estadísticas en una sola petición.** `/fixtures?id=` trae
ambos (antes se pedían por separado). El plan gratuito no permite varios
partidos por petición (`ids`: "Free plans do not have access"), así que
[fetch_match_data.py](backend/jobs/fetch_match_data.py) reparte la cuota
con prioridades (picks > mercados evaluados > partidos de liga para el
histórico) y **reserva 70 peticiones para el flujo de las 08:00** mientras
no haya corrido (`/status` no gasta cuota). Lo que no quepa hoy se trae en
la siguiente pasada de la tarea "liquidar" (cada 2 h).

**3. Histórico al día.** El espejo de Kaggle se publica una vez al mes
(última: 05/09). Simulando 19 días de retraso sobre 6,900 partidos, tiros y
faltas — los mejores mercados — perdían ~0.5-1 punto de acierto. Las
estadísticas traídas en el punto 2 se suman al CSV
([count_stats_overlay.py](backend/ml/features/count_stats_overlay.py)),
pero **solo si la API cuenta igual que football-data**: con ≥ 20 partidos,
el total medio de la API debe estar a ±12% (o 3 errores estándar) del
CSV en las mismas ligas. Si no, esa estadística no se mezcla. Hoy, con
12-15 partidos, no se mezcla ninguna: se activará sola al tener muestra.

**Emparejamiento de nombres.** [TeamResolver](backend/ml/features/count_stats.py):
alias, exacto, sin forma jurídica ("1. FC Magdeburg" → "Magdeburg") y
difuso conservador, con **guarda de país** (un "Nacional" uruguayo no hereda
al portugués; un "Barcelona SC" ecuatoriano no hereda al Barcelona; las
ligas femeninas no heredan al equipo masculino). Cobertura en las ligas
principales cubiertas: de 210/268 a **398/398** equipos, sumando las ligas
reconciliadas con FiveThirtyEight — cuyo "país" era la etiqueta de la
fuente y requirió un mapa propio. De paso apareció un alias roto desde el
inicio: el PSV nunca tuvo predicción de conteos.

**Casa del usuario.** `ALLOWED_BOOKMAKERS` en `backend/.env` (ej.
`Betano,1xBet`) limita la cuota del pick a las casas donde se puede apostar:
el EV solo existe en la casa que da esa cuota, y Bet365 está bloqueada en
Colombia. La probabilidad sin margen sigue usando todas las casas.

## Casas del usuario y nuevos datos: el árbitro (23/09)

**Casas.** El usuario apuesta en Betano, Bwin y Rushbet. En API-Football,
Betano aparece en casi todos los partidos; Bwin está en su catálogo pero no
dio cuotas en ninguno de los 96 partidos sincronizados; Rushbet no está.
`ALLOWED_BOOKMAKERS=Betano,Bwin` en `backend/.env`. Para Rushbet (y Bwin),
cada pick muestra **"vale la pena desde X"** = (1 + EV mínimo) / prob. del
modelo: si esa casa paga X o más, la apuesta vale lo mismo.

**Árbitro para tarjetas** ([referees.py](backend/ml/features/referees.py)).
Nuestro histórico no trae árbitro; API-Football sí, a 1 petición por liga y
temporada (plan gratuito: 2022-2024). Piloto con Premier, LaLiga y Serie A
(3,327 partidos cruzados con el CSV, factor de cada árbitro calculado solo
con sus partidos anteriores, test en 2024-25):

| | Brier sin árbitro | con árbitro |
|---|---|---|
| Tarjetas 3.5 | 0.2272 | 0.2242 |
| Tarjetas 4.5 | 0.2368 | 0.2333 |
| Faltas 25.5 | 0.2101 | 0.2106 |

Mejora las 5 líneas de tarjetas (+1.2-1.4 pts de acierto en 3.5 y 4.5) —
2-3 veces más que Dixon-Coles, Binomial Negativa o Holt-Winters — y no
aporta en faltas, así que solo se aplica a tarjetas. Pero el test pareado
por partido dio **t = −1.8**, por debajo del umbral de −2, así que
[build_referee_factors.py](backend/ml/training/build_referee_factors.py)
guardó la tabla **sin activarla**. [import_referees.py](backend/ml/ingestion/import_referees.py)
baja las 22 divisiones × 3 temporadas (66 peticiones, dentro del
presupuesto de cuota: 2-3 días) y revalida cada vez; con ~7 veces más
partidos de prueba se activará sola si el efecto es real. El árbitro de los
partidos próximos se actualiza al resincronizar (se designa 1-3 días antes).

**Fuentes de datos evaluadas y descartadas por ahora:**
- Más temporadas del mismo tipo: ya hay 122k partidos; el límite no es
  el volumen sino la información que la casa no tiene.
- xG (Understat/FBref): serviría a los mercados de goles, que están
  bloqueados porque la casa acierta más; FBref además prohíbe el scraping.
- Alineaciones: la API las da ~1 h antes del partido (el flujo corre a las
  08:00) y modelar el impacto de cada jugador es un proyecto aparte.
- Plan de pago de API-Football: la palanca más grande (temporada en curso
  por liga, 20 partidos por petición, miles de peticiones al día, más
  ligas con estadísticas, lesiones). Decisión del usuario.

## Solo datos gratuitos: archive.org, cuotas del histórico y árbitros (23/09)

Petición del usuario: "hazlo con toda la información gratis que encuentres".
Cada fuente se probó antes de entrar; lo que no mejoró no se usa.

**1. football-data.co.uk vía Internet Archive** ([import_football_data_archive.py](backend/ml/ingestion/import_football_data_archive.py)).
La fuente original de nuestro histórico está bloqueada en Colombia (Coljuegos),
pero archive.org guarda copias de sus CSV varias veces al mes. Solo se usan
copias ya archivadas. Verificación: en 357 partidos presentes en ambas
fuentes, **4,284 de 4,284 valores idénticos**. Aporta:
- la temporada en curso al día en 17 de 22 divisiones (211 partidos que el
  espejo mensual aún no tenía; se refresca cada mañana);
- el **árbitro** de las 9 ligas inglesas y escocesas desde 2017-18
  (29,412 partidos, incluida la 2025-26 que la API gratis no da);
- tarjetas de la National League 2016-2025, que el espejo no traía.

La fusión no duplica: un partido del archivo es "el mismo" si coinciden
división, fecha y local O visitante. Eso destapó un **error viejo**: el
espejo escribe unas temporadas "Nott'm Forest" y otras "Nottm Forest", y el
historial del Nottingham Forest (y del Gladbach) estaba partido en dos
equipos. `normalize` ya ignora apóstrofos.

**2. Cuotas 1X2 del histórico** ([market_adjustment.py](backend/ml/features/market_adjustment.py)).
Ya estaban en el CSV (Bet365) y en cada partido que sincronizamos. El
desequilibrio favorito/débil explica tarjetas que el modelo por equipos no
ve: partidos parejos se calientan. Prueba en 2025-26 (7,701 partidos):
**tarjetas t = −4.2 → activado** (coef −0.29); tiros, tiros a puerta,
córners y faltas no pasaron → no se usa ahí. Calibración y validación se
rehicieron con el ajuste incluido.

**3. Árbitro** ([build_referee_factors.py](backend/ml/training/build_referee_factors.py)).
Con archivo + API (59 de 66 liga-temporadas ya bajadas): 40,698 partidos,
733 árbitros. Prueba sin fuga en 10,422 partidos de 2024-25 y 2025-26:
Brier 0.2203 → 0.2178, **t = −4.6 → activado** para tarjetas (el piloto de
3 ligas daba t = −1.8 y no se activó; con más datos se confirmó). Se aplica
sobre el total ya ajustado por cuotas.

**Descartadas (probadas o evaluadas):** Holt-Winters/forma reciente (±0.001),
Dixon-Coles con ρ de libro (empeora), cuotas del mercado en tiros/faltas/
córners (ruido), xG para goles (mercado bloqueado porque la casa acierta
más), alineaciones (llegan 1 h antes del partido).

## Apartado de fútbol colombiano (contador propio)

Petición del usuario: fútbol colombiano en un apartado aparte, con su propio
contador, para sacar conclusiones solo de Colombia.

**Fuente (gratis).** Se buscaron alternativas: webs de estadísticas
(MakeYourStats, OddAlerts, AiScore) cuyas condiciones no permiten descarga
automática, y APIs gratuitas como Highlightly (100 consultas/día, requiere
cuenta del usuario). La mejor sigue siendo API-Football, la que ya usamos:
Primera A (239) y Primera B (240) con resultado, árbitro y estadísticas por
partido, y cuotas de 9 casas **incluida Betano** en córners, tarjetas, goles y
ganador (no hay mercados de tiros ni faltas para Colombia).

**Historial con el plan gratis** ([import_colombia.py](backend/ml/ingestion/import_colombia.py)):
- 2022-2024: una petición por liga y temporada (6);
- 2025-2026: el plan no deja pedir esas temporadas, pero sí el historial de
  enfrentamientos (`/fixtures/headtohead`), que trae todas: una petición por
  pareja de equipos de la Primera A actual (~190) cubre todos sus partidos;
- estadísticas: una petición por partido, de lo más reciente hacia atrás
  (prioridad 3 de jobs/fetch_match_data.py, después de picks y evaluaciones).
Todo dentro del presupuesto diario; se completa solo en 1-2 semanas.

**Separación.** Todo lo colombiano lleva `model_version = "col-v1"`
(picks, evaluaciones, validaciones); la API filtra por `scope=europa|colombia`
en picks, candidatos, partidos, rendimiento y "Modelo vs casa". Los picks
colombianos (máx. 3/día) ya no ocupan cupos de los 5 de Europa, y los
contadores de Europa no incluyen Colombia.

**Modelo** ([colombia.py](backend/ml/features/colombia.py)). El mismo modelo
de conteos, entrenado solo con partidos colombianos de la BD (córners y
tarjetas; necesita 10+ partidos con estadísticas por equipo), sin los
ajustes validados en Europa (árbitro, cuotas). Goles: el modelo general con
el historial colombiano. **Sin validación propia todavía**: sus picks salen
como experimentales (`require_validation=False`) y la app lo avisa. Su
contador y su "Modelo vs casa" son precisamente la validación. Cuando haya
muestra: calibrar y validar con datos colombianos y exigir validación como en
Europa.

## Revisión externa de código (23/09): un error real mío, lo demás verificado

- **Crítico y real (introducido al añadir el ajuste de tarjetas por cuotas):**
  en `predict_match_probabilities`, `p_home, p_away = _market_1x2(...)` pisaba
  las probabilidades del clasificador. Sin cuotas 1X2 el endpoint caía
  (`float(None)`); con cuotas, el 1X2 y el hándicap ±0.5 del "modelo" eran los
  de la casa, lo que habría contaminado "Modelo vs casa". Corregido con
  nombres propios. Se comprobó que **ninguna evaluación quedó contaminada**
  (todas eran anteriores al error y el flujo no corrió en medio).
- **Real:** el texto del Montecarlo decía "candidato a apuesta de valor… habilidad
  de ~+1.8 pts" en 1X2; tras corregir la fuga de calibración esa cifra es ~0 y
  la casa acierta más. Ahora remite a la evidencia sin cifras que caduquen.
- **Real:** `.env.example` tenía una variable inexistente y le faltaban todas
  las nuevas; ahora coincide exactamente con `Settings`. Una conexión a la BD
  por partido en el job diario → una sola sesión. Imports duplicados.
- **No aplica:** "faltan índices" (existen en `schema.sql`; la consulta de
  cuotas tarda 0.45 ms con índice); "carrera entre fetch y settle" (ambos
  corren bajo el mismo candado `.daily_lock`); CORS fijo (el navegador habla
  con la API a través del proxy de Vite, mismo origen).
- **Menores, sin cambio:** filtrar confianza en SQL (pocos picks por día),
  `lru_cache` de settings (patrón estándar de FastAPI), tabla de lesiones sin
  datos (el plan gratuito no las da; ya documentado).

## Consultas solo bajo demanda: botones en vez de tareas programadas

**El problema.** Las 100 consultas diarias se iban solas: tres tareas
programadas (flujo diario a las 08:00 y al iniciar sesión, liquidación cada 2
horas, Colombia a las 19:10) más `start.bat`, que lanzaba el flujo completo al
arrancar. Cada pasada importaba además historial (Colombia, árbitros) con lo
que sobrara: el 23/09, 82 de las 100 se fueron en historial colombiano.

**Ahora nada corre solo.** Las tareas se quitaron del Programador y los
archivos que las registraban (`register_task.ps1`, `run_hidden.vbs`,
`colombia_ahora.bat`) se borraron. Lo que gasta cuota son cuatro acciones
(`backend/jobs/on_demand.py`) que la página lanza con botones:

| Botón | Qué hace | Coste |
|---|---|---|
| Verificar resultados | resultado + estadísticas de los partidos con picks, liquidación y cuota de cierre | ~2 por partido con pick |
| Refrescar cuotas | cuotas de más de 3 h de los partidos de las próximas 12 h, y rehace sus picks | 1 por partido |
| Actualizar el día | partidos y cuotas de hoy y mañana y sus picks | ~2 + 1 por partido con historial |
| Completar datos | medición "Modelo vs casa", historial colombiano, árbitros, huecos del histórico | lo que sobre, dejando reserva |

"Actualizar el día" es lo único automático, y solo **al abrir la página**: la
primera visita tras renovarse la cuota (00:00 UTC = 19:00 en Colombia) la
lanza una vez; si falla no se reintenta en cada visita (`auto_attempted` en
`data/actions_state.json`). Cada botón muestra antes cuánto gastaría
(`GET /api/actions/estimates`) y pide confirmación si pasa de 15.

**Cómo corre.** `POST /api/actions/{acción}` lanza `python -m
jobs.on_demand <acción>` en su propio proceso sin ventana y responde al
instante; la página consulta `GET /api/actions/status` mientras corre
(cuota usada según el registro local, paso actual, resultado de la última).
El candado sigue siendo `.daily_lock`; si el proceso muere (PC suspendido),
el estado se limpia solo al comprobar que su PID ya no existe. La salida de
cada paso queda en `backend/actions.log`. `daily.bat` y `settle.bat` quedan
como atajos manuales de "Actualizar el día" y "Verificar resultados"
(arrancan Docker si hace falta).

**Ajustes que evitan gastar de más:** `sync_odds --fresh-hours 3` no vuelve a
pedir cuotas recientes (actualizar dos veces no cuesta el doble) ni pide las
de partidos ya empezados, y guarda lo traído si la cuota se agota a mitad; la
cuota de cierre solo se pide para partidos de los últimos 4 días (antes un
partido sin cuotas en la API se volvía a pedir en cada liquidación);
`fetch_match_data --tiers` separa "tus picks" (sin reserva) del resto.

## Apartado NBA

Pestaña propia, con contador, "Modelo vs casa" y botones propios; nada de lo
de la NBA suma a los contadores de fútbol. Tablas aparte (`nba_teams`,
`nba_games`, `nba_odds`, `nba_predictions`, `nba_evaluations`): el modelo de
fútbol lee `matches` entera y 110 "goles" por equipo le romperían los
promedios. Los picks y evaluaciones repiten las columnas de fútbol para que la
API (`/api/nba/...`) devuelva las mismas respuestas y la página use los mismos
componentes.

**Datos (todos gratis salvo lo marcado):**

| Fuente | Qué da | Coste |
|---|---|---|
| stats.nba.com (`leaguegamelog`) | todos los partidos de una temporada con la estadística de cada equipo, 1 petición | gratis |
| cdn.nba.com (`scheduleLeagueV2`) | calendario de la temporada en curso con hora UTC, estado y marcador | gratis |
| ESPN (API pública de su web) | líneas de apertura y cierre de varias casas por partido desde 2017; líneas de los próximos partidos | gratis |
| API-Basketball (API-Sports) | cuotas de Betano, Pinnacle, Bet365, 1xBet... de ayer/hoy/mañana | misma clave que fútbol, **100 diarias aparte** |

nba.com rechaza peticiones sin cabeceras de navegador (403 o se cuelga):
`ml/nba/sources.py` las manda. En API-Basketball el número del hándicap es
**siempre la línea del local**, también en el lado "Away" (verificado con
Betano: con el visitante favorito a 1.70, "Away +1.5" paga 1.80, así que es
"visitante gana por 2+"), igual que en API-Football. El plan gratis no da
temporadas fuera de 2022-2024 ni fechas fuera de ayer/hoy/mañana: sirve para
las cuotas del día, no para el historial (el historial sale de nba.com y ESPN).

**Modelo (`ml/nba/ratings.py`).** Ataque, defensa y ritmo de cada equipo en
puntos por 100 posesiones, corregidos partido a partido (como un Elo de
puntos), más ventaja de local (que deriva: en la burbuja de 2020 fue ~0) y
castigo por back-to-back. Al empezar temporada los ratings se acercan a la
media. Todos los partidos de un día se predicen antes de actualizar con
cualquiera de ellos (test que lo verifica). Margen y total se tratan como
normales; de ahí salen ganador, hándicap y total (`ml/nba/markets.py`), y al
revés, de la probabilidad sin margen de una casa sale el margen o total que
espera (así se comparan casas con líneas distintas; Pinnacle pesa más).

**Validación (`ml/nba/train.py`), sin tocar lo que se evalúa:** parámetros
ajustados con 2015-16 a 2020-21; el aporte del modelo sobre la línea de
cierre (resultado - línea = w · (modelo - línea)) se mide en 2021-22 a
2023-24 y se confirma en 2024-25 y 2025-26, con apuestas simuladas a las
cuotas reales de cierre y de apertura de ESPN. En vivo, la probabilidad es
línea del mercado + w · (modelo - línea): con w = 0 (el modelo no sabe nada
que la línea no sepa), los picks solo nacen de diferencias de precio, Betano
o Bwin pagando más de lo que el resto del mercado considera justo. El
resultado queda en `models/nba/validation.json` y se muestra en la pestaña.

**Resultado (24/09/2026, 6.591 partidos con línea de cierre):** el modelo
**no le gana a la casa**. Error medio en el margen 10.97 puntos contra 10.56
de la línea; en el total 14.69 contra 14.21; aporte sobre la línea w = -0.02
(t = -0.3) y -0.06 (t = -0.9); en el ganador la casa predice mejor (Brier
0.199 contra 0.208, t = 5.3). Apostando con el modelo solo en 2024-26:
ROI -6.6% a cierre y -3.4% a apertura en ~3.000 apuestas. Por eso w = 0 en
vivo. La compra de precio entre casas daría +6.3% (566 apuestas, 2021-24),
pero las líneas más apartadas del consenso "ganan" aún más (+10.9%): la firma
de líneas viejas en ESPN (cada casa se registra a su hora), así que queda
marcada como no fiable y la decide el contador propio. Para no caer en lo
mismo en vivo, los picks de hándicap y total solo van en líneas a menos de
1.5 puntos de lo que espera el mercado, y el precio se compara con Pinnacle
sin margen en esa misma línea (nunca con la propia casa).

**Operación.** "Actualizar NBA" (gratis: calendario, resultados, líneas de
ESPN, picks y liquidación) se lanza sola al abrir la pestaña si pasaron 3
horas; "Cuotas Betano / Pinnacle" gasta la cuota de baloncesto (1 por día +
1 por partido de las próximas 36 h, sin repetir los de menos de 2 horas). La
primera vez importa el historial (~15.000 partidos y ~6.000 cierres de ESPN,
reanudable) y entrena el modelo. La pretemporada no genera picks.

## Interfaz "Boleto nocturno"

Tablero oscuro (un solo tema) con barra lateral: los tres apartados con su
contador, las consultas de fútbol y de baloncesto del día y "Recargar" (solo
relee la base de datos). Cada pick es un boleto: el partido arriba, una
perforación, y abajo la apuesta con la cuota grande en monoespaciada, la
ventaja (EV) en lima y las probabilidades del modelo y de la cuota lado a
lado. Tipografías: Bricolage Grotesque (títulos), IBM Plex Sans (texto),
IBM Plex Mono (cifras); los colores son tokens en `frontend/src/index.css`.
Las acciones que gastan consultas viven en `useActions.ts` (lo comparten la
barra lateral y la de acciones). En pantallas de menos de 1024 px la barra
lateral pasa a una barra superior con los apartados. Horas y fechas en
formato de Colombia ("7:30 p. m."), y las selecciones nombran al equipo
("Empate o Millonarios").

## Pendiente (próximos pasos)

- **Backtest contra cuotas reales.** `Matches.csv` trae cuotas históricas de
  cierre (`OddHome/OddDraw/OddAway`, `Over25/Under25`) para 122k partidos.
  Todo backtest hasta ahora fue contra un mercado *simulado* (538 + margen);
  este respondería "¿le gana al mercado de verdad?" con miles de apuestas.
  Es el paso más importante que queda.
- BTTS y Over/Under de goles: no generan picks porque el backtest con cuotas
  reales dio yield negativo. Solo entrarían con datos que el mercado no
  pondere bien (alineaciones, xG en vivo).
- Cuando "Modelo vs casa" tenga veredicto en tiros/tarjetas/faltas: si la
  casa acierta más, bloquear ese mercado; si hay empate, mezclar la
  probabilidad del modelo con la de la casa (el peso se ajusta con esas
  mismas evaluaciones) en vez de apostar la del modelo sola.
- Reglas de tarjetas por casa: unas cuentan solo amarillas, otras "puntos".
  Elegir la cuota más alta entre casas puede elegir otra regla (EV fantasma).
- Migraciones Alembic; hoy son `ALTER TABLE` a mano y `schema.sql` puede
  divergir de la BD real.
- `elo_history` / `teams.elo_rating` nunca se escriben: el Elo se recalcula
  desde cero en cada predicción (O(n²); hoy ~3 s, se degradará con más ligas).
