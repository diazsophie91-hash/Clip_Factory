# Clip Factory

Clip Factory is a local Windows application for transcribing video, proposing short excerpts, preparing narrative episodes and exporting subtitled vertical clips. It listens on `127.0.0.1` only. Processing and media remain on this computer.

## Start

Use the existing environment and launcher:

```bat
start_clip_factory.bat
```

If `.venv` is missing, run `INSTALLER_ET_DEMARRER.bat`. It creates the runtime environment with Python 3.14 and installs only the packages in `requirements.txt`; it does not reinstall or merge the acoustic environment.

The confirmed runtime is Python 3.14.7 in `.venv`, Faster-Whisper 1.2.1, CTranslate2 4.8.5 and the bundled FFmpeg from imageio-ffmpeg 0.8.1. A separate `.alignenv` contains CPU PyTorch 2.11.0 and Transformers 4.57.6. Keep these environments separate. The imports for Faster-Whisper and `Wav2Vec2Processor` were checked; the acoustic model itself has not been benchmarked in this refactor.

The observed runtime package versions are recorded in [requirements.txt](requirements.txt) and [requirements-align.txt](requirements-align.txt). They are reference manifests for the working environments; do not install them over the existing environments as an update step. No dependency was installed or upgraded during this work.

The local model options and reasons for keeping them unchanged are documented in [COMPARAISON_MOTEURS.md](COMPARAISON_MOTEURS.md).

The [TikTok Studio UX reference](REFERENCE_UX.md) records the public product principles used for the navigation and the remaining design gaps.

## Main workflows

- **Tableau de bord** records clips, campaigns, manually entered metrics and the monthly earnings goal.
- **Atelier vidéo** imports a source, chooses independent excerpts or narrative episodes, transcribes text, supports text corrections and exports a vertical MP4 with subtitles.
- **Bibliothèque** lists the local source files, MP4 exports and saved sessions. It does not delete or move media.
- The shared navigation links to campaign records, clips/exports, dashboard statistics and settings. Campaigns, clips, statistics and settings still share the dashboard page in this release.
- Candidate ranking uses transcript-based heuristics. It exposes narrative interest, approximate comprehensibility, autonomy, transcription quality, campaign fit, and unmeasured audio/visual dimensions separately. The displayed index is a sorting aid, not a prediction of virality. Preview and listen before using a suggestion.
- Candidate-card ratings and independent source references are optional development-audit tools; no annotation or user review is required to generate or export clips. Candidate reviews are saved to `validation/annotations.json`. The blind reference view saves source hash, original timestamps, content type, narrative-interest rating and optional justification to `validation/reference_spans.json`, separately from transcripts and engine results.
- Campaigns can store minimum and maximum clip duration. Candidate generation and export enforce those limits when a campaign is selected.

## Tests

Run the local regression and HTTP smoke tests with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

These tests cover candidate mechanics, Amara credit rejection, narrative-part coverage, confidence defaults, one worker per session, session persistence, local page rendering, media byte-range preview and dashboard exports. Synthetic transcripts validate mechanics only; they do not demonstrate real-world clip quality.

The available real-source comparison and its limits are documented in [RAPPORT_VALIDATION_MOTEUR.md](RAPPORT_VALIDATION_MOTEUR.md). The local folder contains only two distinct source videos, so it cannot validate every requested genre. See [ETUDE_EVALUATION_AUTOMATIQUE.md](ETUDE_EVALUATION_AUTOMATIQUE.md) for public datasets, automatic metrics, candidate local models, and an evaluation plan designed to avoid making users annotate.

Re-run the frozen reference, score-weight profiles and segmentation profiles on the exact same stored transcripts with:

```powershell
.\.venv\Scripts\python.exe -m tools.run_comparison
```

This writes `validation/latest_comparison.json`, including video/transcript/engine hashes, per-candidate outcomes and rejection reasons. Candidate review precision and independent-span recall are optional development metrics and remain `null` until reviewers choose to save labels; they are not production prerequisites. The same schema accepts new source transcripts progressively; duplicate video hashes are recognized as one source. `--freeze-baseline` is only for intentionally updating the frozen reference manifest after review.

## Data and recovery

User data remains in `data.json`, `studio.json`, `transcript.json`, `sessions/` and `media/`. Before a release or manual edit, copy those paths and `app.py`/`forced_align.py` to a dated folder outside the media directories. The existing pre-refactor code copies are in `backups/20261109_pre_refonte/`.

To restore, stop Clip Factory, copy the chosen backup files back to their original paths, and restore the matching data snapshot only if the user intends to roll back that data. Never replace the current `media/` folder with a code backup. Check that the project and all source/export paths still exist before restarting.

## Current limits

- The app is still a single-file HTTP application with JSON persistence; this work does not migrate data to SQLite.
- Transcription and forced-alignment accuracy have not been measured on a labeled French corpus. The acoustic model import is available, but its model load/inference was not exercised here.
- Candidate analysis does not perform semantic-model inference or audiovisual quality analysis. The local semantic-model load attempt is documented in `RAPPORT_VALIDATION_MOTEUR.md`; no language model is integrated.
- Narrative part boundaries are heuristic. The proposed cuts need human review; the existing UI has no full timeline editor.
- Campaign data is entered by the user. There are no official social-network publishing or analytics API integrations.
- Upload progress, cancellation, account management and a general-purpose update workflow remain incomplete.
