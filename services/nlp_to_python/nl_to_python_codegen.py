"""
Natural Language to Python Code Generator - Code Generation Module
Contains all polars code generators for different operations.

This module contains:
- Base operation code generator class
- Individual code generators for each operation type
- Code generator registry for operation routing
- Helper functions for code generation utilities

FILE VERSION: 2024-12-03-V4
"""

import sys
print("="*80, file=sys.stderr)
print("[CODEGEN_MODULE_V4] nl_to_python_codegen.py LOADED - FILE VERSION 2024-12-03-V4", file=sys.stderr)
print("="*80, file=sys.stderr)

import re
from typing import Dict, List, Any, Optional
import polars as pl
import numpy as np
from datetime import datetime, timedelta

# Import scipy stats if available
try:
    from scipy import stats
    SCIPY_AVAILABLE = True
except Exception:
    stats = None
    SCIPY_AVAILABLE = False

# Import logging for debugging
import logging


# ============================================================================
# BASE CODE GENERATOR
# ============================================================================

class OperationCodeGenerator:
    """Base class for operation code generators"""
    
    @staticmethod
    def generate(params: Dict[str, Any]) -> str:
        """Generate polars code for this operation"""
        raise NotImplementedError


# ============================================================================
# 🆕 COLUMN DESCRIPTION CODE GENERATOR
# ============================================================================

class ColumnDescriptionCodeGen(OperationCodeGenerator):
    """
    Generate code to lookup column descriptions from tableau metadata
    Simple metadata assembly instead of complex pandas transformations
    """
    
    @staticmethod
    def generate(params: Dict[str, Any]) -> str:
        """
        Generate code to lookup column descriptions and basic stats
        
        Args:
            params: Should contain:
                - describe_columns: List[str] - columns to describe
                - workbook_name: str - workbook name for metadata lookup
                - detail_level: str - "basic", "detailed", "statistical"
                - include_samples: bool - include sample values
        """
        describe_columns = params.get("describe_columns", [])
        workbook_name = params.get("workbook_name", "FRODashboard_final")  # Default from your tableau_descriptions
        detail_level = params.get("detail_level", "detailed")
        include_samples = params.get("include_samples", True)
        
        if not describe_columns:
            return """
# ERROR: No columns specified for description
result = [{"error": "No columns specified for description"}]
"""
        
        # Build the metadata lookup code
        code = f"""# Column Description Lookup
import json
import os
from pathlib import Path

# Load business descriptions from tableau metadata
try:
    tableau_desc_path = Path('tableau_descriptions/{workbook_name}/field_descriptions_{workbook_name}.json')
    if tableau_desc_path.exists():
        with open(tableau_desc_path, 'r', encoding='utf-8') as f:
            business_metadata = json.load(f)
    else:
        business_metadata = {{'field_descriptions': []}}
        print(f"Warning: Metadata file not found at {{tableau_desc_path}}")
except Exception as e:
    business_metadata = {{'field_descriptions': []}}
    print(f"Warning: Could not load metadata: {{e}}")

# Extract descriptions for requested columns
columns_to_describe = {describe_columns}
descriptions = []

for column in columns_to_describe:
    # Find business description
    business_desc = None
    data_type_info = None
    
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
    
    # Get basic stats from DataFrame if column exists
    column_stats = {{}}
    if column in df.columns:
        try:
            column_stats = {{
                'data_type': str(df[column].dtype),
                'total_rows': len(df),
                'unique_count': df[column].n_unique(),
                'null_count': df[column].null_count(),
                'null_percentage': round((df[column].null_count() / len(df)) * 100, 1) if len(df) > 0 else 0
            }}
            
            # Add sample values if requested
            {'sample_values = df[column].drop_nulls().unique().limit(5).to_list()' if include_samples else 'sample_values = []'}
            column_stats['sample_values'] = sample_values
            
        except Exception as e:
            column_stats = {{'error': f'Could not analyze column: {{str(e)}}'}}
    else:
        column_stats = {{'error': f'Column "{{column}}" not found in dataframe'}}
    
    # Build description object
    description = {{
        'column_name': column,
        'business_description': business_desc or f'No description available for {{column}}',
        'statistical_profile': column_stats,
        'detail_level': '{detail_level}'
    }}
    
    descriptions.append(description)

# Return the descriptions
result = descriptions
"""
        
        return code


# ============================================================================
# INDIVIDUAL CODE GENERATORS
# ============================================================================

