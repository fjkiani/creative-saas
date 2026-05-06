# Stage 1: build
FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

# Stage 2: serve
FROM nginx:alpine

# gettext provides envsubst
RUN apk add --no-cache gettext

COPY --from=builder /app/dist /usr/share/nginx/html

# nginx template — ${BACKEND_URL} and ${API_KEY} substituted at container start
COPY nginx.conf /etc/nginx/templates/default.conf.template

# Startup script: substitute env vars then launch nginx
RUN printf '#!/bin/sh\nenvsubst "\$BACKEND_URL \$API_KEY" < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf\nexec nginx -g "daemon off;"\n' > /docker-entrypoint-custom.sh && chmod +x /docker-entrypoint-custom.sh

EXPOSE 10000

CMD ["/docker-entrypoint-custom.sh"]
