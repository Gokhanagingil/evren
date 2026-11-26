"""Kimlik çözümleme (identification) — ServiceNow IRE semantiği + olasılıksal eşleştirme."""
from .dependent import dependent_keys, entry_signature
from .index import CandidateIndex, CandidateProfile, InMemoryCandidateIndex
from .keys import extract_keys
from .model import Feature, IdentityKeys, Outcome, Resolution, SourceRecord
from .resolver import IdentityEngine, IdentityResolver, InMemoryLinkStore, LinkStore
from .rules import IdentificationRule, IdentifierEntry, RuleSet
from .scoring import MatchThresholds, ScoringWeights, contradictions, jaro_winkler, score

__all__ = [
    "CandidateIndex",
    "CandidateProfile",
    "Feature",
    "IdentificationRule",
    "IdentifierEntry",
    "IdentityEngine",
    "IdentityKeys",
    "IdentityResolver",
    "InMemoryCandidateIndex",
    "InMemoryLinkStore",
    "LinkStore",
    "MatchThresholds",
    "Outcome",
    "Resolution",
    "RuleSet",
    "ScoringWeights",
    "SourceRecord",
    "contradictions",
    "dependent_keys",
    "entry_signature",
    "extract_keys",
    "jaro_winkler",
    "score",
]
