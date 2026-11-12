#!/usr/bin/env python3
"""Local French CTC forced aligner for Clip Factory, using an Apache-2.0 model.

Input JSON: audio_path (16 kHz mono PCM WAV), segments [{text,start,end,words?}]
Output JSON: aligned segments/words with audio-relative timestamps.
"""
import argparse
import json
import math
import re
import sys
import unicodedata
import wave
from pathlib import Path
from array import array

MODEL_ID = "bofenghuang/asr-wav2vec2-ctc-french"
SAMPLE_RATE = 16000


def read_wav(path):
    import numpy as np
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    if rate != SAMPLE_RATE:
        raise ValueError(f"Le WAV d'alignement doit être à {SAMPLE_RATE} Hz (reçu {rate} Hz).")
    if sample_width != 2:
        raise ValueError(f"Le WAV d'alignement doit être en PCM 16 bits (reçu {sample_width * 8} bits).")
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples


def strip_accents(ch):
    return "".join(c for c in unicodedata.normalize("NFD", ch) if unicodedata.category(c) != "Mn")


def token_for_char(ch, vocab):
    """Return one or more model label IDs for a written character."""
    low = ch.lower()
    candidates = [low]
    decomp = strip_accents(low)
    if decomp != low:
        candidates.append(decomp)
    # French ligatures / curly apostrophes: punctuation is intentionally omitted.
    if low == "œ":
        candidates.extend(["oe"])
    for candidate in candidates:
        if len(candidate) == 1 and candidate in vocab:
            return [vocab[candidate]]
        if len(candidate) > 1 and all(c in vocab for c in candidate):
            return [vocab[c] for c in candidate]
    return []


def build_targets(segments, vocab, delimiter_id):
    token_ids = []
    # For each transcription word, record the corresponding target-token positions.
    word_records = []
    segment_word_indices = [[] for _ in segments]
    for seg_idx, seg in enumerate(segments):
        text = str(seg.get("text", "") or "")
        matches = list(re.finditer(r"\S+", text))
        for match in matches:
            display_word = match.group(0)
            char_positions = []
            for ch in display_word:
                ids = token_for_char(ch, vocab)
                for token_id in ids:
                    char_positions.append(len(token_ids))
                    token_ids.append(token_id)
            rec = {
                "segment_index": seg_idx,
                "word_index": len(segment_word_indices[seg_idx]),
                "word": display_word,
                "target_positions": char_positions,
                "fallback": None,
            }
            # Keep original Whisper timing for punctuation-only / unsupported tokens as fallback.
            old_words = seg.get("words", []) or []
            if rec["word_index"] < len(old_words):
                rec["fallback"] = old_words[rec["word_index"]]
            word_records.append(rec)
            segment_word_indices[seg_idx].append(len(word_records) - 1)
            if delimiter_id is not None:
                token_ids.append(delimiter_id)
    # A trailing word delimiter adds no useful alignment target.
    if delimiter_id is not None and token_ids and token_ids[-1] == delimiter_id:
        token_ids.pop()
    return token_ids, word_records, segment_word_indices


