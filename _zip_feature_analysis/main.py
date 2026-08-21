from __future__ import annotations
import os
import shutil
import threading
import hashlib
import json
import time
import re
import random
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
import sys
from pathlib import Path

# 윈도우 GUI 실행을 위한 추가 모듈
import uvicorn
import webview

# DB 경로를 윈도우 로컬 폴더로 강제 지정 (models.py 로드 전 실행)
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent  # EXE 옆
else:
    BASE_DIR = Path(__file__).resolve().parent
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(BASE_DIR, 'library.db')}"

from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Query
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import inspect, text
from pydantic import BaseModel

from models import SessionLocal, engine, Base, FileRecord, ProcessLog
from services import PipelineService
from utils import (extract_smart_meta, get_file_hash, extract_epub_cover_and_title,
                   extract_zip_first_image, normalize, compose_basename, split_title_volume,
                   TEXT_DOC_EXTS, COMIC_EXTS, AUDIO_EXTS, ARCHIVE_EXTS)
from auto_organizer import start_folder_watcher

# 윈도우 데스크톱 환경에 맞춘 상대 경로 설정
TEMP_DIR = os.path.join(BASE_DIR, "temp_process")
FINAL_DIR = os.path.join(BASE_DIR, "final_storage")
ARCHIVE_DIR = os.path.join(BASE_DIR, "archive")
THUMB_DIR = os.path.join(BASE_DIR, "static", "thumbnails")
LOG_FILE = os.path.join(BASE_DIR, "pipeline.log")
STATIC_DIR = os.path.join(BASE_DIR, "static")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")

NON_TEXT_DIR = os.path.join(ARCHIVE_DIR, "[비텍스트_보류]")
COMIC_DIR = os.path.join(NON_TEXT_DIR, "[만화]")
AUDIO_DIR = os.path.join(NON_TEXT_DIR, "[음원]")
BLACKLIST_DIR = os.path.join(BASE_DIR, "blacklist")

for d in [TEMP_DIR, FINAL_DIR, ARCHIVE_DIR, THUMB_DIR, STATIC_DIR, NON_TEXT_DIR, COMIC_DIR, AUDIO_DIR, BLACKLIST_DIR]:
    os.makedirs(d, exist_ok=True)

Base.metadata.create_all(bind=engine)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

service = PipelineService(TEMP_DIR, FINAL_DIR, ARCHIVE_DIR, THUMB_DIR, BLACKLIST_DIR, LOG_FILE)

current_status = {
    "is_processing": False, "filename": "-", "hash_short": "-", "step": "대기 중",
    "progress_archive": 0, "progress_extract": 0, "progress_process": 0
}
_status_lock = threading.Lock()
_pipeline_lock = threading.Lock()

def load_settings():
    default = {
        "auto_scan": False, "scan_interval": 60, "lib_sync": False, "sync_interval": 60,
        "group_by": "initial", "rename_pattern": "prefix", "process_txt": True
        # identify_epub 옵션 완전 제거
    }
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f: default.update(json.load(f))
        except: pass
    return default

def save_settings(data: dict):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f: json.dump(data, f, indent=4)

class SettingsModel(BaseModel):
    auto_scan: bool; scan_interval: int; lib_sync: bool; sync_interval: int
    group_by: str; rename_pattern: str; process_txt: bool

class ReviewSubmitModel(BaseModel):
    series_name: str
    file_ids: list[int]
    rating: int
    comment: str
    mark_as_read: bool

def update_status_dict(filename=None, hash_short=None, step=None, is_processing=None, pa=None, pe=None, pp=None):
    with _status_lock:
        if filename is not None: current_status["filename"] = filename
        if hash_short is not None: current_status["hash_short"] = hash_short
        if step is not None: current_status["step"] = step
        if is_processing is not None: current_status["is_processing"] = is_processing
        if pa is not None: current_status["progress_archive"] = pa
        if pe is not None: current_status["progress_extract"] = pe
        if pp is not None: current_status["progress_process"] = pp

def extract_epub_internal_meta(epub_path: str):
    title, creator = None, None
    try:
        with zipfile.ZipFile(epub_path, 'r') as zf:
            if 'META-INF/container.xml' in zf.namelist():
                root = ET.fromstring(zf.read('META-INF/container.xml'))
                opf_path = next((elem.attrib.get('full-path') for elem in root.iter() if 'rootfile' in elem.tag.lower()), None)
                if opf_path and opf_path in zf.namelist():
                    opf_root = ET.fromstring(zf.read(opf_path))
                    for elem in opf_root.iter():
                        tag = elem.tag.lower()
                        if tag.endswith('title') and elem.text and not title: title = elem.text.strip()
                        elif tag.endswith('creator') and elem.text and not creator: creator = elem.text.strip()
    except: pass
    if title: title = normalize(title)
    if creator: creator = normalize(creator)
    return creator, title

