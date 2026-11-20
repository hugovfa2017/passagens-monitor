"""Atendimento dos bots do Telegram: quem manda /start passa a receber os alertas, sem ninguém cadastrar número de chat.

Os monitores (passagens e PS5) só enviam mensagens. Este módulo é o lado que escuta. A cada rodada do workflow
"Atendimento dos bots" (de 5 em 5 minutos, um de cada vez), para cada bot:
  1. lê o que chegou desde a última rodada (getUpdates);
  2. /start inscreve o chat e responde; /sair cancela; outra mensagem recebe uma explicação curta;
     grupo onde o bot entra é inscrito; quem bloqueia o bot ou o tira do grupo sai da lista;
  3. avisa o dono, sem som, de cada inscrito novo (com o comando para remover, se for um estranho);
  4. grava a lista de inscritos e, só depois que ela foi guardada no repositório, confirma a leitura no Telegram.

A lista de inscritos fica em arquivo cifrado (assinantes.cif e ps5/assinantes.cif): o repositório é público e o arquivo
só abre com o token do próprio bot. No log ficam só contagens; nome e número de chat nunca aparecem.
Só este workflow chama getUpdates. Dois leitores ao mesmo tempo atendem a mesma mensagem duas vezes.

Quem recebe os alertas = a lista fixa do dono (segredos TELEGRAM_CHAT_ID / TELEGRAM_CHAT_ID_PS5; o primeiro é o dono)
+ os inscritos pelo /start. Comandos só do dono: /inscritos e /remover <número>.

Uso:  python3 atendimento.py              -> atende os dois bots e grava as listas
      python3 atendimento.py --confirmar  -> confirma a leitura (depois que as listas foram guardadas)
      python3 atendimento.py --teste      -> auto-checagem (não envia nada)
"""
import base64, hashlib, hmac, html, json, os, re, secrets, sys, tempfile, urllib.parse, urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
LIMITE = 300   # inscritos por bot: cada rodada dos monitores manda uma mensagem para cada um


def _ids(variavel):
    return re.findall(r'-?\d{5,}', os.environ.get(variavel, ''))


BOTS = [
    dict(chave='passagens', nome='monitor de passagens', token='TELEGRAM_TOKEN', arquivo='assinantes.cif',
         fixos=lambda: _ids('TELEGRAM_CHAT_ID'),
         sobre='Chega uma mensagem por hora com os preços; ela só toca quando um preço fica abaixo do alvo ou bate o menor já visto.',
         descricao='Avisos de preço de passagens. Toque em Iniciar para receber: a confirmação chega em poucos minutos. '
                   'Para parar, mande /sair.'),
    dict(chave='ps5', nome='buscador de PS5', token='TELEGRAM_TOKEN_PS5', arquivo=os.path.join('ps5', 'assinantes.cif'),
         fixos=lambda: _ids('TELEGRAM_CHAT_ID_PS5') or _ids('TELEGRAM_CHAT_ID')[:1],
         sobre='A busca roda de 30 em 30 minutos. O aviso toca quando um PS5 fica abaixo do alvo ou bate o menor preço já visto; '
               'uma vez por dia chega um resumo sem som.',
         descricao='Avisos de preço do PS5 no Brasil, com cupom ou sem. Toque em Iniciar para receber: a confirmação chega em '
                   'poucos minutos. Para parar, mande /sair.'),
]
COMANDOS = [{'command': 'start', 'description': 'Receber os alertas'}, {'command': 'sair', 'description': 'Parar de receber'}]


# ---------------------------------------------------------------- lista de inscritos (arquivo cifrado)
def _chaves(token):
    chave = lambda rotulo: hmac.new(token.encode(), rotulo, hashlib.sha256).digest()
    return chave(b'assinantes-v1-cifra'), chave(b'assinantes-v1-selo')


