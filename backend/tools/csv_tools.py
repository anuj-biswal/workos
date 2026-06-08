import os
import csv
import json

from langchain_core.tools import tool

def get_file_path(workspace_id: str, filename: str) -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "workspaces", workspace_id, filename)

@tool
def create_csv_file(filename: str, data: list[dict], workspace_id: str = "default-workspace") -> str:
    """Create a CSV file from a list of dictionaries (rows)."""
    try:
        import pandas as pd
        path = get_file_path(workspace_id, filename)
        df = pd.DataFrame(data)
        df.to_csv(path, index=False)
        return f"Successfully created CSV file {filename}"
    except Exception as e:
        return f"Error creating CSV file: {e}"

@tool
def read_csv_file(filename: str, workspace_id: str = "default-workspace") -> str:
    """Read a CSV file and return a string representation of the data."""
    try:
        import pandas as pd
        path = get_file_path(workspace_id, filename)
        df = pd.read_csv(path)
        return df.to_json(orient='records')
    except Exception as e:
        return f"Error reading CSV file: {e}"

@tool
def modify_csv_file(filename: str, data: list[dict], workspace_id: str = "default-workspace") -> str:
    """Overwrite a CSV file with a new list of dictionaries."""
    return create_csv_file.invoke({"filename": filename, "data": data, "workspace_id": workspace_id})
