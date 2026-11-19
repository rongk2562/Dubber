"""
Robust SRT Parser, Matcher, Diff Engine, and ChatGPT Export/Prompt Helper.
Supports external SRT translation workflows (Export Chinese SRT -> Translate externally -> Import Khmer SRT).
"""

import os
import re
import json
import copy
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

from app.models import Segment, GlossaryItem, Project
from app.dubbing.speech_rate import khmer_syllable_count
from app.dubbing.transliteration import transliterate_khmer_dialogue


# --- Status Constants ---
STATUS_OK = "OK"
STATUS_NEW = "NEW"
STATUS_UNMATCHED = "UNMATCHED"
STATUS_TIME_INVALID = "TIME_INVALID"
STATUS_TOO_LONG = "TOO_LONG"
STATUS_REVIEW = "REVIEW"
STATUS_EMPTY = "EMPTY"
STATUS_LOCKED = "LOCKED"

STATUS_LABELS_KM = {
    STATUS_OK: "OK",
    STATUS_NEW: "ថ្មី",
    STATUS_UNMATCHED: "មិនផ្គូផ្គង",
    STATUS_TIME_INVALID: "ពេលវេលាខុស",
    STATUS_TOO_LONG: "វែងពេក",
    STATUS_REVIEW: "ត្រូវពិនិត្យ",
    STATUS_EMPTY: "ទទេ",
    STATUS_LOCKED: "ជាប់សោ",
}


@dataclass
class SrtEntry:
    index: int
    start: float
    end: float
    text: str
    raw_index_str: str = ""


@dataclass
class SrtParseResult:
    entries: List[SrtEntry] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    encoding_used: str = "utf-8"

    @property
    def is_valid(self) -> bool:
        return len(self.entries) > 0 and len(self.errors) == 0


@dataclass
class DiffItem:
    seg_index: int              # Index in project.segments (-1 if unmatched SRT entry)
    seg_id: int                 # Segment.id (or SRT index if unmatched)
    start: float
    end: float
    srt_start: Optional[float] = None
    srt_end: Optional[float] = None
    src_text: str = ""
    old_text: str = ""
    new_text: str = ""
    status: str = STATUS_OK
    notes: str = ""
    syllables: int = 0
    syll_per_sec: float = 0.0
    required_speed: float = 1.0
    is_locked: bool = False
    overwrite_approved: bool = True
    matched_srt_indices: List[int] = field(default_factory=list)
    merged_seg_indices: List[int] = field(default_factory=list)

    @property
    def status_label(self) -> str:
        return STATUS_LABELS_KM.get(self.status, self.status)


@dataclass
class ImportSnapshot:
    """1-step Undo/Redo snapshot for an SRT import operation."""
    segments_state: List[Dict[str, Any]]
    description: str = "SRT Import"
    timestamp: float = field(default_factory=time.time)


def decode_srt_bytes(raw_bytes: bytes) -> Tuple[str, str]:
    """
    Auto-detect encoding (UTF-8-BOM, UTF-16 LE/BE, UTF-8, fallback cp1252/latin-1).
    Returns (decoded_text, encoding_name).
    """
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        return raw_bytes.decode("utf-8-sig", errors="replace"), "utf-8-sig"
    if raw_bytes.startswith(b"\xff\xfe") or raw_bytes.startswith(b"\xfe\xff"):
        return raw_bytes.decode("utf-16", errors="replace"), "utf-16"

    # Heuristic for BOM-less UTF-16
    if len(raw_bytes) >= 4 and b"\x00" in raw_bytes[:100]:
        try:
            text = raw_bytes.decode("utf-16-le")
            if "-->" in text:
                return text, "utf-16-le"
        except Exception:
            pass
        try:
            text = raw_bytes.decode("utf-16-be")
            if "-->" in text:
                return text, "utf-16-be"
        except Exception:
            pass

    try:
        return raw_bytes.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw_bytes.decode("cp1252", errors="replace"), "cp1252"


def normalize_unicode_and_zwsp(text: str, keep_zwsp: bool = True) -> str:
    """
    Normalize text to Unicode NFC and handle Zero-Width Space (U+200B):
    - keep_zwsp=True: collapse repeated U+200B into a single U+200B (consistent for subtitles).
    - keep_zwsp=False: remove U+200B completely (clean for TTS engines).
    """
    if not text:
        return ""
    cleaned = text.replace("\ufeff", "").replace("\r", "")
    cleaned = unicodedata.normalize("NFC", cleaned)
    if keep_zwsp:
        cleaned = re.sub(r"\u200b+", "\u200b", cleaned)
    else:
        cleaned = cleaned.replace("\u200b", "")
    # Collapse excess spaces/tabs
    cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
    return cleaned


