from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from src.config import ConfigError, Settings, _SPECS, load_settings

VALID_ENV = (
    "SYMBOL=BTC/USDT\n"
    "TIMEFRAME=15m\n"
    "BUFFER_CANDLES_LENGTH=200\n"
    "BASE_AMOUNT_POSITION=0.001\n"
    "N_BARS_STATIC_SL=10\n"
    "PCT_STATIC_SL=0.02\n"
    "PCT_TRAILING_SL=0.01\n"
    "BITGET_API_KEY=test-key\n"
    "BITGET_API_SECRET=test-secret\n"
    "BITGET_PASSPHRASE=test-passphrase\n"
)


def write_env(tmp_path: Path, content: str) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(content, encoding="utf-8")
    return env_file


def test_load_settings_valid_env(tmp_path):
    settings = load_settings(write_env(tmp_path, VALID_ENV))

    assert settings.symbol == "BTC/USDT"
    assert settings.timeframe == "15m"
    assert settings.buffer_candles_length == 200
    assert settings.base_amount_position == pytest.approx(0.001)
    assert settings.n_bars_static_sl == 10
    assert settings.pct_static_sl == pytest.approx(0.02)
    assert settings.pct_trailing_sl == pytest.approx(0.01)
    assert settings.bitget_api_key == "test-key"
    assert settings.bitget_api_secret == "test-secret"
    assert settings.bitget_passphrase == "test-passphrase"


def test_load_settings_coerces_types(tmp_path):
    settings = load_settings(write_env(tmp_path, VALID_ENV))

    assert isinstance(settings.buffer_candles_length, int)
    assert isinstance(settings.n_bars_static_sl, int)
    assert isinstance(settings.base_amount_position, float)
    assert isinstance(settings.pct_static_sl, float)
    assert isinstance(settings.pct_trailing_sl, float)


def test_missing_keys_reported_all_at_once(tmp_path):
    content = VALID_ENV.replace("SYMBOL=BTC/USDT\n", "").replace(
        "BITGET_API_KEY=test-key\n", ""
    )

    with pytest.raises(ConfigError) as exc_info:
        load_settings(write_env(tmp_path, content))

    message = str(exc_info.value)
    assert "SYMBOL" in message
    assert "BITGET_API_KEY" in message


def test_invalid_int_reports_key_and_value(tmp_path):
    content = VALID_ENV.replace(
        "BUFFER_CANDLES_LENGTH=200", "BUFFER_CANDLES_LENGTH=abc"
    )

    with pytest.raises(ConfigError) as exc_info:
        load_settings(write_env(tmp_path, content))

    message = str(exc_info.value)
    assert "BUFFER_CANDLES_LENGTH" in message
    assert "abc" in message


def test_empty_value_counts_as_missing(tmp_path):
    content = VALID_ENV.replace("PCT_TRAILING_SL=0.01", "PCT_TRAILING_SL=")

    with pytest.raises(ConfigError) as exc_info:
        load_settings(write_env(tmp_path, content))

    assert "PCT_TRAILING_SL" in str(exc_info.value)


def test_missing_env_file(tmp_path):
    with pytest.raises(ConfigError, match="No se encuentra"):
        load_settings(tmp_path / "no-existe.env")


def test_settings_is_frozen(tmp_path):
    settings = load_settings(write_env(tmp_path, VALID_ENV))

    with pytest.raises(FrozenInstanceError):
        settings.symbol = "ETH/USDT"


def test_specs_cover_all_settings_fields():
    spec_fields = {spec.field for spec in _SPECS}
    settings_fields = {field.name for field in fields(Settings)}

    assert spec_fields == settings_fields
