"""
╔══════════════════════════════════════════════════════════════════════╗
║                         MNOGRAM v1.0                                 ║
║         Cloud-Native Enterprise Media Sharing Platform               ║
║                                                                      ║
║  Architecture: Stateless services → Front Door → Blob/CosmosDB       ║
║  Roles: Admin | Creator | Consumer                                   ║
╚══════════════════════════════════════════════════════════════════════╝

NOTE (for coursework demo):
- In Azure mode: Media bytes -> Azure Blob Storage, Metadata/Comments/Logs -> Azure Cosmos DB (NoSQL)
- In Local mode: SQLite is used as a fallback (useful for offline development)
- Scaling/traffic dashboards are still simulated unless you wire App Insights/Azure Monitor.
"""

import base64
import datetime
import hashlib
import io
import json
import math
import os
import random
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

# ─────────────────────────────────────────────────────────────────────
#  AZURE CONFIGURATION (read from environment variables)
# ─────────────────────────────────────────────────────────────────────
AZURE_STORAGE_CONNECTION_STRING = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
AZURE_COSMOS_URI = os.environ.get("AZURE_COSMOS_URI", "")
AZURE_COSMOS_KEY = os.environ.get("AZURE_COSMOS_KEY", "")
AZURE_COGNITIVE_KEY = os.environ.get("AZURE_COGNITIVE_KEY", "")
AZURE_COGNITIVE_ENDPOINT = os.environ.get("AZURE_COGNITIVE_ENDPOINT", "")

# Names
BLOB_CONTAINER = os.environ.get("BLOB_CONTAINER", "mnogram-media")
COSMOS_DATABASE = os.environ.get("COSMOS_DATABASE", "mnogram-db")
COSMOS_MEDIA_CT = os.environ.get("COSMOS_MEDIA_CT", "media-items")
COSMOS_COMMENTS_CT = os.environ.get("COSMOS_COMMENTS_CT", "comments")
COSMOS_LOGS_CT = os.environ.get("COSMOS_LOGS_CT", "logs")

# Azure enabled only if core secrets exist
USE_AZURE = bool(AZURE_STORAGE_CONNECTION_STRING) and bool(AZURE_COSMOS_URI) and bool(AZURE_COSMOS_KEY)

# ─────────────────────────────────────────────────────────────────────
#  LOCAL SQLITE FALLBACK
# ─────────────────────────────────────────────────────────────────────
DB_PATH = "mnogram_local.db"


