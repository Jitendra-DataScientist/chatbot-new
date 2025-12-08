"""
Layer 0: Query Normalizer with Driver-Aware Rephrasing
Uses GPT-4o with constrained decoding to normalize queries for better downstream processing.

Purpose:
- Selects one or multiple relevant drivers (constrained, not optional)
- Rephrases query in a way those drivers need to consume
- Fixes grammar, word order, spelling in regular words (but NOT column names with underscores)
- Only passes normalized_query forward (driver hints stay internal)

Author: System Architecture
Date: 2025-12-05
"""

import logging
from typing import List, Optional
from pydantic import BaseModel, Field
from enum import Enum

try:
    import instructor
    INSTRUCTOR_AVAILABLE = True
except ImportError:
    INSTRUCTOR_AVAILABLE = False
    logging.warning("instructor library not available. Install with: pip install instructor")

logger = logging.getLogger(__name__)


# ============================================================================
# 16 OPERATION DRIVERS (Brief Definitions)
# ============================================================================

class DriverType(str, Enum):
    """16 operation drivers for data analysis"""
    TIME_SERIES = "time_series"
    COMPARISON = "comparison"
    TOP_N = "top_n"
    BOTTOM_N = "bottom_n"
    DISTRIBUTION = "distribution"
    AGGREGATION = "aggregation"
    FILTERING = "filtering"
    RANKING = "ranking"
    CORRELATION = "correlation"
    GROUPING = "grouping"
    PERIOD_OVER_PERIOD = "period_over_period"
    CUMULATIVE = "cumulative"
    MOVING_AVERAGE = "moving_average"
    PERCENTAGE = "percentage"
    CONDITIONAL = "conditional"
    MULTI_METRIC = "multi_metric"


# Driver requirements (brief context for GPT-4o)
DRIVER_REQUIREMENTS = {
    "time_series": "Requires: metric + temporal dimension (day/week/month/year). Shows trends over time.",
    "comparison": "Requires: metric + 2+ categories or periods to compare. Shows differences between groups.",
    "top_n": "Requires: metric + dimension + number N. Shows highest values.",
    "bottom_n": "Requires: metric + dimension + number N. Shows lowest values.",
    "distribution": "Requires: metric + dimension. Shows spread/frequency across categories.",
    "aggregation": "Requires: metric + aggregation function (sum/avg/count/min/max). Simple summary.",
    "filtering": "Requires: dimension + filter condition. Subset of data based on criteria.",
    "ranking": "Requires: metric + dimension. Orders by value (no N limit).",
    "correlation": "Requires: 2+ metrics. Shows relationship between metrics.",
    "grouping": "Requires: metric + 1+ dimensions. Breaks down by categories.",
    "period_over_period": "Requires: metric + temporal dimension + comparison period (week/month/year). Compares time periods.",
    "cumulative": "Requires: metric + temporal dimension. Running total over time.",
    "moving_average": "Requires: metric + temporal dimension + window size. Smoothed trend.",
    "percentage": "Requires: metric + dimension. Shows proportions/percentages.",
    "conditional": "Requires: metric + condition/threshold. Shows values meeting criteria.",
    "multi_metric": "Requires: 2+ metrics + optional dimension. Multiple measures together."
}


# ============================================================================
# NORMALIZED QUERY OUTPUT (Constrained)
# ============================================================================

class NormalizedQueryOutput(BaseModel):
    """
    Constrained output from Layer 0
    MUST select at least one driver (not optional!)
    """
    selected_drivers: List[DriverType] = Field(
        ...,
        min_items=1,
        max_items=3,
        description="One or more drivers that match the query intent. REQUIRED - must select at least 1."
    )
    
    normalized_query: str = Field(
        ...,
        description=(
            "Restructured query with ACTUAL words from user's input. "
            "Replace placeholders with real values from the query. "
            "Reorder words to match driver format. "
            "NEVER output '[metric]' or '[dimension]' literally - use the actual words user said. "
            "Fix spelling in regular words only (NOT column names with underscores). "
            "Preserve all content and meaning."
        )
    )
    
    reasoning: Optional[str] = Field(
        None,
        description="Brief explanation of why these driver(s) were selected (for debugging)"
    )
    
    class Config:
        use_enum_values = True


# ============================================================================
# LAYER 0 QUERY NORMALIZER
# ============================================================================

