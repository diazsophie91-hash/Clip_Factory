# Manifeste d'inventaire et de nettoyage

Inventaire initial constaté le 9 novembre 2026 dans `[dossier local du projet]`; complété avec le nettoyage vérifié du 11 novembre. Aucun média ou donnée utilisateur n'a été supprimé. Les montants sont des octets tels que remontés par le système de fichiers. Le but de ce document est de distinguer les caches reproductibles des données, des sauvegardes et des installateurs de récupération.

## Décision générale

- Le code courant n'importe ni les anciens bundles V0_11 à V0_15, ni les instantanés `*.backup_*`, ni `PATCH_CORRECTIF.py`, ni `find_python312.ps1`.
- Les `.pyc` des caches racine/tests/outils/validation étaient reproductibles sans référence runtime et ont été supprimés le 11 novembre (394 932 B). Les caches `.pyc` embarqués dans les bundles historiques sont conservés avec ces bundles.
- Les deux WAV de `media/temp_audio` ne sont référencés ni dans `studio.json`, ni dans les sessions/transcriptions sauvegardées. L'application sait extraire de nouveau ce son. Ils sont candidats de nettoyage, mais restent sous `media/` et sont donc conservés conformément à la consigne de préserver les médias.
- Les anciens bundles et snapshots ne sont pas nécessaires au lancement quotidien, mais ils sont des installateurs ou seuls points de restauration hors Git. Il est impossible de les qualifier de « sans risque » tant qu'aucune sauvegarde versionnée distincte n'existe. Les dossiers V0_13 contiennent en plus un `data.json` et des sessions historiques à préserver impérativement.
- `PATCH_CORRECTIF.py` et `find_python312.ps1` sont absents des dépendances de lancement; le premier est un correctif manuel de compatibilité/export et le second un outil de diagnostic Python 3.12. Ils restent utiles en dépannage et ne sont pas des déchets certains.

## Candidats de nettoyage, chemins et tailles

### Caches Python reproductibles

| Chemin | Octets | Motif |
|---|---:|---|
| `__pycache__/annotation_store.cpython-314.pyc` | 13 734 | Cache Python régénéré à l'import. |
| `__pycache__/app.cpython-314.pyc` | 194 766 | Cache Python régénéré à l'import. |
| `__pycache__/content_engine.cpython-314.pyc` | 44 626 | Cache Python régénéré à l'import. |
| `__pycache__/forced_align.cpython-314.pyc` | 16 656 | Cache Python régénéré à l'import. |
| `tools/__pycache__/__init__.cpython-314.pyc` | 209 | Cache Python régénéré. |
| `tools/__pycache__/benchmark_topic_boundaries.cpython-314.pyc` | 12 037 | Cache Python régénéré. |
| `tools/__pycache__/run_comparison.cpython-314.pyc` | 3 306 | Cache Python régénéré. |
| `tools/__pycache__/validation_harness.cpython-314.pyc` | 21 100 | Cache Python régénéré. |
| `tests/__pycache__/test_annotations.cpython-314.pyc` | 13 977 | Cache des tests régénéré. |
| `tests/__pycache__/test_content_engine.cpython-314.pyc` | 16 828 | Cache des tests régénéré. |
| `tests/__pycache__/test_qmsum_topic_audit.cpython-314.pyc` | 4 102 | Cache du test QMSum régénéré. |
| `tests/__pycache__/test_http_smoke.cpython-314.pyc` | 8 059 | Cache des tests régénéré. |
| `tests/__pycache__/test_sessions.cpython-314.pyc` | 4 856 | Cache des tests régénéré. |
| `validation/__pycache__/baseline_content_engine.cpython-314.pyc` | 40 676 | Cache Python régénéré. |
| **Total caches racine/tests/outils/validation** | **394 932** | Caches reproductibles supprimés le 11 novembre 2026. |

Le même inventaire a repéré des `.pyc` dans les paquets de mise à jour. Ils sont sans effet sur l'application actuelle, mais font partie des bundles conservés ci-dessous : `V0_13/payload/__pycache__/app.cpython-313.pyc` (177 582), `V0_14/payload/__pycache__/app.cpython-313.pyc` (181 108), `V0_14/payload/__pycache__/forced_align.cpython-313.pyc` (15 791), `V0_15/payload/__pycache__/app.cpython-313.pyc` (184 634).

