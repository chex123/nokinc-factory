"""Immutable parent ceilings and observed usage; provisioned by a trusted administrator.

These inputs are not authentication. One parent covers a tenant's whole work
item, including every child unit, amendment and model escalation. ARP-1/A04.
"""

from nokinc_factory.domain.review_base import Count, Identifier, Limit, Moment, ReviewModel


class ParentBudget(ReviewModel):
    tenant_id: Identifier
    work_item_id: Identifier
    budget_id: Identifier
    max_invocations: Limit
    max_repairs: Count
    max_tokens: Limit
    max_cost_microusd: Limit
    expires_at: Moment


class ParentUsage(ReviewModel):
    invocations: Count = 0
    repairs: Count = 0
    tokens_spent: Count = 0
    cost_microusd_spent: Count = 0
    tokens_reserved: Count = 0
    cost_microusd_reserved: Count = 0


class ReviewStoreGrant(ReviewModel):
    """Authenticated tenant/worker context injected outside the persistence adapter.

    A network client must never be allowed to manufacture this context. This
    type records the trusted accessor's choice; OIDC/service authentication is
    separate. Database credentials stay outside model-controlled tools.
    """

    tenant_id: Identifier
    subject_id: Identifier