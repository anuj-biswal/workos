from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import os
import shutil
import uuid
import time
import uvicorn
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pydantic import BaseModel
from typing import List, Optional

# Thread pool for blocking LangGraph / LLM calls
_EXECUTOR = ThreadPoolExecutor(max_workers=4)

app = FastAPI(title="AgentOS — Agentic AI Platform")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_WORKSPACE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspaces")
os.makedirs(BASE_WORKSPACE_DIR, exist_ok=True)

# ── Rate limiter (in-memory sliding window) ──────────────────────────────────
_rate_buckets: dict[str, list[float]] = {}
RATE_LIMIT = 30   # max requests per minute
RATE_WINDOW = 60  # seconds

def check_rate_limit(client_id: str = "global") -> bool:
    now = time.time()
    bucket = _rate_buckets.setdefault(client_id, [])
    # Prune old entries
    _rate_buckets[client_id] = [t for t in bucket if now - t < RATE_WINDOW]
    if len(_rate_buckets[client_id]) >= RATE_LIMIT:
        return False
    _rate_buckets[client_id].append(now)
    return True

# ── In-memory task scheduler ─────────────────────────────────────────────────
_tasks: list[dict] = []
_task_counter = 0

class TaskCreate(BaseModel):
    title: str
    description: str = ""
    date: str  # YYYY-MM-DD
    priority: str = "medium"  # low, medium, high

class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    date: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None  # todo, in-progress, done

# ── Pydantic models ──────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    workspace_id: str

class PlanApproval(BaseModel):
    plan_steps: List[dict]
    workspace_id: str

class TokenLimitUpdate(BaseModel):
    limit: int

