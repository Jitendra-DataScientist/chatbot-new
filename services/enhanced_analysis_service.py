"""
Enhanced Analysis Service
Handles computational analysis before LLM interpretation to reduce hallucinations.
Implements two-stage approach: compute first, then interpret.
"""

import re
import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
import logging
import sys
import os
import json

# Add parent directory to path for master logger import
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from master_logger import setup_module_logger

from services.data_processor import TableauDataProcessor
from services.NL_to_python import NLToPythonGenerator


class EnhancedAnalysisService:
    """
    Service for computational analysis before LLM interpretation.
    Eliminates LLM hallucinations by providing concrete computed results.
    """
    
    def __init__(self, data_processor: TableauDataProcessor, nl_to_python: NLToPythonGenerator):
        self.data_processor = data_processor
        self.nl_to_python = nl_to_python
        self.logger = setup_module_logger('services.enhanced_analysis_service')
        
    async def get_computational_results(self, query: str, data: pd.DataFrame, chart_context: Dict = None) -> Dict[str, Any]:
        """
        Stage 1: Get concrete computational results using existing infrastructure.
        
        Args:
            query: User's natural language query
            data: DataFrame to analyze
            chart_context: Chart context with domain info and features
            
        Returns:
            Dictionary with computed results, no LLM interpretation
        """
        self.logger.info("=== STARTING COMPUTATIONAL ANALYSIS ===")
        self.logger.info(f"Query: '{query}'")
        self.logger.info(f"Data shape: {data.shape}")
        
        try:
            # Step 1: Detect query patterns (inspired by reference code)
            query_patterns = self._detect_query_patterns(query)
            self.logger.info(f"Detected patterns: {query_patterns}")
            
            # Step 2: Execute pandas aggregation using existing infrastructure
            pandas_result = self.data_processor.execute_pandas_aggregation(query, data)
            self.logger.info(f"Pandas execution status: {pandas_result.get('execution_status')}")
            
            # Step 3: Execute targeted analysis based on patterns and chart context
            targeted_analysis = self._execute_targeted_analysis(query, data, chart_context, query_patterns)
            
            # Step 4: Compute statistics and rankings
            computed_statistics = self._compute_key_statistics(data, chart_context)
            
            # Step 5: Create structured pivot data
            pivot_data = self._create_structured_pivot_data(data, chart_context, query_patterns)
            
            computational_results = {
                'pandas_execution': pandas_result,
                'query_patterns': query_patterns,
                'targeted_analysis': targeted_analysis,
                'computed_statistics': computed_statistics,
                'pivot_data': pivot_data,
                'success': True,
                'timestamp': datetime.now().isoformat()
            }
            print(json.dumps(computational_results, indent=4))
            self.logger.info("=== COMPUTATIONAL ANALYSIS COMPLETED ===")
            return computational_results
            
        except Exception as e:
            self.logger.error(f"Computational analysis failed: {e}")
            # Fallback to basic pandas execution
            return {
                'pandas_execution': self.data_processor.execute_pandas_aggregation(query, data),
                'query_patterns': {'is_analysis_query': False},
                'success': False,
                'error': str(e),
                'fallback': True
            }
    
    def _detect_query_patterns(self, query: str) -> Dict[str, Any]:
        """
        Detect specific analytical patterns in the query.
        Based on reference code's detect_period_analysis function.
        Enhanced to support transition queries (from X to Y) with robust regex patterns.
        """
        query_lower = query.lower()
        
        # Time period patterns
        period_patterns = {
            'month': r'\b(january|february|march|april|may|june|july|august|september|october|november|december|\d{4}-\d{2}|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b',
            'quarter': r'\b(q[1-4]|quarter [1-4]|\d{4}[-\s]?q[1-4])\b',
            'year': r'\b(\d{4})\b',
            'week': r'\b(week \d+|\d{4}-w\d+|w\d+ \d{4})\b',
            'date': r'\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b'
        }
        
        # Sentiment detection for transitions
        is_spike = any(word in query_lower for word in ['spike', 'surge', 'jump', 'increase', 'growth', 'rise', 'up'])
        is_dip = any(word in query_lower for word in ['dip', 'drop', 'decline', 'decrease', 'fall', 'down', 'slump'])
        
        # Sentiment detection for single periods
        is_high = any(word in query_lower for word in ['high', 'peak', 'spike', 'increase', 'growth', 'up', 'rise', 'surge'])
        is_low = any(word in query_lower for word in ['low', 'drop', 'decline', 'decrease', 'fall', 'down', 'dip', 'slump'])
        
        # Extract time periods
        found_periods = {}
        extracted_values = []
        for period_type, pattern in period_patterns.items():
            matches = re.findall(pattern, query_lower)
            if matches:
                found_periods[period_type] = matches
                extracted_values.extend(matches)
        
        # ROBUST TRANSITION DETECTION using named capture groups (based on reference patterns)
        transitions = self._extract_transitions_robust(query_lower, is_spike, is_dip)
        has_transitions = bool(transitions)
        
        # Analysis type detection
        is_why_analysis = any(word in query_lower for word in ['why', 'what caused', 'what drove', 'reason'])
        is_top_analysis = 'top' in query_lower and any(word in query_lower for word in ['contributors', 'factors', 'drivers'])
        is_comparison = any(word in query_lower for word in ['compare', 'vs', 'versus', 'mom', 'yoy', 'qoq'])
        
        return {
            'is_analysis_query': bool(found_periods and (is_high or is_low or has_transitions)) or is_why_analysis,
            'is_time_period_query': bool(found_periods) and not has_transitions,
            'is_transition_query': has_transitions,
            'transitions': transitions,
            'is_why_analysis': is_why_analysis,
            'is_top_analysis': is_top_analysis,
            'is_comparison': is_comparison,
            'sentiment': 'high' if is_high else 'low' if is_low else 'neutral',
            'periods': found_periods,
            'extracted_values': extracted_values,
            'needs_mom': any(p in found_periods for p in ['month', 'date']),
            'needs_qoq': any(p in found_periods for p in ['quarter', 'month', 'date']),
            'needs_yoy': bool(found_periods)
        }
    
    def _detect_sentiment_in_text(self, text: str) -> str:
        """
        Reusable sentiment detection for any text snippet.
        Finds the sentiment word CLOSEST to the end of the text (most relevant).
        
        Args:
            text: Text snippet to analyze for sentiment
            
        Returns:
            'spike', 'dip', or 'change'
        """
        spike_words = ['spike', 'surge', 'jump', 'increase', 'growth', 'rise', 'up']
        dip_words = ['dip', 'drop', 'decline', 'decrease', 'fall', 'down', 'slump']
        
        text_lower = text.lower()
        
        # Find the RIGHTMOST (closest to end) position of each sentiment type
        # This finds the sentiment word closest to the transition phrase
        spike_positions = [text_lower.rfind(word) for word in spike_words if word in text_lower]
        dip_positions = [text_lower.rfind(word) for word in dip_words if word in text_lower]
        
        closest_spike = max(spike_positions) if spike_positions else -1
        closest_dip = max(dip_positions) if dip_positions else -1
        
        if closest_spike > closest_dip:
            return 'spike'
        elif closest_dip > closest_spike:
            return 'dip'
        else:
            return 'change'
    
    def _extract_transitions_robust(self, query_lower: str, is_spike: bool, is_dip: bool) -> List[Dict]:
        """
        Robustly extract ALL transition periods from a query using two-step approach.
        
        Step 1: Extract all explicit "from/between X to/and Y" pairs
        Step 2: Handle trailing "till/until/to Z" that refer back to previous period
        
        Handles cases like:
        - "from June to July" → 1 transition
        - "from June to July, then a dip till August" → 2 transitions (June→July, July→August)
        - "from June to July, then from August to December" → 2 transitions
        
        Based on reference code's two-step extraction approach.
        
        Args:
            query_lower: Lowercase query string
            is_spike: Whether query indicates a spike
            is_dip: Whether query indicates a dip
            
        Returns:
            List of transition dictionaries with 'from', 'to', 'sentiment'
        """
        transitions = []
        
        # Month regex with optional year
        month_re = (
            r'(?:january|february|march|april|may|june|july|august|september|october|november|december|'
            r'jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?:\s+\d{4})?'
        )
        
        # STEP 1: Extract all explicit "from/between X to/and Y" pairs
        # This captures ALL occurrences, not just the first one
        explicit_pattern = re.compile(
            rf'\b(?:from|between)\s+'
            rf'(?P<from>{month_re})\s+'
            rf'(?:to|till|until|and|through)\s+'
            rf'(?P<to>{month_re})',
            flags=re.IGNORECASE
        )
        
        for match in explicit_pattern.finditer(query_lower):
            # Extract local context for per-transition sentiment detection
            # Look BEFORE the match for sentiment words (not after, to avoid next transition's sentiment)
            match_start = match.start()
            match_end = match.end()
            context_start = max(0, match_start - 40)  # 40 chars before to catch sentiment
            context_end = match_end  # Don't include text after (avoids next transition's sentiment)
            context = query_lower[context_start:context_end]
            
            # Detect sentiment in local context (reusing existing logic)
            sentiment = self._detect_sentiment_in_text(context)
            
            transitions.append({
                'from': match.group('from').strip(),
                'to': match.group('to').strip(),
                'sentiment': sentiment  # Per-transition sentiment!
            })
        
        # STEP 2: Handle trailing "till/until/through/to X" phrases
        # These implicitly refer back to the last mentioned period
        # Example: "from June to July, then till August" → adds July→August
        if transitions:
            till_pattern = re.compile(
                rf'\b(?:till|until|through|to)\s+(?P<to>{month_re})',
                flags=re.IGNORECASE
            )
            
            last_to = transitions[-1]['to']
            
            for match in till_pattern.finditer(query_lower):
                next_to = match.group('to').strip()
                # Only add if it's a new period (not already captured)
                if next_to.lower() != last_to.lower():
                    # Check if this "to X" is part of an already-captured explicit transition
                    already_captured = any(
                        t['to'].lower() == next_to.lower() 
                        for t in transitions
                    )
                    if not already_captured:
                        # Extract local context for sentiment detection (look before, not after)
                        match_start = match.start()
                        match_end = match.end()
                        context_start = max(0, match_start - 40)
                        context_end = match_end
                        context = query_lower[context_start:context_end]
                        
                        # Detect sentiment in local context
                        sentiment = self._detect_sentiment_in_text(context)
                        
                        transitions.append({
                            'from': last_to,
                            'to': next_to,
                            'sentiment': sentiment  # Per-transition sentiment!
                        })
                        last_to = next_to
        
        # FALLBACK: If no transitions found with explicit "from/between" keywords,
        # try simple "Month to Month" pattern (without from/between)
        if not transitions:
            simple_pattern = re.compile(
                rf'\b(?P<from>{month_re})\s+'
                rf'(?:to|till|until|through)\s+'
                rf'(?P<to>{month_re})',
                flags=re.IGNORECASE
            )
            
            for match in simple_pattern.finditer(query_lower):
                # Extract local context for sentiment (look before match)
                match_start = match.start()
                match_end = match.end()
                context_start = max(0, match_start - 40)
                context_end = match_end
                context = query_lower[context_start:context_end]
                
                sentiment = self._detect_sentiment_in_text(context)
                
                transitions.append({
                    'from': match.group('from').strip(),
                    'to': match.group('to').strip(),
                    'sentiment': sentiment
                })
        
        # Handle Quarter-based transitions if no month transitions found
        if not transitions:
            quarter_pattern = re.compile(
                r'\b(?:from|between)?\s*'
                r'(?P<from>q[1-4](?:\s+\d{4})?)\s+'
                r'(?:to|till|until|and|through)\s+'
                r'(?P<to>q[1-4](?:\s+\d{4})?)',
                flags=re.IGNORECASE
            )
            
            for match in quarter_pattern.finditer(query_lower):
                # Extract local context for sentiment (look before match)
                match_start = match.start()
                match_end = match.end()
                context_start = max(0, match_start - 40)
                context_end = match_end
                context = query_lower[context_start:context_end]
                
                sentiment = self._detect_sentiment_in_text(context)
                
                transitions.append({
                    'from': match.group('from').strip(),
                    'to': match.group('to').strip(),
                    'sentiment': sentiment
                })
        
        # Handle Year-based transitions if no other transitions found
        if not transitions:
            year_pattern = re.compile(
                r'\b(?:from|between)?\s*'
                r'(?P<from>\d{4})\s+'
                r'(?:to|till|until|and|through)\s+'
                r'(?P<to>\d{4})',
                flags=re.IGNORECASE
            )
            
            for match in year_pattern.finditer(query_lower):
                # Extract local context for sentiment (look before match)
                match_start = match.start()
                match_end = match.end()
                context_start = max(0, match_start - 40)
                context_end = match_end
                context = query_lower[context_start:context_end]
                
                sentiment = self._detect_sentiment_in_text(context)
                
                transitions.append({
                    'from': match.group('from').strip(),
                    'to': match.group('to').strip(),
                    'sentiment': sentiment
                })
        
        self.logger.info(f"[TRANSITION_EXTRACTION] Query: '{query_lower}'")
        self.logger.info(f"[TRANSITION_EXTRACTION] Extracted {len(transitions)} transition(s): {transitions}")
        
        return transitions
    
    def _extract_target_period(self, query: str, query_patterns: Dict) -> Optional[str]:
        """
        Extract specific target period from query.
        Converts natural language periods to standardized format.
        
        Args:
            query: User query like "why was March high?"
            query_patterns: Detected patterns from _detect_query_patterns
            
        Returns:
            Standardized period string like "March" or "2025-03" or None
        """
        try:
            self.logger.info(f"Extracting target period from query: '{query}'")
            
            # Method 1: Use detected patterns from query_patterns
            periods = query_patterns.get('periods', {})
            extracted_values = query_patterns.get('extracted_values', [])
            
            self.logger.debug(f"Detected periods from patterns: {periods}")
            self.logger.debug(f"Extracted values: {extracted_values}")
            
            # Priority: month > quarter > year > date
            if 'month' in periods and periods['month']:
                month_value = periods['month'][0]  # Take first match
                self.logger.info(f"Found month from patterns: {month_value}")
                
                # Convert common abbreviations to full names
                month_mapping = {
                    'jan': 'January', 'feb': 'February', 'mar': 'March', 'apr': 'April',
                    'may': 'May', 'jun': 'June', 'jul': 'July', 'aug': 'August',
                    'sep': 'September', 'oct': 'October', 'nov': 'November', 'dec': 'December',
                    'january': 'January', 'february': 'February', 'march': 'March', 'april': 'April',
                    'june': 'June', 'july': 'July', 'august': 'August', 'september': 'September',
                    'october': 'October', 'november': 'November', 'december': 'December'
                }
                result = month_mapping.get(month_value.lower(), month_value.title())
                self.logger.info(f"Mapped month to: {result}")
                return result
            
            elif 'quarter' in periods and periods['quarter']:
                result = periods['quarter'][0].upper()  # Q1, Q2, etc.
                self.logger.info(f"Found quarter: {result}")
                return result
            
            elif 'year' in periods and periods['year']:
                result = periods['year'][0]
                self.logger.info(f"Found year: {result}")
                return result
            
            elif 'date' in periods and periods['date']:
                result = periods['date'][0]
                self.logger.info(f"Found date: {result}")
                return result
            
            # Method 2: Fallback - direct extraction from query text
            self.logger.info("No periods found in patterns, trying direct extraction from query")
            return self._extract_period_from_query_text(query)
            
        except Exception as e:
            self.logger.error(f"Error extracting target period: {e}")
            return None
    
    def _extract_period_from_query_text(self, query: str) -> Optional[str]:
        """
        Fallback method to extract period directly from query text.
        
        Args:
            query: User query text
            
        Returns:
            Period string or None
        """
        try:
            query_lower = query.lower()
            self.logger.debug(f"Direct extraction from query: '{query_lower}'")
            
            # Month names mapping for direct extraction
            month_mapping = {
                'january': 'January', 'february': 'February', 'march': 'March', 'april': 'April',
                'may': 'May', 'june': 'June', 'july': 'July', 'august': 'August',
                'september': 'September', 'october': 'October', 'november': 'November', 'december': 'December',
                'jan': 'January', 'feb': 'February', 'mar': 'March', 'apr': 'April',
                'jun': 'June', 'jul': 'July', 'aug': 'August', 'sep': 'September',
                'oct': 'October', 'nov': 'November', 'dec': 'December'
            }
            
            # Check for month names in the query
            for month_key, month_name in month_mapping.items():
                if month_key in query_lower:
                    self.logger.info(f"Direct extraction found month: {month_name}")
                    return month_name
            
            # Check for quarters
            import re
            quarter_match = re.search(r'\b(q[1-4]|quarter [1-4])\b', query_lower)
            if quarter_match:
                result = quarter_match.group(1).upper()
                self.logger.info(f"Direct extraction found quarter: {result}")
                return result
            
            # Check for years
            year_match = re.search(r'\b(20[0-9]{2})\b', query_lower)
            if year_match:
                result = year_match.group(1)
                self.logger.info(f"Direct extraction found year: {result}")
                return result
            
            self.logger.warning("No period found in direct extraction")
            return None
            
        except Exception as e:
            self.logger.error(f"Error in direct period extraction: {e}")
            return None
    
    def _extract_transition_periods(self, query: str, transitions: List[Dict], data: pd.DataFrame) -> List[Dict]:
        """
        Extract transition periods with year inference.
        Handles year inference from data when not explicitly specified.
        
        Args:
            query: User query
            transitions: List of transition dicts with 'from' and 'to' keys
            data: DataFrame to infer year context from
            
        Returns:
            List of dicts with parsed periods: [{'from': (period, year), 'to': (period, year), 'sentiment': ...}]
        """
        try:
            self.logger.info(f"Extracting transition periods from query: '{query}'")
            
            # Get all years mentioned in query
            query_years = re.findall(r'\b(20[0-9]{2})\b', query)
            self.logger.debug(f"Years found in query: {query_years}")
            
            # Infer default year from data if not specified
            default_year = self._infer_year_from_data(data)
            self.logger.info(f"Default year inferred from data: {default_year}")
            
            result_periods = []
            
            for trans in transitions:
                from_period_raw = trans['from']
                to_period_raw = trans['to']
                sentiment = trans.get('sentiment', 'change')
                
                self.logger.debug(f"Processing transition: {from_period_raw} to {to_period_raw}")
                
                # Parse from_period with year
                from_period, from_year = self._parse_period_with_year(from_period_raw, query_years, default_year)
                
                # Parse to_period with year
                to_period, to_year = self._parse_period_with_year(to_period_raw, query_years, default_year)
                
                # Handle consecutive months assumption (e.g., "June to July" assumes same year)
                if not query_years and from_period and to_period:
                    # Both should use default year for consecutive months
                    to_year = from_year
                    
                    # Check if it's cross-year (e.g., December to January)
                    from_month_num = self._month_name_to_number(from_period)
                    to_month_num = self._month_name_to_number(to_period)
                    
                    if from_month_num and to_month_num and to_month_num < from_month_num:
                        # Crossing year boundary (Dec to Jan)
                        to_year = str(int(from_year) + 1)
                        self.logger.info(f"Detected cross-year transition: {from_period} {from_year} to {to_period} {to_year}")
                
                result_periods.append({
                    'from': from_period,
                    'from_year': from_year,
                    'to': to_period,
                    'to_year': to_year,
                    'sentiment': sentiment,
                    'year_assumption': f"Using year {from_year} for {from_period} and {to_year} for {to_period}" + 
                                     (" (inferred from data)" if not query_years else " (from query)")
                })
                
                self.logger.info(f"Extracted transition: {from_period} {from_year} → {to_period} {to_year} (sentiment: {sentiment})")
            
            return result_periods
            
        except Exception as e:
            self.logger.error(f"Error extracting transition periods: {e}")
            return []
    
    def _parse_period_with_year(self, period_str: str, query_years: List[str], default_year: int) -> Tuple[str, str]:
        """
        Parse a period string and extract/infer year.
        
        Args:
            period_str: Period like "june", "2025-06", "june 2025"
            query_years: Years found in the query
            default_year: Default year to use if not found
            
        Returns:
            Tuple of (period_name, year_string)
        """
        try:
            period_str_lower = period_str.lower().strip()
            
            # Check if year is embedded in the period string
            year_in_period = re.search(r'\b(20[0-9]{2})\b', period_str)
            if year_in_period:
                year = year_in_period.group(1)
                # Remove year from period string
                period_clean = re.sub(r'\b20[0-9]{2}\b', '', period_str_lower).strip()
                period_clean = re.sub(r'[-\s]+', ' ', period_clean).strip()
                
                # Convert to standard format
                period_name = self._standardize_period_name(period_clean)
                return period_name, year
            
            # No year in period, use query year or default
            if query_years:
                year = query_years[0]  # Use first mentioned year
            else:
                year = str(default_year)
            
            period_name = self._standardize_period_name(period_str_lower)
            return period_name, year
            
        except Exception as e:
            self.logger.error(f"Error parsing period with year: {e}")
            return period_str, str(default_year)
    
    def _standardize_period_name(self, period_str: str) -> str:
        """
        Standardize period name to title case.
        
        Args:
            period_str: Period like "june", "jan", "q1"
            
        Returns:
            Standardized period name like "June", "Q1"
        """
        period_str = period_str.strip().lower()
        
        # Month name mapping
        month_mapping = {
            'jan': 'January', 'feb': 'February', 'mar': 'March', 'apr': 'April',
            'may': 'May', 'jun': 'June', 'jul': 'July', 'aug': 'August',
            'sep': 'September', 'oct': 'October', 'nov': 'November', 'dec': 'December',
            'january': 'January', 'february': 'February', 'march': 'March', 'april': 'April',
            'june': 'June', 'july': 'July', 'august': 'August', 'september': 'September',
            'october': 'October', 'november': 'November', 'december': 'December'
        }
        
        if period_str in month_mapping:
            return month_mapping[period_str]
        
        # Quarter handling
        if period_str.startswith('q') and len(period_str) == 2:
            return period_str.upper()
        
        # Default to title case
        return period_str.title()
    
    def _parse_period_to_pandas_period(self, period_str: str, data: pd.DataFrame) -> Optional[pd.Period]:
        """
        Convert period string to pandas Period object for arithmetic.
        
        Args:
            period_str: Period like "March", "Q1", "2024"
            data: DataFrame to infer year context from
            
        Returns:
            pandas Period object or None
        """
        try:
            if not period_str:
                return None
            
            # For month names, we need to infer the year
            month_names = ['January', 'February', 'March', 'April', 'May', 'June',
                          'July', 'August', 'September', 'October', 'November', 'December']
            
            if period_str in month_names:
                # Try to infer year from data
                year = self._infer_year_from_data(data)
                month_num = month_names.index(period_str) + 1
                return pd.Period(f"{year}-{month_num:02d}", freq='M')
            
            # For quarters like "Q1"
            if period_str.startswith('Q') and len(period_str) == 2:
                year = self._infer_year_from_data(data)
                return pd.Period(f"{year}-{period_str}", freq='Q')
            
            # For years
            if period_str.isdigit() and len(period_str) == 4:
                return pd.Period(period_str, freq='Y')
            
            # For explicit formats like "2025-03"
            if '-' in period_str:
                return pd.Period(period_str, freq='M')
            
            return None
            
        except Exception as e:
            self.logger.error(f"Error parsing period to pandas Period: {e}")
            return None
    
    def _infer_year_from_data(self, data: pd.DataFrame) -> int:
        """
        Infer the most recent/relevant year from the data.
        
        Args:
            data: DataFrame with potential date columns
            
        Returns:
            Year as integer, defaults to current year
        """
        try:
            # Look for date columns
            date_columns = []
            for col in data.columns:
                if 'date' in col.lower() or 'time' in col.lower() or 'month' in col.lower():
                    date_columns.append(col)
            
            for col in date_columns:
                try:
                    # Try to parse dates and get the most recent year
                    dates = pd.to_datetime(data[col], errors='coerce').dropna()
                    if len(dates) > 0:
                        return dates.max().year
                except:
                    continue
            
            # Default to current year
            return datetime.now().year
            
        except Exception as e:
            self.logger.error(f"Error inferring year from data: {e}")
            return datetime.now().year
    
    def _create_period_based_pivot(self, data: pd.DataFrame, chart_context: Dict, target_period: str, query: str = "") -> Optional[pd.DataFrame]:
        """
        Create pivot table focused on the target period, similar to reference code's approach.
        
        Args:
            data: DataFrame to analyze
            chart_context: Chart context with axes and features
            target_period: Specific period like "March"
            query: Original user query (for smart aggregation decision)
            
        Returns:
            Pivot DataFrame with periods as index and categories as columns
        """
        try:
            self.logger.info(f"Creating pivot table for target period: {target_period}")
            
            if not chart_context:
                self.logger.error("No chart context provided for pivot table creation")
                return None
            
            x_axis = chart_context.get('x_axis_detected')
            y_axis = chart_context.get('y_axis_detected')
            top_features = chart_context.get('top_5_features', [])
            
            self.logger.info(f"Chart context - x_axis: {x_axis}, y_axis: {y_axis}, top_features: {top_features}")
            self.logger.info(f"Data columns available: {list(data.columns)}")
            
            if not x_axis or not y_axis:
                self.logger.error(f"Missing axes - x_axis: {x_axis}, y_axis: {y_axis}")
                return None
                
            if x_axis not in data.columns:
                self.logger.error(f"X-axis column '{x_axis}' not found in data columns: {list(data.columns)}")
                return None
                
            if y_axis not in data.columns:
                self.logger.error(f"Y-axis column '{y_axis}' not found in data columns: {list(data.columns)}")
                return None
            
            # Prepare data copy
            data_copy = data.copy()
            
            # Handle time-based grouping
            self.logger.info(f"Determining time column for period analysis")
            
            if 'create_month' in data_copy.columns:
                self.logger.info(f"Found 'create_month' column. Dtype: {data_copy['create_month'].dtype}")
                self.logger.info(f"Sample create_month values: {data_copy['create_month'].head().tolist()}")
                self.logger.info(f"Unique create_month values: {sorted(data_copy['create_month'].unique().tolist())}")
                
                # Convert to month names if needed
                if data_copy['create_month'].dtype != 'object':
                    self.logger.info(f"Converting numeric create_month to month names")
                    data_copy['create_month'] = pd.to_datetime(data_copy['create_month'], errors='coerce')
                    data_copy['month_name'] = data_copy['create_month'].dt.month_name()
                    time_col = 'month_name'
                    self.logger.info(f"Created month_name column. Sample values: {data_copy['month_name'].head().tolist()}")
                else:
                    time_col = 'create_month'
                    self.logger.info(f"Using create_month as-is (already object type)")
            elif 'month' in x_axis.lower():
                self.logger.info(f"Using x_axis '{x_axis}' as time column (contains 'month')")
                time_col = x_axis
            else:
                self.logger.info(f"Using x_axis '{x_axis}' as time column (fallback)")
                time_col = x_axis
            
            self.logger.info(f"Selected time column: {time_col}")
            
            # For period-specific analysis, we want to create a comprehensive pivot
            # that shows the target period in context with other periods
            
            # Find ALL valid features for breakdown (instead of just the first one)
            valid_breakdown_features = []
            for feature in top_features:
                if (feature in data_copy.columns and 
                    feature != time_col and 
                    feature != y_axis and
                    data_copy[feature].nunique() <= 100):  # Reasonable number of categories
                    valid_breakdown_features.append(feature)
            
            self.logger.info(f"Found {len(valid_breakdown_features)} valid breakdown features: {valid_breakdown_features}")
            
            if valid_breakdown_features:
                # Create a comprehensive analysis with multiple feature breakdowns
                multi_feature_analysis = {}
                
                for feature in valid_breakdown_features:
                    self.logger.info(f"Creating pivot table for feature: {feature}")
                    
                    # Use smart aggregation to decide how to aggregate y_axis
                    try:
                        if self.nl_to_python.smart_aggregation_decider:
                            agg_decision = self.nl_to_python.smart_aggregation_decider.decide_aggregation(
                                query=query if query else "aggregate data",
                                column=y_axis,
                                df=data_copy
                            )
                            agg_function = agg_decision['aggregation'].upper()
                            
                            # Map to pandas function
                            pandas_agg_map = {
                                'COUNT': 'count',
                                'COUNT_DISTINCT': 'nunique',
                                'SUM': 'sum',
                                'AVG': 'mean',
                                'MEDIAN': 'median',
                                'MIN': 'min',
                                'MAX': 'max'
                            }
                            pandas_agg = pandas_agg_map.get(agg_function, 'count')
                            
                            self.logger.info(f"[SMART_AGGREGATION] Decided: {agg_function} for '{y_axis}' (pandas: {pandas_agg})")
                            self.logger.debug(f"[SMART_AGGREGATION] Reasoning: {agg_decision['reasoning']}")
                            self.logger.debug(f"[SMART_AGGREGATION] Confidence: {agg_decision['confidence']}")
                        else:
                            # Fallback if smart aggregation not available
                            pandas_agg = 'count'
                            self.logger.warning(f"[SMART_AGGREGATION] Smart aggregation decider not available, falling back to 'count'")
                    except Exception as e:
                        # Fallback on error
                        pandas_agg = 'count'
                        self.logger.warning(f"[SMART_AGGREGATION] Error deciding aggregation: {e}, falling back to 'count'")
                    
                    # Create pivot: time_periods x breakdown_feature using smart aggregation
                    pivot_df = data_copy.groupby([time_col, feature])[y_axis].agg(pandas_agg).reset_index()
                    self.logger.info(f"Grouped data for {feature} shape: {pivot_df.shape}")
                    
                    # Create pivot table with the decided aggregation
                    pivot_table = pivot_df.pivot(index=time_col, columns=feature, values=y_axis).fillna(0)
                    self.logger.info(f"Pivot table for {feature} created. Shape: {pivot_table.shape}")
                    self.logger.info(f"Pivot table columns for {feature}: {list(pivot_table.columns)}")
                    
                    # Ensure our target period is included
                    month_order = ["January", "February", "March", "April", "May", "June",
                                  "July", "August", "September", "October", "November", "December"]
                    
                    if time_col in ['create_month', 'month_name'] and target_period in month_order:
                        # Reorder to have months in chronological order
                        available_months = [m for m in month_order if m in pivot_table.index]
                        if available_months:
                            pivot_table = pivot_table.reindex(available_months)
                    
                    # Store the pivot table for this feature
                    multi_feature_analysis[feature] = pivot_table
                
                self.logger.info(f"Created {len(multi_feature_analysis)} feature-specific pivot tables")
                
                # Return the primary feature's pivot table for backward compatibility
                # But store all features in a way we can access later
                primary_feature = valid_breakdown_features[0]
                primary_pivot = multi_feature_analysis[primary_feature]
                
                # Attach all feature analyses to the primary pivot for later access
                primary_pivot._multi_feature_data = multi_feature_analysis
                primary_pivot._valid_features = valid_breakdown_features
                
                self.logger.info(f"Returning primary pivot for {primary_feature}, with {len(valid_breakdown_features)} features stored")
                return primary_pivot
            else:
                # Fallback: simple time-based aggregation
                simple_agg = data_copy.groupby(time_col)[y_axis].agg(['count', 'sum', 'mean']).round(2)
                return simple_agg
            
        except Exception as e:
            self.logger.error(f"Error creating period-based pivot: {e}")
            return None
    
    def _calculate_mom_comparison(self, pivot_df: pd.DataFrame, target_period: pd.Period) -> Optional[pd.Series]:
        """
        Calculate Month-over-Month comparison for the target period.
        Works with timestamp-based pivot index.
        
        Args:
            pivot_df: Pivot table with timestamp periods as index
            target_period: Target period as pandas Period
            
        Returns:
            Series with MoM change values or None
        """
        try:
            if pivot_df is None or target_period is None:
                return None
            
            self.logger.info(f"Calculating MoM for target_period: {target_period}")
            self.logger.info(f"Available periods in pivot: {list(pivot_df.index)}")
            
            # Find actual index strings that match the target periods (handles format variations)
            target_timestamp = self._find_period_in_index(pivot_df, target_period)
            
            # Calculate previous month
            prev_period = target_period - 1
            prev_timestamp = self._find_period_in_index(pivot_df, prev_period)
            
            self.logger.info(f"Found current period: {target_timestamp}")
            self.logger.info(f"Found previous period: {prev_timestamp}")
            
            # Check if both periods exist in the data
            if target_timestamp is None:
                self.logger.warning(f"Target period {target_period} not found in pivot index")
                return None
                
            if prev_timestamp is None:
                self.logger.warning(f"Previous period {prev_period} not found in pivot index")
                return None
            
            # Get values for both periods
            current_values = pivot_df.loc[target_timestamp]
            prev_values = pivot_df.loc[prev_timestamp]
            
            self.logger.info(f"Current values shape: {current_values.shape}")
            self.logger.info(f"Previous values shape: {prev_values.shape}")
            
            # Calculate change
            mom_change = current_values - prev_values
            
            self.logger.info(f"MoM calculation successful. Sample changes: {dict(list(mom_change.head().items()))}")
            return mom_change
            
        except Exception as e:
            self.logger.error(f"Error calculating MoM comparison: {e}")
            return None

    def _period_to_timestamp_string(self, period: pd.Period) -> str:
        """
        Convert pandas Period to timestamp string format that matches pivot index.
        
        Args:
            period: pandas Period object
            
        Returns:
            Timestamp string like "2025-03-01 00:00:00"
        """
        return f"{period.year}-{period.month:02d}-01 00:00:00"

    def _find_period_in_index(self, pivot_df: pd.DataFrame, target_period: pd.Period) -> Optional[str]:
        """
        Find matching period in pivot index regardless of timestamp format.
        This handles variations like "2025-06-01 00:00:00" vs "2025-06-01 00:00:00.000"
        
        Args:
            pivot_df: Pivot table with timestamp periods as index
            target_period: Target period as pandas Period
            
        Returns:
            Actual index string that matches the target period, or None if not found
        """
        if pivot_df is None or target_period is None:
            return None
            
        target_year_month = (target_period.year, target_period.month)
        
        for idx_str in pivot_df.index:
            try:
                # Parse timestamp and extract year/month
                idx_timestamp = pd.to_datetime(str(idx_str))
                if (idx_timestamp.year, idx_timestamp.month) == target_year_month:
                    return str(idx_str)
            except Exception as e:
                self.logger.debug(f"Could not parse index entry '{idx_str}': {e}")
                continue
        return None

    def _calculate_qoq_comparison(self, pivot_df: pd.DataFrame, target_period: pd.Period) -> Optional[pd.Series]:
        """
        Calculate Quarter-over-Quarter comparison for the target period.
        Works with timestamp-based pivot index.
        
        Args:
            pivot_df: Pivot table with timestamp periods as index
            target_period: Target period as pandas Period
            
        Returns:
            Series with QoQ change values or None
        """
        try:
            if pivot_df is None or target_period is None:
                return None
            
            self.logger.info(f"Calculating QoQ for target_period: {target_period}")
            self.logger.info(f"Available periods in pivot: {list(pivot_df.index)}")
            
            # Find actual index strings that match the target periods (handles format variations)
            target_timestamp = self._find_period_in_index(pivot_df, target_period)
            
            # Calculate previous quarter (3 months ago)
            prev_quarter_period = target_period - 3
            prev_quarter_timestamp = self._find_period_in_index(pivot_df, prev_quarter_period)
            
            self.logger.info(f"Found current period: {target_timestamp}")
            self.logger.info(f"Found previous quarter period: {prev_quarter_timestamp}")
            
            # Check if both periods exist in the data
            if target_timestamp is None:
                self.logger.warning(f"Target period {target_period} not found in pivot index")
                return None
                
            if prev_quarter_timestamp is None:
                self.logger.warning(f"Previous quarter period {prev_quarter_period} not found in pivot index")
                return None
            
            # Get values for both periods
            current_values = pivot_df.loc[target_timestamp]
            prev_quarter_values = pivot_df.loc[prev_quarter_timestamp]
            
            self.logger.info(f"Current values shape: {current_values.shape}")
            self.logger.info(f"Previous quarter values shape: {prev_quarter_values.shape}")
            
            # Calculate change
            qoq_change = current_values - prev_quarter_values
            
            self.logger.info(f"QoQ calculation successful. Sample changes: {dict(list(qoq_change.head().items()))}")
            return qoq_change
            
        except Exception as e:
            self.logger.error(f"Error calculating QoQ comparison: {e}")
            return None
    
    def _calculate_yoy_comparison(self, pivot_df: pd.DataFrame, target_period: pd.Period) -> Optional[pd.Series]:
        """
        Calculate Year-over-Year comparison for the target period.
        Works with timestamp-based pivot index.
        
        Args:
            pivot_df: Pivot table with timestamp periods as index
            target_period: Target period as pandas Period
            
        Returns:
            Series with YoY change values or None
        """
        try:
            if pivot_df is None or target_period is None:
                return None
            
            self.logger.info(f"Calculating YoY for target_period: {target_period}")
            self.logger.info(f"Available periods in pivot: {list(pivot_df.index)}")
            
            # Find actual index strings that match the target periods (handles format variations)
            target_timestamp = self._find_period_in_index(pivot_df, target_period)
            
            # Calculate previous year (same month, previous year)
            prev_year_period = target_period - 12  # 12 months ago
            prev_year_timestamp = self._find_period_in_index(pivot_df, prev_year_period)
            
            self.logger.info(f"Found current period: {target_timestamp}")
            self.logger.info(f"Found previous year period: {prev_year_timestamp}")
            
            # Check if both periods exist in the data
            if target_timestamp is None:
                self.logger.warning(f"Target period {target_period} not found in pivot index")
                return None
                
            if prev_year_timestamp is None:
                self.logger.warning(f"Previous year period {prev_year_period} not found in pivot index")
                return None
            
            # Get values for both periods
            current_values = pivot_df.loc[target_timestamp]
            prev_year_values = pivot_df.loc[prev_year_timestamp]
            
            self.logger.info(f"Current values shape: {current_values.shape}")
            self.logger.info(f"Previous year values shape: {prev_year_values.shape}")
            
            # Calculate change
            yoy_change = current_values - prev_year_values
            
            self.logger.info(f"YoY calculation successful. Sample changes: {dict(list(yoy_change.head().items()))}")
            return yoy_change
            
        except Exception as e:
            self.logger.error(f"Error calculating YoY comparison: {e}")
            return None


    def _get_top_contributors_for_period(self, pivot_df: pd.DataFrame, target_period: str, sentiment: str) -> Dict:
        """
        Get top contributors specifically for the target period.
        
        Args:
            pivot_df: Pivot table with periods as index
            target_period: Target period string like "March"
            sentiment: "high" or "low" to determine sorting
            
        Returns:
            Dictionary with top contributors and their values
        """
        try:
            if pivot_df is None or target_period not in pivot_df.index:
                return {}
            
            # Get values for the target period only
            period_values = pivot_df.loc[target_period]
            
            # # Sort based on sentiment
            # if sentiment == 'low':
            #     # For "low" queries, show smallest contributors first
            #     sorted_values = period_values.sort_values(ascending=True)
            # else:
            #     # For "high" queries, show largest contributors first
            #     sorted_values = period_values.sort_values(ascending=False)

            # Always show largest contributors first, regardless of sentiment
            sorted_values = period_values.sort_values(ascending=False)

            # Get top 5 contributors
            top_5 = sorted_values.head(5)
            
            # Calculate percentages
            total = period_values.sum()
            top_contributors = {}
            
            for contributor, value in top_5.items():
                percentage = (value / total * 100) if total > 0 else 0
                top_contributors[str(contributor)] = {
                    'value': int(value),
                    'percentage': round(percentage, 2)
                }
            
            return top_contributors
            
        except Exception as e:
            self.logger.error(f"Error getting top contributors for period: {e}")
            return {}
    
    def _execute_targeted_analysis(self, query: str, data: pd.DataFrame, chart_context: Dict, query_patterns: Dict) -> Dict[str, Any]:
        """
        Execute analysis targeted to the specific query pattern.
        Routes to transition analysis or single-period analysis based on query type.
        """
        try:
            # NEW: Handle transition queries
            if query_patterns.get('is_transition_query') and chart_context:
                return self._execute_transition_analysis(query, data, chart_context, query_patterns)
            # Existing: Handle single-period queries
            elif query_patterns.get('is_time_period_query') and chart_context:
                return self._execute_time_period_analysis(query, data, chart_context, query_patterns)
            elif query_patterns.get('is_top_analysis'):
                return self._execute_top_contributors_analysis(data, chart_context)
            elif query_patterns.get('is_comparison'):
                return self._execute_comparison_analysis(data, chart_context, query_patterns)
            else:
                return self._execute_general_analysis(data, chart_context)
        except Exception as e:
            self.logger.error(f"Targeted analysis failed: {e}")
            return {'error': str(e), 'analysis_type': 'fallback'}
    
    def _execute_transition_analysis(self, query: str, data: pd.DataFrame, chart_context: Dict, query_patterns: Dict) -> Dict[str, Any]:
        """
        Execute comprehensive transition analysis for period-to-period comparisons.
        Implements reference code logic (lines 86-167) for ALL 5 features.
        
        Analyzes transitions like "Why spike from June to July 2025?"
        For each transition and each feature, calculates:
        - from_values, to_values
        - absolute_change, percent_change
        - top_increases, top_decreases
        
        Args:
            query: User query
            data: DataFrame to analyze
            chart_context: Chart context with features
            query_patterns: Detected query patterns including transitions
            
        Returns:
            Structured transition analysis results for all features
        """
        try:
            self.logger.info("=== STARTING TRANSITION ANALYSIS ===")
            self.logger.info(f"Query: '{query}'")
            
            # Step 1: Extract transition periods with year inference
            transitions_raw = query_patterns.get('transitions', [])
            if not transitions_raw:
                return {'error': 'No transitions detected in query'}
            
            extracted_transitions = self._extract_transition_periods(query, transitions_raw, data)
            if not extracted_transitions:
                return {'error': 'Could not extract transition periods'}
            
            self.logger.info(f"Processing {len(extracted_transitions)} transition(s)")
            
            # Step 2: Create pivot data for analysis
            # We'll use the first transition's from_period as reference for pivot creation
            first_trans = extracted_transitions[0]
            reference_period = first_trans['from']
            
            self.logger.info(f"Creating pivot data for transition analysis")
            pivot_df = self._create_period_based_pivot(data, chart_context, reference_period, query)
            
            if pivot_df is None:
                return {'error': 'Could not create pivot data for transition analysis'}
            
            # Extract multi-feature data
            multi_feature_data = getattr(pivot_df, '_multi_feature_data', {})
            valid_features = getattr(pivot_df, '_valid_features', [])
            
            self.logger.info(f"Pivot created with {len(valid_features)} features: {valid_features}")
            
            # Step 3: Analyze each transition
            transitions_results = []
            missing_periods_report = []
            
            for trans_idx, trans in enumerate(extracted_transitions):
                from_period = trans['from']
                from_year = trans['from_year']
                to_period = trans['to']
                to_year = trans['to_year']
                sentiment = trans.get('sentiment', 'change')
                year_assumption = trans.get('year_assumption', '')
                
                self.logger.info(f"\n--- Processing Transition {trans_idx + 1}: {from_period} {from_year} → {to_period} {to_year} ---")
                
                # Parse periods to pandas Period objects for arithmetic
                from_pd_period = pd.Period(f"{from_year}-{self._month_name_to_number(from_period):02d}", freq='M')
                to_pd_period = pd.Period(f"{to_year}-{self._month_name_to_number(to_period):02d}", freq='M')
                
                self.logger.info(f"Parsed pandas periods: {from_pd_period} → {to_pd_period}")
                
                # Find matching periods in data
                from_matching = self._find_matching_periods(f"{from_period} {from_year}", list(pivot_df.index), query_patterns)
                to_matching = self._find_matching_periods(f"{to_period} {to_year}", list(pivot_df.index), query_patterns)
                
                # Handle missing periods
                periods_available = True
                if not from_matching:
                    missing_periods_report.append(f"{from_period} {from_year} (start period)")
                    self.logger.warning(f"Missing data for from_period: {from_period} {from_year}")
                    periods_available = False
                
                if not to_matching:
                    missing_periods_report.append(f"{to_period} {to_year} (end period)")
                    self.logger.warning(f"Missing data for to_period: {to_period} {to_year}")
                    periods_available = False
                
                if not periods_available:
                    self.logger.warning(f"Skipping transition due to missing data")
                    continue
                
                from_matched = from_matching[0]
                to_matched = to_matching[0]
                
                self.logger.info(f"Matched periods: '{from_matched}' → '{to_matched}'")
                
                # PRIMARY FEATURE ANALYSIS (using main pivot)
                primary_from_values = pivot_df.loc[from_matched]
                primary_to_values = pivot_df.loc[to_matched]
                
                primary_absolute_change = primary_to_values - primary_from_values
                primary_percent_change = (primary_absolute_change / primary_from_values * 100).replace([float('inf'), -float('inf')], None)
                
                # Convert to dict and ensure numpy types are converted to Python native types
                primary_top_increases = self._convert_numpy_types(primary_absolute_change.nlargest(5).to_dict())
                primary_top_decreases = self._convert_numpy_types(primary_absolute_change.nsmallest(5).to_dict())
                
                self.logger.info(f"Primary feature analysis completed")
                
                # MULTI-FEATURE BREAKDOWN (all 5 features)
                feature_breakdowns = {}
                
                if multi_feature_data and valid_features:
                    self.logger.info(f"Processing transition for {len(valid_features)} features")
                    
                    for feature_name in valid_features:
                        feature_pivot = multi_feature_data[feature_name]
                        self.logger.debug(f"Analyzing transition for feature: {feature_name}")
                        
                        # Find matching periods for this feature
                        feature_from_matching = self._find_matching_periods(f"{from_period} {from_year}", list(feature_pivot.index), query_patterns)
                        feature_to_matching = self._find_matching_periods(f"{to_period} {to_year}", list(feature_pivot.index), query_patterns)
                        
                        if not feature_from_matching or not feature_to_matching:
                            self.logger.warning(f"Missing data for feature {feature_name} in transition periods")
                            continue
                        
                        feature_from_matched = feature_from_matching[0]
                        feature_to_matched = feature_to_matching[0]
                        
                        # Get values for from and to periods
                        from_values = feature_pivot.loc[feature_from_matched]
                        to_values = feature_pivot.loc[feature_to_matched]
                        
                        # Calculate changes (exactly as reference code lines 148-150)
                        absolute_change = to_values - from_values
                        percent_change = (absolute_change / from_values * 100).replace([float('inf'), -float('inf')], None)
                        
                        # Get top increases and decreases (exactly as reference code lines 159-160)
                        # Convert numpy types to Python native types for JSON serialization
                        top_increases = self._convert_numpy_types(absolute_change.nlargest(5).to_dict())
                        top_decreases = self._convert_numpy_types(absolute_change.nsmallest(5).to_dict())
                        
                        # Store feature-specific transition analysis
                        # Ensure all numeric values are Python native types for JSON serialization
                        feature_breakdowns[feature_name] = {
                            'from_values': self._series_to_dict(from_values),
                            'to_values': self._series_to_dict(to_values),
                            'absolute_change': self._series_to_dict(absolute_change),
                            'percent_change': self._series_to_dict_with_none(percent_change),
                            'top_increases': top_increases,
                            'top_decreases': top_decreases,
                            'total_from': int(float(from_values.sum())),
                            'total_to': int(float(to_values.sum())),
                            'total_change': int(float(absolute_change.sum()))
                        }
                        
                        self.logger.info(f"Feature {feature_name}: {len(from_values)} categories, change: {int(absolute_change.sum())}")
                
                # Build transition result structure (follows reference code lines 112-116)
                transition_result = {
                    'from_period': f"{from_period} {from_year}",
                    'to_period': f"{to_period} {to_year}",
                    'sentiment': sentiment,
                    'year_assumption': year_assumption,
                    
                    # Primary feature results
                    'from_values': self._series_to_dict(primary_from_values),
                    'to_values': self._series_to_dict(primary_to_values),
                    'absolute_change': self._series_to_dict(primary_absolute_change),
                    'percent_change': self._series_to_dict_with_none(primary_percent_change),
                    'top_increases': primary_top_increases,
                    'top_decreases': primary_top_decreases,
                    
                    # Multi-feature breakdown
                    'feature_breakdowns': feature_breakdowns
                }
                
                transitions_results.append(transition_result)
                self.logger.info(f"Completed transition {trans_idx + 1} with {len(feature_breakdowns)} features")
            
            # Build final analysis result
            analysis_result = {
                'analysis_type': 'transition_analysis',
                'transitions': transitions_results,
                'transitions_count': len(transitions_results),
                'features_analyzed': valid_features if valid_features else ['primary_feature'],
                'missing_periods': missing_periods_report if missing_periods_report else None,
                'query': query,
                'success': True
            }
            
            self.logger.info(f"=== TRANSITION ANALYSIS COMPLETED ===")
            self.logger.info(f"Analyzed {len(transitions_results)} transition(s) across {len(valid_features)} features")
            if missing_periods_report:
                self.logger.warning(f"Missing data for periods: {missing_periods_report}")
            
            return analysis_result
            
        except Exception as e:
            self.logger.error(f"Transition analysis failed: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return {'error': str(e), 'analysis_type': 'transition_analysis'}
    
    def _series_to_dict_with_none(self, series: pd.Series) -> Dict:
        """
        Convert pandas Series to dictionary, preserving None values for division by zero.
        Used for percent_change where None indicates division by zero.
        Handles numpy types to ensure JSON compatibility.
        
        Args:
            series: Pandas Series to convert
            
        Returns:
            Dictionary with None preserved for null values, using Python native types
        """
        if series is None:
            return None
        
        try:
            result = {}
            for k, v in series.items():
                if pd.isna(v):
                    result[str(k)] = None
                else:
                    # Convert numpy types to Python native types
                    if isinstance(v, (np.integer, np.int64, np.int32)):
                        result[str(k)] = int(v)
                    elif isinstance(v, (np.floating, np.float64, np.float32)):
                        result[str(k)] = float(v)
                    else:
                        result[str(k)] = float(v)
            return result
        except Exception as e:
            self.logger.error(f"Error converting series to dict with none: {e}")
            return {}
    
    def _convert_numpy_types(self, obj: Any) -> Any:
        """
        Recursively convert numpy types to Python native types for JSON serialization.
        
        Args:
            obj: Object to convert (dict, list, numpy type, etc.)
            
        Returns:
            Object with all numpy types converted to Python native types
        """
        if isinstance(obj, dict):
            return {k: self._convert_numpy_types(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_numpy_types(item) for item in obj]
        elif isinstance(obj, (np.integer, np.int64, np.int32, np.int16, np.int8)):
            return int(obj)
        elif isinstance(obj, (np.floating, np.float64, np.float32, np.float16)):
            return float(obj)
        elif isinstance(obj, np.bool_):
            return bool(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif pd.isna(obj):
            return None
        else:
            return obj
    
    def _execute_time_period_analysis(self, query: str, data: pd.DataFrame, chart_context: Dict, query_patterns: Dict) -> Dict[str, Any]:
        """
        Execute comprehensive time period analysis following reference code pattern.
        Focuses on the specific period mentioned in the query (e.g., "March").
        """
        try:
            # Step 1: Extract the target period from the query
            target_period_str = self._extract_target_period(query, query_patterns)
            if not target_period_str:
                return {'error': 'Could not extract target period from query'}
            
            self.logger.info(f"Analyzing specific period: {target_period_str}")
            
            # Step 2: Create period-based pivot table
            self.logger.info(f"Creating period-based pivot table for {target_period_str}")
            pivot_df = self._create_period_based_pivot(data, chart_context, target_period_str, query)
            
            if pivot_df is None:
                self.logger.error(f"Failed to create pivot table for {target_period_str}")
                return {'error': 'Could not create period-based analysis'}
            
            self.logger.info(f"Pivot table created successfully. Shape: {pivot_df.shape}")
            self.logger.info(f"Pivot table index (periods): {list(pivot_df.index)}")
            self.logger.info(f"Pivot table columns (features): {list(pivot_df.columns)}")
            
            # Extract multi-feature data if available
            multi_feature_data = getattr(pivot_df, '_multi_feature_data', {})
            valid_features = getattr(pivot_df, '_valid_features', [])
            
            if multi_feature_data:
                self.logger.info(f"Multi-feature analysis available for {len(valid_features)} features: {valid_features}")
            else:
                self.logger.info(f"Single-feature analysis mode")
            
            # Step 3: Parse period for temporal comparisons
            target_period = self._parse_period_to_pandas_period(target_period_str, data)
            self.logger.info(f"target_period after parsing: {target_period}")
            self.logger.info(f"target_period type: {type(target_period)}")
            sentiment = query_patterns.get('sentiment', 'neutral')
            
            # Step 4: Get current period values (the specific period mentioned) - with flexible matching
            self.logger.info(f"Looking for target period '{target_period_str}' using flexible matching")
            
            # Use flexible period matching
            matching_periods = self._find_matching_periods(target_period_str, list(pivot_df.index), query_patterns)
            
            if not matching_periods:
                self.logger.error(f"Target period '{target_period_str}' not found using flexible matching")
                self.logger.error(f"Available periods in index: {list(pivot_df.index)}")
                return {'error': f'Target period {target_period_str} not found in data'}
            
            # Use the first matching period (most relevant)
            matched_period = matching_periods[0]
            self.logger.info(f"Target period '{target_period_str}' matched to '{matched_period}'")
            
            if len(matching_periods) > 1:
                self.logger.info(f"Multiple matches found: {matching_periods}, using first: {matched_period}")
            
            current_values = pivot_df.loc[matched_period]
            self.logger.info(f"Current period values for {matched_period}: {current_values.to_dict()}")
            
            # Update target_period_str to the matched period for consistent reporting
            target_period_display = target_period_str  # Keep original for display
            target_period_str = str(matched_period)  # Use matched for calculations
            
            # Sort values based on sentiment for the target period
            if sentiment == 'low':
                current_sorted = current_values.sort_values(ascending=True)
            else:
                current_sorted = current_values.sort_values(ascending=False)
            
            # Step 5: Calculate temporal comparisons
            mom_comparison = self._calculate_mom_comparison(pivot_df, target_period)
            self.logger.info(f"mom_comparison result: {mom_comparison}")
            if mom_comparison is not None:
                self.logger.info(f"MoM non-zero changes: {(mom_comparison != 0).sum()}/{len(mom_comparison)}")
            self.logger.info(f"pivot_df.index: {list(pivot_df.index)}")
            qoq_comparison = self._calculate_qoq_comparison(pivot_df, target_period)
            yoy_comparison = self._calculate_yoy_comparison(pivot_df, target_period)
            
            # Sort temporal comparisons the same way
            mom_sorted = mom_comparison.sort_values(ascending=(sentiment == 'low')) if mom_comparison is not None else None
            qoq_sorted = qoq_comparison.sort_values(ascending=(sentiment == 'low')) if qoq_comparison is not None else None
            yoy_sorted = yoy_comparison.sort_values(ascending=(sentiment == 'low')) if yoy_comparison is not None else None
            
            # Step 6: Get top contributors for the specific period
            top_contributors = self._get_top_contributors_for_period(pivot_df, matched_period, sentiment)
            
            # Step 7: Build structured result following reference code format
            self.logger.info(f"Building structured results for {target_period_display} (matched to {target_period_str})")
            
            analysis_result = {
                'analysis_type': 'time_period_analysis',
                'target_period': target_period_display,  # Use original query term for display
                'matched_period': target_period_str,     # Include actual matched period
                'sentiment': sentiment,
                
                # Core results in reference code format (primary feature)
                'current': self._series_to_dict(current_sorted),
                'mom': self._series_to_dict(mom_sorted) if mom_sorted is not None else None,
                'qoq': self._series_to_dict(qoq_sorted) if qoq_sorted is not None else None,
                'yoy': self._series_to_dict(yoy_sorted) if yoy_sorted is not None else None,
                'top_contributors': top_contributors,
                
                # Multi-feature breakdown analysis
                'feature_breakdowns': {},
                
                # Additional metadata
                'period_info': {
                    'target_period': target_period_display,
                    'matched_period': target_period_str,
                    'dataframe_used': 'filtered_chart_data',
                    'analysis_focus': f'{sentiment} analysis for {target_period_display}',
                    'features_analyzed': valid_features if valid_features else ['primary_feature']
                },
                
                # Summary statistics for the target period
                'target_period_summary': {
                    'total_value': int(current_values.sum()),
                    'max_contributor': current_sorted.index[0] if len(current_sorted) > 0 else None,
                    'max_value': int(current_sorted.iloc[0]) if len(current_sorted) > 0 else None,
                    'contributor_count': len(current_values)
                }
            }
            
            # If multi-feature data is available, process each feature
            if multi_feature_data and valid_features:
                self.logger.info(f"Processing multi-feature analysis for {len(valid_features)} features")
                
                for feature_name in valid_features:
                    feature_pivot = multi_feature_data[feature_name]
                    self.logger.info(f"Processing feature: {feature_name}")
                    
                    # Find matching periods for this feature's pivot
                    feature_matching_periods = self._find_matching_periods(target_period_display, list(feature_pivot.index), query_patterns)
                    
                    if feature_matching_periods:
                        feature_matched_period = feature_matching_periods[0]
                        feature_values = feature_pivot.loc[feature_matched_period]
                        
                        # Sort values based on sentiment
                        feature_sorted = feature_values.sort_values(ascending=(sentiment == 'low'))
                        
                        # Get top contributors for this feature
                        feature_top_contributors = self._get_top_contributors_for_period(feature_pivot, feature_matched_period, sentiment)
                        
                        # Store feature-specific analysis
                        analysis_result['feature_breakdowns'][feature_name] = {
                            'current': self._series_to_dict(feature_sorted),
                            'top_contributors': feature_top_contributors,
                            'total_value': int(feature_values.sum()),
                            'max_contributor': feature_sorted.index[0] if len(feature_sorted) > 0 else None,
                            'max_value': int(feature_sorted.iloc[0]) if len(feature_sorted) > 0 else None,
                            'contributor_count': len(feature_values)
                        }
                        
                        self.logger.info(f"Feature {feature_name}: {len(feature_sorted)} categories, total={int(feature_values.sum())}")
                
                self.logger.info(f"Multi-feature analysis completed for {len(analysis_result['feature_breakdowns'])} features")

            self.logger.info(f"Final analysis_result feature_breakdowns: {list(analysis_result['feature_breakdowns'].keys())}")
            self.logger.info(f"Feature breakdowns count: {len(analysis_result['feature_breakdowns'])}")
            self.logger.info(f"Successfully built structured results for {target_period_display} (matched to {target_period_str})")
            self.logger.info(f"Results include: current={len(analysis_result['current'])} items, mom={'available' if mom_sorted is not None else 'not available'}")
            self.logger.info(f"Top contributors: {analysis_result['top_contributors']}")
            self.logger.info(f"Target period summary: total={analysis_result['target_period_summary']['total_value']}, max_contributor={analysis_result['target_period_summary']['max_contributor']}")
            
            return analysis_result
            
        except Exception as e:
            self.logger.error(f"Time period analysis failed: {e}")
            return {'error': str(e), 'analysis_type': 'time_period_analysis'}
    
    def _series_to_dict(self, series: pd.Series) -> Dict:
        """
        Convert pandas Series to dictionary for JSON serialization.
        Handles numpy types to ensure JSON compatibility.
        
        Args:
            series: Pandas Series to convert
            
        Returns:
            Dictionary representation with Python native types
        """
        if series is None:
            return None
        
        try:
            result = {}
            for k, v in series.items():
                if pd.notna(v):
                    # Convert numpy types to Python native types
                    if isinstance(v, (np.integer, np.int64, np.int32)):
                        result[str(k)] = int(v)
                    elif isinstance(v, (np.floating, np.float64, np.float32)):
                        result[str(k)] = float(v)
                    else:
                        result[str(k)] = int(v)
                else:
                    result[str(k)] = 0
            return result
        except Exception as e:
            self.logger.error(f"Error converting series to dict: {e}")
            return {}
    
    def _detect_period_format(self, periods_list) -> Dict[str, Any]:
        """
        Automatically detect what format the periods are in.
        
        Args:
            periods_list: List of period values from data
            
        Returns:
            Dict with format info: {
                'format_type': 'timestamp'|'month_name'|'numeric'|'mixed',
                'sample_values': [...],
                'is_timestamp': bool,
                'has_year_info': bool,
                'date_pattern': str if applicable
            }
        """
        if not periods_list:
            return {'format_type': 'empty', 'sample_values': []}
        
        sample_values = list(periods_list)[:5]  # Analyze first 5 values
        format_info = {
            'format_type': 'unknown',
            'sample_values': sample_values,
            'is_timestamp': False,
            'has_year_info': False,
            'date_pattern': None
        }
        
        # Check if values are timestamps
        timestamp_count = 0
        month_name_count = 0
        numeric_count = 0
        
        for value in sample_values:
            value_str = str(value)
            
            # Check for timestamp patterns
            if (('-' in value_str and ('2024' in value_str or '2025' in value_str)) or 
                isinstance(value, (pd.Timestamp, datetime))):
                timestamp_count += 1
                format_info['has_year_info'] = True
            
            # Check for month names
            elif value_str.lower() in ['january', 'february', 'march', 'april', 'may', 'june',
                                     'july', 'august', 'september', 'october', 'november', 'december']:
                month_name_count += 1
            
            # Check for numeric (month numbers)
            elif value_str.isdigit() and 1 <= int(value_str) <= 12:
                numeric_count += 1
        
        # Determine dominant format
        total_samples = len(sample_values)
        if timestamp_count > total_samples * 0.7:
            format_info['format_type'] = 'timestamp'
            format_info['is_timestamp'] = True
            format_info['date_pattern'] = 'YYYY-MM-DD' if '-' in str(sample_values[0]) else 'timestamp_object'
        elif month_name_count > total_samples * 0.7:
            format_info['format_type'] = 'month_name'
        elif numeric_count > total_samples * 0.7:
            format_info['format_type'] = 'numeric'
        else:
            format_info['format_type'] = 'mixed'
        
        self.logger.debug(f"Detected period format: {format_info}")
        return format_info
    
    def _find_matching_periods(self, target_period_str: str, available_periods, query_patterns: Dict = None) -> List:
        """
        Dynamically find periods that match the target, supporting flexible matching.
        Enhanced to extract year from target_period_str parameter directly.
        
        Args:
            target_period_str: Target period like "March" or "March 2025"
            available_periods: List of available periods in the data
            query_patterns: Optional patterns from query analysis
            
        Returns:
            List of matching periods from available_periods (most recent when multiple)
        """
        if not available_periods:
            return []
        
        matching_periods = []
        target_lower = target_period_str.lower()
        
        # Get format info about available periods
        format_info = self._detect_period_format(available_periods)
        
        # FIX #1: Extract year from target_period_str parameter FIRST (highest priority)
        target_year = None
        year_in_string = re.search(r'\b(20\d{2})\b', target_period_str)
        if year_in_string:
            target_year = year_in_string.group(1)
            self.logger.info(f"Year extracted from target_period_str: {target_year}")
        
        # Fallback: Extract year from query patterns if not found in string
        if not target_year and query_patterns and 'year' in query_patterns.get('periods', {}):
            years = query_patterns['periods']['year']
            target_year = years[0] if years else None
            if target_year:
                self.logger.info(f"Year extracted from query_patterns: {target_year}")
        
        self.logger.info(f"Looking for '{target_period_str}' in {format_info['format_type']} format")
        self.logger.debug(f"Target year for filtering: {target_year}")
        
        for period in available_periods:
            period_str = str(period)
            
            if format_info['is_timestamp']:
                # Handle timestamp format
                if self._period_matches_timestamp(target_period_str, period_str, target_year):
                    matching_periods.append(period)
            
            elif format_info['format_type'] == 'month_name':
                # Handle month name format
                if target_lower in period_str.lower():
                    matching_periods.append(period)
            
            elif format_info['format_type'] == 'numeric':
                # Handle numeric month format
                month_num = self._month_name_to_number(target_period_str)
                if month_num and str(month_num) == period_str:
                    matching_periods.append(period)
            
            else:
                # Fallback: partial string matching
                if target_lower in period_str.lower():
                    matching_periods.append(period)
        
        self.logger.info(f"Found {len(matching_periods)} matching periods: {matching_periods}")
        
        # FIX #2: When multiple matches exist and no year was specified, prefer the MOST RECENT
        if not target_year and len(matching_periods) > 1:
            # Sort to ensure we have chronological order, then take the last (most recent)
            most_recent = matching_periods[-1]
            self.logger.info(f"Multiple years found ({len(matching_periods)} matches), preferring most recent: {most_recent}")
            self.logger.debug(f"All matches found: {matching_periods}")
            self.logger.debug(f"Selected (most recent): {most_recent}")
            return [most_recent]
        
        return matching_periods
    
    def _period_matches_timestamp(self, target_period: str, timestamp_str: str, target_year: str = None) -> bool:
        """
        Check if a target period matches a timestamp string.
        
        Args:
            target_period: Like "March" or "March 2025"
            timestamp_str: Like "2025-03-01 00:00:00"
            target_year: Optional specific year to match
            
        Returns:
            True if the timestamp represents the target period
        """
        try:
            # Extract month number from target period
            month_num = self._month_name_to_number(target_period)
            if not month_num:
                return False
            
            # Parse timestamp to extract month and year
            if isinstance(timestamp_str, str):
                # Handle string timestamps like "2025-03-01 00:00:00"
                if '-' in timestamp_str:
                    parts = timestamp_str.split('-')
                    if len(parts) >= 3:
                        year_part = parts[0]
                        month_part = parts[1]
                        
                        # Check month match
                        if f"{month_num:02d}" != month_part:
                            return False
                        
                        # Check year match if specified
                        if target_year and target_year != year_part:
                            return False
                        
                        return True
            
            # Fallback: try to parse as pandas timestamp
            try:
                ts = pd.Timestamp(timestamp_str)
                if ts.month == month_num:
                    if target_year and str(ts.year) != target_year:
                        return False
                    return True
            except:
                pass
                
        except Exception as e:
            self.logger.debug(f"Error matching period {target_period} to timestamp {timestamp_str}: {e}")
        
        return False
    
    def _month_name_to_number(self, month_name: str) -> Optional[int]:
        """
        Convert month name to number dynamically.
        
        Args:
            month_name: Month name like "March", "mar", "march"
            
        Returns:
            Month number (1-12) or None if not found
        """
        month_name_lower = month_name.lower().strip()
        
        # Full month names
        month_mapping = {
            'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
            'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12
        }
        
        # Check full names first
        if month_name_lower in month_mapping:
            return month_mapping[month_name_lower]
        
        # Check abbreviated forms
        for full_name, number in month_mapping.items():
            if month_name_lower.startswith(full_name[:3]):  # First 3 chars
                return number
        
        return None
    
    def _execute_top_contributors_analysis(self, data: pd.DataFrame, chart_context: Dict) -> Dict[str, Any]:
        """
        Execute top contributors analysis.
        """
        try:
            top_features = chart_context.get('top_5_features', []) if chart_context else []
            y_axis = chart_context.get('y_axis_detected') if chart_context else None
            
            if not top_features or not y_axis or y_axis not in data.columns:
                # Fallback to numeric columns
                numeric_cols = data.select_dtypes(include=[np.number]).columns.tolist()
                if numeric_cols:
                    y_axis = numeric_cols[0]
                    top_features = data.columns.tolist()[:5]
                else:
                    return {'error': 'No suitable columns for analysis'}
            
            contributors_analysis = {}
            
            for feature in top_features:
                if feature in data.columns and feature != y_axis:
                    try:
                        # Get top contributors for this feature
                        feature_agg = data.groupby(feature)[y_axis].agg(['count', 'sum', 'mean']).round(2)
                        feature_sorted = feature_agg.sort_values('count', ascending=False)
                        
                        # Calculate percentages
                        total_count = feature_agg['count'].sum()
                        feature_sorted['percentage'] = (feature_sorted['count'] / total_count * 100).round(2)
                        
                        contributors_analysis[feature] = {
                            'top_contributor': feature_sorted.index[0],
                            'top_value': feature_sorted.iloc[0]['count'],
                            'top_percentage': feature_sorted.iloc[0]['percentage'],
                            'all_contributors': feature_sorted.head(5).to_dict('index')
                        }
                    except Exception as e:
                        self.logger.warning(f"Failed to analyze feature {feature}: {e}")
                        continue
            
            return {
                'analysis_type': 'top_contributors_analysis',
                'contributors_by_feature': contributors_analysis,
                'features_analyzed': list(contributors_analysis.keys())
            }
            
        except Exception as e:
            self.logger.error(f"Top contributors analysis failed: {e}")
            return {'error': str(e), 'analysis_type': 'top_contributors_analysis'}
    
    def _execute_comparison_analysis(self, data: pd.DataFrame, chart_context: Dict, query_patterns: Dict) -> Dict[str, Any]:
        """
        Execute comparison analysis (MoM, YoY, etc.).
        """
        try:
            # This would implement comparison logic
            # For now, return basic structure
            return {
                'analysis_type': 'comparison_analysis',
                'comparison_type': 'temporal' if query_patterns.get('needs_mom') else 'categorical',
                'note': 'Comparison analysis implementation pending'
            }
        except Exception as e:
            return {'error': str(e), 'analysis_type': 'comparison_analysis'}
    
    def _execute_general_analysis(self, data: pd.DataFrame, chart_context: Dict) -> Dict[str, Any]:
        """
        Execute general analysis for non-specific queries.
        """
        try:
            # Basic descriptive statistics
            numeric_cols = data.select_dtypes(include=[np.number]).columns.tolist()
            categorical_cols = data.select_dtypes(exclude=[np.number]).columns.tolist()
            
            analysis = {
                'analysis_type': 'general_analysis',
                'data_overview': {
                    'total_rows': len(data),
                    'numeric_columns': len(numeric_cols),
                    'categorical_columns': len(categorical_cols)
                }
            }
            
            if numeric_cols:
                analysis['numeric_summary'] = data[numeric_cols].describe().round(2).to_dict()
            
            return analysis
            
        except Exception as e:
            return {'error': str(e), 'analysis_type': 'general_analysis'}
    
    def _compute_key_statistics(self, data: pd.DataFrame, chart_context: Dict) -> Dict[str, Any]:
        """
        Compute key statistics that LLM would otherwise hallucinate.
        """
        try:
            statistics = {}
            
            # Get numeric columns
            numeric_cols = data.select_dtypes(include=[np.number]).columns.tolist()
            
            for col in numeric_cols:
                col_stats = {
                    'max_value': float(data[col].max()),
                    'min_value': float(data[col].min()),
                    'mean_value': float(data[col].mean()),
                    'sum_value': float(data[col].sum()),
                    'std_value': float(data[col].std()) if data[col].std() is not None else 0
                }
                statistics[col] = col_stats
            
            # Get categorical columns with value counts
            categorical_cols = data.select_dtypes(exclude=[np.number]).columns.tolist()
            
            for col in categorical_cols[:5]:  # Limit to first 5 to avoid huge output
                if data[col].nunique() <= 50:  # Only for reasonable number of categories
                    value_counts = data[col].value_counts().head(10)
                    total_count = len(data)
                    
                    statistics[col] = {
                        'top_category': value_counts.index[0],
                        'top_count': int(value_counts.iloc[0]),
                        'top_percentage': round(value_counts.iloc[0] / total_count * 100, 2),
                        'unique_values': int(data[col].nunique()),
                        'value_distribution': value_counts.to_dict()
                    }
            
            return statistics
            
        except Exception as e:
            self.logger.error(f"Statistics computation failed: {e}")
            return {'error': str(e)}
    
    def _create_structured_pivot_data(self, data: pd.DataFrame, chart_context: Dict, query_patterns: Dict) -> str:
        """
        Create structured pivot data based on query patterns and chart context.
        Replaces the hardcoded CSV generation.
        """
        try:
            if not chart_context:
                return "No chart context available for pivot data"
            
            x_axis = chart_context.get('x_axis_detected')
            y_axis = chart_context.get('y_axis_detected')
            top_features = chart_context.get('top_5_features', [])
            print(x_axis, y_axis, top_features)
            print(data.columns)
            if not x_axis or not y_axis or not top_features:
                return "Insufficient chart context for pivot data"
            
            if x_axis not in data.columns or y_axis not in data.columns:
                return f"Required columns not found: {x_axis}, {y_axis}"
            
            pivot_text = ""
            
            # Process each top feature
            # for feature in top_features[:3]:  # Limit to top 3 to avoid huge output
            for feature in top_features:
                if feature not in data.columns or feature == x_axis or feature == y_axis:
                    print ("skipping feature:", feature)
                    continue
                
                try:
                    # Skip features with too many unique values
                    if data[feature].nunique() > 100:
                        print ("skipping feature:", feature)
                        continue
                    
                    # Create pivot table
                    pivot_df = data.groupby([x_axis, feature])[y_axis].count().reset_index()
                    pivot_df = pivot_df.pivot(index=x_axis, columns=feature, values=y_axis).fillna(0)
                    
                    # If x_axis looks like months, reorder them
                    if 'month' in x_axis.lower() and pivot_df.index.dtype == 'object':
                        month_order = ["January", "February", "March", "April", "May", "June",
                                     "July", "August", "September", "October", "November", "December"]
                        # Reorder if possible
                        available_months = [m for m in month_order if m in pivot_df.index]
                        if available_months:
                            pivot_df = pivot_df.reindex(available_months)
                    print("printing pivot_df")
                    print(pivot_df)
                    pivot_text += f"\n=== {feature.upper()} BREAKDOWN ===\n"
                    pivot_text += pivot_df.to_csv()
                    pivot_text += "\n"
                    
                except Exception as e:
                    self.logger.warning(f"Failed to create pivot for feature {feature}: {e}")
                    continue
            
            return pivot_text if pivot_text else "No suitable pivot data could be generated"
            
        except Exception as e:
            self.logger.error(f"Pivot data creation failed: {e}")
            return f"Error creating pivot data: {str(e)}"
    
    def _format_transition_csv_text(self, targeted_analysis: Dict) -> str:
        """
        Format transition analysis results as CSV-style text for LLM consumption.
        Implements reference code narration logic (lines 257-289) for all 5 features.
        
        Args:
            targeted_analysis: Transition analysis results
            
        Returns:
            Formatted CSV-style text with transition data for all features
        """
        try:
            csv_sections = []
            transitions = targeted_analysis.get('transitions', [])
            
            if not transitions:
                return "No transition data available"
            
            csv_sections.append(f"=== TRANSITION ANALYSIS RESULTS ===\n")
            
            # Report missing periods if any
            missing_periods = targeted_analysis.get('missing_periods')
            if missing_periods:
                csv_sections.append(f"NOTE: Missing data for periods: {', '.join(missing_periods)}\n")
                csv_sections.append("Analysis conducted for available periods only.\n")
            
            # Process each transition
            for trans_idx, trans in enumerate(transitions):
                from_period = trans.get('from_period', 'Unknown')
                to_period = trans.get('to_period', 'Unknown')
                year_assumption = trans.get('year_assumption', '')
                
                csv_sections.append(f"\n{'='*80}")
                csv_sections.append(f"TRANSITION {trans_idx + 1}: FROM {from_period.upper()} TO {to_period.upper()}")
                csv_sections.append(f"{'='*80}\n")
                
                if year_assumption:
                    csv_sections.append(f"Year Assumption: {year_assumption}\n")
                
                # PRIMARY FEATURE SUMMARY
                csv_sections.append("\n--- PRIMARY FEATURE TRANSITION ---\n")
                csv_sections.append(f"Period,{from_period},{to_period},Absolute_Change,Percent_Change")
                
                from_values = trans.get('from_values', {})
                to_values = trans.get('to_values', {})
                absolute_change = trans.get('absolute_change', {})
                percent_change = trans.get('percent_change', {})
                
                # Show top 10 entities
                for entity in list(from_values.keys())[:10]:
                    from_val = from_values.get(entity, 0)
                    to_val = to_values.get(entity, 0)
                    abs_change = absolute_change.get(entity, 0)
                    pct_change = percent_change.get(entity, 'N/A')
                    pct_str = f"{pct_change:.1f}%" if isinstance(pct_change, (int, float)) else str(pct_change)
                    csv_sections.append(f"{entity},{from_val},{to_val},{abs_change:+d},{pct_str}")
                
                # Top increases and decreases
                top_increases = trans.get('top_increases', {})
                top_decreases = trans.get('top_decreases', {})
                
                if top_increases:
                    csv_sections.append(f"\nBiggest Increases (Top 5):")
                    csv_sections.append("Entity,Change")
                    for entity, change in list(top_increases.items())[:5]:
                        csv_sections.append(f"{entity},{change:+.0f}")
                
                if top_decreases:
                    csv_sections.append(f"\nBiggest Decreases (Top 5):")
                    csv_sections.append("Entity,Change")
                    for entity, change in list(top_decreases.items())[:5]:
                        csv_sections.append(f"{entity},{change:+.0f}")
                
                # MULTI-FEATURE BREAKDOWN (ALL 5 FEATURES)
                feature_breakdowns = trans.get('feature_breakdowns', {})
                
                if feature_breakdowns:
                    csv_sections.append(f"\n\n{'='*80}")
                    csv_sections.append(f"COMPREHENSIVE MULTI-FEATURE TRANSITION ANALYSIS")
                    csv_sections.append(f"{'='*80}\n")
                    
                    for feature_name, feature_data in feature_breakdowns.items():
                        csv_sections.append(f"\n--- {feature_name.upper()} BREAKDOWN ---")
                        csv_sections.append(f"From {from_period} to {to_period}\n")
                        
                        # Feature transition data
                        csv_sections.append(f"Category,{from_period}_Value,{to_period}_Value,Absolute_Change,Percent_Change")
                        
                        feature_from = feature_data.get('from_values', {})
                        feature_to = feature_data.get('to_values', {})
                        feature_abs_change = feature_data.get('absolute_change', {})
                        feature_pct_change = feature_data.get('percent_change', {})
                        
                        # Show all categories (or top 15 if too many)
                        categories = list(feature_from.keys())
                        display_categories = categories[:15] if len(categories) > 15 else categories
                        
                        for category in display_categories:
                            f_val = feature_from.get(category, 0)
                            t_val = feature_to.get(category, 0)
                            abs_chg = feature_abs_change.get(category, 0)
                            pct_chg = feature_pct_change.get(category, 'N/A')
                            pct_str = f"{pct_chg:.1f}%" if isinstance(pct_chg, (int, float)) else str(pct_chg)
                            csv_sections.append(f"{category},{f_val},{t_val},{abs_chg:+d},{pct_str}")
                        
                        # Feature-specific top movers
                        feature_top_increases = feature_data.get('top_increases', {})
                        feature_top_decreases = feature_data.get('top_decreases', {})
                        
                        if feature_top_increases:
                            csv_sections.append(f"\nTop {feature_name.title()} Increases:")
                            csv_sections.append("Category,Change")
                            for category, change in list(feature_top_increases.items())[:5]:
                                csv_sections.append(f"{category},{change:+.0f}")
                        
                        if feature_top_decreases:
                            csv_sections.append(f"\nTop {feature_name.title()} Decreases:")
                            csv_sections.append("Category,Change")
                            for category, change in list(feature_top_decreases.items())[:5]:
                                csv_sections.append(f"{category},{change:+.0f}")
                        
                        # Feature summary
                        total_from = feature_data.get('total_from', 0)
                        total_to = feature_data.get('total_to', 0)
                        total_change = feature_data.get('total_change', 0)
                        
                        csv_sections.append(f"\n{feature_name.title()} Summary:")
                        csv_sections.append(f"Total {from_period}: {total_from}")
                        csv_sections.append(f"Total {to_period}: {total_to}")
                        csv_sections.append(f"Total Change: {total_change:+d}")
                        
                        if len(categories) > 15:
                            csv_sections.append(f"(Showing top 15 of {len(categories)} categories)")
            
            result = "\n".join(csv_sections)
            self.logger.info(f"Formatted transition CSV text: {len(result)} characters")
            return result
            
        except Exception as e:
            self.logger.error(f"Error formatting transition CSV text: {e}")
            return f"Error formatting transition data: {str(e)}"
    
    def prepare_enhanced_csv_text(self, data: pd.DataFrame, chart_context: Dict, computational_results: Dict) -> str:
        """
        Prepare enhanced CSV text using computational results.
        For time period analysis, provides period-specific structured data.
        For transition analysis, provides transition comparison data.
        """
        try:
            if not computational_results.get('success'):
                return computational_results.get('pivot_data', "No computational results available")
            
            # Check if this is a transition analysis
            targeted_analysis = computational_results.get('targeted_analysis', {})
            is_transition_analysis = (targeted_analysis.get('analysis_type') == 'transition_analysis' and 
                                    'transitions' in targeted_analysis)
            
            if is_transition_analysis:
                # Handle transition analysis (reference code lines 257-289)
                return self._format_transition_csv_text(targeted_analysis)
            
            # Check if this is a time period analysis with structured results
            is_time_period_analysis = (targeted_analysis.get('analysis_type') == 'time_period_analysis' and 
                                     'current' in targeted_analysis)
            
            if is_time_period_analysis:
                # Build period-specific CSV-style data following reference code pattern
                target_period = targeted_analysis.get('target_period', 'Unknown')
                csv_text = f"=== PERIOD-SPECIFIC ANALYSIS DATA FOR {target_period.upper()} ===\n\n"
                
                # Current period breakdown
                current_results = targeted_analysis.get('current', {})
                if current_results:
                    csv_text += f"{target_period} Values by Category:\n"
                    csv_text += "Category,Value\n"
                    for category, value in current_results.items():
                        csv_text += f"{category},{value}\n"
                    csv_text += "\n"
                
                # Month-over-month comparison if available
                mom_results = targeted_analysis.get('mom', {})
                if mom_results:
                    csv_text += f"{target_period} vs Previous Month Changes:\n"
                    csv_text += "Category,Change\n"
                    for category, change in mom_results.items():
                        csv_text += f"{category},{change:+d}\n"
                    csv_text += "\n"
                
                # Top contributors summary
                top_contributors = targeted_analysis.get('top_contributors', {})
                if top_contributors:
                    csv_text += f"Top Contributors in {target_period}:\n"
                    csv_text += "Contributor,Value,Percentage\n"
                    for contributor, data in top_contributors.items():
                        value = data.get('value', 0)
                        percentage = data.get('percentage', 0)
                        csv_text += f"{contributor},{value},{percentage}%\n"
                    csv_text += "\n"
                
                # Multi-feature breakdowns if available
                feature_breakdowns = targeted_analysis.get('feature_breakdowns', {})
                self.logger.info(f"CSV Generation - feature_breakdowns keys: {list(feature_breakdowns.keys())}")
                self.logger.info(f"CSV Generation - feature_breakdowns empty: {len(feature_breakdowns) == 0}")
                self.logger.info(f"DEBUG: feature_breakdowns = {feature_breakdowns}")
                if feature_breakdowns:
                    csv_text += f"\n=== COMPREHENSIVE {target_period.upper()} MULTI-FEATURE ANALYSIS ===\n\n"
                    self.logger.info(f"CSV text length before multi-feature: {len(csv_text)}")
                    for feature_name, feature_data in feature_breakdowns.items():
                        self.logger.info(f"CSV Generation: Processing {feature_name}")
                        self.logger.info(f"Processing feature {feature_name}: data keys = {list(feature_data.keys())}")
                        self.logger.info(f"Feature {feature_name} current data length: {len(feature_data.get('current', {}))}")
                        csv_text += f"{feature_name.upper()} BREAKDOWN ({target_period}):\n"
                        csv_text += "Category,Value\n"
                        
                        # Add feature-specific current data
                        feature_current = feature_data.get('current', {})
                        self.logger.info(f"CSV Generation: {feature_name} has {len(feature_current)} current items")
                        for category, value in feature_current.items():
                            csv_text += f"{category},{value}\n"

                        self.logger.info(f"CSV Generation: {feature_name} section completed")

                        # Add feature-specific top contributors
                        feature_top_contributors = feature_data.get('top_contributors', {})
                        if feature_top_contributors:
                            csv_text += f"\nTop {feature_name.title()} Contributors ({target_period}):\n"
                            csv_text += "Contributor,Value,Percentage\n"
                            for contributor, data in feature_top_contributors.items():
                                value = data.get('value', 0)
                                percentage = data.get('percentage', 0)
                                csv_text += f"{contributor},{value},{percentage}%\n"
                        
                        csv_text += "\n"
                    print ("\n\n\n----------------csv_text----------------\n\n\n")
                    print (csv_text)
                    print ("csv_text printed")
                    self.logger.info(f"CSV text length after multi-feature: {len(csv_text)}")
                
                # Period summary
                summary = targeted_analysis.get('target_period_summary', {})
                if summary:
                    csv_text += f"{target_period} Summary:\n"
                    csv_text += f"Total Value: {summary.get('total_value', 0)}\n"
                    csv_text += f"Top Contributor: {summary.get('max_contributor', 'Unknown')}\n"
                    csv_text += f"Top Value: {summary.get('max_value', 0)}\n"
                
                return csv_text
            
            else:
                # Use the structured pivot data from computational results (fallback)
                pivot_data = computational_results.get('pivot_data', '')
                
                # Add computed statistics summary
                statistics = computational_results.get('computed_statistics', {})
                if statistics:
                    stats_summary = "\n=== COMPUTED STATISTICS SUMMARY ===\n"
                    for col, stats in statistics.items():
                        if isinstance(stats, dict) and 'top_category' in stats:
                            stats_summary += f"{col}: Top = {stats['top_category']} ({stats['top_count']} occurrences, {stats['top_percentage']}%)\n"
                        elif isinstance(stats, dict) and 'max_value' in stats:
                            stats_summary += f"{col}: Max = {stats['max_value']}, Mean = {stats['mean_value']:.2f}\n"
                    
                    pivot_data = stats_summary + "\n" + pivot_data
                
                return pivot_data
            
        except Exception as e:
            self.logger.error(f"Enhanced CSV text preparation failed: {e}")
            return f"Error preparing enhanced CSV text: {str(e)}"
    
    def build_interpretation_prompt(self, query: str, intent_result, chart_context: Dict, 
                                  data_summary: str, computational_results: Dict) -> str:
        """
        Build LLM prompt focused on interpreting computed results.
        Creates period-specific prompts for time period analysis.
        Creates transition-specific prompts for transition analysis (reference code lines 292-305).
        """
        domain_type = chart_context.get('domain_type', 'General Business') if chart_context else 'General Business'
        
        # Format computational results for LLM
        computed_facts = self._format_computational_results(computational_results)
        
        # Check analysis type
        targeted_analysis = computational_results.get('targeted_analysis', {})
        analysis_type = targeted_analysis.get('analysis_type', 'unknown')
        
        # Handle transition analysis (reference code lines 292-305)
        is_transition_analysis = (analysis_type == 'transition_analysis' and 'transitions' in targeted_analysis)
        
        if is_transition_analysis:
            transitions = targeted_analysis.get('transitions', [])
            missing_periods = targeted_analysis.get('missing_periods')
            features_analyzed = targeted_analysis.get('features_analyzed', [])
            
            # Build transition description
            transition_desc = []
            for trans in transitions:
                from_p = trans.get('from_period', 'Unknown')
                to_p = trans.get('to_period', 'Unknown')
                transition_desc.append(f"{from_p} to {to_p}")
            
            transitions_str = ", ".join(transition_desc)
            
            analysis_prompt = f"""
TRANSITION ANALYTICS INTERPRETATION - {domain_type.upper()} DOMAIN:

USER QUERY: "{query}"
ANALYSIS INTENT: {intent_result.primary_intent if hasattr(intent_result, 'primary_intent') else 'Transition Analysis'}
TRANSITIONS ANALYZED: {transitions_str}
FEATURES ANALYZED: {', '.join(features_analyzed)}
{"MISSING PERIODS: " + ', '.join(missing_periods) if missing_periods else ""}

BUSINESS CONTEXT:
{self._format_chart_context_for_prompt(chart_context) if chart_context else 'No specific business context available'}

PRE-COMPUTED TRANSITION ANALYSIS RESULTS:
{computed_facts}

SPECIALIZED INTERPRETATION INSTRUCTIONS FOR TRANSITION ANALYSIS:

You are analyzing period-to-period transitions in {domain_type} operations. 
All calculations are already done - your job is to interpret the business meaning.

For each transition, provide insights on:
1. **Overall Trend**: What is the overall direction (increase/decrease) and magnitude?
2. **Feature-by-Feature Analysis**: For EACH of the {len(features_analyzed)} features analyzed, explain:
   - Which categories had the largest increases
   - Which categories had the largest decreases
   - What patterns emerge across categories
3. **Cross-Feature Patterns**: Are there common entities appearing in multiple features' top movers?
4. **Business Drivers**: What business factors likely drove these changes?
5. **Operational Insights**: What do these transitions tell us about {domain_type} operations?
6. **Actionable Recommendations**: Based on the transition patterns, what should be monitored or actioned?

CRITICAL GUIDELINES FOR TRANSITION ANALYSIS:
- Use the EXACT computed values and changes provided
- Analyze ALL {len(features_analyzed)} features comprehensively
- Explain WHY the transition occurred based on contributor analysis
- Connect changes across different features to form a coherent narrative
- Provide specific, data-backed insights (not general observations)
- If periods were missing, note this and focus on available data

Your analysis should answer: "What drove the change from period A to period B, and what does this mean for {domain_type} operations?"
"""
            return analysis_prompt
        
        # Check if this is a time period analysis with structured results
        is_time_period_analysis = (analysis_type == 'time_period_analysis' and 
                                 'current' in targeted_analysis)
        
        if is_time_period_analysis:
            # Build period-specific prompt following reference code pattern
            target_period = targeted_analysis.get('target_period', 'Unknown Period')
            sentiment = targeted_analysis.get('sentiment', 'neutral')
            
            analysis_prompt = f"""
PERIOD-SPECIFIC ANALYTICS INTERPRETATION - {domain_type.upper()} DOMAIN:

USER QUERY: "{query}"
ANALYSIS INTENT: {intent_result.primary_intent}
TARGET PERIOD: {target_period}
ANALYSIS FOCUS: {sentiment} analysis for {target_period}

BUSINESS CONTEXT:
{self._format_chart_context_for_prompt(chart_context) if chart_context else 'No specific business context available'}

PRE-COMPUTED PERIOD ANALYSIS RESULTS:
{computed_facts}

SPECIALIZED INTERPRETATION INSTRUCTIONS FOR {target_period.upper()} ANALYSIS:

You are analyzing why {target_period} showed {"high" if sentiment == "high" else "low" if sentiment == "low" else "specific"} values. 
All calculations are already done - your job is to interpret the business meaning.

1. **{target_period} Focus**: Explain what specifically happened in {target_period} based on the computed values
2. **Contributing Factors**: Analyze the top contributors that drove {target_period}'s performance  
3. **Temporal Context**: If Month-over-Month, Quarter-over-Quarter, or Year-over-Year data is available, explain the temporal changes.
4. **Business Impact**: Connect {target_period}'s patterns to {domain_type} operations and decisions
5. **Root Cause Analysis**: Based on the contributor breakdown, identify likely reasons for the {sentiment} performance

CRITICAL GUIDELINES FOR PERIOD ANALYSIS:
- Focus specifically on {target_period} - not general trends across all periods
- Use the exact computed values and percentages provided
- Explain WHY {target_period} was different based on the contributor analysis
- Connect the specific contributors to business operations
- Provide actionable insights for similar periods in the future

Your analysis should answer: "What made {target_period} {"high" if sentiment == "high" else "low" if sentiment == "low" else "notable"} and what business factors contributed to this?"
"""
        else:
            # Build standard interpretation-focused prompt
            analysis_prompt = f"""
ANALYTICS INTERPRETATION REQUEST - {domain_type.upper()} DOMAIN:

USER QUERY: "{query}"
ANALYSIS INTENT: {intent_result.primary_intent}
CONFIDENCE: {intent_result.confidence}

BUSINESS CONTEXT:
{self._format_chart_context_for_prompt(chart_context) if chart_context else 'No specific business context available'}

DATASET OVERVIEW:
{data_summary}

COMPUTED ANALYTICAL FACTS:
{computed_facts}

INTERPRETATION INSTRUCTIONS:
You are provided with PRE-COMPUTED analytical results. Your task is to INTERPRET these facts, not calculate anything new.

1. **Business Interpretation**: Explain what the computed results mean in {domain_type} terms
2. **Key Insights**: Highlight the most significant findings from the computed data
3. **Practical Implications**: Connect findings to business decisions and operational insights
4. **Context Integration**: Use the business domain knowledge to add depth to interpretation

CRITICAL GUIDELINES:
- DO NOT perform any calculations - all numbers are already computed
- DO NOT assume or speculate beyond the provided facts
- Focus on explaining WHY the computed results matter for business
- Use the exact numbers provided in the computed facts
- Provide actionable insights based on the data patterns shown

Provide a clear, business-focused interpretation of the computed analytical results.
"""
        
        return analysis_prompt
    
    def _format_computational_results(self, computational_results: Dict) -> str:
        """
        Format computational results for LLM consumption.
        Enhanced support for both time period analysis and transition analysis.
        """
        if not computational_results.get('success'):
            return f"Computational analysis failed: {computational_results.get('error', 'Unknown error')}"
        
        formatted_results = []
        
        # Add pandas execution results
        pandas_result = computational_results.get('pandas_execution', {})
        if pandas_result and pandas_result.get('execution_status') == 'success':
            formatted_results.append(f"Generated Code: {pandas_result.get('generated_code', 'N/A')}")
            formatted_results.append(f"Operation Type: {pandas_result.get('operation_type', 'N/A')}")
            formatted_results.append(f"Result: {pandas_result.get('result', 'N/A')}")
        
        # Add targeted analysis results with enhanced formatting
        targeted_analysis = computational_results.get('targeted_analysis', {})
        if targeted_analysis and 'error' not in targeted_analysis:
            analysis_type = targeted_analysis.get('analysis_type', 'Unknown')
            formatted_results.append(f"\nTargeted Analysis ({analysis_type}):")
            
            # Handle transition analysis
            if analysis_type == 'transition_analysis':
                transitions = targeted_analysis.get('transitions', [])
                formatted_results.append(f"- Transitions Analyzed: {len(transitions)}")
                
                missing_periods = targeted_analysis.get('missing_periods')
                if missing_periods:
                    formatted_results.append(f"- Missing Periods: {', '.join(missing_periods)}")
                
                for trans_idx, trans in enumerate(transitions):
                    from_period = trans.get('from_period', 'Unknown')
                    to_period = trans.get('to_period', 'Unknown')
                    year_assumption = trans.get('year_assumption', '')
                    
                    formatted_results.append(f"\nTRANSITION {trans_idx + 1}: {from_period} → {to_period}")
                    if year_assumption:
                        formatted_results.append(f"  Year Assumption: {year_assumption}")
                    
                    # Primary feature summary
                    top_increases = trans.get('top_increases', {})
                    top_decreases = trans.get('top_decreases', {})
                    
                    if top_increases:
                        formatted_results.append(f"\n  PRIMARY FEATURE - Top Increases:")
                        for entity, change in list(top_increases.items())[:3]:
                            formatted_results.append(f"    • {entity}: +{change:.0f}")
                    
                    if top_decreases:
                        formatted_results.append(f"\n  PRIMARY FEATURE - Top Decreases:")
                        for entity, change in list(top_decreases.items())[:3]:
                            formatted_results.append(f"    • {entity}: {change:.0f}")
                    
                    # Multi-feature breakdown
                    feature_breakdowns = trans.get('feature_breakdowns', {})
                    if feature_breakdowns:
                        formatted_results.append(f"\n  MULTI-FEATURE BREAKDOWN ({len(feature_breakdowns)} features):")
                        
                        for feature_name, feature_data in feature_breakdowns.items():
                            total_change = feature_data.get('total_change', 0)
                            formatted_results.append(f"\n  {feature_name.upper()}: Total Change = {total_change:+d}")
                            
                            feat_increases = feature_data.get('top_increases', {})
                            feat_decreases = feature_data.get('top_decreases', {})
                            
                            if feat_increases:
                                formatted_results.append(f"    Top Increases:")
                                for category, change in list(feat_increases.items())[:3]:
                                    formatted_results.append(f"      • {category}: +{change:.0f}")
                            
                            if feat_decreases:
                                formatted_results.append(f"    Top Decreases:")
                                for category, change in list(feat_decreases.items())[:3]:
                                    formatted_results.append(f"      • {category}: {change:.0f}")
                
                return "\n".join(formatted_results)
            
            # Handle single period time series analysis (existing logic)
            
            if analysis_type == 'time_period_analysis':
                target_period = targeted_analysis.get('target_period', 'Unknown')
                sentiment = targeted_analysis.get('sentiment', 'neutral')
                
                formatted_results.append(f"- Target Period: {target_period}")
                formatted_results.append(f"- Analysis Focus: {sentiment} analysis")
                
                # Multi-feature breakdown analysis - comprehensive approach
                feature_breakdowns = targeted_analysis.get('feature_breakdowns', {})
                if feature_breakdowns:
                    formatted_results.append(f"\n=== COMPREHENSIVE {target_period.upper()} MULTI-FEATURE BREAKDOWN ===")
                    
                    for feature_name, feature_data in feature_breakdowns.items():
                        formatted_results.append(f"\n{feature_name.upper()} BREAKDOWN ({target_period}):")
                        
                        # Add current breakdown for this feature
                        feature_current = feature_data.get('current', {})
                        if feature_current:
                            for category, value in list(feature_current.items())[:5]:
                                if value > 0:  # Only show non-zero values
                                    formatted_results.append(f"  • {category}: {value}")
                        
                        # Add top contributors for this feature
                        feature_top_contributors = feature_data.get('top_contributors', {})
                        if feature_top_contributors:
                            formatted_results.append(f"\nTop {feature_name.title()} Contributors ({target_period}):")
                            for contributor, data in list(feature_top_contributors.items())[:3]:
                                value = data.get('value', 0)
                                percentage = data.get('percentage', 0)
                                formatted_results.append(f"  • {contributor}: {value} ({percentage:.1f}% of {target_period})")
                
                else:
                    # Fallback to primary feature only if no multi-feature data available
                    current_results = targeted_analysis.get('current', {})
                    if current_results:
                        formatted_results.append(f"\n{target_period.upper()} SPECIFIC VALUES:")
                        for contributor, value in list(current_results.items())[:5]:
                            formatted_results.append(f"  • {contributor}: {value}")
                    
                    # Format top contributors for the specific period
                    top_contributors = targeted_analysis.get('top_contributors', {})
                    if top_contributors:
                        formatted_results.append(f"\nTOP CONTRIBUTORS IN {target_period}:")
                        for contributor, data in list(top_contributors.items())[:5]:
                            value = data.get('value', 0)
                            percentage = data.get('percentage', 0)
                            formatted_results.append(f"  • {contributor}: {value} ({percentage}% of {target_period} total)")

                # Format temporal comparisons (keep this regardless)
                mom_results = targeted_analysis.get('mom', {})
                if mom_results:
                    formatted_results.append(f"\nMONTH-OVER-MONTH CHANGES for {target_period}:")
                    for contributor, change in list(mom_results.items())[:5]:
                        sign = "+" if change >= 0 else ""
                        formatted_results.append(f"  • {contributor}: {sign}{change}")
                
                # ADD THIS: QoQ formatting
                qoq_results = targeted_analysis.get('qoq', {})
                if qoq_results:
                    formatted_results.append(f"\nQUARTER-OVER-QUARTER CHANGES for {target_period}:")
                    for contributor, change in list(qoq_results.items())[:5]:
                        sign = "+" if change >= 0 else ""
                        formatted_results.append(f"  • {contributor}: {sign}{change}")
                
                # ADD THIS: YoY formatting  
                yoy_results = targeted_analysis.get('yoy', {})
                if yoy_results:
                    formatted_results.append(f"\nYEAR-OVER-YEAR CHANGES for {target_period}:")
                    for contributor, change in list(yoy_results.items())[:5]:
                        sign = "+" if change >= 0 else ""
                        formatted_results.append(f"  • {contributor}: {sign}{change}")

                # Format summary for the target period
                summary = targeted_analysis.get('target_period_summary', {})
                if summary:
                    total_value = summary.get('total_value', 0)
                    max_contributor = summary.get('max_contributor', 'Unknown')
                    max_value = summary.get('max_value', 0)
                    formatted_results.append(f"\n{target_period.upper()} SUMMARY:")
                    formatted_results.append(f"  • Total Value: {total_value}")
                    formatted_results.append(f"  • Top Contributor: {max_contributor} ({max_value})")
            
            elif analysis_type == 'top_contributors_analysis':
                contributors = targeted_analysis.get('contributors_by_feature', {})
                for feature, data in contributors.items():
                    formatted_results.append(f"- {feature}: Top contributor = {data.get('top_contributor')} ({data.get('top_value')} occurrences, {data.get('top_percentage')}%)")
        
        # Add computed statistics
        statistics = computational_results.get('computed_statistics', {})
        if statistics:
            formatted_results.append("\nComputed Statistics:")
            for col, stats in list(statistics.items())[:5]:  # Limit output
                if isinstance(stats, dict):
                    if 'top_category' in stats:
                        formatted_results.append(f"- {col}: Top category = {stats['top_category']} ({stats['top_count']} occurrences, {stats['top_percentage']}%)")
                    elif 'max_value' in stats:
                        formatted_results.append(f"- {col}: Max = {stats['max_value']}, Mean = {stats['mean_value']:.2f}, Sum = {stats['sum_value']}")
        
        return "\n".join(formatted_results) if formatted_results else "No computational results available"
    
    def _format_chart_context_for_prompt(self, chart_context: Dict) -> str:
        """
        Format chart context for the interpretation prompt.
        """
        if not chart_context:
            return "No chart context available"
        
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

TOP IMPACTFUL FEATURES (with expert reasoning):
{chr(10).join(feature_reasoning) if feature_reasoning else 'No feature reasoning available'}

ANALYSIS TIMESTAMP: {chart_context.get('timestamp', 'Unknown')}
"""
        return context_summary
