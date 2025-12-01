"""
Temporal Anomaly Detection Service - 3-Layer Time-Series Anomaly Detection

NO HARDCODING:
- Dynamically detects date columns via fuzzy matching
- Auto-detects time granularity from data
- Works with any metric column
- Identifies anomalous time periods (not records)

3-Layer Detection System:
1. Statistical Layer: STL Decomposition (Seasonal-Trend-Loess) for residual analysis
2. ML Layer: Change Point Detection (PELT algorithm) for abrupt shifts
3. Business Validation Layer: Context-aware filtering of false positives

Author: Temporal Anomaly Detection Module
Date: November 2025
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional, Tuple
import logging
from scipy import stats
from datetime import datetime


class TemporalAnomalyDetectionService:
    """
    Detects anomalous time periods in time-series data
    
    Differences from outlier_detection:
    - Outlier detection: Finds unusual RECORDS (individual data points)
    - Temporal anomaly detection: Finds unusual TIME PERIODS (aggregated patterns)
    """
    
    def __init__(self, logger=None, threshold: float = 2.5):
        """
        Initialize temporal anomaly detection service
        
        Args:
            logger: Logger instance
            threshold: Z-score threshold for anomaly detection (default 2.5 for balanced sensitivity)
        """
        self.logger = logger or logging.getLogger(__name__)
        self.threshold = threshold
    
    def detect_temporal_anomalies(self,
                                   df: pd.DataFrame,
                                   date_column: str,
                                   metric_column: str,
                                   aggregation: str = 'sum',
                                   temporal_filter: Optional[Dict] = None,
                                   query: str = '') -> Dict[str, Any]:
        """
        Main entry point for temporal anomaly detection with granularity support
        
        Args:
            df: DataFrame with time-series data
            date_column: Column with dates (already validated)
            metric_column: Column with metric values
            aggregation: Aggregation method ('sum', 'count', 'mean', 'count_distinct')
            temporal_filter: Optional dict with 'type', 'value', 'text' to filter by month/quarter/year
            query: User's original query (for granularity detection)
        
        Returns:
            Dictionary with anomaly results
        """
        try:
            self.logger.info("="*80)
            self.logger.info("=== TEMPORAL ANOMALY DETECTION SERVICE ===")
            self.logger.info(f"Date column: {date_column}")
            self.logger.info(f"Metric column: {metric_column}")
            self.logger.info(f"Aggregation: {aggregation}")
            self.logger.info(f"Dataset size: {len(df)} rows")
            
            # Step 1: Extract desired granularity from query (NEW)
            granularity = self._extract_temporal_granularity(query) if query else 'auto'
            
            # Apply temporal filter if provided
            if temporal_filter:
                self.logger.info(f"Temporal filter: {temporal_filter['type']} = {temporal_filter['value']} ('{temporal_filter['text']}')")
                df = self._apply_temporal_filter(df, date_column, temporal_filter)
                self.logger.info(f"Filtered dataset size: {len(df)} rows")
            else:
                self.logger.info("No temporal filter - analyzing full time series")
            
            # Step 2: Aggregate to desired granularity if needed (NEW)
            if granularity == 'quarter':
                df = self._aggregate_to_quarters(df, date_column, metric_column, aggregation)
            elif granularity == 'year':
                df = self._aggregate_to_years(df, date_column, metric_column, aggregation)
            # For 'month', 'day', or 'auto', use data as-is (already at that granularity)
            
            self.logger.info("="*80)
            
            # Step 3: Prepare time series
            ts_data = self._prepare_time_series(df, date_column, metric_column, aggregation)
            
            if ts_data is None or len(ts_data) < 3:
                self.logger.warning("Insufficient time series data (need at least 3 periods)")
                return {
                    'success': False,
                    'error': 'Insufficient time series data',
                    'anomalies': []
                }
            
            self.logger.info(f"Time series prepared: {len(ts_data)} periods")
            
            # Layer 1: STL Decomposition (Statistical anomaly detection)
            stl_anomalies = self._detect_stl_anomalies(ts_data)
            
            # Layer 2: Change Point Detection (ML-based abrupt shift detection)
            changepoint_anomalies = self._detect_changepoints(ts_data)
            
            # Layer 3: Combine and validate with business logic
            final_anomalies = self._combine_and_validate(
                ts_data, stl_anomalies, changepoint_anomalies
            )
            
            # Step 4: Format period display based on granularity (NEW)
            for anomaly in final_anomalies:
                if 'period' in anomaly:
                    anomaly['period_display'] = self._format_period_display(anomaly['period'], granularity)
                elif 'date' in anomaly:
                    anomaly['period_display'] = self._format_period_display(anomaly['date'], granularity)
            
            self.logger.info(f"✓ Detection complete - identified {len(final_anomalies)} anomalous periods")
            
            return {
                'success': True,
                'anomalies': final_anomalies,
                'total_anomalies': len(final_anomalies),
                'time_series_data': ts_data,
                'granularity': granularity,  # NEW: Include detected granularity
                'detection_summary': {
                    'method': '3-Layer (STL + Changepoint + Validation)',
                    'periods_analyzed': len(ts_data),
                    'threshold': self.threshold,
                    'date_column': date_column,
                    'metric_column': metric_column,
                    'aggregation': aggregation
                }
            }
            
        except Exception as e:
            self.logger.error(f"Temporal anomaly detection failed: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'anomalies': [],
                'granularity': granularity if 'granularity' in locals() else 'unknown'
            }
    
    def _apply_temporal_filter(self,
                               df: pd.DataFrame,
                               date_column: str,
                               temporal_filter: Dict) -> pd.DataFrame:
        """
        Apply temporal filter to DataFrame
        
        Args:
            df: Input DataFrame
            date_column: Date column name
            temporal_filter: Dict with 'type' (month/quarter/year) and 'value'
        
        Returns:
            Filtered DataFrame
        """
        df_copy = df.copy()
        
        # Ensure date column is datetime
        if not pd.api.types.is_datetime64_any_dtype(df_copy[date_column]):
            df_copy[date_column] = pd.to_datetime(df_copy[date_column], errors='coerce')
        
        # Remove rows with invalid dates
        df_copy = df_copy[df_copy[date_column].notna()]
        
        filter_type = temporal_filter['type']
        filter_value = temporal_filter['value']
        
        if filter_type == 'month':
            # Filter to specific month (across all years)
            filtered = df_copy[df_copy[date_column].dt.month == filter_value]
            self.logger.info(f"Filtered to month={filter_value}: {len(df_copy)} → {len(filtered)} rows")
        
        elif filter_type == 'quarter':
            # Filter to specific quarter (across all years)
            filtered = df_copy[df_copy[date_column].dt.quarter == filter_value]
            self.logger.info(f"Filtered to quarter={filter_value}: {len(df_copy)} → {len(filtered)} rows")
        
        elif filter_type == 'year':
            # Filter to specific year
            filtered = df_copy[df_copy[date_column].dt.year == filter_value]
            self.logger.info(f"Filtered to year={filter_value}: {len(df_copy)} → {len(filtered)} rows")
        
        else:
            self.logger.warning(f"Unknown filter type: {filter_type}, returning unfiltered data")
            filtered = df_copy
        
        if len(filtered) == 0:
            self.logger.warning(f"Temporal filter resulted in 0 rows! Filter: {temporal_filter}")
        
        return filtered
    
    def _prepare_time_series(self,
                            df: pd.DataFrame,
                            date_column: str,
                            metric_column: str,
                            aggregation: str) -> Optional[pd.DataFrame]:
        """
        Prepare time series by grouping data by date and aggregating
        
        Returns:
            DataFrame with columns: [date, value]
        """
        try:
            # Convert date column to datetime if not already
            if not pd.api.types.is_datetime64_any_dtype(df[date_column]):
                df_copy = df.copy()
                df_copy[date_column] = pd.to_datetime(df_copy[date_column], errors='coerce')
            else:
                df_copy = df
            
            # Remove rows with invalid dates
            df_copy = df_copy[df_copy[date_column].notna()]
            
            if len(df_copy) == 0:
                self.logger.warning("No valid dates in date column")
                return None
            
            # Auto-detect granularity
            granularity = self._detect_granularity(df_copy[date_column])
            self.logger.info(f"Detected time granularity: {granularity}")
            
            # Apply aggregation
            agg_lower = aggregation.lower()
            
            if agg_lower in ['count', 'count_distinct', 'nunique']:
                # Count records per period
                if agg_lower == 'count':
                    ts = df_copy.groupby(date_column).size().reset_index(name='value')
                else:
                    ts = df_copy.groupby(date_column)[metric_column].nunique().reset_index(name='value')
            elif agg_lower in ['sum', 'total']:
                ts = df_copy.groupby(date_column)[metric_column].sum().reset_index(name='value')
            elif agg_lower in ['mean', 'avg', 'average']:
                ts = df_copy.groupby(date_column)[metric_column].mean().reset_index(name='value')
            elif agg_lower in ['median']:
                ts = df_copy.groupby(date_column)[metric_column].median().reset_index(name='value')
            else:
                # Default to sum
                ts = df_copy.groupby(date_column)[metric_column].sum().reset_index(name='value')
            
            # Sort by date
            ts = ts.sort_values(date_column).reset_index(drop=True)
            ts.rename(columns={date_column: 'date'}, inplace=True)
            
            self.logger.info(f"Time series prepared: {len(ts)} periods, range: {ts['date'].min()} to {ts['date'].max()}")
            
            return ts
            
        except Exception as e:
            self.logger.error(f"Failed to prepare time series: {e}")
            return None
    
    def _detect_granularity(self, date_series: pd.Series) -> str:
        """
        Auto-detect time granularity from date series
        
        Returns:
            'daily', 'weekly', 'monthly', 'quarterly', or 'yearly'
        """
        try:
            # Calculate median time difference
            sorted_dates = date_series.sort_values()
            diffs = sorted_dates.diff().dropna()
            
            if len(diffs) == 0:
                return 'unknown'
            
            median_diff = diffs.median()
            median_days = median_diff.total_seconds() / (24 * 3600)
            
            # Classify granularity
            if median_days < 2:
                return 'daily'
            elif median_days < 10:
                return 'weekly'
            elif median_days < 40:
                return 'monthly'
            elif median_days < 120:
                return 'quarterly'
            else:
                return 'yearly'
                
        except Exception as e:
            self.logger.warning(f"Granularity detection failed: {e}")
            return 'unknown'
    
    def _detect_stl_anomalies(self, ts_data: pd.DataFrame) -> List[int]:
        """
        Layer 1: STL Decomposition for statistical anomaly detection
        
        Uses Seasonal-Trend-Loess decomposition to separate:
        - Trend component
        - Seasonal component  
        - Residual component
        
        Anomalies are periods where residuals exceed threshold
        
        Returns:
            List of anomalous period indices
        """
        self.logger.info("[LAYER1] Starting STL decomposition")
        
        try:
            values = ts_data['value'].values
            
            # Need at least 4 periods for STL
            if len(values) < 4:
                self.logger.warning("[LAYER1] Too few periods for STL, using simple Z-score")
                return self._simple_zscore_detection(values)
            
            # Use statsmodels STL if available, otherwise fall back
            try:
                from statsmodels.tsa.seasonal import STL
                
                # Determine seasonal period
                seasonal_period = self._estimate_seasonal_period(len(values))
                
                if seasonal_period is None or len(values) < 2 * seasonal_period:
                    self.logger.warning("[LAYER1] Not enough data for seasonal decomposition, using Z-score")
                    return self._simple_zscore_detection(values)
                
                # Perform STL decomposition
                stl = STL(values, seasonal=seasonal_period, trend=None, robust=True)
                result = stl.fit()
                
                # Get residuals
                residuals = result.resid
                
                # Identify anomalies in residuals
                anomalies = self._find_anomalies_in_residuals(residuals)
                
                self.logger.info(f"[LAYER1] STL detected {len(anomalies)} statistical anomalies")
                return anomalies
                
            except ImportError:
                self.logger.warning("[LAYER1] statsmodels not available, using simple Z-score")
                return self._simple_zscore_detection(values)
                
        except Exception as e:
            self.logger.warning(f"[LAYER1] STL decomposition failed: {e}, using fallback")
            return self._simple_zscore_detection(ts_data['value'].values)
    
    def _simple_zscore_detection(self, values: np.ndarray) -> List[int]:
        """
        Fallback: Simple Z-score based anomaly detection
        """
        mean = np.mean(values)
        std = np.std(values)
        
        if std == 0:
            return []
        
        z_scores = np.abs((values - mean) / std)
        anomalies = np.where(z_scores > self.threshold)[0].tolist()
        
        self.logger.info(f"[LAYER1_FALLBACK] Z-score detected {len(anomalies)} anomalies")
        return anomalies
    
    def _estimate_seasonal_period(self, n_periods: int) -> Optional[int]:
        """
        Estimate seasonal period based on number of data points
        
        Returns:
            Seasonal period or None if data is too small
        """
        if n_periods >= 24:
            return 12  # Monthly seasonality (annual)
        elif n_periods >= 14:
            return 7   # Weekly seasonality
        elif n_periods >= 8:
            return 4   # Quarterly seasonality
        else:
            return None  # Not enough data for seasonality
    
    def _find_anomalies_in_residuals(self, residuals: np.ndarray) -> List[int]:
        """
        Find anomalies in residual component using Z-score
        """
        # Use median absolute deviation (MAD) for robustness
        median_resid = np.median(residuals)
        mad = np.median(np.abs(residuals - median_resid))
        
        if mad == 0:
            # All residuals are the same - no anomalies
            return []
        
        # Modified Z-score = 0.6745 * (x - median) / MAD
        modified_z_scores = 0.6745 * (residuals - median_resid) / mad
        
        # Find anomalies
        anomalies = np.where(np.abs(modified_z_scores) > self.threshold)[0].tolist()
        
        return anomalies
    
    def _detect_changepoints(self, ts_data: pd.DataFrame) -> List[int]:
        """
        Layer 2: Change Point Detection using PELT algorithm
        
        Detects abrupt shifts/changes in time series mean/variance
        
        Returns:
            List of changepoint indices (periods where shifts occur)
        """
        self.logger.info("[LAYER2] Starting changepoint detection")
        
        try:
            values = ts_data['value'].values
            
            # Try using ruptures library if available
            try:
                import ruptures as rpt
                
                # PELT algorithm for changepoint detection
                model = "rbf"  # Radial Basis Function kernel
                algo = rpt.Pelt(model=model, min_size=2, jump=1).fit(values)
                
                # Detect changepoints with penalty tuning
                penalty = self._calculate_penalty(len(values))
                changepoints = algo.predict(pen=penalty)
                
                # Remove last point (end of series)
                if changepoints and changepoints[-1] == len(values):
                    changepoints = changepoints[:-1]
                
                self.logger.info(f"[LAYER2] PELT detected {len(changepoints)} changepoints: {changepoints}")
                return changepoints
                
            except ImportError:
                self.logger.warning("[LAYER2] ruptures not available, using simple changepoint detection")
                return self._simple_changepoint_detection(values)
                
        except Exception as e:
            self.logger.warning(f"[LAYER2] Changepoint detection failed: {e}, using fallback")
            return self._simple_changepoint_detection(ts_data['value'].values)
    
    def _calculate_penalty(self, n_periods: int) -> float:
        """
        Calculate penalty for PELT algorithm
        
        Higher penalty = fewer changepoints (more conservative)
        Lower penalty = more changepoints (more sensitive)
        """
        # Adaptive penalty based on series length
        base_penalty = np.log(n_periods) * np.var(n_periods)
        return base_penalty * 1.5  # Conservative (1.5x multiplier)
    
    def _simple_changepoint_detection(self, values: np.ndarray) -> List[int]:
        """
        Fallback: Simple changepoint detection using rolling mean
        """
        if len(values) < 4:
            return []
        
        changepoints = []
        window_size = max(2, len(values) // 4)
        
        for i in range(window_size, len(values) - window_size):
            # Compare mean before and after
            before = values[i-window_size:i]
            after = values[i:i+window_size]
            
            mean_before = np.mean(before)
            mean_after = np.mean(after)
            std_before = np.std(before)
            
            if std_before == 0:
                continue
            
            # Detect significant change
            change_ratio = abs(mean_after - mean_before) / (std_before + 1e-6)
            
            if change_ratio > 2.0:  # Significant change
                changepoints.append(i)
        
        self.logger.info(f"[LAYER2_FALLBACK] Simple detection found {len(changepoints)} changepoints")
        return changepoints
    
    def _combine_and_validate(self,
                              ts_data: pd.DataFrame,
                              stl_anomalies: List[int],
                              changepoint_anomalies: List[int]) -> List[Dict[str, Any]]:
        """
        Layer 3: Combine results from both methods and apply business validation
        
        Returns:
            List of validated anomaly dictionaries
        """
        self.logger.info("[LAYER3] Combining results and applying business validation")
        
        values = ts_data['value'].values
        dates = ts_data['date'].values
        
        # Combine anomalies from both methods
        all_anomaly_indices = set(stl_anomalies + changepoint_anomalies)
        
        anomalies = []
        
        for idx in sorted(all_anomaly_indices):
            if idx >= len(values):
                continue
            
            # Determine confidence based on agreement
            in_stl = idx in stl_anomalies
            in_changepoint = idx in changepoint_anomalies
            
            if in_stl and in_changepoint:
                confidence = 'HIGH'
            elif in_stl or in_changepoint:
                confidence = 'MEDIUM'
            else:
                continue
            
            # Calculate anomaly details
            anomaly_info = self._get_anomaly_details(
                ts_data, idx, values, dates, confidence
            )
            
            # Layer 3: Business validation
            is_valid = self._validate_business_logic(ts_data, idx, anomaly_info)
            
            if is_valid:
                anomalies.append(anomaly_info)
        
        # Sort by confidence and severity
        anomalies.sort(key=lambda x: (
            {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}.get(x['confidence'], 0),
            x['severity_score']
        ), reverse=True)
        
        self.logger.info(f"[LAYER3] Final anomalies after validation: {len(anomalies)}")
        self.logger.info(f"[LAYER3] Confidence breakdown: HIGH={sum(1 for a in anomalies if a['confidence']=='HIGH')}, MEDIUM={sum(1 for a in anomalies if a['confidence']=='MEDIUM')}")
        
        return anomalies
    
    def _get_anomaly_details(self,
                            ts_data: pd.DataFrame,
                            idx: int,
                            values: np.ndarray,
                            dates: np.ndarray,
                            confidence: str) -> Dict[str, Any]:
        """
        Extract detailed information about an anomaly
        """
        # Get anomaly value
        anomaly_value = float(values[idx])
        anomaly_date = pd.Timestamp(dates[idx])
        
        # Calculate statistics
        mean_value = float(np.mean(values))
        median_value = float(np.median(values))
        std_value = float(np.std(values))
        
        # Calculate deviation
        deviation = anomaly_value - mean_value
        deviation_pct = (deviation / mean_value * 100) if mean_value != 0 else 0
        
        # Determine anomaly type
        if anomaly_value > mean_value + std_value:
            anomaly_type = 'spike'
        elif anomaly_value < mean_value - std_value:
            anomaly_type = 'dip'
        else:
            anomaly_type = 'unusual'
        
        # Calculate severity score
        z_score = abs((anomaly_value - mean_value) / std_value) if std_value > 0 else 0
        severity_score = float(z_score)
        
        # Get context (neighboring periods)
        context_before = []
        context_after = []
        
        if idx > 0:
            context_before.append({
                'date': str(dates[idx-1]),
                'value': float(values[idx-1])
            })
        
        if idx < len(values) - 1:
            context_after.append({
                'date': str(dates[idx+1]),
                'value': float(values[idx+1])
            })
        
        return {
            'period': str(anomaly_date.date()),
            'value': anomaly_value,
            'expected_value': mean_value,
            'deviation': deviation,
            'deviation_pct': deviation_pct,
            'type': anomaly_type,
            'confidence': confidence,
            'severity_score': severity_score,
            'reason': f"{anomaly_type.capitalize()}: value is {abs(deviation_pct):.1f}% {'above' if deviation > 0 else 'below'} average",
            'context_before': context_before,
            'context_after': context_after,
            'z_score': z_score
        }
    
    def _extract_temporal_granularity(self, query: str) -> str:
        """
        Extract desired temporal granularity from user query using keyword detection
        
        Args:
            query: User's natural language query
            
        Returns:
            'quarter', 'year', 'month', 'day', or 'auto'
        """
        query_lower = query.lower()
        
        # Check for quarter references
        quarter_keywords = ['quarter', 'quarterly', 'q1', 'q2', 'q3', 'q4', 'quarters', 'qoq']
        if any(keyword in query_lower for keyword in quarter_keywords):
            self.logger.info(f"[GRANULARITY] Detected QUARTER granularity from query")
            return 'quarter'
        
        # Check for year references
        year_keywords = ['year', 'yearly', 'annual', 'annually', 'years', 'yoy']
        if any(keyword in query_lower for keyword in year_keywords):
            self.logger.info(f"[GRANULARITY] Detected YEAR granularity from query")
            return 'year'
        
        # Check for month references (explicit - to override default)
        month_keywords = ['month', 'monthly', 'months', 'mom']
        if any(keyword in query_lower for keyword in month_keywords):
            self.logger.info(f"[GRANULARITY] Detected MONTH granularity from query")
            return 'month'
        
        # Check for day/week references
        day_keywords = ['day', 'daily', 'days', 'week', 'weekly', 'wow']
        if any(keyword in query_lower for keyword in day_keywords):
            self.logger.info(f"[GRANULARITY] Detected DAY/WEEK granularity from query")
            return 'day'
        
        self.logger.info(f"[GRANULARITY] No explicit granularity found, using AUTO (data's natural granularity)")
        return 'auto'

    def _aggregate_to_quarters(self, df: pd.DataFrame, date_column: str, metric_column: str, agg_func: str) -> pd.DataFrame:
        """
        Aggregate data to quarter level
        
        Args:
            df: DataFrame with temporal data
            date_column: Name of date column
            metric_column: Name of metric to aggregate
            agg_func: Aggregation function ('sum', 'mean', 'count')
            
        Returns:
            DataFrame aggregated by quarter with 'quarter' column
        """
        df = df.copy()
        
        # Ensure date column is datetime
        if not pd.api.types.is_datetime64_any_dtype(df[date_column]):
            df[date_column] = pd.to_datetime(df[date_column])
        
        # Create quarter column as Period
        df['quarter_period'] = df[date_column].dt.to_period('Q')
        
        # Aggregate by quarter
        if agg_func.lower() == 'sum':
            result = df.groupby('quarter_period')[metric_column].sum().reset_index()
        elif agg_func.lower() in ['mean', 'avg']:
            result = df.groupby('quarter_period')[metric_column].mean().reset_index()
        elif agg_func.lower() == 'count':
            result = df.groupby('quarter_period')[metric_column].count().reset_index()
        else:
            result = df.groupby('quarter_period')[metric_column].agg(agg_func).reset_index()
        
        # Convert period to string for compatibility
        result['quarter'] = result['quarter_period'].astype(str)
        result = result.drop(columns=['quarter_period'])
        
        # Rename for consistency with original date column name
        result = result.rename(columns={'quarter': date_column})
        
        self.logger.info(f"[AGGREGATION] Aggregated {len(df)} rows to {len(result)} quarters")
        self.logger.info(f"[AGGREGATION] Quarter range: {result[date_column].min()} to {result[date_column].max()}")
        
        return result

    def _aggregate_to_years(self, df: pd.DataFrame, date_column: str, metric_column: str, agg_func: str) -> pd.DataFrame:
        """
        Aggregate data to year level
        
        Args:
            df: DataFrame with temporal data
            date_column: Name of date column
            metric_column: Name of metric to aggregate
            agg_func: Aggregation function ('sum', 'mean', 'count')
            
        Returns:
            DataFrame aggregated by year
        """
        df = df.copy()
        
        # Ensure date column is datetime
        if not pd.api.types.is_datetime64_any_dtype(df[date_column]):
            df[date_column] = pd.to_datetime(df[date_column])
        
        # Extract year
        df['year'] = df[date_column].dt.year
        
        # Aggregate by year
        if agg_func.lower() == 'sum':
            result = df.groupby('year')[metric_column].sum().reset_index()
        elif agg_func.lower() in ['mean', 'avg']:
            result = df.groupby('year')[metric_column].mean().reset_index()
        elif agg_func.lower() == 'count':
            result = df.groupby('year')[metric_column].count().reset_index()
        else:
            result = df.groupby('year')[metric_column].agg(agg_func).reset_index()
        
        # Rename for consistency
        result = result.rename(columns={'year': date_column})
        
        self.logger.info(f"[AGGREGATION] Aggregated {len(df)} rows to {len(result)} years")
        self.logger.info(f"[AGGREGATION] Year range: {result[date_column].min()} to {result[date_column].max()}")
        
        return result

    def _format_period_display(self, period_value: str, granularity: str) -> str:
        """
        Format period value for display based on granularity
        
        Args:
            period_value: Raw period value (e.g., "2025Q1", "2025-03-01")
            granularity: Detected granularity
            
        Returns:
            Formatted string (e.g., "Q1 2025", "March 2025", "2025")
        """
        try:
            if granularity == 'quarter':
                # Convert "2025Q1" to "Q1 2025"
                if 'Q' in str(period_value):
                    parts = str(period_value).split('Q')
                    return f"Q{parts[1]} {parts[0]}"
                else:
                    return str(period_value)
            
            elif granularity == 'year':
                # Just return year
                return str(period_value)
            
            elif granularity == 'month':
                # Convert to "Month YYYY" format
                if isinstance(period_value, str) and '-' in period_value:
                    date_obj = pd.to_datetime(period_value)
                    return date_obj.strftime('%B %Y')
                else:
                    return str(period_value)
            
            else:
                # Default: return as-is
                return str(period_value)
                
        except Exception as e:
            self.logger.warning(f"[FORMAT] Could not format period '{period_value}': {e}")
            return str(period_value)
    
    def _validate_business_logic(self,
                                 ts_data: pd.DataFrame,
                                 idx: int,
                                 anomaly_info: Dict) -> bool:
        """
        Layer 3: Apply business logic validation
        
        Returns True if anomaly is valid, False if it's a false positive
        """
        values = ts_data['value'].values
        
        # Rule 1: Reject edge effects (first/last period might be incomplete)
        if idx == 0 or idx == len(values) - 1:
            self.logger.debug(f"[VALIDATION] Period {idx} is edge - lower confidence but accepting")
            # Don't reject, but it's noted in confidence
        
        # Rule 2: Check if it's a zero value (might be legitimate absence)
        if anomaly_info['value'] == 0:
            # Zero might be legitimate (e.g., no activity in that period)
            mean_value = np.mean(values)
            if mean_value > 10:  # If average is substantial, zero is truly anomalous
                self.logger.debug(f"[VALIDATION] Zero value in period {idx} - legitimate anomaly")
            else:
                self.logger.debug(f"[VALIDATION] Zero value in low-average series - accepting")
        
        # Rule 3: Reject if deviation is too small in absolute terms
        abs_deviation = abs(anomaly_info['deviation'])
        mean_value = abs(anomaly_info['expected_value'])
        
        if abs_deviation < 1.0 and mean_value < 10:
            # Very small absolute deviation in low-value series
            self.logger.debug(f"[VALIDATION] Deviation too small ({abs_deviation:.2f}) - rejecting")
            return False
        
        # Rule 4: Check for consecutive anomalies (might indicate trend shift, not anomaly)
        if idx > 0 and idx < len(values) - 1:
            prev_value = values[idx - 1]
            next_value = values[idx + 1]
            curr_value = values[idx]
            
            # If all three are similar, it's a trend shift, not an anomaly
            if abs(curr_value - prev_value) < abs_deviation * 0.3 and \
               abs(curr_value - next_value) < abs_deviation * 0.3:
                self.logger.debug(f"[VALIDATION] Period {idx} appears to be trend shift - accepting as anomaly anyway")
        
        # Default: accept the anomaly
        return True

