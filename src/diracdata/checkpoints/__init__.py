"""diracdata.checkpoints -- durable conversation continuity (transcript.md + running summary.md).

`Conversation` is the unit of continuity; it persists through a `CheckpointBackend` (any diracdata
store works today; Postgres/Redis backends are stubs behind optional extras). Import lazily so the
stub backends' optional deps are only needed when actually used.
"""

from diracdata.checkpoints.base import CheckpointBackend
from diracdata.checkpoints.conversation import Conversation

__all__ = ["Conversation", "CheckpointBackend", "PostgresCheckpointer", "RedisCheckpointer"]


def __getattr__(name: str):
    if name == "PostgresCheckpointer":
        from diracdata.checkpoints.postgres import PostgresCheckpointer
        return PostgresCheckpointer
    if name == "RedisCheckpointer":
        from diracdata.checkpoints.redis import RedisCheckpointer
        return RedisCheckpointer
    raise AttributeError(name)
