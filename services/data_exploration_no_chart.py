"""
Data Exploration Service
Enhanced with proper result extraction and visualization handling
Fixed: Dictionary result to DataFrame conversion, visualization timeouts
MIGRATED: From pandas to polars
"""

import json
import logging
import time
import os
import asyncio
import traceback
from functools import wraps
from scipy import stats 
from typing import Dict, List, Any, Optional, Tuple
import polars as pl
import pandas as pd  # Keep for execute_code sandbox
import numpy as np
import re

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from master_logger import setup_module_logger

from services.llm_service import LLMService
from services.data_processor import TableauDataProcessor
from services.visualization_service import IntelligentVisualizationService
from services.fuzzy_column_matcher import FuzzyColumnMatcher
from services.multi_table_service import TableauMultiTableService
from services.insight_generator import TableauInsightGenerator
from services.NL_to_python import NLToPythonGenerator
from services.enhanced_analysis_service import EnhancedAnalysisService
from services.period_extraction_service import PeriodExtractionService
from services.response_template_engine import ResponseTemplateEngine

master_logger = setup_module_logger('services.data_exploration')
master_logger.info("DATA EXPLORATION SERVICE MODULE INITIALIZATION STARTED")


class data_exploration:
    """
    Self-contained class for data exploration queries with enhanced result handling
    Migrated to use polars instead of pandas
    """
    
    def __init__(self, llm_client, smart_agg_decider, cache_path: str = "causal_analysis_cache.json"):
        """Initialize the data_exploration class"""
        master_logger.info("=== INITIALIZING DATA EXPLORATION SERVICE ===")
        
        self.llm_client = llm_client
        self.smart_aggregation_decider = smart_agg_decider
        self.cache_path = cache_path
        
        # Initialize data attributes
        self.original_csv_data = None
        self.workbook_name = None
        
        # Initialize services
        self.llm_service = LLMService(openai_client=llm_client)
        self.data_processor = TableauDataProcessor()
        self.viz_service = IntelligentVisualizationService()
        self.multi_table_service = TableauMultiTableService()
        self.insight_generator = TableauInsightGenerator()
        self.nl_to_python = NLToPythonGenerator(openai_client=llm_client)
        self.enhanced_analysis = EnhancedAnalysisService(self.data_processor, self.nl_to_python)
        self.fuzzy_matcher = FuzzyColumnMatcher(threshold=70)
        
        # Initialize template engine for professional response formatting
        self.template_engine = ResponseTemplateEngine()
        master_logger.info("✓ Response template engine initialized")
        
        # Initialize period extraction service
        self.period_extractor = PeriodExtractionService(
            model_path="event-period-ner-bert"
        )
        master_logger.info("✓ Period extraction service initialized")
        
        # Initialize ConversationOrchestrator
        try:
            from services.conversation_orchestrator import ConversationOrchestrator
            
            self.conversation_orchestrator = ConversationOrchestrator(
                llm_client=llm_client,
                nl_to_python_generator=self.nl_to_python
            )
            
            self.conversation_orchestrator.set_execution_callbacks(
                execute_pandas_fn=self.execute_pandas_aggregation_with_codet5,
                apply_column_cleaning_fn=self._apply_column_cleaning
            )
            
            self.has_conversation_support = True
            master_logger.info("✓ ConversationOrchestrator initialized with callbacks")
            
        except ImportError as e:
            master_logger.warning(f"ConversationOrchestrator not available: {e}")
            self.conversation_orchestrator = None
            self.has_conversation_support = False
        except Exception as e:
            master_logger.error(f"Failed to initialize ConversationOrchestrator: {e}")
            self.conversation_orchestrator = None
            self.has_conversation_support = False
        
        master_logger.info("✓ All services initialized")
    
    async def process(self, 
                query_text: str,
                csv_data: pl.DataFrame,
                selected_chart: str,
                intent_result = None,
                chart_context: Optional[Dict] = None,
                conversation_state: Optional[Dict] = None,
                use_conversation: bool = True,
                context: Optional[Dict] = None) -> Dict[str, Any]:  # 🆕 Add context parameter
        """Process exploration query"""
        master_logger.info("=" * 80)
        master_logger.info("=== PROCESSING DATA EXPLORATION QUERY ===")
        master_logger.info(f"Query: '{query_text}'")
        master_logger.info(f"Selected chart: {selected_chart}")
        master_logger.info(f"Use conversation: {use_conversation}")
        master_logger.info(f"Has conversation state: {conversation_state is not None}")
        
        # 🆕 Extract workbook_name from context (handle both dict and Pydantic model)
        self.workbook_name = None
        if context:
            if isinstance(context, dict):
                self.workbook_name = context.get('workbook_name')
            elif hasattr(context, 'workbook_name'):
                # Handle Pydantic ChatContext model
                self.workbook_name = context.workbook_name
            master_logger.info(f"🔍 Workbook extracted from context: {self.workbook_name}")
        
        if use_conversation and self.has_conversation_support:
            return await self._process_with_orchestrator(
                query_text, csv_data, selected_chart, 
                intent_result, chart_context, conversation_state
            )
        else:
            return await self._process_single_turn(
                query_text, csv_data, selected_chart,
                intent_result, chart_context
            )
    
    async def _process_with_orchestrator(self,
                                        query_text: str,
                                        csv_data: pl.DataFrame,
                                        selected_chart: str,
                                        intent_result,
                                        chart_context: Optional[Dict],
                                        conversation_state: Optional[Dict]) -> Dict[str, Any]:
        # Convert pandas to polars if needed (DataManager returns pandas)
        if csv_data is not None and isinstance(csv_data, pd.DataFrame):
            csv_data = pl.from_pandas(csv_data)
            master_logger.info("Converted pandas DataFrame to polars")
        """Process query using ConversationOrchestrator"""
        master_logger.info("[ORCHESTRATOR] Processing with conversational support")
        start_time = time.time()
        
        try:
            analysis_data, chart_context = self._apply_column_cleaning(csv_data, selected_chart, query_text)
            
            orchestrator_result = await self.conversation_orchestrator.process_query(
                query=query_text,
                df_data=analysis_data,
                df_columns=list(analysis_data.columns),
                selected_chart=selected_chart,
                chart_context=chart_context,
                conversation_state=conversation_state
            )
            
            if orchestrator_result.get('needs_clarification'):
                return {
                    'success': True,
                    'response': orchestrator_result['clarifying_question'],
                    'needs_clarification': True,
                    'conversation_state': orchestrator_result.get('conversation_state'),
                    'execution_time': time.time() - start_time
                }
            
            if not orchestrator_result.get('success'):
                return {
                    'success': False,
                    'response': f"Error: {orchestrator_result.get('error', 'Unknown error')}",
                    'error': True,
                    'conversation_state': orchestrator_result.get('conversation_state'),
                    'execution_time': time.time() - start_time
                }
            
            analysis_result = orchestrator_result.get('result')
            
            visualization = None
            is_scalar_result = self._check_if_scalar(analysis_result)
            
            if not is_scalar_result and self._requires_visualization(intent_result, {'pandas_execution': analysis_result}):
                # Convert result dict to DataFrame for visualization
                result_df = self._result_dict_to_dataframe(analysis_result)
                viz_data = result_df if result_df is not None else analysis_data
                
                visualization = await self._create_visualization_for_intent(
                    intent_result, {'pandas_execution': analysis_result}, viz_data
                )
            
            # Build analysis dict with nl_result for template engine and ranking flags
            analysis_dict = {'pandas_execution': analysis_result}
            if 'nl_result' in orchestrator_result:
                analysis_dict['nl_result'] = orchestrator_result['nl_result']
            
            response_dict = self._format_table_response(analysis_dict, query=query_text, intent_result=intent_result)
            
            return {
                'success': True,
                'response': response_dict.get('response', 'Analysis completed'),
                'table_data': response_dict.get('table_data'),
                'computational_results': {'pandas_execution': analysis_result},
                'needs_visualization': visualization is not None,
                'chart_type': visualization.get('chart_type') if visualization else None,
                'chart_image': visualization.get('chart_image') if visualization else None,
                'chart_context': chart_context,
                'conversation_state': orchestrator_result.get('conversation_state'),
                'execution_time': time.time() - start_time
            }
            
        except Exception as e:
            master_logger.error(f"[ORCHESTRATOR] Error: {e}")
            master_logger.error(traceback.format_exc())
            return {
                'success': False,
                'response': f"Error in conversational processing: {str(e)}",
                'error': True,
                'execution_time': time.time() - start_time
            }
    
    async def _process_single_turn(self,
                                   query_text: str,
                                   csv_data: pl.DataFrame,
                                   selected_chart: str,
                                   intent_result,
                                   chart_context: Optional[Dict]) -> Dict[str, Any]:
        """Process query using single-turn logic"""
        # Convert pandas to polars if needed (DataManager returns pandas)
        if csv_data is not None and isinstance(csv_data, pd.DataFrame):
            csv_data = pl.from_pandas(csv_data)
            master_logger.info("Converted pandas DataFrame to polars")
        
        if csv_data is not None:
            master_logger.info(f"Original CSV data shape: {csv_data.shape}")
            master_logger.info(f"Original CSV columns: {list(csv_data.columns)}")
        
        start_time = time.time()
        
        try:
            # Store original CSV
            self.original_csv_data = csv_data
            
            # Step 2: Apply column cleaning
            master_logger.info("STEP 2: Data filtering and chart context loading")
            analysis_data = csv_data
            chart_context = None
            
            if csv_data is not None and selected_chart:
                master_logger.info(f"Loading chart context for: {selected_chart}")
                analysis_data, chart_context = self._apply_column_cleaning(csv_data, selected_chart, query_text)
                
                if chart_context:
                    chart_context['chart_name'] = selected_chart
                    master_logger.info(f"Chart context loaded successfully")
            else:
                master_logger.info("No chart context loading - using full CSV data")
            
            # Step 3: Execute analysis
            master_logger.info("STEP 3: Executing enhanced analysis")
            analysis_result = await self._execute_intent_analysis(
                intent_result, query_text, analysis_data, chart_context)
            
            master_logger.info(f"Analysis completed. Success: {analysis_result.get('success', False)}")
            
            # 🆕 EARLY EXIT FOR COLUMN DESCRIPTIONS - NO VISUALIZATION NEEDED
            if (analysis_result and 
                analysis_result.get('pandas_execution', {}).get('operation_type') == 'column_description'):
                
                master_logger.info("🎯 [COLUMN_DESC_EXIT] Column description detected - bypassing visualization pipeline")
                master_logger.info("🚀 [COLUMN_DESC_EXIT] Sending metadata directly to frontend")
                
                # Create specialized response for column descriptions - format for Chrome extension display
                column_desc_result = analysis_result.get('pandas_execution', {}).get('result', {})
                
                # Format the actual column description content for display
                description_text = self._format_column_description_for_display(column_desc_result)
                
                formatted_response = {
                    "response": description_text,  # Put actual description content here for Chrome extension
                    "result_data": column_desc_result,  # Keep structured data for other uses
                    "table_data": None,  # No table data needed for descriptions
                    "success": True,
                    "metadata": {
                        "is_column_description": True,
                        "operation_type": "column_description",
                        "bypass_visualization": True,
                        "display_format": "description_cards"
                    }
                }
                
                master_logger.info("✅ [COLUMN_DESC_EXIT] Column description response ready - skipping all visualization steps")
                return formatted_response
            
            # Step 4: Generate visualization (FIXED SECTION - for analytical operations only)
            master_logger.info("STEP 4: Checking visualization requirements")
            visualization = None
            
            # Extract and validate result data
            pandas_execution = analysis_result.get('pandas_execution') if analysis_result else None
            is_scalar_result = False
            viz_ready_df = None
            
            if pandas_execution:
                result_data = pandas_execution.get('result')
                
                if result_data:
                    result_type = result_data.get('type')
                    
                    if result_type == 'dict' or (isinstance(result_data, dict) and 'type' not in result_data):
                        # CRITICAL FIX: Convert dictionary results to DataFrame
                        master_logger.info(f"[VIZ_PREP] Converting dict result to DataFrame")
                        
                        # Check if it's a scalar value
                        if result_type == 'scalar' and 'value' in result_data:
                            is_scalar_result = True
                            master_logger.info("[VIZ_PREP] Scalar result - skipping visualization")
                        else:
                            # Multi-value dictionary - convert to DataFrame
                            try:
                                # Check if it's already formatted as DataFrame dict
                                if 'data' in result_data and 'columns' in result_data:
                                    viz_ready_df = pl.DataFrame(
                                        {col: [row[i] for row in result_data['data']] 
                                         for i, col in enumerate(result_data.get('columns', []))}
                                    )
                                    master_logger.info(f"[VIZ_PREP] Reconstructed DataFrame: shape={viz_ready_df.shape}")
                                else:
                                    # Simple dictionary - extract non-type keys
                                    dict_items = {k: v for k, v in result_data.items() if k != 'type'}
                                    
                                    # ENHANCED: Check if dict values are DataFrames
                                    has_dataframe_values = any(isinstance(v, (pl.DataFrame, dict)) and 
                                                               (isinstance(v, pl.DataFrame) or ('data' in v and 'columns' in v))
                                                               for v in dict_items.values())
                                    
                                    if has_dataframe_values:
                                        master_logger.warning("[VIZ_PREP] Dict contains nested DataFrames - cannot visualize directly")
                                        master_logger.warning(f"[VIZ_PREP] Dict keys: {list(dict_items.keys())}")
                                        is_scalar_result = True  # Treat as non-visualizable
                                    elif dict_items:
                                        # Simple key-value pairs
                                        viz_ready_df = pl.DataFrame({
                                            'Category': list(dict_items.keys()),
                                            'Count': list(dict_items.values())
                                        })
                                        master_logger.info(f"[VIZ_PREP] Created DataFrame from dict: shape={viz_ready_df.shape}")
                                        master_logger.info(f"[VIZ_PREP] DataFrame preview:\n{viz_ready_df.head()}")
                                        
                                        # Update pandas_execution for consistency
                                        pandas_execution['result'] = {
                                            'type': 'dataframe',
                                            'data': viz_ready_df.rows(),
                                            'columns': viz_ready_df.columns,
                                            'index': list(range(len(viz_ready_df))),
                                            'shape': viz_ready_df.shape
                                        }
                                    else:
                                        is_scalar_result = True
                                        
                            except Exception as e:
                                master_logger.error(f"[VIZ_PREP] Failed to convert dict to DataFrame: {e}")
                                master_logger.error(traceback.format_exc())
                                is_scalar_result = True
                                
                    elif result_type == 'dataframe':
                        # Check if single row (treat as scalar)
                        result_shape = result_data.get('shape', (0, 0))
                        if result_shape[0] == 1:
                            is_scalar_result = True
                            master_logger.info(f"[VIZ_PREP] Single-row DataFrame - skipping visualization")
                        else:
                            # Multi-row DataFrame - reconstruct it
                            try:
                                viz_ready_df = pl.DataFrame(
                                    {col: [row[i] for row in result_data['data']] 
                                     for i, col in enumerate(result_data.get('columns', []))}
                                )
                                master_logger.info(f"[VIZ_PREP] Using DataFrame result: shape={viz_ready_df.shape}")
                            except Exception as e:
                                master_logger.error(f"[VIZ_PREP] Failed to reconstruct DataFrame: {e}")
                                is_scalar_result = True
                                
                    elif result_type == 'scalar':
                        is_scalar_result = True
                        master_logger.info("[VIZ_PREP] Scalar result - skipping visualization")
                else:
                    master_logger.warning("[VIZ_PREP] No result data available")
                    is_scalar_result = True
            else:
                master_logger.warning("[VIZ_PREP] No pandas_execution available")
                is_scalar_result = True
            
            # Create visualization if appropriate
            if analysis_result and self._requires_visualization(intent_result, analysis_result) and not is_scalar_result:
                master_logger.info("[VIZ_PREP] Generating visualization")
                
                # Use viz_ready_df if available, otherwise fall back to analysis_data
                viz_input = viz_ready_df if viz_ready_df is not None else analysis_data
                
                try:
                    visualization = await self._create_visualization_for_intent(
                        intent_result, 
                        analysis_result, 
                        viz_input
                    )
                except Exception as e:
                    master_logger.error(f"[VIZ_PREP] Visualization failed: {e}")
                    master_logger.error(traceback.format_exc())
                    visualization = None
            else:
                if is_scalar_result:
                    master_logger.info("[VIZ_PREP] No visualization for scalar result")
                else:
                    master_logger.info("[VIZ_PREP] No visualization required")
            
            # Step 5: Format response
            master_logger.info("STEP 5: Formatting table response")
            
            response_dict = self._format_table_response(analysis_result, query=query_text, intent_result=intent_result)
            response_text = response_dict.get("response", "No response generated")
            table_data = response_dict.get("table_data", None)
            
            execution_time = time.time() - start_time
            
            master_logger.info("=== DATA EXPLORATION PROCESSING COMPLETED ===")
            master_logger.info("=" * 80)
            
            return {
                "success": True,
                "response": response_text,
                "table_data": table_data,
                "computational_results": analysis_result,
                "needs_visualization": visualization is not None,
                "chart_type": visualization.get('chart_type') if visualization else None,
                "chart_image": visualization.get('chart_image') if visualization else None,
                "chart_context": chart_context,
                "execution_time": execution_time
            }
            
        except Exception as e:
            master_logger.error("=" * 80)
            master_logger.error("=== DATA EXPLORATION PROCESSING FAILED ===")
            master_logger.error(f"Error: {e}")
            master_logger.error(traceback.format_exc())
            master_logger.error("=" * 80)
            
            execution_time = time.time() - start_time
            
            return {
                "success": False,
                "response": f"I encountered an error processing your query: {str(e)}",
                "error": True,
                "execution_time": execution_time
            }
    
    def _check_if_scalar(self, analysis_result: Any) -> bool:
        """Check if analysis result is scalar or singular value"""
        if isinstance(analysis_result, dict):
            # Handle both nested {'result': {...}} and direct {...} structures
            if 'type' in analysis_result:
                # Direct structure (new orchestrator flow)
                result_type = analysis_result.get('type')
                result_shape = analysis_result.get('shape', (0, 0))
            elif 'result' in analysis_result:
                # Nested structure (old flow)
                result_type = analysis_result.get('result', {}).get('type')
                result_shape = analysis_result.get('result', {}).get('shape', (0, 0))
            else:
                return False
            
            return (result_type == 'scalar') or (result_type == 'dataframe' and result_shape[0] == 1)
        return False
    
    def _result_dict_to_dataframe(self, analysis_result: dict):
        """Convert result dict back to Polars DataFrame for visualization"""
        if not isinstance(analysis_result, dict):
            return None
        
        # Handle direct structure {'type': 'dataframe', 'data': [...], 'columns': [...]}
        if 'type' in analysis_result and analysis_result.get('type') == 'dataframe':
            data = analysis_result.get('data', [])
            columns = analysis_result.get('columns', [])
            if data and columns:
                import polars as pl
                return pl.DataFrame(data, schema=columns, orient='row')
        
        # Handle nested structure {'result': {'type': 'dataframe', ...}}
        if 'result' in analysis_result:
            result = analysis_result['result']
            if isinstance(result, dict) and result.get('type') == 'dataframe':
                data = result.get('data', [])
                columns = result.get('columns', [])
                if data and columns:
                    import polars as pl
                    return pl.DataFrame(data, schema=columns, orient='row')
        
        return None
    
    def _load_causal_cache(self, chart_name):
        """Load causal analysis cache"""
        master_logger.info(f"Loading causal cache for chart: {chart_name}")
        
        try:
            cache_file = self.cache_path
            
            if not os.path.exists(cache_file):
                master_logger.warning(f"Causal cache file not found: {cache_file}")
                return None
            
            with open(cache_file, 'r') as f:
                cache_data = json.load(f)
            
            if chart_name in cache_data:
                chart_cache = cache_data[chart_name]
                master_logger.info(f"Cache found for chart '{chart_name}'")
                return chart_cache
            else:
                master_logger.warning(f"No cache entry found for chart: {chart_name}")
                return None
                
        except Exception as e:
            master_logger.error(f"Error loading causal cache: {e}")
            return None

    def _extract_query_column_terms(self, query: str) -> List[str]:
        """Extract potential column names from query"""
        master_logger.info(f"[QUERY_COLUMN_EXTRACTION] Extracting from: '{query}'")
        
        extracted_terms = []
        query_lower = query.lower()
        
        # Aggregation patterns
        aggregation_patterns = [
            r'count\s+of\s+(\w+(?:_\w+)*)',
            r'sum\s+of\s+(\w+(?:_\w+)*)',
            r'total\s+(\w+(?:_\w+)*)',
            r'(\w+(?:_\w+)*)\s+count',
        ]
        
        for pattern in aggregation_patterns:
            matches = re.findall(pattern, query_lower)
            for match in matches:
                if match and len(match) > 2 and match not in extracted_terms:
                    extracted_terms.append(match)
        
        # Underscore terms
        underscore_terms = re.findall(r'\b(\w+_\w+(?:_\w+)*)\b', query_lower)
        for term in underscore_terms:
            if term not in extracted_terms and len(term) > 3:
                extracted_terms.append(term)
        
        master_logger.info(f"[QUERY_COLUMN_EXTRACTION] Extracted: {extracted_terms}")
        return extracted_terms

    def _apply_column_cleaning(self, csv_data, selected_chart, query: str = None):
        """Load cache and filter CSV to chart-relevant columns"""
        master_logger.info("STEP 2A: Loading chart context and filtering data")
        
        cache_data = self._load_causal_cache(selected_chart)
        
        if cache_data:
            chart_columns = [cache_data.get('x_axis_detected'), cache_data.get('y_axis_detected')]
            top_features = cache_data.get('top_5_features', [])
            relevant_columns = list(set([col for col in chart_columns + top_features if col]))
            
            # Query-based enhancement
            query_matched_columns = []
            if query:
                query_terms = self._extract_query_column_terms(query)
                
                if query_terms:
                    for term in query_terms:
                        fuzzy_match = self.fuzzy_matcher.find_best_match(
                            term,
                            list(csv_data.columns),
                            context=f"query_column_extraction|{selected_chart}",
                            query_context=query
                        )
                        
                        if fuzzy_match and fuzzy_match not in relevant_columns:
                            query_matched_columns.append(fuzzy_match)
                            master_logger.info(f"[COLUMN_CLEANING] Added: '{term}' → '{fuzzy_match}'")
                    
                    if query_matched_columns:
                        relevant_columns = list(set(relevant_columns + query_matched_columns))
            
            available_columns = []
            csv_column_list = list(csv_data.columns)
            
            for cached_col in relevant_columns:
                if not cached_col:
                    continue
                if cached_col in csv_column_list:
                    available_columns.append(cached_col)
                else:
                    fuzzy_match = self.fuzzy_matcher.find_best_match(
                        cached_col,
                        csv_column_list,
                        context=f"column_cleaning|{selected_chart}",
                        query_context=query
                    )
                    
                    if fuzzy_match:
                        available_columns.append(fuzzy_match)
            
            if available_columns:
                filtered_data = csv_data.select(available_columns)
                master_logger.info(f"Filtered to {len(available_columns)} columns")
                return filtered_data, cache_data
            else:
                return csv_data, cache_data
        else:
            return csv_data, None

    async def _execute_intent_analysis(self, intent_result, query: str, data, chart_context=None) -> Dict[str, Any]:
        """Execute analysis"""
        
        if data is None or len(data) == 0:
            master_logger.warning("No data available")
            return {"error": "No data available"}
        
        intent_type = intent_result.primary_intent if intent_result else 'exploration'
        master_logger.info(f"Executing analysis for intent: {intent_type}")
        
        analysis_result = {
            "intent_type": intent_type,
            "query": query,
            "data_shape": data.shape
        }
        
        try:
            pandas_result = self.execute_pandas_aggregation_with_codet5(query, data, intent_result, chart_context)
            analysis_result["pandas_execution"] = pandas_result
            analysis_result["success"] = pandas_result.get('execution_status') == 'success'
            
            # Pass through nl_result for template engine
            if 'nl_result' in pandas_result:
                analysis_result["nl_result"] = pandas_result['nl_result']
            
            return analysis_result
            
        except Exception as e:
            master_logger.error(f"Error executing analysis: {e}")
            return {"error": str(e), "intent_type": intent_type}
    
    def _requires_visualization(self, intent_result, analysis_result=None) -> bool:
        """Determine if visualization is needed"""
        if intent_result is None:
            master_logger.info("[VIZ_CHECK] No intent_result, defaulting to visualization=True")
            return True
        
        # 🆕 EXPLICIT CHECK: Column descriptions never need visualization
        if (analysis_result and 
            analysis_result.get('pandas_execution', {}).get('operation_type') == 'column_description'):
            master_logger.info("[VIZ_CHECK] ⛔ Column description detected - NO visualization needed")
            return False
        
        intent = intent_result.primary_intent if hasattr(intent_result, 'primary_intent') else str(intent_result)
        master_logger.info(f"[VIZ_CHECK] Checking intent: {intent}")
        
        visualization_intents = [
            "trend_analysis", "anomaly_detection", "top_bottom_analysis", 
            "comparison", "seasonality", "data_exploration", "shap_analysis"
        ]
        
        needs_viz = intent in visualization_intents
        master_logger.info(f"[VIZ_CHECK] Intent '{intent}' needs visualization: {needs_viz}")
        
        return needs_viz
    
    async def _create_visualization_for_intent(self, intent_result, analysis_result, data):
        """
        Create visualization with robust error handling.
        Uses the prepared DataFrame passed as 'data' parameter.
        """
        try:
            query = analysis_result.get("query", "")
            master_logger.info(f"[VIZ] Creating visualization for query: {query}")
            
            # Check if data parameter is already a prepared DataFrame
            if isinstance(data, pl.DataFrame) and len(data) > 0:
                master_logger.info(f"[VIZ] Using prepared DataFrame: shape={data.shape}")
                master_logger.info(f"[VIZ] DataFrame columns: {list(data.columns)}")
                master_logger.info(f"[VIZ] DataFrame preview:\n{data.head()}")
                
                # Use the prepared DataFrame directly (convert to pandas for viz service)
                viz_df = data.to_pandas()
            else:
                # Fallback: try to reconstruct from analysis_result
                master_logger.warning("[VIZ] Data parameter not a DataFrame, attempting reconstruction")
                pandas_result = analysis_result.get('pandas_execution', {})
                result_data = pandas_result.get('result', {})
                
                if result_data and result_data.get('type') == 'dataframe':
                    try:
                        viz_df_polars = pl.DataFrame(
                            {col: [row[i] for row in result_data['data']] 
                             for i, col in enumerate(result_data.get('columns', []))}
                        )
                        viz_df = viz_df_polars.to_pandas()
                        viz_df.columns = [str(col) for col in viz_df.columns]
                        master_logger.info(f"[VIZ] Reconstructed DataFrame: shape={viz_df.shape}")
                    except Exception as e:
                        master_logger.error(f"[VIZ] Reconstruction failed: {e}")
                        return None
                else:
                    master_logger.error("[VIZ] No valid data available for visualization")
                    return None
            
            # Detect if this is a comparison query - force bar chart
            is_comparison = any(word in query.lower() for word in ['compare', 'vs', 'versus', 'q1', 'q2', 'q3', 'q4', 'mom', 'yoy'])
            if is_comparison:
                master_logger.info("[VIZ] Comparison query detected - will use bar chart")
            
            # Create visualization
            master_logger.info("[VIZ] Calling visualization service")
            try:
                visualization = self.viz_service.create_intelligent_visualization(query, viz_df)
            except Exception as viz_error:
                master_logger.error(f"[VIZ] Visualization service error: {viz_error}")
                master_logger.error(traceback.format_exc())
                
                # Try creating a simple bar chart manually as fallback
                if is_comparison and len(viz_df.columns) == 2:
                    master_logger.info("[VIZ] Attempting manual bar chart creation")
                    import matplotlib
                    matplotlib.use('Agg')
                    import matplotlib.pyplot as plt
                    import io
                    import base64
                    
                    try:
                        fig, ax = plt.subplots(figsize=(10, 6))
                        ax.bar(viz_df.iloc[:, 0], viz_df.iloc[:, 1])
                        ax.set_xlabel(viz_df.columns[0])
                        ax.set_ylabel(viz_df.columns[1])
                        ax.set_title(f"Comparison: {query}")
                        plt.tight_layout()
                        
                        buffer = io.BytesIO()
                        plt.savefig(buffer, format='png', dpi=100, bbox_inches='tight')
                        buffer.seek(0)
                        image_base64 = base64.b64encode(buffer.read()).decode()
                        plt.close(fig)
                        
                        visualization = {
                            'chart_type': 'bar',
                            'chart_image': image_base64,
                            'needs_visualization': True
                        }
                        master_logger.info("[VIZ] ✓ Manual bar chart created successfully")
                    except Exception as manual_error:
                        master_logger.error(f"[VIZ] Manual chart creation failed: {manual_error}")
                        return None
                else:
                    return None
            
            if not visualization:
                master_logger.warning("[VIZ] Visualization service returned None")
                return None
            
            if not visualization.get('needs_visualization', True):
                master_logger.info("[VIZ] Visualization service determined no viz needed")
                return None
            
            if not visualization.get('chart_image'):
                master_logger.warning("[VIZ] Visualization returned but no chart_image generated")
                master_logger.warning(f"[VIZ] Visualization keys: {visualization.keys()}")
            else:
                master_logger.info("[VIZ] ✓ Visualization created successfully with chart_image")
            
            return visualization
            
        except Exception as e:
            master_logger.error(f"[VIZ] Error creating visualization: {e}")
            master_logger.error(traceback.format_exc())
            return None
    
    def _format_table_response(self, analysis_result, query: str = None, intent_result = None) -> Dict[str, Any]:
        """Format analysis result into table response with template engine integration"""
        try:
            master_logger.info(f"[TABLE_FORMAT] Formatting result")
            
            # Extract intent type and nl_result for template engine
            intent_type = intent_result.primary_intent if intent_result and hasattr(intent_result, 'primary_intent') else 'data_exploration'
            nl_result = analysis_result.get('nl_result') if isinstance(analysis_result, dict) else None
            
            master_logger.info(f"[TABLE_FORMAT] Intent type: {intent_type}, Has NL result: {nl_result is not None}")
            
            # Extract result
            result_data = None
            
            if isinstance(analysis_result, dict):
                if 'pandas_execution' in analysis_result:
                    pandas_exec = analysis_result['pandas_execution']
                    
                    # Handle both structures:
                    # 1. New flow (orchestrator): pandas_exec is already formatted dict with 'type' key
                    # 2. Old flow: pandas_exec has 'result' key containing formatted dict
                    if isinstance(pandas_exec, dict) and 'type' in pandas_exec:
                        # New flow: already formatted
                        result_data = pandas_exec
                    elif isinstance(pandas_exec, dict) and 'result' in pandas_exec:
                        # Old flow: extract result
                        result_data = pandas_exec['result']
                    else:
                        result_data = None
                else:
                    result_data = analysis_result
            else:
                result_data = analysis_result
            
            # FIXED: Check for None explicitly, not truthiness (avoids DataFrame ambiguity)
            if result_data is None:
                return {
                    "response": "No data found.",
                    "result_data": None,
                    "table_data": None
                }
            
            # Reconstruct DataFrame
            df = None
            
            if isinstance(result_data, dict):
                result_type = result_data.get('type')
                
                if result_type == 'dataframe':
                    try:
                        df = pl.DataFrame(
                            {col: [row[i] for row in result_data['data']] 
                             for i, col in enumerate(result_data.get('columns', []))}
                        )
                    except Exception as e:
                        master_logger.error(f"[TABLE_FORMAT] Reconstruction failed: {e}")
                        return {"response": "Error reconstructing data", "result_data": None, "table_data": None}
                        
                elif result_type == 'scalar':
                    scalar_value = result_data.get('value')
                    return {"response": f"Result: {scalar_value}", "result_data": scalar_value, "table_data": None}
                    
            elif isinstance(result_data, pl.DataFrame):
                df = result_data.clone()
            
            # FIXED: Check for None or empty explicitly
            if df is None or (isinstance(df, pl.DataFrame) and len(df) == 0):
                return {"response": "No data found.", "result_data": None, "table_data": None}
            
            # Limit rows
            max_display_rows = 50
            original_row_count = len(df)
            
            # FIXED: Don't override ranking sort order - preserve rank column sorting
            # FIXED: Don't override time series sort order - preserve date column sorting
            if 'rank' in df.columns:
                # For ranking results, preserve the existing rank-based sort order
                master_logger.info(f"[TABLE_FORMAT] Preserving rank-based sort order")
                df = df.head(max_display_rows)
            elif len(df.columns) >= 2:
                # Check if FIRST or SECOND column is a date/time column (time series data)
                is_time_series = False
                time_series_col = None
    
                for col_idx, col in enumerate(df.columns[:2]):  # Check first 2 columns
                    col_dtype = df[col].dtype
                    is_datetime_dtype = col_dtype in [pl.Date, pl.Datetime, pl.Time, pl.Duration]
                    has_date_name = any(keyword in col.lower() for keyword in ['date', 'time', 'period', 'day', 'month', 'year'])
        
                    # Check if values look like dates
                    try:
                        sample_val = df[col][0] if len(df) > 0 else None
                        is_datetime_like = isinstance(sample_val, str) and any(c.isdigit() for c in sample_val) and '-' in sample_val
                    except:
                        is_datetime_like = False
        
                    master_logger.info(f"[TABLE_FORMAT] Column {col_idx} check: col='{col}', dtype={col_dtype}, is_datetime={is_datetime_dtype}, has_date_name={has_date_name}")
        
                    if is_datetime_dtype or (has_date_name and is_datetime_like):
                        is_time_series = True
                        time_series_col = col
                        break
    
                if is_time_series:
                    # For time series, preserve chronological order
                    master_logger.info(f"[TABLE_FORMAT] ✅ Preserving time series chronological order for '{time_series_col}'")
                    df = df.head(max_display_rows)
                else:
                    # Extract ranking flags from nl_result to determine sorting
                    is_bottom_query = getattr(nl_result, 'is_bottom_query', False) if nl_result else False
                    is_top_query = getattr(nl_result, 'is_top_query', False) if nl_result else False
                    
                    # For ranking queries, preserve code-generated order; otherwise apply default sorting
                    if is_bottom_query or is_top_query:
                        master_logger.info(f"[TABLE_FORMAT] ✅ Preserving {'bottom' if is_bottom_query else 'top'} ranking order from generated code")
                        df = df.head(max_display_rows)
                    else:
                        # For non-ranking queries, sort by last column descending (default)
                        value_col = df.columns[-1]
                        master_logger.info(f"[TABLE_FORMAT] Not time series/ranking, sorting by last column '{value_col}' descending")
                        if df[value_col].dtype in [pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64, pl.Float32, pl.Float64]:
                            df = df.sort(value_col, descending=True).head(max_display_rows)
                        else:
                            df = df.head(max_display_rows)
            else:
                df = df.head(max_display_rows)

            displayed_rows = len(df)
            
            # Create table data
            table_data = {
                "columns": df.columns,
                "rows": [[str(val) if val is not None else '' for val in row] 
                        for row in df.rows()],
                "total_rows": original_row_count,
                "displayed_rows": displayed_rows,
                "truncated": original_row_count > displayed_rows
            }
            
            # Create markdown (fallback)
            markdown_table = self._dataframe_to_markdown(df)
            
            # Try templated response first
            response_text = markdown_table
            if query and self.template_engine.can_template_response(intent_type, query):
                master_logger.info(f"[TABLE_FORMAT] Using template engine for {intent_type}")
                templated_response = self.template_engine.format_templated_response(
                    intent_type=intent_type,
                    query=query,
                    df=df,  # polars DataFrame (template engine will convert)
                    fallback_markdown=markdown_table,
                    nl_result=nl_result
                )
                response_text = templated_response
            else:
                master_logger.info(f"[TABLE_FORMAT] Using markdown fallback (query={query}, can_template={self.template_engine.can_template_response(intent_type, query) if query else False})")
            
            if displayed_rows < original_row_count:
                response_text += f"\n\nNote: Showing top {displayed_rows} of {original_row_count} results."
            
            return {
                "response": response_text,
                "result_data": df,
                "table_data": table_data
            }
            
        except Exception as e:
            master_logger.error(f"[TABLE_FORMAT] Error: {e}")
            master_logger.error(traceback.format_exc())
            return {"response": f"Error formatting results: {str(e)}", "result_data": None, "table_data": None}

    def _dataframe_to_markdown(self, df: pl.DataFrame) -> str:
        """Convert polars DataFrame to markdown"""
        try:
            # Convert to pandas for markdown conversion
            return df.to_pandas().to_markdown(index=False)
        except (AttributeError, ImportError):
            lines = []
            headers = '| ' + ' | '.join(f"**{col}**" for col in df.columns) + ' |'
            lines.append(headers)
            separator = '|' + '|'.join(':' + '-' * (len(str(col)) + 2) for col in df.columns) + '|'
            lines.append(separator)
            for row in df.iter_rows(named=False):
                row_str = '| ' + ' | '.join(str(val) for val in row) + ' |'
                lines.append(row_str)
            return '\n'.join(lines)

    def _format_column_description_for_display(self, column_desc_result) -> str:
        """
        🆕 Format column description data for Chrome extension display
        
        Converts structured column description metadata into readable text
        that the Chrome extension can display properly.
        
        Args:
            column_desc_result: Column description result from specialized formatter
            
        Returns:
            Formatted text string for display
        """
        try:
            if not column_desc_result:
                return "❌ No column description data available"
            
            # Handle both structured result and direct descriptions list
            if isinstance(column_desc_result, dict) and 'descriptions' in column_desc_result:
                descriptions = column_desc_result.get('descriptions', [])
            elif isinstance(column_desc_result, list):
                descriptions = column_desc_result
            else:
                descriptions = [column_desc_result] if column_desc_result else []
            
            if not descriptions:
                return "❌ No column descriptions found"
            
            # Format each description nicely
            formatted_parts = []
            
            for desc in descriptions:
                if isinstance(desc, dict):
                    column_name = desc.get('column_name', 'Unknown Column')
                    business_desc = desc.get('business_description', 'No description available')
                    
                    # Create the main description
                    desc_text = f"📋 **{column_name}**\n{business_desc}"
                    
                    # 🆕 ENHANCED STATISTICAL INFO BASED ON DATA TYPE
                    stats = desc.get('statistical_profile', {})
                    data_type_classification = desc.get('data_type_classification', 'unknown')
                    
                    if stats and not stats.get('error'):
                        polars_dtype = stats.get('polars_dtype', stats.get('data_type', 'Unknown'))
                        unique_count = stats.get('unique_count', 'N/A')
                        null_count = stats.get('null_count', 'N/A')
                        total_rows = stats.get('total_rows', 'N/A')
                        
                        # Basic info for all types
                        desc_text += f"\n\n📊 **Data Profile:**"
                        desc_text += f"\n• Type: {data_type_classification.title()} ({polars_dtype})"
                        desc_text += f"\n• Records: {total_rows:,} total, {null_count:,} null ({stats.get('null_percentage', 0):.1f}%)"
                        desc_text += f"\n• Unique Values: {unique_count:,}"
                        
                        # Type-specific enhanced statistics
                        if data_type_classification == 'categorical' and 'mode' in stats:
                            desc_text += f"\n\n📈 **Category Analysis:**"
                            desc_text += f"\n• Most Common: '{stats.get('mode', 'N/A')}' ({stats.get('mode_percentage', 0):.1f}%)"
                            
                            # Show top categories
                            top_values = stats.get('top_values', [])
                            if top_values:
                                desc_text += f"\n• Top Categories:"
                                for i, top_val in enumerate(top_values[:3], 1):
                                    desc_text += f"\n  {i}. {top_val.get('value', 'N/A')}: {top_val.get('count', 0):,} ({top_val.get('percentage', 0):.1f}%)"
                            
                            diversity = stats.get('diversity_index', 0)
                            if diversity > 0:
                                diversity_desc = "High" if diversity > 0.7 else "Medium" if diversity > 0.4 else "Low"
                                desc_text += f"\n• Distribution: {diversity_desc} diversity ({diversity:.2f})"
                            
                            rare_count = stats.get('rare_values_count', 0)
                            if rare_count > 0:
                                desc_text += f"\n• Rare Categories: {rare_count} with <1% frequency"
                                
                        elif data_type_classification == 'numerical':
                            # Check if numerical analysis succeeded or failed
                            if 'error' in stats:
                                # Numerical analysis failed - show error information
                                desc_text += f"\n\n⚠️ **Numerical Analysis Issue:**"
                                desc_text += f"\n• Classification: Marked as numerical in metadata"
                                
                                original_dtype = stats.get('original_dtype', 'Unknown')
                                desc_text += f"\n• Actual Data Type: {original_dtype}"
                                
                                error_msg = stats.get('error', 'Unknown error')
                                if 'conversion' in error_msg.lower():
                                    desc_text += f"\n• Issue: Data cannot be converted to numbers"
                                else:
                                    desc_text += f"\n• Issue: {error_msg}"
                                
                                # Show sample values to help understand the format
                                sample_vals = stats.get('sample_values', [])
                                if sample_vals:
                                    sample_str = ', '.join(str(v) for v in sample_vals[:3])
                                    desc_text += f"\n• Sample Values: {sample_str}"
                                    
                                desc_text += f"\n• Recommendation: Check data format or update classification"
                                
                                # Show fallback categorical analysis if available
                                if 'fallback_analysis' in stats and stats.get('classification_mismatch'):
                                    fallback_stats = stats.get('fallback_analysis', {})
                                    if 'mode' in fallback_stats:
                                        desc_text += f"\n\n📊 **Alternative Analysis (as Categories):**"
                                        desc_text += f"\n• Most Common: '{fallback_stats.get('mode', 'N/A')}' ({fallback_stats.get('mode_percentage', 0):.1f}%)"
                                        
                                        # Show top categories
                                        top_values = fallback_stats.get('top_values', [])
                                        if top_values:
                                            desc_text += f"\n• Top Values:"
                                            for i, top_val in enumerate(top_values[:3], 1):
                                                desc_text += f"\n  {i}. {top_val.get('value', 'N/A')}: {top_val.get('count', 0):,} ({top_val.get('percentage', 0):.1f}%)"
                                
                            elif 'mean' in stats:
                                # Successful numerical analysis
                                desc_text += f"\n\n📈 **Statistical Summary:**"
                                desc_text += f"\n• Mean: {stats.get('mean', 'N/A'):,.2f} | Median: {stats.get('median', 'N/A'):,.2f}"
                                desc_text += f"\n• Range: {stats.get('min', 'N/A'):,.2f} to {stats.get('max', 'N/A'):,.2f}"
                                desc_text += f"\n• Std Dev: {stats.get('std', 'N/A'):,.2f}"
                                
                                # Show conversion info if applicable
                                original_dtype = stats.get('original_dtype', '')
                                if original_dtype.lower() in ['utf8', 'string'] and stats.get('conversion_successful'):
                                    desc_text += f"\n• Data: Converted from {original_dtype} to numbers"
                                
                                # Distribution info
                                distribution = stats.get('distribution_shape', '')
                                if distribution:
                                    distribution_desc = {
                                        'normal': 'Normal distribution',
                                        'right_skewed': 'Right-skewed (long tail high)',
                                        'left_skewed': 'Left-skewed (long tail low)', 
                                        'constant': 'Constant values'
                                    }.get(distribution, f'{distribution} distribution')
                                    desc_text += f"\n• Shape: {distribution_desc}"
                                
                                outlier_count = stats.get('outlier_count', 0)
                                zero_count = stats.get('zero_count', 0)
                                if outlier_count > 0 or zero_count > 0:
                                    desc_text += f"\n• Special Values:"
                                    if outlier_count > 0:
                                        desc_text += f" {outlier_count:,} outliers"
                                    if zero_count > 0:
                                        desc_text += f" {zero_count:,} zeros"
                                    
                        elif data_type_classification == 'identifier' and 'uniqueness_percentage' in stats:
                            desc_text += f"\n\n🔑 **Identifier Analysis:**"
                            uniqueness_pct = stats.get('uniqueness_percentage', 0)
                            desc_text += f"\n• Uniqueness: {uniqueness_pct:.1f}%"
                            
                            is_unique_key = stats.get('is_unique_key', False)
                            if is_unique_key:
                                desc_text += f"\n• Status: ✅ Perfect unique key"
                            else:
                                duplicate_count = stats.get('duplicate_count', 0)
                                if duplicate_count > 0:
                                    desc_text += f"\n• Duplicates: {duplicate_count:,} records"
                                
                            format_info = stats.get('format_info', {})
                            if format_info:
                                length = format_info.get('typical_length', 0)
                                has_nums = format_info.get('contains_numbers', False)
                                has_letters = format_info.get('contains_letters', False)
                                desc_text += f"\n• Format: {length} chars, "
                                format_parts = []
                                if has_nums: format_parts.append("numbers")
                                if has_letters: format_parts.append("letters")
                                desc_text += " + ".join(format_parts) if format_parts else "other"
                        
                        # Add sample values for all types
                        sample_values = stats.get('sample_values', [])
                        if sample_values:
                            samples_str = ', '.join(str(v) for v in sample_values[:5])
                            desc_text += f"\n\n💡 **Sample Values:** {samples_str}"
                    
                    formatted_parts.append(desc_text)
                else:
                    formatted_parts.append(f"❓ {str(desc)}")
            
            # Combine all descriptions
            if len(formatted_parts) == 1:
                result_text = f"✨ **Column Description**\n\n{formatted_parts[0]}"
            else:
                result_text = f"✨ **Column Descriptions ({len(formatted_parts)} fields)**\n\n" + "\n\n---\n\n".join(formatted_parts)
            
            master_logger.info(f"[COLUMN_DESC_DISPLAY] Formatted {len(descriptions)} descriptions for Chrome extension")
            return result_text
            
        except Exception as e:
            master_logger.error(f"[COLUMN_DESC_DISPLAY] Error formatting for display: {e}")
            return f"❌ Error formatting column description: {str(e)}"

    def _format_column_description_result(self, result) -> Dict[str, Any]:
        """
        🆕 Specialized formatting for column description results
        
        Column descriptions are fundamentally different from analytical data:
        - They contain metadata, not tabular data
        - They should be displayed as description cards, not data tables
        - They don't need chart visualizations
        
        Args:
            result: List of column description dictionaries
            
        Returns:
            Formatted result optimized for column description display
        """
        try:
            master_logger.info(f"[COLUMN_DESC_FORMAT] Processing {len(result) if isinstance(result, list) else 1} column description(s)")
            
            # Ensure result is a list
            if not isinstance(result, list):
                result = [result]
            
            # 🆕 PRESERVE ALL ENHANCED STATISTICAL DATA
            descriptions = []
            for desc in result:
                if isinstance(desc, dict):
                    # ✅ Keep the COMPLETE original data structure with all enhanced statistics
                    enhanced_desc = {
                        'column_name': desc.get('column_name', 'Unknown Column'),
                        'business_description': desc.get('business_description', 'No description available'),
                        'data_type_classification': desc.get('data_type_classification', 'unknown'),  # ✅ Preserve classification
                        'statistical_profile': desc.get('statistical_profile', {}),                    # ✅ Preserve ALL stats
                        'detail_level': desc.get('detail_level', 'basic')
                    }
                    
                    # 🔍 DEBUG: Log what we're preserving
                    stats = enhanced_desc.get('statistical_profile', {})
                    master_logger.info(f"[COLUMN_DESC_FORMAT] Preserving enhanced stats for '{enhanced_desc['column_name']}': {len(stats)} metrics")
                    if 'mean' in stats:
                        master_logger.info(f"[COLUMN_DESC_FORMAT] ✅ Numerical stats preserved: mean={stats.get('mean')}")
                    if 'mode' in stats:
                        master_logger.info(f"[COLUMN_DESC_FORMAT] ✅ Categorical stats preserved: mode={stats.get('mode')}")
                    
                    descriptions.append(enhanced_desc)
            
            # Create specialized column description format
            formatted_result = {
                'type': 'column_descriptions',  # 🎯 Special type for Chrome extension
                'descriptions': descriptions,
                'count': len(descriptions),
                'display_format': 'description_cards',  # Hint for frontend rendering
                'metadata': {
                    'is_metadata': True,
                    'requires_special_display': True,
                    'suggested_layout': 'cards' if len(descriptions) <= 3 else 'list'
                }
            }
            
            master_logger.info(f"[COLUMN_DESC_FORMAT] ✅ Formatted {len(descriptions)} column descriptions for display")
            return formatted_result
            
        except Exception as e:
            master_logger.error(f"[COLUMN_DESC_FORMAT] Error formatting column descriptions: {e}")
            # Fallback to basic format
            return {
                'type': 'column_descriptions',
                'descriptions': [{'column_name': 'Error', 'business_description': f'Error formatting descriptions: {str(e)}'}],
                'count': 1,
                'display_format': 'error'
            }

    def _format_aggregation_result(self, result) -> Dict[str, Any]:
        """
        Format analytical data results into serializable dict - handles both pandas and polars
        
        NOTE: This method is for ANALYTICAL DATA only (charts, tables, aggregations)
        Column descriptions use _format_column_description_result() instead
        """
        try:
            # Handle pandas DataFrame (from execute_code)
            if isinstance(result, pd.DataFrame):
                formatted = {
                    'type': 'dataframe',
                    'data': result.values.tolist(),
                    'columns': result.columns.tolist(),
                    'index': result.index.tolist(),
                    'shape': result.shape
                }
                return formatted
            # Handle polars DataFrame
            elif isinstance(result, pl.DataFrame):
                formatted = {
                    'type': 'dataframe',
                    'data': result.rows(),
                    'columns': result.columns,
                    'index': list(range(len(result))),
                    'shape': result.shape
                }
                return formatted
            # Handle pandas Series
            elif isinstance(result, pd.Series):
                return {
                    'type': 'series',
                    'data': result.to_dict(),
                    'name': result.name,
                    'index': result.index.tolist()
                }
            # Handle polars Series
            elif isinstance(result, pl.Series):
                return {
                    'type': 'series',
                    'data': {i: val for i, val in enumerate(result.to_list())},
                    'name': result.name,
                    'index': list(range(len(result)))
                }
            elif isinstance(result, dict):
                # CRITICAL FIX: Check if dictionary contains DataFrame values
                has_dataframe_values = any(isinstance(v, (pd.DataFrame, pd.Series, pl.DataFrame, pl.Series)) for v in result.values())
                
                if has_dataframe_values:
                    master_logger.warning(f"[FORMAT_RESULT] Dict contains DataFrame values - flattening")
                    
                    # Strategy: Combine all DataFrames into a single result
                    combined_data = {}
                    
                    for key, value in result.items():
                        if isinstance(value, pd.DataFrame):
                            # Add key as a column to the DataFrame
                            value_copy = value.copy()
                            value_copy['source'] = key
                            combined_data[key] = value_copy
                        elif isinstance(value, pl.DataFrame):
                            # Add key as a column to the DataFrame
                            value_copy = value.clone()
                            combined_data[key] = value_copy.with_columns(pl.lit(key).alias('source'))
                        elif isinstance(value, pd.Series):
                            combined_data[key] = value.to_frame()
                        elif isinstance(value, pl.Series):
                            combined_data[key] = value.to_frame()
                        else:
                            # Scalar value
                            combined_data[key] = value
                    
                    # If we have multiple DataFrames, concatenate them
                    if any(isinstance(v, (pd.DataFrame, pl.DataFrame)) for v in combined_data.values()):
                        dfs_pandas = [v for v in combined_data.values() if isinstance(v, pd.DataFrame)]
                        dfs_polars = [v for v in combined_data.values() if isinstance(v, pl.DataFrame)]
                        
                        # Convert polars to pandas for concatenation
                        dfs = dfs_pandas + [df.to_pandas() for df in dfs_polars]
                        
                        if len(dfs) > 1:
                            try:
                                combined_df = pd.concat(dfs, ignore_index=True)
                                return {
                                    'type': 'dataframe',
                                    'data': combined_df.values.tolist(),
                                    'columns': combined_df.columns.tolist(),
                                    'index': combined_df.index.tolist(),
                                    'shape': combined_df.shape
                                }
                            except Exception as e:
                                master_logger.error(f"[FORMAT_RESULT] Failed to concatenate DataFrames: {e}")
                                # Fallback: return first DataFrame only
                                first_df = dfs[0]
                                return {
                                    'type': 'dataframe',
                                    'data': first_df.values.tolist(),
                                    'columns': first_df.columns.tolist(),
                                    'index': first_df.index.tolist(),
                                    'shape': first_df.shape
                                }
                        else:
                            # Single DataFrame
                            df = dfs[0]
                            return {
                                'type': 'dataframe',
                                'data': df.values.tolist(),
                                'columns': df.columns.tolist(),
                                'index': df.index.tolist(),
                                'shape': df.shape
                            }
                    else:
                        # No DataFrames, just scalar values
                        return {
                            'type': 'dict',
                            **result
                        }
                else:
                    # Plain dictionary (no DataFrame values)
                    return {
                        'type': 'dict',
                        **result
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
            master_logger.error(traceback.format_exc())
            return {
                'type': 'error',
                'value': str(result)
            }

    def execute_pandas_aggregation_with_codet5(self, query: str, df: pl.DataFrame, 
                                               intent_result=None, chart_context=None) -> Dict[str, Any]:
        """Execute pandas aggregation using NL_to_python - accepts polars, converts for code execution"""
        try:
            master_logger.info("=" * 80)
            master_logger.info("[3-LAYER_AGGREGATION] Starting pandas aggregation")
            master_logger.info(f"Query: '{query}'")
            
            df_for_code = self.original_csv_data if self.original_csv_data is not None else df
            
            # 🆕 Get workbook_name for metadata-based identifier detection
            workbook_name = getattr(self, 'workbook_name', None)
            master_logger.info(f"🔍 DEBUG: workbook_name extracted = {workbook_name}")
            master_logger.info(f"🔍 DEBUG: self.workbook_name = {getattr(self, 'workbook_name', 'NOT SET')}")
            
            # 🆕 LAYER 0: Query Normalization with Driver-Aware Rephrasing
            try:
                from services.layer0_constrained_parser import create_query_normalizer
                
                master_logger.info("[LAYER0] Normalizing query...")
                layer0_normalizer = create_query_normalizer(self.llm_client)
                
                # Get normalized query (driver hints stay internal, not passed forward)
                normalized_query = layer0_normalizer.normalize(query)
                master_logger.info(f"[LAYER0] ✅ Normalized: '{query}' → '{normalized_query}'")
                
                query_to_use = normalized_query
                
            except Exception as layer0_error:
                master_logger.error(f"[LAYER0] ❌ Normalization failed, falling back to original query")
                master_logger.error(f"[LAYER0] Error: {type(layer0_error).__name__}: {layer0_error}")
                master_logger.error(f"[LAYER0] Traceback:\n{traceback.format_exc()}")
                query_to_use = query
            
            # Generate code using LangGraph workflow (using polars DataFrame directly)
            nl_result = self.nl_to_python.generate_python_code(
                query=query_to_use,
                df_columns=list(df_for_code.columns),
                df_sample=df_for_code,
                workbook_name=workbook_name  # 🆕 Pass workbook_name for identifier detection
            )
            
            if not nl_result or not nl_result.generated_code:
                master_logger.error(f"[3-LAYER_AGGREGATION] Code generation failed")
                return {
                    'query': query,
                    'execution_status': 'error',
                    'error': 'Code generation failed',
                    'result': None
                }
            
            generated_code = nl_result.generated_code
            # Fix indentation issues in generated code
            import textwrap
            generated_code = textwrap.dedent(generated_code)
            master_logger.info(f"[3-LAYER_AGGREGATION] Generated code:\n{generated_code}")
            
            # 🔍 ADD DEBUG HERE - Before execution
            master_logger.info("=" * 80)
            master_logger.info("🔍 PRE-EXECUTION DEBUG")
            master_logger.info(f"1. df_for_code shape: {df_for_code.shape}")
            if 'account_country' in df_for_code.columns:
                master_logger.info(f"2. Brazil rows: {len(df_for_code.filter(pl.col('account_country') == 'Brazil'))}")
            if 'create_day' in df_for_code.columns:
                master_logger.info(f"3. create_day dtype: {df_for_code['create_day'].dtype}")
                master_logger.info(f"4. create_day first 3 values: {df_for_code['create_day'].head(3).to_list()}")
            if 'case_id' in df_for_code.columns:
                master_logger.info(f"5. case_id dtype: {df_for_code['case_id'].dtype}")
                master_logger.info(f"6. case_id first 3 values: {df_for_code['case_id'].head(3).to_list()}")
            master_logger.info("=" * 80)
        
            # Execute with polars DataFrame
            result, status = self.execute_code(generated_code, df_for_code)

            
            if result is None or "Error" in status:
                master_logger.error(f"[3-LAYER_AGGREGATION] Execution failed: {status}")
                return {
                    'query': query,
                    'execution_status': 'error',
                    'error': status,
                    'result': None
                }
            
            # 🆕 SPECIALIZED HANDLING FOR COLUMN DESCRIPTIONS
            if nl_result.operation_type == 'column_description':
                master_logger.info("[COLUMN_DESC_FORMAT] ✨ Using specialized column description formatting")
                formatted_result = self._format_column_description_result(result)
            else:
                # Regular data analysis formatting
                formatted_result = self._format_aggregation_result(result)
            
            return {
                'query': query,
                'generated_code': generated_code,
                'operation_type': nl_result.operation_type,
                'confidence': nl_result.confidence,
                'explanation': nl_result.explanation,
                'execution_status': status,
                'result': formatted_result,
                'result_type': type(result).__name__,
                'nl_result': nl_result  # Pass through for template engine
            }
            
        except Exception as e:
            master_logger.error(f"[3-LAYER_AGGREGATION] Error: {e}")
            master_logger.error(traceback.format_exc())
            return {
                'query': query,
                'execution_status': 'error',
                'error': str(e),
                'result': None
            }

    def execute_code(self, code: str, df: pl.DataFrame) -> Tuple[Any, str]:
        """Execute polars code in sandbox"""
        try:
            try:
                from scipy import stats
            except ImportError:
                stats = None

            safe_globals = {  
                'df': df.clone(), 
                'pl': pl,
                'pd': pd,  # Keep pandas available for compatibility
                'np': np,
                'datetime': __import__('datetime'),  # For pure Python datetime operations
                'stats': stats,
                'original_total': len(df)  # Add original_total for percentage calculations
            }
            safe_locals = {}
            
            exec(code, safe_globals, safe_locals)
            
            if 'result' in safe_locals:
                result = safe_locals['result']
            elif 'result' in safe_globals:
                result = safe_globals['result']
            else:
                master_logger.error("Code did not produce a 'result' variable")
                return None, "Error: Code did not produce a 'result' variable"
            
            # Handle polars Series - convert to DataFrame
            if isinstance(result, pl.Series):
                metric_name = result.name if result.name else 'value'
                result = result.to_frame(name=metric_name)
            
            # Pandas Series handling (if code still produces pandas)
            if isinstance(result, pd.Series):
                metric_name = result.name if result.name else 'value'
                result = result.reset_index(name=metric_name)
            
            # Pandas DataFrame handling (if code still produces pandas)
            if isinstance(result, pd.DataFrame) and not isinstance(result.index, pd.RangeIndex):
                result = result.reset_index()
            
            return result, "success"
            
        except Exception as e:
            master_logger.error(f"Exception during code execution: {type(e).__name__}: {e}")
            master_logger.error(traceback.format_exc())
            return None, f"Error executing code: {e}"
    