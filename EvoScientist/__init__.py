"""EvoScientist Agent — AI-powered research & code execution."""

from .backends import CustomSandboxBackend, ReadOnlyFilesystemBackend
from .config import (
    EvoScientistConfig,
    load_config,
    save_config,
    get_effective_config,
    get_config_path,
)
from .llm import get_chat_model, MODELS, list_models, DEFAULT_MODEL
from .middleware import create_skills_middleware
from .prompts import get_system_prompt, RESEARCHER_INSTRUCTIONS
from .tools import tavily_search, think_tool

# Lazy imports for EvoScientist_agent and create_cli_agent to avoid
# triggering model initialization at import time. This allows CLI commands
# like `onboard` and `config` to run without API keys being configured.


def __getattr__(name):
    if name == "EvoScientist_agent":
        from .EvoScientist import EvoScientist_agent
        return EvoScientist_agent
    if name == "create_cli_agent":
        from .EvoScientist import create_cli_agent
        return create_cli_agent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Agent graph (main export, lazily loaded)
    "EvoScientist_agent",
    "create_cli_agent",
    # Backends
    "CustomSandboxBackend",
    "ReadOnlyFilesystemBackend",
    # Configuration
    "EvoScientistConfig",
    "load_config",
    "save_config",
    "get_effective_config",
    "get_config_path",
    # LLM
    "get_chat_model",
    "MODELS",
    "list_models",
    "DEFAULT_MODEL",
    # Middleware
    "create_skills_middleware",
    # Prompts
    "get_system_prompt",
    "RESEARCHER_INSTRUCTIONS",
    # Tools
    "tavily_search",
    "think_tool",
]
