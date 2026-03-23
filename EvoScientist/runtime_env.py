"""Runtime environment guards for stable EvoScientist startup."""

from __future__ import annotations

import os


def ensure_torch_backend_autoload_safe_default() -> None:
    """Prevent torch device backend autoload from breaking startup.

    On some machines, importing libraries in the LangChain stack can import
    ``torch``, which may auto-load third-party backends (for example
    ``torch_npu``) and fail early when system libraries are missing.

    We default ``TORCH_DEVICE_BACKEND_AUTOLOAD`` to ``0`` only when the user
    did not set it explicitly, keeping explicit user choices intact.
    """
    os.environ.setdefault("TORCH_DEVICE_BACKEND_AUTOLOAD", "0")
