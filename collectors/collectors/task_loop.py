"""One async event loop per Celery prefork child.

Collector modules cache asyncpg/Redis clients. Those clients must keep the same
event loop across task invocations; asyncio.run closes its loop after each call.
This bridge is for synchronous tasks in the configured prefork worker only.
"""
import asyncio
import contextvars
import os

_runner = None
_owner_pid = None


def run_async(coro):
    """Run a collector coroutine without closing the worker child's loop."""
    global _runner, _owner_pid
    pid = os.getpid()
    if _runner is None or _owner_pid != pid:
        # Never run an event loop inherited from a parent process after fork.
        _runner = asyncio.Runner()
        _owner_pid = pid
    return _runner.run(coro, context=contextvars.copy_context())


async def _close_clients():
    from collectors.db import close_pg_pool
    from collectors.cache import close_redis
    await asyncio.gather(close_pg_pool(), close_redis(), return_exceptions=True)


def close_task_loop(**kwargs):
    """Best-effort bounded cleanup on Celery child shutdown."""
    global _runner, _owner_pid
    runner, pid = _runner, _owner_pid
    _runner = _owner_pid = None
    if runner is None or pid != os.getpid():
        return
    try:
        runner.run(asyncio.wait_for(_close_clients(), timeout=2))
    except Exception:
        # The OS releases remaining sockets when the worker child exits.
        pass
    finally:
        runner.close()
