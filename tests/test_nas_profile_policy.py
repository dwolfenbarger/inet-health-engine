import importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('retention_nas',Path(__file__).resolve().parents[1]/'scripts'/'retention-nas-pilot.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class NasProfilePolicyTests(unittest.TestCase):
    def test_missing_mount_refuses_local_fallback(self):
        reply=json.dumps({'filesystems':[{'source':'/dev/local','fstype':'ext4','target':'/'}]})
        with patch.object(module.socket,'gethostname',return_value='proxmox1'),patch.object(module,'run',return_value=reply):
            with self.assertRaisesRegex(RuntimeError,'local fallback'):module.mount_check()
    def test_symlink_inside_namespace_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'archive';root.mkdir();(root/'escape').symlink_to(Path(tmp),target_is_directory=True)
            with patch.object(module,'ROOT',root):
                with self.assertRaisesRegex(RuntimeError,'Symlink'):module.size_guard()
    def test_profile_size_cap_counts_sparse_file_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with (root/'oversize').open('wb') as output:output.truncate(6*1024**3)
            with patch.object(module,'ROOT',root):
                with self.assertRaisesRegex(RuntimeError,'budget'):module.size_guard()
