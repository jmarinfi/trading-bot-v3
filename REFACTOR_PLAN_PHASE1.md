# Subplan Fase 1 — Scaffolding: config validada, logging, eventos y fixes de crashes latentes

> Derivado de: `REFACTOR_PLAN.md`, punto 1 de «Orden de implementación».
> Estado: aprobado, pendiente de ejecución. Cada tarea es pequeña (≈30–60 min), verificable de forma independiente y deja la suite en verde.

## Alcance de la fase

Crear la infraestructura que necesitan las fases siguientes (config validada, logging, eventos tipados) y eliminar los crashes latentes que impiden arrancar o ejecutar la estrategia. **No** se toca todavía la concurrencia, Portfolio ni la persistencia.

---

## Tarea 1 — Añadir `MAX_NUM_OPEN_ORDERS` a `.env` y crear `.env.example`

**Ficheros:** `.env`, `.env.example` (nuevo)

- Añadir a `.env`: `MAX_NUM_OPEN_ORDERS=5` (valor propuesto por defecto; **pendiente de confirmación** — hoy `trade_worker.py:19` calcula `limit = int(MAX_NUM_OPEN_ORDERS) + 5` para `watch_my_trades`).
- Crear `.env.example` documentando todas las claves con placeholders (sin secretos reales): `BITGET_API_KEY`, `BITGET_API_SECRET`, `BITGET_PASSPHRASE`, `SYMBOL`, `TIMEFRAME`, `BUFFER_CANDLES_LENGTH`, `BASE_AMOUNT_POSITION`, `N_BARS_STATIC_SL`, `PCT_STATIC_LS`, `PCT_TRAILING_LS`, `MAX_NUM_OPEN_ORDERS`.
- Las claves `QUOTE`, `BASE`, `PRICE_PRECISION`, `QUANTITY_PRECISION` existen en `.env` pero no se usan en el código: se documentan como opcionales (no se validan ni se eliminan).

**Criterio de aceptación:** `grep MAX_NUM_OPEN_ORDERS .env` devuelve la clave; `.env.example` existe y no contiene secretos.

---

## Tarea 2 — `src/config.py`: `Settings` validado con fail-fast

**Ficheros:** `src/config.py` (reescribir), `tests/test_config.py` (nuevo)

Sustituir el dict crudo (`config = dotenv_values(".env")`, config.py:3) por:

```python
@dataclass(frozen=True)
class Settings:
    symbol: str
    timeframe: str
    buffer_candles_length: int
    base_amount_position: float
    n_bars_static_sl: int
    pct_static_sl: float
    pct_trailing_sl: float
    max_num_open_orders: int
    bitget_api_key: str
    bitget_api_secret: str
    bitget_passphrase: str

class ConfigError(RuntimeError): ...

def load_settings(env_file: str | Path = ".env") -> Settings: ...
```

- `load_settings` recibe la ruta del fichero (testeable con `tmp_path`, sin tocar el `.env` real).
- Valida en una sola pasada y lanza `ConfigError` con **la lista completa** de claves ausentes o con tipo inválido (coerción int/float/str según el campo), no una por una.
- `max_num_open_orders`: opcional con default `5`; el resto, obligatorias.
- No se exporta ningún dict global: los consumidores migran en la Tarea 3.

**Tests (`tests/test_config.py`):**
- `.env` completo en `tmp_path` → `Settings` con tipos correctos.
- Faltan 2 claves → `ConfigError` cuyo mensaje menciona **ambas**.
- `BUFFER_CANDLES_LENGTH=abc` → `ConfigError` que nombra la clave.
- `MAX_NUM_OPEN_ORDERS` ausente → default `5`.

**Criterio de aceptación:** `uv run pytest tests/test_config.py` en verde.

---

## Tarea 3 — Migrar consumidores a `Settings` (elimina el `TypeError` de arranque)

**Ficheros:** `main.py`, `src/trade_worker.py`

- `main.py:13-19`: eliminar las lecturas a nivel de módulo; cargar `settings = load_settings()` **dentro** de `main()` (así importar el módulo no ejecuta validación ni I/O).
- `main.py:27-31` y `:37-44`: usar `settings.bitget_*` y `settings.*` en la construcción de `BitgetExchange` y `Portfolio`.
- `src/trade_worker.py:11-12`: eliminar `SYMBOL` y `MAX_NUM_OPEN_ORDERS` a nivel de módulo (fuente del crash actual: `int(None)` en línea 19). `trade_worker` y `_watch_my_trades_loop` reciben `symbol: str` y `max_num_open_orders: int` (o el `Settings`) como parámetros desde `main.py`.
- Verificar que no quedan importadores de `config` (hoy solo main.py y trade_worker.py).

**Criterio de aceptación:** `grep -rn "from src.config import config" src/ main.py` no devuelve nada; `uv run python -c "import main"` no lanza excepción; `uv run pytest` en verde.

---

## Tarea 4 — `src/logging_setup.py`: logging estructurado

**Ficheros:** `src/logging_setup.py` (nuevo), `tests/test_logging_setup.py` (nuevo)

```python
def setup_logging(
    level: int = logging.INFO,
    log_file: Path = Path("data/bot.log"),
) -> None: ...
```

- Root logger con formato `%(asctime)s %(levelname)s %(name)s %(message)s`.
- Dos handlers: consola (stdout) + `RotatingFileHandler(log_file, maxBytes=5 MB, backupCount=5)`.
- Crea el directorio padre si no existe; idempotente (segunda llamada no duplica handlers).
- Se invocará como primera línea de `main()` (se cablea en la Tarea 5).

