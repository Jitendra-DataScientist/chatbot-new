from flask import Flask, render_template, request, jsonify, session
from datetime import datetime, timedelta
from pathlib import Path
import argparse
import os
import sys
import json
import threading
import time
import logging
import traceback
import numpy as np
import asyncio
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Disable proxy for corporate network compatibility - Fix for connection issues
os.environ['NO_PROXY'] = '*'
for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    os.environ.pop(key, None)

# Import master logger for comprehensive logging
from master_logger import get_master_logger, function_logger, log_js_message, setup_module_logger

# Import Chrome extension logger
from chrome_extension_logger import chrome_extension_logger

# Import the tableau logic from enhanced tableau_backend with parsing capabilities
from tableau_backend import (
    TableauConnectionManager, 
    TableauDataProcessor, 
    ChatStateManager,
    initialize_tableau_connection,
    initialize_tableau_connection_with_metadata,
    detect_specific_anomalies,
    run_lang_graph_analysis,
    EnhancedWorkbookDataFetcher,
    # NEW: TWB parsing and metadata extraction capabilities
    TWBParser,
    DatasourceExtractor,
    WorkbookDataExporter,
    CompleteWorkbookDataManager,
    # Metadata classes
    ChartMetadata,
    FieldMetadata,
    DatasourceMetadata,
    FilterMetadata,
    BlendRelationship
)

# Import CSV data loader for analytics assistant
from services.csv_data_loader import CSVDataLoader

# Enhanced services availability flag (imports will be done later after debug_log is defined)
ENHANCED_SERVICES_AVAILABLE = False

# ============================================================================
# AUTO-EXPORT CONFIGURATION
# ============================================================================

# Enable/disable auto-export functionality on dashboard load
AUTO_EXPORT_ENABLED = os.getenv('AUTO_EXPORT_ENABLED', 'true').lower() == 'true'
AUTO_EXPORT_CSV = os.getenv('AUTO_EXPORT_CSV', 'true').lower() == 'true'
AUTO_EXPORT_METADATA = os.getenv('AUTO_EXPORT_METADATA', 'true').lower() == 'true'

app = Flask(__name__)

# Manual CORS implementation for Chrome extension support
@app.after_request
def after_request(response):
    """Add CORS headers to all responses"""
    origin = request.headers.get('Origin')
    
    # Allow Chrome extensions and localhost
    if (origin and (
        origin.startswith('chrome-extension://') or 
        origin.startswith('http://localhost:') or
        origin.startswith('https://tableau-aws.uberinternal.com') or
        origin.startswith('https://tableau.uberinternal.com')
    )):
        response.headers['Access-Control-Allow-Origin'] = origin
    
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Tableau-Auth'
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    return response

@app.before_request
def handle_preflight():
    """Handle CORS preflight requests"""
    if request.method == "OPTIONS":
        origin = request.headers.get('Origin')
        response = jsonify({'status': 'ok'})
        response.status_code = 200
        
        # Add CORS headers directly to preflight response
        if (origin and (
            origin.startswith('chrome-extension://') or 
            origin.startswith('http://localhost:') or
            origin.startswith('https://tableau-aws.uberinternal.com') or
            origin.startswith('https://tableau.uberinternal.com')
        )):
            response.headers['Access-Control-Allow-Origin'] = origin
        
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Tableau-Auth'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        return response

# Configure logging for debugging
logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('chatbot_debug.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

# Setup master logger for comprehensive debugging
master_logger = setup_module_logger('app')
master_logger.info("APP MODULE INITIALIZATION STARTED")
master_logger.info(f"Python version: {sys.version}")
master_logger.info(f"Working directory: {os.getcwd()}")
master_logger.info(f"Script path: {os.path.abspath(__file__)}")

# Debug flag - set to True for extensive logging
DEBUG_MODE = True
master_logger.info(f"Debug mode enabled: {DEBUG_MODE}")

def debug_log(message, data=None):
    """Enhanced debug logging function"""
    # Log to master logger in addition to existing logging
    master_logger.debug(f"DEBUG_LOG: {message}", extra={'data': data} if data else {})
    
    if DEBUG_MODE:
        if data:
            logger.debug(f"[DEBUG] {message}: {data}")
        else:
            logger.debug(f"[DEBUG] {message}")

# Import enhanced services now that debug_log is defined
try:
    from meta_agents.enhanced_query_agent import EnhancedQueryAgent
    from meta_agents.query_understanding_agent import QueryAgent
    from models.schemas import EnhancedChatRequest, EnhancedChatResponse
    ENHANCED_SERVICES_AVAILABLE = True
    debug_log("Enhanced services imported successfully")
except ImportError as e:
    debug_log(f"Enhanced services not available: {e}")
    ENHANCED_SERVICES_AVAILABLE = False

# OpenAI Configuration
master_logger.info("OPENAI CONFIGURATION STARTED")
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

# CRITICAL: Set as environment variable so imported modules can use it
if OPENAI_API_KEY and 'OPENAI_API_KEY' not in os.environ:
    os.environ['OPENAI_API_KEY'] = OPENAI_API_KEY
    master_logger.info("Set OPENAI_API_KEY environment variable for imported modules")

master_logger.info(f"OpenAI API key configured: {bool(OPENAI_API_KEY)}")
if OPENAI_API_KEY:
    master_logger.info(f"OpenAI API key length: {len(OPENAI_API_KEY)} characters")
    master_logger.info(f"OpenAI API key prefix: {OPENAI_API_KEY[:20]}...")
else:
    master_logger.warning("No OpenAI API key found in environment variables")

debug_log("Initializing OpenAI client", {"api_key_present": bool(OPENAI_API_KEY)})

# Initialize OpenAI client with SSL fix
master_logger.info("INITIALIZING OPENAI CLIENT")
from openai import OpenAI
import ssl
import certifi
import httpx

# SSL configuration options
DISABLE_SSL_VERIFY = os.environ.get('DISABLE_OPENAI_SSL_VERIFY', 'false').lower() == 'true'

master_logger.info(f"[CONFIG] Environment variable DISABLE_OPENAI_SSL_VERIFY = '{os.environ.get('DISABLE_OPENAI_SSL_VERIFY', 'NOT_SET')}'")
master_logger.info(f"[CONFIG] DISABLE_SSL_VERIFY evaluated to: {DISABLE_SSL_VERIFY}")

# TEMPORARY FIX: Force disable SSL verification for corporate firewall
# TODO: Remove this after SSL certificate issues are resolved
DISABLE_SSL_VERIFY = True
master_logger.warning("[CONFIG] FORCING SSL verification to be disabled for debugging (TEMPORARY)")

# Check environment variable FIRST to avoid unnecessary SSL failures
if DISABLE_SSL_VERIFY:
    master_logger.warning("[WARNING] SSL verification disabled via environment variable (INSECURE)")
    try:
        http_client = httpx.Client(verify=False, timeout=300.0)
        openai_client = OpenAI(
            api_key=OPENAI_API_KEY,
            http_client=http_client
        ) if OPENAI_API_KEY else None
        if openai_client:
            master_logger.info("[SUCCESS] OpenAI client initialized successfully with SSL verification DISABLED")
        else:
            master_logger.warning("OpenAI client not initialized - no API key provided")
    except Exception as e:
        master_logger.error(f"Failed to initialize OpenAI client even with SSL disabled: {e}")
        openai_client = None
else:
    # Use secure SSL methods
    try:
        # Method 1: Use certifi certificate bundle (recommended)
        http_client = httpx.Client(
            verify=certifi.where(),  # Use certifi certificate bundle
            timeout=300.0
        )
        
        openai_client = OpenAI(
            api_key=OPENAI_API_KEY,
            http_client=http_client
        ) if OPENAI_API_KEY else None
        
        if openai_client:
            master_logger.info("OpenAI client initialized successfully with SSL fix (certifi)")
        else:
            master_logger.warning("OpenAI client not initialized - no API key provided")
    except Exception as e:
        master_logger.error(f"SSL fix method failed: {e}")
        
        # Method 2: Fallback to default client
        try:
            openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
            if openai_client:
                master_logger.info("OpenAI client initialized with default SSL settings")
        except Exception as e2:
            master_logger.error(f"All SSL methods failed: {e2}")
            master_logger.error("Set DISABLE_OPENAI_SSL_VERIFY=true to disable SSL verification (NOT RECOMMENDED)")
            openai_client = None

def cleanup_expired_auth_tokens():
    """
    Clean expired authentication tokens on application startup
    
    This function performs maintenance on the persistent authentication cache
    by removing expired tokens and optimizing storage space.
    """
    try:
        from auth_storage import AuthTokenStorage
        
        master_logger.info("[INIT] STARTUP: Cleaning expired authentication tokens")
        
        # Initialize auth storage (this will auto-clean expired tokens)
        auth_storage = AuthTokenStorage("auth_cache.pickle", cache_timeout_minutes=450)
        
        # Get initial cache stats
        initial_stats = auth_storage.get_cache_stats()
        initial_total = initial_stats.get('total_entries', 0)
        initial_valid = initial_stats.get('valid_entries', 0)
        initial_expired = initial_stats.get('expired_entries', 0)
        
        master_logger.info(f"[STATS] AUTH_STARTUP_STATS: Found {initial_total} cached tokens ({initial_valid} valid, {initial_expired} expired)")
        
        # Load cache (automatically cleans expired tokens)
        cache = auth_storage.load_auth_cache()
        
        # Get post-cleanup stats
        final_stats = auth_storage.get_cache_stats()
        final_total = final_stats.get('total_entries', 0)
        final_valid = final_stats.get('valid_entries', 0)
        
        cleaned_count = initial_total - final_total
        
        if cleaned_count > 0:
            master_logger.info(f"[OK] AUTH_CLEANUP_SUCCESS: Removed {cleaned_count} expired tokens, {final_valid} active tokens remain")
        else:
            master_logger.info(f"[OK] AUTH_CLEANUP_SUCCESS: No expired tokens found, {final_valid} active tokens remain")
        
        # Log cache file status
        cache_size = final_stats.get('cache_file_size', 0)
        master_logger.info(f"[FILE] AUTH_CACHE_STATUS: {auth_storage.cache_file} ({cache_size} bytes, timeout: {auth_storage.cache_timeout})")
        
        return final_valid, cleaned_count
        
    except Exception as e:
        master_logger.error(f"[ERR] AUTH_CLEANUP_ERROR: Failed to clean expired tokens: {e}")
        return 0, 0

# Global state managers (using workbook name as key instead of session)
master_logger.info("INITIALIZING GLOBAL STATE MANAGERS")
try:
    connection_manager = TableauConnectionManager()
    master_logger.info("TableauConnectionManager initialized")
    
    # Perform startup authentication cleanup
    cleanup_expired_auth_tokens()
    
    state_manager = ChatStateManager()
    master_logger.info("ChatStateManager initialized")
    
    # FIX: Create global SessionContextManager for disambiguation cache
    # Context Manager by Aniket 1/12/2025
    from meta_agents.query_understanding_agent import SessionContextManager
    global_session_manager = SessionContextManager()
    master_logger.info("Global SessionContextManager initialized for disambiguation cache")
    
    data_processor = TableauDataProcessor()
    master_logger.info("TableauDataProcessor initialized")
    
    # Initialize smart aggregation service if OpenAI client is available (global for access in routes)
    global smart_agg_decider
    smart_agg_decider = None
    
    if openai_client:
        try:
            from services.smart_aggregation_service import SmartAggregationDecider
            cache_file_path = os.path.join(os.getcwd(), 'smart_aggregation_cache.json')
            smart_agg_decider = SmartAggregationDecider(llm_client=openai_client, cache_file_path=cache_file_path)
            data_processor.set_smart_aggregation(smart_agg_decider)
            master_logger.info("SmartAggregationDecider initialized and integrated with data processor")
        except Exception as e:
            master_logger.warning(f"Failed to initialize smart aggregation service: {e}")
            master_logger.warning("Continuing without smart aggregation (will use pattern-based aggregation)")
    else:
        master_logger.warning("OpenAI client not available - smart aggregation will not be initialized")
    
    # Initialize CSV data loader for analytics assistant (workbook-aware)
    csv_data_loader = CSVDataLoader()
    master_logger.info("CSVDataLoader initialized (workbook-aware mode)")
    
    # CSV data will be loaded when workbook is connected
    global csv_summary_data
    csv_summary_data = None
    master_logger.info("CSV data will be loaded from tableau_exports when workbook is connected")
    
    # ============================================================================
    # EXPORT PROGRESS TRACKING (for frontend status polling)
    # ============================================================================
    global export_status_tracker
    export_status_tracker = {}  # Key: connection_key, Value: status dict
    master_logger.info("Export status tracker initialized")
    
    # Initialize enhanced services
    if ENHANCED_SERVICES_AVAILABLE:
        try:
            enhanced_query_agent = EnhancedQueryAgent(openai_client=openai_client)
            enhanced_data_fetcher = EnhancedWorkbookDataFetcher(openai_client=openai_client, openai_api_key=OPENAI_API_KEY)
            
            # Initialize QueryAgent as global singleton (eliminates 4-second per-query overhead)
            query_agent = QueryAgent(openai_client, {
                'agent': {
                    'llm_model_name': 'gpt-4o-mini',
                    'llm_temperature': 0.1,
                    'agent_requirements': {
                        'shap_analysis': ['data_processor', 'insight_generator'],
                        'anomaly_detection': ['data_processor', 'visualization'],
                        'trend_analysis': ['data_processor', 'visualization'],
                        'statistical_significance': ['data_processor'],
                        'comparison': ['multi_table', 'data_processor'],
                        'top_bottom_analysis': ['data_processor'],
                        'seasonality': ['data_processor', 'visualization'],
                        'prediction': ['data_processor', 'insight_generator'],
                        'data_exploration': ['data_processor', 'visualization']
                    }
                }
            }, logger)
            master_logger.info("Enhanced services initialized successfully")
            
            # Set smart aggregation on enhanced query agent if available
            if smart_agg_decider is not None:
                try:
                    enhanced_query_agent.set_smart_aggregation(smart_agg_decider)
                    query_agent.set_smart_aggregation(smart_agg_decider)
                    master_logger.info("Smart aggregation set on enhanced query agent and query agent")
                except Exception as e:
                    master_logger.warning(f"Failed to set smart aggregation on enhanced services: {e}")
        except Exception as e:
            master_logger.error(f"Failed to initialize enhanced services: {e}")
            enhanced_query_agent = None
            enhanced_data_fetcher = None
            query_agent = None
    else:
        enhanced_query_agent = None
        enhanced_data_fetcher = None
        query_agent = None
        master_logger.warning("Enhanced services not available")
    
    master_logger.info("All global state managers initialized successfully")
except Exception as e:
    master_logger.error(f"Failed to initialize global state managers: {e}")
    raise

debug_log("Initialized global state managers")

# Connection refresh interval (30 minutes)
CONNECTION_REFRESH_INTERVAL = 30 * 60
master_logger.info(f"Connection refresh interval set to {CONNECTION_REFRESH_INTERVAL} seconds")

# ============================================================================
# EXPORT STATUS HELPER FUNCTIONS
# ============================================================================

def get_export_status(connection_key: str) -> dict:
    """
    Get export status for a specific connection.
    
    Args:
        connection_key: Unique identifier for the connection
        
    Returns:
        dict: Current export status with progress information
    """
    global export_status_tracker
    return export_status_tracker.get(connection_key, {
        'in_progress': False,
        'stage': 'idle',
        'message': '',
        'worksheets_processed': 0,
        'total_worksheets': 0,
        'datasources_processed': 0,
        'total_datasources': 0,
        'current_item': ''
    })

def update_export_status(connection_key: str, **updates):
    """
    Update export status for a connection.
    Safe to call - wraps in try/except to prevent breaking export on status update failure.
    
    Args:
        connection_key: Unique identifier for the connection
        **updates: Status fields to update (stage, message, worksheets_processed, etc.)
    """
    global export_status_tracker
    
    try:
        if connection_key not in export_status_tracker:
            export_status_tracker[connection_key] = get_export_status(connection_key)
        
        export_status_tracker[connection_key].update(updates)
        
        # Log progress (truncate connection key for readability)
        stage = updates.get('stage', '')
        message = updates.get('message', '')
        if stage or message:
            master_logger.info(f"Export status [{connection_key[:8]}...]: {stage} - {message}")
    except Exception as e:
        # Status update failure should not break export
        master_logger.warning(f"Failed to update export status (non-critical): {e}")

def reload_csv_data_for_workbook(workbook_name: str) -> bool:
    """
    Reload CSV data from tableau_exports for a specific workbook
    
    Args:
        workbook_name: Name of the workbook to load exported CSV data from
        
    Returns:
        bool: True if successful, False otherwise
    """
    global csv_data_loader, csv_summary_data
    
    try:
        master_logger.info(f"Reloading CSV data for workbook: {workbook_name}")
        
        # Load data from workbook exports
        success = csv_data_loader.load_data_from_workbook_exports(workbook_name)
        
        if success:
            # Update global CSV data
            csv_summary_data = csv_data_loader.get_data()
            master_logger.info(f"Successfully reloaded CSV data from tableau_exports/{workbook_name}")
            master_logger.info(f"New CSV data shape: {csv_summary_data.shape if csv_summary_data is not None else 'None'}")
            return True
        else:
            master_logger.warning(f"Failed to load CSV data for workbook: {workbook_name}")
            return False
            
    except Exception as e:
        master_logger.error(f"Error reloading CSV data for workbook {workbook_name}: {e}")
        return False

@function_logger('app.call_llm')
def call_llm(prompt, model="gpt-3.5-turbo", temperature=0):
    """Helper function to call OpenAI LLM with debugging"""
    master_logger.info(f"LLM call initiated - model: {model}, temperature: {temperature}")
    master_logger.debug(f"LLM prompt preview: {prompt[:200]}..." if len(prompt) > 200 else f"LLM prompt: {prompt}")
    
    debug_log("LLM call initiated", {"model": model, "prompt_length": len(prompt)})
    
    if not openai_client:
        master_logger.warning("LLM call attempted but no OpenAI client available")
        debug_log("LLM call failed - no API key configured")
        return "AI analysis unavailable - OpenAI API key not configured"
    
    try:
        start_time = time.time()
        master_logger.debug("Calling OpenAI chat completions API")
        
        response = openai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a helpful data analysis assistant for Tableau dashboards."},
                {"role": "user", "content": prompt}
            ],
            temperature=temperature
        )
        
        api_call_time = time.time() - start_time
        result = response.choices[0].message.content
        
        master_logger.info(f"LLM call successful - response length: {len(result)}, API call time: {api_call_time:.3f}s")
        master_logger.debug(f"LLM response preview: {result[:300]}..." if len(result) > 300 else f"LLM response: {result}")
        
        # Log usage statistics if available
        if hasattr(response, 'usage') and response.usage:
            master_logger.info(f"LLM token usage - prompt: {response.usage.prompt_tokens}, completion: {response.usage.completion_tokens}, total: {response.usage.total_tokens}")
        
        debug_log("LLM call successful", {"response_length": len(result)})
        return result
        
    except Exception as e:
        master_logger.error(f"LLM call failed with exception: {type(e).__name__}: {str(e)}")
        debug_log("LLM call failed", {"error": str(e)})
        return f"AI Error: {str(e)}"

