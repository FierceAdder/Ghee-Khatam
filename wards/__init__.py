"""Secret Wards: an offline, CPU-only, LLM-free privacy firewall for hospital records.

Pipeline order (nothing is redacted before linking, because pseudonyms and per-patient
date shifts depend on the linkage result):

    ingest -> documents -> detect -> learn -> link -> sweep -> redact/privacy -> write
"""

__version__ = "0.5.0"
