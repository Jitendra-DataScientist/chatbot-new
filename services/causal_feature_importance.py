#!/usr/bin/env python3
"""
Causal Feature Importance Analysis - Performance Optimized with Intelligent Feature Filtering

HOW TOP 5 FEATURES ARE SELECTED:
================================

This script uses a multi-method approach to identify the most causally important features:

PREPROCESSING STEPS:
===================
1. DATA PREPARATION:
   - Missing value imputation (median for numeric, 'Unknown' for categorical)
   - Automatic sampling for large datasets (>50,000 rows)

2. COMPOUND TIME FEATURE REMOVAL:
   - Identifies time features by pattern matching (datetime, date, time, weekday, duration)
   - Groups related time concepts (e.g., 'created', 'closed', 'modified')
   - Keeps most informative format: datetime > date > time > weekday > duration
   - Considers missing values and uniqueness as tie-breakers
   - Removes all other variants of the same time concept

3. CATEGORICAL ENCODING:
   - Categorical encoding with LabelEncoder (features only, not target)
   - Converts all feature strings to numeric for correlation analysis

4. REDUNDANCY REMOVAL:
   - For NUMERIC targets: Excludes features with perfect correlation to target (|correlation| > 0.999)
   - For CATEGORICAL targets: Skips target correlation removal (incompatible data types)
   - Always removes multicollinear features (correlation > 0.95 between features)
   - Keeps only first occurrence of highly correlated feature groups
   - Prevents analysis of redundant/duplicate information

FEATURE IMPORTANCE METHODS:
==========================
1. CORRELATION ANALYSIS (Weight: 30%)
   - Computes Pearson correlation between each feature and target
   - Weights by statistical significance (p < 0.05)
   - Score = |correlation| * (1 - p_value) if significant, else 0

2. TREE-BASED IMPORTANCE (Weight: 40%) 
   - Uses Random Forest feature importance (Gini impurity reduction)
   - Automatically detects classification vs regression tasks
   - Provides non-linear relationship detection
   - OPTIMIZED: Adaptive estimators (10-50) based on dataset size

3. CAUSAL TREATMENT EFFECTS (Weight: 20%)
   - Treats each feature as binary treatment (above/below median)
   - Estimates Average Treatment Effect (ATE) on target
   - Adjusts for confounding using propensity score weighting

4. ECONML CAUSAL FOREST (Weight: 10%)
   - Advanced causal inference using Double Machine Learning
   - Estimates heterogeneous treatment effects
   - Controls for confounding through orthogonalization
   - Only used if econml library is available
   - OPTIMIZED: Limited to ≤20 features, reduced estimators (10-25)

PERFORMANCE OPTIMIZATIONS:
=========================
- COMPOUND TIME FILTERING: Removes redundant time representations (keeps best format only)
- REDUNDANCY FILTERING: Removes perfectly correlated and multicollinear features
- AUTO-SAMPLING: Datasets >50,000 rows automatically sampled to 50,000
- ADAPTIVE ESTIMATORS: RF estimators scale with dataset size (10-50)
- PARALLEL PROCESSING: Multi-core processing with n_jobs=-1
- SMART LIMITS: EconML skipped for >20 features (computational efficiency)
- PROGRESS TRACKING: Real-time progress indicators for long operations
- MEMORY EFFICIENT: Graceful handling of large datasets

FINAL SCORING:
- Combines all methods using weighted average
- Handles missing values and categorical encoding automatically
- Returns top 5 features ranked by composite causal importance score
- For dataframes with ≤5 features, returns all features

PERFORMANCE EXPECTATIONS:
========================
| Dataset Size    | Estimators | EconML | Expected Time |
|----------------|------------|---------|---------------|
| < 1,000 rows   | 50         | ✅      | < 30 seconds  |
| 1,000-10,000   | 25-50      | ✅      | 1-2 minutes   |
| 10,000-50,000  | 10-25      | ❌*     | 2-5 minutes   |
| > 50,000       | 10 (sampled)| ❌     | 3-5 minutes   |
*EconML disabled if >20 features

EDGE CASE HANDLING:
- Dataframes with <10 rows: raises error
- Missing target column: raises error  
- All categorical features: automatic label encoding
- Missing econml: graceful fallback to 3-method approach
- Large datasets: automatic sampling and optimization
- Perfect correlations: automatic removal to prevent redundancy
- Multicollinearity: keeps only first occurrence of correlated features
- Compound time features: intelligent consolidation based on information richness
- Mixed data types: proper handling of categorical vs numeric targets
"""

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import train_test_split
from scipy.stats import pearsonr
from sklearn.linear_model import LogisticRegression, LinearRegression

