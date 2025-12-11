"""
Request Context System - Foundation for Multi-User, Multi-Dashboard Architecture

This module provides immutable, hierarchical context objects that uniquely identify
every request in the system. Eliminates fragile connection_key logic and enables
proper user/dashboard/session isolation.

Architecture:
    User Layer (authenticated identity)
    └─ Dashboard Instance Layer (workbook + dashboard)
       └─ Session Layer (page lifecycle)
          └─ Query Layer (individual conversations)

Author: Enterprise Architecture Refactor
Date: December 2024
"""

import hashlib
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from master_logger import setup_module_logger

logger = setup_module_logger('core.request_context')


@dataclass(frozen=True)
class UserIdentity:
    """
    Immutable user identity extracted from Tableau session.
    
    Uses Tableau LUID (UUID) as primary identifier for stability and uniqueness.
    Email/username may change, but LUID remains constant.
    
    Attributes:
        primary_id: Tableau user LUID (UUID format) - globally unique, immutable
        username: User email address (for display and logging)
        display_name: Human-readable name (for UI)
        system_user_id: Tableau internal numeric ID (optional, for validation)
        domain_name: Tableau authentication domain (e.g., 'external', 'local')
    """
    primary_id: str
    username: str
    display_name: str
    system_user_id: Optional[int] = None
    domain_name: str = 'local'
    
    def __post_init__(self):
        """Validate user identity fields on creation"""
        if not self.primary_id or not self.primary_id.strip():
            raise ValueError("User primary_id cannot be empty")
        if not self.username or not self.username.strip():
            raise ValueError("Username cannot be empty")
        if not self.display_name or not self.display_name.strip():
            raise ValueError("Display name cannot be empty")
        
        # Log user identity creation (without PII in production)
        logger.debug(f"UserIdentity created - ID: {self.primary_id[:8]}..., Domain: {self.domain_name}")
    
    @classmethod
    def from_tableau_session(cls, session_data: Dict[str, Any]) -> 'UserIdentity':
        """
        Factory method: Create UserIdentity from Tableau session data.
        
        Expected format (from window.bootstrapData.user or Extensions API):
        {
            'luid': 'b00ae7b7-b09e-4701-8cb9-1ee3156bce89',
            'username': 'user@example.com',
            'displayName': 'John Doe',
            'systemUserId': 12345,
            'domainName': 'external'
        }
        
        Args:
            session_data: Dictionary containing Tableau user data
            
        Returns:
            UserIdentity instance
            
        Raises:
            ValueError: If required fields are missing or invalid
        """
        # Validate required fields
        required_fields = ['luid', 'username', 'displayName']
        missing = [f for f in required_fields if not session_data.get(f)]
        
        if missing:
            raise ValueError(f"Missing required user fields: {missing}. Session data keys: {list(session_data.keys())}")
        
        logger.info(f"Creating UserIdentity from Tableau session - User: {session_data.get('username')}")
        
        return cls(
            primary_id=session_data['luid'],
            username=session_data['username'],
            display_name=session_data['displayName'],
            system_user_id=session_data.get('systemUserId'),
            domain_name=session_data.get('domainName', 'local')
        )
    
    @classmethod
    def create_anonymous(cls, fingerprint: Optional[str] = None) -> 'UserIdentity':
        """
        Factory method: Create anonymous user identity.
        
        Used as fallback when Tableau user data is unavailable.
        Creates stable anonymous ID based on browser fingerprint or generates new UUID.
        
        Args:
            fingerprint: Optional browser fingerprint for stable anonymous ID
            
        Returns:
            UserIdentity instance for anonymous user
        """
        if fingerprint:
            # Create deterministic UUID from fingerprint
            anonymous_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"anonymous_{fingerprint}"))
        else:
            # Generate random UUID for truly anonymous session
            anonymous_id = str(uuid.uuid4())
        
        logger.warning(f"Creating anonymous UserIdentity - ID: {anonymous_id[:8]}...")
        
        return cls(
            primary_id=f"anonymous_{anonymous_id}",
            username='anonymous@local',
            display_name='Anonymous User',
            system_user_id=None,
            domain_name='anonymous'
        )
    
    def get_user_key(self) -> str:
        """Return unique user key for storage and lookups"""
        return self.primary_id
    
    def is_anonymous(self) -> bool:
        """Check if this is an anonymous user"""
        return self.domain_name == 'anonymous' or self.primary_id.startswith('anonymous_')
    
    def __str__(self) -> str:
        """Human-readable representation"""
        return f"{self.display_name} ({self.username})"
    
    def __repr__(self) -> str:
        """Developer-friendly representation"""
        return f"UserIdentity(id={self.primary_id[:8]}..., user={self.username}, domain={self.domain_name})"


