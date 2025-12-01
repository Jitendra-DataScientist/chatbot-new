"""
Persistent Authentication Token Storage System

This module provides pickle-based persistent storage for Tableau authentication tokens
with automatic expiration, file locking, and error recovery capabilities.

Author: Authentication Optimization System
Date: 2024
"""

import pickle
import os
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, Any
from master_logger import setup_module_logger

# Setup module logger
master_logger = setup_module_logger('auth_storage')

# Platform-specific imports for file locking
try:
    import fcntl  # Unix/Linux file locking
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False
    try:
        import msvcrt  # Windows file locking
        HAS_MSVCRT = True
    except ImportError:
        HAS_MSVCRT = False
        master_logger.warning("No file locking available - running without concurrent access protection")


class AuthTokenStorage:
    """
    Persistent authentication token storage using pickle files.
    
    Features:
    - Atomic file operations to prevent corruption
    - Platform-specific file locking for concurrent access
    - Automatic expiration cleanup
    - Graceful error recovery from corrupted files
    - Thread-safe operations
    """
    
    def __init__(self, cache_file: str = "auth_cache.pickle", cache_timeout_minutes: int = 450):
        """
        Initialize AuthTokenStorage
        
        Args:
            cache_file: Path to pickle cache file
            cache_timeout_minutes: Auth token timeout in minutes (default: 450 = 7.5 hours)
        """
        self.cache_file = cache_file
        self.cache_timeout = timedelta(minutes=cache_timeout_minutes)
        self._lock = threading.Lock()
        
        # Ensure cache directory exists
        cache_dir = os.path.dirname(self.cache_file) if os.path.dirname(self.cache_file) else "."
        if cache_dir != "." and not os.path.exists(cache_dir):
            os.makedirs(cache_dir, exist_ok=True)
        
        master_logger.info(f"AuthTokenStorage initialized - cache_file: {self.cache_file}, timeout: {cache_timeout_minutes}min")

    def _lock_file(self, file_handle, lock_type: str = "exclusive"):
        """
        Platform-specific file locking
        
        Args:
            file_handle: Open file handle
            lock_type: "shared" for read, "exclusive" for write
        """
        if HAS_FCNTL:
            # Unix/Linux locking
            if lock_type == "shared":
                fcntl.flock(file_handle.fileno(), fcntl.LOCK_SH)
            else:
                fcntl.flock(file_handle.fileno(), fcntl.LOCK_EX)
        elif HAS_MSVCRT:
            # Windows locking
            try:
                if lock_type == "shared":
                    # Windows doesn't have shared locks easily - use exclusive
                    msvcrt.locking(file_handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    msvcrt.locking(file_handle.fileno(), msvcrt.LK_LOCK, 1)
            except IOError:
                # Lock already held - continue without locking
                pass

    def _unlock_file(self, file_handle):
        """Platform-specific file unlocking"""
        if HAS_FCNTL:
            fcntl.flock(file_handle.fileno(), fcntl.LOCK_UN)
        elif HAS_MSVCRT:
            try:
                msvcrt.locking(file_handle.fileno(), msvcrt.LK_UNLCK, 1)
            except IOError:
                pass

    def load_auth_cache(self) -> Dict[str, Any]:
        """
        Load authentication cache from pickle file with automatic cleanup
        
        Returns:
            Dictionary containing cached auth tokens
        """
        with self._lock:
            try:
                if not os.path.exists(self.cache_file):
                    master_logger.debug("Auth cache file does not exist - returning empty cache")
                    return {}

                with open(self.cache_file, 'rb') as f:
                    try:
                        self._lock_file(f, "shared")
                        cache = pickle.load(f)
                        self._unlock_file(f)
                        
                        master_logger.debug(f"Loaded auth cache with {len(cache)} entries")
                        
                        # Clean expired entries
                        cleaned_cache = self._clean_expired_tokens(cache)
                        
                        # Save cleaned cache if any entries were removed
                        if len(cleaned_cache) != len(cache):
                            master_logger.info(f"Cleaned {len(cache) - len(cleaned_cache)} expired auth entries")
                            self._save_auth_cache_internal(cleaned_cache)
                        
                        return cleaned_cache
                        
                    except (pickle.PickleError, EOFError, ValueError) as e:
                        master_logger.error(f"Pickle file corrupted: {e}")
                        self._handle_corrupted_cache()
                        return {}
                        
            except (IOError, OSError) as e:
                master_logger.error(f"Failed to read auth cache file: {e}")
                return {}

    def save_auth_cache(self, cache: Dict[str, Any]):
        """
        Public method to save auth cache
        
        Args:
            cache: Dictionary of auth tokens to save
        """
        with self._lock:
            self._save_auth_cache_internal(cache)

    def _save_auth_cache_internal(self, cache: Dict[str, Any]):
        """
        Save authentication cache to pickle file with atomic operations and retry logic
        
        Args:
            cache: Dictionary of auth tokens to save
        """
        max_retries = 3
        retry_delay = 0.1
        
        for attempt in range(max_retries):
            try:
                # Atomic write using temporary file with unique timestamp
                temp_file = f"{self.cache_file}.tmp.{int(time.time())}.{attempt}"
                
                with open(temp_file, 'wb') as f:
                    self._lock_file(f, "exclusive")
                    pickle.dump(cache, f, protocol=pickle.HIGHEST_PROTOCOL)
                    f.flush()
                    os.fsync(f.fileno())  # Force write to disk
                    self._unlock_file(f)
                
                # Atomic rename (replaces original file) with Windows-specific handling
                if os.name == 'nt':  # Windows
                    if os.path.exists(self.cache_file):
                        # Try to remove existing file with retry
                        for remove_attempt in range(3):
                            try:
                                os.remove(self.cache_file)
                                break
                            except PermissionError:
                                if remove_attempt < 2:
                                    time.sleep(0.05)
                                    continue
                                else:
                                    raise
                
                os.rename(temp_file, self.cache_file)
                master_logger.debug(f"Auth cache saved with {len(cache)} entries (attempt {attempt + 1})")
                return  # Success - exit retry loop
                
            except (pickle.PickleError, IOError, OSError, PermissionError) as e:
                master_logger.warning(f"Auth cache save attempt {attempt + 1} failed: {e}")
                
                # Clean up temp file if it exists
                temp_file_to_clean = f"{self.cache_file}.tmp.{int(time.time())}.{attempt}"
                if os.path.exists(temp_file_to_clean):
                    try:
                        os.remove(temp_file_to_clean)
                    except:
                        pass
                
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    master_logger.error(f"Failed to save auth cache after {max_retries} attempts: {e}")
                    return

    def get_auth_token(self, content_url: str) -> Optional[Tuple[str, str]]:
        """
        Get authentication token from cache with comprehensive logging
        
        Args:
            content_url: Tableau content URL key
            
        Returns:
            Tuple of (auth_token, site_id) if valid, None if expired/missing
        """
        import time
        start_time = time.time()
        
        cache = self.load_auth_cache()
        cache_key = f"auth_{content_url}"
        load_time = time.time() - start_time
        
        if cache_key in cache:
            cached_entry = cache[cache_key]
            
            # Check if still valid
            if self._is_entry_valid(cached_entry):
                # Calculate time since stored and remaining time
                stored_time = cached_entry.get('timestamp', datetime.now())
                age_seconds = (datetime.now() - stored_time).total_seconds()
                remaining_seconds = self.cache_timeout.total_seconds() - age_seconds
                
                total_time = time.time() - start_time
                master_logger.info(f"[AUTH] [BLIND_TRUST_HIT] AUTH_PICKLE_HIT: {content_url} | age: {age_seconds:.1f}s | remaining: {remaining_seconds/3600:.1f}h | lookup: {total_time*1000:.1f}ms")
                return cached_entry['auth_token'], cached_entry['site_id']
            else:
                # Token expired - 7.5h timer ended
                stored_time = cached_entry.get('timestamp', datetime.now()) 
                age_seconds = (datetime.now() - stored_time).total_seconds()
                
                master_logger.info(f"[AUTH] [BLIND_TRUST_EXPIRED] AUTH_EXPIRED: {content_url} | age: {age_seconds/3600:.1f}h | timeout: {self.cache_timeout.total_seconds()/3600:.1f}h")
                
                # Remove expired entry and save
                del cache[cache_key]
                self.save_auth_cache(cache)
                return None
        
        total_time = time.time() - start_time
        master_logger.info(f"[MISS] AUTH_CACHE_MISS: {content_url} | cache_entries: {len(cache)} | lookup: {total_time*1000:.1f}ms")
        return None

    def store_auth_token(self, content_url: str, auth_token: str, site_id: str):
        """
        Store authentication token in cache with performance logging
        
        Args:
            content_url: Tableau content URL key
            auth_token: Authentication token
            site_id: Site ID
        """
        import time
        start_time = time.time()
        
        cache = self.load_auth_cache()
        cache_key = f"auth_{content_url}"
        
        # Create cache entry with expiration info
        now = datetime.now()
        expires_at = now + self.cache_timeout
        
        cache[cache_key] = {
            'auth_token': auth_token,
            'site_id': site_id,
            'timestamp': now,
            'expires_at': expires_at
        }
        
        self.save_auth_cache(cache)
        
        store_time = time.time() - start_time
        token_preview = f"{auth_token[:8]}..." if len(auth_token) > 8 else auth_token
        
        master_logger.info(f"[AUTH] [BLIND_TRUST_START] AUTH_TOKEN_STORED: {content_url} | token: {token_preview} | 7.5h_timer_started | expires: {self.cache_timeout} | store_time: {store_time*1000:.1f}ms | total_cached: {len(cache)}")

    def _is_entry_valid(self, entry: Dict[str, Any]) -> bool:
        """
        BLIND TRUST: Check if cache entry is still valid (7.5h time-based only)
        
        Args:
            entry: Cache entry dictionary
            
        Returns:
            True if entry is still valid, False if expired (7.5h timer only)
        """
        now = datetime.now()
        
        # BLIND TRUST MODE: Only check time-based expiration, no server validation
        if 'expires_at' in entry:
            is_valid = now < entry['expires_at']
            if is_valid:
                remaining = (entry['expires_at'] - now).total_seconds() / 3600
                master_logger.debug(f"[AUTH] BLIND_TRUST: Pickle cache valid for {remaining:.1f}h more")
            return is_valid
        elif 'timestamp' in entry:
            time_diff = now - entry['timestamp']
            is_valid = time_diff < self.cache_timeout
            if is_valid:
                remaining = (self.cache_timeout - time_diff).total_seconds() / 3600
                master_logger.debug(f"[AUTH] BLIND_TRUST: Pickle cache valid for {remaining:.1f}h more")
            return is_valid
        else:
            # Invalid entry format
            master_logger.warning("[AUTH] BLIND_TRUST: Invalid cache entry format")
            return False

    def _clean_expired_tokens(self, cache: Dict[str, Any]) -> Dict[str, Any]:
        """
        Remove expired tokens from cache
        
        Args:
            cache: Original cache dictionary
            
        Returns:
            Cleaned cache dictionary with expired entries removed
        """
        cleaned = {}
        expired_count = 0
        
        for key, entry in cache.items():
            if self._is_entry_valid(entry):
                cleaned[key] = entry
            else:
                expired_count += 1
                master_logger.debug(f"Removing expired auth entry: {key}")
        
        if expired_count > 0:
            master_logger.info(f"Cleaned {expired_count} expired auth entries from cache")
        
        return cleaned

    def _handle_corrupted_cache(self):
        """
        Handle corrupted pickle file by backing up and resetting
        """
        if os.path.exists(self.cache_file):
            # Create backup with timestamp
            timestamp = int(time.time())
            backup_file = f"{self.cache_file}.corrupted.{timestamp}"
            
            try:
                os.rename(self.cache_file, backup_file)
                master_logger.warning(f"Corrupted auth cache backed up to: {backup_file}")
            except (IOError, OSError) as e:
                master_logger.error(f"Failed to backup corrupted cache: {e}")
                try:
                    os.remove(self.cache_file)
                    master_logger.info("Removed corrupted auth cache file")
                except:
                    pass

    def clear_cache(self):
        """Clear all cached authentication tokens"""
        with self._lock:
            try:
                if os.path.exists(self.cache_file):
                    os.remove(self.cache_file)
                    master_logger.info("Auth cache cleared")
            except (IOError, OSError) as e:
                master_logger.error(f"Failed to clear auth cache: {e}")

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the auth cache
        
        Returns:
            Dictionary with cache statistics
        """
        cache = self.load_auth_cache()
        
        valid_entries = 0
        expired_entries = 0
        
        for entry in cache.values():
            if self._is_entry_valid(entry):
                valid_entries += 1
            else:
                expired_entries += 1
        
        file_size = 0
        if os.path.exists(self.cache_file):
            file_size = os.path.getsize(self.cache_file)
        
        return {
            'total_entries': len(cache),
            'valid_entries': valid_entries,
            'expired_entries': expired_entries,
            'cache_file': self.cache_file,
            'cache_file_size': file_size,
            'cache_timeout_minutes': self.cache_timeout.total_seconds() / 60
        }


# Global instance for easy import
auth_storage = AuthTokenStorage()


if __name__ == "__main__":
    # Test the auth storage system
    import sys
    
    print("Testing AuthTokenStorage...")
    
    storage = AuthTokenStorage("test_auth_cache.pickle", cache_timeout_minutes=1)
    
    # Test storing and retrieving
    storage.store_auth_token("test_url", "test_token_123", "site_456")
    
    result = storage.get_auth_token("test_url")
    if result:
        token, site_id = result
        print(f"[OK] Retrieved: token={token}, site_id={site_id}")
    else:
        print("[ERR] Failed to retrieve token")
    
    # Test stats
    stats = storage.get_cache_stats()
    print(f"[STATS] Cache stats: {stats}")
    
    print("[OK] AuthTokenStorage test completed")
