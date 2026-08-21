import os
import re
import zipfile
import hashlib
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET

TEXT_DOC_EXTS = {'.txt', '.text', '.epub', '.html', '.xhtml', '.xml', '.md'}
COMIC_EXTS    = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.pdf'}
AUDIO_EXTS    = {'.mp3', '.m4a', '.wav', '.flac', '.ogg', '.wma', '.aac'}
ARCHIVE_EXTS  = {'.zip', '.7z', '.rar', '.tar', '.gz', '.alz', '.egg', '.cbz'}

def split_title_volume(title: str) -> tuple[str, str | None]:
    title = (title or "").strip()
    if not title: return title, None
    patterns = [
        r'\s*[\(\[]?\s*(\d{1,3})\s*권\s*[\)\]]?\s*$',
        r'\s*[\(\[]?\s*(\d{1,3})\s*부\s*[\)\]]?\s*$',
        r'\s*[\(\[]?\s*(?:vol\.?|volume|볼륨)\s*(\d{1,3})\s*[\)\]]?\s*$',
        r'\s+(\d{1,3})\s*$',
    ]
    for i, pat in enumerate(patterns):
        m = re.search(pat, title, re.IGNORECASE)
        if m:
            base = title[:m.start()].strip(' -_.([')
            if base:
                unit = '부' if i == 1 else '권'
                return base, f"{int(m.group(1))}{unit}"
    return title, None

def compose_basename(ext: str, writer: str | None, clean_title: str, base_name: str, rename_pattern: str = "prefix") -> str:
    ext = (ext or "").lower()
    if ext == '.epub':
        core, vol = split_title_volume(clean_title)
        title = f"{core} {vol}" if vol else clean_title
        new_base = f"[{writer}] {title}" if writer else title
    elif writer:
        new_base = f"{clean_title} [{writer}]" if rename_pattern == "suffix" else f"[{writer}] {clean_title}"
    else:
        new_base = base_name
    return re.sub(r'[\\/*?:"<>|]', "_", new_base).strip()

def normalize(s: str) -> str:
    if not s: return ""
    return unicodedata.normalize('NFC', s)

def extract_smart_meta(filename: str) -> tuple[str, int, bool, list[str]]:
    name_without_ext = os.path.splitext(filename)[0]
    normalized_name = normalize(name_without_ext)
    writer = None; tags = []; episode_count = 0; is_complete = False

    brackets = re.findall(r'\[([^\]]+)\]', normalized_name)
    if brackets:
        writer = brackets[0].strip()
        for b in brackets[1:]:
            if ',' in b: tags.extend([t.strip() for t in b.split(',') if t.strip()])
            else: tags.append(b.strip())

    ep_match = re.search(re.compile(r'(\d+)\s*(?:화|권|단행본|T|t)', re.IGNORECASE), normalized_name)
    if ep_match: episode_count = int(ep_match.group(1))
    elif "단편" in normalized_name: episode_count = 1

    if any(k in normalized_name for k in ["완결", "[완]", "(완)", "외전"]):
        is_complete = True

    return writer, episode_count, is_complete, tags

def get_file_hash(filepath: str) -> str:
    hasher = hashlib.sha256()
    try:
        with open(filepath, 'rb') as f:
            buf = f.read(65536)
            while len(buf) > 0:
                hasher.update(buf); buf = f.read(65536)
        return hasher.hexdigest()
    except: return "ERROR"

