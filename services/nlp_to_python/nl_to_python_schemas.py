"""
Natural Language to Python Code Generator - Schemas Module
Contains all data models, validation logic, and grounding functions.

This module contains:
- Exception classes and context managers
- Pydantic schemas for Stage 1 and Stage 2
- Value grounding and validation functions
- Base operation classes and results
- LangGraph state definitions
- Dynamic schema management
"""

import re
import json
import logging
import os
import tokenize
from io import StringIO
from typing import Dict, List, Any, Optional, Tuple, Set, Literal, Union, get_args
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from difflib import SequenceMatcher
from abc import ABC, abstractmethod

import numpy as np
import polars as pl

# Import scipy stats if available
try:
    from scipy import stats
    SCIPY_AVAILABLE = True
except Exception:
    stats = None
    SCIPY_AVAILABLE = False

# Import models and services (from existing V4)
from models.schemas import NLToPythonResult, PandasOperation
from services.fuzzy_column_matcher import FuzzyColumnMatcher
from master_logger import setup_module_logger

# Try to import OpenAI
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logging.warning("OpenAI library not available. Install with: pip install openai")

# Pydantic for structured outputs
try:
    from pydantic import BaseModel, Field, create_model
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    logging.warning("Pydantic not available. Install with: pip install pydantic")

# LangGraph for workflow orchestration (REQUIRED)
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

# LangSmith for observability and tracing (REQUIRED)
from langsmith import traceable, Client as LangSmithClient
import langsmith


# ============================================================================
# EXCEPTIONS AND CONTEXT MANAGEMENT
# ============================================================================

class UserInputRequiredException(Exception):
    """Exception raised when user input is required for disambiguation"""
    def __init__(self, message, candidates=None, context=None):
        super().__init__(message)
        self.candidates = candidates or []
        self.context = context or {}