def run_integrated_pipeline():
    if not _pipeline_lock.acquire(blocking=False): return
    try: _run_pipeline_inner()
    finally: _pipeline_lock.release()

def _run_pipeline_inner():
    update_status_dict(is_processing=True, pa=0, pe=0, pp=0)
    session = SessionLocal()
    settings = load_settings()
    try:
        initial_files = [f for f in os.listdir(TEMP_DIR) if os.path.isfile(os.path.join(TEMP_DIR, f))]
        if not initial_files: return

        blacklist_hashes = set()
        for f in os.listdir(BLACKLIST_DIR):
            bl_path = os.path.join(BLACKLIST_DIR, f)
            if os.path.isfile(bl_path): blacklist_hashes.add(get_file_hash(bl_path))

        update_status_dict(step="1단계: 스마트 아카이빙 백업 및 블랙리스트 차단")
        batch_folder = os.path.join(ARCHIVE_DIR, f"batch_{datetime.now().strftime('%Y%m%d_%H%M')}")
        os.makedirs(batch_folder, exist_ok=True)
        files_to_zip = []

        valid_initial_files = []
        for i, f in enumerate(initial_files):
            f_path = os.path.join(TEMP_DIR, f)
            f_hash = get_file_hash(f_path)
            if f_hash in blacklist_hashes:
                service.log_event(session, f"[블랙리스트 차단] 삭제: '{f}'", "WARNING")
                os.remove(f_path); continue

            valid_initial_files.append(f)
            ext = os.path.splitext(f)[1].lower()
            if ext in ARCHIVE_EXTS: shutil.copy2(f_path, os.path.join(batch_folder, f))
            else: files_to_zip.append(f)
            update_status_dict(pa=int(((i+1)/len(initial_files))*100))

        if files_to_zip:
            with zipfile.ZipFile(os.path.join(batch_folder, "files.zip"), 'w', zipfile.ZIP_DEFLATED) as zf:
                for f in files_to_zip: zf.write(os.path.join(TEMP_DIR, f), f)
        update_status_dict(pa=100)

        update_status_dict(step="2단계: 압축 분석 및 해제")
        queue = valid_initial_files.copy()
        to_process = []
        total_zips = sum(1 for f in queue if os.path.splitext(f)[1].lower() in ARCHIVE_EXTS and os.path.splitext(f)[1].lower() != '.epub')
        extracted_count = 0

        while queue:
            filename = queue.pop(0)
            file_path = os.path.join(TEMP_DIR, filename)
            if not os.path.exists(file_path): continue

            ext = os.path.splitext(filename)[1].lower()
            zip_base_name = os.path.splitext(filename)[0]

            if ext == '.epub':
                to_process.append((filename, "epub", zip_base_name)); continue

            if ext in ARCHIVE_EXTS:
                kind = service.classify_archive_content(file_path)

                if kind in ('comic', 'audio'):
                    sub_dir = COMIC_DIR if kind == 'comic' else AUDIO_DIR
                    os.makedirs(sub_dir, exist_ok=True)
                    safe_dest = service.get_safe_path(sub_dir, zip_base_name, ext, file_path)
                    shutil.move(file_path, safe_dest); continue

                if kind == 'empty':
                    safe_dest = service.get_safe_path(NON_TEXT_DIR, zip_base_name, ext, file_path)
                    shutil.move(file_path, safe_dest); continue

                safe_folder = f"ext_{hashlib.md5(filename.encode()).hexdigest()[:8]}"
                extract_target = os.path.join(TEMP_DIR, safe_folder)
                os.makedirs(extract_target, exist_ok=True)
                try:
                    with zipfile.ZipFile(file_path, 'r') as inner_zf:
                        for member in inner_zf.infolist():
                            try: decoded = member.filename.encode('cp437').decode('euc-kr')
                            except: decoded = member.filename
                            if ".." in decoded or decoded.startswith('/'): continue
                            t_path = os.path.join(extract_target, decoded)
                            if not os.path.realpath(t_path).startswith(os.path.realpath(extract_target)): continue
                            os.makedirs(os.path.dirname(t_path), exist_ok=True)
                            if member.is_dir(): continue
                            with inner_zf.open(member) as source, open(t_path, 'wb') as target:
                                shutil.copyfileobj(source, target)
                except Exception:
                    shutil.rmtree(extract_target, ignore_errors=True); os.remove(file_path); continue

                for root, _, files in os.walk(extract_target):
                    for f in files:
                        if f == ".DS_Store" or "__MACOSX" in root: continue
                        src = os.path.join(root, f)
                        original_base_name = os.path.splitext(f)[0]
                        encoded_name = original_base_name.encode('utf-8')
                        if len(encoded_name) > 170:
                            original_base_name = encoded_name[:170].decode('utf-8', 'ignore')

                        safe_new_f_path = service.get_safe_path(TEMP_DIR, original_base_name, os.path.splitext(f)[1], src)
                        shutil.move(src, safe_new_f_path); queue.append(os.path.basename(safe_new_f_path))

                shutil.rmtree(extract_target, ignore_errors=True); os.remove(file_path)
                extracted_count += 1
                if total_zips > 0: update_status_dict(pe=min(100, int((extracted_count/total_zips)*100)))
            else:
                to_process.append((filename, ext.lstrip('.'), zip_base_name))
        update_status_dict(pe=100)

        update_status_dict(step="3단계: 사용자 맞춤 분류 및 보관소 적재")
        for i, item in enumerate(to_process):
            filename, virtual_ext, zip_base_name = item
            file_path = os.path.join(TEMP_DIR, filename)
            if not os.path.exists(file_path): continue
            ext = os.path.splitext(filename)[1].lower()

            if not settings.get("process_txt", True) and ext in {'.txt', '.text'}:
                continue

            if ext not in TEXT_DOC_EXTS and virtual_ext != 'epub':
                if ext in AUDIO_EXTS: dest_dir = AUDIO_DIR
                elif ext in COMIC_EXTS: dest_dir = COMIC_DIR
                else: dest_dir = NON_TEXT_DIR
                os.makedirs(dest_dir, exist_ok=True)
                safe_dest = service.get_safe_path(dest_dir, zip_base_name, ext, file_path)
                shutil.move(file_path, safe_dest); continue

            file_hash = get_file_hash(file_path)
            if file_hash in blacklist_hashes: os.remove(file_path); continue

            update_status_dict(filename=filename, hash_short=file_hash[:8])
            existing = session.query(FileRecord).filter(FileRecord.file_hash == file_hash).first()
            if existing: os.remove(file_path); continue

            thumb_file = None
            if virtual_ext == "epub":
                thumb_file, refined_title = extract_epub_cover_and_title(file_path, THUMB_DIR, file_hash)
                base_name = refined_title; ext = ".epub"; is_zip_flag = True
            else:
                base_name = os.path.splitext(filename)[0]; is_zip_flag = ext in ARCHIVE_EXTS
                if ext == '.zip' or ext == '.cbz':
                    thumb_file = extract_zip_first_image(file_path, THUMB_DIR, file_hash)

            writer, episode_count, is_complete, tags_list = extract_smart_meta(base_name + ext)
            clean_title = re.sub(r'\[.*?\]|\(.*?\)', '', base_name).strip()
            if not clean_title: clean_title = base_name

            if ext == '.epub':
                epub_writer, epub_title = extract_epub_internal_meta(file_path)
                if not epub_writer and not epub_title:
                    new_base_name = base_name
                else:
                    if epub_writer: writer = epub_writer
                    if epub_title: clean_title = epub_title
                    new_base_name = compose_basename(ext, writer, clean_title, base_name, settings.get("rename_pattern", "prefix"))
            elif ext in {'.txt', '.text'}:
                new_base_name = base_name
            else:
                new_base_name = compose_basename(ext, writer, clean_title, base_name, settings.get("rename_pattern", "prefix"))

            encoded_name = new_base_name.encode('utf-8')
            if len(encoded_name) > 170:
                new_base_name = encoded_name[:170].decode('utf-8', 'ignore')
            new_base_name = re.sub(r'[\\/*?:"<>|]', "_", new_base_name)

            if settings.get("group_by", "initial") == "writer": folder_name = re.sub(r'[\\/*?:"<>|]', "_", writer) if writer else "미상"
            else: folder_name = service.get_initial_folder_name(clean_title)

            # ✨ [수정] EPUB 야매 감지기 기능 제거 (is_personal = False 고정)
            is_personal = False

            cat_dir = service.get_cat(FINAL_DIR, ext, is_personal)
            target_dir = os.path.join(cat_dir, folder_name)
            os.makedirs(target_dir, exist_ok=True)

            final_path = service.get_safe_path(target_dir, new_base_name, ext, file_path)
            shutil.move(file_path, final_path)

            stat = os.stat(final_path)
            session.add(FileRecord(
                name=os.path.basename(final_path), path=final_path.replace('\\', '/'), file_hash=file_hash,
                extension=ext.lstrip('.'), size_mb=round(stat.st_size/1048576, 2),
                mtime=datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                is_zip=is_zip_flag, writer=writer, episode_count=episode_count,
                is_complete=is_complete, tags=json.dumps(tags_list, ensure_ascii=False), thumbnail=thumb_file,
                series_name=None, user_rating=0, user_comment="", is_read=False, is_favorite=False
            ))
            session.commit()
            update_status_dict(pp=int(((i+1)/len(to_process))*100))
    except Exception as e:
        session.rollback(); service.log_event(session, f"엔진 크래시 방어: {e}", "ERROR")
    finally:
        session.close()
        update_status_dict(is_processing=False, step="대기 중", filename="-", hash_short="-", pa=0, pe=0, pp=0)

