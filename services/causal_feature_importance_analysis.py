import polars as pl
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier, GradientBoostingRegressor
from sklearn.feature_selection import SelectKBest, f_classif, f_regression, mutual_info_classif, mutual_info_regression
from sklearn.metrics import classification_report
import warnings
warnings.filterwarnings('ignore')

# Install required packages (run these if not installed):
# pip install econml shap xgboost scipy openai

try:
    from econml.dml import DML, LinearDML
    from econml.dr import DRLearner
    from econml.metalearners import TLearner, SLearner, XLearner
    from scipy.stats import f_oneway, chi2_contingency
    ECONML_AVAILABLE = True
except ImportError as e:
    print(f"Please install missing packages: {e}")
    print("Run: pip install econml scipy")
    ECONML_AVAILABLE = False

# SHAP is optional due to Windows DLL issues
try:
    import shap
    SHAP_AVAILABLE = True
except Exception as e:
    print(f"SHAP not available (optional): {e}")
    SHAP_AVAILABLE = False

# XGBoost is optional
try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False

try:
    from openai import OpenAI
    import os
    LLM_AVAILABLE = True
    # Initialize OpenAI client - will be overridden by external client if provided
    openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
except ImportError:
    LLM_AVAILABLE = False
    openai_client = None

import json
import re