class DefaultContextManager:
    """
    Enhanced context manager with session-based disambiguation caching
    Integrates with SessionContextManager from query_understanding_agent
    """
    
    def __init__(self, session_manager=None, session_id: str = None, source_id: str = None):
        """
        Initialize context manager with session support
        
        Args:
            session_manager: SessionContextManager instance for caching
            session_id: Current session ID
            source_id: Current data source ID
        """
        self.session_manager = session_manager
        self.session_id = session_id or f"session_{datetime.now().timestamp()}"
        self.source_id = source_id or "default"
        self.logger = logging.getLogger(__name__)
    
    def ask_user_for_column(self, filter_value, candidate_columns, query):
        """
        Ask user to select correct column for filter value
        First checks disambiguation cache before prompting
        
        Args:
            filter_value: The filter value (e.g., "usa")
            candidate_columns: List of dicts with 'column', 'actual_value', 'sample_values'
            query: Original query
            
        Returns:
            Selected actual_value or None
        """
        self.logger.info(f"[CONTEXT_MGR] Disambiguation needed for filter: {filter_value}")
        self.logger.info(f"[CONTEXT_MGR] Candidates: {[c.get('actual_value') for c in candidate_columns]}")
        
        # 🆕 CHECK CACHE FIRST
        if self.session_manager and candidate_columns:
            # Try to find cached choice for this filter value
            for candidate in candidate_columns:
                column = candidate.get('column')
                actual_value = candidate.get('actual_value')
                
                cached = self.session_manager.get_cached_disambiguation(
                    self.session_id,
                    self.source_id,
                    column,
                    filter_value
                )
                
                if cached and cached == actual_value:
                    self.logger.info(f"[CONTEXT_MGR] ✅ Found cached choice: '{filter_value}' → '{cached}'")
                    return cached
        
        # 🆕 NO CACHE HIT - Need user input (or auto-select for testing)
        if candidate_columns:
            selected = candidate_columns[0]['actual_value']
            column = candidate_columns[0]['column']
            
            self.logger.info(f"[CONTEXT_MGR] Auto-selected first candidate: {selected}")
            
            # 🆕 CACHE THE CHOICE
            if self.session_manager:
                self.session_manager.update_disambiguation_cache(
                    self.session_id,
                    self.source_id,
                    column,
                    filter_value,
                    selected
                )
                self.logger.info(f"[CONTEXT_MGR] 💾 Cached choice for future queries")
            
            return selected
        
        return None
    
    def ask_user_for_metric(self, query, available_metrics, operation_type):
        """
        Ask user for metric selection
        🆕 Enhanced with session caching
        """
        self.logger.warning(f"[CONTEXT_MGR] User input needed for metric selection")
        
        # 🆕 CHECK CACHE for metric selection
        if self.session_manager and available_metrics:
            cache_key = f"metric_selection:{operation_type}"
            cached = self.session_manager.get_cached_disambiguation(
                self.session_id,
                self.source_id,
                cache_key,
                query  # Use query as the term
            )
            
            if cached and cached in available_metrics:
                self.logger.info(f"[CONTEXT_MGR] ✅ Found cached metric: {cached}")
                return cached
        
        # Auto-select first metric as fallback
        if available_metrics:
            selected = available_metrics[0]
            self.logger.info(f"[CONTEXT_MGR] Auto-selected first metric: {selected}")
            
            # 🆕 CACHE THE CHOICE
            if self.session_manager:
                cache_key = f"metric_selection:{operation_type}"
                self.session_manager.update_disambiguation_cache(
                    self.session_id,
                    self.source_id,
                    cache_key,
                    query,
                    selected
                )
            
            return selected
        
        return None
    
    def get_cached_disambiguation(self, session_id: str, source_id: str, column: str, term: str) -> Optional[str]:
        """
        Get cached disambiguation choice for a given term in a column
        
        Args:
            session_id: Current session ID
            source_id: Current data source ID  
            column: Column name being filtered
            term: The term to look up
            
        Returns:
            Cached disambiguation choice or None
        """
        if self.session_manager:
            return self.session_manager.get_cached_disambiguation(session_id, source_id, column, term)
        return None

    def update_disambiguation_cache(self, session_id: str, source_id: str, column: str, term: str, choice: str):
        """
        Update disambiguation cache with a new choice
        
        Args:
            session_id: Current session ID
            source_id: Current data source ID
            column: Column name being filtered
            term: The term that was disambiguated
            choice: The chosen actual value
        """
        if self.session_manager:
            self.session_manager.update_disambiguation_cache(session_id, source_id, column, term, choice)


# ============================================================================
# STAGE 1: STRICT COLUMN & FILTER SCHEMAS
# ============================================================================