def conditional_pipeline_trigger():
    settings = load_settings()
    if settings.get("auto_scan", False):
        run_integrated_pipeline()

def smart_background_daemon():
    last_scan_time = 0
    while True:
        try:
            settings = load_settings(); now = time.time()
            if settings.get("auto_scan", False):
                scan_interval_sec = max(5, settings.get("scan_interval", 60))
                if now - last_scan_time >= scan_interval_sec:
                    last_scan_time = now
                    if any(os.path.isfile(os.path.join(TEMP_DIR, f)) for f in os.listdir(TEMP_DIR)):
                        if not current_status["is_processing"]: run_integrated_pipeline()
        except Exception: pass
        time.sleep(5)

@app.on_event("startup")
def startup_event():
    start_folder_watcher(TEMP_DIR, conditional_pipeline_trigger)
    threading.Thread(target=smart_background_daemon, daemon=True).start()

def _serialize(r):
    return {
        "id": r.id, "name": r.name, "path": r.path, "ext": r.extension, "size_mb": r.size_mb,
        "writer": r.writer, "episode_count": r.episode_count, "is_complete": r.is_complete,
        "tags": json.loads(r.tags or "[]"), "thumbnail": r.thumbnail, "is_lock": r.is_lock,
        "series_name": getattr(r, 'series_name', None),
        "user_rating": getattr(r, 'user_rating', 0),
        "user_comment": getattr(r, 'user_comment', "") or "",
        "is_read": getattr(r, 'is_read', False),
        "is_favorite": getattr(r, 'is_favorite', False)
    }

