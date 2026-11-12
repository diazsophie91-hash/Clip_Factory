# Audit et évolution du mode épisodes narratifs

État au 11 novembre 2026. L'objectif de cette passe était le défaut observé dans « Les pubs VS La vie » : la première partie s'arrêtait à 218,69 s et traversait plusieurs séquences indépendantes.

## Diagnostic de l'ancien découpage

L'ancien `build_story_parts` transformait la ponctuation Whisper en unités puis notait les fins avec `_cliffhanger_score`. Une question rapportait à elle seule 3,8 points; une amorce de suspense ou une transition en rapportait d'autres. Les limites de durée servaient de plage souple, mais le choix final pouvait favoriser un score de question au détriment d'une transition de scène. À 218,69 s, il coupait après « D'ailleurs, en assurance, c'est combien… ? », avant la réponse, alors que la vidéo avait déjà traversé les séquences de fromage, l'entretien autour de la publicité, la chanson et le début d'une autre saynète.

Le texte des transcriptions montre des transitions formatées (« Dans les pubs », « Dans la vie »), des dialogues question/réponse et une longue sortie Whisper répétitive pour des rires. La première version traitait toutes les phrases comme un seul flot narratif; elle ne faisait pas d'étape de détection des séquences.

## Architecture expérimentale ajoutée

Le mode épisodes utilise désormais deux étapes dans `content_engine.py` :

1. `detect_narrative_sequences` construit des séquences probables à partir des segments Whisper, du contexte lexical TF-IDF léger, des grandes pauses de parole et des amorces répétées. Les marqueurs alternés d'un format comparatif sont regroupés par paire. Les questions et leurs réponses brèves restent dans la même séquence. Les segments Whisper manifestement répétitifs sont exclus des indices lexicaux, mais leur durée reste dans la timeline.
2. `build_story_parts` regroupe les séquences dans l'ordre. La durée moyenne demandée est une cible souple; la programmation dynamique choisit parmi les frontières trouvées à l'étape 1. Elle ne crée donc pas de coupe à l'intérieur d'une séquence détectée. Si la transcription est trop pauvre pour fournir environ deux séquences par épisode demandé, le moteur reprend l'ancien découpage, étiqueté de faible confiance, afin de préserver le comportement de « Voici 3 mondes ».

Le détecteur accepte des timestamps visuels comme signaux de corroboration : une coupe d'image seule ne provoque jamais une frontière. **L'application ne calcule pas encore ces signaux visuels**; la séparation de production actuelle se fonde sur la transcription et ses timestamps. Les pistes audio ne sont pas analysées pour la musique, les rires ou les changements de voix, et aucun diariseur n'est installé. Les scores du mode clips indépendants n'ont pas été modifiés.

## Comparaison sur transcriptions locales

Les entrées, leurs SHA-256 et l'ancien découpage sont conservés dans `validation/episode_segmentation_baseline_20261111.json`. Le résultat nouveau et les temps CPU sont dans `validation/episode_segmentation_after_20261111.json`; `python -m tools.benchmark_episode_segmentation` reproduit la comparaison. Les durées sont mesurées sur des transcriptions déjà sauvegardées, pas sur une transcription Whisper complète.

### « Les pubs VS La vie » — 497,255 s

| Version | Coupures et durées des parties |
|---|---|
| Avant | 0–218,69 (218,7 s); 218,69–337,05 (118,4 s); 337,05–438,37 (101,3 s); 438,37–497,255 (58,9 s) |
| Après | 0–105,43 (105,4 s); 105,43–230,23 (124,8 s); 230,23–388,30 (158,1 s); 388,30–497,255 (109,0 s) |

L'étape A trouve 12 séquences. Leurs regroupements sont :

- 0–105,43 : ouverture automobile puis trois paires de saynètes publicitaires / vie quotidienne;
- 105,43–230,23 : discussion publicitaire, chanson, séquence automobile puis parodie d'assurance;
- 230,23–388,30 : séquence publicitaire « Sauvage »/vie quotidienne puis éléments de tournage et de commentaire;
- 388,30–497,255 : séquence Temu, gag autour du fromage et retour automobile.

La première coupure est avancée de **113,26 s** et termine à une frontière détectée. Cela corrige le défaut temporel précis. Les deux dernières parties regroupent encore plusieurs sous-séquences; le résultat n'établit donc pas que chaque épisode est sémantiquement homogène. Le nombre quatre demandé est gardé, ce qui oblige parfois à regrouper plusieurs séquences.

### « Voici 3 mondes » — 269,978 s

Sa transcription n'a que six segments utiles. Le moteur refuse de déduire une structure assez solide et reprend les frontières précédentes : 0–195,71; 195,71–224,11; 224,11–269,978 s. Le résultat n'est pas dégradé, mais **n'est pas amélioré**. Les trois sorties portent la marque `low_transcript_evidence` et ne doivent pas être interprétées comme une validation narrative.

## Parcours Atelier et tests

Un test HTTP crée une session Atelier isolée dans un répertoire temporaire, y charge la transcription locale sauvegardée, appelle réellement `/split-parts` et vérifie les épisodes enregistrés et leur affichage dans l'Atelier. Il couvre le parcours session → analyse des épisodes sans relancer Whisper; la transcription complète à partir d'un nouveau média n'a pas été rejouée.

Les tests couvrent aussi : anecdote jusqu'à sa conclusion, question/réponse brève, deux vignettes distinctes, paire d'amorces alternées, changement visuel seul et changement visuel corroborant une rupture lexicale. La suite complète valide les routes, sessions, prévisualisations et exports.

Résultat final : **42 tests réussis** (`unittest` en 3,355 s), dont sept nouveaux tests de séquences et de parcours Atelier. La suite HTTP valide l'accueil, le studio, la bibliothèque, les exports et la prévisualisation; le nouveau parcours d'épisodes utilise une session temporaire et n'écrit pas dans les sessions utilisateur.

## Mesures et limites

- Sur les transcriptions locales, la détection puis la construction des épisodes prennent environ 0,03 s chacune pour « Les pubs VS La vie » et environ 0,001 s chacune pour « Voici 3 mondes »; aucun nouveau modèle ni paquet n'est chargé.
- Les changements d'image et les mots ne comprennent pas les synonymes, l'identité des intervenants ou les réactions audio. Le TF-IDF lexical peut donc manquer un changement de sujet sans pause et fusionner des vignettes lexicalement proches.
- Les limites de séquence sont des hypothèses du moteur, pas des annotations humaines. Le jeu de validation ne contient pas de notes humaines; aucune hausse de qualité narrative n'est démontrée.
- Le détecteur visuel optionnel n'est pas relié au parcours Atelier, et l'évaluation n'inclut pas les sept sources locales ni une série de genres. Il reste à tester avec davantage de vidéos indépendantes.

Ce résultat est une expérimentation de segmentation plus explicable et reproductible, pas une preuve que les épisodes sont prêts pour une production automatique sans vérification.
