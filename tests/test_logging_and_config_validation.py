"""Phase 4 — structured logging + startup config validation."""

import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import logging_setup  # noqa: E402


# ---------- logging ----------

def test_json_formatter_emits_one_object_with_fields_and_extra():
    rec = logging.makeLogRecord({"name": "t", "levelname": "INFO", "msg": "hello %s", "args": ("world",)})
    rec.agent = "0xABC"  # a structured extra
    line = logging_setup.JsonFormatter().format(rec)
    obj = json.loads(line)
    assert obj["level"] == "INFO"
    assert obj["logger"] == "t"
    assert obj["msg"] == "hello world"
    assert obj["agent"] == "0xABC"


def test_configure_logging_text_and_json_are_idempotent(capsys):
    logging_setup.configure_logging(level="DEBUG", fmt="text")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    n_handlers = len(root.handlers)
    logging_setup.configure_logging(level="INFO", fmt="json")  # reconfigure
    assert len(root.handlers) == n_handlers  # not doubled
    assert isinstance(root.handlers[0].formatter, logging_setup.JsonFormatter)


# ---------- config validation ----------

def test_validate_local_has_no_requirements(monkeypatch):
    monkeypatch.setattr(config, "NETWORK", "local")
    assert config.validate_startup() == []


def test_validate_testnet_lists_missing(monkeypatch):
    monkeypatch.setattr(config, "NETWORK", "whitechain_testnet")
    monkeypatch.setattr(config, "SETTLEMENT_MODE", "legacy")
    for name in (
        "WHITECHAIN_TESTNET_RPC", "DEPLOYER_PRIVATE_KEY", "TEURC_ADDRESS",
        "FACILITATOR_WALLET_ADDRESS", "FACILITATOR_WALLET_PRIVATE_KEY",
        "SERVICE_PROVIDER_WALLET_ADDRESS", "SOUL_REGISTRY_ADDRESS",
        "SOUL_ATTRIBUTE_REGISTRY_ADDRESS", "SOUL_BOUND_TOKEN_REGISTRY_ADDRESS",
        "IS_VERIFIED_ATTRIBUTE_ADDRESS",
    ):
        monkeypatch.setattr(config, name, "")
    problems = config.validate_startup()
    assert any("WHITECHAIN_TESTNET_RPC" in p for p in problems)
    assert any("TEURC_ADDRESS" in p for p in problems)
    # No secret VALUES appear — only names.
    assert all("0x" not in p for p in problems)


def test_validate_atomic_requires_router(monkeypatch):
    monkeypatch.setattr(config, "NETWORK", "whitechain_testnet")
    monkeypatch.setattr(config, "SETTLEMENT_MODE", "atomic")
    monkeypatch.setattr(config, "ROUTER_ADDRESS", "")
    monkeypatch.setattr(config, "ROUTER_KYA_ADDRESS", "")
    problems = config.validate_startup()
    assert any("ROUTER_ADDRESS" in p for p in problems)


def test_validate_flags_bad_fee_and_mode(monkeypatch):
    monkeypatch.setattr(config, "NETWORK", "whitechain_testnet")
    monkeypatch.setattr(config, "FACILITATOR_FEE_BPS", 5000)
    monkeypatch.setattr(config, "SETTLEMENT_MODE", "nonsense")
    problems = config.validate_startup()
    assert any("FACILITATOR_FEE_BPS" in p for p in problems)
    assert any("SETTLEMENT_MODE" in p for p in problems)


def test_require_valid_raises_on_incomplete(monkeypatch):
    monkeypatch.setattr(config, "NETWORK", "whitechain_testnet")
    monkeypatch.setattr(config, "SETTLEMENT_MODE", "legacy")
    monkeypatch.setattr(config, "WHITECHAIN_TESTNET_RPC", "")
    with pytest.raises(RuntimeError):
        config.require_valid_startup()
