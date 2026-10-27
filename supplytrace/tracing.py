"""OpenTelemetry SDK with bounded, redacted local JSONL export; no network exporter."""
import json
import threading
from contextlib import contextmanager
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


class BoundedExporter(SpanExporter):
    def __init__(self, path, limit=16*1024*1024):
        self.path, self.limit, self.lock = path, limit, threading.Lock()
        self.dropped = 0

    def export(self, spans):
        try:
            with self.lock:
                if self.path.exists() and self.path.stat().st_size >= self.limit:
                    self.dropped += len(spans)
                    return SpanExportResult.SUCCESS
                with self.path.open('a', encoding='utf-8') as stream:
                    for span in spans:
                        stream.write(json.dumps({'name':span.name,'trace_id':f'{span.context.trace_id:032x}',
                            'span_id':f'{span.context.span_id:016x}', 'parent_id':f'{span.parent.span_id:016x}' if span.parent else None,
                            'start_ns':span.start_time,'end_ns':span.end_time,'status':span.status.status_code.name,
                            'attributes':dict(span.attributes)}, ensure_ascii=True)+'\n')
            return SpanExportResult.SUCCESS
        except OSError:
            self.dropped += len(spans)
            return SpanExportResult.FAILURE


class Tracing:
    def __init__(self, directory):
        self.exporter = BoundedExporter(directory/'traces.jsonl')
        self.provider = TracerProvider(resource=Resource({'service.name':'supplytrace','service.version':'0.3.0'}), shutdown_on_exit=False)
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.tracer = self.provider.get_tracer('supplytrace.workspace')

    @contextmanager
    def span(self, name, parent='', **attributes):
        context = TraceContextTextMapPropagator().extract({'traceparent':parent}) if parent else None
        # Suppress automatic exception events: exception messages may contain source data.
        with self.tracer.start_as_current_span(name, context=context, record_exception=False,
                                               set_status_on_exception=False, attributes=attributes) as span:
            try:
                yield span
            except BaseException:
                span.set_status(trace.StatusCode.ERROR)
                raise

    @staticmethod
    def parent():
        carrier = {}
        TraceContextTextMapPropagator().inject(carrier)
        return carrier.get('traceparent','')
