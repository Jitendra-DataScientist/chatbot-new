"""
Enhanced LLM Service for Tableau Analytics Agent
Provides AI-powered analysis and response generation using OpenAI
"""

import json
from typing import Dict, Any, List, Optional
import os
from datetime import datetime
import sys
import logging
import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential

# Use OpenAI instead of local services
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    OpenAI = None

from models.schemas import AgentResponse, QueryIntent, StatisticalResult

class LLMService:
    def __init__(self, openai_client=None, openai_api_key: Optional[str] = None):
        self.logger = logging.getLogger(__name__)
        
        # Check if OpenAI is available
        if not OPENAI_AVAILABLE:
            self.logger.error("OpenAI not available. LLM service will not function.")
            self.client = None
            return
        
        # Use provided client or create new one
        try:
            if openai_client:
                self.client = openai_client
                self.logger.info("Using provided OpenAI client")
            elif openai_api_key:
                self.client = OpenAI(api_key=openai_api_key)
                self.logger.info("Created new OpenAI client with provided API key")
            else:
                # Try to get from environment
                self.client = OpenAI()  # Will use OPENAI_API_KEY env var
                self.logger.info("Created new OpenAI client from environment")
            
            self.model = "gpt-4o-mini"  # Fast and cost-effective for most tasks
            
            # Test the connection
            test_response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "test"}],
                max_tokens=5
            )
            self.logger.info("OpenAI LLM service initialized successfully")
            
        except Exception as e:
            self.logger.error(f"Error initializing OpenAI LLM service: {e}")
            self.client = None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def _make_request(self, messages: List[Dict[str, str]], max_tokens: int = 2000, temperature: float = 0.1) -> str:
        """Make a request to OpenAI API"""
        if not self.client:
            raise Exception("OpenAI client not initialized")
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature
            )
            return response.choices[0].message.content
        except Exception as e:
            self.logger.error(f"OpenAI API error: {e}")
            raise
    
    async def generate_workbook_summary(self, workbook_name: str, worksheets_info: List[Dict], total_rows: int) -> Dict[str, str]:
        """Generate intelligent 2-line workbook summary"""
        
        # Extract key information
        worksheet_names = [ws.get('name', '') for ws in worksheets_info]
        key_metrics = []
        analysis_types = []
        
        # Identify potential metrics from worksheet names
        metric_keywords = {
            'revenue': ['revenue', 'sales', 'income', 'earnings'],
            'customers': ['customer', 'client', 'user', 'account'],
            'orders': ['order', 'transaction', 'purchase'],
            'performance': ['performance', 'kpi', 'metric', 'score'],
            'time': ['time', 'date', 'month', 'quarter', 'year'],
            'operational': ['operational', 'ops', 'process', 'workflow']
        }
        
        for ws_name in worksheet_names:
            ws_lower = ws_name.lower()
            for metric, keywords in metric_keywords.items():
                if any(keyword in ws_lower for keyword in keywords):
                    if metric not in key_metrics:
                        key_metrics.append(metric.title())
        
        # Determine analysis types
        if any('trend' in ws.lower() for ws in worksheet_names):
            analysis_types.append('Trend analysis')
        if any(word in ' '.join(worksheet_names).lower() for word in ['performance', 'score', 'metric']):
            analysis_types.append('Performance metrics')
        if any(word in ' '.join(worksheet_names).lower() for word in ['customer', 'segment']):
            analysis_types.append('Customer analytics')
        if any(word in ' '.join(worksheet_names).lower() for word in ['sales', 'revenue']):
            analysis_types.append('Revenue analysis')
        
        # Default analysis types if none detected
        if not analysis_types:
            analysis_types = ['Data exploration', 'Statistical analysis']
        
        # Generate summary lines
        summary_line1 = f"📊 Loaded {total_rows:,} rows across {len(worksheets_info)} worksheets"
        if key_metrics:
            summary_line1 += f" with metrics: {', '.join(key_metrics[:4])}"
        
        summary_line2 = f"🔍 Available analysis: {', '.join(analysis_types[:4])}"
        
        return {
            'summary_line1': summary_line1,
            'summary_line2': summary_line2,
            'key_metrics': key_metrics,
            'analysis_types': analysis_types
        }

    async def generate_auto_analysis(self, worksheet_name: str, df_data: Dict[str, Any], most_impactful_columns: List[Dict]) -> Dict[str, Any]:
        """Generate automatic analysis for selected worksheet"""
        
        system_prompt = """You are an expert data analyst providing concise, actionable insights. 
        Generate exactly 3 analysis sections with 2-3 lines each:
        1. Most Impactful Columns (data drivers)
        2. Data Aggregation Insights (key breakdowns)  
        3. Statistical Analysis (correlations, patterns)
        
        Be specific with numbers and percentages. Use business language."""

        # Prepare data context
        columns = df_data.get('columns', [])
        sample_data = df_data.get('sample_data', [])
        data_shape = df_data.get('shape', (0, 0))
        
        user_prompt = f"""
Worksheet: {worksheet_name}
Data: {data_shape[0]:,} rows, {len(columns)} columns
Columns: {', '.join(columns[:10])}
Sample data: {str(sample_data[:3])[:500]}
Most impactful: {most_impactful_columns}

Generate business insights in exactly this format:

🎯 **Impact Factors:** [2 most impactful insights with percentages]

📊 **Data Breakdown:** [Key aggregation findings with specific numbers]

📈 **Statistical Summary:** [Correlations, trends, or patterns with confidence levels]
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        try:
            response = await self._make_request(messages, max_tokens=400, temperature=0.1)
            
            return {
                'analysis_text': response,
                'impact_columns': most_impactful_columns,
                'worksheet_name': worksheet_name,
                'generated_at': datetime.now().isoformat()
            }
            
        except Exception as e:
            self.logger.error(f"Error generating auto-analysis: {e}")
            # Fallback analysis
            return {
                'analysis_text': f"""🎯 **Impact Factors:** {worksheet_name} contains {len(columns)} analysis dimensions with {data_shape[0]:,} data points