@app.route("/")
@function_logger('app.routes.index')
def index():
    """Render the sticky chatbot UI"""
    master_logger.info(f"Index page requested from {request.remote_addr}")
    master_logger.debug(f"Request headers: {dict(request.headers)}")
    
    debug_log("Index page requested")
    
    try:
        result = render_template("index.html")
        master_logger.info("Index page rendered successfully")
        return result
    except Exception as e:
        master_logger.error(f"Failed to render index page: {e}")
        raise

@app.route("/api/analytics/initialize", methods=["POST"])
@function_logger('app.routes.initialize_analytics')
def initialize_analytics():
    """Initialize analytics assistant with workbook data"""
    master_logger.info("=== ANALYTICS INITIALIZATION ENDPOINT CALLED ===")
    master_logger.info(f"Request from: {request.remote_addr}")
    master_logger.info(f"User-Agent: {request.headers.get('User-Agent', 'Unknown')}")
    master_logger.info(f"Content-Type: {request.content_type}")
    
    debug_log("Analytics initialization endpoint called")
    
    try:
        data = request.get_json(silent=True) or {}
        master_logger.info(f"Request data received - size: {len(str(data))} characters")
        master_logger.debug(f"Full request data: {json.dumps(data, indent=2, default=str)}")
        
        debug_log("Received analytics initialization request", data)
        
        # Check if data loader is available and load data
        if not csv_data_loader:
            master_logger.error("Data loader not initialized")
            return jsonify({
                "success": False,
                "error": "Data loader not available"
            }), 500
        
        # Load workbook data
        success = csv_data_loader.load_data()
        if not success:
            master_logger.error("Failed to load workbook data")
            return jsonify({
                "success": False,
                "error": "Failed to load workbook data"
            }), 400
        
        # Generate summary
        line1, line2 = csv_data_loader.generate_summary_lines()
        summary_stats = csv_data_loader.get_summary_stats()
        
        master_logger.info(f"Workbook data loaded successfully - {summary_stats.get('total_rows', 0)} rows, {summary_stats.get('total_columns', 0)} columns")
        
        # Create a connection key for the workbook
        connection_key = f"workbook_{summary_stats.get('file_name', 'data')}"
        
        # Analytics endpoint should not create its own state - redirect to Tableau initialization
        master_logger.info("Analytics endpoint called - redirecting to proper Tableau initialization")
        
        # Just return the CSV summary data for now
        response_data = {
            "success": True,
            "analytics_summary": {
                "summary_line1": line1,
                "summary_line2": line2
            },
            "redirect_to_tableau": True,
            "message": "Analytics summary generated. Please use Tableau initialization for full functionality."
        }
        
        debug_log("Analytics summary generated", {
            "rows": summary_stats.get('total_rows', 0),
            "columns": summary_stats.get('total_columns', 0)
        })
        
        master_logger.info(f"Returning successful analytics response: {json.dumps(response_data, default=str)}")
        return jsonify(response_data)
        
    except Exception as e:
        master_logger.error(f"EXCEPTION in analytics initialization: {type(e).__name__}: {str(e)}")
        master_logger.error(f"Full traceback: {traceback.format_exc()}")
        
        debug_log("Analytics initialization exception", {"error": str(e), "type": type(e).__name__})
        
        error_response = {
            "success": False,
            "error": f"Analytics initialization failed: {str(e)}"
        }
        
        master_logger.error(f"Returning exception response: {json.dumps(error_response)}")
        return jsonify(error_response), 500

@app.route("/api/tableau/initialize", methods=["POST"])
@function_logger('app.routes.initialize_tableau')
def initialize_tableau():
    """Initialize Tableau connection with dashboard context - Enhanced with debugging"""
    master_logger.info("=== TABLEAU INITIALIZATION ENDPOINT CALLED ===")
    master_logger.info(f"Request from: {request.remote_addr}")
    master_logger.info(f"User-Agent: {request.headers.get('User-Agent', 'Unknown')}")
    master_logger.info(f"Content-Type: {request.content_type}")
    
    debug_log("Tableau initialization endpoint called")
    
    try:
        data = request.get_json(silent=True) or {}
        master_logger.info(f"Request data received - size: {len(str(data))} characters")
        master_logger.debug(f"Full request data: {json.dumps(data, indent=2, default=str)}")
        
        debug_log("Received initialization request", data)
        
        # Get Tableau context from the request
        tableau_context = data.get("tableauContext", {})
        dashboard_name = tableau_context.get("dashboardName")
        workbook_name = tableau_context.get("workbookName") 
        workbook_id = tableau_context.get("workbookId") or tableau_context.get("workbook_id")
        view_content_url = tableau_context.get("viewContentUrl") or tableau_context.get("view_content_url")
        site_content_url = tableau_context.get("siteContentUrl") or tableau_context.get("site_content_url")
        worksheet_names = tableau_context.get("worksheetNames", [])
        url = tableau_context.get("url")
        
        master_logger.info("Extracting Tableau context from request")
        master_logger.info(f"Dashboard name: {dashboard_name}")
        master_logger.info(f"Workbook name: {workbook_name}")
        master_logger.info(f"Workbook ID: {workbook_id}")
        master_logger.info(f"View contentUrl: {view_content_url}")
        master_logger.info(f"Site contentUrl: {site_content_url}")
        master_logger.info(f"Worksheet count: {len(worksheet_names)}")
        master_logger.info(f"Source URL: {url}")
        master_logger.debug(f"Worksheet names: {worksheet_names}")
        master_logger.debug(f"Full tableau context: {json.dumps(tableau_context, indent=2, default=str)}")
        
        debug_log("Extracted Tableau context", {
            "dashboard_name": dashboard_name,
            "workbook_name": workbook_name,
            "worksheet_count": len(worksheet_names),
            "worksheets": worksheet_names,
            "url": url
        })
        
        # Prefer workbook_id as the stable connection key when available
        connection_key = workbook_id or workbook_name or dashboard_name or "default_workbook"
        if not connection_key:
            connection_key = f"workbook_{hash(str(tableau_context))}"
        
        master_logger.info(f"Generated connection key: '{connection_key}'")
        master_logger.debug(f"Connection key generation logic - workbook: {workbook_name}, dashboard: {dashboard_name}")
        
        debug_log("Generated connection key", {"connection_key": connection_key})
        
        # Check if we already have a valid connection for this workbook
        master_logger.info(f"Checking for existing valid connection for key: {connection_key}")
        has_valid_connection = state_manager.has_valid_connection(connection_key)
        master_logger.info(f"Valid connection exists: {has_valid_connection}")
        
        if has_valid_connection:
            master_logger.info("Using existing cached connection")
            debug_log("Found existing valid connection", {"connection_key": connection_key})
            
            state = state_manager.get_state(connection_key)
            master_logger.info(f"Retrieved cached state - views: {len(state.available_views)}, workbook: {state.workbook_name}")
            
            # Generate workbook summary using CSV data for cached connection
            workbook_summary = None
            try:
                # Use CSV data for summary but keep Tableau charts
                if csv_data_loader:
                    debug_log("Generating CSV-based workbook summary for cached connection")
                    csv_success = csv_data_loader.load_data()
                    if csv_success:
                        line1, line2 = csv_data_loader.generate_summary_lines()
                        workbook_summary = {
                            'summary_line1': line1,
                            'summary_line2': line2
                        }
                        debug_log("CSV-based workbook summary generated for cached connection", workbook_summary)
                    else:
                        debug_log("CSV data loading failed for cached connection, using fallback")
                        workbook_summary = {
                            'summary_line1': f"📊 Connected to workbook: {state.workbook_name} (cached)",
                            'summary_line2': f"🔍 Found {len(state.available_views)} worksheets ready for analysis"
                        }
                else:
                    # Fallback to enhanced data fetcher
                    if enhanced_data_fetcher:
                        debug_log("Generating workbook summary for cached connection")
                        workbook_data = asyncio.run(enhanced_data_fetcher.fetch_workbook_data(state))
                        workbook_summary = workbook_data.get('workbook_summary')
                        debug_log("Workbook summary generated for cached connection", workbook_summary)
            except Exception as e:
                master_logger.warning(f"Could not generate workbook summary for cached connection: {e}")
                debug_log("Failed to generate workbook summary for cached connection", {"error": str(e)})
                # Fallback summary
                workbook_summary = {
                    'summary_line1': f"📊 Connected to workbook: {state.workbook_name} (cached)",
                    'summary_line2': f"🔍 Found {len(state.available_views)} worksheets ready for analysis"
                }
            
            response_data = {
                "success": True,
                "cached": True,
                "connection_key": connection_key,  # Include connection_key in response
                "workbook_name": state.workbook_name,
                "dashboard_name": state.dashboard_name,
                "available_views": len(state.available_views),
                "connection_age": state.get_connection_age(),
                "workbook_summary": workbook_summary
            }
            
            master_logger.info(f"Returning cached connection response: {json.dumps(response_data, default=str)}")
            return jsonify(response_data)
        
        master_logger.info("Initializing NEW Tableau connection")
        debug_log("Initializing new Tableau connection")
        
        # Initialize new connection
        master_logger.debug("Calling initialize_tableau_connection with dashboard context")
        success, state_or_error = initialize_tableau_connection(
            dashboard_context={
                "dashboard_name": dashboard_name,
                "workbook_name": workbook_name,
                "worksheet_names": worksheet_names,
                "url": url
            }
        )
        
        master_logger.info(f"Tableau connection initialization result: success={success}")
        
        if success:
            master_logger.info("Tableau connection SUCCESSFUL")
            master_logger.info(f"Connected workbook: {state_or_error.workbook_name}")
            master_logger.info(f"Available views: {len(state_or_error.available_views)}")
            master_logger.info(f"Raw data available: {state_or_error.raw_data is not None}")
            
            debug_log("Tableau connection successful", {
                "workbook_name": state_or_error.workbook_name,
                "views_count": len(state_or_error.available_views),
                "has_raw_data": state_or_error.raw_data is not None
            })
            
            # Store the connection state using workbook name as key
            master_logger.info(f"Storing connection state with key: {connection_key}")
            state_manager.store_state(connection_key, state_or_error)
            
            # ============================================================================
            # AUTO-EXPORT FUNCTIONALITY
            # ============================================================================
            
            # Trigger automatic export if enabled
            if AUTO_EXPORT_ENABLED and workbook_name:
                # Get workbook_id from connection state if not provided in request
                actual_workbook_id = workbook_id or getattr(state_or_error, 'workbook_id', None)
                
                master_logger.info("=== TRIGGERING AUTO-EXPORT ===")
                master_logger.info(f"Auto-export enabled: {AUTO_EXPORT_ENABLED}")
                master_logger.info(f"Auto-export CSV: {AUTO_EXPORT_CSV}")
                master_logger.info(f"Auto-export metadata: {AUTO_EXPORT_METADATA}")
                master_logger.info(f"Workbook name: {workbook_name}")
                master_logger.info(f"Workbook ID (request): {workbook_id}")
                master_logger.info(f"Workbook ID (state): {getattr(state_or_error, 'workbook_id', None)}")
                master_logger.info(f"Using workbook ID: {actual_workbook_id}")
                
                try:
                    # Use CompleteWorkbookDataManager for comprehensive export
                    complete_manager = CompleteWorkbookDataManager(connection_manager)
                    
                    # Run async export in the background (don't block initialization)
                    def run_auto_export():
                        # Initialize export status for this connection
                        update_export_status(
                            connection_key,
                            in_progress=True,
                            stage='starting',
                            message='Preparing to download dashboard data...'
                        )
                        
                        try:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            
                            # If we have workbook_id, do full export including TWB download
                            if actual_workbook_id:
                                master_logger.info(f"Running full export with workbook_id: {actual_workbook_id}")
                                
                                # Update status before starting export
                                update_export_status(
                                    connection_key,
                                    stage='downloading',
                                    message='Downloading workbook file and metadata...'
                                )
                                
                                # Create progress callback for the export
                                def progress_callback(update):
                                    """Forward progress updates to export status tracker"""
                                    update_export_status(connection_key, **update)
                                
                                export_result = loop.run_until_complete(
                                    complete_manager.fetch_complete_workbook_data(
                                        workbook_name=workbook_name,
                                        workbook_id=actual_workbook_id,
                                        site_id=state_or_error.site_id,
                                        auth_token=state_or_error.auth_token,
                                        export_to_csv=AUTO_EXPORT_CSV,
                                        progress_callback=progress_callback
                                    )
                                )
                            else:
                                # Fallback: Create basic export using available worksheet data
                                master_logger.info("Running basic export (no workbook_id available)")
                                try:
                                    if AUTO_EXPORT_CSV and enhanced_data_fetcher:
                                        # Get worksheet data and export to CSV
                                        workbook_data = loop.run_until_complete(
                                            enhanced_data_fetcher.fetch_workbook_data(state_or_error)
                                        )
                                        
                                        if workbook_data.get('success') and 'worksheets_data' in workbook_data:
                                            # Use WorkbookDataExporter for CSV export only
                                            exporter = WorkbookDataExporter()
                                            export_result = exporter.export_all_to_csv(
                                                workbook_name=workbook_name,
                                                worksheets_data=workbook_data['worksheets_data'],
                                                datasources_data={}  # No datasource data available in this mode
                                            )
                                            export_result['mode'] = 'basic_csv_only'
                                        else:
                                            master_logger.warning("Could not fetch workbook data for basic export")
                                            export_result = {'success': False, 'error': 'No workbook data available'}
                                    else:
                                        master_logger.info("CSV export disabled or enhanced services not available")
                                        export_result = {'success': True, 'mode': 'metadata_only'}
                                        
                                    # Create metadata export if possible (requires existing metadata)
                                    if AUTO_EXPORT_METADATA:
                                        try:
                                            # Try to export any existing metadata
                                            metadata_path = f"twb_metadata_{workbook_name}.json"
                                            if os.path.exists(metadata_path):
                                                master_logger.info(f"Metadata file already exists: {metadata_path}")
                                                export_result['metadata_exported'] = True
                                            else:
                                                master_logger.info("No existing metadata to export")
                                                export_result['metadata_exported'] = False
                                        except Exception as e:
                                            master_logger.warning(f"Metadata export failed: {e}")
                                            export_result['metadata_exported'] = False
                                            
                                except Exception as e:
                                    master_logger.error(f"Basic export failed: {e}")
                                    export_result = {'success': False, 'error': f'Basic export failed: {str(e)}', 'mode': 'failed'}
                            
                            if export_result.get('success'):
                                export_mode = export_result.get('mode', 'full')
                                master_logger.info(f"✅ Auto-export completed successfully (mode: {export_mode})")
                                
                                if export_mode == 'full':
                                    master_logger.info(f"Metadata exported: {AUTO_EXPORT_METADATA}")
                                    if 'metadata_path' in export_result:
                                        metadata_dir = os.path.dirname(export_result['metadata_path'])
                                        master_logger.info(f"Metadata folder created: {metadata_dir}")
                                        master_logger.info(f"Metadata file: {export_result['metadata_path']}")
                                    if AUTO_EXPORT_CSV and 'csv_export' in export_result:
                                        csv_info = export_result['csv_export']
                                        master_logger.info(f"CSV export completed: {csv_info.get('output_dir', 'Unknown location')}")
                                        master_logger.info(f"Worksheets exported: {csv_info.get('worksheets_exported', 0)}")
                                        master_logger.info(f"Datasources exported: {csv_info.get('datasources_exported', 0)}")
                                elif export_mode == 'basic_csv_only':
                                    master_logger.info(f"Basic CSV export completed: {export_result.get('output_dir', 'Unknown location')}")
                                    master_logger.info(f"Worksheets exported: {export_result.get('worksheets_exported', 0)}")
                                    master_logger.info(f"Total rows exported: {export_result.get('total_worksheet_rows', 0)}")
                                else:
                                    master_logger.info(f"Export mode '{export_mode}' completed")
                                
                                # Reload CSV data from the newly exported files
                                try:
                                    csv_reload_success = reload_csv_data_for_workbook(workbook_name)
                                    if csv_reload_success:
                                        master_logger.info(f"✅ CSV data reloaded from tableau_exports/{workbook_name}")
                                    else:
                                        master_logger.warning(f"⚠️ Failed to reload CSV data for workbook: {workbook_name}")
                                except Exception as csv_reload_error:
                                    master_logger.error(f"❌ Error reloading CSV data: {csv_reload_error}")
                                
                                # Mark export as complete (preserve existing counts from backend)
                                current_status = get_export_status(connection_key)
                                update_export_status(
                                    connection_key,
                                    in_progress=False,
                                    stage='complete',
                                    message='Dashboard data ready! You can now ask questions.',
                                    worksheets_processed=current_status.get('worksheets_processed', 0),
                                    total_worksheets=current_status.get('total_worksheets', 0),
                                    datasources_processed=current_status.get('datasources_processed', 0),
                                    total_datasources=current_status.get('total_datasources', 0)
                                )
                            else:
                                master_logger.warning(f"Auto-export failed: {export_result.get('error', 'Unknown error')} (mode: {export_result.get('mode', 'unknown')})")
                                # Mark export as failed
                                update_export_status(
                                    connection_key,
                                    in_progress=False,
                                    stage='failed',
                                    message=f"Export failed: {export_result.get('error', 'Unknown error')}"
                                )
                                
                        except Exception as e:
                            master_logger.error(f"Auto-export exception: {e}")
                            master_logger.error(traceback.format_exc())
                            # Mark export as failed
                            update_export_status(
                                connection_key,
                                in_progress=False,
                                stage='failed',
                                message=f"Export error: {str(e)}"
                            )
                        finally:
                            loop.close()
                    
                    # Start export in background thread
                    export_thread = threading.Thread(target=run_auto_export, daemon=True)
                    export_thread.start()
                    
                    master_logger.info("Auto-export started in background thread")
                    debug_log("Auto-export triggered", {
                        "workbook_name": workbook_name,
                        "workbook_id_request": workbook_id,
                        "workbook_id_state": getattr(state_or_error, 'workbook_id', None),
                        "actual_workbook_id": actual_workbook_id,
                        "export_mode": "full" if actual_workbook_id else "basic",
                        "export_csv": AUTO_EXPORT_CSV,
                        "export_metadata": AUTO_EXPORT_METADATA
                    })
                    
                except Exception as e:
                    master_logger.error(f"Failed to start auto-export: {e}")
                    debug_log("Auto-export failed to start", {"error": str(e)})
            else:
                master_logger.info("Auto-export skipped (disabled or missing workbook name)")
                debug_log("Auto-export skipped", {
                    "enabled": AUTO_EXPORT_ENABLED,
                    "has_workbook_name": bool(workbook_name),
                    "workbook_id_from_request": workbook_id,
                    "workbook_id_from_state": getattr(state_or_error, 'workbook_id', None) if 'state_or_error' in locals() else None
                })
            
            # Generate workbook summary (lightweight, no CSV loading during init)
            # CSV data will be loaded by the export thread and reloaded automatically
            workbook_summary = None
            try:
                # Don't load CSV data here - export thread will handle it
                # This keeps initialization fast and non-blocking
                debug_log("Generating lightweight summary for immediate response")
                workbook_summary = {
                    'summary_line1': f"📊 Connected to workbook: {state_or_error.workbook_name}",
                    'summary_line2': f"🔍 Found {len(state_or_error.available_views)} worksheets available"
                }
                
                # If auto-export is running, add a note
                if AUTO_EXPORT_ENABLED and workbook_name:
                    workbook_summary['summary_line2'] += " (loading data in background...)"
                    
                debug_log("Lightweight summary generated for immediate response", workbook_summary)
                
            except Exception as e:
                master_logger.warning(f"Could not generate workbook summary for new connection: {e}")
                debug_log("Failed to generate workbook summary for new connection", {"error": str(e)})
                # Fallback summary
                workbook_summary = {
                    'summary_line1': f"📊 Connected to workbook: {state_or_error.workbook_name}",
                    'summary_line2': f"🔍 Found {len(state_or_error.available_views)} worksheets ready for analysis"
                }
            
            response_data = {
                "success": True,
                "cached": False,
                "connection_key": connection_key,
                "workbook_name": state_or_error.workbook_name,
                "dashboard_name": state_or_error.dashboard_name,
                "available_views": len(state_or_error.available_views),
                "raw_data_available": state_or_error.raw_data is not None,
                "workbook_summary": workbook_summary
            }
            
            master_logger.info(f"Returning successful connection response: {json.dumps(response_data, default=str)}")
            return jsonify(response_data)
            
        else:
            master_logger.error(f"Tableau connection FAILED: {str(state_or_error)}")
            debug_log("Tableau connection failed", {"error": str(state_or_error)})
            
            error_response = {
                "success": False,
                "error": str(state_or_error)
            }
            
            master_logger.error(f"Returning error response: {json.dumps(error_response)}")
            return jsonify(error_response), 400
            
    except Exception as e:
        master_logger.error(f"EXCEPTION in Tableau initialization: {type(e).__name__}: {str(e)}")
        master_logger.error(f"Full traceback: {traceback.format_exc()}")
        
        debug_log("Tableau initialization exception", {"error": str(e), "type": type(e).__name__})
        
        error_response = {
            "success": False,
            "error": f"Initialization failed: {str(e)}"
        }
        
        master_logger.error(f"Returning exception response: {json.dumps(error_response)}")
        return jsonify(error_response), 500