def ctc_viterbi(log_probs, target_ids, blank_id):
    """Viterbi forced alignment for a CTC target sequence. Returns frame -> target-char index."""
    import numpy as np
    frames, vocab_size = log_probs.shape
    n = len(target_ids)
    if n == 0:
        raise ValueError("Aucun caractère de la transcription n'est reconnu par le modèle d'alignement.")
    if frames < n:
        raise ValueError("L'extrait est trop court pour aligner tout le texte reconnu.")

    # CTC state sequence: blank, label, blank, label, ..., blank.
    states = np.full(2 * n + 1, blank_id, dtype=np.int64)
    states[1::2] = np.asarray(target_ids, dtype=np.int64)
    num_states = len(states)
    neg = np.float32(-1e30)
    trellis = np.full((frames, num_states), neg, dtype=np.float32)
    back = np.zeros((frames, num_states), dtype=np.int8)
    trellis[0, 0] = log_probs[0, blank_id]
    if num_states > 1:
        trellis[0, 1] = log_probs[0, states[1]]

    for t in range(1, frames):
        prev = trellis[t - 1]
        row = trellis[t]
        for st in range(num_states):
            best_score = prev[st]
            best_delta = 0
            if st >= 1 and prev[st - 1] > best_score:
                best_score = prev[st - 1]
                best_delta = 1
            if st >= 2 and states[st] != blank_id and states[st] != states[st - 2] and prev[st - 2] > best_score:
                best_score = prev[st - 2]
                best_delta = 2
            row[st] = best_score + log_probs[t, states[st]]
            back[t, st] = best_delta

    possible_ends = [num_states - 1, num_states - 2]
    state = max(possible_ends, key=lambda st: float(trellis[-1, st]))
    if float(trellis[-1, state]) <= float(neg / 2):
        raise ValueError("Le texte ne peut pas être aligné de façon fiable sur cet audio.")

    path = np.empty(frames, dtype=np.int32)
    for t in range(frames - 1, -1, -1):
        path[t] = state
        if t > 0:
            state -= int(back[t, state])
    target_frame_positions = [[] for _ in range(n)]
    for frame_idx, state_idx in enumerate(path):
        if state_idx % 2 == 1:
            target_frame_positions[(state_idx - 1) // 2].append(frame_idx)
    return target_frame_positions, trellis


def align_payload(payload):
    import numpy as np
    import torch
    from transformers import AutoModelForCTC, Wav2Vec2Processor

    torch.set_num_threads(max(1, min(6, __import__("os").cpu_count() or 1)))
    audio_path = Path(payload["audio_path"])
    if not audio_path.exists():
        raise FileNotFoundError(f"Fichier audio de réalignement introuvable : {audio_path}")
    audio = read_wav(audio_path)
    duration = len(audio) / SAMPLE_RATE
    if duration < 1.0:
        raise ValueError("Audio trop court pour le réalignement.")

    print("Chargement du modèle acoustique français VoxPopuli…", flush=True)
    processor = Wav2Vec2Processor.from_pretrained(MODEL_ID)  # plain CTC processor; no pyctcdecode dependency
    model = AutoModelForCTC.from_pretrained(MODEL_ID)
    model.eval()
    inputs = processor(audio, sampling_rate=SAMPLE_RATE, return_tensors="pt", padding=True)
    with torch.inference_mode():
        logits = model(inputs.input_values).logits[0]
    log_probs = torch.log_softmax(logits.float(), dim=-1).cpu().numpy().astype(np.float32)

    tokenizer = processor.tokenizer
    raw_vocab = tokenizer.get_vocab()
    vocab = {str(k).lower(): int(v) for k, v in raw_vocab.items()}
    blank_id = tokenizer.pad_token_id
    if blank_id is None:
        blank_id = model.config.pad_token_id
    delimiter = getattr(tokenizer, "word_delimiter_token", "|") or "|"
    delimiter_id = vocab.get(str(delimiter).lower())

    segments = payload.get("segments", [])
    target_ids, records, segment_word_indices = build_targets(segments, vocab, delimiter_id)
    if not target_ids:
        raise ValueError("Aucun mot de la transcription n'est compatible avec le modèle d'alignement français.")
    print(f"Alignement acoustique de {len(records)} mots…", flush=True)
    frame_positions, trellis = ctc_viterbi(log_probs, target_ids, int(blank_id))
    frame_seconds = duration / log_probs.shape[0]

    # Word timing = first aligned character frame to last aligned character frame.
    for rec in records:
        all_frames = []
        for target_pos in rec["target_positions"]:
            all_frames.extend(frame_positions[target_pos])
        if all_frames:
            rec["start"] = max(0.0, min(all_frames) * frame_seconds)
            rec["end"] = min(duration, (max(all_frames) + 1) * frame_seconds)
            rec["alignment"] = "acoustique"
        else:
            fallback = rec.get("fallback") or {}
            rec["start"] = float(fallback.get("start", 0.0))
            rec["end"] = float(fallback.get("end", rec["start"] + 0.15))
            rec["alignment"] = "estimation Whisper (mot non alignable)"

    output_segments = []
    for seg_idx, seg in enumerate(segments):
        aligned_words = []
        for rec_idx in segment_word_indices[seg_idx]:
            rec = records[rec_idx]
            aligned_words.append({
                "word": rec["word"],
                "start": round(float(rec["start"]), 3),
                "end": round(max(float(rec["start"]) + 0.015, float(rec["end"])), 3),
                "alignment": rec["alignment"],
            })
        # Ensure monotonically ordered cues and avoid pathological zero-length intervals.
        aligned_words.sort(key=lambda item: (item["start"], item["end"]))
        output_segments.append({
            "start": round(min([w["start"] for w in aligned_words], default=float(seg.get("start", 0))), 3),
            "end": round(max([w["end"] for w in aligned_words], default=float(seg.get("end", 0))), 3),
            "text": seg.get("text", ""),
            "words": aligned_words,
        })

    flat = [w for seg in output_segments for w in seg["words"]]
    return {
        "segments": output_segments,
        "words": flat,
        "alignment_model": MODEL_ID,
        "alignment_frames": int(log_probs.shape[0]),
        "audio_duration": round(duration, 3),
        "aligned_words": sum(1 for w in flat if w.get("alignment") == "acoustique"),
        "total_words": len(flat),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json")
    parser.add_argument("output_json")
    args = parser.parse_args()
    inp = Path(args.input_json)
    out = Path(args.output_json)
    try:
        payload = json.loads(inp.read_text(encoding="utf-8"))
        result = align_payload(payload)
        out.write_text(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"OK: {result['aligned_words']}/{result['total_words']} mots alignés acoustiquement.", flush=True)
    except Exception as exc:
        out.write_text(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"ERREUR: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise


if __name__ == "__main__":
    main()
