"""Opaque execution IDs are unique inside their trusted provider namespace."""

from test_review_fixtures import at, begin, finish, model, receipt, report, session

from nokinc_factory.domain.review_base import renewed
from nokinc_factory.policy.review import start_session


def test_equal_opaque_ids_from_distinct_providers_are_not_replays() -> None:
    original = session()
    second_model = renewed(model("second", reviewer=True, family="c"), provider="other-provider")
    s = start_session(
        scope=original.seed.scope, contract=original.seed.contract, policy=original.seed.policy,
        original_doer=original.seed.original_doer,
        reviewers=(*original.seed.reviewers, second_model), artifact=original.artifact, now=at(0),
    )
    first = begin(s, 1)
    done = finish(first, report(first), proof=receipt(first, execution_id="opaque-1"))
    second = begin(done, 2, actor=second_model)
    final = finish(second, report(second), proof=receipt(second, execution_id="opaque-1"))
    assert len(final.valid_completed_reports) == 2
    assert "REPLAY" not in final.terminal_reasons