@app.route("/api/get_worksheets", methods=["GET"])
@function_logger('app.routes.get_worksheets')
def get_worksheets():
    """Get available worksheets/charts for the current connection"""
    master_logger.info("=== GET WORKSHEETS ENDPOINT CALLED ===")
    master_logger.info(f"Request from: {request.remote_addr}")
    
    debug_log("Get worksheets endpoint called")
    
    try:
        # Get connection key from query params or try to determine from context
        connection_key = request.args.get('connection_key', 'default_workbook')
        
        master_logger.info(f"Connection key from request: '{connection_key}'")
        master_logger.debug(f"All query parameters: {dict(request.args)}")
        
        debug_log("Getting worksheets for connection", {"connection_key": connection_key})
        
        # Get current state - try exact match first
        current_state = state_manager.get_state(connection_key)
        
        # If not found, try to find a matching state by iterating through all states
        if not current_state:
            debug_log("Exact match not found, searching all states")
            if hasattr(state_manager, '_states'):
                debug_log("Available connection keys", {"keys": list(state_manager._states.keys())})
                
                # Try to find by workbook name match
                for key, state in state_manager._states.items():
                    if (state.workbook_name == connection_key or 
                        key == connection_key or
                        state.workbook_name and connection_key in state.workbook_name):
                        debug_log("Found matching state", {"stored_key": key, "workbook_name": state.workbook_name})
                        current_state = state
                        break
        
        if not current_state:
            debug_log("No current state found", {"connection_key": connection_key})
            # Return more helpful error with available keys
            available_keys = []
            if hasattr(state_manager, '_states'):
                available_keys = [f"{key} (workbook: {state.workbook_name})" 
                                for key, state in state_manager._states.items()]
            
            return jsonify({
                "success": False,
                "error": f"No active Tableau connection found for '{connection_key}'. Available connections: {available_keys}"
            }), 400
        
        # Extract worksheet information
        worksheets = []
        for view in current_state.available_views:
            worksheet_info = {
                "id": view.get('id'),
                "name": view.get('name'),
                "contentUrl": view.get('contentUrl'),
                "type": "worksheet"  # Default type, could be enhanced
            }
            worksheets.append(worksheet_info)
        
        debug_log("Worksheets retrieved successfully", {
            "count": len(worksheets),
            "worksheets": [w["name"] for w in worksheets]
        })
        
        return jsonify({
            "success": True,
            "worksheets": worksheets,
            "workbook_name": current_state.workbook_name,
            "count": len(worksheets)
        })
        
    except Exception as e:
        debug_log("Get worksheets exception", {"error": str(e)})
        return jsonify({
            "success": False,
            "error": f"Failed to get worksheets: {str(e)}"
        }), 500

@app.route("/api/get_workbook_summary", methods=["GET"])
@function_logger('app.routes.get_workbook_summary')
def get_workbook_summary():
    """Get or generate workbook data summary with column statistics and LLM description"""
    master_logger.info("=== GET WORKBOOK SUMMARY ENDPOINT CALLED ===")
    
    try:
        # Get workbook name from query params
        workbook_name = request.args.get('workbook')
        
        if not workbook_name:
            return jsonify({
                "success": False,
                "error": "Workbook name is required"
            }), 400
        
        master_logger.info(f"Getting summary for workbook: {workbook_name}")
        
        # Check if CSV data is loaded
        if csv_data_loader.data is None:
            return jsonify({
                "success": False,
                "error": "CSV data not loaded. Please connect to workbook first."
            }), 400
        
        # Get the DataFrame
        df = csv_data_loader.data
        
        # Find metadata file
        # Normalize workbook name (remove spaces, replace with underscores for folder lookup)
        project_root = Path(__file__).parent
        workbook_name_normalized = workbook_name.replace(' ', '')  # Remove spaces for folder name
        metadata_path = project_root / "tableau_metadata" / workbook_name_normalized / f"metadata_{workbook_name_normalized}.json"
        
        if not metadata_path.exists():
            master_logger.warning(f"Metadata file not found: {metadata_path}")
            metadata_path_str = None
        else:
            metadata_path_str = str(metadata_path)
        
        # Initialize summarizer
        from services.workbook_data_summarizer import WorkbookDataSummarizer
        summarizer = WorkbookDataSummarizer()
        
        # Get or generate summary
        summary = summarizer.get_summary(
            workbook_name=workbook_name,
            df=df,
            metadata_path=metadata_path_str,
            llm_client=openai_client
        )
        
        master_logger.info(f"Summary retrieved successfully for {workbook_name}")
        
        return jsonify({
            "success": True,
            "workbook_name": workbook_name,
            "summary": summary
        })
        
    except Exception as e:
        master_logger.error(f"Error getting workbook summary: {e}")
        master_logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": f"Failed to get workbook summary: {str(e)}"
        }), 500

@app.route("/api/get_field_descriptions", methods=["GET"])
@function_logger('app.routes.get_field_descriptions')
def get_field_descriptions():
    """Get field descriptions/comments from tableau_descriptions folder"""
    master_logger.info("=== GET FIELD DESCRIPTIONS ENDPOINT CALLED ===")
    
    try:
        # Get workbook name from query params
        workbook_name = request.args.get('workbook')
        
        if not workbook_name:
            return jsonify({
                "success": False,
                "error": "Workbook name is required"
            }), 400
        
        master_logger.info(f"Getting field descriptions for workbook: {workbook_name}")
        
        # Look for field descriptions file
        project_root = Path(__file__).parent
        descriptions_dir = project_root / "tableau_descriptions" 
        descriptions_path = descriptions_dir / workbook_name / f"field_descriptions_{workbook_name}.json"
        
        if not descriptions_path.exists():
            master_logger.warning(f"Field descriptions file not found: {descriptions_path}")
            
            # Try alternative workbook name formats
            alternatives = [
                workbook_name.replace(" ", "_"),
                workbook_name.replace("_", " "),
                workbook_name.replace(" ", ""),
                workbook_name.replace("Dashboard", "Dashboard_final")
            ]
            
            found_path = None
            for alt_name in alternatives:
                alt_path = descriptions_dir / alt_name / f"field_descriptions_{alt_name}.json"
                if alt_path.exists():
                    found_path = alt_path
                    master_logger.info(f"Found descriptions using alternative name: {alt_name}")
                    break
            
            if not found_path:
                master_logger.warning(f"No field descriptions found for workbook: {workbook_name}")
                return jsonify({
                    "success": True,
                    "workbook_name": workbook_name,
                    "field_descriptions": {},
                    "total_descriptions": 0,
                    "message": "No field descriptions found. Field descriptions are generated when processing TWBX files with comments."
                })
            else:
                descriptions_path = found_path
        
        # Read field descriptions
        try:
            with open(descriptions_path, 'r', encoding='utf-8') as f:
                descriptions_data = json.load(f)
            
            master_logger.info(f"Found {descriptions_data.get('total_fields_with_descriptions', 0)} field descriptions")
            
            # Transform array to object format expected by frontend
            field_descriptions = {}
            for field in descriptions_data.get('field_descriptions', []):
                field_name = field.get('field_name')
                comment = field.get('comment')
                if field_name and comment:
                    field_descriptions[field_name] = {
                        'description': comment,
                        'field_name': field_name
                    }
            
            master_logger.info(f"Successfully loaded {len(field_descriptions)} field descriptions")
            
            return jsonify({
                "success": True,
                "workbook_name": workbook_name,
                "field_descriptions": field_descriptions,
                "total_descriptions": len(field_descriptions),
                "exported_at": descriptions_data.get('exported_at')
            })
            
        except json.JSONDecodeError as e:
            master_logger.error(f"Error parsing field descriptions JSON: {e}")
            return jsonify({
                "success": False,
                "error": "Invalid field descriptions file format"
            }), 500
            
    except Exception as e:
        master_logger.error(f"Error getting field descriptions: {e}")
        master_logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": f"Failed to get field descriptions: {str(e)}"
        }), 500

@app.route("/api/export-status", methods=["GET"])
@function_logger('app.routes.get_export_status')
def get_export_status_api():
    """
    Get current export progress for frontend polling.
    
    Query parameters:
        connection_key (str): Unique identifier for the connection
        
    Returns:
        JSON with export status including:
        - in_progress (bool): Whether export is currently running
        - stage (str): Current stage (idle, downloading, parsing, etc.)
        - message (str): Human-readable progress message
        - worksheets_processed (int): Number of worksheets fetched
        - total_worksheets (int): Total worksheets to fetch
        - datasources_processed (int): Number of datasources processed
        - total_datasources (int): Total datasources to process
        - current_item (str): Name of current item being processed
    """
    try:
        connection_key = request.args.get('connection_key', '')
        
        if not connection_key:
            return jsonify({
                'error': 'connection_key parameter required'
            }), 400
        
        status = get_export_status(connection_key)
        return jsonify(status)
        
    except Exception as e:
        master_logger.error(f"Error getting export status: {e}")
        return jsonify({
            'error': f'Failed to get export status: {str(e)}',
            'in_progress': False,
            'stage': 'error'
        }), 500

@app.route("/api/tableau/refresh", methods=["POST"])
def refresh_tableau_connection():
    """Force refresh the Tableau connection"""
    debug_log("Tableau refresh endpoint called")
    
    try:
        data = request.get_json(silent=True) or {}
        connection_key = data.get("connection_key", "default_workbook")
        
        debug_log("Refresh request", {"connection_key": connection_key})
        
        if not connection_key:
            debug_log("Refresh failed - no connection key")
            return jsonify({"success": False, "error": "No connection key provided"}), 400
        
        # Clear the existing state
        debug_log("Clearing existing state", {"connection_key": connection_key})
        state_manager.clear_state(connection_key)
        
        # Re-initialize
        debug_log("Re-initializing connection")
        return initialize_tableau()
        
    except Exception as e:
        debug_log("Refresh exception", {"error": str(e)})
        return jsonify({
            "success": False,
            "error": f"Refresh failed: {str(e)}"
        }), 500

@app.route("/api/check-cache", methods=["POST"])
@function_logger('app.routes.check_cache')
def check_cache():
    """Check if cache exists for a specific chart"""
    try:
        data = request.get_json(silent=True) or {}
        chart_name = data.get('chart_name')
        workbook_id = data.get('workbook_id')
        
        if not chart_name:
            return jsonify({
                "success": False,
                "error": "No chart name provided"
            }), 400
        
        cache_path = "causal_analysis_cache.json"
        cache_exists = False
        
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'r') as f:
                    cache = json.load(f)
                    # Nested: cache[workbook_id][chart_name]
                    if workbook_id and isinstance(cache, dict) and workbook_id in cache:
                        cache_exists = chart_name in cache.get(workbook_id, {})
                    else:
                        # Fallback: scan all workbooks for chart
                        cache_exists = any(
                            isinstance(charts, dict) and chart_name in charts
                            for charts in (cache.values() if isinstance(cache, dict) else [])
                        )
                    debug_log("Cache check completed", {
                        "chart_name": chart_name,
                        "workbook_id": workbook_id,
                        "cache_exists": cache_exists
                    })
            except Exception as e:
                debug_log("Error reading cache file", {"error": str(e)})
                cache_exists = False
        else:
            debug_log("Cache file does not exist", {"cache_path": cache_path})
        
        return jsonify({
            "success": True,
            "cache_exists": cache_exists,
            "chart_name": chart_name
        })
        
    except Exception as e:
        debug_log("Cache check exception", {"error": str(e)})
        return jsonify({
            "success": False,
            "error": f"Failed to check cache: {str(e)}"
        }), 500

# ============================================================================
# NEW: TWB PARSING AND METADATA ENDPOINTS
# ============================================================================

@app.route("/api/tableau/parse", methods=["POST"])
@function_logger('app.routes.parse_tableau_workbook')
def parse_tableau_workbook():
    """Parse TWB/TWBX file and extract metadata"""
    master_logger.info("=== TWB PARSING ENDPOINT CALLED ===")
    
    try:
        data = request.get_json(silent=True) or {}
        workbook_name = data.get("workbook_name")
        workbook_id = data.get("workbook_id")
        connection_key = data.get("connection_key", "default_workbook")
        
        debug_log("Parse workbook request", {
            "workbook_name": workbook_name,
            "workbook_id": workbook_id,
            "connection_key": connection_key
        })
        
        if not workbook_name:
            return jsonify({
                "success": False,
                "error": "Workbook name is required"
            }), 400
        
        # Get current state for authentication
        current_state = state_manager.get_state(connection_key)
        if not current_state:
            return jsonify({
                "success": False,
                "error": "No active Tableau connection found. Please initialize connection first."
            }), 400
        
        # Initialize complete workbook data manager
        complete_manager = CompleteWorkbookDataManager(connection_manager)
        
        # Fetch complete workbook data with parsing
        complete_data = asyncio.run(complete_manager.fetch_complete_workbook_data(
            workbook_name=workbook_name,
            workbook_id=workbook_id or current_state.workbook_id,
            site_id=current_state.site_id,
            auth_token=current_state.auth_token,
            export_to_csv=data.get("export_to_csv", False)
        ))
        
        master_logger.info(f"Workbook parsing completed: {complete_data.get('success', False)}")
        
        return jsonify({
            "success": complete_data.get('success', False),
            "workbook_name": workbook_name,
            "metadata": complete_data.get('metadata', {}),
            "charts_metadata": complete_data.get('charts_metadata', {}),
            "charts_metadata_readable": complete_data.get('charts_metadata_readable', {}),
            "datasources_extracted": len(complete_data.get('datasources_data', {})),
            "csv_export": complete_data.get('csv_export'),
            "error": complete_data.get('error')
        })
        
    except Exception as e:
        master_logger.error(f"Error in TWB parsing endpoint: {e}")
        return jsonify({
            "success": False,
            "error": f"Parsing failed: {str(e)}"
        }), 500

@app.route("/api/tableau/metadata/<chart_name>", methods=["GET"])
@function_logger('app.routes.get_chart_metadata')
def get_chart_metadata(chart_name):
    """Get readable metadata for a specific chart"""
    master_logger.info(f"Chart metadata requested for: {chart_name}")
    
    try:
        connection_key = request.args.get('connection_key', 'default_workbook')
        
        # Get current state
        current_state = state_manager.get_state(connection_key)
        if not current_state:
            return jsonify({
                "success": False,
                "error": "No active Tableau connection found"
            }), 400
        
        # Initialize TWB parser and parse if needed
        twb_parser = TWBParser()
        
        # For now, return a message that parsing needs to be done first
        return jsonify({
            "success": False,
            "message": f"Please use /api/tableau/parse endpoint first to extract metadata for chart '{chart_name}'",
            "chart_name": chart_name,
            "available_charts": [view['name'] for view in current_state.available_views]
        })
        
    except Exception as e:
        master_logger.error(f"Error getting chart metadata: {e}")
        return jsonify({
            "success": False,
            "error": f"Failed to get metadata: {str(e)}"
        }), 500

@app.route("/api/tableau/export", methods=["POST"])
@function_logger('app.routes.export_tableau_data')
def export_tableau_data():
    """Export workbook and datasource data to CSV files"""
    master_logger.info("=== TABLEAU EXPORT ENDPOINT CALLED ===")
    
    try:
        data = request.get_json(silent=True) or {}
        workbook_name = data.get("workbook_name")
        connection_key = data.get("connection_key", "default_workbook")
        export_worksheets = data.get("export_worksheets", True)
        export_datasources = data.get("export_datasources", True)
        
        debug_log("Export request", {
            "workbook_name": workbook_name,
            "export_worksheets": export_worksheets,
            "export_datasources": export_datasources
        })
        
        if not workbook_name:
            return jsonify({
                "success": False,
                "error": "Workbook name is required"
            }), 400
        
        # Get current state
        current_state = state_manager.get_state(connection_key)
        if not current_state:
            return jsonify({
                "success": False,
                "error": "No active Tableau connection found"
            }), 400
        
        # Initialize exporter
        exporter = WorkbookDataExporter()
        
        # Collect worksheet data if requested
        worksheets_data = {}
        if export_worksheets:
            for view in current_state.available_views:
                try:
                    df = data_processor.get_view_data(
                        current_state.site_id,
                        view['id'],
                        current_state.auth_token
                    )
                    if df is not None and not df.empty:
                        worksheets_data[view['name']] = df
                except Exception as e:
                    master_logger.warning(f"Failed to export worksheet {view['name']}: {e}")
        
        # For datasources, we'd need parsed metadata - for now use empty dict
        datasources_data = {} if not export_datasources else {}
        
        # Export to CSV
        export_result = exporter.export_all_to_csv(
            workbook_name=workbook_name,
            worksheets_data=worksheets_data,
            datasources_data=datasources_data
        )
        
        master_logger.info(f"Export completed: {export_result.get('success', False)}")
        
        return jsonify(export_result)
        
    except Exception as e:
        master_logger.error(f"Error in export endpoint: {e}")
        return jsonify({
            "success": False,
            "error": f"Export failed: {str(e)}"
        }), 500

