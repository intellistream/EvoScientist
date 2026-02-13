"""Configuration package for EvoScientist.

Re-exports all public symbols from settings and onboard submodules
so that existing ``from EvoScientist.config import X`` imports continue
to work without modification.
"""

from .settings import (
    get_config_dir,
    get_config_path,
    EvoScientistConfig,
    load_config,
    save_config,
    reset_config,
    get_config_value,
    set_config_value,
    list_config,
    get_effective_config,
    apply_config_to_env,
)
from .onboard import run_onboard

__all__ = [
    # settings
    "get_config_dir",
    "get_config_path",
    "EvoScientistConfig",
    "load_config",
    "save_config",
    "reset_config",
    "get_config_value",
    "set_config_value",
    "list_config",
    "get_effective_config",
    "apply_config_to_env",
    # onboard
    "run_onboard",
]
