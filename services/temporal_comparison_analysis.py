"""
Temporal Comparison Analyzer - Delta-Based Causal Analysis

NO HARDCODING:
- Identifies features dynamically from data
- Calculates contributions based on actual distributions
- Ranks drivers by data-driven metrics
- Generates explanations using actual values

Author: Temporal Comparison Analysis Module
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Any, Tuple, Optional
import logging
import json


class TemporalComparisonAnalyzer:
    """
    Analyzes what drove the change between two time periods
    
    Unlike general causal analysis (which analyzes all data),
    this specifically compares two periods to explain the delta
    """
    
    def __init__(self, df: pd.DataFrame, y_column: str,
                 period1_filter: pd.Series, period2_filter: pd.Series,
                 period1_label: str, period2_label: str,
                 llm_client, logger=None,
                 date_column: str = None, aggregation_method: str = None,
                 chart_name: str = None, unified_drivers: List[Dict[str, Any]] = None,
                 smart_agg_decider=None):
        """
        Initialize analyzer
        
        Args:
            df: Full DataFrame
            y_column: Target metric column
            period1_filter: Boolean filter for period 1 (comparison period)
            period2_filter: Boolean filter for period 2 (target period)
            period1_label: Human-readable label for period 1
            period2_label: Human-readable label for period 2
            llm_client: OpenAI client for explanation generation
            logger: Logger instance
            date_column: Date column for grouping (optional, for pre-aggregated data)
            aggregation_method: Aggregation method to apply (COUNT_DISTINCT, SUM, AVG, etc.)
            chart_name: Chart name for loading cached causal features (optional)
            unified_drivers: Pre-calculated drivers (optional, for multi-metric consistency)
            smart_agg_decider: Smart aggregation service for feature-specific aggregation decisions
        """
        self.df = df
        self.y_column = y_column
        self.period1_filter = period1_filter
        self.period2_filter = period2_filter
        self.period1_label = period1_label
        self.period2_label = period2_label
        self.llm_client = llm_client
        self.logger = logger or logging.getLogger(__name__)
        self.date_column = date_column
        self.aggregation_method = aggregation_method
        self.chart_name = chart_name
        self.unified_drivers = unified_drivers
        self.smart_agg_decider = smart_agg_decider
        
        self.df_p1 = df[period1_filter]
        self.df_p2 = df[period2_filter]
    
    def analyze(self) -> Dict[str, Any]:
        """
        Main analysis method - identify what drove the change
        
        Returns:
            Analysis results with drivers and explanation
        """
        try:
            self.logger.info("="*80)
            self.logger.info("=== TEMPORAL COMPARISON ANALYSIS ===")
            self.logger.info(f"Comparing: {self.period2_label} vs {self.period1_label}")
            self.logger.info(f"Period 1 records: {len(self.df_p1)}")
            self.logger.info(f"Period 2 records: {len(self.df_p2)}")
            self.logger.info("="*80)
            
            # Step 1: Calculate delta metrics
            delta_metrics = self._calculate_delta()
            
            self.logger.info(f"Delta: {delta_metrics['delta']:+,.2f} ({delta_metrics['delta_pct']:+.1f}%)")
            
            # Step 2 & 3: Use unified drivers if provided, otherwise calculate them
            if self.unified_drivers is not None:
                self.logger.info(f"[UNIFIED_DRIVERS] Using pre-calculated drivers ({len(self.unified_drivers)} drivers)")
                drivers = self.unified_drivers
            else:
                self.logger.info("[UNIFIED_DRIVERS] No pre-calculated drivers, calculating now")
                # Get categorical features from causal cache or fall back to dynamic identification
                categorical_features = self._get_features_from_cache()
                
                self.logger.info(f"Analyzing {len(categorical_features)} categorical features for drivers")
                
                # Calculate contribution for each feature
                drivers = self._identify_drivers(categorical_features, delta_metrics['delta'])
            
            # Step 4: Generate natural language explanation
            # OPTIMIZATION: Commented out - this explanation is never displayed in UI (wasteful LLM call)
            # explanation = self._generate_explanation(delta_metrics, drivers)
            explanation = ""  # Empty string - not used in final output
            
            self.logger.info(f"✓ Analysis complete - identified {len(drivers)} top drivers")
            
            return {
                'success': True,
                'analysis_type': 'temporal_comparison',
                'comparison_context': f"{self.period2_label} vs {self.period1_label}",
                'delta_metrics': delta_metrics,
                'top_drivers': drivers,
                'explanation': explanation,
                'period1_label': self.period1_label,
                'period2_label': self.period2_label
            }
            
        except Exception as e:
            self.logger.error(f"Temporal comparison analysis failed: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return {
                'success': False,
                'error': str(e)
            }
    
    def _calculate_delta(self) -> Dict[str, Any]:
        """
        Calculate metric change between periods
        """
        # NEW: If date_column and aggregation_method provided, use proper groupby aggregation
        if self.date_column and self.aggregation_method:
            value_p1, value_p2, metric_type = self._calculate_with_aggregation()
        else:
            # LEGACY: Detect if this is a count column or value column
            is_count_column = self._is_count_column()
            
            if is_count_column:
                # Count records
                value_p1 = len(self.df_p1)
                value_p2 = len(self.df_p2)
                metric_type = 'count'
            else:
                # Sum values
                value_p1 = self.df_p1[self.y_column].sum()
                value_p2 = self.df_p2[self.y_column].sum()
                metric_type = 'sum'
        
        # Calculate delta
        delta = value_p2 - value_p1
        delta_pct = (delta / value_p1 * 100) if value_p1 > 0 else 0
        direction = 'increase' if delta > 0 else 'decrease' if delta < 0 else 'no_change'
        
        return {
            'metric': self.y_column,
            'metric_type': metric_type,
            'period1_value': float(value_p1),
            'period2_value': float(value_p2),
            'delta': float(delta),
            'delta_pct': float(delta_pct),
            'direction': direction
        }
    
    def _calculate_with_aggregation(self) -> Tuple[float, float, str]:
        """
        Calculate metric values using proper groupby aggregation
        
        This is used when we have pre-aggregated data (like Closed_Volume)
        that needs to be grouped by date before aggregating
        
        Returns:
            Tuple of (period1_value, period2_value, metric_type)
        """
        try:
            agg_method = self.aggregation_method.upper() if self.aggregation_method else 'COUNT_DISTINCT'
            
            # Map aggregation methods to pandas functions
            if agg_method in ['COUNT_DISTINCT', 'COUNT', 'NUNIQUE']:
                # Group by date, count distinct/unique values
                grouped_p1 = self.df_p1.groupby(self.date_column)[self.y_column].nunique()
                grouped_p2 = self.df_p2.groupby(self.date_column)[self.y_column].nunique()
                metric_type = 'count_distinct'
            elif agg_method == 'SUM':
                # Group by date, sum values
                grouped_p1 = self.df_p1.groupby(self.date_column)[self.y_column].sum()
                grouped_p2 = self.df_p2.groupby(self.date_column)[self.y_column].sum()
                metric_type = 'sum'
            elif agg_method in ['AVG', 'AVERAGE', 'MEAN']:
                # Group by date, average values
                grouped_p1 = self.df_p1.groupby(self.date_column)[self.y_column].mean()
                grouped_p2 = self.df_p2.groupby(self.date_column)[self.y_column].mean()
                metric_type = 'average'
            else:
                # Default to count
                grouped_p1 = self.df_p1.groupby(self.date_column)[self.y_column].count()
                grouped_p2 = self.df_p2.groupby(self.date_column)[self.y_column].count()
                metric_type = 'count'
            
            # Extract the single value (should be one value per period after grouping)
            value_p1 = grouped_p1.iloc[0] if len(grouped_p1) > 0 else 0
            value_p2 = grouped_p2.iloc[0] if len(grouped_p2) > 0 else 0
            
            self.logger.info(f"[AGGREGATION] Method={agg_method}, P1={value_p1}, P2={value_p2}")
            
            return float(value_p1), float(value_p2), metric_type
            
        except Exception as e:
            self.logger.error(f"Aggregation calculation failed: {e}, falling back to legacy method")
            # Fallback to legacy behavior
            if self._is_count_column():
                return float(len(self.df_p1)), float(len(self.df_p2)), 'count'
            else:
                return float(self.df_p1[self.y_column].sum()), float(self.df_p2[self.y_column].sum()), 'sum'
    
    def _is_count_column(self) -> bool:
        """
        Detect if column is for counting (high cardinality ID) or summing (measure)
        """
        try:
            unique_ratio = self.df[self.y_column].nunique() / len(self.df)
            
            # If >90% unique values, it's likely an ID column for counting
            if unique_ratio > 0.9:
                return True
            
            # If column name suggests it's for counting
            col_lower = self.y_column.lower()
            if any(word in col_lower for word in ['id', 'number', 'count', 'ticket']):
                return True
            
            return False
        
        except:
            return False
    
    def _fallback_aggregation_for_feature(self, feature: str) -> str:
        """
        Fallback method to determine aggregation for a feature when smart_agg_decider is unavailable
        
        Returns:
            Aggregation method string (COUNT, SUM, etc.)
        """
        try:
            # Check if column is categorical (object type)
            if pd.api.types.is_object_dtype(self.df[feature]):
                return 'COUNT'
            
            # Check if numeric but with low cardinality (likely categorical)
            if pd.api.types.is_integer_dtype(self.df[feature]) or pd.api.types.is_float_dtype(self.df[feature]):
                unique_ratio = self.df[feature].nunique() / len(self.df)
                if unique_ratio < 0.05:  # Less than 5% unique = categorical
                    return 'COUNT'
                else:
                    # Check column name patterns
                    col_lower = feature.lower()
                    if any(pattern in col_lower for pattern in ['count', 'num', 'number']):
                        return 'SUM'
                    elif any(pattern in col_lower for pattern in ['avg', 'average', 'mean']):
                        return 'AVG'
                    else:
                        return 'SUM'  # Default for numeric
            
            # Default to COUNT for unknown types
            return 'COUNT'
        
        except Exception as e:
            self.logger.warning(f"Error in fallback aggregation for {feature}: {e}")
            return 'COUNT'
    
    def _get_features_from_cache(self) -> List[str]:
        """
        Load top features from causal_analysis_cache.json if available
        Supports both nested (workbook_id/chart_name) and flat (chart_name) structures
        Falls back to dynamic identification if cache not found
        """
        import os
        
        # Try to load from cache
        cache_file = "causal_analysis_cache.json"
        
        if self.chart_name and os.path.exists(cache_file):
            try:
                with open(cache_file, 'r') as f:
                    cache_data = json.load(f)
                
                # Try to find cached features (support both nested and flat structures)
                cached_features = None
                
                # NEW: Try nested structure first (workbook_id/chart_name)
                # Look through all workbooks to find our chart
                for workbook_id, workbook_charts in cache_data.items():
                    if isinstance(workbook_charts, dict) and self.chart_name in workbook_charts:
                        if "top_5_features" in workbook_charts[self.chart_name]:
                            cached_features = workbook_charts[self.chart_name]["top_5_features"]
                            self.logger.info(f"[CACHE] Found features in nested structure: {workbook_id}/{self.chart_name}")
                            break
                
                # FALLBACK: Try flat structure (for backward compatibility)
                if not cached_features and self.chart_name in cache_data:
                    if "top_5_features" in cache_data[self.chart_name]:
                        cached_features = cache_data[self.chart_name]["top_5_features"]
                        self.logger.info(f"[CACHE] Found features in flat structure: {self.chart_name}")
                
                if cached_features:
                    # Filter out date/time columns and validate features exist in dataframe
                    valid_features = []
                    for feature in cached_features:
                        if feature not in self.df.columns:
                            self.logger.warning(f"[CACHE] Feature '{feature}' not in dataframe, skipping")
                            continue
                        
                        # Skip date/time columns
                        col_lower = feature.lower()
                        if any(pattern in col_lower for pattern in ['date', 'time', 'timestamp', 'day', 'week', 'month', 'year', 'quarter']):
                            self.logger.info(f"[CACHE] Skipping date/time feature: {feature}")
                            continue
                        
                        # Skip datetime dtype columns
                        if pd.api.types.is_datetime64_any_dtype(self.df[feature]):
                            self.logger.info(f"[CACHE] Skipping datetime dtype feature: {feature}")
                            continue
                        
                        valid_features.append(feature)
                    
                    if valid_features:
                        self.logger.info(f"[CACHE] Loaded {len(valid_features)} features from cache for '{self.chart_name}': {valid_features}")
                        return valid_features
                    else:
                        self.logger.warning(f"[CACHE] No valid features found in cache for '{self.chart_name}', falling back to dynamic")
                else:
                    self.logger.warning(f"[CACHE] Chart '{self.chart_name}' not found in cache, falling back to dynamic")
            except Exception as e:
                self.logger.error(f"[CACHE] Error loading cache: {e}, falling back to dynamic")
        else:
            if not self.chart_name:
                self.logger.info("[CACHE] No chart_name provided, using dynamic feature identification")
            else:
                self.logger.info(f"[CACHE] Cache file not found at '{cache_file}', using dynamic feature identification")
        
        # Fall back to dynamic identification
        return self._identify_categorical_features()
    
    def _identify_categorical_features(self) -> List[str]:
        """
        Identify categorical features dynamically - NO HARDCODING
        """
        categorical_features = []
        
        for col in self.df.columns:
            # Skip the target metric
            if col == self.y_column:
                continue
            
            # Skip date/time columns
            if pd.api.types.is_datetime64_any_dtype(self.df[col]):
                continue
            
            # Skip if column name suggests it's a date
            col_lower = col.lower()
            if any(word in col_lower for word in ['date', 'time', 'datetime', 'timestamp']):
                continue
            
            # Check if categorical (object type or low cardinality numeric)
            if pd.api.types.is_object_dtype(self.df[col]):
                categorical_features.append(col)
            elif pd.api.types.is_integer_dtype(self.df[col]) or pd.api.types.is_float_dtype(self.df[col]):
                # Check cardinality
                unique_ratio = self.df[col].nunique() / len(self.df)
                if unique_ratio < 0.05:  # Less than 5% unique values = categorical
                    categorical_features.append(col)
        
        self.logger.info(f"Identified {len(categorical_features)} categorical features")
        
        return categorical_features
    
    def _identify_drivers(self, categorical_features: List[str], 
                         total_delta: float) -> List[Dict[str, Any]]:
        """
        Calculate contribution of each feature to the delta
        """
        contributions = []
        
        for feature in categorical_features:
            try:
                contribution = self._calculate_feature_contribution(feature, total_delta)
                
                if contribution is not None:
                    contributions.append({
                        'feature': feature,
                        'contribution_score': contribution['score'],
                        'top_category_changes': contribution['top_changes'][:10],  # Top 10 categories (ensures 5+ positive and 5+ negative available)
                        'num_categories': contribution['num_categories'],
                        'contribution_pct': contribution['contribution_pct']
                    })
            
            except Exception as e:
                self.logger.warning(f"Failed to calculate contribution for {feature}: {e}")
                continue
        
        # Rank by absolute contribution score
        contributions.sort(key=lambda x: abs(x['contribution_score']), reverse=True)
        
        # Return top 5 drivers
        return contributions[:5]
    
    def _calculate_feature_contribution(self, feature: str, 
                                       total_delta: float) -> Optional[Dict[str, Any]]:
        """
        Calculate how a feature contributed to the delta
        
        Logic:
        - Get distribution of categories in both periods
        - Calculate how each category's contribution changed
        - Sum absolute changes as contribution score
        """
        # NEW: Call smart aggregation for THIS specific feature
        if self.smart_agg_decider:
            try:
                decision = self.smart_agg_decider.decide_aggregation(
                    query="temporal comparison drivers",
                    column=feature,
                    df=self.df
                )
                feature_agg_method = decision['aggregation'].upper()
            except Exception as e:
                self.logger.warning(f"[FEATURE_AGG] Failed to get aggregation for {feature}: {e}, using fallback")
                feature_agg_method = self._fallback_aggregation_for_feature(feature)
        else:
            # Fallback if no smart_agg_decider provided
            feature_agg_method = self._fallback_aggregation_for_feature(feature)
        
        # Get distributions based on feature's aggregation method
        if feature_agg_method in ['COUNT', 'COUNT_DISTINCT', 'NUNIQUE']:
            # Count records per category
            dist_p1 = self.df_p1[feature].value_counts()
            dist_p2 = self.df_p2[feature].value_counts()
        elif feature_agg_method in ['SUM', 'AVG', 'MEAN', 'AVERAGE']:
            # Convert string values to numeric first
            try:
                df_p1_copy = self.df_p1.copy()
                df_p2_copy = self.df_p2.copy()
                
                df_p1_copy[f'{feature}_numeric'] = pd.to_numeric(df_p1_copy[feature], errors='coerce')
                df_p2_copy[f'{feature}_numeric'] = pd.to_numeric(df_p2_copy[feature], errors='coerce')
                
                if feature_agg_method == 'SUM':
                    dist_p1 = df_p1_copy.groupby(feature)[f'{feature}_numeric'].sum()
                    dist_p2 = df_p2_copy.groupby(feature)[f'{feature}_numeric'].sum()
                else:  # AVG/MEAN
                    dist_p1 = df_p1_copy.groupby(feature)[f'{feature}_numeric'].mean()
                    dist_p2 = df_p2_copy.groupby(feature)[f'{feature}_numeric'].mean()
            except Exception as e:
                self.logger.warning(f"Failed to aggregate {feature} with {feature_agg_method}: {e}, falling back to count")
                dist_p1 = self.df_p1[feature].value_counts()
                dist_p2 = self.df_p2[feature].value_counts()
        else:
            # Default to count
            dist_p1 = self.df_p1[feature].value_counts()
            dist_p2 = self.df_p2[feature].value_counts()
        
        # Get all categories
        all_categories = set(dist_p1.index) | set(dist_p2.index)
        
        # Skip if too many categories (>50)
        if len(all_categories) > 50:
            return None
        
        # Calculate shifts for each category
        category_shifts = []
        
        for category in all_categories:
            val_p1 = dist_p1.get(category, 0)
            val_p2 = dist_p2.get(category, 0)
            shift = val_p2 - val_p1
            
            if val_p1 > 0:
                shift_pct = (shift / val_p1) * 100
            else:
                shift_pct = float('inf') if shift > 0 else 0
            
            category_shifts.append({
                'category': str(category),
                'period1_value': float(val_p1),
                'period2_value': float(val_p2),
                'shift': float(shift),
                'shift_pct': float(shift_pct) if shift_pct != float('inf') else 999.0
            })
        
        # Sort by absolute shift
        category_shifts.sort(key=lambda x: abs(x['shift']), reverse=True)
        
        # Calculate total contribution (sum of absolute shifts)
        total_contribution = sum(abs(c['shift']) for c in category_shifts)
        
        # Contribution as percentage of total delta
        contribution_pct = (total_contribution / abs(total_delta) * 100) if total_delta != 0 else 0
        
        return {
            'score': total_contribution,
            'contribution_pct': float(contribution_pct),
            'top_changes': category_shifts,
            'num_categories': len(all_categories),
            'aggregation_method': feature_agg_method  # Include for debugging
        }
    
    def _generate_explanation(self, delta_metrics: Dict[str, Any],
                             drivers: List[Dict[str, Any]]) -> str:
        """
        Generate natural language explanation using GPT-4o
        """
        # Format drivers for LLM
        drivers_text = self._format_drivers_for_llm(drivers)
        
        prompt = f"""