@app.get("/api/settings")
def api_get_settings(): return load_settings()

@app.post("/api/settings")
def api_update_settings(settings: SettingsModel):
    save_settings(settings.dict()); return {"success": True}

@app.post("/api/files/{file_id}/read")
def api_toggle_read(file_id: int):
    s = SessionLocal()
    r = s.query(FileRecord).filter(FileRecord.id == file_id).first()
    if r:
        r.is_read = not getattr(r, 'is_read', False)
        s.commit(); status = r.is_read; s.close()
        return {"success": True, "is_read": status}
    s.close()
    return {"success": False}

@app.get("/api/read_items")
def api_get_read_items(page: int = Query(1, ge=1), limit: int = Query(24, ge=1)):
    s = SessionLocal()
    base = s.query(FileRecord).filter(FileRecord.is_read == True)
    total = base.count()
    res = base.order_by(FileRecord.id.desc()).offset((page-1)*limit).limit(limit).all()
    s.close()
    return {"total": total, "page": page, "limit": limit, "files": [_serialize(r) for r in res]}

@app.get("/api/favorites")
def api_get_favorites(page: int = Query(1, ge=1), limit: int = Query(24, ge=1)):
    s = SessionLocal()
    base = s.query(FileRecord).filter(FileRecord.is_favorite == True)
    total = base.count()
    res = base.order_by(FileRecord.id.desc()).offset((page-1)*limit).limit(limit).all()
    s.close()
    return {"total": total, "page": page, "limit": limit, "files": [_serialize(r) for r in res]}

@app.post("/api/files/{file_id}/favorite")
def api_toggle_favorite(file_id: int):
    s = SessionLocal()
    r = s.query(FileRecord).filter(FileRecord.id == file_id).first()
    if r:
        r.is_favorite = not getattr(r, 'is_favorite', False)
        s.commit(); status = r.is_favorite; s.close()
        return {"success": True, "is_favorite": status}
    s.close()
    return {"success": False}

