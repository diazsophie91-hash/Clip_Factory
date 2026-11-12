# Validation qualitative du moteur de sélection

Date : 9 novembre 2026. Ce rapport distingue les comptages reproductibles des jugements humains. Aucun label de qualité n'a été inventé pour remplir le jeu de validation.

## Audit du moteur de référence

- **Transcription et segmentation** : les mots sont aplatis depuis les segments Whisper. En l'absence de mots horodatés, l'ancien moteur répartit les temps proportionnellement sur le texte, ce qui peut fabriquer des bornes imprécises. Les ancres proviennent de ponctuation, de majuscules, de silences et d'une grille de secours de 3,5 s. Les fins testées étaient principalement 24/29/34/39/44 s. Les parties narratives utilisent la ponctuation comme fin de phrase; les segments oraux sans ponctuation sont donc fragiles.
- **Filtres de référence** : liste de phrases de crédits/sous-titres; seuils Whisper (no-speech >0,82, logprob <−1,45, compression >2,9); 10 mots minimum; rejet d'une répétition de quatre mots identiques, d'une diversité lexicale <0,30, d'une confiance moyenne <0,30 ou de plus de 75 % de mots sous 0,30. Des métadonnées Whisper absentes sont traitées comme neutres, pas comme un défaut.
- **Note de référence** : 45 % de signaux lexicaux/ponctuation, 35 % de confiance et qualité de transcription, 20 % d'autonomie (début, ponctuation finale et quelques débuts dépendants). La note narrative récompense surtout questions, exclamations, contrastes, mots expressifs et mots d'accroche. Elle ne comprend pas le sens de l'échange. La confiance de transcription n'est pas une vérité terrain. Audio et visuel ne sont pas mesurés.
- **Diversité de référence** : chevauchement temporel, assoupli dans un second passage de 42 % à 62 %, plus une distance minimale de 9 s entre milieux. Aucun rapprochement lexical n'était effectué; deux fenêtres de temps distinctes pouvaient donc proposer le même texte.
- **Pourquoi le nombre de résultats varie** : le quota utilisateur est un maximum, pas une cible. Une plage plus courte que la durée minimale de campagne est refusée par la route; des segments connus comme hallucinations peuvent être supprimés; les filtres de qualité et de mots peuvent éliminer tous les candidats; la déduplication temporelle peut réduire plusieurs fenêtres à une ou deux idées. Sur la transcription Amara, 23 segments de crédits connus ont été rejetés avant la création de candidats (0 conservé). Sur « Voici 3 mondes », 40 fenêtres étaient classées comme valides malgré seulement 43 mots et le moteur n'en gardait que 2 après la seule diversité temporelle. Sur « Les pubs VS La vie », l'analyse produisait au contraire 1 027 fenêtres valides, puis s'arrêtait au quota de 8.

La nouvelle version garde les mêmes familles d'ancres et les filtres de base pour préserver l'interface, mais elle ajoute le rejet des fenêtres sous 45 % de parole, le rejet des fins coupées, une déduplication textuelle par tranches de trois mots et des durées cibles réparties dans la plage de campagne. Son indice de tri combine désormais 30 % de signaux narratifs lexicaux, 20 % de compréhensibilité approximée (densité + confiance), 30 % de transcription et 20 % d'autonomie. Audio et visuel restent non notés; la campagne agit comme contrainte dure. Les diagnostics rendent visibles les rejets et le quota. Cet indice reste heuristique et n'ajoute pas de compréhension sémantique.

## Corpus local réellement disponible

Le dossier `media/sources/` contient 7 fichiers, mais leurs empreintes SHA‑256 ne correspondent qu'à 2 vidéos distinctes. Six fichiers sont des copies binaires de la même source. Les copies et transcriptions enregistrées d'une même source ne sont pas comptées comme des cas indépendants.

| Source distincte | Type observable | Durée | Transcription évaluée |
|---|---|---:|---|
| `source_57e0fbd52b.mp4` — « Voici 3 mondes, lequel tu choisis ? » | Montage de scénarios/mondes, choix narratif | 270,0 s | Whisper small local : 6 segments, 43 mots; une transcription enregistrée plus ancienne, 23 segments/205 mots, est constituée de crédits de sous-titres halluciné (Amara) |
| `source_5efb9fef42.mp4` — « Les pubs VS La vie 2026 » | Divertissement humoristique, saynètes publicitaires et dialogues | 497,3 s | Whisper small local : 198 segments, 1 717 mots |