def create_stage1_schema(df_columns: List[str], column_samples: Dict[str, List[Any]]):
    """
    Create STRICT schema with dynamic Literal types for columns
    This makes hallucination IMPOSSIBLE for column selection
    
    Args:
        df_columns: Actual dataframe column names
        column_samples: Sample values per column (for context, not enum)
    
    Returns:
        Pydantic model class with strict enums
    """
    
    if not PYDANTIC_AVAILABLE:
        raise ImportError("Pydantic required for strict schema")
    
    # Create dynamic Literal type for columns
    # This ensures LLM can ONLY select from actual columns
    ColumnEnum = Literal[tuple(df_columns)]
    
    class AdditionalFilter(BaseModel):
        """Additional filter for multi-condition queries"""
        column: ColumnEnum = Field(description="Column to filter")
        operator: Literal["==", "!=", ">", "<", ">=", "<=", "in", "contains", "not_null"] = Field(
            default="==",
            description="Filter operator"
        )
        value: Union[str, int, float, List[str], List[int]] = Field(
            description="Value to filter by"
        )
    
    class Stage1ColumnFilterSelection(BaseModel):
        """
        Stage 1: Column and Filter Selection with STRICT enums
        
        JSON strict mode ensures LLM cannot hallucinate column names.
        All columns MUST be from the actual dataframe.
        """
        
        # Primary filter (if any)
        filter_column: Optional[ColumnEnum] = Field(
            None,
            description="Column to filter on (MUST be from available columns)"
        )
        filter_operator: Optional[Literal["==", "!=", ">", "<", ">=", "<=", "in", "contains", "not_null"]] = Field(
            None,
            description="Filter operator"
        )
        filter_value: Optional[Union[str, int, float, List[str], List[int]]] = Field(
            None,
            description="Value(s) to filter by - will be validated against actual data"
        )
        
        # Additional filters for multi-condition queries
        additional_filters: Optional[List[AdditionalFilter]] = Field(
            None,
            description="Additional filter conditions (e.g., 'closed tickets for India' = status filter + country filter)"
        )
        
        # Group by columns
        group_by_columns: Optional[List[ColumnEnum]] = Field(
            None,
            description="Columns to group by (each MUST be from available columns)"
        )
        
        # Metric/aggregation column
        metric_column: Optional[ColumnEnum] = Field(
            None,
            description="Column containing the metric to calculate (MUST be from available columns)"
        )
        
        # Date/time column
        date_column: Optional[ColumnEnum] = Field(
            None,
            description="Date/time column for temporal filtering (MUST be from available columns)"
        )
        
        # Secondary columns (for comparisons, rankings, etc.)
        secondary_columns: Optional[List[ColumnEnum]] = Field(
            None,
            description="Additional columns needed for the operation"
        )
        
        # Intents for Stage 2 (agentic processing)
        temporal_intent: Optional[str] = Field(
            None,
            description="Temporal expression as-is from query (e.g., 'Q3 2024', 'last quarter', 'last 30 days')"
        )
        operation_intent: Optional[str] = Field(
            None,
            description="High-level operation description (e.g., 'rate calculation', 'comparison', 'time series')"
        )
        
        #Window specification change by Aniket - Extract window size from query for intelligent rolling/expanding window selection
        window_specification: Optional[str] = Field(
            None,
            description="Window size from query (e.g., '7 days', '30D', '3 months') for rolling windows, or indicators like 'cumulative'/'running' for expanding windows"
        )
        
        # Reasoning (for debugging)
        reasoning: str = Field(
            description="Explanation of column selections"
        )
    
    return Stage1ColumnFilterSelection


# ============================================================================
# STAGE 2: AGENTIC TEMPORAL & OPERATION SCHEMAS
# ============================================================================

class TemporalFilter(BaseModel):
    """Parsed temporal filter with strict types"""
    filter_type: Literal[
        "last_n_days", "last_n_months", "last_quarter", "this_quarter", "next_quarter",
        "last_month", "this_month", "next_month", "last_year", "this_year", "next_year",
        "specific_quarter", "specific_month", "specific_year", "date_range",
        "ytd", "mtd", "qtd"
    ] = Field(description="Type of temporal filter")
    
    # Parameters for different filter types
    year: Optional[int] = Field(None, description="Year (if specific year/quarter/month)")
    quarter: Optional[int] = Field(None, ge=1, le=4, description="Quarter 1-4")
    month: Optional[int] = Field(None, ge=1, le=12, description="Month 1-12")
    n_value: Optional[int] = Field(None, gt=0, description="N value for last_n_* filters")
    start_date: Optional[str] = Field(None, description="Start date for date_range")
    end_date: Optional[str] = Field(None, description="End date for date_range")


# Valid operations enum - prevents hallucination
VALID_OPERATIONS = Literal[
    #UPDATED: Unified period_comparison operation
    "period_comparison",  # 🆕 Unified temporal comparison (replaces quarter_comparison, month_on_month, year_on_year)
    "time_series",        # 🆕 Continuous time progression with optional comparisons
    "general_comparison",#JN
    "pivot",# RR
    "composition_percentage",#AR - only filtered percentage now
    "breakdown",#Changes for percentage by Aniket - new operation for distribution/breakdown queries
    "percentile",# AC
    "ranking",#AC
    "window_function",#2 AR
    "grouped_aggregation",#done AR
    "column_description", # 🆕 NEW: Column metadata and description lookup
    #disabled distribution
    #"distribution",
    "statistical_test",
    "binning",
    "date_arithmetic"
]


class OperationParams(BaseModel):
    """Parameters for operations - allows any fields"""
    class Config:
        extra = "allow"


