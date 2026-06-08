import sys
# Standard workaround for ChromaDB sqlite3 version requirements on Linux/Render
try:
    if sys.platform != 'win32':
        __import__('pysqlite3')
        sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ImportError:
    pass

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
_EXECUTOR = ThreadPoolExecutor(max_workers=8)
# Thread pool specifically for heavy background tasks (like Docling parsing)
_INGEST_EXECUTOR = ThreadPoolExecutor(max_workers=2)

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

# ── RAG Engine initialization ────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

from rag.rag_engine import RAGEngine
from rag.evaluator import RAGEvaluator
from tools.rag_tools import set_rag_engine, get_last_diagnostics, get_last_context_chunks, get_search_history
import logging

VECTORSTORE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vectorstore")
rag_engine = RAGEngine(persist_dir=VECTORSTORE_DIR)
set_rag_engine(rag_engine)
rag_evaluator = RAGEvaluator(model="gpt-4o-mini")
logging.info("RAG engine + evaluator initialized")

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

class ChunkUpdate(BaseModel):
    text: str

class RAGQueryRequest(BaseModel):
    query: str
    workspace_id: str = "default-workspace"
    expand_query: bool = True
    use_reranker: bool = True

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
    
    # Auto-index the file for RAG search (run in background thread)
    try:
        loop = asyncio.get_event_loop()
        loop.run_in_executor(
            _INGEST_EXECUTOR,
            lambda: rag_engine.ingest_file(workspace_id, file.filename, file_path)
        )
        log_activity("rag_index", f"Indexing {file.filename} for semantic search")
    except Exception as e:
        logging.warning(f"RAG indexing failed for {file.filename}: {e}")
    
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
        from tools.rag_tools import clear_turn_context
        clear_turn_context()
        initial_state = {"messages": [HumanMessage(content=req.message)], "workspace_id": req.workspace_id}
        for _ in graph.stream(initial_state, config):
            pass
        current_state = graph.get_state(config)
        plan = current_state.values.get("plan", [])
        messages = current_state.values.get("messages", [])
        
        last_message_content = ""
        from langchain_core.messages import AIMessage
        if not plan and messages and isinstance(messages[-1], AIMessage):
            last_message_content = messages[-1].content
            
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
        from tools.rag_tools import clear_turn_context
        clear_turn_context()
        graph.update_state(config, {"plan": req.plan_steps})
        all_results = []
        
        max_loops = 5
        for _ in range(max_loops):
            for _ in graph.stream(None, config):
                pass
            current_state = graph.get_state(config)
            
            # Accumulate results from this iteration
            iteration_results = current_state.values.get("results", [])
            all_results.extend(iteration_results)
            
            # If the graph has reached END, it will have no next nodes
            if not current_state.next:
                break
                
        messages = current_state.values.get("messages", [])
        last_message_content = ""
        from langchain_core.messages import AIMessage
        if messages and isinstance(messages[-1], AIMessage) and not getattr(messages[-1], "tool_calls", None):
            last_message_content = messages[-1].content
            
        return all_results, last_message_content

    try:
        loop = asyncio.get_event_loop()
        results, reply = await asyncio.wait_for(
            loop.run_in_executor(_EXECUTOR, _run_executor),
            timeout=120.0
        )
        log_activity("execution", f"Executed {len(req.plan_steps)} steps", {"results_count": len(results)})
        
        # Build debug data if RAG search was used
        debug_data = None
        rag_diagnostics = get_last_diagnostics()
        if rag_diagnostics and rag_diagnostics.get("query"):
            debug_data = {
                "rag_diagnostics": rag_diagnostics,
                "per_step_timing": [{"step_id": r.get("step_id"), "tool": r.get("tool"), "duration_ms": r.get("duration_ms", 0)} for r in results],
                "errors_encountered": [{"step_id": r.get("step_id"), "tool": r.get("tool"), "error_type": r.get("error_type"), "retries": r.get("retries", 0)} for r in results if r.get("status") == "error"],
            }
            
            # Run LLM-as-judge evaluation if we have context and a reply
            context_chunks = get_last_context_chunks()
            if reply and context_chunks:
                try:
                    eval_result = rag_evaluator.evaluate(
                        question=rag_diagnostics.get("query", ""),
                        answer=reply,
                        context_chunks=context_chunks,
                    )
                    debug_data["llm_evaluation"] = eval_result
                except Exception as eval_err:
                    logging.warning(f"LLM evaluation failed: {eval_err}")
                    debug_data["llm_evaluation"] = {"error": str(eval_err)}
        
        response = {
            "status": "completed",
            "results": results,
            "reply": reply,
            "token_usage": {
                "used": token_usage["total_tokens"],
                "limit": token_usage["limit"],
                "remaining": max(0, token_usage["limit"] - token_usage["total_tokens"]),
            }
        }
        if debug_data:
            response["debug"] = debug_data
        
        return response
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
async def preview_file(workspace_id: str, filename: str, highlight: Optional[str] = None):
    """Return file content for inline preview."""
    file_path = os.path.join(BASE_WORKSPACE_DIR, workspace_id, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    ftype = _get_file_type(filename)
    
    try:
        if ftype == "text":
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(50_000)  # cap at 50KB
            
            if highlight:
                import html
                content_esc = html.escape(content)
                highlight_esc = html.escape(highlight)
                if highlight_esc in content_esc:
                    content_esc = content_esc.replace(highlight_esc, f'<mark class="highlight-chunk" style="background-color: rgba(250, 204, 21, 0.4); padding: 2px; border-radius: 2px;">{highlight_esc}</mark>')
                else:
                    lines = [l.strip() for l in highlight_esc.split('\n') if len(l.strip()) > 4]
                    for l in lines:
                        if l in content_esc:
                            content_esc = content_esc.replace(l, f'<mark class="highlight-chunk" style="background-color: rgba(250, 204, 21, 0.4); padding: 2px; border-radius: 2px;">{l}</mark>')
                return {"type": "html", "content": f"<pre style='font-size: 0.8rem; white-space: pre-wrap; word-break: break-all;'>{content_esc}</pre>"}
                
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
            try:
                import pdfplumber
                with pdfplumber.open(file_path) as pdf:
                    text = ""
                    for page in pdf.pages[:5]:
                        text += (page.extract_text() or "") + "\n"
            except ImportError:
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

# ── RAG endpoints ─────────────────────────────────────────────────────────────
@app.get("/api/workspace/{workspace_id}/rag/chunks")
def rag_get_chunks(workspace_id: str, document: Optional[str] = None, page: int = 1, per_page: int = 50):
    return rag_engine.get_chunks(workspace_id, document, page, per_page)

@app.get("/api/workspace/{workspace_id}/rag/chunks/{chunk_id}")
def rag_get_chunk(workspace_id: str, chunk_id: str):
    chunk = rag_engine.get_chunk(workspace_id, chunk_id)
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return chunk

@app.put("/api/workspace/{workspace_id}/rag/chunks/{chunk_id}")
def rag_update_chunk(workspace_id: str, chunk_id: str, update: ChunkUpdate):
    success = rag_engine.update_chunk(workspace_id, chunk_id, update.text)
    if not success:
        raise HTTPException(status_code=404, detail="Chunk not found")
    return {"status": "updated"}

@app.delete("/api/workspace/{workspace_id}/rag/chunks/{chunk_id}")
def rag_delete_chunk(workspace_id: str, chunk_id: str):
    success = rag_engine.delete_chunk(workspace_id, chunk_id)
    return {"status": "deleted"}

@app.get("/api/workspace/{workspace_id}/rag/pdf-page/{filename}/{page_number}")
def rag_pdf_page(workspace_id: str, filename: str, page_number: int, highlight: Optional[str] = None):
    from fastapi.responses import Response
    file_path = os.path.join(BASE_WORKSPACE_DIR, workspace_id, filename)
    img_bytes = rag_engine.render_pdf_page(file_path, page_number, highlight_text=highlight)
    if not img_bytes:
        raise HTTPException(status_code=404, detail="Page not found or rendering failed")
    return Response(content=img_bytes, media_type="image/png")

@app.post("/api/upload-folder")
async def upload_folder(request: Request, workspace_id: Optional[str] = Form("default-workspace")):
    form = await request.form()
    files = form.getlist("files")
    workspace_path = os.path.join(BASE_WORKSPACE_DIR, workspace_id)
    os.makedirs(workspace_path, exist_ok=True)
    
    results = []
    for file in files:
        if isinstance(file, UploadFile):
            file_path = os.path.join(workspace_path, file.filename)
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            try:
                loop = asyncio.get_event_loop()
                loop.run_in_executor(
                    _INGEST_EXECUTOR,
                    lambda f=file.filename, p=file_path: rag_engine.ingest_file(workspace_id, f, p)
                )
                results.append({"filename": file.filename, "status": "uploaded_and_indexing"})
            except Exception as e:
                results.append({"filename": file.filename, "status": f"error: {str(e)}"})
                
    return {"workspace_id": workspace_id, "files": results}

@app.post("/api/rag/query")
def rag_query(req: RAGQueryRequest):
    return rag_engine.search(req.workspace_id, req.query, top_k=8, expand_query=req.expand_query, use_reranker=req.use_reranker)

@app.post("/api/workspace/{workspace_id}/rag/re-embed-all")
def rag_reembed_all(workspace_id: str):
    return rag_engine.re_embed_all(workspace_id)

@app.get("/api/workspace/{workspace_id}/rag/status")
def rag_status(workspace_id: str):
    """Get RAG indexing status for a workspace."""
    indexed_files = rag_engine.get_indexed_files(workspace_id)
    total_chunks = rag_engine.get_total_chunks(workspace_id)
    return {
        "workspace_id": workspace_id,
        "indexed_files": indexed_files,
        "total_chunks": total_chunks,
    }

@app.post("/api/workspace/{workspace_id}/rag/reindex")
def rag_reindex(workspace_id: str):
    """Re-index all files in a workspace."""
    workspace_path = os.path.join(BASE_WORKSPACE_DIR, workspace_id)
    if not os.path.exists(workspace_path):
        raise HTTPException(status_code=404, detail="Workspace not found")
    
    results = []
    for fname in os.listdir(workspace_path):
        fpath = os.path.join(workspace_path, fname)
        if os.path.isfile(fpath):
            result = rag_engine.ingest_file(workspace_id, fname, fpath)
            results.append(result)
    
    total_chunks = sum(r["chunks_created"] for r in results)
    log_activity("rag_reindex", f"Re-indexed {len(results)} files ({total_chunks} chunks)")
    return {"files_indexed": len(results), "total_chunks": total_chunks, "details": results}

@app.get("/api/workspace/{workspace_id}/rag/eval")
def rag_eval(workspace_id: str):
    """Get RAG evaluation metrics: index health, search history, LLM judge scores."""
    indexed_files = rag_engine.get_indexed_files(workspace_id)
    total_chunks = rag_engine.get_total_chunks(workspace_id)
    search_history = get_search_history()
    eval_summary = rag_evaluator.get_summary()
    eval_history = rag_evaluator.get_history()
    last_diagnostics = get_last_diagnostics()
    
    return {
        "index_health": {
            "indexed_files": len(indexed_files),
            "total_chunks": total_chunks,
            "files": indexed_files,
        },
        "search_performance": {
            "total_searches": len(search_history),
            "history": search_history[-10:],  # last 10
            "last_diagnostics": last_diagnostics,
        },
        "llm_evaluation": {
            "summary": eval_summary,
            "history": eval_history[-10:],  # last 10
        },
    }

# ── Mount static frontend ────────────────────────────────────────────────────
os.makedirs("static", exist_ok=True)
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, reload_excludes=["workspaces/*", "vectorstore/*", "scratch/*", "*.png", "*.csv", "*.txt", "*.xlsx", "*.pdf"])
