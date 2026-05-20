# AgentOS — Agentic AI Workspace Platform

## Overview
An enterprise AI-powered document analysis, data transformation, and visualization platform built for interview demonstration. Features a FastAPI backend with LangGraph agent orchestration and a vanilla HTML/CSS/JS frontend.

## Architecture

```
agent-showcase/
├── backend/
│   ├── main.py              # FastAPI server — all API endpoints
│   ├── agent.py             # LangGraph agent — planner/executor with ReAct
│   ├── .env                 # OPENAI_API_KEY
│   ├── requirements.txt     # Python dependencies
│   ├── tools/
│   │   ├── file_tools.py    # Create/read/modify text, Excel, PDF files
│   │   ├── csv_tools.py     # Create/read/modify CSV files [NEW]
│   │   ├── dataset_tools.py # Analyze/clean/transform datasets
│   │   ├── chart_tools.py   # Bar/line/pie/histogram charts [UPDATED]
│   │   └── vision_tools.py  # Image analysis via vision model
│   ├── static/
│   │   ├── index.html       # Frontend HTML [REWRITTEN - needs CSS+JS]
│   │   ├── styles.css       # Frontend CSS [NEEDS REWRITE]
│   │   └── app.js           # Frontend JS [NEEDS REWRITE]
│   └── workspaces/
│       └── default-workspace/  # File storage
└── README.md
```

## What's Been Completed

### Backend (100% Done)
1. **main.py** — Complete rewrite with:
   - `POST /api/upload` — File upload with activity logging
   - `POST /api/chat` — Agent chat with token tracking + rate limiting
   - `POST /api/execute` — Plan execution with timeout
   - `GET /api/workspace/{id}/files` — List files with metadata (size, type, modified)
   - `GET /api/workspace/{id}/download/{filename}` — Download file
   - `GET /api/workspace/{id}/preview/{filename}` — Inline preview (text, table, image, PDF)
   - `DELETE /api/workspace/{id}/files/{filename}` — Always returns 403 (protected)
   - `GET /api/tokens` — Token usage stats
   - `POST /api/tokens/limit` — Set/extend token limit
   - `GET /api/activity` — Activity log
   - `GET/POST/PUT/DELETE /api/tasks` — Task scheduler CRUD
   - Rate limiter: 30 req/min sliding window

2. **agent.py** — Complete rewrite with:
   - Model: `gpt-5.4-mini`
   - Token budget enforcement (blocks requests when limit exceeded)
   - Activity logging for all agent calls and tool executions
   - File context injection (lists workspace files in system prompt)
   - All 20 tools registered (no delete tool for agent)
   - ReAct methodology with plan→interrupt→execute flow

3. **tools/csv_tools.py** — NEW: create_csv_file, read_csv_file, modify_csv_file
4. **tools/chart_tools.py** — UPDATED: Added generate_histogram, improved chart styling
5. **tools/file_tools.py** — Unchanged (delete_file exists but not given to agent)
6. **tools/dataset_tools.py** — Unchanged
7. **tools/vision_tools.py** — Unchanged

### Frontend (Partially Done)
1. **index.html** — COMPLETE rewrite with:
   - Login screen (mock auth, name + role)
   - Sidebar navigation (Dashboard, Files, Agent Chat, Settings)
   - Dashboard view: Calendar, Tasks, Recent Files, Activity, Stats
   - Files view: Upload zone, file grid with filters, preview panel
   - Chat view: Chat interface with welcome chips, plan editor, sidebar
   - Settings view: Token management (ring chart), model config, tools grid, guardrails
   - Modals: Image zoom, task create/edit
   - Toast notifications container

2. **styles.css** — ⚠️ NEEDS COMPLETE REWRITE (still has old dark theme)
3. **app.js** — ⚠️ NEEDS COMPLETE REWRITE (still has old single-page chat code)

## What Still Needs To Be Done

### styles.css — Premium Warm Theme
Design spec:
- Color palette: Cream/warm background (#FDF8F4), dark text (#1a1a2e)
- Accent: Coral (#FF6B6B), Teal (#4ECDC4), Warm yellow (#F7DC6F)
- Inter font, 12-16px border radius, soft shadows
- Login screen: animated gradient orbs, centered card
- Sidebar: icon nav, collapsible, token mini-bar, user avatar
- Dashboard: CSS grid (2-col for calendar+tasks, full-width for files+activity+stats)
- Calendar: 7-col grid, colored task dots, hover effects
- Task list: checkbox items, priority colors, inline edit
- File grid: cards with type icons, hover preview, filter tabs
- Chat: bubbles, plan cards with editable steps, inline images
- Settings: circular token ring (SVG), tool cards grid, guardrail items
- Modals: overlay with backdrop blur, smooth transitions
- Toasts: slide-in from top-right
- Animations: fadeIn, slideUp, pulse, skeleton loading

### app.js — Full Application Logic
Needs:
- State management: { user, currentView, tasks, files, activities, tokenUsage, chatMessages }
- Router: hash-based (#dashboard, #files, #chat, #settings)
- Login flow: form submit → store user → show app
- Dashboard: calendar rendering, task CRUD, activity feed polling, stats
- Files: upload (drag+drop), grid rendering, filter tabs, preview panel
- Chat: message send, plan rendering with edit/execute, file attach, auto-scroll
- Settings: token ring animation, limit update, preset buttons
- Toasts: showToast(message, type)
- All API calls to the backend endpoints listed above

## How to Run

```bash
cd backend
# Activate venv if needed
pip install -r requirements.txt
python main.py
# Opens on http://localhost:8000
```

## API Quick Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /api/upload | Upload file (multipart) |
| POST | /api/chat | Send message to agent |
| POST | /api/execute | Execute approved plan |
| GET | /api/workspace/{id}/files | List workspace files |
| GET | /api/workspace/{id}/download/{f} | Download file |
| GET | /api/workspace/{id}/preview/{f} | Preview file content |
| DELETE | /api/workspace/{id}/files/{f} | Always 403 (protected) |
| GET | /api/tokens | Token usage stats |
| POST | /api/tokens/limit | Update token limit |
| GET | /api/activity | Activity log |
| GET | /api/tasks | List tasks |
| POST | /api/tasks | Create task |
| PUT | /api/tasks/{id} | Update task |
| DELETE | /api/tasks/{id} | Delete task |

## Key Design Decisions
- **No file deletion**: Users cannot delete files from the UI or via agent
- **Mock login**: Any name/role works, stored in-memory
- **Single workspace**: All users share `default-workspace`
- **In-memory storage**: Tasks, activity, tokens reset on server restart
- **Token guardrail**: Agent refuses to work when budget exhausted
- **Rate limit**: 30 req/min sliding window
