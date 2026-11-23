"""QORVEXA offline evaluation harness (Task 02B foundation).

Runs RCA and retrieval benchmark cases against the Task 02A metric functions and writes
deterministic JSONL results. It is never imported by API request paths.

Modules:
    schemas    pydantic models for benchmark cases, predictions and results (JSONL records)
    jsonl      strict case loading, lenient prediction loading, deterministic writing
    rca        scoring of RCA predictions; adapter from ``RcaAssessment`` to a prediction
    retrieval  running retrieval cases through an injected search function
    runner     run directories with results, summary and manifest
    __main__   command line: ``python -m factorymind.evaluation --help``
"""
