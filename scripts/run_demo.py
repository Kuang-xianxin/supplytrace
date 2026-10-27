"""Run the judge/demo build in the foreground with project-owned configuration."""
import argparse
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',type=Path,default=ROOT/'runtime-demo')
    args=parser.parse_args()
    os.environ['SUPPLYTRACE_RUNTIME']=str(args.runtime.resolve())
    os.environ['SUPPLYTRACE_DISABLE_DOTENV']='false'
    os.environ.pop('SUPPLYTRACE_USE_PROCESS_ENV',None)
    sys.path.insert(0,str(ROOT))
    import uvicorn
    uvicorn.run('supplytrace.app:app',host='127.0.0.1',port=8212,workers=1,access_log=False)
