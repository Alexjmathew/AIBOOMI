"""
db_layer.py – unified database abstraction for Firebase Firestore **or** SQLite.

Rules
-----
* If ``firebase.json`` exists and can be loaded → Firebase is used.
* Otherwise → SQLite (file: ``fitness.db``) is used (offline / no-credentials mode).

Public API (mirrors Firestore's document-centric style)
--------------------------------------------------------
    db = get_db()                          # returns FirebaseDB or SQLiteDB

    # User CRUD
    db.get_user(email)                     # -> dict | None
    db.set_user(email, data)               # create / overwrite
    db.update_user(email, data)            # partial update (merge)
    db.user_exists(email)                  # -> bool
    db.all_users()                         # -> list[dict]  (admin)

    # Session helpers
    db.append_session(email, session_data) # push one session dict
    db.update_sessions(email, sessions)    # replace entire sessions list
    db.update_badges(email, badges)        # replace badges list
    db.delete_field(email, field)          # remove a top-level field
"""

import json
import os
import sqlite3
import threading

# ──────────────────────────────────────────────────────────────────── constants
FIREBASE_JSON = "firebase.json"
SQLITE_FILE   = "fitness.db"

# ─────────────────────────────────────────────────────────────────── detection
def _firebase_available():
    """Return True only when firebase.json exists and is parseable."""
    if not os.path.isfile(FIREBASE_JSON):
        return False
    try:
        with open(FIREBASE_JSON) as f:
            data = json.load(f)
        # basic sanity: must have a project_id key or be a service account
        return bool(data.get("project_id") or data.get("type") == "service_account")
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════════ Firebase DB
class FirebaseDB:
    """Thin wrapper around firebase_admin + firestore."""

    def __init__(self):
        import firebase_admin
        from firebase_admin import auth as fb_auth, credentials, firestore
        self._auth = fb_auth
        self._fs   = firestore

        cred = credentials.Certificate(FIREBASE_JSON)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        self._client = firestore.client()
        self.mode = "firebase"
        print("[db_layer] Using Firebase Firestore")

    # ── internal
    def _ref(self, email):
        return self._client.collection("users").document(email)

    # ── User CRUD ────────────────────────────────────────────────────────────
    def get_user(self, email):
        doc = self._ref(email).get()
        return doc.to_dict() if doc.exists else None

    def set_user(self, email, data):
        self._ref(email).set(data)

    def update_user(self, email, data):
        self._ref(email).update(data)

    def user_exists(self, email):
        return self._ref(email).get().exists

    def all_users(self):
        users = []
        for doc in self._client.collection("users").stream():
            u = doc.to_dict()
            u.setdefault("email", doc.id)
            u.pop("password", None)
            u.pop("password_hash", None)
            users.append(u)
        return users

    # ── Session helpers ──────────────────────────────────────────────────────
    def append_session(self, email, session_data):
        self._ref(email).update({"sessions": self._fs.ArrayUnion([session_data])})

    def update_sessions(self, email, sessions):
        self._ref(email).update({"sessions": sessions})

    def update_badges(self, email, badges):
        self._ref(email).update({"badges": badges})

    def delete_field(self, email, field):
        self._ref(email).update({field: self._fs.DELETE_FIELD})

    # ── Firebase-only Auth helpers (used by auth routes) ────────────────────
    def firebase_get_user_by_email(self, email):
        return self._auth.get_user_by_email(email)

    def firebase_create_user(self, email, password):
        return self._auth.create_user(email=email, password=password)

    def firebase_generate_reset_link(self, email):
        return self._auth.generate_password_reset_link(email)


# ══════════════════════════════════════════════════════════════════ SQLite DB
class SQLiteDB:
    """
    Offline SQLite backend.  Schema:
        users  (email TEXT PK, data TEXT)   – stores the entire user dict as JSON
    """

    _local = threading.local()

    def __init__(self, path=SQLITE_FILE):
        self._path = path
        self._init_db()
        self.mode = "sqlite"
        print("[db_layer] firebase.json not found/invalid -> SQLite offline mode ({})".format(path))

    # ── connection (one per thread) ──────────────────────────────────────────
    def _conn(self):
        if not getattr(self._local, "conn", None):
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    def close(self):
        conn = getattr(self._local, "conn", None)
        if conn:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    def _init_db(self):
        with sqlite3.connect(self._path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    email TEXT PRIMARY KEY,
                    data  TEXT NOT NULL
                )
            """)
            conn.commit()

    # ── internal helpers ─────────────────────────────────────────────────────
    def _read(self, email):
        row = self._conn().execute(
            "SELECT data FROM users WHERE email = ?", (email,)
        ).fetchone()
        return json.loads(row["data"]) if row else None

    def _write(self, email, data):
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO users (email, data) VALUES (?, ?)",
            (email, json.dumps(data, default=str))
        )
        conn.commit()

    # ── User CRUD ────────────────────────────────────────────────────────────
    def get_user(self, email):
        return self._read(email)

    def set_user(self, email, data):
        self._write(email, data)

    def update_user(self, email, data):
        existing = self._read(email) or {}
        existing.update(data)
        self._write(email, existing)

    def user_exists(self, email):
        return self._read(email) is not None

    def all_users(self):
        rows = self._conn().execute("SELECT data FROM users").fetchall()
        users = []
        for row in rows:
            u = json.loads(row["data"])
            u.pop("password", None)
            u.pop("password_hash", None)
            users.append(u)
        return users

    # ── Session helpers ──────────────────────────────────────────────────────
    def append_session(self, email, session_data):
        user = self._read(email) or {}
        sessions = user.get("sessions", [])
        sessions.append(session_data)
        user["sessions"] = sessions
        self._write(email, user)

    def update_sessions(self, email, sessions):
        user = self._read(email) or {}
        user["sessions"] = sessions
        self._write(email, user)

    def update_badges(self, email, badges):
        user = self._read(email) or {}
        user["badges"] = badges
        self._write(email, user)

    def delete_field(self, email, field):
        user = self._read(email) or {}
        user.pop(field, None)
        self._write(email, user)

    # ── Stub auth helpers (no Firebase Auth in offline mode) ─────────────────
    def firebase_get_user_by_email(self, email):
        """In offline mode we validate purely via the password_hash in SQLite."""
        user = self._read(email)
        if user is None:
            raise LookupError("User not found: {}".format(email))
        return user   # return the dict as a stand-in for a Firebase user record

    def firebase_create_user(self, email, password):
        """No-op: SQLite auth is handled purely by password_hash in user dict."""
        pass

    def firebase_generate_reset_link(self, email):
        """Offline stub – cannot send real reset emails without Firebase."""
        raise NotImplementedError("Password reset emails require Firebase connectivity")


# ══════════════════════════════════════════════════════════════════ factory
_db_instance = None
_db_lock     = threading.Lock()


def get_db():
    """Return the singleton DB (Firebase or SQLite, decided once at startup)."""
    global _db_instance
    if _db_instance is None:
        with _db_lock:
            if _db_instance is None:
                if _firebase_available():
                    _db_instance = FirebaseDB()
                else:
                    _db_instance = SQLiteDB()
    return _db_instance
