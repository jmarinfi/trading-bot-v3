# Plan: persistencia de `Position` en SQLite sin ORM

## Decisiones (confirmadas)
- **Driver**: `sqlite3` de la librería estándar (síncrono, cero dependencias nuevas). Las escrituras son puntuales (<1 ms) y no dañan el event loop. Si en el futuro hiciera falta, migrar a `aiosqlite` es mecánico (misma API).
- **Esquema**: una única tabla `positions` con columnas escalares + columnas JSON para los objetos anidados (señales, órdenes, fills). Inmune a variaciones del schema de ccxt; consultable con `json_extract` si algún día hace falta.
- **Atomicidad multi-writer**: garantizada por SQLite (transacciones ACID) + `journal_mode=WAL` + `busy_timeout`. Cada escritura será una única sentencia dentro de una transacción corta. Además, al no haber `await` dentro de las llamadas, dos corrutinas del mismo loop no pueden intercalarse a mitad de transacción; y WAL + busy_timeout lo hace también seguro si en el futuro hay workers en procesos separados.

## Contexto del proyecto (hallazgos de la exploración)
- `Position` (`src/portfolio/portfolio.py:9`) es un `TypedDict` (dict plano en runtime) con: `symbol`, `open_signal`/`close_signal` (`Signal`, dataclass frozen con `data: dict[str, Any]` que contiene filas de DataFrame → valores numpy/pandas), `open_order`/`close_order`/`tp_order`/`sl_order` (`Order` de ccxt, TypedDict), `open_fills`/`close_fills` (`list[Trade]` de ccxt) e `is_open: bool`.
- `Portfolio` es un stub: solo `__init__` con parámetros de configuración; `positions_path = "logs/positions.log"` no se usa en ningún sitio.
- Toda la concurrencia actual es asyncio en un único proceso/event loop: `candle_worker` y `trade_worker` (con `_watch_my_trades_loop` y `_consume_signals_loop`) comunicados por `asyncio.Queue`. Nadie instancia `Portfolio` todavía.
- No existe ninguna persistencia, ni tests, ni librería de logging. Gestor de paquetes: uv. Python ≥ 3.14.

## 1. Nuevo archivo `src/portfolio/position_store.py` (~120 líneas)

**Pragmas** aplicados a cada conexión en `__init__`:

```sql
PRAGMA journal_mode=WAL;       -- lecturas y escritura concurrentes
PRAGMA synchronous=NORMAL;     -- buen compromiso durabilidad/latencia con WAL
PRAGMA busy_timeout=5000;      -- escritores concurrentes esperan en vez de fallar
```

**Schema** (creado con `CREATE TABLE IF NOT EXISTS`):

```sql
CREATE TABLE IF NOT EXISTS positions (
    id           INTEGER PRIMARY KEY,
    symbol       TEXT NOT NULL,
    is_open      INTEGER NOT NULL,
    open_signal  TEXT,          -- JSON
    close_signal TEXT,          -- JSON
    open_order   TEXT,          -- JSON (Order de ccxt)
    close_order  TEXT,          -- JSON
    tp_order     TEXT,          -- JSON
    sl_order     TEXT,          -- JSON
    open_fills   TEXT,          -- JSON array de Trade
    close_fills  TEXT,          -- JSON array de Trade
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_positions_is_open ON positions(is_open);
```

**Clase `PositionStore`**:

```python
PositionStore(db_path: Path = Path("data/positions.db"))
    # crea el directorio padre, abre conexión, aplica pragmas y schema

.save(position: Position) -> int
    # INSERT si la posición no tiene "id"; UPDATE por id si lo tiene.
    # Escribe el id generado de vuelta en position["id"].
    # Una sola sentencia por transacción (with conn:) → atómica.

.get(position_id: int) -> Position | None
.get_open() -> list[Position]      # para recuperar posiciones abiertas al arrancar
.get_all(limit: int = 100) -> list[Position]   # más recientes primero (depuración)
.close() -> None
```

**Helpers de serialización** (mismo módulo):
- `_to_json(obj)`: `json.dumps` con `default=` que convierte `Signal` (vía `dataclasses.asdict`), escalares numpy (`np.floating` → `float`, `np.integer` → `int`, etc. — `Signal.data` contiene filas de DataFrame), `Decimal`, y fallback a `str`. Así los valores pandas/numpy de `signal.data` no rompen el dump.
- Deserialización: `Signal(**json.loads(...))` para señales; órdenes/trades/fills se quedan como dicts planos (son `TypedDict` de ccxt, o sea dicts en runtime). Claves ausentes o `None` → columna NULL, y a la inversa. `is_open` se convierte 0/1 ↔ bool.

## 2. Modificar `src/portfolio/portfolio.py` (mínimo)
- Añadir `id: NotRequired[int]` a `Position` (para llevar el id de fila tras el primer `save`).
- En `Portfolio.__init__`: sustituir el `positions_path` (hoy sin uso) por un parámetro `db_path: Path = Path("data/positions.db")` y exponer `self.store = PositionStore(db_path)`. No se añaden más métodos a `Portfolio` (la lógica de trading queda fuera de alcance).

## 3. `.gitignore`
- Añadir `data/` para que la base de datos no se commitee.

## 4. Tests (ligeros)
- `uv add --dev pytest`.
- `tests/test_position_store.py` con `tmp_path`:
  - Roundtrip completo de una `Position` con `Signal.data` conteniendo valores numpy y dicts anidados estilo ccxt.
  - `save` dos veces sobre la misma posición → una sola fila (UPDATE, no duplicado).
  - Dos `PositionStore` (dos conexiones, simulando dos workers/procesos) escribiendo → ambas escrituras triunfan sin filas perdidas ni `SQLITE_BUSY`.
  - `get_open` filtra correctamente.

## 5. Verificación
- `uv run pytest`.
- Smoke manual: `uv run python -c ...` creando el store en una ruta temporal, guardando y leyendo una posición de prueba.

## Fuera de alcance (explícito)
- Cablear el store a `trade_worker`/`Portfolio` (la lógica de trading aún no existe); el store queda listo para cuando eso llegue.
- Migración de `positions.log` (no hay nada que migrar).
