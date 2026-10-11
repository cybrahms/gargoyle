"""Send an SDK trace to scope receive (requires the Python OTLP exporter)."""

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

provider = TracerProvider(resource=Resource.create({"service.name": "example-app"}))
provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)
with tracer.start_as_current_span("hello") as span:
    with tracer.start_as_current_span("do work"):
        span.set_attribute("example.input", "hello world")
    print(f"Trace ID: {span.get_span_context().trace_id:032x}")
provider.shutdown()