class Stage2AgenticPlan(BaseModel):
    """
    Stage 2: Agentic temporal parsing and operation selection
    
    Uses validated columns from Stage 1.
    LLM handles temporal complexity and operation chaining.
    """
    
    # Parsed temporal filters
    temporal_filters: List[TemporalFilter] = Field(
        default_factory=list,
        description="Parsed temporal filters from temporal_intent"
    )
    
    # Operations to perform (enum constrained)
    operations: List[VALID_OPERATIONS] = Field(
        description="List of operations to perform in sequence"
    )
    
    # Parameters for each operation (as nested object instead of Dict)
    numerator_column: Optional[str] = Field(None, description="Numerator column for rate calculation")
    group_by_columns: Optional[List[str]] = Field(None, description="Columns to group by")
    agg_column: Optional[str] = Field(None, description="Column to aggregate")
    agg_functions: Optional[List[str]] = Field(None, description="Aggregation functions")
    index: Optional[List[str]] = Field(None, description="Index columns for pivot")
    columns: Optional[List[str]] = Field(None, description="Columns for pivot")
    values: Optional[str] = Field(None, description="Values column for pivot")
    aggfunc: Optional[str] = Field(None, description="Aggregation function")
    column: Optional[str] = Field(None, description="Column for operation")
    rate_name: Optional[str] = Field(None, description="Name for rate result")
    as_percentage: bool = Field(True, description="Return as percentage")
    
    # 🆕 PERIOD COMPARISON PARAMETERS
    comparison_type: Optional[Literal[
        "lag",              # Simple shift (compare with previous)
        "lead",             # Future shift (next period)
        "temporal_change",  # Percentage/absolute change from previous
        "delta",            # Absolute difference
        "ratio"             # Current / Previous ratio
    ]] = Field(None, description="Type of period comparison")
    
    period_granularity: Optional[Literal[
        "hour",         # Hourly comparison
        "day",          # Daily comparison
        "week",         # Week over week
        "business_day", # Business day (Mon-Fri)
        "month",        # Month over month
        "quarter",      # Quarter over quarter
        "year",         # Year over year
        "season"        # Seasonal comparison (Spring/Summer/Fall/Winter)
    ]] = Field(None, description="Granularity of period comparison")
    
    shift_periods: Optional[int] = Field(
        None, 
        description="Number of periods to shift (1=previous, 7=last week, -1=next)"
    )
    
    compare_periods: Optional[List[str]] = Field(
        None,
        description="Specific periods to compare (e.g., ['Q1 2024', 'Q2 2024'])"
    )
    
    output_format: Optional[Literal["percentage", "absolute", "both"]] = Field(
        "percentage",
        description="How to format temporal_change output"
    )
    
    # 🆕 TIME SERIES PARAMETERS
    time_series_granularity: Optional[Literal[
        "hour",
        "day", 
        "week",
        "month",
        "quarter",
        "year"
    ]] = Field(None, description="Granularity for time series aggregation")
    
    time_series_include_comparison: Optional[bool] = Field(
        False,
        description="Include comparison metrics (WoW, MoM, YoY)"
    )
    
    time_series_comparison_type: Optional[Literal[
        "previous_period",      # Compare to previous week/month/quarter
        "same_period_last_year" # Compare to same period in previous year
    ]] = Field(None, description="Type of comparison for time series")
    
    #Window function parameters change by Aniket - Enhanced support for time-based and row-based windows
    window_type: Optional[Literal["rolling", "expanding"]] = Field(None, description="Type of window function: 'rolling' for fixed-size windows, 'expanding' for cumulative calculations")
    window_size: Optional[int] = Field(None, description="Window size in number of periods (for rolling) or days/months (if time-based)")
    window_unit: Optional[Literal["days", "months", "years", "weeks"]] = Field(None, description="Unit for time-based rolling windows (e.g., 'days' for 7-day rolling)")
    window_operation: Optional[str] = Field(None, description="Window operation: 'mean', 'sum', 'count', 'std', 'var', 'min', 'max', 'median', 'corr', 'cov'")
    window_column2: Optional[str] = Field(None, description="Second column for correlation/covariance operations")
    window_is_time_based: bool = Field(False, description="True if window should be time-based (requires date column), False for row-based")
    window_operation_explicit: bool = Field(False, description="Smart aggregation by Aniket - True if user explicitly mentioned the operation (count/sum/avg/std), False if inferred/ambiguous and needs smart aggregation validation")
    
    # 🆕 COLUMN DESCRIPTION PARAMETERS
    describe_columns: Optional[List[str]] = Field(
        None,
        description="Columns to describe (for column_description operation)"
    )
    detail_level: Optional[Literal["basic", "detailed", "statistical"]] = Field(
        "detailed",
        description="Level of detail for column descriptions"
    )
    include_samples: Optional[bool] = Field(
        True,
        description="Include sample values in column descriptions"
    )
    
    # Additional filters or transformations
    requires_sorting: bool = Field(
        default=False,
        description="Whether results need sorting"
    )
    sort_column: Optional[str] = Field(None, description="Column to sort by")
    sort_ascending: bool = Field(default=False, description="Sort order")
    
    limit_results: Optional[int] = Field(
        None,
        description="Limit number of results (for top N queries)"
    )
    
    # Confidence and reasoning
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence in this plan (0-1)"
    )
    reasoning: str = Field(
        description="Explanation of the execution plan"
    )
    ambiguities: List[str] = Field(
        default_factory=list,
        description="Any ambiguities or assumptions made"
    )


