"""
Temporal Keyword Detector
Reuses existing temporal extraction logic from the codebase

Detects temporal keywords to SKIP disambiguation:
- Month names (january, february, march, ..., jan, feb, mar, ...)
- Quarter names (q1, q2, q3, q4, quarter)
- Day names (monday, tuesday, ..., mon, tue, ...)
- Year patterns (2020, 2021, 2022, 2023, 2024, 2025, ...)


"""

import re
import logging
from typing import List, Set, Optional
from master_logger import setup_module_logger


class TemporalDetector:
    """
    Detects temporal keywords in queries to skip disambiguation

    Reuses temporal logic from:
    - services/enhanced_analysis_service.py (month_mapping)
    - services/period_extraction_service.py (BERT NER - optional)
    - services/nlp_to_python/nl_to_python_schemas.py (TemporalFilter)
    """

    # Comprehensive temporal keyword mappings
    # Source: enhanced_analysis_service.py lines 391-448
    MONTH_FULL_NAMES = {
        'january', 'february', 'march', 'april', 'may', 'june',
        'july', 'august', 'september', 'october', 'november', 'december'
    }

    MONTH_ABBREVIATIONS = {
        'jan', 'feb', 'mar', 'apr', 'may', 'jun',
        'jul', 'aug', 'sep', 'sept', 'oct', 'nov', 'dec'
    }

    QUARTER_KEYWORDS = {
        'q1', 'q2', 'q3', 'q4', 'quarter',
        'quarter 1', 'quarter 2', 'quarter 3', 'quarter 4',
        'first quarter', 'second quarter', 'third quarter', 'fourth quarter'
    }

    DAY_FULL_NAMES = {
        'monday', 'tuesday', 'wednesday', 'thursday',
        'friday', 'saturday', 'sunday'
    }

    DAY_ABBREVIATIONS = {
        'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'
    }

    # Year patterns (recent years for data analysis)
    YEAR_RANGE = range(2015, 2031)  # 2015-2030
    YEAR_KEYWORDS = {str(year) for year in YEAR_RANGE}

    # Relative temporal keywords
    RELATIVE_KEYWORDS = {
        'today', 'yesterday', 'tomorrow',
        'last week', 'this week', 'next week',
        'last month', 'this month', 'next month',
        'last quarter', 'this quarter', 'next quarter',
        'last year', 'this year', 'next year',
        'ytd', 'mtd', 'qtd', 'year to date', 'month to date', 'quarter to date',
        'last n days', 'last n months', 'last n years',
        'recent', 'latest', 'current', 'past', 'previous'
    }

    # All temporal keywords combined
    ALL_TEMPORAL_KEYWORDS = (
        MONTH_FULL_NAMES |
        MONTH_ABBREVIATIONS |
        QUARTER_KEYWORDS |
        DAY_FULL_NAMES |
        DAY_ABBREVIATIONS |
        YEAR_KEYWORDS |
        RELATIVE_KEYWORDS
    )

    def __init__(self, use_bert_ner: bool = True):
        """
        Initialize temporal detector

        Args:
            use_bert_ner: Whether to use BERT NER for advanced extraction
                         (requires period_extraction_service.py)
        """
        self.logger = setup_module_logger('services.context_manager.temporal_detector')
        self.use_bert_ner = use_bert_ner
        self.period_extractor = None

        # Try to load BERT NER if requested
        if use_bert_ner:
            try:
                from services.period_extraction_service import PeriodExtractionService
                self.period_extractor = PeriodExtractionService()
                if self.period_extractor.model_available:
                    self.logger.info("✓ BERT NER loaded for advanced temporal extraction")
                else:
                    self.logger.info("⚠️  BERT NER not available, using keyword matching only")
                    self.period_extractor = None
            except Exception as e:
                self.logger.warning(f"⚠️  Could not load BERT NER: {e}")
                self.period_extractor = None

    def is_temporal_keyword(self, value: str) -> bool:
        """
        Check if a single value is a temporal keyword

        Args:
            value: Value to check (e.g., "march", "q1", "2024")

        Returns:
            True if value is temporal keyword, False otherwise

        Examples:
            >>> detector.is_temporal_keyword("march")
            True
            >>> detector.is_temporal_keyword("q1")
            True
            >>> detector.is_temporal_keyword("usa")
            False
        """
        if not value:
            return False

        # Normalize value
        normalized = value.lower().strip()

        # Check exact keyword match
        if normalized in self.ALL_TEMPORAL_KEYWORDS:
            return True

        # Check year pattern (4 digits, 2015-2030)
        if re.match(r'^20[12][0-9]$', normalized):
            return True

        # Check quarter pattern variations
        if re.match(r'^q[1-4]$', normalized):
            return True

        # Check "last N" patterns
        if re.match(r'^last\s+\d+\s+(day|week|month|quarter|year)s?$', normalized):
            return True

        return False

    def extract_temporal_entities(self, query: str) -> List[str]:
        """
        Extract all temporal entities from query

        Uses multi-strategy approach:
        1. Keyword matching (fast, reliable)
        2. BERT NER (advanced, optional)

        Args:
            query: Natural language query

        Returns:
            List of temporal entities found

        Examples:
            >>> detector.extract_temporal_entities("count of tickets in march")
            ['march']
            >>> detector.extract_temporal_entities("q1 2024 revenue")
            ['q1', '2024']
        """
        temporal_entities = []
        query_lower = query.lower()

        # Strategy 1: Keyword matching
        temporal_entities.extend(self._extract_by_keywords(query_lower))

        # Strategy 2: BERT NER (if available)
        if self.period_extractor and self.period_extractor.model_available:
            temporal_entities.extend(self._extract_by_bert(query))

        # Remove duplicates while preserving order
        seen = set()
        unique_entities = []
        for entity in temporal_entities:
            normalized = entity.lower().strip()
            if normalized not in seen:
                seen.add(normalized)
                unique_entities.append(entity)

        if unique_entities:
            self.logger.info(f"[TEMPORAL] Extracted {len(unique_entities)} temporal entities: {unique_entities}")

        return unique_entities

    def _extract_by_keywords(self, query_lower: str) -> List[str]:
        """
        Extract temporal entities using keyword matching

        Args:
            query_lower: Query in lowercase

        Returns:
            List of matched temporal keywords
        """
        found = []

        # Split query into words
        words = re.findall(r'\b\w+\b', query_lower)

        for word in words:
            if word in self.ALL_TEMPORAL_KEYWORDS:
                found.append(word)

        # Check multi-word patterns (e.g., "last month", "this quarter")
        for keyword in self.RELATIVE_KEYWORDS:
            if keyword in query_lower:
                found.append(keyword)

        return found

    def _extract_by_bert(self, query: str) -> List[str]:
        """
        Extract temporal entities using BERT NER

        Args:
            query: Original query

        Returns:
            List of PERIOD entities from BERT
        """
        try:
            result = self.period_extractor.extract_periods_and_events(query)
            periods = result.get('periods', {})

            # Extract month, quarter, year entities
            temporal_entities = []

            if 'month' in periods and periods['month']:
                temporal_entities.extend(periods['month'])

            if 'quarter' in periods and periods['quarter']:
                temporal_entities.extend(periods['quarter'])

            if 'year' in periods and periods['year']:
                temporal_entities.extend([str(y) for y in periods['year']])

            return temporal_entities

        except Exception as e:
            self.logger.debug(f"[TEMPORAL] BERT NER extraction failed: {e}")
            return []

    def get_all_temporal_keywords(self) -> Set[str]:
        """
        Get complete set of all temporal keywords

        Returns:
            Set of all temporal keywords (for reference/debugging)
        """
        return self.ALL_TEMPORAL_KEYWORDS.copy()

    def should_skip_disambiguation(self, value: str, query: Optional[str] = None) -> bool:
        """
        Determine if a value should skip disambiguation

        Checks:
        1. Is value itself a temporal keyword?
        2. If query provided, is value part of temporal context?

        Args:
            value: Value to check
            query: Optional full query for context

        Returns:
            True if should SKIP disambiguation, False otherwise

        Examples:
            >>> detector.should_skip_disambiguation("march")
            True
            >>> detector.should_skip_disambiguation("usa")
            False
        """
        # Direct keyword check
        if self.is_temporal_keyword(value):
            self.logger.info(f"[TEMPORAL] ⏭️  Skipping disambiguation for temporal keyword: '{value}'")
            return True

        # Context check (if query provided)
        if query:
            temporal_entities = self.extract_temporal_entities(query)
            if value.lower() in [e.lower() for e in temporal_entities]:
                self.logger.info(f"[TEMPORAL] ⏭️  Skipping disambiguation for temporal entity in context: '{value}'")
                return True

        return False






