import importlib.util,json,socket,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
BASE=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod
worker=load('guest_worker',BASE/'scripts/rotate-journal-guest.py')
storage=worker.nas
from collectors.archive_profile import Bundle
class GuardTests(unittest.TestCase):
    def test_mount_identity(self):
        good={'target':str(storage.ROOT),'source':'//192.168.1.60/nas1[/storage/ai/ihealth_storage-recovery-20261101]','fstype':'cifs'}
        marker={'storage_id':storage.STORAGE_ID,'full_raw_access':'development-only'}
        storage.validate_mount(good,marker)
        for change in [{'fstype':'ext4'},{'target':'/'},{'source':'//192.168.1.59/nas0'}]:
            with self.subTest(change=change),self.assertRaises(RuntimeError):storage.validate_mount({**good,**change},marker)
        with self.assertRaises(RuntimeError):storage.validate_mount(good,{**marker,'storage_id':'different'})
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.root_patch=patch.object(storage,'ROOT',self.root);self.root_patch.start()
        self.mount_patch=patch.object(storage,'mount_check',return_value=None);self.mount_patch.start()
        worker.SOURCE_CT=553;worker.SOURCE_NS='prod553';worker.SOURCE_DB='inethealth'
        self.name='routing_messages_p2026102801'
        self.target=self.root/'development/prod553/journals/v2'/self.name
    def tearDown(self):
        self.mount_patch.stop();self.root_patch.stop();self.tmp.cleanup()
    def bundle(self,host=None):
        b=Bundle(self.target,purpose='journal')
        b.add('routing_messages',{'observer':'test','sequence':1,'payload':{'a':1}})
        b.finish({'segment':self.name,'source_host':host or socket.gethostname(),'from_at':'2026-10-28T01:00:00+00:00',
            'until_at':'2026-10-28T02:00:00+00:00','observers':[{'observer':'test','records':1,'first':1,'last':1}],
            'routing_activity':[]})
    def test_verified_existing_segment_ack_then_retire(self):
        self.bundle();calls=[]
        def guest(action,*args,**kw):calls.append(action);return {'retired':True}
        with patch.object(worker,'guest',side_effect=guest):worker.publish({'name':self.name})
        self.assertEqual(calls,['ack','retire'])
    def test_corruption_prevents_ack_and_retirement(self):
        self.bundle()
        next(self.target.glob('*.gz')).write_bytes(b'corrupt')
        with patch.object(worker,'guest') as guest,self.assertRaises(ValueError):worker.publish({'name':self.name})
        guest.assert_not_called()
    def test_recovery_pin_preserves_expired_raw(self):
        self.bundle()
        pin=self.root/'pins/prod553'/self.name
        pin.parent.mkdir(parents=True);pin.write_text('recovery hold')
        with patch.object(worker,'guest',return_value=[{'name':self.name}]),patch.object(worker,'receipt') as receipt:
            worker.expire()
        self.assertTrue((self.target/'manifest.json').exists())
        receipt.assert_not_called()
    def test_wrong_source_prevents_ack_and_retirement(self):
        self.bundle('other-guest')
        with patch.object(worker,'guest') as guest,self.assertRaises(ValueError):worker.publish({'name':self.name})
        guest.assert_not_called()
if __name__=='__main__':unittest.main()
