"""
App Integration Module - Flask Integration for New Architecture

Provides initialization functions and routes for integrating the new
hierarchical context system into app.py.

Functions:
- initialize_new_managers(): Set up all new managers
- register_new_routes(): Add new API endpoints
- migrate_legacy_state(): Helper for gradual migration

Author: Enterprise Architecture Refactor
Date: December 2024
"""

import os
import json
from flask import Flask, jsonify, request, g
from datetime import datetime
from typing import Optional, Dict, Any

from core.request_context import RequestContext
from core.hierarchical_state_manager import HierarchicalStateManager
from core.scoped_data_manager import ScopedDataManager, get_scoped_data_manager
from core.scoped_cache_manager import ScopedCacheManager, get_scoped_cache_manager
from core.session_lifecycle_manager import SessionLifecycleManager
from core.flask_middleware import inject_request_context_middleware, require_context, get_request_context
from master_logger import setup_module_logger

logger = setup_module_logger('core.app_integration')


# Global manager instances
_hierarchical_state_manager: Optional[HierarchicalStateManager] = None
_scoped_data_manager: Optional[ScopedDataManager] = None
_scoped_cache_manager: Optional[ScopedCacheManager] = None
_lifecycle_manager: Optional[SessionLifecycleManager] = None


def initialize_new_managers() -> Dict[str, Any]:
    """
    Initialize all new architecture managers.
    
    Call this early in app.py initialization, after logger setup.
    
    Returns:
        Dictionary containing all initialized managers
    """
    global _hierarchical_state_manager, _scoped_data_manager, _scoped_cache_manager, _lifecycle_manager
    
    logger.info("=" * 80)
    logger.info("INITIALIZING NEW ARCHITECTURE MANAGERS")
    logger.info("=" * 80)
    
    try:
        # Initialize state manager
        _hierarchical_state_manager = HierarchicalStateManager()
        logger.info("✅ HierarchicalStateManager initialized")
        
        # Initialize data manager
        _scoped_data_manager = get_scoped_data_manager()
        logger.info("✅ ScopedDataManager initialized")
        
        # Initialize cache manager
        _scoped_cache_manager = get_scoped_cache_manager()
        logger.info("✅ ScopedCacheManager initialized")
        
        # Initialize lifecycle manager
        _lifecycle_manager = SessionLifecycleManager(
            state_manager=_hierarchical_state_manager,
            data_manager=_scoped_data_manager,
            cache_manager=_scoped_cache_manager,
            cleanup_interval_minutes=30,
            session_ttl_minutes=120
        )
        logger.info("✅ SessionLifecycleManager initialized")
        
        # Start background cleanup
        _lifecycle_manager.start()
        logger.info("✅ Background cleanup thread started")
        
        logger.info("=" * 80)
        logger.info("NEW ARCHITECTURE MANAGERS READY")
        logger.info("=" * 80)
        
        return {
            'state_manager': _hierarchical_state_manager,
            'data_manager': _scoped_data_manager,
            'cache_manager': _scoped_cache_manager,
            'lifecycle_manager': _lifecycle_manager
        }
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize new managers: {e}", exc_info=True)
        raise


def get_managers() -> Dict[str, Any]:
    """
    Get initialized managers.
    
    Returns:
        Dictionary of manager instances
    """
    return {
        'state_manager': _hierarchical_state_manager,
        'data_manager': _scoped_data_manager,
        'cache_manager': _scoped_cache_manager,
        'lifecycle_manager': _lifecycle_manager
    }


def register_new_routes(app: Flask):
    """
    Register new API routes for the new architecture.
    
    Args:
        app: Flask application instance
    """
    logger.info("Registering new API routes...")
    
    # Register middleware
    inject_request_context_middleware(app)
    logger.info("✅ Context middleware registered")
    
    # User API routes
    register_user_routes(app)
    
    # Health and monitoring routes
    register_monitoring_routes(app)
    
    logger.info("✅ New API routes registered")


