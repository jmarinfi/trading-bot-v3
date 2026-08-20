# Subplan Fase 2 — Lógica pura de Portfolio (strangler incremental) + tests

> Derivado de: `REFACTOR_PLAN.md`, punto 2 de «Orden de implementación».
> Estado: aprobado, pendiente de ejecución. Enfoque: **strangler incremental** — extraer la lógica pura como métodos de la `Portfolio` actual y refactorizar `on_trade`/`on_candle` para delegar en ellos. El bot arranca en todo momento; el fix de dedup llega ya al bot vivo. La pureza total (sin exchange/store/async, estado en memoria) se completa en las fases 4–5 cuando el `TradingEngine` absorbe la orquestación.
> División de trabajo habitual: tú implementas el código de cada tarea; yo reviso y añado los tests correspondientes.

## Re-alcance respecto al plan general

El plan original situaba en la fase 2 la «carga única desde la BD y estado en memoria». Con el enfoque strangler eso se traslada a la fase 4 (el engine posee el estado); la fase 2 queda como **extracción de lógica pura + fix de dedup**, manteniendo el comportamiento del bot salvo las dos diferencias documentadas al final.

## API que construye esta fase

Métodos puros (sin I/O, sin store, sin await — testeables sin BD ni mocks). Reciben la lista de posiciones como argumento; en la fase 4, cuando Portfolio posea `self._positions`, el parámetro desaparece (cambio mecánico):

| Método | Firma | Cometido |
|---|---|---|
| `find_open` | `(positions, symbol, side) -> Position \| None` | posición abierta por símbolo/lado (invariante una-por-lado) |
| `filled_amount` | `(position) -> float` | suma de `open_fills` |
| `find_by_open_order` | `(positions, order_id) -> Position \| None` | match de fill de apertura |
| `find_by_exit_order` | `(positions, order_id) -> Position \| None` | match de fill de cierre (close/sl/tp) |
| `record_open_fill` | `(position, trade) -> bool` | dedup por `trade["id"]` + append; `True` si era nuevo |
| `record_close_fill` | `(position, trade) -> bool` | dedup + append + detección de cierre por dust; `True` si este fill cerró la posición |
| `mark_closed` | `(position) -> None` | `is_open = False` (transición explícita) |

---

## Tarea 1 — `find_open()` y `filled_amount()` (refactor de `on_candle`)

**Ficheros:** `src/portfolio/portfolio.py`

```python
def find_open(
    self, positions: list[Position], symbol: str, side: str
) -> Position | None:
    return next(
        (p for p in positions if p.symbol == symbol and p.side == side and p.is_open),
        None,
    )

def filled_amount(self, position: Position) -> float:
    return sum(float(fill["amount"]) for fill in position.open_fills)
```

Sustituciones en `on_candle`:

- Los cuatro bloques `next(...)` de búsqueda de posición duplicada — ramas `enter_long` (~línea 202), `enter_short` (~243), `exit_long` (~284), `exit_short` (~304) — pasan a `self.find_open(opened_positions, signal.symbol, "long")` / `"short"`.
- Las cinco sumas `sum([float(f["amount"]) ...])` — SL trailing (~141), TP long (~165), TP short (~188), exit long (~293), exit short (~313) — pasan a `self.filled_amount(position)`.

**Tests (los añado yo al revisar):** `find_open` devuelve la abierta correcta y `None` para lado/símbolo equivocado o posición cerrada; `filled_amount` con 0, 1 y varios fills.

**Aceptación:** `uv run pytest` en verde; `import main` OK; sin cambios de comportamiento.

---

## Tarea 2 — `find_by_open_order()` y `find_by_exit_order()` (refactor de `on_trade`)

**Ficheros:** `src/portfolio/portfolio.py`

```python
def find_by_open_order(
    self, positions: list[Position], order_id: str
) -> Position | None:
    return next((p for p in positions if p.open_order["id"] == order_id), None)

def find_by_exit_order(
    self, positions: list[Position], order_id: str
) -> Position | None:
    return next(
        (
            p
            for p in positions
            if (p.close_order and p.close_order["id"] == order_id)
            or (p.sl_order and p.sl_order["id"] == order_id)
            or (p.tp_order and p.tp_order["id"] == order_id)
        ),
        None,
    )
```