def db_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    """Initialise local SQLite database (used when Azure credentials absent)."""
    conn = db_conn()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            role TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uploader TEXT,
            title TEXT,
            caption TEXT,
            location TEXT,
            tags TEXT,
            ai_tags TEXT,
            ai_caption TEXT,
            sentiment TEXT,
            moderation TEXT,
            file_name TEXT,
            file_data BLOB,
            blob_url TEXT,
            likes INTEGER DEFAULT 0,
            views INTEGER DEFAULT 0,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id INTEGER,
            commenter TEXT,
            comment TEXT,
            sentiment TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT,
            service TEXT,
            message TEXT,
            trace_id TEXT,
            created_at TEXT
        );
    """)
    conn.commit()
    conn.close()


init_db()

# ─────────────────────────────────────────────────────────────────────
#  AZURE CLIENT HELPERS
# ─────────────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def _blob_service_client():
    from azure.storage.blob import BlobServiceClient
    return BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)


@st.cache_resource(show_spinner=False)
def _cosmos_container_clients():
    """Return Cosmos container clients (media, comments, logs)."""
    from azure.cosmos import CosmosClient
    client = CosmosClient(AZURE_COSMOS_URI, credential=AZURE_COSMOS_KEY)
    db = client.get_database_client(COSMOS_DATABASE)
    return (
        db.get_container_client(COSMOS_MEDIA_CT),
        db.get_container_client(COSMOS_COMMENTS_CT),
        db.get_container_client(COSMOS_LOGS_CT),
    )


def upload_to_blob(file_bytes: bytes, blob_name: str) -> str:
    """Upload bytes to Azure Blob Storage; returns blob URL or local pseudo-URL."""
    if not USE_AZURE:
        return f"local://{blob_name}"

    try:
        client = _blob_service_client()
        container = client.get_container_client(BLOB_CONTAINER)
        try:
            container.create_container()
        except Exception:
            pass

        blob = container.get_blob_client(blob_name)
        blob.upload_blob(file_bytes, overwrite=True)
        return blob.url
    except Exception as e:
        add_log("ERROR", "BlobStorage", f"Blob upload failed: {e}")
        return f"local://{blob_name}"


# ─────────────────────────────────────────────────────────────────────
#  AI / SENTIMENT (Cognitive optional; demo uses mock if not configured)
# ─────────────────────────────────────────────────────────────────────
def _mock_ai_analysis() -> dict:
    tag_pool = [
        "photography", "nature", "urban", "portrait", "travel", "architecture",
        "food", "art", "lifestyle", "technology", "fashion", "sport", "animals"
    ]
    return {
        "tags": random.sample(tag_pool, 4),
        "caption": random.choice([
            "A stunning visual captured with exceptional detail.",
            "An artistic composition showcasing modern aesthetics.",
            "Vibrant colors and dynamic framing in this shot.",
            "A candid moment frozen in time with beautiful lighting.",
        ]),
        "moderation": "APPROVED",
    }


def analyze_image_with_ai(file_bytes: bytes) -> dict:
    """
    If Azure Computer Vision keys are configured, call it.
    Otherwise return mock AI output (still useful for demo).
    """
    if not (AZURE_COGNITIVE_KEY and AZURE_COGNITIVE_ENDPOINT):
        return _mock_ai_analysis()

    try:
        import requests as req
        headers = {
            "Ocp-Apim-Subscription-Key": AZURE_COGNITIVE_KEY,
            "Content-Type": "application/octet-stream",
        }
        params = {"visualFeatures": "Categories,Description,Tags,Adult", "language": "en"}
        resp = req.post(
            f"{AZURE_COGNITIVE_ENDPOINT}/vision/v3.2/analyze",
            headers=headers, params=params, data=file_bytes, timeout=10
        )
        data = resp.json()
        tags = [t["name"] for t in data.get("tags", [])[:5]]
        caption = data.get("description", {}).get("captions", [{}])[0].get("text", "AI caption unavailable")
        adult = data.get("adult", {})
        mod = "FLAGGED" if adult.get("isAdultContent") or adult.get("isRacyContent") else "APPROVED"
        return {"tags": tags, "caption": caption, "moderation": mod}
    except Exception as e:
        add_log("ERROR", "CognitiveServices", f"{e}")
        return _mock_ai_analysis()


def analyze_sentiment(text: str) -> str:
    """Naive local sentiment (demo)."""
    pos = ["love", "great", "amazing", "beautiful", "fantastic", "excellent", "wonderful"]
    neg = ["hate", "bad", "awful", "terrible", "horrible", "disgusting", "worst"]
    t = (text or "").lower()
    p = sum(w in t for w in pos)
    n = sum(w in t for w in neg)
    if p > n:
        return "😊 Positive"
    if n > p:
        return "😡 Negative"
    return "😐 Neutral"


# ─────────────────────────────────────────────────────────────────────
#  LOG HELPERS (Cosmos in Azure mode, SQLite otherwise)
# ─────────────────────────────────────────────────────────────────────
def add_log(level: str, service: str, message: str):
    trace_id = hashlib.md5(f"{time.time()}{random.random()}".encode()).hexdigest()[:8]
    ts = datetime.datetime.utcnow().isoformat()

    if USE_AZURE:
        try:
            _, _, logs_ct = _cosmos_container_clients()
            doc = {
                "id": str(uuid.uuid4()),
                "level": level,
                "service": service,
                "message": message,
                "trace_id": trace_id,
                "created_at": ts,
            }
            logs_ct.create_item(doc)
            return
        except Exception as e:
            # Fall back to SQLite logs if Cosmos has an issue
            pass

    conn = db_conn()
    conn.execute(
        "INSERT INTO logs (level,service,message,trace_id,created_at) VALUES (?,?,?,?,?)",
        (level, service, message, trace_id, ts),
    )
    conn.commit()
    conn.close()


def get_logs(limit: int = 100):
    if USE_AZURE:
        try:
            _, _, logs_ct = _cosmos_container_clients()
            items = list(
                logs_ct.query_items(
                    query="SELECT * FROM c ORDER BY c.created_at DESC OFFSET 0 LIMIT @lim",
                    parameters=[{"name": "@lim", "value": int(limit)}],
                    enable_cross_partition_query=True,
                )
            )
            return items  # list[dict]
        except Exception as e:
            pass

    conn = db_conn()
    rows = conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return rows  # list[tuple]


# ─────────────────────────────────────────────────────────────────────
#  MEDIA + COMMENTS PERSISTENCE
# ─────────────────────────────────────────────────────────────────────
def save_media(uploader, title, caption, location, tags, ai_result, file_name, file_bytes, blob_url):
    ts = datetime.datetime.utcnow().isoformat()

    if USE_AZURE:
        media_ct, _, _ = _cosmos_container_clients()
        doc = {
            "id": str(uuid.uuid4()),
            "uploader": uploader,  # partition key
            "title": title,
            "caption": caption,
            "location": location,
            "tags": tags,
            "ai_tags": ai_result.get("tags", []),
            "ai_caption": ai_result.get("caption", ""),
            "sentiment": analyze_sentiment(caption),
            "moderation": ai_result.get("moderation", "APPROVED"),
            "file_name": file_name,
            "blob_url": blob_url,
            "likes": 0,
            "views": 0,
            "created_at": ts,
        }
        media_ct.create_item(doc)
        add_log("INFO", "UploadService", f"Media '{title}' uploaded by {uploader}")
        return doc["id"]

    conn = db_conn()
    conn.execute("""
        INSERT INTO media (uploader,title,caption,location,tags,ai_tags,ai_caption,
                           sentiment,moderation,file_name,file_data,blob_url,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        uploader, title, caption, location, tags,
        json.dumps(ai_result.get("tags", [])),
        ai_result.get("caption", ""),
        analyze_sentiment(caption),
        ai_result.get("moderation", "APPROVED"),
        file_name, file_bytes, blob_url, ts
    ))
    conn.commit()
    conn.close()
    add_log("INFO", "UploadService", f"Media '{title}' uploaded by {uploader}")
    return None