def strip_subtitle_tags(text: str) -> str:
    """Remove ASS override tags ({\\an8}, {\\pos(...)}) and HTML tags (<i>, <b>, <font...>)."""
    if not text:
        return ""
    out = re.sub(r"\{\\[^}]*\}", "", text)
    out = re.sub(r"</?[a-zA-Z][^>]*>", "", out)
    return out


_TS_PATTERN = re.compile(
    r"^\s*(?:(\d{1,3}):)?(\d{1,2}):(\d{1,2})(?:[,\.](\d{1,3}))?\s*$"
)


def parse_srt_timestamp(ts_str: str) -> Optional[float]:
    """
    Parse SRT timestamp supporting ',' or '.' milliseconds (e.g. 00:01:23,450 or 00:01:23.450).
    Returns seconds as float, or None if invalid.
    """
    if not ts_str:
        return None
    m = _TS_PATTERN.match(ts_str.strip())
    if not m:
        return None
    h_str, m_str, s_str, ms_str = m.groups()
    hours = int(h_str) if h_str is not None else 0
    mins = int(m_str)
    secs = int(s_str)
    if mins >= 60 or secs >= 60:
        return None
    if ms_str is None:
        ms = 0
    else:
        # Pad e.g. ".5" -> 500ms, ".45" -> 450ms
        ms = int(ms_str.ljust(3, "0")[:3])
    return round(hours * 3600 + mins * 60 + secs + ms / 1000.0, 3)


def parse_srt_content(content: str, encoding_used: str = "utf-8") -> SrtParseResult:
    """
    Robustly parse SRT content handling CRLF/LF, BOM, extra blank lines,
    non-sequential/duplicate indices, '.' or ',' ms separators, multi-line text,
    and HTML/ASS tags. Records malformed blocks as warnings instead of crashing.
    """
    result = SrtParseResult(encoding_used=encoding_used)
    if not content or not content.strip():
        result.errors.append("ឯកសារ SRT ទទេ គ្មានទិន្នន័យឡើយ។")
        return result

    normalized = content.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n+", normalized.strip())

    auto_idx = 0
    for block_num, block in enumerate(blocks, start=1):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip() != ""]
        if not lines:
            continue

        # Find timestamp line (with '-->')
        ts_line_idx = -1
        for i, ln in enumerate(lines):
            if "-->" in ln:
                ts_line_idx = i
                break

        if ts_line_idx == -1:
            result.warnings.append(f"ប្លុកទី {block_num}: រកមិនឃើញពេលវេលា (-->) — រំលងប្លុកនេះ។")
            continue

        # Parse index if present before timestamp line
        raw_idx_str = ""
        parsed_idx = None
        if ts_line_idx > 0:
            raw_idx_str = lines[ts_line_idx - 1]
            digits = re.sub(r"[^\d]", "", raw_idx_str)
            if digits:
                parsed_idx = int(digits)
            else:
                result.warnings.append(f"ប្លុកទី {block_num}: លេខរៀង '{raw_idx_str}' មិនត្រឹមត្រូវ។")

        auto_idx += 1
        entry_idx = parsed_idx if parsed_idx is not None else auto_idx

        # Parse start --> end
        ts_line = lines[ts_line_idx]
        # Strip trailing positioning coordinates (e.g. X1:0 Y1:0)
        parts = ts_line.split("-->", 1)
        start_raw = parts[0].strip()
        end_part = parts[1].strip().split()[0] if parts[1].strip() else ""

        start_sec = parse_srt_timestamp(start_raw)
        end_sec = parse_srt_timestamp(end_part)
        if start_sec is None or end_sec is None:
            result.warnings.append(f"ជួរ #{entry_idx}: ទម្រង់ពេលវេលាមិនត្រឹមត្រូវ ('{ts_line}')។")
            continue

        # Join multi-line text with space, strip tags, normalize NFC & ZWSP
        text_lines = lines[ts_line_idx + 1:]
        raw_text = " ".join(text_lines)
        clean_text = strip_subtitle_tags(raw_text)
        clean_text = normalize_unicode_and_zwsp(clean_text, keep_zwsp=True)

        result.entries.append(
            SrtEntry(
                index=entry_idx,
                start=start_sec,
                end=end_sec,
                text=clean_text,
                raw_index_str=raw_idx_str,
            )
        )

    if not result.entries:
        result.errors.append("មិនអាចអានទិន្នន័យ SRT ណាមួយបានទេ។ សូមពិនិត្យទម្រង់ឯកសារឡើងវិញ។")

    return result