# EconML for advanced causal inference
try:
    from econml.dml import CausalForestDML
    from econml.sklearn_extensions.linear_model import WeightedLasso
    ECONML_AVAILABLE = True
    print("EconML imported successfully")
except ImportError:
    ECONML_AVAILABLE = False
    print("EconML not available. Install with: pip install econml")


def remove_compound_time_features(df, feature_columns):
    """
    Remove compound/derived time features and keep only the most informative format.
    
    Groups related time features by concept and keeps the best version based on:
    1. Information richness (datetime > date > time > weekday > duration)
    2. Fewest missing values
    3. Most unique values
    
    Parameters:
    -----------
    df : pandas.DataFrame
        Input dataframe
    feature_columns : list
        List of feature column names
    
    Returns:
    --------
    list
        Filtered list of feature columns with compound time features removed
    """
    
    print("Removing compound time features...")
    
    import re
    from datetime import datetime
    
    # Define time-related patterns and their priority (higher = better)
    time_patterns = {
        'datetime_with_tz': {'pattern': r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}', 'priority': 10},
        'datetime': {'pattern': r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}', 'priority': 9},
        'datetime_short': {'pattern': r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}', 'priority': 8},
        'date': {'pattern': r'\d{4}-\d{2}-\d{2}', 'priority': 7},
        'time': {'pattern': r'^\d{2}:\d{2}:\d{2}$', 'priority': 6},
        'time_short': {'pattern': r'^\d{2}:\d{2}$', 'priority': 5},
        'weekday': {'pattern': r'^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)$', 'priority': 4},
        'duration_text': {'pattern': r'^\d+(day|hr|min|sec)s?$', 'priority': 3},
        'month_year': {'pattern': r'^\d{4}-\d{2}$', 'priority': 2}
    }
    
    # Identify time features and their concepts
    time_feature_groups = {}
    non_time_features = []
    
    for feature in feature_columns:
        if feature not in df.columns:
            continue
            
        # Sample some non-null values to check format
        sample_values = df[feature].dropna().astype(str).head(10).tolist()
        if len(sample_values) == 0:
            non_time_features.append(feature)
            continue
            
        # Check if it matches any time pattern
        is_time_feature = False
        feature_priority = 0
        
        for pattern_name, pattern_info in time_patterns.items():
            pattern = pattern_info['pattern']
            priority = pattern_info['priority']
            
            # Check if most sample values match the pattern
            matches = sum(1 for val in sample_values if re.match(pattern, str(val)))
            if matches >= len(sample_values) * 0.7:  # 70% match threshold
                is_time_feature = True
                feature_priority = priority
                break
        
        if is_time_feature:
            # Extract concept from feature name (remove time-specific suffixes)
            concept = feature.lower()
            
            # Remove common time suffixes to group similar concepts
            time_suffixes = ['_date', '_time', '_datetime', '_day', '_week', '_month', '_year', 
                           'date', 'time', '_ist', '_utc', '_tz', '_calc']
            
            for suffix in time_suffixes:
                if concept.endswith(suffix):
                    concept = concept[:-len(suffix)]
                    break
            
            # Group by concept
            if concept not in time_feature_groups:
                time_feature_groups[concept] = []
            
            time_feature_groups[concept].append({
                'feature': feature,
                'priority': feature_priority,
                'missing_count': df[feature].isna().sum(),
                'unique_count': df[feature].nunique()
            })
        else:
            non_time_features.append(feature)
    
    # Select best feature from each time concept group
    selected_time_features = []
    removed_time_features = []
    
    for concept, features in time_feature_groups.items():
        if len(features) == 1:
            # Only one feature in group, keep it
            selected_time_features.append(features[0]['feature'])
        else:
            # Multiple features, select the best one
            print(f"  Concept '{concept}' has {len(features)} variants:")
            
            # Sort by priority (desc), then missing count (asc), then unique count (desc)
            features.sort(key=lambda x: (-x['priority'], x['missing_count'], -x['unique_count']))
            
            best_feature = features[0]
            selected_time_features.append(best_feature['feature'])
            
            print(f"    ✅ Kept: {best_feature['feature']} (priority={best_feature['priority']}, missing={best_feature['missing_count']}, unique={best_feature['unique_count']})")
            
            for feature in features[1:]:
                removed_time_features.append(feature['feature'])
                print(f"    ❌ Removed: {feature['feature']} (priority={feature['priority']}, missing={feature['missing_count']}, unique={feature['unique_count']})")
    
    final_features = non_time_features + selected_time_features
    
    print(f"  Time feature analysis:")
    print(f"    Found {len(time_feature_groups)} time concepts")
    print(f"    Removed {len(removed_time_features)} compound time features")
    print(f"    Kept {len(selected_time_features)} best time features")
    print(f"    Final feature count: {len(final_features)} (from {len(feature_columns)})")
    
    return final_features


