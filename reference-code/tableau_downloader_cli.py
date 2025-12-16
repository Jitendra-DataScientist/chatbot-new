"""
Tableau Backend Integration Module
Combines the robust Tableau connection logic with Flask-compatible state management
"""

import pandas as pd
import polars as pl
import requests
import xml.etree.ElementTree as ET
import urllib.parse
import json
import warnings
import urllib3
import traceback
import socket
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple, Any
from io import StringIO
import time
import threading
from datetime import datetime, timedelta
from functools import wraps
import os
from fuzzywuzzy import fuzz

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()  # Load .env file into environment variables
except ImportError:
    # dotenv not installed, will use system environment variables only
    pass

# Disable proxy for corporate network compatibility - Fix for connection issues
os.environ['NO_PROXY'] = '*'
for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    os.environ.pop(key, None)
import re
import asyncio
import zipfile
import tempfile
import shutil
from pathlib import Path

# Simple print-based logger for CLI (no master_logger dependency)
class SimplePrintLogger:
    """Simple logger that just prints to console"""
    def info(self, msg): pass  # Silent for clean output
    def debug(self, msg): pass  # Silent
    def warning(self, msg): print(f"⚠️  {msg}")
    def error(self, msg): print(f"❌ {msg}")

def function_logger(name):
    """Decorator that does nothing - just for compatibility"""
    def decorator(func):
        return func
    return decorator

def setup_module_logger(name):
    """Returns a simple print logger"""
    return SimplePrintLogger()

get_master_logger = setup_module_logger  # Alias for compatibility

# Import enhanced services
try:
    from services.data_processor import TableauDataProcessor as EnhancedDataProcessor
    from services.llm_service import LLMService
    from RAG.vector_store import TableauVectorStore
    from models.schemas import TableauWorkbook, TableauWorksheet, WorkbookDataSummary
    from services.csv_calculated_fields_enhancer import CSVCalculatedFieldsEnhancer
    ENHANCED_SERVICES_AVAILABLE = True
except ImportError as e:
    print(f"Enhanced services not available: {e}")
    ENHANCED_SERVICES_AVAILABLE = False

warnings.simplefilter("ignore")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Setup master logger for comprehensive debugging
master_logger = setup_module_logger('tableau_backend')
master_logger.info("TABLEAU BACKEND MODULE INITIALIZATION STARTED")
master_logger.info("Warnings suppressed for urllib3 SSL verification")


# ============================================================================
# EMBEDDED TABLEAU PAT SERVICE (from tableau_pat_service.py)
# ============================================================================

import yaml