### Anciens bundles d'installation autonomes

Ils ne sont référencés par aucun lanceur courant. Leurs scripts `APPLIQUER_MAJ_*.bat` copient leur ancien `app.py` vers le dossier principal et pourraient donc remplacer le code actuel s'ils étaient exécutés. Chaque fichier est listé pour éviter de masquer les données présentes dans V0_13.

| Chemin | Octets | Motif / réserve |
|---|---:|---|
| `Clip_Factory_MAJ_V0_11/APPLIQUER_MAJ_V0_11.bat` | 1 770 | Ancien installateur non utilisé au lancement. |
| `Clip_Factory_MAJ_V0_11/payload/app.py` | 113 256 | Ancien code; copie identique à `app.py.backup_20261109_151144`. |
| `Clip_Factory_MAJ_V0_11/payload/forced_align.py` | 10 673 | Copie identique aux autres `forced_align.py` 10 673 B. |
| `Clip_Factory_MAJ_V0_12/APPLIQUER_MAJ_V0_12.bat` | 1 631 | Ancien installateur non utilisé au lancement. |
| `Clip_Factory_MAJ_V0_12/payload/app.py` | 125 983 | Ancien code; copie identique à `app.py.backup_20261109_152723`. |
| `Clip_Factory_MAJ_V0_12/payload/forced_align.py` | 10 673 | Copie identique aux autres `forced_align.py` 10 673 B. |
| `Clip_Factory_MAJ_V0_13/APPLIQUER_MAJ_V0_13.bat` | 1 866 | Ancien installateur non utilisé au lancement. |
| `Clip_Factory_MAJ_V0_13/LISEZ_MOI.txt` | 899 | Instructions de cet installateur. |
| `Clip_Factory_MAJ_V0_13/VERSION.txt` | 19 | Métadonnée de version. |
| `Clip_Factory_MAJ_V0_13/payload/app.py` | 136 542 | Ancien code; copie identique à `app.py.backup_20261109_155138`. |
| `Clip_Factory_MAJ_V0_13/payload/data.json` | 641 | **À conserver : données/campagnes historiques intégrées au paquet.** |
| `Clip_Factory_MAJ_V0_13/payload/forced_align.py` | 10 673 | Copie identique aux autres `forced_align.py` 10 673 B. |
| `Clip_Factory_MAJ_V0_13/payload/sessions/index.json` | 373 | **À conserver : index de sessions historiques.** |
| `Clip_Factory_MAJ_V0_13/payload/sessions/session_6416d3e2.json` | 358 | **À conserver : session historique.** |
| `Clip_Factory_MAJ_V0_13/payload/sessions/session_c12587d4.json` | 368 | **À conserver : session historique.** |
| `Clip_Factory_MAJ_V0_13/payload/__pycache__/app.cpython-313.pyc` | 177 582 | Cache lié à l'ancien Python et paquet. |
| `Clip_Factory_MAJ_V0_14/APPLIQUER_MAJ_V0_14.bat` | 1 696 | Ancien installateur non utilisé au lancement. |
| `Clip_Factory_MAJ_V0_14/payload/app.py` | 139 247 | Ancien code; copie identique à `app.py.backup_20261109_155849`. |
| `Clip_Factory_MAJ_V0_14/payload/forced_align.py` | 10 673 | Copie identique aux autres `forced_align.py` 10 673 B. |
| `Clip_Factory_MAJ_V0_14/payload/__pycache__/app.cpython-313.pyc` | 181 108 | Cache lié à l'ancien Python et paquet. |
| `Clip_Factory_MAJ_V0_14/payload/__pycache__/forced_align.cpython-313.pyc` | 15 791 | Cache lié à l'ancien Python et paquet. |
| `Clip_Factory_MAJ_V0_15/APPLIQUER_MAJ_V0_15.bat` | 1 625 | Ancien installateur non utilisé au lancement. |
| `Clip_Factory_MAJ_V0_15/LISEZ_MOI.txt` | 1 161 | Instructions de cet installateur. |
| `Clip_Factory_MAJ_V0_15/VERSION.txt` | 5 | Métadonnée de version. |
| `Clip_Factory_MAJ_V0_15/payload/app.py` | 142 065 | Copie exacte de `backups/20261109_pre_refonte/app.py`, point de restauration le plus récent. |
| `Clip_Factory_MAJ_V0_15/payload/forced_align.py` | 10 673 | Copie identique des autres moteurs d'alignement 10 673 B. |
| `Clip_Factory_MAJ_V0_15/payload/__pycache__/app.cpython-313.pyc` | 184 634 | Cache lié à l'ancien Python et paquet. |
| **Total des cinq bundles** | **1 281 985** | Sans compter les environnements ni les données externes. V0_13 entier ne doit pas être supprimé. |