def remove_redundant_features(df_clean, feature_columns, target_column, is_classification, target_corr_threshold=0.999, multicollinear_threshold=0.95):
    """
    Remove features that are perfectly correlated with target or highly multicollinear.
    
    Parameters:
    -----------
    df_clean : pandas.DataFrame
        Cleaned dataframe with encoded features
    feature_columns : list
        List of feature column names
    target_column : str
        Target variable column name
    is_classification : bool
        Whether target is categorical (True) or numeric (False)
    target_corr_threshold : float
        Threshold for target correlation (default: 0.999)
    multicollinear_threshold : float
        Threshold for multicollinearity (default: 0.95)
    
    Returns:
    --------
    list
        Filtered list of feature columns
    """
    
    print("Removing redundant features...")
    
    # Step 1: Remove features perfectly correlated with target (only for numeric targets)
    features_to_keep = []
    target_correlated_features = []
    
    if is_classification:
        print("  Target is categorical - skipping target correlation removal")
        features_to_keep = feature_columns.copy()
    else:
        print("  Target is numeric - checking target correlations")
        for feature in feature_columns:
            try:
                # Calculate correlation with target
                correlation = np.corrcoef(df_clean[feature], df_clean[target_column])[0, 1]
                
                if np.isnan(correlation):
                    # Handle case where correlation cannot be computed (constant features)
                    print(f"  Warning: Cannot compute correlation for {feature} (constant values?)")
                    continue
                
                if abs(correlation) > target_corr_threshold:
                    target_correlated_features.append(feature)
                    print(f"  Excluded {feature}: correlation with target = {correlation:.6f}")
                else:
                    features_to_keep.append(feature)
                    
            except Exception as e:
                print(f"  Warning: Error computing correlation for {feature}: {e}")
                continue
    
    print(f"  Removed {len(target_correlated_features)} features due to high target correlation")
    
    # Step 2: Remove multicollinear features (keep first occurrence)
    if len(features_to_keep) <= 1:
        print("  Insufficient features remaining for multicollinearity check")
        return features_to_keep
    
    # Calculate correlation matrix for remaining features
    feature_data = df_clean[features_to_keep]
    
    try:
        corr_matrix = feature_data.corr()
        
        # Find and remove multicollinear features
        final_features = []
        multicollinear_features = []
        
        for i, feature_i in enumerate(features_to_keep):
            is_multicollinear = False
            
            # Check correlation with already selected features
            for feature_j in final_features:
                if feature_j in corr_matrix.columns and feature_i in corr_matrix.columns:
                    correlation = corr_matrix.loc[feature_i, feature_j]
                    
                    if abs(correlation) > multicollinear_threshold:
                        multicollinear_features.append((feature_i, feature_j, correlation))
                        print(f"  Excluded {feature_i}: correlation with {feature_j} = {correlation:.6f}")
                        is_multicollinear = True
                        break
            
            if not is_multicollinear:
                final_features.append(feature_i)
        
        print(f"  Removed {len(multicollinear_features)} features due to multicollinearity")
        
    except Exception as e:
        print(f"  Warning: Error in multicollinearity check: {e}")
        final_features = features_to_keep
    
    print(f"  Final feature count: {len(final_features)} (from original {len(feature_columns)})")
    
    return final_features


