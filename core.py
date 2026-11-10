from __future__ import annotations

import hashlib
import json
import csv
import zipfile
import os
import re
import sqlite3
import shutil
import uuid
import unicodedata
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

HOME = Path(os.environ.get('FB_POSTER_HOME', str(Path.home() / 'FB_Auto_Post_V4')))
DB_PATH = HOME / 'cong_viec.sqlite3'
DRAFT_PATH = HOME / 'ban_nhap.json'
BROWSER_DIR = HOME / 'trinh_duyet'
EVIDENCE_DIR = HOME / 'bang_chung'
MAX_GROUPS = None  # Không giới hạn số nhóm; Facebook vẫn có thể áp dụng giới hạn riêng.
MAX_IMAGES = 20

QUEUED = 'QUEUED'
PREPARING = 'PREPARING'
SUBMITTING = 'SUBMITTING'
OBSERVED = 'OBSERVED'
PENDING = 'PENDING'
ACKNOWLEDGED = 'ACKNOWLEDGED'
REVIEW = 'REVIEW'
CONFIRMED = 'CONFIRMED'
FAILED = 'FAILED'  
SKIPPED = 'SKIPPED'
LABELS = {
    QUEUED: 'Chưa đăng', PREPARING: 'Đang chuẩn bị',
    SUBMITTING: 'Đang gửi', OBSERVED: 'Đã phát hiện nội dung bài trên nhóm',
    PENDING: 'Đã gửi · Chờ duyệt', ACKNOWLEDGED: 'Facebook báo đã nhận', REVIEW: 'Đã nhấn Đăng · Cần đối chiếu',
    CONFIRMED: 'Thành công', FAILED: 'Bỏ qua · Lỗi chưa gửi',
    SKIPPED: 'Đã bỏ qua',
}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def parse_local_datetime(value: str) -> datetime:
    """Parse an ISO datetime or YYYY-MM-DD HH:MM as local Windows time."""
    raw = (value or '').strip()
    parsed = None
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S'):
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        raise ValueError('Ngày giờ không hợp lệ. Hãy dùng YYYY-MM-DD HH:MM, ví dụ 2026-10-31 17:30.')
    local_now = datetime.now().astimezone()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_now.tzinfo)
    return parsed.astimezone()


def clean_group(url: str) -> str:
    value = url.strip()
    if not value:
        raise ValueError('Đường dẫn nhóm trống.')
    if not value.startswith(('http://', 'https://')):
        value = 'https://' + value
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or '').lower()
        if (parsed.scheme != 'https' or hostname not in {'facebook.com', 'www.facebook.com', 'm.facebook.com'}
                or parsed.username or parsed.password or parsed.port):
            raise ValueError('Chỉ chấp nhận https://www.facebook.com/groups/...')
        match = re.fullmatch(r'/groups/([a-zA-Z0-9_.-]+)/?', parsed.path)
        if not match:
            raise ValueError('Liên kết phải là liên kết gốc của nhóm, không phải bài viết.')
        slug = match.group(1)
        if slug.lower() in {'feed', 'discover', 'create', 'joins'}:
            raise ValueError('Đây không phải địa chỉ nhóm hợp lệ.')
    except (UnicodeError, ValueError) as e:
        raise ValueError(str(e)) from e
    return 'https://www.facebook.com/groups/' + slug.lower()


def clean_groups(text: str) -> list[str]:
    urls = []
    seen = set()
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            url = clean_group(raw)
        except ValueError as exc:
            raise ValueError(f'Dòng {n}: {exc}') from exc
        if url not in seen:
            urls.append(url)
            seen.add(url)
    if not urls:
        raise ValueError('Bạn chưa nhập nhóm Facebook.')
    return urls


def validate_images(images: list[str]) -> list[str]:
    if len(images) > MAX_IMAGES:
        raise ValueError(f'Tối đa {MAX_IMAGES} ảnh/bài.')
    result = []
    for image in images:
        p = Path(image).expanduser().resolve()
        if not p.is_file() or p.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp'}:
            raise ValueError(f'Không tìm thấy ảnh JPG/PNG/WEBP: {image}')
        if p.stat().st_size > 500 * 1024 * 1024:
            raise ValueError(f'Ảnh lớn hơn 500 MB: {p.name}')
        result.append(str(p))
    return result


def norm_text(value: str) -> str:
    """Unicode-safe comparison: whitespace, zero-width and VS16 may change in editor."""
    value = unicodedata.normalize('NFC', value or '')
    return ''.join(ch for ch in value if not ch.isspace() and ch not in '\u200b\ufeff\ufe0f\ufe0e')


def text_is_complete(expected: str, actual: str) -> bool:
    normalized = norm_text(expected)
    return bool(normalized) and normalized in norm_text(actual)


def payload_digest(content: str, images: list[str]) -> str:
    """Contents + file *bytes* prevent duplicate posting even if image paths change."""
    h = hashlib.sha256()
    h.update(unicodedata.normalize('NFC', content).encode('utf-8'))
    for filename in images:
        h.update(b'\0IMAGE\0')
        with open(filename, 'rb') as f:
            for part in iter(lambda: f.read(1024 * 1024), b''):
                h.update(part)
    return h.hexdigest()


@dataclass(frozen=True)
class Job:
    id: int
    group_url: str
    content: str
    images: list[str]
    status: str
    note: str
    sent_intent_at: str | None
    created_at: str
    permalink: str
    evidence_path: str


