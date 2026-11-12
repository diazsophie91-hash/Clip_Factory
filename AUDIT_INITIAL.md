# Audit technique initial — Clip Factory

Date de l'audit : 9 novembre 2026. Périmètre : fichiers du dépôt, données locales, code de production et deux environnements Python présents. Les vidéos et exports ont été inventoriés sans être modifiés.

## État observé

- L'application est un serveur HTTP de bibliothèque standard (`http.server`) lié à `127.0.0.1:8765`. L'interface HTML, le CSS, le JavaScript, le stockage, les traitements vidéo et les routes sont réunis dans `app.py` (environ 2 160 lignes). `forced_align.py` est le seul module de traitement séparé.
- Les fichiers utilisateur sont conservés en JSON (`data.json`, `studio.json`, `sessions/`). Les écritures sont généralement atomiques pour les sessions et l'atelier, mais les fonctions d'écriture ne verrouillent pas les accès concurrents. `load_data()` modifie aussi les données à la lecture en ajoutant une campagne Joyca prédéfinie si elle n'existe pas.
- Aucun `README`, manifeste de dépendances, suite de tests ou consigne `AGENTS.md` n'a été trouvé. Le dépôt Git n'a pas de commit initial ; tous les fichiers du projet apparaissent non suivis. Des sauvegardes horodatées d'`app.py` et `forced_align.py`, cinq dossiers de mises à jour antérieures, des sessions, six sources vidéo et des exports MP4 sont présents.
- Les six fichiers source comprennent cinq copies identiques (même SHA-256) et une autre vidéo. Ils ne seront pas supprimés : la déduplication à l'import devra être une action explicite ou préserver les références de sessions.
- `start_clip_factory.bat` utilise le venv local, puis exécute l'application. Il n'y a pas de script d'installation livré à la racine malgré le message affiché par ce lanceur.

## Moteurs et environnements

- `.venv` : Python 3.14.7, `faster-whisper` 1.2.1, `ctranslate2` 4.8.5, `av` 18.1.0, NumPy 2.5.4, `imageio-ffmpeg` 0.8.1. L'import Faster-Whisper réussit. FFmpeg embarqué est présent (9.0.2). OpenCV n'est pas installé dans cet environnement.
- `.alignenv` : Python 3.14.7, PyTorch CPU 2.11.0, Transformers 4.57.6. Les imports de `torch`, `transformers` et `Wav2Vec2Processor` réussissent. Ce contrôle ne charge pas le modèle acoustique et ne valide donc pas un alignement réel de bout en bout.
- Whisper utilise Faster-Whisper sur CPU en INT8 et propose `base`, `small` ou `medium`. La recherche complète utilise VAD, tandis que la transcription courte ne l'active pas. Le réaligneur est isolé, utilise un modèle CTC français et évite `pyctcdecode`.

## Défauts et risques prioritaires

### Sélection des extraits

- `rank_candidate_excerpts()` fabrique des fenêtres autour de frontières de phrases ou d'une grille temporelle, puis les note avec densité de parole, confiance Whisper, ponctuation, variété lexicale, pauses et lexiques d'accroche/expressivité. Le code reconnaît lui-même que le score est un outil de classement, pas une probabilité. Pourtant l'interface affiche un seul score sur 100 et des étiquettes valorisantes ; aucun sous-score narratif, autonomie, audiovisuel, conformité ou défaut n'est présenté.
- Le nombre de résultats est figé à huit et les durées 20–45 s sont codées dans le moteur, les textes d'interface et la validation d'export. La campagne choisie n'alimente pas ces contraintes.
- Il n'y a pas d'analyse sémantique locale distincte, d'analyse audiovisuelle systématique, de filtre/tri, ni d'indicateur explicite de transcription suspecte au niveau candidat. Les crédits Amara et certaines répétitions sont filtrés par motifs, mais cela ne couvre pas toute hallucination.
- `build_story_parts()` choisit les frontières en séquence et utilise des heuristiques lexicales de suspense. Il exige un nombre demandé de phrases/parties, peut lever une erreur si le compte est irréaliste et ne vérifie pas les invariants de couverture (premier début, absence de trous/chevauchements, dernier point, continuité) après construction. La vue timeline et l'écoute des transitions ne sont pas implémentées.