def get_all_media():
    if USE_AZURE:
        media_ct, _, _ = _cosmos_container_clients()
        items = list(media_ct.query_items(
            query="SELECT * FROM c ORDER BY c.created_at DESC",
            enable_cross_partition_query=True
        ))
        return items  # list[dict]

    conn = db_conn()
    rows = conn.execute("SELECT * FROM media ORDER BY id DESC").fetchall()
    conn.close()
    return rows  # list[tuple]


def get_media_by_id(mid):
    if USE_AZURE:
        media_ct, _, _ = _cosmos_container_clients()
        items = list(media_ct.query_items(
            query="SELECT * FROM c WHERE c.id=@id",
            parameters=[{"name": "@id", "value": str(mid)}],
            enable_cross_partition_query=True
        ))
        return items[0] if items else None

    conn = db_conn()
    row = conn.execute("SELECT * FROM media WHERE id=?", (mid,)).fetchone()
    conn.close()
    return row


def save_comment(media_id, commenter, comment):
    ts = datetime.datetime.utcnow().isoformat()
    sentiment = analyze_sentiment(comment)

    if USE_AZURE:
        _, comments_ct, _ = _cosmos_container_clients()
        doc = {
            "id": str(uuid.uuid4()),
            "mediaId": str(media_id),  # partition key in comments container
            "commenter": commenter,
            "comment": comment,
            "sentiment": sentiment,
            "created_at": ts,
        }
        comments_ct.create_item(doc)
        add_log("INFO", "CommentService", f"Comment by {commenter} on media {media_id}")
        return

    conn = db_conn()
    conn.execute("""
        INSERT INTO comments (media_id,commenter,comment,sentiment,created_at)
        VALUES (?,?,?,?,?)
    """, (media_id, commenter, comment, sentiment, ts))
    conn.commit()
    conn.close()
    add_log("INFO", "CommentService", f"Comment by {commenter} on media #{media_id}")


