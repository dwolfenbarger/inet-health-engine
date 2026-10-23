import importlib.util,os,unittest
from pathlib import Path
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('profile_retention',Path(__file__).resolve().parents[1]/'scripts'/'profile-retention.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class ProfilePolicyTests(unittest.TestCase):
    def test_production_host_refused_before_reading_credentials(self):
        with patch.object(module.socket,'gethostname',return_value='inet-health-prod'),patch.object(Path,'read_text') as read:
            with self.assertRaises(SystemExit):module.setup()
            read.assert_not_called()
    def test_development_cannot_point_export_at_remote_database(self):
        with patch.object(module.socket,'gethostname',return_value='inet-health-uat'),patch.object(Path,'read_text',return_value='TIMESCALE_DSN=postgresql://dummy@192.0.2.1/inethealth'),patch.dict(os.environ,{},clear=False):
            with self.assertRaises(SystemExit):module.setup()
