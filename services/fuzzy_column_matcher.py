"""
Centralized Fuzzy Column Matching Utility

Provides consistent fuzzy string matching for column name resolution across the application.
Uses hybrid approach: character-level fuzzy matching + token-based matching for better results.
"""

from typing import List, Optional, Tuple, Dict
from fuzzywuzzy import fuzz
from master_logger import setup_module_logger

# Initialize logger
logger = setup_module_logger('services.fuzzy_column_matcher')


class FuzzyColumnMatcher:
    """
    Centralized fuzzy matching for column names with debug logging and optimization
    """
    
    def __init__(self, threshold: int = 80, max_length_ratio: float = 2.0):
        """
        Initialize fuzzy matcher with configurable parameters
        
        Args:
            threshold: Minimum similarity score (0-100) to accept a match
            max_length_ratio: Maximum length ratio between query and candidate (optimization)
        """
        self.threshold = threshold
        self.max_length_ratio = max_length_ratio
        logger.info(f"Initialized FuzzyColumnMatcher (threshold={threshold}%, max_length_ratio={max_length_ratio})")
    
    def find_best_match(
        self, 
        query_term: str, 
        available_columns: List[str],
        context: str = "general",
        query_context: Optional[str] = None
    ) -> Optional[str]:
        """
        Find the best matching column using cascading fallback strategy:
        1. Exact match (instant)
        2. Partial substring match (fast)
        3. Fuzzy match (slower, but still fast)
        
        With optional validation layer that checks if core tokens from matched
        column actually appear in the user's query to prevent false positives.
        
        Args:
            query_term: The column name to match
            available_columns: List of actual column names to match against
            context: Description of where this match is happening (for logging)
            query_context: Optional full user query for validation (prevents false positives)
            
        Returns:
            Best matching column name or None if no good match found
        """
        if not query_term or not available_columns:
            return None
        
        # Normalize query term
        query_lower = query_term.lower().strip()
        
        logger.debug(f"[FUZZY_MATCH|{context}] Searching for: '{query_term}'")
        logger.debug(f"[FUZZY_MATCH|{context}] Available columns: {len(available_columns)} columns")
        if query_context:
            logger.debug(f"[FUZZY_MATCH|{context}] Query context provided for validation: '{query_context[:50]}...'")
        
        # Step 1: Try exact match (instant, no overhead)
        for col in available_columns:
            if col.lower() == query_lower:
                logger.debug(f"[FUZZY_MATCH|{context}] EXACT match: '{query_term}' -> '{col}'")
                # Validate the match if query context is provided
                if query_context:
                    match_score = fuzz.ratio(query_lower, col.lower())
                    if not self._validate_match_with_query(col, query_context, match_score):
                        logger.warning(f"[FUZZY_MATCH|{context}] EXACT match rejected after validation: '{col}'")
                        continue  # Try next column
                return col
        
        # Step 2: Try partial substring match (very fast)
        for col in available_columns:
            col_lower = col.lower()
            if query_lower in col_lower or col_lower in query_lower:
                logger.debug(f"[FUZZY_MATCH|{context}] PARTIAL match: '{query_term}' -> '{col}'")
                # Validate the match if query context is provided
                if query_context:
                    match_score = fuzz.ratio(query_lower, col_lower)
                    if not self._validate_match_with_query(col, query_context, match_score):
                        logger.warning(f"[FUZZY_MATCH|{context}] PARTIAL match rejected after validation: '{col}'")
                        continue  # Try next column
                return col
        
        # Step 3: Try fuzzy matching (slower, but still fast for typical column counts)
        logger.debug(f"[FUZZY_MATCH|{context}] No exact/partial match, trying fuzzy matching...")
        
        best_match = None
        best_score = 0
        top_candidates = []  # For debug logging
        
        for col in available_columns:
            # Optimization: Skip columns with very different lengths (likely wrong)
            if self._should_skip_candidate(query_lower, col):
                continue
            
            # Calculate character-level fuzzy similarity score
            char_score = fuzz.ratio(query_lower, col.lower())
            
            # Calculate token-based similarity score (NEW!)
            token_score = self._calculate_token_similarity(query_lower, col)
            
            # Use hybrid scoring: take the maximum of both approaches
            # This ensures we don't regress on existing matches while improving token-based matching
            score = max(char_score, token_score)
            
            # Track top candidates for logging
            if score > 50:  # Only track reasonable candidates
                top_candidates.append((col, score))
            
            # Update best match
            if score > best_score:
                best_score = score
                best_match = col
        
        # Sort top candidates by score for logging
        top_candidates.sort(key=lambda x: x[1], reverse=True)
        
        # Log top 3 candidates
        if top_candidates:
            top_3 = top_candidates[:3]
            logger.info(f"[FUZZY_MATCH|{context}] Top candidates for '{query_term}':")
            for candidate, score in top_3:
                logger.info(f"  - '{candidate}' (score: {score}%)")
        
        # Handle ambiguous matches with tie-breaker logic
        if len(top_candidates) >= 2:
            best_candidate = top_candidates[0]
            second_best = top_candidates[1]
            
            # Check if scores are very close (within 5%) - ambiguous match
            if abs(best_candidate[1] - second_best[1]) <= 5 and best_candidate[1] >= self.threshold:
                logger.warning(f"[FUZZY_MATCH|{context}] AMBIGUOUS MATCH detected for '{query_term}':")
                logger.warning(f"  Top match: '{best_candidate[0]}' (score: {best_candidate[1]}%)")
                logger.warning(f"  Second: '{second_best[0]}' (score: {second_best[1]}%)")
                
                # Apply tie-breaker rules
                best_match = self._apply_tie_breaker(
                    query_lower,
                    [best_candidate[0], second_best[0]],
                    [best_candidate[1], second_best[1]]
                )
                logger.warning(f"  → Tie-breaker selected: '{best_match}'")
        
        # Accept match only if above threshold
        if best_score >= self.threshold:
            logger.info(f"[FUZZY_MATCH|{context}] FUZZY match: '{query_term}' -> '{best_match}' (score: {best_score}%)")
            
            # Validate that matched column actually exists (safety check)
            if best_match in available_columns:
                # Validate the match if query context is provided
                if query_context and not self._validate_match_with_query(best_match, query_context, best_score):
                    logger.warning(f"[FUZZY_MATCH|{context}] FUZZY match rejected after validation: '{best_match}'")
                    return None
                return best_match
            else:
                logger.error(f"[FUZZY_MATCH|{context}] ERROR: Matched column '{best_match}' not in available columns!")
                return None
        else:
            logger.warning(f"[FUZZY_MATCH|{context}] No match found for '{query_term}' (best score: {best_score}%, threshold: {self.threshold}%)")
            return None
    
    def find_multiple_matches(
        self,
        query_terms: List[str],
        available_columns: List[str],
        context: str = "general"
    ) -> Dict[str, Optional[str]]:
        """
        Find matches for multiple query terms efficiently
        
        Args:
            query_terms: List of column names to match
            available_columns: List of actual column names
            context: Description for logging
            
        Returns:
            Dictionary mapping query_term → matched_column (or None)
        """
        logger.info(f"[FUZZY_MATCH|{context}] Batch matching {len(query_terms)} query terms")
        
        results = {}
        for query_term in query_terms:
            matched = self.find_best_match(query_term, available_columns, context)
            results[query_term] = matched
        
        # Summary logging
        successful = sum(1 for v in results.values() if v is not None)
        logger.info(f"[FUZZY_MATCH|{context}] Batch complete: {successful}/{len(query_terms)} matches found")
        
        return results
    
    def _should_skip_candidate(self, query_lower: str, candidate: str) -> bool:
        """
        Optimization: Skip candidates that are obviously wrong based on length
        
        Args:
            query_lower: Normalized query term
            candidate: Candidate column name
            
        Returns:
            True if candidate should be skipped
        """
        query_len = len(query_lower)
        candidate_len = len(candidate.lower())
        
        if query_len == 0 or candidate_len == 0:
            return True
        
        # Skip if length ratio is too different (e.g., "sales" vs "very_long_column_name_here")
        ratio = max(query_len, candidate_len) / min(query_len, candidate_len)
        
        if ratio > self.max_length_ratio:
            return True
        
        return False
    
    def _split_compound_words(self, token: str) -> List[str]:
        """
        Split compound words based on common database naming patterns.
        Handles cases like: closeddate → [closed, date], createddate → [created, date]
        
        Args:
            token: Single token to split (already lowercase)
            
        Returns:
            List of tokens including original and split parts
        """
        tokens = [token]  # Always keep original
        
        # Common suffixes to split (database/programming conventions)
        suffixes = [
            'date', 'time', 'timestamp', 'datetime', 'ts',
            'flag', 'status', 'indicator', 'ind',
            'count', 'total', 'sum', 'avg', 'amount', 'value',
            'id', 'key', 'code', 'type', 'name'
        ]
        
        # Common action/state prefixes
        prefixes = [
            'created', 'closed', 'modified', 'updated', 'deleted', 
            'assigned', 'opened', 'reopened', 'resolved', 'completed',
            'last', 'first', 'initial', 'final', 'total', 'current'
        ]
        
        # Try suffix splitting (longest match first to handle nested patterns)
        for suffix in sorted(suffixes, key=len, reverse=True):
            if token.endswith(suffix) and len(token) > len(suffix):
                prefix_part = token[:-len(suffix)]
                if len(prefix_part) >= 2:  # Valid prefix (at least 2 chars)
                    tokens.append(prefix_part)
                    tokens.append(suffix)
                    
                    # Recursively check if prefix contains known action/state prefixes
                    for prefix in sorted(prefixes, key=len, reverse=True):
                        if prefix_part.endswith(prefix) and len(prefix_part) > len(prefix):
                            remaining = prefix_part[:-len(prefix)]
                            if len(remaining) >= 2:
                                tokens.append(remaining)
                                tokens.append(prefix)
                        elif prefix_part.startswith(prefix) and len(prefix_part) > len(prefix):
                            remaining = prefix_part[len(prefix):]
                            if len(remaining) >= 2:
                                tokens.append(prefix)
                                tokens.append(remaining)
                    
                    # Only process first matching suffix
                    break
        
        # Remove duplicates while preserving order
        seen = set()
        unique_tokens = []
        for t in tokens:
            if t not in seen and len(t) > 0:
                seen.add(t)
                unique_tokens.append(t)
        
        return unique_tokens
    
    def _tokenize(self, text: str) -> List[str]:
        """
        Tokenize a column name by splitting on underscores, spaces, camelCase,
        and compound words (e.g., closeddate → closed, date)
        
        Args:
            text: Column name to tokenize
            
        Returns:
            List of lowercase tokens
        """
        import re
        
        # Replace underscores and spaces with delimiter
        text = text.replace('_', ' ').replace('-', ' ')
        
        # Split camelCase (e.g., "outboundEmail" -> "outbound Email")
        text = re.sub('([a-z])([A-Z])', r'\1 \2', text)
        
        # Split and filter empty tokens
        tokens = [t.lower().strip() for t in text.split() if t.strip()]
        
        # NEW: Split compound words for each token
        all_tokens = []
        for token in tokens:
            compound_tokens = self._split_compound_words(token)
            all_tokens.extend(compound_tokens)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_tokens = []
        for t in all_tokens:
            if t not in seen:
                seen.add(t)
                unique_tokens.append(t)
        
        return unique_tokens
    
    def _normalize_token(self, token: str) -> str:
        """
        Normalize a token to handle singular/plural forms and common variations
        This is completely generic and works for any English word
        
        Args:
            token: Token to normalize
            
        Returns:
            Normalized token
        """
        token = token.lower().strip()
        
        # Handle plural → singular transformations (generic English rules)
        if len(token) > 3:  # Avoid normalizing very short words
            # Handle 'ies' → 'y' (e.g., countries → country, quantities → quantity)
            if token.endswith('ies'):
                return token[:-3] + 'y'
            # Handle 'es' → '' (e.g., boxes → box, emails → email)
            elif token.endswith('es') and len(token) > 4:
                # Check if it's not a word ending naturally in 'es' (like 'sales', 'jones')
                base = token[:-2]
                # If removing 'es' leaves a valid-looking base, do it
                if not base.endswith('e'):  # Avoid 'sales' → 'sal'
                    return base
            # Handle 's' → '' (e.g., products → product, counts → count, tickets → ticket)
            elif token.endswith('s') and not token.endswith('ss'):
                return token[:-1]
        
        return token
    
    def _calculate_token_similarity(self, query: str, candidate: str) -> int:
        """
        Calculate similarity score based on token matching
        Handles cases like 'total_outbound_emails' vs 'outbound_email_count'
        
        Args:
            query: Query column name
            candidate: Candidate column name
            
        Returns:
            Similarity score (0-100)
        """
        query_tokens = self._tokenize(query)
        candidate_tokens = self._tokenize(candidate)
        
        if not query_tokens or not candidate_tokens:
            return 0
        
        # Normalize tokens to handle singular/plural (emails → email, counts → count)
        query_tokens_normalized = [self._normalize_token(t) for t in query_tokens]
        candidate_tokens_normalized = [self._normalize_token(t) for t in candidate_tokens]
        
        # Calculate exact token matches (on normalized tokens)
        query_set = set(query_tokens_normalized)
        candidate_set = set(candidate_tokens_normalized)
        exact_matches = query_set & candidate_set
        
        # If we have exact token matches, that's a strong signal
        if exact_matches:
            # Jaccard similarity for exact matches
            jaccard = len(exact_matches) / len(query_set | candidate_set)
            exact_score = int(jaccard * 100)
        else:
            exact_score = 0
        
        # Calculate fuzzy token matches (handles typos, variations)
        fuzzy_matches = 0
        fuzzy_match_weights = 0
        total_weight = 0
        
        for q_token in query_tokens_normalized:
            best_token_score = 0
            for c_token in candidate_tokens_normalized:
                # Use character-level fuzzy matching on individual tokens
                token_score = fuzz.ratio(q_token, c_token)
                if token_score > best_token_score:
                    best_token_score = token_score
            
            # Weight the match score by how good it is
            # Tokens that match >= 80% contribute their full score
            # Even partial matches contribute proportionally
            if best_token_score >= 80:
                fuzzy_matches += 1
                fuzzy_match_weights += best_token_score
            elif best_token_score >= 60:
                # Partial credit for decent matches
                fuzzy_match_weights += best_token_score * 0.8
            else:
                fuzzy_match_weights += best_token_score * 0.3
            
            total_weight += 100  # Maximum possible score per token
        
        # Calculate weighted fuzzy token match ratio
        if total_weight > 0:
            fuzzy_score = int(fuzzy_match_weights / total_weight * 100)
        else:
            fuzzy_score = 0
        
        # Use the better of exact or fuzzy token matching
        token_score = max(exact_score, fuzzy_score)
        
        # Boost score if core tokens match (ignore common aggregation prefixes/suffixes)
        # These are generic terms used across all domains and datasets
        common_prefixes = {'total', 'sum', 'avg', 'count', 'number', 'distinct', 'unique', 'mean'}
        common_suffixes = {'count', 'amount', 'value', 'sum', 'total', 'number', 'qty', 'quantity'}
        
        # Filter out common prefix/suffix tokens (using normalized tokens)
        query_core = [t for t in query_tokens_normalized if t not in common_prefixes and t not in common_suffixes]
        candidate_core = [t for t in candidate_tokens_normalized if t not in common_prefixes and t not in common_suffixes]
        
        # If core tokens have high overlap, boost the score significantly
        if query_core and candidate_core:
            # Check both exact and fuzzy matches for core tokens
            core_match_score = 0
            matched_core_tokens = 0
            
            for q_core in query_core:
                best_core_match = 0
                for c_core in candidate_core:
                    # Use fuzzy matching to handle minor variations
                    core_sim = fuzz.ratio(q_core, c_core)
                    if core_sim > best_core_match:
                        best_core_match = core_sim
                
                # Count strong matches (>= 85% for normalized tokens)
                if best_core_match >= 85:
                    core_match_score += 1
                    matched_core_tokens += 1
                # Give partial credit for good matches (70-84%)
                elif best_core_match >= 70:
                    core_match_score += 0.5
            
            if core_match_score > 0:
                # Calculate what percentage of core tokens matched
                core_ratio = core_match_score / max(len(query_core), len(candidate_core))
                
                # If most/all core tokens match, this is a strong indicator
                # Boost by up to 25 points for perfect core token alignment
                boost = int(core_ratio * 25)
                
                # Extra boost if ALL core tokens matched perfectly
                if matched_core_tokens == len(query_core) and matched_core_tokens == len(candidate_core):
                    boost += 5  # Additional 5 points for perfect core match
                
                token_score = min(100, token_score + boost)
        
        return token_score
    
    def _apply_tie_breaker(
        self,
        query: str,
        candidates: List[str],
        scores: List[int]
    ) -> str:
        """
        Apply tie-breaker rules when multiple candidates have similar scores
        
        Priority:
        1. Token order match (exact order of tokens)
        2. Shorter name (less likely to have extra unrelated words)
        3. Length similarity to query
        4. First occurrence (deterministic)
        
        Args:
            query: Query term (already lowercase)
            candidates: List of candidate column names (2 or more)
            scores: Corresponding scores
            
        Returns:
            Best candidate after applying tie-breaker
        """
        if len(candidates) == 1:
            return candidates[0]
        
        query_tokens = self._tokenize(query)
        
        # Rule 1: Prefer exact token order match
        for candidate in candidates:
            candidate_tokens = self._tokenize(candidate)
            
            # Check if query tokens appear in same order in candidate
            if self._has_token_order_match(query_tokens, candidate_tokens):
                logger.info(f"[TIE_BREAKER] Selected '{candidate}' (token order match)")
                return candidate
        
        # Rule 2: Prefer shorter name (less noise)
        candidates_with_length = [(c, len(c)) for c in candidates]
        candidates_with_length.sort(key=lambda x: x[1])
        shortest = candidates_with_length[0][0]
        
        # Only use length if there's a meaningful difference (>20% shorter)
        second_shortest = candidates_with_length[1][0] if len(candidates_with_length) > 1 else shortest
        if len(shortest) < len(second_shortest) * 0.8:
            logger.info(f"[TIE_BREAKER] Selected '{shortest}' (shorter name: {len(shortest)} vs {len(second_shortest)} chars)")
            return shortest
        
        # Rule 3: Prefer similar length to query
        query_len = len(query)
        candidates_with_len_diff = [(c, abs(len(c) - query_len)) for c in candidates]
        candidates_with_len_diff.sort(key=lambda x: x[1])
        
        logger.info(f"[TIE_BREAKER] Selected '{candidates_with_len_diff[0][0]}' (length similarity to query)")
        return candidates_with_len_diff[0][0]
    
    def _has_token_order_match(self, query_tokens: List[str], candidate_tokens: List[str]) -> bool:
        """
        Check if query tokens appear in the same order in candidate tokens
        Uses normalized tokens for better matching
        
        Example:
            query: ["total", "sales"] 
            candidate: ["total", "sales", "amount"] → True (same order)
            candidate: ["sales", "total"] → False (different order)
        """
        if not query_tokens or not candidate_tokens:
            return False
        
        # Normalize tokens for comparison
        query_normalized = [self._normalize_token(t) for t in query_tokens]
        candidate_normalized = [self._normalize_token(t) for t in candidate_tokens]
        
        # Find positions of query tokens in candidate
        positions = []
        for q_token in query_normalized:
            for i, c_token in enumerate(candidate_normalized):
                # Allow fuzzy matching for token order (handles variations)
                if fuzz.ratio(q_token, c_token) >= 85:
                    positions.append(i)
                    break
        
        # If we found all tokens, check if they're in order
        if len(positions) == len(query_normalized):
            # Check if positions are in ascending order
            return positions == sorted(positions)
        
        return False
    
    def _validate_match_with_query(
        self,
        matched_column: str,
        query_context: str,
        initial_match_score: int = 100
    ) -> bool:
        """
        Validate that the matched column is actually relevant to the user's query
        by checking if core tokens from the column name appear in the query.
        
        This prevents false positives like matching "count" → "outbound_email_count"
        when the query doesn't mention "outbound" or "email".
        
        Uses adaptive validation threshold based on initial match quality:
        - High confidence (≥85%): 65% validation threshold (lenient for spelling mistakes)
        - Medium confidence (70-84%): 70% validation threshold (standard)
        - Low confidence (<70%): 85% validation threshold (strict)
        
        Args:
            matched_column: The column name that was matched
            query_context: The full user query
            initial_match_score: The fuzzy match score that led to this column (0-100)
            
        Returns:
            True if validated (core tokens found in query), False otherwise
        """
        if not query_context:
            # If no query context provided, accept match (backward compatibility)
            return True
        
        query_lower = query_context.lower().strip()
        col_lower = matched_column.lower().strip()
        
        logger.debug(f"[MATCH_VALIDATION] Validating match: '{matched_column}' against query: '{query_context}'")
        
        # Define common aggregation terms to filter out
        aggregation_terms = {
            'count', 'sum', 'total', 'avg', 'average', 'mean', 'median',
            'min', 'max', 'distinct', 'unique', 'number', 'qty', 'quantity',
            'num', 'cnt', 'amount', 'value'
        }
        
        # Approach 1: Token splitting (for columns with delimiters)
        # Split by underscores, spaces, camelCase
        tokens_from_splitting = self._tokenize(matched_column)
        core_tokens_split = [
            self._normalize_token(t) 
            for t in tokens_from_splitting 
            if self._normalize_token(t) not in aggregation_terms and len(t) > 2
        ]
        
        logger.debug(f"[MATCH_VALIDATION] Tokens from splitting: {tokens_from_splitting} → Core: {core_tokens_split}")
        
        # Approach 2: Aggregation term stripping (for columns without delimiters)
        # Strip aggregation terms from start/end: emailcount → email, totalsales → sales
        core_tokens_stripped = self._strip_aggregation_terms(col_lower, aggregation_terms)
        
        logger.debug(f"[MATCH_VALIDATION] Tokens from stripping: {core_tokens_stripped}")
        
        # Combine all core tokens from both approaches
        all_core_tokens = set(core_tokens_split + core_tokens_stripped)
        all_core_tokens = [t for t in all_core_tokens if len(t) > 2]  # Filter very short tokens
        
        if not all_core_tokens:
            logger.warning(f"[MATCH_VALIDATION] No core tokens extracted from '{matched_column}', accepting match by default")
            return True
        
        logger.info(f"[MATCH_VALIDATION] All core tokens: {all_core_tokens}")
        
        # Determine adaptive validation threshold based on initial match quality
        if initial_match_score >= 85:
            validation_threshold = 65  # More lenient for high confidence matches (handles spelling mistakes)
        elif initial_match_score >= 70:
            validation_threshold = 70  # Standard threshold for medium confidence
        else:
            validation_threshold = 85  # Strict threshold for low confidence (shouldn't reach here normally)
        
        logger.info(f"[MATCH_VALIDATION] Using adaptive threshold: {validation_threshold}% (initial match score: {initial_match_score}%)")
        
        # Check if ANY core token appears in the query (fuzzy match)
        for core_token in all_core_tokens:
            # Tokenize query to check against
            query_tokens = self._tokenize(query_context)
            query_tokens_normalized = [self._normalize_token(qt) for qt in query_tokens]
            
            # Check for exact match first
            if core_token in query_lower or self._normalize_token(core_token) in query_tokens_normalized:
                logger.info(f"[MATCH_VALIDATION] Core token '{core_token}' found in query (exact)")
                return True
            
            # Check for fuzzy match using adaptive threshold
            for query_token in query_tokens_normalized:
                similarity = fuzz.ratio(core_token, query_token)
                if similarity >= validation_threshold:
                    logger.info(f"[MATCH_VALIDATION] Core token '{core_token}' matches query token '{query_token}' (similarity: {similarity}%, threshold: {validation_threshold}%)")
                    return True
        
        # No core tokens found in query
        logger.warning(f"[MATCH_VALIDATION] No core tokens from '{matched_column}' found in query")
        logger.warning(f"[MATCH_VALIDATION] Core tokens: {all_core_tokens}")
        logger.warning(f"[MATCH_VALIDATION] Query tokens: {query_tokens_normalized}")
        return False
    
    def _strip_aggregation_terms(
        self,
        column_name: str,
        aggregation_terms: set
    ) -> List[str]:
        """
        Strip aggregation terms from column names without delimiters.
        Handles cases like: emailcount → email, totalsales → sales, countemails → emails
        
        Args:
            column_name: Column name to process (already lowercase)
            aggregation_terms: Set of aggregation terms to strip
            
        Returns:
            List of core tokens extracted after stripping
        """
        col = column_name.lower().strip()
        core_tokens = []
        
        # Try stripping each aggregation term from prefix and suffix
        for agg_term in aggregation_terms:
            # Try suffix removal: emailcount → email
            if col.endswith(agg_term) and len(col) > len(agg_term) + 2:
                remaining = col[:-len(agg_term)]
                if remaining:  # Make sure something remains
                    core_tokens.append(remaining)
                    logger.debug(f"[AGG_STRIP] Suffix: '{col}' - '{agg_term}' = '{remaining}'")
            
            # Try prefix removal: totalsales → sales, countemails → emails
            if col.startswith(agg_term) and len(col) > len(agg_term) + 2:
                remaining = col[len(agg_term):]
                if remaining:
                    core_tokens.append(remaining)
                    logger.debug(f"[AGG_STRIP] Prefix: '{agg_term}' + '{col}' = '{remaining}'")
        
        # Normalize the extracted tokens (handle plurals)
        normalized_tokens = [self._normalize_token(t) for t in core_tokens]
        
        return normalized_tokens
    
    def get_match_with_score(
        self,
        query_term: str,
        available_columns: List[str],
        context: str = "general"
    ) -> Tuple[Optional[str], int]:
        """
        Get best match along with its similarity score
        
        Args:
            query_term: Column name to match
            available_columns: Available columns
            context: Context for logging
            
        Returns:
            Tuple of (matched_column, score) or (None, 0)
        """
        if not query_term or not available_columns:
            return None, 0
        
        query_lower = query_term.lower().strip()
        
        # Try exact match
        for col in available_columns:
            if col.lower() == query_lower:
                return col, 100
        
        # Try partial match
        for col in available_columns:
            col_lower = col.lower()
            if query_lower in col_lower or col_lower in query_lower:
                # Calculate score for partial match
                score = fuzz.ratio(query_lower, col_lower)
                return col, score
        
        # Try fuzzy match
        best_match = None
        best_score = 0
        
        for col in available_columns:
            if self._should_skip_candidate(query_lower, col):
                continue
            
            score = fuzz.ratio(query_lower, col.lower())
            if score > best_score:
                best_score = score
                best_match = col
        
        if best_score >= self.threshold and best_match:
            return best_match, best_score
        
        return None, best_score


# Create a global singleton instance for easy import
default_matcher = FuzzyColumnMatcher(threshold=70, max_length_ratio=2.5)  # Lowered to 70% for better misspelling tolerance


def find_column_match(
    query_term: str,
    available_columns: List[str],
    context: str = "general",
    threshold: int = 70,
    query_context: Optional[str] = None
) -> Optional[str]:
    """
    Convenience function for quick fuzzy column matching
    
    Args:
        query_term: Column name to find
        available_columns: List of actual column names
        context: Context description for logging
        threshold: Minimum similarity score (0-100, default 70 for misspelling tolerance)
        query_context: Optional full user query for validation (prevents false positives)
        
    Returns:
        Best matching column or None
    """
    if threshold != 70:
        # Use custom threshold
        matcher = FuzzyColumnMatcher(threshold=threshold)
        return matcher.find_best_match(query_term, available_columns, context, query_context)
    else:
        # Use default singleton
        return default_matcher.find_best_match(query_term, available_columns, context, query_context)