### Instantanés de code à la racine

Les copies app.py de trois heures différentes sont identiques; d'autres sont identiques aux payloads de version listés ci-dessus. Elles ne sont pas importées par l'application. En l'absence de commit Git, elles constituent toutefois des historiques de récupération.

| Chemin | Octets | Note |
|---|---:|---|
| `app.py.backup_20261109_135822` | 60 282 | Plus ancien état app conservé; unique dans les snapshots vérifiés. |
| `app.py.backup_20261109_143528` | 101 283 | Identique octet par octet aux backups 143538 et 145115. |
| `app.py.backup_20261109_143538` | 101 283 | Doublon exact de 143528 et 145115. |
| `app.py.backup_20261109_145115` | 101 283 | Doublon exact de 143528 et 143538. |
| `app.py.backup_20261109_151144` | 113 256 | Doublon du payload app.py V0_11. |
| `app.py.backup_20261109_152723` | 125 983 | Doublon du payload app.py V0_12. |
| `app.py.backup_20261109_155138` | 136 542 | Doublon du payload app.py V0_13. |
| `app.py.backup_20261109_155849` | 139 247 | Doublon du payload app.py V0_14. |
| `forced_align.py.backup_20261109_135822` | 10 615 | Seul état forcé-alignement plus ancien et différent des copies 10 673 B. |
| `forced_align.py.backup_20261109_143528` | 10 673 | Doublon exact de toutes les copies forced_align.py à 10 673 B. |
| `forced_align.py.backup_20261109_143538` | 10 673 | Doublon exact. |
| `forced_align.py.backup_20261109_145115` | 10 673 | Doublon exact. |
| `forced_align.py.backup_20261109_151144` | 10 673 | Doublon exact. |
| `forced_align.py.backup_20261109_152723` | 10 673 | Doublon exact. |
| `forced_align.py.backup_20261109_155138` | 10 673 | Doublon exact. |
| `forced_align.py.backup_20261109_155849` | 10 673 | Doublon exact. |
| **Total snapshots de code racine** | **964 485** | Pas supprimés; conserver au moins un jeu de restauration hors Git. |

`backups/20261109_pre_refonte/app.py` (142 065 B) et `forced_align.py` (10 673 B), total 152 738 B, sont référencés par `README.md` comme point de restauration avant refonte et doivent être conservés. `app.py` y est identique à V0_15. `forced_align.py` y est identique aux huit copies racine et aux cinq bundles.

### WAV de travail non référencés

| Chemin | Octets | Motif / réserve |
|---|---:|---|
| `media/temp_audio/align_6d8e2a3a34.wav` | 800 228 | WAV extrait, aucun chemin sauvegardé ne le référence; fichier régénérable à partir de la source, garder avec les médias tant qu'aucun nettoyage n'est approuvé. |
| `media/temp_audio/align_saved_eaa6c21bf4.wav` | 1 184 228 | Idem; aucun `alignment_audio` correspondant trouvé dans `studio.json`, sessions ou transcriptions enregistrées. |

## Éléments référencés ou à préserver absolument

