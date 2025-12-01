"""
Temporal Entity Extractor - Hybrid NER BERT + LLM Approach

NO HARDCODING:
- Uses actual data statistics for validation
- Validates NER output even when confident
- Falls back to LLM when NER fails
- All period resolution based on actual data

Author: Temporal Comparison Analysis Module
"""

import json
import re
from typing import Dict, List, Any, Optional
import pandas as pd
import logging


class TemporalEntityExtractor:
    """
    Extracts temporal entities from queries using hybrid approach
    
    Strategy:
    1. Try NER BERT first (fast but unreliable)
    2. Validate NER output thoroughly (even if confident)
    3. Fallback to LLM if NER fails validation
    4. Support retry with corrections
    """
    
    def __init__(self, query: str, df: pd.DataFrame, date_column: str,
                 llm_client, logger=None, retry_corrections: Optional[List[str]] = None):
        """
        Initialize extractor
        
        Args:
            query: User query to analyze
            df: DataFrame with data
            date_column: Name of date column
            llm_client: OpenAI client for LLM fallback
            logger: Logger instance
            retry_corrections: Corrections from previous attempt (for retry)
        """
        self.query = query
        self.df = df
        self.date_column = date_column
        self.llm_client = llm_client
        self.logger = logger or logging.getLogger(__name__)
        self.retry_corrections = retry_corrections or []
        
        self.date_analysis = None
    
    def extract(self) -> Dict[str, Any]:
        """
        Main extraction method - hybrid approach with validation
        
        Returns:
            Dictionary with extraction results
        """
        try:
            # === DIAGNOSTIC LOGGING START ===
            self.logger.info(f"[EXTRACTOR_DEBUG] extract() called")
            self.logger.info(f"[EXTRACTOR_DEBUG] query: '{self.query}'")
            self.logger.info(f"[EXTRACTOR_DEBUG] date_column: '{self.date_column}'")
            self.logger.info(f"[EXTRACTOR_DEBUG] df.shape: {self.df.shape}")
            self.logger.info(f"[EXTRACTOR_DEBUG] date_column in df.columns: {self.date_column in self.df.columns if self.date_column else 'N/A'}")
            self.logger.info(f"[EXTRACTOR_DEBUG] llm_client is None: {self.llm_client is None}")
            # === DIAGNOSTIC LOGGING END ===
            
            # Step 1: Analyze date column first (need this for validation)
            from services.date_analyzer import analyze_date_column
            
            self.logger.info(f"[EXTRACTOR_DEBUG] About to call analyze_date_column...")
            self.date_analysis = analyze_date_column(
                self.df, 
                self.date_column, 
                self.llm_client, 
                self.logger
            )
            self.logger.info(f"[EXTRACTOR_DEBUG] analyze_date_column returned: {self.date_analysis.keys()}")
            self.logger.info(f"[EXTRACTOR_DEBUG] date_analysis['valid']: {self.date_analysis.get('valid')}")
            
            if not self.date_analysis['valid']:
                self.logger.warning(f"[EXTRACTOR_DEBUG] Date analysis failed, reason: {self.date_analysis.get('error', 'Unknown')}")
                return self._failure_response("Date column analysis failed")
            
            self.logger.info(f"Date analysis: {self.date_analysis['granularity']} granularity, "
                           f"{len(self.date_analysis['years'])} years available")
            
            # Step 2: Try NER BERT (fast but unreliable)
            ner_result = self._try_ner_bert()
            
            # Step 3: Validate NER output (CRITICAL - even if confident!)
            if ner_result and ner_result.get('confidence', 0) > 0.7:
                self.logger.info(f"NER BERT returned result with confidence {ner_result['confidence']:.2f}")
                validated = self._validate_ner_output(ner_result)
                
                if validated['is_valid']:
                    self.logger.info("✓ NER BERT output validated successfully")
                    return self._process_validated_ner(validated)
                else:
                    self.logger.warning(f"✗ NER BERT confident but invalid: {validated['reason']}")
                    # Fall through to LLM
            elif ner_result:
                self.logger.info(f"NER BERT low confidence ({ner_result.get('confidence', 0):.2f}), using LLM")
            else:
                self.logger.info("NER BERT unavailable or failed, using LLM")
            
            # Step 4: LLM fallback (more reliable)
            llm_result = self._extract_with_llm()
            
            if llm_result['success']:
                self.logger.info("✓ LLM extraction successful")
                return llm_result
            
            # Step 5: No temporal comparison detected
            return self._failure_response("No temporal comparison detected")
            
        except Exception as e:
            self.logger.error(f"[EXTRACTOR_DEBUG] Exception in extract(): {e}")
            self.logger.error(f"[EXTRACTOR_DEBUG] Exception type: {type(e).__name__}")
            import traceback
            self.logger.error(f"[EXTRACTOR_DEBUG] Full traceback:")
            self.logger.error(traceback.format_exc())
            return self._failure_response(str(e))
    
    def _try_ner_bert(self) -> Optional[Dict[str, Any]]:
        """
        Try NER BERT for entity extraction
        Returns None if NER unavailable or fails
        """
        try:
            # Try to load NER model
            from transformers import AutoTokenizer, AutoModelForTokenClassification
            from transformers import pipeline
            
            # Load event/period NER model
            model_path = "event-period-ner-bert"
            
            tokenizer = AutoTokenizer.from_pretrained(model_path)
            model = AutoModelForTokenClassification.from_pretrained(model_path)
            
            nlp = pipeline("ner", model=model, tokenizer=tokenizer, aggregation_strategy="simple")
            
            # Run NER on query
            ner_output = nlp(self.query)
            
            # Calculate average confidence
            if ner_output:
                avg_confidence = sum(e['score'] for e in ner_output) / len(ner_output)
            else:
                avg_confidence = 0.0
            
            return {
                'success': True,
                'events': ner_output,
                'confidence': avg_confidence,
                'method': 'ner_bert'
            }
            
        except Exception as e:
            self.logger.warning(f"NER BERT failed: {e}")
            return None
    
    def _validate_ner_output(self, ner_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate NER output - CRITICAL even when confident
        
        Common NER failures:
        1. Empty events list
        2. Hallucinated periods (not in data)
        3. Wrong granularity
        4. Garbled output (random strings)
        """
        events = ner_result.get('events', [])
        
        # Check 1: Events list not empty
        if not events or len(events) == 0:
            return {'is_valid': False, 'reason': 'NER returned empty events list'}
        
        # Check 2: Events contain actual time periods (not garbage)
        valid_time_words = self._get_valid_time_words()
        
        has_valid_period = False
        extracted_periods = []
        
        for event in events:
            event_text = str(event.get('word', '') or event.get('text', '')).lower()
            
            # Check if contains any valid time word
            if any(word in event_text for word in valid_time_words):
                has_valid_period = True
                extracted_periods.append(event_text)
        
        if not has_valid_period:
            return {'is_valid': False, 'reason': 'NER events do not contain valid time periods'}
        
        # Check 3: Extracted periods exist in actual data
        for period_text in extracted_periods:
            if not self._period_exists_in_data(period_text):
                return {
                    'is_valid': False,
                    'reason': f'NER period "{period_text}" not found in data range '
                             f'({self.date_analysis["min_date"].date()} to {self.date_analysis["max_date"].date()})'
                }
        
        # Check 4: Granularity matches data
        ner_granularity = self._infer_granularity_from_periods(extracted_periods)
        data_granularity = self.date_analysis['granularity']
        
        if ner_granularity != data_granularity and ner_granularity != 'unknown':
            return {
                'is_valid': False,
                'reason': f'Granularity mismatch: NER detected {ner_granularity}, data is {data_granularity}'
            }
        
        # All checks passed
        return {
            'is_valid': True,
            'events': events,
            'extracted_periods': extracted_periods
        }
    
    def _get_valid_time_words(self) -> set:
        """
        Build valid time words from actual data (NO HARDCODING)
        """
        valid_words = set()
        
        # Add years from data
        for year in self.date_analysis['years']:
            valid_words.add(str(year))
        
        # Add month names/numbers if data has months
        if 'months_by_year' in self.date_analysis:
            month_names = ['january', 'february', 'march', 'april', 'may', 'june',
                          'july', 'august', 'september', 'october', 'november', 'december']
            valid_words.update(month_names)
            valid_words.update(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 
                               'jul', 'aug', 'sep', 'oct', 'nov', 'dec'])
            
            # Add month numbers
            for months in self.date_analysis['months_by_year'].values():
                for month in months:
                    valid_words.add(f"month {month}")
        
        # Add generic temporal words
        valid_words.update(['week', 'month', 'quarter', 'year', 'q1', 'q2', 'q3', 'q4'])
        
        # Add relative terms
        valid_words.update(['last', 'previous', 'current', 'this'])
        
        return valid_words
    
    def _period_exists_in_data(self, period_text: str) -> bool:
        """
        Check if extracted period exists in actual data
        """
        period_lower = period_text.lower()
        
        # Month names (if in data)
        month_map = {
            'january': 1, 'jan': 1, 'february': 2, 'feb': 2, 'march': 3, 'mar': 3,
            'april': 4, 'apr': 4, 'may': 5, 'june': 6, 'jun': 6,
            'july': 7, 'jul': 7, 'august': 8, 'aug': 8, 'september': 9, 'sep': 9,
            'october': 10, 'oct': 10, 'november': 11, 'nov': 11, 'december': 12, 'dec': 12
        }
        
        for month_name, month_num in month_map.items():
            if month_name in period_lower:
                # Check if this month exists in any year
                if 'months_by_year' in self.date_analysis:
                    for months in self.date_analysis['months_by_year'].values():
                        if month_num in months:
                            return True
                return False
        
        # Year (if mentioned)
        for year in self.date_analysis['years']:
            if str(year) in period_text:
                return True
        
        # Week numbers (if in data)
        week_match = re.search(r'week\s*(\d+)', period_lower)
        if week_match:
            week_num = int(week_match.group(1))
            if 'weeks_by_year' in self.date_analysis:
                for weeks in self.date_analysis['weeks_by_year'].values():
                    if week_num in weeks:
                        return True
                return False
        
        # Generic temporal terms are always valid
        if any(word in period_lower for word in ['last', 'previous', 'current', 'this', 'month', 'quarter']):
            return True
        
        # If we can't determine, assume valid (LLM will validate later)
        return True
    
    def _infer_granularity_from_periods(self, periods: List[str]) -> str:
        """
        Infer query granularity from extracted periods
        """
        periods_text = ' '.join(periods).lower()
        
        if 'week' in periods_text:
            return 'weekly'
        elif any(month in periods_text for month in ['january', 'february', 'march', 'april', 
                                                       'may', 'june', 'july', 'august', 
                                                       'september', 'october', 'november', 'december']):
            return 'monthly'
        elif any(q in periods_text for q in ['q1', 'q2', 'q3', 'q4', 'quarter']):
            return 'quarterly'
        elif any(str(year) in periods_text for year in self.date_analysis['years']):
            # If only year mentioned, likely yearly
            if not any(month in periods_text for month in ['month', 'week', 'quarter']):
                return 'yearly'
        
        return 'unknown'
    
    def _process_validated_ner(self, validated: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process validated NER output into standard format
        """
        extracted_periods = validated['extracted_periods']
        
        # Determine if explicit comparison or sequential
        if len(extracted_periods) >= 2:
            comparison_type = 'explicit'
            period1_text = extracted_periods[0]
            period2_text = extracted_periods[1]
        elif len(extracted_periods) == 1:
            comparison_type = 'sequential'
            period2_text = extracted_periods[0]
            period1_text = None  # Will be inferred as previous period
        else:
            return self._failure_response("Could not identify periods from NER output")
        
        # Build response - only include period1 for explicit comparisons
        response = {
            'success': True,
            'has_comparison': True,
            'period2': {'text': period2_text, 'type': self.date_analysis['granularity']},
            'comparison_type': comparison_type,
            'confidence': 0.8,
            'method': 'ner_validated',
            'date_analysis': self.date_analysis  # Pass date analysis to validator
        }
        
        # Only add period1 for explicit comparisons (validator will infer for sequential)
        if comparison_type == 'explicit':
            response['period1'] = {'text': period1_text, 'type': self.date_analysis['granularity']}
        
        return response
    
    def _extract_with_llm(self) -> Dict[str, Any]:
        """
        LLM-based extraction - more reliable than NER
        Uses actual data statistics for context
        """
        # Build correction context if this is a retry
        correction_context = ""
        if self.retry_corrections:
            correction_context = f"""
⚠️ RETRY ATTEMPT - Previous extraction failed with these issues:
{chr(10).join([f"- {correction}" for correction in self.retry_corrections])}

Please fix these issues in your extraction.
"""
        
        prompt = f"""
Extract temporal comparison from query. Be conservative - only extract if clearly asking for comparison.

QUERY: "{self.query}"

DATA INFO (use this for validation):
- Date range: {self.date_analysis['min_date'].date()} to {self.date_analysis['max_date'].date()}
- Granularity: {self.date_analysis['granularity']}
- Available years: {self.date_analysis['years']}
- Latest year: {self.date_analysis['latest_year']}
- Records: {self.date_analysis['total_records']}

{correction_context}

RULES:
1. Only extract if query clearly asks "why spike/dip in [PERIOD]" or "from [P1] to [P2]"
2. If year not mentioned, use latest year: {self.date_analysis['latest_year']}
3. For single period ("spike in March"), set comparison_type="sequential" and OMIT period1
4. For two periods ("from June to July"), set comparison_type="explicit" and include both periods
5. Validate extracted periods exist in data range
6. Match query granularity with data granularity
7. Return empty if NOT a temporal comparison query

EXAMPLES OF TEMPORAL COMPARISON:
✓ "why spike in March?" → comparison_type="sequential", period2="March 2025", period1=OMIT
✓ "dip from June to July?" → comparison_type="explicit", period1="June 2025", period2="July 2025"
✓ "why spike in week 3?" → comparison_type="sequential", period2="week 3", period1=OMIT
✓ "what caused increase in Q2?" → comparison_type="sequential", period2="Q2", period1=OMIT

EXAMPLES OF NOT TEMPORAL COMPARISON:
✗ "what affects tickets?" → general causal (not specific period)
✗ "show trends over time" → general trends (not specific change)
✗ "monthly analysis" → general temporal (not specific comparison)

Return JSON:
{{
    "is_temporal_comparison": true/false,
    "period1": {{   // ONLY INCLUDE FOR comparison_type="explicit"
        "text": "period description (e.g., 'June 2025')",
        "year": year_number,
        "month": month_number or null,
        "week": week_number or null,
        "quarter": quarter_number or null,
        "type": "month|week|quarter|year"
    }},
    "period2": {{   // ALWAYS INCLUDE (the target period)
        "text": "period description (e.g., 'March 2025')",
        "year": year_number,
        "month": month_number or null,
        "week": week_number or null,
        "quarter": quarter_number or null,
        "type": "month|week|quarter|year"
    }},
    "comparison_type": "explicit|sequential|none",
    "confidence": 0.0-1.0,
    "reasoning": "why this is/isn't a temporal comparison",
    "query_granularity": "daily|weekly|monthly|quarterly|yearly",
    "granularity_matches_data": true/false
}}
"""
        
        try:
            response = self.llm_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.0
            )
            
            result = json.loads(response.choices[0].message.content)
            
            # Validate LLM response
            if result.get('is_temporal_comparison') and result.get('confidence', 0) > 0.7:
                # Check granularity match
                if not result.get('granularity_matches_data', True):
                    return {
                        'success': False,
                        'has_comparison': False,
                        'reason': f"Granularity mismatch: Query asks for {result.get('query_granularity')}, "
                                 f"but data is {self.date_analysis['granularity']}",
                        'error_type': 'GRANULARITY_MISMATCH'
                    }
                
                # Build response - only include period1 for explicit comparisons
                response = {
                    'success': True,
                    'has_comparison': True,
                    'period2': result['period2'],
                    'comparison_type': result['comparison_type'],
                    'confidence': result['confidence'],
                    'reasoning': result['reasoning'],
                    'method': 'llm_extracted',
                    'date_analysis': self.date_analysis  # Pass date analysis to validator
                }
                
                # Only add period1 for explicit comparisons (validator will infer for sequential)
                if result.get('comparison_type') == 'explicit' and 'period1' in result:
                    response['period1'] = result['period1']
                
                return response
            else:
                return {
                    'success': False,
                    'has_comparison': False,
                    'reason': result.get('reasoning', 'Not a temporal comparison'),
                    'confidence': result.get('confidence', 0.0)
                }
        
        except Exception as e:
            self.logger.error(f"LLM extraction failed: {e}")
            return {
                'success': False,
                'has_comparison': False,
                'reason': f"LLM extraction error: {str(e)}"
            }
    
    def _failure_response(self, reason: str) -> Dict[str, Any]:
        """
        Standard failure response - allows fallback to old flow
        """
        return {
            'success': False,
            'has_comparison': False,
            'reason': reason
        }

