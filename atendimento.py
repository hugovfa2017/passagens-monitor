"""Responde a quem escreve para um bot do Telegram e avisa o dono quando um chat novo pede os alertas.

Os bots dos monitores (passagens e PS5) só enviavam: quem tocava em "Iniciar" ficava sem resposta e, para incluir
outro celular, era preciso descobrir o número do chat à mão. Aqui, a cada rodada:
  1. lê o que chegou ao bot desde a última rodada (getUpdates);
  2. responde a cada chat: "este chat já recebe" ou "pedido enviado a quem cuida do monitor";
  3. manda ao dono (o primeiro chat da lista), em particular, quem pediu e o valor novo do segredo, pronto para colar;
  4. confirma a leitura, para não responder duas vezes.
O repositório é público: nome e número de chat só circulam dentro do Telegram. O que `atender` devolve (e vai para o
log) são só contagens.

Uso (cada monitor passa o seu bot):
    atender(api, chats, nome='monitor de passagens', sobre='Chega uma mensagem por hora.', segredo='TELEGRAM_CHAT_ID')
    api(metodo, campos) -> resposta do Telegram (dict); levanta exceção em erro de rede ou recusa.
    python3 atendimento.py --teste   -> auto-checagem (não envia nada)
"""
import html
import sys

SEGREDOS_URL = 'https://github.com/hugovfa2017/passagens-monitor/settings/secrets/actions'
LIMITE = 10   # pedidos novos atendidos por rodada; o que passar disso fica sem resposta (e não se repete)


def _quem(chat):
    """'Maria Silva (@maria)' ou 'grupo Família', para o dono reconhecer quem pediu."""
    if chat.get('type') in ('group', 'supergroup', 'channel'):
        return f"grupo {chat.get('title') or 'sem nome'}"
    nome = ' '.join(filter(None, (chat.get('first_name'), chat.get('last_name')))) or 'sem nome'
    return nome + (f" (@{chat['username']})" if chat.get('username') else '')


def _quem_escreveu(novidades):
    """{id do chat: chat} de quem mandou mensagem ao bot ou o colocou num grupo; um por chat, na ordem de chegada.
    Quem bloqueou o bot ou o tirou do grupo depois disso sai da lista."""
    achados = {}
    for u in novidades:
        m = u.get('message') or u.get('my_chat_member') or {}
        chat = m.get('chat') or {}
        if chat.get('id') is None:
            continue
        saiu = 'left_chat_member' in m or ('my_chat_member' in u and
                                           (m.get('new_chat_member') or {}).get('status') not in ('member', 'administrator'))
        if saiu:
            achados.pop(str(chat['id']), None)
        else:
            achados.setdefault(str(chat['id']), chat)
    return achados


def atender(api, chats, nome, sobre, segredo, limite=LIMITE):
    """chats: ids que já recebem os alertas (o primeiro é o dono). Nunca levanta exceção; devolve uma linha sem dado pessoal."""
    try:
        novidades = api('getUpdates', {'timeout': 0}).get('result') or []
    except Exception as e:   # sem str(e): pode trazer o token na URL
        return f"ATENDER_FALHOU {type(e).__name__} {getattr(e, 'code', '')}".strip()
    if not novidades:
        return 'ATENDER ninguém escreveu'
    ja = [str(c) for c in chats]
    escreveram = _quem_escreveu(novidades)
    novos = {i: c for i, c in escreveram.items() if i not in ja}
    atendidos = dict(list(novos.items())[:limite])
    enviadas = falhas = 0

    def mandar(chat, texto, **extra):
        nonlocal enviadas, falhas
        try:
            api('sendMessage', {'chat_id': chat, 'text': texto, 'parse_mode': 'HTML', 'disable_web_page_preview': 'true', **extra})
            enviadas += 1
        except Exception:
            falhas += 1

    for i in escreveram:
        if i in ja:
            mandar(i, f"✅ Este chat já recebe os alertas do {nome}.\n{sobre}\n"
                      "<i>O bot só envia avisos; esta resposta sai na rodada seguinte à sua mensagem.</i>")
    for i in atendidos:
        if ja:
            mandar(i, f"👋 Oi! Este bot manda os alertas do {nome}.\nAvisei quem cuida do monitor que este chat quer receber. "
                      f"Depois que ele liberar, os alertas chegam aqui.\nCódigo deste chat: <code>{i}</code>")
        else:
            mandar(i, f"👋 Oi! Este bot manda os alertas do {nome}, mas ainda não há nenhum chat cadastrado.\n"
                      f"Código deste chat: <code>{i}</code>\nEsse número vai no segredo <b>{segredo}</b> do GitHub.")
    if ja and atendidos:
        linhas = '\n'.join(f"• {html.escape(_quem(c))}: <code>{i}</code>" for i, c in atendidos.items())
        mandar(ja[0], f"🔔 <b>Pedido para receber os alertas do {nome}</b>\n{linhas}\n\n"
                      f"Para liberar, ponha este valor no segredo <b>{segredo}</b> do GitHub (crie o segredo se ele não existir):\n"
                      f"<code>{','.join([*ja, *atendidos])}</code>\n\n"
                      "<i>Não reconhece alguém? Tire o número dele do valor: quem não está no segredo não recebe nada.</i>",
               reply_markup='{"inline_keyboard": [[{"text": "Abrir os segredos no GitHub", "url": "%s"}]]}' % SEGREDOS_URL)
    try:   # confirma a leitura: o Telegram descarta o que já foi atendido
        api('getUpdates', {'offset': max(u['update_id'] for u in novidades) + 1, 'timeout': 0})
    except Exception:
        return f'ATENDER_FALHOU ao confirmar a leitura (enviadas={enviadas}; pode responder de novo na próxima rodada)'
    return f'ATENDER enviadas={enviadas} pedidos_novos={len(novos)} falhas={falhas}'