def extract_epub_cover_and_title(epub_path: str, thumbnail_dir: str, file_hash: str) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(epub_path, 'r') as zf:
            if 'META-INF/container.xml' not in zf.namelist():
                return None, os.path.splitext(os.path.basename(epub_path))[0]

            container_xml = zf.read('META-INF/container.xml').decode('utf-8', errors='ignore')
            opf_match = re.search(r'full-path="([^"]+)"', container_xml)
            if not opf_match: return None, os.path.splitext(os.path.basename(epub_path))[0]

            opf_path = opf_match.group(1)
            opf_dir = os.path.dirname(opf_path)
            opf_xml = zf.read(opf_path)
            opf_root = ET.fromstring(opf_xml)

            title, creator = None, None
            for elem in opf_root.iter():
                tag_lower = elem.tag.lower()
                if tag_lower.endswith('title') and elem.text: title = elem.text.strip()
                elif tag_lower.endswith('creator') and elem.text: creator = elem.text.strip()

            refined_title = os.path.splitext(os.path.basename(epub_path))[0]
            if title: refined_title = f"[{creator}] {title}" if creator else title

            cover_href = None

            for item in opf_root.iter():
                if item.tag.lower().endswith('item'):
                    if 'cover-image' in item.attrib.get('properties', ''):
                        cover_href = item.attrib.get('href'); break

            if not cover_href:
                meta_cover = None
                for meta in opf_root.iter():
                    if meta.tag.lower().endswith('meta') and meta.attrib.get('name') == 'cover':
                        meta_cover = meta.attrib.get('content'); break
                if meta_cover:
                    for item in opf_root.iter():
                        if item.tag.lower().endswith('item') and item.attrib.get('id') == meta_cover:
                            cover_href = item.attrib.get('href'); break

            if not cover_href:
                for item in opf_root.iter():
                    if item.tag.lower().endswith('item'):
                        media_type = item.attrib.get('media-type', '').lower()
                        if 'image' in media_type:
                            href = item.attrib.get('href', '').lower()
                            item_id = item.attrib.get('id', '').lower()
                            if any(k in href or k in item_id for k in ['cover', 'thumb', 'front', 'title']):
                                cover_href = item.attrib.get('href'); break

            target_entry = None

            if not cover_href:
                image_exts = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}
                for name in zf.namelist():
                    clean_name = name.lower().replace(" ", "")
                    if 'cover' in clean_name and any(clean_name.endswith(ext) for ext in image_exts):
                        target_entry = name
                        break

            if cover_href and not target_entry:
                cover_href_unquoted = urllib.parse.unquote(cover_href)
                cover_full_path = cover_href_unquoted if not opf_dir else os.path.join(opf_dir, cover_href_unquoted).replace('\\', '/')
                cover_full_path = os.path.normpath(cover_full_path).replace('\\', '/')

                for name in zf.namelist():
                    name_clean = name.lower().replace(" ", "")
                    href_clean = cover_href.lower().replace(" ", "")
                    href_unq_clean = cover_href_unquoted.lower().replace(" ", "")
                    full_clean = cover_full_path.lower().replace(" ", "")

                    if name_clean == full_clean or name_clean.endswith(href_unq_clean) or name_clean.endswith(href_clean):
                        target_entry = name; break

            if not target_entry:
                image_exts = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}
                img_entries = [name for name in zf.namelist() if any(name.lower().endswith(ext) for ext in image_exts) and '__MACOSX' not in name]
                if img_entries:
                    img_entries.sort()
                    target_entry = img_entries[0]

            if target_entry:
                # ✨ 핵심 수정: 원본 확장자를 그대로 살려서 썸네일 이름 생성 (.png, .webp 등)
                original_ext = os.path.splitext(target_entry)[1].lower()
                if original_ext not in {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}:
                    original_ext = '.jpg' # 보험용 폴백

                thumb_filename = f"thumb_{file_hash[:12]}{original_ext}"
                dest_path = os.path.join(thumbnail_dir, thumb_filename)

                with zf.open(target_entry) as source, open(dest_path, 'wb') as target:
                    shutil_copy_large(source, target)
                return thumb_filename, normalize(refined_title)

            return None, normalize(refined_title)
    except: return None, os.path.splitext(os.path.basename(epub_path))[0]

def extract_zip_first_image(zip_path: str, thumbnail_dir: str, file_hash: str) -> str | None:
    image_exts = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif'}
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            img_entries = [name for name in zf.namelist() if any(name.lower().endswith(ext) for ext in image_exts) and '__MACOSX' not in name]
            if not img_entries: return None
            img_entries.sort()
            first_img = img_entries[0]

            # ✨ 핵심 수정: 원본 확장자 보존
            original_ext = os.path.splitext(first_img)[1].lower()
            if original_ext not in image_exts: original_ext = '.jpg'

            thumb_filename = f"thumb_{file_hash[:12]}{original_ext}"
            dest_path = os.path.join(thumbnail_dir, thumb_filename)
            with zf.open(first_img) as source, open(dest_path, 'wb') as target:
                shutil_copy_large(source, target)
            return thumb_filename
    except: return None

def shutil_copy_large(source, target):
    buffer_size = 64 * 1024
    while True:
        chunk = source.read(buffer_size)
        if not chunk: break
        target.write(chunk)