@app.post("/api/reviews/add")
def api_add_review(body: ReviewSubmitModel):
    s = SessionLocal()
    records = s.query(FileRecord).filter(FileRecord.id.in_(body.file_ids)).all()
    for r in records:
        r.series_name = body.series_name
        r.user_rating = body.rating
        r.user_comment = body.comment
        if body.mark_as_read: r.is_read = True
    s.commit()
    s.close()
    return {"success": True}

@app.post("/api/reviews/delete")
def api_delete_review(series_name: str = Query(...)):
    s = SessionLocal()
    records = s.query(FileRecord).filter(FileRecord.series_name == series_name).all()
    for r in records:
        r.series_name = None
        r.user_rating = 0
        r.user_comment = ""
    s.commit()
    s.close()
    return {"success": True}

@app.get("/api/reviews/list")
def api_get_reviews():
    s = SessionLocal()
    records = s.query(FileRecord).filter(
        (FileRecord.user_rating > 0) | (FileRecord.user_comment != "") | (FileRecord.series_name != None)
    ).order_by(FileRecord.id.desc()).all()
    s.close()
    return {"files": [_serialize(r) for r in records]}

@app.get("/api/files")
def api_get_files(page: int = Query(1, ge=1), limit: int = Query(24, ge=1)):
    s = SessionLocal()
    total_count = s.query(FileRecord).count()
    offset = (page - 1) * limit
    res = s.query(FileRecord).order_by(FileRecord.id.desc()).offset(offset).limit(limit).all()
    s.close()
    return {"total": total_count, "page": page, "limit": limit, "files": [_serialize(r) for r in res]}

@app.get("/api/search")
def api_search(q: str = "", page: int = Query(1, ge=1), limit: int = Query(24, ge=1)):
    s = SessionLocal()
    try:
        if not q.strip(): return {"total": 0, "page": page, "limit": limit, "files": []}
        base = s.query(FileRecord).filter(
            (FileRecord.name.ilike(f"%{q}%")) | (FileRecord.writer.ilike(f"%{q}%"))
        )
        total = base.count()
        records = base.order_by(FileRecord.id.desc()).offset((page - 1) * limit).limit(limit).all()
        return {"total": total, "page": page, "limit": limit, "files": [_serialize(r) for r in records]}
    except Exception as e: return JSONResponse(status_code=500, content={"detail": str(e)})
    finally: s.close()

@app.post("/api/retry_local_thumbnails")
def api_retry_local_thumbnails(bg_tasks: BackgroundTasks):
    def run_retry():
        s = SessionLocal()
        try:
            records = s.query(FileRecord).filter(FileRecord.extension.in_(['epub', 'zip', 'cbz'])).all()
            success_count = 0
            for r in records:
                needs_retry = False
                if not r.thumbnail:
                    needs_retry = True
                else:
                    thumb_path = os.path.join(THUMB_DIR, r.thumbnail)
                    if not os.path.exists(thumb_path):
                        needs_retry = True

                if needs_retry and os.path.exists(r.path):
                    thumb_file = None
                    if r.extension.lower() == 'epub':
                        thumb_file, _ = extract_epub_cover_and_title(r.path, THUMB_DIR, r.file_hash)
                    else:
                        thumb_file = extract_zip_first_image(r.path, THUMB_DIR, r.file_hash)

                    if thumb_file:
                        r.thumbnail = thumb_file
                        success_count += 1
            s.commit()
            service.log_event(s, f"[표지 재탐색] 완료. 누락되거나 깨진 표지 {success_count}개 복구 성공.", "INFO")
        except Exception as e:
            service.log_event(s, f"[표지 재탐색 오류] {e}", "ERROR")
        finally:
            s.close()
    bg_tasks.add_task(run_retry)
    return {"status": "started"}