def parse_srt_file(file_path: str) -> SrtParseResult:
    """Read an .srt file from disk with automatic encoding detection."""
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return SrtParseResult(errors=[f"រកមិនឃើញឯកសារ: {file_path}"])
    try:
        raw = p.read_bytes()
    except Exception as e:
        return SrtParseResult(errors=[f"មិនអាចអានឯកសារបានទេ: {e}"])

    decoded, enc = decode_srt_bytes(raw)
    return parse_srt_content(decoded, encoding_used=enc)


def _compute_overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _evaluate_speech_rate(text: str, slot_sec: float) -> Tuple[int, float, float, bool]:
    """
    Return (syllables, syll_per_sec, required_speed, is_too_long).
    Flags as too long if > 7.5 syl/s or required speed > 1.15x (based on 6.0 syl/s normal ceiling).
    """
    tts_text = normalize_unicode_and_zwsp(text, keep_zwsp=False)
    sylls = khmer_syllable_count(tts_text)
    dur = max(0.25, slot_sec)
    rate = round(sylls / dur, 2)
    req_speed = round(rate / 6.0, 2) if rate > 6.0 else 1.0
    too_long = rate > 7.5 or req_speed > 1.15
    return sylls, rate, req_speed, too_long


def _finalize_diff_status(
    item: DiffItem,
    seg: Segment,
    srt_start: Optional[float],
    srt_end: Optional[float],
    replace_target: str,
    use_srt_times: bool,
    force_review: bool = False,
) -> DiffItem:
    """Compute final status, speech rate metrics, and lock/empty checks for a DiffItem."""
    slot_for_check = item.end - item.start
    if use_srt_times and srt_start is not None and srt_end is not None:
        slot_for_check = srt_end - srt_start

    # Check invalid timestamps
    if srt_start is not None and srt_end is not None and (srt_end <= srt_start or srt_start < 0):
        item.status = STATUS_TIME_INVALID
        item.notes = f"ពេលវេលា SRT មិនត្រឹមត្រូវ ({srt_start:.2f}s -> {srt_end:.2f}s)"
        return item

    # Check empty text
    if not item.new_text.strip():
        item.new_text = item.old_text
        item.status = STATUS_EMPTY
        item.notes = "ជួរទទេក្នុង SRT — រក្សាអត្ថបទចាស់"
        return item

    # Compute Khmer speech rate if replacing Khmer text
    if replace_target == "km":
        sylls, rate, req_spd, too_long = _evaluate_speech_rate(item.new_text, max(0.25, slot_for_check))
        item.syllables = sylls
        item.syll_per_sec = rate
        item.required_speed = req_spd
    else:
        too_long = False

    # Check locked / manually modified
    is_locked = bool(getattr(seg, "is_locked", False) or getattr(seg, "is_modified", False))
    item.is_locked = is_locked
    if is_locked and item.new_text != item.old_text:
        item.status = STATUS_LOCKED
        item.overwrite_approved = False
        item.notes = "ជួរនេះត្រូវបានកែ/ចាក់សោដោយដៃ — សូមបញ្ជាក់មុនសរសេរជាន់"
        return item

    if force_review:
        item.status = STATUS_REVIEW
        return item

    if too_long:
        item.status = STATUS_TOO_LONG
        item.notes = f"{item.syllables} ព្យាង្គ ({item.syll_per_sec:.1f}/វិ > 7.5 ឬ >1.15x)"
        return item

    if not item.old_text.strip():
        item.status = STATUS_NEW
    else:
        item.status = STATUS_OK
    return item


