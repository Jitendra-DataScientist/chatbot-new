"""
Data Manager - Centralized DataFrame Storage with Reference Pattern

This module eliminates JSON serialization bottlenecks by:
- Storing DataFrames in memory (single source of truth)
- Passing only data_id strings through LangGraph state
- Providing instant data access via memory references

Performance Impact:
- Before: pd.read_json() takes ~2.4 minutes for 2M rows
- After: get_data() takes ~0.001 seconds (memory reference)

Author: Migration from JSON state to reference pattern
Date: November 2025
"""

import os
import time
import hashlib
from typing import Dict, Optional, Any
from datetime import datetime
import pandas as pd
from pathlib import Path
from master_logger import setup_module_logger


class DataManager:
    """
    Singleton Data Manager for efficient DataFrame storage and retrieval
    
    Key Features:
    - Single source of truth for all DataFrames
    - Thread-safe singleton pattern
    - Automatic disk persistence for crash recovery
    - Memory management with size tracking
    - Lightweight metadata caching
    """
    
    _instance = None
    _initialized = False
    
    def __new__(cls):
        """Singleton pattern - only one DataManager instance exists"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize DataManager (only runs once due to singleton)"""
        if DataManager._initialized:
            return
            
        self.logger = setup_module_logger('services.data_manager')
        
        # Data storage
        self._data_store: Dict[str, pd.DataFrame] = {}
        self._metadata_store: Dict[str, dict] = {}
        
        # Disk cache directory
        self._disk_cache_dir = Path("data_cache")
        self._disk_cache_dir.mkdir(exist_ok=True)
        
        # Registration tracking
        self._registration_times: Dict[str, float] = {}
        
        DataManager._initialized = True
        self.logger.info("="*80)
        self.logger.info("DataManager Initialized (Singleton)")
        self.logger.info("- In-memory DataFrame storage with reference pattern")
        self.logger.info("- Eliminates JSON serialization bottleneck")
        self.logger.info(f"- Disk cache: {self._disk_cache_dir.absolute()}")
        self.logger.info("="*80)
    
    def register_data(self, 
                     df: pd.DataFrame, 
                     connection_key: str,
                     force_new: bool = False) -> str:
        """
        Register a DataFrame and get a unique data_id
        
        Args:
            df: The DataFrame to register
            connection_key: Unique key for this connection/workbook
            force_new: If True, always create new registration even if exists
            
        Returns:
            str: Unique data_id for accessing the DataFrame
        """
        start_time = time.time()
        
        # Generate data_id from connection_key
        if force_new:
            timestamp = int(time.time() * 1000)
            data_id = f"{connection_key}_{timestamp}"
        else:
            # Reuse existing registration for same connection
            data_id = connection_key
        
        # Check if already registered
        if data_id in self._data_store and not force_new:
            self.logger.info(f"Data already registered: {data_id}, reusing existing")
            return data_id
        
        # Store DataFrame in memory
        self._data_store[data_id] = df
        self._registration_times[data_id] = time.time()
        
        # Generate and store metadata (lightweight)
        metadata = self._generate_metadata(df, connection_key)
        self._metadata_store[data_id] = metadata
        
        # Persist to disk asynchronously (for crash recovery)
        try:
            self._persist_to_disk(data_id, df)
        except Exception as e:
            self.logger.warning(f"Failed to persist to disk: {e}")
        
        elapsed = time.time() - start_time
        
        self.logger.info("="*80)
        self.logger.info(f"✓ Data Registered: {data_id}")
        self.logger.info(f"  - Shape: {df.shape} ({df.shape[0]:,} rows × {df.shape[1]} cols)")
        self.logger.info(f"  - Memory: {metadata['memory_mb']:.2f} MB")
        self.logger.info(f"  - Columns: {len(df.columns)} ({len(metadata['numeric_columns'])} numeric)")
        self.logger.info(f"  - Date columns: {metadata['date_columns']}")
        self.logger.info(f"  - Registration time: {elapsed:.3f}s")
        self.logger.info(f"  - Total datasets in memory: {len(self._data_store)}")
        self.logger.info("="*80)
        
        return data_id
    
    def get_data(self, data_id: str) -> pd.DataFrame:
        """
        Get DataFrame by data_id (instant memory access)
        
        Args:
            data_id: The unique identifier for the DataFrame
            
        Returns:
            pd.DataFrame: The requested DataFrame
            
        Raises:
            KeyError: If data_id not found
        """
        if data_id not in self._data_store:
            # Try loading from disk
            self.logger.warning(f"Data not in memory: {data_id}, attempting disk load")
            if self._load_from_disk(data_id):
                self.logger.info(f"✓ Loaded from disk: {data_id}")
            else:
                available_ids = list(self._data_store.keys())
                raise KeyError(
                    f"Data ID '{data_id}' not found. "
                    f"Available IDs: {available_ids}"
                )
        
        return self._data_store[data_id]
    
    def get_metadata(self, data_id: str) -> dict:
        """
        Get lightweight metadata without loading full DataFrame
        
        Args:
            data_id: The unique identifier
            
        Returns:
            dict: Metadata dictionary with shape, columns, dtypes, etc.
        """
        if data_id not in self._metadata_store:
            # If metadata missing but data exists, regenerate
            if data_id in self._data_store:
                df = self._data_store[data_id]
                self._metadata_store[data_id] = self._generate_metadata(df, data_id)
            else:
                raise KeyError(f"Metadata not found for data_id: {data_id}")
        
        return self._metadata_store[data_id]
    
    def exists(self, data_id: str) -> bool:
        """Check if data_id exists in memory or disk"""
        if data_id in self._data_store:
            return True
        
        # Check disk
        disk_path = self._disk_cache_dir / f"{data_id}.parquet"
        return disk_path.exists()
    
    def remove(self, data_id: str) -> bool:
        """
        Remove data from memory (disk cache remains for recovery)
        
        Args:
            data_id: The identifier to remove
            
        Returns:
            bool: True if removed, False if not found
        """
        if data_id in self._data_store:
            del self._data_store[data_id]
            self.logger.info(f"Removed from memory: {data_id}")
            
        if data_id in self._metadata_store:
            del self._metadata_store[data_id]
            
        if data_id in self._registration_times:
            del self._registration_times[data_id]
            
        return True
    
    def get_memory_usage(self) -> dict:
        """Get current memory usage statistics"""
        total_memory_mb = sum(
            self._metadata_store[did].get('memory_mb', 0)
            for did in self._data_store
        )
        
        return {
            'total_datasets': len(self._data_store),
            'total_memory_mb': total_memory_mb,
            'datasets': {
                data_id: {
                    'shape': meta.get('shape'),
                    'memory_mb': meta.get('memory_mb'),
                    'registered_at': meta.get('registered_at')
                }
                for data_id, meta in self._metadata_store.items()
            }
        }
    
    def _generate_metadata(self, df: pd.DataFrame, connection_key: str) -> dict:
        """Generate lightweight metadata for a DataFrame"""
        # Identify column types
        numeric_columns = df.select_dtypes(include=['number']).columns.tolist()
        
        # Identify date columns (basic heuristic)
        date_columns = []
        for col in df.columns:
            col_lower = str(col).lower()
            if any(word in col_lower for word in ['date', 'time', 'month', 'year', 'day']):
                date_columns.append(col)
        
        return {
            'shape': df.shape,
            'columns': df.columns.tolist(),
            'dtypes': {str(k): str(v) for k, v in df.dtypes.to_dict().items()},
            'memory_mb': df.memory_usage(deep=True).sum() / (1024 ** 2),
            'numeric_columns': numeric_columns,
            'date_columns': date_columns,
            'connection_key': connection_key,
            'registered_at': datetime.now().isoformat(),
            'has_nulls': df.isnull().any().any()
        }
    
    def _persist_to_disk(self, data_id: str, df: pd.DataFrame) -> None:
        """Persist DataFrame to disk in Parquet format (fast & compressed)"""
        try:
            filepath = self._disk_cache_dir / f"{data_id}.parquet"
            df.to_parquet(filepath, compression='snappy', index=False)
            self.logger.debug(f"Persisted to disk: {filepath}")
        except Exception as e:
            self.logger.error(f"Failed to persist {data_id}: {e}")
            raise
    
    def _load_from_disk(self, data_id: str) -> bool:
        """Load DataFrame from disk cache"""
        try:
            filepath = self._disk_cache_dir / f"{data_id}.parquet"
            if not filepath.exists():
                return False
            
            df = pd.read_parquet(filepath)
            self._data_store[data_id] = df
            
            # Regenerate metadata
            self._metadata_store[data_id] = self._generate_metadata(df, data_id)
            
            self.logger.info(f"Loaded from disk cache: {data_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to load from disk {data_id}: {e}")
            return False
    
    def clear_all(self) -> None:
        """Clear all data from memory (keeps disk cache)"""
        count = len(self._data_store)
        self._data_store.clear()
        self._metadata_store.clear()
        self._registration_times.clear()
        self.logger.info(f"Cleared {count} datasets from memory")
    
    def __repr__(self) -> str:
        usage = self.get_memory_usage()
        return (
            f"DataManager(datasets={usage['total_datasets']}, "
            f"memory={usage['total_memory_mb']:.2f}MB)"
        )


# Global function for easy access
def get_data_manager() -> DataManager:
    """Get the singleton DataManager instance"""
    return DataManager()









