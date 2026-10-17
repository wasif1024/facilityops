# FacilityOps AI web app: built once with its settings, then served as static files by nginx.
#
# Vite bakes VITE_* settings into the bundle at build time, so they are build arguments here
# (azd passes them from the environment; see azure.yaml). None of them is a secret: client ids
# and API scopes are public by design, and the browser sees them anyway.

FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_BASE_URL
ARG VITE_AUTH_MODE=entra
ARG VITE_ENTRA_CLIENT_ID
ARG VITE_ENTRA_TENANT_ID
ARG VITE_ENTRA_API_SCOPE
ARG VITE_EXTERNAL_CLIENT_ID
ARG VITE_EXTERNAL_TENANT_SUBDOMAIN
ARG VITE_EXTERNAL_TENANT_ID
ARG VITE_EXTERNAL_API_SCOPE
RUN npm run build

# Unprivileged nginx: runs as a non-root user and listens on 8080.
FROM nginxinc/nginx-unprivileged:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
