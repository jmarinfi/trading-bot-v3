# Subplan Fase 3 — OrderService: toda la I/O de órdenes sale de Portfolio

> Derivado de: `REFACTOR_PLAN.md`, punto 3 de «Orden de implementación».
> Estado: aprobado, pendiente de ejecución. Continuamos con el enfoque strangler aprobado: el bot arranca en todo momento.
> División de trabajo habitual: tú implementas cada tarea; yo reviso y añado los tests.

## Objetivo de la fase

Crear `src/orders.py` con `OrderService`, que encapsula **toda** la I/O de órdenes con el exchange bajo nombres de dominio, unificando de paso las ramas espejo long/short que hoy duplican ~85 líneas en `on_candle`. Tras la fase 3, `portfolio.py` no importa `BitgetExchange` ni llama al exchange directamente: solo habla con `OrderService`. Esto arregla de paso el bug del `_there_is_coin` sin `await` (portfolio.py:235 y :271 hoy — la comprobación de saldo nunca se ejecutaba).

## API de OrderService

```python
class OrderService:
    """Toda la I/O de órdenes con el exchange, con nombres de dominio."""

    def __init__(self, exchange: BitgetExchange): ...

    async def cancel_all(self, symbol: str) -> None
    async def cancel_sl(self, position: Position) -> None          # no-op si sl_order es None; traga ExchangeError
    async def has_balance(self, coin: str, quantity: float) -> bool
    async def place_sl(self, position: Position, trigger_price: float, amount: float) -> Order
    async def place_tp_limit(self, position: Position, amount: float, price: float) -> Order
    async def open_position(self, symbol: str, side: str, price: float,
                            amount: float, pct_static_sl: float) -> tuple[Order, Order]   # (entry, sl)
    async def market_close(self, position: Position, amount: float, price: float) -> Order
```

Decisiones de diseño:

- **Métodos anclados a `Position`** (`place_sl`, `place_tp_limit`, `market_close`): el lado de la orden (`"sell"` si la posición es long, `"buy"` si short) se deriva **dentro** de OrderService — ahí es donde las ramas espejo desaparecen de verdad. `open_position` recibe `(symbol, side, ...)` porque la posición todavía no existe.
- `cancel_sl` conserva la semántica best-effort actual (guarda por `sl_order is None` + `try/except ExchangeError` con warning). El resto **propaga** excepciones, como hoy hace el exchange crudo — el manejo de "vela descartada" sigue en `trade_worker`.
- **Nuevos logs info compactos por orden colocada** (símbolo, lado, precio, cantidad, order id) además del volcado completo a debug — era la promesa diferida de la fase 1 ("orden creada → info").
- `has_balance` es el `_there_is_coin` actual, correctamente await-ado en los call sites.

---

## Tarea 1 — Crear `src/orders.py` (sin tocar nada más)

**Ficheros:** `src/orders.py` (nuevo)

Los 7 métodos del API anterior. Esqueleto orientativo de los dos más ilustrativos:

```python
async def open_position(self, symbol, side, price, amount, pct_static_sl):
    entry_order = await self.exchange.create_limit_order(
        symbol=symbol,
        side="buy" if side == "long" else "sell",
        amount=amount,
        price=price,
    )
    log.info("entrada %s colocada (%s qty=%s price=%s)", side, symbol, amount, price)
    sl_static_price = price - price * pct_static_sl if side == "long" else price + price * pct_static_sl
    response = await self.exchange.create_trigger_order(
        symbol=symbol,
        side="sell" if side == "long" else "buy",
        trigger_price=sl_static_price,
        order_type="market",
        amount=amount,
    )
    sl_order = Order(id=response["data"]["orderId"])
    log.info("SL estático colocado (%s, order_id=%s)", symbol, sl_order["id"])
    return entry_order, sl_order

async def market_close(self, position, amount, price):
    response = await self.exchange.create_market_order(
        symbol=position.symbol,
        side="sell" if position.side == "long" else "buy",
        amount=amount,
        price=price,
    )
    log.info("cierre a mercado (%s %s qty=%s)", position.symbol, position.side, amount)
    return response
```

El bot no cambia: ningún consumidor todavía.

**Tests (míos, al revisar):** `tests/test_orders.py` con un `FakeExchange` que registra las llamadas — mapeo de lados (long→buy/sell correcto en cada método), dirección del SL estático (por debajo en long, por encima en short), `cancel_sl` no-op sin SL y tragando `ExchangeError`, `has_balance` True/False/moneda ausente, `cancel_all` pasa el símbolo.

---

## Tarea 2 — Portfolio recibe `orders`; migrar `on_trade` + main.py

**Ficheros:** `src/portfolio/portfolio.py`, `main.py`

- `Portfolio.__init__`: el parámetro `exchange: BitgetExchange` pasa a ser `orders: OrderService` (y `self.orders`); desaparece el import de `BitgetExchange`.
- Se eliminan `_cancel_sl_order` y `_there_is_coin` de Portfolio (su lógica ya vive en OrderService como `cancel_sl` y `has_balance`).
- `on_trade`: la llamada `await self._cancel_sl_order(position=position)` pasa a `await self.orders.cancel_sl(position=position)`.
- `main.py`: construir `orders = OrderService(exchange=exchange)` y pasarlo a `Portfolio(..., orders=orders)`.

