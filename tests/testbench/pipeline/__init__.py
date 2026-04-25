"""Pipeline subsystems for the N.E.K.O. Testbench.

This package collects reusable building blocks that sit between the thin
HTTP routers and the heavy upstream memory / LLM modules:

* :mod:`tests.testbench.pipeline.prompt_builder` — builds a
  :class:`~tests.testbench.pipeline.prompt_builder.PromptBundle` containing
  both a structured human-readable breakdown and the flat ``wire_messages``
  that go out to the model. Consumed by ``chat_router`` (Preview) and, from
  P09 onwards, by ``chat_runner`` (real send).

Later phases will add ``chat_runner`` / ``memory_runner`` /
``simulated_user`` / ``scoring_schema`` / ``judge_runner`` / ... here.
"""
