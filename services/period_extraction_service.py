"""
Period Extraction Service using BERT NER Model

This module uses a local BERT model for Named Entity Recognition (NER) to extract
temporal periods and events from natural language queries.

Based on reference_code/period_bert_test.ipynb
Model: event-period-ner-bert (local)

Entity Types:
- PERIOD: Temporal expressions (dates, months, years, relative times)
- EVENT_SPIKE: Increase/growth events
- EVENT_DIP: Decrease/decline events
"""

import re
import numpy as np
import logging
from typing import Dict, List, Tuple, Any
from pathlib import Path

import torch
from transformers import AutoTokenizer, AutoModelForTokenClassification, pipeline

from master_logger import setup_module_logger


class PeriodExtractionService:
    """
    BERT-based NER for extracting temporal periods and events from queries.
    Uses local event-period-ner-bert model.
    Implements complete cleanup pipeline from reference_code/period_bert_test.ipynb
    """
    
    def __init__(self, model_path: str = "event-period-ner-bert"):
        """
        Initialize the BERT NER model from local path.
        
        Args:
            model_path: Path to local model directory (default: "event-period-ner-bert")
        """
        self.logger = setup_module_logger('services.period_extraction_service')
        self.model_path = model_path
        
        try:
            self.logger.info(f"Loading BERT model from local path: {model_path}")
            
            # Set deterministic behavior for reproducible results
            torch.use_deterministic_algorithms(True, warn_only=True)
            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = False
            
            # Detect available device
            device = 0 if torch.cuda.is_available() else -1  # 0 for GPU, -1 for CPU
            device_name = "GPU (CUDA)" if device == 0 else "CPU"
            self.logger.info(f"Using device: {device_name}")
            
            # Load tokenizer and model from local path with explicit dtype
            self.tokenizer = AutoTokenizer.from_pretrained(model_path)
            self.model = AutoModelForTokenClassification.from_pretrained(
                model_path,
                torch_dtype=torch.float32  # Explicit precision for consistency
            )
            
            # Set model to evaluation mode for inference
            self.model.eval()
            
            # Create NER pipeline with explicit device and framework settings
            self.ner_pipeline = pipeline(
                "ner",
                model=self.model,
                tokenizer=self.tokenizer,
                aggregation_strategy="simple",
                device=device,  # Explicit device placement
                framework="pt"  # Explicit PyTorch framework
            )
            
            self.logger.info(f"✓ BERT NER model loaded successfully from {model_path} on {device_name}")
            
        except Exception as e:
            self.logger.error(f"Failed to load BERT model from {model_path}: {e}")
            raise
    
    # ============================================================================
    # CLEANUP LAYER - POST-PROCESSING FUNCTIONS
    # Exact implementation from reference_code/period_bert_test.ipynb
    # ============================================================================
    
    def merge_subword_tokens(self, entities: List[Dict]) -> List[Dict]:
        """
        Merge BERT subword tokens (##) into complete words
        
        Args:
            entities: List of entity dictionaries from NER pipeline
            
        Returns:
            List of merged entities
        """
        if not entities:
            return []
        
        cleaned = []
        i = 0
        
        while i < len(entities):
            current = entities[i].copy()
            
            # Check if next token is a subword
            if i + 1 < len(entities) and entities[i + 1]['word'].startswith('##'):
                # Merge consecutive subwords
                merged_word = current['word']
                merged_score = [current['score']]
                j = i + 1
                
                while j < len(entities) and entities[j]['word'].startswith('##'):
                    merged_word += entities[j]['word'].replace('##', '')
                    merged_score.append(entities[j]['score'])
                    j += 1
                
                current['word'] = merged_word
                current['score'] = sum(merged_score) / len(merged_score)
                i = j
            else:
                i += 1
            
            cleaned.append(current)
        
        return cleaned
    
    def strip_punctuation(self, entities: List[Dict]) -> List[Dict]:
        """
        Remove trailing/leading punctuation from entity words
        
        Args:
            entities: List of entity dictionaries
            
        Returns:
            List of entities with cleaned words
        """
        cleaned = []
        
        for entity in entities:
            word = entity['word']
            # Strip common punctuation but keep hyphens and slashes in dates
            # Put hyphen at the end to avoid character range issues
            word = re.sub(r'^[^\w\s/\-]+|[^\w\s/\-]+$', '', word)
            
            if word:  # Only keep if there's content left
                entity_copy = entity.copy()
                entity_copy['word'] = word
                cleaned.append(entity_copy)
        
        return cleaned
    
    def consolidate_adjacent_periods(self, entities: List[Dict], original_query: str) -> List[Dict]:
        """
        Consolidate adjacent PERIOD entities that should be one (e.g., date ranges)
        
        Args:
            entities: List of entity dictionaries
            original_query: Original query text for context
            
        Returns:
            List of consolidated entities
        """
        if not entities:
            return []
        
        consolidated = []
        i = 0
        
        while i < len(entities):
            current = entities[i].copy()
            
            # Check if current is PERIOD and next is also PERIOD
            if (current['entity_group'] == 'PERIOD' and
                i + 1 < len(entities) and
                entities[i + 1]['entity_group'] == 'PERIOD'):
                
                # Check if they should be merged (close proximity in original text)
                current_word = current['word']
                next_word = entities[i + 1]['word']
                
                # Look for these words in original query
                pattern = re.escape(current_word) + r'[\s\-]*' + re.escape(next_word)
                if re.search(pattern, original_query, re.IGNORECASE):
                    # Merge them
                    current['word'] = f"{current_word} {next_word}"
                    current['score'] = (current['score'] + entities[i + 1]['score']) / 2
                    i += 2
                else:
                    i += 1
            else:
                i += 1
            
            consolidated.append(current)
        
        return consolidated
    
    def normalize_event_types(self, entities: List[Dict]) -> List[Dict]:
        """
        Optionally normalize EVENT_DIP and EVENT_SPIKE to just EVENT
        
        Args:
            entities: List of entity dictionaries
            
        Returns:
            List of entities with normalized types
        """
        normalized = []
        
        for entity in entities:
            entity_copy = entity.copy()
            if entity_copy['entity_group'] in ['EVENT_DIP', 'EVENT_SPIKE']:
                entity_copy['original_type'] = entity_copy['entity_group']
                entity_copy['entity_group'] = 'EVENT'
            normalized.append(entity_copy)
        
        return normalized
    
    def filter_low_confidence(self, entities: List[Dict], threshold: float = 0.5) -> List[Dict]:
        """
        Filter out entities with confidence scores below threshold
        
        Args:
            entities: List of entity dictionaries
            threshold: Minimum confidence score (default 0.5)
            
        Returns:
            Filtered list of entities
        """
        return [e for e in entities if e['score'] >= threshold]
    
    def add_fallback_temporal_extraction(self, query: str, entities: List[Dict]) -> List[Dict]:
        """
        Add regex-based fallback for common temporal expressions missed by NER
        
        Args:
            query: Original query text
            entities: List of existing entities
            
        Returns:
            Enhanced list with fallback entities
        """
        # Check if we already have PERIOD entities
        has_period = any(e['entity_group'] == 'PERIOD' for e in entities)
        
        if not has_period:
            # Regex patterns for common temporal expressions
            patterns = [
                (r'\byesterday\b', 'yesterday'),
                (r'\btoday\b', 'today'),
                (r'\btomorrow\b', 'tomorrow'),
                (r'\bthis (week|month|year|quarter)\b', None),
                (r'\blast (week|month|year|quarter)\b', None),
                (r'\bnext (week|month|year|quarter)\b', None),
                (r'\bpast \d+ (days|weeks|months|years)\b', None),
            ]
            
            for pattern, fixed_word in patterns:
                match = re.search(pattern, query, re.IGNORECASE)
                if match:
                    fallback_entity = {
                        'entity_group': 'PERIOD',
                        'word': fixed_word or match.group(0),
                        'score': 0.9,  # High confidence for regex match
                        'start': match.start(),
                        'end': match.end(),
                        'fallback': True
                    }
                    entities.append(fallback_entity)
                    break  # Only add one fallback per query
        
        return entities
    
    def clean_entities(self, entities: List[Dict], query: str,
                      merge_events: bool = False,
                      confidence_threshold: float = 0.5,
                      use_fallback: bool = True) -> Dict:
        """
        Complete cleanup pipeline for NER entities
        
        Args:
            entities: Raw entities from NER pipeline
            query: Original query text
            merge_events: Whether to merge EVENT_DIP/EVENT_SPIKE to EVENT
            confidence_threshold: Minimum confidence score
            use_fallback: Whether to use regex fallback for missed temporal expressions
            
        Returns:
            Dictionary with cleaned entities and metadata
        """
        original_count = len(entities)
        
        # Step 1: Merge subword tokens
        entities = self.merge_subword_tokens(entities)
        
        # Step 2: Strip punctuation
        entities = self.strip_punctuation(entities)
        
        # Step 3: Consolidate adjacent periods
        entities = self.consolidate_adjacent_periods(entities, query)
        
        # Step 4: Filter low confidence
        entities = self.filter_low_confidence(entities, confidence_threshold)
        
        # Step 5: Add fallback temporal extraction
        if use_fallback:
            entities = self.add_fallback_temporal_extraction(query, entities)
        
        # Step 6: Normalize event types (optional)
        if merge_events:
            entities = self.normalize_event_types(entities)
        
        # Separate by type
        events = [e for e in entities if 'EVENT' in e['entity_group']]
        periods = [e for e in entities if e['entity_group'] == 'PERIOD']
        
        return {
            'entities': entities,
            'events': events,
            'periods': periods,
            'original_count': original_count,
            'cleaned_count': len(entities),
            'has_period': len(periods) > 0,
            'has_event': len(events) > 0
        }
    
    # ============================================================================
    # UTILITY FUNCTIONS
    # ============================================================================
    
    def convert_to_serializable(self, obj):
        """Convert numpy types to native Python types for JSON serialization"""
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, dict):
            return {key: self.convert_to_serializable(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self.convert_to_serializable(item) for item in obj]
        else:
            return obj
    
    # ============================================================================
    # MAIN INFERENCE METHOD
    # ============================================================================
    
    def extract_periods_and_events(self, query: str, apply_cleanup: bool = True) -> Dict:
        """
        Run NER inference on a query with optional cleanup
        
        Main method to extract temporal periods and events from natural language queries.
        
        Args:
            query: Input text query
            apply_cleanup: Whether to apply cleanup layer (default True)
            
        Returns:
            Dictionary with cleaned entities separated by type:
            {
                'entities': List[Dict],  # All cleaned entities
                'events': List[Dict],    # EVENT_SPIKE/EVENT_DIP entities
                'periods': List[Dict],   # PERIOD entities
                'original_count': int,
                'cleaned_count': int,
                'has_period': bool,
                'has_event': bool
            }
        """
        try:
            self.logger.debug(f"Extracting periods/events from query: '{query}'")
            
            # Pre-process query: Strip leading/trailing punctuation to improve BERT confidence
            # This addresses the issue where "?" or "!" at the end causes lower confidence scores
            # because the BERT model was trained on declarative sentences without such punctuation
            query_cleaned = re.sub(r'^[^\w\s]+|[^\w\s]+$', '', query.strip())
            
            if query_cleaned != query.strip():
                self.logger.debug(f"Stripped leading/trailing punctuation: '{query}' → '{query_cleaned}'")
            
            # Get raw entities from BERT model with deterministic inference
            with torch.no_grad():  # Disable gradient computation for inference
                raw_entities = self.ner_pipeline(query_cleaned)
            raw_entities = self.convert_to_serializable(raw_entities)
            
            self.logger.debug(f"Raw BERT output: {len(raw_entities)} entities")
            
            # Apply cleanup if requested
            if apply_cleanup:
                cleaned_result = self.clean_entities(
                    raw_entities.copy(),
                    query,
                    merge_events=False,  # Keep EVENT_DIP/EVENT_SPIKE distinction
                    confidence_threshold=0.5,
                    use_fallback=True
                )
            else:
                cleaned_result = {
                    'entities': raw_entities,
                    'events': [],
                    'periods': [],
                    'original_count': len(raw_entities),
                    'cleaned_count': len(raw_entities),
                    'has_period': False,
                    'has_event': False
                }
            
            self.logger.debug(f"Cleaned result: {cleaned_result['cleaned_count']} entities, "
                            f"{len(cleaned_result['periods'])} periods, "
                            f"{len(cleaned_result['events'])} events")
            
            return cleaned_result
            
        except Exception as e:
            self.logger.error(f"Error processing query: {query}")
            self.logger.error(f"Error: {str(e)}", exc_info=True)
            return {
                'entities': [],
                'events': [],
                'periods': [],
                'original_count': 0,
                'cleaned_count': 0,
                'has_period': False,
                'has_event': False
            }