class Layer0QueryNormalizer:
    """
    Normalizes NL queries using GPT-4o with constrained decoding
    - MUST select ≥1 driver
    - Rephrases for driver consumption
    - Only normalized_query is passed forward
    """
    
    def __init__(self, llm_client):
        """
        Initialize with LLM client (should have SSL bypass already configured)
        
        Args:
            llm_client: OpenAI client instance (with SSL bypass)
        """
        if not INSTRUCTOR_AVAILABLE:
            raise ImportError("instructor library required. Install with: pip install instructor")
        
        self.llm_client = llm_client
        self.instructor_client = instructor.from_openai(llm_client)
        
        logger.info("[LAYER0] Query Normalizer initialized with GPT-4o")
    
    def normalize(self, query: str) -> str:
        """
        Normalize query using driver-aware rephrasing
        
        Args:
            query: Raw user query
            
        Returns:
            Normalized query string (driver hints stay internal)
        """
        try:
            logger.info(f"[LAYER0] Normalizing query: '{query}'")
            
            # Build context about drivers for GPT-4o
            driver_context = self._build_driver_context()
            
            # System prompt for GPT-4o
            system_prompt = f"""You are a query normalizer for a data analysis system. You must reason carefully about driver selection and query normalization.

Available Drivers:
{driver_context}

Your Process (FOLLOW THIS REASONING):

Step 0: IDENTIFY COLUMN NAMES (critical for spelling correction)
Before normalizing, identify which words are column names vs regular words:

Column name indicators:
- Contains underscores (pattern: [word]_[word], e.g., "[prefix]_[suffix]")
- Snake_case pattern (lowercase with underscores)
- Technical abbreviations (e.g., "qty", "amt", "prod", "dept", "emp")
- Not standard English words

If a word matches these patterns → Column name → Preserve EXACTLY (no spelling fixes even if looks wrong)
If a word is a regular English word → Can fix spelling if misspelled

Pattern Examples (using placeholders):
- "[column_with_underscore]" (contains underscore) → Column name → Keep exactly (even if spelling looks wrong!)
- "[regular_english_word_misspelled]" (no underscore, regular word) → Fix spelling
- "[prefix]_[suffix_misspelled]" (has underscore pattern) → Column name → Keep exactly (don't fix suffix!)
- "[dimension_word_misspelled]" (regular word, no underscore) → Fix spelling to correct word

Step 1: ANALYZE THE QUERY
- What is the user asking for?
- What facts are present (metric, dimensions, temporal keywords, numbers, etc.)?
- What facts are implied but not explicit?

Step 2: REASON ABOUT DRIVER SELECTION
For each potential driver, ask yourself:
- "Does this driver's requirements match the query intent?"
- "Can I extract ALL the required elements from the query?"
  * If driver needs metric + temporal: Are both present/implied?
  * If driver needs dimension + N: Are both present/implied?
  * If driver needs comparison: Is comparison present/implied?

CRITICAL: Only select a driver if you can extract/identify ALL its requirements from the query.
If you cannot extract requirements → DO NOT SELECT that driver, try another.

You MUST select at least 1 driver (required).

Step 3: RESTRUCTURE FOR DRIVER CONSUMPTION
Based on the driver(s) you selected in Step 2, restructure the query to match what that driver expects.

Each driver expects a specific structure - match it:

time_series: "[metric] by [time_period]"
- Reorder to show what metric, grouped by what time period
- Preserve filters: "in [month/quarter/year]", "for [time_range]"

period_over_period: "[metric] period over period by [time_period]"
- Or: "[metric] [time_period] over [time_period]"
- Preserve comparison context: "this vs last", specific periods

top_n / bottom_n: "top/bottom [N] [dimension] by [metric]"
- Normalize: "highest" → "top", "lowest" → "bottom"
- Keep the number N and all other values

filtering: "[metric] in/for/where [filter_value]"
- PRESERVE the filter value exactly (month name, category, etc.)
- Don't transform specific values into generic patterns

grouping: "[metric] by [dimension]"
- Just reorder if needed

aggregation: "[aggregation_function] of [metric]"
- Or: "[metric] [aggregation_word]"

CRITICAL PRESERVATION RULES:
❌ DO NOT remove ANY content words (values, filters, dimensions, metrics)
❌ DO NOT change specific values (month names, categories, numbers stay exact)
❌ DO NOT transform specific filters into generic patterns
❌ DO NOT change column names with underscores
❌ DO NOT add words that weren't in the query

✅ CAN reorder words to match driver structure
✅ CAN fix spelling in regular words (NOT column names)
✅ CAN fix grammar (articles, prepositions)
✅ CAN normalize pattern words: "highest"→"top", "trend"→match time_series pattern

Key Rule: If removing/changing a word loses information → KEEP IT EXACTLY

IMPORTANT: PLACEHOLDERS MUST BE REPLACED WITH ACTUAL VALUES!

The examples below use placeholders like [metric], [dimension], [filter_value] to show PATTERNS.
When you output your normalized_query, you MUST replace these placeholders with the ACTUAL words from the user's query.

Example: If user says "weekly sales trend", output "sales by week" (NOT "[metric] by week")

Placeholder notation:
- [metric] = replace with actual metric from query
- [dimension] = replace with actual dimension from query  
- [filter_value] = replace with actual filter value from query
- [N] = replace with actual number from query

Example 1 (Time Series - Restructure):
Input: "weekly [metric] trend"
Reasoning:
  - Driver: time_series (needs "[metric] by [time_period]")
  - "weekly" → "by week" (frequency to period)
  - Replace [metric] with actual metric from user's query
Output: "[metric] by week"
(Replace [metric] with the actual word user said)

Example 2 (Filtering - PRESERVE Filter Value):
Input: "give count of total [metric] in [filter_value]"
Reasoning:
  - Driver: filtering + aggregation
  - [filter_value] is SPECIFIC → MUST preserve!
  - "count of total" → "count of"
  - Don't transform specific filter into generic pattern!
  - Replace placeholders with actual words from query
Output: "count of [metric] in [filter_value]"
(Replace [metric] and [filter_value] with actual words)

Example 3 (Top N - Normalize Pattern):
Input: "highest [N] [dimension] on [metric]"
Reasoning:
  - Driver: top_n (needs "top [N] [dimension] by [metric]")
  - "highest" → "top" (pattern normalization)
  - "on" → "by" (preposition fix)
  - Replace placeholders with actual values from query
Output: "top [N] [dimension] by [metric]"
(Replace [N], [dimension], [metric] with actual values)

Example 4 (Period Comparison):
Input: "[metric] week over week"
Reasoning:
  - Driver: period_over_period
  - "week over week" signals comparison
  - Replace [metric] with actual word from query
Output: "[metric] week over week"
(Replace [metric] with actual value)

Example 5 (Column Name with Underscore):
Input: "[column_name] for [filter_value]"
Reasoning:
  - Step 0: [column_name] has underscore → preserve exactly
  - Driver: filtering
  - Replace placeholders with actual values from query
Output: "[column_name] for [filter_value]"
(Replace with actual column name and filter value)

Example 6 (Spelling Fix):
Input: "[metric] by [dimension_misspelled]"
Reasoning:
  - Step 0: [dimension_misspelled] → regular word → fix spelling
  - Driver: grouping
  - Replace placeholders with actual values (fix spelling in regular words)
Output: "[metric] by [dimension_corrected]"
(Replace placeholders, fix spelling in regular words only)

Your output MUST include:
1. selected_drivers: List of 1-3 drivers (REQUIRED, at least 1)
2. normalized_query: Restructured query with ACTUAL values from user's query
3. reasoning: Brief explanation of driver selection and restructuring

CRITICAL: Replace placeholders with actual words from the user's query!
- [metric] → the actual metric word user said
- [dimension] → the actual dimension word user said
- [filter_value] → the actual filter value user said
- [N] → the actual number user said

Do NOT output "[metric]" literally - use the real words from the query!"""

            # Call GPT-4o with constrained decoding
            response = self.instructor_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Analyze and normalize this query: {query}"}
                ],
                response_model=NormalizedQueryOutput,
                temperature=0.2,  # Slight flexibility for pattern word transformation
                max_tokens=800  # More tokens for reasoning output
            )
            
            # Log internal driver selection and reasoning (for debugging)
            logger.info(f"[LAYER0] ✅ Analysis complete")
            logger.info(f"[LAYER0] Selected drivers: {response.selected_drivers}")
            logger.info(f"[LAYER0] Reasoning: {response.reasoning or 'No reasoning provided'}")
            logger.info(f"[LAYER0] Normalized query: '{response.normalized_query}'")
            
            # Only return normalized_query (driver hints stay internal)
            return response.normalized_query
            
        except Exception as e:
            logger.error(f"[LAYER0] Normalization failed: {e}")
            logger.warning(f"[LAYER0] Falling back to original query")
            return query  # Fallback to original if normalization fails
    
    def _build_driver_context(self) -> str:
        """Build formatted driver context for GPT-4o"""
        lines = []
        for driver_key, description in DRIVER_REQUIREMENTS.items():
            lines.append(f"- {driver_key}: {description}")
        return "\n".join(lines)


# ============================================================================
# FACTORY FUNCTION
# ============================================================================

def create_query_normalizer(llm_client) -> Layer0QueryNormalizer:
    """
    Factory function to create Layer0QueryNormalizer
    
    Args:
        llm_client: OpenAI client with SSL bypass configured
        
    Returns:
        Layer0QueryNormalizer instance
    """
    return Layer0QueryNormalizer(llm_client)
