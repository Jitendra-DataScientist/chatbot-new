"""
Data Exploration Service
Enhanced with proper result extraction and visualization handling
Fixed: Dictionary result to DataFrame conversion, visualization timeouts
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
import pandas as pd
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

master_logger = setup_module_logger('services.data_exploration')
master_logger.info("DATA EXPLORATION SERVICE MODULE INITIALIZATION STARTED")


class data_exploration:
    """
    Self-contained class for data exploration queries with enhanced result handling
    """
    
    def __init__(self, llm_client, smart_agg_decider, cache_path: str = "causal_analysis_cache.json"):
        """Initialize the data_exploration class"""
        master_logger.info("=== INITIALIZING DATA EXPLORATION SERVICE ===")
        
        self.llm_client = llm_client
        self.smart_aggregation_decider = smart_agg_decider
        self.cache_path = cache_path
        
        # Initialize services
        self.llm_service = LLMService(openai_client=llm_client)
        self.data_processor = TableauDataProcessor()
        self.viz_service = IntelligentVisualizationService()
        self.multi_table_service = TableauMultiTableService()
        self.insight_generator = TableauInsightGenerator()
        self.nl_to_python = NLToPythonGenerator(openai_client=llm_client)
        self.enhanced_analysis = EnhancedAnalysisService(self.data_processor, self.nl_to_python)
        self.fuzzy_matcher = FuzzyColumnMatcher(threshold=70)
        
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
                csv_data: pd.DataFrame,
                selected_chart: str,
                intent_result = None,
                chart_context: Optional[Dict] = None,
                conversation_state: Optional[Dict] = None,
                use_conversation: bool = True) -> Dict[str, Any]:
        """Process exploration query"""
        master_logger.info("=" * 80)
        master_logger.info("=== PROCESSING DATA EXPLORATION QUERY ===")
        master_logger.info(f"Query: '{query_text}'")
        master_logger.info(f"Selected chart: {selected_chart}")
        master_logger.info(f"Use conversation: {use_conversation}")
        master_logger.info(f"Has conversation state: {conversation_state is not None}")
        
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
                                        csv_data: pd.DataFrame,
                                        selected_chart: str,
                                        intent_result,
                                        chart_context: Optional[Dict],
                                        conversation_state: Optional[Dict]) -> Dict[str, Any]:
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
            
            if not is_scalar_result and self._requires_visualization(intent_result):
                visualization = await self._create_visualization_for_intent(
                    intent_result, {'pandas_execution': analysis_result}, analysis_data
                )
            
            response_dict = self._format_table_response({'pandas_execution': analysis_result})
            
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
                                   csv_data: pd.DataFrame,
                                   selected_chart: str,
                                   intent_result,
                                   chart_context: Optional[Dict]) -> Dict[str, Any]:
        """Process query using single-turn logic"""
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
            
            # Step 4: Generate visualization (FIXED SECTION)
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
                                    viz_ready_df = pd.DataFrame(
                                        result_data['data'],
                                        columns=result_data.get('columns', [])
                                    )
                                    master_logger.info(f"[VIZ_PREP] Reconstructed DataFrame: shape={viz_ready_df.shape}")
                                else:
                                    # Simple dictionary - extract non-type keys
                                    dict_items = {k: v for k, v in result_data.items() if k != 'type'}
                                    
                                    # ENHANCED: Check if dict values are DataFrames
                                    has_dataframe_values = any(isinstance(v, (pd.DataFrame, dict)) and 
                                                               (isinstance(v, pd.DataFrame) or ('data' in v and 'columns' in v))
                                                               for v in dict_items.values())
                                    
                                    if has_dataframe_values:
                                        master_logger.warning("[VIZ_PREP] Dict contains nested DataFrames - cannot visualize directly")
                                        master_logger.warning(f"[VIZ_PREP] Dict keys: {list(dict_items.keys())}")
                                        is_scalar_result = True  # Treat as non-visualizable
                                    elif dict_items:
                                        # Simple key-value pairs
                                        viz_ready_df = pd.DataFrame([dict_items]).T.reset_index()
                                        viz_ready_df.columns = ['Category', 'Count']
                                        master_logger.info(f"[VIZ_PREP] Created DataFrame from dict: shape={viz_ready_df.shape}")
                                        master_logger.info(f"[VIZ_PREP] DataFrame preview:\n{viz_ready_df.head()}")
                                        
                                        # Update pandas_execution for consistency
                                        pandas_execution['result'] = {
                                            'type': 'dataframe',
                                            'data': viz_ready_df.values.tolist(),
                                            'columns': viz_ready_df.columns.tolist(),
                                            'index': viz_ready_df.index.tolist(),
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
                                viz_ready_df = pd.DataFrame(
                                    result_data['data'],
                                    columns=result_data.get('columns', [])
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
            if analysis_result and self._requires_visualization(intent_result) and not is_scalar_result:
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
            
            response_dict = self._format_table_response(analysis_result)
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
            result_type = analysis_result.get('result', {}).get('type')
            result_shape = analysis_result.get('result', {}).get('shape', (0, 0))
            return (result_type == 'scalar') or (result_type == 'dataframe' and result_shape[0] == 1)
        return False
    
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
                filtered_data = csv_data[available_columns]
                master_logger.info(f"Filtered to {len(available_columns)} columns")
                return filtered_data, cache_data
            else:
                return csv_data, cache_data
        else:
            return csv_data, None

    async def _execute_intent_analysis(self, intent_result, query: str, data, chart_context=None) -> Dict[str, Any]:
        """Execute analysis"""
        
        if data is None or data.empty:
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
            
            return analysis_result
            
        except Exception as e:
            master_logger.error(f"Error executing analysis: {e}")
            return {"error": str(e), "intent_type": intent_type}
    
    def _requires_visualization(self, intent_result) -> bool:
        """Determine if visualization is needed"""
        if intent_result is None:
            master_logger.info("[VIZ_CHECK] No intent_result, defaulting to visualization=True")
            return True
        
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
            if isinstance(data, pd.DataFrame) and not data.empty:
                master_logger.info(f"[VIZ] Using prepared DataFrame: shape={data.shape}")
                master_logger.info(f"[VIZ] DataFrame columns: {list(data.columns)}")
                master_logger.info(f"[VIZ] DataFrame preview:\n{data.head()}")
                
                # Use the prepared DataFrame directly
                viz_df = data
            else:
                # Fallback: try to reconstruct from analysis_result
                master_logger.warning("[VIZ] Data parameter not a DataFrame, attempting reconstruction")
                pandas_result = analysis_result.get('pandas_execution', {})
                result_data = pandas_result.get('result', {})
                
                if result_data and result_data.get('type') == 'dataframe':
                    try:
                        viz_df = pd.DataFrame(
                            result_data['data'],
                            columns=result_data.get('columns', [])
                        )
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
    
    def _format_table_response(self, analysis_result, query: str = None) -> Dict[str, Any]:
        """Format analysis result into table response - FIXED DataFrame ambiguity error"""
        try:
            master_logger.info(f"[TABLE_FORMAT] Formatting result")
            
            # Extract result
            result_data = None
            
            if isinstance(analysis_result, dict):
                if 'pandas_execution' in analysis_result:
                    pandas_exec = analysis_result['pandas_execution']
                    if 'result' in pandas_exec:
                        result_data = pandas_exec['result']
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
                        df = pd.DataFrame(
                            result_data['data'],
                            columns=result_data.get('columns', []),
                            index=result_data.get('index', None)
                        )
                    except Exception as e:
                        master_logger.error(f"[TABLE_FORMAT] Reconstruction failed: {e}")
                        return {"response": "Error reconstructing data", "result_data": None, "table_data": None}
                        
                elif result_type == 'scalar':
                    scalar_value = result_data.get('value')
                    return {"response": f"Result: {scalar_value}", "result_data": scalar_value, "table_data": None}
                    
            elif isinstance(result_data, pd.DataFrame):
                df = result_data.copy()
            
            # FIXED: Check for None or empty explicitly
            if df is None or (isinstance(df, pd.DataFrame) and df.empty):
                return {"response": "No data found.", "result_data": None, "table_data": None}
            
            # Handle indices
            if isinstance(df.index, pd.MultiIndex):
                df.index = df.index.map(lambda x: ' - '.join(str(v) for v in x))
            
            is_meaningful_index = not isinstance(df.index, pd.RangeIndex)
            if is_meaningful_index:
                df = df.reset_index()
            
            # Limit rows
            max_display_rows = 50
            original_row_count = len(df)
            # change for time series by aniket
            # FIXED: Don't override ranking sort order - preserve rank column sorting
            # FIXED: Don't override time series sort order - preserve date column sorting
            if 'rank' in df.columns:
                # For ranking results, preserve the existing rank-based sort order
                master_logger.info(f"[TABLE_FORMAT] Preserving rank-based sort order")
                df = df.head(max_display_rows)
            elif len(df.columns) >= 2:
                # Check if FIRST or SECOND column is a date/time column (time series data)
                # (Second column check handles reset_index() creating an 'index' column)
                is_time_series = False
                time_series_col = None
    
                for col_idx, col in enumerate(df.columns[:2]):  # Check first 2 columns
                    col_dtype = df[col].dtype
                    is_datetime_dtype = pd.api.types.is_datetime64_any_dtype(col_dtype)
                    has_date_name = any(keyword in col.lower() for keyword in ['date', 'time', 'period', 'day', 'month', 'year'])
        
                    # Check if values look like dates
                    try:
                        sample_val = df[col].iloc[0] if len(df) > 0 else None
                        is_datetime_like = isinstance(sample_val, (pd.Timestamp, np.datetime64)) or \
                             (isinstance(sample_val, str) and any(c.isdigit() for c in sample_val) and '-' in sample_val)
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
                    # For other data, sort by last column if numeric
                    value_col = df.columns[-1]
                    master_logger.info(f"[TABLE_FORMAT] Not time series, sorting by last column '{value_col}' descending")
                    if pd.api.types.is_numeric_dtype(df[value_col]):
                        df = df.sort_values(value_col, ascending=False).head(max_display_rows)
                    else:
                        df = df.head(max_display_rows)
            else:
                df = df.head(max_display_rows)

             #time series formatting end
            displayed_rows = len(df)
            
            # Create table data
            table_data = {
                "columns": df.columns.tolist(),
                "rows": [[str(val) if val is not None and not pd.isna(val) else '' for val in row] 
                        for row in df.values.tolist()],
                "total_rows": original_row_count,
                "displayed_rows": displayed_rows,
                "truncated": original_row_count > displayed_rows
            }
            
            # Create markdown
            markdown_table = self._dataframe_to_markdown(df)
            
            response_text = markdown_table
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

    def _dataframe_to_markdown(self, df: pd.DataFrame) -> str:
        """Convert DataFrame to markdown"""
        try:
            return df.to_markdown(index=False)
        except AttributeError:
            lines = []
            headers = '| ' + ' | '.join(f"**{col}**" for col in df.columns) + ' |'
            lines.append(headers)
            separator = '|' + '|'.join(':' + '-' * (len(str(col)) + 2) for col in df.columns) + '|'
            lines.append(separator)
            for _, row in df.iterrows():
                row_str = '| ' + ' | '.join(str(val) for val in row.values) + ' |'
                lines.append(row_str)
            return '\n'.join(lines)

    def _format_aggregation_result(self, result) -> Dict[str, Any]:
        """Format pandas result into serializable dict - FIXED to handle nested DataFrames"""
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
            elif isinstance(result, dict):
                # CRITICAL FIX: Check if dictionary contains DataFrame values
                has_dataframe_values = any(isinstance(v, (pd.DataFrame, pd.Series)) for v in result.values())
                
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
                        elif isinstance(value, pd.Series):
                            combined_data[key] = value.to_frame()
                        else:
                            # Scalar value
                            combined_data[key] = value
                    
                    # If we have multiple DataFrames, concatenate them
                    if any(isinstance(v, pd.DataFrame) for v in combined_data.values()):
                        dfs = [v for v in combined_data.values() if isinstance(v, pd.DataFrame)]
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

    def execute_pandas_aggregation_with_codet5(self, query: str, df: pd.DataFrame, 
                                               intent_result=None, chart_context=None) -> Dict[str, Any]:
        """Execute pandas aggregation using NL_to_python"""
        try:
            master_logger.info("=" * 80)
            master_logger.info("[3-LAYER_AGGREGATION] Starting pandas aggregation")
            master_logger.info(f"Query: '{query}'")
            
            df_for_code = self.original_csv_data if self.original_csv_data is not None else df
            
            # Generate code using LangGraph workflow
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
            master_logger.info(f"2. Brazil rows: {len(df_for_code[df_for_code['account_country'] == 'Brazil'])}")
            master_logger.info(f"3. create_day dtype: {df_for_code['create_day'].dtype}")
            master_logger.info(f"4. create_day first 3 values: {df_for_code['create_day'].head(3).tolist()}")
            master_logger.info(f"5. case_id dtype: {df_for_code['case_id'].dtype}")
            master_logger.info(f"6. case_id first 3 values: {df_for_code['case_id'].head(3).tolist()}")
            master_logger.info("=" * 80)
        
            # Execute
            result, status = self.execute_code(generated_code, df_for_code)
            
            if result is None or "Error" in status:
                master_logger.error(f"[3-LAYER_AGGREGATION] Execution failed: {status}")
                return {
                    'query': query,
                    'execution_status': 'error',
                    'error': status,
                    'result': None
                }
            
            return {
                'query': query,
                'generated_code': generated_code,
                'operation_type': nl_result.operation_type,
                'confidence': nl_result.confidence,
                'explanation': nl_result.explanation,
                'execution_status': status,
                'result': self._format_aggregation_result(result),
                'result_type': type(result).__name__
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

    def execute_code(self, code: str, df: pd.DataFrame) -> Tuple[Any, str]:
        """Execute pandas code in sandbox"""
        try:
            try:
                from scipy import stats
            except ImportError:
                stats = None

            safe_globals = {  
                'df': df.copy(), 
                'pd': pd, 
                'np': np,
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
                return None, "Error: Code did not produce a 'result' variable"
            
            if isinstance(result, pd.Series):
                metric_name = result.name if result.name else 'value'
                result = result.reset_index(name=metric_name)
            
            if isinstance(result, pd.DataFrame) and not isinstance(result.index, pd.RangeIndex):
                result = result.reset_index()
            
            return result, "success"
            
        except Exception as e:
            return None, f"Error executing code: {e}"