"""Audit trace / event models and logging.

Security events are persisted to public.security_events. Telemetry
never stores raw privileged document text — only identifiers, actor,
action, outcome and a short detail string.
"""