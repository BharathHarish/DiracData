"""diracdata.streaming -- a standalone, provider-agnostic streaming library.

Normalizes any model's stream (Anthropic / OpenAI / Bedrock Converse / OpenAI-compatible like LiteLLM)
into a canonical event envelope, keeping reasoning/thinking separate from the answer, and folds it to a
clean result. Depends only on `diracdata.config` + `diracdata.utils.streaming` (the Sink type) and the
provider message types -- nothing from agents/memory/context. Importable on its own.
"""

from diracdata.streaming.events import EventType, StreamEvent, Usage
from diracdata.streaming.adapters import StreamAdapter, build_adapter, detect_provider
from diracdata.streaming.collector import CollectedResult, Collector, collect
from diracdata.streaming.modes import StreamMode, allowed_kinds, coerce_mode, mode_sink

__all__ = ["EventType", "StreamEvent", "Usage", "StreamAdapter", "build_adapter",
           "detect_provider", "Collector", "CollectedResult", "collect",
           "StreamMode", "mode_sink", "coerce_mode", "allowed_kinds"]
