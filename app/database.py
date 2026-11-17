"""
Database and Persistence Layer (SQLite + JSON)
F1: Project management (New / Save / Load JSON / SQLite) + Auto-save & Resume
"""

import json
import sqlite3
import os
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Any
from app.config import DATA_DIR, DEFAULT_DB_PATH, TEMP_DIR
from app.models import Project, Segment, Speaker, GlossaryItem

class DatabaseManager:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.autosave_file = DATA_DIR / "autosave_project.json"
        self._init_db()

    def _init_db(self):
        """Initialize SQLite tables for project history, glossary, and settings."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Projects history table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recent_projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    file_path TEXT UNIQUE NOT NULL,
                    video_path TEXT,
                    segment_count INTEGER DEFAULT 0,
                    last_opened REAL NOT NULL
                )
            """)
            
            # Global character / terms glossary
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS global_glossary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    src_term TEXT UNIQUE NOT NULL,
                    km_term TEXT NOT NULL,
                    category TEXT DEFAULT 'Character'
                )
            """)
            
            # Application key-value config
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS app_kv_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            conn.commit()

    # --- Project File Persistence (JSON / SQLite) ---
    def save_project_to_file(self, project: Project, file_path: str) -> bool:
        """Save project state as a JSON file (.aidub / .json)."""
        try:
            project.project_file_path = file_path
            data = project.to_dict()
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            self.record_recent_project(project.name, file_path, project.video_path, len(project.segments))
            return True
        except Exception as e:
            print(f"[Error] Failed to save project: {e}")
            return False

    def load_project_from_file(self, file_path: str) -> Optional[Project]:
        """Load project state from a JSON file (.aidub / .json)."""
        try:
            if not os.path.exists(file_path):
                return None
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            project = Project.from_dict(data)
            project.project_file_path = file_path
            self.record_recent_project(project.name, file_path, project.video_path, len(project.segments))
            return project
        except Exception as e:
            print(f"[Error] Failed to load project: {e}")
            return None

    # --- Auto-save and Recovery ---
    def auto_save_project(self, project: Project) -> bool:
        """Auto-save current working project so user can resume after crash or restart."""
        try:
            data = project.to_dict()
            with open(self.autosave_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"[Warn] Auto-save error: {e}")
            return False

    def has_autosave(self) -> bool:
        return self.autosave_file.exists() and self.autosave_file.stat().st_size > 20

    def load_autosave(self) -> Optional[Project]:
        if not self.has_autosave():
            return None
        try:
            with open(self.autosave_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return Project.from_dict(data)
        except Exception as e:
            print(f"[Error] Failed to load autosave: {e}")
            return None

    def clear_autosave(self):
        if self.autosave_file.exists():
            try:
                self.autosave_file.unlink()
            except Exception:
                pass

    # --- Recent Projects History ---
    def record_recent_project(self, name: str, file_path: str, video_path: str, seg_count: int):
        import time
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO recent_projects (name, file_path, video_path, segment_count, last_opened)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    name = excluded.name,
                    video_path = excluded.video_path,
                    segment_count = excluded.segment_count,
                    last_opened = excluded.last_opened
            """, (name, file_path, video_path, seg_count, time.time()))
            conn.commit()

    def get_recent_projects(self, limit: int = 10) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT name, file_path, video_path, segment_count, last_opened 
                FROM recent_projects 
                ORDER BY last_opened DESC 
                LIMIT ?
            """, (limit,))
            return [dict(r) for r in cursor.fetchall()]

    # --- Global Glossary ---
    def get_global_glossary(self) -> List[GlossaryItem]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT src_term, km_term, category FROM global_glossary ORDER BY src_term ASC")
            return [GlossaryItem(src_term=r[0], km_term=r[1], category=r[2]) for r in cursor.fetchall()]

    def save_global_glossary_item(self, item: GlossaryItem):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO global_glossary (src_term, km_term, category)
                VALUES (?, ?, ?)
                ON CONFLICT(src_term) DO UPDATE SET
                    km_term = excluded.km_term,
                    category = excluded.category
            """, (item.src_term, item.km_term, item.category))
            conn.commit()

    def delete_global_glossary_item(self, src_term: str):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM global_glossary WHERE src_term = ?", (src_term,))
            conn.commit()

    # --- App Configuration Storage ---
    def set_config(self, key: str, value: str):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO app_kv_config (key, value) VALUES (?, ?)", (key, value))
            conn.commit()

    def get_config(self, key: str, default: str = "") -> str:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM app_kv_config WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else default
