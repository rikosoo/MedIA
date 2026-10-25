FROM node:22-slim
# NÃO usar /app: com o projeto em /app e a rota /app do site, o Next.js troca o layout raiz pelo da área
# logada e a tela de login sai quebrada (sem formulário nem CSS, presa em "Carregando…").
WORKDIR /srv/web
COPY package*.json ./
RUN npm ci
COPY . .
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build
USER node
EXPOSE 3000
CMD ["npm", "start"]
