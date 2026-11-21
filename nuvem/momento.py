"""Momento de compra: o preço de agora é bom, normal ou ruim? Só funções puras.

Três sinais, cada um com o seu motivo escrito na mensagem:
  histórico -> o preço da recomendada nos últimos 7 dias, guardado no estado (só vale com 3 dias ou mais de histórico);
  Google    -> o aviso da tela de compra ("é um preço baixo", "normal" ou "alto" para a rota);
  prazo     -> a 3 semanas ou menos do voo, o preço costuma subir.
O alvo do trecho (viagem.json), quando é batido, decide sozinho; enquanto o preço está acima dele, os sinais bons viram
só "acompanhar" (com o quanto falta para o alvo), a não ser perto do voo.

Uso: python3 nuvem/momento.py --teste
"""
import sys
from datetime import datetime

FORMATO = '%Y-%m-%d %H:%M'
DIAS = 7                  # janela do histórico
HORAS_MINIMAS = 72        # menos histórico que isso não diz nada
GUARDAR_DIAS = 14         # quanto do histórico fica no estado
PERTO_DO_MENOR = 10       # reais: até aqui acima do menor da janela ainda conta como "o menor"
ACIMA_DA_MEDIA = 1.08     # a partir daqui o preço está caro para a semana
PRAZO_DIAS = 21


def brl(n):
    return 'R$' + f'{round(n):,}'.replace(',', '.')


def _horas(a, b):
    return (datetime.strptime(b, FORMATO) - datetime.strptime(a, FORMATO)).total_seconds() / 3600


def anotar(hist, agora, pp):
    """Acrescenta o preço de agora ao histórico (só quando muda) e tira o que ficou velho demais. hist: [[quando, preço]],
    em ordem; cada preço vale até o ponto seguinte. Fica o último ponto anterior ao corte, para saber de onde o preço vinha."""
    if not hist or hist[-1][1] != pp:
        hist.append([agora, pp])
    velhos = [i for i, (q, _) in enumerate(hist) if _horas(q, agora) > GUARDAR_DIAS * 24]
    if velhos:
        del hist[:velhos[-1]]
    return hist


def semana(hist, agora, dias=DIAS):
    """{'min', 'media', 'horas'} do preço nos últimos `dias` dias (média ponderada pelo tempo em que cada preço valeu;
    horas = quanto histórico existe dentro da janela). None sem histórico."""
    inicio_janela = -dias * 24
    trechos = []          # (horas em que valeu, preço), só a parte dentro da janela
    for (q, pp), seguinte in zip(hist, hist[1:] + [[agora, None]]):
        a, b = max(-_horas(q, agora), inicio_janela), -_horas(seguinte[0], agora)
        if b > a:
            trechos.append((b - a, pp))
    if not trechos:
        return None
    horas = sum(h for h, _ in trechos)
    return {'min': min(pp for _, pp in trechos), 'media': round(sum(h * pp for h, pp in trechos) / horas), 'horas': round(horas)}


