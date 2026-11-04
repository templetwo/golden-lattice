"""Record every completed wire artifact, so a failed run can be salvaged (M2).

RecordingClient wraps any client satisfying the wire protocols. Each completed
submit_* call is kept, with its model and its parsed result, in `completed`. If a
later call fails and the run is abandoned, those artifacts are still on disk.
Nothing is changed: the wrapper returns the inner result unmodified.
"""

from __future__ import annotations

import inspect
from typing import Any

SUBMIT_METHODS = frozenset({
    "submit_investigation_proposal",
    "submit_phase_1_response",
    "submit_self_reflection",
    "submit_cross_reading",
    "submit_phase_2_tagging",
    "submit_phase_3_dialogue",
})
_MODEL_KEYS = ("model_id", "reader_model", "tagger_model", "speaker_model")


def _model_of(kwargs: dict[str, Any]) -> str | None:
    for key in _MODEL_KEYS:
        value = kwargs.get(key)
        if value is not None:
            return getattr(value, "value", str(value))
    return None


def _dump(result: Any) -> Any:
    if isinstance(result, (tuple, list)):
        return [_dump(item) for item in result]
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    return result


class RecordingClient:
    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.completed: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)  # AttributeError propagates for unknown names
        if name not in SUBMIT_METHODS:
            return attr

        async def recorded(*args: Any, **kwargs: Any) -> Any:
            result = attr(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            self.completed.append({
                "method": name,
                "model": _model_of(kwargs),
                "result": _dump(result),
            })
            return result

        return recorded
