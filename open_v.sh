#!/bin/bash
# Abre el chat del vigilante de varios repos, cada uno en su puerto.
# Uso: bash open_v.sh        (Ctrl+C cierra todos)

REPOS="$HOME/DevelopmentProjects"

CHATS=(
  "therapist-calendar 8781"
  "contenido-kokora 8782"
  "web-chinese-tutor 8783"
  "lector-tutor 8784"
)

pids=()
cerrar() {
  echo
  echo "Cerrando chats…"
  kill "${pids[@]}" 2>/dev/null
  exit 0
}
trap cerrar INT TERM

for chat in "${CHATS[@]}"; do
  read -r repo puerto <<< "$chat"
  carpeta="$REPOS/$repo"
  if [ ! -f "$carpeta/conversaciones/_app/chat.py" ]; then
    echo "✗ $repo: no encontré $carpeta/conversaciones/_app/chat.py"
    continue
  fi
  rama=$(git -C "$carpeta" branch --show-current)
  [ "$rama" != "main" ] && echo "  ⚠ $repo está en la rama «$rama», no en main: puede verse una versión vieja"
  (cd "$carpeta" && python3 conversaciones/_app/chat.py sincronizar > "/tmp/vigilante-$repo-sync.log" 2>&1) \
    || echo "  ⚠ $repo: no pude actualizarlo, mira /tmp/vigilante-$repo-sync.log"
  (cd "$carpeta" && python3 conversaciones/_app/chat.py ui --puerto "$puerto" --no-abrir) \
    > "/tmp/vigilante-$repo.log" 2>&1 &
  pids+=($!)
  echo "✓ $repo → http://127.0.0.1:$puerto"
done

if [ ${#pids[@]} -eq 0 ]; then
  echo "No se abrió ningún chat. Revisa la carpeta REPOS al inicio de este archivo."
  exit 1
fi

sleep 3
for chat in "${CHATS[@]}"; do
  read -r repo puerto <<< "$chat"
  [ -f "$REPOS/$repo/conversaciones/_app/chat.py" ] && open "http://127.0.0.1:$puerto"
done

echo
echo "Listo. Deja esta ventana abierta; Ctrl+C cierra todos los chats."
echo "Si uno no carga, mira su registro en /tmp/vigilante-<repo>.log"
wait
