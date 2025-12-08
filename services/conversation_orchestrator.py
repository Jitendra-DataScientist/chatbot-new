"""
Conversation Orchestrator
Manages multi-turn conversations for data exploration queries
Integrates with NL to Python workflow for conversation-aware query processing
"""

import logging
import time
from typing import Dict, List, Any, Optional
import polars as pl

from master_logger import setup_module_logger
from services.nlp_to_python.nl_to_python_schemas import UserDisambiguationRequired


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

        # Execution callbacks (set by data_exploration service)
        self.execute_pandas_fn = None
        self.apply_column_cleaning_fn = None

        self.logger.info("ConversationOrchestrator initialized")

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
                           conversation_state: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Process a conversational query with context awareness

        Args:
            query: User's natural language query
            df_data: DataFrame containing the data
            df_columns: List of column names
            selected_chart: Selected chart name (if any)
            chart_context: Chart-specific context
            conversation_state: Previous conversation state

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

            # The NL to Python generator already has conversation history built-in
            # It will automatically use _conversation_history when processing
            # We just need to pass the session_id through kwargs

            self.logger.info(f"[CONVERSATION] Using session_id: {session_id}")
            self.logger.info(f"[CONVERSATION] Delegating to NL to Python workflow")

            # Delegate to NL to Python generator with session context
            # The generator will use its internal conversation history
            # Note: execute_pandas_fn expects (query, df, intent_result, chart_context)
            try:
                result = self.execute_pandas_fn(
                    query=query,
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

            # Update conversation state with current query
            conversation_state['history'].append({
                'query': query,
                'timestamp': time.time(),
                'success': result.get('success', False)
            })

            # Keep only last 5 queries in history
            if len(conversation_state['history']) > 5:
                conversation_state['history'] = conversation_state['history'][-5:]

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