def veredito(pp, alvo=None, sem=None, aviso=None, faltam=None):
    """{'sinal': 'bom' | 'acompanhar' | 'esperar', 'motivos': [frases]} ou None quando não há sinal nenhum.
    pp: preço de agora; alvo: preço-alvo do trecho; sem: semana(); aviso: aviso do Google (gvoos.ler_aviso);
    faltam: dias até o voo."""
    if alvo and pp <= alvo:
        return {'sinal': 'bom', 'motivos': [f'abaixo do alvo de {brl(alvo)}']}
    pontos, motivos = 0, []
    if sem and sem['horas'] >= HORAS_MINIMAS:
        n = min(DIAS, sem['horas'] // 24)
        if pp <= sem['min'] + PERTO_DO_MENOR and sem['media'] - sem['min'] <= PERTO_DO_MENOR:
            motivos.append(f'preço parado há {n} dias')       # não caiu nem subiu: não é sinal para lado nenhum
        elif pp <= sem['min'] + PERTO_DO_MENOR:
            pontos += 1
            motivos.append(f'menor preço em {n} dias')
        elif pp >= sem['media'] * ACIMA_DA_MEDIA:
            pontos -= 1
            motivos.append(f"{brl(pp - sem['media'])} acima da média de {n} dias ({brl(sem['media'])})")
        else:
            motivos.append(f"{brl(pp - sem['min'])} acima do menor de {n} dias ({brl(sem['min'])})")
    nivel = (aviso or {}).get('nivel')
    if nivel:
        pontos += {'baixo': 1, 'alto': -1}.get(nivel, 0)
        faixa = f" (a rota costuma ficar entre {brl(aviso['de'])} e {brl(aviso['ate'])})" if aviso.get('de') and aviso.get('ate') else ''
        motivos.append(f'para o Google, preço {nivel}{faixa}')
    perto = faltam is not None and faltam <= PRAZO_DIAS
    if perto:
        pontos += 1
        motivos.append(f"faltam {faltam} dias para o voo" if faltam != 1 else 'falta 1 dia para o voo')
    if not motivos:
        return None
    sinal = 'bom' if pontos >= 1 else 'esperar' if pontos <= -1 else 'acompanhar'
    if alvo:   # o alvo é a regra de quem viaja: acima dele, "bom momento" só perto do voo
        motivos.append(f'{brl(pp - alvo)} acima do alvo ({brl(alvo)})')
        if sinal == 'bom' and not perto:
            sinal = 'acompanhar'
    return {'sinal': sinal, 'motivos': motivos}


def _teste():
    # ---- histórico: só anota quando o preço muda; guarda 14 dias e o último ponto anterior ao corte
    h = []
    anotar(h, '2026-11-20 20:17', 800)
    anotar(h, '2026-11-20 21:17', 800)
    anotar(h, '2026-11-21 08:17', 760)
    assert h == [['2026-11-20 20:17', 800], ['2026-11-21 08:17', 760]]
    anotar(h, '2026-12-06 09:00', 700)          # 15 dias depois: o ponto de 800 sai; o de 760 fica (era o preço no começo da janela)
    assert h == [['2026-11-21 08:17', 760], ['2026-12-06 09:00', 700]], h

    # ---- semana: mínimo, média ponderada pelo tempo e quanto histórico há na janela
    assert semana([], '2026-11-21 08:17') is None
    h = [['2026-11-20 08:00', 800], ['2026-11-21 08:00', 700]]
    s = semana(h, '2026-11-21 20:00')            # 24 h a 800, 12 h a 700
    assert s == {'min': 700, 'media': 767, 'horas': 36}, s
    # preço que não mudou há mais de 7 dias: a janela inteira naquele preço
    assert semana([['2026-11-01 08:00', 500]], '2026-11-21 20:00') == {'min': 500, 'media': 500, 'horas': 168}
    # ponto anterior à janela conta só pela parte que cai dentro dela
    h = [['2026-11-10 20:00', 900], ['2026-11-20 20:00', 600]]
    s = semana(h, '2026-11-21 20:00')            # janela de 14/11 20:00 a 21/11 20:00: 144 h a 900, 24 h a 600
    assert s == {'min': 600, 'media': 857, 'horas': 168}, s

    # ---- veredito
    assert veredito(700) is None                                                   # sem sinal nenhum, sem linha
    assert veredito(440, alvo=450, aviso={'nivel': 'alto'}) == {'sinal': 'bom', 'motivos': ['abaixo do alvo de R$450']}
    # pouco histórico não conta; o Google sozinho decide
    pouco = {'min': 700, 'media': 720, 'horas': 30}
    assert veredito(700, sem=pouco) is None
    v = veredito(857, sem=pouco, aviso={'nivel': 'baixo', 'de': 1250, 'ate': 3250}, faltam=48)
    assert v == {'sinal': 'bom', 'motivos': ['para o Google, preço baixo (a rota costuma ficar entre R$1.250 e R$3.250)']}, v
    # trecho com alvo, ainda acima dele: o sinal bom vira "acompanhar" e a linha diz quanto falta; perto do voo, o prazo manda
    v = veredito(857, alvo=450, sem=pouco, aviso={'nivel': 'baixo', 'de': 1250, 'ate': 3250}, faltam=48)
    assert v == {'sinal': 'acompanhar', 'motivos': ['para o Google, preço baixo (a rota costuma ficar entre R$1.250 e R$3.250)', 'R$407 acima do alvo (R$450)']}, v
    v = veredito(857, alvo=450, aviso={'nivel': 'baixo', 'de': None, 'ate': None}, faltam=20)
    assert v == {'sinal': 'bom', 'motivos': ['para o Google, preço baixo', 'faltam 20 dias para o voo', 'R$407 acima do alvo (R$450)']}, v
    assert veredito(857, alvo=450, aviso={'nivel': 'alto'})['sinal'] == 'esperar' and veredito(857, alvo=450) is None   # só o alvo não gera linha
    assert veredito(857, aviso={'nivel': 'normal', 'de': None, 'ate': None}) == {'sinal': 'acompanhar', 'motivos': ['para o Google, preço normal']}
    assert veredito(857, aviso={'nivel': 'alto', 'de': 400, 'ate': 700})['sinal'] == 'esperar'
    # com histórico: no menor da semana é bom; bem acima da média é esperar; no meio, acompanhar
    cheia = {'min': 700, 'media': 760, 'horas': 168}
    assert veredito(705, sem=cheia) == {'sinal': 'bom', 'motivos': ['menor preço em 7 dias']}
    assert veredito(830, sem=cheia) == {'sinal': 'esperar', 'motivos': ['R$70 acima da média de 7 dias (R$760)']}
    assert veredito(740, sem=cheia) == {'sinal': 'acompanhar', 'motivos': ['R$40 acima do menor de 7 dias (R$700)']}
    assert veredito(705, sem={**cheia, 'horas': 80})['motivos'] == ['menor preço em 3 dias']
    # preço que não saiu do lugar na janela não é "o menor": fica neutro
    assert veredito(700, sem={'min': 700, 'media': 704, 'horas': 168}) == {'sinal': 'acompanhar', 'motivos': ['preço parado há 7 dias']}
    assert veredito(700, sem={'min': 700, 'media': 700, 'horas': 100}, aviso={'nivel': 'baixo'})['sinal'] == 'bom'
    # sinais que se anulam: acompanhar, com os dois motivos escritos
    v = veredito(830, sem=cheia, aviso={'nivel': 'baixo', 'de': 1000, 'ate': 2000})
    assert v == {'sinal': 'acompanhar', 'motivos': ['R$70 acima da média de 7 dias (R$760)', 'para o Google, preço baixo (a rota costuma ficar entre R$1.000 e R$2.000)']}, v
    # perto do voo pesa a favor de comprar
    assert veredito(740, sem=cheia, faltam=20) == {'sinal': 'bom', 'motivos': ['R$40 acima do menor de 7 dias (R$700)', 'faltam 20 dias para o voo']}
    assert veredito(830, sem=cheia, faltam=1) == {'sinal': 'acompanhar', 'motivos': ['R$70 acima da média de 7 dias (R$760)', 'falta 1 dia para o voo']}
    assert veredito(740, faltam=22) is None
    print('ok')


if __name__ == '__main__':
    if sys.argv[1:] == ['--teste']:
        _teste()
    else:
        print(__doc__)
