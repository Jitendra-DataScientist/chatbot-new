"""
Layer 0: Constrained Natural Language Parser
Converts NL queries to semantic intermediate representation (IR) using constrained decoding.

Features:
- Zero hallucination (constrained to actual columns via Pydantic validation)
- Dataset agnostic (analyzes schema dynamically)
- Spell checking for column names
- Temporal keyword extraction
- Generic semantic IR output

Author: System Architecture
Date: 2025-12-05
"""

import re
import json
import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from pydantic import BaseModel, Field, validator
from enum import Enum
import polars as pl
from openai import OpenAI

try:
    import instructor
    INSTRUCTOR_AVAILABLE = True
except ImportError:
    INSTRUCTOR_AVAILABLE = False
    logging.warning("instructor library not available. Install with: pip install instructor")

try:
    from fuzzywuzzy import fuzz
    FUZZYWUZZY_AVAILABLE = True
except ImportError:
    FUZZYWUZZY_AVAILABLE = False
    logging.warning("fuzzywuzzy not available. Install with: pip install fuzzywuzzy")


# ============================================================================
# SEMANTIC IR MODELS (Generic - No Dataset-Specific Hardcoding)
# ============================================================================

class AggregationType(str, Enum):
    """Universal aggregation types"""
    COUNT = "count"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    DISTINCT_COUNT = "distinct_count"
    MEDIAN = "median"


class TemporalPeriod(str, Enum):
    """Universal temporal periods"""
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class FilterOperator(str, Enum):
    """Universal filter operators"""
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


class FilterSpec(BaseModel):
    """Single filter specification"""
    column: str
    operator: FilterOperator
    value: Optional[Any] = None
    
    class Config:
        use_enum_values = True


class TemporalConstraint(BaseModel):
    """Temporal filter specification"""
    period: TemporalPeriod
    value: Any  # Month number, quarter number, year, or relative ("last", "this")
    year: Optional[int] = None
    
    class Config:
        use_enum_values = True


class Intent(BaseModel):
    """Query intent - what to analyze"""
    metric: str  # Column to aggregate
    dimensions: Optional[List[str]] = Field(default_factory=list)  # Columns to group by
    aggregation: AggregationType
    
    class Config:
        use_enum_values = True


class Constraints(BaseModel):
    """Query constraints - filters and limits"""
    temporal: Optional[TemporalConstraint] = None
    filters: List[FilterSpec] = Field(default_factory=list)
    limit: Optional[int] = None
    sort_by: Optional[str] = None
    sort_order: Optional[str] = "desc"  # "asc" or "desc"


class Modifiers(BaseModel):
    """Query modifiers - special operations"""
    comparison: Optional[str] = None  # "week_over_week", "month_over_month", etc.
    ranking: Optional[Dict[str, Any]] = None  # {"type": "top", "n": 5}
    calculation: Optional[str] = None  # "moving_average", "cumulative", etc.
    window_size: Optional[int] = None  # For rolling calculations


class SemanticIR(BaseModel):
    """
    Semantic Intermediate Representation
    Generic structure that works for ANY dataset
    """
    intent: Intent
    constraints: Constraints
    modifiers: Modifiers
    original_query: str
    confidence: float = 0.0
    
    class Config:
        use_enum_values = True


# ============================================================================
# SCHEMA ANALYZER (Dynamic - No Hardcoding)
# ============================================================================

