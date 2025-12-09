"""
Context Manager for NL to Python with LangGraph Integration
Standalone module for disambiguation and conversation management

Usage:
    from services.context_manager import (
        DisambiguationManager,
        ConversationMemory,
        TemporalDetector,
        UserDisambiguationRequired,
        ContextManagerState
    )

Author: Aniket
Date: 3/12/2025
"""

from .schemas import (
    UserDisambiguationRequired,
    ContextManagerState,
    DisambiguationCandidate,
    DisambiguationRequest,
    normalize_cache_key,
    build_cache_key,
    extract_cache_key_parts
)

from .temporal_detector import TemporalDetector
from .disambiguation_manager import DisambiguationManager
from .conversation_memory import ConversationMemory


__all__ = [
    # Exception
    'UserDisambiguationRequired',

    # State Types
    'ContextManagerState',
    'DisambiguationCandidate',
    'DisambiguationRequest',

    # Managers
    'DisambiguationManager',
    'ConversationMemory',
    'TemporalDetector',

    # Utilities
    'normalize_cache_key',
    'build_cache_key',
    'extract_cache_key_parts',
]


# Version info
__version__ = '1.0.0'
__author__ = 'Aniket'
__description__ = 'LangGraph-based Context Manager for NL to Python'