@app.route("/api/tableau/initialize-with-metadata", methods=["POST"])
@function_logger('app.routes.initialize_tableau_with_metadata')
def initialize_tableau_with_metadata():
    """Initialize Tableau connection with complete metadata extraction"""
    master_logger.info("=== TABLEAU INITIALIZATION WITH METADATA ENDPOINT CALLED ===")
    
    try:
        data = request.get_json(silent=True) or {}
        tableau_context = data.get("tableauContext", {})
        
        debug_log("Initialize with metadata request", tableau_context)
        
        # Extract context
        dashboard_name = tableau_context.get("dashboardName")
        workbook_name = tableau_context.get("workbookName") 
        workbook_id = tableau_context.get("workbookId")
        
        # Use enhanced initialization
        success, result = asyncio.run(initialize_tableau_connection_with_metadata(
            dashboard_context={
                "dashboard_name": dashboard_name,
                "workbook_name": workbook_name,
                "workbook_id": workbook_id,
                "url": tableau_context.get("url")
            }
        ))
        
        if success:
            # Store enhanced state
            connection_key = workbook_id or workbook_name or dashboard_name or "default_workbook"
            # Note: result is now a dict with enhanced metadata
            state_manager.store_state(connection_key, result)
            
            return jsonify({
                "success": True,
                "connection_key": connection_key,
                "workbook_name": result.get('workbook_name'),
                "has_metadata": result.get('has_complete_metadata', False),
                "charts_count": len(result.get('charts_metadata', {})),
                "datasources_count": len(result.get('datasources_data', {})),
                "csv_exported": 'csv_export' in result
            })
        else:
            return jsonify({
                "success": False,
                "error": str(result)
            }), 400
            
    except Exception as e:
        master_logger.error(f"Error in enhanced initialization: {e}")
        return jsonify({
            "success": False,
            "error": f"Enhanced initialization failed: {str(e)}"
        }), 500

@app.post("/api/chat")
def chat_api():
    """Enhanced chat API with agentic processing and chart selection support"""
    debug_log("Enhanced Chat API endpoint called")
    
    try:
        data = request.get_json(silent=True) or {}
        print("data")
        print (json.dumps(data, indent=4))
        msg = (data.get("message") or "").strip()
        tableau_context = data.get("context", {})
        tableau_ready = data.get("tableauReady", False)
        connection_key = data.get("connection_key")
        
        # NEW: Handle selected chart context
        selected_chart = data.get("selected_chart")
        chart_context = data.get("chart_context", {})
        
        # Enhanced Chrome extension logging for chat requests
        if data.get("source") == "chrome_extension":
            chrome_extension_logger.log_chat_request(
                message=msg,
                selected_chart=selected_chart,
                connection_key=connection_key,
                request_data={
                    "tableau_context": tableau_context,
                    "tableau_ready": tableau_ready,
                    "chart_context": chart_context,
                    "user_agent": request.headers.get('User-Agent'),
                    "remote_addr": request.environ.get('REMOTE_ADDR')
                }
            )
        
        debug_log("Chat request received", {
            "message_length": len(msg),
            "message_preview": msg[:50] + "..." if len(msg) > 50 else msg,
            "tableau_ready": tableau_ready,
            "selected_chart": selected_chart,
            "has_chart_context": bool(chart_context),
            "connection_key_provided": connection_key is not None,
            "connection_key_value": connection_key,
            "request_source": data.get("source")
        })
        
        if not msg:
            debug_log("Chat request rejected - empty message")
            return jsonify({"reply": "Please enter a message."})
        
        # Try to get connection key from context if not provided
        if not connection_key:
            workbook_name = tableau_context.get("workbookName")
            dashboard_name = tableau_context.get("dashboardName")
            connection_key = workbook_name or dashboard_name or "default_workbook"
            debug_log("Connection key derived from context", {
                "workbook_name": workbook_name,
                "dashboard_name": dashboard_name,
                "final_connection_key": connection_key
            })
        else:
            debug_log("Connection key provided in request", {"connection_key": connection_key})
        
        debug_log("Using connection key", {"connection_key": connection_key})
        
        # Get current state
        current_state = state_manager.get_state(connection_key)
        
        if not current_state:
            debug_log("No current state found", {"connection_key": connection_key})
            return jsonify({
                "reply": "Please initialize the Tableau connection first by providing dashboard context.",
                "requires_initialization": True
            })
        
        debug_log("Current state retrieved", {
            "workbook_name": current_state.workbook_name,
            "views_count": len(current_state.available_views),
            "has_raw_data": current_state.raw_data is not None
        })
        
        # NEW: Enhanced agentic processing
        if ENHANCED_SERVICES_AVAILABLE and enhanced_query_agent:
            debug_log("Using enhanced agentic processing")
            return handle_enhanced_query_processing_sync(msg, current_state, selected_chart, chart_context, data.get("source"), data.get('cache_only', False))
        
        # Handle AUTO_ANALYSIS even without enhanced services
        if msg == "AUTO_ANALYSIS" and selected_chart:
            debug_log("Processing AUTO_ANALYSIS request without enhanced services")
            return handle_basic_auto_analysis(current_state, selected_chart, chart_context, data.get("source"))
        
        # Fallback to original processing for backward compatibility
        debug_log("Using original processing (enhanced services not available)")
        
        # NEW: Handle chart-specific queries
        if selected_chart:
            debug_log("Processing chart-specific query")
            return handle_chart_specific_query(msg, current_state, selected_chart, chart_context)
        
        # Handle different types of queries
        if any(keyword in msg.lower() for keyword in ['spike', 'dip', 'anomaly', 'detect']):
            debug_log("Processing anomaly detection request")
            return handle_anomaly_detection(msg, current_state)
        elif any(keyword in msg.lower() for keyword in ['analyze', 'insight', 'why', 'cause']):
            debug_log("Processing analysis request")
            return handle_analysis_request(msg, current_state)
        elif any(keyword in msg.lower() for keyword in ['data', 'show', 'display', 'what']):
            debug_log("Processing data request")
            return handle_data_request(msg, current_state)
        elif any(keyword in msg.lower() for keyword in ['select', 'click', 'point', 'chart']):
            debug_log("Processing chart guidance request")
            return handle_chart_guidance(msg, current_state)
        else:
            debug_log("Processing general chat response")
            # General chat response with chart selection guidance
            base_reply = f"I can help you analyze your Tableau data from '{current_state.workbook_name}'. "
            base_reply += "Please select a chart first using the buttons above, then ask your question."
            
            return jsonify({"reply": base_reply})
    
    except Exception as e:
        debug_log("Chat API exception", {"error": str(e), "type": type(e).__name__})
        
        error_response = f"Error processing your request: {str(e)}"
        
        # Log error response for Chrome extension
        if data.get("source") == "chrome_extension":
            chrome_extension_logger.log_chat_response(
                response=error_response,
                success=False,
                error_details=str(e)
            )
        
        return jsonify({
            "reply": error_response,
            "error": True
        }), 500

async def handle_enhanced_query_processing(msg, state, selected_chart, chart_context, source, cache_only=False):
    """Handle query processing using enhanced agentic system"""
    # Initialize csv_data at function level to avoid scoping issues
    csv_data = None
    
    debug_log("=== ENHANCED QUERY PROCESSING ===")
    debug_log("Enhanced query processing started", {
        "message": msg,
        "selected_chart": selected_chart,
        "workbook": state.workbook_name,
        "source": source,
        "cache_only": cache_only  # Jitendra
    })
    
    try:
        # Step 1: Conditionally fetch workbook data based on query type (LAZY LOADING)
        # Analysis queries (with selected_chart): Fetch only the selected chart
        # Exploration queries (no selected_chart): Fetch metadata only (no chart data)
        
        if selected_chart:
            # ANALYSIS QUERY: Fetch only the selected chart (lazy loading)
            debug_log("Fetching single chart data (lazy loading)", {
                "selected_chart": selected_chart
            })
            workbook_data = await enhanced_data_fetcher.fetch_single_chart_data(state, selected_chart)
        else:
            # EXPLORATION QUERY: Fetch metadata only (no chart data needed)
            debug_log("Fetching workbook metadata only (exploration query)")
            workbook_data = await enhanced_data_fetcher.fetch_workbook_metadata(state)
        
        if not workbook_data or not workbook_data.get('success'):
            debug_log("Failed to fetch workbook data")
            return jsonify({
                "reply": "I'm having trouble accessing the workbook data. Please try refreshing the connection.",
                "error": True
            })
        
        # Step 1.5: Load CSV data early (needed for context creation and Tableau hints extraction)
        if csv_data_loader and csv_data_loader.load_data():
            csv_data = csv_data_loader.data
            debug_log("CSV data loaded early for context creation", {
                "csv_shape": csv_data.shape if hasattr(csv_data, 'shape') else 'not a DataFrame',
                "csv_columns": len(csv_data.columns) if hasattr(csv_data, 'columns') else 0,
                "csv_file_path": csv_data_loader.csv_file_path
            })
        else:
            debug_log("No CSV data available")
        
        # Step 1.6: Extract Tableau aggregation hints from selected chart
        if selected_chart and csv_data_loader and workbook_data.get('worksheets_data'):
            worksheets_data = workbook_data['worksheets_data']
            
            # Find the selected chart in worksheets_data
            chart_df = None
            for ws_name, ws_df in worksheets_data.items():
                if selected_chart in ws_name or ws_name in selected_chart:
                    chart_df = ws_df
                    break
            
            # Extract Tableau columns and map to CSV to store aggregation hints
            if chart_df is not None and hasattr(chart_df, 'columns'):
                tableau_columns = list(chart_df.columns)
                debug_log("Extracting Tableau aggregation hints", {
                    "chart": selected_chart,
                    "tableau_columns": tableau_columns
                })
                
                try:
                    # This call extracts aggregation hints and stores them in tableau_aggregation_hints.json
                    mapped_csv_columns = csv_data_loader.map_tableau_to_csv_columns(
                        tableau_columns, 
                        chart_name=selected_chart
                    )
                    debug_log("Tableau hints extracted and stored", {
                        "chart": selected_chart,
                        "mapped_columns": mapped_csv_columns
                    })
                except Exception as e:
                    debug_log("Error extracting Tableau hints", {
                        "error": str(e),
                        "chart": selected_chart
                    })
        
        # Step 1.7: Handle AUTO_ANALYSIS request (immediate chart analysis)
        if msg == "AUTO_ANALYSIS" and selected_chart:
            debug_log("Processing AUTO_ANALYSIS request")
            return await handle_auto_analysis_request(workbook_data, state, selected_chart, chart_context, source, cache_only)
        
        # Step 2: Create enhanced chat request
        # For exploration queries, use CSV columns; for analysis queries, use Tableau chart columns
        available_columns = []
        if selected_chart and workbook_data.get('worksheets_data'):
            # Analysis query: use Tableau chart columns
            available_columns = get_all_columns_from_workbook(workbook_data['worksheets_data'])
        elif csv_data_loader and csv_data_loader.data is not None:
            # Exploration query: use CSV columns
            available_columns = list(csv_data_loader.data.columns)
        
        # Get CSV file path for context (thread-safe)
        csv_file_path_for_context = csv_data_loader.csv_file_path if csv_data_loader else None
        master_logger.info(f"[CONTEXT DEBUG] csv_file_path being added to context: {csv_file_path_for_context}")
        
        # 🔍 DEBUG: Log state details before building context
        master_logger.info(f"🔍 DEBUG [app.py]: Building context from state:")
        master_logger.info(f"  - state.workbook_name: {state.workbook_name}")
        master_logger.info(f"  - state.workbook_id: {state.workbook_id}")
        master_logger.info(f"  - state type: {type(state)}")
        
        chat_request = EnhancedChatRequest(
            message=msg,
            context={
                "workbook_name": state.workbook_name,
                "workbook_id": state.workbook_id,  # FIX: Add workbook_id to context
                "available_columns": available_columns,
                "available_charts": [view['name'] for view in state.available_views],
                "csv_file_path": csv_file_path_for_context  # Thread-safe CSV path for cache
            },
            tableau_context={
                "workbook_id": state.workbook_id,
                "site_id": state.site_id,
                "connection_key": state.workbook_name
            },
            connection_key=state.workbook_name,
            selected_chart=selected_chart,
            chart_context=chart_context,
            source=source
        )
        
        # 🔍 DEBUG: Log chat_request.context after creation
        master_logger.info(f"🔍 DEBUG [app.py]: chat_request created:")
        master_logger.info(f"  - chat_request.context type: {type(chat_request.context)}")
        master_logger.info(f"  - chat_request.context: {chat_request.context}")
        if hasattr(chat_request.context, '__dict__'):
            master_logger.info(f"  - chat_request.context.__dict__: {chat_request.context.__dict__}")
        
        debug_log("Created enhanced chat request", {
            "has_selected_chart": selected_chart is not None,
            "available_worksheets_count": len(chat_request.context.available_worksheets) if chat_request.context else 0,
            "worksheets_count": len(workbook_data['worksheets_data'])
        })
        
        # Step 3: Route based on message type
        if msg == "AUTO_ANALYSIS":
            debug_log("Processing AUTO_ANALYSIS with enhanced query agent")
            response = await enhanced_query_agent.process_enhanced_query(
                chat_request, 
                workbook_data['worksheets_data']
            )
        else:
            debug_log("Processing regular user query with query understanding agent")
            
            # CSV data already loaded at Step 1.5 above - just verify it's available
            if csv_data is None:
                debug_log("Warning: CSV data was not loaded earlier, attempting to load now")
                if csv_data_loader and csv_data_loader.load_data():
                    csv_data = csv_data_loader.data
                    debug_log("CSV data loaded for query understanding agent (fallback)", {
                        "csv_shape": csv_data.shape if hasattr(csv_data, 'shape') else 'not a DataFrame',
                        "csv_columns": len(csv_data.columns) if hasattr(csv_data, 'columns') else 0
                    })
                else:
                    debug_log("No CSV data available for query understanding agent")
            
            # Use global QueryAgent singleton (initialized at startup)
            # No need to instantiate per-request - eliminates 4-second overhead
            if query_agent is None:
                debug_log("QueryAgent not initialized - enhanced services unavailable")
                return jsonify({
                    "reply": "Query processing service is not available. Please check server logs.",
                    "error": True
                })
            
            # Initialize conversation_state if not present in ChatState
            if not hasattr(state, 'conversation_state') or state.conversation_state is None:
                # Initialize with session_id from ChatState
                state.conversation_state = {
                    'history': [],
                    'context': {},
                    'session_id': state.session_id if hasattr(state, 'session_id') else f"session_{int(time.time())}"
                }
                debug_log("Initialized conversation_state in ChatState", {
                    "session_id": state.conversation_state['session_id']
                })
            
            # Process query with services integration
            # Pass workbook_name as source_id for data registration
            response = await query_agent.process_with_services(
                msg, 
                csv_data, 
                selected_chart,
                source_id=state.workbook_name or "default",
                session_id=state.session_id if hasattr(state, 'session_id') else None,
                context=chat_request.context,
                conversation_state=state.conversation_state  # 🆕 Pass conversation state
            )
            
            # Update conversation_state in ChatState from response
            if 'conversation_state' in response and response['conversation_state']:
                state.conversation_state = response['conversation_state']
                debug_log("Updated conversation_state in ChatState", {
                    "history_size": len(state.conversation_state.get('history', []))
                })
                # ChatStateManager will persist this automatically on next store_state call
        
        debug_log("Enhanced query agent response received", {
            "success": response.get("success", False),
            "has_visualization": response.get("visualization") is not None,
            "has_auto_analysis": response.get("auto_analysis") is not None,
            "execution_time": response.get("execution_time", 0)
        })
        
        # Step 4: Format response for frontend
        formatted_response = format_enhanced_response(response, workbook_data, state)
        
        # Step 5: Log response for Chrome extension
        if source == "chrome_extension":
            chrome_extension_logger.log_chat_response(
                response=formatted_response.get("reply", ""),
                success=response.get("success", False)
            )
        
        # DEBUG: Log formatted response size before jsonify
        if formatted_response.get("visualization") and formatted_response["visualization"].get("chart_image"):
            chart_img_len = len(formatted_response["visualization"]["chart_image"])
            debug_log(f"[VIZ DEBUG] Before jsonify - chart_image length: {chart_img_len} chars")
            debug_log(f"[VIZ DEBUG] Before jsonify - formatted_response keys: {list(formatted_response.keys())}")
        
        debug_log("Enhanced query processing completed successfully")
        return jsonify(formatted_response)
        
    # Context Manager by Aniket 3/12/2025
    # ============================================================================
    except Exception as e:
        # Check if this is a UserDisambiguationRequired exception
        # Import here to avoid circular dependency
        from services.context_manager import UserDisambiguationRequired
        
        if isinstance(e, UserDisambiguationRequired):
            master_logger.info("[CONTEXT_MGR] 🔘 User disambiguation required")
            master_logger.info(f"[CONTEXT_MGR] Message: {e.message}")
            master_logger.info(f"[CONTEXT_MGR] Suggestions: {e.suggestions}")
            master_logger.info(f"[CONTEXT_MGR] Context: {e.context}")
            
            # Add session and query info to context
            disambiguation_response = e.to_dict()
            disambiguation_response['context']['original_query'] = msg
            disambiguation_response['context']['session_id'] = state.session_id if hasattr(state, 'session_id') else f"session_{state.workbook_name}"
            disambiguation_response['context']['source_id'] = state.workbook_name or "default"
            
            debug_log("[CONTEXT_MGR] Returning disambiguation UI to user", {
                "suggestions_count": len(e.suggestions),
                "original_value": e.context.get('original_value')
            })
            
            # Return disambiguation response (not an error - expected flow)
            return jsonify(disambiguation_response), 200
        
        # Log full error details to master_debug.log
        master_logger.error(f"ERROR in enhanced query processing: {type(e).__name__}: {str(e)}")
        master_logger.error(f"Full traceback:\n{traceback.format_exc()}")
        debug_log("Error in enhanced query processing", {
            "error": str(e),
            "type": type(e).__name__,
            "traceback": traceback.format_exc()
        })
        
        # Log error for Chrome extension
        if source == "chrome_extension":
            chrome_extension_logger.log_chat_response(
                response=f"Enhanced processing error: {str(e)}",
                success=False,
                error_details=str(e)
            )
        
        # Fallback to basic response
        fallback_response = f"I encountered an issue with the enhanced analysis. Here's what I can tell you about your data from '{state.workbook_name}': "
        fallback_response += f"You have {len(state.available_views)} worksheets available for analysis. "
        
        if selected_chart:
            fallback_response += f"You've selected '{selected_chart}' for analysis. "
        
        fallback_response += "Please try rephrasing your question or select a different chart."
        
        return jsonify({
            "reply": fallback_response,
            "error": True,
            "enhanced_error": str(e)
        })

