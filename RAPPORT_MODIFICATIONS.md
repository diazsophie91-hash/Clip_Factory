# Rapport de refonte — 9 novembre 2026

## Modifications réalisées

- Extrait le moteur de classement des extraits et de découpage narratif de `app.py` vers `content_engine.py`. Les fonctions de transcription et de segmentation restent séparées du serveur HTTP au niveau du code.
- Remplacé la note unique des candidats par trois dimensions explicables : signaux narratifs tirés du texte, qualité de transcription et indicateur heuristique d'autonomie. Le score audiovisuel reste explicitement « non analysé ». L'interface affiche les défauts détectables, la confiance moyenne et la couverture de parole, avec tri par score, transcription ou autonomie et conservation de la prévisualisation audio/vidéo.
- Les confidences manquantes ne valent plus 100 % : elles reçoivent une valeur prudente. Les crédits de sous-titrage connus, dont Amara, restent exclus.
- Ajouté les durées minimale et maximale aux nouvelles campagnes, le choix d'une campagne et une limite de résultats configurable jusqu'à 30. Les mêmes durées sont contrôlées avant l'export lorsqu'une campagne est choisie.
- Empêché les doubles workers dans une même session par un registre protégé par verrou. L'état `busy` est maintenant lié à un worker actif et ne dépend plus uniquement d'un libellé persistant.
- Ajouté des délais aux appels FFmpeg de lecture des métadonnées, d'extraction audio et de rendu.
- Ajouté une page Bibliothèque en lecture seule qui liste les sources, exports MP4 et sessions existants, ainsi qu'une navigation vers les principales sections du tableau de bord.
- Retiré le semis automatique de la campagne Joyca depuis `load_data()` et les présélections liées à un nom de fichier. Les campagnes Joyca déjà enregistrées et les médias restent présents.
- Ajouté l'installateur Windows `INSTALLER_ET_DEMARRER.bat`, les manifestes de référence des deux environnements, une procédure de restauration, et une suite de tests.
- Comparé les options Whisper, le modèle CTC français actuel et Qwen3-VL pour une future analyse sémantique locale. Les moteurs restent inchangés faute de benchmark sur ce PC et sur des clips étiquetés ; le détail et les sources primaires figurent dans `COMPARAISON_MOTEURS.md`.
- Consulté les références publiques TikTok Studio et consigné les principes ergonomiques, leur reprise partielle et les écarts à traiter dans `REFERENCE_UX.md`.
- Copié les deux modules actifs avant modification dans `backups/20261109_pre_refonte/`.

## Validation effectuée

- `py_compile` réussi pour l'application, le moteur, le réaligneur et les tests.
- 12 tests réussis : contraintes de durée et limite des résultats, dimensions de score, confiance absente, rejet des crédits Amara, couverture des épisodes, exclusivité des workers, création/fermeture/réouverture de session, absence d'insertion de campagne lors d'une lecture, rendu de l'accueil et du studio, page Bibliothèque, exports JSON/CSV et prévisualisation par plages HTTP.
- Serveur de test lié à `127.0.0.1` sur un port temporaire ; aucune donnée distante ni publication n'a été utilisée.
- FFmpeg a décodé entièrement un export local existant sans erreur. Une source locale a été lue et ses métadonnées ont été détectées (497,26 s, 3 840 × 2 160).
- Les transcriptions sauvegardées du projet ont été passées au moteur sans modifier les fichiers : les deux transcriptions composées surtout de crédits parasites ont produit zéro proposition ; deux autres en ont produit deux chacune. Ce contrôle confirme le rejet de certaines hallucinations, pas la pertinence narrative des autres suggestions.
- Les deux environnements ont été inspectés ; Faster-Whisper et le processeur Wav2Vec2 s'importent. Aucun paquet n'a été installé ou mis à niveau.

## Fonctionnalités terminées dans ce lot

- Moteur de classement plus transparent et paramétrable par campagne.
- Contrôle de durée de campagne avant export.
- Protection contre deux tâches dans une même session.
- Bibliothèque locale consultable et navigation de base.
- Tests unitaires et fumée HTTP reproductibles.
- Documentation d'installation, d'architecture, de données et de restauration.

## Limites restantes

- Cette livraison est une amélioration ciblée, pas la refonte complète décrite dans le brief. Le serveur et la majorité des flux restent dans `app.py`; SQLite, les migrations, l'annulation, les vraies barres de progression et l'import sans copie inutile ne sont pas implémentés.
- Le moteur reste heuristique et transcriptuel. Il n'utilise ni modèle sémantique local spécialisé, ni vision/audio pour noter le potentiel d'un clip. Les notes ne prédisent pas les vues ou revenus.
- Les durées de campagne sont enregistrées et appliquées, mais les mentions, hashtags, formats, budgets et conditions ne disposent pas encore d'un contrôle de conformité complet.
- Les épisodes n'ont pas encore de timeline éditable ni d'écoute de transitions dans l'interface. Le test confirme la continuité temporelle pour un cas synthétique ; les coupes narratives demandent encore une vérification humaine.
- Les tests couvrent des invariants et les routes locales. Ils ne remplacent pas les tests de précision Whisper, d'alignement sur parole française annotée, de rendu de sous-titres sur plusieurs vidéos, ni une validation visuelle de tous les formats.
- Le modèle CTC n'a pas été chargé ni utilisé pour un nouvel export. Aucun nouveau clip n'a été produit pendant cette passe.
- Comptes sociaux, analytics officiels, performances mesurées par vidéo, finances détaillées, paramètres serveur et mise à jour complète de l'application restent à construire.
- Les vidéos sources du dossier contiennent cinq copies identiques. Elles ont été préservées ; aucune déduplication n'a été appliquée.
