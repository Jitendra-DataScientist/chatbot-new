"""
Clean LLM-Only Query Understanding Agent - COMPLETE VERSION
🆕 UPDATED: Complete schema update integration for chained queries
🆕 UPDATED: Dynamic column tracking with result propagation
🆕 UPDATED: Multi-source support with independent schema management
"""

import json
import logging
import time
import os
import yaml
import asyncio
import traceback
import re
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from datetime import datetime
import pandas as pd
from functools import lru_cache
from collections import defaultdict # 🆕 1. Add Imports

import torch
import torch.nn as nn
from tenacity import retry, stop_after_attempt, wait_random_exponential
from openai import OpenAI
from transformers import BertTokenizer, BertModel, BertPreTrainedModel, BertConfig

# LangGraph imports for workflow orchestration
from langgraph.graph import StateGraph, END
from typing import TypedDict, Annotated

# Import master logger for comprehensive logging
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from master_logger import get_master_logger, function_logger, setup_module_logger

# Import services for query processing
from services.llm_service import LLMService
from services.data_processor import TableauDataProcessor
from services.visualization_service import IntelligentVisualizationService
from services.fuzzy_column_matcher import FuzzyColumnMatcher
from services.multi_table_service import TableauMultiTableService
from services.insight_generator import TableauInsightGenerator
# 🆕 Import the specialist agent and its context manager
from services.NL_to_python import NLToPythonGenerator, DefaultContextManager
from services.enhanced_analysis_service import EnhancedAnalysisService

# Setup master logger for comprehensive debugging
master_logger = setup_module_logger('meta_agents.query_understanding_agent')
master_logger.info("QUERY UNDERSTANDING AGENT MODULE INITIALIZATION STARTED")

# --- Structured Data Classes ---

@dataclass
class QueryIntent:
    """Structured representation of a user query's intent and entities."""
    primary_intent: str
    confidence: float
    entities: Dict[str, List[str]] = field(default_factory=dict)
    requires_agents: List[str] = field(default_factory=list)
    chart_requirements: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class AgentResponse:
    """Standardized response from any agent in the system."""
    agent_id: str
    success: bool
    data: Optional[QueryIntent] = None
    message: str = ""
    confidence: float = 0.0
    execution_time: float = 0.0
    metadata: Dict = field(default_factory=dict)


# LangGraph State Schema for Query Processing
class QueryProcessingState(TypedDict):
    """State schema for LangGraph-based query processing workflow"""
    # Core input data
    query: str
    data_id: str  # FIX: Add data_id to TypedDict (used by shap_analysis)
    connection_key: str  # FIX: Add connection_key to TypedDict
    csv_data: Optional[pd.DataFrame]
    selected_chart: Optional[str]
    context: Optional[Dict[str, Any]]
    
    # 🆕 NEW: Session and Schema state
    session_id: str
    source_id: str
    
    # Intent classification results
    bert_classification: Optional[Dict[str, Any]]
    intent_result: Optional[QueryIntent]
    
    # Data processing intermediate results
    filtered_data: Optional[pd.DataFrame]
    chart_context: Optional[Dict[str, Any]]
    query_columns: Optional[List[str]]
    
    # Analysis results from different services
    analysis_result: Optional[Dict[str, Any]]
    computational_results: Optional[Dict[str, Any]]
    llm_enhanced_analysis: Optional[str]
    nl_result: Optional[Dict[str, Any]]
    
    # Visualization and final output
    visualization: Optional[Dict[str, Any]]
    final_response: Optional[Dict[str, Any]]
    
    # Flow control and routing
    routing_decision: str
    service_route: Optional[str]
    needs_fallback: bool
    processing_complete: bool
    
    # Metadata and error handling
    execution_metadata: Dict[str, Any]
    error_context: Optional[Dict[str, Any]]


# 🆕 2. Add SessionContextManager Class
class SessionContextManager:
    """
    Manages per-user session context at dashboard/data-source level
    - 5-query history per source
    - Disambiguation cache per source
    - Followup detection
    - 🆕 Schema change tracking
    """

    def __init__(self, redis_client=None):
        self.redis = redis_client
        self.fallback_memory = {}  # Dev fallback
        self.logger = setup_module_logger('meta_agents.session_context_manager')

    def load_session(self, session_id: str) -> dict:
        """Load session context from storage"""
        if self.redis:
            data = self.redis.get(f"session:{session_id}")
            return json.loads(data) if data else self._new_session()
        else:
            return self.fallback_memory.get(session_id, self._new_session())

    def save_session(self, session_id: str, context: dict):
        """Save session with 1-hour TTL"""
        if self.redis:
            self.redis.setex(f"session:{session_id}", 3600, json.dumps(context))
        else:
            self.fallback_memory[session_id] = context

    def add_query_to_history(
        self, 
        session_id: str, 
        query: str, 
        source_id: str,
        resolved_entities: dict
    ):
        """Add query to source-specific history (last 5)"""
        context = self.load_session(session_id)
        
        if source_id not in context['source_contexts']:
            context['source_contexts'][source_id] = self._new_source_context()
        
        source_ctx = context['source_contexts'][source_id]
        source_ctx['query_history'].append({
            'query': query,
            'resolved_entities': resolved_entities,
            'timestamp': datetime.now().isoformat()
        })
        
        # Keep only last 5
        source_ctx['query_history'] = source_ctx['query_history'][-5:]
        
        self.save_session(session_id, context)

    def update_disambiguation_cache(
        self, 
        session_id: str, 
        source_id: str,
        column: str,
        term: str, 
        resolved_value: str
    ):
        """Store user's disambiguation choice for source"""
        context = self.load_session(session_id)
        
        if source_id not in context['source_contexts']:
            context['source_contexts'][source_id] = self._new_source_context()
        
        key = self.get_disambiguation_cache_key(column, term)
        context['source_contexts'][source_id]['disambiguation_cache'][key] = resolved_value
        
        self.logger.info(f"[CACHE] Source '{source_id}': {key} → {resolved_value}")
        self.save_session(session_id, context)

    def get_cached_disambiguation(
        self, 
        session_id: str, 
        source_id: str,
        column: str,
        term: str
    ) -> Optional[str]:
        """Check if user already resolved this ambiguity for source"""
        context = self.load_session(session_id)
        
        if source_id not in context['source_contexts']:
            return None
        
        key = self.get_disambiguation_cache_key(column, term)
        return context['source_contexts'][source_id]['disambiguation_cache'].get(key)

    def update_schema(
        self,
        session_id: str,
        source_id: str,
        new_columns: List[str],
        operation_type: str
    ):
        """🆕 Track schema changes (computed columns)"""
        context = self.load_session(session_id)
        
        if source_id not in context['source_contexts']:
            context['source_contexts'][source_id] = self._new_source_context()
            
        source_ctx = context['source_contexts'][source_id]
        source_ctx['schema_updates'].append({
            'columns': new_columns,
            'operation': operation_type,
            'timestamp': datetime.now().isoformat()
        })
        
        self.logger.info(f"[SCHEMA] Source '{source_id}': Added {len(new_columns)} computed columns from {operation_type}")
        self.save_session(session_id, context)

    def get_computed_columns(self, session_id: str, source_id: str) -> List[str]:
        """🆕 Get all computed columns for a source"""
        context = self.load_session(session_id)
        
        if source_id not in context['source_contexts']:
            return []
        
        all_columns = []
        for update in context['source_contexts'][source_id].get('schema_updates', []):
            all_columns.extend(update['columns'])
        
        return list(set(all_columns))  # Deduplicate

    def get_disambiguation_cache_key(self, column: str, term: str, operation_type: str = None) -> str:
        """
        🆕 Generate consistent cache keys for disambiguation
        
        Args:
            column: Column name
            term: Filter term/value
            operation_type: Optional operation context
        Returns:
            Cache key string
        """
        if operation_type:
            return f"{column}:{term.lower()}:{operation_type}"
        return f"{column}:{term.lower()}"

    def _new_session(self) -> dict:
        return {
            'source_contexts': {},
            'current_source': None
        }
        
    def _new_source_context(self) -> dict:
        return {
            'query_history': [],
            'disambiguation_cache': {},
            'schema_updates': []
        }

# Define the custom multi-task classification model
class BertForMultiTaskClassification(BertPreTrainedModel):
    def __init__(self, config, num_intent_labels=3, num_subcategory_labels=19):
        super().__init__(config)
        self.bert = BertModel(config)
        classifier_dropout = config.hidden_dropout_prob
        self.dropout = nn.Dropout(classifier_dropout)
        self.intent_classifier = nn.Linear(config.hidden_size, num_intent_labels)
        self.subcategory_classifier = nn.Linear(config.hidden_size, num_subcategory_labels)
        self.post_init()
    
    def forward(self, input_ids=None, attention_mask=None, token_type_ids=None, **kwargs):
        outputs = self.bert(input_ids, attention_mask=attention_mask, token_type_ids=token_type_ids)
        pooled_output = self.dropout(outputs[1])
        intent_logits = self.intent_classifier(pooled_output)
        subcategory_logits = self.subcategory_classifier(pooled_output)
        return {'intent_logits': intent_logits, 'subcategory_logits': subcategory_logits}