def get_comments(media_id):
    if USE_AZURE:
        _, comments_ct, _ = _cosmos_container_clients()
        items = list(comments_ct.query_items(
            query="SELECT * FROM c WHERE c.mediaId=@mid ORDER BY c.created_at DESC",
            parameters=[{"name": "@mid", "value": str(media_id)}],
            enable_cross_partition_query=True
        ))
        return items  # list[dict]

    conn = db_conn()
    rows = conn.execute(
        "SELECT * FROM comments WHERE media_id=? ORDER BY id DESC", (media_id,)
    ).fetchall()
    conn.close()
    return rows


def like_media(media_id):
    if USE_AZURE:
        media = get_media_by_id(media_id)
        if not media:
            return
        media_ct, _, _ = _cosmos_container_clients()
        media["likes"] = int(media.get("likes", 0)) + 1
        media_ct.replace_item(item=media["id"], body=media)
        return

    conn = db_conn()
    conn.execute("UPDATE media SET likes=likes+1 WHERE id=?", (media_id,))
    conn.commit()
    conn.close()


def increment_views(media_id):
    if USE_AZURE:
        media = get_media_by_id(media_id)
        if not media:
            return
        media_ct, _, _ = _cosmos_container_clients()
        media["views"] = int(media.get("views", 0)) + 1
        media_ct.replace_item(item=media["id"], body=media)
        return

    conn = db_conn()
    conn.execute("UPDATE media SET views=views+1 WHERE id=?", (media_id,))
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────
#  SCALABILITY METRICS SIMULATION (still demo)
# ─────────────────────────────────────────────────────────────────────
def generate_live_metrics(t: float) -> dict:
    hour_cycle = math.sin(t / 3600 * 2 * math.pi) * 0.5 + 0.5
    spike = 1.5 if random.random() < 0.05 else 1.0
    base_users = int(250 + hour_cycle * 600 + random.gauss(0, 20))
    active_users = int(base_users * spike)
    uploads_pm = round(active_users * 0.04 + random.gauss(0, 2), 1)
    throughput = int(active_users * 12 + random.gauss(0, 50))
    latency_ms = round(80 + random.gauss(0, 15) + (spike - 1) * 120, 1)
    storage_gb = round(120 + t / 86400 * 3 + random.gauss(0, 0.5), 2)
    bandwidth_mb = round(throughput * 0.8 + random.gauss(0, 30), 1)
    node_count = max(2, min(20, int(active_users / 80) + 1))
    cache_hit = round(min(98, 72 + hour_cycle * 15 + random.gauss(0, 3)), 1)
    queue_depth = max(0, int(uploads_pm * 0.4 + random.gauss(0, 5)))
    db_ops = int(throughput * 1.4)
    failed_req = int(throughput * random.uniform(0.001, 0.008))
    return {
        "active_users": active_users,
        "uploads_pm": uploads_pm,
        "throughput": throughput,
        "latency_ms": latency_ms,
        "storage_gb": storage_gb,
        "bandwidth_mb": bandwidth_mb,
        "node_count": node_count,
        "cache_hit": cache_hit,
        "queue_depth": queue_depth,
        "db_ops": db_ops,
        "failed_req": failed_req,
        "success_req": throughput - failed_req,
        "spike_active": spike > 1.0,
        "cpu_pct": min(95, int(30 + (active_users / 800) * 60 + random.gauss(0, 5))),
        "mem_pct": min(90, int(45 + (node_count / 20) * 30 + random.gauss(0, 3))),
    }


