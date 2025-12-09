"""
Context Manager Schemas and Exceptions
LangGraph-based context management for NL to Python disambiguation
"""

from typing import Dict, List, Any, Optional
from typing_extensions import TypedDict
from dataclasses import dataclass


# ============================================================================
# EXCEPTIONS
# ============================================================================

class UserDisambiguationRequired(Exception):
    """
    Raised when user needs to select from multiple column/value options.

    This exception triggers a UI flow where the user is presented with
    3 buttons containing the top suggestions, allowing them to choose
    the correct value/column match.

    Integrates with LangGraph state for caching user choices.
    """

    def __init__(self, message: str, suggestions: List[Dict[str, Any]], context: Dict[str, Any]):
        """
        Initialize disambiguation exception

        Args:
            message: User-friendly message explaining the disambiguation
            suggestions: List of suggestion dicts with keys:
                - label: Display text for the button
                - column: Column name (if applicable)
                - confidence: Match confidence score (0-100)
            context: Additional context dict with keys:
                - original_value: User's original input
                - column_name: Target column name
                - operation_type: Type of operation (filter, metric, group_by)
                - session_id: Current session ID
                - source_id: Data source ID
                - match_type: Type of match (calculated_field, filter_value, etc.)
        """
        self.message = message
        self.suggestions = suggestions
        self.context = context
        super().__init__(message)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict for API response"""
        return {
            'status': 'disambiguation_required',
            'message': self.message,
            'suggestions': self.suggestions,
            'context': self.context
        }


# ============================================================================
# LANGGRAPH STATE SCHEMAS
# ============================================================================

class ContextManagerState(TypedDict, total=False):
    """
    LangGraph state for context management

    This extends the existing QueryProcessingState with context manager fields.
    All fields are optional (total=False) to allow incremental state building.

    Cache Strategy:
    - disambiguation_cache: Query-scoped, cleared on new query
    - conversation_history: Persisted via LangGraph checkpointing (last 5)
    - temporal_entities: Extracted once per query, used to skip disambiguation
    """

    # ========================================================================
    # CORE QUERY DATA
    # ========================================================================
    query: str
    session_id: str
    source_id: str
    workbook_name: Optional[str]

    # ========================================================================
    # DISAMBIGUATION CACHE (Query-scoped)
    # ========================================================================
    disambiguation_cache: Dict[str, str]
    """
    Cache for user's disambiguation choices within current query
    Format: {cache_key: selected_value}

    Cache key format: "{column_type}:{normalized_value}"
    Examples:
        - "metric:tickets" → "Number of Tickets"
        - "filter:usa" → "United States"
        - "status:open" → "Opened: Customer Responded"

    Lifecycle:
    - Created fresh for each new query
    - Populated when user makes disambiguation choice
    - Used on query re-run (after user clicks button)
    - Cleared when new query arrives
    """

    # ========================================================================
    # CONVERSATION HISTORY (LangGraph Checkpoint)
    # ========================================================================
    conversation_history: List[Dict[str, Any]]
    """
    Last 5 queries with extracted entities
    Managed by LangGraph checkpointing for automatic persistence

    Format: [
        {
            'query': 'count of tickets by status',
            'timestamp': '2025-12-03T20:00:00',
            'entities': {
                'metric': 'Number of Tickets',
                'group_by': 'status',
                'filters': []
            }
        },
        ...
    ]
    """

    # ========================================================================
    # CURRENT QUERY ENTITIES
    # ========================================================================
    current_entities: Dict[str, Any]
    """
    Entities extracted from current query
    Used for follow-up detection and context

    Format: {
        'columns': ['status', 'case_id'],
        'filters': [{'column': 'status', 'value': 'open'}],
        'temporal': ['march', 'q1'],
        'metrics': ['Number of Tickets']
    }
    """

    temporal_entities: List[str]
    """
    Temporal keywords extracted from query
    Used to SKIP disambiguation for temporal values

    Examples: ['march', 'q1', '2024', 'monday']

    These should NEVER trigger disambiguation UI
    """

    # ========================================================================
    # FOLLOW-UP DETECTION
    # ========================================================================
    is_followup: bool
    """Whether current query is a follow-up to previous query"""

    followup_context: Optional[Dict[str, Any]]
    """Context inherited from previous query for follow-ups"""


@dataclass
class DisambiguationCandidate:
    """
    Single candidate for disambiguation
    """
    label: str
    column: str
    confidence: float
    actual_value: Any
    sample_values: Optional[List[Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for API"""
        return {
            'label': self.label,
            'column': self.column,
            'confidence': self.confidence
        }


@dataclass
class DisambiguationRequest:
    """
    Request for user disambiguation
    """
    message: str
    candidates: List[DisambiguationCandidate]
    context: Dict[str, Any]

    def to_exception(self) -> UserDisambiguationRequired:
        """Convert to exception for raising"""
        return UserDisambiguationRequired(
            message=self.message,
            suggestions=[c.to_dict() for c in self.candidates[:3]],  # Top 3
            context=self.context
        )


# ============================================================================
# CACHE KEY UTILITIES
# ============================================================================

def normalize_cache_key(value: Any) -> str:
    """
    Normalize cache key for consistent lookups
    Handles capitalization and whitespace variations

    Examples:
        "March" → "march"
        "  March  " → "march"
        "MARCH" → "march"
        "march" → "march"

    Returns:
        Normalized lowercase, trimmed string
    """
    if not isinstance(value, str):
        value = str(value)

    # Convert to lowercase and strip whitespace
    normalized = value.lower().strip()

    # Remove extra internal whitespace (multiple spaces → single space)
    normalized = ' '.join(normalized.split())

    return normalized


def build_cache_key(column_type: str, value: Any) -> str:
    """
    Build cache key from column type and value

    Args:
        column_type: Type of column (metric, filter, group_by, etc.)
        value: Original value from query

    Returns:
        Cache key string: "{column_type}:{normalized_value}"
    """
    normalized_value = normalize_cache_key(value)
    return f"{column_type}:{normalized_value}"


def extract_cache_key_parts(cache_key: str) -> tuple[str, str]:
    """
    Extract column type and value from cache key

    Args:
        cache_key: Cache key string from build_cache_key()

    Returns:
        (column_type, normalized_value)
    """
    if ':' not in cache_key:
        return ('unknown', cache_key)

    parts = cache_key.split(':', 1)
    return (parts[0], parts[1])


