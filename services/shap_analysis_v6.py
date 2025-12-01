"""
Agentic SHAP Analysis Service - V6 (NL_to_python Pattern)

Architecture:
  Stage 1: Strict enum-based column selection (Pydantic Literal - no hallucinations)
  Stage 1.5: Value grounding (fuzzy matching against actual data)
  Stage 2: Operation planning with sub-intent detection
  Validator: Plan validation (LLM checks if plan addresses query)
  Stage 3.1: Code generation (operation registry pattern)
  Stage 3.2: Code execution (execute on actual CSV data)
  Validator: Results validation (LLM checks if results answer query)
  Stage 3.3: Insight formatting (format with actual numbers)

Key Features:
- NO HARDCODINGS: Works with any dataset, any columns, any charts
- 35+ Sub-intents covering all analysis types
- Retry mechanism with LLM validation
- Integration with UniversalCausalAnalyzer
- All claims backed by actual computation

Author: Based on NL_to_python V5 architecture
Date: November 2025
"""

import re
import json
import logging
import os
import traceback
from typing import Dict, List, Any, Optional, Tuple, Literal, Union
from datetime import datetime, timedelta
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# LangGraph
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.memory import MemorySaver

# Pydantic for structured outputs
try:
    from pydantic import BaseModel, Field, create_model
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    logging.warning("Pydantic not available. Install with: pip install pydantic")

# Import existing services
from services.fuzzy_column_matcher import FuzzyColumnMatcher
from services.tableau_aggregation_hints import get_hints_manager
from services.period_extraction_service import PeriodExtractionService
from services.visualization_service import IntelligentVisualizationService
from services.outlier_detection_service import OutlierDetectionService
from services.temporal_anomaly_detection_service import TemporalAnomalyDetectionService
from master_logger import setup_module_logger


# ============================================================================
# SUB-INTENT REGISTRY (Complete Taxonomy)
# ============================================================================

# ============================================================================
# SUB_INTENTS: 7-Layer Explanation Framework
# ============================================================================
# This module provides structured explanations for "WHY" questions using:
# 1. Causal drivers (what truly causes changes)
# 2. Segment breakdown (where changes happened)
# 3. Positive/negative contributors (who drove it)
# 4. Temporal patterns (when it started)
# 5. Trend summary (what happened)
# 
# Other analysis types (ranking, percentiles, pivot, etc.) handled by NL_to_python.py
# ============================================================================

SUB_INTENTS = {
    # ============= LAYER 4: CAUSAL DRIVERS =============
    "general_drivers": {
        "description": "Find causal factors that affect target (uses EconML + SHAP)",
        "examples": ["what affects tickets", "find drivers of sales", "why spike"],
        "required": ["target_metric"],
        "operation": "causal_analysis",
        "layer": "causal_drivers"
    },
    
    # ============= SPECIFIC FEATURE IMPACT =============
    "specific_feature_impact": {
        "description": "How a SPECIFIC column affects target metric (targeted analysis)",
        "examples": ["how account_manager affects tickets", "impact of region on sales", "how status affects revenue"],
        "required": ["feature_columns", "target_metric"],
        "operation": "feature_breakdown",
        "layer": "specific_impact",
        "note": "Use when user mentions specific column(s) in query. More efficient than general_drivers."
    },
    
    # ============= MULTIPLE FEATURE INTERACTION =============
    "multiple_feature_impact": {
        "description": "How multiple features interact to affect target (interaction effects)",
        "examples": ["how region and product affect sales together", "combined impact of status and priority on tickets"],
        "required": ["feature_columns", "target_metric"],
        "operation": "multi_feature_breakdown",
        "layer": "interaction_effects",
        "note": "Use when user asks about 2+ specific columns affecting target together"
    },
    
    # ============= CORRELATION ANALYSIS =============
    "correlation_analysis": {
        "description": "Numeric correlations between columns (pre-causal step)",
        "examples": ["correlation between age and tickets", "correlation matrix", "which metrics correlate with sales"],
        "required": ["target_metric"],
        "operation": "correlation_matrix",
        "layer": "correlation",
        "note": "Used as exploratory step before causal analysis or for numeric relationships"
    },
    
    # ============= OUTLIER/ANOMALY DETECTION =============
    "outlier_detection": {
        "description": "Identify anomalous data points using statistical and ML methods (Modified Z-Score, Isolation Forest)",
        "examples": ["find outliers", "detect anomalies", "identify unusual cases", "which records are outliers"],
        "required": [],
        "operation": "outlier_detection",
        "layer": "anomaly_detection"
    },
    
    # ============= TEMPORAL ANOMALY DETECTION =============
    "temporal_anomaly_detection": {
        "description": "Auto-detect anomalous time periods in trends (not individual records)",
        "examples": ["find anomalies in monthly trend", "detect unusual periods", "which months are anomalous", "anomalies in the time series"],
        "required": ["date_column", "target_metric"],
        "operation": "temporal_anomaly_detection",
        "layer": "anomaly_detection"
    },
    
    # ============= LAYER 2: SEGMENT BREAKDOWN =============
    "categorical_breakdown": {
        "description": "Breakdown metric by categories (where changes happened)",
        "examples": ["breakdown by status", "tickets by priority", "sales by region"],
        "required": ["categorical_column", "target_metric"],
        "operation": "grouped_aggregation",
        "layer": "segment_breakdown"
    },
    
    # ============= LAYER 3: POSITIVE/NEGATIVE CONTRIBUTORS =============
    "contribution_analysis": {
        "description": "Which categories contributed most/least to changes",
        "examples": ["which regions drove growth", "top contributors", "negative impacts"],
        "required": ["categorical_column", "target_metric"],
        "operation": "contribution_pct",
        "layer": "contributors"
    },
    
    # ============= LAYER 5: TEMPORAL PATTERN =============
    "period_over_period": {
        "description": "Compare periods to identify when changes started (MoM, QoQ, YoY)",
        "examples": ["month over month", "Q3 vs Q4", "year over year growth"],
        "required": ["date_column", "target_metric", "granularity"],
        "operation": "period_comparison",
        "layer": "temporal_pattern"
    },
    
    # ============= LAYER 1: TREND SUMMARY =============
    "temporal_trend": {
        "description": "Summarize trends over time (what happened)",
        "examples": ["monthly trends", "sales over time", "ticket trends"],
        "required": ["date_column", "target_metric"],
        "operation": "temporal_aggregation",
        "layer": "trend_summary"
    }
}

# ============================================================================
# NOTE: Other operations are handled by NL_to_python.py
# ============================================================================
# The following are NOT in this module - routed to services/NL_to_python.py:
#
# - Ranking & Top/Bottom: NL_to_python's "ranking" operation
# - Percentiles: NL_to_python's "percentile" operation
# - Period comparison (standalone): NL_to_python's "period_comparison"
# - Breakdown (non-causal): NL_to_python's "breakdown" operation
# - Cumulative/Running totals: NL_to_python's "window_function"
# - Cross-tabulation: NL_to_python's "pivot" operation
# - Statistical tests: NL_to_python's "statistical_test" operation
# - Date arithmetic: NL_to_python's "date_arithmetic" operation
# - Binning/Buckets: NL_to_python's "binning" operation
# - Growth rate: NL_to_python's "period_comparison" with temporal_change
# - Seasonal analysis: NL_to_python + external libs
# - Basic aggregations: NL_to_python's "grouped_aggregation"
# - Correlations: NL_to_python's correlation matrix
#
# Separation ensures:
# - shap_analysis_v6.py: Causal explanations ("WHY" questions)
# - NL_to_python.py: Data manipulation ("WHAT/HOW/SHOW" questions)
# ============================================================================


# ============================================================================
# RETRY MANAGER
# ============================================================================

class AnalysisRetryManager:
    """
    Manages retry logic when results don't match query
    Tracks errors and suggests corrections
    
    Serializable to dict for msgpack compatibility with LangGraph checkpointing
    """
    
    def __init__(self, max_retries=2):
        self.max_retries = max_retries
        self.retry_history = []
    
    def should_retry(self, attempt: int) -> bool:
        """Check if we should retry"""
        return attempt < self.max_retries
    
    def record_failure(self, attempt: int, reason: str, stage1: Dict, stage2: Dict):
        """Record failure reason for learning"""
        self.retry_history.append({
            "attempt": attempt,
            "reason": reason,
            "stage1": stage1,
            "stage2": stage2,
            "timestamp": datetime.now().isoformat()
        })
    
    def get_correction_suggestions(self) -> List[str]:
        """Generate correction suggestions based on previous failures"""
        if not self.retry_history:
            return []
        
        last_failure = self.retry_history[-1]
        reason = last_failure['reason']
        suggestions = []
        
        # Analyze failure reason and suggest corrections
        if "mentioned columns not in plan" in reason.lower():
            suggestions.append("Ensure mentioned_features are included in group_by_columns")
            suggestions.append("Use 'specific_feature_impact' sub-intent for specific column queries")
        
        if "empty results" in reason.lower():
            suggestions.append("Verify filter values match actual data values")
            suggestions.append("Check column names for exact case-sensitive matches")
        
        if "wrong analysis type" in reason.lower():
            suggestions.append("Re-evaluate if query is 'specific' vs 'general'")
            suggestions.append("Match sub-intent to query pattern more carefully")
        
        if "results don't answer query" in reason.lower():
            suggestions.append("User asked about specific columns - ensure they appear in results")
            suggestions.append("Check if analysis type matches what user requested")
        
        return suggestions
    
    def to_dict(self) -> Dict:
        """
        Convert to dict for msgpack serialization
        Required for LangGraph checkpointing
        """
        return {
            "max_retries": self.max_retries,
            "retry_history": self.retry_history
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'AnalysisRetryManager':
        """
        Create instance from dict (deserialization)
        """
        manager = cls(max_retries=data.get("max_retries", 2))
        manager.retry_history = data.get("retry_history", [])
        return manager


# ============================================================================
# CODE GENERATORS (Operation Registry Pattern)
# ============================================================================

class CodeGeneratorRegistry:
    """
    Registry of code generators for each operation
    Similar to NL_to_python's CODE_GENERATORS
    """
    
    @staticmethod
    def generate_categorical_breakdown(params: Dict) -> str:
        """Generate code for categorical breakdown"""
        group_cols = params['group_columns']
        agg_col = params.get('agg_column')
        agg_funcs = params.get('agg_functions', ['count', 'sum', 'mean'])
        is_count = params.get('is_count_metric', False)
        
        if is_count:
            # Count by groups
            code = f"""
# Breakdown by {group_cols}
result = df.groupby({group_cols}).size().reset_index(name='count')
result['percentage'] = (result['count'] / result['count'].sum() * 100).round(2)
result = result.sort_values('count', ascending=False)
result
"""
        else:
            # Aggregation by groups
            # Use list directly - pandas doesn't like nested dict renamer
            code = f"""
# Breakdown by {group_cols}
result = df.groupby({group_cols})['{agg_col}'].agg({agg_funcs}).reset_index()
result = result.sort_values('{agg_funcs[0]}', ascending=False)
result
"""
        return code
    
    @staticmethod
    def generate_period_over_period(params: Dict) -> str:
        """Generate code for period-over-period comparison"""
        date_col = params['date_column']
        metric = params['target_metric']
        granularity = params.get('granularity', 'M')
        comparison_type = params.get('comparison_type', 'mom')
        is_count = params.get('is_count_metric', False)
        
        # Map comparison types to pandas frequency codes
        comparison_freq_map = {'mom': 'M', 'qoq': 'Q', 'yoy': 'Y', 'wow': 'W', 'dod': 'D'}
        
        # Map human-readable granularity to pandas frequency codes (universal, not dataset-specific)
        granularity_freq_map = {
            'month': 'M', 'monthly': 'M',
            'quarter': 'Q', 'quarterly': 'Q', 
            'year': 'Y', 'yearly': 'Y', 'annual': 'Y', 'annually': 'Y',
            'week': 'W', 'weekly': 'W',
            'day': 'D', 'daily': 'D'
        }
        
        # Get frequency: first try comparison_type, then map granularity, finally use as-is
        freq = comparison_freq_map.get(comparison_type)
        if not freq and isinstance(granularity, str):
            freq = granularity_freq_map.get(granularity.lower(), granularity)
        else:
            freq = freq or granularity
        
        if is_count:
            # Count over time
            code = f"""
# Period-over-period comparison (count)
df['{date_col}'] = pd.to_datetime(df['{date_col}'], errors='coerce')
df_grouped = df.groupby(pd.Grouper(key='{date_col}', freq='{freq}')).size()

# Calculate change
result = pd.DataFrame({{
    'period': df_grouped.index,
    'current': df_grouped.values,
    'previous': df_grouped.shift(1).values,
    'change': (df_grouped - df_grouped.shift(1)).values,
    'pct_change': ((df_grouped - df_grouped.shift(1)) / df_grouped.shift(1) * 100).values
}})
result = result.dropna()
result
"""
        else:
            # Metric over time
            code = f"""
# Period-over-period comparison
df['{date_col}'] = pd.to_datetime(df['{date_col}'], errors='coerce')
df_grouped = df.groupby(pd.Grouper(key='{date_col}', freq='{freq}'))['{metric}'].sum()

# Calculate change
result = pd.DataFrame({{
    'period': df_grouped.index,
    'current': df_grouped.values,
    'previous': df_grouped.shift(1).values,
    'change': (df_grouped - df_grouped.shift(1)).values,
    'pct_change': ((df_grouped - df_grouped.shift(1)) / df_grouped.shift(1) * 100).values
}})
result = result.dropna()
result
"""
        return code
    
    @staticmethod
    def generate_contribution_analysis(params: Dict) -> str:
        """Generate code for contribution analysis"""
        category_col = params['categorical_column']
        metric = params['target_metric']
        is_count = params.get('is_count_metric', False)
        
        if is_count:
            code = f"""
# Contribution analysis (count)
result = df.groupby('{category_col}').size().reset_index(name='count')
result['percentage'] = (result['count'] / result['count'].sum() * 100).round(2)
result['cumulative_pct'] = result['percentage'].cumsum()
result = result.sort_values('percentage', ascending=False)
result
"""
        else:
            code = f"""
# Contribution analysis
result = df.groupby('{category_col}')['{metric}'].sum().reset_index(name='total')
result['percentage'] = (result['total'] / result['total'].sum() * 100).round(2)
result['cumulative_pct'] = result['percentage'].cumsum()
result = result.sort_values('percentage', ascending=False)
result
"""
        return code
    
    @staticmethod
    def generate_general_drivers(params: Dict) -> str:
        """
        Generate code for general drivers analysis
        Uses ALL top drivers from causal analysis to show actual numbers
        """
        top_drivers = params.get("top_drivers", [])
        agg_column = params.get("agg_column")
        is_count_metric = params.get("is_count_metric", False)
        
        if not top_drivers:
            return ""
        
        # Generate analysis for ALL top drivers
        code_blocks = []
        
        for idx, driver_info in enumerate(top_drivers):
            # driver_info is (driver_name, score) tuple
            driver_name = driver_info[0] if isinstance(driver_info, (tuple, list)) else driver_info
            
            if is_count_metric:
                code_block = f"""
# Driver #{idx+1}: {driver_name}
driver_{idx+1}_result = df.groupby('{driver_name}').size().reset_index(name='count')
driver_{idx+1}_result['percentage'] = (driver_{idx+1}_result['count'] / driver_{idx+1}_result['count'].sum() * 100).round(2)
driver_{idx+1}_result['driver_name'] = '{driver_name}'
driver_{idx+1}_result = driver_{idx+1}_result.sort_values('count', ascending=False)
"""
            else:
                code_block = f"""
# Driver #{idx+1}: {driver_name}
driver_{idx+1}_result = df.groupby('{driver_name}')['{agg_column}'].agg(['mean', 'sum', 'count']).reset_index()
driver_{idx+1}_result.columns = ['{driver_name}', 'avg_{agg_column}', 'total_{agg_column}', 'count']
driver_{idx+1}_result['driver_name'] = '{driver_name}'
driver_{idx+1}_result = driver_{idx+1}_result.sort_values('avg_{agg_column}', ascending=False)
"""
            code_blocks.append(code_block)
        
        # Combine all driver analyses
        combined_code = "\n".join(code_blocks)
        
        # Collect all driver DataFrames into result list
        combined_code += """
# Collect all driver results with their DataFrames
result = []
"""
        
        # Add each driver result to the list
        for idx in range(len(top_drivers)):
            driver_name = top_drivers[idx][0] if isinstance(top_drivers[idx], (tuple, list)) else top_drivers[idx]
            combined_code += f"""
result.append({{'driver': '{driver_name}', 'data': driver_{idx+1}_result.to_dict('records')}})
"""
        
        return combined_code
    
    @staticmethod
    def generate_specific_feature_impact(params: Dict) -> str:
        """
        Generate code for specific feature impact analysis
        Shows how a specific column affects the target metric
        """
        feature_cols = params.get('feature_columns', [])
        agg_col = params.get('agg_column')
        is_count = params.get('is_count_metric', False)
        
        if not feature_cols:
            return ""
        
        # Handle single or multiple features
        if len(feature_cols) == 1:
            feature = feature_cols[0]
            if is_count:
                code = f"""
# Specific Feature Impact: {feature} → Count
result = df.groupby('{feature}').size().reset_index(name='count')
result['percentage'] = (result['count'] / result['count'].sum() * 100).round(2)
result = result.sort_values('count', ascending=False)

# Add impact score (normalized)
result['impact_score'] = (result['count'] / result['count'].max() * 100).round(2)
result
"""
            else:
                code = f"""
# Specific Feature Impact: {feature} → {agg_col}
result = df.groupby('{feature}')['{agg_col}'].agg(['mean', 'sum', 'count', 'std']).reset_index()
result.columns = ['{feature}', 'avg_{agg_col}', 'total_{agg_col}', 'count', 'std_{agg_col}']
result = result.sort_values('avg_{agg_col}', ascending=False)

# Add impact score (normalized by mean)
result['impact_score'] = (result['avg_{agg_col}'] / result['avg_{agg_col}'].max() * 100).round(2)
result
"""
        else:
            # Multiple features - show each separately
            feature_list_str = "', '".join(feature_cols)
            if is_count:
                code = f"""
# Specific Feature Impact (Multiple): {feature_list_str}
result = {{}}
for feature in {feature_cols}:
    feature_result = df.groupby(feature).size().reset_index(name='count')
    feature_result['percentage'] = (feature_result['count'] / feature_result['count'].sum() * 100).round(2)
    feature_result['impact_score'] = (feature_result['count'] / feature_result['count'].max() * 100).round(2)
    feature_result = feature_result.sort_values('count', ascending=False)
    result[feature] = feature_result.to_dict('records')
result
"""
            else:
                code = f"""
# Specific Feature Impact (Multiple): {feature_list_str}
result = {{}}
for feature in {feature_cols}:
    feature_result = df.groupby(feature)['{agg_col}'].agg(['mean', 'sum', 'count']).reset_index()
    feature_result.columns = [feature, 'avg_{agg_col}', 'total_{agg_col}', 'count']
    feature_result['impact_score'] = (feature_result['avg_{agg_col}'] / feature_result['avg_{agg_col}'].max() * 100).round(2)
    feature_result = feature_result.sort_values('avg_{agg_col}', ascending=False)
    result[feature] = feature_result.to_dict('records')
result
"""
        return code
    
    @staticmethod
    def generate_multiple_feature_impact(params: Dict) -> str:
        """
        Generate code for multiple feature interaction analysis
        Shows how multiple features interact to affect target
        """
        feature_cols = params.get('feature_columns', [])
        agg_col = params.get('agg_column')
        is_count = params.get('is_count_metric', False)
        
        if len(feature_cols) < 2:
            # Fallback to single feature
            return CodeGeneratorRegistry.generate_specific_feature_impact(params)
        
        # Limit to top 2 features for interaction (to avoid combinatorial explosion)
        features = feature_cols[:2]
        
        if is_count:
            code = f"""
# Multiple Feature Interaction: {features[0]} × {features[1]} → Count
result = df.groupby({features}).size().reset_index(name='count')
result['percentage'] = (result['count'] / result['count'].sum() * 100).round(2)
result = result.sort_values('count', ascending=False)

# Show top 10 combinations
result_top = result.head(10)
result_top
"""
        else:
            code = f"""
# Multiple Feature Interaction: {features[0]} × {features[1]} → {agg_col}
result = df.groupby({features})['{agg_col}'].agg(['mean', 'sum', 'count']).reset_index()
result.columns = {features} + ['avg_{agg_col}', 'total_{agg_col}', 'count']
result = result.sort_values('avg_{agg_col}', ascending=False)

# Show top 10 combinations
result_top = result.head(10)
result_top
"""
        return code
    
    @staticmethod
    def generate_correlation_analysis(params: Dict) -> str:
        """
        Generate code for correlation analysis
        Shows numeric correlations between columns
        
        Requires:
        - target_metric: Column whose correlations to compute
        - numeric_columns: Optional list of specific columns to correlate with
        """
        target_metric = params.get('target_metric')
        numeric_cols = params.get('numeric_columns', [])
        
        # VALIDATION: target_metric is required for correlation analysis
        if not target_metric or target_metric == 'None':
            raise ValueError(
                "correlation_analysis requires a valid 'target_metric' parameter. "
                "Stage 2 must extract the target column from the query (e.g., 'age_in_hours' "
                "from 'correlation between age_in_hours and other metrics')."
            )
        
        if numeric_cols:
            # Specific numeric columns provided
            cols_list = [target_metric] + [c for c in numeric_cols if c != target_metric]
            code = f"""
# Correlation Analysis: {target_metric} with specific columns
numeric_cols = {cols_list}

# Validate target metric exists
if '{target_metric}' not in df.columns:
    raise ValueError(f"Target metric '{target_metric}' not found in dataframe columns")

correlation_data = df[numeric_cols].corr()

# Extract correlations with target metric
target_correlations = correlation_data['{target_metric}'].drop('{target_metric}')
result = target_correlations.sort_values(ascending=False).reset_index()
result.columns = ['column', 'correlation']
result['abs_correlation'] = result['correlation'].abs()
result = result.sort_values('abs_correlation', ascending=False)
result
"""
        else:
            # All numeric columns
            code = f"""
# Correlation Analysis: {target_metric} with all numeric columns
numeric_cols = df.select_dtypes(include=['int64', 'float64']).columns.tolist()

# Remove identifier columns (those with high cardinality)
numeric_cols = [col for col in numeric_cols if df[col].nunique() < len(df) * 0.9]

# Validate target metric
if '{target_metric}' not in df.columns:
    raise ValueError(f"Target metric '{target_metric}' not found in dataframe columns")

if '{target_metric}' not in numeric_cols:
    raise ValueError(f"Target metric '{target_metric}' is not a numeric column. Available numeric: {{numeric_cols[:10]}}")

if len(numeric_cols) > 1:
    correlation_matrix = df[numeric_cols].corr()
    
    # Extract correlations with target metric
    if '{target_metric}' in correlation_matrix.columns:
        target_correlations = correlation_matrix['{target_metric}'].drop('{target_metric}')
        result = target_correlations.sort_values(ascending=False).reset_index()
        result.columns = ['column', 'correlation']
        result['abs_correlation'] = result['correlation'].abs()
        result = result.sort_values('abs_correlation', ascending=False)
    else:
        raise ValueError(f"Target metric '{target_metric}' not found in correlation matrix")
else:
    result = pd.DataFrame({{'message': ['Not enough numeric columns for correlation analysis']}})

result
"""
        return code
    
    @staticmethod
    def generate_temporal_trend(params: Dict) -> str:
        """
        Generate code for temporal trend analysis
        
        If query specifies granularity (weekly/monthly/quarterly) → show only that
        If query is general ("why spike?") → show MoM, QoQ, AND YoY all at once
        """
        date_column = params.get("date_column")
        agg_column = params.get("agg_column")
        is_count_metric = params.get("is_count_metric", False)
        granularity = params.get("granularity")  # None if not specified
        
        if not date_column:
            return ""
        
        # Determine which trends to show
        if granularity and granularity.lower() in ['week', 'weekly', 'day', 'daily', 'month', 'monthly', 'quarter', 'quarterly', 'year', 'yearly', 'annual', 'annually']:
            # Query specified a granularity → show only that one
            freq_map = {
                'day': 'D', 'daily': 'D',
                'week': 'W', 'weekly': 'W',
                'month': 'M', 'monthly': 'M',
                'quarter': 'Q', 'quarterly': 'Q',
                'year': 'Y', 'yearly': 'Y', 'annual': 'Y', 'annually': 'Y'
            }
            freq = freq_map.get(granularity.lower(), 'M')
            frequencies = [(freq, granularity)]
        else:
            # General query → show MoM, QoQ, YoY all at once
            frequencies = [('M', 'Month-over-Month'), ('Q', 'Quarter-over-Quarter'), ('Y', 'Year-over-Year')]
        
        # Generate code for each frequency
        if is_count_metric:
            code = f"""
# Temporal trend analysis (count metric) - Multiple granularities
df['{date_column}'] = pd.to_datetime(df['{date_column}'], errors='coerce')
df_temporal = df.dropna(subset=['{date_column}'])
df_temporal = df_temporal.set_index('{date_column}')

temporal_results = {{}}
"""
            for freq, name in frequencies:
                code += f"""
# {name} analysis
trend_{freq} = df_temporal.resample('{freq}').size().reset_index(name='count')
trend_{freq}['period'] = trend_{freq}['{date_column}'].dt.to_period('{freq}').astype(str)
trend_{freq}['pct_change'] = trend_{freq}['count'].pct_change() * 100
trend_{freq}['granularity'] = '{name}'
temporal_results['{name}'] = trend_{freq}
"""
            code += """
result = temporal_results
"""
        else:
            code = f"""
# Temporal trend analysis (aggregate metric) - Multiple granularities
df['{date_column}'] = pd.to_datetime(df['{date_column}'], errors='coerce')
df_temporal = df.dropna(subset=['{date_column}', '{agg_column}'])
df_temporal = df_temporal.set_index('{date_column}')

temporal_results = {{}}
"""
            for freq, name in frequencies:
                code += f"""
# {name} analysis
trend_{freq} = df_temporal['{agg_column}'].resample('{freq}').agg(['sum', 'mean', 'count']).reset_index()
trend_{freq}['period'] = trend_{freq}['{date_column}'].dt.to_period('{freq}').astype(str)
trend_{freq}['pct_change'] = trend_{freq}['sum'].pct_change() * 100
trend_{freq}['granularity'] = '{name}'
temporal_results['{name}'] = trend_{freq}
"""
            code += """
result = temporal_results
"""
        
        return code


# ============================================================================
# MAIN AGENTIC SHAP ANALYSIS CLASS
# ============================================================================

class AgenticShapAnalysisV6:
    """
    Agentic SHAP Analysis with NL_to_python Pattern
    
    NO HARDCODINGS - Works with any dataset, columns, charts in production
    """
    
    def __init__(self, 
                 llm_client,
                 smart_agg_decider=None,
                 causal_cache_path: str = "causal_analysis_cache.json"):
        """
        Initialize the agentic SHAP analysis service
        
        Args:
            llm_client: OpenAI client for LLM calls
            smart_agg_decider: Global SmartAggregationDecider instance
            causal_cache_path: Path to causal analysis cache file
        """
        self.llm_client = llm_client
        self.smart_agg_decider = smart_agg_decider
        self.causal_cache_path = causal_cache_path
        self.logger = setup_module_logger('services.shap_analysis.V6')
        
        # Initialize supporting services
        self.fuzzy_matcher = FuzzyColumnMatcher(threshold=70)
        self.period_extractor = PeriodExtractionService(model_path="event-period-ner-bert")
        self.viz_service = IntelligentVisualizationService()
        self.hints_manager = get_hints_manager(logger=self.logger)
        self.code_generator = CodeGeneratorRegistry()
        
        # Initialize LangGraph workflow
        self.workflow = self._build_workflow()
        self.app = self.workflow.compile(checkpointer=MemorySaver())
        
        self.logger.info("="*80)
        self.logger.info("Agentic SHAP Analysis V6 Initialized (NL_to_python Pattern)")
        self.logger.info("- NO HARDCODINGS: Works with any dataset/columns")
        self.logger.info("- 25+ Sub-intents with dedicated code generators")
        self.logger.info("- Retry mechanism with LLM validation")
        self.logger.info("- Integration with UniversalCausalAnalyzer")
        self.logger.info("="*80)
    
    def _build_workflow(self) -> StateGraph:
        """Build LangGraph workflow with NL_to_python pattern"""
        workflow = StateGraph(dict)
        
        # ===== STAGE 1: Column Selection =====
        workflow.add_node("stage1_column_selection", self._stage1_column_selection_tool)
        workflow.add_node("stage1_5_value_grounding", self._stage1_5_value_grounding_tool)
        
        # ===== STAGE 2: Planning =====
        workflow.add_node("stage2_operation_planning", self._stage2_operation_planning_tool)
        
        # ===== NEW: TEMPORAL COMPARISON STAGES (Conditional) =====
        workflow.add_node("stage1_6_temporal_extraction", self._stage1_6_temporal_extraction_tool)
        workflow.add_node("stage1_7_temporal_validation", self._stage1_7_temporal_validation_tool)
        workflow.add_node("temporal_comparison_execution", self._temporal_comparison_execution_tool)
        
        # ===== OUTLIER DETECTION EXECUTION (Conditional) =====
        workflow.add_node("outlier_detection_execution", self._outlier_detection_execution_tool)
        
        # ===== TEMPORAL ANOMALY DETECTION EXECUTION (Conditional) =====
        workflow.add_node("temporal_anomaly_detection_execution", self._temporal_anomaly_detection_execution_tool)
        
        workflow.add_node("validate_plan", self._validate_plan_tool)
        
        # ===== STAGE 3: Execution =====
        workflow.add_node("stage3_1_code_generation", self._stage3_1_code_generation_tool)
        workflow.add_node("stage3_1_5_llm_repair", self._stage3_1_5_llm_code_repair)  # LLM Code Repair
        workflow.add_node("stage3_2_code_execution", self._stage3_2_code_execution_tool)
        workflow.add_node("validate_results", self._validate_results_tool)
        workflow.add_node("stage3_3_insight_formatting", self._stage3_3_insight_formatting_tool)
        
        # Orchestrator and finalizer
        workflow.add_node("task_orchestrator", self._task_orchestrator_tool)
        workflow.add_node("finalizer", self._finalizer_tool)
        
        # ==== EDGES ====
        workflow.add_edge(START, "task_orchestrator")
        
        # Orchestrator routes to appropriate stage
        workflow.add_conditional_edges(
            "task_orchestrator",
            self._route_next_stage,
            {
                "stage1": "stage1_column_selection",
                "stage1_5": "stage1_5_value_grounding",
                "stage2": "stage2_operation_planning",
                "stage1_6": "stage1_6_temporal_extraction",  # NEW: Temporal extraction
                "stage1_7": "stage1_7_temporal_validation",  # NEW: Temporal validation
                "temporal_execution": "temporal_comparison_execution",  # NEW: Temporal execution
                "outlier_execution": "outlier_detection_execution",  # NEW: Outlier detection
                "temporal_anomaly_execution": "temporal_anomaly_detection_execution",  # NEW: Temporal anomaly detection
                "validate_plan": "validate_plan",
                "stage3_1": "stage3_1_code_generation",
                "stage3_1_5": "stage3_1_5_llm_repair",  # LLM Code Repair
                "stage3_2": "stage3_2_code_execution",
                "validate_results": "validate_results",
                "stage3_3": "stage3_3_insight_formatting",
                "finalizer": "finalizer",
                "end": END
            }
        )
        
        # All nodes return to orchestrator
        for node in ["stage1_column_selection", "stage1_5_value_grounding",
                     "stage2_operation_planning", 
                     "stage1_6_temporal_extraction", "stage1_7_temporal_validation",  # NEW
                     "temporal_comparison_execution",  # NEW: Temporal execution
                     "outlier_detection_execution",  # NEW: Outlier detection
                     "temporal_anomaly_detection_execution",  # NEW: Temporal anomaly detection
                     "validate_plan",
                     "stage3_1_code_generation", "stage3_1_5_llm_repair",
                     "stage3_2_code_execution", "validate_results", 
                     "stage3_3_insight_formatting"]:
            workflow.add_edge(node, "task_orchestrator")
        
        workflow.add_edge("finalizer", END)
        
        return workflow
    
    # ========================================================================
    # QUERY NORMALIZATION
    # ========================================================================
    
    def _normalize_query(self, query: str) -> str:
        """
        Normalize common abbreviations in query to full forms.
        This ensures consistent temporal entity detection across different query formats.
        
        Args:
            query: Original user query
            
        Returns:
            Normalized query with abbreviations expanded
        """
        import re
        
        month_abbrev = {
            r'\bjan\b': 'January',
            r'\bjanu\b': 'January',
            r'\bfeb\b': 'February', 
            r'\bfebr\b': 'February',
            r'\bmar\b': 'March',
            r'\bmarc\b': 'March',
            r'\bapr\b': 'April',
            r'\bapri\b': 'April',
            r'\bmay\b': 'May',
            r'\bjun\b': 'June',
            r'\bjune\b': 'June',
            r'\bjul\b': 'July',
            r'\bjuly\b': 'July',
            r'\baug\b': 'August',
            r'\baugust\b': 'August',
            r'\bsep\b': 'September',
            r'\bsept\b': 'September',
            r'\boct\b': 'October',
            r'\bocto\b': 'October',
            r'\bnov\b': 'November',
            r'\bnove\b': 'November',
            r'\bdec\b': 'December',
            r'\bdece\b': 'December'
        }
        
        normalized = query
        for abbrev, full in month_abbrev.items():
            normalized = re.sub(abbrev, full, normalized, flags=re.IGNORECASE)
        
        return normalized
    
    # ========================================================================
    # MAIN ENTRY POINT
    # ========================================================================
    
    def process(self, 
                query_text: str,
                data_id: str,
                connection_key: str,
                selected_chart: str,
                intent_result=None,
                chart_context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Main entry point for processing analysis queries
        
        Args:
            query_text: User's natural language query
            data_id: Reference ID for DataFrame in DataManager (eliminates JSON bottleneck)
            connection_key: Connection/workbook identifier
            selected_chart: Name of the selected chart
            intent_result: QueryIntent object with primary_intent, confidence, etc.
            chart_context: Optional chart context with metadata
            
        Returns:
            Dict with analysis results in UI format
        """
        # Normalize query abbreviations (e.g., "feb" -> "February")
        original_query = query_text
        query_text = self._normalize_query(query_text)
        if original_query != query_text:
            self.logger.info(f"Query normalized: '{original_query}' -> '{query_text}'")
        
        # Get DataFrame from DataManager (instant memory access)
        from services.data_manager import get_data_manager
        data_manager = get_data_manager()
        csv_data = data_manager.get_data(data_id)
        
        self.logger.info("="*80)
        self.logger.info("=== STARTING AGENTIC SHAP ANALYSIS (V6) ===")
        self.logger.info(f"Query: '{query_text}'")
        self.logger.info(f"Chart: '{selected_chart}'")
        self.logger.info(f"Data ID: {data_id}")
        self.logger.info(f"CSV shape: {csv_data.shape}")
        self.logger.info("="*80)
        
        # Generate truly unique thread_id to prevent state carryover between queries
        import uuid
        thread_id = f"analysis_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:8]}"
        
        # Initialize state as dictionary - EXPLICIT RESET for new query
        initial_state = {
            # Input data (reference pattern - no JSON serialization)
            "query": query_text,
            "data_id": data_id,  # Reference to DataFrame in DataManager (instant access)
            "connection_key": connection_key,
            "selected_chart": selected_chart,
            "intent_result_dict": intent_result.__dict__ if hasattr(intent_result, '__dict__') else (intent_result if isinstance(intent_result, dict) else {}),
            "chart_context": chart_context or {},
            "csv_file_path": (chart_context or {}).get("csv_file_path"),  # Thread-safe CSV path from request context
            
            # Thread ID for checkpointing (must be unique per query)
            "thread_id": thread_id,
            
            # === CRITICAL: Explicitly reset ALL stage completion flags ===
            "stage1_completed": False,
            "stage1_5_completed": False,
            "stage2_completed": False,
            "plan_validated": False,
            "stage3_1_completed": False,
            "stage3_2_completed": False,
            "results_valid": False,
            "stage3_3_completed": False,
            "stage1_failed": False,
            "plan_validation_failed": False,
            "correction_mode": False,
            "fatal_error": False,
            
            # === CRITICAL: Explicitly reset ALL counters to 0 for new query ===
            "retry_attempt": 0,
            "retry_count": 0,
            "code_repair_attempt": 0,
            "total_iterations": 0,  # Safety net: hard cap on workflow iterations
            
            # Retry management (stored as dict for msgpack serialization)
            "max_retries": 2,  # Maximum retry attempts (configurable)
            "retry_manager_dict": AnalysisRetryManager(max_retries=2).to_dict(),
            "results_mismatch_query": False,
            "retry_corrections": [],
            
            # === CRITICAL: Reset ALL error tracking ===
            "error_stage": None,
            "execution_errors": [],
            "validation_errors": [],
            "failed_sub_intent": None,
            "failed_code": None,
            "execution_error_message": None,
            "execution_error_traceback": None,
            
            # LLM Code Repair (self-healing)
            "max_code_repair_attempts": 3,
            "code_repair_success": False,
            "repaired_code": None,
            
            # === CRITICAL: Reset ALL results storage ===
            "stage1_columns": {},
            "stage2_plan": {},
            "generated_codes": {},
            "execution_results": {},
            "final_insights": {},
            
            # Messages and errors
            "messages": [],
            "errors": [],
            
            # Available columns (for Stage 1 schema)
            "available_columns": csv_data.columns.tolist()
        }
        
        try:
            # Run the workflow
            config = {
                "configurable": {"thread_id": initial_state["thread_id"]},
                "recursion_limit": 100  # Increased to handle LLM repair attempts (3 attempts × multiple sub-intents)
            }
            final_state = self.app.invoke(initial_state, config)
            
            self.logger.info("Workflow completed successfully")
            
            # Extract final result
            final_result = final_state.get("final_result", {})
            
            return final_result
            
        except Exception as e:
            self.logger.error(f"Workflow failed: {str(e)}")
            self.logger.error(traceback.format_exc())
            
            return {
                "success": False,
                "error": str(e),
                "query": query_text,
                "messages": initial_state.get("messages", []),
                "response": f"Analysis failed: {str(e)}"
            }
    
    # ========================================================================
    # TASK ORCHESTRATOR & ROUTING
    # ========================================================================
    
    def _task_orchestrator_tool(self, state: Dict) -> Dict:
        """
        Task orchestrator manages workflow progression
        """
        current_route = self._route_next_stage(state)
        self.logger.info(f"[ORCHESTRATOR] Next route: {current_route}")
        return state
    
    def _route_next_stage(self, state: Dict) -> str:
        """
        Route to next stage based on completion status
        Handles stage-aware retry logic and fatal errors
        """
        # === SAFETY NET: Hard cap on total workflow iterations ===
        total_iterations = state.get("total_iterations", 0)
        max_total_iterations = 30  # Reasonable for most complex queries
        
        if total_iterations >= max_total_iterations:
            self.logger.error(f"⛔ SAFETY NET: Hit hard cap of {max_total_iterations} total iterations")
            self.logger.error(f"   Current state: stage1={state.get('stage1_completed')}, "
                            f"stage2={state.get('stage2_completed')}, "
                            f"stage3_1={state.get('stage3_1_completed')}, "
                            f"stage3_2={state.get('stage3_2_completed')}")
            self.logger.error(f"   Retry counters: retry_attempt={state.get('retry_attempt')}, "
                            f"retry_count={state.get('retry_count')}, "
                            f"code_repair_attempt={state.get('code_repair_attempt')}")
            self.logger.error(f"   This suggests an infinite loop. Check validator or routing logic.")
            state["fatal_error"] = True
            state["errors"].append(f"Hit hard cap of {max_total_iterations} iterations - potential infinite loop")
            return "finalizer"
        
        # Increment iteration counter
        state["total_iterations"] = total_iterations + 1
        
        # Check for fatal errors (e.g., Stage 1 failure)
        if state.get("fatal_error"):
            self.logger.error("⛔ Fatal error detected, routing to finalizer")
            return "finalizer"
        
        # Check if Stage 1 failed critically
        if state.get("stage1_failed") and not state.get("stage1_completed"):
            self.logger.error("⛔ Stage 1 failed, cannot proceed")
            return "finalizer"
        
        # === STAGE-AWARE RETRY LOGIC ===
        # Handle plan validation failure (from Validator 1)
        if state.get("plan_validation_failed") and not state.get("plan_validated"):
            retry_count = state.get("retry_count", 0)
            max_retries = state.get("max_retries", 2)
            
            if retry_count < max_retries:
                # Increment retry counter and reset Stage 2
                state["retry_count"] = retry_count + 1
                state["stage2_completed"] = False
                state["plan_validation_failed"] = False  # Clear flag
                self.logger.warning(f"🔄 Plan validation failed. Retrying Stage 2 (attempt {retry_count + 1}/{max_retries})")
                return "stage2"
            else:
                # Max retries reached, proceed with warnings
                self.logger.warning(f"⚠️ Max retries reached, proceeding despite validation concerns")
                state["plan_validated"] = True
                state["plan_validation_failed"] = False
        
        # === LLM CODE REPAIR ROUTING (PRIORITY: Try repair before full retry) ===
        # If code execution failed, try LLM repair first (up to 3 attempts)
        execution_errors = state.get("execution_errors", [])
        if execution_errors and not state.get("results_valid"):
            code_repair_attempt = state.get("code_repair_attempt", 0)
            max_code_repair_attempts = state.get("max_code_repair_attempts", 3)
            code_repair_success = state.get("code_repair_success", False)
            
            # If repair succeeded, remove only the fixed error from the list
            if code_repair_success:
                self.logger.info("✅ Code repair succeeded, removing fixed error from list")
                failed_sub_intent = state.get("failed_sub_intent")
                execution_errors = state.get("execution_errors", [])
                state["execution_errors"] = [e for e in execution_errors if failed_sub_intent not in e]
                state["code_repair_attempt"] = 0
                state["code_repair_success"] = False  # Reset flag for next repair
                
                # Check if there are more errors to repair
                remaining_errors = state.get("execution_errors", [])
                if remaining_errors:
                    self.logger.info(f"   Still have {len(remaining_errors)} errors to repair, setting up next failure")
                    
                    # Extract next failed sub-intent name from error string (format: "sub_intent: error message")
                    next_error = remaining_errors[0]
                    next_sub_intent = next_error.split(":")[0].strip()
                    
                    # Get the failed code and error details from execution_results
                    execution_results = state.get("execution_results", {})
                    generated_code_dict = state.get("generated_code", {})
                    
                    # Set up state for next repair
                    state["failed_sub_intent"] = next_sub_intent
                    state["failed_code"] = generated_code_dict.get(next_sub_intent, "")
                    state["execution_error_message"] = execution_results.get(f"{next_sub_intent}_error", "Unknown error")
                    state["execution_error_traceback"] = ""  # Don't have traceback for subsequent failures
                    
                    self.logger.info(f"   Next failure to repair: {next_sub_intent}")
                    # Don't fall through - route directly to repair next error
                    return "stage3_1_5"
                else:
                    self.logger.info("   All errors repaired, continuing to validation")
                    # Clear repair state
                    state["failed_sub_intent"] = None
                    state["failed_code"] = None
                    # Let normal flow continue to validation
            
            # If we haven't exhausted repair attempts, try LLM repair
            elif code_repair_attempt < max_code_repair_attempts:
                self.logger.info(f"🔧 Routing to LLM Code Repair (attempt {code_repair_attempt + 1}/{max_code_repair_attempts})")
                return "stage3_1_5"
            
            # LLM repair exhausted, mark this sub-intent as unfixable and continue
            else:
                failed_sub_intent = state.get("failed_sub_intent")
                self.logger.warning(f"⚠️ LLM repair exhausted ({max_code_repair_attempts} attempts) for '{failed_sub_intent}'")
                self.logger.warning(f"   Marking '{failed_sub_intent}' as unfixable, will skip in results")
                
                # Track unfixable sub-intents
                unfixable_intents = state.get("unfixable_sub_intents", [])
                if failed_sub_intent and failed_sub_intent not in unfixable_intents:
                    unfixable_intents.append(failed_sub_intent)
                    state["unfixable_sub_intents"] = unfixable_intents
                
                # Remove this specific error from execution_errors list
                execution_results = state.get("execution_results", {})
                error_key = f"{failed_sub_intent}_error"
                if error_key in execution_results:
                    del execution_results[error_key]
                    self.logger.info(f"   Removed error entry: {error_key}")
                
                # Remove from execution_errors list
                execution_errors = [e for e in execution_errors if failed_sub_intent not in e]
                state["execution_errors"] = execution_errors
                
                # Reset code repair counter
                state["code_repair_attempt"] = 0
                state["failed_sub_intent"] = None
                state["failed_code"] = None
                
                # If there are other errors, continue processing them
                # If no more errors, continue to validation
                if not execution_errors:
                    self.logger.info("   No more execution errors, continuing to result validation")
                else:
                    self.logger.info(f"   Still have {len(execution_errors)} other errors to process")
        
        # Handle execution errors (from Stage 3.2) - AFTER LLM repair attempts
        execution_errors = state.get("execution_errors", [])
        if execution_errors and not state.get("results_valid"):
            retry_count = state.get("retry_count", 0)
            max_retries = state.get("max_retries", 2)
            error_stage = state.get("error_stage", "stage3")
            
            if retry_count < max_retries:
                # Determine where to retry based on error stage
                state["retry_count"] = retry_count + 1
                
                if error_stage == "stage1":
                    # Stage 1 error - retry from Stage 1
                    self.logger.warning(f"🔄 Stage 1 error. Retrying from Stage 1 (attempt {retry_count + 1}/{max_retries})")
                    state["stage1_completed"] = False
                    return "stage1"
                
                elif error_stage == "stage2" or error_stage == "stage3":
                    # Stage 2 or 3 error - retry planning from Stage 2
                    # Stage 1 columns are likely fine, just need better plan
                    self.logger.warning(f"🔄 Execution error. Retrying planning from Stage 2 (attempt {retry_count + 1}/{max_retries})")
                    state["stage2_completed"] = False
                    state["stage3_1_completed"] = False
                    state["stage3_2_completed"] = False
                    return "stage2"
            else:
                # Max retries reached, fail gracefully
                self.logger.error(f"❌ Max retries ({max_retries}) reached with execution errors. Failing gracefully.")
                return "finalizer"
        
        # Handle results validation retry (from Validator 2)
        if state.get("results_mismatch_query"):
            retry_manager_dict = state.get("retry_manager_dict")
            if retry_manager_dict:
                retry_manager = AnalysisRetryManager.from_dict(retry_manager_dict)
                retry_attempt = state.get("retry_attempt", 0)
                if retry_manager.should_retry(retry_attempt):
                    # Retry from Stage 2 (results don't match query)
                    self.logger.info(f"🔄 Results validation failed. Retrying from Stage 2 (attempt {retry_attempt + 1})")
                    state["retry_attempt"] = retry_attempt + 1
                    state["stage2_completed"] = False
                    state["stage3_1_completed"] = False
                    state["stage3_2_completed"] = False
                    state["results_mismatch_query"] = False  # Clear flag
                    return "stage2"
        
        # === NORMAL STAGE PROGRESSION ===
        # Stage 1
        if not state.get("stage1_completed"):
            return "stage1"
        if not state.get("stage1_5_completed"):
            return "stage1_5"
        
        # Stage 2
        if not state.get("stage2_completed"):
            return "stage2"
        
        # === NEW: STAGE 1.6 - TEMPORAL EXTRACTION (CONDITIONAL) ===
        if not state.get("stage1_6_completed"):
            # Check if we should try temporal extraction
            should_extract = False
            trigger_reason = None
            
            # Trigger 1: Stage 2 dual validation passed
            if state.get("proceed_to_temporal_extraction"):
                should_extract = True
                trigger_reason = "stage2_validation"
            
            # Trigger 2: Late detection from Stage 3 (backtrack)
            if state.get("late_temporal_detection"):
                should_extract = True
                trigger_reason = "late_detection_stage3"
                self.logger.warning("⚠️ LATE DETECTION: Stage 3 identified temporal intent - going back to Stage 1.6")
            
            if should_extract:
                self.logger.info(f"→ Proceeding to Stage 1.6: Temporal Extraction (trigger: {trigger_reason})")
                return "stage1_6"
            else:
                self.logger.info("→ Skipping Stage 1.6: Not a temporal comparison query")
                state["stage1_6_completed"] = True
                state["has_temporal_comparison"] = False
        
        # === NEW: STAGE 1.7 - TEMPORAL VALIDATION (CONDITIONAL) ===
        if not state.get("stage1_7_completed"):
            if state.get("has_temporal_comparison"):
                self.logger.info("→ Proceeding to Stage 1.7: Temporal Validation")
                return "stage1_7"
            else:
                self.logger.info("→ Skipping Stage 1.7: No temporal comparison detected")
                state["stage1_7_completed"] = True
                state["temporal_comparison_validated"] = False
        
        # === TEMPORAL RETRY LOGIC ===
        if (state.get("temporal_validation_failed") and 
            not state.get("temporal_comparison_validated") and 
            state.get("temporal_retry_count", 0) < 2):
            # Retry Stage 1.6 with corrections
            retry_count = state.get("temporal_retry_count", 0) + 1
            state["temporal_retry_count"] = retry_count
            state["stage1_6_completed"] = False
            state["stage1_7_completed"] = False
            self.logger.warning(f"🔄 TEMPORAL RETRY: Attempt #{retry_count}/2 with corrections")
            return "stage1_6"
        
        # === TEMPORAL FALLBACK: If retries exhausted, use old flow ===
        if (state.get("temporal_validation_failed") and 
            state.get("temporal_retry_count", 0) >= 2):
            self.logger.error("✗ Temporal extraction failed after 2 retries - falling back to standard flow")
            state["has_temporal_comparison"] = False
            state["temporal_comparison_validated"] = False
            state["temporal_validation_failed"] = False
        
        # ===================================================================
        # ROUTING BASED ON LLM'S SUB-INTENT DECISION (NO FLAGS)
        # Trust the LLM's Stage 2 decision - route based on sub_intents only
        # ===================================================================
        
        # Get sub-intents from Stage 2 plan
        stage2_plan = state.get("stage2_plan", {})
        sub_intents = stage2_plan.get("sub_intents", [])
        
        self.logger.info(f"[ROUTING] Sub-intents from Stage 2: {sub_intents}")
        
        # === ROUTE BASED ON SUB-INTENT (LLM's decision) ===
        
        # FIX: Check if causal analysis needs to run FIRST (before any execution)
        # Causal analysis runs in stage3_1 (code generation), must complete before temporal/outlier/anomaly
        if stage2_plan.get("trigger_causal_analysis") and not state.get("stage3_1_completed"):
            self.logger.info("🔬 ROUTING: Stage 3.1 Code Generation (causal analysis needed first)")
            return "stage3_1"
        
        # Check for temporal_anomaly_detection sub-intent
        if "temporal_anomaly_detection" in sub_intents and not state.get("temporal_anomaly_execution_completed"):
            self.logger.info("🔀 ROUTING: Temporal Anomaly Detection (LLM detected temporal_anomaly_detection sub-intent)")
            return "temporal_anomaly_execution"
        
        # Check for outlier_detection sub-intent
        if "outlier_detection" in sub_intents and not state.get("outlier_execution_completed"):
            self.logger.info("🔀 ROUTING: Outlier Detection (LLM detected outlier_detection sub-intent)")
            return "outlier_execution"
        
        # Check for temporal comparison sub-intents (period_over_period, temporal_trend)
        temporal_comparison_intents = ["period_over_period", "temporal_trend"]
        if any(intent in sub_intents for intent in temporal_comparison_intents):
            # Only route to temporal execution if temporal extraction/validation completed
            if (state.get("temporal_comparison_validated") and 
                not state.get("temporal_execution_completed")):
                self.logger.info(f"🔀 ROUTING: Temporal Comparison (LLM detected {[i for i in sub_intents if i in temporal_comparison_intents]})")
                return "temporal_execution"
        
        # === After execution completions, check if we should finalize ===
        if state.get("temporal_anomaly_execution_completed"):
            if state.get("completed"):
                self.logger.info("✓ Temporal anomaly detection completed - routing to finalizer")
                return "finalizer"
            else:
                # Execution failed - clear flag and continue to standard flow
                if not state.get("temporal_anomaly_fallback_triggered"):
                    self.logger.warning("⚠️ Temporal anomaly detection failed, falling back to standard flow")
                    state["temporal_anomaly_fallback_triggered"] = True
        
        if state.get("outlier_execution_completed"):
            if state.get("completed"):
                self.logger.info("✓ Outlier detection completed - routing to finalizer")
                return "finalizer"
            else:
                if not state.get("outlier_fallback_triggered"):
                    self.logger.warning("⚠️ Outlier detection failed, falling back to standard flow")
                    state["outlier_fallback_triggered"] = True
        
        if state.get("temporal_execution_completed"):
            if state.get("completed"):
                self.logger.info("✓ Temporal comparison completed - routing to finalizer")
                return "finalizer"
            else:
                if not state.get("temporal_fallback_triggered"):
                    self.logger.warning("⚠️ Temporal execution failed, falling back to standard flow")
                    state["temporal_fallback_triggered"] = True
                    state["has_temporal_comparison"] = False
                    state["temporal_comparison_validated"] = False
        
        # === STANDARD FLOW (if not temporal/outlier/temporal_anomaly or failed) ===
        if not state.get("plan_validated"):
            return "validate_plan"
        
        # Stage 3
        if not state.get("stage3_1_completed"):
            return "stage3_1"
        
        # FIX: After stage3_1 completes (causal analysis done), check if we should route to temporal
        # This happens when trigger_causal=True AND temporal_trend sub-intent exists
        if (state.get("stage3_1_completed") and 
            stage2_plan.get("trigger_causal_analysis") and
            not state.get("temporal_execution_completed")):
            temporal_comparison_intents = ["period_over_period", "temporal_trend"]
            if any(intent in sub_intents for intent in temporal_comparison_intents):
                if state.get("temporal_comparison_validated"):
                    self.logger.info("🔀 ROUTING: Causal complete, now routing to Temporal Comparison")
                    return "temporal_execution"
        
        if not state.get("stage3_2_completed"):
            return "stage3_2"
        
        # Results validation
        if not state.get("results_valid") and not state.get("result_validation"):
            return "validate_results"
        
        # Insight formatting
        if not state.get("stage3_3_completed"):
            return "stage3_3"
        
        # All stages complete
        return "finalizer"
    
    # ========================================================================
    # STAGE 1: STRICT COLUMN SELECTION
    # ========================================================================
    
    def _stage1_column_selection_tool(self, state: Dict) -> Dict:
        """
        Stage 1: Strict column selection using Pydantic Literal enums
        Prevents LLM from hallucinating column names
        
        NO HARDCODINGS: Uses actual columns from CSV data
        """
        self.logger.info("="*80)
        self.logger.info("=== STAGE 1: STRICT COLUMN SELECTION (NO HALLUCINATIONS) ===")
        self.logger.info("="*80)
        
        try:
            query = state.get("query", "")
            
            # Get DataFrame from DataManager (instant memory access - no JSON deserialization)
            from services.data_manager import get_data_manager
            data_manager = get_data_manager()
            data_id = state.get("data_id")
            df = data_manager.get_data(data_id)
            
            # Get actual columns from data (NO HARDCODING)
            actual_columns = df.columns.tolist()
            self.logger.info(f"Actual columns in data: {len(actual_columns)}")
            
            if not PYDANTIC_AVAILABLE:
                raise ImportError("Pydantic required for strict schema")
            
            # Create Pydantic schema with strict column enums
            # This makes hallucination IMPOSSIBLE
            ColumnEnum = Literal[tuple(actual_columns)]
            
            class StrictColumnSelection(BaseModel):
                """Strict column selection - cannot hallucinate"""
                
                # Mentioned FEATURES (X variables - for grouping/causation)
                mentioned_features: Optional[List[ColumnEnum]] = Field(
                    None,
                    description="Feature/categorical columns mentioned as causal factors (X in 'how X affects Y')"
                )
                
                # Mentioned METRICS (Y variables - for filtering which metrics to analyze)
                mentioned_metrics: Optional[List[ColumnEnum]] = Field(
                    None,
                    description="Metric/numeric columns mentioned as specific targets (Y in temporal/spike queries)"
                )
                
                # Target metric (Y in "how X affects Y")
                target_metric_column: Optional[ColumnEnum] = Field(
                    None,
                    description="The metric being analyzed (Y) - use ID column for counts"
                )
                
                # Date column for temporal analysis
                date_column: Optional[ColumnEnum] = Field(
                    None,
                    description="Date column if temporal analysis needed"
                )
                
                # Categorical columns for grouping
                categorical_columns: Optional[List[ColumnEnum]] = Field(
                    None,
                    description="Categorical columns for segmentation (max 3)"
                )
                
                # Numeric columns for correlation
                numeric_columns: Optional[List[ColumnEnum]] = Field(
                    None,
                    description="Numeric columns for correlation analysis"
                )
                
                # Filter column and value (if query filters to specific value)
                filter_column: Optional[ColumnEnum] = None
                filter_value: Optional[str] = Field(
                    None,
                    description="Value to filter by (exact value from data)"
                )
                
                # Query classification
                query_type: Literal["specific", "general"] = Field(
                    description="Is query about specific columns (mentions names) or general analysis (exploratory)?"
                )
                
                # Whether target is a count metric (ID column)
                is_count_metric: bool = Field(
                    default=False,
                    description="True if analyzing count of rows (use ID column), False if analyzing a numeric metric"
                )
                
                confidence: float = Field(ge=0.0, le=1.0)
            
            # Get column types dynamically
            numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
            categorical_cols = df.select_dtypes(include=['object', 'category']).columns.tolist()
            date_cols = []
            for col in df.columns:
                if 'date' in col.lower() or 'time' in col.lower():
                    date_cols.append(col)
            
            # Sample values per column (handles Timestamps properly)
            # Similar to NL_to_python's approach
            column_samples = {}
            for col in actual_columns[:20]:  # Limit to prevent token overflow
                try:
                    unique_vals = df[col].dropna().unique()
                    if len(unique_vals) > 0:
                        # Convert to list and then explicitly to strings for JSON serialization
                        sample_vals = [str(val) for val in unique_vals[:5]]
                        column_samples[col] = sample_vals
                except Exception as e:
                    # Skip columns that can't be sampled
                    self.logger.warning(f"Could not sample column {col}: {str(e)}")
                    continue
            
            # Call LLM with strict schema
            system_prompt = f"""You are extracting columns from a user query for data analysis.

CRITICAL: You MUST select ONLY from the available columns list below.
You CANNOT invent, modify, or guess column names.

Available columns (EXACT names you must use):
{json.dumps(actual_columns, indent=2)}

Column types (for context):
- Numeric columns: {numeric_cols[:10]}
- Categorical columns: {categorical_cols[:10]}
- Date columns: {date_cols}

Sample values per column (for context only):
{json.dumps(column_samples, indent=2)}

RULES:
1. mentioned_features: Feature/categorical columns mentioned as CAUSAL FACTORS (X variables)
   - Pattern: "how X affects Y", "impact of X on Y", "breakdown by X"
   - Examples:
     * "how account_manager affects tickets" → mentioned_features=['account_manager']
     * "impact of region on sales" → mentioned_features=['region']
     * "breakdown by status" → mentioned_features=['status']
     * "correlation between age_in_hours and eng_transfers" → mentioned_features=['age_in_hours', 'eng_transfers']
   - Use: For grouping/segmentation analysis
   
2. mentioned_metrics: Metric/numeric columns mentioned as SPECIFIC TARGETS (Y variables)
   - Pattern: "spike in Y", "trend in Y", "why Y increased", "Y in [time period]"
   - Examples:
     * "spike in open volume in June" → mentioned_metrics=['open_volume']
     * "why closed volume increased in march" → mentioned_metrics=['closed_volume']
     * "trend in revenue over time" → mentioned_metrics=['revenue']
     * "how did sales change" → mentioned_metrics=['sales']
     * "spike in march" → mentioned_metrics=None (no specific metric mentioned)
   - Use: For filtering which metrics to analyze in multi-metric charts
   - IMPORTANT: Only include if user specifically names the metric. If query is general ("spike in march"), leave as None.
   
3. target_metric_column: The primary metric being analyzed (Y)
   - If user says "tickets" or "count" → use ID column (e.g., case_id, ticket_id)
   - If user says "sales" or "revenue" → use that numeric column
   - For CORRELATION queries "correlation between X and Y":
     → target_metric_column = X (first mentioned column becomes the target)
   - For CORRELATION queries "correlation between X and other metrics":
     → target_metric_column = X (the explicitly named column is the target)
   - For CORRELATION queries "which metrics correlate with X":
     → target_metric_column = X
   - Set is_count_metric=True if counting rows, False if analyzing a metric
   
3. query_type:
   - "specific" if query mentions column names ("how X affects Y", "correlation between X and Y")
   - "general" if query is exploratory ("what affects Y", "find drivers")
   
4. categorical_columns: Select up to 3 most relevant categorical columns for segmentation

5. numeric_columns: SPECIAL RULES for CORRELATION queries:
   - If user says "other metrics", "all metrics", "other columns" → select all numeric columns
   - If user mentions exactly 2 specific column names (e.g., "correlation between X and Y"):
     * If BOTH columns are in the numeric columns list → select only those 2
     * If one column is not numeric or is an ID column → select all numeric columns (fallback)
   - If user mentions 1 column and asks "what correlates with X" → select all numeric columns
   - For NON-correlation queries: select numeric columns relevant to the query
   
6. date_column: Identify the date/time column for temporal analysis

   STEP 1 - DETECT IF TEMPORAL:
   Ask yourself: "Does this query analyze HOW something changes OVER TIME?"
   
   Temporal indicators:
   - Month names: january, february, march, april, may, june, july, august, september, october, november, december
   - Quarters: Q1, Q2, Q3, Q4, "quarter 1", etc.
   - Years: 2024, 2023, 2025
   - Keywords: "trend", "over time", "MoM", "QoQ", "YoY", "vs last", "compared to"
   - Changes with time: "spike in [month]", "drop in [quarter]", "increase in [year]"
   
   Key rules:
   - "why [event] in [time period]" → TEMPORAL
   - Month/quarter/year names → TEMPORAL (even without explicit keywords)
   - Word order doesn't matter: "march for X" and "X for march" are BOTH temporal
   - If NO time periods AND NO temporal keywords → NOT TEMPORAL
   
   STEP 2 - SELECT DATE COLUMN (CRITICAL PRIORITY ORDER):
   
   **PRIORITY 1: USE CHART CONTEXT (MOST IMPORTANT)**
   Check if any date/time columns appear in the available columns list.
   These are the columns the chart actually uses - USE THESE FIRST.
   Look for patterns: 'create_month', 'create_date', 'month', 'quarter', 'year', 'week', 'day'
   
   **PRIORITY 2: MATCH TEMPORAL GRANULARITY**
   If multiple date columns available:
   - Month names in query → prefer 'month' columns
   - Quarter mentions → prefer 'quarter' columns
   - Year mentions → prefer 'year' columns
   
   **CRITICAL RULES - AVOID WRONG SELECTION:**
   
   Rule A: DO NOT match date columns based on metric names in query
           ✗ "closed volume in march" → DO NOT select "closeddate"
           ✓ "closed volume in march" → Select "create_month" or similar
           
   Rule B: Metric name ≠ Time axis (these are INDEPENDENT concepts)
           - "closed volume" is a METRIC (what you're measuring)
           - "create_month" is TIME AXIS (when you're measuring)
           - Don't confuse them!
           
   Rule C: If chart uses a specific date column (visible in available columns), USE THAT
           - Chart shows create_month → Use create_month
           - Don't pick other date columns like createddate, closeddate, etc.
   
   **EXAMPLES:**
   ✓ Query: "spike in march", Available: ['create_month', 'createddate'] → Select 'create_month'
   ✓ Query: "closed volume in march", Available: ['create_month', 'closeddate'] → Select 'create_month' (NOT closeddate!)
   ✗ Query: "closed volume in march" → DO NOT select 'closeddate' just because query mentions "closed"
   
   **IF NOT TEMPORAL:** Leave as None

REMEMBER: Use EXACT column names from the available columns list!
"""
            
            response = self.llm_client.beta.chat.completions.parse(
                model="gpt-4o-2024-08-06",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Query: {query}"}
                ],
                response_format=StrictColumnSelection
            )
            
            result = response.choices[0].message.parsed
            
            # === PRIORITY SYSTEM: Smart column selection (data-driven, not hardcoded) ===
            # Override LLM's choice with smarter logic based on actual available columns
            target_metric = result.target_metric_column
            is_count_metric = result.is_count_metric
            
            self.logger.info(f"[PRIORITY SYSTEM] LLM suggested: {target_metric}, is_count={is_count_metric}")
            
            # Build priority candidates using SmartAggregationDecider (NO HARDCODING)
            query_lower = query.lower()
            candidates = []
            
            # === NEW: Determine which columns actually need SmartAgg analysis ===
            # Only analyze columns that are relevant to the query, not all 95 columns!
            columns_to_analyze = []
            
            # Priority 1: Target metric column (if numeric)
            if result.target_metric_column and result.target_metric_column in numeric_cols:
                columns_to_analyze.append(result.target_metric_column)
                self.logger.info(f"[SMART_AGG] Will analyze target_metric: {result.target_metric_column}")
            
            # Priority 2: Mentioned metrics (if numeric and not already added)
            if result.mentioned_metrics:
                for col in result.mentioned_metrics:
                    if col in numeric_cols and col not in columns_to_analyze:
                        columns_to_analyze.append(col)
                        self.logger.info(f"[SMART_AGG] Will analyze mentioned metric: {col}")
            
            # Priority 3: Mentioned features (if numeric and not already added) - for correlation queries
            if result.mentioned_features:
                for col in result.mentioned_features:
                    if col in numeric_cols and col not in columns_to_analyze:
                        columns_to_analyze.append(col)
                        self.logger.info(f"[SMART_AGG] Will analyze mentioned feature (numeric): {col}")
            
            # Special case: Correlation queries need all numeric columns for comparison
            is_correlation_query = any(keyword in query_lower for keyword in ['correlation', 'correlate', 'correlated'])
            if is_correlation_query:
                if result.numeric_columns:
                    # Use LLM's selection of numeric columns for correlation
                    columns_to_analyze = [col for col in result.numeric_columns if col in numeric_cols]
                    self.logger.info(f"[SMART_AGG] Correlation query detected - analyzing {len(columns_to_analyze)} numeric columns")
                else:
                    # Fallback: analyze all numeric columns for correlation
                    columns_to_analyze = numeric_cols
                    self.logger.info(f"[SMART_AGG] Correlation query but no numeric_columns specified - analyzing all {len(numeric_cols)} numeric columns")
            
            # Fallback: If no columns identified, check chart context first
            if not columns_to_analyze:
                # PRIORITY 1: Check chart context for column suggestions
                selected_chart = state.get("selected_chart", "")
                if selected_chart:
                    chart_suggested_cols = self._extract_columns_from_chart_name(selected_chart, df, numeric_cols)
                    if chart_suggested_cols:
                        columns_to_analyze = chart_suggested_cols
                        self.logger.info(f"[CHART_CONTEXT] Using columns from chart '{selected_chart}': {chart_suggested_cols}")
                        
                        # NEW: Check if multi-Y-axis chart with no specific metric mentioned
                        if len(chart_suggested_cols) > 1:
                            query_lower = query.lower()
                            # Check if any metric is mentioned in query
                            mentioned_metrics = [
                                col for col in chart_suggested_cols 
                                if col.lower().replace('_', ' ') in query_lower
                            ]
                            
                            if mentioned_metrics:
                                self.logger.info(f"[MULTI_Y_AXIS] User mentioned specific metrics: {mentioned_metrics}")
                                columns_to_analyze = mentioned_metrics
                            else:
                                self.logger.info(f"[MULTI_Y_AXIS] No specific metric mentioned, will analyze all Y-axes: {chart_suggested_cols}")
                                # Keep all chart columns for analysis
                
                # PRIORITY 2: Use target_metric if provided
                if not columns_to_analyze and result.target_metric_column:
                    columns_to_analyze = [result.target_metric_column]
                    self.logger.warning(f"[SMART_AGG] No numeric columns identified, using target_metric: {result.target_metric_column}")
                
                # PRIORITY 3: Last resort - first numeric column
                if not columns_to_analyze and numeric_cols:
                    columns_to_analyze = [numeric_cols[0]]
                    self.logger.warning(f"[SMART_AGG] No columns identified, using first numeric column: {numeric_cols[0]}")
            
            self.logger.info(f"[SMART_AGG] Total columns to analyze: {len(columns_to_analyze)} (out of {len(numeric_cols)} numeric columns)")
            
            # Use SmartAggregationDecider for intelligent column selection
            if self.smart_agg_decider is not None and df is not None:
                self.logger.info("[SMART_AGG] Using SmartAggregationDecider for column analysis")
                
                # Analyze ONLY the relevant columns (not all numeric columns)
                for col in columns_to_analyze:
                    try:
                        # Get LLM-based decision with actual data analysis
                        decision = self.smart_agg_decider.decide_aggregation(
                            query=query,
                            column=col,
                            df=df
                        )
                        
                        # Determine if this is a count metric based on LLM decision
                        is_count = decision['aggregation'] in ['COUNT', 'COUNT_DISTINCT']
                        
                        # Map confidence to priority score (high=3, medium=2, low=1)
                        confidence_map = {'high': 3, 'medium': 2, 'low': 1}
                        priority_score = confidence_map.get(decision.get('confidence', 'low'), 1)
                        
                        # Store as (priority_score, column_name, is_count_metric, aggregation_type, reasoning)
                        candidates.append((priority_score, col, is_count, decision['aggregation'], decision.get('reasoning', '')))
                        
                        self.logger.info(f"[SMART_AGG] {col}: aggregation={decision['aggregation']}, "
                                       f"confidence={decision.get('confidence', 'unknown')}, "
                                       f"is_count={is_count}")
                        
                    except Exception as e:
                        self.logger.warning(f"[SMART_AGG] Error analyzing column '{col}': {e}")
                        # Fallback: treat as regular numeric column (priority=1, not count, use SUM)
                        candidates.append((1, col, False, 'SUM', 'Fallback: analysis failed'))
                
                # Sort by priority score (highest first)
                candidates.sort(key=lambda x: x[0], reverse=True)
                
            else:
                # Fallback: SmartAggregationDecider not available
                self.logger.warning("[SMART_AGG] SmartAggregationDecider not available, using basic fallback")
                
                # Basic fallback: treat relevant columns as regular metrics (not all 95 columns)
                for col in columns_to_analyze:
                    # Simple heuristic: if column name suggests counting, mark as count
                    col_lower = col.lower()
                    is_count = any(pattern in col_lower for pattern in ['_id', 'id_'])
                    agg_type = 'COUNT' if is_count else 'SUM'
                    candidates.append((1, col, is_count, agg_type, 'Fallback: no SmartAgg'))
                    self.logger.info(f"[FALLBACK] {col}: is_count={is_count}, agg={agg_type}")
            
            # === POST-SMART-AGG: SELECTION LOGIC ===
            # Check if multi-metric analysis is needed BEFORE running priority system
            requires_multi_metric = len(columns_to_analyze) > 1
            
            if requires_multi_metric:
                # Multi-metric scenario - DO NOT select single best column
                # Store all columns for separate analysis
                self.logger.info(f"[MULTI_METRIC] Multi-metric chart detected with {len(columns_to_analyze)} metrics")
                self.logger.info(f"[MULTI_METRIC] Skipping single-column selection, will analyze all: {columns_to_analyze}")
                
                # Use first column as default target_metric (for backward compatibility)
                # But the real work will happen in execution using columns_to_analyze
                target_metric = columns_to_analyze[0] if columns_to_analyze else target_metric
                
                # Get aggregation info for first column (fallback)
                if candidates:
                    first_candidate = candidates[0]
                    is_count_metric = first_candidate[2]
                    recommended_aggregation = first_candidate[3]
                else:
                    recommended_aggregation = None
                    
            # Select best candidate based on priority score + query relevance
            elif candidates:
                # Candidates already sorted by priority score (highest first) from SmartAggregationDecider
                
                # If multiple candidates at same priority, use query relevance
                best_priority_score = candidates[0][0]
                same_priority_candidates = [c for c in candidates if c[0] == best_priority_score]
                
                if len(same_priority_candidates) > 1:
                    # Get selected chart context for bonus scoring
                    selected_chart = state.get("selected_chart", "")
                    chart_keywords = set()
                    if selected_chart:
                        # Extract keywords from chart name (e.g., "Number of Tickets Line chart" -> {"number", "of", "tickets", "line", "chart"})
                        chart_keywords = set(selected_chart.lower().replace('_', ' ').replace('-', ' ').split())
                        self.logger.info(f"[CHART_CONTEXT] Selected chart: '{selected_chart}'")
                        self.logger.info(f"[CHART_CONTEXT] Chart keywords: {chart_keywords}")
                    
                    # Determine if we should use chart bonus
                    # Only use chart context when NO columns were detected in the query
                    mentioned_features = result.mentioned_features if result.mentioned_features else []
                    mentioned_metrics = result.mentioned_metrics if result.mentioned_metrics else []
                    use_chart_bonus = len(mentioned_features) == 0 and len(mentioned_metrics) == 0
                    
                    if use_chart_bonus:
                        self.logger.info(f"[CHART_BONUS] Enabled: No columns detected in query, using chart context to guide selection")
                    else:
                        all_mentioned = mentioned_features + mentioned_metrics
                        self.logger.info(f"[CHART_BONUS] Disabled: Columns detected {all_mentioned}, respecting explicit column mentions")
                    
                    # Score by query keyword overlap + chart context
                    query_keywords = set(query_lower.replace('_', ' ').split())
                    
                    def relevance_score(col_name):
                        col_keywords = set(col_name.lower().replace('_', ' ').split())
                        overlap = 0
                        
                        # PRIORITY 1: Chart context bonus (highest weight)
                        # Only applied when query has no explicit column mentions
                        if use_chart_bonus and chart_keywords:
                            chart_overlap = len(chart_keywords & col_keywords)
                            if chart_overlap > 0:
                                # Give +100 bonus for matching chart context
                                # This ensures chart-related columns are preferred for general queries
                                overlap += 100 * chart_overlap
                                self.logger.debug(f"[CHART_BONUS] {col_name}: +{100 * chart_overlap} (matched {chart_overlap} chart keywords)")
                        
                        # PRIORITY 2: Query keyword overlap
                        query_overlap = len(query_keywords & col_keywords)
                        overlap += query_overlap
                        
                        # PRIORITY 3: Bonus if full column name appears in query
                        if col_name.lower() in query_lower:
                            overlap += 10
                        
                        return overlap
                    
                    # Sort same-priority candidates by relevance
                    same_priority_candidates.sort(key=lambda x: relevance_score(x[1]), reverse=True)
                    self.logger.info(f"[SELECTION] Multiple candidates at priority={best_priority_score}, selecting most query-relevant")
                    for candidate in same_priority_candidates[:3]:  # Log top 3
                        score = relevance_score(candidate[1])
                        self.logger.info(f"   - {candidate[1]}: relevance_score={score}")
                
                # Extract best candidate (priority_score, column, is_count, aggregation_type, reasoning)
                best_priority_score, best_column, best_is_count, best_agg_type, best_reasoning = same_priority_candidates[0]
                
                self.logger.info(f"[SELECTION] Selected: {best_column} (priority_score={best_priority_score}), "
                               f"is_count={best_is_count}, aggregation={best_agg_type}")
                self.logger.info(f"[SELECTION] Reasoning: {best_reasoning}")
                
                # Override LLM's choice with SmartAggregationDecider result
                target_metric = best_column
                is_count_metric = best_is_count
                recommended_aggregation = best_agg_type
            else:
                self.logger.info(f"[SELECTION] No candidates found, keeping LLM suggestion: {target_metric}")
                recommended_aggregation = None  # No recommendation available
            
            # CRITICAL: Infer date_column from chart cache if LLM didn't detect it
            # This enables temporal aggregation for outlier detection even on non-temporal queries
            inferred_date_column = result.date_column
            if not inferred_date_column:
                selected_chart = state.get("selected_chart", "")
                if selected_chart:
                    try:
                        import os
                        cache_file = "causal_analysis_cache.json"
                        if os.path.exists(cache_file):
                            with open(cache_file, 'r') as f:
                                cache_data = json.load(f)
                            # NEW: Nested structure lookup using workbook_id
                            chart_context = state.get("chart_context", {})
                            workbook_id_nested = chart_context.get("workbook_id")
                            chart_cache = None
                            if workbook_id_nested and isinstance(cache_data, dict) and workbook_id_nested in cache_data:
                                chart_cache = cache_data[workbook_id_nested].get(selected_chart)
                            if not chart_cache and isinstance(cache_data, dict):
                                # Fallback scan across all workbooks
                                for _wb, charts in cache_data.items():
                                    if isinstance(charts, dict) and selected_chart in charts:
                                        chart_cache = charts[selected_chart]
                                        break
                            if chart_cache:
                                x_axis = chart_cache.get("x_axis_detected")
                                if x_axis and x_axis in df.columns:
                                    inferred_date_column = x_axis
                                    self.logger.info(f"[DATE_INFERENCE] Inferred date_column from chart cache: {inferred_date_column}")
                                else:
                                    self.logger.debug(f"[DATE_INFERENCE] x_axis '{x_axis}' from cache not found in CSV columns")
                    except Exception as e:
                        self.logger.debug(f"[DATE_INFERENCE] Could not infer date_column from cache: {e}")
            
            # Store results with priority system overrides
            state["stage1_columns"] = {
                "mentioned_features": result.mentioned_features or [],
                "mentioned_metrics": result.mentioned_metrics or [],
                "target_metric_column": target_metric,  # May be overridden by priority system
                "target_metrics": columns_to_analyze if columns_to_analyze else [],  # UPDATED: Always store list (even single metric)
                "requires_multi_metric_analysis": len(columns_to_analyze) > 1,  # NEW: Flag for multi-metric
                "date_column": inferred_date_column,  # May be inferred from chart cache
                "categorical_columns": result.categorical_columns or [],
                "numeric_columns": result.numeric_columns or [],
                "filter_column": result.filter_column,
                "filter_value": result.filter_value,
                "query_type": result.query_type,
                "is_count_metric": is_count_metric,  # May be overridden by priority system
                "recommended_aggregation": recommended_aggregation,  # NEW: specific aggregation from SmartAgg
                "confidence": result.confidence
            }
            
            # === POST-VALIDATION: Smart correlation column filtering ===
            # For correlation queries, validate and refine numeric_columns selection
            query_lower = query.lower()
            is_correlation_query = any(keyword in query_lower for keyword in ['correlation', 'correlate', 'correlated'])
            
            if is_correlation_query:
                self.logger.info("[CORRELATION_VALIDATION] Detected correlation query, validating numeric_columns...")
                
                extracted_numeric_cols = result.numeric_columns or []
                mentioned_features = result.mentioned_features or []
                mentioned_metrics = result.mentioned_metrics or []
                all_mentioned = mentioned_features + mentioned_metrics
                
                self.logger.info(f"[CORRELATION_VALIDATION] Extracted numeric_columns: {extracted_numeric_cols}")
                self.logger.info(f"[CORRELATION_VALIDATION] Mentioned features: {mentioned_features}")
                self.logger.info(f"[CORRELATION_VALIDATION] Mentioned metrics: {mentioned_metrics}")
                
                # Check if user explicitly asked for "other metrics" or general exploration
                is_general_exploration = any(phrase in query_lower for phrase in [
                    'other metric', 'all metric', 'other column', 'all column', 'what correlate'
                ])
                
                if is_general_exploration:
                    # Scenario 1: User wants correlation with ALL numeric columns
                    self.logger.info("[CORRELATION_VALIDATION] Scenario 1: General exploration - will use all numeric columns")
                    # Keep extracted_numeric_cols as is (LLM should have selected all)
                    final_numeric_cols = extracted_numeric_cols
                    
                elif len(all_mentioned) == 2:
                    # Scenario 2 or 3: User mentioned exactly 2 columns
                    # Validate both are valid numeric columns (not high-cardinality IDs)
                    self.logger.info(f"[CORRELATION_VALIDATION] User mentioned 2 columns: {all_mentioned}")
                    
                    valid_cols = []
                    for col in all_mentioned:
                        if col in numeric_cols:
                            # Check if it's a high-cardinality ID column
                            unique_ratio = df[col].nunique() / len(df)
                            if unique_ratio < 0.9:
                                valid_cols.append(col)
                                self.logger.info(f"[CORRELATION_VALIDATION]   ✓ {col} is valid (unique_ratio={unique_ratio:.2f})")
                            else:
                                self.logger.info(f"[CORRELATION_VALIDATION]   ✗ {col} is high-cardinality ID (unique_ratio={unique_ratio:.2f})")
                        else:
                            self.logger.info(f"[CORRELATION_VALIDATION]   ✗ {col} is not numeric")
                    
                    if len(valid_cols) == 2:
                        # Scenario 3: Both are valid numeric columns → use only these 2
                        final_numeric_cols = valid_cols
                        self.logger.info(f"[CORRELATION_VALIDATION] Scenario 3: Both columns valid - using only: {final_numeric_cols}")
                    else:
                        # Scenario 2: At least one is invalid → fallback to all numeric columns
                        final_numeric_cols = extracted_numeric_cols
                        self.logger.info(f"[CORRELATION_VALIDATION] Scenario 2: Invalid column(s) detected - falling back to all numeric columns")
                        
                else:
                    # Single column or other case → use all numeric columns
                    self.logger.info(f"[CORRELATION_VALIDATION] Using all numeric columns (mentioned={len(all_mentioned)} columns)")
                    final_numeric_cols = extracted_numeric_cols
                
                # Update state with validated numeric columns
                state["stage1_columns"]["numeric_columns"] = final_numeric_cols
                self.logger.info(f"[CORRELATION_VALIDATION] Final numeric_columns: {final_numeric_cols}")
            
            state["stage1_completed"] = True
            state["messages"].append(f"✓ Stage 1: Columns validated (no hallucinations possible)")
            
            self.logger.info(f"✓ Stage 1 Complete:")
            self.logger.info(f"  - Query type: {result.query_type}")
            self.logger.info(f"  - Mentioned features: {result.mentioned_features}")
            self.logger.info(f"  - Mentioned metrics: {result.mentioned_metrics}")
            self.logger.info(f"  - Target metric: {target_metric} (final selection after priority system)")
            self.logger.info(f"  - Is count metric: {is_count_metric} (final value after priority system)")
            self.logger.info(f"  - Date column: {inferred_date_column}{' (inferred from chart)' if inferred_date_column and not result.date_column else ''}")
            self.logger.info(f"  - Confidence: {result.confidence}")
            
        except Exception as e:
            self.logger.error(f"Stage 1 failed: {str(e)}")
            self.logger.error(traceback.format_exc())
            state["errors"].append(f"Stage 1 CRITICAL: {str(e)}")
            state["stage1_completed"] = False  # ← FIXED: Don't mark as complete if failed
            state["stage1_failed"] = True
            state["fatal_error"] = True  # Signal that we cannot proceed
            state["messages"].append(f"❌ Stage 1 failed: {str(e)}")
        
        return state
    
    # ========================================================================
    # STAGE 1.5: VALUE GROUNDING
    # ========================================================================
    
    def _stage1_5_value_grounding_tool(self, state: Dict) -> Dict:
        """
        Stage 1.5: Ground filter values to actual data
        Ensures filter values actually exist in the data
        
        NO HARDCODINGS: Uses actual data values
        """
        self.logger.info("="*80)
        self.logger.info("=== STAGE 1.5: VALUE GROUNDING ===")
        self.logger.info("="*80)
        
        try:
            stage1 = state.get("stage1_columns", {})
            filter_col = stage1.get("filter_column")
            filter_val = stage1.get("filter_value")
            
            if filter_col and filter_val:
                # Get DataFrame from DataManager
                from services.data_manager import get_data_manager
                data_manager = get_data_manager()
                data_id = state.get("data_id")
                df = data_manager.get_data(data_id)
                
                # Get actual values in the column (NO HARDCODING)
                actual_values = df[filter_col].dropna().unique()
                
                # Fuzzy match the filter value
                matched_value = self.fuzzy_matcher.find_best_match(
                    filter_val,
                    [str(v) for v in actual_values],
                    context="value_grounding"
                )
                
                if matched_value:
                    state["stage1_columns"]["filter_value_grounded"] = matched_value
                    self.logger.info(f"✓ Grounded '{filter_val}' → '{matched_value}'")
                    state["messages"].append(f"✓ Filter value grounded: '{filter_val}' → '{matched_value}'")
                else:
                    self.logger.warning(f"⚠️ Could not ground filter value: '{filter_val}'")
                    self.logger.warning(f"   Available values: {list(actual_values)[:10]}")
                    state["messages"].append(f"⚠️ Could not find '{filter_val}' in {filter_col}")
            else:
                self.logger.info("No filter value to ground")
            
            # === NEW: TEMPORAL FILTER EXTRACTION ===
            # Extract temporal filters (month, quarter, year) from query
            # Only run for temporal queries (those with a date column)
            query = state.get("query", "")
            date_column = stage1.get("date_column")
            
            if date_column and query:
                self.logger.info("→ Attempting to extract temporal filter from query...")
                temporal_filter = self._extract_temporal_filter(query)
                
                if temporal_filter:
                    state["temporal_filter"] = temporal_filter
                    self.logger.info(f"✓ Temporal filter extracted: {temporal_filter['type']} = {temporal_filter['value']} ('{temporal_filter['text']}')")
                    state["messages"].append(f"✓ Detected temporal filter: {temporal_filter['text']}")
                else:
                    self.logger.info("No temporal filter found in query (analyzing full time series)")
            
            state["stage1_5_completed"] = True
            
        except Exception as e:
            self.logger.error(f"Stage 1.5 failed: {str(e)}")
            state["errors"].append(f"Stage 1.5: {str(e)}")
            state["stage1_5_completed"] = True  # Continue anyway
        
        return state
    
    # ========================================================================
    # STAGE 2: OPERATION PLANNING
    # ========================================================================
    
    def _stage2_operation_planning_tool(self, state: Dict) -> Dict:
        """
        Stage 2: Operation Planning with sub-intent detection
        Determines WHAT to analyze and HOW
        
        NO HARDCODINGS: Sub-intents chosen based on query patterns, not hardcoded rules
        """
        self.logger.info("="*80)
        self.logger.info("=== STAGE 2: OPERATION PLANNING ===")
        self.logger.info("="*80)
        
        try:
            query = state.get("query", "")
            stage1 = state.get("stage1_columns", {})
            retry_attempt = state.get("retry_attempt", 0)
            retry_corrections = state.get("retry_corrections", [])
            
            # Get available sub-intents (NO HARDCODING)
            available_sub_intents = list(SUB_INTENTS.keys())
            
            # Build sub-intent descriptions for LLM
            sub_intent_desc = []
            for intent, details in SUB_INTENTS.items():
                sub_intent_desc.append(f"- {intent}: {details['description']}")
                sub_intent_desc.append(f"  Examples: {', '.join(details['examples'][:2])}")
            
            sub_intent_info = "\n".join(sub_intent_desc)
            
            # Define Pydantic schema for operation plan
            SubIntentEnum = Literal[tuple(available_sub_intents)]
            
            class OperationPlan(BaseModel):
                """Operation planning with sub-intents"""
                
                # Sub-intents (more specific than general analysis types)
                sub_intents: List[SubIntentEnum] = Field(
                    description="List of specific sub-intents to execute"
                )
                
                # Operation parameters (filled from Stage 1)
                group_by_columns: Optional[List[str]] = Field(
                    None,
                    description="Columns to group by (from mentioned_features for specific queries)"
                )
                
                agg_column: Optional[str] = Field(
                    None,
                    description="Column to aggregate (from target_metric_column)"
                )
                
                agg_functions: List[str] = Field(
                    default=["count", "sum", "mean"],
                    description="Aggregation functions to use"
                )
                
                # For temporal
                comparison_type: Optional[Literal["mom", "qoq", "yoy", "wow", "dod"]] = None
                period_granularity: Optional[str] = None
                
                # For causal
                trigger_causal_analysis: bool = Field(
                    default=False,
                    description="True if should run UniversalCausalAnalyzer"
                )
                
                # Analysis mode (NEW - for 7-layer interpretive analysis)
                analysis_mode: str = Field(
                    default="factual",
                    description="Either 'factual' (state facts) or 'interpretive' (explain why/how with 7-layer analysis)"
                )
                
                analysis_mode_reasoning: Optional[str] = Field(
                    default=None,
                    description="Brief explanation for why factual or interpretive mode was chosen"
                )
                
                confidence: float = Field(ge=0.0, le=1.0)
                reasoning: str = Field(description="Explain why these sub-intents were chosen")
            
            # Build system prompt with retry context
            system_prompt = f"""You are planning data analysis operations based on user query.

Stage 1 Results:
{json.dumps(stage1, indent=2)}

Available Sub-Intents (choose from these):
{sub_intent_info}

Query Intent Classification Guide:

This module provides 7-Layer Explanations for "WHY" and "HOW X AFFECTS Y" questions.
9 sub-intents available (others handled by NL_to_python.py):

AVAILABLE SUB-INTENTS (Choose from these only):
1. general_drivers - Find causal drivers (uses EconML + SHAP) - USE FOR EXPLORATORY
2. specific_feature_impact - How a specific column affects target - USE WHEN COLUMN NAMED
3. multiple_feature_impact - Interaction between 2+ features - USE FOR MULTI-COLUMN IMPACT
4. correlation_analysis - Numeric correlations - USE FOR CORRELATION QUERIES
5. categorical_breakdown - Segment analysis (where changes happened)
6. contribution_analysis - Attribution (positive/negative contributors)
7. period_over_period - Temporal comparison (MoM, QoQ, YoY)
8. temporal_trend - Trend summary (what happened over time)
9. outlier_detection - Identify anomalous data points - USE FOR ANOMALY/OUTLIER QUERIES

Sub-Intent Selection Rules:

1. Specific Feature Impact (User Names Specific Column):
   Pattern: "how [COLUMN] affects", "impact of [COLUMN] on", "[COLUMN] effect on"
   Action: Use "specific_feature_impact", trigger_causal_analysis=FALSE
   Examples: 
   - "how account_manager affects tickets?" → specific_feature_impact
   - "impact of region on sales" → specific_feature_impact
   - "how status affects revenue" → specific_feature_impact
   IMPORTANT: DO NOT use general_drivers when user mentions specific column!

2. Multiple Feature Interaction (User Names 2+ Columns):
   Pattern: "how [COL1] and [COL2] affect", "[COL1] and [COL2] impact on"
   Action: Use "multiple_feature_impact", trigger_causal_analysis=FALSE
   Examples:
   - "how region and product affect sales together" → multiple_feature_impact
   - "combined impact of status and priority" → multiple_feature_impact

3. Correlation Analysis:
   Pattern: "correlation between", "correlation matrix", "correlated with"
   Action: Use "correlation_analysis", trigger_causal_analysis=FALSE
   
   IMPORTANT FOR PARAMETER EXTRACTION:
   - For "correlation between X and other metrics" → agg_column = X (extract X as target)
   - For "correlation between X and Y" → agg_column = X (pick first mentioned column)
   - The agg_column becomes the target metric whose correlations we compute
   
   Examples:
   - "correlation between age_in_hours and other metrics" → correlation_analysis, agg_column = "age_in_hours"
   - "correlation between eng_transfers and Number_Of_Tickets" → correlation_analysis, agg_column = "eng_transfers"
   - "which metrics correlate with sales" → correlation_analysis, agg_column = "sales"

4. General Causal Drivers (Exploratory - No Specific Column Named):
   Pattern: "why", "what causes", "what affects", "find drivers", "spike", "dip"
   Action: Set trigger_causal_analysis=TRUE, use "general_drivers" sub-intent
   Examples: 
   - "why is there a spike in March?" → general_drivers + temporal_trend
   - "what affects tickets?" → general_drivers (no column mentioned)
   - "find drivers of sales" → general_drivers

5. Segment Breakdown (Layer 2: Where It Happened):
   Pattern: "breakdown", "by category", "split by", "distribution"
   Action: Use "categorical_breakdown" sub-intent
   Examples:
   - "breakdown by status" → categorical_breakdown
   - "tickets by region" → categorical_breakdown

6. Contributors (Layer 3: Who Drove It):
   Pattern: "top contributors", "negative impact", "drove growth", "caused decline"
   Action: Use "contribution_analysis" sub-intent
   Examples:
   - "which regions drove growth?" → contribution_analysis
   - "top contributors to decline" → contribution_analysis

7. Temporal Pattern (Layer 5: When It Started):
   Pattern: "MoM", "QoQ", "YoY", "month over month", "quarter over quarter"
   Action: Use "period_over_period" sub-intent
   Examples:
   - "month over month growth" → period_over_period
   - "Q1 vs Q2" → period_over_period

8. Trend Summary (Layer 1: What Happened):
   Pattern: "trends", "over time", "monthly pattern", "show trend"
   Action: Use "temporal_trend" sub-intent
   Examples:
   - "monthly trends" → temporal_trend
   - "sales over time" → temporal_trend

9. Outlier Detection (Anomaly Identification - Records):
   Pattern: "find outliers", "detect anomalies", "identify unusual", "which records are outliers"
   Action: Use "outlier_detection" sub-intent
   Examples:
   - "find outliers in the data" → outlier_detection
   - "detect anomalies" → outlier_detection
   - "which cases are unusual" → outlier_detection
   Note: This identifies unusual RECORDS (individual data points)

10. Temporal Anomaly Detection (Anomaly Identification - Time Periods):
   Pattern: "anomalies in trend", "unusual periods", "which months are anomalous", "find anomalies in time series"
   Action: Use "temporal_anomaly_detection" sub-intent
   Examples:
   - "find anomalies in monthly trend" → temporal_anomaly_detection
   - "detect unusual periods" → temporal_anomaly_detection
   - "which months are anomalous" → temporal_anomaly_detection
   Note: This identifies unusual TIME PERIODS (not individual records)

QUERIES NOT HANDLED HERE (Route to NL_to_python.py):
- Ranking/Top N: "top 5 regions" → NL_to_python
- Percentiles: "90th percentile" → NL_to_python
- Pivot tables: "cross-tab" → NL_to_python

Selection Strategy:
- For "WHY" questions → Always use general_drivers + trigger_causal_analysis=True
- Combine with temporal sub-intents if time dimension mentioned
- Use categorical_breakdown for "where" questions
- Use contribution_analysis for attribution questions

PARAMETERS:
- group_by_columns: Use mentioned_features from Stage 1 (for feature/categorical columns)
- agg_column: Use target_metric_column from Stage 1
- trigger_causal_analysis: True for general_drivers, False otherwise

=============================================================================
ANALYSIS MODE DETECTION (CRITICAL - FOR 7-LAYER INTERPRETIVE ANALYSIS):
=============================================================================

Determine if this query requires FACTUAL or INTERPRETIVE analysis:

FACTUAL MODE: User wants to see numbers/facts WITHOUT interpretation
- User asks to IDENTIFY, DETECT, FIND, LIST, SHOW facts (WHAT questions)
- Keywords: "detect", "find", "identify", "list", "show", "what", "which"
- Examples:
  * "detect unusual periods from march to july" → FACTUAL
  * "what are the outliers" → FACTUAL
  * "which quarters are anomalous in 2025" → FACTUAL
  * "find anomalies in the data" → FACTUAL
  * "list all records with high values" → FACTUAL
- Output: Return the detected anomalies/outliers/facts, no interpretation needed
- analysis_mode: "factual"

INTERPRETIVE MODE: User wants to UNDERSTAND and EXPLAIN the numbers (WHY/HOW questions)
- User asks WHY, HOW, to EXPLAIN, ANALYZE, INTERPRET, or understand REASONS/DRIVERS/CAUSES
- Keywords: "why", "how", "explain", "analyze", "interpret", "understand", "reason", "cause", "driver", "what caused"
- Examples:
  * "why did revenue increase from march to july" → INTERPRETIVE
  * "explain this spike in tickets" → INTERPRETIVE
  * "how did product A perform" → INTERPRETIVE
  * "what caused the drop in sales" → INTERPRETIVE
  * "analyze the trend" → INTERPRETIVE
  * "interpret the results" → INTERPRETIVE
- Output: Provide 7-layer interpretive analysis with drivers, patterns, insights
- analysis_mode: "interpretive"

Key distinction:
- FACTUAL: "What happened?" (state the fact - just the numbers)
- INTERPRETIVE: "Why did it happen?" / "How did it happen?" (explain the reason - provide insights)

CRITICAL DECISION RULE:
- If query contains WHY/HOW/EXPLAIN keywords → analysis_mode = "interpretive"
- If query contains DETECT/FIND/IDENTIFY/LIST/WHAT/WHICH keywords → analysis_mode = "factual"
- Default to "factual" if ambiguous

Return in your response:
- analysis_mode: "factual" or "interpretive"
- analysis_mode_reasoning: Brief 1-sentence explanation of why you chose this mode

"""
            
            # === CORRECTIVE PROMPT: Validator-guided retry ===
            # Check if this is a correction attempt (validator rejected previous plan)
            correction_mode = state.get("correction_mode", False)
            validation_issues = state.get("validation_issues", [])
            validation_suggestions = state.get("validation_suggestions", [])
            
            if correction_mode and validation_issues:
                self.logger.info(f"🔧 Using corrective prompt mode with {len(validation_issues)} issues")
                
                # Build detailed corrective prompt with validator feedback
                issues_text = "\n".join([f"❌ {issue}" for issue in validation_issues])
                suggestions_text = "\n".join([f"✓ {suggestion}" for suggestion in validation_suggestions]) if validation_suggestions else "No specific suggestions provided."
                
                system_prompt += f"""

🚨 CORRECTIVE RETRY - Your previous plan was REJECTED by the validator!

VALIDATION ERRORS FOUND:
{issues_text}

SUGGESTIONS FOR CORRECTION:
{suggestions_text}

CRITICAL RULES TO FOLLOW:
1. If query asks "how [SPECIFIC_COLUMN] affects [metric]":
   - DO NOT use 'general_drivers' sub-intent
   - USE 'categorical_breakdown' or 'specific_feature_impact'
   - Set trigger_causal_analysis = FALSE
   - Include the SPECIFIC_COLUMN in group_by_columns

2. If query asks general "why" or "what affects":
   - USE 'general_drivers' sub-intent
   - Set trigger_causal_analysis = TRUE
   - Do NOT include specific columns in group_by_columns

3. If query has temporal aspect ("over time", "trends", "spike in March"):
   - Add 'temporal_trend' to sub_intents
   - Set date_column appropriately

IMPORTANT: Your previous plan had the above errors. Generate a CORRECTED plan now!
"""
            elif retry_attempt > 0 and retry_corrections:
                # Fallback to old retry mechanism if not in correction mode
                system_prompt += f"""

⚠️ RETRY ATTEMPT #{retry_attempt}
Previous attempt failed. Apply these corrections:
{chr(10).join(['- ' + c for c in retry_corrections])}

CRITICAL: Fix the issues from previous attempt!
"""
            
            # Call LLM
            response = self.llm_client.beta.chat.completions.parse(
                model="gpt-4o-2024-08-06",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Query: {query}"}
                ],
                response_format=OperationPlan
            )
            
            plan = response.choices[0].message.parsed
            
            # === EXTRACT ANALYSIS MODE (NEW - for 7-layer interpretive analysis) ===
            analysis_mode = getattr(plan, 'analysis_mode', 'factual')
            analysis_mode_reasoning = getattr(plan, 'analysis_mode_reasoning', '')
            
            # Store analysis mode in state
            state["analysis_mode"] = analysis_mode
            state["analysis_mode_reasoning"] = analysis_mode_reasoning
            
            self.logger.info(f"✓ ANALYSIS MODE: {analysis_mode}")
            if analysis_mode_reasoning:
                self.logger.info(f"   Reasoning: {analysis_mode_reasoning}")
            
            # Store plan
            state["stage2_plan"] = {
                "sub_intents": plan.sub_intents,
                "group_by_columns": plan.group_by_columns or stage1.get("mentioned_features", []),
                "agg_column": plan.agg_column or stage1.get("target_metric_column"),
                "agg_functions": plan.agg_functions,
                "comparison_type": plan.comparison_type,
                "period_granularity": plan.period_granularity,
                "trigger_causal_analysis": plan.trigger_causal_analysis,
                "confidence": plan.confidence,
                "reasoning": plan.reasoning,
                "is_count_metric": stage1.get("is_count_metric", False),
                "analysis_mode": analysis_mode,  # NEW: Store in plan too
                "analysis_mode_reasoning": analysis_mode_reasoning  # NEW
            }
            
            state["stage2_completed"] = True
            state["messages"].append(f"✓ Stage 2: Plan created - {plan.sub_intents}")
            
            # Clear correction mode flag after generating new plan
            # (Next validation will set it again if plan is still invalid)
            if state.get("correction_mode"):
                state["correction_mode"] = False
                self.logger.info("🔄 Correction mode cleared - new plan generated")
            
            # === NEW: DUAL VALIDATION FOR TEMPORAL COMPARISON ===
            # Trust LLM decisions first, use patterns only as fallback
            
            # Check if LLM identified any temporal sub-intent
            temporal_sub_intents = ['temporal_trend', 'temporal_anomaly_detection', 'period_over_period']
            has_temporal_intent = any(intent in plan.sub_intents for intent in temporal_sub_intents)
            
            if has_temporal_intent:
                # LLM says it's temporal - trust it!
                detected_intents = [intent for intent in temporal_sub_intents if intent in plan.sub_intents]
                self.logger.info(f"✓ LLM DETECTED TEMPORAL INTENT: {detected_intents}")
                
                # Check if specific period comparison (for temporal_trend queries)
                if "temporal_trend" in plan.sub_intents and self._is_specific_period_comparison(query):
                    state["proceed_to_temporal_extraction"] = True
                    state["temporal_validation_reason"] = "llm_temporal_specific"
                elif "temporal_trend" in plan.sub_intents:
                    # General trends, don't need temporal extraction
                    state["proceed_to_temporal_extraction"] = False
                    state["temporal_validation_reason"] = "llm_temporal_general"
                else:
                    # Other temporal intents (period_over_period, temporal_anomaly_detection)
                    # These may benefit from temporal extraction if pattern detected
                    has_temporal_pattern = self._quick_temporal_check(query)
                    state["proceed_to_temporal_extraction"] = has_temporal_pattern
                    state["temporal_validation_reason"] = "llm_temporal_with_pattern" if has_temporal_pattern else "llm_temporal_no_pattern"
            else:
                # LLM didn't identify temporal - check patterns as fallback
                has_temporal_pattern = self._quick_temporal_check(query)
                
                if has_temporal_pattern:
                    self.logger.warning("⚠ PATTERN FALLBACK: Pattern detected but LLM didn't identify temporal intent")
                    state["proceed_to_temporal_extraction"] = True
                    state["temporal_validation_reason"] = "pattern_fallback"
                else:
                    self.logger.info("✗ NOT TEMPORAL: Neither LLM nor patterns suggest temporal comparison")
                    state["proceed_to_temporal_extraction"] = False
                    state["temporal_validation_reason"] = "both_reject"
            
            self.logger.info(f"✓ Stage 2 Complete:")
            self.logger.info(f"  - Sub-intents: {plan.sub_intents}")
            self.logger.info(f"  - Group by: {plan.group_by_columns}")
            self.logger.info(f"  - Aggregate: {plan.agg_column}")
            self.logger.info(f"  - Trigger causal: {plan.trigger_causal_analysis}")
            self.logger.info(f"  - Reasoning: {plan.reasoning}")
            self.logger.info(f"  - Temporal extraction: {state['proceed_to_temporal_extraction']}")
            
            # No more flag setting - routing will use sub_intents directly
            
        except Exception as e:
            self.logger.error(f"Stage 2 failed: {str(e)}")
            self.logger.error(traceback.format_exc())
            state["errors"].append(f"Stage 2: {str(e)}")
            state["stage2_completed"] = True
        
        return state
    
    # ========================================================================
    # NEW: TEMPORAL COMPARISON DETECTION AND VALIDATION
    # ========================================================================
    
    def _quick_temporal_check(self, query: str) -> bool:
        """
        Lightweight pattern matching for temporal comparison queries
        Fast check (<1ms) to filter obvious non-temporal queries
        
        Returns True if query might be temporal comparison
        """
        import re
        
        query_lower = query.lower()
        
        # Pattern 1: "spike/dip/increase/decrease in [period]"
        spike_dip_patterns = [
            r'\b(spike|dip|increase|decrease|drop|rise)\s+(in|during)\s+\w+',
            r'\bwhy.*(spike|dip|increase|decrease)\b',
            r'\b(spike|dip)\s+(in|during|from)\b'
        ]
        
        # Pattern 2: "from [period1] to [period2]"
        comparison_patterns = [
            r'\bfrom\s+\w+\s+to\s+\w+',
            r'\b\w+\s+vs\s+\w+',
            r'\b\w+\s+versus\s+\w+',
            r'\bcompare\s+\w+\s+(and|with)\s+\w+',
        ]
        
        # Pattern 3: Time period mentions
        time_periods = [
            'january', 'february', 'march', 'april', 'may', 'june',
            'july', 'august', 'september', 'october', 'november', 'december',
            'week', 'quarter', 'q1', 'q2', 'q3', 'q4',
            'month', 'year', 'last month', 'this month'
        ]
        
        # Check patterns
        for pattern in spike_dip_patterns + comparison_patterns:
            if re.search(pattern, query_lower):
                # Also check if has time period mention
                if any(period in query_lower for period in time_periods):
                    return True
        
        return False
    
    def _is_specific_period_comparison(self, query: str) -> bool:
        """
        Distinguish between specific period comparison and general trends
        
        Returns:
            True: "why spike in March?" (specific period comparison)
            False: "show trends over time" (general trends)
        """
        query_lower = query.lower()
        
        # Must have BOTH change indicator AND specific period
        change_words = ['spike', 'dip', 'increase', 'decrease', 'drop', 'rise', 
                        'growth', 'decline', 'change', 'why']
        
        specific_periods = ['january', 'february', 'march', 'april', 'may', 'june',
                           'july', 'august', 'september', 'october', 'november', 'december',
                           'week 1', 'week 2', 'q1', 'q2', 'q3', 'q4', 
                           'from ', ' to ']
        
        has_change = any(word in query_lower for word in change_words)
        has_specific_period = any(period in query_lower for period in specific_periods)
        
        # Exclude general trend queries
        general_trend_words = ['show trends', 'trend over', 'monthly trend', 
                              'plot', 'chart', 'graph']
        is_general_trend = any(word in query_lower for word in general_trend_words)
        
        return has_change and has_specific_period and not is_general_trend
    
    def _extract_temporal_filter(self, query: str) -> Optional[Dict]:
        """
        Extract temporal filters (month, quarter, year) from query
        
        Returns:
            Dict with type, value, text if found, None otherwise
            Example: {'type': 'month', 'value': 3, 'text': 'march'}
        """
        import re
        
        query_lower = query.lower()
        
        # Month mapping
        month_map = {
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
        
        # Priority 1: Try PeriodExtractionService if available
        if self.period_extractor:
            try:
                result = self.period_extractor.extract_periods_and_events(query, apply_cleanup=True)
                if result.get('periods'):
                    # Use the first detected period
                    period = result['periods'][0]
                    period_text = period.get('text', '').lower()
                    
                    # Map to our format
                    for month_name, month_num in month_map.items():
                        if month_name in period_text:
                            return {
                                'type': 'month',
                                'value': month_num,
                                'text': month_name.title()
                            }
                    
                    # Check for quarter
                    quarter_match = re.search(r'q([1-4])', period_text)
                    if quarter_match:
                        return {
                            'type': 'quarter',
                            'value': int(quarter_match.group(1)),
                            'text': f"Q{quarter_match.group(1)}"
                        }
                    
                    # Check for year
                    year_match = re.search(r'\b(20\d{2})\b', period_text)
                    if year_match:
                        return {
                            'type': 'year',
                            'value': int(year_match.group(1)),
                            'text': year_match.group(1)
                        }
            except Exception as e:
                self.logger.debug(f"PeriodExtractionService failed, using fallback: {e}")
        
        # Priority 2: Fallback to regex patterns
        # Extract month
        for month_name, month_num in month_map.items():
            if month_name in query_lower:
                return {
                    'type': 'month',
                    'value': month_num,
                    'text': month_name.title()
                }
        
        # Extract quarter (Q1, Q2, Q3, Q4)
        quarter_match = re.search(r'\bq([1-4])\b', query_lower)
        if quarter_match:
            quarter = int(quarter_match.group(1))
            return {
                'type': 'quarter',
                'value': quarter,
                'text': f"Q{quarter}"
            }
        
        # Extract year (2020-2029)
        year_match = re.search(r'\b(20[2-3]\d)\b', query_lower)
        if year_match:
            year = int(year_match.group(1))
            return {
                'type': 'year',
                'value': year,
                'text': str(year)
            }
        
        return None
    
    def _extract_columns_from_chart_name(self, 
                                         chart_name: str, 
                                         df: pd.DataFrame,
                                         numeric_cols: List[str]) -> List[str]:
        """
        Extract likely column names from chart title
        
        Args:
            chart_name: Chart title (e.g., "Number of Tickets Line chart")
            df: DataFrame with all columns
            numeric_cols: List of numeric column names
        
        Returns:
            List of matched column names (max 2)
        
        Examples:
            "Number of Tickets Line chart" → ["Number_Of_Tickets"]
            "Closed Volume & Open volume Brief" → ["Closed_Volume"]
        """
        if not chart_name:
            return []
        
        # Remove common chart type words
        cleaned = chart_name.lower()
        for word in ['chart', 'line', 'bar', 'pie', 'scatter', 'brief', 'dashboard', 'graph', 'plot']:
            cleaned = cleaned.replace(word, '')
        
        # Split into words
        chart_words = set(cleaned.strip().replace('_', ' ').replace('-', ' ').split())
        
        # Remove common stop words
        stop_words = {'of', 'the', 'and', 'in', 'on', 'at', 'to', 'for', 'by', 'with', '&', 'a', 'an'}
        chart_words = chart_words - stop_words
        
        if not chart_words:
            return []
        
        matches = []
        
        # Try matching with all columns first (prioritize numeric)
        for col in numeric_cols:
            col_words = set(col.lower().replace('_', ' ').replace('-', ' ').split())
            overlap = len(chart_words & col_words)
            
            if overlap >= 2:  # At least 2 words match
                matches.append((col, overlap, True))  # True = numeric
        
        # If no numeric matches, try non-numeric columns
        if not matches:
            for col in df.columns:
                if col not in numeric_cols:
                    col_words = set(col.lower().replace('_', ' ').replace('-', ' ').split())
                    overlap = len(chart_words & col_words)
                    
                    if overlap >= 2:
                        matches.append((col, overlap, False))  # False = non-numeric
        
        # If still no matches, try single word matches (but only for numeric)
        if not matches:
            for col in numeric_cols:
                col_words = set(col.lower().replace('_', ' ').replace('-', ' ').split())
                overlap = len(chart_words & col_words)
                
                if overlap >= 1:  # At least 1 word match
                    matches.append((col, overlap, True))
        
        if not matches:
            self.logger.debug(f"[CHART_CONTEXT] No column matches found for chart: '{chart_name}'")
            return []
        
        # Sort by: numeric first, then overlap count
        matches.sort(key=lambda x: (x[2], x[1]), reverse=True)
        
        # Return top 2 matches
        result = [col for col, _, _ in matches[:2]]
        self.logger.info(f"[CHART_CONTEXT] Chart '{chart_name}' matched columns: {result}")
        return result
    
    def _stage1_6_temporal_extraction_tool(self, state: Dict) -> Dict:
        """
        NEW STAGE 1.6: Temporal Entity Extraction
        
        Only runs if dual validation passed in Stage 2
        Extracts specific temporal entities for period comparison
        """
        self.logger.info("="*80)
        self.logger.info("=== STAGE 1.6: TEMPORAL ENTITY EXTRACTION ===")
        self.logger.info("="*80)
        
        try:
            from services.temporal_entity_extractor import TemporalEntityExtractor
            
            query = state.get("query", "")
            
            # Get DataFrame from DataManager
            from services.data_manager import get_data_manager
            data_manager = get_data_manager()
            data_id = state.get("data_id")
            df = data_manager.get_data(data_id)
            
            stage1 = state.get("stage1_columns", {})
            date_column = stage1.get("date_column")
            
            # === SURGICAL FIX: Infer date_column from chart context if None ===
            if date_column is None:
                self.logger.warning("⚠️ date_column is None, attempting to infer from chart context")
                
                # Validate DataFrame exists before inference
                if df is None:
                    self.logger.error("⊘ DataFrame is None, cannot infer date_column")
                    state["has_temporal_comparison"] = False
                    state["temporal_entities"] = {
                        "success": False, 
                        "has_comparison": False,
                        "reason": "DataFrame not available for date column inference"
                    }
                    state["stage1_6_completed"] = True
                    return state
                
                # Get chart context
                selected_chart = state.get("selected_chart", "")
                original_tableau_cols = state.get("original_tableau_columns", {})
                chart_data_columns = state.get("chart_data_columns", [])
                
                if selected_chart and chart_data_columns:
                    self.logger.info(f"[DATE_INFERENCE] Chart: '{selected_chart}'")
                    self.logger.info(f"[DATE_INFERENCE] Chart data columns: {chart_data_columns}")
                    
                    # Identify date columns in the dataframe
                    date_cols = []
                    for col in df.columns:
                        col_lower = col.lower()
                        if any(keyword in col_lower for keyword in ['date', 'time', 'day', 'week', 'month', 'quarter', 'year']):
                            date_cols.append(col)
                    
                    self.logger.info(f"[DATE_INFERENCE] Date columns in data: {date_cols[:10]}")
                    
                    # Find date column from chart context
                    # Priority 1: Exact match with chart data columns
                    for chart_col in chart_data_columns:
                        if chart_col in date_cols:
                            date_column = chart_col
                            self.logger.info(f"✓ [DATE_INFERENCE] Exact match found: {date_column}")
                            break
                    
                    # Priority 2: Check if chart keywords match any date column
                    if date_column is None:
                        chart_keywords = set(selected_chart.lower().replace('_', ' ').replace('-', ' ').split())
                        for date_col in date_cols:
                            col_keywords = set(date_col.lower().replace('_', ' ').split())
                            if chart_keywords & col_keywords:
                                date_column = date_col
                                self.logger.info(f"✓ [DATE_INFERENCE] Fuzzy match found: {date_column}")
                                break
                    
                    # Update state if we found a date column
                    if date_column:
                        stage1["date_column"] = date_column
                        state["stage1_columns"] = stage1
                        self.logger.info(f"✓ [DATE_INFERENCE] Updated stage1 with inferred date_column: {date_column}")
                else:
                    self.logger.warning("[DATE_INFERENCE] No chart context available for inference")
            else:
                self.logger.info(f"[DATE_COLUMN] Using date_column from Stage 1: {date_column}")
            
            # If still None after inference, cannot proceed with temporal extraction
            if date_column is None:
                self.logger.warning("⊘ No date column available for temporal extraction (Stage 1 + inference both failed)")
                state["has_temporal_comparison"] = False
                state["temporal_entities"] = {
                    "success": False, 
                    "has_comparison": False,
                    "reason": "No date column available - Stage 1 did not identify a date column and chart context inference failed"
                }
                state["stage1_6_completed"] = True
                return state
            
            # Check if this is a retry
            retry_count = state.get("temporal_retry_count", 0)
            retry_corrections = state.get("temporal_retry_corrections", [])
            
            if retry_count > 0:
                self.logger.warning(f"🔄 TEMPORAL RETRY ATTEMPT #{retry_count}")
                self.logger.info(f"Corrections: {retry_corrections}")
            
            # === DIAGNOSTIC LOGGING START ===
            self.logger.info(f"[STAGE1.6_DEBUG] About to create TemporalEntityExtractor")
            self.logger.info(f"[STAGE1.6_DEBUG] Query: '{query}'")
            self.logger.info(f"[STAGE1.6_DEBUG] DataFrame shape: {df.shape}")
            self.logger.info(f"[STAGE1.6_DEBUG] DataFrame columns (first 15): {list(df.columns)[:15]}")
            self.logger.info(f"[STAGE1.6_DEBUG] date_column value: '{date_column}'")
            self.logger.info(f"[STAGE1.6_DEBUG] date_column in df.columns: {date_column in df.columns if date_column else 'N/A'}")
            if date_column and date_column in df.columns:
                self.logger.info(f"[STAGE1.6_DEBUG] df['{date_column}'].dtype: {df[date_column].dtype}")
                self.logger.info(f"[STAGE1.6_DEBUG] df['{date_column}'].shape: {df[date_column].shape}")
                self.logger.info(f"[STAGE1.6_DEBUG] df['{date_column}'].isna().sum(): {df[date_column].isna().sum()}")
                self.logger.info(f"[STAGE1.6_DEBUG] df['{date_column}'].head(3): {df[date_column].head(3).tolist()}")
            self.logger.info(f"[STAGE1.6_DEBUG] llm_client type: {type(self.llm_client)}")
            self.logger.info(f"[STAGE1.6_DEBUG] llm_client is None: {self.llm_client is None}")
            # === DIAGNOSTIC LOGGING END ===
            
            # Extract temporal entities
            extractor = TemporalEntityExtractor(
                query=query,
                df=df,
                date_column=date_column,
                llm_client=self.llm_client,
                logger=self.logger,
                retry_corrections=retry_corrections
            )
            
            self.logger.info(f"[STAGE1.6_DEBUG] TemporalEntityExtractor created, calling extract()...")
            result = extractor.extract()
            self.logger.info(f"[STAGE1.6_DEBUG] extract() returned, success={result.get('success')}, has_comparison={result.get('has_comparison')}")
            
            if result['success'] and result['has_comparison']:
                self.logger.info("✓ Temporal entities extracted successfully")
                state["has_temporal_comparison"] = True
                state["temporal_entities"] = result
            else:
                self.logger.warning(f"⊘ Temporal extraction failed: {result.get('reason', 'Unknown')}")
                state["has_temporal_comparison"] = False
                state["temporal_entities"] = result
        
        except Exception as e:
            self.logger.error(f"[STAGE1.6_DEBUG] Exception in Stage 1.6: {e}")
            self.logger.error(f"[STAGE1.6_DEBUG] Exception type: {type(e).__name__}")
            import traceback
            self.logger.error(f"[STAGE1.6_DEBUG] Full traceback:")
            self.logger.error(traceback.format_exc())
            state["has_temporal_comparison"] = False
        
        state["stage1_6_completed"] = True
        return state
    
    def _stage1_7_temporal_validation_tool(self, state: Dict) -> Dict:
        """
        NEW STAGE 1.7: Temporal Validation
        
        Validates extracted entities and creates period filters
        Decides whether to retry or fallback
        """
        self.logger.info("="*80)
        self.logger.info("=== STAGE 1.7: TEMPORAL VALIDATION ===")
        self.logger.info("="*80)
        
        try:
            from services.temporal_validator import TemporalValidator
            
            temporal_entities = state.get("temporal_entities", {})
            
            # Get DataFrame from DataManager
            from services.data_manager import get_data_manager
            data_manager = get_data_manager()
            data_id = state.get("data_id")
            df = data_manager.get_data(data_id)
            
            stage1 = state.get("stage1_columns", {})
            date_column = stage1.get("date_column")
            
            validator = TemporalValidator(
                temporal_entities=temporal_entities,
                df=df,
                date_column=date_column,
                logger=self.logger
            )
            
            validation_result = validator.validate_and_resolve()
            
            if validation_result['success']:
                # Validation passed
                self.logger.info("✓ Temporal validation PASSED")
                state["temporal_comparison_validated"] = True
                state["temporal_validation_failed"] = False
                state["period_filters"] = {
                    'period1_filter': validation_result['period1_filter'].tolist(),  # Convert Series to list for serialization
                    'period2_filter': validation_result['period2_filter'].tolist(),  # Convert Series to list for serialization
                    'period1_label': validation_result['period1_label'],
                    'period2_label': validation_result['period2_label'],
                    'period1_count': validation_result['period1_count'],
                    'period2_count': validation_result['period2_count'],
                }
                
                # CRITICAL: Store resolved period details for temporal pattern calculation
                if 'period1_details' in validation_result:
                    state['temporal_entities']['period1'] = validation_result['period1_details']
                if 'period2_details' in validation_result:
                    state['temporal_entities']['period2'] = validation_result['period2_details']
                
                self.logger.info(f"[STAGE1.7] Stored period1_details: {validation_result.get('period1_details')}")
                self.logger.info(f"[STAGE1.7] Stored period2_details: {validation_result.get('period2_details')}")
            else:
                # Validation failed
                error_type = validation_result.get('error_type')
                error_message = validation_result.get('message')
                
                self.logger.warning(f"✗ Temporal validation FAILED: {error_type}")
                self.logger.warning(f"   Error: {error_message}")
                
                # Determine if retry is possible
                can_retry = self._can_retry_temporal(error_type, state)
                
                if can_retry:
                    self.logger.info("→ Retry is possible - will retry Stage 1.6")
                    state["temporal_validation_failed"] = True
                    state["temporal_retry_corrections"] = validation_result.get('corrections', [])
                else:
                    self.logger.warning("→ Retry not possible - falling back to standard flow")
                    state["temporal_validation_failed"] = True
                    state["temporal_retry_count"] = 999  # Force fallback
                
                state["temporal_comparison_validated"] = False
                state["validation_error"] = validation_result
        
        except Exception as e:
            self.logger.error(f"Stage 1.7 error: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            state["temporal_comparison_validated"] = False
            state["temporal_validation_failed"] = True
        
        state["stage1_7_completed"] = True
        return state
    
    def _temporal_comparison_execution_tool(self, state: Dict) -> Dict:
        """
        NEW NODE: Temporal Comparison Execution
        
        Executes temporal comparison analysis as a proper workflow node.
        This follows LangGraph best practices - execution happens in nodes, not routing.
        """
        self.logger.info("="*80)
        self.logger.info("=== TEMPORAL COMPARISON EXECUTION ===")
        self.logger.info("="*80)
        
        try:
            # Call the existing temporal comparison logic
            state = self._execute_temporal_comparison(state)
            state["temporal_execution_completed"] = True
            
            if state.get("completed"):
                self.logger.info("✓ Temporal comparison execution succeeded")
            else:
                self.logger.warning("✗ Temporal comparison execution failed")
        
        except Exception as e:
            self.logger.error(f"Temporal comparison execution error: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            state["temporal_execution_completed"] = True
            state["has_temporal_comparison"] = False
        
        return state
    
    def _can_retry_temporal(self, error_type: str, state: Dict) -> bool:
        """
        Decide if temporal extraction should be retried based on error type
        """
        # Retryable errors (LLM might do better with corrections)
        retryable_errors = {
            'PERIOD_NOT_FOUND',
            'AMBIGUOUS_PERIOD',
            'INVALID_DATE_RANGE',
        }
        
        # Non-retryable errors (fundamental issues)
        non_retryable_errors = {
            'GRANULARITY_MISMATCH',
            'INSUFFICIENT_DATA',
            'NO_DATE_COLUMN',
        }
        
        if error_type in non_retryable_errors:
            return False
        
        if error_type in retryable_errors:
            # Only retry if haven't tried too many times
            return state.get("temporal_retry_count", 0) < 2
        
        # Unknown error - try once
        return state.get("temporal_retry_count", 0) == 0
    
    def _late_temporal_detection(self, query: str, stage2_plan: dict) -> dict:
        """
        LATE DETECTION: Deep check if query is actually temporal
        despite missing earlier detection
        
        Uses LLM for more thorough analysis than lightweight check
        """
        prompt = f"""
Analyze if this query requires SPECIFIC PERIOD COMPARISON (not just general trends).

QUERY: "{query}"

STAGE 2 PLAN:
Sub-intents: {stage2_plan.get('sub_intents', [])}

DETECTION CRITERIA:
A query requires specific period comparison if it asks about:
1. WHY a metric changed in a specific period ("why dip last month?", "spike in Q2?")
2. Comparison between two specific periods ("from June to July")
3. What drove a change at a specific time ("what caused increase in March?")

NOT period comparison if:
- General trends ("show trends over time", "monthly analysis")
- Just plotting ("plot monthly tickets")
- General patterns ("seasonal trends")

EXAMPLES:
✓ "why dip last month?" → YES (specific period: last month)
✓ "spike in Q2?" → YES (specific period: Q2)
✓ "what happened in March?" → YES if asking about change
✓ "from June to July?" → YES (explicit comparison)
✗ "show monthly trends" → NO (general trends)
✗ "plot over time" → NO (general plotting)
✗ "seasonal patterns" → NO (general patterns)

Return JSON:
{{
    "is_temporal_comparison": true/false,
    "specific_periods_mentioned": ["period1", "period2"] or [],
    "confidence": 0.0-1.0,
    "reasoning": "explain your decision",
    "missed_reason": "why might this have been missed in Stage 2?"
}}
"""
        
        try:
            response = self.llm_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.0
            )
            
            result = json.loads(response.choices[0].message.content)
            
            return {
                'is_temporal': result.get('is_temporal_comparison', False),
                'confidence': result.get('confidence', 0.0),
                'reason': result.get('reasoning', ''),
                'missed_reason': result.get('missed_reason', ''),
                'periods': result.get('specific_periods_mentioned', [])
            }
        
        except Exception as e:
            self.logger.error(f"Late detection failed: {e}")
            return {'is_temporal': False, 'confidence': 0.0, 'reason': str(e)}
    
    # ========================================================================
    # OUTLIER DETECTION EXECUTION
    # ========================================================================
    
    def _outlier_detection_execution_tool(self, state: Dict) -> Dict:
        """
        NEW NODE: Outlier Detection Execution
        
        Executes outlier detection using multi-layer approach:
        - Layer 1: Modified Z-Score (MAD) for univariate
        - Layer 2: Isolation Forest for multivariate
        - Layer 3: Business logic validation
        
        LOGIC MIRRORS TEMPORAL ANOMALY DETECTION:
        - Prefers chart data (aggregated) over CSV (raw records)
        - Supports multi-metric analysis (all Y-axes when none specified)
        - Uses SmartAggregation decisions
        """
        self.logger.info("="*80)
        self.logger.info("=== OUTLIER DETECTION EXECUTION ===")
        self.logger.info("="*80)
        
        try:
            stage1 = state.get("stage1_columns", {})
            stage2 = state.get("stage2_plan", {})
            
            # REPLICATE TEMPORAL ANOMALY LOGIC: Get metric column(s)
            target_metrics = stage1.get("target_metrics")
            requires_multi_metric = stage1.get("requires_multi_metric_analysis", False)
            
            # Determine metrics to analyze
            if requires_multi_metric and target_metrics:
                metrics_to_analyze = target_metrics
                self.logger.info(f"[MULTI_METRIC] Analyzing {len(metrics_to_analyze)} metrics: {metrics_to_analyze}")
            else:
                # Single metric
                metric_column = stage2.get("agg_column") or stage1.get("target_metric_column")
                metrics_to_analyze = [metric_column] if metric_column else []
            
            if not metrics_to_analyze:
                self.logger.warning("[OUTLIER] No specific metrics detected from query")
                
                # PRIORITY 1: Try to get Y-axes from chart context
                chart_context = state.get("chart_context", {})
                y_axes_detected = chart_context.get("y_axes_detected", [])
                
                if y_axes_detected:
                    self.logger.info(f"[OUTLIER] Found {len(y_axes_detected)} Y-axes in chart context: {y_axes_detected}")
                    
                    # Try to match columns mentioned in query with Y-axes
                    query = state.get("query", "").lower()
                    query_words = set(query.split())
                    
                    matched_y_axes = []
                    for y_axis in y_axes_detected:
                        # Check if Y-axis name appears in query (case-insensitive, fuzzy match)
                        y_axis_words = set(y_axis.lower().replace('_', ' ').split())
                        if y_axis_words & query_words:  # Intersection check
                            matched_y_axes.append(y_axis)
                    
                    if matched_y_axes:
                        self.logger.info(f"[OUTLIER] Matched Y-axes from query: {matched_y_axes}")
                        metrics_to_analyze = matched_y_axes
                    else:
                        self.logger.info(f"[OUTLIER] No Y-axes matched query, using ALL Y-axes: {y_axes_detected}")
                        metrics_to_analyze = y_axes_detected
                    
                    # Continue with multi-metric analysis path (don't execute the fallback below)
                    self.logger.info(f"[OUTLIER] Proceeding with chart-based analysis for {len(metrics_to_analyze)} metrics")
                    
                else:
                    # FALLBACK: Use full CSV for multi-feature outlier detection
                    self.logger.warning("[OUTLIER] No Y-axes in chart context - falling back to full CSV multi-feature detection")
                    
                    # Get DataFrame from DataManager
                    from services.data_manager import get_data_manager
                    data_manager = get_data_manager()
                    data_id = state.get("data_id")
                    df = data_manager.get_data(data_id)
                    
                    # Get features for analysis
                    features = stage1.get("mentioned_features", [])
                    if not features:
                        features = []
                        for col in df.columns:
                            if pd.api.types.is_numeric_dtype(df[col]) or df[col].dtype == 'object':
                                features.append(col)
                
                self.logger.info(f"[OUTLIER] Features for analysis: {features}")
                
                # Initialize outlier detection service
                outlier_service = OutlierDetectionService(logger=self.logger)
                
                # Run detection
                results = outlier_service.detect_outliers(
                    df=df,
                    features=features,
                    contamination=0.05,
                    z_threshold=3.5,
                    aggregation_method=None,
                    metric_column=None
                )
                
                if results.get("success"):
                    self.logger.info(f"✓ Outlier detection complete - found {results['total_outliers']} outliers")
                    
                    # Full CSV mode - always use row-based formatting
                    formatted_result = self._format_outlier_results(
                        results=results,
                        df=df,
                        data_source="csv_data",  # Explicitly CSV data
                        date_column=None,
                        metric_column=None,
                        aggregation=None
                    )
                    state["final_result"] = formatted_result
                    state["completed"] = True
                else:
                    self.logger.error(f"✗ Outlier detection failed: {results.get('error')}")
                    state["completed"] = False
                    state["errors"].append(f"Outlier detection: {results.get('error')}")
                
                state["outlier_execution_completed"] = True
                return state
            
            # MULTI-METRIC OUTLIER DETECTION (like temporal anomaly)
            # Get temporal filter
            temporal_filter = state.get("temporal_filter")
            query = state.get("query", "")
            
            # Get date column
            date_column = stage1.get("date_column")
            
            # Initialize outlier detection service
            outlier_service = OutlierDetectionService(logger=self.logger)
            
            # Store results per metric with metadata for formatting
            all_results = {}
            result_metadata = {}  # Track data_source, date_column, df for each metric
            
            for metric_column in metrics_to_analyze:
                self.logger.info(f"\n{'='*60}")
                self.logger.info(f"Analyzing metric: {metric_column}")
                self.logger.info(f"{'='*60}")
                
                # Smart data source selection (SAME AS TEMPORAL ANOMALY)
                df, data_source, mapped_metric = self._select_data_source(state, metric_column, temporal_filter)
                
                self.logger.info(f"Dataset shape: {df.shape}")
                self.logger.info(f"Data source: {data_source}")
                self.logger.info(f"Metric column: {mapped_metric}")
                
                # Determine aggregation method (SAME AS TEMPORAL ANOMALY)
                # PRIORITY: Use Stage1's SmartAggregation recommendation if available
                recommended_agg = stage1.get("recommended_aggregation")
                
                if recommended_agg:
                    # Map SmartAgg recommendation to outlier service format
                    agg_mapping = {
                        'SUM': 'sum',
                        'AVG': 'mean',
                        'MEAN': 'mean',
                        'COUNT': 'count',
                        'COUNT_DISTINCT': 'count_distinct',
                        'NUNIQUE': 'count_distinct',
                        'MIN': 'min',
                        'MAX': 'max',
                        'MEDIAN': 'median'
                    }
                    aggregation = agg_mapping.get(recommended_agg, 'sum')
                    self.logger.info(f"[SMART_AGG] Using Stage1 recommendation: {recommended_agg} → {aggregation}")
                else:
                    # Fallback to Stage2's agg_functions
                    agg_functions = stage2.get("agg_functions", ["sum"])
                    aggregation = agg_functions[0] if agg_functions else "sum"
                    
                    # Check if this is a count metric
                    is_count_metric = stage1.get("is_count_metric", False)
                    if is_count_metric and aggregation.upper() not in ["COUNT_DISTINCT", "NUNIQUE"]:
                        aggregation = "count"
                    
                    self.logger.info(f"[FALLBACK] Using Stage2 agg_functions: {aggregation}")
                
                # If using chart data, aggregation is already done by Tableau
                if data_source == "chart_data":
                    aggregation = "sum"  # Just sum the pre-aggregated values
                    self.logger.info(f"[CHART_DATA] Using SUM aggregation (data already aggregated by Tableau)")
                
                self.logger.info(f"Date column: {date_column if date_column else 'None'}")
                self.logger.info(f"Aggregation: {aggregation}")
                
                # Determine features to analyze
                if data_source == "chart_data":
                    # For chart data, analyze the specific metric column
                    features = [mapped_metric]
                else:
                    # For CSV data, can analyze multiple features
                    features = stage1.get("mentioned_features", [])
                    if not features:
                        features = [mapped_metric]
                
                self.logger.info(f"Features: {features}")
                
                # CRITICAL FIX: Aggregate data by date column when:
                # 1. Date column exists AND
                # 2. Data source is full CSV (not pre-aggregated chart data) AND
                # 3. Either: temporal keywords were used OR chart context suggests temporal analysis
                # This makes outlier detection show PERIODS not ROWS (matching chart visualization)
                should_aggregate = (
                    date_column and 
                    date_column in df.columns and 
                    data_source == "full_csv"  # Only aggregate raw CSV, not pre-aggregated chart data
                )
                
                if should_aggregate:
                    self.logger.info(f"[CHART_AGGREGATION] Aggregating CSV data by {date_column} to match chart visualization")
                    
                    # Aggregate using same logic as temporal anomaly detection
                    df_agg = self._aggregate_for_outlier_detection(
                        df=df,
                        date_column=date_column,
                        metric_column=mapped_metric,
                        aggregation=aggregation
                    )
                    
                    if df_agg is not None and len(df_agg) > 0:
                        self.logger.info(f"[CHART_AGGREGATION] Aggregated {len(df)} rows to {len(df_agg)} periods")
                        df = df_agg
                        # After aggregation, analyze only the aggregated metric
                        features = [mapped_metric]
                        # Mark this as aggregated data for proper formatting
                        data_source = "aggregated_temporal"
                    else:
                        self.logger.warning(f"[CHART_AGGREGATION] Aggregation failed, using raw data")
                
                # Run detection
                results = outlier_service.detect_outliers(
                    df=df,
                    features=features,
                    contamination=0.05,
                    z_threshold=3.5,
                    aggregation_method=aggregation,
                    metric_column=mapped_metric
                )
                
                all_results[metric_column] = results
                
                # Store metadata for formatting (CRITICAL FIX)
                result_metadata[metric_column] = {
                    'data_source': data_source,
                    'date_column': date_column,
                    'mapped_metric': mapped_metric,
                    'df': df,
                    'aggregation': aggregation
                }
            
            # Check if any succeeded
            successful_results = {k: v for k, v in all_results.items() if v.get("success")}
            
            if not successful_results:
                self.logger.error("✗ All metrics failed outlier detection")
                state["outlier_execution_completed"] = True
                state["completed"] = False
                state["errors"].append("Outlier detection failed for all metrics")
                return state
            
            # Format results
            if len(successful_results) == 1:
                # Single metric - use simple format
                metric_name = list(successful_results.keys())[0]
                results = successful_results[metric_name]
                metadata = result_metadata[metric_name]
                
                # Pass metadata for proper formatting (CRITICAL FIX)
                formatted_result = self._format_outlier_results(
                    results=results,
                    df=metadata['df'],
                    data_source=metadata['data_source'],
                    date_column=metadata['date_column'],
                    metric_column=metadata['mapped_metric'],
                    aggregation=metadata['aggregation']
                )
            else:
                # Multi-metric - format separately with metadata
                formatted_result = self._format_multi_metric_outlier_results(
                    successful_results=successful_results,
                    all_results=all_results,
                    result_metadata=result_metadata
                )
            
            state["final_result"] = formatted_result
            state["completed"] = True
            state["outlier_execution_completed"] = True
            
            total_outliers = sum(r.get("total_outliers", 0) for r in successful_results.values())
            self.logger.info(f"✓ Multi-metric outlier detection complete - found {total_outliers} total outliers across {len(successful_results)} metrics")
            
        except Exception as e:
            self.logger.error(f"Outlier detection execution error: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            state["outlier_execution_completed"] = True
            state["has_outlier_detection"] = False
            state["completed"] = False
            state["errors"].append(f"Outlier execution: {str(e)}")
        
        return state
    
    def _format_outlier_results(self, 
                                 results: Dict, 
                                 df: pd.DataFrame,
                                 data_source: str,
                                 date_column: Optional[str],
                                 metric_column: Optional[str],
                                 aggregation: Optional[str]) -> str:
        """
        Format outlier detection results for user display.
        
        CRITICAL: Different formatting for chart data vs CSV data
        - Chart data / Aggregated temporal: Show as PERIODS (like temporal anomaly detection)
        - CSV data: Show as ROWS (traditional outlier detection)
        
        Args:
            results: Outlier detection results from OutlierDetectionService
            df: DataFrame used for detection
            data_source: "chart_data" or "csv_data" or "full_csv" or "aggregated_temporal"
            date_column: Date column name (if temporal data)
            metric_column: Metric column being analyzed
            aggregation: Aggregation method used
        """
        outliers = results.get("outliers", [])
        summary = results.get("detection_summary", {})
        
        if not outliers:
            return "No significant outliers detected in the dataset."
        
        # Determine if we should use temporal/chart formatting
        # aggregated_temporal means we aggregated raw CSV by date - show as periods!
        use_temporal_format = ((data_source == "chart_data" or data_source == "aggregated_temporal") 
                              and date_column is not None)
        
        if use_temporal_format:
            # CHART DATA FORMATTING (like temporal anomaly detection)
            return self._format_outlier_results_chart_data(
                outliers=outliers,
                summary=summary,
                df=df,
                date_column=date_column,
                metric_column=metric_column,
                aggregation=aggregation,
                total_outliers=results['total_outliers']
            )
        else:
            # CSV DATA FORMATTING (traditional row-based)
            return self._format_outlier_results_csv_data(
                outliers=outliers,
                summary=summary,
                total_outliers=results['total_outliers']
            )
    
    def _format_outlier_results_chart_data(self,
                                           outliers: List[Dict],
                                           summary: Dict,
                                           df: pd.DataFrame,
                                           date_column: str,
                                           metric_column: str,
                                           aggregation: str,
                                           total_outliers: int) -> str:
        """
        Format outlier results for CHART DATA (aggregated/temporal data).
        Mirrors temporal anomaly detection format - shows PERIODS not rows.
        """
        lines = []
        lines.append(f"**Outlier Detection Results:**\n")
        lines.append(f"Identified {total_outliers} outlier period(s) in the aggregated data.\n")
        lines.append(f"**Detection Method:** {summary.get('univariate_method')} + {summary.get('multivariate_method')}\n")
        lines.append(f"**Time Periods Analyzed:** {summary.get('dataset_size')}\n")
        lines.append(f"**Date Column:** {date_column}\n")
        lines.append(f"**Metric Column:** {metric_column}\n")
        lines.append(f"**Aggregation:** {aggregation}\n\n")
        
        lines.append("**Outlier Periods:**\n")
        
        # Calculate baseline statistics for deviation
        metric_values = df[metric_column].dropna()
        median_value = metric_values.median()
        mean_value = metric_values.mean()
        
        # Show all outliers (sorted by confidence and anomaly score - already done by service)
        for i, outlier in enumerate(outliers, 1):
            row_data = outlier['row_data']
            
            # Extract temporal information
            if date_column in row_data:
                period_value = row_data[date_column]
                
                # Format period for display
                period_display = self._format_period_for_display(period_value)
            else:
                period_display = f"Period {outlier['row_index']}"
            
            # Get actual value
            actual_value = row_data.get(metric_column, 0)
            
            # Calculate deviation from median/mean
            deviation_from_median = actual_value - median_value
            if median_value != 0:
                deviation_pct = (deviation_from_median / median_value) * 100
            else:
                deviation_pct = 0
            
            # Determine type (spike or dip)
            outlier_type = "SPIKE" if actual_value > median_value else "DIP"
            
            lines.append(f"{i}. **{period_display}** ({outlier_type})")
            lines.append(f"   **Value:** {actual_value:,.2f}")
            lines.append("")  # Blank line between outliers
        
        return "\n".join(lines)
    
    def _format_outlier_results_csv_data(self,
                                         outliers: List[Dict],
                                         summary: Dict,
                                         total_outliers: int) -> str:
        """
        Format outlier results for CSV DATA (raw records).
        Traditional row-based outlier detection format.
        """
        lines = []
        lines.append(f"**Outlier Detection Results:**\n")
        lines.append(f"Identified {total_outliers} outlier(s) using ensemble detection methods.\n")
        lines.append(f"**Detection Method:** {summary.get('univariate_method')} + {summary.get('multivariate_method')}\n")
        lines.append(f"**Dataset Size:** {summary.get('dataset_size')} rows\n")
        lines.append(f"**Features Analyzed:** {summary.get('features_analyzed')}\n\n")
        
        lines.append("**Detected Outliers:**\n")
        
        # Show all outliers (sorted by confidence and anomaly score)
        for i, outlier in enumerate(outliers, 1):
            lines.append(f"{i}. **Row Index {outlier['row_index']}** (Confidence: {outlier['confidence']})")
            lines.append(f"   Anomaly Score: {outlier['anomaly_score']:.2f}")
            
            # Show reasons
            if outlier['reasons']:
                lines.append("   **Why it's an outlier:**")
                for reason in outlier['reasons'][:3]:  # Top 3 reasons
                    lines.append(f"   - {reason}")
            
            # Show top contributing features
            if outlier['feature_contributions']:
                top_contributors = sorted(
                    outlier['feature_contributions'].items(),
                    key=lambda x: abs(x[1]),
                    reverse=True
                )[:3]
                
                lines.append("   **Top Contributing Features:**")
                for feature, score in top_contributors:
                    lines.append(f"   - {feature}: Z-score = {score:.2f}")
            
            lines.append("")  # Blank line between outliers
        
        return "\n".join(lines)
    
    def _format_period_for_display(self, period_value) -> str:
        """
        Format period value for display (handles various date formats).
        Mirrors temporal anomaly detection's period formatting.
        """
        try:
            # Handle pandas Timestamp
            if hasattr(period_value, 'strftime'):
                return period_value.strftime('%B %Y')  # e.g., "March 2024"
            
            # Handle string dates
            if isinstance(period_value, str):
                # Try to parse as datetime
                try:
                    import pandas as pd
                    dt = pd.to_datetime(period_value)
                    return dt.strftime('%B %Y')
                except:
                    # Return as-is if can't parse
                    return str(period_value)
            
            # Handle numeric or other types
            return str(period_value)
            
        except Exception as e:
            self.logger.warning(f"[FORMAT] Could not format period '{period_value}': {e}")
            return str(period_value)
    
    def _format_multi_metric_outlier_results(self, 
                                            successful_results: Dict, 
                                            all_results: Dict,
                                            result_metadata: Dict) -> str:
        """
        Format multi-metric outlier detection results.
        Uses metadata to determine chart vs CSV formatting per metric.
        """
        lines = []
        lines.append("**Multi-Metric Outlier Detection Results:**\n")
        lines.append(f"Analyzed {len(all_results)} metrics separately:\n\n")
        
        for metric_name, results in all_results.items():
            if metric_name not in successful_results:
                continue
            
            lines.append(f"\n{'='*60}")
            lines.append(f"**Metric: {metric_name}**")
            lines.append(f"{'='*60}\n")
            
            outliers = results.get("outliers", [])
            summary = results.get("detection_summary", {})
            
            if not outliers:
                lines.append("No significant outliers detected.\n")
                continue
            
            # Get metadata for this metric
            metadata = result_metadata.get(metric_name, {})
            data_source = metadata.get('data_source', 'csv_data')
            date_column = metadata.get('date_column')
            metric_column = metadata.get('mapped_metric', metric_name)
            df = metadata.get('df')
            aggregation = metadata.get('aggregation')
            
            # Determine formatting type
            # aggregated_temporal means we aggregated CSV by date - show as periods!
            use_temporal_format = ((data_source == "chart_data" or data_source == "aggregated_temporal") 
                                  and date_column is not None)
            
            if use_temporal_format and df is not None:
                # CHART DATA FORMAT
                lines.append(f"Identified {results['total_outliers']} outlier period(s).\n")
                lines.append(f"**Detection Method:** {summary.get('univariate_method')} + {summary.get('multivariate_method')}")
                lines.append(f"**Time Periods Analyzed:** {summary.get('dataset_size')}\n")
                
                lines.append("**Outlier Periods:**\n")
                
                # Calculate baseline
                metric_values = df[metric_column].dropna()
                median_value = metric_values.median()
                
                for i, outlier in enumerate(outliers[:10], 1):  # Top 10
                    row_data = outlier['row_data']
                    
                    # Extract period
                    if date_column in row_data:
                        period_value = row_data[date_column]
                        period_display = self._format_period_for_display(period_value)
                    else:
                        period_display = f"Period {outlier['row_index']}"
                    
                    actual_value = row_data.get(metric_column, 0)
                    deviation = actual_value - median_value
                    deviation_pct = (deviation / median_value * 100) if median_value != 0 else 0
                    outlier_type = "SPIKE" if actual_value > median_value else "DIP"
                    
                    lines.append(f"{i}. **{period_display}** ({outlier_type})")
                    lines.append(f"   **Value:** {actual_value:,.2f}")
                    lines.append("")
                
                if len(outliers) > 10:
                    lines.append(f"... and {len(outliers) - 10} more outlier periods\n")
            else:
                # CSV DATA FORMAT
                lines.append(f"Identified {results['total_outliers']} outlier(s).\n")
                lines.append(f"**Detection Method:** {summary.get('univariate_method')} + {summary.get('multivariate_method')}")
                lines.append(f"**Dataset Size:** {summary.get('dataset_size')} rows")
                lines.append(f"**Features Analyzed:** {summary.get('features_analyzed')}\n")
                
                lines.append("**Detected Outliers:**\n")
                
                for i, outlier in enumerate(outliers[:10], 1):  # Show top 10 per metric
                    lines.append(f"{i}. **Row Index {outlier['row_index']}** (Confidence: {outlier['confidence']})")
                    lines.append(f"   Anomaly Score: {outlier['anomaly_score']:.2f}")
                    
                    if outlier['reasons']:
                        lines.append("   **Why it's an outlier:**")
                        for reason in outlier['reasons'][:3]:
                            lines.append(f"   - {reason}")
                    
                    if outlier['feature_contributions']:
                        top_contributors = sorted(
                            outlier['feature_contributions'].items(),
                            key=lambda x: abs(x[1]),
                            reverse=True
                        )[:3]
                        
                        lines.append("   **Top Contributing Features:**")
                        for feature, score in top_contributors:
                            lines.append(f"   - {feature}: Z-score = {score:.2f}")
                    
                    lines.append("")
                
                if len(outliers) > 10:
                    lines.append(f"... and {len(outliers) - 10} more outliers\n")
        
        return "\n".join(lines)
    
    def _aggregate_for_outlier_detection(self,
                                          df: pd.DataFrame,
                                          date_column: str,
                                          metric_column: str,
                                          aggregation: str) -> Optional[pd.DataFrame]:
        """
        Aggregate data by date for outlier detection on TIME PERIODS.
        Mirrors temporal_anomaly_detection_service._prepare_time_series logic.
        
        This converts row-level data into period-level data so outliers
        are detected on aggregated PERIODS (months/quarters) not individual ROWS.
        
        Args:
            df: Raw DataFrame
            date_column: Column to group by (temporal)
            metric_column: Metric to aggregate
            aggregation: Aggregation method ('sum', 'count', 'mean', etc.)
            
        Returns:
            Aggregated DataFrame with date column and metric column
        """
        try:
            # Convert date column to datetime if not already
            if not pd.api.types.is_datetime64_any_dtype(df[date_column]):
                df_copy = df.copy()
                df_copy[date_column] = pd.to_datetime(df_copy[date_column], errors='coerce')
            else:
                df_copy = df.copy()
            
            # Remove rows with invalid dates
            df_copy = df_copy[df_copy[date_column].notna()]
            
            if len(df_copy) == 0:
                self.logger.warning("[AGGREGATION] No valid dates in date column")
                return None
            
            # Apply aggregation (same logic as temporal anomaly service)
            agg_lower = aggregation.lower()
            
            if agg_lower in ['count', 'count_distinct', 'nunique']:
                if agg_lower == 'count':
                    agg_df = df_copy.groupby(date_column).size().reset_index(name=metric_column)
                else:
                    agg_df = df_copy.groupby(date_column)[metric_column].nunique().reset_index(name=metric_column)
            elif agg_lower in ['sum', 'total']:
                agg_df = df_copy.groupby(date_column)[metric_column].sum().reset_index()
            elif agg_lower in ['mean', 'avg', 'average']:
                agg_df = df_copy.groupby(date_column)[metric_column].mean().reset_index()
            elif agg_lower in ['median']:
                agg_df = df_copy.groupby(date_column)[metric_column].median().reset_index()
            else:
                # Default to sum
                agg_df = df_copy.groupby(date_column)[metric_column].sum().reset_index()
            
            # Sort by date
            agg_df = agg_df.sort_values(date_column).reset_index(drop=True)
            
            self.logger.info(f"[AGGREGATION] Aggregated to {len(agg_df)} periods: {agg_df[date_column].min()} to {agg_df[date_column].max()}")
            
            return agg_df
            
        except Exception as e:
            self.logger.error(f"[AGGREGATION] Failed to aggregate data: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return None
    
    # ========================================================================
    # DATA SOURCE SELECTION HELPERS
    # ========================================================================
    
    def _find_metric_in_chart(self, metric_column: str, chart_columns: list) -> str:
        """
        Find metric column in chart data with fuzzy matching.
        Handles Tableau's "Distinct count of X" naming convention.
        """
        # Exact match
        if metric_column in chart_columns:
            return metric_column
        
        # Fuzzy match - handle Tableau naming
        metric_normalized = metric_column.lower().replace('_', ' ')
        
        for col in chart_columns:
            col_normalized = col.lower()
            
            # Check if metric name is substring of chart column
            # e.g., "Open_Volume" matches "Distinct count of Open_Volume"
            if metric_normalized in col_normalized:
                self.logger.info(f"[METRIC_MAPPING] '{metric_column}' → '{col}'")
                return col
        
        return None
    
    def _is_chart_compatible_with_filter(self, chart_df, temporal_filter):
        """
        Check if chart data covers the requested temporal range.
        Returns False if user asks for time period outside chart's range.
        """
        if not temporal_filter:
            return True
        
        # Get date column from chart
        date_cols = [col for col in chart_df.columns if 'month' in col.lower() or 'date' in col.lower()]
        
        if not date_cols:
            return False
        
        date_col = date_cols[0]
        
        try:
            # Parse chart date range
            chart_dates = pd.to_datetime(chart_df[date_col], errors='coerce')
            chart_min = chart_dates.min()
            chart_max = chart_dates.max()
            
            # Parse filter range
            filter_start = temporal_filter.get('start_date')
            filter_end = temporal_filter.get('end_date')
            
            if filter_start and pd.to_datetime(filter_start) < chart_min:
                self.logger.info(f"[CHART_COMPAT] Filter start {filter_start} < chart min {chart_min}")
                return False
            
            if filter_end and pd.to_datetime(filter_end) > chart_max:
                self.logger.info(f"[CHART_COMPAT] Filter end {filter_end} > chart max {chart_max}")
                return False
            
            return True
            
        except Exception as e:
            self.logger.warning(f"[CHART_COMPAT] Error checking compatibility: {e}")
            return False
    
    def _select_data_source(self, state, metric_column, temporal_filter):
        """
        Intelligently select between chart data and full CSV.
        
        Rules:
        1. If chart selected AND metric exists in chart AND no incompatible temporal filter:
           → Use chart data (Tableau pre-aggregated)
        
        2. If user asks for different time period than chart shows:
           → Use full CSV (need raw data to re-aggregate)
        
        3. If user asks for metric not in chart:
           → Use full CSV
        
        4. If no chart selected:
           → Use full CSV
        
        Returns:
            (dataframe, source_type, mapped_metric_column)
        """
        selected_chart = state.get("selected_chart")
        chart_data_available = (
            selected_chart and 
            state.get("original_tableau_data") and 
            selected_chart in state["original_tableau_data"]
        )
        
        if not chart_data_available:
            self.logger.info(f"[DATA_SOURCE] Using full CSV (no chart selected)")
            # Get DataFrame from DataManager
            from services.data_manager import get_data_manager
            data_manager = get_data_manager()
            data_id = state.get("data_id")
            df = data_manager.get_data(data_id)
            return df, "full_csv", metric_column
        
        chart_df = state["original_tableau_data"][selected_chart]
        
        # Check if metric exists in chart (with fuzzy matching)
        chart_metric_col = self._find_metric_in_chart(metric_column, chart_df.columns.tolist())
        
        if not chart_metric_col:
            self.logger.info(f"[DATA_SOURCE] Using full CSV (metric '{metric_column}' not in chart)")
            # Get DataFrame from DataManager
            from services.data_manager import get_data_manager
            data_manager = get_data_manager()
            data_id = state.get("data_id")
            df = data_manager.get_data(data_id)
            return df, "full_csv", metric_column
        
        # Check if temporal filter is compatible with chart data
        if temporal_filter and not self._is_chart_compatible_with_filter(chart_df, temporal_filter):
            self.logger.info(f"[DATA_SOURCE] Using full CSV (temporal filter incompatible with chart range)")
            # Get DataFrame from DataManager
            from services.data_manager import get_data_manager
            data_manager = get_data_manager()
            data_id = state.get("data_id")
            df = data_manager.get_data(data_id)
            return df, "full_csv", metric_column
        
        # All checks passed - use chart data
        self.logger.info(f"[DATA_SOURCE] Using chart data for '{selected_chart}' (shape: {chart_df.shape})")
        self.logger.info(f"[DATA_SOURCE] Metric '{metric_column}' mapped to '{chart_metric_col}'")
        
        return chart_df.copy(), "chart_data", chart_metric_col
    
    # ========================================================================
    # TEMPORAL ANOMALY DETECTION EXECUTION
    # ========================================================================
    
    def _temporal_anomaly_detection_execution_tool(self, state: Dict) -> Dict:
        """
        NEW NODE: Temporal Anomaly Detection Execution
        
        Executes temporal anomaly detection using 3-layer approach:
        - Layer 1: STL Decomposition for statistical anomalies
        - Layer 2: Change Point Detection (PELT) for abrupt shifts
        - Layer 3: Business logic validation
        """
        self.logger.info("="*80)
        self.logger.info("=== TEMPORAL ANOMALY DETECTION EXECUTION ===")
        self.logger.info("="*80)
        
        try:
            stage1 = state.get("stage1_columns", {})
            stage2 = state.get("stage2_plan", {})
            
            # Get metric column(s)
            target_metrics = stage1.get("target_metrics")
            requires_multi_metric = stage1.get("requires_multi_metric_analysis", False)
            
            # Determine metrics to analyze
            if requires_multi_metric and target_metrics:
                metrics_to_analyze = target_metrics
                self.logger.info(f"[MULTI_METRIC] Analyzing {len(metrics_to_analyze)} metrics: {metrics_to_analyze}")
            else:
                # Single metric
                metric_column = stage2.get("agg_column") or stage1.get("target_metric_column")
                metrics_to_analyze = [metric_column] if metric_column else []
            
            if not metrics_to_analyze:
                self.logger.warning("[TEMPORAL_ANOMALY] No specific metrics detected from query")
                
                # PRIORITY 1: Try to get Y-axes from chart context
                chart_context = state.get("chart_context", {})
                y_axes_detected = chart_context.get("y_axes_detected", [])
                
                if y_axes_detected:
                    self.logger.info(f"[TEMPORAL_ANOMALY] Found {len(y_axes_detected)} Y-axes in chart context: {y_axes_detected}")
                    
                    # Try to match columns mentioned in query with Y-axes
                    query = state.get("query", "").lower()
                    query_words = set(query.split())
                    
                    matched_y_axes = []
                    for y_axis in y_axes_detected:
                        # Check if Y-axis name appears in query (case-insensitive, fuzzy match)
                        y_axis_words = set(y_axis.lower().replace('_', ' ').split())
                        if y_axis_words & query_words:  # Intersection check
                            matched_y_axes.append(y_axis)
                    
                    if matched_y_axes:
                        self.logger.info(f"[TEMPORAL_ANOMALY] Matched Y-axes from query: {matched_y_axes}")
                        metrics_to_analyze = matched_y_axes
                    else:
                        self.logger.info(f"[TEMPORAL_ANOMALY] No Y-axes matched query, using ALL Y-axes: {y_axes_detected}")
                        metrics_to_analyze = y_axes_detected
                    
                    self.logger.info(f"[TEMPORAL_ANOMALY] Proceeding with chart-based analysis for {len(metrics_to_analyze)} metrics")
                
                else:
                    # FALLBACK: Error out if no Y-axes available
                    self.logger.error("[TEMPORAL_ANOMALY] No metric column or Y-axes available for temporal anomaly detection")
                    state["temporal_anomaly_execution_completed"] = True
                    state["completed"] = False
                    state["errors"].append("Temporal anomaly detection: No metric column found")
                    return state
            
            # Get date column
            date_column = stage1.get("date_column")
            
            if not date_column:
                self.logger.error("No date column available for temporal anomaly detection")
                state["temporal_anomaly_execution_completed"] = True
                state["completed"] = False
                state["errors"].append("Temporal anomaly detection: No date column found")
                return state
            
            # Get temporal filter if extracted in Stage 1.5
            temporal_filter = state.get("temporal_filter")
            
            # Get original query for granularity detection
            query = state.get("query", "")
            
            # Initialize temporal anomaly detection service
            anomaly_service = TemporalAnomalyDetectionService(
                logger=self.logger,
                threshold=2.5  # Balanced threshold
            )
            
            # Store results per metric
            all_results = {}
            
            for metric_column in metrics_to_analyze:
                self.logger.info(f"\n{'='*60}")
                self.logger.info(f"Analyzing metric: {metric_column}")
                self.logger.info(f"{'='*60}")
                
                # Smart data source selection
                df, data_source, mapped_metric = self._select_data_source(state, metric_column, temporal_filter)
                
                self.logger.info(f"Dataset shape: {df.shape}")
                self.logger.info(f"Data source: {data_source}")
                self.logger.info(f"Metric column: {mapped_metric}")
                
                # Determine aggregation method
                # PRIORITY: Use Stage1's SmartAggregation recommendation if available
                recommended_agg = stage1.get("recommended_aggregation")
                
                if recommended_agg:
                    # Map SmartAgg recommendation to temporal service format
                    agg_mapping = {
                        'SUM': 'sum',
                        'AVG': 'mean',
                        'MEAN': 'mean',
                        'COUNT': 'count',
                        'COUNT_DISTINCT': 'count_distinct',  # ✅ Map to underscore format
                        'NUNIQUE': 'count_distinct',
                        'MIN': 'min',
                        'MAX': 'max',
                        'MEDIAN': 'median'
                    }
                    aggregation = agg_mapping.get(recommended_agg, 'sum')
                    self.logger.info(f"[SMART_AGG] Using Stage1 recommendation: {recommended_agg} → {aggregation}")
                else:
                    # Fallback to Stage2's agg_functions
                    agg_functions = stage2.get("agg_functions", ["sum"])
                    aggregation = agg_functions[0] if agg_functions else "sum"
                    
                    # Check if this is a count metric - but preserve COUNT_DISTINCT
                    is_count_metric = stage1.get("is_count_metric", False)
                    if is_count_metric and aggregation.upper() not in ["COUNT_DISTINCT", "NUNIQUE"]:
                        # Only override to 'count' if not already COUNT_DISTINCT
                        aggregation = "count"
                    
                    self.logger.info(f"[FALLBACK] Using Stage2 agg_functions: {aggregation}")
                
                # If using chart data, aggregation is already done by Tableau
                if data_source == "chart_data":
                    aggregation = "sum"  # Just sum the pre-aggregated values
                    self.logger.info(f"[CHART_DATA] Using SUM aggregation (data already aggregated by Tableau)")
                
                self.logger.info(f"Date column: {date_column}")
                self.logger.info(f"Aggregation: {aggregation}")
                
                # Run detection
                results = anomaly_service.detect_temporal_anomalies(
                    df=df,
                    date_column=date_column,
                    metric_column=mapped_metric,
                    aggregation=aggregation,
                    temporal_filter=temporal_filter,
                    query=query
                )
                
                all_results[metric_column] = results
            
            # Check if any succeeded
            successful_results = {k: v for k, v in all_results.items() if v.get("success")}
            
            if successful_results:
                if len(successful_results) == 1:
                    # Single metric
                    metric = list(successful_results.keys())[0]
                    results = successful_results[metric]
                    self.logger.info(f"✓ Temporal anomaly detection complete - found {results['total_anomalies']} anomalous periods")
                    
                    formatted_result = self._format_temporal_anomaly_results(results, date_column, metric)
                    state["final_result"] = formatted_result
                else:
                    # Multiple metrics
                    self.logger.info(f"✓ Multi-metric temporal anomaly detection complete")
                    formatted_result = self._format_multi_metric_anomaly_results(successful_results, date_column)
                    state["final_result"] = formatted_result
                
                state["completed"] = True
                state["temporal_anomaly_execution_completed"] = True
                
            else:
                self.logger.error(f"✗ All temporal anomaly detections failed")
                state["temporal_anomaly_execution_completed"] = True
                state["completed"] = False
                state["errors"].append("Temporal anomaly detection: All metrics failed")
            
        except Exception as e:
            self.logger.error(f"Temporal anomaly detection execution error: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            state["temporal_anomaly_execution_completed"] = True
            state["has_temporal_anomaly_detection"] = False
            state["completed"] = False
            state["errors"].append(f"Temporal anomaly execution: {str(e)}")
        
        return state
    
    def _format_temporal_anomaly_results(self, results: Dict, date_column: str, metric_column: str) -> str:
        """
        Format temporal anomaly detection results for user display
        """
        anomalies = results.get("anomalies", [])
        summary = results.get("detection_summary", {})
        
        if not anomalies:
            return f"No significant temporal anomalies detected in the {date_column} time series."
        
        # Build formatted response
        lines = []
        lines.append(f"**Temporal Anomaly Detection Results:**\n")
        lines.append(f"Identified {results['total_anomalies']} anomalous period(s) in the time series.\n")
        lines.append(f"**Detection Method:** {summary.get('method')}\n")
        lines.append(f"**Time Periods Analyzed:** {summary.get('periods_analyzed')}\n\n")
        
        lines.append("**Anomalous Periods:**\n")
        
        # Show all anomalies (sorted by confidence and severity)
        for i, anomaly in enumerate(anomalies, 1):
            lines.append(f"{i}. **{anomaly['period']}** ({anomaly['type'].upper()})")
            lines.append(f"   **Value:** {anomaly['value']:,.2f}")
            lines.append("")  # Blank line between anomalies
        
        return "\n".join(lines)
    
    def _format_multi_metric_anomaly_results(self, all_results: Dict, date_column: str) -> str:
        """
        Format multi-metric temporal anomaly detection results for user display
        """
        lines = []
        lines.append(f"**Multi-Metric Temporal Anomaly Detection Results:**\n")
        lines.append(f"Analyzed {len(all_results)} metrics separately:\n")
        
        for metric, results in all_results.items():
            anomalies = results.get("anomalies", [])
            summary = results.get("detection_summary", {})
            
            lines.append(f"\n{'='*60}")
            lines.append(f"**Metric: {metric}**")
            lines.append(f"{'='*60}\n")
            
            if not anomalies:
                lines.append(f"No significant anomalies detected for {metric}.\n")
                continue
            
            lines.append(f"Identified {results['total_anomalies']} anomalous period(s).\n")
            lines.append(f"**Detection Method:** {summary.get('method')}\n")
            
            lines.append("**Anomalous Periods:**\n")
            
            # Show all anomalies per metric
            for i, anomaly in enumerate(anomalies, 1):
                lines.append(f"{i}. **{anomaly['period']}** ({anomaly['type'].upper()})")
                lines.append(f"   **Value:** {anomaly['value']:,.2f}")
                lines.append("")
        
        return "\n".join(lines)
    
    def _execute_temporal_comparison(self, state: Dict) -> Dict:
        """
        Execute temporal comparison analysis
        Routes to new temporal comparison analyzer
        Supports multiple y-axes when not explicitly mentioned in query
        """
        try:
            from services.temporal_comparison_analysis import TemporalComparisonAnalyzer
            
            # Get DataFrame from DataManager
            from services.data_manager import get_data_manager
            data_manager = get_data_manager()
            data_id = state.get("data_id")
            df = data_manager.get_data(data_id)
            
            stage1 = state.get("stage1_columns", {})
            period_filters = state.get("period_filters", {})
            query = state.get("query", "")
            
            self.logger.info("="*80)
            self.logger.info("=== EXECUTING TEMPORAL COMPARISON ANALYSIS ===")
            self.logger.info("="*80)
            
            # Determine which y-axes to analyze
            y_columns_to_analyze = self._identify_y_axes_for_temporal(state, df, stage1, query)
            
            if not y_columns_to_analyze:
                # Fallback to Stage 1 selection
                y_columns_to_analyze = [stage1.get("target_metric_column")]
                self.logger.info(f"[MULTI_Y_AXES] Fallback to Stage 1 selection: {y_columns_to_analyze}")
            
            # ========================================================================
            # CALCULATE UNIFIED DRIVERS ONCE (for all y-axes)
            # ========================================================================
            unified_drivers = None
            if len(y_columns_to_analyze) > 1:
                # Multiple y-axes: calculate drivers once
                self.logger.info("[UNIFIED_DRIVERS] Multiple y-axes detected, calculating unified drivers")
                unified_drivers = self._calculate_unified_drivers(
                    df=df,
                    period_filters=period_filters,
                    chart_name=state.get("selected_chart", ""),
                    date_column=stage1.get("date_column"),
                    query=query
                )
                self.logger.info(f"[UNIFIED_DRIVERS] Calculated {len(unified_drivers) if unified_drivers else 0} unified drivers")
            else:
                self.logger.info("[UNIFIED_DRIVERS] Single y-axis, drivers will be calculated per-metric")
            
            # Run temporal comparison for each y-axis
            all_results = []
            for y_col in y_columns_to_analyze:
                if not y_col or y_col == 'None':
                    continue
                    
                self.logger.info(f"[MULTI_Y_AXES] Analyzing y-axis: {y_col}")
                
                # Get aggregation method for this specific column
                # Get chart name from state
                selected_chart = state.get("selected_chart", "")
                
                agg_method = self._get_aggregation_for_column(y_col, df, query, chart_name=selected_chart)
                
                
                # Create analyzer
                analyzer = TemporalComparisonAnalyzer(
                    df=df,
                    y_column=y_col,
                    period1_filter=pd.Series(period_filters['period1_filter']),
                    period2_filter=pd.Series(period_filters['period2_filter']),
                    period1_label=period_filters['period1_label'],
                    period2_label=period_filters['period2_label'],
                    llm_client=self.llm_client,
                    logger=self.logger,
                    date_column=stage1.get("date_column"),
                    aggregation_method=agg_method,
                    chart_name=selected_chart,
                    unified_drivers=unified_drivers,  # Pass pre-calculated drivers
                    smart_agg_decider=self.smart_agg_decider  # Pass smart aggregation service
                )
                
                # Run analysis
                result = analyzer.analyze()
                
                if result['success']:
                    all_results.append(result)
                    self.logger.info(f"✓ Temporal comparison completed for {y_col}")
            
            # Combine results if multiple y-axes were analyzed
            if len(all_results) > 0:
                combined_result = self._combine_temporal_results(all_results, query)
                
                # Format final result
                state["final_result"] = {
                    "success": True,
                    "analysis_type": "temporal_comparison",
                    "query": query,
                    "comparison_context": combined_result['comparison_context'],
                    "delta": combined_result.get('delta_metrics', {}),
                    "top_drivers": combined_result.get('top_drivers', []),
                    "explanation": combined_result['explanation'],
                    "response": combined_result['explanation'],
                    "analyzed_metrics": [r['delta_metrics']['metric'] for r in all_results],
                    "all_metric_results": all_results  # Store individual results for 7-layer generation
                }
                state["completed"] = True
                self.logger.info(f"✓ Combined temporal comparison completed for {len(all_results)} metric(s)")
            else:
                self.logger.error("✗ Temporal comparison failed for all metrics")
                state["has_temporal_comparison"] = False
            
        except Exception as e:
            self.logger.error(f"Temporal comparison execution failed: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            state["has_temporal_comparison"] = False
        
        return state
    
    def _identify_y_axes_for_temporal(self, state: Dict, df: pd.DataFrame, 
                                      stage1: Dict, query: str) -> List[str]:
        """
        Identify which y-axes to analyze for temporal comparison
        
        Uses y_axes_detected from causal_analysis_cache.json for the selected chart.
        Returns list of y-axis column names to analyze.
        If query mentions specific column, return only that one (if in chart).
        If no mention, return all y-axes from cached chart data.
        """
        try:
            import os
            import json
            
            selected_chart = state.get("selected_chart", "")
            cache_file = "causal_analysis_cache.json"
            
            # Get workbook_id for nested cache structure
            chart_context = state.get("chart_context", {})
            workbook_id = chart_context.get("workbook_id")
            
            # Try to load y-axes from causal analysis cache (NEW nested structure)
            if selected_chart and os.path.exists(cache_file):
                try:
                    with open(cache_file, 'r') as f:
                        cache_data = json.load(f)
                    
                    # Support NEW nested structure: cache[workbook_id][chart_name]
                    chart_cache = None
                    if workbook_id and workbook_id in cache_data and selected_chart in cache_data[workbook_id]:
                        chart_cache = cache_data[workbook_id][selected_chart]
                        self.logger.info(f"[MULTI_Y_AXES] Found cache using nested structure: {workbook_id}/{selected_chart}")
                    elif selected_chart in cache_data:
                        # Fallback to OLD flat structure for backward compatibility
                        chart_cache = cache_data[selected_chart]
                        self.logger.info(f"[MULTI_Y_AXES] Found cache using flat structure: {selected_chart}")
                    
                    if chart_cache and "y_axes_detected" in chart_cache:
                        cached_y_axes = chart_cache["y_axes_detected"]
                        
                        # Validate y-axes exist in dataframe
                        valid_y_axes = [col for col in cached_y_axes if col in df.columns and pd.api.types.is_numeric_dtype(df[col])]
                        
                        if valid_y_axes:
                            self.logger.info(f"[MULTI_Y_AXES] Loaded y-axes from cache for '{selected_chart}': {valid_y_axes}")
                            
                            # Check if query mentions specific METRICS (not features)
                            mentioned_metrics = stage1.get("mentioned_metrics", [])
                            
                            if mentioned_metrics:
                                self.logger.info(f"[MULTI_Y_AXES] Query mentions specific metrics: {mentioned_metrics}")
                                # Match mentioned metrics ONLY against cached y-axes
                                matched_y_axes = [col for col in mentioned_metrics if col in valid_y_axes]
                                
                                if matched_y_axes:
                                    self.logger.info(f"[MULTI_Y_AXES] Using mentioned y-axes from cache: {matched_y_axes}")
                                    return matched_y_axes
                                else:
                                    self.logger.info(f"[MULTI_Y_AXES] Mentioned columns not in cached y-axes, using all: {valid_y_axes}")
                                    return valid_y_axes
                            
                            # No specific mention - return ALL y-axes from cache
                            self.logger.info(f"[MULTI_Y_AXES] No specific mention, analyzing all cached y-axes: {valid_y_axes}")
                            return valid_y_axes
                        else:
                            self.logger.warning(f"[MULTI_Y_AXES] No valid y-axes found in cache for '{selected_chart}'")
                    else:
                        self.logger.warning(f"[MULTI_Y_AXES] Chart '{selected_chart}' not found in cache or no y_axes_detected")
                except Exception as e:
                    self.logger.error(f"[MULTI_Y_AXES] Error loading from cache: {e}")
            else:
                if not selected_chart:
                    self.logger.warning("[MULTI_Y_AXES] No selected_chart provided")
                else:
                    self.logger.warning(f"[MULTI_Y_AXES] Cache file not found: {cache_file}")
            
            # Fallback: Use target_metrics from Stage 1 (NEW: multi-metric support)
            target_metrics = stage1.get("target_metrics", [])
            if target_metrics:
                self.logger.warning(f"[MULTI_Y_AXES] No cache, using target_metrics from Stage 1: {target_metrics}")
                return target_metrics
            
            # Final fallback: single target_metric_column
            single_target = stage1.get("target_metric_column")
            if single_target:
                self.logger.warning(f"[MULTI_Y_AXES] No target_metrics, using single target_metric_column: {single_target}")
                return [single_target]
            
            # Nothing found
            self.logger.warning("[MULTI_Y_AXES] No y-axes found from cache or Stage 1")
            return []
            
        except Exception as e:
            self.logger.error(f"Error identifying y-axes: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return []
    
    def _get_aggregation_for_column(self, column: str, df: pd.DataFrame, query: str, 
                                    chart_name: str = None) -> str:
        """
        Get appropriate aggregation method for a specific column
        
        Args:
            column: Column name to get aggregation for
            df: DataFrame with the data
            query: User query for context
            chart_name: Optional chart name to lookup Tableau hints
        
        Returns:
            Aggregation method string (COUNT_DISTINCT, SUM, AVG, etc.)
        """
        try:
            # Try to get Tableau hint first
            tableau_hint = None
            if chart_name:
                tableau_hint = self.hints_manager.get_hint(chart_name, column)
                if tableau_hint:
                    self.logger.info(f"[TABLEAU_HINT] Found hint for '{chart_name}' / '{column}': {tableau_hint}")
            
            if self.smart_agg_decider:
                decision = self.smart_agg_decider.decide_aggregation(
                    query=query, 
                    column=column, 
                    df=df,
                    chart_name=chart_name,
                    tableau_hint=tableau_hint
                )
                return decision.get('aggregation', 'COUNT_DISTINCT')
            else:
                # Fallback heuristic - but prioritize Tableau hint if available
                if tableau_hint:
                    self.logger.info(f"[TABLEAU_HINT] Using hint in fallback mode: {tableau_hint}")
                    return tableau_hint
                
                col_lower = column.lower()
                if any(pattern in col_lower for pattern in ['_id', 'id_', 'count']):
                    return 'COUNT_DISTINCT'
                elif any(pattern in col_lower for pattern in ['volume', 'total', 'sum']):
                    return 'SUM'
                elif any(pattern in col_lower for pattern in ['avg', 'average', 'mean']):
                    return 'AVG'
                else:
                    return 'COUNT_DISTINCT'
        except Exception as e:
            self.logger.error(f"Error getting aggregation for {column}: {e}")
            return 'COUNT_DISTINCT'

    def _calculate_unified_drivers(self, df: pd.DataFrame, period_filters: Dict,
                                   chart_name: str, date_column: str, query: str) -> List[Dict[str, Any]]:
        """
        Calculate unified drivers once for all y-axes
        
        This ensures consistent driver rankings across multiple metrics
        by using smart aggregation for each feature independently
        
        Args:
            df: Full DataFrame
            period_filters: Dict with period1_filter, period2_filter, labels
            chart_name: Chart name for loading cached features
            date_column: Date column for grouping
            query: User query for context
            
        Returns:
            List of driver dictionaries with contribution scores and category breakdowns
        """
        try:
            from services.temporal_comparison_analysis import TemporalComparisonAnalyzer
            
            self.logger.info("="*80)
            self.logger.info("=== CALCULATING UNIFIED DRIVERS ===")
            self.logger.info("="*80)
            
            # Create a temporary analyzer just to get features and calculate drivers
            # Use a dummy y_column (will be ignored since we're calculating drivers independently)
            temp_analyzer = TemporalComparisonAnalyzer(
                df=df,
                y_column='_dummy_',  # Not used for driver calculation
                period1_filter=pd.Series(period_filters['period1_filter']),
                period2_filter=pd.Series(period_filters['period2_filter']),
                period1_label=period_filters['period1_label'],
                period2_label=period_filters['period2_label'],
                llm_client=self.llm_client,
                logger=self.logger,
                date_column=date_column,
                chart_name=chart_name,
                smart_agg_decider=self.smart_agg_decider
            )
            
            # Get features from cache
            categorical_features = temp_analyzer._get_features_from_cache()
            
            if not categorical_features:
                self.logger.warning("[UNIFIED_DRIVERS] No features found from cache")
                return []
            
            self.logger.info(f"[UNIFIED_DRIVERS] Analyzing {len(categorical_features)} features")
            
            # Calculate a dummy total delta (will be recalculated per metric later)
            # This is just for ranking purposes
            df_p1 = df[period_filters['period1_filter']]
            df_p2 = df[period_filters['period2_filter']]
            total_delta = len(df_p2) - len(df_p1)  # Simple record count delta as proxy
            
            # Calculate drivers
            drivers = temp_analyzer._identify_drivers(categorical_features, total_delta)
            
            self.logger.info(f"[UNIFIED_DRIVERS] ✓ Calculated {len(drivers)} unified drivers")
            
            return drivers
            
        except Exception as e:
            self.logger.error(f"[UNIFIED_DRIVERS] Failed to calculate unified drivers: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return []
    
    def _combine_temporal_results(self, results: List[Dict], query: str) -> Dict:
        """
        Combine multiple temporal analysis results into one response
        
        New format:
        - Show LLM explanations for each metric first (without driver bullets)
        - Then show unified drivers once at the end
        """
        try:
            if len(results) == 1:
                # Single result, return as-is
                return results[0]
            
            # Multiple results - format with LLM explanations first, then drivers
            combined_parts = []
            
            # Part 1: LLM explanations for each metric (extract just the text before "**Top Drivers:**")
            for result in results:
                metric = result['delta_metrics']['metric']
                full_explanation = result['explanation']
                
                # Extract just the LLM-generated text (before the driver bullets)
                if "**Top Drivers:**" in full_explanation:
                    llm_text = full_explanation.split("**Top Drivers:**")[0].strip()
                else:
                    llm_text = full_explanation
                
                # Add section header for this metric
                combined_parts.append(f"**{metric} Analysis:**\n{llm_text}")
            
            # Part 2: Unified drivers (use from first result since they should be the same)
            # Extract the driver bullets from the first result
            first_explanation = results[0]['explanation']
            if "**Top Drivers:**" in first_explanation:
                driver_section = first_explanation.split("**Top Drivers:**", 1)[1]
                combined_parts.append(f"\n**Top Drivers:**{driver_section}")
            
            # Create combined result
            combined = {
                'success': True,
                'comparison_context': results[0]['comparison_context'],  # Use first result's context
                'explanation': "\n\n".join(combined_parts),
                'delta_metrics': results[0]['delta_metrics'],  # Primary metric from first result
                'top_drivers': results[0]['top_drivers']  # Unified drivers
            }
            
            self.logger.info(f"[MULTI_Y_AXES] Combined {len(results)} temporal analysis results")
            return combined
            
        except Exception as e:
            self.logger.error(f"Error combining temporal results: {e}")
            # Fallback to first result
            return results[0] if results else {'success': False, 'error': str(e)}
    
    # ========================================================================
    # VALIDATOR: PLAN VALIDATION
    # ========================================================================
    
    def _validate_plan_tool(self, state: Dict) -> Dict:
        """
        VALIDATOR: Check if plan addresses the user's query
        LLM-powered validation checkpoint
        """
        self.logger.info("="*80)
        self.logger.info("=== VALIDATOR: PLAN VALIDATION ===")
        self.logger.info("="*80)
        
        try:
            query = state.get("query", "")
            stage1 = state.get("stage1_columns", {})
            stage2 = state.get("stage2_plan", {})
            
            # Build sub-intent descriptions dynamically (NO HARDCODING)
            sub_intent_descriptions = []
            for intent, details in SUB_INTENTS.items():
                examples_str = ', '.join(f'"{ex}"' for ex in details['examples'][:3])
                sub_intent_descriptions.append(
                    f"- **{intent}**: {details['description']}\n"
                    f"  Examples: {examples_str}"
                )
            sub_intent_info = "\n\n".join(sub_intent_descriptions)
            
            class PlanValidation(BaseModel):
                """Validation result"""
                plan_is_valid: bool
                addresses_query: bool
                mentioned_columns_included: bool = Field(
                    description="If query mentions specific columns, are they in the plan?"
                )
                issues: List[str] = []
                suggestions: List[str] = []
                confidence: float
            
            response = self.llm_client.beta.chat.completions.parse(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": f"""You are validating an analysis plan.

AVAILABLE SUB-INTENTS (use these to validate):
{sub_intent_info}

VALIDATION GUIDELINES:

1. **Correlation queries** (keywords: "correlation", "correlate", "correlated"):
   - Should use "correlation_analysis" sub-intent
   - These are NUMERIC relationship queries, NOT causal/impact queries
   - IMPORTANT: "correlation between X and Y" ≠ "how X affects Y"
   - Example: "correlation between age and tickets" → correlation_analysis ✓
   - Example: "which metrics correlate with sales" → correlation_analysis ✓

2. **Specific feature impact** (keywords: "how X affects Y", "impact of X"):
   - Should use "specific_feature_impact" sub-intent
   - X (specific column) MUST be in group_by_columns
   - trigger_causal_analysis should be FALSE (this is targeted analysis)
   - Example: "how account_manager affects tickets" → specific_feature_impact ✓

3. **Multiple feature interaction** (keywords: "together", "combined", "X and Y affect"):
   - Should use "multiple_feature_impact" sub-intent
   - 2+ specific columns mentioned TOGETHER
   - Example: "how region and product affect sales together" → multiple_feature_impact ✓

4. **General drivers** (keywords: "what affects", "what drives", "why"):
   - Should use "general_drivers" sub-intent
   - NO specific column mentioned (exploratory query)
   - trigger_causal_analysis should be TRUE (needs causal analysis)
   - Example: "what affects tickets?" → general_drivers ✓
   - Example: "why spike in sales?" → general_drivers ✓

5. **Other sub-intents**: Validate based on their descriptions listed above

CRITICAL REASONING:
- Use your intelligence to understand user intent, don't just pattern match
- Column mentions don't automatically mean "specific_feature_impact"
- Distinguish: Correlation (numeric relationship) vs Impact (causal effect) vs Breakdown (categorization)
- If a valid sub-intent exists for the query type, accept it
- Only reject if the sub-intent clearly doesn't match what the user asked

BE SMART: You're an LLM - reason about the query semantics, don't just check rules."""},
                    {"role": "user", "content": f"""
Query: {query}

Stage 1 (Columns):
{json.dumps(stage1, indent=2)}

Stage 2 (Plan):
{json.dumps(stage2, indent=2)}

Validate this plan. Does it address what the user asked?
"""}
                ],
                response_format=PlanValidation
            )
            
            validation = response.choices[0].message.parsed
            
            if not validation.plan_is_valid or not validation.addresses_query:
                self.logger.warning(f"⚠️ Plan validation failed!")
                self.logger.warning(f"   Issues: {validation.issues}")
                
                # Check if we can retry or should apply course correction
                retry_count = state.get("retry_count", 0)
                max_retries = state.get("max_retries", 2)
                
                # COURSE CORRECTION (try to fix simple issues)
                correction_applied = False
                if stage1.get("mentioned_features") and stage1.get("query_type") == "specific":
                    if not validation.mentioned_columns_included:
                        # Fix: Add mentioned features to group_by
                        self.logger.info("🔧 Correcting plan: Adding mentioned features")
                        state["stage2_plan"]["group_by_columns"] = stage1["mentioned_features"]
                        state["messages"].append("✓ Plan corrected: Added mentioned features")
                        validation.plan_is_valid = True
                        correction_applied = True
                
                # If correction didn't work and we haven't maxed retries, mark for retry
                if not correction_applied and retry_count < max_retries:
                    self.logger.warning(f"🔄 Marking for retry from Stage 2 (attempt {retry_count + 1}/{max_retries})")
                    state["plan_validation_failed"] = True
                    state["validation_issues"] = validation.issues
                    state["validation_suggestions"] = validation.suggestions  # Store suggestions for corrective prompt
                    state["correction_mode"] = True  # FLAG: Use corrective prompt in Stage 2
                    state["error_stage"] = "stage2"  # Mark that Stage 2 failed
                    state["plan_validated"] = False  # Don't proceed yet
                    
                    # Log stored corrections for Stage 2 to use
                    self.logger.info(f"📝 Stored {len(validation.issues)} validation errors for corrective retry")
                    self.logger.info(f"📝 Stored {len(validation.suggestions)} suggestions for corrective retry")
                else:
                    # Either correction worked or max retries reached
                    if not correction_applied:
                        self.logger.warning(f"⚠️ Max retries reached or no correction available, proceeding with warnings")
                    state["plan_validated"] = True
                    state["plan_validation_failed"] = False
            else:
                self.logger.info("✓ Plan validation passed")
                state["plan_validated"] = True
                state["plan_validation_failed"] = False
            
            state["plan_validation"] = validation.dict()
            
        except Exception as e:
            self.logger.error(f"Plan validation failed: {str(e)}")
            state["plan_validated"] = True  # Continue anyway
        
        return state
    
    # ========================================================================
    # STAGE 3.1: CODE GENERATION
    # ========================================================================
    
    def _stage3_1_code_generation_tool(self, state: Dict) -> Dict:
        """
        Stage 3.1: Generate pandas code for each sub-intent
        Uses operation registry pattern (NO HARDCODING)
        """
        self.logger.info("="*80)
        self.logger.info("=== STAGE 3.1: CODE GENERATION ===")
        self.logger.info("="*80)
        
        try:
            stage1 = state.get("stage1_columns", {})
            stage2 = state.get("stage2_plan", {})
            
            # Get DataFrame from DataManager
            from services.data_manager import get_data_manager
            data_manager = get_data_manager()
            data_id = state.get("data_id")
            df = data_manager.get_data(data_id)
            
            generated_codes = {}
            
            # Get sub-intents to execute
            sub_intents = stage2.get("sub_intents", [])
            self.logger.info(f"Generating code for {len(sub_intents)} sub-intents: {sub_intents}")
            
            # Check if we should trigger causal analysis first
            causal_top_drivers = []  # Initialize for use later
            if stage2.get("trigger_causal_analysis"):
                self.logger.info("🔬 Triggering UniversalCausalAnalyzer")
                
                # === ROUTING: Choose between single-metric or multi-metric causal analysis ===
                stage1 = state.get("stage1_columns", {})
                requires_multi_metric = stage1.get("requires_multi_metric_analysis", False)
                target_metrics = stage1.get("target_metrics", [])
                
                if requires_multi_metric and len(target_metrics) > 1:
                    self.logger.info(f"🔀 [ROUTING] Multi-metric analysis required for {len(target_metrics)} metrics")
                    causal_results = self._run_multi_metric_causal_analysis(state, df)
                else:
                    self.logger.info("🔀 [ROUTING] Single-metric analysis")
                    causal_results = self._run_causal_analysis(state, df)
                
                if causal_results:
                    state["causal_analysis_results"] = causal_results
                    # Extract top drivers for use in code generation
                    causal_top_drivers = causal_results.get("top_drivers", [])
                    # Update group_by_columns with top drivers if not set
                    if causal_top_drivers and not stage2.get("group_by_columns"):
                        stage2["group_by_columns"] = [d[0] for d in causal_top_drivers[:5]]
                        self.logger.info(f"✓ Using top drivers from causal: {stage2['group_by_columns']}")
                    
                    # Store date column from causal analysis for temporal analysis
                    if causal_results.get("x_axis_column"):
                        stage2["date_column"] = causal_results.get("x_axis_column")
                        self.logger.info(f"✓ Using date column from causal: {stage2['date_column']}")
                    
                    # Store target metric from causal analysis if not set
                    if causal_results.get("y_column") and not stage2.get("agg_column"):
                        stage2["agg_column"] = causal_results.get("y_column")
                        self.logger.info(f"✓ Using target metric from causal: {stage2['agg_column']}")
            
            # Generate code for each sub-intent (only 5 causal/explanation sub-intents)
            for sub_intent in sub_intents:
                self.logger.info(f"Generating code for: {sub_intent}")
                
                if sub_intent == "categorical_breakdown":
                    # Get recommended aggregation from stage1 (from SmartAggregationDecider)
                    recommended_agg = stage1.get("recommended_aggregation")
                    
                    # Convert SmartAgg recommendation to pandas agg function
                    agg_mapping = {
                        'SUM': ['sum'],
                        'AVG': ['mean'],
                        'MEAN': ['mean'],
                        'COUNT': ['count'],
                        'COUNT_DISTINCT': ['nunique'],
                        'MIN': ['min'],
                        'MAX': ['max'],
                        'MEDIAN': ['median']
                    }
                    
                    # Use recommended aggregation if available, otherwise use old default
                    if recommended_agg and recommended_agg in agg_mapping:
                        agg_funcs = agg_mapping[recommended_agg]
                        self.logger.info(f"  Using SmartAgg recommendation: {recommended_agg} → {agg_funcs}")
                    else:
                        agg_funcs = stage2.get("agg_functions", ["sum"])  # Changed default from ['count','sum','mean'] to just ['sum']
                        self.logger.info(f"  No SmartAgg recommendation, using default: {agg_funcs}")
                    
                    params = {
                        "group_columns": stage2.get("group_by_columns", []),
                        "agg_column": stage2.get("agg_column"),
                        "agg_functions": agg_funcs,
                        "is_count_metric": stage2.get("is_count_metric", False)
                    }
                    code = self.code_generator.generate_categorical_breakdown(params)
                    generated_codes["categorical_breakdown"] = code
                    self.logger.info("  ✓ Generated code for categorical_breakdown")
                
                elif sub_intent == "period_over_period":
                    params = {
                        "date_column": stage1.get("date_column"),
                        "target_metric": stage2.get("agg_column"),
                        "granularity": stage2.get("period_granularity", "M"),
                        "comparison_type": stage2.get("comparison_type", "mom"),
                        "is_count_metric": stage2.get("is_count_metric", False)
                    }
                    code = self.code_generator.generate_period_over_period(params)
                    generated_codes["period_over_period"] = code
                    self.logger.info("  ✓ Generated code for period_over_period")
                
                elif sub_intent == "contribution_analysis":
                    params = {
                        "categorical_column": stage2.get("group_by_columns", [])[0] if stage2.get("group_by_columns") else None,
                        "target_metric": stage2.get("agg_column"),
                        "is_count_metric": stage2.get("is_count_metric", False)
                    }
                    if params["categorical_column"]:
                        code = self.code_generator.generate_contribution_analysis(params)
                        generated_codes["contribution"] = code
                        self.logger.info("  ✓ Generated code for contribution")
                
                elif sub_intent == "general_drivers":
                    # Use top drivers from causal analysis
                    # causal_top_drivers was set earlier when causal analysis ran
                    if causal_top_drivers:
                        # Need to determine agg_column - use target metric from Stage 1 or default
                        agg_column = stage2.get("agg_column") or stage1.get("target_metric_column") or "case_id"
                        params = {
                            "top_drivers": causal_top_drivers,
                            "agg_column": agg_column,
                            "is_count_metric": stage2.get("is_count_metric", True)
                        }
                        code = self.code_generator.generate_general_drivers(params)
                        generated_codes["general_drivers"] = code
                        self.logger.info(f"  ✓ Generated code for general_drivers (analyzing {len(causal_top_drivers)} drivers)")
                    else:
                        self.logger.warning("  ⚠️ No causal drivers available for general_drivers analysis")
                
                elif sub_intent == "temporal_trend":
                    # Generate temporal trend analysis
                    date_col = stage1.get("date_column") or stage2.get("date_column")
                    if date_col:
                        agg_column = stage2.get("agg_column") or stage1.get("target_metric_column")
                        params = {
                            "date_column": date_col,
                            "agg_column": agg_column,
                            "is_count_metric": stage2.get("is_count_metric", False),
                            "granularity": stage2.get("period_granularity", "month")
                        }
                        code = self.code_generator.generate_temporal_trend(params)
                        generated_codes["temporal_trend"] = code
                        self.logger.info("  ✓ Generated code for temporal_trend")
                    else:
                        self.logger.warning("  ⚠️ No date column available for temporal_trend analysis")
                
                elif sub_intent == "specific_feature_impact":
                    # Generate specific feature impact analysis
                    # Use group_by from Stage 2 (refined) or fallback to mentioned features from Stage 1
                    feature_cols = stage2.get("group_by_columns", []) or stage1.get("mentioned_features", [])
                    if feature_cols:
                        agg_column = stage2.get("agg_column") or stage1.get("target_metric_column")
                        
                        # Generate separate code for each feature to enable proper per-feature formatting
                        for feature_col in feature_cols:
                            params = {
                                "feature_columns": [feature_col],  # Single feature at a time
                                "agg_column": agg_column,
                                "is_count_metric": stage2.get("is_count_metric", False)
                            }
                            code = self.code_generator.generate_specific_feature_impact(params)
                            # Store with feature-specific key for proper formatting
                            generated_codes[f"{feature_col}_impact"] = code
                            self.logger.info(f"  ✓ Generated code for specific_feature_impact: {feature_col}")
                    else:
                        self.logger.warning("  ⚠️ No feature columns available for specific_feature_impact")
                
                elif sub_intent == "multiple_feature_impact":
                    # Generate multiple feature interaction analysis
                    # Use group_by from Stage 2 (refined) or fallback to mentioned features from Stage 1
                    feature_cols = stage2.get("group_by_columns", []) or stage1.get("mentioned_features", [])
                    if len(feature_cols) >= 2:
                        agg_column = stage2.get("agg_column") or stage1.get("target_metric_column")
                        params = {
                            "feature_columns": feature_cols,
                            "agg_column": agg_column,
                            "is_count_metric": stage2.get("is_count_metric", False)
                        }
                        code = self.code_generator.generate_multiple_feature_impact(params)
                        generated_codes["multiple_feature_impact"] = code
                        self.logger.info(f"  ✓ Generated code for multiple_feature_impact: {feature_cols[:2]}")
                    else:
                        self.logger.warning("  ⚠️ Need at least 2 features for multiple_feature_impact")
                
                elif sub_intent == "correlation_analysis":
                    # Generate correlation analysis
                    target_metric = stage2.get("agg_column") or stage1.get("target_metric_column")
                    numeric_cols = stage1.get("numeric_columns", [])
                    self.logger.info(f"[CORRELATION_CODE_GEN] target_metric: {target_metric}")
                    self.logger.info(f"[CORRELATION_CODE_GEN] numeric_columns from Stage 1: {numeric_cols}")
                    self.logger.info(f"[CORRELATION_CODE_GEN] Number of columns: {len(numeric_cols) if numeric_cols else 0}")
                    params = {
                        "target_metric": target_metric,
                        "numeric_columns": numeric_cols
                    }
                    code = self.code_generator.generate_correlation_analysis(params)
                    generated_codes["correlation_analysis"] = code
                    if numeric_cols and len(numeric_cols) <= 5:
                        self.logger.info(f"  ✓ Generated code for correlation_analysis with specific columns: {numeric_cols}")
                    else:
                        self.logger.info(f"  ✓ Generated code for correlation_analysis with all numeric columns")
                
                else:
                    self.logger.warning(f"⚠️ Unknown sub-intent: {sub_intent}")
                    self.logger.warning(f"   Valid sub-intents: {list(SUB_INTENTS.keys())}")
                    self.logger.warning(f"   This query may need to be routed to NL_to_python.py instead")
            
            state["generated_codes"] = generated_codes
            state["stage3_1_completed"] = True
            state["messages"].append(f"✓ Stage 3.1: Generated {len(generated_codes)} code blocks")
            
            self.logger.info(f"✓ Stage 3.1 Complete: Generated {len(generated_codes)} code blocks")
            
        except Exception as e:
            self.logger.error(f"Stage 3.1 failed: {str(e)}")
            self.logger.error(traceback.format_exc())
            state["errors"].append(f"Stage 3.1: {str(e)}")
            state["stage3_1_completed"] = True
        
        return state
    
    # ========================================================================
    # STAGE 3.2: CODE EXECUTION
    # ========================================================================
    
    def _stage3_2_code_execution_tool(self, state: Dict) -> Dict:
        """
        Stage 3.2: Execute generated code and collect results
        Returns ACTUAL COMPUTED STATISTICS
        """
        self.logger.info("="*80)
        self.logger.info("=== STAGE 3.2: CODE EXECUTION (ACTUAL COMPUTATION) ===")
        self.logger.info("="*80)
        
        try:
            # Get DataFrame from DataManager
            from services.data_manager import get_data_manager
            data_manager = get_data_manager()
            data_id = state.get("data_id")
            df = data_manager.get_data(data_id)
            
            generated_codes = state.get("generated_codes", {})
            
            execution_results = {}
            execution_errors = []  # Track errors
            unfixable_intents = state.get("unfixable_sub_intents", [])
            
            self.logger.info(f"Executing {len(generated_codes)} code blocks")
            if unfixable_intents:
                self.logger.info(f"Skipping {len(unfixable_intents)} unfixable sub-intents: {unfixable_intents}")
            
            for name, code in generated_codes.items():
                # Skip unfixable sub-intents (already failed repair attempts)
                if name in unfixable_intents:
                    self.logger.info(f"⏭️  Skipping {name} (marked as unfixable)")
                    continue
                
                try:
                    self.logger.info(f"Executing: {name}")
                    
                    # Execute code safely
                    result = self._execute_code_safely(code, df)
                    
                    if result is not None:
                        # Store result (convert numpy types for msgpack serialization)
                        if isinstance(result, (pd.DataFrame, pd.Series)):
                            result_dict = result.to_dict(orient='records') if isinstance(result, pd.DataFrame) else result.to_dict()
                            # Convert any numpy types in the dict
                            execution_results[name] = self._convert_numpy_to_python(result_dict)
                            self.logger.info(f"  ✓ {name}: DataFrame/Series with {len(result)} rows")
                        else:
                            # Special handling for general_drivers: may contain DataFrames in dict values
                            if name == "general_drivers" and isinstance(result, dict):
                                cleaned_result = {}
                                for key, val in result.items():
                                    if isinstance(val, pd.DataFrame):
                                        cleaned_result[key] = self._convert_numpy_to_python(val.to_dict('records'))
                                        self.logger.info(f"    → Converted DataFrame in '{key}' to dict")
                                    else:
                                        cleaned_result[key] = self._convert_numpy_to_python(val)
                                execution_results[name] = cleaned_result
                                self.logger.info(f"  ✓ {name}: dict with {len(cleaned_result)} keys")
                            else:
                                execution_results[name] = self._convert_numpy_to_python(result)
                                self.logger.info(f"  ✓ {name}: {type(result)}")
                    else:
                        self.logger.warning(f"  ⚠️ {name}: No result returned")
                    
                except Exception as e:
                    self.logger.error(f"  ❌ {name} failed: {str(e)}")
                    execution_results[f"{name}_error"] = str(e)
                    execution_errors.append(f"{name}: {str(e)}")
                    
                    # Store first failed code details for LLM repair
                    if not state.get("failed_sub_intent"):  # Only store first failure
                        state["failed_sub_intent"] = name
                        state["failed_code"] = code
                        state["execution_error_message"] = str(e)
                        state["execution_error_traceback"] = traceback.format_exc()
                        self.logger.info(f"📝 Stored error details for LLM repair: {name}")
            
            # Mark error stage if there were execution errors
            if execution_errors:
                state["execution_errors"] = execution_errors
                state["error_stage"] = "stage3"  # Mark that Stage 3 had errors
            
            state["execution_results"] = execution_results
            state["stage3_2_completed"] = True
            state["messages"].append(f"✓ Stage 3.2: Executed {len(execution_results)} analyses")
            
            self.logger.info(f"✓ Stage 3.2 Complete: {len(execution_results)} results")
            
        except Exception as e:
            self.logger.error(f"Stage 3.2 failed: {str(e)}")
            self.logger.error(traceback.format_exc())
            state["errors"].append(f"Stage 3.2: {str(e)}")
            state["stage3_2_completed"] = True
        
        return state
    
    def _convert_numpy_to_python(self, obj):
        """
        Recursively convert numpy types to Python native types
        Needed for msgpack serialization in LangGraph
        """
        import numpy as np
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, pd.DataFrame):
            # Convert DataFrame to dict of records, then recursively convert numpy types
            return self._convert_numpy_to_python(obj.to_dict('records'))
        elif isinstance(obj, pd.Series):
            # Convert Series to dict, then recursively convert numpy types
            return self._convert_numpy_to_python(obj.to_dict())
        elif isinstance(obj, dict):
            return {k: self._convert_numpy_to_python(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self._convert_numpy_to_python(item) for item in obj]
        else:
            return obj
    
    def _execute_code_safely(self, code: str, df: pd.DataFrame) -> Any:
        """
        Execute pandas code safely with proper namespace
        """
        # Create safe namespace
        namespace = {
            'df': df,
            'pd': pd,
            'np': np
        }
        
        try:
            # Execute code
            exec(code, namespace)
            
            # Get result (last variable assigned or 'result')
            if 'result' in namespace:
                return namespace['result']
            
            # If no 'result', return last computed value
            return None
            
        except Exception as e:
            self.logger.error(f"Code execution error: {str(e)}")
            self.logger.error(f"Code:\n{code}")
            raise
    
    # ========================================================================
    # STAGE 3.1.5: LLM CODE REPAIR (SELF-HEALING)
    # ========================================================================
    
    def _stage3_1_5_llm_code_repair(self, state: Dict) -> Dict:
        """
        Stage 3.1.5: LLM-powered code repair for failed executions
        Uses GPT-4o-mini to fix code based on error messages and data context
        """
        self.logger.info("="*80)
        self.logger.info("=== STAGE 3.1.5: LLM CODE REPAIR ===")
        self.logger.info("="*80)
        
        try:
            # Get DataFrame from DataManager
            from services.data_manager import get_data_manager
            data_manager = get_data_manager()
            data_id = state.get("data_id")
            if data_id:
                df = data_manager.get_data(data_id)
            else:
                df = None
            
            query = state.get("query", "")
            failed_sub_intent = state.get("failed_sub_intent")
            failed_code = state.get("failed_code")
            error_message = state.get("execution_error_message")
            error_traceback = state.get("execution_error_traceback")
            code_repair_attempt = state.get("code_repair_attempt", 0)
            max_attempts = state.get("max_code_repair_attempts", 3)
            
            if not failed_code or not error_message:
                self.logger.warning("No failed code or error message to repair")
                state["code_repair_success"] = False
                state["code_repair_attempt"] = code_repair_attempt + 1  # Increment to prevent infinite loop
                return state
            
            if df is None:
                self.logger.error("DataFrame is None (csv_data_json not in state), cannot perform code repair")
                state["code_repair_success"] = False
                state["code_repair_attempt"] = code_repair_attempt + 1  # Increment to prevent infinite loop
                return state
            
            self.logger.info(f"Attempting to repair code (attempt {code_repair_attempt + 1}/{max_attempts})")
            self.logger.info(f"Failed sub-intent: {failed_sub_intent}")
            self.logger.info(f"Error: {error_message}")
            
            # Call LLM to repair the code
            repaired_code = self._call_llm_for_code_repair(
                query=query,
                failed_code=failed_code,
                error_message=error_message,
                error_traceback=error_traceback,
                df=df,
                sub_intent=failed_sub_intent
            )
            
            if repaired_code:
                self.logger.info("✓ LLM generated repaired code")
                
                # Try executing the repaired code
                try:
                    result = self._execute_code_safely(repaired_code, df)
                    
                    # Success! Store the repaired code and result
                    self.logger.info("✅ Repaired code executed successfully!")
                    state["code_repair_success"] = True
                    state["repaired_code"] = repaired_code
                    
                    # Store result in execution_results
                    execution_results = state.get("execution_results", {})
                    
                    # Convert result to serializable format
                    if isinstance(result, (pd.DataFrame, pd.Series)):
                        result_dict = result.to_dict(orient='records') if isinstance(result, pd.DataFrame) else result.to_dict()
                        execution_results[failed_sub_intent] = self._convert_numpy_to_python(result_dict)
                    else:
                        execution_results[failed_sub_intent] = self._convert_numpy_to_python(result)
                    
                    state["execution_results"] = execution_results
                    
                    # Log the repair for template improvement
                    self._log_code_repair(
                        query=query,
                        sub_intent=failed_sub_intent,
                        failed_code=failed_code,
                        fixed_code=repaired_code,
                        error=error_message
                    )
                    
                    # Remove only the fixed sub-intent from error list (may be other failures)
                    execution_errors = state.get("execution_errors", [])
                    state["execution_errors"] = [e for e in execution_errors if failed_sub_intent not in e]
                    
                except Exception as repair_error:
                    self.logger.warning(f"Repaired code still failed: {str(repair_error)}")
                    state["code_repair_success"] = False
                    state["code_repair_attempt"] = code_repair_attempt + 1
                    
                    # Update error info for next attempt
                    state["execution_error_message"] = str(repair_error)
                    state["execution_error_traceback"] = traceback.format_exc()
            else:
                self.logger.warning("LLM failed to generate repaired code")
                state["code_repair_success"] = False
                state["code_repair_attempt"] = code_repair_attempt + 1
        
        except Exception as e:
            self.logger.error(f"Stage 3.1.5 failed: {str(e)}")
            self.logger.error(traceback.format_exc())
            state["code_repair_success"] = False
            state["code_repair_attempt"] = code_repair_attempt + 1
        
        return state
    
    def _call_llm_for_code_repair(
        self,
        query: str,
        failed_code: str,
        error_message: str,
        error_traceback: str,
        df: pd.DataFrame,
        sub_intent: str
    ) -> Optional[str]:
        """
        Call LLM to repair failed pandas code
        Returns fixed code or None if repair fails
        """
        try:
            # Check if df is valid
            if df is None:
                self.logger.error("DataFrame is None, cannot repair code without data context")
                return None
            
            # Prepare data context for LLM
            columns_info = {
                "columns": df.columns.tolist(),
                "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
                "shape": df.shape
            }
            
            # Get sample data (convert datetime to strings for JSON)
            sample_df = df.head(2).copy()
            datetime_cols = []
            for col in sample_df.select_dtypes(include=['datetime64', 'datetime']).columns:
                datetime_cols.append(col)
                sample_df[col] = sample_df[col].astype(str)
            sample_data = sample_df.to_dict('records')
            
            # Build LLM prompt
            system_prompt = """You are a pandas code debugging expert. Your task is to fix broken pandas code.

CRITICAL RULES:
1. Return ONLY the fixed Python code, no explanations
2. The code must assign the final result to a variable called 'result'
3. Available in namespace: df (DataFrame), pd (pandas), np (numpy)
4. Common pandas errors to avoid:
   - Nested dict in .agg() → Use list of function names instead
   - Wrong frequency codes → Use pandas standards (D, W, M, Q, Y)
   - Column name typos → Check actual column names carefully
5. Keep the same analysis intent as the original code"""

            user_prompt = f"""Fix this pandas code that failed:

**User Query:** {query}
**Sub-intent:** {sub_intent}

**Failed Code:**
```python
{failed_code}
```

**Error:**
{error_message}

**DataFrame Info:**
- Columns: {columns_info['columns']}
- Data types: {columns_info['dtypes']}
- Shape: {columns_info['shape']}
- Sample (first 2 rows): {json.dumps(sample_data, indent=2)}
"""

            if datetime_cols:
                user_prompt += f"\n**Note:** Datetime columns (shown as strings): {datetime_cols}\n"
            
            user_prompt += "\n**Return only the fixed code:**"
            
            # Call LLM
            response = self.llm_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,  # Deterministic repairs
                max_tokens=1000
            )
            
            repaired_code = response.choices[0].message.content.strip()
            
            # Extract code from markdown if present
            if "```python" in repaired_code:
                repaired_code = repaired_code.split("```python")[1].split("```")[0].strip()
            elif "```" in repaired_code:
                repaired_code = repaired_code.split("```")[1].split("```")[0].strip()
            
            return repaired_code
            
        except Exception as e:
            self.logger.error(f"LLM code repair call failed: {str(e)}")
            return None
    
    def _log_code_repair(
        self,
        query: str,
        sub_intent: str,
        failed_code: str,
        fixed_code: str,
        error: str
    ):
        """
        Log code repairs to file for template improvement analysis
        """
        try:
            log_file = "code_repair_log.json"
            
            # Load existing log
            if os.path.exists(log_file):
                with open(log_file, 'r') as f:
                    repair_log = json.load(f)
            else:
                repair_log = []
            
            # Add new repair entry
            repair_entry = {
                "timestamp": datetime.now().isoformat(),
                "query": query,
                "sub_intent": sub_intent,
                "failed_code": failed_code,
                "fixed_code": fixed_code,
                "error": error
            }
            
            repair_log.append(repair_entry)
            
            # Save updated log
            with open(log_file, 'w') as f:
                json.dump(repair_log, f, indent=2)
            
            self.logger.info(f"✓ Logged code repair to {log_file}")
            
        except Exception as e:
            self.logger.warning(f"Failed to log code repair: {str(e)}")
    
    # ========================================================================
    # CAUSAL ANALYSIS INTEGRATION
    # ========================================================================
    
    def _get_csv_basename_from_state(self, state: Dict) -> str:
        """
        Get CSV filename (basename only) from state - thread-safe for multi-user Flask app
        
        Args:
            state: Current state dict containing csv_file_path from request context
            
        Returns:
            str: CSV filename (basename) or "In-memory DataFrame" if not available
        """
        csv_path = state.get("csv_file_path")
        if csv_path and isinstance(csv_path, str):
            import os
            return os.path.basename(csv_path)
        return "In-memory DataFrame"
    
    def _check_causal_cache(self, workbook_id: str, chart_name: str) -> Optional[Dict]:
        """
        Check if causal results exist in cache for this workbook+chart combination
        
        Args:
            workbook_id: Tableau workbook ID
            chart_name: Chart/worksheet name
            
        Returns:
            Cached results dict or None if not found
        """
        try:
            import json
            import os
            
            if not os.path.exists(self.causal_cache_path):
                self.logger.info(f"[CACHE MISS] Cache file does not exist: {self.causal_cache_path}")
                return None
            
            with open(self.causal_cache_path, 'r') as f:
                cache_data = json.load(f)
            
            # NEW nested structure: cache[workbook_id][chart_name]
            if workbook_id in cache_data and chart_name in cache_data[workbook_id]:
                self.logger.info(f"[CACHE HIT] ✓ Found cached causal results for: {workbook_id}/{chart_name}")
                return cache_data[workbook_id][chart_name]
            
            self.logger.info(f"[CACHE MISS] No cache entry for: {workbook_id}/{chart_name}")
            return None
            
        except Exception as e:
            self.logger.warning(f"[CACHE ERROR] Cache check failed: {e}")
            return None
    
    def _cache_causal_results(self, workbook_id: str, chart_name: str, 
                              causal_results: Dict, state: Dict):
        """
        Write causal analysis results to cache with nested structure by workbook
        Auto-creates cache file if it doesn't exist
        
        Args:
            workbook_id: Tableau workbook ID
            chart_name: Chart/worksheet name
            causal_results: Results from _run_causal_analysis
            state: Current state dict with stage1 info
        """
        try:
            import json
            import os
            from datetime import datetime
            
            self.logger.info(f"[CACHE WRITE] Caching causal results for: {workbook_id}/{chart_name}")
            
            # Load existing cache or create new
            cache_data = {}
            if os.path.exists(self.causal_cache_path):
                try:
                    with open(self.causal_cache_path, 'r') as f:
                        cache_data = json.load(f)
                    self.logger.info(f"[CACHE WRITE] Loaded existing cache with {len(cache_data)} workbooks")
                except Exception as e:
                    self.logger.warning(f"[CACHE WRITE] Error reading existing cache, creating new: {e}")
                    cache_data = {}
            else:
                self.logger.info(f"[CACHE WRITE] Cache file doesn't exist, will create new")
            
            # Extract top 5 features
            top_drivers = causal_results.get("top_drivers", [])
            top_5_features = [feat for feat, _ in top_drivers[:5]]
            
            # Build feature details
            feature_details = []
            for i, (feature, data) in enumerate(top_drivers[:5]):
                detail = {
                    'feature': feature,
                    'llm_reasoning': data.get('llm_reasoning', 'Statistical selection'),
                    'combined_score': data.get('combined_score', 0.0),
                    'rank': i + 1
                }
                feature_details.append(detail)
            
            # Get stage1 info for x/y axes
            stage1 = state.get("stage1_columns", {})
            
            # === MULTI-METRIC SUPPORT: Handle both single and multiple y-axes ===
            # Check if this is multi-metric analysis (y_columns) or single metric (y_column)
            y_columns_list = causal_results.get("y_columns", [])
            if not y_columns_list:
                # Single metric - wrap in list
                single_y = causal_results.get("y_column")
                y_columns_list = [single_y] if single_y else []
            
            # Filter out None values
            y_columns_list = [y for y in y_columns_list if y]
            
            # Primary y-axis for backward compatibility (first in list)
            y_primary = y_columns_list[0] if y_columns_list else None
            
            self.logger.info(f"[CACHE WRITE] Y-axes to cache: {y_columns_list}")
            
            # Create cache entry with all metadata (like old code lines 2523-2535)
            cache_entry = {
                'top_5_features': top_5_features,
                'domain_type': causal_results.get("domain", "Unknown"),
                'x_axis_detected': causal_results.get("x_axis_column"),  # Primary X-axis (backward compatibility)
                'x_axes_detected': [causal_results.get("x_axis_column")] if causal_results.get("x_axis_column") else [],
                'y_axis_detected': y_primary,  # Primary metric (backward compatibility)
                'y_axes_detected': y_columns_list,  # NEW: All metrics for multi-axis charts
                'xy_detection_confidence': 0.95,
                'xy_detection_reasoning': f"Detected from stage1 analysis: X={causal_results.get('x_axis_column')}, Y={y_columns_list}",
                'feature_details': feature_details,
                'timestamp': datetime.now().isoformat() + 'Z',
                'csv_file_used': self._get_csv_basename_from_state(state)  # Thread-safe CSV filename from request context
            }
            
            # Create nested structure: cache[workbook_id][chart_name]
            if workbook_id not in cache_data:
                cache_data[workbook_id] = {}
            
            cache_data[workbook_id][chart_name] = cache_entry
            
            # Write cache file (auto-creates if doesn't exist)
            with open(self.causal_cache_path, 'w') as f:
                json.dump(cache_data, f, indent=2)
            
            self.logger.info(f"[CACHE WRITE] ✓ Cached results written successfully")
            self.logger.info(f"[CACHE WRITE]   Top features: {top_5_features}")
            
        except Exception as e:
            self.logger.error(f"[CACHE WRITE] Failed to cache results: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
    
    def _run_causal_analysis(self, state: Dict, df: pd.DataFrame) -> Optional[Dict]:
        """
        Run UniversalCausalAnalyzer when needed
        Checks cache FIRST to avoid expensive recomputation
        NO HARDCODING: Uses detected columns and data
        """
        self.logger.info("="*80)
        self.logger.info("=== RUNNING UNIVERSAL CAUSAL ANALYZER ===")
        self.logger.info("="*80)
        
        try:
            stage1 = state.get("stage1_columns", {})
            selected_chart = state.get("selected_chart", "")
            chart_context = state.get("chart_context", {})
            workbook_id = chart_context.get("workbook_id")
            
            # === CACHE CHECK FIRST (avoid expensive recomputation) ===
            if workbook_id and selected_chart:
                self.logger.info(f"[CACHE CHECK] Checking cache for: {workbook_id}/{selected_chart}")
                cached_result = self._check_causal_cache(workbook_id, selected_chart)
                
                if cached_result:
                    self.logger.info("="*80)
                    self.logger.info("✓ USING CACHED CAUSAL RESULTS (skipping expensive analysis)")
                    self.logger.info(f"  Cached features: {cached_result['top_5_features']}")
                    self.logger.info(f"  Cached domain: {cached_result.get('domain_type', 'Unknown')}")
                    self.logger.info("="*80)
                    
                    # Return in same format as fresh analysis
                    feature_details = cached_result.get("feature_details", [])
                    top_drivers = []
                    for i, feat in enumerate(cached_result["top_5_features"]):
                        detail = feature_details[i] if i < len(feature_details) else {}
                        top_drivers.append((
                            feat,
                            {
                                "combined_score": detail.get("combined_score", 0.0),
                                "llm_reasoning": detail.get("llm_reasoning", "Cached result")
                            }
                        ))
                    
                    return {
                        "top_drivers": top_drivers,
                        "domain": cached_result.get("domain_type", "Unknown"),
                        "analysis_type": "cached",
                        "y_column": cached_result.get("y_axis_detected"),
                        "x_axis_column": cached_result.get("x_axis_detected")
                    }
            else:
                self.logger.info("[CACHE CHECK] Skipping cache check (no workbook_id or chart_name)")
            
            # === CACHE MISS: Run expensive causal analysis ===
            self.logger.info("="*80)
            self.logger.info("No cache found - running FRESH causal analysis...")
            self.logger.info("="*80)
            
            # Get parameters from Stage 1
            y_column = stage1.get("target_metric_column")
            x_axis_column = stage1.get("date_column")
            
            # === SMART CAUSAL ROUTING: Choose between cached vs fresh analysis ===
            cache_valid = False
            use_cached_analyzer = False
            
            # Check if cache is valid for this query (for Y-axis detection only)
            if not y_column and workbook_id and selected_chart:
                # Load the causal analysis cache to get Y axes for this chart
                import json
                import os
                if self.causal_cache_path and os.path.exists(self.causal_cache_path):
                    try:
                        with open(self.causal_cache_path, 'r') as f:
                            causal_cache = json.load(f)
                        
                        # NEW nested structure: cache[workbook_id][chart_name]
                        if workbook_id in causal_cache and selected_chart in causal_cache[workbook_id]:
                            chart_cache = causal_cache[workbook_id][selected_chart]
                            y_axes = chart_cache.get("y_axes_detected", [])
                            x_axes = chart_cache.get("x_axes_detected", [])
                            
                            if y_axes:
                                # Use the first Y axis if not specified
                                y_column = y_axes[0]
                                self.logger.info(f"Using Y-axis from chart cache: {y_column}")
                                
                                # Cache is valid if we found Y axis for this chart
                                cache_valid = True
                            
                            if x_axes and not x_axis_column:
                                # Use the first X axis if not specified
                                x_axis_column = x_axes[0]
                                self.logger.info(f"Using X-axis from chart cache: {x_axis_column}")
                        else:
                            self.logger.warning(f"Chart '{workbook_id}/{selected_chart}' not found in causal cache")
                    except Exception as e:
                        self.logger.warning(f"Failed to load causal cache: {str(e)}")
            
            # Final fallback if still no target metric
            if not y_column:
                self.logger.warning("No target metric available for causal analysis")
                return None
            
            # === ROUTING DECISION: cached vs fresh analysis ===
            # Use cached analyzer if:
            # 1. Cache is valid (chart found in cache, Y axis detected)
            # 2. AND date column is available (time-series queries)
            # 
            # Use fresh analyzer if:
            # 1. Cache is invalid (no cached data for this chart)
            # 2. OR no date column (segmentation analysis needed)
            
            if cache_valid and x_axis_column:
                use_cached_analyzer = True
                self.logger.info("🔄 [ROUTING] Using CACHED analyzer (aniket.py)")
                self.logger.info(f"   - Cache valid: ✓")
                self.logger.info(f"   - Date column: {x_axis_column} ✓")
                from services.causal_feature_importance_aniket import analyze_universal_causal_impact_integrated
            else:
                use_cached_analyzer = False
                self.logger.info("🔄 [ROUTING] Using FLEXIBLE analyzer (analysis.py)")
                self.logger.info(f"   - Cache valid: {'✓' if cache_valid else '✗'}")
                self.logger.info(f"   - Date column: {x_axis_column or 'None ✗ (will use segmentation)'}")
                from services.causal_feature_importance_analysis import analyze_universal_causal_impact_integrated
            
            # Save DataFrame to temporary CSV
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as tmp:
                csv_path = tmp.name
                df.to_csv(csv_path, index=False)
            
            self.logger.info(f"Running causal analysis:")
            self.logger.info(f"  - Y column: {y_column}")
            self.logger.info(f"  - X axis: {x_axis_column}")
            self.logger.info(f"  - CSV: {csv_path}")
            
            # Run causal analyzer
            analyzer, top_drivers = analyze_universal_causal_impact_integrated(
                csv_path=csv_path,
                y_column=y_column,
                x_axis_column=x_axis_column,
                openai_client=self.llm_client,
                logger=self.logger,
                time_aggregation="daily",
                max_features=12
            )
            
            # Clean up temp file
            import os
            os.unlink(csv_path)
            
            self.logger.info(f"✓ Causal analysis complete: {len(top_drivers)} drivers found")
            
            # Convert numpy types to Python native types (for msgpack serialization)
            def convert_numpy_types(obj):
                """Recursively convert numpy types to Python native types"""
                import numpy as np
                if isinstance(obj, np.integer):
                    return int(obj)
                elif isinstance(obj, np.floating):
                    return float(obj)
                elif isinstance(obj, np.ndarray):
                    return obj.tolist()
                elif isinstance(obj, dict):
                    return {k: convert_numpy_types(v) for k, v in obj.items()}
                elif isinstance(obj, (list, tuple)):
                    return [convert_numpy_types(item) for item in obj]
                else:
                    return obj
            
            # Prepare results with converted types
            causal_results = {
                "top_drivers": convert_numpy_types(top_drivers),  # List of (feature, score) tuples
                "domain": str(analyzer.llm_selection.get('domain', 'Unknown')),
                "analysis_type": str(analyzer.analysis_type),
                "y_column": y_column,  # Return detected target metric
                "x_axis_column": x_axis_column  # Return detected date column for temporal analysis
            }
            
            # === WRITE TO CACHE for next time (only if we have workbook_id) ===
            if workbook_id and selected_chart:
                self._cache_causal_results(
                    workbook_id=workbook_id,
                    chart_name=selected_chart,
                    causal_results=causal_results,
                    state=state
                )
            else:
                self.logger.warning("[CACHE WRITE] Skipping cache write (no workbook_id or chart_name)")
            
            return causal_results
            
        except Exception as e:
            self.logger.error(f"Causal analysis failed: {str(e)}")
            self.logger.error(traceback.format_exc())
            return None
    
    def _run_multi_metric_causal_analysis(self, state: Dict, df: pd.DataFrame) -> Optional[Dict]:
        """
        Run causal analysis for MULTIPLE metrics (multi-Y-axis charts)
        Based on old-code approach: loops through all metrics, deduplicates features
        
        Returns: Aggregated results with y_columns (list) instead of y_column (single)
        """
        self.logger.info("="*80)
        self.logger.info("=== MULTI-METRIC CAUSAL ANALYSIS ===")
        self.logger.info("="*80)
        
        try:
            stage1 = state.get("stage1_columns", {})
            selected_chart = state.get("selected_chart", "")
            chart_context = state.get("chart_context", {})
            workbook_id = chart_context.get("workbook_id")
            
            # Get ALL metrics to analyze
            target_metrics = stage1.get("target_metrics", [])
            x_axis_column = stage1.get("date_column")
            
            if not target_metrics:
                self.logger.warning("[MULTI_METRIC] No target_metrics found, falling back to single metric")
                return self._run_causal_analysis(state, df)
            
            self.logger.info(f"[MULTI_METRIC] Analyzing {len(target_metrics)} metrics: {target_metrics}")
            self.logger.info(f"[MULTI_METRIC] X-axis: {x_axis_column}")
            
            # === CACHE CHECK FIRST (avoid expensive recomputation) ===
            if workbook_id and selected_chart:
                self.logger.info(f"[CACHE CHECK] Checking cache for multi-metric analysis: {workbook_id}/{selected_chart}")
                cached_result = self._check_causal_cache(workbook_id, selected_chart)
                
                if cached_result:
                    self.logger.info(f"[CACHE HIT] ✓ Using cached multi-metric results, skipping expensive analysis")
                    # Convert single-metric cache to multi-metric format if needed
                    if 'y_column' in cached_result and 'y_columns' not in cached_result:
                        cached_result['y_columns'] = target_metrics  # Add multi-metric field
                    return cached_result
                else:
                    self.logger.info(f"[CACHE MISS] No cached results found, running expensive causal analysis")
            else:
                self.logger.warning(f"[CACHE SKIP] No workbook_id or selected_chart, cannot use cache")
            
            # Choose analyzer type (same logic as single-metric)
            # Use cached analyzer if date column is available
            if x_axis_column:
                from services.causal_feature_importance_aniket import analyze_universal_causal_impact_integrated
                self.logger.info("🔄 [MULTI_METRIC] Using CACHED analyzer (aniket.py)")
            else:
                from services.causal_feature_importance_analysis import analyze_universal_causal_impact_integrated
                self.logger.info("🔄 [MULTI_METRIC] Using FLEXIBLE analyzer (analysis.py)")
            
            # Save DataFrame to temporary CSV (reused for all metrics)
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as tmp:
                csv_path = tmp.name
                df.to_csv(csv_path, index=False)
            
            # MULTI-METRIC LOOP (like old code lines 2465-2509)
            all_feature_names = []
            all_feature_details_dict = {}  # Use dict to track first occurrence
            domain_type = 'Business Analytics'  # Default
            
            for idx, y_column in enumerate(target_metrics):
                self.logger.info(f"\n[MULTI_METRIC] === Analyzing metric {idx+1}/{len(target_metrics)}: {y_column} ===")
                
                try:
                    # Run causal for this specific metric
                    analyzer, top_drivers = analyze_universal_causal_impact_integrated(
                        csv_path=csv_path,
                        y_column=y_column,
                        x_axis_column=x_axis_column,
                        openai_client=self.llm_client,
                        logger=self.logger,
                        time_aggregation="daily",
                        max_features=12
                    )
                    
                    # Extract domain type from first successful analyzer
                    if idx == 0:
                        domain_type = analyzer.llm_selection.get('domain', 'Business Analytics') if hasattr(analyzer, 'llm_selection') else 'Business Analytics'
                    
                    if top_drivers and len(top_drivers) > 0:
                        # Collect feature names from this metric
                        metric_features = [feat for feat, data in top_drivers]
                        all_feature_names.extend(metric_features)
                        
                        self.logger.info(f"[MULTI_METRIC] Metric '{y_column}' yielded {len(metric_features)} features: {metric_features}")
                        
                        # Store feature details for FIRST occurrence only (like old code lines 2492-2503)
                        for feature, data in top_drivers:
                            if feature not in all_feature_details_dict:
                                # First occurrence - store the details
                                detail = {
                                    'feature': feature,
                                    'llm_reasoning': data.get('llm_reasoning', 'Statistical selection'),
                                    'combined_score': data.get('combined_score', 0.0),
                                    'rank': len(all_feature_details_dict) + 1  # Sequential rank
                                }
                                all_feature_details_dict[feature] = detail
                                self.logger.info(f"[MULTI_METRIC] New feature '{feature}' added with rank {detail['rank']}")
                            else:
                                self.logger.info(f"[MULTI_METRIC] Feature '{feature}' already exists, skipping (keeping first occurrence)")
                    else:
                        self.logger.warning(f"[MULTI_METRIC] Metric '{y_column}' returned no features")
                        
                except Exception as metric_error:
                    self.logger.warning(f"[MULTI_METRIC] Metric '{y_column}' failed: {metric_error}")
                    continue
            
            # Clean up temp file
            import os
            os.unlink(csv_path)
            
            # Deduplication using list(set()) (like old code line 2512)
            unique_feature_names = list(set(all_feature_names))
            self.logger.info(f"[MULTI_METRIC] Deduplication: {len(all_feature_names)} total features → {len(unique_feature_names)} unique features")
            self.logger.info(f"[MULTI_METRIC] Unique features: {unique_feature_names}")
            
            # Build final feature_details list from dict
            feature_details = list(all_feature_details_dict.values())
            
            if not unique_feature_names:
                self.logger.error("[MULTI_METRIC] No features found across any metric")
                return None
            
            # Convert to top_drivers format (return all unique features like old code)
            top_drivers = []
            for feat_name in unique_feature_names:
                feat_detail = all_feature_details_dict.get(feat_name, {})
                top_drivers.append((
                    feat_name,
                    {
                        'combined_score': feat_detail.get('combined_score', 0.0),
                        'llm_reasoning': feat_detail.get('llm_reasoning', 'Multi-metric selection')
                    }
                ))
            
            # Convert numpy types
            def convert_numpy_types(obj):
                import numpy as np
                if isinstance(obj, np.integer):
                    return int(obj)
                elif isinstance(obj, np.floating):
                    return float(obj)
                elif isinstance(obj, np.ndarray):
                    return obj.tolist()
                elif isinstance(obj, dict):
                    return {k: convert_numpy_types(v) for k, v in obj.items()}
                elif isinstance(obj, (list, tuple)):
                    return [convert_numpy_types(item) for item in obj]
                else:
                    return obj
            
            # Prepare results with MULTIPLE y_columns
            causal_results = {
                "top_drivers": convert_numpy_types(top_drivers),
                "domain": str(domain_type),
                "analysis_type": "multi_metric",
                "y_column": target_metrics[0],  # Primary (backward compatibility)
                "y_columns": target_metrics,  # NEW: All metrics analyzed
                "x_axis_column": x_axis_column
            }
            
            self.logger.info(f"✓ Multi-metric causal analysis complete:")
            self.logger.info(f"  - Analyzed {len(target_metrics)} metrics: {target_metrics}")
            self.logger.info(f"  - Found {len(unique_feature_names)} unique drivers")
            self.logger.info(f"  - Top 5: {[d[0] for d in top_drivers]}")
            
            # === WRITE TO CACHE ===
            if workbook_id and selected_chart:
                self._cache_causal_results(
                    workbook_id=workbook_id,
                    chart_name=selected_chart,
                    causal_results=causal_results,
                    state=state
                )
            else:
                self.logger.warning("[CACHE WRITE] Skipping cache write (no workbook_id or chart_name)")
            
            return causal_results
            
        except Exception as e:
            self.logger.error(f"Multi-metric causal analysis failed: {str(e)}")
            self.logger.error(traceback.format_exc())
            return None
    
    # ========================================================================
    # VALIDATOR: RESULTS VALIDATION
    # ========================================================================
    
    def _validate_results_tool(self, state: Dict) -> Dict:
        """
        VALIDATOR: Check if results actually answer the query
        Triggers retry if mismatch detected
        """
        self.logger.info("="*80)
        self.logger.info("=== VALIDATOR: RESULTS vs QUERY VALIDATION ===")
        self.logger.info("="*80)
        
        try:
            query = state.get("query", "")
            stage1 = state.get("stage1_columns", {})
            stage2 = state.get("stage2_plan", {})
            execution_results = state.get("execution_results", {})
            
            # Check for errors - but only fail if there's NO corresponding successful result
            # (Error entries may persist even after successful retry/repair)
            error_keys = [k for k in execution_results.keys() if "_error" in k]
            fatal_errors = []
            
            for error_key in error_keys:
                # Extract the base name (e.g., "categorical_breakdown" from "categorical_breakdown_error")
                base_name = error_key.replace("_error", "")
                
                # Check if there's a successful result for this sub-intent
                if base_name not in execution_results:
                    # Error with no successful result - this is a real failure
                    fatal_errors.append(error_key)
                else:
                    # Stale error entry but we have a successful result - safe to ignore
                    self.logger.info(f"🧹 Ignoring stale error entry '{error_key}' - successful result exists for '{base_name}'")
            
            if fatal_errors:
                self.logger.warning(f"Execution errors (no successful results): {fatal_errors}")
                state["results_valid"] = False
                state["results_mismatch_query"] = False  # Don't retry on execution errors
                return state
            
            # Summarize execution results for LLM (exclude error entries)
            results_summary = {}
            for name, result in execution_results.items():
                # Skip error entries - we only want successful results in the summary
                if "_error" in name:
                    continue
                    
                if isinstance(result, list) and len(result) > 0:
                    # Sample first 3 rows
                    results_summary[name] = {
                        "sample_rows": result[:3],
                        "row_count": len(result),
                        "columns": list(result[0].keys()) if result else []
                    }
                elif isinstance(result, dict):
                    results_summary[name] = {
                        "keys": list(result.keys())[:10],
                        "sample_values": {k: result[k] for k in list(result.keys())[:3]}
                    }
            
            class ResultValidation(BaseModel):
                """Validation of results against query"""
                results_answer_query: bool = Field(
                    description="Do the results actually answer the user's question?"
                )
                
                mentioned_columns_present: bool = Field(
                    description="If query mentions specific columns, are they in results?"
                )
                
                correct_analysis_type: bool = Field(
                    description="Is the analysis type appropriate for the query?"
                )
                
                results_have_data: bool = Field(
                    description="Are results non-empty and meaningful?"
                )
                
                issues: List[str] = []
                corrections_needed: List[str] = []
                confidence: float
            
            response = self.llm_client.beta.chat.completions.parse(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": """You are validating if analysis results answer the user's query.

CRITICAL CHECKS:
1. If user asked "how X affects Y" where X is a specific column:
   - Results MUST show breakdown by X
   - X should appear as a column or grouping variable in results
   
2. If user asked "what affects Y" (general):
   - Results should show multiple factors/correlations
   
3. If user asked "correlation between X and Y" or "correlation between X and other metrics":
   - Results should show correlations WITH X (X as the target)
   - It's CORRECT to show X's correlations with ALL numeric columns, not just Y
   - For "correlation between X and Y": showing X's correlations with multiple columns is VALID
   - For "correlation between X and other metrics": showing X's correlations with many columns is EXPECTED
   - The key validation: Is X the target metric in the correlation analysis?
   
4. Are results non-empty and meaningful?
5. Do results contain actual numbers/statistics?

BE STRICT: If results don't match query, mark as invalid and suggest corrections.
BUT FOR CORRELATION QUERIES: Be lenient if X is the target and multiple correlations are shown."""},
                    {"role": "user", "content": f"""
User Query: {query}

Stage 1 (Detected Columns):
{json.dumps(stage1, indent=2)}

Stage 2 (Analysis Plan):
{json.dumps(stage2, indent=2)}

Execution Results Summary:
{json.dumps(results_summary, indent=2)}

Does this analysis answer the user's query?
"""}
                ],
                response_format=ResultValidation
            )
            
            validation = response.choices[0].message.parsed
            
            # Check if retry is needed
            should_retry = False
            if not validation.results_answer_query or not validation.mentioned_columns_present:
                self.logger.warning(f"⚠️ Results validation failed!")
                self.logger.warning(f"   Issues: {validation.issues}")
                
                # Deserialize retry manager from dict
                retry_manager_dict = state.get("retry_manager_dict")
                retry_attempt = state.get("retry_attempt", 0)
                
                if retry_manager_dict:
                    retry_manager = AnalysisRetryManager.from_dict(retry_manager_dict)
                    
                    if retry_manager.should_retry(retry_attempt):
                        # Record failure
                        failure_reason = " | ".join(validation.issues)
                        retry_manager.record_failure(
                            retry_attempt,
                            failure_reason,
                            stage1,
                            stage2
                        )
                        
                        # Get correction suggestions
                        corrections = retry_manager.get_correction_suggestions()
                        corrections.extend(validation.corrections_needed)
                        
                        state["retry_attempt"] = retry_attempt + 1
                        state["results_mismatch_query"] = True
                        state["retry_corrections"] = corrections
                        
                        # Store updated retry manager back as dict
                        state["retry_manager_dict"] = retry_manager.to_dict()
                        
                        # Reset stages for retry
                        state["stage2_completed"] = False
                        state["plan_validated"] = False
                        state["stage3_1_completed"] = False
                        state["stage3_2_completed"] = False
                        
                        self.logger.info(f"🔄 Retry {state['retry_attempt']}/{retry_manager.max_retries}")
                        state["messages"].append(f"🔄 Retrying analysis (attempt {state['retry_attempt']})")
                        
                        should_retry = True
                    else:
                        self.logger.error("❌ Max retries reached, continuing with partial results")
                        state["results_valid"] = False
                        state["results_mismatch_query"] = False
                else:
                    self.logger.warning("No retry manager found in state")
                    state["results_valid"] = False
                    state["results_mismatch_query"] = False
            else:
                # Validation passed
                state["results_valid"] = True
                state["results_mismatch_query"] = False
                self.logger.info("✅ Results validation passed")
            
            state["result_validation"] = validation.dict()
            
            if not should_retry:
                # Only mark as validated if not retrying
                state["results_valid"] = validation.results_answer_query
            
        except Exception as e:
            self.logger.error(f"Results validation failed: {str(e)}")
            state["results_valid"] = True  # Continue anyway
            state["results_mismatch_query"] = False
        
        return state
    
    # ========================================================================
    # STAGE 3.3: INSIGHT FORMATTING
    # ========================================================================
    
    def _stage3_3_insight_formatting_tool(self, state: Dict) -> Dict:
        """
        Stage 3.3: Format results with ACTUAL NUMBERS
        User-friendly presentation of computed statistics
        """
        self.logger.info("="*80)
        self.logger.info("=== STAGE 3.3: INSIGHT FORMATTING (WITH ACTUAL NUMBERS) ===")
        self.logger.info("="*80)
        
        try:
            query = state.get("query", "")
            execution_results = state.get("execution_results", {})
            stage1 = state.get("stage1_columns", {})
            stage2 = state.get("stage2_plan", {})
            causal_results = state.get("causal_analysis_results")
            
            insights = []
            
            # Format general_drivers (sub-intent specific: driver-by-driver breakdown)
            if "general_drivers" in execution_results:
                result = execution_results["general_drivers"]
                if isinstance(result, list) and len(result) > 0:
                    insights.append("\n**🎯 Key Drivers Analysis:**\n")
                    
                    for driver_result in result:
                        driver_name = driver_result.get('driver', 'Unknown')
                        data = driver_result.get('data', [])
                        
                        if data:
                            insights.append(f"\n**{driver_name.replace('_', ' ').title()}:**")
                            
                            for idx, row in enumerate(data[:5], 1):
                                # Get values dynamically from row
                                count = row.get('count', 0)
                                pct = row.get('percentage', 0)
                                # Get the driver value (column name matches driver_name)
                                key_val = row.get(driver_name, 'Unknown')
                                
                                insights.append(
                                    f"  {idx}. {key_val}: {int(count):,} tickets ({pct:.1f}%)"
                                )
            
            # Format specific feature impact
            for key, result in execution_results.items():
                if "_impact" in key and key != "multiple_feature_impact" and isinstance(result, list) and len(result) > 0:
                    feature = key.replace("_impact", "")
                    insights.append(f"\n**{feature.replace('_', ' ').title()} Impact Analysis:**\n")
                    
                    # Detect column structure dynamically from first row
                    if result:
                        sample_row = result[0]
                        row_keys = set(sample_row.keys())
                        
                        # Find aggregation columns dynamically
                        avg_col = next((k for k in row_keys if k.startswith('avg_') or k == 'average' or k == 'mean'), None)
                        total_col = next((k for k in row_keys if k.startswith('total_') or k == 'total' or k == 'sum'), None)
                        std_col = next((k for k in row_keys if k.startswith('std_') or k == 'std' or k == 'stddev'), None)
                        
                    for idx, row in enumerate(result[:5], 1):
                        feature_value = row.get(feature, 'Unknown')
                        
                        # Pattern 1: Count metrics (has count and percentage)
                        if 'count' in row and 'percentage' in row:
                            insights.append(
                                f"{idx}. **{feature_value}**: "
                                f"{int(row.get('count', 0)):,} tickets "
                                f"({row.get('percentage', 0):.1f}% of total)"
                            )
                        
                        # Pattern 2: Value metrics with average (flexible column names)
                        elif avg_col and avg_col in row:
                            parts = [f"{idx}. **{feature_value}**:"]
                            
                            # Add average
                            avg_val = row.get(avg_col, 0)
                            parts.append(f"avg {avg_val:.2f}")
                            
                            # Add total if present
                            if total_col and total_col in row:
                                total_val = row.get(total_col, 0)
                                parts.append(f"total {total_val:.1f}")
                            
                            # Add count if present
                            if 'count' in row:
                                count_val = row.get('count', 0)
                                parts.append(f"({int(count_val):,} records)")
                            
                            # Add std if present
                            if std_col and std_col in row:
                                std_val = row.get(std_col, 0)
                                parts.append(f"±{std_val:.2f}")
                            
                            insights.append(" | ".join(parts))
                        
                        # Pattern 3: Generic fallback - show available numeric columns
                        else:
                            parts = [f"{idx}. **{feature_value}**:"]
                            for col, val in row.items():
                                if col != feature and isinstance(val, (int, float)):
                                    if isinstance(val, float):
                                        parts.append(f"{col}={val:.2f}")
                                    else:
                                        parts.append(f"{col}={val:,}")
                            
                            # Only append if we found some numeric data
                            if len(parts) > 1:
                                insights.append(" | ".join(parts))
                            else:
                                # No numeric columns found - just show the feature value
                                insights.append(f"{idx}. **{feature_value}**")
            
            # Format multiple feature impact (interaction effects)
            if "multiple_feature_impact" in execution_results:
                result = execution_results["multiple_feature_impact"]
                if isinstance(result, list) and len(result) > 0:
                    # Get the actual feature columns from stage2
                    feature_cols = stage2.get("group_by_columns", [])
                    
                    if len(feature_cols) >= 2:
                        insights.append(f"\n**Multiple Feature Impact Analysis:**\n")
                        insights.append(f"_Interaction: {' × '.join(feature_cols[:2])}_\n")
                        
                        # Detect if it's count-based or value-based from first row
                        if result:
                            sample_row = result[0]
                            has_count = 'count' in sample_row and 'percentage' in sample_row
                            
                            # Find aggregation columns dynamically
                            avg_col = next((k for k in sample_row.keys() if k.startswith('avg_')), None)
                            total_col = next((k for k in sample_row.keys() if k.startswith('total_')), None)
                            
                            for idx, row in enumerate(result[:5], 1):
                                # Get the actual feature values from the DataFrame columns
                                feature1_val = row.get(feature_cols[0], 'Unknown')
                                feature2_val = row.get(feature_cols[1], 'Unknown')
                                combination = f"({feature1_val}, {feature2_val})"
                                
                                # Format based on metric type
                                if has_count:
                                    # Count-based metric
                                    insights.append(
                                        f"{idx}. **{combination}**: "
                                        f"{int(row.get('count', 0)):,} tickets "
                                        f"({row.get('percentage', 0):.1f}% of total)"
                                    )
                                elif avg_col:
                                    # Value-based metric with average
                                    parts = [f"{idx}. **{combination}**:"]
                                    parts.append(f"avg {row.get(avg_col, 0):.2f}")
                                    
                                    if total_col:
                                        parts.append(f"total {row.get(total_col, 0):.1f}")
                                    
                                    if 'count' in row:
                                        parts.append(f"({int(row.get('count', 0)):,} records)")
                                    
                                    insights.append(" | ".join(parts))
                                else:
                                    # Fallback: show whatever numeric values exist
                                    parts = [f"{idx}. **{combination}**:"]
                                    for col, val in row.items():
                                        if col not in feature_cols and isinstance(val, (int, float)):
                                            if isinstance(val, float):
                                                parts.append(f"{col}={val:.2f}")
                                            else:
                                                parts.append(f"{col}={val:,}")
                                    
                                    if len(parts) > 1:
                                        insights.append(" | ".join(parts))
            
            # Format categorical breakdown
            if "categorical_breakdown" in execution_results:
                result = execution_results["categorical_breakdown"]
                if isinstance(result, list) and len(result) > 0:
                    group_cols = stage2.get("group_by_columns", [])
                    insights.append(f"\n**Breakdown by {', '.join(group_cols)}:**\n")
                    
                    for idx, row in enumerate(result[:10], 1):  # Show top 10
                        key_col = group_cols[0] if group_cols else list(row.keys())[0]
                        key_val = row.get(key_col, 'Unknown')
                        
                        # Handle count metrics (has 'count' and 'percentage')
                        if 'count' in row and 'percentage' in row:
                            insights.append(
                                f"{idx}. **{key_val}**: "
                                f"{int(row.get('count', 0)):,} ({row.get('percentage', 0):.1f}%)"
                            )
                        # Handle aggregation metrics (has sum/mean/etc)
                        else:
                            # Find numeric columns (excluding the group column)
                            numeric_cols = [k for k in row.keys() if k != key_col and isinstance(row.get(k), (int, float))]
                            if numeric_cols:
                                # Format with first numeric column (usually sum or mean)
                                first_col = numeric_cols[0]
                                val = row.get(first_col, 0)
                                insights.append(
                                    f"{idx}. **{key_val}**: {first_col}={val:,.2f}"
                                )
                            else:
                                # Fallback: just show the key
                                insights.append(f"{idx}. **{key_val}**")
            
            # Format correlation_analysis
            if "correlation_analysis" in execution_results:
                result = execution_results["correlation_analysis"]
                if isinstance(result, list) and len(result) > 0:
                    target_metric = stage2.get("agg_column", "target")
                    insights.append(f"\n**Correlation Analysis (with {target_metric}):**\n")
                    for row in result[:10]:  # Show top 10 correlations
                        corr_val = row.get('correlation', 0)
                        column_name = row.get('column', 'Unknown')
                        if abs(corr_val) > 0.05:  # Lower threshold to show more results
                            direction = "positively" if corr_val > 0 else "negatively"
                            strength = "strong" if abs(corr_val) > 0.5 else "moderate" if abs(corr_val) > 0.3 else "weak"
                            insights.append(
                                f"• **{column_name}**: {strength} {direction} correlated "
                                f"({corr_val:+.3f})"
                            )
            
            # Format period over period
            if "period_over_period" in execution_results:
                result = execution_results["period_over_period"]
                if isinstance(result, list) and len(result) > 0:
                    insights.append("\n**Period-over-Period Analysis:**\n")
                    for row in result[-5:]:  # Last 5 periods
                        period = row.get('period', 'Unknown')
                        pct_change = row.get('pct_change', 0)
                        current = row.get('current', 0)
                        direction = "↑" if pct_change > 0 else "↓" if pct_change < 0 else "→"
                        insights.append(
                            f"• **{period}**: {int(current):,} "
                            f"({direction} {abs(pct_change):.1f}%)"
                        )
            
            # Format distribution
            if "distribution" in execution_results:
                result = execution_results["distribution"]
                if isinstance(result, list) and len(result) > 0:
                    stats = result[0]
                    insights.append("\n**Distribution Statistics:**\n")
                    insights.append(f"• Mean: {stats.get('mean', 0):.2f}")
                    insights.append(f"• Median: {stats.get('median', 0):.2f}")
                    insights.append(f"• Std Dev: {stats.get('std', 0):.2f}")
                    insights.append(f"• P90: {stats.get('p90', 0):.2f}")
                    insights.append(f"• P95: {stats.get('p95', 0):.2f}")
            
            # Format causal analysis if available
            if causal_results:
                top_drivers = causal_results.get("top_drivers", [])
                if top_drivers:
                    insights.append("\n**Key Drivers (from Causal Analysis):**\n")
                    for idx, (feature, data) in enumerate(top_drivers[:5], 1):
                        # Extract numeric score from dict or use as-is if already numeric
                        score = data.get('combined_score', 0) if isinstance(data, dict) else data
                        insights.append(
                            f"{idx}. **{feature}**: Impact score {score:.2f}"
                        )
            
            # Join all insights
            final_text = "\n".join(insights) if insights else "Analysis completed but no significant patterns found."
            
            # Generate visualizations for categorical results
            chart_image = None
            try:
                # Check if we have categorical breakdown or general_drivers results
                categorical_results = []
                
                # Collect categorical breakdown results
                if "categorical_breakdown" in execution_results:
                    result = execution_results["categorical_breakdown"]
                    if isinstance(result, list) and len(result) > 0:
                        # Convert to DataFrame for visualization
                        result_df = pd.DataFrame(result)
                        categorical_results.append({
                            'type': 'categorical_breakdown',
                            'df': result_df,
                            'name': ', '.join(stage2.get("group_by_columns", []))
                        })
                
                # Collect general_drivers results (multiple features)
                if "general_drivers" in execution_results:
                    result = execution_results["general_drivers"]
                    if isinstance(result, list) and len(result) > 0:
                        for driver_result in result:
                            driver_name = driver_result.get('driver', 'Unknown')
                            data = driver_result.get('data', [])
                            if data:
                                # Convert to DataFrame for visualization
                                result_df = pd.DataFrame(data)
                                categorical_results.append({
                                    'type': 'general_drivers',
                                    'df': result_df,
                                    'name': driver_name
                                })
                
                # Collect specific_feature_impact results (per-column keys)
                # Look for keys ending with "_impact" (excluding "multiple_feature_impact")
                for key, result in execution_results.items():
                    if key.endswith("_impact") and key != "multiple_feature_impact":
                        if isinstance(result, list) and len(result) > 0:
                            # Extract feature name from key (e.g., "account_manager_impact" -> "account_manager")
                            feature_name = key.replace("_impact", "")
                            result_df = pd.DataFrame(result)
                            categorical_results.append({
                                'type': 'specific_feature_impact',
                                'df': result_df,
                                'name': f'Impact of {feature_name}',
                                'operation_type': 'feature_impact'
                            })
                
                # Collect multiple_feature_impact results (interaction effects)
                if "multiple_feature_impact" in execution_results:
                    result = execution_results["multiple_feature_impact"]
                    if isinstance(result, list) and len(result) > 0:
                        result_df = pd.DataFrame(result)
                        features = stage2.get("group_by_columns", ["feature1", "feature2"])
                        categorical_results.append({
                            'type': 'multiple_feature_impact',
                            'df': result_df,
                            'name': f'Interaction: {" × ".join(features[:2])}',
                            'operation_type': 'interaction_heatmap'
                        })
                
                # Collect correlation_analysis results
                if "correlation_analysis" in execution_results:
                    result = execution_results["correlation_analysis"]
                    
                    # Handle both list and DataFrame results
                    if isinstance(result, list) and len(result) > 0:
                        result_df = pd.DataFrame(result)
                        target = stage2.get("agg_column", "target")
                        categorical_results.append({
                            'type': 'correlation_analysis',
                            'df': result_df,
                            'name': f'Correlations with {target}',
                            'operation_type': 'correlation'
                        })
                    elif isinstance(result, pd.DataFrame):
                        categorical_results.append({
                            'type': 'correlation_analysis',
                            'df': result,
                            'name': 'Correlation Matrix',
                            'operation_type': 'correlation'
                        })
                
                # Generate visualization for the first categorical result
                # (for multiple features, we'll create separate charts later)
                if categorical_results:
                    self.logger.info(f"[VIZ] Generating visualization for {len(categorical_results)} result(s)")
                    
                    # For now, visualize the first result
                    first_result = categorical_results[0]
                    operation_type = first_result.get('operation_type', 'categorical_breakdown')
                    
                    self.logger.info(f"[VIZ] Creating chart for {first_result['type']}: {first_result['name']}")
                    self.logger.info(f"[VIZ] Operation type: {operation_type}")
                    
                    viz_result = self.viz_service.create_intelligent_visualization(
                        query=query,
                        result_df=first_result['df'],
                        operation_type=operation_type
                    )
                    
                    if viz_result.get('needs_visualization') and viz_result.get('chart_image'):
                        chart_image = viz_result['chart_image']
                        self.logger.info(f"[VIZ] ✓ Chart generated successfully for {first_result['name']}")
                    else:
                        self.logger.warning(f"[VIZ] No visualization generated")
                        
            except Exception as e:
                self.logger.error(f"[VIZ] Visualization generation failed: {str(e)}")
                self.logger.error(traceback.format_exc())
            
            state["final_insights"] = {
                "formatted_text": final_text,
                "chart_image": chart_image,
                "raw_results": execution_results,
                "causal_results": causal_results
            }
            state["stage3_3_completed"] = True
            state["messages"].append("✓ Stage 3.3: Insights formatted with actual numbers")
            
            self.logger.info("✓ Stage 3.3 Complete: Insights formatted")
            
        except Exception as e:
            self.logger.error(f"Stage 3.3 failed: {str(e)}")
            self.logger.error(traceback.format_exc())
            state["errors"].append(f"Stage 3.3: {str(e)}")
            state["stage3_3_completed"] = True
        
        return state
    
    # ========================================================================
    # LAYER 6: DATA VALIDATION
    # ========================================================================
    
    def _layer6_data_validation(self, state: Dict, df: pd.DataFrame) -> Dict:
        """
        Layer 6: Data Validation - "Is it real?"
        Validates data quality, detects anomalies, checks for issues
        """
        validation_results = {
            "missing_data": {},
            "duplicates": 0,
            "outliers": {},
            "data_quality": "good",
            "warnings": []
        }
        
        try:
            # Check for missing data
            missing_pct = (df.isnull().sum() / len(df) * 100).to_dict()
            validation_results["missing_data"] = {k: v for k, v in missing_pct.items() if v > 0}
            
            # Check for duplicates
            validation_results["duplicates"] = df.duplicated().sum()
            
            # Data quality assessment
            if any(v > 20 for v in validation_results["missing_data"].values()):
                validation_results["data_quality"] = "poor"
                validation_results["warnings"].append("High missing data detected (>20%)")
            elif validation_results["duplicates"] > len(df) * 0.1:
                validation_results["data_quality"] = "fair"
                validation_results["warnings"].append(f"{validation_results['duplicates']} duplicate rows found")
            
            self.logger.info(f"✓ Data validation complete: {validation_results['data_quality']}")
            
        except Exception as e:
            self.logger.error(f"Data validation failed: {str(e)}")
            validation_results["warnings"].append(f"Validation error: {str(e)}")
        
        return validation_results
    
    # ========================================================================
    # LAYER 7: FORWARD VIEW
    # ========================================================================
    
    def _layer7_forward_view(self, state: Dict, causal_results: Dict) -> Dict:
        """
        Layer 7: Forward View - "What next?"
        Generates what-if scenarios and forward-looking insights
        """
        forward_view = {
            "scenarios": [],
            "recommendations": [],
            "risk_factors": []
        }
        
        try:
            # Extract top drivers from causal results
            top_drivers = causal_results.get("top_drivers", [])
            
            if top_drivers:
                # Generate scenario for top driver
                top_driver = top_drivers[0][0] if isinstance(top_drivers[0], tuple) else top_drivers[0]
                forward_view["scenarios"].append(
                    f"If {top_driver} remains constant, expect similar patterns"
                )
                forward_view["recommendations"].append(
                    f"Focus on optimizing {top_driver} for maximum impact"
                )
            
            self.logger.info(f"✓ Forward view generated: {len(forward_view['scenarios'])} scenarios")
            
        except Exception as e:
            self.logger.error(f"Forward view generation failed: {str(e)}")
        
        return forward_view
    
    # ========================================================================
    # LAYER 8: EXECUTIVE SUMMARY
    # ========================================================================
    
    def _layer8_executive_summary(self, state: Dict, all_results: Dict) -> str:
        """
        Layer 8: Executive Summary - 2-3 line takeaway
        Combines all layers into a coherent narrative
        """
        try:
            summary_parts = []
            
            # Layer 1: Trend summary
            if "trend_summary" in all_results:
                summary_parts.append("Analysis shows clear patterns in the data")
            
            # Layer 4: Causal drivers
            causal_results = all_results.get("causal_results", {})
            top_drivers = causal_results.get("top_drivers", [])
            if top_drivers:
                driver_names = [d[0] if isinstance(d, tuple) else d for d in top_drivers[:2]]
                summary_parts.append(f"primarily driven by {' and '.join(driver_names)}")
            
            # Layer 6: Data validation
            validation = all_results.get("data_validation", {})
            if validation.get("data_quality") == "good":
                summary_parts.append("Data quality is reliable")
            
            summary = ". ".join(summary_parts) + "."
            
            self.logger.info(f"✓ Executive summary generated: {len(summary)} chars")
            return summary
            
        except Exception as e:
            self.logger.error(f"Executive summary generation failed: {str(e)}")
            return "Analysis completed with insights provided above."
    
    # ========================================================================
    # 7-LAYER INTERPRETIVE ANALYSIS
    # ========================================================================
    
    def _generate_seven_layer_analysis(self, state: Dict, query: str) -> Dict:
        """
        Generate 7-layer interpretive analysis from temporal comparison data
        Now supports multiple metrics - generates separate analysis for each
        """
        try:
            final_result = state.get("final_result", {})
            all_metric_results = final_result.get("all_metric_results", [])
            
            # If no individual results stored, fall back to old single-analysis behavior
            if not all_metric_results:
                self.logger.info("[7-LAYER] No all_metric_results, using legacy single analysis")
                return self._generate_single_seven_layer_analysis(final_result, query, state)
            
            # Generate 7-layer analysis for EACH metric
            self.logger.info(f"[7-LAYER] Generating analysis for {len(all_metric_results)} metric(s)")
            all_analyses = []
            
            for metric_result in all_metric_results:
                delta_metrics = metric_result.get('delta_metrics', {})
                top_drivers = metric_result.get('top_drivers', [])
                comparison_context = metric_result.get('comparison_context', "")
                
                if not delta_metrics or not isinstance(delta_metrics, dict):
                    self.logger.warning(f"[7-LAYER] Skipping metric due to missing delta_metrics")
                    continue
                
                metric_name = delta_metrics.get('metric', 'unknown')
                self.logger.info(f"[7-LAYER] Generating for metric: {metric_name}")
                
                sections = []
                
                # ============================================================
                # CRITICAL: Use tables for interpretive_temporal_comparison
                # ============================================================
                # Layer 1: Trend Summary - USE TABLE (no LLM)
                trend_table = self._generate_trend_summary_table(delta_metrics, comparison_context)
                if trend_table:
                    sections.append({"title": "Trend Summary", "content": trend_table, "layer": 1, "description": "Overall change between periods with absolute and percentage values"})
                    self.logger.info("[7-LAYER] Added Trend Summary TABLE")
                
                # Layer 2: Segment Breakdown - USE TABLE (no LLM)
                segments_table = self._generate_segment_breakdown_table(top_drivers, delta_metrics)
                if segments_table:
                    sections.append({"title": "Segment Breakdown", "content": segments_table, "layer": 2, "description": "All categories in a single unified view, sorted by magnitude of impact"})
                    self.logger.info("[7-LAYER] Added Segment Breakdown TABLE")
                
                # Layer 3: Key Drivers - USE TABLE (no LLM)
                drivers_table = self._generate_key_drivers_table(top_drivers, delta_metrics)
                if drivers_table:
                    sections.append({"title": "Key Drivers", "content": drivers_table, "layer": 3, "description": "Categories split into positive and negative contributors to see what increased vs decreased"})
                    self.logger.info("[7-LAYER] Added Key Drivers TABLE")
                # ============================================================
                
                # Layer 5: Temporal Pattern (keep LLM-generated)
                temporal = self._generate_temporal_pattern(delta_metrics, comparison_context, state)
                if temporal:
                    sections.append({"title": "Temporal Pattern", "content": temporal, "layer": 5})
                
                # NEW: Get required context for new sections
                date_column = state.get("stage1_columns", {}).get("date_column")
                chart_name = state.get("selected_chart", "")
                
                # NEW: Layer 6 - General Stats (only if date_column available)
                if date_column:
                    general_stats = self._generate_general_stats_section(state, metric_name, date_column)
                    if general_stats:
                        sections.append({"title": "General Stats", "content": general_stats, "layer": 6})
                
                # NEW: Layer 7 - Impactful Columns (only if chart_name available)
                if chart_name:
                    chart_context = state.get("chart_context", {})
                    workbook_id = chart_context.get("workbook_id")
                    impactful_cols = self._generate_impactful_columns_section(chart_name, metric_name, workbook_id)
                    if impactful_cols:
                        sections.append({"title": "Impactful Columns", "content": impactful_cols, "layer": 7})
                
                # NEW: Layer 8 - Anomalies (only if date_column available)
                if date_column:
                    anomalies = self._generate_anomalies_section(state, metric_name, date_column)
                    if anomalies:
                        sections.append({"title": "Anomalies", "content": anomalies, "layer": 8})
                
                # Executive Summary (keep LLM-generated)
                exec_summary = self._generate_executive_summary_seven_layer(sections, delta_metrics, top_drivers, query)
                
                analysis = {
                    "metric": metric_name,
                    "executive_summary": exec_summary,
                    "sections": sections,
                    "query": query,
                    "comparison_context": comparison_context
                }
                
                all_analyses.append(analysis)
                self.logger.info(f"[7-LAYER] ✓ Generated analysis for {metric_name}")
            
            if not all_analyses:
                self.logger.warning("[7-LAYER] No analyses generated")
                return None
            
            # Return structure with multiple analyses
            return {
                "type": "multi_metric",
                "analyses": all_analyses,
                "metric_count": len(all_analyses)
            }
            
        except Exception as e:
            self.logger.error(f"7-layer generation failed: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return None
    
    def _generate_single_seven_layer_analysis(self, final_result: Dict, query: str, state: Dict = None) -> Dict:
        """
        Legacy single-metric 7-layer generation (fallback)
        """
        try:
            # Use empty state dict if not provided for backwards compatibility
            if state is None:
                state = {}
            
            delta_metrics = final_result.get("delta", {})
            top_drivers = final_result.get("top_drivers", [])
            comparison_context = final_result.get("comparison_context", "")
            
            if not delta_metrics or not isinstance(delta_metrics, dict):
                return None
            
            sections = []
            
            # ============================================================
            # CRITICAL: Use tables for interpretive_temporal_comparison
            # ============================================================
            # Layer 1: Trend Summary - USE TABLE (no LLM)
            trend_table = self._generate_trend_summary_table(delta_metrics, comparison_context)
            if trend_table:
                sections.append({"title": "Trend Summary", "content": trend_table, "layer": 1, "description": "Overall change between periods with absolute and percentage values"})
                self.logger.info("[7-LAYER] Added Trend Summary TABLE (single metric)")
            
            # Layer 2: Segment Breakdown - USE TABLE (no LLM)
            segments_table = self._generate_segment_breakdown_table(top_drivers, delta_metrics)
            if segments_table:
                sections.append({"title": "Segment Breakdown", "content": segments_table, "layer": 2, "description": "All categories in a single unified view, sorted by magnitude of impact"})
                self.logger.info("[7-LAYER] Added Segment Breakdown TABLE (single metric)")
            
            # Layer 3: Key Drivers - USE TABLE (no LLM)
            drivers_table = self._generate_key_drivers_table(top_drivers, delta_metrics)
            if drivers_table:
                sections.append({"title": "Key Drivers", "content": drivers_table, "layer": 3, "description": "Categories split into positive and negative contributors to see what increased vs decreased"})
                self.logger.info("[7-LAYER] Added Key Drivers TABLE (single metric)")
            # ============================================================
            
            # Layer 5: Temporal Pattern (keep LLM-generated)
            temporal = self._generate_temporal_pattern(delta_metrics, comparison_context, {'final_result': final_result})
            if temporal:
                sections.append({"title": "Temporal Pattern", "content": temporal, "layer": 5})
            
            # NEW: Get required context for new sections
            metric_name = delta_metrics.get('metric', 'unknown')
            date_column = state.get("stage1_columns", {}).get("date_column") if state else None
            chart_name = state.get("selected_chart", "") if state else ""
            
            # NEW: Layer 6 - General Stats (only if date_column available and state provided)
            if date_column and state:
                general_stats = self._generate_general_stats_section(state, metric_name, date_column)
                if general_stats:
                    sections.append({"title": "General Stats", "content": general_stats, "layer": 6})
            
            # NEW: Layer 7 - Impactful Columns (only if chart_name available)
            if chart_name:
                chart_context = state.get("chart_context", {}) if state else {}
                workbook_id = chart_context.get("workbook_id")
                impactful_cols = self._generate_impactful_columns_section(chart_name, metric_name, workbook_id)
                if impactful_cols:
                    sections.append({"title": "Impactful Columns", "content": impactful_cols, "layer": 7})
            
            # NEW: Layer 8 - Anomalies (only if date_column available and state provided)
            if date_column and state:
                anomalies = self._generate_anomalies_section(state, metric_name, date_column)
                if anomalies:
                    sections.append({"title": "Anomalies", "content": anomalies, "layer": 8})
            
            # Executive Summary (keep LLM-generated)
            exec_summary = self._generate_executive_summary_seven_layer(sections, delta_metrics, top_drivers, query)
            
            return {
                "type": "single_metric",
                "executive_summary": exec_summary,
                "sections": sections,
                "query": query,
                "comparison_context": comparison_context
            }
        except Exception as e:
            self.logger.error(f"Single 7-layer generation failed: {e}")
            return None
    
    # ============================================================================
    # NEW: TABLE GENERATION FUNCTIONS FOR INTERPRETIVE TEMPORAL COMPARISON
    # ============================================================================
    
    def _generate_trend_summary_table(self, delta_metrics: Dict, comparison_context: str) -> str:
        """Generate trend summary as HTML table with all metric data"""
        try:
            metric = delta_metrics.get('metric', 'metric')
            delta = delta_metrics.get('delta', 0)
            delta_pct = delta_metrics.get('delta_pct', 0)
            period1_value = delta_metrics.get('period1_value', 0)
            period2_value = delta_metrics.get('period2_value', 0)
            
            # Extract period labels from comparison_context (e.g., "March 2025 vs February 2025")
            periods = comparison_context.split(' vs ')
            period2_label = periods[0].strip() if len(periods) > 0 else "Period 2"
            period1_label = periods[1].strip() if len(periods) > 1 else "Period 1"
            
            # Generate HTML table
            table_html = f"""<table style="width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 14px;">
<thead>
<tr style="background-color: #f0f0f0; border-bottom: 2px solid #ddd;">
<th style="padding: 10px; text-align: left; border: 1px solid #ddd;">Period</th>
<th style="padding: 10px; text-align: right; border: 1px solid #ddd;">{metric}</th>
<th style="padding: 10px; text-align: right; border: 1px solid #ddd;">Absolute Change</th>
<th style="padding: 10px; text-align: right; border: 1px solid #ddd;">% Change</th>
</tr>
</thead>
<tbody>
<tr style="border-bottom: 1px solid #ddd;">
<td style="padding: 10px; border: 1px solid #ddd;">{period1_label}</td>
<td style="padding: 10px; text-align: right; border: 1px solid #ddd;">{period1_value:,.2f}</td>
<td style="padding: 10px; text-align: right; border: 1px solid #ddd;">-</td>
<td style="padding: 10px; text-align: right; border: 1px solid #ddd;">-</td>
</tr>
<tr style="border-bottom: 1px solid #ddd;">
<td style="padding: 10px; border: 1px solid #ddd;">{period2_label}</td>
<td style="padding: 10px; text-align: right; border: 1px solid #ddd;">{period2_value:,.2f}</td>
<td style="padding: 10px; text-align: right; border: 1px solid #ddd; color: {'green' if delta > 0 else 'red' if delta < 0 else 'black'}; font-weight: bold;">{delta:+,.2f}</td>
<td style="padding: 10px; text-align: right; border: 1px solid #ddd; color: {'green' if delta_pct > 0 else 'red' if delta_pct < 0 else 'black'}; font-weight: bold;">{delta_pct:+.1f}%</td>
</tr>
</tbody>
</table>"""
            # Remove all newlines to prevent them from being converted to <br> tags in frontend
            return table_html.replace('\n', '').strip()
            
        except Exception as e:
            self.logger.error(f"Trend summary table generation failed: {e}")
            return None
    
    def _generate_segment_breakdown_table(self, top_drivers: List[Dict], delta_metrics: Dict) -> str:
        """Generate segment breakdown as HTML table with ALL category data, sorted by absolute value"""
        try:
            if not top_drivers:
                return None
            
            metric = delta_metrics.get('metric', 'metric')
            
            # Collect ALL category changes from ALL drivers
            all_segments = []
            for driver in top_drivers:  # ALL features, not just top 3
                feature = driver.get('feature', 'unknown')
                top_changes = driver.get('top_category_changes', [])
                
                # Get ALL categories, not just top 2
                for cat_change in top_changes:  # ALL categories
                    category = cat_change.get('category', 'unknown')
                    shift = cat_change.get('shift', 0)
                    period1_value = cat_change.get('period1_value', 0)
                    period2_value = cat_change.get('period2_value', 0)
                    shift_pct = cat_change.get('shift_pct', 0)
                    
                    all_segments.append({
                        'feature': feature,
                        'category': category,
                        'shift': shift,
                        'period1_value': period1_value,
                        'period2_value': period2_value,
                        'shift_pct': shift_pct
                    })
            
            if not all_segments:
                return None
            
            # Sort by absolute shift value (descending)
            all_segments.sort(key=lambda x: abs(x['shift']), reverse=True)
            
            # Generate table rows
            rows_html = ""
            for segment in all_segments:
                shift_color = 'green' if segment['shift'] > 0 else 'red' if segment['shift'] < 0 else 'black'
                row = f"""<tr style="border-bottom: 1px solid #ddd;">
<td style="padding: 8px; border: 1px solid #ddd;">{segment['feature']}</td>
<td style="padding: 8px; border: 1px solid #ddd;">{segment['category']}</td>
<td style="padding: 8px; text-align: right; border: 1px solid #ddd;">{segment['period1_value']:,.2f}</td>
<td style="padding: 8px; text-align: right; border: 1px solid #ddd;">{segment['period2_value']:,.2f}</td>
<td style="padding: 8px; text-align: right; border: 1px solid #ddd; color: {shift_color}; font-weight: bold;">{segment['shift']:+,.2f}</td>
<td style="padding: 8px; text-align: right; border: 1px solid #ddd; color: {shift_color};">{segment['shift_pct']:+.1f}%</td>
</tr>"""
                rows_html += row.replace('\n', '')
            
            # Generate complete table
            table_html = f"""<table style="width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 13px;">
<thead>
<tr style="background-color: #f0f0f0; border-bottom: 2px solid #ddd;">
<th style="padding: 8px; text-align: left; border: 1px solid #ddd;">Feature</th>
<th style="padding: 8px; text-align: left; border: 1px solid #ddd;">Category</th>
<th style="padding: 8px; text-align: right; border: 1px solid #ddd;">Period 1</th>
<th style="padding: 8px; text-align: right; border: 1px solid #ddd;">Period 2</th>
<th style="padding: 8px; text-align: right; border: 1px solid #ddd;">Contribution</th>
<th style="padding: 8px; text-align: right; border: 1px solid #ddd;">% Change</th>
</tr>
</thead>
<tbody>{rows_html}</tbody>
</table>"""
            # Remove all newlines to prevent them from being converted to <br> tags in frontend
            return table_html.replace('\n', '').strip()
            
        except Exception as e:
            self.logger.error(f"Segment breakdown table generation failed: {e}")
            return None
    
    def _generate_key_drivers_table(self, top_drivers: List[Dict], delta_metrics: Dict) -> str:
        """Generate key drivers as TWO HTML tables (positive and negative), with ALL category data"""
        try:
            if not top_drivers:
                return None
            
            metric = delta_metrics.get('metric', 'metric')
            
            # Collect ALL positive and negative contributors from ALL drivers
            positive_segments = []
            negative_segments = []
            
            for driver in top_drivers:  # ALL features, not just top 3
                feature = driver.get('feature', 'unknown')
                top_changes = driver.get('top_category_changes', [])
                
                # Get ALL categories, not just top 3
                for cat_change in top_changes:  # ALL categories
                    category = cat_change.get('category', 'unknown')
                    shift = cat_change.get('shift', 0)
                    period1_value = cat_change.get('period1_value', 0)
                    period2_value = cat_change.get('period2_value', 0)
                    shift_pct = cat_change.get('shift_pct', 0)
                    
                    segment_data = {
                        'feature': feature,
                        'category': category,
                        'shift': shift,
                        'period1_value': period1_value,
                        'period2_value': period2_value,
                        'shift_pct': shift_pct
                    }
                    
                    if shift > 0:
                        positive_segments.append(segment_data)
                    elif shift < 0:
                        negative_segments.append(segment_data)
            
            # Sort by absolute shift value (descending)
            positive_segments.sort(key=lambda x: abs(x['shift']), reverse=True)
            negative_segments.sort(key=lambda x: abs(x['shift']), reverse=True)
            
            # Generate positive contributors table
            positive_html = ""
            if positive_segments:
                positive_rows = ""
                for segment in positive_segments:
                    row = f"""<tr style="border-bottom: 1px solid #ddd;">
<td style="padding: 8px; border: 1px solid #ddd;">{segment['feature']}</td>
<td style="padding: 8px; border: 1px solid #ddd;">{segment['category']}</td>
<td style="padding: 8px; text-align: right; border: 1px solid #ddd;">{segment['period1_value']:,.2f}</td>
<td style="padding: 8px; text-align: right; border: 1px solid #ddd;">{segment['period2_value']:,.2f}</td>
<td style="padding: 8px; text-align: right; border: 1px solid #ddd; color: green; font-weight: bold;">{segment['shift']:+,.2f}</td>
<td style="padding: 8px; text-align: right; border: 1px solid #ddd; color: green;">{segment['shift_pct']:+.1f}%</td>
</tr>"""
                    positive_rows += row.replace('\n', '')
                
                positive_html = f"""<div style="margin-bottom: 20px;">
<h4 style="margin: 10px 0 5px 0; color: green;">✅ Positive Contributors</h4>
<table style="width: 100%; border-collapse: collapse; margin: 0; font-size: 13px;">
<thead>
<tr style="background-color: #f0f0f0; border-bottom: 2px solid #ddd;">
<th style="padding: 8px; text-align: left; border: 1px solid #ddd;">Feature</th>
<th style="padding: 8px; text-align: left; border: 1px solid #ddd;">Category</th>
<th style="padding: 8px; text-align: right; border: 1px solid #ddd;">Period 1</th>
<th style="padding: 8px; text-align: right; border: 1px solid #ddd;">Period 2</th>
<th style="padding: 8px; text-align: right; border: 1px solid #ddd;">Impact</th>
<th style="padding: 8px; text-align: right; border: 1px solid #ddd;">% Change</th>
</tr>
</thead>
<tbody>{positive_rows}</tbody>
</table>
</div>"""
                positive_html = positive_html.replace('\n', '')
            
            # Generate negative contributors table
            negative_html = ""
            if negative_segments:
                negative_rows = ""
                for segment in negative_segments:
                    row = f"""<tr style="border-bottom: 1px solid #ddd;">
<td style="padding: 8px; border: 1px solid #ddd;">{segment['feature']}</td>
<td style="padding: 8px; border: 1px solid #ddd;">{segment['category']}</td>
<td style="padding: 8px; text-align: right; border: 1px solid #ddd;">{segment['period2_value']:,.2f}</td>
<td style="padding: 8px; text-align: right; border: 1px solid #ddd;">{segment['period1_value']:,.2f}</td>
<td style="padding: 8px; text-align: right; border: 1px solid #ddd; color: red; font-weight: bold;">{segment['shift']:,.2f}</td>
<td style="padding: 8px; text-align: right; border: 1px solid #ddd; color: red;">{segment['shift_pct']:+.1f}%</td>
</tr>"""
                    negative_rows += row.replace('\n', '')
                
                negative_html = f"""<div style="margin-bottom: 20px;">
<h4 style="margin: 10px 0 5px 0; color: red;">⛔ Negative Contributors</h4>
<table style="width: 100%; border-collapse: collapse; margin: 0; font-size: 13px;">
<thead>
<tr style="background-color: #f0f0f0; border-bottom: 2px solid #ddd;">
<th style="padding: 8px; text-align: left; border: 1px solid #ddd;">Feature</th>
<th style="padding: 8px; text-align: left; border: 1px solid #ddd;">Category</th>
<th style="padding: 8px; text-align: right; border: 1px solid #ddd;">Period 1</th>
<th style="padding: 8px; text-align: right; border: 1px solid #ddd;">Period 2</th>
<th style="padding: 8px; text-align: right; border: 1px solid #ddd;">Impact</th>
<th style="padding: 8px; text-align: right; border: 1px solid #ddd;">% Change</th>
</tr>
</thead>
<tbody>{negative_rows}</tbody>
</table>
</div>"""
                negative_html = negative_html.replace('\n', '')
            
            # Combine both tables
            combined_html = positive_html + negative_html
            # Remove all newlines to prevent them from being converted to <br> tags in frontend
            return combined_html.replace('\n', '').strip() if combined_html else None
            
        except Exception as e:
            self.logger.error(f"Key drivers table generation failed: {e}")
            return None
    
    # ============================================================================
    # END: TABLE GENERATION FUNCTIONS
    # ============================================================================
    
    def _generate_trend_summary(self, delta_metrics: Dict, comparison_context: str) -> str:
        """Generate trend summary using LLM"""
        try:
            metric = delta_metrics.get('metric', 'metric')
            delta = delta_metrics.get('delta', 0)
            delta_pct = delta_metrics.get('delta_pct', 0)
            period1_value = delta_metrics.get('period1_value', 0)
            period2_value = delta_metrics.get('period2_value', 0)
            
            prompt = f"""Summarize this metric change in 2-3 sentences.

METRIC: {metric}
CHANGE: {delta:+,.2f} ({delta_pct:+.1f}%)
FROM: {period1_value:,.2f} TO: {period2_value:,.2f}
COMPARISON: {comparison_context}

State the overall change, contextualize the magnitude, note absolute values if relevant.
Be professional and data-driven. Example: "{metric} increased by 14% quarter-over-quarter, rising from 245 to 279. This represents significant growth."

Generate trend summary:"""

            response = self.llm_client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=200
            )
            
            return response.choices[0].message.content.strip()
        except Exception as e:
            self.logger.error(f"Trend summary failed: {e}")
            return None
    
    def _generate_segment_breakdown(self, top_drivers: List[Dict], delta_metrics: Dict) -> str:
        """Generate segment breakdown using LLM"""
        try:
            if not top_drivers:
                return None
            
            metric = delta_metrics.get('metric', 'metric')
            
            # Extract top category changes from each driver
            segments_list = []
            for driver in top_drivers[:3]:  # Top 3 features
                feature = driver.get('feature', 'unknown')
                top_changes = driver.get('top_category_changes', [])
                
                # Get top 2 categories per feature
                for cat_change in top_changes[:2]:
                    category = cat_change.get('category', 'unknown')
                    shift = cat_change.get('shift', 0)
                    segments_list.append(f"- {feature} = {category}: {shift:+,.2f}")
            
            if not segments_list:
                return None
            
            segments_text = "\n".join(segments_list)
            
            prompt = f"""Analyze segment breakdown in 3-4 sentences.

METRIC: {metric}
TOP SEGMENTS BY CONTRIBUTION:
{segments_text}

Identify top 2-3 segments that drove change, quantify contributions, note patterns.
Example: "Most gain came from West region (+28 units) and Product A (+15 units). Northeast offset with -12 units."

Generate segment breakdown:"""

            response = self.llm_client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=300
            )
            
            return response.choices[0].message.content.strip()
        except Exception as e:
            self.logger.error(f"Segment breakdown failed: {e}")
            return None
    
    def _generate_driver_analysis(self, top_drivers: List[Dict], delta_metrics: Dict) -> str:
        """Generate driver analysis using LLM"""
        try:
            if not top_drivers:
                return None
            
            metric = delta_metrics.get('metric', 'metric')
            
            # Extract positive and negative contributors from top_category_changes
            positive_list = []
            negative_list = []
            
            for driver in top_drivers[:3]:  # Top 3 features
                feature = driver.get('feature', 'unknown')
                top_changes = driver.get('top_category_changes', [])
                
                for cat_change in top_changes[:3]:  # Top 3 categories per feature
                    category = cat_change.get('category', 'unknown')
                    shift = cat_change.get('shift', 0)
                    
                    if shift > 0:
                        positive_list.append(f"- {feature} = {category}: {shift:+,.2f}")
                    elif shift < 0:
                        negative_list.append(f"- {feature} = {category}: {shift:+,.2f}")
            
            positive_text = "\n".join(positive_list[:5]) if positive_list else "None"
            negative_text = "\n".join(negative_list[:5]) if negative_list else "None"
            
            prompt = f"""Analyze positive/negative drivers in 3-4 sentences.

METRIC: {metric}
POSITIVE: {positive_text}
NEGATIVE: {negative_text}

Highlight key positive contributors, identify offsetting forces, explain net effect.
Example: "Key positives were ad efficiency and repeat customers (+45 units). Higher churn offset with -18. Net driven by improved marketing ROI."

Generate driver analysis:"""

            response = self.llm_client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=300
            )
            
            return response.choices[0].message.content.strip()
        except Exception as e:
            self.logger.error(f"Driver analysis failed: {e}")
            return None
    
    def _generate_temporal_pattern(self, delta_metrics: Dict, comparison_context: str, state: Dict) -> str:
        """Generate data-driven temporal comparisons (DoD/WoW/MoM/QoQ/YoY)"""
        try:
            temporal_result = state.get('temporal_entities', {})
            if not temporal_result:
                self.logger.warning("[TEMPORAL_PATTERN] No temporal_entities in state")
                return None
            
            comparison_type = temporal_result.get('comparison_type', 'sequential')
            period1 = temporal_result.get('period1')
            period2 = temporal_result.get('period2')
            
            if not period2:
                self.logger.warning("[TEMPORAL_PATTERN] No period2 found")
                return None
            
            # Determine if range or single period
            if comparison_type == 'explicit' and period1 and period2:
                if self._is_range_query(period1, period2):
                    self.logger.info("[TEMPORAL_PATTERN] Detected range query")
                    return self._calculate_range_progression(state, delta_metrics, period1, period2)
            
            # Default: single-period comparisons
            self.logger.info("[TEMPORAL_PATTERN] Using single-period comparisons")
            return self._calculate_single_period_comparisons(state, delta_metrics, period2)
            
        except Exception as e:
            self.logger.error(f"[TEMPORAL_PATTERN] Failed: {e}")
            return None
    
    def _is_range_query(self, period1: Dict, period2: Dict) -> bool:
        """Detect if this is a range query using integer comparison"""
        if not period1 or not period2:
            return False
        
        period_type = period2.get('type', 'monthly')
        
        try:
            if period_type == 'monthly':
                year_diff = period2.get('year', 0) - period1.get('year', 0)
                month_diff = period2.get('month', 0) - period1.get('month', 0)
                total_diff = (year_diff * 12) + month_diff
                is_range = total_diff > 1
                self.logger.info(f"[RANGE_DETECTION] Monthly: diff={total_diff}, is_range={is_range}")
                return is_range
            
            elif period_type == 'quarterly':
                year_diff = period2.get('year', 0) - period1.get('year', 0)
                quarter_diff = period2.get('quarter', 0) - period1.get('quarter', 0)
                total_diff = (year_diff * 4) + quarter_diff
                is_range = total_diff > 1
                self.logger.info(f"[RANGE_DETECTION] Quarterly: diff={total_diff}, is_range={is_range}")
                return is_range
            
            elif period_type == 'weekly':
                year_diff = period2.get('year', 0) - period1.get('year', 0)
                week_diff = period2.get('week', 0) - period1.get('week', 0)
                total_diff = (year_diff * 52) + week_diff
                is_range = total_diff > 1
                self.logger.info(f"[RANGE_DETECTION] Weekly: diff={total_diff}, is_range={is_range}")
                return is_range
            
            elif period_type == 'yearly':
                year_diff = period2.get('year', 0) - period1.get('year', 0)
                is_range = year_diff > 1
                self.logger.info(f"[RANGE_DETECTION] Yearly: diff={year_diff}, is_range={is_range}")
                return is_range
            
            # Fallback to date comparison
            start_date = period1.get('start_date')
            end_date = period2.get('end_date')
            if start_date and end_date:
                date_diff = (end_date - start_date).days
                is_range = date_diff > 32
                self.logger.info(f"[RANGE_DETECTION] Fallback: date_diff={date_diff}, is_range={is_range}")
                return is_range
            
            return False
            
        except Exception as e:
            self.logger.error(f"[RANGE_DETECTION] Error: {e}")
            return False
    
    def _calculate_single_period_comparisons(self, state: Dict, delta_metrics: Dict, target_period: Dict) -> str:
        """Calculate DoD/WoW/MoM/QoQ/YoY based on precedence"""
        try:
            self.logger.info("[SINGLE_PERIOD] === Starting single period comparisons ===")
            
            # Extract data requirements - reconstruct DataFrame from state
            # Get DataFrame from DataManager
            from services.data_manager import get_data_manager
            data_manager = get_data_manager()
            data_id = state.get("data_id")
            if not data_id:
                self.logger.warning("[SINGLE_PERIOD] No data_id in state")
                return None
            
            df = data_manager.get_data(data_id)
            self.logger.info(f"[SINGLE_PERIOD] DataFrame loaded: shape={df.shape}")
            
            stage1_columns = state.get('stage1_columns', {})
            date_column = stage1_columns.get('date_column')
            metric_column = delta_metrics.get('metric')
            
            # Try Tableau hint first
            chart_name = state.get("selected_chart", "")
            tableau_hint = None
            if chart_name and self.hints_manager:
                tableau_hint = self.hints_manager.get_hint(chart_name, metric_column)
            
            if tableau_hint:
                agg_method = tableau_hint
                self.logger.info(f"[SINGLE_PERIOD] Using Tableau hint: {tableau_hint}")
            else:
                agg_method = stage1_columns.get('recommended_aggregation', 'SUM')
            
            self.logger.info(f"[SINGLE_PERIOD] date_column='{date_column}', metric_column='{metric_column}', agg_method='{agg_method}'")
            
            if df is None or date_column is None or metric_column is None:
                self.logger.warning(f"[SINGLE_PERIOD] Missing required data: df={df is not None}, date_column={date_column}, metric_column={metric_column}")
                return None
            
            # Check if columns exist
            if date_column not in df.columns:
                self.logger.error(f"[SINGLE_PERIOD] date_column '{date_column}' not in DataFrame columns: {df.columns.tolist()}")
                return None
            
            if metric_column not in df.columns:
                self.logger.error(f"[SINGLE_PERIOD] metric_column '{metric_column}' not in DataFrame columns: {df.columns.tolist()}")
                return None
            
            self.logger.info(f"[SINGLE_PERIOD] target_period structure: {target_period}")
            
            # Get query granularity and data granularity
            temporal_result = state.get('temporal_entities', {})
            query_granularity = temporal_result.get('query_granularity', 'monthly')
            
            date_analysis = temporal_result.get('date_analysis', {})
            data_granularity = date_analysis.get('granularity', 'monthly')
            
            self.logger.info(f"[SINGLE_PERIOD] query_granularity='{query_granularity}', data_granularity='{data_granularity}'")
            
            comparisons = []
            
            # Granularity hierarchy: daily < weekly < monthly < quarterly < yearly
            granularity_order = {'daily': 0, 'weekly': 1, 'monthly': 2, 'quarterly': 3, 'yearly': 4}
            query_level = granularity_order.get(query_granularity, 2)
            data_level = granularity_order.get(data_granularity, 2)
            
            self.logger.info(f"[SINGLE_PERIOD] query_level={query_level}, data_level={data_level}")
            
            # 1. Match query granularity first (if data supports it)
            if data_level <= 0 and query_level == 0:  # DoD
                self.logger.info("[SINGLE_PERIOD] Attempting DoD calculation...")
                dod = self._calculate_comparison(df, date_column, metric_column, target_period, agg_method, 'DoD')
                if dod:
                    comparisons.append(dod)
                    self.logger.info(f"[SINGLE_PERIOD] DoD SUCCESS: {dod}")
                else:
                    self.logger.warning("[SINGLE_PERIOD] DoD returned None")
            
            if data_level <= 1 and query_level == 1:  # WoW
                self.logger.info("[SINGLE_PERIOD] Attempting WoW calculation...")
                wow = self._calculate_comparison(df, date_column, metric_column, target_period, agg_method, 'WoW')
                if wow:
                    comparisons.append(wow)
                    self.logger.info(f"[SINGLE_PERIOD] WoW SUCCESS: {wow}")
                else:
                    self.logger.warning("[SINGLE_PERIOD] WoW returned None")
            
            if data_level <= 2 and query_level == 2:  # MoM
                self.logger.info("[SINGLE_PERIOD] Attempting MoM calculation...")
                mom = self._calculate_comparison(df, date_column, metric_column, target_period, agg_method, 'MoM')
                if mom:
                    comparisons.append(mom)
                    self.logger.info(f"[SINGLE_PERIOD] MoM SUCCESS: {mom}")
                else:
                    self.logger.warning("[SINGLE_PERIOD] MoM returned None")
            
            # 2. Then broader contexts (always show if data available)
            if query_level < 3:  # Don't show QoQ for yearly queries
                self.logger.info("[SINGLE_PERIOD] Attempting QoQ calculation...")
                qoq = self._calculate_comparison(df, date_column, metric_column, target_period, agg_method, 'QoQ')
                if qoq:
                    comparisons.append(qoq)
                    self.logger.info(f"[SINGLE_PERIOD] QoQ SUCCESS: {qoq}")
                else:
                    self.logger.warning("[SINGLE_PERIOD] QoQ returned None")
            
            self.logger.info("[SINGLE_PERIOD] Attempting YoY calculation...")
            yoy = self._calculate_comparison(df, date_column, metric_column, target_period, agg_method, 'YoY')
            if yoy:
                comparisons.append(yoy)
                self.logger.info(f"[SINGLE_PERIOD] YoY SUCCESS: {yoy}")
            else:
                self.logger.warning("[SINGLE_PERIOD] YoY returned None")
            
            self.logger.info(f"[SINGLE_PERIOD] Total comparisons calculated: {len(comparisons)}")
            
            if not comparisons:
                self.logger.warning("[SINGLE_PERIOD] No comparisons calculated")
                return None
            
            result = "Temporal Comparisons:\n" + "\n".join(comparisons)
            self.logger.info(f"[SINGLE_PERIOD] Final result:\n{result}")
            return result
            
        except Exception as e:
            self.logger.error(f"[SINGLE_PERIOD] Failed: {e}")
            import traceback
            self.logger.error(f"[SINGLE_PERIOD] Traceback:\n{traceback.format_exc()}")
            return None
    
    def _calculate_comparison(self, df, date_column: str, metric_column: str, 
                            target_period: Dict, agg_method: str, comparison_type: str) -> str:
        """Calculate a specific temporal comparison (DoD/WoW/MoM/QoQ/YoY)"""
        try:
            self.logger.info(f"[{comparison_type}] === Starting calculation ===")
            self.logger.info(f"[{comparison_type}] target_period: {target_period}")
            
            # Get previous period
            prev_period = self._get_previous_period(target_period, comparison_type)
            if not prev_period:
                self.logger.warning(f"[{comparison_type}] Could not calculate previous period")
                return None
            
            self.logger.info(f"[{comparison_type}] prev_period: {prev_period}")
            
            # Aggregate for both periods
            self.logger.info(f"[{comparison_type}] Aggregating target period...")
            target_value = self._aggregate_for_period(df, date_column, metric_column, target_period, agg_method)
            self.logger.info(f"[{comparison_type}] target_value={target_value}")
            
            self.logger.info(f"[{comparison_type}] Aggregating previous period...")
            prev_value = self._aggregate_for_period(df, date_column, metric_column, prev_period, agg_method)
            self.logger.info(f"[{comparison_type}] prev_value={prev_value}")
            
            if target_value is None or prev_value is None:
                self.logger.warning(f"[{comparison_type}] Missing data for comparison (target={target_value}, prev={prev_value})")
                return None
            
            # Calculate percentage change
            if prev_value == 0:
                if target_value == 0:
                    self.logger.info(f"[{comparison_type}] Both values are 0, skipping")
                    return None
                self.logger.warning(f"[{comparison_type}] Previous period was 0, cannot calculate percentage")
                return f"• {comparison_type}: N/A (previous period was 0)"
            
            pct_change = ((target_value - prev_value) / prev_value) * 100
            sign = "+" if pct_change >= 0 else ""
            
            # Format labels
            target_label = target_period.get('label', str(target_period))
            prev_label = prev_period.get('label', str(prev_period))
            
            result = f"• {comparison_type}: {sign}{pct_change:.1f}% ({target_label} vs {prev_label})"
            self.logger.info(f"[{comparison_type}] SUCCESS: {result}")
            return result
            
        except Exception as e:
            self.logger.error(f"[{comparison_type}] Calculation failed: {e}")
            import traceback
            self.logger.error(f"[{comparison_type}] Traceback:\n{traceback.format_exc()}")
            return None
    
    def _get_previous_period(self, period: Dict, comparison_type: str) -> Dict:
        """Calculate previous period based on comparison type"""
        try:
            self.logger.info(f"[GET_PREV_PERIOD][{comparison_type}] Input period: {period}")
            
            period_type = period.get('type', 'monthly')
            start_date = period.get('start_date')
            end_date = period.get('end_date')
            
            self.logger.info(f"[GET_PREV_PERIOD][{comparison_type}] period_type={period_type}, start_date={start_date}, end_date={end_date}")
            
            if not start_date or not end_date:
                self.logger.error(f"[GET_PREV_PERIOD][{comparison_type}] Missing start_date or end_date")
                return None
            
            # Calculate offset based on comparison type
            if comparison_type == 'DoD':
                prev_end = end_date - pd.Timedelta(days=1)
                prev_start = prev_end
                label = f"{prev_end.strftime('%Y-%m-%d')}"
            
            elif comparison_type == 'WoW':
                prev_start = start_date - pd.Timedelta(weeks=1)
                prev_end = end_date - pd.Timedelta(weeks=1)
                label = f"Week of {prev_start.strftime('%Y-%m-%d')}"
            
            elif comparison_type == 'MoM':
                prev_month = start_date.month - 1 if start_date.month > 1 else 12
                prev_year = start_date.year if start_date.month > 1 else start_date.year - 1
                prev_start = pd.Timestamp(year=prev_year, month=prev_month, day=1)
                last_day = pd.Timestamp(year=prev_year, month=prev_month, day=1) + pd.offsets.MonthEnd(0)
                prev_end = last_day
                label = f"{prev_start.strftime('%B %Y')}"
            
            elif comparison_type == 'QoQ':
                current_quarter = period.get('quarter')
                current_year = period.get('year')
                
                if current_quarter and current_year:
                    prev_quarter = current_quarter - 1 if current_quarter > 1 else 4
                    prev_year = current_year if current_quarter > 1 else current_year - 1
                    
                    # Calculate quarter start/end
                    quarter_start_month = (prev_quarter - 1) * 3 + 1
                    prev_start = pd.Timestamp(year=prev_year, month=quarter_start_month, day=1)
                    quarter_end_month = quarter_start_month + 2
                    prev_end = pd.Timestamp(year=prev_year, month=quarter_end_month, day=1) + pd.offsets.MonthEnd(0)
                    label = f"Q{prev_quarter} {prev_year}"
                else:
                    # Fallback: subtract 3 months
                    prev_start = start_date - pd.DateOffset(months=3)
                    prev_end = end_date - pd.DateOffset(months=3)
                    label = f"{prev_start.strftime('%B %Y')}"
            
            elif comparison_type == 'YoY':
                prev_start = start_date - pd.DateOffset(years=1)
                prev_end = end_date - pd.DateOffset(years=1)
                
                if period_type == 'monthly':
                    label = f"{prev_start.strftime('%B %Y')}"
                elif period_type == 'quarterly':
                    prev_quarter = period.get('quarter', 1)
                    label = f"Q{prev_quarter} {prev_start.year}"
                else:
                    label = f"{prev_start.year}"
            
            else:
                return None
            
            result = {
                'start_date': prev_start,
                'end_date': prev_end,
                'label': label,
                'type': period_type
            }
            
            self.logger.info(f"[GET_PREV_PERIOD][{comparison_type}] SUCCESS: {result}")
            return result
            
        except Exception as e:
            self.logger.error(f"[GET_PREV_PERIOD][{comparison_type}] Failed: {e}")
            import traceback
            self.logger.error(f"[GET_PREV_PERIOD][{comparison_type}] Traceback:\n{traceback.format_exc()}")
            return None
    
    def _aggregate_for_period(self, df, date_column: str, metric_column: str, 
                             period: Dict, agg_method: str):
        """Aggregate metric for a specific period"""
        try:
            start_date = period.get('start_date')
            end_date = period.get('end_date')
            period_label = period.get('label', 'unknown')
            
            self.logger.info(f"[AGGREGATE][{period_label}] start_date={start_date}, end_date={end_date}, agg_method={agg_method}")
            
            if start_date is None or end_date is None:
                self.logger.error(f"[AGGREGATE][{period_label}] Missing start_date or end_date")
                return None
            
            # Ensure date column is datetime
            if not pd.api.types.is_datetime64_any_dtype(df[date_column]):
                self.logger.info(f"[AGGREGATE][{period_label}] Converting date_column to datetime")
                df = df.copy()
                df[date_column] = pd.to_datetime(df[date_column], errors='coerce')
            
            self.logger.info(f"[AGGREGATE][{period_label}] df['{date_column}'].dtype={df[date_column].dtype}")
            self.logger.info(f"[AGGREGATE][{period_label}] df shape before filter: {df.shape}")
            
            # Filter data for period
            mask = (df[date_column] >= start_date) & (df[date_column] <= end_date)
            period_df = df[mask]
            
            self.logger.info(f"[AGGREGATE][{period_label}] Filtered df shape: {period_df.shape}, rows found: {len(period_df)}")
            
            if len(period_df) == 0:
                self.logger.warning(f"[AGGREGATE][{period_label}] No data for period")
                return None
            
            # Apply aggregation
            if agg_method == 'SUM':
                result = period_df[metric_column].sum()
            elif agg_method == 'COUNT':
                result = len(period_df)
            elif agg_method == 'COUNT_DISTINCT':
                result = period_df[metric_column].nunique()
            elif agg_method in ['AVG', 'MEAN']:
                result = period_df[metric_column].mean()
            elif agg_method == 'MIN':
                result = period_df[metric_column].min()
            elif agg_method == 'MAX':
                result = period_df[metric_column].max()
            else:
                # Default to sum
                self.logger.warning(f"[AGGREGATE][{period_label}] Unknown agg_method '{agg_method}', defaulting to SUM")
                result = period_df[metric_column].sum()
            
            self.logger.info(f"[AGGREGATE][{period_label}] Result: {result}")
            return result
                
        except Exception as e:
            self.logger.error(f"[AGGREGATE] Failed: {e}")
            import traceback
            self.logger.error(f"[AGGREGATE] Traceback:\n{traceback.format_exc()}")
            return None
    
    def _calculate_range_progression(self, state: Dict, delta_metrics: Dict, 
                                    period1: Dict, period2: Dict) -> str:
        """Show period-by-period progression for range queries"""
        try:
            # Extract data requirements - reconstruct DataFrame from state
            # Get DataFrame from DataManager
            from services.data_manager import get_data_manager
            data_manager = get_data_manager()
            data_id = state.get("data_id")
            if not data_id:
                self.logger.warning("[RANGE_PROGRESSION] No data_id in state")
                return None
            
            df = data_manager.get_data(data_id)
            stage1_columns = state.get('stage1_columns', {})
            date_column = stage1_columns.get('date_column')
            metric_column = delta_metrics.get('metric')
            
            # Try Tableau hint first
            chart_name = state.get("selected_chart", "")
            tableau_hint = None
            if chart_name and self.hints_manager:
                tableau_hint = self.hints_manager.get_hint(chart_name, metric_column)
            
            if tableau_hint:
                agg_method = tableau_hint
                self.logger.info(f"[RANGE_PROGRESSION] Using Tableau hint: {tableau_hint}")
            else:
                agg_method = stage1_columns.get('recommended_aggregation', 'SUM')
            
            if df is None or date_column is None or metric_column is None:
                return None
            
            # Get all periods in range
            periods = self._get_periods_in_range(period1, period2)
            if not periods or len(periods) < 2:
                self.logger.warning("[RANGE_PROGRESSION] Insufficient periods in range")
                return None
            
            results = []
            baseline = None
            prev_value = None
            
            for idx, period in enumerate(periods):
                value = self._aggregate_for_period(df, date_column, metric_column, period, agg_method)
                
                if value is None:
                    continue
                
                if idx == 0:
                    baseline = value
                    results.append(f"• {period['label']}: {value:,.0f} (baseline)")
                else:
                    # Calculate changes
                    pct_from_prev = ((value - prev_value) / prev_value * 100) if prev_value and prev_value != 0 else 0
                    pct_from_baseline = ((value - baseline) / baseline * 100) if baseline and baseline != 0 else 0
                    
                    sign_prev = "+" if pct_from_prev >= 0 else ""
                    sign_base = "+" if pct_from_baseline >= 0 else ""
                    
                    results.append(
                        f"• {period['label']}: {value:,.0f} "
                        f"({sign_prev}{pct_from_prev:.1f}% from prev, {sign_base}{pct_from_baseline:.1f}% from baseline)"
                    )
                
                prev_value = value
            
            if not results:
                return None
            
            return "Period-by-Period Progression:\n" + "\n".join(results)
            
        except Exception as e:
            self.logger.error(f"[RANGE_PROGRESSION] Failed: {e}")
            return None
    
    def _get_periods_in_range(self, period1: Dict, period2: Dict) -> list:
        """Generate all periods between period1 and period2"""
        try:
            period_type = period2.get('type', 'monthly')
            periods = []
            
            if period_type == 'monthly':
                start_year = period1.get('year')
                start_month = period1.get('month')
                end_year = period2.get('year')
                end_month = period2.get('month')
                
                current_year = start_year
                current_month = start_month
                
                while (current_year < end_year) or (current_year == end_year and current_month <= end_month):
                    start_date = pd.Timestamp(year=current_year, month=current_month, day=1)
                    last_day = (start_date + pd.offsets.MonthEnd(0)).day
                    end_date = pd.Timestamp(year=current_year, month=current_month, day=last_day)
                    
                    periods.append({
                        'start_date': start_date,
                        'end_date': end_date,
                        'label': f"{start_date.strftime('%B %Y')}",
                        'type': 'monthly',
                        'year': current_year,
                        'month': current_month
                    })
                    
                    current_month += 1
                    if current_month > 12:
                        current_month = 1
                        current_year += 1
            
            elif period_type == 'quarterly':
                start_year = period1.get('year')
                start_quarter = period1.get('quarter')
                end_year = period2.get('year')
                end_quarter = period2.get('quarter')
                
                current_year = start_year
                current_quarter = start_quarter
                
                while (current_year < end_year) or (current_year == end_year and current_quarter <= end_quarter):
                    start_month = (current_quarter - 1) * 3 + 1
                    start_date = pd.Timestamp(year=current_year, month=start_month, day=1)
                    end_month = start_month + 2
                    end_date = pd.Timestamp(year=current_year, month=end_month, day=1) + pd.offsets.MonthEnd(0)
                    
                    periods.append({
                        'start_date': start_date,
                        'end_date': end_date,
                        'label': f"Q{current_quarter} {current_year}",
                        'type': 'quarterly',
                        'year': current_year,
                        'quarter': current_quarter
                    })
                    
                    current_quarter += 1
                    if current_quarter > 4:
                        current_quarter = 1
                        current_year += 1
            
            elif period_type == 'yearly':
                start_year = period1.get('year')
                end_year = period2.get('year')
                
                for year in range(start_year, end_year + 1):
                    start_date = pd.Timestamp(year=year, month=1, day=1)
                    end_date = pd.Timestamp(year=year, month=12, day=31)
                    
                    periods.append({
                        'start_date': start_date,
                        'end_date': end_date,
                        'label': f"{year}",
                        'type': 'yearly',
                        'year': year
                    })
            
            else:
                # Fallback for weekly or other types - use date ranges
                start_date = period1.get('start_date')
                end_date = period2.get('end_date')
                
                if start_date and end_date:
                    periods.append(period1)
                    periods.append(period2)
            
            return periods
            
        except Exception as e:
            self.logger.error(f"[GET_PERIODS_IN_RANGE] Failed: {e}")
            return []
    
    def _generate_general_stats_section(self, state: Dict, metric_column: str, date_column: str) -> Optional[str]:
        """
        Generate general statistics: min, 25th percentile, median, 75th percentile, max, and outliers.
        Uses FULL CSV data with same aggregation logic as chart.
        
        Returns None if data unavailable - will gracefully skip this section.
        """
        try:
            self.logger.info(f"[GENERAL_STATS] Generating for metric: {metric_column}")
            
            # Get CSV data
            # Get DataFrame from DataManager
            from services.data_manager import get_data_manager
            data_manager = get_data_manager()
            data_id = state.get("data_id")
            if not data_id:
                self.logger.warning("[GENERAL_STATS] No data_id available")
                return None
            
            df = data_manager.get_data(data_id)
            
            if metric_column not in df.columns or date_column not in df.columns:
                self.logger.warning(f"[GENERAL_STATS] Missing columns: {metric_column} or {date_column}")
                return None
            
            # Try Tableau hint first, then stage1, then default
            chart_name = state.get("selected_chart", "")
            tableau_hint = None
            if chart_name and self.hints_manager:
                tableau_hint = self.hints_manager.get_hint(chart_name, metric_column)
            
            if tableau_hint:
                agg_mapping = {
                    'COUNT_DISTINCT': 'nunique', 'SUM': 'sum', 'AVG': 'mean', 
                    'MEAN': 'mean', 'COUNT': 'count', 'NUNIQUE': 'nunique',
                    'MIN': 'min', 'MAX': 'max', 'MEDIAN': 'median'
                }
                agg_method = agg_mapping.get(tableau_hint, 'sum')
                self.logger.info(f"[GENERAL_STATS] Using Tableau hint: {tableau_hint} → {agg_method}")
            else:
                stage1 = state.get("stage1_columns", {})
                recommended_agg = stage1.get("recommended_aggregation")
                if recommended_agg:
                    agg_mapping = {
                        'SUM': 'sum', 'AVG': 'mean', 'MEAN': 'mean', 'COUNT': 'count',
                        'COUNT_DISTINCT': 'nunique', 'NUNIQUE': 'nunique',
                        'MIN': 'min', 'MAX': 'max', 'MEDIAN': 'median'
                    }
                    agg_method = agg_mapping.get(recommended_agg, 'sum')
                else:
                    agg_method = 'sum'
            
            self.logger.info(f"[GENERAL_STATS] Using aggregation: {agg_method}")
            
            # Aggregate by date
            if agg_method == 'nunique':
                aggregated = df.groupby(date_column)[metric_column].nunique().reset_index()
            else:
                aggregated = df.groupby(date_column)[metric_column].agg(agg_method).reset_index()
            
            y_values = aggregated[metric_column]
            x_values = aggregated[date_column]
            
            # Calculate statistics
            min_val = float(y_values.min())
            max_val = float(y_values.max())
            median_val = float(y_values.median())
            percentile_25 = float(y_values.quantile(0.25))
            percentile_75 = float(y_values.quantile(0.75))
            
            # Find months for each statistic
            min_month = x_values.iloc[y_values.idxmin()]
            max_month = x_values.iloc[y_values.idxmax()]
            median_month = x_values.iloc[(y_values - median_val).abs().idxmin()]
            p25_month = x_values.iloc[(y_values - percentile_25).abs().idxmin()]
            p75_month = x_values.iloc[(y_values - percentile_75).abs().idxmin()]
            
            # Detect outliers using IQR method
            iqr = percentile_75 - percentile_25
            lower_bound = percentile_25 - 1.5 * iqr
            upper_bound = percentile_75 + 1.5 * iqr
            outliers = aggregated[(y_values < lower_bound) | (y_values > upper_bound)]
            
            # Format response
            lines = []
            lines.append(f"**Statistical Summary for {metric_column}:**")
            lines.append(f"• Min: {min_val:,.2f} (in {self._format_month_for_display(str(min_month))})")
            lines.append(f"• 25th Percentile: {percentile_25:,.2f} (in {self._format_month_for_display(str(p25_month))})")
            lines.append(f"• Median: {median_val:,.2f} (in {self._format_month_for_display(str(median_month))})")
            lines.append(f"• 75th Percentile: {percentile_75:,.2f} (in {self._format_month_for_display(str(p75_month))})")
            lines.append(f"• Max: {max_val:,.2f} (in {self._format_month_for_display(str(max_month))})")
            
            if len(outliers) > 0:
                lines.append(f"\n**Outliers (IQR method):**")
                for _, row in outliers.iterrows():
                    lines.append(f"• {self._format_month_for_display(str(row[date_column]))}: {row[metric_column]:,.2f}")
            else:
                lines.append(f"\n**Outliers:** None detected")
            
            result = "\n".join(lines)
            self.logger.info(f"[GENERAL_STATS] ✓ Generated successfully")
            return result
            
        except Exception as e:
            self.logger.error(f"[GENERAL_STATS] Failed: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return None
    
    def _generate_impactful_columns_section(self, chart_name: str, metric_column: str, workbook_id: str = None) -> Optional[str]:
        """
        Extract top_5_features from causal_analysis_cache.json for given workbook+chart.
        
        Args:
            chart_name: Name of the chart/worksheet
            metric_column: Metric being analyzed (for logging)
            workbook_id: Tableau workbook ID (optional, for nested cache lookup)
        
        Returns None if cache file missing or chart not in cache - will gracefully skip this section.
        """
        try:
            self.logger.info(f"[IMPACTFUL_COLS] Looking for: workbook={workbook_id}, chart={chart_name}, metric={metric_column}")
            
            if not chart_name:
                self.logger.warning("[IMPACTFUL_COLS] No chart name provided")
                return None
            
            # Read causal analysis cache
            cache_path = "causal_analysis_cache.json"
            if not os.path.exists(cache_path):
                self.logger.warning(f"[IMPACTFUL_COLS] Cache file not found: {cache_path}")
                return None
            
            with open(cache_path, 'r') as f:
                cache_data = json.load(f)
            
            # NEW nested structure: cache[workbook_id][chart_name]
            if workbook_id:
                # Use nested structure with workbook_id
                if workbook_id not in cache_data:
                    self.logger.warning(f"[IMPACTFUL_COLS] Workbook '{workbook_id}' not in cache")
                    return None
                
                if chart_name not in cache_data[workbook_id]:
                    self.logger.warning(f"[IMPACTFUL_COLS] Chart '{chart_name}' not in workbook cache")
                    return None
                
                chart_cache = cache_data[workbook_id][chart_name]
            else:
                # Fallback: try flat structure (for backward compatibility during transition)
                if chart_name not in cache_data:
                    self.logger.warning(f"[IMPACTFUL_COLS] Chart '{chart_name}' not in cache (no workbook_id provided)")
                    return None
                chart_cache = cache_data[chart_name]
            
            top_features = chart_cache.get("top_5_features", [])
            
            if not top_features:
                self.logger.warning(f"[IMPACTFUL_COLS] No top_5_features found for chart '{chart_name}'")
                return None
            
            # Format response
            lines = []
            lines.append(f"**Top Impactful Columns (from causal analysis):**")
            for i, feature in enumerate(top_features, 1):
                lines.append(f"{i}. {feature}")
            
            result = "\n".join(lines)
            self.logger.info(f"[IMPACTFUL_COLS] ✓ Found {len(top_features)} features")
            return result
            
        except Exception as e:
            self.logger.error(f"[IMPACTFUL_COLS] Failed: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return None
    
    def _generate_anomalies_section(self, state: Dict, metric_column: str, date_column: str) -> Optional[str]:
        """
        Run temporal anomaly detection on ALL available data (no temporal filtering).
        Uses same logic as _temporal_anomaly_detection_execution_tool but with temporal_filter=None.
        
        Returns None if detection fails - will gracefully skip this section.
        """
        try:
            self.logger.info(f"[ANOMALIES] Running detection for metric: {metric_column}")
            
            # Get stage info for aggregation
            stage1 = state.get("stage1_columns", {})
            
            # Smart data source selection (no temporal filter - get all data)
            df, data_source, mapped_metric = self._select_data_source(state, metric_column, temporal_filter=None)
            
            if df is None or df.empty:
                self.logger.warning("[ANOMALIES] No data available")
                return None
            
            self.logger.info(f"[ANOMALIES] Data source: {data_source}, shape: {df.shape}")
            
            # Try Tableau hint first, then stage1, then default
            chart_name = state.get("selected_chart", "")
            tableau_hint = None
            if chart_name and self.hints_manager:
                tableau_hint = self.hints_manager.get_hint(chart_name, metric_column)
            
            if tableau_hint:
                agg_mapping = {
                    'COUNT_DISTINCT': 'count_distinct', 'SUM': 'sum', 'AVG': 'mean',
                    'MEAN': 'mean', 'COUNT': 'count', 'NUNIQUE': 'count_distinct',
                    'MIN': 'min', 'MAX': 'max', 'MEDIAN': 'median'
                }
                aggregation = agg_mapping.get(tableau_hint, 'sum')
                self.logger.info(f"[ANOMALIES] Using Tableau hint: {tableau_hint} → {aggregation}")
            else:
                recommended_agg = stage1.get("recommended_aggregation")
                if recommended_agg:
                    agg_mapping = {
                        'SUM': 'sum', 'AVG': 'mean', 'MEAN': 'mean', 'COUNT': 'count',
                        'COUNT_DISTINCT': 'count_distinct', 'NUNIQUE': 'count_distinct',
                        'MIN': 'min', 'MAX': 'max', 'MEDIAN': 'median'
                    }
                    aggregation = agg_mapping.get(recommended_agg, 'sum')
                else:
                    aggregation = 'sum'
            
            # If using chart data, aggregation is already done
            if data_source == "chart_data":
                aggregation = "sum"
            
            self.logger.info(f"[ANOMALIES] Using aggregation: {aggregation}")
            
            # Initialize temporal anomaly detection service
            anomaly_service = TemporalAnomalyDetectionService(
                logger=self.logger,
                threshold=2.5
            )
            
            # Run detection (no temporal filter - analyze ALL data)
            results = anomaly_service.detect_temporal_anomalies(
                df=df,
                date_column=date_column,
                metric_column=mapped_metric,
                aggregation=aggregation,
                temporal_filter=None,  # No filtering - analyze all available data
                query=""
            )
            
            if not results.get("success"):
                self.logger.warning(f"[ANOMALIES] Detection failed: {results.get('error')}")
                return None
            
            total_anomalies = results.get("total_anomalies", 0)
            anomalies_list = results.get("anomalies", [])
            
            if total_anomalies == 0:
                return f"**Anomalies for {metric_column}:** None detected"
            
            # Format response
            lines = []
            lines.append(f"**Anomalies for {metric_column}:** {total_anomalies} anomalous period(s) detected")
            
            for anomaly in anomalies_list[:10]:  # Limit to top 10
                period = anomaly.get('period', 'Unknown')
                value = anomaly.get('value', 0)
                anomaly_type = anomaly.get('type', 'unknown')
                
                lines.append(f"• {period}: {value:,.2f} (type: {anomaly_type})")
            
            if total_anomalies > 10:
                lines.append(f"• ... and {total_anomalies - 10} more")
            
            result = "\n".join(lines)
            self.logger.info(f"[ANOMALIES] ✓ Found {total_anomalies} anomalies")
            return result
            
        except Exception as e:
            self.logger.error(f"[ANOMALIES] Failed: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return None
    
    def _format_month_for_display(self, month_str: str) -> str:
        """Helper to format month strings consistently"""
        try:
            # Handle various date formats
            if isinstance(month_str, str):
                # Try to parse as date
                for fmt in ['%Y-%m-%d', '%Y-%m', '%B', '%b']:
                    try:
                        dt = datetime.strptime(month_str, fmt)
                        return dt.strftime('%B %Y')
                    except:
                        continue
            return str(month_str)
        except:
            return str(month_str)
    
    def _generate_executive_summary_seven_layer(self, sections: List[Dict], delta_metrics: Dict, 
                                                 top_drivers: List[Dict], query: str) -> str:
        """Generate executive summary"""
        try:
            metric = delta_metrics.get('metric', 'metric')
            delta = delta_metrics.get('delta', 0)
            delta_pct = delta_metrics.get('delta_pct', 0)
            
            # OPTIMIZATION: Removed section_summaries - they only contained truncated HTML markup
            # section_summaries = []
            # for section in sections:
            #     content = section.get("content", "")
            #     snippet = content[:150] + "..." if len(content) > 150 else content
            #     section_summaries.append(snippet)
            
            # Extract top 3 categories from drivers
            top_categories = []
            for driver in top_drivers[:2]:  # Top 2 features
                feature = driver.get('feature', 'unknown')
                top_changes = driver.get('top_category_changes', [])
                
                for cat_change in top_changes[:2]:  # Top 2 categories per feature
                    category = cat_change.get('category', 'unknown')
                    shift = cat_change.get('shift', 0)
                    top_categories.append(f"{feature}={category} ({shift:+.0f})")
            
            drivers_text = ", ".join(top_categories[:3]) if top_categories else "No drivers"
            
            prompt = f"""Generate executive summary in 2-4 sentences.

QUERY: {query}
METRIC: {metric}, CHANGE: {delta:+,.2f} ({delta_pct:+.1f}%)
TOP DRIVERS: {drivers_text}

State what happened with %, identify primary drivers with numbers, mention key segments, be actionable.
Example: "Revenue rose 14% QoQ. Most gain from West (+28%) and Product A (+15%). Key positives were ad efficiency and repeat customers, offset by churn."

Generate executive summary:"""

            response = self.llm_client.chat.completions.create(
                model="gpt-4o-mini",  # OPTIMIZATION: Switched from gpt-4o to use different rate limit pool
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=300
            )
            
            return response.choices[0].message.content.strip()
        except Exception as e:
            self.logger.error(f"Executive summary failed: {e}")
            return f"{delta_metrics.get('metric', 'Metric')} changed by {delta_metrics.get('delta_pct', 0):+.1f}%. See analysis below."
    
    # ========================================================================
    # FINALIZER
    # ========================================================================
    
    def _finalizer_tool(self, state: Dict) -> Dict:
        """
        Finalizer: Prepare final results for UI
        Handles fatal errors gracefully
        """
        self.logger.info("="*80)
        self.logger.info("=== FINALIZER: PREPARING FINAL RESULTS ===")
        self.logger.info("="*80)
        
        try:
            # ========================================================================
            # GUARD CLAUSE: Preserve temporal comparison results if already created
            # ========================================================================
            if state.get("temporal_execution_completed"):
                self.logger.info("✓ Temporal execution completed - preserving existing final_result")
                
                # Temporal execution already created final_result
                if "final_result" in state:
                    # Check for interpretive mode
                    analysis_mode = state.get("analysis_mode", "factual")
                    
                    if analysis_mode == "interpretive":
                        self.logger.info("[7-LAYER] Interpretive mode detected - generating analysis")
                        query = state.get("query", "")
                        seven_layer_result = self._generate_seven_layer_analysis(state, query)
                        
                        if seven_layer_result:
                            state["final_result"]["seven_layer_analysis"] = seven_layer_result
                            state["final_result"]["analysis_type"] = "interpretive_temporal_comparison"
                            self.logger.info("✅ 7-layer analysis added")
                        else:
                            self.logger.warning("[7-LAYER] Generation failed, keeping factual")
                            state["final_result"]["analysis_type"] = "temporal_comparison"
                    else:
                        state["final_result"]["analysis_type"] = "temporal_comparison"
                    
                    # Add messages and errors if not already there
                    if "messages" not in state["final_result"]:
                        state["final_result"]["messages"] = state.get("messages", [])
                    if "errors" not in state["final_result"]:
                        state["final_result"]["errors"] = state.get("errors", []) if state.get("errors") else None
                    
                    self.logger.info("✅ Temporal comparison results finalized")
                    self.logger.info(f"   Analysis type: {state['final_result'].get('analysis_type', 'unknown')}")
                    self.logger.info(f"   Comparison: {state['final_result'].get('comparison_context', 'N/A')}")
                    
                    return state
                else:
                    self.logger.warning("⚠️ temporal_execution_completed=True but no final_result found, proceeding with standard flow")
            
            # ========================================================================
            # GUARD CLAUSE: Preserve outlier detection results if already created
            # ========================================================================
            if state.get("outlier_execution_completed"):
                self.logger.info("✓ Outlier execution completed - preserving existing final_result")
                
                if "final_result" in state:
                    final_result_value = state["final_result"]
                    
                    # If final_result is a string, convert to dict format
                    if isinstance(final_result_value, str):
                        self.logger.info("   Converting string final_result to dict format")
                        state["final_result"] = {
                            "success": True,
                            "query": state.get("query", ""),
                            "response": final_result_value,
                            "analysis_type": "outlier_detection",
                            "messages": state.get("messages", []),
                            "errors": state.get("errors", []) if state.get("errors") else None
                        }
                    else:
                        # Already a dict, just ensure metadata is complete
                        if "messages" not in state["final_result"]:
                            state["final_result"]["messages"] = state.get("messages", [])
                        if "errors" not in state["final_result"]:
                            state["final_result"]["errors"] = state.get("errors", []) if state.get("errors") else None
                    
                    self.logger.info("✅ Outlier detection results finalized")
                    self.logger.info(f"   Analysis type: {state['final_result'].get('analysis_type', 'outlier_detection')}")
                    self.logger.info(f"   Total outliers: {state['final_result'].get('response', '').count('Row Index') if isinstance(state['final_result'].get('response'), str) else 'N/A'}")
                    
                    return state
                else:
                    self.logger.warning("⚠️ outlier_execution_completed=True but no final_result found, proceeding with standard flow")
            
            # ========================================================================
            # GUARD CLAUSE: Preserve temporal anomaly detection results if already created
            # ========================================================================
            if state.get("temporal_anomaly_execution_completed"):
                self.logger.info("✓ Temporal anomaly execution completed - preserving existing final_result")
                
                if "final_result" in state:
                    final_result_value = state["final_result"]
                    
                    # If final_result is a string, convert to dict format
                    if isinstance(final_result_value, str):
                        self.logger.info("   Converting string final_result to dict format")
                        state["final_result"] = {
                            "success": True,
                            "query": state.get("query", ""),
                            "response": final_result_value,
                            "analysis_type": "temporal_anomaly_detection",
                            "messages": state.get("messages", []),
                            "errors": state.get("errors", []) if state.get("errors") else None
                        }
                    else:
                        # Already a dict, just ensure metadata is complete
                        if "messages" not in state["final_result"]:
                            state["final_result"]["messages"] = state.get("messages", [])
                        if "errors" not in state["final_result"]:
                            state["final_result"]["errors"] = state.get("errors", []) if state.get("errors") else None
                    
                    self.logger.info("✅ Temporal anomaly results finalized")
                    self.logger.info(f"   Analysis type: {state['final_result'].get('analysis_type', 'temporal_anomaly_detection')}")
                    self.logger.info(f"   Total anomalies: {state['final_result'].get('response', '').count('**')/2 if isinstance(state['final_result'].get('response'), str) else 'N/A'}")
                    
                    return state
                else:
                    self.logger.warning("⚠️ temporal_anomaly_execution_completed=True but no final_result found, proceeding with standard flow")
            
            # ========================================================================
            # STANDARD FLOW: Create final_result from final_insights (Stage 3 output)
            # ========================================================================
            query = state.get("query", "")
            final_insights = state.get("final_insights", {})
            messages = state.get("messages", [])
            errors = state.get("errors", [])
            fatal_error = state.get("fatal_error", False)
            
            # Check for fatal errors (Stage 1 failure)
            if fatal_error:
                self.logger.error("⛔ Fatal error encountered during analysis")
                error_msg = errors[-1] if errors else "Unknown fatal error"
                final_result = {
                    "success": False,
                    "query": query,
                    "response": f"Analysis failed at an early stage. {error_msg}",
                    "insights": {},
                    "messages": messages,
                    "errors": errors,
                    "fatal_error": True,
                    "success_message": "Analysis failed - unable to proceed"
                }
                state["final_result"] = final_result
                return state
            
            # Compile final result for successful/partial completion
            final_result = {
                "success": len(errors) == 0,
                "query": query,
                
                # Analysis results
                "response": final_insights.get("formatted_text", "Analysis completed."),
                "chart_image": final_insights.get("chart_image"),  # Add chart image
                "insights": final_insights,
                
                # Metadata
                "messages": messages,
                "errors": errors if errors else None,
                
                # Stage info (for debugging)
                "stage1_columns": state.get("stage1_columns"),
                "stage2_plan": state.get("stage2_plan"),
                "retry_attempts": state.get("retry_attempt", 0),
                
                # Success message
                "success_message": "Analysis completed successfully" if len(errors) == 0 else f"Analysis completed with {len(errors)} errors"
            }
            
            state["final_result"] = final_result
            
            self.logger.info("✅ Analysis workflow completed successfully")
            self.logger.info(f"   Retry attempts: {state.get('retry_attempt', 0)}")
            self.logger.info(f"   Total messages: {len(messages)}")
            
            # DEBUG: Log types of all state keys to identify serialization issues
            self.logger.info("="*80)
            self.logger.info("DEBUG: STATE INSPECTION FOR SERIALIZATION")
            self.logger.info("="*80)
            self._debug_inspect_state_types(state)
            
        except Exception as e:
            self.logger.error(f"Finalizer failed: {str(e)}")
            state["final_result"] = {
                "success": False,
                "error": str(e),
                "query": state.get("query", ""),
                "messages": state.get("messages", []),
                "response": f"Analysis failed: {str(e)}"
            }
        
        return state
    
    def _debug_inspect_state_types(self, state: Dict, prefix: str = "", max_depth: int = 3):
        """
        Recursively inspect state to find pandas Series or DataFrame objects
        that would cause msgpack serialization failures
        """
        import pandas as pd
        import numpy as np
        
        if max_depth <= 0:
            return
        
        for key, value in state.items():
            full_key = f"{prefix}.{key}" if prefix else key
            
            # Check the type
            value_type = type(value).__name__
            
            # Flag problematic types
            if isinstance(value, pd.Series):
                self.logger.error(f"🚨 FOUND Series at: {full_key}")
                self.logger.error(f"   Type: {value_type}")
                self.logger.error(f"   Shape: {value.shape}")
                self.logger.error(f"   First few values: {value.head().tolist() if len(value) > 0 else 'empty'}")
            elif isinstance(value, pd.DataFrame):
                self.logger.error(f"🚨 FOUND DataFrame at: {full_key}")
                self.logger.error(f"   Type: {value_type}")
                self.logger.error(f"   Shape: {value.shape}")
            elif isinstance(value, np.ndarray):
                self.logger.warning(f"⚠️  FOUND ndarray at: {full_key}")
                self.logger.warning(f"   Shape: {value.shape}")
            elif isinstance(value, dict):
                # Recurse into dicts
                self._debug_inspect_state_types(value, prefix=full_key, max_depth=max_depth-1)
            elif isinstance(value, (list, tuple)):
                # Check list/tuple elements
                for idx, item in enumerate(value):
                    if idx > 10:  # Limit inspection to first 10 items
                        break
                    if isinstance(item, (pd.Series, pd.DataFrame, np.ndarray)):
                        self.logger.error(f"🚨 FOUND {type(item).__name__} at: {full_key}[{idx}]")
                    elif isinstance(item, dict):
                        self._debug_inspect_state_types(item, prefix=f"{full_key}[{idx}]", max_depth=max_depth-1)


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

# Global instance (will be initialized by app.py)
_shap_analysis_instance = None

def shap_analysis(llm_client, smart_agg_decider=None, causal_cache_path="causal_analysis_cache.json"):
    """
    Get or create singleton instance of AgenticShapAnalysisV6
    """
    global _shap_analysis_instance
    
    if _shap_analysis_instance is None:
        _shap_analysis_instance = AgenticShapAnalysisV6(
            llm_client=llm_client,
            smart_agg_decider=smart_agg_decider,
            causal_cache_path=causal_cache_path
        )
    
    return _shap_analysis_instance