class Store:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_url TEXT NOT NULL,
                    content TEXT NOT NULL,
                    images_json TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    sent_intent_at TEXT,
                    permalink TEXT NOT NULL DEFAULT '',
                    evidence_path TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
                CREATE TABLE IF NOT EXISTS saved_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    groups_text TEXT NOT NULL,
                    images_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scheduled_posts (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    groups_text TEXT NOT NULL,
                    images_json TEXT NOT NULL,
                    scheduled_at TEXT NOT NULL,
                    delay_seconds INTEGER NOT NULL DEFAULT 15,
                    status TEXT NOT NULL DEFAULT 'scheduled',
                    job_ids_json TEXT NOT NULL DEFAULT '[]',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_scheduled_status_time ON scheduled_posts(status, scheduled_at);
                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS activity_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'Hoạt động',
                    message TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_log(created_at);
            ''')
            # A scheduled run interrupted by closing/crashing the app must never silently repost.
            db.execute("UPDATE scheduled_posts SET status='needs_review',error=?,updated_at=? WHERE status='running'",
                       ('Ứng dụng đóng khi lịch đang chạy. Kiểm tra Lịch sử Facebook trước khi tạo lịch mới để tránh đăng trùng.', now()))
            # PREPARING has not clicked yet (SUBMITTING is committed immediately before click).
            # Distinguish them on crash recovery to avoid false 'sent' or automatic retry.
            db.execute("UPDATE jobs SET status=?,note=?,updated_at=? WHERE status=?",
                       (FAILED, 'Ứng dụng đóng ở bước chuẩn bị, chưa nhấn Đăng.', now(), PREPARING))
            db.execute("UPDATE jobs SET status=?,note=?,updated_at=? WHERE status=?",
                       (REVIEW, 'Ứng dụng đóng trong lúc nhấn Đăng; có thể đã gửi, không thử lại.',
                        now(), SUBMITTING))

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=10000')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def row_job(row) -> Job:
        return Job(id=row['id'], group_url=row['group_url'], content=row['content'],
                   images=json.loads(row['images_json']), status=row['status'],
                   note=row['note'], sent_intent_at=row['sent_intent_at'],
                   created_at=row['created_at'], permalink=row['permalink'],
                   evidence_path=row['evidence_path'])

    def add_activity(self, category: str, message: str) -> None:
        message = (message or '').strip()
        if not message:
            return
        with self._db() as db:
            db.execute('INSERT INTO activity_log(created_at,category,message) VALUES(?,?,?)',
                       (now(), (category or 'Hoạt động')[:60], message[:2000]))

    def list_activity(self, limit: int = 1000) -> list[dict]:
        with self._db() as db:
            rows = db.execute('SELECT id,created_at,category,message FROM activity_log '
                              'ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        return [dict(row) for row in rows]

    def clear_activity(self) -> None:
        with self._db() as db:
            db.execute('DELETE FROM activity_log')

    def add_jobs(self, content: str, images: list[str], groups: list[str], *,
                 repeat_key: str | None = None) -> tuple[list[int], list[str]]:
        
        if not content.strip():
            raise ValueError('Bạn chưa nhập nội dung chữ.')
        images = validate_images(images)
        digest = payload_digest(content, images)
        created, duplicate = [], []
        with self._db() as db:
            for group in groups:
                seed = group + '\n' + digest
                if repeat_key:
                    seed += '\nREPEAT:' + repeat_key
                identity = hashlib.sha256(seed.encode()).hexdigest()
                cur = db.execute('''INSERT OR IGNORE INTO jobs (
                     group_url,content,images_json,fingerprint,status,created_at,updated_at)
                     VALUES (?,?,?,?,?,?,?)''',
                     (group, content, json.dumps(images, ensure_ascii=False), identity,
                      QUEUED, now(), now()))
                if cur.rowcount:
                    created.append(cur.lastrowid)
                elif repeat_key:
                    
                    row = db.execute('SELECT id FROM jobs WHERE fingerprint=?', (identity,)).fetchone()
                    if row is not None:
                        created.append(int(row['id']))
                else:
                    duplicate.append(group)
        return created, duplicate

    def latest_sent_intent_for_payload(self, content: str, images: list[str], exclude_id: int | None = None) -> str | None:
        images_json = json.dumps(images, ensure_ascii=False)
        with self._db() as db:
            if exclude_id is None:
                row = db.execute('''SELECT sent_intent_at FROM jobs
                    WHERE content=? AND images_json=? AND sent_intent_at IS NOT NULL
                    ORDER BY sent_intent_at DESC LIMIT 1''', (content, images_json)).fetchone()
            else:
                row = db.execute('''SELECT sent_intent_at FROM jobs
                    WHERE content=? AND images_json=? AND sent_intent_at IS NOT NULL AND id<>?
                    ORDER BY sent_intent_at DESC LIMIT 1''', (content, images_json, exclude_id)).fetchone()
        return row['sent_intent_at'] if row else None

    def get_app_state(self, key: str) -> str | None:
        with self._db() as db:
            row = db.execute('SELECT value FROM app_state WHERE key=?', (key,)).fetchone()
        return row['value'] if row else None

    def set_app_state(self, key: str, value: str) -> None:
        with self._db() as db:
            db.execute('''INSERT INTO app_state(key, value) VALUES(?, ?)
                          ON CONFLICT(key) DO UPDATE SET value=excluded.value''', (key, value))

    def get(self, job_id: int) -> Job:
        with self._db() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self.row_job(row)

    def all(self, limit: int = 300) -> list[Job]:
        with self._db() as db:
            rows = db.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        return [self.row_job(row) for row in rows]

    def transition(self, job_id: int, status: str, note: str = '', *, permalink: str | None = None,
                   evidence_path: str | None = None, submitted: bool = False) -> None:
        changes = ['status=?', 'note=?', 'updated_at=?']
        params: list[object] = [status, note, now()]
        if submitted:
            changes.append('sent_intent_at=?')
            params.append(now())
        if permalink is not None:
            changes.append('permalink=?')
            params.append(permalink)
        if evidence_path is not None:
            changes.append('evidence_path=?')
            params.append(evidence_path)
        params.append(job_id)
        with self._db() as db:
            db.execute(f"UPDATE jobs SET {','.join(changes)} WHERE id=?", params)

    def retry_before_submit(self, job_id: int) -> bool:
        """Only an explicit user action; no retry when click may have happened."""
        with self._db() as db:
            cur = db.execute('''UPDATE jobs SET status=?,note='',updated_at=?
                WHERE id=? AND status=? AND sent_intent_at IS NULL''',
                (QUEUED, now(), job_id, FAILED))
            return cur.rowcount == 1

    def confirm(self, job_id: int) -> None:
        with self._db() as db:
            db.execute('''UPDATE jobs SET status=?,note=?,updated_at=?
                WHERE id=? AND status IN (?,?,?,?)''',
                (CONFIRMED, 'Người dùng đã xem bài trên Facebook và xác nhận.', now(), job_id,
                 REVIEW, OBSERVED, PENDING, ACKNOWLEDGED))

    def skip(self, job_id: int) -> None:
        with self._db() as db:
            db.execute('UPDATE jobs SET status=?,note=?,updated_at=? WHERE id=? AND status=?',
                       (SKIPPED, 'Đã bỏ qua theo yêu cầu.', now(), job_id, QUEUED))

    def save_library_post(self, title: str, content: str, groups_text: str, images: list[str]) -> int:
        title = (title or '').strip()[:120]
        if not title:
            raise ValueError('Hãy nhập tên để dễ tìm lại bài.')
        if not (content or '').strip():
            raise ValueError('Nội dung bài đang trống.')
        clean_groups(groups_text)
        images = validate_images(images)
        created = now()
        with self._db() as db:
            cur = db.execute('INSERT INTO saved_posts(title,content,groups_text,images_json,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                             (title, content, groups_text, json.dumps([], ensure_ascii=False), created, created))
            post_id = int(cur.lastrowid)
        copied = []
        folder = self.path.parent / 'thu_vien_anh' / str(post_id)
        try:
            if images:
                folder.mkdir(parents=True, exist_ok=True)
                for i, src in enumerate(images, 1):
                    source = Path(src)
                    dest = folder / f'{i:02d}{source.suffix.lower()}'
                    shutil.copy2(source, dest)
                    copied.append(str(dest.resolve()))
            with self._db() as db:
                db.execute('UPDATE saved_posts SET images_json=?,updated_at=? WHERE id=?',
                           (json.dumps(copied, ensure_ascii=False), now(), post_id))
        except Exception:
            with self._db() as db:
                db.execute('DELETE FROM saved_posts WHERE id=?', (post_id,))
            if folder.exists():
                shutil.rmtree(folder, ignore_errors=True)
            raise
        return post_id

    def list_library_posts(self, limit: int = 300) -> list[dict]:
        with self._db() as db:
            rows = db.execute('SELECT * FROM saved_posts ORDER BY updated_at DESC,id DESC LIMIT ?', (limit,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item['images'] = json.loads(item.pop('images_json') or '[]')
            item['image_count'] = len(item['images'])
            result.append(item)
        return result

    def get_library_post(self, post_id: int) -> dict:
        with self._db() as db:
            row = db.execute('SELECT * FROM saved_posts WHERE id=?', (post_id,)).fetchone()
        if row is None:
            raise KeyError(post_id)
        item = dict(row)
        item['images'] = json.loads(item.pop('images_json') or '[]')
        return item

    def delete_library_post(self, post_id: int) -> None:
        with self._db() as db:
            row = db.execute('SELECT id FROM saved_posts WHERE id=?', (post_id,)).fetchone()
            if row is None:
                return
            db.execute('DELETE FROM saved_posts WHERE id=?', (post_id,))
        folder = self.path.parent / 'thu_vien_anh' / str(post_id)
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)

    def add_schedule(self, title: str, content: str, groups_text: str, images: list[str],
                     scheduled_at: str, delay_seconds: int = 15) -> str:
        title = (title or '').strip()[:120] or 'Bài đăng theo lịch'
        if not (content or '').strip():
            raise ValueError('Nội dung bài đang trống.')
        clean_groups(groups_text)
        images = validate_images(images)
        try:
            when = parse_local_datetime(scheduled_at)
        except (TypeError, ValueError) as exc:
            raise ValueError(str(exc) or 'Ngày giờ hẹn không hợp lệ. Dùng định dạng YYYY-MM-DD HH:MM.') from exc
        if when <= datetime.now().astimezone() + timedelta(seconds=10):
            raise ValueError('Thời gian hẹn phải ở tương lai ít nhất 10 giây.')
        schedule_id = uuid.uuid4().hex
        folder = self.path.parent / 'lich_hen_anh' / schedule_id
        copied = []
        try:
            if images:
                folder.mkdir(parents=True, exist_ok=True)
                for i, src in enumerate(images, 1):
                    source = Path(src)
                    dest = folder / f'{i:02d}{source.suffix.lower()}'
                    shutil.copy2(source, dest)
                    copied.append(str(dest.resolve()))
            stamp = now()
            with self._db() as db:
                db.execute('''INSERT INTO scheduled_posts
                    (id,title,content,groups_text,images_json,scheduled_at,delay_seconds,status,job_ids_json,error,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (schedule_id, title, content, groups_text, json.dumps(copied, ensure_ascii=False),
                     when.isoformat(timespec='seconds'), max(0, int(delay_seconds)), 'scheduled', '[]', '', stamp, stamp))
            return schedule_id
        except Exception:
            if folder.exists():
                shutil.rmtree(folder, ignore_errors=True)
            raise

    @staticmethod
    def _schedule_row(row) -> dict:
        item = dict(row)
        item['images'] = json.loads(item.pop('images_json') or '[]')
        item['job_ids'] = json.loads(item.pop('job_ids_json') or '[]')
        return item

    def list_schedules(self, limit: int = 300) -> list[dict]:
        with self._db() as db:
            rows = db.execute('SELECT * FROM scheduled_posts ORDER BY scheduled_at ASC,created_at DESC LIMIT ?', (limit,)).fetchall()
        return [self._schedule_row(row) for row in rows]

    def get_schedule(self, schedule_id: str) -> dict:
        with self._db() as db:
            row = db.execute('SELECT * FROM scheduled_posts WHERE id=?', (schedule_id,)).fetchone()
        if row is None:
            raise KeyError(schedule_id)
        return self._schedule_row(row)

    def claim_schedule(self, schedule_id: str) -> bool:
        with self._db() as db:
            cur = db.execute("UPDATE scheduled_posts SET status='running',error='',updated_at=? WHERE id=? AND status='scheduled'",
                             (now(), schedule_id))
            return cur.rowcount == 1

    def update_schedule(self, schedule_id: str, status: str | None = None, *,
                        job_ids: list[int] | None = None, error: str | None = None) -> None:
        changes, params = [], []
        if status is not None:
            changes.append('status=?'); params.append(status)
        if job_ids is not None:
            changes.append('job_ids_json=?'); params.append(json.dumps(job_ids))
        if error is not None:
            changes.append('error=?'); params.append(error)
        changes.append('updated_at=?'); params.append(now())
        params.append(schedule_id)
        with self._db() as db:
            db.execute(f"UPDATE scheduled_posts SET {','.join(changes)} WHERE id=?", params)

    def cancel_schedule(self, schedule_id: str) -> bool:
        with self._db() as db:
            cur = db.execute("UPDATE scheduled_posts SET status='cancelled',updated_at=? WHERE id=? AND status='scheduled'",
                             (now(), schedule_id))
            return cur.rowcount == 1

    def reschedule(self, schedule_id: str, scheduled_at: str) -> bool:
        try:
            when = parse_local_datetime(scheduled_at)
        except (TypeError, ValueError) as exc:
            raise ValueError(str(exc) or 'Ngày giờ không hợp lệ. Dùng YYYY-MM-DD HH:MM.') from exc
        if when <= datetime.now().astimezone() + timedelta(seconds=10):
            raise ValueError('Thời gian hẹn phải ở tương lai ít nhất 10 giây.')
        with self._db() as db:
            cur = db.execute("UPDATE scheduled_posts SET scheduled_at=?,updated_at=? WHERE id=? AND status='scheduled'",
                             (when.isoformat(timespec='seconds'), now(), schedule_id))
            return cur.rowcount == 1


def save_draft(text: str, groups: str, images: list[str]):
    DRAFT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DRAFT_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps({'content': text, 'groups': groups, 'images': images},
                              ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(DRAFT_PATH)


def load_draft() -> dict:
    """Prefer V4 data; one-time read-only fallback for previous V3 / V2.5 drafts."""
    blank = {'content': '', 'groups': '', 'images': []}
    if DRAFT_PATH.is_file():
        try:
            data = json.loads(DRAFT_PATH.read_text(encoding='utf-8'))
            return {key: data.get(key, blank[key]) for key in blank}
        except (OSError, ValueError, TypeError):
            return blank
    candidates = [
        (Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'FBPostDeDung' / 'du_lieu.json', 'v3'),
        (Path.home() / 'FacebookPostAssistant' / 'ban_nhap_v23.json', 'v2'),
    ]
    for path, source in candidates:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if source == 'v3':
                result = {'content': data.get('message', ''),
                          'groups': data.get('groups_text', ''),
                          'images': data.get('images', [])}
            else:
                config = path.parent / 'cau_hinh_v2.json'
                try:
                    settings = json.loads(config.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    settings = {}
                result = {'content': data.get('message', ''),
                          'groups': settings.get('groups', ''),
                          'images': data.get('images', [])}
            if not isinstance(result['images'], list):
                result['images'] = []
            if result['content'] or result['groups']:
                return result
        except (OSError, ValueError, TypeError):
            pass
    return blank

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

Progress = Callable[[str], None]

COMPOSER_RE = re.compile(
    r'(?:Viết\s+gì\s*(?:đó|đi)?|Bạn\s+(?:viết\s+gì\s*(?:đi|đó)?|đang\s+nghĩ\s+gì|muốn\s+chia\s+sẻ\s+gì)|'
    r'Tạo\s+(?:một\s+)?bài\s+viết|Viết\s+(?:một\s+)?bài(?:\s+viết)?|Chia\s+sẻ\s+gì\s*(?:đó)?|'
    r'Bắt\s+đầu\s+(?:một\s+)?(?:cuộc\s+)?thảo\s+luận|'
    r'Write\s+something|What[’\\\'`]?s\s+on\s+your\s+mind|'
    r'Create\s+(?:a\s+)?post|Write\s+(?:a\s+)?post|Share\s+something|Start\s+(?:a\s+)?discussion)',
    re.I,
)
PHOTO_RE = re.compile(r'^(?:Ảnh/video|Ảnh|Photo/video|Add photos/videos|Photo|Photos/Videos)$', re.I)
POST_RE = re.compile(r'^\s*(?:Đăng(?: bài)?|Post(?: now)?|Publish(?: post)?)\s*$', re.I)
PENDING_RE = re.compile(r'(đang chờ (phê )?duyệt|chờ quản trị viên|pending approval|pending admin approval)', re.I)
BUSY_RE = re.compile(r'(đang tải (ảnh|lên)|uploading|processing media)', re.I)
SUCCESS_RE = re.compile(r'(bài viết của bạn đã được (đăng|chia sẻ)|đã chia sẻ bài viết|\byour post (?:was|has been) (?:shared|posted|published)\b|post published successfully)', re.I)
NO_POST_RIGHTS_RE = re.compile(
    r'(?:chỉ\s+(?:quản\s+trị\s+viên|người\s+kiểm\s+duyệt).{0,45}(?:đăng|viết)|'
    r'bạn\s+không\s+(?:thể|được)\s+(?:đăng|viết)|'
    r'only\s+admins\s+can\s+post|you\s+can[’\\\'`]?t\s+post)', re.I
)


class PublishError(RuntimeError):
    pass


def first_visible(locators):
    for locator in locators:
        try:
            for index in range(min(locator.count(), 30)):
                obj = locator.nth(index)
                if obj.is_visible():
                    return obj
        except Exception:
            continue
    return None


def editor_value(editor) -> str:
    for fn in ('inner_text', 'text_content', 'input_value'):
        try:
            value = getattr(editor, fn)(timeout=1500)
            if value:
                return value
        except Exception:
            pass
    return ''


def _editor_is_post_input(editor) -> bool:
    """Không nhầm ô bình luận với ô soạn bài nhóm đang hiển thị."""
    try:
        clues = ' '.join((editor.get_attribute(a, timeout=750) or '') for a in
                         ('aria-label', 'data-placeholder', 'placeholder', 'data-testid'))
        return bool(COMPOSER_RE.search(clues)) and not re.search(r'bình luận|comment', clues, re.I)
    except Exception:
        return False


def _safe_inline_scope(editor):
    """Chỉ cho phép nút Đăng trong vùng chứa chính ô soạn bài (không phải nút ở feed)."""
    for selector in ('xpath=ancestor::form[1]',
                     'xpath=ancestor::*[@role="form"][1]',
                     'xpath=ancestor::*[@data-pagelet="Composer"][1]'):
        try:
            loc = editor.locator(selector)
            if loc.count() and loc.first.is_visible():
                return loc.first
        except Exception:
            pass
    
    for depth in (2, 3, 4):
        try:
            parent = editor.locator(f'xpath=ancestor::div[{depth}]')
            if parent.count() and parent.get_by_role('button', name=POST_RE).count():
                return parent
        except Exception:
            pass
    return None


def visible_editor(page):
    try:
        dialogs = page.locator('[role="dialog"], [aria-modal="true"]')
        for i in reversed(range(min(dialogs.count(), 8))):
            dlg = dialogs.nth(i)
            if dlg.is_visible():
                editor = first_visible([dlg.locator('[contenteditable="true"][role="textbox"]'),
                                        dlg.locator('[contenteditable="true"]')])
                if editor:
                    return dlg, editor
    except Exception:
        pass
    
    for selector in ('main [contenteditable="true"]',
                     '[role="main"] [contenteditable="true"]',
                     '[contenteditable="true"][role="textbox"]'):
        try:
            boxes = page.locator(selector)
            for i in range(min(boxes.count(), 25)):
                box = boxes.nth(i)
                if box.is_visible() and _editor_is_post_input(box):
                    return (_safe_inline_scope(box) or page), box
        except Exception:
            continue
    return None, None


def _composer_candidates(page):
    """Ưu tiên các nút trong main; dùng nhãn/placeholder trước, không click bài trong feed."""
    roots = []
    for css in ('[role="main"]', 'main'):
        try:
            loc = page.locator(css)
            if loc.count():
                roots.append(loc.first)
                break
        except Exception:
            pass
    roots.append(page)
    for root in roots:
        yield root.get_by_role('button', name=COMPOSER_RE)
        yield root.locator('[role="button"]').filter(has_text=COMPOSER_RE)
        # Có giao diện chỉ dùng div hoặc span thay vì role=button.
        yield root.get_by_text(COMPOSER_RE)
        yield root.locator('[aria-label]').filter(has_text=COMPOSER_RE)


def _wait_for_editor(page, seconds: float):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        scope, editor = visible_editor(page)
        if editor is not None:
            return scope, editor
        page.wait_for_timeout(350)
    return None, None


def composer_diagnostics(page, folder: Path, log: Progress):
    """Ghi nhãn bấm hiện tại để lần sau tìm được selector đúng mà không đoán."""
    try:
        data = page.evaluate("""() => {
          const visible = el => { const r = el.getBoundingClientRect();
            const st = getComputedStyle(el); return r.width > 0 && r.height > 0 &&
              st.display !== 'none' && st.visibility !== 'hidden'; };
          return [...document.querySelectorAll('button,[role="button"],[contenteditable="true"],[aria-label]')]
            .filter(visible).slice(0, 200).map(el => ({
              tag:el.tagName, role:el.getAttribute('role'),
              aria:el.getAttribute('aria-label'), placeholder:el.getAttribute('data-placeholder'),
              text:(el.innerText||'').trim().slice(0,100)
            }));
        }""")
        path = folder / 'chi_tiet_o_soan_bai.txt'
        path.write_text('URL: ' + page.url + '\n' + json.dumps(data, ensure_ascii=False, indent=2),
                        encoding='utf-8')
        log('Đã lưu thông tin các nút Facebook: ' + str(path))
    except Exception as exc:
        log('Không ghi được thông tin nút: ' + type(exc).__name__)


def open_composer(page, log: Progress, evidence_folder: Path | None = None):
    scope, editor = visible_editor(page)
    if editor is not None and scope != page:
        return scope, editor
    log('Đang tìm ô soạn bài trong nhóm (nhiều kiểu giao diện)...')
    
    for attempt in range(3):
        if attempt:
            page.wait_for_timeout(1200)
        scope, editor = visible_editor(page)
        if editor is not None and scope != page:
            return scope, editor
        for locator in _composer_candidates(page):
            try:
                count = min(locator.count(), 18)
            except Exception:
                continue
            for i in range(count):
                node = locator.nth(i)
                try:
                    if not node.is_visible():
                        continue
                    
                    if node.get_attribute('aria-disabled') == 'true':
                        continue
                    node.click(timeout=1800)
                    scope, editor = _wait_for_editor(page, 2.5)
                    if editor is not None and scope != page:
                        log('Đã mở được ô soạn bài Facebook.')
                        return scope, editor
                except Exception:
                    continue
        
        if attempt == 1:
            try:
                page.mouse.wheel(0, 500)
                page.wait_for_timeout(750)
            except Exception:
                pass
    if evidence_folder is not None:
        composer_diagnostics(page, evidence_folder, log)
    try:
        body = page.locator('body').inner_text(timeout=2200)[:20000]
    except Exception:
        body = ''
    if NO_POST_RIGHTS_RE.search(body):
        raise PublishError('Facebook thông báo tài khoản không có quyền tạo bài viết tại nhóm này.')
    raise PublishError('Không tìm thấy ô soạn bài trên nhóm. Hãy mở ảnh lỗi và chi_tiet_o_soan_bai.txt '
                       'để kiểm tra Facebook hiện nhãn gì; chưa gửi bài nào.')


def attach_images(page, scope, images: list[str], log: Progress):
    if not images:
        return
    log(f'Đang chọn {len(images)} ảnh...')
    file_inputs = scope.locator('input[type="file"]')
    if file_inputs.count() == 0:
        button = first_visible([
            scope.get_by_role('button', name=PHOTO_RE),
            scope.get_by_text(PHOTO_RE),
            scope.locator('[aria-label*="Ảnh/video"]'),
        ])
        if button is None:
            raise PublishError('Không tìm thấy chức năng Thêm ảnh/video của Facebook.')
        button.click(timeout=4500)
        page.wait_for_timeout(400)
    
        fresh_scope, _ = visible_editor(page)
        scope = fresh_scope or scope
        file_inputs = scope.locator('input[type="file"]')
        if file_inputs.count() == 0:
            file_inputs = page.locator('input[type="file"][accept*="image"]')
    if file_inputs.count() == 0:
        raise PublishError('Không xuất hiện ô chọn tệp ảnh của Facebook.')
    selected = False
    reason = ''
    for idx in reversed(range(min(file_inputs.count(), 12))):
        inp = file_inputs.nth(idx)
        try:
            accept = inp.get_attribute('accept', timeout=1000) or ''
            if accept and 'image' not in accept and '.jpg' not in accept and '.png' not in accept:
                continue
            
            inp.set_input_files(images, timeout=7000)
            selected = True
            
            log('Đã chọn tệp: ' + ', '.join(Path(p).name for p in images))
            break
        except Exception as exc:
            reason = str(exc).splitlines()[0][:180]
    if not selected:
        raise PublishError('Không thể chọn ảnh. ' + reason)
    
    log('Ảnh đã được chọn. Sẽ tiếp tục theo dõi trạng thái tải ảnh trước khi đăng.')


def fill_and_verify(page, content: str, log: Progress):
    """After attachments have been selected, find the new editor and enter text.
    Check *the current visible editor*, never a stale pre-upload locator.
    """
    scope, editor = visible_editor(page)
    if editor is None:
        raise PublishError('Không thấy ô soạn chữ sau khi chọn ảnh; chưa nhấn Đăng.')
    for attempt in range(2):
        try:
            editor.fill(content, timeout=5000)
        except Exception:
            editor.click(timeout=3000)
            page.keyboard.insert_text(content)
        page.wait_for_timeout(350)
        scope, new_editor = visible_editor(page)
        if new_editor is None:
            raise PublishError('Ô soạn bài biến mất sau khi tải ảnh.')
        if text_is_complete(content, editor_value(new_editor)):
            editor = new_editor
            break
        log(f'Facebook làm mất chữ ở lần nhập {attempt + 1}; đang khôi phục...')
        editor = new_editor
    else:
        raise PublishError('Không thể giữ nguyên chữ/emoji sau khi tải ảnh; hủy trước khi nhấn Đăng.')
    for _ in range(2):
        page.wait_for_timeout(350)
        scope, editor = visible_editor(page)
        if editor is None or not text_is_complete(content, editor_value(editor)):
            raise PublishError('Chữ không ổn định trong ô soạn; chưa nhấn Đăng để tránh bài chỉ có ảnh.')
    log('Đã kiểm tra chữ + emoji nhiều lần sau khi chọn ảnh.')
    return scope


def publishing_button(scope, page):
    candidates = [scope.get_by_role('button', name=POST_RE),
                  scope.locator('[role="button"][aria-label="Đăng"]'),
                  scope.locator('[role="button"][aria-label="Post"]')]
    if scope == page:
        
        return None
    for locator in candidates:
        try:
            for i in reversed(range(min(locator.count(), 12))):
                node = locator.nth(i)
                if (node.is_visible() and node.is_enabled()
                        and node.get_attribute('aria-disabled') != 'true'):
                    return node
        except Exception:
            pass
    return None


def wait_publish_ready(page, content: str, seconds: int, log: Progress):
    deadline = time.monotonic() + seconds
    last_note = ''
    while time.monotonic() < deadline:
        scope, editor = visible_editor(page)
        if editor is None or not text_is_complete(content, editor_value(editor)):
            raise PublishError('Nội dung chữ bị mất khi Facebook xử lý ảnh; chưa nhấn Đăng.')
        # Detect obvious media-processing labels in composer, wait rather than click.
        try:
            busy = scope.get_by_text(BUSY_RE).first
            if busy.count() and busy.is_visible():
                last_note = 'Facebook vẫn đang xử lý ảnh'
                page.wait_for_timeout(650)
                continue
        except Exception:
            pass
        button = publishing_button(scope, page)
        if button is not None:
            return button
        last_note = 'Nút Đăng chưa sẵn sàng (có thể do quyền đăng hoặc giao diện Facebook)'
        page.wait_for_timeout(650)
    raise PublishError(f'{last_note}. Hết {seconds} giây; chưa nhấn Đăng.')


def _find_published_permalink(page, content: str) -> str:
    """Bằng chứng yếu hơn xác nhận ảnh: chữ của bài + permalink cùng một article."""
    excerpt = norm_text(content)[:150]
    if len(excerpt) < 20:
        return ''
    articles = page.locator('[role="article"]')
    for index in range(min(articles.count(), 24)):
        try:
            article = articles.nth(index)
            if not article.is_visible() or excerpt not in norm_text(article.inner_text(timeout=1200)):
                continue
            hrefs = article.locator('a[href*="/posts/"], a[href*="/permalink/"]').evaluate_all(
                '(list) => list.map(a => a.href).filter(Boolean)')
            if hrefs:
                return hrefs[0]
        except Exception:
            continue
    return ''


def observe_publish(page, content: str, group: str, log: Progress) -> tuple[str, str, str]:
    """Wait for post composer to finish rather than navigating away too early.

    A click is *not* proof of publication. If the post may still be uploading,
    leave it as REVIEW instead of automatically resubmitting it.
    """
    deadline = time.monotonic() + 5.0
    ui_success = False
    composer_still_contains_post = False
    while time.monotonic() < deadline:
        try:
            alerts = page.locator('[role="alert"], [role="status"], [aria-live="polite"]')
            feedback = ' '.join(alerts.all_inner_texts(timeout=1000))
            if PENDING_RE.search(feedback):
                return PENDING, 'Facebook thông báo bài đang chờ duyệt.', ''
            if SUCCESS_RE.search(feedback):
                ui_success = True
        except Exception:
            pass
        try:
            dialogs_text = ' '.join(page.locator('[role="dialog"]').all_inner_texts(timeout=1000))
            if PENDING_RE.search(dialogs_text):
                return PENDING, 'Facebook thông báo bài đang chờ duyệt.', ''
        except Exception:
            pass
        if ui_success:
            break
        try:
            _, editor = visible_editor(page)
            composer_still_contains_post = bool(editor and text_is_complete(content, editor_value(editor)))
        except Exception:
            composer_still_contains_post = False
        if not composer_still_contains_post:
            break
        page.wait_for_timeout(300)

    if ui_success:
        # Acknowledged is deliberately NOT the same as confirmed with images.
        return ACKNOWLEDGED, 'Facebook phản hồi đã nhận bài; chưa đối chiếu đủ chữ và ảnh.', ''

    if composer_still_contains_post:
        # Do not reload and risk canceling an unfinished upload.
        return REVIEW, ('Đã nhấn Đăng nhưng ô soạn vẫn còn nội dung; có thể Facebook đang xử lý. '
                        'Không tự đăng lại nhóm này.'), ''

    # Only check feed after editor is gone. Try the current page first.
    try:
        permalink = _find_published_permalink(page, content)
        if permalink:
            return OBSERVED, 'Đã tìm thấy nội dung chữ và liên kết bài; số ảnh cần đối chiếu.', permalink
    except Exception:
        pass
    try:
        page.goto(group, wait_until='domcontentloaded', timeout=15000)
        page.wait_for_timeout(550)
        permalink = _find_published_permalink(page, content)
        if permalink:
            return OBSERVED, 'Đã tìm thấy nội dung chữ và liên kết bài; số ảnh cần đối chiếu.', permalink
    except Exception as exc:
        log('Chưa kiểm chứng được bài trên bảng tin: ' + type(exc).__name__)
    return REVIEW, ('Đã nhấn Đăng nhưng chưa thu được bằng chứng đủ để xác nhận bài. '
                    'KHÔNG tự đăng lại; nếu bạn nhìn thấy bài, dùng nút ĐÃ THẤY BÀI.'), ''


def screenshot(page, folder: Path, name: str, log: Progress) -> None:
    try:
        page.screenshot(path=str(folder / name), full_page=False, timeout=4000)
    except Exception as exc:
        log(f'Không chụp được {name}: {type(exc).__name__}')


def _run_one_on_page(job: Job, store: Store, log: Progress, page, ready_timeout: int = 18) -> str:
    """Thao tác một nhóm trong phiên Chrome đã mở; không click lại sau lỗi mơ hồ."""
    folder = EVIDENCE_DIR / f'bai_{job.id}_{datetime.now():%Y%m%d_%H%M%S}'
    folder.mkdir(parents=True, exist_ok=True)
    store.transition(job.id, PREPARING, 'Đang mở nhóm...', evidence_path=str(folder))
    had_submit_intent = False
    try:
        page.set_default_timeout(4500)
        log('Đang mở nhóm: ' + job.group_url)
        page.goto(job.group_url, wait_until='domcontentloaded', timeout=15000)
        # No forced 1.2s sleep; open_composer handles late-rendering groups.
        if ('/login' in page.url or 'checkpoint' in page.url or
                (page.locator('input[name="email"]').count() and
                 page.locator('input[name="pass"]').count())):
            raise PublishError('Facebook chưa đăng nhập hoặc yêu cầu xác minh. Nhấn Đăng nhập trước.')
        scope, _ = open_composer(page, log, folder)
        attach_images(page, scope, job.images, log)
        fill_and_verify(page, job.content, log)
        screenshot(page, folder, '01_truoc_khi_dang.png', log)
        log('Kiểm tra nút Đăng và ảnh đã sẵn sàng...')
        button = wait_publish_ready(page, job.content, ready_timeout, log)
        scope, editor = visible_editor(page)
        if editor is None or not text_is_complete(job.content, editor_value(editor)):
            raise PublishError('Nội dung chữ biến mất ngay trước khi Đăng; đã chặn gửi bài chỉ có ảnh.')
        # Commit BEFORE clicking, so crashes never generate a blind retry.
        had_submit_intent = True
        store.transition(job.id, SUBMITTING,
                         'Đã ghi ý định nhấn Đăng; không tự thử lại nếu kết quả chưa rõ.', submitted=True)
        log('Nhấn Đăng một lần duy nhất...')
        button.click(timeout=6500)
        status, note, permalink = observe_publish(page, job.content, job.group_url, log)
        screenshot(page, folder, '02_sau_khi_dang.png', log)
        (folder / 'ket_qua.txt').write_text(
            f'Nhom: {job.group_url}\nTrang thai: {status}\n{note}\nLien ket: {permalink}\n',
            encoding='utf-8')
        store.transition(job.id, status, note, permalink=permalink)
        log('Kết quả: ' + note)
        return status
    except Exception as exc:
        screenshot(page, folder, 'loi_hien_thi.png', log)
        detail = f'{type(exc).__name__}: {str(exc).splitlines()[0][:360]}'
        if had_submit_intent:
            store.transition(job.id, REVIEW, detail + ' Đã có thao tác Đăng; không thử lại nhóm này.')
            log('CHƯA XÁC MINH (không phải kết luận đăng thất bại): ' + detail)
            return REVIEW
        store.transition(job.id, FAILED, detail + ' Chưa bấm Đăng.')
        log('BỎ QUA TRƯỚC KHI GỬI: ' + detail)
        return FAILED


@contextmanager
def browser_batch_session():
    """Dùng MỘT Chromium cho cả lượt, thay vì tốn thời gian mở Chrome từng nhóm."""
    from playwright.sync_api import sync_playwright
    BROWSER_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(BROWSER_DIR), headless=False, locale='vi-VN',
            viewport={'width': 1290, 'height': 860},
            accept_downloads=False, timeout=16000)
        try:
            yield context.pages[0] if context.pages else context.new_page()
        finally:
            context.close()


def run_one(job: Job, store: Store, log: Progress, ready_timeout: int = 18, *, page=None) -> str:
    """Chạy đơn lẻ hoặc tận dụng trình duyệt chung (tương thích code dùng run_one cũ)."""
    if page is not None:
        return _run_one_on_page(job, store, log, page, ready_timeout)
    with browser_batch_session() as session_page:
        return _run_one_on_page(job, store, log, session_page, ready_timeout)


# Những trạng thái cuối cùng: đều cho phép chuyển NHÓM KHÁC khi bật tự chạy.
# REVIEW = có khả năng đã nhấn Đăng; chỉ chuyển nhóm, KHÔNG thử lại nhóm này.
# FAILED = chắc chắn chưa nhấn Đăng; ghi nhận lỗi và bỏ qua, không tự thử lại.
FINAL_STATES = {OBSERVED, PENDING, ACKNOWLEDGED, CONFIRMED, REVIEW, FAILED, SKIPPED}


def can_advance_automatically(status: str, enabled: bool, allow_unverified: bool = False) -> bool:
    """Không nhầm 'được chuyển nhóm' với 'đã đăng thành công'.

    allow_unverified giữ trong chữ ký để tương thích bản V6 nhưng không cần bật nữa:
    REVIEW và FAILED được lưu lại, không tự thử lại, rồi chuyển sang nhóm KHÁC.
    """
    return bool(enabled and status in FINAL_STATES)


def describe_outcome(status: str) -> str:
    if status == OBSERVED:
        return 'Đã thấy bài có chữ và liên kết trên nhóm; chưa đối chiếu toàn bộ ảnh.'
    if status == PENDING:
        return 'Facebook báo đã nhận bài và đang chờ duyệt.'
    if status == ACKNOWLEDGED:
        return 'Facebook báo đã nhận bài; chưa chứng minh đã hiển thị công khai.'
    if status == REVIEW:
        return 'Đã có thao tác Đăng, cần đối chiếu kết quả; không tự gửi lại nhóm này.'
    if status == FAILED:
        return 'Lỗi trước khi nhấn Đăng; bỏ qua nhóm này, không thử lại tự động.'
    if status == SKIPPED:
        return 'Đã bỏ qua.'
    return 'Chưa có kết quả cuối cùng, cần kiểm tra.'


def login_browser(log: Progress):
    """Log in yourself in the visible Chromium profile; app never asks for passwords."""
    from playwright.sync_api import sync_playwright
    BROWSER_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(BROWSER_DIR), headless=False, locale='vi-VN',
            viewport={'width': 1250, 'height': 850}, timeout=17000)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto('https://www.facebook.com/', wait_until='domcontentloaded', timeout=25000)
            log('Hãy đăng nhập Facebook trong Chrome, sau đó ĐÓNG cửa sổ Chrome để quay lại ứng dụng.')
            # User decides when to close. Maximum 10 minutes to avoid hanging forever.
            end = time.monotonic() + 600
            while time.monotonic() < end:
                try:
                    if not ctx.pages:
                        break
                except Exception:
                    break
                time.sleep(1)
        finally:
            ctx.close()
    log('Đã đóng trình duyệt đăng nhập. Phiên được lưu cục bộ trên máy này.')