# ── File upload ───────────────────────────────────────────────────────────────
@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), workspace_id: Optional[str] = Form(None)):
    if not workspace_id:
        workspace_id = "default-workspace"
        
    workspace_path = os.path.join(BASE_WORKSPACE_DIR, workspace_id)
    os.makedirs(workspace_path, exist_ok=True)
    
    file_path = os.path.join(workspace_path, file.filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    file_size = os.path.getsize(file_path)
    
    from agent import log_activity
    log_activity("file_upload", f"Uploaded {file.filename}", {"size": file_size})
    
    return {"workspace_id": workspace_id, "filename": file.filename, "status": "uploaded", "size": file_size}

# ── Chat endpoint ─────────────────────────────────────────────────────────────
from agent import graph, token_usage, activity_log, log_activity
from langchain_core.messages import HumanMessage

@app.post("/api/chat")
async def chat(req: ChatRequest):
    if not check_rate_limit():
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Please wait a moment before sending another request.")
    
    config = {"configurable": {"thread_id": req.workspace_id}}
    
    def _run_planner():
        initial_state = {"messages": [HumanMessage(content=req.message)], "workspace_id": req.workspace_id}
        for _ in graph.stream(initial_state, config):
            pass
        current_state = graph.get_state(config)
        plan = current_state.values.get("plan", [])
        messages = current_state.values.get("messages", [])
        last_message_content = messages[-1].content if messages else ""
        token_snap = current_state.values.get("token_usage_snapshot", {})
        return plan, last_message_content, token_snap

    try:
        loop = asyncio.get_event_loop()
        plan, last_message_content, token_snap = await loop.run_in_executor(_EXECUTOR, _run_planner)
        
        log_activity("chat", f"User: {req.message[:80]}...")
        
        response = {
            "token_usage": {
                "used": token_usage["total_tokens"],
                "limit": token_usage["limit"],
                "remaining": max(0, token_usage["limit"] - token_usage["total_tokens"]),
                "this_call": token_snap.get("this_call", {}),
            }
        }
        
        if not plan:
            response["reply"] = last_message_content or "I don't need to use any tools for that."
        else:
            response["reply"] = last_message_content or "I have analyzed your request. Here is my proposed execution plan:"
            response["plan"] = plan
        
        return response
    except Exception as e:
        try:
            graph.update_state(config, {"messages": [], "plan": [], "results": [], "workspace_id": req.workspace_id})
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Agent error (session reset): {str(e)}")

# ── Execute plan ──────────────────────────────────────────────────────────────
@app.post("/api/execute")
async def execute_plan(req: PlanApproval):
    if not check_rate_limit():
        raise HTTPException(status_code=429, detail="Rate limit exceeded.")
    
    config = {"configurable": {"thread_id": req.workspace_id}}

    def _run_executor():
        graph.update_state(config, {"plan": req.plan_steps})
        for _ in graph.stream(None, config):
            pass
        current_state = graph.get_state(config)
        messages = current_state.values.get("messages", [])
        last_message_content = ""
        from langchain_core.messages import AIMessage
        if messages and isinstance(messages[-1], AIMessage) and not getattr(messages[-1], "tool_calls", None):
            last_message_content = messages[-1].content
            
        return current_state.values.get("results", []), last_message_content

    try:
        loop = asyncio.get_event_loop()
        results, reply = await asyncio.wait_for(
            loop.run_in_executor(_EXECUTOR, _run_executor),
            timeout=120.0
        )
        log_activity("execution", f"Executed {len(req.plan_steps)} steps", {"results_count": len(results)})
        
        return {
            "status": "completed",
            "results": results,
            "reply": reply,
            "token_usage": {
                "used": token_usage["total_tokens"],
                "limit": token_usage["limit"],
                "remaining": max(0, token_usage["limit"] - token_usage["total_tokens"]),
            }
        }
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Execution timed out after 120 seconds.")
    except Exception as e:
        try:
            graph.update_state(config, {"messages": [], "plan": [], "results": [], "workspace_id": req.workspace_id})
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Execution error (session reset): {str(e)}")

# ── Workspace files ───────────────────────────────────────────────────────────
@app.get("/api/workspace/{workspace_id}/files")
async def list_files(workspace_id: str):
    workspace_path = os.path.join(BASE_WORKSPACE_DIR, workspace_id)
    if not os.path.exists(workspace_path):
        return {"files": []}
    
    files = []
    for fname in os.listdir(workspace_path):
        fpath = os.path.join(workspace_path, fname)
        if os.path.isfile(fpath):
            stat = os.stat(fpath)
            files.append({
                "name": fname,
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "type": _get_file_type(fname),
            })
    return {"files": files}

def _get_file_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    type_map = {
        "xlsx": "excel", "xls": "excel",
        "csv": "csv",
        "pdf": "pdf",
        "txt": "text", "md": "text", "log": "text",
        "png": "image", "jpg": "image", "jpeg": "image", "gif": "image", "svg": "image",
        "doc": "word", "docx": "word",
    }
    return type_map.get(ext, "other")

@app.get("/api/workspace/{workspace_id}/download/{filename}")
async def download_file(workspace_id: str, filename: str):
    file_path = os.path.join(BASE_WORKSPACE_DIR, workspace_id, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, filename=filename)

@app.get("/api/workspace/{workspace_id}/preview/{filename}")
async def preview_file(workspace_id: str, filename: str):
    """Return file content for inline preview."""
    file_path = os.path.join(BASE_WORKSPACE_DIR, workspace_id, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    ftype = _get_file_type(filename)
    
    try:
        if ftype == "text":
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(50_000)  # cap at 50KB
            return {"type": "text", "content": content}
        
        elif ftype == "csv":
            import pandas as pd
            df = pd.read_csv(file_path, nrows=100).fillna("")
            return {"type": "table", "content": df.to_dict(orient="records")}
        
        elif ftype == "excel":
            import pandas as pd
            df = pd.read_excel(file_path, nrows=100).fillna("")
            return {"type": "table", "content": df.to_dict(orient="records")}
        
        elif ftype == "image":
            import base64
            with open(file_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            ext = filename.rsplit(".", 1)[-1].lower()
            mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "svg": "image/svg+xml"}.get(ext, "image/png")
            return {"type": "image", "content": f"data:{mime};base64,{b64}"}
        
        elif ftype == "pdf":
            import PyPDF2
            with open(file_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                text = ""
                for page in reader.pages[:5]:
                    text += page.extract_text() + "\n"
            return {"type": "text", "content": text}
        
        else:
            return {"type": "unsupported", "content": "Preview not available for this file type."}
    except Exception as e:
        return {"type": "error", "content": str(e)}

# File deletion protection — always return 403
@app.delete("/api/workspace/{workspace_id}/files/{filename}")
async def delete_file_protected(workspace_id: str, filename: str):
    raise HTTPException(status_code=403, detail="File deletion is disabled. Uploaded and generated files are protected.")

# ── Token tracking endpoints ─────────────────────────────────────────────────
@app.get("/api/tokens")
async def get_tokens():
    return {
        "prompt_tokens": token_usage["prompt_tokens"],
        "completion_tokens": token_usage["completion_tokens"],
        "total_tokens": token_usage["total_tokens"],
        "limit": token_usage["limit"],
        "remaining": max(0, token_usage["limit"] - token_usage["total_tokens"]),
        "history": token_usage["history"][-50:],  # last 50 entries
    }

@app.post("/api/tokens/limit")
async def set_token_limit(req: TokenLimitUpdate):
    if req.limit < 1000:
        raise HTTPException(status_code=400, detail="Minimum token limit is 1,000")
    token_usage["limit"] = req.limit
    log_activity("settings", f"Token limit updated to {req.limit:,}")
    return {"limit": token_usage["limit"], "used": token_usage["total_tokens"]}

# ── Activity log ──────────────────────────────────────────────────────────────
@app.get("/api/activity")
async def get_activity():
    return {"activities": activity_log[-50:]}  # last 50

# ── Task scheduler CRUD ──────────────────────────────────────────────────────
@app.get("/api/tasks")
async def get_tasks():
    return {"tasks": _tasks}

@app.post("/api/tasks")
async def create_task(task: TaskCreate):
    global _task_counter
    _task_counter += 1
    new_task = {
        "id": _task_counter,
        "title": task.title,
        "description": task.description,
        "date": task.date,
        "priority": task.priority,
        "status": "todo",
        "created_at": time.time(),
    }
    _tasks.append(new_task)
    log_activity("task_created", f"Task: {task.title}")
    return new_task

@app.put("/api/tasks/{task_id}")
async def update_task(task_id: int, update: TaskUpdate):
    for task in _tasks:
        if task["id"] == task_id:
            if update.title is not None: task["title"] = update.title
            if update.description is not None: task["description"] = update.description
            if update.date is not None: task["date"] = update.date
            if update.priority is not None: task["priority"] = update.priority
            if update.status is not None: task["status"] = update.status
            log_activity("task_updated", f"Task #{task_id}: {task['title']}")
            return task
    raise HTTPException(status_code=404, detail="Task not found")

@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: int):
    global _tasks
    before = len(_tasks)
    _tasks = [t for t in _tasks if t["id"] != task_id]
    if len(_tasks) < before:
        log_activity("task_deleted", f"Task #{task_id}")
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Task not found")

# ── Mount static frontend ────────────────────────────────────────────────────
os.makedirs("static", exist_ok=True)
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, reload_excludes=["workspaces/*", "scratch/*", "*.png", "*.csv", "*.txt", "*.xlsx", "*.pdf"])