async def handle_auto_analysis_request(workbook_data, state, selected_chart, chart_context, source, cache_only=False):
    """Handle automatic analysis when chart is selected"""
    debug_log("=== AUTO ANALYSIS REQUEST ===")
    debug_log("Auto-analysis started", {
        "selected_chart": selected_chart,
        "workbook": state.workbook_name,
        "source": source
    })
    
    try:
        # Get specific worksheet data for the selected chart (now returns CSV data)
        worksheets_data = workbook_data.get('worksheets_data', {})
        if not worksheets_data:
            debug_log("No worksheets data available for auto-analysis")
            return jsonify({
                "reply": "Could not access chart data for analysis.",
                "error": True
            })
        
        # Get CSV data and extract Tableau column mapping
        chart_data = None
        tableau_columns = []
        
        if enhanced_data_fetcher:
            # This now returns CSV data but extracts Tableau columns
            chart_data = enhanced_data_fetcher.get_worksheet_data_for_chart(worksheets_data, selected_chart)
            
            # Get the stored Tableau column info from enhanced_data_fetcher
            try:
                # Check if enhanced_data_fetcher has stored tableau columns
                if hasattr(enhanced_data_fetcher, 'tableau_chart_columns') and selected_chart in enhanced_data_fetcher.tableau_chart_columns:
                    tableau_columns = enhanced_data_fetcher.tableau_chart_columns[selected_chart]
                    debug_log("Retrieved tableau columns from data fetcher", {
                        "chart": selected_chart,
                        "columns": tableau_columns
                    })
                else:
                    debug_log("No tableau columns found in data fetcher", {
                        "chart": selected_chart,
                        "has_tableau_chart_columns": hasattr(enhanced_data_fetcher, 'tableau_chart_columns'),
                        "available_charts": list(getattr(enhanced_data_fetcher, 'tableau_chart_columns', {}).keys())
                    })
            except Exception as e:
                debug_log("Error getting tableau columns", {"error": str(e)})
                
            debug_log("Tableau columns extracted", {
                "chart": selected_chart,
                "tableau_columns": tableau_columns
            })
            
            # Map Tableau columns to CSV columns
            mapped_csv_columns = []
            if tableau_columns and hasattr(csv_data_loader, 'map_tableau_to_csv_columns'):
                debug_log("Starting column mapping", {
                    "chart": selected_chart,
                    "tableau_columns": tableau_columns
                })
                mapped_csv_columns = csv_data_loader.map_tableau_to_csv_columns(tableau_columns, chart_name=selected_chart)
                debug_log("Column mapping completed", {
                    "tableau_columns": tableau_columns,
                    "mapped_csv_columns": mapped_csv_columns,
                    "mapping_successful": len(mapped_csv_columns) > 0
                })
            else:
                debug_log("Column mapping skipped", {
                    "has_tableau_columns": bool(tableau_columns),
                    "tableau_columns_value": tableau_columns,
                    "has_mapping_function": hasattr(csv_data_loader, 'map_tableau_to_csv_columns'),
                    "csv_loader_available": csv_data_loader is not None
                })
        
        if chart_data is None or chart_data.empty:
            debug_log("No data found for selected chart")
            return jsonify({
                "reply": f"Could not find data for chart '{selected_chart}'. Please try selecting a different chart.",
                "error": True
            })
        
        debug_log("Chart data found (CSV data)", {
            "chart": selected_chart,
            "rows": len(chart_data),
            "columns": len(chart_data.columns),
            "using_csv_data": True
        })
        
        # Generate auto-analysis using enhanced services
        if enhanced_query_agent:
            debug_log("Generating auto-analysis with enhanced agent")
            
            # Create a special auto-analysis context with column mapping
            analysis_context = {
                "chart_name": selected_chart,
                "data_shape": chart_data.shape,
                "columns": chart_data.columns.tolist(),
                "sample_data": chart_data.head(3).to_dict() if not chart_data.empty else {},
                "auto_analysis": True,
                "tableau_columns": tableau_columns,
                "mapped_csv_columns": mapped_csv_columns if 'mapped_csv_columns' in locals() else [],
                "using_csv_data": True,
                "data_source": "CSV data mapped from Tableau chart columns"
            }
            
            # Create analysis message with mapped column instructions
            if mapped_csv_columns:
                analysis_message = f"Generate auto-analysis for the chart '{selected_chart}' focusing specifically on these CSV columns: {mapped_csv_columns}. These columns were mapped from the Tableau chart columns: {tableau_columns}. IMPORTANT: Only analyze the mapped columns {mapped_csv_columns}, do not use any other columns like outbound_email_count."
                debug_log("Analysis message with mapped columns", {
                    "message": analysis_message,
                    "focus_columns": mapped_csv_columns
                })
            else:
                analysis_message = f"Generate auto-analysis for the chart '{selected_chart}' using CSV data."
                debug_log("Analysis message without mapped columns", {
                    "message": analysis_message,
                    "reason": "No mapped columns available"
                })
            
            # Call enhanced query agent for auto-analysis
            auto_analysis_request = EnhancedChatRequest(
                message=analysis_message,
                context={
                    "workbook_name": state.workbook_name,
                    "workbook_id": state.workbook_id,
                    "csv_file_path": csv_data_loader.csv_file_path if csv_data_loader else None,
                    "selected_chart": selected_chart,
                    "chart_context": chart_context,
                    "tableau_columns": tableau_columns,
                    "mapped_csv_columns": mapped_csv_columns,
                    "using_csv_data": True,
                    "data_source": "CSV data with Tableau column mapping",
                    "focus_columns": mapped_csv_columns,
                    "analysis_instruction": f"Focus analysis on columns: {mapped_csv_columns}" if mapped_csv_columns else "Analyze available data"
                },
                selected_chart=selected_chart,
                auto_analysis=True
            )
            
            # Create worksheets data with CSV data for analysis
            # Use only mapped columns for pure chart-specific analysis
            analysis_data = chart_data
            if mapped_csv_columns:
                # Use only the mapped columns - no hardcoded context addition
                available_analysis_cols = [col for col in mapped_csv_columns if col in chart_data.columns]
                
                if available_analysis_cols:
                    analysis_data = chart_data[available_analysis_cols]
                    debug_log("Filtered CSV data to mapped columns only", {
                        "original_columns": len(chart_data.columns),
                        "filtered_columns": len(analysis_data.columns),
                        "mapped_columns": mapped_csv_columns,
                        "available_mapped_columns": available_analysis_cols
                    })
            
            # Prepare data for enhanced agent - include both chart-specific and full dataset
            csv_worksheets_data = {selected_chart: analysis_data}
            
            # Add full worksheets data for impact analysis
            for ws_name, ws_data in worksheets_data.items():
                if ws_name != selected_chart:  # Don't duplicate the selected chart data
                    csv_worksheets_data[ws_name] = ws_data
            
            debug_log("Sending CSV data to enhanced agent", {
                "selected_chart": selected_chart,
                "chart_data_shape": analysis_data.shape,
                "analysis_columns": analysis_data.columns.tolist(),
                "mapped_columns": mapped_csv_columns,
                "total_worksheets": len(csv_worksheets_data),
                "all_worksheets": list(csv_worksheets_data.keys()),
                "full_dataset_available": len(csv_worksheets_data) > 1
            })
            
            # Process with enhanced agent using full CSV data
            agent_response = await enhanced_query_agent.process_enhanced_query(auto_analysis_request, csv_worksheets_data)
            
            debug_log("Auto-analysis generated", {
                "success": agent_response.get("success", False),
                "has_analysis": "auto_analysis" in agent_response
            })
            
            # Clean agent response for JSON serialization
            clean_agent_response = _make_json_serializable(agent_response)

            
            # If cache_only mode, return minimal response without detailed summary ## Jitendra
            if cache_only:
                debug_log("Cache-only mode: Returning minimal response")
                return jsonify({
                    "success": True,
                    "cache_created": True,
                    "reply": f"Analysis cache created for '{selected_chart}'",
                    "metadata": {
                        "chart": selected_chart,
                        "cache_only": True
                    }
                })

            # Format response for frontend
            is_auto_analysis = analysis_message.startswith("Generate auto-analysis for the chart")
            response_data = format_enhanced_response(clean_agent_response, workbook_data, state, is_auto_analysis)
            
            # For AUTO_ANALYSIS requests, don't include the detailed auto_analysis object
            # The simplified response is already in the reply field
            if not analysis_message.startswith("Generate auto-analysis for the chart"):
                response_data["auto_analysis"] = _make_json_serializable(agent_response.get("auto_analysis", 
                    "Chart analysis completed. Most impacting columns identified and statistical summary generated."))
            else:
                debug_log("Skipping detailed auto_analysis for AUTO_ANALYSIS request", {
                    "analysis_message": analysis_message,
                    "has_reply": "reply" in response_data
                })
            
            # Log for Chrome extension
            if source == "chrome_extension":
                chrome_extension_logger.log_chat_response(
                    response=response_data.get("reply", "Auto-analysis completed"),
                    success=True
                )
            
            return jsonify(response_data)
        
        else:
            debug_log("Enhanced agent not available, using basic auto-analysis")
            # Fallback: Basic auto-analysis
            return await generate_basic_auto_analysis(chart_data, selected_chart, state)
            
    except Exception as e:
        debug_log("Error in auto-analysis", {
            "error": str(e),
            "type": type(e).__name__,
            "traceback": traceback.format_exc()
        })
        
        # Log error for Chrome extension
        if source == "chrome_extension":
            chrome_extension_logger.log_chat_response(
                response=f"Auto-analysis error: {str(e)}",
                success=False,
                error_details=str(e)
            )
        
        return jsonify({
            "reply": f"I encountered an issue during auto-analysis: {str(e)}. Please try selecting the chart again.",
            "error": True,
            "auto_analysis_error": str(e)
        })

async def generate_basic_auto_analysis(chart_data, selected_chart, state):
    """Generate basic auto-analysis when enhanced services aren't available - simplified output"""
    debug_log("Generating basic auto-analysis")
    
    try:
        # Identify chart columns
        chart_columns = chart_data.columns.tolist()
        debug_log("Chart columns identified", {"columns": chart_columns})
        
        # Perform statistical analysis internally to determine most impactful columns
        numeric_cols = chart_data.select_dtypes(include=[np.number]).columns.tolist()
        categorical_cols = chart_data.select_dtypes(include=['object']).columns.tolist()
        
        # Internal analysis: variance analysis for numeric columns
        most_impactful = []
        impact_reasons = []
        
        if numeric_cols:
            # Calculate variance and other statistics internally
            variances = chart_data[numeric_cols].var().sort_values(ascending=False)
            
            # Select top 2 numeric columns by variance
            top_variance_cols = variances.head(2).index.tolist()
            for col in top_variance_cols:
                variance_pct = (variances[col] / variances.sum() * 100) if variances.sum() > 0 else 0
                reason = f"highest data variance ({variance_pct:.0f}% of total)"
                most_impactful.append(col)
                impact_reasons.append(reason)
        
        # Add categorical columns if we need more impactful columns
        if len(most_impactful) < 2 and categorical_cols:
            for col in categorical_cols[:2-len(most_impactful)]:
                if col not in most_impactful:
                    # Calculate diversity internally
                    cardinality = chart_data[col].nunique()
                    reason = f"high categorical diversity ({cardinality} unique values)"
                    most_impactful.append(col)
                    impact_reasons.append(reason)
        
        # Create simplified output - only show chart columns and most impactful
        if len(most_impactful) >= 2:
            auto_analysis = f"""📊 **Chart Columns:** {', '.join(chart_columns)}

🎯 **Most Impactful:** {most_impactful[0]} ({impact_reasons[0]}), {most_impactful[1]} ({impact_reasons[1]})"""
        elif len(most_impactful) >= 1:
            auto_analysis = f"""📊 **Chart Columns:** {', '.join(chart_columns)}

🎯 **Most Impactful:** {most_impactful[0]} ({impact_reasons[0]})"""
        else:
            auto_analysis = f"""📊 **Chart Columns:** {', '.join(chart_columns)}

🎯 **Most Impactful:** No additional data columns available for impact analysis"""
        
        return jsonify({
            "reply": f"Analysis for '{selected_chart}':",
            "auto_analysis": auto_analysis,
            "success": True,
            "chart_columns": chart_columns,
            "most_impactful_columns": most_impactful[:2]
        })
        
    except Exception as e:
        debug_log("Error in basic auto-analysis", {"error": str(e)})
        return jsonify({
            "reply": f"Could not complete auto-analysis: {str(e)}",
            "error": True
        })

def handle_basic_auto_analysis(state, selected_chart, chart_context, source):
    """Handle basic auto-analysis when enhanced services aren't available"""
    debug_log("=== BASIC AUTO ANALYSIS ===")
    debug_log("Basic auto-analysis started", {
        "selected_chart": selected_chart,
        "workbook": state.workbook_name,
        "source": source
    })
    
    try:
        # Use raw data if available
        if state.raw_data is not None and not state.raw_data.empty:
            chart_data = state.raw_data
            debug_log("Using raw data for basic auto-analysis", {
                "rows": len(chart_data),
                "columns": len(chart_data.columns)
            })
        else:
            debug_log("No raw data available for basic auto-analysis")
            return jsonify({
                "reply": f"Chart '{selected_chart}' selected, but no data available for immediate analysis. Please try asking a specific question about this chart.",
                "auto_analysis": "📊 Chart selected successfully! Ask me specific questions about this data to get detailed insights.",
                "success": True
            })
        
        # Identify chart columns (all available columns for basic analysis)
        chart_columns = chart_data.columns.tolist()
        debug_log("Chart columns identified", {"columns": chart_columns})
        
        # Perform statistical analysis internally to determine most impactful columns
        numeric_cols = chart_data.select_dtypes(include=[np.number]).columns.tolist()
        categorical_cols = chart_data.select_dtypes(include=['object']).columns.tolist()
        
        # Internal analysis: variance analysis for numeric columns
        most_impactful = []
        impact_reasons = []
        
        if numeric_cols:
            # Calculate variance and other statistics internally
            variances = chart_data[numeric_cols].var().sort_values(ascending=False)
            correlations = {}
            
            # Calculate correlations between numeric columns internally
            if len(numeric_cols) > 1:
                corr_matrix = chart_data[numeric_cols].corr()
                for i, col1 in enumerate(numeric_cols):
                    for j, col2 in enumerate(numeric_cols[i+1:], i+1):
                        corr_val = abs(corr_matrix.iloc[i, j])
                        import pandas as pd
                        if not pd.isna(corr_val):
                            correlations[(col1, col2)] = corr_val
            
            # Select top 2 numeric columns by variance
            top_variance_cols = variances.head(2).index.tolist()
            for col in top_variance_cols:
                variance_pct = (variances[col] / variances.sum() * 100) if variances.sum() > 0 else 0
                reason = f"highest data variance ({variance_pct:.0f}% of total)"
                most_impactful.append(col)
                impact_reasons.append(reason)
        
        # Add categorical columns if we need more impactful columns
        if len(most_impactful) < 2 and categorical_cols:
            for col in categorical_cols[:2-len(most_impactful)]:
                if col not in most_impactful:
                    # Calculate diversity internally
                    cardinality = chart_data[col].nunique()
                    total_rows = len(chart_data)
                    diversity_ratio = cardinality / total_rows if total_rows > 0 else 0
                    reason = f"high categorical diversity ({cardinality} unique values)"
                    most_impactful.append(col)
                    impact_reasons.append(reason)
        
        # Create simplified output - only show chart columns and most impactful
        if len(most_impactful) >= 2:
            auto_analysis = f"""📊 **Chart Columns:** {', '.join(chart_columns)}

🎯 **Most Impactful:** {most_impactful[0]} ({impact_reasons[0]}), {most_impactful[1]} ({impact_reasons[1]})"""
        elif len(most_impactful) >= 1:
            auto_analysis = f"""📊 **Chart Columns:** {', '.join(chart_columns)}

🎯 **Most Impactful:** {most_impactful[0]} ({impact_reasons[0]})"""
        else:
            auto_analysis = f"""📊 **Chart Columns:** {', '.join(chart_columns)}

🎯 **Most Impactful:** No additional data columns available for impact analysis"""
        
        debug_log("Basic auto-analysis completed", {
            "chart_columns": chart_columns,
            "most_impactful": most_impactful[:2],
            "impact_reasons": impact_reasons[:2]
        })
        
        # Log for Chrome extension
        if source == "chrome_extension":
            chrome_extension_logger.log_chat_response(
                response=f"Basic auto-analysis completed for '{selected_chart}'",
                success=True
            )
        
        return jsonify({
            "reply": f"Analysis for '{selected_chart}':",
            "auto_analysis": auto_analysis,
            "success": True,
            "chart_columns": chart_columns,
            "most_impactful_columns": most_impactful[:2],
            "basic_analysis": True
        })
        
    except Exception as e:
        debug_log("Error in basic auto-analysis", {
            "error": str(e),
            "type": type(e).__name__
        })
        
        # Log error for Chrome extension
        if source == "chrome_extension":
            chrome_extension_logger.log_chat_response(
                response=f"Basic auto-analysis error: {str(e)}",
                success=False,
                error_details=str(e)
            )
        
        return jsonify({
            "reply": f"Could not complete auto-analysis: {str(e)}",
            "error": True,
            "basic_analysis_error": str(e)
        })

def handle_enhanced_query_processing_sync(msg, state, selected_chart, chart_context, source, cache_only=False):
    """Synchronous wrapper for enhanced query processing"""
    import asyncio
    
    try:
        # Run the async function
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(
            handle_enhanced_query_processing(msg, state, selected_chart, chart_context, source, cache_only)
        )
        loop.close()
        return result
    except Exception as e:
        debug_log(f"Error in sync wrapper: {e}")
        return jsonify({
            "reply": f"Error in enhanced processing: {str(e)}",
            "error": True
        })

async def get_or_fetch_workbook_data(state):
    """Get workbook data, fetching if not already available"""
    debug_log("Getting workbook data")
    
    try:
        # Note: CSV data loader is not used here anymore for worksheet data
        # CSV is only used for generating summaries on connection
        
        # Use enhanced data fetcher if available (for Tableau data)
        if enhanced_data_fetcher:
            debug_log("Using enhanced data fetcher")
            workbook_data = await enhanced_data_fetcher.fetch_workbook_data(state)
            debug_log("Workbook data fetched", {
                "success": workbook_data.get("success", False),
                "total_rows": workbook_data.get("total_rows", 0),
                "total_worksheets": workbook_data.get("total_worksheets", 0)
            })
            return workbook_data
        else:
            debug_log("Enhanced data fetcher not available, using basic data")
            # Fallback to basic data structure
            worksheets_data = {}
            
            for view in state.available_views:
                view_name = view.get('name')
                view_id = view.get('id')
                
                if view_name and view_id:
                    try:
                        df = data_processor.get_view_data(state.site_id, view_id, state.auth_token)
                        if df is not None:
                            worksheets_data[view_name] = df
                    except Exception as e:
                        debug_log(f"Error fetching data for {view_name}: {e}")
            
            return {
                'success': True,
                'worksheets_data': worksheets_data,
                'workbook_summary': {
                    'summary_line1': f"📊 Data from {len(worksheets_data)} worksheets",
                    'summary_line2': "🔍 Ready for analysis"
                },
                'total_rows': sum(len(df) for df in worksheets_data.values()),
                'total_worksheets': len(worksheets_data)
            }
            
    except Exception as e:
        debug_log(f"Error getting workbook data: {e}")
        return {
            'success': False,
            'error': str(e),
            'worksheets_data': {}
        }

def get_all_columns_from_workbook(worksheets_data):
    """Extract all unique column names from workbook data"""
    all_columns = set()
    
    for df in worksheets_data.values():
        if df is not None:
            all_columns.update(df.columns.tolist())
    
    return list(all_columns)

def _make_json_serializable(obj):
    """Convert objects to JSON serializable format"""
    import numpy as np
    import pandas as pd
    
    if obj is None:
        return None
    elif isinstance(obj, (np.integer, np.floating)):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, pd.DataFrame):
        return {
            'type': 'dataframe',
            'data': obj.head(20).fillna('').to_dict('records'),
            'shape': list(obj.shape),
            'columns': obj.columns.tolist()
        }
    elif isinstance(obj, pd.Series):
        return {
            'type': 'series', 
            'data': obj.head(20).fillna('').to_dict(),
            'name': str(obj.name) if obj.name is not None else None,
            'length': len(obj)
        }
    elif isinstance(obj, dict):
        return {k: _make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_make_json_serializable(item) for item in obj]
    elif isinstance(obj, str):
        # Preserve string formatting (don't convert to string again)
        return obj
    elif hasattr(obj, '__dict__'):
        # Handle custom objects by converting to dict
        return _make_json_serializable(obj.__dict__)
    elif hasattr(obj, 'to_dict'):
        # Handle objects with to_dict method
        return _make_json_serializable(obj.to_dict())
    else:
        try:
            # Try to convert to string if all else fails
            return str(obj)
        except:
            return None

