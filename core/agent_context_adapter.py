"""
Agent Context Adapter - Bridge for Existing Agents

Provides adapters and utilities to make existing agents work with RequestContext
without requiring complete rewrites. Manages per-session agent state using the
new scoped infrastructure.

Strategies:
1. Context Injection: Pass RequestContext to agents that can be modified
2. State Wrapper: Wrap agent state in scoped storage for agents that can't change
3. Factory Pattern: Create per-session agent instances from factory

Author: Enterprise Architecture Refactor
Date: December 2024
"""

from typing import Dict, Optional, Any
import threading

from core.request_context import RequestContext
from core.scoped_cache_manager import get_scoped_cache_manager
from master_logger import setup_module_logger

logger = setup_module_logger('core.agent_context_adapter')


class AgentContextManager:
    """
    Manages agent state per session using scoped cache.
    
    For agents that maintain internal state (conversation history, memory, etc.),
    this manager stores that state in session-scoped cache so each session
    gets its own isolated agent state.
    
    Usage:
        # In agent initialization
        context_mgr = AgentContextManager('query_agent')
        
        # Store agent state
        context_mgr.set_state(context, 'conversation_history', history)
        
        # Retrieve agent state
        history = context_mgr.get_state(context, 'conversation_history', default=[])
    """
    
    def __init__(self, agent_name: str):
        """
        Initialize context manager for specific agent.
        
        Args:
            agent_name: Unique name for this agent (used as cache type)
        """
        self.agent_name = agent_name
        self.cache_manager = get_scoped_cache_manager()
        
        logger.debug(f"AgentContextManager created for: {agent_name}")
    
    def set_state(
        self,
        context: RequestContext,
        state_key: str,
        state_value: Any,
        ttl_minutes: Optional[int] = None
    ):
        """
        Store agent state for session.
        
        Args:
            context: RequestContext identifying the session
            state_key: Key for this piece of state
            state_value: Value to store
            ttl_minutes: Optional TTL (defaults to session lifetime)
        """
        cache_type = f"agent_{self.agent_name}"
        self.cache_manager.set(context, cache_type, state_key, state_value, ttl_minutes)
        
        logger.debug(
            f"Stored agent state - Agent: {self.agent_name}, "
            f"Key: {state_key}, Session: {context.session_id[:8]}..."
        )
    
    def get_state(
        self,
        context: RequestContext,
        state_key: str,
        default: Any = None
    ) -> Any:
        """
        Retrieve agent state for session.
        
        Args:
            context: RequestContext identifying the session
            state_key: Key for state to retrieve
            default: Default value if not found
            
        Returns:
            Stored state or default
        """
        cache_type = f"agent_{self.agent_name}"
        return self.cache_manager.get(context, cache_type, state_key, default)
    
    def clear_state(self, context: RequestContext) -> int:
        """
        Clear all state for this agent in this session.
        
        Args:
            context: RequestContext identifying the session
            
        Returns:
            Number of state entries cleared
        """
        cache_type = f"agent_{self.agent_name}"
        return self.cache_manager.clear_type(context, cache_type)