class QueryAgent:
    """
    Clean, reliable query understanding using GPT-4o-mini only.
    Eliminates rule-based complexity that doesn't add value.
    """
    
    def __init__(self, llm_client: OpenAI, config: Dict, logger: logging.Logger):
        master_logger.info("=== INITIALIZING QUERY AGENT ===")
        
        self.agent_id = "query_agent"
        self.llm_client = llm_client
        self.config = config
        self.logger = logger
        
        master_logger.info(f"Agent ID: {self.agent_id}")
        master_logger.debug(f"Config keys: {list(config.keys())}")
        
        # Simplified config access
        self.llm_model_name = config['agent']['llm_model_name']
        self.llm_temperature = config['agent']['llm_temperature']
        self.agent_requirements = config['agent']['agent_requirements']
        
        master_logger.info(f"LLM model: {self.llm_model_name}")
        master_logger.info(f"LLM temperature: {self.llm_temperature}")
        master_logger.debug(f"Agent requirements: {list(self.agent_requirements.keys())}")
        
        # Valid intents from config
        self.valid_intents = list(self.agent_requirements.keys())
        master_logger.info(f"Valid intents: {self.valid_intents}")
        
        # Initialize multi-task BERT intent and subcategory classifier
        master_logger.info("Loading multi-task BERT intent and subcategory classifier...")
        local_dir = "./intent-classifier-new"
        self.tokenizer = BertTokenizer.from_pretrained(local_dir)
        self.model = BertForMultiTaskClassification.from_pretrained(
            local_dir,
            config=BertConfig.from_pretrained(local_dir),
            num_intent_labels=3,
            num_subcategory_labels=19
        )
        self.model.eval()
        
        # Load label mappings
        with open(f"{local_dir}/label_mappings.json", 'r') as f:
            label_mappings = json.load(f)
        self.intent_labels = {int(k): v for k, v in label_mappings['intent']['id2label'].items()}
        self.subcategory_labels = {int(k): v for k, v in label_mappings['subcategory']['id2label'].items()}
        master_logger.info(f"✓ Multi-task BERT Classifier loaded from {local_dir}")
        master_logger.info(f"✓ Intent labels: {self.intent_labels}")
        master_logger.info(f"✓ Subcategory labels: {self.subcategory_labels}")
        
        # Performance metrics
        self.metrics = {
            'total_queries': 0,
            'successful_classifications': 0,
            'llm_errors': 0,
            'processing_times': [],
            'intent_distribution': {},
            'average_confidence': 0.0
        }
        
        # Initialize services for query processing
        self.llm_service = LLMService(openai_client=llm_client)
        self.data_processor = TableauDataProcessor()
        self.viz_service = IntelligentVisualizationService()
        self.multi_table_service = TableauMultiTableService()
        self.insight_generator = TableauInsightGenerator()
        self.nl_to_python = NLToPythonGenerator(openai_client=llm_client)
        self.enhanced_analysis = EnhancedAnalysisService(self.data_processor, self.nl_to_python)
        self.fuzzy_matcher = FuzzyColumnMatcher(threshold=70)  # Lowered to 70% for better misspelling tolerance
        self.smart_aggregation_decider = None  # Will be set via set_smart_aggregation()
        
        # 🆕 3. Update QueryAgent.__init__
        self.session_manager = SessionContextManager()
        self.source_schemas = {}  # source_id → {'base_columns': [...], 'all_columns': [...], 'last_result': DataFrame}
        master_logger.info("✅ Session context manager initialized")
        master_logger.info("✅ Schema tracking initialized")
        
        master_logger.info("QueryAgent initialized successfully with services")
        master_logger.debug(f"Initial metrics: {self.metrics}")
        
        # Build LangGraph for query processing workflow
        self._build_langgraph()
        master_logger.info("QueryAgent initialized successfully with LangGraph")
    
    def set_smart_aggregation(self, smart_decider):
        """Set smart aggregation decider and propagate to all services that need it"""
        self.smart_aggregation_decider = smart_decider
        
        # Propagate to data processor (which will propagate to its NL_to_python instance)
        if hasattr(self.data_processor, 'set_smart_aggregation'):
            self.data_processor.set_smart_aggregation(smart_decider)
        
        # Propagate to our own NL_to_python instance
        if hasattr(self.nl_to_python, 'set_smart_aggregation'):
            self.nl_to_python.set_smart_aggregation(smart_decider)
        
        # Propagate to enhanced_analysis service if it has NL_to_python
        if hasattr(self.enhanced_analysis, 'nl_to_python') and hasattr(self.enhanced_analysis.nl_to_python, 'set_smart_aggregation'):
            self.enhanced_analysis.nl_to_python.set_smart_aggregation(smart_decider)
        
        master_logger.info("[SMART_AGGREGATION] Smart aggregation set on QueryAgent and propagated to all services")

    @function_logger('meta_agents.query_understanding_agent.QueryAgent.process')
    async def process(self, query_text: str, context: Dict = None) -> AgentResponse:
        """Main processing method - intent classification only (maintains backward compatibility)"""
        master_logger.info("=== PROCESSING QUERY (Intent Classification Only) ===")
        master_logger.info(f"Query: '{query_text}'")
        master_logger.debug(f"Context: {context}")
        
        start_time = time.time()
        self.metrics['total_queries'] += 1
        
        try:
            master_logger.info("Starting intent classification")
            # Use existing intent classification method (not LangGraph for this method)
            classification = await self._classify_intent(query_text, context)
            
            master_logger.debug(f"Classification result: {classification}")
            
            if classification['success']:
                master_logger.info("Classification successful")
                self.metrics['successful_classifications'] += 1
                
                master_logger.debug("Building response")
                response = self._build_response(query_text, classification, context, start_time)
                
                master_logger.debug("Updating metrics")
                self._update_metrics(classification['confidence'])
                
                master_logger.info(f"Query processed successfully - intent: {classification.get('primary_intent')}, confidence: {classification.get('confidence'):.2f}")
                return response
            else:
                error_msg = f"Classification failed: {classification.get('error')}"
                master_logger.error(error_msg)
                raise Exception(error_msg)
                
        except Exception as e:
            self.metrics['llm_errors'] += 1
            master_logger.error(f"Query processing failed: {type(e).__name__}: {str(e)}")
            master_logger.error(f"Full traceback: {traceback.format_exc()}")
            
            self.logger.error(f"Query processing failed: {e}", exc_info=True)
            
            execution_time = time.time() - start_time
            master_logger.error(f"Query processing failed after {execution_time:.3f} seconds")
            
            return AgentResponse(
                agent_id=self.agent_id,
                success=False,
                message=f"Failed to process query: {str(e)}",
                execution_time=execution_time
            )

    @retry(
        wait=wait_random_exponential(min=1, max=10),
        stop=stop_after_attempt(3)
    )
    async def _classify_intent(self, query: str, context: Dict = None) -> Dict[str, Any]:
        """Multi-task BERT-based classification for both intent and subcategory."""
        master_logger.info("=== CLASSIFYING INTENT WITH MULTI-TASK BERT ===")
        master_logger.info(f"Query for classification: '{query}'")
        master_logger.debug(f"Context for classification: {context}")
        
        try:
            start_time = time.time()
            
            # Tokenize query
            master_logger.info("Tokenizing query for multi-task model")
            inputs = self.tokenizer(query, padding='max_length', truncation=True, max_length=64, return_tensors='pt')
            
            # Run model inference
            master_logger.info("Running multi-task model inference")
            with torch.no_grad():
                outputs = self.model(**inputs)
                intent_logits = outputs['intent_logits']
                subcategory_logits = outputs['subcategory_logits']
                
                # Get predictions with confidence scores
                intent_probs = torch.softmax(intent_logits, dim=-1)
                subcategory_probs = torch.softmax(subcategory_logits, dim=-1)
                
                intent_pred = torch.argmax(intent_probs, dim=-1).item()
                subcategory_pred = torch.argmax(subcategory_probs, dim=-1).item()
                
                intent_confidence = intent_probs[0][intent_pred].item()
                subcategory_confidence = subcategory_probs[0][subcategory_pred].item()
            
            # Map predictions to labels
            predicted_intent = self.intent_labels[intent_pred]
            subcategory = self.subcategory_labels[subcategory_pred]
            
            master_logger.info(f"Multi-task model prediction: intent={predicted_intent} (conf={intent_confidence:.4f}), subcategory={subcategory} (conf={subcategory_confidence:.4f})")
            
            inference_time = (time.time() - start_time) * 1000  # ms
            master_logger.info(f"Classification completed in {inference_time:.1f}ms")
            
            # Map BERT intent to system intent if needed
            # The reference code uses: exploration, analysis, prediction
            # We need to map these to the valid_intents in our system
            intent_mapping = {
                'exploration': 'data_exploration',
                'analysis': 'shap_analysis',  # Default analysis to shap_analysis
                'prediction': 'prediction'
            }
            
            # Check if we need to map the intent based on subcategory
            if predicted_intent == 'analysis':
                if subcategory == 'causal':
                    mapped_intent = 'shap_analysis'
                elif subcategory == 'anomaly':
                    mapped_intent = 'anomaly_detection'
                elif subcategory == 'trend':
                    mapped_intent = 'trend_analysis'
                elif subcategory == 'correlation':
                    mapped_intent = 'statistical_significance'
                else:
                    mapped_intent = 'shap_analysis'
            elif predicted_intent == 'exploration':
                if subcategory == 'ranking':
                    mapped_intent = 'top_bottom_analysis'
                elif subcategory == 'comparison':
                    mapped_intent = 'comparison'
                else:
                    mapped_intent = 'data_exploration'
            elif predicted_intent == 'prediction':
                mapped_intent = 'prediction'
            else:
                # If intent is already in valid_intents, use it directly
                mapped_intent = predicted_intent if predicted_intent in self.valid_intents else 'data_exploration'
            
            master_logger.info(f"Intent mapping: {predicted_intent} → {mapped_intent}")
            
            # Validate mapped intent
            if mapped_intent not in self.valid_intents:
                master_logger.warning(f"Mapped intent {mapped_intent} not in valid_intents, defaulting to data_exploration")
                mapped_intent = 'data_exploration'
            
            # Build result in expected format
            result = {
                'primary_intent': mapped_intent,
                'confidence': intent_confidence,
                'reasoning': f"Multi-task BERT classified as '{predicted_intent}' (conf={intent_confidence:.4f}) with sub-category '{subcategory}' (conf={subcategory_confidence:.4f}). Mapped to '{mapped_intent}'.",
                'entities': {
                    'original_intent': predicted_intent,
                    'sub_category': subcategory,
                    'subcategory_confidence': subcategory_confidence,
                    'metrics': [],  # Could extract these with NER if needed
                    'dimensions': []
                },
                'success': True,
                'inference_time_ms': inference_time
            }
            
            master_logger.info(f"Final classification result: {result}")
            return result
            
        except Exception as e:
            master_logger.error(f"Multi-task BERT classification failed: {type(e).__name__}: {str(e)}")
            master_logger.error(f"Full traceback: {traceback.format_exc()}")
            raise

    def _build_response(self, query_text: str, classification: Dict, context: Dict, start_time: float) -> AgentResponse:
        """Build structured response from classification."""
        master_logger.info("Building response from classification")
        
        primary_intent = classification['primary_intent']
        confidence = classification['confidence']
        
        master_logger.debug(f"Primary intent: {primary_intent}, confidence: {confidence}")
        
        # Update intent distribution
        if primary_intent not in self.metrics['intent_distribution']:
            self.metrics['intent_distribution'][primary_intent] = 0
        self.metrics['intent_distribution'][primary_intent] += 1
        
        master_logger.debug(f"Intent distribution updated: {self.metrics['intent_distribution']}")
        
        # Get required agents
        required_agents = self.agent_requirements.get(primary_intent, ['visualization'])
        master_logger.debug(f"Required agents for {primary_intent}: {required_agents}")
        
        # Build query intent object
        master_logger.debug("Building QueryIntent object")
        query_intent = QueryIntent(
            primary_intent=primary_intent,
            confidence=confidence,
            entities=classification.get('entities', {}),
            requires_agents=required_agents,
            chart_requirements=self._get_chart_requirements(primary_intent),
            metadata={
                'original_query': query_text,
                'reasoning': classification.get('reasoning', ''),
                'context': context or {},
                'processing_timestamp': datetime.now().isoformat()
            }
        )

        execution_time = time.time() - start_time
        self.metrics['processing_times'].append(execution_time)
        
        master_logger.info(f"Response built successfully - execution time: {execution_time:.3f}s")
        master_logger.debug(f"Processing times count: {len(self.metrics['processing_times'])}")
        
        response = AgentResponse(
            agent_id=self.agent_id,
            success=True,
            data=query_intent,
            confidence=confidence,
            execution_time=execution_time,
            message=f"Classified as: {primary_intent}",
            metadata={'method': 'llm_only'}
        )
        
        master_logger.debug(f"AgentResponse created: {response}")
        return response
    
    def _get_chart_requirements(self, intent: str) -> Dict[str, Any]:
        """Define data requirements based on intent."""
        base_requirements = {'requires_chart_selection': True}
        
        requirements_map = {
            'shap_analysis': {
                'minimum_numeric_columns': 2,
                'minimum_rows': 10
            },
            'statistical_significance': {
                'minimum_numeric_columns': 2,
                'minimum_rows': 5
            },
            'anomaly_detection': {
                'minimum_numeric_columns': 1,
                'minimum_rows': 20
            },
            'trend_analysis': {
                'requires_time_column': True,
                'minimum_rows': 10
            },
            'comparison': {
                'minimum_groups': 2,
                'minimum_rows': 5
            }
        }
        
        specific_reqs = requirements_map.get(intent, {})
        return {**base_requirements, **specific_reqs}
    
    def _update_metrics(self, confidence: float):
        """Update running metrics."""
        total = self.metrics['total_queries']
        current_avg = self.metrics['average_confidence']
        self.metrics['average_confidence'] = ((current_avg * (total - 1)) + confidence) / total
    
    def get_agent_status(self) -> Dict[str, Any]:
        """Get performance statistics."""
        total_time = sum(self.metrics['processing_times'])
        num_queries = len(self.metrics['processing_times'])
        avg_time = (total_time / num_queries) * 1000 if num_queries > 0 else 0
        
        return {
            'agent_id': self.agent_id,
            'total_queries': self.metrics['total_queries'],
            'success_rate': round(self.metrics['successful_classifications'] / max(self.metrics['total_queries'], 1), 3),
            'average_confidence': round(self.metrics['average_confidence'], 3),
            'average_processing_time_ms': round(avg_time, 2),
            'error_rate': round(self.metrics['llm_errors'] / max(self.metrics['total_queries'], 1), 3),
            'intent_distribution': self.metrics['intent_distribution']
        }

    # 🆕 4. Update process_with_services Signature
    async def process_with_services(
        self, 
        query: str, 
        csv_data, 
        selected_chart: str = None, 
        source_id: str = "default",  # 🆕 NEW
        session_id: str = None,      # 🆕 NEW
        context: Dict = None
    ) -> Dict[str, Any]:
        # 🔧 FIX: Handle ChatContext object
        if source_id and hasattr(source_id, '__dict__'):  # If it's an object, not a string
            # Extract the actual source_id from the object
            if hasattr(source_id, 'source_id'):
                source_id = source_id.source_id
            elif hasattr(source_id, 'id'):
                source_id = source_id.id
        else:
            source_id = "default"  # Only use default if object has no id
    
        # Ensure source_id is a string (handles None and other edge cases)
        source_id = str(source_id) if source_id else "default"
    
        """
        Process query with full service integration using LangGraph workflow
        🆕 NOW SUPPORTS: Session context and schema tracking
        """
        
        # 🆕 Generate session_id if not provided
        if not session_id:
            session_id = f"session_{int(datetime.now().timestamp())}"
            
        # 🆕 Initialize schema for this source if first time
        if source_id not in self.source_schemas:
            self.source_schemas[source_id] = {
                'base_columns': list(csv_data.columns),
                'all_columns': list(csv_data.columns),
                'last_result': None
            }
            master_logger.info(f"[SCHEMA_INIT] Source '{source_id}': {len(csv_data.columns)} base columns")
        
        # 🆕 Load computed columns from previous queries
        computed_columns = self.session_manager.get_computed_columns(session_id, source_id)
        if computed_columns:
            all_columns = list(set(self.source_schemas[source_id]['base_columns'] + computed_columns))
            self.source_schemas[source_id]['all_columns'] = all_columns
            master_logger.info(f"[SCHEMA_ENHANCED] Source '{source_id}': {len(computed_columns)} computed columns loaded")
            
        # 🆕 Update csv_data to last_result if it exists (it has the computed columns)
        if self.source_schemas[source_id]['last_result'] is not None:
            csv_data = self.source_schemas[source_id]['last_result']
            master_logger.info(f"[SCHEMA_ENHANCED] Using enhanced data from last result (Shape: {csv_data.shape})")

        # Register data with DataManager EARLY (so all code paths can use data_id)
        from services.data_manager import DataManager
        data_manager = DataManager()
        connection_key = source_id or "default"
        data_id = data_manager.register_data(csv_data, connection_key)
        master_logger.info(f"[DATA_MANAGER] Data registered: {data_id} (shape: {csv_data.shape if csv_data is not None else 'None'})")
        
        # Track current query for visualization check
        self._current_query = query
        
        master_logger.info("=" * 80)
        master_logger.info("=== FALLBACK: PROCESSING QUERY WITH LANGGRAPH ===")
        master_logger.info(f"Query: '{query}'")
        master_logger.info(f"Selected chart: {selected_chart}")
        master_logger.info(f"CSV data available: {csv_data is not None}")
        master_logger.info(f"Context provided: {context is not None}")
        
        if csv_data is not None:
            master_logger.info(f"CSV data shape: {csv_data.shape if hasattr(csv_data, 'shape') else 'not a DataFrame'}")
            master_logger.info(f"CSV columns (now includes computed): {list(csv_data.columns) if hasattr(csv_data, 'columns') else 'N/A'}")

        
        try:
            ## Jitendra: this is the intent classification step for routing to analysis or exloration service
            # ========== EARLY ROUTING - ALWAYS CHECK FOR ANALYSIS INTENT ==========
            # Always classify intent first to detect analysis queries
            master_logger.info("="*80)
            master_logger.info("🔀 CLASSIFYING INTENT")
            master_logger.info(f"   Query: {query}")
            master_logger.info(f"   Selected chart: {selected_chart}")
            master_logger.info("="*80)
            
            # Do intent classification
            classification = await self._classify_intent(query, context)
            original_intent = classification.get('entities', {}).get('original_intent', '')
            primary_intent = classification.get('primary_intent', '')
            
            master_logger.info(f"Intent classified: original={original_intent}, primary={primary_intent}")
            
            # If intent is 'analysis' AND no chart is in THIS request, require chart selection
            # Note: selected_chart here comes from the current request, not from session
            if original_intent == 'analysis' and not selected_chart:
                master_logger.info("🔀 ANALYSIS INTENT DETECTED - Chart selection required")
                master_logger.info("="*80)
                
                # Return immediately with chart selection requirement
                # This will be caught by app.py and trigger the dropdown
                minimal_intent = QueryIntent(
                    primary_intent=primary_intent,
                    confidence=classification.get('confidence', 0.8),
                    entities=classification.get('entities', {})
                )
                
                return {
                    "success": True,
                    "reply": "I can analyze that for you! Please select a chart to analyze.",
                    "intent": minimal_intent,
                    "requires_chart_selection": True,
                    "execution_time": 0,
                    "routed_to": "requires_chart_selection"
                }
            
            # If no chart is selected and not an analysis query, continue with exploration
            if selected_chart is None:
                
                # If intent is 'exploration', route to data_exploration_no_chart
                master_logger.info("🔀 ROUTING TO DATA_EXPLORATION_NO_CHART (exploration intent)")
                master_logger.info("="*80)
                
                try:
                    from services.data_exploration_no_chart import data_exploration
                    
                    import app
                    smart_agg_decider = getattr(app, 'smart_agg_decider', None)
                    
                    if not smart_agg_decider:
                        master_logger.warning("smart_agg_decider not available, proceeding without it")
                    
                    exploration_service = data_exploration(
                        llm_client=self.llm_client,
                        smart_agg_decider=smart_agg_decider,
                        cache_path="causal_analysis_cache.json"
                    )
                    
                    # Create a minimal intent_result for the service (required parameter)
                    minimal_intent = QueryIntent(
                        primary_intent='data_exploration',
                        confidence=1.0,
                        entities={'query': query}
                    )
                    
                    # Get DataFrame from DataManager (already registered at line 717-722)
                    csv_data_from_manager = data_manager.get_data(data_id)
                    
                    result = await exploration_service.process(
                        query_text=query,
                        csv_data=csv_data_from_manager,
                        selected_chart=None,
                        intent_result=minimal_intent,
                        chart_context=None
                    )
                    
                    master_logger.info("✅ data_exploration_no_chart service completed")
                    master_logger.info("="*80)
                    
                    return {
                        "success": result.get('success', False),
                        "reply": result.get('response', 'Analysis completed'),
                        "intent": minimal_intent,
                        "analysis_result": result.get('computational_results', {}),
                        "visualization": {
                            'needs_visualization': result.get('needs_visualization', False),
                            'chart_type': result.get('chart_type'),
                            'chart_image': result.get('chart_image')
                        },
                        "execution_time": result.get('execution_time', 0),
                        "routed_to": "services.data_exploration_no_chart.data_exploration"
                    }
                    
                except Exception as e:
                    master_logger.error(f"❌ Error in data_exploration_no_chart service: {e}", exc_info=True)
                    master_logger.info("⚠️  Falling back to LangGraph workflow...")
            
            # ========== FULL INTENT CLASSIFICATION (for chart-based routing) ==========## Jitendra
            # Step 1: Get full intent using existing process method
            master_logger.info("="*80)
            master_logger.info("STEP 1: Full intent classification for chart-based routing")
            master_logger.info("="*80)
            intent_response = await self.process(query, context)
            
            if not intent_response.success:
                master_logger.error(f"Intent classification failed: {intent_response.message}")
                master_logger.info("Falling back to LangGraph workflow")
                # Fall through to LangGraph (will reach line 937)
            else:
                intent_result = intent_response.data
                master_logger.info(f"Intent determined: {intent_result.primary_intent} (confidence: {intent_result.confidence})")
                
                # Update original_intent from full classification
                original_intent = intent_result.entities.get('original_intent', original_intent)

            
            # ========== ROUTING TO SHAP_ANALYSIS SERVICE (if analysis with chart) ==========
            # If this is an analysis query WITH chart, route to shap_analysis service
            if original_intent == 'analysis' and selected_chart:
                master_logger.info("="*80)
                master_logger.info("🔀 ROUTING TO SHAP_ANALYSIS SERVICE")
                master_logger.info(f"   Original Intent: {original_intent}")
                master_logger.info(f"   Primary Intent: {primary_intent}")
                master_logger.info(f"   Chart: {selected_chart}")
                master_logger.info("="*80)
                
                try:
                    # Import and initialize the shap_analysis service
                    from services.shap_analysis_v6 import shap_analysis
                    
                    # Get global smart_agg_decider from app context
                    import app
                    smart_agg_decider = getattr(app, 'smart_agg_decider', None)
                    
                    if not smart_agg_decider:
                        master_logger.warning("smart_agg_decider not available, proceeding without it")
                    
                    # Get intent_result properly
                    intent_response = await self.process(query, context)
                    if intent_response.success:
                        intent_result = intent_response.data
                    else:
                        # Fallback intent
                        intent_result = QueryIntent(
                            primary_intent=primary_intent,
                            confidence=classification.get('confidence', 0.8),
                            entities=classification.get('entities', {})
                        )
                    
                    # Initialize the analysis service
                    analysis_service = shap_analysis(
                        llm_client=self.llm_client, 
                        smart_agg_decider=smart_agg_decider,
                        causal_cache_path="causal_analysis_cache.json"
                    )
                    
                    # Get workbook_id and csv_file_path from context (passed from app.py)
                    workbook_id = None
                    csv_file_path = None
                    if context:
                        # DEBUG: Log what context actually is
                        master_logger.info(f"[SHAP_ANALYSIS] Context type: {type(context)}")
                        master_logger.info(f"[SHAP_ANALYSIS] Context value: {context}")
                        
                        # Handle both dict and ChatContext object
                        if isinstance(context, dict):
                            workbook_id = context.get('workbook_id')
                            csv_file_path = context.get('csv_file_path')
                            master_logger.info(f"[SHAP_ANALYSIS] Context is dict, extracted csv_file_path: {csv_file_path}")
                        else:
                            workbook_id = getattr(context, 'workbook_id', None)
                            csv_file_path = getattr(context, 'csv_file_path', None)
                            master_logger.info(f"[SHAP_ANALYSIS] Context is object, extracted csv_file_path: {csv_file_path}")
                        master_logger.info(f"[SHAP_ANALYSIS] Retrieved workbook_id from context: {workbook_id}")
                        master_logger.info(f"[SHAP_ANALYSIS] Retrieved csv_file_path from context: {csv_file_path}")
                    else:
                        master_logger.warning(f"[SHAP_ANALYSIS] No context provided, workbook_id and csv_file_path will be None")
                    
                    # Process the query with DataManager reference pattern
                    result = analysis_service.process(
                        query_text=query,
                        data_id=data_id,  # Pass data_id instead of csv_data
                        connection_key=source_id or "default",
                        selected_chart=selected_chart,
                        intent_result=intent_result,  # Pass the intent_result
                        chart_context={"workbook_id": workbook_id, "csv_file_path": csv_file_path}  # Pass workbook_id and csv_file_path for cache
                    )
                    
                    # Return the result directly
                    master_logger.info("✅ shap_analysis service completed successfully")
                    master_logger.info("="*80)
                    
                    # Extract seven_layer_analysis if present
                    seven_layer = result.get('seven_layer_analysis')
                    if seven_layer:
                        master_logger.info("✅ 7-layer analysis found in result, adding to response")
                    
                    response_dict = {
                        "success": result.get('success', False),
                        "reply": result.get('response', 'Analysis completed'),
                        "intent": intent_result,
                        "analysis_result": result.get('computational_results', {}),
                        "shap_visualizations": result.get('shap_visualizations', []),
                        "visualization": {
                            'needs_visualization': result.get('needs_visualization', False),
                            'chart_type': result.get('chart_type'),
                            'chart_image': result.get('chart_image')
                        },
                        "execution_time": result.get('execution_time', 0),
                        "routed_to": "services.shap_analysis.shap_analysis"
                    }
                    
                    # Add seven_layer_analysis if present
                    if seven_layer:
                        response_dict["seven_layer_analysis"] = seven_layer
                    
                    # 🆕 Update schema if analysis produced new columns (Ashish's feature)
                    try:
                        self._update_schema_from_result({"analysis_result": result}, session_id, source_id)
                    except Exception as schema_err:
                        master_logger.warning(f"Schema update failed: {schema_err}")
                    
                    # 🆕 Save query to history (Ashish's feature)
                    try:
                        self.session_manager.add_query_to_history(
                            session_id,
                            query,
                            source_id,
                            resolved_entities={'intent': intent_result.primary_intent}
                        )
                    except Exception as history_err:
                        master_logger.warning(f"History update failed: {history_err}")
                    
                    return response_dict
                    
                except Exception as e:
                    master_logger.error(f"❌ Error in shap_analysis service: {e}", exc_info=True)
                    master_logger.info("⚠️  Falling back to LangGraph workflow...")
                    # Fall through to LangGraph if there's an error
            
            # ========== ROUTING TO DATA_EXPLORATION SERVICE ==========
            # If original_intent is exploration WITH chart, route to data_exploration service
            if 'intent_result' in locals() and intent_result is not None:
                current_original_intent = intent_result.entities.get('original_intent', original_intent)
                
                if current_original_intent == 'exploration' and selected_chart:
                    master_logger.info("="*80)
                    master_logger.info("🔀 ROUTING TO DATA_EXPLORATION SERVICE (with chart)")
                    master_logger.info(f"   Original Intent: {current_original_intent}")
                    master_logger.info(f"   Primary Intent: {intent_result.primary_intent}")
                    master_logger.info(f"   Chart: {selected_chart}")
                    master_logger.info("="*80)
                    
                    try:
                        from services.data_exploration import data_exploration
                        
                        import app
                        smart_agg_decider = getattr(app, 'smart_agg_decider', None)
                        
                        if not smart_agg_decider:
                            master_logger.warning("smart_agg_decider not available, proceeding without it")
                        
                        exploration_service = data_exploration(
                            llm_client=self.llm_client,
                            smart_agg_decider=smart_agg_decider,
                            cache_path="causal_analysis_cache.json"
                        )
                        
                        result = await exploration_service.process(
                            query_text=query,
                            csv_data=csv_data,
                            selected_chart=selected_chart,
                            intent_result=intent_result,
                            chart_context=None
                        )
                        
                        # 🆕 Update schema if exploration produced new columns (Ashish's feature)
                        try:
                            self._update_schema_from_result({"analysis_result": result}, session_id, source_id)
                        except Exception as schema_err:
                            master_logger.warning(f"Schema update failed: {schema_err}")
                        
                        # 🆕 Save query to history (Ashish's feature)
                        try:
                            self.session_manager.add_query_to_history(
                                session_id,
                                query,
                                source_id,
                                resolved_entities={'intent': intent_result.primary_intent}
                            )
                        except Exception as history_err:
                            master_logger.warning(f"History update failed: {history_err}")
                        
                        return {
                            "success": result.get('success', False),
                            "reply": result.get('response', 'Exploration completed'),
                            "intent": intent_result,
                            "analysis_result": result.get('computational_results', {}),
                            "visualization": {
                                'needs_visualization': result.get('needs_visualization', False),
                                'chart_type': result.get('chart_type'),
                                'chart_image': result.get('chart_image')
                            },
                            "execution_time": result.get('execution_time', 0),
                            "routed_to": "services.data_exploration.data_exploration"
                        }
                        
                    except Exception as e:
                        master_logger.error(f"❌ Error in data_exploration service: {e}", exc_info=True)
                        master_logger.info("⚠️  Falling back to LangGraph workflow...")
                        # Fall through to LangGraph if there's an error

        except Exception as intent_error:
            master_logger.error(f"Intent classification/routing failed: {intent_error}")
            master_logger.info("Falling back to LangGraph workflow")
            # Fall through to LangGraph
        
        # ========== FALLBACK TO LANGGRAPH WORKFLOW ==========

        try:
            # Check if LangGraph is available
            if not self._is_langgraph_available():
                master_logger.error("LangGraph not available, falling back to original implementation")
                return await self._process_with_services_fallback(query, csv_data, selected_chart, context)
            
            # Data already registered at line 717-722, use that data_id
            # (No duplicate registration needed)
            
            # Initialize state for LangGraph
            initial_state = {
                "query": query,
                "data_id": data_id,  # Reference to DataFrame (instant access, no JSON)
                "connection_key": connection_key,
                "selected_chart": selected_chart,
                "context": context,
                "bert_classification": None,
                "intent_result": None,
                "filtered_data": None,
                "chart_context": None,
                "query_columns": None,
                "analysis_result": None,
                "computational_results": None,
                "llm_enhanced_analysis": None,
                "nl_result": None,
                "visualization": None,
                "final_response": None,
                "routing_decision": "",
                "service_route": None,
                "needs_fallback": False,
                "processing_complete": False,
                "execution_metadata": {"start_time": time.time()},
                "error_context": None,
                "session_id": session_id,  # 🆕 NEW
                "source_id": source_id,    # 🆕 NEW
            }
            
            master_logger.info("Invoking LangGraph for query processing")
            
            # Execute the LangGraph workflow
            final_state = await self.query_graph.ainvoke(initial_state)
            
            master_logger.info("LangGraph execution completed")
            master_logger.info(f"Processing complete: {final_state.get('processing_complete')}")
            master_logger.info(f"Service used: {final_state.get('service_route')}")
            master_logger.info(f"Fallback needed: {final_state.get('needs_fallback')}")
            
            # 🆕 Update schema if analysis produced new columns
            if final_state.get("analysis_result"):
                self._update_schema_from_result(final_state, session_id, source_id)
            
            # 🆕 Save query to history
            if final_state.get("processing_complete"):
                self.session_manager.add_query_to_history(
                    session_id,
                    query,
                    source_id,
                    resolved_entities={'intent': final_state.get('intent_result', {}).primary_intent if final_state.get('intent_result') else 'unknown'}
                )

            # Return the final response
            final_response = final_state.get("final_response")
            if final_response:
                master_logger.info("=== LANGGRAPH QUERY PROCESSING COMPLETED SUCCESSFULLY ===")
                master_logger.info("=" * 80)
                return final_response
            else:
                # This shouldn't happen, but provide a fallback
                master_logger.error("LangGraph completed but no final_response generated")
                return {
                    "success": False,
                    "reply": "Processing completed but no response was generated",
                    "error": True,
                    "langgraph_state": "no_final_response"
                }
                
        except Exception as e:
            master_logger.error("=" * 80)
            master_logger.error("=== LANGGRAPH QUERY PROCESSING FAILED ===")
            master_logger.error(f"Error: {e}")
            master_logger.error(f"Error type: {type(e).__name__}")
            master_logger.error(f"Traceback: {traceback.format_exc()}")
            master_logger.error("=" * 80)
            
            # Try fallback to original implementation
            master_logger.info("Attempting fallback to original implementation")
            try:
                return await self._process_with_services_fallback(query, csv_data, selected_chart, context)
            except Exception as fallback_error:
                master_logger.error(f"Fallback also failed: {fallback_error}")
                return {
                    "success": False,
                    "reply": f"I encountered an error processing your query: {str(e)}. Fallback also failed: {str(fallback_error)}",
                    "error": True,
                    "langgraph_error": True,
                    "fallback_error": True
                }

    # 🆕 5. Add Schema Update Method
    def _update_schema_from_result(self, state: Dict, session_id: str, source_id: str):
        """🆕 Update schema tracking after operation produces new columns"""
        try:
            analysis_result = state.get("analysis_result", {})
            
            # Check computational_results for pandas execution
            comp_results = analysis_result.get("computational_results", {})
            pandas_exec = comp_results.get("pandas_execution", analysis_result.get("pandas_execution", {}))
            
            if not pandas_exec:
                return
            
            result_data = pandas_exec.get("result", {})
            if result_data.get("type") != "dataframe":
                return
            
            # Get result columns
            result_columns = result_data.get("columns", [])
            base_columns = set(self.source_schemas[source_id]['base_columns'])
            
            # Detect new computed columns
            new_columns = [col for col in result_columns if col not in base_columns]
            
            if new_columns:
                # Update local schema
                self.source_schemas[source_id]['all_columns'] = list(set(
                    self.source_schemas[source_id]['all_columns'] + new_columns
                ))
                
                # Reconstruct DataFrame for next query
                try:
                    import pandas as pd
                    result_df = pd.DataFrame(result_data['data'], index=result_data.get('index'))
                    result_df.columns = result_data.get('columns', result_df.columns)
                    self.source_schemas[source_id]['last_result'] = result_df
                except Exception as e:
                    master_logger.warning(f"Could not reconstruct DataFrame: {e}")
                
                # Update session manager
                operation_type = analysis_result.get("intent_type", "unknown")
                self.session_manager.update_schema(session_id, source_id, new_columns, operation_type)
                
                master_logger.info(f"[SCHEMA_UPDATE] ✅ Added {len(new_columns)} computed columns: {new_columns}")
        except Exception as e:
            master_logger.error(f"Schema update failed: {e}")

    async def _process_with_services_fallback(self, query: str, csv_data, selected_chart: str = None, context: Dict = None) -> Dict[str, Any]:
        """Fallback implementation using original logic (without LangGraph)"""
        master_logger.info("=== FALLBACK: USING ORIGINAL PROCESS_WITH_SERVICES LOGIC ===")
        
        try:
            # Simplified fallback - just run intent classification + general analysis
            intent_response = await self.process(query, context)
            
            if not intent_response.success:
                return {
                    "success": False,
                    "reply": f"Failed to understand query: {intent_response.message}",
                    "error": True
                }
            
            intent_result = intent_response.data
            
            # Basic data processing
            analysis_data = csv_data
            chart_context = None
            if csv_data is not None and selected_chart:
                analysis_data, chart_context = self._apply_column_cleaning(csv_data, selected_chart, query)
            
            # Execute analysis
            analysis_result = await self._execute_intent_analysis(intent_result, query, analysis_data, chart_context)
            
            # Generate response
            response_text = await self._generate_enhanced_response(query, intent_result, analysis_result)
            
            return {
                "success": True,
                "reply": response_text,
                "intent": intent_result,
                "analysis_result": analysis_result,
                "chart_context": chart_context,
                "execution_time": intent_response.execution_time,
                "fallback_used": True
            }
            
        except Exception as e:
            master_logger.error(f"Fallback implementation failed: {e}")
            return {
                "success": False,
                "reply": f"I encountered an error processing your query: {str(e)}",
                "error": True
            }
    
    def _load_causal_cache(self, chart_name, workbook_id: Optional[str] = None):
        """Load causal analysis cache with comprehensive logging (nested: cache[workbook_id][chart_name])"""
        master_logger.info(f"Loading causal cache for chart: {chart_name}")
        
        try:
            import json
            import os
            
            cache_file = "causal_analysis_cache.json"
            
            if not os.path.exists(cache_file):
                master_logger.warning(f"Causal cache file not found: {cache_file}")
                return None
            
            with open(cache_file, 'r') as f:
                cache_data = json.load(f)
            
            # Nested lookup
            chart_cache = None
            if workbook_id and isinstance(cache_data, dict) and workbook_id in cache_data:
                if chart_name in cache_data[workbook_id]:
                    chart_cache = cache_data[workbook_id][chart_name]
            if chart_cache is None and isinstance(cache_data, dict):
                # Scan all workbooks for the chart
                for wb_id, charts in cache_data.items():
                    if isinstance(charts, dict) and chart_name in charts:
                        chart_cache = charts[chart_name]
                        break
            
            if chart_cache:
                master_logger.info(f"Cache found for chart '{chart_name}':")
                master_logger.info(f"  - Domain: {chart_cache.get('domain_type', 'Unknown')}")
                master_logger.info(f"  - Features: {chart_cache.get('top_5_features', [])}")
                master_logger.info(f"  - Timestamp: {chart_cache.get('timestamp', 'Unknown')}")
                master_logger.info(f"  - Feature details count: {len(chart_cache.get('feature_details', []))}")
                return chart_cache
            
            master_logger.warning(f"No cache entry found for chart: {chart_name}")
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
        # Pattern 1: "count of X", "sum of X", "total X", etc.
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
                if match and len(match) > 2:  # Skip very short terms
                    if match not in extracted_terms:
                        extracted_terms.append(match)
                        master_logger.info(f"[QUERY_COLUMN_EXTRACTION] Found via aggregation pattern: '{match}'")
        
        # Pattern 2: Multi-word terms with underscores (column-like naming)
        # Example: "total_outbound_emails", "inbound_email_count"
        underscore_terms = re.findall(r'\b(\w+_\w+(?:_\w+)*)\b', query_lower)
        for term in underscore_terms:
            if term not in extracted_terms and len(term) > 3:
                extracted_terms.append(term)
                master_logger.info(f"[QUERY_COLUMN_EXTRACTION] Found underscore term: '{term}'")
        
        # PHASE 2: NEW - Token-based extraction with comprehensive stopword filtering
        # This ensures we don't miss simple column names like "product", "type", "status"
        master_logger.info(f"[QUERY_COLUMN_EXTRACTION] Starting token-based extraction (NEW)")
        
        # Comprehensive stopword list to filter out query noise
        stopwords = {
            # Articles & Determiners
            'the', 'a', 'an', 'this', 'that', 'these', 'those',
            # Common verbs
            'give', 'show', 'find', 'get', 'list', 'display', 'tell', 'see',
            'have', 'has', 'had', 'been', 'will', 'would', 'could', 'should', 'can',
            # Prepositions
            'of', 'in', 'on', 'at', 'to', 'for', 'with', 'from', 'by',
            'about', 'across', 'against', 'along', 'around', 'before', 'behind',
            'between', 'into', 'through', 'during', 'over', 'under',
            # Conjunctions
            'and', 'or', 'but', 'nor', 'yet', 'so',
            # Pronouns
            'me', 'my', 'you', 'your', 'their', 'them', 'mine', 'yours', 'his', 'her', 'its',
            # Query words
            'what', 'where', 'when', 'which', 'who', 'whom', 'whose', 'why', 'how',
            # Quantifiers (noise for column extraction)
            'all', 'each', 'every', 'some', 'any', 'many', 'few', 'most', 'more', 'less',
            # Other common noise
            'there', 'here', 'wise', 'per', 'than', 'then', 'are', 'was', 'were', 'be',
            'not', 'out', 'up', 'down', 'off', 'only', 'just', 'like', 'such'
        }
        
        # Extract all tokens (3+ characters) from query
        all_tokens = re.findall(r'\b(\w{3,})\b', query_lower)
        
        # Filter out stopwords and already extracted terms
        meaningful_tokens = [
            token for token in all_tokens 
            if token not in stopwords and token not in extracted_terms
        ]
        
        # Add meaningful tokens to extracted terms
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
            # Get chart columns + top 5 features from causal cache
            chart_columns = [cache_data.get('x_axis_detected'), cache_data.get('y_axis_detected')]
            top_features = cache_data.get('top_5_features', [])
            relevant_columns = list(set([col for col in chart_columns + top_features if col]))
            
            master_logger.info(f"[COLUMN_CLEANING] Cached columns to find: {relevant_columns}")
            master_logger.info(f"[COLUMN_CLEANING] Available CSV columns ({len(csv_data.columns)}): {list(csv_data.columns)}")
            
            # 🆕 NEW: Extract query-mentioned columns and fuzzy match against original CSV
            query_matched_columns = []
            if query:
                master_logger.info("[COLUMN_CLEANING] 🆕 QUERY-BASED COLUMN ENHANCEMENT ACTIVATED")
                master_logger.info(f"[COLUMN_CLEANING] Query: '{query}'")
                
                # Extract potential column terms from query
                query_terms = self._extract_query_column_terms(query)
                
                if query_terms:
                    master_logger.info(f"[COLUMN_CLEANING] Fuzzy matching {len(query_terms)} query terms against original CSV...")
                    
                    for term in query_terms:
                        # Use fuzzy matcher to find best match in ORIGINAL CSV columns
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
                        # Merge query-matched columns with causal cache columns
                        relevant_columns = list(set(relevant_columns + query_matched_columns))
                        master_logger.info(f"[COLUMN_CLEANING] 📊 Enhanced column set: {len(relevant_columns)} columns (causal cache + query-matched)")
                    else:
                        master_logger.info("[COLUMN_CLEANING] No additional columns added from query (no matches above threshold)")
                else:
                    master_logger.info("[COLUMN_CLEANING] No potential column terms extracted from query")
            else:
                master_logger.info("[COLUMN_CLEANING] No query provided, skipping query-based column enhancement")
            
            # Filter CSV to relevant columns - use fuzzy matching for better matching
            available_columns = []
            csv_column_list = list(csv_data.columns)
            
            for cached_col in relevant_columns:
                if not cached_col:
                    continue
                
                # Try exact match first
                if cached_col in csv_column_list:
                    available_columns.append(cached_col)
                    master_logger.info(f"[COLUMN_CLEANING] ✓ Exact match: '{cached_col}'")
                else:
                    # Try fuzzy matching
                    fuzzy_match = self.fuzzy_matcher.find_best_match(
                        cached_col,
                        csv_column_list,
                        context=f"column_cleaning|{selected_chart}",
                        query_context=query
                    )
                    
                    if fuzzy_match:
                        available_columns.append(fuzzy_match)
                        master_logger.info(f"[COLUMN_CLEANING] ✓ Fuzzy matched cache column '{cached_col}' → CSV column '{fuzzy_match}'")
                        
                        # Log type inference
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
            
            # Basic data info
            basic_summary = f"""
DATASET OVERVIEW:
- Shape: {data.shape[0]} rows × {data.shape[1]} columns
- Columns: {list(data.columns)}
- Domain: {domain_type}

COLUMN ANALYSIS:"""

            # Add column-specific insights
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
            
            # Sample data
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
        
        # Extract feature reasoning for richer context
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

    async def _execute_llm_enhanced_analysis(self, intent_result, query: str, data, chart_context, nl_result):
        """Execute domain-aware LLM analysis with comprehensive context"""
        import asyncio
        import time
        import pandas as pd
        master_logger.info("=== STARTING LLM-ENHANCED ANALYSIS ===")
        master_logger.info(f"Query: '{query}'")
        master_logger.info(f"Intent: {intent_result.primary_intent}")
        master_logger.info(f"Data shape: {data.shape if hasattr(data, 'shape') else 'N/A'}")
        master_logger.info(f"Chart context available: {chart_context is not None}")
        
        if chart_context:
            master_logger.info(f"Domain type: {chart_context.get('domain_type', 'Unknown')}")
            master_logger.info(f"Chart name: {chart_context.get('chart_name', 'Unknown')}")
            master_logger.info(f"Top features: {chart_context.get('top_5_features', [])}")
        
        # Stage 1: Get computational results using enhanced analysis service
        computational_results = await self.enhanced_analysis.get_computational_results(query, data, chart_context)
        print ("\n\n\n----------------computational_results----------------\n\n\n")
        print (json.dumps(computational_results, indent=4))
        master_logger.info(f"Computational analysis completed. Success: {computational_results.get('success', False)}")
        
        # Prepare comprehensive context for LLM
        data_summary = self._prepare_data_summary(data, chart_context)
        nl_summary = self._prepare_nl_summary(nl_result)
        domain_type = chart_context.get('domain_type', 'General Business') if chart_context else 'General Business'
        
        # Stage 2: Prepare enhanced CSV text using computational results
        csv_text = self.enhanced_analysis.prepare_enhanced_csv_text(data, chart_context, computational_results)
        print ("\n\n\n----------------csv_text----------------\n\n\n")
        print (csv_text)
        master_logger.info(f"Enhanced CSV text prepared: {len(csv_text)} characters")

        # Stage 3: Build interpretation-focused prompt using computational results
        analysis_prompt = self.enhanced_analysis.build_interpretation_prompt(
            query, intent_result, chart_context, data_summary, computational_results)
        print ("\n\n\n----------------analysis_prompt----------------\n\n\n")
        print (analysis_prompt)
        print ("analysis_prompt printed")
        try:
            master_logger.info("Preparing LLM request with comprehensive context")
            master_logger.info(f"Prompt length: {len(analysis_prompt)} characters")
            
            messages = [
                {
                    "role": "system", 
                    "content": f"You are a senior {domain_type} analyst with expertise in data interpretation, business intelligence, and operational insights. Provide clear analysis that explains technical findings."
                },
                {"role": "user", "content": analysis_prompt}
            ]
            
            master_logger.info("Calling OpenAI API for enhanced analysis")
            start_time = time.time()
            
            # Use large context window for comprehensive analysis
            response = await asyncio.to_thread(
                self.llm_client.chat.completions.create,
                model="gpt-4o-mini",
                messages=messages,
                max_tokens=12000,  # Increased for detailed domain-specific analysis
                temperature=0.2   # Slightly creative but still analytical
            )
            
            llm_call_time = time.time() - start_time
            master_logger.info(f"LLM call completed successfully in {llm_call_time:.3f} seconds")
            
            # Log token usage
            if hasattr(response, 'usage') and response.usage:
                master_logger.info(f"Token usage - prompt: {response.usage.prompt_tokens}, completion: {response.usage.completion_tokens}, total: {response.usage.total_tokens}")
            
            llm_analysis = response.choices[0].message.content
            master_logger.info(f"LLM analysis generated: {len(llm_analysis)} characters")
            master_logger.info("=== LLM-ENHANCED ANALYSIS COMPLETED SUCCESSFULLY ===")
            print ("\n\n\n----------------return----------------\n\n\n")
            print ({
                "success": True,
                "llm_enhanced_analysis": llm_analysis,
                "nl_result": nl_result,
                "computational_results": computational_results,
                "chart_context": chart_context,
                "domain_type": domain_type,
                "data_shape": data.shape if hasattr(data, 'shape') else None,
                "processing_time": llm_call_time,
                "token_usage": response.usage.__dict__ if hasattr(response, 'usage') and response.usage else None
            })
            return {
                "success": True,
                "llm_enhanced_analysis": llm_analysis,
                "nl_result": nl_result,
                "computational_results": computational_results,
                "chart_context": chart_context,
                "domain_type": domain_type,
                "data_shape": data.shape if hasattr(data, 'shape') else None,
                "processing_time": llm_call_time,
                "token_usage": response.usage.__dict__ if hasattr(response, 'usage') and response.usage else None
            }
            
        except Exception as e:
            master_logger.error(f"LLM-enhanced analysis failed: {e}")
            master_logger.error(f"Error type: {type(e).__name__}")
            master_logger.info("Falling back to standard NL_to_python result")
            print ("\n\n\n----------------return----------------\n\n\n")
            print ({
                "success": False,
                "error": str(e),
                "fallback": nl_result,
                "computational_results": computational_results if 'computational_results' in locals() else None,
                "domain_type": domain_type
            })
            return {
                "success": False,
                "error": str(e),
                "fallback": nl_result,
                "computational_results": computational_results if 'computational_results' in locals() else None,
                "domain_type": domain_type
            }
    
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
            # Check if this is a chart-aware intent that can benefit from LLM enhancement
            chart_aware_intents = ["anomaly_detection", "trend_analysis", "shap_analysis"]
            
            if chart_context and intent_type in chart_aware_intents:
                master_logger.info(f"Using LLM-enhanced analysis for chart-aware intent: {intent_type}")
                
                # Get standard NL_to_python result first
                nl_result = self.data_processor.execute_pandas_aggregation(query, data)
                
                # Enhance with LLM analysis using chart context
                enhanced_result = await self._execute_llm_enhanced_analysis(
                    intent_result, query, data, chart_context, nl_result)
                
                return enhanced_result
                
            elif intent_type == "top_bottom_analysis":
                # Use data processor for ranking analysis
                pandas_result = self.data_processor.execute_pandas_aggregation(query, data)
                analysis_result["pandas_execution"] = pandas_result
                analysis_result["success"] = pandas_result.get('execution_status') == 'success'
                master_logger.info(f"[ANALYSIS] top_bottom_analysis execution status: {pandas_result.get('execution_status')}")
                
            elif intent_type == "comparison":
                # Use data processor for comparison
                pandas_result = self.data_processor.execute_pandas_aggregation(query, data)
                analysis_result["pandas_execution"] = pandas_result
                analysis_result["success"] = pandas_result.get('execution_status') == 'success'
                master_logger.info(f"[ANALYSIS] comparison execution status: {pandas_result.get('execution_status')}")
                
            elif intent_type in ["statistical_significance", "correlation"]:
                # Statistical analysis
                statistical_summary = self.data_processor.calculate_statistical_summary(data)
                analysis_result["statistical_analysis"] = statistical_summary
                # Statistical analysis success if no error key present
                analysis_result["success"] = 'error' not in statistical_summary
                master_logger.info(f"[ANALYSIS] statistical analysis success: {analysis_result['success']}")
                
            elif intent_type == "trend_analysis":
                # Trend analysis with aggregation
                pandas_result = self.data_processor.execute_pandas_aggregation(query, data)
                analysis_result["pandas_execution"] = pandas_result
                analysis_result["success"] = pandas_result.get('execution_status') == 'success'
                master_logger.info(f"[ANALYSIS] trend_analysis execution status: {pandas_result.get('execution_status')}")
                
            else:
                # General data exploration
                pandas_result = self.data_processor.execute_pandas_aggregation(query, data)
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
        
        # Skip visualization for AUTO_ANALYSIS requests (they only need text analysis)
        if hasattr(self, '_current_query'):
            query = str(self._current_query).lower()
            if 'auto-analysis' in query or 'auto_analysis' in query:
                return False
        
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
            
            # CHECK BOTH POSSIBLE STRUCTURES:
            # Structure 1: analysis_result['computational_results']['pandas_execution'] (LLM-enhanced path)
            # Structure 2: analysis_result['pandas_execution'] (simple analysis path)
            
            computational_results = analysis_result.get('computational_results', {})
            
            # Try nested structure first (LLM-enhanced analysis: trend_analysis, anomaly_detection, etc.)
            pandas_result = computational_results.get('pandas_execution', {})
            
            if pandas_result:
                master_logger.info(f"[VIZ DEBUG] Using nested pandas_execution from computational_results (LLM-enhanced path)")
            else:
                # If not found, try direct structure (simple analysis: top_bottom, comparison, etc.)
                pandas_result = analysis_result.get('pandas_execution', {})
                if pandas_result:
                    master_logger.info(f"[VIZ DEBUG] Using direct pandas_execution from analysis_result (simple analysis path)")
                else:
                    master_logger.info(f"[VIZ DEBUG] No pandas_execution found in either structure")
            
            master_logger.info(f"[VIZ DEBUG] computational_results exists: {bool(computational_results)}")
            master_logger.info(f"[VIZ DEBUG] pandas_result exists: {bool(pandas_result)}")
            
            # Extract execution details
            execution_status = pandas_result.get('execution_status') if pandas_result else None
            is_partial = pandas_result.get('partial', False) if pandas_result else False
            result_data = pandas_result.get('result', {}) if pandas_result else {}
            
            master_logger.info(f"[VIZ DEBUG] execution_status: {execution_status}")
            master_logger.info(f"[VIZ DEBUG] is_partial: {is_partial}")
            master_logger.info(f"[VIZ DEBUG] result_data type: {result_data.get('type') if result_data else 'None'}")

            # Initialize visualization variables
            viz_df = None
            viz_source = "none"
            should_attempt_viz = True  # Control flag to prevent forcing inappropriate visualizations
            
            # Try to reconstruct DataFrame from pandas result
            if result_data:
                result_type = result_data.get('type')
                
                if result_type == 'dataframe':
                    # Check if this is a singular value DataFrame (1 row = single answer like "USA")
                    shape = result_data.get('shape', (0, 0))
                    if shape[0] == 1:
                        # Single-row DataFrame represents a singular answer, not suitable for visualization
                        master_logger.info(f"[VIZ DEBUG] Single-row DataFrame (shape={shape}) detected - treating as singular value")
                        master_logger.info(f"[VIZ DEBUG] This is a singular answer (e.g., 'which country has most tickets?'), not suitable for chart visualization")
                        viz_source = "singular_value_dataframe"
                        should_attempt_viz = False  # Don't try to visualize singular answers
                    else:
                        # Multi-row DataFrame - proceed with reconstruction for visualization
                        try:
                            master_logger.info(f"[VIZ DEBUG] Reconstructing DataFrame from pandas result")
                            master_logger.info(f"[VIZ DEBUG] - Data rows: {len(result_data.get('data', []))}")
                            master_logger.info(f"[VIZ DEBUG] - Index items: {len(result_data.get('index', []))}")
                            master_logger.info(f"[VIZ DEBUG] - Columns: {len(result_data.get('columns', []))}")
                            
                            viz_df = pd.DataFrame(result_data['data'], index=result_data.get('index'))
                            viz_df.columns = result_data.get('columns', viz_df.columns)
                            viz_source = "reconstructed_dataframe"
                            
                            master_logger.info(f"[VIZ DEBUG] Reconstructed DF: shape={viz_df.shape}, columns={viz_df.columns.tolist()}")
                            
                        except Exception as e:
                            master_logger.error(f"[VIZ DEBUG] Reconstruction failed: {e}")
                            viz_source = "reconstruction_failed"
                        
                elif result_type == 'series':
                    # This shouldn't happen after normalization fix, but handle gracefully
                    master_logger.warning(f"[VIZ DEBUG] Unexpected series type - normalization should have converted this!")
                    viz_source = "unexpected_series"
                    # Still attempt visualization with raw data fallback
                    
                elif result_type == 'scalar':
                    # Single value - not suitable for visualization
                    scalar_value = result_data.get('value')
                    master_logger.info(f"[VIZ DEBUG] Scalar result ({scalar_value}) - not suitable for chart visualization")
                    viz_source = "scalar_result"
                    should_attempt_viz = False  # Don't try to visualize single numbers
                    
                elif result_type == 'other':
                    master_logger.warning(f"[VIZ DEBUG] Non-standard result type: {result_data.get('value')}")
                    viz_source = "other_type"
                    should_attempt_viz = False  # Don't try to visualize non-standard types
                    
                else:
                    master_logger.warning(f"[VIZ DEBUG] Unknown result type: {result_type}")
                    viz_source = "unknown_type"
                    # Still attempt with raw data fallback
            else:
                master_logger.info(f"[VIZ DEBUG] No result_data available (execution likely failed or returned None)")
                viz_source = "no_result_data"
                # Will fall back to raw data
            
            # Decision: What data to use for visualization?
            if not should_attempt_viz:
                master_logger.info(f"[VIZ DEBUG] Skipping visualization attempt (reason: {viz_source})")
                return None  # Return None to indicate visualization not suitable
            
            if viz_df is not None:
                master_logger.info(f"[VIZ DEBUG] Using {viz_source} for visualization")
                visualization = self.viz_service.create_intelligent_visualization(query, viz_df)
            else:
                master_logger.info(f"[VIZ DEBUG] Falling back to original data (reason: {viz_source})")
                visualization = self.viz_service.create_intelligent_visualization(query, data)
            
            # Respect visualization service's decision
            if visualization and not visualization.get('needs_visualization', True):
                master_logger.info(f"[VIZ DEBUG] Visualization service determined no visualization needed")
                return None
            
            master_logger.info(f"[VIZ DEBUG] Visualization created successfully (source: {viz_source})")
            return visualization
        except Exception as e:
            master_logger.error(f"Error creating visualization: {e}")
            return None
    
    async def _generate_enhanced_response(self, query: str, intent_result, analysis_result: Dict[str, Any]) -> str:
        """Generate enhanced response using LLM service"""
        try:
            response = await self.llm_service.generate_enhanced_response(
                query, intent_result, analysis_result, worksheet_name="CSV Data"
            )
            master_logger.info("Enhanced response generated successfully")
            return response
        except Exception as e:
            master_logger.error(f"Error generating enhanced response: {e}")
            return f"I've analyzed your query and identified it as {intent_result.primary_intent}. The analysis has been completed successfully."

    # ==================== LANGGRAPH NODE METHODS ====================
    
    async def _input_routing_node(self, state: QueryProcessingState) -> QueryProcessingState:
        """Initial routing based on chart availability - replaces early routing in process_with_services"""
        master_logger.info("=== LANGGRAPH: INPUT ROUTING NODE ===")
        
        try:
            if state["selected_chart"] is None:
                state["routing_decision"] = "no_chart_exploration"
                state["service_route"] = "data_exploration_no_chart"
                master_logger.info("Routing to no-chart exploration (no chart selected)")
            else:
                state["routing_decision"] = "intent_classification"
                master_logger.info("Routing to intent classification (chart selected)")
                
            state["execution_metadata"]["routing_timestamp"] = time.time()
            master_logger.info(f"Routing decision: {state['routing_decision']}")
        except Exception as e:
            master_logger.error(f"Input routing failed: {e}")
            state["needs_fallback"] = True
            state["error_context"] = {"node": "input_routing", "error": str(e)}
        
        return state

    async def _intent_classification_node(self, state: QueryProcessingState) -> QueryProcessingState:
        """Intent classification using existing _classify_intent method"""
        master_logger.info("=== LANGGRAPH: INTENT CLASSIFICATION NODE ===")
        
        try:
            # Use existing intent classification method
            classification = await self._classify_intent(state["query"], state.get("context"))
            
            if classification["success"]:
                state["bert_classification"] = classification
                
                # Build QueryIntent using existing _build_response method logic
                query_intent = QueryIntent(
                    primary_intent=classification["primary_intent"],
                    confidence=classification["confidence"], 
                    entities=classification.get("entities", {}),
                    requires_agents=self.agent_requirements.get(classification["primary_intent"], ["visualization"]),
                    chart_requirements=self._get_chart_requirements(classification["primary_intent"]),
                    metadata={
                        "original_query": state["query"],
                        "reasoning": classification.get("reasoning", ""),
                        "context": state.get("context", {}),
                        "processing_timestamp": datetime.now().isoformat()
                    }
                )
                state["intent_result"] = query_intent
                master_logger.info(f"Intent classified as: {classification['primary_intent']} (confidence: {classification['confidence']:.3f})")
                
            else:
                state["needs_fallback"] = True
                state["error_context"] = {"node": "intent_classification", "error": classification.get("error")}
                master_logger.error(f"Intent classification failed: {classification.get('error')}")
                
        except Exception as e:
            master_logger.error(f"Intent classification node failed: {e}")
            state["needs_fallback"] = True
            state["error_context"] = {"node": "intent_classification", "error": str(e)}
            
        return state

    async def _data_preprocessing_node(self, state: QueryProcessingState) -> QueryProcessingState:
        """Data preprocessing using existing _apply_column_cleaning method"""
        master_logger.info("=== LANGGRAPH: DATA PREPROCESSING NODE ===")
        
        try:
            # Get DataFrame from DataManager using data_id
            from services.data_manager import DataManager
            data_manager = DataManager()
            data_id = state.get("data_id")
            
            if data_id and state.get("selected_chart"):
                csv_data = data_manager.get_data(data_id)
                original_shape = csv_data.shape
                
                filtered_data, chart_context = self._apply_column_cleaning(
                    csv_data, 
                    state["selected_chart"], 
                    state["query"]
                )
                state["filtered_data"] = filtered_data
                state["chart_context"] = chart_context
                master_logger.info(f"Data filtered from {original_shape} to {filtered_data.shape if filtered_data is not None else 'None'}")
            else:
                # Use original data if available
                csv_data = data_manager.get_data(data_id) if data_id else None
                state["filtered_data"] = csv_data
                state["chart_context"] = None
                master_logger.info("Using original CSV data (no filtering applied)")
                
        except Exception as e:
            master_logger.error(f"Data preprocessing failed: {e}")
            state["needs_fallback"] = True
            state["error_context"] = {"node": "data_preprocessing", "error": str(e)}
            
        return state

    async def _service_routing_node(self, state: QueryProcessingState) -> QueryProcessingState:
        """Determine which service to route to based on intent"""
        master_logger.info("=== LANGGRAPH: SERVICE ROUTING NODE ===")
        
        try:
            intent_result = state.get("intent_result")
            if not intent_result:
                state["service_route"] = "fallback"
                master_logger.warning("No intent result available, routing to fallback")
                return state
                
            original_intent = intent_result.entities.get('original_intent', '')
            primary_intent = intent_result.primary_intent
            
            # Apply existing routing logic from process_with_services
            if original_intent == 'analysis':
                state["service_route"] = "shap_analysis"
            elif original_intent == 'exploration':
                state["service_route"] = "data_exploration"  
            else:
                state["service_route"] = "general_processing"
                
            master_logger.info(f"Service route determined: {state['service_route']} (intent: {primary_intent}, original: {original_intent})")
        except Exception as e:
            master_logger.error(f"Service routing failed: {e}")
            state["needs_fallback"] = True
            state["error_context"] = {"node": "service_routing", "error": str(e)}
            
        return state

    async def _no_chart_exploration_node(self, state: QueryProcessingState) -> QueryProcessingState:
        """No-chart exploration using existing logic from process_with_services"""
        master_logger.info("=== LANGGRAPH: NO-CHART EXPLORATION NODE ===")
        
        try:
            # Extract the no-chart exploration logic from process_with_services
            from services.data_exploration_no_chart import data_exploration
            
            import app
            smart_agg_decider = getattr(app, 'smart_agg_decider', None)
            
            exploration_service = data_exploration(
                llm_client=self.llm_client,
                smart_agg_decider=smart_agg_decider,
                cache_path="causal_analysis_cache.json"
            )
            
            minimal_intent = QueryIntent(
                primary_intent='data_exploration',
                confidence=1.0,
                entities={'query': state["query"]}
            )
            
            # Get DataFrame from DataManager
            from services.data_manager import DataManager
            data_manager = DataManager()
            csv_data = data_manager.get_data(state["data_id"])
            
            result = await exploration_service.process(
                query_text=state["query"],
                csv_data=csv_data,
                selected_chart=None,
                intent_result=minimal_intent,
                chart_context=None
            )
            
            state["final_response"] = {
                "success": result.get('success', False),
                "reply": result.get('response', 'Analysis completed'),
                "intent": minimal_intent,
                "analysis_result": result.get('computational_results', {}),
                "visualization": {
                    'needs_visualization': result.get('needs_visualization', False),
                    'chart_type': result.get('chart_type'),
                    'chart_image': result.get('chart_image')
                },
                "execution_time": result.get('execution_time', 0),
                "routed_to": "services.data_exploration_no_chart.data_exploration"
            }
            state["processing_complete"] = True
            master_logger.info("✅ No-chart exploration completed successfully")
            
        except Exception as e:
            master_logger.error(f"No-chart exploration failed: {e}")
            state["needs_fallback"] = True
            state["error_context"] = {"node": "no_chart_exploration", "error": str(e)}
            
        return state

    async def _shap_analysis_service_node(self, state: QueryProcessingState) -> QueryProcessingState:
        """SHAP analysis service using existing logic from process_with_services"""
        master_logger.info("=== LANGGRAPH: SHAP ANALYSIS SERVICE NODE ===")
        
        try:
            # Extract SHAP analysis logic from process_with_services
            from services.shap_analysis_v6 import shap_analysis
            
            import app
            smart_agg_decider = getattr(app, 'smart_agg_decider', None)
            
            analysis_service = shap_analysis(
                llm_client=self.llm_client,
                smart_agg_decider=smart_agg_decider,
                causal_cache_path="causal_analysis_cache.json"
            )
            
            # Extract workbook_id from context for cache
            workbook_id = None
            ctx = state.get("context")
            if ctx:
                if isinstance(ctx, dict):
                    workbook_id = ctx.get("workbook_id")
                else:  # ChatContext object
                    workbook_id = getattr(ctx, "workbook_id", None)
            
            # Pass data_id instead of csv_data (reference pattern - eliminates JSON bottleneck)
            result = analysis_service.process(
                query_text=state["query"],
                data_id=state["data_id"],
                connection_key=state.get("connection_key", "default"),
                selected_chart=state["selected_chart"],
                intent_result=state["intent_result"],
                chart_context={"workbook_id": workbook_id}  # FIX: Pass workbook_id for cache
            )
            
            state["final_response"] = {
                "success": result.get('success', False),
                "reply": result.get('response', 'Analysis completed'),
                "intent": state["intent_result"],
                "analysis_result": result.get('computational_results', {}),
                "features_table": result.get('features_table'),
                "shap_visualizations": result.get('shap_visualizations', []),
                "visualization": {
                    'needs_visualization': result.get('needs_visualization', False),
                    'chart_type': result.get('chart_type'),
                    'chart_image': result.get('chart_image')
                },
                "execution_time": result.get('execution_time', 0),
                "routed_to": "services.shap_analysis.shap_analysis"
            }
            state["processing_complete"] = True
            master_logger.info("✅ SHAP analysis completed successfully")
            
        except Exception as e:
            master_logger.error(f"SHAP analysis failed: {e}")
            state["needs_fallback"] = True
            state["error_context"] = {"node": "shap_analysis", "error": str(e)}
            
        return state

    async def _data_exploration_service_node(self, state: QueryProcessingState) -> QueryProcessingState:
        """Data exploration service using existing logic from process_with_services"""
        master_logger.info("=== LANGGRAPH: DATA EXPLORATION SERVICE NODE ===")
        
        try:
            # Extract data exploration logic from process_with_services
            from services.data_exploration import data_exploration
            
            import app
            smart_agg_decider = getattr(app, 'smart_agg_decider', None)
            
            exploration_service = data_exploration(
                llm_client=self.llm_client,
                smart_agg_decider=smart_agg_decider,
                cache_path="causal_analysis_cache.json"
            )
            
            # Get DataFrame from DataManager
            from services.data_manager import DataManager
            data_manager = DataManager()
            csv_data = data_manager.get_data(state["data_id"])
            
            result = await exploration_service.process(
                query_text=state["query"],
                csv_data=csv_data,
                selected_chart=state["selected_chart"],
                intent_result=state["intent_result"],
                chart_context=None
            )
            
            state["final_response"] = {
                "success": result.get('success', False),
                "reply": result.get('response', 'Exploration completed'),
                "intent": state["intent_result"],
                "analysis_result": result.get('computational_results', {}),
                "visualization": {
                    'needs_visualization': result.get('needs_visualization', False),
                    'chart_type': result.get('chart_type'),
                    'chart_image': result.get('chart_image')
                },
                "execution_time": result.get('execution_time', 0),
                "routed_to": "services.data_exploration.data_exploration"
            }
            state["processing_complete"] = True
            master_logger.info("✅ Data exploration completed successfully")
            
        except Exception as e:
            master_logger.error(f"Data exploration failed: {e}")
            state["needs_fallback"] = True
            state["error_context"] = {"node": "data_exploration", "error": str(e)}
            
        return state

    # 🆕 6. Pass Session Context to NL_to_python
    async def _general_processing_node(self, state: QueryProcessingState) -> QueryProcessingState:
        """General processing using existing _execute_intent_analysis method"""
        master_logger.info("=== LANGGRAPH: GENERAL PROCESSING NODE ===")
        
        try:
            # 🆕 Pass session context to NL_to_python
            session_id = state.get("session_id")
            source_id = state.get("source_id", "default")
            
            if hasattr(self.nl_to_python, 'context_manager'):
                # Reinitialize context manager with session support
                from services.NL_to_python import DefaultContextManager
                self.nl_to_python.context_manager = DefaultContextManager(
                    session_manager=self.session_manager,
                    session_id=session_id,
                    source_id=source_id
                )
                master_logger.info(f"[CONTEXT_MGR] Passed session {session_id} / source {source_id} to NL_to_python")

            # Use existing intent analysis method
            analysis_result = await self._execute_intent_analysis(
                state["intent_result"], 
                state["query"], 
                state["filtered_data"], 
                state["chart_context"]
            )
            state["analysis_result"] = analysis_result
            master_logger.info(f"General processing completed, success: {analysis_result.get('success', False)}")
            
        except Exception as e:
            master_logger.error(f"General processing failed: {e}")
            state["needs_fallback"] = True
            state["error_context"] = {"node": "general_processing", "error": str(e)}
            
        return state

    async def _visualization_node(self, state: QueryProcessingState) -> QueryProcessingState:
        """Visualization using existing _create_visualization_for_intent method"""
        master_logger.info("=== LANGGRAPH: VISUALIZATION NODE ===")
        
        try:
            if state.get("analysis_result") and self._requires_visualization(state["intent_result"]):
                visualization = await self._create_visualization_for_intent(
                    state["intent_result"], 
                    state["analysis_result"], 
                    state["filtered_data"]
                )
                state["visualization"] = visualization
                master_logger.info(f"Visualization created: {visualization is not None}")
            else:
                master_logger.info("No visualization required or analysis result unavailable")
                
        except Exception as e:
            master_logger.error(f"Visualization failed: {e}")
            # Visualization failure is not critical, continue processing
            state["visualization"] = None
            
        return state

    async def _response_assembly_node(self, state: QueryProcessingState) -> QueryProcessingState:
        """Final response assembly using existing _generate_enhanced_response method"""
        master_logger.info("=== LANGGRAPH: RESPONSE ASSEMBLY NODE ===")
        
        try:
            if not state.get("final_response"):
                # Generate response using existing methods
                if state.get("analysis_result", {}).get('llm_enhanced_analysis'):
                    response_text = state["analysis_result"]["llm_enhanced_analysis"]
                else:
                    response_text = await self._generate_enhanced_response(
                        state["query"], 
                        state["intent_result"], 
                        state["analysis_result"]
                    )
                    
                state["final_response"] = {
                    "success": True,
                    "reply": response_text,
                    "intent": state["intent_result"],
                    "analysis_result": state["analysis_result"],
                    "visualization": state["visualization"],
                    "chart_context": state["chart_context"],
                    "execution_time": time.time() - state["execution_metadata"]["start_time"]
                }
            state["processing_complete"] = True
            master_logger.info("Response assembly completed")
            
        except Exception as e:
            master_logger.error(f"Response assembly failed: {e}")
            state["needs_fallback"] = True
            state["error_context"] = {"node": "response_assembly", "error": str(e)}
            
        return state

    async def _fallback_node(self, state: QueryProcessingState) -> QueryProcessingState:
        """Fallback processing for error recovery"""
        master_logger.info("=== LANGGRAPH: FALLBACK NODE ===")
        
        error_context = state.get("error_context", {})
        master_logger.warning(f"Fallback triggered by: {error_context}")
        
        # Create minimal fallback response
        state["final_response"] = {
            "success": False,
            "reply": f"I encountered an issue processing your query. Error in {error_context.get('node', 'unknown')}: {error_context.get('error', 'Unknown error')}",
            "error": True,
            "fallback_used": True
        }
        state["processing_complete"] = True
        state["needs_fallback"] = False
        
        return state

    # ==================== LANGGRAPH ROUTING FUNCTIONS ====================

    def _route_after_input(self, state: QueryProcessingState) -> str:
        """Route after input routing node"""
        if state.get("needs_fallback"):
            return "fallback"
        
        routing_decision = state.get("routing_decision")
        if routing_decision == "no_chart_exploration":
            return "no_chart_exploration"
        elif routing_decision == "intent_classification":
            return "intent_classification"
        else:
            return "fallback"

    def _route_after_intent_classification(self, state: QueryProcessingState) -> str:
        """Route after intent classification"""
        if state.get("needs_fallback"):
            return "fallback"
        return "data_preprocessing"

    def _route_after_data_preprocessing(self, state: QueryProcessingState) -> str:
        """Route after data preprocessing"""
        if state.get("needs_fallback"):
            return "fallback"
        return "service_routing"

    def _route_after_service_routing(self, state: QueryProcessingState) -> str:
        """Route to appropriate service after service routing"""
        if state.get("needs_fallback"):
            return "fallback"
            
        service_route = state.get("service_route")
        if service_route == "shap_analysis":
            return "shap_analysis_service"
        elif service_route == "data_exploration":
            return "data_exploration_service"
        elif service_route == "general_processing":
            return "general_processing"
        else:
            return "fallback"

    def _route_after_service_processing(self, state: QueryProcessingState) -> str:
        """Route after service processing completes"""
        if state.get("needs_fallback"):
            return "fallback"
        if state.get("processing_complete"):
            return END
        return "visualization"

    def _route_after_general_processing(self, state: QueryProcessingState) -> str:
        """Route after general processing"""
        if state.get("needs_fallback"):
            return "fallback"
        return "visualization"

    def _route_after_visualization(self, state: QueryProcessingState) -> str:
        """Route after visualization"""
        if state.get("needs_fallback"):
            return "fallback"
        return "response_assembly"

    def _route_after_response_assembly(self, state: QueryProcessingState) -> str:
        """Route after response assembly"""
        if state.get("needs_fallback"):
            return "fallback"
        return END

    def _build_langgraph(self):
        """Build the LangGraph for query processing workflow"""
        master_logger.info("Building LangGraph for query processing")
        
        try:
            # Create the graph
            workflow = StateGraph(QueryProcessingState)
            
            # Add all nodes
            workflow.add_node("input_routing", self._input_routing_node)
            workflow.add_node("intent_classification", self._intent_classification_node)
            workflow.add_node("data_preprocessing", self._data_preprocessing_node)
            workflow.add_node("service_routing", self._service_routing_node)
            workflow.add_node("no_chart_exploration", self._no_chart_exploration_node)
            workflow.add_node("shap_analysis_service", self._shap_analysis_service_node)
            workflow.add_node("data_exploration_service", self._data_exploration_service_node)
            workflow.add_node("general_processing", self._general_processing_node)
            workflow.add_node("create_visualization", self._visualization_node)
            workflow.add_node("response_assembly", self._response_assembly_node)
            workflow.add_node("fallback", self._fallback_node)
            
            # Set entry point
            workflow.set_entry_point("input_routing")
            
            # Add conditional edges with routing functions
            workflow.add_conditional_edges(
                "input_routing",
                self._route_after_input,
                {
                    "no_chart_exploration": "no_chart_exploration",
                    "intent_classification": "intent_classification", 
                    "fallback": "fallback"
                }
            )
            
            workflow.add_conditional_edges(
                "intent_classification",
                self._route_after_intent_classification,
                {
                    "data_preprocessing": "data_preprocessing",
                    "fallback": "fallback"
                }
            )
            
            workflow.add_conditional_edges(
                "data_preprocessing", 
                self._route_after_data_preprocessing,
                {
                    "service_routing": "service_routing",
                    "fallback": "fallback"
                }
            )
            
            workflow.add_conditional_edges(
                "service_routing",
                self._route_after_service_routing,
                {
                    "shap_analysis_service": "shap_analysis_service",
                    "data_exploration_service": "data_exploration_service",
                    "general_processing": "general_processing",
                    "fallback": "fallback"
                }
            )
            
            # Service nodes can either complete processing or continue
            workflow.add_conditional_edges(
                "shap_analysis_service",
                self._route_after_service_processing,
                {
                    END: END,
                    "visualization": "create_visualization", 
                    "fallback": "fallback"
                }
            )
            
            workflow.add_conditional_edges(
                "data_exploration_service",
                self._route_after_service_processing,
                {
                    END: END,
                    "visualization": "create_visualization",
                    "fallback": "fallback"
                }
            )
            
            workflow.add_edge("no_chart_exploration", END)
            
            workflow.add_conditional_edges(
                "general_processing",
                self._route_after_general_processing,
                {
                    "visualization":"create_visualization",
                    "fallback": "fallback"
                }
            )
            
            workflow.add_conditional_edges(
                "create_visualization",
                self._route_after_visualization,
                {
                    "response_assembly": "response_assembly",
                    "fallback": "fallback"
                }
            )
            
            workflow.add_conditional_edges(
                "response_assembly",
                self._route_after_response_assembly,
                {
                    END: END,
                    "fallback": "fallback"
                }
            )
            
            workflow.add_edge("fallback", END)
            
            # Compile the graph
            self.query_graph = workflow.compile()
            master_logger.info("✅ LangGraph compiled successfully with all nodes and routing")
            
        except Exception as e:
            master_logger.error(f"Failed to build LangGraph: {e}")
            master_logger.error(f"Traceback: {traceback.format_exc()}")
            # Set a None graph to indicate failure
            self.query_graph = None
            raise

    def _is_langgraph_available(self) -> bool:
        """Check if LangGraph is available and working"""
        try:
            return hasattr(self, 'query_graph') and self.query_graph is not None
        except Exception as e:
            master_logger.error(f"LangGraph availability check failed: {e}")
            return False

    def get_langgraph_status(self) -> Dict[str, Any]:
        """Get LangGraph-specific status information"""
        return {
            "langgraph_enabled": self._is_langgraph_available(),
            "graph_nodes": len(self.query_graph.nodes) if hasattr(self, 'query_graph') and self.query_graph else 0,
            "graph_edges": len(self.query_graph.edges) if hasattr(self, 'query_graph') and self.query_graph else 0,
        }


    async def test_langgraph_integration(self):
        """Test LangGraph integration with sample queries"""
        master_logger.info("=== TESTING LANGGRAPH INTEGRATION ===")
        
        test_queries = [
            ("show me top 5 sales by region", None),  # No chart - should route to no_chart_exploration
            ("correlation between months and handling time", "test_chart"),  # With chart - should do intent classification
            ("why did time tank last quarter", "performance_chart"),  # Analysis intent
        ]
        
        for query, chart in test_queries:
            master_logger.info(f"Testing: '{query}' with chart: {chart}")
            
            try:
                # Test with sample data
                sample_data = pd.DataFrame({
                    'region': ['North', 'South', 'East', 'West'],
                    'sales': [100, 200, 150, 300],
                    'months': ['Jan', 'Feb', 'Mar', 'Apr'],
                    'handling_time': [5, 10, 8, 12]
                })
                
                result = await self.process_with_services(query, sample_data, chart)
                
                master_logger.info(f"✅ Test passed for '{query}':")
                master_logger.info(f"   Success: {result.get('success')}")
                master_logger.info(f"   Routed to: {result.get('routed_to', 'N/A')}")
                master_logger.info(f"   Reply length: {len(result.get('reply', ''))}")
                
            except Exception as e:
                master_logger.error(f"❌ Test failed for '{query}': {e}")