# 🆕 UNIFIED PERIOD COMPARISON CODE GENERATOR
class PeriodComparisonCodeGen(OperationCodeGenerator):
    """
    Universal period comparison generator
    Replaces quarter_comparison, month_on_month, year_on_year
    Supports: hour, day, week, business_day, month, quarter, year, season
    """
    
    @staticmethod
    def generate(params: Dict[str, Any]) -> str:
        import logging
        logger = logging.getLogger(__name__)
        logger.info("="*100)
        logger.info(f"[CODEGEN_V4_ENTRY] PeriodComparisonCodeGen.generate() - FILE VERSION 2024-12-03-V4")
        logger.info("="*100)
        
        comparison_type = params.get("comparison_type", "lag")
        period_granularity = params.get("period_granularity", "month")
        shift_periods = params.get("shift_periods")  # Can be None for specific period comparisons
        compare_periods = params.get("compare_periods")  # Specific periods
        output_format = params.get("output_format", "percentage")
        
        logger.info(f"[CODEGEN_ENTRY] compare_periods={compare_periods}, group_by_columns={params.get('group_by_columns')}")
        
        # 🔥 NEW: Get actual column to use (might be helper column)
        metric_column = params.get("metric_column") or params.get("values", "case_id")
        has_helper_column = params.get("has_helper_column", False)
        
        group_by = params.get("group_by_columns", [])
        date_column = params.get("date_column")
        aggfunc = params.get("aggfunc", "count")
        temporal_filters = params.get("temporal_filters", [])
        
        # 🔥 NEW: Adjust aggfunc if using helper column
        if has_helper_column and aggfunc == 'count':
            aggfunc = 'sum'  # Sum the helper column instead of counting
        
        # SPECIFIC PERIOD COMPARISON (Q3 vs Q4, Jan vs Feb)
        logger.info(f"[CODEGEN_BRANCH] Checking: compare_periods={compare_periods}, len={len(compare_periods) if compare_periods else 0}")
        if compare_periods and len(compare_periods) >= 2:
            logger.info(f"[CODEGEN_BRANCH] ✅ Taking SPECIFIC PERIODS path (calling _generate_specific_periods)")
            return PeriodComparisonCodeGen._generate_specific_periods(
                compare_periods, metric_column, group_by, date_column, aggfunc, has_helper_column, temporal_filters
            )
        else:
            logger.info(f"[CODEGEN_BRANCH] ❌ Taking SHIFT-BASED path (NOT specific periods) - this is likely WRONG for Q1 vs Q2!")
        
        # For shift-based comparisons, default shift_periods to 1 if not provided
        if shift_periods is None:
            shift_periods = 1
        
        # DYNAMIC SHIFT-BASED COMPARISON
        if not date_column:
            return "# ERROR: Period comparison requires date_column\nresult = pl.DataFrame({'error': ['No date column specified']})\n"
        
        shift_expr = PeriodComparisonCodeGen._get_shift_expression(
            period_granularity, shift_periods, date_column
        )
        
        code = f"""# Period Comparison: {comparison_type.upper()} by {shift_periods} {period_granularity}
"""
        
        if group_by:
            # Grouped comparison
            code += f"""
# Convert date column to datetime if it's a string
if df['{date_column}'].dtype in [pl.String, pl.Utf8]:
    df = df.with_columns(pl.col('{date_column}').str.to_datetime())

# Group by period and additional dimensions
"""
            if period_granularity == "season":
                code += PeriodComparisonCodeGen._add_season_column(date_column)
                group_cols = "['season'] + " + str(group_by) if group_by else "['season']"
            elif period_granularity == "business_day":
                code += f"df = df.with_columns(pl.col('{date_column}').dt.truncate('1d').alias('business_day'))\n"
                code += f"df = df.filter(pl.col('{date_column}').dt.weekday() < 5)  # Keep only Mon-Fri\n"
                group_cols = "['business_day'] + " + str(group_by) if group_by else "['business_day']"
            else:
                freq = PeriodComparisonCodeGen._get_polars_freq(period_granularity)
                code += f"df = df.with_columns(pl.col('{date_column}').dt.truncate('{freq}').alias('period'))\n"
                group_cols = "['period'] + " + str(group_by) if group_by else "['period']"
            
            # Check if metric_column is in group_by to avoid collision
            if metric_column in group_by:
                code += f"""
# Note: metric_column '{metric_column}' is in group_by, using count to count rows
result = df.group_by({group_cols}).agg(pl.len().alias('{aggfunc}'))
result = result.rename({{list({group_cols})[0]: 'current_period'}})
"""
            else:
                agg_expr = PeriodComparisonCodeGen._get_polars_agg(aggfunc, metric_column)
                code += f"""
result = df.group_by({group_cols}).agg({agg_expr}.alias('{aggfunc}'))
result = result.rename({{list({group_cols})[0]: 'current_period'}})
"""
            
            code += f"""
# Sort to ensure proper ordering
result = result.sort('current_period')
"""
        else:
            # Non-grouped comparison
            code += f"""
# Convert date column to datetime if it's a string
if df['{date_column}'].dtype in [pl.String, pl.Utf8]:
    df = df.with_columns(pl.col('{date_column}').str.to_datetime())
"""
            if period_granularity == "season":
                code += PeriodComparisonCodeGen._add_season_column(date_column)
                agg_expr = PeriodComparisonCodeGen._get_polars_agg(aggfunc, metric_column)
                code += f"result = df.group_by('season').agg({agg_expr}.alias('{aggfunc}'))\n"
                code += f"result = result.rename({{'season': 'current_period'}})\n"
            elif period_granularity == "business_day":
                code += f"df = df.with_columns(pl.col('{date_column}').dt.truncate('1d').alias('business_day'))\n"
                code += f"df = df.filter(pl.col('{date_column}').dt.weekday() < 5)  # Keep only Mon-Fri\n"
                agg_expr = PeriodComparisonCodeGen._get_polars_agg(aggfunc, metric_column)
                code += f"result = df.group_by('business_day').agg({agg_expr}.alias('{aggfunc}'))\n"
                code += f"result = result.rename({{'business_day': 'current_period'}})\n"
            else:
                freq = PeriodComparisonCodeGen._get_polars_freq(period_granularity)
                code += f"df = df.with_columns(pl.col('{date_column}').dt.truncate('{freq}').alias('period'))\n"
                agg_expr = PeriodComparisonCodeGen._get_polars_agg(aggfunc, metric_column)
                code += f"result = df.group_by('period').agg({agg_expr}.alias('{aggfunc}'))\n"
                code += f"result = result.rename({{'period': 'current_period'}})\n"
            
            code += "result = result.sort('current_period')\n"
        
        # Apply comparison logic
        if comparison_type == "lag":
            code += f"""
# Lag: Show current and previous values
result = result.with_columns(pl.col('{aggfunc}').shift({shift_periods}).alias('previous_{period_granularity}'))
"""
        
        elif comparison_type == "lead":
            code += f"""
# Lead: Show current and next values
result = result.with_columns(pl.col('{aggfunc}').shift(-{shift_periods}).alias('next_{period_granularity}'))
"""
        
        elif comparison_type == "temporal_change":
            code += f"""
# Temporal Change: Calculate change from previous period
result = result.with_columns(pl.col('{aggfunc}').shift({shift_periods}).alias('previous_{period_granularity}'))
"""
            if output_format in ["percentage", "both"]:
                code += f"""result = result.with_columns(((pl.col('{aggfunc}') - pl.col('previous_{period_granularity}')) / pl.col('previous_{period_granularity}') * 100).round(2).alias('percentage_change'))
"""
            if output_format in ["absolute", "both"]:
                code += f"""result = result.with_columns((pl.col('{aggfunc}') - pl.col('previous_{period_granularity}')).alias('absolute_change'))
"""
        
        elif comparison_type == "delta":
            code += f"""
# Delta: Absolute difference from previous period
result = result.with_columns(pl.col('{aggfunc}').diff({shift_periods}).alias('{period_granularity}_change'))
"""
        
        elif comparison_type == "ratio":
            code += f"""
# Ratio: Current / Previous
result = result.with_columns(pl.col('{aggfunc}').shift({shift_periods}).alias('previous_{period_granularity}'))
result = result.with_columns((pl.col('{aggfunc}') / pl.col('previous_{period_granularity}')).round(2).alias('ratio'))
"""
        
        return code
    
    @staticmethod
    def _generate_specific_periods(compare_periods, metric_column, group_by, date_column, aggfunc, has_helper_column=False, temporal_filters=None):
        """
        Generate code for comparing specific periods (e.g., Q1 vs Q2 2025, Feb vs Mar 2025)
        
        Uses pre-built filter expressions from temporal_filters when available,
        ensuring consistent column naming across aggregation and percentage calculations.
        """
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"[CODEGEN] _generate_specific_periods called")
        logger.info(f"[CODEGEN] compare_periods={compare_periods}, group_by={group_by}, aggfunc={aggfunc}")
        
        if temporal_filters is None:
            temporal_filters = []
        
        # Normalize period labels for consistent column naming
        period1_label = PeriodComparisonCodeGen._normalize_period_label(compare_periods[0])
        period2_label = PeriodComparisonCodeGen._normalize_period_label(compare_periods[1]) if len(compare_periods) > 1 else None
        
        logger.info(f"[CODEGEN] period1_label={period1_label}, period2_label={period2_label}")
        
        code = f"""# Specific Period Comparison: {period1_label} vs {period2_label}
# Convert date column to datetime if it's a string
if df['{date_column}'].dtype in [pl.String, pl.Utf8]:
    df = df.with_columns(pl.col('{date_column}').str.to_datetime())
"""
        
        logger.info(f"[CODEGEN_PATH] temporal_filters length: {len(temporal_filters)}")
        logger.info(f"[CODEGEN_PATH] temporal_filters: {temporal_filters}")
        
        # Use pre-built filter expressions if available, otherwise fallback to manual parsing
        if len(temporal_filters) >= 2:
            # Use pre-built filter expressions (preferred - more reliable)
            filter_expr1 = temporal_filters[0].get('filter_expression', '')
            filter_expr2 = temporal_filters[1].get('filter_expression', '')
            
            if filter_expr1 and filter_expr2:
                # Extract just the condition part (remove "df.filter(" and trailing ")")
                filter_expr1 = PeriodComparisonCodeGen._extract_filter_condition(filter_expr1)
                filter_expr2 = PeriodComparisonCodeGen._extract_filter_condition(filter_expr2)
                
                code += f"""
# Filter for the two periods using pre-built expressions
df_p1 = df.filter({filter_expr1})
df_p2 = df.filter({filter_expr2})
"""
            else:
                # Fallback to manual parsing
                code += PeriodComparisonCodeGen._generate_period_filters_fallback(
                    compare_periods, date_column
                )
        else:
            # Fallback to manual parsing
            code += PeriodComparisonCodeGen._generate_period_filters_fallback(
                compare_periods, date_column
            )
        
        # Aggregate for each period
        agg_expr = PeriodComparisonCodeGen._get_polars_agg(aggfunc, metric_column)
        
        logger.info(f"[CODEGEN] group_by is {'EMPTY' if not group_by else f'NOT EMPTY: {group_by}'}")
        
        if group_by:
            # Grouped comparison - result will have dimensions + two period columns
            logger.info(f"[CODEGEN] Taking GROUPED branch - will create columns '{period1_label}' and '{period2_label}'")
            code += f"""
# Aggregate by groups
agg_p1 = df_p1.group_by({group_by}).agg({agg_expr}.alias('{period1_label}'))
agg_p2 = df_p2.group_by({group_by}).agg({agg_expr}.alias('{period2_label}'))

# Merge results
result = agg_p1.join(agg_p2, on={group_by}, how='outer')

# Calculate percentage change
result = result.with_columns(
    ((pl.col('{period2_label}') - pl.col('{period1_label}')) / pl.col('{period1_label}') * 100).round(2).alias('percentage_change')
)
"""
        else:
            # Non-grouped comparison - result will have two period columns in separate rows
            logger.info(f"[CODEGEN] Taking NON-GROUPED branch - will create 'period' and 'value' columns")
            code += f"""
# Aggregate totals
val_p1 = df_p1.select({agg_expr}).item()
val_p2 = df_p2.select({agg_expr}).item()

# Create result with consistent column naming
result = pl.DataFrame({{
    'period': ['{period1_label}', '{period2_label}'],
    'value': [val_p1, val_p2]
}})

# Calculate percentage change (from period1 to period2)
pct_change = ((val_p2 - val_p1) / val_p1 * 100) if val_p1 != 0 else 0.0
result = result.with_columns(pl.lit(pct_change).round(2).alias('percentage_change'))
"""
        
        logger.info(f"[CODEGEN] Generated code length: {len(code)} chars")
        logger.info(f"[CODEGEN_RESULT] Returning from _generate_specific_periods()")
        logger.info(f"[CODEGEN_RESULT] Code contains 'value' column: {'value' in code}")
        logger.info(f"[CODEGEN_RESULT] Code contains '{aggfunc}' column: {'{aggfunc}' in code}")
        logger.info(f"[CODEGEN_RESULT] Last 600 chars of code:\n{code[-600:]}")
        
        return code
    
    @staticmethod
    def _normalize_period_label(period_str):
        """
        Normalize period labels for consistent naming.
        Examples: 'Q1 2025' -> 'Q1 2025', 'february 2025' -> 'February 2025'
        """
        import re
        
        # Capitalize first letter of month names
        month_names = {
            'january': 'January', 'february': 'February', 'march': 'March',
            'april': 'April', 'may': 'May', 'june': 'June',
            'july': 'July', 'august': 'August', 'september': 'September',
            'october': 'October', 'november': 'November', 'december': 'December'
        }
        
        period_lower = period_str.lower()
        for month_lower, month_proper in month_names.items():
            if month_lower in period_lower:
                return period_str.replace(month_lower, month_proper).replace(month_lower.capitalize(), month_proper)
        
        # Handle quarter abbreviations (Q1, q1 -> Q1)
        period_str = re.sub(r'q(\d)', r'Q\1', period_str, flags=re.IGNORECASE)
        
        return period_str
    
    @staticmethod
    def _extract_filter_condition(filter_expression):
        """
        Extract or validate the filter condition.
        The pre-built expressions are already just conditions (no df.filter wrapper),
        so this mainly validates and returns them as-is.
        """
        # Pre-built expressions from _build_temporal_filter_expression are already conditions
        # They look like: "((df['date'].dt.month() == 2) & (df['date'].dt.year() == 2025))"
        # We just need to ensure they're ready to use in df.filter()
        return filter_expression
    
    @staticmethod
    def _generate_period_filters_fallback(compare_periods, date_column):
        """
        Generate period filters by parsing period strings (fallback when pre-built expressions unavailable).
        """
        period1 = compare_periods[0]
        period2 = compare_periods[1] if len(compare_periods) > 1 else compare_periods[0]
        
        # Detect granularity
        if "Q" in period1.upper():
            granularity = "quarter"
        elif any(month.lower() in period1.lower() for month in ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]):
            granularity = "month"
        else:
            granularity = "year"
        
        code = ""
        
        if granularity == "quarter":
            # Parse quarters and years
            q1, y1 = PeriodComparisonCodeGen._parse_quarter(period1)
            q2, y2 = PeriodComparisonCodeGen._parse_quarter(period2)
            
            code += f"df = df.with_columns(pl.col('{date_column}').dt.quarter().alias('quarter'))\n"
            code += f"df = df.with_columns(pl.col('{date_column}').dt.year().alias('year'))\n"
            code += f"""
# Filter for the two quarters
df_p1 = df.filter((pl.col('quarter') == {q1}) & (pl.col('year') == {y1}))
df_p2 = df.filter((pl.col('quarter') == {q2}) & (pl.col('year') == {y2}))
"""
        
        elif granularity == "month":
            # Parse months and years
            m1, y1 = PeriodComparisonCodeGen._parse_month(period1)
            m2, y2 = PeriodComparisonCodeGen._parse_month(period2)
            
            code += f"df = df.with_columns(pl.col('{date_column}').dt.month().alias('month'))\n"
            code += f"df = df.with_columns(pl.col('{date_column}').dt.year().alias('year'))\n"
            code += f"""
# Filter for the two months
df_p1 = df.filter((pl.col('month') == {m1}) & (pl.col('year') == {y1}))
df_p2 = df.filter((pl.col('month') == {m2}) & (pl.col('year') == {y2}))
"""
        
        else:  # year
            y1 = PeriodComparisonCodeGen._parse_year(period1)
            y2 = PeriodComparisonCodeGen._parse_year(period2)
            
            code += f"df = df.with_columns(pl.col('{date_column}').dt.year().alias('year'))\n"
            code += f"""
# Filter for the two years
df_p1 = df.filter(pl.col('year') == {y1})
df_p2 = df.filter(pl.col('year') == {y2})
"""
        
        return code
    
    @staticmethod
    def _parse_quarter(period_str):
        """Parse quarter string like 'Q1 2025', 'first quarter', '1st quarter' into (quarter, year)"""
        period_lower = period_str.lower()
        
        # Handle written quarter names
        quarter_names = {
            'first': 1, '1st': 1,
            'second': 2, '2nd': 2,
            'third': 3, '3rd': 3,
            'fourth': 4, '4th': 4
        }
        
        quarter = 1  # default
        
        # Check for written quarter names
        for name, num in quarter_names.items():
            if name in period_lower and 'quarter' in period_lower:
                quarter = num
                break
        
        # Check for Q1, Q2, etc. format
        match = re.search(r'Q(\d)', period_str, re.IGNORECASE)
        if match:
            quarter = int(match.group(1))
        
        year_match = re.search(r'(\d{4})', period_str)
        year = int(year_match.group(1)) if year_match else datetime.now().year
        
        return quarter, year
    
    @staticmethod
    def _parse_month(period_str):
        """Parse month string like 'Jan 2025', 'February 2025', 'march 2025' into (month, year)"""
        # Full month names and abbreviations
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
        
        period_lower = period_str.lower()
        month = 1
        
        # Check full names first (longer matches have priority)
        for month_name in sorted(month_map.keys(), key=len, reverse=True):
            if month_name in period_lower:
                month = month_map[month_name]
                break
        
        year_match = re.search(r'(\d{4})', period_str)
        year = int(year_match.group(1)) if year_match else datetime.now().year
        
        return month, year
    
    @staticmethod
    def _parse_year(period_str):
        """Parse year string like '2025' into year"""
        year_match = re.search(r'(\d{4})', period_str)
        return int(year_match.group(1)) if year_match else datetime.now().year
    
    @staticmethod
    def _get_shift_expression(period_granularity, shift_periods, date_column):
        """Get the shift expression for different period granularities"""
        # This is used for documentation purposes in the generated code
        return f"{shift_periods} {period_granularity}"
    
    @staticmethod
    def _get_polars_freq(period_granularity):
        """Convert period granularity to polars frequency string"""
        freq_map = {
            "hour": "1h",
            "day": "1d",
            "week": "1w",
            "month": "1mo",
            "quarter": "1q",
            "year": "1y"
        }
        return freq_map.get(period_granularity, "1mo")
    
    @staticmethod
    def _get_polars_agg(aggfunc, column):
        """Generate polars aggregation expression"""
        agg_map = {
            'count': f"pl.len()",
            'sum': f"pl.col('{column}').sum()",
            'mean': f"pl.col('{column}').mean()",
            'avg': f"pl.col('{column}').mean()",
            'median': f"pl.col('{column}').median()",
            'min': f"pl.col('{column}').min()",
            'max': f"pl.col('{column}').max()",
            'std': f"pl.col('{column}').std()",
            'var': f"pl.col('{column}').var()"
        }
        return agg_map.get(aggfunc, f"pl.col('{column}').{aggfunc}()")
    
    @staticmethod
    def _add_season_column(date_column):
        """Generate code to add season column"""
        return f"""df = df.with_columns(
    pl.col('{date_column}').dt.month().map_elements(
        lambda m: 'Spring' if m in [3,4,5] else 'Summer' if m in [6,7,8] else 'Fall' if m in [9,10,11] else 'Winter',
        return_dtype=pl.Utf8
    ).alias('season')
)
"""