def _teste():
    def bot(novidades, recusar=()):
        """Um Telegram de mentira: devolve as novidades até a leitura ser confirmada e anota o que foi enviado."""
        estado = {'fila': list(novidades), 'enviado': []}

        def api(metodo, campos=None):
            campos = campos or {}
            if metodo == 'getUpdates':
                estado['fila'] = [u for u in estado['fila'] if u['update_id'] >= campos.get('offset', 0)]
                return {'ok': True, 'result': list(estado['fila'])}
            if campos['chat_id'] in recusar:
                raise OSError('https://api.telegram.org/bot123:SEGREDO/sendMessage')
            estado['enviado'].append((campos['chat_id'], campos['text'], campos.get('reply_markup')))
            return {'ok': True}
        return api, estado

    msg = lambda n, chat, texto='/start': {'update_id': n, 'message': {'chat': chat, 'text': texto}}
    dono = {'id': 111111111, 'type': 'private', 'first_name': 'Dono'}
    maria = {'id': 222222222, 'type': 'private', 'first_name': 'Maria', 'last_name': '<Silva>', 'username': 'maria'}
    grupo = {'id': -1001234567890, 'type': 'supergroup', 'title': 'Família'}
    args = dict(nome='monitor de passagens', sobre='Chega uma mensagem por hora.', segredo='TELEGRAM_CHAT_ID')

    # ninguém escreveu: não envia nada
    api, e = bot([])
    assert atender(api, ['111111111'], **args) == 'ATENDER ninguém escreveu' and not e['enviado']

    # o dono, uma pessoa nova (duas mensagens: um pedido só) e um grupo onde o bot entrou
    api, e = bot([msg(5, dono), msg(6, maria), msg(7, maria, 'oi'),
                  {'update_id': 8, 'my_chat_member': {'chat': grupo, 'new_chat_member': {'status': 'member'}}}])
    r = atender(api, ['111111111'], **args)
    assert r == 'ATENDER enviadas=4 pedidos_novos=2 falhas=0', r
    para = [c for c, _, _ in e['enviado']]
    assert para == ['111111111', '222222222', '-1001234567890', '111111111'], para
    assert 'já recebe os alertas do monitor de passagens' in e['enviado'][0][1] and 'Chega uma mensagem por hora.' in e['enviado'][0][1]
    assert 'Avisei quem cuida do monitor' in e['enviado'][1][1] and '<code>222222222</code>' in e['enviado'][1][1]
    aviso, botao = e['enviado'][3][1], e['enviado'][3][2]
    assert '• Maria &lt;Silva&gt; (@maria): <code>222222222</code>\n• grupo Família: <code>-1001234567890</code>' in aviso, aviso
    assert '<code>111111111,222222222,-1001234567890</code>' in aviso and '<b>TELEGRAM_CHAT_ID</b>' in aviso and SEGREDOS_URL in botao
    # nada pessoal no que vai para o log; a leitura foi confirmada, então a rodada seguinte não repete
    assert not any(x in r for x in ('2222', 'Maria', 'Família'))
    assert atender(api, ['111111111'], **args) == 'ATENDER ninguém escreveu' and len(e['enviado']) == 4

    # quem já está na lista não vira pedido; quem bloqueou o bot ou tirou o bot do grupo não recebe resposta
    api, e = bot([msg(1, maria), msg(2, grupo),
                  {'update_id': 3, 'my_chat_member': {'chat': grupo, 'new_chat_member': {'status': 'left'}}},
                  {'update_id': 4, 'message': {'chat': {'id': 333333333, 'type': 'private'}, 'left_chat_member': {}}}])
    assert atender(api, ['111111111', '222222222'], **args) == 'ATENDER enviadas=1 pedidos_novos=0 falhas=0'
    assert [c for c, _, _ in e['enviado']] == ['222222222']

    # sem ninguém cadastrado: quem escreveu recebe o próprio código e o nome do segredo; não há dono para avisar
    api, e = bot([msg(1, maria)])
    assert atender(api, [], **args) == 'ATENDER enviadas=1 pedidos_novos=1 falhas=0'
    assert 'ainda não há nenhum chat cadastrado' in e['enviado'][0][1] and '<b>TELEGRAM_CHAT_ID</b>' in e['enviado'][0][1]

    # mais pedidos que o limite: só os primeiros são atendidos, mas a leitura é confirmada (sem repetir depois)
    api, e = bot([msg(n, {'id': 900000000 + n, 'type': 'private', 'first_name': f'P{n}'}) for n in range(1, 6)])
    assert atender(api, ['111111111'], limite=2, **args) == 'ATENDER enviadas=3 pedidos_novos=5 falhas=0'
    assert '900000002' in e['enviado'][-1][1] and '900000003' not in e['enviado'][-1][1] and not e['fila'][5:]
    assert atender(api, ['111111111'], **args) == 'ATENDER ninguém escreveu'

    # envio recusado ou rede fora do ar: conta a falha, não levanta exceção e não mostra a URL (que traz o token)
    api, e = bot([msg(1, maria)], recusar=('222222222',))
    r = atender(api, ['111111111'], **args)
    assert r == 'ATENDER enviadas=1 pedidos_novos=1 falhas=1' and 'SEGREDO' not in r, r

    def fora_do_ar(metodo, campos=None):
        raise TimeoutError('https://api.telegram.org/bot123:SEGREDO/getUpdates')
    assert atender(fora_do_ar, ['111111111'], **args) == 'ATENDER_FALHOU TimeoutError'
    print('ok')


if __name__ == '__main__':
    if sys.argv[1:] == ['--teste']:
        _teste()
    else:
        print(__doc__)
