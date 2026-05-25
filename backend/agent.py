import os
from typing import Annotated, TypedDict, List
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from dotenv import load_dotenv
import time

# Import our tools
from tools.file_tools import (
    create_text_file, read_text_file, modify_text_file,
    create_excel_file, read_excel_file, modify_excel_file,
    create_pdf_file, read_pdf_file, modify_pdf_file
)
from tools.csv_tools import (
    create_csv_file, read_csv_file, modify_csv_file
)
from tools.dataset_tools import (
    analyze_dataset, clean_dataset, transform_dataset, visualize_dataset
)
from tools.chart_tools import (
    generate_bar_chart, generate_line_chart, generate_pie_chart, generate_histogram
)
from tools.vision_tools import analyze_image

load_dotenv()

# ── Token tracking ────────────────────────────────────────────────────────────
# Global, in-memory token usage. Resets on server restart.
token_usage = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "limit": 500_000,       # default limit
    "history": [],          # list of {timestamp, prompt, completion, total}
}

def record_token_usage(response):
    """Extract token counts from a LangChain AIMessage's response_metadata."""
    meta = getattr(response, "response_metadata", {}) or {}
    usage = meta.get("token_usage") or meta.get("usage") or {}
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    total = prompt + completion
    token_usage["prompt_tokens"] += prompt
    token_usage["completion_tokens"] += completion
    token_usage["total_tokens"] += total
    token_usage["history"].append({
        "timestamp": time.time(),
        "prompt": prompt,
        "completion": completion,
        "total": total,
    })
    return {"prompt": prompt, "completion": completion, "total": total}

def check_token_budget():
    """Return True if we are still within budget."""
    return token_usage["total_tokens"] < token_usage["limit"]

# ── Activity log ──────────────────────────────────────────────────────────────
activity_log: list[dict] = []

def log_activity(action: str, detail: str = "", meta: dict | None = None):
    entry = {
        "id": len(activity_log) + 1,
        "timestamp": time.time(),
        "action": action,
        "detail": detail,
        "meta": meta or {},
    }
    activity_log.append(entry)
    return entry

# ── Tools list (NO delete_file — users cannot delete from agent) ─────────────
ALL_TOOLS = [
    create_text_file, read_text_file, modify_text_file,
    create_excel_file, read_excel_file, modify_excel_file,
    create_pdf_file, read_pdf_file, modify_pdf_file,
    create_csv_file, read_csv_file, modify_csv_file,
    analyze_dataset, clean_dataset, transform_dataset, visualize_dataset,
    generate_bar_chart, generate_line_chart, generate_pie_chart, generate_histogram,
    analyze_image
]

TOOL_MAP = {tool.name: tool for tool in ALL_TOOLS}

# ── Agent state ───────────────────────────────────────────────────────────────
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    plan: list
    results: list
    workspace_id: str
    planner_response: object
    token_usage_snapshot: dict

# ── System prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are AgentOS, an expert Data Scientist and AI Assistant built for enterprise document analysis, data transformation, and visualization.

You have access to tools for:
• File operations — create, read, modify text/CSV/Excel/PDF files
• Dataset operations — analyze, clean, transform datasets
• Charting — bar charts, line charts, pie charts, histograms
• Vision — view and interpret chart images

METHODOLOGY (ReAct — Reason + Act):
1. **Thought**: Analyze the user's request carefully. Identify what data is needed and which tools are best suited.
2. **Action**: Select the exact tools needed. Plan the sequence logically.

STEP DESCRIPTIONS (CRITICAL):
When you decide to use tools, your reply text MUST contain a brief, human-readable summary of your plan. Write it like you are explaining to a colleague what you are about to do, NOT like a system log. For example:
- GOOD: "I'll read your sales data, identify the key revenue columns, and create a bar chart comparing revenue by product."
- BAD: "Execute read_csv_file, then execute generate_bar_chart."
Be conversational, clear, and explain the WHY behind each step.

