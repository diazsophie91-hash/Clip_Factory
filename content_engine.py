"""Transcript-based excerpt ranking and narrative episode segmentation.

Scores are deterministic heuristic aids. This module does not perform semantic or
audiovisual model inference.
"""
import re
import unicodedata
import hashlib


def _normal_words(transcript):
    words = []
    for seg in transcript.get("segments", []):
        seg_words = seg.get("words") or []
        if seg_words:
            for w in seg_words:
                token = str(w.get("word", "")).strip()
                if not token:
                    continue
                try:
                    a, b = float(w.get("start", seg.get("start", 0))), float(w.get("end", seg.get("end", 0)))
                except (TypeError, ValueError):
                    continue
                if b > a:
                    try:
                        # Never treat absent confidence metadata as certainty.
                        probability = float(w.get("probability", 0.55))
                    except (TypeError, ValueError):
                        probability = 0.55
                    words.append({"word": token, "start": a, "end": b, "probability": probability})
        else:
            # Defensive fallback for older transcripts without word timestamps.
            toks = re.findall(r"\S+", str(seg.get("text", "")))
            if not toks:
                continue
            a, b = float(seg.get("start", 0)), float(seg.get("end", 0))
            step = max(0.04, (b-a)/len(toks))
            for i, token in enumerate(toks):
                words.append({"word": token, "start": a+i*step, "end": min(b, a+(i+1)*step), "probability": 0.45})
    if not words:
        # Some aligner/export versions keep only the flattened word list.
        for w in transcript.get("words", []) or []:
            token = str(w.get("word", "")).strip()
            try:
                a, b = float(w.get("start", 0)), float(w.get("end", 0))
            except (TypeError, ValueError):
                continue
            if token and b > a:
                words.append({"word": token, "start": a, "end": b})
    words.sort(key=lambda x: (x["start"], x["end"]))
    # Remove accidental duplicates caused by segment/word fallback overlap.
    cleaned=[]
    for w in words:
        if cleaned and w["word"] == cleaned[-1]["word"] and abs(w["start"]-cleaned[-1]["start"]) < 0.025:
            continue
        cleaned.append(w)
    return cleaned

