"""Trusted provisioning of immutable work budgets and approved review seeds.

Not exposed to models, the public API or the worker database role. Authentication,
policy/quorum checks and verified model qualification precede these operations.
"""

from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from nokinc_factory.adapters.postgres_review_schema import budgets, registrations, require_postgres
from nokinc_factory.domain.review_budget import ParentBudget
from nokinc_factory.domain.review_session import ReviewSeed
from nokinc_factory.policy.review_rules import validate_seed


class ReviewStoreDenied(ValueError):
    """Deterministically denied; no transaction was committed."""


class PostgresReviewAdmin:
    def __init__(self, engine: Engine) -> None:
        require_postgres(engine)
        self._engine = engine

    def provision(self, parent: ParentBudget) -> None:
        parent = ParentBudget.model_validate(parent)
        try:
            with self._engine.begin() as connection:
                connection.execute(text("SELECT set_config('factory.tenant_id', :tenant, true)"),
                                   {"tenant": parent.tenant_id})
                old = connection.scalar(select(budgets.c.limits_json).where(
                    budgets.c.tenant_id == parent.tenant_id,
                    budgets.c.work_item_id == parent.work_item_id,
                ))
                if old is not None:
                    if ParentBudget.model_validate_json(old) != parent:
                        raise ReviewStoreDenied("Existing work-item budget cannot reset or change")
                    return
                connection.execute(budgets.insert().values(
                    tenant_id=parent.tenant_id, budget_id=parent.budget_id,
                    work_item_id=parent.work_item_id, limits_json=parent.model_dump_json(),
                ))
        except IntegrityError as exc:
            raise ReviewStoreDenied("Budget already provisioned; reload authority") from exc

    def register(self, seed: ReviewSeed, parent_budget_id: str) -> None:
        seed = ReviewSeed.model_validate(seed)
        validate_seed(seed)
        scope = seed.scope
        try:
            with self._engine.begin() as connection:
                connection.execute(text("SELECT set_config('factory.tenant_id', :tenant, true)"),
                                   {"tenant": scope.tenant_id})
                parent = connection.scalar(select(budgets.c.budget_id).where(
                    budgets.c.tenant_id == scope.tenant_id,
                    budgets.c.work_item_id == scope.work_item_id,
                    budgets.c.budget_id == parent_budget_id,
                ).with_for_update())
                if parent is None:
                    raise ReviewStoreDenied("No approved parent budget")
                old = connection.execute(select(registrations).where(
                    registrations.c.tenant_id == scope.tenant_id,
                    registrations.c.work_item_id == scope.work_item_id,
                    registrations.c.session_id == scope.session_id,
                )).mappings().one_or_none()
                if old is not None:
                    if old["seed_digest"] != seed.content_digest or old["budget_id"] != parent:
                        raise ReviewStoreDenied("Registered seed/parent cannot change")
                    return
                connection.execute(registrations.insert().values(
                    tenant_id=scope.tenant_id, work_item_id=scope.work_item_id,
                    session_id=scope.session_id, budget_id=parent, seed_digest=seed.content_digest,
                ))
        except IntegrityError as exc:
            raise ReviewStoreDenied("Unit already registered; reload existing authority") from exc