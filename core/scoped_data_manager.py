"""
Scoped Data Manager - Session-Isolated DataFrame Storage

Replaces global singleton DataManager with proper session scoping.
Each session gets its own isolated DataFrame namespace, preventing
data leakage between users and dashboards.

Key Features:
- Complete DataFrame isolation per session
- Thread-safe operations with RLock
- Automatic memory tracking and reporting
- Disk persistence for crash recovery
- Efficient cleanup on session expiration

Architecture Decisions:
1. Two-tier storage: {session_key: {data_name: DataFrame}}
2. Separate metadata for lightweight operations
3. Parquet format for fast disk persistence
4. RequestContext-based scoping (immutable, type-safe)

Author: Enterprise Architecture Refactor
Date: December 2024
"""

import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List, Tuple
import pandas as pd

from core.request_context import RequestContext
from master_logger import setup_module_logger

logger = setup_module_logger('core.scoped_data_manager')


class ScopedDataManager:
    """
    Thread-safe DataFrame manager with session-level isolation.
    
    Unlike the original singleton DataManager that shared data globally,
    this manager scopes all data to specific sessions using RequestContext.
    
    Structure:
        _data_store: {
            session_key: {
                'main_data': DataFrame,
                'chart_1': DataFrame,
                'chart_2': DataFrame,
                ...
            }
        }
    
    Benefits:
    - Complete isolation: User A cannot access User B's data
    - Dashboard isolation: Same user on different dashboards has separate data
    - Session isolation: Multiple browser tabs get separate data stores
    - Memory safety: Automatic cleanup on session expiration
    
    Performance:
    - O(1) data access via session_key + data_name
    - Lazy loading: DataFrames only loaded when needed
    - Disk persistence: Survive app restarts
    """
    
    def __init__(self, disk_cache_dir: str = "data_cache"):
        """
        Initialize scoped data manager.
        
        Args:
            disk_cache_dir: Directory for persistent DataFrame storage
        """
        # Data storage: {session_key: {data_name: DataFrame}}
        self._data_store: Dict[str, Dict[str, pd.DataFrame]] = {}
        
        # Metadata: {session_key: {data_name: {...}}}
        self._metadata_store: Dict[str, Dict[str, dict]] = {}
        
        # Thread safety
        self._lock = threading.RLock()
        
        # Disk cache
        self._disk_cache_dir = Path(disk_cache_dir)
        self._disk_cache_dir.mkdir(exist_ok=True)
        
        # Statistics
        self._total_dataframes_registered = 0
        self._total_cache_hits = 0
        self._total_cache_misses = 0
        
        logger.info("=" * 80)
        logger.info("ScopedDataManager Initialized")
        logger.info(f"- Session-scoped DataFrame isolation")
        logger.info(f"- Disk cache: {self._disk_cache_dir.absolute()}")
        logger.info(f"- Thread-safe with RLock")
        logger.info("=" * 80)
    
    def register_data(
        self,
        context: RequestContext,
        df: pd.DataFrame,
        data_name: str = "main_data"
    ) -> str:
        """
        Register DataFrame scoped to session.
        
        Args:
            context: RequestContext identifying the session
            df: DataFrame to register
            data_name: Name for this DataFrame within the session
            
        Returns:
            Scoped data ID for retrieval (session_key:data_name)
        """
        if df is None or df.empty:
            raise ValueError("Cannot register None or empty DataFrame")
        
        session_key = context.get_session_key()
        scoped_data_id = f"{session_key}:{data_name}"
        
        with self._lock:
            # Ensure session namespace exists
            if session_key not in self._data_store:
                self._data_store[session_key] = {}
                self._metadata_store[session_key] = {}
                logger.info(f"Created data namespace for session: {context.session_id[:8]}...")
            
            # Check if data already exists
            if data_name in self._data_store[session_key]:
                logger.warning(
                    f"Overwriting existing data '{data_name}' for session {context.session_id[:8]}..."
                )
            
            # Store DataFrame
            self._data_store[session_key][data_name] = df
            
            # Generate metadata
            memory_mb = df.memory_usage(deep=True).sum() / 1024**2
            metadata = {
                'registered_at': datetime.utcnow().isoformat(),
                'shape': df.shape,
                'memory_mb': memory_mb,
                'columns': list(df.columns),
                'dtypes': {col: str(dtype) for col, dtype in df.dtypes.items()},
                'user': context.user.username,
                'dashboard': context.dashboard_name
            }
            self._metadata_store[session_key][data_name] = metadata
            
            self._total_dataframes_registered += 1
            
            logger.info(
                f"Registered DataFrame '{data_name}' - "
                f"Session: {context.session_id[:8]}..., "
                f"Shape: {df.shape}, "
                f"Memory: {memory_mb:.2f}MB, "
                f"User: {context.user.username}"
            )
            logger.debug(f"  Registration session_key: {session_key}")
            
            # Persist to disk asynchronously (fire and forget)
            self._persist_to_disk_async(scoped_data_id, df)
        
        return scoped_data_id
    
    def get_data(
        self,
        context: RequestContext,
        data_name: str = "main_data"
    ) -> Optional[pd.DataFrame]:
        """
        Retrieve DataFrame for session.
        
        Args:
            context: RequestContext identifying the session
            data_name: Name of DataFrame to retrieve
            
        Returns:
            DataFrame if exists, None otherwise
        """
        session_key = context.get_session_key()
        
        with self._lock:
            # Check memory cache first
            if session_key in self._data_store:
                if data_name in self._data_store[session_key]:
                    self._total_cache_hits += 1
                    logger.debug(
                        f"Cache HIT - Retrieved '{data_name}' for session {context.session_id[:8]}..."
                    )
                    return self._data_store[session_key][data_name]
            
            # Try disk cache
            self._total_cache_misses += 1
            scoped_data_id = f"{session_key}:{data_name}"
            
            if self._load_from_disk(scoped_data_id, session_key, data_name):
                logger.info(
                    f"Loaded '{data_name}' from disk cache for session {context.session_id[:8]}..."
                )
                return self._data_store[session_key][data_name]
            
            logger.debug(
                f"Cache MISS - No data '{data_name}' for session {context.session_id[:8]}..."
            )
            logger.debug(f"  Lookup session_key: {session_key}")
            logger.debug(f"  Available session_keys: {list(self._data_store.keys())}")
            return None
    
    def has_data(self, context: RequestContext, data_name: str = "main_data") -> bool:
        """
        Check if DataFrame exists for session.
        
        Args:
            context: RequestContext identifying the session
            data_name: Name of DataFrame to check
            
        Returns:
            True if DataFrame exists
        """
        session_key = context.get_session_key()
        
        with self._lock:
            if session_key in self._data_store:
                return data_name in self._data_store[session_key]
            return False
    
    def list_data(self, context: RequestContext) -> List[str]:
        """
        List all DataFrame names for a session.
        
        Args:
            context: RequestContext identifying the session
            
        Returns:
            List of data names available for this session
        """
        session_key = context.get_session_key()
        
        with self._lock:
            if session_key in self._data_store:
                return list(self._data_store[session_key].keys())
            return []
    
    def get_metadata(
        self,
        context: RequestContext,
        data_name: str = "main_data"
    ) -> Optional[dict]:
        """
        Get metadata for DataFrame without loading the full data.
        
        Args:
            context: RequestContext identifying the session
            data_name: Name of DataFrame
            
        Returns:
            Metadata dictionary if exists, None otherwise
        """
        session_key = context.get_session_key()
        
        with self._lock:
            if session_key in self._metadata_store:
                return self._metadata_store[session_key].get(data_name)
            return None
    
    def cleanup_session(self, context: RequestContext) -> int:
        """
        Remove all data for a session.
        
        Call this when session expires to free memory.
        
        Args:
            context: RequestContext identifying the session
            
        Returns:
            Number of DataFrames removed
        """
        session_key = context.get_session_key()
        count = 0
        
        with self._lock:
            # Remove from memory
            if session_key in self._data_store:
                count = len(self._data_store[session_key])
                del self._data_store[session_key]
            
            # Remove metadata
            if session_key in self._metadata_store:
                del self._metadata_store[session_key]
            
            # Remove disk cache
            self._cleanup_disk_cache(session_key)
        
        if count > 0:
            logger.info(
                f"Cleaned up {count} DataFrame(s) for session {context.session_id[:8]}... "
                f"(User: {context.user.username})"
            )
        
        return count
    
    def get_memory_usage(self) -> Dict:
        """
        Get comprehensive memory usage statistics.
        
        Returns:
            Dictionary with memory stats per session and totals
        """
        with self._lock:
            session_stats = []
            total_memory_mb = 0.0
            total_dataframes = 0
            
            for session_key, data_dict in self._data_store.items():
                session_memory = 0.0
                df_count = len(data_dict)
                
                for data_name, df in data_dict.items():
                    memory_mb = df.memory_usage(deep=True).sum() / 1024**2
                    session_memory += memory_mb
                
                total_memory_mb += session_memory
                total_dataframes += df_count
                
                session_stats.append({
                    'session_key': session_key,
                    'dataframe_count': df_count,
                    'memory_mb': round(session_memory, 2)
                })
            
            # Sort by memory usage
            session_stats.sort(key=lambda x: x['memory_mb'], reverse=True)
            
            return {
                'total_sessions': len(self._data_store),
                'total_dataframes': total_dataframes,
                'total_memory_mb': round(total_memory_mb, 2),
                'total_registered': self._total_dataframes_registered,
                'cache_hits': self._total_cache_hits,
                'cache_misses': self._total_cache_misses,
                'cache_hit_rate': (
                    self._total_cache_hits / (self._total_cache_hits + self._total_cache_misses)
                    if (self._total_cache_hits + self._total_cache_misses) > 0
                    else 0.0
                ),
                'top_sessions': session_stats[:10]  # Top 10 by memory
            }
    
    def _persist_to_disk_async(self, scoped_data_id: str, df: pd.DataFrame):
        """
        Persist DataFrame to disk in background thread.
        
        Uses Parquet format for fast, compressed storage.
        
        Args:
            scoped_data_id: Unique identifier for this data
            df: DataFrame to persist
        """
        def persist():
            try:
                # Sanitize filename (replace : with _)
                safe_filename = scoped_data_id.replace(':', '_')
                filepath = self._disk_cache_dir / f"{safe_filename}.parquet"
                
                df.to_parquet(filepath, compression='snappy', index=False)
                logger.debug(f"Persisted to disk: {filepath.name}")
            except Exception as e:
                logger.error(f"Failed to persist {scoped_data_id}: {e}")
        
        # Run in background thread (fire and forget)
        thread = threading.Thread(target=persist, daemon=True)
        thread.start()
    
    def _load_from_disk(
        self,
        scoped_data_id: str,
        session_key: str,
        data_name: str
    ) -> bool:
        """
        Load DataFrame from disk cache.
        
        Args:
            scoped_data_id: Unique identifier
            session_key: Session key for storage
            data_name: Data name within session
            
        Returns:
            True if loaded successfully, False otherwise
        """
        try:
            safe_filename = scoped_data_id.replace(':', '_')
            filepath = self._disk_cache_dir / f"{safe_filename}.parquet"
            
            if not filepath.exists():
                return False
            
            # Load DataFrame
            df = pd.read_parquet(filepath)
            
            # Store in memory
            if session_key not in self._data_store:
                self._data_store[session_key] = {}
            
            self._data_store[session_key][data_name] = df
            
            # Generate metadata
            memory_mb = df.memory_usage(deep=True).sum() / 1024**2
            if session_key not in self._metadata_store:
                self._metadata_store[session_key] = {}
            
            self._metadata_store[session_key][data_name] = {
                'loaded_from_disk': True,
                'loaded_at': datetime.utcnow().isoformat(),
                'shape': df.shape,
                'memory_mb': memory_mb,
                'columns': list(df.columns)
            }
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to load from disk {scoped_data_id}: {e}")
            return False
    
    def _cleanup_disk_cache(self, session_key: str):
        """
        Remove disk cache files for a session.
        
        Args:
            session_key: Session key to cleanup
        """
        try:
            # Find all files matching session_key pattern
            safe_key = session_key.replace(':', '_')
            pattern = f"{safe_key}_*.parquet"
            
            for filepath in self._disk_cache_dir.glob(pattern):
                try:
                    filepath.unlink()
                    logger.debug(f"Deleted disk cache: {filepath.name}")
                except Exception as e:
                    logger.warning(f"Failed to delete {filepath}: {e}")
                    
        except Exception as e:
            logger.error(f"Failed to cleanup disk cache for {session_key}: {e}")
    
    def __repr__(self) -> str:
        """Developer-friendly representation"""
        stats = self.get_memory_usage()
        return (
            f"ScopedDataManager("
            f"sessions={stats['total_sessions']}, "
            f"dataframes={stats['total_dataframes']}, "
            f"memory={stats['total_memory_mb']:.1f}MB)"
        )


# Global instance for easy access (but with proper scoping)
_scoped_data_manager: Optional[ScopedDataManager] = None


def get_scoped_data_manager() -> ScopedDataManager:
    """
    Get or create the global ScopedDataManager instance.
    
    This is a singleton, but unlike the old DataManager, it properly
    scopes all data by session using RequestContext.
    
    Returns:
        ScopedDataManager instance
    """
    global _scoped_data_manager
    
    if _scoped_data_manager is None:
        _scoped_data_manager = ScopedDataManager()
    
    return _scoped_data_manager

