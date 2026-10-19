"""Guardrails: checks around the assistant that mask, block or warn.

One module per guardrail. Every pattern list, threshold and on/off flag lives
in config.py.

- Masking changes the text and carries on (secrets.py, the answer side).
- Blocking stops the request with a fixed reply (input.py, moderation.py,
  injection.py, off-topic routing, rate_limit.py).
- Warning adds information and changes nothing else (commands.py).
"""