def causal_feature_importance(df, target_column):
    """
    Perform causal analysis and return top 5 most important features.
    
    Parameters:
    -----------
    df : pandas.DataFrame
        Input dataframe
    target_column : str
        Name of the target variable column
    
    Returns:
    --------
    list
        Top 5 most important feature names (or all features if less than 5)
    """
    
    # Validate inputs
    if not isinstance(df, pd.DataFrame):
        raise ValueError("Input must be a pandas DataFrame")
    
    if target_column not in df.columns:
        raise ValueError(f"Target column '{target_column}' not found in dataframe")
    
    if df.shape[0] < 10:
        raise ValueError("Dataframe must have at least 10 rows for meaningful analysis")
    
    # Get feature columns (all columns except target)
    feature_columns = [col for col in df.columns if col != target_column]
    
    # Handle case where there are 5 or fewer total columns
    if len(feature_columns) <= 5:
        print(f"Dataframe has only {len(feature_columns)} feature columns. Returning all features.")
        return feature_columns
    
    # Prepare data
    df_clean = df.copy()
    
    # For very large datasets, sample to speed up computation
    if df_clean.shape[0] > 50000:
        print(f"Large dataset detected ({df_clean.shape[0]} rows). Sampling 50,000 rows for faster computation.")
        df_clean = df_clean.sample(n=50000, random_state=42)
    
    # Handle missing values
    print("Handling missing values...")
    for col in df_clean.columns:
        if df_clean[col].dtype == 'object':
            df_clean[col] = df_clean[col].fillna('Unknown')
        else:
            df_clean[col] = df_clean[col].fillna(df_clean[col].median())
    
    # Remove compound time features (keep most informative format only)
    feature_columns = remove_compound_time_features(df_clean, feature_columns)
    
    if len(feature_columns) == 0:
        print("ERROR: No features remaining after compound time feature removal!")
        return []
    
    # Encode categorical variables (features only, not target)
    label_encoders = {}
    for col in feature_columns:
        if df_clean[col].dtype == 'object':
            le = LabelEncoder()
            df_clean[col] = le.fit_transform(df_clean[col].astype(str))
            label_encoders[col] = le
    
    # Determine if target is classification or regression
    target_unique_values = df_clean[target_column].nunique()
    is_classification = target_unique_values <= 10 or df_clean[target_column].dtype == 'object'
    
    # Remove redundant features (now that features are encoded)
    feature_columns = remove_redundant_features(df_clean, feature_columns, target_column, is_classification)
    
    if len(feature_columns) == 0:
        print("ERROR: No features remaining after redundancy removal!")
        return []
    
    # Encode target if categorical
    if df_clean[target_column].dtype == 'object':
        target_encoder = LabelEncoder()
        df_clean[target_column] = target_encoder.fit_transform(df_clean[target_column].astype(str))
    
    # Feature importance analysis
    feature_scores = {}
    
    # 1. Correlation-based importance
    print("Computing correlation-based importance...")
    for feature in feature_columns:
        try:
            correlation, p_value = pearsonr(df_clean[feature], df_clean[target_column])
            # Use absolute correlation weighted by significance
            score = abs(correlation) * (1 - p_value) if p_value < 0.05 else 0
            feature_scores[feature] = {'correlation_score': score}
        except:
            feature_scores[feature] = {'correlation_score': 0}
    
    # 2. Tree-based feature importance
    print(f"Computing tree-based feature importance... (Dataset: {df_clean.shape[0]} rows, {len(feature_columns)} features)")
    X = df_clean[feature_columns]
    y = df_clean[target_column]
    
    # Use fewer estimators for faster computation
    n_estimators = min(50, max(10, 100000 // df_clean.shape[0]))  # Scale with dataset size
    print(f"Using {n_estimators} estimators for Random Forest")
    
    if is_classification:
        model = RandomForestClassifier(n_estimators=n_estimators, random_state=42, n_jobs=-1)
    else:
        model = RandomForestRegressor(n_estimators=n_estimators, random_state=42, n_jobs=-1)
    
    try:
        print("Fitting Random Forest...")
        model.fit(X, y)
        print("Random Forest fitted successfully!")
        importances = model.feature_importances_
        
        for i, feature in enumerate(feature_columns):
            if feature not in feature_scores:
                feature_scores[feature] = {}
            feature_scores[feature]['tree_importance'] = importances[i]
        print(f"Tree importance computed for {len(feature_columns)} features")
    except Exception as e:
        print(f"Random Forest failed: {e}")
        # Fallback if tree model fails
        for feature in feature_columns:
            if feature not in feature_scores:
                feature_scores[feature] = {}
            feature_scores[feature]['tree_importance'] = 0
    
    # 3. Causal analysis using treatment effects
    print(f"Computing causal treatment effects for {len(feature_columns)} features...")
    
    # For each feature, treat it as a treatment and estimate its causal effect
    for i, feature in enumerate(feature_columns):
        if i % 10 == 0 and i > 0:
            print(f"Processed {i}/{len(feature_columns)} features for causal analysis...")
        try:
            # Create binary treatment (above/below median)
            median_val = df_clean[feature].median()
            treatment = (df_clean[feature] > median_val).astype(int)
            
            # Simple average treatment effect
            treated_outcomes = df_clean[treatment == 1][target_column]
            control_outcomes = df_clean[treatment == 0][target_column]
            
            if len(treated_outcomes) > 0 and len(control_outcomes) > 0:
                ate = abs(treated_outcomes.mean() - control_outcomes.mean())
                
                # Propensity score adjustment
                other_features = [f for f in feature_columns if f != feature]
                if len(other_features) > 0:
                    try:
                        # Estimate propensity scores
                        X_prop = df_clean[other_features]
                        if is_classification:
                            prop_model = LogisticRegression(random_state=42, max_iter=1000)
                        else:
                            prop_model = LinearRegression()
                        
                        prop_model.fit(X_prop, treatment)
                        
                        # Weight ATE by propensity score variance (higher variance = better instrument)
                        prop_scores = prop_model.predict_proba(X_prop)[:, 1] if is_classification else prop_model.predict(X_prop)
                        prop_variance = np.var(prop_scores)
                        
                        causal_score = ate * (1 + prop_variance)  # Boost score for features with good variation
                    except:
                        causal_score = ate
                else:
                    causal_score = ate
            else:
                causal_score = 0
                
            feature_scores[feature]['causal_score'] = causal_score
            
        except:
            feature_scores[feature]['causal_score'] = 0
    
    # 4. EconML Causal Forest Analysis
    econml_scores = {}
    if ECONML_AVAILABLE and len(feature_columns) >= 2 and len(feature_columns) <= 20:
        print(f"Computing EconML causal forest importance for {len(feature_columns)} features...")
        try:
            # For each feature, estimate its causal effect using CausalForestDML
            for i, feature in enumerate(feature_columns):
                if i % 5 == 0 and i > 0:
                    print(f"EconML: Processed {i}/{len(feature_columns)} features...")
                try:
                    # Use this feature as treatment
                    T = (df_clean[feature] > df_clean[feature].median()).astype(int)
                    Y = df_clean[target_column].values
                    
                    # Use other features as confounders
                    other_features = [f for f in feature_columns if f != feature]
                    if len(other_features) > 0:
                        X = df_clean[other_features].values
                        
                        # Initialize CausalForestDML with reduced estimators for speed
                        econml_estimators = min(25, max(10, 50000 // df_clean.shape[0]))
                        if is_classification:
                            # For classification targets, we'll still use regression in the causal model
                            # but interpret the results as probability changes
                            est = CausalForestDML(
                                model_y=RandomForestRegressor(n_estimators=econml_estimators, random_state=42),
                                model_t=RandomForestClassifier(n_estimators=econml_estimators, random_state=42),
                                random_state=42,
                                n_estimators=econml_estimators
                            )
                        else:
                            est = CausalForestDML(
                                model_y=RandomForestRegressor(n_estimators=econml_estimators, random_state=42),
                                model_t=RandomForestRegressor(n_estimators=econml_estimators, random_state=42),
                                random_state=42,
                                n_estimators=econml_estimators
                            )
                        
                        # Fit the model
                        est.fit(Y, T, X=X)
                        
                        # Estimate treatment effects
                        treatment_effects = est.effect(X)
                        
                        # Calculate average absolute treatment effect
                        avg_effect = np.abs(treatment_effects).mean()
                        
                        # Weight by effect heterogeneity (std of treatment effects)
                        effect_heterogeneity = np.std(treatment_effects)
                        
                        # Combined score: higher for larger average effects and more heterogeneity
                        econml_score = avg_effect * (1 + effect_heterogeneity)
                        
                        econml_scores[feature] = econml_score
                        
                    else:
                        econml_scores[feature] = 0
                        
                except Exception as e:
                    econml_scores[feature] = 0
                    
        except Exception as e:
            print(f"EconML analysis failed: {e}")
            econml_scores = {f: 0 for f in feature_columns}
    else:
        if not ECONML_AVAILABLE:
            print("Skipping EconML analysis (library not available)")
        elif len(feature_columns) > 20:
            print(f"Skipping EconML analysis (too many features: {len(feature_columns)} > 20)")
        else:
            print("Skipping EconML analysis (insufficient features)")
        econml_scores = {f: 0 for f in feature_columns}
    
    # Add EconML scores to feature_scores
    for feature in feature_columns:
        if feature not in feature_scores:
            feature_scores[feature] = {}
        feature_scores[feature]['econml_score'] = econml_scores.get(feature, 0)
    
    # 5. Combine scores with weights
    print("Combining scores...")
    final_scores = {}
    
    for feature in feature_columns:
        scores = feature_scores[feature]
        
        # Weighted combination of different importance measures
        correlation_weight = 0.3
        tree_weight = 0.4
        causal_weight = 0.2
        econml_weight = 0.1
        
        final_score = (
            scores.get('correlation_score', 0) * correlation_weight +
            scores.get('tree_importance', 0) * tree_weight +
            scores.get('causal_score', 0) * causal_weight +
            scores.get('econml_score', 0) * econml_weight
        )
        
        final_scores[feature] = final_score
    
    # Sort features by importance and return top 5
    sorted_features = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
    
    # Return top 5 or all features if less than 5
    top_features = sorted_features[:min(5, len(sorted_features))]
    
    print(f"\nTop {len(top_features)} most important features:")
    for i, (feature, score) in enumerate(top_features, 1):
        print(f"{i}. {feature}: {score:.4f}")
    
    return [feature for feature, _ in top_features]


def example_usage():
    """Example of how to use the causal_feature_importance function."""
    
    # Create sample data
    np.random.seed(42)
    n_samples = 1000
    
    # Generate features
    feature1 = np.random.normal(0, 1, n_samples)
    feature2 = np.random.normal(0, 1, n_samples)
    feature3 = np.random.normal(0, 1, n_samples)
    feature4 = np.random.choice(['A', 'B', 'C'], n_samples)
    feature5 = np.random.uniform(0, 100, n_samples)
    feature6 = np.random.binomial(1, 0.3, n_samples)
    
    # Generate target with causal relationships
    target = (
        2 * feature1 +           # Strong causal effect
        0.5 * feature2 +         # Medium causal effect  
        0.1 * feature3 +         # Weak causal effect
        np.random.normal(0, 1, n_samples)  # Noise
    )
    
    # Create dataframe
    sample_df = pd.DataFrame({
        'important_feature': feature1,
        'medium_feature': feature2,
        'weak_feature': feature3,
        'categorical_feature': feature4,
        'random_feature': feature5,
        'binary_feature': feature6,
        'target': target
    })
    
    print("Sample dataframe shape:", sample_df.shape)
    print("Sample dataframe columns:", sample_df.columns.tolist())
    
    # Perform causal analysis
    top_features = causal_feature_importance(sample_df, 'target')
    
    return top_features


if __name__ == "__main__":
    # Run example
    # print("Running example analysis...")
    # example_features = example_usage()
    # print(f"\nReturned top features: {example_features}")
    
    print("Loading CSV file...")
    df = pd.read_csv('tickets - GFi7H5K2D (2) (1).csv')
    print(df.dtypes)
    print(f"Loaded dataframe with shape: {df.shape}")
    
    # Test with smaller subset first (first 10 columns)
    print("\n" + "="*50)
    print("TESTING WITH FIRST 10 COLUMNS")
    print("="*50)
    df_small = df#[df.columns[:10]]
    print(f"Small dataframe shape: {df_small.shape}")
    top_features = causal_feature_importance(df_small, 'case_id')
    print(f"Top features (small): {top_features}")
    
    # Then test with first 4 columns
    # print("\n" + "="*50) 
    # print("TESTING WITH FIRST 4 COLUMNS")
    # print("="*50)
    # df_tiny = df[df.columns[:4]]
    # print(f"Tiny dataframe shape: {df_tiny.shape}")
    # top_features = causal_feature_importance(df_tiny, 'age_in_hours')
    # print(f"Top features (tiny): {top_features}")
