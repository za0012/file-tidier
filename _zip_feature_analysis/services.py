from __future__ import annotations
import os
import re
import json
import shutil
import zipfile
import hashlib
import unicodedata
import httpx
from bs4 import BeautifulSoup
from datetime import datetime
import xml.etree.ElementTree as ET
from sqlalchemy.orm import Session

from models import FileRecord, ProcessLog
# 기존 import 교체
from utils import (extract_smart_meta, get_file_hash, normalize,
                   TEXT_DOC_EXTS, COMIC_EXTS, AUDIO_EXTS, ARCHIVE_EXTS)


class PipelineService:
    def __init__(self, temp_dir: str, final_dir: str, archive_dir: str, thumb_dir: str, blacklist_dir: str, log_file: str):
        self.temp_dir = temp_dir
        self.final_dir = final_dir
        self.archive_dir = archive_dir
        self.thumb_dir = thumb_dir
        self.blacklist_dir = blacklist_dir
        self.log_file = log_file

    def write_external_log(self, level: str, message: str):
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {message}\n")
        except OSError:
            pass  # 로그 파일 접근 불가 시 무시

    def log_event(self, session: Session, message: str, level: str = "INFO", file_path: str = None):
        session.add(ProcessLog(level=level, message=message, file_path=file_path))
        session.commit()
        self.write_external_log(level, message)

    def get_safe_path(self, base_dir, base_name, ext, file_path):
        base_name = normalize(base_name)
        path = os.path.join(base_dir, f"{base_name}{ext}")
        c = 1
        while os.path.exists(path):
            path = os.path.join(base_dir, f"{base_name} ({c}){ext}")
            c += 1
        return path

    def get_cat(self, base, ext, is_personal_epub=False):
        ext = ext.lower()
        if ext == '.epub':
            cat_name = "[개인_변환_EPUB]" if is_personal_epub else "Epub"
        elif ext in ['.txt', '.text']:
            cat_name = "Txt"
        elif ext == '.pdf':
            cat_name = "PDF_만화"
        else:
            cat_name = "ETC"

        cat = os.path.join(base, cat_name)
        os.makedirs(cat, exist_ok=True)
        return cat

    def get_initial_folder_name(self, title: str) -> str:
        clean_title = re.sub(r'\[.*?\]|\(.*?\)', '', title).strip()
        if not clean_title:
            clean_title = title.strip()
            if not clean_title: return "[특수기호 및 기타]"

        first_char = clean_title[0]

        if '가' <= first_char <= '힣':
            cho_list = ['ㄱ', 'ㄲ', 'ㄴ', 'ㄷ', 'ㄸ', 'ㄹ', 'ㅁ', 'ㅂ', 'ㅃ', 'ㅅ', 'ㅆ', 'ㅇ', 'ㅈ', 'ㅉ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ']
            cho_index = (ord(first_char) - 0xAC00) // 588
            cho = cho_list[cho_index]
            mapping = {'ㄲ': 'ㄱ', 'ㄸ': 'ㄷ', 'ㅃ': 'ㅂ', 'ㅆ': 'ㅅ', 'ㅉ': 'ㅈ'}
            return mapping.get(cho, cho)

        if 'a' <= first_char.lower() <= 'z': return first_char.upper()
        if '0' <= first_char <= '9': return "0-9"
        return "[특수기호 및 기타]"

    # ✨ [핵심 기능] EPUB 파일이 개인 변환본인지 정식 출판물인지 판별하는 엔진
    def identify_epub_source(self, file_path: str) -> bool:
        """True를 반환하면 '개인 텍스트 변환본', False면 '정식 출판물'로 간주합니다."""
        if not zipfile.is_zipfile(file_path):
            return False

        score = 0
        try:
            with zipfile.ZipFile(file_path, 'r') as zf:
                # 1. content.opf 파일 찾기
                opf_path = None
                for name in zf.namelist():
                    if name.endswith('.opf'):
                        opf_path = name
                        break

                if not opf_path: return True # opf조차 없으면 야매 변환본 확률 99%

                # 2. OPF 파일 XML 파싱
                opf_content = zf.read(opf_path)
                root = ET.fromstring(opf_content)

                # XML 네임스페이스 처리
                namespaces = {
                    'dc': 'http://purl.org/dc/elements/1.1/',
                    'opf': 'http://www.idpf.org/2007/opf'
                }

                metadata = root.find('.//opf:metadata', namespaces)
                if metadata is None: metadata = root.find('.//{*}metadata') # fallback

                if metadata is not None:
                    # [검사 1] 출판사 (Publisher) 유무
                    publisher = metadata.find('.//dc:publisher', namespaces)
                    if publisher is None: publisher = metadata.find('.//{*}publisher')

                    if publisher is not None and publisher.text:
                        pub_text = publisher.text.strip().lower()
                        if any(x in pub_text for x in ['출판', '미디어', '코믹스', '북스', '연재']):
                            score += 40
                        elif pub_text == '미상' or pub_text == 'unknown':
                            score -= 20
                    else:
                        score -= 30 # 출판사가 없으면 개인 변환본 확률 높음

                    # [검사 2] ISBN 유무 (Identifier)
                    identifiers = metadata.findall('.//dc:identifier', namespaces)
                    if not identifiers: identifiers = metadata.findall('.//{*}identifier')

                    has_isbn = False
                    for ident in identifiers:
                        if ident.text and ('isbn' in ident.text.lower() or re.search(r'\d{13}', ident.text)):
                            has_isbn = True
                            score += 50
                            break
                    if not has_isbn:
                        score -= 20

                    # [검사 3] Generator (변환기) 추적
                    for meta_tag in metadata.findall('.//opf:meta', namespaces) + metadata.findall('.//{*}meta'):
                        if meta_tag.get('name') == 'generator':
                            gen_text = meta_tag.get('content', '').lower()
                            # 텍본 변환기, calibre, EasyEpub 등 개인이 흔히 쓰는 툴 감지
                            if any(x in gen_text for x in ['calibre', 'easypub', 'sigil', 'text', 'converter']):
                                score -= 60

        except Exception:
            return True # 파싱 에러가 나면 구조가 엉성한 개인 변환본으로 간주

        # 총점이 0점 미만이면 개인 변환본(True)으로 판정!
        return score < 0

    def analyze_and_clean_duplicate_lines(self, file_path: str) -> tuple[bool, int, int]:
        try:
            if not os.path.exists(file_path): return False, 0, 0
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f: lines = f.readlines()
            seen = set(); cleaned_lines = []; removed_count = 0
            for line in lines:
                norm = line.strip().replace(" ", "")
                if not norm: cleaned_lines.append(line); continue
                if norm in seen: removed_count += 1
                else: seen.add(norm); cleaned_lines.append(line)
            if removed_count > 0:
                with open(file_path, 'w', encoding='utf-8') as f: f.writelines(cleaned_lines)
            return True, removed_count, len(cleaned_lines)
        except: return False, 0, 0

    def fetch_thumbnail_from_web(self, db_session: Session):
        records = db_session.query(FileRecord).filter(FileRecord.thumbnail == None).all()
        headers = {"User-Agent": "Mozilla/5.0"}
        success_count = 0
        for r in records:
            clean_title = re.sub(r'\[.*?\]|\(.*?\)', '', r.name).replace(f".{r.extension}", '').strip()
            query = f"{clean_title} 소설 표지"
            url = f"https://search.naver.com/search.naver?where=image&query={query}"
            try:
                resp = httpx.get(url, headers=headers, timeout=10.0)
                soup = BeautifulSoup(resp.text, 'html.parser')
                imgs = soup.find_all("img", class_="_image")
                for img in imgs:
                    src = img.get("data-lazy-src") or img.get("src")
                    if src and src.startswith("http"):
                        img_data = httpx.get(src, timeout=10.0).content
                        thumb_name = f"auto_{r.file_hash[:12]}.jpg"
                        save_path = os.path.join(self.thumb_dir, thumb_name)
                        with open(save_path, "wb") as f: f.write(img_data)
                        r.thumbnail = thumb_name
                        db_session.commit()
                        self.log_event(db_session, f"[웹 스크래핑] 표지 수집 완료: {clean_title}", "INFO")
                        success_count += 1
                        break
            except: pass
        return success_count

    def execute_bulk_rename(self, db_session: Session, target_ids: list[int], pattern: str) -> int:
        records = db_session.query(FileRecord).filter(FileRecord.id.in_(target_ids)).all()
        renamed_count = 0
        for r in records:
            clean_title = re.sub(r'\[.*?\]|\(.*?\)', '', r.name).replace(f".{r.extension}", '').strip()
            new_name = pattern.replace("{title}", clean_title).replace("{writer}", r.writer or "미상").replace("{episode}", str(r.episode_count or 0))
            new_name = normalize(new_name) + f".{r.extension}"
            new_name = re.sub(r'[\\/*?:"<>|]', "_", new_name)
            if new_name == r.name: continue
            old_path = r.path

            initial_folder = self.get_initial_folder_name(clean_title)

            is_personal = False
            if r.extension.lower() == 'epub':
                is_personal = self.identify_epub_source(old_path)

            cat_dir = self.get_cat(self.final_dir, f".{r.extension}", is_personal)
            target_dir = os.path.join(cat_dir, initial_folder)
            os.makedirs(target_dir, exist_ok=True)

            new_path = self.get_safe_path(target_dir, os.path.splitext(new_name)[0], f".{r.extension}", old_path)
            try:
                shutil.move(old_path, new_path)
                r.name = os.path.basename(new_path)
                r.path = new_path.replace('\\', '/')
                db_session.commit()
                renamed_count += 1
            except: db_session.rollback()
        return renamed_count
    # ── PipelineService 클래스 안에 추가 ──────────────────────
    def classify_archive_content(self, file_path: str) -> str:
        """압축파일 내용을 분석해 처리 방향을 결정합니다.
        'archive' = 내부에 압축파일 존재(추출 대상),
        'text'    = 문서 포함(추출 대상),
        'comic'   = 이미지/PDF 위주(통째 격리),
        'audio'   = 음원 위주(통째 격리),
        'empty'   = 처리 대상 없음."""
        if not zipfile.is_zipfile(file_path):
            return 'text'   # zip 외 포맷(rar/7z 등)은 기존 추출 흐름에 위임
        doc = comic = audio = nested = 0
        try:
            with zipfile.ZipFile(file_path, 'r') as zf:
                for name in zf.namelist():
                    if name.endswith('/') or '__MACOSX' in name or name.endswith('.DS_Store'):
                        continue
                    ext = os.path.splitext(name)[1].lower()
                    if ext in TEXT_DOC_EXTS:   doc += 1
                    elif ext in COMIC_EXTS:    comic += 1
                    elif ext in AUDIO_EXTS:    audio += 1
                    elif ext in ARCHIVE_EXTS:  nested += 1
        except Exception:
            return 'text'
        if nested > 0:                                   # [요청2] 중첩 압축 -> 추출
            return 'archive'
        if doc > 0:
            if (comic + audio) >= 3 and doc <= 2:        # 문서 구색뿐인 미디어 모음
                return 'audio' if audio > comic else 'comic'
            return 'text'
        if audio > comic:
            return 'audio'
        if comic > 0:
            return 'comic'
        return 'empty'

    def sync_local_library_to_db(self, db_session: Session):
        if not os.path.exists(self.final_dir): return
        supported_exts = {'.txt', '.text', '.epub', '.zip', '.cbz', '.pdf', '.7z'}
        detected_files = []
        for root, _, files in os.walk(self.final_dir):
            for f in files:
                if os.path.splitext(f)[1].lower() in supported_exts:
                    detected_files.append(os.path.join(root, f))

        new_indexed_count = 0
        for path in detected_files:
            try:
                norm_path = path.replace('\\', '/')
                if db_session.query(FileRecord).filter(FileRecord.path == norm_path).first(): continue
                filename = os.path.basename(path)
                file_hash = get_file_hash(path)
                if db_session.query(FileRecord).filter(FileRecord.file_hash == file_hash).first(): continue

                ext = os.path.splitext(filename)[1].lower()
                writer, episode_count, is_complete, tags_list = extract_smart_meta(filename)
                stat = os.stat(path)

                db_session.add(FileRecord(
                    name=filename, path=norm_path, file_hash=file_hash,
                    extension=ext.lstrip('.'), size_mb=round(stat.st_size/1048576, 2),
                    mtime=datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    is_zip=(ext in {'.zip', '.7z', '.cbz'}), writer=writer, episode_count=episode_count,
                    is_complete=is_complete, tags=json.dumps(tags_list, ensure_ascii=False), thumbnail=None
                ))
                db_session.commit()
                new_indexed_count += 1
            except: db_session.rollback()
        if new_indexed_count > 0:
            self.log_event(db_session, f"[백그라운드 스캔] 유실 문서 {new_indexed_count}개 DB 보충 완료.", "INFO")