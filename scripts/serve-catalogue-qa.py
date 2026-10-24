import os,sys
from pathlib import Path
for line in Path('/home/david/inet-health-engine/.env').read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);os.environ[k.strip()]=v.strip().strip('"').strip("'")
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'api'))
from fastapi import FastAPI
from api.routes_noc import router
import uvicorn
app=FastAPI();app.include_router(router,prefix='/api/v1')
uvicorn.run(app,host='127.0.0.1',port=8011,log_level='warning')
