"""
Conversation Memory with LangGraph Checkpointing
Manages 5-query conversation history for follow-up question detection

Uses LangGraph checkpointing for automatic persistence - no Redis needed!

"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from master_logger import setup_module_logger
from .schemas import ContextManagerState


class ConversationMemory:
    """
    Manages conversation history using LangGraph checkpointing

    Features:
    - Stores last 5 queries per session
    - Extracts and tracks entities (columns, filters, metrics)
    - Detects follow-up questions
    - Automatic persistence via LangGraph

    Architecture:
    - conversation_history stored in LangGraph state
    - LangGraph checkpointing handles persistence
    - No manual save/load required!
    """

    MAX_HISTORY_SIZE = 5  # Keep last 5 queries

    # Follow-up indicators
    FOLLOWUP_KEYWORDS = {
        'what about', 'how about', 'instead', 'also',
        'same but', 'previous', 'last', 'earlier',
        'that', 'those', 'these', 'this', 'it',
        'show me', 'more', 'less', 'other', 'different'
    }

    def __init__(self):
        """Initialize conversation memory"""
        self.logger = setup_module_logger('services.context_manager.conversation_memory')

    # ========================================================================
    # PUBLIC API
    # ========================================================================

    def add_query(
        self,
        state: ContextManagerState,
        query: str,
        entities: Dict[str, Any],
        success: bool = True
    ) -> ContextManagerState:
        """
        Add query to conversation history

        Args:
            state: LangGraph state
            query: User's query
            entities: Extracted entities (columns, filters, metrics, etc.)
            success: Whether query executed successfully

        Returns:
            Updated state with query added to history

        Note:
            LangGraph checkpointing automatically persists this state
        """
        self.logger.debug(f"[MEMORY] Step 1: Adding query to history: '{query[:50]}...'")

        # Initialize history if needed
        if 'conversation_history' not in state or state['conversation_history'] is None:
            self.logger.debug(f"[MEMORY] Initializing new conversation history")
            state['conversation_history'] = []

        # Create history entry
        entry = {
            'query': query,
            'timestamp': datetime.now().isoformat(),
            'entities': entities,
            'success': success
        }
        self.logger.debug(f"[MEMORY] Step 2: Created entry with {len(entities)} entities, success={success}")

        # Add to history
        state['conversation_history'].append(entry)
        current_size = len(state['conversation_history'])

        # Keep only last N queries
        if current_size > self.MAX_HISTORY_SIZE:
            self.logger.debug(f"[MEMORY] Step 3: Trimming history from {current_size} to {self.MAX_HISTORY_SIZE}")
            state['conversation_history'] = state['conversation_history'][-self.MAX_HISTORY_SIZE:]
            current_size = self.MAX_HISTORY_SIZE

        self.logger.info(f"[MEMORY] 💾 Added query to history (total: {current_size}/{self.MAX_HISTORY_SIZE})")
        self.logger.debug(f"[MEMORY] Entities stored: {list(entities.keys())}")

        return state

    def get_recent_queries(
        self,
        state: ContextManagerState,
        n: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Get recent queries from history

        Args:
            state: LangGraph state
            n: Number of recent queries to return

        Returns:
            List of query entries (most recent first)
        """
        if 'conversation_history' not in state or not state['conversation_history']:
            return []

        # Return last n queries (reverse chronological)
        return list(reversed(state['conversation_history'][-n:]))

    def get_last_query(self, state: ContextManagerState) -> Optional[Dict[str, Any]]:
        """
        Get most recent query

        Args:
            state: LangGraph state

        Returns:
            Last query entry or None if no history
        """
        if 'conversation_history' not in state or not state['conversation_history']:
            return None

        return state['conversation_history'][-1]

    def detect_followup(
        self,
        query: str,
        state: ContextManagerState
    ) -> tuple[bool, Optional[Dict[str, Any]]]:
        """
        Detect if query is a follow-up question

        Args:
            query: Current query
            state: LangGraph state with history

        Returns:
            (is_followup, context_from_previous)

        Examples:
            >>> memory.detect_followup("what about closed tickets?", state)
            (True, {'columns': ['status'], 'filters': [...]})

            >>> memory.detect_followup("count of open tickets", state)
            (False, None)
        """
        self.logger.debug(f"[MEMORY] Step 1: Checking if query is follow-up: '{query}'")
        query_lower = query.lower()

        # Check for follow-up keywords
        self.logger.debug(f"[MEMORY] Step 2: Scanning for follow-up keywords")
        matched_keywords = [kw for kw in self.FOLLOWUP_KEYWORDS if kw in query_lower]
        has_followup_keyword = len(matched_keywords) > 0

        if matched_keywords:
            self.logger.debug(f"[MEMORY] Found follow-up keywords: {matched_keywords}")

        if not has_followup_keyword:
            self.logger.debug(f"[MEMORY] No follow-up keywords found, this is a standalone query")
            return False, None

        # Get last query for context
        self.logger.debug(f"[MEMORY] Step 3: Retrieving last query from history")
        last_query = self.get_last_query(state)

        if not last_query:
            self.logger.debug(f"[MEMORY] No previous query in history, cannot be a follow-up")
            return False, None

        self.logger.debug(f"[MEMORY] Last query: '{last_query.get('query', '')[:50]}...'")

        # Check if query is too short (likely follow-up)
        word_count = len(query.split())
        self.logger.debug(f"[MEMORY] Step 4: Query has {word_count} words")

        if word_count <= 5 and has_followup_keyword:
            self.logger.info(f"[MEMORY] 🔗 Detected follow-up: '{query}' (keywords: {matched_keywords})")
            entities = last_query.get('entities', {})
            self.logger.debug(f"[MEMORY] Context from previous: {list(entities.keys())}")
            return True, entities

        self.logger.debug(f"[MEMORY] Query too long ({word_count} words), likely standalone despite keywords")
        return False, None

    def get_conversation_summary(self, state: ContextManagerState) -> str:
        """
        Generate human-readable conversation summary

        Args:
            state: LangGraph state

        Returns:
            Formatted conversation summary
        """
        recent = self.get_recent_queries(state, n=3)

        if not recent:
            return "No previous conversation history."

        lines = ["Recent queries:"]
        for i, entry in enumerate(recent, 1):
            query = entry['query']
            success_icon = "✓" if entry.get('success') else "✗"
            lines.append(f"{i}. {success_icon} {query}")

        return "\n".join(lines)

    def get_entities_from_history(
        self,
        state: ContextManagerState,
        entity_type: str
    ) -> List[Any]:
        """
        Extract specific entity type from conversation history

        Args:
            state: LangGraph state
            entity_type: Type of entity (columns, filters, metrics, etc.)

        Returns:
            List of entities of specified type from recent history
        """
        entities = []
        recent = self.get_recent_queries(state, n=5)

        for entry in recent:
            entry_entities = entry.get('entities', {})
            if entity_type in entry_entities:
                value = entry_entities[entity_type]
                if isinstance(value, list):
                    entities.extend(value)
                else:
                    entities.append(value)

        # Remove duplicates while preserving order
        seen = set()
        unique_entities = []
        for entity in entities:
            if entity not in seen:
                seen.add(entity)
                unique_entities.append(entity)

        return unique_entities

    def clear_history(self, state: ContextManagerState) -> ContextManagerState:
        """
        Clear conversation history (e.g., on new session)

        Args:
            state: LangGraph state

        Returns:
            Updated state with empty history
        """
        state['conversation_history'] = []
        self.logger.info("[MEMORY] 🗑️  Cleared conversation history")
        return state

    # ========================================================================
    # UTILITY METHODS
    # ========================================================================

    def has_history(self, state: ContextManagerState) -> bool:
        """Check if conversation history exists"""
        return (
            'conversation_history' in state and
            state['conversation_history'] is not None and
            len(state['conversation_history']) > 0
        )

    def get_history_size(self, state: ContextManagerState) -> int:
        """Get current history size"""
        if not self.has_history(state):
            return 0
        return len(state['conversation_history'])

    def enrich_followup_query(
        self,
        query: str,
        context: Dict[str, Any]
    ) -> str:
        """
        Enrich follow-up query with context from previous query

        Args:
            query: Follow-up query (e.g., "what about closed?")
            context: Entities from previous query

        Returns:
            Enriched query with implied context

        Examples:
            >>> memory.enrich_followup_query(
            ...     "what about closed?",
            ...     {'columns': ['status'], 'metric': 'Number of Tickets'}
            ... )
            "count of Number of Tickets where status is closed"
        """
        # This is a placeholder for future enhancement
        # For now, just return original query
        # LLM can use context from state directly
        return query

