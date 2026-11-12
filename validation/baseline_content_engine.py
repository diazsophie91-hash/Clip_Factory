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

def rank_candidate_excerpts(transcript, min_duration=20.0, max_duration=45.0, limit=8, diagnostics=None):
    """Generate a broad set of speech-led clip candidates, then rank and diversify them.

    Scores are ranking aids, not probabilities of virality. Candidates are not
    discarded merely for lacking lexical hooks, punctuation, or a high absolute score.
    """
    import bisect
    min_duration = max(3.0, float(min_duration))
    max_duration = max(min_duration, float(max_duration))
    limit = max(1, min(30, int(limit)))
    segments = transcript.get("segments", []) or []
    stats = {"input_segments": len(segments), "rejected_hallucination_segments": 0,
             "rejected_quality_segments": 0, "retained_segments": 0,
             "normalized_words": 0, "anchors": 0, "candidate_windows_considered": 0,
             "raw_candidates": 0, "selected_candidates": 0,
             "rejected_as_redundant": 0, "rejected_by_quota": 0,
             "limited_by_quota": False}
    def finish(selected):
        stats["selected_candidates"] = len(selected)
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update(stats)
        return selected
    def reject(reason, start, end, text="", **extra):
        if diagnostics is not None:
            payload = f"{float(start):.3f}|{float(end):.3f}|{text}"
            row = {"candidate_id": hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20],
                   "start": round(float(start), 3), "end": round(float(end), 3),
                   "text": str(text), "decision": "rejected", "reason": reason}
            row.update(extra)
            stats.setdefault("candidate_audit", []).append(row)
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
            if diagnostics is not None:
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
            if diagnostics is not None:
                stats.setdefault("rejected_segments", []).append({"segment_index": segment_index,
                    "start": seg.get("start"), "end": seg.get("end"), "text": seg.get("text", ""),
                    "reason": "whisper_segment_quality", "no_speech_prob": no_speech,
                    "avg_logprob": avg_logprob, "compression_ratio": compression})
            continue
        safe_segments.append(seg)

    working = dict(transcript)
    working["segments"] = safe_segments
    words = _normal_words(working)
    stats["retained_segments"] = len(safe_segments)
    stats["normalized_words"] = len(words)
    if len(words) < 10:
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
        if sentence_end_re.search(prev) or gap >= 0.28 or sentence_start_re.search(curr):
            anchors.add(i)
    stats["anchors"] = len(anchors)
    t = max(range_start + 3.0, starts[0] + 3.0)
    while t < range_end - min_duration:
        idx = bisect.bisect_left(starts, t)
        if idx < len(words):
            # Back up to the nearest sensible boundary within 1.5 seconds.
            candidates_before = [k for k in anchors if 0 < k <= idx and starts[idx] - starts[k] <= 1.5]
            anchors.add(max(candidates_before, key=lambda k: starts[k]) if candidates_before else idx)
        t += 3.5

    hooks = {"attends", "imagine", "regarde", "écoute", "pourquoi", "comment", "incroyable", "impossible", "jamais", "vraiment", "mais", "non", "quoi", "sérieux", "secret", "meilleur", "pire", "choisis", "choisir", "personne", "voici", "alors", "sauf", "pourtant", "si"}
    expressive = {"incroyable", "dingue", "fou", "folle", "énorme", "terrible", "horrible", "génial", "magnifique", "peur", "choqué", "choquée", "drôle", "impossible", "jamais", "adoré", "déteste", "surprise", "surpris", "grave", "parfait"}
    fillers = {"euh", "heu", "bah", "ben", "genre", "voilà", "enfin"}
    contrasts = {"mais", "pourtant", "cependant", "sauf", "alors", "finalement", "contrairement", "ensuite", "jusque"}
    dependent_openers = {"mais", "et", "donc", "alors", "ça", "cela", "il", "elle", "ils", "elles", "ce", "cette", "ces", "qui", "que"}

    candidates = []
    # One or two candidate end points per start, over a wider range of durations.
    targets = (24.0, 29.0, 34.0, 39.0, 44.0)
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
            stats["candidate_windows_considered"] += 1
            end = min(range_end, ends[j] + 0.12)
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
            score = round(narrative * 0.45 + transcript_quality * 0.35 + autonomy * 0.20, 1)

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
                               "scores": {"narrative_signals": round(narrative), "transcript_quality": round(transcript_quality),
                                          "autonomy": round(autonomy), "audiovisual": None},
                               "defects": defects, "score_method": "heuristique de transcription"}
            candidates.append(candidate)
            if diagnostics is not None:
                payload = f"{candidate['start']:.3f}|{candidate['end']:.3f}|{text}"
                stats.setdefault("candidate_audit", []).append({"candidate_id": hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20],
                    "start": candidate["start"], "end": candidate["end"], "text": text,
                    "decision": "pending_diversity", "reason": None, "score": score,
                    "scores": candidate["scores"], "speech_coverage": round(speech_coverage, 4),
                    "confidence": round(avg_conf, 4)})

    stats["raw_candidates"] = len(candidates)
    candidates.sort(key=lambda c: (-c["score"], abs(c["duration"] - 32.0), c["start"]))
    # De-duplicate near-identical cuts while allowing some overlap where the content
    # genuinely differs. A second pass relaxes diversity if a long source has few options.
    selected = []
    audit_by_key = {(row["start"], row["end"], row["text"]): row
                    for row in stats.get("candidate_audit", [])}
    last_diversity_reasons = {}
    for pass_index, max_overlap in enumerate((0.42, 0.62)):
        for candidate_index, c in enumerate(candidates):
            if c in selected:
                continue
            too_similar = False
            reasons = []
            duplicate_of = None
            for old in selected:
                overlap = max(0.0, min(c["end"], old["end"]) - max(c["start"], old["start"]))
                ratio = overlap / max(1.0, min(c["duration"], old["duration"]))
                midpoint_gap = abs((c["start"] + c["end"]) / 2 - (old["start"] + old["end"]) / 2)
                reasons = []
                if ratio > max_overlap: reasons.append("time_overlap_initial_pass" if pass_index == 0 else "time_overlap_relaxed_pass")
                if midpoint_gap < 9.0: reasons.append("midpoint_distance")
                if reasons:
                    too_similar = True
                    duplicate_of = old
                    break
            key = (c["start"], c["end"], c["text"])
            if too_similar:
                last_diversity_reasons[key] = (reasons, duplicate_of)
                continue
            selected.append(c)
            if key in audit_by_key:
                audit_by_key[key].update({"decision": "selected", "reason": None, "rank": len(selected)})
            if len(selected) >= limit:
                remaining = []
                for pending in candidates[candidate_index + 1:]:
                    pending_key = (pending["start"], pending["end"], pending["text"])
                    row = audit_by_key.get(pending_key)
                    if row and row.get("decision") == "pending_diversity":
                        row.update({"decision": "rejected", "reason": "result_quota"})
                        stats["rejected_by_quota"] += 1
                        remaining.append(row)
                stats["limited_by_quota"] = bool(remaining)
                for pending_key, (flags, duplicate) in last_diversity_reasons.items():
                    row = audit_by_key.get(pending_key)
                    if row and row.get("decision") == "pending_diversity":
                        row.update({"decision": "rejected", "reason": flags[0],
                            "rejection_reasons": flags,
                            "duplicate_of": f"{duplicate['start']:.3f}-{duplicate['end']:.3f}"})
                        stats["rejected_as_redundant"] += 1
                return finish(selected)
    for key, (flags, duplicate) in last_diversity_reasons.items():
        row = audit_by_key.get(key)
        if row and row.get("decision") == "pending_diversity":
            row.update({"decision": "rejected", "reason": flags[0],
                        "rejection_reasons": flags,
                        "duplicate_of": f"{duplicate['start']:.3f}-{duplicate['end']:.3f}"})
            stats["rejected_as_redundant"] += 1
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