### Fiabilité et persistance

- Les contrôles « traitement déjà en cours » reposent sur une liste répétée de libellés de statut et des lectures/écritures JSON sans verrou. Deux requêtes rapprochées, ou deux sessions travaillant sur le même fichier source, peuvent contourner ce contrôle. Les workers sont des threads daemon non annulables ; leur état est seulement représenté par un texte.
- Plusieurs commandes FFmpeg sont exécutées sans délai maximal. `get_duration()` et `get_video_size()` interprètent la sortie stderr sans vérifier le code retour. Les appels de rendu vérifient toutefois le code retour dans le flux d'export principal.
- L'import accepte une taille jusqu'à 3 Gio et écrit une nouvelle copie à chaque envoi. Il ne signale pas les doublons, n'affiche pas une progression chiffrée, et l'extraction de métadonnées survient après l'écriture complète.
- Le dashboard ne fournit que des champs manuels limités pour les campagnes et clips. Il ne distingue pas systématiquement les métriques mesurées des saisies/estimations. Les comptes sont codés en dur dans le formulaire.
- Les exports sont téléchargés en mémoire entière par `/files/` alors que la prévisualisation de la source prend en charge le streaming par plages.

### Interface et architecture

- Navigation réduite au tableau de bord et à l'atelier ; campagnes, bibliothèque, comptes, statistiques et paramètres n'ont pas d'espaces dédiés. Le thème clair/sombre existe, mais le thème n'est pas un réglage persistant serveur.
- Des cas Joyca et des instructions de campagne apparaissent dans les valeurs par défaut, le formulaire, les messages d'aide et l'import (nom de fichier) ; cela lie l'atelier à une campagne particulière.
- Les erreurs de traitement sont conservées dans des chaînes d'état, mais les libellés de traitement occupent plusieurs listes synchronisées qui peuvent diverger.
- Les données de campagne contiennent deux entrées Joyca proches, dont une ajoutée automatiquement par le chargement. Cette observation est documentée ; les données existantes ne sont pas corrigées automatiquement pendant l'audit.

## Architecture cible proposée

1. Conserver une application locale et les données JSON actuelles pour ce lot ; ne pas lancer de migration SQLite sans procédure de migration, vérification et export testées.
2. Extraire progressivement le domaine (sessions, bibliothèques média, transcription, sélection narrative, épisodes, campagne/statistiques) du serveur et de l'interface. Définir des contrats JSON stables et un état de tâche explicite (identifiant, session, type, état, progression, erreur, annulation) avant d'ajouter un ordonnanceur.
3. Pour les extraits, produire des candidats diversifiés depuis les mots horodatés, exposer des dimensions explicables et des défauts observés, et présenter les notes comme des indices de tri. Ajouter les contraintes de durée/nombre dans le modèle campagne. Ne pas prétendre à une compréhension sémantique/audio-visuelle absente.
4. Pour les épisodes, faire respecter et tester les invariants de couverture chronologique indépendamment de la qualité du score de suspense ; afficher des limites modifiables avant export.
5. Maintenir Faster-Whisper et le réaligneur actuels tant que les modèles ne sont pas comparés sur des clips locaux annotés. Ne pas installer de nouveau modèle ni envoyer de média à un service externe pendant cette refonte.

## Ordre de travail

1. Ajouter des tests de non-régression pour sélection, épisodes, contraintes de campagne, sessions et routes locales.
2. Corriger l'honnêteté des scores et exposer les dimensions de classement, puis rendre nombre/durée de résultats configurables.
3. Ajouter le contrôle de conformité campagne avant export et retirer les valeurs par défaut Joyca codées dans les flux génériques.
4. Renforcer les verrous/tâches et commandes de sous-processus sans casser l'état historique JSON.
5. Ajouter navigation et vues dédiées par étapes, puis documenter installation, sauvegarde/restauration et limites mesurées.

## Limites de cet audit

Les modèles Whisper et CTC n'ont pas été téléchargés/chargés pour un benchmark, aucun clip utilisateur n'a été analysé ou réexporté, et aucune publication n'a été effectuée. Il n'existe pas de corpus annoté local pour mesurer la précision narrative ou acoustique. Le rapport distingue donc les faits vérifiés par inspection/imports des performances qui restent à mesurer.
