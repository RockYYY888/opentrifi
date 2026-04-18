FROM nginx:1.27-alpine

COPY default.conf.template /opt/nginx-templates/default.conf.template
COPY default.conf.ssl.template /opt/nginx-templates/default.conf.ssl.template
COPY render-config.sh /docker-entrypoint.d/40-render-config.sh

RUN chmod +x /docker-entrypoint.d/40-render-config.sh
