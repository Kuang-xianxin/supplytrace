"""Size-bounded singleflight cache. Errors/unknown IDs are never cached."""
import json
import random
import threading
import time
from collections import OrderedDict

from .models import WorkflowError


class SingleFlight:
    def __init__(self, entries=128, max_bytes=4*1024*1024, ttl=15, wait=3):
        self.lock = threading.Lock()
        self.values, self.flights = OrderedDict(), {}
        self.entries, self.max_bytes, self.ttl, self.wait = entries, max_bytes, ttl, wait
        self.bytes = self.hits = self.loads = self.waits = 0

    def get(self, key, load):
        with self.lock:
            cached = self.values.get(key)
            if cached and cached[0] > time.monotonic():
                self.hits += 1
                self.values.move_to_end(key)
                return json.loads(cached[1])
            if cached:
                self.bytes -= len(cached[1]); del self.values[key]
            event = self.flights.get(key)
            owner = event is None
            if owner:
                if len(self.flights) >= 32:
                    raise WorkflowError('query_busy', 503)
                event = self.flights[key] = threading.Event()
            else:
                self.waits += 1
        if not owner:
            if not event.wait(self.wait):
                raise WorkflowError('query_timeout', 503)
            with self.lock:
                value = self.values.get(key)
                if value and value[0] > time.monotonic():
                    return json.loads(value[1])
            # No recursive retry avalanche after a failed leader.
            raise WorkflowError('query_unavailable', 503)
        try:
            self.loads += 1
            result = load()
            data = json.dumps(result, ensure_ascii=False).encode()
            if len(data) <= self.max_bytes:
                with self.lock:
                    while self.values and (len(self.values) >= self.entries or self.bytes+len(data)>self.max_bytes):
                        _, old = self.values.popitem(last=False); self.bytes -= len(old[1])
                    self.values[key] = (time.monotonic()+self.ttl*random.uniform(.8,1.2), data)
                    self.bytes += len(data)
            return result
        finally:
            with self.lock:
                self.flights.pop(key, None)
                event.set()

    def snapshot(self):
        with self.lock:
            return {'entries':len(self.values),'bytes':self.bytes,'flights':len(self.flights),
                    'hits':self.hits,'loads':self.loads,'waits':self.waits}
