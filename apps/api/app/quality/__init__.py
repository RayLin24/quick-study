"""Hard quality gates for a generated tutorial.

A document that fails one of these is not published. The gates are deliberately
structural rather than judgement calls: citations resolve inside the approved snapshot
scope, code samples parse, diagrams render, and Markdown is well-formed. Anything a model
cannot prove here stays marked as illustrative or unverified.
"""

from app.quality.gates import QualityReport, run_quality_gates

__all__ = ["QualityReport", "run_quality_gates"]
