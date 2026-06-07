from typing import List, Dict, Any
from dataclasses import dataclass
from langchain_text_splitters import RecursiveCharacterTextSplitter
from .parsers import DocumentSection

@dataclass
class Chunk:
    id: str
    text: str
    metadata: Dict[str, Any]

class TableAwareChunker:
    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""]
        )

    def chunk(self, sections: List[DocumentSection], filename: str, workspace_id: str) -> List[Chunk]:
        chunks = []
        chunk_idx = 0
        
        for section in sections:
            # Generate a base metadata dictionary for this section
            base_metadata = {
                "filename": filename,
                "workspace_id": workspace_id,
                "page": section.page,
                "is_table": section.section_type == "table"
            }
            
            if section.section_type == "table":
                # Preserve tables as complete units
                # We do not split tables to avoid losing context
                chunks.append(Chunk(
                    id=f"{filename}__p{section.page}__c{chunk_idx}",
                    text=section.text,
                    metadata={**base_metadata, "chunk_index": chunk_idx}
                ))
                chunk_idx += 1
            else:
                # Regular text splitting
                # Using Langchain's text splitter
                split_texts = self.text_splitter.split_text(section.text)
                
                for split_text in split_texts:
                    if not split_text.strip():
                        continue
                        
                    chunks.append(Chunk(
                        id=f"{filename}__p{section.page}__c{chunk_idx}",
                        text=split_text.strip(),
                        metadata={**base_metadata, "chunk_index": chunk_idx}
                    ))
                    chunk_idx += 1
                    
        return chunks