def rank_candidate_excerpts(transcript, min_duration=20.0, max_duration=45.0, limit=8, diagnostics=None, config=None):
    """Generate a broad set of speech-led clip candidates, then rank and diversify them.

    Scores are ranking aids, not probabilities of virality. Candidates are not
    discarded merely for lacking lexical hooks, punctuation, or a high absolute score.
    """
    import bisect
    min_duration = max(3.0, float(min_duration))
    max_duration = max(min_duration, float(max_duration))
    limit = max(1, min(30, int(limit)))
    config = dict(config or {})
    min_speech_coverage = max(0.0, min(1.0, float(config.get("min_speech_coverage", 0.45))))
    incomplete_end_gap = max(0.0, float(config.get("incomplete_end_gap", 0.18)))
    anchor_mode = str(config.get("anchor_mode", "current"))
    use_grid = bool(config.get("use_grid", True))
    weights = dict(config.get("weights") or {})
    weights = {"narrative_interest": max(0.0, float(weights.get("narrative_interest", 0.30))),
               "comprehensibility": max(0.0, float(weights.get("comprehensibility", 0.20))),
               "transcript_quality": max(0.0, float(weights.get("transcript_quality", 0.30))),
               "autonomy": max(0.0, float(weights.get("autonomy", 0.20)))}
    weight_total = sum(weights.values()) or 1.0
    weights = {key: value / weight_total for key, value in weights.items()}
    overlap_threshold = max(0.0, min(1.0, float(config.get("max_time_overlap", 0.42))))
    midpoint_distance = max(0.0, float(config.get("midpoint_distance", 9.0)))
    text_similarity_threshold = max(0.0, min(1.0, float(config.get("text_similarity", 0.48))))
    capture_audit = diagnostics is not None
    segments = transcript.get("segments", []) or []
    stats = {"input_segments": len(segments), "rejected_hallucination_segments": 0,
             "rejected_quality_segments": 0, "retained_segments": 0,
             "normalized_words": 0, "anchors": 0, "endpoint_candidates_examined": 0,
             "candidate_windows_considered": 0, "rejected_sparse_windows": 0,
             "rejected_incomplete_endings": 0, "raw_candidates": 0,
             "rejected_as_redundant": 0, "selected_candidates": 0,
             "rejected_by_quota": 0, "limited_by_quota": False,
             "configuration": {"min_speech_coverage": min_speech_coverage,
                               "incomplete_end_gap": incomplete_end_gap,
                               "anchor_mode": anchor_mode, "use_grid": use_grid,
                               "weights": weights, "max_time_overlap": overlap_threshold,
                               "midpoint_distance": midpoint_distance,
                               "text_similarity": text_similarity_threshold}}

    def reject(reason, start, end, text="", **extra):
        if capture_audit:
            payload = f"{float(start):.3f}|{float(end):.3f}|{text}"
            row = {"candidate_id": hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20],
                   "start": round(float(start), 3), "end": round(float(end), 3),
                   "text": str(text), "decision": "rejected", "reason": reason}
            row.update(extra)
            stats.setdefault("candidate_audit", []).append(row)

    def finish(result):
        stats["raw_candidates"] = stats.get("raw_candidates", 0)
        stats["selected_candidates"] = len(result)
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update(stats)
        return result

    if not segments:
        return finish([])

    # Reject only clear transcription failures. The previous thresholds were too strict
    # for quiet speech, music under dialogue, and conversational French.
    safe_segments = []
    known_hallucinations = (
        "sous titres realises par la communaute",
        "amara org", "sous titres realises", "merci d avoir regarde",
        "merci d avoir visionne", "abonnez vous a la chaine",
    )
    def norm_text(value):
        value = str(value or "").lower().replace("’", "'")
        value = unicodedata.normalize("NFD", value)
        value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
        return re.sub(r"[^a-z0-9']+", " ", value).strip()

    for segment_index, seg in enumerate(segments):
        text_norm = norm_text(seg.get("text", ""))
        if any(p in text_norm for p in known_hallucinations):
            stats["rejected_hallucination_segments"] += 1
            if capture_audit:
                stats.setdefault("rejected_segments", []).append({"segment_index": segment_index,
                    "start": seg.get("start"), "end": seg.get("end"), "text": seg.get("text", ""),
                    "reason": "known_subtitle_or_credit_hallucination"})
            continue
        try:
            no_speech = float(seg.get("no_speech_prob", 0.0) or 0.0)
            avg_logprob = float(seg.get("avg_logprob", 0.0) or 0.0)
            compression = float(seg.get("compression_ratio", 0.0) or 0.0)
        except (TypeError, ValueError):
            no_speech, avg_logprob, compression = 0.0, 0.0, 0.0
        if no_speech > 0.82 or avg_logprob < -1.45 or compression > 2.9:
            stats["rejected_quality_segments"] += 1
            if capture_audit:
                stats.setdefault("rejected_segments", []).append({"segment_index": segment_index,
                    "start": seg.get("start"), "end": seg.get("end"), "text": seg.get("text", ""),
                    "reason": "whisper_segment_quality", "no_speech_prob": no_speech,
                    "avg_logprob": avg_logprob, "compression_ratio": compression})
            continue
        safe_segments.append(seg)
    stats["retained_segments"] = len(safe_segments)

    working = dict(transcript)
    working["segments"] = safe_segments
    words = _normal_words(working)
    stats["normalized_words"] = len(words)
    if len(words) < 10:
        if capture_audit:
            stats["early_rejection_reason"] = "fewer_than_10_words_after_segment_filters"
        return finish([])

    range_start = float(transcript.get("range_start", words[0]["start"]))
    range_end = float(transcript.get("range_end", words[-1]["end"]))
    starts = [float(w["start"]) for w in words]
    ends = [float(w["end"]) for w in words]
    sentence_end_re = re.compile(r"[.!?…][\"'»)]*$")
    sentence_start_re = re.compile(r"^[\"'«(]*[A-ZÀ-ÖØ-Þ0-9]")

    # Natural starts + a regular fallback grid. The fallback is important when
    # Whisper omits punctuation in spontaneous speech.
    anchors = {0}
    for i in range(1, len(words)):
        prev = str(words[i - 1]["word"]).strip()
        gap = starts[i] - ends[i - 1]
        curr = str(words[i]["word"]).strip()
        is_sentence = bool(sentence_end_re.search(prev))
        is_pause = gap >= 0.28
        is_capital_start = bool(sentence_start_re.search(curr))
        if (is_sentence or
                (anchor_mode in ("current", "sentence_pause") and is_pause) or
                (anchor_mode == "current" and is_capital_start)):
            anchors.add(i)
    if use_grid:
        t = max(range_start + 3.0, starts[0] + 3.0)
        while t < range_end - min_duration:
            idx = bisect.bisect_left(starts, t)
            if idx < len(words):
                # Back up to the nearest sensible boundary within 1.5 seconds.
                candidates_before = [k for k in anchors if 0 < k <= idx and starts[idx] - starts[k] <= 1.5]
                anchors.add(max(candidates_before, key=lambda k: starts[k]) if candidates_before else idx)
            t += 3.5
    stats["anchors"] = len(anchors)

    hooks = {"attends", "imagine", "regarde", "écoute", "pourquoi", "comment", "incroyable", "impossible", "jamais", "vraiment", "mais", "non", "quoi", "sérieux", "secret", "meilleur", "pire", "choisis", "choisir", "personne", "voici", "alors", "sauf", "pourtant", "si"}
    expressive = {"incroyable", "dingue", "fou", "folle", "énorme", "terrible", "horrible", "génial", "magnifique", "peur", "choqué", "choquée", "drôle", "impossible", "jamais", "adoré", "déteste", "surprise", "surpris", "grave", "parfait"}
    fillers = {"euh", "heu", "bah", "ben", "genre", "voilà", "enfin"}
    contrasts = {"mais", "pourtant", "cependant", "sauf", "alors", "finalement", "contrairement", "ensuite", "jusque"}
    dependent_openers = {"mais", "et", "donc", "alors", "ça", "cela", "il", "elle", "ils", "elles", "ce", "cette", "ces", "qui", "que"}

    candidates = []
    # Candidate endpoints adapt to the active campaign duration range.
    span = max(0.0, max_duration - min_duration)
    targets = tuple(sorted({round(min_duration + span * fraction, 3)
                            for fraction in (0.0, 0.25, 0.5, 0.75, 1.0)}))
    center_duration = (min_duration + max_duration) / 2.0
    for i in sorted(anchors):
        start = max(range_start, starts[i] - 0.05)
        if range_end - start < min_duration:
            continue
        left = bisect.bisect_left(ends, start + min_duration)
        right = bisect.bisect_right(ends, min(range_end, start + max_duration))
        if left >= right:
            continue
        possible = set()
        for target in targets:
            target_end = start + target
            lo = max(left, bisect.bisect_left(ends, target_end - 4.0))
            hi = min(right, bisect.bisect_right(ends, target_end + 4.0))
            if hi > lo:
                possible.update(range(lo, hi))
        if not possible:
            possible.update(range(left, right))
        possible = [j for j in possible if j > i]
        if not possible:
            continue

        def endpoint_quality(j):
            dur = ends[j] - start
            ending = str(words[j]["word"]).strip()
            punctuation = 1 if sentence_end_re.search(ending) else 0
            next_gap = (starts[j + 1] - ends[j]) if j + 1 < len(words) else 0.0
            natural_stop = 1 if punctuation or next_gap >= 0.28 else 0
            nearest = min(abs(dur - x) for x in targets)
            return natural_stop * 3.0 - nearest * 0.10 + min(j - i + 1, 75) * 0.01

        # Keep multiple endings so an excellent clip is not missed just because
        # a different endpoint has punctuation.
        best_js = sorted(possible, key=endpoint_quality, reverse=True)[:5]
        for j in best_js:
            stats["endpoint_candidates_examined"] += 1
            stats["candidate_windows_considered"] += 1
            # Keep a small tail after the final word for natural audio decay, but
            # never let that padding cross into the next recognized word/turn.
            padded_end = min(range_end, ends[j] + 0.12)
            if j + 1 < len(words) and starts[j + 1] >= ends[j]:
                end = min(padded_end, starts[j + 1])
            else:
                end = padded_end
            dur = end - start
            if not (min_duration <= dur <= max_duration):
                reject("duration_outside_requested_range", start, end)
                continue
            chosen = words[i:j + 1]
            toks = [re.sub(r"^[^\wÀ-ÿ]+|[^\wÀ-ÿ]+$", "", str(w["word"]).lower()) for w in chosen]
            toks = [x for x in toks if x]
            if len(toks) < 10:
                reject("fewer_than_10_words", start, end, " ".join(str(w["word"]) for w in chosen))
                continue
            normalized = " ".join(toks)
            if any(p in normalized for p in known_hallucinations):
                reject("known_subtitle_or_credit_hallucination", start, end, " ".join(str(w["word"]) for w in chosen))
                continue
            run, max_run = 1, 1
            for k in range(1, len(toks)):
                run = run + 1 if toks[k] == toks[k - 1] else 1
                max_run = max(max_run, run)
            unique_ratio = len(set(toks)) / max(1, len(toks))
            # Hard reject only obvious word loops, not normal repeated names/phrases.
            if max_run >= 4 or unique_ratio < 0.30:
                reject("repetition_or_low_lexical_diversity", start, end, " ".join(str(w["word"]) for w in chosen),
                       repeated_run=max_run, unique_ratio=round(unique_ratio, 4))
                continue
            probabilities = [max(0.0, min(1.0, float(w.get("probability", 0.55) or 0.0))) for w in chosen]
            avg_conf = sum(probabilities) / len(probabilities) if probabilities else 0.0
            low_conf_ratio = sum(p < 0.30 for p in probabilities) / max(1, len(probabilities))
            if avg_conf < 0.30 or low_conf_ratio > 0.75:
                reject("low_word_confidence", start, end, " ".join(str(w["word"]) for w in chosen),
                       avg_confidence=round(avg_conf, 4), low_confidence_ratio=round(low_conf_ratio, 4))
                continue

            text = " ".join(str(w["word"]) for w in chosen)
            wps = len(toks) / max(1.0, dur)
            hook_count = sum(1 for token in toks[:24] if token in hooks)
            expressive_count = sum(1 for token in toks if token in expressive)
            question_count, exclaim_count = text.count("?"), text.count("!")
            sentence_end = bool(sentence_end_re.search(str(chosen[-1]["word"]).strip()))
            start_boundary = (i == 0 or sentence_end_re.search(str(words[i - 1]["word"]).strip()) or starts[i] - ends[i - 1] > 0.28)
            filler_n = sum(1 for token in toks if token in fillers)
            gaps = sum(max(0.0, starts[k + 1] - ends[k]) for k in range(i, j))
            pause_ratio = gaps / max(1.0, dur)
            speech_coverage = min(1.0, sum(max(0.0, min(end, float(w["end"])) - max(start, float(w["start"]))) for w in chosen) / max(1.0, dur))
            # A long interval held together by a few sparse utterances is not a usable
            # clip. Keep this as a conservative rejection rather than rewarding pauses.
            if speech_coverage < min_speech_coverage:
                stats["rejected_sparse_windows"] += 1
                reject("speech_coverage_below_threshold", start, end, text,
                       speech_coverage=round(speech_coverage, 4), threshold=min_speech_coverage)
                continue
            next_gap = (starts[j + 1] - ends[j]) if j + 1 < len(words) else 0.0
            if not sentence_end and next_gap < incomplete_end_gap:
                stats["rejected_incomplete_endings"] += 1
                reject("incomplete_ending_without_pause", start, end, text,
                       next_gap=round(next_gap, 4), threshold=incomplete_end_gap)
                continue
            # Balanced score: speech density, intelligibility, coherent boundaries and
            # variety matter more than a narrow dictionary of clickbait words.
            contrast_count = sum(1 for token in toks if token in contrasts)
            specificity = min(1.0, sum(bool(re.search(r"\d", str(w["word"]))) for w in chosen) / 2.0)
            narrative = 28.0 + min(24.0, question_count * 12.0 + exclaim_count * 5.0)
            narrative += min(20.0, contrast_count * 8.0) + min(12.0, expressive_count * 2.0)
            narrative += specificity * 8.0 + min(8.0, hook_count * 1.3)
            narrative = max(0.0, min(100.0, narrative - min(18.0, filler_n * 1.2)))
            transcript_quality = 100.0 * avg_conf
            transcript_quality -= min(25.0, low_conf_ratio * 32.0)
            transcript_quality -= min(30.0, max(0.0, 0.48 - unique_ratio) * 60.0)
            transcript_quality -= min(20.0, max(0.0, pause_ratio - 0.18) * 50.0)
            transcript_quality = max(0.0, min(100.0, transcript_quality))
            opener = toks[0] if toks else ""
            autonomy = 46.0 + (18.0 if start_boundary else 0.0) + (20.0 if sentence_end else 0.0)
            autonomy -= 18.0 if opener in dependent_openers else 0.0
            autonomy += min(12.0, max(0.0, (unique_ratio - 0.4) * 20.0))
            autonomy = max(0.0, min(100.0, autonomy))
            comprehensibility = 100.0 * (0.55 * speech_coverage + 0.45 * (1.0 - low_conf_ratio))
            comprehensibility = max(0.0, min(100.0, comprehensibility))
            score = round(narrative * weights["narrative_interest"] +
                          comprehensibility * weights["comprehensibility"] +
                          transcript_quality * weights["transcript_quality"] +
                          autonomy * weights["autonomy"], 1)

            reasons = []
            if sentence_end: reasons.append("fin de phrase naturelle")
            if start_boundary: reasons.append("début de séquence cohérent")
            if speech_coverage >= 0.48: reasons.append("dialogue suffisamment continu")
            if contrast_count: reasons.append("marqueur de contraste ou de progression")
            if question_count: reasons.append("question détectée")
            if avg_conf >= 0.68: reasons.append("confiance de transcription élevée")
            elif avg_conf < 0.5: reasons.append("transcription à vérifier")
            title = " ".join(str(w["word"]) for w in chosen[:9]).strip()
            if len(title) > 88:
                title = title[:85].rsplit(" ", 1)[0] + "…"
            label = "signaux élevés" if score >= 72 else ("signaux modérés" if score >= 56 else "à vérifier")
            defects = []
            if low_conf_ratio > 0.20: defects.append("plusieurs mots à faible confiance")
            if pause_ratio > 0.24: defects.append("pauses notables")
            if not sentence_end: defects.append("fin de phrase incomplète")
            if opener in dependent_openers: defects.append("début potentiellement dépendant du contexte")
            candidate = {"start": round(start, 3), "end": round(end, 3), "duration": round(dur, 1),
                               "score": score, "quality_label": label, "text": text, "title": title,
                               "reasons": reasons[:4], "words": len(toks), "confidence": round(avg_conf, 2),
                               "speech_coverage": round(speech_coverage, 2),
                               "scores": {"narrative_interest": round(narrative),
                                          "comprehensibility": round(comprehensibility),
                                          "autonomy": round(autonomy),
                                          "transcript_quality": round(transcript_quality),
                                          "audio_quality": None, "visual_quality": None,
                                          "audiovisual": None, "campaign_fit": 100},
                               "defects": defects,
                               "score_method": "indice de tri heuristique explicable, sans mesure de viralité"}
            id_payload = f"{transcript.get('source', '')}|{candidate['start']:.3f}|{candidate['end']:.3f}|{text}"
            candidate["candidate_id"] = hashlib.sha256(id_payload.encode("utf-8")).hexdigest()[:20]
            candidates.append(candidate)
            if capture_audit:
                stats.setdefault("candidate_audit", []).append({"candidate_id": candidate["candidate_id"],
                    "start": candidate["start"], "end": candidate["end"], "text": text,
                    "decision": "pending_diversity", "reason": None,
                    "scores": candidate["scores"], "score": score,
                    "speech_coverage": round(speech_coverage, 4),
                    "confidence": round(avg_conf, 4)})

    stats["raw_candidates"] = len(candidates)
    candidates.sort(key=lambda c: (-c["score"], abs(c["duration"] - center_duration), c["start"]))
    # De-duplicate both overlapping time ranges and near-identical spoken content.
    # Text comparison matters when Whisper timestamps are sparse or malformed.
    def shingles(candidate):
        toks = re.findall(r"[a-zà-ÿ0-9']+", unicodedata.normalize("NFC", candidate["text"].lower()))
        return {tuple(toks[i:i+3]) for i in range(max(1, len(toks)-2)) if len(toks[i:i+3]) == 3}
    audit_by_id = {row["candidate_id"]: row for row in stats.get("candidate_audit", []) if row.get("candidate_id")}
    selected = []
    for c in candidates:
        too_similar = False
        c_shingles = shingles(c)
        for old in selected:
            overlap = max(0.0, min(c["end"], old["end"]) - max(c["start"], old["start"]))
            ratio = overlap / max(1.0, min(c["duration"], old["duration"]))
            midpoint_gap = abs((c["start"] + c["end"]) / 2 - (old["start"] + old["end"]) / 2)
            old_shingles = shingles(old)
            shared = len(c_shingles & old_shingles) / max(1, len(c_shingles | old_shingles))
            duplicate_reasons = []
            if ratio > overlap_threshold: duplicate_reasons.append("time_overlap")
            if midpoint_gap < midpoint_distance: duplicate_reasons.append("midpoint_distance")
            if shared > text_similarity_threshold: duplicate_reasons.append("text_shingle_similarity")
            if duplicate_reasons:
                too_similar = True
                duplicate_of = old["candidate_id"]
                break
        if too_similar:
            stats["rejected_as_redundant"] += 1
            if c["candidate_id"] in audit_by_id:
                audit_by_id[c["candidate_id"]].update({"decision": "rejected", "reason": duplicate_reasons[0],
                                                        "rejection_reasons": duplicate_reasons,
                                                        "duplicate_of": duplicate_of,
                                                        "text_similarity": round(shared, 4),
                                                        "time_overlap_ratio": round(ratio, 4),
                                                        "midpoint_distance_seconds": round(midpoint_gap, 4)})
            continue
        if len(selected) < limit:
            selected.append(c)
            if c["candidate_id"] in audit_by_id:
                audit_by_id[c["candidate_id"]].update({"decision": "selected", "reason": None,
                                                        "rank": len(selected)})
        else:
            stats["limited_by_quota"] = True
            stats["rejected_by_quota"] += 1
            if c["candidate_id"] in audit_by_id:
                audit_by_id[c["candidate_id"]].update({"decision": "rejected", "reason": "result_quota"})

    return finish(selected[:limit])

