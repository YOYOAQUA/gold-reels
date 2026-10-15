#!/usr/bin/env bash
# Gold Reels installer for macOS. Everything goes into one folder (default ~/GoldReels).
set -euo pipefail

STUDIO="${GOLD_REELS_HOME:-$HOME/GoldReels}"
SKILL="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"

echo "[1/5] Python manager (uv)"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

echo "[2/5] Studio folder: $STUDIO"
mkdir -p "$STUDIO/input" "$STUDIO/projects" "$STUDIO/output" "$STUDIO/.tmp"

echo "[3/5] Studio Python"
if [ ! -x "$STUDIO/.venv/bin/python" ]; then
  uv venv --python 3.11 "$STUDIO/.venv"
fi

echo "[4/5] Video engine + Hebrew speech-to-text"
uv pip install --python "$STUDIO/.venv/bin/python" "faster-whisper>=1.1" "imageio-ffmpeg>=0.5"

cat > "$STUDIO/gr" <<EOF
#!/usr/bin/env bash
export GOLD_REELS_HOME="$STUDIO"
exec "$STUDIO/.venv/bin/python" "$SKILL/scripts/gr.py" "\$@"
EOF
chmod +x "$STUDIO/gr"

echo "[5/5] Hebrew model (~1.6GB, first time only) + self-test"
"$STUDIO/gr" setup
