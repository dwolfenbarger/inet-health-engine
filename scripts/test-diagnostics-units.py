"""Focused current-user detector and existing issue contract regressions."""
import os,sys,unittest,json
from pathlib import Path
root=Path(__file__).resolve().parents[1];os.chdir(root);sys.path[:0]=[str(root/'collectors'),str(root/'api')]
suite=unittest.TestSuite()
for pattern in ['test_routing_diagnostics.py','test_diagnostic_status.py','test_issues.py']:
 suite.addTests(unittest.TestLoader().discover(str(root/'tests'),pattern=pattern))
r=unittest.TextTestRunner(verbosity=2).run(suite)
print(json.dumps({'tests':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'pass':r.wasSuccessful()}));assert r.wasSuccessful() and r.testsRun>=27
