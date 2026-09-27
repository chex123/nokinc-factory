"""Bound returned executor data without dropping usage or turning truncation into acceptance."""

from pydantic import ValidationError

from nokinc_factory.domain.review_report import RepairReport, ReviewReport
from nokinc_factory.domain.review_session import ExecutionStop, InvocationReceipt
from nokinc_factory.ports.review_execution import ExecutionResult

MAX_OUTPUT_BYTES = 128_000


def normalize_result(value: object) -> tuple[InvocationReceipt | None, str | None,
                                             ExecutionStop | None]:
    if not isinstance(value, ExecutionResult):
        return None, None, "EXECUTION_INVALID"
    try:
        receipt = (InvocationReceipt.model_validate(value.receipt)
                   if value.receipt is not None else None)
    except ValidationError:
        return None, None, "EXECUTION_INVALID"
    try:
        output = value.output
        if isinstance(output, (ReviewReport, RepairReport)):
            output = output.model_dump_json()
        if output is not None and not isinstance(output, str):
            return receipt, None, "EXECUTION_INVALID"
        if output is not None and len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
            return receipt, None, "OUTPUT_LIMIT"
    except (ValueError, TypeError, UnicodeError):
        return receipt, None, "EXECUTION_INVALID"
    return receipt, output, None