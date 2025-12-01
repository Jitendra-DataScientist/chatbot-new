"""
Outlier Detection Service - Multi-Layer Robust Detection

3-Layer Detection System:
1. Feature-Level: Modified Z-Score with MAD (univariate)
2. Multivariate: Isolation Forest with LOF fallback
3. Domain-Aware: Business logic validation

Author: Outlier Detection Module
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Any, Tuple, Optional
import logging
from scipy.stats import median_abs_deviation
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from sklearn.preprocessing import StandardScaler


class OutlierDetectionService:
    """
    Robust outlier detection using ensemble of methods
    """
    
    def __init__(self, logger=None):
        """
        Initialize outlier detection service
        
        Args:
            logger: Logger instance
        """
        self.logger = logger or logging.getLogger(__name__)
    
    def detect_outliers(self, df: pd.DataFrame, 
                       features: List[str],
                       contamination: float = 0.05,
                       z_threshold: float = 3.5,
                       aggregation_method: str = None,
                       metric_column: str = None) -> Dict[str, Any]:
        """
        Main entry point for outlier detection
        
        Args:
            df: DataFrame to analyze
            features: List of feature columns to consider
            contamination: Expected proportion of outliers (default 5%)
            z_threshold: Modified Z-score threshold (default 3.5)
            aggregation_method: Aggregation method used (for validation)
            metric_column: Target metric column name
        
        Returns:
            Dictionary with outlier results
        """
        try:
            self.logger.info("="*80)
            self.logger.info("=== OUTLIER DETECTION SERVICE ===")
            self.logger.info(f"Dataset size: {len(df)} rows")
            self.logger.info(f"Features to analyze: {len(features)}")
            self.logger.info("="*80)
            
            # Handle small datasets
            if len(df) < 30:
                self.logger.warning(f"Small dataset ({len(df)} rows) - using univariate only")
                return self._detect_univariate_only(df, features, z_threshold)
            
            # Layer 1: Univariate outlier scores
            univariate_results = self._detect_univariate_outliers(df, features, z_threshold)
            
            # Layer 2: Multivariate outlier detection
            multivariate_results = self._detect_multivariate_outliers(df, features, contamination)
            
            # Layer 3: Combine and validate
            final_results = self._combine_and_validate(
                df, univariate_results, multivariate_results, 
                features, aggregation_method, metric_column
            )
            
            self.logger.info(f"✓ Detection complete - identified {len(final_results['outliers'])} outliers")
            
            return final_results
            
        except Exception as e:
            self.logger.error(f"Outlier detection failed: {e}", exc_info=True)
            return {
                'success': False,
                'error': str(e),
                'outliers': []
            }
    
    def _detect_univariate_outliers(self, df: pd.DataFrame, 
                                   features: List[str],
                                   z_threshold: float) -> Dict[str, Any]:
        """
        Layer 1: Modified Z-Score detection per feature
        
        Uses Median Absolute Deviation (MAD) for robustness to skewed data
        """
        self.logger.info("[LAYER1] Starting univariate outlier detection (Modified Z-Score)")
        
        outlier_scores = pd.DataFrame(index=df.index)
        feature_outliers = {}
        
        for feature in features:
            try:
                # Skip non-numeric columns
                if not pd.api.types.is_numeric_dtype(df[feature]):
                    continue
                
                values = df[feature].dropna()
                if len(values) == 0:
                    continue
                
                # Calculate Modified Z-Score using MAD
                median = values.median()
                mad = median_abs_deviation(values, nan_policy='omit')
                
                if mad == 0:
                    # All values are the same - no outliers
                    outlier_scores[feature] = 0
                    continue
                
                # Modified Z-Score = 0.6745 * (x - median) / MAD
                modified_z = 0.6745 * (df[feature] - median) / mad
                outlier_scores[feature] = modified_z.abs()
                
                # Count outliers for this feature
                feature_outliers[feature] = (outlier_scores[feature] > z_threshold).sum()
                
            except Exception as e:
                self.logger.warning(f"Failed to calculate Z-score for {feature}: {e}")
                continue
        
        # Calculate row-wise max Z-score (worst outlier score across features)
        if len(outlier_scores.columns) > 0:
            max_z_scores = outlier_scores.max(axis=1)
        else:
            max_z_scores = pd.Series(0, index=df.index)
        
        self.logger.info(f"[LAYER1] Analyzed {len(outlier_scores.columns)} numeric features")
        self.logger.info(f"[LAYER1] Feature outliers: {sum(feature_outliers.values())} total detections")
        
        return {
            'scores': outlier_scores,
            'max_scores': max_z_scores,
            'feature_outliers': feature_outliers,
            'threshold': z_threshold
        }
    
    def _detect_multivariate_outliers(self, df: pd.DataFrame,
                                     features: List[str],
                                     contamination: float) -> Dict[str, Any]:
        """
        Layer 2: Isolation Forest with LOF fallback
        """
        self.logger.info("[LAYER2] Starting multivariate outlier detection (Isolation Forest)")
        
        # Prepare feature matrix - only numeric features
        numeric_features = []
        for feature in features:
            if pd.api.types.is_numeric_dtype(df[feature]):
                numeric_features.append(feature)
        
        if len(numeric_features) == 0:
            self.logger.warning("[LAYER2] No numeric features - skipping multivariate detection")
            return {
                'method': 'none',
                'outlier_scores': pd.Series(0, index=df.index),
                'predictions': np.ones(len(df))
            }
        
        # Create feature matrix
        X = df[numeric_features].fillna(df[numeric_features].median())
        
        # Handle single feature case
        if X.shape[1] == 1:
            self.logger.info("[LAYER2] Single feature - using univariate only")
            return {
                'method': 'univariate_only',
                'outlier_scores': pd.Series(0, index=df.index),
                'predictions': np.ones(len(df))
            }
        
        # Standardize features
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        
        try:
            # Primary method: Isolation Forest
            self.logger.info("[LAYER2] Fitting Isolation Forest")
            iso_forest = IsolationForest(
                contamination=contamination,
                random_state=42,
                n_estimators=100
            )
            predictions = iso_forest.fit_predict(X_scaled)
            outlier_scores = -iso_forest.score_samples(X_scaled)  # Higher = more anomalous
            
            method = 'isolation_forest'
            self.logger.info(f"[LAYER2] Isolation Forest detected {(predictions == -1).sum()} outliers")
            
        except Exception as e:
            # Fallback: Local Outlier Factor
            self.logger.warning(f"[LAYER2] Isolation Forest failed: {e}")
            self.logger.info("[LAYER2] Falling back to Local Outlier Factor (LOF)")
            
            try:
                lof = LocalOutlierFactor(
                    contamination=contamination,
                    n_neighbors=min(20, len(df) - 1)
                )
                predictions = lof.fit_predict(X_scaled)
                outlier_scores = -lof.negative_outlier_factor_  # Higher = more anomalous
                method = 'lof'
                
                self.logger.info(f"[LAYER2] LOF detected {(predictions == -1).sum()} outliers")
                
            except Exception as e2:
                self.logger.error(f"[LAYER2] Both methods failed: {e2}")
                return {
                    'method': 'failed',
                    'outlier_scores': pd.Series(0, index=df.index),
                    'predictions': np.ones(len(df))
                }
        
        return {
            'method': method,
            'outlier_scores': pd.Series(outlier_scores, index=df.index),
            'predictions': predictions,
            'features_used': numeric_features
        }
    
    def _combine_and_validate(self, df: pd.DataFrame,
                             univariate_results: Dict,
                             multivariate_results: Dict,
                             features: List[str],
                             aggregation_method: str,
                             metric_column: str) -> Dict[str, Any]:
        """
        Layer 3: Combine results and apply business logic validation
        """
        self.logger.info("[LAYER3] Combining results and validating with business logic")
        
        outliers = []
        
        # Get predictions from both layers
        univariate_outliers = (univariate_results['max_scores'] > univariate_results['threshold'])
        multivariate_outliers = (multivariate_results['predictions'] == -1)
        
        # Iterate through potential outliers
        for idx in df.index:
            is_uni_outlier = univariate_outliers.loc[idx] if idx in univariate_outliers.index else False
            is_multi_outlier = multivariate_outliers[df.index.get_loc(idx)] if multivariate_results['method'] not in ['none', 'univariate_only', 'failed'] else False
            
            # Determine confidence based on agreement
            if is_uni_outlier and is_multi_outlier:
                confidence = 'HIGH'
            elif is_uni_outlier or is_multi_outlier:
                confidence = 'MEDIUM'
            else:
                continue  # Not an outlier
            
            # Get outlier details
            outlier_info = self._get_outlier_details(
                df, idx, univariate_results, multivariate_results, 
                features, confidence
            )
            
            # Layer 3: Business logic validation
            is_valid = self._validate_business_logic(
                df, idx, outlier_info, aggregation_method, metric_column
            )
            
            if is_valid:
                outliers.append(outlier_info)
        
        # Sort by confidence and anomaly score
        outliers.sort(key=lambda x: (
            {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}.get(x['confidence'], 0),
            x['anomaly_score']
        ), reverse=True)
        
        self.logger.info(f"[LAYER3] Final outliers after validation: {len(outliers)}")
        self.logger.info(f"[LAYER3] Confidence breakdown: HIGH={sum(1 for o in outliers if o['confidence']=='HIGH')}, MEDIUM={sum(1 for o in outliers if o['confidence']=='MEDIUM')}")
        
        return {
            'success': True,
            'outliers': outliers,
            'total_outliers': len(outliers),
            'detection_summary': {
                'univariate_method': 'Modified Z-Score (MAD)',
                'multivariate_method': multivariate_results['method'],
                'features_analyzed': len(features),
                'dataset_size': len(df)
            }
        }
    
    def _get_outlier_details(self, df: pd.DataFrame, idx: int,
                            univariate_results: Dict,
                            multivariate_results: Dict,
                            features: List[str],
                            confidence: str) -> Dict[str, Any]:
        """
        Extract detailed information about an outlier
        """
        row = df.loc[idx]
        
        # Get Z-scores for this row
        z_scores = univariate_results['scores'].loc[idx] if idx in univariate_results['scores'].index else {}
        
        # Get multivariate score
        multi_score = multivariate_results['outlier_scores'].loc[idx] if idx in multivariate_results['outlier_scores'].index else 0
        
        # Find top contributing features (highest Z-scores)
        feature_contributions = {}
        reasons = []
        
        if isinstance(z_scores, pd.Series):
            # Sort features by absolute Z-score
            sorted_features = z_scores.abs().sort_values(ascending=False)
            
            for feature, z_score in sorted_features.head(5).items():
                if z_score > univariate_results['threshold']:
                    feature_contributions[feature] = float(z_score)
                    
                    # Calculate how extreme the value is
                    feature_values = df[feature].dropna()
                    median_val = feature_values.median()
                    actual_val = row[feature]
                    
                    if pd.notna(actual_val) and pd.notna(median_val):
                        if median_val != 0:
                            ratio = actual_val / median_val
                            reasons.append(
                                f"Modified Z-Score: {z_score:.2f} on {feature} "
                                f"(value {actual_val:.2f} is {ratio:.1f}x the median {median_val:.2f})"
                            )
                        else:
                            reasons.append(
                                f"Modified Z-Score: {z_score:.2f} on {feature} "
                                f"(value {actual_val:.2f}, median is 0)"
                            )
        
        # Add multivariate reason if applicable
        if multivariate_results['method'] not in ['none', 'univariate_only', 'failed']:
            reasons.append(f"{multivariate_results['method']}: anomaly score {multi_score:.3f}")
        
        # Calculate combined anomaly score
        anomaly_score = float(univariate_results['max_scores'].loc[idx]) if idx in univariate_results['max_scores'].index else 0
        
        return {
            'row_index': int(idx),
            'confidence': confidence,
            'anomaly_score': anomaly_score,
            'reasons': reasons,
            'feature_contributions': feature_contributions,
            'row_data': row.to_dict()
        }
    
    def _validate_business_logic(self, df: pd.DataFrame, idx: int,
                                outlier_info: Dict,
                                aggregation_method: str,
                                metric_column: str) -> bool:
        """
        Layer 3: Apply business logic validation
        
        Returns True if outlier is valid, False if it's a false positive
        """
        row = df.loc[idx]
        
        # Rule 1: Check for edge cases in temporal data
        if 'create_month' in df.columns or 'create_date' in df.columns:
            # Check if this is first or last record in time series
            date_col = 'create_month' if 'create_month' in df.columns else 'create_date'
            if pd.notna(row.get(date_col)):
                is_edge = (df[date_col].min() == row[date_col] or 
                          df[date_col].max() == row[date_col])
                if is_edge:
                    self.logger.debug(f"[VALIDATION] Row {idx} is temporal edge - lower confidence")
        
        # Rule 2: For COUNT metrics, validate if zeros or extreme values make sense
        if aggregation_method == 'COUNT' or aggregation_method == 'COUNT_DISTINCT':
            if metric_column and metric_column in row:
                val = row[metric_column]
                if val == 0:
                    # Zero counts might be legitimate (e.g., no activity in a period)
                    self.logger.debug(f"[VALIDATION] Row {idx} has zero count - legitimate outlier")
        
        # Rule 3: Check if it's just a rare category vs true anomaly
        categorical_features = [col for col in df.columns if df[col].dtype == 'object']
        for cat_feature in categorical_features:
            if cat_feature in outlier_info.get('feature_contributions', {}):
                value = row.get(cat_feature)
                if pd.notna(value):
                    value_count = (df[cat_feature] == value).sum()
                    if value_count == 1:
                        self.logger.debug(f"[VALIDATION] Row {idx} has rare category '{value}' in {cat_feature}")
        
        # Rule 4: Reject obvious data quality issues
        if metric_column and metric_column in row:
            val = row[metric_column]
            if pd.isna(val):
                self.logger.debug(f"[VALIDATION] Row {idx} has null metric - rejecting")
                return False
        
        # Default: accept the outlier
        return True
    
    def _detect_univariate_only(self, df: pd.DataFrame,
                               features: List[str],
                               z_threshold: float) -> Dict[str, Any]:
        """
        Fallback for small datasets - univariate detection only
        """
        self.logger.info("[SMALL_DATASET] Using univariate detection only")
        
        univariate_results = self._detect_univariate_outliers(df, features, z_threshold)
        
        outliers = []
        univariate_outliers = (univariate_results['max_scores'] > z_threshold)
        
        for idx in df[univariate_outliers].index:
            outlier_info = self._get_outlier_details(
                df, idx, univariate_results, 
                {'method': 'none', 'outlier_scores': pd.Series(0, index=df.index), 'predictions': np.ones(len(df))},
                features, 'MEDIUM'
            )
            outliers.append(outlier_info)
        
        return {
            'success': True,
            'outliers': outliers,
            'total_outliers': len(outliers),
            'detection_summary': {
                'univariate_method': 'Modified Z-Score (MAD)',
                'multivariate_method': 'skipped (dataset too small)',
                'features_analyzed': len(features),
                'dataset_size': len(df)
            }
        }
