from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

_REQUIRED = object()


class ConfigError(RuntimeError):
    """Claves ausentes o con valores inválidos en el fichero de entorno."""


@dataclass(frozen=True)
class Settings:
    symbol: str
    timeframe: str
    buffer_candles_length: int
    base_amount_position: float
    n_bars_static_sl: int
    pct_static_sl: float
    pct_trailing_sl: float
    bitget_api_key: str
    bitget_api_secret: str
    bitget_passphrase: str


@dataclass(frozen=True)
class _SettingSpec:
    field: str
    env_key: str
    type: type
    default: Any = _REQUIRED


_SPECS: list[_SettingSpec] = [
    _SettingSpec("symbol", "SYMBOL", str),
    _SettingSpec("timeframe", "TIMEFRAME", str),
    _SettingSpec("buffer_candles_length", "BUFFER_CANDLES_LENGTH", int),
    _SettingSpec("base_amount_position", "BASE_AMOUNT_POSITION", float),
    _SettingSpec("n_bars_static_sl", "N_BARS_STATIC_SL", int),
    _SettingSpec("pct_static_sl", "PCT_STATIC_SL", float),
    _SettingSpec("pct_trailing_sl", "PCT_TRAILING_SL", float),
    _SettingSpec("bitget_api_key", "BITGET_API_KEY", str),
    _SettingSpec("bitget_api_secret", "BITGET_API_SECRET", str),
    _SettingSpec("bitget_passphrase", "BITGET_PASSPHRASE", str),
]


def load_settings(env_file: str | Path = ".env") -> Settings:
    """Carga y valida la configuración del fichero de entorno"""
    env_path = Path(env_file)
    if not env_path.is_file():
        raise ConfigError(f"No se encuentra el fichero de configuración: {env_path}")

    raw = dotenv_values(env_path)
    errors: list[str] = []
    values: dict[str, Any] = {}

    for spec in _SPECS:
        raw_value = raw.get(spec.env_key)
        if raw_value is None or not raw_value.strip():
            if spec.default is _REQUIRED:
                errors.append(f"{spec.env_key}: ausente")
            else:
                values[spec.field] = spec.default
            continue
        try:
            values[spec.field] = spec.type(raw_value)
        except TypeError, ValueError:
            errors.append(
                f"{spec.env_key}: se esperaba {spec.type.__name__}, "
                f"se obtuvo '{raw_value}'"
            )

    if errors:
        raise ConfigError(
            f"Configuración inválida en {env_path}:\n- " + "\n- ".join(errors)
        )

    return Settings(**values)