class UniversalCausalAnalyzer:
    """
    Universal causal analyzer that handles any Y-axis type:
    - Numerical columns (regression)
    - Categorical columns (classification)
    - ID columns (volume analysis)
    """

    def __init__(self, data_path, y_column, x_axis_column=None, time_aggregation='daily',
                 max_features=12, openai_client=None, logger=None):
        """
        Initialize universal causal analyzer - FULLY AUTOMATIC

        Args:
            data_path: Path to CSV file
            y_column: Target variable (can be numerical, categorical, or ID for counting)
            x_axis_column: Time column (for volume analysis and temporal exclusion)
            time_aggregation: 'daily', 'weekly', 'monthly' (for ID-based volume analysis)
            max_features: Maximum categorical features to analyze
            openai_client: External OpenAI client to use instead of creating new one
            logger: External logger to use instead of print statements
        """
        # Use external logger or create default
        if logger:
            self.logger = logger
        else:
            import logging
            self.logger = logging.getLogger(__name__)
        
        # Use external OpenAI client or global one
        if openai_client is not None:
            self.openai_client = openai_client
            self.logger.info("Using external OpenAI client for causal analysis")
        else:
            # Access the global variable directly
            self.openai_client = globals().get('openai_client')
            self.logger.info("Using global OpenAI client for causal analysis")
        
        self.logger.info("UNIVERSAL CAUSAL ANALYZER")
        self.logger.info("Automatically detects Y-axis type and chooses appropriate analysis method")
        self.logger.info("LLM determines business relevance - NO HARDCODED LISTS!")
        self.logger.info("=" * 70)

        # Robust CSV reading with comprehensive NULL handling
        # Handles various NULL representations from different data sources:
        # - \N (MySQL/PostgreSQL exports)
        # - Empty strings (Excel, manual CSVs)
        # - Literal NULL strings (various database exports)
        # - Python/Pandas NULL representations
        self.data = pl.read_csv(
            data_path,
            null_values=["\\N", "", "NULL", "null", "None", "N/A", "NA", "nan", "NaN"],
            infer_schema_length=10000,  # Scan more rows for accurate type inference
            try_parse_dates=False,  # Prevent automatic date parsing confusion
            ignore_errors=False  # Fail fast on unexpected issues (better for debugging)
        )
        self.y_column = y_column
        self.x_axis_column = x_axis_column
        self.time_aggregation = time_aggregation
        self.max_features = max_features

        # Analysis results
        self.feature_impacts = {}
        self.label_encoders = {}
        self.analysis_type = None
        self.target_type = None

        # Detect target type and choose analysis method
        self._detect_target_type()

    def _detect_target_type(self):
        """Automatically detect what type of target variable we have"""
        self.logger.info("ANALYZING TARGET VARIABLE: '{}'".format(self.y_column))
        self.logger.info("-" * 50)

        y_data = self.data[self.y_column].drop_nulls()

        # Basic stats
        n_unique = y_data.n_unique()  # Polars method (with underscore)
        total_records = len(y_data)
        uniqueness_ratio = n_unique / total_records

        self.logger.info("Target variable stats:")
        self.logger.info("  • Total records: {:,}".format(total_records))
        self.logger.info("  • Unique values: {:,}".format(n_unique))
        self.logger.info("  • Uniqueness ratio: {:.1%}".format(uniqueness_ratio))
        self.logger.info("  • Data type: {}".format(y_data.dtype))

        # Decision logic
        if y_data.dtype.is_numeric():  # Polars dtype check
            if uniqueness_ratio > 0.8:
                # High uniqueness + numeric = likely ID column
                self.target_type = 'id_for_volume'
                
                # Choose analysis type based on whether time column is available
                if self.x_axis_column:
                    self.analysis_type = 'volume_analysis'
                    self.logger.info("  DETECTED: ID column for VOLUME analysis (WITH time)")
                    self.logger.info("     Will aggregate by time periods and analyze ticket counts")
                else:
                    self.analysis_type = 'segmentation_analysis'
                    self.logger.info("  DETECTED: ID column for SEGMENTATION analysis (NO time)")
                    self.logger.info("     Will analyze factors driving volume without temporal aggregation")

            elif n_unique > 20:
                # Many unique values + numeric = continuous variable
                self.target_type = 'numerical'
                self.analysis_type = 'regression'
                self.logger.info("  DETECTED: NUMERICAL variable")
                self.logger.info("     Will analyze what drives higher/lower values")

            else:
                # Few unique values + numeric = categorical encoded as numbers
                self.target_type = 'categorical'
                self.analysis_type = 'classification'
                self.logger.info("  DETECTED: CATEGORICAL variable (encoded as numbers)")
                self.logger.info("     Will analyze what predicts each category")
        else:
            # String/object type
            if n_unique > 100:
                # Too many categories, might be ID-like
                self.target_type = 'id_for_volume'
                
                # Choose analysis type based on whether time column is available
                if self.x_axis_column:
                    self.analysis_type = 'volume_analysis'
                    self.logger.info("  DETECTED: ID-like column for VOLUME analysis (WITH time)")
                    self.logger.info("     Will aggregate by time periods and analyze counts")
                else:
                    self.analysis_type = 'segmentation_analysis'
                    self.logger.info("  DETECTED: ID-like column for SEGMENTATION analysis (NO time)")
                    self.logger.info("     Will analyze factors driving volume without temporal aggregation")
            else:
                # Reasonable number of categories
                self.target_type = 'categorical'
                self.analysis_type = 'classification'
                self.logger.info("  DETECTED: CATEGORICAL variable")
                self.logger.info("     Will analyze what predicts each category")

        # Show detected approach
        self.logger.info("ANALYSIS APPROACH: {}".format(self.analysis_type.upper()))
        if self.analysis_type == 'volume_analysis':
            self.logger.info("   • Aggregate data by {} periods".format(self.time_aggregation))
            self.logger.info("   • Count occurrences per period")
            self.logger.info("   • Find categorical factors that drive higher counts")
        elif self.analysis_type == 'segmentation_analysis':
            self.logger.info("   • Analyze volume by categorical segments")
            self.logger.info("   • Count occurrences per category combination")
            self.logger.info("   • Find categorical factors that drive higher counts (no time dimension)")
        elif self.analysis_type == 'regression':
            self.logger.info("   • Analyze individual records")
            self.logger.info("   • Find categorical factors that drive higher/lower values")
        elif self.analysis_type == 'classification':
            self.logger.info("   • Analyze individual records")
            self.logger.info("   • Find categorical factors that predict specific categories")

    def _identify_categorical_features(self):
        """Identify categorical features for analysis - Enhanced for business relevance"""
        self.logger.info("IDENTIFYING CATEGORICAL FEATURES")
        self.logger.info("-" * 40)

        categorical_cols = []
        excluded_cols = [self.y_column]

        # Add x_axis_column and its derivatives to exclusion list
        if self.x_axis_column:
            excluded_cols.append(self.x_axis_column)
            # Exclude temporal derivatives
            x_base = self.x_axis_column.lower()
            temporal_keywords = ['day', 'week', 'month', 'year', 'date', 'time']

            for col in self.data.columns:
                col_lower = col.lower()
                if (x_base in col_lower or col_lower in x_base or
                    (any(temp in col_lower for temp in temporal_keywords) and
                     any(temp in x_base for temp in temporal_keywords))):
                    excluded_cols.append(col)

        self.logger.info("Excluding columns: {}".format(excluded_cols))

        # Identify categorical columns with enhanced business logic
        for col in self.data.columns:
            if col not in excluded_cols:
                # Check if categorical
                if (str(self.data[col].dtype) in ['String', 'Utf8', 'Categorical'] or
                    (self.data[col].dtype.is_numeric() and self.data[col].n_unique() <= 50)):

                    unique_count = self.data[col].n_unique()  # Polars method
                    total_rows = len(self.data)
                    unique_ratio = unique_count / total_rows

                    # ADAPTIVE CRITERIA (no hardcoded thresholds - works for any dataset size)
                    # Based on statistical principles, not arbitrary numbers
                    MIN_SAMPLES_PER_CATEGORY = 10   # Statistical minimum for stable estimates
                    MAX_SPARSITY_RATIO = 0.5        # >50% unique = ID column
                    MAX_DOMINANCE_RATIO = 0.95      # One value can't dominate >95%
                    
                    # Check 1: Minimum variation (must have at least 2 different values)
                    if unique_count < 2:
                        self.logger.info("  Excluded {}: No variation ({} unique value)".format(col, unique_count))
                        continue
                    
                    # Check 2: Not an ID column (sparsity check)
                    if unique_ratio > MAX_SPARSITY_RATIO:
                        self.logger.info("  Excluded {}: Too sparse ({:.1%} unique - likely ID column)".format(col, unique_ratio))
                        continue
                    
                    # Check 3: Practical cardinality (need enough samples per category)
                    # Adaptive: adjusts based on dataset size
                    max_practical_categories = total_rows // MIN_SAMPLES_PER_CATEGORY
                    if unique_count > max_practical_categories:
                        self.logger.info("  Excluded {}: {} categories exceeds practical limit ({} for {} rows)".format(
                            col, unique_count, max_practical_categories, total_rows))
                        continue
                    
                    # Check 4: Distribution check (avoid columns where one value dominates)
                    # Polars value_counts() returns DataFrame with 'count' column
                    value_counts = self.data[col].value_counts()
                    most_common_freq = value_counts['count'][0]  # Get first count value
                    dominance_ratio = most_common_freq / total_rows
                    
                    if dominance_ratio > MAX_DOMINANCE_RATIO:
                        self.logger.info("  Excluded {}: One value dominates {:.1%} of data - insufficient variation".format(col, dominance_ratio))
                        continue
                    
                    # Passed all checks - include it!
                    categorical_cols.append(col)

        self.logger.info("Found {} categorical features".format(len(categorical_cols)))
        return categorical_cols

    def _might_be_business_dimension(self, col_name):
        """Detect if column might represent a business dimension vs outcome metric"""
        col_lower = col_name.lower()

        # Business dimension keywords (things you can control/segment by)
        dimension_keywords = [
            'type', 'category', 'tier', 'level', 'status', 'priority', 'origin', 'source',
            'channel', 'region', 'country', 'area', 'zone', 'segment', 'product', 'service',
            'queue', 'team', 'group', 'owner', 'manager', 'assigned', 'flag', 'label',
            'classification', 'grade', 'class', 'kind', 'method', 'approach', 'strategy'
        ]

        # Outcome metric keywords (results of processes, not controllable)
        outcome_keywords = [
            'count', 'total', 'number', 'amount', 'volume', 'time', 'duration', 'age',
            'response', 'resolution', 'handling', 'processing', 'wait', 'delay', 'gap',
            'score', 'rating', 'satisfaction', 'performance', 'metric', 'measure', 'kpi'
        ]

        # Check for dimension keywords
        has_dimension = any(keyword in col_lower for keyword in dimension_keywords)
        has_outcome = any(keyword in col_lower for keyword in outcome_keywords)

        # Prefer dimensions over outcomes
        return has_dimension and not has_outcome

    def _prioritize_features(self, categorical_features):
        """Simple feature prioritization - let LLM do the business logic"""
        self.logger.info("PREPARING FEATURES FOR ANALYSIS")
        self.logger.info("-" * 40)

        self.logger.info("Found categorical features: {}".format(categorical_features))
        self.logger.info("LLM will determine business relevance automatically")

        # Just return the features limited by max_features - no hardcoded business logic
        return categorical_features[:self.max_features]

    def _detect_multicollinearity_volume(self, feature_data, feature_names, threshold=0.8):
        """Detect and remove multicollinear features in volume analysis"""
        print("🔍 DETECTING MULTICOLLINEARITY IN VOLUME DATA")
        print("-" * 50)
        self.logger.info("🔍 DETECTING MULTICOLLINEARITY IN VOLUME DATA")
        self.logger.info("-" * 50)

        # Calculate correlation matrix between ALL features (both mode and entropy)
        corr_matrix = np.corrcoef(feature_data.T)

        # Find highly correlated pairs
        high_corr_pairs = []
        for i in range(len(feature_names)):
            for j in range(i+1, len(feature_names)):
                corr_val = abs(corr_matrix[i, j])
                if not np.isnan(corr_val) and corr_val > threshold:
                    high_corr_pairs.append((feature_names[i], feature_names[j], corr_val))

        if high_corr_pairs:
            print(f"Found {len(high_corr_pairs)} highly correlated feature pairs:")
            self.logger.info(f"Found {len(high_corr_pairs)} highly correlated feature pairs:")
            for feat1, feat2, corr in high_corr_pairs:
                print(f"  🔗 {feat1} <-> {feat2}: r={corr:.3f}")
                self.logger.info(f"  🔗 {feat1} <-> {feat2}: r={corr:.3f}")

        # Enhanced removal logic with business priority
        features_to_remove = set()

        # Group correlated features by their base names
        base_feature_groups = {}
        for feat1, feat2, corr in high_corr_pairs:
            # Extract base feature names
            base1 = feat1.replace('_mode', '').replace('_entropy', '')
            base2 = feat2.replace('_mode', '').replace('_entropy', '')

            # If same base feature (e.g., agent_email_mode vs agent_email_entropy), keep mode
            if base1 == base2:
                if feat1.endswith('_entropy'):
                    features_to_remove.add(feat1)
                    print(f"    → Removing {feat1} (keeping mode over entropy)")
                    self.logger.info(f"    → Removing {feat1} (keeping mode over entropy)")
                elif feat2.endswith('_entropy'):
                    features_to_remove.add(feat2)
                    print(f"    → Removing {feat2} (keeping mode over entropy)")
                    self.logger.info(f"    → Removing {feat2} (keeping mode over entropy)")
                continue

            # Different base features - use business priority and domain knowledge
            is_feat1_business = self._is_business_relevant(base1)
            is_feat2_business = self._is_business_relevant(base2)

            # Check for related email count features (major multicollinearity source)
            email_related_1 = any(term in base1.lower() for term in ['email', 'count', 'inbound', 'outbound'])
            email_related_2 = any(term in base2.lower() for term in ['email', 'count', 'inbound', 'outbound'])

            if email_related_1 and email_related_2:
                # Both are email-related, keep the most general one
                if 'total' in base1.lower() and 'total' not in base2.lower():
                    features_to_remove.add(feat1)
                    print(f"    → Removing {feat1} (total counts are derived metrics)")
                    self.logger.info(f"    → Removing {feat1} (total counts are derived metrics)")
                elif 'total' in base2.lower() and 'total' not in base1.lower():
                    features_to_remove.add(feat2)
                    print(f"    → Removing {feat2} (total counts are derived metrics)")
                    self.logger.info(f"    → Removing {feat2} (total counts are derived metrics)")
                elif 'outbound' in base1.lower() and 'inbound' in base2.lower():
                    features_to_remove.add(feat1)  # Keep inbound over outbound
                    print(f"    → Removing {feat1} (inbound more fundamental than outbound)")
                    self.logger.info(f"    → Removing {feat1} (inbound more fundamental than outbound)")
                elif 'inbound' in base1.lower() and 'outbound' in base2.lower():
                    features_to_remove.add(feat2)  # Keep inbound over outbound
                    print(f"    → Removing {feat2} (inbound more fundamental than outbound)")
                    self.logger.info(f"    → Removing {feat2} (inbound more fundamental than outbound)")
                else:
                    # Default: remove the later one
                    if feature_names.index(feat2) > feature_names.index(feat1):
                        features_to_remove.add(feat2)
                    else:
                        features_to_remove.add(feat1)
                    print(f"    → Removing {feat2} (similar email metrics)")
                    self.logger.info(f"    → Removing {feat2} (similar email metrics)")
                continue

            # Business priority logic
            if is_feat1_business and not is_feat2_business:
                features_to_remove.add(feat2)
                print(f"    → Keeping {feat1} (business priority), removing {feat2}")
                self.logger.info(f"    → Keeping {feat1} (business priority), removing {feat2}")
            elif is_feat2_business and not is_feat1_business:
                features_to_remove.add(feat1)
                print(f"    → Keeping {feat2} (business priority), removing {feat1}")
                self.logger.info(f"    → Keeping {feat2} (business priority), removing {feat1}")
            else:
                # Neither or both are business priority - remove based on actionability
                actionability1 = self._assess_actionability(base1)
                actionability2 = self._assess_actionability(base2)

                if actionability1 > actionability2:
                    features_to_remove.add(feat2)
                    print(f"    → Keeping {feat1} (more actionable), removing {feat2}")
                    self.logger.info(f"    → Keeping {feat1} (more actionable), removing {feat2}")
                elif actionability2 > actionability1:
                    features_to_remove.add(feat1)
                    print(f"    → Keeping {feat2} (more actionable), removing {feat1}")
                    self.logger.info(f"    → Keeping {feat2} (more actionable), removing {feat1}")
                else:
                    # Default: remove the later one
                    if feature_names.index(feat2) > feature_names.index(feat1):
                        features_to_remove.add(feat2)
                        print(f"    → Removing {feat2} (arbitrary choice)")
                        self.logger.info(f"    → Removing {feat2} (arbitrary choice)")
                    else:
                        features_to_remove.add(feat1)
                        print(f"    → Removing {feat1} (arbitrary choice)")
                        self.logger.info(f"    → Removing {feat1} (arbitrary choice)")

        return features_to_remove

    def _is_business_relevant(self, feature_name):
        """Let LLM determine business relevance - no hardcoded logic"""
        return False  # LLM will handle all business logic

    def _assess_actionability(self, feature_name):
        """Simplified actionability - LLM will do the real assessment"""
        return 2  # Default medium - LLM will override this

    def _assess_feature_type(self, feature_name):
        """Assess if feature is likely actionable dimension vs outcome metric"""
        col_lower = feature_name.lower()

        # Business dimension indicators
        dimension_keywords = [
            'type', 'category', 'tier', 'level', 'status', 'priority', 'origin', 'source',
            'channel', 'region', 'country', 'area', 'zone', 'segment', 'product', 'service',
            'queue', 'team', 'group', 'owner', 'manager', 'assigned', 'flag', 'label'
        ]

        # Outcome metric indicators
        outcome_keywords = [
            'count', 'total', 'number', 'amount', 'volume', 'time', 'duration', 'age',
            'response', 'resolution', 'handling', 'processing', 'email'
        ]

        has_dimension = any(keyword in col_lower for keyword in dimension_keywords)
        has_outcome = any(keyword in col_lower for keyword in outcome_keywords)

        if has_dimension and not has_outcome:
            return "Business Dimension (Actionable)"
        elif has_outcome and not has_dimension:
            return "Outcome Metric (Early Warning)"
        elif has_dimension and has_outcome:
            return "Mixed (Review Needed)"
        else:
            return "Unknown"

    def _format_features_for_llm(self, final_results):
        """Format feature results for LLM prompt with enhanced business context"""
        feature_list = []
        feature_names = []

        for feat, data in final_results.items():
            feature_names.append(feat)

            # Format scores nicely
            score_parts = []
            for method, score in data['raw_scores'].items():
                if 'p' not in method:  # Skip p-values in display
                    score_parts.append(f"{method}={score:.2f}")

            # Add feature type assessment
            feature_type = self._assess_feature_type(feat)
            feature_entry = f"- {feat}: {', '.join(score_parts)} | Type: {feature_type}"
            feature_list.append(feature_entry)

        return feature_list, feature_names

    def _call_llm_for_selection(self, final_results, basic_stats):
        """Use LLM to select top 5 features intelligently with enhanced actionability focus"""
        if not LLM_AVAILABLE:
            self.logger.warning("LLM not available, falling back to statistical ranking")
            return None

        if not self.openai_client:
            self.logger.warning("OpenAI client not initialized, falling back to statistical ranking")
            return None

        try:
            self.logger.info(f"📝 Formatting {len(final_results)} features for LLM prompt...")
            feature_list, feature_names = self._format_features_for_llm(final_results)
            self.logger.info(f"   Formatted feature list has {len(feature_list)} entries")
            
            if len(feature_list) == 0:
                self.logger.error("❌ CRITICAL: feature_list is EMPTY! LLM will receive NO features!")
                self.logger.error("   This will cause LLM to respond with generic examples")

            prompt = f"""You are a business intelligence expert analyzing factors that drive {self.y_column} changes.

ANALYSIS CONTEXT:
- Target Variable: {self.y_column}
- Analysis Type: {self.analysis_type}
- Data Pattern: {basic_stats}

CRITICAL UNDERSTANDING - ACTIONABLE vs OUTCOME FEATURES:

ACTIONABLE FEATURES (BUSINESS LEVERS):
- Things management can CONTROL or CHANGE
- Business dimensions you can segment/filter by
- Process choices, routing decisions, resource allocation
- Customer characteristics, service tiers, geographic areas
- Examples: account_tier, service_area, priority, type, origin, status, owner_assignment

OUTCOME FEATURES (EARLY WARNING INDICATORS):
- RESULTS of business processes, not controllable inputs
- Volume/count metrics, timing metrics, performance scores
- Communication volumes, response times, handling duration
- Examples: email_count, total_volume, response_time, resolution_time, satisfaction_score

YOUR TASK:
1. **Identify the business domain** from the target variable and feature names
2. **Classify each feature** as either "Actionable Lever" or "Outcome Indicator"
3. **Prioritize Actionable Levers** - these enable operational improvements
4. **Include 1-2 Outcome Indicators** - these provide early warning signals
5. **Select TOP 5 features** with this priority: Actionable Levers > Outcome Indicators

SELECTION CRITERIA (in priority order):
1. **Actionability**: Can management influence this factor?
2. **Statistical Strength**: High scores across multiple methods
3. **Business Impact**: Does changing this drive meaningful {self.y_column} improvements?
4. **Dashboard Utility**: Useful for filtering, segmentation, and trend analysis
5. **Causal Logic**: True drivers vs downstream effects

FEATURE CANDIDATES:
{chr(10).join(feature_list)}

INSTRUCTIONS:
- Infer business domain from feature patterns
- For each candidate, determine if it's actionable or outcome-based
- Prioritize features that enable operational decisions
- Outcome features should only be selected if they're top statistical performers
- Explain your reasoning focusing on actionability

Format your response as:
DOMAIN: [identified business domain]

FEATURE ANALYSIS:
[For each feature, classify as Actionable/Outcome with reasoning]

TOP 5 FEATURES:
1. FeatureName | Type: Actionable/Outcome | Score | Why Selected
2. FeatureName | Type: Actionable/Outcome | Score | Why Selected
3. FeatureName | Type: Actionable/Outcome | Score | Why Selected
4. FeatureName | Type: Actionable/Outcome | Score | Why Selected
5. FeatureName | Type: Actionable/Outcome | Score | Why Selected"""

            # Make LLM call using GPT-4 for better reasoning
            response = self.openai_client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are a business intelligence expert who understands the difference between actionable business levers vs outcome metrics. You prioritize features that enable operational decisions over statistical correlation."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=1500,
                temperature=0.2
            )

            llm_content = response.choices[0].message.content
            
            # Add validation step
            validation_result = self._validate_llm_selection(llm_content, final_results)
            return validation_result

        except Exception as e:
            self.logger.error("LLM call failed: {}".format(e))
            self.logger.error("Check your OpenAI API key and internet connection")
            self.logger.error("Returning None to prevent fallback - fix LLM setup to continue")
            return None

    def _validate_llm_selection(self, llm_content, final_results):
        """Validate LLM selection for actionability bias"""
        try:
            parsed_result = self._parse_llm_response(llm_content, final_results)

            if not parsed_result:
                return None

            # Count actionable vs outcome features in selection
            selected_features = parsed_result['selected_features']
            actionable_count = 0
            outcome_count = 0

            for item in selected_features:
                feature_name = item['feature']
                feature_type = self._assess_feature_type(feature_name)

                if "Actionable" in feature_type:
                    actionable_count += 1
                elif "Outcome" in feature_type or "Early Warning" in feature_type:
                    outcome_count += 1

            # Validation check
            if outcome_count > actionable_count:
                print(f"⚠️  LLM VALIDATION WARNING:")
                print(f"   Selected {outcome_count} outcome features vs {actionable_count} actionable features")
                print(f"   This suggests focus on metrics rather than levers")
                self.logger.warning(f"⚠️  LLM VALIDATION WARNING:")
                self.logger.warning(f"   Selected {outcome_count} outcome features vs {actionable_count} actionable features")
                self.logger.warning(f"   This suggests focus on metrics rather than levers")

                # Add validation note to results
                parsed_result['validation_warning'] = {
                    'actionable_count': actionable_count,
                    'outcome_count': outcome_count,
                    'message': 'Selection heavily favors outcome metrics over actionable levers'
                }
            else:
                print(f"✅ LLM VALIDATION PASSED:")
                print(f"   Selected {actionable_count} actionable features vs {outcome_count} outcome features")
                print(f"   Good balance of business levers and indicators")
                self.logger.info(f"✅ LLM VALIDATION PASSED:")
                self.logger.info(f"   Selected {actionable_count} actionable features vs {outcome_count} outcome features")
                self.logger.info(f"   Good balance of business levers and indicators")

            return parsed_result

        except Exception as e:
            print(f"⚠️  Validation failed: {e}")
            self.logger.warning(f"⚠️  Validation failed: {e}")
            return self._parse_llm_response(llm_content, final_results)

    def _parse_llm_response(self, llm_content, final_results):
        """Parse LLM response and extract selected features with reasoning"""
        try:
            lines = llm_content.strip().split('\n')

            # Extract domain
            domain = "Unknown"
            for line in lines:
                if line.startswith("DOMAIN:"):
                    domain = line.replace("DOMAIN:", "").strip()
                    break

            # Extract top 5 features
            selected_features = []
            in_features_section = False

            for line in lines:
                if "TOP 5 FEATURES:" in line:
                    in_features_section = True
                    continue

                if in_features_section and line.strip():
                    # Parse format: "1. FeatureName | Score | Context | Reasoning"
                    match = re.match(r'\d+\.\s*([^|]+)\s*\|([^|]*)\|([^|]*)\|(.*)', line)
                    if match:
                        feature_name = match.group(1).strip().lower()
                        llm_score = match.group(2).strip()
                        context = match.group(3).strip()
                        reasoning = match.group(4).strip()

                        # Find matching feature in results (case insensitive)
                        for feat, data in final_results.items():
                            if feat.lower() == feature_name or feature_name in feat.lower():
                                selected_features.append({
                                    'feature': feat,
                                    'data': data,
                                    'llm_reasoning': reasoning,
                                    'domain_context': context,
                                    'llm_score': llm_score
                                })
                                break

            if len(selected_features) > 0:
                return {
                    'domain': domain,
                    'selected_features': selected_features[:5]  # Ensure max 5
                }
            else:
                print("⚠️  Could not parse LLM response properly")
                self.logger.warning("⚠️  Could not parse LLM response properly")
                return None

        except Exception as e:
            print(f"⚠️  Error parsing LLM response: {e}")
            self.logger.warning(f"⚠️  Error parsing LLM response: {e}")
            return None

    def _volume_analysis(self):
        """Enhanced volume analysis with multicollinearity detection and better methods"""
        print("🎯 RUNNING ENHANCED VOLUME ANALYSIS")
        print("-" * 45)
        self.logger.info("🎯 RUNNING ENHANCED VOLUME ANALYSIS")
        self.logger.info("-" * 45)

        if not self.x_axis_column:
            print("❌ Volume analysis requires a time column (x_axis_column)")
            self.logger.error("❌ Volume analysis requires a time column (x_axis_column)")
            return {}

        # Convert time column (Polars method - flexible datetime parsing)
        # Check if column needs datetime conversion
        if self.data[self.x_axis_column].dtype == pl.String or self.data[self.x_axis_column].dtype == pl.Utf8:
            # Use str.to_datetime() which auto-detects format (handles various formats)
            self.data = self.data.with_columns(
                pl.col(self.x_axis_column).str.to_datetime().alias(self.x_axis_column)
            )
        elif not self.data[self.x_axis_column].dtype.is_temporal():
            # If not string and not datetime, try direct cast
            self.data = self.data.with_columns(
                pl.col(self.x_axis_column).cast(pl.Datetime).alias(self.x_axis_column)
            )

        # Create time periods (Polars method)
        if self.time_aggregation == 'daily':
            self.data = self.data.with_columns(
                pl.col(self.x_axis_column).cast(pl.Date).alias('time_period')
            )
        elif self.time_aggregation == 'weekly':
            self.data = self.data.with_columns(
                pl.col(self.x_axis_column).dt.truncate('1w').alias('time_period')
            )
        elif self.time_aggregation == 'monthly':
            self.data = self.data.with_columns(
                pl.col(self.x_axis_column).dt.truncate('1mo').alias('time_period')
            )

        # Get categorical features
        categorical_features = self._identify_categorical_features()
        prioritized_features = self._prioritize_features(categorical_features)

        print(f"Aggregating by {self.time_aggregation} periods...")
        self.logger.info(f"Aggregating by {self.time_aggregation} periods...")

        # Aggregate data
        aggregated_data = []
        for period in self.data['time_period'].unique():
            # Polars filtering method
            period_data = self.data.filter(pl.col('time_period') == period)

            row = {
                'time_period': period,
                'count': len(period_data)  # Target: count per period
            }

            # Add categorical breakdowns (mode per period)
            for feat in prioritized_features:
                if len(period_data[feat].drop_nulls()) > 0:
                    # Polars mode() returns Series, get first element
                    mode_val = period_data[feat].mode()
                    row[f'{feat}_mode'] = mode_val[0] if len(mode_val) > 0 else 'Unknown'

                    # Add diversity measure (entropy)
                    # Polars value_counts() returns DataFrame with columns ['value', 'count']
                    value_counts_df = period_data[feat].value_counts()
                    if len(value_counts_df) > 1:
                        counts = value_counts_df['count'].to_numpy()
                        proportions = counts / len(period_data)
                        entropy = -np.sum(proportions * np.log(proportions + 1e-10))
                        row[f'{feat}_entropy'] = entropy
                    else:
                        row[f'{feat}_entropy'] = 0
                else:
                    row[f'{feat}_mode'] = 'Unknown'
                    row[f'{feat}_entropy'] = 0

            aggregated_data.append(row)

        agg_df = pl.DataFrame(aggregated_data)
        print(f"Created {len(agg_df)} time periods")
        print(f"Average count per {self.time_aggregation}: {agg_df['count'].mean():.1f}")
        self.logger.info(f"Created {len(agg_df)} time periods")
        self.logger.info(f"Average count per {self.time_aggregation}: {agg_df['count'].mean():.1f}")

        # Prepare feature data for analysis
        target = agg_df['count'].to_numpy()
        mode_cols = [col for col in agg_df.columns if col.endswith('_mode')]
        entropy_cols = [col for col in agg_df.columns if col.endswith('_entropy')]

        if len(mode_cols) == 0:
            return {}

        # Encode categorical mode features
        mode_data = agg_df.select(mode_cols)
        encoders = {}
        encoded_mode_arrays = []
        for col in mode_cols:
            le = LabelEncoder()
            encoded = le.fit_transform(mode_data[col].cast(pl.Utf8).to_numpy())
            encoded_mode_arrays.append(encoded)
            encoders[col] = le

        # Combine encoded modes and entropy features
        all_feature_data = np.column_stack([
            np.column_stack(encoded_mode_arrays),
            agg_df.select(entropy_cols).to_numpy() if entropy_cols else np.zeros((len(agg_df), 0))
        ])
        all_feature_names = mode_cols + entropy_cols

        # Detect and remove multicollinear features
        multicollinear_features = self._detect_multicollinearity_volume(
            all_feature_data, all_feature_names, threshold=0.8
        )

        if multicollinear_features:
            # Remove multicollinear features
            remaining_indices = [i for i, name in enumerate(all_feature_names)
                               if name not in multicollinear_features]
            final_feature_data = all_feature_data[:, remaining_indices]
            final_feature_names = [all_feature_names[i] for i in remaining_indices]
            print(f"After multicollinearity removal: {len(final_feature_names)} features remain")
            self.logger.info(f"After multicollinearity removal: {len(final_feature_names)} features remain")
        else:
            final_feature_data = all_feature_data
            final_feature_names = all_feature_names
            print("No multicollinear features detected")
            self.logger.info("No multicollinear features detected")

        # Advanced analysis methods
        results = {}

        print("\n🔬 RUNNING ADVANCED VOLUME ANALYSIS METHODS:")
        self.logger.info("\n🔬 RUNNING ADVANCED VOLUME ANALYSIS METHODS:")

        # Method 1: Enhanced Correlation Analysis
        print("  1. Enhanced correlation analysis...")
        self.logger.info("  1. Enhanced correlation analysis...")
        for i, col in enumerate(final_feature_names):
            if final_feature_data.shape[1] > i:
                # Spearman correlation (handles non-linear relationships better)
                from scipy.stats import spearmanr
                corr, p_val = spearmanr(final_feature_data[:, i], target)
                if not np.isnan(corr):
                    feat_name = col.replace('_mode', '').replace('_entropy', '')
                    if feat_name not in results:
                        results[feat_name] = {}
                    results[feat_name]['spearman_corr'] = abs(corr)
                    results[feat_name]['spearman_p'] = p_val

        # Method 2: Advanced Random Forest with hyperparameter tuning
        print("  2. Optimized Random Forest...")
        self.logger.info("  2. Optimized Random Forest...")
        try:
            from sklearn.model_selection import cross_val_score
            rf_optimized = RandomForestRegressor(
                n_estimators=200,
                max_depth=8,
                min_samples_split=5,
                min_samples_leaf=3,
                random_state=42
            )
            rf_optimized.fit(final_feature_data, target)

            # Cross-validation score
            cv_scores = cross_val_score(rf_optimized, final_feature_data, target, cv=5)
            print(f"     RF CV Score: {cv_scores.mean():.3f} ± {cv_scores.std():.3f}")
            self.logger.info(f"     RF CV Score: {cv_scores.mean():.3f} ± {cv_scores.std():.3f}")

            for i, col in enumerate(final_feature_names):
                feat_name = col.replace('_mode', '').replace('_entropy', '')
                if feat_name not in results:
                    results[feat_name] = {}
                results[feat_name]['rf_importance'] = rf_optimized.feature_importances_[i]
        except Exception as e:
            print(f"     RF analysis failed: {e}")
            self.logger.warning(f"     RF analysis failed: {e}")

        # Method 3: Gradient Boosting (XGBoost style)
        print("  3. Gradient Boosting analysis...")
        self.logger.info("  3. Gradient Boosting analysis...")
        try:
            gb = GradientBoostingRegressor(
                n_estimators=100,
                max_depth=4,
                learning_rate=0.1,
                subsample=0.8,
                random_state=42
            )
            gb.fit(final_feature_data, target)

            for i, col in enumerate(final_feature_names):
                feat_name = col.replace('_mode', '').replace('_entropy', '')
                if feat_name not in results:
                    results[feat_name] = {}
                results[feat_name]['gb_importance'] = gb.feature_importances_[i]
            self.logger.info("     GB analysis completed successfully")
        except Exception as e:
            print(f"     GB analysis failed: {e}")
            self.logger.warning(f"     GB analysis failed: {e}")

        # Method 4: Permutation Importance (most reliable)
        print("  4. Permutation importance...")
        self.logger.info("  4. Permutation importance...")
        try:
            from sklearn.inspection import permutation_importance
            perm_imp = permutation_importance(
                rf_optimized, final_feature_data, target,
                n_repeats=10, random_state=42, scoring='neg_mean_squared_error'
            )

            for i, col in enumerate(final_feature_names):
                feat_name = col.replace('_mode', '').replace('_entropy', '')
                if feat_name not in results:
                    results[feat_name] = {}
                results[feat_name]['perm_importance'] = perm_imp.importances_mean[i]
            self.logger.info("     Permutation importance completed successfully")
        except Exception as e:
            print(f"     Permutation importance failed: {e}")
            self.logger.warning(f"     Permutation importance failed: {e}")

        # Method 5: Statistical significance tests
        print("  5. Statistical significance tests...")
        self.logger.info("  5. Statistical significance tests...")
        mode_only_names = [name for name in final_feature_names if name.endswith('_mode')]
        for i, col in enumerate(mode_only_names):
            try:
                # One-way ANOVA for categorical features
                feat_idx = final_feature_names.index(col)
                feature_values = final_feature_data[:, feat_idx]

                groups = []
                for category in np.unique(feature_values):
                    group_targets = target[feature_values == category]
                    if len(group_targets) > 1:
                        groups.append(group_targets)

                if len(groups) >= 2:
                    from scipy.stats import f_oneway
                    f_stat, p_val = f_oneway(*groups)

                    feat_name = col.replace('_mode', '')
                    if feat_name not in results:
                        results[feat_name] = {}
                    results[feat_name]['anova_f'] = f_stat
                    results[feat_name]['anova_p'] = p_val
            except Exception as e:
                pass

        # Combine results - NO hardcoded business priority weighting
        print("\n📊 COMBINING STATISTICAL RESULTS")
        self.logger.info("\n📊 COMBINING STATISTICAL RESULTS")
        final_results = {}

        for feat, methods in results.items():
            # Normalize scores
            normalized_scores = []
            method_names = []

            for method, score in methods.items():
                if 'p' not in method and score > 0:  # Skip p-values
                    normalized_scores.append(score)
                    method_names.append(method)

            if normalized_scores:
                combined_score = np.mean(normalized_scores)

                final_results[feat] = {
                    'combined_score': combined_score,
                    'methods': method_names,
                    'raw_scores': methods
                }

        # Store results before LLM processing
        self.statistical_results = final_results.copy()

        # Prepare basic stats for LLM
        basic_stats = f"Average count per {self.time_aggregation}: {agg_df['count'].mean():.1f}, " \
                     f"Time periods: {len(agg_df)}, Features analyzed: {len(final_results)}"

        # Call LLM for intelligent feature selection
        print("\n🤖 CALLING LLM FOR INTELLIGENT FEATURE SELECTION...")
        self.logger.info("\n🤖 CALLING LLM FOR INTELLIGENT FEATURE SELECTION...")
        llm_selection = self._call_llm_for_selection(final_results, basic_stats)

        if llm_selection:
            print(f"✅ LLM identified domain: {llm_selection['domain']}")
            print(f"✅ LLM selected {len(llm_selection['selected_features'])} features with business reasoning")
            self.logger.info(f"✅ LLM identified domain: {llm_selection['domain']}")
            self.logger.info(f"✅ LLM selected {len(llm_selection['selected_features'])} features with business reasoning")

            # Store LLM results
            self.llm_selection = llm_selection

            # Create LLM-enhanced results (preserve original scores but add LLM reasoning)
            llm_enhanced_results = {}
            for item in llm_selection['selected_features']:
                feat = item['feature']
                llm_enhanced_results[feat] = {
                    **item['data'],  # Original statistical data
                    'llm_selected': True,
                    'llm_reasoning': item['llm_reasoning'],
                    'domain_context': item['domain_context'],
                    'selection_method': 'LLM + Statistics'
                }

            # Add any remaining features that weren't selected by LLM
            for feat, data in final_results.items():
                if feat not in llm_enhanced_results:
                    llm_enhanced_results[feat] = {
                        **data,
                        'llm_selected': False,
                        'selection_method': 'Statistical Only'
                    }

            final_results = llm_enhanced_results
        else:
            print("❌ LLM REQUIRED - Statistical fallback disabled")
            print("Fix your OpenAI API setup to continue:")
            print("1. Check OPENAI_API_KEY environment variable")
            print("2. Verify API key has credits")
            print("3. Check internet connection")
            self.logger.error("❌ LLM REQUIRED - Statistical fallback disabled")
            self.logger.error("Fix your OpenAI API setup to continue:")
            self.logger.error("1. Check OPENAI_API_KEY environment variable")
            self.logger.error("2. Verify API key has credits")
            self.logger.error("3. Check internet connection")
            raise Exception("LLM functionality required - fix OpenAI setup")

        self.aggregated_data = agg_df
        self.encoders = encoders
        self.multicollinear_removed = multicollinear_features

        return final_results

    def _segmentation_analysis(self):
        """
        Segmentation analysis without time dimension
        Analyzes which categorical factors drive volume (count) differences
        """
        print("🎯 RUNNING SEGMENTATION ANALYSIS (NO TIME)")
        print("-" * 45)
        self.logger.info("🎯 RUNNING SEGMENTATION ANALYSIS (NO TIME)")
        self.logger.info("-" * 45)
        
        # Get categorical features
        categorical_features = self._identify_categorical_features()
        prioritized_features = self._prioritize_features(categorical_features)
        
        if not prioritized_features:
            print("❌ No categorical features found for segmentation")
            self.logger.error("❌ No categorical features found for segmentation")
            return {}
        
        print(f"Found {len(prioritized_features)} categorical features for analysis")
        self.logger.info(f"Found {len(prioritized_features)} categorical features for analysis")
        
        # For segmentation without time, we create aggregated data by categories
        # Count records for each unique combination of top categorical features
        
        # Use top 5 features to avoid combinatorial explosion
        top_features = prioritized_features[:5]
        
        print(f"Using top {len(top_features)} features: {top_features}")
        self.logger.info(f"Using top {len(top_features)} features: {top_features}")
        
        # Aggregate counts by each feature individually
        aggregated_data = []
        for feat in top_features:
            value_counts = self.data[feat].value_counts()
            
            for value, count in value_counts.items():
                row = {
                    'segment_feature': feat,
                    'segment_value': str(value),
                    'count': count
                }
                aggregated_data.append(row)
        
        agg_df = pl.DataFrame(aggregated_data)
        print(f"Created {len(agg_df)} segments")
        self.logger.info(f"Created {len(agg_df)} segments")
        
        # Encode the feature and value columns
        le_feature = LabelEncoder()
        le_value = LabelEncoder()
        
        # Encode using Polars
        agg_df = agg_df.with_columns([
            pl.Series('segment_feature_encoded', le_feature.fit_transform(agg_df['segment_feature'].to_numpy())),
            pl.Series('segment_value_encoded', le_value.fit_transform(agg_df['segment_value'].to_numpy()))
        ])
        
        # Prepare data for regression (predicting count from feature+value)
        X = agg_df[['segment_feature_encoded', 'segment_value_encoded']].to_numpy()
        y = agg_df['count'].to_numpy()
        
        # Run SHAP analysis
        print("Running SHAP analysis...")
        self.logger.info("Running SHAP analysis...")
        
        try:
            model = RandomForestRegressor(n_estimators=100, max_depth=5, random_state=42)
            model.fit(X, y)
            
            explainer = shap.TreeExplainer(model)
            shap_values = explainer.shap_values(X)
            
            # Calculate importance for each original feature
            feature_importance = {}
            
            for i, feat in enumerate(top_features):
                # Get rows for this feature
                feat_mask = agg_df['segment_feature'] == feat
                feat_indices = np.where(feat_mask)[0]
                
                if len(feat_indices) > 0:
                    # Average absolute SHAP value for this feature's segments
                    feat_shap = np.abs(shap_values[feat_indices, :]).mean()
                    feature_importance[feat] = {
                        'shap_importance': float(feat_shap),
                        'total_count': int(agg_df.filter(feat_mask)['count'].sum()),
                        'num_segments': len(feat_indices)
                    }
            
            # Sort by SHAP importance
            sorted_features = sorted(
                feature_importance.items(),
                key=lambda x: x[1]['shap_importance'],
                reverse=True
            )
            
            final_results = {}
            for feat, data in sorted_features:
                final_results[feat] = {
                    **data,
                    'llm_selected': False,
                    'selection_method': 'SHAP Analysis'
                }
            
            print(f"✓ Analysis complete - found {len(final_results)} impacting features")
            self.logger.info(f"✓ Analysis complete - found {len(final_results)} impacting features")
            
            self.aggregated_data = agg_df
            return final_results
            
        except Exception as e:
            print(f"❌ Segmentation analysis failed: {str(e)}")
            self.logger.error(f"❌ Segmentation analysis failed: {str(e)}")
            return {}

    def _regression_analysis(self):
        """Analyze numerical target variables"""
        print("📊 RUNNING REGRESSION ANALYSIS")
        print("-" * 40)
        self.logger.info("📊 RUNNING REGRESSION ANALYSIS")
        self.logger.info("-" * 40)

        # Get features and target
        categorical_features = self._identify_categorical_features()
        prioritized_features = self._prioritize_features(categorical_features)

        # Prepare data
        y = self.data[self.y_column].to_numpy()
        
        # CRITICAL FIX: Remove rows with NaN in target variable
        self.logger.info(f"🧹 DATA CLEANING: Checking for NaN values in target '{self.y_column}'...")
        nan_count = np.isnan(y).sum()
        total_rows = len(y)
        if nan_count > 0:
            self.logger.warning(f"⚠️  Found {nan_count} NaN values ({nan_count/total_rows*100:.2f}%) in target variable")
            # Create mask for valid rows (no NaN in target)
            valid_mask = ~np.isnan(y)
            y = y[valid_mask]
            # Apply same mask to feature data (Polars-compatible filtering)
            self.data = self.data.filter(pl.Series(valid_mask))
            self.logger.info(f"✅ Cleaned data: {len(y)} valid rows remaining (removed {nan_count} rows)")
        else:
            self.logger.info(f"✅ No NaN values in target variable - data is clean ({total_rows} rows)")

        # Encode categorical features
        feature_arrays = []
        for col in prioritized_features:
            col_dtype = self.data[col].dtype
            
            if col_dtype.is_numeric():
                # Already numeric, just extract
                feature_arrays.append(self.data[col].to_numpy())
            else:
                # Non-numeric (strings, categoricals, etc.), needs encoding
                le = LabelEncoder()
                col_data = self.data[col].cast(pl.Utf8).to_numpy()
                encoded_col = le.fit_transform(col_data)
                feature_arrays.append(encoded_col)
                self.label_encoders[col] = le

        # Stack into 2D array
        X = np.column_stack(feature_arrays)

        # Analysis methods
        results = {}

        # Method 1: F-test (ANOVA)
        try:
            self.logger.info("📊 METHOD 1: Running F-test (ANOVA)...")
            from sklearn.feature_selection import f_regression
            f_scores, p_values = f_regression(X, y)
            f_test_count = 0
            for i, feat in enumerate(prioritized_features):
                if not np.isnan(f_scores[i]):
                    results[feat] = {'f_score': f_scores[i], 'p_value': p_values[i]}
                    f_test_count += 1
            self.logger.info(f"✅ F-test completed successfully: {f_test_count} features scored")
        except Exception as e:
            self.logger.error(f"❌ F-test FAILED: {type(e).__name__}: {str(e)}")
            self.logger.error(f"   Data shape: X={X.shape}, y={y.shape}, features={len(prioritized_features)}")

        # Method 2: Random Forest
        try:
            self.logger.info("🌲 METHOD 2: Running Random Forest...")
            rf = RandomForestRegressor(n_estimators=100, random_state=42, max_depth=10, max_samples=min(10000, len(X)))
            rf.fit(X, y)
            rf_count = 0
            for i, feat in enumerate(prioritized_features):
                if feat in results:
                    results[feat]['rf_importance'] = rf.feature_importances_[i]
                else:
                    results[feat] = {'rf_importance': rf.feature_importances_[i]}
                rf_count += 1
            self.logger.info(f"✅ Random Forest completed successfully: {rf_count} features scored")
        except Exception as e:
            self.logger.error(f"❌ Random Forest FAILED: {type(e).__name__}: {str(e)}")
            self.logger.error(f"   Data shape: X={X.shape}, y={y.shape}")

        # Method 3: Permutation Importance
        try:
            self.logger.info("🔄 METHOD 3: Running Permutation Importance...")
            from sklearn.inspection import permutation_importance
            # Reduce repeats and sample size for large datasets
            n_samples = min(5000, len(X))
            perm_imp = permutation_importance(rf, X[:n_samples], y[:n_samples], n_repeats=3, random_state=42)
            perm_count = 0
            for i, feat in enumerate(prioritized_features):
                if feat in results:
                    results[feat]['perm_importance'] = perm_imp.importances_mean[i]
                else:
                    results[feat] = {'perm_importance': perm_imp.importances_mean[i]}
                perm_count += 1
            self.logger.info(f"✅ Permutation Importance completed successfully: {perm_count} features scored")
        except Exception as e:
            self.logger.error(f"❌ Permutation Importance FAILED: {type(e).__name__}: {str(e)}")
            self.logger.error(f"   Attempted on {n_samples if 'n_samples' in locals() else 'N/A'} samples")

        # Method 4: Causal methods (simplified)
        try:
            self.logger.info("⚡ METHOD 4: Running Causal Analysis (TLearner)...")
            from econml.metalearners import TLearner
            causal_results = {}
            # Sample for large datasets
            sample_size = min(3000, len(X))
            X_sample = X[:sample_size]
            y_sample = y[:sample_size]
            
            for feat in prioritized_features[:5]:  # Limit for speed
                feat_idx = prioritized_features.index(feat)
                T = X_sample[:, feat_idx]
                X_conf = np.delete(X_sample, feat_idx, axis=1)

                if len(np.unique(T)) > 1:
                    # Binarize treatment
                    T_binary = (T > np.median(T)).astype(int)
                    if len(np.unique(T_binary)) == 2:
                        tlearner = TLearner(models=RandomForestRegressor(n_estimators=30, random_state=42, max_depth=5))
                        tlearner.fit(y_sample, T_binary, X=X_conf)
                        te = tlearner.effect(X_conf)
                        causal_results[feat] = abs(np.mean(te))

            causal_count = 0
            for feat, effect in causal_results.items():
                if feat in results:
                    results[feat]['causal_effect'] = effect
                else:
                    results[feat] = {'causal_effect': effect}
                causal_count += 1
            self.logger.info(f"✅ Causal Analysis completed successfully: {causal_count} features scored")
        except Exception as e:
            self.logger.error(f"❌ Causal Analysis FAILED: {type(e).__name__}: {str(e)}")
            self.logger.error(f"   Attempted on {sample_size if 'sample_size' in locals() else 'N/A'} samples")

        # Combine results
        self.logger.info("\n" + "="*80)
        self.logger.info("📊 COMBINING STATISTICAL RESULTS")
        self.logger.info("="*80)
        self.logger.info(f"Raw results dictionary has {len(results)} features")
        
        # FALLBACK: If all methods failed, use simple correlation as a safety net
        if len(results) == 0:
            self.logger.warning("⚠️  ALL statistical methods failed! Using FALLBACK correlation method...")
            try:
                for i, feat in enumerate(prioritized_features):
                    # Simple correlation between feature and target
                    feat_values = X[:, i]
                    if len(np.unique(feat_values)) > 1:  # Has variation
                        # Remove any remaining NaN in feature values
                        valid_indices = ~(np.isnan(feat_values) | np.isnan(y))
                        if valid_indices.sum() > 0:
                            corr = np.corrcoef(feat_values[valid_indices], y[valid_indices])[0, 1]
                            if not np.isnan(corr):
                                results[feat] = {'correlation': abs(corr)}
                self.logger.info(f"✅ FALLBACK method succeeded: {len(results)} features scored via correlation")
            except Exception as e:
                self.logger.error(f"❌ Even FALLBACK method failed: {type(e).__name__}: {str(e)}")
        
        final_results = {}
        for feat, methods in results.items():
            # Normalize scores
            normalized_scores = []
            for method, score in methods.items():
                if method != 'p_value':  # Skip p-values for scoring
                    normalized_scores.append(score)

            if normalized_scores:
                final_results[feat] = {
                    'combined_score': np.mean(normalized_scores),
                    'methods': list(methods.keys()),
                    'raw_scores': methods
                }

        self.logger.info(f"✅ Final results dictionary has {len(final_results)} features")
        if len(final_results) > 0:
            self.logger.info(f"   Top 3 features by combined score:")
            sorted_features = sorted(final_results.items(), key=lambda x: x[1]['combined_score'], reverse=True)
            for i, (feat, data) in enumerate(sorted_features[:3], 1):
                self.logger.info(f"      {i}. {feat}: score={data['combined_score']:.4f}, methods={data['methods']}")
        else:
            self.logger.error("❌ CRITICAL: final_results is EMPTY! No features will be sent to LLM!")
            self.logger.error(f"   This means ALL {len(prioritized_features)} features failed statistical analysis")
        
        # Call LLM for selection
        basic_stats = f"Target mean: {y.mean():.2f}, std: {y.std():.2f}, Features: {len(final_results)}"
        self.logger.info(f"\n🤖 Calling LLM with {len(final_results)} features...")
        llm_selection = self._call_llm_for_selection(final_results, basic_stats)

        if not llm_selection:
            raise Exception("LLM functionality required - fix OpenAI setup")

        return self._apply_llm_selection(final_results, llm_selection)

    def _classification_analysis(self):
        """Analyze categorical target variables"""
        print("🏷️  RUNNING CLASSIFICATION ANALYSIS")
        print("-" * 45)
        self.logger.info("🏷️  RUNNING CLASSIFICATION ANALYSIS")
        self.logger.info("-" * 45)

        # Get features and encode target
        categorical_features = self._identify_categorical_features()
        prioritized_features = self._prioritize_features(categorical_features)

        # Encode target
        if self.data[self.y_column].dtype == 'object':
            le_target = LabelEncoder()
            y = le_target.fit_transform(self.data[self.y_column].astype(str))
            self.target_encoder = le_target
            print(f"Target categories: {list(le_target.classes_)}")
            self.logger.info(f"Target categories: {list(le_target.classes_)}")
        else:
            y = self.data[self.y_column].to_numpy()

        # Encode features
        feature_arrays = []
        for col in prioritized_features:
            col_dtype = self.data[col].dtype
            
            if col_dtype.is_numeric():
                # Already numeric, just extract
                feature_arrays.append(self.data[col].to_numpy())
            else:
                # Non-numeric (strings, categoricals, etc.), needs encoding
                le = LabelEncoder()
                col_data = self.data[col].cast(pl.Utf8).to_numpy()
                encoded_col = le.fit_transform(col_data)
                feature_arrays.append(encoded_col)
                self.label_encoders[col] = le

        # Stack into 2D array
        X = np.column_stack(feature_arrays)

        # Analysis methods
        results = {}

        # Method 1: Chi-square test
        try:
            for i, feat in enumerate(prioritized_features):
                # Create contingency table using Polars
                contingency_df = pl.DataFrame({
                    'feature': X[:, i],
                    'target': y
                }).groupby(['feature', 'target']).count()
                
                # Pivot to create contingency matrix
                unique_features = np.unique(X[:, i])
                unique_targets = np.unique(y)
                contingency = np.zeros((len(unique_features), len(unique_targets)))
                
                for row in contingency_df.iter_rows(named=True):
                    feat_idx = np.where(unique_features == row['feature'])[0][0]
                    target_idx = np.where(unique_targets == row['target'])[0][0]
                    contingency[feat_idx, target_idx] = row['count']
                
                chi2, p_val, dof, expected = chi2_contingency(contingency)
                results[feat] = {'chi2': chi2, 'chi2_p_value': p_val}
        except:
            pass

        # Method 2: Mutual Information
        try:
            mi_scores = mutual_info_classif(X, y)
            for i, feat in enumerate(prioritized_features):
                if feat in results:
                    results[feat]['mutual_info'] = mi_scores[i]
                else:
                    results[feat] = {'mutual_info': mi_scores[i]}
        except:
            pass

        # Method 3: Random Forest
        try:
            rf = RandomForestClassifier(n_estimators=100, random_state=42)
            rf.fit(X, y)
            for i, feat in enumerate(prioritized_features):
                if feat in results:
                    results[feat]['rf_importance'] = rf.feature_importances_[i]
                else:
                    results[feat] = {'rf_importance': rf.feature_importances_[i]}
        except:
            pass

        # Combine results
        final_results = {}
        for feat, methods in results.items():
            # Normalize and combine scores
            scores = []
            for method, score in methods.items():
                if 'p_value' not in method and score > 0:
                    scores.append(score)

            if scores:
                final_results[feat] = {
                    'combined_score': np.mean(scores),
                    'methods': list(methods.keys()),
                    'raw_scores': methods
                }

        # Call LLM for selection
        n_classes = len(np.unique(y))
        basic_stats = f"Classes: {n_classes}, Features: {len(final_results)}"
        llm_selection = self._call_llm_for_selection(final_results, basic_stats)

        if not llm_selection:
            raise Exception("LLM functionality required - fix OpenAI setup")

        return self._apply_llm_selection(final_results, llm_selection)

    def _apply_llm_selection(self, final_results, llm_selection):
        """Apply LLM selection to results"""
        llm_enhanced_results = {}
        for item in llm_selection['selected_features']:
            feat = item['feature']
            llm_enhanced_results[feat] = {
                **item['data'],
                'llm_selected': True,
                'llm_reasoning': item['llm_reasoning'],
                'domain_context': item['domain_context'],
                'selection_method': 'LLM + Statistics'
            }

        for feat, data in final_results.items():
            if feat not in llm_enhanced_results:
                llm_enhanced_results[feat] = {
                    **data,
                    'llm_selected': False,
                    'selection_method': 'Statistical Only'
                }

        self.llm_selection = llm_selection
        return llm_enhanced_results

    def run_analysis(self):
        """Run the appropriate analysis based on target type"""
        print(f"🚀 STARTING {self.analysis_type.upper()}")
        print("=" * 50)
        self.logger.info(f"🚀 STARTING {self.analysis_type.upper()}")
        self.logger.info("=" * 50)

        if self.analysis_type == 'volume_analysis':
            self.feature_impacts = self._volume_analysis()
        elif self.analysis_type == 'segmentation_analysis':
            self.feature_impacts = self._segmentation_analysis()
        elif self.analysis_type == 'regression':
            self.feature_impacts = self._regression_analysis()
        elif self.analysis_type == 'classification':
            self.feature_impacts = self._classification_analysis()

        print(f"✅ Analysis completed. Found {len(self.feature_impacts)} impacting features.")
        self.logger.info(f"✅ Analysis completed. Found {len(self.feature_impacts)} impacting features.")

    def get_top_drivers(self, n_features=5):
        """Get top driving factors with LLM-enhanced selection"""
        if not self.feature_impacts:
            print("❌ No analysis results found. Run analysis first.")
            self.logger.error("❌ No analysis results found. Run analysis first.")
            return []

        # Check if we have LLM results
        has_llm_results = hasattr(self, 'llm_selection') and self.llm_selection is not None

        if has_llm_results:
            # Use LLM-selected order
            llm_selected = [item['feature'] for item in self.llm_selection['selected_features']]
            other_features = [feat for feat in self.feature_impacts.keys() if feat not in llm_selected]

            # Combine: LLM selected first, then others by score
            other_sorted = sorted(
                [(feat, self.feature_impacts[feat]) for feat in other_features],
                key=lambda x: x[1].get('combined_score', 0),
                reverse=True
            )

            # Create final sorted list
            sorted_features = []
            for feat in llm_selected[:n_features]:
                if feat in self.feature_impacts:
                    sorted_features.append((feat, self.feature_impacts[feat]))

            # Fill remaining slots with statistical top performers
            remaining_slots = n_features - len(sorted_features)
            for feat, data in other_sorted[:remaining_slots]:
                sorted_features.append((feat, data))
        else:
            # Should not happen with new version
            raise Exception("LLM selection required")

        # Display results
        print(f"\n🏆 TOP {n_features} DRIVERS OF {self.y_column.upper()}")
        print("=" * 80)
        print(f"Analysis Type: {self.analysis_type.upper()}")
        print(f"Target Type: {self.target_type.upper()}")
        print(f"🤖 LLM-Enhanced Selection | Domain Identified: {self.llm_selection['domain']}")
        self.logger.info(f"\n🏆 TOP {n_features} DRIVERS OF {self.y_column.upper()}")
        self.logger.info("=" * 80)
        self.logger.info(f"Analysis Type: {self.analysis_type.upper()}")
        self.logger.info(f"Target Type: {self.target_type.upper()}")
        self.logger.info(f"🤖 LLM-Enhanced Selection | Domain Identified: {self.llm_selection['domain']}")

        # Show validation results
        if 'validation_warning' in self.llm_selection:
            warning = self.llm_selection['validation_warning']
            print(f"⚠️  VALIDATION WARNING: {warning['message']}")
            print(f"   Actionable: {warning['actionable_count']}, Outcomes: {warning['outcome_count']}")
            self.logger.warning(f"⚠️  VALIDATION WARNING: {warning['message']}")
            self.logger.warning(f"   Actionable: {warning['actionable_count']}, Outcomes: {warning['outcome_count']}")

        # Show multicollinearity removal if applicable
        if hasattr(self, 'multicollinear_removed') and self.multicollinear_removed:
            print(f"🔗 Removed {len(self.multicollinear_removed)} multicollinear features")
            self.logger.info(f"🔗 Removed {len(self.multicollinear_removed)} multicollinear features")

        print("-" * 80)
        self.logger.info("-" * 80)

        # Display features with enhanced information
        for i, (feature, data) in enumerate(sorted_features[:n_features]):
            is_llm_selected = data.get('llm_selected', False)
            selection_icon = "🤖" if is_llm_selected else "📊"
            selection_method = data.get('selection_method', 'Statistical Only')

            # Assess feature type
            feature_type = self._assess_feature_type(feature)
            type_icon = "🎯" if "Actionable" in feature_type else "📊" if "Outcome" in feature_type or "Early Warning" in feature_type else "❓"

            print(f"\n{i+1}. {selection_icon} {type_icon} {feature.upper()}")
            print(f"   Combined Score: {data.get('combined_score', 0):.4f}")
            print(f"   Selection Method: {selection_method}")
            print(f"   Feature Type: {feature_type}")
            print(f"   Statistical Methods: {', '.join(data.get('methods', []))}")
            self.logger.info(f"\n{i+1}. {selection_icon} {type_icon} {feature.upper()}")
            self.logger.info(f"   Combined Score: {data.get('combined_score', 0):.4f}")
            self.logger.info(f"   Selection Method: {selection_method}")
            self.logger.info(f"   Feature Type: {feature_type}")
            self.logger.info(f"   Statistical Methods: {', '.join(data.get('methods', []))}")

            # Show LLM reasoning if available
            if 'llm_reasoning' in data:
                print(f"   🧠 LLM Reasoning: {data['llm_reasoning']}")
                self.logger.info(f"   🧠 LLM Reasoning: {data['llm_reasoning']}")

            if 'domain_context' in data:
                print(f"   🏢 Domain Context: {data['domain_context']}")
                self.logger.info(f"   🏢 Domain Context: {data['domain_context']}")

            # Show category impacts
            self._show_category_impact(feature)

        # Enhanced business priority analysis
        print(f"\n🎯 SELECTION ANALYSIS:")
        print("-" * 50)
        self.logger.info(f"\n🎯 SELECTION ANALYSIS:")
        self.logger.info("-" * 50)

        llm_selected_count = sum(1 for _, data in sorted_features[:n_features] if data.get('llm_selected', False))
        print(f"🤖 LLM-Selected Features: {llm_selected_count}/{n_features}")
        print(f"📊 Statistical-Only Features: {n_features - llm_selected_count}/{n_features}")
        self.logger.info(f"🤖 LLM-Selected Features: {llm_selected_count}/{n_features}")
        self.logger.info(f"📊 Statistical-Only Features: {n_features - llm_selected_count}/{n_features}")

        # Show LLM domain understanding
        print(f"🏢 Domain Identified: {self.llm_selection['domain']}")
        self.logger.info(f"🏢 Domain Identified: {self.llm_selection['domain']}")

        # Enhanced actionability assessment
        print(f"\n💡 ACTIONABILITY ASSESSMENT:")
        print("-" * 35)
        self.logger.info(f"\n💡 ACTIONABILITY ASSESSMENT:")
        self.logger.info("-" * 35)

        actionable_features = []
        outcome_features = []
        mixed_features = []

        for feature, data in sorted_features[:n_features]:
            feature_type = self._assess_feature_type(feature)
            if "Actionable" in feature_type:
                actionable_features.append(feature)
            elif "Outcome" in feature_type or "Early Warning" in feature_type:
                outcome_features.append(feature)
            else:
                mixed_features.append(feature)

        if actionable_features:
            print(f"✅ {len(actionable_features)} ACTIONABLE features (business levers):")
            self.logger.info(f"✅ {len(actionable_features)} ACTIONABLE features (business levers):")
            for feat in actionable_features:
                print(f"   🎯 {feat}")
                self.logger.info(f"   🎯 {feat}")

        if outcome_features:
            print(f"⚠️  {len(outcome_features)} OUTCOME features (early warning indicators):")
            self.logger.info(f"⚠️  {len(outcome_features)} OUTCOME features (early warning indicators):")
            for feat in outcome_features:
                print(f"   📊 {feat}")
                self.logger.info(f"   📊 {feat}")

        if mixed_features:
            print(f"❓ {len(mixed_features)} MIXED features (review needed):")
            self.logger.info(f"❓ {len(mixed_features)} MIXED features (review needed):")
            for feat in mixed_features:
                print(f"   ❓ {feat}")
                self.logger.info(f"   ❓ {feat}")

        print(f"\n🎯 INTERPRETATION:")
        print("-" * 20)
        print("✅ LLM applied enhanced business intelligence + statistical analysis")
        print("✅ Features classified by actionability vs outcome type")
        self.logger.info(f"\n🎯 INTERPRETATION:")
        self.logger.info("-" * 20)
        self.logger.info("✅ LLM applied enhanced business intelligence + statistical analysis")
        self.logger.info("✅ Features classified by actionability vs outcome type")

        if len(actionable_features) >= len(outcome_features):
            print("✅ Good balance: Focus on actionable levers with outcome indicators")
            self.logger.info("✅ Good balance: Focus on actionable levers with outcome indicators")
        else:
            print("⚠️  Consider: More outcome metrics selected than actionable levers")
            print("💡 Suggestion: Review if business dimensions were available in data")
            self.logger.warning("⚠️  Consider: More outcome metrics selected than actionable levers")
            self.logger.warning("💡 Suggestion: Review if business dimensions were available in data")

        if self.analysis_type == 'volume_analysis':
            print("• Use actionable features for capacity planning and resource allocation")
            print("• Use outcome features as early warning systems for volume spikes")
            self.logger.info("• Use actionable features for capacity planning and resource allocation")
            self.logger.info("• Use outcome features as early warning systems for volume spikes")

        return sorted_features[:n_features]

    def _show_category_impact(self, feature):
        """Show impact by category"""
        try:
            if self.analysis_type == 'volume_analysis' and hasattr(self, 'aggregated_data'):
                # Volume analysis category impact
                mode_col = f'{feature}_mode'
                if mode_col in self.aggregated_data.columns:
                    avg_by_category = (
                        self.aggregated_data
                        .groupby(mode_col)
                        .agg(pl.col('count').mean().alias('avg_count'))
                        .sort('avg_count', descending=True)
                    )
                    if len(avg_by_category) > 1:
                        print(f"   📈 Volume by category:")
                        for row in avg_by_category.head(3).iter_rows(named=True):
                            print(f"      • {row[mode_col]}: {row['avg_count']:.1f} per {self.time_aggregation}")

            elif feature in self.label_encoders:
                # Individual record analysis
                feature_data = self.data[feature].to_numpy()
                target_data = self.data[self.y_column].to_numpy()

                category_stats = {}
                for category in self.label_encoders[feature].classes_:
                    mask = self.data[feature] == category
                    if np.sum(mask) >= 10:  # At least 10 samples
                        if self.target_type == 'numerical':
                            category_stats[category] = np.mean(target_data[mask])
                        elif self.target_type == 'categorical':
                            # Most common target category for this feature category
                            mode_target = pl.Series(target_data[mask]).mode()
                            if len(mode_target) > 0:
                                category_stats[category] = mode_target[0]

                if category_stats:
                    print(f"   📈 Impact by category:")
                    sorted_cats = sorted(category_stats.items(), key=lambda x: x[1], reverse=True)
                    for cat, impact in sorted_cats[:3]:
                        print(f"      • {cat}: {impact:.2f}")

        except Exception as e:
            pass  # Silently skip if category analysis fails