class TimeSeriesCodeGen(OperationCodeGenerator):
    """Generate code for time series analysis"""
    
    @staticmethod
    def generate(params: Dict[str, Any]) -> str:
        analysis_type = params.get("analysis_type", "trend")
        date_column = params.get("date_column")
        metric_column = params.get("metric_column", "case_id")
        has_helper_column = params.get("has_helper_column", False)
        group_by = params.get("group_by_columns", [])
        aggfunc = params.get("aggfunc", "count")
        
        # Adjust aggfunc if using helper column
        if has_helper_column and aggfunc == 'count':
            aggfunc = 'sum'
        
        if not date_column:
            return "# ERROR: Time series analysis requires date_column\nresult = pl.DataFrame({'error': ['No date column specified']})\n"
        
        code = f"""# Time Series Analysis: {analysis_type}
# Convert date column to datetime if it's a string
if df['{date_column}'].dtype in [pl.String, pl.Utf8]:
    df = df.with_columns(pl.col('{date_column}').str.to_datetime())

"""
        
        # Add type conversion for numeric operations if needed
        numeric_ops = ['sum', 'mean', 'avg', 'median', 'std', 'var', 'min', 'max']
        if aggfunc in numeric_ops and metric_column and metric_column != 'case_id':
            code += f"""# Convert {metric_column} to numeric type if it's a string
if df['{metric_column}'].dtype == pl.String or df['{metric_column}'].dtype == pl.Utf8:
    df = df.with_columns(
        pl.col('{metric_column}').cast(pl.Float64, strict=False).alias('{metric_column}')
    )

"""
        
        if analysis_type == "trend":
            # Basic trend analysis with moving average
            window = params.get("window", 7)
            
            if group_by:
                agg_expr = TimeSeriesCodeGen._get_polars_agg(aggfunc, metric_column)
                code += f"""
# Group by date and additional dimensions
result = df.group_by(['{date_column}'] + {group_by}).agg({agg_expr}.alias('value'))
result = result.sort('{date_column}')

# Calculate moving average within groups
result = result.with_columns(
    pl.col('value').rolling_mean(window_size={window}).over({group_by}).alias('moving_avg')
)
"""
            else:
                agg_expr = TimeSeriesCodeGen._get_polars_agg(aggfunc, metric_column)
                code += f"""
# Group by date
result = df.group_by('{date_column}').agg({agg_expr}.alias('value'))
result = result.sort('{date_column}')

# Calculate moving average
result = result.with_columns(
    pl.col('value').rolling_mean(window_size={window}).alias('moving_avg')
)
"""
        
        elif analysis_type == "seasonality":
            # Detect seasonal patterns
            period = params.get("period", "month")
            
            code += f"""
# Extract time period
df = df.with_columns(pl.col('{date_column}').dt.{period}().alias('period'))

# Calculate average by period
"""
            agg_expr = TimeSeriesCodeGen._get_polars_agg(aggfunc, metric_column)
            
            if group_by:
                code += f"""result = df.group_by(['period'] + {group_by}).agg({agg_expr}.alias('avg_value'))
result = result.sort('period')
"""
            else:
                code += f"""result = df.group_by('period').agg({agg_expr}.alias('avg_value'))
result = result.sort('period')
"""
        
        elif analysis_type == "rolling_stats":
            # Rolling statistics
            window = params.get("window", 7)
            stats = params.get("stats", ["mean", "std"])
            
            agg_expr = TimeSeriesCodeGen._get_polars_agg(aggfunc, metric_column)
            code += f"""
# Group by date
result = df.group_by('{date_column}').agg({agg_expr}.alias('value'))
result = result.sort('{date_column}')

# Calculate rolling statistics
"""
            
            if "mean" in stats:
                code += f"result = result.with_columns(pl.col('value').rolling_mean(window_size={window}).alias('rolling_mean'))\n"
            if "std" in stats:
                code += f"result = result.with_columns(pl.col('value').rolling_std(window_size={window}).alias('rolling_std'))\n"
            if "min" in stats:
                code += f"result = result.with_columns(pl.col('value').rolling_min(window_size={window}).alias('rolling_min'))\n"
            if "max" in stats:
                code += f"result = result.with_columns(pl.col('value').rolling_max(window_size={window}).alias('rolling_max'))\n"
        
        elif analysis_type == "cumulative":
            # Cumulative sum
            agg_expr = TimeSeriesCodeGen._get_polars_agg(aggfunc, metric_column)
            
            if group_by:
                code += f"""
# Group by date and additional dimensions
result = df.group_by(['{date_column}'] + {group_by}).agg({agg_expr}.alias('value'))
result = result.sort('{date_column}')

# Calculate cumulative sum within groups
result = result.with_columns(
    pl.col('value').cum_sum().over({group_by}).alias('cumulative')
)
"""
            else:
                code += f"""
# Group by date
result = df.group_by('{date_column}').agg({agg_expr}.alias('value'))
result = result.sort('{date_column}')

# Calculate cumulative sum
result = result.with_columns(pl.col('value').cum_sum().alias('cumulative'))
"""
        
        return code
    
    @staticmethod
    def _get_polars_agg(aggfunc, column):
        """Generate polars aggregation expression"""
        agg_map = {
            'count': f"pl.len()",
            'sum': f"pl.col('{column}').sum()",
            'mean': f"pl.col('{column}').mean()",
            'avg': f"pl.col('{column}').mean()",
            'median': f"pl.col('{column}').median()",
            'min': f"pl.col('{column}').min()",
            'max': f"pl.col('{column}').max()",
            'std': f"pl.col('{column}').std()",
            'var': f"pl.col('{column}').var()"
        }
        return agg_map.get(aggfunc, f"pl.col('{column}').{aggfunc}()")


