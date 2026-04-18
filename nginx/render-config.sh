#!/bin/sh
set -eu

DOMAIN="${ASSET_TRACKER_DOMAIN:-_}"
TEMPLATE="/opt/nginx-templates/default.conf.template"

if [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ] && [ -f "/etc/letsencrypt/live/${DOMAIN}/privkey.pem" ]; then
	TEMPLATE="/opt/nginx-templates/default.conf.ssl.template"
fi

sed "s/__ASSET_TRACKER_DOMAIN__/${DOMAIN}/g" "$TEMPLATE" \
	> /etc/nginx/conf.d/default.conf
