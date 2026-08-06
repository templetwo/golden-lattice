"""Orchestrator — the layer where the lattice meets the world.

Public API:
  - LatticeConfig: frozen Pydantic configuration.
  - run_lattice_session_async: async core orchestration entry.
  - run_lattice_session: sync wrapper for CLI callers.
  - AnthropicClient: concrete provider client.
  - OrchestratorError, OrchestratorTimeoutError, OrchestratorProviderError.

The orchestrator is the only async layer. Substrate, wire-layer parsers,
and synthesis engine remain sync pure functions. The orchestrator is also
the canonical Session-builder — userspace surfaces should construct Sessions
through this layer rather than hand-assembling phases.
"""

from golden_lattice.orchestrator.anthropic_client import AnthropicClient
from golden_lattice.orchestrator.config import (
    LatticeConfig,
    validate_provider_capabilities,
)
from golden_lattice.orchestrator.errors import (
    OrchestratorCapabilityError,
    OrchestratorError,
    OrchestratorProviderError,
    OrchestratorTimeoutError,
)
from golden_lattice.orchestrator.orchestrator import (
    DEFAULT_INVITED_MODELS,
    run_lattice_session,
    run_lattice_session_async,
)

__all__ = [
    "AnthropicClient",
    "DEFAULT_INVITED_MODELS",
    "LatticeConfig",
    "OrchestratorCapabilityError",
    "OrchestratorError",
    "OrchestratorProviderError",
    "OrchestratorTimeoutError",
    "run_lattice_session",
    "run_lattice_session_async",
    "validate_provider_capabilities",
]