def match_srt_to_segments(
    segments: List[Segment],
    srt_entries: List[SrtEntry],
    mode: str = "auto",
    replace_target: str = "km",
    use_srt_times: bool = False,
) -> List[DiffItem]:
    """
    Match parsed SRT entries against project segments.
    mode:
      - "auto": index if counts are equal, else time overlap (>= 50%)
      - "index": match by seg.id == srt.index (with 1-to-1 positional fallback)
      - "time": match by timestamp overlap (>= 50%), supporting merge/split detection
      - "order": 1-to-1 sequential order starting from row 1
    """
    if not segments:
        return []

    effective_mode = mode
    if mode == "auto":
        effective_mode = "index" if len(srt_entries) == len(segments) else "time"

    diffs: List[DiffItem] = []

    if effective_mode in ("index", "order"):
        srt_by_idx: Dict[int, SrtEntry] = {}
        if effective_mode == "index":
            for e in srt_entries:
                if e.index not in srt_by_idx:
                    srt_by_idx[e.index] = e

        used_srt_positions = set()
        for seg_pos, seg in enumerate(segments):
            old_val = seg.text_km if replace_target == "km" else seg.text_src
            matched_entry: Optional[SrtEntry] = None
            matched_pos = -1

            if effective_mode == "index":
                if seg.id in srt_by_idx:
                    cand = srt_by_idx[seg.id]
                    pos = srt_entries.index(cand)
                    if pos not in used_srt_positions:
                        matched_entry = cand
                        matched_pos = pos
                elif seg_pos < len(srt_entries) and seg_pos not in used_srt_positions:
                    matched_entry = srt_entries[seg_pos]
                    matched_pos = seg_pos
            else:  # order
                if seg_pos < len(srt_entries):
                    matched_entry = srt_entries[seg_pos]
                    matched_pos = seg_pos

            if matched_entry is not None:
                used_srt_positions.add(matched_pos)
                item = DiffItem(
                    seg_index=seg_pos,
                    seg_id=seg.id,
                    start=seg.start,
                    end=seg.end,
                    srt_start=matched_entry.start,
                    srt_end=matched_entry.end,
                    src_text=seg.text_src,
                    old_text=old_val,
                    new_text=matched_entry.text,
                    matched_srt_indices=[matched_entry.index],
                )
                diffs.append(
                    _finalize_diff_status(
                        item, seg, matched_entry.start, matched_entry.end, replace_target, use_srt_times
                    )
                )
            else:
                diffs.append(
                    DiffItem(
                        seg_index=seg_pos,
                        seg_id=seg.id,
                        start=seg.start,
                        end=seg.end,
                        src_text=seg.text_src,
                        old_text=old_val,
                        new_text=old_val,
                        status=STATUS_UNMATCHED,
                        notes="គ្មានជួរត្រូវគ្នាក្នុង SRT",
                    )
                )

        # Report extra unmatched SRT entries
        for pos, e in enumerate(srt_entries):
            if pos not in used_srt_positions:
                diffs.append(
                    DiffItem(
                        seg_index=-1,
                        seg_id=e.index,
                        start=e.start,
                        end=e.end,
                        srt_start=e.start,
                        srt_end=e.end,
                        src_text="",
                        old_text="",
                        new_text=e.text,
                        status=STATUS_UNMATCHED,
                        notes=f"ជួរ SRT #{e.index} លើសពីចំនួនជួរក្នុងតារាង",
                    )
                )
        return diffs

    # --- Time Overlap Matching (handles merges & splits cleanly) ---
    # For each (seg_pos, srt_pos), compute overlap >= 50% of shorter duration
    seg_to_srts: Dict[int, List[int]] = {i: [] for i in range(len(segments))}
    srt_to_segs: Dict[int, List[int]] = {j: [] for j in range(len(srt_entries))}

    for i, seg in enumerate(segments):
        seg_dur = max(0.05, seg.end - seg.start)
        for j, srt in enumerate(srt_entries):
            srt_dur = max(0.05, srt.end - srt.start)
            ov = _compute_overlap(seg.start, seg.end, srt.start, srt.end)
            if ov <= 0:
                continue
            ratio_seg = ov / seg_dur
            ratio_srt = ov / srt_dur
            if max(ratio_seg, ratio_srt) >= 0.50:
                seg_to_srts[i].append(j)
                srt_to_segs[j].append(i)

    handled_segs = set()
    used_srts = set()

    for i, seg in enumerate(segments):
        if i in handled_segs:
            continue
        old_val = seg.text_km if replace_target == "km" else seg.text_src
        matched_srts = seg_to_srts.get(i, [])

        if not matched_srts:
            diffs.append(
                DiffItem(
                    seg_index=i,
                    seg_id=seg.id,
                    start=seg.start,
                    end=seg.end,
                    src_text=seg.text_src,
                    old_text=old_val,
                    new_text=old_val,
                    status=STATUS_UNMATCHED,
                    notes="រកមិនឃើញពេលវេលាត្រួតគ្នា ≥ 50%",
                )
            )
            handled_segs.add(i)
            continue

        # Case A: 1 Khmer SRT entry spans multiple Chinese segments (Merged by ChatGPT)
        if len(matched_srts) == 1 and len(srt_to_segs[matched_srts[0]]) > 1:
            j = matched_srts[0]
            srt = srt_entries[j]
            group_seg_indices = sorted(srt_to_segs[j])
            primary_idx = group_seg_indices[0]
            primary_seg = segments[primary_idx]
            combined_start = min(segments[idx].start for idx in group_seg_indices)
            combined_end = max(segments[idx].end for idx in group_seg_indices)
            combined_src = " ".join(segments[idx].text_src for idx in group_seg_indices if segments[idx].text_src)
            old_primary = primary_seg.text_km if replace_target == "km" else primary_seg.text_src

            item = DiffItem(
                seg_index=primary_idx,
                seg_id=primary_seg.id,
                start=combined_start,
                end=combined_end,
                srt_start=srt.start,
                srt_end=srt.end,
                src_text=combined_src,
                old_text=old_primary,
                new_text=srt.text,
                notes=f"1 ខ្មែរ ផ្គូផ្គង {len(group_seg_indices)} ជួរចិន (Merge ជួរ {[segments[k].id for k in group_seg_indices]})",
                matched_srt_indices=[srt.index],
                merged_seg_indices=group_seg_indices,
            )
            diffs.append(
                _finalize_diff_status(
                    item, primary_seg, srt.start, srt.end, replace_target, use_srt_times, force_review=True
                )
            )
            for idx in group_seg_indices:
                handled_segs.add(idx)
            used_srts.add(j)
            continue

        # Case B: Multiple Khmer SRT entries match 1 Chinese segment (Split by ChatGPT)
        if len(matched_srts) > 1:
            joined_text = " ".join(srt_entries[j].text for j in matched_srts if srt_entries[j].text).strip()
            s_start = min(srt_entries[j].start for j in matched_srts)
            s_end = max(srt_entries[j].end for j in matched_srts)
            srt_ids = [srt_entries[j].index for j in matched_srts]
            item = DiffItem(
                seg_index=i,
                seg_id=seg.id,
                start=seg.start,
                end=seg.end,
                srt_start=s_start,
                srt_end=s_end,
                src_text=seg.text_src,
                old_text=old_val,
                new_text=joined_text,
                notes=f"{len(matched_srts)} ខ្មែរ ផ្គូផ្គង 1 ជួរចិន (ភ្ជាប់ជួរ SRT {srt_ids})",
                matched_srt_indices=srt_ids,
            )
            diffs.append(
                _finalize_diff_status(
                    item, seg, s_start, s_end, replace_target, use_srt_times, force_review=True
                )
            )
            handled_segs.add(i)
            for j in matched_srts:
                used_srts.add(j)
            continue

        # Case C: Clean 1-to-1 time overlap match
        j = matched_srts[0]
        srt = srt_entries[j]
        item = DiffItem(
            seg_index=i,
            seg_id=seg.id,
            start=seg.start,
            end=seg.end,
            srt_start=srt.start,
            srt_end=srt.end,
            src_text=seg.text_src,
            old_text=old_val,
            new_text=srt.text,
            matched_srt_indices=[srt.index],
        )
        diffs.append(
            _finalize_diff_status(item, seg, srt.start, srt.end, replace_target, use_srt_times)
        )
        handled_segs.add(i)
        used_srts.add(j)

    # Extra unmatched SRT entries
    for j, srt in enumerate(srt_entries):
        if j not in used_srts:
            diffs.append(
                DiffItem(
                    seg_index=-1,
                    seg_id=srt.index,
                    start=srt.start,
                    end=srt.end,
                    srt_start=srt.start,
                    srt_end=srt.end,
                    src_text="",
                    old_text="",
                    new_text=srt.text,
                    status=STATUS_UNMATCHED,
                    notes=f"ជួរ SRT #{srt.index} គ្មានជួរត្រូវគ្នាក្នុងតារាង",
                )
            )

    return diffs