@app.post("/api/reorganize_library")
def api_reorganize_library(bg_tasks: BackgroundTasks):
    def run_reorg():
        s = SessionLocal(); settings = load_settings()
        blacklist_hashes = set()
        for f in os.listdir(BLACKLIST_DIR):
            bl_path = os.path.join(BLACKLIST_DIR, f)
            if os.path.isfile(bl_path): blacklist_hashes.add(get_file_hash(bl_path))

        try:
            moved_count = 0
            supported_exts = {'.txt', '.text', '.epub', '.zip', '.cbz', '.pdf'}
            for root, _, files in os.walk(FINAL_DIR):
                for filename in files:
                    ext = os.path.splitext(filename)[1].lower()
                    if ext not in supported_exts: continue
                    if not settings.get("process_txt", True) and ext in {'.txt', '.text'}: continue

                    old_path = os.path.join(root, filename).replace('\\', '/')

                    f_hash = get_file_hash(old_path)
                    if f_hash in blacklist_hashes:
                        os.remove(old_path)
                        record = s.query(FileRecord).filter(FileRecord.path == old_path).first()
                        if record: s.delete(record)
                        continue

                    base_name, _ = os.path.splitext(filename)
                    writer, ep, comp, tags = extract_smart_meta(filename)
                    clean_title = re.sub(r'\[.*?\]|\(.*?\)', '', base_name).strip()
                    if not clean_title: clean_title = base_name

                    if ext in {'.txt', '.text'}:
                        new_base_name = base_name
                    else:
                        new_base_name = compose_basename(ext, writer, clean_title, base_name, settings.get("rename_pattern", "prefix"))

                    encoded_name = new_base_name.encode('utf-8')
                    if len(encoded_name) > 170: new_base_name = encoded_name[:170].decode('utf-8', 'ignore')
                    new_base_name = re.sub(r'[\\/*?:"<>|]', "_", new_base_name)

                    if settings.get("group_by", "initial") == "writer": folder_name = re.sub(r'[\\/*?:"<>|]', "_", writer) if writer else "미상"
                    else: folder_name = service.get_initial_folder_name(clean_title)

                    # ✨ [수정] EPUB 야매 감지기 기능 제거
                    is_personal = False

                    cat_dir = service.get_cat(FINAL_DIR, ext, is_personal)
                    target_dir = os.path.join(cat_dir, folder_name)
                    os.makedirs(target_dir, exist_ok=True)

                    new_path_expected = os.path.join(target_dir, new_base_name + ext).replace('\\', '/')
                    if old_path != new_path_expected:
                        actual_new_path = service.get_safe_path(target_dir, new_base_name, ext, old_path)
                        shutil.move(old_path, actual_new_path)
                        record = s.query(FileRecord).filter(FileRecord.path == old_path).first()
                        if record:
                            record.name = os.path.basename(actual_new_path)
                            record.path = actual_new_path.replace('\\', '/')
                            record.extension = ext.lstrip('.')
                            record.writer = writer
                        moved_count += 1
            s.commit()
            for root, dirs, files in os.walk(FINAL_DIR, topdown=False):
                for d in dirs:
                    dir_path = os.path.join(root, d)
                    try:
                        if not os.listdir(dir_path): os.rmdir(dir_path)
                    except Exception: pass
        except Exception as e: s.rollback()
        finally: s.close()
    bg_tasks.add_task(run_reorg)
    return {"status": "started"}

@app.get("/api/status")
def api_get_status():
    with _status_lock: return dict(current_status)

@app.get("/api/scan")
def api_trigger_scan(bg_tasks: BackgroundTasks):
    bg_tasks.add_task(run_integrated_pipeline)
    return {"status": "ok"}

@app.get("/api/logs")
def api_get_logs():
    s = SessionLocal()
    logs = s.query(ProcessLog).order_by(ProcessLog.timestamp.desc()).limit(50).all()
    s.close()
    return {"logs": [{"time": l.timestamp.strftime("%H:%M:%S"), "level": l.level, "message": l.message} for l in logs]}

@app.get("/api/temp_files")
def api_temp_files():
    try: return {"files": [{"name": f} for f in os.listdir(TEMP_DIR) if os.path.isfile(os.path.join(TEMP_DIR, f))]}
    except: return {"files": []}

@app.get("/")
def serve_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path): return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return FileResponse(index_path)

app.mount("/thumbnails", StaticFiles(directory=THUMB_DIR), name="thumbnails")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ✨ [핵심 추가] 윈도우 GUI(웹뷰) 독립 실행을 위한 쓰레딩 분기 로직
def start_server():
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="error")

if __name__ == "__main__":
    t = threading.Thread(target=start_server, daemon=True)
    t.start()
    time.sleep(1) # 서버가 켜질 때까지 1초 대기

    # pywebview를 이용해 네이티브 윈도우 창으로 웹 인터페이스를 띄웁니다.
    webview.create_window("📚 창고관리 파이프라인 콘솔", "http://127.0.0.1:8000", width=1200, height=800)
    webview.start()