# ─────────────────────────────────────────────────────────────────────
#  THEME / CSS (kept compact here; you can paste your big CSS back)
# ─────────────────────────────────────────────────────────────────────
DARK_BG = "#0d1117"
CARD_BG = "#161b22"
ACCENT = "#00d4ff"
ACCENT2 = "#7c3aed"
SUCCESS = "#22c55e"
WARN = "#f59e0b"
DANGER = "#ef4444"
TEXT_MAIN = "#e6edf3"
TEXT_DIM = "#8b949e"

PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color=TEXT_MAIN, family="monospace"),
    margin=dict(l=10, r=10, t=30, b=10),
    xaxis=dict(gridcolor="#21262d", zerolinecolor="#21262d"),
    yaxis=dict(gridcolor="#21262d", zerolinecolor="#21262d"),
)

st.markdown(f"""
<style>
  html, body, [class*="css"] {{
      background-color: {DARK_BG} !important;
      color: {TEXT_MAIN} !important;
  }}
  .stApp {{ background-color: {DARK_BG} !important; }}
  [data-testid="stSidebar"] {{
      background: linear-gradient(180deg, #161b22 0%, #0d1117 100%) !important;
      border-right: 1px solid #30363d !important;
  }}
  .metric-card {{
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 12px;
      padding: 18px 20px;
      margin-bottom: 12px;
  }}
  .logo-text {{
      font-family: monospace;
      font-size: 26px;
      font-weight: 800;
      color: {ACCENT};
  }}
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────
#  DEMO USERS
# ─────────────────────────────────────────────────────────────────────
USERS = {
    "admin": {"password": "admin123", "role": "admin"},
    "creator": {"password": "creator123", "role": "creator"},
    "user": {"password": "user123", "role": "consumer"},
}


def init_session():
    defaults = {
        "logged_in": False,
        "username": None,
        "role": None,
        "metrics_history": {"ts": [], "users": [], "throughput": [], "latency": [], "nodes": [], "cache": [], "queue": []},
        "page": "dashboard",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


init_session()


def show_login():
    st.markdown("<br><br>", unsafe_allow_html=True)
    st.markdown("<div style='text-align:center'><div class='logo-text'>MNOGRAM</div></div>", unsafe_allow_html=True)

    username = st.text_input("Username", placeholder="admin / creator / user")
    password = st.text_input("Password", type="password", placeholder="Enter password")

    if st.button("Sign In", use_container_width=True):
        u = USERS.get(username)
        if u and u["password"] == password:
            st.session_state.logged_in = True
            st.session_state.username = username
            st.session_state.role = u["role"]
            add_log("INFO", "AuthService", f"Login: {username} ({u['role']})")
            st.rerun()
        else:
            st.error("Invalid credentials")


def show_sidebar():
    with st.sidebar:
        st.markdown("<div class='logo-text'>MNOGRAM</div>", unsafe_allow_html=True)
        st.caption(f"Mode: {'Azure (Blob+Cosmos)' if USE_AZURE else 'Local (SQLite)'}")
        st.caption(f"User: {st.session_state.username} ({st.session_state.role})")

        role = st.session_state.role

        pages = []
        if role == "admin":
            pages = [("📊", "Admin Dashboard", "admin_dashboard"),
                     ("🗂️", "Upload Monitor", "upload_monitor"),
                     ("📋", "System Logs", "logs"),
                     ("🖼️", "Browse Media", "browse")]
        elif role == "creator":
            pages = [("⬆️", "Upload Media", "upload"),
                     ("🖼️", "My Posts", "my_posts"),
                     ("🖼️", "Browse Media", "browse")]
        else:
            pages = [("🖼️", "Browse Media", "browse"),
                     ("🔍", "Search", "search")]

        for icon, label, key in pages:
            if st.button(f"{icon} {label}", use_container_width=True):
                st.session_state.page = key
                st.rerun()

        if st.button("Sign Out", use_container_width=True):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()


def _render_media_grid(media_rows, can_comment=True):
    cols = st.columns(2)
    for i, item in enumerate(media_rows):
        col = cols[i % 2]
        with col:
            if USE_AZURE and isinstance(item, dict):
                mid = item.get("id")
                uploader = item.get("uploader")
                title = item.get("title")
                caption = item.get("caption")
                location = item.get("location")
                ai_tags = item.get("ai_tags", [])
                ai_caption = item.get("ai_caption", "")
                sentiment = item.get("sentiment", "")
                moderation = item.get("moderation", "")
                file_name = item.get("file_name", "")
                blob_url = item.get("blob_url", "")
                likes = int(item.get("likes", 0))
                views = int(item.get("views", 0))
                created_at = item.get("created_at", "")

                st.markdown(f"### {title}")
                st.caption(f"@{uploader} · {location} · {created_at[:10]}")
                if blob_url:
                    # If your container is private, this still works if Blob URL is public.
                    # For strict private blobs you'd generate SAS tokens (extra step).
                    st.image(blob_url, use_container_width=True)
                st.write(caption)
                st.caption(f"✨ AI: {ai_caption}")
                st.caption(f"🏷️ {', '.join(ai_tags) if isinstance(ai_tags, list) else ai_tags}")
                st.caption(f"{sentiment} · Moderation: {moderation}")

            else:
                # SQLite tuple mode
                (mid, uploader, title, caption, location, tags, ai_tags, ai_caption, sentiment,
                 moderation, file_name, file_data, blob_url, likes, views, created_at) = item

                st.markdown(f"### {title}")
                st.caption(f"@{uploader} · {location} · {created_at[:10]}")

                if file_data and file_name.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
                    st.image(bytes(file_data), use_container_width=True)
                st.write(caption)
                try:
                    tags_list = json.loads(ai_tags) if ai_tags else []
                except Exception:
                    tags_list = []
                st.caption(f"✨ AI: {ai_caption}")
                st.caption(f"🏷️ {', '.join(tags_list)}")
                st.caption(f"{sentiment} · Moderation: {moderation}")

            c1, c2 = st.columns(2)
            with c1:
                if st.button(f"❤️ {likes}", key=f"like_{mid}"):
                    like_media(mid)
                    increment_views(mid)
                    add_log("INFO", "EngagementService", f"Like on media {mid}")
                    st.rerun()
            with c2:
                st.markdown(f"👁️ {views}")

            if can_comment:
                with st.expander(f"💬 Comments ({len(get_comments(mid))})"):
                    comments = get_comments(mid)
                    if USE_AZURE and comments and isinstance(comments[0], dict):
                        for c in comments:
                            st.markdown(f"**@{c.get('commenter')}**: {c.get('comment')}  \n{c.get('created_at','')}")
                    else:
                        for c in comments:
                            # (id, media_id, commenter, comment, sentiment, created_at)
                            st.markdown(f"**@{c[2]}**: {c[3]}  \n{c[5]}")
                    new_comment = st.text_input("Add a comment…", key=f"comment_input_{mid}")
                    if st.button("Post", key=f"post_comment_{mid}"):
                        if new_comment.strip():
                            save_comment(mid, st.session_state.username, new_comment.strip())
                            st.rerun()


def page_upload():
    st.header("⬆️ Upload Media")
    st.caption("Azure mode: bytes go to Blob Storage; metadata to Cosmos DB")

    with st.form("upload_form"):
        uploaded_file = st.file_uploader("Choose image", type=["jpg", "jpeg", "png", "gif"])
        title = st.text_input("Title")
        caption = st.text_area("Caption", height=80)
        location = st.text_input("Location")
        tags = st.text_input("Tag people")
        submitted = st.form_submit_button("Upload & Publish")

    if not submitted:
        return
    if not uploaded_file or not title:
        st.error("Please choose a file and enter a title.")
        return

    file_bytes = uploaded_file.read()
    blob_name = f"{st.session_state.username}/{int(time.time())}_{uploaded_file.name}"
    blob_url = upload_to_blob(file_bytes, blob_name)

    ai_result = analyze_image_with_ai(file_bytes)
    if ai_result.get("moderation") == "FLAGGED":
        st.error("🚫 Flagged by moderation. Upload rejected.")
        add_log("WARN", "ContentModeration", f"FLAGGED upload '{title}'")
        return

    save_media(
        st.session_state.username, title, caption, location, tags,
        ai_result, uploaded_file.name, file_bytes, blob_url
    )
    st.success("✅ Uploaded")


def page_browse():
    st.header("🖼️ Browse Media")
    media = get_all_media()
    if not media:
        st.info("No media yet.")
        return
    _render_media_grid(media, can_comment=True)


def page_my_posts():
    st.header("🖼️ My Posts")
    media = get_all_media()
    if USE_AZURE and media and isinstance(media[0], dict):
        my_media = [m for m in media if m.get("uploader") == st.session_state.username]
    else:
        my_media = [m for m in media if m[1] == st.session_state.username]
    if not my_media:
        st.info("You have no posts yet.")
        return
    _render_media_grid(my_media, can_comment=False)


def page_search():
    st.header("🔍 Search")
    q = st.text_input("Search")
    if not q:
        st.info("Enter a search term.")
        return
    ql = q.lower()
    media = get_all_media()

    results = []
    if USE_AZURE and media and isinstance(media[0], dict):
        for m in media:
            blob = (m.get("blob_url") or "")
            if ql in (m.get("title") or "").lower() or ql in (m.get("caption") or "").lower() or ql in (m.get("location") or "").lower():
                results.append(m)
    else:
        for r in media:
            if ql in (r[2] or "").lower() or ql in (r[3] or "").lower() or ql in (r[4] or "").lower():
                results.append(r)

    st.caption(f"{len(results)} result(s)")
    if results:
        _render_media_grid(results, can_comment=True)
    else:
        st.warning("No results found.")


def page_admin_dashboard():
    st.header("📊 Admin Dashboard (demo metrics)")
    t = time.time()
    m = generate_live_metrics(t)
    st.write(m)
    st.caption("These metrics are simulated. Real telemetry comes from Application Insights (next step).")


def page_upload_monitor():
    st.header("🗂️ Upload Monitor")
    media = get_all_media()
    st.caption(f"Total uploads: {len(media)}")
    if USE_AZURE and media and isinstance(media[0], dict):
        df = pd.DataFrame(media)
        st.dataframe(df[["id", "uploader", "title", "location", "likes", "views", "created_at"]], use_container_width=True)
    else:
        rows = []
        for r in media:
            rows.append([r[0], r[1], r[2], r[4], r[14], r[15], r[16]])
        df = pd.DataFrame(rows, columns=["ID", "Uploader", "Title", "Location", "Likes", "Views", "Created"])
        st.dataframe(df, use_container_width=True)


def page_logs():
    st.header("📋 System Logs")
    logs = get_logs(200)
    if not logs:
        st.info("No logs.")
        return

    if USE_AZURE and isinstance(logs[0], dict):
        df = pd.DataFrame(logs)
        st.dataframe(df[["created_at", "level", "service", "message", "trace_id"]], use_container_width=True)
    else:
        # (id, level, service, message, trace_id, created_at)
        rows = [[r[5], r[1], r[2], r[3], r[4]] for r in logs]
        df = pd.DataFrame(rows, columns=["created_at", "level", "service", "message", "trace_id"])
        st.dataframe(df, use_container_width=True)


def main():
    st.set_page_config(page_title="Mnogram", page_icon="🔷", layout="wide")

    if not st.session_state.get("logged_in"):
        show_login()
        return

    show_sidebar()
    role = st.session_state.role
    page = st.session_state.page

    if role == "admin":
        if page == "admin_dashboard":
            page_admin_dashboard()
        elif page == "upload_monitor":
            page_upload_monitor()
        elif page == "logs":
            page_logs()
        elif page == "browse":
            page_browse()
        else:
            page_admin_dashboard()

    elif role == "creator":
        if page == "upload":
            page_upload()
        elif page == "my_posts":
            page_my_posts()
        elif page == "browse":
            page_browse()
        else:
            page_upload()

    else:
        if page == "browse":
            page_browse()
        elif page == "search":
            page_search()
        else:
            page_browse()


if __name__ == "__main__":
    main()