def create_import_snapshot(segments: List[Segment], description: str = "SRT Import") -> ImportSnapshot:
    """Capture deep snapshot of all segments for 1-step Undo/Redo."""
    return ImportSnapshot(
        segments_state=[s.to_dict() for s in segments],
        description=description,
    )


def restore_import_snapshot(project: Project, snapshot: ImportSnapshot) -> None:
    """Restore project.segments from an ImportSnapshot."""
    project.segments = [Segment.from_dict(copy.deepcopy(d)) for d in snapshot.segments_state]


def save_project_backup(project: Project, backup_dir: Path) -> Optional[str]:
    """Automatically save a JSON project backup before applying bulk SRT import."""
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        safe_name = re.sub(r"[^\w\-]", "_", project.name or "project")
        backup_path = backup_dir / f"{safe_name}_pre_srt_import_{ts}.json"
        data = {
            "name": project.name,
            "video_path": project.video_path,
            "segments": [s.to_dict() for s in project.segments],
            "created_at": time.time(),
        }
        backup_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(backup_path)
    except Exception as e:
        print(f"[SRT IO] Backup warning: {e}")
        return None


def apply_srt_diff(
    project: Project,
    diffs: List[DiffItem],
    replace_target: str = "km",
    use_srt_times: bool = False,
    clear_changed_tts: bool = True,
    backup_dir: Optional[Path] = None,
) -> Tuple[ImportSnapshot, int, Optional[str]]:
    """
    Apply approved DiffItems to project.segments.
    Returns (undo_snapshot, changed_count, backup_file_path).
    """
    undo_snap = create_import_snapshot(project.segments, description="Before SRT Import")
    backup_path = save_project_backup(project, backup_dir) if backup_dir else None

    changed_count = 0
    indices_to_remove = set()

    for item in diffs:
        if item.seg_index < 0 or item.seg_index >= len(project.segments):
            continue
        if item.status in (STATUS_UNMATCHED, STATUS_TIME_INVALID, STATUS_EMPTY):
            continue
        if item.status == STATUS_LOCKED and not item.overwrite_approved:
            continue

        seg = project.segments[item.seg_index]
        old_val = seg.text_km if replace_target == "km" else seg.text_src
        new_val = normalize_unicode_and_zwsp(item.new_text, keep_zwsp=True)
        if replace_target == "km":
            new_val = transliterate_khmer_dialogue(new_val)

        text_changed = (new_val != old_val)
        time_changed = False

        if use_srt_times and item.srt_start is not None and item.srt_end is not None and item.srt_end > item.srt_start:
            if abs(seg.start - item.srt_start) > 0.005 or abs(seg.end - item.srt_end) > 0.005:
                seg.start = round(item.srt_start, 3)
                seg.end = round(item.srt_end, 3)
                time_changed = True

        # Handle merged segments (1 Khmer -> multiple Chinese segments)
        if len(item.merged_seg_indices) > 1:
            combined_end = max(project.segments[k].end for k in item.merged_seg_indices)
            if not use_srt_times:
                seg.end = combined_end
            if replace_target == "src":
                seg.text_src = new_val
            else:
                seg.text_src = item.src_text
                seg.text_km = new_val
            for extra_idx in item.merged_seg_indices[1:]:
                indices_to_remove.add(extra_idx)
            text_changed = True
        else:
            if replace_target == "km":
                seg.text_km = new_val
            else:
                seg.text_src = new_val

        if text_changed or time_changed:
            changed_count += 1
            seg.translation_source = "imported"
            seg.actual_start = 0.0
            seg.actual_end = 0.0

            if clear_changed_tts:
                seg.tts_file = ""
                seg.tts_duration = 0.0
                seg.tts_outdated = True
            elif seg.tts_file:
                seg.tts_outdated = True

            # Update slot / speech-rate status badge
            if replace_target == "km":
                sylls, rate, req_spd, too_long = _evaluate_speech_rate(seg.text_km, seg.slot)
                if too_long:
                    seg.sync_status = "OVERFLOW"
                    seg.sync_notes = f"SRT Import: {sylls} ព្យាង្គ ({rate:.1f}/វិ > 7.5 ឬ >1.15x)"
                else:
                    seg.sync_status = "OK"
                    seg.sync_notes = f"SRT Imported ({rate:.1f} ព្យាង្គ/វិ)"

    if indices_to_remove:
        project.segments = [
            s for idx, s in enumerate(project.segments) if idx not in indices_to_remove
        ]
        # Re-number IDs sequentially if segments were merged
        for new_id, s in enumerate(project.segments, start=1):
            s.id = new_id

    return undo_snap, changed_count, backup_path