**Importante:** al desaparecer `self.exchange` en esta tarea, `on_candle` (que aún usa el exchange) rompe hasta aplicar la T3 — **T2 y T3 deben ir en el mismo commit** y se verifica al final de ambas.

---

## Tarea 3 — Migrar la gestión de posiciones de `on_candle`

**Fichero:** `src/portfolio/portfolio.py`

- `cancel_all_orders` + log → `await self.orders.cancel_all(symbol=self.symbol)`.
- Prune: `self._cancel_sl_order(...)` → `self.orders.cancel_sl(position=position)`.
- Switch SL (estático→trailing): `cancel_trigger_order` + `create_trigger_order` con el `side` condicional → `await self.orders.cancel_sl(position=position)` + `position.sl_order = await self.orders.place_sl(position=position, trigger_price=sl_trailing_price, amount=self.filled_amount(position=position))`.
- **TP unificado**: los dos bloques espejo (long con `exit_long_price`, short con `exit_short_price`) colapsan en uno:

```python
side_word = "long" if position.side == "long" else "short"
exit_price = last_row.get(f"exit_{side_word}_price", None)
exit_signal = next(
    (s for s in signals if s.symbol == position.symbol and s.type == f"exit_{side_word}"),
    None,
)
if exit_price is not None and exit_price != 0 and exit_signal is None:
    position.tp_order = await self.orders.place_tp_limit(
        position=position,
        amount=self.filled_amount(position=position),
        price=exit_price,
    )
```

**Verificación T2+T3:** `uv run pytest` (45 passed, previa adaptación de `make_portfolio` en mis tests) + `uv run python -c "import main"` OK.

---

## Tarea 4 — Migrar las señales de `on_candle`; fix del await; colapso de las entradas espejo

**Fichero:** `src/portfolio/portfolio.py`

- Las dos ramas de entrada (42+42 líneas espejo) colapsan en una:

```python
if signal.type in ("enter_long", "enter_short"):
    side = "long" if signal.type == "enter_long" else "short"
    position = self.find_open(positions=opened_positions, symbol=signal.symbol, side=side)
    coin = quote if side == "long" else base
    needed = (self.base_amount_position * float(signal.data["close"])
              if side == "long" else self.base_amount_position)
    if position is None and await self.orders.has_balance(coin=coin, quantity=needed):
        entry_order, sl_order = await self.orders.open_position(
            symbol=signal.symbol,
            side=side,
            price=float(signal.data["close"]),
            amount=self.base_amount_position,
            pct_static_sl=self.pct_static_sl,
        )
        position = Position(
            symbol=signal.symbol, side=side, open_signal=signal,
            open_order=entry_order, sl_order=sl_order, is_open=True,
        )
        opened_positions.append(position)
        self.store.save(position=position)
```

- **El `await` de `has_balance` es el fix del bug**: desde hoy las entradas se rechazan si no hay saldo suficiente (antes la comprobación nunca se ejecutaba y la orden se colocaba igual).
- Las dos ramas de salida colapsan en una con `market_close(position=position, amount=self.filled_amount(position=position), price=signal.data["close"])`.

**Tests (míos, al revisar):** con `FakeOrderService` + BD tmp — entrada con saldo insuficiente no crea posición; con saldo crea la posición con sus órdenes; señal de salida coloca el cierre a mercado del lado correcto.

---

## Tarea 5 — Verificación de cierre de fase

- `uv run pytest` completo en verde (45 existentes + `test_orders.py` + tests de gating, adaptando `make_portfolio`).
- `uv run python -c "import main"` sin excepciones.
- Arranque manual corto (arrancar y Ctrl-C) — tu parte.
- `grep -n "exchange" src/portfolio/portfolio.py` → cero referencias (solo `orders`).
- Marcar fase 3 completada en `REFACTOR_PLAN.md`.

---

## Cambios de comportamiento (los únicos)

1. **FIX del chequeo de saldo**: las entradas ahora se gatean por `has_balance` — con fondos insuficientes la orden de entrada ya no se coloca (antes el check no se ejecutaba nunca). Es el cambio de comportamiento más importante de todo el refactor y estaba anunciado desde la fase 1.
2. Nuevos logs `info` compactos por cada orden colocada (antes solo había volcados a `debug`).

Todo lo demás es refactor equivalente: mismas órdenes, mismos lados, mismos precios, mismo manejo de errores.

## Fuera de alcance

- `TradingEngine`, cola de eventos, estado en memoria, save-tras-cada-mutación → fase 4.
- `trades_watcher`, eliminación de `trade_worker.py`, recableado completo → fase 5.
- Reconciliación al arranque → fase 7.
- Journal y endurecimiento de `PositionStore` → fase 6.

## Orden y dependencias

```
T1 (orders.py, aislado) ─► T2 + T3 (mismo commit: Portfolio pierde exchange) ─► T4 ─► T5
```

T1 es código nuevo sin riesgo; T2+T3 son la mecánica de sustitución (van juntos para que el fichero compile); T4 es donde colapsan las ramas espejo y entra el fix del await.