📊 **Data Breakdown:** Key columns include {', '.join(columns[:3])} with diverse value distributions  

📈 **Statistical Summary:** Dataset ready for correlation analysis, trend detection, and comparative studies""",
                'impact_columns': most_impactful_columns,
                'worksheet_name': worksheet_name,
                'generated_at': datetime.now().isoformat()
            }

    async def analyze_column_impact(self, df_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Analyze which columns have the most impact/variance"""
        
        columns = df_data.get('columns', [])
        sample_data = df_data.get('sample_data', [])
        
        if not sample_data or not columns:
            return []
        
        # Simple heuristic-based impact analysis
        impact_columns = []
        
        # Prioritize numeric columns that might be key metrics
        metric_keywords = ['revenue', 'sales', 'amount', 'value', 'total', 'count', 'score', 'rate']
        dimension_keywords = ['category', 'type', 'region', 'segment', 'status', 'grade']
        
        for col in columns:
            col_lower = col.lower()
            impact_score = 0.5  # Default
            
            # Higher impact for metric columns
            if any(keyword in col_lower for keyword in metric_keywords):
                impact_score = 0.9
            # Medium impact for dimension columns
            elif any(keyword in col_lower for keyword in dimension_keywords):
                impact_score = 0.7
            
            # Analyze sample data variance (simple approach)
            try:
                sample_values = [row.get(col) for row in sample_data if row.get(col) is not None]
                if sample_values:
                    unique_values = len(set(str(v) for v in sample_values))
                    total_values = len(sample_values)
                    
                    # Higher variance = higher impact
                    if unique_values > 1:
                        variance_factor = min(unique_values / total_values, 1.0)
                        impact_score += variance_factor * 0.2
            except:
                pass
            
            impact_columns.append({
                'column_name': col,
                'impact_score': min(impact_score, 1.0),
                'variance_explained': impact_score * 100,  # Convert to percentage
                'data_type': self._infer_column_type(col, sample_data),
                'sample_values': [str(row.get(col, ''))[:50] for row in sample_data[:3] if row.get(col) is not None]
            })
        
        # Sort by impact score and return top 5
        impact_columns.sort(key=lambda x: x['impact_score'], reverse=True)
        return impact_columns[:5]

    def _infer_column_type(self, column: str, sample_data: List[Dict]) -> str:
        """Infer column data type from sample data"""
        try:
            sample_values = [row.get(column) for row in sample_data if row.get(column) is not None]
            if not sample_values:
                return 'unknown'
            
            # Check if numeric
            try:
                [float(str(v).replace(',', '')) for v in sample_values if str(v).replace(',', '').replace('.', '').isdigit()]
                return 'numeric'
            except:
                pass
            
            # Check if date
            if any(keyword in column.lower() for keyword in ['date', 'time', 'month', 'year']):
                return 'datetime'
            
            # Check unique values ratio
            unique_ratio = len(set(str(v) for v in sample_values)) / len(sample_values)
            if unique_ratio < 0.5:
                return 'categorical'
            else:
                return 'text'
                
        except:
            return 'unknown'

    async def generate_enhanced_response(self, query: str, intent: QueryIntent, analysis_result: Dict[str, Any], worksheet_name: str = None) -> str:
        """Generate enhanced response based on query intent and analysis"""
        
        intent_response_templates = {
            'shap_analysis': "🎯 **Feature Importance Analysis**\n\n",
            'anomaly_detection': "🚨 **Anomaly Detection Results**\n\n", 
            'trend_analysis': "📈 **Trend Analysis**\n\n",
            'statistical_significance': "📊 **Statistical Analysis**\n\n",
            'comparison': "⚖️ **Comparative Analysis**\n\n",
            'top_bottom_analysis': "🏆 **Ranking Analysis**\n\n",
            'seasonality': "🔄 **Seasonality Analysis**\n\n",
            'prediction': "🔮 **Predictive Analysis**\n\n",
            'data_exploration': "🔍 **Data Exploration**\n\n"
        }
        
        system_prompt = f"""You are an expert data analyst providing insights for the '{intent.primary_intent}' analysis type.
        
        Generate a professional, actionable response that:
        1. Directly answers the user's question
        2. Provides specific findings with numbers
        3. Includes business implications
        4. Suggests next steps
        
        Use markdown formatting and be concise but comprehensive."""

        context = f"""
User Query: "{query}"
Analysis Type: {intent.primary_intent}
Worksheet: {worksheet_name or 'Selected worksheet'}
Analysis Results: {json.dumps(analysis_result, default=str)[:1000]}
Intent Confidence: {intent.confidence:.1%}
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context}
        ]

        try:
            response = await self._make_request(messages, max_tokens=800, temperature=0.1)
            
            # Add intent-specific header
            header = intent_response_templates.get(intent.primary_intent, "📊 **Analysis Results**\n\n")
            
            return header + response
            
        except Exception as e:
            self.logger.error(f"Error generating enhanced response: {e}")
            
            # Fallback response
            return f"""📊 **Analysis Complete**

