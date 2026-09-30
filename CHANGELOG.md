# Changelog

## 1.0.1

- **Assistant de calibrage** : la montée ne démarrait pas à l'étape 2 (un « stop » envoyé juste avant l'ordre de montée l'empêchait d'être pris en compte par les modules RF). L'ordre de montée est maintenant envoyé seul.
- **Écart minimal de 1,2 s entre deux commandes radio** vers un même moteur, pour qu'aucune ne soit perdue (arrêt suivi d'un nouvel ordre, par exemple). La position est calculée à partir de l'envoi réel.

## 1.0.0

Première version.

- **Volets temporisés configurables depuis l'interface** : commande (switch RFXCOM ou autre), temps de montée et de descente, commande inversée, pièce. Un volet = un appareil, ajouté avec « Ajouter un volet ».
- **Arrêt par répétition de la commande** (modules RF AC / DI-O / Chacon), ou par une entité d'arrêt dédiée. Un ordre dans le sens où le volet bouge déjà n'est jamais renvoyé (il l'arrêterait).
- **Recalage aux extrémités** : secondes ajoutées quand on va en butée, et course complète si le volet est déjà annoncé en butée (utile quand un ordre radio n'est pas passé). Action « Recaler ».
- **Assistant de calibrage** : chronomètre la montée et la descente depuis l'interface.
- **Interrupteurs muraux** : relayés (appui = ouvrir / fermer, stop si le volet bouge déjà) ou appairés au moteur (suivi de la position seulement). **Liaisons** vers n'importe quel autre volet (Z-Wave, Zigbee…).
- **Position mémorisée** au redémarrage, mises à jour fluides (1 s) et arrêt calé exactement sur la fin du trajet.
- **Reprise de Cover Time Based** (`cover_rf_time_based`) : volets, temps, switchs, sens et interrupteurs muraux détectés ; entity_id, pièces, alias vocaux, labels et exposition conservés ; automatisations d'interrupteurs remplacées désactivées. Action « Analyser l'existant » pour voir ce qui serait repris.
