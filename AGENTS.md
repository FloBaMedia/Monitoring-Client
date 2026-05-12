# AGENTS.md

Hinweise für KI-Agenten, die in diesem Repository arbeiten.

## Monitoring-Projektstruktur

Das Monitoring-System besteht aus drei separaten Repositories, die als Geschwisterordner liegen. Relative Pfade ausgehend von diesem Repo:

- **API**: `../Monitoring-API`
- **Frontend**: `../Monitoring-Frontend`
- **Client**: `.` (dieses Repo)

Wenn Änderungen in diesem Client mit der API oder dem Frontend zusammenspielen, sollten die entsprechenden Repos referenziert oder mitgepflegt werden.