class ComparisonCodeGen(OperationCodeGenerator):
    """Generate code for general comparisons"""
    
    @staticmethod
    def generate(params: Dict[str, Any]) -> str:
        dimension = params["dimension"]
        metric_column = params.get("metric_column", "case_id")
        has_helper_column = params.get("has_helper_column", False)
        aggfunc = params.get("aggfunc", "count")
        filter_top_n = params.get("filter_top_n")
        
        # Adjust aggfunc if using helper column
        if has_helper_column and aggfunc == 'count':
            aggfunc = 'sum'
        
        agg_expr = ComparisonCodeGen._get_polars_agg(aggfunc, metric_column)
        
        code = f"""# General Comparison by {dimension}
result = df.group_by('{dimension}').agg({agg_expr}.alias('{aggfunc}'))
result = result.sort('{aggfunc}', descending=True)
"""
        
        if filter_top_n:
            code += f"result = result.head({filter_top_n})\n"
        
        return code
    
    @staticmethod
    def _get_polars_agg(aggfunc, column):
        """Generate polars aggregation expression"""
        agg_map = {
            'count': f"pl.len()",
            'sum': f"pl.col('{column}').sum()",
            'mean': f"pl.col('{column}').mean()",
            'avg': f"pl.col('{column}').mean()",
            'median': f"pl.col('{column}').median()",
            'min': f"pl.col('{column}').min()",
            'max': f"pl.col('{column}').max()",
            'std': f"pl.col('{column}').std()",
            'var': f"pl.col('{column}').var()"
        }
        return agg_map.get(aggfunc, f"pl.col('{column}').{aggfunc}()")


class PivotCodeGen(OperationCodeGenerator):
    """Generate code for pivot tables"""
    
    @staticmethod
    def generate(params: Dict[str, Any]) -> str:
        index = params["index"]
        columns = params["columns"]
        values = params.get("values", "case_id")
        aggfunc = params.get("aggfunc", "count")
        has_helper_column = params.get("has_helper_column", False)
        
        # Adjust aggfunc if using helper column
        if has_helper_column and aggfunc == 'count':
            aggfunc = 'sum'
        
        # Polars doesn't have a direct pivot_table, need to use group_by + pivot
        agg_expr = PivotCodeGen._get_polars_agg(aggfunc, values)
        
        code = f"""# Pivot Table
# Group by index and columns first
grouped = df.group_by(['{index}', '{columns}']).agg({agg_expr}.alias('value'))

# Pivot to wide format
result = grouped.pivot(index='{index}', columns='{columns}', values='value')
"""
        
        return code
    
    @staticmethod
    def _get_polars_agg(aggfunc, column):
        """Generate polars aggregation expression"""
        agg_map = {
            'count': f"pl.len()",
            'sum': f"pl.col('{column}').sum()",
            'mean': f"pl.col('{column}').mean()",
            'avg': f"pl.col('{column}').mean()",
            'median': f"pl.col('{column}').median()",
            'min': f"pl.col('{column}').min()",
            'max': f"pl.col('{column}').max()",
            'std': f"pl.col('{column}').std()",
            'var': f"pl.col('{column}').var()"
        }
        return agg_map.get(aggfunc, f"pl.col('{column}').{aggfunc}()")