def format_enhanced_response(agent_response, workbook_data, state, is_auto_analysis=False):
    """Format the enhanced agent response for the frontend"""
    
    try:
        # For AUTO_ANALYSIS requests, return only the essential simplified response
        if is_auto_analysis:
            return {
                "reply": agent_response.get("reply", "Analysis completed"),
                "success": agent_response.get("success", True),
                "error": not agent_response.get("success", True),
                "available_charts": [view['name'] for view in state.available_views]
            }
        
        # Base response for regular queries
        formatted = {
            "reply": agent_response.get("reply", "Analysis completed"),
            "success": agent_response.get("success", True),
            "error": not agent_response.get("success", True)
        }
        
        # Add workbook summary if this is a chart selection (auto-analysis)
        # But skip for AUTO_ANALYSIS requests to keep response simplified
        if agent_response.get("auto_analysis") and not is_auto_analysis:
            auto_analysis = agent_response["auto_analysis"]
            if hasattr(auto_analysis, 'summary_lines'):
                summary_text = "\n".join(auto_analysis.summary_lines)
                formatted["reply"] = summary_text + "\n\n" + formatted["reply"]
        
        # Add chart selection UI if needed
        if agent_response.get("requires_chart_selection"):
            formatted["requires_chart_selection"] = True
            formatted["available_charts"] = [view['name'] for view in state.available_views]
        
        # Add suggested actions
        if agent_response.get("suggested_actions"):
            formatted["suggested_actions"] = agent_response["suggested_actions"]
        
        # Add features table for chrome extension (Top/Bottom 5 analysis)
        if agent_response.get("features_table"):
            formatted["features_table"] = agent_response["features_table"]
            debug_log(f"Added features table to response: {agent_response['features_table'].get('title', 'Unknown')}")
        
        # ✅ FIXED: Add SHAP visualizations for chrome extension
        if agent_response.get("shap_visualizations"):
            formatted["shap_visualizations"] = agent_response["shap_visualizations"]
            debug_log(f"Added SHAP visualizations to response: {len(agent_response['shap_visualizations'])} visualization sets")
        
        # Check for 7-layer interpretive analysis
        if agent_response.get("seven_layer_analysis"):
            seven_layer = agent_response["seven_layer_analysis"]
            analysis_type = seven_layer.get("type", "single_metric")
            
            def get_section_icon(title: str) -> str:
                """Get emoji icon for section title"""
                icons = {
                    "Trend Summary": "📊",
                    "Segment Breakdown": "🔍",
                    "Key Drivers": "⬆️",
                    "Temporal Pattern": "⏱️"
                }
                return icons.get(title, "📌")
            
            if analysis_type == "multi_metric":
                # Multiple metrics - format each separately with collapsible sections
                analyses = seven_layer.get("analyses", [])
                formatted["analysis_type"] = "interpretive_multi_metric"
                formatted["seven_layer_analyses"] = []  # Array of analyses
                
                reply_parts = []
                for analysis in analyses:
                    metric_name = analysis.get("metric", "Unknown Metric")
                    exec_summary = analysis.get("executive_summary", "")
                    sections = analysis.get("sections", [])
                    
                    # Add metric header and executive summary (always visible)
                    reply_parts.append(f"## {metric_name}")
                    reply_parts.append(exec_summary)
                    reply_parts.append("")
                    
                    # Prepare collapsible sections for this metric
                    formatted_sections = []
                    for section in sections:
                        formatted_sections.append({
                            "id": f"{metric_name.lower().replace(' ', '-')}-{section.get('title', '').lower().replace(' ', '-')}",
                            "title": get_section_icon(section.get("title", "")) + " " + section.get("title", ""),
                            "content": section.get("content", ""),
                            "layer": section.get("layer", 0),
                            "collapsed": True,
                            "description": section.get("description")
                        })
                    
                    # Add to per-metric analysis structure
                    formatted["seven_layer_analyses"].append({
                        "metric": metric_name,
                        "executive_summary": exec_summary,
                        "sections": formatted_sections,
                        "comparison_context": analysis.get("comparison_context", "")
                    })
                
                formatted["reply"] = "\n".join(reply_parts).strip()
                
            else:
                # Single metric - format with collapsible sections (legacy format)
                exec_summary = seven_layer.get("executive_summary", "")
                sections = seven_layer.get("sections", [])
                
                # Executive summary always visible
                reply_parts = [exec_summary, ""]
                
                # Prepare collapsible sections
                formatted_sections = []
                for section in sections:
                    formatted_sections.append({
                        "id": section.get("title", "").lower().replace(" ", "-"),
                        "title": get_section_icon(section.get("title", "")) + " " + section.get("title", ""),
                        "content": section.get("content", ""),
                        "layer": section.get("layer", 0),
                        "collapsed": True,
                        "description": section.get("description")
                    })
                
                formatted["reply"] = exec_summary
                formatted["analysis_type"] = "interpretive"
                formatted["seven_layer_sections"] = formatted_sections
                formatted["seven_layer_analysis"] = seven_layer  # Keep original for backward compat
        
        # Add visualization if available
        if agent_response.get("visualization"):
            viz = agent_response["visualization"]
            chart_image = viz.get("chart_image")
            
            # DEBUG: Log chart_image length at formatting stage
            if chart_image:
                debug_log(f"[VIZ DEBUG] format_enhanced_response - chart_image length: {len(chart_image)} chars")
                debug_log(f"[VIZ DEBUG] format_enhanced_response - chart_image preview: {chart_image[:100]}...")
            else:
                debug_log("[VIZ DEBUG] format_enhanced_response - chart_image is None or empty")
            
            formatted["visualization"] = {
                "type": "static",
                "chart_type": viz.get("chart_type"),
                "chart_image": chart_image,
                "title": viz.get("title"),
                "description": viz.get("description"),
                "insights": viz.get("insights", [])
            }
        
        # Add metadata with safe intent handling
        intent_data = agent_response.get("intent")
        intent_value = None
        confidence_value = None
        
        if intent_data:
            try:
                # Handle different intent object types
                if hasattr(intent_data, 'primary_intent'):
                    # IntentType object
                    primary_intent = getattr(intent_data, 'primary_intent', None)
                    if hasattr(primary_intent, 'value'):
                        intent_value = primary_intent.value
                    else:
                        intent_value = str(primary_intent)
                    confidence_value = getattr(intent_data, 'confidence', None)
                elif isinstance(intent_data, dict):
                    # Dict format
                    intent_value = intent_data.get("primary_intent", {}).get("value")
                    confidence_value = intent_data.get("confidence")
                else:
                    # String or other format
                    intent_value = str(intent_data)
            except Exception as e:
                debug_log(f"Error processing intent data: {e}")
                intent_value = str(intent_data) if intent_data else None
        
        formatted["metadata"] = {
            "intent": intent_value,
            "confidence": confidence_value,
            "execution_time": agent_response.get("execution_time", 0),
            "workbook_name": state.workbook_name,
            "total_worksheets": workbook_data.get("total_worksheets", 0),
            "total_rows": workbook_data.get("total_rows", 0)
        }
        
        return formatted
        
    except Exception as e:
        debug_log(f"Error formatting enhanced response: {e}")
        return {
            "reply": agent_response.get("reply", "Analysis completed"),
            "success": False,
            "error": True,
            "format_error": str(e)
        }

def handle_chart_specific_query(msg, state, selected_chart, chart_context):
    """Handle queries about a specific selected chart"""
    debug_log("Handling chart-specific query", {
        "chart": selected_chart,
        "context": chart_context,
        "message": msg
    })
    
    try:
        # Find the selected chart in available views
        target_view = None
        for view in state.available_views:
            if view['name'] == selected_chart:
                target_view = view
                break
        
        if not target_view:
            debug_log("Selected chart not found in available views")
            return jsonify({
                "reply": f"Chart '{selected_chart}' not found in available worksheets."
            })
        
        # Get data for the selected chart
        debug_log("Fetching data for selected chart")
        chart_data = data_processor.get_view_data(
            state.site_id, 
            target_view['id'], 
            state.auth_token
        )
        
        if chart_data is None or chart_data.empty:
            debug_log("No data available for selected chart")
            return jsonify({
                "reply": f"Could not retrieve data for chart '{selected_chart}'. Please check permissions."
            })
        
        debug_log("Chart data retrieved successfully", {
            "rows": len(chart_data),
            "columns": len(chart_data.columns)
        })
        
        # Analyze the question in context of the selected chart
        analysis_response = analyze_chart_question(msg, selected_chart, chart_data, state.workbook_name)
        
        debug_log("Chart-specific analysis completed")
        return jsonify({"reply": analysis_response})
        
    except Exception as e:
        debug_log("Chart-specific query exception", {"error": str(e)})
        return jsonify({
            "reply": f"Error analyzing chart '{selected_chart}': {str(e)}",
            "error": True
        })

def analyze_chart_question(question, chart_name, chart_data, workbook_name):
    """Analyze a question about a specific chart"""
    debug_log("Analyzing chart question", {
        "chart": chart_name,
        "data_shape": chart_data.shape,
        "question": question
    })
    
    try:
        question_lower = question.lower()
        
        # Determine analysis type based on question
        if any(word in question_lower for word in ['top', 'highest', 'best', 'maximum', 'largest']):
            return get_top_values_analysis(chart_name, chart_data)
        
        elif any(word in question_lower for word in ['trend', 'over time', 'change', 'growth', 'pattern']):
            return get_trend_analysis(chart_name, chart_data)
        
        elif any(word in question_lower for word in ['spike', 'peak', 'high', 'anomaly']):
            return get_spike_analysis(chart_name, chart_data)
        
        elif any(word in question_lower for word in ['dip', 'low', 'drop', 'decrease']):
            return get_dip_analysis(chart_name, chart_data)
        
        elif any(word in question_lower for word in ['insight', 'analysis', 'summary', 'overview']):
            return get_insights_analysis(chart_name, chart_data)
        
        elif any(word in question_lower for word in ['compare', 'comparison', 'versus', 'vs']):
            return get_comparison_analysis(chart_name, chart_data)
        
        else:
            return get_general_chart_analysis(question, chart_name, chart_data, workbook_name)
    
    except Exception as e:
        debug_log("Chart question analysis failed", {"error": str(e)})
        return f"Error analyzing your question about '{chart_name}': {str(e)}"

def get_top_values_analysis(chart_name, chart_data):
    """Get top values from the chart data"""
    try:
        numeric_cols = chart_data.select_dtypes(include=['number']).columns.tolist()
        
        if not numeric_cols:
            return f"No numeric data found in '{chart_name}' to determine top values."
        
        analysis = f"**Top Values Analysis for '{chart_name}':**\n\n"
        
        for col in numeric_cols[:2]:  # Analyze top 2 numeric columns
            top_5 = chart_data.nlargest(5, col)
            analysis += f"**Top 5 in {col}:**\n"
            
            for idx, row in top_5.iterrows():
                # Try to find a descriptive column (non-numeric)
                desc_cols = [c for c in chart_data.columns if c != col and chart_data[c].dtype == 'object']
                if desc_cols:
                    desc = row[desc_cols[0]]
                    analysis += f"• {desc}: {row[col]:,.0f}\n"
                else:
                    analysis += f"• Row {idx}: {row[col]:,.0f}\n"
            
            analysis += "\n"
        
        return analysis.strip()
        
    except Exception as e:
        return f"Could not analyze top values for '{chart_name}': {str(e)}"

def get_trend_analysis(chart_name, chart_data):
    """Analyze trends in the chart data"""
    try:
        numeric_cols = chart_data.select_dtypes(include=['number']).columns.tolist()
        date_cols = [col for col in chart_data.columns if any(keyword in col.lower() for keyword in ['date', 'time', 'month', 'year'])]
        
        if not numeric_cols:
            return f"No numeric data found in '{chart_name}' for trend analysis."
        
        analysis = f"**Trend Analysis for '{chart_name}':**\n\n"
        
        for col in numeric_cols[:2]:
            values = chart_data[col].dropna()
            if len(values) > 1:
                first_value = values.iloc[0]
                last_value = values.iloc[-1]
                change = ((last_value - first_value) / first_value) * 100 if first_value != 0 else 0
                
                trend_direction = "increasing" if change > 5 else "decreasing" if change < -5 else "stable"
                analysis += f"**{col} Trend:** {trend_direction} ({change:+.1f}% overall change)\n"
                analysis += f"• Start: {first_value:,.0f}\n"
                analysis += f"• End: {last_value:,.0f}\n"
                analysis += f"• Average: {values.mean():,.0f}\n\n"
        
        return analysis.strip()
        
    except Exception as e:
        return f"Could not analyze trends for '{chart_name}': {str(e)}"

def get_spike_analysis(chart_name, chart_data):
    """Detect spikes in the chart data"""
    try:
        anomalies = detect_specific_anomalies(chart_data, "Spike", threshold=0.2)
        
        if anomalies:
            analysis = f"**Spike Detection for '{chart_name}':**\n\n"
            analysis += f"Found {len(anomalies)} significant spike(s):\n\n"
            
            for anomaly in anomalies[:3]:  # Show top 3
                analysis += f"• {anomaly['description']}\n"
            
            if len(anomalies) > 3:
                analysis += f"\n...and {len(anomalies) - 3} more spikes detected."
        else:
            analysis = f"No significant spikes detected in '{chart_name}' with current sensitivity settings."
        
        return analysis
        
    except Exception as e:
        return f"Could not analyze spikes for '{chart_name}': {str(e)}"

def get_dip_analysis(chart_name, chart_data):
    """Detect dips in the chart data"""
    try:
        anomalies = detect_specific_anomalies(chart_data, "Dip", threshold=0.2)
        
        if anomalies:
            analysis = f"**Dip Detection for '{chart_name}':**\n\n"
            analysis += f"Found {len(anomalies)} significant dip(s):\n\n"
            
            for anomaly in anomalies[:3]:  # Show top 3
                analysis += f"• {anomaly['description']}\n"
            
            if len(anomalies) > 3:
                analysis += f"\n...and {len(anomalies) - 3} more dips detected."
        else:
            analysis = f"No significant dips detected in '{chart_name}' with current sensitivity settings."
        
        return analysis
        
    except Exception as e:
        return f"Could not analyze dips for '{chart_name}': {str(e)}"

def get_insights_analysis(chart_name, chart_data):
    """Generate insights about the chart data"""
    try:
        analysis = f"**Key Insights for '{chart_name}':**\n\n"
        
        # Data overview
        analysis += f"📊 **Data Overview:**\n"
        analysis += f"• Rows: {len(chart_data):,}\n"
        analysis += f"• Columns: {len(chart_data.columns)}\n\n"
        
        # Numeric insights
        numeric_cols = chart_data.select_dtypes(include=['number']).columns.tolist()
        if numeric_cols:
            analysis += f"📈 **Numeric Analysis:**\n"
            for col in numeric_cols[:2]:
                values = chart_data[col].dropna()
                analysis += f"• **{col}:** Min: {values.min():,.0f}, Max: {values.max():,.0f}, Avg: {values.mean():,.0f}\n"
            analysis += "\n"
        
        # Missing data check
        missing_data = chart_data.isnull().sum().sum()
        if missing_data > 0:
            analysis += f"WARNING **Data Quality:** {missing_data} missing values detected\n\n"
        
        return analysis.strip()
        
    except Exception as e:
        return f"Could not generate insights for '{chart_name}': {str(e)}"

def get_comparison_analysis(chart_name, chart_data):
    """Perform comparison analysis"""
    try:
        analysis = f"**Comparison Analysis for '{chart_name}':**\n\n"
        
        numeric_cols = chart_data.select_dtypes(include=['number']).columns.tolist()
        categorical_cols = chart_data.select_dtypes(include=['object']).columns.tolist()
        
        if len(numeric_cols) >= 2:
            col1, col2 = numeric_cols[0], numeric_cols[1]
            correlation = chart_data[col1].corr(chart_data[col2]) if len(chart_data) > 1 else 0
            
            analysis += f"**{col1} vs {col2}:**\n"
            analysis += f"• Correlation: {correlation:.2f}\n"
            analysis += f"• {col1} range: {chart_data[col1].min():,.0f} - {chart_data[col1].max():,.0f}\n"
            analysis += f"• {col2} range: {chart_data[col2].min():,.0f} - {chart_data[col2].max():,.0f}\n"
        
        elif categorical_cols and numeric_cols:
            cat_col = categorical_cols[0]
            num_col = numeric_cols[0]
            
            analysis += f"**{cat_col} Performance ({num_col}):**\n"
            grouped = chart_data.groupby(cat_col)[num_col].agg(['mean', 'count']).reset_index()
            top_performers = grouped.nlargest(3, 'mean')
            
            for _, row in top_performers.iterrows():
                analysis += f"• {row[cat_col]}: {row['mean']:,.0f} avg ({row['count']} records)\n"
        
        return analysis
        
    except Exception as e:
        return f"Could not perform comparison analysis for '{chart_name}': {str(e)}"

def get_general_chart_analysis(question, chart_name, chart_data, workbook_name):
    """General AI-powered analysis for the chart"""
    try:
        # Create a comprehensive analysis prompt
        data_summary = f"""
        Chart: {chart_name}
        Workbook: {workbook_name}
        Data shape: {chart_data.shape[0]} rows, {chart_data.shape[1]} columns
        Columns: {', '.join(chart_data.columns.tolist()[:10])}
        Sample data: {chart_data.head(2).to_string()}
        """
        
        analysis_prompt = f"""
        User is asking about a Tableau chart: "{question}"
        
        Chart context:
        {data_summary}
        
        Please provide a helpful analysis addressing their specific question. Be concise and actionable.
        Focus on insights relevant to their question about this specific chart.
        """
        
        ai_response = call_llm(analysis_prompt)
        
        return f"**Analysis for '{chart_name}':**\n\n{ai_response}"
        
    except Exception as e:
        return f"Could not analyze '{chart_name}': {str(e)}"

# Keep all your existing handler functions
def handle_chart_context_query(msg, state, worksheet_name, selected_data):
    """Handle queries with chart interaction context - Enhanced with debugging"""
    debug_log("Handling chart context query", {
        "worksheet": worksheet_name,
        "selected_data_count": len(selected_data),
        "message": msg
    })
    
    try:
        if not selected_data or len(selected_data) == 0:
            debug_log("No selected data available")
            return jsonify({
                "reply": "No chart data selected. Please click on a data point in the chart first."
            })
        
        # Get the first selected data point
        data_point = selected_data[0]
        point_description = ", ".join([f"{k}: {v}" for k, v in data_point.items()])
        
        debug_log("Processing selected data point", {
            "data_point": data_point,
            "description": point_description
        })
        
        # Create context-aware analysis
        analysis_prompt = f"""
        User selected a data point in Tableau worksheet '{worksheet_name}':
        Selected data: {point_description}
        
        User question: "{msg}"
        
        Workbook: {state.workbook_name}
        Available worksheets: {', '.join([v['name'] for v in state.available_views[:5]])}
        
        The user is asking about this specific selected data point. Provide insights about:
        1. What this data point represents
        2. How to interpret the user's question in context of this selection
        3. Specific analysis or next steps for this data point
        
        Be concise and actionable.
        """
        
        debug_log("Calling LLM for chart context analysis")
        # Get AI analysis
        ai_response = call_llm(analysis_prompt)
        
        reply = f"Analysis for selected data ({point_description}) in '{worksheet_name}':\n\n{ai_response}"
        
        # Add suggested actions
        reply += f"\n\n💡 **Suggestions:**\n"
        reply += f"- Ask 'why did this spike?' to detect anomalies\n"
        reply += f"- Ask 'compare with similar data' for benchmarking\n"
        reply += f"- Ask 'show me the trend' for time series analysis"
        
        debug_log("Chart context query completed successfully")
        return jsonify({"reply": reply})
        
    except Exception as e:
        debug_log("Chart context query exception", {"error": str(e)})
        return jsonify({
            "reply": f"Error analyzing selected chart data: {str(e)}",
            "error": True
        })

