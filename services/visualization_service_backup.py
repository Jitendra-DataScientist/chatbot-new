"""
Pure Plotly Visualization Service for Tableau Analytics Agent
Generates intelligent, context-aware charts using only Plotly
"""

import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
from typing import Dict, Any, Optional, List, Tuple
import logging
from datetime import datetime

from models.schemas import PlotlyVisualization, ChartType, QueryIntent

class PlotlyVisualizationService:
    """
    Creates intelligent visualizations using pure Plotly based on query context
    Replaces matplotlib/seaborn with query-driven chart generation
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.default_theme = "plotly_white"
        self.color_palette = px.colors.qualitative.Set3
        
        # Chart type mapping based on query intent
        self.intent_chart_mapping = {
            'trend_analysis': ['line', 'scatter'],
            'comparison': ['bar', 'box'],
            'top_bottom_analysis': ['bar', 'pie'],
            'statistical_significance': ['scatter', 'heatmap'],
            'anomaly_detection': ['line', 'scatter', 'box'],
            'data_exploration': ['histogram', 'scatter'],
            'seasonality': ['line', 'heatmap'],
            'shap_analysis': ['bar', 'treemap'],
            'prediction': ['line', 'scatter']
        }

    def create_intelligent_visualization(self, query: str, df: pd.DataFrame, intent: Optional[QueryIntent] = None) -> PlotlyVisualization:
        """
        Create intelligent visualization based on query and data characteristics
        
        Args:
            query: User's natural language query
            df: DataFrame to visualize
            intent: Parsed query intent (optional)
            
        Returns:
            PlotlyVisualization with Plotly JSON
        """
        try:
            self.logger.info(f"Creating visualization for query: '{query}'")
            self.logger.info(f"DataFrame shape: {df.shape}")
            
            if df.empty:
                return self._create_empty_chart_message()
            
            # Analyze query intent for chart type
            chart_intent = self._analyze_visualization_intent(query, intent)
            
            # Analyze data structure
            data_analysis = self._analyze_data_structure(df)
            
            # Determine optimal chart type and configuration
            chart_type, chart_config = self._determine_optimal_chart(chart_intent, data_analysis, df)
            
            # Generate the Plotly chart
            plotly_json = self._generate_plotly_chart(chart_type, chart_config, df)
            
            # Generate insights about the visualization
            insights = self._generate_chart_insights(chart_type, chart_config, df)
            
            return PlotlyVisualization(
                chart_type=ChartType(chart_type),
                plotly_json=plotly_json,
                title=chart_config.get('title', 'Data Visualization'),
                description=chart_config.get('description', f'{chart_type.title()} chart visualization'),
                insights=insights,
                metadata={
                    'query': query,
                    'data_shape': df.shape,
                    'chart_config': chart_config,
                    'generated_at': datetime.now().isoformat()
                }
            )
            
        except Exception as e:
            self.logger.error(f"Error creating visualization: {e}")
            return self._create_error_chart(str(e))

    def _analyze_visualization_intent(self, query: str, intent: Optional[QueryIntent] = None) -> Dict[str, Any]:
        """Analyze what type of visualization the query is asking for"""
        
        query_lower = query.lower()
        
        viz_intent = {
            'explicit_chart_type': None,
            'comparison_needed': False,
            'trend_analysis': False,
            'distribution_analysis': False,
            'ranking_analysis': False,
            'correlation_analysis': False,
            'temporal_analysis': False,
            'categorical_breakdown': False
        }
        
        # Explicit chart type mentions
        chart_mentions = {
            'bar': ['bar chart', 'bar graph', 'column chart', 'histogram'],
            'line': ['line chart', 'line graph', 'trend', 'over time'],
            'pie': ['pie chart', 'donut', 'proportion', 'percentage breakdown'],
            'scatter': ['scatter plot', 'correlation', 'relationship'],
            'box': ['box plot', 'distribution', 'quartiles'],
            'heatmap': ['heatmap', 'correlation matrix', 'heat map']
        }
        
        for chart_type, keywords in chart_mentions.items():
            if any(keyword in query_lower for keyword in keywords):
                viz_intent['explicit_chart_type'] = chart_type
                break
        
        # Intent analysis
        viz_intent['comparison_needed'] = any(word in query_lower for word in [
            'compare', 'vs', 'versus', 'against', 'between', 'difference'
        ])
        
        viz_intent['trend_analysis'] = any(word in query_lower for word in [
            'trend', 'over time', 'timeline', 'progression', 'change'
        ])
        
        viz_intent['distribution_analysis'] = any(word in query_lower for word in [
            'distribution', 'spread', 'range', 'frequency'
        ])
        
        viz_intent['ranking_analysis'] = any(word in query_lower for word in [
            'top', 'bottom', 'highest', 'lowest', 'best', 'worst', 'rank'
        ])
        
        viz_intent['correlation_analysis'] = any(word in query_lower for word in [
            'correlation', 'relationship', 'related', 'associated'
        ])
        
        viz_intent['temporal_analysis'] = any(word in query_lower for word in [
            'time', 'date', 'month', 'year', 'day', 'seasonal'
        ])
        
        viz_intent['categorical_breakdown'] = any(word in query_lower for word in [
            'by category', 'by type', 'group by', 'breakdown'
        ])
        
        # Use intent if provided
        if intent:
            primary_intent = intent.primary_intent
            if primary_intent in self.intent_chart_mapping:
                viz_intent['intent_suggestions'] = self.intent_chart_mapping[primary_intent]
        
        return viz_intent

    def _analyze_data_structure(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Analyze DataFrame structure for optimal visualization"""
        
        analysis = {
            'row_count': len(df),
            'column_count': len(df.columns),
            'numeric_columns': [],
            'categorical_columns': [],
            'datetime_columns': [],
            'high_cardinality_cols': [],
            'low_cardinality_cols': []
        }
        
        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                analysis['numeric_columns'].append(col)
            elif pd.api.types.is_datetime64_any_dtype(df[col]):
                analysis['datetime_columns'].append(col)
            else:
                analysis['categorical_columns'].append(col)
                
                # Check cardinality
                unique_count = df[col].nunique()
                if unique_count > 20:
                    analysis['high_cardinality_cols'].append(col)
                elif unique_count <= 10:
                    analysis['low_cardinality_cols'].append(col)
        
        return analysis

    def _determine_optimal_chart(self, viz_intent: Dict, data_analysis: Dict, df: pd.DataFrame) -> Tuple[str, Dict]:
        """Determine the best chart type and configuration"""
        
        # If user explicitly requested a chart type
        if viz_intent['explicit_chart_type']:
            chart_type = viz_intent['explicit_chart_type']
            return chart_type, self._get_chart_config(chart_type, data_analysis, df, viz_intent)
        
        # Smart chart selection based on intent and data
        num_cols = data_analysis['numeric_columns']
        cat_cols = data_analysis['categorical_columns']
        date_cols = data_analysis['datetime_columns']
        low_card_cols = data_analysis['low_cardinality_cols']
        
        # Time series analysis
        if (viz_intent['trend_analysis'] or viz_intent['temporal_analysis']) and date_cols and num_cols:
            return 'line', self._get_chart_config('line', data_analysis, df, viz_intent)
        
        # Correlation analysis
        if viz_intent['correlation_analysis'] and len(num_cols) >= 2:
            return 'scatter', self._get_chart_config('scatter', data_analysis, df, viz_intent)
        
        # Ranking/Top-bottom analysis
        if viz_intent['ranking_analysis'] and num_cols and cat_cols:
            return 'bar', self._get_chart_config('bar', data_analysis, df, viz_intent)
        
        # Distribution analysis
        if viz_intent['distribution_analysis'] and num_cols:
            return 'histogram', self._get_chart_config('histogram', data_analysis, df, viz_intent)
        
        # Categorical breakdown with small categories
        if viz_intent['categorical_breakdown'] and low_card_cols and num_cols:
            if len(df[low_card_cols[0]].unique()) <= 8:
                return 'pie', self._get_chart_config('pie', data_analysis, df, viz_intent)
            else:
                return 'bar', self._get_chart_config('bar', data_analysis, df, viz_intent)
        
        # Comparison analysis
        if viz_intent['comparison_needed'] and num_cols and cat_cols:
            return 'bar', self._get_chart_config('bar', data_analysis, df, viz_intent)
        
        # Default fallback based on data structure
        if date_cols and num_cols:
            return 'line', self._get_chart_config('line', data_analysis, df, viz_intent)
        elif len(num_cols) >= 2:
            return 'scatter', self._get_chart_config('scatter', data_analysis, df, viz_intent)
        elif num_cols and cat_cols:
            return 'bar', self._get_chart_config('bar', data_analysis, df, viz_intent)
        elif num_cols:
            return 'histogram', self._get_chart_config('histogram', data_analysis, df, viz_intent)
        else:
            return 'bar', self._get_chart_config('bar', data_analysis, df, viz_intent)

    def _get_chart_config(self, chart_type: str, data_analysis: Dict, df: pd.DataFrame, viz_intent: Dict) -> Dict:
        """Get configuration for specific chart type"""
        
        num_cols = data_analysis['numeric_columns']
        cat_cols = data_analysis['categorical_columns']
        date_cols = data_analysis['datetime_columns']
        low_card_cols = data_analysis['low_cardinality_cols']
        
        config = {
            'chart_type': chart_type,
            'title': 'Data Visualization',
            'description': f'{chart_type.title()} chart visualization'
        }
        
        if chart_type == 'line':
            x_col = date_cols[0] if date_cols else (cat_cols[0] if cat_cols else df.columns[0])
            y_col = num_cols[0] if num_cols else df.columns[1]
            config.update({
                'x_column': x_col,
                'y_column': y_col,
                'title': f'{y_col} over {x_col}',
                'mode': 'lines+markers'
            })
            
        elif chart_type == 'bar':
            x_col = cat_cols[0] if cat_cols else low_card_cols[0] if low_card_cols else df.columns[0]
            y_col = num_cols[0] if num_cols else df.columns[1]
            config.update({
                'x_column': x_col,
                'y_column': y_col,
                'title': f'{y_col} by {x_col}',
                'orientation': 'v'
            })
            
        elif chart_type == 'scatter':
            x_col = num_cols[0] if len(num_cols) >= 1 else df.columns[0]
            y_col = num_cols[1] if len(num_cols) >= 2 else df.columns[1]
            config.update({
                'x_column': x_col,
                'y_column': y_col,
                'title': f'{y_col} vs {x_col}',
                'size_column': None,
                'color_column': cat_cols[0] if cat_cols else None
            })
            
        elif chart_type == 'pie':
            names_col = low_card_cols[0] if low_card_cols else cat_cols[0]
            values_col = num_cols[0] if num_cols else None
            config.update({
                'names_column': names_col,
                'values_column': values_col,
                'title': f'{names_col} Distribution'
            })
            
        elif chart_type == 'histogram':
            col = num_cols[0] if num_cols else df.columns[0]
            config.update({
                'column': col,
                'title': f'Distribution of {col}',
                'bins': min(30, max(10, len(df) // 20))
            })
            
        elif chart_type == 'box':
            y_col = num_cols[0] if num_cols else df.columns[0]
            x_col = cat_cols[0] if cat_cols else None
            config.update({
                'y_column': y_col,
                'x_column': x_col,
                'title': f'Distribution of {y_col}' + (f' by {x_col}' if x_col else '')
            })
            
        elif chart_type == 'heatmap':
            numeric_cols = [col for col in num_cols if df[col].dtype in ['int64', 'float64']][:10]
            config.update({
                'columns': numeric_cols,
                'title': 'Correlation Heatmap',
                'correlation_method': 'pearson'
            })
        
        return config

    def _generate_plotly_chart(self, chart_type: str, config: Dict, df: pd.DataFrame) -> Dict[str, Any]:
        """Generate the actual Plotly chart JSON"""
        
        try:
            if chart_type == 'line':
                return self._create_line_chart(config, df)
            elif chart_type == 'bar':
                return self._create_bar_chart(config, df)
            elif chart_type == 'scatter':
                return self._create_scatter_chart(config, df)
            elif chart_type == 'pie':
                return self._create_pie_chart(config, df)
            elif chart_type == 'histogram':
                return self._create_histogram(config, df)
            elif chart_type == 'box':
                return self._create_box_chart(config, df)
            elif chart_type == 'heatmap':
                return self._create_heatmap(config, df)
            else:
                return self._create_fallback_chart(df)
                
        except Exception as e:
            self.logger.error(f"Error creating {chart_type} chart: {e}")
            return self._create_fallback_chart(df)

    def _create_line_chart(self, config: Dict, df: pd.DataFrame) -> Dict[str, Any]:
        """Create Plotly line chart"""
        x_col = config['x_column']
        y_col = config['y_column']
        
        # Sort by x column for better line visualization
        df_sorted = df.sort_values(x_col)
        
        fig = px.line(
            df_sorted, 
            x=x_col, 
            y=y_col,
            title=config['title'],
            template=self.default_theme,
            markers=True
        )
        
        fig.update_layout(
            showlegend=True,
            hovermode='x unified'
        )
        
        return fig.to_dict()

    def _create_bar_chart(self, config: Dict, df: pd.DataFrame) -> Dict[str, Any]:
        """Create Plotly bar chart"""
        x_col = config['x_column']
        y_col = config['y_column']
        
        # Aggregate data if needed
        if df[x_col].duplicated().any():
            df_agg = df.groupby(x_col)[y_col].sum().reset_index()
        else:
            df_agg = df
        
        # Sort by y values for better visualization
        df_agg = df_agg.sort_values(y_col, ascending=False).head(20)  # Top 20
        
        fig = px.bar(
            df_agg,
            x=x_col,
            y=y_col,
            title=config['title'],
            template=self.default_theme,
            color=y_col,
            color_continuous_scale='viridis'
        )
        
        fig.update_layout(
            xaxis_tickangle=-45,
            showlegend=False
        )
        
        return fig.to_dict()

    def _create_scatter_chart(self, config: Dict, df: pd.DataFrame) -> Dict[str, Any]:
        """Create Plotly scatter chart"""
        x_col = config['x_column']
        y_col = config['y_column']
        color_col = config.get('color_column')
        
        fig = px.scatter(
            df,
            x=x_col,
            y=y_col,
            color=color_col,
            title=config['title'],
            template=self.default_theme,
            opacity=0.7
        )
        
        # Add trendline if both columns are numeric
        if df[x_col].dtype in ['int64', 'float64'] and df[y_col].dtype in ['int64', 'float64']:
            fig.add_trace(
                go.Scatter(
                    x=df[x_col],
                    y=np.poly1d(np.polyfit(df[x_col], df[y_col], 1))(df[x_col]),
                    mode='lines',
                    name='Trendline',
                    line=dict(dash='dash')
                )
            )
        
        return fig.to_dict()

    def _create_pie_chart(self, config: Dict, df: pd.DataFrame) -> Dict[str, Any]:
        """Create Plotly pie chart"""
        names_col = config['names_column']
        values_col = config.get('values_column')
        
        if values_col:
            # Aggregate values by names
            df_agg = df.groupby(names_col)[values_col].sum().reset_index()
            # Keep top 10 categories
            df_agg = df_agg.nlargest(10, values_col)
            
            fig = px.pie(
                df_agg,
                names=names_col,
                values=values_col,
                title=config['title'],
                template=self.default_theme
            )
        else:
            # Count occurrences
            value_counts = df[names_col].value_counts().head(10)
            
            fig = px.pie(
                values=value_counts.values,
                names=value_counts.index,
                title=config['title'],
                template=self.default_theme
            )
        
        fig.update_traces(textposition='inside', textinfo='percent+label')
        
        return fig.to_dict()

    def _create_histogram(self, config: Dict, df: pd.DataFrame) -> Dict[str, Any]:
        """Create Plotly histogram"""
        col = config['column']
        bins = config.get('bins', 30)
        
        fig = px.histogram(
            df,
            x=col,
            nbins=bins,
            title=config['title'],
            template=self.default_theme
        )
        
        fig.update_layout(
            bargap=0.1,
            showlegend=False
        )
        
        return fig.to_dict()

    def _create_box_chart(self, config: Dict, df: pd.DataFrame) -> Dict[str, Any]:
        """Create Plotly box plot"""
        y_col = config['y_column']
        x_col = config.get('x_column')
        
        if x_col:
            fig = px.box(
                df,
                x=x_col,
                y=y_col,
                title=config['title'],
                template=self.default_theme
            )
        else:
            fig = px.box(
                df,
                y=y_col,
                title=config['title'],
                template=self.default_theme
            )
        
        return fig.to_dict()

    def _create_heatmap(self, config: Dict, df: pd.DataFrame) -> Dict[str, Any]:
        """Create Plotly correlation heatmap"""
        columns = config.get('columns', df.select_dtypes(include=['number']).columns.tolist())
        
        if len(columns) < 2:
            return self._create_fallback_chart(df)
        
        # Calculate correlation matrix
        corr_matrix = df[columns].corr()
        
        fig = px.imshow(
            corr_matrix,
            title=config['title'],
            template=self.default_theme,
            color_continuous_scale='RdBu_r',
            aspect='auto'
        )
        
        fig.update_layout(
            width=600,
            height=600
        )
        
        return fig.to_dict()

    def _create_fallback_chart(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Create a simple fallback chart when other options fail"""
        
        # Try to create a simple bar chart with first categorical and first numeric column
        numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
        categorical_cols = df.select_dtypes(include=['object']).columns.tolist()
        
        if categorical_cols and numeric_cols:
            df_agg = df.groupby(categorical_cols[0])[numeric_cols[0]].sum().reset_index()
            df_agg = df_agg.head(10)  # Top 10
            
            fig = px.bar(
                df_agg,
                x=categorical_cols[0],
                y=numeric_cols[0],
                title="Data Overview",
                template=self.default_theme
            )
        else:
            # Last resort: simple data summary
            fig = go.Figure()
            fig.add_annotation(
                text=f"Dataset: {df.shape[0]} rows, {df.shape[1]} columns<br>Columns: {', '.join(df.columns[:5])}",
                xref="paper", yref="paper",
                x=0.5, y=0.5, xanchor='center', yanchor='middle',
                showarrow=False, font=dict(size=16)
            )
            fig.update_layout(
                title="Dataset Overview",
                template=self.default_theme,
                height=400
            )
        
        return fig.to_dict()

    def _generate_chart_insights(self, chart_type: str, config: Dict, df: pd.DataFrame) -> List[str]:
        """Generate insights about the created visualization"""
        
        insights = []
        
        try:
            if chart_type == 'line':
                insights.append(f"Time series showing {config.get('y_column', 'values')} progression")
                insights.append("Look for trends, seasonality, and anomalies in the data")
                
            elif chart_type == 'bar':
                insights.append(f"Comparing {config.get('y_column', 'values')} across {config.get('x_column', 'categories')}")
                insights.append("Identify top performers and outliers")
                
            elif chart_type == 'scatter':
                insights.append(f"Relationship between {config.get('x_column', 'X')} and {config.get('y_column', 'Y')}")
                insights.append("Look for correlations, clusters, and outliers")
                
            elif chart_type == 'pie':
                insights.append(f"Proportional breakdown of {config.get('names_column', 'categories')}")
                insights.append("Identify dominant categories and distribution patterns")
                
            elif chart_type == 'histogram':
                insights.append(f"Distribution analysis of {config.get('column', 'values')}")
                insights.append("Check for normality, skewness, and outliers")
                
            elif chart_type == 'box':
                insights.append(f"Statistical distribution of {config.get('y_column', 'values')}")
                insights.append("Analyze median, quartiles, and outliers")
                
            elif chart_type == 'heatmap':
                insights.append("Correlation patterns between variables")
                insights.append("Identify strong positive/negative relationships")
            
            # Add data-specific insights
            insights.append(f"Based on {df.shape[0]:,} data points across {df.shape[1]} dimensions")
            
        except Exception as e:
            self.logger.error(f"Error generating insights: {e}")
            insights = [f"{chart_type.title()} visualization of the data", "Analyze patterns and trends in the chart"]
        
        return insights

    def _create_empty_chart_message(self) -> PlotlyVisualization:
        """Create visualization for empty data"""
        fig = go.Figure()
        fig.add_annotation(
            text="No data available for visualization",
            xref="paper", yref="paper",
            x=0.5, y=0.5, xanchor='center', yanchor='middle',
            showarrow=False, font=dict(size=18)
        )
        fig.update_layout(
            title="No Data",
            template=self.default_theme,
            height=300
        )
        
        return PlotlyVisualization(
            chart_type=ChartType.BAR,
            plotly_json=fig.to_dict(),
            title="No Data Available",
            description="No data to visualize",
            insights=["Please ensure data is available before creating visualizations"],
            metadata={}
        )

    def _create_error_chart(self, error_message: str) -> PlotlyVisualization:
        """Create visualization for errors"""
        fig = go.Figure()
        fig.add_annotation(
            text=f"Visualization Error:<br>{error_message}",
            xref="paper", yref="paper",
            x=0.5, y=0.5, xanchor='center', yanchor='middle',
            showarrow=False, font=dict(size=14, color="red")
        )
        fig.update_layout(
            title="Visualization Error",
            template=self.default_theme,
            height=300
        )
        
        return PlotlyVisualization(
            chart_type=ChartType.BAR,
            plotly_json=fig.to_dict(),
            title="Error",
            description=f"Error creating visualization: {error_message}",
            insights=["Please check your data and query for issues"],
            metadata={'error': error_message}
        )