def _sentence_units_for_story(transcript):
    """Build cuttable narrative units from word timings; only end at sentence/phrase boundaries."""
    words = _normal_words(transcript)
    if not words:
        return []
    units, current = [], []

    def flush(reason="fin de phrase"):
        nonlocal current
        if not current:
            return
        text = " ".join(str(w.get("word", "")).strip() for w in current).strip()
        if text:
            units.append({
                "start": float(current[0]["start"]),
                "end": float(current[-1]["end"]),
                "text": text,
                "reason": reason,
            })
        current = []

    for w in words:
        current.append(w)
        token = str(w.get("word", "")).strip()
        # Never split on silence alone: only a recognized sentence-ending mark can create a cut.
        if re.search(r"[.!?…][\"'»)]*$", token):
            flush("fin de phrase")
    flush("fin du passage")
    return [u for u in units if u["end"] > u["start"]]

def _cliffhanger_score(current_text, next_text=""):
    """Heuristic narrative hook score. This is a ranking aid, not semantic understanding."""
    text = (current_text or "").strip()
    low = text.lower()
    nxt = (next_text or "").strip().lower()
    score, reasons = 0.0, []
    if "?" in text:
        score += 3.8; reasons.append("question qui appelle une suite")
    if "!" in text:
        score += 0.8; reasons.append("fin expressive")
    suspense_patterns = [
        r"\bet là\b", r"\bsauf que\b", r"\bmais là\b", r"\bmais le problème\b",
        r"\ble truc,? c'est\b", r"\bce qui va se passer\b", r"\bvous allez voir\b",
        r"\btu vas voir\b", r"\bà ce moment\b", r"\bjuste au moment\b",
        r"\bet le pire\b", r"\bet là,? surprise\b", r"\battends\b", r"\bimagine\b",
        r"\bpersonne ne savait\b", r"\bje ne m'attendais pas\b", r"\bincroyable\b",
    ]
    if any(re.search(p, low) for p in suspense_patterns):
        score += 3.0; reasons.append("indice de suspense ou de rebondissement")
    if re.search(r"(?:\.\.\.|…|:)\s*$", text):
        score += 2.2; reasons.append("suspension narrative")
    if re.search(r"\b(pourtant|soudain|finalement|exactement|jamais|personne|secret|révélation)\b", low):
        score += 1.0; reasons.append("motif narratif fort")
    if re.match(r"^(mais|et là|sauf que|alors|ensuite|à ce moment|le lendemain)\b", nxt):
        score += 1.2; reasons.append("transition vers la suite")
    if re.search(r"\b(bref|voilà|c'est tout|fin de l'histoire)\b", low):
        score -= 2.0
    # Complete sentences remain the only normal cut points; add a small baseline.
    if re.search(r"[.!?…][\"'»)]*$", text):
        score += 1.0
    return score, reasons

