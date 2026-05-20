import os
import base64
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

def get_file_path(workspace_id: str, filename: str) -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "workspaces", workspace_id, filename)

@tool
def analyze_image(filename: str, workspace_id: str = "default-workspace") -> str:
    """Use this tool to view a chart or image file. The image will be loaded into your context so you can summarize or answer questions about it."""
    # The actual image loading is intercepted and handled by executor_node
    # to inject the image directly into the main agent's message history.
    return f"IMAGE_REQUEST:{filename}"
