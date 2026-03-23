from __future__ import annotations

import os

from EvoScientist.runtime_env import ensure_torch_backend_autoload_safe_default


def test_torch_backend_autoload_defaults_to_zero(monkeypatch):
    monkeypatch.delenv("TORCH_DEVICE_BACKEND_AUTOLOAD", raising=False)

    ensure_torch_backend_autoload_safe_default()

    assert os.environ.get("TORCH_DEVICE_BACKEND_AUTOLOAD") == "0"


def test_torch_backend_autoload_respects_existing_value(monkeypatch):
    monkeypatch.setenv("TORCH_DEVICE_BACKEND_AUTOLOAD", "1")

    ensure_torch_backend_autoload_safe_default()

    assert os.environ.get("TORCH_DEVICE_BACKEND_AUTOLOAD") == "1"