class DisambiguationCacheAdapter:
    """
    Adapter for disambiguation cache using new scoped system.
    
    Replaces global disambiguation cache with session-scoped version.
    Maintains backward compatibility with existing disambiguation logic.
    """
    
    def __init__(self):
        self.cache_manager = get_scoped_cache_manager()
        logger.debug("DisambiguationCacheAdapter initialized")
    
    def set_disambiguation(
        self,
        context: RequestContext,
        column_name: str,
        original_value: str,
        selected_value: str,
        source_id: Optional[str] = None
    ):
        """
        Store user's disambiguation choice.
        
        Args:
            context: RequestContext identifying the session
            column_name: Name of column being disambiguated
            original_value: Original ambiguous value
            selected_value: User's selected clarification
            source_id: Optional source identifier
        """
        # Normalize key
        cache_key = self._make_cache_key(column_name, original_value, source_id)
        
        self.cache_manager.set(
            context,
            'disambiguation',
            cache_key,
            selected_value,
            ttl_minutes=None  # Persists for session lifetime
        )
        
        logger.info(
            f"Stored disambiguation - Column: {column_name}, "
            f"'{original_value}' -> '{selected_value}', "
            f"Session: {context.session_id[:8]}..."
        )
    
    def get_disambiguation(
        self,
        context: RequestContext,
        column_name: str,
        original_value: str,
        source_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Retrieve user's previous disambiguation choice.
        
        Args:
            context: RequestContext identifying the session
            column_name: Column name
            original_value: Original value
            source_id: Optional source identifier
            
        Returns:
            Previously selected value or None
        """
        cache_key = self._make_cache_key(column_name, original_value, source_id)
        
        return self.cache_manager.get(
            context,
            'disambiguation',
            cache_key,
            default=None
        )
    
    def get_all_disambiguations(
        self,
        context: RequestContext
    ) -> Dict[str, str]:
        """
        Get all disambiguation choices for session.
        
        Args:
            context: RequestContext identifying the session
            
        Returns:
            Dictionary of all disambiguation choices
        """
        return self.cache_manager.get_all(context, 'disambiguation')
    
    def _make_cache_key(
        self,
        column_name: str,
        original_value: str,
        source_id: Optional[str]
    ) -> str:
        """
        Create normalized cache key for disambiguation.
        
        Args:
            column_name: Column name
            original_value: Original value
            source_id: Optional source identifier
            
        Returns:
            Normalized cache key
        """
        # Normalize value (lowercase, strip whitespace)
        normalized_value = str(original_value).lower().strip()
        
        # Include source_id if provided for finer granularity
        if source_id:
            return f"{column_name}:{source_id}:{normalized_value}"
        else:
            return f"{column_name}:{normalized_value}"


class ConversationHistoryAdapter:
    """
    Adapter for conversation history using scoped system.
    
    Stores conversation history per session, replacing global conversation state.
    """
    
    def __init__(self, max_history: int = 10):
        """
        Initialize conversation history adapter.
        
        Args:
            max_history: Maximum number of conversation turns to keep
        """
        self.cache_manager = get_scoped_cache_manager()
        self.max_history = max_history
        logger.debug(f"ConversationHistoryAdapter initialized (max_history={max_history})")
    
    def add_turn(
        self,
        context: RequestContext,
        query: str,
        response: str,
        metadata: Optional[Dict] = None
    ):
        """
        Add conversation turn to history.
        
        Args:
            context: RequestContext identifying the session
            query: User query
            response: Agent response
            metadata: Optional metadata (entities, intent, etc.)
        """
        # Get current history
        history = self.get_history(context)
        
        # Add new turn
        turn = {
            'timestamp': context.last_activity.isoformat(),
            'query': query,
            'response': response,
            'metadata': metadata or {}
        }
        
        history.append(turn)
        
        # Trim to max_history
        if len(history) > self.max_history:
            history = history[-self.max_history:]
        
        # Store back
        self.cache_manager.set(
            context,
            'conversation_history',
            'turns',
            history,
            ttl_minutes=None
        )
        
        logger.debug(
            f"Added conversation turn - Session: {context.session_id[:8]}..., "
            f"Total turns: {len(history)}"
        )
    
    def get_history(
        self,
        context: RequestContext
    ) -> list:
        """
        Get conversation history for session.
        
        Args:
            context: RequestContext identifying the session
            
        Returns:
            List of conversation turns
        """
        return self.cache_manager.get(
            context,
            'conversation_history',
            'turns',
            default=[]
        )
    
    def clear_history(self, context: RequestContext):
        """
        Clear conversation history for session.
        
        Args:
            context: RequestContext identifying the session
        """
        self.cache_manager.delete(context, 'conversation_history', 'turns')
        logger.info(f"Cleared conversation history - Session: {context.session_id[:8]}...")


class StatelessAgentWrapper:
    """
    Wrapper that makes stateful agents behave statelessly.
    
    Takes an agent class that normally maintains internal state and wraps it
    so that state is stored in scoped cache instead.
    
    Usage:
        # Wrap existing agent
        wrapper = StatelessAgentWrapper(QueryAgent, 'query_agent')
        
        # Use agent with context
        result = wrapper.process(context, query="...", data=df)
    """
    
    def __init__(self, agent_class, agent_name: str, **agent_init_kwargs):
        """
        Initialize wrapper with agent class.
        
        Args:
            agent_class: Class of agent to wrap
            agent_name: Unique name for this agent
            **agent_init_kwargs: Kwargs to pass to agent constructor
        """
        self.agent_class = agent_class
        self.agent_name = agent_name
        self.agent_init_kwargs = agent_init_kwargs
        self.context_manager = AgentContextManager(agent_name)
        
        # Cache of agent instances per session (lazy creation)
        self._agent_instances: Dict[str, Any] = {}
        self._lock = threading.RLock()
        
        logger.info(f"StatelessAgentWrapper created for: {agent_name}")
    
    def get_agent_instance(self, context: RequestContext):
        """
        Get or create agent instance for session.
        
        Creates one agent instance per session for isolation.
        
        Args:
            context: RequestContext identifying the session
            
        Returns:
            Agent instance for this session
        """
        session_key = context.get_session_key()
        
        with self._lock:
            if session_key not in self._agent_instances:
                # Create new agent instance for this session
                agent = self.agent_class(**self.agent_init_kwargs)
                self._agent_instances[session_key] = agent
                
                logger.info(
                    f"Created new agent instance - Agent: {self.agent_name}, "
                    f"Session: {context.session_id[:8]}..."
                )
            
            return self._agent_instances[session_key]
    
    def cleanup_session(self, context: RequestContext):
        """
        Cleanup agent instance for session.
        
        Args:
            context: RequestContext identifying the session
        """
        session_key = context.get_session_key()
        
        with self._lock:
            if session_key in self._agent_instances:
                del self._agent_instances[session_key]
                logger.info(
                    f"Cleaned up agent instance - Agent: {self.agent_name}, "
                    f"Session: {context.session_id[:8]}..."
                )
        
        # Also clear context manager state
        self.context_manager.clear_state(context)


# Singleton adapters for easy access
_disambiguation_adapter: Optional[DisambiguationCacheAdapter] = None
_conversation_adapter: Optional[ConversationHistoryAdapter] = None


def get_disambiguation_adapter() -> DisambiguationCacheAdapter:
    """Get global disambiguation adapter instance"""
    global _disambiguation_adapter
    if _disambiguation_adapter is None:
        _disambiguation_adapter = DisambiguationCacheAdapter()
    return _disambiguation_adapter


def get_conversation_adapter() -> ConversationHistoryAdapter:
    """Get global conversation history adapter instance"""
    global _conversation_adapter
    if _conversation_adapter is None:
        _conversation_adapter = ConversationHistoryAdapter(max_history=10)
    return _conversation_adapter