Sustituciones en `on_trade` (~líneas 74–90): los dos bloques `next(...)` pasan a usar estos métodos.

**Tests:** match por `open_order`; match por cada tipo de orden de salida (close, sl, tp); posiciones con órdenes a `None` no rompen; order id desconocido → `None`.

**Aceptación:** `uv run pytest` en verde; sin cambios de comportamiento.

---

## Tarea 3 — `record_open_fill()` con dedup por `trade["id"]`

**Ficheros:** `src/portfolio/portfolio.py`

```python
def record_open_fill(self, position: Position, trade: Trade) -> bool:
    if any(fill["id"] == trade["id"] for fill in position.open_fills):
        return False
    position.open_fills.append(trade)
    return True
```

En `on_trade` (~líneas 78–79):

```python
if position is not None:
    if not self.record_open_fill(position=position, trade=trade):
        log.info("fill de apertura duplicado ignorado (trade %s)", trade["id"])
```

**Tests:** fill nuevo se añade y devuelve `True`; duplicado por id no se añade y devuelve `False`; ids distintos ambos se añaden.

**Aceptación:** `uv run pytest` en verde.

---

## Tarea 4 — `record_close_fill()` con dedup + cierre por dust; `mark_closed()`

**Ficheros:** `src/portfolio/portfolio.py`

```python
def mark_closed(self, position: Position) -> None:
    position.is_open = False

def record_close_fill(self, position: Position, trade: Trade) -> bool:
    if any(fill["id"] == trade["id"] for fill in position.close_fills):
        return False
    position.close_fills.append(trade)
    sum_open = self.filled_amount(position)
    sum_close = sum(float(fill["amount"]) for fill in position.close_fills)
    if abs(sum_open - sum_close) < calculate_dust_value(trade["price"]):
        self.mark_closed(position)
        return True
    return False
```

En `on_trade` (~líneas 91–101), el bloque de fills de cierre queda:

```python
if position is not None:
    if self.record_close_fill(position=position, trade=trade):
        await self._cancel_sl_order(position=position)
```

Y el prune de `on_candle` (~líneas 118–121) usa `mark_closed`:

```python
if not position.open_fills:
    await self._cancel_sl_order(position=position)
    self.mark_closed(position)
    continue
```

**Tests:** fill de cierre parcial no cierra; el que iguala por debajo del dust cierra (`is_open=False`, devuelve `True`); duplicado no cuenta doble ni dispara cierre; frontera exacta del dust.

**Aceptación:** `uv run pytest` en verde.

---

## Tarea 5 — Verificación de cierre de fase

- `uv run pytest` completo en verde (27 existentes + los nuevos de `tests/test_portfolio.py`).
- `uv run python -c "import main"` sin excepciones.
- Arranque manual corto del bot (arrancar y Ctrl-C).
- Marcar la fase 2 como completada en `REFACTOR_PLAN.md`.

---

## Cambios de comportamiento introducidos (los únicos)

1. **Dedup de fills activo en el bot vivo**: los fills reenviados por el websocket tras una reconexión (o ya presentes tras un reinicio — los ids sobreviven al roundtrip JSON de la BD) ya no se acumulan ni corrompen las sumas de cierre. Es un fix real de un bug latente.
2. En `on_trade`, `is_open` pasa a `False` **antes** del `cancel_sl` en vez de después (el método puro hace la transición y el I/O va detrás). Sin impacto funcional: el cancel es best-effort con su `try/except`.

Todo lo demás es refactor equivalente.

## Fuera de alcance de esta fase

- **Estado en memoria y carga única desde la BD** → fase 4 (el engine posee el estado; hoy cada evento sigue releyendo `get_open()`).
- **Carreras on_trade/on_candle** → fase 4–5 (cola de eventos de un solo consumidor). La fase 2 no las resuelve.
- **Bug del `_there_is_coin` sin `await`** (~líneas 210 y 251) → fase 3 (`OrderService.has_balance`).
- **`return` prematuro de `on_candle`** (~línea 194) que pierde mutaciones → fase 4 (save tras cada mutación).

## Orden y dependencias

```
T1 ─► T2 ─► T3 ─► T4 ─► T5   (secuencial: cada una refactoriza código que la siguiente toca)
```

Las tareas son deliberadamente pequeñas: T1 y T2 son extracción mecánica; T3 y T4 introducen el dedup (única lógica nueva); T5 cierra.
