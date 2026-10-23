FROM node:22-alpine AS build
WORKDIR /src
COPY package.json tsconfig.json index.html ./
COPY vite.config.ts ./
COPY src ./src
RUN npm install --no-audit --no-fund && npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 8080