def _fluxo(chave, nonce, n):
    """n bytes que só quem tem a chave consegue gerar (HMAC-SHA256 em modo contador)."""
    return b''.join(hmac.new(chave, nonce + i.to_bytes(8, 'big'), hashlib.sha256).digest() for i in range(n // 32 + 1))[:n]


def cifrar(token, dados):
    cifra, selo = _chaves(token)
    nonce = secrets.token_bytes(16)
    corpo = bytes(a ^ b for a, b in zip(dados, _fluxo(cifra, nonce, len(dados))))
    return base64.b64encode(nonce + corpo + hmac.new(selo, nonce + corpo, hashlib.sha256).digest()).decode()


def decifrar(token, texto):
    """Os dados, ou None se o arquivo foi alterado ou cifrado com outro token."""
    try:
        bruto = base64.b64decode(texto.strip(), validate=True)
    except ValueError:
        return None
    cifra, selo = _chaves(token)
    nonce, corpo, marca = bruto[:16], bruto[16:-32], bruto[-32:]
    if len(bruto) < 48 or not hmac.compare_digest(marca, hmac.new(selo, nonce + corpo, hashlib.sha256).digest()):
        return None
    return bytes(a ^ b for a, b in zip(corpo, _fluxo(cifra, nonce, len(corpo))))


def inscritos(token, arquivo):
    """Ids dos chats inscritos pelo /start. Lista vazia se não há arquivo, não há token ou o arquivo não abre."""
    try:
        dados = decifrar(token, open(arquivo, encoding='ascii').read()) if token else None
        return [str(i) for i in json.loads(dados)['chats']] if dados else []
    except (OSError, ValueError, KeyError, TypeError):
        return []


def gravar_inscritos(token, arquivo, ids):
    texto = json.dumps({'chats': list(ids)})
    texto += ' ' * (-len(texto) % 1024)   # tamanho em degraus: o arquivo não entrega quantos são
    with tempfile.NamedTemporaryFile('w', dir=os.path.dirname(arquivo) or '.', delete=False, encoding='ascii') as f:
        f.write(cifrar(token, texto.encode()) + '\n')
    os.replace(f.name, arquivo)


# ---------------------------------------------------------------- conversa
def _quem(chat):
    """'Maria Silva (@maria)' ou 'grupo Família', para o dono reconhecer quem entrou."""
    if chat.get('type') in ('group', 'supergroup', 'channel'):
        return f"grupo {chat.get('title') or 'sem nome'}"
    nome = ' '.join(filter(None, (chat.get('first_name'), chat.get('last_name')))) or 'sem nome'
    return nome + (f" (@{chat['username']})" if chat.get('username') else '')


def _comando(texto):
    """'/start@MeuBot abc' -> ('start', 'abc'); texto comum -> ('', '')."""
    m = re.match(r'/([A-Za-z_]+)(?:@\w+)?(?:\s+(.*))?$', (texto or '').strip(), re.S)
    return (m[1].lower(), (m[2] or '').strip()) if m else ('', '')


ACAO = {'start': 'entrar', 'sair': 'sair', 'stop': 'sair', 'parar': 'sair', 'inscritos': 'inscritos', 'remover': 'remover'}


def _eventos(novidades):
    """[(id do chat, chat, ação, argumento)] na ordem de chegada. Toque repetido no mesmo botão conta uma vez."""
    saida, ultimo = [], {}
    for u in novidades:
        if 'my_chat_member' in u:
            m = u['my_chat_member']
            chat, status = m.get('chat') or {}, (m.get('new_chat_member') or {}).get('status')
            if status in ('left', 'kicked'):
                ev = ('saiu', '')            # bloqueou o bot ou o tirou do grupo
            elif chat.get('type') != 'private' and status in ('member', 'administrator'):
                ev = ('entrar', '')          # o bot entrou num grupo
            else:
                continue
        else:
            m = u.get('message') or {}
            chat = m.get('chat') or {}
            cmd, arg = _comando(m.get('text'))
            if chat.get('type') != 'private' and cmd not in ACAO:
                continue                     # conversa de grupo que não é com o bot
            ev = (ACAO.get(cmd, 'ajuda'), arg)
        if chat.get('id') is None:
            continue
        i = str(chat['id'])
        if ultimo.get(i) != ev:
            saida.append((i, chat, *ev))
        ultimo[i] = ev
    return saida


def atender(api, fixos, lista, nome, sobre, limite=LIMITE):
    """api(metodo, campos) fala com o Telegram. fixos: lista do dono (o primeiro é o dono). lista: inscritos de agora.
    Devolve {'inscritos': lista nova, 'offset': o que confirmar depois de guardar a lista (ou None), 'resumo': contagens}.
    Nunca levanta exceção nem confirma a leitura: quem chama guarda a lista primeiro."""
    try:
        novidades = api('getUpdates', {'timeout': 0}).get('result') or []
    except Exception as e:   # sem str(e): pode trazer o token na URL
        return {'inscritos': list(lista), 'offset': None,
                'resumo': f"FALHOU ao ler as mensagens ({type(e).__name__} {getattr(e, 'code', '')})".replace(' )', ')')}
    fixos, lista = [str(c) for c in fixos], [str(c) for c in lista]
    dono = fixos[0] if fixos else None
    if not novidades:
        return {'inscritos': lista, 'offset': None, 'resumo': f'ninguém escreveu (recebem: {len(set(fixos + lista))})'}
    cont = dict(enviadas=0, falhas=0, saidas=0)
    novos = []

    def mandar(chat, texto, **extra):
        try:
            api('sendMessage', {'chat_id': chat, 'text': texto, 'parse_mode': 'HTML', 'disable_web_page_preview': 'true', **extra})
            cont['enviadas'] += 1
        except Exception:
            cont['falhas'] += 1

    for i, chat, acao, arg in _eventos(novidades):
        recebe, privado = i in fixos or i in lista, chat.get('type') == 'private'
        if acao == 'saiu':
            if i in lista:
                lista.remove(i)
                cont['saidas'] += 1
        elif acao == 'entrar':
            if recebe:
                mandar(i, f"✅ Este chat já recebe os alertas do {nome}.\n{sobre}\nPara parar, mande /sair.")
            elif len(lista) >= limite:
                mandar(i, f"😕 A lista de quem recebe os alertas do {nome} está cheia. Tente de novo outro dia.")
            else:
                lista.append(i)
                novos.append((i, chat))
                mandar(i, f"✅ Pronto! Este chat passa a receber os alertas do {nome}.\n{sobre}\nPara parar, mande /sair.")
        elif acao == 'sair':
            if i in lista:
                lista.remove(i)
                cont['saidas'] += 1
                mandar(i, f"🛑 Pronto, este chat não recebe mais os alertas do {nome}. Para voltar, mande /start.")
            elif i in fixos:
                mandar(i, "ℹ️ Este chat está na lista fixa do dono do monitor (um segredo no GitHub); por aqui não dá para sair.")
            else:
                mandar(i, "ℹ️ Este chat não está inscrito. Para receber os alertas, mande /start.")
        elif acao == 'inscritos' and i == dono:
            numeros = ', '.join(f'<code>{x}</code>' for x in lista) or 'ninguém'
            mandar(i, f"📋 Recebem os alertas do {nome}: {len(fixos)} da lista fixa e {len(lista)} pelo /start.\n"
                      f"Pelo /start: {numeros}\nPara tirar alguém: <code>/remover número</code>")
        elif acao == 'remover' and i == dono:
            alvo = (re.findall(r'-?\d{5,}', arg) or [''])[0]
            if alvo in lista:
                lista.remove(alvo)
                cont['saidas'] += 1
                mandar(i, f"🗑 Removido. Esse chat não recebe mais os alertas do {nome}.")
            else:
                mandar(i, "ℹ️ Esse número não está entre os inscritos pelo /start. Veja a lista com /inscritos.")
        elif privado:   # qualquer outra mensagem, ou comando do dono vindo de outra pessoa
            mandar(i, f"ℹ️ Este chat recebe os alertas do {nome}.\n{sobre}\nPara parar, mande /sair." if recebe else
                      f"👋 Este bot manda os alertas do {nome}. Para receber, mande /start.")

    entraram = [(i, c) for i, c in novos if i in lista]
    if dono and entraram:
        linhas = '\n'.join(f"• {html.escape(_quem(c))}. Para remover: <code>/remover {i}</code>" for i, c in entraram[:20])
        mandar(dono, f"🆕 <b>Inscrição nova nos alertas do {nome}</b>\n{linhas}\nAgora recebem: {len(set(fixos + lista))}.",
               disable_notification='true')
    return {'inscritos': lista, 'offset': max(u['update_id'] for u in novidades) + 1,
            'resumo': f"mensagens={cont['enviadas']} novos={len(entraram)} saidas={cont['saidas']} "
                      f"recebem={len(set(fixos + lista))} falhas={cont['falhas']}"}


def apresentar(api, descricao):
    """Texto que aparece antes do botão Iniciar e a lista de comandos do bot. Só altera quando está diferente."""
    try:
        if api('getMyDescription').get('result', {}).get('description') != descricao:
            api('setMyDescription', {'description': descricao})
        if api('getMyCommands').get('result') != COMANDOS:
            api('setMyCommands', {'commands': json.dumps(COMANDOS)})
        return ''
    except Exception as e:
        return f' (apresentação do bot não atualizada: {type(e).__name__})'


# ---------------------------------------------------------------- rodada
def _token(variavel):
    achado = re.search(r'\d+:[A-Za-z0-9_-]{30,}', os.environ.get(variavel, ''))
    return achado[0] if achado else ''


def _api(token, metodo, campos=None):
    dados = urllib.parse.urlencode(campos).encode() if campos else None
    with urllib.request.urlopen(urllib.request.Request(f'https://api.telegram.org/bot{token}/{metodo}', data=dados), timeout=30) as r:
        return json.loads(r.read())


def _pendente():
    return os.environ.get('ATENDIMENTO_PENDENTE') or os.path.join(tempfile.gettempdir(), 'atendimento-pendente.json')


def rodar(bots=BOTS, pasta=AQUI, api_de=None):
    """Atende cada bot e grava a lista de inscritos. O que falta confirmar no Telegram vai para um arquivo temporário."""
    api_de = api_de or (lambda token: lambda metodo, campos=None: _api(token, metodo, campos))
    linhas, pendente = [], {}
    for b in bots:
        token = _token(b['token'])
        if not token:
            linhas.append(f"{b['chave']}: sem token")
            continue
        api, arquivo = api_de(token), os.path.join(pasta, b['arquivo'])
        antes = inscritos(token, arquivo)
        r = atender(api, b['fixos'](), antes, b['nome'], b['sobre'])
        if r['inscritos'] != antes:
            gravar_inscritos(token, arquivo, r['inscritos'])
        if r['offset'] is not None:
            pendente[b['chave']] = r['offset']
        linhas.append(f"{b['chave']}: {r['resumo']}{apresentar(api, b['descricao'])}")
    with open(_pendente(), 'w', encoding='utf-8') as f:
        json.dump(pendente, f)
    return linhas


def confirmar(bots=BOTS, api_de=None):
    """Diz ao Telegram que as mensagens já foram atendidas. Roda depois que a lista de inscritos foi guardada."""
    api_de = api_de or (lambda token: lambda metodo, campos=None: _api(token, metodo, campos))
    try:
        pendente = json.load(open(_pendente(), encoding='utf-8'))
    except (OSError, ValueError):
        return ['nada a confirmar']
    linhas = []
    for b in bots:
        if b['chave'] in pendente:
            try:
                api_de(_token(b['token']))('getUpdates', {'offset': pendente[b['chave']], 'timeout': 0})
                linhas.append(f"{b['chave']}: leitura confirmada")
            except Exception as e:
                linhas.append(f"{b['chave']}: FALHOU ao confirmar ({type(e).__name__}); a próxima rodada pode responder de novo")
    os.remove(_pendente())
    return linhas or ['nada a confirmar']


# ---------------------------------------------------------------- auto-checagem
def _teste():
    def bot(novidades=(), recusar=()):
        """Um Telegram de mentira: guarda as novidades até a leitura ser confirmada e anota o que foi enviado."""
        e = {'fila': list(novidades), 'enviado': [], 'descricao': '', 'comandos': [], 'ajustes': 0}

        def api(metodo, campos=None):
            campos = campos or {}
            if metodo == 'getUpdates':
                e['fila'] = [u for u in e['fila'] if u['update_id'] >= int(campos.get('offset', 0))]
                return {'ok': True, 'result': list(e['fila'])}
            if metodo == 'getMyDescription':
                return {'ok': True, 'result': {'description': e['descricao']}}
            if metodo == 'getMyCommands':
                return {'ok': True, 'result': e['comandos']}
            if metodo == 'setMyDescription':
                e['descricao'], e['ajustes'] = campos['description'], e['ajustes'] + 1
                return {'ok': True}
            if metodo == 'setMyCommands':
                e['comandos'], e['ajustes'] = json.loads(campos['commands']), e['ajustes'] + 1
                return {'ok': True}
            if campos['chat_id'] in recusar:
                raise OSError('https://api.telegram.org/bot123:SEGREDO/sendMessage')
            e['enviado'].append((campos['chat_id'], campos['text'], campos.get('disable_notification')))
            return {'ok': True}
        return api, e

    msg = lambda n, chat, texto='/start': {'update_id': n, 'message': {'chat': chat, 'text': texto}}
    membro = lambda n, chat, status: {'update_id': n, 'my_chat_member': {'chat': chat, 'new_chat_member': {'status': status}}}
    dono = {'id': 111111111, 'type': 'private', 'first_name': 'Dono'}
    maria = {'id': 222222222, 'type': 'private', 'first_name': 'Maria', 'last_name': '<Silva>', 'username': 'maria'}
    grupo = {'id': -1001234567890, 'type': 'supergroup', 'title': 'Família'}
    args = dict(nome='monitor de passagens', sobre='Chega uma mensagem por hora.')
    D = ['111111111']

    # ---- arquivo cifrado: vai e volta; outro token ou arquivo mexido não abre; não mostra os números
    tk, outro = '123456789:' + 'A' * 35, '987654321:' + 'B' * 35
    with tempfile.TemporaryDirectory() as d:
        arq = os.path.join(d, 'assinantes.cif')
        assert inscritos(tk, arq) == [] and inscritos('', arq) == []
        gravar_inscritos(tk, arq, ['222222222', '-1001234567890'])
        cru = open(arq).read()
        assert inscritos(tk, arq) == ['222222222', '-1001234567890'] and inscritos(outro, arq) == []
        assert '2222' not in cru and '2222' not in base64.b64decode(cru).decode('latin-1')
        tamanho = len(cru)
        gravar_inscritos(tk, arq, [str(900000000 + n) for n in range(40)])
        assert len(open(arq).read()) == tamanho and open(arq).read() != cru          # mesmo degrau de tamanho, outro conteúdo
        open(arq, 'w').write(cru[:60] + ('A' if cru[60] != 'A' else 'B') + cru[61:])
        assert inscritos(tk, arq) == []
        open(arq, 'w').write('isto não é o arquivo')
        assert inscritos(tk, arq) == []

    # ---- ninguém escreveu: não envia nada e não há o que confirmar
    api, e = bot()
    r = atender(api, D, ['222222222'], **args)
    assert r == {'inscritos': ['222222222'], 'offset': None, 'resumo': 'ninguém escreveu (recebem: 2)'} and not e['enviado']

    # ---- /start inscreve na hora, sem o dono fazer nada; toque dobrado conta uma vez; o dono só é avisado, sem som
    api, e = bot([msg(5, maria), msg(6, maria), membro(7, grupo, 'member'), msg(8, dono)])
    r = atender(api, D, [], **args)
    assert r['inscritos'] == ['222222222', '-1001234567890'] and r['offset'] == 9, r
    assert r['resumo'] == 'mensagens=4 novos=2 saidas=0 recebem=3 falhas=0', r
    assert [c for c, _, _ in e['enviado']] == ['222222222', '-1001234567890', '111111111', '111111111']
    assert e['enviado'][0][1].startswith('✅ Pronto! Este chat passa a receber os alertas do monitor de passagens.') and '/sair' in e['enviado'][0][1]
    assert e['enviado'][2][1].startswith('✅ Este chat já recebe')
    aviso, _, mudo = e['enviado'][3][1], None, e['enviado'][3][2]
    assert mudo == 'true' and '• Maria &lt;Silva&gt; (@maria). Para remover: <code>/remover 222222222</code>' in aviso, aviso
    assert '• grupo Família.' in aviso and 'Agora recebem: 3.' in aviso
    assert not any(x in r['resumo'] for x in ('2222', 'Maria', 'Família'))          # nada pessoal no log
    # a leitura não foi confirmada por atender(): quem chama guarda a lista antes
    assert len(e['fila']) == 4

    # ---- quem já está inscrito não entra duas vezes; mensagem solta recebe a explicação; estranho recebe o convite
    api, e = bot([msg(1, maria), msg(2, dono, 'oi'), msg(3, {'id': 333333333, 'type': 'private'}, 'quanto custa?'),
                  msg(4, grupo, 'conversa do grupo'), msg(5, {'id': 444444444, 'type': 'private'}, '/inscritos')])
    r = atender(api, D, ['222222222'], **args)
    assert r['inscritos'] == ['222222222'] and r['resumo'] == 'mensagens=4 novos=0 saidas=0 recebem=2 falhas=0', r
    assert [(c, t[:1]) for c, t, _ in e['enviado']] == [('222222222', '✅'), ('111111111', 'ℹ'), ('333333333', '👋'), ('444444444', '👋')]

    # ---- /sair cancela; bloquear o bot ou tirar o bot do grupo também; a lista fixa do dono não sai por comando
    api, e = bot([msg(1, maria, '/sair'), membro(2, grupo, 'left'), msg(3, dono, '/stop'),
                  membro(4, {'id': 555555555, 'type': 'private'}, 'kicked'), msg(5, {'id': 333333333, 'type': 'private'}, '/sair')])
    r = atender(api, D, ['222222222', '-1001234567890', '555555555'], **args)
    assert r['inscritos'] == [] and r['resumo'] == 'mensagens=3 novos=0 saidas=3 recebem=1 falhas=0', r
    assert [(c, t[:1]) for c, t, _ in e['enviado']] == [('222222222', '🛑'), ('111111111', 'ℹ'), ('333333333', 'ℹ')]
    # entrou e saiu na mesma rodada: não fica na lista e o dono não é avisado
    api, e = bot([msg(1, maria), msg(2, maria, '/sair')])
    r = atender(api, D, [], **args)
    assert r['inscritos'] == [] and [c for c, _, _ in e['enviado']] == ['222222222', '222222222']

    # ---- comandos do dono: ver a lista e remover alguém
    api, e = bot([msg(1, dono, '/inscritos'), msg(2, dono, '/remover 222222222'), msg(3, dono, '/remover 999999999'),
                  msg(4, maria, '/remover 111111111')])
    r = atender(api, D, ['222222222'], **args)
    assert r['inscritos'] == [] and '1 da lista fixa e 1 pelo /start' in e['enviado'][0][1] and '<code>222222222</code>' in e['enviado'][0][1]
    assert e['enviado'][1][1].startswith('🗑') and e['enviado'][2][1].startswith('ℹ') and e['enviado'][3][1].startswith('👋')

    # ---- lista cheia: não inscreve
    api, e = bot([msg(1, maria)])
    r = atender(api, D, ['900000001', '900000002'], limite=2, **args)
    assert r['inscritos'] == ['900000001', '900000002'] and e['enviado'][0][1].startswith('😕')

    # ---- envio recusado ou rede fora do ar: conta a falha, não levanta exceção e não mostra a URL (que traz o token)
    api, e = bot([msg(1, maria)], recusar=('222222222',))
    r = atender(api, D, [], **args)
    assert r['inscritos'] == ['222222222'] and r['resumo'] == 'mensagens=1 novos=1 saidas=0 recebem=2 falhas=1', r

    def fora_do_ar(metodo, campos=None):
        raise TimeoutError('https://api.telegram.org/bot123:SEGREDO/getUpdates')
    r = atender(fora_do_ar, D, ['222222222'], **args)
    assert r == {'inscritos': ['222222222'], 'offset': None, 'resumo': 'FALHOU ao ler as mensagens (TimeoutError)'}, r

    # ---- rodada completa: grava a lista cifrada, só confirma a leitura depois, e a rodada seguinte não repete nada
    ambiente = dict(os.environ)
    try:
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'ps5'))
            os.environ.update(TELEGRAM_TOKEN=tk, TELEGRAM_CHAT_ID='111111111', TELEGRAM_TOKEN_PS5=outro,
                              ATENDIMENTO_PENDENTE=os.path.join(d, 'pendente.json'))
            os.environ.pop('TELEGRAM_CHAT_ID_PS5', None)
            api1, e1 = bot([msg(5, maria)])
            api2, e2 = bot([msg(3, maria), msg(4, grupo, '/start@Ps5Bot')])
            api_de = lambda token: {tk: api1, outro: api2}[token]
            linhas = rodar(pasta=d, api_de=api_de)
            assert linhas == ['passagens: mensagens=2 novos=1 saidas=0 recebem=2 falhas=0',
                              'ps5: mensagens=3 novos=2 saidas=0 recebem=3 falhas=0'], linhas
            assert inscritos(tk, os.path.join(d, 'assinantes.cif')) == ['222222222']
            assert inscritos(outro, os.path.join(d, 'ps5', 'assinantes.cif')) == ['222222222', '-1001234567890']
            assert e1['descricao'] == BOTS[0]['descricao'] and e2['comandos'] == COMANDOS and e1['ajustes'] == 2
            assert len(e1['fila']) == 1 and len(e2['fila']) == 2          # ainda não confirmado
            assert confirmar(api_de=api_de) == ['passagens: leitura confirmada', 'ps5: leitura confirmada']
            assert not e1['fila'] and not e2['fila'] and confirmar(api_de=api_de) == ['nada a confirmar']
            antes = open(os.path.join(d, 'assinantes.cif')).read()
            linhas = rodar(pasta=d, api_de=api_de)
            assert linhas == ['passagens: ninguém escreveu (recebem: 2)', 'ps5: ninguém escreveu (recebem: 3)'], linhas
            assert open(os.path.join(d, 'assinantes.cif')).read() == antes and e1['ajustes'] == 2 and len(e1['enviado']) == 2
            # sem o token de um bot, ele é pulado e o outro segue
            os.environ.pop('TELEGRAM_TOKEN_PS5')
            assert rodar(pasta=d, api_de=api_de)[1] == 'ps5: sem token'
    finally:
        os.environ.clear()
        os.environ.update(ambiente)
    print('ok')


if __name__ == '__main__':
    if sys.argv[1:] == ['--teste']:
        _teste()
    elif sys.argv[1:] == ['--confirmar']:
        print('\n'.join(confirmar()))
    elif not sys.argv[1:]:
        print('\n'.join(rodar()))
    else:
        print(__doc__)
