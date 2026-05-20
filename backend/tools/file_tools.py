import os
import pandas as pd
from langchain_core.tools import tool

def get_file_path(workspace_id: str, filename: str) -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "workspaces", workspace_id, filename)

@tool
def create_text_file(filename: str, content: str, workspace_id: str = "default-workspace") -> str:
    """Create a text file with the given content."""
    try:
        path = get_file_path(workspace_id, filename)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Successfully created text file {filename}"
    except Exception as e:
        return f"Error creating text file: {e}"

@tool
def read_text_file(filename: str, workspace_id: str = "default-workspace") -> str:
    """Read contents of a text file."""
    try:
        path = get_file_path(workspace_id, filename)
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error reading text file: {e}"

@tool
def modify_text_file(filename: str, content: str, workspace_id: str = "default-workspace") -> str:
    """Overwrite a text file with new content."""
    return create_text_file.invoke({"filename": filename, "content": content, "workspace_id": workspace_id})

@tool
def delete_file(filename: str, workspace_id: str = "default-workspace") -> str:
    """Delete a file from the workspace."""
    try:
        path = get_file_path(workspace_id, filename)
        if os.path.exists(path):
            os.remove(path)
            return f"Successfully deleted {filename}"
        return f"File {filename} not found."
    except Exception as e:
        return f"Error deleting file: {e}"

@tool
def create_excel_file(filename: str, data: list[dict], workspace_id: str = "default-workspace") -> str:
    """Create an Excel file from a list of dictionaries (rows)."""
    try:
        path = get_file_path(workspace_id, filename)
        df = pd.DataFrame(data)
        df.to_excel(path, index=False)
        return f"Successfully created Excel file {filename}"
    except Exception as e:
        return f"Error creating Excel file: {e}"

@tool
def read_excel_file(filename: str, workspace_id: str = "default-workspace") -> str:
    """Read an Excel file and return a string representation of the data."""
    try:
        path = get_file_path(workspace_id, filename)
        df = pd.read_excel(path)
        return df.to_json(orient='records')
    except Exception as e:
        return f"Error reading Excel file: {e}"

@tool
def modify_excel_file(filename: str, data: list[dict], workspace_id: str = "default-workspace") -> str:
    """Overwrite an Excel file with a new list of dictionaries."""
    return create_excel_file.invoke({"filename": filename, "data": data, "workspace_id": workspace_id})

@tool
def create_pdf_file(filename: str, content: str, workspace_id: str = "default-workspace") -> str:
    """Create a PDF file with the given text content using FPDF or ReportLab."""
    try:
        path = get_file_path(workspace_id, filename)
        # Attempt to use fpdf
        try:
            from fpdf import FPDF
            pdf = FPDF()
            pdf.add_page()
            pdf.set_font("Arial", size=12)
            pdf.multi_cell(0, 10, txt=content)
            pdf.output(path)
            return f"Successfully created PDF {filename}"
        except ImportError:
            # Fallback to reportlab
            from reportlab.pdfgen import canvas
            c = canvas.Canvas(path)
            c.drawString(100, 750, content) # very basic fallback
            c.save()
            return f"Successfully created PDF {filename} using reportlab fallback"
    except Exception as e:
        return f"Error creating PDF file: {e}"

@tool
def read_pdf_file(filename: str, workspace_id: str = "default-workspace") -> str:
    """Read text from a PDF file."""
    try:
        path = get_file_path(workspace_id, filename)
        import PyPDF2
        with open(path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
            return text
    except Exception as e:
        return f"Error reading PDF: {e}"

@tool
def modify_pdf_file(filename: str, content: str, workspace_id: str = "default-workspace") -> str:
    """Overwrite a PDF file with new text content."""
    return create_pdf_file.invoke({"filename": filename, "content": content, "workspace_id": workspace_id})
