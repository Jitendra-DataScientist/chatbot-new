"""
Smart Aggregation System for Tableau Analytics Agent

Complete aggregation decision system that:
1. Uses LLM to decide initial aggregation based on actual data
2. Stores decisions and dynamically switches based on query context
3. Validates and generates code with appropriate aggregations
4. Handles numeric coercion for object dtype columns

Based on reference implementation with full feature parity.
"""

import pandas as pd
import json
import yaml
from pathlib import Path
from typing import Dict, Any, Optional, List
import logging

from master_logger import setup_module_logger
from services.tableau_aggregation_hints import get_hints_manager


class AggregationMapper:
    """Maps between different aggregation functions."""
    
    def __init__(self):
        self.agg_map = {
            'COUNT': {
                'pandas': lambda col: col.count(),
                'pandas_str': '.count()',
                'alternatives': ['COUNT_DISTINCT', 'SUM']
            },
            'COUNT_DISTINCT': {
                'pandas': lambda col: col.nunique(),
                'pandas_str': '.nunique()',
                'alternatives': ['COUNT']
            },
            'SUM': {
                'pandas': lambda col: col.sum(),
                'pandas_str': '.sum()',
                'alternatives': ['AVG', 'COUNT']
            },
            'AVG': {
                'pandas': lambda col: col.mean(),
                'pandas_str': '.mean()',
                'alternatives': ['SUM', 'MEDIAN']
            },
            'MEDIAN': {
                'pandas': lambda col: col.median(),
                'pandas_str': '.median()',
                'alternatives': ['AVG']
            },
            'MIN': {
                'pandas': lambda col: col.min(),
                'pandas_str': '.min()',
                'alternatives': ['MAX']
            },
            'MAX': {
                'pandas': lambda col: col.max(),
                'pandas_str': '.max()',
                'alternatives': ['MIN']
            }
        }
    
    def get_aggregation_function(self, agg_type: str):
        """Get the pandas function for an aggregation type."""
        return self.agg_map.get(agg_type, {}).get('pandas')
    
    def get_aggregation_string(self, agg_type: str):
        """Get the pandas method string for code generation."""
        return self.agg_map.get(agg_type, {}).get('pandas_str', '.sum()')


