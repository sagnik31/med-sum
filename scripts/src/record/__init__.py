"""
The structured record of a document, and the schemas the model fills in.

- common.py: verbatim text, citations, provenance, corrections
- printed.py: what the model reads (classify and parse outputs)
- document.py: the stored record, with values derived by code
- schemas.py: JSON schema export (Ollama `format`, committed files)
"""

from record.common import SCHEMA_VERSION
from record.document import DocumentRecord, PageTranscription
from record.printed import CLASSIFIER_LABELS, PARSE_MODELS, DocumentType

__all__ = [
    "CLASSIFIER_LABELS",
    "PARSE_MODELS",
    "SCHEMA_VERSION",
    "DocumentRecord",
    "DocumentType",
    "PageTranscription",
]