class CompositionPercentageCodeGen(OperationCodeGenerator):
    """Generate code for composition/percentage calculations"""
    
    @staticmethod
    def generate(params: Dict[str, Any]) -> str:
        group_by_columns = params.get("group_by_columns", [])
        temporal_filters = params.get("temporal_filters", [])
        filters = params.get("filters", [])
        
        # Check if helper column is being used
        has_helper_column = params.get("has_helper_column", False)
        
        # Case 1: No grouping - simple filtered percentage
        if not group_by_columns:
            return """# Percentage Calculation (filtered vs total)
filtered_count = len(df)
percentage = (filtered_count / original_total * 100) if original_total > 0 else 0
result = pl.DataFrame({
    'numerator': [filtered_count],
    'denominator': [original_total],
    'percentage': [round(percentage, 2)]
})
"""
        
        # Case 2: Composition breakdown
        group_col = group_by_columns[0] if isinstance(group_by_columns, list) else group_by_columns
        
        code = f"# Composition Percentage Breakdown by {group_col}\n"
        
        # BUILD FILTER CONDITIONS
        filter_code_parts = []
        
        # Apply categorical filters
        # CRITICAL: Skip filters on the grouping column to avoid 100% issue
        for filter_spec in filters:
            column = filter_spec.get('column')
            value = filter_spec.get('value')
            operator = filter_spec.get('operator', '==')
            
            # Skip filter if it's on the same column we're grouping by
            if column == group_col:
                continue
            
            if isinstance(value, list):
                # Handle list of values (e.g., ['USA', 'Canada', 'UK'])
                filter_code_parts.append(f"pl.col('{column}').is_in({value})")
            elif isinstance(value, str):
                if operator == '==':
                    filter_code_parts.append(f"pl.col('{column}') == '{value}'")
                elif operator == '!=':
                    filter_code_parts.append(f"pl.col('{column}') != '{value}'")
                else:
                    filter_code_parts.append(f"pl.col('{column}') {operator} '{value}'")
            else:
                filter_code_parts.append(f"pl.col('{column}') {operator} {value}")
        
        # Use pre-built filter expressions from temporal_filters
        for temp_filter in temporal_filters:
            # Check if pre-built filter_expression exists
            if 'filter_expression' in temp_filter and temp_filter['filter_expression']:
                # Use the pre-built expression directly
                filter_code_parts.append(temp_filter['filter_expression'])
        
        # APPLY COMBINED FILTERS
        if filter_code_parts:
            filter_expression = ", ".join(filter_code_parts)
            code += f"\n# Apply filters\ndf_filtered = df.filter({filter_expression})\n"
        else:
            code += "\ndf_filtered = df.clone()\n"
        
        # Group by and calculate composition percentages
        # Using len() to count rows (safe for all column types)
        code += f"""
# Group by {group_col} and calculate composition percentages
grouped = df_filtered.group_by('{group_col}').agg(pl.len().alias('count'))
total = grouped.select(pl.col('count').sum()).item()
result = grouped.with_columns(
    (pl.col('count') / total * 100).round(2).alias('percentage')
)
result = result.sort('percentage', descending=True)
"""
        
        return code
    
    @staticmethod
    def _get_polars_agg(aggfunc, column):
        """Generate polars aggregation expression"""
        agg_map = {
            'count': f"pl.len()",
            'sum': f"pl.col('{column}').sum()",
            'mean': f"pl.col('{column}').mean()",
            'avg': f"pl.col('{column}').mean()",
            'median': f"pl.col('{column}').median()",
            'min': f"pl.col('{column}').min()",
            'max': f"pl.col('{column}').max()",
            'std': f"pl.col('{column}').std()",
            'var': f"pl.col('{column}').var()"
        }
        return agg_map.get(aggfunc, f"pl.col('{column}').{aggfunc}()")


class BreakdownCodeGen(OperationCodeGenerator):
    """Generate code for multi-dimensional breakdowns"""
    
    @staticmethod
    def generate(params: Dict[str, Any]) -> str:
        dimensions = params["dimensions"]
        metric_column = params.get("metric_column", "case_id")
        has_helper_column = params.get("has_helper_column", False)
        aggfunc = params.get("aggfunc", "count")
        
        # Adjust aggfunc if using helper column
        if has_helper_column and aggfunc == 'count':
            aggfunc = 'sum'
        
        agg_expr = BreakdownCodeGen._get_polars_agg(aggfunc, metric_column)
        
        code = ""
        
        # Add type conversion for numeric operations if needed
        numeric_ops = ['sum', 'mean', 'avg', 'median', 'std', 'var', 'min', 'max']
        if aggfunc in numeric_ops and metric_column and metric_column != 'case_id':
            code += f"""# Convert {metric_column} to numeric type if it's a string
if df['{metric_column}'].dtype == pl.String or df['{metric_column}'].dtype == pl.Utf8:
    df = df.with_columns(
        pl.col('{metric_column}').cast(pl.Float64, strict=False).alias('{metric_column}')
    )

"""
        
        code += f"""# Breakdown by {', '.join(dimensions)}
result = df.group_by({dimensions}).agg({agg_expr}.alias('{aggfunc}'))
result = result.sort('{aggfunc}', descending=True)
"""
        
        return code
    
    @staticmethod
    def _get_polars_agg(aggfunc, column):
        """Generate polars aggregation expression"""
        agg_map = {
            'count': f"pl.len()",
            'sum': f"pl.col('{column}').sum()",
            'mean': f"pl.col('{column}').mean()",
            'avg': f"pl.col('{column}').mean()",
            'median': f"pl.col('{column}').median()",
            'min': f"pl.col('{column}').min()",
            'max': f"pl.col('{column}').max()",
            'std': f"pl.col('{column}').std()",
            'var': f"pl.col('{column}').var()"
        }
        return agg_map.get(aggfunc, f"pl.col('{column}').{aggfunc}()")


class PercentileCodeGen(OperationCodeGenerator):
    """Generate code for percentile calculations"""
    
    @staticmethod
    def generate(params: Dict[str, Any]) -> str:
        column = params["column"]
        percentiles = params.get("percentiles", [0.25, 0.5, 0.75])  # Default as decimals
        group_by = params.get("group_by_columns")
        
        code = f"""# Percentile Analysis for {column}
# Convert column to numeric type if it's a string
if df['{column}'].dtype == pl.String or df['{column}'].dtype == pl.Utf8:
    df = df.with_columns(
        pl.col('{column}').cast(pl.Float64, strict=False).alias('{column}')
    )

"""
        
        if group_by:
            # Group-wise percentiles
            percentile_exprs = ", ".join([
                f"pl.col('{column}').quantile({p}).alias('p{int(p*100)}')"
                for p in percentiles
            ])
            code += f"""result = df.group_by({group_by}).agg([{percentile_exprs}])
"""
        else:
            # Overall percentiles
            percentile_exprs = ", ".join([
                f"pl.col('{column}').quantile({p}).alias('p{int(p*100)}')"
                for p in percentiles
            ])
            code += f"""result = df.select([{percentile_exprs}])
"""
        
        return code


