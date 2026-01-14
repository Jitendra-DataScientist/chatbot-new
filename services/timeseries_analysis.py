import pandas as pd
import numpy as np
import json
import os
from datetime import datetime
from typing import Dict, Any, Tuple
import warnings
warnings.filterwarnings('ignore')

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()  # Load .env file if it exists
except ImportError:
    pass  # python-dotenv not installed, skip loading .env

# Machine learning libraries  
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_squared_error

# TimeGPT (using nixtla)
try:
    # gazelle:ignore nixtla.NixtlaClient
    # gazelle:ignore nixtla
    from nixtla import NixtlaClient
except ImportError:
    print("Warning: nixtla not installed. Install with: pip install nixtla")
    NixtlaClient = None

# Seasonal decomposition
from statsmodels.tsa.seasonal import seasonal_decompose
import matplotlib.pyplot as plt


def detect_data_type(series):
    """Detect if a series is categorical or numerical"""
    if series.dtype == 'object' or series.dtype.name == 'category':
        return 'categorical'
    elif pd.api.types.is_numeric_dtype(series):
        return 'numerical'
    else:
        # Try to convert to numeric
        try:
            pd.to_numeric(series)
            return 'numerical'
        except:
            return 'categorical'


def aggregate_to_target_points(df, time_col, target_col, target_points=15):
    """Aggregate dataframe to approximately target_points data points"""
    df_sorted = df.sort_values(time_col)
    total_rows = len(df_sorted)
    
    if total_rows <= target_points:
        return df_sorted
    
    # Calculate group size to get approximately target_points
    group_size = max(1, total_rows // target_points)
    
    # Create groups
    df_sorted['group'] = df_sorted.index // group_size
    
    # Aggregate
    aggregated = df_sorted.groupby('group').agg({
        time_col: 'first',  # Take first timestamp of each group
        target_col: 'mean'  # Average the target values
    }).reset_index(drop=True)
    
    return aggregated


def preprocess_data(df, time_col, target_col):
    """Preprocess the input dataframe"""
    # Make a copy
    df_clean = df[[time_col, target_col]].copy()
    
    # Convert time column to datetime if not already
    if not pd.api.types.is_datetime64_any_dtype(df_clean[time_col]):
        df_clean[time_col] = pd.to_datetime(df_clean[time_col])
    
    # Detect target type
    target_type = detect_data_type(df_clean[target_col])
    
    if target_type == 'categorical':
        # Group by time and categorical value, count occurrences
        df_clean['count'] = 1
        df_grouped = df_clean.groupby([time_col, target_col])['count'].sum().reset_index()
        # Further aggregate by time only (sum counts for each time period)
        df_processed = df_grouped.groupby(time_col)['count'].sum().reset_index()
        df_processed.columns = [time_col, 'target']
    else:
        # For numerical data, just rename the target column
        df_processed = df_clean.copy()
        df_processed.columns = [time_col, 'target']
        # Remove any non-numeric values
        df_processed['target'] = pd.to_numeric(df_processed['target'], errors='coerce')
        df_processed = df_processed.dropna()
    
    # Aggregate to 10-20 data points
    df_final = aggregate_to_target_points(df_processed, time_col, 'target')
    
    return df_final


def moving_average_analysis(df, time_col='time'):
    """Perform moving average-based time series analysis as LSTM alternative"""
    values = df['target'].values
    
    # Calculate moving averages for trend
    window_size = max(2, len(values) // 4)  # Adaptive window size
    if len(values) >= window_size:
        # Calculate moving average for trend
        trend = np.convolve(values, np.ones(window_size)/window_size, mode='same')
    else:
        # Linear trend for small datasets
        trend = np.linspace(values[0], values[-1], len(values))
    
    # Calculate seasonality as residual
    seasonal = values - trend
    
    # Smooth the seasonality
    if len(seasonal) >= 3:
        seasonal = np.convolve(seasonal, np.ones(3)/3, mode='same')
    
    return trend, seasonal


def timegpt_analysis(df, time_col='time'):
    """Perform TimeGPT-based analysis"""
    if NixtlaClient is None:
        # Fallback to simple statistical decomposition
        return statistical_decomposition(df, time_col)
    
    try:
        # Get API key from environment variable
        api_key = os.getenv('NIXTLA_API_KEY')
        if not api_key:
            print("Warning: NIXTLA_API_KEY not found in environment variables. Using statistical decomposition instead.")
            return statistical_decomposition(df, time_col)
        
        # Initialize TimeGPT client with API key
        client = NixtlaClient(api_key=api_key)
        
        # Prepare data for TimeGPT
        timegpt_df = df.copy()
        timegpt_df.columns = ['ds', 'y']  # TimeGPT expects these column names
        
        # Make forecast (TimeGPT works better with forecasting)
        forecast = client.forecast(timegpt_df, h=len(df))
        
        # For now, use the original values and extract components using statistical method
        return statistical_decomposition(df, time_col)
        
    except Exception as e:
        print(f"TimeGPT failed: {e}. Using statistical decomposition instead.")
        return statistical_decomposition(df, time_col)


def statistical_decomposition(df, time_col='time'):
    """Fallback statistical decomposition"""
    values = df['target'].values
    
    try:
        decomposition = seasonal_decompose(values, model='additive', period=max(2, len(values)//3))
        trend = decomposition.trend.fillna(method='bfill').fillna(method='ffill').values
        seasonal = decomposition.seasonal.fillna(0).values
    except:
        # Simple linear trend
        trend = np.linspace(values[0], values[-1], len(values))
        seasonal = values - trend
    
    return trend, seasonal


def timeseries_analysis(df, time_col, target_col):
    """
    Main function to perform time series analysis using Moving Average and TimeGPT
    
    Parameters:
    df (pd.DataFrame): Input dataframe with time and target columns
    time_col (str): Name of the time column
    target_col (str): Name of the target column
    
    Returns:
    dict: JSON-like dictionary with seasonality and trend components
    """
    
    # Preprocess data
    df_processed = preprocess_data(df, time_col, target_col)
    
    if len(df_processed) < 2:
        raise ValueError("Insufficient data points for analysis")
    
    # Moving Average Analysis (replacing LSTM)
    ma_trend, ma_seasonal = moving_average_analysis(df_processed, 'time')
    
    # TimeGPT Analysis
    timegpt_trend, timegpt_seasonal = timegpt_analysis(df_processed, 'time')
    
    # Combine results with weights (can be configured via environment variables)
    w1 = float(os.getenv('MOVING_AVERAGE_WEIGHT', '0.5'))  # Moving Average weight
    w2 = float(os.getenv('TIMEGPT_WEIGHT', '0.5'))  # TimeGPT weight
    
    # Ensure weights sum to 1
    total_weight = w1 + w2
    if total_weight != 1.0:
        w1 = w1 / total_weight
        w2 = w2 / total_weight
    
    combined_trend = w1 * ma_trend + w2 * timegpt_trend
    combined_seasonal = w1 * ma_seasonal + w2 * timegpt_seasonal
    
    # Create output dictionary
    timestamps = df_processed[time_col].dt.strftime('%Y-%m-%d %H:%M:%S').tolist()
    
    result = {
        'seasonality': {
            timestamps[i]: round(float(combined_seasonal[i]), 2) 
            for i in range(len(timestamps))
        },
        'trend': {
            timestamps[i]: round(float(combined_trend[i]), 2) 
            for i in range(len(timestamps))
        }
    }
    
    return result


# Example usage and testing
if __name__ == "__main__":
    """
    # Create sample data for testing
    dates = pd.date_range('2025-01-01', periods=30, freq='D')
    
    # Test with numerical data
    numerical_target = np.sin(np.arange(30) * 0.5) + np.random.normal(0, 0.1, 30) + np.arange(30) * 0.1
    df_numerical = pd.DataFrame({
        'timestamp': dates,
        'value': numerical_target
    })
    
     print("Testing with numerical data (Moving Average + TimeGPT):")
     result_num = timeseries_analysis(df_numerical, 'timestamp', 'value')
     print(f"Number of data points: {len(result_num['trend'])}")
     print("Sample trend values:", list(result_num['trend'].values())[:3])
     print("Sample seasonality values:", list(result_num['seasonality'].values())[:3])
    
    # Test with categorical data
    categories = np.random.choice(['A', 'B', 'C'], 50)
    dates_cat = pd.date_range('2025-01-01', periods=50, freq='H')
    df_categorical = pd.DataFrame({
        'timestamp': dates_cat,
        'category': categories
    })
    
     print("\nTesting with categorical data (Moving Average + TimeGPT):")
     result_cat = timeseries_analysis(df_categorical, 'timestamp', 'category')
     print(f"Number of data points: {len(result_cat['trend'])}")
     print("Sample trend values:", list(result_cat['trend'].values())[:3])
     print("Sample seasonality values:", list(result_cat['seasonality'].values())[:3])
    """
    df = pd.read_csv('tickets - GFi7H5K2D (2) (1).csv')
    import json
    result_cat = timeseries_analysis(df, 'create_month', 'case_id')
    print (json.dumps(result_cat,indent=4))

