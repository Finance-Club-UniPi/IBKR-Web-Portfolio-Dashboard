#!/usr/bin/env bash
# Update IBKR Portfolio Dashboard to the latest version
# Release ZIPs keep Compose beside the launcher; the source repo keeps it above.
cd -- "$(dirname -- "$0")" || exit 1
if [ ! -f docker-compose.yml ]; then
    cd .. || exit 1
fi
echo ""
echo " Updating IBKR Portfolio Dashboard..."
docker compose pull && docker compose up -d
echo " Done. Dashboard restarted at http://localhost:8050"
echo ""
