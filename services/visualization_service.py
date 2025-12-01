"""
Intelligent Visualization Service
Handles chart creation and visualization logic
PURE DATA-DRIVEN APPROACH - No keyword dependencies
"""

import pandas as pd
import numpy as np
import matplotlib
# CRITICAL: Use non-GUI backend to avoid threading issues on macOS
matplotlib.use('Agg')  # Must be called before importing pyplot
import matplotlib.pyplot as plt
import seaborn as sns
import io
import base64
from typing import Dict, Any, List, Optional, Tuple
import logging
from master_logger import setup_module_logger

master_logger = setup_module_logger('meta_agents.query_understanding_agent')

# Set style for better-looking charts (forces font cache load at startup)
plt.style.use('default')
sns.set_palette("husl")


class IntelligentVisualizationService:
    """Service for creating intelligent visualizations based on data and query"""
    
    def __init__(self):
        """Initialize visualization service"""
        self.logger = master_logger
        
    def create_intelligent_visualization(self, query: str, result_df: pd.DataFrame, operation_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Create intelligent visualization based on user query and data characteristics
        
        Args:
            query: User's natural language query
            result_df: DataFrame to visualize
            operation_type: Type of operation (e.g., 'period_comparison') to enable operation-specific chart rules
        """
        try:
            # ============ DEFENSIVE FALLBACK LAYER ============
            fallback_activated = False
            fallback_reason = None
            original_type = type(result_df).__name__
            
            # CASE 1: Handle Series input (clear contract violation)
            if isinstance(result_df, pd.Series):
                fallback_reason = "series_input"
                master_logger.warning(f"[VIZ FALLBACK] ⚠️ Received Series instead of DataFrame!")
                master_logger.warning(f"[VIZ FALLBACK] Series name: {result_df.name}, length: {len(result_df)}")
                master_logger.warning(f"[VIZ FALLBACK] This indicates upstream normalization was skipped")
                
                # Normalize Series to DataFrame
                metric_name = result_df.name if result_df.name else 'value'
                result_df = result_df.reset_index(name=metric_name)
                fallback_activated = True
                
                master_logger.info(f"[VIZ FALLBACK] ✓ Normalized Series → DataFrame")
                master_logger.info(f"[VIZ FALLBACK] New shape: {result_df.shape}")
                master_logger.info(f"[VIZ FALLBACK] New columns: {result_df.columns.tolist()}")
            
            # CASE 2: Handle DataFrame with index-based structure
            elif isinstance(result_df, pd.DataFrame):
                has_named_index = result_df.index.name is not None
                has_default_index = result_df.index.equals(pd.RangeIndex(len(result_df)))
                single_column = len(result_df.columns) == 1
                
                # CRITICAL: Also check if index contains non-numeric data (categorical groupby result)
                index_is_categorical = (
                    result_df.index.dtype == 'object' or 
                    pd.api.types.is_string_dtype(result_df.index) or
                    pd.api.types.is_categorical_dtype(result_df.index)
                )
                
                # Normalize if index carries data that should be a column
                should_reset = (
                    single_column and 
                    (has_named_index or index_is_categorical) and 
                    not has_default_index
                )
                
                if should_reset:
                    fallback_reason = "indexed_structure"
                    index_name = result_df.index.name or 'index'
                    index_dtype = result_df.index.dtype
                    column_name = result_df.columns[0]
                    
                    master_logger.warning(f"[VIZ FALLBACK] ⚠️ Index-based structure detected:")
                    master_logger.warning(f"[VIZ FALLBACK] - Index name: '{index_name}' (dtype: {index_dtype})")
                    master_logger.warning(f"[VIZ FALLBACK] - Index is categorical: {index_is_categorical}")
                    master_logger.warning(f"[VIZ FALLBACK] - Single data column: '{column_name}'")
                    master_logger.warning(f"[VIZ FALLBACK] - Converting to column-based for visualization compatibility")
                    
                    # Convert index to column for visualization
                    result_df = result_df.reset_index()
                    fallback_activated = True
                    
                    # Verify the reset worked
                    master_logger.info(f"[VIZ FALLBACK] ✓ Index reset completed")
                    master_logger.info(f"[VIZ FALLBACK] ✓ New columns: {result_df.columns.tolist()}")
                    master_logger.info(f"[VIZ FALLBACK] ✓ New shape: {result_df.shape}")
                    
                    # Context-specific logging
                    if pd.api.types.is_datetime64_any_dtype(result_df[index_name]):
                        master_logger.info(f"[VIZ FALLBACK] ✓ DateTime '{index_name}' now in column - enables time series charts")
                    else:
                        master_logger.info(f"[VIZ FALLBACK] ✓ Category '{index_name}' now in column - enables categorical charts")
            
            if fallback_activated:
                master_logger.warning(f"[VIZ FALLBACK] ═══════════════════════════════════════════════════════")
                master_logger.warning(f"[VIZ FALLBACK] ⚠️  FALLBACK WAS ACTIVATED (reason: {fallback_reason})")
                master_logger.warning(f"[VIZ FALLBACK] ⚠️  Original type: {original_type}")
                master_logger.warning(f"[VIZ FALLBACK] ⚠️  CHECK UPSTREAM CODE - normalization should happen earlier!")
                master_logger.warning(f"[VIZ FALLBACK] ═══════════════════════════════════════════════════════")
            
            # ============ END DEFENSIVE FALLBACK ============
            
            # DEFENSIVE FIX: Ensure all column names are strings (matplotlib fails with numeric column names)
            if isinstance(result_df, pd.DataFrame):
                original_columns = result_df.columns.tolist()
                result_df.columns = [str(col) for col in result_df.columns]
                if original_columns != result_df.columns.tolist():
                    master_logger.info(f"[VIZ] Sanitized numeric column names to strings: {original_columns} → {result_df.columns.tolist()}")
            
            master_logger.info(f"[VIZ] Starting visualization for query: {query}")
            master_logger.info(f"[VIZ] DataFrame shape: {result_df.shape}")
            master_logger.info(f"[VIZ] DataFrame columns: {list(result_df.columns)}")
            master_logger.info(f"[VIZ] DataFrame dtypes:\n{result_df.dtypes}")
            master_logger.info(f"[VIZ] First few rows:\n{result_df.head()}")
            
            # ========== SMART DATETIME DETECTION & CONVERSION ==========
            # Check string columns for datetime patterns
            for col in result_df.select_dtypes(include=['object', 'string']).columns:
                try:
                    # Try to parse as datetime
                    sample_values = result_df[col].dropna().head(10)
                    if len(sample_values) > 0:
                        # Try parsing a sample
                        pd.to_datetime(sample_values.iloc[0])
                        
                        # If successful, check if majority can be parsed
                        test_parse = pd.to_datetime(sample_values, errors='coerce')
                        success_rate = test_parse.notna().sum() / len(sample_values)
                        
                        if success_rate > 0.8:  # 80%+ parseable = datetime column
                            master_logger.info(f"[VIZ DATETIME] 🔍 Detected datetime pattern in '{col}'")
                            master_logger.info(f"[VIZ DATETIME] Converting '{col}' from {result_df[col].dtype} → datetime64")
                            
                            # Convert entire column
                            result_df[col] = pd.to_datetime(result_df[col], errors='coerce')
                            
                            master_logger.info(f"[VIZ DATETIME] ✓ Conversion successful: {result_df[col].dtype}")
                            master_logger.info(f"[VIZ DATETIME] ✓ Sample values: {result_df[col].head(3).tolist()}")
                except Exception as e:
                    # Not a datetime column, continue
                    pass
            # ========== END DATETIME DETECTION ==========
            
            if result_df.empty or len(result_df) == 0:
                master_logger.info("[VIZ] DataFrame is empty, no visualization needed")
                return {"needs_visualization": False}
                
        except Exception as e:
            master_logger.error(f"[VIZ] Error in initial check: {e}")
            return {"needs_visualization": False}
        
        # ========== PURE DATA-DRIVEN ANALYSIS ==========
        data_profile = self._profile_data_characteristics(result_df)
        master_logger.info(f"[VIZ] Data profile: {data_profile}")
        
        # Determine chart type based purely on data characteristics
        chart_type, chart_config = self._select_chart_from_data_profile(data_profile, result_df, operation_type)
        # ========== END DATA-DRIVEN ANALYSIS ==========
        
        master_logger.info(f"[VIZ] Chart type selected: {chart_type}")
        master_logger.info(f"[VIZ] Chart config: {chart_config}")

        if not chart_type:
            master_logger.warning("[VIZ] No chart type determined, returning needs_visualization=False")
            return {"needs_visualization": False}
        
        # Generate the chart
        chart_data = self._generate_chart(chart_type, result_df, chart_config, query)

        master_logger.info(f"[VIZ] Returning visualization:")
        master_logger.info(f"[VIZ] - needs_visualization: True")
        master_logger.info(f"[VIZ] - chart_type: {chart_type}")
        master_logger.info(f"[VIZ] - chart_image exists: {chart_data.get('image_base64') is not None}")

        return {
            "needs_visualization": True,
            "chart_type": chart_type,
            "chart_image": chart_data.get("image_base64"),
            "chart_description": chart_data.get("description"),
            "chart_config": chart_config,
            "interactive": False,
            "data_insights": self._generate_data_insights(result_df, chart_type)
        }
    
    def _profile_data_characteristics(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        PURE DATA-DRIVEN: Analyze data structure without any keyword hints
        Returns comprehensive data profile for visualization decision
        """
        # Column type analysis
        numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
        categorical_cols = df.select_dtypes(include=['object', 'category', 'string']).columns.tolist()
        datetime_cols = df.select_dtypes(include=['datetime', 'datetimetz']).columns.tolist()
        
        master_logger.info(f"[VIZ PROFILE] Column analysis:")
        master_logger.info(f"[VIZ PROFILE] - Total columns: {len(df.columns)}")
        master_logger.info(f"[VIZ PROFILE] - Numeric: {numeric_cols}")
        master_logger.info(f"[VIZ PROFILE] - Categorical: {categorical_cols}")
        master_logger.info(f"[VIZ PROFILE] - Datetime: {datetime_cols}")
        master_logger.info(f"[VIZ PROFILE] - All dtypes: {dict(df.dtypes)}")
        
        profile = {
            'row_count': len(df),
            'column_count': len(df.columns),
            'numeric_columns': numeric_cols,
            'categorical_columns': categorical_cols,
            'datetime_columns': datetime_cols,
            'num_numeric': len(numeric_cols),
            'num_categorical': len(categorical_cols),
            'num_datetime': len(datetime_cols),
        }
        
        # ========== AGGREGATION SIGNATURE DETECTION ==========
        # Signal 1: Row count (aggregated data typically has few rows)
        profile['is_small_dataset'] = len(df) < 100
        profile['is_very_small_dataset'] = len(df) < 50
        
        # Signal 2: Categorical uniqueness ratio (100% unique = aggregated groups)
        if len(categorical_cols) > 0:
            cat_col = categorical_cols[0]
            unique_ratio = df[cat_col].nunique() / len(df)
            profile['categorical_unique_ratio'] = unique_ratio
            profile['categories_mostly_unique'] = unique_ratio > 0.8
        else:
            profile['categorical_unique_ratio'] = 0
            profile['categories_mostly_unique'] = False
        
        # Signal 3: Structure pattern (1 categorical + 1 numeric = classic groupby result)
        profile['has_groupby_structure'] = (
            len(categorical_cols) >= 1 and 
            len(numeric_cols) == 1
        )
        
        # Signal 4: Numeric value patterns (counts/aggregates vs raw data)
        if len(numeric_cols) > 0:
            numeric_col = numeric_cols[0]
            values = df[numeric_col].dropna()
            
            if len(values) > 0:
                # Check if values look like counts (all integers, reasonable range)
                all_integers = values.apply(lambda x: float(x).is_integer()).all()
                min_val = values.min()
                max_val = values.max()
                value_range = max_val - min_val
                
                # Aggregated counts typically: integers, positive, moderate range
                profile['numeric_all_integers'] = all_integers
                profile['numeric_all_positive'] = (min_val >= 0)
                profile['numeric_value_range'] = value_range
                
                # Check for coefficient of variation (CV)
                if values.mean() > 0:
                    cv = values.std() / values.mean()
                    profile['numeric_coefficient_variation'] = cv
                    # High CV suggests raw data, low CV suggests aggregated counts
                    profile['has_low_variation'] = cv < 1.0
                else:
                    profile['numeric_coefficient_variation'] = 0
                    profile['has_low_variation'] = False
            else:
                profile['numeric_all_integers'] = False
                profile['numeric_all_positive'] = False
                profile['numeric_value_range'] = 0
                profile['numeric_coefficient_variation'] = 0
                profile['has_low_variation'] = False
        
        # Signal 5: Repeated values check (raw data has many repeats, aggregated has unique values)
        if len(numeric_cols) > 0:
            numeric_col = numeric_cols[0]
            value_counts = df[numeric_col].value_counts()
            unique_values = len(value_counts)
            total_values = len(df[numeric_col].dropna())
            
            if total_values > 0:
                repetition_ratio = unique_values / total_values
                profile['numeric_repetition_ratio'] = repetition_ratio
                # High ratio (close to 1) = most values are unique = aggregated
                # Low ratio = many repeated values = raw data
                profile['values_mostly_unique'] = repetition_ratio > 0.7
            else:
                profile['numeric_repetition_ratio'] = 0
                profile['values_mostly_unique'] = False
        
        # ========== AGGREGATION SCORE (0-5) ==========
        # Count how many aggregation signals are present
        aggregation_signals = [
            profile.get('is_very_small_dataset', False),
            profile.get('has_groupby_structure', False),
            profile.get('categories_mostly_unique', False),
            profile.get('values_mostly_unique', False),
            profile.get('numeric_all_integers', False) and profile.get('numeric_all_positive', False)
        ]
        
        profile['aggregation_score'] = sum(aggregation_signals)
        profile['is_aggregated_data'] = profile['aggregation_score'] >= 3
        
        master_logger.info(f"[VIZ PROFILE] Aggregation signals detected: {sum(aggregation_signals)}/5")
        master_logger.info(f"[VIZ PROFILE] - Small dataset (<50 rows): {aggregation_signals[0]}")
        master_logger.info(f"[VIZ PROFILE] - Groupby structure (cat+num): {aggregation_signals[1]}")
        master_logger.info(f"[VIZ PROFILE] - Categories unique (>80%): {aggregation_signals[2]}")
        master_logger.info(f"[VIZ PROFILE] - Values unique (>70%): {aggregation_signals[3]}")
        master_logger.info(f"[VIZ PROFILE] - Integer counts pattern: {aggregation_signals[4]}")
        
        return profile
    
    def _select_chart_from_data_profile(self, profile: Dict, df: pd.DataFrame, operation_type: Optional[str] = None) -> Tuple[str, Dict]:
        """
        PURE DATA-DRIVEN: Select chart type based solely on data characteristics
        No query parsing, no keywords - just data structure
        
        Args:
            profile: Data profile dictionary with column types and statistics
            df: DataFrame to visualize
            operation_type: Type of operation (e.g., 'period_comparison') to enable operation-specific chart rules
        """
        numeric_cols = profile['numeric_columns']
        categorical_cols = profile['categorical_columns']
        datetime_cols = profile['datetime_columns']
        
        master_logger.info(f"[VIZ DECISION] Starting chart selection")
        master_logger.info(f"[VIZ DECISION] Aggregation score: {profile['aggregation_score']}/5")
        master_logger.info(f"[VIZ DECISION] Is aggregated: {profile['is_aggregated_data']}")
        
        # ========== DECISION TREE (PURE DATA-DRIVEN) ==========
        
        # RULE 0: Period comparison data (column named 'period' + numeric) → ALWAYS BAR CHART
        # Only applies to period_comparison operation_type
        if (operation_type == 'period_comparison' and 
            len(categorical_cols) > 0 and 'period' in categorical_cols and len(numeric_cols) > 0):
            master_logger.info(f"[VIZ DECISION] ✓ RULE 0: Period comparison detected (column named 'period')")
            master_logger.info(f"[VIZ DECISION]   → BAR CHART (period comparison always uses bar chart)")
            return 'bar', {
                'title': f'{numeric_cols[0]} by period',
                'x_column': 'period',
                'y_column': numeric_cols[0]
            }
        
        # RULE 0.5: Interaction heatmap (2 categorical + numeric) → HEATMAP
        # Only applies to interaction_heatmap operation_type
        if (operation_type == 'interaction_heatmap' and 
            len(categorical_cols) >= 2 and len(numeric_cols) > 0):
            master_logger.info(f"[VIZ DECISION] ✓ RULE 0.5: Interaction heatmap detected (2+ categorical columns)")
            master_logger.info(f"[VIZ DECISION]   - Categorical columns: {categorical_cols[:2]}")
            master_logger.info(f"[VIZ DECISION]   - Numeric column: {numeric_cols[0]}")
            master_logger.info(f"[VIZ DECISION]   → HEATMAP (2D categorical interaction)")
            return 'heatmap', {
                'title': f'{numeric_cols[0]} by {categorical_cols[0]} × {categorical_cols[1]}',
                'x_column': categorical_cols[0],
                'y_column': categorical_cols[1],
                'value_column': numeric_cols[0]
            }
        
        # RULE 1: Time series data (datetime + numeric)
        if len(datetime_cols) > 0 and len(numeric_cols) > 0:
            master_logger.info(f"[VIZ DECISION] ✓ RULE 1: Time series structure detected")
            master_logger.info(f"[VIZ DECISION]   - Datetime column: {datetime_cols[0]}")
            master_logger.info(f"[VIZ DECISION]   - Numeric column: {numeric_cols[0]}")
            return 'line', {
                'title': f'{numeric_cols[0]} over time',
                'x_column': datetime_cols[0],
                'y_column': numeric_cols[0]
            }
        
        # RULE 2: Categorical + Numeric with aggregation signature → BAR CHART
        if profile['has_groupby_structure'] and profile['is_aggregated_data']:
            master_logger.info(f"[VIZ DECISION] ✓ RULE 2: Aggregated categorical data")
            master_logger.info(f"[VIZ DECISION]   → BAR CHART (categorical grouping)")
            return 'bar', {
                'title': f'{numeric_cols[0]} by {categorical_cols[0]}',
                'x_column': categorical_cols[0],
                'y_column': numeric_cols[0]
            }
        
        # RULE 3: Categorical + Numeric BUT looks like raw data → BAR CHART (still better than histogram)
        if len(categorical_cols) > 0 and len(numeric_cols) > 0:
            master_logger.info(f"[VIZ DECISION] ✓ RULE 3: Categorical + numeric structure")
            master_logger.info(f"[VIZ DECISION]   → BAR CHART (mixed types)")
            return 'bar', {
                'title': f'{numeric_cols[0]} by {categorical_cols[0]}',
                'x_column': categorical_cols[0],
                'y_column': numeric_cols[0]
            }
        
        # RULE 4: Single numeric column, LARGE dataset (>100 rows) → HISTOGRAM
        if len(numeric_cols) == 1 and len(categorical_cols) == 0 and not profile['is_small_dataset']:
            master_logger.info(f"[VIZ DECISION] ✓ RULE 4: Single numeric, large dataset ({profile['row_count']} rows)")
            master_logger.info(f"[VIZ DECISION]   → HISTOGRAM (raw data distribution)")
            return 'histogram', {
                'title': f'Distribution of {numeric_cols[0]}',
                'column': numeric_cols[0]
            }
        
        # RULE 5: Single numeric column, small dataset BUT all values unique → LINE CHART
        # This catches cases where categorical grouping column was lost but we have aggregated counts
        if (len(numeric_cols) == 1 and 
            len(categorical_cols) == 0 and 
            profile['is_very_small_dataset'] and 
            profile.get('values_mostly_unique', False) and
            profile.get('numeric_repetition_ratio', 0) == 1.0):  # 100% unique = definitely aggregated
            master_logger.info(f"[VIZ DECISION] ✓ RULE 5: Single numeric, very small dataset with all unique values")
            master_logger.info(f"[VIZ DECISION]   → LINE CHART (likely aggregated data, categorical column lost)")
            master_logger.warning(f"[VIZ DECISION] ⚠️  This suggests upstream data normalization failed!")
            return 'line', {
                'title': f'{numeric_cols[0]} by index',
                'x_column': df.index.name or 'index',
                'y_column': numeric_cols[0]
            }
        
        # RULE 6: Single numeric column, SMALL dataset with high variation → HISTOGRAM
        if (len(numeric_cols) == 1 and 
            len(categorical_cols) == 0 and 
            profile.get('numeric_coefficient_variation', 0) > 1.0):
            master_logger.info(f"[VIZ DECISION] ✓ RULE 6: Single numeric with high variation (CV={profile['numeric_coefficient_variation']:.2f})")
            master_logger.info(f"[VIZ DECISION]   → HISTOGRAM (variable raw data)")
            return 'histogram', {
                'title': f'Distribution of {numeric_cols[0]}',
                'column': numeric_cols[0]
            }
        
        # RULE 7: Single numeric column, SMALL dataset with low variation → LINE/BAR
        # (This catches edge cases where it's probably sequential data)
        if len(numeric_cols) == 1 and len(categorical_cols) == 0 and profile['is_small_dataset']:
            master_logger.info(f"[VIZ DECISION] ✓ RULE 7: Single numeric, small dataset ({profile['row_count']} rows)")
            master_logger.info(f"[VIZ DECISION]   → LINE CHART (sequential data)")
            return 'line', {
                'title': f'{numeric_cols[0]} sequence',
                'x_column': df.index.name or 'index',
                'y_column': numeric_cols[0]
            }
        
        # RULE 8: Two numeric columns → SCATTER PLOT
        if len(numeric_cols) >= 2:
            master_logger.info(f"[VIZ DECISION] ✓ RULE 7: Two numeric columns")
            master_logger.info(f"[VIZ DECISION]   → SCATTER PLOT (correlation)")
            return 'scatter', {
                'title': f'{numeric_cols[0]} vs {numeric_cols[1]}',
                'x_column': numeric_cols[0],
                'y_column': numeric_cols[1]
            }
        
        # FALLBACK: Default to bar chart
        master_logger.warning(f"[VIZ DECISION] ⚠️ FALLBACK: No specific rule matched")
        master_logger.warning(f"[VIZ DECISION]   → BAR CHART (default)")
        
        if len(categorical_cols) > 0 and len(numeric_cols) > 0:
            return 'bar', {
                'title': f'{numeric_cols[0]} by {categorical_cols[0]}',
                'x_column': categorical_cols[0],
                'y_column': numeric_cols[0]
            }
        else:
            # Last resort
            return 'bar', {
                'title': 'Data visualization',
                'x_column': df.columns[0],
                'y_column': df.columns[-1]
            }
    
    def _generate_chart(self, chart_type: str, df: pd.DataFrame, config: Dict, query: str) -> Dict:
        """
        Generate the actual chart image with dynamic sizing based on data characteristics
        """
        master_logger.info(f"[VIZ] _generate_chart called with chart_type: {chart_type}")
        master_logger.info(f"[VIZ] Config: {config}")
        
        try:
            # ========== ADAPTIVE FIGURE SIZING ==========
            # Calculate optimal figure size based on data characteristics (NO HARDCODING)
            num_rows = len(df)
            
            # Determine if this is a categorical chart (bar/pie)
            is_categorical_chart = chart_type in ['bar', 'pie']
            
            if is_categorical_chart and num_rows > 0:
                # Calculate space needed per category
                # Base calculation: minimum space per item + scaling factor
                min_space_per_item = 0.3  # Minimum inches per category
                base_size = 6  # Base size for small datasets
                
                # Adaptive sizing: grows with number of categories but with diminishing returns
                calculated_size = base_size + (num_rows * min_space_per_item)
                
                # For many categories (>15), use horizontal orientation for better readability
                # Decision point: when vertical bars become too cramped
                use_horizontal = num_rows > 15
                
                if use_horizontal:
                    # Horizontal bar chart: height grows with categories, width stays reasonable
                    fig_width = 10  # Standard width for horizontal bars
                    fig_height = max(base_size, calculated_size)  # Height scales with categories
                    master_logger.info(f"[VIZ] Using HORIZONTAL bar chart for {num_rows} categories")
                    master_logger.info(f"[VIZ] Calculated height: {fig_height:.1f} inches ({min_space_per_item} inches per category)")
                else:
                    # Vertical bar chart: width grows with categories, height stays reasonable
                    fig_width = max(10, calculated_size)  # Width scales with categories
                    fig_height = 6  # Standard height for vertical bars
                    master_logger.info(f"[VIZ] Using VERTICAL bar chart for {num_rows} categories")
                    master_logger.info(f"[VIZ] Calculated width: {fig_width:.1f} inches")
                
                figsize = (fig_width, fig_height)
                orientation = 'horizontal' if use_horizontal else 'vertical'
            else:
                # Default size for non-categorical charts
                figsize = (10, 6)
                orientation = 'vertical'
            
            master_logger.info(f"[VIZ] Creating figure with size: {figsize}, orientation: {orientation}")
            plt.figure(figsize=figsize)
            # ========== END ADAPTIVE SIZING ==========
            
            if chart_type == 'bar':
                x_col = config.get('x_column')
                y_col = config.get('y_column')
                
                if x_col in df.columns and y_col in df.columns:
                    # DEFENSIVE CHECK: Validate y_col is numeric for bar chart
                    # Bar charts require numeric y-axis values to plot heights
                    if not pd.api.types.is_numeric_dtype(df[y_col]):
                        master_logger.error(f"[VIZ] Cannot create bar chart: y_column '{y_col}' is not numeric (dtype: {df[y_col].dtype})")
                        master_logger.error(f"[VIZ] Bar charts require numeric y-axis values. Skipping chart generation.")
                        plt.close()
                        return {
                            'image_base64': None, 
                            'description': f'Cannot generate bar chart: {y_col} contains non-numeric data'
                        }
                    
                    # Sort datetime columns for proper ordering
                    if pd.api.types.is_datetime64_any_dtype(df[x_col]):
                        plot_df = df.sort_values(x_col)
                        if orientation == 'horizontal':
                            # Reverse order for horizontal bars so largest appears at top
                            plot_df = plot_df.iloc[::-1]
                            plt.barh(range(len(plot_df)), plot_df[y_col])
                            plt.yticks(range(len(plot_df)), plot_df[x_col].dt.strftime('%Y-%m-%d'))
                            # Set tight Y-axis limits to remove extra top/bottom space
                            plt.ylim(-0.5, len(plot_df) - 0.5)
                            plt.ylabel(x_col)
                            plt.xlabel(y_col)
                        else:
                            plt.bar(range(len(plot_df)), plot_df[y_col])
                            plt.xticks(range(len(plot_df)), plot_df[x_col].dt.strftime('%Y-%m-%d'), rotation=45, ha='right')
                            plt.xlabel(x_col)
                            plt.ylabel(y_col)
                    else:
                        if orientation == 'horizontal':
                            # Horizontal bar chart - reverse order so largest appears at top
                            plot_df = df.iloc[::-1]
                            plt.barh(plot_df[x_col], plot_df[y_col])
                            # Set tight Y-axis limits to remove extra top/bottom space
                            plt.ylim(-0.5, len(plot_df) - 0.5)
                            plt.ylabel(x_col)
                            plt.xlabel(y_col)
                        else:
                            # Vertical bar chart
                            plt.bar(df[x_col], df[y_col])
                            plt.xticks(rotation=45, ha='right')
                            plt.xlabel(x_col)
                            plt.ylabel(y_col)
                    
                elif y_col in df.columns and x_col not in df.columns:
                    # Handle case where x_col references index (e.g., 'index' or named index)
                    master_logger.info(f"[VIZ] x_column '{x_col}' not in columns, using index")
                    
                    # DEFENSIVE CHECK: Validate y_col is numeric for bar chart
                    if not pd.api.types.is_numeric_dtype(df[y_col]):
                        master_logger.error(f"[VIZ] Cannot create bar chart: y_column '{y_col}' is not numeric (dtype: {df[y_col].dtype})")
                        master_logger.error(f"[VIZ] Bar charts require numeric y-axis values. Skipping chart generation.")
                        plt.close()
                        return {
                            'image_base64': None, 
                            'description': f'Cannot generate bar chart: {y_col} contains non-numeric data'
                        }
                    
                    x_values = df.index
                    y_values = df[y_col]
                    
                    if orientation == 'horizontal':
                        # Horizontal bar chart with index - reverse order so largest appears at top
                        plot_df = df.iloc[::-1]
                        x_values = plot_df.index
                        y_values = plot_df[y_col]
                        plt.barh(range(len(y_values)), y_values)
                        plt.yticks(range(len(y_values)), x_values)
                        # Set tight Y-axis limits to remove extra top/bottom space
                        plt.ylim(-0.5, len(y_values) - 0.5)
                        plt.ylabel(x_col)
                        plt.xlabel(y_col)
                    else:
                        # Vertical bar chart with index
                        plt.bar(range(len(y_values)), y_values)
                        plt.xticks(range(len(y_values)), x_values, rotation=45, ha='right')
                        plt.xlabel(x_col)
                        plt.ylabel(y_col)
                else:
                    master_logger.error(f"[VIZ] Could not find columns for bar chart: x={x_col}, y={y_col}")
                    master_logger.error(f"[VIZ] Available columns: {df.columns.tolist()}")
                
            elif chart_type == 'histogram':
                col = config.get('column')
                if col in df.columns:
                    plt.hist(df[col].dropna(), bins=30)
                    plt.xlabel(col)
                    plt.ylabel('Frequency')
                
            elif chart_type == 'line':
                x_col = config.get('x_column')
                y_col = config.get('y_column')
                
                # Handle both column-based and index-based x-axis
                if x_col in df.columns and y_col in df.columns:
                    # Format datetime x-axis if applicable
                    x_values = df[x_col]
                    if pd.api.types.is_datetime64_any_dtype(x_values):
                        # Sort by datetime for proper line chart
                        plot_df = df.sort_values(x_col)
                        plt.plot(plot_df[x_col], plot_df[y_col], marker='o', linewidth=2, markersize=8)
                        plt.gcf().autofmt_xdate()  # Auto-format datetime labels
                    else:
                        plt.plot(df[x_col], df[y_col], marker='o', linewidth=2, markersize=8)
                    
                    plt.xlabel(x_col)
                    plt.ylabel(y_col)
                    plt.grid(True, alpha=0.3)
                    
                elif y_col in df.columns:
                    # Use index as x-axis (common for aggregated data where categorical was lost)
                    x_values = df.index
                    y_values = df[y_col]
                    
                    plt.plot(range(len(y_values)), y_values, marker='o', linewidth=2, markersize=8)
                    plt.xticks(range(len(y_values)), x_values, rotation=45, ha='right')
                    plt.xlabel(x_col)
                    plt.ylabel(y_col)
                    plt.grid(True, alpha=0.3)
                else:
                    master_logger.error(f"[VIZ] Could not find columns for line chart: x={x_col}, y={y_col}")
                
            elif chart_type == 'scatter':
                x_col = config.get('x_column')
                y_col = config.get('y_column')
                
                if x_col in df.columns and y_col in df.columns:
                    plt.scatter(df[x_col], df[y_col])
                    plt.xlabel(x_col)
                    plt.ylabel(y_col)
                
            elif chart_type == 'pie':
                labels_col = config.get('labels')
                values_col = config.get('values')
                
                if labels_col in df.columns and values_col in df.columns:
                    plt.pie(df[values_col], labels=df[labels_col], autopct='%1.1f%%')
            
            elif chart_type == 'heatmap':
                x_col = config.get('x_column')
                y_col = config.get('y_column')
                value_col = config.get('value_column')
                
                master_logger.info(f"[VIZ HEATMAP] Creating heatmap for {x_col} × {y_col}")
                master_logger.info(f"[VIZ HEATMAP] Value column: {value_col}")
                
                if x_col in df.columns and y_col in df.columns and value_col in df.columns:
                    # Create pivot table for heatmap
                    pivot_df = df.pivot_table(
                        index=y_col, 
                        columns=x_col, 
                        values=value_col, 
                        aggfunc='mean'  # Use mean if there are duplicates
                    )
                    
                    master_logger.info(f"[VIZ HEATMAP] Pivot table shape: {pivot_df.shape}")
                    master_logger.info(f"[VIZ HEATMAP] Rows (y-axis): {len(pivot_df)}")
                    master_logger.info(f"[VIZ HEATMAP] Columns (x-axis): {len(pivot_df.columns)}")
                    
                    # Adaptive sizing for heatmap
                    num_x_categories = len(pivot_df.columns)
                    num_y_categories = len(pivot_df.index)
                    
                    # Calculate optimal size: 0.5 inches per cell (minimum)
                    heatmap_width = max(8, num_x_categories * 0.6)
                    heatmap_height = max(6, num_y_categories * 0.5)
                    
                    master_logger.info(f"[VIZ HEATMAP] Calculated size: {heatmap_width:.1f}w × {heatmap_height:.1f}h inches")
                    
                    # Close previous figure and create new one with correct size
                    plt.close()
                    plt.figure(figsize=(heatmap_width, heatmap_height))
                    
                    # Create heatmap
                    sns.heatmap(
                        pivot_df, 
                        annot=True,  # Show values in cells
                        fmt='.1f',   # Format numbers to 1 decimal place
                        cmap='YlOrRd',  # Yellow-Orange-Red color scheme
                        cbar_kws={'label': value_col},
                        linewidths=0.5,
                        linecolor='white'
                    )
                    
                    plt.xlabel(x_col)
                    plt.ylabel(y_col)
                    plt.xticks(rotation=45, ha='right')
                    plt.yticks(rotation=0)
                    
                    master_logger.info(f"[VIZ HEATMAP] ✓ Heatmap created successfully")
            
            plt.title(config.get('title', 'Chart'))
            plt.tight_layout()
            
            # Convert to base64
            buffer = io.BytesIO()
            
            # Conditional savefig: skip bbox_inches='tight' for horizontal charts
            # to preserve our ylim() settings and avoid extra padding
            if orientation == 'horizontal':
                plt.savefig(buffer, format='png', dpi=100)
                master_logger.info("[VIZ] Saved without bbox_inches='tight' for horizontal chart (preserves ylim)")
            else:
                plt.savefig(buffer, format='png', dpi=100, bbox_inches='tight')
            
            buffer.seek(0)
            image_base64 = base64.b64encode(buffer.read()).decode()
            plt.close()
            
            return {
                'image_base64': image_base64,
                'description': config.get('title', 'Chart')
            }
            
        except Exception as e:
            master_logger.error(f"[VIZ] Error generating chart: {e}")
            master_logger.error(f"[VIZ] Chart type: {chart_type}")
            master_logger.error(f"[VIZ] Config: {config}")
            master_logger.error(f"[VIZ] DataFrame columns: {df.columns.tolist()}")
            import traceback
            master_logger.error(f"[VIZ] Traceback: {traceback.format_exc()}")
            plt.close()
            return {'image_base64': None, 'description': 'Error generating chart'}
    
    def _generate_data_insights(self, df: pd.DataFrame, chart_type: str) -> List[str]:
        """
        Generate insights about the data
        """
        insights = []
        
        try:
            insights.append(f"Dataset contains {len(df)} records")
            
            numeric_cols = df.select_dtypes(include=['number']).columns
            if len(numeric_cols) > 0:
                for col in numeric_cols[:2]:  # Top 2 numeric columns
                    mean_val = df[col].mean()
                    max_val = df[col].max()
                    insights.append(f"{col}: mean={mean_val:.2f}, max={max_val:.2f}")
        
        except Exception as e:
            master_logger.warning(f"[VIZ] Could not generate insights: {e}")
        
        return insights