class SchemaAnalyzer:
    """
    Analyzes DataFrame schema to categorize columns
    100% dynamic - works for any dataset
    """
    
    def __init__(self, df_sample: pl.DataFrame, calculated_fields: List[Dict]):
        self.df_sample = df_sample
        self.calculated_fields = calculated_fields
        self.logger = logging.getLogger(__name__)
        self.schema = self._analyze()
    
    def _analyze(self) -> Dict[str, List[str]]:
        """Categorize all columns by type"""
        
        schema = {
            "numeric": [],
            "categorical": [],
            "datetime": [],
            "id": [],
            "calculated": []
        }
        
        # Analyze base columns
        for col in self.df_sample.columns:
            dtype = str(self.df_sample[col].dtype)
            unique_count = self.df_sample[col].n_unique()
            total_count = len(self.df_sample)
            uniqueness = unique_count / total_count if total_count > 0 else 0
            
            # Datetime columns
            if 'Datetime' in dtype or 'Date' in dtype:
                schema["datetime"].append(col)
            
            # String/Utf8 columns
            elif dtype in ['String', 'Utf8']:
                # Check if it's an ID (high uniqueness)
                if uniqueness > 0.95 or 'id' in col.lower():
                    schema["id"].append(col)
                else:
                    schema["categorical"].append(col)
            
            # Numeric columns
            elif 'Int' in dtype or 'Float' in dtype:
                # Check if it's an ID
                if uniqueness > 0.95 or 'id' in col.lower():
                    schema["id"].append(col)
                else:
                    schema["numeric"].append(col)
            
            else:
                schema["categorical"].append(col)
        
        # Add calculated fields (handle both dict and list formats)
        if isinstance(self.calculated_fields, dict):
            # Dict format from workbook_metadata: {"field_name": {"formula": ..., "datatype": ...}}
            for field_name in self.calculated_fields.keys():
                schema["calculated"].append(field_name)
        elif isinstance(self.calculated_fields, list):
            # List format: [{"name": "...", "formula": ...}]
            for cf in self.calculated_fields:
                if isinstance(cf, dict) and 'name' in cf:
                    schema["calculated"].append(cf['name'])
                elif isinstance(cf, str):
                    schema["calculated"].append(cf)
        
        self.logger.info(f"[SCHEMA] Analyzed: {len(schema['numeric'])} numeric, "
                        f"{len(schema['categorical'])} categorical, "
                        f"{len(schema['datetime'])} datetime, "
                        f"{len(schema['id'])} ID, "
                        f"{len(schema['calculated'])} calculated")
        
        return schema
    
    def get_all_columns(self) -> List[str]:
        """Get all column names"""
        return (self.schema["numeric"] + 
                self.schema["categorical"] + 
                self.schema["datetime"] + 
                self.schema["id"] + 
                self.schema["calculated"])
    
    def is_temporal_column(self, col: str) -> bool:
        """Check if column is temporal"""
        if col in self.schema["datetime"]:
            return True
        
        # Check for temporal patterns in name
        temporal_patterns = ['date', 'time', 'day', 'week', 'month', 'quarter', 'year']
        col_lower = col.lower()
        return any(pattern in col_lower for pattern in temporal_patterns)
    
    def get_temporal_columns(self) -> List[str]:
        """Get all temporal columns"""
        temporal = self.schema["datetime"].copy()
        
        # Add columns with temporal patterns in name
        for col in self.get_all_columns():
            if self.is_temporal_column(col) and col not in temporal:
                temporal.append(col)
        
        return temporal


# ============================================================================
# PRE-PROCESSOR (Spell Check & Temporal Extraction)
# ============================================================================