@dataclass(frozen=True)
class RequestContext:
    """
    Immutable request context that uniquely identifies a session.
    
    This object travels through the entire request pipeline, providing
    hierarchical isolation: User -> Dashboard -> Session -> Query
    
    Immutability (frozen=True) ensures thread-safety and prevents accidental
    modification during request processing.
    
    Key Design Decisions:
    1. Frozen dataclass - immutable for thread safety
    2. Hierarchical keys - enable flexible resource scoping
    3. Temporal tracking - automatic activity monitoring
    4. Validation in __post_init__ - fail fast on invalid data
    
    Attributes:
        user: UserIdentity object (who is making the request)
        workbook_id: Tableau workbook ID (stable identifier)
        workbook_name: Workbook name (human-readable)
        dashboard_name: Dashboard name within workbook
        session_id: UUID generated on page load/refresh
        created_at: When session was created
        last_activity: Last activity timestamp (updated immutably)
    """
    user: UserIdentity
    workbook_id: str
    workbook_name: str
    dashboard_name: str
    session_id: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_activity: datetime = field(default_factory=datetime.utcnow)
    
    def __post_init__(self):
        """Validate context on creation"""
        # Validate required fields
        if not self.workbook_id or not self.workbook_id.strip():
            raise ValueError("Workbook ID cannot be empty")
        if not self.dashboard_name or not self.dashboard_name.strip():
            raise ValueError("Dashboard name cannot be empty")
        if not self.session_id or not self.session_id.strip():
            raise ValueError("Session ID cannot be empty")
        
        # Validate UUID format for session_id
        try:
            uuid.UUID(self.session_id)
        except ValueError:
            raise ValueError(f"Session ID must be valid UUID format, got: {self.session_id}")
        
        logger.debug(
            f"RequestContext created - User: {self.user.username}, "
            f"Dashboard: {self.dashboard_name}, Session: {self.session_id[:8]}..."
        )
    
    @classmethod
    def from_request(cls, request_data: Dict[str, Any]) -> 'RequestContext':
        """
        Factory method: Create RequestContext from API request data.
        
        Expected format:
        {
            'context': {
                'user': {
                    'luid': '...',
                    'username': '...',
                    'displayName': '...',
                    'systemUserId': 123,
                    'domainName': 'external'
                },
                'workbook_id': 'workbook_123',
                'workbook_name': 'Sales Dashboard',
                'dashboard_name': 'Overview',
                'session_id': 'uuid-here',
                'timestamp': '2024-12-09T10:00:00Z'
            }
        }
        
        Args:
            request_data: Dictionary from Flask request.get_json()
            
        Returns:
            RequestContext instance
            
        Raises:
            ValueError: If required fields missing or invalid
        """
        # Extract context object
        context_data = request_data.get('context', {})
        
        if not context_data:
            raise ValueError("Missing 'context' object in request data")
        
        # Extract and validate user identity
        user_data = context_data.get('user', {})
        if not user_data:
            raise ValueError("Missing 'user' object in context")
        
        try:
            user = UserIdentity.from_tableau_session(user_data)
        except Exception as e:
            logger.error(f"Failed to create UserIdentity: {e}")
            raise ValueError(f"Invalid user data: {e}")
        
        # Extract dashboard context
        required_dashboard_fields = ['workbook_id', 'dashboard_name', 'session_id']
        missing = [f for f in required_dashboard_fields if not context_data.get(f)]
        
        if missing:
            raise ValueError(
                f"Missing required context fields: {missing}. "
                f"Available keys: {list(context_data.keys())}"
            )
        
        # Parse timestamp if provided (for session restoration)
        created_at = datetime.utcnow()
        if 'timestamp' in context_data:
            try:
                created_at = datetime.fromisoformat(context_data['timestamp'].replace('Z', '+00:00'))
            except Exception as e:
                logger.warning(f"Failed to parse timestamp: {e}, using current time")
        
        logger.info(
            f"Creating RequestContext from request - "
            f"User: {user.username}, Dashboard: {context_data['dashboard_name']}, "
            f"Session: {context_data['session_id'][:8]}..."
        )
        
        return cls(
            user=user,
            workbook_id=context_data['workbook_id'],
            workbook_name=context_data.get('workbook_name', context_data['workbook_id']),
            dashboard_name=context_data['dashboard_name'],
            session_id=context_data['session_id'],
            created_at=created_at,
            last_activity=datetime.utcnow()
        )
    
    def with_updated_activity(self) -> 'RequestContext':
        """
        Return new context with updated activity timestamp.
        
        Immutable pattern: Returns new instance rather than modifying existing.
        This ensures thread-safety and prevents accidental state corruption.
        
        Returns:
            New RequestContext with updated last_activity
        """
        return replace(self, last_activity=datetime.utcnow())
    
    def get_session_key(self) -> str:
        """
        Get unique session key for storage.
        
        Format: user_id:workbook_name:session_id
        Uses workbook_name (stable) instead of workbook_id (which changes during initialization).
        This provides complete isolation per user/dashboard/session.
        
        Returns:
            String key for session-level resource storage
        """
        return f"{self.user.primary_id}:{self.workbook_name}:{self.session_id}"
    
    def get_dashboard_key(self) -> str:
        """
        Get unique dashboard key for user+dashboard resources.
        
        Format: user_id:workbook_name
        Uses workbook_name (stable) instead of workbook_id.
        Use for resources shared across sessions of same user on same dashboard.
        
        Returns:
            String key for dashboard-level resource storage
        """
        return f"{self.user.primary_id}:{self.workbook_name}"
    
    def get_user_key(self) -> str:
        """
        Get unique user key for user-level resources.
        
        Returns:
            User's primary ID for user-level resource storage
        """
        return self.user.primary_id
    
    def get_age_seconds(self) -> float:
        """Get session age in seconds"""
        return (datetime.utcnow() - self.created_at).total_seconds()
    
    def get_idle_seconds(self) -> float:
        """Get idle time in seconds since last activity"""
        return (datetime.utcnow() - self.last_activity).total_seconds()
    
    def is_expired(self, max_idle_minutes: int = 120) -> bool:
        """
        Check if session has expired based on idle time.
        
        Args:
            max_idle_minutes: Maximum idle time before expiration
            
        Returns:
            True if session is expired
        """
        idle_minutes = self.get_idle_seconds() / 60
        return idle_minutes > max_idle_minutes
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert context to dictionary for serialization.
        
        Useful for logging, debugging, and passing to external systems.
        
        Returns:
            Dictionary representation
        """
        return {
            'user': {
                'primary_id': self.user.primary_id,
                'username': self.user.username,
                'display_name': self.user.display_name,
                'system_user_id': self.user.system_user_id,
                'domain_name': self.user.domain_name,
                'is_anonymous': self.user.is_anonymous()
            },
            'workbook_id': self.workbook_id,
            'workbook_name': self.workbook_name,
            'dashboard_name': self.dashboard_name,
            'session_id': self.session_id,
            'created_at': self.created_at.isoformat(),
            'last_activity': self.last_activity.isoformat(),
            'session_key': self.get_session_key(),
            'dashboard_key': self.get_dashboard_key(),
            'age_seconds': self.get_age_seconds(),
            'idle_seconds': self.get_idle_seconds()
        }
    
    def __str__(self) -> str:
        """Human-readable representation"""
        return (
            f"Session[{self.session_id[:8]}...] - "
            f"User: {self.user.display_name}, "
            f"Dashboard: {self.dashboard_name}"
        )
    
    def __repr__(self) -> str:
        """Developer-friendly representation"""
        return (
            f"RequestContext("
            f"user={self.user.username}, "
            f"dashboard={self.dashboard_name}, "
            f"session={self.session_id[:8]}..., "
            f"age={self.get_age_seconds():.0f}s)"
        )


# Utility functions for context validation and parsing

def validate_context_data(data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """
    Validate request data contains proper context structure.
    
    Args:
        data: Request data dictionary
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if 'context' not in data:
        return False, "Missing 'context' object in request"
    
    context = data['context']
    
    # Check for user
    if 'user' not in context:
        return False, "Missing 'user' object in context"
    
    user = context['user']
    required_user_fields = ['luid', 'username', 'displayName']
    missing_user = [f for f in required_user_fields if not user.get(f)]
    if missing_user:
        return False, f"Missing user fields: {missing_user}"
    
    # Check for dashboard context
    required_context_fields = ['workbook_id', 'dashboard_name', 'session_id']
    missing_context = [f for f in required_context_fields if not context.get(f)]
    if missing_context:
        return False, f"Missing context fields: {missing_context}"
    
    # Validate session_id format
    try:
        uuid.UUID(context['session_id'])
    except ValueError:
        return False, f"Invalid session_id format: {context.get('session_id')}"
    
    return True, None