CRITICAL RULES:
- If the user asks for a summary or explanation of a chart/image, use the `analyze_image` tool.
- If you can answer from conversation history alone (no data operations needed), reply directly with NO tool calls.
- When creating, reading, or modifying files, use the specific file tools (e.g. `create_excel_file`, `read_csv_file`).
- For programmatic data analysis, cleaning, or transformation, use the `*_dataset` tools.
- For charts, use `generate_bar_chart`, `generate_line_chart`, `generate_pie_chart`, or `generate_histogram`.
- All file operations are relative to the workspace — provide ONLY the filename (e.g. 'data.xlsx').
- Give output files descriptive names (e.g. 'financial_analysis_chart.png', 'cleaned_data.csv').
- Be professional, concise, and analytical.
- When summarizing data or reports, be thorough: mention key findings, notable trends, and actionable insights.
- When the user asks about specific data, determine which workspace file is most relevant based on the file list provided.
"""

def _get_workspace_files(workspace_id: str) -> list[str]:
    """List all files in the workspace."""
    workspace_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspaces", workspace_id)
    if os.path.exists(workspace_path):
        return [f for f in os.listdir(workspace_path) if os.path.isfile(os.path.join(workspace_path, f))]
    return []

def _humanize_tool_call(tool_name: str, args: dict) -> str:
    """Generate a human-readable description from a tool call."""
    filename = args.get("filename", "")
    labels = {
        "read_text_file": f"Read and review the contents of '{filename}'",
        "create_text_file": f"Create a new text file '{filename}'",
        "modify_text_file": f"Update the contents of '{filename}'",
        "read_csv_file": f"Read and analyze the CSV data in '{filename}'",
        "create_csv_file": f"Create a new CSV file '{filename}'",
        "modify_csv_file": f"Update the CSV data in '{filename}'",
        "read_excel_file": f"Read and analyze the Excel spreadsheet '{filename}'",
        "create_excel_file": f"Create a new Excel spreadsheet '{filename}'",
        "modify_excel_file": f"Update the Excel spreadsheet '{filename}'",
        "read_pdf_file": f"Read and extract text from the PDF '{filename}'",
        "create_pdf_file": f"Generate a new PDF document '{filename}'",
        "modify_pdf_file": f"Update the PDF document '{filename}'",
        "analyze_dataset": f"Analyze the dataset in '{filename}' for key statistics and patterns",
        "clean_dataset": f"Clean the dataset in '{filename}' by handling missing values and duplicates",
        "transform_dataset": f"Transform and reshape the data in '{filename}'",
        "visualize_dataset": f"Create a visualization from the data in '{filename}'",
        "generate_bar_chart": f"Generate a bar chart from '{filename}'",
        "generate_line_chart": f"Generate a line chart from '{filename}'",
        "generate_pie_chart": f"Generate a pie chart from '{filename}'",
        "generate_histogram": f"Generate a histogram from '{filename}'",
        "analyze_image": f"Analyze and interpret the image '{filename}'",
    }
    return labels.get(tool_name, f"Process '{filename}' using {tool_name.replace('_', ' ')}")

# ── Graph nodes ───────────────────────────────────────────────────────────────
def planner_node(state: AgentState):
    if not check_token_budget():
        return {
            "plan": [],
            "planner_response": None,
            "messages": [AIMessage(content="⚠️ Token budget exhausted. Please increase your token limit in Settings to continue.")],
            "token_usage_snapshot": {
                "used": token_usage["total_tokens"],
                "limit": token_usage["limit"],
            }
        }

    llm = ChatOpenAI(model="gpt-5.4-mini", temperature=0).bind_tools(ALL_TOOLS)
    
    # Inject workspace file context
    workspace_files = _get_workspace_files(state.get("workspace_id", "default-workspace"))
    file_context = ""
    if workspace_files:
        file_context = f"\n\nFiles currently in the workspace: {', '.join(workspace_files)}\nUse these filenames when the user refers to data — match the most relevant file to their request."
    
    system_prompt = SystemMessage(content=SYSTEM_PROMPT + file_context)
    messages = [system_prompt] + state["messages"]
    response = llm.invoke(messages)
    
    # Record token usage
    usage_this_call = record_token_usage(response)
    log_activity("agent_call", "Planner LLM call", usage_this_call)
    
    plan = []
    if hasattr(response, "tool_calls") and response.tool_calls:
        for tc in response.tool_calls:
            plan.append({
                "id": tc["id"],
                "tool": tc["name"],
                "args": tc["args"],
                "description": tc["args"].get("description", "") or _humanize_tool_call(tc["name"], tc["args"])
            })
    
    updates = {
        "plan": plan,
        "planner_response": response,
        "token_usage_snapshot": {
            "used": token_usage["total_tokens"],
            "limit": token_usage["limit"],
            "this_call": usage_this_call,
        }
    }
    if not plan:
        updates["messages"] = [response]
    return updates

def route_after_planner(state: AgentState):
    if state.get("plan"):
        return "executor"
    return END

def executor_node(state: AgentState):
    results = []
    new_messages = []
    
    planner_resp = state.get("planner_response")
    if planner_resp:
        new_messages.append(planner_resp)
    
    workspace_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspaces", state.get("workspace_id", "default-workspace"))
    os.makedirs(workspace_path, exist_ok=True)
    
    for step in state.get("plan", []):
        tool_name = step.get("tool")
        tool_func = TOOL_MAP.get(tool_name)
        tool_call_id = step.get("id", "custom")
        
        if tool_func:
            try:
                args = dict(step.get("args", {}))
                args["workspace_id"] = state["workspace_id"]
                res = tool_func.invoke(args)
                
                if isinstance(res, str) and res.startswith("IMAGE_REQUEST:"):
                    filename = res.split(":", 1)[1]
                    path = os.path.join(workspace_path, filename)
                    if os.path.exists(path):
                        import base64
                        with open(path, "rb") as f:
                            base64_image = base64.b64encode(f.read()).decode('utf-8')
                        new_messages.append(ToolMessage(content=f"Successfully loaded image {filename}", tool_call_id=tool_call_id))
                        
                        mime_type = "image/png"
                        if filename.lower().endswith((".jpg", ".jpeg")):
                            mime_type = "image/jpeg"
                        
                        new_messages.append(HumanMessage(content=[
                            {"type": "text", "text": f"System: Here is the image {filename} you requested to view:"},
                            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}}
                        ]))
                        results.append({"step_id": tool_call_id, "tool": tool_name, "status": "success", "output": f"Image {filename} loaded into context."})
                        log_activity("tool_exec", f"Loaded image: {filename}")
                    else:
                        new_messages.append(ToolMessage(content="Error: Image file not found.", tool_call_id=tool_call_id))
                        results.append({"step_id": tool_call_id, "tool": tool_name, "status": "error", "output": f"Image file '{filename}' not found."})
                else:
                    out_str = str(res)
                    new_messages.append(ToolMessage(content=out_str, tool_call_id=tool_call_id))
                    results.append({"step_id": tool_call_id, "tool": tool_name, "status": "success", "output": out_str})
                    log_activity("tool_exec", f"Executed {tool_name}", {"output_preview": out_str[:200]})
            except Exception as e:
                new_messages.append(ToolMessage(content=f"Error: {str(e)}", tool_call_id=tool_call_id))
                results.append({"step_id": tool_call_id, "tool": tool_name, "status": "error", "output": str(e)})
                log_activity("tool_error", f"Error in {tool_name}: {str(e)}")
        else:
            new_messages.append(ToolMessage(content=f"Error: Tool {tool_name} not found", tool_call_id=tool_call_id))
            results.append({"step_id": tool_call_id, "tool": tool_name, "status": "error", "output": f"Tool {tool_name} not found"})
            
    return {"results": results, "messages": new_messages, "plan": []}

# ── Build graph ───────────────────────────────────────────────────────────────
workflow = StateGraph(AgentState)
workflow.add_node("planner", planner_node)
workflow.add_node("executor", executor_node)

workflow.add_edge(START, "planner")
workflow.add_conditional_edges("planner", route_after_planner)
workflow.add_edge("executor", "planner")

memory = MemorySaver()
graph = workflow.compile(checkpointer=memory, interrupt_before=["executor"])
