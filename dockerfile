FROM python:3.11-slim

# Installation des dépendances système
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Installation d'Ollama
RUN curl -fsSL https://ollama.com/install.sh | sh

# On prépare le dossier de travail
WORKDIR /app
COPY . .

# On s'assure que le port de Hugging Face est exposé
EXPOSE 7860

# Démarrage d'Ollama, téléchargement du modèle 3B, puis lancement de ton serveur
CMD ollama serve & sleep 5 && ollama pull qwen2.5-coder:3b && python ash.py