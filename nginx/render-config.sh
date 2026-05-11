#!/bin/sh
set -eu

DOMAIN="${ASSET_TRACKER_DOMAIN:-_}"
EXTRA_DOMAINS="${ASSET_TRACKER_EXTRA_DOMAINS:-}"
SERVER_NAMES="$DOMAIN"
TEMPLATE="/opt/nginx-templates/default.conf.template"

if [ -n "$EXTRA_DOMAINS" ]; then
	SERVER_NAMES="$SERVER_NAMES $(printf '%s' "$EXTRA_DOMAINS" | tr ',' ' ')"
fi

if [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ] && [ -f "/etc/letsencrypt/live/${DOMAIN}/privkey.pem" ]; then
	TEMPLATE="/opt/nginx-templates/default.conf.ssl.template"
fi

sed \
	-e "s/__ASSET_TRACKER_DOMAIN__/${DOMAIN}/g" \
	-e "s/__ASSET_TRACKER_SERVER_NAMES__/${SERVER_NAMES}/g" \
	"$TEMPLATE" \
	> /etc/nginx/conf.d/default.conf
