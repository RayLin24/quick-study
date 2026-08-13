"""Tutorial generation: the document model, evidence assembly and the generation steps.

``schema`` is the single content model everything else produces or renders. ``mermaid_ir``
constrains diagrams. ``evidence`` assembles the per-chapter evidence pack from retrieval,
``prompts`` frames that evidence as untrusted data, ``citations`` keeps the ledger that
decides what may be published, and ``pipeline`` exposes the ordered generation steps the
workflow graph calls.
"""