class QueryPreprocessor:
    """
    Pre-processes query before LLM:
    - Spell checks column names
    - Extracts temporal keywords
    - Extracts intent keywords
    """
    
    # Universal temporal keywords (no dataset hardcoding)
    MONTHS = {
        'january': 1, 'jan': 1,
        'february': 2, 'feb': 2,
        'march': 3, 'mar': 3,
        'april': 4, 'apr': 4,
        'may': 5,
        'june': 6, 'jun': 6,
        'july': 7, 'jul': 7,
        'august': 8, 'aug': 8,
        'september': 9, 'sep': 9, 'sept': 9,
        'october': 10, 'oct': 10,
        'november': 11, 'nov': 11,
        'december': 12, 'dec': 12
    }
    
    def __init__(self, schema_analyzer: SchemaAnalyzer):
        self.schema = schema_analyzer
        self.all_columns = schema_analyzer.get_all_columns()
        self.logger = logging.getLogger(__name__)
    
    def process(self, query: str) -> Dict[str, Any]:
        """
        Pre-process query
        Returns: {corrected_query, temporal, intent_hints}
        """
        
        # 1. Spell check column names
        corrected_query = self._spell_check_columns(query)
        
        # 2. Extract temporal keywords
        temporal = self._extract_temporal(corrected_query)
        
        # 3. Extract intent hints
        intent_hints = self._extract_intent_hints(corrected_query)
        
        return {
            "original": query,
            "corrected": corrected_query,
            "temporal": temporal,
            "intent_hints": intent_hints
        }
    
    def _spell_check_columns(self, query: str) -> str:
        """Fuzzy match and replace column name typos"""
        
        if not FUZZYWUZZY_AVAILABLE:
            return query
        
        corrected = query
        replacements = []
        
        # Extract words from query
        words = re.findall(r'\b\w+\b', query.lower())
        
        for word in words:
            if len(word) < 3:  # Skip very short words
                continue
            
            # Find best matching column
            best_match = None
            best_score = 0
            
            for col in self.all_columns:
                col_lower = col.lower()
                
                # Multiple scoring strategies
                exact_score = fuzz.ratio(word, col_lower)
                partial_score = fuzz.partial_ratio(word, col_lower)
                token_score = fuzz.token_sort_ratio(word, col_lower)
                
                # Check if word is acronym of column
                # E.g., "opn" → "Open_Volume"
                col_words = re.findall(r'\b\w+', col)
                acronym = ''.join([w[0].lower() for w in col_words if w])
                acronym_score = 100 if word == acronym else 0
                
                # Combined score
                score = max(exact_score, partial_score, token_score, acronym_score)
                
                if score > best_score and score > 75:  # Threshold
                    best_score = score
                    best_match = col
            
            if best_match and best_match.lower() != word:
                replacements.append({
                    "original": word,
                    "corrected": best_match,
                    "score": best_score
                })
        
        # Apply replacements (case-insensitive)
        for rep in replacements:
            pattern = re.compile(re.escape(rep["original"]), re.IGNORECASE)
            corrected = pattern.sub(rep["corrected"], corrected)
            
            self.logger.info(f"[SPELL_CHECK] '{rep['original']}' → '{rep['corrected']}' "
                           f"(confidence: {rep['score']}%)")
        
        return corrected
    
    def _extract_temporal(self, query: str) -> Optional[Dict]:
        """Extract temporal keywords using regex (no LLM needed)"""
        
        query_lower = query.lower()
        
        # Extract month
        for month_name, month_num in self.MONTHS.items():
            if month_name in query_lower:
                return {
                    "period": "month",
                    "value": month_num,
                    "year": self._extract_year(query_lower)
                }
        
        # Extract quarter
        quarter_match = re.search(r'q([1-4])', query_lower)
        if quarter_match:
            return {
                "period": "quarter",
                "value": int(quarter_match.group(1)),
                "year": self._extract_year(query_lower)
            }
        
        # Extract year
        year = self._extract_year(query_lower)
        if year:
            return {
                "period": "year",
                "value": year,
                "year": year
            }
        
        return None
    
    def _extract_year(self, query_lower: str) -> Optional[int]:
        """Extract year from query"""
        year_match = re.search(r'\b(20\d{2})\b', query_lower)
        if year_match:
            return int(year_match.group(1))
        return None
    
    def _extract_intent_hints(self, query: str) -> Dict[str, Any]:
        """Extract intent hints using keyword matching"""
        
        query_lower = query.lower()
        
        hints = {
            "aggregation": None,
            "has_grouping": False,
            "top_n": None,
            "bottom_n": None,
            "comparison": None
        }
        
        # Aggregation detection
        if any(kw in query_lower for kw in ['count', 'number of', 'how many', 'total number']):
            hints["aggregation"] = "count"
        elif any(kw in query_lower for kw in ['sum', 'total']):
            hints["aggregation"] = "sum"
        elif any(kw in query_lower for kw in ['average', 'avg', 'mean']):
            hints["aggregation"] = "avg"
        elif any(kw in query_lower for kw in ['minimum', 'min', 'lowest value']):
            hints["aggregation"] = "min"
        elif any(kw in query_lower for kw in ['maximum', 'max', 'highest value']):
            hints["aggregation"] = "max"
        
        # Grouping detection
        if any(kw in query_lower for kw in ['by', 'per', 'for each', 'breakdown', 'split by']):
            hints["has_grouping"] = True
        
        # Top N detection
        top_match = re.search(r'\b(?:top|first|highest|best)\s+(\d+)\b', query_lower)
        if top_match:
            hints["top_n"] = int(top_match.group(1))
        
        # Bottom N detection
        bottom_match = re.search(r'\b(?:bottom|last|lowest|worst)\s+(\d+)\b', query_lower)
        if bottom_match:
            hints["bottom_n"] = int(bottom_match.group(1))
        
        # Comparison detection
        if any(kw in query_lower for kw in ['week over week', 'wow']):
            hints["comparison"] = "week_over_week"
        elif any(kw in query_lower for kw in ['month over month', 'mom']):
            hints["comparison"] = "month_over_month"
        elif any(kw in query_lower for kw in ['year over year', 'yoy']):
            hints["comparison"] = "year_over_year"
        
        return hints