# Integrated function for external applications
def analyze_universal_causal_impact_integrated(csv_path, y_column, x_axis_column=None,
                                             openai_client=None, logger=None,
                                             time_aggregation='daily', max_features=12):
    """
    Integrated version that uses external OpenAI client and logger
    
    Args:
        csv_path: Path to CSV file
        y_column: Target variable (numerical, categorical, or ID for counting)
        x_axis_column: Time column (for volume analysis and temporal exclusion)
        openai_client: External OpenAI client to use
        logger: External logger to use
        time_aggregation: 'daily', 'weekly', 'monthly' (for volume analysis)
        max_features: Maximum categorical features to analyze

    Returns:
        analyzer, top_drivers
    """
    analyzer = UniversalCausalAnalyzer(
        csv_path, y_column, x_axis_column, time_aggregation, max_features,
        openai_client=openai_client, logger=logger
    )

    analyzer.run_analysis()
    top_drivers = analyzer.get_top_drivers(n_features=5)

    return analyzer, top_drivers

# Main function - Universal analyzer
def analyze_universal_causal_impact(csv_path, y_column, x_axis_column=None,
                                  time_aggregation='daily', max_features=12):
    """
    Universal causal analysis - REQUIRES LLM for intelligent feature selection

    Args:
        csv_path: Path to CSV file
        y_column: Target variable (numerical, categorical, or ID for counting)
        x_axis_column: Time column (for volume analysis and temporal exclusion)
        time_aggregation: 'daily', 'weekly', 'monthly' (for volume analysis)
        max_features: Maximum categorical features to analyze

    Returns:
        analyzer, top_drivers

    Raises:
        Exception: If OpenAI API is not properly configured
    """

    analyzer = UniversalCausalAnalyzer(
        csv_path, y_column, x_axis_column, time_aggregation, max_features
    )

    analyzer.run_analysis()
    top_drivers = analyzer.get_top_drivers(n_features=5)

    return analyzer, top_drivers