| Chemin | Inventaire/volume | Références / décision |
|---|---|---|
| `app.py`, `content_engine.py`, `forced_align.py`, `annotation_store.py` | Modules actifs | Chargés par les lanceurs, routes, tâches ou tests; conserver. |
| `tools/`, `tests/` | Scripts/tests actifs, y compris audit QMSum | Le runner et les tests importent ces modules; conserver. |
| `requirements.txt`, `requirements-align.txt`, `INSTALLER_ET_DEMARRER.bat`, `start_clip_factory.bat` | Dépendances et lanceurs | Ressources du démarrage/installation; conserver. |
| `.venv/` | 4 701 fichiers, 399 263 612 B | Environnement d'exécution actif; conserver. |
| `.alignenv/` | 27 223 fichiers, 791 838 782 B | Environnement séparé d'alignement/outils; consigne explicite de préservation. |
| `.git/` | 157 fichiers, 955 174 211 B; 139 objets lâches (~910,90 MiB), aucun commit ni fichier suivi | Métadonnées Git à préserver. Aucun objet n'a été supprimé/pruné; les données des blobs ne sont pas attribuables sans inspection dédiée. |
| `data.json`, `studio.json`, `transcript.json` | 4 416 B; 66 635 B; 60 729 B | Campagnes, studio et transcript sauvegardés; conserver. |
| `sessions/` | 9 fichiers, 191 749 B | Index, sessions et snapshots de transcriptions référencés par l'application; conserver. |
| `media/sources/` | 7 vidéos, 2 882 017 029 B | Sources référencées par sessions/studio. Six fichiers de 387 596 026 B ont le même SHA-256 `ad73e37e…9a4af216`; le septième fait 556 440 873 B. **Copies identiques toujours conservées** car ce sont des médias utilisateur. |
| `media/exports/` | Un MP4, 7 050 575 B | Export utilisateur; conserver. |
| `validation/transcripts/` | 2 JSON, 541 767 B | Entrées des comparaisons/tests; conserver. |
| `validation/baseline_content_engine.py`, `baseline_manifest.json`, `baseline_results.json`, `comparison_results.json`, `latest_comparison.json` | Référence, hashes, sorties d'audit | Preuves/reproductibilité des comparaisons locales; conserver. |
| `validation/annotations.json`, `reference_spans.json`, `source_catalog.json` | Fichiers d'annotation vides | Outil de validation facultatif, ne bloque pas la production; conserver. |
| `validation/qmsum_topic_benchmark.json`, `validation/history/qmsum_topic_benchmark_20261109_exploratory_invalid.json` | Audit courant et ancien résultat invalidé | Le premier retire les métriques, l'archive garde les anciens chiffres et hashes; conserver tous deux. |
| `validation/frames/` | 5 JPEG, 190 914 B | Images déjà utilisées pour l'inspection qualitative; preuve d'audit; conserver. |

Les mises à jour et snapshots sont donc des candidats d'archivage/suppression **uniquement après** une sauvegarde vérifiée et la décision que les anciens installeurs/versions ne doivent plus être récupérables. Le dossier V0_13 requiert une extraction/conservation distincte de `payload/data.json` et des 3 fichiers `payload/sessions/*` avant toute suppression partielle. Aucun de ces arbitrages n'a été exécuté.

## État de l'opération

Le dépôt démarre sans commit et tous ses fichiers sont non suivis. Une suppression antérieure avait été bloquée par le contrôle automatique de l'outil. Pour la demande du 11 novembre, la suppression normale, chemin par chemin, des **14 `.pyc`** listés dans les caches racine/tests/outils/validation a abouti : **394 932 octets** libérés. Les répertoires de cache peuvent rester vides et Python les régénérera à la prochaine exécution. Les `.pyc` inclus dans les anciens bundles V0_13 à V0_15 ont été conservés avec les bundles. Aucun dossier ni aucun autre type de fichier n'a été supprimé.

Artefacts temporaires de cette inspection, hors dépôt et sans référence Clip Factory : `[dossier temporaire local]` (20 JPEG, 239 076 B au total) et `[fichier WAV temporaire local]` (8 639 524 B). Ce sont des sorties locales reproductibles de l'audit. Elles ne font pas partie des candidats de suppression du dépôt; aucune suppression n'a été tentée.