class RankingCodeGen(OperationCodeGenerator):
    """Generate code for ranking operations"""
    
    @staticmethod
    def generate(params: Dict[str, Any]) -> str:
        column = params["column"]
        method = params.get("method", "dense")
        # For bottom queries, we want ascending=True (smallest values first)
        # For top queries, we want ascending=False (largest values first)
        is_bottom_query = params.get("is_bottom_query", False)
        ascending = params.get("ascending", is_bottom_query)
        group_by = params.get("group_by_columns")
        top_n = params.get("top_n") or params.get("limit_results")
        result_column = params.get("result_column_name", "rank")
        has_helper_column = params.get("has_helper_column", False)
        aggfunc = params.get("aggfunc", "count")
        
        code = ""
        query = params.get("query", "")
        
        # 🆕 SMART DETECTION: If group_by exists AND any of these conditions are true:
        # 1. Column name suggests aggregation ("Number of X", "Count of Y", "Total X")
        # 2. Helper column exists (identifier that needs counting)
        # 3. Aggregation function is specified (mean, sum, median, etc.) 
        # 4. Query contains aggregation keywords (average, total, sum, etc.)
        query_lower = query.lower() if query else ""
        aggregation_keywords = ['average', 'mean', 'total', 'sum', 'median', 'maximum', 'minimum', 'max', 'min']
        has_aggregation_keyword = any(keyword in query_lower for keyword in aggregation_keywords)
        has_aggregation_func = aggfunc and aggfunc not in ['count', 'rank']
        
        needs_aggregation = group_by and (
            'number of' in column.lower() or 
            'count of' in column.lower() or
            'total' in column.lower() or
            has_helper_column or  # If helper column exists, it's an identifier that needs counting
            has_aggregation_func or  # Explicit aggregation function specified
            has_aggregation_keyword  # Query mentions aggregation (e.g., "average resolution time")
        )
        
        # 🆕 EXTRACT AGGREGATION FUNCTION from query if not already specified correctly
        if needs_aggregation and has_aggregation_keyword and aggfunc == 'count':
            # Map query keywords to aggregation functions
            if 'average' in query_lower or 'mean' in query_lower:
                aggfunc = 'mean'
            elif 'sum' in query_lower or 'total' in query_lower:
                aggfunc = 'sum'
            elif 'median' in query_lower:
                aggfunc = 'median'
            elif 'maximum' in query_lower or 'max ' in query_lower:
                aggfunc = 'max'
            elif 'minimum' in query_lower or 'min ' in query_lower:
                aggfunc = 'min'
        
        if needs_aggregation:
            # This is "Rank countries by number of tickets" - aggregate then rank
            # 🆕 SMART LOGIC: Only use helper column if we're doing a COUNT operation
            # For other aggregations (mean, sum, etc.), use the actual column
            if has_helper_column and aggfunc == 'count':
                # Counting rows - use helper column
                aggfunc = 'sum'
                metric_col = '_count_helper'
            else:
                # Aggregating actual values (mean, median, etc.) - use the real column
                metric_col = column
                # Add type conversion for numeric operations
                numeric_ops = ['sum', 'mean', 'avg', 'median', 'std', 'var', 'min', 'max']
                if aggfunc in numeric_ops and column and column != 'case_id':
                    code += f"""# Convert {column} to numeric type if it's a string
if df['{column}'].dtype == pl.String or df['{column}'].dtype == pl.Utf8:
    df = df.with_columns(
        pl.col('{column}').cast(pl.Float64, strict=False).alias('{column}')
    )

"""
            
            agg_expr = RankingCodeGen._get_polars_agg(aggfunc, metric_col)
            
            code += f"""# Ranking {group_by} by {column} (aggregation-based ranking)
# Step 1: Aggregate by group
result = df.group_by({group_by}).agg({agg_expr}.alias('{aggfunc}'))

# Step 2: Sort by aggregated value
result = result.sort('{aggfunc}', descending={not ascending})
"""
            
            # Add head() BEFORE adding rank column if we have a limit
            if top_n:
                query_type = "bottom" if is_bottom_query else "top"
                code += f"""
# Step 3: Take exactly {query_type} {top_n} results (avoids issues with ranking ties)
result = result.head({top_n})
"""
            
            code += f"""
# Step 4: Add rank column for display
result = result.with_columns(
    pl.col('{aggfunc}').rank(method='{method}', descending={not ascending}).alias('{result_column}')
)
"""
        elif group_by:
            # Ranking within groups (row-level)
            code += f"""# Ranking by {column} within groups
result = df.with_columns(
    pl.col('{column}').rank(method='{method}', descending={not ascending}).over({group_by}).alias('{result_column}')
)
result = result.sort('{result_column}')
"""
            if top_n:
                query_type = "bottom" if is_bottom_query else "top"
                code += f"""
# Take exactly {query_type} {top_n} results
result = result.head({top_n})
"""
        else:
            # Overall ranking (row-level)
            code += f"""# Ranking by {column}
result = df.with_columns(
    pl.col('{column}').rank(method='{method}', descending={not ascending}).alias('{result_column}')
)
result = result.sort('{result_column}')
"""
            if top_n:
                query_type = "bottom" if is_bottom_query else "top"
                code += f"""
# Take exactly {query_type} {top_n} results
result = result.head({top_n})
"""
        
        # Note: head() filtering is now applied within each ranking path above
        
        return code
    
    @staticmethod
    def _get_polars_agg(aggfunc, column):
        """Generate polars aggregation expression"""
        agg_map = {
            'count': f"pl.len()",
            'sum': f"pl.col('{column}').sum()",
            'mean': f"pl.col('{column}').mean()",
            'avg': f"pl.col('{column}').mean()",
            'median': f"pl.col('{column}').median()",
            'min': f"pl.col('{column}').min()",
            'max': f"pl.col('{column}').max()",
            'std': f"pl.col('{column}').std()",
            'var': f"pl.col('{column}').var()"
        }
        return agg_map.get(aggfunc, f"pl.col('{column}').{aggfunc}()")
    
    @staticmethod
    def _detect_top_query(query: str) -> bool:
        """
        Detect if query is asking for top/highest/best results
        """
        if not query:
            return False
        
        query_lower = query.lower()
        top_indicators = ['top', 'best', 'highest', 'largest', 'most', 'greatest', 'maximum', 'max']
        
        for indicator in top_indicators:
            if re.search(r'\b' + re.escape(indicator) + r'\b', query_lower):
                return True
        return False
    
    @staticmethod  
    def _detect_bottom_query(query: str) -> bool:
        """
        Detect if query is asking for bottom/lowest/worst results
        Includes typo tolerance for common misspellings
        """
        if not query:
            return False
        
        query_lower = query.lower()
        
        # Exact matches
        bottom_indicators = ['bottom', 'worst', 'lowest', 'smallest', 'least', 'fewest', 'minimum', 'min']
        
        for indicator in bottom_indicators:
            if re.search(r'\b' + re.escape(indicator) + r'\b', query_lower):
                return True
        
        # Common typos for "bottom"
        bottom_typos = ['bototm', 'botom', 'bottm', 'botttom', 'boottom']
        for typo in bottom_typos:
            if re.search(r'\b' + re.escape(typo) + r'\b', query_lower):
                return True
        
        return False


class WindowFunctionCodeGen(OperationCodeGenerator):
    """Generate code for window functions"""
    
    @staticmethod
    def generate(params: Dict[str, Any]) -> str:
        operation = params["operation"]
        column = params["column"]
        partition_by = params.get("partition_by_columns")
        order_by = params.get("order_by_column")
        result_column = params.get("result_column_name", "window_result")
        
        code = f"""# Window Function: {operation}
"""
        
        # Build window specification
        if partition_by and order_by:
            code += f"""result = df.sort('{order_by}')
result = result.with_columns(
    pl.col('{column}').{operation}().over({partition_by}).alias('{result_column}')
)
"""
        elif partition_by:
            code += f"""result = df.with_columns(
    pl.col('{column}').{operation}().over({partition_by}).alias('{result_column}')
)
"""
        elif order_by:
            code += f"""result = df.sort('{order_by}')
result = result.with_columns(
    pl.col('{column}').{operation}().alias('{result_column}')
)
"""
        else:
            code += f"""result = df.with_columns(
    pl.col('{column}').{operation}().alias('{result_column}')
)
"""
        
        return code


