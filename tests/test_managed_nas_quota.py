import importlib.util,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('nas_quota',Path(__file__).resolve().parents[1]/'scripts/nas-quota.py');nas=importlib.util.module_from_spec(spec);spec.loader.exec_module(nas)
class ManagedQuota(unittest.TestCase):
 def test_reserve_is_not_available_for_admission(self):
  with patch.object(nas,'usage',return_value={'total_bytes':260*nas.GIB,'detail_bytes':0}):
   with self.assertRaises(RuntimeError):nas.admit(1)
 def test_detail_bucket_is_bounded(self):
  with patch.object(nas,'usage',return_value={'total_bytes':120*nas.GIB,'detail_bytes':120*nas.GIB}):
   with self.assertRaises(RuntimeError):nas.admit(1)
 def test_valid_admission(self):
  with patch.object(nas,'usage',return_value={'total_bytes':nas.GIB,'detail_bytes':nas.GIB}):self.assertEqual(nas.admit(nas.GIB)['detail_bytes'],nas.GIB)
 def test_unbounded_publication_is_refused(self):
  for incoming in [-1,nas.GIB+1]:
   with self.assertRaises(ValueError):nas.admit(incoming)
 def test_missing_mount_never_falls_back(self):
  from types import SimpleNamespace
  with patch.object(nas.socket,'gethostname',return_value='proxmox1'),patch.object(nas.subprocess,'run',return_value=SimpleNamespace(stdout='{"filesystems":[{"target":"/","source":"/dev/root","fstype":"ext4"}]}')):
   with self.assertRaises(RuntimeError):nas.mount_check()
