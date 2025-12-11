"""
Session Lifecycle Manager - Coordinated Session Management

Orchestrates session lifecycle across all managers:
- HierarchicalStateManager (ChatState)
- ScopedDataManager (DataFrames)
- ScopedCacheManager (caches)
- Agent instances (if using factory pattern)

Key Responsibilities:
1. Background cleanup thread for expired sessions
2. Coordinated resource cleanup across all managers
3. Session metrics and monitoring
4. Graceful shutdown handling

Author: Enterprise Architecture Refactor
Date: December 2024
"""

import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass

from core.request_context import RequestContext
from core.hierarchical_state_manager import HierarchicalStateManager
from core.scoped_data_manager import ScopedDataManager
from core.scoped_cache_manager import ScopedCacheManager
from master_logger import setup_module_logger

logger = setup_module_logger('core.session_lifecycle_manager')


@dataclass
class CleanupStatistics:
    """Statistics from a cleanup cycle"""
    timestamp: datetime
    sessions_expired: int
    states_removed: int
    dataframes_removed: int
    cache_entries_removed: int
    duration_seconds: float


class SessionLifecycleManager:
    """
    Coordinates session lifecycle and automatic cleanup.
    
    This manager runs a background thread that periodically:
    1. Identifies expired sessions from HierarchicalStateManager
    2. Cleans up states, data, and caches for expired sessions
    3. Logs cleanup statistics
    4. Provides health monitoring endpoints
    
    Thread Safety:
    - All operations are thread-safe via manager locks
    - Background thread uses daemon=True for automatic shutdown
    - Graceful shutdown with stop() method
    
    Configuration:
    - cleanup_interval_minutes: How often to run cleanup (default: 30)
    - session_ttl_minutes: Max idle time before expiration (default: 120)
    """
    
    def __init__(
        self,
        state_manager: HierarchicalStateManager,
        data_manager: ScopedDataManager,
        cache_manager: ScopedCacheManager,
        cleanup_interval_minutes: int = 30,
        session_ttl_minutes: int = 120
    ):
        """
        Initialize lifecycle manager with all resource managers.
        
        Args:
            state_manager: HierarchicalStateManager instance
            data_manager: ScopedDataManager instance
            cache_manager: ScopedCacheManager instance
            cleanup_interval_minutes: How often to run cleanup
            session_ttl_minutes: Session idle timeout
        """
        self.state_manager = state_manager
        self.data_manager = data_manager
        self.cache_manager = cache_manager
        
        self.cleanup_interval_minutes = cleanup_interval_minutes
        self.session_ttl_minutes = session_ttl_minutes
        
        # Background thread
        self._cleanup_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        
        # Cleanup history
        self._cleanup_history: List[CleanupStatistics] = []
        self._max_history = 100  # Keep last 100 cleanup cycles
        
        # Optional custom cleanup hooks
        self._cleanup_hooks: List[Callable[[RequestContext], None]] = []
        
        logger.info("=" * 80)
        logger.info("SessionLifecycleManager Initialized")
        logger.info(f"- Cleanup interval: {cleanup_interval_minutes} minutes")
        logger.info(f"- Session TTL: {session_ttl_minutes} minutes")
        logger.info("=" * 80)
    
    def start(self):
        """
        Start background cleanup thread.
        
        Thread runs as daemon so it automatically stops when main process exits.
        """
        if self._running:
            logger.warning("Lifecycle manager already running")
            return
        
        self._stop_event.clear()
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            name="SessionLifecycleCleanup",
            daemon=True
        )
        self._cleanup_thread.start()
        self._running = True
        
        logger.info(
            f"✅ Background cleanup thread started - "
            f"Running every {self.cleanup_interval_minutes} minutes"
        )
    
    def stop(self, timeout: float = 10.0):
        """
        Stop background cleanup thread gracefully.
        
        Args:
            timeout: Maximum time to wait for thread to stop (seconds)
        """
        if not self._running:
            return
        
        logger.info("Stopping lifecycle manager...")
        self._stop_event.set()
        
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=timeout)
            
            if self._cleanup_thread.is_alive():
                logger.warning(
                    f"Cleanup thread did not stop within {timeout}s timeout"
                )
            else:
                logger.info("✅ Cleanup thread stopped gracefully")
        
        self._running = False
    
    def add_cleanup_hook(self, hook: Callable[[RequestContext], None]):
        """
        Add custom cleanup hook called for each expired session.
        
        Useful for cleaning up additional resources not managed by core managers.
        
        Args:
            hook: Function that takes RequestContext and performs cleanup
        """
        self._cleanup_hooks.append(hook)
        logger.info(f"Added cleanup hook: {hook.__name__}")
    
    def force_cleanup(self) -> CleanupStatistics:
        """
        Force immediate cleanup cycle (bypassing scheduled interval).
        
        Useful for testing or manual cleanup triggers.
        
        Returns:
            CleanupStatistics from this cycle
        """
        logger.info("🔧 Manual cleanup triggered")
        return self._perform_cleanup()
    
    def cleanup_specific_session(self, context: RequestContext) -> bool:
        """
        Cleanup a specific session immediately.
        
        Args:
            context: RequestContext identifying session to cleanup
            
        Returns:
            True if session was found and cleaned up
        """
        logger.info(
            f"Cleaning up specific session: {context.session_id[:8]}... "
            f"(User: {context.user.username})"
        )
        
        # Remove from state manager
        state_removed = self.state_manager.clear_session(context)
        
        # Remove from data manager
        dataframes_removed = self.data_manager.cleanup_session(context)
        
        # Remove from cache manager
        cache_entries_removed = self.cache_manager.cleanup_session(context)
        
        # Run custom hooks
        for hook in self._cleanup_hooks:
            try:
                hook(context)
            except Exception as e:
                logger.error(f"Cleanup hook {hook.__name__} failed: {e}")
        
        if state_removed:
            logger.info(
                f"✅ Cleaned up session {context.session_id[:8]}... - "
                f"DataFrames: {dataframes_removed}, Cache entries: {cache_entries_removed}"
            )
            return True
        else:
            logger.warning(f"Session {context.session_id[:8]}... not found")
            return False
    
    def get_health_status(self) -> Dict:
        """
        Get health status and resource usage.
        
        Returns:
            Dictionary with health metrics
        """
        state_stats = self.state_manager.get_statistics()
        data_stats = self.data_manager.get_memory_usage()
        cache_stats = self.cache_manager.get_statistics()
        
        # Get last cleanup info
        last_cleanup = None
        if self._cleanup_history:
            last = self._cleanup_history[-1]
            last_cleanup = {
                'timestamp': last.timestamp.isoformat(),
                'sessions_expired': last.sessions_expired,
                'duration_seconds': round(last.duration_seconds, 2)
            }
        
        return {
            'lifecycle_manager': {
                'running': self._running,
                'cleanup_interval_minutes': self.cleanup_interval_minutes,
                'session_ttl_minutes': self.session_ttl_minutes,
                'last_cleanup': last_cleanup,
                'total_cleanup_cycles': len(self._cleanup_history)
            },
            'state_manager': state_stats,
            'data_manager': data_stats,
            'cache_manager': cache_stats,
            'overall_health': self._calculate_health_score(state_stats, data_stats)
        }
    
    def get_cleanup_history(self, last_n: int = 10) -> List[Dict]:
        """
        Get recent cleanup history.
        
        Args:
            last_n: Number of recent cycles to return
            
        Returns:
            List of cleanup statistics dictionaries
        """
        recent = self._cleanup_history[-last_n:]
        return [
            {
                'timestamp': stat.timestamp.isoformat(),
                'sessions_expired': stat.sessions_expired,
                'states_removed': stat.states_removed,
                'dataframes_removed': stat.dataframes_removed,
                'cache_entries_removed': stat.cache_entries_removed,
                'duration_seconds': round(stat.duration_seconds, 3)
            }
            for stat in recent
        ]
    
    def _cleanup_loop(self):
        """
        Background thread loop that performs periodic cleanup.
        
        Runs until stop_event is set.
        """
        logger.info("🔄 Cleanup loop started")
        
        while not self._stop_event.is_set():
            try:
                # Wait for cleanup interval (or until stop event)
                wait_seconds = self.cleanup_interval_minutes * 60
                
                if self._stop_event.wait(timeout=wait_seconds):
                    # Stop event was set
                    break
                
                # Perform cleanup
                self._perform_cleanup()
                
            except Exception as e:
                logger.error(f"❌ Error in cleanup loop: {e}", exc_info=True)
                # Continue loop despite error
        
        logger.info("🔄 Cleanup loop stopped")
    
    def _perform_cleanup(self) -> CleanupStatistics:
        """
        Perform one cleanup cycle.
        
        Returns:
            CleanupStatistics from this cycle
        """
        start_time = time.time()
        
        logger.info("=" * 80)
        logger.info(f"🧹 Starting cleanup cycle at {datetime.utcnow().isoformat()}")
        logger.info(f"   Session TTL: {self.session_ttl_minutes} minutes")
        
        try:
            # Step 1: Get expired sessions from state manager
            expired_contexts = self.state_manager.cleanup_expired_sessions(
                max_idle_minutes=self.session_ttl_minutes
            )
            
            sessions_expired = len(expired_contexts)
            
            if sessions_expired == 0:
                logger.info("   No expired sessions found")
                duration = time.time() - start_time
                
                stats = CleanupStatistics(
                    timestamp=datetime.utcnow(),
                    sessions_expired=0,
                    states_removed=0,
                    dataframes_removed=0,
                    cache_entries_removed=0,
                    duration_seconds=duration
                )
                
                self._record_cleanup(stats)
                return stats
            
            logger.info(f"   Found {sessions_expired} expired session(s)")
            
            # Step 2: Cleanup data and caches for expired sessions
            total_dataframes = 0
            total_cache_entries = 0
            
            for context in expired_contexts:
                try:
                    # Cleanup data
                    df_count = self.data_manager.cleanup_session(context)
                    total_dataframes += df_count
                    
                    # Cleanup caches
                    cache_count = self.cache_manager.cleanup_session(context)
                    total_cache_entries += cache_count
                    
                    # Run custom cleanup hooks
                    for hook in self._cleanup_hooks:
                        try:
                            hook(context)
                        except Exception as e:
                            logger.error(
                                f"Cleanup hook {hook.__name__} failed for "
                                f"session {context.session_id[:8]}...: {e}"
                            )
                    
                    logger.debug(
                        f"   ✓ Cleaned session {context.session_id[:8]}... - "
                        f"User: {context.user.username}, "
                        f"DataFrames: {df_count}, Cache: {cache_count}"
                    )
                    
                except Exception as e:
                    logger.error(
                        f"Failed to cleanup session {context.session_id[:8]}...: {e}",
                        exc_info=True
                    )
            
            # Step 3: Cleanup orphaned expired cache entries
            expired_cache_entries = self.cache_manager.cleanup_expired_entries()
            total_cache_entries += expired_cache_entries
            
            duration = time.time() - start_time
            
            # Log summary
            logger.info("   " + "=" * 76)
            logger.info(f"   ✅ Cleanup completed in {duration:.2f}s")
            logger.info(f"      Sessions expired: {sessions_expired}")
            logger.info(f"      DataFrames removed: {total_dataframes}")
            logger.info(f"      Cache entries removed: {total_cache_entries}")
            logger.info("=" * 80)
            
            # Record statistics
            stats = CleanupStatistics(
                timestamp=datetime.utcnow(),
                sessions_expired=sessions_expired,
                states_removed=sessions_expired,
                dataframes_removed=total_dataframes,
                cache_entries_removed=total_cache_entries,
                duration_seconds=duration
            )
            
            self._record_cleanup(stats)
            return stats
            
        except Exception as e:
            logger.error(f"❌ Cleanup cycle failed: {e}", exc_info=True)
            duration = time.time() - start_time
            
            stats = CleanupStatistics(
                timestamp=datetime.utcnow(),
                sessions_expired=0,
                states_removed=0,
                dataframes_removed=0,
                cache_entries_removed=0,
                duration_seconds=duration
            )
            
            self._record_cleanup(stats)
            return stats
    
    def _record_cleanup(self, stats: CleanupStatistics):
        """Record cleanup statistics in history"""
        self._cleanup_history.append(stats)
        
        # Trim history if too long
        if len(self._cleanup_history) > self._max_history:
            self._cleanup_history = self._cleanup_history[-self._max_history:]
    
    def _calculate_health_score(
        self,
        state_stats: Dict,
        data_stats: Dict
    ) -> str:
        """
        Calculate overall health score based on resource usage.
        
        Returns:
            'healthy', 'warning', or 'critical'
        """
        # Check memory usage
        memory_mb = data_stats.get('total_memory_mb', 0)
        if memory_mb > 5000:  # 5GB
            return 'critical'
        elif memory_mb > 2000:  # 2GB
            return 'warning'
        
        # Check session count
        session_count = state_stats.get('total_active_sessions', 0)
        if session_count > 1000:
            return 'critical'
        elif session_count > 500:
            return 'warning'
        
        return 'healthy'
    
    def __repr__(self) -> str:
        """Developer-friendly representation"""
        return (
            f"SessionLifecycleManager("
            f"running={self._running}, "
            f"cleanup_interval={self.cleanup_interval_minutes}min, "
            f"ttl={self.session_ttl_minutes}min)"
        )