_STORY_STOP_WORDS = {
    "alors", "au", "aucun", "avec", "avoir", "bon", "car", "ce", "ces", "cette", "ceux",
    "chez", "comme", "comment", "dans", "de", "des", "du", "elle", "elles", "en", "est",
    "et", "eux", "fait", "faire", "fois", "font", "ici", "il", "ils", "je", "la", "le",
    "les", "leur", "lui", "ma", "mais", "me", "meme", "mes", "moi", "mon", "ne", "nos",
    "notre", "nous", "on", "ou", "par", "pas", "pour", "qu", "que", "quel", "quelle",
    "qui", "sa", "se", "ses", "son", "sur", "ta", "te", "tes", "toi", "ton", "tu", "un",
    "une", "vos", "votre", "vous", "y", "c", "d", "j", "l", "n", "s", "t",
}


def _story_terms(text):
    value = unicodedata.normalize("NFD", str(text or "").lower())
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return [token for token in re.findall(r"[a-z0-9']+", value)
            if len(token) > 2 and token not in _STORY_STOP_WORDS]


def _story_similarity(left_text, right_text, document_frequency=None, doc_count=1):
    """Small dependency-free TF-IDF cosine used only as a topic continuity cue."""
    from collections import Counter
    from math import log, sqrt

    left, right = Counter(_story_terms(left_text)), Counter(_story_terms(right_text))
    if not left or not right:
        return 0.0
    frequency = document_frequency or {}

    def vector(counter):
        return {term: count * (1.0 + log((doc_count + 1) / (frequency.get(term, 0) + 1)))
                for term, count in counter.items()}

    a, b = vector(left), vector(right)
    dot = sum(value * b.get(term, 0.0) for term, value in a.items())
    norm_a = sqrt(sum(value * value for value in a.values()))
    norm_b = sqrt(sum(value * value for value in b.values()))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def detect_narrative_sequences(transcript, visual_transitions=()):
    """Stage A: infer coarse narrative sequences from transcript context and timing.

    Visual cuts are optional corroborating evidence. They never create a boundary
    on their own. No speaker diarization or semantic language model is implied.
    """
    from collections import Counter

    raw_segments = transcript.get("segments", []) or []
    rows = []
    for index, seg in enumerate(raw_segments):
        text = str(seg.get("text", "")).strip()
        try:
            start, end = float(seg.get("start", 0)), float(seg.get("end", 0))
            no_speech = float(seg.get("no_speech_prob", 0.0) or 0.0)
            avg_logprob = float(seg.get("avg_logprob", 0.0) or 0.0)
            compression = float(seg.get("compression_ratio", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        terms = _story_terms(text)
        if not text or end <= start or no_speech > 0.82 or avg_logprob < -1.45 or compression > 2.9:
            continue
        # Extremely repetitive ASR output (often laughter/music loops) is not a
        # topic vector. Its elapsed time still remains inside adjacent episodes.
        lexical_ratio = len(set(terms)) / max(1, len(terms))
        if len(terms) >= 12 and lexical_ratio < 0.24:
            continue
        normalized_text = unicodedata.normalize("NFD", text.lower())
        normalized_text = "".join(ch for ch in normalized_text if unicodedata.category(ch) != "Mn")
        raw_tokens = re.findall(r"[a-z0-9']+", normalized_text)
        rows.append({"index": index, "start": start, "end": end, "text": text,
                     "terms": terms, "tokens": _story_terms(text), "raw_tokens": raw_tokens})

    if not rows:
        return []

    df = Counter(term for row in rows for term in set(row["terms"]))
    exact_openers = Counter()
    opener_times = {}
    opener_for_row = []
    for row in rows:
        tokens = row["raw_tokens"][:3]
        opener = " ".join(tokens) if len(tokens) >= 2 else ""
        opener_terms = _story_terms(opener)
        structural_intro = (len(tokens) >= 3 and tokens[0] == "dans" and
                            tokens[-1] in {"vie", "pubs", "jeu", "histoire", "aventure"})
        if ((len(row["raw_tokens"]) > 6 and not structural_intro) or
                (len(set(opener_terms)) < 2 and not structural_intro)):
            opener = ""
        opener_for_row.append(opener)
        if opener:
            exact_openers[opener] += 1
            opener_times.setdefault(opener, []).append(row["start"])

    visual_times = sorted(float(t) for t in visual_transitions or () if isinstance(t, (int, float)))
    range_start = float(transcript.get("range_start", rows[0]["start"]) or 0.0)
    range_end = float(transcript.get("range_end", rows[-1]["end"]) or rows[-1]["end"])
    boundaries = []
    for i in range(1, len(rows)):
        previous, current = rows[i - 1], rows[i]
        gap = max(0.0, current["start"] - previous["end"])
        left_context = " ".join(row["text"] for row in rows[max(0, i - 3):i])
        right_context = " ".join(row["text"] for row in rows[i:min(len(rows), i + 3)])
        similarity = _story_similarity(left_context, right_context, df, len(rows))
        opener = opener_for_row[i]
        repeated_opener = bool(opener and exact_openers[opener] >= 2 and
                               any(abs(current["start"] - t) >= 10.0 for t in opener_times[opener]))
        visual_nearby = any(abs(current["start"] - cut) <= 0.75 for cut in visual_times)

        # Keep a direct question and its short answer/reactive turn together.
        previous_ends_question = bool(re.search(r"\?+[\"'»)]*$", previous["text"].strip()))
        current_tokens = current["tokens"]
        short_reply = (len(current_tokens) <= 5 and
                       bool(re.match(r"^(oui|non|si|quoi|comment|exactement|ah|hein)\b",
                                     unicodedata.normalize("NFD", current["text"].lower()).replace("\u0301", ""))))
        if previous_ends_question and gap <= 1.5 and short_reply and not repeated_opener:
            continue

        evidence = []
        if repeated_opener:
            evidence.append("reprise d'une amorce de séquence")
        topic_with_pause = similarity <= 0.16 and gap >= 6.0
        topic_with_visual = similarity <= 0.12 and visual_nearby
        if topic_with_pause:
            evidence.append("rupture lexicale avec pause de parole")
        if topic_with_visual:
            evidence.append("rupture lexicale corroborée par changement de plan")
        # Require a recurring opener seen in another distant scene or a strong lexical break plus a long
        # pause (or corroborating visual cut). Ordinary dialogue turns stay joined.
        if not repeated_opener and not (topic_with_pause or topic_with_visual):
            continue
        if boundaries and current["start"] - boundaries[-1]["time"] < 4.0:
            continue
        boundaries.append({"time": current["start"], "similarity": round(similarity, 4),
                           "speech_gap": round(gap, 3), "evidence": evidence or ["changement de sujet probable"],
                           "opener": opener if repeated_opener else None})

    # Repeated contrast formats commonly announce two linked halves (for example
    # an idealized version followed by a real-life version). Keep both halves in
    # one elementary sequence; the next repeated first-half label starts the next.
    paired = []
    first_opener = opener_for_row[0] if opener_for_row else ""
    last_contrast = ({"opener": first_opener, "time": rows[0]["start"]}
                     if first_opener.startswith("dans ") else None)
    for boundary in boundaries:
        opener = boundary.get("opener") or ""
        is_contrast_label = opener.startswith("dans ")
        if is_contrast_label:
            if last_contrast and last_contrast["opener"] != opener and boundary["time"] - last_contrast["time"] <= 30.0:
                continue
            last_contrast = boundary
            paired.append(boundary)
            continue
        # A short repeated setup directly after a structural marker is usually
        # the first line of that scene, not a second sequence boundary.
        if opener and paired and (paired[-1].get("opener") or "").startswith("dans ") and boundary["time"] - paired[-1]["time"] <= 2.0:
            continue
        paired.append(boundary)
    boundaries = paired

    points = [range_start] + [item["time"] for item in boundaries if range_start < item["time"] < range_end] + [range_end]
    sequences = []
    for seq_idx, (start, end) in enumerate(zip(points, points[1:]), 1):
        content = [row["text"] for row in rows if row["start"] < end and row["end"] > start]
        previous_boundary = next((item for item in boundaries if item["time"] == start), None)
        next_boundary = next((item for item in boundaries if item["time"] == end), None)
        text = " ".join(content).strip()
        sequences.append({"index": seq_idx, "start": round(start, 3), "end": round(end, 3),
                          "duration": round(end - start, 3), "text": text,
                          "title": " ".join(text.split()[:10]),
                          "start_evidence": previous_boundary["evidence"] if previous_boundary else ["début du contenu"],
                          "end_evidence": next_boundary["evidence"] if next_boundary else ["fin du contenu"],
                          "boundary_confidence": "strong" if previous_boundary and previous_boundary.get("opener") else
                                                ("corroborated" if previous_boundary else "source_edge")})
    return sequences


def _partition_sequences(sequences, count):
    """Stage B: partition only at detected sequence boundaries; duration is soft."""
    n = len(sequences)
    parts_count = min(max(1, int(count)), n)
    if parts_count <= 1:
        return [(0, n)] if n else []
    total_start, total_end = sequences[0]["start"], sequences[-1]["end"]
    target = (total_end - total_start) / parts_count
    # Dynamic programming minimizes total relative duration imbalance. Every
    # candidate cut is a Stage A sequence boundary, never an arbitrary sentence.
    dp = [[None] * (n + 1) for _ in range(parts_count + 1)]
    dp[0][0] = (0.0, [])
    for k in range(1, parts_count + 1):
        for end_i in range(k, n + 1):
            best = None
            for start_i in range(k - 1, end_i):
                prior = dp[k - 1][start_i]
                if prior is None:
                    continue
                duration = sequences[end_i - 1]["end"] - sequences[start_i]["start"]
                if duration <= 0:
                    continue
                penalty = abs(duration - target) / max(1.0, target)
                cut_strength = 0.0
                if start_i > 0:
                    cut_strength = 1.0 if sequences[start_i].get("boundary_confidence") == "strong" else 0.5
                candidate = (prior[0] + penalty * penalty, prior[1] + [start_i], -cut_strength)
                if best is None or candidate < best:
                    best = candidate
            if best is not None:
                dp[k][end_i] = (best[0], best[1])
    cuts = dp[parts_count][n][1] if dp[parts_count][n] else [0]
    cuts = cuts + [n]
    return list(zip(cuts, cuts[1:]))


def _build_story_parts_legacy(transcript, count, range_start, range_end):
    """Conservative compatibility path when transcript evidence is too sparse."""
    units = _sentence_units_for_story(transcript)
    if len(units) < count:
        raise ValueError(f"Pas assez de phrases complètes ({len(units)}) pour créer {count} parties. Réduis le nombre de parties ou vérifie la transcription.")
    cuts = []
    for i, unit in enumerate(units[:-1]):
        score, reasons = _cliffhanger_score(unit["text"], units[i + 1]["text"])
        end = min(range_end, float(unit["end"]))
        if range_start < end < range_end - 2:
            cuts.append({"time": end, "score": score, "reasons": reasons or [unit.get("reason", "fin de phrase")], "text": unit["text"]})
    if not cuts:
        raise ValueError("Aucune frontière de phrase exploitable n'a été trouvée.")
    parts, current_start = [], range_start
    for part_no in range(1, count):
        remaining_parts = count - part_no + 1
        remaining_time = range_end - current_start
        target = remaining_time / remaining_parts
        min_len = max(8.0, target * 0.45)
        max_len = min(target * 1.80, remaining_time - max(8.0, target * 0.35) * (remaining_parts - 1))
        possible = [c for c in cuts if current_start + min_len < c["time"] < current_start + max_len]
        if not possible:
            possible = [c for c in cuts if c["time"] > current_start + max(6.0, target * 0.30)
                        and c["time"] < range_end - max(6.0, target * 0.30) * (remaining_parts - 1)]
        if not possible:
            break
        chosen = max(possible, key=lambda c: c["score"] * 2.4 -
                     abs((c["time"] - current_start) - target) / max(1.0, target) * 2.2)
        part_end = float(chosen["time"])
        parts.append({"index": len(parts) + 1, "start": round(current_start, 3), "end": round(part_end, 3),
                      "duration": round(part_end - current_start, 1), "teaser": chosen["text"][-240:],
                      "reason": "Découpage de secours sur phrase : " + " · ".join(chosen["reasons"][:2]),
                      "score": round(chosen["score"], 1), "sequence_count": None,
                      "boundary_confidence": "low_transcript_evidence"})
        current_start = part_end
        cuts = [c for c in cuts if c["time"] > current_start + 0.01]
    if range_end - current_start < 8 and parts:
        parts[-1]["end"] = round(range_end, 3)
        parts[-1]["duration"] = round(range_end - parts[-1]["start"], 1)
    else:
        parts.append({"index": len(parts) + 1, "start": round(current_start, 3), "end": round(range_end, 3),
                      "duration": round(range_end - current_start, 1), "teaser": "Dernière partie de la vidéo",
                      "reason": "Fin du contenu · segmentation de secours", "score": 0,
                      "sequence_count": None, "boundary_confidence": "low_transcript_evidence"})
    for i, part in enumerate(parts, 1):
        part["index"] = i
    return parts


def build_story_parts(transcript, count, video_duration=None, visual_transitions=()):
    """Detect narrative sequences, then build chronological episodes at their boundaries."""
    count = max(2, min(30, int(count)))
    range_start = float(transcript.get("range_start", 0.0) or 0.0)
    range_end = float(transcript.get("range_end", video_duration or 0.0) or video_duration or 0.0)
    if video_duration is not None:
        range_end = min(float(video_duration), range_end if range_end > 0 else float(video_duration))
    if range_end <= range_start:
        raise ValueError("La durée de la vidéo transcrite est invalide.")
    sequences = detect_narrative_sequences(transcript, visual_transitions)
    if not sequences:
        raise ValueError("La transcription ne contient pas de séquence narrative exploitable.")
    if len(sequences) < count * 2:
        return _build_story_parts_legacy(transcript, count, range_start, range_end)
    # Extend any first/last speech segment to the source edges. Episodes tile the
    # source so that no silent gaps or unassigned material disappear on export.
    sequences[0]["start"] = round(range_start, 3)
    sequences[-1]["end"] = round(range_end, 3)
    sequences[-1]["duration"] = round(sequences[-1]["end"] - sequences[-1]["start"], 3)
    groups = _partition_sequences(sequences, count)
    parts = []
    for part_index, (first, last) in enumerate(groups, 1):
        group = sequences[first:last]
        start = range_start if first == 0 else group[0]["start"]
        end = range_end if last == len(sequences) else group[-1]["end"]
        text = " ".join(item["text"] for item in group).strip()
        evidence = group[-1]["end_evidence"]
        parts.append({"index": part_index, "start": round(start, 3), "end": round(end, 3),
                      "duration": round(end - start, 1), "teaser": text[-240:] or "Épisode",
                      "reason": "Frontière de séquence : " + " · ".join(evidence[:2]),
                      "score": None, "sequence_count": len(group),
                      "sequence_titles": [item["title"] for item in group[:8]],
                      "boundary_confidence": group[-1]["boundary_confidence"]})
    return parts
