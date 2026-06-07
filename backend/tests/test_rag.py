import pytest
from unittest.mock import MagicMock, patch
from rag.rag_engine import RAGEngine

@pytest.fixture
def mock_chroma():
    with patch('rag.rag_engine.chromadb.PersistentClient') as mock_client:
        mock_client.return_value.get_or_create_collection.return_value = MagicMock()
        yield mock_client

def test_rag_engine_init(mock_chroma):
    engine = RAGEngine(persist_directory="test_db")
    assert engine is not None
    assert engine.persist_directory == "test_db"
    assert engine.collection is not None

def test_rag_engine_status(mock_chroma):
    engine = RAGEngine(persist_directory="test_db")
    engine.collection.count.return_value = 10
    engine.collection.get.return_value = {"metadatas": [{"filename": "doc1.pdf"}]}
    
    status = engine.get_status("test-workspace")
    assert status["total_chunks"] == 10
    assert status["indexed_files"] == 1
    assert "doc1.pdf" in status["file_list"]

