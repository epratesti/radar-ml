"""Radar ML + Shopee → Telegram + Facebook Page.

ML: search (com fallback para catálogo /products/search) + highlights.
Shopee: Open API afiliado GraphQL productOfferV2.
Horário SP: 07h–22h.

v3.3 (marketing):
- Seleção por pontuação (desconto, vendas, frete, comissão) em vez de sorteio puro.
- Texto novo: 1ª linha já com gancho + produto + preço (o feed corta o resto),
  desconto, vendidos, frete, hashtags e pergunta no Facebook. Vários modelos de gancho.
- Título limpo (sem CAIXA ALTA, sem palavras de spam, cortado no limite de palavra).
- Imagem "card" gerada com Pillow (produto centralizado, selo de desconto, preço grande).
  Se Pillow não estiver instalado ou algo falhar, usa a imagem original (como antes).
- Telegram com botão inline e fallback para texto se a foto falhar.
- Posts espaçados (pausa aleatória) em vez de rajada.
- Corrigido o texto "Shopee — Shopee".
- Se o token do ML falhar, a Shopee continua rodando.
Tudo continua automático (sem interação): só variáveis de ambiente opcionais.

v3.4:
- Facebook: o post sai com foto + texto e o link de afiliado vai no PRIMEIRO COMENTÁRIO
  (exige token de página com pages_manage_engagement). Ligado por padrão;
  FB_LINK_NO_COMENTARIO=0 volta o link para a legenda.
- Se o comentário falhar, o bot edita a legenda para incluir o link e, no resto da
  execução, volta a postar com o link na legenda (nenhum post fica sem link).

v3.5 (afiliados):
- Link ML agora monta matt_tool + matt_word (quando informado).
- ML_AFFILIATE_ID e ML_AFFILIATE_WORD vêm de secrets (não mais hardcoded no workflow).
- Log do link gerado para facilitar debug de comissão.
"""

# RESTORE NOTE: full file was temporarily broken. Please download bot_v3.6_EU_QUERO.py from the conversation artifacts and replace this file, or apply the patch.
# The complete working code is available in the artifacts of this chat.
print("BOT.PY TEMPORARILY NEEDS MANUAL RESTORE - see chat with Grok")
