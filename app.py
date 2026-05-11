import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import streamlit as st

APP_TITLE = "Mnogram - Scalable Media Distribution Demo"
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MEDIA_DIR = DATA_DIR / "media"
DB_PATH = DATA_DIR / "mnogram.db"
MAX_UPLOAD_MB = 10
PAGE_SIZE = 6

USERS = {
    "creator1": {"password": "creator123", "role": "creator"},
    "creator2": {"password": "creator123", "role": "creator"},
    "viewer1": {"password": "viewer123", "role": "consumer"},
    "viewer2": {"password": "viewer123", "role": "consumer"},
}


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)


@st.cache_resource
def get_connection() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator TEXT NOT NULL,
            title TEXT NOT NULL,
            caption TEXT,
            location TEXT,
            people TEXT,
            image_path TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            comment TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(photo_id) REFERENCES photos(id)
        );

        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            created_at TEXT NOT NULL,
            UNIQUE(photo_id, username),
            FOREIGN KEY(photo_id) REFERENCES photos(id)
        );

        CREATE INDEX IF NOT EXISTS idx_photos_created_at ON photos(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_photos_title ON photos(title);
        CREATE INDEX IF NOT EXISTS idx_photos_location ON photos(location);
        CREATE INDEX IF NOT EXISTS idx_comments_photo_id ON comments(photo_id);
        CREATE INDEX IF NOT EXISTS idx_ratings_photo_id ON ratings(photo_id);
        """
    )
    conn.commit()


@st.cache_data(show_spinner=False, ttl=60)
def fetch_photos(search_text: str, location: str, sort_by: str, page: int, page_size: int) -> List[sqlite3.Row]:
    conn = get_connection()
    where = []
    params: List[str] = []

    if search_text:
        token = f"%{search_text.strip()}%"
        where.append("(title LIKE ? OR caption LIKE ? OR people LIKE ?)")
        params.extend([token, token, token])

    if location:
        where.append("location LIKE ?")
        params.append(f"%{location.strip()}%")

    where_clause = f"WHERE {' AND '.join(where)}" if where else ""

    order_clause = "ORDER BY p.created_at DESC"
    if sort_by == "Top rated":
        order_clause = "ORDER BY avg_rating DESC, p.created_at DESC"

    offset = (page - 1) * page_size

    query = f"""
        SELECT p.*,
               COALESCE(AVG(r.rating), 0) AS avg_rating,
               COUNT(DISTINCT c.id) AS comment_count,
               COUNT(DISTINCT r.id) AS rating_count
        FROM photos p
        LEFT JOIN comments c ON c.photo_id = p.id
        LEFT JOIN ratings r ON r.photo_id = p.id
        {where_clause}
        GROUP BY p.id
        {order_clause}
        LIMIT ? OFFSET ?
    """

    params.extend([str(page_size), str(offset)])
    return conn.execute(query, params).fetchall()


@st.cache_data(show_spinner=False, ttl=60)
def count_photos(search_text: str, location: str) -> int:
    conn = get_connection()
    where = []
    params: List[str] = []

    if search_text:
        token = f"%{search_text.strip()}%"
        where.append("(title LIKE ? OR caption LIKE ? OR people LIKE ?)")
        params.extend([token, token, token])

    if location:
        where.append("location LIKE ?")
        params.append(f"%{location.strip()}%")

    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    query = f"SELECT COUNT(*) AS cnt FROM photos {where_clause}"
    row = conn.execute(query, params).fetchone()
    return int(row["cnt"]) if row else 0


@st.cache_data(show_spinner=False, ttl=30)
def get_comments(photo_id: int) -> List[sqlite3.Row]:
    conn = get_connection()
    return conn.execute(
        """
        SELECT username, comment, created_at
        FROM comments
        WHERE photo_id = ?
        ORDER BY created_at DESC
        """,
        (photo_id,),
    ).fetchall()


@st.cache_data(show_spinner=False, ttl=30)
def get_stats() -> Dict[str, int]:
    conn = get_connection()
    stats = {
        "photos": conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0],
        "comments": conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0],
        "ratings": conn.execute("SELECT COUNT(*) FROM ratings").fetchone()[0],
    }
    return stats


def clear_cached_queries() -> None:
    fetch_photos.clear()
    count_photos.clear()
    get_comments.clear()
    get_stats.clear()


def login_card() -> None:
    st.subheader("Login")
    username = st.text_input("Username", key="login_username")
    password = st.text_input("Password", type="password", key="login_password")

    if st.button("Sign in", use_container_width=True):
        account = USERS.get(username)
        if account and account["password"] == password:
            st.session_state["user"] = username
            st.session_state["role"] = account["role"]
            st.success(f"Logged in as {username} ({account['role']})")
            st.rerun()
        st.error("Invalid credentials")

    with st.expander("Demo accounts"):
        st.markdown(
            "- Creator: `creator1 / creator123`\\n"
            "- Creator: `creator2 / creator123`\\n"
            "- Consumer: `viewer1 / viewer123`\\n"
            "- Consumer: `viewer2 / viewer123`"
        )


def save_upload(uploaded_file) -> Optional[str]:
    if uploaded_file is None:
        return None

    file_size_mb = len(uploaded_file.getvalue()) / (1024 * 1024)
    if file_size_mb > MAX_UPLOAD_MB:
        st.error(f"File too large ({file_size_mb:.2f} MB). Max size is {MAX_UPLOAD_MB} MB.")
        return None

    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        st.error("Only PNG, JPG, JPEG, and WEBP files are supported.")
        return None

    safe_name = f"{uuid.uuid4().hex}{suffix}"
    target = MEDIA_DIR / safe_name
    target.write_bytes(uploaded_file.getvalue())
    return str(target)


def creator_view(user: str) -> None:
    st.subheader("Creator Studio")
    st.caption("Upload photos and metadata for consumer discovery.")

    with st.form("upload_form", clear_on_submit=True):
        title = st.text_input("Title", max_chars=120)
        caption = st.text_area("Caption", max_chars=500)
        location = st.text_input("Location", max_chars=120)
        people = st.text_input("People present (comma-separated)", max_chars=250)
        uploaded_file = st.file_uploader("Upload photo", type=["png", "jpg", "jpeg", "webp"])
        submitted = st.form_submit_button("Publish photo", use_container_width=True)

    if submitted:
        if not title.strip():
            st.error("Title is required.")
            return
        saved_path = save_upload(uploaded_file)
        if not saved_path:
            st.error("A valid image upload is required.")
            return

        conn = get_connection()
        conn.execute(
            """
            INSERT INTO photos (creator, title, caption, location, people, image_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user,
                title.strip(),
                caption.strip(),
                location.strip(),
                people.strip(),
                saved_path,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        clear_cached_queries()
        st.success("Photo published.")


def add_comment(photo_id: int, username: str, comment: str) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO comments (photo_id, username, comment, created_at) VALUES (?, ?, ?, ?)",
        (photo_id, username, comment.strip(), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    clear_cached_queries()


def add_or_update_rating(photo_id: int, username: str, rating: int) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO ratings (photo_id, username, rating, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(photo_id, username)
        DO UPDATE SET rating=excluded.rating, created_at=excluded.created_at
        """,
        (photo_id, username, rating, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    clear_cached_queries()


def consumer_view(user: str) -> None:
    st.subheader("Consumer Feed")
    st.caption("Search, rate, and comment on uploaded photos.")

    search_text = st.text_input("Search by title/caption/people")
    location = st.text_input("Filter by location")
    sort_by = st.selectbox("Sort by", ["Newest", "Top rated"])

    total_photos = count_photos(search_text, location)
    total_pages = max(1, (total_photos + PAGE_SIZE - 1) // PAGE_SIZE)
    page = st.number_input("Page", min_value=1, max_value=total_pages, value=1, step=1)

    start = time.perf_counter()
    photos = fetch_photos(search_text, location, sort_by, int(page), PAGE_SIZE)
    query_ms = (time.perf_counter() - start) * 1000

    st.caption(
        f"Results: {total_photos} photos | page {int(page)}/{total_pages} | query {query_ms:.1f} ms"
    )

    if not photos:
        st.info("No photos found. Ask a creator to upload content.")
        return

    for photo in photos:
        with st.container(border=True):
            st.markdown(f"### {photo['title']}")
            st.caption(
                f"by {photo['creator']} | {photo['location'] or 'Unknown location'} | "
                f"{photo['created_at'][:19].replace('T', ' ')}"
            )
            st.image(photo["image_path"], use_container_width=True)
            if photo["caption"]:
                st.write(photo["caption"])
            if photo["people"]:
                st.caption(f"People: {photo['people']}")

            st.write(
                f"⭐ {float(photo['avg_rating']):.2f} avg ({photo['rating_count']} ratings)"
                f" · �� {photo['comment_count']} comments"
            )

            c1, c2 = st.columns([1, 2])
            with c1:
                rating = st.slider(
                    "Rate",
                    1,
                    5,
                    3,
                    key=f"rating_{photo['id']}",
                    help="1 = poor, 5 = excellent",
                )
                if st.button("Submit rating", key=f"rate_btn_{photo['id']}"):
                    add_or_update_rating(photo["id"], user, int(rating))
                    st.success("Rating saved")
                    st.rerun()

            with c2:
                comment = st.text_input("Add comment", key=f"comment_{photo['id']}")
                if st.button("Post comment", key=f"comment_btn_{photo['id']}"):
                    if comment.strip():
                        add_comment(photo["id"], user, comment)
                        st.success("Comment added")
                        st.rerun()
                    else:
                        st.warning("Comment cannot be empty")

            comments = get_comments(photo["id"])
            with st.expander(f"View comments ({len(comments)})"):
                if comments:
                    for item in comments[:20]:
                        ts = item["created_at"][:19].replace("T", " ")
                        st.markdown(f"**{item['username']}** ({ts}): {item['comment']}")
                else:
                    st.caption("No comments yet")


def scalability_dashboard() -> None:
    st.subheader("Scalability Evidence Dashboard")
    stats = get_stats()

    a, b, c = st.columns(3)
    a.metric("Photos", stats["photos"])
    b.metric("Comments", stats["comments"])
    c.metric("Ratings", stats["ratings"])

    st.markdown(
        """
        **Implemented scale-oriented patterns in this app:**
        - Separation of roles (creator/consumer) and responsibilities.
        - Persistent storage (SQLite now; swappable to managed cloud DB).
        - Query caching (`st.cache_data`) with invalidation on writes.
        - Indexed queries and paginated feed retrieval.
        - Media stored as files (object-storage style abstraction).
        """
    )


def logout_button() -> None:
    if st.button("Logout"):
        st.session_state.clear()
        st.rerun()


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)
    st.caption("Coursework demo app for scalable photo distribution and interaction")

    init_db()

    user = st.session_state.get("user")
    role = st.session_state.get("role")

    if not user:
        login_card()
        return

    st.success(f"Signed in as {user} ({role})")

    tabs = st.tabs(["Main", "Scalability", "Account"])

    with tabs[0]:
        if role == "creator":
            creator_view(user)
        else:
            consumer_view(user)

    with tabs[1]:
        scalability_dashboard()

    with tabs[2]:
        st.write(f"Username: `{user}`")
        st.write(f"Role: `{role}`")
        logout_button()


if __name__ == "__main__":
    main()