def build_story_parts(transcript, count, video_duration=None):
    """Choose variable-length, chronological parts at sentence boundaries with cliffhanger-aware scoring."""
    count = max(2, min(30, int(count)))
    range_start = float(transcript.get("range_start", 0.0) or 0.0)
    range_end = float(transcript.get("range_end", video_duration or 0.0) or video_duration or 0.0)
    if video_duration is not None:
        range_end = min(float(video_duration), range_end if range_end > 0 else float(video_duration))
    if range_end <= range_start:
        raise ValueError("La durée de la vidéo transcrite est invalide.")
    units = _sentence_units_for_story(transcript)
    if len(units) < count:
        raise ValueError(f"Pas assez de phrases complètes ({len(units)}) pour créer {count} parties. Réduis le nombre de parties ou vérifie la transcription.")

    # Build possible cut points after each complete phrase. The final unit is reserved for the last part.
    cuts = []
    for i, unit in enumerate(units[:-1]):
        next_text = units[i+1]["text"]
        score, reasons = _cliffhanger_score(unit["text"], next_text)
        end = min(range_end, float(unit["end"]))
        if range_start < end < range_end - 2:
            cuts.append({"time": end, "score": score, "reasons": reasons or [unit.get("reason", "fin de phrase")], "text": unit["text"]})
    if not cuts:
        raise ValueError("Aucune frontière de phrase exploitable n'a été trouvée.")

    parts = []
    current_start = range_start
    for part_no in range(1, count):
        remaining_parts = count - part_no + 1
        remaining_time = range_end - current_start
        target = remaining_time / remaining_parts
        min_len = max(8.0, target * 0.45)
        max_len = min(target * 1.80, remaining_time - max(8.0, target * 0.35) * (remaining_parts - 1))
        possible = [c for c in cuts if c["time"] > current_start + min_len and c["time"] < current_start + max_len]
        if not possible:
            # Relax bounds, but still never cut inside a word or sentence.
            possible = [c for c in cuts if c["time"] > current_start + max(6.0, target * 0.30)
                        and c["time"] < range_end - max(6.0, target * 0.30) * (remaining_parts - 1)]
        if not possible:
            break

        def candidate_value(c):
            length = c["time"] - current_start
            balance_penalty = abs(length - target) / max(1.0, target)
            # Narrative interest has greater weight than exact duration balance.
            return (c["score"] * 2.4) - (balance_penalty * 2.2)

        chosen = max(possible, key=candidate_value)
        part_end = float(chosen["time"])
        parts.append({
            "index": len(parts) + 1,
            "start": round(current_start, 3),
            "end": round(part_end, 3),
            "duration": round(part_end-current_start, 1),
            "teaser": chosen["text"][-240:],
            "reason": " · ".join(chosen["reasons"][:2]),
            "score": round(chosen["score"], 1),
        })
        current_start = part_end
        cuts = [c for c in cuts if c["time"] > current_start + 0.01]

    if range_end - current_start < 8 and parts:
        # Avoid a tiny tail: merge it into the previous episode rather than output a useless fragment.
        parts[-1]["end"] = round(range_end, 3)
        parts[-1]["duration"] = round(range_end - parts[-1]["start"], 1)
        parts[-1]["reason"] += " · fin de la vidéo"
    else:
        parts.append({
            "index": len(parts) + 1,
            "start": round(current_start, 3),
            "end": round(range_end, 3),
            "duration": round(range_end-current_start, 1),
            "teaser": "Dernière partie de la vidéo",
            "reason": "conclusion / fin du contenu",
            "score": 0,
        })
    for i, part in enumerate(parts, 1):
        part["index"] = i
    return parts
