#!/bin/bash
cd /Users/jackmichaels/march_madness
/opt/miniconda3/bin/python3 -m scripts.update_scores
/usr/bin/git add data/games.json
/usr/bin/git commit -m "Auto-update scores" --allow-empty=false || exit 0
/usr/bin/git pull --rebase
/usr/bin/git push
