"""Citation verification package (work package 5).

Conservative normalization + structured, reproducible citation verification.
Statuses are EXACTLY: verified / weak / conflicting / unavailable.
"""
from citations import normalize, verifier

__all__ = ["normalize", "verifier"]