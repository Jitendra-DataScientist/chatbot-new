"""
Vector Database System for Tableau Analytics Agent
Caches workbook-level data for efficient retrieval and context
"""

import os
import json
import pickle
import numpy as np
import pandas as pd
from typing import Dict, List, Any, Optional, Tuple
import logging
from datetime import datetime, timedelta
import hashlib
from pathlib import Path

# Optional dependencies for RAG system
try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    faiss = None

# OpenAI for embeddings (should be available since it's in requirements.txt)
try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    openai = None

RAG_DEPENDENCIES_AVAILABLE = FAISS_AVAILABLE and OPENAI_AVAILABLE

from models.schemas import DataChunk, VectorSearchResult

class TableauVectorStore:
    """
    Vector database for caching and retrieving Tableau workbook data
    Uses FAISS for efficient similarity search and retrieval
    """
    
    def __init__(self, data_dir: str = "RAG/data", openai_client=None, openai_api_key: str = None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.logger = logging.getLogger(__name__)
        
        # Check if RAG dependencies are available
        if not RAG_DEPENDENCIES_AVAILABLE:
            if not FAISS_AVAILABLE:
                self.logger.warning("FAISS not available. Vector store will use basic fallback.")
            if not OPENAI_AVAILABLE:
                self.logger.warning("OpenAI not available. Vector store will use basic fallback.")
            self.openai_client = None
            self.embedding_dim = 1536  # OpenAI text-embedding-3-small dimension
            self.index = None
            self.chunks: List[DataChunk] = []
            self.cache_ttl = 3600 * 24  # 24 hours
            return
        
        # Initialize OpenAI client for embeddings
        try:
            if openai_client:
                self.openai_client = openai_client
                self.logger.info("Using provided OpenAI client for embeddings")
            elif openai_api_key:
                self.openai_client = openai.OpenAI(api_key=openai_api_key)
                self.logger.info("Created new OpenAI client for embeddings")
            else:
                # Try to get from environment
                self.openai_client = openai.OpenAI()  # Will use OPENAI_API_KEY env var
                self.logger.info("Created OpenAI client from environment")
            
            self.embedding_dim = 1536  # Dimension of text-embedding-3-small
            self.embedding_model = "text-embedding-3-small"  # Cheaper and faster than text-embedding-3-large
            
            # Test the connection
            test_response = self.openai_client.embeddings.create(
                input="test",
                model=self.embedding_model
            )
            self.logger.info("OpenAI embeddings initialized successfully")
            
        except Exception as e:
            self.logger.error(f"Error initializing OpenAI embeddings: {e}")
            self.openai_client = None
            self.embedding_dim = 1536
        
        # FAISS index for vector similarity search
        self.index = None
        self.chunks: List[DataChunk] = []
        
        # Cache settings
        self.cache_ttl = 3600 * 24  # 24 hours
        
        # Initialize or load existing index
        self._initialize_index()

    def _generate_embedding(self, text: str) -> Optional[List[float]]:
        """Generate embedding using OpenAI API"""
        if not self.openai_client:
            return None
            
        try:
            response = self.openai_client.embeddings.create(
                input=text,
                model=self.embedding_model
            )
            return response.data[0].embedding
        except Exception as e:
            self.logger.error(f"Error generating embedding: {e}")
            return None

    def _initialize_index(self):
        """Initialize or load existing FAISS index"""
        
        index_path = self.data_dir / "faiss_index.index"
        chunks_path = self.data_dir / "chunks.pkl"
        
        try:
            if index_path.exists() and chunks_path.exists():
                # Load existing index
                self.index = faiss.read_index(str(index_path))
                with open(chunks_path, 'rb') as f:
                    self.chunks = pickle.load(f)
                
                self.logger.info(f"Loaded existing vector index with {len(self.chunks)} chunks")
            else:
                # Create new index
                self.index = faiss.IndexFlatIP(self.embedding_dim)  # Inner product for cosine similarity
                self.chunks = []
                
                self.logger.info("Created new vector index")
                
        except Exception as e:
            self.logger.error(f"Error initializing vector index: {e}")
            # Fallback to new index
            self.index = faiss.IndexFlatIP(self.embedding_dim)
            self.chunks = []

    def cache_workbook_data(self, workbook_id: str, workbook_name: str, worksheets_data: Dict[str, pd.DataFrame]) -> bool:
        """
        Cache workbook data in vector database
        
        Args:
            workbook_id: Unique workbook identifier
            workbook_name: Human readable workbook name
            worksheets_data: Dictionary of worksheet_name -> DataFrame
            
        Returns:
            Success status
        """
        try:
            self.logger.info(f"Caching workbook data: {workbook_name} ({workbook_id})")
            
            # If RAG dependencies are not available, skip caching but return success
            if not RAG_DEPENDENCIES_AVAILABLE:
                self.logger.info("RAG dependencies not available, skipping vector caching (basic functionality preserved)")
                return True
            
            # Clear existing data for this workbook
            self._remove_workbook_data(workbook_id)
            
            chunks_to_add = []
            
            for worksheet_name, df in worksheets_data.items():
                if df is None or df.empty:
                    continue
                
                # Create data chunk for this worksheet
                chunk_id = self._generate_chunk_id(workbook_id, worksheet_name)
                
                # Generate comprehensive data summary
                data_summary = self._generate_data_summary(df, worksheet_name)
                
                # Generate column information
                column_info = self._generate_column_info(df)
                
                # Create embedding if OpenAI client is available
                embedding = None
                if self.openai_client:
                    text_for_embedding = f"{worksheet_name} {data_summary} {' '.join(df.columns.tolist())}"
                    embedding = self._generate_embedding(text_for_embedding)
                
                # Create data chunk
                chunk = DataChunk(
                    chunk_id=chunk_id,
                    workbook_id=workbook_id,
                    worksheet_name=worksheet_name,
                    data_summary=data_summary,
                    column_info=column_info,
                    embedding=embedding,
                    metadata={
                        'workbook_name': workbook_name,
                        'shape': df.shape,
                        'cached_at': datetime.now().isoformat(),
                        'columns': df.columns.tolist(),
                        'dtypes': {col: str(dtype) for col, dtype in df.dtypes.items()}
                    }
                )
                
                chunks_to_add.append(chunk)
                
                # Cache raw DataFrame data separately
                df_path = self.data_dir / f"{chunk_id}_data.pkl"
                with open(df_path, 'wb') as f:
                    pickle.dump(df, f)
            
            # Add chunks to vector store
            if chunks_to_add:
                self._add_chunks_to_index(chunks_to_add)
                self._save_index()
                
                self.logger.info(f"Successfully cached {len(chunks_to_add)} worksheets for workbook {workbook_name}")
                return True
            else:
                self.logger.warning(f"No valid worksheets found in workbook {workbook_name}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error caching workbook data: {e}")
            return False

    def search_similar_data(self, query: str, workbook_id: Optional[str] = None, top_k: int = 5) -> List[VectorSearchResult]:
        """
        Search for similar data chunks based on query
        
        Args:
            query: Search query
            workbook_id: Optional workbook filter
            top_k: Number of top results to return
            
        Returns:
            List of similar data chunks
        """
        try:
            # If RAG dependencies are not available, return empty results
            if not RAG_DEPENDENCIES_AVAILABLE:
                self.logger.info("RAG dependencies not available, returning empty search results")
                return []
                
            if not self.openai_client or not self.chunks:
                return []
            
            # Generate query embedding
            query_embedding_list = self._generate_embedding(query)
            if not query_embedding_list:
                return []
            
            query_embedding = np.array([query_embedding_list]).astype('float32')
            
            # Normalize for cosine similarity
            faiss.normalize_L2(query_embedding)
            
            # Search in FAISS index
            scores, indices = self.index.search(query_embedding, min(top_k * 2, len(self.chunks)))
            
            results = []
            
            for score, idx in zip(scores[0], indices[0]):
                if idx >= len(self.chunks):
                    continue
                
                chunk = self.chunks[idx]
                
                # Filter by workbook if specified
                if workbook_id and chunk.workbook_id != workbook_id:
                    continue
                
                # Check if chunk is still valid (not expired)
                if self._is_chunk_expired(chunk):
                    continue
                
                result = VectorSearchResult(
                    chunk_id=chunk.chunk_id,
                    similarity_score=float(score),
                    data_summary=chunk.data_summary,
                    metadata=chunk.metadata
                )
                
                results.append(result)
                
                if len(results) >= top_k:
                    break
            
            self.logger.info(f"Found {len(results)} similar data chunks for query: '{query}'")
            return results
            
        except Exception as e:
            self.logger.error(f"Error searching similar data: {e}")
            return []

    def get_worksheet_data(self, chunk_id: str) -> Optional[pd.DataFrame]:
        """
        Retrieve cached worksheet DataFrame by chunk ID
        
        Args:
            chunk_id: Chunk identifier
            
        Returns:
            Cached DataFrame or None
        """
        try:
            df_path = self.data_dir / f"{chunk_id}_data.pkl"
            
            if df_path.exists():
                with open(df_path, 'rb') as f:
                    df = pickle.load(f)
                
                self.logger.info(f"Retrieved cached data for chunk: {chunk_id}")
                return df
            else:
                self.logger.warning(f"No cached data found for chunk: {chunk_id}")
                return None
                
        except Exception as e:
            self.logger.error(f"Error retrieving worksheet data for {chunk_id}: {e}")
            return None

    def get_workbook_chunks(self, workbook_id: str) -> List[DataChunk]:
        """
        Get all chunks for a specific workbook
        
        Args:
            workbook_id: Workbook identifier
            
        Returns:
            List of data chunks for the workbook
        """
        try:
            workbook_chunks = [chunk for chunk in self.chunks if chunk.workbook_id == workbook_id]
            
            # Filter out expired chunks
            valid_chunks = [chunk for chunk in workbook_chunks if not self._is_chunk_expired(chunk)]
            
            self.logger.info(f"Found {len(valid_chunks)} valid chunks for workbook: {workbook_id}")
            return valid_chunks
            
        except Exception as e:
            self.logger.error(f"Error getting workbook chunks: {e}")
            return []

    def clear_expired_cache(self):
        """Remove expired cache entries"""
        try:
            initial_count = len(self.chunks)
            
            # Find expired chunks
            expired_chunk_ids = []
            valid_chunks = []
            
            for chunk in self.chunks:
                if self._is_chunk_expired(chunk):
                    expired_chunk_ids.append(chunk.chunk_id)
                else:
                    valid_chunks.append(chunk)
            
            # Remove expired data files
            for chunk_id in expired_chunk_ids:
                df_path = self.data_dir / f"{chunk_id}_data.pkl"
                if df_path.exists():
                    df_path.unlink()
            
            # Update chunks list and rebuild index if needed
            if expired_chunk_ids:
                self.chunks = valid_chunks
                self._rebuild_index()
                self._save_index()
                
                self.logger.info(f"Removed {len(expired_chunk_ids)} expired chunks from cache")
            
        except Exception as e:
            self.logger.error(f"Error clearing expired cache: {e}")

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        try:
            workbook_counts = {}
            for chunk in self.chunks:
                workbook_id = chunk.workbook_id
                workbook_counts[workbook_id] = workbook_counts.get(workbook_id, 0) + 1
            
            cache_size_mb = sum(
                f.stat().st_size for f in self.data_dir.glob("*.pkl")
            ) / (1024 * 1024)
            
            return {
                'total_chunks': len(self.chunks),
                'unique_workbooks': len(workbook_counts),
                'workbook_distribution': workbook_counts,
                'cache_size_mb': round(cache_size_mb, 2),
                'data_directory': str(self.data_dir),
                'index_loaded': self.index is not None
            }
            
        except Exception as e:
            self.logger.error(f"Error getting cache stats: {e}")
            return {'error': str(e)}

    def _generate_chunk_id(self, workbook_id: str, worksheet_name: str) -> str:
        """Generate unique chunk ID"""
        combined = f"{workbook_id}_{worksheet_name}_{datetime.now().date()}"
        return hashlib.md5(combined.encode()).hexdigest()

    def _generate_data_summary(self, df: pd.DataFrame, worksheet_name: str) -> str:
        """Generate comprehensive data summary for a worksheet"""
        
        try:
            numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
            categorical_cols = df.select_dtypes(include=['object']).columns.tolist()
            
            summary_parts = [
                f"Worksheet '{worksheet_name}' contains {len(df):,} rows and {len(df.columns)} columns."
            ]
            
            if numeric_cols:
                summary_parts.append(f"Numeric columns: {', '.join(numeric_cols[:5])}.")
                
                # Add basic statistics for key numeric columns
                for col in numeric_cols[:2]:
                    mean_val = df[col].mean()
                    if not pd.isna(mean_val):
                        summary_parts.append(f"{col} average: {mean_val:.2f}.")
            
            if categorical_cols:
                summary_parts.append(f"Categorical columns: {', '.join(categorical_cols[:5])}.")
                
                # Add top categories for key categorical columns
                for col in categorical_cols[:2]:
                    top_value = df[col].mode().iloc[0] if not df[col].mode().empty else None
                    if top_value:
                        summary_parts.append(f"Most common {col}: {top_value}.")
            
            # Data quality info
            missing_pct = (df.isnull().sum().sum() / df.size) * 100
            if missing_pct > 0:
                summary_parts.append(f"Missing data: {missing_pct:.1f}%.")
            
            return " ".join(summary_parts)
            
        except Exception as e:
            self.logger.error(f"Error generating data summary: {e}")
            return f"Worksheet '{worksheet_name}' with {len(df)} rows and {len(df.columns)} columns."

    def _generate_column_info(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Generate detailed column information"""
        
        try:
            column_info = {}
            
            for col in df.columns:
                info = {
                    'dtype': str(df[col].dtype),
                    'non_null_count': int(df[col].count()),
                    'unique_count': int(df[col].nunique())
                }
                
                if pd.api.types.is_numeric_dtype(df[col]):
                    info.update({
                        'mean': float(df[col].mean()) if not df[col].empty else 0,
                        'std': float(df[col].std()) if not df[col].empty else 0,
                        'min': float(df[col].min()) if not df[col].empty else 0,
                        'max': float(df[col].max()) if not df[col].empty else 0
                    })
                else:
                    # For categorical columns, store top values
                    top_values = df[col].value_counts().head(3)
                    info['top_values'] = top_values.to_dict()
                
                column_info[col] = info
            
            return column_info
            
        except Exception as e:
            self.logger.error(f"Error generating column info: {e}")
            return {}

    def _add_chunks_to_index(self, chunks: List[DataChunk]):
        """Add data chunks to FAISS index"""
        
        if not self.encoder:
            return
        
        embeddings = []
        
        for chunk in chunks:
            if chunk.embedding:
                embeddings.append(chunk.embedding)
            else:
                # Generate embedding if not provided
                text = f"{chunk.worksheet_name} {chunk.data_summary}"
                embedding = self._generate_embedding(text)
                if embedding:
                    embeddings.append(embedding)
                    chunk.embedding = embedding
        
        if embeddings:
            # Convert to numpy array and normalize
            embeddings_array = np.array(embeddings).astype('float32')
            faiss.normalize_L2(embeddings_array)
            
            # Add to FAISS index
            self.index.add(embeddings_array)
            
            # Add chunks to our list
            self.chunks.extend(chunks)

    def _rebuild_index(self):
        """Rebuild FAISS index from current chunks"""
        
        if not self.openai_client or not self.chunks:
            return
        
        # Create new index
        self.index = faiss.IndexFlatIP(self.embedding_dim)
        
        # Add all chunks
        embeddings = []
        
        for chunk in self.chunks:
            if chunk.embedding:
                embeddings.append(chunk.embedding)
            else:
                # Generate missing embedding
                text = f"{chunk.worksheet_name} {chunk.data_summary}"
                embedding = self._generate_embedding(text)
                if embedding:
                    embeddings.append(embedding)
                    chunk.embedding = embedding
        
        if embeddings:
            embeddings_array = np.array(embeddings).astype('float32')
            faiss.normalize_L2(embeddings_array)
            self.index.add(embeddings_array)

    def _save_index(self):
        """Save FAISS index and chunks to disk"""
        
        try:
            index_path = self.data_dir / "faiss_index.index"
            chunks_path = self.data_dir / "chunks.pkl"
            
            # Save FAISS index
            faiss.write_index(self.index, str(index_path))
            
            # Save chunks
            with open(chunks_path, 'wb') as f:
                pickle.dump(self.chunks, f)
            
            self.logger.info("Saved vector index and chunks to disk")
            
        except Exception as e:
            self.logger.error(f"Error saving index: {e}")

    def _remove_workbook_data(self, workbook_id: str):
        """Remove all data for a specific workbook"""
        
        try:
            # Find chunks to remove
            chunks_to_remove = [chunk for chunk in self.chunks if chunk.workbook_id == workbook_id]
            
            # Remove data files
            for chunk in chunks_to_remove:
                df_path = self.data_dir / f"{chunk.chunk_id}_data.pkl"
                if df_path.exists():
                    df_path.unlink()
            
            # Update chunks list
            self.chunks = [chunk for chunk in self.chunks if chunk.workbook_id != workbook_id]
            
            # Rebuild index if we removed chunks
            if chunks_to_remove:
                self._rebuild_index()
                
                self.logger.info(f"Removed {len(chunks_to_remove)} chunks for workbook: {workbook_id}")
            
        except Exception as e:
            self.logger.error(f"Error removing workbook data: {e}")

    def _is_chunk_expired(self, chunk: DataChunk) -> bool:
        """Check if a chunk has expired"""
        
        try:
            cached_at_str = chunk.metadata.get('cached_at')
            if not cached_at_str:
                return True
            
            cached_at = datetime.fromisoformat(cached_at_str)
            expiry_time = cached_at + timedelta(seconds=self.cache_ttl)
            
            return datetime.now() > expiry_time
            
        except Exception as e:
            self.logger.error(f"Error checking chunk expiry: {e}")
            return True  # Err on the side of caution
