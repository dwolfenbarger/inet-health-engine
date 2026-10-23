import gzip,ipaddress,json,struct,tempfile,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from collectors.routing_model import ris_changes,mrt_change,segments,origin
from collectors.routing_mrt import covering_files,WaitingArchive
from collectors.mrt_audit import audit,AuditError

UTC=timezone.utc
OBS={'id':'test','collector':'rrc23','peer_ip':'2001:db8::1','peer_asn':4288000245}
class RoutingTests(unittest.TestCase):
    def frame(self,**patch):
        data={'host':'rrc23','peer':'2001:db8::1','peer_asn':'4288000245','id':'session-1','timestamp':1000,
              'type':'UPDATE','path':[4288000245,13335,13335],
              'announcements':[{'prefixes':['1.1.1.0/24'],'next_hop':'192.0.2.9'}]}
        return {'type':'ris_message','data':{**data,**patch}}
    def test_peer_ip_is_part_of_identity_even_with_same_asn(self):
        with self.assertRaises(ValueError):ris_changes(self.frame(peer='2001:db8::2'),OBS)
    def test_full_asn_prepend_and_multifamily_peer_transport(self):
        at,ident,items=ris_changes(self.frame(),OBS)
        self.assertEqual(items[0]['path'],[4288000245,13335,13335])
        self.assertEqual(items[0]['prefix'],'1.1.1.0/24')
        self.assertEqual(items[0]['attrs']['next_hop'],'192.0.2.9')
    def test_sets_confederations_and_as0_are_evidence_not_invented_paths(self):
        self.assertEqual(segments('174 {13335, 15169}'),[174,[13335,15169]])
        self.assertIsNone(origin(segments('174 {13335,15169}')))
        self.assertEqual(segments('174 0'),[174,0])
        self.assertIsNone(origin(segments('174 0')))
        self.assertEqual(segments('174 (64496 64497) 13335')[1],{'confederation':'(64496 64497)'})
    def test_withdrawal_has_no_inferred_global_origin(self):
        _,_,items=ris_changes(self.frame(path=[],announcements=[],withdrawals=['1.1.1.0/24']),OBS)
        self.assertFalse(items[0]['present']);self.assertIsNone(items[0]['origin_asn'])
    def test_snapshot_time_differs_from_last_change_time(self):
        at=datetime(2026,10,23,tzinfo=UTC)
        raw={'timestamp':1000,'elem_type':'A','peer_ip':OBS['peer_ip'],'peer_asn':OBS['peer_asn'],
             'prefix':'1.1.1.0/24','as_path':'174 13335'}
        row=mrt_change(raw,OBS,snapshot=at)
        self.assertEqual(row['event_at'],at)
        self.assertEqual(row['attrs']['timestamp'],1000)
    def test_archive_hole_is_not_repaired_by_a_later_file(self):
        at=datetime(2026,10,23,tzinfo=UTC)
        files=[{'at':at+timedelta(minutes=n)} for n in [0,10]]
        with self.assertRaises(WaitingArchive):covering_files(files,at,at+timedelta(minutes=10),300)
        files.insert(1,{'at':at+timedelta(minutes=5)})
        self.assertEqual(len(covering_files(files,at+timedelta(seconds=10),at+timedelta(minutes=10),300)),3)
    def record(self,kind,subtype,payload):return struct.pack('!IHHI',1000,kind,subtype,len(payload))+payload
    def write_audit(self,data,peer='192.0.2.1'):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'test.gz';p.write_bytes(gzip.compress(data));return audit(p,peer)
    def test_independent_rib_entry_audit_counts_selected_peer(self):
        def peer(ip):return bytes([2])+b'\x00'*4+ipaddress.ip_address(ip).packed+struct.pack('!I',4288000245)
        index=struct.pack('!IHH',0,0,2)+peer('192.0.2.1')+peer('192.0.2.2')
        rib=struct.pack('!IB',1,24)+bytes([1,1,1])+struct.pack('!H',2)
        rib+=struct.pack('!HIH',0,900,0)+struct.pack('!HIH',1,900,0)
        result=self.write_audit(self.record(13,1,index)+self.record(13,2,rib))
        self.assertTrue(result['selected_peer_present']);self.assertEqual(result['rib_v4'],1)
    def test_ipv6_as4_session_reset_is_visible_to_recovery(self):
        body=struct.pack('!IIHH',4288000245,12654,0,2)
        body+=ipaddress.ip_address(OBS['peer_ip']).packed+ipaddress.ip_address('2001:db8::2').packed+struct.pack('!HH',6,1)
        result=self.write_audit(self.record(17,5,struct.pack('!I',500000)+body),OBS['peer_ip'])
        self.assertEqual(result['state_changes'],[{'time':1000.5,'old':6,'new':1}])
    def test_add_path_and_truncation_do_not_claim_complete_decode(self):
        body=struct.pack('!IIHH',4288000245,12654,0,1)+ipaddress.ip_address('192.0.2.1').packed+ipaddress.ip_address('192.0.2.2').packed
        with self.assertRaises(AuditError):self.write_audit(self.record(16,9,body))
        with self.assertRaises(AuditError):self.write_audit(b'\x00'*5)


class ReplaySchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def test_budget_yields_without_reapplying_committed_archives(self):
        from unittest.mock import AsyncMock,patch
        from collectors.routing_mrt import replay_files,ReplayPending
        pool=AsyncMock();pool.fetchval.return_value=['committed']
        files=[{'url':s} for s in ['committed','next','later']]
        importer=AsyncMock()
        with patch('collectors.routing_mrt.get_pg_pool',AsyncMock(return_value=pool)),patch('collectors.routing_mrt.import_file',importer):
            with self.assertRaises(ReplayPending):
                await replay_files({'id':'test'},{'generation':'test'},files,Path('/tmp'),budget=1)
        self.assertEqual(importer.await_count,1)
        self.assertEqual(importer.await_args.args[2]['url'],'next')
    async def test_graceful_stop_leaves_archive_boundary_unmodified(self):
        import asyncio
        from unittest.mock import AsyncMock,patch
        from collectors.routing_mrt import replay_files,ReplayPending
        pool=AsyncMock();pool.fetchval.return_value=[]
        stop=asyncio.Event();stop.set();importer=AsyncMock()
        with patch('collectors.routing_mrt.get_pg_pool',AsyncMock(return_value=pool)),patch('collectors.routing_mrt.import_file',importer):
            with self.assertRaises(ReplayPending):
                await replay_files({'id':'test'},{'generation':'test'},[{'url':'next'}],Path('/tmp'),stop)
        importer.assert_not_awaited()

if __name__=='__main__':unittest.main()
