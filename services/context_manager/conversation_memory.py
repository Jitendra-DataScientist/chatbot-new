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

    MAX_HISTORY_SIZE = 100  # Keep last 100 queries (full session history)

    # Follow-up indicators
    FOLLOWUP_KEYWORDS = {
        'what about', 'how about', 'instead', 'also', 'now',
        'same but', 'previous', 'last', 'earlier', 'again',
        'that', 'those', 'these', 'this', 'it', 'them',
        'show me', 'more', 'less', 'other', 'different',
        'same', 'similarly', 'rather', 'just'
    }

    def __init__(self, llm_client=None):
        """
        Initialize conversation memory
        
        Args:
            llm_client: Optional OpenAI client for LLM-based disambiguation
        """
        self.logger = setup_module_logger('services.context_manager.conversation_memory')
        self.llm_client = llm_client

    # ========================================================================
    # PUBLIC API
    # ========================================================================

    def add_query(
        self,
        state: ContextManagerState,
        query: str,
        entities: Dict[str, Any],
        success: bool = True,
        result_summary: Optional[Dict[str, Any]] = None,
        enriched_query: Optional[str] = None,
        generated_code: Optional[str] = None,
        intent: Optional[str] = None
    ) -> ContextManagerState:
        """
        Add query to conversation history with full context

        Args:
            state: LangGraph state
            query: User's original query
            entities: Extracted entities (columns, filters, metrics, etc.)
            success: Whether query executed successfully
            result_summary: Summary of query results (top N rows, totals, etc.)
            enriched_query: Enriched query if this was a follow-up
            generated_code: Generated pandas/polars code
            intent: Detected intent (e.g., 'top_bottom_analysis')

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

        # Create comprehensive history entry
        entry = {
            'query': query,  # Original user query
            'normalized_query': enriched_query if enriched_query else query,  # Layer 0 normalized output (for follow-ups)
            'enriched_query': enriched_query if enriched_query else query,  # Backward compatibility
            'timestamp': datetime.now().isoformat(),
            'entities': entities,
            'success': success,
            'result_summary': result_summary or {},
            'generated_code': generated_code[:500] if generated_code else None,  # Truncate code
            'intent': intent
        }
        self.logger.debug(f"[MEMORY] Step 2: Created entry with {len(entities)} entities, success={success}, has_result={result_summary is not None}")

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

    def _is_query_incomplete(self, query: str) -> bool:
        """
        Check if query is missing essential components (entity or metric)
        
        Examples of incomplete queries (likely follow-ups):
        - "bottom 20" - has operation but no entity
        - "now top 10" - has operation but no entity  
        - "what about march?" - has time but no metric
        
        Args:
            query: User's query text
            
        Returns:
            True if query appears incomplete
        """
        import re
        query_lower = query.lower()
        
        # Ranking/operation keywords
        ranking_keywords = ['top', 'bottom', 'highest', 'lowest', 'best', 'worst', 
                           'most', 'least', 'maximum', 'minimum', 'max', 'min']
        has_ranking = any(kw in query_lower for kw in ranking_keywords)
        
        # Check for numbers
        has_number = bool(re.search(r'\d+', query))
        
        # Common entity keywords
        entity_keywords = ['country', 'countries', 'product', 'products', 'customer', 
                          'customers', 'ticket', 'tickets', 'order', 'orders',
                          'item', 'items', 'user', 'users', 'account', 'accounts',
                          'case', 'cases', 'issue', 'issues', 'agent', 'agents']
        has_entity = any(ent in query_lower for ent in entity_keywords)
        
        # Common metric keywords  
        metric_keywords = ['count', 'sum', 'average', 'avg', 'total', 'revenue',
                          'sales', 'cost', 'price', 'amount', 'volume', 'value']
        has_metric = any(met in query_lower for met in metric_keywords)
        
        # If has operation + number but NO entity → likely followup
        if has_ranking and has_number and not has_entity:
            self.logger.debug(f"[MEMORY] Query incomplete: has ranking+number but no entity")
            return True
        
        # If has operation but NO metric → likely followup
        if has_ranking and not has_metric:
            self.logger.debug(f"[MEMORY] Query incomplete: has ranking but no metric")
            return True
        
        # Very short queries (< 4 words) are often followups
        word_count = len(query.split())
        if word_count < 4:
            self.logger.debug(f"[MEMORY] Query very short ({word_count} words)")
            return True
            
        return False

    def detect_followup(
        self,
        query: str,
        state: ContextManagerState
    ) -> tuple[bool, Optional[Dict[str, Any]]]:
        """
        Detect if query is a follow-up question using smart context matching

        Args:
            query: Current query
            state: LangGraph state with history

        Returns:
            (is_followup, context_from_previous)

        Examples:
            >>> memory.detect_followup("what about closed tickets?", state)
            (True, {'primary_entity': 'tickets', ...})

            >>> memory.detect_followup("count of open tickets", state)
            (False, None)
        """
        self.logger.debug(f"[MEMORY] Step 1: Checking if query is follow-up: '{query}'")
        
        # No history = not a follow-up
        if not self.has_history(state):
            self.logger.debug(f"[MEMORY] No history, cannot be a follow-up")
            return False, None

        query_lower = query.lower()
        word_count = len(query.split())

        # ═══════════════════════════════════════════════════════
        # TIER 1: HIGH CONFIDENCE - KEYWORD + SHORT QUERY
        # ═══════════════════════════════════════════════════════
        self.logger.debug(f"[MEMORY] Step 2: Checking follow-up keywords")
        matched_keywords = [kw for kw in self.FOLLOWUP_KEYWORDS if kw in query_lower]
        has_followup_keyword = len(matched_keywords) > 0

        if matched_keywords:
            self.logger.debug(f"[MEMORY] Found keywords: {matched_keywords}")

        # Very short query (≤ 5 words) + keyword = use smart matching
        if has_followup_keyword and word_count <= 5:
            self.logger.info(f"[MEMORY] 🔗 FOLLOW-UP detected (Tier 1): keywords={matched_keywords}, words={word_count}")
            best_context = self._find_best_context_match(query, state)
            if best_context:
                self.logger.debug(f"[MEMORY] Found best match from {best_context.get('candidates_checked', 0)} candidates")
                return True, best_context
            else:
                # Fallback to last query
                last_query = self.get_last_query(state)
                return True, last_query.get('entities', {}) if last_query else {}

        # ═══════════════════════════════════════════════════════
        # TIER 2: QUERY INCOMPLETENESS CHECK
        # ═══════════════════════════════════════════════════════
        self.logger.debug(f"[MEMORY] Step 3: Checking if query is incomplete")
        is_incomplete = self._is_query_incomplete(query)
        
        if is_incomplete:
            self.logger.info(f"[MEMORY] 🔗 FOLLOW-UP detected (Tier 2): query incomplete, words={word_count}")
            best_context = self._find_best_context_match(query, state)
            if best_context:
                self.logger.debug(f"[MEMORY] Found best match using smart matching")
                return True, best_context
            else:
                # Fallback to last query
                last_query = self.get_last_query(state)
                return True, last_query.get('entities', {}) if last_query else {}

        # ═══════════════════════════════════════════════════════
        # TIER 3: STANDALONE QUERY
        # ═══════════════════════════════════════════════════════
        self.logger.debug(f"[MEMORY] No follow-up signals detected - treating as standalone query")
        return False, None

    def _find_best_context_match(
        self,
        query: str,
        state: ContextManagerState,
        lookback_window: int = 10
    ) -> Optional[Dict[str, Any]]:
        """
        Find best matching context from recent history using smart scoring
        
        Scoring factors:
        - Recency: More recent queries score higher
        - Entity match: Queries with matching entities score higher
        - Operation match: Queries with same operation type score higher
        
        Args:
            query: Current query text
            state: LangGraph state with history
            lookback_window: How many recent queries to consider
            
        Returns:
            Best matching context or None
        """
        import re
        from datetime import datetime, timedelta
        
        if not self.has_history(state):
            return None
        
        recent_queries = self.get_recent_queries(state, n=lookback_window)
        if not recent_queries:
            return None
        
        query_lower = query.lower()
        
        # Extract entities from current query
        entity_keywords = ['country', 'countries', 'product', 'products', 'customer', 
                          'customers', 'ticket', 'tickets', 'order', 'orders',
                          'item', 'items', 'user', 'users', 'account', 'accounts',
                          'case', 'cases', 'issue', 'issues', 'agent', 'agents']
        current_entities = [ent for ent in entity_keywords if ent in query_lower]
        
        # Check for operation type
        ranking_ops = ['top', 'bottom', 'highest', 'lowest', 'best', 'worst', 'most', 'least']
        has_ranking = any(op in query_lower for op in ranking_ops)
        
        candidates = []
        now = datetime.now()
        
        for i, entry in enumerate(recent_queries):
            score = 0.0
            entry_entities = entry.get('entities', {})
            entry_timestamp = entry.get('timestamp')
            
            # Recency score (0.0 to 1.0, most recent = 1.0)
            if entry_timestamp:
                try:
                    entry_time = datetime.fromisoformat(entry_timestamp)
                    time_diff = (now - entry_time).total_seconds()
                    # Exponential decay: queries in last 60s get high scores
                    recency_score = max(0.0, 1.0 - (time_diff / 300.0))  # 5 min window
                except:
                    recency_score = 1.0 / (i + 1)  # Position-based fallback
            else:
                recency_score = 1.0 / (i + 1)  # Position-based
            
            # Entity match score
            entity_score = 0.0
            entry_primary = entry_entities.get('primary_entity_label', '')
            if current_entities and entry_primary:
                if any(ent in entry_primary.lower() for ent in current_entities):
                    entity_score = 1.0
            
            # Operation match score
            operation_score = 0.0
            entry_operation = entry_entities.get('operation', '')
            if has_ranking and entry_operation == 'ranking':
                operation_score = 1.0
            
            # Weighted final score
            # If entity matches explicitly, boost entity weight
            if entity_score > 0:
                final_score = (recency_score * 0.3) + (entity_score * 0.6) + (operation_score * 0.1)
            else:
                # No explicit entity - recency dominates
                final_score = (recency_score * 0.8) + (operation_score * 0.2)
            
            candidates.append({
                'entry': entry,
                'score': final_score,
                'recency_score': recency_score,
                'entity_score': entity_score,
                'operation_score': operation_score
            })
            
            self.logger.debug(
                f"[MEMORY] Candidate {i}: query='{entry.get('query', '')[:30]}...', "
                f"score={final_score:.2f} (recency={recency_score:.2f}, entity={entity_score:.2f}, op={operation_score:.2f})"
            )
        
        if not candidates:
            return None
        
        # Sort by score descending
        candidates.sort(key=lambda x: x['score'], reverse=True)
        best = candidates[0]
        
        self.logger.info(
            f"[MEMORY] ✅ Best match: '{best['entry'].get('query', '')[:40]}...' "
            f"(score={best['score']:.2f})"
        )
        
        # Return entities from best match, plus metadata
        result = best['entry'].get('entities', {}).copy()
        result['candidates_checked'] = len(candidates)
        result['match_score'] = best['score']
        result['matched_query'] = best['entry'].get('query', '')
        
        return result

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

    async def _llm_disambiguate_reference(
        self,
        query: str,
        candidates: List[Dict[str, Any]]
    ) -> Optional[int]:
        """
        Use LLM to disambiguate which previous query user is referring to
        
        Args:
            query: Current ambiguous query
            candidates: List of candidate previous queries with scores
            
        Returns:
            Index of best matching candidate or None
        """
        if not self.llm_client or not candidates:
            return None
        
        try:
            # Build prompt with candidates
            candidate_text = ""
            for i, candidate in enumerate(candidates[:5], 1):  # Top 5 only
                entry = candidate.get('entry', {})
                candidate_text += f"{i}. \"{entry.get('query', '')}\" (score: {candidate.get('score', 0):.2f})\n"
            
            prompt = f"""Given the current user query and previous queries, determine which previous query the user is most likely referring to.

Current query: "{query}"

Previous queries (most relevant first):
{candidate_text}

Which previous query is the user referring to? Respond with ONLY the number (1-{min(5, len(candidates))}) or "none" if unclear.
Your response:"""

            response = self.llm_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=10
            )
            
            answer = response.choices[0].message.content.strip().lower()
            
            # Parse answer
            if answer.isdigit():
                idx = int(answer) - 1  # Convert to 0-indexed
                if 0 <= idx < len(candidates):
                    self.logger.info(f"[MEMORY] 🤖 LLM selected candidate {idx + 1}")
                    return idx
            
            self.logger.debug(f"[MEMORY] LLM returned unclear answer: {answer}")
            return None
            
        except Exception as e:
            self.logger.warning(f"[MEMORY] LLM disambiguation failed: {e}")
            return None

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
            ...     {'primary_entity': 'tickets', 'metric': 'count'}
            ... )
            "count of tickets where status is closed"
        """
        # This is a placeholder for future enhancement
        # For now, just return original query
        # LLM can use context from state directly
        return query

