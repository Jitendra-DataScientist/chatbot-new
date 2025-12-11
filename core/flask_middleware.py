"""
Flask Middleware for Request Context Management

Provides Flask before_request hooks to:
1. Validate incoming request context
2. Create RequestContext from request data
3. Attach context to Flask 'g' object for route access
4. Log context information for debugging

Author: Enterprise Architecture Refactor
Date: December 2024
"""

from flask import g, request, jsonify
from functools import wraps
from typing import Optional

from core.request_context import RequestContext, validate_context_data, create_context_from_legacy_connection_key
from master_logger import setup_module_logger

logger = setup_module_logger('core.flask_middleware')


def inject_request_context_middleware(app):
    """
    Register Flask before_request hook for context injection.
    
    This middleware:
    1. Checks if request needs context validation (POST to /api/*)
    2. Validates context structure
    3. Creates RequestContext object
    4. Attaches to g.request_context for route access
    
    Args:
        app: Flask application instance
    """
    
    @app.before_request
    def inject_context():
        """
        Before request hook to inject RequestContext.
        
        Returns:
            None if successful, error JSON response if validation fails
        """
        # Only process POST requests to /api/* endpoints
        if request.method != 'POST' or not request.path.startswith('/api/'):
            return None
        
        # Skip context validation for specific endpoints
        skip_endpoints = [
            '/api/user/log_frontend_data',  # User data logging
            '/api/extension/log',  # Extension logging
            '/api/health',  # Health check
        ]
        
        if request.path in skip_endpoints:
            logger.debug(f"Skipping context validation for: {request.path}")
            return None
        
        try:
            # Get request data
            data = request.get_json(silent=True) or {}
            
            # Check if context is provided
            if 'context' not in data:
                # Try to handle legacy requests with connection_key or tableauContext
                legacy_indicators = [
                    'connection_key' in data,
                    'tableauContext' in data,
                    request.args.get('connection_key') is not None
                ]
                
                if any(legacy_indicators):
                    logger.warning(
                        f"Legacy request format detected for {request.path} - "
                        "migration to RequestContext recommended"
                    )
                    # Allow legacy requests to pass through for gradual migration
                    g.request_context = None  # Mark as legacy
                    return None
                
                # No context and not legacy - validation error
                logger.error(f"Missing 'context' in request to {request.path}")
                return jsonify({
                    'error': 'Missing request context',
                    'message': 'All API requests must include user and session context. Please refresh the page to reinitialize your session.',
                    'code': 'MISSING_CONTEXT'
                }), 400
            
            # Validate context structure
            is_valid, error_message = validate_context_data(data)
            
            if not is_valid:
                logger.error(f"Invalid context in request to {request.path}: {error_message}")
                return jsonify({
                    'error': 'Invalid request context',
                    'message': error_message,
                    'code': 'INVALID_CONTEXT',
                    'help': 'Please refresh the page to reinitialize your session.'
                }), 400
            
            # Create RequestContext
            try:
                context = RequestContext.from_request(data)
                g.request_context = context
                
                logger.debug(
                    f"✅ Context validated - "
                    f"User: {context.user.username}, "
                    f"Dashboard: {context.dashboard_name}, "
                    f"Session: {context.session_id[:8]}..., "
                    f"Path: {request.path}"
                )
                
                return None  # Success - continue to route
                
            except Exception as e:
                logger.error(f"Failed to create RequestContext: {e}")
                return jsonify({
                    'error': 'Failed to parse request context',
                    'message': str(e),
                    'code': 'CONTEXT_PARSE_ERROR'
                }), 400
                
        except Exception as e:
            logger.error(f"Context middleware error: {e}", exc_info=True)
            # Don't block request on middleware error (fail open)
            g.request_context = None
            return None


def require_context(f):
    """
    Decorator to require RequestContext in routes.
    
    Usage:
        @app.post("/api/chat")
        @require_context
        def chat_api():
            context = g.request_context  # Guaranteed to exist
            ...
    
    Args:
        f: Flask route function
        
    Returns:
        Wrapped function that checks for context
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not hasattr(g, 'request_context') or g.request_context is None:
            logger.error(f"Route {request.path} requires context but none found")
            return jsonify({
                'error': 'Request context required',
                'message': 'This endpoint requires a valid session context. Please refresh the page.',
                'code': 'CONTEXT_REQUIRED'
            }), 400
        
        return f(*args, **kwargs)
    
    return decorated_function


def get_request_context() -> Optional[RequestContext]:
    """
    Get RequestContext from Flask g object.
    
    Safe accessor that returns None if context not available.
    
    Returns:
        RequestContext if available, None otherwise
    """
    return getattr(g, 'request_context', None)


def context_to_response_metadata(context: RequestContext) -> dict:
    """
    Extract metadata from context for API responses.
    
    Useful for including context info in responses for debugging.
    
    Args:
        context: RequestContext
        
    Returns:
        Dictionary with safe metadata
    """
    return {
        'session_id': context.session_id,
        'user_id': context.user.primary_id,
        'dashboard_name': context.dashboard_name,
        'session_age_seconds': int(context.get_age_seconds())
    }

