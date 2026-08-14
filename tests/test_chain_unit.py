"""Phase 3 — unit coverage for chain.py branches not hit by the deploy path:
the whitechain_testnet RPC selection and the artifact-not-found errors.
"""

import sys
from pathlib import Path

import pytest
from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chain  # noqa: E402
import config  # noqa: E402


def test_get_w3_testnet_requires_rpc(monkeypatch):
    monkeypatch.setattr(config, "NETWORK", "whitechain_testnet")
    monkeypatch.setattr(config, "WHITECHAIN_TESTNET_RPC", "")
    with pytest.raises(RuntimeError):
        chain.get_w3()


def test_get_w3_testnet_builds_http_provider(monkeypatch):
    # Does not connect — just constructs the Web3 over the configured RPC URL.
    monkeypatch.setattr(config, "NETWORK", "whitechain_testnet")
    monkeypatch.setattr(config, "WHITECHAIN_TESTNET_RPC", "https://rpc-testnet.whitechain.io")
    w3 = chain.get_w3()
    assert isinstance(w3, Web3)
    assert isinstance(w3.provider, Web3.HTTPProvider)


def test_load_artifact_unknown_name_raises():
    with pytest.raises(chain.ArtifactNotFound):
        chain.load_artifact("NoSuchContract")
