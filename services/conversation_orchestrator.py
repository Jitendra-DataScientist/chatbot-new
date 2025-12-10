"""
Conversation Orchestrator
Manages multi-turn conversations for data exploration queries
Integrates with NL to Python workflow for conversation-aware query processing
"""

import logging
import time
from datetime import datetime
from typing import Dict, List, Any, Optional
import polars as pl

from master_logger import setup_module_logger
from services.nlp_to_python.nl_to_python_schemas import UserDisambiguationRequired
from services.utils import to_dict_safe, get_nested_value
from services.context_manager.conversation_memory import ConversationMemory
from services.layer0_constrained_parser import create_query_normalizer


class ConversationOrchestrator:
    """
    Orchestrates multi-turn conversational data exploration.

    Responsibilities:
    - Maintain conversation state across queries
    - Detect follow-up questions and reference previous context
    - Delegate to NL_to_python generator with conversation context
    - Handle clarification and disambiguation flows
    """

    def __init__(self, llm_client, nl_to_python_generator):
        """
        Initialize ConversationOrchestrator

        Args:
            llm_client: OpenAI client for LLM interactions
            nl_to_python_generator: NLToPythonGenerator instance
        """
        self.logger = setup_module_logger('services.conversation_orchestrator')
        self.llm_client = llm_client
        self.nl_to_python = nl_to_python_generator
        
        # Create our own ConversationMemory instance for follow-up detection
        # (Separate from nl_to_python's DefaultContextManager to avoid state mixing)
        self.conversation_memory = ConversationMemory()
        
        # Initialize Layer 0 for follow-up query enrichment
        try:
            self.layer0_normalizer = create_query_normalizer(llm_client)
            self.logger.info("Layer 0 query normalizer initialized for follow-up enrichment")
        except Exception as e:
            self.logger.warning(f"Layer 0 initialization failed: {e}")
            self.layer0_normalizer = None

        # Execution callbacks (set by data_exploration service)
        self.execute_pandas_fn = None
        self.apply_column_cleaning_fn = None

        self.logger.info("ConversationOrchestrator initialized with ConversationMemory")

    def set_execution_callbacks(self, execute_pandas_fn, apply_column_cleaning_fn):
        """
        Set callback functions for code execution

        Args:
            execute_pandas_fn: Function to execute generated pandas code
            apply_column_cleaning_fn: Function to apply column cleaning
        """
        self.execute_pandas_fn = execute_pandas_fn
        self.apply_column_cleaning_fn = apply_column_cleaning_fn
        self.logger.info("Execution callbacks configured")

    async def process_query(self,
                           query: str,
                           df_data: pl.DataFrame,
                           df_columns: List[str],
                           selected_chart: Optional[str] = None,
                           chart_context: Optional[Dict] = None,
                           conversation_state: Optional[Dict] = None,
                           query_metadata: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Process a conversational query with context awareness

        Args:
            query: User's natural language query
            df_data: DataFrame containing the data
            df_columns: List of column names
            selected_chart: Selected chart name (if any)
            chart_context: Chart-specific context
            conversation_state: Previous conversation state
            query_metadata: Query metadata (is_followup, merged_by, original_query, etc.)

        Returns:
            Dict with query result, conversation state, and metadata
        """
        self.logger.info(f"[CONVERSATION] Processing query: '{query}'")
        self.logger.info(f"[CONVERSATION] Has conversation state: {conversation_state is not None}")

        start_time = time.time()

        try:
            # Initialize conversation state if not provided
            if conversation_state is None:
                conversation_state = {
                    'history': [],
                    'context': {},
                    'session_id': f"session_{int(time.time())}"
                }

            # Extract session_id from conversation_state
            session_id = conversation_state.get('session_id', 'default')

            # ═══════════════════════════════════════════════════════
            # 🆕 LAYER 0 NORMALIZATION + FOLLOW-UP DETECTION
            # ═══════════════════════════════════════════════════════
            normalized_query = query
            is_followup = False
            
            # 🆕 Check if query was already merged by query_understanding_agent
            if query_metadata and query_metadata.get('merged_by') == 'query_understanding_agent':
                self.logger.info(f"[CONVERSATION] ✅ Query already merged by query_understanding_agent")
                self.logger.info(f"[CONVERSATION] Using pre-merged query: '{query}'")
                normalized_query = query  # Already merged!
                is_followup = query_metadata.get('is_followup', False)
            # Otherwise, check if this is a follow-up question and merge here
            elif conversation_state.get('history') and len(conversation_state['history']) > 0:
                # Use our own ConversationMemory for follow-up detection
                is_followup, prev_context = self.conversation_memory.detect_followup(
                    query, 
                    conversation_state
                )
                
                if is_followup and prev_context:
                    last_entry = conversation_state['history'][-1]
                    # Get Layer 0's previous NORMALIZED output (not raw query)
                    prev_normalized_query = last_entry.get('normalized_query', last_entry.get('query', ''))
                    
                    self.logger.info(f"[CONVERSATION] 🔗 FOLLOW-UP DETECTED")
                    self.logger.info(f"[CONVERSATION] Previous normalized query: '{prev_normalized_query[:50]}...'")
                    
                    # Use Layer 0 to merge queries
                    if self.layer0_normalizer:
                        try:
                            self.logger.info(f"[LAYER0] Using Layer 0 LLM to merge follow-up with previous query")
                            followup_result = self.layer0_normalizer.normalize_with_context(
                                query=query,
                                previous_normalized_query=prev_normalized_query
                            )
                            
                            normalized_query = followup_result['enriched_query']
                            self.logger.info(f"[LAYER0] ✅ Merged query: '{normalized_query}'")
                            
                        except Exception as layer0_error:
                            self.logger.error(f"[LAYER0] Follow-up merge failed: {layer0_error}")
                            self.logger.warning(f"[LAYER0] Falling back to original query")
                            normalized_query = query
                    else:
                        self.logger.warning(f"[LAYER0] Layer 0 not available")
                        normalized_query = query
                else:
                    self.logger.info(f"[CONVERSATION] Not a follow-up")
            
            # If not a follow-up (or no history), normalize through Layer 0
            if not is_followup and self.layer0_normalizer:
                try:
                    self.logger.info(f"[LAYER0] Normalizing standalone query through Layer 0")
                    normalized_query = self.layer0_normalizer.normalize(query)
                    self.logger.info(f"[LAYER0] ✅ Normalized: '{normalized_query}'")
                except Exception as e:
                    self.logger.error(f"[LAYER0] Normalization failed: {e}")
                    self.logger.warning(f"[LAYER0] Using original query")
                    normalized_query = query

            self.logger.info(f"[CONVERSATION] Using session_id: {session_id}")
            self.logger.info(f"[CONVERSATION] Query to NL→Python: '{normalized_query}'")

            # Delegate to NL to Python generator with session context
            # The generator will use its internal conversation history
            # Note: execute_pandas_fn expects (query, df, intent_result, chart_context)
            try:
                result = self.execute_pandas_fn(
                    query=normalized_query,  # 🆕 Use Layer 0 normalized query
                    df=df_data,
                    intent_result=None,
                    chart_context=chart_context
                )
            except UserDisambiguationRequired:
                # Re-raise UserDisambiguationRequired to trigger 3-button UI
                # This exception needs to propagate to the API layer
                self.logger.info(f"[CONVERSATION] User disambiguation required, re-raising")
                raise
            except Exception as exec_error:
                # Handle other errors
                self.logger.error(f"[CONVERSATION] Error during code execution: {exec_error}", exc_info=True)
                return {
                    'success': False,
                    'error': str(exec_error),
                    'response': f"Error executing query: {str(exec_error)}",
                    'conversation_state': conversation_state,
                    'needs_clarification': False,
                    'execution_time': time.time() - start_time
                }

            # Update conversation state with current query and comprehensive context
            # Extract entities from nl_result for follow-up detection
            nl_result_raw = result.get('nl_result') if isinstance(result, dict) else None
            nl_result_dict = to_dict_safe(nl_result_raw)
            
            # Build comprehensive entity structure
            query_entities = self._extract_entities_from_nl_result(nl_result_raw, nl_result_dict, result)
            
            # Extract result summary (top N rows for reference queries)
            result_summary = self._extract_result_summary(result)
            
            # Get intent from result
            intent = result.get('intent') or nl_result_dict.get('operation_type', 'unknown')
            
            # Update conversation history using our own ConversationMemory
            # Store both original query and Layer 0's normalized output
            updated_state = self.conversation_memory.add_query(
                state=conversation_state,
                query=query,  # Original user query
                entities=query_entities,
                success=result.get('success', False),
                result_summary=result_summary,
                enriched_query=normalized_query if normalized_query != query else None,  # Layer 0 normalized output
                generated_code=result.get('generated_code'),
                intent=intent
            )
            
            # Update conversation_state with new history
            conversation_state['history'] = updated_state.get('conversation_history', [])
            self.logger.info(f"[CONVERSATION] 💾 Saved to history: {len(conversation_state['history'])} total queries")
            
            self.logger.info(f"[CONVERSATION] Saved entities: {list(query_entities.keys())}")

            # History is managed by ConversationMemory (MAX_HISTORY_SIZE=100)
            # No need to trim here

            # Build response
            # Check if result is a dict or other structure
            if isinstance(result, dict):
                success = result.get('execution_status') != 'error' and result.get('result') is not None
                response = {
                    'success': success,
                    'result': result.get('result'),  # This is the actual result data
                    'response': result.get('formatted_response', ''),
                    'conversation_state': conversation_state,
                    'needs_clarification': False,
                    'execution_time': time.time() - start_time,
                    'query': result.get('query'),
                    'generated_code': result.get('generated_code'),
                    'operation_type': result.get('operation_type'),
                    'nl_result': result.get('nl_result')  # Pass through for ranking flags and template engine
                }

                # Add error info if present
                if 'error' in result:
                    response['error'] = result['error']
                    response['success'] = False

                # Add visualization if present
                if 'visualization' in result:
                    response['visualization'] = result['visualization']

                # Add metadata
                if 'metadata' in result:
                    response['metadata'] = result['metadata']
            else:
                # Result is not a dict (shouldn't happen, but handle gracefully)
                response = {
                    'success': True,
                    'result': result,
                    'response': '',
                    'conversation_state': conversation_state,
                    'needs_clarification': False,
                    'execution_time': time.time() - start_time
                }

            self.logger.info(f"[CONVERSATION] Query processed successfully in {response['execution_time']:.2f}s")

            return response

        except UserDisambiguationRequired:
            # Re-raise UserDisambiguationRequired to trigger 3-button UI
            # This exception needs to propagate to the API layer
            self.logger.info(f"[CONVERSATION] User disambiguation required (outer catch), re-raising")
            raise
        except Exception as e:
            self.logger.error(f"[CONVERSATION] Error processing query: {e}", exc_info=True)

            return {
                'success': False,
                'error': str(e),
                'response': f"I encountered an error processing your query: {str(e)}",
                'conversation_state': conversation_state,
                'needs_clarification': False,
                'execution_time': time.time() - start_time
            }

    def detect_follow_up(self, query: str, conversation_state: Dict) -> bool:
        """
        Detect if query is a follow-up question

        Args:
            query: Current query text
            conversation_state: Previous conversation state

        Returns:
            True if query appears to be a follow-up
        """
        # Simple heuristics for follow-up detection
        follow_up_indicators = [
            'what about', 'how about', 'instead', 'also',
            'same but', 'previous', 'last', 'earlier',
            'that', 'those', 'these', 'this'
        ]

        query_lower = query.lower()
        has_history = conversation_state and len(conversation_state.get('history', [])) > 0

        return has_history and any(indicator in query_lower for indicator in follow_up_indicators)

    def _extract_entities_from_nl_result(self, nl_result_raw, nl_result_dict: Dict, result: Dict) -> Dict[str, Any]:
        """
        Extract comprehensive entities from NLToPythonResult for conversation context
        
        Args:
            nl_result_raw: Raw nl_result object (Pydantic model)
            nl_result_dict: Dict version of nl_result
            result: Full result dictionary
            
        Returns:
            Dict with extracted entities
        """
        self.logger.debug(f"[ENTITY_EXTRACT] Starting entity extraction")
        self.logger.debug(f"[ENTITY_EXTRACT] nl_result_raw type: {type(nl_result_raw)}")
        self.logger.debug(f"[ENTITY_EXTRACT] nl_result_dict keys: {list(nl_result_dict.keys()) if nl_result_dict else 'None'}")
        
        entities = {}
        
        # Primary entity (from group_by_columns)
        group_by = get_nested_value(nl_result_raw, 'group_by_columns', default=[])
        self.logger.debug(f"[ENTITY_EXTRACT] Extracted group_by_columns: {group_by}")
        
        if group_by and len(group_by) > 0:
            entities['primary_entity'] = group_by[0]  # First grouping column
            entities['primary_entity_label'] = self._humanize_column_name(group_by[0])
            entities['group_by'] = group_by
            self.logger.info(f"[ENTITY_EXTRACT] Primary entity: '{entities['primary_entity']}' → label: '{entities['primary_entity_label']}'")
        else:
            entities['primary_entity'] = ''
            entities['primary_entity_label'] = ''
            entities['group_by'] = []
            self.logger.warning(f"[ENTITY_EXTRACT] No group_by_columns found in nl_result")
        
        # Metric/aggregation column
        metric_col = get_nested_value(nl_result_raw, 'metric_column', default='')
        self.logger.debug(f"[ENTITY_EXTRACT] Extracted metric_column: '{metric_col}'")
        
        if metric_col:
            entities['metric'] = self._humanize_column_name(metric_col)
            entities['metric_column'] = metric_col
            self.logger.info(f"[ENTITY_EXTRACT] Metric: '{metric_col}' → label: '{entities['metric']}'")
        else:
            entities['metric'] = ''
            entities['metric_column'] = ''
            self.logger.warning(f"[ENTITY_EXTRACT] No metric_column found in nl_result")
        
        # Filter column
        filter_col = get_nested_value(nl_result_raw, 'filter_column', default='')
        if filter_col:
            entities['filter_column'] = filter_col
        
        # Operation type
        operation = get_nested_value(nl_result_raw, 'operation_type', default='unknown')
        entities['operation'] = operation
        
        # Direction (top/bottom)
        is_top = get_nested_value(nl_result_raw, 'is_top_query', default=False)
        is_bottom = get_nested_value(nl_result_raw, 'is_bottom_query', default=False)
        if is_top:
            entities['direction'] = 'top'
        elif is_bottom:
            entities['direction'] = 'bottom'
        else:
            entities['direction'] = None
        
        # Aggregation type (try to infer from generated code)
        generated_code = result.get('generated_code', '')
        if 'count(' in generated_code.lower():
            entities['aggregation_type'] = 'count'
        elif 'sum(' in generated_code.lower():
            entities['aggregation_type'] = 'sum'
        elif 'mean(' in generated_code.lower() or 'avg(' in generated_code.lower():
            entities['aggregation_type'] = 'avg'
        elif 'max(' in generated_code.lower():
            entities['aggregation_type'] = 'max'
        elif 'min(' in generated_code.lower():
            entities['aggregation_type'] = 'min'
        else:
            entities['aggregation_type'] = 'count'  # default
        
        # Limit (try to extract from code)
        import re
        limit_match = re.search(r'\.head\((\d+)\)', generated_code)
        if limit_match:
            entities['limit'] = int(limit_match.group(1))
        else:
            entities['limit'] = None
        
        # FALLBACK: If primary entities are empty, try to extract from query or generated code
        if not entities['primary_entity'] or not entities['metric']:
            self.logger.warning(f"[ENTITY_EXTRACT] Primary entities empty, attempting fallback extraction")
            
            # Try to extract from generated code (groupby statements)
            if not entities['primary_entity'] and generated_code:
                groupby_match = re.search(r"\.groupby\(['\"]([^'\"]+)['\"]\)", generated_code)
                if groupby_match:
                    fallback_entity = groupby_match.group(1)
                    entities['primary_entity'] = fallback_entity
                    entities['primary_entity_label'] = self._humanize_column_name(fallback_entity)
                    entities['group_by'] = [fallback_entity]
                    self.logger.info(f"[ENTITY_EXTRACT] Fallback: Extracted entity from code: '{fallback_entity}'")
            
            # Try to extract metric from aggregation in code
            if not entities['metric'] and generated_code:
                agg_match = re.search(r"\.agg\(\{['\"]([^'\"]+)['\"]:", generated_code)
                if agg_match:
                    fallback_metric = agg_match.group(1)
                    entities['metric'] = self._humanize_column_name(fallback_metric)
                    entities['metric_column'] = fallback_metric
                    self.logger.info(f"[ENTITY_EXTRACT] Fallback: Extracted metric from code: '{fallback_metric}'")
        
        # Final validation and logging
        self.logger.info(f"[ENTITY_EXTRACT] ✅ Final entities extracted:")
        self.logger.info(f"[ENTITY_EXTRACT]   - primary_entity: '{entities.get('primary_entity', '')}'")
        self.logger.info(f"[ENTITY_EXTRACT]   - primary_entity_label: '{entities.get('primary_entity_label', '')}'")
        self.logger.info(f"[ENTITY_EXTRACT]   - metric: '{entities.get('metric', '')}'")
        self.logger.info(f"[ENTITY_EXTRACT]   - operation: '{entities.get('operation', '')}'")
        self.logger.info(f"[ENTITY_EXTRACT]   - direction: '{entities.get('direction', '')}'")
        self.logger.info(f"[ENTITY_EXTRACT]   - limit: {entities.get('limit', 'None')}")
        
        if not entities.get('primary_entity') and not entities.get('metric'):
            self.logger.error(f"[ENTITY_EXTRACT] ❌ Critical: Both entity and metric are empty!")
        
        return entities
    
    def _humanize_column_name(self, column_name: str) -> str:
        """Convert column name to human-readable label"""
        if not column_name:
            return ''
        
        # Replace underscores with spaces
        humanized = column_name.replace('_', ' ')
        
        # Handle common patterns
        replacements = {
            'account country': 'countries',
            'country': 'countries',
            'product': 'products',
            'ticket': 'tickets',
            'case': 'cases',
            'customer': 'customers',
            'order': 'orders',
            'user': 'users',
            'agent': 'agents'
        }
        
        humanized_lower = humanized.lower()
        for pattern, replacement in replacements.items():
            if pattern in humanized_lower:
                return replacement
        
        return humanized
    
    def _extract_result_summary(self, result: Dict) -> Dict[str, Any]:
        """
        Extract summary of query results for storage
        
        Args:
            result: Full result dictionary
            
        Returns:
            Summary dict with top N rows and metadata
        """
        summary = {}
        
        result_data = result.get('result')
        if result_data is not None:
            # Handle DataFrame
            if hasattr(result_data, 'head'):
                # It's a DataFrame
                try:
                    top_rows = result_data.head(10).to_dict(orient='records')
                    summary['top_rows'] = top_rows
                    summary['total_rows'] = len(result_data)
                    summary['columns'] = list(result_data.columns)
                except:
                    summary['top_rows'] = []
            elif isinstance(result_data, dict):
                # Already a dict
                summary = result_data
            elif isinstance(result_data, list) and len(result_data) > 0:
                # List of records
                summary['top_rows'] = result_data[:10]
                summary['total_rows'] = len(result_data)
        
        return summary
    
    def _add_to_history_manual(self, conversation_state: Dict, query: str, enriched_query: str,
                               entities: Dict, result: Dict, result_summary: Dict, intent: str):
        """
        Manually add query to conversation history (fallback when ConversationMemory not available)
        
        Args:
            conversation_state: Conversation state dict
            query: Original query
            enriched_query: Enriched query (if follow-up)
            entities: Extracted entities
            result: Full result dict
            result_summary: Result summary
            intent: Detected intent
        """
        if 'history' not in conversation_state:
            conversation_state['history'] = []
        
        entry = {
            'query': query,
            'enriched_query': enriched_query if enriched_query != query else query,
            'timestamp': datetime.now().isoformat(),
            'entities': entities,
            'success': result.get('success', False),
            'result_summary': result_summary,
            'generated_code': result.get('generated_code', '')[:500] if result.get('generated_code') else None,
            'intent': intent
        }
        
        conversation_state['history'].append(entry)
        
        # Keep last 100 queries
        if len(conversation_state['history']) > 100:
            conversation_state['history'] = conversation_state['history'][-100:]

    def get_conversation_summary(self, conversation_state: Dict) -> str:
        """
        Generate a summary of the conversation for context

        Args:
            conversation_state: Current conversation state

        Returns:
            Human-readable conversation summary
        """
        if not conversation_state or not conversation_state.get('history'):
            return "No previous conversation history."

        history = conversation_state['history']
        summary_lines = ["Recent conversation:"]

        for i, entry in enumerate(history[-3:], 1):  # Last 3 queries
            query = entry['query']
            success = "✓" if entry.get('success') else "✗"
            summary_lines.append(f"{i}. {success} {query}")

        return "\n".join(summary_lines)

