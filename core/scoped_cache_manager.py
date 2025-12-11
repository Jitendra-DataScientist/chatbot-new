"""
Scoped Cache Manager - Unified Multi-Level Cache System

Replaces scattered global caches with a unified, session-scoped cache system.
Supports multiple cache types (disambiguation, conversation history, query results, etc.)
with automatic session isolation and expiration.

Key Features:
- Multiple cache types in single manager
- Session-scoped isolation (no cross-contamination)
- Thread-safe with fine-grained locking
- TTL (time-to-live) support per cache type
- Memory-efficient with automatic cleanup
- Comprehensive statistics and monitoring

Cache Types Supported:
- disambiguation: User's column/value disambiguation choices
- conversation_history: Last N queries and entities
- query_cache: Cached query results
- agent_state: Agent-specific state/memory
- custom: Any application-specific cache

Author: Enterprise Architecture Refactor
Date: December 2024
"""

import threading
from datetime import datetime, timedelta
from typing import Dict, Optional, Any, List, Set
from dataclasses import dataclass, field

from core.request_context import RequestContext
from master_logger import setup_module_logger

logger = setup_module_logger('core.scoped_cache_manager')


@dataclass
class CacheEntry:
    """
    Individual cache entry with metadata.
    
    Tracks value, creation time, access count for cache analytics.
    """
    value: Any
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_accessed: datetime = field(default_factory=datetime.utcnow)
    access_count: int = 0
    ttl_minutes: Optional[int] = None
    
    def is_expired(self) -> bool:
        """Check if entry has exceeded TTL"""
        if self.ttl_minutes is None:
            return False
        
        age_minutes = (datetime.utcnow() - self.created_at).total_seconds() / 60
        return age_minutes > self.ttl_minutes
    
    def access(self):
        """Record access (for statistics)"""
        self.last_accessed = datetime.utcnow()
        self.access_count += 1