**Tests:** al llamar `setup_logging(log_file=tmp_path/"bot.log")` y logear una línea, el fichero existe y contiene la línea; segunda llamada no duplica handlers (`len(root.handlers)` estable).

**Criterio de aceptación:** `uv run pytest tests/test_logging_setup.py` en verde.

---

## Tarea 5 — Sustituir `print()` por loggers y cablear logging en `main()`

**Ficheros:** `main.py`, `src/trade_worker.py`, `src/candle_worker.py`, `src/portfolio/portfolio.py`, `src/strategy/base.py`

- Primera línea de `main()`: `setup_logging()`.
- En cada fichero: `log = logging.getLogger(__name__)` y reemplazar cada `print(...)`.
- Criterio de niveles:
  - `info`: flujo normal (arranque, señal consumida, vela procesada, orden creada).
  - `warning`: errores manejados y recuperados (cancelaciones que fallan con `ExchangeError`, vela descartada por error de red, reintentos con backoff).
  - `error`: excepciones inesperadas antes de propagarlas.
  - Volcados grandes (dataframes, respuestas completas de ccxt): `debug`.
- Mantener el texto actual de los mensajes (los prefijos tipo `[portfolio] -` pueden eliminarse; el nombre del logger ya identifica el módulo).
- `scripts.py` se deja tal cual (script manual con su propio `logging.basicConfig`).

**Criterio de aceptación:** `grep -rn "print(" src/ main.py` no devuelve nada; una ejecución manual corta (arrancar y parar con Ctrl-C) escribe en consola y en `data/bot.log`.

---

## Tarea 6 — Fix de `mr_ret.py` (AttributeError) y de `base.py` (KeyError latente)

**Ficheros:** `src/strategy/mr_ret.py`, `src/strategy/base.py`, `tests/test_mr_ret.py` (nuevo)

Dos bugs encadenados; arreglar solo el primero destapa el segundo:

1. **mr_ret.py:53**: `self.name_entry_col` → `self.name_entry_long_col` (la condición `zscore < -enter_zscore` con filtro de tendencia alcista es una entrada larga).
   **mr_ret.py:58**: `self.name_exit_col` → `self.name_exit_long_col` (`zscore >= 0` cierra la larga).
2. **base.py:50-57**: `last_row[self.name_entry_short_col]` / `last_row[self.name_exit_short_col]` harán `KeyError` porque `MrRetStrategy` nunca crea las columnas `entry_short`/`exit_short`. Cambiar los cuatro accesos a `last_row.get(<col>, 0) == 1` para que una estrategia que no genera un tipo de señal no rompa `run()`.

**Tests (`tests/test_mr_ret.py`):**
- DataFrame sintético de ~120 velas (suficiente para SMA 45, RSI 14 y ret 30) con una caída pronunciada al final → `strategy.run(df)` devuelve `(signals, last_row)` sin `AttributeError` ni `KeyError`, y `last_row` contiene `entry_long` y `exit_long_price`.
- Caso sin señal (tendente al alza sin caída) → `signals == []` sin excepción.

**Criterio de aceptación:** `uv run pytest tests/test_mr_ret.py` en verde.

---

## Tarea 7 — `src/events.py`: eventos tipados

**Ficheros:** `src/events.py` (nuevo), `tests/test_events.py` (nuevo)

```python
from dataclasses import dataclass
from typing import Any
from ccxt.base.types import Trade
from src.strategy.base import Signal

@dataclass(frozen=True)
class CandleClosed:
    signals: list[Signal]
    last_row: dict[str, Any]
    timeframe: str

@dataclass(frozen=True)
class FillsReceived:
    trades: list[Trade]

Event = CandleClosed | FillsReceived
```

- Solo definición de tipos: sin productores/consumidores aún (llegan en las fases 4–5). No se cablea nada todavía para no mezclar esta fase con la refactorización de workers.

**Tests:** construcción de ambos eventos y `FrozenInstanceError` al intentar reasignar un campo.

**Criterio de aceptación:** `uv run pytest tests/test_events.py` en verde.

---

## Tarea 8 — Verificación de cierre de fase

- `uv run pytest` completo en verde (incluye los existentes `tests/test_position_store.py`).
- `uv run python -c "import main"` sin excepciones.
- `grep -rn "print(" src/ main.py` vacío; `grep -rn "dotenv_values" src/` solo en `src/config.py`.
- Marcar la fase 1 como completada en `REFACTOR_PLAN.md`.

---

## Dependencias y orden

```
T1 (.env) ──► T3 (migración necesita la clave presente)
T2 (Settings) ─► T3
T4 (logging) ──► T5 (prints→loggers cablea setup_logging)
T6 (strategy)  ── independiente
T7 (events)    ── independiente
T8 ── al final, con todo lo anterior merged
```

Orden de ejecución recomendado: **T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8**. T6 y T7 son de bajo riesgo y pueden ir en cualquier punto intermedio.

## Fuera de alcance de esta fase (se abordará en fases posteriores)

- Cualquier cambio en `Portfolio`, concurrencia o `PositionStore` (fases 2–6).
- Cablear `event_queue` / productores de eventos (fases 4–5).
- Eliminar `trade_worker.py` (fase 5; aquí solo se parchea su crash de arranque).