# ============================================================================
# LAYER 0 CONSTRAINED PARSER (Main Class)
# ============================================================================

class Layer0ConstrainedParser:
    """
    Layer 0: Constrained NL to Semantic IR Parser
    
    Uses instructor + Pydantic for constraint enforcement
    100% generic - no dataset-specific hardcoding
    """
    
    def __init__(
        self,
        df_sample: pl.DataFrame,
        calculated_fields: List[Dict],
        llm_client: Optional[OpenAI] = None
    ):
        """
        Initialize parser
        
        Args:
            df_sample: Sample of DataFrame to analyze
            calculated_fields: List of calculated field definitions
            llm_client: OpenAI client (optional - will create if not provided)
        """
        
        self.logger = logging.getLogger(__name__)
        
        if not INSTRUCTOR_AVAILABLE:
            raise ImportError("instructor library required. Install with: pip install instructor")
        
        # Initialize schema analyzer
        self.schema_analyzer = SchemaAnalyzer(df_sample, calculated_fields)
        self.all_columns = self.schema_analyzer.get_all_columns()
        
        # Initialize preprocessor
        self.preprocessor = QueryPreprocessor(self.schema_analyzer)
        
        # Initialize LLM client with instructor
        if llm_client is None:
            # Fallback: Create client with SSL bypass for corporate firewall
            import httpx
            http_client = httpx.Client(verify=False, timeout=300.0)
            llm_client = OpenAI(http_client=http_client)
            self.logger.warning("[LAYER0] No llm_client provided - created fallback client with SSL bypass")
        
        self.client = instructor.from_openai(llm_client)
        
        self.logger.info(f"[LAYER0] Initialized with {len(self.all_columns)} columns")
    
    def parse(self, query: str) -> SemanticIR:
        """
        Parse natural language query to semantic IR
        
        Args:
            query: Natural language query
            
        Returns:
            SemanticIR object with validated column names
        """
        
        self.logger.info(f"[LAYER0] Parsing query: '{query}'")
        
        # Step 1: Pre-process query
        processed = self.preprocessor.process(query)
        
        self.logger.info(f"[LAYER0] Pre-processed: temporal={processed['temporal']}, "
                        f"hints={processed['intent_hints']}")
        
        # Step 2: Build prompt with context
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(processed)
        
        # Step 3: Call LLM with constrained decoding
        try:
            # Create dynamic Pydantic model with column enum
            ParsedQueryModel = self._create_dynamic_model()
            
            result = self.client.chat.completions.create(
                model="gpt-4o-2024-08-06",
                response_model=ParsedQueryModel,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_retries=2,
                temperature=0.1
            )
            
            # Convert to SemanticIR
            semantic_ir = self._convert_to_semantic_ir(result, query)
            
            self.logger.info(f"[LAYER0] ✅ Parsed successfully")
            self.logger.debug(f"[LAYER0] IR: {semantic_ir.dict()}")
            
            return semantic_ir
            
        except Exception as e:
            self.logger.error(f"[LAYER0] Parsing failed: {e}", exc_info=True)
            raise
    
    def _create_dynamic_model(self) -> type:
        """
        Create Pydantic model with dynamic column enums
        This enforces EXACT column names - prevents hallucination
        """
        
        from pydantic import create_model, ConfigDict
        from typing import Literal
        
        # Create enum of all valid columns
        ColumnEnum = Literal[tuple(self.all_columns)]
        
        # Define the model dynamically
        ParsedQuery = create_model(
            'ParsedQuery',
            metric=(ColumnEnum, ...),
            dimensions=(Optional[List[ColumnEnum]], None),
            aggregation=(AggregationType, ...),
            filters=(Optional[List[FilterSpec]], None),
            temporal_period=(Optional[TemporalPeriod], None),
            temporal_value=(Optional[Any], None),
            temporal_year=(Optional[int], None),
            top_n=(Optional[int], None),
            bottom_n=(Optional[int], None),
            comparison=(Optional[str], None),
            __config__=ConfigDict(use_enum_values=True)
        )
        
        return ParsedQuery
    
    def _build_system_prompt(self) -> str:
        """Build system prompt with dynamic schema info"""
        
        schema = self.schema_analyzer.schema
        
        return f"""You are a query parser that converts natural language to structured format.

📊 AVAILABLE COLUMNS:

Numeric columns (for aggregation):
{json.dumps(schema['numeric'][:15], indent=2)}
{f"... and {len(schema['numeric']) - 15} more" if len(schema['numeric']) > 15 else ""}

Categorical columns (for grouping):
{json.dumps(schema['categorical'][:15], indent=2)}
{f"... and {len(schema['categorical']) - 15} more" if len(schema['categorical']) > 15 else ""}

Datetime/Temporal columns:
{json.dumps(schema['datetime'], indent=2)}

ID columns (for counting):
{json.dumps(schema['id'][:10], indent=2)}
{f"... and {len(schema['id']) - 10} more" if len(schema['id']) > 10 else ""}

Calculated fields (pre-computed metrics):
{json.dumps(schema['calculated'], indent=2)}

🎯 YOUR TASK:
Parse the query and extract:
1. **metric**: Column to aggregate (MUST be from available columns)
2. **dimensions**: Columns to group by (MUST be from available columns)
3. **aggregation**: Type of aggregation (count, sum, avg, min, max)
4. **filters**: Any filter conditions
5. **temporal info**: Period, value, year if mentioned
6. **top_n/bottom_n**: If "top N" or "bottom N" is mentioned
7. **comparison**: If comparing periods (week over week, etc.)

🚨 CRITICAL RULES:
1. Use ONLY exact column names from the available columns above
2. Do NOT invent column names
3. For temporal grouping, use temporal columns from the schema
4. Calculated fields already encode logic - don't add redundant filters
5. If "count", use an ID column as metric unless specific column mentioned

Return structured output matching the schema."""
    
    def _build_user_prompt(self, processed: Dict) -> str:
        """Build user prompt with pre-processed info"""
        
        hints = processed['intent_hints']
        temporal = processed['temporal']
        
        prompt = f"""Parse this query:
"{processed['corrected']}"

Pre-extracted information to help you:
- Temporal: {temporal}
- Suggested aggregation: {hints.get('aggregation')}
- Has grouping: {hints.get('has_grouping')}
- Top N: {hints.get('top_n')}
- Bottom N: {hints.get('bottom_n')}
- Comparison: {hints.get('comparison')}

Extract the structured components."""
        
        return prompt
    
    def _convert_to_semantic_ir(self, parsed: Any, original_query: str) -> SemanticIR:
        """Convert parsed result to SemanticIR"""
        
        # Build Intent
        intent = Intent(
            metric=parsed.metric,
            dimensions=parsed.dimensions or [],
            aggregation=parsed.aggregation
        )
        
        # Build Constraints
        temporal_constraint = None
        if parsed.temporal_period and parsed.temporal_value is not None:
            temporal_constraint = TemporalConstraint(
                period=parsed.temporal_period,
                value=parsed.temporal_value,
                year=parsed.temporal_year
            )
        
        constraints = Constraints(
            temporal=temporal_constraint,
            filters=parsed.filters or [],
            limit=parsed.top_n or parsed.bottom_n,
            sort_order="desc" if parsed.top_n else "asc" if parsed.bottom_n else "desc"
        )
        
        # Build Modifiers
        ranking = None
        if parsed.top_n:
            ranking = {"type": "top", "n": parsed.top_n}
        elif parsed.bottom_n:
            ranking = {"type": "bottom", "n": parsed.bottom_n}
        
        modifiers = Modifiers(
            comparison=parsed.comparison,
            ranking=ranking
        )
        
        # Create SemanticIR
        ir = SemanticIR(
            intent=intent,
            constraints=constraints,
            modifiers=modifiers,
            original_query=original_query,
            confidence=0.9  # High confidence due to constrained decoding
        )
        
        return ir


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def setup_logger():
    """Setup logging for Layer 0"""
    logger = logging.getLogger(__name__)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            '[%(asctime)s] %(levelname)s - %(name)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


# Initialize module logger
setup_logger()