class ScopedCacheManager:
    """
    Unified cache manager with session-level isolation.
    
    Architecture:
        _caches: {
            session_key: {
                cache_type: {
                    key: CacheEntry
                }
            }
        }
    
    This three-tier structure provides:
    - Session isolation: Different sessions never share cache
    - Type separation: Different cache purposes organized clearly
    - Fast lookups: O(1) access via session_key + cache_type + key
    
    Thread Safety:
    - RLock for reentrant operations
    - Fine-grained locking per operation
    - No deadlocks from nested calls
    
    Memory Management:
    - Automatic TTL-based expiration
    - Session cleanup on expiration
    - Size limits per cache type (optional)
    """
    
    # Default TTL per cache type (minutes)
    DEFAULT_TTLS = {
        'disambiguation': None,  # No TTL, persists for session
        'conversation_history': None,  # Persists for session
        'query_cache': 30,  # 30 minutes
        'agent_state': None,  # Persists for session
        'temporal_context': 60,  # 1 hour
        'custom': 60  # 1 hour default for custom caches
    }
    
    # Maximum entries per cache type (to prevent memory bloat)
    MAX_ENTRIES_PER_TYPE = {
        'disambiguation': 100,
        'conversation_history': 50,
        'query_cache': 20,
        'agent_state': 10,
        'temporal_context': 30,
        'custom': 50
    }
    
    def __init__(self):
        """Initialize empty cache structure"""
        # Main cache storage: {session_key: {cache_type: {key: CacheEntry}}}
        self._caches: Dict[str, Dict[str, Dict[str, CacheEntry]]] = {}
        
        # Thread safety
        self._lock = threading.RLock()
        
        # Statistics
        self._total_sets = 0
        self._total_gets = 0
        self._total_hits = 0
        self._total_misses = 0
        self._total_expirations = 0
        
        logger.info("=" * 80)
        logger.info("ScopedCacheManager Initialized")
        logger.info(f"- Session-scoped cache isolation")
        logger.info(f"- Supported cache types: {list(self.DEFAULT_TTLS.keys())}")
        logger.info(f"- Thread-safe with RLock")
        logger.info("=" * 80)
    
    def set(
        self,
        context: RequestContext,
        cache_type: str,
        key: str,
        value: Any,
        ttl_minutes: Optional[int] = None
    ):
        """
        Set value in session-scoped cache.
        
        Args:
            context: RequestContext identifying the session
            cache_type: Type of cache ('disambiguation', 'conversation_history', etc.)
            key: Cache key within the cache type
            value: Value to cache
            ttl_minutes: Optional custom TTL (overrides default)
        """
        session_key = context.get_session_key()
        
        # Use default TTL if not specified
        if ttl_minutes is None:
            ttl_minutes = self.DEFAULT_TTLS.get(cache_type, 60)
        
        with self._lock:
            # Ensure session tier exists
            if session_key not in self._caches:
                self._caches[session_key] = {}
            
            # Ensure cache type tier exists
            if cache_type not in self._caches[session_key]:
                self._caches[session_key][cache_type] = {}
            
            cache_dict = self._caches[session_key][cache_type]
            
            # Check size limit
            max_entries = self.MAX_ENTRIES_PER_TYPE.get(cache_type, 50)
            if key not in cache_dict and len(cache_dict) >= max_entries:
                # Evict oldest entry (LRU-style)
                self._evict_oldest(cache_dict)
            
            # Store entry
            cache_dict[key] = CacheEntry(
                value=value,
                ttl_minutes=ttl_minutes
            )
            
            self._total_sets += 1
            
            logger.debug(
                f"Cache SET - Type: {cache_type}, Key: {key[:50]}..., "
                f"Session: {context.session_id[:8]}..."
            )
    
    def get(
        self,
        context: RequestContext,
        cache_type: str,
        key: str,
        default: Any = None
    ) -> Any:
        """
        Get value from session-scoped cache.
        
        Args:
            context: RequestContext identifying the session
            cache_type: Type of cache
            key: Cache key
            default: Default value if not found or expired
            
        Returns:
            Cached value or default
        """
        session_key = context.get_session_key()
        
        with self._lock:
            self._total_gets += 1
            
            # Check if session has this cache type
            if (session_key in self._caches and
                cache_type in self._caches[session_key] and
                key in self._caches[session_key][cache_type]):
                
                entry = self._caches[session_key][cache_type][key]
                
                # Check expiration
                if entry.is_expired():
                    # Remove expired entry
                    del self._caches[session_key][cache_type][key]
                    self._total_expirations += 1
                    self._total_misses += 1
                    
                    logger.debug(
                        f"Cache EXPIRED - Type: {cache_type}, Key: {key[:50]}..., "
                        f"Session: {context.session_id[:8]}..."
                    )
                    
                    return default
                
                # Valid entry - record access and return
                entry.access()
                self._total_hits += 1
                
                logger.debug(
                    f"Cache HIT - Type: {cache_type}, Key: {key[:50]}..., "
                    f"Session: {context.session_id[:8]}..."
                )
                
                return entry.value
            
            # Cache miss
            self._total_misses += 1
            
            logger.debug(
                f"Cache MISS - Type: {cache_type}, Key: {key[:50]}..., "
                f"Session: {context.session_id[:8]}..."
            )
            
            return default
    
    def has(self, context: RequestContext, cache_type: str, key: str) -> bool:
        """
        Check if key exists in cache (without accessing value).
        
        Args:
            context: RequestContext identifying the session
            cache_type: Type of cache
            key: Cache key
            
        Returns:
            True if key exists and not expired
        """
        session_key = context.get_session_key()
        
        with self._lock:
            if (session_key in self._caches and
                cache_type in self._caches[session_key] and
                key in self._caches[session_key][cache_type]):
                
                entry = self._caches[session_key][cache_type][key]
                return not entry.is_expired()
            
            return False
    
    def delete(self, context: RequestContext, cache_type: str, key: str) -> bool:
        """
        Delete specific cache entry.
        
        Args:
            context: RequestContext identifying the session
            cache_type: Type of cache
            key: Cache key
            
        Returns:
            True if entry was deleted, False if not found
        """
        session_key = context.get_session_key()
        
        with self._lock:
            if (session_key in self._caches and
                cache_type in self._caches[session_key] and
                key in self._caches[session_key][cache_type]):
                
                del self._caches[session_key][cache_type][key]
                
                logger.debug(
                    f"Cache DELETE - Type: {cache_type}, Key: {key[:50]}..., "
                    f"Session: {context.session_id[:8]}..."
                )
                
                return True
            
            return False
    
    def clear_type(self, context: RequestContext, cache_type: str) -> int:
        """
        Clear all entries of a specific cache type for session.
        
        Args:
            context: RequestContext identifying the session
            cache_type: Type of cache to clear
            
        Returns:
            Number of entries cleared
        """
        session_key = context.get_session_key()
        
        with self._lock:
            if (session_key in self._caches and
                cache_type in self._caches[session_key]):
                
                count = len(self._caches[session_key][cache_type])
                del self._caches[session_key][cache_type]
                
                logger.info(
                    f"Cleared cache type '{cache_type}' - {count} entries removed, "
                    f"Session: {context.session_id[:8]}..."
                )
                
                return count
            
            return 0
    
    def cleanup_session(self, context: RequestContext) -> int:
        """
        Remove all caches for a session.
        
        Called when session expires to free memory.
        
        Args:
            context: RequestContext identifying the session
            
        Returns:
            Total number of entries removed
        """
        session_key = context.get_session_key()
        total_entries = 0
        
        with self._lock:
            if session_key in self._caches:
                # Count total entries
                for cache_type, cache_dict in self._caches[session_key].items():
                    total_entries += len(cache_dict)
                
                # Remove entire session
                del self._caches[session_key]
                
                logger.info(
                    f"Cleaned up all caches for session {context.session_id[:8]}... - "
                    f"{total_entries} entries removed"
                )
        
        return total_entries
    
    def cleanup_expired_entries(self) -> int:
        """
        Remove expired entries across all sessions.
        
        Returns:
            Number of expired entries removed
        """
        expired_count = 0
        
        with self._lock:
            # Iterate through all sessions and cache types
            for session_key in list(self._caches.keys()):
                for cache_type in list(self._caches[session_key].keys()):
                    # Find expired entries
                    cache_dict = self._caches[session_key][cache_type]
                    expired_keys = [
                        k for k, entry in cache_dict.items()
                        if entry.is_expired()
                    ]
                    
                    # Remove expired entries
                    for key in expired_keys:
                        del cache_dict[key]
                        expired_count += 1
                    
                    # Remove empty cache type
                    if not cache_dict:
                        del self._caches[session_key][cache_type]
                
                # Remove empty session
                if not self._caches[session_key]:
                    del self._caches[session_key]
        
        if expired_count > 0:
            logger.info(f"Cleaned up {expired_count} expired cache entries")
        
        return expired_count
    
    def get_all(
        self,
        context: RequestContext,
        cache_type: str
    ) -> Dict[str, Any]:
        """
        Get all entries of a specific cache type for session.
        
        Args:
            context: RequestContext identifying the session
            cache_type: Type of cache
            
        Returns:
            Dictionary of all non-expired entries
        """
        session_key = context.get_session_key()
        result = {}
        
        with self._lock:
            if (session_key in self._caches and
                cache_type in self._caches[session_key]):
                
                cache_dict = self._caches[session_key][cache_type]
                
                # Return all non-expired entries
                for key, entry in cache_dict.items():
                    if not entry.is_expired():
                        entry.access()
                        result[key] = entry.value
        
        return result
    
    def get_statistics(self) -> Dict:
        """
        Get comprehensive cache statistics.
        
        Returns:
            Dictionary with cache stats
        """
        with self._lock:
            total_sessions = len(self._caches)
            total_entries = 0
            entries_by_type = {}
            
            for session_key, session_caches in self._caches.items():
                for cache_type, cache_dict in session_caches.items():
                    entries_by_type[cache_type] = entries_by_type.get(cache_type, 0) + len(cache_dict)
                    total_entries += len(cache_dict)
            
            hit_rate = (
                self._total_hits / self._total_gets
                if self._total_gets > 0
                else 0.0
            )
            
            return {
                'total_sessions': total_sessions,
                'total_entries': total_entries,
                'entries_by_type': entries_by_type,
                'total_sets': self._total_sets,
                'total_gets': self._total_gets,
                'total_hits': self._total_hits,
                'total_misses': self._total_misses,
                'total_expirations': self._total_expirations,
                'hit_rate': round(hit_rate, 3)
            }
    
    def _evict_oldest(self, cache_dict: Dict[str, CacheEntry]):
        """
        Evict oldest entry from cache (LRU-style).
        
        Args:
            cache_dict: Cache dictionary to evict from
        """
        if not cache_dict:
            return
        
        # Find oldest accessed entry
        oldest_key = min(
            cache_dict.keys(),
            key=lambda k: cache_dict[k].last_accessed
        )
        
        del cache_dict[oldest_key]
        logger.debug(f"Evicted cache entry: {oldest_key[:50]}... (LRU)")
    
    def __repr__(self) -> str:
        """Developer-friendly representation"""
        stats = self.get_statistics()
        return (
            f"ScopedCacheManager("
            f"sessions={stats['total_sessions']}, "
            f"entries={stats['total_entries']}, "
            f"hit_rate={stats['hit_rate']:.1%})"
        )


# Global instance for easy access
_scoped_cache_manager: Optional[ScopedCacheManager] = None


def get_scoped_cache_manager() -> ScopedCacheManager:
    """
    Get or create the global ScopedCacheManager instance.
    
    Returns:
        ScopedCacheManager instance
    """
    global _scoped_cache_manager
    
    if _scoped_cache_manager is None:
        _scoped_cache_manager = ScopedCacheManager()
    
    return _scoped_cache_manager


