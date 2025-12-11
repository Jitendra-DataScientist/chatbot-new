"""
Hierarchical State Manager - Multi-Tenant State Management

Replaces flat dictionary state manager with proper three-tier hierarchy:
    User -> Dashboard -> Session -> ChatState

Key Features:
- Complete isolation between users, dashboards, and sessions
- Thread-safe with fine-grained locking (RLock for reentrant calls)
- Automatic session tracking and expiration
- Memory-efficient cleanup of expired sessions
- Comprehensive metrics and monitoring hooks

Architecture Decisions:
1. Three-level nested dictionary for O(1) lookups with isolation
2. Separate metadata store for efficient expiration checks without traversing states
3. RLock (reentrant) to allow nested state access patterns
4. Immutable RequestContext prevents accidental key corruption

Author: Enterprise Architecture Refactor
Date: December 2024
"""

import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from core.request_context import RequestContext
from tableau_backend import ChatState
from master_logger import setup_module_logger

logger = setup_module_logger('core.hierarchical_state_manager')


@dataclass
class SessionMetadata:
    """
    Lightweight metadata for session tracking.
    
    Stored separately from ChatState to enable efficient expiration
    checks without loading heavy state objects.
    """
    context: RequestContext
    created_at: datetime
    last_activity: datetime
    query_count: int = 0
    data_size_mb: float = 0.0
    
    def update_activity(self):
        """Update last activity timestamp"""
        self.last_activity = datetime.utcnow()
        self.query_count += 1
    
    def get_age_minutes(self) -> float:
        """Get session age in minutes"""
        return (datetime.utcnow() - self.created_at).total_seconds() / 60
    
    def get_idle_minutes(self) -> float:
        """Get idle time in minutes"""
        return (datetime.utcnow() - self.last_activity).total_seconds() / 60
    
    def is_expired(self, max_idle_minutes: int) -> bool:
        """Check if session is expired"""
        return self.get_idle_minutes() > max_idle_minutes


