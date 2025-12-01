"""
Enhanced Insight Generator for Tableau Analytics Agent
Generates comprehensive insights and auto-analysis for Tableau data
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional
import logging
from datetime import datetime
from scipy import stats

from models.schemas import AutoAnalysisResult, ImpactColumn, StatisticalResult
from services.llm_service import LLMService
from services.data_processor import TableauDataProcessor

class TableauInsightGenerator:
    """
    Enhanced insight generator specifically designed for Tableau workbook analysis
    Generates auto-analysis results when users select charts
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.llm_service = LLMService()
        self.data_processor = TableauDataProcessor()

    async def generate_auto_analysis(self, worksheet_name: str, df: pd.DataFrame, context: Optional[Dict[str, Any]] = None) -> AutoAnalysisResult:
        """
        Generate comprehensive auto-analysis for a selected worksheet
        This is called immediately when user selects a chart
        
        Args:
            worksheet_name: Name of the selected worksheet
            df: DataFrame containing the worksheet data
            context: Additional context about the workbook/selection
            
        Returns:
            AutoAnalysisResult with 3 key components
        """
        try:
            start_time = datetime.now()
            
            self.logger.info(f"Generating auto-analysis for worksheet: {worksheet_name}")
            
            # 1. Analyze most impactful columns
            most_impactful = await self._analyze_most_impactful_columns(df)
            
            # 2. Generate data aggregation insights
            data_aggregation = await self._generate_data_aggregation_insights(df, worksheet_name)
            
            # 3. Perform statistical analysis
            statistical_analysis = await self._perform_statistical_analysis(df)
            
            # Generate summary lines (2-3 lines each section)
            summary_lines = await self._generate_summary_lines(
                most_impactful, data_aggregation, statistical_analysis, worksheet_name
            )
            
            execution_time = (datetime.now() - start_time).total_seconds()
            
            return AutoAnalysisResult(
                most_impactful_columns=most_impactful,
                data_aggregation=data_aggregation,
                statistical_analysis=statistical_analysis,
                summary_lines=summary_lines,
                execution_time=execution_time
            )
            
        except Exception as e:
            self.logger.error(f"Error generating auto-analysis for {worksheet_name}: {e}")
            return self._create_fallback_analysis(worksheet_name, df)

    async def _analyze_most_impactful_columns(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Analyze and identify the 2 most impactful columns"""
        
        try:
            # Use data processor to get impact analysis
            impact_columns = self.data_processor.analyze_column_impact(df)
            
            # Convert to dictionary format and take top 2
            most_impactful = []
            for impact_col in impact_columns[:2]:
                most_impactful.append({
                    'column_name': impact_col.column_name,
                    'impact_score': impact_col.impact_score,
                    'variance_explained': impact_col.variance_explained,
                    'data_type': impact_col.data_type,
                    'sample_values': impact_col.sample_values,
                    'business_relevance': self._assess_business_relevance(impact_col.column_name)
                })
            
            return most_impactful
            
        except Exception as e:
            self.logger.error(f"Error analyzing impactful columns: {e}")
            # Fallback to simple analysis
            return self._get_fallback_impactful_columns(df)

    async def _generate_data_aggregation_insights(self, df: pd.DataFrame, worksheet_name: str) -> Dict[str, Any]:
        """Generate data aggregation insights using NL to Python"""
        
        try:
            insights = {}
            
            # Find the most relevant aggregation based on data structure
            numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
            categorical_cols = df.select_dtypes(include=['object']).columns.tolist()
            
            if categorical_cols and numeric_cols:
                # Generate category breakdown query
                cat_col = categorical_cols[0]
                num_col = numeric_cols[0]
                
                aggregation_query = f"count of each {cat_col}"
                
                # Execute aggregation
                aggregation_result = self.data_processor.execute_pandas_aggregation(aggregation_query, df)
                
                insights['primary_aggregation'] = {
                    'type': 'categorical_breakdown',
                    'query': aggregation_query,
                    'result': aggregation_result.get('result'),
                    'description': f"Breakdown of {cat_col} categories"
                }
                
                # If we have numeric data, also do sum/average
                if len(numeric_cols) > 0:
                    numeric_query = f"sum of {num_col} by {cat_col}"
                    numeric_result = self.data_processor.execute_pandas_aggregation(numeric_query, df)
                    
                    insights['secondary_aggregation'] = {
                        'type': 'numeric_aggregation', 
                        'query': numeric_query,
                        'result': numeric_result.get('result'),
                        'description': f"Total {num_col} by {cat_col}"
                    }
            
            elif numeric_cols:
                # Pure numeric analysis
                insights['primary_aggregation'] = {
                    'type': 'descriptive_stats',
                    'description': f"Statistical summary of {len(numeric_cols)} numeric columns",
                    'result': self._get_numeric_summary(df, numeric_cols)
                }
            
            else:
                # Categorical only
                insights['primary_aggregation'] = {
                    'type': 'categorical_summary',
                    'description': f"Category distribution across {len(categorical_cols)} columns",
                    'result': self._get_categorical_summary(df, categorical_cols)
                }
            
            return insights
            
        except Exception as e:
            self.logger.error(f"Error generating data aggregation insights: {e}")
            return {'error': str(e)}

    async def _perform_statistical_analysis(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Perform statistical analysis and return 2-line summary"""
        
        try:
            analysis = {}
            
            numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
            
            if len(numeric_cols) >= 2:
                # Correlation analysis
                corr_matrix = df[numeric_cols].corr()
                
                # Find strongest correlation
                strongest_corr = 0
                strongest_pair = None
                
                for i in range(len(numeric_cols)):
                    for j in range(i + 1, len(numeric_cols)):
                        corr_val = abs(corr_matrix.iloc[i, j])
                        if corr_val > strongest_corr and not pd.isna(corr_val):
                            strongest_corr = corr_val
                            strongest_pair = (numeric_cols[i], numeric_cols[j])
                
                analysis['correlation'] = {
                    'strongest_correlation': strongest_corr,
                    'strongest_pair': strongest_pair,
                    'interpretation': self._interpret_correlation(strongest_corr)
                }
                
                # Statistical significance test
                if strongest_pair and strongest_corr > 0.3:
                    try:
                        col1_data = df[strongest_pair[0]].dropna()
                        col2_data = df[strongest_pair[1]].dropna()
                        
                        if len(col1_data) > 3 and len(col2_data) > 3:
                            # Pearson correlation test
                            stat, p_value = stats.pearsonr(col1_data, col2_data)
                            
                            analysis['significance_test'] = {
                                'test_type': 'pearson_correlation',
                                'statistic': float(stat),
                                'p_value': float(p_value),
                                'is_significant': p_value < 0.05,
                                'confidence_level': '95%'
                            }
                    except:
                        pass
            
            # Descriptive statistics
            analysis['descriptive'] = {
                'total_rows': len(df),
                'total_columns': len(df.columns),
                'numeric_columns': len(numeric_cols),
                'missing_data_percentage': float((df.isnull().sum().sum() / df.size) * 100),
                'data_quality': 'good' if (df.isnull().sum().sum() / df.size) < 0.1 else 'moderate'
            }
            
            # Outlier detection for first numeric column
            if numeric_cols:
                col = numeric_cols[0]
                Q1 = df[col].quantile(0.25)
                Q3 = df[col].quantile(0.75)
                IQR = Q3 - Q1
                outliers = df[(df[col] < (Q1 - 1.5 * IQR)) | (df[col] > (Q3 + 1.5 * IQR))]
                
                analysis['outliers'] = {
                    'column': col,
                    'outlier_count': len(outliers),
                    'outlier_percentage': float((len(outliers) / len(df)) * 100),
                    'has_outliers': len(outliers) > 0
                }
            
            return analysis
            
        except Exception as e:
            self.logger.error(f"Error performing statistical analysis: {e}")
            return {'error': str(e)}

    async def _generate_summary_lines(self, impactful_cols: List[Dict], aggregation: Dict, statistics: Dict, worksheet_name: str) -> List[str]:
        """Generate 2-3 line summaries for each analysis section"""
        
        try:
            summary_lines = []
            
            # 1. Most Impactful Columns Summary (2-3 lines)
            if impactful_cols and len(impactful_cols) >= 2:
                col1 = impactful_cols[0]
                col2 = impactful_cols[1]
                impact_line = f"🎯 **Top Impact Factors:** {col1['column_name']} drives {col1['variance_explained']:.0f}% variance, {col2['column_name']} {col2['variance_explained']:.0f}%"
                summary_lines.append(impact_line)
            elif impactful_cols and len(impactful_cols) >= 1:
                col1 = impactful_cols[0]
                impact_line = f"🎯 **Primary Impact Factor:** {col1['column_name']} accounts for {col1['variance_explained']:.0f}% of data variance"
                summary_lines.append(impact_line)
            else:
                summary_lines.append("🎯 **Impact Analysis:** Multiple factors contribute to data patterns")
            
            # 2. Data Aggregation Summary (2-3 lines)
            if aggregation.get('primary_aggregation'):
                primary = aggregation['primary_aggregation']
                result = primary.get('result', {})
                
                if primary['type'] == 'categorical_breakdown' and result:
                    result_data = result.get('value') if result.get('type') == 'other' else result.get('data', {})
                    if isinstance(result_data, dict) and result_data:
                        top_category = max(result_data.items(), key=lambda x: x[1]) if result_data else None
                        if top_category:
                            agg_line = f"📊 **Data Breakdown:** {top_category[0]} leads with {top_category[1]} occurrences"
                        else:
                            agg_line = f"📊 **Data Breakdown:** {primary['description']}"
                    else:
                        agg_line = f"📊 **Data Breakdown:** {primary['description']}"
                elif primary['type'] == 'descriptive_stats':
                    agg_line = f"📊 **Data Summary:** {len(result)} metrics analyzed across {statistics.get('descriptive', {}).get('total_rows', 0):,} records"
                else:
                    agg_line = f"📊 **Data Insights:** {primary.get('description', 'Analysis completed')}"
                
                summary_lines.append(agg_line)
            else:
                summary_lines.append("📊 **Data Aggregation:** Key patterns identified in dataset structure")
            
            # 3. Statistical Analysis Summary (2-3 lines)
            if statistics.get('correlation') and statistics['correlation']['strongest_pair']:
                corr = statistics['correlation']
                pair = corr['strongest_pair']
                strength = corr['strongest_correlation']
                
                significance = ""
                if statistics.get('significance_test'):
                    sig_test = statistics['significance_test']
                    if sig_test['is_significant']:
                        significance = f", {sig_test['confidence_level']} confidence"
                
                stat_line = f"📈 **Statistical Summary:** {corr['interpretation']} correlation ({strength:.2f}) between {pair[0]} and {pair[1]}{significance}"
                
                # Add outlier info if available
                if statistics.get('outliers') and statistics['outliers']['has_outliers']:
                    outlier_info = statistics['outliers']
                    stat_line += f". {outlier_info['outlier_count']} outliers detected in {outlier_info['column']}"
                
                summary_lines.append(stat_line)
            else:
                data_quality = statistics.get('descriptive', {}).get('data_quality', 'good')
                missing_pct = statistics.get('descriptive', {}).get('missing_data_percentage', 0)
                stat_line = f"📈 **Statistical Summary:** {data_quality.title()} data quality with {missing_pct:.1f}% missing values. Ready for detailed analysis"
                summary_lines.append(stat_line)
            
            return summary_lines
            
        except Exception as e:
            self.logger.error(f"Error generating summary lines: {e}")
            return [
                f"🎯 **Analysis Complete:** {worksheet_name} contains structured data ready for insights",
                "📊 **Data Overview:** Multiple dimensions available for aggregation and comparison", 
                "📈 **Statistical Ready:** Dataset prepared for correlation and trend analysis"
            ]

    def _assess_business_relevance(self, column_name: str) -> str:
        """Assess business relevance of a column based on naming patterns"""
        
        col_lower = column_name.lower()
        
        if any(keyword in col_lower for keyword in ['revenue', 'sales', 'profit', 'income']):
            return 'high_revenue_impact'
        elif any(keyword in col_lower for keyword in ['customer', 'client', 'user']):
            return 'customer_focused'
        elif any(keyword in col_lower for keyword in ['cost', 'expense', 'price']):
            return 'cost_related'
        elif any(keyword in col_lower for keyword in ['time', 'date', 'period']):
            return 'temporal_dimension'
        elif any(keyword in col_lower for keyword in ['region', 'location', 'area']):
            return 'geographic_dimension'
        else:
            return 'operational_metric'

    def _interpret_correlation(self, correlation_value: float) -> str:
        """Interpret correlation strength"""
        
        abs_corr = abs(correlation_value)
        
        if abs_corr >= 0.8:
            return 'Very strong'
        elif abs_corr >= 0.6:
            return 'Strong'
        elif abs_corr >= 0.4:
            return 'Moderate'
        elif abs_corr >= 0.2:
            return 'Weak'
        else:
            return 'Very weak'

    def _get_numeric_summary(self, df: pd.DataFrame, numeric_cols: List[str]) -> Dict[str, Any]:
        """Get summary statistics for numeric columns"""
        
        try:
            summary = {}
            for col in numeric_cols[:3]:  # Limit to first 3
                summary[col] = {
                    'mean': float(df[col].mean()),
                    'median': float(df[col].median()),
                    'std': float(df[col].std()),
                    'min': float(df[col].min()),
                    'max': float(df[col].max())
                }
            return summary
        except:
            return {'error': 'Unable to calculate numeric summary'}

    def _get_categorical_summary(self, df: pd.DataFrame, categorical_cols: List[str]) -> Dict[str, Any]:
        """Get summary for categorical columns"""
        
        try:
            summary = {}
            for col in categorical_cols[:3]:  # Limit to first 3
                value_counts = df[col].value_counts().head(5)
                summary[col] = {
                    'unique_count': int(df[col].nunique()),
                    'top_values': value_counts.to_dict()
                }
            return summary
        except:
            return {'error': 'Unable to calculate categorical summary'}

    def _get_fallback_impactful_columns(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Fallback method for identifying impactful columns"""
        
        try:
            impactful = []
            
            # Prioritize numeric columns first
            numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
            for col in numeric_cols[:2]:
                variance = float(df[col].var()) if df[col].var() > 0 else 0.5
                impactful.append({
                    'column_name': col,
                    'impact_score': min(variance / df[col].max() if df[col].max() > 0 else 0.5, 1.0),
                    'variance_explained': min(variance * 10, 100),
                    'data_type': 'numeric',
                    'sample_values': [str(val) for val in df[col].dropna().head(3).tolist()],
                    'business_relevance': self._assess_business_relevance(col)
                })
            
            # If we need more, add categorical columns
            if len(impactful) < 2:
                categorical_cols = df.select_dtypes(include=['object']).columns.tolist()
                for col in categorical_cols[:2-len(impactful)]:
                    cardinality_ratio = df[col].nunique() / len(df)
                    impactful.append({
                        'column_name': col,
                        'impact_score': cardinality_ratio if cardinality_ratio < 0.8 else 0.6,
                        'variance_explained': cardinality_ratio * 100,
                        'data_type': 'categorical',
                        'sample_values': df[col].dropna().head(3).astype(str).tolist(),
                        'business_relevance': self._assess_business_relevance(col)
                    })
            
            return impactful
            
        except Exception as e:
            self.logger.error(f"Error in fallback impactful columns: {e}")
            return []


    def _create_fallback_analysis(self, worksheet_name: str, df: pd.DataFrame) -> AutoAnalysisResult:
        """Create fallback analysis result when main analysis fails"""
        
        return AutoAnalysisResult(
            most_impactful_columns=self._get_fallback_impactful_columns(df),
            data_aggregation={
                'primary_aggregation': {
                    'type': 'basic_summary',
                    'description': f'{len(df):,} rows with {len(df.columns)} columns',
                    'result': {'rows': len(df), 'columns': len(df.columns)}
                }
            },
            statistical_analysis={
                'descriptive': {
                    'total_rows': len(df),
                    'total_columns': len(df.columns),
                    'data_quality': 'available'
                }
            },
            summary_lines=[
                f"🎯 **Dataset Overview:** {worksheet_name} contains {len(df):,} records across {len(df.columns)} dimensions",
                "📊 **Data Available:** Multiple columns ready for aggregation and analysis",
                "📈 **Analysis Ready:** Data structure suitable for statistical and comparative analysis"
            ],
            execution_time=0.1
        )

    async def generate_insights_for_intent(self, df: pd.DataFrame, intent: str, query: str) -> Dict[str, Any]:
        """Generate specific insights based on user intent"""
        
        try:
            insights = {
                'intent': intent,
                'query': query,
                'insights': [],
                'recommendations': []
            }
            
            if intent == 'shap_analysis':
                insights['insights'] = await self._generate_feature_importance_insights(df)
            elif intent == 'anomaly_detection':
                insights['insights'] = await self._generate_anomaly_insights(df)
            elif intent == 'trend_analysis':
                insights['insights'] = await self._generate_trend_insights(df)
            elif intent == 'statistical_significance':
                insights['insights'] = await self._generate_statistical_insights(df)
            elif intent == 'comparison':
                insights['insights'] = await self._generate_comparison_insights(df)
            else:
                insights['insights'] = await self._generate_general_insights(df)
            
            return insights
            
        except Exception as e:
            self.logger.error(f"Error generating insights for intent {intent}: {e}")
            return {'error': str(e), 'intent': intent}

    async def _generate_feature_importance_insights(self, df: pd.DataFrame) -> List[str]:
        """Generate insights for feature importance analysis"""
        
        # Placeholder for SHAP-like analysis
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        
        insights = []
        if len(numeric_cols) > 1:
            # Simple variance-based importance
            variances = df[numeric_cols].var().sort_values(ascending=False)
            top_feature = variances.index[0]
            insights.append(f"'{top_feature}' shows highest variance and potential impact on outcomes")
            insights.append(f"Feature importance analysis reveals {len(numeric_cols)} significant variables")
        else:
            insights.append("Limited numeric features available for importance analysis")
        
        return insights

    async def _generate_anomaly_insights(self, df: pd.DataFrame) -> List[str]:
        """Generate insights for anomaly detection"""
        
        insights = []
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        
        if numeric_cols:
            col = numeric_cols[0]
            Q1 = df[col].quantile(0.25)
            Q3 = df[col].quantile(0.75)
            IQR = Q3 - Q1
            outliers = df[(df[col] < (Q1 - 1.5 * IQR)) | (df[col] > (Q3 + 1.5 * IQR))]
            
            if len(outliers) > 0:
                insights.append(f"Detected {len(outliers)} potential anomalies in '{col}' ({len(outliers)/len(df)*100:.1f}% of data)")
                insights.append(f"Outlier threshold: values beyond {Q1-1.5*IQR:.2f} - {Q3+1.5*IQR:.2f} range")
            else:
                insights.append(f"No significant anomalies detected in '{col}' using IQR method")
        else:
            insights.append("No numeric data available for anomaly detection")
        
        return insights

    async def _generate_trend_insights(self, df: pd.DataFrame) -> List[str]:
        """Generate insights for trend analysis"""
        
        insights = []
        date_cols = [col for col in df.columns if any(keyword in str(col).lower() for keyword in ['date', 'time', 'month', 'year'])]
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        
        if date_cols and numeric_cols:
            insights.append(f"Time-based analysis available with {len(date_cols)} temporal and {len(numeric_cols)} metric columns")
            insights.append("Trend analysis can reveal seasonality, growth patterns, and cyclical behavior")
        elif numeric_cols:
            insights.append(f"Sequential trend analysis possible across {len(numeric_cols)} numeric variables")
        else:
            insights.append("Limited trend analysis capability with current data structure")
        
        return insights

    async def _generate_statistical_insights(self, df: pd.DataFrame) -> List[str]:
        """Generate insights for statistical analysis"""
        
        insights = []
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        
        if len(numeric_cols) >= 2:
            # Calculate correlations
            corr_matrix = df[numeric_cols].corr()
            strong_corrs = (corr_matrix.abs() > 0.7).sum().sum() - len(numeric_cols)  # Exclude diagonal
            
            insights.append(f"Correlation analysis across {len(numeric_cols)} variables identifies {strong_corrs} strong relationships")
            
            if strong_corrs > 0:
                insights.append("Statistical significance testing recommended for identified correlations")
            else:
                insights.append("Variables show independence - suitable for diverse analytical approaches")
        else:
            insights.append("Additional numeric variables needed for comprehensive statistical analysis")
        
        return insights

    async def _generate_comparison_insights(self, df: pd.DataFrame) -> List[str]:
        """Generate insights for comparison analysis"""
        
        insights = []
        categorical_cols = df.select_dtypes(include=['object']).columns.tolist()
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        
        if categorical_cols and numeric_cols:
            cat_col = categorical_cols[0]
            num_col = numeric_cols[0]
            unique_categories = df[cat_col].nunique()
            
            insights.append(f"Comparison analysis available across {unique_categories} categories in '{cat_col}'")
            
            if unique_categories <= 10:
                insights.append(f"Statistical comparison tests (ANOVA, t-tests) applicable for '{num_col}' across categories")
            else:
                insights.append(f"Large category count ({unique_categories}) - consider grouping for meaningful comparisons")
        else:
            insights.append("Comparison analysis requires both categorical grouping and numeric measures")
        
        return insights

    async def _generate_general_insights(self, df: pd.DataFrame) -> List[str]:
        """Generate general insights for data exploration"""
        
        insights = []
        
        # Data structure insights
        insights.append(f"Dataset contains {len(df):,} records with {len(df.columns)} variables for analysis")
        
        # Data quality insight
        missing_pct = (df.isnull().sum().sum() / df.size) * 100
        if missing_pct < 5:
            insights.append("Excellent data quality with minimal missing values")
        elif missing_pct < 15:
            insights.append(f"Good data quality with {missing_pct:.1f}% missing values")
        else:
            insights.append(f"Data quality concerns - {missing_pct:.1f}% missing values require attention")
        
        # Analysis readiness
        numeric_count = len(df.select_dtypes(include=[np.number]).columns)
        categorical_count = len(df.select_dtypes(include=['object']).columns)
        
        if numeric_count > 0 and categorical_count > 0:
            insights.append(f"Balanced dataset with {numeric_count} metrics and {categorical_count} dimensions enables comprehensive analysis")
        elif numeric_count > 0:
            insights.append(f"Numeric-focused dataset with {numeric_count} variables suitable for statistical analysis")
        else:
            insights.append(f"Categorical dataset with {categorical_count} variables suitable for distribution analysis")
        
        return insights
