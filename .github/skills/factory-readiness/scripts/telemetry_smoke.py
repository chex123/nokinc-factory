"""Check the payments shell and real OTLP receipt, not the missing factory loop.

Run from the payments repository using its virtual environment. HTTP app calls
use ASGI TestClient; OTLP export and Jaeger reads use a real localhost container.
Synthetic spans are emitted directly, never claimed as refund business evidence.
Emission and receipt verification are separate: Jaeger indexing is asynchronous.
An emission result alone is NOT telemetry receipt evidence.
"""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import yaml
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--otlp-port", type=int, required=True)
    parser.add_argument("--jaeger-port", type=int, required=True)
    parser.add_argument("--mode", choices=("emit", "verify"), required=True)
    parser.add_argument("--work-item-id", required=True, help="Unique synthetic ID for this run")
    args = parser.parse_args()
    work_item = args.work_item_id
    if args.mode == "verify":
        verify_receipt(args.jaeger_port, work_item)
        return
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = f"http://127.0.0.1:{args.otlp_port}"
    os.environ["WORK_ITEM_ID"] = work_item
    os.environ["BUILD_ID"] = "audit-local-no-release"
    os.environ["GIT_SHA"] = "audit-unreleased"

    # Environment is fixed before the existing app initializes its tracer.
    from payments.assurance import UndeclaredSpan
    from payments.main import app, assurance

    with TestClient(app) as client:
        health = client.get("/healthz")
        before = client.get("/assurance/coverage").json()
        small_refund = client.post("/refunds", json={"amount": 49.99})
        large_refund = client.post("/refunds", json={"amount": 900.00})
        assert health.status_code == 200

        for name in sorted(assurance.declared_spans):
            with assurance.span(name, synthetic=True):
                pass
        try:
            with assurance.span("audit.undeclared"):
                pass
        except UndeclaredSpan:
            undeclared_blocked = True
        else:
            undeclared_blocked = False
        assert undeclared_blocked
        after = client.get("/assurance/coverage").json()

    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    assert provider.force_flush(timeout_millis=10000), "OTLP exporter did not flush"
    result = {
        "scope": "ASGI app shell plus OTLP emission; NOT end-to-end factory",
        "health_http_status": health.status_code,
        "refund_49_99_http_status": small_refund.status_code,
        "refund_900_http_status": large_refund.status_code,
        "coverage_before_direct_emission": before["complete"],
        "coverage_after_direct_emission": after["complete"],
        "undeclared_span_blocked": undeclared_blocked,
        "work_item_id": work_item,
        "receipt_validation": "NOT_RUN: run verify mode independently",
    }
    print(json.dumps(result, indent=2))
    provider.shutdown()


def verify_receipt(jaeger_port: int, work_item: str) -> None:
    declared = set(yaml.safe_load(Path("spans.declared.yaml").read_text())["spans"])
    params = urlencode({
        "service": "nokinc-demo-payments",
        "tags": json.dumps({"work_item.id": work_item}),
        "limit": "20",
    })
    with urlopen(f"http://127.0.0.1:{jaeger_port}/api/traces?{params}", timeout=10) as response:
        payload = json.load(response)
    received = payload.get("data", [])
    spans = [span for item in received for span in item.get("spans", [])]
    observed = {span["operationName"] for span in spans}
    identity_bound = bool(spans) and all(
        any(tag["key"] == "work_item.id" and tag["value"] == work_item for tag in span["tags"])
        for span in spans
    )
    result = {
        "scope": "Real Jaeger receipt of directly emitted synthetic spans only",
        "work_item_id": work_item,
        "jaeger_trace_count": len(received),
        "trace_ids": sorted(item["traceID"] for item in received),
        "observed_in_jaeger": sorted(observed),
        "work_item_identity_verified": identity_bound,
        "all_declared_spans_received": declared <= observed,
    }
    print(json.dumps(result, indent=2))
    assert result["all_declared_spans_received"], "Jaeger did not return all synthetic spans"
    assert identity_bound, "Received spans are not bound to this synthetic run"


if __name__ == "__main__":
    main()