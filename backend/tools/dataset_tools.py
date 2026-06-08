import os
import json

from langchain_core.tools import tool

def get_file_path(workspace_id: str, filename: str) -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "workspaces", workspace_id, filename)

@tool
def analyze_dataset(filename: str, workspace_id: str = "default-workspace") -> str:
    """Programmatically analyze a dataset file (Excel or CSV) and return summary statistics. Do NOT use this tool if the user asks you to explain or summarize a chart, or for general conversational queries."""
    try:
        import pandas as pd
        path = get_file_path(workspace_id, filename)
        if filename.endswith(".csv"):
            df = pd.read_csv(path)
        else:
            df = pd.read_excel(path)
        
        info = {
            "columns": list(df.columns),
            "shape": df.shape,
            "dtypes": df.dtypes.astype(str).to_dict(),
            "summary": df.describe(include='all').to_json()
        }
        return json.dumps(info)
    except Exception as e:
        return f"Error analyzing dataset: {e}"

@tool
def clean_dataset(filename: str, output_filename: str = None, drop_na: bool = True, drop_duplicates: bool = True, workspace_id: str = "default-workspace") -> str:
    """Clean a dataset by dropping NAs and duplicates, then save it."""
    try:
        import pandas as pd
        path = get_file_path(workspace_id, filename)
        if filename.endswith(".csv"):
            df = pd.read_csv(path)
        else:
            df = pd.read_excel(path)
            
        if drop_na:
            df = df.dropna()
        if drop_duplicates:
            df = df.drop_duplicates()
            
        out_name = output_filename or filename
        out_path = get_file_path(workspace_id, out_name)
        
        if out_name.endswith(".csv"):
            df.to_csv(out_path, index=False)
        else:
            df.to_excel(out_path, index=False)
            
        return f"Successfully cleaned dataset and saved to {out_name}"
    except Exception as e:
        return f"Error cleaning dataset: {e}"

@tool
def transform_dataset(filename: str, operations: list[dict], output_filename: str = None, workspace_id: str = "default-workspace") -> str:
    """
    Transform a dataset. 
    operations format: [{"type": "rename", "columns": {"old": "new"}}, {"type": "filter", "query": "age > 30"}]
    """
    try:
        import pandas as pd
        path = get_file_path(workspace_id, filename)
        if filename.endswith(".csv"):
            df = pd.read_csv(path)
        else:
            df = pd.read_excel(path)
            
        for op in operations:
            if op["type"] == "rename":
                df = df.rename(columns=op.get("columns", {}))
            elif op["type"] == "filter":
                df = df.query(op.get("query", ""))
            elif op["type"] == "drop":
                df = df.drop(columns=op.get("columns", []))
                
        out_name = output_filename or filename
        out_path = get_file_path(workspace_id, out_name)
        
        if out_name.endswith(".csv"):
            df.to_csv(out_path, index=False)
        else:
            df.to_excel(out_path, index=False)
            
        return f"Successfully transformed dataset and saved to {out_name}"
    except Exception as e:
        return f"Error transforming dataset: {e}"

@tool
def visualize_dataset(filename: str, workspace_id: str = "default-workspace") -> str:
    """Generic tool to check if a dataset can be visualized. (Use specific chart tools to actually generate)."""
    return analyze_dataset.invoke({"filename": filename, "workspace_id": workspace_id})
