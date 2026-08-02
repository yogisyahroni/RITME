#!/usr/bin/env bash
# Rebuilds frontend/dist/ from frontend/src/App.jsx.
# Only needed if you edit the UI — the repo already ships a working
# dist/ build, so `python server.py` works with zero Node.js setup
# unless you're changing the frontend code itself.
#
# Requires Node.js. Run from the frontend/ directory:
#   cd frontend && ./build.sh
set -e

echo "Installing build tools (esbuild, tailwindcss)…"
npm install --no-save esbuild tailwindcss@3 react@18 react-dom@18 lucide-react@0.383.0

echo "Bundling React app…"
cat > /tmp/ritme_entry.jsx << 'EOF'
import React from "react";
import { createRoot } from "react-dom/client";
import Ritme from "./src/App.jsx";
const root = createRoot(document.getElementById("root"));
root.render(<Ritme />);
EOF
cp /tmp/ritme_entry.jsx entry.jsx
npx esbuild entry.jsx --bundle --loader:.jsx=jsx --format=iife --outfile=dist/bundle.js

echo "Building Tailwind CSS…"
cat > tailwind.config.js << 'EOF'
module.exports = { content: ["./src/App.jsx", "./src/TimelineEditor.jsx"], theme: { extend: {} }, plugins: [] }
EOF
cat > input.css << 'EOF'
@tailwind base;
@tailwind components;
@tailwind utilities;
EOF
npx tailwindcss -i input.css -o dist/tailwind.css --minify

# Cache-busting: inject mtime bundle.js sebagai versi query string di index.html
# biar browser selalu fetch bundle terbaru setelah rebuild (URL beda = cache miss).
VER=$(stat -c %Y dist/bundle.js 2>/dev/null || date +%s)
sed -i "s|bundle.js?v=[0-9]*|bundle.js?v=$VER|" dist/index.html
echo "Done. dist/bundle.js + dist/tailwind.css rebuilt (bundle?v=$VER)."
