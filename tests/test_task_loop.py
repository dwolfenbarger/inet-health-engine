"""Regressions for clients surviving consecutive Celery task invocations."""
import asyncio
import contextvars
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collectors"))
from collectors import task_loop


class TaskLoopTests(unittest.TestCase):
    def tearDown(self):
        with patch.object(task_loop, "_close_clients", new_callable=AsyncMock):
            task_loop.close_task_loop()

    def test_consecutive_tasks_reuse_loop_bound_client(self):
        # Like asyncpg, a pending Future belongs to the loop that created it.
        client = {}

        async def cycle():
            loop = asyncio.get_running_loop()
            client.setdefault("future", loop.create_future())
            future = client["future"]
            loop.call_soon(future.set_result, "written")
            result = await future
            client["future"] = loop.create_future()
            return result

        self.assertEqual(task_loop.run_async(cycle()), "written")
        self.assertEqual(task_loop.run_async(cycle()), "written")

    def test_old_fresh_loop_pattern_reproduces_failure(self):
        async def make_client():
            return asyncio.get_running_loop().create_future()

        future = asyncio.run(make_client())

        async def reuse_client():
            await future

        with self.assertRaisesRegex(RuntimeError, "different loop"):
            asyncio.run(reuse_client())

    def test_failure_does_not_close_loop_for_next_task(self):
        seen = []

        async def cycle(fail=False):
            seen.append(asyncio.get_running_loop())
            if fail:
                raise ValueError("retry")
            return 7

        with self.assertRaises(ValueError):
            task_loop.run_async(cycle(True))
        self.assertEqual(task_loop.run_async(cycle()), 7)
        self.assertIs(seen[0], seen[1])

    def test_context_is_fresh_for_each_task(self):
        value = contextvars.ContextVar("task_value", default="clean")

        async def cycle():
            old = value.get()
            value.set("previous task")
            return old

        self.assertEqual(task_loop.run_async(cycle()), "clean")
        self.assertEqual(task_loop.run_async(cycle()), "clean")

    def test_shutdown_closes_loop_and_next_use_creates_new_one(self):
        async def current():
            return asyncio.get_running_loop()

        old = task_loop.run_async(current())
        with patch.object(task_loop, "_close_clients", new_callable=AsyncMock) as close:
            task_loop.close_task_loop()
            close.assert_awaited_once()
        self.assertTrue(old.is_closed())
        self.assertIsNot(old, task_loop.run_async(current()))

    def test_new_process_does_not_reuse_parent_runner(self):
        async def current():
            return asyncio.get_running_loop()

        parent_loop = task_loop.run_async(current())
        parent_runner = task_loop._runner
        try:
            with patch.object(task_loop.os, "getpid", return_value=os.getpid() + 1):
                child_loop = task_loop.run_async(current())
                self.assertIsNot(parent_loop, child_loop)
                with patch.object(task_loop, "_close_clients", new_callable=AsyncMock):
                    task_loop.close_task_loop()
        finally:
            parent_runner.close()


if __name__ == "__main__":
    unittest.main()
