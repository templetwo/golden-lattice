"""Usage metering for live calls (eval harness step 9).

MeteredAnthropicClient wraps every Messages call the wire layer makes. It
reads usage and the served model from the final message and hands one record
per call to a sink. It does not change the wire layer or the Session schema:
the per-call record lives in the run's sidecar, not in the protocol.
"""

from __future__ import annotations

from typing import Any, Callable

from golden_lattice.evaluation.cost import call_cost_usd
from golden_lattice.memory_graph.base import ModelId
from golden_lattice.orchestrator.anthropic_client import AnthropicClient


def usage_record(
    *,
    requested_model: str,
    phase: str,
    response: Any,
) -> dict:
    """One sidecar entry: requested vs served model, tokens, and cost."""
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    served = getattr(response, "model", None) or requested_model
    details = getattr(usage, "output_tokens_details", None)
    thinking = getattr(details, "thinking_tokens", None)
    stop_reason = getattr(response, "stop_reason", None)
    billed_input = input_tokens + cache_write + cache_read  # conservative: all at input rate
    return {
        "phase": phase,
        "requested_model": requested_model,
        "served_model": served,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_write_tokens": cache_write,
        "cache_read_tokens": cache_read,
        "thinking_tokens": int(thinking) if isinstance(thinking, int) else None,
        "stop_reason": stop_reason,
        "cost_usd": call_cost_usd(served, billed_input, output_tokens),
    }


class MeteredAnthropicClient(AnthropicClient):
    """AnthropicClient that reports one usage record per Messages call."""

    def __init__(self, *, api_key: str, sink: Callable[[dict], None], **kwargs: Any) -> None:
        super().__init__(api_key=api_key, **kwargs)
        self._sink = sink

    async def _stream_message(self, *, model_id: ModelId, phase: str, **kwargs: Any) -> Any:
        response = await super()._stream_message(model_id=model_id, phase=phase, **kwargs)
        self._sink(
            usage_record(
                requested_model=model_id.value,
                phase=phase,
                response=response,
            )
        )
        return response
