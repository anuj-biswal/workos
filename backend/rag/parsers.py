import os
import io
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import pandas as pd
import fitz  # PyMuPDF
from PIL import Image
import pytesseract
import logging

# Docling is imported lazily in DoclingParser

logger = logging.getLogger(__name__)

@dataclass
class DocumentSection:
    text: str
    page: int
    section_type: str  # "text", "table", "heading", "image_description"
    table_data: Optional[List[List[str]]] = None
    metadata: Optional[Dict[str, Any]] = None

class BaseParser(ABC):
    @abstractmethod
    def parse(self, file_path: str) -> List[DocumentSection]:
        pass

class DoclingParser(BaseParser):
    def __init__(self):
        try:
            from docling.document_converter import DocumentConverter
            self.converter = DocumentConverter()
        except ImportError:
            raise ImportError("Docling is not installed")

    def parse(self, file_path: str) -> List[DocumentSection]:
        try:
            from docling_core.types.doc.document import TableItem, TextItem
        except ImportError:
            raise ImportError("docling_core is not installed")
        sections = []
        try:
            # Run Docling conversion
            result = self.converter.convert(file_path)
            doc = result.document
            
            for item, level in doc.iterate_items():
                if isinstance(item, TextItem):
                    sections.append(DocumentSection(
                        text=item.text,
                        page=item.prov[0].page_no if item.prov else 1,
                        section_type="heading" if item.label.name.startswith("HEAD") else "text"
                    ))
                elif isinstance(item, TableItem):
                    table_data = item.export_to_list()
                    table_text = item.export_to_markdown()
                    sections.append(DocumentSection(
                        text=table_text,
                        page=item.prov[0].page_no if item.prov else 1,
                        section_type="table",
                        table_data=table_data
                    ))
        except Exception as e:
            logger.error(f"Docling parsing failed for {file_path}: {e}")
            raise
            
        return sections

class PyMuPDFParser(BaseParser):
    def parse(self, file_path: str) -> List[DocumentSection]:
        sections = []
        try:
            doc = fitz.open(file_path)
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                text = page.get_text()
                if text.strip():
                    sections.append(DocumentSection(
                        text=text.strip(),
                        page=page_num + 1,
                        section_type="text"
                    ))
        except Exception as e:
            logger.error(f"PyMuPDF parsing failed for {file_path}: {e}")
            raise
            
        return sections

class OCRParser(BaseParser):
    def parse(self, file_path: str) -> List[DocumentSection]:
        sections = []
        try:
            doc = fitz.open(file_path)
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                pix = page.get_pixmap(dpi=150)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                text = pytesseract.image_to_string(img)
                
                if text.strip():
                    sections.append(DocumentSection(
                        text=text.strip(),
                        page=page_num + 1,
                        section_type="text"
                    ))
        except Exception as e:
            logger.error(f"OCR parsing failed for {file_path}: {e}")
            raise
            
        return sections

class PlainTextParser(BaseParser):
    def parse(self, file_path: str) -> List[DocumentSection]:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            return [DocumentSection(
                text=text.strip(),
                page=1,
                section_type="text"
            )]
        except Exception as e:
            logger.error(f"Text parsing failed for {file_path}: {e}")
            raise

class SpreadsheetParser(BaseParser):
    def parse(self, file_path: str) -> List[DocumentSection]:
        sections = []
        try:
            ext = os.path.splitext(file_path)[1].lower()
            if ext == '.csv':
                df = pd.read_csv(file_path)
                text = f"Columns: {', '.join(df.columns.tolist())}\n" + df.to_string(max_rows=500)
                # Store it as a text chunk for simplicity or a table chunk if we want to parse it as table
                sections.append(DocumentSection(
                    text=text,
                    page=1,
                    section_type="table",
                    table_data=[df.columns.tolist()] + df.values.tolist()
                ))
            elif ext in ['.xlsx', '.xls']:
                xls = pd.ExcelFile(file_path)
                for sheet_name in xls.sheet_names:
                    df = pd.read_excel(xls, sheet_name)
                    text = f"Sheet: {sheet_name}\nColumns: {', '.join(df.columns.tolist())}\n" + df.to_string(max_rows=500)
                    sections.append(DocumentSection(
                        text=text,
                        page=1,
                        section_type="table",
                        table_data=[df.columns.tolist()] + df.values.tolist()
                    ))
        except Exception as e:
            logger.error(f"Spreadsheet parsing failed for {file_path}: {e}")
            raise
        return sections

class DocumentParserFactory:
    @staticmethod
    def parse(file_path: str) -> List[DocumentSection]:
        ext = os.path.splitext(file_path)[1].lower()
        
        if ext in ['.txt', '.md', '.log']:
            return PlainTextParser().parse(file_path)
            
        elif ext in ['.csv', '.xlsx', '.xls']:
            return SpreadsheetParser().parse(file_path)
            
        elif ext in ['.pdf', '.docx', '.pptx']:
            # Try Docling first
            try:
                import docling
                logger.info(f"Attempting Docling parse for {file_path}")
                sections = DoclingParser().parse(file_path)
                if sections:
                    return sections
            except ImportError:
                pass
            except Exception as e:
                logger.warning(f"Docling failed, falling back... {e}")
            
            # Try PyMuPDF for PDFs (or if Docling isn't available)
            if ext == '.pdf':
                try:
                    logger.info(f"Attempting PyMuPDF parse for {file_path}")
                    sections = PyMuPDFParser().parse(file_path)
                    if any(s.text.strip() for s in sections):
                        return sections
                except Exception as e:
                    logger.warning(f"PyMuPDF failed, falling back to OCR... {e}")
                    
                # Finally, OCR
                logger.info(f"Attempting OCR parse for {file_path}")
                return OCRParser().parse(file_path)
                
            raise ValueError(f"Could not parse document {file_path} with available parsers.")
            
        else:
            raise ValueError(f"Unsupported file type: {ext}")
