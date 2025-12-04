"""
Response Template Engine for Data Exploration Service

Provides templated, user-friendly responses for different analysis operations
while maintaining markdown table formatting as fallback.
"""

import pandas as pd
import polars as pl
import numpy as np
from typing import Dict, Any, Optional, Tuple, List, Union
import re
from master_logger import setup_module_logger

master_logger = setup_module_logger('services.response_template_engine')

class ResponseTemplateEngine:
    """
    Generates templated responses for data exploration operations
    """
    
    def __init__(self):
        """Initialize the template engine"""
        master_logger.info("Initializing Response Template Engine")
        self._templates = self._load_templates()
        self._operation_patterns = self._load_operation_patterns()
    
    def _load_templates(self) -> Dict[str, Dict[str, str]]:
        """Load response templates for different operations"""
        return {
            "top_bottom_analysis": {
                "top_template": """**Top {count} {entity_name} by {metric_name}**

Here are the highest-performing {entity_name} based on {metric_name}:

{formatted_results}

**Key Insight**: {top_insight}""",
                
                "bottom_template": """**Bottom {count} {entity_name} by {metric_name}**

Here are the {entity_name} with the lowest {metric_name}:

{formatted_results}

**Key Finding**: {bottom_insight}""",
                
                "top_insight_template": "{leader} leads with {leader_value}, which is {comparison_text} ahead of the second place.",
                "bottom_insight_template": "These {count} {entity_name} each have {common_pattern}, {interpretation}."
            },
            
            # Placeholder for future templates
            "trend_analysis": {
                "template": """**{metric_name} Trend Analysis**

Analyzing trends over {time_period}:

{formatted_results}

**Trend Summary**: {trend_insight}"""
            },
            
            "comparison": {
                "template": """**Comparison Analysis**

Comparing {compared_items}:

{formatted_results}

**Comparison Result**: {comparison_insight}"""
            },
            
            "data_exploration": {
                "template": """**Data Exploration Results**

Found {total_records} records matching your criteria:

{formatted_results}

**Overview**: {exploration_insight}"""
            },
            
            "percentile_analysis": {
                "simple_template": """**{metric_name} Percentile Analysis**

Here are the percentiles for {metric_name}:

{formatted_results}

**Statistical Summary**: {percentile_insight}""",
                
                "grouped_template": """**{metric_name} Percentiles by {entity_name}**

Percentile breakdown across different {entity_name}:

{formatted_results}

**Distribution Insights**: {grouped_insight}""",
                
                "insight_template": "The median ({median_label}) is {median_value}, showing {distribution_pattern}.",
                "grouped_insight_template": "Distribution varies across {entity_name}, with {comparison_insight}."
            },
            
            "aggregation_summary": {
                "single_value_template": """**{metric_name} Summary**

{summary_description}:

{formatted_results}

**Summary**: {aggregation_insight}""",
                
                "breakdown_template": """**{metric_name} Breakdown by {entity_name}**

Here's the breakdown of {metric_name} across different {entity_name}:

{formatted_results}

**Analysis**: {breakdown_insight}""",
                
                "time_summary_template": """**{metric_name} for {time_period}**

{time_description}:

{formatted_results}

**Period Summary**: {time_insight}"""
            },
            
            "composition_percentage": {
                "simple_percentage_template": """**Percentage Analysis**

{filter_description}:

{formatted_results}

**Result**: {percentage_insight}""",
                
                "breakdown_template": """**Percentage Composition by {entity_name}**

Here's the percentage breakdown across different {entity_name}:

{formatted_results}

**Composition Analysis**: {composition_insight}""",
                
                "insight_template": "{filtered_count:,} out of {total_count:,} records match the criteria ({percentage}%).",
                "composition_insight_template": "{top_category} makes up the largest portion at {top_percentage}, followed by {second_category} at {second_percentage}."
            }
        }
    
    def _load_operation_patterns(self) -> Dict[str, List[str]]:
        """Load patterns to detect operation sub-types"""
        return {
            "top_patterns": ["top", "highest", "best", "largest", "maximum", "most"],
            "bottom_patterns": ["bottom", "lowest", "worst", "smallest", "minimum", "least", "fewest"],
            "trend_patterns": ["trend", "over time", "monthly", "quarterly", "yearly", "temporal"],
            "comparison_patterns": ["compare", "vs", "versus", "difference", "against"],
            "percentile_patterns": ["percentile", "quartile", "median", "quantile", "p25", "p50", "p75", "p90", "p95", "p99", "25th", "50th", "75th", "90th", "95th", "99th"],
            "composition_patterns": ["percentage", "percent", "composition", "breakdown", "distribution", "split", "proportion", "share", "ratio"]
        }
    
    def can_template_response(self, intent_type: str, query: str) -> bool:
        """Check if we can create a templated response for this operation"""
        supported_intents = ["top_bottom_analysis", "trend_analysis", "comparison", "data_exploration", "percentile_analysis", "aggregation_summary", "composition_percentage"]
        return intent_type in supported_intents and intent_type in self._templates
    
    def format_templated_response(self, 
                                 intent_type: str, 
                                 query: str, 
                                 df: Union[pd.DataFrame, pl.DataFrame], 
                                 fallback_markdown: str,
                                 nl_result=None) -> str:
        """
        Create a templated response for the given operation
        
        Args:
            intent_type: The type of analysis (e.g., 'top_bottom_analysis')
            query: Original user query
            df: Result DataFrame (pandas or polars)
            fallback_markdown: Fallback markdown table
            nl_result: NL-to-Python extraction result (optional)
            
        Returns:
            Formatted templated response or fallback
        """
        try:
            master_logger.info(f"[TEMPLATE] Formatting {intent_type} response")
            
            # Convert polars DataFrame to pandas for template processing
            if isinstance(df, pl.DataFrame):
                master_logger.info("[TEMPLATE] Converting polars DataFrame to pandas for template processing")
                df = df.to_pandas()
            
            if not self.can_template_response(intent_type, query):
                master_logger.info(f"[TEMPLATE] No template available for {intent_type}, using fallback")
                return fallback_markdown
            
            if intent_type == "top_bottom_analysis":
                return self._format_top_bottom_response(query, df, fallback_markdown, nl_result)
            elif intent_type == "trend_analysis":
                return self._format_trend_response(query, df, fallback_markdown)
            elif intent_type == "comparison":
                return self._format_comparison_response(query, df, fallback_markdown)
            elif intent_type == "data_exploration":
                return self._format_exploration_response(query, df, fallback_markdown)
            elif intent_type == "percentile_analysis":
                return self._format_percentile_response(query, df, fallback_markdown, nl_result)
            elif intent_type == "aggregation_summary":
                return self._format_aggregation_summary_response(query, df, fallback_markdown, nl_result)
            elif intent_type == "composition_percentage":
                return self._format_composition_percentage_response(query, df, fallback_markdown, nl_result)
            else:
                return fallback_markdown
                
        except Exception as e:
            master_logger.error(f"[TEMPLATE] Error formatting template: {e}")
            master_logger.info(f"[TEMPLATE] Falling back to markdown table")
            return fallback_markdown
    
    def _format_top_bottom_response(self, query: str, df: pd.DataFrame, fallback: str, nl_result=None) -> str:
        """Format top/bottom analysis with rich template"""
        try:
            # Check if we have NL result - required for template formatting
            if not nl_result:
                master_logger.info("[TEMPLATE] No NL result provided, using fallback")
                return fallback
                
            # Detect if it's top or bottom query
            if not query:
                master_logger.error("[TEMPLATE] Query is None or empty")
                return fallback
                
            query_lower = query.lower()
            is_top = any(pattern in query_lower for pattern in self._operation_patterns["top_patterns"])
            is_bottom = any(pattern in query_lower for pattern in self._operation_patterns["bottom_patterns"])
            
            # Default to top if unclear
            if not is_top and not is_bottom:
                is_top = True
            
            # Extract key information from NL result
            count = len(df)
            entity_name, metric_name = self._extract_entity_and_metric_from_nl_result(nl_result)
            
            # Format results nicely - pass NL result for better column detection
            formatted_results = self._format_ranking_results(df, is_top, nl_result)
            
            # Generate insights - pass NL result for better column detection
            if is_top:
                insight = self._generate_top_insight(df, entity_name, metric_name, nl_result)
                template = self._templates["top_bottom_analysis"]["top_template"]
            else:
                insight = self._generate_bottom_insight(df, entity_name, metric_name, count, nl_result)
                template = self._templates["top_bottom_analysis"]["bottom_template"]
            
            # Fill template
            response = template.format(
                count=count,
                entity_name=entity_name,
                metric_name=metric_name,
                formatted_results=formatted_results,
                top_insight=insight if is_top else "",
                bottom_insight=insight if not is_top else ""
            )
            
            master_logger.info(f"[TEMPLATE] Successfully formatted top_bottom_analysis response")
            return response
        
        except Exception as e:
            master_logger.error(f"[TEMPLATE] Error in top_bottom formatting: {e}")
            return fallback
    
    def _extract_entity_and_metric_from_nl_result(self, nl_result) -> Tuple[str, str]:
        """Extract entity and metric from NL-to-Python result"""
        try:
            # Extract from NL result 
            group_by_cols = getattr(nl_result, 'group_by_columns', None) or []
            metric_col = getattr(nl_result, 'metric_column', None)
            
            # Convert to user-friendly names
            entity_name = "Items"  # default
            if group_by_cols and len(group_by_cols) > 0:
                # Take first group_by column and clean it up
                raw_entity = group_by_cols[0].replace('_', ' ').replace('account ', '').title()
                entity_name = raw_entity if raw_entity else "Items"
            
            metric_name = "Value"  # default  
            if metric_col:
                # Clean up metric name
                raw_metric = metric_col.replace('_', ' ').title()
                metric_name = raw_metric if raw_metric else "Value"
            
            master_logger.info(f"[TEMPLATE] Extracted from NL: entity='{entity_name}', metric='{metric_name}'")
            return entity_name, metric_name
            
        except Exception as e:
            master_logger.error(f"[TEMPLATE] Error extracting from NL result: {e}")
            return "Items", "Value"
    
    def _get_columns(self, df: pd.DataFrame, nl_result=None) -> tuple:
        """Centralized column detection logic"""
        cat_col = None
        num_col = None
        
        if nl_result:
            # Use NL result columns if available
            group_by_cols = getattr(nl_result, 'group_by_columns', None) or []
            metric_col = getattr(nl_result, 'metric_column', None)
            
            if group_by_cols and group_by_cols[0] in df.columns:
                cat_col = group_by_cols[0]
            if metric_col and metric_col in df.columns:
                num_col = metric_col
        
        # Fallback to automatic detection if NL result not available
        if not cat_col:
            cat_col = next((col for col in df.columns if df[col].dtype == 'object'), None)
        
        if not num_col:
            # Better numeric column detection - exclude rank-like columns
            excluded_patterns = ['rank', 'index', 'level_0', 'level_1', 'Unnamed']
            for col in df.columns:
                if (pd.api.types.is_numeric_dtype(df[col]) and 
                    not any(pattern.lower() in col.lower() for pattern in excluded_patterns)):
                    num_col = col
                    break
        
        return cat_col, num_col
    
    def _format_ranking_results(self, df: pd.DataFrame, is_top: bool, nl_result=None) -> str:
        """Format ranking results cleanly"""
        try:
            results = []
            
            # Use centralized column detection
            cat_col, num_col = self._get_columns(df, nl_result)
            
            master_logger.info(f"[TEMPLATE] Column detection: cat_col='{cat_col}', num_col='{num_col}'")
            master_logger.info(f"[TEMPLATE] Available columns: {list(df.columns)}")
            
            if not cat_col or not num_col:
                master_logger.error(f"[TEMPLATE] Missing columns - cat_col: {cat_col}, num_col: {num_col}")
                return "Unable to format results"
            
            for i, (_, row) in enumerate(df.iterrows()):
                entity = str(row[cat_col])
                value = row[num_col]
                
                # Format value
                if isinstance(value, (int, np.integer)):
                    formatted_value = f"{value:,}"
                elif isinstance(value, (float, np.floating)):
                    formatted_value = f"{value:,.1f}"
                else:
                    formatted_value = str(value)
                
                results.append(f"**{i+1}.** {entity}: {formatted_value}")
            
            return "\n".join(results)
            
        except Exception as e:
            master_logger.error(f"[TEMPLATE] Error formatting results: {e}")
            return "Results formatting failed"
    
    def _generate_top_insight(self, df: pd.DataFrame, entity_name: str, metric_name: str, nl_result=None) -> str:
        """Generate simple insight for top performers"""
        try:
            # Use same column detection logic as _format_ranking_results
            cat_col, num_col = self._get_columns(df, nl_result)
            
            if not cat_col or not num_col or len(df) == 0:
                return "Analysis completed successfully."
            
            top_value = df.iloc[0][num_col]
            
            # Check if multiple entities share the top value
            top_entities = df[df[num_col] == top_value][cat_col].tolist()
            num_tied = len(top_entities)
            
            # Single leader - use original template
            if num_tied == 1:
                leader = top_entities[0]
                if len(df) > 1:
                    second_value = df.iloc[1][num_col]
                    diff = top_value - second_value
                    return f"{leader} leads with {top_value:,.0f}, ahead by {diff:,.0f}."
                else:
                    return f"{leader} leads with {top_value:,.0f}."
            
            # Multiple entities tied at top - use new template
            else:
                # Format entity names with commas and "and"
                if num_tied == 2:
                    entities_str = f"{top_entities[0]} and {top_entities[1]}"
                else:
                    entities_str = ", ".join(top_entities[:-1]) + f", and {top_entities[-1]}"
                
                # Find the next highest value (if exists) to calculate gap
                non_tied_df = df[df[num_col] != top_value]
                if len(non_tied_df) > 0:
                    next_value = non_tied_df.iloc[0][num_col]
                    diff = top_value - next_value
                    return f"{entities_str} are tied at the top with {top_value:,.0f}, ahead by {diff:,.0f}."
                else:
                    return f"{entities_str} are tied with {top_value:,.0f}."
            
        except Exception as e:
            return "Analysis completed successfully."
    
    def _generate_bottom_insight(self, df: pd.DataFrame, entity_name: str, metric_name: str, count: int, nl_result=None) -> str:
        """Generate simple insight for bottom performers"""
        try:
            # Use same column detection logic as _format_ranking_results
            cat_col, num_col = self._get_columns(df, nl_result)
            
            if not cat_col or not num_col or len(df) == 0:
                return "Analysis completed successfully."
            
            # For bottom queries, data is sorted ascending, so lowest is at index 0
            bottom_value = df.iloc[0][num_col]
            
            # Check if multiple entities share the bottom value
            bottom_entities = df[df[num_col] == bottom_value][cat_col].tolist()
            num_tied = len(bottom_entities)
            
            # Single entity at bottom - use original template
            if num_tied == 1:
                bottom_entity = bottom_entities[0]
                return f"{bottom_entity} has the lowest {metric_name.lower()} at {bottom_value:,.0f}."
            
            # Multiple entities tied at bottom - use new template
            else:
                # Format entity names with commas and "and"
                if num_tied == 2:
                    entities_str = f"{bottom_entities[0]} and {bottom_entities[1]}"
                else:
                    entities_str = ", ".join(bottom_entities[:-1]) + f", and {bottom_entities[-1]}"
                
                # Find the next lowest value (if exists) to calculate gap
                non_tied_df = df[df[num_col] != bottom_value]
                if len(non_tied_df) > 0:
                    next_value = non_tied_df.iloc[0][num_col]
                    diff = next_value - bottom_value
                    return f"{entities_str} are tied with the lowest {metric_name.lower()} at {bottom_value:,.0f}, {diff:,.0f} below the next."
                else:
                    return f"{entities_str} are tied with {bottom_value:,.0f}."
                
        except Exception as e:
            return "Analysis completed successfully."
    
    def _format_percentile_response(self, query: str, df: pd.DataFrame, fallback: str, nl_result=None) -> str:
        """Format percentile analysis with rich template"""
        try:
            master_logger.info("[TEMPLATE] Formatting percentile analysis response")
            
            if df.empty:
                master_logger.warning("[TEMPLATE] Empty DataFrame for percentile analysis")
                return fallback
            
            # Determine if this is simple percentiles or grouped percentiles
            is_grouped = self._is_grouped_percentiles(df)
            
            if is_grouped:
                return self._format_grouped_percentiles(query, df, fallback, nl_result)
            else:
                return self._format_simple_percentiles(query, df, fallback, nl_result)
                
        except Exception as e:
            master_logger.error(f"[TEMPLATE] Error formatting percentile response: {e}")
            return fallback
    
    def _is_grouped_percentiles(self, df: pd.DataFrame) -> bool:
        """Determine if this is grouped percentiles or simple percentiles"""
        # Simple percentiles have 'percentile' and 'value' columns
        if 'percentile' in df.columns and 'value' in df.columns:
            return False
        
        # Check for the long-form format with 'level_1' containing percentile indicators
        if 'level_1' in df.columns:
            # Check if level_1 contains percentile indicators (p25, p50, p75, etc.)
            sample_values = df['level_1'].astype(str).unique()
            percentile_values = [v for v in sample_values if v.startswith('p') and v[1:].replace('.', '').isdigit()]
            if len(percentile_values) > 0:
                return True  # This is grouped percentiles in long format
        
        # Grouped percentiles have p25, p50, p75, etc. columns
        percentile_cols = [col for col in df.columns if col.startswith('p') and col[1:].isdigit()]
        return len(percentile_cols) > 0
    
    def _format_simple_percentiles(self, query: str, df: pd.DataFrame, fallback: str, nl_result=None) -> str:
        """Format simple percentiles (no grouping)"""
        try:
            # Extract metric name from NL result or query
            metric_name = self._extract_metric_name_from_nl_result(nl_result) or "Value"
            
            # Format the results
            formatted_results = []
            median_value = None
            median_label = None
            
            for _, row in df.iterrows():
                percentile = row['percentile']
                value = row['value']
                
                # Convert percentile to readable format
                percentile_num = percentile.replace('p', '')
                percentile_readable = f"{percentile_num}th percentile"
                
                # Track median for insights
                if percentile == 'p50':
                    median_value = value
                    median_label = "50th percentile"
                
                # Format value
                if isinstance(value, (int, np.integer)):
                    formatted_value = f"{value:,}"
                elif isinstance(value, (float, np.floating)):
                    formatted_value = f"{value:,.1f}"
                else:
                    formatted_value = str(value)
                
                formatted_results.append(f"**{percentile_readable}**: {formatted_value}")
            
            results_text = "\n".join(formatted_results)
            
            # Generate insights
            insight = self._generate_percentile_insights(df, median_value, median_label)
            
            # Use template
            template = self._templates["percentile_analysis"]["simple_template"]
            response = template.format(
                metric_name=metric_name,
                formatted_results=results_text,
                percentile_insight=insight
            )
            
            master_logger.info("[TEMPLATE] Successfully formatted simple percentile response")
            return response
            
        except Exception as e:
            master_logger.error(f"[TEMPLATE] Error in simple percentile formatting: {e}")
            return fallback
    
    def _format_grouped_percentiles(self, query: str, df: pd.DataFrame, fallback: str, nl_result=None) -> str:
        """Format grouped percentiles (by category)"""
        try:
            # Extract entity and metric names
            entity_name, metric_name = self._extract_entity_and_metric_from_nl_result(nl_result)
            
            # Check if this is long-form format with 'level_1' column
            if 'level_1' in df.columns:
                return self._format_long_form_percentiles(query, df, fallback, nl_result, entity_name, metric_name)
            
            # Get group columns and percentile columns (wide format)
            percentile_cols = sorted([col for col in df.columns if col.startswith('p') and col[1:].isdigit()])
            group_cols = [col for col in df.columns if col not in percentile_cols]
            
            if not percentile_cols or not group_cols:
                master_logger.warning("[TEMPLATE] Could not identify group/percentile columns")
                return fallback
            
            group_col = group_cols[0]  # Use first group column
            
            # Format results
            formatted_results = []
            
            for _, row in df.iterrows():
                group_value = row[group_col]
                percentile_values = []
                
                for p_col in percentile_cols:
                    p_num = p_col.replace('p', '')
                    value = row[p_col]
                    
                    if isinstance(value, (int, np.integer)):
                        formatted_value = f"{value:,}"
                    elif isinstance(value, (float, np.floating)):
                        formatted_value = f"{value:,.1f}"
                    else:
                        formatted_value = str(value)
                    
                    percentile_values.append(f"p{p_num}: {formatted_value}")
                
                percentiles_text = ", ".join(percentile_values)
                formatted_results.append(f"**{group_value}**: {percentiles_text}")
            
            results_text = "\n".join(formatted_results)
            
            # Generate grouped insights
            insight = self._generate_grouped_percentile_insights(df, entity_name, group_col, percentile_cols)
            
            # Use template
            template = self._templates["percentile_analysis"]["grouped_template"]
            response = template.format(
                metric_name=metric_name,
                entity_name=entity_name,
                formatted_results=results_text,
                grouped_insight=insight
            )
            
            master_logger.info("[TEMPLATE] Successfully formatted grouped percentile response")
            return response
            
        except Exception as e:
            master_logger.error(f"[TEMPLATE] Error in grouped percentile formatting: {e}")
            return fallback
    
    def _format_long_form_percentiles(self, query: str, df: pd.DataFrame, fallback: str, nl_result, entity_name: str, metric_name: str) -> str:
        """Format percentiles in long form with level_1 column containing percentile indicators"""
        try:
            # Use the column information directly from NL result instead of detecting
            if nl_result and hasattr(nl_result, 'group_by_columns') and hasattr(nl_result, 'metric_column'):
                group_col = nl_result.group_by_columns[0] if nl_result.group_by_columns else None
                value_col = nl_result.metric_column
            else:
                # Fallback: if nl_result doesn't have the info, detect from DataFrame
                numeric_cols = [col for col in df.columns if df[col].dtype in ['int64', 'float64', 'int32', 'float32']]
                value_col = numeric_cols[0] if numeric_cols else 'time_to_close'
                group_col = [col for col in df.columns if col not in ['level_1', value_col] and df[col].dtype == 'object'][0]
            
            if not group_col or not value_col:
                master_logger.warning(f"[TEMPLATE] Could not determine columns: group_col={group_col}, value_col={value_col}")
                return fallback
                
            formatted_results = []
            
            # Group by the grouping column (e.g., account_country)
            groups = df.groupby(group_col)
            
            for group_name, group_data in groups:
                percentile_values = []
                
                # Process each percentile for this group
                for _, row in group_data.iterrows():
                    percentile_indicator = str(row['level_1'])
                    value = row[value_col]
                    
                    # Convert percentile indicator to readable format
                    if percentile_indicator.startswith('p'):
                        p_num = percentile_indicator.replace('p', '')
                        percentile_readable = f"p{p_num}"
                    else:
                        percentile_readable = percentile_indicator
                    
                    # Format value
                    if isinstance(value, (int, np.integer)):
                        formatted_value = f"{value:,}"
                    elif isinstance(value, (float, np.floating)):
                        formatted_value = f"{value:,.1f}"
                    else:
                        formatted_value = str(value)
                    
                    percentile_values.append(f"{percentile_readable}: {formatted_value}")
                
                # Sort percentiles by numeric value for consistent display
                percentile_values.sort(key=lambda x: int(x.split('p')[1].split(':')[0]) if 'p' in x else 0)
                percentiles_text = ", ".join(percentile_values)
                formatted_results.append(f"**{group_name}**: {percentiles_text}")
            
            results_text = "\n".join(formatted_results)
            
            # Generate insights (simplified for long form)
            all_values = df[value_col].dropna()
            if len(all_values) > 0:
                median_value = all_values.median()
                insight = f"The median {metric_name.lower()} across all groups is {median_value:,.1f}."
            else:
                insight = "Unable to generate insights from the data."
            
            # Use template
            template = self._templates["percentile_analysis"]["grouped_template"]
            response = template.format(
                metric_name=metric_name,
                entity_name=entity_name,
                formatted_results=results_text,
                grouped_insight=insight
            )
            
            master_logger.info("[TEMPLATE] Successfully formatted long-form percentile response")
            return response
            
        except Exception as e:
            master_logger.error(f"[TEMPLATE] Error in long-form percentile formatting: {e}")
            return fallback
    
    def _extract_metric_name_from_nl_result(self, nl_result) -> str:
        """Extract just the metric name from NL result"""
        if not nl_result:
            return "Value"
        
        metric_col = getattr(nl_result, 'metric_column', None) or getattr(nl_result, 'column', None)
        if metric_col:
            return metric_col.replace('_', ' ').title()
        
        return "Value"
    
    def _generate_percentile_insights(self, df: pd.DataFrame, median_value, median_label) -> str:
        """Generate simple insights for percentiles using only existing data"""
        try:
            if median_value is not None and median_label:
                # Only format the median value, no new calculations
                if isinstance(median_value, (int, np.integer)):
                    formatted_median = f"{median_value:,}"
                elif isinstance(median_value, (float, np.floating)):
                    formatted_median = f"{median_value:,.1f}"
                else:
                    formatted_median = str(median_value)
                
                return f"The median ({median_label}) is {formatted_median}."
            
            # Simple count-based insight using existing data only
            percentile_count = len(df)
            return f"Calculated {percentile_count} percentile values successfully."
            
        except Exception as e:
            return "Percentile analysis completed successfully."
    
    def _generate_grouped_percentile_insights(self, df: pd.DataFrame, entity_name: str, group_col: str, percentile_cols: list) -> str:
        """Generate simple insights for grouped percentiles using only existing data"""
        try:
            # Only use counts and basic info that's already in the DataFrame
            group_count = len(df)
            percentile_count = len(percentile_cols)
            
            if group_count == 1:
                return f"Percentile breakdown calculated for 1 {entity_name.lower()[:-1] if entity_name.lower().endswith('s') else entity_name.lower()}."
            else:
                return f"Percentile breakdown calculated for {group_count} {entity_name.lower()} across {percentile_count} percentiles."
            
        except Exception as e:
            return "Percentile analysis completed successfully."

    # Placeholder methods for future templates
    def _format_trend_response(self, query: str, df: pd.DataFrame, fallback: str) -> str:
        """Format trend analysis - to be implemented"""
        master_logger.info("[TEMPLATE] Trend analysis template not yet implemented")
        return fallback
    
    def _format_comparison_response(self, query: str, df: pd.DataFrame, fallback: str) -> str:
        """Format comparison analysis - to be implemented"""
        master_logger.info("[TEMPLATE] Comparison analysis template not yet implemented")
        return fallback
    
    def _format_exploration_response(self, query: str, df: pd.DataFrame, fallback: str) -> str:
        """Format data exploration - to be implemented"""
        master_logger.info("[TEMPLATE] Data exploration template not yet implemented")
        return fallback
    
    def _format_aggregation_summary_response(self, query: str, df: pd.DataFrame, fallback: str, nl_result=None) -> str:
        """Format aggregation summary with intelligent template selection"""
        try:
            master_logger.info("[TEMPLATE] Formatting aggregation summary response")
            
            if df.empty:
                master_logger.warning("[TEMPLATE] Empty DataFrame for aggregation summary")
                return fallback
            
            # Determine the type of aggregation summary
            summary_type = self._detect_aggregation_summary_type(df, query)
            master_logger.info(f"[TEMPLATE] Detected summary type: {summary_type}")
            
            if summary_type == "single_value":
                return self._format_single_value_summary(query, df, fallback, nl_result)
            elif summary_type == "breakdown":
                return self._format_breakdown_summary(query, df, fallback, nl_result)
            elif summary_type == "time_summary":
                return self._format_time_summary(query, df, fallback, nl_result)
            else:
                # Fallback to single value if uncertain
                return self._format_single_value_summary(query, df, fallback, nl_result)
                
        except Exception as e:
            master_logger.error(f"[TEMPLATE] Error formatting aggregation summary: {e}")
            return fallback
    
    def _detect_aggregation_summary_type(self, df: pd.DataFrame, query: str) -> str:
        """Detect what type of aggregation summary this is"""
        query_lower = query.lower()
        
        # Check for time-based indicators
        time_indicators = ["in march", "in april", "in january", "in q1", "in q2", "monthly", "quarterly", "yearly", "this month", "last month"]
        if any(indicator in query_lower for indicator in time_indicators):
            return "time_summary"
        
        # Check for single value indicators (total, count, sum, average)
        single_value_indicators = ["total", "sum", "average", "avg", "mean", "overall"]
        if any(indicator in query_lower for indicator in single_value_indicators) and len(df) <= 3:
            return "single_value"
        
        # If multiple rows with categorical data, it's likely a breakdown
        if len(df) > 1:
            categorical_columns = [col for col in df.columns if df[col].dtype == 'object']
            if categorical_columns:
                return "breakdown"
        
        # Default to single value for small datasets
        return "single_value"
    
    def _format_single_value_summary(self, query: str, df: pd.DataFrame, fallback: str, nl_result=None) -> str:
        """Format single value summaries like 'total ticket count in march'"""
        try:
            # Extract metric name
            metric_name = self._extract_metric_name_from_nl_result(nl_result) or "Value"
            
            # Get the main value (usually in the last numeric column)
            numeric_cols = [col for col in df.columns if pd.api.types.is_numeric_dtype(df[col])]
            if not numeric_cols:
                return fallback
            
            main_value = df[numeric_cols[-1]].iloc[0] if len(df) > 0 else 0
            
            # Format the value
            if isinstance(main_value, (int, np.integer)):
                formatted_value = f"**{main_value:,}**"
            elif isinstance(main_value, (float, np.floating)):
                formatted_value = f"**{main_value:,.1f}**"
            else:
                formatted_value = f"**{str(main_value)}**"
            
            # Detect time period from query
            time_period = self._extract_time_period_from_query(query)
            
            # Generate summary description and insight
            if time_period:
                summary_desc = f"Total {metric_name} for {time_period}"
                insight = f"Recorded {main_value:,} {metric_name.lower()} during {time_period}."
            else:
                summary_desc = f"Total {metric_name}"
                insight = f"Total of {main_value:,} {metric_name.lower()} found."
            
            # Use template
            template = self._templates["aggregation_summary"]["single_value_template"]
            response = template.format(
                metric_name=metric_name,
                summary_description=summary_desc,
                formatted_results=formatted_value,
                aggregation_insight=insight
            )
            
            master_logger.info("[TEMPLATE] Successfully formatted single value summary")
            return response
            
        except Exception as e:
            master_logger.error(f"[TEMPLATE] Error in single value summary formatting: {e}")
            return fallback
    
    def _format_breakdown_summary(self, query: str, df: pd.DataFrame, fallback: str, nl_result=None) -> str:
        """Format breakdown summaries like 'tickets by status'"""
        try:
            # Extract entity and metric names
            entity_name, metric_name = self._extract_entity_and_metric_from_nl_result(nl_result)
            
            # Get columns for breakdown
            cat_col, num_col = self._get_columns(df, nl_result)
            
            if not cat_col or not num_col:
                return self._format_single_value_summary(query, df, fallback, nl_result)
            
            # Format results
            formatted_results = []
            total = df[num_col].sum() if pd.api.types.is_numeric_dtype(df[num_col]) else len(df)
            
            for _, row in df.iterrows():
                category = str(row[cat_col])
                value = row[num_col]
                
                if isinstance(value, (int, np.integer)):
                    formatted_value = f"{value:,}"
                elif isinstance(value, (float, np.floating)):
                    formatted_value = f"{value:,.1f}"
                else:
                    formatted_value = str(value)
                
                # Calculate percentage if possible
                if pd.api.types.is_numeric_dtype(df[num_col]) and total > 0:
                    percentage = (value / total) * 100
                    formatted_results.append(f"**{category}**: {formatted_value} ({percentage:.1f}%)")
                else:
                    formatted_results.append(f"**{category}**: {formatted_value}")
            
            results_text = "\n".join(formatted_results)
            
            # Generate insight
            if pd.api.types.is_numeric_dtype(df[num_col]):
                top_category = df.loc[df[num_col].idxmax(), cat_col]
                top_value = df[num_col].max()
                insight = f"{top_category} has the highest {metric_name.lower()} with {top_value:,}."
            else:
                insight = f"Breakdown shows {len(df)} different {entity_name.lower()}."
            
            # Use template
            template = self._templates["aggregation_summary"]["breakdown_template"]
            response = template.format(
                metric_name=metric_name,
                entity_name=entity_name,
                formatted_results=results_text,
                breakdown_insight=insight
            )
            
            master_logger.info("[TEMPLATE] Successfully formatted breakdown summary")
            return response
            
        except Exception as e:
            master_logger.error(f"[TEMPLATE] Error in breakdown summary formatting: {e}")
            return fallback
    
    def _format_time_summary(self, query: str, df: pd.DataFrame, fallback: str, nl_result=None) -> str:
        """Format time-based summaries"""
        try:
            # Extract time period and metric
            time_period = self._extract_time_period_from_query(query)
            metric_name = self._extract_metric_name_from_nl_result(nl_result) or "Value"
            
            # Check if it's a single value or breakdown
            if len(df) == 1:
                return self._format_single_value_summary(query, df, fallback, nl_result)
            else:
                return self._format_breakdown_summary(query, df, fallback, nl_result)
                
        except Exception as e:
            master_logger.error(f"[TEMPLATE] Error in time summary formatting: {e}")
            return fallback
    
    def _extract_time_period_from_query(self, query: str) -> str:
        """Extract time period mentions from query"""
        import re
        query_lower = query.lower()
        
        # Common time period patterns
        patterns = {
            r'\bin march\b': 'March',
            r'\bin april\b': 'April', 
            r'\bin january\b': 'January',
            r'\bin february\b': 'February',
            r'\bin may\b': 'May',
            r'\bin june\b': 'June',
            r'\bin july\b': 'July',
            r'\bin august\b': 'August',
            r'\bin september\b': 'September',
            r'\bin october\b': 'October',
            r'\bin november\b': 'November',
            r'\bin december\b': 'December',
            r'\bin q1\b': 'Q1',
            r'\bin q2\b': 'Q2',
            r'\bin q3\b': 'Q3',
            r'\bin q4\b': 'Q4',
            r'\bthis month\b': 'this month',
            r'\blast month\b': 'last month',
            r'\bthis year\b': 'this year',
            r'\blast year\b': 'last year'
        }
        
        for pattern, period in patterns.items():
            if re.search(pattern, query_lower):
                return period
        
        return None

    def _format_composition_percentage_response(self, query: str, df: pd.DataFrame, fallback: str, nl_result=None) -> str:
        """Format composition percentage analysis with intelligent template selection"""
        try:
            master_logger.info("[TEMPLATE] Formatting composition percentage response")
            
            if df.empty:
                master_logger.warning("[TEMPLATE] Empty DataFrame for composition percentage")
                return fallback
            
            # Determine the type of composition percentage result
            is_simple_percentage = self._is_simple_percentage_result(df)
            master_logger.info(f"[TEMPLATE] Detected composition type: {'simple' if is_simple_percentage else 'breakdown'}")
            
            if is_simple_percentage:
                return self._format_simple_percentage(query, df, fallback, nl_result)
            else:
                return self._format_percentage_breakdown(query, df, fallback, nl_result)
                
        except Exception as e:
            master_logger.error(f"[TEMPLATE] Error formatting composition percentage: {e}")
            return fallback
    
    def _is_simple_percentage_result(self, df: pd.DataFrame) -> bool:
        """Determine if this is a simple percentage result or breakdown"""
        # Simple percentage has 'numerator', 'denominator', 'percentage' columns
        required_simple_cols = ['numerator', 'denominator', 'percentage']
        has_simple_cols = all(col in df.columns for col in required_simple_cols)
        
        # Breakdown has group column, 'count', and 'percentage' columns
        has_count_and_percentage = 'count' in df.columns and 'percentage' in df.columns
        
        return has_simple_cols and not has_count_and_percentage
    
    def _format_simple_percentage(self, query: str, df: pd.DataFrame, fallback: str, nl_result=None) -> str:
        """Format simple percentage results (filtered vs total)"""
        try:
            if len(df) == 0:
                return fallback
            
            # Extract values from the first row
            row = df.iloc[0]
            filtered_count = int(row['numerator'])
            total_count = int(row['denominator'])
            percentage = float(row['percentage'])
            
            # Extract filter description from NL result or query
            filter_description = self._extract_filter_description(query, nl_result)
            
            # Format the main result
            formatted_results = f"**{filtered_count:,}** out of **{total_count:,}** records (**{percentage}%**)"
            
            # Generate insight using template
            insight_template = self._templates["composition_percentage"]["insight_template"]
            insight = insight_template.format(
                filtered_count=filtered_count,
                total_count=total_count,
                percentage=f"{percentage}%"
            )
            
            # Use simple percentage template
            template = self._templates["composition_percentage"]["simple_percentage_template"]
            response = template.format(
                filter_description=filter_description,
                formatted_results=formatted_results,
                percentage_insight=insight
            )
            
            master_logger.info("[TEMPLATE] Successfully formatted simple percentage response")
            return response
            
        except Exception as e:
            master_logger.error(f"[TEMPLATE] Error in simple percentage formatting: {e}")
            return fallback
    
    def _format_percentage_breakdown(self, query: str, df: pd.DataFrame, fallback: str, nl_result=None) -> str:
        """Format percentage breakdown results (composition by category)"""
        try:
            # Extract entity name from NL result
            entity_name, _ = self._extract_entity_and_metric_from_nl_result(nl_result)
            
            # 🔧 FIX: Use NL result to get the correct group column
            group_col = None
            if nl_result:
                group_by_cols = getattr(nl_result, 'group_by_columns', None) or []
                if group_by_cols and group_by_cols[0] in df.columns:
                    group_col = group_by_cols[0]
                    master_logger.info(f"[TEMPLATE] Using NL result group column: {group_col}")
            
            # Fallback: exclude rank-like columns and system columns
            if not group_col:
                excluded_cols = ['count', 'percentage', 'level_0', 'index', 'rank', 'Unnamed']
                group_cols = [col for col in df.columns if not any(excl.lower() in col.lower() for excl in excluded_cols)]
                if not group_cols:
                    master_logger.warning("[TEMPLATE] No suitable group column found for percentage breakdown")
                    return fallback
                group_col = group_cols[0]
                master_logger.info(f"[TEMPLATE] Using fallback group column: {group_col}")
            
            # Add debug logging
            master_logger.info(f"[TEMPLATE] Available columns: {list(df.columns)}")
            master_logger.info(f"[TEMPLATE] Selected group column: '{group_col}'")
            master_logger.info(f"[TEMPLATE] Sample values from group column: {df[group_col].head(3).tolist()}")
            
            # Format results
            formatted_results = []
            total_percentage = 0
            
            for _, row in df.iterrows():
                category = str(row[group_col])
                count = int(row['count'])
                percentage = float(row['percentage'])
                total_percentage += percentage
                
                formatted_results.append(f"**{category}**: {count:,} ({percentage:.1f}%)")
            
            results_text = "\n".join(formatted_results)
            
            # Generate composition insights
            insight = self._generate_composition_insights(df, entity_name, group_col)
            
            # Use breakdown template
            template = self._templates["composition_percentage"]["breakdown_template"]
            response = template.format(
                entity_name=entity_name,
                formatted_results=results_text,
                composition_insight=insight
            )
            
            master_logger.info("[TEMPLATE] Successfully formatted percentage breakdown response")
            return response
            
        except Exception as e:
            master_logger.error(f"[TEMPLATE] Error in percentage breakdown formatting: {e}")
            return fallback
    
    def _extract_filter_description(self, query: str, nl_result=None) -> str:
        """Extract filter description from query or NL result"""
        # Try to extract from NL result first
        if nl_result:
            filters = getattr(nl_result, 'filters', None) or []
            temporal_filters = getattr(nl_result, 'temporal_filters', None) or []
            
            filter_descriptions = []
            
            # Process categorical filters
            for filter_spec in filters:
                column = filter_spec.get('column', '')
                value = filter_spec.get('value', '')
                if column and value:
                    clean_column = column.replace('_', ' ').title()
                    if isinstance(value, list):
                        if len(value) == 1:
                            filter_descriptions.append(f"{clean_column} = {value[0]}")
                        else:
                            filter_descriptions.append(f"{clean_column} in {value}")
                    else:
                        filter_descriptions.append(f"{clean_column} = {value}")
            
            # Process temporal filters
            for temp_filter in temporal_filters:
                period = temp_filter.get('period', '')
                if period:
                    filter_descriptions.append(f"in {period}")
            
            if filter_descriptions:
                return f"Filtered data ({', '.join(filter_descriptions)})"
        
        # Fallback: extract from query
        query_lower = query.lower()
        if "percentage" in query_lower or "percent" in query_lower:
            return "Percentage analysis of filtered data"
        elif "composition" in query_lower:
            return "Data composition analysis"
        else:
            return "Filtered data analysis"
    
    def _generate_composition_insights(self, df: pd.DataFrame, entity_name: str, group_col: str) -> str:
        """Generate insights for composition breakdown using existing data only"""
        try:
            if len(df) == 0:
                return "No data available for analysis."
            
            # Get top percentage value
            top_percentage = float(df.iloc[0]['percentage'])
            
            # Check if multiple categories share the top percentage
            top_categories = df[df['percentage'] == top_percentage][group_col].tolist()
            num_tied = len(top_categories)
            
            # Single top category - use original template
            if num_tied == 1:
                top_category = str(top_categories[0])
                
                if len(df) > 1:
                    second_row = df.iloc[1]
                    second_category = str(second_row[group_col])
                    second_percentage = float(second_row['percentage'])
                    
                    # Use composition insight template  
                    insight_template = self._templates["composition_percentage"]["composition_insight_template"]
                    return insight_template.format(
                        top_category=top_category,
                        top_percentage=f"{top_percentage:.1f}%",
                        second_category=second_category,
                        second_percentage=f"{second_percentage:.1f}%"
                    )
                else:
                    return f"{top_category} represents {top_percentage:.1f}% of the total composition."
            
            # Multiple categories tied at top percentage
            else:
                # Format category names with commas and "and"
                if num_tied == 2:
                    categories_str = f"{top_categories[0]} and {top_categories[1]}"
                else:
                    categories_str = ", ".join(str(c) for c in top_categories[:-1]) + f", and {top_categories[-1]}"
                
                non_tied_df = df[df['percentage'] != top_percentage]
                if len(non_tied_df) > 0:
                    next_percentage = float(non_tied_df.iloc[0]['percentage'])
                    return f"{categories_str} are tied at the top with {top_percentage:.1f}% each, above the next at {next_percentage:.1f}%."
                else:
                    return f"{categories_str} are tied with {top_percentage:.1f}% each."
            
        except Exception as e:
            return "Composition analysis completed successfully."
