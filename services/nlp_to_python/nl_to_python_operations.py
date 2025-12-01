"""
Natural Language to Python Code Generator - Operations Module
Contains individual operation nodes, routing logic, and result combination.

This module contains:
- Individual operation node implementations (PeriodComparisonNode, TimeSeriesNode, etc.)
- Operation router for intelligent execution planning
- Results combiner for merging multiple operation outputs
- Operation registry and parameter extraction utilities
"""

import re
import time
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
import polars as pl
import numpy as np

# Import base classes from schemas
from .nl_to_python_schemas import (
    BaseOperationNode, ValidationResult, OperationResult, 
    NLToPythonState, setup_module_logger, traceable
)

# Import code generators
from .nl_to_python_codegen import (
    PeriodComparisonCodeGen, TimeSeriesCodeGen, RankingCodeGen, 
    BreakdownCodeGen, GroupedAggregationCodeGen, WindowFunctionCodeGen,
    PercentileCodeGen, CompositionPercentageCodeGen, PivotCodeGen
)


# ============================================================================
# ALL OPERATION NODES - INDIVIDUAL LANGGRAPH NODES
# ============================================================================

class PeriodComparisonNode(BaseOperationNode):
    """🆕 Period Comparison as individual LangGraph node"""
    
    def __init__(self):
        super().__init__("period_comparison")
    
    def validate_inputs(self, state: Dict[str, Any]) -> ValidationResult:
        stage1_result = state.get("stage1_grounded")
        stage2_result = state.get("stage2_result")
        
        if not stage1_result:
            return ValidationResult(False, "Missing Stage 1 result")
        if not stage2_result:
            return ValidationResult(False, "Missing Stage 2 result")
        if not stage1_result.date_column:
            return ValidationResult(False, "Period comparison requires date column")
            
        return ValidationResult(True)
    
    def execute_operation(self, state: Dict[str, Any]) -> OperationResult:
        try:
            operation_params = self._extract_operation_params(state)
            
            # Use existing PeriodComparisonCodeGen
            generator = PeriodComparisonCodeGen()
            generated_code = generator.generate(operation_params)
            
            if not generated_code or len(generated_code.strip()) < 10:
                return OperationResult(False, error="Failed to generate period comparison code")
            
            # Execute the code to get data
            df_sample = state.get("df_sample")
            result_data = self._execute_code(generated_code, df_sample)
            
            return OperationResult(
                success=True,
                data=result_data,
                generated_code=generated_code,
                metadata={"operation_type": "period_comparison"}
            )
            
        except Exception as e:
            return OperationResult(False, error=f"Period comparison error: {str(e)}")
    
    def _execute_code(self, code: str, df_sample: pl.DataFrame) -> Any:
        """Execute generated code and return result"""
        try:
            exec_globals = {
                'df': df_sample.clone(),
                'pl': pl,
                'np': np,
                'datetime': datetime,
                'timedelta': timedelta
            }
            
            exec(code, exec_globals)
            return exec_globals.get('result', df_sample)
            
        except Exception as e:
            self.logger.error(f"Code execution error: {e}")
            return None

    def _extract_operation_params(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Extract operation parameters from state"""
        stage1_result = state.get("stage1_grounded")
        stage2_result = state.get("stage2_result")
        
        if not stage1_result or not stage2_result:
            return {}
        
        # Build operation_params from state (same as existing Stage 3)
        operation_params = {
            'numerator_column': stage2_result.numerator_column or stage1_result.metric_column,
            'group_by_columns': stage2_result.group_by_columns or stage1_result.group_by_columns or [],
            'agg_column': stage2_result.agg_column or stage1_result.metric_column,
            'agg_functions': stage2_result.agg_functions or ['count'],
            'index': stage2_result.index or stage1_result.group_by_columns or [],
            'columns': stage2_result.columns,
            'values': stage2_result.values or stage1_result.metric_column,
            'aggfunc': stage2_result.aggfunc or 'count',
            'column': stage2_result.column or stage1_result.metric_column,
            'rate_name': stage2_result.rate_name or 'rate',
            'as_percentage': stage2_result.as_percentage,
            'date_column': stage1_result.date_column,
            # Period comparison parameters
            'comparison_type': stage2_result.comparison_type,
            'period_granularity': stage2_result.period_granularity,
            'shift_periods': stage2_result.shift_periods,
            'compare_periods': stage2_result.compare_periods,
            'output_format': stage2_result.output_format,
            'metric_column': stage1_result.metric_column,
            # Time series parameters
            'time_series_granularity': stage2_result.time_series_granularity,
            'time_series_include_comparison': stage2_result.time_series_include_comparison,
            'time_series_comparison_type': stage2_result.time_series_comparison_type,
            # Window function parameters
            'window_type': stage2_result.window_type,
            'window_size': stage2_result.window_size,
            'window_unit': stage2_result.window_unit,
            'operation': stage2_result.window_operation,
            'window_is_time_based': stage2_result.window_is_time_based,
            'column2': stage2_result.window_column2,
            'query': state.get("query", ""),  # For context
            'filters': getattr(stage1_result, "filters", None) or [
                       {
                        "column": getattr(stage1_result, "filter_column", None),
                        "value": getattr(stage1_result, "filter_value", None)
                       }
                        ] if getattr(stage1_result, "filter_column", None) and getattr(stage1_result, "filter_value", None) else [],

            'temporal_filters': [
                filter_entry
                for f in (getattr(stage2_result, "temporal_filters", []) or [])
                if f is not None
                for filter_entry in [
                    # Month filter (if month exists)
                    {
                        "column": stage1_result.date_column,
                        "type": "month",
                        "value": getattr(f, "month", None)
                    } if getattr(f, "month", None) is not None else None,
                    # Year filter (if year exists)  
                    {
                        "column": stage1_result.date_column,
                        "type": "year", 
                        "value": getattr(f, "year", None)
                    } if getattr(f, "year", None) is not None else None
                ]
                if filter_entry is not None
            ],

            # NEW: Extract limit and query type information for ranking operations
            # 🔥 FIX BY JITENDRA: Extract limit directly from query text, ignore Stage 2 limit (returns wrong default of 10)
            'limit_results': self._extract_top_n_limit_simple(state.get("query", "")),
            'is_top_query': RankingCodeGen._detect_top_query(state.get("query", "")),
            'is_bottom_query': RankingCodeGen._detect_bottom_query(state.get("query", ""))

        }
        
        return operation_params
    
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
        """Simple extraction of top N limit from query"""
        query_lower = query.lower()
        
        # Look for patterns like "top 5", "bottom 10", "first 3", "last 7" (including typos)
        import re
        patterns = [
            r'\b(?:top|bottom|bototm|botom|bottm|first|last|highest|lowest)\s+(\d+)\b',
            r'\b(\d+)\s+(?:top|bottom|bototm|botom|bottm|highest|lowest|best|worst)\b',
            r'\blimit\s+(\d+)\b',
            r'\bshow\s+(\d+)\b'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, query_lower)
            if match:
                try:
                    return int(match.group(1))
                except (ValueError, IndexError):
                    continue
        
        return None


class TimeSeriesNode(BaseOperationNode):
    """🆕 Time Series as individual LangGraph node"""
    
    def __init__(self):
        super().__init__("time_series")
    
    def validate_inputs(self, state: Dict[str, Any]) -> ValidationResult:
        stage1_result = state.get("stage1_grounded")
        if not stage1_result or not stage1_result.date_column:
            return ValidationResult(False, "Time series requires date column")
        return ValidationResult(True)
    
    def execute_operation(self, state: Dict[str, Any]) -> OperationResult:
        try:
            operation_params = self._extract_operation_params(state)
            
            generator = TimeSeriesCodeGen()
            generated_code = generator.generate(operation_params)
            
            if not generated_code:
                return OperationResult(False, error="Failed to generate time series code")
            
            df_sample = state.get("df_sample")
            result_data = self._execute_code(generated_code, df_sample)
            
            return OperationResult(
                success=True,
                data=result_data,
                generated_code=generated_code,
                metadata={"operation_type": "time_series"}
            )
            
        except Exception as e:
            return OperationResult(False, error=f"Time series error: {str(e)}")
    
    def _execute_code(self, code: str, df_sample: pl.DataFrame) -> Any:
        """Execute generated code and return result"""
        try:
            exec_globals = {
                'df': df_sample.clone(),
                'pl': pl,
                'np': np,
                'datetime': datetime,
                'timedelta': timedelta
            }
            
            exec(code, exec_globals)
            return exec_globals.get('result', df_sample)
            
        except Exception as e:
            self.logger.error(f"Code execution error: {e}")
            return None
    
    def _extract_operation_params(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Extract operation parameters from state - delegate to base implementation"""
        return PeriodComparisonNode._extract_operation_params(self, state)


class RankingNode(BaseOperationNode):
    """🆕 Ranking as individual LangGraph node"""
    
    def __init__(self):
        super().__init__("ranking")
    
    def validate_inputs(self, state: Dict[str, Any]) -> ValidationResult:
        stage1_result = state.get("stage1_grounded")
        if not stage1_result or not stage1_result.metric_column:
            return ValidationResult(False, "Ranking requires metric column")
        return ValidationResult(True)
    
    def execute_operation(self, state: Dict[str, Any]) -> OperationResult:
        try:
            operation_params = self._extract_operation_params(state)
            
            generator = RankingCodeGen()
            generated_code = generator.generate(operation_params)
            
            if not generated_code:
                return OperationResult(False, error="Failed to generate ranking code")
            
            df_sample = state.get("df_sample")
            result_data = self._execute_code(generated_code, df_sample)
            
            return OperationResult(
                success=True,
                data=result_data,
                generated_code=generated_code,
                metadata={"operation_type": "ranking"}
            )
            
        except Exception as e:
            return OperationResult(False, error=f"Ranking error: {str(e)}")
    
    def _execute_code(self, code: str, df_sample: pl.DataFrame) -> Any:
        """Execute generated code and return result"""
        try:
            exec_globals = {
                'df': df_sample.clone(),
                'pl': pl,
                'np': np,
                'datetime': datetime,
                'timedelta': timedelta
            }
            
            exec(code, exec_globals)
            return exec_globals.get('result', df_sample)
            
        except Exception as e:
            self.logger.error(f"Code execution error: {e}")
            return None

    def _extract_operation_params(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Extract operation parameters from state - delegate to base implementation"""
        return PeriodComparisonNode._extract_operation_params(self, state)


class BreakdownNode(BaseOperationNode):
    """🆕 Breakdown as individual LangGraph node"""
    
    def __init__(self):
        super().__init__("breakdown")
    
    def validate_inputs(self, state: Dict[str, Any]) -> ValidationResult:
        stage2_result = state.get("stage2_result")
        if not stage2_result or not stage2_result.column:
            return ValidationResult(False, "Breakdown requires column specification")
        return ValidationResult(True)
    
    def execute_operation(self, state: Dict[str, Any]) -> OperationResult:
        try:
            operation_params = self._extract_operation_params(state)
            
            generator = BreakdownCodeGen()
            generated_code = generator.generate(operation_params)
            
            if not generated_code:
                return OperationResult(False, error="Failed to generate breakdown code")
            
            df_sample = state.get("df_sample")
            result_data = self._execute_code(generated_code, df_sample)
            
            return OperationResult(
                success=True,
                data=result_data,
                generated_code=generated_code,
                metadata={"operation_type": "breakdown"}
            )
            
        except Exception as e:
            return OperationResult(False, error=f"Breakdown error: {str(e)}")
    
    def _execute_code(self, code: str, df_sample: pl.DataFrame) -> Any:
        """Execute generated code and return result"""
        try:
            exec_globals = {
                'df': df_sample.clone(),
                'pl': pl,
                'np': np,
                'datetime': datetime,
                'timedelta': timedelta
            }
            
            exec(code, exec_globals)
            return exec_globals.get('result', df_sample)
            
        except Exception as e:
            self.logger.error(f"Code execution error: {e}")
            return None

    def _extract_operation_params(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Extract operation parameters from state - delegate to base implementation"""
        return PeriodComparisonNode._extract_operation_params(self, state)


class GroupedAggregationNode(BaseOperationNode):
    """🆕 Grouped Aggregation as individual LangGraph node"""
    
    def __init__(self):
        super().__init__("grouped_aggregation")
    
    def validate_inputs(self, state: Dict[str, Any]) -> ValidationResult:
        stage1_result = state.get("stage1_grounded")
        if not stage1_result:
            return ValidationResult(False, "Missing Stage 1 result")
        return ValidationResult(True)
    
    def execute_operation(self, state: Dict[str, Any]) -> OperationResult:
        try:
            operation_params = self._extract_operation_params(state)
            
            generator = GroupedAggregationCodeGen()
            generated_code = generator.generate(operation_params)
            
            if not generated_code:
                return OperationResult(False, error="Failed to generate aggregation code")
            
            df_sample = state.get("df_sample")
            result_data = self._execute_code(generated_code, df_sample)
            
            return OperationResult(
                success=True,
                data=result_data,
                generated_code=generated_code,
                metadata={"operation_type": "grouped_aggregation"}
            )
            
        except Exception as e:
            return OperationResult(False, error=f"Aggregation error: {str(e)}")
    
    def _execute_code(self, code: str, df_sample: pl.DataFrame) -> Any:
        """Execute generated code and return result"""
        try:
            exec_globals = {
                'df': df_sample.clone(),
                'pl': pl,
                'np': np,
                'datetime': datetime,
                'timedelta': timedelta
            }
            
            exec(code, exec_globals)
            return exec_globals.get('result', df_sample)
            
        except Exception as e:
            self.logger.error(f"Code execution error: {e}")
            return None

    def _extract_operation_params(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Extract operation parameters from state - delegate to base implementation"""
        return PeriodComparisonNode._extract_operation_params(self, state)


class WindowFunctionNode(BaseOperationNode):
    """🆕 Window Function as individual LangGraph node"""
    
    def __init__(self):
        super().__init__("window_function")
    
    def validate_inputs(self, state: Dict[str, Any]) -> ValidationResult:
        stage2_result = state.get("stage2_result")
        if not stage2_result or not stage2_result.window_type:
            return ValidationResult(False, "Window function requires window type")
        return ValidationResult(True)
    
    def execute_operation(self, state: Dict[str, Any]) -> OperationResult:
        try:
            operation_params = self._extract_operation_params(state)
            
            generator = WindowFunctionCodeGen()
            generated_code = generator.generate(operation_params)
            
            if not generated_code:
                return OperationResult(False, error="Failed to generate window function code")
            
            df_sample = state.get("df_sample")
            result_data = self._execute_code(generated_code, df_sample)
            
            return OperationResult(
                success=True,
                data=result_data,
                generated_code=generated_code,
                metadata={"operation_type": "window_function"}
            )
            
        except Exception as e:
            return OperationResult(False, error=f"Window function error: {str(e)}")
    
    def _execute_code(self, code: str, df_sample: pl.DataFrame) -> Any:
        """Execute generated code and return result"""
        try:
            exec_globals = {
                'df': df_sample.clone(),
                'pl': pl,
                'np': np,
                'datetime': datetime,
                'timedelta': timedelta
            }
            
            exec(code, exec_globals)
            return exec_globals.get('result', df_sample)
            
        except Exception as e:
            self.logger.error(f"Code execution error: {e}")
            return None

    def _extract_operation_params(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Extract operation parameters from state - delegate to base implementation"""
        return PeriodComparisonNode._extract_operation_params(self, state)


class PercentileNode(BaseOperationNode):
    """🆕 Percentile as individual LangGraph node"""
    
    def __init__(self):
        super().__init__("percentile")
    
    def validate_inputs(self, state: Dict[str, Any]) -> ValidationResult:
        stage2_result = state.get("stage2_result")
        if not stage2_result or not stage2_result.column:
            return ValidationResult(False, "Percentile requires column specification")
        return ValidationResult(True)
    
    def execute_operation(self, state: Dict[str, Any]) -> OperationResult:
        try:
            operation_params = self._extract_operation_params(state)
            
            generator = PercentileCodeGen()
            generated_code = generator.generate(operation_params)
            
            if not generated_code:
                return OperationResult(False, error="Failed to generate percentile code")
            
            df_sample = state.get("df_sample")
            result_data = self._execute_code(generated_code, df_sample)
            
            return OperationResult(
                success=True,
                data=result_data,
                generated_code=generated_code,
                metadata={"operation_type": "percentile"}
            )
            
        except Exception as e:
            return OperationResult(False, error=f"Percentile error: {str(e)}")
    
    def _execute_code(self, code: str, df_sample: pl.DataFrame) -> Any:
        """Execute generated code and return result"""
        try:
            exec_globals = {
                'df': df_sample.clone(),
                'pl': pl,
                'np': np,
                'datetime': datetime,
                'timedelta': timedelta
            }
            
            exec(code, exec_globals)
            return exec_globals.get('result', df_sample)
            
        except Exception as e:
            self.logger.error(f"Code execution error: {e}")
            return None

    def _extract_operation_params(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Extract operation parameters from state - delegate to base implementation"""
        return PeriodComparisonNode._extract_operation_params(self, state)


class CompositionPercentageNode(BaseOperationNode):
    """🆕 Composition Percentage as individual LangGraph node"""
    
    def __init__(self):
        super().__init__("composition_percentage")
    
    def validate_inputs(self, state: Dict[str, Any]) -> ValidationResult:
        return ValidationResult(True)  # No special requirements
    
    def execute_operation(self, state: Dict[str, Any]) -> OperationResult:
        try:
            operation_params = self._extract_operation_params(state)
            
            generator = CompositionPercentageCodeGen()
            generated_code = generator.generate(operation_params)
            
            if not generated_code:
                return OperationResult(False, error="Failed to generate composition percentage code")
            
            df_sample = state.get("df_sample")
            result_data = self._execute_code(generated_code, df_sample)
            
            return OperationResult(
                success=True,
                data=result_data,
                generated_code=generated_code,
                metadata={"operation_type": "composition_percentage"}
            )
            
        except Exception as e:
            return OperationResult(False, error=f"Composition percentage error: {str(e)}")
    
    def _execute_code(self, code: str, df_sample: pl.DataFrame) -> Any:
        """Execute generated code and return result"""
        try:
            # Add original_total for percentage calculation
            exec_globals = {
                'df': df_sample.clone(),
                'original_total': len(df_sample),
                'pl': pl,
                'np': np,
                'datetime': datetime,
                'timedelta': timedelta
            }
            
            exec(code, exec_globals)
            return exec_globals.get('result', df_sample)
            
        except Exception as e:
            self.logger.error(f"Code execution error: {e}")
            return None

    def _extract_operation_params(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Extract operation parameters from state - delegate to base implementation"""
        return PeriodComparisonNode._extract_operation_params(self, state)


class PivotNode(BaseOperationNode):
    """🆕 Pivot as individual LangGraph node"""
    
    def __init__(self):
        super().__init__("pivot")
    
    def validate_inputs(self, state: Dict[str, Any]) -> ValidationResult:
        stage2_result = state.get("stage2_result")
        if not stage2_result or not stage2_result.index:
            return ValidationResult(False, "Pivot requires index specification")
        return ValidationResult(True)
    
    def execute_operation(self, state: Dict[str, Any]) -> OperationResult:
        try:
            operation_params = self._extract_operation_params(state)
            generator = PivotCodeGen()
            generated_code = generator.generate(operation_params)
            
            if not generated_code:
                return OperationResult(False, error="Failed to generate pivot code")
            
            df_sample = state.get("df_sample")
            result_data = self._execute_code(generated_code, df_sample)
            
            return OperationResult(
                success=True,
                data=result_data,
                generated_code=generated_code,
                metadata={"operation_type": "pivot"}
            )
            
        except Exception as e:
            return OperationResult(False, error=f"Pivot error: {str(e)}")
    
    def _execute_code(self, code: str, df_sample: pl.DataFrame) -> Any:
        """Execute generated code and return result"""
        try:
            exec_globals = {
                'df': df_sample.clone(),
                'pl': pl,
                'np': np
            }
            
            exec(code, exec_globals)
            return exec_globals.get('result', df_sample)
            
        except Exception as e:
            self.logger.error(f"Code execution error: {e}")
            return None

    def _extract_operation_params(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Extract operation parameters from state - delegate to base implementation"""
        return PeriodComparisonNode._extract_operation_params(self, state)


# Create registry of all operation nodes
OPERATION_NODES = {
    "period_comparison": PeriodComparisonNode,
    "time_series": TimeSeriesNode,
    "ranking": RankingNode,
    "breakdown": BreakdownNode,
    "grouped_aggregation": GroupedAggregationNode,
    "window_function": WindowFunctionNode,
    "percentile": PercentileNode,
    "composition_percentage": CompositionPercentageNode,
    "pivot": PivotNode,
    # Additional nodes can be added here...
}


# ============================================================================
# OPERATION ROUTER NODE - INTELLIGENT ROUTING
# ============================================================================

class RoutingPlan:
    """Routing plan data structure"""
    def __init__(self, strategy: str, operation_queue: List[str], combination_strategy: str, 
                 operation_analysis: Dict, is_valid: bool, error: str = None):
        self.strategy = strategy
        self.operation_queue = operation_queue  
        self.combination_strategy = combination_strategy
        self.operation_analysis = operation_analysis
        self.is_valid = is_valid
        self.error = error
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "operation_queue": self.operation_queue,
            "combination_strategy": self.combination_strategy,
            "operation_analysis": self.operation_analysis,
            "is_valid": self.is_valid,
            "error": self.error
        }


class OperationRouterNode(BaseOperationNode):
    """
    🆕 Intelligent router that determines execution strategy for operations
    
    Handles:
    - Single operation routing  
    - Multi-operation dependencies
    - Parallel vs sequential execution
    - Operation parameter distribution
    """
    
    def __init__(self):
        super().__init__("operation_router")
        
    @traceable(name="operation_router_node")
    def execute_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Main router execution with LangGraph integration"""
        start_time = time.time()
        
        try:
            self.logger.info("[ROUTER_NODE] Starting operation routing...")
            
            # Extract Stage 2 results
            stage2_result = state.get("stage2_result")
            if not stage2_result or not stage2_result.operations:
                return self._create_error_state(state, "No operations to route", start_time)
            
            # Create routing plan
            routing_plan = self._create_routing_plan(stage2_result, state)
            
            # Validate routing plan
            if not routing_plan.is_valid:
                return self._create_error_state(state, f"Invalid routing plan: {routing_plan.error}", start_time)
            
            self.logger.info(f"[ROUTER_NODE] ✅ Routing plan created: {routing_plan.strategy}")
            self.logger.info(f"[ROUTER_NODE] Operations queue: {routing_plan.operation_queue}")
            
            return {
                **state,
                "operation_routing_plan": routing_plan.to_dict(),
                "operation_queue": routing_plan.operation_queue,
                "combination_strategy": routing_plan.combination_strategy,
                "operation_status": {op: "pending" for op in routing_plan.operation_queue},
                "operation_results": {},
                "operation_errors": {},
                "current_node": "router_completed",
                "node_execution_times": {
                    **state.get("node_execution_times", {}),
                    "operation_router": time.time() - start_time
                }
            }
            
        except Exception as e:
            return self._create_error_state(state, f"Router error: {str(e)}", start_time)
    
    def validate_inputs(self, state: Dict[str, Any]) -> ValidationResult:
        """Validate router inputs"""
        stage2_result = state.get("stage2_result")
        if not stage2_result:
            return ValidationResult(False, "Missing Stage 2 result")
        if not stage2_result.operations:
            return ValidationResult(False, "No operations to route")
        return ValidationResult(True)
    
    def execute_operation(self, state: Dict[str, Any]) -> OperationResult:
        """This method is not used for router - uses execute_node directly"""
        return OperationResult(True, data="Router executed via execute_node")
    
    def _create_routing_plan(self, stage2_result, state: Dict[str, Any]) -> RoutingPlan:
        """Create intelligent routing plan for operations"""
        operations = stage2_result.operations
        
        if not operations:
            return RoutingPlan(
                strategy="none",
                operation_queue=[],
                combination_strategy="none",
                operation_analysis={},
                is_valid=False,
                error="No operations detected"
            )
        
        # Single operation - simple routing
        if len(operations) == 1:
            operation = operations[0]
            return RoutingPlan(
                strategy="single",
                operation_queue=[operation],
                combination_strategy="none",
                operation_analysis={operation: self._analyze_operation(operation, state)},
                is_valid=True
            )
        
        # Multiple operations - analyze dependencies
        operation_analysis = {op: self._analyze_operation(op, state) for op in operations}
        
        # Check for dependencies
        has_dependencies = any(
            self._find_dependencies(op, operations) 
            for op in operations
        )
        
        if has_dependencies:
            # Sequential execution required
            sorted_ops = self._sort_by_dependencies(operations)
            return RoutingPlan(
                strategy="sequential",
                operation_queue=sorted_ops,
                combination_strategy="sequential_merge",
                operation_analysis=operation_analysis,
                is_valid=True
            )
        else:
            # Check if all can run in parallel
            all_parallel = all(self._can_run_parallel(op, operations) for op in operations)
            
            if all_parallel:
                # Parallel execution
                return RoutingPlan(
                    strategy="parallel",
                    operation_queue=operations,
                    combination_strategy="parallel_merge",
                    operation_analysis=operation_analysis,
                    is_valid=True
                )
            else:
                # Mixed - use sequential for safety
                return RoutingPlan(
                    strategy="sequential",
                    operation_queue=operations,
                    combination_strategy="sequential_merge",
                    operation_analysis=operation_analysis,
                    is_valid=True
                )
    
    def _analyze_operation(self, operation: str, state: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze individual operation requirements"""
        return {
            "complexity": self._estimate_complexity(operation),
            "dependencies": self._find_dependencies(operation, []),
            "can_parallel": self._can_run_parallel(operation, []),
            "estimated_time": self._estimate_execution_time(operation)
        }
    
    def _sort_by_dependencies(self, operations: List[str]) -> List[str]:
        """Sort operations by their dependencies"""
        # Simple topological sort for operation dependencies
        sorted_ops = []
        remaining = set(operations)
        
        while remaining:
            # Find operations with no remaining dependencies
            ready = [
                op for op in remaining 
                if not any(dep in remaining for dep in self._find_dependencies(op, list(remaining)))
            ]
            
            if not ready:
                # Circular dependency or unable to resolve - use original order
                sorted_ops.extend(sorted(remaining))
                break
            
            # Add ready operations in order
            for op in sorted(ready):
                sorted_ops.append(op)
                remaining.remove(op)
        
        return sorted_ops
    
    def _estimate_complexity(self, operation: str) -> str:
        """Estimate operation complexity"""
        complexity_map = {
            "grouped_aggregation": "low",
            "breakdown": "low", 
            "composition_percentage": "low",
            "ranking": "medium",
            "time_series": "medium",
            "period_comparison": "medium",
            "window_function": "high",
            "statistical_test": "high",
            "pivot": "high"
        }
        return complexity_map.get(operation, "medium")
    
    def _find_dependencies(self, operation: str, all_operations: List[str]) -> List[str]:
        """Find dependencies between operations"""
        # Define operation dependencies
        dependencies = {
            "ranking": ["grouped_aggregation"],  # Ranking often needs aggregation first
            "window_function": ["time_series"],  # Window functions on time series
            "statistical_test": ["breakdown", "grouped_aggregation"]  # Stats on grouped data
        }
        
        op_deps = dependencies.get(operation, [])
        return [dep for dep in op_deps if dep in all_operations]
    
    def _can_run_parallel(self, operation: str, all_operations: List[str]) -> bool:
        """Check if specific operation can run in parallel with others"""
        # Operations that must run sequentially
        sequential_ops = {"ranking", "window_function", "pivot"}
        return operation not in sequential_ops
    
    def _estimate_execution_time(self, operation: str) -> float:
        """Estimate execution time for operation (in seconds)"""
        time_estimates = {
            "grouped_aggregation": 0.5,
            "breakdown": 0.8,
            "composition_percentage": 0.3,
            "ranking": 1.2,
            "time_series": 1.5,
            "period_comparison": 2.0,
            "window_function": 3.0,
            "statistical_test": 2.5,
            "pivot": 2.2
        }
        return time_estimates.get(operation, 1.0)


# ============================================================================
# RESULTS COMBINER NODE - INTELLIGENT MERGING
# ============================================================================

class CombinationResult:
    """Result from combining multiple operation outputs"""
    def __init__(self, success: bool, data: Any = None, error: str = None, 
                 combination_type: str = None, source_operations: List[str] = None):
        self.success = success
        self.data = data
        self.error = error
        self.combination_type = combination_type
        self.source_operations = source_operations or []


class ResultsCombinerNode(BaseOperationNode):
    """
    🆕 Intelligent results combiner that merges outputs from multiple operation nodes
    
    Handles:
    - Sequential result combination
    - Parallel result merging  
    - Multi-query response formatting
    - Conflict resolution
    """
    
    def __init__(self):
        super().__init__("results_combiner")
    
    @traceable(name="results_combiner_node")
    def execute_node(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Main combiner execution with LangGraph integration"""
        start_time = time.time()
        
        try:
            self.logger.info("[COMBINER_NODE] Starting results combination...")
            
            # Validate inputs
            operation_results = state.get("operation_results", {})
            combination_strategy = state.get("combination_strategy", "sequential_merge")
            
            if not operation_results:
                return self._create_error_state(state, "No operation results to combine", start_time)
            
            self.logger.info(f"[COMBINER] Strategy: {combination_strategy}")
            self.logger.info(f"[COMBINER] Results to combine: {list(operation_results.keys())}")
            
            # Execute combination strategy
            if combination_strategy == "none":
                combined_result = self._handle_single_result(operation_results)
            elif combination_strategy == "sequential_merge":
                combined_result = self._combine_sequential_results(operation_results, state)
            elif combination_strategy == "parallel_merge":
                combined_result = self._combine_parallel_results(operation_results, state)
            else:
                return self._create_error_state(state, f"Unknown combination strategy: {combination_strategy}", start_time)
            
            if not combined_result.success:
                return self._create_error_state(state, combined_result.error, start_time)
            
            self.logger.info(f"[COMBINER_NODE] ✅ Success - Combined {len(operation_results)} results")
            
            # Create final NLToPythonResult
            final_result = self._create_final_result(combined_result, state)
            
            return {
                **state,
                "final_combined_result": combined_result.data,
                "final_result": final_result,
                "combination_errors": [],
                "current_node": "combiner_completed",
                "node_execution_times": {
                    **state.get("node_execution_times", {}),
                    "results_combiner": time.time() - start_time
                }
            }
            
        except Exception as e:
            return self._create_error_state(state, f"Combiner error: {str(e)}", start_time)
    
    def validate_inputs(self, state: Dict[str, Any]) -> ValidationResult:
        """Validate combiner inputs"""
        operation_results = state.get("operation_results", {})
        if not operation_results:
            return ValidationResult(False, "No operation results to combine")
        return ValidationResult(True)
    
    def execute_operation(self, state: Dict[str, Any]) -> OperationResult:
        """This method is not used for combiner - uses execute_node directly"""
        return OperationResult(True, data="Combiner executed via execute_node")
    
    def _handle_single_result(self, operation_results: Dict[str, Any]) -> CombinationResult:
        """Handle single operation result (no combination needed)"""
        operation_name = list(operation_results.keys())[0]
        result_obj = operation_results[operation_name]  # This is an OperationResult
        
        self.logger.info(f"[COMBINER] Single result from {operation_name}")
        
        # ✅ FIX: Extract the actual data and code from OperationResult
        combined_data = {
            "combined_code": getattr(result_obj, 'generated_code', ''),
            "operation_data": getattr(result_obj, 'data', None),
            "metadata": getattr(result_obj, 'metadata', {}),
            "success": getattr(result_obj, 'success', True),
            "error": getattr(result_obj, 'error', None)
        }
        
        return CombinationResult(
            success=True,
            data=combined_data,  # ✅ Now it's a proper dictionary with .get() method support
            combination_type="single",
            source_operations=[operation_name]
        )
    
    def _combine_sequential_results(self, operation_results: Dict[str, Any], state: Dict[str, Any]) -> CombinationResult:
        """Combine results from sequential operations"""
        
        # Get execution order from routing plan
        routing_plan = state.get("operation_routing_plan", {})
        operation_queue = routing_plan.get("operation_queue", list(operation_results.keys()))
        
        self.logger.info(f"[COMBINER] Sequential combination order: {operation_queue}")
        
        combined_data = []
        combined_code_parts = []
        operation_summaries = []
        
        for operation in operation_queue:
            if operation not in operation_results:
                self.logger.warning(f"[COMBINER] Missing result for operation: {operation}")
                continue
                
            result = operation_results[operation]
            
            # Extract data and code from operation result
            if hasattr(result, 'generated_code'):
                combined_code_parts.append(f"# === {operation.upper()} OPERATION ===")
                combined_code_parts.append(result.generated_code)
                combined_code_parts.append("")
                
            if hasattr(result, 'data') and result.data is not None:
                combined_data.append({
                    "operation": operation,
                    "data": result.data,
                    "explanation": getattr(result, 'explanation', f"{operation} results")
                })
            
            # Create operation summary
            operation_summaries.append({
                "operation": operation,
                "success": True,
                "rows": len(result.data) if hasattr(result, 'data') and hasattr(result.data, '__len__') else "N/A",
                "description": self._get_operation_description(operation)
            })
        
        # Combine all code parts
        combined_code = "\n".join(combined_code_parts)
        
        self.logger.info(f"[COMBINER] Combined {len(combined_data)} sequential results")
        
        return CombinationResult(
            success=True,
            data={
                "combined_results": combined_data,
                "combined_code": combined_code,
                "operation_summaries": operation_summaries,
                "combination_type": "sequential"
            },
            combination_type="sequential",
            source_operations=operation_queue
        )
    
    def _combine_parallel_results(self, operation_results: Dict[str, Any], state: Dict[str, Any]) -> CombinationResult:
        """Combine results from parallel operations"""
        
        self.logger.info(f"[COMBINER] Parallel combination of {len(operation_results)} results")
        
        # For parallel operations, create separate sections for each result
        parallel_sections = []
        combined_code_parts = []
        
        for operation, result in operation_results.items():
            
            # Create section for this operation
            section = {
                "operation": operation,
                "title": self._get_operation_title(operation),
                "data": result.data if hasattr(result, 'data') else result,
                "explanation": getattr(result, 'explanation', f"{operation} analysis results"),
                "chart_type": getattr(result, 'suggested_chart_type', 'table')
            }
            parallel_sections.append(section)
            
            # Add code section
            if hasattr(result, 'generated_code'):
                combined_code_parts.append(f"# === {operation.upper()} OPERATION ===")
                combined_code_parts.append(result.generated_code)
                combined_code_parts.append("")
        
        combined_code = "\n".join(combined_code_parts)
        
        self.logger.info(f"[COMBINER] Created {len(parallel_sections)} parallel sections")
        
        return CombinationResult(
            success=True,
            data={
                "parallel_sections": parallel_sections,
                "combined_code": combined_code,
                "section_count": len(parallel_sections),
                "combination_type": "parallel"
            },
            combination_type="parallel",
            source_operations=list(operation_results.keys())
        )
    
    def _create_final_result(self, combined_result: CombinationResult, state: Dict[str, Any]):
        """Create final NLToPythonResult from combined results"""
        
        # 🆕 Update dynamic schema if new columns were created
        schema_manager = state.get("schema_manager")
        source_id = state.get("source_id", "default")
        
        if schema_manager and combined_result.data:
            result_data = combined_result.data.get("combined_results", [])
            if result_data and len(result_data) > 0:
                # Get the last result DataFrame
                last_result = result_data[-1].get("data")
                if isinstance(last_result, pl.DataFrame):
                    operation_type = combined_result.source_operations[-1] if combined_result.source_operations else "unknown"
                    new_columns = schema_manager.update_from_result(
                        source_id, 
                        last_result, 
                        operation_type
                    )
                    if new_columns:
                        self.logger.info(f"[COMBINER] Updated schema with {len(new_columns)} new columns: {new_columns}")
        
        # Determine overall operation type
        source_ops = combined_result.source_operations
        if len(source_ops) == 1:
            operation_type = source_ops[0]
        else:
            operation_type = f"multi_operation_{combined_result.combination_type}"
        
        # Determine chart type
        if combined_result.combination_type == "parallel":
            chart_type = "multi_section"  # Special chart type for multiple sections
        else:
            chart_type = self._suggest_chart_type_for_combination(source_ops)
        
        # Get overall confidence (average from Stage 2)
        stage2_result = state.get("stage2_result")
        confidence = stage2_result.confidence if stage2_result else 0.8
        
        # Create explanation
        explanation = self._create_combination_explanation(combined_result, state)
        
        from models.schemas import NLToPythonResult
        
        return NLToPythonResult(
            original_query=state.get("query", ""),
            generated_code=combined_result.data.get("combined_code", ""),
            operation_type=operation_type,
            confidence=confidence,
            suggested_chart_type=chart_type,
            explanation=explanation,
            metadata={
                "combination_type": combined_result.combination_type,
                "source_operations": source_ops,
                "operation_count": len(source_ops)
            }
        )
    
    def _get_operation_description(self, operation: str) -> str:
        """Get human-readable description of operation"""
        descriptions = {
            "time_series": "Time-based trend analysis",
            "period_comparison": "Period-over-period comparison",
            "ranking": "Ranking and top/bottom analysis",
            "breakdown": "Distribution and breakdown analysis",
            "grouped_aggregation": "Grouped statistical aggregation",
            "composition_percentage": "Percentage composition analysis",
            "window_function": "Rolling/expanding window analysis"
        }
        return descriptions.get(operation, f"{operation} analysis")
    
    def _get_operation_title(self, operation: str) -> str:
        """Get display title for operation"""
        titles = {
            "time_series": "📈 Time Series Analysis",
            "period_comparison": "📊 Period Comparison",
            "ranking": "🏆 Ranking Analysis", 
            "breakdown": "🍰 Distribution Breakdown",
            "grouped_aggregation": "📋 Aggregated Summary",
            "composition_percentage": "📊 Percentage Analysis",
            "window_function": "📊 Window Function Analysis"
        }
        return titles.get(operation, f"📊 {operation.title()} Analysis")
    
    def _suggest_chart_type_for_combination(self, operations: List[str]) -> str:
        """Suggest chart type for combined operations"""
        if "time_series" in operations or "period_comparison" in operations:
            return "line"
        elif "ranking" in operations:
            return "bar"
        elif "breakdown" in operations:
            return "pie"
        else:
            return "table"
    
    def _create_combination_explanation(self, combined_result: CombinationResult, state: Dict[str, Any]) -> str:
        """Create explanation for combined results"""
        ops = combined_result.source_operations
        
        if len(ops) == 1:
            return f"Executed {ops[0]} analysis successfully."
        elif combined_result.combination_type == "sequential":
            return f"Executed {len(ops)} operations sequentially: {' → '.join(ops)}"
        else:
            return f"Executed {len(ops)} operations in parallel: {', '.join(ops)}"
