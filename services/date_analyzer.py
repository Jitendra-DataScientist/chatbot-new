"""
Date Analyzer - Dynamic Date Format and Granularity Detection

NO HARDCODING:
- Detects date formats from actual data samples
- Determines granularity from actual date gaps
- Builds available periods from actual data
- All decisions based on data analysis, not assumptions

Author: Temporal Comparison Analysis Module
"""

import pandas as pd
import numpy as np
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import logging


class DateAnalyzer:
    """
    Analyzes date columns dynamically - zero hardcoding
    """
    
    def __init__(self, df: pd.DataFrame, date_column: str, llm_client=None, logger=None):
        """
        Initialize date analyzer
        
        Args:
            df: DataFrame with date data
            date_column: Name of the date column to analyze
            llm_client: OpenAI client for format detection
            logger: Logger instance
        """
        self.df = df
        self.date_column = date_column
        self.llm_client = llm_client
        self.logger = logger or logging.getLogger(__name__)
        
        self.parsed_dates = None
        self.format_info = None
        self.granularity = None
    
    def analyze(self) -> Dict[str, Any]:
        """
        Main analysis method - detects everything dynamically
        
        Returns:
            Dictionary with date analysis results
        """
        try:
            # === DIAGNOSTIC LOGGING START ===
            self.logger.info(f"[ANALYZER_DEBUG] analyze() called")
            self.logger.info(f"[ANALYZER_DEBUG] date_column: '{self.date_column}'")
            self.logger.info(f"[ANALYZER_DEBUG] df.shape: {self.df.shape}")
            self.logger.info(f"[ANALYZER_DEBUG] date_column in df.columns: {self.date_column in self.df.columns}")
            if self.date_column in self.df.columns:
                self.logger.info(f"[ANALYZER_DEBUG] df['{self.date_column}'].dtype: {self.df[self.date_column].dtype}")
                self.logger.info(f"[ANALYZER_DEBUG] df['{self.date_column}'].shape: {self.df[self.date_column].shape}")
                self.logger.info(f"[ANALYZER_DEBUG] df['{self.date_column}'].isna().sum(): {self.df[self.date_column].isna().sum()}")
                self.logger.info(f"[ANALYZER_DEBUG] df['{self.date_column}'].head(3): {self.df[self.date_column].head(3).tolist()}")
            else:
                self.logger.error(f"[ANALYZER_DEBUG] date_column '{self.date_column}' NOT IN df.columns!")
                self.logger.error(f"[ANALYZER_DEBUG] Available columns: {list(self.df.columns)[:20]}")
            self.logger.info(f"[ANALYZER_DEBUG] llm_client is None: {self.llm_client is None}")
            # === DIAGNOSTIC LOGGING END ===
            
            # Step 1: Detect format from actual data
            self.logger.info(f"[ANALYZER_DEBUG] Step 1: Calling _detect_format()...")
            self.format_info = self._detect_format()
            self.logger.info(f"[ANALYZER_DEBUG] _detect_format() returned: success={self.format_info.get('success')}")
            
            if not self.format_info['success']:
                error_msg = f"Format detection failed: {self.format_info.get('error')}"
                self.logger.error(f"[ANALYZER_DEBUG] {error_msg}")
                return self._error_response(error_msg)
            
            # Step 2: Parse dates using detected format
            self.logger.info(f"[ANALYZER_DEBUG] Step 2: Calling _parse_dates()...")
            self.parsed_dates = self._parse_dates()
            self.logger.info(f"[ANALYZER_DEBUG] _parse_dates() returned, is None: {self.parsed_dates is None}")
            
            if self.parsed_dates is None or len(self.parsed_dates) == 0:
                error_msg = "Date parsing failed"
                self.logger.error(f"[ANALYZER_DEBUG] {error_msg}")
                return self._error_response(error_msg)
            
            # Step 3: Detect actual granularity from data
            self.logger.info(f"[ANALYZER_DEBUG] Step 3: Calling _detect_granularity()...")
            self.granularity = self._detect_granularity()
            self.logger.info(f"[ANALYZER_DEBUG] _detect_granularity() returned: {self.granularity}")
            
            # Step 4: Extract available periods from data
            periods_info = self._extract_available_periods()
            
            # Step 5: Build comprehensive analysis
            result = {
                'valid': True,
                'date_column': self.date_column,
                'format_detected': self.format_info,
                'granularity': self.granularity,
                'min_date': self.parsed_dates.min(),
                'max_date': self.parsed_dates.max(),
                'latest_year': int(self.parsed_dates.dt.year.max()),
                'earliest_year': int(self.parsed_dates.dt.year.min()),
                'years': sorted([int(y) for y in self.parsed_dates.dt.year.unique()]),
                'total_records': len(self.df),
                'date_range_days': (self.parsed_dates.max() - self.parsed_dates.min()).days,
                **periods_info
            }
            
            self.logger.info(f"✓ Date analysis complete: {self.granularity} granularity, "
                           f"{result['min_date'].date()} to {result['max_date'].date()}")
            
            return result
            
        except Exception as e:
            self.logger.error(f"[ANALYZER_DEBUG] Exception in analyze(): {e}")
            self.logger.error(f"[ANALYZER_DEBUG] Exception type: {type(e).__name__}")
            import traceback
            self.logger.error(f"[ANALYZER_DEBUG] Full traceback:")
            self.logger.error(traceback.format_exc())
            return self._error_response(str(e))
    
    def _detect_format(self) -> Dict[str, Any]:
        """
        Detect date format from actual data samples - NO HARDCODING
        Uses LLM to identify format from examples
        """
        try:
            # Get sample values from actual data
            sample_values = self.df[self.date_column].dropna().head(50).tolist()
            
            if len(sample_values) == 0:
                return {'success': False, 'error': 'No non-null values in date column'}
            
            # Convert samples to strings for analysis
            sample_strings = [str(v) for v in sample_values[:20]]
            
            # Use LLM to detect format (no assumptions)
            if self.llm_client:
                format_info = self._detect_format_with_llm(sample_strings)
                if format_info['success']:
                    return format_info
            
            # Fallback: Let pandas infer (still no hardcoding)
            self.logger.warning("LLM format detection unavailable, using pandas inference")
            return {
                'success': True,
                'type': 'inferred',
                'format': 'auto',
                'has_year': True,
                'has_month': True,
                'has_day': True,
                'method': 'pandas_infer'
            }
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def _detect_format_with_llm(self, sample_strings: List[str]) -> Dict[str, Any]:
        """
        Use LLM to detect date format from samples
        """
        prompt = f"""
Analyze these date samples and determine the format.

SAMPLES:
{chr(10).join(sample_strings)}

TASK:
1. Identify the date format type
2. Determine what components are present (year, month, day, week, quarter)
3. Provide a format string if applicable
4. Be specific about the pattern

IMPORTANT: Base your answer ONLY on these actual samples. Do not assume.

Return JSON:
{{
    "success": true,
    "type": "datetime|year_only|month_year|week_year|quarter_year|custom",
    "format": "detected format string (e.g., %Y-%m-%d) or 'auto' if complex",
    "has_year": true/false,
    "has_month": true/false,
    "has_day": true/false,
    "has_week": true/false,
    "has_quarter": true/false,
    "pattern_description": "human readable description",
    "examples_parsed": ["how first 3 samples would be parsed"],
    "confidence": 0.0-1.0
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
            result['method'] = 'llm_detected'
            return result
            
        except Exception as e:
            self.logger.error(f"LLM format detection failed: {e}")
            return {'success': False, 'error': str(e)}
    
    def _parse_dates(self) -> Optional[pd.Series]:
        """
        Parse dates using detected format - handles any format dynamically
        """
        try:
            format_type = self.format_info.get('type', 'inferred')
            format_string = self.format_info.get('format', 'auto')
            
            # Try to parse with detected format
            if format_string != 'auto' and format_string is not None:
                try:
                    dates = pd.to_datetime(self.df[self.date_column], format=format_string)
                    self.logger.info(f"✓ Dates parsed with format: {format_string}")
                    return dates
                except:
                    self.logger.warning(f"Format string {format_string} failed, falling back to auto")
            
            # Fallback: pandas auto-inference (still works for any format)
            dates = pd.to_datetime(self.df[self.date_column], infer_datetime_format=True, errors='coerce')
            
            # Check how many parsed successfully
            null_count = dates.isna().sum()
            if null_count > len(dates) * 0.1:  # More than 10% failed
                self.logger.warning(f"Warning: {null_count}/{len(dates)} dates failed to parse")
            
            self.logger.info(f"✓ Dates parsed with pandas auto-inference")
            return dates
            
        except Exception as e:
            self.logger.error(f"Date parsing failed: {e}")
            return None
    
    def _detect_granularity(self) -> str:
        """
        Detect actual granularity from data - NO ASSUMPTIONS
        
        Handles edge case: dates that look daily but are actually monthly
        (e.g., 2025-04-01, 2025-05-01, 2025-06-01 are MONTHLY not DAILY)
        """
        try:
            dates = self.parsed_dates.dropna().sort_values()
            
            if len(dates) < 2:
                return 'unknown'
            
            # Check 1: Are all dates on 1st of month?
            if self.format_info.get('has_day'):
                days = dates.dt.day
                if (days == 1).sum() / len(days) > 0.9:  # 90%+ are 1st of month
                    self.logger.info("Detected: Dates are 1st of month → MONTHLY granularity")
                    return 'monthly'
            
            # Check 2: Are all dates on same weekday (e.g., all Mondays)?
            if self.format_info.get('has_day'):
                weekdays = dates.dt.dayofweek
                unique_weekdays = weekdays.nunique()
                if unique_weekdays == 1:
                    weekday_name = dates.dt.day_name().iloc[0]
                    self.logger.info(f"Detected: All dates are {weekday_name} → WEEKLY granularity")
                    return 'weekly'
            
            # Check 3: Calculate time gaps between dates
            time_gaps = dates.diff().dropna()
            median_gap = time_gaps.median()
            
            # Convert to days for comparison
            median_days = median_gap.days
            
            if median_days <= 1.5:
                return 'daily'
            elif median_days <= 8:  # ~7 days
                return 'weekly'
            elif median_days <= 35:  # ~30 days
                return 'monthly'
            elif median_days <= 100:  # ~90 days
                return 'quarterly'
            elif median_days <= 370:  # ~365 days
                return 'yearly'
            else:
                return 'irregular'
            
        except Exception as e:
            self.logger.error(f"Granularity detection failed: {e}")
            return 'unknown'
    
    def _extract_available_periods(self) -> Dict[str, Any]:
        """
        Extract available periods from actual data - completely dynamic
        """
        try:
            dates = self.parsed_dates.dropna()
            
            result = {}
            
            # Extract years (always available)
            result['available_years'] = sorted([int(y) for y in dates.dt.year.unique()])
            
            # Extract months by year (if has month info)
            if self.format_info.get('has_month'):
                months_by_year = {}
                for year in result['available_years']:
                    year_dates = dates[dates.dt.year == year]
                    months = sorted([int(m) for m in year_dates.dt.month.unique()])
                    months_by_year[str(year)] = months
                result['months_by_year'] = months_by_year
            
            # Extract weeks by year (if has week info)
            if self.format_info.get('has_day'):  # Can calculate weeks if we have day
                weeks_by_year = {}
                for year in result['available_years']:
                    year_dates = dates[dates.dt.year == year]
                    weeks = sorted([int(w) for w in year_dates.dt.isocalendar().week.unique()])
                    weeks_by_year[str(year)] = weeks
                result['weeks_by_year'] = weeks_by_year
            
            # Extract quarters by year
            if self.format_info.get('has_month'):
                quarters_by_year = {}
                for year in result['available_years']:
                    year_dates = dates[dates.dt.year == year]
                    quarters = sorted([int(q) for q in year_dates.dt.quarter.unique()])
                    quarters_by_year[str(year)] = quarters
                result['quarters_by_year'] = quarters_by_year
            
            # Count records per period (for validation)
            result['records_per_year'] = {}
            for year in result['available_years']:
                count = (dates.dt.year == year).sum()
                result['records_per_year'][str(year)] = int(count)
            
            return result
            
        except Exception as e:
            self.logger.error(f"Period extraction failed: {e}")
            return {}
    
    def _error_response(self, error_message: str) -> Dict[str, Any]:
        """
        Standard error response
        """
        return {
            'valid': False,
            'error': error_message,
            'date_column': self.date_column
        }


def analyze_date_column(df: pd.DataFrame, date_column: str, 
                        llm_client=None, logger=None) -> Dict[str, Any]:
    """
    Convenience function to analyze date column
    
    Args:
        df: DataFrame with date data
        date_column: Name of date column
        llm_client: Optional OpenAI client for format detection
        logger: Optional logger
    
    Returns:
        Dictionary with comprehensive date analysis
    """
    analyzer = DateAnalyzer(df, date_column, llm_client, logger)
    return analyzer.analyze()

