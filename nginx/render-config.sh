#!/bin/sh
set -eu

DOMAIN="${ASSET_TRACKER_DOMAIN:-_}"
EXTRA_DOMAINS="${ASSET_TRACKER_EXTRA_DOMAINS:-}"
PLACEHOLDER_DOMAINS="${ASSET_TRACKER_PLACEHOLDER_DOMAINS:-}"
SERVER_NAMES="$DOMAIN"
TEMPLATE="/opt/nginx-templates/default.conf.template"

if [ -n "$EXTRA_DOMAINS" ]; then
	SERVER_NAMES="$SERVER_NAMES $(printf '%s' "$EXTRA_DOMAINS" | tr ',' ' ')"
fi

PLACEHOLDER_SERVER_NAMES="$(printf '%s' "$PLACEHOLDER_DOMAINS" | tr ',' ' ' | xargs || true)"

build_placeholder_http_servers() {
	if [ -z "$PLACEHOLDER_SERVER_NAMES" ]; then
		return 0
	fi

	cat <<EOF
server {
	listen 80;
	listen [::]:80;
	server_name $PLACEHOLDER_SERVER_NAMES;

	location ^~ /.well-known/acme-challenge/ {
		root /var/www/certbot;
	}

	location = / {
		root /usr/share/nginx/placeholder;
		try_files /index.html =404;
	}

	location / {
		return 404;
	}
}
EOF
}

build_placeholder_http_redirect_servers() {
	if [ -z "$PLACEHOLDER_SERVER_NAMES" ]; then
		return 0
	fi

	cat <<EOF
server {
	listen 80;
	listen [::]:80;
	server_name $PLACEHOLDER_SERVER_NAMES;

	location ^~ /.well-known/acme-challenge/ {
		root /var/www/certbot;
	}

	location / {
		return 301 https://\$host\$request_uri;
	}
}
EOF
}

build_placeholder_https_servers() {
	if [ -z "$PLACEHOLDER_SERVER_NAMES" ]; then
		return 0
	fi

	cat <<EOF
server {
	listen 443 ssl;
	listen [::]:443 ssl;
	server_name $PLACEHOLDER_SERVER_NAMES;

	ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
	ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
	ssl_session_cache shared:SSL:10m;
	ssl_session_timeout 10m;
	ssl_protocols TLSv1.2 TLSv1.3;
	ssl_prefer_server_ciphers on;

	add_header Cross-Origin-Opener-Policy "same-origin" always;
	add_header Cross-Origin-Resource-Policy "same-origin" always;
	add_header Permissions-Policy "camera=(), geolocation=(), microphone=()" always;
	add_header Referrer-Policy "same-origin" always;
	add_header X-Content-Type-Options "nosniff" always;
	add_header X-Frame-Options "DENY" always;

	location = / {
		add_header Cache-Control "no-store" always;
		add_header Pragma "no-cache" always;
		root /usr/share/nginx/placeholder;
		try_files /index.html =404;
	}

	location / {
		return 404;
	}
}
EOF
}

if [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ] && [ -f "/etc/letsencrypt/live/${DOMAIN}/privkey.pem" ]; then
	TEMPLATE="/opt/nginx-templates/default.conf.ssl.template"
fi

PLACEHOLDER_HTTP_SERVERS="$(build_placeholder_http_servers)"
PLACEHOLDER_HTTPS_SERVERS=""

if [ "$TEMPLATE" = "/opt/nginx-templates/default.conf.ssl.template" ]; then
	PLACEHOLDER_HTTP_SERVERS="$(build_placeholder_http_redirect_servers)"
	PLACEHOLDER_HTTPS_SERVERS="$(build_placeholder_https_servers)"
fi

awk \
	-v domain="$DOMAIN" \
	-v server_names="$SERVER_NAMES" \
	-v placeholder_http_servers="$PLACEHOLDER_HTTP_SERVERS" \
	-v placeholder_https_servers="$PLACEHOLDER_HTTPS_SERVERS" '
	{
		gsub(/__ASSET_TRACKER_DOMAIN__/, domain)
		gsub(/__ASSET_TRACKER_SERVER_NAMES__/, server_names)
		if ($0 ~ /__ASSET_TRACKER_PLACEHOLDER_HTTP_SERVERS__/) {
			print placeholder_http_servers
			next
		}
		if ($0 ~ /__ASSET_TRACKER_PLACEHOLDER_HTTPS_SERVERS__/) {
			print placeholder_https_servers
			next
		}
		print
	}
	' "$TEMPLATE" \
	> /etc/nginx/conf.d/default.conf
