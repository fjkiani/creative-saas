# Stage 1: build
FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

# Stage 2: serve
FROM nginx:alpine

# Install gettext for envsubst
RUN apk add --no-cache gettext

COPY --from=builder /app/dist /usr/share/nginx/html

# nginx.conf uses ${BACKEND_URL} and ${API_KEY} — substituted at container start
COPY nginx.conf /etc/nginx/templates/default.conf.template

EXPOSE 10000

# envsubst replaces ${BACKEND_URL} and ${API_KEY} in the nginx template,
# writes the result to conf.d/default.conf, then starts nginx
CMD ["/bin/sh", "-c", \
     "envsubst '${BACKEND_URL} ${API_KEY}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]
