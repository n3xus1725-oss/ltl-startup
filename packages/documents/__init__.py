"""Freight documents package."""

from packages.documents.classifier import DocumentClassifier
from packages.documents.normalizer import DocumentNormalizer
from packages.documents.pipeline import DocumentProcessingPipeline
from packages.documents.schemas import (
    NormalizedBOL,
    NormalizedInvoice,
    NormalizedPOD,
    NormalizedRateCon,
    NormalizedScaleTicket,
    ProcessedDocumentResult,
)

__all__ = [
    "DocumentClassifier",
    "DocumentNormalizer",
    "DocumentProcessingPipeline",
    "NormalizedBOL",
    "NormalizedInvoice",
    "NormalizedPOD",
    "NormalizedRateCon",
    "NormalizedScaleTicket",
    "ProcessedDocumentResult",
]
