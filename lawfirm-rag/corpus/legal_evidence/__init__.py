"""Legal-evidence domain package (corpus/legal_evidence).

Typed models + deterministic temporal/citation/status logic mirroring the
0002 legal-evidence migration. Pure logic only (no network, no DB).
"""
from corpus.legal_evidence import models, seed, status, temporal

__all__ = ["models", "seed", "status", "temporal"]