class TableauPATService:
    """Service for fetching Tableau PAT credentials from uSecret (secrets.yaml)"""

    def __init__(self, secrets_path: Optional[str] = None):
        """
        Initialize the PAT service with path to secrets.yaml

        Args:
            secrets_path: Path to secrets.yaml. If None, uses default Uber path for tableau_chatbot service
        """
        if secrets_path is None:
            # Default path for Up services - using tableau_chatbot service
            service_name = os.getenv('SERVICE_NAME', 'tableau_chatbot')
            self.secrets_path = f'/langley/udocker/{service_name}/current/secrets.yaml'
        else:
            self.secrets_path = secrets_path

        self._secrets_cache = None
        self._cache_timestamp = None
        self.cache_duration_seconds = 300  # 5 minutes

    def _load_secrets(self) -> Dict[str, Any]:
        """Load secrets from secrets.yaml file"""
        try:
            with open(self.secrets_path, 'r') as f:
                secrets = yaml.safe_load(f)
            return secrets
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Secrets file not found at {self.secrets_path}. "
                "Ensure your service is deployed and secrets are configured in uSecret.v2 at https://secrets.uberinternal.com"
            )
        except Exception as e:
            raise Exception(f"Error loading secrets: {str(e)}")

    def _is_cache_valid(self) -> bool:
        """Check if cache is valid"""
        if self._cache_timestamp is None or self._secrets_cache is None:
            return False
        age = (datetime.now() - self._cache_timestamp).total_seconds()
        return age < self.cache_duration_seconds

    def _get_secrets(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Get secrets with caching"""
        if not force_refresh and self._is_cache_valid():
            return self._secrets_cache

        secrets = self._load_secrets()
        self._secrets_cache = secrets
        self._cache_timestamp = datetime.now()
        return secrets

    def extract_site_content_url_from_url(self, tableau_url: str) -> Optional[str]:
        """Extract site_content_url from Tableau URL"""
        patterns = [r'/#/site/([^/]+)', r'/t/([^/]+)', r'/site/([^/]+)']
        for pattern in patterns:
            match = re.search(pattern, tableau_url)
            if match:
                return match.group(1).strip()
        return None

    def get_credentials_by_site_content_url(self, site_content_url: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Fetch Tableau PAT credentials by site_content_url from secrets.yaml

        Expected secrets.yaml structure:
        tableau_credentials:
          site1:
            tableau_server_url: "https://tableau.uberinternal.com"
            api_version: "3.19"
            site_content_url: "site1"
            personal_access_token_name: "token_name"
            personal_access_token_secret: "token_secret"
          site2:
            ...
        """
        try:
            secrets = self._get_secrets()

            # Navigate to tableau_credentials section
            if 'tableau_credentials' not in secrets:
                return False, None, "No 'tableau_credentials' section found in secrets.yaml"

            tableau_creds = secrets['tableau_credentials']

            # Find matching site
            for site_key, site_config in tableau_creds.items():
                if site_config.get('site_content_url') == site_content_url:
                    credentials = {
                        'tableau_server_url': site_config.get('tableau_server_url', '').strip(),
                        'api_version': str(site_config.get('api_version', '3.19')).strip(),
                        'site_content_url': site_content_url.strip()
                    }

                    # PAT credentials
                    pat_name = site_config.get('personal_access_token_name', '').strip()
                    pat_secret = site_config.get('personal_access_token_secret', '').strip()

                    if pat_name and pat_secret:
                        credentials['personal_access_token_name'] = pat_name
                        credentials['personal_access_token_secret'] = pat_secret
                        credentials['auth_type'] = 'personal_access_token'
                        return True, credentials, None

            return False, None, f"No credentials found for site: {site_content_url}"

        except Exception as e:
            return False, None, f"Error fetching credentials: {str(e)}"

    def get_all_credentials_by_site_content_url(self, site_content_url: str) -> Tuple[bool, Optional[List[Dict[str, Any]]], Optional[str]]:
        """Fetch ALL credentials for a site (for fallback support)"""
        try:
            secrets = self._get_secrets()

            if 'tableau_credentials' not in secrets:
                return False, None, "No 'tableau_credentials' section found in secrets.yaml"

            tableau_creds = secrets['tableau_credentials']
            credentials_list = []

            for site_key, site_config in tableau_creds.items():
                if site_config.get('site_content_url') == site_content_url:
                    credentials = {
                        'tableau_server_url': site_config.get('tableau_server_url', '').strip(),
                        'api_version': str(site_config.get('api_version', '3.19')).strip(),
                        'site_content_url': site_content_url.strip(),
                        '_site_key': site_key
                    }

                    pat_name = site_config.get('personal_access_token_name', '').strip()
                    pat_secret = site_config.get('personal_access_token_secret', '').strip()

                    if pat_name and pat_secret:
                        credentials['personal_access_token_name'] = pat_name
                        credentials['personal_access_token_secret'] = pat_secret
                        credentials['auth_type'] = 'personal_access_token'
                        credentials_list.append(credentials)

            if not credentials_list:
                return False, None, f"No valid credentials found for site: {site_content_url}"

            return True, credentials_list, None

        except Exception as e:
            return False, None, f"Error fetching credentials: {str(e)}"

# Global instance
tableau_pat_service = TableauPATService()


# ============================================================================
# CUSTOM EXCEPTIONS FOR BETTER ERROR HANDLING
# ============================================================================

class TableauConnectionError(Exception):
    """Base exception for Tableau connection issues"""
    pass

class NetworkError(TableauConnectionError):
    """Network connectivity issues"""
    def __init__(self, message, suggestions=None):
        super().__init__(message)
        self.suggestions = suggestions or [
            "Connect to corporate VPN",
            "Check internet connection",
            "Verify firewall settings"
        ]

class AuthenticationError(TableauConnectionError):
    """Authentication failures"""
    pass


# ============================================================================
# PHASE 1: TWB PARSING - METADATA EXTRACTION CLASSES
# ============================================================================

@dataclass
class ChartMetadata:
    """Metadata for a single chart/worksheet"""
    worksheet_name: str
    x_axis: List[str] = field(default_factory=list)
    y_axis: List[str] = field(default_factory=list)
    color_encoding: Optional[str] = None
    size_encoding: Optional[str] = None
    detail_fields: List[str] = field(default_factory=list)
    mark_type: str = "Automatic"
    filters: List[Dict[str, Any]] = field(default_factory=list)
    calculated_fields: List[Dict[str, Any]] = field(default_factory=list)
    datasources_used: List[str] = field(default_factory=list)
    sort_info: Optional[Dict[str, Any]] = None

@dataclass
class FieldMetadata:
    """Metadata for a field/column"""
    internal_name: str
    caption: str
    datatype: str
    role: str
    field_type: str
    aggregation: Optional[str] = None
    formula: Optional[str] = None
    aliases: Dict[str, str] = field(default_factory=dict)
    is_calculated: bool = False
    is_hidden: bool = False
    description: Optional[str] = None

@dataclass
class DatasourceMetadata:
    """Metadata for a datasource"""
    name: str
    caption: str
    connection_type: str
    server: Optional[str] = None
    dbname: Optional[str] = None
    custom_sql: Optional[str] = None
    fields: List[FieldMetadata] = field(default_factory=list)
    extract_path: Optional[str] = None

@dataclass
class FilterMetadata:
    """Metadata for filters"""
    field_name: str
    filter_type: str
    values: List[Any] = field(default_factory=list)
    is_exclude: bool = False
    min_value: Optional[Any] = None
    max_value: Optional[Any] = None

@dataclass
class BlendRelationship:
    """Metadata for datasource blends"""
    primary_datasource: str
    secondary_datasource: str
    linking_fields: List[Tuple[str, str]] = field(default_factory=list)

# Load configuration
def get_env_variable(key: str, default: str = "") -> str:
    """Get environment variable with fallback to default"""
    try:
        # Try to read from .env file first
        if os.path.exists('.env'):
            with open('.env', 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        env_key, env_value = line.split('=', 1)
                        if env_key.strip() == key:
                            return env_value.strip()
        
        # Fallback to system environment variables
        return os.getenv(key, default)
    except Exception as e:
        master_logger.warning(f"Error reading environment variable {key}: {e}")
        return default

@function_logger('tableau_backend.load_tableau_config')
def load_tableau_config(filepath: str = None, site_content_url: str = None, tableau_url: str = None, get_all_fallbacks: bool = False) -> Dict:
    """
    Loads Tableau configuration from Google Sheets PAT credentials service

    This function now uses the direct Google Sheets API to fetch credentials by site_content_url:
    - Fetches credentials from Google Sheets by site_content_url lookup
    - Supports extracting site_content_url from Tableau URLs
    - Includes 5-minute in-memory caching for performance
    - Provides comprehensive logging and error handling
    - Supports fallback credentials (multiple credentials for same site)

    Args:
        filepath: DEPRECATED - Kept for backwards compatibility, falls back to tableau_config.json if needed
        site_content_url: The site_content_url to lookup (e.g., "uMetricAnalytics")
        tableau_url: Full Tableau URL to extract site_content_url from
        get_all_fallbacks: If True, returns ALL credentials for the site (for fallback support)

    Returns:
        If get_all_fallbacks=False:
            Dict containing single Tableau configuration
        If get_all_fallbacks=True:
            List of Dict containing all credentials for the site (ordered by priority)
    """
    # tableau_pat_service is now embedded in this file (see above)

    master_logger.info("[CONFIG] Configuration mode: Google Sheets PAT Service (Direct API)")
    master_logger.info("[CONFIG] Fetching credentials from Google Sheets...")

    try:
        # Priority 1: Use provided site_content_url
        if site_content_url:
            master_logger.info(f"Using provided site_content_url: {site_content_url}")

        # Priority 2: Extract from provided Tableau URL
        elif tableau_url:
            master_logger.info(f"Extracting site_content_url from URL: {tableau_url}")
            site_content_url = tableau_pat_service.extract_site_content_url_from_url(tableau_url)
            if not site_content_url:
                raise ValueError(f"Could not extract site_content_url from URL: {tableau_url}")
            master_logger.info(f"Extracted site_content_url: {site_content_url}")

        # Priority 3: Check environment variable
        elif get_env_variable('TABLEAU_SITE_CONTENT_URL', None):
            site_content_url = get_env_variable('TABLEAU_SITE_CONTENT_URL')
            master_logger.info(f"Using site_content_url from environment: {site_content_url}")

        # Priority 4: Fall back to tableau_config.json
        else:
            master_logger.warning("No site_content_url or tableau_url provided, falling back to tableau_config.json")
            if filepath is None:
                filepath = 'tableau_config.json'

            if os.path.exists(filepath):
                with open(filepath, 'r') as f:
                    config = json.load(f)
                master_logger.info(f"[FALLBACK] Loaded configuration from {filepath}")

                # Ensure auth_type is set
                if 'auth_type' not in config:
                    if 'personal_access_token_name' in config:
                        config['auth_type'] = 'personal_access_token'
                    elif 'username' in config:
                        config['auth_type'] = 'username_password'

                return config
            else:
                raise FileNotFoundError(
                    f"No site_content_url/tableau_url provided and fallback file not found: {filepath}. "
                    f"Please provide site_content_url, tableau_url, or set TABLEAU_SITE_CONTENT_URL env variable."
                )

        # Fetch credentials from Google Sheets by site_content_url
        if get_all_fallbacks:
            # Get ALL credentials for fallback support
            master_logger.info(f"Fetching ALL credentials for {site_content_url} (fallback mode enabled)")
            success, credentials_list, error = tableau_pat_service.get_all_credentials_by_site_content_url(site_content_url)

            if not success:
                raise RuntimeError(f"Failed to fetch credentials from Google Sheets: {error}")

            master_logger.info(f"[SUCCESS] Loaded {len(credentials_list)} credential(s) for fallback")
            return credentials_list  # Return list of credentials

        else:
            # Get single credential (first match)
            success, config, error = tableau_pat_service.get_credentials_by_site_content_url(site_content_url)

            if not success:
                raise RuntimeError(f"Failed to fetch credentials from Google Sheets: {error}")

        master_logger.info("[SUCCESS] Tableau configuration loaded successfully from Google Sheets")
        master_logger.debug(f"Configuration keys: {list(config.keys())}")

        # Log important configuration details (without sensitive info)
        if 'tableau_server_url' in config:
            master_logger.info(f"Tableau server URL: {config['tableau_server_url']}")
        if 'api_version' in config:
            master_logger.info(f"API version: {config['api_version']}")
        if 'site_content_url' in config:
            master_logger.info(f"Site content URL: {config['site_content_url']}")

        # Log authentication type
        auth_type = config.get('auth_type', 'unknown')
        master_logger.info(f"Authentication type: {auth_type}")

        if auth_type == "username_password" and 'username' in config:
            master_logger.info(f"Username configured: {config['username']}")
        elif auth_type == "personal_access_token" and 'personal_access_token_name' in config:
            master_logger.info(f"PAT name configured: {config['personal_access_token_name']}")

        return config

    except Exception as e:
        master_logger.error(f"[FAILED] Failed to load Tableau configuration: {e}")
        master_logger.error(traceback.format_exc())
        print(f"CRITICAL ERROR: Failed to load Tableau configuration: {e}")
        print("Please check:")
        print("  1. Google Sheet contains credentials for the site_content_url")
        print("  2. Google Sheets API credentials (token.pickle, credentials.json) are valid")
        print("  3. The 'Credentials' sheet has the correct columns")
        print("  4. Or provide a valid tableau_config.json as fallback")
        raise RuntimeError(f"Failed to load Tableau credentials: {e}")

# For CLI mode, TABLEAU_CONFIG will be loaded dynamically from URL
# Set a placeholder that will be replaced when CLI runs
TABLEAU_CONFIG = None
master_logger.info("Tableau configuration will be loaded dynamically")

# Retry decorator for handling network issues
@function_logger('tableau_backend.retry_decorator')
def retry_on_failure(max_retries=3, backoff_factor=1.0):
    """Decorator for retrying functions with exponential backoff"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            func_name = f"{func.__name__}"
            master_logger.debug(f"Retry wrapper called for {func_name} with max_retries={max_retries}")
            
            last_exception = None
            for attempt in range(max_retries):
                try:
                    master_logger.debug(f"Attempt {attempt + 1}/{max_retries} for {func_name}")
                    result = func(*args, **kwargs)
                    
                    if attempt > 0:
                        master_logger.info(f"{func_name} succeeded on attempt {attempt + 1}")
                    
                    return result
                    
                except Exception as e:
                    last_exception = e
                    master_logger.warning(f"Attempt {attempt + 1} failed for {func_name}: {type(e).__name__}: {str(e)}")
                    
                    if attempt < max_retries - 1:
                        wait_time = backoff_factor * (2 ** attempt)
                        master_logger.info(f"Retrying {func_name} in {wait_time} seconds...")
                        print(f"Attempt {attempt + 1} failed: {str(e)}. Retrying in {wait_time} seconds...")
                        time.sleep(wait_time)
                    else:
                        master_logger.error(f"All {max_retries} attempts failed for {func_name}. Final error: {str(e)}")
                        print(f"All {max_retries} attempts failed. Last error: {str(e)}")
                        
            raise last_exception
        return wrapper
    return decorator

@dataclass
class ChatState:
    """Enhanced chat state with connection tracking and chart interactions"""
    # Connection info
    auth_token: Optional[str] = None
    site_id: Optional[str] = None
    workbook_id: Optional[str] = None
    workbook_name: Optional[str] = None
    dashboard_name: Optional[str] = None
    
    # Data
    available_views: List[dict] = field(default_factory=list)
    raw_data: Optional[pd.DataFrame] = None
    
    # Chart interactions
    chart_interactions: List[Dict] = field(default_factory=list)
    active_worksheet: Optional[str] = None
    selected_data: Optional[List[Dict]] = None
    
    # Metadata
    connection_timestamp: Optional[datetime] = None
    last_activity: Optional[datetime] = None
    
    def __post_init__(self):
        if self.connection_timestamp is None:
            self.connection_timestamp = datetime.utcnow()
        self.last_activity = datetime.utcnow()
        
        master_logger.debug(f"ChatState initialized - connection_timestamp: {self.connection_timestamp}")
    
    def update_activity(self):
        """Update last activity timestamp"""
        old_activity = self.last_activity
        self.last_activity = datetime.utcnow()
        master_logger.debug(f"ChatState activity updated - previous: {old_activity}, new: {self.last_activity}")
    
    def add_chart_interaction(self, worksheet_name: str, selected_data: List[Dict], interaction_type: str = "click"):
        """Add a chart interaction to the state"""
        master_logger.info(f"Adding chart interaction - worksheet: {worksheet_name}, type: {interaction_type}")
        master_logger.debug(f"Selected data count: {len(selected_data)}")
        
        interaction = {
            "timestamp": datetime.utcnow().isoformat(),
            "worksheet": worksheet_name,
            "selected_data": selected_data,
            "interaction_type": interaction_type
        }
        
        if not self.chart_interactions:
            self.chart_interactions = []
            master_logger.debug("Initialized chart_interactions list")
        
        self.chart_interactions.append(interaction)
        interactions_count_before = len(self.chart_interactions)
        
        # Keep only last 10 interactions
        self.chart_interactions = self.chart_interactions[-10:]
        interactions_count_after = len(self.chart_interactions)
        
        if interactions_count_before != interactions_count_after:
            master_logger.debug(f"Trimmed chart interactions from {interactions_count_before} to {interactions_count_after}")
        
        # Update active context
        self.active_worksheet = worksheet_name
        self.selected_data = selected_data
        master_logger.debug(f"Updated active context - worksheet: {worksheet_name}")
        
        self.update_activity()
    
    def get_latest_interaction(self) -> Optional[Dict]:
        """Get the most recent chart interaction"""
        if self.chart_interactions:
            return self.chart_interactions[-1]
        return None
    
    def is_connection_fresh(self, max_age_minutes: int = 30) -> bool:
        """Check if connection is still fresh"""
        if not self.connection_timestamp:
            return False
        age = datetime.utcnow() - self.connection_timestamp
        return age.total_seconds() < (max_age_minutes * 60)
    
    def get_connection_age(self) -> Dict[str, Any]:
        """Get connection age information"""
        if not self.connection_timestamp:
            return {"age_minutes": None, "is_fresh": False}
        
        age = datetime.utcnow() - self.connection_timestamp
        age_minutes = age.total_seconds() / 60
        
        return {
            "age_minutes": round(age_minutes, 1),
            "is_fresh": self.is_connection_fresh(),
            "connected_at": self.connection_timestamp.isoformat()
        }


# ============================================================================
# TWB PARSER CLASS - COMPLETE METADATA EXTRACTION
# ============================================================================

class TWBParser:
    """Parse TWB/TWBX files to extract complete metadata"""
    
    def __init__(self):
        master_logger.info("Initializing TWBParser")
        self.workbook_xml = None
        self.charts: Dict[str, ChartMetadata] = {}
        self.datasources: Dict[str, DatasourceMetadata] = {}
        self.blends: List[BlendRelationship] = []
        
        # Global collection of ALL fields with descriptions (from any source)
        self.all_fields_with_descriptions: List[FieldMetadata] = []
    
    def _get_or_create_metadata_dir(self, workbook_name: str, base_dir: str) -> str:
        """
        Get existing metadata directory for workbook or create a new one.
        Reuses existing directories instead of creating new timestamped ones.
        """
        try:
            # Ensure base directory exists
            os.makedirs(base_dir, exist_ok=True)
            
            # Look for existing directories with the same workbook name
            existing_dirs = []
            if os.path.exists(base_dir):
                for item in os.listdir(base_dir):
                    item_path = os.path.join(base_dir, item)
                    if os.path.isdir(item_path) and item.startswith(workbook_name):
                        existing_dirs.append(item_path)
            
            if existing_dirs:
                # Use the most recent existing directory
                existing_dir = max(existing_dirs, key=os.path.getmtime)
                master_logger.info(f"Reusing existing metadata directory: {existing_dir}")
                return existing_dir
            else:
                # Create new directory without timestamp for consistency
                output_dir = os.path.join(base_dir, workbook_name)
                os.makedirs(output_dir, exist_ok=True)
                master_logger.info(f"Created new metadata directory: {output_dir}")
                return output_dir
                
        except Exception as e:
            master_logger.error(f"Error in _get_or_create_metadata_dir: {e}")
            # Fallback to timestamped directory
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            fallback_dir = os.path.join(base_dir, f"{workbook_name}_{timestamp}")
            os.makedirs(fallback_dir, exist_ok=True)
            return fallback_dir
    
    @function_logger('tableau_backend.TWBParser.parse_twbx')
    def parse_twbx(self, twbx_path: str) -> bool:
        """Extract and parse TWBX package"""
        try:
            master_logger.info(f"Parsing TWBX file: {twbx_path}")
            
            with tempfile.TemporaryDirectory() as temp_dir:
                with zipfile.ZipFile(twbx_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)
                
                twb_files = list(Path(temp_dir).glob('*.twb'))
                if not twb_files:
                    master_logger.error("No TWB file found in TWBX package")
                    return False
                
                twb_path = str(twb_files[0])
                return self.parse_twb(twb_path)
                
        except Exception as e:
            master_logger.error(f"Error parsing TWBX: {e}")
            return False
    
    @function_logger('tableau_backend.TWBParser.parse_twb')
    def parse_twb(self, twb_path: str) -> bool:
        """Parse TWB XML file"""
        try:
            master_logger.info(f"Parsing TWB file: {twb_path}")
            
            tree = ET.parse(twb_path)
            self.workbook_xml = tree.getroot()
            
            self._parse_datasources()
            self._parse_worksheets()
            self._parse_blends()
            
            # Debug: Scan XML for field descriptions
            self._debug_xml_descriptions()
            
            # Build universal calculation ID mapping after parsing
            self._comprehensive_calc_mapping = self.build_comprehensive_calc_mapping()
            
            master_logger.info(f"TWB parsing complete - {len(self.charts)} charts, {len(self.datasources)} datasources")
            return True
            
        except Exception as e:
            master_logger.error(f"Error parsing TWB: {e}")
            return False
    
    def _parse_datasources(self):
        """Parse datasource definitions"""
        try:
            datasource_elements = self.workbook_xml.findall('.//datasource')
            master_logger.info(f"🔍 Found {len(datasource_elements)} datasource elements in TWB")
            
            for ds_elem in datasource_elements:
                ds_name = ds_elem.get('name', '')
                master_logger.debug(f"Processing datasource: {ds_name}")
                
                if not ds_name or ds_name.startswith('Parameters'):
                    master_logger.debug(f"Skipping datasource: {ds_name} (parameters or empty)")
                    continue
                
                ds_metadata = DatasourceMetadata(
                    name=ds_name,
                    caption=ds_elem.get('caption', ds_name),
                    connection_type='unknown'
                )
                
                connection = ds_elem.find('.//connection')
                if connection is not None:
                    ds_metadata.connection_type = connection.get('class', 'unknown')
                    ds_metadata.server = connection.get('server')
                    ds_metadata.dbname = connection.get('dbname')
                    
                    relation = connection.find('.//relation')
                    if relation is not None and relation.get('type') == 'text':
                        ds_metadata.custom_sql = relation.text
                
                # Find columns
                column_elements = ds_elem.findall('.//column')
                master_logger.info(f"Datasource '{ds_name}': Found {len(column_elements)} column elements")
                
                fields_parsed = 0
                for col_elem in column_elements:
                    field = self._parse_field(col_elem)
                    if field:
                        ds_metadata.fields.append(field)
                        fields_parsed += 1
                
                master_logger.info(f"Datasource '{ds_name}': Successfully parsed {fields_parsed}/{len(column_elements)} fields")
                self.datasources[ds_name] = ds_metadata
                
        except Exception as e:
            master_logger.error(f"Error parsing datasources: {e}")
            master_logger.error(traceback.format_exc())
    
    def _parse_field(self, col_elem) -> Optional[FieldMetadata]:
        """Parse a single field/column"""
        try:
            internal_name = col_elem.get('name', '')
            if not internal_name:
                return None
            
            field = FieldMetadata(
                internal_name=internal_name,
                caption=col_elem.get('caption', internal_name),
                datatype=col_elem.get('datatype', 'string'),
                role=col_elem.get('role', 'dimension'),
                field_type=col_elem.get('type', 'nominal')
            )
            
            calculation = col_elem.find('.//calculation')
            if calculation is not None:
                field.is_calculated = True
                field.formula = calculation.get('formula', '')
            
            field.aggregation = col_elem.get('aggregation')
            
            # Extract field description/comment - Enhanced with multiple strategies
            field.description = None
            
            # Strategy 1: Look for <desc><formatted-text><run> structure
            desc_elem = col_elem.find('.//desc')
            if desc_elem is not None:
                # Look for formatted-text within desc
                formatted_text = desc_elem.find('.//formatted-text')
                if formatted_text is not None:
                    run_elem = formatted_text.find('.//run')
                    if run_elem is not None and run_elem.text:
                        field.description = run_elem.text.strip()
                        master_logger.debug(f"✓ Found desc>formatted-text>run description for {internal_name}: {field.description}")
                
                # Alternative: Direct run under desc
                if not field.description:
                    run_elem = desc_elem.find('.//run')
                    if run_elem is not None and run_elem.text:
                        field.description = run_elem.text.strip()
                        master_logger.debug(f"✓ Found desc>run description for {internal_name}: {field.description}")
                
                # Alternative: Text directly in desc
                if not field.description and desc_elem.text and desc_elem.text.strip():
                    field.description = desc_elem.text.strip()
                    master_logger.debug(f"✓ Found direct desc text for {internal_name}: {field.description}")
            
            # Strategy 2: Look for direct formatted-text (outside desc)
            if not field.description:
                formatted_text = col_elem.find('.//formatted-text')
                if formatted_text is not None:
                    run_elem = formatted_text.find('.//run')
                    if run_elem is not None and run_elem.text:
                        field.description = run_elem.text.strip()
                        master_logger.debug(f"✓ Found formatted-text>run description for {internal_name}: {field.description}")
            
            # Strategy 3: Look for any run element in column
            if not field.description:
                run_elem = col_elem.find('.//run')
                if run_elem is not None and run_elem.text and len(run_elem.text.strip()) > 5:
                    field.description = run_elem.text.strip()
                    master_logger.debug(f"✓ Found direct run description for {internal_name}: {field.description}")
            
            # Strategy 4: Search through all child elements for description-like text
            if not field.description:
                for elem in col_elem.iter():
                    if elem.text and elem.text.strip():
                        text = elem.text.strip()
                        # Look for description-like patterns
                        if (len(text) > 15 and 
                            any(indicator in text.lower() for indicator in ['categorical', 'numerical', 'measure', 'dimension', 'time', 'count', 'total', 'name', 'flag', 'indicates', 'represents'])):
                            field.description = text
                            master_logger.debug(f"✓ Found pattern-matched description for {internal_name}: {field.description}")
                            break
            
            # Log successful description extraction and store in global collection
            if field.description:
                master_logger.info(f"✅ EXTRACTED DESCRIPTION for '{field.caption or internal_name}': {field.description[:100]}{'...' if len(field.description) > 100 else ''}")
                
                # Store in global collection for export (regardless of which datasource it belongs to)
                self.all_fields_with_descriptions.append(field)
                master_logger.debug(f"📝 Added field '{field.caption}' to global descriptions collection (total: {len(self.all_fields_with_descriptions)})")
            else:
                # Debug: Log XML structure for fields without descriptions
                master_logger.debug(f"❌ No description found for field: {field.caption or internal_name}")
                
                # Log all text content in this column element for debugging
                text_elements = []
                for elem in col_elem.iter():
                    if elem.text and elem.text.strip():
                        text_elements.append(f"{elem.tag}: '{elem.text.strip()[:30]}...'")
                
                if text_elements:
                    master_logger.debug(f"Available text in {internal_name}: {text_elements[:3]}")  # Show first 3
            
            aliases_elem = col_elem.find('.//aliases')
            if aliases_elem is not None:
                for alias in aliases_elem.findall('.//alias'):
                    key = alias.get('key', '')
                    value = alias.get('value', '')
                    if key and value:
                        field.aliases[key] = value
            
            return field
            
        except Exception as e:
            master_logger.error(f"Error parsing field: {e}")
            return None
    
    def _debug_xml_descriptions(self):
        """Debug method to scan entire XML for description patterns"""
        try:
            if not self.workbook_xml:
                return
            
            master_logger.info("🔍 DEBUGGING: Scanning entire TWB XML for description patterns...")
            
            # Find all elements that might contain descriptions
            desc_patterns = ['desc', 'description', 'comment', 'formatted-text', 'run']
            found_patterns = {}
            
            for pattern in desc_patterns:
                # Find with any namespace (check both with and without namespace)
                elements = self.workbook_xml.findall(f'.//{pattern}')
                
                # Also find elements where the tag ends with the pattern (namespace handling)
                if not elements:
                    elements = []
                    for elem in self.workbook_xml.iter():
                        if elem.tag.endswith(f'}}{pattern}') or elem.tag == pattern:
                            elements.append(elem)
                
                if elements:
                    found_patterns[pattern] = len(elements)
                    master_logger.info(f"✅ Found {len(elements)} '{pattern}' elements")

                    # Build parent map for ElementTree (since getparent() is lxml-only)
                    parent_map = {c: p for p in self.workbook_xml.iter() for c in p}

                    # Log first few examples with more context
                    for i, elem in enumerate(elements[:5]):  # Show more examples
                        if elem.text and elem.text.strip():
                            parent = parent_map.get(elem)
                            parent_tag = parent.tag if parent is not None else 'root'
                            parent_info = f"Parent: {parent_tag}"
                            text_preview = elem.text.strip()[:150]
                            master_logger.info(f"  📄 Example {i+1}: {parent_info} -> '{text_preview}{'...' if len(elem.text.strip()) > 150 else ''}'")

                            # For run elements, also check their parent structure
                            if pattern == 'run' and parent is not None:
                                grandparent = parent_map.get(parent)
                                grandparent_tag = grandparent.tag if grandparent is not None else 'root'
                                hierarchy = f"{grandparent_tag} > {parent.tag} > {elem.tag}"
                                master_logger.debug(f"    Hierarchy: {hierarchy}")
                else:
                    master_logger.info(f"❌ No '{pattern}' elements found")
            
            # Look for column elements with any text content
            columns = self.workbook_xml.findall('.//column')
            
            # Also find with namespace handling
            if not columns:
                columns = []
                for elem in self.workbook_xml.iter():
                    if elem.tag.endswith('}column') or elem.tag == 'column':
                        columns.append(elem)
            
            master_logger.info(f"Found {len(columns)} column elements total")
            
            columns_with_text = 0
            for col in columns:
                col_name = col.get('name', 'unknown')
                col_caption = col.get('caption', '')
                
                # Check all child elements for text
                has_description_text = False
                for child in col.iter():
                    if child.text and child.text.strip() and len(child.text.strip()) > 10:
                        has_description_text = True
                        master_logger.debug(f"Column {col_caption or col_name} has text in {child.tag}: {child.text.strip()[:50]}...")
                        
                if has_description_text:
                    columns_with_text += 1
            
            master_logger.info(f"Columns with potential description text: {columns_with_text}/{len(columns)}")
            
            if not found_patterns:
                master_logger.warning("❌ No description patterns found in TWB XML")
                master_logger.info("💡 This TWB file may not contain field descriptions, or they may be stored in a different format")
            else:
                master_logger.info(f"✓ Found description patterns: {found_patterns}")
            
        except Exception as e:
            master_logger.error(f"Error during XML description debugging: {e}")
            master_logger.error(traceback.format_exc())
    
    def _parse_worksheets(self):
        """Parse worksheet/chart definitions with enhanced field extraction"""
        try:
            for ws_elem in self.workbook_xml.findall('.//worksheet'):
                ws_name = ws_elem.get('name', '')
                if not ws_name:
                    continue
                
                chart = ChartMetadata(worksheet_name=ws_name)
                
                # Enhanced x/y axis extraction
                table = ws_elem.find('.//table')
                if table is not None:
                    # Method 1: Check panes for encodings
                    for pane in table.findall('.//pane'):
                        # X-axis fields
                        for encoding in pane.findall('.//encoding'):
                            attr_name = encoding.get('attr')
                            if attr_name == 'x' or encoding.get('name') == 'x':
                                field_name = encoding.get('field', '')
                                if field_name and field_name not in chart.x_axis:
                                    chart.x_axis.append(field_name)
                        
                        # Y-axis fields  
                        for encoding in pane.findall('.//encoding'):
                            attr_name = encoding.get('attr')
                            if attr_name == 'y' or encoding.get('name') == 'y':
                                field_name = encoding.get('field', '')
                                if field_name and field_name not in chart.y_axis:
                                    chart.y_axis.append(field_name)
                        
                        # Color encoding
                        for encoding in pane.findall('.//encoding'):
                            attr_name = encoding.get('attr')
                            if attr_name == 'color' or encoding.get('name') == 'color':
                                chart.color_encoding = encoding.get('field', '')
                        
                        # Size encoding
                        for encoding in pane.findall('.//encoding'):
                            attr_name = encoding.get('attr')
                            if attr_name == 'size' or encoding.get('name') == 'size':
                                chart.size_encoding = encoding.get('field', '')
                    
                    # Method 2: Check rows and cols elements
                    rows = table.find('.//rows')
                    if rows is not None:
                        for field in rows.text.split(',') if rows.text else []:
                            field = field.strip().strip('[]')
                            if field and field not in chart.y_axis:
                                chart.y_axis.append(field)
                    
                    cols = table.find('.//cols')
                    if cols is not None:
                        for field in cols.text.split(',') if cols.text else []:
                            field = field.strip().strip('[]')
                            if field and field not in chart.x_axis:
                                chart.x_axis.append(field)
                
                # Extract all used fields from datasource dependencies
                for ds_dep in ws_elem.findall('.//datasource-dependencies'):
                    ds_name = ds_dep.get('datasource', '')
                    if ds_name and ds_name not in chart.datasources_used:
                        chart.datasources_used.append(ds_name)
                    
                    # Extract all column references from this datasource
                    for col_ref in ds_dep.findall('.//column'):
                        col_name = col_ref.get('name', '')
                        col_caption = col_ref.get('caption', '')
                        
                        if col_caption and col_caption not in chart.detail_fields:
                            chart.detail_fields.append(col_caption)
                        
                        # Extract calculated fields
                        calc = col_ref.find('.//calculation')
                        if calc is not None:
                            formula = calc.get('formula', '')
                            if formula:
                                calc_id_match = re.search(r'Calculation_\d+', col_name)
                                calc_id = calc_id_match.group(0) if calc_id_match else None
                                
                                chart.calculated_fields.append({
                                    'name': col_caption or col_name,
                                    'formula': formula,
                                    'datatype': col_ref.get('datatype', 'unknown'),
                                    'calc_id': calc_id 
                                })
                
                # Enhanced filter extraction
                for filter_elem in ws_elem.findall('.//filter'):
                    filter_meta = self._parse_filter_enhanced(filter_elem)
                    if filter_meta:
                        chart.filters.append(filter_meta)
                
                # Extract mark type
                style_elem = ws_elem.find('.//style')
                if style_elem is not None:
                    for style_rule in style_elem.findall('.//style-rule'):
                        element = style_rule.get('element')
                        if element == 'mark':
                            chart.mark_type = style_rule.get('type', 'Automatic')
                
                self.charts[ws_name] = chart
                
                master_logger.info(f"Parsed worksheet '{ws_name}': x_axis={chart.x_axis}, y_axis={chart.y_axis}, fields={len(chart.detail_fields)}")
                
        except Exception as e:
            master_logger.error(f"Error parsing worksheets: {e}")
            master_logger.error(traceback.format_exc())
    
    def _parse_filter_enhanced(self, filter_elem) -> Optional[Dict]:
        """Parse filter metadata with enhanced value extraction"""
        try:
            field_name = filter_elem.get('column', '')
            if not field_name:
                return None
            
            filter_meta = {
                'field_name': field_name,
                'filter_type': filter_elem.get('class', 'categorical'),
                'values': [],
                'is_exclude': False
            }
            
            # Extract filter values from multiple locations
            # Method 1: groupfilter members
            for groupfilter in filter_elem.findall('.//groupfilter'):
                filter_meta['is_exclude'] = groupfilter.get('function') == 'except'
                
                for member in groupfilter.findall('.//member'):
                    value = member.get('value', '')
                    if value and value not in filter_meta['values']:
                        filter_meta['values'].append(value)
            
            # Method 2: Direct filter values
            for value_elem in filter_elem.findall('.//value'):
                value = value_elem.text
                if value and value not in filter_meta['values']:
                    filter_meta['values'].append(value)
            
            # Method 3: Range filters (quantitative)
            min_elem = filter_elem.find('.//min')
            max_elem = filter_elem.find('.//max')
            
            if min_elem is not None:
                filter_meta['min_value'] = min_elem.text
            if max_elem is not None:
                filter_meta['max_value'] = max_elem.text
            
            return filter_meta
            
        except Exception as e:
            master_logger.error(f"Error parsing filter: {e}")
            return None
    
    def _parse_blends(self):
        """Parse datasource blend relationships"""
        try:
            for ws in self.workbook_xml.findall('.//worksheet'):
                dependencies = ws.findall('.//datasource-dependencies')
                if len(dependencies) > 1:
                    primary_ds = dependencies[0].get('datasource', '')
                    
                    for dep in dependencies[1:]:
                        secondary_ds = dep.get('datasource', '')
                        
                        blend = BlendRelationship(
                            primary_datasource=primary_ds,
                            secondary_datasource=secondary_ds
                        )
                        
                        for link in dep.findall('.//column[@caption]'):
                            link_field = link.get('name', '')
                            if link_field:
                                blend.linking_fields.append((link_field, link_field))
                        
                        if blend.linking_fields:
                            self.blends.append(blend)
                            
        except Exception as e:
            master_logger.error(f"Error parsing blends: {e}")
    
    def build_comprehensive_calc_mapping(self) -> Dict[str, str]:
        """Build mapping of calculation IDs to readable names"""
        calc_mapping = {}
    
        try:
            master_logger.info("Building comprehensive calculation ID mapping...")
            
            # Strategy 1: Direct mapping from calculated fields
            for chart_name, chart in self.charts.items():
                for calc_field in chart.calculated_fields:
                    calc_id = calc_field.get('calc_id')
                    field_name = calc_field.get('name')
                    
                    if calc_id and calc_id not in calc_mapping:
                        calc_mapping[calc_id] = field_name
                        master_logger.info(f"DIRECT: {calc_id} -> {field_name} (chart: {chart_name})")
            
            # Strategy 2: Y-Axis Internal mapping
            for chart_name, chart in self.charts.items():
                for y_internal in chart.y_axis:
                    calc_ids_in_axis = re.findall(r'Calculation_\d+', y_internal)
                
                    for calc_id in calc_ids_in_axis:
                        if calc_id not in calc_mapping:
                            matched_name = None
                        
                            # Check if calc_id appears in any calculated field's formula
                            for calc_field in chart.calculated_fields:
                                formula = calc_field.get('formula', '')
                                if calc_id in formula:
                                    matched_name = calc_field['name']
                                    break
                        
                            # Extract prefix type (cnt, ctd, usr, sum, avg) and match semantically
                            if not matched_name:
                                prefix_match = re.search(r'\[(cnt|ctd|usr|sum|avg|min|max):', y_internal)
                            
                                if prefix_match:
                                    prefix = prefix_match.group(1)
                                
                                    for calc_field in chart.calculated_fields:
                                        formula = calc_field.get('formula', '').upper()
                                        field_name = calc_field['name']
                                    
                                        if prefix in ['cnt', 'ctd']:
                                            if 'COUNT' in formula or 'COUNTD' in formula:
                                                matched_name = field_name
                                                break
                                        elif prefix == 'usr':
                                            if '/' in formula or '*' in formula or 'SUM' in formula:
                                                matched_name = field_name
                                                break
                                        elif prefix in ['sum', 'avg']:
                                            if prefix.upper() in formula:
                                                matched_name = field_name
                                                break
                        
                            # Use the first calculated field as fallback
                            if not matched_name and chart.calculated_fields:
                                matched_name = chart.calculated_fields[0]['name']
                        
                            # Store the mapping
                            if matched_name:
                                calc_mapping[calc_id] = matched_name
                                master_logger.info(f"Y-AXIS: {calc_id} -> {matched_name} (chart: {chart_name})")
            
            master_logger.info(f"Mapped {len(calc_mapping)} calculation IDs")
            return calc_mapping
        
        except Exception as e:
            master_logger.error(f"Error building calc mapping: {e}")
            return {}   
     
    def replace_all_calc_ids(self, text: str) -> str:
        """Replace ALL calculation IDs in text with readable names"""
        if not text or not isinstance(text, str):
            return text
        
        try:
            if not hasattr(self, '_comprehensive_calc_mapping'):
                self._comprehensive_calc_mapping = self.build_comprehensive_calc_mapping()
            
            calc_ids = re.findall(r'Calculation_\d+', text)
            if not calc_ids:
                return text
            
            cleaned_text = text
            for calc_id in set(calc_ids):
                if calc_id in self._comprehensive_calc_mapping:
                    readable = self._comprehensive_calc_mapping[calc_id]
                    cleaned_text = cleaned_text.replace(f'[{calc_id}]', f'[{readable}]')
                    cleaned_text = cleaned_text.replace(calc_id, readable)
            
            return cleaned_text
            
        except Exception as e:
            master_logger.error(f"Error replacing calc IDs: {e}")
            return text
    
    def get_readable_field_name(self, internal_name: str, chart_name: str = None, calc_mapping: Dict[str, str] = None) -> str:
        """Convert internal Tableau field name to readable caption"""
        try:
            if calc_mapping is None:
                if hasattr(self, '_comprehensive_calc_mapping'):
                    calc_mapping = self._comprehensive_calc_mapping
            
            # First, try using the calc_mapping if provided
            if calc_mapping:
                calc_match = re.search(r'Calculation_\d+', internal_name)
                if calc_match:
                    calc_id = calc_match.group(0)
                    if calc_id in calc_mapping:
                        return calc_mapping[calc_id]
            
            # Check all datasources for this field
            for ds in self.datasources.values():
                for field in ds.fields:
                    if field.internal_name == internal_name:
                        return field.caption
            
            # Fallback: Extract readable name from internal name
            cleaned = internal_name.replace('[', '').replace(']', '')
            
            # Split by dots and get the last part
            parts = cleaned.split('.')
            if len(parts) > 1:
                last_part = parts[-1]
                
                # Remove Tableau metadata prefixes (mn:, usr:, tmn:, etc.)
                if ':' in last_part:
                    name_parts = last_part.split(':')
                    if len(name_parts) >= 2:
                        readable = name_parts[1].replace('_', ' ').title()
                        # Remove trailing metadata (like :ok, :qk)
                        readable = readable.split(':')[0]
                        
                        if readable.startswith('Calculation '):
                            return readable
                        
                        return readable
            
            # Final fallback: return cleaned name
            return cleaned.replace('_', ' ').title()
            
        except Exception as e:
            master_logger.error(f"Error getting readable field name: {e}")
            return internal_name

    def get_readable_chart_metadata(self, chart_name: str, include_expanded_formulas: bool = False) -> Optional[Dict[str, Any]]:
        """Get chart metadata with readable field names and formulas"""
        try:
            chart = self.charts.get(chart_name)
            if not chart:
                return None
            
            # Build universal calculation ID mapping if not already built
            if not hasattr(self, '_comprehensive_calc_mapping'):
                self._comprehensive_calc_mapping = self.build_comprehensive_calc_mapping()
            
            calc_id_mapping = self._comprehensive_calc_mapping
            
            # Convert x_axis
            x_axis_readable = []
            for x_field in chart.x_axis:
                readable_name = self.get_readable_field_name(x_field, chart_name, calc_id_mapping)
                x_axis_readable.append(readable_name)
            
            # Convert y_axis  
            y_axis_readable = []
            for y_field in chart.y_axis:
                readable_name = self.get_readable_field_name(y_field, chart_name, calc_id_mapping)
                y_axis_readable.append(readable_name)
            
            # Process calculated fields with cleaned formulas
            calc_fields_cleaned = []
            for cf in chart.calculated_fields:
                # Replace calculation IDs in formula
                cleaned_formula = self.replace_all_calc_ids(cf['formula'])
                
                calc_field_info = {
                    'name': cf['name'],
                    'formula': cleaned_formula,
                    'datatype': cf.get('datatype', 'unknown')
                }
                
                calc_fields_cleaned.append(calc_field_info)
            
            return {
                'worksheet_name': chart.worksheet_name,
                'x_axis': x_axis_readable,
                'y_axis': y_axis_readable,
                'x_axis_internal': chart.x_axis,
                'y_axis_internal': chart.y_axis,
                'color_encoding': self.get_readable_field_name(chart.color_encoding, chart_name, calc_id_mapping) if chart.color_encoding else None,
                'size_encoding': self.get_readable_field_name(chart.size_encoding, chart_name, calc_id_mapping) if chart.size_encoding else None,
                'detail_fields': chart.detail_fields,
                'mark_type': chart.mark_type,
                'filters': [
                    {
                        'field_name': self.get_readable_field_name(f['field_name'], chart_name, calc_id_mapping),
                        'field_name_internal': f['field_name'],
                        'filter_type': f['filter_type'],
                        'values': f['values'],
                        'is_exclude': f.get('is_exclude', False),
                        'min_value': f.get('min_value'),
                        'max_value': f.get('max_value')
                    }
                    for f in chart.filters
                ],
                'calculated_fields_ordered': calc_fields_cleaned,
                'datasources_used': chart.datasources_used
            }
            
        except Exception as e:
            master_logger.error(f"Error getting readable chart metadata: {e}")
            return None

    def export_metadata_summary(self, workbook_name: str, output_dir: str = None) -> str:
        """
        Export FULLY READABLE metadata with NO calculation IDs to organized folder structure
        
        Args:
            workbook_name: Name of the workbook
            output_dir: Optional directory to save metadata (if None, creates tableau_metadata folder)
            
        Returns:
            Path to the created metadata file
        """
        try:
            # Ensure mapping is built
            if not hasattr(self, '_comprehensive_calc_mapping'):
                self._comprehensive_calc_mapping = self.build_comprehensive_calc_mapping()
        
            master_logger.info(f"Exporting metadata with {len(self._comprehensive_calc_mapping)} calc ID mappings")
            
            # Create organized metadata directory structure
            if output_dir is None:
                metadata_base_dir = "tableau_metadata"
                output_dir = self._get_or_create_metadata_dir(workbook_name, metadata_base_dir)
                
            # Ensure directory exists
            os.makedirs(output_dir, exist_ok=True)
            
            # Create the metadata file path
            output_path = os.path.join(output_dir, f"metadata_{workbook_name}.json")
        
            summary = {
                'workbook_name': workbook_name,
                'parsed_at': datetime.now().isoformat(),
                'metadata_export_path': output_path,
                'twb_metadata': {
                    'datasources': list(self.datasources.keys()),
                    'charts': list(self.charts.keys()),
                    'blends': len(self.blends)
                },
                'charts_metadata_readable': {}
            }
        
            # Process each chart
            for chart_name in self.charts.keys():
                chart = self.charts[chart_name]
            
                # Clean ALL calculated fields using replace_all_calc_ids
                cleaned_calc_fields = []
                for calc_field in chart.calculated_fields:
                    cleaned = {
                        'name': self.replace_all_calc_ids(calc_field['name']),
                        'formula': self.replace_all_calc_ids(calc_field['formula']),
                        'datatype': calc_field.get('datatype', 'unknown')
                    }
                    cleaned_calc_fields.append(cleaned)
            
                # Clean filters
                cleaned_filters = []
                for f in chart.filters:
                    cleaned_filters.append({
                        'field_name': self.replace_all_calc_ids(f.get('field_name', '')),
                        'field_name_internal': f.get('field_name', ''),
                        'filter_type': f.get('filter_type', 'categorical'),
                        'values': f.get('values', []),
                        'is_exclude': f.get('is_exclude', False),
                        'min_value': f.get('min_value'),
                        'max_value': f.get('max_value')
                    })
            
                # Assemble readable metadata
                readable_meta = {
                    'worksheet_name': chart_name,
                    'x_axis': [self.replace_all_calc_ids(x) for x in chart.x_axis],
                    'y_axis': [self.replace_all_calc_ids(y) for y in chart.y_axis],
                    'x_axis_internal': chart.x_axis,
                    'y_axis_internal': chart.y_axis,
                    'color_encoding': self.replace_all_calc_ids(chart.color_encoding) if chart.color_encoding else None,
                    'size_encoding': self.replace_all_calc_ids(chart.size_encoding) if chart.size_encoding else None,
                    'detail_fields': chart.detail_fields,
                    'mark_type': chart.mark_type,
                    'filters': cleaned_filters,
                    'calculated_fields_ordered': cleaned_calc_fields,
                    'datasources_used': chart.datasources_used
                }
            
                summary['charts_metadata_readable'][chart_name] = readable_meta
        
            # Save to file
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)
        
            master_logger.info(f"Exported fully readable metadata to: {output_path}")
            master_logger.info(f"Metadata directory created: {output_dir}")
            return output_path
        
        except Exception as e:
            master_logger.error(f"Error exporting metadata: {e}")
            return None

    def export_field_descriptions(self, workbook_name: str, output_dir: str = None) -> str:
        """
        Export field descriptions/comments to organized folder structure
        
        Args:
            workbook_name: Name of the workbook
            output_dir: Optional directory to save descriptions (if None, creates tableau_descriptions folder)
            
        Returns:
            Path to the created descriptions file
        """
        try:
            master_logger.info(f"Exporting field descriptions for workbook: {workbook_name}")
            
            # Create organized descriptions directory structure
            if output_dir is None:
                descriptions_base_dir = "tableau_descriptions"
                output_dir = self._get_or_create_metadata_dir(workbook_name, descriptions_base_dir)
                
            # Ensure directory exists
            os.makedirs(output_dir, exist_ok=True)
            
            # Create the descriptions file path
            output_path = os.path.join(output_dir, f"field_descriptions_{workbook_name}.json")
        
            # Use the global collection of fields with descriptions
            fields_with_descriptions = []
            total_fields_processed = len(self.all_fields_with_descriptions)
            
            master_logger.info(f"🌍 USING GLOBAL FIELD DESCRIPTIONS COLLECTION")
            master_logger.info(f"Total fields with descriptions found during parsing: {total_fields_processed}")
            
            # Process each field from the global collection
            for field in self.all_fields_with_descriptions:
                # Determine which datasource this field belongs to (best effort)
                datasource_name = "unknown"
                for ds_name, ds_metadata in self.datasources.items():
                    for ds_field in ds_metadata.fields:
                        if ds_field.internal_name == field.internal_name:
                            datasource_name = ds_name
                            break
                
                if field.description and field.description.strip():
                    field_info = {
                        "field_name": field.caption,
                        "comment": field.description.strip()
                    }
                        
                    fields_with_descriptions.append(field_info)
                    master_logger.debug(f"✅ Added to export: {field.caption}")
            
            master_logger.info(f"Global collection processed: {len(fields_with_descriptions)} fields ready for export")
            
            master_logger.info(f"🔍 FIELD DESCRIPTIONS EXTRACTION SUMMARY:")
            master_logger.info(f"  • Global fields with descriptions found: {total_fields_processed}")
            master_logger.info(f"  • Fields successfully exported: {len(fields_with_descriptions)}")
            master_logger.info(f"  • Datasources scanned: {len(self.datasources)}")
            master_logger.info(f"  • Export success rate: {len(fields_with_descriptions)/total_fields_processed*100:.1f}%" if total_fields_processed > 0 else "  • No fields to process")
            
            # Log first few descriptions found as examples
            if fields_with_descriptions:
                master_logger.info(f"✅ SAMPLE DESCRIPTIONS EXPORTED:")
                for i, field_info in enumerate(fields_with_descriptions[:3]):
                    master_logger.info(f"  {i+1}. {field_info['field_name']}: {field_info['comment'][:80]}{'...' if len(field_info['comment']) > 80 else ''}")
            else:
                if total_fields_processed > 0:
                    master_logger.warning(f"❌ EXPORT FAILED - {total_fields_processed} fields found during parsing but none exported")
                else:
                    master_logger.warning(f"❌ NO FIELD DESCRIPTIONS FOUND - Check if TWB contains field descriptions")
            
            # Create simplified summary structure - only field_name and comment
            descriptions_summary = {
                'workbook_name': workbook_name,
                'exported_at': datetime.now().isoformat(),
                'total_fields_with_descriptions': len(fields_with_descriptions),
                'field_descriptions': fields_with_descriptions
            }
        
            # Save to file
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(descriptions_summary, f, indent=2, ensure_ascii=False)
        
            master_logger.info(f"Exported {len(fields_with_descriptions)} field descriptions to: {output_path}")
            master_logger.info(f"Descriptions directory created: {output_dir}")
            return output_path
        
        except Exception as e:
            master_logger.error(f"Error exporting field descriptions: {e}")
            return None


class EnhancedWorkbookDataFetcher:
    """
    Enhanced workbook data fetcher that integrates with our new services
    Fetches workbook-level data and provides intelligent summaries
    """
    
    def __init__(self, openai_client=None, openai_api_key: str = None):
        master_logger.info("Initializing EnhancedWorkbookDataFetcher")
        
        # Initialize services if available
        if ENHANCED_SERVICES_AVAILABLE:
            try:
                self.data_processor = EnhancedDataProcessor()
                self.llm_service = LLMService(openai_client=openai_client, openai_api_key=openai_api_key)
                self.vector_store = TableauVectorStore(openai_client=openai_client, openai_api_key=openai_api_key)
                master_logger.info("Enhanced services initialized successfully")
            except Exception as e:
                master_logger.error(f"Error initializing enhanced services: {e}")
                self.data_processor = None
                self.llm_service = None
                self.vector_store = None
        else:
            self.data_processor = None
            self.llm_service = None
            self.vector_store = None
            master_logger.warning("Enhanced services not available, using basic functionality")
        
        # Original data processor for backward compatibility
        self.tableau_processor = TableauDataProcessor()
        
        # Initialize ChartColumnMapper (will be configured with CSV data later)
        self.chart_column_mapper = None
        try:
            from services.chart_column_mapper import ChartColumnMapper
            self.chart_column_mapper = ChartColumnMapper()
            master_logger.info("ChartColumnMapper initialized (CSV data will be set during fetch)")
        except Exception as e:
            master_logger.error(f"Error initializing ChartColumnMapper: {e}")
            self.chart_column_mapper = None
        
    @function_logger('tableau_backend.EnhancedWorkbookDataFetcher.fetch_workbook_data')
    async def fetch_workbook_data(self, chat_state: ChatState) -> Dict[str, Any]:
        """
        Fetch all workbook data and generate intelligent summary
        
        Args:
            chat_state: Current chat state with Tableau connection
            
        Returns:
            Dictionary with workbook data and metadata
        """
        try:
            master_logger.info(f"=== FETCHING WORKBOOK DATA ===")
            master_logger.info(f"Workbook: {chat_state.workbook_name}")
            master_logger.info(f"Available views: {len(chat_state.available_views)}")
            
            start_time = time.time()
            
            # Configure ChartColumnMapper with CSV data if available
            if self.chart_column_mapper:
                try:
                    import app  # ✅ Import dynamically inside function
                    csv_loader = app.csv_data_loader  # ✅ Use the global instance
                    if csv_loader and csv_loader.data is not None:
                        self.chart_column_mapper.csv_data = csv_loader.data
                        self.chart_column_mapper.fuzzy_matcher = getattr(csv_loader, 'fuzzy_matcher', None)
                        master_logger.info("ChartColumnMapper configured with CSV data and fuzzy matcher")
                    else:
                        master_logger.warning("CSV data not available, ChartColumnMapper will store nulls for csv_matched")
                except Exception as e:
                    master_logger.warning(f"Could not load CSV data for ChartColumnMapper: {e}")
            
            # Step 1: Fetch data from all worksheets
            worksheets_data = await self._fetch_all_worksheets_data(chat_state)
            
            # Step 2: Generate workbook summary using LLM service
            workbook_summary = await self._generate_workbook_summary(
                chat_state.workbook_name, worksheets_data
            )
            
            # Step 3: Cache data in vector store if available
            if self.vector_store and worksheets_data:
                await self._cache_workbook_data(chat_state.workbook_id, chat_state.workbook_name, worksheets_data)
            
            # Step 4: Process with enhanced data processor if available
            processed_data = None
            if self.data_processor and worksheets_data:
                processed_data = self.data_processor.process_workbook_data(worksheets_data)
            
            fetch_time = time.time() - start_time
            master_logger.info(f"Workbook data fetch completed in {fetch_time:.3f} seconds")
            
            return {
                'success': True,
                'workbook_name': chat_state.workbook_name,
                'worksheets_data': worksheets_data,
                'workbook_summary': workbook_summary,
                'processed_data': processed_data,
                'original_tableau_data': getattr(self, '_original_tableau_data', {}),  # Add original data
                'total_rows': sum(len(df) for df in worksheets_data.values() if df is not None),
                'total_worksheets': len(worksheets_data),
                'fetch_time': fetch_time,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            master_logger.error(f"Error fetching workbook data: {e}")
            master_logger.error(f"Full traceback: {traceback.format_exc()}")
            return {
                'success': False,
                'error': str(e),
                'workbook_name': chat_state.workbook_name,
                'worksheets_data': {},
                'workbook_summary': None
            }
    
    @function_logger('tableau_backend.EnhancedWorkbookDataFetcher.fetch_single_chart_data')
    async def fetch_single_chart_data(self, chat_state: ChatState, selected_chart: str) -> Dict[str, Any]:
        """
        Fetch data for a single selected chart only (LAZY LOADING)
        
        Args:
            chat_state: Current chat state with Tableau connection
            selected_chart: Name of the chart to fetch
            
        Returns:
            Dictionary with single chart data and metadata
        """
        try:
            master_logger.info(f"=== FETCHING SINGLE CHART DATA (LAZY LOADING) ===")
            master_logger.info(f"Workbook: {chat_state.workbook_name}")
            master_logger.info(f"Selected chart: {selected_chart}")
            
            start_time = time.time()
            
            # Configure ChartColumnMapper with CSV data if available
            if self.chart_column_mapper:
                try:
                    import app
                    csv_loader = app.csv_data_loader
                    if csv_loader and csv_loader.data is not None:
                        self.chart_column_mapper.csv_data = csv_loader.data
                        self.chart_column_mapper.fuzzy_matcher = getattr(csv_loader, 'fuzzy_matcher', None)
                        master_logger.info("ChartColumnMapper configured with CSV data and fuzzy matcher")
                    else:
                        master_logger.warning("CSV data not available, ChartColumnMapper will store nulls for csv_matched")
                except Exception as e:
                    master_logger.warning(f"Could not load CSV data for ChartColumnMapper: {e}")
            
            # Find the selected chart in available views
            matching_view = next((v for v in chat_state.available_views if v.get('name') == selected_chart), None)
            
            if not matching_view:
                master_logger.error(f"Selected chart '{selected_chart}' not found in available views")
                return {
                    'success': False,
                    'error': f"Chart '{selected_chart}' not found",
                    'workbook_name': chat_state.workbook_name,
                    'worksheets_data': {}
                }
            
            view_id = matching_view.get('id')
            if not view_id:
                master_logger.error(f"No view ID found for {selected_chart}")
                return {
                    'success': False,
                    'error': f"No view ID for chart '{selected_chart}'",
                    'workbook_name': chat_state.workbook_name,
                    'worksheets_data': {}
                }
            
            # Fetch data for the single chart
            worksheets_data = {}
            try:
                master_logger.info(f"Fetching data for selected chart: {selected_chart}")
                
                df = self.tableau_processor.get_view_data(
                    chat_state.site_id, 
                    view_id, 
                    chat_state.auth_token
                )
                
                if df is not None and not df.empty:
                    master_logger.info(f"Successfully fetched data for {selected_chart}, shape: {df.shape}")
                    master_logger.info(f"Column names: {list(df.columns)}")
                    
                    worksheets_data[selected_chart] = df
                    
                    # Store original copy for chart statistics
                    if not hasattr(self, '_original_tableau_data'):
                        self._original_tableau_data = {}
                    self._original_tableau_data[selected_chart] = df.copy()
                    
                    # Process column mappings
                    if self.chart_column_mapper:
                        master_logger.info(f"Processing column mappings for chart: {selected_chart}")
                        self.chart_column_mapper.process_and_store_chart(
                            chart_name=selected_chart,
                            chart_data=df,
                            workbook_name=chat_state.workbook_name
                        )
                else:
                    master_logger.warning(f"No data returned for {selected_chart}")
                    
            except Exception as e:
                master_logger.error(f"Error fetching data for {selected_chart}: {e}")
                master_logger.error(f"Traceback: {traceback.format_exc()}")
            
            fetch_time = time.time() - start_time
            master_logger.info(f"Single chart data fetch completed in {fetch_time:.3f} seconds")
            
            # Save chart mapping to cache
            if self.chart_column_mapper and hasattr(self.chart_column_mapper, 'current_workbook_charts'):
                if self.chart_column_mapper.current_workbook_charts:
                    try:
                        self.chart_column_mapper.save_workbook_to_cache()
                        master_logger.info(f"Saved chart mapping to cache for: {selected_chart}")
                    except Exception as e:
                        master_logger.error(f"Error saving chart mapping: {e}")
            
            return {
                'success': True,
                'workbook_name': chat_state.workbook_name,
                'worksheets_data': worksheets_data,
                'workbook_summary': None,  # Not needed for single chart
                'processed_data': None,
                'original_tableau_data': getattr(self, '_original_tableau_data', {}),
                'total_rows': sum(len(df) for df in worksheets_data.values() if df is not None),
                'total_worksheets': len(worksheets_data),
                'fetch_time': fetch_time,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            master_logger.error(f"Error in fetch_single_chart_data: {e}")
            master_logger.error(f"Full traceback: {traceback.format_exc()}")
            return {
                'success': False,
                'error': str(e),
                'workbook_name': chat_state.workbook_name,
                'worksheets_data': {}
            }
    
    @function_logger('tableau_backend.EnhancedWorkbookDataFetcher.fetch_workbook_metadata')
    async def fetch_workbook_metadata(self, chat_state: ChatState) -> Dict[str, Any]:
        """
        Fetch only workbook metadata without any chart data (for exploration queries)
        
        Args:
            chat_state: Current chat state with Tableau connection
            
        Returns:
            Dictionary with workbook metadata only (no chart data)
        """
        try:
            master_logger.info(f"=== FETCHING WORKBOOK METADATA ONLY (NO CHART DATA) ===")
            master_logger.info(f"Workbook: {chat_state.workbook_name}")
            master_logger.info(f"Available views: {len(chat_state.available_views)}")
            master_logger.info(f"Metadata-only mode for exploration query")
            
            start_time = time.time()
            
            # Return metadata structure without fetching any chart data
            fetch_time = time.time() - start_time
            master_logger.info(f"Metadata fetch completed in {fetch_time:.3f} seconds")
            
            return {
                'success': True,
                'workbook_name': chat_state.workbook_name,
                'worksheets_data': {},  # Empty - no chart data fetched
                'workbook_summary': None,
                'processed_data': None,
                'original_tableau_data': {},
                'total_rows': 0,
                'total_worksheets': 0,
                'fetch_time': fetch_time,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            master_logger.error(f"Error in fetch_workbook_metadata: {e}")
            return {
                'success': False,
                'error': str(e),
                'workbook_name': chat_state.workbook_name,
                'worksheets_data': {}
            }
    
    async def _fetch_all_worksheets_data(self, chat_state: ChatState, progress_callback=None) -> Dict[str, pd.DataFrame]:
        """Fetch data from all available worksheets with progress reporting"""
        
        worksheets_data = {}
        total_worksheets = len(chat_state.available_views)
        
        master_logger.info(f"Fetching data from {total_worksheets} worksheets")
        
        for idx, view in enumerate(chat_state.available_views, 1):
            view_name = view.get('name', 'Unknown')
            view_id = view.get('id')
            
            if not view_id:
                master_logger.warning(f"No view ID found for {view_name}")
                continue
            
            # Report worksheet progress
            if progress_callback:
                try:
                    progress_callback(
                        stage='fetching_worksheets',
                        message=f'Fetching worksheet {idx}/{total_worksheets}: {view_name}',
                        worksheets_processed=idx,
                        total_worksheets=total_worksheets,
                        current_item=view_name
                    )
                except Exception as e:
                    master_logger.warning(f"Progress callback error (non-critical): {e}")
            
            try:
                master_logger.info(f"Fetching data for worksheet: {view_name}")
                master_logger.info(f"DEBUG: About to call get_view_data for {view_name}")
                
                # Use the original Tableau data processor
                df = self.tableau_processor.get_view_data(
                    chat_state.site_id, 
                    view_id, 
                    chat_state.auth_token
                )
                
                if df is not None and not df.empty:
                    master_logger.info(f"DEBUG: Successfully got data for {view_name}, shape: {df.shape}")
                    master_logger.info(f"DEBUG: Column names: {list(df.columns)}")
                    
                    worksheets_data[view_name] = df
                    master_logger.info(f"Successfully fetched {len(df)} rows for {view_name}")
                    
                    # Store original copy for chart statistics (preserve Tableau column names)
                    if not hasattr(self, '_original_tableau_data'):
                        self._original_tableau_data = {}
                    self._original_tableau_data[view_name] = df.copy()
                    master_logger.info(f"DEBUG: Stored original Tableau data copy for chart statistics: {view_name}")
                    master_logger.info(f"DEBUG: Original data copy columns: {list(self._original_tableau_data[view_name].columns)}")
                    
                    # Process and store column mappings using ChartColumnMapper
                    if self.chart_column_mapper:
                        try:
                            master_logger.info(f"Processing column mappings for chart: {view_name}")
                            mapping_result = self.chart_column_mapper.process_and_store_chart(
                                chart_name=view_name,
                                chart_data=df,
                                workbook_name=chat_state.workbook_name
                            )
                            master_logger.info(f"Chart column mapping completed for {view_name}")
                            master_logger.info(f"Mapping summary - X: {mapping_result.get('x_axis_detected')}, "
                                             f"Y: {mapping_result.get('y_axis_detected')}, "
                                             f"Hue: {mapping_result.get('hue_detected')}")
                        except Exception as e:
                            master_logger.error(f"Error processing column mappings for {view_name}: {e}")
                            import traceback
                            master_logger.error(traceback.format_exc())
                    else:
                        master_logger.warning(f"ChartColumnMapper not initialized, skipping column mapping for {view_name}")
                    
                else:
                    master_logger.warning(f"No data returned for worksheet: {view_name}")
                    worksheets_data[view_name] = None
                    
            except Exception as e:
                master_logger.error(f"Error fetching data for {view_name}: {e}")
                worksheets_data[view_name] = None
        
        # Filter out None values
        valid_worksheets = {k: v for k, v in worksheets_data.items() if v is not None}
        
        master_logger.info(f"Successfully fetched data from {len(valid_worksheets)}/{len(chat_state.available_views)} worksheets")
        
        # Save all accumulated chart mappings for this workbook
        if self.chart_column_mapper and hasattr(self.chart_column_mapper, 'current_workbook_charts'):
            if self.chart_column_mapper.current_workbook_charts:
                try:
                    self.chart_column_mapper.save_workbook_to_cache()
                    master_logger.info(f"Saved complete workbook mappings to cache for: {chat_state.workbook_name}")
                except Exception as e:
                    master_logger.error(f"Error saving workbook mappings to cache: {e}")
        
        return valid_worksheets
    
    async def _generate_workbook_summary(self, workbook_name: str, worksheets_data: Dict[str, pd.DataFrame]) -> Dict[str, str]:
        """Generate intelligent workbook summary using LLM service"""
        
        try:
            if not self.llm_service or not worksheets_data:
                return self._generate_basic_summary(workbook_name, worksheets_data)
            
            master_logger.info("Generating intelligent workbook summary")
            
            # Prepare worksheets info for LLM
            worksheets_info = []
            total_rows = 0
            
            for ws_name, df in worksheets_data.items():
                if df is not None:
                    ws_info = {
                        'name': ws_name,
                        'shape': df.shape,
                        'columns': df.columns.tolist(),
                        'dtypes': {col: str(dtype) for col, dtype in df.dtypes.items()}
                    }
                    worksheets_info.append(ws_info)
                    total_rows += len(df)
            
            # Generate summary using LLM service
            summary = await self.llm_service.generate_workbook_summary(
                workbook_name, worksheets_info, total_rows
            )
            
            master_logger.info("Workbook summary generated successfully")
            return summary
            
        except Exception as e:
            master_logger.error(f"Error generating workbook summary: {e}")
            return self._generate_basic_summary(workbook_name, worksheets_data)
    
    def _generate_basic_summary(self, workbook_name: str, worksheets_data: Dict[str, pd.DataFrame]) -> Dict[str, str]:
        """Generate basic summary when LLM service is not available"""
        
        total_rows = sum(len(df) for df in worksheets_data.values() if df is not None)
        total_worksheets = len(worksheets_data)
        
        # Identify key metrics from column names
        all_columns = []
        for df in worksheets_data.values():
            if df is not None:
                all_columns.extend(df.columns.tolist())
        
        metric_keywords = ['revenue', 'sales', 'profit', 'cost', 'amount', 'value', 'total', 'count']
        key_metrics = []
        
        for col in set(all_columns):
            col_lower = col.lower()
            if any(keyword in col_lower for keyword in metric_keywords):
                key_metrics.append(col)
        
        summary_line1 = f"📊 Loaded {total_rows:,} rows across {total_worksheets} worksheets"
        if key_metrics:
            summary_line1 += f" with metrics: {', '.join(key_metrics[:4])}"
        
        summary_line2 = f"🔍 Ready for analysis with {len(set(all_columns))} unique data dimensions"
        
        return {
            'summary_line1': summary_line1,
            'summary_line2': summary_line2,
            'key_metrics': key_metrics[:5],
            'analysis_types': ['Data exploration', 'Statistical analysis', 'Trend analysis']
        }
    
    async def _cache_workbook_data(self, workbook_id: str, workbook_name: str, worksheets_data: Dict[str, pd.DataFrame]):
        """Cache workbook data in vector store"""
        
        try:
            if not self.vector_store:
                return
            
            master_logger.info(f"Caching workbook data for: {workbook_name}")
            
            # Cache in vector store
            success = self.vector_store.cache_workbook_data(
                workbook_id, workbook_name, worksheets_data
            )
            
            if success:
                master_logger.info("Workbook data cached successfully")
            else:
                master_logger.warning("Failed to cache workbook data")
                
        except Exception as e:
            master_logger.error(f"Error caching workbook data: {e}")
    
    def get_worksheet_data_for_chart(self, worksheets_data: Dict[str, pd.DataFrame], chart_name: str) -> Optional[pd.DataFrame]:
        """Get CSV data for analysis and extract Tableau chart column names for mapping"""
        
        try:
            master_logger.info(f"Looking for chart data: '{chart_name}'")
            master_logger.info(f"Available worksheets: {list(worksheets_data.keys())}")
            
            # Extract column names from the Tableau chart for analysis
            tableau_columns = []
            chart_data = None
            
            # Try exact match first to get column names
            if chart_name in worksheets_data:
                chart_data = worksheets_data[chart_name]
                tableau_columns = chart_data.columns.tolist()
                master_logger.info(f"Exact match found for '{chart_name}'")
                master_logger.info(f"Tableau chart columns: {tableau_columns}")
            else:
                # Try fuzzy matching to get column names
                best_match = None
                best_score = 0
                
                for ws_name in worksheets_data.keys():
                    score = fuzz.ratio(chart_name.lower(), ws_name.lower())
                    master_logger.debug(f"Fuzzy match score for '{chart_name}' vs '{ws_name}': {score}")
                    if score > best_score:
                        best_score = score
                        best_match = ws_name
                
                if best_match and best_score > 50:
                    chart_data = worksheets_data[best_match]
                    tableau_columns = chart_data.columns.tolist()
                    master_logger.info(f"Fuzzy matched chart '{chart_name}' to worksheet '{best_match}' (score: {best_score})")
                    master_logger.info(f"Tableau chart columns: {tableau_columns}")
            
            # Now return CSV data for analysis, but store the Tableau column info
            try:
                import app
                if hasattr(app, 'csv_summary_data') and app.csv_summary_data is not None:
                    master_logger.info(f"Using CSV data for analysis of chart '{chart_name}'")
                    master_logger.info(f"CSV data columns: {app.csv_summary_data.columns.tolist()}")
                    
                    # Store tableau column info for mapping
                    if not hasattr(self, 'tableau_chart_columns'):
                        self.tableau_chart_columns = {}
                    self.tableau_chart_columns[chart_name] = tableau_columns
                    
                    # Store original chart data copy for statistics (at same location as columns)
                    if chart_data is not None and hasattr(chart_data, 'columns'):
                        if not hasattr(self, 'original_chart_data'):
                            self.original_chart_data = {}
                        self.original_chart_data[chart_name] = chart_data.copy()
                        master_logger.info(f"DEBUG: Stored chart data copy with columns: {list(chart_data.columns)}")
                    
                    master_logger.info(f"Stored Tableau columns for '{chart_name}': {tableau_columns}")
                    
                    # Make original chart data AND tableau columns globally accessible for statistics
                    app.original_chart_data = getattr(self, 'original_chart_data', {})
                    app.original_tableau_columns = getattr(self, 'tableau_chart_columns', {})
                    master_logger.info(f"DEBUG: Made original chart data and columns accessible globally")
                    master_logger.info(f"DEBUG: Available charts in original data: {list(app.original_chart_data.keys())}")
                    master_logger.info(f"DEBUG: Available tableau columns: {list(app.original_tableau_columns.keys())}")
                    
                    return app.csv_summary_data
                    
            except Exception as csv_error:
                master_logger.warning(f"Could not access CSV data: {csv_error}")
            
            # Fallback to Tableau data if CSV not available
            if chart_data is not None:
                master_logger.warning(f"CSV data not available, using Tableau data for '{chart_name}'")
                return chart_data
            
            # Final fallback
            if worksheets_data:
                first_worksheet = list(worksheets_data.keys())[0]
                master_logger.warning(f"Using fallback worksheet: {first_worksheet}")
                return worksheets_data[first_worksheet]
            
            return None
            
        except Exception as e:
            master_logger.error(f"Error getting worksheet data for chart: {e}")
            return None


# ============================================================================
# PHASE 2: DATASOURCE EXTRACTION (.hyper files)
# ============================================================================

class DatasourceExtractor:
    """Extract data from Tableau datasources (.hyper files)"""
    
    def __init__(self, connection_manager):
        master_logger.info("Initializing DatasourceExtractor")
        self.connection_manager = connection_manager
        self.config = TABLEAU_CONFIG
    
    @function_logger('tableau_backend.DatasourceExtractor.download_datasource')
    def download_datasource(self, site_id: str, datasource_id: str, auth_token: str, output_path: str) -> bool:
        """Download datasource (.tdsx) file"""
        try:
            master_logger.info(f"Downloading datasource: {datasource_id}")
            
            url = f"{self.config['tableau_server_url']}/api/{self.config['api_version']}/sites/{site_id}/datasources/{datasource_id}/content"
            headers = {"X-Tableau-Auth": auth_token}
            
            response = requests.get(url, headers=headers, verify=False, stream=True, timeout=300)
            
            if response.status_code == 200:
                with open(output_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                master_logger.info(f"Datasource downloaded to: {output_path}")
                return True
            else:
                master_logger.error(f"Failed to download datasource: {response.status_code}")
                return False
                
        except Exception as e:
            master_logger.error(f"Error downloading datasource: {e}")
            return False
    
    @function_logger('tableau_backend.DatasourceExtractor.extract_hyper_from_tdsx')
    def extract_hyper_from_tdsx(self, tdsx_path: str, output_dir: str) -> Optional[str]:
        """Extract .hyper file from .tdsx package"""
        try:
            master_logger.info(f"Extracting hyper from: {tdsx_path}")
            
            with zipfile.ZipFile(tdsx_path, 'r') as zip_ref:
                hyper_files = [f for f in zip_ref.namelist() if f.endswith('.hyper')]
                
                if not hyper_files:
                    master_logger.warning("No .hyper file found in package")
                    return None
                
                hyper_file = hyper_files[0]
                hyper_path = os.path.join(output_dir, os.path.basename(hyper_file))
                
                with zip_ref.open(hyper_file) as source, open(hyper_path, 'wb') as target:
                    target.write(source.read())
                
                master_logger.info(f"Hyper file extracted to: {hyper_path}")
                return hyper_path
                
        except Exception as e:
            master_logger.error(f"Error extracting hyper file: {e}")
            return None
    
    @function_logger('tableau_backend.DatasourceExtractor.read_hyper_data')
    def read_hyper_data(self, hyper_path: str, table_name: Optional[str] = None) -> Optional[pd.DataFrame]:
        """Read data from .hyper file using Tableau Hyper API"""
        try:
            master_logger.info(f"Reading hyper file: {hyper_path}")
            
            try:
                from tableauhyperapi import HyperProcess, Telemetry, Connection, CreateMode
            except ImportError:
                master_logger.error("tableauhyperapi not installed. Run: pip install tableauhyperapi")
                return None
            
            with HyperProcess(telemetry=Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as hyper:
                with Connection(endpoint=hyper.endpoint, database=hyper_path, create_mode=CreateMode.NONE) as connection:
                    
                    if table_name is None:
                        tables = connection.catalog.get_table_names('Extract')
                        if not tables:
                            master_logger.error("No tables found in hyper file")
                            return None
                        table_name = tables[0]
                    
                    master_logger.info(f"Reading table: {table_name}")
                    
                    query = f'SELECT * FROM {table_name}'

                    # Correct way to read hyper data
                    result_set = connection.execute_query(query)
                    
                    # Get column names from schema
                    columns = [column.name.unescaped for column in result_set.schema.columns]
                    master_logger.info(f"Found {len(columns)} columns: {columns[:5]}...")   
                    
                    # Read all rows
                    rows = []
                    for row in result_set:
                        rows.append(list(row))

                    master_logger.info(f"Read {len(rows)} rows from hyper file")    
                    df = pd.DataFrame(rows, columns=columns)
                    master_logger.info(f"Successfully loaded {len(df)} rows × {len(df.columns)} columns")
                    return df
                    
        except Exception as e:
            master_logger.error(f"Error reading hyper file: {e}")
            return None
    
    def extract_single_datasource(self, site_id: str, datasource_id: str, auth_token: str) -> Optional[pd.DataFrame]:
        """Complete workflow: download + extract + read single datasource"""
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                tdsx_path = os.path.join(temp_dir, 'datasource.tdsx')
                if not self.download_datasource(site_id, datasource_id, auth_token, tdsx_path):
                    return None
                
                hyper_path = self.extract_hyper_from_tdsx(tdsx_path, temp_dir)
                if not hyper_path:
                    return None
                
                return self.read_hyper_data(hyper_path)
                
        except Exception as e:
            master_logger.error(f"Error in datasource extraction workflow: {e}")
            return None


# ============================================================================
# CSV EXPORT FUNCTIONALITY
# ============================================================================

class WorkbookDataExporter:
    """
    Export workbook and datasource data to CSV files
    Complete data extraction functionality with calculated fields enhancement
    """
    
    def __init__(self):
        master_logger.info("Initializing WorkbookDataExporter")
        self.export_base_dir = "tableau_exports"
        
        # Initialize calculated fields enhancer if available
        try:
            self.calc_fields_enhancer = CSVCalculatedFieldsEnhancer()
            self.calc_fields_available = True
            master_logger.info("CSV Calculated Fields Enhancer initialized")
        except (ImportError, NameError):
            self.calc_fields_enhancer = None
            self.calc_fields_available = False
            master_logger.warning("CSV Calculated Fields Enhancer not available")
    
    def _get_or_create_output_dir(self, workbook_name: str, base_dir: str) -> str:
        """
        Get existing directory for workbook or create a new one.
        Reuses existing directories instead of creating new timestamped ones.
        """
        try:
            # Ensure base directory exists
            os.makedirs(base_dir, exist_ok=True)
            
            # Look for existing directories with the same workbook name
            existing_dirs = []
            if os.path.exists(base_dir):
                for item in os.listdir(base_dir):
                    item_path = os.path.join(base_dir, item)
                    if os.path.isdir(item_path) and item.startswith(workbook_name):
                        existing_dirs.append(item_path)
            
            if existing_dirs:
                # Use the most recent existing directory
                existing_dir = max(existing_dirs, key=os.path.getmtime)
                master_logger.info(f"Reusing existing directory: {existing_dir}")
                return existing_dir
            else:
                # Create new directory without timestamp for consistency
                output_dir = os.path.join(base_dir, workbook_name)
                os.makedirs(output_dir, exist_ok=True)
                master_logger.info(f"Created new directory: {output_dir}")
                return output_dir
                
        except Exception as e:
            master_logger.error(f"Error in _get_or_create_output_dir: {e}")
            # Fallback to timestamped directory
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            fallback_dir = os.path.join(base_dir, f"{workbook_name}_{timestamp}")
            os.makedirs(fallback_dir, exist_ok=True)
            return fallback_dir
    
    def export_all_to_csv(
        self, 
        workbook_name: str,
        worksheets_data: Dict[str, pd.DataFrame],
        datasources_data: Dict[str, pd.DataFrame],
        output_dir: Optional[str] = None,
        metadata_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Export all workbook and datasource data to CSV files with calculated fields enhancement
        """
        try:
            # Create output directory structure
            if output_dir is None:
                output_dir = self._get_or_create_output_dir(workbook_name, self.export_base_dir)
            
            master_logger.info(f"Exporting to: {output_dir}")
            
            # Export worksheets
            worksheets_dir = os.path.join(output_dir, "worksheets")
            worksheet_results = self._export_worksheets(
                worksheets_data, worksheets_dir
            )
            
            # Export datasources
            datasources_dir = os.path.join(output_dir, "datasources")
            datasource_results = self._export_datasources(
                datasources_data, datasources_dir
            )
            
            # Enhance CSV files with calculated fields if metadata is available
            enhancement_results = self._enhance_csv_files_with_calculated_fields(
                worksheet_results, 
                datasource_results, 
                metadata_path,
                workbook_name
            )
            
            # Create summary file
            summary = self._create_export_summary(
                workbook_name,
                worksheet_results,
                datasource_results,
                output_dir,
                enhancement_results
            )
            
            master_logger.info(f"Export complete: {output_dir}")
            
            return {
                'success': True,
                'output_dir': output_dir,
                'worksheets_exported': len(worksheet_results['exported']),
                'datasources_exported': len(datasource_results['exported']),
                'total_worksheet_rows': worksheet_results['total_rows'],
                'total_datasource_rows': datasource_results['total_rows'],
                'summary_file': summary,
                'calculated_fields_enhancement': enhancement_results
            }
            
        except Exception as e:
            master_logger.error(f"Error exporting data: {e}")
            return {
                'success': False,
                'error': str(e),
                'output_dir': output_dir
            }
    
    def _export_worksheets(
        self, 
        worksheets_data: Dict[str, pd.DataFrame], 
        output_dir: str
    ) -> Dict:
        """Export worksheet data to CSV files"""
        
        os.makedirs(output_dir, exist_ok=True)
        
        exported = []
        failed = []
        total_rows = 0
        
        for ws_name, df in worksheets_data.items():
            try:
                if df is None or df.empty:
                    master_logger.warning(f"Skipping empty worksheet: {ws_name}")
                    failed.append({'name': ws_name, 'reason': 'empty'})
                    continue
                
                # Sanitize filename
                safe_name = self._sanitize_filename(ws_name)
                filepath = os.path.join(output_dir, f"{safe_name}.csv")
                
                # Export to CSV
                df.to_csv(filepath, index=False, encoding='utf-8')
                
                exported.append({
                    'name': ws_name,
                    'filepath': filepath,
                    'rows': len(df),
                    'columns': len(df.columns)
                })
                
                total_rows += len(df)
                master_logger.info(f"Exported worksheet: {ws_name} ({len(df)} rows)")
                
            except Exception as e:
                master_logger.error(f"Failed to export worksheet {ws_name}: {e}")
                failed.append({'name': ws_name, 'reason': str(e)})
        
        return {
            'exported': exported,
            'failed': failed,
            'total_rows': total_rows
        }
    
    def _export_datasources(
        self, 
        datasources_data: Dict[str, pd.DataFrame], 
        output_dir: str
    ) -> Dict:
        """Export datasource data to CSV files"""
        
        os.makedirs(output_dir, exist_ok=True)
        
        exported = []
        failed = []
        total_rows = 0
        
        for ds_name, df in datasources_data.items():
            try:
                if df is None or df.empty:
                    master_logger.warning(f"Skipping empty datasource: {ds_name}")
                    failed.append({'name': ds_name, 'reason': 'empty'})
                    continue
                
                # Sanitize filename
                safe_name = self._sanitize_filename(ds_name)
                filepath = os.path.join(output_dir, f"{safe_name}.csv")
                
                # Export to CSV
                df.to_csv(filepath, index=False, encoding='utf-8')
                
                exported.append({
                    'name': ds_name,
                    'filepath': filepath,
                    'rows': len(df),
                    'columns': len(df.columns)
                })
                
                total_rows += len(df)
                master_logger.info(f"Exported datasource: {ds_name} ({len(df)} rows)")
                
            except Exception as e:
                master_logger.error(f"Failed to export datasource {ds_name}: {e}")
                failed.append({'name': ds_name, 'reason': str(e)})
        
        return {
            'exported': exported,
            'failed': failed,
            'total_rows': total_rows
        }
    
    def _enhance_csv_files_with_calculated_fields(
        self,
        worksheet_results: Dict,
        datasource_results: Dict, 
        metadata_path: Optional[str],
        workbook_name: str
    ) -> Dict[str, Any]:
        """Enhance exported CSV files with calculated fields from metadata"""
        
        enhancement_results = {
            'enabled': self.calc_fields_available,
            'metadata_available': metadata_path is not None,
            'enhanced_files': [],
            'failed_files': [],
            'calculated_fields_added': 0,
            'total_files_processed': 0
        }
        
        if not self.calc_fields_available:
            master_logger.info("CSV calculated fields enhancer not available - skipping enhancement")
            return enhancement_results
        
        if not metadata_path or not os.path.exists(metadata_path):
            master_logger.warning(f"Metadata file not found: {metadata_path} - skipping calculated fields enhancement")
            return enhancement_results
        
        master_logger.info(f"Enhancing CSV files with calculated fields using metadata: {metadata_path}")
        
        # Get all exported CSV files from both worksheets and datasources
        all_exported_files = []
        all_exported_files.extend(worksheet_results.get('exported', []))
        all_exported_files.extend(datasource_results.get('exported', []))
        
        enhancement_results['total_files_processed'] = len(all_exported_files)
        
        # Get calculated fields summary first
        try:
            calc_fields_summary = self.calc_fields_enhancer.get_calculated_fields_summary(metadata_path)
            non_aggregated_count = calc_fields_summary.get('non_aggregated_fields', 0)
            
            master_logger.info(f"Found {non_aggregated_count} non-aggregated calculated fields that can be added to CSV")
            
            if non_aggregated_count == 0:
                master_logger.info("No non-aggregated calculated fields found - CSV files will not be enhanced")
                return enhancement_results
                
        except Exception as e:
            master_logger.error(f"Error getting calculated fields summary: {e}")
            enhancement_results['failed_files'].append({
                'file': 'metadata_analysis',
                'error': str(e)
            })
            return enhancement_results
        
        # Enhance each CSV file
        for file_info in all_exported_files:
            csv_filepath = file_info['filepath']
            file_name = file_info['name']
            
            try:
                master_logger.info(f"Enhancing CSV file: {csv_filepath}")
                
                # Create enhanced version of the file
                enhanced_path = self.calc_fields_enhancer.enhance_csv_with_calculated_fields(
                    csv_path=csv_filepath,
                    metadata_path=metadata_path,
                    output_path=csv_filepath  # Overwrite original file
                )
                
                # Get the number of calculated fields added by comparing columns
                try:
                    enhanced_df = pd.read_csv(enhanced_path)
                    original_columns = file_info.get('columns', 0)
                    enhanced_columns = len(enhanced_df.columns)
                    calc_fields_added = max(0, enhanced_columns - original_columns)
                    
                    enhancement_results['enhanced_files'].append({
                        'name': file_name,
                        'filepath': enhanced_path,
                        'original_columns': original_columns,
                        'enhanced_columns': enhanced_columns,
                        'calculated_fields_added': calc_fields_added
                    })
                    
                    enhancement_results['calculated_fields_added'] += calc_fields_added
                    
                    master_logger.info(f"Successfully enhanced {file_name}: {original_columns} -> {enhanced_columns} columns (+{calc_fields_added} calculated fields)")
                    
                except Exception as column_error:
                    master_logger.warning(f"Could not count enhanced columns for {file_name}: {column_error}")
                    enhancement_results['enhanced_files'].append({
                        'name': file_name,
                        'filepath': enhanced_path,
                        'original_columns': file_info.get('columns', 0),
                        'enhanced_columns': 'unknown',
                        'calculated_fields_added': 'unknown'
                    })
                
            except Exception as e:
                master_logger.error(f"Failed to enhance CSV file {csv_filepath}: {e}")
                enhancement_results['failed_files'].append({
                    'name': file_name,
                    'filepath': csv_filepath,
                    'error': str(e)
                })
        
        master_logger.info(f"CSV enhancement completed: {len(enhancement_results['enhanced_files'])} files enhanced, {len(enhancement_results['failed_files'])} failed")
        master_logger.info(f"Total calculated fields added across all files: {enhancement_results['calculated_fields_added']}")
        
        return enhancement_results
    
    def _create_export_summary(
        self,
        workbook_name: str,
        worksheet_results: Dict,
        datasource_results: Dict,
        output_dir: str,
        enhancement_results: Optional[Dict[str, Any]] = None
    ) -> str:
        """Create summary JSON file for the export"""
        
        summary = {
            'workbook_name': workbook_name,
            'export_timestamp': datetime.now().isoformat(),
            'output_directory': output_dir,
            'worksheets': {
                'exported_count': len(worksheet_results['exported']),
                'failed_count': len(worksheet_results['failed']),
                'total_rows': worksheet_results['total_rows'],
                'details': worksheet_results['exported']
            },
            'datasources': {
                'exported_count': len(datasource_results['exported']),
                'failed_count': len(datasource_results['failed']),
                'total_rows': datasource_results['total_rows'],
                'details': datasource_results['exported']
            }
        }
        
        # Add calculated fields enhancement results if available
        if enhancement_results:
            summary['calculated_fields_enhancement'] = {
                'enabled': enhancement_results.get('enabled', False),
                'metadata_available': enhancement_results.get('metadata_available', False),
                'total_files_processed': enhancement_results.get('total_files_processed', 0),
                'enhanced_files_count': len(enhancement_results.get('enhanced_files', [])),
                'failed_files_count': len(enhancement_results.get('failed_files', [])),
                'total_calculated_fields_added': enhancement_results.get('calculated_fields_added', 0),
                'enhanced_files': enhancement_results.get('enhanced_files', []),
                'failed_files': enhancement_results.get('failed_files', [])
            }
        
        summary_path = os.path.join(output_dir, 'export_summary.json')
        
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        
        master_logger.info(f"Summary created: {summary_path}")
        
        return summary_path
    
    def _sanitize_filename(self, name: str) -> str:
        """Sanitize name for use as filename"""
        # Remove or replace invalid characters
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, '_')
        
        # Limit length
        if len(name) > 200:
            name = name[:200]
        
        return name.strip()


# ============================================================================
# COMPLETE WORKBOOK DATA MANAGER
# ============================================================================

class CompleteWorkbookDataManager:
    """Orchestrate complete workbook data fetching with metadata"""
    
    def __init__(self, connection_manager):
        master_logger.info("Initializing CompleteWorkbookDataManager")
        self.connection_manager = connection_manager
        self.twb_parser = TWBParser()
        self.ds_extractor = DatasourceExtractor(connection_manager)
        self.exporter = WorkbookDataExporter()
    
    @function_logger('tableau_backend.CompleteWorkbookDataManager.fetch_complete_workbook_data')
    async def fetch_complete_workbook_data(self, 
                                           workbook_name: str,
                                           workbook_id: str,
                                           site_id: str,
                                           auth_token: str,
                                           export_to_csv: bool = True,
                                           progress_callback: callable = None) -> Dict[str, Any]:
        """
        Fetch complete workbook data with metadata
        Combines TWB parsing + datasource extraction
        
        Args:
            workbook_name: Name of the workbook
            workbook_id: Tableau workbook ID
            site_id: Tableau site ID
            auth_token: Authentication token
            export_to_csv: Whether to export data to CSV
            progress_callback: Optional callback function to report progress updates.
                              Called with dict containing stage, message, and other progress info.
        """
        try:
            master_logger.info(f"=== FETCHING COMPLETE WORKBOOK DATA ===")
            master_logger.info(f"Workbook: {workbook_name}")
            
            result = {
                'success': False,
                'workbook_name': workbook_name,
                'metadata': {},
                'datasources_data': {},
                'charts_metadata': {},
                'error': None
            }
            
            # Helper function to safely call progress callback
            def report_progress(**kwargs):
                if progress_callback and callable(progress_callback):
                    try:
                        progress_callback(kwargs)
                    except Exception as e:
                        master_logger.warning(f"Progress callback error (non-critical): {e}")
            
            # Helper function to generate chart column mappings
            def generate_chart_column_mappings():
                """Generate chart_column_mappings.json from metadata directory"""
                try:
                    from CHART_COLUMN_MAPPINGS_READER import TableauColumnMappingExtractor
                    metadata_dir = "tableau_metadata"
                    
                    if not os.path.exists(metadata_dir):
                        master_logger.warning(f"Metadata directory not found: {metadata_dir}")
                        master_logger.warning("Chart column mappings cannot be generated without metadata")
                        return None
                    
                    master_logger.info(f"📊 Generating chart column mappings from metadata...")
                    os.makedirs(metadata_dir, exist_ok=True)
                    
                    extractor = TableauColumnMappingExtractor(
                        metadata_dir=metadata_dir,
                        output_file="chart_column_mappings.json")
                    extractor.process_all_dashboards()
                    extractor.save_results()
                    
                    master_logger.info(f"✅ Chart column mappings generated successfully")
                    master_logger.info(f"   Output: chart_column_mappings.json")
                    master_logger.info(f"   Dashboards: {extractor.results['summary']['total_dashboards']}")
                    master_logger.info(f"   Charts: {extractor.results['summary']['total_charts']}")
                    
                    return extractor.results
                    
                except Exception as mapping_error:
                    master_logger.warning(f"Could not generate chart column mappings: {mapping_error}")
                    master_logger.warning(f"This is non-critical - continuing without mappings")
                    return None
            
            # === CHECK IF CSVs ALREADY EXIST ===
            csv_dir = os.path.join("tableau_exports", workbook_name, "datasources")
            
            if os.path.exists(csv_dir):
                csv_files = [f for f in os.listdir(csv_dir) if f.endswith('.csv')]
                
                if len(csv_files) > 0:
                    master_logger.info(f"✅ Found {len(csv_files)} existing CSV(s) in {csv_dir}")
                    master_logger.info(f"⏭️  Skipping extraction - using existing data")
                    
                    # Mark as successful without doing extraction
                    result['success'] = True
                    result['csv_export_dir'] = os.path.join("tableau_exports", workbook_name)
                    result['csv_files'] = csv_files
                    result['datasources_exported'] = len(csv_files)
                    result['skipped_extraction'] = True
                    
                    # Generate chart_column_mappings.json (depends on metadata, not CSV data)
                    result['chart_column_mappings'] = generate_chart_column_mappings()
                    
                    # Report completion
                    report_progress(stage='complete', message='Using existing CSV data')
                    
                    return result
            
            # If we reach here, CSVs don't exist - proceed with normal extraction
            master_logger.info(f"No existing CSVs found in {csv_dir} - proceeding with full extraction")
            
            # Step 1: Download TWB file
            report_progress(stage='downloading', message='Downloading workbook file...')
            twb_path = await self._download_twb_file(workbook_name, workbook_id, site_id, auth_token)
            if not twb_path:
                result['error'] = "Failed to download TWB file"
                return result
            
            # Step 2: Parse TWB metadata
            report_progress(stage='parsing', message='Parsing workbook structure and metadata...')
            if not self.twb_parser.parse_twbx(twb_path) and not self.twb_parser.parse_twb(twb_path):
                result['error'] = "Failed to parse TWB metadata"
                return result
            
            # Export parsed metadata to organized folder structure
            try:
                metadata_path = self.twb_parser.export_metadata_summary(workbook_name)
                if metadata_path:
                    result['metadata_path'] = metadata_path
                    master_logger.info(f"Metadata successfully exported to: {metadata_path}")
                else:
                    master_logger.warning("Metadata export returned None - export may have failed")
            except Exception as e:
                master_logger.warning(f"Could not export metadata summary: {e}")
                master_logger.warning(traceback.format_exc())
            
            # Export field descriptions to organized folder structure
            try:
                descriptions_path = self.twb_parser.export_field_descriptions(workbook_name)
                if descriptions_path:
                    result['descriptions_path'] = descriptions_path
                    master_logger.info(f"Field descriptions successfully exported to: {descriptions_path}")
                else:
                    master_logger.warning("Field descriptions export returned None - export may have failed")
            except Exception as e:
                master_logger.warning(f"Could not export field descriptions: {e}")
                master_logger.warning(traceback.format_exc())
            # Generate chart column mappings (will process the metadata file we just created)
            result['chart_column_mappings'] = generate_chart_column_mappings()
            
            result['charts_metadata'] = {
                name: {
                    'x_axis': chart.x_axis,
                    'y_axis': chart.y_axis,
                    'color_encoding': chart.color_encoding,
                    'size_encoding': chart.size_encoding,
                    'detail_fields': chart.detail_fields,
                    'calculated_fields': chart.calculated_fields,
                    'filters': chart.filters,
                    'datasources': chart.datasources_used
                }
                for name, chart in self.twb_parser.charts.items()
            }
            
            # Add readable metadata alongside internal metadata
            result['charts_metadata_readable'] = {
                name: self.twb_parser.get_readable_chart_metadata(name)
                for name in self.twb_parser.charts.keys()
            }
            
            result['metadata'] = {
                'datasources': list(self.twb_parser.datasources.keys()),
                'charts': list(self.twb_parser.charts.keys()),
                'blends': len(self.twb_parser.blends)
            }
            
            # Step 3: Extract datasource data from the downloaded .twbx
            master_logger.info("Extracting datasource data from .twbx package")
            report_progress(stage='extracting', message='Extracting data sources from workbook...')
            
            if twb_path and os.path.exists(twb_path):
                try:
                    # The twb_path is actually the .twbx file
                    with zipfile.ZipFile(twb_path, 'r') as zip_ref:
                        # List all files in the package
                        all_files = zip_ref.namelist()
                        master_logger.info(f"Files in .twbx: {all_files}")
                        
                        # Find .hyper files
                        hyper_files = [f for f in all_files if f.endswith('.hyper')]
                        master_logger.info(f"Found {len(hyper_files)} .hyper files: {hyper_files}")
                        
                        # Find .csv files
                        csv_files = [f for f in all_files if f.endswith('.csv')]
                        master_logger.info(f"Found {len(csv_files)} .csv files: {csv_files}")
                        
                        total_datasources = len(hyper_files) + len(csv_files)
                        report_progress(
                            stage='extracting',
                            message=f'Processing {total_datasources} data source(s)...',
                            total_datasources=total_datasources,
                            datasources_processed=0
                        )
                        
                        if hyper_files or csv_files:
                            # Extract to temp directory and read
                            temp_extract_dir = tempfile.mkdtemp()
                            
                            datasources_completed = 0
                            for hyper_file in hyper_files:
                                try:
                                    ds_name = os.path.basename(hyper_file).replace('.hyper', '')
                                    report_progress(
                                        stage='extracting',
                                        message=f'Reading data source: {ds_name}',
                                        datasources_processed=datasources_completed,
                                        total_datasources=total_datasources,
                                        current_item=ds_name
                                    )
                                    
                                    # Extract hyper file
                                    hyper_path = zip_ref.extract(hyper_file, temp_extract_dir)
                                    master_logger.info(f"Extracted .hyper to: {hyper_path}")
                                    
                                    # Read data from hyper file
                                    df = self.ds_extractor.read_hyper_data(hyper_path)
                                    
                                    if df is not None and not df.empty:
                                        # Use a clean name for the datasource
                                        result['datasources_data'][ds_name] = df
                                        master_logger.info(f"Loaded {len(df)} rows from {ds_name}")
                                    else:
                                        master_logger.warning(f"No data in hyper file: {hyper_file}")
                                    
                                    datasources_completed += 1
                                    
                                    # Report completion immediately after reading
                                    report_progress(
                                        stage='extracting',
                                        message=f'Completed data source {datasources_completed}/{total_datasources}: {ds_name}',
                                        datasources_processed=datasources_completed,
                                        total_datasources=total_datasources,
                                        current_item=ds_name
                                    )
                                        
                                except Exception as hyper_error:
                                    master_logger.error(f"Error reading hyper file {hyper_file}: {hyper_error}")
                                    datasources_completed += 1
                            
                            # Process CSV files
                            for csv_file in csv_files:
                                try:
                                    ds_name = os.path.basename(csv_file).replace('.csv', '')
                                    report_progress(
                                        stage='extracting',
                                        message=f'Reading data source: {ds_name}',
                                        datasources_processed=datasources_completed,
                                        total_datasources=total_datasources,
                                        current_item=ds_name
                                    )
                                    
                                    # Extract CSV file
                                    csv_path = zip_ref.extract(csv_file, temp_extract_dir)
                                    master_logger.info(f"Extracted .csv to: {csv_path}")
                                    
                                    # Read CSV data
                                    import pandas as pd
                                    df = pd.read_csv(csv_path)
                                    
                                    if df is not None and not df.empty:
                                        # Use a clean name for the datasource
                                        result['datasources_data'][ds_name] = df
                                        master_logger.info(f"Loaded {len(df)} rows from {ds_name}.csv ({len(df.columns)} columns)")
                                    else:
                                        master_logger.warning(f"No data in CSV file: {csv_file}")
                                    
                                    datasources_completed += 1
                                    
                                    # Report completion immediately after reading
                                    report_progress(
                                        stage='extracting',
                                        message=f'Completed data source {datasources_completed}/{total_datasources}: {ds_name}',
                                        datasources_processed=datasources_completed,
                                        total_datasources=total_datasources,
                                        current_item=ds_name
                                    )
                                        
                                except Exception as csv_error:
                                    master_logger.error(f"Error reading CSV file {csv_file}: {csv_error}")
                                    master_logger.error(traceback.format_exc())
                                    datasources_completed += 1
                            
                            # Cleanup temp directory
                            try:
                                shutil.rmtree(temp_extract_dir)
                            except:
                                pass
                        else:
                            # Live connection changes by Aniket - Use VizQL for live connections
                            master_logger.warning("No .hyper or .csv files found in .twbx package")
                            master_logger.info("🔄 Attempting VizQL extraction for live connection workbook...")

                            # Get available views for VizQL extraction
                            views = None
                            try:
                                # Try REST API first
                                master_logger.info("Attempting to get views via REST API...")
                                views = self.connection_manager.get_workbook_views(site_id, workbook_id, auth_token)
                                master_logger.info(f"✅ Got {len(views)} views via REST API")
                            except Exception as rest_error:
                                master_logger.warning(f"REST API failed: {rest_error}")
                                master_logger.info("Falling back to metadata-based views...")

                                # Fall back to using views from TWB metadata
                                if result.get('metadata') and result['metadata'].get('twb_metadata'):
                                    charts = result['metadata']['twb_metadata'].get('charts', [])
                                    if charts:
                                        # Convert chart names to view format for VizQL
                                        views = [{'name': chart, 'id': None} for chart in charts]
                                        master_logger.info(f"✅ Using {len(views)} views from TWB metadata")
                                    else:
                                        master_logger.warning("No charts found in metadata")
                                else:
                                    master_logger.warning("No metadata available for fallback")

                            # Proceed with VizQL extraction if we have views
                            if views:
                                try:
                                    master_logger.info(f"🚀 Starting VizQL Trust Auth extraction for {len(views)} views...")

                                    # Extract data using VizQL with Trust Auth
                                    vizql_data = await self._extract_data_via_vizql(
                                        workbook_id=workbook_id,
                                        workbook_name=workbook_name,
                                        site_id=site_id,
                                        auth_token=auth_token,
                                        views=views,
                                        progress_callback=progress_callback
                                    )

                                    # Add VizQL data to result
                                    if vizql_data:
                                        result['datasources_data'].update(vizql_data)
                                        master_logger.info(f"✅ VizQL extraction successful: {len(vizql_data)} view(s) extracted")
                                    else:
                                        master_logger.warning("⚠️  VizQL extraction returned no data")

                                except Exception as vizql_error:
                                    master_logger.error(f"❌ VizQL Trust Auth extraction failed: {vizql_error}")
                                    master_logger.error(traceback.format_exc())
                            else:
                                master_logger.warning("❌ No views available for VizQL extraction")

                except Exception as extract_error:
                    master_logger.error(f"Error extracting data from .twbx: {extract_error}")
                    master_logger.error(traceback.format_exc())
            else:
                master_logger.warning("No .twbx file available for data extraction")
            
            # Export to CSV (datasources + worksheet data)
            if export_to_csv:
                report_progress(stage='exporting', message='Exporting data to CSV files...')
                worksheets_data = {}
                
                # Try to get worksheet data as well
                try:
                    # Create a minimal chat state for data fetching
                    class MinimalChatState:
                        def __init__(self, workbook_name, site_id, auth_token):
                            self.workbook_name = workbook_name
                            self.site_id = site_id  
                            self.auth_token = auth_token
                            self.available_views = []  # Will be populated
                            
                    # Get available views from connection manager
                    try:
                        views_list = self.connection_manager.get_workbook_views(site_id, workbook_id, auth_token)
                        if views_list:
                            available_views = [
                                {'name': view.get('name'), 'id': view.get('id')}
                                for view in views_list
                            ]
                        else:
                            available_views = []
                    except Exception as e:
                        master_logger.warning(f"Could not fetch views for worksheet data: {e}")
                        available_views = []
                    
                    if available_views:
                        # Create minimal state
                        minimal_state = MinimalChatState(workbook_name, site_id, auth_token)
                        minimal_state.available_views = available_views
                        
                        # Use data processor to get worksheet data with progress reporting
                        data_processor = TableauDataProcessor()
                        data_processor.config = self.connection_manager.config  # Use connection manager's config
                        total_worksheets = len(available_views)
                        for idx, view in enumerate(available_views, 1):
                            # Report worksheet progress
                            report_progress(
                                stage='fetching_worksheets',
                                message=f'Fetching worksheet {idx}/{total_worksheets}: {view["name"]}',
                                worksheets_processed=idx,
                                total_worksheets=total_worksheets,
                                current_item=view['name']
                            )
                            
                            try:
                                view_data = data_processor.get_view_data(site_id, view['id'], auth_token)
                                if view_data is not None and not view_data.empty:
                                    worksheets_data[view['name']] = view_data
                                    master_logger.info(f"Retrieved worksheet data: {view['name']} ({len(view_data)} rows)")
                            except Exception as e:
                                master_logger.warning(f"Could not get data for worksheet {view['name']}: {e}")
                    
                except Exception as e:
                    master_logger.warning(f"Could not fetch worksheet data: {e}")
                
                # Export both datasources and worksheets
                if result['datasources_data'] or worksheets_data:
                    # Get metadata path for calculated fields enhancement
                    metadata_path = result.get('metadata_path')
                    
                    export_result = self.exporter.export_all_to_csv(
                        workbook_name=workbook_name,
                        worksheets_data=worksheets_data,
                        datasources_data=result['datasources_data'],
                        metadata_path=metadata_path
                    )
                    result['csv_export'] = export_result
                    master_logger.info(f"Data exported to CSV: {export_result.get('output_dir')}")
                    master_logger.info(f"   • Worksheets exported: {len(worksheets_data)}")
                    master_logger.info(f"   • Datasources exported: {len(result['datasources_data'])}")
                else:
                    master_logger.info("No data available for CSV export")
            else:
                master_logger.info("CSV export disabled")
            
            result['success'] = True

            # Log summary of what was extracted
            master_logger.info(f"=" * 80)
            master_logger.info(f"COMPLETE WORKBOOK DATA EXTRACTION SUMMARY")
            master_logger.info(f"=" * 80)
            master_logger.info(f"Charts parsed: {len(result['charts_metadata'])}")
            master_logger.info(f"Datasources extracted: {len(result['datasources_data'])}")
            for ds_name, df in result['datasources_data'].items():
                master_logger.info(f"   • {ds_name}: {len(df):,} rows × {len(df.columns)} columns")
            if 'csv_export' in result:
                export_info = result['csv_export']
                master_logger.info(f"CSV export location: {export_info.get('output_dir')}")
                if export_info.get('success'):
                    master_logger.info(f"   • Worksheets exported: {export_info.get('worksheets_exported', 0)}")
                    master_logger.info(f"   • Datasources exported: {export_info.get('datasources_exported', 0)}")
                    master_logger.info(f"   • Total rows exported: {export_info.get('total_worksheet_rows', 0) + export_info.get('total_datasource_rows', 0):,}")
            if 'metadata_path' in result:
                master_logger.info(f"Metadata export location: {result['metadata_path']}")
                metadata_dir = os.path.dirname(result['metadata_path'])
                master_logger.info(f"   • Metadata folder: {metadata_dir}")
            if 'descriptions_path' in result:
                master_logger.info(f"Field descriptions export location: {result['descriptions_path']}")
                descriptions_dir = os.path.dirname(result['descriptions_path'])
                master_logger.info(f"   • Field descriptions folder: {descriptions_dir}")
            master_logger.info(f"=" * 80)
            
            master_logger.info(f"Complete workbook data fetched successfully")
            
            return result
            
        except Exception as e:
            master_logger.error(f"Error fetching complete workbook data: {e}")
            return {
                'success': False,
                'workbook_name': workbook_name,
                'error': str(e),
                'metadata': {},
                'datasources_data': {},
                'charts_metadata': {}
            }
    
    # Live connection changes by Aniket - VizQL with Trusted Authentication for production
    async def _extract_data_via_vizql(self, workbook_id: str, workbook_name: str, site_id: str, auth_token: str,
                                      views: List[dict], progress_callback: callable = None) -> Dict[str, pl.DataFrame]:
        """
        Extract data from live connection workbooks using VizQL with Trusted Authentication

        PRODUCTION-READY: Fully automated, no manual intervention required

        Uses Tableau Trusted Authentication to:
        1. Request trusted ticket from Tableau Server
        2. Redeem ticket for VizQL session cookies
        3. Query views via VizQL to get underlying data
        4. Parse and return full Polars DataFrames (optimized for large data)

        Args:
            workbook_id: Workbook ID
            workbook_name: Workbook name
            site_id: Site ID
            auth_token: Authentication token (PAT)
            views: List of view dictionaries with 'id' and 'name'
            progress_callback: Optional progress callback

        Returns:
            Dict mapping view names to Polars DataFrames with FULL underlying data
        """
        master_logger.info("=" * 80)
        master_logger.info("LIVE CONNECTION DATA EXTRACTION VIA VIZQL (Trusted Auth) - by Aniket")
        master_logger.info("=" * 80)

        vizql_data = {}

        def report_progress(**kwargs):
            if progress_callback and callable(progress_callback):
                try:
                    progress_callback(kwargs)
                except Exception as e:
                    master_logger.warning(f"Progress callback error (non-critical): {e}")

        total_views = len(views)
        master_logger.info(f"Extracting data from {total_views} view(s) using VizQL with Trusted Auth")

        # STEP 1: Get username for trusted ticket (required by Tableau)
        # For now, use a placeholder - admin will configure this
        username = self.connection_manager.config.get('tableau_username', 'tableau_user')
        master_logger.info(f"[VIZQL] Using username: {username}")

        # STEP 2: Test trusted authentication ONCE before looping through all views
        # This prevents wasting time retrying for every view when server config is wrong
        master_logger.info("[VIZQL] ⚠️  PRE-FLIGHT CHECK: Testing trusted authentication before processing all views...")

        try:
            test_ticket = await self.connection_manager._get_trusted_ticket(
                username=username,
                site_content_url=self.connection_manager.config.get('site_content_url', '')
            )

            if not test_ticket:
                master_logger.error("=" * 80)
                master_logger.error("[VIZQL] ❌ TRUSTED AUTHENTICATION PRE-FLIGHT CHECK FAILED")
                master_logger.error("[VIZQL] Aborting VizQL extraction - no views will be processed")
                master_logger.error("[VIZQL] ")
                master_logger.error("[VIZQL] To fix this, configure Trusted Authentication on Tableau Server:")
                master_logger.error("[VIZQL]   1. Enable Trusted Authentication in Tableau Server settings")
                master_logger.error("[VIZQL]   2. Add your server IP to the trusted hosts list")
                master_logger.error("[VIZQL]   3. Verify the username is correct in config")
                master_logger.error("=" * 80)
                return vizql_data  # Return empty dict immediately - don't process any views

            master_logger.info("[VIZQL] ✅ Pre-flight check passed - trusted authentication is working!")
        except Exception as preflight_error:
            master_logger.error("=" * 80)
            master_logger.error(f"[VIZQL] ❌ PRE-FLIGHT CHECK ERROR: {preflight_error}")
            master_logger.error("[VIZQL] Aborting VizQL extraction for all views")
            master_logger.error("=" * 80)
            return vizql_data  # Return empty dict on any error

        for idx, view in enumerate(views, 1):
            view_id = view.get('id')
            view_name = view.get('name')

            try:
                master_logger.info(f"[{idx}/{total_views}] Processing view: {view_name}")

                report_progress(
                    stage='vizql_extraction',
                    message=f'Extracting live data from view {idx}/{total_views}: {view_name}',
                    views_processed=idx - 1,
                    total_views=total_views,
                    current_item=view_name
                )

                # Extract data using VizQL with Trusted Auth
                master_logger.info(f"[VIZQL] Extracting data using VizQL for view: {view_name}")
                df = await self.connection_manager.extract_view_data_via_vizql(
                    username=username,
                    workbook_name=workbook_name,
                    view_name=view_name,
                    site_content_url=self.connection_manager.config.get('site_content_url', '')
                )

                if df is not None and df.height > 0:  # Polars: use .height instead of .empty
                    vizql_data[view_name] = df
                    master_logger.info(f"[VIZQL] ✅ Successfully extracted data from {view_name}: {df.height} rows × {df.width} columns")

                    report_progress(
                        stage='vizql_extraction',
                        message=f'Completed view {idx}/{total_views}: {view_name} ({df.height} rows)',
                        views_processed=idx,
                        total_views=total_views,
                        current_item=view_name
                    )
                else:
                    master_logger.warning(f"[VIZQL] ⚠️  No data extracted from view: {view_name}")

            except Exception as view_error:
                master_logger.error(f"[VIZQL] Error extracting data from view {view_name}: {view_error}")
                master_logger.debug(traceback.format_exc())

        master_logger.info("=" * 80)
        master_logger.info(f"VIZQL EXTRACTION COMPLETE: {len(vizql_data)}/{total_views} views extracted")
        master_logger.info("=" * 80)

        return vizql_data

    async def _download_twb_file(self, workbook_name: str, workbook_id: str, site_id: str, auth_token: str) -> Optional[str]:
        """Download TWB/TWBX file from Tableau Server"""
        try:
            master_logger.info(f"Downloading workbook file: {workbook_name}")
            
            url = f"{self.connection_manager.config['tableau_server_url']}/api/{self.connection_manager.config['api_version']}/sites/{site_id}/workbooks/{workbook_id}/content"
            headers = {"X-Tableau-Auth": auth_token}
            
            temp_dir = tempfile.mkdtemp()
            output_path = os.path.join(temp_dir, f"{workbook_name}.twbx")
            
            response = requests.get(url, headers=headers, verify=False, stream=True, timeout=300)
            
            if response.status_code == 200:
                with open(output_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                master_logger.info(f"Workbook file downloaded to: {output_path}")
                return output_path
            else:
                master_logger.error(f"Failed to download workbook: {response.status_code}")
                return None
                
        except Exception as e:
            master_logger.error(f"Error downloading workbook file: {e}")
            return None


class TableauConnectionManager:
    """Manages Tableau Server connections with authentication"""
    
    def __init__(self):
        master_logger.info("Initializing TableauConnectionManager")

        # Config will be set dynamically in CLI mode
        self.config = TABLEAU_CONFIG if TABLEAU_CONFIG else {}

        # Import and initialize persistent auth storage (optional in CLI mode)
        try:
            from auth_storage import AuthTokenStorage
            self.auth_storage = AuthTokenStorage("auth_cache.pickle", cache_timeout_minutes=450)
        except ImportError:
            # CLI mode - auth_storage not available, that's OK
            self.auth_storage = None
            master_logger.info("Auth storage not available (CLI mode)")

        self._lock = threading.Lock()
        self._auth_lock = threading.Lock()  # Add authentication lock
        self._active_auth_requests = set()  # Track active auth requests

        master_logger.info("TableauConnectionManager initialized")
        master_logger.debug(f"Configuration keys available: {list(self.config.keys()) if self.config else 'No config'}")
        if self.config and 'tableau_server_url' in self.config:
            master_logger.info(f"Configured for server: {self.config['tableau_server_url']}")
    
    @function_logger('tableau_backend.TableauConnectionManager.authenticate')
    def authenticate(self, content_url="") -> Tuple[str, str]:
        """Authenticate to Tableau Server using configured method (PAT or username/password)"""
        if not self.config:
            raise RuntimeError("Configuration not loaded. Ensure config is set before authenticating.")

        auth_type = self.config.get('auth_type', 'personal_access_token')

        if auth_type == 'username_password':
            return self.sign_in_with_username_password(content_url)
        else:
            return self.sign_in_with_pat(content_url)
    
    @function_logger('tableau_backend.TableauConnectionManager.sign_in_with_username_password')
    def sign_in_with_username_password(self, content_url="") -> Tuple[str, str]:
        """Sign in to Tableau Server using Username and Password"""
        if not self.config:
            raise RuntimeError("Configuration not loaded. Ensure config is set before authenticating.")

        master_logger.info(f"=== TABLEAU USERNAME/PASSWORD AUTHENTICATION STARTED ===")
        master_logger.info(f"Content URL: '{content_url}'")
        master_logger.info(f"Thread ID: {threading.current_thread().ident}")

        # Create a unique key for this auth request
        auth_key = f"{content_url}_{threading.current_thread().ident}"
        master_logger.debug(f"Generated auth key: {auth_key}")
        
        # Check if same auth is already in progress
        with self._auth_lock:
            if auth_key in self._active_auth_requests:
                master_logger.warning(f"Auth request {auth_key} already in progress, waiting...")
                wait_count = 0
                while auth_key in self._active_auth_requests:
                    time.sleep(0.1)
                    wait_count += 1
                    if wait_count % 50 == 0:
                        master_logger.debug(f"Still waiting for auth request {auth_key} (waited {wait_count/10}s)")
                        
                master_logger.info("Other auth request completed, checking cache")
                cached_auth = self._get_cached_auth(content_url)
                if cached_auth:
                    master_logger.info("Found cached auth after waiting")
                    return cached_auth
            
            # Mark this auth request as active
            master_logger.debug(f"Marking auth request {auth_key} as active")
            self._active_auth_requests.add(auth_key)
        
        try:
            # Check cache first
            master_logger.info("Checking authentication cache")
            cached_auth = self._get_cached_auth(content_url)
            if cached_auth:
                master_logger.info("Found valid cached authentication")
                return cached_auth
            
            master_logger.info("No cached auth found, performing new authentication")
            
            # Perform actual authentication
            url = f"{self.config['tableau_server_url']}/api/{self.config['api_version']}/auth/signin"
            headers = {"Content-Type": "application/xml"}
            
            master_logger.info(f"Authentication URL: {url}")
            master_logger.info(f"Username: {self.config.get('username', 'NOT_SET')}")
            master_logger.debug(f"Request headers: {headers}")
            
            # Build site element
            if content_url:
                site_element = f'<site contentUrl="{content_url}"/>'
            else:
                site_element = '<site/>'
            
            master_logger.debug(f"Site element: {site_element}")
            
            # Build XML payload for username/password authentication
            payload = f'''
            <tsRequest>
                <credentials name="{self.config['username']}" password="{self.config['password']}">
                    {site_element}
                </credentials>
            </tsRequest>'''.strip()
            
            master_logger.debug("Payload prepared (credentials masked)")
            master_logger.info("Sending authentication request to Tableau server")
            
            start_time = time.time()
            response = requests.post(url, headers=headers, data=payload, verify=False, timeout=300)
            auth_duration = time.time() - start_time
            
            master_logger.info(f"Authentication request completed in {auth_duration:.2f}s")
            master_logger.info(f"Response status code: {response.status_code}")
            
            if response.status_code == 200:
                try:
                    # Parse the XML response
                    master_logger.info("Parsing authentication response")
                    root = ET.fromstring(response.text)
                    namespace = {'ts': 'http://tableau.com/api'}
                    
                    auth_token = root.find(".//ts:credentials", namespace).attrib['token']
                    site_id = root.find(".//ts:site", namespace).attrib['id']
                    
                    master_logger.info(f"Extracted auth token (length: {len(auth_token)})")
                    master_logger.info(f"Site ID: {site_id}")
                    
                    # Cache the successful authentication
                    master_logger.debug("Caching authentication credentials")
                    self._cache_auth(content_url, auth_token, site_id)
                    
                    master_logger.info("=== TABLEAU USERNAME/PASSWORD AUTHENTICATION SUCCESSFUL ===")
                    print(f"Successfully authenticated to site: {site_id}")
                    
                    return auth_token, site_id
                    
                except Exception as parse_error:
                    master_logger.error(f"Failed to parse authentication response: {parse_error}")
                    master_logger.debug(f"Response content: {response.text[:500]}...")
                    raise Exception(f"Failed to parse authentication response: {parse_error}")
                    
            else:
                master_logger.error(f"Authentication failed with status code: {response.status_code}")
                master_logger.error(f"Response text: {response.text}")
                raise Exception(f"Authentication failed: HTTP {response.status_code}")
                
        except requests.exceptions.ConnectionError as e:
            master_logger.error(f"Connection error during authentication: {type(e).__name__}: {str(e)}")
            
            if "Failed to resolve" in str(e) or "nodename nor servname" in str(e):
                error_msg = f"Cannot reach server '{self.config['tableau_server_url']}'. Please check your network connection and server URL."
                master_logger.error(f"DNS resolution failed: {error_msg} Original error: {str(e)}")
                raise Exception(error_msg)
            else:
                master_logger.error(f"Network connection failed: {str(e)}")
                raise Exception(f"Network connection failed: {str(e)}")
            
        except Exception as e:
            master_logger.error(f"Unexpected authentication error: {type(e).__name__}: {str(e)}")
            master_logger.error(f"Full traceback: {traceback.format_exc()}")
            raise Exception(f"Authentication failed: {str(e)}")
            
        finally:
            # Remove from active requests
            with self._auth_lock:
                master_logger.debug(f"Removing auth request {auth_key} from active requests")
                self._active_auth_requests.discard(auth_key)

    @function_logger('tableau_backend.TableauConnectionManager.sign_in_with_pat')
    def sign_in_with_pat(self, content_url="") -> Tuple[str, str]:
        """Sign in to Tableau Server using Personal Access Token with race condition protection"""
        if not self.config:
            raise RuntimeError("Configuration not loaded. Ensure config is set before authenticating.")

        master_logger.info(f"=== TABLEAU AUTHENTICATION STARTED ===")
        master_logger.info(f"Content URL: '{content_url}'")
        master_logger.info(f"Thread ID: {threading.current_thread().ident}")

        # Create a unique key for this auth request
        auth_key = f"{content_url}_{threading.current_thread().ident}"
        master_logger.debug(f"Generated auth key: {auth_key}")
        
        # Check if same auth is already in progress
        with self._auth_lock:
            if auth_key in self._active_auth_requests:
                master_logger.warning(f"Auth request {auth_key} already in progress, waiting...")
                # Wait for the other request to complete
                print(f"Auth request {auth_key} already in progress, waiting...")
                
                wait_count = 0
                while auth_key in self._active_auth_requests:
                    time.sleep(0.1)
                    wait_count += 1
                    if wait_count % 50 == 0:  # Log every 5 seconds
                        master_logger.debug(f"Still waiting for auth request {auth_key} (waited {wait_count/10}s)")
                        
                master_logger.info("Other auth request completed, checking cache")
                
                # Try to get from cache
                cached_auth = self._get_cached_auth(content_url)
                if cached_auth:
                    master_logger.info("Found cached auth after waiting")
                    return cached_auth
            
            # Mark this auth request as active
            master_logger.debug(f"Marking auth request {auth_key} as active")
            self._active_auth_requests.add(auth_key)
        
        try:
            # Check cache first
            master_logger.info("Checking authentication cache")
            cached_auth = self._get_cached_auth(content_url)
            if cached_auth:
                master_logger.info("Found valid cached authentication")
                return cached_auth
            
            master_logger.info("No cached auth found, performing new authentication")
            
            # Perform actual authentication
            url = f"{self.config['tableau_server_url']}/api/{self.config['api_version']}/auth/signin"
            headers = {"Content-Type": "application/xml"}
            
            master_logger.info(f"Authentication URL: {url}")
            master_logger.info(f"PAT name: {self.config['personal_access_token_name']}")
            master_logger.debug(f"Request headers: {headers}")
            
            print(f"Attempting to connect to: {url}")
            print(f"Using PAT name: {self.config['personal_access_token_name']}")
            
            site_element = f'<site contentUrl="{content_url}"/>' if content_url else '<site/>'
            master_logger.debug(f"Site element: {site_element}")
            
            payload = f"""
                <tsRequest>
                    <credentials personalAccessTokenName="{self.config['personal_access_token_name']}" 
                               personalAccessTokenSecret="{self.config['personal_access_token_secret']}">
                        {site_element}
                    </credentials>
                </tsRequest>
            """
            
            master_logger.debug("Payload prepared (secrets masked)")
            master_logger.info("Sending authentication request to Tableau server")
            
            start_time = time.time()
            response = requests.post(url, headers=headers, data=payload, verify=False, timeout=300)
            request_time = time.time() - start_time
            
            master_logger.info(f"Authentication request completed in {request_time:.3f} seconds")
            master_logger.info(f"Response status code: {response.status_code}")
            master_logger.debug(f"Response headers: {dict(response.headers)}")
            
            if response.status_code == 200:
                master_logger.info("Authentication successful - parsing response")
                master_logger.debug(f"Response content length: {len(response.text)}")
                
                try:
                    root = ET.fromstring(response.text)
                    namespace = {'ts': 'http://tableau.com/api'}
                    
                    auth_token = root.find(".//ts:credentials", namespace).attrib['token']
                    site_id = root.find(".//ts:site", namespace).attrib['id']
                    
                    master_logger.info(f"Extracted auth token (length: {len(auth_token)})")
                    master_logger.info(f"Site ID: {site_id}")
                    
                    # Cache the successful authentication
                    master_logger.debug("Caching authentication credentials")
                    self._cache_auth(content_url, auth_token, site_id)
                    
                    master_logger.info("=== TABLEAU AUTHENTICATION SUCCESSFUL ===")
                    print(f"Successfully authenticated to site: {site_id}")
                    
                    return auth_token, site_id
                    
                except Exception as parse_error:
                    master_logger.error(f"Failed to parse authentication response: {parse_error}")
                    master_logger.debug(f"Response content: {response.text[:500]}...")
                    raise Exception(f"Failed to parse authentication response: {parse_error}")
                    
            else:
                master_logger.error(f"Authentication failed with status {response.status_code}")
                master_logger.error(f"Response text: {response.text}")
                raise Exception(f"Failed to sign in (Status Code: {response.status_code}): {response.text}")
                
        except requests.exceptions.ConnectionError as e:
            master_logger.error(f"Connection error during authentication: {type(e).__name__}: {str(e)}")
            
            if "Failed to resolve" in str(e) or "nodename nor servname" in str(e):
                error_msg = f"Cannot reach server '{self.config['tableau_server_url']}'. This appears to be an internal domain - ensure you're connected to the corporate VPN or internal network. Original error: {str(e)}"
                master_logger.error(f"DNS resolution failed: {error_msg}")
                raise Exception(error_msg)
            else:
                error_msg = f"Connection error: {str(e)}"
                master_logger.error(error_msg)
                raise Exception(error_msg)
                
        except requests.exceptions.Timeout as e:
            error_msg = f"Connection timeout to '{self.config['tableau_server_url']}'. Check your network connection and VPN status."
            master_logger.error(f"Request timeout: {error_msg}")
            raise Exception(error_msg)
            
        except Exception as e:
            master_logger.error(f"Unexpected authentication error: {type(e).__name__}: {str(e)}")
            master_logger.error(f"Full traceback: {traceback.format_exc()}")
            raise Exception(f"Authentication failed: {str(e)}")
            
        finally:
            # Remove from active requests
            with self._auth_lock:
                master_logger.debug(f"Removing auth request {auth_key} from active requests")
                self._active_auth_requests.discard(auth_key)
    
    def _get_cached_auth(self, content_url):
        """Get cached authentication from persistent storage if still valid"""
        if self.auth_storage is None:
            return None  # CLI mode - no caching
        return self.auth_storage.get_auth_token(content_url)

    def _cache_auth(self, content_url, auth_token, site_id):
        """Cache authentication result in persistent storage"""
        if self.auth_storage is None:
            return  # CLI mode - no caching
        self.auth_storage.store_auth_token(content_url, auth_token, site_id)
    
    def validate_token(self, auth_token: str, site_id: str) -> bool:
        """DISABLED: Always return True - relying on 7.5h time-based validation only"""
        # BLIND TRUST MODE: No server-side validation, only time-based expiration
        master_logger.info(f"🔒 BLIND_TRUST_MODE: Skipping server validation for token {auth_token[:8]}...")
        return True
    
    def get_valid_auth(self, content_url="") -> Tuple[str, str]:
        """Get valid authentication, refreshing if necessary"""
        # Use multi-layer caching for optimal performance
        return self.get_or_create_auth_from_storage(content_url)
    
    def get_or_create_auth_from_storage(self, content_url: str, chat_state: Optional['ChatState'] = None) -> Tuple[str, str]:
        """
        Multi-layer authentication caching with smart priority hierarchy:
        1. ChatState (fastest - in memory)
        2. Pickle Cache (persistent - file system)  
        3. Fresh Auth (slowest - network request)
        
        Args:
            content_url: Tableau content URL key
            chat_state: Optional ChatState object for in-memory caching
            
        Returns:
            Tuple of (auth_token, site_id)
        """
        from datetime import datetime, timedelta
        
        # Layer 1: Try ChatState first (fastest - in memory)
        if chat_state and self._is_chatstate_auth_valid(chat_state):
            # Calculate session age and remaining time
            age_seconds = (datetime.utcnow() - chat_state.connection_timestamp).total_seconds()
            remaining_hours = (timedelta(minutes=450).total_seconds() - age_seconds) / 3600
            
            master_logger.info(f"🔒 ⚡ BLIND_TRUST_CHATSTATE_HIT: {content_url} | source: ChatState | age: {age_seconds:.1f}s | remaining: {remaining_hours:.1f}h | performance: instant")
            return chat_state.auth_token, chat_state.site_id
        
        # Layer 2: Try pickle cache (persistent - file system)
        cached_auth = self._get_cached_auth(content_url)
        if cached_auth:
            auth_token, site_id = cached_auth
            
            # Update ChatState from pickle cache if provided
            if chat_state:
                self._update_chatstate_auth(chat_state, auth_token, site_id)
                master_logger.info(f"🔒 🔄 BLIND_TRUST_PICKLE_TO_CHATSTATE: {content_url} | Updated ChatState from pickle cache | performance: promoted to Layer 1")
            else:
                master_logger.info(f"🔒 📁 BLIND_TRUST_PICKLE_ONLY: {content_url} | Using pickle cache without ChatState")
            
            return auth_token, site_id
        
        # Layer 3: Fresh authentication (slowest - network request)
        import time
        auth_start_time = time.time()
        
        master_logger.info(f"🔒 [NET] BLIND_TRUST_FRESH_AUTH: Starting fresh authentication for {content_url}")
        auth_token, site_id = self.authenticate(content_url)
        
        auth_duration = time.time() - auth_start_time
        token_preview = f"{auth_token[:8]}..." if len(auth_token) > 8 else auth_token
        master_logger.info(f"🔒 [OK] BLIND_TRUST_FRESH_SUCCESS: {content_url} | token: {token_preview} | duration: {auth_duration:.2f}s | 7.5h_timer_started")
        
        # Store in both ChatState and pickle cache for future use
        if chat_state:
            self._update_chatstate_auth(chat_state, auth_token, site_id)
        
        # Pickle cache is automatically updated by _cache_auth() in authenticate()
        
        return auth_token, site_id
    
    def _is_chatstate_auth_valid(self, chat_state: 'ChatState') -> bool:
        """
        BLIND TRUST: Check if ChatState has valid authentication (7.5h time-based only)
        
        Args:
            chat_state: ChatState object to check
            
        Returns:
            True if ChatState auth is valid, False if expired (7.5h timer only)
        """
        if not (chat_state.auth_token and chat_state.site_id and chat_state.connection_timestamp):
            master_logger.debug("🔒 BLIND_TRUST: Missing auth data in ChatState")
            return False
        
        # BLIND TRUST MODE: Only check 7.5-hour timer, no server validation
        session_timeout = timedelta(minutes=450)  # 7.5 hours
        time_since_auth = datetime.utcnow() - chat_state.connection_timestamp
        is_valid = time_since_auth < session_timeout
        
        if is_valid:
            remaining_hours = (session_timeout - time_since_auth).total_seconds() / 3600
            master_logger.debug(f"🔒 BLIND_TRUST: ChatState valid for {remaining_hours:.1f}h more")
        else:
            expired_hours = time_since_auth.total_seconds() / 3600
            master_logger.info(f"🔒 BLIND_TRUST: ChatState expired {expired_hours:.1f}h ago, will re-authenticate")
            
        return is_valid
    
    def _update_chatstate_auth(self, chat_state: 'ChatState', auth_token: str, site_id: str):
        """
        Update ChatState with authentication information with performance logging
        
        Args:
            chat_state: ChatState object to update
            auth_token: Authentication token
            site_id: Site ID
        """
        import time
        start_time = time.time()
        
        # Store previous state for comparison
        had_previous_auth = bool(chat_state.auth_token)
        
        chat_state.auth_token = auth_token
        chat_state.site_id = site_id
        chat_state.connection_timestamp = datetime.utcnow()
        chat_state.update_activity()
        
        update_time = time.time() - start_time
        token_preview = f"{auth_token[:8]}..." if len(auth_token) > 8 else auth_token
        
        if had_previous_auth:
            master_logger.info(f"[UPD] CHATSTATE_REFRESH: Updated existing ChatState | token: {token_preview} | update_time: {update_time*1000:.1f}ms")
        else:
            master_logger.info(f"[NEW] CHATSTATE_INIT: Initialized new ChatState | token: {token_preview} | update_time: {update_time*1000:.1f}ms")

    def _manual_browser_login(self, driver, site_content_url: str = "") -> bool:
        """
        Opens browser for manual login and saves session cookies
        Handles Uber's two-step authentication process:
        1. Authenticate at whober.uberinternal.com
        2. Then access Tableau

        Returns True if login successful, False otherwise
        """
        try:
            import time as time_module
            import pickle

            master_logger.info("[MANUAL_LOGIN] Opening Tableau SSO for manual login...")
            master_logger.info("[MANUAL_LOGIN] ⚠️  PLEASE LOGIN MANUALLY IN THE BROWSER WINDOW")

            # Navigate to Tableau's SAML SSO endpoint
            # This will redirect to whober/USSO automatically and handle the full auth flow
            sso_url = "https://tableau.uberinternal.com/wg/saml/SSO/index.html"
            master_logger.info(f"[MANUAL_LOGIN] Loading Tableau SSO: {sso_url}")
            master_logger.info("[MANUAL_LOGIN] This will redirect to Uber authentication...")

            driver.get(sso_url)

            # Wait a bit for the redirect
            time_module.sleep(3)

            master_logger.info("[MANUAL_LOGIN] ⚠️  Please complete authentication in the browser")
            master_logger.info("[MANUAL_LOGIN] The page will redirect through Uber SSO automatically")
            master_logger.info("[MANUAL_LOGIN] Waiting for authentication to complete and Tableau cookies...")

            # Wait for user to login (check every 5 seconds for up to 5 minutes)
            max_wait_time = 300  # 5 minutes
            check_interval = 5
            elapsed = 0

            while elapsed < max_wait_time:
                time_module.sleep(check_interval)
                elapsed += check_interval

                try:
                    # Check current URL - might be on USSO page
                    current_url = driver.current_url

                    # Check if login successful by looking for cookies
                    cookies = driver.get_cookies()

                    if cookies is None:
                        master_logger.debug(f"[MANUAL_LOGIN] On redirect page ({current_url}), waiting... ({elapsed}/{max_wait_time}s)")
                        continue

                    # Look for Tableau session cookies
                    tableau_cookies = [c for c in cookies if 'workgroup_session_id' in c.get('name', '').lower()
                                     or 'tableausessionid' in c.get('name', '').lower()]

                    if tableau_cookies:
                        master_logger.info(f"[MANUAL_LOGIN] ✅ Login detected! Found {len(tableau_cookies)} session cookies")

                        # Save cookies to file
                        cookie_file = "/Users/aranja14/Desktop/chatbot/tableau_browser_cookies.pkl"
                        with open(cookie_file, 'wb') as f:
                            pickle.dump(cookies, f)

                        master_logger.info(f"[MANUAL_LOGIN] ✅ Saved {len(cookies)} cookies to {cookie_file}")
                        return True

                    master_logger.debug(f"[MANUAL_LOGIN] Waiting for login... ({elapsed}/{max_wait_time}s)")

                except Exception as check_error:
                    master_logger.debug(f"[MANUAL_LOGIN] Cookie check error (normal during SSO redirect): {check_error}")
                    continue

            master_logger.error("[MANUAL_LOGIN] ❌ Timeout waiting for login")
            return False

        except Exception as e:
            master_logger.error(f"[MANUAL_LOGIN] Error during manual login: {e}")
            return False

    def _load_saved_cookies(self, driver) -> bool:
        """Load previously saved cookies into browser"""
        try:
            import pickle
            import urllib.parse

            cookie_file = "/Users/aranja14/Desktop/chatbot/tableau_browser_cookies.pkl"

            if not os.path.exists(cookie_file):
                master_logger.warning("[COOKIES] No saved cookies found")
                return False

            with open(cookie_file, 'rb') as f:
                cookies = pickle.load(f)

            # Navigate to Tableau first (required to set cookies)
            driver.get(self.config['tableau_server_url'])
            import time as time_module
            time_module.sleep(2)

            # Inject cookies
            for cookie in cookies:
                try:
                    # Remove domain if it conflicts
                    if 'domain' in cookie:
                        cookie['domain'] = urllib.parse.urlparse(self.config['tableau_server_url']).hostname
                    driver.add_cookie(cookie)
                except Exception as e:
                    master_logger.debug(f"[COOKIES] Skipped cookie {cookie.get('name')}: {e}")

            master_logger.info(f"[COOKIES] ✅ Loaded {len(cookies)} cookies from file")
            return True

        except Exception as e:
            master_logger.error(f"[COOKIES] Error loading cookies: {e}")
            return False

    # Live connection changes by Aniket - VizQL extraction with Trusted Authentication for PRODUCTION
    async def extract_view_data_via_vizql(self, username: str, workbook_name: str, view_name: str,
                                          site_content_url: str = "") -> Optional[pd.DataFrame]:
        """
        Extract data from a Tableau view using VizQL with Trusted Authentication

        PRODUCTION-READY: Fully automated, no manual intervention

        Args:
            username: Tableau username for trusted ticket
            workbook_name: Name of the workbook
            view_name: Name of the view
            site_content_url: Site content URL (e.g., "uMetricAnalytics")

        Returns:
            DataFrame with full underlying data or None if failed
        """
        try:
            master_logger.info(f"[VIZQL] Extracting data for view: {view_name}")

            # STEP 1: Request trusted ticket
            master_logger.info(f"[VIZQL] Step 1: Requesting trusted ticket for user: {username}")
            trusted_ticket = await self._get_trusted_ticket(username, site_content_url)

            if not trusted_ticket:
                master_logger.error("[VIZQL] ❌ Failed to get trusted ticket")
                return None

            master_logger.info(f"[VIZQL] ✅ Got trusted ticket: {trusted_ticket[:20]}...")

            # STEP 2: Redeem ticket for session cookies
            master_logger.info("[VIZQL] Step 2: Redeeming ticket for VizQL session cookies...")
            session = await self._redeem_trusted_ticket(trusted_ticket, site_content_url)

            if not session:
                master_logger.error("[VIZQL] ❌ Failed to redeem trusted ticket")
                return None

            master_logger.info(f"[VIZQL] ✅ Got VizQL session with {len(session.cookies)} cookies")

            # STEP 3: Make VizQL request to get data
            master_logger.info("[VIZQL] Step 3: Requesting data via VizQL...")
            df = await self._vizql_get_data(session, workbook_name, view_name, site_content_url)

            if df is not None and not df.empty:
                master_logger.info(f"[VIZQL] ✅ Successfully extracted data: {df.shape[0]} rows × {df.shape[1]} columns")
                return df
            else:
                master_logger.warning("[VIZQL] ⚠️  No data extracted")
                return None

        except Exception as e:
            master_logger.error(f"[VIZQL] Error extracting data: {type(e).__name__}: {str(e)}")
            master_logger.debug(traceback.format_exc())
            return None

    async def _get_trusted_ticket(self, username: str, site_content_url: str = "") -> Optional[str]:
        """Request a trusted ticket from Tableau Server"""
        try:
            # Build trusted ticket URL
            trusted_url = f"{self.config['tableau_server_url']}/trusted"

            # Prepare request data
            data = {
                'username': username
            }

            if site_content_url:
                data['target_site'] = site_content_url

            master_logger.debug(f"[VIZQL] Requesting trusted ticket from: {trusted_url}")
            master_logger.debug(f"[VIZQL] Request data: {data}")

            # Make request
            response = requests.post(trusted_url, data=data, verify=False, timeout=30)

            if response.status_code == 200:
                ticket = response.text.strip()

                # Check if ticket is valid (not -1 which means auth failed)
                if ticket == "-1":
                    master_logger.error("[VIZQL] Trusted authentication failed - ticket returned -1")
                    master_logger.error("[VIZQL] This means:")
                    master_logger.error("[VIZQL]   1. Trusted authentication is not enabled on Tableau Server")
                    master_logger.error("[VIZQL]   2. Your server IP is not in the trusted hosts list")
                    master_logger.error("[VIZQL]   3. Username is invalid")
                    return None

                return ticket
            else:
                master_logger.error(f"[VIZQL] Failed to get trusted ticket: {response.status_code}")
                master_logger.error(f"[VIZQL] Response: {response.text}")
                return None

        except Exception as e:
            master_logger.error(f"[VIZQL] Error getting trusted ticket: {e}")
            return None

    async def _redeem_trusted_ticket(self, ticket: str, site_content_url: str = "") -> Optional[requests.Session]:
        """Redeem trusted ticket for VizQL session cookies"""
        try:
            # Create session
            session = requests.Session()
            session.verify = False

            # Build redeem URL
            if site_content_url:
                redeem_url = f"{self.config['tableau_server_url']}/t/{site_content_url}/trusted/{ticket}"
            else:
                redeem_url = f"{self.config['tableau_server_url']}/trusted/{ticket}"

            master_logger.debug(f"[VIZQL] Redeeming ticket at: {redeem_url}")

            # Redeem ticket
            response = session.get(redeem_url, timeout=30)

            if response.status_code == 200:
                master_logger.info(f"[VIZQL] Ticket redeemed successfully")
                master_logger.debug(f"[VIZQL] Session cookies: {list(session.cookies.keys())}")
                return session
            else:
                master_logger.error(f"[VIZQL] Failed to redeem ticket: {response.status_code}")
                return None

        except Exception as e:
            master_logger.error(f"[VIZQL] Error redeeming ticket: {e}")
            return None

    async def _vizql_get_data(self, session: requests.Session, workbook_name: str, view_name: str,
                             site_content_url: str = "") -> Optional[pl.DataFrame]:
        """Get data from Tableau view using VizQL (returns Polars DataFrame for large data)"""
        try:
            import urllib.parse
            import re
            import json

            # Build view URL
            encoded_workbook = urllib.parse.quote(workbook_name)
            encoded_view = urllib.parse.quote(view_name)

            if site_content_url:
                view_url = f"{self.config['tableau_server_url']}/t/{site_content_url}/views/{encoded_workbook}/{encoded_view}"
            else:
                view_url = f"{self.config['tableau_server_url']}/views/{encoded_workbook}/{encoded_view}"

            master_logger.debug(f"[VIZQL] View URL: {view_url}")

            # STEP 1: Load the view page to establish VizQL session
            master_logger.info("[VIZQL] Loading view page to establish session...")
            response = session.get(view_url, timeout=60)

            if response.status_code != 200:
                master_logger.error(f"[VIZQL] Failed to load view: {response.status_code}")
                return None

            master_logger.info("[VIZQL] ✅ View page loaded successfully")
            html_content = response.text

            # STEP 2: Extract session information from the page
            master_logger.info("[VIZQL] Extracting session information...")

            # Extract session ID (multiple patterns to try)
            session_id = None
            session_patterns = [
                r'"sessionid":"([^"]+)"',
                r'sessionid["\']?\s*:\s*["\']([^"\']+)["\']',
                r'vizql_session["\']?\s*:\s*["\']([^"\']+)["\']'
            ]

            for pattern in session_patterns:
                match = re.search(pattern, html_content, re.IGNORECASE)
                if match:
                    session_id = match.group(1)
                    master_logger.info(f"[VIZQL] ✅ Found session ID: {session_id[:20]}...")
                    break

            if not session_id:
                master_logger.error("[VIZQL] Failed to extract session ID from page")
                master_logger.debug(f"[VIZQL] HTML preview: {html_content[:500]}")
                return None

            # Extract worksheet name(s)
            worksheet_names = []
            worksheet_patterns = [
                r'"worksheet":"([^"]+)"',
                r'"name":"([^"]+)","worksheetImpl"',
                r'worksheet["\']?\s*:\s*["\']([^"\']+)["\']'
            ]

            for pattern in worksheet_patterns:
                matches = re.findall(pattern, html_content, re.IGNORECASE)
                if matches:
                    worksheet_names.extend(matches)

            # Remove duplicates
            worksheet_names = list(set(worksheet_names))

            if not worksheet_names:
                # Fallback: try to use view name as worksheet name
                worksheet_names = [view_name]
                master_logger.warning(f"[VIZQL] No worksheet names found, using view name: {view_name}")
            else:
                master_logger.info(f"[VIZQL] ✅ Found {len(worksheet_names)} worksheet(s): {worksheet_names}")

            # STEP 3: Try to extract data from each worksheet
            all_dataframes = []

            for worksheet_name in worksheet_names:
                try:
                    master_logger.info(f"[VIZQL] Attempting to extract data from worksheet: {worksheet_name}")

                    # Build VizQL data endpoint
                    if site_content_url:
                        vizql_base = f"{self.config['tableau_server_url']}/t/{site_content_url}/vizql/w/{encoded_workbook}/v/{encoded_view}"
                    else:
                        vizql_base = f"{self.config['tableau_server_url']}/vizql/w/{encoded_workbook}/v/{encoded_view}"

                    # Try the summary data endpoint first (this usually returns underlying data)
                    vizql_data_url = f"{vizql_base}/vudcsv/sessions/{session_id}/views/{urllib.parse.quote(worksheet_name)}"

                    master_logger.debug(f"[VIZQL] Requesting data from: {vizql_data_url}")

                    # Try CSV format first (easiest to parse)
                    params = {
                        'summary': 'true',
                        'underlying': 'true'
                    }

                    data_response = session.get(vizql_data_url, params=params, timeout=60)

                    if data_response.status_code == 200:
                        master_logger.info(f"[VIZQL] ✅ Successfully retrieved data for worksheet: {worksheet_name}")

                        # Try to parse as CSV using Polars for better performance
                        try:
                            from io import StringIO
                            df = pl.read_csv(StringIO(data_response.text))

                            if df.height > 0:  # Polars uses .height instead of .shape[0]
                                master_logger.info(f"[VIZQL] ✅ Parsed CSV data: {df.height} rows × {df.width} columns")
                                all_dataframes.append(df)
                                continue
                        except Exception as csv_error:
                            master_logger.debug(f"[VIZQL] CSV parsing failed: {csv_error}")

                    # If CSV didn't work, try alternative endpoint
                    master_logger.info("[VIZQL] CSV endpoint failed, trying alternative VizQL endpoints...")

                    # Try bootstrapSession/getSummaryData endpoint
                    alt_url = f"{vizql_base}/bootstrapSession/sessions/{session_id}"
                    post_data = {
                        'sheet_id': worksheet_name,
                        'summary': 'true'
                    }

                    alt_response = session.post(alt_url, data=post_data, timeout=60)

                    if alt_response.status_code == 200:
                        # Try to parse JSON response
                        try:
                            json_data = alt_response.json()
                            df = self._parse_vizql_json_to_polars(json_data, worksheet_name)

                            if df is not None and df.height > 0:
                                master_logger.info(f"[VIZQL] ✅ Parsed JSON data: {df.height} rows × {df.width} columns")
                                all_dataframes.append(df)
                                continue
                        except Exception as json_error:
                            master_logger.debug(f"[VIZQL] JSON parsing failed: {json_error}")

                    master_logger.warning(f"[VIZQL] ⚠️  Could not extract data from worksheet: {worksheet_name}")

                except Exception as worksheet_error:
                    master_logger.error(f"[VIZQL] Error processing worksheet {worksheet_name}: {worksheet_error}")
                    master_logger.debug(traceback.format_exc())

            # STEP 4: Combine all dataframes
            if all_dataframes:
                if len(all_dataframes) == 1:
                    final_df = all_dataframes[0]
                else:
                    # Concatenate all dataframes using Polars
                    try:
                        final_df = pl.concat(all_dataframes, how="vertical")
                        master_logger.info(f"[VIZQL] ✅ Combined {len(all_dataframes)} worksheets into single DataFrame")
                    except Exception as concat_error:
                        master_logger.warning(f"[VIZQL] Failed to concatenate dataframes: {concat_error}")
                        final_df = all_dataframes[0]

                master_logger.info(f"[VIZQL] ✅ Final data: {final_df.height} rows × {final_df.width} columns")
                return final_df
            else:
                master_logger.warning("[VIZQL] ⚠️  No data extracted from any worksheet")
                return None

        except Exception as e:
            master_logger.error(f"[VIZQL] Error getting VizQL data: {e}")
            master_logger.debug(traceback.format_exc())
            return None

    def _parse_vizql_json_to_polars(self, json_data: dict, worksheet_name: str) -> Optional[pl.DataFrame]:
        """
        Parse VizQL JSON response and convert to Polars DataFrame

        Args:
            json_data: JSON response from VizQL API
            worksheet_name: Name of the worksheet being parsed

        Returns:
            Polars DataFrame or None if parsing fails
        """
        try:
            master_logger.debug(f"[VIZQL] Parsing JSON data for worksheet: {worksheet_name}")

            # VizQL JSON can have various structures, try common patterns
            data_dict = {}

            # Pattern 1: Check for 'vqlCmdResponse' structure
            if 'vqlCmdResponse' in json_data:
                cmd_response = json_data['vqlCmdResponse']

                # Look for data in layoutStatus.applicationPresModel
                if 'layoutStatus' in cmd_response:
                    layout_status = cmd_response['layoutStatus']

                    if 'applicationPresModel' in layout_status:
                        pres_model = layout_status['applicationPresModel']

                        # Extract data from presentation model
                        if 'dataDictionary' in pres_model:
                            data_dict = pres_model['dataDictionary']
                        elif 'dataSegments' in pres_model:
                            # Alternative structure
                            data_dict = pres_model['dataSegments']

            # Pattern 2: Direct data structure
            elif 'secondaryInfo' in json_data and 'presModelHolder' in json_data['secondaryInfo']:
                pres_model = json_data['secondaryInfo']['presModelHolder']
                if 'dataDictionary' in pres_model:
                    data_dict = pres_model['dataDictionary']

            # Pattern 3: dataValues direct structure
            elif 'dataValues' in json_data:
                data_dict = json_data['dataValues']

            # If we found data dictionary, try to extract columns and values
            if data_dict:
                # Extract column information
                columns = []
                column_data = {}

                # Try to find column names and data
                if 'presModelMap' in data_dict:
                    pres_map = data_dict['presModelMap']

                    # Iterate through presentation model to find columns
                    for key, value in pres_map.items():
                        if isinstance(value, dict):
                            if 'fieldCaption' in value:
                                col_name = value['fieldCaption']
                                columns.append(col_name)

                                # Look for data values
                                if 'valueAlias' in value:
                                    column_data[col_name] = value['valueAlias']
                                elif 'dataValues' in value:
                                    column_data[col_name] = value['dataValues']

                # If we have columns and data, create DataFrame
                if columns and column_data:
                    try:
                        df = pl.DataFrame(column_data)
                        master_logger.info(f"[VIZQL] ✅ Parsed JSON to DataFrame: {df.height} rows × {df.width} columns")
                        return df
                    except Exception as df_error:
                        master_logger.debug(f"[VIZQL] Failed to create DataFrame from column_data: {df_error}")

            # If standard parsing didn't work, try to extract any tabular data
            # Look for arrays that might contain row data
            for key in ['dataTable', 'rows', 'data', 'values']:
                if key in json_data and isinstance(json_data[key], list):
                    try:
                        # Try to convert list to DataFrame
                        if json_data[key]:
                            df = pl.DataFrame(json_data[key])
                            if df.height > 0:
                                master_logger.info(f"[VIZQL] ✅ Parsed JSON array '{key}' to DataFrame: {df.height} rows × {df.width} columns")
                                return df
                    except Exception:
                        pass

            master_logger.warning("[VIZQL] Could not parse VizQL JSON response into DataFrame")
            master_logger.debug(f"[VIZQL] JSON structure keys: {list(json_data.keys())}")
            return None

        except Exception as e:
            master_logger.error(f"[VIZQL] Error parsing VizQL JSON: {e}")
            master_logger.debug(traceback.format_exc())
            return None

    # JavaScript API implementation (kept for reference, not used in production)
    @retry_on_failure(max_retries=2, backoff_factor=0.5)
    def extract_view_data_via_javascript_api(self, workbook_name: str, view_name: str,
                                             site_content_url: str = "") -> Optional[pd.DataFrame]:
        """
        Extract data from a Tableau view using JavaScript API with manual browser login

        FIRST TIME: Opens visible browser window and waits for you to login manually via Uber SSO
        SUBSEQUENT TIMES: Uses saved cookies for automatic authentication (runs headless)

        This approach gets FULL underlying data from live connection dashboards!

        Args:
            workbook_name: Name of the workbook
            view_name: Name of the view
            site_content_url: Site content URL (e.g., "uMetricAnalytics")

        Returns:
            DataFrame with extracted data or None if failed
        """
        try:
            master_logger.info(f"[JS_API] Extracting data for view: {view_name}")

            # Import selenium here to avoid loading if not needed
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from webdriver_manager.chrome import ChromeDriverManager

            # Check if we have saved cookies
            cookie_file = "/Users/aranja14/Desktop/chatbot/tableau_browser_cookies.pkl"
            has_saved_cookies = os.path.exists(cookie_file)

            # Configure Chrome - use headless ONLY if we have saved cookies
            chrome_options = Options()
            if has_saved_cookies:
                master_logger.info("[JS_API] Using saved cookies - running in headless mode")
                chrome_options.add_argument('--headless')
            else:
                master_logger.info("[JS_API] No saved cookies - opening visible browser for manual login")
                master_logger.info("[JS_API] ⚠️  YOU WILL NEED TO LOGIN MANUALLY IN THE BROWSER WINDOW")

            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--window-size=1920,1080')
            chrome_options.add_argument('--disable-blink-features=AutomationControlled')
            chrome_options.add_argument('--ignore-certificate-errors')

            master_logger.debug("[JS_API] Initializing Chrome browser...")

            # Initialize driver with automatic driver management
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)

            try:
                import time as time_module

                # STEP 1: Authenticate browser session
                if has_saved_cookies:
                    master_logger.info("[JS_API] Step 1: Loading saved cookies...")
                    if not self._load_saved_cookies(driver):
                        master_logger.warning("[JS_API] Failed to load saved cookies, requesting manual login...")
                        if not self._manual_browser_login(driver, site_content_url):
                            master_logger.error("[JS_API] Manual login failed")
                            return None
                else:
                    master_logger.info("[JS_API] Step 1: Requesting manual login...")
                    if not self._manual_browser_login(driver, site_content_url):
                        master_logger.error("[JS_API] Manual login failed")
                        return None

                master_logger.info("[JS_API] ✅ Browser authenticated successfully")

                # STEP 2: Navigate to the Tableau view
                master_logger.info("[JS_API] Step 2: Loading Tableau view...")

                # URL-encode workbook and view names for Tableau URL format
                import urllib.parse
                encoded_workbook = urllib.parse.quote(workbook_name)
                encoded_view = urllib.parse.quote(view_name)

                # Build Tableau view URL
                if site_content_url:
                    view_url = f"{self.config['tableau_server_url']}/t/{site_content_url}/views/{encoded_workbook}/{encoded_view}"
                else:
                    view_url = f"{self.config['tableau_server_url']}/views/{encoded_workbook}/{encoded_view}"

                # Add embed parameter for simpler rendering
                view_url += "?:embed=y&:display_count=n&:showVizHome=n"

                master_logger.info(f"[JS_API] Loading Tableau view: {view_url}")

                driver.get(view_url)

                # STEP 3: Wait for Tableau visualization to load
                master_logger.info("[JS_API] Step 3: Waiting for Tableau viz to load...")

                time_module.sleep(3)
                page_title = driver.title
                master_logger.debug(f"[JS_API] Page title: {page_title}")

                # Wait for Tableau API to be available (max 30 seconds)
                WebDriverWait(driver, 30).until(
                    lambda d: d.execute_script("return typeof tableau !== 'undefined'")
                )
                master_logger.debug("[JS_API] Tableau API loaded")

                # Check what Tableau API is available
                tableau_info = driver.execute_script("""
                    if (typeof tableau === 'undefined') return {available: false};
                    return {
                        available: true,
                        hasVizManager: typeof tableau.VizManager !== 'undefined',
                        hasEmbedding: typeof tableau.Embedding !== 'undefined',
                        properties: Object.keys(tableau)
                    };
                """)
                master_logger.debug(f"[JS_API] Tableau API info: {tableau_info}")

                # Wait a bit more for the viz to fully initialize
                time_module.sleep(3)

                # STEP 4: Extract data from the visualization
                master_logger.info("[JS_API] Step 4: Extracting data via JavaScript API...")

                # Check if any vizzes are loaded
                viz_count = driver.execute_script("return tableau.VizManager.getVizs().length;")
                master_logger.debug(f"[JS_API] Number of vizzes found: {viz_count}")

                if viz_count == 0:
                    master_logger.error("[JS_API] ❌ No vizzes found on page - visualization may not have loaded")
                    master_logger.debug(f"[JS_API] Current URL: {driver.current_url}")
                    return None

                # Execute JavaScript to extract data using Tableau's JS API
                data_json = driver.execute_script("""
                    return new Promise((resolve, reject) => {
                        try {
                            // Get the viz object
                            var viz = tableau.VizManager.getVizs()[0];
                            if (!viz) {
                                reject('No viz found - this should not happen');
                                return;
                            }

                            // Get the active sheet
                            var sheet = viz.getWorkbook().getActiveSheet();

                            // If it's a dashboard, get the first worksheet
                            if (sheet.getSheetType() === 'dashboard') {
                                var worksheets = sheet.getWorksheets();
                                if (worksheets.length > 0) {
                                    sheet = worksheets[0];
                                }
                            }

                            // Get underlying data
                            sheet.getUnderlyingDataAsync().then(function(table) {
                                var columns = table.getColumns().map(col => col.getFieldName());
                                var data = table.getData().map(function(row) {
                                    var rowData = {};
                                    for (var i = 0; i < columns.length; i++) {
                                        rowData[columns[i]] = row[i].formattedValue;
                                    }
                                    return rowData;
                                });

                                resolve({
                                    columns: columns,
                                    data: data
                                });
                            }).catch(reject);

                        } catch(e) {
                            reject(e.toString());
                        }
                    });
                """)

                master_logger.info(f"[JS_API] ✅ Data extracted successfully: {len(data_json.get('data', []))} rows")

                # Convert to DataFrame
                if data_json and 'data' in data_json and len(data_json['data']) > 0:
                    df = pd.DataFrame(data_json['data'])
                    master_logger.info(f"[JS_API] Created DataFrame: {df.shape[0]} rows x {df.shape[1]} columns")
                    return df
                else:
                    master_logger.warning("[JS_API] No data extracted from view")
                    return None

            finally:
                # Always close the browser
                driver.quit()
                master_logger.debug("[JS_API] Browser closed")

        except Exception as e:
            master_logger.error(f"[JS_API] Error extracting data: {type(e).__name__}: {str(e)}")
            master_logger.debug(traceback.format_exc())
            return None

    @retry_on_failure(max_retries=3, backoff_factor=1.0)
    def get_workbook_id(self, site_id: str, auth_token: str, workbook_name: str) -> str:
        """Get workbook ID by name with retry logic"""
        encoded_workbook_name = urllib.parse.quote_plus(workbook_name)
        url = f"{self.config['tableau_server_url']}/api/{self.config['api_version']}/sites/{site_id}/workbooks?filter=name:eq:{encoded_workbook_name}"
        headers = {"X-Tableau-Auth": auth_token}
        response = requests.get(url, headers=headers, verify=False)
        
        if response.status_code == 200:
            root = ET.fromstring(response.text)
            namespace = {'ts': 'http://tableau.com/api'}
            workbook_element = root.find(".//ts:workbook", namespace)
            if workbook_element is not None:
                return workbook_element.attrib['id']
            else:
                raise Exception(f"Workbook '{workbook_name}' not found")
        else:
            raise Exception(f"Failed to fetch workbooks: {response.text}")
    
    @retry_on_failure(max_retries=2, backoff_factor=0.5)
    def get_all_workbooks(self, auth_token: str, site_id: str) -> List[str]:
        """Get list of all available workbook names for fuzzy matching with pagination support

        Fix for pagination: Retrieve ALL workbooks, not just first 100
        by Aniket 2/12/2025
        """
        workbook_names = []
        page_size = 100
        page_number = 1
        namespace = {'ts': 'http://tableau.com/api'}

        while True:
            url = f"{self.config['tableau_server_url']}/api/{self.config['api_version']}/sites/{site_id}/workbooks?pageSize={page_size}&pageNumber={page_number}"
            headers = {"X-Tableau-Auth": auth_token}
            response = requests.get(url, headers=headers, verify=False)

            if response.status_code == 200:
                root = ET.fromstring(response.text)

                # Get pagination info from response
                pagination = root.find(".//ts:pagination", namespace)

                # Extract workbooks from this page
                page_workbooks = []
                for workbook_elem in root.findall(".//ts:workbook", namespace):
                    workbook_name = workbook_elem.attrib.get('name')
                    if workbook_name:
                        page_workbooks.append(workbook_name)

                workbook_names.extend(page_workbooks)
                master_logger.debug(f"Retrieved {len(page_workbooks)} workbooks from page {page_number}")

                # Check if there are more pages
                if pagination is not None:
                    total_available = int(pagination.attrib.get('totalAvailable', 0))
                    page_size_attr = int(pagination.attrib.get('pageSize', page_size))

                    # If we've retrieved all workbooks, break
                    if len(workbook_names) >= total_available:
                        master_logger.info(f"Retrieved all {total_available} workbooks across {page_number} page(s)")
                        break
                else:
                    # No pagination element means we got all results
                    break

                # Check if we got fewer workbooks than page size (last page)
                if len(page_workbooks) < page_size:
                    master_logger.info(f"Retrieved total {len(workbook_names)} workbooks across {page_number} page(s)")
                    break

                page_number += 1
            else:
                master_logger.error(f"Failed to get workbooks list: {response.text}")
                raise Exception(f"Failed to fetch workbooks list: {response.text}")

        master_logger.info(f"Retrieved {len(workbook_names)} workbooks for fuzzy matching")
        return workbook_names
    
    @retry_on_failure(max_retries=2, backoff_factor=0.5)
    def get_workbook_views(self, site_id: str, workbook_id: str, auth_token: str) -> List[dict]:
        """Get all views/worksheets in a workbook with retry logic"""
        url = f"{self.config['tableau_server_url']}/api/{self.config['api_version']}/sites/{site_id}/workbooks/{workbook_id}/views"
        headers = {"X-Tableau-Auth": auth_token}
        response = requests.get(url, headers=headers, verify=False)
        
        if response.status_code == 200:
            root = ET.fromstring(response.text)
            namespace = {'ts': 'http://tableau.com/api'}
            
            views_data = []
            for view_elem in root.findall(".//ts:view", namespace):
                view_info = {
                    'id': view_elem.attrib.get('id'),
                    'name': view_elem.attrib.get('name'),
                    'contentUrl': view_elem.attrib.get('contentUrl')
                }
                views_data.append(view_info)
            return views_data
        else:
            raise Exception(f"Failed to get workbook sheets: {response.text}")
    
    def test_workbook_access(self, auth_token: str, site_id: str, workbook_name: str) -> Dict:
        """Test if we have access to the specified workbook and its data"""
        try:
            workbook_id = self.get_workbook_id(site_id, auth_token, workbook_name)
            available_views = self.get_workbook_views(site_id, workbook_id, auth_token)
            
            # Test data access on first available view
            data_access = False
            data_shape = (0, 0)
            
            if available_views:
                test_view = available_views[0]
                try:
                    # Use direct class instantiation instead of relative import
                    processor = TableauDataProcessor()
                    test_data = processor.get_view_data(site_id, test_view['id'], auth_token)
                    data_access = test_data is not None and not test_data.empty
                    data_shape = test_data.shape if test_data is not None else (0, 0)
                except Exception as data_error:
                    print(f"Data access test failed: {data_error}")
            
            return {
                'workbook_accessible': True,
                'workbook_id': workbook_id,
                'views_count': len(available_views),
                'views': available_views,
                'data_accessible': data_access,
                'test_data_shape': data_shape
            }
            
        except Exception as e:
            return {
                'workbook_accessible': False,
                'error': str(e),
                'views_count': 0,
                'views': [],
                'data_accessible': False,
                'test_data_shape': (0, 0)
            }

    # New helper: get workbook name by ID
    @retry_on_failure(max_retries=2, backoff_factor=0.5)
    def get_workbook_name_by_id(self, site_id: str, auth_token: str, workbook_id: str) -> Optional[str]:
        url = f"{self.config['tableau_server_url']}/api/{self.config['api_version']}/sites/{site_id}/workbooks/{workbook_id}"
        headers = {"X-Tableau-Auth": auth_token}
        response = requests.get(url, headers=headers, verify=False)
        if response.status_code == 200:
            root = ET.fromstring(response.text)
            namespace = {'ts': 'http://tableau.com/api'}
            workbook_element = root.find(".//ts:workbook", namespace)
            if workbook_element is not None:
                return workbook_element.attrib.get('name')
            return None
        else:
            raise Exception(f"Failed to fetch workbook name for id {workbook_id}: {response.text}")

    # New helper: find view and parent workbook by contentUrl (e.g., 'Workbook/View')
    @retry_on_failure(max_retries=2, backoff_factor=0.5)
    def find_view_and_workbook_by_content_url(self, site_id: str, auth_token: str, content_url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        # Search views with a filter on contentUrl if supported; otherwise list and scan
        url = f"{self.config['tableau_server_url']}/api/{self.config['api_version']}/sites/{site_id}/views"
        headers = {"X-Tableau-Auth": auth_token}
        response = requests.get(url, headers=headers, verify=False)
        if response.status_code == 200:
            root = ET.fromstring(response.text)
            namespace = {'ts': 'http://tableau.com/api'}
            for view_elem in root.findall(".//ts:view", namespace):
                v_content = view_elem.attrib.get('contentUrl')
                if v_content and v_content.lower() == content_url.lower():
                    view_id = view_elem.attrib.get('id')
                    workbook_elem = view_elem.find("..//ts:workbook", namespace)
                    workbook_id = workbook_elem.attrib.get('id') if workbook_elem is not None else None
                    workbook_name = workbook_elem.attrib.get('name') if workbook_elem is not None else None
                    return view_id, workbook_id, workbook_name
            # Fallback: try endswith match in case of query suffixes
            for view_elem in root.findall(".//ts:view", namespace):
                v_content = view_elem.attrib.get('contentUrl') or ''
                if v_content.lower().endswith(content_url.lower()):
                    view_id = view_elem.attrib.get('id')
                    workbook_elem = view_elem.find("..//ts:workbook", namespace)
                    workbook_id = workbook_elem.attrib.get('id') if workbook_elem is not None else None
                    workbook_name = workbook_elem.attrib.get('name') if workbook_elem is not None else None
                    return view_id, workbook_id, workbook_name
            return None, None, None
        else:
            raise Exception(f"Failed to list views: {response.text}")

class TableauDataProcessor:
    """Handles data retrieval and processing from Tableau views"""
    
    def __init__(self):
        master_logger.info("Initializing TableauDataProcessor")
        self.config = TABLEAU_CONFIG
        self.smart_aggregation_decider = None  # Will be set via set_smart_aggregation()
        master_logger.debug("TableauDataProcessor initialized with config")
    
    def set_smart_aggregation(self, smart_decider):
        """Set smart aggregation decider instance (for compatibility with app.py initialization)"""
        self.smart_aggregation_decider = smart_decider
        master_logger.info("[SMART_AGGREGATION] Smart aggregation decider set for TableauDataProcessor (tableau_backend)")
    
    @function_logger('tableau_backend.TableauDataProcessor.get_view_data')
    def get_view_data(self, site_id: str, view_id: str, auth_token: str) -> Optional[pd.DataFrame]:
        """Get data from a specific view and sort chronologically"""
        master_logger.info(f"=== FETCHING VIEW DATA ===")
        master_logger.info(f"Site ID: {site_id}")
        master_logger.info(f"View ID: {view_id}")
        master_logger.info(f"Auth token length: {len(auth_token) if auth_token else 0}")
        
        url = f"{self.config['tableau_server_url']}/api/{self.config['api_version']}/sites/{site_id}/views/{view_id}/data"
        headers = {"X-Tableau-Auth": auth_token}
        
        master_logger.info(f"Request URL: {url}")
        master_logger.debug(f"Request headers: {headers}")
        
        try:
            master_logger.info("Sending request to get view data")
            start_time = time.time()
            
            response = requests.get(url, headers=headers, verify=False, timeout=300)
            
            request_time = time.time() - start_time
            master_logger.info(f"Request completed in {request_time:.3f} seconds")
            master_logger.info(f"Response status: {response.status_code}")
            master_logger.debug(f"Response headers: {dict(response.headers)}")
            
            if response.status_code == 200:
                master_logger.info("Successfully retrieved view data")
                master_logger.debug(f"Response content length: {len(response.text)}")
                
                try:
                    csv_data = StringIO(response.text)
                    df = pd.read_csv(csv_data)
                    
                    master_logger.info(f"CSV parsed successfully - shape: {df.shape}")
                    master_logger.debug(f"Column names: {list(df.columns)}")
                    
                    if not df.empty:
                        master_logger.debug(f"Data preview (first 3 rows):\n{df.head(3).to_string()}")
                    
                    sorted_df = self.sort_dataframe_chronologically(df)
                    master_logger.info("Data sorted chronologically")
                    
                    return sorted_df
                    
                except Exception as parse_error:
                    master_logger.error(f"Failed to parse CSV data: {parse_error}")
                    master_logger.debug(f"Raw response content preview: {response.text[:500]}...")
                    return None
                    
            else:
                master_logger.error(f"Failed to get view data: HTTP {response.status_code}")
                master_logger.error(f"Response text: {response.text}")
                print(f"Failed to get view data: HTTP {response.status_code}")
                return None
                
        except Exception as e:
            master_logger.error(f"Error fetching view data: {type(e).__name__}: {str(e)}")
            master_logger.error(f"Full traceback: {traceback.format_exc()}")
            print(f"Error fetching view data: {e}")
            return None
    
    def sort_dataframe_chronologically(self, df: pd.DataFrame) -> pd.DataFrame:
        """Sort dataframe by date/month columns chronologically"""
        if df is None or df.empty:
            return df
       
        # Find potential date/month columns
        date_cols = []
        for col in df.columns:
            if any(keyword in col.lower() for keyword in ['date', 'time', 'month', 'year', 'day', 'week', 'create']):
                date_cols.append(col)
       
        if not date_cols:
            return df
       
        df_sorted = df.copy()
        primary_date_col = date_cols[0]
       
        # Check if it contains month names
        if df_sorted[primary_date_col].dtype == 'object':
            sample_values = df_sorted[primary_date_col].dropna().astype(str).tolist()
           
            # If it contains month names, create a sorting order
            if any(month in str(val) for val in sample_values for month in ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']):
                month_order = ['January', 'February', 'March', 'April', 'May', 'June',
                              'July', 'August', 'September', 'October', 'November', 'December']
               
                def month_sort_key(month_str):
                    month_str = str(month_str).strip()
                    for i, month in enumerate(month_order):
                        if month in month_str:
                            return i
                    return 999
               
                df_sorted['_sort_key'] = df_sorted[primary_date_col].apply(month_sort_key)
                df_sorted = df_sorted.sort_values('_sort_key').drop('_sort_key', axis=1).reset_index(drop=True)
       
        return df_sorted

class ChatStateManager:
    """Manages chat states with persistence and cleanup"""
    
    def __init__(self):
        self._states = {}
        self._lock = threading.Lock()
    
    def store_state(self, session_id: str, state: ChatState):
        """Store chat state for a session"""
        with self._lock:
            state.update_activity()
            self._states[session_id] = state
    
    def get_state(self, session_id: str) -> Optional[ChatState]:
        """Get chat state for a session"""
        with self._lock:
            state = self._states.get(session_id)
            if state:
                state.update_activity()
            return state
    
    def has_valid_connection(self, session_id: str) -> bool:
        """Check if session has a valid, fresh connection"""
        state = self.get_state(session_id)
        return state is not None and state.auth_token is not None and state.is_connection_fresh()
    
    def clear_state(self, session_id: str):
        """Clear state for a session"""
        with self._lock:
            self._states.pop(session_id, None)
    
    def cleanup_old_states(self, max_age_hours: int = 2):
        """Cleanup old states to prevent memory leaks"""
        cutoff_time = datetime.utcnow() - timedelta(hours=max_age_hours)
        
        with self._lock:
            expired_sessions = []
            for session_id, state in self._states.items():
                if state.last_activity and state.last_activity < cutoff_time:
                    expired_sessions.append(session_id)
            
            for session_id in expired_sessions:
                del self._states[session_id]
            
            if expired_sessions:
                print(f"Cleaned up {len(expired_sessions)} expired sessions")

# ============================================================================
# URL-TO-WORKBOOK CACHING SYSTEM
# ============================================================================

# Cache files for comprehensive caching
URL_WORKBOOK_CACHE_FILE = "url_workbook_cache.json"
COMPREHENSIVE_CACHE_FILE = "tableau_comprehensive_cache.json"

# Cache expiration times (in seconds)
CACHE_EXPIRATION = {
    'workbook_validation': 600,    # 10 minutes
    'connection_state': 1800,      # 30 minutes  
    'view_metadata': 300,          # 5 minutes
    'url_mapping': 86400          # 24 hours
}

def load_url_workbook_cache() -> Dict[str, str]:
    """
    Load URL to workbook name mappings from cache file
    Returns: Dictionary mapping URLs to workbook names
    """
    cache_file_path = os.path.join(os.getcwd(), URL_WORKBOOK_CACHE_FILE)
    
    try:
        if os.path.exists(cache_file_path):
            with open(cache_file_path, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
                master_logger.info(f"Loaded URL-workbook cache with {len(cache_data)} entries from {cache_file_path}")
                return cache_data
        else:
            master_logger.info(f"No existing cache file found at {cache_file_path}, starting with empty cache")
            return {}
    except Exception as e:
        master_logger.error(f"Failed to load URL-workbook cache: {e}")
        return {}

def save_url_workbook_cache(cache_data: Dict[str, str]) -> None:
    """
    Save URL to workbook name mappings to cache file
    Args:
        cache_data: Dictionary mapping URLs to workbook names
    """
    cache_file_path = os.path.join(os.getcwd(), URL_WORKBOOK_CACHE_FILE)
    
    try:
        master_logger.debug(f"Attempting to save cache to: {cache_file_path}")
        master_logger.debug(f"Cache data to save: {cache_data}")
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(cache_file_path) if os.path.dirname(cache_file_path) else ".", exist_ok=True)
        
        with open(cache_file_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False)
            f.flush()  # Ensure data is written to disk
            os.fsync(f.fileno())  # Force write to disk
            
        master_logger.info(f"✅ Saved URL-workbook cache with {len(cache_data)} entries to {cache_file_path}")
        
        # Verify file was created
        if os.path.exists(cache_file_path):
            file_size = os.path.getsize(cache_file_path)
            master_logger.debug(f"Cache file created successfully, size: {file_size} bytes")
        else:
            master_logger.error(f"Cache file was not created at {cache_file_path}")
            
    except PermissionError as e:
        master_logger.error(f"Permission denied saving cache to {cache_file_path}: {e}")
    except IOError as e:
        master_logger.error(f"I/O error saving cache to {cache_file_path}: {e}")
    except Exception as e:
        master_logger.error(f"Unexpected error saving URL-workbook cache: {e}")
        master_logger.error(f"Cache file path: {cache_file_path}")
        master_logger.error(f"Current working directory: {os.getcwd()}")

def extract_base_url_for_caching(url: str) -> str:
    """
    Extract a normalized base URL for caching purposes
    Removes query parameters and fragments to create a consistent cache key
    """
    try:
        if not url:
            master_logger.warning("Empty URL provided for cache key generation")
            return ""
            
        # Remove query parameters and fragments but keep the main dashboard path
        if '?' in url:
            url = url.split('?')[0]
        
        # For Tableau URLs, keep the structure: protocol://host/path/to/dashboard
        # Example: https://tableau.uberinternal.com/#/site/CODS/views/NOVAScorecardandEvaluation/ConversationFunnel
        master_logger.debug(f"Normalized URL for caching: {url}")
        return url
    except Exception as e:
        master_logger.error(f"Failed to normalize URL for caching: {e}")
        return url if url else ""

def check_cached_workbook_exists(connection_manager, auth_token: str, site_id: str, workbook_name: str) -> bool:
    """
    Check if a cached workbook name still exists on the server
    First checks comprehensive cache, then validates if needed
    Returns: True if workbook exists, False otherwise
    """
    try:
        # First check comprehensive cache
        cached_result = get_cached_workbook_validation(workbook_name)
        if cached_result is not None:
            return cached_result
        
        # Cache miss or expired - validate with server
        master_logger.debug(f"Validating workbook '{workbook_name}' existence on server")
        try:
            workbook_id = connection_manager.get_workbook_id(site_id, auth_token, workbook_name)
            if workbook_id:
                master_logger.info(f"✅ Workbook '{workbook_name}' confirmed to exist on server")
                cache_workbook_validation(workbook_name, True)
                return True
            else:
                master_logger.warning(f"❌ Workbook '{workbook_name}' no longer exists on server")
                cache_workbook_validation(workbook_name, False)
                return False
        except Exception as e:
            master_logger.warning(f"❌ Workbook '{workbook_name}' verification failed: {e}")
            cache_workbook_validation(workbook_name, False)
            return False
            
    except Exception as e:
        master_logger.error(f"Error checking workbook existence: {e}")
        return False

def load_comprehensive_cache() -> Dict:
    """
    Load comprehensive cache including validation results, connection state, and metadata
    Returns: Dictionary with cached data and timestamps
    """
    cache_file_path = os.path.join(os.getcwd(), COMPREHENSIVE_CACHE_FILE)
    
    try:
        if os.path.exists(cache_file_path):
            with open(cache_file_path, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
                master_logger.info(f"Loaded comprehensive cache from {cache_file_path}")
                return cache_data
        else:
            master_logger.info(f"No comprehensive cache file found, starting fresh")
            return {
                'workbook_validations': {},
                'connection_states': {},
                'view_metadata': {},
                'url_mappings': {}
            }
    except Exception as e:
        master_logger.error(f"Failed to load comprehensive cache: {e}")
        return {
            'workbook_validations': {},
            'connection_states': {},
            'view_metadata': {},
            'url_mappings': {}
        }

def save_comprehensive_cache(cache_data: Dict) -> None:
    """
    Save comprehensive cache to file
    Args:
        cache_data: Dictionary with all cached data
    """
    cache_file_path = os.path.join(os.getcwd(), COMPREHENSIVE_CACHE_FILE)
    
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(cache_file_path) if os.path.dirname(cache_file_path) else ".", exist_ok=True)
        
        with open(cache_file_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False, default=str)
            f.flush()
            os.fsync(f.fileno())
            
        master_logger.info(f"✅ Saved comprehensive cache to {cache_file_path}")
        
    except Exception as e:
        master_logger.error(f"Failed to save comprehensive cache: {e}")

def is_cache_expired(timestamp: str, cache_type: str) -> bool:
    """
    Check if a cache entry has expired
    Args:
        timestamp: ISO timestamp string
        cache_type: Type of cache to check expiration for
    Returns: True if expired, False if still valid
    """
    try:
        from datetime import datetime, timezone
        cache_time = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        current_time = datetime.now(timezone.utc)
        age_seconds = (current_time - cache_time).total_seconds()
        
        expiration_limit = CACHE_EXPIRATION.get(cache_type, 300)  # Default 5 minutes
        expired = age_seconds > expiration_limit
        
        if expired:
            master_logger.debug(f"Cache entry expired: {age_seconds:.1f}s > {expiration_limit}s")
        else:
            master_logger.debug(f"Cache entry still valid: {age_seconds:.1f}s < {expiration_limit}s")
            
        return expired
    except Exception as e:
        master_logger.error(f"Error checking cache expiration: {e}")
        return True  # If we can't parse, assume expired

def get_cached_workbook_validation(workbook_name: str) -> Optional[bool]:
    """
    Get cached workbook validation result if not expired
    Returns: True/False if cached and valid, None if not cached or expired
    """
    try:
        cache = load_comprehensive_cache()
        validations = cache.get('workbook_validations', {})
        
        if workbook_name in validations:
            entry = validations[workbook_name]
            if not is_cache_expired(entry['timestamp'], 'workbook_validation'):
                master_logger.info(f"📋 Using cached workbook validation: {workbook_name} = {entry['exists']}")
                return entry['exists']
            else:
                master_logger.debug(f"Cached workbook validation expired for: {workbook_name}")
        
        return None
    except Exception as e:
        master_logger.error(f"Error retrieving cached workbook validation: {e}")
        return None

def cache_workbook_validation(workbook_name: str, exists: bool) -> None:
    """
    Cache workbook validation result with timestamp
    """
    try:
        from datetime import datetime, timezone
        cache = load_comprehensive_cache()
        cache['workbook_validations'][workbook_name] = {
            'exists': exists,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        save_comprehensive_cache(cache)
        master_logger.info(f"✅ Cached workbook validation: {workbook_name} = {exists}")
    except Exception as e:
        master_logger.error(f"Error caching workbook validation: {e}")

def get_cached_connection_state(workbook_name: str) -> Optional[Dict]:
    """
    Get cached connection state if not expired
    Returns: Connection state dict if cached and valid, None otherwise
    """
    try:
        cache = load_comprehensive_cache()
        connections = cache.get('connection_states', {})
        
        if workbook_name in connections:
            entry = connections[workbook_name]
            if not is_cache_expired(entry['timestamp'], 'connection_state'):
                master_logger.info(f"🚀 Using cached connection state for: {workbook_name}")
                return entry['state']
            else:
                master_logger.debug(f"Cached connection state expired for: {workbook_name}")
        
        return None
    except Exception as e:
        master_logger.error(f"Error retrieving cached connection state: {e}")
        return None

def cache_connection_state(workbook_name: str, connection_state: Dict) -> None:
    """
    Cache connection state with timestamp
    """
    try:
        from datetime import datetime, timezone
        cache = load_comprehensive_cache()
        cache['connection_states'][workbook_name] = {
            'state': connection_state,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        save_comprehensive_cache(cache)
        master_logger.info(f"✅ Cached connection state for: {workbook_name}")
    except Exception as e:
        master_logger.error(f"Error caching connection state: {e}")

# ============================================================================
# WORKBOOK DETECTION AND FUZZY MATCHING
# ============================================================================

def normalize_workbook_name(name: str) -> str:
    """Normalize workbook name for fuzzy matching"""
    if not name:
        return ""
    
    # Remove common URL encoding and special characters
    normalized = name.replace("%20", " ").replace("_", " ").replace("-", " ")
    # Remove extra spaces and convert to lowercase
    normalized = " ".join(normalized.split()).lower()
    return normalized

def fuzzy_match_workbook_name(url_name: str, available_workbooks: list, threshold: float = 0.6) -> str:
    """
    Use fuzzy matching to find the best workbook name match
    Returns the best matching workbook name or None if no good match found
    """
    if not url_name or not available_workbooks:
        return None
    
    normalized_url = normalize_workbook_name(url_name)
    # Also create a version without spaces for comparison
    normalized_url_no_spaces = normalized_url.replace(" ", "")
    
    best_match = None
    best_score = 0
    
    master_logger.info(f"Fuzzy matching '{url_name}' (normalized: '{normalized_url}', no spaces: '{normalized_url_no_spaces}') against {len(available_workbooks)} workbooks")
    
    for workbook in available_workbooks:
        normalized_workbook = normalize_workbook_name(workbook)
        normalized_workbook_no_spaces = normalized_workbook.replace(" ", "")
        
        score = 0
        
        # Check for exact match without spaces (highest priority)
        if normalized_url_no_spaces == normalized_workbook_no_spaces:
            score = 1.0
        # Check for substring match without spaces
        elif normalized_url_no_spaces in normalized_workbook_no_spaces or normalized_workbook_no_spaces in normalized_url_no_spaces:
            score = 0.95
        # Check for substring match with spaces
        elif normalized_url in normalized_workbook or normalized_workbook in normalized_url:
            score = 0.9
        else:
            # Calculate word-based similarity for cases where words are separate
            url_words = set(normalized_url.split())
            workbook_words = set(normalized_workbook.split())
            
            if url_words and workbook_words:
                intersection = len(url_words & workbook_words)
                union = len(url_words | workbook_words)
                score = intersection / union if union > 0 else 0
                
                # Special case: if URL is one long word, try breaking it down
                if len(url_words) == 1 and len(workbook_words) > 1:
                    # Check if the single URL word contains all workbook words as substrings
                    url_word = list(url_words)[0]
                    matches = 0
                    for wb_word in workbook_words:
                        if wb_word in url_word.lower():
                            matches += 1
                    if matches == len(workbook_words):
                        score = 0.85  # High score for containing all words
                    elif matches >= len(workbook_words) * 0.7:  # 70% of words match
                        score = max(score, 0.7)
        
        master_logger.debug(f"Workbook '{workbook}' (normalized: '{normalized_workbook}', no spaces: '{normalized_workbook_no_spaces}') - similarity score: {score:.3f}")
        
        if score > best_score and score >= threshold:
            best_match = workbook
            best_score = score
    
    master_logger.info(f"Best match: '{best_match}' with score {best_score:.3f}")
    return best_match

def get_workbook_from_dashboard_context(dashboard_context: Dict) -> Tuple[str, str]:
    """
    Enhanced workbook detection from dashboard context without fallback
    Returns: (workbook_name, dashboard_name)
    """
    dashboard_name = dashboard_context.get("dashboard_name", "")
    workbook_name = dashboard_context.get("workbook_name", "")
    
    # If we have a workbook name, return it directly (no fallback)
    if workbook_name:
        return workbook_name, dashboard_name
    
    # Try to extract from dashboard name if it looks like a workbook reference
    if dashboard_name:
        # Handle common patterns like "Dashboard Name" -> "Dashboard Name"
        if " Dashboard" in dashboard_name:
            potential_workbook = dashboard_name.replace(" Dashboard", "").replace(" dashboard", "")
            return potential_workbook, dashboard_name
        
        # Try direct dashboard name as workbook
        return dashboard_name, dashboard_name
    
    # No fallback - return None to indicate failure
    return None, dashboard_name

def try_fuzzy_match_workbook(connection_manager, auth_token, site_id, url_workbook_name: str, source_url: str = None) -> Tuple[str, Dict]:
    """
    Try to find workbook using cached mappings first, then fuzzy matching against available workbooks
    Args:
        connection_manager: Tableau connection manager
        auth_token: Authentication token
        site_id: Site ID
        url_workbook_name: Workbook name extracted from URL
        source_url: Original source URL for caching
    """
    master_logger.info(f"Attempting workbook resolution for: '{url_workbook_name}'")
    master_logger.debug(f"Source URL for caching: '{source_url}'")
    
    # Load URL-workbook cache
    cache_data = load_url_workbook_cache()
    cache_key = None
    
    # Extract cache key if we have a source URL
    if source_url:
        cache_key = extract_base_url_for_caching(source_url)
        master_logger.debug(f"Generated cache key: '{cache_key}'")
        if cache_key in cache_data:
            cached_workbook = cache_data[cache_key]
            master_logger.info(f"📋 Found cached mapping: URL '{cache_key}' -> Workbook '{cached_workbook}'")
            
            # Check for cached connection state first
            cached_connection = get_cached_connection_state(cached_workbook)
            if cached_connection is not None:
                master_logger.info(f"🚀 Using cached connection state for: {cached_workbook}")
                return cached_workbook, cached_connection
            
            # No cached connection - verify workbook exists and test access
            if check_cached_workbook_exists(connection_manager, auth_token, site_id, cached_workbook):
                try:
                    master_logger.debug(f"Testing workbook access for cached workbook: {cached_workbook}")
                    access_test = connection_manager.test_workbook_access(auth_token, site_id, cached_workbook)
                    if access_test['workbook_accessible']:
                        master_logger.info(f"✅ Successfully used cached workbook: {cached_workbook}")
                        # Cache the successful connection state
                        cache_connection_state(cached_workbook, access_test)
                        return cached_workbook, access_test
                    else:
                        master_logger.warning(f"❌ Cached workbook '{cached_workbook}' not accessible, removing from cache")
                        del cache_data[cache_key]
                        save_url_workbook_cache(cache_data)
                except Exception as e:
                    master_logger.error(f"Error accessing cached workbook '{cached_workbook}': {e}")
                    del cache_data[cache_key]
                    save_url_workbook_cache(cache_data)
            else:
                # Remove invalid cache entry
                master_logger.info(f"🗑️ Removing invalid cache entry for URL: {cache_key}")
                del cache_data[cache_key]
                save_url_workbook_cache(cache_data)
    
    # Cache miss or invalid cache entry - proceed with fuzzy matching
    master_logger.info(f"🔍 Cache miss - proceeding with fuzzy matching for: '{url_workbook_name}'")
    
    try:
        # Get list of all available workbooks
        available_workbooks = connection_manager.get_all_workbooks(auth_token, site_id)
        master_logger.info(f"Retrieved {len(available_workbooks)} available workbooks for fuzzy matching")
        
        # Try fuzzy matching
        matched_workbook = fuzzy_match_workbook_name(url_workbook_name, available_workbooks)
        
        if matched_workbook:
            master_logger.info(f"🎯 Fuzzy match found: '{matched_workbook}' for '{url_workbook_name}'")
            access_test = connection_manager.test_workbook_access(auth_token, site_id, matched_workbook)
            if access_test['workbook_accessible']:
                master_logger.info(f"✅ Successfully connected to fuzzy-matched workbook: {matched_workbook}")
                
                # Cache the successful connection state
                cache_connection_state(matched_workbook, access_test)
                
                # Cache the successful URL mapping
                if source_url:
                    if not cache_key:
                        # Regenerate cache_key if it wasn't set earlier
                        cache_key = extract_base_url_for_caching(source_url)
                        master_logger.debug(f"Regenerated cache key for caching: '{cache_key}'")
                    
                    if cache_key:
                        cache_data[cache_key] = matched_workbook
                        save_url_workbook_cache(cache_data)
                        master_logger.info(f"💾 Cached new mapping: URL '{cache_key}' -> Workbook '{matched_workbook}'")
                    else:
                        master_logger.warning(f"⚠️ Could not generate cache key from URL: '{source_url}'")
                else:
                    master_logger.debug("No source URL provided, skipping cache save")
                
                return matched_workbook, access_test
            else:
                master_logger.warning(f"❌ Fuzzy-matched workbook '{matched_workbook}' not accessible")
        else:
            master_logger.info(f"❌ No suitable fuzzy match found for '{url_workbook_name}'")
    
    except Exception as e:
        master_logger.error(f"Error during fuzzy matching: {str(e)}")
    
    raise Exception(f"Could not find matching workbook for '{url_workbook_name}'")

@function_logger('tableau_backend.initialize_tableau_connection')
def initialize_tableau_connection(dashboard_context: Optional[Dict] = None) -> Tuple[bool, Any]:
    """
    Initialize connection to Tableau with enhanced error handling and state management
    Returns: (success: bool, state_or_error: ChatState or str)
    """
    master_logger.info("=== INITIALIZING TABLEAU CONNECTION ===")
    master_logger.debug(f"Dashboard context: {dashboard_context}")
    
    try:
        master_logger.info("Using global TableauConnectionManager")
        try:
            from app import connection_manager
        except ImportError:
            # CLI mode - connection_manager should be in globals()
            connection_manager = globals().get('connection_manager')
            if not connection_manager:
                raise RuntimeError("connection_manager not found. In CLI mode, ensure it's initialized before calling this function.")

        # Extract context details
        master_logger.info("Extracting workbook context")
        if not dashboard_context:
            master_logger.warning("No dashboard context provided")
            return False, "No dashboard context provided"

        extracted_workbook_name, dashboard_name = get_workbook_from_dashboard_context(dashboard_context)
        extracted_workbook_id = dashboard_context.get("workbook_id") or dashboard_context.get("workbookId")
        view_content_url = dashboard_context.get("view_content_url") or dashboard_context.get("viewContentUrl")

        master_logger.info(f"Context - workbook_id: '{extracted_workbook_id}', workbook_name: '{extracted_workbook_name}', view_path: '{view_content_url}', dashboard: '{dashboard_name}'")

        # Extract source URL for dynamic credential loading
        source_url = dashboard_context.get("url") if dashboard_context else None
        if source_url:
            master_logger.debug(f"Extracted source URL from dashboard context: '{source_url}'")

        # Load credentials dynamically based on URL if provided
        # This allows different sites to use different credentials from Google Sheets
        # NEW: Support for multiple credentials with fallback
        config_to_use = TABLEAU_CONFIG
        all_credentials = None
        auth_token = None
        site_id = None

        if source_url:
            master_logger.info("🔄 Loading site-specific credentials from Google Sheets based on URL...")
            try:
                # Get ALL credentials for this site (for fallback support)
                all_credentials = load_tableau_config(tableau_url=source_url, get_all_fallbacks=True)
                master_logger.info(f"✅ Loaded {len(all_credentials)} credential(s) for site")

                # Try each credential until one succeeds
                for credential_idx, credential in enumerate(all_credentials, start=1):
                    master_logger.info(f"🔑 Trying credential #{credential_idx}/{len(all_credentials)} - auth_type: {credential.get('auth_type')}")

                    try:
                        # Update connection_manager config with this credential
                        connection_manager.config = credential
                        content_url = credential.get("site_content_url", "")

                        # Try to authenticate with this credential
                        # Clear cache to force fresh authentication attempt
                        cache_key = f"{content_url}_auth"
                        if hasattr(connection_manager, '_auth_cache') and cache_key in connection_manager._auth_cache:
                            del connection_manager._auth_cache[cache_key]
                            master_logger.debug(f"Cleared auth cache for fresh attempt with credential #{credential_idx}")

                        # Attempt authentication
                        auth_token, site_id = connection_manager.authenticate(content_url)

                        # If we get here, authentication succeeded!
                        master_logger.info(f"✅ SUCCESS! Credential #{credential_idx} worked - auth_type: {credential.get('auth_type')}")
                        config_to_use = credential
                        break  # Exit loop on success

                    except Exception as auth_error:
                        master_logger.warning(f"❌ Credential #{credential_idx} failed: {auth_error}")
                        if credential_idx < len(all_credentials):
                            master_logger.info(f"⏩ Trying next credential...")
                        else:
                            master_logger.error(f"❌ All {len(all_credentials)} credentials failed!")
                            raise Exception(f"All {len(all_credentials)} credentials failed. Last error: {auth_error}")

            except Exception as e:
                master_logger.warning(f"⚠️ Failed to load site-specific credentials with fallback, using default config: {e}")
                config_to_use = TABLEAU_CONFIG
                all_credentials = None

        # If no URL provided or fallback failed, use default authentication
        if not auth_token:
            master_logger.info("Using default authentication flow...")

            # Update connection_manager config if we have a valid config
            if config_to_use:
                connection_manager.config = config_to_use

            # Authenticate using multi-layer caching
            content_url = config_to_use.get("site_content_url", "")

            # Get authentication with smart caching (pickle cache will be used if available)
            auth_token, site_id = connection_manager.get_or_create_auth_from_storage(
                content_url=content_url,
                chat_state=None  # Will be created later with full context
            )

        # Resolution strategy: ID -> view path -> exact name -> fuzzy last
        current_workbook = None
        access_test = None

        # 1) Resolve by workbook ID (exact)
        if extracted_workbook_id:
            master_logger.info("Attempting exact resolution by workbook_id")
            try:
                available_views = connection_manager.get_workbook_views(site_id, extracted_workbook_id, auth_token)
                access_test = {
                    'workbook_accessible': True,
                    'workbook_id': extracted_workbook_id,
                    'views': available_views,
                    'views_count': len(available_views),
                    'data_accessible': False,
                    'test_data_shape': (0, 0)
                }
                try:
                    current_workbook = connection_manager.get_workbook_name_by_id(site_id, auth_token, extracted_workbook_id)
                except Exception:
                    current_workbook = extracted_workbook_name or ""
            except Exception as e:
                master_logger.warning(f"Workbook ID resolution failed: {e}")

        # 2) Resolve by view contentUrl (exact)
        if access_test is None and view_content_url:
            master_logger.info("Attempting exact resolution by view contentUrl")
            try:
                vid, wbid, wbname = connection_manager.find_view_and_workbook_by_content_url(site_id, auth_token, view_content_url)
                if wbid:
                    available_views = connection_manager.get_workbook_views(site_id, wbid, auth_token)
                    access_test = {
                        'workbook_accessible': True,
                        'workbook_id': wbid,
                        'views': available_views,
                        'views_count': len(available_views),
                        'data_accessible': False,
                        'test_data_shape': (0, 0)
                    }
                    current_workbook = wbname or extracted_workbook_name or ""
            except Exception as e:
                master_logger.warning(f"View path resolution failed: {e}")

        # 3) Exact by name
        if access_test is None and extracted_workbook_name:
            master_logger.info("Attempting exact resolution by workbook name")
            try:
                access_test = connection_manager.test_workbook_access(auth_token, site_id, extracted_workbook_name)
                if access_test.get('workbook_accessible'):
                    current_workbook = extracted_workbook_name
                else:
                    access_test = None
            except Exception as e:
                master_logger.warning(f"Exact name resolution failed: {e}")

        # 4) Fuzzy last resort
        if access_test is None and extracted_workbook_name:
            master_logger.warning("Attempting fuzzy match as last resort")
            try:
                current_workbook, access_test = try_fuzzy_match_workbook(connection_manager, auth_token, site_id, extracted_workbook_name, source_url)
            except Exception as fuzzy_error:
                master_logger.error(f"Fuzzy matching failed: {str(fuzzy_error)}")
                return False, f"Cannot find workbook matching '{extracted_workbook_name}': {str(fuzzy_error)}"

        if access_test is None or not access_test.get('workbook_accessible'):
            return False, "Could not resolve a valid workbook via id, view path, or name"

        # Extract resolved details
        workbook_id = access_test['workbook_id']
        available_views = access_test['views']
        if not current_workbook:
            current_workbook = extracted_workbook_name or ""
        
        # Look for raw_data view and fetch it
        raw_data = None
        raw_data_view = next((v for v in available_views if v['name'].lower() == 'raw_data'), None)
        
        if raw_data_view and access_test['data_accessible']:
            try:
                data_processor = TableauDataProcessor()
                df_raw = data_processor.get_view_data(site_id, raw_data_view['id'], auth_token)
                raw_data = df_raw
                print(f"Raw data loaded: {raw_data.shape if raw_data is not None else 'None'}")
            except Exception as raw_data_error:
                print(f"Could not load raw_data view: {raw_data_error}")
        
        # Create state object
        state = ChatState(
            auth_token=auth_token,
            site_id=site_id,
            workbook_id=workbook_id,
            workbook_name=current_workbook,
            dashboard_name=dashboard_name,
            available_views=available_views,
            raw_data=raw_data
        )
        
        print(f"Successfully initialized connection to '{current_workbook}' with {len(available_views)} views")
        return True, state
        
    except Exception as e:
        error_msg = f"Failed to initialize Tableau connection: {str(e)}"
        print(error_msg)
        return False, error_msg

def detect_specific_anomalies(df: pd.DataFrame, analysis_type: str = "Spike", threshold: float = 0.1) -> List[Dict]:
    """
    Detect specific anomalies (spikes or dips) and return detailed list with time context
    """
    if df is None or df.empty:
        return []
   
    # Try to find numeric columns for analysis
    numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
   
    # Force convert columns to numeric if they contain numbers
    for col in df.columns:
        if col not in numeric_cols and 'month' not in col.lower():
            try:
                cleaned_series = df[col].astype(str)
                cleaned_series = cleaned_series.str.replace(',', '', regex=False)
                cleaned_series = cleaned_series.str.replace(' ', '', regex=False)
                cleaned_series = cleaned_series.str.strip()
               
                numeric_series = pd.to_numeric(cleaned_series, errors='coerce')
                non_null_count = numeric_series.notna().sum()
               
                if non_null_count > 0:
                    df[col] = numeric_series
                    numeric_cols.append(col)
                   
            except Exception:
                continue
   
    if not numeric_cols:
        return []
   
    # Try to find date/time columns for context
    date_cols = []
    for col in df.columns:
        if any(keyword in col.lower() for keyword in ['date', 'time', 'month', 'year', 'day', 'week']):
            date_cols.append(col)
    
    time_context_col = date_cols[0] if date_cols else (df.select_dtypes(include=['object']).columns.tolist()[0] if len(df.select_dtypes(include=['object']).columns) > 0 else None)
   
    anomalies = []
   
    # Analyze each numeric column
    for value_col in numeric_cols:
        if len(df) > 1:
            df_temp = df.copy()
            df_temp['pct_change'] = df_temp[value_col].pct_change()
           
            if analysis_type == "Spike":
                events_data = df_temp[df_temp['pct_change'] > threshold]
            else:  # Dip
                events_data = df_temp[df_temp['pct_change'] < -threshold]
               
            for idx, row in events_data.iterrows():
                time_context = str(row[time_context_col]) if time_context_col else f"Row {idx}"
                change_direction = "increase" if analysis_type == "Spike" else "decrease"
                description = f"{time_context}: {row['pct_change']:.2%} {change_direction} in {value_col} (Value: {row[value_col]})"
               
                anomaly_info = {
                    'type': analysis_type,
                    'column': value_col,
                    'index': idx,
                    'value': row[value_col],
                    'change': row['pct_change'],
                    'time_context': time_context,
                    'description': description,
                    'row_data': row.to_dict()
                }
                anomalies.append(anomaly_info)
   
    return anomalies

def run_lang_graph_analysis():
    """
    Placeholder for advanced AI analysis
    In production, this would integrate with your LangGraph/AI analysis pipeline
    """
    # This is a simplified placeholder - integrate with your actual AI analysis
    return {
        "executive_summary": "Analysis completed using simplified logic. Integrate with your AI pipeline for advanced insights.",
        "insight": "Detected events have been identified and are ready for detailed analysis.",
        "qna_answer": "AI analysis placeholder - integrate with your LLM pipeline",
        "validation": "PASS - Analysis framework ready",
        "delta_df": pd.DataFrame()
    }


# ============================================================================
# ENHANCED INITIALIZATION WITH METADATA
# ============================================================================

@function_logger('tableau_backend.initialize_tableau_connection_with_metadata')
async def initialize_tableau_connection_with_metadata(dashboard_context: Optional[Dict] = None) -> Tuple[bool, Any]:
    """
    Enhanced initialization that includes TWB metadata extraction
    Use this instead of initialize_tableau_connection() for complete functionality
    """
    master_logger.info("=== INITIALIZING TABLEAU CONNECTION WITH METADATA ===")
    
    try:
        # Step 1: Initialize basic connection
        success, result = initialize_tableau_connection(dashboard_context)
        
        if not success:
            return False, result
        
        # Step 2: Fetch complete metadata
        complete_manager = CompleteWorkbookDataManager(TableauConnectionManager())
        
        complete_data = await complete_manager.fetch_complete_workbook_data(
            workbook_name=result['workbook_name'],
            workbook_id=result['workbook_id'],
            site_id=result['site_id'],
            auth_token=result['auth_token']
        )
        
        # Step 3: Merge metadata into result
        result['twb_metadata'] = complete_data.get('metadata', {})
        result['charts_metadata'] = complete_data.get('charts_metadata', {})
        result['charts_metadata_readable'] = complete_data.get('charts_metadata_readable', {})
        result['datasources_data'] = complete_data.get('datasources_data', {})
        result['has_complete_metadata'] = complete_data.get('success', False)
        
        # Include CSV export info if available
        if 'csv_export' in complete_data:
            result['csv_export'] = complete_data['csv_export']
        
        master_logger.info("Tableau connection with metadata initialized successfully")
        return True, result
        
    except Exception as e:
        master_logger.error(f"Error initializing with metadata: {e}")
        # Fall back to basic connection
        return initialize_tableau_connection(dashboard_context)


# ============================================================================
# AUTO-EXPORT CONFIGURATION HELPER
# ============================================================================

def configure_auto_export(enabled=True, export_csv=True, export_metadata=True):
    """
    Helper function to configure auto-export settings at runtime
    
    Args:
        enabled (bool): Enable/disable auto-export functionality
        export_csv (bool): Enable/disable CSV export
        export_metadata (bool): Enable/disable metadata export
    """
    # Import app module to modify globals
    import app
    
    app.AUTO_EXPORT_ENABLED = enabled
    app.AUTO_EXPORT_CSV = export_csv  
    app.AUTO_EXPORT_METADATA = export_metadata
    
    master_logger.info(f"Auto-export configuration updated:")
    master_logger.info(f"  - Enabled: {enabled}")
    master_logger.info(f"  - CSV Export: {export_csv}")
    master_logger.info(f"  - Metadata Export: {export_metadata}")

def get_auto_export_status():
    """Get current auto-export configuration status"""
    try:
        import app
        return {
            'enabled': app.AUTO_EXPORT_ENABLED,
            'csv_export': app.AUTO_EXPORT_CSV,
            'metadata_export': app.AUTO_EXPORT_METADATA
        }
    except ImportError:
        return {
            'enabled': False,
            'csv_export': False, 
            'metadata_export': False,
            'error': 'App module not available'
        }

def cleanup_old_metadata_files(days_old: int = 30):
    """
    Clean up old metadata files and folders
    
    Args:
        days_old: Remove metadata folders older than this many days
        
    Returns:
        Dictionary with cleanup statistics
    """
    cleanup_stats = {
        'folders_removed': 0,
        'files_removed': 0,
        'errors': []
    }
    
    try:
        metadata_base_dir = "tableau_metadata"
        if not os.path.exists(metadata_base_dir):
            master_logger.info("No tableau_metadata directory found - nothing to clean up")
            return cleanup_stats
            
        cutoff_time = datetime.now() - timedelta(days=days_old)
        
        for folder_name in os.listdir(metadata_base_dir):
            folder_path = os.path.join(metadata_base_dir, folder_name)
            
            if os.path.isdir(folder_path):
                try:
                    # Get folder creation time
                    folder_time = datetime.fromtimestamp(os.path.getctime(folder_path))
                    
                    if folder_time < cutoff_time:
                        # Remove old folder
                        import shutil
                        file_count = sum([len(files) for r, d, files in os.walk(folder_path)])
                        shutil.rmtree(folder_path)
                        
                        cleanup_stats['folders_removed'] += 1
                        cleanup_stats['files_removed'] += file_count
                        master_logger.info(f"Removed old metadata folder: {folder_path} ({file_count} files)")
                        
                except Exception as e:
                    error_msg = f"Error removing folder {folder_path}: {e}"
                    cleanup_stats['errors'].append(error_msg)
                    master_logger.error(error_msg)
        
        master_logger.info(f"Metadata cleanup completed: {cleanup_stats['folders_removed']} folders, {cleanup_stats['files_removed']} files removed")
        
    except Exception as e:
        error_msg = f"Error during metadata cleanup: {e}"
        cleanup_stats['errors'].append(error_msg)
        master_logger.error(error_msg)
        
    return cleanup_stats

# ============================================================================
# MODULE COMPLETION LOG
# ============================================================================

master_logger.info("="*80)
master_logger.info("TABLEAU BACKEND MODULE FULLY LOADED")
master_logger.info("Features: REST API, TWB Parsing, Datasource Extraction, Complete Metadata, CSV Export")
master_logger.info("Enhanced with parsing capabilities from tableau_backend.py")
master_logger.info("="*80)
master_logger.info("Available classes:")
master_logger.info("  ✅ TWBParser - Complete TWB/TWBX metadata extraction")
master_logger.info("  ✅ DatasourceExtractor - .hyper file processing")
master_logger.info("  ✅ WorkbookDataExporter - CSV export functionality")
master_logger.info("  ✅ CompleteWorkbookDataManager - Full workbook processing")
master_logger.info("  ✅ Enhanced initialization with metadata support")
master_logger.info("="*80)

# Additional utility functions can be added here as needed
# ============================================================================
# COMMAND LINE INTERFACE - ADDED FOR STANDALONE USE
# ============================================================================

if __name__ == "__main__":
    import sys
    import asyncio
    
    print("="*80)
    print("TABLEAU DASHBOARD DOWNLOADER - Command Line Interface")
    print("="*80)

    # HARDCODED URL - change this to test different dashboards
    HARDCODED_URL = "https://tableau.uberinternal.com/#/site/uMetricAnalytics/views/MTDMobilityMarketplaceDashboard/MTDMobilityMarketplaceDimensionComparison?:iid=1"

    # Use hardcoded URL if no argument provided
    if len(sys.argv) < 2:
        print(f"\n⚠️  No URL provided, using hardcoded URL")
        dashboard_url = HARDCODED_URL
    else:
        dashboard_url = sys.argv[1]

    output_dir = sys.argv[2] if len(sys.argv) > 2 else "tableau_exports"
    
    print(f"\nDashboard URL: {dashboard_url}")
    print(f"Output directory: {output_dir}\n")

    # Initialize connection manager globally
    global connection_manager
    connection_manager = TableauConnectionManager()

    # Make it available in globals for initialize_tableau_connection
    globals()['connection_manager'] = connection_manager

    # Extract workbook name from URL
    # URL format: https://tableau.uberinternal.com/#/site/CODS/views/WORKBOOK/VIEW
    workbook_name = None
    view_name = None

    if '/views/' in dashboard_url:
        try:
            views_part = dashboard_url.split('/views/')[1]
            views_part = views_part.split('?')[0].split('#')[0]  # Remove query params
            parts = views_part.split('/')
            if len(parts) >= 2:
                workbook_name = urllib.parse.unquote(parts[0])
                view_name = urllib.parse.unquote(parts[1])
                print(f"✓ Extracted from URL:")
                print(f"  Workbook: {workbook_name}")
                print(f"  View: {view_name}\n")
        except Exception as e:
            print(f"⚠️  Could not extract workbook name from URL: {e}")

    # Create dashboard context from URL
    dashboard_context = {
        "url": dashboard_url,
        "workbook_name": workbook_name,
        "dashboard_name": view_name if view_name else "Dashboard"
    }

    # Run the connection initialization
    success, result = initialize_tableau_connection(dashboard_context)

    if success:
        print("\n✅ Connection successful!")
        print(f"✓ Workbook: {result.workbook_name}")
        print(f"✓ Site ID: {result.site_id}")
        print(f"✓ Workbook ID: {result.workbook_id}")
        print(f"✓ Available views: {len(result.available_views)}")

        # Create CompleteWorkbookDataManager to download the data
        print("\n📥 Starting workbook data download...")
        data_manager = CompleteWorkbookDataManager(connection_manager)

        # Run async download
        async def download_data():
            workbook_result = await data_manager.fetch_complete_workbook_data(
                workbook_name=result.workbook_name,
                workbook_id=result.workbook_id,
                site_id=result.site_id,
                auth_token=result.auth_token,
                export_to_csv=True
            )
            return workbook_result

        workbook_result = asyncio.run(download_data())

        if workbook_result.get('success'):
            print(f"\n✅ Download complete!")
            print(f"📁 Output directory: {workbook_result.get('csv_export_dir', 'N/A')}")

            # Print summary
            metadata = workbook_result.get('metadata', {})
            datasources = workbook_result.get('datasources_data', {})
            print(f"\n📊 Summary:")
            print(f"  - Datasources: {len(datasources)}")
            if metadata:
                print(f"  - Worksheets: {len(metadata.get('worksheets', []))}")
                print(f"  - Dashboards: {len(metadata.get('dashboards', []))}")
        else:
            print(f"\n❌ Download failed: {workbook_result.get('error')}")
            sys.exit(1)
    else:
        print(f"\n❌ Connection failed: {result}")
        sys.exit(1)

