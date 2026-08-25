"""Domain Expert agent. Spec Part 7.

Turns a conversation into a Business Ready story. **No architecture** -- the
Architect runs after Gate 1, and asking this agent to design is the sequencing
defect the spec exists to prevent.

The behaviour that matters most here is REFUSAL. An agent that fills gaps by
assumption produces a story whose assumptions become acceptance criteria, then
tests, then code. By the time anyone notices, the wrong thing is in production
and it is covered by passing tests.

So the contract is: return either a complete story, or the questions that block
one. Never a story with invented answers.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from nokinc_factory.domain.story import BusinessReady

SYSTEM_PROMPT = """\
You are a business analyst eliciting a software change. You produce a Business
Ready story or you ask questions. You never invent an answer.

WHAT YOU MUST ESTABLISH BEFORE A STORY EXISTS

1. The problem, who has it, and what it costs them
2. What is explicitly OUT of scope
3. Acceptance criteria as Gherkin scenarios, INCLUDING at least one failure or
   edge case
4. Business rules in business language -- limits, windows, thresholds
5. Where test data comes from and whether it is sensitive
6. A non-functional target as a NUMBER, or an explicit "no change"
7. Data classification: NONE, PII, PAYMENT, HEALTH or CREDENTIAL

HOW TO BEHAVE

- Ask about the failure case early. People describe the happy path and stop.
  "What should happen when the charge was NOT actually duplicated?"
- Push for numbers. "Fast" is not a target. "p95 under 400ms" is.
- Push for the boundary. "Should this also handle partial refunds?" -- and record
  the answer in scope_out if it is no.
- Ask where the test data comes from. If nobody knows, that is an open question,
  not a detail to fill in later.
- Ask at most three questions per turn. More reads as an interrogation and people
  start answering carelessly.

WHAT YOU MUST NOT DO

- Do not propose a service boundary, an API shape, a database design or a
  technology. That is the Architect's job after Gate 1.
- Do not invent a number, a threshold, a window or a limit. Ask.
- Do not write a story with only happy-path scenarios.
- Do not resolve an ambiguity silently. Put it in open_questions and say so.

If you cannot complete the story, return blocking questions instead. That is a
successful outcome, not a failure.
"""


class BlockingQuestions(BaseModel):
    """Returned instead of a story when unknowns remain.

    Deliberately a distinct type rather than a story with empty fields, so a
    caller cannot mistake an incomplete story for a complete one.
    """

    questions: list[str] = Field(
        description="At most three. Specific and answerable, not open-ended."
    )
    what_is_established: list[str] = Field(
        default_factory=list,
        description="What is already settled, so the person is not asked twice.",
    )


class Elicitation(BaseModel):
    """Either a complete story, or the questions blocking one. Never both."""

    story: BusinessReady | None = None
    blocked_by: BlockingQuestions | None = None

    def model_post_init(self, _: object) -> None:
        if (self.story is None) == (self.blocked_by is None):
            raise ValueError("exactly one of story or blocked_by must be set")


class Conversation(BaseModel):
    """Accumulated state across turns. Injected, never held in the agent."""

    work_item_id: str
    turns: list[str] = Field(default_factory=list)


def _conversation_so_far(ctx: RunContext[Conversation]) -> str:
    if not ctx.deps.turns:
        return "This is the first turn. Nothing has been established yet."
    prior = "\n".join(f"- {t}" for t in ctx.deps.turns)
    return f"Established so far, do not ask about these again:\n{prior}"


def build_agent(
    model: str = "openai:gpt-5.6-terra",
) -> Agent[Conversation, Elicitation]:
    """Construct the agent.

    Deliberately a function, not a module-level singleton. Constructing an Agent
    resolves the provider, which requires credentials -- so a module-level
    instance makes `import nokinc_factory.agents.domain_expert` fail on any
    machine without an API key, including CI and any test that merely imports
    the module.

    `model` is resolved through ModelPort in a real deployment.
    """
    agent: Agent[Conversation, Elicitation] = Agent(
        model,
        deps_type=Conversation,
        output_type=Elicitation,
        system_prompt=SYSTEM_PROMPT,
    )
    agent.system_prompt(_conversation_so_far)
    return agent