def handle_chart_guidance(msg, state):
    """Handle queries about chart interaction guidance"""
    debug_log("Handling chart guidance request")
    
    try:
        guidance = f"""
📊 **Chart Selection Guide for '{state.workbook_name}':**

**Available worksheets:**
{chr(10).join([f'• {view["name"]}' for view in state.available_views[:8]])}

**How to use:**
1. **Select a chart** using the buttons above
2. **Ask questions** about the selected chart:
   - "What are the top values?"
   - "Show me trends over time"
   - "Detect any spikes or anomalies"
   - "Give me key insights"
   - "Compare the data"

**What I can analyze:**
- Top/bottom values and rankings
- Trend analysis and patterns
- Anomaly detection (spikes and dips)
- Statistical insights and summaries
- Data comparisons and correlations

**Please select a chart first to get started!**
        """
        
        debug_log("Chart guidance provided successfully")
        return jsonify({"reply": guidance})
        
    except Exception as e:
        debug_log("Chart guidance exception", {"error": str(e)})
        return jsonify({
            "reply": f"Error providing chart guidance: {str(e)}",
            "error": True
        })

def handle_anomaly_detection(msg, state):
    """Handle anomaly detection requests - Enhanced with debugging"""
    debug_log("Handling anomaly detection request", {"message": msg})
    
    try:
        # Determine analysis type
        analysis_type = "Spike" if "spike" in msg.lower() else "Dip"
        debug_log("Analysis type determined", {"type": analysis_type})
        
        # Get available views
        analyzable_views = [v for v in state.available_views if v['name'].lower() != 'raw_data']
        
        debug_log("Analyzable views found", {
            "count": len(analyzable_views),
            "views": [v['name'] for v in analyzable_views]
        })
        
        if not analyzable_views:
            debug_log("No analyzable views available")
            return jsonify({
                "reply": "No analyzable worksheets found in your workbook. Available views: " + 
                        ", ".join([v['name'] for v in state.available_views])
            })
        
        # Use the first available view for quick analysis
        selected_view = analyzable_views[0]
        debug_log("Selected view for analysis", {"view": selected_view['name']})
        
        # Fetch data
        debug_log("Fetching data from view")
        chart_data = data_processor.get_view_data(
            state.site_id, 
            selected_view['id'], 
            state.auth_token
        )
        
        if chart_data is None or chart_data.empty:
            debug_log("No data retrieved from view")
            return jsonify({
                "reply": f"Could not fetch data from worksheet '{selected_view['name']}'. Please check your permissions."
            })
        
        debug_log("Data retrieved successfully", {
            "rows": len(chart_data),
            "columns": len(chart_data.columns)
        })
        
        # Detect anomalies
        debug_log("Detecting anomalies")
        anomalies = detect_specific_anomalies(chart_data, analysis_type)
        
        debug_log("Anomaly detection completed", {"count": len(anomalies)})
        
        if anomalies:
            anomaly_descriptions = [a['description'] for a in anomalies[:3]]  # Show top 3
            reply = f"Found {len(anomalies)} {analysis_type.lower()}(s) in '{selected_view['name']}':\n\n" + \
                   "\n".join([f"• {desc}" for desc in anomaly_descriptions])
            
            if len(anomalies) > 3:
                reply += f"\n\n... and {len(anomalies) - 3} more."
        else:
            reply = f"No significant {analysis_type.lower()}s detected in '{selected_view['name']}' with current settings."
        
        debug_log("Anomaly detection response prepared")
        return jsonify({"reply": reply})
        
    except Exception as e:
        debug_log("Anomaly detection exception", {"error": str(e)})
        return jsonify({
            "reply": f"Error during anomaly detection: {str(e)}",
            "error": True
        })

def handle_analysis_request(msg, state):
    """Handle analysis and insight requests - Enhanced with debugging"""
    debug_log("Handling analysis request", {"message": msg})
    
    try:
        if state.raw_data is not None:
            debug_log("Using raw data for analysis", {
                "rows": state.raw_data.shape[0],
                "columns": state.raw_data.shape[1]
            })
            
            # Use AI to analyze the raw data
            analysis_prompt = f"""
            User question: "{msg}"
            
            Available data from Tableau workbook '{state.workbook_name}':
            - Rows: {state.raw_data.shape[0]}
            - Columns: {', '.join(state.raw_data.columns[:10])}
            - Sample data: {state.raw_data.head(3).to_string()}
            
            Provide insights based on this data structure and the user's question. Be concise and actionable.
            """
            
            debug_log("Calling LLM for analysis")
            # Call LLM for analysis
            ai_response = call_llm(analysis_prompt)
            reply = f"AI Analysis for workbook '{state.workbook_name}':\n\n{ai_response}"
        else:
            debug_log("No raw data available for analysis")
            reply = f"I can analyze your data from '{state.workbook_name}', but the raw_data sheet " \
                   f"is not available. Available worksheets: {', '.join([v['name'] for v in state.available_views])}"
        
        debug_log("Analysis request completed successfully")
        return jsonify({"reply": reply})
        
    except Exception as e:
        debug_log("Analysis request exception", {"error": str(e)})
        return jsonify({
            "reply": f"Error during analysis: {str(e)}",
            "error": True
        })

def handle_data_request(msg, state):
    """Handle data display and information requests"""
    debug_log("Handling data request")
    
    try:
        reply = f"Data from workbook '{state.workbook_name}':\n\n"
        reply += f"📊 Available worksheets ({len(state.available_views)}):\n"
        
        for view in state.available_views:
            reply += f"• {view['name']}\n"
        
        if state.raw_data is not None:
            reply += f"\n📋 Raw data: {state.raw_data.shape[0]} rows, {state.raw_data.shape[1]} columns\n"
            reply += f"Columns: {', '.join(state.raw_data.columns[:5])}..."
        
        reply += f"\nYour request: {msg}"
        
        debug_log("Data request completed successfully")
        return jsonify({"reply": reply})
        
    except Exception as e:
        debug_log("Data request exception", {"error": str(e)})
        return jsonify({
            "reply": f"Error retrieving data info: {str(e)}",
            "error": True
        })

# Keep all your existing endpoints unchanged
@app.route("/api/chart/interaction", methods=["POST"])
def handle_chart_interaction():
    """Handle chart interaction events from Tableau - Enhanced with debugging"""
    debug_log("Chart interaction endpoint called")
    
    try:
        data = request.get_json(silent=True) or {}
        interaction = data.get("interaction", {})
        tableau_context = data.get("tableauContext", {})
        connection_key = data.get("connectionKey", "default_workbook")
        
        debug_log("Chart interaction received", {
            "interaction_type": interaction.get("type"),
            "worksheet": interaction.get("worksheet"),
            "selected_data_count": len(interaction.get("selectedData", [])),
            "connection_key": connection_key
        })
        
        # Get current state
        current_state = state_manager.get_state(connection_key)
        if not current_state:
            debug_log("No current state found for chart interaction")
            return jsonify({
                "success": False,
                "error": "No active Tableau connection found"
            }), 400
        
        # Store interaction context in state
        if not hasattr(current_state, 'chart_interactions'):
            current_state.chart_interactions = []
        
        interaction_record = {
            "timestamp": interaction.get("timestamp"),
            "worksheet": interaction.get("worksheet"),
            "selected_data": interaction.get("selectedData", []),
            "interaction_type": interaction.get("type", "click")
        }
        
        current_state.chart_interactions.append(interaction_record)
        
        # Keep only last 10 interactions to prevent memory buildup
        current_state.chart_interactions = current_state.chart_interactions[-10:]
        
        # Update activity
        current_state.update_activity()
        
        debug_log("Chart interaction stored successfully", {
            "total_interactions": len(current_state.chart_interactions)
        })
        
        return jsonify({
            "success": True,
            "message": f"Interaction recorded for {interaction.get('worksheet', 'unknown worksheet')}"
        })
        
    except Exception as e:
        debug_log("Chart interaction exception", {"error": str(e)})
        return jsonify({
            "success": False,
            "error": f"Error handling chart interaction: {str(e)}"
        }), 500

@app.route("/api/chart/analyze", methods=["POST"])
def analyze_chart_selection():
    """Analyze selected chart data point - Enhanced with debugging"""
    debug_log("Chart analysis endpoint called")
    
    try:
        data = request.get_json(silent=True) or {}
        context = data.get("context", {})
        tableau_context = data.get("tableauContext", {})
        connection_key = data.get("connectionKey", "default_workbook")
        
        worksheet_name = context.get("worksheet")
        data_point = context.get("dataPoint", {})
        analysis_type = context.get("analysisType", "spike")
        
        debug_log("Chart analysis request", {
            "worksheet": worksheet_name,
            "data_point": data_point,
            "analysis_type": analysis_type,
            "connection_key": connection_key
        })
        
        # Get current state
        current_state = state_manager.get_state(connection_key)
        if not current_state:
            debug_log("No current state found for chart analysis")
            return jsonify({
                "success": False,
                "error": "No active Tableau connection found"
            }), 400
        
        # Find the relevant worksheet data
        target_view = None
        for view in current_state.available_views:
            if view['name'] == worksheet_name:
                target_view = view
                break
        
        if not target_view:
            debug_log("Target worksheet not found", {"worksheet": worksheet_name})
            return jsonify({
                "success": False,
                "error": f"Worksheet '{worksheet_name}' not found"
            })
        
        debug_log("Target worksheet found", {"view_id": target_view['id']})
        
        # Get worksheet data
        debug_log("Fetching worksheet data for analysis")
        worksheet_data = data_processor.get_view_data(
            current_state.site_id,
            target_view['id'],
            current_state.auth_token
        )
        
        if worksheet_data is None or worksheet_data.empty:
            debug_log("No worksheet data available for analysis")
            return jsonify({
                "success": False,
                "error": "Could not fetch worksheet data"
            })
        
        debug_log("Worksheet data fetched", {
            "rows": len(worksheet_data),
            "columns": len(worksheet_data.columns)
        })
        
        # Perform analysis on the selected data point
        debug_log("Performing data point analysis")
        analysis_result = analyze_selected_data_point(
            worksheet_data, 
            data_point, 
            analysis_type,
            current_state.workbook_name
        )
        
        debug_log("Data point analysis completed")
        
        return jsonify({
            "success": True,
            "analysis": analysis_result,
            "context": {
                "worksheet": worksheet_name,
                "data_point": data_point,
                "analysis_type": analysis_type
            }
        })
        
    except Exception as e:
        debug_log("Chart analysis exception", {"error": str(e)})
        return jsonify({
            "success": False,
            "error": f"Analysis failed: {str(e)}"
        }), 500

def analyze_selected_data_point(data, selected_point, analysis_type, workbook_name):
    """Analyze a specific data point selected by the user - Enhanced with debugging"""
    debug_log("Analyzing selected data point", {
        "data_shape": data.shape,
        "selected_point": selected_point,
        "analysis_type": analysis_type,
        "workbook": workbook_name
    })
    
    try:
        # Build context for the analysis
        point_description = ", ".join([f"{k}: {v}" for k, v in selected_point.items()])
        
        # Create analysis prompt
        analysis_prompt = f"""
        User selected a data point in Tableau workbook '{workbook_name}':
        Selected point: {point_description}
        
        Analysis type requested: {analysis_type}
        
        Available data context:
        - Total rows: {len(data)}
        - Columns: {', '.join(data.columns[:10])}
        - Sample data around selection: {data.head(3).to_string()}
        
        Please provide insights about this specific data point, focusing on:
        1. What makes this data point significant
        2. Potential reasons for any {analysis_type.lower()}s or anomalies
        3. Recommended next steps for analysis
        
        Be specific and actionable.
        """
        
        debug_log("Calling LLM for data point analysis")
        # Call LLM for analysis
        analysis = call_llm(analysis_prompt)
        
        result = f"Analysis for selected point ({point_description}):\n\n{analysis}"
        debug_log("Data point analysis completed successfully")
        return result
        
    except Exception as e:
        debug_log("Data point analysis exception", {"error": str(e)})
        return f"Error analyzing data point: {str(e)}"

@app.route("/api/state")
def get_state():
    """Get current connection state"""
    debug_log("State endpoint called")
    
    connection_key = request.args.get('connection_key', 'default_workbook')
    debug_log("Getting state", {"connection_key": connection_key})
    
    state = state_manager.get_state(connection_key)
    if not state:
        debug_log("No state found")
        return jsonify({"connected": False})
    
    state_info = {
        "connected": True,
        "workbook_name": state.workbook_name,
        "dashboard_name": state.dashboard_name,
        "available_views": len(state.available_views),
        "connection_age": state.get_connection_age(),
        "raw_data_available": state.raw_data is not None
    }
    
    debug_log("State retrieved successfully", state_info)
    return jsonify(state_info)

# Debug endpoint for troubleshooting
@app.route("/api/debug/status")
def debug_status():
    """Debug endpoint to check system status"""
    debug_log("Debug status endpoint called")
    
    try:
        # Get all active states
        active_states = []
        if hasattr(state_manager, '_states'):
            for key, state in state_manager._states.items():
                active_states.append({
                    "connection_key": key,
                    "workbook_name": state.workbook_name,
                    "dashboard_name": state.dashboard_name,
                    "views_count": len(state.available_views),
                    "has_raw_data": state.raw_data is not None,
                    "connection_age": state.get_connection_age(),
                    "chart_interactions": len(getattr(state, 'chart_interactions', []))
                })
        
        status = {
            "system": {
                "openai_configured": openai_client is not None,
                "debug_mode": DEBUG_MODE,
                "active_connections": len(active_states)
            },
            "connections": active_states
        }
        
        debug_log("Debug status compiled", status)
        return jsonify(status)
        
    except Exception as e:
        debug_log("Debug status exception", {"error": str(e)})
        return jsonify({
            "error": str(e),
            "system": {
                "openai_configured": openai_client is not None,
                "debug_mode": DEBUG_MODE
            }
        }), 500

@app.route("/api/log", methods=["POST"])
@function_logger('app.routes.log')
def handle_js_logging():
    """Handle logging requests from JavaScript frontend"""
    try:
        data = request.get_json(silent=True) or {}
        
        message = data.get('message', 'No message')
        level = data.get('level', 'INFO').upper()
        source = data.get('source', 'unknown')
        log_data = data.get('data', {})
        timestamp = data.get('timestamp', datetime.now().isoformat())
        
        # Log to master logger
        log_js_message(
            message=message,
            level=level,
            data=log_data,
            source=source
        )
        
        master_logger.info(f"JavaScript log received from {source}: {message}")
        
        return jsonify({"success": True, "logged": True})
        
    except Exception as e:
        master_logger.error(f"Failed to handle JavaScript logging: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/extension/log", methods=["POST"])