# Example usage and setup
if __name__ == "__main__":
    # ==============================================
    # MAIN ENTRY POINT - JUST CHANGE THESE 3 LINES
    # ==============================================

    CSV_PATH = "your_data.csv"              # 👈 YOUR CSV FILE PATH
    Y_COLUMN = "case_id"                    # 👈 YOUR TARGET COLUMN
    X_AXIS_COLUMN = "datestr"               # 👈 YOUR TIME COLUMN

    print("🎯 UNIVERSAL CAUSAL ANALYZER")
    print("🤖 LLM-REQUIRED VERSION (No Statistical Fallback)")
    print("=" * 70)
    print()

    # Check LLM setup
    print("🤖 LLM SETUP CHECK:")
    print("-" * 20)
    if LLM_AVAILABLE:
        print("✅ OpenAI package installed")
        import os
        if os.getenv('OPENAI_API_KEY'):
            print("✅ OPENAI_API_KEY found")
        else:
            print("❌ OPENAI_API_KEY not set!")
            print("Run: export OPENAI_API_KEY='your-api-key-here'")
            exit(1)
    else:
        print("❌ OpenAI package not installed!")
        print("Run: pip install openai>=1.0.0")
        exit(1)

    print()
    print(f"📊 ANALYZING: {Y_COLUMN}")
    print(f"📅 Time Column: {X_AXIS_COLUMN}")
    print(f"📁 Data File: {CSV_PATH}")
    print()

    try:
        # MAIN FUNCTION CALL
        analyzer, top_drivers = analyze_universal_causal_impact(
            csv_path=CSV_PATH,
            y_column=Y_COLUMN,
            x_axis_column=X_AXIS_COLUMN,
            time_aggregation="daily",
            max_features=12
        )

        print(f"\n🎯 SUCCESS! Analysis completed with LLM intelligence")
        print(f"Domain identified: {analyzer.llm_selection['domain']}")
        print(f"Top {len(top_drivers)} drivers found")

        # Show quick summary
        print(f"\n🔥 QUICK SUMMARY:")
        print("-" * 20)
        for i, (feature, data) in enumerate(top_drivers[:3]):
            reasoning = data.get('llm_reasoning', 'Statistical selection')
            print(f"{i+1}. {feature} - {reasoning}")

    except FileNotFoundError:
        print(f"❌ Error: Could not find file '{CSV_PATH}'")
        print("Update CSV_PATH with your actual file path")

    except KeyError as e:
        print(f"❌ Error: Column {e} not found in dataset")
        print("Check that Y_COLUMN and X_AXIS_COLUMN match your data exactly")

    except Exception as e:
        print(f"❌ Error: {e}")
        if "OpenAI" in str(e) or "API key" in str(e):
            print("\n🔧 FIX YOUR OPENAI SETUP:")
            print("1. Get API key: https://platform.openai.com/account/api-keys")
            print("2. Set environment variable: export OPENAI_API_KEY='sk-...'")
            print("3. Make sure you have credits in your account")

    print(f"\n📝 TO USE THIS CODE:")
    print("1. Update CSV_PATH, Y_COLUMN, X_AXIS_COLUMN above")
    print("2. Set your OPENAI_API_KEY environment variable")
    print("3. Run the script")
    print("4. Get LLM-enhanced business insights!")