# Plan de refactorización robusta — trading-bot-v3

> Fecha: 2026-08-18 · Estado: pendiente de aprobación/ejecución

## Diagnóstico (validado en código)

- **No hay hilos OS**: todo son tareas asyncio sobre un event loop. El peligro real es que `_watch_my_trades_loop` y `_consume_signals_loop` (src/trade_worker.py:88-93) llaman concurrentemente a `Portfolio.on_trade` y `Portfolio.on_candle`, ambos con ciclo *leer-`get_open()` → mutar entre `await`s → guardar fila completa*. El último que guarda sobreescribe al otro (lost updates de fills, órdenes SL/TP e `is_open`). No existe ningún lock en el proyecto.
- **Portfolio es una clase-dios** (7 responsabilidades): ejecución de órdenes, política SL/TP, bookkeeping de posiciones, interpretación de señales, chequeo de saldo, orquestación de persistencia y logging. `on_candle` es un método de 223 líneas con ramas long/short duplicadas en espejo.
- **Persistencia frágil**: SQLite como único estado (se relee en cada evento), sin manejo de errores ni reintentos, guardados por lotes no atómicos, bloqueante en el event loop, sin reconciliación con el exchange al arrancar, sin dedup de fills.
- **Bugs latentes confirmados**:
  1. `_there_is_coin` sin `await` (portfolio.py:207 y 250) → la comprobación de saldo **nunca se ejecuta** (una corrutina siempre es truthy).
  2. `return` prematuro que pierde mutaciones sin guardar (portfolio.py:191-192): cancelaciones de SL, `is_open=False` y `tp_order` no se persisten cuando la vela no trae señales.
  3. `MAX_NUM_OPEN_ORDERS` ausente de config → `TypeError` al arrancar (trade_worker.py:12,19).
  4. `self.name_entry_col` / `name_exit_col` inexistentes en mr_ret.py:53,58 → `AttributeError`.
  5. Fills duplicados tras reconexión del websocket se acumulan sin dedup y corrompen los importes usados para detectar cierres.

## Arquitectura destino: single-writer (actor)

Todas las fuentes de eventos desembocan en **una cola** consumida por **una tarea** (`TradingEngine`). Las carreras quedan eliminadas por construcción, sin un solo lock:

```
candle_worker ──── CandleClosed ────┐
                                    ├──> event_queue ──> TradingEngine (1 tarea secuencial)
trades_watcher ─── FillsReceived ───┘        ├─ Portfolio      (estado puro en memoria, sin I/O)
                                             ├─ OrderService   (toda la I/O de órdenes con el exchange)
                                             ├─ PositionStore  (persistencia, con reintento)
                                             └─ Journal        (auditoría append-only)
main.py: config validada → logging → Reconciler (arranque) → TaskGroup{candle_worker, trades_watcher, engine}
```

## Estructura de ficheros

```
src/
  config.py              # validación fail-fast de claves requeridas (antes dict global crudo)
  logging_setup.py       # NUEVO: logging stdlib, consola + RotatingFileHandler (data/bot.log)
  events.py              # NUEVO: dataclasses CandleClosed(signals, last_row, timeframe), FillsReceived(trades)
  candle_worker.py       # cambia: encola CandleClosed en event_queue (resto igual)
  trades_watcher.py      # NUEVO: reemplaza trade_worker.py; watch_my_trades → FillsReceived; solo backoff, sin tocar estado
  engine.py              # NUEVO: TradingEngine (consumidor único + política de velas)
  orders.py              # NUEVO: OrderService (I/O de órdenes, unifica long/short vía side)
  reconciliation.py      # NUEVO: sincronización exchange ↔ BD al arranque
  portfolio/
    portfolio.py         # REESCRITO: Portfolio = estado puro en memoria (sin exchange, sin store, sin async)
    position.py          # sin cambios
    position_store.py    # endurecido: reintento, deserialización tolerante
    journal.py           # NUEVO: tabla append-only journal(id, ts, kind, payload)
```

`trade_worker.py` se elimina. `exchange/`, `data/`, `strategy/` no cambian (salvo fixes). Esquema `positions` intacto: no hay migración.

## Diseño por módulo

### Portfolio (estado puro)

Carga sus posiciones una única vez de `PositionStore.get_open()` al construirse. Solo métodos síncronos sin I/O: matching de fills por id de orden, `record_open_fill`/`record_close_fill` con **dedup por `trade["id"]`**, detección de cierre por dust (`calculate_dust_value` se muda aquí), invariantes (máx. una posición abierta por símbolo/lado), `mark_closed`, `add`. 100% testeable sin mocks.

### OrderService

Operaciones de dominio sobre BitgetExchange: `open_position(side, price, amount, pct_static_sl)`, `close_position_market`, `place_trailing_sl`, `place_tp_limit`, `cancel_sl`, `cancel_all`, `has_balance` (el `_there_is_coin` arreglado). Unifica las ramas espejo long/short de 42+42 líneas en una sola parametrizada por `side`. Logea y propaga excepciones.

### TradingEngine

Único punto que muta estado y toca store/journal. Bucle: `event = await queue.get()` → dispatch.