Le corpus ne contient pas de podcast, de gameplay commenté, ni d'interview longue indépendante. Une scène de style interview apparaît dans la vidéo humoristique, mais elle ne remplace pas un jeu de validation de podcasts/interviews. Il n'y a donc pas assez de diversité pour conclure sur ces formats.

## Comparaison avant/après

Les deux versions ont été exécutées sur les mêmes transcriptions locales, avec une campagne de 20–45 secondes et une limite de 8 propositions. « Fenêtres brutes » désigne les fenêtres valides avant déduplication; « examinées » compte les fenêtres candidates passées aux contrôles de durée et de fin. Les compteurs détaillés sont conservés dans `validation/comparison_results.json`.

| Transcription/source | Fenêtres brutes avant | Conservées avant | Fenêtres examinées après | Fenêtres valides après déduplication initiale | Conservées après |
|---|---:|---:|---:|---:|---:|
| « Voici 3 mondes », nouvelle transcription | 40 | 2 | 40 | 0 (40 sous le seuil de couverture de parole) | 0 |
| « Les pubs VS La vie », nouvelle transcription | 1 027 | 8 | 1 056 | 917 | 8 (limite atteinte) |
| Transcription Amara de « Voici 3 mondes » | 0 | 0 | 49 | 0; les 23 segments de crédits sont écartés | 0 |

La différence entre les « fenêtres examinées » et les fenêtres brutes avant vient de la nouvelle mesure de génération et des filtres ajoutés. Le classement historique a émis 2 suggestions à partir de seulement 43 mots étalés sur près d'une minute; les deux avaient environ 21–23 % de couverture de parole. Elles sont maintenant refusées. Pour la source dense, l'analyse a identifié 917 fenêtres admissibles avant déduplication et gardé 8 propositions différentes selon les contraintes demandées. Le moteur ne force donc pas 8 résultats lorsqu'aucun passage ne satisfait ses garde-fous.

## Revue qualitative des cas

### « Voici 3 mondes »

- Les deux suggestions de référence (environ 195–229 s et 223–249 s) reprenaient en grande partie les mêmes phrases de choix de monde malgré un faible chevauchement temporel. Les horodatages Whisper sont très clairsemés; le second extrait contient « Un hiver qui ne finit jamais », « Un chalet, du bois qui craque » et une fin mal formée (« Pré… est prête, si on avait vous choisi »).
- Ces deux propositions sont de faux positifs plausibles : elles sont notées par des marqueurs lexicaux, mais la parole ne couvre qu'une petite fraction de l'intervalle et le texte final est incomplet. Après le contrôle de densité, aucune proposition n'est conservée. Cette abstention est préférable à présenter ces fenêtres comme de bons clips.
- Passage potentiellement manqué : le segment des « trois mondes » doit être retranscrit avec une couverture fiable avant de pouvoir juger quelle histoire, révélation ou conclusion constitue le meilleur extrait. Le classement ne peut pas récupérer du contenu absent de la transcription.
- Les images échantillonnées montrent un montage visuel de scénarios, mais aucune écoute complète de la vidéo n'a été faite. La qualité audio et l'intérêt audiovisuel ne sont pas évalués.

### « Les pubs VS La vie »