class SmartAggregationDecider:
    """
    Intelligent aggregation decider that uses LLM reasoning and statistical
    analysis to determine appropriate aggregations for queries.
    """
    
    def __init__(self, llm_client, config_path: Optional[str] = None, cache_file_path: Optional[str] = None):
        """
        Initialize the aggregation decider.
        
        Args:
            llm_client: LLM client with chat.completions.create() method (OpenAI format)
            config_path: Optional path to user-defined column configs
            cache_file_path: Optional path to cache file for persistent storage
        """
        self.llm = llm_client
        self.user_config = self._load_user_config(config_path) if config_path else {}
        self.mapper = AggregationMapper()
        self.decision_store = {}  # Store decisions for reuse
        self.cache_file_path = cache_file_path
        self.logger = setup_module_logger('services.smart_aggregation')
        self.hints_manager = get_hints_manager(logger=self.logger)
        
        # Configuration from reference code
        self.smart_aggregation_enabled = True  # ON by default
        self.confidence_threshold = 'high'  # Only apply high confidence decisions automatically
        
        # Load cached decisions from file if cache path is provided
        if self.cache_file_path:
            self._load_cache_from_file()
        
        self.logger.info("SmartAggregationDecider initialized with LLM support")
        
    def _load_user_config(self, config_path: str) -> Dict:
        """Load user-defined column semantics from YAML config."""
        try:
            with open(config_path, 'r') as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            self.logger.warning(f"Could not load config from {config_path}: {e}")
            return {}
    
    def _load_cache_from_file(self):
        """Load cached aggregation decisions from JSON file."""
        try:
            if not Path(self.cache_file_path).exists():
                self.logger.info(f"[CACHE_LOAD] No cache file found at {self.cache_file_path}, starting with empty cache")
                return
            
            with open(self.cache_file_path, 'r') as f:
                cached_decisions = json.load(f)
            
            # Validate that it's a dictionary
            if not isinstance(cached_decisions, dict):
                self.logger.warning(f"[CACHE_LOAD] Invalid cache file format, expected dict, got {type(cached_decisions)}")
                return
            
            # Load into decision_store
            self.decision_store = cached_decisions
            self.logger.info(f"[CACHE_LOAD] Loaded {len(self.decision_store)} cached decisions from {self.cache_file_path}")
            
        except json.JSONDecodeError as e:
            self.logger.error(f"[CACHE_LOAD] Failed to parse cache file {self.cache_file_path}: {e}")
            self.logger.info("[CACHE_LOAD] Starting with empty cache")
        except Exception as e:
            self.logger.error(f"[CACHE_LOAD] Error loading cache file {self.cache_file_path}: {e}")
            self.logger.info("[CACHE_LOAD] Starting with empty cache")
    
    def _save_cache_to_file(self):
        """Save cached aggregation decisions to JSON file."""
        if not self.cache_file_path:
            return
        
        try:
            with open(self.cache_file_path, 'w') as f:
                json.dump(self.decision_store, f, indent=2)
            
            self.logger.debug(f"[CACHE_SAVE] Saved {len(self.decision_store)} decisions to {self.cache_file_path}")
            
        except Exception as e:
            self.logger.error(f"[CACHE_SAVE] Failed to save cache to {self.cache_file_path}: {e}")
            # Don't raise - cache save failure shouldn't break the main flow
    
    def analyze_column_with_query(self, df: pd.DataFrame, col: str) -> Dict[str, Any]:
        """
        Analyze column by querying actual data samples.
        
        Returns:
            Dict with column metadata including actual data samples
        """
        col_data = df[col]
        
        self.logger.debug(f"[COLUMN_ANALYSIS] Analyzing '{col}': dtype={col_data.dtype}, unique_ratio={col_data.nunique() / len(df):.2f}")
        
        # Get diverse samples
        sample_data = {
            'first_10': col_data.head(10).tolist(),
            'random_10': col_data.sample(min(10, len(df))).tolist() if len(df) > 0 else [],
            'unique_values': col_data.unique()[:20].tolist(),
            'value_counts': col_data.value_counts().head(10).to_dict()
        }
        
        metadata = {
            'name': col,
            'dtype': str(col_data.dtype),
            'total_rows': len(df),
            'non_null': col_data.notna().sum(),
            'unique_count': col_data.nunique(),
            'unique_ratio': col_data.nunique() / len(df) if len(df) > 0 else 0,
            'actual_samples': sample_data,
        }
        
        # Add numeric stats if applicable
        if pd.api.types.is_numeric_dtype(col_data):
            metadata.update({
                'min': float(col_data.min()) if col_data.notna().any() else None,
                'max': float(col_data.max()) if col_data.notna().any() else None,
                'mean': float(col_data.mean()) if col_data.notna().any() else None,
                'median': float(col_data.median()) if col_data.notna().any() else None,
                'is_integer': pd.api.types.is_integer_dtype(col_data),
            })
            
            metadata['statistical_inference'] = self._infer_type_statistically(metadata)
        
        return metadata
    
    def _infer_type_statistically(self, metadata: Dict) -> Dict[str, str]:
        """Statistical inference about column type."""
        unique_ratio = metadata['unique_ratio']
        is_integer = metadata.get('is_integer', False)
        unique_count = metadata['unique_count']
        col_name = metadata['name'].lower()
        
        self.logger.debug(f"[STATISTICAL_INFERENCE] Column '{metadata['name']}': unique_ratio={unique_ratio:.2f}, is_integer={is_integer}, unique_count={unique_count}")
        
        # Check column name patterns
        if any(pattern in col_name for pattern in ['_id', 'id_', '_number']):
            inference = {
                'type': 'likely_identifier',
                'suggested_agg': 'COUNT_DISTINCT',
                'confidence': 'high'
            }
            self.logger.debug(f"[STATISTICAL_INFERENCE] '{metadata['name']}' → IDENTIFIER (name pattern)")
            return inference
        
        if any(pattern in col_name for pattern in ['_count', 'count_', 'total_', 'num_']):
            inference = {
                'type': 'pre_aggregated_measure',
                'suggested_agg': 'SUM',
                'confidence': 'high',
                'note': 'Column name suggests pre-aggregated counts'
            }
            self.logger.debug(f"[STATISTICAL_INFERENCE] '{metadata['name']}' → PRE-AGGREGATED (name pattern)")
            return inference
        
        # High uniqueness suggests identifier
        if unique_ratio > 0.9:
            inference = {
                'type': 'likely_identifier',
                'suggested_agg': 'COUNT_DISTINCT',
                'confidence': 'high'
            }
            self.logger.debug(f"[STATISTICAL_INFERENCE] '{metadata['name']}' → IDENTIFIER (high uniqueness)")
            return inference
        
        # Low uniqueness cases
        if unique_ratio < 0.1:
            if unique_count <= 2:
                inference = {
                    'type': 'binary_indicator',
                    'suggested_agg': 'SUM',
                    'confidence': 'medium'
                }
                self.logger.debug(f"[STATISTICAL_INFERENCE] '{metadata['name']}' → BINARY INDICATOR")
                return inference
            
            inference = {
                'type': 'likely_categorical_or_preaggregated',
                'suggested_agg': 'AMBIGUOUS',
                'confidence': 'low',
                'note': f'Low cardinality ({unique_count} values) - need to examine actual data'
            }
            self.logger.debug(f"[STATISTICAL_INFERENCE] '{metadata['name']}' → AMBIGUOUS (low cardinality)")
            return inference
        
        # Continuous numeric
        if not is_integer:
            inference = {
                'type': 'likely_measure',
                'suggested_agg': 'AVG',
                'confidence': 'medium'
            }
            self.logger.debug(f"[STATISTICAL_INFERENCE] '{metadata['name']}' → MEASURE (float)")
            return inference
        
        inference = {
            'type': 'unknown',
            'suggested_agg': 'SUM',
            'confidence': 'low'
        }
        self.logger.debug(f"[STATISTICAL_INFERENCE] '{metadata['name']}' → UNKNOWN (default)")
        return inference
    
    def decide_aggregation(self, 
                          query: str, 
                          column: str, 
                          df: pd.DataFrame,
                          data_source_name: Optional[str] = None,
                          chart_name: Optional[str] = None,
                          tableau_hint: Optional[str] = None) -> Dict[str, Any]:
        """
        Main method to decide appropriate aggregation for a column in a query.
        
        Args:
            query: User's natural language query
            column: Column name to aggregate
            df: DataFrame containing the data
            data_source_name: Optional name for config lookup
            chart_name: Optional chart/worksheet name for loading tableau hints
            tableau_hint: Optional explicit Tableau aggregation hint (highest priority)
            
        Returns:
            Dict with 'aggregation', 'reasoning', 'confidence', 'column_type'
        """
        self.logger.info(f"[AGGREGATION_DECISION] Deciding aggregation for column '{column}' in query: '{query}'")
        
        # HIGHEST PRIORITY: Explicit Tableau hint passed directly
        if tableau_hint:
            self.logger.info(f"[TABLEAU_HINT] Using explicit Tableau aggregation hint for '{column}': {tableau_hint}")
            decision = {
                'aggregation': tableau_hint.upper(),
                'reasoning': f"Explicit Tableau aggregation hint from column name prefix",
                'confidence': 'very_high',
                'source': 'tableau_hint_explicit',
                'column_type': 'tableau_defined'
            }
            self.decision_store[column] = decision
            self._save_cache_to_file()
            return decision
        
        # HIGH PRIORITY: Tableau hint from hints manager (if chart_name provided)
        if chart_name:
            stored_hint = self.hints_manager.get_hint(chart_name, column)
            if stored_hint:
                self.logger.info(f"[TABLEAU_HINT] Using stored Tableau hint for '{chart_name}' / '{column}': {stored_hint}")
                decision = {
                    'aggregation': stored_hint.upper(),
                    'reasoning': f"Tableau aggregation hint from chart '{chart_name}' column definition",
                    'confidence': 'very_high',
                    'source': 'tableau_hint_stored',
                    'column_type': 'tableau_defined'
                }
                self.decision_store[column] = decision
                self._save_cache_to_file()
                return decision
        
        # Check if already decided for this column
        if column in self.decision_store:
            stored = self.decision_store[column]
            self.logger.debug(f"[AGGREGATION_DECISION] Using cached decision for '{column}': {stored['aggregation']}")
            # Re-evaluate based on current query
            return self._adjust_for_query(stored, query)
        
        # Layer 1: Check user-defined config
        if data_source_name and data_source_name in self.user_config:
            if column in self.user_config[data_source_name].get('columns', {}):
                config = self.user_config[data_source_name]['columns'][column]
                decision = {
                    'aggregation': config['aggregation'],
                    'reasoning': f"User-defined rule: {config.get('description', 'N/A')}",
                    'confidence': 'highest',
                    'source': 'user_config',
                    'column_type': 'user_defined'
                }
                self.decision_store[column] = decision
                self._save_cache_to_file()
                self.logger.info(f"[AGGREGATION_DECISION] '{column}': {decision['aggregation']} (source: user_config)")
                return decision
        
        # Layer 2: Analyze with actual data samples
        col_metadata = self.analyze_column_with_query(df, column)
        
        # Layer 3: LLM decides with full context
        decision = self._llm_decide(query, col_metadata)
        self.decision_store[column] = decision
        self._save_cache_to_file()
        
        self.logger.info(f"[AGGREGATION_DECISION] '{column}': {decision['aggregation']} (confidence: {decision['confidence']})")
        self.logger.debug(f"[AGGREGATION_REASONING] {decision['reasoning']}")
        
        return decision

        
        # Layer 2: Analyze with actual data samples
        col_metadata = self.analyze_column_with_query(df, column)
        
        # Layer 3: LLM decides with full context
        decision = self._llm_decide(query, col_metadata)
        self.decision_store[column] = decision
        self._save_cache_to_file()
        
        self.logger.info(f"[AGGREGATION_DECISION] '{column}': {decision['aggregation']} (confidence: {decision['confidence']})")
        self.logger.debug(f"[AGGREGATION_REASONING] {decision['reasoning']}")
        
        return decision
    
    def _adjust_for_query(self, stored_decision: Dict, query: str) -> Dict[str, Any]:
        """Adjust stored decision based on query keywords."""
        query_lower = query.lower()
        base_agg = stored_decision['aggregation']
        col_type = stored_decision.get('column_type', 'unknown')
        
        self.logger.debug(f"[QUERY_ADJUSTMENT] Checking query keywords for adjustment: base_agg={base_agg}, col_type={col_type}")
        
        # Query keyword overrides
        if any(word in query_lower for word in ['average', 'avg', 'mean']):
            if base_agg in ['SUM', 'COUNT'] and col_type in ['pre_aggregated_measure', 'measure']:
                adjusted_decision = {
                    **stored_decision,
                    'aggregation': 'AVG',
                    'reasoning': f"Query asks for average. {stored_decision['reasoning']}",
                    'query_override': True
                }
                self.logger.info(f"[QUERY_ADJUSTMENT] Adjusted to AVG based on query keyword")
                return adjusted_decision
        
        elif any(word in query_lower for word in ['total', 'sum of']):
            if base_agg == 'AVG' and col_type == 'pre_aggregated_measure':
                adjusted_decision = {
                    **stored_decision,
                    'aggregation': 'SUM',
                    'reasoning': f"Query asks for total. {stored_decision['reasoning']}",
                    'query_override': True
                }
                self.logger.info(f"[QUERY_ADJUSTMENT] Adjusted to SUM based on query keyword")
                return adjusted_decision
        
        return stored_decision
    
    def _llm_decide(self, query: str, col_metadata: Dict) -> Dict[str, Any]:
        """Use LLM to decide aggregation based on query context."""
        if not self.smart_aggregation_enabled:
            self.logger.debug("[LLM_DECISION] Smart aggregation disabled, using statistical fallback")
            stat_inference = col_metadata.get('statistical_inference', {})
            return {
                'aggregation': stat_inference.get('suggested_agg', 'SUM'),
                'reasoning': f"Smart aggregation disabled. Statistical inference: {stat_inference.get('type', 'unknown')}",
                'confidence': 'low',
                'source': 'statistical_fallback',
                'column_type': stat_inference.get('type', 'unknown')
            }
        
        prompt = self._build_decision_prompt(query, col_metadata)
        
        try:
            self.logger.debug(f"[LLM_DECISION] Calling LLM for column '{col_metadata['name']}'")
            
            # Call LLM with OpenAI format
            response = self.llm.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                temperature=0.1
            )
            
            response_text = response.choices[0].message.content
            
            # Extract JSON from response
            # Sometimes LLM wraps JSON in code blocks
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()
            
            result = json.loads(response_text)
            result['source'] = 'llm'
            
            # Infer column type from reasoning
            reasoning_lower = result.get('reasoning', '').lower()
            if 'identifier' in reasoning_lower or 'id' in reasoning_lower:
                result['column_type'] = 'identifier'
            elif 'pre-aggregated' in reasoning_lower or 'pre aggregated' in reasoning_lower:
                result['column_type'] = 'pre_aggregated_measure'
            elif 'categorical' in reasoning_lower:
                result['column_type'] = 'categorical'
            else:
                result['column_type'] = 'measure'
            
            self.logger.info(f"[LLM_DECISION] LLM decided: {result['aggregation']} with {result['confidence']} confidence")
            self.logger.debug(f"[LLM_REASONING] {result['reasoning']}")
            
            return result
            
        except Exception as e:
            self.logger.error(f"[LLM_DECISION] LLM decision failed: {e}")
            stat_inference = col_metadata.get('statistical_inference', {})
            return {
                'aggregation': stat_inference.get('suggested_agg', 'SUM'),
                'reasoning': f"Fallback to statistical inference: {stat_inference.get('type', 'unknown')}",
                'confidence': 'low',
                'source': 'statistical_fallback',
                'column_type': stat_inference.get('type', 'unknown')
            }
    
    def _build_decision_prompt(self, query: str, col_metadata: Dict) -> str:
        """Build prompt for LLM aggregation decision."""
        samples = col_metadata['actual_samples']
        stat_inference = col_metadata.get('statistical_inference', {})
        
        ambiguity_note = ""
        if stat_inference.get('suggested_agg') in ['AMBIGUOUS', 'NEEDS_LLM_DECISION']:
            ambiguity_note = f"""
AMBIGUOUS COLUMN DETECTED:
{stat_inference.get('note', '')}

You must examine the actual sample values carefully to determine the column's true nature.
"""
        
        return f"""You are an expert data analyst. Determine the appropriate aggregation function.

USER QUERY: "{query}"
COLUMN: {col_metadata['name']}

ACTUAL DATA IN THIS COLUMN:
- First 10 values: {samples['first_10']}
- Random 10 values: {samples['random_10']}
- Unique values (up to 20): {samples['unique_values']}
- Value frequency: {json.dumps(samples['value_counts'], indent=2)}

METADATA:
- Total rows: {col_metadata['total_rows']}
- Unique count: {col_metadata['unique_count']} ({col_metadata['unique_ratio']:.1%})
- Data type: {col_metadata['dtype']}
{self._format_numeric_stats(col_metadata)}

{ambiguity_note}

STATISTICAL HINT: {stat_inference}

CRITICAL EXAMPLES:

1. Column "outbound_email_count" with values ['0','1','2','3','5']
   Query: "count of outbound_email_count by product"
   → Values are PRE-AGGREGATED COUNTS (each row already contains a count)
   → Decision: SUM (to get total across all rows)

2. Column "outbound_email" with values ['john@co.com', 'jane@co.com']  
   Query: "count of outbound_email by product"
   → Values are EMAIL ADDRESSES (categorical)
   → Decision: COUNT or COUNT_DISTINCT

3. Column "ticket_id" with 98% unique values
   Query: "count of ticket_id by month"
   → High cardinality identifier
   → Decision: COUNT_DISTINCT

DECISION RULES:
1. Check column name for patterns (_count, _id, _total)
2. Examine actual values - are they IDs, categories, or numbers?
3. Match query intent ("count", "total", "average") with data type
4. Pre-aggregated counts should be SUMMED when grouping

OUTPUT (JSON only):
{{
    "aggregation": "COUNT|SUM|AVG|COUNT_DISTINCT|MEDIAN|MIN|MAX",
    "reasoning": "Explain what you see in the data and why this aggregation fits",
    "confidence": "high|medium|low"
}}"""

    def _format_numeric_stats(self, metadata: Dict) -> str:
        """Format numeric statistics for prompt."""
        if 'min' in metadata:
            return f"""
- Numeric range: {metadata.get('min')} to {metadata.get('max')}
- Mean: {metadata.get('mean'):.2f}, Median: {metadata.get('median'):.2f}
- Is integer: {metadata.get('is_integer')}"""
        return ""
    
    def apply_aggregation(self, df: pd.DataFrame, column: str, 
                         group_by: List[str], query: str,
                         data_source_name: Optional[str] = None) -> tuple:
        """
        Apply aggregation to dataframe based on stored decision.
        
        Returns:
            (result_df, aggregation_used, decision_dict)
        """
        # Get or create decision
        decision = self.decide_aggregation(query, column, df, data_source_name)
        agg_type = decision['aggregation']
        agg_func = self.mapper.get_aggregation_function(agg_type)
        
        # Convert column to numeric if it's stored as string (COERCION LOGIC)
        df_copy = df.copy()
        if df_copy[column].dtype == 'object':
            self.logger.debug(f"[COERCION] Column '{column}' dtype=object → converting with pd.to_numeric(errors='coerce')")
            df_copy[column] = pd.to_numeric(df_copy[column], errors='coerce').fillna(0)
            self.logger.debug(f"[COERCION] Conversion complete. New dtype: {df_copy[column].dtype}")
        
        # Apply aggregation
        if group_by:
            result = df_copy.groupby(group_by)[column].agg(agg_func).reset_index()
            self.logger.info(f"[AGGREGATION_APPLIED] Grouped by {group_by}, aggregated '{column}' with {agg_type}")
        else:
            result = pd.DataFrame({column: [agg_func(df_copy[column])]})
            self.logger.info(f"[AGGREGATION_APPLIED] Aggregated '{column}' with {agg_type} (no grouping)")
        
        return result, agg_type, decision
    
    def generate_code(self, query: str, column: str, group_by: List[str],
                     data_source_name: Optional[str] = None,
                     file_path: str = 'data.csv') -> str:
        """
        Generate pandas code with appropriate aggregation.
        
        Args:
            query: User query
            column: Column to aggregate
            group_by: Columns to group by
            data_source_name: Data source name
            file_path: Path to CSV file
            
        Returns:
            Python code as string
        """
        # Get decision (from store or make new one based on query)
        if column in self.decision_store:
            decision = self._adjust_for_query(self.decision_store[column], query)
        else:
            # Can't make decision without DataFrame, use query keywords
            decision = self._guess_from_query(query, column)
        
        agg_str = self.mapper.get_aggregation_string(decision['aggregation'])
        group_by_str = str(group_by)
        
        self.logger.info(f"[CODE_GENERATION] Generating code for '{column}' with {decision['aggregation']}")
        
        code = f"""import pandas as pd

# Load data
df = pd.read_csv('{file_path}')

# Convert to numeric (handle string-stored numbers)
df['{column}'] = pd.to_numeric(df['{column}'], errors='coerce').fillna(0)

# Group and aggregate
# Using {decision['aggregation']} because: {decision['reasoning']}
result = df.groupby({group_by_str})['{column}']{agg_str}.reset_index()

# Sort results
result = result.sort_values('{column}', ascending=False)

print(result)
"""
        return code
    
    def _guess_from_query(self, query: str, column: str) -> Dict:
        """Guess aggregation from query keywords when DataFrame not available."""
        query_lower = query.lower()
        col_lower = column.lower()
        
        self.logger.debug(f"[GUESS_FROM_QUERY] Guessing aggregation from query keywords for '{column}'")
        
        # Check column name
        if '_count' in col_lower or 'total_' in col_lower:
            base_agg = 'SUM'
            col_type = 'pre_aggregated_measure'
        elif '_id' in col_lower or 'id_' in col_lower:
            base_agg = 'COUNT_DISTINCT'
            col_type = 'identifier'
        else:
            base_agg = 'SUM'
            col_type = 'unknown'
        
        # Override based on query
        if 'average' in query_lower or 'avg' in query_lower:
            agg = 'AVG'
        elif 'count' in query_lower and col_type == 'identifier':
            agg = 'COUNT_DISTINCT'
        else:
            agg = base_agg
        
        return {
            'aggregation': agg,
            'reasoning': f'Inferred from query keywords and column name',
            'confidence': 'medium',
            'column_type': col_type
        }
    
    def validate_aggregation(self, code: str, query: str, 
                            decisions: List[Dict]) -> List[str]:
        """Validate generated code uses appropriate aggregations."""
        issues = []
        code_lower = code.lower()
        
        self.logger.debug(f"[VALIDATION] Validating aggregation code")
        
        for decision in decisions:
            col = decision.get('column', '')
            expected_agg = decision.get('aggregation', '')
            col_type = decision.get('column_type', '')
            
            # Check mismatches
            if expected_agg == 'COUNT_DISTINCT' and 'sum(' in code_lower:
                issues.append(
                    f"ERROR: SUM used on identifier '{col}'. Expected COUNT_DISTINCT."
                )
            
            if col_type == 'pre_aggregated_measure' and expected_agg == 'SUM':
                if 'mean(' in code_lower or 'avg(' in code_lower:
                    issues.append(
                        f"WARNING: AVG used on pre-aggregated count '{col}'. "
                        f"Verify this is intentional."
                    )
        
        # Query-code alignment
        if 'average' in query.lower() and 'mean(' not in code_lower:
            issues.append("WARNING: Query asks for 'average' but code doesn't use mean()")
        
        if issues:
            for issue in issues:
                self.logger.warning(f"[VALIDATION] {issue}")
        else:
            self.logger.info(f"[VALIDATION] Code validation passed")
        
        return issues
    
    def generate_schema_description(self, df: pd.DataFrame, 
                                    data_source_name: str) -> str:
        """Generate enhanced schema description for prompts."""
        descriptions = []
        
        self.logger.debug(f"[SCHEMA_DESCRIPTION] Generating schema description for data source '{data_source_name}'")
        
        for col in df.columns:
            metadata = self.analyze_column_with_query(df, col)
            stat_inference = metadata.get('statistical_inference', {})
            
            config_desc = ""
            if data_source_name in self.user_config:
                if col in self.user_config[data_source_name].get('columns', {}):
                    config = self.user_config[data_source_name]['columns'][col]
                    config_desc = f" [CONFIG: {config['aggregation']}]"
            
            descriptions.append(
                f"- {col} ({metadata['dtype']}): "
                f"{metadata['unique_count']} unique ({metadata['unique_ratio']:.1%}) "
                f"→ {stat_inference.get('suggested_agg', 'SUM')}"
                f"{config_desc}"
            )
        
        return "\n".join(descriptions)
    
    def clear_decision_cache(self):
        """Clear the decision cache."""
        self.logger.info("[CACHE] Clearing decision cache")
        self.decision_store.clear()
    
    def get_decision_cache(self) -> Dict:
        """Get the current decision cache."""
        return self.decision_store.copy()
    
    def set_smart_aggregation_enabled(self, enabled: bool):
        """Enable or disable smart aggregation."""
        self.smart_aggregation_enabled = enabled
        self.logger.info(f"[CONFIG] Smart aggregation {'enabled' if enabled else 'disabled'}")
    
    def set_confidence_threshold(self, threshold: str):
        """Set confidence threshold for automatic application."""
        self.confidence_threshold = threshold
        self.logger.info(f"[CONFIG] Confidence threshold set to '{threshold}'")