- `on_trade` se convierte en `_handle_fills`: match puro en Portfolio → si detecta cierre → `cancel_sl` → `mark_closed` → **`store.save()` inmediatamente tras cada mutación** (desaparecen los save-all por lotes con snapshots).
- `on_candle` se descompone en: `_prune_unfilled` (posiciones sin fills), `_manage_stop_losses` (switch estático→trailing), `_place_tp_limits`, `_process_signals` (entry→`_open_position(side)`, exit→`_close_position(side)`).
- Errores ccxt → log + journal + abortar el evento actual y continuar (semántica actual de "vela descartada", pero conservando lo ya persistido).
- Errores de sqlite tras reintento o excepciones inesperadas → fail-stop (abortar el bot antes que operar con estado no persistido); la reconciliación del siguiente arranque repara el estado del exchange.

### trades_watcher / candle_worker

Watchers finos que solo traducen websocket/vela a eventos y encolan. Sin acceso a Portfolio.

### Persistencia

- **PositionStore**: `save` con 3 reintentos ante `sqlite3.OperationalError`; `_to_signal` tolerante a claves desconocidas (una fila con drift de esquema no tumba `get_open()`). Se mantiene sqlite3 síncrono (escrituras <1 ms y ya no hay interleavings); WAL y busy_timeout como ahora.
- **Journal**: escrituras best-effort (un fallo logea pero no detiene el trading, a diferencia del store); registra `fills_received`, `order_placed`, `order_cancelled`, `position_opened`, `position_closed`, `candle_processed`, `reconciliation`, `error`.

### Reconciliación al arranque

Se ejecuta antes de arrancar los workers, single-threaded, usando el mismo Portfolio/OrderService:

- Para cada posición abierta en BD: comparar sus órdenes con `fetch_opened_orders` del exchange, recuperar fills perdidos y marcar cerradas las completadas.
- Órdenes SL/TP desaparecidas → warning + recolocación best-effort.
- Órdenes abiertas en el exchange sin posición en BD → warning + cancelación (la BD es la fuente de verdad).
- Todo queda reflejado en el journal.

### Logging

`logging_setup.py` con formato `asctime level name message`, consola + archivo rotativo (data/bot.log, p. ej. 5 MB × 5). Sustituir los ~15 `print()` de portfolio/workers/main por loggers de módulo. `scripts.py` se queda como está.

### Fixes incluidos

1. Await del chequeo de saldo (resuelto por diseño en OrderService).
2. Return que perdía mutaciones (resuelto por diseño: save tras cada mutación).
3. `MAX_NUM_OPEN_ORDERS` validado en config y añadido a `.env` (valor propuesto: 5, a confirmar).
4. Nombres de columnas en mr_ret.py corregidos a los definidos en BaseStrategy.
5. Dedup de fills por `trade["id"]`.

## Cambios de comportamiento a ser consciente

1. **El chequeo de saldo por fin funciona**: entradas pueden rechazarse por fondos insuficientes (antes se saltaba silenciosamente y la orden se colocaba igual).
2. Un fill que llegue mientras se procesa una vela espera en la cola a que termine (antes interfoliaba de forma insegura): retraso de segundos como máximo, a cambio de cero lost updates.
3. Los fills duplicados del websocket ya no alteran los importes acumulados.

## Plan de pruebas

- `test_portfolio.py` (nuevo): matching de fills, dedup, cierre por dust, invariante una-por-lado — lógica pura, sin mocks.
- `test_engine.py` (nuevo): FakeExchange en memoria; procesado secuencial, orden de operaciones por vela, gating por saldo, error de exchange aborta la vela pero conserva lo persistido.
- `test_position_store.py` (extender): reintento ante BD bloqueada, deserialización tolerante.
- `test_journal.py` (nuevo): roundtrip append/lectura, fallo del journal no detiene el trading.
- `test_reconciliation.py` (nuevo): órdenes huérfanas canceladas, fills perdidos recuperados.

## Orden de implementación

1. ✅ Scaffolding: `config.py` validado, `logging_setup.py`, `events.py` + fixes de crashes latentes (completada — ver `REFACTOR_PLAN_PHASE1.md`; durante la fase, `mr_ret.py` fue sustituida por `lrs_strategy.py` y se resolvió no introducir la clave `MAX_NUM_OPEN_ORDERS`).
2. ✅ `Portfolio` puro + `test_portfolio.py` (completada con enfoque strangler — ver `REFACTOR_PLAN_PHASE2.md`: métodos puros extraídos y dedup de fills activo en el bot vivo; la conversión a estado en memoria se completa en la fase 4 con el engine).
3. ✅ `OrderService` (completada — ver `REFACTOR_PLAN_PHASE3.md`: toda la I/O de órdenes vive en `src/orders.py`, las ramas espejo long/short colapsaron, y el chequeo de saldo `has_balance` queda correctamente await-ado — fix activo del bug detectado en la fase 1).
4. `TradingEngine` + `test_engine.py` con FakeExchange.
5. `trades_watcher.py`, adaptar `candle_worker.py`, recablear `main.py`; eliminar `trade_worker.py`.
6. PositionStore endurecido + `journal.py` + tests.
7. `reconciliation.py` + tests.
8. Limpieza final: borrar código muerto, actualizar PLAN.md y README.md, suite completa en verde.

## Verificación

- `uv run pytest` tras cada fase.
- Revisión final: sin `print()` en el bot, sin referencias al antiguo `trade_worker`, y ningún `asyncio.Lock` (no debe hacer falta ninguno por diseño).
