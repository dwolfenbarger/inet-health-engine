import asyncio,unittest
from collectors.routing_stop import request_stop,stop_reason

class RoutingStopTests(unittest.TestCase):
    def test_capacity_cause_survives_later_signal_and_drain(self):
        event=asyncio.Event();request_stop(event,'JournalCapacityPause');request_stop(event,'SignalStop')
        self.assertEqual(stop_reason(event,'GracefulStop'),'JournalCapacityPause')
    def test_signal_stop_remains_the_first_cause(self):
        event=asyncio.Event();request_stop(event,'SignalStop');request_stop(event,'DiskCapacityPause')
        self.assertEqual(stop_reason(event),'SignalStop')
    def test_regular_disconnect_preserves_its_own_reason(self):
        self.assertEqual(stop_reason(asyncio.Event(),'RemoteProtocolError'),'RemoteProtocolError')
