"""A working process must not mask stale input, missing coverage or a failed consumer."""
import unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import AsyncMock
from api.routes_diagnostics import read_status
class DiagnosticStatusTests(unittest.IsolatedAsyncioTestCase):
 async def status(self,*,consumer='waiting',heartbeat_age=0,input_age=0,lag=0,policy_state='running',enabled=True,covered=True,started=True,stream='connected'):
  now=datetime.now(timezone.utc);pool=AsyncMock()
  pool.fetchrow.return_value={'enabled':enabled,'state':policy_state,'checked_at':now,'retention_hours':24}
  intake=now-timedelta(seconds=input_age)
  row={'intake_stream_state':stream,'observer':'fixture','collector':'rrc00','peer_asn':64500,'peer_ip':'192.0.2.1','intake_sequence':100,'intake_received_at':intake,'intake_event_at':intake,'sequence':90 if started else None,'received_at':intake-timedelta(seconds=lag) if started else None,'event_at':intake if started else None,'heartbeat_at':now-timedelta(seconds=heartbeat_age) if started else None,'status':consumer,'error_code':'FixtureFailure' if consumer=='error' else None,'continuity':'{}'}
  pool.fetch.side_effect=[[row] if covered else [],[]]
  return await read_status(pool)
 async def test_current_consumer_is_running_without_claiming_global_health(self):
  result=await self.status();self.assertEqual(result['state'],'running');self.assertTrue(result['limits'])
 async def test_error_is_degraded_even_with_fresh_heartbeat(self):
  result=await self.status(consumer='error');self.assertEqual(result['state'],'degraded');self.assertEqual(result['observers'][0]['error_code'],'FixtureFailure')
 async def test_stopped_consumer_is_stale(self):self.assertEqual((await self.status(heartbeat_age=100))['state'],'stale')
 async def test_stale_input_is_not_running(self):self.assertEqual((await self.status(input_age=200))['state'],'input_stale')
 async def test_fresh_heartbeat_does_not_hide_backlog_age(self):
  result=await self.status(lag=120);self.assertEqual(result['state'],'catching_up');self.assertEqual(result['observers'][0]['processing_lag_seconds'],120)
 async def test_missing_cursor_is_warming(self):self.assertEqual((await self.status(started=False))['state'],'warming')
 async def test_uncovered_collector_is_not_healthy(self):self.assertEqual((await self.status(covered=False))['state'],'not_covered')
 async def test_capacity_pause_is_explicit(self):self.assertEqual((await self.status(policy_state='capacity_paused'))['state'],'capacity_paused')
 async def test_disabled_policy_is_explicit(self):self.assertEqual((await self.status(enabled=False))['state'],'disabled')

 async def test_disconnected_source_is_visible_before_its_receive_clock_goes_stale(self):
  result=await self.status(stream='disconnected');self.assertEqual(result['state'],'input_unavailable');self.assertEqual(result['observers'][0]['input_state'],'disconnected')
