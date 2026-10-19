"""The ingest pipeline: source -> validate -> plan -> embed -> apply -> manifest -> report.

Modules: sources (where runbooks come from), chunking (frontmatter + splitting),
validation, manifest, sync (plan / embed / apply), report, lock, run (wires
the steps together). Entry point: python -m runbook_assistant.ingest
"""

# Bump when chunking or metadata code changes in a way that changes the stored
# chunks: a new version forces a full rebuild (see manifest.fingerprint).
# A code constant, not a setting: it describes the code, not the deployment.
PIPELINE_VERSION = 1
