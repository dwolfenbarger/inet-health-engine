import gzip,hashlib,json,tempfile,unittest
from pathlib import Path
from collectors.archive_profile import Bundle,JournalCoverage,atomic_json,verify

class ArchiveProfileTests(unittest.TestCase):
    def bundle(self,root,**kwargs):return Bundle(Path(root)/'run',**kwargs)
    def test_full_journal_roundtrip_and_chunking(self):
        with tempfile.TemporaryDirectory() as root:
            b=self.bundle(root,part_raw=80)
            rows=[{'observer':'peer-v6','sequence':i,'payload':{'asn':4294967295,'path':[1,[2,3],1],'prefix':'2001:db8::/32'}} for i in range(1,4)]
            for row in rows:b.add('routing_messages',row)
            b.finish({'journal_expected':{'peer-v6':{'sequence':3,'messages':3}}})
            proof=verify(b.root)
            self.assertEqual(proof['records'],3);self.assertEqual(proof['journal_counts'],{'peer-v6':3})
            self.assertFalse(proof['expiry_eligible']);self.assertEqual(len(b.objects),3)
            restored=[]
            for item in b.objects:
                with gzip.open(b.root/item['file'],'rt') as source:restored.extend(json.loads(line) for line in source)
            self.assertEqual(restored,rows)
    def test_production_refused(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):self.bundle(root,environment='production')
    def test_missing_sequence_and_duplicate_refused(self):
        c=JournalCoverage({'o':{'sequence':2,'messages':2}});c.add({'observer':'o','sequence':1})
        with self.assertRaises(ValueError):c.finish()
        with self.assertRaises(ValueError):c.add({'observer':'o','sequence':1})
    def test_coverage_counter_mismatch_refused(self):
        with self.assertRaises(ValueError):JournalCoverage({'o':{'sequence':2,'messages':1}})
    def test_input_budget_does_not_publish_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            b=self.bundle(root,max_raw=4)
            with self.assertRaises(ValueError):b.add('sample',{'data':'too much'})
            b.abort();self.assertFalse((b.root/'manifest.json').exists())
    def test_corruption_refused(self):
        with tempfile.TemporaryDirectory() as root:
            b=self.bundle(root);b.add('sample',{'data':1});b.finish({})
            p=b.root/b.objects[0]['file'];p.write_bytes(p.read_bytes()[:-3])
            with self.assertRaises(ValueError):verify(b.root)
    def test_traversal_and_symlinks_refused(self):
        with tempfile.TemporaryDirectory() as root:
            b=self.bundle(root);b.add('sample',{'data':1});m=b.finish({})
            m['objects'][0]['file']='../outside.jsonl.gz';atomic_json(b.root/'manifest.json',m)
            with self.assertRaises(ValueError):verify(b.root)
    def test_forged_totals_refused(self):
        with tempfile.TemporaryDirectory() as root:
            b=self.bundle(root);b.add('sample',{'data':1});m=b.finish({});m['raw_bytes']+=1
            atomic_json(b.root/'manifest.json',m)
            with self.assertRaises(ValueError):verify(b.root)
    def test_empty_sample_is_not_health_or_expiry_proof(self):
        with tempfile.TemporaryDirectory() as root:
            b=self.bundle(root);b.finish({'sample_only':True});v=verify(b.root)
            self.assertEqual(v['records'],0);self.assertIsNone(v['ratio']);self.assertFalse(v['expiry_eligible'])

if __name__=='__main__':unittest.main()