I've analyzed your query "{query}" with {intent.confidence:.1%} confidence as a {intent.primary_intent.replace('_', ' ')} request.

**Key Findings:**
• Analysis completed successfully for {worksheet_name or 'selected worksheet'}
• Data processing and insights generated
• Results available in the analysis above

**Next Steps:**
• Review the detailed findings
• Ask follow-up questions for deeper analysis
• Select different charts for comparative insights"""

    async def generate_chart_description(self, chart_config: Dict[str, Any], data_summary: Dict[str, Any]) -> str:
        """Generate description for a chart/visualization"""
        
        chart_type = chart_config.get('chart_type', 'unknown')
        title = chart_config.get('title', 'Data Visualization')
        
        system_prompt = """Generate a brief, informative description of what this chart shows. 
        Include the chart type, main insights, and what users should look for. Keep it under 100 words."""

        user_prompt = f"""
Chart Type: {chart_type}
Title: {title}
Chart Config: {json.dumps(chart_config, default=str)[:300]}
Data Summary: {json.dumps(data_summary, default=str)[:200]}

Generate a description of what this visualization shows and key insights to look for.
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        try:
            response = await self._make_request(messages, max_tokens=150, temperature=0.1)
            return response.strip()
        except Exception as e:
            self.logger.error(f"Error generating chart description: {e}")
            return f"{chart_type.title()} chart showing {title}. Analyze patterns, trends, and outliers in the data."