- Risque éditorial de référence : la fenêtre 185–230 s, auparavant première au score, enchaînait des textes de publicité, une transition « dans les pubs / dans la vie » et plusieurs situations. Le résultat est dense en parole et reçoit donc un score élevé; le texte seul ne permet pas de décider si ce montage est une séquence comique voulue ou une coupe qui mélange deux scènes. La version actuelle retient encore à 185,4–223,9 s une fenêtre dense qui traverse la même transition. Son début « Vendez-vous votre voiture ? » et l’arrivée d’un dialogue de vie courante montrent que le défaut de cohérence narrative reste un faux positif; son ouverture peut aussi manquer le contexte précis. À écouter avant export.
- Passages intéressants présents parmi les résultats : la dispute « Descendez Arnaqueuse ! » (401–438 s), la saynète du fromage à 3 h du matin (452–479 s), et les commentaires sur la publicité Intermarché/IA (119–158 s). Ils ont une situation identifiable et, pour le fromage, une chute textuelle. Le texte seul ne permet pas de confirmer le jeu, les réactions, ni la coupe audio.
- Passage potentiellement manqué : la fin exacte de la saynète au fromage (« j'ai rêvé que tu étais dedans avec du fromage ») est une chute prometteuse; il faut écouter pour vérifier si la proposition finit après le rire/la réaction plutôt qu'au seul point final Whisper. Une fenêtre peut aussi omettre le contexte visuel de la saynète.
- Erreurs de transcription visibles dans le texte : mots et noms propres déformés, faux départs et ponctuation incertaine. Sans écoute de l'original, elles restent des erreurs probables, pas des erreurs acoustiquement confirmées.
- Le classement après correction garde 8 propositions sur 917 fenêtres admissibles. Le plafond demandé limite toujours la quantité; les autres fenêtres peuvent contenir des scènes pertinentes. Ce rapport n'affirme pas que le top 8 est exhaustif.

## Modifications mesurables

- Rejet des fenêtres dont les mots occupent moins de 45 % de l'intervalle. Sur la transcription clairsemée, les 40 fenêtres candidates de la référence sont rejetées et les 2 faux positifs disparaissent.
- Rejet des fins manifestement coupées lorsqu'aucune ponctuation ni pause ne marque un arrêt naturel.
- Déduplication basée sur les tranches de temps et les suites de trois mots. Cela traite le cas où des horodatages clairsemés donnent deux fenêtres différentes contenant presque le même passage.
- Les diagnostics séparent le nombre de segments rejetés comme hallucinations ou segments de mauvaise qualité, les fenêtres examinées, les rejets pour silence/fin coupée, les candidats avant diversité, les doublons et le quota.
- Le résultat expose des dimensions distinctes : intérêt narratif heuristique, compréhensibilité approximée, autonomie, qualité de transcription, audio (non analysé), visuel (non analysé) et respect des contraintes de campagne. L'indice de tri n'est pas une probabilité de viralité.
- Un formulaire permet d'annoter chaque proposition : excellent, bon, moyen, mauvais ou inutilisable, avec raisons et commentaire. Les annotations sont sauvegardées dans `validation/annotations.json`, indexées par source et coupe. Elles ne sont pas utilisées pour entraîner ni modifier le moteur. Le jeu commence vide et ne devient une validation humaine qu'après écoute et annotation effectives.

## Modèle sémantique local

La machine dispose de Transformers 4.57.6 et de PyTorch CPU dans `.alignenv`, mais pas d'`accelerate`. Qwen3-0.6B a été retenu comme essai raisonnable de petite taille, car sa fiche officielle le présente comme un modèle génératif multilingue. Le modèle (~596 M paramètres) a été téléchargé dans le cache utilisateur; aucun média n'a été transmis.

Deux extraits ont été testés en français avec le contexte textuel disponible. Le prompt ouvert ne rendait pas les catégories narratives; sur le passage incohérent « Voici 3 mondes », il attribuait pourtant 3/4 en compréhensibilité et autonomie. Un prompt restreint à un JSON de tags reconnaissait « absurde » dans la saynète du fromage, mais ajoutait « révélation » et mettait compréhensibilité/autonomie à 0. Pour le montage publicitaire, il renvoyait « absurde » et « question intrigante », sans repérer le besoin de contexte, avec les mêmes zéros. Les réponses varient selon le prompt et ne sont pas cohérentes avec une évaluation éditoriale fiable. Le prompt ouvert a pris 26 s et 36 s; le prompt contraint environ 9 s par extrait sur CPU. Le modèle n'est pas intégré au classement. Ce petit essai ne permet pas d'extrapoler à tous les modèles locaux.

Sources techniques : [fiche Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B), [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper), [fiche Whisper large-v3](https://huggingface.co/openai/whisper-large-v3). Une suite d'essais devra comparer d'autres modèles locaux sur les mêmes passages annotés, transmettre du contexte avant/après, exiger des justifications ancrées dans la transcription et évaluer séparément audio/visuel avec des outils adaptés.

## Limites et conclusion

### Audit du benchmark QMSum — métriques retirées

**Verdict : l'ancien benchmark de frontières n'est pas valide.** Il traitait les ensembles de `topic_list[].relevant_text_span` comme des labels de segmentation chronologique. QMSum annote des passages pertinents, parfois multiples et non contigus, pour des thèmes/requêtes de résumé. L'exemple officiel montre un thème avec trois plages séparées. Dans les partitions du test inspectées ici, 28 thèmes ont des plages multiples disjointes, 1 966 tours sont non annotés et 145 tours portent plusieurs labels (comptes sous l'hypothèse inclusive de l'ancien script). Le script excluait les paires adjacentes qui touchaient un tour non annoté, puis ne scorait que les 18 681 paires restantes; ses 112 « gold boundaries » sont des changements d'ensembles de labels, pas des frontières de sujet de référence. Sa tolérance était ±1 tour. En plus, l'appariement greedy pouvait sous-compter les matches à tolérance non nulle. Les formules micro P/R/F1 étaient usuelles, mais la référence, le dénominateur des négatifs et parfois le compte TP ne sont pas valides pour la segmentation. Le README officiel ne précise pas non plus si les indices de fin des plages sont inclusifs; le protocole précédent le supposait.

Les anciennes valeurs sont conservées dans [l'archive exploratoire invalidée](validation/history/qmsum_topic_benchmark_20261109_exploratory_invalid.json). Le [JSON courant](validation/qmsum_topic_benchmark.json) marque `status=invalid_for_segmentation` et ne publie aucune métrique de performance; [le script](tools/benchmark_topic_boundaries.py) calcule seulement la structure des annotations. Aucun modèle n'est intégré. La durée antérieure de 1 694,86 s décrit seulement le coût de cette exécution, pas la validité du résultat.

### Diagnostic local et récupération ciblée du VAD

**« Voici 3 mondes »** : la transcription locale `small`, plage 0–269,98 s, ne contenait que 6 segments et 41 word timestamps, tous autour de 193,7–249,1 s. La comparaison de la même vidéo et du même Whisper `small` avec seulement `vad_filter=False` a rendu 36 segments et 305 word timestamps en 75,9 s. Le passage sans VAD retrouve notamment le récit à partir d'environ 110 s et la question finale; il produit aussi quatre crédits Amara hallucinés (vers 28, 58, 88 et 268 s). Le classement a filtré ces crédits, conservé 101 mots utilisables, examiné 55 fenêtres, rejeté 34 fenêtres trop clairsemées et proposé 2 fenêtres (146,69–167,56 s à 83 % de couverture; 224,23–249,54 s à 47 %). Le premier passage VAD ne proposait rien et ses suggestions historiques étaient à 15–37 % de couverture sous le seuil 45 %.

Cela isole le défaut : le VAD a supprimé beaucoup de parole; le seuil de couverture a ensuite rejeté les fenêtres sur la transcription amputée. Les 45 % seuls ne sont donc pas la cause racine. Les PTS du flux audio et vidéo démarrent tous deux à zéro et durent le même temps, ce qui n'indique pas d'offset général. Sans VAD, les horodatages Whisper suivent le récit mais certains mots/noms restent faux; les crédits montrent que la passe non filtrée ne doit pas remplacer aveuglément la passe principale.

La reprise sans VAD du scan de clips est désormais plus conservatrice : elle ne part que si le premier passage a réellement produit des fenêtres candidates rejetées pour couverture de parole trop faible. Un résultat vide ou sous dix mots sans fenêtres à examiner — cas compatible avec silence ou musique mal reconnue — s'abstient au lieu de lancer une transcription coûteuse et potentiellement hallucinée. Lorsqu'elle est déclenchée, la reprise est acceptée uniquement si les filtres existants trouvent davantage de mots utilisables et au moins un candidat. Sinon la première passe reste choisie. Les poids et le seuil de couverture n'ont pas changé. Sur « Voici 3 mondes », le cas observé (42 mots utilisables, fenêtres clairsemées) conserve la reprise qui avait donné 2 candidates contre 0; elles restent à écouter, pas déclarées bonnes.

Limite de vérification : l'ablation Whisper et le ranker ont été exécutés directement sur le média local, et les fonctions de décision/horodatage du fallback sont couvertes par des tests. Le chemin complet de reprise depuis une nouvelle session Atelier n'a pas été exécuté de bout en bout; le résultat 0→2 mesure le pipeline de transcription/ranking contrôlé, pas une recette utilisateur complète.

**« Les pubs VS La vie »** : la sortie courante 185,40–223,93 s combine le visuel d'une publicité de lessive déjà en cours, la carte « Dans les pubs » (segment 193,03 s), un clip publicitaire vertical, la carte « La vie » (209,17 s), puis un échange de rue; elle se termine à 223,93 s, 0,12 s après le début du prochain segment ASR (223,81 s) et avant sa suite/la sortie comique. Des images à 185,4, 193,0, 194,4, 209,2, 210,0 et 223,9 s confirment ces changements. Une détection FFmpeg `scene` à seuil 0,25 signale des coupes à 188,00, 189,52 et 193,16 s dans l'intervalle; ce seuil ne mesure ni les sujets ni le récit. Ici, la frontière est le défaut démontré : début trop tôt dans une saynète précédente et fin coupée pendant l'action. Le texte Whisper dans cette zone déforme plusieurs mots/noms, tandis que les sous-titres incrustés visibles divergent à certains instants. Audio et visuel sont présents, mais le score `audio_quality` reste non mesuré; aucune règle de rejet basée uniquement sur les changements de plans n'a été ajoutée.

### Vérification de l'application et du dépôt

- La première exécution de ce rapport avait **30 tests réussis**. Après les correctifs ciblés du 11 novembre, `python -m unittest discover -s tests -v` compte **35 tests réussis**; le détail du nouveau lot figure ci-dessous.
- Serveur local réel démarré puis arrêté après vérification : `/`, `/library`, `/studio` et `/reference-review` ont répondu HTTP 200. Aucun média/session n'a été supprimé ou recréé par ce smoke test.
- Le dépôt n'a aucun commit (`No commits yet on main`); le contenu est suivi uniquement comme fichiers non suivis. Il n'existe donc pas de baseline Git propre permettant une comparaison de diff avant/après. Le comptage complet est passé de 122 entrées avant cette passe à 126 ensuite : archive de l'ancien JSON QMSum, nouveau test, manifeste, et cache `.pyc` généré. Le script d'audit remplace un fichier existant. Le statut reste `No commits yet on main`.
- À la date de ce premier audit, aucun fichier n'avait été supprimé et la suppression était bloquée. Le suivi du 11 novembre a ensuite supprimé les seuls caches `.pyc` reproductibles, par chemins explicites (394 932 octets). Les éléments historiques et les données protégées restent présents; voir le bilan de suivi ci-dessous et le manifeste.

## Suivi ciblé du 11 novembre 2026

### Reprise sans VAD : vérification et limite

Le fallback ne remplace jamais une passe primaire qui a déjà produit des propositions, car la condition d'entrée exige une liste de propositions vide. Le cas local clairsemé de « Voici 3 mondes » contient 42 mots après filtres et des fenêtres rejetées pour densité : il continue donc de déclencher une seule passe sans VAD. En revanche, aucun mot exploitable ou moins de dix mots sans fenêtre candidate ne suffit plus à déclencher la passe. Les tests vérifient aussi que les crédits Amara connus ne forment pas une proposition de fallback.

Les cas « pas de parole » et « musique/bruit » ont été couverts par des tests déterministes de transcriptions dont les segments sont rejetés par les signaux de non-parole de Whisper; ce ne sont pas des essais audio réels de silence ou de musique. Une hallucination sans parole, longue, confiante et ressemblant à une conversation peut encore passer les heuristiques lexicales. Le fallback est plus prudent et moins coûteux sur les scans vides, mais ces tests ne prouvent pas qu'il ne crée jamais de faux positif. Aucun nouveau test Whisper/modèle n'a été lancé : la comparaison locale VAD/sans-VAD précédente a mesuré 6 segments/41 timestamps contre 36/305 et environ 75,9 s pour la seconde passe.

### Frontières et modes de production

Un défaut mesurable était le padding de 120 ms ajouté après le dernier mot : sur le candidat 185,40 s de « Les pubs VS La vie », il portait la fin de 223,81 à 223,93 s, soit 120 ms dans le timestamp de la réplique suivante. Le moteur borne maintenant ce padding au début du mot reconnu suivant. La proposition finit à 223,81 s. Cela corrige un chevauchement temporel précis; cela ne démontre pas que 223,81 s est la meilleure fin narrative. La référence antérieure s'arrêtait à 229,59 s après plusieurs répétitions de l'annonce. Le candidat actuel plus court peut être une chute humoristique volontaire ou une conclusion prématurée; les transcriptions seules ne tranchent pas. Aucune pénalité générique de changement de scène ni prolongation basée sur le score n'a été introduite.

| Source | Mode | Avant cette correction | Après | Mesure locale après correction |
|---|---|---|---|---|
| Voici 3 mondes | Clips indépendants | 0 conservé par le moteur courant; la référence conservait 2 fenêtres à 15–37 % de couverture | 0 (inchangé) | Médiane classement 0,0045 s sur 5 exécutions |
| Voici 3 mondes | Épisodes | 3 parties : 0–195,71; 195,71–224,11; 224,11–269,98 s | Inchangé | 0,0016 s; 3 parties |
| Les pubs VS La vie | Clips indépendants | 8; candidat visé 185,40–223,93 s (score heuristique 84,9) | 8; même texte/score, fin 223,81 s | 0,9606 s médian sur 5 exécutions; dépassement de frontière réduit de 120 ms à 0 |
| Les pubs VS La vie | Épisodes | 4 parties : 0–218,69; 218,69–337,05; 337,05–438,37; 438,37–497,255 s | Inchangé | 0,0094 s; 4 parties |

Les résultats reproductibles utilisent les JSON locaux `validation/transcripts/source_57e0fbd52b_small.json` (SHA-256 `22c336ccd2740bec23b0790bbdacebc9b13f490d311cc775b492c12d425db6f1`) et `source_5efb9fef42_small.json` (SHA-256 `e1c6b73a867c917c8466a363f8e24ff92c7e1a314b9dcf08f589befe63919556`), les durées de campagne 20–45 s, limite 8 et médiane de 5 appels au ranker. Le chemin indépendant et `build_story_parts` sont bien différents : ils donnent des types de sortie et des découpages distincts. Mais le mode épisodes ne comprend pas le sens du récit : sur la vidéo de 497 s, sa première partie s'étend jusqu'à 218,69 s, à travers plusieurs saynètes. Sa continuité sémantique reste à améliorer.

### Tests, application et nettoyage

- **35 tests réussis**; la dernière exécution a pris 3,321 s dans unittest et environ 4,9 s au total. Cinq tests ajoutés : silence/bruit sans retry, reprise du cas clairsemé, parasite Amara sans candidat, frontière de clip ne débordant pas sur le mot suivant, et distinction des chemins « clips indépendants » / « épisodes ».
- La suite réexécute les smoke tests HTTP de l'accueil, bibliothèque, studio, prévisualisation, exports et annotations; ils sont tous réussis. Aucun modèle ou paquet n'a été ajouté.
- Le ranker ne fait pas d'inférence sémantique. Les indices internes et le nombre de propositions n'établissent pas une hausse de qualité narrative; aucun jeu de notes indépendant n'existe encore.
- Les temps rapportés ici sont ceux du ranker et du découpage sur CPU, sur transcriptions déjà disponibles. Aucun profil mémoire/GPU complet ni traitement intégral Whisper sur une configuration matérielle neuve n'a été mesuré pendant cette passe.
- Nettoyage : 14 fichiers `.pyc` reproductibles supprimés par chemins explicites, 394 932 octets. Bundles V0_11–V0_15, snapshots, scripts de réparation/diagnostic, données de V0_13, vidéos, sessions, exports, bibliothèques, configs, `.venv` et `.alignenv` conservés. Voir [MANIFESTE_NETTOYAGE.md](MANIFESTE_NETTOYAGE.md).

### Prochaines priorités techniques

1. Ajouter une évaluation reproductible des frontières sur des interactions complètes (question, réponse, réaction, conclusion), sans confondre coupe d'image et changement de sujet.
2. Construire une segmentation d'épisodes à partir de transitions de récit; les fins de phrase et un équilibrage temporel seuls sont insuffisants.
3. Mesurer les erreurs de début/fin, omissions et faux positifs sur davantage de vidéos indépendantes avant tout changement de score ou de seuil.
4. N'examiner une analyse visuelle/audio plus coûteuse que sur les fenêtres candidates et leur contexte immédiat, après avoir défini des critères qui distinguent une transition narrative d'un simple changement de plan.

- Il n'existe pas encore d'annotations humaines dans le fichier de validation. Les résultats avant/après mesurent la génération, les filtres et la diversité, pas le taux réel de bons clips.
- Aucune écoute des vidéos n'a permis de noter l'audio, les pauses audibles, le rire, les réactions ou les coupes exactes. L'évaluation visuelle n'a couvert que quelques images de la première source.
- Le corpus ne couvre que deux vidéos indépendantes et ne comprend pas les familles de contenu demandées (podcast, gameplay commenté, interview longue, conversation naturelle, récit long).
- Les heuristiques de mots-clés ne comprennent toujours pas réellement une histoire, une révélation ou une conclusion. Les erreurs de Whisper et la couverture manquante limitent le moteur en amont.
- Les 30 tests automatisés passent. Ils vérifient le code et la mécanique, pas la pertinence narrative.

Conclusion : les corrections mesurent une baisse des faux positifs sur une transcription notoirement clairsemée et limitent les doublons textuels. Les données et annotations disponibles ne permettent pas de déclarer le moteur fiable professionnellement.


## Calibration reproductible — 9 novembre 2026

Le banc local `tools.run_comparison` exécute six versions sur les mêmes JSON Whisper : la référence figée, le moteur courant, deux répartitions de poids et deux règles d'ancrage. Le manifeste lie le code de référence et chaque transcription à leurs empreintes SHA-256; le programme refuse de comparer silencieusement une entrée modifiée. `validation/latest_comparison.json` conserve les sorties, paramètres, candidats, raisons de rejet et annotations utilisées.

| Source | Version | Fenêtres examinées | Valides avant diversité | Rejets de diversité | Quota | Conservées |
|---|---|---:|---:|---:|---:|---:|
| Voici 3 mondes | Référence | 40 | 40 | 38 | 0 | 2 |
| Voici 3 mondes | Courant et pondérations | 40 | 0 | 0 | 0 | 0 |
| Voici 3 mondes | Ancres phrase/pause sans grille | 40 | 0 | 0 | 0 | 0 |
| Voici 3 mondes | Ponctuation seule sans grille | 25 | 0 | 0 | 0 | 0 |
| Les pubs VS La vie | Référence | 1 056 | 1 027 | 365 | 654 | 8 |
| Les pubs VS La vie | Courant et pondérations | 1 056 | 917 | 699 | 210 | 8 |
| Les pubs VS La vie | Ancres phrase/pause sans grille | 761 | 641 | 503 | 130 | 8 |
| Les pubs VS La vie | Ponctuation seule sans grille | 671 | 558 | 437 | 113 | 8 |

Dans la transcription dense, 22 fenêtres dépassent la durée, 39 échouent au seuil de densité vocale, 73 finissent sans ponctuation ni pause suffisante, 4 contiennent moins de 10 mots et 1 est éliminée pour répétition/diversité lexicale; 6 segments Whisper sont rejetés pour leurs métadonnées qualité. Les 917 restantes se répartissent en 8 sélectionnées, 699 rejetées par diversité et 210 au-delà du quota. Une proposition par profil a le même compteur, mais ce n'est pas une mesure d'amélioration narrative.

**Aucune annotation humaine n'était présente au lancement** dans `validation/annotations.json`; `validation/reference_spans.json` vient d'être créé séparément et est également vide. En conséquence, précision sur les propositions notées, rappel des passages positifs indépendants, erreurs médianes de début/fin et taux de corrections manuelles sont enregistrés comme `null` dans le résultat. Le rappel demande qu’une proposition couvre au moins 50 % de la référence et 20 % de sa propre durée. Les tableaux ci-dessus décrivent seulement le comportement des algorithmes et ne valident pas leur qualité éditoriale. Les anciennes annotations de propositions, si elles sont ajoutées, ne servent qu'à la précision des propositions notées. Les passages de référence sont choisis manuellement depuis le lecteur de la source, sans afficher ni reprendre les propositions du moteur; seuls ces intervalles indépendants servent à mesurer le rappel et les passages manqués.

Si l'on choisit de faire un audit de développement, les propositions peuvent être annotées sur leurs cartes et les références saisies dans la vue aveugle. Les raisons incluent « plusieurs scènes ou sujets » pour distinguer un défaut de cohérence narrative. Cette revue reste facultative : elle ne conditionne ni génération, ni sélection, ni export. Pour les comparaisons reproductibles, relancer depuis la racine du projet :

```powershell
.\.venv\Scripts\python.exe -m tools.run_comparison
```

Aucune dépendance supplémentaire n'est nécessaire. Le nouveau système rend les causes de rejet inspectables et compare les profils de façon reproductible, mais ne calibre pas encore les poids : il faut d'abord plusieurs évaluations humaines sur plusieurs vidéos indépendantes. Les deux seuls contenus locaux restent insuffisants pour la validation demandée.

Le banc accepte aussi, en arguments, un JSON de transcription autonome ou une transcription exportée depuis une session (`sessions/transcript_<id>.json`); dans ce dernier cas il retrouve les métadonnées source/durée dans la session correspondante et exclut suggestions et évaluations de la charge de transcription. Les chemins média enregistrés sont résolus avant le repli vers `media/sources`. Exemple après ajout de sources indépendantes :

```powershell
.\.venv\Scripts\python.exe -m tools.run_comparison .\sessions\transcript_<id>.json .\validation\transcripts\podcast.json
```

Les nouvelles entrées sont comparées à l'implémentation de référence figée et aux profils candidats. Le champ `baseline_output_verified_against_manifest` indique si les sorties de la référence ont déjà été figées pour cette entrée; les empreintes du fichier d'entrée, de la transcription normalisée, du média accessible et des moteurs sont enregistrées pour rendre chaque lancement traçable. Aucune modification de pondération par défaut n'est induite par une annotation isolée.


## Séparation des annotations et ajout de corpus

Après la clarification de l'annotation :

- `validation/annotations.json` reçoit uniquement les évaluations des propositions générées (note et raisons), avec un identifiant de revue lié à la source, aux temps et au texte-candidat.
- `validation/reference_spans.json` reçoit les passages choisis dans une vue aveugle dédiée `/reference-review`. Cette page ne montre aucune suggestion. Chaque entrée contient l'empreinte SHA-256 de la vidéo originale, les timestamps d'origine, le type principal de contenu, l'intérêt narratif et une justification facultative. Elle ne copie ni transcript, ni score, ni identifiant de candidat.
- `validation/source_catalog.json` conserve le classement principal de chaque vidéo pour pouvoir ajouter progressivement podcast, interview, divertissement, humour, gaming commenté, conversation ou récit. Des copies binaires ont le même hash et ne sont pas comptées comme vidéos indépendantes.

Aucun poids par défaut n'a été recalibré à partir des notes. Les profils pondérés restent des expériences distinctes dans le banc; ils ne remplacent pas le moteur actuel. Les fichiers d'annotations/source peuvent rester vides sans empêcher Clip Factory de produire ou d'exporter des clips. Leur remplissage est un audit facultatif, pas un travail demandé à chaque utilisateur. Pour les prochaines optimisations, voir [ETUDE_EVALUATION_AUTOMATIQUE.md](ETUDE_EVALUATION_AUTOMATIQUE.md), qui privilégie les corpus publics et des métriques reproductibles.
