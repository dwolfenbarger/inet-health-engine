import importlib.util,subprocess,unittest
from pathlib import Path
from unittest.mock import patch
from collections import namedtuple
spec=importlib.util.spec_from_file_location('retention_capacity',Path(__file__).resolve().parents[1]/'scripts'/'retention-capacity-guard.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
Disk=namedtuple('Disk','total used free')
class RetentionCapacityTests(unittest.TestCase):
    def test_production_refused(self):
        with patch.object(module.socket,'gethostname',return_value='inet-health-prod'),patch.object(module.subprocess,'run') as run:
            with self.assertRaises(RuntimeError):module.check_once()
            run.assert_not_called()
    def test_free_space_does_not_change_scheduling(self):
        with patch.object(module.socket,'gethostname',return_value='inet-health-uat'),patch.object(module.shutil,'disk_usage',return_value=Disk(0,0,40*1024**3)),patch.object(module.subprocess,'run') as run:
            self.assertEqual(module.check_once()['action'],'observe');run.assert_not_called()
    def test_low_space_stops_only_beat_not_worker(self):
        with patch.object(module.socket,'gethostname',return_value='inet-health-uat'),patch.object(module.shutil,'disk_usage',return_value=Disk(0,0,25*1024**3)),patch.object(module.subprocess,'run') as run:
            self.assertTrue(module.check_once()['paused_future_scheduling']);self.assertEqual(run.call_args.args[0],['systemctl','stop','inet-celery-beat'])
    def test_dry_run_does_not_stop_beat(self):
        with patch.object(module.socket,'gethostname',return_value='inet-health-uat'),patch.object(module.shutil,'disk_usage',return_value=Disk(0,0,25*1024**3)),patch.object(module.subprocess,'run') as run:
            self.assertFalse(module.check_once(dry_run=True)['paused_future_scheduling']);run.assert_not_called()
