# AGENTS.md

Hinweise für KI-Agenten, die in diesem Repository arbeiten.

## Monitoring-Projektstruktur

Das Monitoring-System besteht aus drei separaten Repositories, die als Geschwisterordner liegen. Relative Pfade ausgehend von diesem Repo:

- **API**: `../Monitoring-API`
- **Frontend**: `../Monitoring-Frontend`
- **Client**: `.` (dieses Repo)

Wenn Änderungen in diesem Client mit der API oder dem Frontend zusammenspielen, sollten die entsprechenden Repos referenziert oder mitgepflegt werden.

## Branch-Workflow

Neue Feature-/Fix-Branches **immer von `main`** erstellen:

```bash
git fetch origin
git checkout main
git pull origin main
git checkout -b fix/mein-fix
```

**Wichtig:**

- **Niemals** direkt auf `main` pushen oder committen.
- Alle Änderungen laufen über einen Feature-/Fix-Branch → Pull Request → Review → Merge nach `main`.
- Nach dem Merge nach `main` (durch Mensch/Review) deployt Coolify automatisch, falls für dieses Repo zutreffend.
- Jede Code-Änderung am Agent (`agent/`) muss `AGENT_VERSION` in `agent/models/constants.py` im selben Commit erhöhen — siehe [Client Version Bump](../Monitoring-API/AGENTS.md#client-version-bump). Ohne Bump erkennt der Auto-Updater die neue Version nicht und ausgerollte Agents bleiben auf dem alten Stand.