class HierarchicalStateManager:
    """
    Thread-safe hierarchical state manager with proper multi-tenant isolation.
    
    Structure:
        _user_states: {
            user_id: {
                dashboard_key: {
                    session_id: ChatState
                }
            }
        }
    
    This structure ensures:
    - Different users never see each other's data
    - Same user on different dashboards has isolated states
    - Multiple sessions of same user on same dashboard are isolated
    
    Thread Safety:
    - RLock (reentrant lock) allows nested calls from same thread
    - All public methods acquire lock before accessing state
    - Metadata operations are also locked for consistency
    
    Performance:
    - O(1) lookup for any session (three dict lookups)
    - Lazy creation: structures created on-demand
    - Efficient cleanup: only traverses expired sessions
    """
    
    def __init__(self):
        """Initialize empty hierarchical state structure"""
        # Main state storage: {user_id: {dashboard_key: {session_id: ChatState}}}
        self._user_states: Dict[str, Dict[str, Dict[str, ChatState]]] = {}
        
        # Metadata for efficient tracking: {session_key: SessionMetadata}
        self._session_metadata: Dict[str, SessionMetadata] = {}
        
        # Reentrant lock for thread safety
        self._lock = threading.RLock()
        
        # Statistics
        self._total_sessions_created = 0
        self._total_sessions_expired = 0
        
        logger.info("=" * 80)
        logger.info("HierarchicalStateManager Initialized")
        logger.info("- Three-tier isolation: User -> Dashboard -> Session")
        logger.info("- Thread-safe with RLock")
        logger.info("- Automatic session tracking and expiration")
        logger.info("=" * 80)
    
    def get_or_create_state(self, context: RequestContext) -> ChatState:
        """
        Get existing state or create new one for the given context.
        
        This is the primary method for accessing state. Always use this
        instead of direct dictionary access to ensure proper initialization
        and activity tracking.
        
        Args:
            context: Immutable RequestContext identifying the session
            
        Returns:
            ChatState for the session (existing or newly created)
        """
        session_key = context.get_session_key()
        
        with self._lock:
            # Navigate/create the three-tier structure
            user_id = context.get_user_key()
            dashboard_key = context.get_dashboard_key()
            session_id = context.session_id
            
            # Ensure user tier exists
            if user_id not in self._user_states:
                self._user_states[user_id] = {}
                logger.info(f"Created user tier for: {context.user.username}")
            
            user_states = self._user_states[user_id]
            
            # Ensure dashboard tier exists
            if dashboard_key not in user_states:
                user_states[dashboard_key] = {}
                logger.info(f"Created dashboard tier for: {context.user.username} -> {context.dashboard_name}")
            
            dashboard_states = user_states[dashboard_key]
            
            # Check if session exists
            if session_id in dashboard_states:
                # Existing session - update activity
                state = dashboard_states[session_id]
                state.update_activity()
                
                # Update metadata
                if session_key in self._session_metadata:
                    self._session_metadata[session_key].update_activity()
                
                logger.debug(
                    f"Retrieved existing state - "
                    f"User: {context.user.username}, Session: {session_id[:8]}..."
                )
                
                return state
            
            # Create new session
            state = self._create_new_state(context)
            dashboard_states[session_id] = state
            
            # Create metadata
            self._session_metadata[session_key] = SessionMetadata(
                context=context,
                created_at=context.created_at,
                last_activity=datetime.utcnow()
            )
            
            self._total_sessions_created += 1
            
            logger.info(
                f"Created NEW session - "
                f"User: {context.user.username}, "
                f"Dashboard: {context.dashboard_name}, "
                f"Session: {session_id[:8]}... "
                f"(Total sessions: {self._total_sessions_created})"
            )
            
            return state
    
    def get_state(self, context: RequestContext) -> Optional[ChatState]:
        """
        Get state if it exists, without creating new one.
        
        Use this when you want to check if a session exists but don't
        want to auto-create it.
        
        Args:
            context: RequestContext identifying the session
            
        Returns:
            ChatState if exists, None otherwise
        """
        with self._lock:
            user_id = context.get_user_key()
            dashboard_key = context.get_dashboard_key()
            session_id = context.session_id
            
            if user_id in self._user_states:
                if dashboard_key in self._user_states[user_id]:
                    if session_id in self._user_states[user_id][dashboard_key]:
                        state = self._user_states[user_id][dashboard_key][session_id]
                        state.update_activity()
                        
                        # Update metadata
                        session_key = context.get_session_key()
                        if session_key in self._session_metadata:
                            self._session_metadata[session_key].update_activity()
                        
                        return state
            
            return None
    
    def get_state_by_session_id(self, session_id: str) -> Optional[ChatState]:
        """
        Retrieve state by session_id alone across all users and dashboards.
        
        This is the primary access method for multi-user, multi-dashboard scenarios
        where the frontend only has session_id available (e.g., query parameters).
        
        Session ID is globally unique (UUID) and is the only safe way to retrieve
        state without risking data leakage between users or dashboard sessions.
        
        Performance: O(n) where n = total active sessions. Optimized with early exit.
        For production at scale, consider adding a reverse index: {session_id: (user, dashboard)}
        
        Args:
            session_id: UUID string identifying the session
            
        Returns:
            ChatState if session exists, None otherwise
        """
        with self._lock:
            # Traverse hierarchy to find session
            for user_id, user_states in self._user_states.items():
                for dashboard_key, dashboard_states in user_states.items():
                    if session_id in dashboard_states:
                        state = dashboard_states[session_id]
                        state.update_activity()
                        
                        # Update metadata if available
                        # Note: We don't have full context here, so we iterate metadata
                        for meta_key, metadata in self._session_metadata.items():
                            if metadata.context.session_id == session_id:
                                metadata.update_activity()
                                break
                        
                        logger.debug(f"Found state for session {session_id[:8]}... under user={user_id[:8]}..., dashboard={dashboard_key}")
                        return state
            
            logger.warning(f"No state found for session_id: {session_id[:8]}...")
            return None
    
    def store_state(self, context: RequestContext, state: ChatState):
        """
        Explicitly store/update state for a session.
        
        Typically not needed as get_or_create_state handles creation,
        but useful for updating state with new data.
        
        Args:
            context: RequestContext identifying the session
            state: ChatState to store
        """
        session_key = context.get_session_key()
        
        with self._lock:
            user_id = context.get_user_key()
            dashboard_key = context.get_dashboard_key()
            session_id = context.session_id
            
            # Ensure structure exists
            if user_id not in self._user_states:
                self._user_states[user_id] = {}
            if dashboard_key not in self._user_states[user_id]:
                self._user_states[user_id][dashboard_key] = {}
            
            # Store state
            self._user_states[user_id][dashboard_key][session_id] = state
            state.update_activity()
            
            # Update or create metadata
            if session_key in self._session_metadata:
                self._session_metadata[session_key].update_activity()
            else:
                self._session_metadata[session_key] = SessionMetadata(
                    context=context,
                    created_at=context.created_at,
                    last_activity=datetime.utcnow()
                )
            
            logger.debug(f"Stored state for session: {session_id[:8]}...")
    
    def clear_session(self, context: RequestContext) -> bool:
        """
        Remove specific session and its associated resources.
        
        Args:
            context: RequestContext identifying the session
            
        Returns:
            True if session was removed, False if it didn't exist
        """
        session_key = context.get_session_key()
        
        with self._lock:
            user_id = context.get_user_key()
            dashboard_key = context.get_dashboard_key()
            session_id = context.session_id
            
            # Check if session exists
            if (user_id in self._user_states and
                dashboard_key in self._user_states[user_id] and
                session_id in self._user_states[user_id][dashboard_key]):
                
                # Remove session
                del self._user_states[user_id][dashboard_key][session_id]
                
                # Cleanup empty structures
                if not self._user_states[user_id][dashboard_key]:
                    del self._user_states[user_id][dashboard_key]
                if not self._user_states[user_id]:
                    del self._user_states[user_id]
                
                # Remove metadata
                self._session_metadata.pop(session_key, None)
                
                logger.info(f"Cleared session: {session_id[:8]}... for user: {context.user.username}")
                return True
            
            return False
    
    def cleanup_expired_sessions(self, max_idle_minutes: int = 120) -> List[RequestContext]:
        """
        Remove sessions that have been idle for too long.
        
        Returns list of expired contexts so caller can cleanup associated
        resources (DataFrames, caches, agents, etc.)
        
        Args:
            max_idle_minutes: Maximum idle time before expiration
            
        Returns:
            List of RequestContexts for expired sessions
        """
        expired_contexts = []
        cutoff_time = datetime.utcnow() - timedelta(minutes=max_idle_minutes)
        
        with self._lock:
            # Find expired sessions from metadata (efficient)
            expired_keys = []
            for session_key, metadata in self._session_metadata.items():
                if metadata.last_activity < cutoff_time:
                    expired_keys.append(session_key)
                    expired_contexts.append(metadata.context)
            
            # Remove expired sessions
            for context in expired_contexts:
                self._remove_session_internal(context)
            
            self._total_sessions_expired += len(expired_contexts)
        
        if expired_contexts:
            logger.info(
                f"Cleaned up {len(expired_contexts)} expired sessions "
                f"(idle > {max_idle_minutes} min). "
                f"Total expired: {self._total_sessions_expired}"
            )
        
        return expired_contexts
    
    def get_user_sessions(self, user_id: str) -> List[Tuple[RequestContext, ChatState]]:
        """
        Get all active sessions for a specific user.
        
        Useful for monitoring, debugging, and user-level operations.
        
        Args:
            user_id: User's primary ID (LUID)
            
        Returns:
            List of (RequestContext, ChatState) tuples
        """
        sessions = []
        
        with self._lock:
            if user_id in self._user_states:
                for dashboard_key, dashboard_states in self._user_states[user_id].items():
                    for session_id, state in dashboard_states.items():
                        # Find matching metadata
                        session_key = f"{user_id}:{dashboard_key.split(':')[1]}:{session_id}"
                        if session_key in self._session_metadata:
                            context = self._session_metadata[session_key].context
                            sessions.append((context, state))
        
        return sessions
    
    def get_dashboard_sessions(self, user_id: str, dashboard_key: str) -> List[Tuple[RequestContext, ChatState]]:
        """
        Get all active sessions for a specific user on a specific dashboard.
        
        Args:
            user_id: User's primary ID
            dashboard_key: Dashboard key (user_id:workbook_id)
            
        Returns:
            List of (RequestContext, ChatState) tuples
        """
        sessions = []
        
        with self._lock:
            if (user_id in self._user_states and
                dashboard_key in self._user_states[user_id]):
                
                for session_id, state in self._user_states[user_id][dashboard_key].items():
                    session_key = f"{dashboard_key}:{session_id}"
                    if session_key in self._session_metadata:
                        context = self._session_metadata[session_key].context
                        sessions.append((context, state))
        
        return sessions
    
    def get_statistics(self) -> Dict:
        """
        Get comprehensive statistics about state manager.
        
        Returns:
            Dictionary with stats (users, dashboards, sessions, memory, etc.)
        """
        with self._lock:
            total_users = len(self._user_states)
            total_dashboards = sum(len(dashboards) for dashboards in self._user_states.values())
            total_sessions = len(self._session_metadata)
            
            # Calculate age distribution
            now = datetime.utcnow()
            age_buckets = {'<5min': 0, '5-30min': 0, '30-120min': 0, '>120min': 0}
            
            for metadata in self._session_metadata.values():
                age_min = metadata.get_age_minutes()
                if age_min < 5:
                    age_buckets['<5min'] += 1
                elif age_min < 30:
                    age_buckets['5-30min'] += 1
                elif age_min < 120:
                    age_buckets['30-120min'] += 1
                else:
                    age_buckets['>120min'] += 1
            
            return {
                'total_users': total_users,
                'total_dashboards': total_dashboards,
                'total_active_sessions': total_sessions,
                'total_sessions_created': self._total_sessions_created,
                'total_sessions_expired': self._total_sessions_expired,
                'session_age_distribution': age_buckets,
                'avg_sessions_per_user': total_sessions / total_users if total_users > 0 else 0,
            }
    
    def _create_new_state(self, context: RequestContext) -> ChatState:
        """
        Internal method to create new ChatState from context.
        
        Args:
            context: RequestContext with session information
            
        Returns:
            New ChatState instance
        """
        # Create ChatState with proper session tracking
        state = ChatState(
            session_id=context.session_id,
            workbook_name=context.workbook_name,
            workbook_id=context.workbook_id,
            dashboard_name=context.dashboard_name,
            connection_timestamp=context.created_at,
            last_activity=datetime.utcnow()
        )
        
        return state
    
    def _remove_session_internal(self, context: RequestContext):
        """
        Internal method to remove session without lock acquisition.
        
        Assumes caller has already acquired lock.
        Used by cleanup_expired_sessions to avoid nested lock issues.
        
        Args:
            context: RequestContext identifying session to remove
        """
        user_id = context.get_user_key()
        dashboard_key = context.get_dashboard_key()
        session_id = context.session_id
        session_key = context.get_session_key()
        
        # Remove from state hierarchy
        if (user_id in self._user_states and
            dashboard_key in self._user_states[user_id] and
            session_id in self._user_states[user_id][dashboard_key]):
            
            del self._user_states[user_id][dashboard_key][session_id]
            
            # Cleanup empty structures
            if not self._user_states[user_id][dashboard_key]:
                del self._user_states[user_id][dashboard_key]
            if not self._user_states[user_id]:
                del self._user_states[user_id]
        
        # Remove metadata
        self._session_metadata.pop(session_key, None)
    
    def __repr__(self) -> str:
        """Developer-friendly representation"""
        stats = self.get_statistics()
        return (
            f"HierarchicalStateManager("
            f"users={stats['total_users']}, "
            f"dashboards={stats['total_dashboards']}, "
            f"sessions={stats['total_active_sessions']})"
        )

