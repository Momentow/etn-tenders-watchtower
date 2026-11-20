"""Job handlers by kind, registered by the modules that implement them (the read pipeline, T-017; documents, T-012).

    from watchtower.core.handlers import register

    @register("read")
    async def handle_read(ctx: JobContext) -> None: ...

Until a kind has a handler, its jobs stay queued: nothing is lost, they run once the handler ships.
"""

from watchtower.core.models import JobKind
from watchtower.core.scheduler import Handler

HANDLERS: dict[JobKind, Handler] = {}


def register(kind: JobKind):
    def decorator(fn: Handler) -> Handler:
        if kind in HANDLERS:
            raise ValueError(f"a handler for {kind!r} jobs is already registered")
        HANDLERS[kind] = fn
        return fn

    return decorator