@function_logger('app.routes.extension_log')
def handle_extension_logging():
    """Enhanced logging endpoint specifically for Chrome extension events"""
    try:
        data = request.get_json(silent=True) or {}
        
        # Extract Chrome extension specific data
        event_type = data.get('event_type', 'GENERAL')
        message = data.get('message', 'No message')
        level = data.get('level', 'info').lower()
        url = data.get('url')
        user_agent = request.headers.get('User-Agent')
        extension_data = data.get('data', {})
        
        # Additional context from request
        remote_addr = request.environ.get('REMOTE_ADDR', 'unknown')
        
        # Enhanced data with request context
        enhanced_data = {
            **extension_data,
            'remote_addr': remote_addr,
            'request_headers': dict(request.headers),
            'timestamp_server': datetime.now().isoformat()
        }
        
        # Log to Chrome extension logger
        chrome_extension_logger.log_extension_event(
            level=level,
            event_type=event_type,
            message=message,
            data=enhanced_data,
            url=url,
            user_agent=user_agent
        )
        
        # Also log to master logger for cross-reference
        master_logger.info(f"Chrome Extension [{event_type}]: {message}")
        
        return jsonify({
            "success": True, 
            "logged": True,
            "log_file": "chrome_extension_debug.log",
            "event_type": event_type
        })
        
    except Exception as e:
        error_msg = f"Failed to handle Chrome extension logging: {e}"
        master_logger.error(error_msg)
        chrome_extension_logger.log_error('LOGGING_SYSTEM', error_msg, traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/user/log_frontend_data", methods=["POST"])
@function_logger('app.routes.user_frontend_data')
def log_user_frontend_data():
    """
    Endpoint to receive and store comprehensive user data from the frontend.
    This captures all available user information from the browser/Chrome extension context.
    Each user's data is stored as a separate object in user_frontend_data.json
    """
    master_logger.info("=== USER FRONTEND DATA LOGGING ENDPOINT CALLED ===")
    logger.info("=== USER FRONTEND DATA LOGGING ENDPOINT CALLED ===")
    chrome_extension_logger.log_extension_event(
        level='info',
        event_type='USER_DATA_EXTRACTION',
        message='User frontend data endpoint called',
        data={'timestamp': datetime.now().isoformat()}
    )
    
    try:
        user_data = request.get_json(silent=True) or {}
        
        master_logger.info(f"Received user data from frontend - size: {len(str(user_data))} characters")
        logger.info(f"Received user data from frontend - keys: {list(user_data.keys())}")
        
        # Add server-side metadata
        server_metadata = {
            'server_timestamp': datetime.now().isoformat(),
            'remote_addr': request.environ.get('REMOTE_ADDR', 'unknown'),
            'user_agent': request.headers.get('User-Agent'),
            'referer': request.headers.get('Referer')
        }
        
        # Merge with user data
        complete_user_data = {
            **user_data,
            'server_metadata': server_metadata
        }
        
        master_logger.info(f"Complete user data assembled with {len(complete_user_data)} top-level keys")
        
        # Define the file path for user data storage
        user_data_file = os.path.join(os.getcwd(), 'user_frontend_data.json')
        
        master_logger.info(f"Target file path: {user_data_file}")
        logger.info(f"Target file path: {user_data_file}")
        
        # Load existing data or create new array
        existing_users = []
        if os.path.exists(user_data_file):
            master_logger.info(f"User data file exists, loading existing data")
            try:
                with open(user_data_file, 'r', encoding='utf-8') as f:
                    existing_users = json.load(f)
                    if not isinstance(existing_users, list):
                        existing_users = [existing_users]
                master_logger.info(f"Loaded {len(existing_users)} existing user records")
            except Exception as e:
                master_logger.warning(f"Could not load existing user data file: {e}")
                logger.warning(f"Could not load existing user data file: {e}")
                existing_users = []
        else:
            master_logger.info("User data file does not exist, will create new file")
            logger.info("User data file does not exist, will create new file")
        
        # Append new user data
        existing_users.append(complete_user_data)
        
        master_logger.info(f"Appended new user data. Total users now: {len(existing_users)}")
        logger.info(f"Appended new user data. Total users now: {len(existing_users)}")
        
        # Save back to file with pretty printing
        try:
            master_logger.info(f"Writing user data to file: {user_data_file}")
            logger.info(f"Writing user data to file: {user_data_file}")
            
            with open(user_data_file, 'w', encoding='utf-8') as f:
                json.dump(existing_users, f, indent=2, ensure_ascii=False)
            
            # Verify file was created
            if os.path.exists(user_data_file):
                file_size = os.path.getsize(user_data_file)
                master_logger.info(f"✅ User data file created successfully: {user_data_file}")
                master_logger.info(f"✅ File size: {file_size} bytes")
                logger.info(f"✅ User data file created successfully: {user_data_file}")
                logger.info(f"✅ File size: {file_size} bytes")
            else:
                master_logger.error(f"❌ File was NOT created: {user_data_file}")
                logger.error(f"❌ File was NOT created: {user_data_file}")
            
            master_logger.info(f"User frontend data logged successfully. Total users: {len(existing_users)}")
            logger.info(f"User frontend data logged successfully. Total users: {len(existing_users)}")
            
            chrome_extension_logger.log_extension_event(
                level='info',
                event_type='USER_DATA_LOGGED',
                message=f"User data captured and stored - Total users: {len(existing_users)}",
                data={
                    'total_users_logged': len(existing_users),
                    'data_points_captured': len(complete_user_data),
                    'file_path': user_data_file,
                    'file_size_bytes': file_size if os.path.exists(user_data_file) else 0
                }
            )
            
            return jsonify({
                "success": True,
                "message": "User data logged successfully",
                "file_path": user_data_file,
                "total_users": len(existing_users),
                "data_points_captured": len(complete_user_data)
            })
            
        except Exception as e:
            error_msg = f"Failed to write user data to file: {e}"
            master_logger.error(error_msg)
            master_logger.error(traceback.format_exc())
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            chrome_extension_logger.log_error('USER_DATA_FILE_WRITE_ERROR', error_msg, traceback.format_exc())
            return jsonify({"success": False, "error": error_msg}), 500
    
    except Exception as e:
        error_msg = f"Failed to process user frontend data: {e}"
        master_logger.error(error_msg)
        master_logger.error(traceback.format_exc())
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        chrome_extension_logger.log_error('USER_DATA_PROCESSING_ERROR', error_msg, traceback.format_exc())
        return jsonify({"success": False, "error": str(e)}), 500
@app.route("/api/feedback", methods=["POST"])
@function_logger('app.routes.feedback')
def handle_feedback_submission():
    """Handle feedback submissions from Chrome extension and other clients"""
    try:
        # Import Google Sheets service
        from services.google_sheets_service import send_feedback_to_google_sheets
        data = request.get_json(silent=True) or {}
        
        # Extract feedback data
        rating = data.get('rating', 0)
        complaints = data.get('complaints', '').strip()
        improvements = data.get('improvements', '').strip()
        product_name = data.get('productName', 'Unknown Product')
        timestamp = data.get('timestamp') or datetime.now().isoformat()
        user_agent = request.headers.get('User-Agent', 'Unknown')
        url = data.get('url', request.headers.get('Referer', 'Unknown'))
        
        # Validate required data
        if rating == 0 and not complaints and not improvements:
            return jsonify({
                "success": False, 
                "error": "Please provide either a rating or some feedback text"
            }), 400
        
        # Prepare feedback entry
        feedback_entry = {
            'timestamp': timestamp,
            'product_name': product_name,
            'rating': rating,
            'complaints': complaints,
            'improvements': improvements,
            'url': url,
            'user_agent': user_agent,
            'remote_addr': request.environ.get('REMOTE_ADDR', 'unknown'),
            'session_id': session.get('id', 'anonymous')
        }
        
        # Store feedback (multiple storage methods for reliability)
        feedback_stored = False
        google_sheets_success = False
        
        # Method 0: Try to send to Google Sheets first (primary storage)
        try:
            master_logger.info("Attempting to send feedback to Google Sheets...")
            success, response_data, error_msg = send_feedback_to_google_sheets(feedback_entry)
            
            if success:
                master_logger.info("Successfully sent feedback to Google Sheets", extra={
                    'google_sheets_response': response_data,
                    'event_type': 'GOOGLE_SHEETS_SUCCESS'
                })
                google_sheets_success = True
                feedback_stored = True
            else:
                master_logger.warning(f"Failed to send feedback to Google Sheets: {error_msg}")
                
        except Exception as sheets_error:
            master_logger.error(f"Exception while sending to Google Sheets: {sheets_error}")
        
        # Method 1: Try to append to feedback JSON file
        try:
            feedback_file = 'feedback_submissions.json'
            feedback_list = []
            
            # Load existing feedback if file exists
            if os.path.exists(feedback_file):
                try:
                    with open(feedback_file, 'r', encoding='utf-8') as f:
                        feedback_list = json.load(f)
                        if not isinstance(feedback_list, list):
                            feedback_list = []
                except (json.JSONDecodeError, IOError):
                    feedback_list = []
            
            # Add new feedback
            feedback_list.append(feedback_entry)
            
            # Keep only last 1000 feedback entries to prevent file from growing too large
            if len(feedback_list) > 1000:
                feedback_list = feedback_list[-1000:]
            
            # Save updated feedback
            with open(feedback_file, 'w', encoding='utf-8') as f:
                json.dump(feedback_list, f, indent=2, ensure_ascii=False)
            
            feedback_stored = True
            master_logger.info(f"Feedback stored to file: {feedback_file}")
            
        except Exception as file_error:
            master_logger.warning(f"Failed to store feedback in file: {file_error}")
        
        # Method 2: Log feedback to master logger (always works as backup)
        try:
            master_logger.info(f"FEEDBACK RECEIVED - Product: {product_name}, Rating: {rating}/5", extra={
                'feedback_data': feedback_entry,
                'event_type': 'FEEDBACK_SUBMISSION'
            })
            
            # Also log to Chrome extension logger if from extension
            if 'chrome-extension' in user_agent.lower() or 'chrome extension' in product_name.lower():
                chrome_extension_logger.log_extension_event(
                    level='info',
                    event_type='FEEDBACK_SUBMISSION',
                    message=f"Feedback received - Rating: {rating}/5",
                    data=feedback_entry,
                    url=url,
                    user_agent=user_agent
                )
            
            feedback_stored = True
            
        except Exception as log_error:
            master_logger.error(f"Failed to log feedback: {log_error}")
        
        # Method 3: Try to save to a simple text log as last resort
        if not feedback_stored:
            try:
                with open('feedback_simple.log', 'a', encoding='utf-8') as f:
                    f.write(f"{timestamp} | {product_name} | Rating: {rating}/5 | URL: {url}\n")
                    if complaints:
                        f.write(f"  Complaints: {complaints}\n")
                    if improvements:
                        f.write(f"  Improvements: {improvements}\n")
                    f.write("---\n")
                feedback_stored = True
            except Exception as simple_log_error:
                master_logger.error(f"Failed to write simple feedback log: {simple_log_error}")
        
        if feedback_stored:
            # Create response with summary
            response_data = {
                "success": True,
                "message": "Thank you for your feedback!",
                "summary": {
                    "rating": rating,
                    "has_complaints": bool(complaints),
                    "has_improvements": bool(improvements),
                    "timestamp": timestamp,
                    "google_sheets_integrated": google_sheets_success
                }
            }
            
            # Log successful submission
            sheets_status = "✅ Google Sheets" if google_sheets_success else "❌ Google Sheets"
            master_logger.info(f"Feedback submission successful for {product_name} | {sheets_status}")
            
            return jsonify(response_data)
        else:
            # All storage methods failed
            master_logger.error("All feedback storage methods failed")
            return jsonify({
                "success": False,
                "error": "Failed to store feedback. Please try again later."
            }), 500
            
    except Exception as e:
        error_msg = f"Failed to handle feedback submission: {e}"
        master_logger.error(error_msg)
        return jsonify({
            "success": False, 
            "error": "An error occurred while processing your feedback"
        }), 500

@app.route("/api/feedback/google-sheets/test", methods=["GET"])
@function_logger('app.routes.test_google_sheets')
def test_google_sheets_connection():
    """Test the Google Sheets connection"""
    try:
        from services.google_sheets_service import test_google_sheets_connection
        
        success, response_data, error_msg = test_google_sheets_connection()
        
        if success:
            return jsonify({
                "success": True,
                "message": "Google Sheets connection successful",
                "data": response_data
            })
        else:
            return jsonify({
                "success": False,
                "error": error_msg or "Unknown error"
            }), 400
            
    except Exception as e:
        master_logger.error(f"Error testing Google Sheets connection: {e}")
        return jsonify({
            "success": False,
            "error": f"Exception during connection test: {str(e)}"
        }), 500

@app.route("/api/data-exploration/google-sheets/test", methods=["GET"])
@function_logger('app.routes.test_data_exploration_sheets')
def test_data_exploration_google_sheets():
    """Test the data exploration Google Sheets connection"""
    try:
        from services.google_sheets_service import test_data_exploration_google_sheets_connection
        
        success, response_data, error_msg = test_data_exploration_google_sheets_connection()
        
        if success:
            return jsonify({
                "success": True,
                "message": "Data exploration Google Sheets connection successful",
                "data": response_data
            })
        else:
            return jsonify({
                "success": False,
                "error": error_msg or "Unknown error"
            }), 400
            
    except Exception as e:
        master_logger.error(f"Error testing data exploration Google Sheets connection: {e}")
        return jsonify({
            "success": False,
            "error": f"Exception during connection test: {str(e)}"
        }), 500

@app.route("/api/data-exploration/test-log", methods=["POST"])
@function_logger('app.routes.test_data_exploration_log')
def test_data_exploration_log():
    """Test logging a data exploration entry"""
    try:
        from services.google_sheets_service import send_data_exploration_log_to_google_sheets
        from datetime import datetime
        
        # Get test data from request or use defaults
        data = request.get_json(silent=True) or {}
        
        test_log_data = {
            'timestamp': data.get('timestamp', datetime.now().isoformat()),
            'user_query': data.get('user_query', 'Test query: What is the total count of tickets?'),
            'generated_code': data.get('generated_code', 'df[df["status"]=="open"].count()'),
            'generated_answer': data.get('generated_answer', 'Total tickets: 150'),
            'execution_status': data.get('execution_status', 'success'),
            'chart_selected': data.get('chart_selected', 'test_chart'),
            'data_shape': data.get('data_shape', '(1000, 50)'),
            'session_id': data.get('session_id', 'test_session')
        }
        
        # Send to Google Sheets
        success, response_data, error_msg = send_data_exploration_log_to_google_sheets(test_log_data)
        
        if success:
            master_logger.info("Test data exploration log sent successfully")
            return jsonify({
                "success": True,
                "message": "Test log entry created successfully",
                "log_data": test_log_data,
                "google_sheets_response": response_data
            })
        else:
            master_logger.warning(f"Test data exploration log failed: {error_msg}")
            return jsonify({
                "success": False,
                "error": error_msg or "Failed to send test log",
                "log_data": test_log_data
            }), 400
            
    except Exception as e:
        master_logger.error(f"Error testing data exploration logging: {e}")
        return jsonify({
            "success": False,
            "error": f"Exception during test logging: {str(e)}"
        }), 500

@app.route("/api/feedback/google-sheets/status", methods=["GET"])
@function_logger('app.routes.google_sheets_status')
def get_google_sheets_status():
    """Get current Google Sheets integration status"""
    try:
        from services.google_sheets_service import google_sheets_service
        
        config = google_sheets_service.config.get('google_sheets', {})
        
        return jsonify({
            "success": True,
            "status": {
                "enabled": google_sheets_service.is_enabled(),
                "apps_script_url": config.get('apps_script_url', 'Not configured'),
                "timeout_seconds": config.get('timeout_seconds', 10),
                "retry_attempts": config.get('retry_attempts', 2),
                "fallback_on_failure": config.get('fallback_on_failure', True)
            }
        })
        
    except Exception as e:
        master_logger.error(f"Error getting Google Sheets status: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============================================================================
# CONTEXT MANAGER - USER DISAMBIGUATION ENDPOINT
# Context Manager by Aniket 1/12/2025
# ============================================================================

@app.route("/api/query/disambiguation", methods=["POST"])
def handle_user_disambiguation():
    """
    Handle user's selection from disambiguation UI (3-button flow)

    When multiple column/value matches are found, user is presented with 3 buttons.
    This endpoint receives the user's choice and caches it for future queries.

    Context Manager by Aniket 1/12/2025
    """
    try:
        data = request.json
        master_logger.info("[CONTEXT_MGR] User disambiguation received")
        master_logger.info(f"[CONTEXT_MGR] Data: {json.dumps(data, indent=2)}")

        # Extract parameters
        selected_value = data.get('selected_value')
        column_name = data.get('column_name')
        original_value = data.get('original_value')
        session_id = data.get('session_id')
        source_id = data.get('source_id')
        original_query = data.get('original_query')  # The query that triggered disambiguation

        if not all([selected_value, column_name, original_value, session_id, source_id]):
            return jsonify({
                'success': False,
                'error': 'Missing required parameters'
            }), 400

        # 🆕 NORMALIZE the original_value for consistent cache keys
        # This ensures "March", "march", "  March  " all map to same cache entry
        def normalize_cache_key(value):
            """Normalize cache key: lowercase, trim, remove extra spaces"""
            if not isinstance(value, str):
                value = str(value)
            normalized = value.lower().strip()
            normalized = ' '.join(normalized.split())  # Remove extra internal spaces
            return normalized

        normalized_original = normalize_cache_key(original_value)
        master_logger.info(f"[CACHE_NORM_WRITE] Original: '{original_value}' → Normalized: '{normalized_original}'")

        # 🆕 LANGGRAPH APPROACH: Write to class-level cache in NLToPythonGeneratorV5
        # Context Manager by Aniket 1/12/2025
        from services.nlp_to_python.nl_to_python_workflow import NLToPythonGeneratorV5

        # Write to the global class-level cache (with normalized key)
        import time
        cache_key = (session_id, source_id, column_name, normalized_original)
        NLToPythonGeneratorV5._global_disambiguation_cache[cache_key] = selected_value
        NLToPythonGeneratorV5._cache_timestamps[cache_key] = time.time()  # Track timestamp for re-run detection
        master_logger.info(f"[LANGGRAPH_CACHE] 💾 WRITE: {cache_key} → {selected_value}")

        # ALSO: Write to legacy session manager for backward compatibility (with normalized key)
        global_session_manager.update_disambiguation_cache(
            session_id,
            source_id,
            column_name,
            normalized_original,
            selected_value
        )

        master_logger.info(f"[CONTEXT_MGR] ✅ Cached user choice: '{original_value}' → '{selected_value}'")
        master_logger.info(f"[CONTEXT_MGR] Column: {column_name}, Session: {session_id}, Source: {source_id}")

        # Re-run the original query with cached disambiguation
        # The query will now find the cached value and proceed without raising exception
        if original_query:
            master_logger.info(f"[CONTEXT_MGR] Re-running query: {original_query}")

            # TODO: Re-execute the NL to Python workflow with the cached choice
            # This will be implemented when the main query endpoint is identified
            # For now, return success and let the client re-send the query

            return jsonify({
                'success': True,
                'message': f"Using '{selected_value}' for your query",
                'cached_choice': {
                    'original_value': original_value,
                    'selected_value': selected_value,
                    'column_name': column_name
                },
                'action': 'rerun_query',  # Signal to client to re-send the query
                'session_id': session_id,
                'source_id': source_id
            })
        else:
            return jsonify({
                'success': True,
                'message': f"Choice cached. Your next query will use '{selected_value}'",
                'cached_choice': {
                    'original_value': original_value,
                    'selected_value': selected_value,
                    'column_name': column_name
                }
            })

    except Exception as e:
        master_logger.error(f"[CONTEXT_MGR] Error handling user disambiguation: {e}")
        master_logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


def cleanup_old_connections():
    """Background task to cleanup old connections"""
    debug_log("Starting connection cleanup thread")
    
    while True:
        try:
            debug_log("Running connection cleanup")
            state_manager.cleanup_old_states(max_age_hours=2)
            time.sleep(CONNECTION_REFRESH_INTERVAL)
        except Exception as e:
            debug_log("Error in connection cleanup", {"error": str(e)})
            time.sleep(60)  # Wait 1 minute before retrying

if __name__ == "__main__":
    master_logger.info("=== FLASK APPLICATION STARTUP ===")
    master_logger.info(f"Starting Flask application at {datetime.now()}")
    
    # Start background cleanup thread
    master_logger.info("Starting background cleanup thread")
    debug_log("Starting background cleanup thread")
    cleanup_thread = threading.Thread(target=cleanup_old_connections, daemon=True)
    cleanup_thread.start()
    master_logger.info("Background cleanup thread started successfully")
    
    # Hard-disable any env-based debug/reloader that might be set
    os.environ.pop("FLASK_DEBUG", None)
    os.environ.pop("FLASK_ENV", None)
    os.environ["FLASK_RUN_RELOAD"] = "false"
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8502, help="Port to run the app on (default: 8502)")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    parser.add_argument("--reload", action="store_true", help="Enable Flask reloader")
    args = parser.parse_args()

    # Force disable reloader unless explicitly requested
    use_reloader = args.reload if hasattr(args, 'reload') else False
    debug = args.debug if hasattr(args, 'debug') else False
    
    master_logger.info(f"Flask startup configuration - port: {args.port}, debug: {debug}, reloader: {use_reloader}")
    
    debug_log("Starting Flask application", {
        "port": args.port,
        "debug": debug,
        "reloader": use_reloader
    })
    
    startup_message = f"🚀 Starting Flask with Tableau Integration on http://127.0.0.1:{args.port}"
    print(startup_message)
    master_logger.info(startup_message)
    
    config_info = [
        f"   Debug: {debug}",
        f"   Reloader: {use_reloader}",
        f"   Debug Mode: {DEBUG_MODE}",
        f"   Log File: chatbot_debug.log",
        f"   Master Log File: master_debug.log",
        f"   Auto-Export: {'Enabled' if AUTO_EXPORT_ENABLED else 'Disabled'}",
        f"   Auto-Export CSV: {'Yes' if AUTO_EXPORT_CSV else 'No'}",
        f"   Auto-Export Metadata: {'Yes' if AUTO_EXPORT_METADATA else 'No'}"
    ]
    
    for line in config_info:
        print(line)
        master_logger.info(line.strip())
    
    try:
        master_logger.info("Starting Flask server...")
        app.run(
            host="0.0.0.0",
            port=args.port,
            debug=debug,
            use_reloader=use_reloader,
            threaded=True
        )
    except KeyboardInterrupt:
        master_logger.info("Flask app stopped by user (KeyboardInterrupt)")
        debug_log("Flask app stopped by user")
        print("\n👋 Flask app stopped")
    except Exception as e:
        master_logger.error(f"Flask startup exception: {type(e).__name__}: {str(e)}")
        debug_log("Flask startup exception", {"error": str(e)})
        print(f"⚠ Error starting Flask: {e}")
        sys.exit(1)