# ============================================================================
# 🆕 DYNAMIC SCHEMA MANAGER (From query_understanding_agent.py)
# ============================================================================

class DynamicSchemaManager:
    """
    Manages dynamically updated schemas as queries produce computed columns
    Integrated into LangGraph state for full context awareness
    """
    
    def __init__(self):
        self.source_schemas = {}  # source_id → {columns: [...], sample_data: df}
        self.logger = setup_module_logger('services.dynamic_schema_manager')
    
    def initialize_source(self, source_id: str, df: pl.DataFrame):
        """Initialize schema for a data source"""
        self.source_schemas[source_id] = {
            'base_columns': list(df.columns),
            'computed_columns': [],
            'all_columns': list(df.columns),
            'sample_data': df.head(100).clone(),
            'last_result': None
        }
        self.logger.info(f"[SCHEMA_INIT] Source '{source_id}': {len(df.columns)} base columns")
    
    def update_from_result(self, source_id: str, result_df: pl.DataFrame, operation_type: str):
        """Update schema after operation produces new columns"""
        if source_id not in self.source_schemas:
            self.logger.warning(f"[SCHEMA_UPDATE] Source '{source_id}' not initialized")
            return
        
        schema = self.source_schemas[source_id]
        base_cols = set(schema['base_columns'])
        result_cols = set(result_df.columns)
        
        # Detect new computed columns
        new_columns = result_cols - base_cols
        
        if new_columns:
            schema['computed_columns'].extend(list(new_columns))
            schema['all_columns'] = list(base_cols | result_cols)
            schema['last_result'] = result_df.head(100).clone()
            
            self.logger.info(f"[SCHEMA_UPDATE] Source '{source_id}': Added {len(new_columns)} computed columns: {list(new_columns)}")
            self.logger.info(f"[SCHEMA_UPDATE] Total columns now: {len(schema['all_columns'])}")
        
        return new_columns
    
    def get_columns(self, source_id: str) -> List[str]:
        """Get all available columns (base + computed) for a source"""
        if source_id not in self.source_schemas:
            return []
        return self.source_schemas[source_id]['all_columns']
    
    def get_sample_data(self, source_id: str) -> Optional[pl.DataFrame]:
        """Get sample data (base or last result) for a source"""
        if source_id not in self.source_schemas:
            return None
        
        schema = self.source_schemas[source_id]
        # Prefer last result if available (includes computed columns)
        if schema['last_result'] is not None:
            return schema['last_result']
        return schema['sample_data']


# ============================================================================
# BASE OPERATION CLASSES AND RESULTS  
# ============================================================================

class ValidationResult:
    """Result of input validation for operation nodes"""
    def __init__(self, success: bool, error: str = None):
        self.success = success
        self.error = error

