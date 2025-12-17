"""
Natural Language to Python Code Generator - Workflow Module
Contains the main workflow orchestrator using LangGraph.

This module contains:
- Performance tracking and logging utilities
- Main NLToPythonGeneratorV5 class with LangGraph workflow
- Node implementations for each workflow stage
- Routing logic and error recovery strategies
- Legacy methods for backward compatibility
"""

import os
import re
import time
import json
import logging
from typing import Dict, List, Any, Optional, Tuple, get_args
from datetime import datetime, timedelta
import polars as pl
import numpy as np

# Import from other modules in our package
from .nl_to_python_schemas import (
    DefaultContextManager, create_stage1_schema, TemporalFilter, OperationParams, Stage2AgenticPlan,
    DynamicSchemaManager, ValidationResult, OperationResult, NLToPythonState,
    UserInputRequiredException, MultiOperationQueryHandler, VALID_OPERATIONS,
    setup_module_logger, traceable
)
from .nl_to_python_operations import (
    OPERATION_NODES, OperationRouterNode, ResultsCombinerNode, 
    PeriodComparisonNode, TimeSeriesNode, RankingNode, BreakdownNode,
    GroupedAggregationNode, WindowFunctionNode, PercentileNode,
    CompositionPercentageNode, PivotNode
)
from .nl_to_python_codegen import CODE_GENERATORS

# External imports
from models.schemas import NLToPythonResult, PandasOperation
from services.fuzzy_column_matcher import FuzzyColumnMatcher

# LangGraph imports
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

# LangSmith imports
from langsmith import traceable, Client as LangSmithClient
import langsmith

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

# Import scipy stats if available
try:
    from scipy import stats
    SCIPY_AVAILABLE = True
except Exception:
    stats = None
    SCIPY_AVAILABLE = False


# ============================================================================
# LANGGRAPH OBSERVABILITY & PERFORMANCE TRACKING
# ============================================================================

class LangGraphPerformanceTracker:
    """
    🆕 Performance tracking for LangGraph workflow execution
    
    Tracks execution times, success rates, and performance metrics
    for each node in the LangGraph workflow
    """
    
    def __init__(self):
        self.node_timings = {}      # node_name → list of execution times
        self.node_success_rates = {}  # node_name → (successes, failures)
        self.current_timers = {}    # node_name → start_time
        self.workflow_start_time = None
        self.logger = setup_module_logger('services.performance_tracker')
    
    def start_workflow_timing(self):
        """Start timing the entire workflow"""
        self.workflow_start_time = time.time()
        self.logger.debug("[PERF] Workflow timing started")
    
    def end_workflow_timing(self) -> float:
        """End workflow timing and return duration"""
        if self.workflow_start_time:
            duration = time.time() - self.workflow_start_time
            self.logger.debug(f"[PERF] Workflow completed in {duration:.2f}s")
            return duration
        return 0.0
    
    def start_node_timing(self, node_name: str):
        """Start timing a specific node"""
        self.current_timers[node_name] = time.time()
        self.logger.debug(f"[PERF] Started timing node: {node_name}")
    
    def end_node_timing(self, node_name: str, success: bool = True) -> float:
        """End timing for a node and record the result"""
        if node_name in self.current_timers:
            duration = time.time() - self.current_timers[node_name]
            del self.current_timers[node_name]
            
            # Record timing
            if node_name not in self.node_timings:
                self.node_timings[node_name] = []
            self.node_timings[node_name].append(duration)
            
            # Record success/failure
            if node_name not in self.node_success_rates:
                self.node_success_rates[node_name] = [0, 0]  # [successes, failures]
            
            if success:
                self.node_success_rates[node_name][0] += 1
            else:
                self.node_success_rates[node_name][1] += 1
            
            self.logger.debug(f"[PERF] Node {node_name} completed in {duration:.2f}s (success: {success})")
            return duration
        
        return 0.0
    
    def get_node_performance(self, node_name: str) -> Dict[str, Any]:
        """Get performance statistics for a specific node"""
        timings = self.node_timings.get(node_name, [])
        success_data = self.node_success_rates.get(node_name, [0, 0])
        
        if not timings:
            return {"error": "No timing data available"}
        
        return {
            "executions": len(timings),
            "avg_time": sum(timings) / len(timings),
            "min_time": min(timings),
            "max_time": max(timings),
            "total_time": sum(timings),
            "successes": success_data[0],
            "failures": success_data[1], 
            "success_rate": success_data[0] / (success_data[0] + success_data[1]) if (success_data[0] + success_data[1]) > 0 else 0.0
        }
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """Get overall performance summary for all nodes"""
        summary = {
            "total_nodes": len(self.node_timings),
            "nodes": {},
            "workflow_efficiency": 0.0,
            "performance_score": 0.0
        }
        
        total_time = 0.0
        total_successes = 0
        total_executions = 0
        
        for node_name in self.node_timings:
            node_perf = self.get_node_performance(node_name)
            summary["nodes"][node_name] = node_perf
            
            total_time += node_perf["total_time"]
            total_successes += node_perf["successes"]
            total_executions += node_perf["executions"]
        
        # Calculate efficiency metrics
        if total_executions > 0:
            summary["workflow_efficiency"] = total_successes / total_executions
            summary["avg_node_time"] = total_time / total_executions
            
            # Simple performance score (higher is better)
            success_factor = summary["workflow_efficiency"] * 100
            speed_factor = max(0, 100 - (summary["avg_node_time"] * 10))  # Penalize slow nodes
            summary["performance_score"] = (success_factor + speed_factor) / 2
        
        return summary
    
    def reset_metrics(self):
        """Reset all performance metrics"""
        self.node_timings.clear()
        self.node_success_rates.clear()
        self.current_timers.clear()
        self.workflow_start_time = None
        self.logger.debug("[PERF] Metrics reset")


