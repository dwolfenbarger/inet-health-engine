"""Read-only comparison of fresh-loop versus persistent-loop collector clients."""
import asyncio
import os
from pathlib import Path
import sys

mode, source_root, runtime_env = sys.argv[1:4]
# Use DEV1's existing private runtime configuration without printing values.
for line in Path(runtime_env).read_text().splitlines():
    if line.strip() and not line.lstrip().startswith('#') and '=' in line:
        key, value = line.split('=', 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")
os.chdir(source_root)
sys.path.insert(0, str(Path(source_root) / 'collectors'))
from collectors.db import get_pg_pool, close_pg_pool
from collectors.cache import get_redis, close_redis
from collectors.task_loop import run_async, close_task_loop

async def cycle():
    pool = await get_pg_pool()
    pg = await pool.fetchval('SELECT 1')
    cache = await get_redis()
    redis = await cache.ping()
    return {'postgres': pg == 1, 'redis': redis is True}

run = asyncio.run if mode == 'old' else run_async
try:
    for n in range(1, 4):
        print({'mode': mode, 'cycle': n, 'result': run(cycle())}, flush=True)
except Exception as exc:
    print({'mode': mode, 'error_type': type(exc).__name__,
           'event_loop_failure': 'loop' in str(exc).lower()}, flush=True)
    sys.exit(1)
finally:
    if mode != 'old':
        close_task_loop()
