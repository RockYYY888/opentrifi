#!/bin/sh
set -eu

DOMAIN="${ASSET_TRACKER_DOMAIN:-_}"
EXTRA_DOMAINS="${ASSET_TRACKER_EXTRA_DOMAINS:-}"
PLACEHOLDER_DOMAINS="${ASSET_TRACKER_PLACEHOLDER_DOMAINS:-}"
ENABLE_DEFAULT_PLACEHOLDER="${ASSET_TRACKER_ENABLE_DEFAULT_PLACEHOLDER:-false}"
SERVER_NAMES="$DOMAIN"
TEMPLATE="/opt/nginx-templates/default.conf.template"

if [ -n "$EXTRA_DOMAINS" ]; then
	SERVER_NAMES="$SERVER_NAMES $(printf '%s' "$EXTRA_DOMAINS" | tr ',' ' ')"
fi

PLACEHOLDER_SERVER_NAMES="$(printf '%s' "$PLACEHOLDER_DOMAINS" | tr ',' ' ' | xargs || true)"

is_default_placeholder_enabled() {
	case "$ENABLE_DEFAULT_PLACEHOLDER" in
		1 | true | TRUE | yes | YES | on | ON)
			return 0
			;;
		*)
			return 1
			;;
	esac
}

build_default_http_placeholder_server() {
	if ! is_default_placeholder_enabled; then
		return 0
	fi

	cat <<EOF
server {
	listen 80 default_server;
	listen [::]:80 default_server;
	server_name _;

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

build_default_https_placeholder_server() {
	if ! is_default_placeholder_enabled; then
		return 0
	fi

	cat <<EOF
server {
	listen 443 ssl default_server;
	listen [::]:443 ssl default_server;
	server_name _;

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
DEFAULT_HTTP_PLACEHOLDER_SERVER="$(build_default_http_placeholder_server)"
DEFAULT_HTTPS_PLACEHOLDER_SERVER=""

if [ "$TEMPLATE" = "/opt/nginx-templates/default.conf.ssl.template" ]; then
	PLACEHOLDER_HTTP_SERVERS="$(build_placeholder_http_redirect_servers)"
	PLACEHOLDER_HTTPS_SERVERS="$(build_placeholder_https_servers)"
	DEFAULT_HTTPS_PLACEHOLDER_SERVER="$(build_default_https_placeholder_server)"
fi

awk \
	-v domain="$DOMAIN" \
	-v server_names="$SERVER_NAMES" \
	-v placeholder_http_servers="$PLACEHOLDER_HTTP_SERVERS" \
	-v default_http_placeholder_server="$DEFAULT_HTTP_PLACEHOLDER_SERVER" \
	-v default_https_placeholder_server="$DEFAULT_HTTPS_PLACEHOLDER_SERVER" \
	-v placeholder_https_servers="$PLACEHOLDER_HTTPS_SERVERS" '
	{
		gsub(/__ASSET_TRACKER_DOMAIN__/, domain)
		gsub(/__ASSET_TRACKER_SERVER_NAMES__/, server_names)
		if ($0 ~ /__ASSET_TRACKER_DEFAULT_HTTP_PLACEHOLDER_SERVER__/) {
			print default_http_placeholder_server
			next
		}
		if ($0 ~ /__ASSET_TRACKER_DEFAULT_HTTPS_PLACEHOLDER_SERVER__/) {
			print default_https_placeholder_server
			next
		}
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
