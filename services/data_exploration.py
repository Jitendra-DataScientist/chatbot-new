"""
Data Exploration Service
Consolidated service for handling exploration-intent queries
Cloned from query_understanding_agent.py ORIGINAL FLOW (lines 559-636)
"""

import json
import logging
import time
import os
import asyncio
import traceback
from typing import Dict, List, Any, Optional
import pandas as pd
import numpy as np
import re

# Import master logger
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from master_logger import setup_module_logger

# Import services
from services.llm_service import LLMService
from services.data_processor import TableauDataProcessor
from services.visualization_service import IntelligentVisualizationService
from services.fuzzy_column_matcher import FuzzyColumnMatcher
from services.multi_table_service import TableauMultiTableService
from services.insight_generator import TableauInsightGenerator
from services.NL_to_python import NLToPythonGenerator
from services.enhanced_analysis_service import EnhancedAnalysisService
# from services.codet5_service import CodeT5Service
from services.period_extraction_service import PeriodExtractionService

# Setup logger
master_logger = setup_module_logger('services.data_exploration')
master_logger.info("DATA EXPLORATION SERVICE MODULE INITIALIZATION STARTED")


class data_exploration:
    """
    Self-contained class for data exploration queries (original_intent == 'exploration').
    
    Consolidates logic from query_understanding_agent.py ORIGINAL FLOW section.
    """
    
    def __init__(self, llm_client, smart_agg_decider, cache_path: str = "causal_analysis_cache.json"):
        """
        Initialize the data_exploration class.
        
        Args:
            llm_client: OpenAI client instance
            smart_agg_decider: Smart aggregation decision service
            cache_path: Path to causal analysis cache file
        """
        master_logger.info("=== INITIALIZING DATA EXPLORATION SERVICE ===")
        
        self.llm_client = llm_client
        self.smart_aggregation_decider = smart_agg_decider
        self.cache_path = cache_path
        
        # Initialize services (same as QueryAgent)
        self.llm_service = LLMService(openai_client=llm_client)
        self.data_processor = TableauDataProcessor(openai_client=llm_client)
        self.viz_service = IntelligentVisualizationService()
        self.multi_table_service = TableauMultiTableService(openai_client=llm_client)
        self.insight_generator = TableauInsightGenerator()
        self.nl_to_python = NLToPythonGenerator(openai_client=llm_client)
        self.enhanced_analysis = EnhancedAnalysisService(self.data_processor, self.nl_to_python)
        self.fuzzy_matcher = FuzzyColumnMatcher(threshold=70)  # Lowered to 70% for better misspelling tolerance
        
        # Initialize period extraction service
        self.period_extractor = PeriodExtractionService(
            model_path="event-period-ner-bert"
        )
        master_logger.info("✓ Period extraction service initialized")
        
        master_logger.info("✓ All services initialized")
        master_logger.info("data_exploration class initialized successfully")
    
    async def process(self, 
                query_text: str,
                csv_data: pd.DataFrame,
                selected_chart: str,
                intent_result = None,
                chart_context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Process exploration query through the complete analysis pipeline.
        EXACT CLONE of query_understanding_agent.py lines 559-636 (ORIGINAL FLOW)
        
        Args:
            query_text: User's natural language query
            csv_data: DataFrame containing the data to analyze
            selected_chart: Name of the selected chart
            intent_result: QueryIntent object from classification
            chart_context: Optional pre-loaded chart context
            
        Returns:
            Dictionary with success, response, computational_results, needs_visualization, etc.
        """
        master_logger.info("=" * 80)
        master_logger.info("=== PROCESSING DATA EXPLORATION QUERY ===")
        master_logger.info(f"Query: '{query_text}'")
        master_logger.info(f"Selected chart: {selected_chart}")
        master_logger.info(f"CSV data available: {csv_data is not None}")
        
        if csv_data is not None:
            master_logger.info(f"Original CSV data shape: {csv_data.shape if hasattr(csv_data, 'shape') else 'not a DataFrame'}")
            master_logger.info(f"Original CSV columns: {list(csv_data.columns) if hasattr(csv_data, 'columns') else 'N/A'}")
        
        start_time = time.time()
        
        try:
            # Store original CSV for CodeT5 column extraction (needs all 87 columns)
            self.original_csv_data = csv_data
            
            # Apply numeric coercion to the full CSV upfront
            # This ensures all columns (not just filtered ones) are properly typed
            # so any column the model references will have correct dtypes
            #if self.original_csv_data is not None:
            #    master_logger.info("STEP 1.5: Applying numeric coercion to full CSV")
            #    self.original_csv_data = self._parse_and_coerce_numeric_columns(self.original_csv_data)
            #    master_logger.info("STEP 1.5: Numeric coercion complete for full CSV")
            
            # ========== ORIGINAL FLOW (EXACT CLONE from lines 559-636) ==========
            # Step 2: Apply column cleaning and load chart context
            master_logger.info("STEP 2: Data filtering and chart context loading")
            analysis_data = csv_data
            chart_context = None
            
            if csv_data is not None and selected_chart:
                master_logger.info(f"Loading chart context for: {selected_chart}")
                analysis_data, chart_context = self._apply_column_cleaning(csv_data, selected_chart, query_text)
                
                if chart_context:
                    chart_context['chart_name'] = selected_chart
                    master_logger.info(f"Chart context loaded successfully:")
                    master_logger.info(f"  - Domain: {chart_context.get('domain_type', 'Unknown')}")
                    master_logger.info(f"  - Top features: {chart_context.get('top_5_features', [])}")
                    master_logger.info(f"  - X/Y axes: {chart_context.get('x_axis_detected')} / {chart_context.get('y_axis_detected')}")
                    
                    if hasattr(analysis_data, 'shape'):
                        master_logger.info(f"  - Filtered data shape: {analysis_data.shape}")
                        master_logger.info(f"  - Filtered columns: {list(analysis_data.columns)}")
                else:
                    master_logger.warning(f"No chart context found for: {selected_chart}")
            else:
                master_logger.info("No chart context loading - using full CSV data")
            
            # Step 3: Execute enhanced analysis
            master_logger.info("STEP 3: Executing enhanced analysis")
            analysis_result = await self._execute_intent_analysis(
                intent_result, query_text, analysis_data, chart_context)
            
            master_logger.info(f"Analysis completed. Success: {analysis_result.get('success', False)}")
            
            # Step 4: Generate visualization if needed (skip for scalar results)
            master_logger.info("STEP 4: Checking visualization requirements")
            visualization = None
            
            # Check if result is scalar or singular value (single row) - no visualization needed
            is_scalar_result = False
            if analysis_result and analysis_result.get('pandas_execution'):
                result_type = analysis_result['pandas_execution'].get('result', {}).get('type')
                result_shape = analysis_result['pandas_execution'].get('result', {}).get('shape', (0, 0))
                
                # Check for both true scalars and singular value DataFrames (1 row = singular answer)
                is_scalar_result = (result_type == 'scalar') or (result_type == 'dataframe' and result_shape[0] == 1)
                
                if is_scalar_result:
                    if result_type == 'scalar':
                        master_logger.info("Result is scalar - skipping visualization")
                    else:
                        master_logger.info(f"Result is singular value DataFrame (shape={result_shape}) - skipping visualization")
            
            if analysis_result and self._requires_visualization(intent_result) and not is_scalar_result:
                master_logger.info("Generating visualization")
                visualization = await self._create_visualization_for_intent(intent_result, analysis_result, analysis_data)
            else:
                if is_scalar_result:
                    master_logger.info("No visualization for scalar result")
                else:
                    master_logger.info("No visualization required for this intent")
            
            # Step 5: Format table response (removed LLM verbose generation)
            master_logger.info("STEP 5: Formatting table response")
            response_text = self._format_table_response(analysis_result)
            
            execution_time = time.time() - start_time
            
            # Step 6: Log data exploration query to Google Sheets
            self._log_data_exploration_to_sheets(
                query_text=query_text,
                generated_code=analysis_result.get('pandas_execution', {}).get('generated_code', ''),
                generated_answer=response_text,
                execution_status='success',
                chart_selected=selected_chart or 'none',
                data_shape=f"{csv_data.shape}" if csv_data is not None else 'unknown'
            )
            
            master_logger.info("=== DATA EXPLORATION PROCESSING COMPLETED SUCCESSFULLY ===")
            master_logger.info("=" * 80)
            
            return {
                "success": True,
                "response": response_text,
                "computational_results": analysis_result,
                "needs_visualization": visualization is not None if visualization else False,
                "chart_type": visualization.get('chart_type') if visualization else None,
                "chart_image": visualization.get('chart_image') if visualization else None,
                "chart_context": chart_context,
                "execution_time": execution_time
            }
            
        except Exception as e:
            master_logger.error("=" * 80)
            master_logger.error("=== DATA EXPLORATION PROCESSING FAILED ===")
            master_logger.error(f"Error: {e}")
            master_logger.error(f"Error type: {type(e).__name__}")
            master_logger.error(f"Traceback: {traceback.format_exc()}")
            master_logger.error("=" * 80)
            
            execution_time = time.time() - start_time
            
            # Log failed data exploration query to Google Sheets
            self._log_data_exploration_to_sheets(
                query_text=query_text,
                generated_code='',
                generated_answer=f"Error: {str(e)}",
                execution_status='failed',
                chart_selected=selected_chart or 'none',
                data_shape=f"{csv_data.shape}" if csv_data is not None else 'unknown'
            )
            
            return {
                "success": False,
                "response": f"I encountered an error processing your query: {str(e)}",
                "error": True,
                "execution_time": execution_time
            }
    
    def _load_causal_cache(self, chart_name):
        """Load causal analysis cache with comprehensive logging"""
        master_logger.info(f"Loading causal cache for chart: {chart_name}")
        
        try:
            import json
            import os
            
            cache_file = self.cache_path
            
            if not os.path.exists(cache_file):
                master_logger.warning(f"Causal cache file not found: {cache_file}")
                return None
            
            with open(cache_file, 'r') as f:
                cache_data = json.load(f)
            
            master_logger.info(f"Cache file loaded. Available charts: {list(cache_data.keys())}")
            
            if chart_name in cache_data:
                chart_cache = cache_data[chart_name]
                master_logger.info(f"Cache found for chart '{chart_name}':")
                master_logger.info(f"  - Domain: {chart_cache.get('domain_type', 'Unknown')}")
                master_logger.info(f"  - Features: {chart_cache.get('top_5_features', [])}")
                master_logger.info(f"  - Timestamp: {chart_cache.get('timestamp', 'Unknown')}")
                master_logger.info(f"  - Feature details count: {len(chart_cache.get('feature_details', []))}")
                return chart_cache
            else:
                master_logger.warning(f"No cache entry found for chart: {chart_name}")
                master_logger.info(f"Available charts in cache: {list(cache_data.keys())}")
                return None
                
        except Exception as e:
            master_logger.error(f"Error loading causal cache: {e}")
            return None

    def _extract_query_column_terms(self, query: str) -> List[str]:
        """
        Extract potential column names from user's query using hybrid approach:
        1. Pattern matching for high-confidence extractions (aggregations, etc.)
        2. Token-based extraction with stopword filtering for maximum recall
        
        Examples:
        - "give me count of each product across months" → ['product', 'count', 'months']
        - "total_outbound_emails" from "give count of total_outbound_emails product wise" → ['total_outbound_emails', 'product']
        - "inbound email count by type" → ['inbound', 'email', 'count', 'type']
        
        Args:
            query: User's natural language query
            
        Returns:
            List of potential column name terms extracted from query
        """
        import re
        
        master_logger.info(f"[QUERY_COLUMN_EXTRACTION] Extracting column terms from query: '{query}'")
        
        extracted_terms = []
        query_lower = query.lower()
        
        # PHASE 1: High-confidence pattern matching (keep existing logic)
        aggregation_patterns = [
            r'count\s+of\s+(\w+(?:_\w+)*)',
            r'sum\s+of\s+(\w+(?:_\w+)*)',
            r'total\s+of\s+(\w+(?:_\w+)*)',
            r'average\s+of\s+(\w+(?:_\w+)*)',
            r'avg\s+of\s+(\w+(?:_\w+)*)',
            r'max\s+of\s+(\w+(?:_\w+)*)',
            r'min\s+of\s+(\w+(?:_\w+)*)',
            r'(\w+(?:_\w+)*)\s+count',
            r'(\w+(?:_\w+)*)\s+total',
            r'(\w+(?:_\w+)*)\s+sum',
        ]
        
        for pattern in aggregation_patterns:
            matches = re.findall(pattern, query_lower)
            for match in matches:
                if match and len(match) > 2:
                    if match not in extracted_terms:
                        extracted_terms.append(match)
                        master_logger.info(f"[QUERY_COLUMN_EXTRACTION] Found via aggregation pattern: '{match}'")
        
        # Pattern 2: Multi-word terms with underscores (column-like naming)
        underscore_terms = re.findall(r'\b(\w+_\w+(?:_\w+)*)\b', query_lower)
        for term in underscore_terms:
            if term not in extracted_terms and len(term) > 3:
                extracted_terms.append(term)
                master_logger.info(f"[QUERY_COLUMN_EXTRACTION] Found underscore term: '{term}'")
        
        # PHASE 2: NEW - Token-based extraction with comprehensive stopword filtering
        master_logger.info(f"[QUERY_COLUMN_EXTRACTION] Starting token-based extraction (NEW)")
        
        stopwords = {
            'the', 'a', 'an', 'this', 'that', 'these', 'those',
            'give', 'show', 'find', 'get', 'list', 'display', 'tell', 'see',
            'have', 'has', 'had', 'been', 'will', 'would', 'could', 'should', 'can',
            'of', 'in', 'on', 'at', 'to', 'for', 'with', 'from', 'by',
            'about', 'across', 'against', 'along', 'around', 'before', 'behind',
            'between', 'into', 'through', 'during', 'over', 'under',
            'and', 'or', 'but', 'nor', 'yet', 'so',
            'me', 'my', 'you', 'your', 'their', 'them', 'mine', 'yours', 'his', 'her', 'its',
            'what', 'where', 'when', 'which', 'who', 'whom', 'whose', 'why', 'how',
            'all', 'each', 'every', 'some', 'any', 'many', 'few', 'most', 'more', 'less',
            'there', 'here', 'wise', 'per', 'than', 'then', 'are', 'was', 'were', 'be',
            'not', 'out', 'up', 'down', 'off', 'only', 'just', 'like', 'such'
        }
        
        all_tokens = re.findall(r'\b(\w{3,})\b', query_lower)
        meaningful_tokens = [
            token for token in all_tokens 
            if token not in stopwords and token not in extracted_terms
        ]
        for token in meaningful_tokens:
            extracted_terms.append(token)
            master_logger.info(f"[QUERY_COLUMN_EXTRACTION] Found via token extraction: '{token}'")
        
        master_logger.info(f"[QUERY_COLUMN_EXTRACTION] Extracted {len(extracted_terms)} terms total: {extracted_terms}")
        return extracted_terms

    def _apply_column_cleaning(self, csv_data, selected_chart, query: str = None):
        """
        Load causal cache and filter CSV data to chart-relevant subset
        Enhanced to include query-mentioned columns that may not be in causal cache
        
        Args:
            csv_data: Original CSV DataFrame
            selected_chart: Chart name for cache lookup
            query: User's query (optional, for query-based column extraction)
        """
        master_logger.info("STEP 2A: Loading chart context and filtering data")
        
        cache_data = self._load_causal_cache(selected_chart)
        
        if cache_data:
            chart_columns = [cache_data.get('x_axis_detected'), cache_data.get('y_axis_detected')]
            top_features = cache_data.get('top_5_features', [])
            relevant_columns = list(set([col for col in chart_columns + top_features if col]))
            
            master_logger.info(f"[COLUMN_CLEANING] Cached columns to find: {relevant_columns}")
            master_logger.info(f"[COLUMN_CLEANING] Available CSV columns ({len(csv_data.columns)}): {list(csv_data.columns)}")
            
            # 🆕 NEW: Query-based enhancement
            query_matched_columns = []
            if query:
                master_logger.info("[COLUMN_CLEANING] 🆕 QUERY-BASED COLUMN ENHANCEMENT ACTIVATED")
                master_logger.info(f"[COLUMN_CLEANING] Query: '{query}'")
                
                query_terms = self._extract_query_column_terms(query)
                
                if query_terms:
                    master_logger.info(f"[COLUMN_CLEANING] Fuzzy matching {len(query_terms)} query terms against original CSV...")
                    
                    for term in query_terms:
                        fuzzy_match = self.fuzzy_matcher.find_best_match(
                            term,
                            list(csv_data.columns),
                            context=f"query_column_extraction|{selected_chart}",
                            query_context=query
                        )
                        
                        if fuzzy_match and fuzzy_match not in relevant_columns:
                            query_matched_columns.append(fuzzy_match)
                            master_logger.info(f"[COLUMN_CLEANING] ✅ ADDED query-matched column: '{term}' → '{fuzzy_match}'")
                        elif fuzzy_match and fuzzy_match in relevant_columns:
                            master_logger.info(f"[COLUMN_CLEANING] ℹ️ Query term '{term}' → '{fuzzy_match}' (already in causal cache)")
                        else:
                            master_logger.warning(f"[COLUMN_CLEANING] ❌ No fuzzy match found for query term: '{term}'")
                    
                    if query_matched_columns:
                        master_logger.info(f"[COLUMN_CLEANING] 🎯 Total query-matched columns added: {len(query_matched_columns)}: {query_matched_columns}")
                        relevant_columns = list(set(relevant_columns + query_matched_columns))
                        master_logger.info(f"[COLUMN_CLEANING] 📊 Enhanced column set: {len(relevant_columns)} columns (causal cache + query-matched)")
                else:
                    master_logger.info("[COLUMN_CLEANING] No potential column terms extracted from query")
            else:
                master_logger.info("[COLUMN_CLEANING] No query provided, skipping query-based column enhancement")
            
            available_columns = []
            csv_column_list = list(csv_data.columns)
            
            for cached_col in relevant_columns:
                if not cached_col:
                    continue
                if cached_col in csv_column_list:
                    available_columns.append(cached_col)
                    master_logger.info(f"[COLUMN_CLEANING] ✓ Exact match: '{cached_col}'")
                else:
                    fuzzy_match = self.fuzzy_matcher.find_best_match(
                        cached_col,
                        csv_column_list,
                        context=f"column_cleaning|{selected_chart}",
                        query_context=query
                    )
                    
                    if fuzzy_match:
                        available_columns.append(fuzzy_match)
                        master_logger.info(f"[COLUMN_CLEANING] ✓ Fuzzy matched cache column '{cached_col}' → CSV column '{fuzzy_match}'")
                        col_dtype = csv_data[fuzzy_match].dtype
                        master_logger.info(f"[TYPE_INFERENCE] Column '{fuzzy_match}' type: {col_dtype}")
                    else:
                        master_logger.warning(f"[COLUMN_CLEANING] ✗ No match found for cached column '{cached_col}'")
            
            if available_columns:
                filtered_data = csv_data[available_columns]
                master_logger.info(f"Filtered data to {len(available_columns)} chart-relevant columns: {available_columns}")
                master_logger.info(f"Filtered data shape: {filtered_data.shape}")
                return filtered_data, cache_data
            else:
                master_logger.warning("No relevant columns found in CSV data, using original data")
                return csv_data, cache_data
        else:
            master_logger.info("No chart context available, using original CSV data")
            return csv_data, None

    def _prepare_data_summary(self, data, chart_context):
        """Prepare domain-aware data summary for LLM context"""
        try:
            domain_type = chart_context.get('domain_type', 'General') if chart_context else 'General'
            basic_summary = f"""
DATASET OVERVIEW:
- Shape: {data.shape[0]} rows × {data.shape[1]} columns
- Columns: {list(data.columns)}
- Domain: {domain_type}

COLUMN ANALYSIS:"""

            column_insights = []
            for col in data.columns:
                try:
                    col_info = f"\n• {col}:"
                    if data[col].dtype in ['int64', 'float64']:
                        col_info += f" Numeric (range: {data[col].min():.2f} to {data[col].max():.2f})"
                    else:
                        unique_count = data[col].nunique()
                        col_info += f" Categorical ({unique_count} unique values)"
                        if unique_count <= 10:
                            top_values = data[col].value_counts().head(3)
                            col_info += f" | Top: {dict(top_values)}"
                    column_insights.append(col_info)
                except:
                    column_insights.append(f"\n• {col}: Analysis unavailable")
            
            sample_summary = f"""

SAMPLE DATA (First 3 rows):
{data.head(3).to_dict('records')}"""
            
            return basic_summary + "".join(column_insights) + sample_summary
            
        except Exception as e:
            master_logger.warning(f"Error preparing data summary: {e}")
            return f"Data summary unavailable: {data.shape if hasattr(data, 'shape') else 'Unknown shape'}"

    def _prepare_nl_summary(self, nl_result):
        """Format NL_to_python result for LLM consumption"""
        if not nl_result:
            return "No NL analysis result available"
        
        return f"""
Generated Code: {nl_result.get('generated_code', 'N/A')}
Operation Type: {nl_result.get('operation_type', 'N/A')}
Execution Status: {nl_result.get('execution_status', 'N/A')}
Result: {nl_result.get('result', 'N/A')}
Confidence: {nl_result.get('confidence', 'N/A')}
Explanation: {nl_result.get('explanation', 'N/A')}
"""

    def _format_chart_context(self, chart_context):
        """Format comprehensive chart context including domain and feature reasoning for LLM"""
        if not chart_context:
            return "No chart context"
        
        feature_reasoning = []
        for detail in chart_context.get('feature_details', []):
            feature_reasoning.append(f"• {detail['feature']} (Rank {detail['rank']}): {detail['llm_reasoning']}")
        
        context_summary = f"""
BUSINESS DOMAIN: {chart_context.get('domain_type', 'Unknown')}

CHART CONFIGURATION:
- Chart Name: {chart_context.get('chart_name', 'Unknown')}
- X-Axis (Independent): {chart_context.get('x_axis_detected', 'Unknown')}
- Y-Axis (Dependent): {chart_context.get('y_axis_detected', 'Unknown')}
- Detection Confidence: {chart_context.get('xy_detection_confidence', 'Unknown')}

TOP 5 IMPACTFUL FEATURES (with expert reasoning):
{chr(10).join(feature_reasoning) if feature_reasoning else 'No feature reasoning available'}

ANALYSIS TIMESTAMP: {chart_context.get('timestamp', 'Unknown')}
DATA SOURCE: {chart_context.get('csv_file_used', 'Unknown')}
"""
        return context_summary

    
    async def _execute_intent_analysis(self, intent_result, query: str, data, chart_context=None) -> Dict[str, Any]:
        """Execute analysis with optional LLM enhancement"""
        
        if data is None or data.empty:
            master_logger.warning("No data available for analysis")
            return {"error": "No data available for analysis"}
        
        intent_type = intent_result.primary_intent
        master_logger.info(f"Executing analysis for intent: {intent_type}")
        
        analysis_result = {
            "intent_type": intent_type,
            "query": query,
            "data_shape": data.shape if hasattr(data, 'shape') else None
        }
        
        try:
            if intent_type == "top_bottom_analysis":
                pandas_result = self.execute_pandas_aggregation_with_codet5(query, data, intent_result, chart_context)
                analysis_result["pandas_execution"] = pandas_result
                analysis_result["success"] = pandas_result.get('execution_status') == 'success'
                master_logger.info(f"[ANALYSIS] top_bottom_analysis execution status: {pandas_result.get('execution_status')}")
                
            elif intent_type == "comparison":
                pandas_result = self.execute_pandas_aggregation_with_codet5(query, data, intent_result, chart_context)
                analysis_result["pandas_execution"] = pandas_result
                analysis_result["success"] = pandas_result.get('execution_status') == 'success'
                master_logger.info(f"[ANALYSIS] comparison execution status: {pandas_result.get('execution_status')}")
                
            elif intent_type in ["statistical_significance", "correlation"]:
                statistical_summary = self.data_processor.calculate_statistical_summary(data)
                analysis_result["statistical_analysis"] = statistical_summary
                analysis_result["success"] = 'error' not in statistical_summary
                master_logger.info(f"[ANALYSIS] statistical analysis success: {analysis_result['success']}")
                
            elif intent_type == "trend_analysis":
                pandas_result = self.execute_pandas_aggregation_with_codet5(query, data, intent_result, chart_context)
                analysis_result["pandas_execution"] = pandas_result
                analysis_result["success"] = pandas_result.get('execution_status') == 'success'
                master_logger.info(f"[ANALYSIS] trend_analysis execution status: {pandas_result.get('execution_status')}")
                
            else:
                pandas_result = self.execute_pandas_aggregation_with_codet5(query, data, intent_result, chart_context)
                analysis_result["pandas_execution"] = pandas_result
                analysis_result["success"] = pandas_result.get('execution_status') == 'success'
                master_logger.info(f"[ANALYSIS] general exploration execution status: {pandas_result.get('execution_status')}")
            
            master_logger.info(f"Analysis completed for intent: {intent_type}")
            return analysis_result
            
        except Exception as e:
            master_logger.error(f"Error executing intent analysis: {e}")
            return {"error": str(e), "intent_type": intent_type}
    
    def _requires_visualization(self, intent_result) -> bool:
        """Determine if visualization is needed based on intent"""
        visualization_intents = [
            "trend_analysis", "anomaly_detection", "top_bottom_analysis", 
            "comparison", "seasonality", "data_exploration", "shap_analysis"
        ]
        return intent_result.primary_intent in visualization_intents
    
    async def _create_visualization_for_intent(self, intent_result, analysis_result, data):
        """Create visualization based on intent"""
        try:
            query = analysis_result.get("query", "")
            master_logger.info(f"[VIZ DEBUG] analysis_result keys: {analysis_result.keys() if analysis_result else 'None'}")
            
            # Extract operation_type for conditional visualization handling
            operation_type = None
            if analysis_result and analysis_result.get('pandas_execution'):
                operation_type = analysis_result['pandas_execution'].get('operation_type')
                master_logger.info(f"[VIZ DEBUG] operation_type: {operation_type}")
            
            computational_results = analysis_result.get('computational_results', {})
            pandas_result = computational_results.get('pandas_execution', {})
            
            if pandas_result:
                master_logger.info(f"[VIZ DEBUG] Using nested pandas_execution from computational_results (LLM-enhanced path)")
            else:
                pandas_result = analysis_result.get('pandas_execution', {})
                if pandas_result:
                    master_logger.info(f"[VIZ DEBUG] Using direct pandas_execution from analysis_result (simple analysis path)")
                else:
                    master_logger.info(f"[VIZ DEBUG] No pandas_execution found in either structure")
            
            master_logger.info(f"[VIZ DEBUG] computational_results exists: {bool(computational_results)}")
            master_logger.info(f"[VIZ DEBUG] pandas_result exists: {bool(pandas_result)}")
            
            execution_status = pandas_result.get('execution_status') if pandas_result else None
            is_partial = pandas_result.get('partial', False) if pandas_result else False
            result_data = pandas_result.get('result', {}) if pandas_result else {}
            
            master_logger.info(f"[VIZ DEBUG] execution_status: {execution_status}")
            master_logger.info(f"[VIZ DEBUG] is_partial: {is_partial}")
            master_logger.info(f"[VIZ DEBUG] result_data type: {result_data.get('type') if result_data else 'None'}")

            viz_df = None
            viz_source = "none"
            should_attempt_viz = True
            
            # Check if result_viz exists (separate visualization dataframe) - only for period_comparison
            result_viz_data = None
            if pandas_result and operation_type == 'period_comparison':
                result_viz_data = pandas_result.get('result_viz', {})
            
            if result_viz_data:
                master_logger.info(f"[VIZ DEBUG] result_viz found - using separate visualization dataframe")
                try:
                    viz_df = pd.DataFrame(result_viz_data['data'], index=result_viz_data.get('index'))
                    viz_df.columns = result_viz_data.get('columns', viz_df.columns)
                    viz_df.columns = [str(col) for col in viz_df.columns]
                    viz_source = "result_viz_dataframe"
                    master_logger.info(f"[VIZ DEBUG] Using result_viz: shape={viz_df.shape}, columns={viz_df.columns.tolist()}")
                except Exception as e:
                    master_logger.error(f"[VIZ DEBUG] result_viz reconstruction failed: {e}, falling back to result")
                    result_viz_data = None
            
            if result_viz_data is None and result_data:
                result_type = result_data.get('type')
                
                if result_type == 'dataframe':
                    try:
                        master_logger.info(f"[VIZ DEBUG] Reconstructing DataFrame from pandas result")
                        master_logger.info(f"[VIZ DEBUG] - Data rows: {len(result_data.get('data', []))}")
                        master_logger.info(f"[VIZ DEBUG] - Index items: {len(result_data.get('index', []))}")
                        master_logger.info(f"[VIZ DEBUG] - Columns: {len(result_data.get('columns', []))}")
                        
                        viz_df = pd.DataFrame(result_data['data'], index=result_data.get('index'))
                        viz_df.columns = result_data.get('columns', viz_df.columns)
                        viz_df.columns = [str(col) for col in viz_df.columns]
                        
                        viz_source = "reconstructed_dataframe"
                        master_logger.info(f"[VIZ DEBUG] Reconstructed DF: shape={viz_df.shape}, columns={viz_df.columns.tolist()}")
                        master_logger.info(f"[VIZ DEBUG] Column names sanitized to strings for matplotlib compatibility")
                        
                    except Exception as e:
                        master_logger.error(f"[VIZ DEBUG] Reconstruction failed: {e}")
                        viz_source = "reconstruction_failed"
                        
                elif result_type == 'series':
                    master_logger.warning(f"[VIZ DEBUG] Unexpected series type - normalization should have converted this!")
                    viz_source = "unexpected_series"
                    
                elif result_type == 'scalar':
                    scalar_value = result_data.get('value')
                    master_logger.info(f"[VIZ DEBUG] Scalar result ({scalar_value}) - not suitable for chart visualization")
                    viz_source = "scalar_result"
                    should_attempt_viz = False
                    
                elif result_type == 'other':
                    master_logger.warning(f"[VIZ DEBUG] Non-standard result type: {result_data.get('value')}")
                    viz_source = "other_type"
                    should_attempt_viz = False
                    
                else:
                    master_logger.warning(f"[VIZ DEBUG] Unknown result type: {result_type}")
                    viz_source = "unknown_type"
            else:
                master_logger.info(f"[VIZ DEBUG] No result_data available (execution likely failed or returned None)")
                viz_source = "no_result_data"
            
            if not should_attempt_viz:
                master_logger.info(f"[VIZ DEBUG] Skipping visualization attempt (reason: {viz_source})")
                return None
            
            if viz_df is not None:
                master_logger.info(f"[VIZ DEBUG] Using {viz_source} for visualization")
                visualization = self.viz_service.create_intelligent_visualization(query, viz_df, operation_type=operation_type)
            else:
                master_logger.info(f"[VIZ DEBUG] Falling back to original data (reason: {viz_source})")
                visualization = self.viz_service.create_intelligent_visualization(query, data, operation_type=operation_type)
            
            if visualization and not visualization.get('needs_visualization', True):
                master_logger.info(f"[VIZ DEBUG] Visualization service determined no visualization needed")
                return None
            
            master_logger.info(f"[VIZ DEBUG] Visualization created successfully (source: {viz_source})")
            return visualization
        except Exception as e:
            master_logger.error(f"Error creating visualization: {e}")
            return None
    
    def _format_markdown_with_bold_headers(self, df: pd.DataFrame) -> str:
        """
        Convert DataFrame to markdown format with bold headers.
        """
        try:
            markdown_table = df.to_markdown(index=False)
            if not markdown_table:
                return df.to_string(index=False)
            
            lines = markdown_table.split('\n')
            if len(lines) < 2:
                return markdown_table
            
            header_line = lines[0]
            if '|' in header_line:
                parts = header_line.split('|')
                columns = [part.strip() for part in parts if part.strip()]
                bold_columns = [f"**{col}**" for col in columns]
                new_header = '| ' + ' | '.join(bold_columns) + ' |'
                lines[0] = new_header
                return '\n'.join(lines)
            
            return markdown_table
            
        except Exception as e:
            master_logger.warning(f"[TABLE_FORMAT] Error formatting markdown with bold headers: {e}")
            return df.to_markdown(index=False)
    
    def _format_table_response(self, analysis_result: Dict[str, Any]) -> str:
        """
        Format pandas execution result into readable table text with intelligent sorting/filtering.
        """
        try:
            pandas_execution = analysis_result.get('pandas_execution', {})
            result_data = pandas_execution.get('result', {})
            result_type = result_data.get('type')
            
            master_logger.info(f"[TABLE_FORMAT] Formatting result type: {result_type}")
            
            if result_type == 'scalar':
                value = result_data.get('value')
                master_logger.info(f"[TABLE_FORMAT] Scalar value: {value}")
                return f"Result: {value}"
            
            elif result_type == 'series':
                master_logger.info(f"[TABLE_FORMAT] Processing series result")
                data_dict = result_data.get('data', {})
                series = pd.Series(data_dict)
                series.name = result_data.get('name', 'value')
                df = series.reset_index()
                df.columns = ['category', series.name]
                df = df.sort_values(by=series.name, ascending=False)
                df_top5 = df.head(5)
                table_str = self._format_markdown_with_bold_headers(df_top5)
                if len(df) > 5:
                    disclaimer = "\n\nNote: Showing top 5 results only. Please refer to the graph for complete data visualization."
                    return table_str + disclaimer
                else:
                    return table_str
            
            elif result_type == 'dataframe':
                master_logger.info(f"[TABLE_FORMAT] Processing dataframe result")
                df = pd.DataFrame(result_data['data'], index=result_data.get('index'))
                df.columns = result_data.get('columns', df.columns)
                
                is_meaningful_index = False
                if df.index.name:
                    is_meaningful_index = True
                    master_logger.info(f"[TABLE_FORMAT] Index is meaningful: has name '{df.index.name}'")
                elif isinstance(df.index, pd.RangeIndex):
                    is_meaningful_index = False
                    master_logger.info(f"[TABLE_FORMAT] Index is NOT meaningful: RangeIndex")
                elif isinstance(df.index, (pd.DatetimeIndex, pd.TimedeltaIndex, pd.PeriodIndex, pd.CategoricalIndex)):
                    is_meaningful_index = True
                    master_logger.info(f"[TABLE_FORMAT] Index is meaningful: type {type(df.index).__name__}")
                elif len(df) > 0:
                    sample_values = df.index[:min(5, len(df))].tolist()
                    try:
                        if all(isinstance(v, (int, np.integer)) for v in sample_values):
                            is_meaningful_index = False
                            master_logger.info(f"[TABLE_FORMAT] Index is NOT meaningful: integer values {sample_values}")
                        else:
                            is_meaningful_index = True
                            master_logger.info(f"[TABLE_FORMAT] Index is meaningful: non-integer values {sample_values}")
                    except:
                        is_meaningful_index = True
                        master_logger.info(f"[TABLE_FORMAT] Index is meaningful: cannot determine type, showing to be safe")
                
                if is_meaningful_index:
                    df = df.reset_index()
                    master_logger.info(f"[TABLE_FORMAT] Reset meaningful index to visible column")
                else:
                    master_logger.info(f"[TABLE_FORMAT] Keeping non-meaningful index hidden")
                
                master_logger.info(f"[TABLE_FORMAT] DataFrame shape: {df.shape}, columns: {df.columns.tolist()}")
                
                num_cols = len(df.columns)
                if num_cols >= 2:
                    groupby_cols = df.columns[:-1].tolist()
                    value_col = df.columns[-1]
                    num_groupby_features = len(groupby_cols)
                    master_logger.info(f"[TABLE_FORMAT] Detected {num_groupby_features} groupby features, value column: {value_col}")
                    
                    if num_groupby_features == 1:
                        master_logger.info(f"[TABLE_FORMAT] Single groupby - sorting and taking top 5")
                        df_sorted = df.sort_values(by=value_col, ascending=False)
                        df_result = df_sorted.head(5).reset_index(drop=True)
                        
                    elif num_groupby_features >= 2:
                        master_logger.info(f"[TABLE_FORMAT] Multiple groupby - applying top 5/1 logic")
                        primary_col = groupby_cols[0]
                        secondary_col = groupby_cols[1]
                        primary_totals = df.groupby(primary_col)[value_col].sum().sort_values(ascending=False)
                        top5_primary = primary_totals.head(5).index.tolist()
                        master_logger.info(f"[TABLE_FORMAT] Top 5 {primary_col}: {top5_primary}")
                        
                        result_rows = []
                        for primary_val in top5_primary:
                            primary_df = df[df[primary_col] == primary_val]
                            top_secondary = primary_df.sort_values(by=value_col, ascending=False).head(1)
                            if not top_secondary.empty:
                                result_rows.append(top_secondary)
                        
                        if result_rows:
                            df_result = pd.concat(result_rows, ignore_index=True)
                            master_logger.info(f"[TABLE_FORMAT] Multiple groupby result: {len(df_result)} rows")
                        else:
                            df_result = df.head(5)
                            master_logger.warning(f"[TABLE_FORMAT] No result rows, using fallback top 5")
                    else:
                        df_result = df.head(5)
                else:
                    df_result = df.head(5)
                    master_logger.info(f"[TABLE_FORMAT] Single column or unusual format - showing top 5")
                
                # Replace NaN with empty string in percentage_change column for display (period_comparison only)
                if 'percentage_change' in df_result.columns:
                    # Only apply to period_comparison operations
                    pandas_execution = analysis_result.get('pandas_execution', {})
                    if pandas_execution.get('operation_type') == 'period_comparison':
                        df_result['percentage_change'] = df_result['percentage_change'].fillna('')
                
                table_str = self._format_markdown_with_bold_headers(df_result)
                if len(df) > 5:
                    disclaimer = "\n\nNote: Showing top 5 results only. Please refer to the graph for complete data visualization."
                    return table_str + disclaimer
                else:
                    return table_str
            
            else:
                master_logger.warning(f"[TABLE_FORMAT] Unknown result type: {result_type}")
                value = result_data.get('value', 'No result available')
                return f"Result: {value}"
                
        except Exception as e:
            master_logger.error(f"[TABLE_FORMAT] Error formatting table response: {e}", exc_info=True)
            return f"Analysis completed. Unable to format results: {str(e)}"
    
    # (numeric coercion intentionally commented out in this version per provided snippet)

    # (query-relevant column extraction intentionally commented out in this version per provided snippet)

    def _format_aggregation_result(self, result) -> Dict[str, Any]:
        """
        Format pandas result (DataFrame, Series, or scalar) into serializable dict.
        """
        try:
            if isinstance(result, pd.DataFrame):
                return {
                    'type': 'dataframe',
                    'data': result.values.tolist(),
                    'columns': result.columns.tolist(),
                    'index': result.index.tolist(),
                    'shape': result.shape
                }
            elif isinstance(result, pd.Series):
                return {
                    'type': 'series',
                    'data': result.to_dict(),
                    'name': result.name,
                    'index': result.index.tolist()
                }
            elif isinstance(result, (int, float, np.integer, np.floating)):
                return {
                    'type': 'scalar',
                    'value': float(result)
                }
            else:
                return {
                    'type': 'other',
                    'value': str(result)
                }
        except Exception as e:
            master_logger.error(f"[FORMAT_RESULT] Error: {e}")
            return {
                'type': 'error',
                'value': str(result)
            }

    def execute_pandas_aggregation_with_codet5(self, query: str, df: pd.DataFrame, 
                                               intent_result=None, chart_context=None) -> Dict[str, Any]:
        try:
            master_logger.info("=" * 80)
            master_logger.info("[3-LAYER_AGGREGATION] Starting pandas aggregation with 3-layer defense")
            master_logger.info(f"Query: '{query}'")
            
            # Use original CSV if available, else fall back to provided df
            df_for_code = self.original_csv_data if self.original_csv_data is not None else df
            
            # Generate code using date-aware 3-layer defense
            nl_result = self.nl_to_python.generate_pandas_code(
                query=query,
                df_columns=list(df_for_code.columns),
                df_sample=df_for_code
            )
            
            if not nl_result or not nl_result.generated_code:
                master_logger.error(f"[3-LAYER_AGGREGATION] Code generation failed")
                return {
                    'query': query,
                    'execution_status': 'error',
                    'error': 'Code generation failed',
                    'result': None,
                    'partial': False
                }
            
            generated_code = nl_result.generated_code
            operation_type = nl_result.operation_type
            master_logger.info(f"[3-LAYER_AGGREGATION] Generated code: {generated_code}")
            master_logger.info(f"[3-LAYER_AGGREGATION] Operation type: {operation_type}")

            # Execute on FULL df_for_code
            result, status, result_viz = self.execute_code(generated_code, df_for_code, operation_type=operation_type)
            
            if result is None or "Error" in status:
                master_logger.error(f"[3-LAYER_AGGREGATION] Execution failed: {status}")
                return {
                    'query': query,
                    'execution_status': 'error',
                    'error': status,
                    'result': None,
                    'partial': False
                }
            
            aggregation_result = {
                'query': query,
                'generated_code': generated_code,
                'operation_type': nl_result.operation_type,
                'confidence': nl_result.confidence,
                'explanation': nl_result.explanation,
                'execution_status': status,
                'result': self._format_aggregation_result(result),
                'result_viz': self._format_aggregation_result(result_viz) if result_viz is not None else None,
                'result_type': type(result).__name__
            }
            
            # Only include result_viz for period_comparison operations
            if operation_type == "period_comparison" and result_viz is not None:
                aggregation_result['result_viz'] = self._format_aggregation_result(result_viz)
            
            master_logger.info(f"[3-LAYER_AGGREGATION] Aggregation completed successfully")
            return aggregation_result
            
        except Exception as e:
            # Proper error handling (fixes previous bare 'pass')
            master_logger.error(f"[3-LAYER_AGGREGATION] Error: {e}")
            master_logger.error(f"[3-LAYER_AGGREGATION] Traceback: {traceback.format_exc()}")
            return {
                'query': query,
                'execution_status': 'error',
                'error': str(e),
                'result': None,
                'partial': False
            }

    def execute_code(self, code: str, df: pd.DataFrame, operation_type: str = None):
        """
        Safely execute the generated pandas code and normalize results.
        Supports both single-line and multi-line code execution with persistent state.
        Returns (result, status, result_viz) where result_viz is for visualization.
        
        Args:
            code: Pandas code to execute
            df: DataFrame to operate on
            operation_type: Type of operation (e.g., 'period_comparison') to enable operation-specific features
        """
        try:
            safe_globals = {
                'df': df.copy(),
                'pd': pd,
                'np': np,
                'original_total': len(df)  # Add original_total for percentage calculations
            }
            
            code_lines = [line for line in code.strip().split('\n') if line.strip()]
            
            merged_lines = []
            continuation_operators = (',', '[', '(', '{', '+', '-', '*', '/', '//', '**', '%', 
                                     '==', '!=', '<', '>', '<=', '>=', '&', '|', '^', '=', '\\')
            closing_brackets = (']', ')', '}')
            
            for line in code_lines:
                stripped = line.strip()
                is_continuation = False
                
                if merged_lines and (stripped.startswith(closing_brackets) or stripped.startswith('.')):
                    is_continuation = True
                
                if merged_lines and not is_continuation:
                    prev_line = merged_lines[-1].rstrip()
                    if prev_line.endswith(continuation_operators):
                        is_continuation = True
                
                if is_continuation:
                    merged_lines[-1] = merged_lines[-1] + ' ' + stripped
                else:
                    merged_lines.append(stripped)
            
            code_lines = merged_lines
            
            if len(code_lines) > 1:
                master_logger.info(f"[MULTI_LINE_EXEC] Executing {len(code_lines)} lines of code")
                for i, line in enumerate(code_lines[:-1], 1):
                    master_logger.debug(f"[MULTI_LINE_EXEC] Executing line {i}: {line}")
                    exec(line, safe_globals)
                
                last_line = code_lines[-1].strip()
                
                if '=' in last_line and not any(op in last_line.split('=')[0] for op in ['==', '!=', '<=', '>=']):
                    parts = last_line.split('=', 1)
                    if len(parts) == 2 and parts[0].strip().isidentifier():
                        var_name = parts[0].strip()
                        exec(last_line, safe_globals)
                        result = safe_globals[var_name]
                        master_logger.info(f"[MULTI_LINE_EXEC] Executed assignment and retrieved '{var_name}'")
                    else:
                        last_line = parts[1].strip()
                        result = eval(last_line, safe_globals)
                elif last_line.startswith('print(') and last_line.endswith(')'):
                    last_line = last_line[6:-1].strip()
                    master_logger.info(f"[MULTI_LINE_EXEC] Detected print statement")
                    result = eval(last_line, safe_globals)
                else:
                    result = eval(last_line, safe_globals)
            else:
                code_stripped = code.strip()
                if '=' in code_stripped and not any(op in code_stripped.split('=')[0] for op in ['==', '!=', '<=', '>=']):
                    parts = code_stripped.split('=', 1)
                    if len(parts) == 2 and parts[0].strip().isidentifier():
                        var_name = parts[0].strip()
                        exec(code_stripped, safe_globals)
                        result = safe_globals[var_name]
                        master_logger.info(f"[SINGLE_LINE_EXEC] Executed assignment and retrieved '{var_name}'")
                    else:
                        expr = parts[1].strip()
                        result = eval(expr, safe_globals)
                else:
                    result = eval(code_stripped, safe_globals)
        
            # Normalize Series → DataFrame
            if isinstance(result, pd.Series):
                master_logger.info(f"[NORMALIZATION] Converting Series to DataFrame")
                metric_name = result.name if result.name else 'value'
                if result.index.name is None:
                    master_logger.info(f"[NORMALIZATION] Index is unnamed (meaningless), dropping it")
                    result = result.to_frame(name=metric_name).reset_index(drop=True)
                else:
                    master_logger.info(f"[NORMALIZATION] Index is named '{result.index.name}' (meaningful), keeping it")
                    result = result.reset_index()
                master_logger.info(f"[NORMALIZATION] Converted to DataFrame: shape={result.shape}, columns={result.columns.tolist()}")

            # Check if result_viz exists (only for period_comparison operations)
            result_viz = None
            if operation_type == "period_comparison":
                result_viz = safe_globals.get('result_viz', None)
                if result_viz is not None:
                    master_logger.info(f"[VIZ_SEPARATE] result_viz found with shape: {result_viz.shape if hasattr(result_viz, 'shape') else 'N/A'}")

            master_logger.info(f"Successfully executed pandas code: {code}")
            if result_viz is not None:
                master_logger.info(f"[VIZ_SEPARATE] result_viz found with shape: {result_viz.shape if hasattr(result_viz, 'shape') else 'N/A'}")
            return result, "success", result_viz
        
        except Exception as e:
            error_msg = f"Error executing pandas code '{code}': {e}"
            master_logger.error(error_msg)
            return None, error_msg, None
    
    def _log_data_exploration_to_sheets(self, query_text: str, generated_code: str, 
                                       generated_answer: str, execution_status: str,
                                       chart_selected: str, data_shape: str):
        """
        Log data exploration query details to Google Sheets for analysis and improvement.
        This method runs asynchronously to avoid impacting user response times.
        """
        try:
            from services.google_sheets_service import send_data_exploration_log_to_google_sheets
            from datetime import datetime
            import threading
            
            # Prepare log data
            log_data = {
                'timestamp': datetime.now().isoformat(),
                'user_query': query_text or '',
                'generated_code': generated_code or '',
                'generated_answer': generated_answer or '',
                'execution_status': execution_status or 'unknown',
                'chart_selected': chart_selected or 'none',
                'data_shape': data_shape or 'unknown',
                'session_id': 'anonymous'  # Can be enhanced with actual session tracking
            }
            
            # Log attempt
            master_logger.info("[DATA_EXPLORATION_LOG] Attempting to log to Google Sheets...")
            master_logger.debug(f"[DATA_EXPLORATION_LOG] Log data: {log_data}")
            
            def log_async():
                """Async logging function to avoid blocking user response"""
                try:
                    success, response_data, error_msg = send_data_exploration_log_to_google_sheets(log_data)
                    
                    if success:
                        master_logger.info("[DATA_EXPLORATION_LOG] Successfully logged to Google Sheets")
                        master_logger.debug(f"[DATA_EXPLORATION_LOG] Response: {response_data}")
                        
                        # Also log to local backup file
                        self._log_to_backup_file(log_data)
                        
                    else:
                        master_logger.warning(f"[DATA_EXPLORATION_LOG] Failed to log to Google Sheets: {error_msg}")
                        
                        # Fallback to local file only
                        self._log_to_backup_file(log_data, fallback=True)
                        
                except Exception as e:
                    master_logger.error(f"[DATA_EXPLORATION_LOG] Exception during async logging: {e}")
                    
                    # Fallback to local file only  
                    self._log_to_backup_file(log_data, fallback=True)
            
            # Start async logging thread
            thread = threading.Thread(target=log_async, daemon=True)
            thread.start()
            
        except Exception as e:
            master_logger.error(f"[DATA_EXPLORATION_LOG] Error setting up logging: {e}")
    
    def _log_to_backup_file(self, log_data: dict, fallback: bool = False):
        """Log data exploration queries to local backup file"""
        try:
            import json
            import os
            from datetime import datetime
            
            backup_file = "data_exploration_logs.json"
            
            # Load existing logs
            logs = []
            if os.path.exists(backup_file):
                try:
                    with open(backup_file, 'r', encoding='utf-8') as f:
                        logs = json.load(f)
                except json.JSONDecodeError:
                    master_logger.warning(f"[DATA_EXPLORATION_LOG] Corrupted backup file, starting fresh")
                    logs = []
            
            # Add new log entry
            logs.append(log_data)
            
            # Keep only last 1000 entries to prevent file from growing too large
            if len(logs) > 1000:
                logs = logs[-1000:]
            
            # Write back to file
            with open(backup_file, 'w', encoding='utf-8') as f:
                json.dump(logs, f, indent=2, ensure_ascii=False)
            
            log_type = "FALLBACK" if fallback else "BACKUP"
            master_logger.info(f"[DATA_EXPLORATION_LOG] {log_type} logged to local file: {backup_file}")
            
        except Exception as e:
            master_logger.error(f"[DATA_EXPLORATION_LOG] Failed to write backup log: {e}")