# --- Test Function ---
async def test_simplified_agent():
    """Test the LangGraph-enabled agent thoroughly."""
    master_logger.info("=== STARTING LANGGRAPH QUERY AGENT TEST ===")
    
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)
    
    # Load configuration
    master_logger.info("Loading configuration for testing")
    try:
        with open("config.yaml", "r") as f:
            config = yaml.safe_load(f)
        master_logger.info("Configuration loaded successfully")
        master_logger.debug(f"Config keys: {list(config.keys())}")
    except FileNotFoundError:
        master_logger.error("config.yaml not found")
        print("ERROR: config.yaml not found")
        return

    # Initialize
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set")
        return
    
    llm_client = OpenAI(api_key=api_key)
    agent = QueryAgent(llm_client, config, logger)
    
    # Test LangGraph integration first
    try:
        await agent.test_langgraph_integration()
    except Exception as e:
        print(f"LangGraph integration test failed: {e}")
    
    # Comprehensive test cases
    test_cases = [
        # The problematic cases from your system
        "correlation between months and handling time",
        "why did time tank last quarter", 
        "top 2 important features",
        
        # Clear cases
        "show me top 5 sales by region",
        "detect anomalies in call volume", 
        "compare Q3 vs Q4 performance",
        "seasonal patterns in revenue",
        "predict next month's sales",
        
        # Edge cases
        "what drives customer satisfaction?",
        "unusual spikes in response time",
        "forecast holiday season demand"
    ]
    
    print("\n" + "="*60)
    print("TESTING LANGGRAPH-ENABLED AGENT")
    print("="*60)
    
    for i, query in enumerate(test_cases, 1):
        print(f"\nTest {i}: \"{query}\"")
        print("-" * 50)
        
        # Test intent classification (process method)
        response = await agent.process(query)
        
        if response.success:
            intent = response.data.primary_intent
            confidence = response.confidence
            time_ms = response.execution_time * 1000
            agents = response.data.requires_agents
            
            print(f"Intent: {intent}")
            print(f"Confidence: {confidence:.1%}")
            print(f"Time: {time_ms:.0f}ms") 
            print(f"Agents: {agents}")
            print(f"Reasoning: {response.data.metadata.get('reasoning', 'N/A')}")
        else:
            print(f"FAILED: {response.message}")
    
    print("\n" + "="*60)
    print("LANGGRAPH STATISTICS")
    print("="*60)
    
    # Regular stats
    stats = agent.get_agent_status()
    for key, value in stats.items():
        print(f"{key}: {value}")
    
    # LangGraph-specific stats
    lg_stats = agent.get_langgraph_status()
    for key, value in lg_stats.items():
        print(f"{key}: {value}")

if __name__ == "__main__":
    asyncio.run(test_simplified_agent())