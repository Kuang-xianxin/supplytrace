import json,subprocess,threading
from .config import ROOT
from .models import WorkflowError

class MeterlessMemory:
    def __init__(self,runtime):self.runtime=runtime;self.lock=threading.Lock()
    def call(self,op,**values):
        payload={'op':op,'persistDir':str(self.runtime/'hmem'),**values}
        try:
            with self.lock:
                p=subprocess.run(['node','--import','tsx',str(ROOT/'integrations/hmem.ts')],input=json.dumps(payload),
                    cwd=ROOT,text=True,encoding='utf8',capture_output=True,timeout=15,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if p.returncode:raise WorkflowError('meterless_engine_error')
            return json.loads(p.stdout)
        except (OSError,subprocess.TimeoutExpired,ValueError):raise WorkflowError('meterless_unavailable') from None
