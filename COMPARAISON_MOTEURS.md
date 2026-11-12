# Comparaison des moteurs locaux

État vérifié le 9 novembre 2026. Cette comparaison choisit les moteurs à garder pour le lot actuel ; elle ne remplace pas un benchmark sur les voix, accents, noms propres et musiques présents dans les vidéos de l'utilisateur.

## Transcription

| Option | Rôle | Atouts documentés | Limites pour Clip Factory | Décision |
|---|---|---|---|---|
| Faster-Whisper 1.2.1 + CTranslate2, `small`/`medium` | Transcription ASR actuelle | Le projet Faster-Whisper réimplémente Whisper sur CTranslate2 et rapporte jusqu'à 4× de vitesse dans ses propres benchmarks ; la quantification INT8 est disponible. L'environnement local confirme que Faster-Whisper et CTranslate2 s'importent. | Le résultat d'un benchmark amont n'est pas une mesure sur cette machine ou sur les vidéos présentes. | Conserver le moteur et les options actuelles ; mesurer avant tout remplacement. [Dépôt Faster-Whisper](https://github.com/SYSTRAN/faster-whisper) |
| Whisper large-v3 | Modèle Whisper plus grand | La fiche OpenAI indique une baisse d'erreurs annoncée de 10 à 20 % par rapport à large-v2 sur un ensemble de langues. | Ce chiffre n'est pas une comparaison avec `small`/`medium` sur du français conversationnel. Le modèle est plus lourd ; le temps CPU et la mémoire sur ce PC ne sont pas mesurés. | Candidat à une comparaison offline contrôlée, pas un défaut activé. [Fiche OpenAI](https://huggingface.co/openai/whisper-large-v3) |
| Qwen3-VL 2B/8B (modèle visuel et langage local) | Analyse sémantique texte + images/vidéo | Le dépôt officiel documente l'entrée vidéo et le contrôle du budget de pixels/frames. Cela peut aider à contextualiser les scènes en complément de la transcription. [Dépôt Qwen3-VL](https://github.com/QwenLM/Qwen3-VL) | Nouveaux poids, Transformers et utilitaires vidéo ; coût mémoire/CPU inconnu ici. Une vidéo longue doit être échantillonnée et le budget d'images contrôlé. Ce modèle ne fournit pas à lui seul un alignement de mots ni une preuve de performance TikTok. | Ne pas installer avant d'avoir vérifié RAM/GPU et mesuré qualité/vitesse sur quelques segments locaux représentatifs. |

Whisper small est le compromis actuel orienté vitesse ; medium est déjà proposé pour un passage à vérifier. La documentation et les résultats publiés orientent les options, mais ne permettent pas de certifier les noms propres, l'argot, les interlocuteurs qui se chevauchent ou la musique de ces sources.

## Réalignement acoustique

L'application utilise `bofenghuang/asr-wav2vec2-ctc-french`, un modèle CTC français finetuné sur plus de 2 200 heures issues de plusieurs corpus, dont VoxPopuli, Common Voice, TEDx, MediaSpeech et African Accented French. Sa fiche publie des WER auto-déclarés, notamment 11,44 sur Common Voice 11.0, 9,33 sur VoxPopuli et 16,22 sur African Accented French ; ces valeurs décrivent les corpus du modèle et ne prédisent pas l'alignement de clips de créateurs. La fiche publie également une variante avec modèle de langue, mais celui-ci est volumineux. [Fiche du modèle CTC](https://huggingface.co/bofenghuang/asr-wav2vec2-ctc-french)

Le réaligneur local utilise les sorties CTC pour placer un texte déjà reconnu. Ce moteur n'est pas la transcription principale ; une erreur d'orthographe doit rester corrigée dans Whisper avant alignement. Le code actuel charge un processeur et un modèle `AutoModelForCTC` ; les imports ont fonctionné, mais le poids d'environ 1,26 Go, la latence et la qualité de placement n'ont pas été mesurés pendant ce lot.

## Protocole avant tout changement de modèle

1. Constituer un petit jeu de clips locaux autorisés avec transcription de référence corrigée : conversation rapide, noms propres, accent, chevauchement, musique, silence et crédit parasite.
2. Comparer les mêmes fichiers dans les mêmes réglages (modèle, beam, VAD, prompt), en enregistrant WER/CER, erreurs sur noms propres, taux de mots hallucinés, précision des timestamps, temps de calcul et mémoire maximale.
3. Mesurer la qualité de l'alignement séparément de la transcription : erreur absolue médiane et proportion de mots au-delà de 200 ms.
4. Mesurer tout modèle visuel sur un échantillon de frames horodatées et des jugements humains ; ne pas lui envoyer les vidéos à un service distant.
5. Garder le moteur actuel si aucun remplacement ne démontre un gain utile sans coût ou régression de compatibilité.