class GroupedAggregationCodeGen(OperationCodeGenerator):
    """Generate code for grouped aggregations"""
    
    @staticmethod
    def generate(params: Dict[str, Any]) -> str:
        group_by = params.get("group_by_columns", [])
        
        # Get actual column to use (might be helper column)
        agg_column = params.get("agg_column")
        has_helper_column = params.get("has_helper_column", False)
        
        agg_functions = params.get("agg_functions", ["count"])
        filters = params.get("filters", [])
        temporal_filters = params.get("temporal_filters", [])
        
        if isinstance(agg_functions, str):
            agg_functions = [agg_functions]
        
        # Adjust agg_functions if using helper column
        adjusted_agg_functions = []
        for func in agg_functions:
            if has_helper_column and func == 'count':
                adjusted_agg_functions.append('sum')  # Sum the helper column instead of counting
            else:
                adjusted_agg_functions.append(func)
        
        # Check if agg_column is in group_by to prevent collision
        if agg_column and group_by and agg_column in group_by:
            # Generate filter code
            filter_code_parts = []
            
            # Apply categorical filters
            for filter_spec in filters:
                column = filter_spec.get('column')
                value = filter_spec.get('value')
                operator = filter_spec.get('operator', '==')
                
                if isinstance(value, str):
                    filter_code_parts.append(f"pl.col('{column}') {operator} '{value}'")
                else:
                    filter_code_parts.append(f"pl.col('{column}') {operator} {value}")
            
            # Apply temporal filters
            for temp_filter in temporal_filters:
                if 'filter_expression' in temp_filter and temp_filter['filter_expression']:
                    filter_code_parts.append(temp_filter['filter_expression'])
            
            if filter_code_parts:
                filter_expression = ", ".join(filter_code_parts)
                filter_code = f"""# Apply filters
df_filtered = df.filter({filter_expression})
"""
            else:
                filter_code = f"""# No filters
df_filtered = df.clone()
"""
            
            # Use len() to avoid collision
            base_code = f"""{filter_code}
# Grouped Aggregation (collision avoided - using len instead of count)
result = df_filtered.group_by({group_by}).agg([
    pl.len().alias('count')
])
"""
            
            # Add sorting and limiting if requested (for top/bottom N queries)
            limit_results = params.get("limit_results") or params.get("top_n")
            if limit_results:
                is_bottom_query = params.get("is_bottom_query", False)
                # For collision case, we always use 'count' as the sort column
                sort_column = "count"
                descending = not is_bottom_query  # Top = descending (largest first), Bottom = ascending (smallest first)
                
                limit_code = f"""
# Sort and limit to {'bottom' if is_bottom_query else 'top'} {limit_results}
result = result.sort('{sort_column}', descending={descending})
result = result.head({limit_results})
"""
                return base_code + limit_code
            
            return base_code
        
        # Build aggregation expressions
        agg_exprs = []
        for func in adjusted_agg_functions:
            agg_expr = GroupedAggregationCodeGen._get_polars_agg(func, agg_column, f"{func}")
            agg_exprs.append(agg_expr)
        
        # Generate filter code
        filter_code_parts = []
        
        # Apply categorical filters
        for filter_spec in filters:
            column = filter_spec.get('column')
            value = filter_spec.get('value')
            operator = filter_spec.get('operator', '==')
            
            if isinstance(value, str):
                filter_code_parts.append(f"pl.col('{column}') {operator} '{value}'")
            else:
                filter_code_parts.append(f"pl.col('{column}') {operator} {value}")
        
        # Apply temporal filters
        for temp_filter in temporal_filters:
            if 'filter_expression' in temp_filter and temp_filter['filter_expression']:
                filter_code_parts.append(temp_filter['filter_expression'])
        
        if filter_code_parts:
            filter_expression = ", ".join(filter_code_parts)
            filter_code = f"""# Apply filters
df_filtered = df.filter({filter_expression})
"""
        else:
            filter_code = f"""# No filters
df_filtered = df.clone()
"""
        
        # Add type conversion for numeric operations if needed
        numeric_ops = ['sum', 'mean', 'avg', 'median', 'std', 'var', 'min', 'max']
        needs_numeric_conversion = any(func in numeric_ops for func in adjusted_agg_functions)
        
        type_conversion_code = ""
        if needs_numeric_conversion and agg_column and agg_column != 'case_id':
            type_conversion_code = f"""# Convert {agg_column} to numeric type if it's a string
if df_filtered['{agg_column}'].dtype == pl.String or df_filtered['{agg_column}'].dtype == pl.Utf8:
    df_filtered = df_filtered.with_columns(
        pl.col('{agg_column}').cast(pl.Float64, strict=False).alias('{agg_column}')
    )

"""
        
        # Build aggregation code
        if group_by:
            agg_list = ",\n    ".join(agg_exprs)
            agg_code = f"""{type_conversion_code}# Grouped Aggregation
result = df_filtered.group_by({group_by}).agg([
    {agg_list}
])
"""
        else:
            # Simple aggregation (no grouping)
            agg_list = ",\n    ".join(agg_exprs)
            agg_code = f"""{type_conversion_code}# Simple Aggregation (no grouping)
result = df_filtered.select([
    {agg_list}
])
"""
        
        # Add sorting and limiting if requested (for top/bottom N queries)
        limit_results = params.get("limit_results") or params.get("top_n")
        if limit_results and group_by:  # Only apply limit for grouped aggregations (not simple aggregations)
            is_bottom_query = params.get("is_bottom_query", False)
            
            # Determine sort column from the first aggregation function
            # The alias is the function name itself (e.g., 'sum', 'mean', 'count')
            sort_column = adjusted_agg_functions[0] if adjusted_agg_functions else "sum"
            
            # Top = descending (largest first), Bottom = ascending (smallest first)
            descending = not is_bottom_query
            
            limit_code = f"""
# Sort and limit to {'bottom' if is_bottom_query else 'top'} {limit_results}
result = result.sort('{sort_column}', descending={descending})
result = result.head({limit_results})
"""
            return filter_code + agg_code + limit_code
        
        return filter_code + agg_code
    
    @staticmethod
    def _get_polars_agg(func, column, alias):
        """Generate polars aggregation expression with alias"""
        agg_map = {
            'count': f"pl.len().alias('{alias}')",
            'sum': f"pl.col('{column}').sum().alias('{alias}')",
            'mean': f"pl.col('{column}').mean().alias('{alias}')",
            'avg': f"pl.col('{column}').mean().alias('{alias}')",
            'median': f"pl.col('{column}').median().alias('{alias}')",
            'min': f"pl.col('{column}').min().alias('{alias}')",
            'max': f"pl.col('{column}').max().alias('{alias}')",
            'std': f"pl.col('{column}').std().alias('{alias}')",
            'var': f"pl.col('{column}').var().alias('{alias}')"
        }
        return agg_map.get(func, f"pl.col('{column}').{func}().alias('{alias}')")


class DistributionCodeGen(OperationCodeGenerator):
    """Generate code for distribution analysis"""
    
    @staticmethod
    def generate(params: Dict[str, Any]) -> str:
        column = params["column"]
        bins = params.get("bins", 10)
        
        code = f"""# Distribution Analysis for {column}
# Calculate histogram
hist_result = df.select(pl.col('{column}').hist(bin_count={bins}))

# Extract bin edges and counts
result = pl.DataFrame({{
    'bin': hist_result['breakpoint'][0],
    'count': hist_result['count'][0]
}})
"""
        
        return code


class StatisticalTestCodeGen(OperationCodeGenerator):
    """Generate code for statistical tests"""
    
    @staticmethod
    def generate(params: Dict[str, Any]) -> str:
        test_type = params["test_type"]
        
        if not SCIPY_AVAILABLE:
            return "# ERROR: scipy is required for statistical tests\nresult = pl.DataFrame({'error': ['scipy not available']})\n"
        
        if test_type == "t_test":
            column = params["column"]
            group_column = params["group_column"]
            
            code = f"""# T-Test: {column} by {group_column}
import scipy.stats as stats

# Get unique groups
groups = df['{group_column}'].unique().to_list()
if len(groups) != 2:
    result = pl.DataFrame({{'error': ['T-test requires exactly 2 groups']}})
else:
    group1_data = df.filter(pl.col('{group_column}') == groups[0])['{column}'].to_numpy()
    group2_data = df.filter(pl.col('{group_column}') == groups[1])['{column}'].to_numpy()
    
    t_stat, p_value = stats.ttest_ind(group1_data, group2_data)
    
    result = pl.DataFrame({{
        'test': ['t-test'],
        'statistic': [t_stat],
        'p_value': [p_value],
        'significant': [p_value < 0.05]
    }})
"""
        
        elif test_type == "anova":
            column = params["column"]
            group_column = params["group_column"]
            
            code = f"""# ANOVA: {column} by {group_column}
import scipy.stats as stats

# Get data for each group
groups = df['{group_column}'].unique().to_list()
group_data = [df.filter(pl.col('{group_column}') == g)['{column}'].to_numpy() for g in groups]

f_stat, p_value = stats.f_oneway(*group_data)

result = pl.DataFrame({{
    'test': ['anova'],
    'statistic': [f_stat],
    'p_value': [p_value],
    'significant': [p_value < 0.05]
}})
"""
        
        elif test_type == "correlation":
            column1 = params["column1"]
            column2 = params["column2"]
            method = params.get("method", "pearson")
            
            if method == "pearson":
                code = f"""# Pearson Correlation: {column1} vs {column2}
correlation = df.select(pl.corr('{column1}', '{column2}')).item()

result = pl.DataFrame({{
    'correlation': [correlation],
    'method': ['pearson']
}})
"""
            else:
                code = f"""# {method.capitalize()} Correlation: {column1} vs {column2}
import scipy.stats as stats

data1 = df['{column1}'].to_numpy()
data2 = df['{column2}'].to_numpy()

if '{method}' == 'spearman':
    corr, p_value = stats.spearmanr(data1, data2)
else:  # kendall
    corr, p_value = stats.kendalltau(data1, data2)

result = pl.DataFrame({{
    'correlation': [corr],
    'p_value': [p_value],
    'method': ['{method}']
}})
"""
        
        else:
            code = "# ERROR: Unknown statistical test\nresult = pl.DataFrame({'error': ['Unknown test type']})\n"
        
        return code


class BinningCodeGen(OperationCodeGenerator):
    """Generate code for binning/discretization"""
    
    @staticmethod
    def generate(params: Dict[str, Any]) -> str:
        column = params["column"]
        bins = params.get("bins", 5)
        labels = params.get("labels")
        method = params.get("method", "cut")
        result_column = params.get("result_column_name", f"{column}_binned")
        
        code = f"""# Binning: {column}
"""
        
        if method == "qcut":
            # Quantile-based binning
            if labels:
                code += f"""result = df.with_columns(
    pl.col('{column}').qcut({bins}, labels={labels}).alias('{result_column}')
)
"""
            else:
                code += f"""result = df.with_columns(
    pl.col('{column}').qcut({bins}).alias('{result_column}')
)
"""
        else:
            # Equal-width binning
            if labels:
                code += f"""result = df.with_columns(
    pl.col('{column}').cut({bins}, labels={labels}).alias('{result_column}')
)
"""
            else:
                code += f"""result = df.with_columns(
    pl.col('{column}').cut({bins}).alias('{result_column}')
)
"""
        
        return code


