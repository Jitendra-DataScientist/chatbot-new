"""
Temporal Validator - Period Resolution and Validation

NO HARDCODING:
- Resolves periods based on actual data
- Validates against actual date ranges
- Generates specific error messages with data context
- Creates dynamic period filters

Author: Temporal Comparison Analysis Module
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
import logging
import calendar


class TemporalValidator:
    """
    Validates and resolves temporal entities against actual data
    """
    
    def __init__(self, temporal_entities: Dict[str, Any], df: pd.DataFrame,
                 date_column: str, logger=None):
        """
        Initialize validator
        
        Args:
            temporal_entities: Extracted temporal entities from extractor
            df: DataFrame with data
            date_column: Name of date column
            logger: Logger instance
        """
        self.temporal_entities = temporal_entities
        self.df = df
        self.date_column = date_column
        self.logger = logger or logging.getLogger(__name__)
        
        self.date_analysis = None
        self.parsed_dates = None
    
    def validate_and_resolve(self) -> Dict[str, Any]:
        """
        Main validation and resolution method
        
        Returns:
            Dictionary with validation results and period filters
        """
        try:
            # Step 1: Get date analysis from temporal entities
            if 'date_analysis' in self.temporal_entities:
                self.date_analysis = self.temporal_entities['date_analysis']
            else:
                # Run date analysis if not provided
                from services.date_analyzer import analyze_date_column
                self.date_analysis = analyze_date_column(self.df, self.date_column)
            
            if not self.date_analysis['valid']:
                return self._error_response('INVALID_DATE_COLUMN', 
                                           'Date column analysis failed')
            
            # Parse dates for filtering
            self.parsed_dates = pd.to_datetime(self.df[self.date_column])
            
            # Step 2: Extract periods from entities
            period1_spec = self.temporal_entities.get('period1', {})
            period2_spec = self.temporal_entities.get('period2', {})
            comparison_type = self.temporal_entities.get('comparison_type', 'explicit')
            
            # Step 3: Resolve period2 (the target period)
            period2_resolved = self._resolve_period(period2_spec, is_target_period=True)
            
            if not period2_resolved['success']:
                return self._error_response(period2_resolved['error_type'],
                                           period2_resolved['message'],
                                           period2_resolved.get('corrections', []))
            
            # Step 4: Resolve period1 (the comparison period)
            if comparison_type == 'sequential' and not period1_spec:
                # Infer previous period
                period1_resolved = self._infer_previous_period(period2_resolved)
            else:
                period1_resolved = self._resolve_period(period1_spec, is_target_period=False)
            
            if not period1_resolved['success']:
                return self._error_response(period1_resolved['error_type'],
                                           period1_resolved['message'],
                                           period1_resolved.get('corrections', []))
            
            # Step 5: Create period filters
            period1_filter = self._create_period_filter(period1_resolved)
            period2_filter = self._create_period_filter(period2_resolved)
            
            # Step 6: Validate data sufficiency
            period1_count = period1_filter.sum()
            period2_count = period2_filter.sum()
            
            if period1_count < 10 or period2_count < 10:
                return self._error_response(
                    'INSUFFICIENT_DATA',
                    f"⚠️ Not enough data in one or both periods.\n"
                    f"   • {period1_resolved['label']}: {period1_count} records\n"
                    f"   • {period2_resolved['label']}: {period2_count} records\n"
                    f"   Need at least 10 records per period for meaningful analysis."
                )
            
            # Step 7: Success - return validated periods with filters
            self.logger.info(f"✓ Temporal validation passed:")
            self.logger.info(f"   Period 1: {period1_resolved['label']} ({period1_count} records)")
            self.logger.info(f"   Period 2: {period2_resolved['label']} ({period2_count} records)")
            
            return {
                'success': True,
                'period1_filter': period1_filter,
                'period2_filter': period2_filter,
                'period1_label': period1_resolved['label'],
                'period2_label': period2_resolved['label'],
                'period1_count': int(period1_count),
                'period2_count': int(period2_count),
                'period1_details': period1_resolved,
                'period2_details': period2_resolved,
                'comparison_context': f"Comparing {period2_resolved['label']} vs {period1_resolved['label']}"
            }
            
        except Exception as e:
            self.logger.error(f"Temporal validation error: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return self._error_response('VALIDATION_ERROR', str(e))
    
    def _resolve_period(self, period_spec: Dict[str, Any], 
                       is_target_period: bool = False) -> Dict[str, Any]:
        """
        Resolve period specification to actual date range
        
        Args:
            period_spec: Period specification from extractor
            is_target_period: True if this is the target period (period2)
        
        Returns:
            Resolved period details or error
        """
        try:
            if not period_spec:
                return {
                    'success': False,
                    'error_type': 'MISSING_PERIOD',
                    'message': 'Period specification missing'
                }
            
            # Handle None or empty text gracefully
            period_text = period_spec.get('text') or ''
            period_text = period_text.lower() if period_text else ''
            
            # If period_text is empty after cleaning, return error
            if not period_text:
                return {
                    'success': False,
                    'error_type': 'MISSING_PERIOD_TEXT',
                    'message': 'Period text is empty or None - cannot resolve period'
                }
            
            period_type = period_spec.get('type', self.date_analysis['granularity'])
            year = period_spec.get('year')
            month = period_spec.get('month')
            week = period_spec.get('week')
            quarter = period_spec.get('quarter')
            
            # Resolve year (use latest if not specified)
            if not year:
                year = self.date_analysis['latest_year']
                self.logger.info(f"No year specified, using latest: {year}")
            
            # Check if year exists in data
            if year not in self.date_analysis['years']:
                available_years = ', '.join(str(y) for y in self.date_analysis['years'])
                return {
                    'success': False,
                    'error_type': 'PERIOD_NOT_FOUND',
                    'message': f"⚠️ Year {year} not found in data.\n"
                              f"   Available years: {available_years}",
                    'corrections': [f"Use one of the available years: {available_years}"]
                }
            
            # Resolve based on period type
            if period_type == 'monthly' or month:
                return self._resolve_month_period(year, month, period_text)
            elif period_type == 'weekly' or week:
                return self._resolve_week_period(year, week, period_text)
            elif period_type == 'quarterly' or quarter:
                return self._resolve_quarter_period(year, quarter, period_text)
            elif period_type == 'yearly':
                return self._resolve_year_period(year)
            else:
                # Try to infer from text
                return self._resolve_from_text(period_text, year)
        
        except Exception as e:
            return {
                'success': False,
                'error_type': 'RESOLUTION_ERROR',
                'message': f"Error resolving period: {str(e)}"
            }
    
    def _resolve_month_period(self, year: int, month: Optional[int], 
                             period_text: str) -> Dict[str, Any]:
        """
        Resolve monthly period
        """
        # Map month names to numbers
        month_map = {
            'january': 1, 'jan': 1, 'february': 2, 'feb': 2, 'march': 3, 'mar': 3,
            'april': 4, 'apr': 4, 'may': 5, 'june': 6, 'jun': 6,
            'july': 7, 'jul': 7, 'august': 8, 'aug': 8, 'september': 9, 'sep': 9,
            'october': 10, 'oct': 10, 'november': 11, 'nov': 11, 'december': 12, 'dec': 12
        }
        
        # Extract month from text if not provided
        if not month:
            for month_name, month_num in month_map.items():
                if month_name in period_text:
                    month = month_num
                    break
        
        if not month:
            return {
                'success': False,
                'error_type': 'INVALID_PERIOD',
                'message': f"Could not determine month from '{period_text}'"
            }
        
        # Check if month exists in data for this year
        if 'months_by_year' in self.date_analysis:
            available_months = self.date_analysis['months_by_year'].get(str(year), [])
            if month not in available_months:
                month_names = [calendar.month_name[m] for m in available_months]
                return {
                    'success': False,
                    'error_type': 'PERIOD_NOT_FOUND',
                    'message': f"⚠️ {calendar.month_name[month]} {year} not found in data.\n"
                              f"   Available months in {year}: {', '.join(month_names)}",
                    'corrections': [f"Use one of the available months: {', '.join(month_names)}"]
                }
        
        # Create date range for the month
        start_date = pd.Timestamp(year=year, month=month, day=1)
        
        # Last day of month
        last_day = calendar.monthrange(year, month)[1]
        end_date = pd.Timestamp(year=year, month=month, day=last_day)
        
        return {
            'success': True,
            'type': 'monthly',
            'year': year,
            'month': month,
            'start_date': start_date,
            'end_date': end_date,
            'label': f"{calendar.month_name[month]} {year}"
        }
    
    def _resolve_week_period(self, year: int, week: Optional[int],
                            period_text: str) -> Dict[str, Any]:
        """
        Resolve weekly period
        """
        import re
        
        # Extract week number from text if not provided
        if not week:
            week_match = re.search(r'week\s*(\d+)', period_text)
            if week_match:
                week = int(week_match.group(1))
        
        if not week:
            return {
                'success': False,
                'error_type': 'INVALID_PERIOD',
                'message': f"Could not determine week number from '{period_text}'"
            }
        
        # Check if week exists in data for this year
        if 'weeks_by_year' in self.date_analysis:
            available_weeks = self.date_analysis['weeks_by_year'].get(str(year), [])
            if week not in available_weeks:
                return {
                    'success': False,
                    'error_type': 'PERIOD_NOT_FOUND',
                    'message': f"⚠️ Week {week} of {year} not found in data.\n"
                              f"   Available weeks in {year}: {min(available_weeks)}-{max(available_weeks)}",
                    'corrections': [f"Use a week between {min(available_weeks)} and {max(available_weeks)}"]
                }
        
        # Get date range for the week
        # ISO week starts on Monday
        jan_4 = pd.Timestamp(year=year, month=1, day=4)
        week_start = jan_4 + pd.Timedelta(weeks=week-1) - pd.Timedelta(days=jan_4.dayofweek)
        week_end = week_start + pd.Timedelta(days=6)
        
        return {
            'success': True,
            'type': 'weekly',
            'year': year,
            'week': week,
            'start_date': week_start,
            'end_date': week_end,
            'label': f"Week {week} {year}"
        }
    
    def _resolve_quarter_period(self, year: int, quarter: Optional[int],
                               period_text: str) -> Dict[str, Any]:
        """
        Resolve quarterly period
        """
        import re
        
        # Extract quarter from text if not provided
        if not quarter:
            quarter_match = re.search(r'q(\d)', period_text.lower())
            if quarter_match:
                quarter = int(quarter_match.group(1))
        
        if not quarter or quarter not in [1, 2, 3, 4]:
            return {
                'success': False,
                'error_type': 'INVALID_PERIOD',
                'message': f"Invalid quarter specification in '{period_text}'"
            }
        
        # Check if quarter exists in data
        if 'quarters_by_year' in self.date_analysis:
            available_quarters = self.date_analysis['quarters_by_year'].get(str(year), [])
            if quarter not in available_quarters:
                return {
                    'success': False,
                    'error_type': 'PERIOD_NOT_FOUND',
                    'message': f"⚠️ Q{quarter} {year} not found in data.\n"
                              f"   Available quarters in {year}: Q{', Q'.join(map(str, available_quarters))}",
                    'corrections': [f"Use one of: Q{', Q'.join(map(str, available_quarters))}"]
                }
        
        # Map quarter to month range
        quarter_months = {
            1: (1, 3),
            2: (4, 6),
            3: (7, 9),
            4: (10, 12)
        }
        
        start_month, end_month = quarter_months[quarter]
        start_date = pd.Timestamp(year=year, month=start_month, day=1)
        end_date = pd.Timestamp(year=year, month=end_month, day=calendar.monthrange(year, end_month)[1])
        
        return {
            'success': True,
            'type': 'quarterly',
            'year': year,
            'quarter': quarter,
            'start_date': start_date,
            'end_date': end_date,
            'label': f"Q{quarter} {year}"
        }
    
    def _resolve_year_period(self, year: int) -> Dict[str, Any]:
        """
        Resolve yearly period
        """
        start_date = pd.Timestamp(year=year, month=1, day=1)
        end_date = pd.Timestamp(year=year, month=12, day=31)
        
        return {
            'success': True,
            'type': 'yearly',
            'year': year,
            'start_date': start_date,
            'end_date': end_date,
            'label': str(year)
        }
    
    def _resolve_from_text(self, period_text: str, year: int) -> Dict[str, Any]:
        """
        Try to resolve period from text when type is ambiguous
        """
        # Try month first
        month_result = self._resolve_month_period(year, None, period_text)
        if month_result['success']:
            return month_result
        
        # Try week
        week_result = self._resolve_week_period(year, None, period_text)
        if week_result['success']:
            return week_result
        
        # Try quarter
        quarter_result = self._resolve_quarter_period(year, None, period_text)
        if quarter_result['success']:
            return quarter_result
        
        return {
            'success': False,
            'error_type': 'AMBIGUOUS_PERIOD',
            'message': f"Could not determine period type from '{period_text}'"
        }
    
    def _infer_previous_period(self, period_resolved: Dict[str, Any]) -> Dict[str, Any]:
        """
        Infer previous period for sequential comparison
        """
        period_type = period_resolved['type']
        year = period_resolved['year']
        
        if period_type == 'monthly':
            month = period_resolved['month']
            prev_month = month - 1
            prev_year = year
            
            if prev_month < 1:
                prev_month = 12
                prev_year = year - 1
            
            return self._resolve_month_period(prev_year, prev_month, 
                                             f"{calendar.month_name[prev_month]} {prev_year}")
        
        elif period_type == 'weekly':
            week = period_resolved['week']
            prev_week = week - 1
            prev_year = year
            
            if prev_week < 1:
                prev_week = 52
                prev_year = year - 1
            
            return self._resolve_week_period(prev_year, prev_week, f"week {prev_week}")
        
        elif period_type == 'quarterly':
            quarter = period_resolved['quarter']
            prev_quarter = quarter - 1
            prev_year = year
            
            if prev_quarter < 1:
                prev_quarter = 4
                prev_year = year - 1
            
            return self._resolve_quarter_period(prev_year, prev_quarter, f"Q{prev_quarter}")
        
        elif period_type == 'yearly':
            prev_year = year - 1
            return self._resolve_year_period(prev_year)
        
        else:
            return {
                'success': False,
                'error_type': 'CANNOT_INFER_PREVIOUS',
                'message': f"Cannot infer previous period for type '{period_type}'"
            }
    
    def _create_period_filter(self, period_resolved: Dict[str, Any]) -> pd.Series:
        """
        Create boolean filter for period (works with any granularity)
        """
        start_date = period_resolved['start_date']
        end_date = period_resolved['end_date']
        
        # Create filter
        filter_mask = (self.parsed_dates >= start_date) & (self.parsed_dates <= end_date)
        
        return filter_mask
    
    def _error_response(self, error_type: str, message: str, 
                       corrections: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Standard error response with context
        """
        return {
            'success': False,
            'error_type': error_type,
            'message': message,
            'corrections': corrections or [],
            'available_periods': self._list_available_periods()
        }
    
    def _list_available_periods(self) -> Dict[str, Any]:
        """
        List available periods from data for user guidance
        """
        result = {
            'years': self.date_analysis.get('years', []),
            'date_range': {
                'min': str(self.date_analysis.get('min_date', '')),
                'max': str(self.date_analysis.get('max_date', ''))
            },
            'granularity': self.date_analysis.get('granularity', 'unknown')
        }
        
        # Add specific periods based on granularity
        if self.date_analysis.get('granularity') == 'monthly':
            result['months_by_year'] = self.date_analysis.get('months_by_year', {})
        elif self.date_analysis.get('granularity') == 'weekly':
            result['weeks_by_year'] = self.date_analysis.get('weeks_by_year', {})
        elif self.date_analysis.get('granularity') == 'quarterly':
            result['quarters_by_year'] = self.date_analysis.get('quarters_by_year', {})
        
        return result