def register_user_routes(app: Flask):
    """Register user-related API routes"""
    
    @app.route("/api/user/get_current_user", methods=["GET"])
    def get_current_user():
        """
        Retrieve current user from recent user_frontend_data.json entry.
        
        This is a bridge API for session initialization until full Tableau
        auth integration. The frontend calls this on startup to get user info.
        
        Returns:
            JSON with user data or error
        """
        try:
            user_data_file = os.path.join(os.getcwd(), 'user_frontend_data.json')
            
            if not os.path.exists(user_data_file):
                return jsonify({
                    'success': False,
                    'error': 'No user data available',
                    'message': 'User tracking file not found'
                }), 404
            
            # Load user data file
            with open(user_data_file, 'r', encoding='utf-8') as f:
                all_users = json.load(f)
            
            if not all_users:
                return jsonify({
                    'success': False,
                    'error': 'No user data found'
                }), 404
            
            # Get most recent entry
            latest_entry = all_users[-1]
            
            # Try to extract user from tableau_session_data
            user_info = None
            
            # Check for tableau_session_data structure
            if 'tableau_session_data' in latest_entry:
                session_data = latest_entry['tableau_session_data']
                if 'user' in session_data:
                    user = session_data['user']
                    user_info = {
                        'luid': user.get('luid'),
                        'username': user.get('username'),
                        'displayName': user.get('displayName'),
                        'systemUserId': user.get('systemUserId'),
                        'domainName': user.get('domainName', 'local')
                    }
            
            # Fallback: try to find user in storage_data
            if not user_info and 'storage_data' in latest_entry:
                storage = latest_entry['storage_data']
                if 'local_storage' in storage:
                    # Try to parse from localStorage items
                    # (implementation depends on structure)
                    pass
            
            if user_info:
                return jsonify({
                    'success': True,
                    'user': user_info,
                    'timestamp': latest_entry.get('extraction_timestamp', datetime.utcnow().isoformat())
                })
            else:
                return jsonify({
                    'success': False,
                    'error': 'Could not extract user information',
                    'message': 'User data structure not recognized'
                }), 404
                
        except Exception as e:
            logger.error(f"Error getting current user: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route("/api/user/session_info", methods=["POST"])
    @require_context
    def get_session_info():
        """
        Get detailed session information.
        
        Requires context in request body.
        
        Returns:
            JSON with session details
        """
        context = get_request_context()
        
        if not context:
            return jsonify({'error': 'Context required'}), 400
        
        managers = get_managers()
        state_manager = managers['state_manager']
        
        # Get state if exists
        state = state_manager.get_state(context)
        
        return jsonify({
            'success': True,
            'session': {
                'session_id': context.session_id,
                'user': {
                    'luid': context.user.primary_id,
                    'username': context.user.username,
                    'display_name': context.user.display_name,
                    'is_anonymous': context.user.is_anonymous()
                },
                'dashboard': {
                    'workbook_id': context.workbook_id,
                    'workbook_name': context.workbook_name,
                    'dashboard_name': context.dashboard_name
                },
                'timing': {
                    'created_at': context.created_at.isoformat(),
                    'last_activity': context.last_activity.isoformat(),
                    'age_seconds': int(context.get_age_seconds()),
                    'idle_seconds': int(context.get_idle_seconds())
                },
                'state_exists': state is not None
            }
        })


def register_monitoring_routes(app: Flask):
    """Register health and monitoring routes"""
    
    @app.route("/api/health/managers", methods=["GET"])
    def health_check_managers():
        """
        Health check endpoint for new managers.
        
        Returns comprehensive health status.
        
        Returns:
            JSON with health metrics
        """
        try:
            managers = get_managers()
            lifecycle_manager = managers['lifecycle_manager']
            
            if not lifecycle_manager:
                return jsonify({
                    'error': 'Lifecycle manager not initialized'
                }), 500
            
            health_status = lifecycle_manager.get_health_status()
            
            return jsonify({
                'success': True,
                'timestamp': datetime.utcnow().isoformat(),
                'health': health_status
            })
            
        except Exception as e:
            logger.error(f"Health check failed: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route("/api/health/cleanup_history", methods=["GET"])
    def get_cleanup_history():
        """
        Get recent cleanup cycle history.
        
        Query params:
            - last_n: Number of recent cycles to return (default: 10)
        
        Returns:
            JSON with cleanup history
        """
        try:
            last_n = int(request.args.get('last_n', 10))
            
            managers = get_managers()
            lifecycle_manager = managers['lifecycle_manager']
            
            if not lifecycle_manager:
                return jsonify({
                    'error': 'Lifecycle manager not initialized'
                }), 500
            
            history = lifecycle_manager.get_cleanup_history(last_n=last_n)
            
            return jsonify({
                'success': True,
                'cleanup_history': history
            })
            
        except Exception as e:
            logger.error(f"Failed to get cleanup history: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route("/api/admin/force_cleanup", methods=["POST"])
    def force_cleanup():
        """
        Force immediate cleanup cycle (admin endpoint).
        
        Returns:
            JSON with cleanup statistics
        """
        try:
            managers = get_managers()
            lifecycle_manager = managers['lifecycle_manager']
            
            if not lifecycle_manager:
                return jsonify({
                    'error': 'Lifecycle manager not initialized'
                }), 500
            
            stats = lifecycle_manager.force_cleanup()
            
            return jsonify({
                'success': True,
                'message': 'Cleanup completed',
                'statistics': {
                    'sessions_expired': stats.sessions_expired,
                    'states_removed': stats.states_removed,
                    'dataframes_removed': stats.dataframes_removed,
                    'cache_entries_removed': stats.cache_entries_removed,
                    'duration_seconds': round(stats.duration_seconds, 2)
                }
            })
            
        except Exception as e:
            logger.error(f"Force cleanup failed: {e}", exc_info=True)
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500


def cleanup_on_shutdown():
    """
    Cleanup function to call on app shutdown.
    
    Stops background threads gracefully.
    """
    logger.info("Shutting down new architecture managers...")
    
    if _lifecycle_manager:
        _lifecycle_manager.stop()
        logger.info("✅ Lifecycle manager stopped")
    
    logger.info("✅ Shutdown complete")