class DateArithmeticCodeGen(OperationCodeGenerator):
    """Generate code for date arithmetic"""
    
    @staticmethod
    def generate(params: Dict[str, Any]) -> str:
        operation = params["operation"]
        date_column = params.get("date_column")
        result_column = params.get("result_column_name", "result")
        
        code = ""
        
        if operation == "date_diff":
            date_column2 = params.get("date_column2")
            unit = params.get("unit", "days")
            
            # Polars date difference - convert date columns to datetime if they're strings
            code += f"""# Convert date columns to datetime if they're strings
if df['{date_column}'].dtype in [pl.String, pl.Utf8]:
    df = df.with_columns(pl.col('{date_column}').str.to_datetime().alias('{date_column}'))
if df['{date_column2}'].dtype in [pl.String, pl.Utf8]:
    df = df.with_columns(pl.col('{date_column2}').str.to_datetime().alias('{date_column2}'))

result = df.with_columns(
    (pl.col('{date_column2}') - pl.col('{date_column}')).dt.total_{unit}().alias('{result_column}')
)
"""
        
        elif operation == "extract_component":
            component = params["component"]
            code += f"""# Convert date column to datetime if it's a string
if df['{date_column}'].dtype in [pl.String, pl.Utf8]:
    df = df.with_columns(pl.col('{date_column}').str.to_datetime().alias('{date_column}'))

result = df.with_columns(
    pl.col('{date_column}').dt.{component}().alias('{result_column}')
)
"""
        
        return code


class FilterCodeGen(OperationCodeGenerator):
    """Generate code for filtering"""
    
    @staticmethod
    def generate(params: Dict[str, Any]) -> str:
        column = params["column"]
        operator = params["operator"]
        value = params["value"]
        
        if operator == "in":
            return f"df = df.filter(pl.col('{column}').is_in({value}))\n"
        elif operator == "contains":
            return f"df = df.filter(pl.col('{column}').str.contains('{value}', literal=False))\n"
        elif operator == "not_null":
            return f"df = df.filter(pl.col('{column}').is_not_null())\n"
        elif operator == "==":
            return f"df = df.filter(pl.col('{column}') == {repr(value)})\n"
        elif operator == "!=":
            return f"df = df.filter(pl.col('{column}') != {repr(value)})\n"
        elif operator == ">":
            return f"df = df.filter(pl.col('{column}') > {repr(value)})\n"
        elif operator == "<":
            return f"df = df.filter(pl.col('{column}') < {repr(value)})\n"
        elif operator == ">=":
            return f"df = df.filter(pl.col('{column}') >= {repr(value)})\n"
        elif operator == "<=":
            return f"df = df.filter(pl.col('{column}') <= {repr(value)})\n"
        else:
            return f"df = df.filter(pl.col('{column}') {operator} {repr(value)})\n"


class TemporalFilterCodeGen(OperationCodeGenerator):
    """Generate code for temporal filtering"""
    
    @staticmethod
    def generate(params: Dict[str, Any]) -> str:
        date_column = params["date_column"]
        filter_type = params["filter_type"]
        
        # Convert date column to datetime if it's a string
        code = f"""# Convert date column to datetime if it's a string
if df['{date_column}'].dtype in [pl.String, pl.Utf8]:
    df = df.with_columns(pl.col('{date_column}').str.to_datetime())
"""
        
        if filter_type == "last_n_days":
            n = params["n_value"]
            code += f"""from datetime import datetime, timedelta
cutoff = datetime.now() - timedelta(days={n})
df = df.filter(pl.col('{date_column}') >= cutoff)
"""
        
        elif filter_type == "last_n_months":
            n = params["n_value"]
            code += f"""from datetime import datetime
from dateutil.relativedelta import relativedelta
cutoff = datetime.now() - relativedelta(months={n})
df = df.filter(pl.col('{date_column}') >= cutoff)
"""
        
        elif filter_type == "last_quarter":
            code += f"""from datetime import datetime
now = datetime.now()
last_q = now.month // 3 if now.month % 3 != 1 else (now.month // 3) - 1
last_q = last_q if last_q > 0 else 4
last_q_year = now.year if last_q < 4 else now.year - 1
df = df.filter(
    (pl.col('{date_column}').dt.quarter() == last_q) & 
    (pl.col('{date_column}').dt.year() == last_q_year)
)
"""
        
        elif filter_type == "this_quarter":
            code += f"""from datetime import datetime
now = datetime.now()
current_quarter = (now.month - 1) // 3 + 1
df = df.filter(
    (pl.col('{date_column}').dt.quarter() == current_quarter) & 
    (pl.col('{date_column}').dt.year() == now.year)
)
"""
        
        elif filter_type == "specific_quarter":
            quarter = params["quarter"]
            year = params["year"]
            code += f"""df = df.filter(
    (pl.col('{date_column}').dt.quarter() == {quarter}) & 
    (pl.col('{date_column}').dt.year() == {year})
)
"""
        
        elif filter_type == "specific_month":
            month = params["month"]
            year = params.get("year")
            if year:
                code += f"""df = df.filter(
    (pl.col('{date_column}').dt.month() == {month}) & 
    (pl.col('{date_column}').dt.year() == {year})
)
"""
            else:
                code += f"df = df.filter(pl.col('{date_column}').dt.month() == {month})\n"
        
        elif filter_type == "specific_year":
            year = params["year"]
            code += f"df = df.filter(pl.col('{date_column}').dt.year() == {year})\n"
        
        elif filter_type == "date_range":
            start = params["start_date"]
            end = params["end_date"]
            code += f"""df = df.filter(
    (pl.col('{date_column}') >= '{start}') & 
    (pl.col('{date_column}') <= '{end}')
)
"""
        
        elif filter_type == "ytd":
            code += f"""from datetime import datetime
now = datetime.now()
start_of_year = datetime(now.year, 1, 1)
df = df.filter(
    (pl.col('{date_column}') >= start_of_year) & 
    (pl.col('{date_column}') <= now)
)
"""
        
        elif filter_type == "mtd":
            code += f"""from datetime import datetime
now = datetime.now()
start_of_month = datetime(now.year, now.month, 1)
df = df.filter(
    (pl.col('{date_column}') >= start_of_month) & 
    (pl.col('{date_column}') <= now)
)
"""
        
        elif filter_type == "qtd":
            code += f"""from datetime import datetime
now = datetime.now()
quarter_start_month = ((now.month - 1) // 3) * 3 + 1
start_of_quarter = datetime(now.year, quarter_start_month, 1)
df = df.filter(
    (pl.col('{date_column}') >= start_of_quarter) & 
    (pl.col('{date_column}') <= now)
)
"""
        
        return code


# ============================================================================
# CODE GENERATOR REGISTRY
# ============================================================================

CODE_GENERATORS = {
    # 🆕 UNIFIED PERIOD COMPARISON
    "period_comparison": PeriodComparisonCodeGen,
    
    # 🆕 TIME SERIES
    "time_series": TimeSeriesCodeGen,
    
    # 🆕 COLUMN DESCRIPTION
    "column_description": ColumnDescriptionCodeGen,
    
    # Other operations
    "general_comparison": ComparisonCodeGen,
    "pivot": PivotCodeGen,
    "composition_percentage": CompositionPercentageCodeGen,
    "breakdown": BreakdownCodeGen,
    "percentile": PercentileCodeGen,
    "ranking": RankingCodeGen,
    "window_function": WindowFunctionCodeGen,
    "grouped_aggregation": GroupedAggregationCodeGen,
    
    # Additional operations
    "distribution": DistributionCodeGen,
    "statistical_test": StatisticalTestCodeGen,
    "binning": BinningCodeGen,
    "date_arithmetic": DateArithmeticCodeGen,
    
    # Filtering
    "filter": FilterCodeGen,
    "temporal_filter": TemporalFilterCodeGen,
}


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_season(month):
    """Helper function for season-based period comparisons"""
    if month in [3, 4, 5]:
        return 'Spring'
    elif month in [6, 7, 8]:
        return 'Summer'
    elif month in [9, 10, 11]:
        return 'Fall'
    else:
        return 'Winter'


def calc_percentiles(x):
    """Helper function for percentile calculations"""
    return pl.DataFrame({
        'p25': [x.quantile(0.25)],
        'p50': [x.quantile(0.5)], 
        'p75': [x.quantile(0.75)]
    })
