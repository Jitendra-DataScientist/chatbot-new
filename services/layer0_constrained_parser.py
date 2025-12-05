"""
Layer 0: Query Normalizer with Driver-Aware Rephrasing
Uses GPT-4o mini with constrained decoding to normalize queries for better downstream processing.

Purpose:
- Selects one or multiple relevant drivers (constrained, not optional)
- Rephrases query in a way those drivers need to consume
- Fixes grammar, word order, spelling (but NOT column names)
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


# Driver requirements (brief context for GPT-4o mini)
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
            "Rephrased query optimized for the selected driver(s) consumption. "
            "Fix grammar, word order, articles (a/the), prepositions (by/to/for). "
            "Fix common spelling mistakes BUT preserve exact column names (e.g., 'opn_volume' stays 'opn_volume'). "
            "Keep all nouns, numbers, and meaning unchanged. "
            "Structure the query to show what the driver(s) need clearly."
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
    Normalizes NL queries using GPT-4o mini with constrained decoding
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
        
        logger.info("[LAYER0] Query Normalizer initialized with GPT-4o mini")
    
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
            
            # Build context about drivers for GPT-4o mini
            driver_context = self._build_driver_context()
            
            # System prompt for GPT-4o mini
            system_prompt = f"""You are a query normalizer for a data analysis system. You must reason carefully about driver selection and query normalization.

Available Drivers:
{driver_context}

Your Process (FOLLOW THIS REASONING):

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

Step 3: NORMALIZE THE QUERY
Based on selected driver(s):
- Make implicit facts explicit (e.g., "trend" → "over time" or "period over period")
- Restructure to show what the driver needs clearly
- Fix spelling mistakes in regular words (NOT column names like "opn_volume")
- Fix grammar, word order, articles (a/the/an), prepositions (by/to/for/of/on)
- You CAN add facts to make driver requirements explicit (not hallucination, just clarification)

Rules:
✅ Make implicit information explicit (e.g., "trend" implies time comparison)
✅ Add facts needed for driver (e.g., add "week over week" if temporal comparison is implied)
✅ Fix spelling mistakes in regular words
✅ Fix grammar and word order
❌ Do NOT change column names (e.g., "opn_volume" stays "opn_volume")
❌ Do NOT change core meaning or intent
❌ Do NOT hallucinate facts that aren't in the query

Examples (generic, not from real data):

Example 1:
Input: "monthly product sales trend"
Reasoning:
  - Intent: Show how sales change over months
  - Present: metric="product sales", temporal="monthly", pattern="trend"
  - "trend" implies time-based comparison
  - time_series driver: needs metric + temporal ✓ (both present)
  - Can extract requirements: YES
Selected: [time_series]
Output: "product sales by month over time"
(Made "trend" explicit as "over time", shows metric + temporal clearly)

Example 2:
Input: "highest 10 stores on revenue"
Reasoning:
  - Intent: Find top stores by revenue
  - Present: metric="revenue", dimension="stores", N=10, ranking="highest"
  - top_n driver: needs metric + dimension + N ✓ (all present)
  - Can extract requirements: YES
Selected: [top_n]
Output: "top 10 stores by revenue"
(Normalized "highest" to "top", clear structure)

Example 3:
Input: "customer growth this quarter vs last quarter"
Reasoning:
  - Intent: Compare two time periods
  - Present: metric="customer growth", temporal="quarter", comparison="this vs last"
  - period_over_period driver: needs metric + temporal + comparison ✓ (all present)
  - comparison driver: needs metric + categories to compare ✓ (two quarters)
  - Can extract requirements for both: YES
Selected: [period_over_period, comparison]
Output: "customer growth this quarter vs last quarter"
(Already clear, minimal changes needed)

Your output MUST include:
1. selected_drivers: List of 1-3 drivers (REQUIRED, at least 1)
2. normalized_query: Restructured query that makes driver requirements explicit
3. reasoning: Brief explanation of why you selected these drivers and how you normalized the query"""

            # Call GPT-4o mini with constrained decoding
            response = self.instructor_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Analyze and normalize this query: {query}"}
                ],
                response_model=NormalizedQueryOutput,
                temperature=0.2,  # Slightly higher for reasoning flexibility
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
        """Build formatted driver context for GPT-4o mini"""
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
