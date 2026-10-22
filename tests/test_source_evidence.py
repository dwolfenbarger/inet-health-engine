import sys,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'collectors'))
from collectors.public_sources import atlas_record,ioda_record
from collectors.rpki_collector import parse_validations

class SourceEvidenceTests(unittest.TestCase):
    def setUp(self):self.now=datetime.now(timezone.utc)
    def ping(self,**changes):
        return dict(timestamp=self.now.timestamp(),prb_id=123,msm_id=2001,type='ping',af=6,
                    sent=3,rcvd=0,avg=-1,**changes)
    def test_all_loss_is_retained_without_negative_latency(self):
        r=atlas_record(self.ping(),{'type':'ping','af':6},self.now)
        self.assertEqual(r['payload']['loss_percent'],100)
        self.assertIsNone(r['payload']['rtt_ms'])
        self.assertNotIn('asn',r)
    def test_source_ip_is_never_an_asn(self):
        raw=self.ping();raw['from']='2001:db8::1';raw['avg']=0;raw['rcvd']=3
        r=atlas_record(raw,{'type':'ping','af':6},self.now)
        self.assertEqual(r['payload']['source_address'],'2001:db8::1')
        self.assertEqual(r['payload']['rtt_ms'],0)
        self.assertEqual(r['payload']['loss_percent'],0)
    def test_stale_atlas_probe_is_excluded(self):
        raw=self.ping();raw['timestamp']-=7200
        self.assertIsNone(atlas_record(raw,{'type':'ping','af':6},self.now))
    def test_ioda_score_is_preserved_without_probability(self):
        raw={'entity':{'code':'4288000245','type':'asn'},'from':self.now.timestamp(),
             'until':self.now.timestamp()+3600,'score':2473.41,'datasource':'bgp','method':'median'}
        r=ioda_record(raw,'asn')
        self.assertEqual(r['asn'],4288000245)
        self.assertEqual(r['payload']['raw']['score'],2473.41)
        self.assertIsNone(r['payload']['confidence'])
        other={**raw,'until':raw['until']+60}
        self.assertEqual(ioda_record(other,'asn')['id'],r['id'])
    def validation(self,state='valid'):
        return {'generatedTime':self.now.isoformat(),'validated_routes':[{
          'route':{'prefix':'1.1.1.0/24','origin_asn':'AS13335'},
          'validity':{'state':state,'VRPs':{'matched':[]},'reason':'as'}}]}
    def test_rpki_preserves_reason_and_snapshot(self):
        r=parse_validations(self.validation('invalid'),[('1.1.1.0/24',13335)],self.now)[0]
        self.assertEqual(r['payload']['validity']['reason'],'as')
        self.assertEqual(r['observed_at'],self.now)
    def test_rpki_requires_fresh_complete_results(self):
        data=self.validation();data['generatedTime']=(self.now-timedelta(hours=1)).isoformat()
        with self.assertRaises(ValueError):parse_validations(data,[('1.1.1.0/24',13335)],self.now)
        with self.assertRaises(ValueError):parse_validations(self.validation(),[('1.0.0.0/24',13335)],self.now)
        with self.assertRaises(KeyError):parse_validations({},[('1.1.1.0/24',13335)],self.now)
    def test_rpki_does_not_invent_not_found(self):
        with self.assertRaises(ValueError):parse_validations(self.validation('error'),[('1.1.1.0/24',13335)],self.now)

if __name__=='__main__':unittest.main()
