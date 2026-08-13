"""Run and step lifecycle.

``state_machine`` owns the run-level transitions (execution status separate from pipeline
phase); ``steps`` owns the at-least-once execution contract: an idempotency key per unit
of work, a time-boxed lease per attempt, and success recorded only after the side effects.
"""
