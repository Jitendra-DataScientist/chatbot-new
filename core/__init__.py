"""
Core Module - Enterprise Architecture Components

This module contains the new multi-tenant architecture for the chatbot application.

Components:
- request_context: Immutable context objects (UserIdentity, RequestContext)
- hierarchical_state_manager: Three-tier state isolation
- scoped_data_manager: Session-scoped DataFrame storage
- scoped_cache_manager: Unified cache system
- session_lifecycle_manager: Cleanup orchestration
- agent_context_adapter: Adapters for existing agents
- flask_middleware: Context validation middleware
- app_integration: Flask integration helpers

Usage:
    from core.app_integration import initialize_new_managers, register_new_routes
    
    # In app.py
    new_managers = initialize_new_managers()
    register_new_routes(app)

Version: 1.0
Date: December 2024
"""

__version__ = "1.0.0"
__author__ = "Enterprise Architecture Team"

# Convenience imports for easy access
from core.request_context import UserIdentity, RequestContext
from core.hierarchical_state_manager import HierarchicalStateManager
from core.scoped_data_manager import ScopedDataManager, get_scoped_data_manager
from core.scoped_cache_manager import ScopedCacheManager, get_scoped_cache_manager
from core.session_lifecycle_manager import SessionLifecycleManager
from core.app_integration import initialize_new_managers, register_new_routes, get_managers

__all__ = [
    # Classes
    'UserIdentity',
    'RequestContext',
    'HierarchicalStateManager',
    'ScopedDataManager',
    'ScopedCacheManager',
    'SessionLifecycleManager',
    
    # Functions
    'get_scoped_data_manager',
    'get_scoped_cache_manager',
    'initialize_new_managers',
    'register_new_routes',
    'get_managers',
]