Generate a clear, actionable explanation for this temporal comparison analysis.

COMPARISON: {self.period2_label} vs {self.period1_label}

METRIC: {delta_metrics['metric']}
- {self.period1_label}: {delta_metrics['period1_value']:,.0f}
- {self.period2_label}: {delta_metrics['period2_value']:,.0f}
- Change: {delta_metrics['delta']:+,.0f} ({delta_metrics['delta_pct']:+.1f}%)
- Direction: {delta_metrics['direction']}

TOP DRIVERS OF CHANGE:
{drivers_text}

INSTRUCTIONS:
Generate a 2-4 sentence explanation that:
1. States what happened (spike/dip and magnitude)
2. Identifies the primary driver with specific numbers
3. Mentions 1-2 specific categories that changed significantly
4. Is actionable and specific (use actual numbers from data)

Be concise but informative. Focus on the most impactful changes.

Example good output:
"{self.period2_label} saw a 15.6% decline compared to {self.period1_label}, dropping from 450 to 380 tickets. 
The primary driver was a shift in product distribution, with Product A decreasing by 50 tickets (-30%) while Product B remained stable. 
Additionally, the North region contributed to the decline with 25 fewer tickets (-18%)."
"""
        
        try:
            response = self.llm_client.chat.completions.create(
                model="gpt-4o",  # Use full GPT-4o for quality explanation
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,  # Slightly creative but mostly factual
                max_tokens=300
            )
            
            explanation = response.choices[0].message.content
            self.logger.info("✓ Generated explanation with GPT-4o")
            
            # Append structured driver list
            driver_list = self._format_driver_bullet_list(drivers, delta_metrics['direction'])
            full_explanation = f"{explanation}\n\n{driver_list}"
            
            return full_explanation
        
        except Exception as e:
            self.logger.error(f"Failed to generate explanation: {e}")
            
            # Fallback: template-based explanation
            direction_word = "increased" if delta_metrics['delta'] > 0 else "decreased"
            
            top_driver = drivers[0] if drivers else None
            
            if top_driver:
                top_category = top_driver['top_category_changes'][0]
                fallback = (f"{self.period2_label} {direction_word} by {abs(delta_metrics['delta_pct']):.1f}% "
                           f"compared to {self.period1_label}. "
                           f"The primary driver was {top_driver['feature']}, particularly "
                           f"{top_category['category']} which changed by {top_category['shift']:+.0f}.")
            else:
                fallback = (f"{self.period2_label} {direction_word} by {abs(delta_metrics['delta_pct']):.1f}% "
                           f"compared to {self.period1_label}.")
            
            # Append structured driver list even in fallback
            driver_list = self._format_driver_bullet_list(drivers, delta_metrics['direction'])
            return f"{fallback}\n\n{driver_list}"
    
    def _format_drivers_for_llm(self, drivers: List[Dict[str, Any]]) -> str:
        """
        Format drivers into readable text for LLM
        """
        formatted = []
        
        for i, driver in enumerate(drivers, 1):
            feature_name = driver['feature']
            top_changes = driver['top_category_changes']
            
            # Format top 2 category changes
            changes_text = []
            for change in top_changes[:2]:
                cat = change['category']
                shift = change['shift']
                shift_pct = change['shift_pct']
                
                if abs(shift_pct) < 999:  # Not infinity
                    changes_text.append(f"  - {cat}: {shift:+,.0f} ({shift_pct:+.1f}%)")
                else:
                    changes_text.append(f"  - {cat}: {shift:+,.0f} (new/removed)")
            
            formatted.append(
                f"{i}. {feature_name} (contribution: {driver['contribution_pct']:.1f}%)\n"
                + "\n".join(changes_text)
            )
        
        return "\n\n".join(formatted)
    
    def _format_driver_bullet_list(self, drivers: List[Dict[str, Any]], direction: str) -> str:
        """
        Format drivers as a structured bullet list
        
        Args:
            drivers: List of driver dictionaries
            direction: 'increase' or 'decrease' to determine whether to show positive or negative changes
        
        Returns:
            Formatted bullet list string
        """
        if not drivers:
            return ""
        
        header = "**Top Drivers:**"
        bullet_list = [header]
        
        for i, driver in enumerate(drivers[:5], 1):  # Show top 5 drivers
            feature_name = driver['feature']
            all_changes = driver['top_category_changes']
            
            # Main driver line (no contribution percentage)
            bullet_list.append(f"{i}. **{feature_name}**")
            
            # Filter changes based on direction
            if direction == 'increase':
                # For spike, show top 5 positive changes
                positive_changes = [c for c in all_changes if c['shift'] > 0]
                positive_changes.sort(key=lambda x: x['shift'], reverse=True)
                relevant_changes = positive_changes[:5]
            else:
                # For dip, show top 5 negative changes
                negative_changes = [c for c in all_changes if c['shift'] < 0]
                negative_changes.sort(key=lambda x: x['shift'])  # Sort ascending (most negative first)
                relevant_changes = negative_changes[:5]
            
            # Show the changes
            for change in relevant_changes:
                cat = change['category']
                shift = change['shift']
                shift_pct = change['shift_pct']
                
                if abs(shift_pct) < 999:  # Not infinity
                    bullet_list.append(f"   - {cat}: {shift:+,.0f} ({shift_pct:+.1f}%)")
                else:
                    bullet_list.append(f"   - {cat}: {shift:+,.0f}")
        
        return "\n".join(bullet_list)


def analyze_temporal_comparison(df: pd.DataFrame, y_column: str,
                                period1_filter: pd.Series, period2_filter: pd.Series,
                                period1_label: str, period2_label: str,
                                llm_client, logger=None) -> Dict[str, Any]:
    """
    Convenience function for temporal comparison analysis
    
    Args:
        df: Full DataFrame
        y_column: Target metric column
        period1_filter: Boolean filter for period 1
        period2_filter: Boolean filter for period 2
        period1_label: Label for period 1
        period2_label: Label for period 2
        llm_client: OpenAI client
        logger: Logger instance
    
    Returns:
        Analysis results dictionary
    """
    analyzer = TemporalComparisonAnalyzer(
        df=df,
        y_column=y_column,
        period1_filter=period1_filter,
        period2_filter=period2_filter,
        period1_label=period1_label,
        period2_label=period2_label,
        llm_client=llm_client,
        logger=logger
    )
    
    return analyzer.analyze()