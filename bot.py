"""Radar ML + Shopee → Telegram + Facebook Page.

ML: search (com fallback para catálogo /products/search) + highlights.
Shopee: Open API afiliado GraphQL productOfferV2.
Horário SP: 07h–22h.

v3.9 (nov/2026):
- Corrige preço 0 da Shopee: normaliza priceMin (micros / centavos / reais)
  e descarta valores inválidos (< R$ 0,50 ou > R$ 50.000).
- Desliga posts do Mercado Livre por padrão (Telegram + Facebook).
  Para reativar: secret/env ML_ENABLED=1.
"""

# TEMPORARY RESTORE - full file will be pushed next
print('Use the local fixed bot.py')