def _fmt_srt_ts(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    h = total_ms // 3600000
    m = (total_ms % 3600000) // 60000
    s = (total_ms % 60000) // 1000
    ms = total_ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def export_chinese_srt_for_chatgpt(
    segments: List[Segment],
    output_path: str,
    chunk_size: int = 0,
) -> List[str]:
    """
    Export Chinese source SRT with exact segment IDs and timestamps from the app.
    If chunk_size > 0 (e.g., 50), splits into multiple files: <stem>_part1.srt, <stem>_part2.srt...
    Returns list of written file paths.
    """
    if not segments:
        return []

    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    def _build_srt_block(segs: List[Segment]) -> str:
        lines = []
        for s in segs:
            txt = (s.zh_fixed or s.text_src or "").strip()
            lines.append(str(s.id))
            lines.append(f"{_fmt_srt_ts(s.start)} --> {_fmt_srt_ts(s.end)}")
            lines.append(txt)
            lines.append("")
        return "\n".join(lines)

    created: List[str] = []
    if chunk_size and chunk_size > 0 and len(segments) > chunk_size:
        part_num = 1
        for start_i in range(0, len(segments), chunk_size):
            chunk = segments[start_i:start_i + chunk_size]
            part_path = out_p.with_name(f"{out_p.stem}_part{part_num}{out_p.suffix or '.srt'}")
            part_path.write_text(_build_srt_block(chunk), encoding="utf-8")
            created.append(str(part_path))
            part_num += 1
    else:
        out_p.write_text(_build_srt_block(segments), encoding="utf-8")
        created.append(str(out_p))

    return created


def build_chatgpt_prompt(glossary: List[GlossaryItem], summary: str = "") -> str:
    """
    Build the exact Khmer ChatGPT SRT translation prompt template with glossary & story context.
    """
    glossary_pairs = [
        f"{g.src_term}={g.km_term}" for g in (glossary or []) if g.src_term and g.km_term
    ]
    # Add built-in drama name transliterations if not already present
    defaults = [
        "Gang Hong/江宏=គាំងហុង",
        "Baoshou/保寿=ប៉ោសូវ",
        "Su Bao/苏宝=ស៊ូប៉ាវ",
    ]
    existing_str = ", ".join(glossary_pairs)
    for d in defaults:
        if d.split("=")[1] not in existing_str:
            glossary_pairs.append(d)

    glossary_str = ", ".join(glossary_pairs) if glossary_pairs else "គ្មាន"
    summary_str = summary.strip() if summary and summary.strip() else "រឿងភាគចិន/វីដេអូខ្លីនិយាយប្រចាំថ្ងៃ"

    return (
        "បកឯកសារ SRT ខាងក្រោមពីចិនទៅខ្មែរ ដើម្បីដាក់សំឡេង (dubbing)។\n"
        "ច្បាប់:\n"
        "- រក្សាលេខរៀង និងពេលវេលាដូចដើមទាំងស្រុង។ ចំនួនបន្ទាត់ត្រូវស្មើដើម មិនត្រូវ merge/split។\n"
        "- បកតាមអត្ថន័យ និងអារម្មណ៍ ជាខ្មែរនិយាយប្រចាំថ្ងៃ ធម្មជាតិ មិនបកតាមអក្សរ។\n"
        "- ប្រវែងសមនឹងរយៈពេលបន្ទាត់ (≈ ១ វិនាទី = ៦ ព្យាង្គ)។ សង្ខេបបើវែងពេក។\n"
        f"- ឈ្មោះតួអង្គ/ពាក្យជំនាញ ប្រើតាម glossary: {glossary_str}\n"
        "- បើអក្សរចិនមើលទៅដូចស្ដាប់ខុស (homophone) ឱ្យបកតាម context ហើយកុំបង្កើតឈ្មោះ pinyin។\n"
        "- ទិន្នផល: SRT ពេញលេញ តែប៉ុណ្ណោះ។\n"
        f"Context រឿង: {summary_str}"
    )