class LangGraphStateLogger:
    """
    🆕 Structured logging for LangGraph state transitions and debugging
    
    Provides comprehensive logging for workflow state changes,
    routing decisions, and error contexts
    """
    
    def __init__(self, base_logger):
        self.logger = base_logger
        self.state_history = []  # Track state transitions for debugging
        self.error_contexts = []  # Track error contexts for analysis
        
    def log_state_transition(self, from_node: str, to_node: str, state: Dict[str, Any], reason: str = ""):
        """Log a state transition between workflow nodes"""
        
        transition = {
            "timestamp": datetime.now().isoformat(),
            "from_node": from_node,
            "to_node": to_node,
            "reason": reason,
            "state_summary": self._create_state_summary(state)
        }
        
        self.state_history.append(transition)
        
        self.logger.info(f"[STATE_TRANSITION] {from_node} → {to_node}: {reason}")
        self.logger.debug(f"[STATE_DETAIL] {transition}")
    
    def log_routing_decision(self, router_name: str, state: Dict[str, Any], 
                             decision: str, reasoning: str = ""):
        """Log a routing decision with context"""
        
        routing_info = {
            "timestamp": datetime.now().isoformat(),
            "router": router_name,
            "decision": decision,
            "reasoning": reasoning,
            "state_context": self._create_routing_context(state)
        }
        
        self.logger.info(f"[ROUTING] {router_name} → {decision}: {reasoning}")
        self.logger.debug(f"[ROUTING_DETAIL] {routing_info}")
    
    def log_error_context(self, error_location: str, error: Exception, 
                          state: Dict[str, Any], recovery_info: Dict[str, Any] = None):
        """Log error context for debugging and recovery analysis"""
        
        error_context = {
            "timestamp": datetime.now().isoformat(),
            "location": error_location,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "state_summary": self._create_state_summary(state),
            "recovery_info": recovery_info or {},
            "stack_trace": logging.getLogger().isEnabledFor(logging.DEBUG)
        }
        
        self.error_contexts.append(error_context)
        
        self.logger.error(f"[ERROR_CONTEXT] {error_location}: {type(error).__name__} - {str(error)}")
        
        if recovery_info:
            self.logger.info(f"[RECOVERY] {recovery_info}")
    
    def _create_state_summary(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Create a concise summary of workflow state for logging"""
        
        return {
            "query_length": len(state.get("query", "")),
            "columns_count": len(state.get("df_columns", [])),
            "has_stage1_result": bool(state.get("stage1_result")),
            "has_stage1_grounded": bool(state.get("stage1_grounded")),
            "has_stage2_result": bool(state.get("stage2_result")),
            "has_final_result": bool(state.get("final_result")),
            "retry_counts": {
                "stage1": state.get("stage1_retry_count", 0),
                "stage2": state.get("stage2_retry_count", 0),
                "grounding": state.get("grounding_retry_count", 0)
            },
            "confidence": state.get("confidence", 0.0),
            "current_node": state.get("current_node", "unknown"),
            "operation_status": state.get("operation_status", {}),
            "errors_present": bool(state.get("stage1_errors") or state.get("stage2_errors") or state.get("grounding_errors"))
        }
    
    def _create_routing_context(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Create routing-specific context for decision analysis"""
        
        return {
            "retry_counts": {
                "stage1": state.get("stage1_retry_count", 0),
                "stage2": state.get("stage2_retry_count", 0)
            },
            "confidence": state.get("confidence", 0.0),
            "has_errors": bool(state.get("stage1_errors") or state.get("stage2_errors")),
            "operation_queue": state.get("operation_queue", []),
            "current_node": state.get("current_node", "unknown")
        }
    
    def get_state_history(self) -> List[Dict[str, Any]]:
        """Get the complete state transition history"""
        return self.state_history.copy()
    
    def get_error_contexts(self) -> List[Dict[str, Any]]:
        """Get all error contexts for analysis"""
        return self.error_contexts.copy()
    
    def clear_history(self):
        """Clear state history and error contexts"""
        self.state_history.clear()
        self.error_contexts.clear()
        self.logger.debug("[STATE_LOGGER] History cleared")


# ============================================================================
# MAIN GENERATOR CLASS (V5 - HALLUCINATION-FREE)
# ============================================================================

class NLToPythonGeneratorV5:
    """
    Hallucination-Free Natural Language to Python Code Generator (V5)
    
    Two-Stage Architecture:
        Stage 1: Strict enum-based column & filter selection (JSON strict mode)
        Stage 2: Agentic temporal context + tool calling
    
    UPDATED: Unified period_comparison operation with comprehensive granularity support
    UPDATED: Added time_series operation for continuous time progression
    Compatible with existing V4 architecture
    """
    
    # Constants from V4
    CURRENT_YEAR = datetime.now().year
    
    GRANULARITY_MAP = {
        'day': 'D',
        'week': 'W',
        'month': 'M',
        'quarter': 'Q',
        'year': 'Y'
    }
    
    def __init__(
        self,
        openai_client: Optional[OpenAI] = None,
        openai_api_key: Optional[str] = None,
        context_manager: Optional[DefaultContextManager] = None,
        session_manager = None,  # 🆕 NEW: SessionContextManager instance
        session_id: str = None,  # 🆕 NEW: Current session ID
        source_id: str = None,   # 🆕 NEW: Current source ID

        use_agentic_mode: bool = True,
        value_grounding_threshold: float = 0.75,
        smart_aggregation_service = None,  #Changes for percentage by Aniket - Smart aggregation service
    ):
        """
            Initialize the generator
        
    Args:
        openai_client: OpenAI client instance
        openai_api_key: OpenAI API key (if client not provided)
        context_manager: User interaction handler
        session_manager: 🆕 SessionContextManager for disambiguation caching
        session_id: 🆕 Current session ID
        source_id: 🆕 Current data source ID
        use_agentic_mode: If True, use two-stage agentic approach
        value_grounding_threshold: Threshold for fuzzy value matching (0-1)
        smart_aggregation_service: SmartAggregationDecider instance
        """
        # Initialize logger
        self.logger = setup_module_logger('services.NL_to_python')
        
        #Changes for percentage by Aniket - Store smart aggregation service
        self.smart_aggregation_service = smart_aggregation_service
        if smart_aggregation_service:
            self.logger.info("[INIT] Smart aggregation service enabled")
        else:
            self.logger.info("[INIT] Smart aggregation service not provided, using defaults")
        
        # Initialize fuzzy matcher (from V4)
        self.fuzzy_matcher = FuzzyColumnMatcher(threshold=70)
        
        # Initialize context manager
        if context_manager:
            self.context_manager = context_manager
        elif session_manager:
            # Create context manager with session integration
            self.context_manager = DefaultContextManager(
            session_manager=session_manager,
            session_id=session_id,
            source_id=source_id
            )
        else:
            # Fallback to basic context manager
            self.context_manager = DefaultContextManager()
        # Store session context
        self.session_id = session_id or f"session_{datetime.now().timestamp()}"
        self.source_id = source_id or "default"
        # Value grounding threshold
        self.value_grounding_threshold = value_grounding_threshold
        
        # Initialize OpenAI client
        if openai_client:
            self.client = openai_client
            self.use_llm = True
            self.logger.info("[LLM_INIT] Using provided OpenAI client")
        elif OPENAI_AVAILABLE:
            api_key = openai_api_key or os.getenv('OPENAI_API_KEY')
            if api_key:
                self.client = OpenAI(api_key=api_key)
                self.use_llm = True
                self.logger.info("[LLM_INIT] OpenAI client initialized successfully")
            else:
                self.client = None
                self.use_llm = False
                self.logger.warning("[LLM_INIT] OpenAI API key not found")
        else:
            self.client = None
            self.use_llm = False
            self.logger.warning("[LLM_INIT] OpenAI library not available")
        
        self.use_agentic_mode = use_agentic_mode and self.use_llm
        
        if self.use_agentic_mode:
            self.logger.info("[MODE] Running in TWO-STAGE AGENTIC mode (hallucination-free)")
        else:
            self.logger.info("[MODE] Agentic mode disabled")
        
        # 🆕 Initialize LangGraph workflow orchestration (ALWAYS ON)
        self.langgraph_workflow = None  # Will be built on-demand
        self.logger.info("[LANGGRAPH] LangGraph orchestration ENABLED with error recovery")
        
        # 🆕 Load workbook metadata for identifier detection
        self.workbook_metadata = self._load_all_workbook_metadata()
        self.logger.info(f"[METADATA] Loaded metadata for {len(self.workbook_metadata)} workbook(s)")
        
        # 🆕 Initialize observability and performance tracking (ALWAYS ON)
        self.performance_tracker = LangGraphPerformanceTracker()
        self.state_logger = LangGraphStateLogger(self.logger)
        self.logger.info("[OBSERVABILITY] Performance tracking and structured logging ENABLED")
        
        # Placeholder for smart aggregation decider (compatibility)
        self.smart_aggregation_decider = None
    
    def set_smart_aggregation(self, smart_decider):
        """Set smart aggregation decider (for compatibility)"""
        self.smart_aggregation_decider = smart_decider
        self.logger.info("[SMART_AGGREGATION] Smart aggregation decider set")

    def _load_all_workbook_metadata(self) -> Dict[str, Any]:
        """
        Load workbook_data_summary.json once at initialization
        Thread-safe: read-only access, each request gets its own workbook slice
        
        Returns:
            Dict mapping workbook_name -> metadata
        """
        try:
            metadata_path = os.path.join(os.getcwd(), 'workbook_data_summary.json')
            if os.path.exists(metadata_path):
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
                    return metadata
            else:
                self.logger.warning(f"[METADATA] workbook_data_summary.json not found at {metadata_path}")
                return {}
        except Exception as e:
            self.logger.error(f"[METADATA] Failed to load workbook metadata: {e}", exc_info=True)
            return {}
    
    def _load_chart_calculated_fields(self, chart_name: str, workbook_name: str) -> List[Dict]:
        """
        Load calculated field definitions from chart metadata as hints for Stage 1.
        
        These hints help the LLM understand that queries like "cpw in may" should
        use the calculated field formula (SUM(cost)/SUM(twc)) rather than the raw
        cpw_std_all column.
        
        Args:
            chart_name: Name of the selected chart
            workbook_name: Name of the workbook
        
        Returns:
            List of calculated field hints:
            [
                {
                    "name": "CPW",
                    "type": "ratio",
                    "numerator": "cost",
                    "denominator": "twc",
                    "formula_display": "SUM([cost]) / SUM([twc])"
                },
                ...
            ]
        """
        if not chart_name or not workbook_name:
            return []
        
        try:
            # Load Tableau metadata
            safe_name = workbook_name.replace(' ', '')
            metadata_path = os.path.join(
                'tableau_metadata',
                safe_name,
                f'metadata_{safe_name}.json'
            )
            
            if not os.path.exists(metadata_path):
                self.logger.debug(f"[STAGE1_HINTS] No Tableau metadata at: {metadata_path}")
                return []
            
            with open(metadata_path, 'r') as f:
                metadata = json.load(f)
            
            # Find the chart
            chart_metadata = None
            for sheet in metadata.get('worksheets', []):
                if sheet.get('name') == chart_name:
                    chart_metadata = sheet
                    break
            
            if not chart_metadata:
                self.logger.debug(f"[STAGE1_HINTS] Chart '{chart_name}' not found in metadata")
                return []
            
            # Parse calculated fields for ratio formulas
            hints = []
            for calc_field in chart_metadata.get('calculated_fields_ordered', []):
                formula = calc_field.get('formula', '')
                name = calc_field.get('name', '')
                
                # Parse ratio formulas: SUM([x])/SUM([y])
                ratio_match = re.search(
                    r'SUM\(\[([^\]]+)\]\)\s*/\s*SUM\(\[([^\]]+)\]\)',
                    formula,
                    re.IGNORECASE
                )
                
                if ratio_match:
                    hints.append({
                        'name': name,
                        'type': 'ratio',
                        'numerator': ratio_match.group(1),
                        'denominator': ratio_match.group(2),
                        'formula_display': f"SUM([{ratio_match.group(1)}]) / SUM([{ratio_match.group(2)}])"
                    })
                    self.logger.info(f"[STAGE1_HINTS] Found ratio field: {name} = {ratio_match.group(1)}/{ratio_match.group(2)}")
            
            return hints
            
        except Exception as e:
            self.logger.warning(f"[STAGE1_HINTS] Could not load chart hints: {e}")
            return []
    
    def _is_identifier_column(self, column_name: str, workbook_name: str, df_sample: pl.DataFrame) -> bool:
        """
        Multi-signal identifier detection using workbook metadata
        Handles repeated IDs using cardinality and semantic checks
        Zero hardcoding - relies on metadata classification
        
        Args:
            column_name: Column to check
            workbook_name: Workbook name for metadata lookup
            df_sample: Sample dataframe for statistical checks
            
        Returns:
            True if column is an identifier (record ID, transaction number, etc.)
        """
        # Get metadata for this specific workbook
        self.logger.info(f"🔍 DEBUG: _is_identifier_column called with column='{column_name}', workbook='{workbook_name}'")
        workbook_meta = self.workbook_metadata.get(workbook_name, {})
        self.logger.info(f"🔍 DEBUG:   workbook_meta found = {workbook_meta is not None and len(workbook_meta) > 0}")
        
        if not workbook_meta:
            self.logger.debug(f"[IDENTIFIER_DETECT] No metadata for workbook '{workbook_name}'")
            return False
        
        # ============================================================
        # TIER 1: Metadata Classification (PRIMARY - Works for ALL)
        # ============================================================
        categorical_meta = workbook_meta.get('categorical_columns', {}).get(column_name, {})
        self.logger.info(f"🔍 DEBUG:   categorical_meta found = {categorical_meta is not None and len(categorical_meta) > 0}")
        if categorical_meta:
            self.logger.info(f"🔍 DEBUG:   column_type = '{categorical_meta.get('column_type')}'")
        
        # 🆕 CRITICAL CHECK: Exclude numeric/quantitative columns from identifier detection
        # Numeric metrics like time_to_close, revenue, etc. can have high cardinality but are NOT identifiers
        quantitative_meta = workbook_meta.get('quantitative_columns', {}).get(column_name, {})
        if quantitative_meta:
            self.logger.info(f"❌ [NUMERIC_SKIP] '{column_name}' is a quantitative column - NOT an identifier")
            return False
        
        # Also check if column name suggests it's a numeric metric (time, duration, amount, etc.)
        numeric_indicators = ['time', 'duration', 'amount', 'price', 'cost', 'revenue', 'value', 'rate', 'age', 'hours', 'minutes', 'seconds', 'days']
        if any(indicator in column_name.lower() for indicator in numeric_indicators):
            # Double-check by looking at actual data type in df_sample if available
            if df_sample is not None and column_name in df_sample.columns:
                try:
                    # Check if column has numeric-like data
                    sample_col = df_sample[column_name].drop_nulls().head(100)
                    # Try to cast to float - if it works, it's numeric
                    try:
                        sample_col.cast(pl.Float64, strict=False)
                        self.logger.info(f"❌ [NUMERIC_SKIP] '{column_name}' contains numeric values - NOT an identifier")
                        return False
                    except:
                        pass  # Not numeric, continue with identifier checks
                except:
                    pass  # Error checking sample, continue with identifier checks
        
        # Signal 1A: Tableau's direct classification
        if categorical_meta.get('column_type') == 'identifier':
            self.logger.info(f"✅ [TIER 1A] '{column_name}' marked as identifier in metadata")
            return True
        
        # Signal 1B: Cardinality check (handles repeated IDs)
        unique_count = categorical_meta.get('unique', 0)
        total_count = categorical_meta.get('count', 1)
        if total_count > 0:
            MIN_SAMPLES_PER_CATEGORY = 10  # From causal_feature_importance
            max_practical_categories = total_count // MIN_SAMPLES_PER_CATEGORY
            if unique_count > max_practical_categories:
                self.logger.info(f"✅ [TIER 1B] '{column_name}' has {unique_count} unique values (max practical: {max_practical_categories}) - identifier detected")
                return True
        
        # Signal 1C: Sparsity ratio (>50% unique)
        if total_count > 0:
            unique_ratio = unique_count / total_count
            if unique_ratio > 0.5:
                self.logger.info(f"✅ [TIER 1C] '{column_name}' is {unique_ratio:.1%} unique - identifier detected")
                return True
        
        # ============================================================
        # TIER 2: Calculated Field Semantic Analysis (ENHANCEMENT)
        # Only applies if column is a calculated field
        # ============================================================
        calc_meta = workbook_meta.get('calculated_columns', {}).get(column_name, {})
        if calc_meta:
            formula = calc_meta.get('formula', '')
            if formula:
                # Extract referenced columns: [column_name] pattern
                import re
                referenced_cols = re.findall(r'\[(\w+)\]', formula)
                
                # Check if ANY referenced column is an identifier
                for ref_col in referenced_cols:
                    if ref_col != column_name:  # Avoid recursion
                        ref_cat_meta = workbook_meta.get('categorical_columns', {}).get(ref_col, {})
                        if ref_cat_meta.get('column_type') == 'identifier':
                            self.logger.info(f"✅ [TIER 2] '{column_name}' calculated field returns identifier '{ref_col}'")
                            return True
        
        self.logger.debug(f"❌ '{column_name}' not detected as identifier")
        return False

    @traceable(name="langgraph_workflow_execution")
    def generate_python_code(
        self,
        query: str,
        df_columns: List[str],
        df_sample: pl.DataFrame,
        workbook_name: Optional[str] = None,
        **kwargs
    ) -> Optional[NLToPythonResult]:
        """
        Main entry point - generates python code from natural language using LangGraph workflow
        
        This is a simplified version that uses the original implementation approach
        while leveraging the new modular architecture where possible.
        
        Args:
            query: Natural language query
            df_columns: List of dataframe column names
            df_sample: Sample of the dataframe
            workbook_name: Name of the workbook (for metadata lookup)
            **kwargs: Additional parameters
            
        Returns:
            NLToPythonResult object or None
        """
        
        # Store workbook_name for use in helper methods
        self._current_workbook_name = workbook_name
        
        self.logger.info(f"🔍 DEBUG: _current_workbook_name set to = '{workbook_name}'")
        self.logger.info(f"🔍 DEBUG: Metadata loaded for workbooks: {list(self.workbook_metadata.keys())}")
        
        if not workbook_name:
            self.logger.error(f"❌ CRITICAL: workbook_name not provided!")
            self.logger.error(f"   Identifier detection DISABLED - may produce incorrect results")
            self.logger.error(f"   Falling back to existing smart aggregation (less reliable)")
        elif workbook_name not in self.workbook_metadata:
            self.logger.error(f"❌ METADATA MISSING: No metadata for workbook '{workbook_name}'")
            self.logger.error(f"   Available workbooks: {list(self.workbook_metadata.keys())[:5]}")
            self.logger.error(f"   Identifier detection DISABLED - may produce incorrect results")
        
        self.logger.info(f"[GENERATE] Processing query: {query}")
        
        # Extract selected_chart from kwargs if available
        selected_chart = kwargs.get('selected_chart', None)
        
        try:
            # For now, use a simplified approach that delegates to the stage methods
            # This ensures compatibility while using the new modular components
            
            # Stage 1: Column & Filter Selection (with optional chart hints)
            stage1_result = self._stage1_column_filter_selection(query, df_columns, df_sample, selected_chart=selected_chart)
            if not stage1_result:
                self.logger.error("[GENERATE] Stage 1 failed")
                return None
            
            # Stage 1.5: Value Grounding
            stage1_grounded = self._stage1_5_value_grounding(stage1_result, df_sample, {})
            if not stage1_grounded:
                stage1_grounded = stage1_result  # Fallback
            
            # Stage 2: Agentic Planning
            stage2_result = self._stage2_agentic_planning(stage1_grounded, df_sample, query)
            if not stage2_result:
                self.logger.error("[GENERATE] Stage 2 failed")
                return None
            
            # 🆕 EARLY EXIT FOR COLUMN DESCRIPTIONS - BYPASS STAGE 3
            operation_type = stage2_result.operations[0] if stage2_result.operations else "grouped_aggregation"
            if operation_type == 'column_description':
                self.logger.info("[GENERATE] 🎯 Column description detected - using direct metadata lookup")
                return self._handle_column_description_direct(stage1_grounded, stage2_result, query, df_sample)
            
            # Stage 3: Code Generation (for analytical operations only)
            generated_code = self._stage3_code_generation(stage1_grounded, stage2_result, query, df_sample)
            if not generated_code:
                self.logger.error("[GENERATE] Stage 3 failed")
                return None
            
            # Create final result
            operation_type = stage2_result.operations[0] if stage2_result.operations else "grouped_aggregation"
            chart_type = self._suggest_chart_type(operation_type)
            confidence = stage2_result.confidence if hasattr(stage2_result, 'confidence') else 0.7
            
            # Detect ranking query flags for proper display sorting
            from services.nlp_to_python.nl_to_python_codegen import RankingCodeGen
            is_top_query = RankingCodeGen._detect_top_query(query)
            is_bottom_query = RankingCodeGen._detect_bottom_query(query)
            
            final_result = NLToPythonResult(
                original_query=query,
                generated_code=generated_code,
                operation_type=operation_type,
                confidence=confidence,
                suggested_chart_type=chart_type,
                explanation=stage2_result.reasoning if hasattr(stage2_result, 'reasoning') else "Code generated successfully",
                is_bottom_query=is_bottom_query,
                is_top_query=is_top_query,
                # 🆕 POPULATE METADATA FROM STAGE1 (for entity extraction & conversation memory)
                group_by_columns=stage1_grounded.group_by_columns if stage1_grounded.group_by_columns else None,
                metric_column=stage1_grounded.metric_column if stage1_grounded.metric_column else None,
                filter_column=stage1_grounded.filter_column if stage1_grounded.filter_column else None
            )
            
            # Log metadata that was captured from Stage1
            self.logger.info(f"[GENERATE] ✅ Success - Generated {len(generated_code)} chars of code")
            self.logger.info(f"[GENERATE] 📊 Metadata captured from Stage1:")
            self.logger.info(f"  - group_by_columns: {final_result.group_by_columns}")
            self.logger.info(f"  - metric_column: {final_result.metric_column}")
            self.logger.info(f"  - filter_column: {final_result.filter_column}")
            return final_result
            
        except Exception as e:
            self.logger.error(f"[GENERATE] ❌ Error: {str(e)}", exc_info=True)
            return None
    
    def _stage1_column_filter_selection(self, query: str, df_columns: List[str], df_sample: pl.DataFrame, selected_chart: Optional[str] = None):
        """
        Stage 1: Column & Filter Selection using strict schema
        
        🆕 Enhanced with calculated field hints from chart metadata
        """
        if not self.use_agentic_mode:
            return self._fallback_stage1(query, df_columns)
        
        # 🆕 Load calculated field hints from chart metadata (if chart is selected)
        calc_field_hints = []
        if selected_chart and self._current_workbook_name:
            calc_field_hints = self._load_chart_calculated_fields(
                chart_name=selected_chart,
                workbook_name=self._current_workbook_name
            )
        
        # Build hints section for prompt
        hints_section = ""
        if calc_field_hints:
            hints_section = "\n\n📊 CALCULATED FIELDS FROM CHART (optional hints):\n"
            for hint in calc_field_hints:
                hints_section += f"  • '{hint['name']}' (ratio): {hint['numerator']} ÷ {hint['denominator']}\n"
                hints_section += f"    Formula: {hint['formula_display']}\n"
            hints_section += "\n💡 If the query asks for a ratio metric (cost per X, X per Y, etc.) AND a matching calculated field exists above, use metric_type='calculated_field'.\n"
        
        try:
            # Create strict schema
            column_samples = {col: df_sample[col].drop_nulls().unique().limit(5).to_list() for col in df_columns}
            Stage1Schema = create_stage1_schema(df_columns, column_samples)
            
            # Enhanced LLM prompt with calculated field hints
            messages = [
                {"role": "system", "content": f"""You are a data analysis assistant. Extract column selections and filters from natural language queries.

Available columns: {df_columns}
Column samples: {column_samples}{hints_section}

Rules:
1. ONLY select columns that exist in the available columns list
2. For filters, extract the intent but don't validate values yet (Stage 1.5 will handle that)
3. Identify temporal expressions (dates, quarters, etc.) in temporal_intent
4. Identify the high-level operation type in operation_intent

🆕 CALCULATED FIELD RULES:
- If the query asks for a ratio/rate (e.g., "cost per word", "price per unit", "X per Y")
  AND a matching calculated field exists in the hints above
  → Set metric_type='calculated_field' and provide calculated_field_name and calculated_field_formula
- Otherwise (simple column lookup, totals, counts, etc.)
  → Set metric_type='column' and provide metric_column

Column Description Queries:
- If user asks "what does X mean", "describe X", "explain X", "tell me about X" where X is a column/field, set operation_intent='column_description'
- Put the column name in secondary_columns (since it's not being filtered/grouped/aggregated)
- Examples: "What does Account Status mean?" → operation_intent='column_description', secondary_columns=['Account Status']"""},
                {"role": "user", "content": f"Query: {query}"}
            ]
            
            response = self.client.beta.chat.completions.parse(
                model="gpt-4o-2024-08-06",
                messages=messages,
                response_format=Stage1Schema,
                temperature=0
            )
            
            result = response.choices[0].message.parsed
            self.logger.info(f"[STAGE1] ✅ Column selection: filter={result.filter_column}, group_by={result.group_by_columns}, metric={result.metric_column}")
            return result
            
        except Exception as e:
            self.logger.error(f"[STAGE1] Error: {e}")
            return self._fallback_stage1(query, df_columns)
    
    def _fallback_stage1(self, query: str, df_columns: List[str]):
        """Fallback Stage 1 when LLM is not available"""
        # Simple heuristic-based column selection
        class FallbackResult:
            def __init__(self):
                self.filter_column = None
                self.filter_operator = None
                self.filter_value = None
                self.additional_filters = None
                self.group_by_columns = None
                self.metric_column = None
                self.date_column = None
                self.secondary_columns = None
                self.temporal_intent = None
                self.operation_intent = 'count'
                self.window_specification = None
                self.reasoning = 'Fallback heuristic selection'
        
        result = FallbackResult()
        
        # 🔥 COLLISION DETECTION FIX BY ANIKET - START
        # Detect composition/breakdown queries where group_by should not equal metric_column
        query_lower = query.lower()
        
        # Check for composition/breakdown keywords
        composition_keywords = ['composition', 'breakdown', 'percentage', 'percent', 'distribution', 
                               'proportion', '% of', 'percentage of']
        is_composition_query = any(keyword in query_lower for keyword in composition_keywords)
        
        if is_composition_query:
            self.logger.info("[FALLBACK_STAGE1] 🔥 Detected composition/breakdown query - Collision detection by Aniket")
            
            # For composition queries, find the grouping column (usually after "by")
            # e.g., "percentage composition by [dimension]" -> group_by = ['dimension_column']
            
            # Simple heuristic: look for column names in the query
            found_columns = [col for col in df_columns if col.lower() in query_lower]
            
            if found_columns:
                # Use the first found column as group_by
                result.group_by_columns = [found_columns[0]]
                # 🔥 KEY FIX: Don't set metric_column to the same as group_by - set to None
                # This prevents the "cannot insert column, already exists" pandas error
                result.metric_column = None  # Will use size() for counting instead of column.count()
                result.operation_intent = 'composition'
                self.logger.info(f"[FALLBACK_STAGE1] 🔥 Composition query detected: group_by={result.group_by_columns}, metric=None (will count) - Collision avoided by Aniket")
                return result
        # 🔥 COLLISION DETECTION FIX BY ANIKET - END
        
        # Enhanced fallback: detect temporal queries
        months = ['january', 'february', 'march', 'april', 'may', 'june',
                  'july', 'august', 'september', 'october', 'november', 'december']
        
        # Check if query contains month names - treat as temporal intent
        for month in months:
            if month in query_lower:
                result.temporal_intent = month
                # Find potential date column
                date_cols = [col for col in df_columns if 'date' in col.lower() or 'month' in col.lower() or 'time' in col.lower()]
                if date_cols:
                    result.date_column = date_cols[0]  # Use first date-like column
                self.logger.info(f"[FALLBACK] Detected temporal query: {month} -> temporal_intent, date_column={result.date_column}")
                break
        
        return result
    
    def _stage1_5_value_grounding(self, stage1_result, df_sample: pl.DataFrame, state: Dict[str, Any]):
        """Stage 1.5: Value Grounding & Validation"""
        # For now, return the original result - value grounding can be enhanced later
        return stage1_result
    
    def _stage2_agentic_planning(self, stage1_grounded, df_sample: pl.DataFrame, query: str = ""):
        """Stage 2: Agentic Planning"""
        # Let LLM handle all operations including column descriptions
        
        if not self.use_agentic_mode:
            return self._fallback_stage2(stage1_grounded, query)
        
        # 🔥 ROBUST FIX: Detect if temporal filter is expected (for fix-up after LLM)
        # Don't bypass LLM - let it determine agg_functions correctly
        temporal_filter_expected = None
        if (hasattr(stage1_grounded, 'filter_value') and 
            hasattr(stage1_grounded, 'filter_column') and
            isinstance(stage1_grounded.filter_value, str)):
            
            month_names = ['january', 'february', 'march', 'april', 'may', 'june',
                           'july', 'august', 'september', 'october', 'november', 'december']
            
            filter_value_lower = stage1_grounded.filter_value.lower()
            if (filter_value_lower in month_names and 
                stage1_grounded.filter_column and 
                any(keyword in stage1_grounded.filter_column.lower() for keyword in ['date', 'month', 'time', 'created'])):
                
                # Store expected temporal filter info for fix-up (but don't bypass LLM)
                month_mapping = {
                    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
                    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12
                }
                temporal_filter_expected = {
                    'filter_type': 'specific_month',
                    'month': month_mapping[filter_value_lower],
                    'filter_column': stage1_grounded.filter_column
                }
                self.logger.info(f"[STAGE2] Temporal filter expected: {filter_value_lower} (month={month_mapping[filter_value_lower]}) - will fix-up if LLM misses it")
        
        # 🆕 NEW: Check if Stage 1 identified a calculated field
        metric_type = getattr(stage1_grounded, 'metric_type', 'column')
        
        if metric_type == 'calculated_field':
            calc_field_name = getattr(stage1_grounded, 'calculated_field_name', None)
            formula = getattr(stage1_grounded, 'calculated_field_formula', None)
            
            if formula and isinstance(formula, dict) and 'numerator' in formula and 'denominator' in formula:
                self.logger.info(f"[STAGE2] ✅ Stage 1 identified calculated field: {calc_field_name}")
                self.logger.info(f"[STAGE2]    Formula: {formula['numerator']} / {formula['denominator']}")
                self.logger.info(f"[STAGE2]    Skipping LLM aggregation - using deterministic ratio formula")
                
                # Build temporal filters from Stage 1 (existing logic for month filters)
                temporal_filters = []
                if temporal_filter_expected:
                    from .nl_to_python_schemas import TemporalFilter
                    temporal_filters.append(TemporalFilter(
                        filter_type=temporal_filter_expected['filter_type'],
                        month=temporal_filter_expected['month']
                    ))
                
                # Create a Stage2AgenticPlan for ratio operation
                from .nl_to_python_schemas import Stage2AgenticPlan
                return Stage2AgenticPlan(
                    operations=['ratio'],
                    temporal_filters=temporal_filters,
                    agg_functions=[],  # Not used for ratios
                    group_by_columns=stage1_grounded.group_by_columns or [],
                    confidence=1.0,
                    reasoning=f"Calculated field '{calc_field_name}' with ratio formula: {formula['numerator']}/{formula['denominator']}",
                    # Store formula info in custom fields (will be accessed in Stage 3)
                    numerator_column=formula['numerator'],
                    column=formula['denominator']  # Using 'column' field to store denominator
                )
        
        try:
            messages = [
                {"role": "system", "content": f"""You are a data analysis planner. Create an execution plan based on column selections.

Available operations: {list(get_args(VALID_OPERATIONS))}

Create a plan with:
1. Temporal filters if needed
2. Operation sequence 
3. Parameters for each operation

Special handling:
- If operation_intent='column_description', use operations=['column_description'] with high confidence (0.95)
- Column description queries don't need temporal filters or complex aggregations

CRITICAL - Distinguishing Aggregation from Ranking:

1. Ranking Queries (top/bottom/lowest/highest with comparative intent):
   Query patterns:
   - "[DIMENSION] with lowest [METRIC]"
   - "[DIMENSION] with highest [METRIC]" 
   - "top [N] [DIMENSION] by [METRIC]"
   - "bottom [N] [DIMENSION] by [METRIC]"
   - "which [DIMENSION] has most/least [METRIC]"
   
   Operation logic:
   - Use operations=['grouped_aggregation']
   - Set agg_functions=['count'] for counting metrics (identifiers, ticket numbers, case numbers, etc.)
   - Set agg_functions=['sum']/['mean']/etc. for numeric metrics
   - Do NOT set agg_functions=['min'] or ['max'] 
   - The system handles sorting via is_top_query/is_bottom_query detection
   
2. Aggregation Queries (computing min/max/avg value within groups):
   Query patterns:
   - "minimum [METRIC] per [DIMENSION]"
   - "maximum [METRIC] by [DIMENSION]"
   - "average [METRIC] for each [DIMENSION]"
   - "min/max/mean/median of [METRIC]"
   
   Operation logic:
   - Use operations=['grouped_aggregation']
   - Set agg_functions=['min'], ['max'], ['mean'], ['median'] etc.
   - These compute statistical aggregates within groups

Key Distinction:
- "which [DIMENSION] has lowest [METRIC_COUNT]" → rank by count → agg_functions=['count']
- "lowest [METRIC_VALUE] per [DIMENSION]" → min within group → agg_functions=['min']

Examples:
  Query: "which [dimension_col] has lowest [count_metric]"
  → agg_functions=['count'] (NOT ['min'])
  
  Query: "minimum [value_metric] per [dimension_col]"
  → agg_functions=['min']

For period_comparison operations:
- Extract specific periods being compared from temporal_intent (e.g., 'Q1 2025', 'Q2 2025', 'February 2025', 'March 2025')
- Set compare_periods with the extracted periods as a list (e.g., ['Q1 2025', 'Q2 2025'])
- Create temporal_filters array with one TemporalFilter object per period:
  * For quarters: filter_type='specific_quarter', quarter=<number>, year=<year>
  * For months: filter_type='specific_month', month=<number>, year=<year>
  * For years: filter_type='specific_year', year=<year>
- Do NOT rely on filter_column/filter_value from Stage 1 for temporal comparisons - they should be in temporal_filters instead

CRITICAL - group_by_columns for period_comparison:
- If comparing ONLY two time periods (e.g., "Q1 vs Q2", "January vs February", "2023 vs 2024") WITHOUT additional dimensions:
  → Set group_by_columns = [] or null (to aggregate entire periods into single values)
  → This ensures result has 2 rows: one for each period with totals
  
- If comparing periods WITH breakdown by a business dimension (e.g., "compare by country Q1 vs Q2", "product sales Q1 vs Q2"):
  → Set group_by_columns = [dimension_column] (e.g., ['dimension_col_1'], ['dimension_col_2'])
  → Remove any date-related columns from group_by_columns (month, week, day, create_month, etc.)
  → This ensures result has one row per dimension value, with separate columns for each period
  
- ALWAYS remove date/time columns from group_by_columns for period_comparison operations:
  → Date columns belong in temporal_filters, NOT group_by_columns
  → Common date columns to remove: create_month, create_week, create_day, date, month, week, day, year, quarter
  
Examples:
  Query: "compare [metric_column] [period1] vs [period2]"
  → group_by_columns = [] (simple period comparison, no dimensions)
  
  Query: "compare [metric_column] by [dimension] [period1] vs [period2]"
  → group_by_columns = [dimension_column] (has business dimension)
  
  Query: "compare [filter_value] [metric_column] [period1] vs [period2]" (if Stage 1 had group_by=['date_column'])
  → group_by_columns = [] (override Stage 1, remove date column)"""},
                {"role": "user", "content": f"""
Original Query: {query}

Stage 1 Results:
- Filter: {stage1_grounded.filter_column} = {stage1_grounded.filter_value}
- Group by: {stage1_grounded.group_by_columns}
- Metric: {stage1_grounded.metric_column}
- Date: {stage1_grounded.date_column}
- Temporal intent: {stage1_grounded.temporal_intent}
- Operation intent: {stage1_grounded.operation_intent}

Create execution plan:"""}
            ]
            
            response = self.client.beta.chat.completions.parse(
                model="gpt-4o-2024-08-06", 
                messages=messages,
                response_format=Stage2AgenticPlan,
                temperature=0
            )
            
            result = response.choices[0].message.parsed
            self.logger.info(f"[STAGE2] ✅ Plan: operations={result.operations}, confidence={result.confidence}")
            self.logger.info(f"[STAGE2_DEBUG] group_by_columns from Stage 2: {result.group_by_columns}")
            self.logger.info(f"[STAGE2_DEBUG] compare_periods from Stage 2: {getattr(result, 'compare_periods', None)}")
            self.logger.info(f"[STAGE2_DEBUG] agg_functions from Stage 2: {getattr(result, 'agg_functions', None)}")
            
            # Validate and correct Stage 2 results
            result = self._validate_and_correct_stage2(result, query)
            
            # 🔥 ROBUST FIX: Fix-up temporal filter if LLM missed it (but keep LLM's agg_functions)
            if temporal_filter_expected:
                has_temporal_filters = hasattr(result, 'temporal_filters') and result.temporal_filters
                if not has_temporal_filters:
                    self.logger.info(f"[STAGE2_FIXUP] LLM missed temporal filter, adding: {temporal_filter_expected}")
                    
                    # Create temporal filter object
                    class TemporalFilterFixup:
                        def __init__(self, filter_type, month=None, year=None):
                            self.filter_type = filter_type
                            self.month = month
                            self.year = year
                            self.quarter = None
                            self.n_value = None
                            self.start_date = None
                            self.end_date = None
                    
                    temporal_filter = TemporalFilterFixup(
                        filter_type=temporal_filter_expected['filter_type'],
                        month=temporal_filter_expected['month'],
                        year=None  # Will default to current year in filter expression builder
                    )
                    result.temporal_filters = [temporal_filter]
                    self.logger.info(f"[STAGE2_FIXUP] ✅ Added temporal filter (kept LLM's agg_functions={getattr(result, 'agg_functions', None)})")
                else:
                    self.logger.info(f"[STAGE2] LLM correctly created temporal filters: {len(result.temporal_filters)} filter(s)")
            
            return result
            
        except Exception as e:
            self.logger.error(f"[STAGE2] Error: {e}")
            return self._fallback_stage2(stage1_grounded, query)
    
    def _validate_and_correct_stage2(self, stage2_result, query: str):
        """
        Validate and correct common Stage 2 LLM mistakes
        
        Detects ranking vs aggregation semantic mismatches using pattern analysis.
        This is a defense-in-depth layer that catches cases where the LLM prompt
        might not prevent the confusion between ranking and aggregation.
        
        Args:
            stage2_result: Stage 2 plan from LLM
            query: Original user query
            
        Returns:
            Corrected Stage 2 plan
        """
        query_lower = query.lower()
        
        # Pattern 1: Ranking queries (comparative/superlative with dimension)
        # Examples: "which X has", "X with most", "top N X", "bottom N X"
        ranking_patterns = [
            r'\b(which|what)\s+\w+\s+(has|have)\s+(most|least|highest|lowest|maximum|minimum)',
            r'\b\w+\s+with\s+(most|least|highest|lowest|top|bottom)',
            r'\b(top|bottom)\s+\d*\s*\w+',
        ]
        
        is_ranking_query = any(
            re.search(pattern, query_lower) 
            for pattern in ranking_patterns
        )
        
        # Check if agg_functions conflicts with ranking intent
        agg_funcs = getattr(stage2_result, 'agg_functions', None) or []
        group_by = getattr(stage2_result, 'group_by_columns', None)
        
        if is_ranking_query and group_by and ('min' in agg_funcs or 'max' in agg_funcs):
            self.logger.warning(
                f"[STAGE2_CORRECTION] Ranking query pattern detected but agg_functions={agg_funcs}. "
                f"Query: '{query}'. Correcting to ['count'] for proper ranking."
            )
            stage2_result.agg_functions = ['count']
            
        return stage2_result
    
    def _fallback_stage2(self, stage1_grounded, query: str = ""):
        """Fallback Stage 2 when LLM is not available or fails
        
        🔥 ROBUST FIX: This fallback now determines agg_functions based on:
        1. Query keywords (average, median, max, min, etc.)
        2. If metric_column exists → default to 'sum' (user wants metric value)
        3. If no metric_column → default to 'count' (user wants row count)
        """
        # Determine aggregation function from query and context
        agg_functions = self._determine_agg_function_from_query(query, stage1_grounded)
        
        class FallbackPlan:
            def __init__(self, agg_funcs):
                self.temporal_filters = []
                self.operations = ['grouped_aggregation']
                self.agg_functions = agg_funcs  # 🔥 No longer hardcoded!
                self.group_by_columns = []  # 🔥 COLLISION DETECTION FIX BY ANIKET - Added missing attribute
                self.confidence = 0.5
                self.reasoning = 'Fallback plan - basic aggregation'
                self.ambiguities = []
        
        plan = FallbackPlan(agg_functions)
        self.logger.info(f"[FALLBACK_STAGE2] 🔥 Using intelligent agg_functions={agg_functions} (not hardcoded 'count')")
        
        # 🔥 COLLISION DETECTION FIX BY ANIKET - START
        # Extract group_by_columns from Stage 1 to prevent NoneType errors
        if hasattr(stage1_grounded, 'group_by_columns') and stage1_grounded.group_by_columns:
            plan.group_by_columns = stage1_grounded.group_by_columns
            self.logger.info(f"[FALLBACK_STAGE2] 🔥 Extracted group_by_columns from Stage 1: {plan.group_by_columns} - Collision detection by Aniket")
        # 🔥 COLLISION DETECTION FIX BY ANIKET - END
        
        # Enhanced fallback: handle temporal intent from Stage 1 OR month filter values
        temporal_intent = None
        
        # Check temporal_intent first
        if hasattr(stage1_grounded, 'temporal_intent') and stage1_grounded.temporal_intent:
            temporal_intent = stage1_grounded.temporal_intent.lower()
        
        # If no temporal_intent, check if filter_value is a month name
        elif (hasattr(stage1_grounded, 'filter_value') and 
              isinstance(stage1_grounded.filter_value, str)):
            month_names = ['january', 'february', 'march', 'april', 'may', 'june',
                           'july', 'august', 'september', 'october', 'november', 'december']
            if stage1_grounded.filter_value.lower() in month_names:
                temporal_intent = stage1_grounded.filter_value.lower()
                self.logger.info(f"[FALLBACK_STAGE2] Using filter_value as temporal_intent: {temporal_intent}")
        
        # Create temporal filter if we have temporal intent
        if temporal_intent:
            month_mapping = {
                'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
                'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12
            }
            
            if temporal_intent in month_mapping:
                # Create a temporal filter object
                class TemporalFilterFallback:
                    def __init__(self, filter_type, month=None, year=None):
                        self.filter_type = filter_type
                        self.month = month
                        self.year = year
                        self.quarter = None
                        self.n_value = None
                        self.start_date = None
                        self.end_date = None
                
                # 🔥 FIX: Don't set year here - let _build_temporal_filter_expression add current year
                temporal_filter = TemporalFilterFallback(
                    filter_type='specific_month',
                    month=month_mapping[temporal_intent],
                    year=None  # Will default to current year in filter expression builder
                )
                
                plan.temporal_filters = [temporal_filter]
                plan.reasoning = f'Fallback plan - temporal aggregation for {temporal_intent}'
                
                self.logger.info(f"[FALLBACK_STAGE2] Created temporal filter: {temporal_intent} -> month={month_mapping[temporal_intent]}, year will default to current")
        
        return plan
    
    def _determine_agg_function_from_query(self, query: str, stage1_result) -> list:
        """
        🔥 ROBUST FIX: Determine aggregation function from query keywords and context.
        
        This method is used by _fallback_stage2 when Stage 2 LLM fails or is unavailable.
        Instead of hardcoding 'count', we intelligently determine the aggregation based on:
        1. Explicit keywords in the query (average, median, max, min, sum, total)
        2. If metric_column exists → default to 'sum' (user wants metric value)
        3. If no metric_column → default to 'count' (user wants row count)
        
        Args:
            query: The original user query
            stage1_result: Stage 1 result containing metric_column info
            
        Returns:
            List of aggregation functions (e.g., ['sum'], ['mean'], ['count'])
        """
        query_lower = query.lower()
        
        # Check for explicit aggregation keywords
        if any(kw in query_lower for kw in ['average', 'avg', 'mean']):
            self.logger.info("[AGG_DETECT] Detected 'average/avg/mean' keyword → agg_functions=['mean']")
            return ['mean']
        
        if 'median' in query_lower:
            self.logger.info("[AGG_DETECT] Detected 'median' keyword → agg_functions=['median']")
            return ['median']
        
        if any(kw in query_lower for kw in ['maximum', 'max ']):  # space after max to avoid matching 'may'
            self.logger.info("[AGG_DETECT] Detected 'max/maximum' keyword → agg_functions=['max']")
            return ['max']
        
        if any(kw in query_lower for kw in ['minimum', 'min ']):  # space after min to avoid false positives
            self.logger.info("[AGG_DETECT] Detected 'min/minimum' keyword → agg_functions=['min']")
            return ['min']
        
        if any(kw in query_lower for kw in ['total', 'sum ']):  # explicit sum
            self.logger.info("[AGG_DETECT] Detected 'total/sum' keyword → agg_functions=['sum']")
            return ['sum']
        
        # Check for count keywords (only if no metric column - otherwise user wants metric value)
        has_metric = hasattr(stage1_result, 'metric_column') and stage1_result.metric_column
        if any(kw in query_lower for kw in ['count', 'number of', 'how many']) and not has_metric:
            self.logger.info("[AGG_DETECT] Detected count keyword without metric → agg_functions=['count']")
            return ['count']
        
        # Default based on metric_column presence
        if has_metric:
            self.logger.info(f"[AGG_DETECT] No explicit keyword, but metric_column='{stage1_result.metric_column}' exists → agg_functions=['sum']")
            return ['sum']
        else:
            self.logger.info("[AGG_DETECT] No explicit keyword, no metric_column → agg_functions=['count']")
            return ['count']
    
    def _stage3_code_generation(self, stage1_grounded, stage2_result, query: str, df_sample: pl.DataFrame) -> str:
        """Stage 3: Code Generation using the new modular code generators"""
        try:
            # Get the primary operation
            operation_type = stage2_result.operations[0] if stage2_result.operations else 'grouped_aggregation'
            
            # 🆕 NEW: Handle ratio operation (from calculated field)
            if operation_type == 'ratio':
                self.logger.info("[STAGE3] Generating ratio aggregation code (from calculated field)")
                return self._generate_ratio_code(stage1_grounded, stage2_result, df_sample)
            
            # Get the appropriate code generator
            code_generator_class = CODE_GENERATORS.get(operation_type)
            if not code_generator_class:
                self.logger.warning(f"[STAGE3] No code generator for {operation_type}, using grouped_aggregation")
                code_generator_class = CODE_GENERATORS['grouped_aggregation']
            
            # Build operation parameters (🆕 pass df_sample)
            operation_params = self._build_operation_params(stage1_grounded, stage2_result, query, df_sample)
            
            # 🆕 ADD DATA CLEANING STEP (returns tuple now)
            cleaning_code, actual_column_used = self._generate_data_cleaning_code(stage1_grounded, stage2_result, df_sample)
            
            # Log what column we're actually using
            if actual_column_used == '_count_helper':
                self.logger.info(f"[STAGE3] Using helper column '_count_helper' instead of '{stage1_grounded.metric_column}'")
            
            # Generate main operation code
            code_generator = code_generator_class()
            operation_code = code_generator.generate(operation_params)
            
            # Combine cleaning + operation code
            if cleaning_code and operation_code:
                generated_code = cleaning_code + "\n" + operation_code
                self.logger.info(f"[STAGE3] ✅ Generated code with data cleaning + {operation_type} generator")
            elif operation_code:
                generated_code = operation_code
                self.logger.info(f"[STAGE3] ✅ Generated code using {operation_type} generator")
            else:
                raise Exception(f"Code generator {operation_type} returned empty code")
            
            return generated_code
                    
        except Exception as e:
            self.logger.error(f"[STAGE3] Error: {e}")
            # Fallback to basic aggregation
            return self._generate_fallback_code(stage1_grounded)
    
    def _generate_ratio_code(self, stage1_grounded, stage2_result, df_sample: pl.DataFrame) -> str:
        """
        Generate polars code for ratio metrics (from calculated fields)
        
        Args:
            stage1_grounded: Stage 1 results
            stage2_result: Stage 2 results with ratio formula
            df_sample: Sample dataframe
            
        Returns:
            Generated polars code for ratio computation
        """
        self.logger.info("[RATIO_CODE] Generating ratio aggregation code")
        
        # Extract formula from Stage 2
        numerator = getattr(stage2_result, 'numerator_column', None)
        denominator = getattr(stage2_result, 'column', None)  # Stored in 'column' field
        
        if not numerator or not denominator:
            self.logger.error(f"[RATIO_CODE] Missing formula: numerator={numerator}, denominator={denominator}")
            return self._generate_fallback_code(stage1_grounded)
        
        self.logger.info(f"[RATIO_CODE] Formula: {numerator} / {denominator}")
        
        # Build filter expression from temporal filters
        filter_expr = "True"  # Default: no filter
        
        if stage2_result.temporal_filters:
            # Get date column
            date_column = stage1_grounded.date_column
            if not date_column:
                # Try to find date column
                date_column = self._discover_and_validate_date_column(stage1_grounded, stage2_result, df_sample)
            
            if date_column and stage2_result.temporal_filters:
                temporal_filter = stage2_result.temporal_filters[0]
                if temporal_filter.filter_type == 'specific_month' and temporal_filter.month:
                    month = temporal_filter.month
                    # Default to current year if not specified
                    year = temporal_filter.year if hasattr(temporal_filter, 'year') and temporal_filter.year else self.CURRENT_YEAR
                    filter_expr = f"((df['{date_column}'].dt.month() == {month}) & (df['{date_column}'].dt.year() == {year}))"
                    self.logger.info(f"[RATIO_CODE] Added temporal filter: month={month}, year={year}")
        
        # Get group by columns
        group_by_columns = stage2_result.group_by_columns or []
        
        # Generate data cleaning code for numerator and denominator
        cleaning_code = f"""# Clean and convert date column to datetime
if '{stage1_grounded.date_column}' in df.columns:
    try:
        df = df.with_columns(
            pl.col('{stage1_grounded.date_column}').str.to_datetime(strict=False).alias('{stage1_grounded.date_column}')
        )
    except:
        pass

# Clean and convert numeric columns
for col in ['{numerator}', '{denominator}']:
    if col in df.columns:
        df = df.with_columns(
            pl.col(col).cast(pl.Float64, strict=False).alias(col)
        )
"""
        
        # Generate ratio aggregation code
        if group_by_columns and len(group_by_columns) > 0:
            # Grouped ratio aggregation
            group_cols_str = ', '.join([f'"{col}"' for col in group_by_columns])
            aggregation_code = f"""
# Apply filters
df_filtered = df.filter({filter_expr})

# Grouped ratio aggregation: {numerator} / {denominator}
result = df_filtered.group_by([{group_cols_str}]).agg([
    (pl.sum("{numerator}") / pl.sum("{denominator}")).alias("result")
])
"""
        else:
            # Simple ratio aggregation (no grouping)
            aggregation_code = f"""
# Apply filters
df_filtered = df.filter({filter_expr})

# Simple ratio aggregation: {numerator} / {denominator}
result = df_filtered.select([
    (pl.sum("{numerator}") / pl.sum("{denominator}")).alias("result")
])
"""
        
        final_code = cleaning_code + aggregation_code
        self.logger.info(f"[RATIO_CODE] ✅ Generated ratio code: {numerator}/{denominator}")
        
        return final_code
    
    def _discover_and_validate_date_column(self, stage1_result, stage2_result, df_sample: pl.DataFrame) -> Optional[str]:
        """
        🆕 NEW METHOD: Discover date column when Stage 1 misses it
        
        This method:
        1. Searches all available columns for date-related columns
        2. Validates the column contains parseable dates
        3. Returns the best date column for temporal operations
        
        Args:
            stage1_result: Stage 1 results
            stage2_result: Stage 2 results (with temporal_filters)
            df_sample: Sample dataframe
            
        Returns:
            Name of the best date column, or None if no suitable column found
        """
        # Check if we already have a date column from Stage 1
        existing_date_column = stage1_result.date_column
        if existing_date_column:
            self.logger.info(f"[DATE_DISCOVERY] Using existing date_column from Stage 1: {existing_date_column}")
            return existing_date_column
        
        # Check if temporal filters exist (if not, no need to discover date column)
        if not hasattr(stage2_result, 'temporal_filters') or not stage2_result.temporal_filters:
            self.logger.info("[DATE_DISCOVERY] No temporal filters, skipping date column discovery")
            return None
        
        self.logger.info("[DATE_DISCOVERY] No date_column from Stage 1, searching all columns...")
        
        # Date-related keywords to search for
        date_keywords = ['date', 'time', 'timestamp', 'created', 'updated', 'modified', 
                         'opened', 'closed', 'resolved', 'month', 'year', 'day']
        
        # Find all columns that might contain dates
        candidate_columns = []
        for col in df_sample.columns:
            col_lower = col.lower()
            if any(keyword in col_lower for keyword in date_keywords):
                candidate_columns.append(col)
        
        if not candidate_columns:
            self.logger.warning("[DATE_DISCOVERY] No date-related columns found in dataframe")
            return None
        
        self.logger.info(f"[DATE_DISCOVERY] Found {len(candidate_columns)} candidate columns: {candidate_columns}")
        
        # Validate each candidate by trying to parse dates
        valid_candidates = []
        for col in candidate_columns:
            sample_values = df_sample[col].drop_nulls().head(50)
            if len(sample_values) == 0:
                continue
            
            # Try to parse as datetime using pure Polars
            success_rate = 0.0
            sample_size = len(sample_values)
            
            # Try multiple common date formats (with and without time components)
            date_formats = [
                None,  # Auto-detect
                # ISO formats with time
                "%Y-%m-%d %H:%M:%S.%3f",
                "%Y-%m-%d %H:%M:%S",
                # Common formats with time
                "%d/%m/%Y %H:%M:%S",
                "%m/%d/%Y %H:%M:%S",
                "%Y/%m/%d %H:%M:%S",
                "%d-%m-%Y %H:%M:%S",
                "%m-%d-%Y %H:%M:%S",
                # Date only formats
                "%Y-%m-%d",
                "%d/%m/%Y",
                "%m/%d/%Y",
                "%Y/%m/%d",
                "%d-%m-%Y",
                "%m-%d-%Y",
            ]
            
            for fmt in date_formats:
                try:
                    if fmt is None:
                        parsed = sample_values.str.to_datetime()
                    else:
                        parsed = sample_values.str.to_datetime(format=fmt, strict=False)
                    
                    null_count = parsed.null_count()
                    success_rate = (sample_size - null_count) / sample_size
                    
                    if success_rate > 0.5:  # If >50% parse successfully, consider it valid
                        valid_candidates.append({
                            'column': col,
                            'success_rate': success_rate,
                            'sample_value': str(sample_values[0]),
                            'dtype': str(df_sample[col].dtype)
                        })
                        self.logger.info(f"[DATE_DISCOVERY] ✅ {col} is parseable (success: {success_rate:.1%}, format: {fmt}, sample: {sample_values[0]})")
                        break
                except Exception as e:
                    # Log the error but continue trying other formats
                    self.logger.debug(f"[DATE_DISCOVERY] Format '{fmt}' failed for {col}: {str(e)}")
                    continue
        
        if not valid_candidates:
            self.logger.error(f"[DATE_DISCOVERY] None of the candidate columns contain valid dates: {candidate_columns}")
            return None
        
        # Sort by success rate (prefer columns with highest parse success)
        valid_candidates.sort(key=lambda x: x['success_rate'], reverse=True)
        
        best_column = valid_candidates[0]['column']
        self.logger.info(f"[DATE_DISCOVERY] 🎯 Selected best date column: {best_column} (success: {valid_candidates[0]['success_rate']:.1%})")
        
        return best_column

    def _detect_date_column_format(self, df_sample: pl.DataFrame, date_column: str) -> Dict[str, Any]:
        """
        🆕 NEW METHOD: Detect the format and type of a date column
        
        Args:
            df_sample: Sample dataframe
            date_column: Name of the date column
            
        Returns:
            Dict with format information:
            {
                'is_datetime': bool,
                'is_string': bool,
                'is_numeric': bool (unix timestamp),
                'detected_format': str (e.g., '%Y-%m-%d', '%d/%m/%Y'),
                'needs_conversion': bool
            }
        """
        if date_column not in df_sample.columns:
            return {'error': 'Column not found'}
        
        sample_values = df_sample[date_column].drop_nulls().head(50)
        if len(sample_values) == 0:
            return {'error': 'No non-null values'}
        
        dtype = df_sample[date_column].dtype
        
        # Check if already datetime
        if dtype in [pl.Date, pl.Datetime]:
            self.logger.info(f"[DATE_FORMAT] Column '{date_column}' is already datetime64")
            return {
                'is_datetime': True,
                'is_string': False,
                'is_numeric': False,
                'detected_format': 'datetime64',
                'needs_conversion': False
            }
        
        # Check if numeric (unix timestamp)
        if dtype in [pl.Int32, pl.Int64, pl.Float32, pl.Float64]:
            # Try to convert as unix timestamp
            try:
                parsed = pd.to_datetime(sample_values, unit='s', errors='coerce')
                success_rate = parsed.notna().sum() / len(sample_values)
                if success_rate > 0.5:
                    self.logger.info(f"[DATE_FORMAT] Column '{date_column}' is unix timestamp")
                    return {
                        'is_datetime': False,
                        'is_string': False,
                        'is_numeric': True,
                        'detected_format': 'unix_timestamp',
                        'needs_conversion': True
                    }
            except:
                pass
        
        # Must be string-based dates
        # Try to detect the format
        sample_str = str(sample_values[0])
        detected_format = None
        
        common_formats = [
            '%Y-%m-%d',         # 2024-03-15
            '%d-%m-%Y',         # 15-03-2024
            '%m-%d-%Y',         # 03-15-2024
            '%Y/%m/%d',         # 2024/03/15
            '%d/%m/%Y',         # 15/03/2024
            '%m/%d/%Y',         # 03/15/2024
            '%Y-%m-%d %H:%M:%S',  # 2024-03-15 14:30:00
            '%d-%m-%Y %H:%M:%S',  # 15-03-2024 14:30:00
            '%Y-%m-%dT%H:%M:%S',  # 2024-03-15T14:30:00 (ISO format)
            '%d.%m.%Y',         # 15.03.2024
            '%Y%m%d',           # 20240315
        ]
        
        for fmt in common_formats:
            try:
                parsed = pd.to_datetime(sample_values, format=fmt, errors='coerce')
                success_rate = parsed.notna().sum() / len(sample_values)
                if success_rate > 0.8:  # 80% success rate
                    detected_format = fmt
                    self.logger.info(f"[DATE_FORMAT] Detected format '{fmt}' for column '{date_column}' (success: {success_rate:.1%})")
                    break
            except:
                continue
        
        if not detected_format:
            # Fallback to infer_datetime_format
            detected_format = 'infer'
            self.logger.info(f"[DATE_FORMAT] Could not detect specific format, will use infer_datetime_format")
        
        return {
            'is_datetime': False,
            'is_string': True,
            'is_numeric': False,
            'detected_format': detected_format,
            'needs_conversion': True,
            'sample_value': sample_str
        }

    def _build_temporal_filter_expression(self, date_column: str, temp_filter, df_sample: pl.DataFrame = None) -> str:
        """
        🆕 ENHANCED: Build temporal filter expression with format-aware conversion
        
        Args:
            date_column: Name of the date column
            temp_filter: TemporalFilter object from Stage 2
            df_sample: Sample dataframe for format detection
            
        Returns:
            Complete filter expression as a string
        """
        if not date_column:
            return ""
        
        # 🔧 FIX: Since _generate_data_cleaning_code() already converts the date column,
        # we should NOT re-convert it in the filter expression
        # Just use df['{date_column}'].dt.xxx directly
        
        datetime_expr = f"df['{date_column}']"  # 🆕 Already converted by cleaning code
        
        # Build filter based on type
        filter_type = temp_filter.filter_type
        
        if filter_type == 'specific_month':
            month = temp_filter.month
            year = temp_filter.year
            if month:
                if year:
                    # Year is explicitly provided
                    return f"(({datetime_expr}.dt.month() == {month}) & ({datetime_expr}.dt.year() == {year}))"
                else:
                    # 🔥 FIX: No year provided, default to current year
                    return f"(({datetime_expr}.dt.month() == {month}) & ({datetime_expr}.dt.year() == datetime.datetime.now().year))"
        
        elif filter_type == 'specific_year':
            year = temp_filter.year
            if year:
                return f"({datetime_expr}.dt.year() == {year})"
        
        elif filter_type == 'specific_quarter':
            quarter = temp_filter.quarter
            year = temp_filter.year
            if quarter and year:
                return f"(({datetime_expr}.dt.quarter() == {quarter}) & ({datetime_expr}.dt.year() == {year}))"
            elif quarter:
                # 🔥 FIX: No year provided, default to current year
                return f"(({datetime_expr}.dt.quarter() == {quarter}) & ({datetime_expr}.dt.year() == datetime.datetime.now().year))"
        
        elif filter_type == 'date_range':
            start = temp_filter.start_date
            end = temp_filter.end_date
            if start and end:
                return f"(({datetime_expr} >= '{start}') & ({datetime_expr} <= '{end}'))"
            elif start:
                return f"({datetime_expr} >= '{start}')"
            elif end:
                return f"({datetime_expr} <= '{end}')"
        
        elif filter_type == 'last_n_days':
            n = temp_filter.n_value
            if n:
                return f"({datetime_expr} >= (datetime.datetime.now() - datetime.timedelta(days={n})))"
        
        elif filter_type == 'last_n_months':
            n = temp_filter.n_value
            if n:
                # Approximate months using 30 days per month
                return f"({datetime_expr} >= (datetime.datetime.now() - datetime.timedelta(days={n}*30)))"
        
        elif filter_type == 'ytd':
            return f"(({datetime_expr} >= datetime.datetime(datetime.datetime.now().year, 1, 1)) & ({datetime_expr} <= datetime.datetime.now()))"
        
        elif filter_type == 'mtd':
            return f"(({datetime_expr} >= datetime.datetime(datetime.datetime.now().year, datetime.datetime.now().month, 1)) & ({datetime_expr} <= datetime.datetime.now()))"
        
        elif filter_type == 'qtd':
            return f"""(({datetime_expr} >= datetime.datetime(datetime.datetime.now().year, ((datetime.datetime.now().month - 1) // 3 * 3) + 1, 1)) & ({datetime_expr} <= datetime.datetime.now()))"""
        
        elif filter_type in ['last_quarter', 'this_quarter', 'next_quarter']:
            # More complex quarter logic (quarter calculation: (month - 1) // 3 + 1)
            if filter_type == 'last_quarter':
                return f"""((({datetime_expr}.dt.quarter() == ((datetime.datetime.now().month - 1) // 3 + 1 - 1 if (datetime.datetime.now().month - 1) // 3 + 1 > 1 else 4)) & ({datetime_expr}.dt.year() == (datetime.datetime.now().year if (datetime.datetime.now().month - 1) // 3 + 1 > 1 else datetime.datetime.now().year - 1))))"""
            elif filter_type == 'this_quarter':
                return f"(({datetime_expr}.dt.quarter() == (datetime.datetime.now().month - 1) // 3 + 1) & ({datetime_expr}.dt.year() == datetime.datetime.now().year))"
            else:  # next_quarter
                return f"""((({datetime_expr}.dt.quarter() == ((datetime.datetime.now().month - 1) // 3 + 1 + 1 if (datetime.datetime.now().month - 1) // 3 + 1 < 4 else 1)) & ({datetime_expr}.dt.year() == (datetime.datetime.now().year if (datetime.datetime.now().month - 1) // 3 + 1 < 4 else datetime.datetime.now().year + 1))))"""
        
        # Fallback - return empty string if unknown filter type
        self.logger.warning(f"[TEMPORAL_FILTER] Unknown filter type: {filter_type}")
        return ""
    
    def _build_operation_params(self, stage1_result, stage2_result, query: str, df_sample: pl.DataFrame) -> Dict[str, Any]:
        """Build operation parameters from stage results"""
        
        # Build filters from Stage 1 results
        filters = []
        if hasattr(stage1_result, 'filter_column') and stage1_result.filter_column and hasattr(stage1_result, 'filter_value') and stage1_result.filter_value is not None:
            # Check if this filter should be handled as temporal instead of regular filter
            filter_value = stage1_result.filter_value
            filter_column = stage1_result.filter_column
            is_temporal_filter = False
            
            # 🆕 ENHANCED CHECK: Skip filters on date columns when temporal filters exist
            if hasattr(stage2_result, 'temporal_filters') and stage2_result.temporal_filters:
                # Check 1: Is the filter value a month name?
                month_names = ['january', 'february', 'march', 'april', 'may', 'june',
                               'july', 'august', 'september', 'october', 'november', 'december']
                if isinstance(filter_value, str) and filter_value.lower() in month_names:
                    is_temporal_filter = True
                    self.logger.info(f"[PARAM_BUILD] Skipping regular filter for month name '{filter_value}' - handled by temporal filter")
                
                # Check 2: Is the filter column a date-related column?
                date_keywords = ['date', 'time', 'timestamp', 'created', 'updated', 'modified', 
                                 'opened', 'closed', 'resolved', 'month', 'year', 'day']
                filter_col_lower = filter_column.lower()
                if any(keyword in filter_col_lower for keyword in date_keywords):
                    is_temporal_filter = True
                    self.logger.info(f"[PARAM_BUILD] Skipping filter on date column '{filter_column}' - handled by temporal filter")
            
            # Only add regular filter if it's not being handled temporally
            if not is_temporal_filter:
                filters.append({
                    'column': stage1_result.filter_column,
                    'operator': getattr(stage1_result, 'filter_operator', None) or ('in' if isinstance(stage1_result.filter_value, list) else '=='),
                    'value': stage1_result.filter_value
                })
        
        # 🆕 CRITICAL FIX: Add additional filters with the same temporal filter check
        if hasattr(stage1_result, 'additional_filters') and stage1_result.additional_filters:
            for additional_filter in stage1_result.additional_filters:
                filter_column = additional_filter.column
                filter_value = additional_filter.value
                is_temporal_filter = False
                
                # Same check as above: skip temporal filters
                if hasattr(stage2_result, 'temporal_filters') and stage2_result.temporal_filters:
                    # Check 1: Is the filter value a month name?
                    month_names = ['january', 'february', 'march', 'april', 'may', 'june',
                                     'july', 'august', 'september', 'october', 'november', 'december']
                    if isinstance(filter_value, str) and filter_value.lower() in month_names:
                        is_temporal_filter = True
                        self.logger.info(f"[PARAM_BUILD] Skipping additional filter for month name '{filter_value}' - handled by temporal filter")
                    
                    # Check 2: Is the filter column a date-related column?
                    date_keywords = ['date', 'time', 'timestamp', 'created', 'updated', 'modified', 
                                     'opened', 'closed', 'resolved', 'month', 'year', 'day']
                    filter_col_lower = filter_column.lower()
                    if any(keyword in filter_col_lower for keyword in date_keywords):
                        is_temporal_filter = True
                        self.logger.info(f"[PARAM_BUILD] Skipping additional filter on date column '{filter_column}' - handled by temporal filter")
                
                # Only add if not temporal
                if not is_temporal_filter:
                    filters.append({
                        'column': additional_filter.column,
                        'operator': additional_filter.operator,
                        'value': additional_filter.value
                    })
        
        # 🆕 DISCOVER DATE COLUMN if missing and temporal filters exist
        date_column = self._discover_and_validate_date_column(stage1_result, stage2_result, df_sample)
        
        # Build temporal filters from Stage 2 results
        temporal_filters = []
        if hasattr(stage2_result, 'temporal_filters') and stage2_result.temporal_filters:
            for temp_filter in stage2_result.temporal_filters:
                
                if not date_column:
                    self.logger.error("[PARAM_BUILD] ❌ Temporal filter exists but no valid date column found!")
                    self.logger.error(f"[PARAM_BUILD] Available columns: {list(df_sample.columns)}")
                    self.logger.error(f"[PARAM_BUILD] Temporal filter: {temp_filter}")
                    # Skip this temporal filter
                    continue
                
                # 🔥 CRITICAL FIX: Check if year was explicitly mentioned in query
                # If not, override with None so current year is used
                query_lower = query.lower()
                year_mentioned_in_query = self._is_year_explicit_in_query(query_lower)
                
                # Override year if it wasn't explicitly mentioned
                if not year_mentioned_in_query and temp_filter.year:
                    self.logger.info(f"[PARAM_BUILD] 🔧 Year {temp_filter.year} not explicit in query, will use current year")
                    temp_filter.year = None  # This will trigger current year default in _build_temporal_filter_expression
                
                # 🆕 CRITICAL FIX: Pre-build filter expression with format-aware conversion
                filter_expression = self._build_temporal_filter_expression(date_column, temp_filter, df_sample)
                
                if not filter_expression:
                    self.logger.warning(f"[PARAM_BUILD] Could not build filter expression for {temp_filter.filter_type}")
                    continue
                
                # Map to format expected by code generators
                tf_dict = {
                    'column': date_column,
                    'type': temp_filter.filter_type,  # Code generators expect 'type' not 'filter_type'
                    'year': temp_filter.year,
                    'quarter': temp_filter.quarter,
                    'month': temp_filter.month,
                    'n_value': temp_filter.n_value,
                    'start_date': temp_filter.start_date,
                    'end_date': temp_filter.end_date,
                    'filter_expression': filter_expression  # 🆕 Pre-built expression
                }
                
                # For specific_month, set 'value' to the month number (code generators expect this)
                if temp_filter.filter_type == 'specific_month' and temp_filter.month:
                    tf_dict['value'] = temp_filter.month
                
                temporal_filters.append(tf_dict)
                self.logger.info(f"[PARAM_BUILD] Added temporal filter with pre-built expression: {filter_expression[:100]}...")
        
        # Determine the 'column' parameter based on operation type
        operations = getattr(stage2_result, 'operations', ['grouped_aggregation'])
        primary_operation = operations[0] if operations else 'grouped_aggregation'
        
        # Column descriptions now use direct handler - shouldn't reach here
        if primary_operation == 'column_description':
            self.logger.error("[PARAM_BUILD] Column description should use direct handler, not reach Stage 3!")
            raise Exception("Column description operations should be handled directly, not through Stage 3")
        
        # For breakdown operations, 'column' should be the first group_by column
        # 🔥 CRITICAL FIX: Check for None explicitly, not using 'or' (empty list [] is falsy!)
        stage2_group_by = getattr(stage2_result, 'group_by_columns', None)
        if stage2_group_by is not None:
            group_by_columns = stage2_group_by  # Use Stage 2 result even if it's []
        else:
            group_by_columns = stage1_result.group_by_columns or []  # Fallback to Stage 1
        
        self.logger.info(f"[PARAM_BUILD_DEBUG] Stage 1 group_by: {stage1_result.group_by_columns}")
        self.logger.info(f"[PARAM_BUILD_DEBUG] Stage 2 group_by: {stage2_group_by}")
        self.logger.info(f"[PARAM_BUILD_DEBUG] Final group_by_columns (before cleanup): {group_by_columns}")
        self.logger.info(f"[PARAM_BUILD_FIX] Used Stage 2: {stage2_group_by is not None}, Stage 2 was empty: {stage2_group_by == []}")
        
        # 🔥 SAFETY: Ensure it's always a list, never None
        if group_by_columns is None:
            group_by_columns = []
        elif not isinstance(group_by_columns, list):
            group_by_columns = [group_by_columns]
        
        # 🔥 NEW: Remove date_column from group_by_columns ONLY for operations that concatenate them
        # Operations like TimeSeriesCodeGen and PeriodComparisonCodeGen do [date_column] + group_by
        # which causes duplicate column errors when date_column is also in group_by
        operations_that_concatenate_date = ['time_series', 'period_comparison']
        
        self.logger.info(f"[PARAM_BUILD_DEBUG] Checking date column removal:")
        self.logger.info(f"[PARAM_BUILD_DEBUG]   primary_operation={primary_operation}, in list: {primary_operation in operations_that_concatenate_date}")
        self.logger.info(f"[PARAM_BUILD_DEBUG]   date_column={date_column}")
        self.logger.info(f"[PARAM_BUILD_DEBUG]   group_by_columns={group_by_columns}")
        self.logger.info(f"[PARAM_BUILD_DEBUG]   date in group_by: {date_column in group_by_columns if group_by_columns else False}")
        
        if primary_operation in operations_that_concatenate_date and date_column and group_by_columns and date_column in group_by_columns:
            group_by_columns = [col for col in group_by_columns if col != date_column]
            self.logger.info(f"[PARAM_BUILD] ✅ Removed date_column '{date_column}' from group_by_columns to avoid duplicate grouping in {primary_operation}")
        else:
            self.logger.info(f"[PARAM_BUILD] ⚠️ Did NOT remove date_column - condition not met")
        
        if primary_operation == 'breakdown' and group_by_columns:
            column_param = group_by_columns[0]  # Use first group_by column for breakdown
        else:
            column_param = getattr(stage2_result, 'column', None) or stage1_result.metric_column
        
        # metric_column - no fallback to hardcoded column names
        metric_column = stage1_result.metric_column
        if metric_column is None:
            self.logger.warning(f"[PARAM_FIX] metric_column is None for {primary_operation} - no fallback applied")
        
        # Also fix column_param if it's None
        if column_param is None:
            column_param = metric_column
            self.logger.info(f"[PARAM_FIX] column_param was None, using metric_column: {column_param}")
        
        # 🔥 NEW: Get actual column to use (might be '_count_helper')
        # We need to call _generate_data_cleaning_code to determine this
        # But we don't want to generate the code twice, so we'll do it here
        _, actual_metric_column = self._generate_data_cleaning_code(stage1_result, stage2_result, df_sample)
        
        # 🔥 CRITICAL FIX: Only use helper column for operations that support it
        operations_supporting_helper = ['period_comparison', 'window_function', 'grouped_aggregation', 'time_series']
        use_helper_column = primary_operation in operations_supporting_helper and actual_metric_column == '_count_helper'
        
        # 🔥 SAFETY: Ensure all list parameters are actually lists, never None
        if filters is None:
            filters = []
        if temporal_filters is None:
            temporal_filters = []
        if group_by_columns is None:
            group_by_columns = []
        
        # 🆕 SAFETY: Auto-set date_column for time_series with temporal grouping
        if primary_operation == 'time_series' and group_by_columns and not date_column:
            # Check if any group_by column is temporal (generic keywords)
            temporal_keywords = ['day', 'week', 'month', 'quarter', 'year', 'date', 'time', 'timestamp']
            for col in group_by_columns:
                col_lower = col.lower()
                if any(keyword in col_lower for keyword in temporal_keywords):
                    date_column = col
                    self.logger.info(f"[SAFETY] Auto-set date_column={col} for time_series with temporal grouping")
                    break
        
        # Build operation parameters dict
        operation_params = {
            'numerator_column': actual_metric_column if use_helper_column else (getattr(stage2_result, 'numerator_column', None) or metric_column),
            'group_by_columns': group_by_columns,
            'agg_column': actual_metric_column if use_helper_column else (getattr(stage2_result, 'agg_column', None) or metric_column),
            'agg_functions': getattr(stage2_result, 'agg_functions', None) or ['count'],
            'date_column': date_column,  # Use discovered/validated date_column
            'metric_column': actual_metric_column if use_helper_column else metric_column,
            'original_metric_column': metric_column,  # Keep original for reference
            'has_helper_column': actual_metric_column == '_count_helper',  # 🔥 NEW FLAG
            'column': column_param,  # Use fixed column_param
            'query': query,
            'filters': filters,
            'temporal_filters': temporal_filters,
            
            # 🆕 PIVOT OPERATION PARAMETERS
            'index': getattr(stage2_result, 'index', None) or group_by_columns or [],
            'columns': getattr(stage2_result, 'columns', None),
            'values': actual_metric_column if use_helper_column else (getattr(stage2_result, 'values', None) or metric_column),
            'aggfunc': getattr(stage2_result, 'aggfunc', None) or 'count',
            
            # 🆕 PERIOD COMPARISON PARAMETERS
            'comparison_type': getattr(stage2_result, 'comparison_type', None),
            'period_granularity': getattr(stage2_result, 'period_granularity', None),
            'shift_periods': getattr(stage2_result, 'shift_periods', None),
            'compare_periods': getattr(stage2_result, 'compare_periods', None),
            'output_format': getattr(stage2_result, 'output_format', None),
            
            # 🆕 TIME SERIES PARAMETERS
            'time_series_granularity': getattr(stage2_result, 'time_series_granularity', None),
            'time_series_include_comparison': getattr(stage2_result, 'time_series_include_comparison', None),
            'time_series_comparison_type': getattr(stage2_result, 'time_series_comparison_type', None),
            
            # 🆕 WINDOW FUNCTION PARAMETERS
            'window_type': getattr(stage2_result, 'window_type', None),
            'window_size': getattr(stage2_result, 'window_size', None),
            'window_unit': getattr(stage2_result, 'window_unit', None),
            'operation': getattr(stage2_result, 'window_operation', None),  # Maps window_operation to operation
            'window_is_time_based': getattr(stage2_result, 'window_is_time_based', None),
            'column2': getattr(stage2_result, 'window_column2', None),
            
            # 🆕 COMPOSITION PERCENTAGE PARAMETERS
            'rate_name': getattr(stage2_result, 'rate_name', None) or 'rate',
            'as_percentage': getattr(stage2_result, 'as_percentage', True),
            
            # 🆕 TRANSFORMATION PARAMETERS (with fallback defaults)
            'needs_transformation': getattr(stage2_result, 'needs_transformation', False),
            'grouping_period': getattr(stage2_result, 'grouping_period', 'day'),
            'column2_needs_transformation': getattr(stage2_result, 'column2_needs_transformation', False),
            'column2_operation': getattr(stage2_result, 'column2_operation', 'sum')
        }
        
        # 🆕 PERCENTILE EXTRACTION (CRITICAL FIX - was missing!)
        # Extract percentile values if this is a percentile operation
        operations = getattr(stage2_result, 'operations', [])
        if 'percentile' in operations:
            extracted_percentiles = self._extract_percentile_values(query)
            operation_params['percentiles'] = extracted_percentiles
            self.logger.info(f"[PERCENTILE_PARAMS] Added percentiles to operation_params: {extracted_percentiles}")
        
        # 🆕 PERIOD COMPARISON EXTRACTION (CRITICAL FIX - was missing!)
        # Extract compare_periods from query if missing for period_comparison operation
        if 'period_comparison' in operations:
            compare_periods = getattr(stage2_result, 'compare_periods', None)
            if not compare_periods:
                # Try to extract periods from query
                extracted_periods = self._extract_compare_periods_from_query(query)
                if extracted_periods:
                    operation_params['compare_periods'] = extracted_periods
                    self.logger.info(f"[PERIOD_COMPARISON_PARAMS] Inferred compare_periods from query: {extracted_periods}")
        
        # 🆕 RANKING PARAMETERS EXTRACTION (CRITICAL FIX - was missing!)
        # 🔥 FIX BY JITENDRA: Extract ranking params for ALL operations (not just 'ranking') to support multi-operation queries like ['grouped_aggregation', 'ranking']
        # Reason: These are optional parameters that any operation can use if present
        # Import here to avoid circular imports
        from services.nlp_to_python.nl_to_python_codegen import RankingCodeGen
        
        # Extract limit for "top N" or "bottom N" queries (optional parameter)
        # 🔥 FIX BY JITENDRA: Extract limit directly from query text, ignore Stage 2 limit (returns wrong default of 10)
        limit_results = self._extract_top_n_limit_simple(query)
        if limit_results:
            operation_params['limit_results'] = limit_results
            self.logger.info(f"[RANKING_PARAMS] Added limit_results: {limit_results}")
        
        # Detect top/bottom query types (optional parameters)
        is_top_query = RankingCodeGen._detect_top_query(query)
        is_bottom_query = RankingCodeGen._detect_bottom_query(query)
        
        operation_params['is_top_query'] = is_top_query
        operation_params['is_bottom_query'] = is_bottom_query
        
        self.logger.info(f"[RANKING_PARAMS] Query type detection - is_top_query: {is_top_query}, is_bottom_query: {is_bottom_query}")
        
        return operation_params
    
    def _generate_fallback_code(self, stage1_result) -> str:
        """Generate fallback polars code for basic aggregation"""
        group_by = stage1_result.group_by_columns or []
        metric = stage1_result.metric_column
        
        if group_by:
            # 🔥 FIX: Use polars syntax instead of pandas
            return f"""
# Fallback: Basic grouped aggregation (polars)
result = df.group_by({group_by}).agg(pl.len().alias('count'))
result = result.sort('count', descending=True)
"""
        else:
            return f"""
# Fallback: Basic count
result = pl.DataFrame({{'count': [len(df)]}})
"""
    
    def _generate_data_cleaning_code(self, stage1_grounded, stage2_result, df_sample: pl.DataFrame = None):
        """
        Generate data cleaning code for both numeric AND date columns
        
        🆕 ENHANCED: Now includes centralized date column cleaning with format detection
        🔥 NEW: Returns tuple (cleaning_code, actual_column_to_use) for helper column support
        
        Returns:
            tuple: (cleaning_code: str, actual_column_to_use: str)
        """
        cleaning_code = ""
        
        # Get columns to clean
        metric_column = stage1_grounded.metric_column
        group_by_columns = getattr(stage2_result, 'group_by_columns', None) or stage1_grounded.group_by_columns or []
        
        # 🆕 DISCOVER DATE COLUMN if needed
        date_column = self._discover_and_validate_date_column(stage1_grounded, stage2_result, df_sample) if df_sample is not None else stage1_grounded.date_column
        
        # 🆕 PART 1: Clean date columns FIRST (most critical for temporal operations)
        if date_column and df_sample is not None:
            # Get format info
            format_info = self._detect_date_column_format(df_sample, date_column)
            
            if format_info.get('needs_conversion'):
                detected_format = format_info.get('detected_format')
                
                if format_info.get('is_numeric'):
                    # Unix timestamp conversion
                    cleaning_code += f"""# 🆕 CRITICAL: Clean and convert unix timestamp column to datetime
if '{date_column}' in df.columns:
    # Convert unix timestamp to datetime (Polars: from_epoch expects seconds)
    df = df.with_columns(
        pl.col('{date_column}').cast(pl.Int64).cast(pl.Datetime(time_unit='s')).alias('{date_column}')
    )

"""
                
                elif format_info.get('is_string'):
                    if detected_format and detected_format != 'infer':
                        # Use specific format
                        cleaning_code += f"""# 🆕 CRITICAL: Clean and convert date column to datetime with detected format
if '{date_column}' in df.columns:
    # Convert to datetime with detected format '{detected_format}'
    df = df.with_columns(
        pl.col('{date_column}').str.to_datetime(format='{detected_format}', strict=False).alias('{date_column}')
    )

"""
                    else:
                        # Use infer_datetime_format with fallback
                        cleaning_code += f"""# 🆕 CRITICAL: Clean and convert date column to datetime with format detection
if '{date_column}' in df.columns:
    # Try to convert to datetime with automatic format inference
    try:
        df = df.with_columns(
            pl.col('{date_column}').str.to_datetime(strict=False).alias('{date_column}')
        )
    except:
        # If auto-detection fails, try common date formats explicitly (with and without time)
        common_formats = [
            '%Y-%m-%d %H:%M:%S.%3f', '%Y-%m-%d %H:%M:%S',
            '%d/%m/%Y %H:%M:%S', '%m/%d/%Y %H:%M:%S', '%Y/%m/%d %H:%M:%S',
            '%d-%m-%Y %H:%M:%S', '%m-%d-%Y %H:%M:%S',
            '%Y-%m-%d', '%d-%m-%Y', '%m/%d/%Y', '%d/%m/%Y', '%Y/%m/%d', '%d.%m.%Y', '%m-%d-%Y'
        ]
        for fmt in common_formats:
            try:
                df = df.with_columns(
                    pl.col('{date_column}').str.to_datetime(format=fmt, strict=False).alias('{date_column}')
                )
                # Check if this format worked for most values
                null_count = df['{date_column}'].null_count()
                if null_count < len(df) * 0.5:
                    break
            except:
                continue

"""
        
        # 🆕 IDENTIFIER DETECTION: Check if metric column is an identifier BEFORE processing
        # This handles columns like count/number columns which return record IDs
        workbook_name = getattr(self, '_current_workbook_name', None)
        
        self.logger.info(f"🔍 DEBUG: In _generate_data_cleaning_code:")
        self.logger.info(f"🔍 DEBUG:   workbook_name = '{workbook_name}'")
        self.logger.info(f"🔍 DEBUG:   metric_column = '{metric_column}'")
        self.logger.info(f"🔍 DEBUG:   df_sample is not None = {df_sample is not None}")
        if df_sample is not None:
            self.logger.info(f"🔍 DEBUG:   df_sample.columns = {list(df_sample.columns)}")
            if metric_column:
                self.logger.info(f"🔍 DEBUG:   metric_column in df_sample.columns = {metric_column in df_sample.columns}")
        
        if metric_column and workbook_name and df_sample is not None and metric_column in df_sample.columns:
            self.logger.info(f"🔍 DEBUG: All conditions met - calling _is_identifier_column('{metric_column}', '{workbook_name}')")
            is_identifier = self._is_identifier_column(metric_column, workbook_name, df_sample)
            self.logger.info(f"🔍 DEBUG: _is_identifier_column returned = {is_identifier}")
            
            if is_identifier:
                self.logger.info(f"⚡ IDENTIFIER DETECTED: '{metric_column}' (workbook: {workbook_name})")
                self.logger.info(f"   → Using helper column for row counting instead of summing identifier values")
                
                cleaning_code += f"""# ⚡ IDENTIFIER COLUMN DETECTED: '{metric_column}'
# Metadata indicates this column contains identifiers (record IDs, transaction numbers, etc.)
# Using helper column for row counting instead of summing identifier values
df = df.with_columns(pl.lit(1).alias('_count_helper'))

"""
                actual_column_to_use = '_count_helper'
                
                # ⚡ EARLY RETURN - Skip all the numeric casting logic below
                return (cleaning_code, actual_column_to_use)
        
        # PART 2: Handle metric column based on ACTUAL dtype (not heuristics)
        agg_functions = getattr(stage2_result, 'agg_functions', ['count']) or ['count']  # 🔥 FIX: Handle None
        operations = getattr(stage2_result, 'operations', []) or []  # 🔥 FIX: Handle None
        actual_column_to_use = metric_column  # Default to original column
        
        if metric_column and df_sample is not None and metric_column in df_sample.columns:
            col_dtype = df_sample[metric_column].dtype
            
            # Check if this is a string/object column
            if col_dtype == 'object' or col_dtype in [pl.Utf8, pl.Categorical]:
                self.logger.info(f"[CLEANING] Detected string column '{metric_column}' with dtype {col_dtype}")
                
                # 🔥 CRITICAL FIX: Don't create helper if metric is a group_by column
                if metric_column in group_by_columns:
                    self.logger.info(f"[CLEANING] String column '{metric_column}' is in group_by, no helper needed")
                    actual_column_to_use = metric_column  # Use original column
                else:
                    # For string columns, check if we need helper for counting/window operations
                    needs_helper = (
                        any(func in ['count', 'size'] for func in agg_functions) or
                        any(op in ['window_function', 'time_series', 'period_comparison', 'grouped_aggregation'] for op in operations)
                    )
                    
                    if needs_helper:
                        cleaning_code += f"""# String column '{metric_column}' - using helper column for counting/window operations
df = df.with_columns(pl.lit(1).alias('_count_helper'))

"""
                        actual_column_to_use = '_count_helper'
                        self.logger.info(f"[CLEANING] Created helper column '_count_helper' for string metric '{metric_column}'")
                    else:
                        self.logger.info(f"[CLEANING] String column '{metric_column}' doesn't need helper (operations: {operations})")
            
            elif metric_column not in group_by_columns:
                # Numeric column - convert to numeric as usual (only if not a group_by column)
                cleaning_code += f"""# Clean and convert {metric_column} to numeric
if '{metric_column}' in df.columns:
    # Convert to numeric, replacing errors with null
    df = df.with_columns(
        pl.col('{metric_column}').cast(pl.Float64, strict=False).alias('{metric_column}')
    )

"""
                self.logger.info(f"[CLEANING] Numeric column '{metric_column}' will be converted to numeric")
        
        return cleaning_code, actual_column_to_use
    
    def _extract_percentile_values(self, query: str) -> List[float]:
        """
        Extract percentile values from natural language queries (missing from refactored version).
        
        Examples:
        - "90th and 99th percentile" -> [0.90, 0.99]
        - "25th, 50th, 75th percentiles" -> [0.25, 0.50, 0.75]
        - "95th percentile" -> [0.95]
        - "top 10%" -> [0.90]
        - "bottom 5%" -> [0.05]
        
        Args:
            query: User's natural language query
            
        Returns:
            List of percentile values as floats (0.0-1.0)
        """
        import re
        
        percentiles = []
        query_lower = query.lower()
        
        # Pattern 1: "Nth percentile" or "Nth and Mth percentile"
        percentile_patterns = [
            r'(\d+)(?:st|nd|rd|th)\s+percentile',
            r'(\d+)(?:st|nd|rd|th)\s+and\s+(\d+)(?:st|nd|rd|th)\s+percentile',
            r'(\d+)(?:st|nd|rd|th),?\s*(\d+)(?:st|nd|rd|th),?\s*(?:and\s+)?(\d+)(?:st|nd|rd|th)\s+percentile',
        ]
        
        for pattern in percentile_patterns:
            matches = re.findall(pattern, query_lower)
            for match in matches:
                if isinstance(match, tuple):
                    # Multiple percentiles found
                    for value in match:
                        if value:  # Skip empty matches
                            percentiles.append(float(value) / 100.0)
                else:
                    # Single percentile found
                    percentiles.append(float(match) / 100.0)
        
        # Pattern 2: "top X%" or "bottom X%"
        top_bottom_patterns = [
            r'top\s+(\d+)%',
            r'bottom\s+(\d+)%'
        ]
        
        for pattern in top_bottom_patterns:
            matches = re.findall(pattern, query_lower)
            for match in matches:
                percentage = float(match) / 100.0
                if 'top' in pattern:
                    # Top X% means (100-X)th percentile
                    percentiles.append(1.0 - percentage)
                else:
                    # Bottom X% means Xth percentile
                    percentiles.append(percentage)
        
        # Pattern 3: Direct percentage values like "0.90 and 0.99"
        decimal_pattern = r'0\.(\d+)'
        matches = re.findall(decimal_pattern, query_lower)
        for match in matches:
            decimal_val = float(f"0.{match}")
            if 0.0 <= decimal_val <= 1.0:
                percentiles.append(decimal_val)
        
        # Remove duplicates and sort
        percentiles = sorted(list(set(percentiles)))
        
        self.logger.info(f"[PERCENTILE_EXTRACTION] Query: '{query}' -> Extracted percentiles: {percentiles}")
        
        # Return default percentiles if none found
        if not percentiles:
            self.logger.warning("[PERCENTILE_EXTRACTION] No percentiles found, using defaults: [0.25, 0.5, 0.75]")
            return [0.25, 0.5, 0.75]
        
        return percentiles
    
    def _extract_compare_periods_from_query(self, query: str) -> List[str]:
        """
        Extract comparison periods from natural language queries (missing from refactored version).
        
        Examples:
        - "compare first quarter to second quarter" -> ["Q1", "Q2"]
        - "Q1 vs Q2" -> ["Q1", "Q2"]
        - "January vs February" -> ["Jan", "Feb"]
        - "2023 vs 2024" -> ["2023", "2024"]
        
        Args:
            query: User's natural language query
            
        Returns:
            List of period strings for comparison
        """
        import re
        
        periods = []
        query_lower = query.lower()
        
        # Pattern 1: "first quarter", "second quarter", etc.
        quarter_names = {
            'first': 'Q1', 'second': 'Q2', 'third': 'Q3', 'fourth': 'Q4',
            '1st': 'Q1', '2nd': 'Q2', '3rd': 'Q3', '4th': 'Q4'
        }
        
        for name, quarter in quarter_names.items():
            if f"{name} quarter" in query_lower:
                if quarter not in periods:
                    periods.append(quarter)
        
        # Pattern 2: Direct quarter references "Q1", "Q2", etc.
        quarter_pattern = r'q([1-4])'
        quarter_matches = re.findall(quarter_pattern, query_lower)
        for match in quarter_matches:
            quarter = f"Q{match}"
            if quarter not in periods:
                periods.append(quarter)
        
        # Pattern 3: Month names
        month_names = {
            'january': 'Jan', 'february': 'Feb', 'march': 'Mar', 'april': 'Apr',
            'may': 'May', 'june': 'Jun', 'july': 'Jul', 'august': 'Aug',
            'september': 'Sep', 'october': 'Oct', 'november': 'Nov', 'december': 'Dec',
            'jan': 'Jan', 'feb': 'Feb', 'mar': 'Mar', 'apr': 'Apr',
            'jun': 'Jun', 'jul': 'Jul', 'aug': 'Aug', 'sep': 'Sep',
            'oct': 'Oct', 'nov': 'Nov', 'dec': 'Dec'
        }
        
        for name, month in month_names.items():
            if name in query_lower:
                if month not in periods:
                    periods.append(month)
        
        # Pattern 4: Year patterns "2023", "2024", etc.
        year_pattern = r'\b(20\d{2})\b'
        year_matches = re.findall(year_pattern, query)
        for year in year_matches:
            if year not in periods:
                periods.append(year)
        
        # Sort periods to ensure consistent ordering
        periods = sorted(periods)
        
        # 🆕 CRITICAL FIX: Combine years with months/quarters when both are present
        # Handle cases like ['2025', 'Feb', 'Mar'] -> ['2025-Feb', '2025-Mar']
        years = [p for p in periods if p.isdigit() and len(p) == 4]
        months = [p for p in periods if p in ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 
                                            'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']]
        quarters = [p for p in periods if p.startswith('Q') and len(p) == 2]
        
        # If we have both years and months/quarters, combine them
        if years and (months or quarters):
            year = years[0]  # Use first year found
            combined_periods = []
            
            if months:
                for month in months:
                    combined_periods.append(f"{year}-{month}")
            
            if quarters:
                for quarter in quarters:
                    combined_periods.append(f"{year}-{quarter}")
            
            periods = combined_periods
            self.logger.info(f"[PERIOD_EXTRACTION] Combined year {year} with periods: {periods}")
        
        self.logger.info(f"[PERIOD_EXTRACTION] Query: '{query}' -> Extracted periods: {periods}")
        
        return periods
    
    def _extract_limit_for_ranking(self, state: Dict[str, Any], stage2_result) -> Optional[int]:
        """Extract limit for ranking operations (moved from Stage 3 to work with LangGraph)"""
        # First try to get existing limit from Stage 2
        existing_limit = getattr(stage2_result, 'limit_results', None)
        if existing_limit:
            self.logger.info(f"[LIMIT_EXTRACTION] Using existing Stage 2 limit: {existing_limit}")
            return existing_limit
        
        # If no existing limit, extract from query using the same logic as Stage 3
        query = state.get("query", "")
        if not query:
            return None
            
        # Extract "top N" limits for ranking operations
        extracted_limit = self._extract_top_n_limit_simple(query)
        if extracted_limit:
            self.logger.info(f"[LIMIT_EXTRACTION] Extracted limit from query '{query}': {extracted_limit}")
            return extracted_limit
        
        self.logger.debug(f"[LIMIT_EXTRACTION] No limit found in query: '{query}'")
        return None
    
    def _extract_top_n_limit_simple(self, query: str) -> Optional[int]:
        """Simple limit extraction for operation nodes"""
        import re
        
        query_lower = query.lower()
        
        # Pattern 1: "top N", "bottom N", "first N", "last N" (including common typos)
        limit_patterns = [
            r'\btop\s+(\d+)\b',
            r'\bbottom\s+(\d+)\b',
            # Common typos for "bottom"
            r'\bbototm\s+(\d+)\b',
            r'\bbotom\s+(\d+)\b', 
            r'\bbottm\s+(\d+)\b',
            r'\bfirst\s+(\d+)\b',
            r'\blast\s+(\d+)\b',
            r'\bhighest\s+(\d+)\b',
            r'\blowest\s+(\d+)\b',
            r'\bbest\s+(\d+)\b',
            r'\bworst\s+(\d+)\b'
        ]
        
        for pattern in limit_patterns:
            matches = re.findall(pattern, query_lower)
            if matches:
                try:
                    limit = int(matches[0])
                    if 1 <= limit <= 1000:
                        return limit
                except ValueError:
                    continue
        
        return None
    
    def _is_year_explicit_in_query(self, query: str) -> bool:
        """
        Check if a year (2020-2099) is explicitly mentioned in the query.
        
        Args:
            query: User's query string (should be lowercased)
            
        Returns:
            True if a year is explicitly mentioned, False otherwise
        """
        import re
        
        # Pattern: 4-digit year between 2000-2099
        year_pattern = r'\b(20\d{2})\b'
        matches = re.findall(year_pattern, query)
        
        if matches:
            self.logger.info(f"[YEAR_DETECTION] Found explicit year(s) in query: {matches}")
            return True
        
        self.logger.debug(f"[YEAR_DETECTION] No explicit year found in query")
        return False


    def _handle_column_description_direct(self, stage1_grounded, stage2_result, query: str, df_sample: pl.DataFrame) -> Optional[NLToPythonResult]:
        """
        🆕 Direct column description handler - bypasses Stage 3 entirely
        
        Performs direct metadata lookup and returns complete NLToPythonResult.
        This is much more efficient than forcing column descriptions through 
        the code generation pipeline.
        
        Args:
            stage1_grounded: Stage 1 results
            stage2_result: Stage 2 plan with column description parameters
            query: Original user query
            df_sample: Sample dataframe for basic stats
            
        Returns:
            Complete NLToPythonResult with description data
        """
        try:
            # Extract columns to describe
            describe_columns = getattr(stage2_result, 'describe_columns', None) or getattr(stage1_grounded, 'secondary_columns', [])
            workbook_name = getattr(self, '_current_workbook_name', None)
            detail_level = getattr(stage2_result, 'detail_level', 'detailed')
            include_samples = getattr(stage2_result, 'include_samples', True)
            
            if not describe_columns:
                self.logger.warning("[COLUMN_DESC_DIRECT] No columns specified for description")
                return self._create_error_result(query, "No columns specified for description")
            
            if not workbook_name:
                self.logger.warning("[COLUMN_DESC_DIRECT] No workbook name provided - cannot load metadata")
                return self._create_error_result(query, "No workbook name provided for metadata lookup")
            
            self.logger.info(f"[COLUMN_DESC_DIRECT] Processing {len(describe_columns)} columns: {describe_columns}")
            
            # Load tableau descriptions
            descriptions = self._load_and_match_descriptions(describe_columns, workbook_name, df_sample, detail_level, include_samples)
            
            # Create a simple "code" that just assigns the result (for compatibility)
            generated_code = f"""# Column Description Lookup Results
descriptions = {descriptions}
result = descriptions
"""
            
            # Create final result
            confidence = getattr(stage2_result, 'confidence', 0.95)
            reasoning = getattr(stage2_result, 'reasoning', f'Column descriptions for: {", ".join(describe_columns)}')
            
            final_result = NLToPythonResult(
                original_query=query,
                generated_code=generated_code,
                operation_type='column_description',
                confidence=confidence,
                suggested_chart_type='table',  # Descriptions are best shown as tables
                explanation=reasoning,
                is_bottom_query=False,  # Column descriptions are not ranking queries
                is_top_query=False
            )
            
            self.logger.info(f"[COLUMN_DESC_DIRECT] ✅ Success - Generated descriptions for {len(describe_columns)} column(s)")
            return final_result
            
        except Exception as e:
            self.logger.error(f"[COLUMN_DESC_DIRECT] ❌ Error: {str(e)}", exc_info=True)
            return self._create_error_result(query, f"Error generating column descriptions: {str(e)}")

    def _load_and_match_descriptions(self, describe_columns: List[str], workbook_name: str, 
                                   df_sample: pl.DataFrame, detail_level: str, include_samples: bool) -> List[Dict[str, Any]]:
        """
        Load tableau descriptions and match with requested columns
        
        Args:
            describe_columns: List of column names to describe
            workbook_name: Workbook name for metadata lookup (will be normalized dynamically)
            df_sample: Sample dataframe for stats
            detail_level: Level of detail for descriptions
            include_samples: Whether to include sample values
            
        Returns:
            List of description dictionaries
        """
        import json
        from pathlib import Path
        
        descriptions = []
        
        # 🆕 DYNAMIC WORKBOOK NAME RESOLUTION - NO HARDCODING
        actual_workbook_name = self._resolve_workbook_name(workbook_name)
        
        # Load business descriptions from tableau metadata
        try:
            if actual_workbook_name:
                tableau_desc_path = Path(f'tableau_descriptions/{actual_workbook_name}/field_descriptions_{actual_workbook_name}.json')
                if tableau_desc_path.exists():
                    with open(tableau_desc_path, 'r', encoding='utf-8') as f:
                        business_metadata = json.load(f)
                    self.logger.info(f"[COLUMN_DESC] ✅ Loaded metadata from: {tableau_desc_path}")
                else:
                    business_metadata = {'field_descriptions': []}
                    self.logger.warning(f"[COLUMN_DESC] Metadata file not found at {tableau_desc_path}")
            else:
                business_metadata = {'field_descriptions': []}
                self.logger.warning(f"[COLUMN_DESC] Could not resolve workbook name '{workbook_name}' to existing directory")
        except Exception as e:
            business_metadata = {'field_descriptions': []}
            self.logger.error(f"[COLUMN_DESC] Could not load metadata: {e}")
        
        # Process each requested column
        for column in describe_columns:
            # Find business description
            business_desc = None
            
            # Match column name (handle both exact and fuzzy matches)
            for field in business_metadata.get('field_descriptions', []):
                field_name = field.get('field_name', '')
                # Try exact match first
                if field_name.lower().replace(' ', '_') == column.lower():
                    business_desc = field.get('comment', '')
                    break
                # Try contains match
                elif column.lower().replace('_', ' ') in field_name.lower():
                    business_desc = field.get('comment', '')
                    break
                # Try reverse contains match
                elif field_name.lower().replace(' ', '_') in column.lower():
                    business_desc = field.get('comment', '')
                    break
            
            # 🆕 ENHANCED STATISTICS BASED ON TABLEAU DATA TYPE CLASSIFICATION
            column_stats = {}
            data_type_classification = "unknown"
            
            # ⚡ ALWAYS calculate enhanced statistics for column descriptions (ignore detail_level)
            if column in df_sample.columns:
                try:
                    # Extract data type from business description
                    data_type_classification = self._extract_data_type_from_description(business_desc or "")
                    
                    # Calculate enhanced statistics based on data type
                    column_stats = self._calculate_enhanced_statistics(
                        df_sample, column, data_type_classification, include_samples
                    )
                    
                    # Add the polars data type for reference
                    column_stats['polars_dtype'] = str(df_sample[column].dtype)
                    
                    self.logger.info(f"[COLUMN_DESC] Enhanced stats for '{column}' (type: {data_type_classification}) - {len(column_stats)} metrics")
                    
                except Exception as e:
                    column_stats = {'error': f'Could not analyze column: {str(e)}'}
                    self.logger.error(f"[COLUMN_DESC] Error generating enhanced stats for {column}: {e}")
            elif column not in df_sample.columns:
                column_stats = {'error': f'Column "{column}" not found in dataframe'}
            
            # Build enhanced description object
            description = {
                'column_name': column,
                'business_description': business_desc or f'No description available for {column}',
                'data_type_classification': data_type_classification,
                'statistical_profile': column_stats,
                'detail_level': detail_level
            }
            
            descriptions.append(description)
        
        return descriptions

    def _resolve_workbook_name(self, input_workbook_name: str) -> Optional[str]:
        """
        🆕 Dynamically resolve workbook name to match actual directory structure
        
        Handles common name variations without hardcoding:
        - Spaces vs no spaces: "FRO Dashboard_final" vs "FRODashboard_final"  
        - Underscores vs spaces: "FRO_Dashboard_final" vs "FRO Dashboard_final"
        - Case variations: "frodashboard_final" vs "FRODashboard_final"
        
        Args:
            input_workbook_name: Workbook name as provided (may have variations)
            
        Returns:
            Actual workbook directory name that exists, or None if not found
        """
        from pathlib import Path
        
        if not input_workbook_name:
            return None
        
        base_dir = Path('tableau_descriptions')
        if not base_dir.exists():
            self.logger.warning(f"[WORKBOOK_RESOLVE] tableau_descriptions directory not found")
            return None
        
        # Generate common variations of the workbook name
        variations = set()
        
        # Original name
        variations.add(input_workbook_name)
        
        # Remove spaces
        variations.add(input_workbook_name.replace(' ', ''))
        
        # Replace spaces with underscores
        variations.add(input_workbook_name.replace(' ', '_'))
        
        # Replace underscores with spaces  
        variations.add(input_workbook_name.replace('_', ' '))
        
        # Remove both spaces and underscores
        variations.add(input_workbook_name.replace(' ', '').replace('_', ''))
        
        # Add common case variations for each
        case_variations = set()
        for var in variations:
            case_variations.add(var)                    # Original case
            case_variations.add(var.lower())            # All lowercase
            case_variations.add(var.upper())            # All uppercase  
            case_variations.add(var.title())            # Title Case
            case_variations.add(var.capitalize())       # First letter capitalized
        
        variations.update(case_variations)
        
        # Remove empty strings
        variations.discard('')
        
        self.logger.debug(f"[WORKBOOK_RESOLVE] Checking {len(variations)} variations for '{input_workbook_name}'")
        
        # Check which variation actually exists
        for variation in variations:
            workbook_dir = base_dir / variation
            metadata_file = workbook_dir / f'field_descriptions_{variation}.json'
            
            if workbook_dir.exists() and metadata_file.exists():
                self.logger.info(f"[WORKBOOK_RESOLVE] ✅ Found match: '{input_workbook_name}' → '{variation}'")
                return variation
        
        # If no exact match, try partial matching on existing directories
        try:
            existing_dirs = [d.name for d in base_dir.iterdir() if d.is_dir()]
            self.logger.debug(f"[WORKBOOK_RESOLVE] Existing directories: {existing_dirs}")
            
            # Try fuzzy matching on existing directory names
            input_cleaned = input_workbook_name.lower().replace(' ', '').replace('_', '')
            
            for existing_dir in existing_dirs:
                existing_cleaned = existing_dir.lower().replace(' ', '').replace('_', '')
                
                # If cleaned versions match, use the existing directory
                if input_cleaned == existing_cleaned:
                    metadata_file = base_dir / existing_dir / f'field_descriptions_{existing_dir}.json'
                    if metadata_file.exists():
                        self.logger.info(f"[WORKBOOK_RESOLVE] ✅ Fuzzy match: '{input_workbook_name}' → '{existing_dir}'")
                        return existing_dir
        
        except Exception as e:
            self.logger.error(f"[WORKBOOK_RESOLVE] Error during fuzzy matching: {e}")
        
        self.logger.warning(f"[WORKBOOK_RESOLVE] ❌ No match found for workbook '{input_workbook_name}'")
        return None

    def _extract_data_type_from_description(self, business_description: str) -> str:
        """
        🆕 Extract data type from tableau business description
        
        Parses: "(categorical) Country where account is located"
        Returns: "categorical"
        
        Supported types:
        - categorical
        - numerical  
        - identifier
        - datetime
        - text
        """
        import re
        
        if not business_description:
            return "unknown"
        
        # Extract type from parentheses at the start
        match = re.match(r'\((\w+)\)', business_description.strip())
        if match:
            data_type = match.group(1).lower()
            self.logger.debug(f"[DATA_TYPE_EXTRACTION] Extracted '{data_type}' from '{business_description[:50]}...'")
            return data_type
        
        # Fallback: guess from keywords
        desc_lower = business_description.lower()
        if any(word in desc_lower for word in ['count', 'number', 'amount', 'time', 'hours', 'days', 'age']):
            self.logger.debug(f"[DATA_TYPE_EXTRACTION] Guessed 'numerical' from keywords in '{business_description[:50]}...'")
            return "numerical"
        elif any(word in desc_lower for word in ['status', 'country', 'category', 'type', 'priority']):
            self.logger.debug(f"[DATA_TYPE_EXTRACTION] Guessed 'categorical' from keywords in '{business_description[:50]}...'")
            return "categorical"
        elif any(word in desc_lower for word in ['id', 'identifier']):
            self.logger.debug(f"[DATA_TYPE_EXTRACTION] Guessed 'identifier' from keywords in '{business_description[:50]}...'")
            return "identifier" 
        
        self.logger.warning(f"[DATA_TYPE_EXTRACTION] Could not determine type for '{business_description[:50]}...', defaulting to 'unknown'")
        return "unknown"

    def _calculate_categorical_stats(self, df: pl.DataFrame, column_name: str) -> Dict[str, Any]:
        """
        🆕 Calculate comprehensive categorical statistics
        
        Returns mode, top values, frequencies, and diversity metrics
        """
        try:
            # Get value counts
            value_counts = df[column_name].drop_nulls().value_counts().sort('count', descending=True)
            
            if len(value_counts) == 0:
                return {'error': 'No non-null values found'}
            
            # Calculate mode (most frequent value)
            mode_row = value_counts.row(0)
            mode_value = mode_row[0]
            mode_count = mode_row[1]
            total_non_null = df[column_name].drop_nulls().len()
            mode_percentage = round((mode_count / total_non_null) * 100, 1) if total_non_null > 0 else 0
            
            # Get top values (limit to 5)
            top_values = []
            for i in range(min(5, len(value_counts))):
                row = value_counts.row(i)
                value = row[0]
                count = row[1]
                percentage = round((count / total_non_null) * 100, 1) if total_non_null > 0 else 0
                top_values.append({
                    'value': str(value),
                    'count': int(count),
                    'percentage': percentage
                })
            
            # Calculate diversity index (higher = more evenly distributed)
            # Using Simpson's Diversity Index: 1 - sum(pi^2) where pi is proportion of each category
            diversity_index = 0.0
            if total_non_null > 0:
                sum_squared_proportions = 0.0
                for i in range(len(value_counts)):
                    proportion = value_counts.row(i)[1] / total_non_null
                    sum_squared_proportions += proportion ** 2
                diversity_index = round(1 - sum_squared_proportions, 3)
            
            # Count rare values (< 1% of data)
            rare_threshold = max(1, total_non_null * 0.01)
            rare_values_count = sum(1 for i in range(len(value_counts)) if value_counts.row(i)[1] < rare_threshold)
            
            self.logger.debug(f"[CATEGORICAL_STATS] {column_name}: mode='{mode_value}' ({mode_percentage}%), diversity={diversity_index}")
            
            return {
                'mode': str(mode_value),
                'mode_frequency': int(mode_count),
                'mode_percentage': mode_percentage,
                'top_values': top_values,
                'diversity_index': diversity_index,
                'rare_values_count': rare_values_count,
                'has_null_category': df[column_name].null_count() > 0
            }
            
        except Exception as e:
            self.logger.error(f"[CATEGORICAL_STATS] Error calculating stats for {column_name}: {e}")
            return {'error': f'Could not calculate categorical statistics: {str(e)}'}

    def _calculate_numerical_stats(self, df: pl.DataFrame, column_name: str) -> Dict[str, Any]:
        """
        🆕 Calculate comprehensive numerical statistics
        
        Returns mean, median, quartiles, outliers, and distribution info
        Handles data type conversion for string columns that contain numeric data
        """
        try:
            # Get the column and check its type
            column = df[column_name]
            column_dtype = str(column.dtype)
            
            self.logger.debug(f"[NUMERICAL_STATS] Column '{column_name}' has dtype: {column_dtype}")
            
            # Try to get numeric data with robust type handling
            numeric_series = None
            
            if column_dtype in ['Float64', 'Float32', 'Int64', 'Int32', 'Int16', 'Int8', 'UInt64', 'UInt32', 'UInt16', 'UInt8']:
                # Already numeric - use as is
                numeric_series = column.drop_nulls()
                self.logger.debug(f"[NUMERICAL_STATS] Using native numeric data for '{column_name}'")
                
            elif column_dtype in ['Utf8', 'String']:
                # String column - try to convert to numeric
                self.logger.debug(f"[NUMERICAL_STATS] Attempting to convert string column '{column_name}' to numeric")
                
                try:
                    # Try to cast to float, handling various formats
                    numeric_series = column.drop_nulls().cast(pl.Float64, strict=False)
                    
                    # Filter out any null values that resulted from failed conversions
                    numeric_series = numeric_series.drop_nulls()
                    
                    if len(numeric_series) == 0:
                        # Try alternative parsing for time formats like "1:30:45" or "1.5 hours"
                        self.logger.debug(f"[NUMERICAL_STATS] Direct cast failed, trying format parsing for '{column_name}'")
                        
                        # Sample a few values to understand the format
                        sample_values = column.drop_nulls().limit(5).to_list()
                        sample_str = ', '.join(str(v) for v in sample_values[:3])
                        
                        return {
                            'error': f'Could not convert string values to numeric. Sample values: {sample_str}',
                            'conversion_attempted': True,
                            'original_dtype': column_dtype,
                            'sample_values': sample_values[:5]
                        }
                        
                except Exception as conversion_error:
                    sample_values = column.drop_nulls().limit(5).to_list()
                    sample_str = ', '.join(str(v) for v in sample_values[:3])
                    
                    self.logger.warning(f"[NUMERICAL_STATS] Could not convert '{column_name}' to numeric: {conversion_error}")
                    return {
                        'error': f'Numeric conversion failed: {str(conversion_error)}. Sample values: {sample_str}',
                        'conversion_attempted': True,
                        'original_dtype': column_dtype,
                        'sample_values': sample_values[:5]
                    }
            else:
                # Unsupported data type for numerical analysis
                sample_values = column.drop_nulls().limit(5).to_list()
                return {
                    'error': f'Unsupported data type for numerical analysis: {column_dtype}',
                    'original_dtype': column_dtype,
                    'sample_values': sample_values[:5]
                }
            
            if len(numeric_series) == 0:
                return {'error': 'No valid numerical values found after conversion'}
            
            # Basic statistics with error handling
            try:
                mean_val = numeric_series.mean()
                median_val = numeric_series.median()
                min_val = numeric_series.min()
                max_val = numeric_series.max()
                std_val = numeric_series.std()
                
                # Quartiles
                q1 = numeric_series.quantile(0.25)
                q3 = numeric_series.quantile(0.75)
                
                # Outlier detection using IQR method
                iqr = q3 - q1
                outlier_lower = q1 - 1.5 * iqr
                outlier_upper = q3 + 1.5 * iqr
                outliers = numeric_series.filter((numeric_series < outlier_lower) | (numeric_series > outlier_upper))
                outlier_count = len(outliers)
                
                # Zero count
                zero_count = numeric_series.filter(numeric_series == 0).len()
                
                # Distribution shape analysis (basic)
                if std_val is not None and std_val > 0:
                    # Calculate skewness (simplified)
                    mean_centered = numeric_series - mean_val
                    skew_numerator = (mean_centered ** 3).mean()
                    skew_denominator = std_val ** 3
                    skewness = skew_numerator / skew_denominator if skew_denominator != 0 else 0
                    
                    if abs(skewness) < 0.5:
                        distribution_shape = "normal"
                    elif skewness > 0.5:
                        distribution_shape = "right_skewed"
                    else:
                        distribution_shape = "left_skewed"
                else:
                    distribution_shape = "constant"
                    skewness = 0
                
                self.logger.debug(f"[NUMERICAL_STATS] {column_name}: mean={mean_val:.2f}, median={median_val:.2f}, std={std_val:.2f}, outliers={outlier_count}")
                
                # Ensure all values are properly converted and handle None values
                result = {
                    'mean': round(float(mean_val), 3) if mean_val is not None else 0.0,
                    'median': round(float(median_val), 3) if median_val is not None else 0.0,
                    'min': round(float(min_val), 3) if min_val is not None else 0.0,
                    'max': round(float(max_val), 3) if max_val is not None else 0.0,
                    'std': round(float(std_val), 3) if std_val is not None else 0.0,
                    'quartiles': [round(float(q1), 3) if q1 is not None else 0.0, 
                                 round(float(median_val), 3) if median_val is not None else 0.0, 
                                 round(float(q3), 3) if q3 is not None else 0.0],
                    'skewness': round(float(skewness), 3) if skewness is not None else 0.0,
                    'distribution_shape': distribution_shape,
                    'outlier_count': int(outlier_count),
                    'zero_count': int(zero_count),
                    'conversion_successful': True,
                    'original_dtype': column_dtype
                }
                
                return result
                
            except Exception as stats_error:
                self.logger.error(f"[NUMERICAL_STATS] Error during statistical calculations for {column_name}: {stats_error}")
                return {
                    'error': f'Statistical calculation error: {str(stats_error)}',
                    'original_dtype': column_dtype,
                    'data_points': len(numeric_series),
                    'calculation_attempted': True
                }
            
        except Exception as e:
            self.logger.error(f"[NUMERICAL_STATS] Error calculating stats for {column_name}: {e}")
            return {'error': f'Could not calculate numerical statistics: {str(e)}'}

    def _calculate_identifier_stats(self, df: pl.DataFrame, column_name: str) -> Dict[str, Any]:
        """
        🆕 Calculate statistics for identifier columns
        
        Returns uniqueness info, duplicate count, and format patterns
        """
        try:
            total_rows = len(df)
            non_null_series = df[column_name].drop_nulls()
            non_null_count = len(non_null_series)
            
            if non_null_count == 0:
                return {'error': 'No non-null identifier values found'}
            
            # Uniqueness analysis
            unique_count = non_null_series.n_unique()
            duplicate_count = non_null_count - unique_count
            uniqueness_percentage = round((unique_count / non_null_count) * 100, 1)
            
            # Sample some values to analyze patterns
            sample_values = non_null_series.unique().limit(10).to_list()
            
            # Basic format analysis
            sample_str = str(sample_values[0]) if sample_values else ""
            format_info = {
                'typical_length': len(sample_str),
                'contains_numbers': any(c.isdigit() for c in sample_str),
                'contains_letters': any(c.isalpha() for c in sample_str),
                'contains_special_chars': any(not c.isalnum() for c in sample_str)
            }
            
            self.logger.debug(f"[IDENTIFIER_STATS] {column_name}: uniqueness={uniqueness_percentage}%, duplicates={duplicate_count}")
            
            return {
                'uniqueness_percentage': uniqueness_percentage,
                'duplicate_count': duplicate_count,
                'is_unique_key': duplicate_count == 0 and non_null_count == total_rows,
                'sample_values': [str(v) for v in sample_values[:3]],
                'format_info': format_info
            }
            
        except Exception as e:
            self.logger.error(f"[IDENTIFIER_STATS] Error calculating stats for {column_name}: {e}")
            return {'error': f'Could not calculate identifier statistics: {str(e)}'}

    def _calculate_enhanced_statistics(self, df: pl.DataFrame, column_name: str, data_type: str, include_samples: bool = True) -> Dict[str, Any]:
        """
        🆕 Calculate appropriate statistics based on tableau data type classification
        
        Routes to the appropriate statistics calculator based on data type
        """
        # Base statistics for all types
        base_stats = {
            'total_rows': len(df),
            'unique_count': df[column_name].n_unique() if column_name in df.columns else 0,
            'null_count': df[column_name].null_count() if column_name in df.columns else len(df),
            'null_percentage': round((df[column_name].null_count() / len(df)) * 100, 1) if len(df) > 0 and column_name in df.columns else 100.0
        }
        
        if column_name not in df.columns:
            return {**base_stats, 'error': f'Column "{column_name}" not found in dataframe'}
        
        # Add sample values if requested
        if include_samples:
            try:
                sample_values = df[column_name].drop_nulls().unique().limit(5).to_list()
                base_stats['sample_values'] = [str(v) for v in sample_values]
            except Exception as e:
                self.logger.warning(f"[ENHANCED_STATS] Could not get sample values for {column_name}: {e}")
                base_stats['sample_values'] = []
        
        # Calculate type-specific statistics with fallback handling
        if data_type == 'categorical':
            type_specific_stats = self._calculate_categorical_stats(df, column_name)
        elif data_type == 'numerical':
            type_specific_stats = self._calculate_numerical_stats(df, column_name)
            
            # 🔧 FALLBACK: If numerical analysis fails, try categorical analysis
            if 'error' in type_specific_stats and 'conversion' in type_specific_stats.get('error', '').lower():
                self.logger.warning(f"[ENHANCED_STATS] Numerical analysis failed for '{column_name}', falling back to categorical analysis")
                categorical_fallback = self._calculate_categorical_stats(df, column_name)
                
                # Merge the error info with categorical results
                type_specific_stats = {
                    **type_specific_stats,  # Keep the error info
                    'fallback_analysis': categorical_fallback,
                    'fallback_type': 'categorical',
                    'classification_mismatch': True
                }
                
        elif data_type == 'identifier':
            type_specific_stats = self._calculate_identifier_stats(df, column_name)
        else:
            type_specific_stats = {}
        
        # Combine base and type-specific stats
        enhanced_stats = {**base_stats, **type_specific_stats}
        
        # Log success/failure info
        if 'error' in type_specific_stats:
            self.logger.warning(f"[ENHANCED_STATS] {data_type} analysis had issues for '{column_name}' - {len(enhanced_stats)} metrics (with error info)")
        else:
            self.logger.info(f"[ENHANCED_STATS] Generated {data_type} statistics for '{column_name}' - {len(enhanced_stats)} metrics")
        
        return enhanced_stats

    def _create_error_result(self, query: str, error_message: str) -> NLToPythonResult:
        """Create error result for column descriptions"""
        return NLToPythonResult(
            original_query=query,
            generated_code=f"result = [{'error': '{error_message}'}]",
            operation_type='column_description',
            confidence=0.0,
            suggested_chart_type='table',
            explanation=f"Error: {error_message}",
            is_bottom_query=False,
            is_top_query=False
        )

    def _suggest_chart_type(self, operation_type: str) -> str:
        """Suggest appropriate chart type based on operation"""
        chart_mapping = {
            "period_comparison": "line",
            "time_series": "line", 
            "composition_percentage": "bar",
            "breakdown": "pie",
            "distribution": "histogram",
            "ranking": "bar",
            "percentile": "box",
            "grouped_aggregation": "bar",
            "column_description": "table",  # 🆕 Added column description mapping
        }
        return chart_mapping.get(operation_type, "bar")


# ============================================================================
# COMPATIBILITY ALIASES
# ============================================================================

# Alias for backward compatibility with V4
NLToPythonV4 = NLToPythonGeneratorV5
NLToPythonGenerator = NLToPythonGeneratorV5