class OperationResult:
    """Result of operation execution"""
    def __init__(self, success: bool, data: Any = None, error: str = None, 
                 generated_code: str = None, metadata: Dict = None):
        self.success = success
        self.data = data
        self.error = error
        self.generated_code = generated_code
        self.metadata = metadata or {}

class BaseOperationNode(ABC):
    """Base class for all operation nodes - future-proof architecture"""
    
    def __init__(self, node_name: str):
        self.node_name = node_name
        self.logger = setup_module_logger(f'operations.{node_name}')
    
    @traceable(name=f"operation_node_execution")
    def execute_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Main LangGraph node execution wrapper"""
        import time
        start_time = time.time()
        
        try:
            self.logger.info(f"[{self.node_name.upper()}_NODE] Starting operation execution...")
            
            # Validate prerequisites
            validation_result = self.validate_inputs(state)
            if not validation_result.success:
                return self._create_error_state(state, validation_result.error, start_time)
            
            # Execute the operation
            operation_result = self.execute_operation(state)
            
            if operation_result.success:
                return self._create_success_state(state, operation_result, start_time)
            else:
                return self._create_error_state(state, operation_result.error, start_time)
                
        except Exception as e:
            self.logger.error(f"[{self.node_name.upper()}_NODE] Unexpected error: {str(e)}", exc_info=True)
            return self._create_error_state(state, f"Unexpected error: {str(e)}", start_time)
    
    @abstractmethod
    def validate_inputs(self, state: Dict[str, Any]) -> ValidationResult:
        """Validate inputs required for this operation"""
        pass
    
    @abstractmethod
    def execute_operation(self, state: Dict[str, Any]) -> OperationResult:
        """Execute the specific operation logic"""
        pass
    
    def _create_success_state(self, state: Dict[str, Any], result: OperationResult, start_time: float) -> Dict[str, Any]:
        """Create success state update"""
        import time
        execution_time = time.time() - start_time
        
        new_state = state.copy()
        new_state.update({
            'operation_results': {**state.get('operation_results', {}), self.node_name: result.data},
            'operation_status': {**state.get('operation_status', {}), self.node_name: "completed"},
            'operation_execution_times': {**state.get('operation_execution_times', {}), self.node_name: execution_time},
            'generated_code': result.generated_code,
            'confidence': result.metadata.get('confidence', 1.0)
        })
        
        self.logger.info(f"[{self.node_name.upper()}_NODE] ✅ Completed in {execution_time:.2f}s")
        return new_state
    
    def _create_error_state(self, state: Dict[str, Any], error: str, start_time: float) -> Dict[str, Any]:
        """Create error state update"""
        import time
        execution_time = time.time() - start_time
        
        new_state = state.copy()
        operation_errors = state.get('operation_errors', {})
        if self.node_name not in operation_errors:
            operation_errors[self.node_name] = []
        operation_errors[self.node_name].append(error)
        
        new_state.update({
            'operation_errors': operation_errors,
            'operation_status': {**state.get('operation_status', {}), self.node_name: "failed"},
            'operation_execution_times': {**state.get('operation_execution_times', {}), self.node_name: execution_time}
        })
        
        self.logger.error(f"[{self.node_name.upper()}_NODE] ❌ Failed after {execution_time:.2f}s: {error}")
        return new_state


# ============================================================================
# LANGGRAPH STATE SCHEMA
# ============================================================================

class NLToPythonState(TypedDict):
    """
    🆕 Enhanced LangGraph state for NL to Python conversion workflow
    
    Tracks the complete state through all stages of the pipeline:
    - Input data and parameters
    - Stage outputs and intermediate results
    - Individual operation node execution
    - Error tracking and recovery state
    - Final results
    """
    
    # === INPUT STATE ===
    query: str
    df_columns: List[str] 
    df_sample: pl.DataFrame
    kwargs: Dict[str, Any]

    # 🆕 SESSION & SOURCE TRACKING
    session_id: str
    source_id: str
    session_manager: Optional[Any]  # SessionContextManager instance
    schema_manager: Optional[DynamicSchemaManager]  # Dynamic schema tracker
    
    # === STAGE 1 STATE ===
    stage1_result: Optional[Any]  # Stage1ColumnFilterSelection result
    stage1_errors: List[str]
    stage1_retry_count: int
    
    # === STAGE 1.5 STATE ===
    stage1_grounded: Optional[Any]  # Grounded stage1 result
    grounding_errors: List[str]
    grounding_retry_count: int
    
    # === STAGE 2 STATE ===  
    stage2_result: Optional[Any]  # Stage2AgenticPlan result
    stage2_errors: List[str]
    stage2_retry_count: int
    
    # === 🆕 OPERATION NODES STATE ===
    # Router state
    operation_routing_plan: Optional[Dict[str, Any]]  # Routing decisions and dependencies
    operation_queue: List[str]                        # Operations to execute in order
    
    # Operation execution state  
    operation_results: Dict[str, Any]                 # Results from each operation node
    operation_errors: Dict[str, List[str]]            # Errors per operation
    operation_status: Dict[str, str]                  # "pending", "in_progress", "completed", "failed"  
    operation_execution_times: Dict[str, float]       # Performance tracking per operation
    next_operation: Optional[str]                     # Next operation to execute
    
    # Results combination state
    combination_strategy: Optional[str]               # "sequential", "parallel", "conditional"
    intermediate_results: List[Any]                   # Results before final combination
    combination_errors: List[str]                     # Errors during result combination
    final_combined_result: Optional[Any]              # Combined result from all operations
    
    # === LEGACY STAGE 3 STATE (for backward compatibility) ===
    generated_code: Optional[str]
    code_generation_errors: List[str]
    
    # === FINAL OUTPUT STATE ===
    operation_type: Optional[str]
    confidence: float
    final_result: Optional[NLToPythonResult]
    
    # === RECOVERY & ROUTING STATE ===
    current_node: str
    recovery_strategies: List[str]
    max_retries: int
    should_use_fallback: bool
    
    # === METADATA ===
    start_time: float
    processing_time: Optional[float]
    node_execution_times: Dict[str, float]


# ============================================================================
# VALUE VALIDATION & GROUNDING
# ============================================================================

class ValueGroundingResult(BaseModel):
    """Result of value grounding process"""
    success: bool
    original_value: Any
    grounded_value: Optional[Any] = None
    method: Optional[str] = None  # "exact", "fuzzy", "semantic"
    confidence: float = 1.0
    suggestions: List[Any] = Field(default_factory=list)
    error: Optional[str] = None


def fuzzy_match_value(query_value: str, actual_values: List[Any], threshold: float = 0.75) -> Optional[Any]:
    """
    Fuzzy string matching for values
    
    Args:
        query_value: Value from LLM/user
        actual_values: Actual values in the dataframe column
        threshold: Minimum similarity score (0-1)
    
    Returns:
        Best matching value or None
    """
    best_match = None
    best_score = 0.0
    
    query_lower = str(query_value).lower()
    
    for actual_val in actual_values:
        actual_str = str(actual_val).lower()
        score = SequenceMatcher(None, query_lower, actual_str).ratio()
        
        if score > best_score and score >= threshold:
            best_score = score
            best_match = actual_val
    
    return best_match


def semantic_match_value(
    query_value: str,
    actual_values: List[Any],
    column_name: str,
    context_manager=None,
    session_id: str = None,
    source_id: str = None,
    query: str = ""
) -> Optional[Any]:
    """
    Context-aware semantic matching using session-based disambiguation
    
    Args:
        query_value: Value from LLM/user
        actual_values: Actual values in the dataframe column
        column_name: Column being filtered
        context_manager: DefaultContextManager instance
        session_id: Current session ID
        source_id: Current data source ID
        query: Original query for context
    
    Returns:
        Best matching value or None
    """
    # If no context manager, skip semantic matching
    if not context_manager:
        return None
    
    # Check session cache for learned mappings
    cached = context_manager.get_cached_disambiguation(
        session_id, source_id, column_name, query_value
    )
    
    if cached and cached in actual_values:
        return cached
    
    # No cache hit - build candidates for disambiguation
    candidates = [{
        'column': column_name,
        'actual_value': val,
        'sample_values': [val]
    } for val in actual_values]
    
    # Delegate to context manager's disambiguation logic
    if candidates:
        return context_manager.ask_user_for_column(
            filter_value=query_value,
            candidate_columns=candidates,
            query=query
        )
    
    return None


def ground_filter_value(
    filter_value: Any,
    actual_values: List[Any],
    column_name: str,
    context_manager=None,
    session_id: str = None,
    source_id: str = None,
    query: str = ""  
) -> ValueGroundingResult:
    """
    Validate and ground filter value against actual data
    
    Args:
        filter_value: Value from Stage 1 LLM output
        actual_values: Actual unique values in the column
        column_name: Name of the column (for logging)
        context_manager: Optional DefaultContextManager for semantic matching
        session_id: Session ID for context
        source_id: Data source ID for context
        query: Original query for context
    
    Returns:
        ValueGroundingResult with grounded value or error
    """
    
    # Handle list of values
    if isinstance(filter_value, list):
        grounded = []
        all_success = True
        methods = []
        
        for val in filter_value:
            # FIXED: use 'val' not 'value', and pass parameters not 'self.'
            result = ground_filter_value(
                val, actual_values, column_name,
                context_manager, session_id, source_id, query
            )
            if result.success:
                grounded.append(result.grounded_value)
                methods.append(result.method)
            else:
                all_success = False
        
        if all_success and grounded:
            return ValueGroundingResult(
                success=True,
                original_value=filter_value,
                grounded_value=grounded,
                method=f"list[{','.join(set(methods))}]",
                confidence=1.0
            )
        else:
            return ValueGroundingResult(
                success=False,
                original_value=filter_value,
                error=f"Could not ground all values in list",
                suggestions=list(actual_values[:5])
            )
    
    # Exact match
    if filter_value in actual_values:
        return ValueGroundingResult(
            success=True,
            original_value=filter_value,
            grounded_value=filter_value,
            method="exact",
            confidence=1.0
        )
    
    # Try semantic matching (context-aware)
    if isinstance(filter_value, str):
        semantic_match = semantic_match_value(
            filter_value, actual_values, column_name,
            context_manager, session_id, source_id, query
        )
        if semantic_match is not None:
            return ValueGroundingResult(
                success=True,
                original_value=filter_value,
                grounded_value=semantic_match,
                method="context_semantic",  # Changed from "semantic"
                confidence=0.95
            )
    
    # Try fuzzy matching
    if isinstance(filter_value, str):
        fuzzy_match = fuzzy_match_value(filter_value, actual_values, threshold=0.75)
        if fuzzy_match is not None:
            return ValueGroundingResult(
                success=True,
                original_value=filter_value,
                grounded_value=fuzzy_match,
                method="fuzzy",
                confidence=0.85
            )
    
    # Failed to ground - return suggestions
    if isinstance(filter_value, str):
        suggestions = []
        for actual_val in actual_values:
            score = SequenceMatcher(None, str(filter_value).lower(), str(actual_val).lower()).ratio()
            suggestions.append((actual_val, score))
        suggestions.sort(key=lambda x: x[1], reverse=True)
        top_suggestions = [s[0] for s in suggestions[:5]]
    else:
        top_suggestions = list(actual_values[:5])
    
    return ValueGroundingResult(
        success=False,
        original_value=filter_value,
        error=f"Value '{filter_value}' not found in column '{column_name}'",
        suggestions=top_suggestions
    )


# ============================================================================
# EXCEPTION FOR INTEGRATION
# ============================================================================

class MultiOperationQueryHandler:
    """
    Handler for queries requiring multiple operations that need to be split and processed separately.
    Created from original UserInputRequiredException pattern but adapted for operation handling.
    """
    
    def __init__(self, message: str, operations: List[str] = None, context: Dict[str, Any] = None):
        self.message = message
        self.operations = operations or []
        self.context = context or {}
        
    def get_operation_plan(self) -> Dict[str, Any]:
        """Returns the plan for handling multiple operations"""
        return {
            'message': self.message,
            'operations': self.operations,
            'context': self.context
        }
