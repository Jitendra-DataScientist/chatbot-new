"""
Disambiguation Manager with LangGraph State Integration
Handles column/value disambiguation with query-scoped caching

Key Features:
1. Column name disambiguation (ambiguous column names)
2. Filter value disambiguation (value exists in multiple columns)
3. Temporal keyword skipping (march, q1, etc. never disambiguate)
4. Query-scoped cache (only for current query re-runs)
5. LangGraph state integration (single source of truth)

"""

import logging
from typing import List, Dict, Any, Optional, Tuple
import polars as pl
from fuzzywuzzy import fuzz

from master_logger import setup_module_logger
from services.fuzzy_column_matcher import FuzzyColumnMatcher

from .schemas import (
    UserDisambiguationRequired,
    ContextManagerState,
    DisambiguationCandidate,
    DisambiguationRequest,
    normalize_cache_key,
    build_cache_key
)
from .temporal_detector import TemporalDetector


class DisambiguationManager:
    """
    Manages column/value disambiguation with LangGraph state

    Architecture:
    - Uses LangGraph state for cache (no Redis, no class-level globals)
    - Cache lifetime: Current query only
    - Temporal keywords automatically skipped
    - Raises UserDisambiguationRequired for UI flow

    Integration:
    - nl_to_python: Calls check_column/value_ambiguity
    - app.py: Catches UserDisambiguationRequired, shows 3-button UI
    - app.py: On user selection, updates state cache
    - nl_to_python: Re-runs with cache populated, no exception
    """

    # Disambiguation thresholds
    FUZZY_MATCH_THRESHOLD = 70  # Minimum score for candidate
    MIN_CANDIDATES = 2  # Min candidates to trigger disambiguation
    MAX_CANDIDATES = 3  # Max candidates to show in UI

    def __init__(self, fuzzy_threshold: int = 70, use_bert_ner: bool = True):
        """
        Initialize disambiguation manager

        Args:
            fuzzy_threshold: Minimum fuzzy match score (0-100)
            use_bert_ner: Use BERT NER for temporal detection
        """
        self.logger = setup_module_logger('services.context_manager.disambiguation_manager')
        self.fuzzy_matcher = FuzzyColumnMatcher(threshold=fuzzy_threshold)
        self.temporal_detector = TemporalDetector(use_bert_ner=use_bert_ner)
        self.fuzzy_threshold = fuzzy_threshold

    # ========================================================================
    # PUBLIC API
    # ========================================================================

    def check_column_ambiguity(
        self,
        column_name: str,
        available_columns: List[str],
        state: ContextManagerState,
        column_type: str = "column"
    ) -> str:
        """
        Check if column name is ambiguous and get disambiguation

        Args:
            column_name: Column name from query (might be fuzzy match)
            available_columns: List of actual columns in dataframe
            state: LangGraph state with cache
            column_type: Type of column (metric, filter, group_by, etc.)

        Returns:
            Resolved column name (exact match from available_columns)

        Raises:
            UserDisambiguationRequired: If multiple matches and not in cache

        Examples:
            >>> manager.check_column_ambiguity("cnt", ["count", "country", "counter"], state)
            # Raises UserDisambiguationRequired with 3 options
        """
        self.logger.debug(f"[DISAMBIGUATION] Step 1: Checking column '{column_name}' (type: {column_type})")
        self.logger.debug(f"[DISAMBIGUATION] Available columns: {available_columns[:10]}{'...' if len(available_columns) > 10 else ''}")

        # Check cache first
        cache_key = build_cache_key(column_type, column_name)
        self.logger.debug(f"[CACHE] Step 2: Looking up cache key: {cache_key}")
        cached_value = self._check_cache(state, cache_key)

        if cached_value:
            self.logger.info(f"[CACHE] ✅ Hit for {column_type}:'{column_name}' → '{cached_value}'")
            return cached_value

        self.logger.debug(f"[CACHE] ❌ Miss for {cache_key}, proceeding to fuzzy matching")

        # Find matching columns using fuzzy matching
        self.logger.debug(f"[DISAMBIGUATION] Step 3: Finding fuzzy matches for '{column_name}'")
        candidates = self._find_column_matches(column_name, available_columns)
        self.logger.debug(f"[DISAMBIGUATION] Found {len(candidates)} candidates: {[c.label for c in candidates]}")

        # If exactly 1 match, no disambiguation needed
        if len(candidates) == 1:
            self.logger.info(f"[DISAMBIGUATION] ✅ Single match found: '{column_name}' → '{candidates[0].label}'")
            return candidates[0].label

        # If 0 or 2+ matches, need disambiguation
        if len(candidates) == 0:
            self.logger.warning(f"[DISAMBIGUATION] ⚠️  No matches for column '{column_name}'")
            self.logger.debug(f"[DISAMBIGUATION] Searched in: {available_columns[:5]}...")
            # Return original and let downstream handle error
            return column_name

        # Multiple matches - raise disambiguation
        self.logger.info(f"[DISAMBIGUATION] ⚠️  {len(candidates)} matches found, raising disambiguation")
        self.logger.debug(f"[DISAMBIGUATION] Step 4: Preparing disambiguation with candidates: {[(c.label, c.confidence) for c in candidates]}")
        self._raise_disambiguation(
            message=f"Multiple columns match '{column_name}'. Please select:",
            candidates=candidates,
            original_value=column_name,
            column_type=column_type,
            state=state
        )

    def check_value_ambiguity(
        self,
        value: str,
        column_name: str,
        df: pl.DataFrame,
        state: ContextManagerState,
        query: Optional[str] = None
    ) -> str:
        """
        Check if filter value is ambiguous across columns

        Args:
            value: Filter value from query (e.g., "usa", "open")
            column_name: Target column for filtering
            df: DataFrame to search for value
            state: LangGraph state with cache
            query: Full query for temporal context detection

        Returns:
            Grounded value (exact value from column)

        Raises:
            UserDisambiguationRequired: If value exists in multiple columns

        Examples:
            >>> manager.check_value_ambiguity("usa", "country", df, state)
            # Returns "United States" if found in country column

            >>> manager.check_value_ambiguity("march", "create_month", df, state, query)
            # Skips disambiguation (temporal keyword)
        """
        self.logger.debug(f"[DISAMBIGUATION] Step 1: Checking value '{value}' in column '{column_name}'")
        self.logger.debug(f"[DISAMBIGUATION] DataFrame shape: {df.shape}, Query: '{query[:50] if query else 'None'}...'")

        # CRITICAL: Skip temporal keywords
        if self.temporal_detector.should_skip_disambiguation(value, query):
            self.logger.info(f"[TEMPORAL] ⏭️  Skipping disambiguation for temporal keyword: '{value}'")
            return value  # Return as-is, let downstream handle temporal logic

        self.logger.debug(f"[DISAMBIGUATION] Step 2: Not a temporal keyword, proceeding with disambiguation")

        # Check cache
        cache_key = build_cache_key("filter", value)
        self.logger.debug(f"[CACHE] Step 3: Checking cache for key: {cache_key}")
        cached_value = self._check_cache(state, cache_key)

        if cached_value:
            self.logger.info(f"[CACHE] ✅ Hit for filter:'{value}' → '{cached_value}'")
            return cached_value

        self.logger.debug(f"[CACHE] ❌ Miss, proceeding to exact/fuzzy matching")

        # Find value in target column
        self.logger.debug(f"[DISAMBIGUATION] Step 4: Looking for value in column '{column_name}'")
        column_data = df[column_name] if column_name in df.columns else None

        if column_data is None:
            self.logger.warning(f"[DISAMBIGUATION] ⚠️  Column '{column_name}' not found in dataframe")
            self.logger.debug(f"[DISAMBIGUATION] Available columns: {df.columns[:10]}")
            return value

        # Try exact match first
        self.logger.debug(f"[DISAMBIGUATION] Step 5: Trying exact match for '{value}'")
        exact_matches = column_data.filter(pl.col(column_name).cast(str).str.to_lowercase() == value.lower())

        if len(exact_matches) > 0:
            actual_value = exact_matches[0, column_name]
            self.logger.info(f"[DISAMBIGUATION] ✅ Exact match: '{value}' → '{actual_value}'")
            return str(actual_value)

        # Try fuzzy matching
        self.logger.debug(f"[DISAMBIGUATION] Step 6: Exact match failed, trying fuzzy matching")
        unique_values = column_data.unique().drop_nulls().to_list()
        self.logger.debug(f"[DISAMBIGUATION] Column has {len(unique_values)} unique values")
        candidates = self._find_value_matches(value, unique_values, column_name)
        self.logger.debug(f"[DISAMBIGUATION] Found {len(candidates)} fuzzy match candidates")

        if len(candidates) == 0:
            self.logger.warning(f"[DISAMBIGUATION] ⚠️  No matches for value '{value}' in column '{column_name}'")
            self.logger.debug(f"[DISAMBIGUATION] Sample values: {unique_values[:5]}")
            # Raise exception to inform user
            raise ValueError(f"Value '{value}' not found in column '{column_name}'")

        if len(candidates) == 1:
            self.logger.info(f"[DISAMBIGUATION] ✅ Single fuzzy match: '{value}' → '{candidates[0].label}' (score: {candidates[0].confidence})")
            return candidates[0].label

        # Multiple matches - raise disambiguation
        self.logger.info(f"[DISAMBIGUATION] ⚠️  {len(candidates)} fuzzy matches found, raising disambiguation")
        self.logger.debug(f"[DISAMBIGUATION] Step 7: Top candidates: {[(c.label, c.confidence) for c in candidates[:3]]}")
        self._raise_disambiguation(
            message=f"Multiple values match '{value}' in column '{column_name}'. Please select:",
            candidates=candidates,
            original_value=value,
            column_type="filter",
            state=state,
            column_name=column_name
        )

    def update_cache(
        self,
        state: ContextManagerState,
        column_type: str,
        original_value: str,
        selected_value: str
    ) -> ContextManagerState:
        """
        Update disambiguation cache in state

        Called by app.py after user makes selection

        Args:
            state: LangGraph state
            column_type: Type of column (metric, filter, etc.)
            original_value: Original query value
            selected_value: User's selected value

        Returns:
            Updated state with cache entry
        """
        cache_key = build_cache_key(column_type, original_value)

        # Ensure cache dict exists
        if 'disambiguation_cache' not in state or state['disambiguation_cache'] is None:
            state['disambiguation_cache'] = {}

        state['disambiguation_cache'][cache_key] = selected_value

        self.logger.info(f"[CACHE] 💾 WRITE: {cache_key} → {selected_value}")

        return state

    def clear_cache(self, state: ContextManagerState) -> ContextManagerState:
        """
        Clear disambiguation cache (called on new query)

        Args:
            state: LangGraph state

        Returns:
            Updated state with empty cache
        """
        state['disambiguation_cache'] = {}
        self.logger.info("[CACHE] 🗑️  Cleared disambiguation cache")
        return state

    # ========================================================================
    # PRIVATE METHODS
    # ========================================================================

    def _check_cache(self, state: ContextManagerState, cache_key: str) -> Optional[str]:
        """Check if value exists in cache"""
        if 'disambiguation_cache' not in state or state['disambiguation_cache'] is None:
            return None

        return state['disambiguation_cache'].get(cache_key)

    def _find_column_matches(
        self,
        query_column: str,
        available_columns: List[str]
    ) -> List[DisambiguationCandidate]:
        """
        Find columns matching query using fuzzy matching

        Returns:
            List of DisambiguationCandidate sorted by score (desc)
        """
        candidates = []

        for col in available_columns:
            score = max(
                fuzz.ratio(query_column.lower(), col.lower()),
                fuzz.partial_ratio(query_column.lower(), col.lower())
            )

            if score >= self.fuzzy_threshold:
                candidates.append(DisambiguationCandidate(
                    label=col,
                    column=col,
                    confidence=score,
                    actual_value=col
                ))

        # Sort by confidence (descending)
        candidates.sort(key=lambda c: c.confidence, reverse=True)

        return candidates[:self.MAX_CANDIDATES]

    def _find_value_matches(
        self,
        query_value: str,
        unique_values: List[Any],
        column_name: str
    ) -> List[DisambiguationCandidate]:
        """
        Find values matching query using fuzzy matching

        Returns:
            List of DisambiguationCandidate sorted by score (desc)
        """
        candidates = []

        for val in unique_values:
            val_str = str(val)
            score = max(
                fuzz.ratio(query_value.lower(), val_str.lower()),
                fuzz.partial_ratio(query_value.lower(), val_str.lower())
            )

            if score >= self.fuzzy_threshold:
                candidates.append(DisambiguationCandidate(
                    label=val_str,
                    column=column_name,
                    confidence=score,
                    actual_value=val
                ))

        # Sort by confidence (descending)
        candidates.sort(key=lambda c: c.confidence, reverse=True)

        return candidates[:self.MAX_CANDIDATES]

    def _raise_disambiguation(
        self,
        message: str,
        candidates: List[DisambiguationCandidate],
        original_value: str,
        column_type: str,
        state: ContextManagerState,
        column_name: Optional[str] = None
    ):
        """
        Raise UserDisambiguationRequired exception

        This triggers the 3-button UI flow in app.py
        """
        context = {
            'original_value': original_value,
            'column_name': column_name or candidates[0].column,
            'column_type': column_type,
            'session_id': state.get('session_id', 'default'),
            'source_id': state.get('source_id', 'default'),
            'match_type': f'{column_type}_disambiguation'
        }

        self.logger.info(f"[DISAMBIGUATION] 🔘 Raising for {column_type}:'{original_value}'")
        self.logger.info(f"[DISAMBIGUATION] Candidates: {[c.label for c in candidates[:3]]}")

        raise UserDisambiguationRequired(
            message=message,
            suggestions=[c.to_dict() for c in candidates[:3]],
            context=context
        )


