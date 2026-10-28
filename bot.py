"""Radar ML + Shopee → Telegram + Facebook Page.

ML: search (com fallback para catálogo /products/search) + highlights.
Shopee: Open API afiliado GraphQL productOfferV2.
Horário SP: 07h–22h.

v3.4 (marketing + engagement):
- Seleção por pontuação (desconto, vendas, frete, comissão) em vez de sorteio puro.
- Texto novo: 1ª linha já com gancho + produto + preço (o feed corta o resto),
  desconto, vendidos, frete, hashtags e pergunta no Facebook. Vários modelos de gancho.
- Título limpo (sem CAIXA ALTA, sem palavras de spam, cortado no limite de palavra).
- Imagem "card" gerada com Pillow (produto centralizado, selo de desconto, preço grande).
- Telegram com botão inline e fallback para texto se a foto falhar.
- Posts espaçados (pausa aleatória) em vez de rajada.
- NOVO: além do link na legenda, tenta publicar o link também no 1º comentário
  do Facebook (aumenta engajamento). Se o comentário falhar, o post continua
  normal com o link na legenda (zero risco). Controlado por FB_LINK_NO_COMENTARIO (padrão=1).
- Menção mais forte de entrega rápida / frete grátis + destaque de desconto alto.
- Se o token do ML falhar, a Shopee continua rodando.
Tudo continua automático (sem interação): só variáveis de ambiente opcionais.
"""

import os, io, json, html, re, random, hashlib, time, unicodedata, datetime as dt
from itertools import zip_longest
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    PIL_OK = True
except Exception:
    PIL_OK = False


def env_int(nome, padrao):
    try:
        return int((os.getenv(nome) or "").strip() or padrao)
    except ValueError:
        return padrao


AFF_ID = os.getenv("ML_AFFILIATE_ID", "71970283")
AFF_PARAMS = os.getenv("ML_AFFILIATE_PARAMS", "matt_tool={id}")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "").strip()
FB_ACCESS_TOKEN = os.getenv("FB_ACCESS_TOKEN", "").strip()
POSTS_POR_GRUPO = env_int("POSTS_POR_GRUPO", 2)
MAX_POSTS = env_int("MAX_POSTS", 8)
REPETIR_APOS_DIAS = env_int("REPETIR_APOS_DIAS", 3)
SHOPEE_POSTS = env_int("SHOPEE_POSTS", 3)
SHOPEE_APP_ID = os.getenv("SHOPEE_APP_ID", "").strip()
SHOPEE_SECRET = os.getenv("SHOPEE_SECRET", "").strip()
SHOPEE_GQL = "https://open-api.affiliate.shopee.com.br/graphql"
POOL_FATOR = max(1, env_int("POOL_FATOR", 3))
DESCONTO_MIN = env_int("DESCONTO_MIN", 0)
PAUSA_MIN = max(0, env_int("PAUSA_MIN_SEG", 45))
PAUSA_MAX = max(PAUSA_MIN, env_int("PAUSA_MAX_SEG", 120))
IMG_CARD = (os.getenv("IMG_CARD", "1").strip() != "0")
BRAND_NAME = os.getenv("BRAND_NAME", "").strip()
FB_LINK_NO_COMENTARIO = (os.getenv("FB_LINK_NO_COMENTARIO", "1").strip() != "0")
API = "https://api.mercadolibre.com"
FB_API = "https://graph.facebook.com/v21.0"
ROOT = Path(__file__).parent
TRENDS = ROOT / "docs" / "trends.json"
STATE = ROOT / "state" / "posted.json"
TZ = ZoneInfo("America/Sao_Paulo")
FB_ATIVO = bool(FB_PAGE_ID and FB_ACCESS_TOKEN)
FB_FALHAS = 0
FB_ERROS_FATAIS = {3, 10, 102, 190, 200, 240, 283, 368}
SEARCH_403 = 0
SEARCH_OFF = False
CAT_FALHAS = 0
CAT_OFF = False
print("bot.py incompleto - restaurar versao completa")
