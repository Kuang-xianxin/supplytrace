"""Bounded process metrics, without filenames, identities, cells or credentials."""
import threading
from collections import Counter, deque

class Metrics:
    def __init__(self):
        self.lock=threading.Lock();self.latency=deque(maxlen=512);self.counts=Counter()

    def record(self,status,elapsed):
        with self.lock:
            self.counts[str(status//100)+'xx']+=1
            self.latency.append(round(elapsed*1000,2))

    def snapshot(self):
        with self.lock:
            values=sorted(self.latency)
            return {'request_classes':dict(self.counts),'latency_window':len(values),'latency_window_limit':512,
                    'latency_ms':{k:values[min(len(values)-1,int(len(values)*p))] if values else None for k,p in [('p50',.5),('p95',.95),('p99',.99)]},
                    'scope':'current_process; no request-specific labels'}
