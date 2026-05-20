import os
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend for server use
import matplotlib.pyplot as plt
import seaborn as sns
from langchain_core.tools import tool

def get_file_path(workspace_id: str, filename: str) -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "workspaces", workspace_id, filename)

def _load_dataframe(workspace_id: str, dataset_filename: str) -> pd.DataFrame:
    """Helper to load a dataset from workspace."""
    path = get_file_path(workspace_id, dataset_filename)
    if dataset_filename.endswith(".csv"):
        return pd.read_csv(path)
    else:
        return pd.read_excel(path)

@tool
def generate_bar_chart(dataset_filename: str, x_column: str, y_column: str, output_filename: str, title: str = "", workspace_id: str = "default-workspace") -> str:
    """Generate a bar chart from a dataset and save it as an image (e.g. .png)."""
    try:
        df = _load_dataframe(workspace_id, dataset_filename)
        plt.figure(figsize=(10, 6))
        sns.barplot(data=df, x=x_column, y=y_column, palette="viridis")
        plt.title(title or f"{y_column} by {x_column}", fontsize=14, fontweight='bold')
        plt.xlabel(x_column, fontsize=12)
        plt.ylabel(y_column, fontsize=12)
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()
        
        out_path = get_file_path(workspace_id, output_filename)
        plt.savefig(out_path, dpi=150, bbox_inches='tight')
        plt.close()
        
        return f"Successfully generated bar chart: {output_filename}"
    except Exception as e:
        return f"Error generating bar chart: {e}"

@tool
def generate_line_chart(dataset_filename: str, x_column: str, y_column: str, output_filename: str, title: str = "", workspace_id: str = "default-workspace") -> str:
    """Generate a line chart from a dataset and save it as an image."""
    try:
        df = _load_dataframe(workspace_id, dataset_filename)
        plt.figure(figsize=(10, 6))
        sns.lineplot(data=df, x=x_column, y=y_column, marker='o', linewidth=2)
        plt.title(title or f"{y_column} over {x_column}", fontsize=14, fontweight='bold')
        plt.xlabel(x_column, fontsize=12)
        plt.ylabel(y_column, fontsize=12)
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        
        out_path = get_file_path(workspace_id, output_filename)
        plt.savefig(out_path, dpi=150, bbox_inches='tight')
        plt.close()
        
        return f"Successfully generated line chart: {output_filename}"
    except Exception as e:
        return f"Error generating line chart: {e}"

@tool
def generate_pie_chart(dataset_filename: str, category_column: str, value_column: str, output_filename: str, title: str = "", workspace_id: str = "default-workspace") -> str:
    """Generate a pie chart from a dataset and save it as an image."""
    try:
        df = _load_dataframe(workspace_id, dataset_filename)
        plt.figure(figsize=(8, 8))
        df_grouped = df.groupby(category_column)[value_column].sum()
        colors = sns.color_palette("Set2", len(df_grouped))
        plt.pie(df_grouped, labels=df_grouped.index, autopct='%1.1f%%', startangle=90, colors=colors)
        plt.title(title or f"{value_column} Distribution by {category_column}", fontsize=14, fontweight='bold')
        plt.tight_layout()
            
        out_path = get_file_path(workspace_id, output_filename)
        plt.savefig(out_path, dpi=150, bbox_inches='tight')
        plt.close()
        
        return f"Successfully generated pie chart: {output_filename}"
    except Exception as e:
        return f"Error generating pie chart: {e}"

@tool
def generate_histogram(dataset_filename: str, column: str, output_filename: str, bins: int = 20, title: str = "", workspace_id: str = "default-workspace") -> str:
    """Generate a histogram for a single numeric column from a dataset and save it as an image."""
    try:
        df = _load_dataframe(workspace_id, dataset_filename)
        plt.figure(figsize=(10, 6))
        sns.histplot(data=df, x=column, bins=bins, kde=True, color="#6366f1")
        plt.title(title or f"Distribution of {column}", fontsize=14, fontweight='bold')
        plt.xlabel(column, fontsize=12)
        plt.ylabel("Frequency", fontsize=12)
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        
        out_path = get_file_path(workspace_id, output_filename)
        plt.savefig(out_path, dpi=150, bbox_inches='tight')
        plt.close()
        
        return f"Successfully generated histogram: {output_filename}"
    except Exception as e:
        return f"Error generating histogram: {e}"