def create_context_from_legacy_connection_key(
    connection_key: str,
    workbook_name: Optional[str] = None,
    dashboard_name: Optional[str] = None
) -> RequestContext:
    """
    Bridge function: Create RequestContext from legacy connection_key.
    
    This enables gradual migration from old system to new.
    Should only be used during transition period.
    
    Args:
        connection_key: Legacy connection key string
        workbook_name: Optional workbook name
        dashboard_name: Optional dashboard name
        
    Returns:
        RequestContext with anonymous user and generated session ID
    """
    logger.warning(
        f"Creating context from legacy connection_key: {connection_key}. "
        "This is a compatibility shim - migrate to proper context system."
    )
    
    # Create anonymous user from connection key hash
    hash_obj = hashlib.md5(connection_key.encode())
    fingerprint = hash_obj.hexdigest()[:16]
    
    user = UserIdentity.create_anonymous(fingerprint)
    
    # Use connection_key as workbook_id if no better option
    workbook_id = workbook_name or connection_key
    dashboard = dashboard_name or "Unknown Dashboard"
    
    # Generate new session ID
    session_id = str(uuid.uuid4())
    
    return RequestContext(
        user=user,
        workbook_id=workbook_id,
        workbook_name=workbook_name or workbook_id,
        dashboard_name=dashboard,
        session_id=session_id
    )

