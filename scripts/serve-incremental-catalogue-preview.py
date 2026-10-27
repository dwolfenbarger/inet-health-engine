"""Loopback-only catalogue qualification; uses the existing service environment."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'api'))
from fastapi import FastAPI
from api.routes_catalogue import router
import uvicorn
app=FastAPI();app.include_router(router,prefix='/api/v1/noc')
if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=8012,log_level='warning')
